# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Round two of the #288 review: a wrapper name is one safe basename, a wrapper is
executable whatever the umask, and uninstall reads and unlinks through a descriptor.

- ``expose`` (and a node unit's ``command``) is joined onto ``~/.local/bin``; a
  manifest naming ``../../x`` or ``/etc/profile.d/x.sh`` must not leave it (under
  sudo that is a root-owned shell file on a login path);
- ``os.open(..., 0o755)`` is subject to the umask, so ``umask 077`` published a
  0700 wrapper and ``umask 777`` a 0000 one;
- ``_remove_if_ours`` checked a leaf symlink, then read and unlinked by pathname.

Everything is a ``tmp_path`` home; nothing here touches a real ``~/.local/bin``.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hammunition.backends import VenvBackend
from hammunition.backends.base import Action
from hammunition.backends.venv import WrapperRefused, wrapper_conflict, wrapper_text, write_wrapper
from hammunition.distro import Target
from hammunition.manifest.schema import PackageManifest, VenvInstall, command_name_problem
from hammunition.plan import InstallPlan, PlannedPackage, venv_wrapper_blockers

HASHED = (
    "example==1.0 --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000"
)
TARGET = Target(distro="debian", version="13", arch="x86_64", pretty_name="Debian 13")

BAD_NAMES = [
    "../../escaped",
    "../x",
    "/etc/profile.d/evil.sh",
    "/abs",
    "sub/dir",
    "a\\b",
    "..\\x",
    ".",
    "..",
    "",
    "nul\x00byte",
    "esc\x1b[31m",
    "new\nline",
    "tab\tname",
    "-rf",
    "-",
    "x" * 65,
    "zero​width",  # Cf: a format character
]
GOOD_NAMES = ["rnsd", "rnodeconf", "gps-tether", "a.b_c-d", "x" * 64, "R2"]


def manifest(expose: list[str]) -> dict[str, Any]:
    return {
        "name": "hardenunit",
        "version": "1.0",
        "summary": "Fixture for the wrapper hardening suite",
        "categories": ["logging"],
        "install": [{"install": {"method": "venv", "requirements": [HASHED], "expose": expose}}],
        "update": {"probe": {"method": "pypi"}, "strategy": "reinstall"},
        "documentation": {
            "what_it_does": "Exists so the wrapper writer has a unit to plan.",
            "why_you_want_it": "You do not; the suite does.",
            "upstream_url": "https://example.invalid/",
        },
    }


def raw_block(expose: list[str]) -> VenvInstall:
    """A VenvInstall built WITHOUT validation: what a bug elsewhere could hand the writer."""
    return VenvInstall.model_construct(
        method="venv", requirements=[HASHED], expose=expose, env={}, payload=None
    )


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "home" / ".local" / "bin"
    path.mkdir(parents=True)
    return path


# -- 1. one safe basename -------------------------------------------------------


@pytest.mark.parametrize("name", BAD_NAMES)
def test_the_schema_refuses_an_unsafe_expose_name(name: str) -> None:
    assert command_name_problem(name) is not None
    with pytest.raises(ValidationError):
        PackageManifest.model_validate(manifest([name]))


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_the_schema_accepts_a_plain_command_name(name: str) -> None:
    assert command_name_problem(name) is None
    PackageManifest.model_validate(manifest([name]))


def test_one_bad_name_among_good_ones_refuses_the_manifest() -> None:
    with pytest.raises(ValidationError, match=r"\.\./\.\./x"):
        PackageManifest.model_validate(manifest(["rnsd", "../../x"]))


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_node_units_command_follows_the_same_rule(name: str) -> None:
    from hammunition.manifest.schema import NodeInstall

    if name == "":
        return  # an empty command means "use the manifest name" only when None
    with pytest.raises(ValidationError):
        NodeInstall.model_validate(
            {
                "method": "node",
                "source": "https://example.invalid/x.tar.gz",
                "sha256": "0" * 64,
                "command": name,
                "build_script": None,
                "env": {},
            }
        )


@pytest.mark.parametrize("name", BAD_NAMES)
def test_the_writer_refuses_an_unsafe_name_even_from_a_constructed_object(
    tmp_path: Path, bindir: Path, name: str
) -> None:
    """Defence in depth: validation bypassed (model_construct), the writer still holds."""
    escaped_before = sorted(p.name for p in tmp_path.rglob("*"))
    backend = VenvBackend(venv_root=tmp_path / "venvs", bin_dir=bindir)
    m = PackageManifest.model_validate(manifest(["ok"]))
    block = raw_block([name])
    for step in backend.steps(m, block):
        if isinstance(step, Action) and step.kind == "wrapper":
            with pytest.raises(WrapperRefused):
                step.perform()
    assert sorted(p.name for p in tmp_path.rglob("*")) == escaped_before


def test_traversal_creates_nothing_outside_bin_dir(tmp_path: Path, bindir: Path) -> None:
    with pytest.raises(WrapperRefused):
        write_wrapper(bindir / ".." / ".." / "escaped", Path("/v/x"), "u", bin_dir=bindir)
    assert not (tmp_path / "home" / "escaped").exists()
    assert list(bindir.iterdir()) == []


def test_an_absolute_name_discards_bin_dir_and_is_refused(tmp_path: Path, bindir: Path) -> None:
    target = tmp_path / "elsewhere" / "evil.sh"
    target.parent.mkdir()
    # Path.__truediv__ with an absolute right-hand side drops the left one.
    path = bindir / str(target)
    assert path == target
    with pytest.raises(WrapperRefused):
        write_wrapper(path, Path("/v/x"), "u", bin_dir=bindir)
    assert not target.exists()


def test_the_writer_refuses_a_dotdot_component_without_being_told_bin_dir(
    tmp_path: Path, bindir: Path
) -> None:
    with pytest.raises(WrapperRefused):
        write_wrapper(bindir / ".." / "x", Path("/v/x"), "u")
    assert not (bindir.parent / "x").exists()


def test_the_conflict_check_refuses_an_escaping_name(tmp_path: Path, bindir: Path) -> None:
    reason = wrapper_conflict(bindir / ".." / ".." / "x", "u", bin_dir=bindir)
    assert reason is not None and "bin" in reason


def test_the_plan_blocks_an_unsafe_name_from_a_constructed_object(bindir: Path) -> None:
    m = PackageManifest.model_validate(manifest(["ok"]))
    planned = PlannedPackage(manifest=m, block=m.install[0], apt_packages=())
    object.__setattr__(planned.block, "install", raw_block(["../../x", "fine"]))
    blockers = venv_wrapper_blockers(InstallPlan(target=TARGET, packages=(planned,)), bindir)
    assert any("../../x" in b.reason for b in blockers)


def test_a_good_name_still_writes_exactly_in_bin_dir(bindir: Path) -> None:
    write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns", bin_dir=bindir)
    assert [p.name for p in bindir.iterdir()] == ["rnsd"]


# -- 2. executable whatever the umask -------------------------------------------


@pytest.fixture
def umask_of() -> Iterator[Any]:
    old = os.umask(0o022)

    def setter(value: int) -> None:
        os.umask(value)

    try:
        yield setter
    finally:
        os.umask(old)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.mark.parametrize("umask", [0o022, 0o077, 0o111, 0o777])
def test_a_new_wrapper_is_0755_under_any_umask(bindir: Path, umask_of: Any, umask: int) -> None:
    umask_of(umask)
    write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert mode(bindir / "rnsd") == 0o755


@pytest.mark.parametrize("umask", [0o022, 0o077, 0o111, 0o777])
def test_a_replaced_wrapper_is_0755_under_any_umask(
    bindir: Path, umask_of: Any, umask: int
) -> None:
    write_wrapper(bindir / "rnsd", Path("/v/old"), "rns")
    umask_of(umask)
    write_wrapper(bindir / "rnsd", Path("/v/new"), "rns")
    assert mode(bindir / "rnsd") == 0o755
    assert "/v/new" in (bindir / "rnsd").read_text()


@pytest.mark.parametrize("umask", [0o022, 0o077, 0o111, 0o777])
def test_the_no_hard_link_fallback_is_0755_under_any_umask(
    bindir: Path, umask_of: Any, monkeypatch: pytest.MonkeyPatch, umask: int
) -> None:
    def nolink(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EPERM, "no hard links here")

    monkeypatch.setattr("hammunition.backends.venv.os.link", nolink)
    umask_of(umask)
    write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert mode(bindir / "rnsd") == 0o755
    assert (bindir / "rnsd").read_text() == wrapper_text("rns", Path("/v/rnsd"))
    assert [p.name for p in bindir.iterdir()] == ["rnsd"]


# -- 3. uninstall: descriptor-based, inode-checked ------------------------------


def test_uninstall_does_not_delete_a_file_swapped_in_after_the_read(
    bindir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's reproduction: the name is replaced by a foreign file after the read."""
    from hammunition import execute

    write_wrapper(bindir / "rnsd", Path("/v/hammunition/venvs/rns/bin/rnsd"), "rns")
    real_fstat = os.fstat
    swapped = {"done": False}

    def swap_then_fstat(fd: int) -> os.stat_result:
        result = real_fstat(fd)
        if not swapped["done"] and os.readlink(f"/proc/self/fd/{fd}") == str(bindir / "rnsd"):
            swapped["done"] = True
            (bindir / "rnsd").unlink()
            (bindir / "rnsd").write_text("#!/bin/sh\necho the operator's own\n")
        return result

    monkeypatch.setattr("os.fstat", swap_then_fstat)
    message = execute._remove_if_ours(bindir / "rnsd")
    assert message.startswith("left in place"), message
    assert (bindir / "rnsd").read_text().endswith("the operator's own\n")


def test_uninstall_still_removes_our_wrapper_and_reports_absence(bindir: Path) -> None:
    from hammunition.execute import _remove_if_ours

    write_wrapper(bindir / "rnsd", Path("/v/hammunition/venvs/rns/bin/rnsd"), "rns")
    assert _remove_if_ours(bindir / "rnsd").startswith("removed")
    assert not (bindir / "rnsd").exists()
    assert _remove_if_ours(bindir / "rnsd").startswith("already absent")


def test_uninstall_with_no_bin_dir_reports_absence(tmp_path: Path) -> None:
    from hammunition.execute import _remove_if_ours

    assert _remove_if_ours(tmp_path / "nothing" / "rnsd").startswith("already absent")


def test_uninstall_leaves_a_fifo_and_a_directory_alone_without_hanging(bindir: Path) -> None:
    from hammunition.execute import _remove_if_ours

    os.mkfifo(bindir / "fifo")
    (bindir / "dir").mkdir()
    assert "left in place" in _remove_if_ours(bindir / "fifo")
    assert "left in place" in _remove_if_ours(bindir / "dir")
    assert (bindir / "fifo").exists() and (bindir / "dir").is_dir()


def test_as_the_operator_a_symlinked_bin_dir_is_followed_by_design(
    tmp_path: Path, bindir: Path
) -> None:
    """D-080's amendment: the operator's own ~/.local/bin may be a link into a dotfiles tree."""
    from hammunition.execute import _remove_if_ours

    real = tmp_path / "dotfiles" / "bin"
    real.mkdir(parents=True)
    write_wrapper(real / "rnsd", Path("/v/hammunition/venvs/rns/bin/rnsd"), "rns")
    link = tmp_path / "linked-bin"
    link.symlink_to(real)
    assert _remove_if_ours(link / "rnsd").startswith("removed")
    assert not (real / "rnsd").exists()


def test_as_root_a_symlinked_ancestor_under_an_operators_home_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under sudo, ~/.local/bin -> /chosen/dir must not let uninstall delete /chosen/dir/rnsd."""
    import pwd

    from hammunition import execute, paths

    home = tmp_path / "home" / "op"
    (home / ".local").mkdir(parents=True)
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    victim = chosen / "rnsd"
    victim.write_text("# generated by hammunition for rns\n")
    (home / ".local" / "bin").symlink_to(chosen)

    entry = pwd.struct_passwd(("op", "x", os.getuid(), os.getgid(), "", str(home), "/bin/sh"))
    monkeypatch.setattr(paths, "_operator_home", lambda path, owner=None: entry)
    message = execute._remove_if_ours(home / ".local" / "bin" / "rnsd")
    assert message.startswith("left in place"), message
    assert victim.exists()


def test_as_root_a_normal_operator_wrapper_is_still_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pwd

    from hammunition import execute, paths

    home = tmp_path / "home" / "op"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    write_wrapper(bin_dir / "rnsd", Path("/v/hammunition/venvs/rns/bin/rnsd"), "rns")
    entry = pwd.struct_passwd(("op", "x", os.getuid(), os.getgid(), "", str(home), "/bin/sh"))
    monkeypatch.setattr(paths, "_operator_home", lambda path, owner=None: entry)
    assert execute._remove_if_ours(bin_dir / "rnsd").startswith("removed")
    assert not (bin_dir / "rnsd").exists()
