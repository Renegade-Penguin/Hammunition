# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The modem's SIM-wedge recovery: installed by ``hardware apply``, removed by
``hardware unapply``.  Issue #427, D-058's resume step as the pattern (D-056).

The Quectel EM160R-GL can come back from a suspend or a boot with its USIM
application in QMI state ``illegal`` while the SIM is fine; ModemManager then
marks the modem ``failed: sim-missing`` and never retries. Two root-owned files
recover it, once per resume or boot:

- ``/usr/local/libexec/hammunition-modem-resume``, the script in
  :mod:`hammunition.hardware.modem_resume_script`, installed whole with our
  header, 0755. It is the only thing that runs ``qmicli`` with write verbs, and
  it runs as root because systemd runs it: **no polkit action and no helper
  verb is added or widened**, and the privileged devctl helper is not involved
  (it has no verb for this; see the decision's notes on the interactive path);
- ``/etc/systemd/system/hammunition-modem-resume.service``, a oneshot ordered
  after the four sleep targets and after ModemManager, wanted by the sleep
  targets and ``multi-user.target``, enabled (never started).

Both are staged and installed by ``install -D``, disclosed in full in the plan,
read back afterwards (D-031), and removed by content: a file is removed only
when it starts with the header Hammunition writes. A file at either path
without that header refuses the plan rather than being overwritten.

A device asks for the step by name (``resume: {step: modem_sim_recover}``); the
catalog never carries the command. Without ``qmicli`` installed there is
nothing to run, and no step is planned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hammunition.backends.base import Command

__all__ = [
    "LOG",
    "PATHS",
    "QMICLI",
    "SCRIPT",
    "SLEEP_TARGETS",
    "STATE",
    "SYSTEMD_DIR",
    "UNIT_HEADER",
    "UNIT_NAME",
    "ModemResume",
    "ModemResumeError",
    "ModemResumeRemoval",
    "ResumeStatus",
    "disclose",
    "install_commands",
    "plan_modem_resume",
    "plan_modem_resume_removal",
    "read_sim_state",
    "removal_commands",
    "script_content",
    "stage",
    "status",
    "unit_content",
    "unit_path",
    "verify",
    "verify_removal",
    "wants_links",
]

QMICLI = "/usr/bin/qmicli"
"""libqmi's tool (``libqmi-utils``). Where it is not, the script has nothing to run."""
SCRIPT = "/usr/local/libexec/hammunition-modem-resume"
SYSTEMD_DIR = "/etc/systemd/system"
STATE = "/run/hammunition/modem-sim.state"
"""The last run's result (0644, ``key=value``), for ``hardware state`` to read unprivileged."""
LOG = "/run/hammunition/modem-resume.log"
UNIT_NAME = "hammunition-modem-resume.service"
SLEEP_TARGETS = (
    "suspend.target",
    "hibernate.target",
    "hybrid-sleep.target",
    "suspend-then-hibernate.target",
)
BOOT_TARGET = "multi-user.target"

PATHS: dict[str, str] = {
    "QMICLI": QMICLI,
    "SCRIPT": SCRIPT,
    "SYSTEMD_DIR": SYSTEMD_DIR,
    "STATE": STATE,
    "LOG": LOG,
}
"""Each path attribute and its real default, for the fixtures that repoint them.
Read as module globals at call time, so a test's ``monkeypatch.setattr`` holds."""

UNIT_HEADER = "# Written by `hammunition hardware apply` (issue #427)."
_SCRIPT_HEADER = (
    f"{UNIT_HEADER} Run by\n"
    f"# {UNIT_NAME} after a resume and at boot: recovers a modem whose SIM\n"
    "# application is stuck in QMI state 'illegal'.\n"
    "# `hammunition hardware unapply` removes this file.\n"
)
_STAGED_SCRIPT = "hammunition-modem-resume"
_STAGED_UNIT = "hammunition-modem-resume.service"


class ModemResumeError(Exception):
    """A file at the step's path that Hammunition did not write."""


def unit_path() -> str:
    return f"{SYSTEMD_DIR}/{UNIT_NAME}"


def wants_links() -> tuple[str, ...]:
    """The symlinks ``systemctl enable`` makes for the unit's ``WantedBy=``."""
    return tuple(
        f"{SYSTEMD_DIR}/{target}.wants/{UNIT_NAME}" for target in (*SLEEP_TARGETS, BOOT_TARGET)
    )


def script_content() -> str:
    """The installed script: a shebang, our header, then the module's own source."""
    source = Path(__file__).with_name("modem_resume_script.py").read_text(encoding="utf-8")
    return f"#!/usr/bin/python3 -I\n{_SCRIPT_HEADER}{source}"


def unit_content() -> str:
    targets = " ".join(SLEEP_TARGETS)
    return (
        f"{UNIT_HEADER} After a suspend, a\n"
        "# hibernation or a boot, recovers a modem whose SIM application is in QMI\n"
        "# state 'illegal' (ModemManager would mark it failed and never retry).\n"
        "# `hammunition hardware unapply` removes this file and the script it runs.\n"
        "[Unit]\n"
        "Description=Recover a modem with a wedged SIM after resume or boot (Hammunition)\n"
        f"After={targets} ModemManager.service\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "TimeoutStartSec=180\n"
        f"ExecStart={SCRIPT}\n"
        "\n"
        "[Install]\n"
        f"WantedBy={targets} {BOOT_TARGET}\n"
    )


def _read(path: str) -> str | None:
    """The file's text; None when nothing is there. A directory, a file that is
    not UTF-8 or one this account cannot read is something, and reads as ""
    so it is never taken for ours."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, UnicodeDecodeError):
        return ""


def _enabled() -> bool:
    return all(Path(link).is_symlink() for link in wants_links())


def _script_executable() -> bool:
    try:
        return os.stat(SCRIPT).st_mode & 0o777 == 0o755
    except OSError:
        return False


def read_sim_state() -> dict[str, str] | None:
    """The last run's ``key=value`` lines, or None when there is no file.

    ``state`` is ``ok``, ``wedged`` (found, recovery not finished), ``recovered``,
    ``failed`` or ``unknown``. A file left by an older boot cannot exist: ``/run``
    is a tmpfs."""
    text = _read(STATE)
    if not text:
        return None
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out or None


@dataclass(frozen=True)
class ModemResume:
    """What ``hardware apply`` will do for the modem recovery step."""

    qmicli: bool
    """libqmi's ``qmicli`` is installed. Without it there is nothing to run."""
    script_current: bool
    unit_current: bool
    enabled: bool
    """Every wanted-by link is present."""

    @property
    def is_noop(self) -> bool:
        return not self.qmicli or (self.script_current and self.unit_current and self.enabled)


def _ours(text: str | None) -> bool:
    """Our header on the first line (the unit) or the second (after the shebang)."""
    return text is not None and any(
        line.startswith(UNIT_HEADER) for line in text.split("\n", 2)[:2]
    )


def _refuse_foreign(path: str, text: str | None) -> None:
    if text is not None and not _ours(text):
        raise ModemResumeError(
            f"{path} exists and was not written by Hammunition. The modem resume step "
            f"(issue #427) installs a file there and never overwrites one it did not "
            f"write. Move it aside and re-run, or use `--no-modem-resume`. Nothing was changed."
        )


def plan_modem_resume() -> ModemResume:
    """Read what is installed. Raises :class:`ModemResumeError` on a foreign file,
    but only where ``qmicli`` is installed and the step would be."""
    if not Path(QMICLI).exists():
        return ModemResume(qmicli=False, script_current=False, unit_current=False, enabled=False)
    script = _read(SCRIPT)
    unit = _read(unit_path())
    _refuse_foreign(SCRIPT, script)
    _refuse_foreign(unit_path(), unit)
    return ModemResume(
        qmicli=True,
        script_current=script == script_content() and _script_executable(),
        unit_current=unit == unit_content(),
        enabled=_enabled(),
    )


def disclose(step: ModemResume) -> list[str]:
    if not step.qmicli:
        return [
            "Modem resume step (issue #427): qmicli (package libqmi-utils) is not installed,",
            "  so there is nothing to run after a resume; not installed. Install it and re-run.",
        ]
    if step.is_noop:
        return []
    lines = [
        "Will install the modem SIM-wedge recovery (issue #427). A Quectel EM160R-GL can come",
        "  back from a suspend or a boot with its SIM application in QMI state 'illegal';",
        "  ModemManager then marks it failed and never retries. After every suspend,",
        "  hibernation and boot this reads the card status (read-only) and, only on",
        "  'illegal', makes ONE recovery attempt as root: stop ModemManager, power the SIM off",
        "  and on (`quectel-usbnet sim-recover` if installed, else `qmicli --uim-sim-power-off=1`",
        "  then `--uim-sim-power-on=1`), wait up to 15 s for 'ready', start ModemManager. It",
        "  does nothing when no 2c7c:0620 modem is present, so a parked modem is never woken.",
        "  It adds no polkit action and no helper verb: systemd runs it as root.",
    ]
    if not step.script_current:
        lines += [
            f"  {SCRIPT} (root-owned 0755, run as root by the unit):",
            *(f"    {line}" for line in script_content().splitlines()),
        ]
    if not step.unit_current:
        lines += [
            f"  {unit_path()}:",
            *(f"    {line}" for line in unit_content().splitlines()),
        ]
    if not step.enabled or not step.unit_current:
        lines.append(
            "  Enabled for suspend, hibernate, hybrid-sleep, suspend-then-hibernate and"
            " multi-user (boot); it does not run now."
        )
    lines += [
        f"  Inspect: `systemctl cat {UNIT_NAME}`, `systemctl status {UNIT_NAME}`,",
        f"  `journalctl -u {UNIT_NAME}`, or without the journal `cat {LOG}`;",
        f"  the result is in {STATE} and `hammunition hardware state` shows 'SIM wedged'.",
        "  Reverse: `hammunition hardware unapply`. `--no-modem-resume` leaves it out of this run.",
    ]
    return lines


def install_commands(step: ModemResume, staging_root: str) -> list[Command]:
    if step.is_noop:
        return []
    out: list[Command] = []
    if not step.script_current:
        out.append(
            Command(
                argv=("install", "-D", "-m", "0755", f"{staging_root}/{_STAGED_SCRIPT}", SCRIPT),
                description=f"Install the modem resume script to {SCRIPT}",
                requires_root=True,
            )
        )
    if not step.unit_current:
        out += [
            Command(
                argv=("install", "-D", "-m", "0644", f"{staging_root}/{_STAGED_UNIT}", unit_path()),
                description=f"Install the modem resume unit to {unit_path()}",
                requires_root=True,
            ),
            Command(
                argv=("systemctl", "daemon-reload"),
                description=f"Reload systemd so it reads {UNIT_NAME}",
                requires_root=True,
            ),
        ]
    if not step.enabled or not step.unit_current:
        out.append(
            Command(
                argv=("systemctl", "enable", UNIT_NAME),
                description=f"Enable {UNIT_NAME} for the sleep targets and boot (it does not run now)",
                requires_root=True,
            )
        )
    return out


def stage(step: ModemResume, staging_dir: Path) -> None:
    if step.is_noop:
        return
    if not step.script_current:
        (staging_dir / _STAGED_SCRIPT).write_text(script_content())
        # Semgrep: a deliberate mode (0755/0644 on installed files and launchers, 0700 private); nothing group- or world-writable.
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
        os.chmod(staging_dir / _STAGED_SCRIPT, 0o755)
    if not step.unit_current:
        (staging_dir / _STAGED_UNIT).write_text(unit_content())
        os.chmod(staging_dir / _STAGED_UNIT, 0o644)


def verify(step: ModemResume) -> list[str]:
    """D-031: the files read back and the links exist, not the exit codes."""
    if not step.qmicli:
        return []
    problems: list[str] = []
    if _read(SCRIPT) != script_content():
        problems.append(f"{SCRIPT} on disk does not match what we wrote")
    elif not _script_executable():
        problems.append(f"{SCRIPT} is not mode 0755")
    if _read(unit_path()) != unit_content():
        problems.append(f"{unit_path()} on disk does not match what we wrote")
    missing = [link for link in wants_links() if not Path(link).is_symlink()]
    if missing:
        problems.append(f"{UNIT_NAME} is not enabled: {', '.join(missing)} missing")
    return problems


ResumeStatus = Literal["installed", "stale", "absent"]


def status() -> ResumeStatus:
    """installed as this engine writes it, present but not current, or absent."""
    script = _read(SCRIPT)
    unit = _read(unit_path())
    if script is None and unit is None:
        return "absent"
    if script == script_content() and unit == unit_content() and _enabled():
        return "installed"
    return "stale"


@dataclass(frozen=True)
class ModemResumeRemoval:
    unit_ours: bool
    script_ours: bool

    @property
    def is_empty(self) -> bool:
        return not (self.unit_ours or self.script_ours)


def plan_modem_resume_removal() -> ModemResumeRemoval:
    """By content, not by the log: only a file that starts with our header."""
    return ModemResumeRemoval(unit_ours=_ours(_read(unit_path())), script_ours=_ours(_read(SCRIPT)))


def removal_commands(removal: ModemResumeRemoval) -> list[Command]:
    out: list[Command] = []
    if removal.unit_ours:
        out += [
            Command(
                argv=("systemctl", "disable", UNIT_NAME),
                description=f"Disable {UNIT_NAME}, removing its links from the targets",
                requires_root=True,
            ),
            Command(
                argv=("rm", "-f", unit_path()),
                description=f"Remove {unit_path()}, written by Hammunition (issue #427)",
                requires_root=True,
            ),
        ]
    if removal.script_ours:
        out.append(
            Command(
                argv=("rm", "-f", SCRIPT, STATE, LOG),
                description=f"Remove {SCRIPT} and its last run's state and log (issue #427)",
                requires_root=True,
            )
        )
    if removal.unit_ours:
        out.append(
            Command(
                argv=("systemctl", "daemon-reload"),
                description=f"Reload systemd so it forgets {UNIT_NAME}",
                requires_root=True,
            )
        )
    return out


def verify_removal(removal: ModemResumeRemoval) -> list[str]:
    problems: list[str] = []
    if removal.unit_ours:
        if Path(unit_path()).exists():
            problems.append(f"{unit_path()} is still present")
        problems += [
            f"{link} is still present" for link in wants_links() if Path(link).is_symlink()
        ]
    if removal.script_ours and Path(SCRIPT).exists():
        problems.append(f"{SCRIPT} is still present")
    return problems
