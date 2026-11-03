# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Planning the hardware role: udev rules and group membership. D-029, M4.

The detection and rule-generation engines already exist (:mod:`.detect`,
:mod:`.udev`); this is the piece that turns them into *what an apply will do to
this machine* — the same disclose-everything-first shape ``install`` has. It
computes a plan and changes nothing; the CLI renders it, and only then runs it.

Two system changes, both idempotent and both disclosed:

- **The rules file.** The whole catalog's rules go to
  ``/etc/udev/rules.d/65-hammunition.rules`` — declarative and harmless for a
  device that is not attached, so writing all of them means a supported device
  works the moment it is plugged in, not only if it happened to be present at
  apply time. If the file on disk already matches, that half is a no-op.
- **Group membership.** The union of the access groups the catalog's devices
  need (``plugdev``, ``dialout``) — added only where the operator is not
  already a member.

Detection is reported alongside but drives nothing (D-020): an apply writes the
same rules whether or not the operator's HackRF is plugged in right now.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hammunition.gpstime.grants import TimeGrants, plan_time_grants
from hammunition.hardware.detect import AttachedDevice, Match, match_catalog, read_usb_bus
from hammunition.hardware.devctl_export import DevctlExport, plan_devctl_export
from hammunition.hardware.gps_resume import GpsResume, plan_gps_resume
from hammunition.hardware.modem_resume import ModemResume, plan_modem_resume
from hammunition.hardware.polkit import PolkitArtifacts, plan_polkit
from hammunition.hardware.udev import RULES_PATH, Omission, rules_file
from hammunition.manifest.hardware import DeviceClass, DeviceManifest

__all__ = ["HardwarePlan", "plan_hardware"]


@dataclass(frozen=True)
class HardwarePlan:
    """What ``hammunition hardware apply`` will do, and what it deliberately won't."""

    user: str
    rules_path: Path
    rules_content: str
    """The complete 65-hammunition.rules the apply will write."""

    rules_already_current: bool
    """True when the file on disk already matches — the write is a no-op."""

    groups_to_add: list[str]
    """Access groups the operator is not yet in; each becomes a gpasswd add."""

    groups_present: list[str]
    """Access groups the operator already has — reported, not touched."""

    omissions: list[Omission]
    """Catalog rules deliberately not emitted, each with why (from udev.py)."""

    detected: list[Match]
    """Recognised devices currently attached. Informational; drives nothing."""

    unrecognised: list[AttachedDevice]
    """Attached devices the catalog does not know — a contributing prompt."""

    polkit: PolkitArtifacts
    """The helper wrapper and polkit action power control needs (D-056)."""

    time: TimeGrants | None = None
    """GPS time's grants (D-058), or None when the caller did not ask for them."""

    gps_resume: GpsResume | None = None
    """The GPS receiver's resume step (issue #177), or None when the caller did
    not ask for it or no catalog entry declares one."""

    modem_resume: ModemResume | None = None
    """The modem SIM-wedge recovery (issue #427), or None when the caller did not
    ask for it or no catalog entry declares one."""

    devctl_export: DevctlExport | None = None
    """The two lists the tray's helper reads (D-056, amended 2026-10-02), or None
    when the caller did not ask for them."""

    @property
    def is_noop(self) -> bool:
        return (
            self.rules_already_current
            and not self.groups_to_add
            and self.polkit.is_noop
            and (self.time is None or self.time.is_noop)
            and (self.gps_resume is None or self.gps_resume.is_noop)
            and (self.modem_resume is None or self.modem_resume.is_noop)
            and (self.devctl_export is None or self.devctl_export.is_noop)
        )


def _device_groups(
    classes: dict[str, DeviceClass], devices: dict[str, DeviceManifest]
) -> list[str]:
    """The union of access groups every catalog hardware entry declares.

    A device inherits its class's groups, so both are read. Sorted for a stable
    plan and a stable test.
    """
    groups: set[str] = set()
    for entry in (*classes.values(), *devices.values()):
        groups.update(entry.groups)
    for device in devices.values():
        if device.device_class and device.device_class in classes:
            groups.update(classes[device.device_class].groups)
    return sorted(groups)


def plan_hardware(
    classes: dict[str, DeviceClass],
    devices: dict[str, DeviceManifest],
    *,
    user: str,
    user_groups_now: frozenset[str],
    attached: list[AttachedDevice] | None = None,
    rules_path: str = RULES_PATH,
    sysfs_root: Path | None = None,
    polkit: PolkitArtifacts | None = None,
    with_time: bool = False,
    with_gps_resume: bool = False,
    with_modem_resume: bool = False,
    with_devctl_export: bool = False,
) -> HardwarePlan:
    """Resolve a hardware plan. Reads sysfs and the current rules file; writes nothing.

    ``attached`` overrides bus detection (for tests and for a caller that has
    already read it); otherwise sysfs is read here. ``user_groups_now`` is the
    operator's current membership, so the plan adds only what is missing.

    ``polkit`` overrides :func:`plan_polkit`'s own resolution the same way
    ``attached`` overrides bus detection: its real inputs are root-owned
    system paths (``/usr/local/libexec/...``, ``/usr/share/polkit-1/...``), so
    a test that wants a plan whose ``is_noop`` is predictable passes one in
    instead of depending on whatever happens to be on the machine running the
    test.

    ``with_time``: `hardware apply` passes True, and the plan carries GPS
    time's grants (D-058); `list` and `doctor` leave it off, so neither asks
    dpkg anything.

    ``with_gps_resume``: `hardware apply` passes True unless `--no-gps-resume`,
    and the plan carries the resume step (issue #177) when an entry declares
    ``resume: {step: gpsd_reopen}``. Raises
    :class:`~hammunition.hardware.gps_resume.GpsResumeError` on a file at the
    step's path that Hammunition did not write.

    ``with_modem_resume``: `hardware apply` passes True unless `--no-modem-resume`,
    and the plan carries the SIM-wedge recovery (issue #427) when an entry declares
    ``resume: {step: modem_sim_recover}``. Raises
    :class:`~hammunition.hardware.modem_resume.ModemResumeError` on a file at the
    step's path that Hammunition did not write.

    ``with_devctl_export``: `hardware apply` passes True and the plan carries the
    helper's device and service lists (D-056, amended 2026-10-02). Raises
    :class:`~hammunition.hardware.devctl_export.DevctlExportError` on a file at
    either path that Hammunition did not write.
    """
    all_entries: list[DeviceClass | DeviceManifest] = [*classes.values(), *devices.values()]
    content, omissions = rules_file(all_entries)

    path = Path(rules_path)
    try:
        current = path.read_text()
    except (OSError, UnicodeDecodeError):
        current = None

    every: dict[str, DeviceClass | DeviceManifest] = {**classes, **devices}
    bus = attached if attached is not None else read_usb_bus(sysfs_root)
    matches, unrecognised = match_catalog(bus, every)

    wanted = _device_groups(classes, devices)
    to_add = [g for g in wanted if g not in user_groups_now]
    present = [g for g in wanted if g in user_groups_now]

    return HardwarePlan(
        user=user,
        rules_path=path,
        rules_content=content,
        rules_already_current=current == content,
        groups_to_add=to_add,
        groups_present=present,
        omissions=omissions,
        detected=matches,
        unrecognised=unrecognised,
        polkit=polkit if polkit is not None else plan_polkit(),
        time=plan_time_grants() if with_time else None,
        gps_resume=_resume_step(all_entries) if with_gps_resume else None,
        modem_resume=_modem_step(all_entries) if with_modem_resume else None,
        devctl_export=plan_devctl_export(classes, devices) if with_devctl_export else None,
    )


def _resume_step(entries: list[DeviceClass | DeviceManifest]) -> GpsResume | None:
    """The resume step, when any catalog entry asks for it by name."""
    wanted = any(e.resume is not None and e.resume.step == "gpsd_reopen" for e in entries)
    return plan_gps_resume() if wanted else None


def _modem_step(entries: list[DeviceClass | DeviceManifest]) -> ModemResume | None:
    """The modem recovery, when any catalog entry asks for it by name."""
    wanted = any(e.resume is not None and e.resume.step == "modem_sim_recover" for e in entries)
    return plan_modem_resume() if wanted else None
