# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The modem recovery step's plan, install, verify and removal, and how
``hardware state`` shows a wedged SIM (issue #427).

Every path is under the test's tmp_path (`modem_resume_files`); `systemctl
enable` and `disable` are simulated by making and removing the ``.wants`` links.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hammunition.backends.base import Command
from hammunition.hardware import modem_resume as mr
from hammunition.interface.hardware import build_hardware, render_hardware


def _install(commands: list[Command]) -> None:
    for command in commands:
        argv = command.argv
        if argv[0] == "install":
            src, dest = Path(argv[-2]), Path(argv[-1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            os.chmod(dest, int(argv[argv.index("-m") + 1], 8))
        elif argv[:2] == ("systemctl", "enable"):
            for link in mr.wants_links():
                Path(link).parent.mkdir(parents=True, exist_ok=True)
                if not Path(link).is_symlink():
                    Path(link).symlink_to(mr.unit_path())
        elif argv[:2] == ("systemctl", "disable"):
            for link in mr.wants_links():
                Path(link).unlink(missing_ok=True)
        elif argv[:2] == ("rm", "-f"):
            for path in argv[2:]:
                Path(path).unlink(missing_ok=True)


def _applied(tmp_path: Path) -> None:
    step = mr.plan_modem_resume()
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    mr.stage(step, staging)
    _install(mr.install_commands(step, str(staging)))


def test_the_unit_runs_at_every_sleep_target_and_at_boot_after_modemmanager() -> None:
    unit = mr.unit_content()
    targets = "suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target"
    assert f"After={targets} ModemManager.service\n" in unit
    assert f"WantedBy={targets} multi-user.target\n" in unit
    assert "Type=oneshot\n" in unit
    assert f"ExecStart={mr.SCRIPT}\n" in unit
    assert unit.startswith(mr.UNIT_HEADER)


def test_the_installed_script_is_the_module_whole_under_our_header() -> None:
    text = mr.script_content()
    assert text.startswith("#!/usr/bin/python3 -I\n" + mr.UNIT_HEADER)
    assert "def main(" in text
    assert "from hammunition" not in text


def test_without_qmicli_no_step_is_planned() -> None:
    step = mr.plan_modem_resume()
    assert not step.qmicli and step.is_noop
    assert mr.install_commands(step, "/stage") == []
    assert "not installed" in " ".join(mr.disclose(step))


def test_apply_installs_the_two_files_and_reads_them_back(
    modem_resume_files: Path, tmp_path: Path
) -> None:
    step = mr.plan_modem_resume()
    assert step.qmicli and not step.is_noop
    commands = mr.install_commands(step, "/stage")
    assert all(c.requires_root for c in commands)
    assert [c.argv[0] for c in commands] == ["install", "install", "systemctl", "systemctl"]
    _applied(tmp_path)
    assert mr.verify(mr.plan_modem_resume()) == []
    assert mr.plan_modem_resume().is_noop
    assert mr.status() == "installed"
    assert os.stat(mr.SCRIPT).st_mode & 0o777 == 0o755


def test_disclosure_names_the_one_attempt_and_that_no_polkit_is_added(
    modem_resume_files: Path,
) -> None:
    text = "\n".join(mr.disclose(mr.plan_modem_resume()))
    assert "ONE recovery attempt" in text
    assert "no polkit action and no helper verb" in text
    assert mr.SCRIPT in text and mr.unit_path() in text


def test_a_foreign_file_refuses_the_plan(modem_resume_files: Path) -> None:
    Path(mr.SCRIPT).parent.mkdir(parents=True)
    Path(mr.SCRIPT).write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(mr.ModemResumeError, match="was not written by Hammunition"):
        mr.plan_modem_resume()


def test_a_tampered_script_reads_stale_and_fails_verify(
    modem_resume_files: Path, tmp_path: Path
) -> None:
    _applied(tmp_path)
    Path(mr.SCRIPT).write_text(Path(mr.SCRIPT).read_text() + "# edit\n")
    assert mr.status() == "stale"
    assert mr.verify(mr.plan_modem_resume())


def test_unapply_removes_only_what_we_wrote(modem_resume_files: Path, tmp_path: Path) -> None:
    _applied(tmp_path)
    removal = mr.plan_modem_resume_removal()
    assert removal.unit_ours and removal.script_ours
    _install(mr.removal_commands(removal))
    assert mr.verify_removal(removal) == []
    assert mr.status() == "absent"
    assert mr.plan_modem_resume_removal().is_empty


def test_a_foreign_unit_is_not_removed(modem_resume_files: Path) -> None:
    Path(mr.unit_path()).parent.mkdir(parents=True)
    Path(mr.unit_path()).write_text("[Unit]\nDescription=someone else's\n")
    assert mr.plan_modem_resume_removal().is_empty


def test_read_sim_state_parses_the_scripts_file(modem_resume_files: Path) -> None:
    assert mr.read_sim_state() is None
    Path(mr.STATE).parent.mkdir(parents=True)
    Path(mr.STATE).write_text("state=failed\nsteps=detected,sim-power-off\ndevice=/dev/cdc-wdm0\n")
    assert mr.read_sim_state() == {
        "state": "failed",
        "steps": "detected,sim-power-off",
        "device": "/dev/cdc-wdm0",
    }


def _doc_lines(sim: dict[str, str] | None) -> list[str]:
    return render_hardware(build_hardware([], [], [], None, sim))


def test_state_says_sim_wedged_when_the_wedge_stands() -> None:
    lines = _doc_lines(
        {"state": "failed", "steps": "detected,sim-power-off", "device": "/dev/cdc-wdm0"}
    )
    text = "\n".join(lines)
    assert "SIM wedged" in text
    assert "/dev/cdc-wdm0" in text and "detected, sim-power-off" in text
    assert "hammunition-modem-resume" in text


def test_state_says_so_when_it_was_recovered_and_is_silent_when_ok_or_absent() -> None:
    assert "recovered it" in "\n".join(_doc_lines({"state": "recovered", "steps": "detected"}))
    for sim in (None, {"state": "ok"}, {}):
        assert "SIM" not in "\n".join(_doc_lines(sim))


def test_the_document_carries_the_sim_for_a_front_end() -> None:
    doc = build_hardware([], [], [], None, {"state": "wedged", "steps": "detected"})
    assert doc.modem_sim is not None and doc.modem_sim.wedged
    assert doc.modem_sim.steps == ("detected",)
    ok = build_hardware([], [], [], None, {"state": "recovered"})
    assert ok.modem_sim is not None and not ok.modem_sim.wedged
    assert build_hardware([], [], [], None, None).modem_sim is None
