# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Copernicus GLO-30 elevation tiles: which squares a region needs, and how
each tile is verified.  D-061.

One tile is one 1 x 1 degree square, named by its south-west corner
(``Copernicus_DSM_COG_10_N01_00_E001_00_DEM`` covers latitude 1 to 2 and
longitude 1 to 2), a Cloud Optimised GeoTIFF of about 39 MB at
``<bucket>/<name>/<name>.tif``. The bucket's ``tileList.txt`` names every tile
that exists. A square not in that list has *no published tile*, never an
error: it is ocean, or land the publisher withholds from the public release
(the final review found Armenia and Azerbaijan absent), and the list cannot
say which, so Hammunition never calls it either. The list is carried in the catalog
(``catalog/data/copernicus-glo30-tiles.txt``) so the plan needs no network to
know it.

A region's squares are the ones its Geofabrik ``.poly`` outline touches,
edge or interior. Measured 2026-09-28 on two installed regions: the
``.osm.pbf`` header's bounding box spanned hundreds of squares for one of
them where its outline touches tens, and did not even contain the outline's
own bounding box; the outline is what the extract was cut with.

Verified in D-057's two modes: a tile with a row in
``catalog/data/copernicus-glo30-pins.yaml`` by the sha256 Hammunition
measured (:data:`PINNED`); any other by the MD5 in the object's S3 ETag, a
single-part upload's MD5, measured equal to ``md5sum`` on one tile
(:data:`UNPINNED`). The plan says which, tile by tile.

Pure apart from the injected :class:`TileProbe`.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from hammunition.resolution import CatalogueMiss, ResolutionContext

BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"
PINNED = "sha256, pinned by Hammunition"
UNPINNED = "MD5 from the publisher's object metadata; not pinned by Hammunition"
TILE = re.compile(r"Copernicus_DSM_COG_10_([NS])(\d{2})_00_([EW])(\d{3})_00_DEM")
_ETAG = re.compile(r'"?([0-9a-f]{32})"?')

Square = tuple[int, int]
"""(latitude, longitude) of a square's south-west corner, in whole degrees."""
Point = tuple[float, float]
"""(longitude, latitude), the order a ``.poly`` file writes."""
Ring = tuple[Point, ...]


class CopernicusError(Exception):
    """A tile, an outline or a list could not be read or verified."""


def tile_name(square: Square) -> str:
    lat, lon = square
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def square_of(name: str) -> Square:
    match = TILE.fullmatch(name)
    if match is None:
        raise CopernicusError(f"{name!r} is not a Copernicus GLO-30 tile name")
    ns, lat, ew, lon = match.groups()
    return (int(lat) * (1 if ns == "N" else -1), int(lon) * (1 if ew == "E" else -1))


def tile_url(name: str) -> str:
    square_of(name)  # refuses anything that is not a tile name
    return f"{BUCKET}/{name}/{name}.tif"


def _wrap(lon: int, per_degree: int = 1) -> int:
    """A whole-cell longitude folded into -180..179 degrees' worth of cells."""
    half = 180 * per_degree
    return (lon + half) % (2 * half) - half


def parse_poly(text: str) -> tuple[list[Ring], list[Ring]]:
    """(outer rings, holes) of an Osmosis ``.poly`` outline.

    The format: a name line; then sections, each a name line (a leading
    ``!`` marks a hole), coordinate lines ``lon lat`` and ``END``; then a
    final ``END``. Refused by name when malformed: an outline that cannot be
    read would select no squares, and a region with no terrain would look
    like a region with nothing to do.
    """
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) < 2 or lines[-1] != "END":
        raise CopernicusError("the outline does not end with END")
    outer: list[Ring] = []
    holes: list[Ring] = []
    current: list[Point] | None = None
    hole = False
    for line in lines[1:-1]:
        if current is None:
            current, hole = [], line.startswith("!")
            continue
        if line == "END":
            if len(current) < 3:
                raise CopernicusError("an outline ring has fewer than three points")
            (holes if hole else outer).append(tuple(current))
            current = None
            continue
        parts = line.split()
        if len(parts) != 2:
            raise CopernicusError(f"not a coordinate line: {line[:60]!r}")
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise CopernicusError(f"not a coordinate line: {line[:60]!r}") from exc
        if not (-360.0 <= lon <= 360.0 and -90.0 <= lat <= 90.0):
            raise CopernicusError(f"a coordinate is out of range: {line[:60]!r}")
        current.append((lon, lat))
    if current is not None:
        raise CopernicusError("an outline ring is not closed with END")
    if not outer:
        raise CopernicusError("the outline has no outer ring")
    return outer, holes


def bbox_ring(left: float, right: float, top: float, bottom: float) -> Ring:
    """A bounding box as a ring; one crossing the antimeridian (``left > right``)
    is unwrapped eastwards so its squares are the ones it covers."""
    if right < left:
        right += 360.0
    return ((left, bottom), (right, bottom), (right, top), (left, top))


def _edge_squares(a: Point, b: Point, out: set[Square], per_degree: int = 1) -> None:
    if abs(a[0] - b[0]) > 180.0 * per_degree:
        raise CopernicusError(
            f"an outline edge spans more than 180 degrees of longitude, from "
            f"{(a[0] / per_degree, a[1] / per_degree)!r} to "
            f"{(b[0] / per_degree, b[1] / per_degree)!r}; no Geofabrik outline measured jumps across +/-180 "
            f"in one segment (Alaska, Fiji, New Zealand and Russia's far east are "
            f"rings meeting it, D-061), so this one is refused rather than "
            f"guessed at"
        )
    (x1, y1), (x2, y2) = sorted((a, b))
    for column in range(math.floor(x1), math.floor(x2) + 1):
        if x2 == x1:
            ya, yb = y1, y2
        else:
            xa, xb = max(x1, column), min(x2, column + 1)
            ya = y1 + (y2 - y1) * (xa - x1) / (x2 - x1)
            yb = y1 + (y2 - y1) * (xb - x1) / (x2 - x1)
        for row in range(math.floor(min(ya, yb)), math.floor(max(ya, yb)) + 1):
            out.add((row, column))


def _inside(x: float, y: float, rings: Sequence[Ring]) -> bool:
    """Even-odd over every ring, so a square wholly inside a hole is outside."""
    crossings = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                crossings = not crossings
    return crossings


def squares_touching(
    outer: Sequence[Ring], holes: Sequence[Ring] = (), *, per_degree: int = 1
) -> frozenset[Square]:
    """Every square the outline touches: those its edges pass through, and
    those whose centre lies inside it. Longitudes are folded into -180..179,
    so an outline written past the antimeridian selects the squares it
    covers; latitudes are kept to -90..89, the squares that exist.

    *per_degree* cuts each degree into that many cells a side and returns
    cell indices at that scale: 8 gives the 1/8-degree cells US Topo's
    7.5-minute quads sit on (D-068), where cell ``(row, column)`` spans
    latitude ``row/8`` to ``(row+1)/8``. Copernicus's tiles are the default, 1.
    """
    if per_degree < 1:
        raise CopernicusError(f"per_degree must be at least 1, got {per_degree}")

    def scaled(ring: Ring) -> Ring:
        return tuple((x * per_degree, y * per_degree) for x, y in ring)

    outer = [scaled(ring) for ring in outer]
    holes = [scaled(ring) for ring in holes]
    found: set[Square] = set()
    rings = [*outer, *holes]
    for ring in rings:
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            _edge_squares(a, b, found, per_degree)
    xs = [x for ring in outer for x, _ in ring]
    ys = [y for ring in outer for _, y in ring]
    for row in range(math.floor(min(ys)), math.floor(max(ys)) + 1):
        for column in range(math.floor(min(xs)), math.floor(max(xs)) + 1):
            if _inside(column + 0.5, row + 0.5, rings):
                found.add((row, column))
    top = 90 * per_degree
    return frozenset(
        (row, _wrap(column, per_degree)) for row, column in found if -top <= row <= top - 1
    )


def select(squares: Iterable[Square], tile_list: frozenset[str]) -> tuple[tuple[str, ...], int]:
    """(the tiles that exist for *squares*, sorted; how many squares have no
    published tile -- sea, or land Copernicus does not release)."""
    if not tile_list:
        raise CopernicusError(
            "the tile list is empty; refusing to plan every square as unpublished rather "
            "than risk a truncated or emptied "
            "catalog/data/copernicus-glo30-tiles.txt going unnoticed"
        )
    names = {tile_name(s) for s in squares}
    tiles = tuple(sorted(names & tile_list))
    return tiles, len(names) - len(tiles)


def parse_tile_list(text: str) -> frozenset[str]:
    """The names in a tile list: one per line, ``#`` comments and blanks skipped."""
    names: set[str] = set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if TILE.fullmatch(line) is None:
            raise CopernicusError(f"line {number} is not a tile name: {line[:80]!r}")
        names.add(line)
    if not names:
        raise CopernicusError(
            "the tile list names no tiles; an empty or comment-only "
            "catalog/data/copernicus-glo30-tiles.txt would otherwise make every "
            "square unpublished with no warning"
        )
    return frozenset(names)


def load_tile_list(path: Path) -> frozenset[str]:
    return parse_tile_list(path.read_text())


@dataclass(frozen=True)
class TilePin:
    name: str
    size: int
    sha256: str
    md5: str


_SHA256 = re.compile(r"[0-9a-f]{64}")
_MD5 = re.compile(r"[0-9a-f]{32}")


def _pin(path: Path, number: int, row: object) -> TilePin:
    """One row of the pins file, or a refusal naming the file and the row."""
    where = f"{path}: pin {number}"
    if not isinstance(row, dict):
        raise CopernicusError(f"{where} is not a mapping of tile, size, sha256 and md5")
    missing = [key for key in ("tile", "size", "sha256", "md5") if key not in row]
    if missing:
        raise CopernicusError(f"{where} has no {', '.join(missing)}")
    name, size, sha256, md5 = row["tile"], row["size"], row["sha256"], row["md5"]
    if not isinstance(name, str) or TILE.fullmatch(name) is None:
        raise CopernicusError(f"{where}: {name!r} is not a Copernicus GLO-30 tile name")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise CopernicusError(f"{where}: size {size!r} is not a positive whole number of bytes")
    if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
        raise CopernicusError(f"{where}: sha256 is not 64 lowercase hex digits")
    if not isinstance(md5, str) or _MD5.fullmatch(md5) is None:
        raise CopernicusError(f"{where}: md5 is not 32 lowercase hex digits")
    return TilePin(name, size, sha256, md5)


def load_pins(path: Path) -> dict[str, TilePin]:
    """The carried sha256 pins, validated row by row.

    Every way the file can be wrong -- unreadable, YAML that does not parse,
    no ``pins`` list, a row missing a key or carrying a malformed value, a tile
    pinned twice -- is a :class:`CopernicusError` naming the file, which the
    plan turns into a refusal (final review, M1), never a traceback."""
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise CopernicusError(f"{path} cannot be read ({exc.strerror or exc})") from exc
    except yaml.YAMLError as exc:
        raise CopernicusError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("pins"), list):
        raise CopernicusError(
            f"{path} has no `pins:` list; scripts/gen_copernicus_pins.py writes it, "
            f"with `pins: []` when nothing is pinned"
        )
    pins: dict[str, TilePin] = {}
    for number, row in enumerate(data["pins"], 1):
        pin = _pin(path, number, row)
        if pin.name in pins:
            raise CopernicusError(f"{path}: pin {number} pins {pin.name} a second time")
        pins[pin.name] = pin
    return pins


@dataclass(frozen=True)
class TileFile:
    name: str
    url: str
    size: int
    sha256: str | None
    md5: str | None
    etag: str | None = None
    """An S3 ETag, single-part or multipart, for a provider whose list
    carries one (USGS 3DEP, D-068 amended 2026-10-01)."""

    @property
    def verified_by(self) -> str:
        if self.sha256:
            return PINNED
        if self.md5 or self.etag:
            return UNPINNED
        raise CopernicusError(
            f"{self.name}: has neither a sha256 pin nor an MD5; nothing verifies it"
        )


class TileProbe(Protocol):
    def head(self, url: str) -> tuple[int, int, str | None]: ...


HEAD_CACHE_TTL = 6 * 60 * 60
_HEAD_CACHE_LIMIT = 20_000


class CachingTileProbe:
    """A thread-safe, persistent cache for successful tile ``HEAD`` answers."""

    def __init__(
        self,
        inner: TileProbe,
        cache_dir: Path,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.cache_path = cache_dir / "tile-heads.json"
        self.now = now
        self._lock = threading.Lock()
        self._local = threading.local()
        self._entries = self._read()
        self._dirty = False
        if self._trim():
            self._dirty = True

    def head(self, url: str) -> tuple[int, int, str | None]:
        current = self.now()
        with self._lock:
            cached = self._entries.get(url)
            if cached is not None and current - cached[3] < HEAD_CACHE_TTL:
                self._local.hits = self.thread_hits() + 1
                return cached[0], cached[1], cached[2]

        self._local.misses = self.thread_misses() + 1
        result = self.inner.head(url)
        if result[0] == 200:
            with self._lock:
                self._entries[url] = (*result, int(self.now()))
                self._trim()
                self._dirty = True
        return result

    def thread_hits(self) -> int:
        """Answers this thread has been served from the cache."""
        return int(getattr(self._local, "hits", 0))

    def thread_misses(self) -> int:
        """Answers this thread has had to ask the publisher for."""
        return int(getattr(self._local, "misses", 0))

    def flush(self) -> None:
        """Atomically write updates once a batch of checks has completed."""
        with self._lock:
            if not self._dirty:
                return
            entries = self._read()
            entries.update(self._entries)
            self._entries = entries
            self._trim()
            descriptor: int | None = None
            temporary: str | None = None
            try:
                self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(prefix=".tile-heads-", dir=self.cache_dir)
                os.fchmod(descriptor, 0o600)
                try:
                    stream = os.fdopen(descriptor, "w", encoding="utf-8")
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.close(descriptor)
                    descriptor = None
                    raise
                descriptor = None
                with stream:
                    json.dump(
                        {
                            url: {
                                "status": value[0],
                                "size": value[1],
                                "etag": value[2],
                                "at": value[3],
                            }
                            for url, value in self._entries.items()
                        },
                        stream,
                        sort_keys=True,
                    )
                    stream.write("\n")
                os.replace(temporary, self.cache_path)
            except BaseException as exc:
                if descriptor is not None:
                    with contextlib.suppress(OSError):
                        os.close(descriptor)
                if temporary is not None:
                    with contextlib.suppress(OSError):
                        os.unlink(temporary)
                if isinstance(exc, OSError):
                    return
                raise
            self._dirty = False

    def _read(self) -> dict[str, tuple[int, int, str | None, int]]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        entries: dict[str, tuple[int, int, str | None, int]] = {}
        for url, record in raw.items():
            if not isinstance(url, str) or not isinstance(record, dict):
                continue
            status, size, etag, at = (
                record.get("status"),
                record.get("size"),
                record.get("etag"),
                record.get("at"),
            )
            if (
                type(status) is int
                and status == 200
                and type(size) is int
                and size >= 0
                and (etag is None or isinstance(etag, str))
                and type(at) is int
            ):
                entries[url] = (status, size, etag, at)
        return entries

    def _trim(self) -> bool:
        if len(self._entries) <= _HEAD_CACHE_LIMIT:
            return False
        oldest = sorted(self._entries, key=lambda url: self._entries[url][3])
        for url in oldest[: len(oldest) - _HEAD_CACHE_LIMIT]:
            del self._entries[url]
        return True


class S3Probe:
    """The real :class:`TileProbe`: a ``HEAD`` to the Copernicus bucket, its
    status, ``Content-Length`` and ``ETag``. The one part of this module that
    touches the network.

    Built the way :class:`hammunition.geofabrik.UrllibProbe` is: only the HTTP
    handlers, no redirect handler and no error processor, so a 404 or a 3xx
    comes back as a status rather than being followed; no ``file:`` URL is ever
    served, and nothing outside :data:`BUCKET` is asked. Nothing it returns is
    trusted: the size and MD5 become what the download is checked against.
    """

    def __init__(self, *, timeout: float = 30.0, bucket: str = BUCKET) -> None:
        self.timeout = timeout
        self.bucket = bucket
        opener = urllib.request.OpenerDirector()
        opener.add_handler(urllib.request.HTTPHandler())
        opener.add_handler(urllib.request.HTTPSHandler())
        self._opener = opener

    def head(self, url: str) -> tuple[int, int, str | None]:
        if not url.startswith(self.bucket + "/"):
            raise CopernicusError(f"refusing {url!r}: only {self.bucket} is asked")
        request = urllib.request.Request(url, headers={"User-Agent": "hammunition"})
        request.method = "HEAD"
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except (urllib.error.URLError, OSError) as exc:
            raise CopernicusError(f"{url} could not be reached: {exc}") from exc
        if response is None:  # pragma: no cover - no handler claimed the scheme
            raise CopernicusError(f"no handler would ask {url!r}")
        with response:
            length = response.headers.get("Content-Length")
            size = int(length) if length and length.isdigit() else 0
            return response.status, size, response.headers.get("ETag")


def _resolve_tile_online(name: str, *, pins: Mapping[str, TilePin], probe: TileProbe) -> TileFile:
    """*name* as a verifiable download: from its pin, asking nothing, or from
    the bucket's ``HEAD`` -- its size and its ETag's MD5.

    An ETag that is not 32 hex digits (a multipart upload's is
    ``<hex>-<parts>``) is not an MD5 of the object, and the tile is refused
    by name rather than downloaded unverified.
    """
    url = tile_url(name)
    pin = pins.get(name)
    if pin is not None:
        return TileFile(name, url, pin.size, pin.sha256, None)
    status, size, etag = probe.head(url)
    if status != 200:
        raise CopernicusError(f"{url} answered HTTP {status}, not 200")
    if size <= 0:
        raise CopernicusError(
            f"{name}: the bucket reported no size for it, so the download cannot be "
            f"bounded or checked; try again later"
        )
    match = _ETAG.fullmatch((etag or "").strip())
    if match is None:
        raise CopernicusError(
            f"{name}: its ETag {etag!r} is not a single-part MD5, so there is nothing "
            f"to verify the download by; it needs a sha256 pin "
            f"(scripts/gen_copernicus_pins.py)"
        )
    return TileFile(name, url, size, None, match.group(1))


def recorded_tile(
    name: str, *, unit: str, pins: Mapping[str, TilePin], context: ResolutionContext
) -> TileFile:
    """*name* from the verified catalogue, without asking the bucket.

    A repository pin wins: the Bunker's copy must carry the pinned sha256 and
    size. Otherwise the record must be this bucket's own object, named for
    the tile, with a single-part MD5 (a multipart ETag is not a digest of the
    object) and a publisher size equal to the stored size."""
    url = tile_url(name)
    pin = pins.get(name)
    if pin is not None:
        context.require_payload(unit, name, sha256=pin.sha256, size=pin.size)
        return TileFile(name, url, pin.size, pin.sha256, None)
    row = context.entry(unit, name)
    if row.publisher_url != url or row.publisher_name != f"{name}.tif":
        raise CatalogueMiss(f"{unit}/{name}: publisher URL/name disagree with the tile")
    if row.publisher_size is None or row.publisher_size <= 0 or row.publisher_size != row.size:
        raise CatalogueMiss(f"{unit}/{name}: publisher size is missing or disagrees")
    digest = row.publisher_digest or ""
    if row.publisher_check != "etag-md5" or _ETAG.fullmatch(digest) is None:
        raise CatalogueMiss(f"{unit}/{name}: publisher_digest is not a single-part MD5")
    context.require_payload(
        unit,
        name,
        sha256=row.sha256,
        size=row.publisher_size,
        publisher_digest=row.publisher_digest,
    )
    return TileFile(name, url, row.publisher_size, None, digest.strip('"'))


def resolve_tile(
    name: str,
    *,
    pins: Mapping[str, TilePin],
    probe: TileProbe,
    context: ResolutionContext | None = None,
    unit: str | None = None,
) -> TileFile:
    """:func:`_resolve_tile_online`, or with a *context* the Bunker's record
    when the bucket cannot be asked (offline, or its retries spent). *unit* is
    the dem-tiles manifest's name, the catalogue key; it is required with a
    context and never inferred."""
    if context is None:
        return _resolve_tile_online(name, pins=pins, probe=probe)
    if unit is None:
        raise ValueError("resolve_tile needs the unit name when given a context")
    return context.choose(
        unit,
        name,
        lambda: _resolve_tile_online(name, pins=pins, probe=probe),
        lambda: recorded_tile(name, unit=unit, pins=pins, context=context),
    )
