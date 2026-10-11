# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811 - the `machine` fixture is imported by name, as the other offline CLI tests do
"""A mirror URL given with a user and password is refused without repeating it.  #381, Task 12."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_offline_context import machine, run  # noqa: F401


def test_station_set_mirror_json_refusal_does_not_echo_a_credential(
    machine: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, err = run(
        capsys,
        machine,
        "station",
        "set",
        "--mirror",
        "http://alice:SECRET@bunker.invalid",
        "--json",
    )
    assert rc != 0
    assert "SECRET" not in out and "SECRET" not in err and "bunker.invalid" in out


def test_mirror_enrol_refusal_does_not_echo_a_credential(
    machine: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, err = run(capsys, machine, "mirror", "enrol", "http://alice:SECRET@bunker.invalid")
    assert rc != 0
    assert "SECRET" not in out and "SECRET" not in err and "bunker.invalid" in err


def test_a_saved_station_with_a_credentialed_mirror_does_not_leak_into_errors_or_the_log(
    machine: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from hammunition.runlog import logs_dir
    from hammunition.station import StationError, config_path, load_station

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mirror: http://alice:SECRET@bunker.invalid\n")
    with pytest.raises(StationError) as refused:
        load_station()
    assert "SECRET" not in str(refused.value)
    rc, out, err = run(capsys, machine, "install", "--offline", "fixture-data")
    assert rc != 0
    assert "SECRET" not in out and "SECRET" not in err
    logs = [p for p in logs_dir(None).glob("*.log")]
    assert logs, "the run wrote no log, so this proves nothing about the log"
    assert all("SECRET" not in p.read_text(errors="replace") for p in logs)
