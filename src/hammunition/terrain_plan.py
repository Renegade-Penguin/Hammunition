# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which terrain tiles the station's regions need, resolved before the plan
prints.  D-061.

For each region, in order:

1. its record, ``<data>/dem-copernicus/<slug>.tiles``, written the last time
   its tiles were installed -- no network; or
2. its Geofabrik outline, ``<region>.poly``, asked through the same
   :class:`~hammunition.geofabrik.Probe` that resolves the regions, turned
   into the squares it touches and filtered by the carried tile list (a
   square not in it has no published tile: sea, or land Copernicus does
   not release).

Then every tile not already installed is resolved to a verifiable download:
from its pin, asking nothing, or from the bucket's ``HEAD`` for its size and
ETag MD5. A pinned tile about to be fetched is ``HEAD``-checked too, as a
pinned region is (piece 1, fix round 1, I3), so an offline plan refuses
before anything runs rather than after apt has.

Offline, the rule is piece 1's: what is installed stays installed and needs
no network; a region with no record and a tile not installed cannot be
resolved, and every such one is named together in one
:class:`~hammunition.copernicus.CopernicusError`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .attributed import PublisherChecks, recheck_installed
from .backends.base import CommandRunner
from .backends.brouter import JAR_GLOB, BRouterConverter, InputPins
from .backends.brouter import Source as BRouterSource
from .backends.dem import (
    COPERNICUS,
    THREEDEP,
    TIF,
    TILES,
    BareEarthDisclosure,
    DemResolution,
    DemTilesBackend,
    RegionTiles,
    TerrainDisclosure,
    read_record,
)
from .backends.derived import Converter
from .backends.fstopo import FsTopoBackend, FsTopoDisclosure, FsTopoResolution
from .backends.garmin import MKGMAP_HEAP, SPLITTER_HEAP, GarminConverter
from .backends.gdal_dem import GdalDemConverter
from .backends.regions import MapLedger, MapResolution, data_root
from .backends.routino import RoutinoConverter, Source
from .backends.splat_sdf import SplatSdfConverter
from .backends.staging import Staging
from .backends.terrain import TerrainLedger, TerrainWork, terrain_needs
from .backends.topo import TopoDisclosure, TopoQuadsBackend, TopoResolution
from .backends.topo_mosaic import UstopoMosaicConverter
from .copernicus import (
    CopernicusError,
    TileFile,
    TilePin,
    TileProbe,
    load_pins,
    load_tile_list,
    parse_poly,
    recorded_tile,
    resolve_tile,
    select,
    squares_touching,
)
from .fetch import Fetcher
from .geofabrik import BASE, GeofabrikError, Probe, RegionFile
from .manifest.schema import BinaryInstall, DemTilesInstall, DerivedDataInstall, TopoQuadsInstall
from .plan import InstallPlan, PlannedPackage
from .progress import run_checks
from .resolution import CatalogueMiss, ResolutionContext
from .retry import OnOutage, Outages, PublisherUnavailable, hint_for, reporter_for
from .topo_bound import ALL, TopoBound
from .usgs3dep import TileRow, check_tile, tile_url
from .usgs3dep import load_tile_list as load_threedep_list
from .usgs3dep import square_of as threedep_square
from .usgs3dep import tile_name as threedep_name


def poly_url(region: str) -> str:
    return f"{BASE}/{region}.poly"


def region_tiles(
    region: str,
    slug: str,
    *,
    installed: Path | None,
    tile_list: frozenset[str],
    probe: Probe,
    context: ResolutionContext | None = None,
) -> RegionTiles:
    """*region*'s tiles: its record when there is one, else its outline.

    *installed* is ``None`` for a stateless listing (:func:`hammunition.artifacts.list_inputs`):
    no record is read, and the outline is always asked for."""
    recorded = (
        read_record(installed / f"{slug}{TILES}", region, slug) if installed is not None else None
    )
    if (
        recorded is not None
        and recorded.bound == "all"
        and (context is None or all(n in tile_list for n in recorded.tiles))
    ):
        return recorded

    def derive(text: str) -> RegionTiles:
        outer, holes = parse_poly(text)
        tiles, unpublished = select(squares_touching(outer, holes), tile_list)
        return RegionTiles(region, slug, tiles, unpublished)

    def fallback() -> RegionTiles:
        assert context is not None
        selected = context.selection("tile-selection", region, read_record)
        if (
            selected is not None
            and selected.bound == "all"
            and all(n in tile_list for n in selected.tiles)
        ):
            return RegionTiles(region, slug, selected.tiles, selected.unpublished)
        return derive(context.outline(region, probe, base=BASE))

    def online() -> RegionTiles:
        return derive(probe.text(poly_url(region)))

    return (
        context.choose("inputs", f"tile-selection/{region}", online, fallback)
        if context is not None
        else online()
    )


def resolve_terrain(
    regions: Sequence[tuple[str, str]],
    *,
    installed: Path,
    tile_list: frozenset[str],
    pins: Mapping[str, TilePin],
    region_probe: Probe,
    tile_probe: TileProbe,
    on_outage: OnOutage | None = None,
    checks: PublisherChecks | None = None,
    context: ResolutionContext | None = None,
    unit: str | None = None,
) -> DemResolution:
    """*regions* as ``(region, slug)`` pairs -- this run's and the kept ones --
    resolved to the tiles they need and how each is fetched. A pair named
    twice is resolved once, so its outline is never asked for twice. A
    publisher that did not answer after the retries goes to *on_outage* and
    that region or tile is left out (#200); without it, it is refused.

    With a *context* the tiles come from the Bunker's records when the bucket
    cannot be asked, and *unit* (the manifest's name, the catalogue key) is
    required. A tile the Bunker cannot answer for, while a Bunker is verified,
    defers the whole selection rather than recording fewer tiles than the
    regions want."""
    if context is not None and unit is None:
        raise ValueError("resolve_terrain needs the unit name when given a context")
    refused: list[str] = []
    entries: list[RegionTiles] = []
    seen: set[tuple[str, str]] = set()
    for region, slug in regions:
        if (region, slug) in seen:
            continue
        seen.add((region, slug))
        try:
            entries.append(
                region_tiles(
                    region,
                    slug,
                    installed=installed,
                    tile_list=tile_list,
                    probe=region_probe,
                    context=context,
                )
            )
        except PublisherUnavailable as exc:
            if on_outage is None:
                refused.append(f"  {region}: its outline could not be read: {exc}")
            else:
                if context is not None:
                    raise CatalogueMiss(f"{region}: no complete selection: {exc}") from exc
                on_outage(f"{region} (its outline)", exc)
        except CatalogueMiss:
            raise
        except (GeofabrikError, CopernicusError, OSError) as exc:
            refused.append(f"  {region}: its outline could not be read: {exc}")
    wanted = sorted({name for entry in entries for name in entry.tiles})
    current = [name for name in wanted if (installed / f"{name}{TIF}").is_file()]
    held = set(current)
    todo = [name for name in wanted if name not in held]

    def check_online(name: str) -> TileFile:
        tile = resolve_tile(name, pins=pins, probe=tile_probe)
        if tile.sha256 is not None:
            status, _, _ = tile_probe.head(tile.url)
            if status != 200:
                raise CopernicusError(f"{tile.url} answered HTTP {status}, not 200")
        return tile

    def check(name: str) -> TileFile:
        # One choose around the resolve and the pinned reachability HEAD, so an
        # outage in either is answered by the same single catalogue lookup.
        if context is None:
            return check_online(name)
        assert unit is not None
        return context.choose(
            unit,
            name,
            lambda: check_online(name),
            lambda: recorded_tile(name, unit=unit, pins=pins, context=context),
        )

    # #197: an installed tile the log attributes is not asked again until the
    # attribution is a week old; a failed re-check is a note, never a refusal.
    # Offline the bucket is never asked; online a failed re-check stays a note
    # (never the Bunker's answer, which says nothing of the installed file).
    if context is None or not context.offline:
        recheck_installed(
            checks,
            installed.name,
            current,
            name=lambda tile: tile,
            path=lambda tile: installed / f"{tile}{TIF}",
            check=check_online,
            label="installed terrain tiles against the Copernicus DEM bucket",
            probe=tile_probe,
        )

    fetch: list[TileFile] = []
    deferred: list[str] = []
    outcomes = run_checks(todo, check, label="terrain tiles against the Copernicus DEM bucket")
    for name, outcome in zip(todo, outcomes, strict=True):
        try:
            tile = outcome.get()
        except PublisherUnavailable as exc:
            if context is not None and context.verified is not None:
                raise CatalogueMiss(f"{name}: no complete tile set: {exc}") from exc
            if on_outage is None:
                refused.append(f"  {name}: {exc}")
            else:
                on_outage(name, exc)
                deferred.append(name)
            continue
        except (CopernicusError, OSError) as exc:
            refused.append(f"  {name}: {exc}")
            continue
        fetch.append(tile)
    if refused:
        raise CopernicusError(
            f"{len(refused)} terrain item(s) could not be resolved and are not installed "
            f"already:\n" + "\n".join(refused) + hint_for(refused)
        )
    return DemResolution(
        regions=tuple(entries),
        fetch=tuple(fetch),
        current=tuple(current),
        deferred=tuple(deferred),
    )


def region_bare_earth(
    region: str,
    slug: str,
    *,
    installed: Path | None,
    tiles: Mapping[str, TileRow],
    region_probe: Probe,
    bound: TopoBound = ALL,
    context: ResolutionContext | None = None,
) -> RegionTiles:
    """Select 3DEP tiles using current records, verified inputs, or an outline.

    *installed* is ``None`` for a stateless listing: no record is read."""
    if bound.mode == "none" or not bound.wants_region(region):
        return RegionTiles(region, slug, (), 0, bound.token)
    recorded = (
        read_record(installed / f"{slug}{TILES}", region, slug) if installed is not None else None
    )
    if (
        recorded is not None
        and recorded.bound == bound.token
        and (context is None or all(n in tiles for n in recorded.tiles))
    ):
        return recorded

    def derive(text: str) -> RegionTiles:
        outer, holes = parse_poly(text)
        names = {threedep_name(square) for square in squares_touching(outer, holes)}
        found = tuple(sorted(name for name in names if name in tiles))
        unpublished = len(names) - len(found)
        found = tuple(name for name in found if bound.keeps(tile_box(name)))
        if not found and unpublished and bound.token != "all":
            unpublished = 0
        return RegionTiles(region, slug, found, unpublished, bound.token)

    def fallback() -> RegionTiles:
        assert context is not None
        selected = context.selection("dem3dep-selection", region, read_record)
        if (
            selected is not None
            and selected.bound in (bound.token, "all")
            and all(n in tiles for n in selected.tiles)
        ):
            narrowed = tuple(n for n in selected.tiles if bound.keeps(tile_box(n)))
            unpublished = selected.unpublished
            if not narrowed and unpublished and selected.bound == "all" and bound.token != "all":
                unpublished = 0
            return RegionTiles(
                region,
                slug,
                narrowed,
                unpublished,
                bound.token,
            )
        return derive(context.outline(region, region_probe, base=BASE))

    def online() -> RegionTiles:
        return derive(region_probe.text(poly_url(region)))

    return (
        context.choose("inputs", f"dem3dep-selection/{region}", online, fallback)
        if context is not None
        else online()
    )


def finish_selection(
    entries: list[RegionTiles],
    refused: list[str],
    *,
    selection_only: bool,
    installed: Path | None,
    tile_probe: TileProbe | None,
) -> DemResolution | None:
    """After the selection loop: with *selection_only*, the selection alone is
    the answer (:func:`hammunition.artifacts.list_inputs` reads no installed
    file and checks no tile against the bucket); otherwise an install caller
    must have given a real *installed* directory and *tile_probe*, and
    ``None`` tells the caller to continue into the installed-file and
    publisher checks below."""
    if selection_only:
        if refused:
            raise CopernicusError("\n".join(refused))
        return DemResolution(regions=tuple(entries))
    if installed is None or tile_probe is None:
        raise CopernicusError("terrain downloads require an installed directory and tile probe")
    return None


def resolve_bare_earth(
    regions: Sequence[tuple[str, str]],
    *,
    installed: Path | None,
    tiles: Mapping[str, TileRow],
    region_probe: Probe,
    tile_probe: TileProbe | None,
    on_outage: OnOutage | None = None,
    checks: PublisherChecks | None = None,
    context: ResolutionContext | None = None,
    bound: TopoBound = ALL,
    unit: str | None = None,
    selection_only: bool = False,
) -> DemResolution:
    """*regions* resolved to their USGS 3DEP tiles, under *bound* (issue #232) (D-068, amended
    2026-10-01): each region's record, else its outline's squares named by
    their north-west corner and kept where the carried list has a tile; then
    every tile not installed HEAD-checked against the list's size and ETag.
    Every refusal is named together, as for Copernicus.

    With a *context* a tile the bucket cannot be asked about is answered by
    the Bunker's record, compared with the carried list; *unit* (the manifest's
    name, the catalogue key) is then required. A tile the Bunker cannot answer
    for, while a Bunker is verified, defers the whole selection.

    With *selection_only* (Task 16), the function returns right after the
    selection loop below, before anything installed is read and before the
    bucket is asked about a single tile: *installed* and *tile_probe* may
    both be ``None``."""
    if context is not None and unit is None:
        raise ValueError("resolve_bare_earth needs the unit name when given a context")
    refused: list[str] = []
    entries: list[RegionTiles] = []
    seen: set[tuple[str, str]] = set()
    for region, slug in regions:
        if (region, slug) in seen:
            continue
        seen.add((region, slug))
        if bound.mode == "none" or not bound.wants_region(region):
            continue
        try:
            entries.append(
                region_bare_earth(
                    region,
                    slug,
                    installed=installed,
                    tiles=tiles,
                    region_probe=region_probe,
                    bound=bound,
                    context=context,
                )
            )
        except PublisherUnavailable as exc:
            if on_outage is None:
                refused.append(f"  {region}: its outline could not be read: {exc}")
            else:
                if context is not None:
                    raise CatalogueMiss(f"{region}: no complete selection: {exc}") from exc
                on_outage(f"{region} (its outline)", exc)
        except CatalogueMiss:
            raise
        except (GeofabrikError, CopernicusError, OSError) as exc:
            refused.append(f"  {region}: its outline could not be read: {exc}")
    early = finish_selection(
        entries, refused, selection_only=selection_only, installed=installed, tile_probe=tile_probe
    )
    if early is not None:
        return early
    assert installed is not None and tile_probe is not None  # finish_selection guaranteed this
    wanted = sorted({name for entry in entries for name in entry.tiles})
    current = [name for name in wanted if (installed / f"{name}{TIF}").is_file()]
    held = set(current)
    todo = [name for name in wanted if name not in held]
    fetch: list[TileFile] = []
    deferred: list[str] = []
    absent = (
        "recorded for a region but no longer in the carried 3DEP list; "
        "scripts/gen_3dep_tiles.py --fetch regenerates it"
    )

    def check_with(name: str, ctx: ResolutionContext | None) -> TileRow:
        row = tiles.get(name)
        if row is None:
            raise CopernicusError(absent)
        check_tile(row, tile_probe, context=ctx, unit=unit)
        return row

    def check(name: str) -> TileRow:
        return check_with(name, context)

    # Offline the bucket is never asked; online a failed re-check stays a note
    # (never the Bunker's answer, which says nothing of the installed file).
    if context is None or not context.offline:
        recheck_installed(
            checks,
            installed.name,
            current,
            name=lambda tile: tile,
            path=lambda tile: installed / f"{tile}{TIF}",
            check=lambda name: check_with(name, None),
            label="installed 3DEP terrain tiles against the USGS bucket",
            probe=tile_probe,
        )

    outcomes = run_checks(todo, check, label="3DEP terrain tiles against the USGS bucket")
    for name, outcome in zip(todo, outcomes, strict=True):
        try:
            row = outcome.get()
        except PublisherUnavailable as exc:
            if context is not None and context.verified is not None:
                raise CatalogueMiss(f"{name}: no complete tile set: {exc}") from exc
            if on_outage is None:
                refused.append(f"  {name}: {exc}")
            else:
                on_outage(name, exc)
                deferred.append(name)
            continue
        except (CopernicusError, OSError) as exc:
            refused.append(f"  {name}: {exc}")
            continue
        fetch.append(TileFile(name, tile_url(name), row.size, None, None, row.etag))
    if refused:
        raise CopernicusError(
            f"{len(refused)} 3DEP item(s) could not be resolved and are not installed "
            f"already:\n" + "\n".join(refused) + hint_for(refused)
        )
    return DemResolution(
        regions=tuple(entries),
        fetch=tuple(fetch),
        current=tuple(current),
        deferred=tuple(deferred),
    )


THREEDEP_LIST = Path("data") / "usgs-3dep-tiles.txt"


def copernicus_chosen_note(unit: str) -> str:
    """The plan's note for a planned 3DEP unit while the station chose Copernicus."""
    return (
        f"{unit}: dem_source is copernicus (the default), so no 3DEP tile is fetched and "
        f"any installed one is removed; `hammunition station set --dem-source 3dep` "
        f"chooses USGS bare earth, about ten times the size of Copernicus"
    )


@dataclass(frozen=True)
class _TileBox:
    south: float
    west: float
    north: float
    east: float


def tile_box(name: str) -> _TileBox:
    """The degree square a 3DEP tile covers, as a box."""
    lat, lon = threedep_square(name)
    return _TileBox(lat, lon, lat + 1, lon + 1)


def installed_tiles(directory: Path) -> DemResolution:
    """The 3DEP tiles on disk, from their regions' records, offline: what is
    kept while the bound cannot be computed (D-035). Nothing is fetched or
    removed."""
    entries: list[RegionTiles] = []
    current: set[str] = set()
    for record in sorted(directory.glob(f"*{TILES}")):
        entry = read_record(record, record.stem, record.stem)
        if entry is None:
            continue
        entries.append(entry)
        current.update(n for n in entry.tiles if (directory / f"{n}{TIF}").is_file())
    return DemResolution(regions=tuple(entries), current=tuple(sorted(current)))


def resolve_station_3dep(
    plan: InstallPlan,
    maps: MapResolution,
    catalog_root: Path,
    *,
    prefix: Path,
    source: str,
    region_probe: Probe,
    tile_probe: TileProbe,
    outages: Outages | None = None,
    checks: PublisherChecks | None = None,
    context: ResolutionContext | None = None,
    bound: TopoBound | None = ALL,
) -> tuple[DemResolution, tuple[str, ...]]:
    """The plan's 3DEP tiles and notes (D-068, amended 2026-10-01). Nothing
    unless the plan holds a ``usgs-3dep`` unit; with the station's
    ``dem_source`` anything but ``3dep``, nothing is resolved -- no outline
    asked, no HEAD -- and the plan says so."""
    unit = _planned_dem(plan, "usgs-3dep")
    if unit is None:
        return DemResolution(), ()
    if source != "3dep":
        return DemResolution(), (copernicus_chosen_note(unit.name),)
    if bound is None:
        return installed_tiles(data_root(prefix) / unit.name), ()
    tiles = load_threedep_list(catalog_root / THREEDEP_LIST)
    regions = [(f.region, f.slug) for f in maps.files]
    ours = {slug for _, slug in regions}
    regions += [(k.region, k.slug) for k in maps.kept if k.slug not in ours]
    resolution = resolve_bare_earth(
        regions,
        installed=data_root(prefix) / unit.name,
        tiles=tiles,
        region_probe=region_probe,
        tile_probe=tile_probe,
        on_outage=reporter_for(outages, unit),
        checks=checks,
        context=context,
        bound=bound,
        unit=unit.name,
    )
    return resolution, ()


# ---------------------------------------------------------------------------
# The whole of piece 2 for one install run: which units the plan holds, the
# backends that build them, what the plan discloses and what the disk needs.
# ---------------------------------------------------------------------------

TILE_LIST = Path("data") / "copernicus-glo30-tiles.txt"
PINS = Path("data") / "copernicus-glo30-pins.yaml"
#: The splitter's heap through ``JAVA_OPTS``, mkgmap's through
#: ``JAVA_TOOL_OPTIONS``: Debian's mkgmap wrapper ignores ``JAVA_OPTS``
#: (Task 6 review, I2), so one variable alone would leave mkgmap on the JVM's
#: default heap.
SPLITTER_ENV = {"JAVA_OPTS": SPLITTER_HEAP, "JAVA_TOOL_OPTIONS": MKGMAP_HEAP}


def _planned(
    plan: InstallPlan, method: type[object], converter: str | None = None
) -> PlannedPackage | None:
    for planned in plan.packages:
        block = planned.block.install
        if isinstance(block, method) and (
            converter is None or getattr(block, "converter", None) == converter
        ):
            return planned
    return None


def _planned_dem(plan: InstallPlan, provider: str) -> PlannedPackage | None:
    """The planned ``dem-tiles`` unit of *provider*, or None."""
    for planned in plan.packages:
        block = planned.block.install
        if isinstance(block, DemTilesInstall) and block.provider == provider:
            return planned
    return None


def resolve_station_terrain(
    plan: InstallPlan,
    maps: MapResolution,
    catalog_root: Path,
    *,
    prefix: Path,
    region_probe: Probe,
    tile_probe: TileProbe,
    outages: Outages | None = None,
    checks: PublisherChecks | None = None,
    context: ResolutionContext | None = None,
) -> DemResolution:
    """The plan's terrain, or an empty resolution when it holds no dem-tiles unit.

    The regions are this run's resolved files and the ones kept offline.
    A missing tile list is refused by name: without it every square would
    look unpublished, and a region would silently get no terrain.

    A region's record is written only when its terrain is installed, so until
    then every plan -- a dry run included -- asks for its outline again; the
    plan says so (Task 10's note, ruled at Task 13: no plan-time cache, which
    under sudo would be a root-owned file in the operator's cache)."""
    unit = _planned_dem(plan, "copernicus-glo30")
    if unit is None:
        return DemResolution()
    listed = catalog_root / TILE_LIST
    try:
        tile_list = load_tile_list(listed)
    except OSError as exc:
        raise CopernicusError(
            f"the Copernicus tile list {listed} cannot be read ({exc.strerror or exc}); "
            f"it is carried in the catalog, and scripts/gen_copernicus_pins.py --refresh-list "
            f"writes it"
        ) from exc
    pins_path = catalog_root / PINS
    pins = load_pins(pins_path) if pins_path.is_file() else {}
    regions = [(f.region, f.slug) for f in maps.files]
    ours = {slug for _, slug in regions}
    regions += [(k.region, k.slug) for k in maps.kept if k.slug not in ours]
    return resolve_terrain(
        regions,
        installed=data_root(prefix) / unit.name,
        tile_list=tile_list,
        pins=pins,
        region_probe=region_probe,
        tile_probe=tile_probe,
        on_outage=reporter_for(outages, unit),
        checks=checks,
        context=context,
        unit=unit.name,
    )


def _alternative(plan: InstallPlan, converter: str) -> str | None:
    planned = _planned(plan, DerivedDataInstall, converter)
    if planned is None or not isinstance(planned.block.install, DerivedDataInstall):
        return None
    return planned.block.install.alternative


def contour_source(plan: InstallPlan) -> str | None:
    """The ``dem-tiles`` unit the planned ``gdal-dem`` block names as its
    ``alternative`` (D-068, amended 2026-10-01), or None."""
    return _alternative(plan, "gdal-dem")


def splat_source(plan: InstallPlan) -> str | None:
    """The same for the planned ``splat-sdf`` block (D-061, amended 2026-10-02)."""
    return _alternative(plan, "splat-sdf")


def brouter_pins(plan: InstallPlan) -> InputPins:
    """The jar and the filters' version the plan installs for the BRouter
    build (D-063), from the planned manifests: a BRouter bumped in this run
    is seen before its new tree is on disk."""
    build = _planned(plan, DerivedDataInstall, "brouter-mapcreator")
    if build is None or not isinstance(build.block.install, DerivedDataInstall):
        return InputPins()
    block = build.block.install
    planned = {p.name: p for p in plan.packages}
    jar = None
    program = planned.get(block.program or "")
    if program is not None and isinstance(program.block.install, BinaryInstall):
        marker = program.block.install.tree_marker
        if marker is not None and fnmatch(marker, JAR_GLOB):
            jar = marker
    profiles = planned.get(block.profiles or "")
    return InputPins(jar=jar, profiles=profiles.manifest.version if profiles else None)


@dataclass(frozen=True)
class TerrainRun:
    """Piece 2's backends for one run, sharing one :class:`TerrainLedger`."""

    ledger: TerrainLedger
    dem: DemTilesBackend
    garmin: GarminConverter
    routino: RoutinoConverter
    gdal: GdalDemConverter
    topo: TopoQuadsBackend
    mosaic: UstopoMosaicConverter
    brouter: BRouterConverter
    splat: SplatSdfConverter
    dem_source: str = "copernicus"
    """The station's ``dem_source`` this run (D-068, amended 2026-10-01)."""
    topo_bound: TopoBound | None = ALL
    """The bound the topographic selection was made under (issue #232)."""

    @property
    def converters(self) -> dict[str, Converter]:
        return {
            "mkgmap": self.garmin,
            "routino-planetsplitter": self.routino,
            "gdal-dem": self.gdal,
            "ustopo-mosaic": self.mosaic,
            "brouter-mapcreator": self.brouter,
            "splat-sdf": self.splat,
        }

    def _topo(self, plan: InstallPlan) -> TopoDisclosure | None:
        """The US Topo part of the disclosure (D-068); None with neither unit."""
        quads = _planned(plan, TopoQuadsInstall)
        mosaic = _planned(plan, DerivedDataInstall, "ustopo-mosaic")
        if quads is None and mosaic is None:
            return None
        block = quads.block.install if quads is not None else None
        building = False
        if mosaic is not None and isinstance(mosaic.block.install, DerivedDataInstall):
            # Anything to do: a warp, a removal, or the VRT rebuilt. The steps
            # are built, not run; building them only reads the disk.
            building = bool(self.mosaic._ustopo_steps(mosaic.manifest, mosaic.block.install))
        return TopoDisclosure(
            resolution=self.topo.resolution,
            licence=block.licence if isinstance(block, TopoQuadsInstall) else "",
            licence_url=block.licence_url if isinstance(block, TopoQuadsInstall) else "",
            warp=tuple(self.mosaic.pending(mosaic.manifest)) if mosaic is not None else (),
            building=building,
            selection=(
                self.topo_bound.describe()
                if self.topo_bound is not None
                else "what is installed is kept (no grid square is set)"
            ),
            everything=self.topo_bound is not None and self.topo_bound.mode == "all",
        )

    def _fstopo(self, plan: InstallPlan) -> FsTopoDisclosure | None:
        """The FSTopo part of the disclosure; None with neither the unit nor
        a mosaic naming it."""
        sheets = next(
            (
                p
                for p in plan.packages
                if isinstance(p.block.install, TopoQuadsInstall)
                and p.block.install.provider == "usfs-fstopo"
            ),
            None,
        )
        mosaic = _planned(plan, DerivedDataInstall, "ustopo-mosaic")
        reads = (
            mosaic is not None
            and isinstance(mosaic.block.install, DerivedDataInstall)
            and mosaic.block.install.fstopo is not None
        )
        resolution = self.topo.fstopo.resolution if self.topo.fstopo else FsTopoResolution()
        if sheets is None and not (reads and (resolution.quads or self._fstopo_left(mosaic))):
            return None
        block = sheets.block.install if sheets is not None else None
        convert: tuple[int, ...] = ()
        building = False
        if mosaic is not None and isinstance(mosaic.block.install, DerivedDataInstall) and reads:
            convert = tuple(self.mosaic.fstopo_sizes(mosaic.manifest, mosaic.block.install))
            building = bool(self.mosaic._fstopo_steps(mosaic.manifest, mosaic.block.install))
        return FsTopoDisclosure(
            resolution=resolution,
            licence=block.licence if isinstance(block, TopoQuadsInstall) else "",
            licence_url=block.licence_url if isinstance(block, TopoQuadsInstall) else "",
            convert=convert,
            building=building,
        )

    def _fstopo_left(self, mosaic: PlannedPackage | None) -> bool:
        """Whether an FSTopo map is on disk with no sheet left to draw."""
        if mosaic is None or not isinstance(mosaic.block.install, DerivedDataInstall):
            return False
        return bool(self.mosaic._fstopo_steps(mosaic.manifest, mosaic.block.install))

    def _bare_earth(self, plan: InstallPlan) -> BareEarthDisclosure | None:
        unit = _planned_dem(plan, THREEDEP)
        if unit is None or not isinstance(unit.block.install, DemTilesInstall):
            return None
        bare = self.dem.bare_earth
        return BareEarthDisclosure(
            resolution=bare.resolution if bare is not None else DemResolution(),
            licence=unit.block.install.licence,
            licence_url=unit.block.install.licence_url,
            chosen=self.dem_source == "3dep",
        )

    def disclosure(self, plan: InstallPlan) -> TerrainDisclosure | None:
        """What the plan says about piece 2; None when it holds none of its units."""
        dem = _planned_dem(plan, "copernicus-glo30")
        garmin = _planned(plan, DerivedDataInstall, "mkgmap")
        routino = _planned(plan, DerivedDataInstall, "routino-planetsplitter")
        gdal = _planned(plan, DerivedDataInstall, "gdal-dem")
        topo = self._topo(plan)
        fstopo = self._fstopo(plan)
        bare_earth = self._bare_earth(plan)
        brouter = _planned(plan, DerivedDataInstall, "brouter-mapcreator")
        splat = _planned(plan, DerivedDataInstall, "splat-sdf")
        if not (
            dem or garmin or routino or gdal or brouter or splat or topo or fstopo or bare_earth
        ):
            return None
        sources = self._routino_sources(routino)
        rebuilt, tiles, squares = self._brouter_work(brouter)
        # Contours from the tiles still to draw; `drawing` from those and the
        # record check -- not from a count of steps, which would count a
        # removal as drawing (Task 12 review).
        contours = self.gdal.pending(gdal.manifest) if gdal is not None else []
        drawing = gdal is not None and (bool(contours) or not self.gdal.current(gdal.manifest))
        licence = dem.block.install if dem is not None else None
        return TerrainDisclosure(
            resolution=self.dem.resolution if dem is not None else DemResolution(),
            licence=licence.licence if isinstance(licence, DemTilesInstall) else "",
            licence_url=licence.licence_url if isinstance(licence, DemTilesInstall) else "",
            garmin=tuple(self.garmin.pending(garmin.manifest)) if garmin is not None else (),
            routino_regions=len(sources),
            routino_total=sum(s.size for s in sources),
            contours=len(contours),
            drawing=drawing,
            brouter_regions=len(rebuilt),
            brouter_total=sum(s.size for s in rebuilt),
            brouter_tiles=tiles,
            brouter_squares=squares,
            topo=topo,
            bare_earth=bare_earth,
            fstopo=fstopo,
            elevation=self.gdal.provider,
            splat_tiles=len(self.splat.pending(splat.manifest)) if splat is not None else 0,
            splat_building=splat is not None and not self.splat.current(splat.manifest),
        )

    def _brouter_work(self, brouter: PlannedPackage | None) -> tuple[list[BRouterSource], int, int]:
        """The regions BRouter's routing files are rebuilt over this run, and
        the tiles and squares folded in; nothing when they are current."""
        if brouter is None or not isinstance(brouter.block.install, DerivedDataInstall):
            return [], 0, 0
        block = brouter.block.install
        rebuilt = self.brouter.pending(brouter.manifest, block)
        if not rebuilt:
            return [], 0, 0
        return rebuilt, len(self.brouter.wanted_tiles(block)), len(self.brouter.squares(block))

    def _routino_sources(self, routino: PlannedPackage | None) -> list[Source]:
        if routino is None or not isinstance(routino.block.install, DerivedDataInstall):
            return []
        return self.routino.pending(routino.manifest, routino.block.install)

    def needs(self, plan: InstallPlan, *, cache: Path, prefix: Path) -> dict[Path, int]:
        """Bytes piece 2 needs per directory this run; empty when it has no units."""
        disclosed = self.disclosure(plan)
        if disclosed is None:
            return {}
        work = TerrainWork(
            tiles=sum(t.size for t in disclosed.resolution.fetch),
            garmin=tuple(f.size for f in disclosed.garmin),
            routino=disclosed.routino_total,
            contour_tiles=disclosed.contours,
            brouter=disclosed.brouter_total,
            brouter_regions=disclosed.brouter_regions,
            brouter_squares=disclosed.brouter_squares,
            quads=sum(q.size for q in disclosed.topo.resolution.fetch) if disclosed.topo else 0,
            warp=tuple(q.size for q in disclosed.topo.warp) if disclosed.topo else (),
            bare_earth=(
                sum(t.size for t in disclosed.bare_earth.resolution.fetch)
                if disclosed.bare_earth
                else 0
            ),
            sheets=(
                sum(f.size for f in disclosed.fstopo.resolution.fetch) if disclosed.fstopo else 0
            ),
            convert=disclosed.fstopo.convert if disclosed.fstopo else (),
            elevation=disclosed.elevation,
            splat_tiles=disclosed.splat_tiles,
        )
        return terrain_needs(
            work,
            cache=cache,
            garmin_staging=self.garmin.staging.directory,
            routino_staging=self.routino.staging.directory,
            contour_staging=self.gdal.staging.directory,
            mosaic_staging=self.mosaic.staging.directory,
            prefix=prefix,
            brouter_staging=self.brouter.staging.directory,
            splat_staging=self.splat.staging.directory,
        )


def build_terrain_run(
    *,
    prefix: Path,
    builds: Path,
    owner: str | None,
    runner: CommandRunner | None,
    fetcher: Fetcher,
    files: Sequence[RegionFile],
    keep: frozenset[str],
    regions: MapLedger,
    resolution: DemResolution,
    pins: InputPins | None = None,
    topo: TopoResolution | None = None,
    bare_earth: DemResolution | None = None,
    dem_source: str = "copernicus",
    contour_source: str | None = None,
    fstopo: FsTopoResolution | None = None,
    splat_source: str | None = None,
    topo_bound: TopoBound | None = ALL,
) -> TerrainRun:
    """Every piece-2 backend for one run. Each converter stages in its own
    directory under the operator's build tree and runs as the operator."""
    ledger = TerrainLedger()
    chosen = dem_source == "3dep"
    # D-068 (amended 2026-10-01): with 3DEP chosen, QMapShack's contours and
    # elevation are drawn from it -- when the gdal-dem block names its unit
    # (*contour_source*); Copernicus stays installed either way, for BRouter
    # and for regions 3DEP does not cover.
    three = bare_earth or DemResolution()
    drawn_from_3dep = chosen and contour_source is not None
    # D-061 (amended 2026-10-02): SPLAT's terrain follows the same choice
    # through its own block's `alternative`.
    splat_from_3dep = chosen and splat_source is not None
    return TerrainRun(
        ledger=ledger,
        dem_source=dem_source,
        topo_bound=topo_bound,
        dem=DemTilesBackend(
            fetcher=fetcher,
            prefix=prefix,
            resolution=resolution,
            keep=keep,
            ledger=ledger,
            runner=runner,
            provider=COPERNICUS,
            bare_earth=DemTilesBackend(
                fetcher=fetcher,
                prefix=prefix,
                resolution=three,
                keep=keep if chosen else frozenset(),
                ledger=ledger,
                runner=runner,
                provider=THREEDEP,
            ),
        ),
        garmin=GarminConverter(
            prefix=prefix,
            files=files,
            staging=Staging(builds / "osm-garmin", owner=owner, environ=SPLITTER_ENV),
            keep=keep,
            regions=regions,
            ledger=ledger,
            runner=runner,
        ),
        routino=RoutinoConverter(
            prefix=prefix,
            files=files,
            staging=Staging(builds / "osm-routino", owner=owner),
            keep=keep,
            regions=regions,
            ledger=ledger,
            runner=runner,
        ),
        gdal=GdalDemConverter(
            prefix=prefix,
            resolution=three if drawn_from_3dep else resolution,
            staging=Staging(builds / "dem-qmapshack", owner=owner),
            ledger=ledger,
            runner=runner,
            source_unit=contour_source if drawn_from_3dep else None,
            provider=THREEDEP if drawn_from_3dep else COPERNICUS,
        ),
        brouter=BRouterConverter(
            prefix=prefix,
            files=files,
            resolution=resolution,
            staging=Staging(builds / "brouter-segments", owner=owner),
            keep=keep,
            regions=regions,
            ledger=ledger,
            runner=runner,
            pins=pins or InputPins(),
        ),
        splat=SplatSdfConverter(
            prefix=prefix,
            resolution=three if splat_from_3dep else resolution,
            staging=Staging(builds / "splat-sdf", owner=owner),
            ledger=ledger,
            runner=runner,
            source_unit=splat_source if splat_from_3dep else None,
        ),
        # D-068: the US Topo sheets and their mosaic share the terrain ledger:
        # a sheet that did not install is named with the tiles that did not.
        topo=TopoQuadsBackend(
            fetcher=fetcher,
            prefix=prefix,
            resolution=topo or TopoResolution(),
            keep=keep,
            ledger=ledger,
            runner=runner,
            fstopo=FsTopoBackend(
                fetcher=fetcher,
                prefix=prefix,
                resolution=fstopo or FsTopoResolution(),
                keep=keep,
                ledger=ledger,
                runner=runner,
            ),
        ),
        mosaic=UstopoMosaicConverter(
            prefix=prefix,
            resolution=topo or TopoResolution(),
            staging=Staging(builds / "ustopo-qmapshack", owner=owner),
            ledger=ledger,
            runner=runner,
            fstopo=fstopo or FsTopoResolution(),
        ),
    )
