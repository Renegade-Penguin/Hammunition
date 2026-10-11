# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every remote data artifact the engine would fetch for a selection, with
no station and no install.  D-070.

``hammunition artifacts`` is Hammunition Bunker's one source of what to
mirror, so this resolves exactly as the plan does -- the same Geofabrik pins,
freshness and fallback (:func:`hammunition.geofabrik.resolve`), the same
outlines, tile list and tile pins (:mod:`hammunition.copernicus`), the same
Kiwix book list and book pins (:func:`hammunition.kiwix.resolve_books`) --
with three differences, each on purpose:

- the selection (map regions, reference books) is given, never read from
  station config;
- nothing installed on this machine is read, so the listing is the same on
  every machine (the plan skips what is installed; a mirror wants it all);
- a region or tile that cannot be resolved is an entry saying why, not a
  refusal of the whole listing.

Task 16 (A10) extends this past `data`/`osm-regions`/`dem-tiles`/
`mwm-regions`/`kiwix-books`/`register`: every unit whose payload comes from a
pinned archive -- `source`, `binary`, `venv`, `node`, `git` and a `derived`
block's converter tool -- plus US Topo and FSTopo sheets (both `topo-quads`
providers) are now part of the default selection, through
:func:`payload_entries` and the region-bound sheet/tile selectors below. Two
further, separate arrays answer what `artifacts` could not before:
:func:`list_inputs` is the exact selection text (a region's outline and its
four recorded selections) a Bunker writes for the engine's own offline
fallback (D-077's run logs and this module's module docstring do not cover
it; see :mod:`hammunition.resolution`), and :func:`list_git_pins` is every
`git` block's pinned revision, for the Bunker to mirror as a verified bundle
(:mod:`hammunition.gitbundles`). Neither reads station config, and neither
clones or fetches anything over the network: a git bundle's bytes, and a
recursive submodule's, are the Bunker's own writer's job, never this
module's.

Pure apart from the injected probes.
"""

from __future__ import annotations

import hashlib
import http.client
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Protocol

from . import acma
from .backends.data import data_name
from .backends.dem import RegionTiles
from .backends.dem import render_record as render_tiles
from .backends.fstopo import render_record as render_sheets
from .backends.kiwix import book_mirror_path
from .backends.topo import render_record as render_quads
from .comaps import ComapsError, load_pins, resolve_regions, sha1_hex
from .copernicus import (
    CopernicusError,
    TileProbe,
    load_tile_list,
    parse_poly,
    resolve_tile,
    select,
    squares_touching,
)
from .copernicus import load_pins as load_tile_pins
from .fstopo import FsPin, FsQuad, FstopoError, GatewayProbe
from .fstopo import load_index as load_fstopo_index
from .fstopo import load_pins as load_fstopo_pins
from .geofabrik import BASE, GeofabrikError, Probe
from .geofabrik import load_pins as load_region_pins
from .geofabrik import resolve as resolve_region
from .gitbundles import bundle_name
from .interface.artifacts import ArtifactEntry, GitPinEntry, InputEntry
from .kiwix import KiwixError, load_book_list, load_pin_file, resolve_books
from .manifest.schema import (
    COMMIT_SHA,
    BinaryInstall,
    DataInstall,
    DemTilesInstall,
    DerivedDataInstall,
    GitInstall,
    KiwixBooksInstall,
    MwmRegionsInstall,
    NodeInstall,
    PackageManifest,
    RegionalDataInstall,
    RegisterInstall,
    RemoteArtifact,
    SourceInstall,
    TopoQuadsInstall,
    VenvInstall,
)
from .payloads import payload_name
from .repeater_sources import SNAPSHOT_CHECK, SNAPSHOT_UNIT, snapshots
from .terrain_plan import PINS as TILE_PINS
from .terrain_plan import (
    THREEDEP_LIST,
    TILE_LIST,
    region_bare_earth,
    region_tiles,
    resolve_bare_earth,
)
from .topo_bound import ALL, TopoBound
from .topo_plan import FSTOPO_INDEX, FSTOPO_PINS, MemoProbe, region_quads, region_sheets
from .topo_plan import INDEX as USTOPO_INDEX
from .usgs3dep import TileRow
from .usgs3dep import load_tile_list as load_3dep_list
from .usgs3dep import tile_url as threedep_url
from .ustopo import Quad, UstopoError
from .ustopo import load_index as load_ustopo_index

__all__ = [
    "SelectionError",
    "SnapshotProbe",
    "etag_entry",
    "fetching_units",
    "input_entry",
    "input_regions",
    "list_artifacts",
    "list_git_pins",
    "list_inputs",
    "payload_entries",
    "select_units",
]

#: Payload-pin variants (Task 16): the install methods whose unit fetches a
#: catalog-pinned archive, besides `data`. `_blocks` admits a `DerivedDataInstall`
#: only when it has a `tool` (otherwise it builds from what another unit already
#: fetched) and admits every `GitInstall` unconditionally -- a git block is
#: fetching for its bundle even with no `extra_files` (:func:`list_git_pins`).
PAYLOAD = (
    SourceInstall,
    BinaryInstall,
    VenvInstall,
    NodeInstall,
    GitInstall,
    DerivedDataInstall,
)
FETCHING = (
    DataInstall,
    RegionalDataInstall,
    DemTilesInstall,
    MwmRegionsInstall,
    KiwixBooksInstall,
    RegisterInstall,
    TopoQuadsInstall,
    *PAYLOAD,
)
Block = (
    DataInstall
    | RegionalDataInstall
    | DemTilesInstall
    | MwmRegionsInstall
    | KiwixBooksInstall
    | RegisterInstall
    | TopoQuadsInstall
    | SourceInstall
    | BinaryInstall
    | VenvInstall
    | NodeInstall
    | GitInstall
    | DerivedDataInstall
)
PayloadBlock = (
    SourceInstall | BinaryInstall | VenvInstall | NodeInstall | GitInstall | DerivedDataInstall
)
REGION_PINS = Path("data") / "geofabrik-pins.yaml"
NO_REGIONS = (
    "no map regions given: pass --map-regions with Geofabrik region paths "
    "(`hammunition maps regions` lists them)"
)
NO_BOOKS = (
    "no books selected: pass --reference-books with Kiwix book ids "
    "(`hammunition reference books` lists them)"
)
#: The books do not share a licence (D-066); each listed book carries its
#: own line, and an entry covering the whole unit says where they are.
BOOKS_LICENCE = "each book's own, from catalog/data/kiwix-books.yaml"


class RegisterProbe(Protocol):
    """The day's size of a ``register`` file (:class:`hammunition.acma.AcmaProbe`)."""

    def size(self, url: str) -> int: ...


class SnapshotProbe(Protocol):
    """The size a publisher's ``HEAD`` reports for an on-request list, or
    ``None`` when it states none (:class:`SnapshotHead`)."""

    def size(self, url: str) -> int | None: ...


class SelectionError(Exception):
    """The selection names something that cannot be listed."""


def _blocks(manifest: PackageManifest) -> list[Block]:
    out: list[Block] = []
    for entry in manifest.install:
        block = entry.install
        if isinstance(block, DerivedDataInstall):
            if block.tool is not None:
                out.append(block)
            continue
        if isinstance(block, FETCHING):
            out.append(block)
    return out


def fetching_units(catalog: Mapping[str, PackageManifest]) -> tuple[str, ...]:
    """Every unit with a ``data``, ``osm-regions``, ``dem-tiles``,
    ``mwm-regions``, ``kiwix-books``, ``register`` or ``topo-quads`` block, or
    a ``source``, ``binary``, ``venv``, ``node`` or ``git`` install block, or a
    ``derived`` block naming a converter ``tool`` (Task 16), sorted."""
    return tuple(sorted(name for name, m in catalog.items() if _blocks(m)))


def select_units(
    catalog: Mapping[str, PackageManifest], requested: Sequence[str]
) -> tuple[str, ...]:
    """*requested* in order, once each, or every fetching unit when empty,
    then ``repeater-snapshots`` (D-078: not a catalog unit, the three
    on-request repeater lists a Bunker may hold). A name not in the catalog,
    or naming a unit that fetches no data, is refused by name: the operator
    typed it (D-039)."""
    if not requested:
        return (*fetching_units(catalog), SNAPSHOT_UNIT)
    unknown = [n for n in requested if n not in catalog and n != SNAPSHOT_UNIT]
    idle = [n for n in requested if n in catalog and not _blocks(catalog[n])]
    problems = []
    if unknown:
        problems.append(f"not in the catalog: {', '.join(unknown)}")
    if idle:
        problems.append(
            f"fetches no remote data artifact (not a data, osm-regions, dem-tiles, "
            f"mwm-regions, kiwix-books, register or topo-quads unit, and no "
            f"source/binary/venv/node/git install block or derived block with a "
            f"converter tool): "
            f"{', '.join(idle)}"
        )
    if problems:
        raise SelectionError("; ".join(problems))
    return tuple(dict.fromkeys(requested))


def _licence(text: str) -> str:
    return " ".join(text.split())


def _deferred(unit: str, name: str | None, licence: str, reason: str) -> ArtifactEntry:
    return ArtifactEntry(
        unit=unit,
        name=name,
        url=None,
        check=None,
        digest=None,
        checksum_url=None,
        size=None,
        licence=licence,
        deferred=reason,
    )


def _data(unit: str, blocks: Sequence[DataInstall]) -> list[ArtifactEntry]:
    out: dict[str, ArtifactEntry] = {}
    for block in blocks:
        for artifact in block.artifacts:
            name = data_name(artifact)
            entry = ArtifactEntry(
                unit=unit,
                name=name,
                url=artifact.url,
                check="sha256",
                digest=artifact.sha256,
                checksum_url=None,
                size=artifact.size,
                licence=_licence(block.licence),
                deferred=None,
            )
            seen = out.get(name)
            if seen is None:
                out[name] = entry
            elif seen.digest != entry.digest:
                # Two install blocks, one name, different bytes: a mirror has
                # one path for both, so the second cannot be listed.
                out[f"{name}\0{artifact.sha256}"] = _deferred(
                    unit,
                    name,
                    entry.licence,
                    f"another artifact of {unit} has the same name and different bytes "
                    f"({seen.url}); a mirror serves one file per name",
                )
    return list(out.values())


def etag_entry(
    unit: str,
    name: str,
    url: str,
    etag: str,
    size: int,
    licence: str,
    *,
    part_size: int | None = None,
) -> ArtifactEntry:
    """One US Topo or 3DEP artifact, checked by the carried S3 ETag: *etag*
    carried verbatim, quotes and multipart suffix included, never stripped,
    suffixed or rehashed (Task 16; the carried-forward review of Task 12's
    `usgs-ustopo` naming by `quad.path` and `dem-3dep` by `row.name`)."""
    return ArtifactEntry(
        unit=unit,
        name=name,
        url=url,
        check="etag-md5",
        digest=etag,
        checksum_url=None,
        size=size,
        licence=licence,
        deferred=None,
        part_size=part_size,
    )


def payload_entries(
    unit: str,
    block: PayloadBlock,
    licence: str,
) -> tuple[ArtifactEntry, ...]:
    """Every catalog-pinned payload *block* fetches: the source archive, the
    binary or Node artifact, the venv's own payload tree, a derived block's
    converter tool, or a git block's `extra_files` artifacts (Task 16). Never
    the git revision itself -- that is :func:`list_git_pins`'s, a bundle
    rather than a plain fetch. Nothing is invented where the manifest carries
    no size or licence of its own: *licence* (the manifest's own `licence`
    field, D-070) is the fallback a block's own field does not override."""
    items: list[tuple[RemoteArtifact, int | None, str]] = []
    if isinstance(block, SourceInstall):
        items.append((block.source, None, licence))
    elif isinstance(block, BinaryInstall | NodeInstall):
        items.append((block.artifact, None, licence))
    elif isinstance(block, VenvInstall) and block.payload is not None:
        items.append((block.payload, None, block.licence or licence))
    elif isinstance(block, DerivedDataInstall) and block.tool is not None:
        items.append((block.tool.artifact, block.tool.size, block.tool.licence))
    elif isinstance(block, GitInstall):
        for extra in block.extra_files:
            if extra.artifact is not None:
                items.append(
                    (
                        RemoteArtifact(url=extra.artifact.url, sha256=extra.artifact.sha256),
                        extra.artifact.size,
                        licence,
                    )
                )
    return tuple(
        ArtifactEntry(
            unit=unit,
            name=payload_name(pin),
            url=pin.url,
            check="sha256",
            digest=pin.sha256,
            checksum_url=None,
            size=size,
            licence=item_licence,
            deferred=None,
        )
        for pin, size, item_licence in items
    )


def _payloads(unit: str, blocks: Sequence[PayloadBlock], licence: str) -> list[ArtifactEntry]:
    """Every *blocks* payload for *unit*, deduplicated by name, a conflicting
    name (two blocks, same name, different bytes) refused the way `_data`
    refuses one (a mirror serves one file per name) -- an arch-conditional
    unit may carry one payload block per arch."""
    out: dict[str, ArtifactEntry] = {}
    for block in blocks:
        for entry in payload_entries(unit, block, licence):
            name = entry.name
            assert name is not None
            seen = out.get(name)
            if seen is None:
                out[name] = entry
            elif seen.digest != entry.digest:
                out[f"{name}\0{entry.digest}"] = _deferred(
                    unit,
                    name,
                    entry.licence,
                    f"another artifact of {unit} has the same name and different bytes "
                    f"({seen.url}); a mirror serves one file per name",
                )
    return list(out.values())


def _regions(
    unit: str,
    licence: str,
    regions: Sequence[str],
    freshness: str,
    *,
    today: date,
    catalog_root: Path,
    probe: Probe,
) -> list[ArtifactEntry]:
    pins_path = catalog_root / REGION_PINS
    pins = load_region_pins(pins_path) if pins_path.is_file() else {}
    out: list[ArtifactEntry] = []
    for region in regions:
        try:
            found = resolve_region(region, freshness, today=today, pins=pins, probe=probe)
        except (GeofabrikError, OSError) as exc:
            out.append(_deferred(unit, region, licence, str(exc)))
            continue
        out.append(
            ArtifactEntry(
                unit=unit,
                name=region,
                url=found.url,
                check="sha256" if found.sha256 else "md5-publisher",
                digest=found.sha256 or found.md5,
                checksum_url=None if found.sha256 else f"{found.url}.md5",
                size=found.size,
                licence=licence,
                deferred=None,
            )
        )
    return out


def _register(unit: str, block: RegisterInstall, probe: RegisterProbe | None) -> ArtifactEntry:
    """The ACMA register (D-074, amended 2026-10-01): its URL, its day's size
    from a ``HEAD``, and no digest, because none is published. A mirror
    serves it at ``acma-register/spectra_rrl.zip`` and the engine checks the
    mirror's copy as it checks the publisher's: by the zip's own structure."""
    licence = _licence(block.licence)
    if probe is None:
        return _deferred(unit, acma.FILE_NAME, licence, "the day's size was not asked for")
    try:
        size = probe.size(acma.URL)
    except (acma.AcmaError, OSError) as exc:
        return _deferred(unit, acma.FILE_NAME, licence, f"its size could not be read: {exc}")
    return ArtifactEntry(
        unit=unit,
        name=acma.FILE_NAME,
        url=acma.URL,
        check=acma.CHECK,
        digest=None,
        checksum_url=None,
        size=size,
        licence=licence,
        deferred=None,
    )


def _snapshots(probe: SnapshotProbe | None) -> list[ArtifactEntry]:
    """The on-request repeater lists (D-064, D-074; listed by D-078): each the
    publisher's URL, no digest (none is published and the list changes under
    the URL), the size from one ``HEAD`` when the server states it, and the
    licence position. A Bunker with ``hold_unverified`` keeps one for its LAN
    and the ``fetch-*`` command reads it first at ``repeater-snapshots/<name>``.
    The check is size and date only."""
    out: list[ArtifactEntry] = []
    for snap in snapshots():
        size: int | None = None
        if probe is None:
            out.append(
                _deferred(SNAPSHOT_UNIT, snap.name, snap.position, "the size was not asked for")
            )
            continue
        try:
            size = probe.size(snap.url)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            out.append(
                _deferred(
                    SNAPSHOT_UNIT, snap.name, snap.position, f"its size could not be read: {exc}"
                )
            )
            continue
        out.append(
            ArtifactEntry(
                unit=SNAPSHOT_UNIT,
                name=snap.name,
                url=snap.url,
                check=SNAPSHOT_CHECK,
                digest=None,
                checksum_url=None,
                size=size,
                licence=snap.position,
                deferred=None,
            )
        )
    return out


def _tiles(
    unit: str,
    licence: str,
    regions: Sequence[str],
    *,
    catalog_root: Path,
    region_probe: Probe,
    tile_probe: TileProbe,
) -> list[ArtifactEntry]:
    try:
        tile_list = load_tile_list(catalog_root / TILE_LIST)
        pins_path = catalog_root / TILE_PINS
        pins = load_tile_pins(pins_path) if pins_path.is_file() else {}
    except (CopernicusError, OSError) as exc:
        return [_deferred(unit, None, licence, f"the carried tile data cannot be read: {exc}")]
    out: list[ArtifactEntry] = []
    wanted: set[str] = set()
    for region in regions:
        try:
            outer, holes = parse_poly(region_probe.text(f"{BASE}/{region}.poly"))
            tiles, _unpublished = select(squares_touching(outer, holes), tile_list)
        except (GeofabrikError, CopernicusError, OSError) as exc:
            out.append(_deferred(unit, region, licence, f"its outline could not be read: {exc}"))
            continue
        wanted.update(tiles)
    for name in sorted(wanted):
        try:
            tile = resolve_tile(name, pins=pins, probe=tile_probe)
        except (CopernicusError, OSError) as exc:
            out.append(_deferred(unit, name, licence, str(exc)))
            continue
        out.append(
            ArtifactEntry(
                unit=unit,
                name=name,
                url=tile.url,
                check="sha256" if tile.sha256 else "etag-md5",
                digest=tile.sha256 or tile.md5,
                checksum_url=None if tile.sha256 else tile.url,
                size=tile.size,
                licence=licence,
                deferred=None,
            )
        )
    return out


def _bare_earth(
    unit: str,
    licence: str,
    regions: Sequence[str],
    *,
    catalog_root: Path,
    region_probe: Probe,
    bound: TopoBound,
) -> list[ArtifactEntry]:
    """USGS 3DEP tiles for *regions* under *bound* (Task 16, D-068 amended
    2026-10-01): each region's outline, selected against the carried list by
    :func:`hammunition.terrain_plan.region_bare_earth` (installed=None: no
    record is read), then each distinct tile's carried size and S3 ETag,
    named by the carried row's own name (the carried-forward Task 12 review)."""
    try:
        tiles = load_3dep_list(catalog_root / THREEDEP_LIST)
    except (CopernicusError, OSError) as exc:
        return [_deferred(unit, None, licence, f"the carried 3DEP tile list cannot be read: {exc}")]
    out: list[ArtifactEntry] = []
    wanted: set[str] = set()
    for region in regions:
        slug = region.replace("/", "-")
        try:
            entry = region_bare_earth(
                region, slug, installed=None, tiles=tiles, region_probe=region_probe, bound=bound
            )
        except (GeofabrikError, CopernicusError, OSError) as exc:
            out.append(_deferred(unit, region, licence, f"its outline could not be read: {exc}"))
            continue
        wanted.update(entry.tiles)
    for name in sorted(wanted):
        row: TileRow | None = tiles.get(name)
        if row is None:
            out.append(
                _deferred(
                    unit,
                    name,
                    licence,
                    "recorded for a region but no longer in the carried 3DEP list; "
                    "scripts/gen_3dep_tiles.py --fetch regenerates it",
                )
            )
            continue
        out.append(etag_entry(unit, row.name, threedep_url(row.name), row.etag, row.size, licence))
    return out


def _topo_quads(
    unit: str,
    block: TopoQuadsInstall,
    regions: Sequence[str],
    *,
    catalog_root: Path,
    region_probe: Probe,
    bound: TopoBound,
) -> list[ArtifactEntry]:
    """US Topo sheets for *regions* under *bound* (Task 16, D-068): each
    region selected with :func:`hammunition.topo_plan.region_quads` against
    the carried index, then each distinct sheet's carried size and S3 ETag,
    named by `quad.path` (the carried-forward Task 12 review)."""
    licence = block.licence
    try:
        index = load_ustopo_index(catalog_root / USTOPO_INDEX)
    except (UstopoError, OSError) as exc:
        return [_deferred(unit, None, licence, f"the carried US Topo index cannot be read: {exc}")]
    out: list[ArtifactEntry] = []
    wanted: dict[str, Quad] = {}
    notes: list[str] = []
    for region in regions:
        slug = region.replace("/", "-")
        try:
            entry = region_quads(
                region,
                slug,
                installed=None,
                index=index,
                probe=region_probe,
                notes=notes,
                bound=bound,
            )
        except (GeofabrikError, CopernicusError, OSError) as exc:
            out.append(_deferred(unit, region, licence, f"its outline could not be read: {exc}"))
            continue
        for quad in entry.quads:
            wanted[quad.path] = quad
    for path in sorted(wanted):
        quad = wanted[path]
        out.append(etag_entry(unit, quad.path, quad.url, quad.etag, quad.size, licence))
    return out


def _fstopo_quads(
    unit: str,
    block: TopoQuadsInstall,
    regions: Sequence[str],
    *,
    catalog_root: Path,
    region_probe: Probe,
    gateway: GatewayProbe | None,
    bound: TopoBound,
) -> list[ArtifactEntry]:
    """Forest Service FSTopo sheets for *regions* under *bound* (Task 16,
    D-068 amended 2026-10-01): each region selected against the carried GTAC
    index, then each distinct sheet located through the gateway -- never
    checked by an ETag, which is Apache's inode-size-mtime, not a content
    digest (:mod:`hammunition.fstopo`). A sheet with a carried pin is
    verified `sha256`; any other is `unverified-fetch`, by name, named by
    `quad.name` (the carried-forward Task 12 review)."""
    licence = block.licence
    try:
        index = load_fstopo_index(catalog_root / FSTOPO_INDEX)
        pins_path = catalog_root / FSTOPO_PINS
        pins: Mapping[int, FsPin] = load_fstopo_pins(pins_path) if pins_path.is_file() else {}
    except (FstopoError, OSError) as exc:
        return [
            _deferred(
                unit, None, licence, f"the carried FSTopo index or pins cannot be read: {exc}"
            )
        ]
    out: list[ArtifactEntry] = []
    wanted: dict[int, FsQuad] = {}
    notes: list[str] = []
    for region in regions:
        slug = region.replace("/", "-")
        try:
            entry = region_sheets(
                region,
                slug,
                installed=None,
                index=index,
                probe=region_probe,
                notes=notes,
                bound=bound,
            )
        except (GeofabrikError, CopernicusError, OSError) as exc:
            out.append(_deferred(unit, region, licence, f"its outline could not be read: {exc}"))
            continue
        for quad in entry.quads:
            wanted[quad.secoord] = quad
    if wanted and gateway is None:
        out.append(_deferred(unit, None, licence, "the Forest Service gateway was not asked for"))
        return out
    for secoord in sorted(wanted):
        quad = wanted[secoord]
        assert gateway is not None
        try:
            url, size = gateway.locate(secoord)
        except (FstopoError, OSError) as exc:
            out.append(_deferred(unit, quad.name, licence, str(exc)))
            continue
        pin = pins.get(secoord)
        if pin is not None:
            out.append(
                ArtifactEntry(unit, quad.name, url, "sha256", pin.sha256, None, size, licence, None)
            )
        else:
            out.append(
                ArtifactEntry(
                    unit, quad.name, url, "unverified-fetch", None, None, size, licence, None
                )
            )
    return out


def _mwm(
    unit: str, licence: str, regions: Sequence[str], *, catalog_root: Path
) -> list[ArtifactEntry]:
    """CoMaps' maps for *regions* (D-069), from the carried pins alone: no
    network. Named ``<version>/<id>.mwm``, the installed layout, so a mirror
    can hold two versions side by side."""
    try:
        pins = load_pins(catalog_root)
    except ComapsError as exc:
        return [_deferred(unit, None, licence, f"the carried map pins cannot be read: {exc}")]
    files, unmapped = resolve_regions(regions, pins)
    out = [
        _deferred(unit, region, licence, "the region table has no CoMaps map for this region")
        for region in unmapped
    ]
    for f in files:
        out.append(
            ArtifactEntry(
                unit=unit,
                name=f"{f.version}/{f.file}",
                url=f.url,
                check="sha1-publisher",
                digest=sha1_hex(f.pin.sha1),
                checksum_url=None,
                size=f.pin.size,
                licence=licence,
                deferred=None,
            )
        )
    return out


def _books(unit: str, books: Sequence[str], *, catalog_root: Path) -> list[ArtifactEntry]:
    """The chosen Kiwix books (D-066), from the carried book list and pins
    alone: no network. Named by book id, the path the books backend asks a
    mirror for (:func:`~hammunition.backends.kiwix.book_mirror_path`). A book
    that cannot be resolved is an entry saying why, by its id."""
    if not books:
        return [_deferred(unit, None, BOOKS_LICENCE, NO_BOOKS)]
    try:
        book_list = load_book_list(catalog_root)
        pins = load_pin_file(catalog_root)
    except KiwixError as exc:
        return [
            _deferred(
                unit, None, BOOKS_LICENCE, f"the carried book list or pins cannot be read: {exc}"
            )
        ]
    out: list[ArtifactEntry] = []
    for book_id in dict.fromkeys(books):
        try:
            (book,) = resolve_books((book_id,), book_list, pins)
        except KiwixError as exc:
            out.append(_deferred(unit, book_id, BOOKS_LICENCE, str(exc)))
            continue
        out.append(
            ArtifactEntry(
                unit=unit,
                name=book_mirror_path(unit, book).name,
                url=book.pin.url,
                check="sha256",
                digest=book.pin.sha256,
                checksum_url=None,
                size=book.pin.size,
                licence=_licence(book.book.licence),
                deferred=None,
            )
        )
    return out


def list_artifacts(
    units: Sequence[str],
    *,
    regions: Sequence[str],
    books: Sequence[str] = (),
    freshness: str,
    catalog: Mapping[str, PackageManifest],
    catalog_root: Path,
    today: date,
    region_probe: Probe,
    tile_probe: TileProbe,
    register_probe: RegisterProbe | None = None,
    snapshot_probe: SnapshotProbe | None = None,
    bound: TopoBound = ALL,
    gateway: GatewayProbe | None = None,
) -> tuple[ArtifactEntry, ...]:
    """Every artifact of *units* for *regions* at *freshness* and the
    reference *books*, in unit order. *bound* (Task 16, issue #232) narrows
    the US Topo, FSTopo and 3DEP selection the way the plan's own does;
    *gateway* answers FSTopo sheets located through the Forest Service's
    raster gateway, and is deferred by name while it is ``None`` and a
    region needs one."""
    out: list[ArtifactEntry] = []
    for unit in units:
        if unit == SNAPSHOT_UNIT:
            out.extend(_snapshots(snapshot_probe))
            continue
        blocks = _blocks(catalog[unit])
        data = [b for b in blocks if isinstance(b, DataInstall)]
        if data:
            out.extend(_data(unit, data))
            continue
        payload_variants = [b for b in blocks if isinstance(b, PAYLOAD)]
        if payload_variants:
            out.extend(_payloads(unit, payload_variants, catalog[unit].licence))
            continue
        block = blocks[0]
        if isinstance(block, RegisterInstall):
            out.append(_register(unit, block, register_probe))
            continue
        if isinstance(block, KiwixBooksInstall):
            out.extend(_books(unit, books, catalog_root=catalog_root))
            continue
        assert isinstance(
            block, RegionalDataInstall | DemTilesInstall | MwmRegionsInstall | TopoQuadsInstall
        )
        licence = _licence(block.licence)
        if not regions:
            out.append(_deferred(unit, None, licence, NO_REGIONS))
        elif isinstance(block, MwmRegionsInstall):
            out.extend(_mwm(unit, licence, regions, catalog_root=catalog_root))
        elif isinstance(block, RegionalDataInstall):
            out.extend(
                _regions(
                    unit,
                    licence,
                    regions,
                    freshness,
                    today=today,
                    catalog_root=catalog_root,
                    probe=region_probe,
                )
            )
        elif isinstance(block, DemTilesInstall) and block.provider == "usgs-3dep":
            out.extend(
                _bare_earth(
                    unit,
                    licence,
                    regions,
                    catalog_root=catalog_root,
                    region_probe=region_probe,
                    bound=bound,
                )
            )
        elif isinstance(block, TopoQuadsInstall) and block.provider == "usfs-fstopo":
            out.extend(
                _fstopo_quads(
                    unit,
                    block,
                    regions,
                    catalog_root=catalog_root,
                    region_probe=region_probe,
                    gateway=gateway,
                    bound=bound,
                )
            )
        elif isinstance(block, TopoQuadsInstall):
            out.extend(
                _topo_quads(
                    unit,
                    block,
                    regions,
                    catalog_root=catalog_root,
                    region_probe=region_probe,
                    bound=bound,
                )
            )
        else:
            out.extend(
                _tiles(
                    unit,
                    licence,
                    regions,
                    catalog_root=catalog_root,
                    region_probe=region_probe,
                    tile_probe=tile_probe,
                )
            )
    return tuple(out)


#: Above this many UTF-8 bytes an inline input is refused outright rather
#: than silently dropped or truncated: a command line, not a file transfer.
MAX_INPUT_BYTES = 8 * 1024 * 1024


def input_entry(kind: str, region: str, name: str, text: str, url: str | None = None) -> InputEntry:
    """One inline selection input: *text* carried verbatim as UTF-8, bounded
    to :data:`MAX_INPUT_BYTES` so a station-wide "all regions" selection does
    not write an unbounded document (Task 16)."""
    body = text.encode("utf-8")
    if len(body) > MAX_INPUT_BYTES:
        raise SelectionError(
            f"input {kind}/{name} is over 8 MiB; narrow the selection with --units"
        )
    return InputEntry(
        kind, region, name, url, hashlib.sha256(body).hexdigest(), len(body), text, None
    )


def input_regions(
    units: Sequence[str], regions: Sequence[str], catalog: Mapping[str, PackageManifest]
) -> tuple[str, ...]:
    """*regions*, deduplicated and in order, when any of *units* is regional
    (needs a Geofabrik outline to choose its payload); empty otherwise, and
    empty for a name :func:`list_artifacts` does not read from the catalog
    (``repeater-snapshots``)."""
    regional = (RegionalDataInstall, DemTilesInstall, TopoQuadsInstall)
    needs_regions = any(
        isinstance(entry.install, regional)
        for unit in units
        if unit in catalog
        for entry in catalog[unit].install
    )
    return tuple(dict.fromkeys(regions)) if needs_regions else ()


def list_inputs(
    regions: Sequence[str], *, catalog_root: Path, probe: Probe, bound: TopoBound = ALL
) -> tuple[InputEntry, ...]:
    """Every selection input a Bunker writes for *regions* (Task 16): each
    region's outline, and the four recorded selections the engine's own
    install-time selectors (:mod:`hammunition.terrain_plan`,
    :mod:`hammunition.topo_plan`) would otherwise read from an installed
    record -- rendered with the same codecs those selectors write, so
    the Bunker's writer and the engine's own offline fallback read the exact
    same bytes. Each outline is fetched once even though up to five selectors
    want it (:class:`~hammunition.topo_plan.MemoProbe`). Malformed or
    unreachable text defers that one input, by kind and region, never a
    zero-byte stand-in."""
    shared = probe if isinstance(probe, MemoProbe) else MemoProbe(probe)
    out: list[InputEntry] = []
    extensions = {
        "region-outline": "poly",
        "tile-selection": "tiles",
        "sheet-selection": "quads",
        "dem3dep-selection": "tiles",
        "fstopo-selection": "quads",
    }
    for region in dict.fromkeys(regions):
        url = f"{BASE}/{region}.poly"
        slug = region.replace("/", "-")

        def outline_record(region: str = region, url: str = url) -> str:
            return shared.text(url)

        def copernicus_record(region: str = region, slug: str = slug) -> str:
            record = region_tiles(
                region,
                slug,
                installed=None,
                tile_list=load_tile_list(catalog_root / TILE_LIST),
                probe=shared,
            )
            return render_tiles(record)

        def threedep_record(region: str = region, slug: str = slug) -> str:
            resolution = resolve_bare_earth(
                ((region, slug),),
                installed=None,
                tiles=load_3dep_list(catalog_root / THREEDEP_LIST),
                region_probe=shared,
                tile_probe=None,
                bound=bound,
                selection_only=True,
            )
            record = (
                resolution.regions[0]
                if resolution.regions
                else RegionTiles(region, slug, (), 0, bound.token)
            )
            return render_tiles(record)

        def topo_record(region: str = region, slug: str = slug) -> str:
            record = region_quads(
                region,
                slug,
                installed=None,
                index=load_ustopo_index(catalog_root / USTOPO_INDEX),
                probe=shared,
                notes=[],
                bound=bound,
            )
            return render_quads(record)

        def fstopo_record(region: str = region, slug: str = slug) -> str:
            record = region_sheets(
                region,
                slug,
                installed=None,
                index=load_fstopo_index(catalog_root / FSTOPO_INDEX),
                probe=shared,
                notes=[],
                bound=bound,
            )
            return render_sheets(record)

        producers: dict[str, Callable[[], str]] = {
            "region-outline": outline_record,
            "tile-selection": copernicus_record,
            "sheet-selection": topo_record,
            "dem3dep-selection": threedep_record,
            "fstopo-selection": fstopo_record,
        }
        for kind, produce in producers.items():
            name = f"{region}.{extensions[kind]}"
            try:
                text = produce()
                if kind == "region-outline":
                    parse_poly(text)  # malformed outlines defer, never an empty record
            except (GeofabrikError, CopernicusError, UstopoError, FstopoError, OSError) as exc:
                out.append(InputEntry(kind, region, name, None, None, None, None, str(exc)))
                continue
            out.append(
                input_entry(kind, region, name, text, url if kind == "region-outline" else None)
            )
    return tuple(out)


def list_git_pins(
    units: Sequence[str], catalog: Mapping[str, PackageManifest]
) -> tuple[GitPinEntry, ...]:
    """Every `git` install block's pinned revision among *units*, for the
    Bunker to mirror as a verified bundle (Task 16, D-024, D-070). A tag with
    no recorded commit (`commit` unset and `ref` not itself a commit) is
    named diagnostically but deferred -- offline bundle verification needs a
    repository pin, not a tag that can move. Recursive submodule bundles are
    not named here: the manifest carries only `submodules: bool`, and the
    Bunker's own writer enumerates the pinned gitlinks through
    :mod:`hammunition.gitbundles` after cloning, never guessed at listing
    time."""
    out: dict[str, GitPinEntry] = {}
    for unit in units:
        if unit not in catalog:
            continue
        for entry in catalog[unit].install:
            block = entry.install
            if not isinstance(block, GitInstall):
                continue
            commit = block.commit or (block.ref if COMMIT_SHA.fullmatch(block.ref) else None)
            if commit is None:
                name = f"{unit}@{block.ref}"
                out[name] = GitPinEntry(
                    "git-bundles",
                    name,
                    block.repo,
                    block.ref,
                    None,
                    block.submodules,
                    catalog[unit].licence,
                    "tag has no recorded commit; offline bundle verification needs a "
                    "repository pin",
                )
            else:
                name = bundle_name(unit, commit)
                out[name] = GitPinEntry(
                    "git-bundles",
                    name,
                    block.repo,
                    block.ref,
                    commit,
                    block.submodules,
                    catalog[unit].licence,
                    None,
                )
    return tuple(out.values())
