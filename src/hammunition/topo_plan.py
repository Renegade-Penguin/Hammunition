# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which US Topo sheets the station's regions need, resolved before the plan
prints.  D-068.

For each region, in order:

1. its record, ``<data>/usgs-ustopo/<slug>.quads``, written the last time its
   sheets were installed -- whole index rows, so no network is needed; or
2. its Geofabrik outline, ``<region>.poly``, asked through the same probe
   that resolves the regions and the terrain (one fetch serves both,
   :class:`MemoProbe`), turned into the 1/8-degree cells it touches and
   matched against the carried index.

A record naming a sheet the carried index no longer lists (a newer edition
was indexed since) is re-selected from the outline, so the newer edition is
fetched and the older removed. Offline, that re-selection cannot happen, and
the record is kept as it is, with a note: what is installed stays installed.

Then every sheet not already installed is checked against the bucket with a
``HEAD`` (:func:`hammunition.ustopo.check_quad`): the same size and ETag the
index carries, or the plan refuses naming it. Offline, a region with no
record and a sheet not installed cannot be resolved, and every such one is
named together in one :class:`~hammunition.ustopo.UstopoError`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .attributed import PublisherChecks, recheck_installed
from .backends.data import human_size
from .backends.fstopo import FsTopoResolution, RegionSheets
from .backends.fstopo import read_record as read_sheets
from .backends.regions import MapResolution, data_root
from .backends.topo import (
    QUADS,
    TIF,
    RegionQuads,
    TopoDisclosure,
    TopoResolution,
    read_record,
    stem_of,
)
from .backends.topo_mosaic import warp_estimate
from .copernicus import CopernicusError, TileProbe, parse_poly
from .fstopo import FsIndex, FsPin, FsQuad, FsQuadFile, FstopoError, GatewayProbe, recorded_sheet
from .fstopo import load_index as load_fstopo_index
from .fstopo import load_pins as load_fstopo_pins
from .geofabrik import BASE, GeofabrikError, Probe
from .manifest.schema import DemTilesInstall, DerivedDataInstall, TopoQuadsInstall
from .plan import Deferral, InstallPlan, PlannedPackage
from .progress import run_checks
from .resolution import CatalogueMiss, ResolutionContext
from .retry import OnOutage, Outages, PublisherUnavailable, hint_for, reporter_for
from .topo_bound import ALL, CONSENT_BYTES, DEFAULT_RADIUS_KM, TopoBound
from .ustopo import Quad, QuadIndex, UstopoError, check_quad, load_index

INDEX = Path("data") / "ustopo-quads.txt"
FSTOPO_INDEX = Path("data") / "fstopo-quads.txt"
FSTOPO_PINS = Path("data") / "fstopo-pins.yaml"


def poly_url(region: str) -> str:
    return f"{BASE}/{region}.poly"


@dataclass
class MemoProbe:
    """A :class:`~hammunition.geofabrik.Probe` that asks each URL's text once.

    The terrain and the US Topo sheets both follow a region's outline; one
    plan asks Geofabrik for it once, not once per unit. A publisher that did
    not answer *after the retries* (:class:`~hammunition.retry.PublisherUnavailable`)
    is remembered for the rest of the run, so each unit that needs the outline
    is told at once and the same dead request is not retried per unit (#200);
    any other failure is not remembered, so each caller sees the error for
    itself."""

    probe: Probe
    texts: dict[str, str] = field(default_factory=dict)
    outages: dict[str, PublisherUnavailable] = field(default_factory=dict)

    def head(self, url: str) -> tuple[int, int, str | None]:
        return self.probe.head(url)

    def text(self, url: str) -> str:
        if url in self.outages:
            raise self.outages[url]
        if url not in self.texts:
            try:
                self.texts[url] = self.probe.text(url)
            except PublisherUnavailable as exc:
                self.outages[url] = exc
                raise
        return self.texts[url]


def region_quads(
    region: str,
    slug: str,
    *,
    installed: Path | None,
    index: QuadIndex,
    probe: Probe,
    notes: list[str],
    bound: TopoBound = ALL,
    context: ResolutionContext | None = None,
) -> RegionQuads:
    """*region*'s sheets under *bound*: its record when it was selected under
    the same bound and is current, else its outline.

    *installed* is ``None`` for a stateless listing
    (:func:`hammunition.artifacts.list_inputs`): no record is read."""
    token = bound.token
    if bound.mode == "none" or not bound.wants_region(region):
        return RegionQuads(region, slug, (), token)
    recorded = (
        read_record(installed / f"{slug}{QUADS}", region, slug) if installed is not None else None
    )
    listed = index.by_path()
    if (
        recorded is not None
        and recorded.bound == token
        and all(q.path in listed for q in recorded.quads)
    ):
        # The index's rows, not the record's (review M1): a regenerated index
        # carries a re-uploaded object's new size and ETag under the same name.
        return RegionQuads(region, slug, tuple(listed[q.path] for q in recorded.quads), token)

    def derive(text: str) -> RegionQuads:
        outer, holes = parse_poly(text)
        return RegionQuads(region, slug, bound.select(index.select(outer, holes)), token)

    def fallback() -> RegionQuads:
        assert context is not None
        selected = context.selection("sheet-selection", region, read_record)
        if (
            selected is not None
            and selected.bound in (token, "all")
            and all(q.path in listed for q in selected.quads)
        ):
            return RegionQuads(
                region, slug, bound.select([listed[q.path] for q in selected.quads]), token
            )
        return derive(context.outline(region, probe, base=BASE))

    def online() -> RegionQuads:
        return derive(probe.text(poly_url(region)))

    try:
        return (
            context.choose("inputs", f"sheet-selection/{region}", online, fallback)
            if context is not None
            else online()
        )
    except (GeofabrikError, CopernicusError, OSError):
        if recorded is None:
            raise
        if recorded.bound == token:
            notes.append(
                f"{region}: a newer US Topo edition is indexed for some of its quads, and its "
                f"outline could not be fetched to choose them; the installed quads are kept"
            )
            return recorded
        if recorded.bound == "all" and all(q.path in listed for q in recorded.quads):
            # A whole region on record narrows to the bound with no outline.
            return RegionQuads(
                region, slug, bound.select([listed[q.path] for q in recorded.quads]), token
            )
        raise


def resolve_topo(
    regions: Sequence[tuple[str, str]],
    *,
    installed: Path,
    index: QuadIndex,
    region_probe: Probe,
    quad_probe: TileProbe,
    on_outage: OnOutage | None = None,
    checks: PublisherChecks | None = None,
    bound: TopoBound = ALL,
    context: ResolutionContext | None = None,
    unit: str | None = None,
) -> tuple[TopoResolution, tuple[str, ...]]:
    """*regions* as ``(region, slug)`` pairs resolved to the sheets they
    need under *bound* and how each is fetched, and any notes for the plan.
    A region the bound leaves out has no entry, so what is installed for it
    goes the way any region no longer wanted does (issue #232).

    A publisher that did not answer after the retries (#200) is passed to
    *on_outage* with the item it concerned -- a region whose outline is not
    available, or one sheet -- and that item is left out; with no *on_outage*
    (a unit the operator typed by name) it is refused like any other.

    With a *context*, a sheet the bucket cannot be asked about is answered by
    the Bunker's record, compared with the carried index; *unit* (the
    manifest's name, the catalogue key) is then required. A sheet the Bunker
    cannot answer for, while a Bunker is verified, defers the whole selection."""
    if context is not None and unit is None:
        raise ValueError("resolve_topo needs the unit name when given a context")
    refused: list[str] = []
    notes: list[str] = []
    entries: list[RegionQuads] = []
    seen: set[tuple[str, str]] = set()
    for region, slug in regions:
        if (region, slug) in seen:
            continue
        seen.add((region, slug))
        if bound.mode == "none" or not bound.wants_region(region):
            continue
        try:
            entries.append(
                region_quads(
                    region,
                    slug,
                    installed=installed,
                    index=index,
                    probe=region_probe,
                    notes=notes,
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
    wanted = {q.path: q for entry in entries for q in entry.quads}
    fetch = []
    current = []
    deferred: list[Quad] = []
    todo = []
    for path in sorted(wanted):
        quad = wanted[path]
        if (installed / f"{quad.name}{TIF}").is_file():
            current.append(quad)
        else:
            todo.append(quad)
    # Offline the bucket is never asked; online a failed re-check stays a note
    # (never the Bunker's answer, which says nothing of the installed file).
    if context is None or not context.offline:
        recheck_installed(
            checks,
            installed.name,
            current,
            name=lambda quad: quad.name,
            path=lambda quad: installed / f"{quad.name}{TIF}",
            check=lambda quad: check_quad(quad, quad_probe),
            label="installed US Topo sheets against the USGS bucket",
            probe=quad_probe,
        )
    outcomes = run_checks(
        todo,
        lambda quad: check_quad(quad, quad_probe, context=context, unit=unit),
        label="US Topo sheets against the USGS bucket",
    )
    for quad, outcome in zip(todo, outcomes, strict=True):
        try:
            outcome.get()
        except PublisherUnavailable as exc:
            if context is not None and context.verified is not None:
                raise CatalogueMiss(f"{quad.name}: no complete sheet set: {exc}") from exc
            if on_outage is None:
                refused.append(f"  {quad.name}: {exc}")
            else:
                on_outage(quad.name, exc)
                deferred.append(quad)
            continue
        except (UstopoError, CopernicusError, OSError) as exc:
            refused.append(f"  {quad.name}: {exc}")
            continue
        fetch.append(quad)
    if refused:
        raise UstopoError(
            f"{len(refused)} US Topo item(s) could not be resolved and are not installed "
            f"already:\n" + "\n".join(refused) + hint_for(refused)
        )
    resolution = TopoResolution(
        regions=tuple(entries),
        fetch=tuple(fetch),
        current=tuple(current),
        deferred=tuple(deferred),
    )
    return resolution, tuple(notes)


def resolve_station_topo(
    plan: InstallPlan,
    maps: MapResolution,
    catalog_root: Path,
    *,
    prefix: Path,
    region_probe: Probe,
    quad_probe: TileProbe,
    outages: Outages | None = None,
    checks: PublisherChecks | None = None,
    bound: TopoBound | None = ALL,
    context: ResolutionContext | None = None,
) -> tuple[TopoResolution, tuple[str, ...]]:
    """The plan's US Topo sheets, or an empty resolution when it holds no
    ``topo-quads`` unit. A missing or empty index is refused by name. With
    no *bound* (the grid square it needs is not set) what is installed is
    kept as it is and nothing is chosen (D-035). With
    *outages*, a publisher that is not answering defers what it concerned
    unless the operator typed the unit (#200)."""
    unit = _planned_topo(plan, "usgs-ustopo")
    if unit is None:
        return TopoResolution(), ()
    if bound is None:
        return installed_quads(data_root(prefix) / unit.name), ()
    index = load_index(catalog_root / INDEX)
    regions = [(f.region, f.slug) for f in maps.files]
    ours = {slug for _, slug in regions}
    regions += [(k.region, k.slug) for k in maps.kept if k.slug not in ours]
    return resolve_topo(
        regions,
        installed=data_root(prefix) / unit.name,
        index=index,
        region_probe=region_probe,
        quad_probe=quad_probe,
        on_outage=reporter_for(outages, unit),
        checks=checks,
        bound=bound,
        context=context,
        unit=unit.name,
    )


def installed_quads(directory: Path) -> TopoResolution:
    """The US Topo sheets on disk, from their regions' records, offline:
    what is kept while the bound cannot be computed. Nothing is fetched or
    removed."""
    found: dict[str, Quad] = {}
    entries: list[RegionQuads] = []
    for record in sorted(directory.glob(f"*{QUADS}")):
        entry = read_record(record, record.stem, record.stem)
        if entry is None:
            continue
        entries.append(entry)
        for quad in entry.quads:
            if (directory / f"{quad.name}{TIF}").is_file():
                found[quad.path] = quad
    return TopoResolution(
        regions=tuple(entries), current=tuple(found[path] for path in sorted(found))
    )


def _planned_topo(plan: InstallPlan, provider: str) -> PlannedPackage | None:
    for planned in plan.packages:
        block = planned.block.install
        if isinstance(block, TopoQuadsInstall) and block.provider == provider:
            return planned
    return None


# ---------------------------------------------------------------------------
# FSTopo (D-068, amended 2026-10-01): the same selection over the GTAC index,
# each sheet located through the raster gateway's one redirect.
# ---------------------------------------------------------------------------


def region_sheets(
    region: str,
    slug: str,
    *,
    installed: Path | None,
    index: FsIndex,
    probe: Probe,
    notes: list[str],
    bound: TopoBound = ALL,
    context: ResolutionContext | None = None,
) -> RegionSheets:
    """*region*'s FSTopo sheets under *bound*: its record when it was selected
    under the same bound and every sheet in it is still indexed (taken at the
    index's vintage), else its outline.

    *installed* is ``None`` for a stateless listing
    (:func:`hammunition.artifacts.list_inputs`): no record is read."""
    token = bound.token
    if bound.mode == "none" or not bound.wants_region(region):
        return RegionSheets(region, slug, (), token)
    recorded = (
        read_sheets(installed / f"{slug}{QUADS}", region, slug) if installed is not None else None
    )
    listed = index.by_secoord()
    if (
        recorded is not None
        and recorded.bound == token
        and all(q.secoord in listed for q in recorded.quads)
    ):
        return RegionSheets(region, slug, tuple(listed[q.secoord] for q in recorded.quads), token)

    def derive(text: str) -> RegionSheets:
        outer, holes = parse_poly(text)
        return RegionSheets(region, slug, bound.select(index.select(outer, holes)), token)

    def fallback() -> RegionSheets:
        assert context is not None
        selected = context.selection("fstopo-selection", region, read_sheets)
        if (
            selected is not None
            and selected.bound in (token, "all")
            and all(q.secoord in listed for q in selected.quads)
        ):
            return RegionSheets(
                region, slug, bound.select([listed[q.secoord] for q in selected.quads]), token
            )
        return derive(context.outline(region, probe, base=BASE))

    def online() -> RegionSheets:
        return derive(probe.text(poly_url(region)))

    try:
        return (
            context.choose("inputs", f"fstopo-selection/{region}", online, fallback)
            if context is not None
            else online()
        )
    except (GeofabrikError, CopernicusError, OSError):
        if recorded is None:
            raise
        if recorded.bound == token:
            notes.append(
                f"{region}: the FSTopo index no longer carries some of its quads, and its "
                f"outline could not be fetched to choose again; the installed quads are kept"
            )
            return recorded
        if recorded.bound == "all" and all(q.secoord in listed for q in recorded.quads):
            return RegionSheets(
                region, slug, bound.select([listed[q.secoord] for q in recorded.quads]), token
            )
        raise


def resolve_fstopo(
    regions: Sequence[tuple[str, str]],
    *,
    installed: Path,
    index: FsIndex,
    pins: dict[int, FsPin],
    region_probe: Probe,
    gateway: GatewayProbe,
    on_outage: OnOutage | None = None,
    checks: PublisherChecks | None = None,
    bound: TopoBound = ALL,
    context: ResolutionContext | None = None,
    unit: str | None = None,
) -> tuple[FsTopoResolution, tuple[str, ...]]:
    """*regions* resolved to the FSTopo sheets they need; every sheet not
    installed is located through the gateway and sized, and checked against
    its pin's size where it has one. Every refusal is named together. A
    publisher that did not answer after the retries goes to *on_outage* and
    that item is left out (#200); without it, it is refused.

    With a *context*, a sheet the gateway cannot be asked about is answered by
    the Bunker's record (:func:`~hammunition.fstopo.recorded_sheet`), which
    never upgrades a sheet without a pin to a verified one; *unit* (the
    catalogue key) is then required. A sheet the Bunker cannot answer for,
    while a Bunker is verified, defers the whole selection. Offline the gateway
    is never asked, and no installed sheet is rechecked."""
    if context is not None and unit is None:
        raise ValueError("resolve_fstopo needs the unit name when given a context")
    refused: list[str] = []
    notes: list[str] = []
    entries: list[RegionSheets] = []
    seen: set[tuple[str, str]] = set()
    for region, slug in regions:
        if (region, slug) in seen:
            continue
        seen.add((region, slug))
        if bound.mode == "none" or not bound.wants_region(region):
            continue
        try:
            entries.append(
                region_sheets(
                    region,
                    slug,
                    installed=installed,
                    index=index,
                    probe=region_probe,
                    notes=notes,
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
    wanted = {q.secoord: q for entry in entries for q in entry.quads}
    fetch: list[FsQuadFile] = []
    deferred: list[FsQuad] = []
    current = []
    todo: list[tuple[int, FsQuad]] = []
    for secoord in sorted(wanted):
        quad = wanted[secoord]
        if (installed / f"{quad.name}{TIF}").is_file():
            current.append(quad)
        else:
            todo.append((secoord, quad))
    if context is None or not context.offline:
        recheck_installed(
            checks,
            installed.name,
            current,
            name=lambda quad: quad.name,
            path=lambda quad: installed / f"{quad.name}{TIF}",
            check=lambda quad: gateway.locate(quad.secoord),
            label="installed FSTopo sheets against the Forest Service gateway",
        )

    def located(item: tuple[int, FsQuad]) -> FsQuadFile:
        """Online: the gateway's redirect and the file's size (two requests),
        agreeing with the pin's size where there is a pin."""
        secoord, quad = item
        url, size = gateway.locate(secoord)
        pin = pins.get(secoord)
        if pin is not None and pin.size != size:
            raise FstopoError(
                f"the gateway now announces {size} bytes where the pin has "
                f"{pin.size}; the Forest Service re-issued it, so it is not fetched against "
                f"the old pin. scripts/gen_fstopo_index.py --pin {secoord} measures it again"
            )
        return FsQuadFile(quad, url, size, pin.sha256 if pin else None)

    def resolved(item: tuple[int, FsQuad]) -> FsQuadFile:
        if context is None:
            return located(item)
        assert unit is not None
        quad = item[1]
        return context.choose(
            unit,
            quad.name,
            lambda: located(item),
            lambda: recorded_sheet(quad, unit=unit, pins=pins, context=context),
        )

    outcomes = run_checks(
        todo,
        resolved,
        label="FSTopo sheets against the Forest Service gateway",
    )
    for (_secoord, quad), outcome in zip(todo, outcomes, strict=True):
        try:
            fetch.append(outcome.get())
        except PublisherUnavailable as exc:
            if context is not None and context.verified is not None:
                raise CatalogueMiss(f"{quad.name}: no complete sheet set: {exc}") from exc
            if on_outage is None:
                refused.append(f"  {quad.name}: {exc}")
            else:
                on_outage(quad.name, exc)
                deferred.append(quad)
        except (FstopoError, OSError) as exc:
            refused.append(f"  {quad.name}: {exc}")
    if refused:
        raise FstopoError(
            f"{len(refused)} FSTopo item(s) could not be resolved and are not installed "
            f"already:\n" + "\n".join(refused) + hint_for(refused)
        )
    pinned = frozenset(s for s in wanted if s in pins)
    return (
        FsTopoResolution(tuple(entries), tuple(fetch), tuple(current), pinned, tuple(deferred)),
        tuple(notes),
    )


def installed_sheets(directory: Path, *, records: bool = False) -> FsTopoResolution:
    """The FSTopo sheets on disk, from their regions' records, offline: what
    the mosaic reads when the unit is not in the plan (it is installed by
    name only, D-068 amended 2026-10-01). Nothing is fetched or removed."""
    found: dict[int, FsQuad] = {}
    entries: list[RegionSheets] = []
    for record in sorted(directory.glob(f"*{QUADS}")):
        entry = read_sheets(record, record.stem, record.stem)
        if entry is not None:
            entries.append(entry)
        for quad in entry.quads if entry is not None else ():
            if (directory / f"{quad.name}{TIF}").is_file():
                found[quad.secoord] = quad
    # With *records*, the regions' records too: a unit that is planned must not
    # see its records removed as the records of regions no longer set.
    return FsTopoResolution(
        regions=tuple(entries) if records else (),
        current=tuple(found[s] for s in sorted(found)),
    )


def resolve_station_fstopo(
    plan: InstallPlan,
    maps: MapResolution,
    catalog_root: Path,
    *,
    prefix: Path,
    region_probe: Probe,
    gateway: GatewayProbe,
    outages: Outages | None = None,
    checks: PublisherChecks | None = None,
    bound: TopoBound | None = ALL,
    context: ResolutionContext | None = None,
) -> tuple[FsTopoResolution, tuple[str, ...]]:
    """The plan's FSTopo sheets, or nothing when it holds no ``usfs-fstopo``
    unit. A missing index or a malformed pins file is refused by name."""
    unit = _planned_topo(plan, "usfs-fstopo")
    if unit is None:
        # Not planned: the mosaic still draws the sheets installed by name.
        mosaic = next(
            (
                p.block.install
                for p in plan.packages
                if isinstance(p.block.install, DerivedDataInstall)
                and p.block.install.converter == "ustopo-mosaic"
                and p.block.install.fstopo
            ),
            None,
        )
        if mosaic is None or mosaic.fstopo is None:
            return FsTopoResolution(), ()
        return installed_sheets(data_root(prefix) / mosaic.fstopo), ()
    if bound is None:
        return installed_sheets(data_root(prefix) / unit.name, records=True), ()
    index = load_fstopo_index(catalog_root / FSTOPO_INDEX)
    pins = load_fstopo_pins(catalog_root / FSTOPO_PINS)
    regions = [(f.region, f.slug) for f in maps.files]
    ours = {slug for _, slug in regions}
    regions += [(k.region, k.slug) for k in maps.kept if k.slug not in ours]
    return resolve_fstopo(
        regions,
        installed=data_root(prefix) / unit.name,
        index=index,
        pins=pins,
        region_probe=region_probe,
        gateway=gateway,
        on_outage=reporter_for(outages, unit),
        checks=checks,
        bound=bound,
        context=context,
        unit=unit.name,
    )


# ---------------------------------------------------------------------------
# The size the install asks about (issue #232).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopoSize:
    """What this run's US Topo work comes to."""

    count: int
    download: int
    disk: int

    def sentence(self) -> str:
        """The one plain sentence the plan prints and the install asks about."""
        noun = "sheet" if self.count == 1 else "sheets"
        head = f"This installs {self.count:,} US Topo {noun}: about {human_size(self.download)}"
        if self.disk == self.download:
            return f"{head} to download and the same on disk."
        return (
            f"{head} to download and about {human_size(self.disk)} of disk once the warped "
            f"copies QMapShack reads are added."
        )


def topo_size(topo: TopoDisclosure | None) -> TopoSize | None:
    """The sheets this run downloads, their download and their disk with the
    warped copies; None when there is nothing to download."""
    if topo is None or not topo.resolution.fetch:
        return None
    download = sum(q.size for q in topo.resolution.fetch)
    warped = sum(warp_estimate(q.size) for q in topo.warp)
    return TopoSize(len(topo.resolution.fetch), download, download + warped)


def size_consent(topo: TopoDisclosure | None) -> TopoSize | None:
    """The size to ask a typed ``yes`` about: ``--topo-all`` is always asked
    (when there is anything to download), any other selection when the
    download and the warped copies come to more than :data:`CONSENT_BYTES`."""
    size = topo_size(topo)
    if size is None or topo is None:
        return None
    return size if topo.everything or size.disk > CONSENT_BYTES else None


# ---------------------------------------------------------------------------
# What the plan tells the operator about the bound (issue #232).
# ---------------------------------------------------------------------------


def topo_units(plan: InstallPlan, *, bare_earth: bool) -> list[PlannedPackage]:
    """The planned units the bound applies to: the US Topo and FSTopo sheets,
    and the 3DEP tiles when the station chose them."""
    found = []
    for planned in plan.packages:
        block = planned.block.install
        if isinstance(block, TopoQuadsInstall) or (
            bare_earth and isinstance(block, DemTilesInstall) and block.provider == "usgs-3dep"
        ):
            found.append(planned)
    return found


def missing_grid_deferral(unit: str) -> Deferral:
    """The deferral a unit gets when the bound needs a grid square and none is
    set (D-035: nothing is invented; what is installed stays)."""
    return Deferral(
        subject=unit,
        what="will not choose which sheets or tiles to fetch this run; what is installed is kept",
        why=(
            "the selection is bounded to a radius around your grid square "
            f"({DEFAULT_RADIUS_KM} km by default) and no grid square is set"
        ),
        remedy=(
            "`hammunition station set --grid-square <yours>`, or choose the regions with "
            "`--topo-regions a,b`, or take every sheet with `--topo-all` (its size is "
            "stated and asked first)"
        ),
        kind="package",
    )


def outside_installed(directory: Path, resolution: TopoResolution) -> int:
    """How many installed US Topo sheets the selection leaves out (and so the
    install removes), counting a newer edition of one kept as kept."""
    stems = {stem_of(q.name) for q in resolution.wanted}
    return sum(
        1 for path in directory.glob(f"*{TIF}") if stem_of(path.name[: -len(TIF)]) not in stems
    )


def selection_note(bound: TopoBound, resolution: TopoResolution, *, outside: int = 0) -> str:
    """The one line before the plan: what the bound chose and how to change it."""
    total = sum(q.size for q in resolution.wanted)
    line = (
        f"US Topo: using {bound.describe()} ({len(resolution.wanted):,} "
        f"{'sheet' if len(resolution.wanted) == 1 else 'sheets'}, about "
        f"{human_size(total)}); `hammunition station set --topo-radius-km`, "
        f"`--topo-regions` or `--topo-all` change this"
    )
    if outside:
        line += (
            f". {outside:,} installed sheets lie outside it and are removed by this install; "
            f"`--topo-all` keeps them"
        )
    return line
