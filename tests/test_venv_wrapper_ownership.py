# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""A venv wrapper never replaces an entry in ``~/.local/bin`` the engine did not write.  #288.

The ``rns`` unit exposes fourteen console scripts. A pipx install of the same
software leaves ``~/.local/bin/rnsd`` and its siblings as symlinks into pipx's
own venv, and a ``pip install --user`` leaves real scripts there. The old
writer opened the path with ``write_text``: through the symlink into another
tool's venv, over the script in place. D-022 says coexist, disclose, never
replace silently.

What these tests hold:

- the plan refuses, by name, every entry the engine did not write (its header
  is the proof), before a single step runs;
- the write itself re-checks and never writes through a link, even when the
  entry changed after the plan;
- re-installing over the engine's own wrapper still works;
- everything is a ``tmp_path`` home; nothing here touches a real
  ``~/.local/bin``, pipx or a station config.

What the design cannot claim is pinned in ``test_a_forged_header_cannot_be_told_from_ours``.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends import BackendError, VenvBackend
from hammunition.backends.base import Action
from hammunition.backends.venv import wrapper_text, write_wrapper
from hammunition.distro import Target
from hammunition.manifest.schema import PackageManifest, VenvInstall
from hammunition.plan import InstallPlan, PlannedPackage, venv_wrapper_blockers

HASHED = (
    "example==1.0 --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000"
)
TARGET = Target(distro="debian", version="13", arch="x86_64", pretty_name="Debian 13")
FOREIGN = "#!/usr/bin/python3\nimport rns\n"


def unit(name: str, expose: list[str]) -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": name,
            "version": "1.0",
            "summary": "Fixture for the wrapper ownership suite",
            "categories": ["logging"],
            "install": [
                {"install": {"method": "venv", "requirements": [HASHED], "expose": expose}}
            ],
            "update": {"probe": {"method": "pypi"}, "strategy": "reinstall"},
            "documentation": {
                "what_it_does": "Exists so the wrapper writer has a unit to plan.",
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


def block_of(m: PackageManifest) -> VenvInstall:
    install = m.install[0].install
    assert isinstance(install, VenvInstall)
    return install


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "home" / ".local" / "bin"
    path.mkdir(parents=True)
    return path


def reasons(plan: InstallPlan, bindir: Path) -> list[str]:
    return [f"{b.subject}: {b.reason}" for b in venv_wrapper_blockers(plan, bindir)]


# -- plan time: refusal by name ----------------------------------------------


def test_a_free_name_plans_clean(bindir: Path) -> None:
    assert venv_wrapper_blockers(plan_of(unit("rns", ["rnsd", "rnstatus"])), bindir) == []


def test_a_missing_bin_dir_plans_clean(tmp_path: Path) -> None:
    plan = plan_of(unit("rns", ["rnsd"]))
    assert venv_wrapper_blockers(plan, tmp_path / "nowhere" / "bin") == []
    assert not (tmp_path / "nowhere").exists(), "planning must create nothing"


def test_a_pip_user_script_is_refused_by_name_and_left_alone(bindir: Path) -> None:
    script = bindir / "rnsd"
    script.write_text(FOREIGN)
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert blocker.subject == "rns"
    assert str(script) in blocker.reason and "rnsd" in blocker.reason
    assert "did not write" in blocker.reason
    assert blocker.remedy is not None and "pip uninstall" in blocker.remedy
    assert script.read_text() == FOREIGN


def test_a_pipx_symlink_is_refused_and_pipx_is_untouched(tmp_path: Path, bindir: Path) -> None:
    pipx = tmp_path / "pipx" / "venvs" / "rns" / "bin"
    pipx.mkdir(parents=True)
    real = pipx / "rnsd"
    real.write_text(FOREIGN)
    (bindir / "rnsd").symlink_to(real)
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert "symbolic link" in blocker.reason and str(real) in blocker.reason
    assert blocker.remedy is not None and "pipx uninstall" in blocker.remedy
    assert real.read_text() == FOREIGN and (bindir / "rnsd").is_symlink()


def test_a_dangling_symlink_is_refused(bindir: Path) -> None:
    (bindir / "rnsd").symlink_to(bindir / "gone")
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert "symbolic link" in blocker.reason


def test_a_symlink_to_one_of_our_own_wrappers_is_still_a_symlink(bindir: Path) -> None:
    ours = bindir / "elsewhere"
    ours.write_text(wrapper_text("rns", Path("/v/rns/bin/rnsd")))
    (bindir / "rnsd").symlink_to(ours)
    assert reasons(plan_of(unit("rns", ["rnsd"])), bindir), "never written through, never adopted"


def test_a_directory_is_refused(bindir: Path) -> None:
    (bindir / "rnsd").mkdir()
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert "not a regular file" in blocker.reason


def test_a_fifo_is_refused_without_hanging(bindir: Path) -> None:
    os.mkfifo(bindir / "rnsd")
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert "not a regular file" in blocker.reason


def test_an_empty_file_is_refused(bindir: Path) -> None:
    (bindir / "rnsd").write_text("")
    assert reasons(plan_of(unit("rns", ["rnsd"])), bindir)


def test_a_huge_file_is_refused_unread(bindir: Path) -> None:
    with open(bindir / "rnsd", "wb") as handle:
        handle.truncate(512 * 1024 * 1024)  # sparse; reading it all would be the bug
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert "too large" in blocker.reason


def test_binary_garbage_is_refused(bindir: Path) -> None:
    (bindir / "rnsd").write_bytes(b"\x7fELF\x02\x01\xff\xfe\x00")
    assert reasons(plan_of(unit("rns", ["rnsd"])), bindir)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_an_unreadable_file_is_refused(bindir: Path) -> None:
    script = bindir / "rnsd"
    script.write_text(FOREIGN)
    script.chmod(0)
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert "cannot be read" in blocker.reason


def test_our_own_wrapper_is_not_a_conflict(bindir: Path) -> None:
    (bindir / "rnsd").write_text(wrapper_text("rns", Path("/v/rns/bin/rnsd")))
    assert venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir) == []


def test_a_wrapper_from_before_the_header_existed_is_still_ours(bindir: Path) -> None:
    """Every wrapper this backend wrote before #288 was these two lines. A
    re-install over one must keep working, so exactly that shape, exec'ing
    into this unit's own venv script, is recognised."""
    legacy = '#!/bin/sh\nexec "/h/.local/share/hammunition/venvs/rns/bin/rnsd" "$@"\n'
    (bindir / "rnsd").write_text(legacy)
    assert venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir) == []


def test_a_legacy_shaped_file_exec_ing_somewhere_else_is_not_ours(bindir: Path) -> None:
    (bindir / "rnsd").write_text('#!/bin/sh\nexec "/opt/other/venvs/otherunit/bin/rnsd" "$@"\n')
    assert reasons(plan_of(unit("rns", ["rnsd"])), bindir)


def test_another_units_wrapper_is_refused_naming_that_unit(bindir: Path) -> None:
    (bindir / "rnsd").write_text(wrapper_text("lxmf", Path("/v/lxmf/bin/rnsd")))
    (blocker,) = venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir)
    assert "lxmf" in blocker.reason


def test_the_header_is_not_enough_with_anything_extra_around_it(bindir: Path) -> None:
    ours = wrapper_text("rns", Path("/v/rns/bin/rnsd"))
    (bindir / "rnsd").write_text(ours + "rm -rf ~\n")
    assert reasons(plan_of(unit("rns", ["rnsd"])), bindir)
    (bindir / "rnsd").write_text("# prologue\n" + ours)
    assert reasons(plan_of(unit("rns", ["rnsd"])), bindir)


def test_a_forged_header_cannot_be_told_from_ours(bindir: Path) -> None:
    """The limit of the design, recorded: ownership is a header, and a file
    whose bytes are exactly what we would write is, by construction, one we
    could have written. It is replaced by an identical wrapper that execs the
    venv script this unit owns. Anything that differs in any byte is refused
    (the tests above)."""
    (bindir / "rnsd").write_text(wrapper_text("rns", Path("/somewhere/else/rnsd")))
    assert venv_wrapper_blockers(plan_of(unit("rns", ["rnsd"])), bindir) == []


def test_every_conflict_is_named_not_just_the_first(bindir: Path) -> None:
    for name in ("rnsd", "rnstatus", "rnpath"):
        (bindir / name).write_text(FOREIGN)
    found = reasons(plan_of(unit("rns", ["rnsd", "rnstatus", "rnpath", "rnid"])), bindir)
    assert len(found) == 3


def test_two_units_exposing_one_name_in_one_transaction_are_refused(bindir: Path) -> None:
    plan = plan_of(unit("alpha", ["tool"]), unit("beta", ["tool"]))
    (blocker,) = venv_wrapper_blockers(plan, bindir)
    assert "alpha" in blocker.reason and "beta" in blocker.reason and "tool" in blocker.reason


def test_a_symlinked_bin_dir_is_the_operators_own_layout_and_is_followed(
    tmp_path: Path,
) -> None:
    """Decision (documented in venv.py): as the operator, a symlinked
    ``~/.local/bin`` is a dotfiles layout, not an attack; the plan looks at the
    directory it really is. Entries inside are judged the same way."""
    real = tmp_path / "dotfiles" / "bin"
    real.mkdir(parents=True)
    link = tmp_path / "home" / ".local" / "bin"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    plan = plan_of(unit("rns", ["rnsd"]))
    assert venv_wrapper_blockers(plan, link) == []
    (real / "rnsd").write_text(FOREIGN)
    assert reasons(plan, link)


# -- write time: the plan can be stale, the write checks again ---------------


def test_a_fresh_write_is_a_complete_executable_wrapper_and_leaves_no_temporary(
    bindir: Path,
) -> None:
    target = Path("/v/rns/bin/rnsd")
    outcome = write_wrapper(bindir / "rnsd", target, "rns")
    body = (bindir / "rnsd").read_text()
    assert body == wrapper_text("rns", target)
    assert body.splitlines()[1] == "# generated by hammunition for rns"
    assert os.access(bindir / "rnsd", os.X_OK) and "wrote" in outcome
    assert [p.name for p in bindir.iterdir()] == ["rnsd"], "no temporary left behind"


def test_the_bin_dir_is_created_when_absent(tmp_path: Path) -> None:
    write_wrapper(tmp_path / "h" / ".local" / "bin" / "rnsd", Path("/v/rnsd"), "rns")
    assert (tmp_path / "h" / ".local" / "bin" / "rnsd").is_file()


def test_reinstalling_over_our_own_wrapper_works_and_follows_the_new_target(
    bindir: Path,
) -> None:
    write_wrapper(bindir / "rnsd", Path("/v/old/rnsd"), "rns")
    write_wrapper(bindir / "rnsd", Path("/v/new/rnsd"), "rns")
    assert '"/v/new/rnsd"' in (bindir / "rnsd").read_text()
    assert [p.name for p in bindir.iterdir()] == ["rnsd"]


def test_a_legacy_wrapper_is_upgraded_in_place(bindir: Path) -> None:
    (bindir / "rnsd").write_text('#!/bin/sh\nexec "/h/hammunition/venvs/rns/bin/rnsd" "$@"\n')
    write_wrapper(bindir / "rnsd", Path("/h/hammunition/venvs/rns/bin/rnsd"), "rns")
    assert "generated by hammunition for rns" in (bindir / "rnsd").read_text()


def test_the_write_refuses_a_symlink_planted_after_the_plan(tmp_path: Path, bindir: Path) -> None:
    victim = tmp_path / "pipx" / "rnsd"
    victim.parent.mkdir()
    victim.write_text(FOREIGN)
    (bindir / "rnsd").symlink_to(victim)
    with pytest.raises(BackendError, match="symbolic link"):
        write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert victim.read_text() == FOREIGN, "the link's target was written through"
    assert (bindir / "rnsd").is_symlink()
    assert sorted(p.name for p in bindir.iterdir()) == ["rnsd"], "no temporary left behind"


def test_the_write_refuses_a_foreign_file_planted_after_the_plan(bindir: Path) -> None:
    (bindir / "rnsd").write_text(FOREIGN)
    with pytest.raises(BackendError, match="did not write"):
        write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert (bindir / "rnsd").read_text() == FOREIGN


def test_the_write_refuses_a_fifo_without_hanging(bindir: Path) -> None:
    os.mkfifo(bindir / "rnsd")
    with pytest.raises(BackendError):
        write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert stat.S_ISFIFO((bindir / "rnsd").lstat().st_mode)


def test_a_symlink_swapped_in_during_the_write_is_not_followed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bindir: Path
) -> None:
    """The residual window, exercised: the entry is absent when the write
    checks, and a symlink appears before the wrapper is published. A new name
    is published with ``link()``, which fails on an existing name rather than
    replacing or following it."""
    victim = tmp_path / "pipx-rnsd"
    victim.write_text(FOREIGN)
    real_link = os.link

    def planted(*args: Any, **kwargs: Any) -> None:
        (bindir / "rnsd").symlink_to(victim)
        real_link(*args, **kwargs)

    monkeypatch.setattr("hammunition.backends.venv.os.link", planted)
    with pytest.raises(BackendError, match="appeared"):
        write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert victim.read_text() == FOREIGN
    assert (bindir / "rnsd").is_symlink()
    assert sorted(p.name for p in bindir.iterdir()) == ["rnsd"]


def test_without_hard_links_a_new_name_is_still_created_exclusively(
    monkeypatch: pytest.MonkeyPatch, bindir: Path
) -> None:
    def no_links(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EPERM, "no hard links here")

    monkeypatch.setattr("hammunition.backends.venv.os.link", no_links)
    write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert (bindir / "rnsd").read_text() == wrapper_text("rns", Path("/v/rnsd"))
    assert [p.name for p in bindir.iterdir()] == ["rnsd"]


def test_a_failed_publish_leaves_no_temporary(
    monkeypatch: pytest.MonkeyPatch, bindir: Path
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("hammunition.backends.venv.os.link", broken)
    with pytest.raises(OSError, match="disk full"):
        write_wrapper(bindir / "rnsd", Path("/v/rnsd"), "rns")
    assert list(bindir.iterdir()) == []


# -- the backend wires both ---------------------------------------------------


def test_the_backends_wrapper_step_carries_the_unit_name(tmp_path: Path) -> None:
    backend = VenvBackend(venv_root=tmp_path / "venvs", bin_dir=tmp_path / "bin")
    m = unit("venvunit", ["venvunit"])
    steps = backend.steps(m, block_of(m))
    wrapper = next(s for s in steps if isinstance(s, Action) and s.kind == "wrapper")
    wrapper.perform()
    body = (tmp_path / "bin" / "venvunit").read_text()
    assert "# generated by hammunition for venvunit\n" in body
    assert str(tmp_path / "venvs" / "venvunit" / "bin" / "venvunit") in body


def test_uninstall_still_recognises_the_new_wrapper_and_will_not_follow_a_link(
    tmp_path: Path, bindir: Path
) -> None:
    from hammunition.execute import _remove_if_ours

    write_wrapper(bindir / "rnsd", Path("/v/rns/bin/rnsd"), "rns")
    assert _remove_if_ours(bindir / "rnsd").startswith("removed")

    victim = tmp_path / "other"
    victim.write_text(wrapper_text("rns", Path("/v/rns/bin/rnsd")))
    (bindir / "rnsd").symlink_to(victim)
    assert "left in place" in _remove_if_ours(bindir / "rnsd")
    assert (bindir / "rnsd").is_symlink() and victim.exists()
