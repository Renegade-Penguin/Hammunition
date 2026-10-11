# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Planning and rendering user services.  D-073 §6, amended 2026-10-02.

Pure: given a manifest's ``user_services`` block, the station, and the hardware
catalog, it decides what to render, defer or skip, and produces the unit-file
text. A *plain* service (no station condition, no station value, no device)
needs neither the station nor the catalog and is always planned; the rig's
services are the ones that need both. The engine (``execute.py``) writes it as the operator and enables it; the
plan view (``interface/plan.py``) discloses it. Nothing here touches the
filesystem or systemd.

The unit file is rendered from fixed fields — the catalog never carries unit
syntax (D-073 §6). A station value is substituted into ``exec`` and re-checked
by the same rule the schema enforces, so a value that slipped a shell character
past ``station set`` cannot reach ``ExecStart=``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition.deferral import Deferral
from hammunition.manifest.schema import STATION_REF, UserService, VenvInstall
from hammunition.paths import venv_root
from hammunition.rig import RigError, resolve_rig
from hammunition.station import Station

if TYPE_CHECKING:
    from hammunition.manifest.hardware import DeviceClass, DeviceManifest
    from hammunition.manifest.schema import PackageManifest

__all__ = [
    "HEADER",
    "HEADER_PREFIX",
    "PlanUserServiceError",
    "PlannedUserService",
    "device_unit_name",
    "header_for",
    "is_ours",
    "plan_user_services",
    "render_unit_file",
    "service_venv_dir",
]

#: The first words of every unit file the engine writes; the catalog unit that
#: wrote it follows. Uninstall removes a file only when it still starts with a
#: header of this shape (D-073 §6d): a file the operator rewrote is left in
#: place and named.
HEADER_PREFIX = "# Written by Hammunition (catalog unit `"


def header_for(unit: str) -> str:
    """The first line of a unit file written for catalog unit *unit*."""
    return f"{HEADER_PREFIX}{unit}`, D-073)."


#: The rig's header, kept as the name D-073's code and tests have always used.
HEADER = header_for("rig-service")


def is_ours(text: str) -> bool:
    """True when *text* is a unit file this engine wrote: its first line is a
    header naming a catalog unit, whichever one."""
    first = text.split("\n", 1)[0]
    return (
        first.startswith(HEADER_PREFIX)
        and first.endswith("`, D-073).")
        and ("`" not in first[len(HEADER_PREFIX) : -len("`, D-073).")])
    )


#: Characters systemd leaves unescaped in a path-derived unit name. ``/`` is
#: handled separately (it becomes ``-``); everything else outside this set,
#: ``-`` included, becomes ``\xNN``. A leading ``.`` is escaped too.
_UNIT_SAFE = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ:_.")


class PlanUserServiceError(ValueError):
    """A user service cannot be rendered — a value failed the post-substitution
    safety check. A bug or a tampered station file, never ordinary input."""


@dataclass(frozen=True)
class PlannedUserService:
    """One user service this plan will write and enable."""

    name: str
    description: str
    exec_argv: tuple[str, ...]
    unit_body: str
    device_path: str | None
    """The resolved ``binds_to_device`` path, or None. The service's restart is
    conditional on this being present."""
    filled_from: tuple[str, ...]
    """The station values that fed it, named for the plan's disclosure."""
    listens: tuple[tuple[str, int], ...]
    unit: str = "rig-service"
    """The catalog unit carrying it (the default is the rig's, where this began)."""
    plain: bool = False
    """True for a plain service (:attr:`UserService.is_plain`): an install also
    ``try-restart``s it, so an upgrade reaches one that is running and a stopped
    one stays stopped until login."""
    first_start: str | None = None
    """What the service does the first time it starts (the manifest's
    ``first_start``), disclosed in the plan; None when the manifest says nothing."""


def device_unit_name(path: str) -> str:
    """systemd's device unit name for a ``/dev`` path, e.g.
    ``dev-serial-by\\x2did-...\\x2dport0.device`` — the same escaping
    ``systemd-escape --path --suffix=device`` produces, done in pure Python so
    it is testable and no subprocess runs."""
    stripped = path.strip("/")
    out: list[str] = []
    for index, char in enumerate(stripped):
        if char == "/":
            out.append("-")
        elif char in _UNIT_SAFE and not (char == "." and index == 0):
            out.append(char)
        else:
            out.append(f"\\x{ord(char):02x}")
    return "".join(out) + ".device"


def render_unit_file(
    name: str,
    description: str,
    exec_argv: Sequence[str],
    device_unit: str | None,
    *,
    unit: str = "rig-service",
    restart: str = "on-failure",
    restart_sec: int = 5,
    restart_prevent_exit_status: Sequence[int] = (),
    station_fed: bool = True,
) -> str:
    """The systemd user unit, rendered from fixed fields.  D-073 §5.

    ``unit`` names the catalog unit in the header; ``station_fed`` says the
    file follows station values (the rig's does), which changes one comment.
    ``restart`` and ``restart_sec`` come from the manifest's fixed set, never
    free text.
    """
    exec_line = " ".join(exec_argv)
    changed = (
        f"# Changed by `hammunition station set` then `hammunition install {unit}`;"
        if station_fed
        else f"# Changed by `hammunition install {unit}`;"
    )
    lines = [
        header_for(unit),
        changed,
        f"# removed by `hammunition uninstall {unit}`. Do not edit: a reinstall",
        "# replaces this file whole.",
        "[Unit]",
        f"Description={description}",
    ]
    if device_unit is not None:
        lines += [f"BindsTo={device_unit}", f"After={device_unit}"]
    # A start limit so a service that cannot start — the user manager still
    # lacking `dialout`, the binary gone — stops after five tries in half a
    # minute rather than restarting forever (review minor).
    lines += ["StartLimitIntervalSec=30", "StartLimitBurst=5"]
    lines += [
        "",
        "[Service]",
        f"ExecStart={exec_line}",
        f"Restart={restart}",
        f"RestartSec={restart_sec}",
        *(
            [f"RestartPreventExitStatus={' '.join(str(c) for c in restart_prevent_exit_status)}"]
            if restart_prevent_exit_status
            else []
        ),
        "NoNewPrivileges=yes",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]
    if device_unit is not None:
        lines.append(f"WantedBy={device_unit}")
    return "\n".join(lines) + "\n"


def _matches(conditions: Mapping[str, str], facts: Mapping[str, str]) -> bool:
    """True when every key in *conditions* equals the fact under it."""
    return all(facts.get(key) == value for key, value in conditions.items())


def service_venv_dir(manifest: PackageManifest, owner: str | None = None) -> Path | None:
    """The virtualenv ``{venv}`` names for *manifest*'s services: where the venv
    backend puts this unit's environment for *owner* (the operator, never root's
    home under ``sudo``), or None when the manifest has no venv block."""
    if not any(isinstance(block.install, VenvInstall) for block in manifest.install):
        return None
    return venv_root(owner or None) / manifest.name


def _substitute(
    word: str, values: Mapping[str, str], interpreter: str, venv_dir: Path | None = None
) -> str:
    """Replace every ``{station.*}``, ``{python}`` and ``{venv}`` in *word*,
    re-checking the result is one safe argv word. A reference with no value is a
    bug (the caller decides what is needed before calling); an unsafe result
    raises."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise PlanUserServiceError(f"no value for {{station.{key}}}")
        return values[key]

    result = STATION_REF.sub(repl, word).replace("{python}", interpreter)
    if "{venv}" in result:
        if venv_dir is None:
            raise PlanUserServiceError("{venv} is used but this manifest has no venv block")
        result = result.replace("{venv}", str(venv_dir))
    # A quote or backslash is as much a shell token to systemd as the others;
    # RIG_DEVICE already excludes them, this is defence in depth (review minor).
    if any(c in result for c in " \t\n\r;|&$`%<>\"'\\"):
        raise PlanUserServiceError(
            f"substituted exec word {result!r} carries a shell character or whitespace; "
            f"refusing to write it into a unit file (D-073 §6a)"
        )
    return result


def _needed(kind: str) -> tuple[str, ...]:
    """The station values a rig of *kind* needs before its service renders."""
    if kind == "cat":
        return ("rig_device", "rig_baud")
    return ("rig_device", "rig_ptt_line")


def plan_user_services(
    manifest: PackageManifest,
    station: Station,
    devices: Mapping[str, DeviceManifest | DeviceClass] | None,
    *,
    model_lister: Callable[[], Mapping[int, tuple[int, int]]] | None = None,
    interpreter: str | None = None,
    venv_dir: Path | None = None,
) -> tuple[list[PlannedUserService], list[Deferral], list[str]]:
    """Plan *manifest*'s user services against *station*.

    Returns ``(planned, deferrals, notes)``: services to render, D-035
    deferrals for a missing value or a rig no longer catalogued, and operator
    notes for a skipped service (flrig, VOX) or an unmeasured radio.

    A *plain* service (:attr:`UserService.is_plain`) is always planned: it
    needs neither the station nor the hardware catalog, so *devices* may be
    ``None`` for a manifest made only of them. The rest are the rig's and are
    planned as before, each group on its own.

    ``interpreter`` fills ``{python}`` in an exec (the loopback filter runs
    under the engine's own interpreter); it defaults to :data:`sys.executable`.
    ``venv_dir`` fills ``{venv}``, the unit's own virtualenv
    (:func:`service_venv_dir`).
    """
    import sys

    if not manifest.user_services:
        return [], [], []

    python = interpreter or sys.executable
    planned: list[PlannedUserService] = []
    deferred: list[Deferral] = []
    for svc in manifest.user_services:
        if svc.is_plain:
            if venv_dir is None and any("{venv}" in word for word in svc.exec):
                # The loader refuses a manifest with no venv block, so this is a
                # caller that did not pass service_venv_dir(): a bug, not an
                # operator's problem, and not something to defer quietly.
                raise PlanUserServiceError(
                    f"{svc.name}: {{venv}} is used but no venv directory was given"
                )
            try:
                exec_argv = tuple(_substitute(word, {}, python, venv_dir) for word in svc.exec)
            except PlanUserServiceError as exc:
                # A home the unit file cannot carry (a space, a `%`): named, not a traceback.
                deferred.append(
                    Deferral(
                        subject=manifest.name,
                        what=f"will not run {svc.name}",
                        why=str(exc),
                        remedy="the program's directory must be a path with no whitespace or "
                        "shell character in it",
                    )
                )
                continue
            planned.append(_planned(manifest, svc, exec_argv, None, ()))
    rig_entries = [svc for svc in manifest.user_services if not svc.is_plain]
    if not rig_entries:
        return planned, deferred, []
    rig_planned, deferrals, notes = _plan_rig(
        manifest, rig_entries, station, devices, model_lister, python, venv_dir
    )
    return planned + rig_planned, deferred + deferrals, notes


def _planned(
    manifest: PackageManifest,
    svc: UserService,
    exec_argv: tuple[str, ...],
    device_path: str | None,
    filled: tuple[str, ...],
) -> PlannedUserService:
    device_unit = device_unit_name(device_path) if device_path else None
    body = render_unit_file(
        svc.name,
        svc.description,
        exec_argv,
        device_unit,
        unit=manifest.name,
        restart=svc.restart,
        restart_sec=svc.restart_sec,
        restart_prevent_exit_status=svc.restart_prevent_exit_status,
        station_fed=not svc.is_plain,
    )
    return PlannedUserService(
        name=svc.name,
        description=svc.description,
        exec_argv=exec_argv,
        unit_body=body,
        device_path=device_path,
        filled_from=filled,
        listens=tuple((lst.address, lst.port) for lst in svc.listens),
        unit=manifest.name,
        plain=svc.is_plain,
        first_start=svc.first_start,
    )


def _plan_rig(
    manifest: PackageManifest,
    entries: Sequence[UserService],
    station: Station,
    devices: Mapping[str, DeviceManifest | DeviceClass] | None,
    model_lister: Callable[[], Mapping[int, tuple[int, int]]] | None,
    python: str,
    venv_dir: Path | None = None,
) -> tuple[list[PlannedUserService], list[Deferral], list[str]]:
    """The station-driven entries: resolve the rig, defer or skip, render."""

    first = entries[0].name
    if devices is None:
        # Without the hardware catalog the rig cannot be resolved, so the
        # services defer by name (D-035) rather than resolve to nothing.
        return (
            [],
            [
                Deferral(
                    subject=manifest.name,
                    what=f"will not run {first}",
                    why="the hardware catalog was not available to resolve the rig",
                    remedy="run this through `hammunition install`, which loads it",
                )
            ],
            [],
        )
    if station.rig is None:
        return (
            [],
            [
                Deferral(
                    subject=manifest.name,
                    what=f"will not run {first}",
                    why="station values not set: rig, rig_device",
                    remedy=(
                        "run `hammunition station set --rig <device> --rig-device <path>` "
                        "(and --rig-baud or --rig-ptt-line for the rig's kind)"
                    ),
                )
            ],
            [],
        )

    try:
        res = resolve_rig(station.rig, devices, model_lister=model_lister)
    except RigError as exc:
        return (
            [],
            [
                Deferral(
                    subject=manifest.name,
                    what=f"will not run {first}",
                    why=f"the rig {station.rig!r} is no longer in the catalog: {exc}",
                    remedy="pick a rig `hammunition list` shows, or hamlib:<model>",
                )
            ],
            [],
        )

    facts = {
        "rig_kind": res.kind,
        "rig_owner": station.rig_owner or "rigctld",
        "rig_ptt_line": station.rig_ptt_line or "",
    }
    values: dict[str, str] = {}
    if station.rig_device is not None:
        values["rig_device"] = station.rig_device
    if station.rig_baud is not None:
        values["rig_baud"] = str(station.rig_baud)
    if res.hamlib_model is not None:
        values["rig_hamlib_model"] = str(res.hamlib_model)
    if station.rig_ptt_line is not None:
        values["rig_ptt_line_hamlib"] = station.rig_ptt_line.upper()

    planned: list[PlannedUserService] = []
    deferrals: list[Deferral] = []
    notes: list[str] = []

    # Which entries this station selects — all of the matching kind, not yet
    # rendered. The rigctld service and its loopback filter are rendered
    # together, so the missing-value and skip decisions are made once over the
    # group, not once per entry (a single deferral line, spec §6b). An empty
    # `when_station` selects for every rig.
    selected = [svc for svc in entries if _matches(svc.when_station, facts)]

    # An entry is skipped only by a non-empty unless_station that matches: an
    # empty one means "nothing excludes this", never "always skip" (review
    # minor). The group is skipped only when every selected entry is.
    def _excluded(svc: UserService) -> bool:
        return bool(svc.unless_station) and _matches(svc.unless_station, facts)

    if selected and all(_excluded(svc) for svc in selected):
        if facts["rig_owner"] == "flrig":
            notes.append(f"  {manifest.name}: skipped — the station's rig is owned by flrig")
        else:
            notes.append(f"  {manifest.name}: skipped — the rig is keyed by VOX, nothing to run")
        return planned, deferrals, notes

    # The needed values are station attributes, checked on the station itself —
    # not the substitution dict, which holds derived names.
    missing_station = [v for v in _needed(res.kind) if getattr(station, v) in (None, "")]
    if missing_station:
        deferrals.append(
            Deferral(
                subject=manifest.name,
                what=f"will not run {first}",
                why="station values not set: " + ", ".join(missing_station),
                remedy="run `hammunition station set "
                + " ".join(f"--{m.replace('_', '-')} …" for m in missing_station)
                + "`",
            )
        )
        return planned, deferrals, notes

    for svc in selected:
        exec_argv = tuple(_substitute(word, values, python, venv_dir) for word in svc.exec)
        filled = _station_sources(svc, res)
        device_path = station.rig_device if svc.binds_to_device else None
        planned.append(_planned(manifest, svc, exec_argv, device_path, filled))
    if res.uncatalogued:
        notes.append(
            f"  {manifest.name}: radio {station.rig} has no manifest; its USB shape, "
            f"ports and known problems are unmeasured here"
        )
    if res.kind == "ptt_only":
        notes.append(
            f"  {manifest.name}: no frequency control — programs see the dummy model's "
            f"frequency; start with the radio off or on a dummy load (opening the port "
            f"may key it)"
        )

    return planned, deferrals, notes


def _station_sources(svc: UserService, res: object) -> tuple[str, ...]:
    """The station values that fed a service, for the plan's 'filled from' line:
    the stored values its exec references, mapped from the derived names back to
    what the operator set."""
    stored: set[str] = {"rig"}
    for key in svc.station_variables:
        if key == "rig_hamlib_model":
            stored.add("rig")
        elif key == "rig_ptt_line_hamlib":
            stored.add("rig_ptt_line")
        elif key.startswith("rig_"):
            stored.add(key)
    return tuple(sorted(stored))
