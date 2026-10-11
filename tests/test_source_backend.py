# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The source backend: unpacking, and the steps a build turns into.

Extraction is where this backend can do real damage, so most of what follows is
written to the attack rather than to the happy path. An archive member named
``../../etc/cron.d/x``, one with an absolute path, or a symlink pointing out of
the tree are all ways to turn "unpack this" into "write anywhere", and whether
they work is decided by the extractor's defaults — which is exactly the kind of
thing that is easy to assume and cheap to assert.

The build-step tests assert the *shape* the plan will print: order, which single
step is privileged, and that declared flags actually reach the compiler. They
run no compiler; that is the container harness's job.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hammunition.backends import Action, BackendError, Command  # noqa: E402
from hammunition.backends.source import (  # noqa: E402
    DEFAULT_PREFIX,
    SourceBackend,
    extract,
    needs_root_for,
)
from hammunition.fetch import Fetcher  # noqa: E402
from hammunition.manifest.schema import (  # noqa: E402
    ManifestError,
    PackageManifest,
    SourceInstall,
)

PAYLOAD_SHA = "a" * 64


def _manifest(build_system: str = "autotools", **install: object) -> PackageManifest:
    block: dict[str, object] = {
        "method": "source",
        "source": {"url": "https://example.invalid/thing-1.0.tar.gz", "sha256": PAYLOAD_SHA},
        "build_system": build_system,
    }
    block.update(install)
    return PackageManifest.model_validate(
        {
            "name": "thing",
            "version": "1.0",
            "summary": "A thing that is built from source",
            "categories": ["digital-modes"],
            "install": [{"install": block}],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Does a thing for the purposes of testing the backend.",
                "why_you_want_it": "Because the source backend needs a manifest to act on.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def _backend(tmp_path: Path, **kwargs: object) -> SourceBackend:
    return SourceBackend(
        Fetcher(tmp_path / "cache"),
        build_root=tmp_path / "build",
        jobs=4,
        **kwargs,  # type: ignore[arg-type]
    )


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


# ---------------------------------------------------------------------------
# Extraction: the refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "member",
    [
        "../../escaped",
        "../escaped",
        "subdir/../../../escaped",
        "/escaped",
    ],
)
def test_no_tar_member_can_write_outside_the_destination(tmp_path: Path, member: str) -> None:
    """The property is *nothing lands outside*, not *an exception is raised*.

    The two mechanisms differ and both are acceptable: tarfile's ``data``
    filter refuses a ``..`` component outright, but an absolute name it
    *neutralises* — the leading ``/`` is stripped and the member lands harmlessly
    inside the destination. Asserting on the exception would have called that
    second case a failure while the machine was perfectly safe, and would have
    said nothing about where the bytes actually went. So this asserts where the
    bytes went.
    """
    victim = tmp_path / "escaped"
    destination = tmp_path / "dest" / "src"
    archive = _tar(tmp_path / "evil.tar.gz", {member: b"pwned", "keep/ok": b"fine"})

    # Refusal is one of two valid outcomes; containment is the other, and the
    # assertions below are what distinguish safe from unsafe either way.
    with contextlib.suppress(Exception):
        extract(archive, destination)

    assert not victim.exists(), f"member {member!r} escaped to {victim}"
    for landed in destination.rglob("*") if destination.exists() else []:
        resolved = landed.resolve()
        assert destination.resolve() in resolved.parents or resolved == destination.resolve(), (
            f"{landed} landed outside {destination}"
        )


def test_an_absolute_tar_member_lands_inside_rather_than_at_its_absolute_path(
    tmp_path: Path,
) -> None:
    """Named separately because the mechanism is worth pinning down: an absolute
    member is made relative, so it is *contained*, not rejected."""
    outside = tmp_path / "absolute-target"
    archive = _tar(tmp_path / "abs.tar.gz", {str(outside): b"pwned", "keep/ok": b"fine"})
    destination = tmp_path / "dest" / "src"

    extract(archive, destination)

    assert not outside.exists(), "an absolute member was written outside the tree"
    assert list(destination.rglob("absolute-target")), (
        "the absolute member vanished entirely; it should be contained, not dropped"
    )


def test_a_zip_member_escaping_the_tree_is_refused(tmp_path: Path) -> None:
    """zip has no equivalent of tar's `data` filter, so the members are screened
    here. mshv ships a zip, so this path is not hypothetical."""
    archive = _zip(tmp_path / "evil.zip", {"../../escaped": b"pwned"})
    with pytest.raises(BackendError, match="outside the build directory"):
        extract(archive, tmp_path / "dest" / "src")
    assert not (tmp_path / "escaped").exists()


def test_a_zip_member_with_an_absolute_path_is_refused(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "abs.zip", {"/etc/cron.d/x": b"pwned"})
    with pytest.raises(BackendError, match="absolute path"):
        extract(archive, tmp_path / "dest" / "src")


def test_an_unknown_archive_format_is_refused(tmp_path: Path) -> None:
    blob = tmp_path / "thing.rar"
    blob.write_bytes(b"not an archive we unpack")
    with pytest.raises(BackendError, match="not an archive"):
        extract(blob, tmp_path / "dest" / "src")


def test_an_empty_archive_is_refused(tmp_path: Path) -> None:
    """D-031: the extractor returning is not evidence anything landed."""
    archive = _tar(tmp_path / "empty.tar.gz", {})
    with pytest.raises(BackendError, match="unpacked to nothing"):
        extract(archive, tmp_path / "dest" / "src")


# ---------------------------------------------------------------------------
# Extraction: what it does when the archive is honest
# ---------------------------------------------------------------------------


def test_a_single_top_level_directory_is_stripped(tmp_path: Path) -> None:
    """The strip is what makes the source root predictable, which is what lets
    the plan print `./configure`'s real path before the download happens."""
    archive = _tar(
        tmp_path / "thing.tar.gz",
        {"thing-1.0/configure": b"#!/bin/sh\n", "thing-1.0/src/main.c": b"int main(){}\n"},
    )
    destination = tmp_path / "dest" / "src"
    outcome = extract(archive, destination)

    assert (destination / "configure").is_file(), "the top-level directory was not stripped"
    assert (destination / "src" / "main.c").is_file()
    assert "thing-1.0" in outcome


def test_a_multi_entry_archive_is_not_stripped(tmp_path: Path) -> None:
    archive = _tar(tmp_path / "flat.tar.gz", {"Makefile": b"all:\n", "main.c": b"int main(){}\n"})
    destination = tmp_path / "dest" / "src"
    extract(archive, destination)
    assert (destination / "Makefile").is_file()
    assert (destination / "main.c").is_file()


def test_re_extraction_starts_from_a_clean_tree(tmp_path: Path) -> None:
    """Idempotent (CLAUDE.md). Layering a new archive over a half-built tree
    leaves stale objects outliving the source they came from."""
    archive = _tar(tmp_path / "thing.tar.gz", {"thing-1.0/configure": b"#!/bin/sh\n"})
    destination = tmp_path / "dest" / "src"
    extract(archive, destination)
    stale = destination / "stale.o"
    stale.write_bytes(b"from a previous build")

    extract(archive, destination)
    assert not stale.exists(), "a stale artefact survived re-extraction"
    assert (destination / "configure").is_file()


def test_extraction_leaves_no_staging_directory(tmp_path: Path) -> None:
    archive = _tar(tmp_path / "thing.tar.gz", {"thing-1.0/configure": b"#!/bin/sh\n"})
    destination = tmp_path / "dest" / "src"
    extract(archive, destination)
    assert [p.name for p in destination.parent.iterdir()] == ["src"]


# ---------------------------------------------------------------------------
# The steps a build turns into
# ---------------------------------------------------------------------------


def test_the_steps_run_in_the_order_a_build_needs(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    manifest = _manifest("autotools")
    steps = backend.steps(manifest, manifest.install[0])

    kinds = [s.kind if isinstance(s, Action) else s.argv[0] for s in steps]
    assert kinds == ["fetch", "extract", "./configure", "make", "make"]


def test_only_the_install_step_is_privileged(tmp_path: Path) -> None:
    """CLAUDE.md drops to the operator wherever possible. A build run wholly as
    root leaves a tree of root-owned objects in the operator's cache."""
    backend = _backend(tmp_path)
    manifest = _manifest("cmake")
    steps = backend.steps(manifest, manifest.install[0])

    privileged = [s for s in steps if s.requires_root]
    assert len(privileged) == 1
    assert isinstance(privileged[0], Command)
    assert privileged[0].argv[:2] == ("cmake", "--install")


def test_the_plan_can_print_every_path_before_anything_is_fetched(tmp_path: Path) -> None:
    """Nothing in `steps()` touches the disk or the network, so `--dry-run`
    renders real paths rather than describing them."""
    backend = _backend(tmp_path)
    manifest = _manifest("cmake")
    steps = backend.steps(manifest, manifest.install[0])

    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "cache").exists()
    rendered = "\n".join(s.display(euid=1000) for s in steps)
    layout = backend.layout(manifest, manifest.install[0].install)  # type: ignore[arg-type]
    assert str(layout.src) in rendered
    # The shared payload step (Task 13) names the artifact and its URL; the
    # sha256 check is the pin's, stated for the mirror route in the step's suffix.
    assert "https://example.invalid/thing-1.0.tar.gz" in rendered
    assert steps[0].description == "Fetch thing source archive"


def test_the_fetch_step_states_that_the_sha256_is_verified(tmp_path: Path) -> None:
    """The shared payload step says so in its outcome (and the plan's mirror
    suffix says which digest is checked), whichever source answered."""
    import hashlib

    from hammunition.fetch import Fetcher as RealFetcher
    from test_fetch_mirror import Routes

    body = b"a source tarball"
    url = "https://example.invalid/thing-1.0.tar.gz"
    manifest = _manifest(
        source={"url": url, "sha256": hashlib.sha256(body).hexdigest()},
    )
    backend = SourceBackend(
        RealFetcher(tmp_path / "cache", transport=Routes({url: body})),
        build_root=tmp_path / "build",
        jobs=4,
    )
    fetch = backend.steps(manifest, manifest.install[0])[0]
    assert isinstance(fetch, Action) and fetch.kind == "fetch"
    assert "sha256 verified" in fetch.perform()
    # a second run is a cache hit, re-verified and said so
    again = backend.steps(manifest, manifest.install[0])[0]
    assert isinstance(again, Action)
    assert "cached" in again.perform() and "sha256 verified" in again.perform()


def test_compiler_flags_reach_the_compiler(tmp_path: Path) -> None:
    """Six AHRL units need -Wno-* to build at all. AHRL carries them as shell
    string-mangling; declaring them makes them reviewable catalog data."""
    manifest = _manifest("autotools", compiler_flags=["-Wno-incompatible-pointer-types"])
    backend = _backend(tmp_path)
    steps = backend.steps(manifest, manifest.install[0])

    configure = next(s for s in steps if isinstance(s, Command) and s.argv[0] == "./configure")
    assert configure.env["CFLAGS"] == "-Wno-incompatible-pointer-types"
    assert configure.env["CXXFLAGS"] == "-Wno-incompatible-pointer-types"


def test_the_qmake_project_file_is_passed_when_declared(tmp_path: Path) -> None:
    """MSHV needs a different .pro per architecture, which is why the field
    exists at all."""
    manifest = _manifest("qmake", project_file="MSHV_64.pro")
    backend = _backend(tmp_path)
    steps = backend.steps(manifest, manifest.install[0])

    qmake = next(s for s in steps if isinstance(s, Command) and s.argv[0] == "qmake")
    assert "MSHV_64.pro" in qmake.argv


def test_the_cmake_project_file_names_the_source_subdirectory(tmp_path: Path) -> None:
    """The schema has always called `project_file` "qmake .pro / cmake
    subdir"; the CMake path ignored it until Signal-Server, whose
    CMakeLists.txt is in `src/` (D-061, amended 2026-10-02)."""
    manifest = _manifest("cmake", project_file="src")
    backend = _backend(tmp_path)
    layout = backend.layout(manifest, manifest.install[0].install)  # type: ignore[arg-type]
    steps = backend.steps(manifest, manifest.install[0])
    configure = next(s for s in steps if isinstance(s, Command) and "--fresh" in s.argv)
    assert configure.argv[configure.argv.index("-S") + 1] == str(layout.src / "src")
    assert configure.cwd == layout.src
    plain = _manifest("cmake")
    steps = backend.steps(plain, plain.install[0])
    configure = next(s for s in steps if isinstance(s, Command) and "--fresh" in s.argv)
    assert configure.argv[configure.argv.index("-S") + 1] == str(layout.src)
    for outside in ("../src", "/src", "a/../../b"):
        with pytest.raises(ValidationError, match="project_file"):
            _manifest("cmake", project_file=outside)


def test_the_install_prefix_is_usr_local(tmp_path: Path) -> None:
    manifest = _manifest("autotools")
    backend = _backend(tmp_path)
    steps = backend.steps(manifest, manifest.install[0])
    configure = next(s for s in steps if isinstance(s, Command) and s.argv[0] == "./configure")
    assert f"--prefix={DEFAULT_PREFIX}" in configure.argv


def test_build_commands_carry_a_working_directory(tmp_path: Path) -> None:
    """`./configure` has no meaning without one, and the plan must show it."""
    manifest = _manifest("autotools")
    backend = _backend(tmp_path)
    layout = backend.layout(manifest, manifest.install[0].install)  # type: ignore[arg-type]
    steps = backend.steps(manifest, manifest.install[0])

    configure = next(s for s in steps if isinstance(s, Command) and s.argv[0] == "./configure")
    assert configure.cwd == layout.src
    assert configure.display(euid=1000).startswith(f"cd {layout.src} &&")


# ---------------------------------------------------------------------------
# The measured zeros, refused by name rather than silently skipped
# ---------------------------------------------------------------------------


def test_a_custom_build_system_is_refused_by_name(tmp_path: Path) -> None:
    manifest = _manifest("custom")
    backend = _backend(tmp_path)
    with pytest.raises(BackendError, match="custom"):
        backend.steps(manifest, manifest.install[0])


def test_patches_are_refused_by_name(tmp_path: Path) -> None:
    """Building unpatched source would produce a binary the manifest did not
    describe, which is worse than refusing."""
    manifest = _manifest(
        "autotools",
        patches=[{"file": "src/main.c", "description": "fix a thing that needs fixing"}],
    )
    backend = _backend(tmp_path)
    with pytest.raises(BackendError, match="patch"):
        backend.steps(manifest, manifest.install[0])


# ---------------------------------------------------------------------------
# Privilege is a property of the destination, not of who is asking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "privileged"),
    [
        ("/usr/local", True),
        ("/usr/local/bin", True),
        ("/opt/thing", True),
        ("/var/lib/thing", True),
        ("/home/operator/.local", False),
        ("/tmp/build-prefix", False),
    ],
)
def test_privilege_is_decided_by_the_destination(prefix: str, privileged: bool) -> None:
    """The first version asked `os.access(prefix, W_OK)` — "can *I* write here".
    Under sudo, and in every one of the root-running target containers, that
    made /usr/local look unprivileged and the install step claim it needed no
    root. Six container jobs caught what the dev machine could not, because the
    dev machine is not root.

    Deciding from the path restores `Command`'s stated rule — already being root
    is not the same as not needing root — and makes the answer identical on
    every machine, which a plan that gets printed and compared requires.
    """
    assert needs_root_for(Path(prefix)) is privileged


def test_the_install_step_stays_privileged_even_when_run_as_root(tmp_path: Path) -> None:
    """Asserted without faking a euid: the question never consults one."""
    manifest = _manifest("cmake")
    backend = SourceBackend(
        Fetcher(tmp_path / "cache"),
        build_root=tmp_path / "build",
        prefix=DEFAULT_PREFIX,
        jobs=2,
    )
    steps = backend.steps(manifest, manifest.install[0])
    install = next(
        s for s in steps if isinstance(s, Command) and s.argv[:2] == ("cmake", "--install")
    )
    assert install.requires_root, "installing into /usr/local is privileged whoever runs it"
    # ...and the sudo prefix is what disappears for root, not the flag.
    assert install.display(euid=0).startswith("cmake")
    assert install.display(euid=1000).startswith("sudo")


# ---------------------------------------------------------------------------
# Builds with no install rule of their own
# ---------------------------------------------------------------------------


def test_a_project_with_no_install_rule_installs_its_declared_binaries(tmp_path: Path) -> None:
    """MSHV and Coil64 ship a .pro with no INSTALLS, so `make install` has
    nothing to do and fails. AHRL leaves the binary in the build tree and
    generates a launcher that cd's into it; copying it into the prefix is the
    better answer and it is what makes `binaries` mean something."""
    manifest = PackageManifest.model_validate(
        {
            "name": "coily",
            "version": "1.0",
            "summary": "A qmake project whose .pro has no install rule",
            "categories": ["electronics"],
            "install": [
                {
                    "install": {
                        "method": "source",
                        "source": {"url": "https://example.invalid/c.tar.gz", "sha256": "0" * 64},
                        "build_system": "qmake",
                        "provides_install_target": False,
                    }
                }
            ],
            "binaries": [{"produced": "Coily", "install_as": "coily"}],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Stands in for a qmake project with no install rule.",
                "why_you_want_it": "Because two real ones in this catalog have none.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )
    backend = SourceBackend(
        Fetcher(tmp_path / "cache"), build_root=tmp_path / "b", prefix=tmp_path / "p", jobs=2
    )
    install_block = manifest.install[0]
    block = install_block.install
    assert isinstance(block, SourceInstall)
    commands = backend._build_commands(
        manifest, install_block, block, backend.layout(manifest, block)
    )

    argvs = [c.argv for c in commands]
    assert ("make", "install") not in argvs, "the failing install step is still there"
    last = commands[-1]
    assert last.argv[0] == "install"
    assert last.argv[-1].endswith("/p/bin/coily")
    assert last.argv[-2].endswith("/src/Coily")


def test_a_cmake_project_with_no_install_rule_copies_from_its_build_dir(tmp_path: Path) -> None:
    """cmake builds out of tree, so what it emits is in the build directory,
    not the source tree. js8call v3.0.3 has no install rule for its executable
    -- `cmake --install` exits 0 and writes an empty install manifest -- and
    the copy has to look where cmake actually wrote JS8Call (2026-09-02)."""
    manifest = PackageManifest.model_validate(
        {
            "name": "js8ish",
            "version": "3.0.3",
            "summary": "A cmake project whose CMakeLists has no install rule",
            "categories": ["digital-modes"],
            "install": [
                {
                    "install": {
                        "method": "source",
                        "source": {"url": "https://example.invalid/j.tar.gz", "sha256": "0" * 64},
                        "build_system": "cmake",
                        "provides_install_target": False,
                    }
                }
            ],
            "binaries": [{"produced": "JS8ish", "install_as": "js8ish"}],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Stands in for a cmake project with no install rule.",
                "why_you_want_it": "Because js8call is one.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )
    backend = SourceBackend(
        Fetcher(tmp_path / "cache"), build_root=tmp_path / "b", prefix=tmp_path / "p", jobs=2
    )
    install_block = manifest.install[0]
    block = install_block.install
    assert isinstance(block, SourceInstall)
    commands = backend._build_commands(
        manifest, install_block, block, backend.layout(manifest, block)
    )

    assert not any("--install" in c.argv for c in commands), "cmake --install would install nothing"
    last = commands[-1]
    assert last.argv[0] == "install"
    assert last.argv[-1].endswith("/p/bin/js8ish")
    assert last.argv[-2].endswith("/build/JS8ish"), (
        "looked in the source tree, where cmake never writes"
    )


def test_declaring_no_install_target_without_binaries_is_refused() -> None:
    """Otherwise the build succeeds and installs nothing -- a silent success,
    which is the failure mode this project keeps writing checks against."""
    with pytest.raises((ValidationError, ManifestError), match="provides_install_target"):
        PackageManifest.model_validate(
            {
                "name": "empty",
                "version": "1.0",
                "summary": "Declares no install target and names no binaries",
                "categories": ["electronics"],
                "install": [
                    {
                        "install": {
                            "method": "source",
                            "source": {
                                "url": "https://example.invalid/e.tar.gz",
                                "sha256": "0" * 64,
                            },
                            "build_system": "qmake",
                            "provides_install_target": False,
                        }
                    }
                ],
                "update": {"probe": {"method": "none"}},
                "documentation": {
                    "what_it_does": "Exists only to be rejected by the validator.",
                    "why_you_want_it": "It should not be representable at all.",
                    "upstream_url": "https://example.invalid/",
                },
            }
        )


def test_the_default_still_runs_the_build_systems_own_install(tmp_path: Path) -> None:
    """Guards the guard: if the flag defaulted to False the test above would
    pass for the wrong reason and every ordinary build would change."""
    manifest = PackageManifest.model_validate(
        {
            "name": "ordinary",
            "version": "1.0",
            "summary": "An ordinary autotools project",
            "categories": ["electronics"],
            "install": [
                {
                    "install": {
                        "method": "source",
                        "source": {"url": "https://example.invalid/o.tar.gz", "sha256": "0" * 64},
                        "build_system": "autotools",
                    }
                }
            ],
            "binaries": [{"produced": "src/ordinary", "install_as": "ordinary"}],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Stands in for every normal source build in the catalog.",
                "why_you_want_it": "To prove the new flag changed nothing by default.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )
    backend = SourceBackend(
        Fetcher(tmp_path / "cache"), build_root=tmp_path / "b", prefix=tmp_path / "p", jobs=2
    )
    install_block = manifest.install[0]
    block = install_block.install
    assert isinstance(block, SourceInstall)
    commands = backend._build_commands(
        manifest, install_block, block, backend.layout(manifest, block)
    )
    assert commands[-1].argv == ("make", "install")


# ---------------------------------------------------------------------------
# Patches — the measured zero ended by linrad (source-build-gaps #2)
# ---------------------------------------------------------------------------


def test_declared_patches_stage_then_apply_in_order(tmp_path: Path) -> None:
    from hammunition.backends.source import SourceLayout, patch_steps
    from hammunition.manifest.schema import Patch

    layout = SourceLayout(root=tmp_path)
    steps = patch_steps(
        "linrad",
        [
            Patch(file="Makefile", description="unbake -Werror", unified_diff="--- a\n+++ b\n"),
            Patch(file="x.c", description="second", unified_diff="--- a\n+++ b\n"),
        ],
        layout,
    )
    kinds = [s.kind if isinstance(s, Action) else s.argv[0] for s in steps]
    assert kinds == ["patch", "patch", "patch", "patch"]
    # stage, apply, stage, apply — and apply runs in the source tree
    assert steps[1].argv[:3] == ("patch", "-p1", "-i")  # type: ignore[union-attr]
    assert steps[1].cwd == layout.src  # type: ignore[union-attr]
    outcome = steps[0].perform()  # type: ignore[union-attr]
    assert "staged patch" in outcome
    staged = layout.root / "patches" / "00-Makefile.diff"
    assert staged.read_text().startswith("--- a")


def test_a_patch_without_a_diff_is_refused_by_name(tmp_path: Path) -> None:
    from hammunition.backends.source import SourceLayout, patch_steps
    from hammunition.manifest.schema import Patch

    with pytest.raises(BackendError, match="no unified_diff"):
        patch_steps(
            "linrad",
            [Patch(file="Makefile", description="described only")],
            SourceLayout(root=tmp_path),
        )


def test_zip_extraction_restores_recorded_unix_modes(tmp_path: Path) -> None:
    """Python's zipfile drops mode bits; linrad's configure arrives
    unrunnable without this. Only recorded bits are restored — a zip that
    carries none gets no invented executables."""
    import os

    from hammunition.backends.source import extract

    archive = tmp_path / "src.zip"
    with zipfile.ZipFile(archive, "w") as z:
        info = zipfile.ZipInfo("tree/configure")
        info.external_attr = 0o755 << 16
        z.writestr(info, "#!/bin/sh\n")
        z.writestr("tree/plain.txt", "data\n")
    dest = tmp_path / "out"
    extract(archive, dest)
    assert os.access(dest / "configure", os.X_OK)
    assert not os.access(dest / "plain.txt", os.X_OK)


def test_install_tree_plans_clear_then_ensure_then_copy(tmp_path: Path) -> None:
    """Gaps #6 and #8: the tree lands whole under the shared prefix, replaced
    in place so a re-run cannot accumulate files upstream deleted."""
    from pathlib import Path as P

    from hammunition.backends.source import tree_destination, tree_install_commands

    commands = tree_install_commands(
        name="mshv", source_tree=tmp_path / "src", prefix=P("/usr/local")
    )
    assert [c.argv[0] for c in commands] == ["rm", "install", "cp"]
    dest = tree_destination(P("/usr/local"), "mshv")
    assert str(dest) == "/usr/local/share/hammunition/mshv"
    assert commands[0].argv == ("rm", "-rf", "--", str(dest))
    assert commands[2].argv == (
        "cp",
        "-aT",
        "--no-preserve=ownership",
        str(tmp_path / "src"),
        str(dest),
    )
    assert all(c.requires_root for c in commands)


def test_install_tree_hands_the_tree_to_the_operator_by_an_explicit_step(
    tmp_path: Path,
) -> None:
    """Issue #38, D-043. Trees under /usr/local were operator-owned by accident:
    `cp -a` under root preserved the owner of the build tree the operator
    unpacked, and nothing in the log or the docs said so. The copy now
    preserves nothing about ownership, and a chown step the log shows hands
    the tree to the operator the run is on behalf of -- deliberately, because
    MSHV and radiosonde-auto-rx write beside their executables."""
    from pathlib import Path as P

    from hammunition.backends.source import tree_destination, tree_install_commands

    commands = tree_install_commands(
        name="mshv", source_tree=tmp_path / "src", prefix=P("/usr/local"), owner="alice"
    )
    assert [c.argv[0] for c in commands] == ["rm", "install", "cp", "chown"]
    dest = tree_destination(P("/usr/local"), "mshv")
    chown = commands[3]
    # -h: the tree's own symlinks change owner and are never followed, so a
    # link pointing outside the tree cannot hand root's files to the operator
    # (measured on Debian 13, 2026-09-07).
    assert chown.argv == ("chown", "-R", "-h", "--", "alice:", str(dest))
    assert chown.requires_root
    assert "alice" in chown.description


def test_install_tree_with_no_operator_stays_root_owned(tmp_path: Path) -> None:
    """No operator known means nobody to hand the tree to: root keeps it,
    which is what `--no-preserve=ownership` under root produces. The build
    tree's owner is never consulted."""
    from pathlib import Path as P

    from hammunition.backends.source import tree_install_commands

    commands = tree_install_commands(
        name="mshv", source_tree=tmp_path / "src", prefix=P("/usr/local")
    )
    assert [c.argv[0] for c in commands] == ["rm", "install", "cp"]
    assert "--no-preserve=ownership" in commands[2].argv


def test_install_tree_into_an_unprivileged_prefix_needs_no_chown(tmp_path: Path) -> None:
    """Under a prefix that needs no root the copy runs as the operator, so the
    tree is theirs already and a chown would be a privileged no-op."""
    from hammunition.backends.source import tree_install_commands

    commands = tree_install_commands(
        name="mshv", source_tree=tmp_path / "src", prefix=tmp_path / "prefix", owner="alice"
    )
    assert [c.argv[0] for c in commands] == ["rm", "install", "cp"]
    assert not any(c.requires_root for c in commands)


def test_the_source_backend_passes_the_operator_to_the_tree_install(tmp_path: Path) -> None:
    from pathlib import Path as P

    backend = _backend(tmp_path, prefix=P("/usr/local"), owner="alice")
    manifest = _manifest("qmake", install_tree=True, tree_marker="thing")
    steps = backend.steps(manifest, manifest.install[0])
    last = steps[-1]
    assert isinstance(last, Command)
    assert last.argv[:5] == ("chown", "-R", "-h", "--", "alice:")


def test_extraction_trusts_magic_bytes_over_the_filename(tmp_path: Path) -> None:
    """A SourceForge /download URL caches as <sha>-download with no archive
    suffix at all; MSHV's verified fetch then failed to unpack. Content
    decides now."""
    import zipfile as zf

    from hammunition.backends.source import extract

    nameless = tmp_path / "cafecafe-download"
    with zf.ZipFile(nameless, "w") as z:
        z.writestr("tree/file.txt", "data\n")
    dest = tmp_path / "out"
    outcome = extract(nameless, dest)
    assert (dest / "file.txt").read_text() == "data\n"
    assert "unpacked 1 entries" in outcome


# --- parallelism is sized to memory, not only to CPUs -----------------------


def _meminfo(tmp_path: Path, mem_kib: int, swap_kib: int) -> Path:
    p = tmp_path / "meminfo"
    p.write_text(
        f"MemTotal:       {mem_kib} kB\nMemFree:        1 kB\n"
        f"SwapTotal:      {swap_kib} kB\nSwapFree:       0 kB\n"
    )
    return p


def test_jobs_are_capped_by_memory_plus_swap(tmp_path: Path) -> None:
    """The js8call measurement, 2026-09-01: four jobs were OOM-killed on a
    3.9 GB guest without swap and completed on one with 3 GB of swap."""
    from hammunition.backends.source import default_jobs

    no_swap = _meminfo(tmp_path, mem_kib=4_004_000, swap_kib=0)
    assert default_jobs(cpu_count=4, meminfo=no_swap) == 1
    swapped = _meminfo(tmp_path, mem_kib=4_004_000, swap_kib=3_244_000)
    assert default_jobs(cpu_count=4, meminfo=swapped) == 3


def test_a_well_provisioned_machine_keeps_one_job_per_cpu(tmp_path: Path) -> None:
    from hammunition.backends.source import default_jobs

    big = _meminfo(tmp_path, mem_kib=32 * 1024 * 1024, swap_kib=0)
    assert default_jobs(cpu_count=8, meminfo=big) == 8


def test_jobs_never_drop_below_one_and_survive_a_missing_meminfo(tmp_path: Path) -> None:
    from hammunition.backends.source import default_jobs

    tiny = _meminfo(tmp_path, mem_kib=512 * 1024, swap_kib=0)
    assert default_jobs(cpu_count=4, meminfo=tiny) == 1
    assert default_jobs(cpu_count=4, meminfo=tmp_path / "absent") == 4
