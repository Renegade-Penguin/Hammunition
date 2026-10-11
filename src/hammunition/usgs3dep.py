# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""USGS 3DEP 1/3-arc-second elevation tiles: names, the carried list, and
the plan-time check.  D-068, amended 2026-10-01.

3DEP is bare earth: measured by the spike on 2026-09-29 over a forested
window inside Shenandoah National Park, Copernicus GLO-30 read 11.8 m above
it on average (sigma 6.6 m), the canopy, which is why it is offered beside
Copernicus and chosen by the station's ``dem_source``. It is about ten times
the size: 488 MB a tile against Copernicus's 46 MB.

One tile is one degree square, 10,812 pixels a side (the 10,800 of a degree
at 1/3 arc second and 6 pixels of overlap past each edge), Float32 in NAD83,
at ``<bucket>/StagedProducts/Elevation/13/TIFF/current/<t>/USGS_13_<t>.tif``
-- named by its **north-west** corner, where Copernicus names the south-west:
``USGS_13_n39w079`` covers latitude 38 to 39 and longitude -79 to -78. Which
tiles exist, with each object's size and S3 ETag, is carried as
``catalog/data/usgs-3dep-tiles.txt`` (``scripts/gen_3dep_tiles.py``), so a
plan needs no network to know a region's tiles; a square with no tile is
"no published tile", never an error, as for Copernicus (D-061).

Verification is the S3 ETag (:mod:`hammunition.s3etag`): the multipart
form ``<md5>-<parts>`` is reproduced by trying part sizes, as US Topo's
sheets are (the spike reproduced ``…-94`` at 5 MiB on one 488 MB tile).
The 2025 objects also carry a full-object CRC-64/NVME, which is not used:
no standard-library implementation exists, a table-driven pure-Python one
(checked against the standard ``123456789`` vector) ran at 6.6 MB/s on
2026-10-01, over a minute for one tile beside an MD5 pass at disk speed,
and the ETag covers every object, CRC header or not. Neither is a
sha256 Hammunition measured, and the plan says so tile by tile in the
Copernicus wording (:data:`~hammunition.copernicus.UNPINNED`).

Pure apart from the injected probe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import s3etag
from .copernicus import CopernicusError, Square, TileProbe
from .copernicus import square_of as copernicus_square
from .resolution import CatalogueMiss, ResolutionContext

__all__ = [
    "BUCKET",
    "PIXELS",
    "PREFIX",
    "REMEDY",
    "TileRow",
    "check_tile",
    "dem_square",
    "load_tile_list",
    "parse_tile_list",
    "square_of",
    "tile_name",
    "tile_url",
]

BUCKET = "https://prd-tnm.s3.amazonaws.com"
PREFIX = "StagedProducts/Elevation/13/TIFF/current/"
TILE = re.compile(r"USGS_13_([ns])(\d{2})([ew])(\d{3})")
#: A tile's width and height in pixels: the contours are rasterised on the
#: tile's degree at this size, so the 1/3" detail is kept (``gdal-dem``).
PIXELS = 10812
REMEDY = "scripts/gen_3dep_tiles.py --fetch regenerates the carried list"
LINE = re.compile(r"(USGS_13_[ns]\d{2}[ew]\d{3}) (\d{1,12}) (\S+)")


def tile_name(square: Square) -> str:
    """The tile covering *square* (its south-west corner), by its north-west one."""
    lat, lon = square
    north = lat + 1
    ns = "n" if north >= 0 else "s"
    ew = "e" if lon >= 0 else "w"
    return f"USGS_13_{ns}{abs(north):02d}{ew}{abs(lon):03d}"


def square_of(name: str) -> Square:
    """The south-west corner of the square *name* covers, as Copernicus's."""
    match = TILE.fullmatch(name)
    if match is None:
        raise CopernicusError(f"{name!r} is not a USGS 3DEP 1/3-arc-second tile name")
    ns, north, ew, west = match.groups()
    return (int(north) * (1 if ns == "n" else -1) - 1, int(west) * (1 if ew == "e" else -1))


def dem_square(name: str) -> Square:
    """The square of a tile of either provider, by its name."""
    if TILE.fullmatch(name):
        return square_of(name)
    return copernicus_square(name)


def tile_url(name: str) -> str:
    square_of(name)  # refuses anything that is not a tile name
    folder = name.removeprefix("USGS_13_")
    return f"{BUCKET}/{PREFIX}{folder}/{name}.tif"


@dataclass(frozen=True)
class TileRow:
    """One tile of the carried list: its name, size and S3 ETag."""

    name: str
    size: int
    etag: str


def parse_tile_list(text: str) -> dict[str, TileRow]:
    """The carried list, by name; refused when empty, as Copernicus's is: a
    truncated file would otherwise read as "no tile anywhere"."""
    rows: dict[str, TileRow] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if match is None:
            raise CopernicusError(f"line {number} is not a 3DEP tile row: {line[:80]!r}")
        name, size, etag = match.group(1), int(match.group(2)), match.group(3)
        try:
            s3etag.parse_etag(etag)
        except s3etag.EtagError as exc:
            raise CopernicusError(f"line {number}: {exc}") from exc
        if size <= 0:
            raise CopernicusError(f"line {number}: size {size} is not a positive number")
        if name in rows:
            raise CopernicusError(f"line {number} carries {name} a second time")
        rows[name] = TileRow(name, size, etag)
    if not rows:
        raise CopernicusError(
            "the 3DEP tile list names no tiles; an empty or comment-only "
            f"catalog/data/usgs-3dep-tiles.txt would otherwise select nothing. {REMEDY}"
        )
    return rows


def load_tile_list(path: Path) -> dict[str, TileRow]:
    try:
        text = path.read_text()
    except OSError as exc:
        raise CopernicusError(
            f"the 3DEP tile list {path} cannot be read ({exc.strerror or exc}); it is "
            f"carried in the catalog, and {REMEDY}"
        ) from exc
    return parse_tile_list(text)


def _check_tile_online(row: TileRow, probe: TileProbe) -> None:
    """Refuse *row* unless the bucket still serves it as the list says:
    status 200, the same size and the same ETag."""
    url = tile_url(row.name)
    status, size, etag = probe.head(url)
    if status != 200:
        raise CopernicusError(f"{row.name}: {url} answered HTTP {status}, not 200; {REMEDY}")
    if size != row.size or (etag or "").strip().strip('"') != row.etag:
        raise CopernicusError(
            f"{row.name}: the bucket now reports {size} bytes and ETag {etag!r}, where the "
            f"carried list has {row.size} and {row.etag!r}; the object changed since the "
            f"list was built, so it is not fetched against the old checksum. {REMEDY}"
        )


def _recorded_tile(row: TileRow, context: ResolutionContext, unit: str) -> None:
    """The Bunker's record for *row*, compared with the carried list (the
    independent side): size, the multipart-capable ETag and the URL. The bytes
    are checked by the fetch (``Fetcher.fetch_etag`` against ``row.etag`` and
    ``row.size``), not here."""
    context.require_payload(unit, row.name, size=row.size)
    found = context.require_payload(unit, row.name, publisher_digest=row.etag)
    if found.publisher_url != tile_url(row.name):
        raise CatalogueMiss(f"{unit}/{row.name}: publisher URL disagrees with the list")
    if found.publisher_size is not None and found.publisher_size != row.size:
        raise CatalogueMiss(f"{unit}/{row.name}: publisher size disagrees with the list")


def check_tile(
    row: TileRow,
    probe: TileProbe,
    *,
    context: ResolutionContext | None = None,
    unit: str | None = None,
) -> None:
    """:func:`_check_tile_online`, or with a *context* the Bunker's record when
    the bucket cannot be asked. *row* is never replaced."""
    if context is None:
        return _check_tile_online(row, probe)
    if unit is None:
        raise CopernicusError("3DEP catalogue check needs its resolved unit name")
    return context.choose(
        unit,
        row.name,
        lambda: _check_tile_online(row, probe),
        lambda: _recorded_tile(row, context, unit),
    )
