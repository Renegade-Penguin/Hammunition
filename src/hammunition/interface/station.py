# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``station show`` as data.  D-059."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from hammunition.interface.envelope import Strict, described
from hammunition.station import STATION_FIELDS, Station

__all__ = [
    "StationDocument",
    "StationSetDocument",
    "StationSetRefusal",
    "build_station",
    "render_station",
    "render_station_set",
]


StationValue = str | int | bool | tuple[str, ...] | None


@dataclass(frozen=True)
class StationSetRefusal(Strict):
    """One station-set flag the CLI refused, including its original value."""

    key: str = described("the station setting named by the flag")
    value: str | int | bool | None = described("the value given to the flag")
    reason: str = described("the CLI's reason for refusing this flag")


@dataclass(frozen=True)
class StationSetDocument(Strict):
    """What station set saved, left as-is, or refused for a local front end.

    This contains station values and is for local programs, not for pasting
    into an issue, forum or chat.
    """

    KIND: ClassVar[str] = "station-set"

    saved: dict[str, StationValue] = described("station keys written and their new values")
    unchanged: dict[str, StationValue] = described(
        "given station keys already equal to their stored values"
    )
    refused: tuple[StationSetRefusal, ...] = described("given flags the CLI refused")
    file: str = described("the station configuration file path")


@dataclass(frozen=True)
class StationDocument(Strict):
    """The saved station values, the values themselves included -- map
    regions among them, per D-057.

    For a local front end filling in a form. Not for pasting into an issue,
    a forum or a chat: a callsign resolves to a name and a licence address,
    and a grid square or a map region says where the station is."""

    KIND: ClassVar[str] = "station"

    path: str = described("the station file")
    file_exists: bool = described("whether that file exists yet")
    callsign: str | None = described("the callsign; null when not set")
    grid_square: str | None = described("the Maidenhead locator; null when not set")
    node_alias: str | None = described("the packet node alias; null when not set")
    map_regions: tuple[str, ...] = described(
        "Geofabrik region paths carrying offline map data; empty when none are set"
    )
    map_freshness: str | None = described(
        "how often map data is refreshed: yearly, monthly or latest; "
        "null means the yearly default applies"
    )
    reference_books: tuple[str, ...] = described(
        "Kiwix book ids chosen for kiwix-library (D-066); empty when none are chosen"
    )
    mirror_require_hardware_key: bool = described(
        "require an enrolled hardware signer for Bunker catalogues"
    )
    mirror: str | None = described(
        "the LAN mirror the verified fetch tries before the publisher, the same digest "
        "checked either way (D-070); null when none is set"
    )
    rig: str | None = described(
        "the station's radio: a catalog device id or hamlib:<model>; null when not set (D-073)"
    )
    rig_device: str | None = described(
        "the serial port the rig is reached on — the full by-id path, for the operator's "
        "own screen (the plan, doctor and status elide the serial); null when not set"
    )
    rig_baud: int | None = described(
        "the CAT serial speed; null when not set or for a PTT-only rig"
    )
    rig_ptt_line: str | None = described(
        "for a PTT-only rig: rts, dtr or vox; null for a CAT rig or when not set"
    )
    rig_owner: str | None = described(
        "who holds the port: rigctld (the default when unset) or flrig; null when not set"
    )
    dem_source: str = described(
        "where QMapShack's elevation is drawn from: `copernicus` (the default, also when "
        "unset) or `3dep`, USGS bare earth (D-068, amended 2026-10-01)"
    )
    topo_radius_km: int = described(
        "how far from the grid square's centre US Topo sheets, FSTopo sheets and 3DEP "
        "tiles are selected, in km: 100 when unset, 0 for none (D-068, amended 2026-10-02)"
    )
    topo_regions: tuple[str, ...] = described(
        "the map regions the topographic selection is narrowed to, a subset of "
        "map_regions; empty when it is not narrowed"
    )
    topo_all: bool = described(
        "whether every sheet of every region is selected, as before the bound; false when unset"
    )
    active_areas: tuple[str, ...] | None = described(
        "the areas drawn and registered (D-082): US state codes and map region names; null "
        "when unset, which means everything loaded is active; an empty list means none is"
    )
    secrets_doppler_project: str | None = described(
        "the Doppler project a keyed download's key is read from when its environment "
        "variable is not set (D-081); a name, never a token; null when not set"
    )
    secrets_doppler_config: str | None = described(
        "the Doppler config within that project (D-081); null when not set"
    )


def build_station(path: Path, station: Station) -> StationDocument:
    return StationDocument(
        path=str(path),
        file_exists=path.exists(),
        callsign=station.callsign,
        grid_square=station.grid_square,
        node_alias=station.node_alias,
        map_regions=station.map_regions,
        map_freshness=station.map_freshness,
        reference_books=station.reference_books,
        mirror=station.mirror,
        mirror_require_hardware_key=station.mirror_require_hardware_key,
        rig=station.rig,
        rig_device=station.rig_device,
        rig_baud=station.rig_baud,
        rig_ptt_line=station.rig_ptt_line,
        rig_owner=station.rig_owner,
        dem_source=station.elevation,
        topo_radius_km=station.topo_radius,
        topo_regions=station.topo_regions,
        topo_all=bool(station.topo_all),
        active_areas=station.active_areas,
        secrets_doppler_project=station.secrets_doppler_project,
        secrets_doppler_config=station.secrets_doppler_config,
    )


def render_station(doc: StationDocument) -> list[str]:
    """``station show`` as the terminal shows it."""
    lines = [f"Station configuration: {doc.path}"]
    radius = "none" if doc.topo_radius_km == 0 else f"{doc.topo_radius_km} km"
    if doc.topo_radius_km == 100:
        radius += " (the default)"
    if not doc.file_exists:
        lines.append("  (no file yet)")
    values = {name: getattr(doc, name) for name in sorted(STATION_FIELDS)}
    if (
        not any(values.values())
        and not doc.map_regions
        and doc.map_freshness is None
        and not doc.reference_books
        and doc.mirror is None
        and not doc.mirror_require_hardware_key
        and doc.dem_source == "copernicus"
        and doc.topo_radius_km == 100
        and not doc.topo_regions
        and not doc.topo_all
        and doc.active_areas is None
        and doc.secrets_doppler_project is None
        and doc.secrets_doppler_config is None
    ):
        return [
            *lines,
            "",
            "Nothing set. `hammunition station set --callsign <yours>` starts it off.",
            "Nothing is invented on your behalf: a configuration file needing a value",
            "you have not given is reported as not written, and the package still installs.",
            "",
            f"  {'topo radius':<14} {radius}",
            f"  {'topo regions':<14} (not set)",
            f"  {'topo all':<14} no",
        ]
    lines.append("")
    lines += [f"  {name:<14} {value if value else '(not set)'}" for name, value in values.items()]
    # Region names reveal where the operator lives, so only a count is ever
    # printed here -- `station show` output is the kind of thing that gets
    # pasted into an issue.
    if doc.map_regions:
        lines.append(f"  {'map regions':<14} {len(doc.map_regions)} set")
    else:
        lines.append(f"  {'map regions':<14} (not set)")
    lines.append(f"  {'map freshness':<14} {doc.map_freshness or 'yearly'}")
    lines.append(
        f"  mirror_require_hardware_key {'yes' if doc.mirror_require_hardware_key else 'no'}"
    )
    lines.append(f"  {'mirror':<14} {doc.mirror or '(not set)'}")
    lines.append(f"  {'dem_source':<14} {doc.dem_source}")
    lines.append(f"  {'topo radius':<14} {radius}")
    # A count, like the map regions: the names say where the station is.
    lines.append(
        f"  {'topo regions':<14} {f'{len(doc.topo_regions)} set' if doc.topo_regions else '(not set)'}"
    )
    lines.append(f"  {'topo all':<14} {'yes' if doc.topo_all else 'no'}")
    # A count, like the regions: the areas say where the operator may be sent.
    # Shown only when set, so a station that never chose reads as it always has.
    if doc.active_areas is not None:
        lines.append(f"  {'active areas':<14} {len(doc.active_areas)} set")
    if doc.secrets_doppler_project or doc.secrets_doppler_config:
        lines.append(
            f"  {'doppler':<14} {doc.secrets_doppler_project}/{doc.secrets_doppler_config}"
        )
    # Which books somebody reads is not where they are: named, not counted
    # (D-066). Shown only when chosen, so a station without them reads as
    # it always has.
    if doc.reference_books:
        lines.append(f"  {'reference books':<14} {', '.join(doc.reference_books)}")
    return lines


def render_station_set(doc: StationSetDocument, station: Station, fields: list[str]) -> list[str]:
    """The successful ``station set`` text, rendered from its document."""
    lines = [f"Saved to {doc.file} (mode 0600)."]
    for field in sorted(fields):
        if field == "map_regions":
            lines.append(f"  {field:<14} {len(station.map_regions)} set")
        elif field == "map_freshness":
            lines.append(f"  {field:<14} {station.freshness}")
        elif field == "reference_books":
            lines.append(f"  {field:<14} {', '.join(station.reference_books)}")
        elif field == "mirror":
            lines.append(f"  {field:<14} {station.mirror or '(cleared)'}")
        elif field == "rig":
            for rig_field in ("rig", "rig_device", "rig_baud", "rig_ptt_line", "rig_owner"):
                value = getattr(station, rig_field)
                if value is not None:
                    lines.append(f"  {rig_field:<14} {value}")
        elif field == "dem_source":
            lines.append(f"  {field:<14} {station.elevation}")
        elif field == "topo_radius_km":
            lines.append(f"  {field:<14} {station.topo_radius} km")
        elif field == "topo_regions":
            lines.append(f"  {field:<14} {len(station.topo_regions)} set")
        elif field == "topo_all":
            lines.append(f"  {field:<14} {'yes' if station.topo_all else 'no'}")
        elif field == "active_areas":
            count = None if station.active_areas is None else len(station.active_areas)
            lines.append(
                f"  {field:<14} {'(cleared: everything loaded)' if count is None else f'{count} set'}"
            )
        elif field in ("secrets_doppler_project", "secrets_doppler_config"):
            lines.append(f"  {field:<24} {getattr(station, field) or '(cleared)'}")
        else:
            lines.append(f"  {field:<14} {station.get(field)}")
    return lines
