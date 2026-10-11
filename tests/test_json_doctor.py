# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``doctor --json``.  D-059."""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from hammunition import doctor
from hammunition.backends.base import CommandResult
from hammunition.distro import Target
from hammunition.doctor import Check
from hammunition.station import Station, save_station
from json_support import (
    FIXTURE_CATALOG,
    assert_golden,
    assert_golden_text,
    assert_text_values_in_json,
    parse_one,
    validate,
)

cli = importlib.import_module("hammunition.cli.main")

TARGET = Target(distro="debian", version="13", arch="x86_64", pretty_name="Debian GNU/Linux 13")

CHECKS = {
    "doctor-ready": [
        Check("system", "ok", "Debian GNU/Linux 13"),
        Check(
            "udev rules",
            "info",
            "udev rules not yet applied",
            "hammunition hardware apply",
            fix_argv=["hammunition", "hardware", "apply"],
        ),
    ],
    "doctor-warn": [
        Check("system", "ok", "Debian GNU/Linux 13"),
        Check(
            "compiler",
            "warn",
            "no C compiler found",
            "sudo apt install build-essential",
            fix_argv=["sudo", "apt", "install", "build-essential"],
        ),
    ],
    "doctor-blocking": [
        Check("catalog", "fail", "the catalog could not be found or loaded", "pass --catalog"),
    ],
}


def _no_security_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A12: these tests are not about security keys, so every tool the gather
    path asks is an explicit, inert miss rather than the real host's."""
    monkeypatch.setattr(
        cli,
        "_security_key_probe",
        lambda argv: CommandResult(
            argv=argv, returncode=127, stdout="", stderr="diagnostic tool unavailable in fixture"
        ),
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    checks: list[Check],
    *flags: str,
) -> tuple[int, str]:
    monkeypatch.setattr(Target, "detect", classmethod(lambda cls: TARGET))
    monkeypatch.setattr(doctor, "run_checks", lambda **kwargs: checks)
    _no_security_keys(monkeypatch)
    rc = cli.main(["--catalog", str(FIXTURE_CATALOG), "doctor", *flags])
    return rc, capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_the_text_is_unchanged(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _rc, out = _run(monkeypatch, capsys, CHECKS[name])
    assert_golden_text(name, out)


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_the_document_matches_its_golden_schema_and_exit_code(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hammunition.interface.doctor import render_doctor

    text_rc, text = _run(monkeypatch, capsys, CHECKS[name])
    rc, out = _run(monkeypatch, capsys, CHECKS[name], "--json")
    assert rc == text_rc, "--json changed the exit code"
    doc = parse_one(out)
    validate(doc)
    assert_golden(name, doc)
    assert_text_values_in_json(text, doc, render_doctor)


def test_the_station_values_never_reach_the_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Count-only, as the text: doctor output is what people paste.

    Includes a placeholder map region (never a real one, per the global rule)
    to pin that a pinned region name is as absent from the document as the
    callsign and grid: doctor's station check is ok/not-set only, never the
    values or the regions a station is carrying.
    """
    monkeypatch.setattr(Target, "detect", classmethod(lambda cls: TARGET))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    _no_security_keys(monkeypatch)
    save_station(
        Station(
            callsign="N0TST",
            grid_square="FN31pr",
            map_regions=("atlantis/oceania",),
        ),
        path=tmp_path / "hammunition" / "station.yml",
    )
    cli.main(["--catalog", str(FIXTURE_CATALOG), "doctor", "--json"])
    out = capsys.readouterr().out
    doc: dict[str, Any] = parse_one(out)
    assert doc["kind"] == "doctor"
    assert "N0TST" not in out and "FN31pr" not in out and "atlantis/oceania" not in out


def test_the_desktops_check_reaches_the_document_with_no_code_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-060's check came from main after the JSON view was built; doctor's
    view is generic over run_checks, so it is carried as the text shows it."""
    from hammunition.desktop import Desktop, SessionScan
    from hammunition.interface.doctor import render_doctor

    monkeypatch.setattr(Target, "detect", classmethod(lambda cls: TARGET))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "XFCE")
    monkeypatch.delenv("SUDO_USER", raising=False)
    _no_security_keys(monkeypatch)
    monkeypatch.setattr(
        cli,
        "scan_sessions",
        lambda: SessionScan(desktops=frozenset({Desktop.xfce}), unrecognised=("sway.desktop",)),
    )
    text_rc = cli.main(["--catalog", str(FIXTURE_CATALOG), "doctor"])
    text = capsys.readouterr().out
    rc = cli.main(["--catalog", str(FIXTURE_CATALOG), "doctor", "--json"])
    doc = parse_one(capsys.readouterr().out)
    assert rc == text_rc
    validate(doc)
    (check,) = [c for c in doc["checks"] if c["name"] == "desktops"]
    assert check["status"] == "info"
    assert check["detail"] == (
        "session files offer Xfce; also sway.desktop, which names no desktop the catalog "
        "knows; this session is Xfce"
    )
    assert check["detail"] in text
    assert_text_values_in_json(text, doc, render_doctor)


def test_a_fresh_install_with_local_bin_off_path_is_not_sent_back_to_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Final review I2, end to end in a scratch HOME: bootstrap has linked
    ~/.local/bin/hammunition to this checkout, and ~/.local/bin is not on PATH
    yet. Doctor's remedy is the PATH one, in text and JSON alike."""
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    checkout = Path(__file__).resolve().parents[1]  # this checkout, as doctor derives it
    (local_bin / "hammunition").symlink_to(checkout / ".venv" / "bin" / "hammunition")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    # Under a root-mapped namespace ~/.local/bin is USER's; root's is $HOME's.
    monkeypatch.setenv("USER", "root")
    monkeypatch.setattr(Target, "detect", classmethod(lambda cls: TARGET))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    _no_security_keys(monkeypatch)
    cli.main(["--catalog", str(FIXTURE_CATALOG), "doctor"])
    text = capsys.readouterr().out
    cli.main(["--catalog", str(FIXTURE_CATALOG), "doctor", "--json"])
    doc = parse_one(capsys.readouterr().out)
    (check,) = [c for c in doc["checks"] if c["name"] == "hammunition"]
    assert check["status"] == "warn"
    assert "log out and back in" in check["fix"]
    assert 'export PATH="$HOME/.local/bin:$PATH"' in check["fix"]
    assert "bootstrap" not in check["fix"]
    assert "bootstrap" not in "".join(ln for ln in text.splitlines() if "hammunition" in ln)


def test_fix_argv_starts_with_an_available_program(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for checks in CHECKS.values():
        _rc, out = _run(monkeypatch, capsys, checks, "--json")
        doc = parse_one(out)
        for check in doc["checks"]:
            argv = check.get("fix_argv")
            if argv is not None:
                assert argv[0] in {"hammunition", "sudo"} or shutil.which(argv[0])


def test_a_single_command_fix_is_rendered_as_a_code_span(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _rc, out = _run(monkeypatch, capsys, CHECKS["doctor-warn"])
    assert "      → `sudo apt install build-essential`" in out
    assert "      → sudo apt install build-essential" not in out


def test_command_rendering_preserves_advice_around_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    check = Check(
        "compiler",
        "warn",
        "no C compiler found",
        "sudo apt install build-essential (the engine also pulls per-build deps at plan time)",
        fix_argv=["sudo", "apt", "install", "build-essential"],
    )
    _rc, out = _run(monkeypatch, capsys, [check])
    assert (
        "      → `sudo apt install build-essential` "
        "(the engine also pulls per-build deps at plan time)"
    ) in out
