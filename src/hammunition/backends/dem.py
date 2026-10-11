# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The dem-tiles backend: Copernicus GLO-30 elevation for the station's
regions.  D-061.

The tiles come from :func:`hammunition.terrain_plan.resolve_terrain`, run
before the plan prints, because each tile's size and how it is verified are
the disclosure. Each tile is fetched into the shared cache, verified by its
pinned sha256 or by its object MD5 (the step says which), installed as
``<data>/<unit>/<tile>.tif`` re-verified on the way in, and its cached copy
deleted: a tile never changes, so the installed copy is the only one worth
keeping and a cached second copy of tens of tiles is gigabytes.

``<data>/<unit>/<slug>.tiles`` records which tiles a region needs -- the
answer from its outline, kept so a later plan needs no network to know it.
A tile no region needs any more is removed, and so is the record of a
region no longer set; a kept region's (offline) are not.

One tile failing does not stop the others: it is recorded in the
:class:`~hammunition.backends.terrain.TerrainLedger`, which fails the
transaction naming it at the end. Nothing here is ever executed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from ..copernicus import CopernicusError, TileFile
from ..fetch import Fetcher, MirrorPath, fetch_disclosure, record_fetch
from ..geofabrik import RegionFile
from ..manifest.schema import DemTilesInstall, PackageManifest, RemoteArtifact
from ..topo_bound import bound_line, split_bound
from .base import Action, BackendError, Command, CommandRunner
from .data import human_size
from .fstopo import FsTopoDisclosure
from .regions import MIB, data_root, prefix_writer, removal_steps
from .terrain import TerrainLedger, tile_key
from .topo import TopoDisclosure
from .verified import PrefixWriter

#: The provider each backend serves, and how its tiles are named in steps.
COPERNICUS = "copernicus-glo30"
THREEDEP = "usgs-3dep"
_LABEL = {COPERNICUS: "terrain tile", THREEDEP: "3DEP bare-earth tile"}

TIF = ".tif"
TILES = ".tiles"
_UNPUBLISHED = "# squares with no published tile: "
#: The header records written before the final review's I1 carried. Still
#: read, so a record already on disk is not rewritten and its outline asked for
#: again; never written.
_LEGACY = "# sea squares: "


@dataclass(frozen=True)
class RegionTiles:
    """The tiles one region needs, and how many of its squares have no
    published tile: sea, or land Copernicus does not release. The tile list
    cannot say which, so neither does anything that reads this."""

    region: str
    slug: str
    tiles: tuple[str, ...]
    unpublished: int
    bound: str = "all"
    """What the selection was made under (:attr:`TopoBound.token`); only 3DEP's
    is ever bounded (issue #232), Copernicus's is always ``all``."""

    @property
    def no_terrain(self) -> bool:
        """Its outline touches squares and Copernicus publishes a tile for none."""
        return not self.tiles and self.unpublished > 0


def no_terrain_line(region: str) -> str:
    """How a region with no published tile at all is named, in the plan and
    its step alike."""
    return f"no terrain available for {region} from Copernicus GLO-30"


def no_bare_earth_line(region: str) -> str:
    """A region USGS 3DEP publishes no tile for while the station chose it."""
    return (
        f"no USGS 3DEP elevation for {region} (3DEP covers the United States); "
        f"QMapShack gets no elevation for it while dem_source is 3dep, and "
        f"Copernicus's tiles stay installed for BRouter"
    )


def _valid_name(name: str) -> bool:
    from ..usgs3dep import dem_square

    try:
        dem_square(name)
    except CopernicusError:
        return False
    return True


def render_record(entry: RegionTiles) -> str:
    return (
        f"{_UNPUBLISHED}{entry.unpublished}\n"
        + bound_line(entry.bound)
        + "".join(f"{name}\n" for name in entry.tiles)
    )


def read_record(path: Path, region: str, slug: str) -> RegionTiles | None:
    """A region's recorded tiles, or None when there is no readable record.

    A region whose outline touches no published tile records no tiles, just
    its header; that is a complete record, not an unreadable one, or it
    would be rewritten and its outline fetched again on every plan. The
    header written before I1 (``# sea squares:``) is read as the same count.
    """
    try:
        text = path.read_text()
    except OSError:
        return None
    header: int | None = None
    bound, _ = split_bound(text.splitlines())
    for line in text.splitlines():
        for prefix in (_UNPUBLISHED, _LEGACY):
            if line.startswith(prefix) and line[len(prefix) :].isdigit():
                header = int(line[len(prefix) :])
    names = [s for s in (line.strip() for line in text.splitlines()) if s and not s.startswith("#")]
    if not names:
        return None if header is None else RegionTiles(region, slug, (), header, bound)
    unpublished = header or 0
    if not all(_valid_name(name) for name in names):
        return None
    return RegionTiles(region, slug, tuple(sorted(set(names))), unpublished, bound)


@dataclass(frozen=True)
class DemResolution:
    """The station's terrain, resolved at plan time."""

    regions: tuple[RegionTiles, ...] = ()
    fetch: tuple[TileFile, ...] = ()
    """Tiles not installed yet: downloaded this run."""
    current: tuple[str, ...] = ()
    """Tiles already installed; nothing happens to them."""
    deferred: tuple[str, ...] = ()
    """Tiles a region needs that this run does not fetch because their
    publisher is not answering (#200). Still in :attr:`tiles`, so a region's
    record and every removal keep them; not in :attr:`available`, so nothing
    is drawn, converted or built over a file that will not exist."""

    @property
    def tiles(self) -> tuple[str, ...]:
        """Every tile any region needs, sorted."""
        return tuple(sorted({name for entry in self.regions for name in entry.tiles}))

    @property
    def available(self) -> tuple[str, ...]:
        """:attr:`tiles` less the deferred ones: what the derived data is made from."""
        held = set(self.deferred)
        return tuple(t for t in self.tiles if t not in held)


@dataclass(frozen=True)
class BareEarthDisclosure:
    """What the plan says about USGS 3DEP (D-068, amended 2026-10-01)."""

    resolution: DemResolution
    licence: str
    licence_url: str
    chosen: bool
    """Whether the station's ``dem_source`` is ``3dep``; when it is not,
    nothing is fetched and any installed tile is removed."""


@dataclass(frozen=True)
class TerrainDisclosure:
    """What the plan says about piece 2, split the way it happens (D-061)."""

    resolution: DemResolution
    licence: str
    licence_url: str
    garmin: Sequence[RegionFile] = ()
    """Regions mkgmap builds a Garmin map from this run."""
    routino_regions: int = 0
    """How many regions the Routino database is rebuilt over; 0 when current."""
    routino_total: int = 0
    """The sum of those regions' ``.osm.pbf`` sizes."""
    contours: int = 0
    """How many tiles have contours drawn this run."""
    drawing: bool = False
    """Whether ``gdal-dem`` has anything to do this run: contours, or its
    two virtual rasters rebuilt because the set of tiles changed."""
    brouter_regions: int = 0
    """How many regions BRouter's routing files are rebuilt over; 0 when
    current (D-063)."""
    brouter_total: int = 0
    """The sum of those regions' ``.osm.pbf`` sizes."""
    brouter_tiles: int = 0
    """How many terrain tiles the rebuild folds in as elevation."""
    brouter_squares: int = 0
    """How many 5-degree squares those tiles fall in."""
    topo: TopoDisclosure | None = None
    """The US Topo sheets and their mosaic (D-068); None when neither unit
    is planned."""
    bare_earth: BareEarthDisclosure | None = None
    """USGS 3DEP (D-068, amended 2026-10-01); None when no 3DEP unit is planned."""
    fstopo: FsTopoDisclosure | None = None
    """The FSTopo sheets and their map (D-068, amended 2026-10-01); None
    when neither the unit nor the mosaic's ``fstopo`` input is planned."""
    elevation: str = COPERNICUS
    """The provider QMapShack's contours are drawn from this run."""
    splat_tiles: int = 0
    """How many tiles get SPLAT's terrain made this run (D-061, amended
    2026-10-02)."""
    splat_building: bool = False
    """Whether ``splat-sdf`` has anything to do: files to make or remove."""


@dataclass(frozen=True)
class DemTilesBackend:
    """Turns a ``dem-tiles`` block and the resolved tiles into steps."""

    fetcher: Fetcher
    prefix: Path
    resolution: DemResolution
    keep: frozenset[str] = frozenset()
    """Slugs kept as installed because a newer map could not be checked for."""
    ledger: TerrainLedger = field(default_factory=TerrainLedger)
    runner: CommandRunner | None = None
    euid: int | None = None
    privileged: bool | None = None
    provider: str = COPERNICUS
    """The ``dem-tiles`` provider this backend's resolution is for."""
    bare_earth: DemTilesBackend | None = None
    """The USGS 3DEP backend a ``usgs-3dep`` block is handed to (D-068,
    amended 2026-10-01), so ``execute.commands_for`` keeps one ``dem``."""
    method = "dem-tiles"

    @property
    def writer(self) -> PrefixWriter:
        return prefix_writer(self.prefix, self.privileged, self.runner, self.euid)

    def data_dir(self, manifest: PackageManifest) -> Path:
        return data_root(self.prefix) / manifest.name

    def cache_path(self, tile: TileFile) -> Path:
        if tile.sha256 is not None:
            return self.fetcher.path_for(RemoteArtifact(url=tile.url, sha256=tile.sha256))
        if tile.etag is not None:
            return self.fetcher.etag_path_for(tile.url, tile.etag)
        return self.fetcher.md5_path_for(tile.url, tile.md5 or "")

    def steps(self, manifest: PackageManifest, block: DemTilesInstall) -> list[Action | Command]:
        if block.provider != self.provider:
            other = self.bare_earth
            if other is None or other.provider != block.provider:
                raise BackendError(
                    f"{manifest.name} installs {block.provider} elevation tiles and no "
                    f"backend for that provider was supplied. Skipping it would report a "
                    f"successful run that installed nothing."
                )
            return other.steps(manifest, block)
        out = self.data_dir(manifest)
        label = _LABEL.get(self.provider, "terrain tile")
        writer = self.writer
        steps: list[Action | Command] = []
        for tile in self.resolution.fetch:
            fetched: dict[str, str | Path] = {}
            facts: dict[str, str] = {}
            sources: tuple[str, ...]
            where = MirrorPath(manifest.name, tile.name)
            if tile.etag is not None:
                # The S3 ETag (D-068), checked either way (D-070, #381).
                digest = f"ETag {tile.etag}"
                check = "ETag"
            else:
                digest = (
                    f"sha256 {tile.sha256[:12]}…"
                    if tile.sha256
                    else f"md5 {(tile.md5 or '')[:12]}…"
                )
                check = "sha256" if tile.sha256 else "md5"
            note, urls, sources = fetch_disclosure(self.fetcher, tile.url, where, check)
            steps.append(
                Action(
                    kind="fetch",
                    description=(
                        f"Fetch {label} {tile.name} ({human_size(tile.size)}, "
                        f"{block.licence}) — {tile.verified_by}{note}"
                    ),
                    detail=f"{urls} ({digest}, {tile.size} bytes)",
                    perform=partial(self._fetch, tile, fetched, where, facts),
                    sources=sources,
                    facts=facts,
                )
            )
            dest = out / f"{tile.name}{TIF}"
            steps.append(
                Action(
                    kind="install-data",
                    description=(
                        f"Install {label} {tile.name}, then delete its cached copy "
                        f"{self.cache_path(tile)} (a tile never changes)"
                    ),
                    # The destination, verbatim, for uninstall's attribution replay.
                    detail=str(dest),
                    perform=partial(self._install, tile, fetched, dest, writer),
                    requires_root=writer.privileged,
                    # What the log attributes (#197): the size a later plan
                    # compares with the file before it trusts the attribution.
                    facts={"size": str(tile.size)},
                )
            )
        for entry in self.resolution.regions:
            record = out / f"{entry.slug}{TILES}"
            if read_record(record, entry.region, entry.slug) == entry:
                continue
            description = (
                f"Record that {no_bare_earth_line(entry.region)}"
                if entry.no_terrain and self.provider == THREEDEP
                else f"Record that {no_terrain_line(entry.region)}: Copernicus publishes no "
                f"tile for any of its {entry.unpublished} square(s) (sea, or land it does "
                f"not release), so nothing is installed for it; its maps still are"
                if entry.no_terrain
                else f"Record the {len(entry.tiles)} {label}(s) {entry.region} needs, "
                f"so a later plan knows them offline"
            )
            steps.append(
                Action(
                    kind="install-data",
                    description=description,
                    detail=str(record),
                    perform=partial(self._record, entry, record, writer),
                    requires_root=writer.privileged,
                )
            )
        slugs = {entry.slug for entry in self.resolution.regions} | set(self.keep)
        steps.extend(removal_steps(out, TILES, slugs, writer))
        steps.extend(removal_steps(out, TIF, set(self.resolution.tiles), writer))
        return steps

    @staticmethod
    def _record(entry: RegionTiles, record: Path, writer: PrefixWriter) -> str:
        writer.write_text(record, render_record(entry))
        if entry.no_terrain:
            return f"wrote {record}; {no_terrain_line(entry.region)}"
        return f"wrote {record}"

    def _fetch(
        self,
        tile: TileFile,
        fetched: dict[str, str | Path],
        where: MirrorPath | None = None,
        facts: dict[str, str] | None = None,
    ) -> str:
        key = tile_key(tile.name)
        try:
            if tile.sha256 is not None:
                result = self.fetcher.fetch(
                    RemoteArtifact(url=tile.url, sha256=tile.sha256),
                    max_bytes=tile.size + MIB,
                    mirror=where,
                )
                how = f"sha256 {result.sha256[:12]}… verified against the pin"
            elif tile.etag is not None:
                result = self.fetcher.fetch_etag(
                    tile.url, tile.etag, expected_size=tile.size, mirror=where
                )
                how = f"ETag {tile.etag} reproduced (the publisher's, not pinned)"
            elif tile.md5 is not None:
                result = self.fetcher.fetch_md5(
                    tile.url, tile.md5, expected_size=tile.size, mirror=where
                )
                how = f"md5 {tile.md5[:12]}… matched the object's metadata (not pinned)"
            else:  # pragma: no cover - resolve_tile always sets one
                raise BackendError(f"{tile.url}: neither a sha256 pin nor an MD5 to verify it by")
            if result.size != tile.size:
                raise BackendError(
                    f"{tile.url}: {tile.size} bytes were expected and {result.size} arrived"
                )
        except (BackendError, OSError) as exc:
            return self.ledger.fail(key, f"{tile.name}: {exc}")
        fetched["path"] = result.path
        fetched["sha256"] = result.sha256
        source = record_fetch(
            result, facts if facts is not None else {}, mirrored=bool(self.fetcher.mirror)
        )
        state = "cached" if result.from_cache else "downloaded"
        return f"{state} {result.size} bytes, {how}{source}"

    def _install(
        self, tile: TileFile, fetched: dict[str, str | Path], dest: Path, writer: PrefixWriter
    ) -> str:
        path = fetched.get("path")
        if tile_key(tile.name) in self.ledger.failed or not isinstance(path, Path):
            return f"skipped: {tile.name} did not verify"
        if tile.sha256:
            algorithm, digest = "sha256", tile.sha256
        elif tile.etag:
            # Re-verified on the way in against the sha256 taken as the bytes
            # arrived, after the ETag matched: the cache is the operator's.
            algorithm, digest = "sha256", str(fetched.get("sha256", ""))
        else:
            algorithm, digest = "md5", tile.md5 or ""
        try:
            writer.install_verified(path, dest, algorithm=algorithm, digest=digest)
        except (BackendError, OSError) as exc:
            return self.ledger.fail(tile_key(tile.name), f"{tile.name}: {exc}")
        path.unlink(missing_ok=True)
        return f"installed {dest} ({human_size(tile.size)}); deleted the cached copy"
