# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The script the modem resume unit runs as root (issue #427).

``qmicli``, ``systemctl`` and ``quectel-usbnet`` are fakes that record their
argv in one log, so the order of the steps is asserted, never the exit codes of
a real modem. ``/dev`` and ``/sys`` are temporary directories shaped like the
real ones. No hardware is touched.
"""

from __future__ import annotations

import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from hammunition.hardware import modem_resume_script as script

CARD = """[{dev}] Successfully got card status
Provisioning applications:
	Primary GW:   session doesn't exist
Slot [1]:
	Card state: '{card}'
	UPIN state: 'not-initialized'
	Application [1]:
		Application type:  'usim (2)'
		Application state: '{state}'
"""


@dataclass
class Rig:
    root: Path
    dev: Path
    sysfs: Path
    bin: Path
    calls: Path
    sim: Path
    log: Path
    state: Path

    def calls_made(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def args(self, *extra: str) -> list[str]:
        return [
            "--dev", str(self.dev),
            "--sysfs", str(self.sysfs),
            "--qmicli", str(self.bin / "qmicli"),
            "--systemctl", str(self.bin / "systemctl"),
            "--quectel-usbnet", str(self.bin / "quectel-usbnet-absent"),
            "--settle", "0",
            "--ready-window", "0.05",
            "--poll", "0",
            "--log-file", str(self.log),
            "--state-file", str(self.state),
            *extra,
        ]  # fmt: skip

    def state_file(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in self.state.read_text().splitlines())


def _exe(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


FAKE_QMICLI = """
import sys
from pathlib import Path
rig = Path({root!r})
args = sys.argv[1:]
with open(rig / "calls", "a") as f:
    f.write("qmicli " + " ".join(args) + "\\n")
sim = rig / "sim"
if "--uim-get-card-status" in args:
    if (rig / "unreadable").exists():
        print("error: couldn't open the QMI device", file=sys.stderr)
        sys.exit(1)
    dev = args[args.index("-d") + 1]
    print({card!r}.format(dev=dev, card="present", state=sim.read_text().strip()))
elif "--uim-sim-power-off=1" in args:
    sim.write_text("illegal")
elif "--uim-sim-power-on=1" in args:
    if not (rig / "stuck").exists():
        sim.write_text("ready")
"""

FAKE_SYSTEMCTL = """
import sys
from pathlib import Path
rig = Path({root!r})
with open(rig / "calls", "a") as f:
    f.write("systemctl " + " ".join(sys.argv[1:]) + "\\n")
if "is-active" in sys.argv and (rig / "mm-inactive").exists():
    sys.exit(3)
"""


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    dev, sysfs, bindir = tmp_path / "dev", tmp_path / "sys", tmp_path / "bin"
    dev.mkdir()
    bindir.mkdir()
    # A Quectel modem: usbmisc/cdc-wdm0/device is the interface, its parent the device.
    usbdev = sysfs / "devices" / "usb1" / "1-3"
    iface = usbdev / "1-3:1.4"
    iface.mkdir(parents=True)
    (usbdev / "idVendor").write_text("2c7c\n")
    (usbdev / "idProduct").write_text("0620\n")
    (sysfs / "class" / "usbmisc" / "cdc-wdm0").mkdir(parents=True)
    (sysfs / "class" / "usbmisc" / "cdc-wdm0" / "device").symlink_to(iface)
    (dev / "cdc-wdm0").write_text("")
    r = Rig(
        tmp_path, dev, sysfs, bindir, tmp_path / "calls", tmp_path / "sim",
        tmp_path / "run" / "modem-resume.log", tmp_path / "run" / "modem-sim.state",
    )  # fmt: skip
    r.sim.write_text("illegal")
    _exe(bindir / "qmicli", FAKE_QMICLI.format(root=str(tmp_path), card=CARD))
    _exe(bindir / "systemctl", FAKE_SYSTEMCTL.format(root=str(tmp_path)))
    return r


def run(rig: Rig, *extra: str) -> int:
    return script.main(rig.args(*extra), sleep=lambda _s: None)


def test_card_status_reads_the_usim_application() -> None:
    text = CARD.format(dev="/dev/cdc-wdm0", card="present", state="illegal")
    assert script.card_status(text) == ("present", "illegal")
    assert script.card_status("garbage") == (None, None)


def test_a_healthy_modem_is_left_alone(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    rig.sim.write_text("ready")
    assert run(rig) == 0
    assert rig.calls_made() == [
        "qmicli -p -d " + str(rig.dev / "cdc-wdm0") + " --uim-get-card-status"
    ]
    assert rig.state_file()["state"] == "ok"
    assert "nothing to do" in capsys.readouterr().out


def test_the_wedge_gets_one_power_cycle_between_stop_and_start(rig: Rig) -> None:
    node = str(rig.dev / "cdc-wdm0")
    assert run(rig) == 0
    assert rig.calls_made() == [
        f"qmicli -p -d {node} --uim-get-card-status",
        "systemctl is-active --quiet ModemManager.service",
        "systemctl stop ModemManager.service",
        f"qmicli -d {node} --uim-sim-power-off=1",
        f"qmicli -d {node} --uim-sim-power-on=1",
        f"qmicli -d {node} --uim-get-card-status",
        "systemctl start ModemManager.service",
    ]
    state = rig.state_file()
    assert state["state"] == "recovered"
    assert state["steps"] == (
        "detected,stop-modemmanager,sim-power-off,sim-power-on,app-ready,start-modemmanager"
    )
    assert "steps run: detected, stop-modemmanager, sim-power-off" in rig.log.read_text()
    assert rig.log.stat().st_mode & 0o777 == 0o644
    assert rig.state.stat().st_mode & 0o777 == 0o644


def test_a_recovery_that_does_not_take_is_tried_once_and_names_the_manual_steps(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    (rig.root / "stuck").write_text("")
    assert run(rig) == 1
    calls = rig.calls_made()
    assert sum("--uim-sim-power-off=1" in c for c in calls) == 1
    assert calls[-1] == "systemctl start ModemManager.service"
    assert rig.state_file()["state"] == "failed"
    out = capsys.readouterr().out
    assert "recovery failed: run `sudo systemctl stop ModemManager`" in out
    assert "app-not-ready" in out


def test_quectel_usbnet_is_preferred_when_installed(rig: Rig) -> None:
    _exe(
        rig.bin / "quectel-usbnet",
        f"""
import sys
from pathlib import Path
rig = Path({str(rig.root)!r})
with open(rig / "calls", "a") as f:
    f.write("quectel-usbnet " + " ".join(sys.argv[1:]) + "\\n")
(rig / "sim").write_text("ready")
""",
    )
    assert run(rig, "--quectel-usbnet", str(rig.bin / "quectel-usbnet")) == 0
    calls = rig.calls_made()
    assert "quectel-usbnet sim-recover" in calls
    assert not any("sim-power" in c for c in calls)
    assert rig.state_file()["steps"].split(",")[2] == "quectel-usbnet-sim-recover"


def test_a_failing_quectel_usbnet_falls_back_to_qmicli_in_the_same_attempt(rig: Rig) -> None:
    _exe(
        rig.bin / "quectel-usbnet",
        f"""
import sys
from pathlib import Path
with open(Path({str(rig.root)!r}) / "calls", "a") as f:
    f.write("quectel-usbnet " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(2)
""",
    )
    assert run(rig, "--quectel-usbnet", str(rig.bin / "quectel-usbnet")) == 0
    calls = rig.calls_made()
    assert calls.count("quectel-usbnet sim-recover") == 1
    assert sum("--uim-sim-power-off=1" in c for c in calls) == 1
    assert "quectel-usbnet-failed" in rig.state_file()["steps"]


def test_modemmanager_that_was_not_running_is_not_started(rig: Rig) -> None:
    (rig.root / "mm-inactive").write_text("")
    assert run(rig) == 0
    calls = rig.calls_made()
    assert not any(c.startswith("systemctl stop") or c.startswith("systemctl start") for c in calls)


def test_no_matching_modem_does_nothing(rig: Rig) -> None:
    assert run(rig, "--usb-id", "1234:5678") == 0
    assert rig.calls_made() == []
    assert not rig.state.exists()


def test_a_modem_that_cannot_be_read_is_reported_not_guessed(rig: Rig) -> None:
    (rig.root / "unreadable").write_text("")
    assert run(rig) == 1
    assert not any("power" in c for c in rig.calls_made())
    assert rig.state_file()["state"] == "unknown"
