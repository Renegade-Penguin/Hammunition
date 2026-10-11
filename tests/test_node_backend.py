# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The node backend (D-037): disclosed as a requirement, refused below the floor,
scripts never run, loopback always, and nothing needs root."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends import BackendError, Command, RecordingRunner
from hammunition.backends.apt import AptBackend, AptPackageState
from hammunition.backends.base import Action
from hammunition.backends.node import NodeBackend, check_lockfile, install_tree, write_wrapper
from hammunition.distro import Target
from hammunition.manifest.schema import ManifestError, NodeInstall, PackageManifest
from hammunition.plan import PlanError, node_version, resolve

TARGET = Target(distro="debian", version="13", arch="x86_64")
DIGEST = "0c179ab1cf1e42bddda53933fc0417d18c5b3ff5c09ae9ffa747714670d2c943"


def manifest(**install_overrides: Any) -> PackageManifest:
    install: dict[str, Any] = {
        "method": "node",
        "artifact": {"url": "https://example.invalid/nodeunit-1.0.tar.gz", "sha256": DIGEST},
        "node_min_version": "20.19",
        "entry": "server.js",
        # Not the manifest name: the launcher below is called that, and both
        # land in ~/.local/bin (see the shadowing test).
        "command": "nodeunit-server",
    }
    install.update(install_overrides)
    return PackageManifest.model_validate(
        {
            "name": "nodeunit",
            "version": "1.0",
            "summary": "Fixture for the node backend suite",
            "categories": ["hf-propagation"],
            "install": [{"install": install, "build_depends": ["nodejs", "npm"]}],
            "launchers": [
                {
                    "name": "nodeunit",
                    "exec": "{node} & sleep 3 && xdg-open http://127.0.0.1:3001; wait",
                }
            ],
            "update": {"probe": {"method": "github_release", "repo": "example/nodeunit"}},
            "documentation": {
                "what_it_does": "Exists so the node backend has a unit to plan.",
                "why_you_want_it": "You do not; the suite does.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def block(m: PackageManifest) -> NodeInstall:
    install = m.install[0].install
    assert isinstance(install, NodeInstall)
    return install


class StubFetcher:
    offline = False
    mirror = None

    def sources_for(self, url: str, mirror: Any) -> tuple[tuple[str, str], ...]:
        return (("publisher", url),)

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, artifact: Any) -> Path:
        return self.root / "cache" / "nodeunit-1.0.tar.gz"

    def fetch(self, artifact: Any) -> Any:
        raise AssertionError("planning must not fetch")


def backend(tmp_path: Path) -> NodeBackend:
    return NodeBackend(
        fetcher=StubFetcher(tmp_path),  # type: ignore[arg-type]
        build_root=tmp_path / "build",
        node_root=tmp_path / "node",
        bin_dir=tmp_path / "bin",
    )


# ---------------------------------------------------------------------------
# Schema: the loopback rule and the D-031 pairing are unrepresentable to break
# ---------------------------------------------------------------------------


def test_a_manifest_cannot_set_host() -> None:
    with pytest.raises((ManifestError, ValueError), match="HOST"):
        manifest(env={"HOST": "0.0.0.0"})


def test_a_build_script_must_name_what_it_produces() -> None:
    with pytest.raises((ManifestError, ValueError), match="build_output"):
        manifest(build_output=None)
    with pytest.raises((ManifestError, ValueError), match="no build_script"):
        manifest(build_script=None)


def test_an_unbuilt_application_is_expressible() -> None:
    m = manifest(build_script=None, build_output=None)
    assert block(m).build_script is None


# ---------------------------------------------------------------------------
# Plan time: the D-037 gate
# ---------------------------------------------------------------------------


def _apt(tmp_path: Path, nodejs: AptPackageState | None) -> AptBackend:
    lists = tmp_path / "lists"
    lists.mkdir(exist_ok=True)
    (lists / "example.invalid_dists_trixie_main_binary-amd64_Packages").touch()

    class Apt(AptBackend):
        def probe(self, packages: Any) -> Any:
            states = {
                name: AptPackageState(name=name, installed=None, candidate="1.0")
                for name in packages
                if name != "nodejs"
            }
            if nodejs is not None and "nodejs" in packages:
                states["nodejs"] = nodejs
            return states

    return Apt(RecordingRunner(), lists_dir=lists)


def _plan(tmp_path: Path, nodejs: AptPackageState | None) -> Any:
    return resolve(
        ["nodeunit"],
        catalog={"nodeunit": manifest()},
        profiles={},
        target=TARGET,
        apt=_apt(tmp_path, nodejs),
        user="operator",
    )


def test_a_launcher_may_not_share_the_node_wrapper_name() -> None:
    """Both are written to ~/.local/bin by name. The first openhamclock dry
    run planned two writes to the same path, the second of which would have
    replaced the server wrapper with a launcher that execs itself."""
    with pytest.raises((ManifestError, ValueError), match="same name as the node wrapper"):
        manifest(command="nodeunit")
    with pytest.raises((ManifestError, ValueError), match="same name as the node wrapper"):
        manifest(command=None)  # defaults to the manifest name, which the launcher uses


def test_node_version_reads_debian_version_strings_to_the_minor() -> None:
    assert node_version("20.19.2+dfsg1-1") == (20, 19)
    assert node_version("1:18.19.1+dfsg-6ubuntu5") == (18, 19)
    assert node_version("22.22.0-1") == (22, 22)
    assert node_version("garbage") is None
    assert node_version("20") is None  # no minor: not a version this gate can judge


def test_the_floor_is_major_dot_minor_because_the_measured_floor_has_a_minor() -> None:
    # openhamclock builds on Node 18 and its server dies at start: require()
    # of an ES module needs 20.19 (measured on the Ubuntu 24.04 VM). A
    # major-only floor would have admitted 20.18.
    with pytest.raises((ManifestError, ValueError), match="node_min_version"):
        manifest(node_min_version="20")


def test_nodejs_and_npm_are_the_transaction_s_own_tool_dependencies(tmp_path: Path) -> None:
    plan = _plan(tmp_path, AptPackageState("nodejs", installed=None, candidate="20.19.2+dfsg1-1"))
    [planned] = plan.packages
    assert "nodejs" in planned.apt_packages and "npm" in planned.apt_packages
    assert "nodejs" in planned.build_only


def test_the_plan_discloses_the_node_requirement_and_the_registry_fetch(tmp_path: Path) -> None:
    plan = _plan(tmp_path, AptPackageState("nodejs", installed=None, candidate="20.19.2+dfsg1-1"))
    [note] = [n for n in plan.notes if n.startswith("nodeunit")]
    assert "Node 20.19 or newer" in note
    assert "nodejs 20.19.2+dfsg1-1" in note
    assert "registry.npmjs.org" in note
    assert "No package lifecycle scripts run" in note


def test_an_installed_node_counts_over_the_candidate(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        AptPackageState("nodejs", installed="24.1.0-1", candidate="20.19.2+dfsg1-1"),
    )
    [note] = [n for n in plan.notes if n.startswith("nodeunit")]
    assert "nodejs 24.1.0-1, installed" in note


def test_a_too_old_node_is_refused_at_plan_time_naming_floor_found_and_source(
    tmp_path: Path,
) -> None:
    with pytest.raises(PlanError) as exc:
        _plan(tmp_path, AptPackageState("nodejs", installed=None, candidate="16.20.2+dfsg-1"))
    text = str(exc.value)
    assert "needing Node 20.19 or newer" in text
    assert "16.20.2+dfsg-1" in text and "16.20 is below the floor" in text
    assert "never fetched (D-037)" in text


def test_the_minor_is_compared_not_just_the_major(tmp_path: Path) -> None:
    with pytest.raises(PlanError) as exc:
        _plan(tmp_path, AptPackageState("nodejs", installed=None, candidate="20.18.3-1"))
    assert "20.18.3-1" in str(exc.value)
    plan = _plan(tmp_path, AptPackageState("nodejs", installed=None, candidate="20.19.0-1"))
    assert plan.packages


def test_an_absent_node_is_refused_at_plan_time_in_d037_words(tmp_path: Path) -> None:
    with pytest.raises(PlanError) as exc:
        _plan(tmp_path, None)
    text = str(exc.value)
    assert "offers no nodejs package" in text
    assert "never fetched (D-037)" in text


# ---------------------------------------------------------------------------
# The steps: fetch, extract, lockfile, ci, build, prune, install, wrapper
# ---------------------------------------------------------------------------


def test_the_steps_run_in_order_with_scripts_ignored_and_no_root(tmp_path: Path) -> None:
    m = manifest()
    steps = backend(tmp_path).steps(m, block(m))
    kinds = [s.kind if isinstance(s, Action) else s.argv[:2] for s in steps]
    assert kinds == [
        "fetch",
        "extract",
        "lockfile",
        ("npm", "ci"),
        ("npm", "run"),
        ("npm", "prune"),
        "install-tree",
        "wrapper",
    ]
    for step in steps:
        if isinstance(step, Command):
            assert "--ignore-scripts" in step.argv, step.argv
            assert not step.requires_root
            assert step.cwd == tmp_path / "build" / f"nodeunit-{DIGEST[:8]}" / "src"
    run = steps[4]
    assert isinstance(run, Command) and run.argv[2] == "build"
    prune = steps[5]
    assert isinstance(prune, Command) and "--omit=dev" in prune.argv


PATCH = {
    "file": "server.js",
    "description": "listen on HOST rather than 0.0.0.0",
    "unified_diff": "--- a/server.js\n+++ b/server.js\n@@ -1 +1 @@\n-app.listen(PORT, '0.0.0.0')\n+app.listen(PORT, HOST)\n",
}


def test_a_patch_applies_after_extract_and_before_npm_touches_the_tree(tmp_path: Path) -> None:
    """openhamclock 26.7.0 reads HOST and listens on 0.0.0.0 regardless — the
    loopback bind D-037 promises is one upstream token away from nothing.
    The patch runs before the lock-file check so a diff upstream has moved
    out from under stops the run with no registry traffic at all."""
    m = manifest(patches=[PATCH])
    steps = backend(tmp_path).steps(m, block(m))
    kinds = [s.kind if isinstance(s, Action) else s.argv[0] for s in steps]
    assert kinds[:5] == ["fetch", "extract", "patch", "patch", "lockfile"], kinds
    apply = steps[3]
    assert isinstance(apply, Command)
    assert apply.argv[:2] == ("patch", "-p1") and not apply.requires_root
    assert apply.cwd == tmp_path / "build" / f"nodeunit-{DIGEST[:8]}" / "src"


def test_a_patched_node_build_pulls_patch_as_a_tool_dependency(tmp_path: Path) -> None:
    plan = resolve(
        ["nodeunit"],
        catalog={"nodeunit": manifest(patches=[PATCH])},
        profiles={},
        target=TARGET,
        apt=_apt(tmp_path, AptPackageState("nodejs", installed=None, candidate="20.19.2+dfsg1-1")),
        user="operator",
    )
    [planned] = plan.packages
    assert "patch" in planned.apt_packages and "patch" in planned.build_only
    unpatched = _plan(
        tmp_path, AptPackageState("nodejs", installed=None, candidate="20.19.2+dfsg1-1")
    )
    assert "patch" not in unpatched.packages[0].apt_packages


def test_an_unbuilt_application_skips_the_build_step(tmp_path: Path) -> None:
    m = manifest(build_script=None, build_output=None)
    steps = backend(tmp_path).steps(m, block(m))
    assert not any(isinstance(s, Command) and s.argv[1] == "run" for s in steps)


# ---------------------------------------------------------------------------
# The in-process actions, performed for real (D-031)
# ---------------------------------------------------------------------------


def _lock(tree: Path, packages: dict[str, dict[str, Any]]) -> None:
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "nodeunit"}, **packages}})
    )


def test_the_lockfile_check_counts_pins_and_refuses_an_unpinned_resolve(tmp_path: Path) -> None:
    tree = tmp_path / "src"
    _lock(
        tree,
        {
            "node_modules/a": {"resolved": "https://r/a.tgz", "integrity": "sha512-AAAA"},
            "node_modules/b": {"resolved": "https://r/b.tgz", "integrity": "sha512-BBBB"},
            "packages/local": {"link": True},
        },
    )
    assert check_lockfile(tree) == "2 package(s) pinned by sha512 integrity in package-lock.json"

    _lock(tree, {"node_modules/a": {"resolved": "https://r/a.tgz"}})
    with pytest.raises(BackendError, match="no integrity digest"):
        check_lockfile(tree)


def test_a_tree_without_a_lockfile_is_refused_by_name(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    with pytest.raises(BackendError, match=r"no package-lock\.json"):
        check_lockfile(tmp_path / "src")


def test_a_lockfile_v1_is_refused(tmp_path: Path) -> None:
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "package-lock.json").write_text(json.dumps({"lockfileVersion": 1, "dependencies": {}}))
    with pytest.raises(BackendError, match="lockfileVersion 1"):
        check_lockfile(tree)


def _built_tree(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "dist").mkdir(parents=True)
    (src / "dist" / "index.html").write_text("<html></html>")
    (src / "server.js").write_text("// server")
    (src / "node_modules" / ".bin").mkdir(parents=True)
    (src / "node_modules" / "pkg").mkdir()
    (src / "node_modules" / "pkg" / "cli.js").write_text("// cli")
    os.symlink("../pkg/cli.js", src / "node_modules" / ".bin" / "pkg")
    return src


def test_install_tree_copies_keeps_symlinks_and_preserves_the_apps_own_env(
    tmp_path: Path,
) -> None:
    src = _built_tree(tmp_path)
    dest = tmp_path / "node" / "nodeunit"
    dest.mkdir(parents=True)
    (dest / ".env").write_text("CALLSIGN=N0CALL\n")
    (dest / "stale.js").write_text("// from a previous version")

    outcome = install_tree(src, dest, entry="server.js", build_output="dist", preserve=[".env"])

    assert outcome == f"installed {dest}, kept .env"
    assert (dest / "server.js").read_text() == "// server"
    assert (dest / ".env").read_text() == "CALLSIGN=N0CALL\n", "the operator's config survives"
    assert not (dest / "stale.js").exists(), "a reinstall replaces the tree, not layers on it"
    link = dest / "node_modules" / ".bin" / "pkg"
    assert link.is_symlink() and os.readlink(link) == "../pkg/cli.js"


def test_install_tree_refuses_an_unbuilt_tree(tmp_path: Path) -> None:
    src = _built_tree(tmp_path)
    (src / "dist" / "index.html").unlink()
    with pytest.raises(BackendError, match="produced no dist/"):
        install_tree(src, tmp_path / "dest", entry="server.js", build_output="dist", preserve=[])
    assert not (tmp_path / "dest").exists(), "nothing copied when the check fails"


def test_install_tree_refuses_a_missing_entry(tmp_path: Path) -> None:
    src = _built_tree(tmp_path)
    with pytest.raises(BackendError, match=r"no app\.js to run"):
        install_tree(src, tmp_path / "dest", entry="app.js", build_output="dist", preserve=[])


def test_the_wrapper_binds_loopback_carries_the_marker_and_is_executable(tmp_path: Path) -> None:
    wrapper = tmp_path / "bin" / "nodeunit"
    tree = tmp_path / "node" / "nodeunit"
    write_wrapper(wrapper, tree=tree, entry="server.js", env={"PORT": "3001"}, name="nodeunit")
    body = wrapper.read_text()
    assert body.splitlines() == [
        "#!/bin/sh",
        "# generated by hammunition for nodeunit",
        f"cd {tree} || exit 1",
        'PORT=3001 HOST=127.0.0.1 exec node server.js "$@"',
    ]
    assert os.access(wrapper, os.X_OK)


def test_the_wrapper_quotes_what_it_interpolates(tmp_path: Path) -> None:
    wrapper = tmp_path / "bin" / "nodeunit"
    write_wrapper(
        wrapper,
        tree=tmp_path / "a dir",
        entry="server.js",
        env={"TITLE": "my shack; rm -rf /"},
        name="nodeunit",
    )
    body = wrapper.read_text()
    assert f"cd '{tmp_path}/a dir' || exit 1" in body
    assert "TITLE='my shack; rm -rf /' HOST=127.0.0.1 exec node server.js" in body


# ---------------------------------------------------------------------------
# Launchers compose the backend's wrapper; uninstall knows the tree
# ---------------------------------------------------------------------------


def test_the_node_placeholder_resolves_to_the_backend_wrapper(tmp_path: Path) -> None:
    from hammunition.launchers import wrapper_body

    m = manifest()
    body = wrapper_body(m, m.launchers[0], node_wrapper=tmp_path / "bin" / "nodeunit-server")
    assert (
        f"{tmp_path}/bin/nodeunit-server & sleep 3 && xdg-open http://127.0.0.1:3001; wait" in body
    )


def test_a_node_launcher_with_no_node_build_fails_loudly(tmp_path: Path) -> None:
    from hammunition.launchers import wrapper_body

    m = manifest()
    with pytest.raises(BackendError, match="diverged"):
        wrapper_body(m, m.launchers[0])


def test_commands_for_wires_the_backend_and_the_launcher_together(tmp_path: Path) -> None:
    from hammunition.execute import commands_for

    plan = _plan(tmp_path, AptPackageState("nodejs", installed=None, candidate="20.19.2+dfsg1-1"))
    apt = _apt(tmp_path, None)
    with pytest.raises(BackendError, match="no node backend"):
        commands_for(plan, apt)
    steps = commands_for(
        plan,
        apt,
        node=backend(tmp_path),
        launcher_bin=tmp_path / "bin",
        launcher_applications=tmp_path / "applications",
    )
    kinds = [s.kind if isinstance(s, Action) else s.argv[0] for s in steps]
    assert "install-tree" in kinds and kinds.count("wrapper") == 2, kinds


def test_uninstall_plans_the_tree_and_the_marked_wrapper(tmp_path: Path) -> None:
    from hammunition.state import RemovalPaths, plan_removal

    paths = RemovalPaths(
        prefix=tmp_path / "prefix",
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        applications_dir=tmp_path / "applications",
        node_root=tmp_path / "node",
    )
    assert paths.node_root is not None
    (paths.node_root / "nodeunit").mkdir(parents=True)
    paths.bin_dir.mkdir()
    (paths.bin_dir / "nodeunit-server").write_text(
        "#!/bin/sh\n# generated by hammunition for nodeunit\n"
    )
    plan = plan_removal(
        ["nodeunit"],
        catalog={"nodeunit": manifest()},
        profiles={},
        target=TARGET,
        attributed=frozenset(),
        states={},
        paths=paths,
    )
    removals = {(r.kind, r.basis, r.requires_root) for r in plan.artifacts["nodeunit"]}
    assert ("tree", "namespaced", False) in removals, "per-user tree, removed without root"
    assert ("wrapper", "marker", False) in removals


def test_uninstall_without_a_node_root_refuses_by_name(tmp_path: Path) -> None:
    from hammunition.state import RemovalError, RemovalPaths, plan_removal

    paths = RemovalPaths(
        prefix=tmp_path / "prefix",
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        applications_dir=tmp_path / "applications",
    )
    with pytest.raises(RemovalError, match="without a node root"):
        plan_removal(
            ["nodeunit"],
            catalog={"nodeunit": manifest()},
            profiles={},
            target=TARGET,
            attributed=frozenset(),
            states={},
            paths=paths,
        )
