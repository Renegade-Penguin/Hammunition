# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""#406: the node backend's wrapper is written the way the venv wrapper is (#288).

``write_wrapper`` in ``backends/node.py`` used ``write_text`` and ``chmod`` by
path, so a pipx-style symlink in ``~/.local/bin`` was written through and a
foreign file was replaced silently. It now shares the venv writer's mechanics
(dir-fd classification, ``O_EXCL|O_NOFOLLOW`` temporary, ``link()`` /
``rename()``, ``fchmod`` 0755), and the plan refuses the same cases by name
(``wrapper_blockers``).

Everything is a ``tmp_path`` bin directory; nothing here touches a real
``~/.local/bin`` or station config. The FIFO cases run under an alarm so a
regression fails instead of hanging the suite.
"""

from __future__ import annotations

import errno
import importlib
import os
import signal
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends import BackendError
from hammunition.backends.node import node_wrapper_text, write_wrapper
from hammunition.backends.venv import WrapperRefused, wrapper_conflict, wrapper_text
from hammunition.distro import Target
from hammunition.manifest.schema import NodeInstall, PackageManifest
from hammunition.plan import InstallPlan, PlannedPackage, wrapper_blockers

DIGEST = "0c179ab1cf1e42bddda53933fc0417d18c5b3ff5c09ae9ffa747714670d2c943"
TARGET = Target(distro="debian", version="13", arch="x86_64", pretty_name="Debian 13")
FOREIGN = "#!/usr/bin/node\nconsole.log('someone else');\n"
TREE = Path("/h/node/nodeunit")


def unit(name: str = "nodeunit", command: str | None = "nodeunit-server") -> PackageManifest:
    install: dict[str, Any] = {
        "method": "node",
        "artifact": {"url": "https://example.invalid/nodeunit-1.0.tar.gz", "sha256": DIGEST},
        "node_min_version": "20.19",
        "entry": "server.js",
    }
    if command is not None:
        install["command"] = command
    return PackageManifest.model_validate(
        {
            "name": name,
            "version": "1.0",
            "summary": "Fixture for the node wrapper ownership suite",
            "categories": ["hf-propagation"],
            "install": [{"install": install, "build_depends": ["nodejs", "npm"]}],
            "update": {"probe": {"method": "github_release", "repo": "example/nodeunit"}},
            "documentation": {
                "what_it_does": "Exists so the node wrapper writer has a unit to plan.",
                "why_you_want_it": "You do not; the suite does.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def plan_of(*manifests: PackageManifest) -> InstallPlan:
    return InstallPlan(
        target=TARGET,
        packages=tuple(
            PlannedPackage(manifest=m, block=m.install[0], apt_packages=()) for m in manifests
        ),
    )


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "home" / ".local" / "bin"
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def _alarm() -> Iterator[None]:
    """A FIFO read or open that blocks fails the test in 20 s instead of hanging."""

    def hang(signum: int, frame: Any) -> None:
        raise TimeoutError("blocked on a special file")

    old = signal.signal(signal.SIGALRM, hang)
    signal.alarm(20)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def put(path: Path, **kwargs: Any) -> str:
    return write_wrapper(
        path,
        tree=kwargs.pop("tree", TREE),
        entry="server.js",
        env=kwargs.pop("env", {"PORT": "3001"}),
        name=kwargs.pop("name", "nodeunit"),
        **kwargs,
    )


def listing(bindir: Path) -> list[str]:
    return sorted(p.name for p in bindir.iterdir())


# -- write time: never through a link, never over somebody else's file --------


def test_a_fresh_write_is_the_exact_wrapper_and_leaves_no_temporary(bindir: Path) -> None:
    put(bindir / "nodeunit-server")
    target = bindir / "nodeunit-server"
    assert target.read_text() == node_wrapper_text("nodeunit", TREE, "server.js", {"PORT": "3001"})
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert listing(bindir) == ["nodeunit-server"]


def test_the_bin_dir_is_created_when_absent(tmp_path: Path) -> None:
    put(tmp_path / "fresh" / "bin" / "nodeunit-server")
    assert (tmp_path / "fresh" / "bin" / "nodeunit-server").is_file()


def test_a_symlink_to_a_file_is_not_written_through(tmp_path: Path, bindir: Path) -> None:
    victim = tmp_path / "pipx" / "nodeunit-server"
    victim.parent.mkdir()
    victim.write_text(FOREIGN)
    (bindir / "nodeunit-server").symlink_to(victim)
    with pytest.raises(BackendError, match="symbolic link"):
        put(bindir / "nodeunit-server")
    assert victim.read_text() == FOREIGN, "the link's target was written through"
    assert (bindir / "nodeunit-server").is_symlink()
    assert listing(bindir) == ["nodeunit-server"], "no temporary left behind"


def test_a_symlink_to_a_directory_is_refused_and_the_directory_untouched(
    tmp_path: Path, bindir: Path
) -> None:
    victim = tmp_path / "somewhere"
    victim.mkdir()
    (bindir / "nodeunit-server").symlink_to(victim, target_is_directory=True)
    with pytest.raises(BackendError, match="symbolic link"):
        put(bindir / "nodeunit-server")
    assert list(victim.iterdir()) == []
    assert (bindir / "nodeunit-server").is_symlink()


def test_a_dangling_symlink_is_not_created_through(tmp_path: Path, bindir: Path) -> None:
    ghost = tmp_path / "ghost"
    (bindir / "nodeunit-server").symlink_to(ghost)
    with pytest.raises(BackendError, match="symbolic link"):
        put(bindir / "nodeunit-server")
    assert not ghost.exists(), "the write created the link's target"
    assert (bindir / "nodeunit-server").is_symlink()


def test_a_symlink_to_our_own_wrapper_is_still_a_symlink(bindir: Path) -> None:
    ours = bindir / "elsewhere"
    ours.write_text(node_wrapper_text("nodeunit", TREE, "server.js", {}))
    (bindir / "nodeunit-server").symlink_to(ours)
    with pytest.raises(BackendError, match="symbolic link"):
        put(bindir / "nodeunit-server")


def test_a_foreign_regular_file_is_refused_and_kept(bindir: Path) -> None:
    (bindir / "nodeunit-server").write_text(FOREIGN)
    with pytest.raises(BackendError, match="did not write"):
        put(bindir / "nodeunit-server")
    assert (bindir / "nodeunit-server").read_text() == FOREIGN
    assert listing(bindir) == ["nodeunit-server"]


def test_a_fifo_is_refused_without_hanging(bindir: Path) -> None:
    os.mkfifo(bindir / "nodeunit-server")
    with pytest.raises(BackendError, match="not a regular file"):
        put(bindir / "nodeunit-server")
    assert stat.S_ISFIFO((bindir / "nodeunit-server").lstat().st_mode)


def test_a_directory_is_refused(bindir: Path) -> None:
    (bindir / "nodeunit-server").mkdir()
    with pytest.raises(BackendError, match="not a regular file"):
        put(bindir / "nodeunit-server")


def test_another_units_node_wrapper_is_refused_naming_that_unit(bindir: Path) -> None:
    (bindir / "nodeunit-server").write_text(node_wrapper_text("other", TREE, "server.js", {}))
    with pytest.raises(BackendError, match="the unit other"):
        put(bindir / "nodeunit-server")


def test_a_venv_shaped_file_with_our_unit_name_is_not_a_node_wrapper(bindir: Path) -> None:
    (bindir / "nodeunit-server").write_text(wrapper_text("nodeunit", Path("/v/bin/x")))
    with pytest.raises(BackendError, match="did not write"):
        put(bindir / "nodeunit-server")


def test_the_header_with_anything_extra_after_it_is_not_ours(bindir: Path) -> None:
    ours = node_wrapper_text("nodeunit", TREE, "server.js", {})
    (bindir / "nodeunit-server").write_text(ours + "rm -rf ~\n")
    with pytest.raises(BackendError, match="did not write"):
        put(bindir / "nodeunit-server")


def test_reinstalling_over_our_own_wrapper_replaces_it_in_place(bindir: Path) -> None:
    put(bindir / "nodeunit-server", env={"PORT": "1"})
    put(bindir / "nodeunit-server", env={"PORT": "2"}, tree=Path("/h/node/new"))
    body = (bindir / "nodeunit-server").read_text()
    assert "PORT=2" in body and "/h/node/new" in body and "PORT=1" not in body
    assert listing(bindir) == ["nodeunit-server"]


def test_a_pre_406_node_wrapper_is_recognised_as_ours(bindir: Path) -> None:
    """The bytes the old writer produced are the bytes the new one produces."""
    old = (
        "#!/bin/sh\n# generated by hammunition for nodeunit\ncd /old/tree || exit 1\n"
        'PORT=3001 HOST=127.0.0.1 exec node server.js "$@"\n'
    )
    (bindir / "nodeunit-server").write_text(old)
    put(bindir / "nodeunit-server")
    assert "/h/node/nodeunit" in (bindir / "nodeunit-server").read_text()


@pytest.mark.parametrize("mask", [0o077, 0o111, 0o777])
def test_the_mode_is_0755_whatever_the_umask(bindir: Path, mask: int) -> None:
    old = os.umask(mask)
    try:
        put(bindir / "nodeunit-server")
        put(bindir / "nodeunit-server")  # and over our own
    finally:
        os.umask(old)
    assert stat.S_IMODE((bindir / "nodeunit-server").stat().st_mode) == 0o755


def test_a_symlink_swapped_in_during_the_write_is_not_followed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bindir: Path
) -> None:
    """The entry is absent when the write checks; a link appears before publish.
    A new name is published with ``link()``, which fails on an existing name."""
    victim = tmp_path / "victim"
    victim.write_text(FOREIGN)
    real_link = os.link

    def planted(*args: Any, **kwargs: Any) -> None:
        (bindir / "nodeunit-server").symlink_to(victim)
        real_link(*args, **kwargs)

    monkeypatch.setattr("hammunition.backends.venv.os.link", planted)
    with pytest.raises(BackendError, match="appeared"):
        put(bindir / "nodeunit-server")
    assert victim.read_text() == FOREIGN
    assert (bindir / "nodeunit-server").is_symlink()
    assert listing(bindir) == ["nodeunit-server"]


def test_a_foreign_file_swapped_in_over_our_wrapper_before_the_rename_is_caught(
    monkeypatch: pytest.MonkeyPatch, bindir: Path
) -> None:
    """The second look, after the temporary exists, sees a change the first did not."""
    put(bindir / "nodeunit-server")
    real_open = os.open
    state = {"swapped": False}

    def swapping(path: Any, flags: int, mode: int = 0o777, **kwargs: Any) -> int:
        fd = real_open(path, flags, mode, **kwargs)
        if (
            not state["swapped"]
            and isinstance(path, str)
            and path.startswith(".nodeunit-server.hammunition-")
        ):
            state["swapped"] = True
            (bindir / "nodeunit-server").unlink()
            (bindir / "nodeunit-server").write_text(FOREIGN)
        return fd

    monkeypatch.setattr("hammunition.backends.venv.os.open", swapping)
    with pytest.raises(BackendError, match="did not write"):
        put(bindir / "nodeunit-server")
    assert (bindir / "nodeunit-server").read_text() == FOREIGN
    assert listing(bindir) == ["nodeunit-server"], "the temporary was cleaned up"


def test_without_hard_links_a_new_name_is_still_created_exclusively(
    monkeypatch: pytest.MonkeyPatch, bindir: Path
) -> None:
    def no_links(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EPERM, "no hard links here")

    monkeypatch.setattr("hammunition.backends.venv.os.link", no_links)
    put(bindir / "nodeunit-server")
    assert stat.S_IMODE((bindir / "nodeunit-server").stat().st_mode) == 0o755
    assert listing(bindir) == ["nodeunit-server"]


def test_a_failed_publish_leaves_no_temporary(
    monkeypatch: pytest.MonkeyPatch, bindir: Path
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("hammunition.backends.venv.os.link", broken)
    with pytest.raises(OSError, match="disk full"):
        put(bindir / "nodeunit-server")
    assert listing(bindir) == []


@pytest.mark.parametrize("name", ["../escaped", "/etc/profile.d/x.sh", "a/b", "..", ""])
def test_a_name_that_leaves_the_bin_dir_is_refused(tmp_path: Path, bindir: Path, name: str) -> None:
    with pytest.raises(WrapperRefused):
        put(bindir / name if name else bindir / "x" / "..", bin_dir=bindir)
    assert not (tmp_path / "escaped").exists()
    assert listing(bindir) == []


def test_a_parent_other_than_the_bin_dir_is_refused(tmp_path: Path, bindir: Path) -> None:
    with pytest.raises(WrapperRefused, match="not directly inside"):
        put(tmp_path / "elsewhere" / "nodeunit-server", bin_dir=bindir)
    assert not (tmp_path / "elsewhere").exists()


# -- plan time: the same cases, by name, before any step runs -----------------


def test_a_free_name_plans_clean_and_creates_nothing(tmp_path: Path) -> None:
    assert wrapper_blockers(plan_of(unit()), tmp_path / "nowhere" / "bin") == []
    assert not (tmp_path / "nowhere").exists()


def test_the_unit_name_is_the_command_when_none_is_set(bindir: Path) -> None:
    (bindir / "nodeunit").write_text(FOREIGN)
    (blocker,) = wrapper_blockers(plan_of(unit(command=None)), bindir)
    assert str(bindir / "nodeunit") in blocker.reason


def test_our_own_wrapper_is_not_a_conflict(bindir: Path) -> None:
    put(bindir / "nodeunit-server")
    assert wrapper_blockers(plan_of(unit()), bindir) == []


def test_a_foreign_file_is_refused_by_name_with_a_remedy(bindir: Path) -> None:
    (bindir / "nodeunit-server").write_text(FOREIGN)
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert blocker.subject == "nodeunit"
    assert str(bindir / "nodeunit-server") in blocker.reason and "did not write" in blocker.reason
    assert blocker.remedy is not None and "move" in blocker.remedy
    assert (bindir / "nodeunit-server").read_text() == FOREIGN


def test_a_symlink_to_a_file_is_a_blocker(tmp_path: Path, bindir: Path) -> None:
    real = tmp_path / "real"
    real.write_text(FOREIGN)
    (bindir / "nodeunit-server").symlink_to(real)
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert "symbolic link" in blocker.reason and str(real) in blocker.reason
    assert real.read_text() == FOREIGN


def test_a_symlink_to_a_directory_is_a_blocker(tmp_path: Path, bindir: Path) -> None:
    (tmp_path / "d").mkdir()
    (bindir / "nodeunit-server").symlink_to(tmp_path / "d", target_is_directory=True)
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert "symbolic link" in blocker.reason


def test_a_dangling_symlink_is_a_blocker(tmp_path: Path, bindir: Path) -> None:
    (bindir / "nodeunit-server").symlink_to(tmp_path / "gone")
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert "symbolic link" in blocker.reason
    assert not (tmp_path / "gone").exists()


def test_a_fifo_is_a_blocker_without_hanging(bindir: Path) -> None:
    os.mkfifo(bindir / "nodeunit-server")
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert "not a regular file" in blocker.reason


def test_a_directory_is_a_blocker(bindir: Path) -> None:
    (bindir / "nodeunit-server").mkdir()
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert "not a regular file" in blocker.reason


def test_a_huge_file_is_refused_unread(bindir: Path) -> None:
    with (bindir / "nodeunit-server").open("wb") as handle:
        handle.truncate(50_000_000)
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert "too large" in blocker.reason


def test_two_units_sharing_one_command_are_refused(bindir: Path) -> None:
    blockers = wrapper_blockers(plan_of(unit("a", "same"), unit("b", "same")), bindir)
    assert len(blockers) == 1 and "both expose the command same" in blockers[0].reason


def test_a_node_unit_and_a_venv_unit_sharing_a_command_are_refused(bindir: Path) -> None:
    from test_venv_wrapper_ownership import unit as venv_unit

    blockers = wrapper_blockers(plan_of(unit("a", "same"), venv_unit("b", ["same"])), bindir)
    assert len(blockers) == 1 and "both expose the command same" in blockers[0].reason


def test_the_venv_blocker_name_still_answers_for_venv_units(bindir: Path) -> None:
    from hammunition.plan import venv_wrapper_blockers
    from test_venv_wrapper_ownership import unit as venv_unit

    (bindir / "rnsd").write_text(FOREIGN)
    assert len(venv_wrapper_blockers(plan_of(venv_unit("rns", ["rnsd"])), bindir)) == 1


def test_the_node_ownership_rule_is_not_the_venv_one(bindir: Path) -> None:
    """The conflict check takes the ownership rule as an argument: a venv wrapper
    for our own unit is ours to a venv unit and foreign to a node unit."""
    path = bindir / "nodeunit-server"
    path.write_text(wrapper_text("nodeunit", Path("/v/bin/x")))
    assert wrapper_conflict(path, "nodeunit", bindir) is None
    (blocker,) = wrapper_blockers(plan_of(unit()), bindir)
    assert "did not write" in blocker.reason


# -- the backend wires both ---------------------------------------------------


def test_the_backends_wrapper_step_writes_through_the_shared_writer(tmp_path: Path) -> None:
    """The step the plan prints performs ``write_wrapper`` with the backend's bin dir."""
    from hammunition.backends.node import NodeBackend

    m = unit()
    install = m.install[0].install
    assert isinstance(install, NodeInstall)
    backend = NodeBackend(
        fetcher=None,  # type: ignore[arg-type]  # only wrapper_for is exercised
        build_root=tmp_path / "b",
        node_root=tmp_path / "n",
        bin_dir=tmp_path / "bin",
    )
    assert backend.wrapper_for(m, install) == tmp_path / "bin" / "nodeunit-server"


def test_a_foreign_node_wrapper_is_a_refused_plan_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The plan path: a pipx-style symlink where a node unit's command goes blocks
    the transaction by name, as a plan document, and nothing is written. HOME is a
    tmp_path, never the real one."""
    from json_support import parse_one, validate
    from test_json_install import _machine, _run

    cli: Any = importlib.import_module("hammunition.cli.main")
    _machine(monkeypatch, tmp_path)
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    other = tmp_path / "other-tool"
    other.write_text(FOREIGN)
    (home / ".local" / "bin" / "nodeunit-server").symlink_to(other)
    monkeypatch.setattr(cli, "resolve", lambda *args, **kwargs: plan_of(unit()))
    rc, out = _run(capsys, "install", "--dry-run", "--json", "nodeunit")
    doc = parse_one(out)
    validate(doc)
    assert rc == cli.EXIT_UNPLANNABLE
    assert doc["outcome"] == "refused" and doc["install"] is None
    (blocker,) = doc["blockers"]
    assert blocker["subject"] == "nodeunit" and "nodeunit-server" in blocker["reason"]
    assert "symbolic link" in blocker["reason"]
    assert other.read_text() == FOREIGN
    assert (home / ".local" / "bin" / "nodeunit-server").is_symlink()
