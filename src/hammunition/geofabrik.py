# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which Geofabrik file a region and a freshness mode mean, and how it is
verified.  D-057.

Measured 2026-09-27: Geofabrik keeps a dated extract every 1 January, the
1st of the last three months, and the last seven days; ``-latest`` is a 302
to today's dated file; every file has a ``.md5`` beside it. A pinned region
is verified by the sha256 the catalog carries; anything else by Geofabrik's
MD5, and the plan says which, per region, every time.

Pure apart from the injected :class:`Probe`, so every branch is testable
without the network.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import yaml

from hammunition.resolution import CatalogueMiss, ResolutionContext

BASE = "https://download.geofabrik.de"
PINNED = "sha256, pinned by Hammunition"
UNPINNED = "MD5 from Geofabrik only; not pinned"
_MD5 = re.compile(r"([0-9a-f]{32})\s+\S+\s*")
_REGION_ID = re.compile(rf"{re.escape(BASE)}/(.+)-latest\.osm\.pbf")


class GeofabrikError(Exception):
    """A region could not be resolved to a verifiable file."""


@dataclass(frozen=True)
class Pin:
    region: str
    snapshot: str
    size: int
    sha256: str


@dataclass(frozen=True)
class RegionFile:
    region: str
    snapshot: str
    url: str
    size: int
    sha256: str | None
    md5: str | None

    @property
    def verified_by(self) -> str:
        return PINNED if self.sha256 else UNPINNED

    @property
    def slug(self) -> str:
        return self.region.replace("/", "-")


class Probe(Protocol):
    def head(self, url: str) -> tuple[int, int, str | None]: ...
    def text(self, url: str) -> str: ...


class UrllibProbe:
    """The real :class:`Probe`: HTTPS to Geofabrik via :mod:`urllib`.  The one
    part of this module that touches the network.

    ``head`` is built with no redirect handler and no error processor, so a
    302's status and ``Location`` come back as they are -- that redirect is how
    ``latest`` names its dated file -- and a 404 is a status, not an exception.
    ``text`` follows redirects and refuses a non-2xx answer by name. Built from
    :class:`~urllib.request.OpenerDirector` with only the HTTP handlers, as
    :class:`hammunition.fetch.UrllibTransport` is, so no ``file:`` URL is ever
    served. Nothing fetched here is trusted: sizes and MD5s become what the
    download is checked against, and a wrong one fails that check.
    """

    #: Enough for an ``.md5`` line or Geofabrik's region index; bounded so a
    #: misbehaving server cannot be read into memory without limit.
    MAX_TEXT = 8 * 1024 * 1024

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout
        head = urllib.request.OpenerDirector()
        head.add_handler(urllib.request.HTTPHandler())
        head.add_handler(urllib.request.HTTPSHandler())
        self._head = head
        text = urllib.request.OpenerDirector()
        for handler in (
            urllib.request.HTTPHandler(),
            urllib.request.HTTPSHandler(),
            urllib.request.HTTPRedirectHandler(),
            urllib.request.HTTPErrorProcessor(),
            urllib.request.HTTPDefaultErrorHandler(),
        ):
            text.add_handler(handler)
        self._text = text

    @staticmethod
    def _checked(url: str) -> urllib.request.Request:
        if not url.startswith(BASE + "/"):
            raise GeofabrikError(f"refusing {url!r}: only {BASE} is asked about map regions")
        return urllib.request.Request(url, headers={"User-Agent": "hammunition"})

    def head(self, url: str) -> tuple[int, int, str | None]:
        request = self._checked(url)
        request.method = "HEAD"
        try:
            response = self._head.open(request, timeout=self.timeout)
        except (urllib.error.URLError, OSError) as exc:
            raise GeofabrikError(f"{url} could not be reached: {exc}") from exc
        if response is None:  # pragma: no cover - no handler claimed the scheme
            raise GeofabrikError(f"no handler would ask {url!r}")
        with response:
            length = response.headers.get("Content-Length")
            size = int(length) if length and length.isdigit() else 0
            return response.status, size, response.headers.get("Location")

    def text(self, url: str) -> str:
        request = self._checked(url)
        try:
            response = self._text.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise GeofabrikError(f"{url} returned HTTP {exc.code} ({exc.reason})") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GeofabrikError(f"{url} could not be fetched: {exc}") from exc
        if response is None:  # pragma: no cover
            raise GeofabrikError(f"no handler would fetch {url!r}")
        with response:
            body: bytes = response.read(self.MAX_TEXT + 1)
        if len(body) > self.MAX_TEXT:
            raise GeofabrikError(f"{url} is larger than {self.MAX_TEXT} bytes; refusing to read it")
        return body.decode("utf-8", errors="replace")


def snapshot_for(freshness: str, today: date) -> str:
    if freshness == "yearly":
        return f"{today:%y}0101"
    if freshness == "monthly":
        return f"{today:%y%m}01"
    raise GeofabrikError(f"{freshness!r} has no fixed snapshot")


def _previous(freshness: str, snapshot: str) -> str:
    # Real date arithmetic, not string slicing: a two-digit year of '00' is
    # 2000, and 2000 - 1 must be 1999 ('99'), not the digit -1.
    when = date(2000 + int(snapshot[:2]), int(snapshot[2:4]), int(snapshot[4:6]))
    if freshness == "yearly":
        previous = when.replace(year=when.year - 1)
    elif when.month == 1:
        previous = when.replace(year=when.year - 1, month=12)
    else:
        previous = when.replace(month=when.month - 1)
    return f"{previous:%y%m%d}"


def _url(region: str, snapshot: str) -> str:
    # `region` is interpolated as given; callers must pass an
    # already-validated region (station config validates it) since the host
    # is fixed and a bad region only ever 404s here, never anything worse.
    return f"{BASE}/{region}-{snapshot}.osm.pbf"


def _md5(probe: Probe, url: str) -> str:
    body = probe.text(url + ".md5").strip()
    match = _MD5.fullmatch(body)
    if match is None:
        raise GeofabrikError(f"{url}.md5 is not an md5 line: {body[:60]!r}")
    return match.group(1)


def _resolve_online(
    region: str,
    freshness: str,
    *,
    today: date,
    pins: Mapping[tuple[str, str], Pin],
    probe: Probe,
) -> RegionFile:
    if freshness == "latest":
        status, _, location = probe.head(f"{BASE}/{region}-latest.osm.pbf")
        found = re.search(r"-(\d{6})\.osm\.pbf$", location or "")
        if status not in (301, 302, 303, 307, 308) or found is None:
            raise GeofabrikError(
                f"{region}-latest.osm.pbf did not redirect to a dated file (HTTP {status}); "
                f"is {region!r} a Geofabrik region? `hammunition maps regions` lists them."
            )
        candidates = [found.group(1)]
    else:
        first = snapshot_for(freshness, today)
        candidates = [first, _previous(freshness, first)]

    for snapshot in candidates:
        pin = pins.get((region, snapshot))
        if pin is not None:
            return RegionFile(region, snapshot, _url(region, snapshot), pin.size, pin.sha256, None)
        url = _url(region, snapshot)
        status, size, _ = probe.head(url)
        if status == 200:
            if size <= 0:
                # No Content-Length: the size the fetch is capped and checked
                # by would be 0, and the failure would surface as a cap hit
                # partway through a download. Refused here, naming the file.
                raise GeofabrikError(
                    f"{region}-{snapshot}.osm.pbf: Geofabrik's server reported no size "
                    f"for it, so the download cannot be bounded or checked; try again later"
                )
            return RegionFile(region, snapshot, url, size, None, _md5(probe, url))
    raise GeofabrikError(
        f"no {freshness} extract for {region!r}: tried "
        + ", ".join(f"{region}-{s}.osm.pbf" for s in candidates)
        + ". Check the region with `hammunition maps regions`, and the machine's clock."
    )


def recorded_region(
    region: str,
    freshness: str,
    *,
    today: date,
    pins: Mapping[tuple[str, str], Pin],
    context: ResolutionContext,
) -> RegionFile:
    if freshness != "latest":
        first = snapshot_for(freshness, today)
        for snapshot in (first, _previous(freshness, first)):
            pin = pins.get((region, snapshot))
            if pin is not None:
                context.require_payload("osm-regions", region, sha256=pin.sha256, size=pin.size)
                return RegionFile(
                    region, snapshot, _url(region, snapshot), pin.size, pin.sha256, None
                )
    row = context.entry("osm-regions", region)
    match = re.fullmatch(
        re.escape(region.rsplit("/", 1)[-1]) + r"-([0-9]{6})\.osm\.pbf", row.publisher_name or ""
    )
    if match is None or row.publisher_size is None or row.publisher_size <= 0:
        raise CatalogueMiss(
            f"osm-regions/{region}: publisher_name/publisher_size do not name a dated extract"
        )
    snapshot = match.group(1)
    if freshness != "latest":
        first = snapshot_for(freshness, today)
        if snapshot not in (first, _previous(freshness, first)):
            raise CatalogueMiss(f"osm-regions/{region}: Bunker has no {freshness} candidate")
    url = _url(region, snapshot)
    if row.publisher_url != url or row.size != row.publisher_size:
        raise CatalogueMiss(f"osm-regions/{region}: publisher URL/size disagree with dated name")
    pin = pins.get((region, snapshot))
    if pin is not None:
        context.require_payload("osm-regions", region, sha256=pin.sha256, size=pin.size)
        return RegionFile(region, snapshot, url, pin.size, pin.sha256, None)
    if (
        row.publisher_check not in ("md5", "md5-publisher")
        or re.fullmatch(r"[0-9a-f]{32}", row.publisher_digest or "") is None
    ):
        raise CatalogueMiss(f"osm-regions/{region}: publisher_digest is not MD5")
    context.require_payload(
        "osm-regions",
        region,
        sha256=row.sha256,
        size=row.publisher_size,
        publisher_digest=row.publisher_digest,
    )
    return RegionFile(region, snapshot, url, row.publisher_size, None, row.publisher_digest)


def resolve(
    region: str,
    freshness: str,
    *,
    today: date,
    pins: Mapping[tuple[str, str], Pin],
    probe: Probe,
    context: ResolutionContext | None = None,
) -> RegionFile:
    def online() -> RegionFile:
        return _resolve_online(region, freshness, today=today, pins=pins, probe=probe)

    if context is None:
        return online()
    return context.choose(
        "osm-regions",
        region,
        online,
        lambda: recorded_region(region, freshness, today=today, pins=pins, context=context),
    )


def load_pins(path: Path) -> dict[tuple[str, str], Pin]:
    data = yaml.safe_load(path.read_text()) or {}
    pins: dict[tuple[str, str], Pin] = {}
    for row in data.get("pins", []):
        pin = Pin(str(row["region"]), str(row["snapshot"]), int(row["size"]), str(row["sha256"]))
        pins[(pin.region, pin.snapshot)] = pin
    return pins


def region_ids(index_json: str) -> list[str]:
    """Every region path Geofabrik's region index names, sorted and
    deduplicated.

    Pure: takes the index's raw JSON text, never fetches it -- ``hammunition
    maps regions`` reads it once through :class:`Probe.text`, from
    ``index-v1-nogeom.json`` (0.51 MB, measured 2026-09-28) rather than the
    3.79 MB ``index-v1.json``: both carry the same
    ``properties.urls.pbf`` shape this function reads, and the nogeom one
    just drops the ``geometry`` key neither this function nor `maps regions`
    uses. Each feature's ``properties.urls.pbf`` is the region's
    ``-latest.osm.pbf`` URL; the region id is the path between :data:`BASE`
    and that suffix, exactly what :func:`resolve` and station config's
    ``map_regions`` take.

    Fails loudly (fix round 1, M6): a feature whose ``properties`` or
    ``urls`` is null or missing is a shape Geofabrik has never published,
    and raising :class:`GeofabrikError` here -- naming the problem -- beats
    an ``AttributeError`` from calling ``.get`` on ``None``, which is what a
    bare ``feature.get("properties", {}).get("urls", {})`` chain did before:
    the ``{}`` default only ever fires when the key is *absent*, never when
    it is present and ``null``. A feature whose ``urls`` exists but simply
    has no ``pbf`` key is not malformed -- some Geofabrik entries offer no
    ``.osm.pbf`` -- and is skipped, not raised.
    """
    try:
        data = json.loads(index_json)
    except json.JSONDecodeError as exc:
        raise GeofabrikError(f"Geofabrik's index is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GeofabrikError("Geofabrik's index is not a JSON object")
    ids: set[str] = set()
    for feature in data.get("features", []):
        if not isinstance(feature, dict):
            raise GeofabrikError(f"a feature in Geofabrik's index is not an object: {feature!r}")
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise GeofabrikError(
                f"a feature in Geofabrik's index has no properties object: {feature!r}"
            )
        urls = properties.get("urls")
        if not isinstance(urls, dict):
            raise GeofabrikError(
                f"{properties.get('id', '?')!r} in Geofabrik's index has no urls object"
            )
        pbf = urls.get("pbf")
        if not isinstance(pbf, str):
            continue
        match = _REGION_ID.fullmatch(pbf)
        if match is not None:
            ids.add(match.group(1))
    return sorted(ids)


def newest_snapshots(pins: Mapping[tuple[str, str], Pin]) -> dict[str, str]:
    """The newest pinned snapshot for each region, keyed by slug.

    Slug-keyed because a slug is what ``update`` reads back from an
    installed region's ``.source`` sidecar
    (:func:`hammunition.backends.regions.installed_slugs`); comparing by
    slug is how it learns a newer map is pinned without asking the network.
    Snapshots are fixed-width ``YYMMDD``, so the newest compares as the
    greatest string.
    """
    newest: dict[str, str] = {}
    for pin in pins.values():
        slug = pin.region.replace("/", "-")
        if pin.snapshot > newest.get(slug, ""):
            newest[slug] = pin.snapshot
    return newest


def current_pinned_snapshots(
    freshness: str,
    today: date,
    pins: Mapping[tuple[str, str], Pin],
    regions: Iterable[str],
) -> dict[str, str]:
    """The pinned snapshot *freshness* would resolve *regions* to today,
    keyed by slug, restricted to what the pin list actually carries.

    Fix round 1 (7+9), I1: comparing an installed region against
    :func:`newest_snapshots` -- the newest pin of *any* snapshot -- reported
    a yearly install at ``260101`` behind a ``260901`` pin forever, because a
    yearly install never resolves to a monthly-shaped snapshot and so never
    clears it. This asks the same question :func:`resolve` asks: the
    snapshot ``freshness`` names for *today* (:func:`snapshot_for`), falling
    back one period when that snapshot has no pin yet, exactly
    :func:`resolve`'s own fallback. A region whose resolved-today snapshot
    (nor its one-period-back fallback) has no pin compares against nothing --
    it is unpinned today, verified by Geofabrik's MD5 only, and `update` has
    no pinned opinion on it.

    ``latest`` has no fixed-format snapshot to resolve -- Geofabrik's
    ``-latest`` redirect names whatever file is newest, arbitrarily, so
    there is no "today's" candidate to compute -- and every region's newest
    pinned snapshot (:func:`newest_snapshots`) stands in instead, per
    region, since different regions can have different newest pins.
    """
    if freshness == "latest":
        newest = newest_snapshots(pins)
        return {
            slug: newest[slug] for region in regions if (slug := region.replace("/", "-")) in newest
        }
    first = snapshot_for(freshness, today)
    previous = _previous(freshness, first)
    out: dict[str, str] = {}
    for region in regions:
        for snapshot in (first, previous):
            if (region, snapshot) in pins:
                out[region.replace("/", "-")] = snapshot
                break
    return out


_ISO_ALPHA2 = re.compile(r"[A-Z]{2}")


def _codes_of(properties: Mapping[str, object]) -> tuple[str, ...]:
    """A feature's own country codes: ``iso3166-1:alpha2``, then the country
    prefix of each ``iso3166-2`` (``US-VT`` -> ``US``), first seen first."""
    found: list[str] = []
    for key in ("iso3166-1:alpha2", "iso3166-2"):
        values = properties.get(key) or []
        if not isinstance(values, list):
            raise GeofabrikError(f"{properties.get('id', '?')!r}: {key} is not a list")
        for value in values:
            code = str(value).split("-", 1)[0] if key == "iso3166-2" else str(value)
            if not _ISO_ALPHA2.fullmatch(code):
                raise GeofabrikError(
                    f"{properties.get('id', '?')!r}: {key} holds {value!r}, which does not "
                    f"name an ISO 3166-1 alpha-2 country"
                )
            if code not in found:
                found.append(code)
    return tuple(found)


def countries_from_index(index_json: str) -> dict[str, tuple[str, ...]]:
    """Region path -> the ISO 3166-1 alpha-2 countries it lies in, from
    Geofabrik's region index.  The address-search fix (D-057 amendment).

    Pure, like :func:`region_ids`: ``scripts/gen_geofabrik_countries.py``
    reads the index once and writes the result to
    ``catalog/data/geofabrik-countries.yaml``, which the engine reads at plan
    time with no network.

    A region's codes are its own ``iso3166-1:alpha2`` list plus the country
    prefix of each ``iso3166-2`` code (``US-VT`` is the US). A region with
    neither -- Bayern, an English county, most of what sits below a country
    -- takes its nearest ancestor's through ``parent``. A region with no
    country anywhere above it (a continent, a group such as ``dach`` or
    ``us-northeast``) is left out, and its conversion merges no boundary.
    """
    try:
        data = json.loads(index_json)
    except json.JSONDecodeError as exc:
        raise GeofabrikError(f"Geofabrik's index is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GeofabrikError("Geofabrik's index is not a JSON object")
    own: dict[str, tuple[str, ...]] = {}
    parent: dict[str, str] = {}
    path: dict[str, str] = {}
    for feature in data.get("features", []):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict) or not isinstance(properties.get("id"), str):
            raise GeofabrikError(f"a feature in Geofabrik's index has no id: {feature!r}")
        fid = properties["id"]
        own[fid] = _codes_of(properties)
        if isinstance(properties.get("parent"), str):
            parent[fid] = properties["parent"]
        urls = properties.get("urls")
        pbf = urls.get("pbf") if isinstance(urls, dict) else None
        match = _REGION_ID.fullmatch(pbf) if isinstance(pbf, str) else None
        if match is not None:
            path[fid] = match.group(1)
    out: dict[str, tuple[str, ...]] = {}
    for fid, region in path.items():
        seen: list[str] = []
        at: str | None = fid
        codes: tuple[str, ...] = ()
        while at is not None and at in own:
            if at in seen:
                raise GeofabrikError(
                    f"Geofabrik's index has a parent cycle: {' -> '.join([*seen, at])}"
                )
            seen.append(at)
            if own[at]:
                codes = own[at]
                break
            at = parent.get(at)
        if codes:
            out[region] = codes
    return dict(sorted(out.items()))


def load_countries(path: Path) -> dict[str, tuple[str, ...]]:
    """``catalog/data/geofabrik-countries.yaml``, validated: region path ->
    ISO 3166-1 alpha-2 codes. Refused by name when a row is malformed, never
    read as an empty table."""
    data = yaml.safe_load(path.read_text()) or {}
    regions = data.get("regions") if isinstance(data, dict) else None
    if not isinstance(regions, dict):
        raise GeofabrikError(f"{path} has no `regions:` mapping")
    table: dict[str, tuple[str, ...]] = {}
    for region, codes in regions.items():
        if not (
            isinstance(codes, list)
            and codes
            and all(isinstance(c, str) and _ISO_ALPHA2.fullmatch(c) for c in codes)
        ):
            raise GeofabrikError(
                f"{path}: {region}: {codes!r} is not a list of ISO 3166-1 alpha-2 codes"
            )
        table[str(region)] = tuple(codes)
    return table
