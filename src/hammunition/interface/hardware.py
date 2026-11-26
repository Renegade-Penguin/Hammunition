# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``hardware state`` as data.  D-059, D-056.

Each device carries the same keys, and the same values, the privileged
helper's ``state`` array does (name, summary, address, identifier, method,
parked, kept, attached), so a front end reading either source reads one
shape -- a tray that switches source must see the same thing. ``kept`` and
``attached`` are real here, not placeholders: a device kept parked across a
reboot but not currently on the bus is carried too, the way the helper and
the kept-off text form (D-056) both already do, with the same ``parked:
null`` / ``method: "usb_deauthorize"`` / name-sorted shape the helper uses
for a row with nothing attached.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from hammunition.interface.envelope import Strict, described

if TYPE_CHECKING:
    from hammunition.hardware.power import Parkable

__all__ = ["HardwareDocument", "SimView", "build_hardware", "render_hardware"]


@dataclass(frozen=True)
class DeviceView(Strict):
    """A catalogued, parkable device -- attached now, or kept parked and not."""

    name: str = described("the catalog entry")
    summary: str = described("one line; empty for a kept entry with nothing attached")
    address: str = described("the USB bus address, e.g. `1-4`: what tells two of a kind apart")
    identifier: str = described("`vendor:product` as the bus reported it")
    method: str = described("how it is parked, e.g. `usb_deauthorize`")
    parked: bool | None = described(
        "parked now; null for a kept entry with nothing attached -- there is "
        "nothing on the bus to read a state off"
    )
    kept: bool = described("kept parked across reboots")
    attached: bool = described("plugged in now; a kept entry may name a device that is not")


@dataclass(frozen=True)
class SkippedView(Strict):
    """A catalogued device that is attached but cannot be parked right now."""

    unit: str = described("the catalog entry")
    why: str = described("why not")


@dataclass(frozen=True)
class SimView(Strict):
    """What the modem resume step last found (issue #427)."""

    state: str = described(
        "`ok`, `wedged` (found, recovery not finished), `recovered`, `failed` or `unknown`"
    )
    wedged: bool = described("the SIM application is stuck now: `wedged`, `failed` or `unknown`")
    steps: tuple[str, ...] = described("the recovery steps that ran, in order; empty when none")
    device: str = described("the QMI control node the check read, e.g. `/dev/cdc-wdm0`")


@dataclass(frozen=True)
class HardwareDocument(Strict):
    """Which catalogued devices can be parked, which are parked now, and
    which are kept parked across reboots -- attached or not.

    Read fresh from sysfs (and the kept-off rules file) on every call,
    unprivileged."""

    KIND: ClassVar[str] = "hardware"

    devices: tuple[DeviceView, ...] = described("attached devices first, then kept-but-absent ones")
    skipped: tuple[SkippedView, ...] = described("attached but not parkable right now")
    kept_error: str | None = described(
        "why the kept-off entries could not be read; null when they were"
    )
    modem_sim: SimView | None = described(
        "the modem resume step's last SIM check (issue #427); null when it has not "
        "run since boot or found no modem"
    )


def build_hardware(
    rows: Sequence[tuple[Parkable, bool]],
    absent: Sequence[Any],
    skipped: Sequence[tuple[str, str]],
    kept_error: str | None = None,
    sim: dict[str, str] | None = None,
) -> HardwareDocument:
    attached = [
        DeviceView(
            name=p.name,
            summary=p.summary,
            address=p.address,
            identifier=p.identifier,
            method=str(p.method),
            parked=p.parked,
            kept=is_kept,
            attached=True,
        )
        for p, is_kept in rows
    ]
    # The same values the helper's `state` prints for a kept entry with
    # nothing attached (devctl.py's `_state`): `parked` is null -- there is
    # no device on the bus to read a state off -- `method` is always
    # `usb_deauthorize`, kept persistence's only method (D-056), and rows
    # are sorted by (name, address), same as the helper's own
    # `sorted(kept, key=lambda e: (e.name, e.address))`. I1: the helper is
    # the source of truth, so this must match it value for value, not only
    # carry the same keys -- confirmed by
    # test_a_device_carries_every_key_and_value_the_helper_prints.
    missing = [
        DeviceView(
            name=e.name,
            summary="",
            address=e.address,
            identifier=f"{e.vendor}:{e.product}",
            method="usb_deauthorize",
            parked=None,
            kept=True,
            attached=False,
        )
        for e in sorted(absent, key=lambda e: (e.name, e.address))
    ]
    return HardwareDocument(
        devices=(*attached, *missing),
        skipped=tuple(SkippedView(unit=unit, why=why) for unit, why in skipped),
        kept_error=kept_error,
        modem_sim=_sim_view(sim),
    )


_STUCK = frozenset({"wedged", "failed", "unknown"})


def _sim_view(sim: dict[str, str] | None) -> SimView | None:
    if not sim or "state" not in sim:
        return None
    state = sim["state"]
    return SimView(
        state=state,
        wedged=state in _STUCK,
        steps=tuple(x for x in sim.get("steps", "").split(",") if x),
        device=sim.get("device", ""),
    )


def render_hardware(doc: HardwareDocument) -> list[str]:
    """``hardware state`` as the terminal shows it (the kept-off form, D-056)."""
    lines = [f"  {s.unit}: not parkable right now — {s.why}" for s in doc.skipped]
    if doc.kept_error is not None:
        lines += ["", f"Kept-off entries could not be read: {doc.kept_error}"]
    lines += _render_sim(doc.modem_sim)
    here = [d for d in doc.devices if d.attached]
    gone = [d for d in doc.devices if not d.attached]
    if here:
        lines.append(f"{'device':24} {'address':10} {'state':8} {'kept':5} summary")
        for d in here:
            lines.append(
                f"{d.name:24} {d.address:10} {'parked' if d.parked else 'awake':8} "
                f"{'yes' if d.kept else 'no':5} {d.summary}"
            )
    if gone:
        lines += [
            "",
            "Kept parked, not attached (cleared with `hammunition hardware wake NAME@ADDRESS`):",
        ]
        lines += [f"  {d.name}@{d.address}  {d.identifier}" for d in gone]
    if not here and not gone:
        lines.append(
            "No parkable device is attached. A device is parkable when its catalog "
            "entry carries a power_control block and it is plugged in now."
        )
        return lines
    lines += [
        "",
        "`hammunition hardware park NAME` keeps it parked across reboots; "
        + "`park --until-reboot NAME` lets a reboot wake it; `wake NAME` brings it back.",
    ]
    return lines


def _render_sim(sim: SimView | None) -> list[str]:
    """The modem's SIM line: silent when all is well, plain when it is not."""
    if sim is None or sim.state == "ok":
        return []
    where = f" ({sim.device})" if sim.device else ""
    ran = f" Steps run: {', '.join(sim.steps)}." if sim.steps else ""
    if sim.state == "recovered":
        return [f"  modem{where}: SIM was wedged and the resume step recovered it.{ran}"]
    if sim.state == "unknown":
        return [
            f"  modem{where}: SIM state could not be read at the last resume check.{ran} "
            "Re-run it: `sudo systemctl start hammunition-modem-resume.service`."
        ]
    return [
        f"  modem{where}: SIM wedged (QMI application state `illegal`; ModemManager will "
        f"not retry).{ran} Recover: `sudo systemctl start hammunition-modem-resume.service`, "
        "or see `journalctl -u hammunition-modem-resume` for the manual steps."
    ]
