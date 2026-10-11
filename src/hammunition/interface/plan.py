# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The plan as data: what ``install`` and ``uninstall`` will do.  D-059.

:func:`build_install_view` turns a resolved :class:`~hammunition.plan.InstallPlan`
and its steps into an :class:`InstallPlanView`; :func:`render_plan_view` prints
it, byte for byte what ``render_plan`` printed before the view existed (the
golden text test holds that); ``install --dry-run --json`` emits it inside a
:class:`PlanDocument`. The removal side is the same shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from hammunition import acma
from hammunition.attributed import CheckLine
from hammunition.backends import Action
from hammunition.backends.data import human_size
from hammunition.backends.dem import (
    BareEarthDisclosure,
    TerrainDisclosure,
    no_bare_earth_line,
    no_terrain_line,
)
from hammunition.backends.fstopo import FsTopoDisclosure, no_sheets_line
from hammunition.backends.regions import ESTIMATE, MapDisclosure, bin_estimate
from hammunition.backends.terrain import (
    BROUTER_FACTOR,
    FSTOPO_FACTOR,
    FSTOPO_MEASURED,
    GARMIN_FACTOR,
    MEASURED,
    ROUTINO_FACTOR,
    SDF_BYTES,
    SDF_MEASURED,
    SDF_SCRATCH_BYTES,
    WARP_FACTOR,
    brouter_estimate,
    contour_bytes,
    contour_scratch,
    garmin_estimate,
    routino_estimate,
)
from hammunition.backends.topo import TopoDisclosure, no_quads_line
from hammunition.backends.topo_mosaic import MEASURED as TOPO_MEASURED
from hammunition.backends.topo_mosaic import fstopo_estimate, warp_estimate
from hammunition.consent import repo_env_var
from hammunition.desktop import Desktop, describe_set
from hammunition.execute import Step
from hammunition.geofabrik import PINNED, RegionFile
from hammunition.interface.envelope import Strict, TargetView, described
from hammunition.interface.plan_group import render_steps
from hammunition.interface.text import wrap
from hammunition.manifest.schema import (
    AptInstall,
    BinaryInstall,
    DataInstall,
    DemTilesInstall,
    DerivedDataInstall,
    GitInstall,
    NodeInstall,
    RegionalDataInstall,
    RegisterInstall,
    SourceInstall,
    TopoQuadsInstall,
    VenvInstall,
)
from hammunition.plan import Blocker, InstallPlan, PlannedPackage
from hammunition.rig import elide_serial
from hammunition.state import RemovalPlan
from hammunition.sudo_ticket import KEEPALIVE_INTERVAL, keepalive_wanted
from hammunition.topo_plan import size_consent

__all__ = [
    "PlanDocument",
    "build_install_view",
    "build_removal_view",
    "plan_state",
    "refused_plan",
    "render_plan_view",
    "render_removal_view",
]

NOT_REVERSED = (
    "Not reversed, by design: dependencies apt pulled in (run "
    "`sudo apt autoremove` to clear orphans), group memberships, and any "
    "config files written — all recorded in the transaction log (D-004). "
    "File capabilities set by Hammunition are cleared before their binaries are removed."
)


def plan_state(
    planned: PlannedPackage,
    built: frozenset[str] = frozenset(),
    maps: MapDisclosure | None = None,
    terrain: TerrainDisclosure | None = None,
    idle: frozenset[str] = frozenset(),
) -> str:
    """What the plan will do to this unit, in two words.

    "already installed" is apt's answer and only apt's: it means every apt
    package the block names is present. A source or binary unit's apt list is
    its build dependencies, or nothing at all, so for those it was saying
    "already installed" one line above a build -- sdrangel's .deb block on
    the Ubuntu 26.04 VM read that way (2026-09-02). The one carve-out is a
    vendor .deb the plan has attributed to this engine and dpkg still holds
    (#63): nothing is planned for it, and the line says so.

    The map units answer from the map disclosure, so this line and the Map
    regions section cannot disagree (bench, 2026-09-28): ``osm-regions`` is
    "already installed" when every selected region is installed and current,
    nothing to fetch and none that could not be checked; ``osm-navit`` when,
    on top of that, no map is left to convert. With no disclosure the plan
    knows nothing about the regions, and the method's wording stands.

    Piece 2's units (D-061) answer from the terrain disclosure the same way:
    ``dem-copernicus`` when no tile is fetched, each converter when it has
    nothing to build this run.
    """
    method = planned.block.install
    if isinstance(method, DerivedDataInstall) and planned.name in idle:
        # A phone unit (D-067) with no region to build and its tool current.
        return "already installed"
    if maps is not None and (maps.current or maps.kept or maps.fetch):
        regions_current = not maps.fetch and not maps.kept
        if isinstance(method, RegionalDataInstall) and regions_current:
            return "already installed"
        if (
            isinstance(method, DerivedDataInstall)
            and method.converter == "navit-maptool"
            and regions_current
            and not maps.convert
        ):
            return "already installed"
    if terrain is not None:
        if isinstance(method, DemTilesInstall):
            # Each provider answers from its own resolution (D-068, amended
            # 2026-10-01): 3DEP's is empty while Copernicus is chosen.
            bare = terrain.bare_earth
            fetching = (
                (bare.resolution.fetch if bare is not None else ())
                if method.provider == "usgs-3dep"
                else terrain.resolution.fetch
            )
            if not fetching:
                return "already installed"
        topo, fstopo = terrain.topo, terrain.fstopo
        if isinstance(method, TopoQuadsInstall):
            sheets = (
                (fstopo.resolution.fetch if fstopo is not None else ())
                if method.provider == "usfs-fstopo"
                else (topo.resolution.fetch if topo is not None else ())
            )
            if (topo is not None or fstopo is not None) and not sheets:
                return "already installed"
        if (
            (topo is not None or fstopo is not None)
            and isinstance(method, DerivedDataInstall)
            and method.converter == "ustopo-mosaic"
            and not (topo is not None and topo.building)
            and not (fstopo is not None and fstopo.building)
        ):
            return "already installed"
        if isinstance(method, DerivedDataInstall):
            quiet = {
                "mkgmap": not terrain.garmin,
                "routino-planetsplitter": not terrain.routino_regions,
                "gdal-dem": not terrain.drawing,
                "brouter-mapcreator": not terrain.brouter_regions,
                "splat-sdf": not terrain.splat_building,
            }
            if quiet.get(method.converter, False):
                return "already installed"
    if isinstance(method, AptInstall):
        return "already installed" if not planned.outstanding else "will install"
    if isinstance(method, BinaryInstall) and planned.deb_installed:
        return "already installed"
    if planned.name in built:
        return "already installed"  # built at this pin, D-051
    if isinstance(method, SourceInstall | GitInstall):
        return "will build"
    if isinstance(method, VenvInstall):
        return "will install"  # into its own venv, reported by the venv step
    if isinstance(method, NodeInstall):
        return "will build"
    if isinstance(method, DerivedDataInstall):
        return "will convert"
    return "will fetch+install"


@dataclass(frozen=True)
class AptLine(Strict):
    """One apt package a unit resolves to."""

    package: str = described("the apt package name")
    outstanding: bool = described("not installed yet; `+` in the text, `=` when already present")
    build_only: bool = described("a build dependency, not the software asked for")


@dataclass(frozen=True)
class PackageLine(Strict):
    """One catalog unit in the plan."""

    name: str = described("the catalog unit")
    method: str = described("the install method of the block that resolved here")
    state: str = described(
        "`will install`, `will build`, `will fetch+install`, `will convert` or `already installed`; "
        "a map unit reads `already installed` only when the map section says nothing is left to do"
    )
    requested_by: tuple[str, ...] = described(
        "`requested`, or the profiles and units that pulled it in"
    )
    apt: tuple[AptLine, ...] = described(
        "the apt packages it resolves to, build dependencies included"
    )


@dataclass(frozen=True)
class DisplacedLine(Strict):
    """An installed distribution package a unit displaces or shadows (D-022)."""

    package: str = described("the distribution package, which stays installed")
    declared_by: str = described("the unit whose manifest declares the conflict")


@dataclass(frozen=True)
class ReleaseSection(Strict):
    """apt packages taken from another release this machine installs from (D-038)."""

    release: str = described("the `--target-release` the apt step runs with")
    packages: tuple[str, ...] = described("the packages that come from that release")


@dataclass(frozen=True)
class NoRecommendsSection(Strict):
    """apt packages installed by a second command without Recommends (D-052)."""

    units: tuple[str, ...] = described("the units whose manifests asked for it")
    packages: tuple[str, ...] = described("the packages that second command installs")


@dataclass(frozen=True)
class RepoLine(Strict):
    """A third-party apt repository the transaction adds, behind its own gate (D-040)."""

    name: str = described("the repository's name, which names its two files")
    unit: str = described("the unit that needs it")
    packages: tuple[str, ...] = described("the apt packages it is expected to supply")
    uri: str = described("the archive URI")
    suites: tuple[str, ...] = described("apt suites")
    components: tuple[str, ...] = described("apt components")
    key_fingerprint: str = described("the pinned signing-key fingerprint")
    sources: str = described("the .sources file written")
    keyring: str = described("the keyring file written")
    consent_env_var: str = described("must equal the key fingerprint for a scripted run")


@dataclass(frozen=True)
class DataArtifactLine(Strict):
    """One file of an offline dataset."""

    url: str = described("where it is fetched from")
    size: int = described(
        "bytes, as declared and verified on fetch; for a `register` block the size "
        "measured when the unit was written, since the file changes daily (`approximate`)"
    )
    size_human: str = described("the size as the text prints it")


@dataclass(frozen=True)
class DataLine(Strict):
    """An offline-data unit: sizes and licence, before anything downloads (D-049)."""

    unit: str = described("the data unit")
    total_size: int = described("bytes, every artifact together")
    total_human: str = described("the total as the text prints it")
    licence: str = described("the licence the data is under")
    licence_url: str = described("where that licence is stated")
    artifacts: tuple[DataArtifactLine, ...] = described("each file fetched")
    installs_under: str = described("where it is installed, relative to the prefix")
    verified_by: str = described(
        "how the download is checked: `sha256, pinned by Hammunition` for a `data` block; "
        "for a `register` block (D-074, amended 2026-10-01) a sentence starting "
        "`unverified:` that says what is checked instead"
    )
    approximate: bool = described(
        "the sizes are a measurement of a file that changes, not a declaration the fetch "
        "checks (a `register` block)"
    )


@dataclass(frozen=True)
class RegionLine(Strict):
    """One map region file."""

    region: str = described("the Geofabrik region path")
    snapshot: str = described("the dated snapshot")
    size: int = described("bytes")
    size_human: str = described("the size as the text prints it")
    verified_by: str = described("how the download is checked")
    nothing_to_do: bool = described("already installed and not being converted")


@dataclass(frozen=True)
class ConvertLine(Strict):
    """A region converted for Navit this run."""

    region: str = described("the Geofabrik region path")
    snapshot: str = described("the dated snapshot")
    estimate: int = described("bytes the converted map is estimated to take")
    estimate_human: str = described("that estimate as the text prints it")
    countries: tuple[str, ...] = described(
        "ISO 3166-1 alpha-2 codes whose closed border is merged into the region first; "
        "empty when none is known or there is no boundary file"
    )
    converter_changed: bool = described(
        "converted again only because an older converter built the installed map"
    )


@dataclass(frozen=True)
class BoundaryLine(Strict):
    """The country-border file merged into each region before conversion."""

    title: str = described("what the file is")
    url: str = described("where it is fetched from")
    size: int = described("bytes, as declared and verified on fetch")
    size_human: str = described("the size as the text prints it")
    licence: str = described("the licence the data is under")
    verified_by: str = described("how the download is checked")


@dataclass(frozen=True)
class KeptLine(Strict):
    """An installed region that could not be checked for a newer map; kept."""

    region: str = described("the Geofabrik region path")
    snapshot: str | None = described("the installed snapshot, when recorded")
    reason: str = described("why it could not be checked")


@dataclass(frozen=True)
class TerrainRegionLine(Strict):
    """The terrain tiles one region needs (D-061)."""

    region: str = described("the Geofabrik region path")
    tiles: int = described("tiles that exist for its outline")
    unpublished: int = described(
        "squares of its outline Copernicus publishes no tile for: sea, or land it does not "
        "release; the tile list cannot say which"
    )
    no_terrain: bool = described(
        "true when its outline touches squares and every one is unpublished: no terrain is "
        "installed for this region, and the plan warns so; its maps still install"
    )
    download: int = described(
        "bytes of its tiles downloaded this run; a tile two regions share counts in both"
    )
    download_human: str = described("as the text prints it")


@dataclass(frozen=True)
class TileLine(Strict):
    """One terrain tile downloaded this run."""

    tile: str = described("the Copernicus GLO-30 tile name; it encodes a latitude and longitude")
    size: int = described("bytes")
    size_human: str = described("the size as the text prints it")
    verified_by: str = described("how the download is checked")


@dataclass(frozen=True)
class PublisherCheckLine(Strict):
    """What the plan did about one installed data item's publisher (#197)."""

    unit: str = described("the data unit the item belongs to")
    item: str = described("the tile, sheet, book or map")
    checked: bool = described(
        "whether the plan asked the item's publisher; false for an item the log attributes "
        "as installed less than seven days ago, and for one on disk the log does not attribute"
    )
    reason: str = described(
        "why, in a sentence; a failed re-check says so and that the copy is kept"
    )
    attributed: str | None = described(
        "the date of the attribution in the log (YYYY-MM-DD); null when the log has none"
    )


@dataclass(frozen=True)
class GarminLine(Strict):
    """A region mkgmap builds a Garmin map from this run."""

    region: str = described("the Geofabrik region path")
    snapshot: str = described("the dated snapshot")
    estimate: int = described("bytes the map is estimated to take")
    estimate_human: str = described("that estimate as the text prints it")


@dataclass(frozen=True)
class TopoRegionLine(Strict):
    """The US Topo quads one region needs (D-068)."""

    region: str = described("the Geofabrik region path")
    quads: int = described("quads whose box its outline touches")
    size: int = described("bytes of all its quads, installed or not")
    size_human: str = described("as the text prints it")
    download: int = described(
        "bytes of its quads downloaded this run; a quad two regions share counts in both"
    )
    download_human: str = described("as the text prints it")


@dataclass(frozen=True)
class QuadLine(Strict):
    """One US Topo quad downloaded this run."""

    quad: str = described("the quad's file name without .tif: state, map name and edition date")
    size: int = described("bytes")
    size_human: str = described("the size as the text prints it")
    verified_by: str = described("how the download is checked")


@dataclass(frozen=True)
class TopoSectionView(Strict):
    """USGS US Topo sheets and QMapShack's mosaic of them (D-068). Local only."""

    regions: tuple[TopoRegionLine, ...] = described("quads per region")
    no_quads: tuple[str, ...] = described(
        "regions no US Topo quad covers (outside the United States); nothing is fetched for them"
    )
    fetch: tuple[QuadLine, ...] = described("quads downloaded this run")
    current: int = described("quads already installed")
    licence: str = described("the sheets' licence")
    licence_url: str = described("where it is stated")
    download_total: int = described("bytes of quads downloaded")
    download_total_human: str = described("as the text prints it")
    warp: int = described("quads warped for QMapShack this run")
    warp_estimate: int = described("bytes the warped quads are estimated to take")
    warp_estimate_human: str = described("as the text prints it")
    disk_total: int = described("bytes: the downloads plus the warped quads")
    disk_total_human: str = described("as the text prints it")
    estimate_note: str = described("how the estimate was measured")
    selection: str = described(
        "how the station's bound chose the sheets: a radius around the grid square (the "
        "default), the --topo-regions, or every sheet (--topo-all); empty when not known"
    )
    size_consent: str | None = described(
        "the one sentence the install asks a typed yes about, which --yes does not answer: "
        "set when --topo-all is chosen or the download and warped copies exceed 10 GB; "
        "null otherwise"
    )


@dataclass(frozen=True)
class BareEarthSectionView(Strict):
    """USGS 3DEP bare-earth elevation (D-068, amended 2026-10-01). Local only."""

    chosen: bool = described(
        "whether the station's dem_source is 3dep; when false nothing is fetched, any "
        "installed 3DEP tile is removed, and QMapShack's elevation is Copernicus's"
    )
    regions: tuple[TerrainRegionLine, ...] = described(
        "3DEP tiles per region and what each downloads this run (about ten times Copernicus)"
    )
    fetch: tuple[TileLine, ...] = described("3DEP tiles downloaded this run")
    current: int = described("3DEP tiles already installed")
    licence: str = described("the elevation data's licence")
    licence_url: str = described("where it is stated")
    download_total: int = described("bytes of 3DEP tiles downloaded")
    download_total_human: str = described("as the text prints it")


@dataclass(frozen=True)
class SheetRegionLine(Strict):
    """The FSTopo quads one region needs (D-068, amended 2026-10-01)."""

    region: str = described("the Geofabrik region path")
    quads: int = described("quads whose box its outline touches")
    download: int = described(
        "bytes of its quads downloaded this run; a quad two regions share counts in both"
    )
    download_human: str = described("as the text prints it")
    all_pinned: bool = described(
        "every quad it needs has a sha256 pinned by Hammunition, so none is unverified"
    )


@dataclass(frozen=True)
class FsTopoSectionView(Strict):
    """Forest Service FSTopo sheets and QMapShack's FSTopo map (D-068,
    amended 2026-10-01). Local only."""

    regions: tuple[SheetRegionLine, ...] = described("quads per region")
    no_quads: tuple[str, ...] = described(
        "regions no FSTopo quad covers (no National Forest land); nothing is fetched for them"
    )
    fetch: tuple[QuadLine, ...] = described("quads downloaded this run")
    unverified: int = described(
        "of those, quads fetched with no checksum: the Forest Service publishes none and "
        "Hammunition has pinned none"
    )
    current: int = described("quads already installed")
    licence: str = described("the sheets' licence")
    licence_url: str = described("where it is stated")
    download_total: int = described("bytes of quads downloaded")
    download_total_human: str = described("as the text prints it")
    convert: int = described("quads converted to tiled RGB for QMapShack this run")
    convert_estimate: int = described("bytes the converted quads are estimated to take")
    convert_estimate_human: str = described("as the text prints it")
    disk_total: int = described("bytes: the downloads plus the converted quads")
    disk_total_human: str = described("as the text prints it")
    estimate_note: str = described("how the estimate was measured")


@dataclass(frozen=True)
class TerrainSectionView(Strict):
    """Terrain, and what is built for QMapShack (D-061). Names where the operator is: local only."""

    regions: tuple[TerrainRegionLine, ...] = described("tiles per region")
    fetch: tuple[TileLine, ...] = described("tiles downloaded this run")
    current: int = described("tiles already installed")
    licence: str = described("the elevation data's licence")
    licence_url: str = described("where it is stated")
    download_total: int = described("bytes of tiles downloaded")
    download_total_human: str = described("as the text prints it")
    garmin: tuple[GarminLine, ...] = described("Garmin maps built this run")
    routino_regions: int = described("regions the Routino database is rebuilt over; 0 when current")
    routino_estimate: int = described("bytes the rebuilt database is estimated to take")
    routino_estimate_human: str = described("as the text prints it")
    contours: int = described("tiles whose contours are drawn this run")
    contours_estimate: int = described("bytes those contours are estimated to take")
    contours_estimate_human: str = described("as the text prints it")
    brouter_regions: int = described(
        "regions BRouter's routing files are rebuilt over; 0 when current (D-063)"
    )
    brouter_tiles: int = described("terrain tiles folded into them as elevation")
    brouter_estimate: int = described("bytes the rebuilt routing files are estimated to take")
    brouter_estimate_human: str = described("as the text prints it")
    disk_total: int = described("bytes: the tiles plus everything estimated to be built")
    disk_total_human: str = described("as the text prints it")
    estimate_note: str = described("how the estimates were measured")
    topo: TopoSectionView | None = described(
        "USGS US Topo quads and their mosaic (D-068); null when neither unit is planned"
    )
    contours_from: str = described(
        "the provider the contours and QMapShack's elevation are drawn from this run: "
        "`copernicus-glo30`, or `usgs-3dep` when the station chose it (D-068, amended 2026-10-01)"
    )
    bare_earth: BareEarthSectionView | None = described(
        "USGS 3DEP (D-068, amended 2026-10-01); null when no 3DEP unit is planned"
    )
    fstopo: FsTopoSectionView | None = described(
        "Forest Service FSTopo quads and their map (D-068, amended 2026-10-01); null when "
        "neither is planned"
    )
    splat_tiles: int = described(
        "tiles SPLAT's terrain (SDF files) is made for this run, for SPLAT! and Signal-Server "
        "(D-061, amended 2026-10-02)"
    )
    splat_estimate: int = described("bytes those files are estimated to take")
    splat_estimate_human: str = described("as the text prints it")


@dataclass(frozen=True)
class MapSectionView(Strict):
    """The station's map regions (D-057). Names where the operator is: local only."""

    fetch: tuple[RegionLine, ...] = described("downloaded and installed this run")
    current: tuple[RegionLine, ...] = described("already installed at the resolved snapshot")
    convert: tuple[ConvertLine, ...] = described("converted for Navit this run")
    kept: tuple[KeptLine, ...] = described("could not be checked; the installed copy stays")
    licence: str = described("the map data's licence")
    licence_url: str = described("where it is stated")
    download_total: int = described("bytes downloaded")
    download_total_human: str = described("as the text prints it")
    disk_total: int = described("bytes: the download plus the estimated converted maps")
    disk_total_human: str = described("as the text prints it")
    estimate_note: str = described("how the conversion estimate was measured")
    terrain: TerrainSectionView | None = described(
        "terrain tiles and QMapShack's maps (D-061); null when no terrain unit is planned"
    )
    boundaries: BoundaryLine | None = described(
        "the country-border file merged into each region with osmium merge before "
        "maptool; null when the converter has none"
    )
    unknown_country: bool = described(
        "maptool runs with -U: a town outside every country boundary is indexed under "
        "the pseudo-country Unknown instead of being dropped"
    )


@dataclass(frozen=True)
class MembershipLine(Strict):
    """A group the operator is added to, and what it grants."""

    user: str = described("the account added")
    group: str = described("the group")
    package: str = described("the unit that needs it")
    detail: str = described("what membership grants")
    reverse_hint: str | None = described("how to undo it by hand, when the manifest says")


@dataclass(frozen=True)
class FileCapabilityLine(Strict):
    """A capability grant to one installed binary."""

    unit: str = described("the catalog unit")
    path: str = described("the installed binary receiving capabilities")
    capabilities: tuple[str, ...] = described(
        "Linux capabilities set with permitted/effective flags"
    )
    detail: str = described("why the capability grant is available")


@dataclass(frozen=True)
class GateLine(Strict):
    """A consent gate the real run will present (D-021). Never answered through JSON."""

    profile: str = described("the gated profile or optional system change")
    env_var: str = described("the scripted-consent variable the gate reads")
    risk_lines: tuple[str, ...] = described("one line per disclosed capability")


@dataclass(frozen=True)
class ConfigLine(Strict):
    """A configuration file the transaction writes."""

    unit: str = described("the unit whose manifest templates it")
    path: str = described("the file written")
    mode: str = described("its octal mode")
    append: bool = described("appended to rather than written")
    backup_existing: bool = described("an existing file is backed up first")
    fills: tuple[str, ...] = described(
        "the station values templated into it, by name (callsign, grid_square, "
        "ax25_callsign, latitude ...); never the values themselves"
    )


@dataclass(frozen=True)
class UserServiceLine(Strict):
    """A systemd user service the transaction writes and enables (D-073 §6b).

    The command line is rendered with the device serial elided, and the
    station values are named, never quoted — the same privacy split as the
    rest of the plan."""

    unit: str = described("the catalog unit carrying it")
    name: str = described("the systemd user unit, without .service")
    path: str = described("the unit file written, under the operator's ~/.config/systemd/user/")
    exec: str = described("the service's command line, with the device serial elided")
    fills: tuple[str, ...] = described("the station values that fed it, by name; never the values")
    listen: str = described("the loopback address:port it binds, e.g. 127.0.0.1:4532")
    starts_now: bool = described(
        "whether the plan restarts it now (a rig service whose radio's port is present)"
    )


@dataclass(frozen=True)
class DesktopsReadView(Strict):
    """What the session files said, when a unit in the request is for particular desktops (D-060)."""

    desktops: tuple[str, ...] = described(
        "the desktops the catalog knows that the session files offer (`kde`, `xfce`, ...)"
    )
    unrecognised: tuple[str, ...] = described(
        "session files read that named no desktop the catalog knows (`cosmic.desktop`)"
    )
    summary: str = described("the line the text prints under the heading")


@dataclass(frozen=True)
class DeferralLine(Strict):
    """Part of the request that will not happen; the rest still does (D-035, D-039, D-060)."""

    kind: str = described(
        "`config` (a file not written) or `package` (a member not installed: the target lacks it, "
        "or, D-060, the machine has no session for the desktop it is for; or, #200, a publisher "
        "did not answer for some of its items after the retries)"
    )
    subject: str = described("what is deferred")
    what: str = described("what will not happen")
    why: str = described("what is missing")
    remedy: str = described("what the operator can do about it")


@dataclass(frozen=True)
class RecordsLine(Strict):
    """Where the transaction log is written."""

    log: str = described("the transaction log file")
    handed_to: str | None = described("the operator it is chowned to, under sudo")


@dataclass(frozen=True)
class SudoLine(Strict):
    """How a run as a user keeps sudo from asking twice (D-062)."""

    keepalive: bool = described(
        "true when the run validates sudo once and refreshes its ticket until the run ends; "
        "false when `--no-sudo-keepalive` turned it off"
    )
    interval_seconds: int = described("seconds between `sudo -n -v` refreshes")
    text: str = described("what the plan prints about it")


@dataclass(frozen=True)
class StepView(Strict):
    """One step, exactly as the real run performs it."""

    description: str = described("why the step runs")
    display: str = described("the line the text prints after `$`, copy-pasteable")
    argv: tuple[str, ...] = described(
        "the argv executed, escalation applied; empty for an in-process step"
    )
    action: str | None = described(
        "the in-process step's kind (`fetch`, `extract`, ...); null for a command"
    )
    requires_root: bool = described("whether it runs as root")
    sources: tuple[str, ...] = described(
        "for a data download (a `data` artifact, a map region, a terrain tile), the URLs it "
        "is fetched from in the order tried: the LAN mirror, then the publisher (D-070); "
        "the publisher alone with no mirror; the Bunker alone under `--offline`; empty for "
        "any other step"
    )
    index: int = described("this step's 1-based position in execution order")
    long_running: bool = field(
        default=False,
        metadata={
            "doc": "true when the backend knows the step can take several minutes (a submodule "
            "fetch, a compile, a venv install, a node build); the text prints one fixed note "
            "under it and claims no duration (#270)"
        },
    )


@dataclass(frozen=True)
class MirrorSection(Strict):
    """The LAN mirror a data download is asked for first (D-070)."""

    url: str = described("the mirror's base URL, from station config")
    ignored: bool = described("true when `--no-mirror` ignores it for this run")
    text: str = described("what the plan prints about it")


@dataclass(frozen=True)
class InstallPlanView(Strict):
    """Everything an install will do, section by section as the text prints it."""

    packages: tuple[PackageLine, ...] = described("every unit, in the order it installs")
    displaced: tuple[DisplacedLine, ...] = described("distribution packages displaced or shadowed")
    apt_release: ReleaseSection | None = described("present when apt resolves from another release")
    no_recommends: NoRecommendsSection | None = described(
        "present when a unit opted out of Recommends"
    )
    repos: tuple[RepoLine, ...] = described("third-party repositories added")
    mirror: MirrorSection | None = described(
        "the LAN mirror data downloads try first (D-070); null when none is set"
    )
    data: tuple[DataLine, ...] = described("offline data downloaded")
    maps: MapSectionView | None = described(
        "the station's map regions (D-057); null when no map unit or nothing to disclose"
    )
    memberships: tuple[MembershipLine, ...] = described("group membership changes")
    file_capabilities: tuple[FileCapabilityLine, ...] = described(
        "opt-in file capabilities applied to installed binaries"
    )
    consent_gates: tuple[GateLine, ...] = described("gates the real run presents")
    config_files: tuple[ConfigLine, ...] = described("configuration written")
    user_services: tuple[UserServiceLine, ...] = described(
        "systemd user services written and enabled (D-073); empty when none"
    )
    desktops_read: DesktopsReadView | None = described(
        "present when a unit in the request is for particular desktops and the session files "
        "were read (D-060); null otherwise"
    )
    deferrals: tuple[DeferralLine, ...] = described("what will NOT happen")
    notes: tuple[str, ...] = described("the plan's notes")
    records: RecordsLine | None = described("where the transaction log goes")
    sudo: SudoLine | None = described(
        "present when a run as a user mixes root steps with steps that are not (D-062); "
        "null when run as root or when sudo is needed by every step or none"
    )
    commands: tuple[StepView, ...] = described("every step, in order")
    suggestion_notes: tuple[str, ...] = described(
        "what happened to the profiles' suggestion groups; the text prints these as `note:` lines"
    )
    region_notes: tuple[str, ...] = described(
        "notes from resolving the map regions; the text prints these as `note:` lines"
    )
    publisher_checks: tuple[PublisherCheckLine, ...] = described(
        "one line per data item already on disk: whether its publisher was asked at plan "
        "time (#197); empty when the plan holds no data unit with installed items"
    )


@dataclass(frozen=True)
class UnitPackages(Strict):
    """A unit and apt packages."""

    unit: str = described("the catalog unit")
    packages: tuple[str, ...] = described("apt packages")


@dataclass(frozen=True)
class UnitFiles(Strict):
    """A unit and files."""

    unit: str = described("the catalog unit")
    paths: tuple[str, ...] = described("files on disk")


@dataclass(frozen=True)
class ArtifactLine(Strict):
    """A file or tree the removal deletes, and why it is this engine's to delete."""

    unit: str = described("the catalog unit")
    kind: str = described("`venv`, `tree`, `binary`, `wrapper`, `desktop-entry` or `apt-repo`")
    path: str = described("what is removed")
    basis: str = described("`namespaced`, `log` or `marker`: how it is known to be ours")


@dataclass(frozen=True)
class RemovalPlanView(Strict):
    """Everything an uninstall will do, section by section as the text prints it."""

    to_remove: tuple[UnitPackages, ...] = described("apt packages removed, per unit")
    artifacts: tuple[ArtifactLine, ...] = described("files and trees removed")
    left_unattributed: tuple[UnitFiles, ...] = described(
        "present, but the log does not attribute it"
    )
    left_foreign: tuple[UnitPackages, ...] = described("installed, but not by this engine")
    already_absent: tuple[UnitPackages, ...] = described("nothing to remove")
    not_reversed: str = described("what uninstall does not undo, by design (D-004)")
    commands: tuple[StepView, ...] = described("every step, in order")


@dataclass(frozen=True)
class BlockerLine(Strict):
    """One reason the transaction cannot be planned."""

    subject: str = described("what is blocked")
    reason: str = described("why")
    remedy: str | None = described("what to do about it")


@dataclass(frozen=True)
class PlanDocument(Strict):
    """The plan `install --dry-run` or `uninstall --dry-run` prints, as data.

    A refused transaction is still a `plan`, with `outcome: "refused"`, every
    blocker, and exit code 2. Includes the paths of files written for the
    operator; for local programs, not for pasting."""

    KIND: ClassVar[str] = "plan"

    action: str = described("`install` or `uninstall`")
    requested: tuple[str, ...] = described("the names given on the command line")
    outcome: str = described("`planned`, or `refused` with the blockers")
    step_count: int = described("the number of steps in this transaction; zero when refused")
    target: TargetView = described("the system planned against")
    blockers: tuple[BlockerLine, ...] = described("empty unless refused")
    install: InstallPlanView | None = described(
        "the install plan; null for an uninstall or a refusal"
    )
    removal: RemovalPlanView | None = described(
        "the removal plan; null for an install or a refusal"
    )


def describe_sessions(desktops: frozenset[Desktop], unrecognised: tuple[str, ...]) -> str:
    """One line for what the session files said (D-060): the desktops the
    catalog knows, and any file read that named none of them, so a COSMIC or
    Sway machine is not reported as having no sessions."""
    files = ", ".join(unrecognised)
    names = "names" if len(unrecognised) == 1 else "name"
    if desktops:
        line = describe_set(desktops)
        if unrecognised:
            line += f"; also {files}, which {names} no desktop the catalog knows"
        return line
    if unrecognised:
        return f"none the catalog knows (read: {files})"
    return "none (no session files)"


def _desktops_read(plan: InstallPlan) -> DesktopsReadView | None:
    if plan.desktops_read is None:
        return None
    return DesktopsReadView(
        desktops=tuple(d.value for d in Desktop if d in plan.desktops_read),
        unrecognised=tuple(plan.sessions_unrecognised),
        summary=describe_sessions(plan.desktops_read, plan.sessions_unrecognised),
    )


def step_view(step: Step, *, euid: int, index: int) -> StepView:
    if isinstance(step, Action):
        return StepView(
            description=step.description,
            display=step.display(euid=euid),
            argv=(),
            action=step.kind,
            requires_root=step.requires_root,
            sources=step.sources,
            index=index,
        )
    return StepView(
        description=step.description,
        display=step.display(euid=euid),
        argv=tuple(step.argv_for(euid=euid)),
        action=None,
        requires_root=step.requires_root,
        sources=(),
        index=index,
        long_running=step.long_running,
    )


def _topo_section(topo: TopoDisclosure | None) -> TopoSectionView | None:
    if topo is None:
        return None
    resolution = topo.resolution
    sizes = {q.path: q.size for q in resolution.fetch}
    download = sum(sizes.values())
    warped = sum(warp_estimate(q.size) for q in topo.warp)
    return TopoSectionView(
        regions=tuple(
            TopoRegionLine(
                region=r.region,
                quads=len(r.quads),
                size=sum(q.size for q in r.quads),
                size_human=human_size(sum(q.size for q in r.quads)),
                download=sum(sizes.get(q.path, 0) for q in r.quads),
                download_human=human_size(sum(sizes.get(q.path, 0) for q in r.quads)),
            )
            for r in resolution.regions
            if r.quads
        ),
        no_quads=tuple(r.region for r in resolution.regions if not r.quads),
        fetch=tuple(
            QuadLine(
                quad=q.name, size=q.size, size_human=human_size(q.size), verified_by=q.verified_by
            )
            for q in resolution.fetch
        ),
        current=len(resolution.current),
        licence=topo.licence.strip(),
        licence_url=topo.licence_url,
        download_total=download,
        download_total_human=human_size(download),
        warp=len(topo.warp),
        warp_estimate=warped,
        warp_estimate_human=human_size(warped),
        disk_total=download + warped,
        disk_total_human=human_size(download + warped),
        estimate_note=TOPO_MEASURED,
        selection=topo.selection,
        size_consent=asked.sentence() if (asked := size_consent(topo)) else None,
    )


def _bare_earth_section(bare: BareEarthDisclosure | None) -> BareEarthSectionView | None:
    if bare is None:
        return None
    resolution = bare.resolution
    sizes = {t.name: t.size for t in resolution.fetch}
    download = sum(sizes.values())
    return BareEarthSectionView(
        chosen=bare.chosen,
        regions=tuple(
            TerrainRegionLine(
                region=r.region,
                tiles=len(r.tiles),
                unpublished=r.unpublished,
                no_terrain=r.no_terrain,
                download=sum(sizes.get(name, 0) for name in r.tiles),
                download_human=human_size(sum(sizes.get(name, 0) for name in r.tiles)),
            )
            for r in resolution.regions
        ),
        fetch=tuple(
            TileLine(
                tile=t.name, size=t.size, size_human=human_size(t.size), verified_by=t.verified_by
            )
            for t in resolution.fetch
        ),
        current=len(resolution.current),
        licence=bare.licence.strip(),
        licence_url=bare.licence_url,
        download_total=download,
        download_total_human=human_size(download),
    )


def _fstopo_section(fstopo: FsTopoDisclosure | None) -> FsTopoSectionView | None:
    if fstopo is None:
        return None
    resolution = fstopo.resolution
    sizes = {f.quad.secoord: f.size for f in resolution.fetch}
    download = sum(sizes.values())
    converted = sum(fstopo_estimate(size) for size in fstopo.convert)
    return FsTopoSectionView(
        regions=tuple(
            SheetRegionLine(
                region=r.region,
                quads=len(r.quads),
                download=sum(sizes.get(q.secoord, 0) for q in r.quads),
                download_human=human_size(sum(sizes.get(q.secoord, 0) for q in r.quads)),
                all_pinned=resolution.all_pinned(r),
            )
            for r in resolution.regions
            if r.quads
        ),
        no_quads=tuple(r.region for r in resolution.regions if not r.quads),
        fetch=tuple(
            QuadLine(
                quad=f.name, size=f.size, size_human=human_size(f.size), verified_by=f.verified_by
            )
            for f in resolution.fetch
        ),
        unverified=sum(1 for f in resolution.fetch if not f.sha256),
        current=len(resolution.current),
        licence=fstopo.licence.strip(),
        licence_url=fstopo.licence_url,
        download_total=download,
        download_total_human=human_size(download),
        convert=len(fstopo.convert),
        convert_estimate=converted,
        convert_estimate_human=human_size(converted),
        disk_total=download + converted,
        disk_total_human=human_size(download + converted),
        estimate_note=FSTOPO_MEASURED,
    )


def _terrain_section(terrain: TerrainDisclosure | None) -> TerrainSectionView | None:
    if terrain is None:
        return None
    resolution = terrain.resolution
    sizes = {t.name: t.size for t in resolution.fetch}
    download = sum(sizes.values())
    garmin = [
        GarminLine(
            region=f.region,
            snapshot=f.snapshot,
            estimate=garmin_estimate(f.size),
            estimate_human=human_size(garmin_estimate(f.size)),
        )
        for f in terrain.garmin
    ]
    routino = routino_estimate(terrain.routino_total)
    contours = terrain.contours * contour_bytes(terrain.elevation)
    brouter = brouter_estimate(terrain.brouter_total)
    splat = terrain.splat_tiles * SDF_BYTES
    disk = download + sum(g.estimate for g in garmin) + routino + contours + brouter + splat
    return TerrainSectionView(
        regions=tuple(
            TerrainRegionLine(
                region=r.region,
                tiles=len(r.tiles),
                unpublished=r.unpublished,
                no_terrain=r.no_terrain,
                download=sum(sizes.get(name, 0) for name in r.tiles),
                download_human=human_size(sum(sizes.get(name, 0) for name in r.tiles)),
            )
            for r in resolution.regions
        ),
        fetch=tuple(
            TileLine(
                tile=t.name, size=t.size, size_human=human_size(t.size), verified_by=t.verified_by
            )
            for t in resolution.fetch
        ),
        current=len(resolution.current),
        licence=terrain.licence.strip(),
        licence_url=terrain.licence_url,
        download_total=download,
        download_total_human=human_size(download),
        garmin=tuple(garmin),
        routino_regions=terrain.routino_regions,
        routino_estimate=routino,
        routino_estimate_human=human_size(routino),
        contours=terrain.contours,
        contours_estimate=contours,
        contours_estimate_human=human_size(contours),
        brouter_regions=terrain.brouter_regions,
        brouter_tiles=terrain.brouter_tiles,
        brouter_estimate=brouter,
        brouter_estimate_human=human_size(brouter),
        disk_total=disk,
        disk_total_human=human_size(disk),
        estimate_note=MEASURED,
        topo=_topo_section(terrain.topo),
        contours_from=terrain.elevation,
        bare_earth=_bare_earth_section(terrain.bare_earth),
        fstopo=_fstopo_section(terrain.fstopo),
        splat_tiles=terrain.splat_tiles,
        splat_estimate=splat,
        splat_estimate_human=human_size(splat),
    )


def _map_section(
    plan: InstallPlan, maps: MapDisclosure | None, terrain: TerrainDisclosure | None = None
) -> MapSectionView | None:
    # The regions' own unit first: its licence is the map data's. A derived
    # unit can sort ahead of it and carry another (gdal-dem's is Copernicus's,
    # D-061), which the region lines would otherwise have been shown under.
    units = sorted(
        (
            p.block.install
            for p in plan.packages
            if isinstance(p.block.install, RegionalDataInstall | DerivedDataInstall)
        ),
        key=lambda block: not isinstance(block, RegionalDataInstall),
    )
    if not units or maps is None or not (maps.fetch or maps.current or maps.kept or maps.convert):
        return None
    # A current region still being converted is not "nothing to do"; nor is
    # one mkgmap builds from, or every region when Routino's database is
    # rebuilt over them all (D-061).
    converting = {f.slug for f in maps.convert}
    if terrain is not None:
        converting |= {f.slug for f in terrain.garmin}
        if terrain.routino_regions or terrain.brouter_regions:
            converting |= {f.slug for f in (*maps.fetch, *maps.current)}

    def line(f: RegionFile, *, current: bool) -> RegionLine:
        return RegionLine(
            region=f.region,
            snapshot=f.snapshot,
            size=f.size,
            size_human=human_size(f.size),
            verified_by=f.verified_by,
            nothing_to_do=current and f.slug not in converting,
        )

    total = sum(f.size for f in maps.fetch)
    disk = total + sum(bin_estimate(f.size) for f in maps.convert)
    return MapSectionView(
        fetch=tuple(line(f, current=False) for f in maps.fetch),
        current=tuple(line(f, current=True) for f in maps.current),
        convert=tuple(
            ConvertLine(
                region=f.region,
                snapshot=f.snapshot,
                estimate=bin_estimate(f.size),
                estimate_human=human_size(bin_estimate(f.size)),
                countries=tuple(maps.countries.get(f.region, ()))
                if maps.boundaries is not None
                else (),
                converter_changed=f.slug in maps.converter_changed,
            )
            for f in maps.convert
        ),
        kept=tuple(
            KeptLine(region=k.region, snapshot=k.snapshot, reason=k.reason) for k in maps.kept
        ),
        licence=units[0].licence.strip(),
        licence_url=units[0].licence_url,
        download_total=total,
        download_total_human=human_size(total),
        disk_total=disk,
        disk_total_human=human_size(disk),
        estimate_note=ESTIMATE,
        boundaries=None
        if maps.boundaries is None
        else BoundaryLine(
            title=maps.boundaries.title,
            url=maps.boundaries.url,
            size=maps.boundaries.size,
            size_human=human_size(maps.boundaries.size),
            licence=maps.boundaries.licence,
            verified_by=PINNED,
        ),
        unknown_country=True,
        terrain=_terrain_section(terrain),
    )


def _sudo_line(commands: Sequence[Step], *, euid: int, keepalive: bool) -> SudoLine | None:
    if not keepalive_wanted(commands, euid=euid):
        return None
    minutes = int(KEEPALIVE_INTERVAL // 60)
    if keepalive:
        text = (
            f"sudo's ticket is kept valid for the length of this transaction; it is not "
            f"extended beyond it. The password is asked once, by `sudo -v`, before the "
            f"first step; then `sudo -n -v`, which cannot prompt, refreshes the ticket "
            f"every {minutes} minutes from this process until the run ends. If a "
            f"refresh fails it is reported once and not retried, and the next root step "
            f"asks as it would have. --no-sudo-keepalive turns this off."
        )
    else:
        text = (
            "--no-sudo-keepalive: each step that needs root runs under its own `sudo`, "
            "and sudo may ask for the password again at any root step that follows a "
            "step outlasting its cached ticket (15 minutes by default). Do not leave "
            "the run unattended."
        )
    return SudoLine(keepalive=keepalive, interval_seconds=int(KEEPALIVE_INTERVAL), text=text)


def _mirror_section(url: str | None, *, ignored: bool) -> MirrorSection | None:
    if url is None:
        return None
    if ignored:
        text = (
            f"A LAN mirror is set in station config ({url}); --no-mirror ignores it for "
            f"this run, and every data download comes from its publisher."
        )
    else:
        text = (
            f"Each data download below (offline data, map regions, terrain tiles, CoMaps "
            f"maps, reference books) is asked "
            f"of the LAN mirror {url} first, as <mirror>/<unit>/<name>, and of its "
            f"publisher if the mirror fails in any way. The digest it is checked by is the "
            f"same whichever answers: the mirror is trusted for speed, never for content. "
            f"A mirror that does not answer at all is not asked again in this run. The "
            f"transaction log records which source each download came from."
        )
    return MirrorSection(url=url, ignored=ignored, text=text)


def build_install_view(
    plan: InstallPlan,
    commands: Sequence[Step],
    *,
    euid: int,
    log_destination: Path | None = None,
    hands_log_to: str | None = None,
    built: frozenset[str] = frozenset(),
    suggestion_notes: Sequence[str] = (),
    maps: MapDisclosure | None = None,
    region_notes: Sequence[str] = (),
    publisher_checks: Sequence[CheckLine] = (),
    terrain: TerrainDisclosure | None = None,
    sudo_keepalive: bool = True,
    mirror: str | None = None,
    mirror_ignored: bool = False,
    idle: frozenset[str] = frozenset(),
) -> InstallPlanView:
    data: list[DataLine] = []
    for planned in plan.packages:
        block = planned.block.install
        if isinstance(block, RegisterInstall):
            data.append(
                DataLine(
                    unit=planned.name,
                    total_size=acma.MEASURED_SIZE,
                    total_human=human_size(acma.MEASURED_SIZE),
                    licence=block.licence.strip(),
                    licence_url=block.licence_url,
                    artifacts=(
                        DataArtifactLine(
                            url=acma.URL,
                            size=acma.MEASURED_SIZE,
                            size_human=human_size(acma.MEASURED_SIZE),
                        ),
                    ),
                    installs_under=f"<prefix>/share/hammunition/data/{planned.name}/",
                    verified_by=acma.VERIFIED_BY,
                    approximate=True,
                )
            )
            continue
        if not isinstance(block, DataInstall):
            continue
        total = sum(a.size for a in block.artifacts)
        data.append(
            DataLine(
                unit=planned.name,
                total_size=total,
                total_human=human_size(total),
                licence=block.licence.strip(),
                licence_url=block.licence_url,
                artifacts=tuple(
                    DataArtifactLine(url=a.url, size=a.size, size_human=human_size(a.size))
                    for a in block.artifacts
                ),
                installs_under=f"<prefix>/share/hammunition/data/{planned.name}/",
                verified_by=PINNED,
                approximate=False,
            )
        )
    return InstallPlanView(
        packages=tuple(
            PackageLine(
                name=p.name,
                method=p.block.install.method,
                state=plan_state(p, built, maps, terrain, idle),
                requested_by=tuple(p.requested_by),
                apt=tuple(
                    AptLine(package=a, outstanding=a in p.outstanding, build_only=a in p.build_only)
                    for a in p.apt_packages
                ),
            )
            for p in plan.packages
        ),
        displaced=tuple(
            DisplacedLine(package=c, declared_by=p.name) for p in plan.packages for c in p.displaces
        ),
        apt_release=(
            ReleaseSection(release=plan.apt_release, packages=tuple(plan.apt_from_release))
            if plan.apt_release is not None
            else None
        ),
        no_recommends=(
            NoRecommendsSection(
                units=plan.apt_no_recommends_units, packages=plan.apt_to_install_no_recommends
            )
            if plan.apt_to_install_no_recommends
            else None
        ),
        repos=tuple(
            RepoLine(
                name=a.repo.name,
                unit=a.unit,
                packages=tuple(a.packages),
                uri=a.repo.uri,
                suites=tuple(a.repo.suites),
                components=tuple(a.repo.components),
                key_fingerprint=a.repo.key_fingerprint,
                sources=a.sources,
                keyring=a.keyring,
                consent_env_var=repo_env_var(a.repo),
            )
            for a in plan.apt_repos
        ),
        # Only when something in this plan is a data download: an apt-only
        # plan never asks the mirror, and must not say it will.
        mirror=_mirror_section(
            mirror if any(isinstance(c, Action) and c.sources for c in commands) else None,
            ignored=mirror_ignored,
        ),
        data=tuple(data),
        maps=_map_section(plan, maps, terrain),
        memberships=tuple(
            MembershipLine(
                user=m.user,
                group=m.group,
                package=m.package,
                detail=m.detail,
                reverse_hint=m.reverse_hint.strip() if m.reverse_hint else None,
            )
            for m in plan.group_memberships
        ),
        file_capabilities=tuple(
            FileCapabilityLine(
                unit=capability.package,
                path=str(capability.path),
                capabilities=capability.capabilities,
                detail=capability.detail,
            )
            for capability in plan.file_capabilities
        ),
        consent_gates=tuple(
            GateLine(profile=name, env_var=gate.env_var, risk_lines=tuple(gate.risk_lines))
            for name, gate in plan.consent_gates
        ),
        config_files=tuple(
            ConfigLine(
                unit=unit,
                path=config.path,
                mode=config.mode,
                append=config.append,
                backup_existing=config.backup_existing,
                fills=tuple(sorted(config.station_variables)),
            )
            for unit, config, _body in plan.config_files
        ),
        user_services=tuple(
            UserServiceLine(
                unit=svc.unit,
                name=svc.name,
                path=f"~/.config/systemd/user/{svc.name}.service",
                exec=elide_serial(" ".join(svc.exec_argv)),
                fills=svc.filled_from,
                listen=", ".join(f"{address}:{port}" for address, port in svc.listens),
                starts_now=svc.device_path is not None and Path(svc.device_path).exists(),
            )
            for svc in plan.user_services
        ),
        desktops_read=_desktops_read(plan),
        deferrals=tuple(
            DeferralLine(kind=d.kind, subject=d.subject, what=d.what, why=d.why, remedy=d.remedy)
            for d in plan.deferrals
        ),
        notes=tuple(plan.notes),
        records=(
            RecordsLine(log=str(log_destination), handed_to=hands_log_to)
            if log_destination is not None
            else None
        ),
        sudo=_sudo_line(commands, euid=euid, keepalive=sudo_keepalive),
        commands=tuple(step_view(c, euid=euid, index=index) for index, c in enumerate(commands, 1)),
        suggestion_notes=tuple(suggestion_notes),
        region_notes=tuple(region_notes),
        publisher_checks=tuple(
            PublisherCheckLine(
                unit=c.unit,
                item=c.item,
                checked=c.checked,
                reason=c.reason,
                attributed=c.attributed,
            )
            for c in publisher_checks
        ),
    )


def render_plan_view(view: InstallPlanView, *, target: TargetView, full: bool = False) -> list[str]:
    """The complete account of what will happen, as the terminal shows it.

    A run of steps that repeat one template for many items is collapsed to the
    template, one example, every item and the totals, unless *full* (D-016,
    amended 2026-10-02). The JSON document is never collapsed.
    """
    lines = [f"Target: {target.description}", ""]

    if view.packages:
        lines.append(f"Packages ({len(view.packages)}):")
        for package in view.packages:
            why = ", ".join(package.requested_by)
            lines.append(f"  {package.name:<28} {package.state:<18} [{why}]")
            for apt in package.apt:
                mark = "+" if apt.outstanding else "="
                note = "  (to build)" if apt.build_only else ""
                lines.append(f"      {mark} {apt.package}{note}")
        lines.append("")

    if view.displaced:
        lines.append("Installed distribution packages displaced or shadowed (D-022):")
        for displaced in view.displaced:
            lines.append(
                f"  {displaced.package}  — declared by {displaced.declared_by}; the distribution "
                f"package stays installed, see that manifest's notes"
            )
        lines.append("")

    if view.apt_release is not None:
        release = view.apt_release.release
        lines.append(f"apt packages resolved from {release} (D-038):")
        lines.extend(
            wrap(
                f"apt refused the default release because a package this machine "
                f"already installs from {release} would have been "
                f"downgraded; the apt step runs with --target-release "
                f"{release}, which takes these from there:",
                indent="  ",
            )
        )
        for apt_package in view.apt_release.packages:
            lines.append(f"      {apt_package}")
        lines.append("")

    if view.no_recommends is not None:
        units = ", ".join(view.no_recommends.units)
        lines.append("apt packages installed without Recommends (D-052):")
        lines.extend(
            wrap(
                f"{units} asked for --no-install-recommends in the manifest, because the "
                f"Recommends of these packages conflict with software this target installs; "
                f"a second apt-get install carries the flag for them alone. Everything else "
                f"in this transaction keeps apt's defaults, and both commands run with "
                f"--no-remove:",
                indent="  ",
            )
        )
        for apt_package in view.no_recommends.packages:
            lines.append(f"      {apt_package}")
        lines.append("")

    if view.repos:
        lines.append("Third-party apt repositories that will be added (D-040):")
        for repo in view.repos:
            lines.append(f"  {repo.name}  [{repo.unit}: {', '.join(repo.packages)}]")
            lines.append(
                f"      {repo.uri}  {' '.join(repo.suites)}  "
                f"{' '.join(repo.components) or '(flat repository)'}"
            )
            lines.append(f"      key {repo.key_fingerprint}")
            lines.append(f"      writes {repo.sources}")
            lines.append(f"      writes {repo.keyring}")
            lines.append(f"      consent: {repo.consent_env_var} must equal the key fingerprint")
        lines.append("")

    if view.mirror is not None:
        lines.append("Data mirror (D-070):")
        lines.extend(wrap(view.mirror.text, indent="  "))
        lines.append("")

    if view.data:
        lines.append("Offline data that will be downloaded and installed (D-049):")
        for data in view.data:
            about = "about " if data.approximate else ""
            lines.append(
                f"  {data.unit:<28} {about}{data.total_human} total, licence: {data.licence}"
            )
            lines.append(f"      stated at {data.licence_url}")
            for artifact in data.artifacts:
                lines.append(f"      {artifact.size_human:>9}  {artifact.url}")
            lines.append(f"      installs under {data.installs_under}")
            if data.verified_by != PINNED:
                lines.extend(wrap(f"warning: {data.verified_by}", indent="      "))
        lines.append("")

    if view.maps is not None:
        # The station's regions, on the operator's own terminal: resolved
        # before this plan printed, so the dated file, its size and how it is
        # checked are known before anything is confirmed (D-057).
        maps = view.maps
        lines.append("Map regions, from station config (D-057):")
        if maps.fetch:
            lines.append("  will be downloaded and installed:")
            width = max(len(f.region) for f in maps.fetch)
            lines.extend(
                f"    {f.region:<{width}}  {f.snapshot}  {f.size_human:>9}  {f.verified_by}"
                for f in maps.fetch
            )
        if maps.current:
            lines.append("  already installed, current:")
            width = max(len(f.region) for f in maps.current)
            lines.extend(
                f"    {f.region:<{width}}  {f.snapshot}"
                + ("  (nothing to do)" if f.nothing_to_do else "")
                for f in maps.current
            )
        if maps.convert:
            lines.append(f"  will be converted for Navit (map sizes an {maps.estimate_note}):")
            width = max(len(f.region) for f in maps.convert)
            for f in maps.convert:
                border = ""
                if maps.boundaries is not None:
                    border = f"  border: {', '.join(f.countries) or 'none known'}"
                changed = "  (converter changed)" if f.converter_changed else ""
                lines.append(
                    f"    {f.region:<{width}}  {f.snapshot}  about {f.estimate_human}"
                    f"{border}{changed}"
                )
            if maps.boundaries is not None:
                lines.extend(
                    wrap(
                        "each region is first merged with its country's closed border "
                        "(osmium merge, in the staging directory, removed afterwards), so "
                        "maptool files its towns under the country and address search "
                        "finds them; maptool runs with -U, so a town the border misses is "
                        "indexed under the country Unknown, not dropped",
                        indent="      ",
                    )
                )
                border_file = maps.boundaries
                lines.extend(
                    wrap(
                        f"country borders: {border_file.title}, {border_file.size_human}, "
                        f"{border_file.licence}, {border_file.verified_by}",
                        indent="      ",
                    )
                )
            else:
                lines.extend(
                    wrap(
                        "maptool runs with -U, so a town outside every country boundary "
                        "is indexed under the country Unknown, not dropped",
                        indent="      ",
                    )
                )
        for kept in maps.kept:
            lines.append(
                f"  {kept.region}: could not check for a newer map; keeping the installed "
                f"{kept.snapshot or '(snapshot not recorded)'}"
            )
            lines.extend(wrap(kept.reason, indent="      "))
        lines.append(f"      licence: {maps.licence}, stated at {maps.licence_url}")
        lines.append(
            f"      download total: {maps.download_total_human}; about "
            f"{maps.disk_total_human} of disk with Navit's maps ({maps.estimate_note})"
        )
        lines.append("      installs under <prefix>/share/hammunition/data/")
        if maps.terrain is not None:
            lines.extend(_render_terrain(maps.terrain))
        lines.append("")

    if view.memberships:
        lines.append("Group membership changes:")
        for membership in view.memberships:
            lines.append(f"  {membership.user} → {membership.group}  ({membership.package})")
            lines.extend(wrap(membership.detail, indent="      "))
            if membership.reverse_hint is not None:
                lines.append(f"      reverse: {membership.reverse_hint}")
        lines.append("")

    if view.file_capabilities:
        lines.append("File capabilities (opt-in; cleared on uninstall):")
        for capability in view.file_capabilities:
            assignments = " ".join(f"{name}=ep" for name in capability.capabilities)
            lines.append(f"  {capability.path}  ({capability.unit})")
            lines.append(f"      setcap {assignments}")
            lines.extend(wrap(capability.detail, indent="      "))
        lines.append("")

    if view.consent_gates:
        lines.append("Consent gates that will be presented:")
        for gate in view.consent_gates:
            lines.append(f"  {gate.profile} ({gate.env_var})")
            for risk in gate.risk_lines:
                wrapped = wrap(risk, indent="        ")
                lines.append("      - " + wrapped[0].strip())
                lines.extend(wrapped[1:])
        lines.append("")

    if view.config_files:
        lines.append("Configuration that will be written:")
        for config in view.config_files:
            backup = "existing file backed up" if config.backup_existing else "NOT backed up"
            verb = "appended to" if config.append else "written"
            fills = f"  fills {', '.join(config.fills)}" if config.fills else ""
            lines.append(
                f"  {config.path}  ({verb}, mode {config.mode}, {backup})  [{config.unit}]{fills}"
            )
        lines.append("")

    if view.user_services:
        lines.append("User services (D-073), run as you, not root:")
        listed: set[str] = set()
        for svc in view.user_services:
            lines.append(f"  {svc.unit}: {svc.name}")
            lines.append(f"    writes   {svc.path}")
            lines.append(f"    runs     {svc.exec}")
            if svc.fills:
                lines.append(f"    filled from: {', '.join(svc.fills)}")
                lines.append(
                    f"    listens  {svc.listen} — any program on this machine can key the "
                    f"transmitter through it; it has no password (rigctld -A is not implemented)"
                )
            elif svc.listen:
                lines.append(
                    f"    listens  {svc.listen} — loopback only; any program on this machine "
                    f"can connect"
                )
            lines.append("    then     systemctl --user daemon-reload")
            lines.append(f"             systemctl --user enable {svc.name}.service")
            if svc.starts_now:
                lines.append(f"             systemctl --user restart {svc.name}.service")
            elif svc.fills:
                lines.append(
                    f"             (restart deferred: {svc.name} starts when the radio's port appears)"
                )
            else:
                lines.append(
                    f"             systemctl --user try-restart {svc.name}.service"
                    f"  (only if it is already running)"
                )
                lines.append(
                    f"             ({svc.name} is not started now: it starts at your next login)"
                )
            if svc.unit not in listed:
                listed.add(svc.unit)
                lines.append(
                    f"    lists    ~/.config/hammunition/devctl-services.yaml, mode 0600: one row, "
                    f"{svc.unit.removesuffix('-service')!r}, for the tray's Services group"
                )
            lines.append(f"    reverse  hammunition uninstall {svc.unit}")
        lines.append("")

    if view.desktops_read is not None:
        # D-060: a unit in this request is for particular desktops, and what
        # decided it is on disk rather than in the caller's environment, so
        # the plan names both the files and what they said.
        lines.append(
            "Desktops read from session files (/usr/share/xsessions, /usr/share/wayland-sessions "
            "and the same under /usr/local/share):"
        )
        lines.append(f"  {view.desktops_read.summary}")
        lines.append("")

    if view.deferrals:
        # After the packages and before the notes: this is the part of the
        # request that will NOT happen, and burying it under a heading called
        # "notes" is how it stops being read. D-035.
        lines.append("Will NOT happen (the rest of the transaction still will):")
        for deferral in view.deferrals:
            lines.append(f"  {deferral.subject}: {deferral.what}")
            lines.extend(wrap(f"why: {deferral.why}", indent="      "))
            lines.extend(wrap(f"→ {deferral.remedy}", indent="      "))
        lines.append("")

    if view.notes:
        lines.append("Notes:")
        for note in view.notes:
            wrapped = wrap(note, indent="      ")
            lines.append("  - " + wrapped[0].strip())
            lines.extend(wrapped[1:])
        lines.append("")

    if view.sudo is not None:
        lines.append("sudo (D-062):")
        lines.extend(wrap(view.sudo.text, indent="  "))
        lines.append("")

    if view.records is not None:
        lines.append("Records:")
        lines.append(f"  transaction log written to {view.records.log}")
        if view.records.handed_to is not None:
            lines.append(
                f"  the log and any directories created for it are given to "
                f"{view.records.handed_to!r} (chown), since root is writing into their home"
            )
        lines.append("")

    lines.append(f"Commands ({len(view.commands)}):")
    if not view.commands:
        lines.append("  (none — everything this plan asks for is already in place)")
    lines.extend(render_steps(view.commands, full=full))
    return lines


def _render_terrain(terrain: TerrainSectionView) -> list[str]:
    """The Terrain block, inside the map section (D-061)."""
    lines: list[str] = []
    if terrain.regions or terrain.fetch or terrain.current:
        lines.append("  Terrain, Copernicus GLO-30 elevation (D-061):")
    if terrain.regions:
        width = max(len(r.region) for r in terrain.regions)
        for region in terrain.regions:
            unpublished = (
                f", {region.unpublished} square(s) with no published tile "
                f"(sea, or land Copernicus does not release)"
                if region.unpublished
                else ""
            )
            fetch = f"; {region.download_human} to download" if region.download else ""
            lines.append(
                f"    {region.region:<{width}}  {region.tiles} tile(s){unpublished}{fetch}"
            )
        # Final review, I1: a region with squares and not one published tile
        # gets no terrain. Said as a warning, never folded into success.
        lines.extend(
            f"    warning: {no_terrain_line(region.region)}; its maps still install"
            for region in terrain.regions
            if region.no_terrain
        )
        # A region's record is written only when its terrain is installed
        # (Task 10's note, ruled at Task 13): until then every plan, a dry
        # run included, asks Geofabrik for its outline again.
        lines.append("    (a region's tiles are read from its outline at Geofabrik, fetched again")
        lines.append("    by every plan until its terrain is installed and its record written)")
    if terrain.fetch:
        lines.append(
            f"    will be downloaded ({len(terrain.fetch)} tile(s), "
            f"{terrain.download_total_human}):"
        )
        width = max(len(t.tile) for t in terrain.fetch)
        lines.extend(
            f"      {t.tile:<{width}}  {t.size_human:>9}  {t.verified_by}" for t in terrain.fetch
        )
    if terrain.current:
        lines.append(f"    already installed: {terrain.current} tile(s)")
    if terrain.licence:
        lines.append(f"      licence: {terrain.licence}, stated at {terrain.licence_url}")
    built: list[str] = []
    if terrain.garmin:
        width = max(len(g.region) for g in terrain.garmin)
        built.extend(
            f"    Garmin map  {g.region:<{width}}  {g.snapshot}  about {g.estimate_human} "
            f"({GARMIN_FACTOR}x the download)"
            for g in terrain.garmin
        )
    if terrain.routino_regions:
        built.append(
            f"    Routino database over {terrain.routino_regions} region(s)  about "
            f"{terrain.routino_estimate_human} ({ROUTINO_FACTOR}x the downloads together)"
        )
    if terrain.brouter_regions:
        elevation = (
            f"elevation from {terrain.brouter_tiles} tile(s)"
            if terrain.brouter_tiles
            else "no elevation (flat)"
        )
        built.append(
            f"    BRouter routing files over {terrain.brouter_regions} region(s)  about "
            f"{terrain.brouter_estimate_human} ({BROUTER_FACTOR}x the downloads together), "
            f"{elevation}; built here, never downloaded from brouter.de"
        )
    if terrain.contours:
        drawn = " of USGS 3DEP" if terrain.contours_from == "usgs-3dep" else ""
        built.append(
            f"    contours for {terrain.contours} tile(s){drawn}  about "
            f"{terrain.contours_estimate_human}, with up to "
            f"{human_size(contour_scratch(terrain.contours_from))} of scratch at a time"
        )
    if built:
        lines.append(f"  Built for QMapShack (sizes an estimate, {terrain.estimate_note}):")
        lines.extend(built)
    if terrain.splat_tiles:
        lines.append(f"  Built for SPLAT! and Signal-Server (sizes an estimate, {SDF_MEASURED}):")
        lines.append(
            f"    SDF terrain for {terrain.splat_tiles} tile(s)  about "
            f"{terrain.splat_estimate_human} (both resolutions, bzip2), with up to "
            f"{human_size(SDF_SCRATCH_BYTES)} of scratch at a time"
        )
    lines.append(
        f"      about {terrain.disk_total_human} of disk for terrain and QMapShack's maps "
        f"({terrain.estimate_note})"
    )
    if terrain.bare_earth is not None:
        lines.extend(_render_bare_earth(terrain.bare_earth))
    if terrain.topo is not None:
        lines.extend(_render_topo(terrain.topo))
    if terrain.fstopo is not None:
        lines.extend(_render_fstopo(terrain.fstopo))
    return lines


def _render_bare_earth(bare: BareEarthSectionView) -> list[str]:
    """The 3DEP block, after Copernicus's (D-068, amended 2026-10-01)."""
    lines = ["  USGS 3DEP bare-earth elevation (D-068):"]
    if not bare.chosen:
        lines.append("    not chosen: dem_source is copernicus, so no 3DEP tile is fetched and any")
        lines.append("    installed one is removed (`hammunition station set --dem-source 3dep`")
        lines.append("    chooses bare earth, about ten times the size of Copernicus)")
        return lines
    if bare.regions:
        width = max(len(r.region) for r in bare.regions)
        for region in bare.regions:
            fetch = f"; {region.download_human} to download" if region.download else ""
            lines.append(f"    {region.region:<{width}}  {region.tiles} tile(s){fetch}")
        lines.extend(
            f"    warning: {no_bare_earth_line(region.region)}"
            for region in bare.regions
            if region.no_terrain
        )
    if bare.fetch:
        lines.append(
            f"    will be downloaded ({len(bare.fetch)} tile(s), {bare.download_total_human}, "
            f"about ten times Copernicus's size for the same ground):"
        )
        width = max(len(t.tile) for t in bare.fetch)
        lines.extend(
            f"      {t.tile:<{width}}  {t.size_human:>9}  {t.verified_by}" for t in bare.fetch
        )
    if bare.current:
        lines.append(f"    already installed: {bare.current} tile(s)")
    if bare.licence:
        lines.append(f"      licence: {bare.licence}, stated at {bare.licence_url}")
    if bare.download_total:
        lines.append(f"      about {bare.download_total_human} of disk for 3DEP tiles")
    return lines


def _render_fstopo(fstopo: FsTopoSectionView) -> list[str]:
    """The FSTopo block, after US Topo's (D-068, amended 2026-10-01)."""
    lines = ["  FSTopo, Forest Service 7.5-minute quads (D-068):"]
    if fstopo.regions:
        width = max(len(r.region) for r in fstopo.regions)
        for region in fstopo.regions:
            fetch = f"; {region.download_human} to download" if region.download else ""
            pinned = ", every one pinned" if region.all_pinned else ""
            lines.append(f"    {region.region:<{width}}  {region.quads} quad(s){pinned}{fetch}")
        if all(r.all_pinned for r in fstopo.regions):
            lines.append("    every FSTopo quad your regions need is pinned by Hammunition")
    lines.extend(f"    note: {no_sheets_line(region)}" for region in fstopo.no_quads)
    if fstopo.fetch:
        lines.append(
            f"    will be downloaded ({len(fstopo.fetch)} quad(s), {fstopo.download_total_human}):"
        )
        width = max(len(q.quad) for q in fstopo.fetch)
        lines.extend(
            f"      {q.quad:<{width}}  {q.size_human:>9}  {q.verified_by}" for q in fstopo.fetch
        )
    if fstopo.unverified:
        lines.append(
            f"    warning: {fstopo.unverified} quad(s) unverified: the Forest Service publishes "
            f"no checksum and Hammunition has pinned none"
        )
    if fstopo.current:
        lines.append(f"    already installed: {fstopo.current} quad(s)")
    if fstopo.licence:
        lines.append(f"      licence: {fstopo.licence}, stated at {fstopo.licence_url}")
    if fstopo.convert:
        lines.append(
            f"    converted for QMapShack: {fstopo.convert} quad(s), about "
            f"{fstopo.convert_estimate_human} ({FSTOPO_FACTOR}x each sheet, "
            f"{fstopo.estimate_note})"
        )
    lines.append(
        f"      about {fstopo.disk_total_human} of disk for FSTopo ({fstopo.estimate_note})"
    )
    return lines


def _render_topo(topo: TopoSectionView) -> list[str]:
    """The US Topo block, after the terrain (D-068)."""
    lines = ["  US Topo, USGS 7.5-minute quads (D-068):"]
    if topo.selection:
        lines.append(f"    selection: {topo.selection}")
    if topo.regions:
        width = max(len(r.region) for r in topo.regions)
        for region in topo.regions:
            fetch = f"; {region.download_human} to download" if region.download else ""
            lines.append(
                f"    {region.region:<{width}}  {region.quads} quad(s), {region.size_human}{fetch}"
            )
    lines.extend(f"    note: {no_quads_line(region)}" for region in topo.no_quads)
    if topo.regions or topo.no_quads:
        lines.append("    (a region's quads are read from its outline at Geofabrik until")
        lines.append("    they are installed and its record written)")
    if topo.fetch:
        lines.append(
            f"    will be downloaded ({len(topo.fetch)} quad(s), {topo.download_total_human}):"
        )
        width = max(len(q.quad) for q in topo.fetch)
        lines.extend(
            f"      {q.quad:<{width}}  {q.size_human:>9}  {q.verified_by}" for q in topo.fetch
        )
    if topo.current:
        lines.append(f"    already installed: {topo.current} quad(s)")
    if topo.licence:
        lines.append(f"      licence: {topo.licence}, stated at {topo.licence_url}")
    if topo.warp:
        lines.append(
            f"    warped for QMapShack: {topo.warp} quad(s), about "
            f"{topo.warp_estimate_human} ({WARP_FACTOR}x each download, {topo.estimate_note})"
        )
    lines.append(f"      about {topo.disk_total_human} of disk for US Topo ({topo.estimate_note})")
    if topo.size_consent:
        lines.append(f"    {topo.size_consent}")
        lines.append("    The install asks you to type yes to that; --yes does not answer it.")
        lines.append(
            "    `hammunition station set --topo-radius-km N` or `--topo-regions` asks for fewer."
        )
    return lines


def build_removal_view(
    plan: RemovalPlan, commands: Sequence[Step], *, euid: int
) -> RemovalPlanView:
    def shown(mapping: dict[str, list[str]]) -> tuple[UnitPackages, ...]:
        # The text lists a unit here only when it has packages, or is not
        # also being removed; the view carries exactly what the text shows.
        return tuple(
            UnitPackages(unit=unit, packages=tuple(packages))
            for unit, packages in mapping.items()
            if packages or unit not in plan.to_remove
        )

    return RemovalPlanView(
        to_remove=tuple(
            UnitPackages(unit=unit, packages=tuple(packages))
            for unit, packages in plan.to_remove.items()
        ),
        artifacts=tuple(
            ArtifactLine(unit=unit, kind=r.kind, path=str(r.path), basis=r.basis)
            for unit, removals in plan.artifacts.items()
            for r in removals
        ),
        left_unattributed=tuple(
            UnitFiles(unit=unit, paths=tuple(paths))
            for unit, paths in plan.left_unattributed.items()
        ),
        left_foreign=shown(plan.left_foreign),
        already_absent=shown(plan.already_absent),
        not_reversed=NOT_REVERSED
        + "".join(
            f"\nLeft in place, because another installed unit still needs it: {unit}: "
            f"{', '.join(paths)}"
            for unit, paths in plan.kept_shared.items()
        ),
        commands=tuple(step_view(c, euid=euid, index=index) for index, c in enumerate(commands, 1)),
    )


def render_removal_view(view: RemovalPlanView, *, target: TargetView) -> list[str]:
    """What an uninstall will do, as the terminal shows it, up to the commands."""
    lines = [f"Target: {target.description}", ""]
    if view.to_remove:
        lines.append(f"Removing ({len(view.to_remove)} unit(s)):")
        for removal in view.to_remove:
            lines.append(f"  {removal.unit:28} - {' '.join(removal.packages)}")
    if view.artifacts:
        lines += ["", f"Removing artifacts ({len(view.artifacts)}):"]
        for artifact in view.artifacts:
            lines.append(
                f"  {artifact.unit:28} {artifact.kind:14} {artifact.path}  [{artifact.basis}]"
            )
    if view.left_unattributed:
        lines += ["", "Left in place — present, but the transaction log does not attribute it:"]
        for files in view.left_unattributed:
            for path in files.paths:
                lines.append(f"  {files.unit:28} {path}")
    for label, shown in (
        ("Left in place — installed, but not installed by Hammunition:", view.left_foreign),
        ("Already absent:", view.already_absent),
    ):
        if shown:
            lines += ["", label]
            for entry in shown:
                packages = " ".join(entry.packages) or "(nothing resolves here)"
                lines.append(f"  {entry.unit:28} {packages}")
    lines += ["", view.not_reversed]
    if view.commands:
        lines += ["", f"Commands ({len(view.commands)}):"]
        for command in view.commands:
            lines.append(f"  # {command.index}: {command.description}")
            lines.append(f"  $ {command.display}")
    else:
        lines += ["", "Nothing to do: none of this is installed, or none of it was ours."]
    return lines


def refused_plan(
    action: str, requested: Sequence[str], target: TargetView, blockers: Sequence[Blocker]
) -> PlanDocument:
    return PlanDocument(
        action=action,
        requested=tuple(requested),
        outcome="refused",
        step_count=0,
        target=target,
        blockers=tuple(
            BlockerLine(subject=b.subject, reason=b.reason, remedy=b.remedy) for b in blockers
        ),
        install=None,
        removal=None,
    )
