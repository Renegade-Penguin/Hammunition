# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Repeater sources beyond the operator's own export.  D-074.

Six sources, each read into its own layer beside D-064's
(:mod:`hammunition.repeaters`, which writes and registers every layer):

- **Open Repeater** (CC0), the ``open-repeater`` data unit's file or a copy
  the operator downloaded: :func:`read_open_repeater`.
- **OpenStreetMap**, filtered by ``osmium tags-filter`` out of the region
  extracts the station already has: :func:`filter_extract`. Nothing is
  downloaded.
- **The RSGB ETCC list** and **Brandmeister's device list**, fetched only
  when the operator asks, through D-064's fetch, and parsed from memory:
  :func:`parse_etcc`, :func:`parse_brandmeister`. Brandmeister's hotspots --
  personal ids and simplex devices, somebody's house -- are dropped before
  anything is written.
- **Direwolf's ``-l`` log**: the APRS repeater objects this station heard,
  :func:`read_direwolf_logs`. Never merged into the directory layers.
- **The ACMA's Register of Radiocommunications Licences** (Australia), the
  ``acma-register`` unit's zip, filtered to the bounding boxes of the region
  extracts installed here: :func:`read_acma` (D-074, amended 2026-10-01).

And :func:`cross_merge`, which joins the directory layers into one
all-sources file by the spike's precedence (2026-10-01).

Every field rule below was measured on the publishers' own files by that
spike; the Direwolf columns were measured by decoding synthetic objects
through Direwolf 1.8.1 itself.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .repeaters import (
    _BAD_FREQUENCY,
    _CALLSIGN,
    _NO_CALLSIGN,
    _NO_POSITION,
    ACMA,
    BRANDMEISTER,
    DIREWOLF,
    DIREWOLF_HEAD,
    ETCC,
    ETCC_HEAD,
    HAND,
    HEARHAM,
    MODES,
    OPEN_REPEATER,
    OSM,
    REPEATERBOOK_API,
    REPEATERBOOK_CSV,
    REPEATERBOOK_GPX,
    ParsedInput,
    Repeater,
    RepeaterInputError,
    Skip,
    _clean,
    _hz,
    _in_band,
    _position,
    _Skips,
    _tone,
    _updated,
    csv_records,
    format_mhz,
)

#: A region's (left, right, top, bottom) in degrees, as an extract's header
#: gives it (:func:`hammunition.osm_pbf.header_bbox`).
Box = tuple[float, float, float, float]

__all__ = [
    "ACMA_OUTSIDE",
    "BRANDMEISTER_URL",
    "ETCC_URL",
    "HOTSPOT_ID",
    "HOTSPOT_SIMPLEX",
    "PRECEDENCE",
    "SNAPSHOT_CHECK",
    "SNAPSHOT_UNIT",
    "Snapshot",
    "SnapshotHead",
    "SnapshotRead",
    "acma_date",
    "acma_layer_name",
    "acma_licence",
    "brandmeister_layer_name",
    "brandmeister_licence",
    "cross_merge",
    "cross_merge_origins",
    "direwolf_date",
    "direwolf_layer_name",
    "direwolf_licence",
    "etcc_layer_name",
    "etcc_licence",
    "extracts_date",
    "filter_extract",
    "installed_extracts",
    "open_repeater_date",
    "open_repeater_layer_name",
    "open_repeater_licence",
    "osm_frequency",
    "osm_layer_name",
    "osm_licence",
    "osm_offset",
    "parse_brandmeister",
    "parse_etcc",
    "read_acma",
    "read_direwolf_logs",
    "read_open_repeater",
    "read_osm_xml",
    "read_snapshot",
    "snapshots",
]

#: The ETCC's whole list as CSV, offered openly on ukrepeater.net's CSV page;
#: 62 kB and 803 rows on 2026-10-01, byte-identical over three fetches.
ETCC_URL = "https://ukrepeater.net/csvcreate_all.php"
ETCC_LIMIT = 8 * 1024 * 1024
#: Brandmeister's device list, no key; about 9.5 MB and 31,993 devices on
#: 2026-10-01, a cached snapshot.
BRANDMEISTER_URL = "https://api.brandmeister.network/v2/device"
BRANDMEISTER_LIMIT = 64 * 1024 * 1024

#: The unit name a Bunker's mirror files the on-request snapshots under
#: (D-070's ``<mirror>/<unit>/<name>``); not a catalog unit, because nothing
#: is installed from it: the ``fetch-*`` commands read it (D-078).
SNAPSHOT_UNIT = "repeater-snapshots"
#: ``hammunition artifacts``' name for the check (D-078): no digest exists and
#: nothing but size and date is checked.
SNAPSHOT_CHECK = "unverified-fetch"

HOTSPOT_ID = "a hotspot or personal id (not 6 digits), dropped as a personal location"
HOTSPOT_SIMPLEX = "transmit equals receive (a simplex hotspot), dropped as a personal location"

#: The all-sources file's order, best first (the spike's precedence): the
#: operator's own export or list, the regulator (the ACMA), the coordinator
#: (the ETCC), CC0 community data, hearham, Brandmeister, OpenStreetMap; the
#: operator's own RepeaterBook API fetch (D-081) ranks after the regulator.
#: The ACMA and the ETCC cover different countries, so their order decides
#: nothing today; the regulator is put first by the spike's word.
PRECEDENCE = (
    REPEATERBOOK_GPX,
    REPEATERBOOK_CSV,
    HAND,
    ACMA,
    REPEATERBOOK_API,
    ETCC,
    OPEN_REPEATER,
    HEARHAM,
    BRANDMEISTER,
    OSM,
)
#: Two directories place one machine a few hundred metres apart; the exact
#: 0.01° key matched 2 of 7 Vermont Brandmeister repeaters to hearham where
#: this matched 5 (the spike).
NEAR_DEGREES = 0.02
#: How far a same-callsign, same-frequency row may lie and still be the same
#: machine. Unbounded, the callsign test would join real separate sites:
#: D-064 measured 1,050 callsign-and-frequency keys in hearham alone that
#: name more than one site. 0.25° (about 25 km) still catches a directory
#: that places a repeater at its town rather than its hill (ruling, D-074).
SAME_CALL_DEGREES = 0.25


# --- licences and names -------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """One list fetched on request: where the publisher serves it, what it is
    called on a mirror, the most that is read, and its licence position."""

    name: str
    url: str
    limit: int
    position: str


def snapshots() -> tuple[Snapshot, ...]:
    """The three on-request fetches (D-064, D-074), read at call time so a
    test can point the URLs at loopback. Each is unverified: the publisher
    states no licence and no digest, and the list changes under the URL."""
    from . import repeaters

    return (
        Snapshot(
            "etcc.csv",
            ETCC_URL,
            ETCC_LIMIT,
            "RSGB ETCC (ukrepeater.net): no licence stated for a list its CSV page offers "
            "openly; D-033's position, fetched on request, never redistributed by the project; "
            "an operator's own Bunker may hold it for their LAN",
        ),
        Snapshot(
            "brandmeister.json",
            BRANDMEISTER_URL,
            BRANDMEISTER_LIMIT,
            "BrandMeister: no terms published for the device API; D-033's position, fetched on "
            "request, never redistributed by the project; hotspots are personal locations and "
            "are dropped on import; an operator's own Bunker may hold it for their LAN",
        ),
        Snapshot(
            "hearham.json",
            repeaters.HEARHAM_URL,
            repeaters.HEARHAM_LIMIT,
            "hearham.com: no data licence stated; D-033's position, fetched on request, never "
            "redistributed by the project; an operator's own Bunker may hold it for their LAN",
        ),
    )


class SnapshotHead:
    """A ``HEAD`` to one of :func:`snapshots`' URLs, for ``hammunition
    artifacts``: the size the server states, or ``None`` when it states none
    or does not answer a ``HEAD`` with a body length. HTTPS handlers only, no
    redirect followed, nothing but a snapshot's own URL asked.  D-078."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout
        opener = urllib.request.OpenerDirector()
        for handler in (
            urllib.request.HTTPSHandler(),
            urllib.request.HTTPErrorProcessor(),
            urllib.request.HTTPDefaultErrorHandler(),
        ):
            opener.add_handler(handler)
        self._opener = opener

    def size(self, url: str) -> int | None:
        if url not in {x.url for x in snapshots()}:
            raise ValueError(f"refusing {url!r}: not an on-request repeater list")
        request = urllib.request.Request(url, headers={"User-Agent": "hammunition"})
        request.method = "HEAD"
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError:
            return None  # an answer, but not a length: listed, size unknown
        if response is None:  # pragma: no cover - no handler claimed the scheme
            raise OSError(f"no handler would ask {url!r}")
        with response:
            length = response.headers.get("Content-Length") or ""
            return int(length) if length.isdecimal() else None


@dataclass(frozen=True)
class SnapshotRead:
    """A snapshot read and parsed: from the LAN mirror or the publisher."""

    parsed: ParsedInput
    sha256: str
    when: datetime
    source: str  # "mirror" or "publisher"
    where: str  # the URL the bytes came from
    mirror_failure: str | None


def read_snapshot(
    snapshot: Snapshot,
    parse: Callable[[bytes, str], ParsedInput],
    *,
    mirror: str | None,
    fetch: Callable[..., tuple[bytes, str, datetime]] | None = None,
    offline: bool = False,
) -> SnapshotRead:
    """*snapshot*, from ``<mirror>/<SNAPSHOT_UNIT>/<name>`` first when a
    *mirror* is given (D-070's shape), else or on any failure there -- it
    did not answer, was too large, or what it sent does not parse as this
    list -- from the publisher.  D-078.

    A mirror is trusted for speed, never for content: what it sent is parsed
    exactly as the publisher's would be, and is still marked unverified, its
    sha256 being only what was observed. Raises the publisher path's own
    :class:`RepeaterFetchError` / :class:`RepeaterInputError`.

    *offline* never asks the publisher: a snapshot the mirror cannot give is
    refused, naming it, and not fetched from the publisher instead. Reading a
    snapshot from a ``file://`` export or the catalogue is #394; until then a
    file export under *offline* is refused too."""
    from . import repeaters
    from .fetch import MirrorPath, mirror_url

    get = fetch or repeaters.fetch_list
    failure: str | None = None
    if mirror and urllib.parse.urlsplit(mirror).scheme == "file":
        # A file export is read by the Bunker transport, never by this plain
        # HTTP fetch; asking it would raise on a scheme with no handler.
        failure = "a file:// mirror is not asked for repeater snapshots"
    elif mirror:
        where = mirror_url(mirror, MirrorPath(SNAPSHOT_UNIT, snapshot.name))
        try:
            body, digest, when = get(where, limit=snapshot.limit)
            try:
                parsed = parse(body, snapshot.url)
            finally:
                del body
            return SnapshotRead(parsed, digest, when, "mirror", where, None)
        except (repeaters.RepeaterFetchError, repeaters.RepeaterInputError) as exc:
            failure = str(exc)
    if offline:
        raise repeaters.RepeaterFetchError(
            f"offline: the {snapshot.name} repeater snapshot has no Bunker route "
            f"({failure or 'no mirror is set'}; #394), and its publisher {snapshot.url} "
            f"is not asked under --offline"
        )
    try:
        body, digest, when = get(snapshot.url, limit=snapshot.limit)
        try:
            parsed = parse(body, snapshot.url)
        finally:
            del body
    except (repeaters.RepeaterFetchError, repeaters.RepeaterInputError) as exc:
        if failure is None:
            raise
        # Both sources failed: say so, the mirror's reason too.
        raise type(exc)(f"{exc} (the LAN mirror had failed first: {failure})") from None
    return SnapshotRead(parsed, digest, when, "publisher", snapshot.url, failure)


def open_repeater_licence() -> str:
    return (
        "Open Repeater, CC0 1.0 (openrepeater.org): dedicated to the public domain; "
        "credited here as a courtesy."
    )


def osm_licence() -> str:
    return (
        "© OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright). Filtered on "
        "this machine from the map extracts it already has; if you share this layer, the "
        "ODbL's terms for a derived database apply to it."
    )


def etcc_licence(when: str, sha256: str) -> str:
    return (
        "ukrepeater.net (the RSGB's ETCC) states no licence for this list, which its CSV "
        "page offers openly. Carried under D-033: fetched on your request, never "
        "redistributed. Positions are at Maidenhead-locator precision: a four-character "
        "locator puts a repeater at its square's centre, tens of kilometres from the site. "
        f"{when}, sha256 {sha256}, not verifiable."
    )


def brandmeister_licence(when: str, sha256: str) -> str:
    return (
        "BrandMeister publishes no terms for its device API. Carried under D-033: fetched "
        "on your request, never redistributed. Hotspots (7- and 9-digit ids, and devices "
        "whose transmit and receive frequencies are equal) are personal locations and "
        f"were dropped before anything was written. {when}, sha256 {sha256}, not verifiable."
    )


def direwolf_licence() -> str:
    return (
        "Received by this station: APRS repeater objects from Direwolf's log. Nothing was "
        "fetched. Kept as its own layer and never merged into the directories."
    )


def acma_licence(day: date) -> str:
    """The attribution the ACMA's licence requires (clause 9), and what was
    done to make this derivative."""
    return (
        "Based on Australian Communications and Media Authority information. ACMA "
        "Register of Radiocommunications Licences, used under the ACMA's Licence to use "
        "the Register (derivatives permitted, attribution required). Filtered on this "
        "machine to the bounding boxes of your installed map regions: granted amateur "
        "repeater licences, their transmitters and sites. No licensee's name or address "
        f"is read or written. Register of {day.isoformat()}, fetched unverified (the "
        "ACMA publishes no checksum)."
    )


def acma_layer_name(day: date) -> str:
    return f"Repeaters (ACMA, {day.isoformat()})"


def open_repeater_layer_name(day: date) -> str:
    return f"Repeaters (Open Repeater {day.isoformat()}, CC0)"


def osm_layer_name(day: date) -> str:
    return f"Repeaters (OpenStreetMap, ODbL, {day.isoformat()})"


def etcc_layer_name(day: date) -> str:
    return f"Repeaters (RSGB ETCC {day.isoformat()}, unverified)"


def brandmeister_layer_name(day: date) -> str:
    return f"DMR repeaters (Brandmeister {day.isoformat()}, unverified)"


def direwolf_layer_name(day: date) -> str:
    return f"Repeaters heard off the air (APRS objects, {day.isoformat()})"


# --- shared --------------------------------------------------------------------------


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _offset_mhz_or_khz(value: object) -> int | None:
    """An offset in MHz when its magnitude is under 50, else in kHz: Open
    Repeater's file holds both (-0.6 177 times, -600 85 times)."""
    number = _number(value)
    if number is None:
        return None
    return round(number * 1_000_000) if abs(number) < 50 else round(number * 1_000)


def _tone_number(value: object) -> str:
    number = _number(value)
    return "" if number is None or number <= 0 else f"{number:.1f}"


def _parsed(
    path: Path, fmt: str, read: int, rows: list[Repeater], skips: _Skips, raw: bytes, mtime: float
) -> ParsedInput:
    return ParsedInput(
        path=path,
        format=fmt,
        read=read,
        rows=tuple(rows),
        skipped=skips.result(),
        sha256=hashlib.sha256(raw).hexdigest(),
        mtime=mtime,
    )


def _read_file(path: Path) -> tuple[bytes, float]:
    try:
        return path.read_bytes(), path.stat().st_mtime
    except OSError as exc:
        raise RepeaterInputError(f"{path}: cannot read it: {exc.strerror or exc}") from None


# --- Open Repeater ----------------------------------------------------------------------


def _open_repeater_list(path: Path, raw: bytes) -> list[Any]:
    try:
        data: Any = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if (
        not isinstance(data, dict)
        or data.get("source") != "Open Repeater"
        or not isinstance(data.get("repeaters"), list)
    ):
        raise RepeaterInputError(
            f"{path}: not Open Repeater's JSON download (an object with source "
            f"'Open Repeater' and a repeaters list)"
        )
    items: list[Any] = data["repeaters"]
    return items


def read_open_repeater(path: Path) -> ParsedInput:
    """Open Repeater's JSON download, as the data unit installs it."""
    raw, mtime = _read_file(path)
    items = _open_repeater_list(path, raw)
    rows: list[Repeater] = []
    skips = _Skips()
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            skips.add("not a repeater entry", number)
            continue
        where = _position(item.get("lat"), item.get("lng"))
        if where is None:
            skips.add(_NO_POSITION, number)
            continue
        call = _clean(item.get("callsign")).upper()
        if not call:
            skips.add(_NO_CALLSIGN, number)
            continue
        frequency = _number(item.get("frequency"))
        hz = _hz(frequency) if frequency is not None else None
        if hz is None:
            skips.add(_BAD_FREQUENCY, number)
            continue
        dcs = _number(item.get("dcs"))
        tone = _tone_number(item.get("ctcss")) or (f"DCS {int(dcs):03d}" if dcs else "")
        rows.append(
            Repeater(
                callsign=call,
                output_hz=hz,
                lat=where[0],
                lon=where[1],
                source=OPEN_REPEATER,
                offset_hz=_offset_mhz_or_khz(item.get("offset")),
                tone=tone,
                mode=_clean(item.get("mode")),
                place=", ".join(x for x in (_clean(item.get("city")),) if x),
                status=_clean(item.get("status")),
                updated=_clean(item.get("last_verified")),
            )
        )
    return _parsed(path, OPEN_REPEATER, len(items), rows, skips, raw, mtime)


def open_repeater_date(path: Path) -> date:
    """The newest ``last_verified`` in the file: the data's own date (D-031).
    The file's modification date when no entry carries one."""
    raw, mtime = _read_file(path)
    days: list[date] = []
    for item in _open_repeater_list(path, raw):
        if isinstance(item, dict):
            when = _updated(_clean(item.get("last_verified")))
            if when is not None:
                days.append(when.date())
    return max(days) if days else date.fromtimestamp(mtime)


# --- the ACMA register ------------------------------------------------------------------

#: Why a register row inside Australia but outside the operator's regions is
#: left out. Counted, never numbered: which rows fall outside says where the
#: regions are.
ACMA_OUTSIDE = "outside the bounding boxes of your installed map regions"
_ACMA_NOT_GRANTED = "the licence is not granted (expired, cancelled or refused)"
_ACMA_NO_SITE = "no site in the register"
#: The ``SS_ID`` of "Amateur Repeater" in ``licence_subservice.csv``.
_ACMA_REPEATER = "602"
_ACMA_GRANTED = "1"
#: The emission class (the designator's three characters after its
#: bandwidth) in plain words, for the classes the 2026-10-01 register's
#: repeater transmitters use. Any other is shown as its designator alone.
_EMISSION_WORDS = {
    "F3E": "FM",
    "F1D": "FM data",
    "F2D": "FM data",
    "F3D": "FM data",
    "F9W": "FM and digital",
    "F1W": "digital",
    "F7W": "digital",
    "W7W": "digital",
    "FXE": "digital voice",
    "J3E": "SSB",
    "A1A": "CW",
    "F1A": "CW",
    "C3F": "ATV",
    "F3F": "ATV",
    "F2F": "ATV",
    "V7W": "digital ATV",
}


def _acma_mode(emission: str) -> str:
    designator = emission.strip().upper()
    if not designator:
        return ""
    words = _EMISSION_WORDS.get(designator[4:7]) if len(designator) >= 7 else None
    return f"{words} ({designator})" if words else designator


def _in_box(lat: float, lon: float, box: Box) -> bool:
    left, right, top, bottom = box
    if not bottom <= lat <= top:
        return False
    if left <= right:
        return left <= lon <= right
    return lon >= left or lon <= right  # a box across the antimeridian


def _acma_rows(archive: Any, name: str) -> Iterable[tuple[int, dict[str, str]]]:
    """*name*'s rows as dicts keyed by upper-case column, with line numbers."""
    with archive.open(name) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text)
        header = [h.strip().upper() for h in next(reader, [])]
        for record in reader:
            yield reader.line_num, dict(zip(header, record, strict=False))


def read_acma(path: Path, boxes: Sequence[Box]) -> ParsedInput:
    """The amateur repeaters in the ACMA register at *path* (the
    ``acma-register`` unit's zip) that lie in any of *boxes*.

    A row is a transmitter (``DEVICE_TYPE`` T) on an "Amateur Repeater"
    licence (``SS_ID`` 602); its input is the receiver of the same licence
    and ``EFL_SYSTEM``, and its position its site's. Kept only on a granted
    licence, with a site that has a position, a frequency from 1 to
    10,000 MHz, inside a box. ``client.csv`` is never opened. ``read``
    counts the transmitters; the skips are numbered by ``device_details.csv``
    line, except :data:`ACMA_OUTSIDE`, which is only counted."""
    import zipfile

    from .acma import AcmaError, check_register

    try:
        check_register(path, crc=False)
    except AcmaError as exc:
        raise RepeaterInputError(str(exc)) from None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        mtime = path.stat().st_mtime
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RepeaterInputError(f"{path}: cannot read it: {exc}") from None
    rows: list[Repeater] = []
    skips = _Skips()
    outside = 0
    read = 0
    try:
        with archive:
            status = {
                r.get("LICENCE_NO", ""): r.get("STATUS", "").strip()
                for _, r in _acma_rows(archive, "licence.csv")
                if r.get("SS_ID", "").strip() == _ACMA_REPEATER
            }
            transmitters: list[tuple[int, dict[str, str]]] = []
            receivers: dict[tuple[str, str], list[str]] = {}
            for line, r in _acma_rows(archive, "device_details.csv"):
                licence = r.get("LICENCE_NO", "")
                if licence not in status:
                    continue
                kind = r.get("DEVICE_TYPE", "").strip().upper()
                if kind == "T":
                    transmitters.append((line, r))
                elif kind == "R":
                    key = (licence, r.get("EFL_SYSTEM", "").strip())
                    receivers.setdefault(key, []).append(r.get("FREQUENCY", ""))
            wanted = {r.get("SITE_ID", "").strip() for _, r in transmitters} - {""}
            sites = {
                r.get("SITE_ID", "").strip(): r
                for _, r in _acma_rows(archive, "site.csv")
                if r.get("SITE_ID", "").strip() in wanted
            }
    except (
        zipfile.BadZipFile,
        OSError,
        EOFError,
        csv.Error,
        NotImplementedError,
        RuntimeError,
    ) as exc:
        raise RepeaterInputError(f"{path}: the register could not be read: {exc}") from None
    for line, r in transmitters:
        read += 1
        licence = r.get("LICENCE_NO", "")
        if status.get(licence) != _ACMA_GRANTED:
            skips.add(_ACMA_NOT_GRANTED, line)
            continue
        site = sites.get(r.get("SITE_ID", "").strip())
        if site is None:
            skips.add(_ACMA_NO_SITE, line)
            continue
        where = _position(site.get("LATITUDE"), site.get("LONGITUDE"))
        if where is None:
            skips.add(_NO_POSITION, line)
            continue
        call = _clean(r.get("CALL_SIGN")).upper()
        if not call:
            skips.add(_NO_CALLSIGN, line)
            continue
        hertz = _number(r.get("FREQUENCY"))
        hz = _hz(hertz / 1e6) if hertz is not None else None
        if hz is None:
            skips.add(_BAD_FREQUENCY, line)
            continue
        if not any(_in_box(where[0], where[1], box) for box in boxes):
            outside += 1
            continue
        inputs = receivers.get((licence, r.get("EFL_SYSTEM", "").strip()), [])
        entry = _number(inputs[0]) if len(inputs) == 1 else None
        precision = _clean(site.get("SITE_PRECISION"))
        notes = [f"ACMA licence {licence}"]
        if precision and precision.lower() != "unknown":
            notes.append(f"site position {precision[0].lower()}{precision[1:]}")
        else:
            notes.append("site position precision unknown")
        place = ", ".join(x for x in (_clean(site.get("NAME")), _clean(site.get("STATE"))) if x)
        rows.append(
            Repeater(
                callsign=call,
                output_hz=hz,
                lat=where[0],
                lon=where[1],
                source=ACMA,
                offset_hz=None if entry is None else round(entry) - hz,
                mode=_acma_mode(r.get("EMISSION", "")),
                place=place,
                notes="; ".join(notes),
            )
        )
    skipped = list(skips.result())
    if outside:
        skipped.append(Skip(ACMA_OUTSIDE, outside, ()))
    return ParsedInput(
        path=path,
        format=ACMA,
        read=read,
        rows=tuple(rows),
        skipped=tuple(skipped),
        sha256=digest.hexdigest(),
        mtime=mtime,
    )


def acma_date(path: Path) -> date:
    """The register's own date: its ``licence.csv`` member's timestamp (D-031)."""
    import zipfile

    from .acma import register_day

    try:
        with zipfile.ZipFile(path) as archive:
            return register_day(archive)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise RepeaterInputError(f"{path}: the register's date cannot be read: {exc}") from None


# --- ETCC ----------------------------------------------------------------------------


def parse_etcc(raw: bytes, url: str) -> ParsedInput:
    """The ETCC's CSV, from memory. ``txMHz`` is the repeater's output
    (UK 2 m outputs sit 600 kHz above their inputs in the measured file)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except (StopIteration, csv.Error):
        header = []
    if tuple(header[:5]) != ETCC_HEAD or not {"lat", "lon"} <= set(header):
        raise RepeaterInputError(f"{url}: not the ETCC's repeater CSV (header {header[:6]})")
    rows: list[Repeater] = []
    skips = _Skips()
    read = 0
    for record in csv_records(reader):
        if not any(cell.strip() for cell in record):
            continue
        read += 1
        line = reader.line_num
        cells = {n: v.strip() for n, v in zip(header, record, strict=False)}
        where = _position(cells.get("lat"), cells.get("lon"))
        if where is None:
            skips.add(_NO_POSITION, line)
            continue
        call = _clean(cells.get("call")).upper()
        if not call:
            skips.add(_NO_CALLSIGN, line)
            continue
        hz = _hz(cells.get("txmhz", ""))
        if hz is None:
            skips.add(_BAD_FREQUENCY, line)
            continue
        entry = _hz(cells.get("rxmhz", ""))
        modes = [
            name
            for column, name in (
                ("analog", "FM"),
                ("dmr", "DMR"),
                ("dstar", "D-STAR"),
                ("fusion", "C4FM"),
            )
            if cells.get(column, "").upper() == "Y"
        ]
        locator = cells.get("qthr", "")
        notes = []
        if cells.get("chan"):
            notes.append(f"channel {cells['chan']}")
        if locator:
            notes.append(
                f"position from locator {locator}"
                + (" (the square's centre, not the site)" if len(locator) <= 4 else "")
            )
        rows.append(
            Repeater(
                callsign=call,
                output_hz=hz,
                lat=where[0],
                lon=where[1],
                source=ETCC,
                offset_hz=None if entry is None else entry - hz,
                tone=_tone(cells.get("ctcss", "")),
                mode=", ".join(modes),
                place=_clean(cells.get("where")).title(),
                notes="; ".join(notes),
            )
        )
    return _parsed(Path(url), ETCC, read, rows, skips, raw, 0.0)


# --- Brandmeister ------------------------------------------------------------------------


def parse_brandmeister(raw: bytes, url: str) -> ParsedInput:
    """Brandmeister's device list, from memory, repeaters only.

    A repeater has a 6-digit id and different transmit and receive
    frequencies; 7- and 9-digit ids are personal ids, and a device whose
    frequencies are equal is a simplex hotspot. Both are somebody's house:
    each is dropped first, counted, and nothing else of it is kept."""
    try:
        data: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, list) or not any(
        isinstance(d, dict) and {"id", "tx", "rx"} <= d.keys() for d in data
    ):
        raise RepeaterInputError(f"{url}: not Brandmeister's device list")
    rows: list[Repeater] = []
    skips = _Skips()
    for number, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            skips.add("not a device entry", number)
            continue
        ident = item.get("id")
        if not isinstance(ident, int) or isinstance(ident, bool) or not 100_000 <= ident <= 999_999:
            skips.add(HOTSPOT_ID, number)
            continue
        tx, rx = _clean(item.get("tx")), _clean(item.get("rx"))
        if tx == rx or _number(tx) == _number(rx):
            skips.add(HOTSPOT_SIMPLEX, number)
            continue
        where = _position(item.get("lat"), item.get("lng"))
        if where is None:
            skips.add(_NO_POSITION, number)
            continue
        call = _clean(item.get("callsign")).upper()
        if not call:
            skips.add(_NO_CALLSIGN, number)
            continue
        hz = _hz(tx)
        if hz is None:
            skips.add(_BAD_FREQUENCY, number)
            continue
        entry = _hz(rx)
        notes = [f"Brandmeister id {ident}"]
        colour = item.get("colorcode")
        if isinstance(colour, int) and not isinstance(colour, bool):
            notes.append(f"colour code {colour}")
        digital = {"dmr_network": "Brandmeister", "dmr_id": str(ident)}
        if isinstance(colour, int) and not isinstance(colour, bool):
            digital["dmr_color_code"] = str(colour)
        master = item.get("lastKnownMaster")
        if isinstance(master, int) and not isinstance(master, bool):
            notes.append(f"master {master}")
        rows.append(
            Repeater(
                callsign=call,
                output_hz=hz,
                lat=where[0],
                lon=where[1],
                source=BRANDMEISTER,
                offset_hz=None if entry is None else entry - hz,
                mode="DMR",
                digital=digital,
                place=_clean(item.get("city")),
                notes="; ".join(notes),
                updated=_clean(item.get("last_seen")),
            )
        )
    return _parsed(Path(url), BRANDMEISTER, len(data), rows, skips, raw, 0.0)


# --- Direwolf ---------------------------------------------------------------------------

_SSID = re.compile(r"-[0-9A-Z]{1,2}$")


def _object_callsign(name: str) -> str:
    """The callsign an object is named by (``N0TST-R``, ircDDB's ``N0TST  B``
    gives ``N0TST``), or empty for an object named by its frequency."""
    first = name.split()[0] if name.split() else ""
    bare = _SSID.sub("", first)
    return first if _CALLSIGN.fullmatch(bare) else ""


def _direwolf_offset(text: str) -> int | None:
    """Direwolf writes the offset in signed kHz: ``-600``, ``+5000``."""
    number = _number(text)
    return None if number is None else round(number * 1_000)


def read_direwolf_logs(paths: Sequence[Path]) -> ParsedInput:
    """Direwolf's ``-l`` daily logs (or one ``-L`` file), read together.

    Kept: a row whose ``dti`` is ``;`` (an APRS object) with a frequency in a
    repeater band; Direwolf has already decoded the frequency (MHz), the
    offset (signed kHz) and the tone (Hz) from the object. An object heard
    again is another row; D-064's merge keeps the newest hearing."""
    rows: list[Repeater] = []
    skips = _Skips()
    read = 0
    digest = hashlib.sha256()
    newest = 0.0
    for path in paths:
        raw, mtime = _read_file(path)
        digest.update(raw)
        newest = max(newest, mtime)
        reader = csv.reader(io.StringIO(raw.decode("utf-8", errors="replace")))
        try:
            header = [h.strip().lower() for h in next(reader)]
        except (StopIteration, csv.Error):
            header = []
        if tuple(header[:3]) != DIREWOLF_HEAD or not {"dti", "frequency"} <= set(header):
            raise RepeaterInputError(
                f"{path}: not a Direwolf log (Direwolf's -l or -L CSV, whose header starts "
                f"{','.join(DIREWOLF_HEAD)})"
            )
        for record in csv_records(reader):
            if not any(cell.strip() for cell in record):
                continue
            read += 1
            line = reader.line_num
            cells = {n: v.strip() for n, v in zip(header, record, strict=False)}
            if cells.get("dti") != ";":
                skips.add("not an APRS object", line)
                continue
            frequency = cells.get("frequency", "")
            if not frequency or _number(frequency) is None or not _in_band(frequency):
                skips.add("frequency outside the repeater bands", line)
                continue
            where = _position(cells.get("latitude"), cells.get("longitude"))
            if where is None:
                skips.add(_NO_POSITION, line)
                continue
            hz = _hz(frequency)
            if hz is None:  # pragma: no cover - a band frequency is in range
                skips.add(_BAD_FREQUENCY, line)
                continue
            name = _clean(cells.get("name"))
            call = _object_callsign(name.upper())
            heard = cells.get("isotime", "")
            notes = [
                x
                for x in (
                    _clean(cells.get("comment")),
                    f"sent by {cells.get('source', '')}" if cells.get("source") else "",
                    f"heard {heard}" if heard else "",
                )
                if x
            ]
            rows.append(
                Repeater(
                    callsign=call,
                    output_hz=hz,
                    lat=where[0],
                    lon=where[1],
                    source=DIREWOLF,
                    offset_hz=_direwolf_offset(cells.get("offset", "")),
                    tone=_tone_number(cells.get("tone")),
                    notes="; ".join(notes),
                    updated=heard,
                    label="" if call else name,
                )
            )
    first = paths[0] if len(paths) == 1 else Path(os.path.commonpath([str(p) for p in paths]))
    return ParsedInput(
        path=first,
        format=DIREWOLF,
        read=read,
        rows=tuple(rows),
        skipped=skips.result(),
        sha256=digest.hexdigest(),
        mtime=newest,
    )


def direwolf_date(rows: Iterable[Repeater]) -> date | None:
    """The newest hearing among *rows*, or None."""
    days = [w.date() for w in (_updated(r.updated) for r in rows) if w is not None]
    return max(days) if days else None


# --- OpenStreetMap ----------------------------------------------------------------------

_OSM_PREFIXES = ("communication:amateur_radio", "communication:ham_radio")
_UNIT = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*(mhz|khz|hz)?\s*$", re.IGNORECASE)
_SCALES = {"mhz": 1.0, "khz": 1e-3, "hz": 1e-6}
#: A bare number is tried in these units, in this order: MHz, kHz, Hz, and
#: Hz written ten times over (146.61 MHz as ``1466100000``, the spike's one
#: Vermont object). No number lands in a band under two of them.
_BARE = (1.0, 1e-3, 1e-6, 1e-7)


def _first_value(text: str) -> str:
    return text.split(";", 1)[0].strip()


def osm_frequency(text: str) -> int | None:
    """An OSM ``frequency_out`` (or ``frequency_in``) in Hz, or None."""
    match = _UNIT.match(_first_value(text))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "").lower()
    for scale in (_SCALES[unit],) if unit else _BARE:
        mhz = value * scale
        if _in_band(f"{mhz:.6f}"):
            return round(mhz * 1_000_000)
    return None


def osm_offset(shift: str, out_hz: int, freq_in: str) -> int | None:
    """``…:repeater:shift`` with its sign (a bare magnitude under 50 is MHz,
    under 50,000 kHz, else Hz), else the input frequency's difference; None
    when neither says which way (an unsigned shift says how far only)."""
    first = _first_value(shift)
    match = _UNIT.match(first)
    if match and first.lstrip()[:1] in "+-":
        value = float(match.group(1).replace(",", "."))
        unit = (match.group(2) or "").lower()
        if unit:
            return round(value * _SCALES[unit] * 1_000_000)
        size = abs(value)
        scale = 1_000_000 if size < 50 else 1_000 if size < 50_000 else 1
        return round(value * scale)
    entry = osm_frequency(freq_in) if freq_in else None
    return None if entry is None else entry - out_hz


def _scheme(tags: dict[str, str]) -> dict[str, str]:
    """Tags under either prefix, keyed as ``communication:amateur_radio…``."""
    out: dict[str, str] = {}
    for key, value in tags.items():
        for prefix in _OSM_PREFIXES:
            if key == prefix or key.startswith(prefix + ":"):
                out.setdefault(_OSM_PREFIXES[0] + key[len(prefix) :], value)
    return out


def _is_repeater(scheme: dict[str, str]) -> bool:
    base = _OSM_PREFIXES[0]
    flag = scheme.get(f"{base}:repeater", "").strip().lower()
    return (
        (bool(flag) and flag != "no")
        or scheme.get(base, "").strip().lower() == "repeater"
        or f"{base}:repeater:frequency_out" in scheme
    )


def read_osm_xml(text: str, path: Path) -> ParsedInput:
    """OSM XML as ``osmium tags-filter -f osm`` writes it: matched objects and
    the nodes their ways reference. A node is placed where it is, a way at
    the mean of its nodes; a relation is counted, not placed."""
    upper = text.upper()  # D-064's rule: no GPX or OSM file needs either
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise RepeaterInputError(f"{path}: XML with a DOCTYPE or ENTITY declaration is refused")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RepeaterInputError(f"{path}: osmium's output does not parse: {exc}") from None
    nodes: dict[str, tuple[float, float]] = {}
    for node in root.iter("node"):
        where = _position(node.get("lat"), node.get("lon"))
        if where is not None:
            nodes[node.get("id", "")] = where
    rows: list[Repeater] = []
    skips = _Skips()
    read = 0
    base = _OSM_PREFIXES[0]
    for element in root:
        if element.tag not in ("node", "way", "relation"):
            continue
        tags = {t.get("k", ""): t.get("v", "") for t in element.iter("tag")}
        scheme = _scheme(tags)
        if not scheme:
            continue  # a node a matched way references
        read += 1
        # Skips are numbered in reading order, never by OSM id: an id is a
        # place, and the skip list reaches the pasteable document.
        number = read
        osm_id = element.get("id", "")
        if not _is_repeater(scheme):
            skips.add("not marked as a repeater", number)
            continue
        if element.tag == "relation":
            skips.add("a relation, which has no single position", number)
            continue
        if element.tag == "node":
            where = nodes.get(element.get("id", ""))
        else:
            refs = list(dict.fromkeys(nd.get("ref", "") for nd in element.iter("nd")))
            points = [nodes[r] for r in refs if r in nodes]
            where = (
                (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
                if points
                else None
            )
        if where is None:
            skips.add(_NO_POSITION, number)
            continue
        hz = osm_frequency(scheme.get(f"{base}:repeater:frequency_out", ""))
        if hz is None:
            skips.add(_BAD_FREQUENCY, number)
            continue
        call = _clean(scheme.get(f"{base}:callsign")).upper()
        named = _clean(scheme.get(f"{base}:repeater")).upper()
        if not call and _CALLSIGN.fullmatch(_SSID.sub("", named)):
            call = named
        shift = scheme.get(f"{base}:repeater:shift", "")
        offset = osm_offset(shift, hz, scheme.get(f"{base}:repeater:frequency_in", ""))
        notes = [f"OpenStreetMap {element.tag} {osm_id}"]
        if shift and offset is None:
            notes.append(f"shift {shift.strip()} (direction not given)")
        rows.append(
            Repeater(
                callsign=call,
                output_hz=hz,
                lat=where[0],
                lon=where[1],
                source=OSM,
                offset_hz=offset,
                tone=_tone(
                    re.sub(r"\s*hz\s*$", "", scheme.get(f"{base}:repeater:ctcss", ""), flags=re.I)
                ),
                mode=_clean(scheme.get(f"{base}:repeater:modulation")),
                place=_clean(tags.get("name")),
                notes="; ".join(notes),
                label="" if call else format_mhz(hz),
            )
        )
    return ParsedInput(
        path=path,
        format=OSM,
        read=read,
        rows=tuple(rows),
        skipped=skips.result(),
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        mtime=0.0,
    )


Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def filter_extract(
    pbf: Path, scratch: Path, *, run: Runner | None = None, label: str = "the extract"
) -> ParsedInput:
    """The repeaters in one region extract: ``osmium tags-filter`` into
    *scratch*, as the operator, then :func:`read_osm_xml`.

    Every message names the extract by *label* (``region extract 2 of 3``),
    never by its file: a region's name says where the operator is (D-057),
    and these messages are the kind that get pasted (final review)."""
    out = scratch / "extract.repeaters.osm"
    argv = [
        "osmium",
        "tags-filter",
        str(pbf),
        f"nwr/{_OSM_PREFIXES[0]}*",
        f"nwr/{_OSM_PREFIXES[1]}*",
        "-f",
        "osm",
        "-o",
        str(out),
        "--overwrite",
    ]
    try:
        result = (run or _run)(argv)
    except FileNotFoundError:
        raise RepeaterInputError(
            "osmium is not installed: it comes from the osmium-tool package, which "
            "`hammunition install osm-navit` installs (or `sudo apt install osmium-tool`)"
        ) from None
    if result.returncode != 0:
        said = result.stderr.strip().replace(str(pbf), label).replace(pbf.name, label)
        said = said.replace(pbf.name.removesuffix(".osm.pbf"), label)
        raise RepeaterInputError(
            f"osmium tags-filter failed on {label} (exit {result.returncode}): {said[:300]}"
        )
    try:
        text = out.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepeaterInputError(
            f"osmium wrote nothing readable for {label}: {exc.strerror or 'unreadable'}"
        ) from None
    finally:
        out.unlink(missing_ok=True)
    return read_osm_xml(text, Path(label))


def installed_extracts(prefix: Path) -> list[tuple[Path, str | None]]:
    """Every region extract installed under *prefix* (D-057), with the
    snapshot its sidecar records, by file name."""
    folder = prefix / "share" / "hammunition" / "data" / "osm-regions"
    if not folder.is_dir():
        return []
    out: list[tuple[Path, str | None]] = []
    for pbf in sorted(folder.glob("*.osm.pbf")):
        try:
            lines = pbf.with_name(pbf.name + ".source").read_text().splitlines()
        except OSError:
            lines = []
        out.append((pbf, (lines[0].strip() or None) if lines else None))
    return out


def _snapshot_date(pbf: Path, snapshot: str | None) -> date:
    if snapshot and re.fullmatch(r"\d{6}", snapshot):
        try:
            return date(2000 + int(snapshot[:2]), int(snapshot[2:4]), int(snapshot[4:6]))
        except ValueError:
            pass
    return date.fromtimestamp(pbf.stat().st_mtime)


def extracts_date(extracts: Sequence[tuple[Path, str | None]]) -> date:
    """The oldest extract's snapshot date (D-031: the data's date)."""
    return min(_snapshot_date(p, s) for p, s in extracts)


# --- across sources ---------------------------------------------------------------------


def _near(a: Repeater, b: Repeater, degrees: float) -> bool:
    return abs(a.lat - b.lat) <= degrees + 1e-9 and abs(a.lon - b.lon) <= degrees + 1e-9


def _fill(kept: Repeater, other: Repeater) -> Repeater:
    from dataclasses import replace

    return replace(
        kept,
        offset_hz=kept.offset_hz if kept.offset_hz is not None else other.offset_hz,
        tone=kept.tone or other.tone,
        mode=kept.mode or other.mode,
        modes=tuple(m for m in MODES if m in {*kept.modes, *other.modes}),
        digital=kept.digital or other.digital,
        place=kept.place or other.place,
        also=(*kept.also, other.source) if other.source not in kept.also else kept.also,
    )


def cross_merge(layers: Iterable[Iterable[Repeater]]) -> tuple[tuple[Repeater, ...], int]:
    """The directory layers joined into one list, and how many rows joined
    a row of another layer.

    Rows are taken best source first (:data:`PRECEDENCE`), each layer's in
    its own order. A row joins a kept row **of another layer** when its
    output frequency is the same and either it lies within 0.02° in
    latitude and longitude, or its callsign is the same and it lies within
    0.25°. Rows of one layer never join each other: D-064's key has already
    merged them, and what it kept apart is a separate site. The kept row
    keeps its position and fields, takes a field it lacks from the joining
    row, and names the joining source in ``also``. A row without a frequency
    is never joined. An APRS object is refused: it is what the station
    heard, not a directory entry."""
    rows, joined, _ = cross_merge_origins(layers)
    return rows, joined


def cross_merge_origins(
    layers: Iterable[Iterable[Repeater]],
) -> tuple[tuple[Repeater, ...], int, tuple[int, ...]]:
    """:func:`cross_merge`, and for each kept row the index (in *layers*) of
    the layer it came from, so a program can say which layer a row is."""
    tagged = [(n, row) for n, layer in enumerate(layers) for row in layer]
    if any(r.source == DIREWOLF for _, r in tagged):
        raise ValueError("an APRS object heard off the air is never merged into the directories")
    rank = {source: n for n, source in enumerate(PRECEDENCE)}
    tagged.sort(key=lambda item: rank[item[1].source])
    kept: list[Repeater] = []
    kept_layers: list[set[int]] = []
    origin: list[int] = []
    by_hz: dict[int, list[int]] = {}
    joined = 0
    for layer, row in tagged:
        match = None
        if row.output_hz:
            for index in by_hz.get(row.output_hz, []):
                if layer in kept_layers[index]:
                    continue
                other = kept[index]
                same_call = bool(row.callsign) and row.callsign == other.callsign
                if _near(row, other, NEAR_DEGREES) or (
                    same_call and _near(row, other, SAME_CALL_DEGREES)
                ):
                    match = index
                    break
        if match is None:
            if row.output_hz:
                by_hz.setdefault(row.output_hz, []).append(len(kept))
            kept.append(row)
            kept_layers.append({layer})
            origin.append(layer)
            continue
        joined += 1
        kept[match] = _fill(kept[match], row)
        kept_layers[match].add(layer)
    return tuple(kept), joined, tuple(origin)
