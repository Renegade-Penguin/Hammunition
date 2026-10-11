# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Forest Service FSTopo 7.5-minute quads: which a region needs, where each
is fetched from, and how it is checked.  D-068, amended 2026-10-01.

FSTopo is the Forest Service's own 7.5-minute series over National Forest
land: USFS trails by **trail number**, 40 ft contours, the forest's roads
and boundaries. US federal work, public domain. Which sheets exist is the
GTAC index (``FSTopo_Index_GTAC``), carried as
``catalog/data/fstopo-quads.txt`` (``scripts/gen_fstopo_index.py``) with
each sheet's box, ``secoord``, edition (a two-digit year, 0 where the
index gives none), state and name, so the plan
chooses a region's sheets with no network, the US Topo way (eighth-degree
cells its outline touches, :mod:`hammunition.ustopo`).

A sheet is fetched from the raster gateway: ``downloadMap.php?mapID=<secoord>
&mapType=tif&seriesType=FSTopo`` answers 302 to the file. The plan follows
that one redirect itself (:class:`GatewayProbe`), refuses one leading
anywhere but ``https://data.fs.usda.gov/geodata/rastergateway/`` and a
``.tif``/``.tiff`` file, and asks the file for its size; the download is the
``Location`` itself, never a redirect followed blind.

**The Forest Service publishes no checksum** (the gateway's ETag is
Apache's inode-size-mtime). A sheet with a row in
``catalog/data/fstopo-pins.yaml`` is checked against the sha256 and size the
maintainer measured (:data:`PINNED`); any other is fetched **unverified**,
only its size and its being a TIFF checked, and the plan says so by name
(:data:`UNVERIFIED`). The pins file starts empty.

Measured on 2026-09-29 on one sheet (Reddish Knob, George Washington
National Forest): a 21.2 MB paletted GeoTIFF, 8951 x 11380, stripped and
without overviews, in EPSG:4269 and clipped exactly to the quad, no collar.

Pure apart from the injected probe.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .copernicus import Ring
from .resolution import CatalogueMiss, ResolutionContext
from .retry import retrying_head
from .ustopo import QuadIndex, boxed_cells

__all__ = [
    "GATEWAY",
    "PINNED",
    "REMEDY",
    "UNVERIFIED",
    "FsIndex",
    "FsPin",
    "FsQuad",
    "FsQuadFile",
    "FstopoError",
    "GatewayProbe",
    "load_index",
    "load_pins",
    "map_url",
    "parse_index",
    "parse_row",
    "recorded_sheet",
    "render_row",
]

GATEWAY = "https://data.fs.usda.gov/geodata/rastergateway/"
PINNED = "sha256, pinned by Hammunition (the Forest Service publishes no checksum)"
UNVERIFIED = (
    "unverified: the Forest Service publishes no checksum and Hammunition has pinned "
    "none; only the size is checked"
)
REMEDY = "scripts/gen_fstopo_index.py --fetch regenerates the carried index"
PIN_REMEDY = "scripts/gen_fstopo_index.py --pin <secoord> measures it again"
_NUMBER = r"(-?\d{1,3}(?:\.\d{1,9})?)"
LINE = re.compile(
    rf"{_NUMBER} {_NUMBER} {_NUMBER} {_NUMBER} (\d{{5,12}}) (\d{{1,4}}) ([A-Z]{{2}}) (\S.*)"
)
_SLUG = re.compile(r"[^A-Za-z0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class FstopoError(Exception):
    """An index, a pin, a sheet or the gateway could not be read or trusted."""


@dataclass(frozen=True)
class FsQuad:
    """One FSTopo quad of the carried index."""

    south: float
    west: float
    north: float
    east: float
    secoord: int
    vintage: int
    state: str
    cell: str

    @property
    def name(self) -> str:
        """``<ST>_<Cell>_<secoord>_<vintage>``: the installed file's name
        without ``.tif``; the vintage last, so a new edition is a new name
        and the older one is replaced as US Topo's are."""
        slug = _SLUG.sub("_", self.cell).strip("_") or "Quad"
        return f"{self.state}_{slug}_{self.secoord}_{self.vintage}"

    @property
    def map_url(self) -> str:
        return map_url(self.secoord)


def map_url(secoord: int) -> str:
    return f"{GATEWAY}downloadMap.php?mapID={secoord}&mapType=tif&seriesType=FSTopo"


def _degrees(value: float) -> str:
    text = f"{value:.7f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def render_row(quad: FsQuad) -> str:
    return (
        f"{_degrees(quad.south)} {_degrees(quad.west)} {_degrees(quad.north)} "
        f"{_degrees(quad.east)} {quad.secoord} {quad.vintage} {quad.state} {quad.cell}"
    )


def parse_row(line: str, number: int = 1) -> FsQuad:
    match = LINE.fullmatch(line)
    if match is None:
        raise FstopoError(f"line {number} is not an FSTopo index row: {line[:100]!r}")
    south, west, north, east = (float(match.group(i)) for i in range(1, 5))
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise FstopoError(f"line {number}: the box {south} {west} {north} {east} is not a box")
    return FsQuad(
        south,
        west,
        north,
        east,
        int(match.group(5)),
        int(match.group(6)),
        match.group(7),
        match.group(8).strip(),
    )


@dataclass(frozen=True)
class FsIndex:
    """The carried index, looked up by eighth-degree cell."""

    quads: tuple[FsQuad, ...]
    by_cell: dict[tuple[int, int], tuple[FsQuad, ...]] = field(
        default_factory=dict, compare=False, repr=False
    )

    @classmethod
    def of(cls, quads: Sequence[FsQuad]) -> FsIndex:
        cells: dict[tuple[int, int], list[FsQuad]] = {}
        for quad in quads:
            for cell in boxed_cells(quad):
                cells.setdefault(cell, []).append(quad)
        return cls(tuple(quads), {cell: tuple(found) for cell, found in cells.items()})

    def select(self, outer: Sequence[Ring], holes: Sequence[Ring] = ()) -> tuple[FsQuad, ...]:
        """Every quad whose box overlaps a cell the outline touches, by secoord."""
        found: dict[int, FsQuad] = {}
        for cell in QuadIndex.touched(outer, holes):
            for quad in self.by_cell.get(cell, ()):
                found[quad.secoord] = quad
        return tuple(found[s] for s in sorted(found))

    def by_secoord(self) -> dict[int, FsQuad]:
        return {quad.secoord: quad for quad in self.quads}


def parse_index(text: str) -> FsIndex:
    """The carried index; an empty one is refused, never read as "no sheet"."""
    quads: list[FsQuad] = []
    seen: set[int] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        quad = parse_row(line, number)
        if quad.secoord in seen:
            raise FstopoError(f"line {number} carries secoord {quad.secoord} a second time")
        seen.add(quad.secoord)
        quads.append(quad)
    if not quads:
        raise FstopoError(
            "the FSTopo index names no quads; an empty or comment-only "
            f"catalog/data/fstopo-quads.txt would otherwise select nothing anywhere. {REMEDY}"
        )
    return FsIndex.of(quads)


def load_index(path: Path) -> FsIndex:
    try:
        text = path.read_text()
    except OSError as exc:
        raise FstopoError(
            f"the FSTopo index {path} cannot be read ({exc.strerror or exc}); it is carried "
            f"in the catalog, and {REMEDY}"
        ) from exc
    return parse_index(text)


@dataclass(frozen=True)
class FsPin:
    """A sheet the maintainer measured: its size and sha256."""

    secoord: int
    size: int
    sha256: str


def load_pins(path: Path) -> dict[int, FsPin]:
    """The carried pins, validated row by row; every way the file can be
    wrong is an :class:`FstopoError` naming it."""
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise FstopoError(f"{path} cannot be read ({exc.strerror or exc})") from exc
    except yaml.YAMLError as exc:
        raise FstopoError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("pins"), list):
        raise FstopoError(
            f"{path} has no `pins:` list; scripts/gen_fstopo_index.py writes it, with "
            f"`pins: []` when nothing is pinned"
        )
    pins: dict[int, FsPin] = {}
    for number, row in enumerate(data["pins"], 1):
        where = f"{path}: pin {number}"
        if not isinstance(row, dict) or {"secoord", "size", "sha256"} - set(row):
            raise FstopoError(f"{where} is not a mapping of secoord, size and sha256")
        secoord, size, sha256 = row["secoord"], row["size"], row["sha256"]
        if not isinstance(secoord, int) or isinstance(secoord, bool) or secoord <= 0:
            raise FstopoError(f"{where}: secoord {secoord!r} is not a positive whole number")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise FstopoError(f"{where}: size {size!r} is not a positive whole number of bytes")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise FstopoError(f"{where}: sha256 is not 64 lowercase hex digits")
        if secoord in pins:
            raise FstopoError(f"{where} pins {secoord} a second time")
        pins[secoord] = FsPin(secoord, size, sha256)
    return pins


@dataclass(frozen=True)
class FsQuadFile:
    """A sheet resolved for download at plan time."""

    quad: FsQuad
    url: str
    """The file the gateway's redirect names, percent-encoded."""
    size: int
    sha256: str | None
    """The maintainer's pin, or None: fetched unverified."""

    @property
    def name(self) -> str:
        return self.quad.name

    @property
    def verified_by(self) -> str:
        return PINNED if self.sha256 else UNVERIFIED


Head = Callable[[str], tuple[int, int, str | None]]
"""A ``HEAD`` that follows no redirect: (status, Content-Length, Location)."""


def _real_head(url: str, timeout: float = 30.0) -> tuple[int, int, str | None]:
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "hammunition"})
    request.method = "HEAD"
    try:
        response = opener.open(request, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        raise FstopoError(f"{url} could not be reached: {exc}") from exc
    if response is None:  # pragma: no cover - no handler claimed the scheme
        raise FstopoError(f"no handler would ask {url!r}")
    with response:
        length = response.headers.get("Content-Length")
        size = int(length) if length and length.isdigit() else 0
        return response.status, size, response.headers.get("Location")


class GatewayProbe:
    """Where the raster gateway sends a sheet, and how large it is. The one
    part of this module that touches the network, and only
    ``data.fs.usda.gov``: a redirect anywhere else is refused, never followed."""

    def __init__(self, head: Head | None = None) -> None:
        # Under the plan-time retry policy (#200) unless a test supplies its own.
        self._head: Head = head or retrying_head(_real_head)

    def locate(self, secoord: int) -> tuple[str, int]:
        """(the sheet's file URL, its size), or an :class:`FstopoError` naming why not."""
        start = map_url(secoord)
        status, _, location = self._head(start)
        if status not in (301, 302, 303, 307, 308) or not location:
            raise FstopoError(
                f"{secoord}: the gateway answered HTTP {status} with no redirect to a sheet; "
                f"the Forest Service may publish no GeoTIFF for it. {REMEDY}"
            )
        url = urllib.parse.quote(location, safe=":/?&=%")
        if not url.startswith(GATEWAY) or not url.lower().endswith((".tif", ".tiff")):
            raise FstopoError(
                f"{secoord}: the gateway redirected to {location!r}, which is not a GeoTIFF "
                f"under {GATEWAY}; refused, not followed"
            )
        if ".." in urllib.parse.urlsplit(url).path.split("/"):
            raise FstopoError(f"{secoord}: the redirect {location!r} climbs out of the gateway")
        if not _is_gateway_geotiff(url):
            raise FstopoError(
                f"{secoord}: the gateway redirected to {location!r}, which is not a plain "
                f"GeoTIFF path under {GATEWAY}; refused, not followed"
            )
        status, size, _ = self._head(url)
        if status != 200 or size <= 0:
            raise FstopoError(
                f"{secoord}: {url} answered HTTP {status} with {size} bytes; nothing to "
                f"fetch or bound the download by"
            )
        return url, size


_GATEWAY_PARTS = urllib.parse.urlsplit(GATEWAY)


def _is_gateway_geotiff(url: str) -> bool:
    """Whether *url* is a GeoTIFF file under the raster gateway, by its parts.

    A prefix test lets a look-alike host, userinfo, a port, a backslash or an
    encoded ``..`` through; this asks for all of: scheme https, exactly the
    gateway's host (so no userinfo and no port), a path under the gateway's
    path with a ``/`` boundary, no control character, backslash or ``.``/``..``
    segment however many times it is percent-encoded, and a ``.tif``/``.tiff``
    name. No query, fragment, empty segment or encoded slash. The URL must already be canonically percent-encoded, as the live
    probe leaves it."""
    if not url or url != urllib.parse.quote(url, safe=":/?&=%"):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if (
        parts.scheme != "https"
        or parts.netloc != _GATEWAY_PARTS.netloc
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or parts.fragment
        or parts.query
        or not parts.path.startswith(_GATEWAY_PARTS.path)
        or not parts.path.lower().endswith((".tif", ".tiff"))
    ):
        return False
    path = parts.path
    for _ in range(4):  # percent-encoding of percent-encoding
        if "\\" in path or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
            return False
        segments = path.split("/")
        if any(segment in (".", "..") for segment in segments):
            return False
        if "" in segments[1:] or "%2f" in path.lower():
            return False  # an empty segment, or a slash hidden inside one
        decoded = urllib.parse.unquote(path)
        if decoded == path:
            return True
        path = decoded
    return False


def recorded_sheet(
    quad: FsQuad, *, unit: str, pins: Mapping[int, FsPin], context: ResolutionContext
) -> FsQuadFile:
    """*quad* as the Bunker's record has it, offline.

    The record only ever supplies the file's URL and, for a sheet the
    repository has no pin for, its announced size. A pinned sheet is checked
    against the repository's own pin (size, then sha256), never against the
    record's view of itself; an unpinned one stays **unverified** however the
    Bunker labels it, so a sha256 in the record never makes it pinned. The
    bytes are checked later, by the fetch. The record must be explicitly
    labelled unverified (``unverified-fetch``/``unverified-zip``); this grants
    nothing: the plan's disclosure and every consent gate are unchanged."""
    row = context.entry(unit, quad.name)
    url = row.publisher_url or ""
    if not _is_gateway_geotiff(url):
        raise CatalogueMiss(f"{unit}/{quad.name}: publisher_url is not a Forest Service GeoTIFF")
    pin = pins.get(quad.secoord)
    if pin is not None:
        context.require_payload(unit, quad.name, size=pin.size)
        context.require_payload(unit, quad.name, sha256=pin.sha256)
        return FsQuadFile(quad, url, pin.size, pin.sha256)
    context.unverified(unit, quad.name)
    if row.publisher_size is None or row.publisher_size <= 0 or row.publisher_size != row.size:
        raise CatalogueMiss(f"{unit}/{quad.name}: publisher_size is absent or inconsistent")
    return FsQuadFile(quad, url, row.publisher_size, None)
