# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``hammunition`` command.

``argparse`` rather than a dependency: the CLI surface is four verbs, and a
tool whose whole pitch is "you can read what it is going to do to your machine"
should be installable without pulling anything extra in to parse its own
arguments.

The verbs are ``install``, ``uninstall``, ``list``, ``status``, ``show``,
``menus``, ``hardware`` and ``station``, all with the M1 property intact: what the engine cannot do it says
so, by name. Every backend the 1.0 measurement requires is written (apt, source,
git, binary, venv, and node by D-037); pipx and CPAN re-measured to zero and a
package declaring one is refused with the backend named rather than skipped. CLAUDE.md forbids a shim
that makes an unsupported combination appear to work, and a CLI that quietly
drops the packages it cannot handle is that shim.

Exit codes, because scripts read them:

==  ==============================================================
0   Success, or a dry run that resolved cleanly
1   A command failed while running, or the system is unsupported
2   The transaction could not be planned — every blocker is printed
3   A consent gate was declined, or could not be presented
==  ==============================================================
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import io
import os
import platform
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import textwrap
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TextIO, cast

from hammunition import navit_config, netiso
from hammunition.acma import AcmaProbe
from hammunition.attributed import PublisherChecks
from hammunition.backends import (
    Action,
    AptBackend,
    AptRepoBackend,
    BackendError,
    BinaryBackend,
    Command,
    CommandResult,
    DataBackend,
    DerivedBackend,
    GitBackend,
    NodeBackend,
    RegionsBackend,
    SourceBackend,
    SubprocessRunner,
    VenvBackend,
)
from hammunition.backends.apt import stale_fetches
from hammunition.backends.comaps_maps import (
    ComapsMapsBackend,
    maps_disk_needs,
    maps_shortfall,
    resolve_station_maps,
)
from hammunition.backends.data import human_size
from hammunition.backends.dem import TIF, TILES, DemResolution, TerrainDisclosure, read_record
from hammunition.backends.fstopo import FsTopoResolution
from hammunition.backends.kiwix import (
    KiwixBooksBackend,
    books_disk_needs,
    books_shortfall,
    resolve_station_books,
)
from hammunition.backends.regions import (
    KeptRegion,
    MapDisclosure,
    MapLedger,
    MapResolution,
    data_root,
    disk_needs,
    installed_slugs,
    installed_snapshot,
    region_current,
)
from hammunition.backends.source import DEFAULT_PREFIX
from hammunition.backends.terrain import combined_shortfall
from hammunition.backends.topo import TopoResolution
from hammunition.comaps import CdnProbe, ComapsError, ComapsPins, MapFile, resolve_regions
from hammunition.comaps import load_pins as load_comaps_pins
from hammunition.consent import (
    ConsentDeclined,
    ConsentRecord,
    ConsentUnavailable,
    resolve_consent,
    resolve_repo_consent,
    resolve_topo_size_consent,
)
from hammunition.copernicus import CachingTileProbe, CopernicusError, S3Probe
from hammunition.country_boundaries import BoundarySource, CountryBoundaryError, boundary_source
from hammunition.desktop import current_desktop, scan_sessions
from hammunition.devctl_helper import plan_helper
from hammunition.distro import DetectionError, Target
from hammunition.doctor import Check, RigStatus
from hammunition.execute import (
    ExecutionReport,
    Step,
    StepOwners,
    already_built,
    artifact_removal_steps,
    build_dir,
    build_effects_present,
    commands_for,
    completion_on_disk,
    completion_states,
    execute,
    run_removal,
    user_groups,
    user_service_removal_steps,
)
from hammunition.fetch import Fetcher
from hammunition.fstopo import FstopoError, GatewayProbe
from hammunition.fstopo import load_index as load_fstopo_index
from hammunition.geofabrik import (
    BASE,
    GeofabrikError,
    Probe,
    RegionFile,
    UrllibProbe,
    current_pinned_snapshots,
    load_countries,
    load_pins,
    region_ids,
)
from hammunition.geofabrik import resolve as resolve_region
from hammunition.hardware import devctl_export, gps_resume
from hammunition.hardware import polkit as hardware_polkit
from hammunition.hardware.apply import HardwarePlan
from hammunition.hardware.polkit import HELPER_PATH, POLICY_PATH, describe_refusal
from hammunition.interface import envelope
from hammunition.interface.services import ServicesDocument, ServiceView
from hammunition.java import JavaProbe
from hammunition.kernel import KernelProbe
from hammunition.keystrength import KeyStrength, classify
from hammunition.kiwix import (
    BookFile,
    KiwixError,
    KiwixProbe,
    load_book_list,
    load_pin_file,
    resolve_books,
)
from hammunition.listening import bound_to_loopback_only
from hammunition.manifest.hardware import DeviceClass, DeviceManifest
from hammunition.manifest.load import CatalogError, load_catalog, load_profiles
from hammunition.manifest.schema import (
    COMMIT_SHA,
    AptInstall,
    BinaryInstall,
    DemTilesInstall,
    DerivedDataInstall,
    GitInstall,
    KiwixBooksInstall,
    MwmRegionsInstall,
    PackageManifest,
    ProfileManifest,
    RegionalDataInstall,
    SourceInstall,
    Status,
    TopoQuadsInstall,
)
from hammunition.paths import (
    applications_dir,
    build_root,
    node_root,
    user_bin_dir,
    user_config_base,
    venv_root,
)
from hammunition.payloads import payload_cached
from hammunition.phone_plan import build_phone_run
from hammunition.plan import (
    NO_MAP_REGIONS,
    Blocker,
    DebDependencyError,
    Deferral,
    InstallPlan,
    PlanError,
    PlannedPackage,
    _without_units,
    cached_data_pin,
    cached_remote,
    catalogue_deferral,
    deb_group_met,
    deb_probe_names,
    offline_network_blockers,
    offline_payload_blockers,
    parse_deb_dependencies,
    payload_misses,
    preflight_data,
    resolve,
)
from hammunition.progress import LiveStatus, Progress, activate_live, current_live
from hammunition.repeater_sources import SnapshotHead
from hammunition.resolution import CatalogueMiss, ResolutionContext
from hammunition.retry import (
    POLICY,
    Outages,
    RetryingProbe,
    reporter_for,
    retrying_head,
)
from hammunition.routing_plan import build_graph_run, graphhopper_jar
from hammunition.security_keys import SecurityKeyState
from hammunition.state import (
    RemovalError,
    RemovalPaths,
    TransactionLog,
    file_capabilities_installed_by_hammunition,
    files_installed_by_hammunition,
    installed_by_hammunition,
    log_path,
    plan_removal,
)
from hammunition.station import (
    Station,
    StationError,
    config_path,
    is_interactive,
    load_station,
    prompt_for,
    save_station,
)
from hammunition.sudo_ticket import SudoKeepalive, keepalive_wanted
from hammunition.terrain_plan import (
    brouter_pins,
    build_terrain_run,
    contour_source,
    resolve_station_3dep,
    resolve_station_terrain,
    splat_source,
)
from hammunition.tiles_plan import build_tiles_run
from hammunition.topo_bound import ALL, BoundUnavailable, TopoBound, make_bound
from hammunition.topo_plan import (
    FSTOPO_INDEX,
    MemoProbe,
    missing_grid_deferral,
    outside_installed,
    resolve_station_fstopo,
    resolve_station_topo,
    selection_note,
    size_consent,
    topo_units,
)
from hammunition.topo_plan import INDEX as USTOPO_INDEX
from hammunition.update import (
    UNKNOWN,
    books_state,
    mwm_state,
    region_snapshots,
    render,
    report,
    requested_units,
)
from hammunition.upstream import (
    NOT_UPSTREAM,
    http_get,
    parse_ls_remote,
    probe_comaps_maps,
    probe_kiwix,
    probe_upstream,
)
from hammunition.upstream import render as render_upstream
from hammunition.urlredact import redact_mirror_url, redact_url_text
from hammunition.ustopo import UstopoError
from hammunition.ustopo import bucket_probe as ustopo_probe
from hammunition.ustopo import load_index as load_ustopo_index

if TYPE_CHECKING:
    from hammunition.areas import Active, PoiLinks
    from hammunition.geoclue import GeoClueGrants, GeoClueState
    from hammunition.hardware.power import KeptEntry, Parkable
    from hammunition.infra import Gathered
    from hammunition.infra_sources import SourceRead
    from hammunition.interface.repeaters import AllSourcesView, RegistrationView
    from hammunition.qmapshack_config import BRouterSetup
    from hammunition.repeater_sources import SnapshotRead
    from hammunition.repeaters import ParsedInput
    from hammunition.upstream import UpstreamRow

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNPLANNABLE = 2
EXIT_CONSENT = 3


# ---------------------------------------------------------------------------
# Locating the catalog
# ---------------------------------------------------------------------------


def find_catalog(explicit: Path | None = None) -> Path:
    """Locate ``catalog/``.

    A git clone is the supported install today — the wheel carries the engine
    and the catalog is a separate tree — so the search walks up from this file
    looking for a checkout. ``--catalog`` and ``HAMMUNITION_CATALOG`` override
    it, and being unable to find one is a loud error rather than an empty
    catalog, because an empty catalog would make ``list`` print nothing and
    look like an answer.
    """
    if explicit is not None:
        candidate = explicit
    elif os.environ.get("HAMMUNITION_CATALOG"):
        candidate = Path(os.environ["HAMMUNITION_CATALOG"])
    else:
        for parent in Path(__file__).resolve().parents:
            if (parent / "catalog" / "packages").is_dir():
                return parent / "catalog"
        raise SystemExit(
            "could not find the catalog. Hammunition is installed from a git clone "
            "today; run it from the checkout, or pass --catalog /path/to/catalog "
            "(or set HAMMUNITION_CATALOG)."
        )
    if not (candidate / "packages").is_dir():
        raise SystemExit(f"{candidate} does not look like a catalog: no packages/ inside it")
    return candidate


def load_all(
    catalog_root: Path,
) -> tuple[dict[str, PackageManifest], dict[str, ProfileManifest]]:
    """Load packages and profiles, cross-checked. Every failure at once (D-016)."""
    packages = load_catalog(catalog_root / "packages")
    profiles = load_profiles(catalog_root / "profiles", packages)
    return packages, profiles


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_plan(
    plan: InstallPlan,
    commands: Sequence[Step],
    *,
    euid: int,
    log_destination: Path | None = None,
    hands_log_to: str | None = None,
    built: frozenset[str] = frozenset(),
    maps: MapDisclosure | None = None,
    terrain: TerrainDisclosure | None = None,
    full: bool = False,
) -> list[str]:
    """The complete account of what will happen. Printed for every run.

    Not only for ``--dry-run``. An operator who is about to say yes should be
    reading the same text the dry run would have shown them, because a
    disclosure that appears only when you ask for it is one most people never
    see.

    ``log_destination`` and ``hands_log_to`` disclose the transaction log — a
    file written to the machine, and under ``sudo`` a file (and the directories
    on the way to it) chowned to the operator. CLAUDE.md: nothing happens to a
    machine that is not written down, before it happens. Showing the resolved
    path also makes a wrong one visible: if the operator does not resolve and
    the log falls back to ``/root``, the plan now says so instead of the
    fallback happening in silence.

    Rendered from :class:`hammunition.interface.plan.InstallPlanView`, the same
    object ``install --dry-run --json`` emits (D-059), so the two cannot drift.
    """
    from hammunition.interface.envelope import target_view
    from hammunition.interface.plan import build_install_view, render_plan_view

    view = build_install_view(
        plan,
        commands,
        euid=euid,
        log_destination=log_destination,
        hands_log_to=hands_log_to,
        built=built,
        maps=maps,
        terrain=terrain,
    )
    return render_plan_view(view, target=target_view(plan.target), full=full)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@envelope.json_capable()
def cmd_list(args: argparse.Namespace) -> int:
    from hammunition.interface.catalog import build_catalog, detect_target, render_catalog

    catalog_root = find_catalog(args.catalog)
    packages, profiles = load_all(catalog_root)
    from hammunition.interface.status import installed_units

    log = TransactionLog(owner=operator(args) or None)
    doc = build_catalog(
        args.what,
        packages,
        profiles,
        detect_target(),
        installed=installed_units(list(log.read())),
        runner=SubprocessRunner(),
    )
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_catalog(doc):
        print(line)
    return EXIT_OK


def operator(args: argparse.Namespace) -> str:
    """Who this run is on behalf of.

    Used for two things that must agree: which account `gpasswd` adds to a
    group, and whose transaction log gets written. They were resolved
    separately, and under `sudo hammunition` the log went to root's home while
    the group membership went to the right person — so `hammunition status`,
    run afterwards as that person, reported no transactions at all.
    """
    return (
        getattr(args, "user", None) or os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    )


@envelope.json_capable()
def cmd_status(args: argparse.Namespace) -> int:
    """What this machine is, what the catalog holds, and what has been done here.

    The most recent transaction is reported by how it actually ended, not by
    what it intended: reading only transaction_begin once reported a run that
    died on package 3 of 20 as if all 20 landed.
    """
    from hammunition.interface.status import build_status, render_status

    try:
        target = Target.detect()
    except DetectionError as exc:
        print(f"Target: unidentified — {exc}", file=sys.stderr)
        return EXIT_FAILED

    catalog_root = find_catalog(args.catalog)
    packages, profiles = load_all(catalog_root)
    log = TransactionLog(owner=operator(args) or None)
    doc = build_status(
        target=target,
        catalog_root=catalog_root,
        packages=packages,
        profiles=profiles,
        log_path=log.path,
        entries=list(log.read()),
    )
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_status(doc):
        print(line)
    return EXIT_OK


@envelope.json_capable()
def cmd_station_show(args: argparse.Namespace) -> int:
    """What is saved, and where. Says plainly when nothing is."""
    from hammunition.interface.station import build_station, render_station

    user = operator(args)
    try:
        station = load_station(owner=user)
    except StationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    doc = build_station(config_path(user), station)
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_station(doc):
        print(line)
    return EXIT_OK


@envelope.json_capable()
def cmd_secrets_status(args: argparse.Namespace) -> int:
    """Where each secret the engine knows would come from; never a value.  D-081."""
    from hammunition.interface.secrets import build_secrets, render_secrets

    try:
        station: Station | None = load_station(owner=operator(args) or None)
    except StationError:
        station = None
    doc = build_secrets(env=os.environ, station=station)
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_secrets(doc):
        print(line)
    return EXIT_OK


@envelope.json_capable()
def cmd_station_set(args: argparse.Namespace) -> int:
    return _cmd_station_set(args, json_output=envelope.wanted(args))


def _cmd_station_set(args: argparse.Namespace, *, json_output: bool) -> int:
    """Set the accepted station values and report each refused flag (D-059)."""
    from hammunition.interface.station import StationSetDocument, StationSetRefusal

    user = operator(args)
    try:
        current = load_station(owner=user)
    except StationError:
        current = Station()

    requested: dict[str, str | int | bool | tuple[str, ...] | None] = {}
    accepted: dict[str, str | int | bool | tuple[str, ...] | None] = {}
    refused: list[StationSetRefusal] = []
    refusal_code = EXIT_FAILED

    def refuse(
        key: str,
        value: str | int | bool | None,
        reason: str,
        code: int = EXIT_FAILED,
    ) -> None:
        nonlocal refusal_code
        if key == "mirror":
            # A refused mirror URL may carry a user and password: never echo them.
            # reason is prose with the URL embedded partway through (the
            # prose-safe matcher); value is the bare operator-typed URL and
            # nothing else, so a literal space inside its userinfo must still
            # redact (#381, Task 19 follow-up).
            reason = redact_url_text(reason)
            value = redact_mirror_url(value) if isinstance(value, str) else value
        if not refused:
            refusal_code = code
        refused.append(StationSetRefusal(key=key, value=value, reason=reason))

    for key, value in (
        ("callsign", args.callsign),
        ("grid_square", args.grid_square),
        ("node_alias", args.node_alias),
        ("map_freshness", args.map_freshness),
        ("mirror", args.mirror),
        ("dem_source", args.dem_source),
        ("topo_radius_km", args.topo_radius_km),
        ("topo_all", args.topo_all),
        ("secrets_doppler_project", args.doppler_project),
        ("secrets_doppler_config", args.doppler_config),
    ):
        if value is None:
            continue
        requested[key] = value
        try:
            candidate = Station(**{key: value})
        except StationError as exc:
            refuse(key, value, str(exc))
        else:
            accepted[key] = getattr(candidate, key)

    if args.map_regions is not None:
        raw_regions = args.map_regions
        requested["map_regions"] = raw_regions
        map_regions = tuple(r for r in (p.strip() for p in raw_regions.split(",")) if r)
        if not map_regions:
            refuse(
                "map_regions",
                raw_regions,
                "--map-regions gave no regions after splitting on ',' and stripping "
                "whitespace; give at least one region, or to remove the maps, uninstall "
                "osm-navit and osm-regions.",
            )
        else:
            try:
                candidate = Station(map_regions=map_regions)
            except StationError as exc:
                refuse("map_regions", raw_regions, str(exc))
            else:
                accepted["map_regions"] = candidate.map_regions

    if args.reference_books is not None:
        raw_books = args.reference_books
        requested["reference_books"] = raw_books
        reference_books = tuple(b for b in (p.strip() for p in raw_books.split(",")) if b)
        if not reference_books:
            refuse(
                "reference_books",
                raw_books,
                "--reference-books gave no book ids after splitting on ',' and stripping "
                "whitespace; give at least one, or to remove the books, uninstall "
                "kiwix-library.",
            )
        else:
            try:
                candidate = Station(reference_books=reference_books)
                books = load_book_list(find_catalog(args.catalog))
            except StationError as exc:
                refuse("reference_books", raw_books, str(exc))
            except (CatalogError, KiwixError) as exc:
                refuse("reference_books", raw_books, str(exc))
            else:
                unknown = [book for book in candidate.reference_books if book not in books]
                if unknown:
                    refuse(
                        "reference_books",
                        raw_books,
                        "not in the catalog's book list: "
                        f"{', '.join(unknown)}. `hammunition reference books` lists the "
                        "books the catalog offers, by id.",
                    )
                else:
                    accepted["reference_books"] = candidate.reference_books

    if args.mirror_require_hardware_key is not None:
        requested["mirror_require_hardware_key"] = args.mirror_require_hardware_key
        accepted["mirror_require_hardware_key"] = args.mirror_require_hardware_key
    if args.clear_mirror:
        requested["mirror"] = None
        accepted["mirror"] = None

    if args.clear_doppler:
        if args.doppler_project is not None or args.doppler_config is not None:
            refuse(
                "secrets_doppler_project",
                args.doppler_project or args.doppler_config,
                "--clear-doppler removes both Doppler names; do not give "
                "--doppler-project or --doppler-config with it.",
            )
        else:
            for key in ("secrets_doppler_project", "secrets_doppler_config"):
                requested[key] = None
                accepted[key] = None
    elif args.doppler_project is not None or args.doppler_config is not None:
        have = (
            accepted.get("secrets_doppler_project", current.secrets_doppler_project),
            accepted.get("secrets_doppler_config", current.secrets_doppler_config),
        )
        if bool(have[0]) != bool(have[1]) and not refused:
            refuse(
                "secrets_doppler_project",
                args.doppler_project or args.doppler_config,
                "Doppler needs both a project and a config: give --doppler-project and "
                "--doppler-config together (names only, never a token).",
            )

    topo_regions = current.topo_regions
    if args.clear_topo_regions:
        requested["topo_regions"] = None
        accepted["topo_regions"] = ()
        topo_regions = ()
    elif args.topo_regions is not None:
        raw_topo_regions = args.topo_regions
        requested["topo_regions"] = raw_topo_regions
        regions = tuple(r for r in (p.strip() for p in raw_topo_regions.split(",")) if r)
        if not regions:
            refuse(
                "topo_regions",
                raw_topo_regions,
                "--topo-regions gave no regions after splitting on ',' and stripping "
                "whitespace; give at least one of the station's map regions.",
            )
        else:
            try:
                effective_map_regions = cast(
                    tuple[str, ...], accepted.get("map_regions", current.map_regions)
                )
                candidate = Station(
                    map_regions=effective_map_regions,
                    topo_regions=regions,
                )
            except StationError as exc:
                refuse("topo_regions", raw_topo_regions, str(exc))
            else:
                accepted["topo_regions"] = candidate.topo_regions
                topo_regions = candidate.topo_regions
    if args.map_regions is not None and args.topo_regions is None and topo_regions:
        effective_map_regions = cast(
            tuple[str, ...], accepted.get("map_regions", current.map_regions)
        )
        kept = tuple(region for region in topo_regions if region in effective_map_regions)
        if kept != topo_regions:
            print(
                f"note: {len(topo_regions) - len(kept)} --topo-regions entr(ies) are no longer "
                "among the map regions and are dropped",
                file=sys.stderr if json_output else sys.stdout,
            )
            topo_regions = kept
            accepted["topo_regions"] = kept

    if args.clear_active_areas:
        if args.active_areas is not None:
            refuse(
                "active_areas",
                " ".join(args.active_areas),
                "--clear-active-areas makes everything loaded active; do not give "
                "--active-areas with it.",
            )
        else:
            requested["active_areas"] = None
            accepted["active_areas"] = None
    elif args.active_areas is not None:
        requested["active_areas"] = tuple(args.active_areas)
        try:
            candidate = Station(active_areas=tuple(args.active_areas))
        except StationError as exc:
            refuse("active_areas", " ".join(args.active_areas), str(exc))
        else:
            accepted["active_areas"] = candidate.active_areas
            unloaded = _unloaded_areas(candidate.active_areas or (), current)
            if unloaded:
                print(
                    f"note: not loaded yet, accepted all the same: {', '.join(unloaded)}",
                    file=sys.stderr if json_output else sys.stdout,
                )

    rig_flags = {
        "rig": args.rig,
        "rig_device": args.rig_device,
        "rig_baud": args.rig_baud,
        "rig_ptt_line": args.rig_ptt_line,
        "rig_owner": args.rig_owner,
    }
    rig_touched = any(value is not None for value in rig_flags.values())
    rig_notes: list[str] = []
    if args.clear_rig:
        if rig_touched:
            reason = (
                "--clear-rig cannot be combined with rig-setting flags; clear first, "
                "then set rig values in a second command."
            )
            refuse("rig", True, reason)
            for key, value in rig_flags.items():
                if value is not None:
                    requested[key] = value
                    refuse(key, value, reason)
        else:
            for key in rig_flags:
                requested[key] = None
                accepted[key] = None
            rig_notes.append("  rig            (cleared)")
    elif rig_touched:
        rig_args = argparse.Namespace(**vars(args))
        invalid_rig_fields: set[str] = set()
        for key, value in rig_flags.items():
            if value is not None:
                requested[key] = value
                try:
                    candidate = Station(**{key: value})
                except StationError as exc:
                    refuse(key, value, str(exc))
                    invalid_rig_fields.add(key)
                    setattr(rig_args, key, None)
                else:
                    setattr(rig_args, key, getattr(candidate, key))

        with contextlib.redirect_stderr(io.StringIO()) as captured:
            rig_result = _resolve_rig_flags(rig_args, current)
        rig_reason = captured.getvalue().strip()
        if isinstance(rig_result, int):
            reason = rig_reason.removeprefix("error:").strip() or "rig settings were refused"
            rejected_keys = [
                key
                for key, value in rig_flags.items()
                if value is not None and key not in invalid_rig_fields
            ]
            for key in rejected_keys:
                refuse(key, rig_flags[key], reason, rig_result)
        else:
            accepted.update(
                {
                    "rig": rig_result.rig,
                    "rig_device": rig_result.rig_device,
                    "rig_baud": rig_result.rig_baud,
                    "rig_ptt_line": rig_result.rig_ptt_line,
                    "rig_owner": rig_result.rig_owner,
                }
            )
            rig_notes.extend(rig_result.notes)

    if not requested and args.unattended is None:
        message = (
            "error: nothing to set. Pass at least one of --callsign, --grid-square, "
            "--node-alias, --map-regions, --map-freshness, --reference-books, --mirror, "
            "--clear-mirror, --mirror-require-hardware-key, --no-mirror-require-hardware-key, --doppler-project, --doppler-config, --clear-doppler, --dem-source, --topo-radius-km, --topo-regions, --topo-all, "
            "--active-areas, --clear-active-areas, "
            "--rig, --rig-device, --rig-baud, --rig-ptt-line, --rig-owner, --clear-rig, "
            "--unattended."
        )
        print(message, file=sys.stderr)
        if json_output:
            envelope.emit(
                envelope.ErrorDocument(
                    command="station set", exit_code=EXIT_FAILED, message=message
                )
            )
        return EXIT_FAILED

    if refused and not json_output:
        print(f"error: {refused[0].reason}", file=sys.stderr)
        return refusal_code

    try:
        station = dataclasses.replace(current, **accepted)  # type: ignore[arg-type]  # Values validated above.
    except StationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    station_fields = (
        "callsign",
        "grid_square",
        "node_alias",
        "map_regions",
        "map_freshness",
        "reference_books",
        "mirror",
        "mirror_require_hardware_key",
        "rig",
        "rig_device",
        "rig_baud",
        "rig_ptt_line",
        "rig_owner",
        "dem_source",
        "topo_radius_km",
        "topo_regions",
        "topo_all",
        "active_areas",
        "secrets_doppler_project",
        "secrets_doppler_config",
    )
    saved = (
        {}
        if refused
        else {
            key: getattr(station, key)
            for key in station_fields
            if getattr(station, key) != getattr(current, key)
        }
    )
    refused_keys = {item.key for item in refused}
    unchanged = {
        key: getattr(station, key)
        for key in requested
        if key not in refused_keys and getattr(station, key) == getattr(current, key)
    }
    if refused:
        station = current

    if args.clear_mirror and not refused:
        from hammunition.signers import SignerError, clear_mirror

        try:
            clear_mirror(owner=user or None)
        except SignerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_UNPLANNABLE

    if (accepted and not refused) or (args.unattended is not None and not refused):
        path = save_station(station, owner=user)
    else:
        path = config_path(user)
    doc = StationSetDocument(
        saved=saved,
        unchanged=unchanged,
        refused=tuple(refused),
        file=str(path),
    )

    code = (
        _apply_unattended(args, station, user)
        if args.unattended is not None and not refused
        else EXIT_OK
    )
    if json_output:
        for item in refused:
            print(f"error: {item.reason}", file=sys.stderr)
        for note in rig_notes:
            print(note, file=sys.stderr)
        envelope.emit(doc)
    else:
        from hammunition.interface.station import render_station_set

        text_fields = list(requested)
        if args.clear_rig or (rig_touched and not isinstance(rig_result, int)):
            text_fields = [
                field
                for field in text_fields
                if field not in ("rig", "rig_device", "rig_baud", "rig_ptt_line", "rig_owner")
            ]
            text_fields.append("rig")
        for line in render_station_set(doc, station, text_fields):
            print(line)
        for note in rig_notes:
            print(note)
        if "rig" in text_fields and station.rig is not None:
            print("  → run `hammunition install rig-service` to apply this to the running service")
    return EXIT_UNPLANNABLE if refused else code


def _unloaded_areas(tokens: tuple[str, ...], station: Station) -> tuple[str, ...]:
    """The *tokens* that match no state layer or region on disk.  D-082."""
    from hammunition import infra
    from hammunition.areas import Active, collect
    from hammunition.repeaters import overlay_dir

    loaded, _ = collect(
        overlay_dir(), infra.overlay_dir(), data_root(DEFAULT_PREFIX), station.map_regions
    )
    return Active(tokens).unloaded(
        [e.area for e in loaded if e.kind == "state"],
        [e.area for e in loaded if e.kind == "region"],
    )


@dataclasses.dataclass(frozen=True)
class _RigFlags:
    rig: str | None
    rig_device: str | None
    rig_baud: int | None
    rig_ptt_line: str | None
    rig_owner: str | None
    fields_set: list[str]
    notes: list[str]


def _resolve_rig_flags(args: argparse.Namespace, current: Station) -> _RigFlags | int:
    """Validate the rig flags against the catalog, enforcing the §4 table.

    Returns the resolved values and operator notes, or an exit code on a
    refusal. The station module checks only the shape a value needs; the
    catalog cross-checks — is it a rig, is the baud in range, does the kind
    allow this flag — are here (D-073 §4).
    """
    touched = any(
        v is not None
        for v in (args.rig, args.rig_device, args.rig_baud, args.rig_ptt_line, args.rig_owner)
    )
    if args.clear_rig:
        if touched:
            print(
                "error: --clear-rig cannot be combined with rig-setting flags; clear first, "
                "then set rig values in a second command.",
                file=sys.stderr,
            )
            return EXIT_FAILED
        return _RigFlags(None, None, None, None, None, ["rig"], ["  rig            (cleared)"])

    rig: str | None = args.rig or current.rig
    rig_changed = args.rig is not None and args.rig != current.rig
    rig_device: str | None = args.rig_device or current.rig_device
    rig_baud: int | None = (
        args.rig_baud if args.rig_baud is not None else None if rig_changed else current.rig_baud
    )
    ptt_line: str | None = args.rig_ptt_line or (None if rig_changed else current.rig_ptt_line)
    owner: str | None = args.rig_owner or current.rig_owner

    if not touched:
        return _RigFlags(rig, rig_device, rig_baud, ptt_line, owner, [], [])

    notes: list[str] = []
    if rig is None:
        print(
            "error: set --rig first (the device or hamlib:<model>); the other rig "
            "values describe the radio it names.",
            file=sys.stderr,
        )
        return EXIT_FAILED

    from hammunition.rig import RigError, check_rig_baud, elide_serial, resolve_rig

    try:
        _classes, devices = _load_hardware_catalog(args)
        res = resolve_rig(rig, devices)
    except (CatalogError, RigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE

    if res.kind == "cat":
        if ptt_line is not None:
            print(
                "error: --rig-ptt-line is for a radio with no CAT; this rig keys over CAT.",
                file=sys.stderr,
            )
            return EXIT_FAILED
        if res.uncatalogued and rig_baud is None:
            print(
                f"error: {rig} has no manifest, so --rig-baud is required — there is no "
                f"range in the catalog to take it from. Give the speed from the radio's "
                f"CAT RATE menu.",
                file=sys.stderr,
            )
            return EXIT_FAILED
        if rig_baud is not None and res.baud_range is not None:
            problem = check_rig_baud(rig_baud, res.baud_range)
            if problem is not None:
                print(f"error: {problem}", file=sys.stderr)
                return EXIT_FAILED
        if res.uncatalogued:
            notes.append(
                f"  note: radio {rig} has no manifest; its USB shape, ports and known "
                f"problems are unmeasured here."
            )
        elif res.baud_range is not None:
            lo, hi = res.baud_range
            notes.append(
                f"  note: this backend's CAT speed range is {lo}..{hi}; set --rig-baud to "
                f"the rate in the radio's CAT RATE menu."
            )
    else:  # ptt_only
        if rig_baud is not None:
            print(
                "error: --rig-baud is refused for a radio with no CAT — no data crosses the line.",
                file=sys.stderr,
            )
            return EXIT_FAILED
        if ptt_line is None:
            print(
                "error: --rig-ptt-line is required for a radio with no CAT (rts, dtr, or "
                "vox): the catalog cannot know which line your interface keys it on.",
                file=sys.stderr,
            )
            return EXIT_FAILED
        if owner == "flrig":
            print(
                "error: --rig-owner flrig needs a CAT rig for flrig to drive; this radio "
                "has no CAT.",
                file=sys.stderr,
            )
            return EXIT_FAILED

    if rig_device is not None:
        if "/dev/serial/by-id/" not in rig_device:
            notes.append(
                "  warning: the rig device is not a /dev/serial/by-id/ path; a /dev/ttyUSB "
                "number changes with plug order."
            )
        elif not Path(rig_device).exists():
            notes.append(
                f"  warning: {elide_serial(rig_device)} does not exist right now (the radio may "
                f"simply be off)."
            )

    return _RigFlags(rig, rig_device, rig_baud, ptt_line, owner, ["rig"], notes)


def _enabled_user_units() -> tuple[str, ...]:
    """The operator's enabled user units, for the disclosure. Best-effort: an
    empty tuple when the user manager cannot be asked."""
    try:
        result = SubprocessRunner().run(
            Command(
                argv=(
                    "systemctl",
                    "--user",
                    "list-unit-files",
                    "--state=enabled",
                    "--no-legend",
                    "--plain",
                ),
                description="List the operator's enabled user units",
            )
        )
    except BackendError:
        return ()
    if not result.ok:
        return ()
    return tuple(line.split()[0] for line in result.stdout.splitlines() if line.strip())


def _apply_unattended(args: argparse.Namespace, station: Station, user: str) -> int:
    """Opt-in linger for the station operator, through the devctl helper. D-073 §5a.

    ``--unattended`` enables linger; ``--no-unattended`` disables it, but only
    if Hammunition turned it on (the helper reads its own record). The plan
    states what linger keeps alive after logout before the prompt.
    """
    del station
    on = bool(args.unattended)
    code = _helper_ready()
    if code is not None:
        return code
    if on:
        print("\nKeeping your services running after you log out (linger):")
        units = _enabled_user_units()
        if units:
            print("  linger starts your user manager at boot and keeps it after logout, so")
            print("  every user service you have enabled keeps running — not only the rig:")
            for unit in units:
                print(f"    {unit}")
        else:
            print("  linger starts your user manager at boot and keeps every enabled user")
            print("  service running after you log out.")
        print(
            "  With the rig service among them, the transmitter is keyable through "
            "127.0.0.1:4532 with nobody at the machine."
        )
    verb_state = "on" if on else "off"
    command = Command(
        argv=("pkexec", HELPER_PATH, "linger", verb_state),
        description=f"Turn linger {verb_state} for {user or 'this account'}",
    )
    print(f"\n  # {command.description}\n  $ {command.display()}")
    if getattr(args, "dry_run", False):
        print("\nDry run: nothing above was executed.")
        return EXIT_OK
    return _run_helper(command, f"Linger is {verb_state}.")


def _apt_lists_note(apt: AptBackend) -> str:
    """When the local package lists were last fetched, as a disclosure.

    The report compares against the archive as those lists describe it; a
    laptop that last ran `apt-get update` before a trip is comparing against
    the archive of that day, and the line says which day.
    """
    if not apt.lists_populated():
        return "no package lists fetched; every apt row above is comparing against nothing"
    newest = max(
        (entry.stat().st_mtime for entry in apt.lists_dir.iterdir() if "_Packages" in entry.name),
        default=None,
    )
    if newest is None:
        return "no package lists fetched; every apt row above is comparing against nothing"
    when = datetime.fromtimestamp(newest, tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return f"last refreshed {when} (`sudo apt-get update` refreshes them; this report does not)"


#: Install blocks whose plan-time resolution asks a publisher directly, with
#: the Bunker route for each still to be built (#381). An offline run refuses
#: them by name rather than letting a probe or a git clone reach a publisher.
_OFFLINE_UNROUTED: tuple[tuple[type, str, bool, str | None], ...] = (
    # Copernicus terrain (Task 8), USGS 3DEP bare earth and US Topo (Task 9),
    # Kiwix books and CoMaps maps (Task 11) and git sources (Task 15, below)
    # are routed; what is left asks a publisher.
    (GitInstall, "git sources", False, None),
)


def _git_bundle_routed(install: GitInstall, name: str, context: ResolutionContext) -> bool:
    """Whether *name*'s pinned revision has a verified ``git-bundles`` entry
    on the enrolled Bunker (D-070, #381 Task 15) -- the route an offline run
    uses instead of a network clone, and an online one tries first. False
    with nothing enrolled, or a tag the manifest never recorded a repository
    commit for: neither is a route, however the question is asked, and this
    function never asks the publisher to find out."""
    if context.verified is None:
        return False
    commit = install.ref if COMMIT_SHA.fullmatch(install.ref) else install.commit
    if commit is None:
        return False
    from hammunition.gitbundles import bundle_name

    row = context.verified.catalogue.artifact(
        "git-bundles", bundle_name(name, commit), context.enrolment_id
    )
    return row is not None


def _offline_unrouted(
    plan: InstallPlan,
    built: frozenset[str] = frozenset(),
    *,
    plan_time: bool,
    context: ResolutionContext | None = None,
) -> list[Blocker]:
    """The units in *plan* whose resolution would ask a publisher, named.

    A kind with a provider is refused only for that provider. ``plan_time``
    selects the kinds a resolver probes while planning (asked before any
    resolver runs); the rest act at execution and are skipped when already
    built. A git unit the enrolled Bunker carries a verified bundle for
    (*context*, #381 Task 15) is routed and never named here -- the git
    backend itself checks the bundle before any build step, the same as
    every other preflight in this table; this is only the plan-time
    disclosure that a *type* match alone is not."""
    out: list[Blocker] = []
    for unit in plan.packages:
        if unit.name in built and not plan_time:
            continue
        for kind, what, at_plan_time, provider in _OFFLINE_UNROUTED:
            install = unit.block.install
            if (
                at_plan_time == plan_time
                and isinstance(install, kind)
                and (provider is None or getattr(install, "provider", None) == provider)
            ):
                if (
                    kind is GitInstall
                    and context is not None
                    and isinstance(install, GitInstall)
                    and _git_bundle_routed(install, unit.name, context)
                ):
                    continue
                out.append(
                    Blocker(
                        subject=unit.name,
                        reason=(
                            f"offline: resolving its {what} asks the publisher, and the Bunker "
                            f"route for that is not built yet"
                        ),
                        remedy="run it online, or leave this unit out of the offline run (#381)",
                    )
                )
    return out


def _resolution_context(
    *, offline: bool, no_mirror: bool, owner: str | None, write_trust: bool = True
) -> tuple[ResolutionContext, list[str]]:
    """The run's one :class:`ResolutionContext`, and the notes it earns.

    Enrolled and not ignored, the Bunker's catalogue is read and verified once
    here. Online, anything wrong with it (unreachable, a bad signature, a
    rollback, a malformed document, a trust state that cannot be written)
    switches the Bunker fallback off for the run with one note naming the
    reason and ``--no-mirror``; the install proceeds, since every download
    stays pinned by its own hash, and unverified metadata is never used.
    Offline, the same failures raise, and so does nothing enrolled (naming
    ``hammunition mirror enrol URL``). Accepting a newer catalogue advances the
    local trust state, and the notes say so; with ``write_trust=False`` (a dry
    run) nothing is written and the note says what a real run would do."""
    from hammunition.catalogue import CatalogueError
    from hammunition.mirror import consistent_state, fetch_transport
    from hammunition.mirror_transport import CatalogueInputs, load_catalogue
    from hammunition.resolution import NO_BUNKER
    from hammunition.signers import SignerError, load_mirror

    station = load_station(owner=owner)
    state = load_mirror(owner=owner)
    consistent_state(station.mirror, state)
    context = ResolutionContext(offline=offline)
    if state is None:
        if offline:
            raise SignerError(NO_BUNKER)
        return context, []
    if no_mirror and not offline:
        return context, []
    transport = fetch_transport(station.mirror, state)
    if transport is None:  # pragma: no cover -- consistent_state proved the URLs equal
        raise SignerError("station mirror differs from enrolled mirror")
    try:
        verified = load_catalogue(
            state,
            require_hardware=station.mirror_require_hardware_key,
            now=datetime.now(UTC),
            transport=transport,
            owner=owner,
            advance=write_trust,
        )
    except (SignerError, CatalogueError, BackendError, OSError) as exc:
        if offline:
            raise
        return context, [
            f"Bunker {state.name} fallback is off for this run: {exc}. The install "
            f"proceeds because every download stays pinned by its own hash; --no-mirror "
            f"skips the Bunker"
        ]
    context.verified = verified
    context.enrolment_id = state.enrolment_id
    context.inputs = CatalogueInputs(transport)
    serial = verified.catalogue.serial
    advanced = serial > state.accepted_serial or (
        serial == state.accepted_serial and state.generated is None
    )
    if not advanced:
        trust = f"the local trust state already holds serial {state.accepted_serial}; unchanged"
    elif write_trust:
        trust = (
            f"the local trust state in the mirror file advanced from serial "
            f"{state.accepted_serial} to {serial} (a local write, not a package action)"
        )
    else:
        trust = (
            f"dry run: the local trust state was not written; a real run advances it from "
            f"serial {state.accepted_serial} to {serial}"
        )
    notes = [
        f"Bunker {state.name}: catalogue serial {serial} verified with {verified.key.id}; {trust}"
    ]
    notes.extend(f"Bunker {state.name}: {warning}" for warning in verified.warnings)
    return context, notes


_DEBIAN_ARCH = {
    "x86_64": "amd64",
    "aarch64": "arm64",
    "armv7l": "armhf",
    "armv6l": "armel",
    "i686": "i386",
    "i386": "i386",
    "riscv64": "riscv64",
    "ppc64le": "ppc64el",
    "s390x": "s390x",
    "loongarch64": "loong64",
}
_ARCH_NAME = re.compile(r"[a-z0-9][a-z0-9\-]*", re.ASCII)


def _dpkg_architectures(option: str) -> list[str] | None:
    """The words dpkg prints for a read-only architecture query, or None when
    dpkg is not installed. A dpkg that fails, or prints something that is not an
    architecture name, is an error: never papered over."""
    try:
        result = subprocess.run(
            ["dpkg", option], capture_output=True, text=True, timeout=30, check=False
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"dpkg {option} could not be run: {exc}") from exc
    if result.returncode:
        raise BackendError(f"dpkg {option} failed: {result.stderr.strip() or result.returncode}")
    words = result.stdout.split()
    if not all(_ARCH_NAME.fullmatch(word) for word in words):
        raise BackendError(f"dpkg {option} answered {result.stdout.strip()!r}, not architectures")
    return words


def _native_arch() -> str:
    """This machine's Debian architecture: ``dpkg --print-architecture``, and
    only without dpkg the kernel's machine name mapped to Debian's."""
    words = _dpkg_architectures("--print-architecture")
    if words is not None:
        if len(words) != 1:
            raise BackendError(f"dpkg --print-architecture answered {words!r}, not one name")
        return words[0]
    machine = platform.machine()
    if machine not in _DEBIAN_ARCH:
        raise BackendError(f"cannot name this machine's native architecture from {machine!r}")
    return _DEBIAN_ARCH[machine]


def _foreign_architectures() -> tuple[str, ...]:
    """The foreign architectures dpkg is configured for (none without dpkg)."""
    return tuple(_dpkg_architectures("--print-foreign-architectures") or ())


def _dpkg_depends(path: Path) -> str:
    """The ``Pre-Depends`` and ``Depends`` of the .deb at *path* as one field
    (local, no network)."""
    result = subprocess.run(
        ["dpkg-deb", "--field", str(path), "Pre-Depends", "Depends"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise OSError(result.stderr.strip() or f"dpkg-deb exited {result.returncode}")
    values = [
        " ".join(value.split())
        for value in re.split(r"^(?:Pre-)?Depends:", result.stdout, flags=re.MULTILINE)[1:]
    ]
    return ", ".join(value for value in values if value)


def _deb_recommends(path: Path) -> str:
    """The Recommends field of the .deb at *path* as one line (local, no network)."""
    result = subprocess.run(
        ["dpkg-deb", "--field", str(path), "Recommends"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        return f"(could not be read: {result.stderr.strip() or f'dpkg-deb exited {result.returncode}'})"
    return " ".join(re.sub(r"^Recommends:", "", result.stdout).split())


def _deb_unmet_file(apt: AptBackend, path: Path) -> list[str]:
    """Dependency groups of the vendor .deb at *path* that no installed package
    meets: not installed (at the stated architecture), outside the stated version
    range, or a field that does not parse (never assumed met)."""
    try:
        native = _native_arch()
        groups = parse_deb_dependencies(_dpkg_depends(path))
        foreign = (
            _foreign_architectures()
            if any(d.arch == "any" for group in groups for d in group)
            else ()
        )
        names = sorted(
            {n for group in groups for d in group for n in deb_probe_names(d, native, foreign)}
        )
        states = apt.probe(names) if names else {}
    except (OSError, subprocess.TimeoutExpired, BackendError, DebDependencyError) as exc:
        return [f"(its dependencies could not be read: {exc})"]
    installed = {name: state.installed for name, state in states.items()}
    return [
        " | ".join(d.text() for d in group)
        for group in groups
        if not deb_group_met(group, installed, native, foreign)
    ]


def _deb_unmet(apt: AptBackend, fetcher: Fetcher, unit: PlannedPackage) -> list[str]:
    """Dependency groups of a cached vendor .deb that no installed package meets."""
    block = unit.block.install
    assert isinstance(block, BinaryInstall)
    return _deb_unmet_file(apt, fetcher.path_for(block.artifact))


def _provenance_notes(
    context: ResolutionContext, already: frozenset[tuple[str, str]] = frozenset()
) -> list[str]:
    """One line per distinct provenance, naming the items the catalogue answered
    (those not in *already*, which an earlier pass printed)."""
    by_text: dict[str, list[str]] = {}
    for (unit, name), text in sorted(context.notes.items()):
        if (unit, name) not in already:
            by_text.setdefault(text, []).append(f"{unit}/{name}")
    return [f"{', '.join(items)}: {text}" for text, items in by_text.items()]


@envelope.json_capable()
def cmd_update(args: argparse.Namespace) -> int:
    """Installed versus the catalog, as a report. D-053: nothing runs."""
    from hammunition.catalogue import CatalogueError
    from hammunition.interface.update import build_update
    from hammunition.signers import SignerError

    # Validated first, whatever else the request is: --offline never asks
    # upstream, so the two cannot be combined, even for an empty request.
    offline = bool(getattr(args, "offline", False))
    if offline and args.upstream:
        print(
            "error: --offline never asks upstream and --upstream asks it; the two cannot "
            "be combined",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    offline_note: str | None = None
    if offline:
        try:
            _, context_notes = _resolution_context(
                offline=True, no_mirror=False, owner=operator(args) or None
            )
        except (SignerError, StationError, CatalogueError, BackendError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_UNPLANNABLE
        offline_note = (
            "offline: no publisher was asked; the Bunker's catalogue was read. This report reads the local apt lists and the install "
            "records. " + " ".join(f"{note}." for note in context_notes)
        )

    try:
        target = Target.detect()
    except DetectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if not target.is_debian_family:
        print(f"error: {target.describe()} is not Debian-family.", file=sys.stderr)
        return EXIT_FAILED

    catalog_root = find_catalog(args.catalog)
    packages, profiles = load_all(catalog_root)
    runner = SubprocessRunner()
    apt = AptBackend(runner)
    user = operator(args)
    read_log = TransactionLog(owner=user or None)

    names = list(dict.fromkeys(args.names))
    from_log = not names
    if not names:
        names = list(requested_units(read_log.read()))
        if not names:
            if envelope.wanted(args):
                envelope.emit(
                    build_update(
                        target,
                        report(
                            InstallPlan(target=target, packages=()),
                            apt_states={},
                            present={},
                            built=(),
                        ),
                        lists_note=_apt_lists_note(apt),
                        from_log=True,
                        upstream=None,
                        offline=offline_note,
                    )
                )
                return EXIT_OK
            print(f"Target: {target.describe()}")
            print(
                "Nothing to compare: the transaction log records no install request here "
                f"({read_log.path}). Name units or profiles to compare them anyway."
            )
            if offline_note is not None:
                print(offline_note)
            return EXIT_OK
        print(f"Comparing the {len(names)} unit(s) the transaction log has ever named here.")

    retired: dict[str, PackageManifest] = {}
    update_profiles = dict(profiles)
    for name in names:
        manifest = packages.get(name)
        if name not in profiles and manifest is not None and manifest.status is Status.retired:
            retired.setdefault(name, manifest)
        profile = profiles.get(name)
        if profile is None:
            continue
        active_members: list[str] = []
        for member in profile.packages:
            member_manifest = packages.get(member)
            if member_manifest is not None and member_manifest.status is Status.retired:
                retired.setdefault(member, member_manifest)
            else:
                active_members.append(member)
        if len(active_members) != len(profile.packages):
            update_profiles[name] = profile.model_copy(update={"packages": active_members})
    update_names = [name for name in names if name not in retired]

    try:
        station = load_station(owner=user)
    except StationError:
        station = Station()
    repos = AptRepoBackend(owner=user or None)
    try:
        plan = resolve(
            update_names,
            catalog=packages,
            profiles=update_profiles,
            target=target,
            apt=apt,
            user=user,
            station=station,
            repos=repos,
            kernel=KernelProbe.detect(),
            java=JavaProbe.detect(),
            desktops=scan_sessions(),
            log=read_log,
        )
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "\nNothing was compared. The report resolves the request the way install "
            "would, so a blocker here is the same blocker install would meet.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    builds = build_root(user or None)
    source = SourceBackend(
        Fetcher(owner=user or None, offline=offline), build_root=builds, owner=user or None
    )
    git = GitBackend(
        runner=runner,
        build_root=builds,
        prefix=source.prefix,
        jobs=source.jobs,
        owner=source.owner,
        fetcher=source.fetcher,
    )
    binary = BinaryBackend(
        fetcher=source.fetcher,
        runner=runner,
        build_root=builds,
        prefix=source.prefix,
        owner=source.owner,
    )
    built = already_built(
        plan, log=read_log, prefix=source.prefix, source=source, git=git, binary=binary
    )
    present = {
        planned.name: build_effects_present(planned, prefix=source.prefix)
        for planned in plan.packages
        if build_dir(planned, source=source, git=git, binary=binary) is not None
    }
    apt_names: list[str] = []
    for planned in plan.packages:
        method = planned.block.install
        if isinstance(method, AptInstall):
            apt_names.extend(method.packages)
    try:
        states = apt.probe(list(dict.fromkeys(apt_names)))
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    # osm-regions, offline (D-053): each installed region's `.source`
    # sidecar against the pinned snapshot the station's own freshness mode
    # would resolve to today (fix round 1, I1) -- not the newest pin of any
    # snapshot, which reported a yearly install behind a monthly-shaped pin
    # forever. No fetch, no probe -- just what is on disk and what the
    # catalog carries.
    pins_path = catalog_root / "data" / "geofabrik-pins.yaml"
    current_pinned = (
        current_pinned_snapshots(
            station.freshness, date.today(), load_pins(pins_path), station.map_regions
        )
        if pins_path.is_file()
        else {}
    )
    regions_by_unit = {
        planned.name: region_snapshots(
            installed_slugs(data_root(source.prefix) / planned.name), current_pinned
        )
        for planned in plan.packages
        if isinstance(planned.block.install, RegionalDataInstall)
    }

    # Kiwix books, offline (D-066): each chosen book's pinned file on disk.
    chosen_books: dict[str, list[BookFile]] = {}
    books_by_unit: dict[str, tuple[str, str]] = {}
    for planned in plan.packages:
        if not isinstance(planned.block.install, KiwixBooksInstall):
            continue
        try:
            chosen_books[planned.name] = resolve_books(
                station.reference_books,
                load_book_list(catalog_root),
                load_pin_file(catalog_root),
            )
        except KiwixError as exc:
            books_by_unit[planned.name] = ("unknown", str(exc))
            continue
        books_by_unit[planned.name] = books_state(
            chosen_books[planned.name], data_root(source.prefix) / planned.name
        )

    # CoMaps' maps, offline (D-069): each map the station's regions need
    # against its pinned version, counted, from the carried table.
    mwm_by_unit: dict[str, tuple[str, str]] = {}
    comaps: dict[str, ComapsPins] = {}
    for planned in plan.packages:
        if not isinstance(planned.block.install, MwmRegionsInstall):
            continue
        try:
            comaps_pins = load_comaps_pins(catalog_root)
        except ComapsError as exc:
            mwm_by_unit[planned.name] = (UNKNOWN, str(exc))
            continue
        comaps[planned.name] = comaps_pins
        files, unmapped = resolve_regions(station.map_regions, comaps_pins)
        mwm_by_unit[planned.name] = mwm_state(
            files, data_root(source.prefix) / planned.name, unmapped=len(unmapped)
        )

    result = report(
        plan,
        apt_states=states,
        present=present,
        built=built,
        regions=regions_by_unit,
        tiles=installed_tile_counts(plan, source.prefix),
        no_terrain=no_terrain_counts(plan, source.prefix),
        quads=installed_quad_counts(plan, source.prefix, catalog_root),
        books=books_by_unit,
        mwm=mwm_by_unit,
        retired=tuple(retired.values()),
    )
    lists_note = _apt_lists_note(apt)
    upstream = (
        _upstream_rows(plan, runner, books=chosen_books, comaps=comaps) if args.upstream else None
    )
    if envelope.wanted(args):
        envelope.emit(
            build_update(
                target,
                result,
                lists_note=lists_note,
                from_log=from_log,
                upstream=upstream,
                offline=offline_note,
            )
        )
        return EXIT_OK
    print(f"Target: {target.describe()}")
    print(render(result, lists_note=lists_note, upstream_asked=bool(args.upstream)))
    if offline_note is not None:
        print(offline_note)
    if upstream is not None:
        print()
        print(render_upstream(upstream))
    return EXIT_OK


def _upstream_rows(
    plan: InstallPlan,
    runner: SubprocessRunner,
    *,
    books: Mapping[str, Sequence[BookFile]] | None = None,
    comaps: Mapping[str, ComapsPins] | None = None,
) -> list[UpstreamRow]:
    """D-053's second half: the catalog's pin against what upstream publishes.

    Opt-in because it is the one thing the engine does that talks to someone
    else's server. A GITHUB_TOKEN in the environment is sent to GitHub's API
    only, for the rate limit; tags come from `git ls-remote`, which needs no
    token on any host.
    """
    token = os.environ.get("GITHUB_TOKEN") or None

    def http(url: str) -> str:
        return http_get(url, token=token)

    def ls_remote(url: str) -> list[str]:
        result = runner.run(
            Command(
                argv=("git", "ls-remote", "--tags", "--refs", "--", url),
                description=f"List the tags at {url}",
                requires_root=False,
            )
        )
        if not result.ok:
            raise BackendError(f"git ls-remote exited {result.returncode}: {result.stderr.strip()}")
        return parse_ls_remote(result.stdout)

    rows = [
        probe_upstream(planned.manifest, http=http, ls_remote=ls_remote)
        for planned in plan.packages
    ]
    # D-066: a book unit is asked about per chosen book, of Kiwix only.
    kiwix = KiwixProbe()
    for unit, chosen in (books or {}).items():
        rows.extend(probe_kiwix(unit, chosen, text=kiwix.text))
    # D-069: CoMaps' maps are asked of the CDN, once per unit: is the pinned
    # version still published, and how old is it.
    for unit, comaps_pins in (comaps or {}).items():
        rows.append(probe_comaps_maps(unit, comaps_pins, head=CdnProbe().head, today=date.today()))
    return [r for r in rows if r.state != NOT_UPSTREAM]


def _station_for(
    args: argparse.Namespace,
    packages: Mapping[str, PackageManifest],
    profiles: Mapping[str, ProfileManifest],
    user: str,
) -> Station:
    """The station values this run will use.

    Three sources, later winning: the saved file, then `--callsign` and friends,
    then a prompt — and the prompt happens only when the request actually needs
    a value, the terminal can answer, and `--yes` was not given. Asking for a
    callsign to install a spectrum analyser would be the kind of prompt people
    learn to dismiss, which is what makes the consent gates worthless.
    """
    try:
        station = load_station(owner=user)
    except StationError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        print("  continuing without saved station values.", file=sys.stderr)
        station = Station()

    overrides = {
        field: value
        for field, value in (
            ("callsign", args.callsign),
            ("grid_square", args.grid_square),
            ("node_alias", args.node_alias),
        )
        if value
    }
    if overrides:
        station = Station(**{**station.as_dict(), **overrides})

    if args.yes or not is_interactive():
        return station

    needed: set[str] = set()
    for name in args.names:
        for package in profiles[name].packages if name in profiles else [name]:
            manifest = packages.get(package)
            if manifest is not None:
                needed |= manifest.station_variables
    outstanding = station.missing(needed)
    if not outstanding:
        return station

    print("Some configuration in this request needs values only you can supply.")
    print("Leave any blank to skip it — the package still installs and the file is not written.\n")
    station = prompt_for(outstanding, station)
    if station.as_dict():
        saved = save_station(station, owner=user)
        print(f"\nSaved to {saved} (mode 0600).\n")
    return station


def _apply_suggestions(
    names: list[str],
    profiles: Mapping[str, ProfileManifest],
    *,
    assume_yes: bool,
) -> tuple[list[str], list[str]]:
    """Resolve each requested profile's suggestion groups (Q-015 #1).

    Detection first: any of the group's ``detect_commands`` on PATH means the
    system already has an answer and it is respected — nothing offered,
    nothing installed. Only an interactive run without ``--yes`` gets the
    selection prompt, and skipping is always an option; a non-interactive run
    notes the skip instead of blocking (the D-035 shape). Returns the extra
    package names chosen and the notes to print with the plan.
    """
    import shutil

    extra: list[str] = []
    notes: list[str] = []
    for name in names:
        profile = profiles.get(name)
        if profile is None:
            continue
        for group in profile.suggests_one_of:
            found = next((c for c in group.detect_commands if shutil.which(c)), None)
            if found:
                notes.append(
                    f"{group.name}: `{found}` is already installed — respected, "
                    f"nothing offered ({name} profile)"
                )
                continue
            if assume_yes or not is_interactive():
                notes.append(
                    f"{group.name}: none detected and this run cannot ask — skipped. "
                    f"The {name} profile's docs list the options "
                    f"({', '.join(group.options)}); install one by name any time"
                )
                continue
            print(f"\nThe {name} profile suggests a {group.name}, and none was detected.")
            print(
                textwrap.fill(group.reason, width=78, initial_indent="  ", subsequent_indent="  ")
            )
            for index, option in enumerate(group.options, start=1):
                flag = "  (recommended)" if option == group.recommended else ""
                print(f"  [{index}] {option}{flag}")
            print("  [s] skip — install none")
            default = ""
            if group.recommended in group.options:
                default = str(group.options.index(group.recommended) + 1)
            prompt = f"Choose a {group.name} [1-{len(group.options)}/s]"
            prompt += f" (default {default}): " if default else ": "
            answer = input(prompt).strip().lower() or default
            if answer.isdigit() and 1 <= int(answer) <= len(group.options):
                chosen = group.options[int(answer) - 1]
                extra.append(chosen)
                notes.append(f"{group.name}: you chose {chosen}; added to this transaction")
            else:
                notes.append(f"{group.name}: skipped by choice")
    return extra, notes


@envelope.json_capable()
def cmd_maps_regions(args: argparse.Namespace) -> int:
    """Every region Geofabrik's region index names, filtered by a substring.  D-057.

    Fetches the index only when this command runs -- network on request,
    like `update --upstream`, never as a side effect of any other command
    and never at import time. ``index-v1-nogeom.json`` (0.51 MB, measured
    2026-09-28) carries the same ``properties.urls.pbf`` shape
    :func:`hammunition.geofabrik.region_ids` reads as ``index-v1.json``
    (3.79 MB); fetching the smaller one is free (fix round 1, M6).
    """
    probe = UrllibProbe()
    try:
        index_json = probe.text(f"{BASE}/index-v1-nogeom.json")
        ids = region_ids(index_json)
    except GeofabrikError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    needle = (args.filter or "").casefold()
    matched = tuple(region for region in ids if needle in region.casefold())
    if envelope.wanted(args):
        from hammunition.interface.regions import RegionsDocument

        envelope.emit(RegionsDocument(filter=args.filter, regions=matched))
        return EXIT_OK
    for region in matched:
        print(region)
    return EXIT_OK


@envelope.json_capable()
def cmd_logs(args: argparse.Namespace) -> int:
    """List the run logs, print the newest, or say where it is.  D-077."""
    from hammunition import runlog
    from hammunition.interface.logs import LogsDocument, RunEntry, render_logs

    directory = runlog.logs_dir(operator(args) or None)
    runs = runlog.list_runs(directory)
    if args.last or args.path:
        if envelope.wanted(args) and not args.path:
            print(
                "error: --last prints a log as text; use the plain `logs --json` list",
                file=sys.stderr,
            )
            return EXIT_UNPLANNABLE
        if not runs:
            print(f"No run logs yet in {directory}.", file=sys.stderr)
            return EXIT_FAILED
        newest = runs[0].path
        if args.path:
            print(newest)
            return EXIT_OK
        try:
            sys.stdout.write(newest.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            print(f"error: cannot read {newest}: {exc}", file=sys.stderr)
            return EXIT_FAILED
        return EXIT_OK
    doc = LogsDocument(
        directory=str(directory),
        total_bytes=runlog.total_size(directory),
        max_files=runlog.MAX_FILES,
        max_bytes=runlog.MAX_BYTES,
        runs=tuple(
            RunEntry(
                path=str(r.path),
                started=r.started,
                command=r.command,
                pid=r.pid,
                size=r.size,
                result=r.result,
                exit_code=r.exit_code,
            )
            for r in runs
        ),
    )
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_logs(doc):
        print(line)
    return EXIT_OK


def _stream_step(argv: Sequence[str], cwd: Path) -> int:
    """Run one self-update step, its output printed line by line (so the run log
    has it) and its command and exit code recorded."""
    from hammunition import runlog

    run = runlog.current()
    began = run.command_start(argv) if run else 0.0
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\n"))
    code = proc.wait()
    if run:
        run.command_end(argv, code, began)
    return code


@envelope.json_capable(dry_run_only=True)
def cmd_self_update(args: argparse.Namespace) -> int:
    """Pull the engine's own checkout and re-run bootstrap.  #303.

    Touches the checkout and its venv only: never apt, the catalog's installs or
    the station. A dirty tree, a branch other than main (without --release) and
    a non-fast-forward are refused with a sentence; nothing is reset.
    """
    from hammunition import selfupdate
    from hammunition.interface.selfupdate import (
        SelfUpdateDocument,
        StepView,
        render_self_update,
    )

    root = selfupdate.find_checkout()
    if root is None:
        print(
            "error: this engine is not running from a git checkout with a bootstrap.sh "
            "(a packaged install updates through its package manager); nothing to update.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    dry = bool(args.dry_run)
    if not dry and os.geteuid() == 0:
        print(
            "error: self-update runs as the account that owns the checkout, never as root; "
            "run it without sudo (bootstrap asks for sudo itself, only when it must).",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    try:
        selfupdate.preflight(root, release=args.release)
        planned = selfupdate.steps(root, release=args.release)
        if not envelope.wanted(args):
            print(f"Engine checkout: {root}")
            print("Steps (each is printed before it runs):")
            for number, step in enumerate(planned, 1):
                print(f"  {number}. {step.description}")
                print(f"       $ {shlex.join(step.argv)}")
            print(
                "Never touched: apt, installed units, station config. "
                "The fetch below runs even under --dry-run; it changes no file in the tree."
            )
            print()
            print(f"$ {shlex.join(planned[0].argv)}")
        selfupdate.fetch(root)
        resolved = selfupdate.resolve(root, release=args.release)
    except selfupdate.Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    before_checkout = selfupdate.checkout_version(root)
    before_installed = selfupdate.installed_version()
    final = selfupdate.steps(root, release=args.release, target=resolved.target)
    doc = SelfUpdateDocument(
        checkout=str(root),
        release=bool(args.release),
        target=resolved.target,
        up_to_date=resolved.up_to_date,
        checkout_version=before_checkout,
        installed_version=before_installed,
        arriving=resolved.arriving,
        steps=tuple(StepView(s.description, s.argv) for s in final),
        dry_run=dry,
    )
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    print()
    for line in render_self_update(doc):
        print(line)
    if dry:
        print("\nDry run: nothing was merged and bootstrap did not run.")
        return EXIT_OK
    if not args.yes and not _prompt("\nProceed with the steps above?"):
        print("Not confirmed; nothing was merged and bootstrap did not run.")
        return EXIT_CONSENT
    to_run = final[1:] if not resolved.up_to_date else final[2:]
    for step in to_run:
        print(f"\n$ {shlex.join(step.argv)}")
        code = _stream_step(step.argv, root)
        if code != 0:
            print(
                f"error: `{shlex.join(step.argv)}` exited {code}; stopping here. "
                "The checkout is as that step left it; run `git status` to see.",
                file=sys.stderr,
            )
            return EXIT_FAILED
    after_checkout = selfupdate.checkout_version(root)
    after_installed = selfupdate.installed_version_in(root)
    print()
    print(f"Version before: {selfupdate.mismatch_detail(before_checkout or '?', before_installed)}")
    print(f"Version after:  {selfupdate.mismatch_detail(after_checkout or '?', after_installed)}")
    if after_checkout is not None and after_installed != after_checkout:
        print(
            "warning: the installed version still differs from the checkout's; "
            "run `hammunition doctor` to see why.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    return EXIT_OK


@envelope.json_capable()
def cmd_transactions(args: argparse.Namespace) -> int:
    """List each transaction in chronological order, including rotated history. D-077."""
    from hammunition import runlog
    from hammunition.interface.transactions import (
        TransactionsDocument,
        build_transactions,
        render_transactions,
    )

    if args.last is not None and args.last < 1:
        print("error: --last must be a positive number", file=sys.stderr)
        return EXIT_UNPLANNABLE

    owner = operator(args) or None
    log = TransactionLog(owner=owner)
    running_logs = {
        str(run.path) for run in runlog.list_runs(runlog.logs_dir(owner)) if run.result == "running"
    }
    doc = build_transactions(list(log.read()), running_logs=running_logs)
    if args.last is not None:
        doc = TransactionsDocument(transactions=doc.transactions[-args.last :])
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_transactions(doc):
        print(line)
    return EXIT_OK


@envelope.json_capable()
def cmd_artifacts(args: argparse.Namespace) -> int:
    """Every remote data artifact the engine would fetch for the selection
    on the command line, with no station and no install.  D-070.

    Hammunition Bunker's one source of what to mirror. The network is asked
    exactly as the plan asks it -- Geofabrik for a region's dated file and
    MD5 and its outline, the Copernicus bucket for an unpinned tile's size
    and ETag -- and only for what the selection names. Reference books come
    from the carried pins alone (D-066).
    """
    from hammunition.artifacts import (
        SelectionError,
        input_regions,
        list_artifacts,
        list_git_pins,
        list_inputs,
        select_units,
    )
    from hammunition.interface.artifacts import ArtifactsDocument, render_artifacts

    regions: tuple[str, ...] = ()
    if args.map_regions is not None:
        regions = tuple(r for r in (p.strip() for p in args.map_regions.split(",")) if r)
        if not regions:
            print(
                "error: --map-regions gave no regions after splitting on ',' and stripping "
                "whitespace; give at least one, or leave the flag out.",
                file=sys.stderr,
            )
            return EXIT_UNPLANNABLE
        try:
            Station(map_regions=regions)  # the shape station config accepts, nothing more
        except StationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_UNPLANNABLE
    books: tuple[str, ...] = ()
    if args.reference_books is not None:
        books = tuple(b for b in (p.strip() for p in args.reference_books.split(",")) if b)
        if not books:
            print(
                "error: --reference-books gave no book ids after splitting on ',' and "
                "stripping whitespace; give at least one, or leave the flag out.",
                file=sys.stderr,
            )
            return EXIT_UNPLANNABLE
        try:
            # The shape station config accepts, nothing more; an id the book
            # list does not carry is listed as deferred, as a region is.
            books = Station(reference_books=books).reference_books
        except StationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_UNPLANNABLE
    requested = (
        tuple(u for u in (p.strip() for p in args.units.split(",")) if u)
        if args.units is not None
        else ()
    )
    catalog_root = find_catalog(args.catalog)
    catalog = load_catalog(catalog_root / "packages")
    try:
        units = select_units(catalog, requested)
    except SelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    # Shared so a region's outline is fetched once even though several units
    # and inputs may want it (Task 16); no explicit CLI bound exists for this
    # command yet, so every selection is whole-region, which the laptop can
    # always narrow further.
    region_probe = MemoProbe(UrllibProbe())
    bound = ALL
    entries = list_artifacts(
        units,
        regions=regions,
        books=books,
        freshness=args.map_freshness,
        catalog=catalog,
        catalog_root=catalog_root,
        today=date.today(),
        region_probe=region_probe,
        tile_probe=S3Probe(),
        register_probe=AcmaProbe(),
        snapshot_probe=SnapshotHead(),
        bound=bound,
        gateway=GatewayProbe(),
    )
    try:
        inputs = list_inputs(
            input_regions(units, regions, catalog),
            catalog_root=catalog_root,
            probe=region_probe,
            bound=bound,
        )
        git_pins = list_git_pins(units, catalog)
    except SelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    doc = ArtifactsDocument(
        map_regions=regions,
        map_freshness=args.map_freshness,
        reference_books=books,
        units=units,
        artifacts=entries,
        inputs=inputs,
        git_pins=git_pins,
    )
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_artifacts(doc):
        print(line)
    return EXIT_OK


def _read_config_nofollow(path: Path) -> tuple[str, int | None]:
    """*path*'s text and mode, or ``("", None)`` when absent.

    Opened with ``O_NOFOLLOW``: a symbolic link in place of the file is
    refused, never read through and never renamed over, because what it
    points at is not a file this command created (the path-link.sh rule).
    Anything but a regular file is refused too. Raises :class:`OSError`
    with a message naming what was found.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if path.is_symlink():  # a dangling link: still not ours to replace
            raise OSError(f"{path} is a symbolic link; left as it is") from None
        return "", None
    except OSError as exc:
        if path.is_symlink():
            raise OSError(f"{path} is a symbolic link; left as it is") from None
        raise OSError(f"cannot read {path}: {exc.strerror or exc}") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"{path} is not a regular file; left as it is")
        raw = handle.read()
    try:
        return raw.decode("utf-8"), stat.S_IMODE(info.st_mode)
    except UnicodeDecodeError:
        raise OSError(f"{path} is not UTF-8 text; left as it is") from None


def _replace_atomically(path: Path, text: str, mode: int | None) -> None:
    """Write *text* to a new file beside *path* and rename it over *path*.

    The temporary file is created exclusively (``mkstemp``) in the same
    directory, so the rename is atomic and nothing pre-planted at a fixed
    name is written through. An existing file's mode is kept; a new one is
    0600, as mkstemp creates it.
    """
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _repeater_poi_paths(text: str) -> tuple[str, bool]:
    """*text* with the operator's overlay POI directories (repeaters, D-064;
    infrastructure, D-075) in ``[Canvas] poiPaths`` while they hold a ``.poi``
    and out of it while not; and whether any does.

    With areas active (D-082) the directory registered is instead
    ``overlays/active-poi``, links to the active areas' ``.poi`` files, which is
    brought up to date here. A QMapShack open during an import writes its own
    list back when it exits, so the launcher puts the paths back before each
    start (D-064). Raises
    :class:`~hammunition.qmapshack_config.QmsConfigError` like
    :func:`~hammunition.qmapshack_config.ensure_paths`."""
    from hammunition.qmapshack_config import Wanted, ensure_paths

    keep, drop, _ = _poi_wants()
    # One Wanted per key each way: ensure_paths edits a key's line once.
    wants = [Wanted("Canvas", "poiPaths", tuple(map(str, keep)))] if keep else []
    gone = [Wanted("Canvas", "poiPaths", tuple(map(str, drop)))] if drop else []
    return ensure_paths(text, wants, remove=gone), bool(keep)


def _active_station() -> tuple[Active, tuple[str, ...]]:
    """The active areas the station holds (None: everything), and its map
    regions. An unreadable station file is everything active, as unset.  D-082."""
    from hammunition.areas import Active

    try:
        station = load_station(owner=operator(argparse.Namespace()) or None)
    except StationError:
        return Active(), ()
    return Active.from_station(station), station.map_regions


def _layer_active(area: str | None) -> bool:
    """Whether a layer of *area* (None: of no area) is drawn right now."""
    active, universe = _active_station()
    return active.area(area, universe)


def _poi_wants(
    active: Active | None = None, *, sync: bool = True
) -> tuple[list[Path], list[Path], PoiLinks]:
    """The directories ``[Canvas] poiPaths`` should name and the ones it should
    not, and the links directory's plan (made, when *sync*).  D-082.

    Everything active: the layer directories that hold a ``.poi``, as before
    the switch. Otherwise only ``overlays/active-poi``, which QMapShack reads as
    a directory and which holds links to the active areas' files."""
    from hammunition import infra
    from hammunition.areas import plan_poi_links, sync_poi_links
    from hammunition.repeaters import overlay_dir, overlays_root

    if active is None:
        active = _active_station()[0]
    repeater_dir, infra_dir = overlay_dir(), infra.overlay_dir()
    plan = plan_poi_links(overlays_root(), repeater_dir, infra_dir, active, _active_station()[1])
    if sync:
        sync_poi_links(plan)
    if active.everything:
        present = (
            (repeater_dir, bool(_repeater_files(repeater_dir, 1))),
            (infra_dir, bool(infra.layer_paths(infra_dir, 1))),
        )
        keep = [d for d, here in present if here]
        drop = [d for d, here in present if not here] + [plan.directory]
    else:
        keep = [plan.directory] if plan.wanted else []
        drop = [repeater_dir, infra_dir] + ([] if plan.wanted else [plan.directory])
    return keep, drop, plan


def _repeater_files(directory: Path, index: int, active: Active | None = None) -> list[Path]:
    """Every repeater layer's file *index* (1, the ``.poi``; 2, the Navit
    textfile) present in *directory*, in layer order: one layer per source
    since D-074; with *active*, only the layers of an active area (D-082)."""
    from hammunition.repeaters import known_layers, layer_area, layer_files

    paths = [
        directory / layer_files(i)[index]
        for i in known_layers(directory)
        if active is None or active.area(layer_area(i))
    ]
    return [p for p in paths if p.is_file()]


def cmd_maps_comaps(args: argparse.Namespace) -> int:
    """Prepare this operator's CoMaps, then start it.  D-069.

    What the ``comaps-offline`` launcher runs. Per user, refused as root.
    Writes ``EulaAccepted=true`` into CoMaps' own settings when no answer is
    there, so the licence dialog does not block the first start, and links
    each map ``comaps-maps`` installed into CoMaps' map directory; then
    replaces itself with CoMaps, with its writable and resource directories
    named. A settings file that is a symbolic link or not a regular file is
    refused, and CoMaps is not started. No ``--json`` form: it replaces
    itself with a GUI (D-059).
    """
    from hammunition.comaps_launch import data_dir, ensure_eula, link_maps, settings_path

    if os.geteuid() == 0:
        print(
            "error: CoMaps' settings and maps are per user; run this as yourself, not as root.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    prefix = DEFAULT_PREFIX
    program = prefix / "bin" / "CoMaps"
    if not args.configure_only and not (program.is_file() and os.access(program, os.X_OK)):
        print(
            f"error: {program} is not installed; `hammunition install comaps` builds it.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    path = settings_path()
    not_started = "Nothing was changed and CoMaps was not started"
    try:
        text, mode = _read_config_nofollow(path)
    except OSError as exc:
        print(f"error: {exc}. {not_started}.", file=sys.stderr)
        return EXIT_FAILED
    updated = ensure_eula(text)
    if updated != text:
        print(
            f"recording in {path} that CoMaps' licence and copyright notice is accepted, "
            f"so its first-start dialog does not block the window (the notice is "
            f"{prefix / 'share' / 'comaps' / 'data' / 'copyright.html'})",
            file=sys.stderr,
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _replace_atomically(path, updated, mode)
        except OSError as exc:
            print(
                f"error: cannot write {path}: {exc.strerror or exc}. CoMaps was not started.",
                file=sys.stderr,
            )
            return EXIT_FAILED
    writable = data_dir()
    try:
        notes = link_maps(data_root(prefix) / "comaps-maps", writable)
    except OSError as exc:
        print(
            f"error: cannot link the maps into {writable}: {exc.strerror or exc}. "
            f"CoMaps was not started.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    for note in notes:
        print(note, file=sys.stderr)
    if args.configure_only:
        return EXIT_OK
    env = {
        **os.environ,
        "MWM_WRITABLE_DIR": str(writable),
        "MWM_RESOURCES_DIR": str(prefix / "share" / "comaps" / "data"),
    }
    sys.stdout.flush()
    sys.stderr.flush()  # execve discards whatever Python still buffers
    try:
        # Semgrep: the program is the CoMaps binary this engine installed; the env only adds two paths.
        # nosemgrep: python.lang.security.audit.dangerous-os-exec-tainted-env-args.dangerous-os-exec-tainted-env-args
        os.execve(str(program), ["CoMaps"], env)
    except OSError as exc:
        print(f"error: cannot start {program}: {exc.strerror or exc}.", file=sys.stderr)
    return EXIT_FAILED


def _installed_brouter(prefix: Path) -> BRouterSetup | None:
    """Hammunition's BRouter when its tree holds one jar and at least one
    routing file is built (D-063); None otherwise, and QMapShack's BRouter
    setup is then not touched."""
    from hammunition.backends.brouter import RD5, find_jar
    from hammunition.backends.source import tree_destination
    from hammunition.qmapshack_config import BRouterSetup

    tree = tree_destination(prefix, "brouter")
    jar = find_jar(tree)
    segments = data_root(prefix) / "brouter-segments"
    try:
        built = any(segments.glob(f"*{RD5}"))
    except OSError:
        built = False
    if jar is None or not built:
        return None
    return BRouterSetup(tree=tree, jar=jar.name, segments=segments, java=shutil.which("java"))


def cmd_maps_qmapshack(args: argparse.Namespace) -> int:
    """Name Hammunition's maps in QMapShack's own configuration, then start it.  D-061.

    What the ``qmapshack-offline`` launcher runs. Per-user: refused under
    root, whose configuration is not the operator's. Additive: a key is
    added or extended only with our directories, existing values stay where
    they are, and nothing else in the file changes, except that an absent or
    negative ``[Route] routino\\database`` becomes 0 so the Routing dock
    selects the database it loaded (bench, 2026-09-29), and the operator's
    repeater directory is kept in ``[Canvas] poiPaths`` exactly while it holds
    a ``.poi`` (D-064); a file it cannot read,
    a symbolic link or anything but a regular file in its place is refused
    and left untouched, and QMapShack is then not started. No ``--json``
    form: it replaces itself with a GUI (D-059).
    """
    from hammunition.qmapshack_config import (
        QmsConfigError,
        config_path,
        ensure_paths,
        register_brouter,
        select_database,
        superseded,
        wanted,
    )

    if os.geteuid() == 0:
        print(
            "error: QMapShack's configuration is per user; run this as yourself, not as root.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    path = config_path()
    not_started = "Nothing was changed and QMapShack was not started"
    try:
        text, mode = _read_config_nofollow(path)
    except OSError as exc:
        print(f"error: {exc}. {not_started}.", file=sys.stderr)
        return EXIT_FAILED
    try:
        data = data_root(DEFAULT_PREFIX)
        with_paths = ensure_paths(text, wanted(data), remove=superseded(data))
        with_poi, _ = _repeater_poi_paths(with_paths)
        selected = select_database(with_poi)
        brouter = _installed_brouter(DEFAULT_PREFIX)
        updated, brouter_notes = (
            register_brouter(selected, brouter) if brouter is not None else (selected, [])
        )
    except QmsConfigError as exc:
        print(
            f"error: {path}: {exc}. {not_started}; "
            f"add the directories in QMapShack's own setup, or move the file aside.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if with_paths != text:
        moved = ensure_paths(text, (), remove=superseded(data)) != text
        print(
            f"adding Hammunition's map, elevation and routing directories to {path} "
            f"(existing entries kept"
            + (
                "; ours moved from [General], where QMapShack does not read them, to [Canvas])"
                if moved
                else ")"
            ),
            file=sys.stderr,
        )
    if with_poi != with_paths:
        print(
            f"keeping your overlay POI collections (repeaters, infrastructure) in "
            f"[Canvas] poiPaths in {path} exactly while they exist (D-064, D-075)",
            file=sys.stderr,
        )
    if selected != with_poi:
        print(
            f"selecting the first routing database in {path}, so the Routing dock's "
            f"Database list is not left blank",
            file=sys.stderr,
        )
    for note in brouter_notes:
        print(f"{note} ({path})", file=sys.stderr)
    if updated != text:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _replace_atomically(path, updated, mode)
        except OSError as exc:
            print(
                f"error: cannot write {path}: {exc.strerror or exc}. QMapShack was not started.",
                file=sys.stderr,
            )
            return EXIT_FAILED
    if args.configure_only:
        return EXIT_OK
    sys.stdout.flush()
    sys.stderr.flush()  # execvp discards whatever Python still buffers
    try:
        os.execvp("qmapshack", ["qmapshack"])
    except OSError as exc:
        print(
            f"error: cannot start qmapshack: {exc.strerror or exc}. "
            f"`hammunition install qmapshack` installs it.",
            file=sys.stderr,
        )
    return EXIT_FAILED


#: The program the GPS tether became: its own project, installed by the
#: `gps-tether` catalog unit (D-071 note, 2026-10-02).
TETHER_PROGRAM = "hammunition-gps-tether"

#: Where that unit installs the project's source tree (a `binary` tarball with
#: `install_tree`, at `<prefix>/share/hammunition/<unit>`); a variable so a test
#: can point it at a scratch tree.
TETHER_TREE = Path("/usr/local/share/hammunition/gps-tether")


def installed_tether() -> tuple[list[str], str] | None:
    """The installed GPS tether as ``(argv, where)``, or None.

    The catalog unit's tree first: run in place with the archive's python3, as
    the user service does. Failing that a ``hammunition-gps-tether`` on the PATH
    or in the operator's ``~/.local/bin`` (a menu entry Plasma starts has no
    ``~/.local/bin`` on its PATH, issue #145), which is how a ``pip install
    --user`` of the project would leave it.
    """
    if (TETHER_TREE / "src" / "hammunition_gps_tether" / "__main__.py").is_file():
        argv = [
            "/usr/bin/env",
            f"PYTHONPATH={TETHER_TREE / 'src'}",
            "/usr/bin/python3",
            "-P",  # no working directory on sys.path (Python 3.11+, which the tether needs)
            "-m",
            "hammunition_gps_tether",
        ]
        return argv, str(TETHER_TREE)
    found = shutil.which(TETHER_PROGRAM) or shutil.which(
        TETHER_PROGRAM, path=str(user_bin_dir(None))
    )
    return None if found is None else ([found], found)


def _tether_call_through(installed: tuple[list[str], str], args: argparse.Namespace) -> int:
    """Run the installed tether in this process's place, with the options given."""
    if os.geteuid() == 0:
        print(
            "error: the GPS tether reads gpsd as any user can; run it as yourself, not as root.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    prefix, where = installed
    argv = list(prefix)
    for flag, value in (
        ("--gpsd", args.gpsd),
        ("--port", args.port),
        ("--position-port", args.position_port),
        ("--nmea-socket", args.nmea_socket),
    ):
        if value is not None:
            argv += [flag, value]
    if args.no_nmea_socket:
        argv.append("--no-nmea-socket")
    print(
        f"hammunition: running the installed tether from {where} (hammunition-gps-tether).",
        file=sys.stderr,
        flush=True,
    )
    try:
        os.execv(argv[0], argv)  # replaces this process; returns only by raising
    except OSError as exc:
        print(f"error: cannot run {argv[0]}: {exc.strerror or exc}.", file=sys.stderr)
    return EXIT_FAILED


def cmd_maps_splat(args: argparse.Namespace) -> int:
    """Point SPLAT! at Hammunition's terrain through ``~/.splat_path``, and
    print Signal-Server's ``-sdf`` argument.  D-061, amended 2026-10-02.

    Per user, refused under root, as ``maps qmapshack`` is. The file is
    written only when absent; one naming another directory is left alone
    with the ``-d`` to pass instead; a symbolic link or anything but a
    regular file there is refused and nothing changes.
    """
    from hammunition.splat_path import SplatPathError, ensure_splat_path, splat_path_file

    if os.geteuid() == 0:
        print(
            "error: ~/.splat_path is per user; run this as yourself, not as root.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    directory = data_root(DEFAULT_PREFIX) / "splat-sdf"
    path = splat_path_file()
    try:
        state = ensure_splat_path(path, directory)
    except (SplatPathError, OSError) as exc:
        print(f"error: {exc}. Nothing was changed.", file=sys.stderr)
        return EXIT_FAILED
    if state == "written":
        print(f"wrote {path}: SPLAT! now finds its terrain in {directory}/")
    elif state == "already":
        print(f"{path} already names {directory}/")
    else:
        line = (path.read_text().splitlines() or [""])[0].strip()
        print(
            f"{path} names {line}, which is yours and is left alone; pass "
            f"-d {directory}/ to splat or splat-hd to read Hammunition's terrain"
        )
    print(f"Signal-Server: signalserver -sdf {directory}/ ... (signalserverHD for 30 m)")
    if not any(directory.glob("*.sdf.bz2")):
        print(
            "note: no SPLAT terrain is installed yet; `hammunition install splat-sdf` makes "
            "it from your map regions' elevation",
            file=sys.stderr,
        )
    return EXIT_OK


def cmd_maps_gps_tether(args: argparse.Namespace) -> int:
    """Run the installed GPS tether in this process's place.  D-061, D-071 note.

    The tether is its own project, hammunition-gps-tether, installed by
    ``hammunition install gps-tether``; the engine carries no copy. This verb
    passes its options to the installed program and replaces itself with it.
    Absent, it refuses and names the install command. Never as root. No
    ``--json`` form: it is a server, not a document (D-059).
    """
    installed = installed_tether()
    if installed is None:
        print(
            "error: the GPS tether is not installed. It is its own project now "
            "(hammunition-gps-tether): `hammunition install gps-tether` installs it.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    return _tether_call_through(installed, args)


@envelope.json_capable()
def cmd_maps_phone(args: argparse.Namespace) -> int:
    """Gather the phone files into one folder with a SHA256SUMS, and print
    the ways to carry them to a phone.  D-067.

    Copies each installed Mapsforge map and POI file, and each Garmin map,
    into ``$XDG_DATA_HOME/hammunition/phone/`` (:mod:`hammunition.phone`).
    Transfers nothing and serves nothing: every route it prints is a command
    for the operator. Per user, refused as root.
    """
    from hammunition import phone
    from hammunition.interface.phone import phone_document

    if os.geteuid() == 0:
        print(
            "error: the phone folder is per user; run this as yourself, not as root.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    data = data_root(DEFAULT_PREFIX)
    directory = phone.phone_dir()
    found = phone.installed(data)
    missing = phone.missing_units(data)
    if not found:
        message = (
            f"No phone files are installed under {data}. `hammunition install phone-maps` "
            f"builds Mapsforge maps and POI files from your map regions; `hammunition "
            f"install navigation` builds Garmin maps. Nothing was copied."
        )
        if envelope.wanted(args):
            print(message, file=sys.stderr)
            envelope.emit(phone_document(None, (), directory=str(directory), missing=missing))
            return EXIT_OK
        print(message)
        return EXIT_OK
    try:
        result = phone.stage(found, directory)
    except (phone.PhoneError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    ways = phone.routes(directory)
    if envelope.wanted(args):
        envelope.emit(phone_document(result, ways, directory=str(directory), missing=missing))
        return EXIT_OK
    for line in phone.render(result, ways):
        print(line)
    return EXIT_OK


def _generated_navit_config() -> Path:
    """The configuration ``osm-navit`` writes under the prefix (D-057)."""
    return data_root(DEFAULT_PREFIX) / "osm-navit" / "navit.xml"


def _qmapshack_poi_path(directory: Path, *, present: bool) -> RegistrationView:
    """``[Canvas] poiPaths`` in QMapShack's file holding the overlay POI
    directories that hold a ``.poi`` and none that does not; nothing else
    changed.  D-064. *directory* is the one the caller changed and *present*
    what it expects of it; the disk decides, as it does for ``maps qmapshack``.

    With areas active (D-082) it holds ``overlays/active-poi`` instead, in step
    with them, and neither layer directory.

    The same editor and the same refusals as ``maps qmapshack``: a symbolic
    link, anything but a regular file, or a line it cannot read is named and
    left untouched."""
    from hammunition.interface.repeaters import RegistrationView
    from hammunition.qmapshack_config import QmsConfigError, Wanted, config_path, ensure_paths

    path = config_path()
    # Both overlay directories follow the disk, as `maps qmapshack` keeps them.
    keep, drop, _ = _poi_wants()
    here = bool(keep)
    target = keep[0] if keep else directory
    wants = [Wanted("Canvas", "poiPaths", tuple(map(str, keep)))] if keep else []
    gone = [Wanted("Canvas", "poiPaths", tuple(map(str, drop)))] if drop else []
    try:
        text, mode = _read_config_nofollow(path)
        updated = ensure_paths(text, wants, remove=gone)
    except (OSError, QmsConfigError) as exc:
        return RegistrationView(
            program="qmapshack",
            config=str(path),
            outcome="refused",
            detail=f"{path}: {exc}; add {target} under POI paths in QMapShack's setup",
        )
    if updated == text:
        if here:
            detail = f"{target} already in [Canvas] poiPaths in {path}"
            return RegistrationView("qmapshack", str(path), "already there", detail)
        return RegistrationView(
            "qmapshack", str(path), "not there", f"nothing to take out of {path}"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace_atomically(path, updated, mode)
    except OSError as exc:
        return RegistrationView(
            "qmapshack", str(path), "refused", f"cannot write {path}: {exc.strerror or exc}"
        )
    if here:
        detail = f"added {target} to [Canvas] poiPaths in {path}"
        return RegistrationView("qmapshack", str(path), "added", detail)
    detail = f"took {directory} out of [Canvas] poiPaths in {path}"
    return RegistrationView("qmapshack", str(path), "removed", detail)


def _overlay_navit_maps(active: Active | None = None) -> list[Path]:
    """Every overlay layer's Navit textfile, repeaters first (D-064, D-074),
    then infrastructure (D-075): the operator's one Navit copy carries all of
    the active areas' (D-082) and every layer that belongs to no area."""
    from hammunition import infra
    from hammunition.repeaters import overlay_dir

    if active is None:
        active = _active_station()[0]
    directory = infra.overlay_dir()
    found = _repeater_files(overlay_dir(), 2, active)
    universe = _active_station()[1]
    for layer_id in infra.known_layers(directory):
        path = directory / infra.layer_files(layer_id)[2]
        if path.is_file() and active.area(infra.layer_area(layer_id), universe):
            found.append(path)
    return found


def _register_repeaters(directory: Path) -> tuple[RegistrationView, RegistrationView]:
    """QMapShack and Navit told about every repeater layer left in
    *directory*: the directory in ``poiPaths`` while any layer is there, and
    the operator's Navit copy rewritten from every overlay layer.  D-064,
    D-074, D-075."""
    return _register_overlays(directory, present=bool(_repeater_files(directory, 1)))


def _register_overlays(
    directory: Path, *, present: bool
) -> tuple[RegistrationView, RegistrationView]:
    """*directory* in QMapShack's ``poiPaths`` exactly while *present*, and
    the operator's Navit copy carrying every overlay layer's textfile (the
    repeaters' and the infrastructure's), or deleted when there is none."""
    from hammunition.interface.repeaters import RegistrationView
    from hammunition.repeaters import overlays_root

    user = overlays_root() / "navit.xml"
    active, universe = _active_station()
    maps = _overlay_navit_maps(active)
    keep = active.region_slugs(universe)
    qmapshack = _qmapshack_poi_path(directory, present=present)
    if maps or keep is not None:
        return qmapshack, _navit_user_config(maps, user, _generated_navit_config(), keep)
    try:
        user.unlink()
        navit = RegistrationView("navit", str(user), "removed", f"deleted {user}")
    except FileNotFoundError:
        navit = RegistrationView("navit", str(user), "not there", f"no {user} to delete")
    except OSError as exc:
        navit = RegistrationView("navit", str(user), "refused", f"{user}: {exc.strerror or exc}")
    return qmapshack, navit


def _navit_user_config(
    overlays: Sequence[Path],
    user: Path,
    generated: Path,
    keep_regions: frozenset[str] | None = None,
) -> RegistrationView:
    """The operator's copy of *generated* with each of *overlays* in its
    mapset, at *user*, mode 0600.  D-064; one map per layer since D-074.
    *keep_regions*, the active regions' file slugs (D-082), leaves the other
    converted maps out of the copy; None keeps them all."""
    from hammunition.interface.repeaters import RegistrationView

    if not generated.is_file():
        return RegistrationView(
            "navit",
            str(user),
            "not written",
            f"no generated configuration at {generated} yet; `hammunition install osm-navit` "
            f"writes it, and navit-offline adds the layer at its next start",
        )
    try:
        body = navit_config.add_maps(generated.read_text(encoding="utf-8"), list(overlays))
        body = navit_config.select_regions(body, keep_regions)
        _read_config_nofollow(user)  # refuses a link or a non-file in its place
        user.parent.mkdir(parents=True, exist_ok=True)
        _replace_atomically(user, body, 0o600)
    except (OSError, navit_config.NavitConfigError) as exc:
        return RegistrationView("navit", str(user), "refused", f"{user}: {exc}")
    return RegistrationView(
        "navit", str(user), "written", f"wrote {user}; navit-offline opens it from now on"
    )


def _refuse_root(what: str) -> bool:
    if os.geteuid() == 0:
        print(
            f"error: {what} are per user; run this as yourself, not as root.",
            file=sys.stderr,
        )
        return True
    return False


def _write_repeater_layer(
    parsed: Sequence[ParsedInput],
    layer_title: str,
    day: date,
    licences: Sequence[str],
    args: argparse.Namespace,
    layer_id: str = "export",
) -> int:
    """Merge *parsed*, write the layer *layer_id*, rebuild the all-sources
    file, register every layer, print or emit."""
    from hammunition.interface.repeaters import (
        InputView,
        RepeatersDocument,
        SkipView,
        render_repeaters,
    )
    from hammunition.repeaters import Layer, merge, overlay_dir, write_layer

    rows, merged = merge(r for p in parsed for r in p.rows)
    if not rows:
        print(
            "error: no repeater with a position and a callsign or frequency was read. "
            "Nothing was written.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    directory = overlay_dir()
    description = " ".join(licences)
    try:
        written = write_layer(directory, Layer(layer_title, description, day, rows), layer_id)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: cannot write the layer in {directory}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    # The layer is in place from here on: a failed rebuild is reported in
    # the all-sources view and the exit status, and registration still runs.
    all_sources, _ = _rebuild_all_sources(directory)
    registered = _register_repeaters(directory)
    doc = RepeatersDocument(
        layer_id=layer_id,
        layer=layer_title,
        exported=day.isoformat(),
        licences=tuple(licences),
        inputs=tuple(
            InputView(
                path=str(p.path),
                format=p.format,
                read=p.read,
                used=len(p.rows),
                skipped=tuple(SkipView(s.reason, s.count, s.first) for s in p.skipped),
                sha256=p.sha256,
            )
            for p in parsed
        ),
        read=sum(p.read for p in parsed),
        skipped=sum(p.read - len(p.rows) for p in parsed),
        merged=merged,
        written=len(rows),
        directory=str(directory),
        files=tuple(str(p) for p in written),
        registered=registered,
        all_sources=all_sources,
    )
    refused = any(r.outcome == "refused" for r in registered) or all_sources.error is not None
    code = EXIT_FAILED if refused else EXIT_OK
    if envelope.wanted(args):
        envelope.emit(doc)
        return code
    for line in render_repeaters(doc):
        print(line)
    return code


def _rebuild_all_sources(directory: Path) -> tuple[AllSourcesView, Path | None]:
    """``repeaters-all.gpx`` rebuilt from the directory layers, as a view,
    and the file when this rebuild deleted it.  D-074. Never raises: a
    rebuild that cannot write says why in the view's ``error``."""
    from hammunition.interface.repeaters import AllSourcesView, LayerSkipView
    from hammunition.repeaters import rebuild_all

    try:
        result = rebuild_all(directory)
    except OSError as exc:
        return AllSourcesView(None, "", (), 0, 0, (), error=f"not rebuilt: {exc}"), None
    view = AllSourcesView(
        file=None if result.path is None else str(result.path),
        name=result.name,
        layers=result.layers if result.path is not None else (),
        written=result.written,
        merged=result.merged,
        skipped=tuple(LayerSkipView(layer, reason) for layer, reason in result.skipped),
        error=None,
    )
    return view, result.removed


@envelope.json_capable()
def cmd_maps_repeaters_import(args: argparse.Namespace) -> int:
    """Convert the operator's own repeater export into overlays.  D-064.

    Fully offline. Reads a RepeaterBook GPX or CSV export (a CSV only with
    Lat and Long), hearham's JSON as served, or a hand-typed CSV; refuses
    CHIRP files, a CSV without positions and KML by name, and then writes
    nothing. Merges on callsign, output frequency and position to 0.01°,
    writes the GPX, POI and Navit files into the operator's overlay
    directory, adds it to QMapShack's ``poiPaths`` and writes the operator's
    Navit configuration. Refused as root: the files are the operator's.

    D-074 adds four inputs, each its own layer and each exclusive of the
    files and of the others: ``--from-open-repeater [FILE]`` (the installed
    ``open-repeater`` data unit's file by default), ``--from-acma [FILE]``
    (the installed ``acma-register`` unit's zip by default, filtered to the
    installed regions' bounding boxes; amended 2026-10-01), ``--from-osm``
    (the region extracts installed here, filtered by osmium) and
    ``--from-direwolf-log FILE...``.
    """
    from hammunition.repeaters import (
        HEARHAM,
        RepeaterInputError,
        export_date,
        hearham_licence,
        layer_name,
        licence_text,
        parse_exported,
        read_inputs,
    )

    if _refuse_root("repeater overlays"):
        return EXIT_FAILED
    routes = [
        name
        for name, given in (
            ("FILE...", bool(args.files)),
            ("--from-open-repeater", args.from_open_repeater is not None),
            ("--from-acma", args.from_acma is not None),
            ("--from-osm", args.from_osm),
            ("--from-direwolf-log", bool(args.from_direwolf_log)),
        )
        if given
    ]
    if len(routes) != 1:
        print(
            "error: give one source per import: your export files, --from-open-repeater, "
            "--from-acma, --from-osm or --from-direwolf-log; each is its own layer (D-074)"
            + (f", not {' and '.join(routes)}" if routes else "")
            + ". Nothing was written.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if args.exported and not args.files:
        print(
            f"error: --exported dates your own export; {routes[0]} is dated by its own "
            f"data. Nothing was written.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if not args.files:
        return _import_repeater_source(args)
    try:
        override = parse_exported(args.exported) if args.exported else None
    except ValueError as exc:
        print(f"error: {exc}. Nothing was written.", file=sys.stderr)
        return EXIT_FAILED
    paths = [Path(p) for p in args.files]
    try:
        parsed = read_inputs(paths)
    except RepeaterInputError as exc:
        print(f"error: {exc}\nNothing was written.", file=sys.stderr)
        return EXIT_FAILED
    licences: list[str] = []
    for item in parsed:
        text = (
            hearham_licence(f"Read from {item.path}", item.sha256)
            if item.format == HEARHAM
            else licence_text(item.format)
        )
        if text not in licences:
            licences.append(text)
    day = export_date(paths, override)
    return _write_repeater_layer(parsed, layer_name(day), day, licences, args)


#: Where the ``open-repeater`` data unit installs its file (D-049, D-074).
OPEN_REPEATER_FILE = Path("open-repeater") / "open-repeater.json"
#: Where the ``acma-register`` unit installs the register (D-074, amended 2026-10-01).
ACMA_FILE = Path("acma-register") / "spectra_rrl.zip"


def _region_boxes() -> tuple[list[tuple[float, float, float, float]], int]:
    """The bounding box of every installed region extract, from its header,
    and how many extracts there are. Every message names an extract by its
    number, never its file: a region says where the operator is (D-057)."""
    from hammunition import osm_pbf
    from hammunition import repeater_sources as rs
    from hammunition.repeaters import RepeaterInputError

    extracts = rs.installed_extracts(DEFAULT_PREFIX)
    if not extracts:
        raise RepeaterInputError(
            f"no map region is installed in {data_root(DEFAULT_PREFIX) / 'osm-regions'}: the "
            f"ACMA layer keeps the repeaters inside your regions' bounding boxes, so "
            f"`hammunition install osm-regions` comes first"
        )
    boxes: list[tuple[float, float, float, float]] = []
    for number, (pbf, _) in enumerate(extracts, start=1):
        label = f"region extract {number} of {len(extracts)}"
        try:
            box = osm_pbf.header_bbox(pbf)
        except (osm_pbf.OsmPbfError, OSError):
            raise RepeaterInputError(
                f"{label}: its header cannot be read for a bounding box; reinstall it with "
                f"`hammunition install osm-regions`"
            ) from None
        if box is None:
            raise RepeaterInputError(
                f"{label} carries no bounding box in its header, so the repeaters in it "
                f"cannot be chosen"
            )
        boxes.append(box)
    return boxes, len(extracts)


def _import_repeater_source(args: argparse.Namespace) -> int:
    """``import --from-open-repeater``, ``--from-acma``, ``--from-osm`` or
    ``--from-direwolf-log``: one D-074 source into its own layer."""
    from hammunition import repeater_sources as rs
    from hammunition.repeaters import (
        OSM,
        ParsedInput,
        Repeater,
        RepeaterInputError,
        Skip,
        overlay_dir,
    )

    try:
        if args.from_open_repeater is not None:
            if args.from_open_repeater:
                path = Path(args.from_open_repeater)
            else:
                path = data_root(DEFAULT_PREFIX) / OPEN_REPEATER_FILE
                if not path.is_file():
                    raise RepeaterInputError(
                        f"no Open Repeater file at {path}: `hammunition install open-repeater` "
                        f"installs it (241 kB, CC0), or give a file you downloaded: "
                        f"--from-open-repeater FILE"
                    )
            parsed = rs.read_open_repeater(path)
            day = rs.open_repeater_date(path)
            return _write_repeater_layer(
                [parsed],
                rs.open_repeater_layer_name(day),
                day,
                [rs.open_repeater_licence()],
                args,
                "open-repeater",
            )
        if args.from_acma is not None:
            if args.from_acma:
                path = Path(args.from_acma)
            else:
                path = data_root(DEFAULT_PREFIX) / ACMA_FILE
                if not path.is_file():
                    raise RepeaterInputError(
                        f"no ACMA register at {path}: `hammunition install acma-register` "
                        f"installs it (about 67.5 MB, unverified: the ACMA publishes no "
                        f"checksum), or give a copy you downloaded: --from-acma FILE"
                    )
            boxes, regions = _region_boxes()
            parsed = rs.read_acma(path, boxes)
            if not parsed.rows:
                raise RepeaterInputError(
                    f"none of the register's {parsed.read} amateur repeater transmitters is "
                    f"inside the bounding boxes of your {regions} installed map region(s), so "
                    f"the ACMA layer would be empty. The register covers Australia only "
                    f"(Geofabrik's australia-oceania/australia and its states)"
                )
            day = rs.acma_date(path)
            return _write_repeater_layer(
                [parsed], rs.acma_layer_name(day), day, [rs.acma_licence(day)], args, "acma"
            )
        if args.from_osm:
            extracts = rs.installed_extracts(DEFAULT_PREFIX)
            folder = data_root(DEFAULT_PREFIX) / "osm-regions"
            if not extracts:
                raise RepeaterInputError(
                    f"no map region is installed in {folder}: `hammunition install osm-regions` "
                    f"fetches your regions, and this filters the repeaters out of them"
                )
            rows: list[Repeater] = []
            skips: dict[str, int] = {}
            read = 0
            with tempfile.TemporaryDirectory(prefix="hammunition-osm-repeaters-") as scratch:
                for number, (pbf, _) in enumerate(extracts, start=1):
                    label = f"region extract {number} of {len(extracts)}"
                    one = rs.filter_extract(pbf, Path(scratch), label=label)
                    rows += one.rows
                    for skip in one.skipped:
                        skips[skip.reason] = skips.get(skip.reason, 0) + skip.count
                    read += one.read
            # One input for the whole directory: a region's name, or its
            # digest, says where the operator is (D-057), and this document
            # is meant to be pasteable (D-064).
            parsed = ParsedInput(
                path=folder,
                format=OSM,
                read=read,
                rows=tuple(rows),
                skipped=tuple(Skip(reason, count, ()) for reason, count in skips.items()),
                sha256="",
                mtime=0.0,
            )
            day = rs.extracts_date(extracts)
            return _write_repeater_layer(
                [parsed], rs.osm_layer_name(day), day, [rs.osm_licence()], args, "osm"
            )
        paths = [Path(p) for p in args.from_direwolf_log]
        parsed = rs.read_direwolf_logs(paths)
        heard = rs.direwolf_date(parsed.rows)
        day = heard or date.fromtimestamp(parsed.mtime)
        return _write_repeater_layer(
            [parsed],
            rs.direwolf_layer_name(day),
            day,
            [rs.direwolf_licence()],
            args,
            "aprs-heard",
        )
    except RepeaterInputError as exc:
        print(f"error: {exc}\nNothing was written in {overlay_dir()}.", file=sys.stderr)
        return EXIT_FAILED


def _snapshot_mirror(args: argparse.Namespace) -> str | None:
    """The station's LAN mirror for an on-request repeater fetch (D-078), or
    ``None`` with ``--no-mirror``, no mirror set, or a station file that
    cannot be read (said once; the publisher is then asked)."""
    if getattr(args, "no_mirror", False):
        return None
    try:
        return load_station(owner=operator(args)).mirror
    except StationError as exc:
        print(f"note: the station file could not be read ({exc}); not asking a mirror.")
        return None


def _snapshot_source_lines(read: SnapshotRead, mirror: str | None) -> str:
    """What the operator is told about where the bytes came from, and the
    ``when`` text the layer's licence records.  D-078."""
    stamp = read.when.isoformat()
    if read.source == "mirror":
        print(
            f"Read from your LAN mirror, {read.where}: a snapshot someone fetched earlier, "
            f"its own date unknown. Still unverified; its sha256 is only what arrived.",
            flush=True,
        )
        return (
            f"Read from the LAN mirror {read.where} at {stamp} (a snapshot fetched earlier, "
            f"date unknown)"
        )
    if mirror:
        why = read.mirror_failure or "it did not have it"
        print(f"The LAN mirror did not supply it ({why}); asking the publisher.", flush=True)
    return f"Fetched {stamp}"


def _fetch_repeater_list(
    args: argparse.Namespace,
    *,
    snapshot: str,
    disclosure: str,
    parse: Callable[[bytes, str], ParsedInput],
    licence: Callable[[str, str], str],
    name: Callable[[date], str],
    layer_id: str,
) -> int:
    """A list fetched on request through D-064's fetch (bounded, HTTPS-only
    redirects), parsed from memory, written as its own layer. The station's
    LAN mirror is asked first, at ``<mirror>/repeater-snapshots/<snapshot>``,
    unless ``--no-mirror`` (D-078).  D-074."""
    from hammunition import repeater_sources as rs
    from hammunition import repeaters

    if _refuse_root("repeater overlays"):
        return EXIT_FAILED
    (snap,) = (x for x in rs.snapshots() if x.name == snapshot)
    mirror = _snapshot_mirror(args)
    print(disclosure, flush=True)
    if mirror:
        print(
            f"Your station names a LAN mirror ({mirror}): it is asked first, for "
            f"{rs.SNAPSHOT_UNIT}/{snap.name}, and the publisher only if it does not have it.",
            flush=True,
        )
    try:
        read = rs.read_snapshot(snap, parse, mirror=mirror)
    except (repeaters.RepeaterFetchError, repeaters.RepeaterInputError) as exc:
        print(f"error: {exc}. Nothing was written.", file=sys.stderr)
        return EXIT_FAILED
    parsed = dataclasses.replace(read.parsed, sha256=read.sha256)
    when_text = _snapshot_source_lines(read, mirror)
    day = read.when.date()
    text = licence(when_text, read.sha256)
    return _write_repeater_layer([parsed], name(day), day, [text], args, layer_id)


def cmd_maps_repeaters_fetch_etcc(args: argparse.Namespace) -> int:
    """Fetch the RSGB ETCC's UK repeater list, on request.  D-074.

    No licence is stated for it (D-033's position); the observed sha256 is
    recorded and the layer marked unverified. No ``--json`` form, like
    ``fetch-hearham``: the disclosure is for a person to read first."""
    from hammunition import repeater_sources as rs

    return _fetch_repeater_list(
        args,
        snapshot="etcc.csv",
        disclosure=(
            f"This fetches the RSGB ETCC's UK repeater list from {rs.ETCC_URL} (about 62 kB), "
            f"now and only now, and converts it on this machine. ukrepeater.net states no "
            f"licence and publishes no checksum, so what arrives is recorded by its sha256 "
            f"and marked unverified. Its positions are at Maidenhead-locator precision."
        ),
        parse=rs.parse_etcc,
        licence=rs.etcc_licence,
        name=rs.etcc_layer_name,
        layer_id="etcc",
    )


def cmd_maps_repeaters_fetch_brandmeister(args: argparse.Namespace) -> int:
    """Fetch Brandmeister's device list, on request, repeaters only.  D-074.

    Most of the list is hotspots, which are personal locations: only a
    6-digit id whose transmit and receive frequencies differ is kept, the
    rest dropped from memory before anything is written. No terms are
    published (D-033); the layer is marked unverified. No ``--json`` form."""
    from hammunition import repeater_sources as rs

    return _fetch_repeater_list(
        args,
        snapshot="brandmeister.json",
        disclosure=(
            f"This fetches Brandmeister's whole DMR device list from {rs.BRANDMEISTER_URL} "
            f"(about 9.5 MB), now and only now, and converts it on this machine. Most entries "
            f"are hotspots, which are personal locations: they are dropped before anything is "
            f"written, and only repeaters (a 6-digit id whose transmit and receive "
            f"frequencies differ) are kept. Brandmeister publishes no terms and no checksum, "
            f"so what arrives is recorded by its sha256 and marked unverified."
        ),
        parse=rs.parse_brandmeister,
        licence=rs.brandmeister_licence,
        name=rs.brandmeister_layer_name,
        layer_id="brandmeister",
    )


def cmd_maps_repeaters_fetch_repeaterbook(args: argparse.Namespace) -> int:
    """Fetch repeaters from RepeaterBook's API with the operator's own token.  D-081.

    Runs the ``repeaterbook-client`` unit's venv python on the engine's small
    runner, which uses the unofficial ``repeaterbook`` client (App #114). The
    token comes from ``REPEATERBOOK`` or Doppler through
    :func:`hammunition.secrets.resolve_secret` and reaches only that
    subprocess's environment: never argv, the station file, a log or a
    document. One layer per state, ``repeaterbook-<AREA>`` (#325), unverified
    and personal use; a re-fetch of a state replaces that state's layer whole,
    and ``--county`` (one state) fetches per county into it; never mirrored,
    never listed by ``artifacts``. Built against the documented format and the client's
    source, not yet run against the live API. No ``--json`` form."""
    from hammunition import repeaterbook as rb
    from hammunition import repeaters
    from hammunition.repeaters import RepeaterFetchError, RepeaterInputError, overlay_dir
    from hammunition.secrets import SecretUnavailable, resolve_secret

    if _refuse_root("repeater overlays"):
        return EXIT_FAILED
    country = args.country
    counties = list(dict.fromkeys(args.county or ()))
    try:
        areas = dict(
            (area, state_id) for state_id, area in (rb.resolve_area(country, s) for s in args.state)
        )
        for area in areas:
            repeaters.area_layer_id(area)
    except (RepeaterInputError, ValueError) as exc:
        print(f"error: {exc}. Nothing was fetched.", file=sys.stderr)
        return EXIT_FAILED
    if counties and len(areas) != 1:
        print(
            "error: --county narrows one state: give exactly one --state with it. "
            "Nothing was fetched.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    python = rb.client_python(operator(args) or None)
    if not python.is_file():
        print(
            f"error: the {rb.UNIT} unit is not installed (no {python}). It is the unofficial "
            f"third-party repeaterbook client, registered with RepeaterBook as App #114; "
            f"install it by name: `hammunition install {rb.UNIT}`. Nothing was fetched.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        station = load_station(owner=operator(args))
    except StationError as exc:
        print(f"note: the station file could not be read ({exc}); using the environment only.")
        station = None
    try:
        token = resolve_secret(rb.TOKEN_ENV, env=os.environ, station=station)
    except SecretUnavailable as exc:
        print(
            f"error: {exc}\nGenerate a token for App #114 (RepeaterBook Python Client) on your "
            f"own account at https://www.repeaterbook.com/user/api_apps.php. Nothing was "
            f"fetched.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(rb.terms(), flush=True)
    requests = len(areas) * max(1, len(counties))
    print(
        f"This fetches {len(areas)} state(s) of {country} through the {rb.UNIT} unit's client, "
        f"{requests} request(s) in all, now and only now; each state is its own layer. Your "
        f"token ({rb.TOKEN_ENV}) goes only into that process's environment and is written "
        f"nowhere; the client's response cache is a private temporary directory removed "
        f"afterwards; the layers are never mirrored.",
        flush=True,
    )
    fetched: dict[str, list[rb.StateRead]] = {}
    first = True
    for area, state_id in areas.items():
        for county in counties or [None]:
            if not first:
                rb.pause()
            first = False
            try:
                fetched.setdefault(area, []).append(
                    rb.run_runner(python, country, state_id, token, county)
                )
            except rb.RunnerError as exc:
                print(f"error: {rb.explain(exc)}. Nothing was written.", file=sys.stderr)
                return EXIT_FAILED
            except (RepeaterFetchError, RepeaterInputError) as exc:
                print(f"error: {exc}. Nothing was written.", file=sys.stderr)
                return EXIT_FAILED
    for area, reads in fetched.items():
        for read in reads:
            if rb.maybe_cut_short(read):
                advice = (
                    "narrow it further with --county"
                    if counties
                    else f"fetch it by county: --state {area} --county NAME (repeatable)"
                )
                print(
                    f"note: RepeaterBook returned {read.parsed.read} rows for {area}, near the "
                    f"most it sends in one answer; it may have been cut short. To get the "
                    f"rest, {advice}."
                )
    directory = overlay_dir()
    if "repeaterbook" in repeaters.present_layers(directory):
        print(
            "note: the earlier merged layer `repeaterbook` is still registered; "
            "`hammunition maps repeaters remove --layer repeaterbook` once every state you "
            "want is fetched by state."
        )
    code = EXIT_OK
    for area, reads in fetched.items():
        day = max(r.when for r in reads).date()
        licences = [rb.terms()]
        for read in reads:
            licences.append(rb.provenance(f"Fetched {read.when.isoformat()}", read.sha256))
        written = _write_repeater_layer(
            [r.parsed for r in reads],
            rb.layer_name(day, area),
            day,
            licences,
            args,
            repeaters.area_layer_id(area),
        )
        code = code if code != EXIT_OK else written
    return code


def cmd_maps_repeaters_fetch_hearham(args: argparse.Namespace) -> int:
    """Fetch hearham.com's repeater list, on request, and convert it.  D-064.

    The one route here that uses the network, and only when run. The sha256
    of what arrived is recorded in the layer and printed; hearham publishes
    no digest and no dated snapshot, so it is marked unverified (D-033's
    position). No ``--json`` form: the disclosure is printed before the
    request, for a person to read."""
    from hammunition import repeater_sources as rs
    from hammunition import repeaters

    if _refuse_root("repeater overlays"):
        return EXIT_FAILED
    (snap,) = (x for x in rs.snapshots() if x.name == "hearham.json")
    url = snap.url
    mirror = _snapshot_mirror(args)
    print(
        f"This fetches hearham.com's whole repeater list from {url} (about 9.5 MB), now and "
        f"only now, and converts it on this machine. hearham publishes no checksum, so what "
        f"arrives is recorded by its sha256 and marked unverified.",
        flush=True,
    )
    if mirror:
        print(
            f"Your station names a LAN mirror ({mirror}): it is asked first, for "
            f"{rs.SNAPSHOT_UNIT}/{snap.name}, and the publisher only if it does not have it.",
            flush=True,
        )

    def parse(body: bytes, source: str) -> ParsedInput:
        with tempfile.TemporaryDirectory(prefix="hammunition-hearham-") as scratch:
            staged = Path(scratch) / "hearham.json"
            staged.write_bytes(body)
            try:
                parsed = repeaters.read_input(staged)
            except repeaters.RepeaterInputError as exc:
                raise repeaters.RepeaterInputError(f"{source}: {exc}") from None
        if parsed.format != repeaters.HEARHAM:
            raise repeaters.RepeaterInputError(
                f"{source} answered with something other than its repeater list ({parsed.format})"
            )
        return dataclasses.replace(parsed, path=Path(source))

    try:
        read = rs.read_snapshot(snap, parse, mirror=mirror)
    except (repeaters.RepeaterFetchError, repeaters.RepeaterInputError) as exc:
        print(f"error: {exc}. Nothing was written.", file=sys.stderr)
        return EXIT_FAILED
    when_text = _snapshot_source_lines(read, mirror)
    day = read.when.date()
    licence = repeaters.hearham_licence(when_text, read.sha256)
    return _write_repeater_layer(
        [read.parsed], repeaters.hearham_layer_name(day), day, [licence], args
    )


def _repeater_bands() -> tuple[tuple[str, int, int], ...]:
    from hammunition.repeaters import BANDS

    return BANDS


def _repeater_mode(text: str) -> str:
    """``--mode``'s value as a vocabulary mode, or an argparse error."""
    from hammunition.repeaters import MODES, normalise_modes

    found = normalise_modes(text)
    if len(found) != 1:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not one repeater mode; the modes are {', '.join(MODES)}"
        )
    return found[0]


@envelope.json_capable()
def cmd_maps_repeaters_list(args: argparse.Namespace) -> int:
    """The repeater layers as data for a front end.  D-074 (amended 2026-10-04), D-059.

    Read-only: reads the overlay directory and each layer's rows file, writes
    nothing, fetches nothing, never rebuilds ``repeaters-all.gpx``. The layers
    are joined in memory as that file is. A layer that cannot be read (no rows
    file, a damaged one) or an id that is not a layer goes in ``skipped`` with
    the reason and the rest is returned: **exit 0 with a partial list**, so a
    front end can draw what there is. Exit 1 only when the directory itself
    cannot be read."""
    from hammunition.interface.repeaters import (
        CentreView,
        LayerSkipView,
        LayerView,
        RepeatersListDocument,
        RowView,
        render_repeaters_list,
    )
    from hammunition.repeater_sources import cross_merge_origins
    from hammunition.repeaters import (
        HEARD_LAYERS,
        LAYERS,
        SOURCE_TRAITS,
        Repeater,
        band_of,
        bearing_deg,
        distance_km,
        is_layer_id,
        layer_area,
        layer_files,
        overlay_dir,
        parse_position,
        present_layers,
        read_layer_rows,
        source_credit,
    )

    centre: CentreView | None = None
    if args.near is not None:
        try:
            lat, lon = parse_position(args.near)
        except ValueError as exc:
            print(f"error: --near: {exc}", file=sys.stderr)
            return EXIT_FAILED
        centre = CentreView(lat=lat, lon=lon, source="argument")
    else:
        try:
            grid = load_station(owner=operator(args)).grid_square
            if grid:
                lat, lon = parse_position(grid)
                centre = CentreView(lat=lat, lon=lon, source="station")
        except (StationError, ValueError):
            centre = None  # no usable station: no distances, and not an error
    if args.within is not None and centre is None:
        print(
            "error: --within needs a position: give --near GRID|LAT,LON, or set the station's "
            "grid square (hammunition station set --grid-square ...)",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if args.within is not None and not args.within >= 0:
        print("error: --within is a distance in kilometres, zero or more", file=sys.stderr)
        return EXIT_FAILED
    wanted_bands = set(args.band or ())
    wanted_modes = set(args.mode or ())

    directory = overlay_dir()
    try:
        if directory.exists():
            list(directory.iterdir())  # an unreadable directory is an error, not "empty"
    except OSError as exc:
        print(f"error: {directory} cannot be read: {exc}", file=sys.stderr)
        return EXIT_FAILED
    here = present_layers(directory)
    asked: list[str] = list(args.layer or [])
    skipped: list[LayerSkipView] = []
    for layer_id in dict.fromkeys(asked):
        if not is_layer_id(layer_id):
            skipped.append(
                LayerSkipView(
                    layer=layer_id,
                    reason=f"no repeater layer {layer_id!r}; the layers are {', '.join(LAYERS)} "
                    f"and repeaterbook-<AREA>",
                )
            )
        elif layer_id not in here:
            skipped.append(LayerSkipView(layer=layer_id, reason="not present in the directory"))
    wanted = [i for i in here if not asked or i in asked]
    views: list[LayerView] = []
    joinable: list[tuple[str, tuple[Repeater, ...]]] = []
    credits: dict[str, None] = {}
    for layer_id in wanted:
        names = layer_files(layer_id)
        rows_path = directory / names[3]
        if not rows_path.is_file():
            skipped.append(
                LayerSkipView(
                    layer=layer_id,
                    reason="written before D-074 kept its rows as data; re-import it to include it",
                )
            )
            continue
        try:
            layer = read_layer_rows(rows_path)
        except ValueError as exc:
            skipped.append(LayerSkipView(layer=layer_id, reason=str(exc)))
            continue
        sources = tuple(dict.fromkeys(r.source for r in layer.rows))
        for row in layer.rows:
            for source in (row.source, *row.also):
                credits[source_credit(source)] = None
        views.append(
            LayerView(
                id=layer_id,
                area=layer_area(layer_id),
                active=_layer_active(layer_area(layer_id)),
                name=layer.name,
                description=layer.description,
                day=layer.day.isoformat(),
                rows=len(layer.rows),
                sources=sources,
                personal_use=any(SOURCE_TRAITS[s][0] for s in sources),
                unverified=any(SOURCE_TRAITS[s][1] for s in sources),
                files=tuple(str(directory / n) for n in names if (directory / n).is_file()),
            )
        )
        if layer_id not in HEARD_LAYERS:
            joinable.append((layer_id, layer.rows))
    merged_rows, merged, origin = cross_merge_origins(rows for _, rows in joinable)
    found: list[RowView] = []
    for r, o in zip(merged_rows, origin, strict=True):
        band = band_of(r.output_hz)
        if wanted_bands and band not in wanted_bands:
            continue
        if wanted_modes and not wanted_modes & set(r.modes):
            continue
        km = bearing = None
        if centre is not None:
            km = round(distance_km(centre.lat, centre.lon, r.lat, r.lon), 2)
            bearing = round(bearing_deg(centre.lat, centre.lon, r.lat, r.lon), 1)
            if args.within is not None and km > args.within:
                continue
        found.append(
            RowView(
                callsign=r.callsign,
                output_hz=r.output_hz,
                offset_hz=r.offset_hz,
                tone=r.tone,
                mode=r.mode_text(),
                modes=r.modes,
                band=band,
                digital=dict(r.digital),
                distance_km=km,
                bearing_deg=bearing,
                place=r.place,
                notes=r.notes,
                use=r.use,
                status=r.status,
                updated=r.updated,
                label=r.label,
                lat=r.lat,
                lon=r.lon,
                source=r.source,
                also=r.also,
                layer=joinable[o][0],
                personal_use=any(SOURCE_TRAITS[s][0] for s in (r.source, *r.also)),
            )
        )
    if centre is not None:
        found.sort(key=lambda row: row.distance_km or 0.0)
    doc = RepeatersListDocument(
        directory=str(directory),
        layers=tuple(views),
        skipped=tuple(skipped),
        rows=tuple(found),
        merged=merged,
        credits=tuple(credits),
        centre=centre,
        within_km=args.within,
    )
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    named = bool(args.near or args.within is not None or wanted_bands or wanted_modes)
    for line in render_repeaters_list(doc, rows=named):
        print(line)
    return EXIT_OK


def _repeater_layer_id(text: str) -> str:
    """``--layer``'s value for ``remove``: a fixed id or ``repeaterbook-AREA``
    (the area upper-cased), or an argparse error."""
    from hammunition.repeaters import AREA_LAYER_PREFIX, LAYERS, is_layer_id

    given = text
    if text.lower().startswith(AREA_LAYER_PREFIX):
        given = AREA_LAYER_PREFIX + text[len(AREA_LAYER_PREFIX) :].upper()
    if not is_layer_id(given):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a repeater layer; the layers are {', '.join(LAYERS)} and "
            f"repeaterbook-AREA (for example repeaterbook-OH)"
        )
    return given


@envelope.json_capable()
def cmd_maps_repeaters_remove(args: argparse.Namespace) -> int:
    """Delete repeater layers and unregister what is gone.  D-064, D-074.

    Every layer and the all-sources file by default; one layer with
    ``--layer ID``, after which the all-sources file is rebuilt from what is
    left. QMapShack's ``poiPaths`` and the operator's Navit copy follow the
    layers that remain. Idempotent: nothing to remove is exit 0. Anything
    else in the directory stays."""
    from hammunition.interface.repeaters import RepeatersRemovedDocument, render_removed
    from hammunition.repeaters import known_layers, overlay_dir, remove_layer

    if _refuse_root("repeater overlays"):
        return EXIT_FAILED
    directory = overlay_dir()
    every = known_layers(directory)
    try:
        removed = remove_layer(directory, args.layer)
    except OSError as exc:
        print(f"error: {exc}. Nothing more was removed.", file=sys.stderr)
        return EXIT_FAILED
    all_sources, dropped = _rebuild_all_sources(directory)
    if dropped is not None:
        removed = (*removed, dropped)
    registered = _register_repeaters(directory)
    doc = RepeatersRemovedDocument(
        directory=str(directory),
        layers=(args.layer,) if args.layer else every,
        removed=tuple(str(p) for p in removed),
        unregistered=registered,
        all_sources=all_sources,
    )
    refused = any(r.outcome == "refused" for r in registered) or all_sources.error is not None
    code = EXIT_FAILED if refused else EXIT_OK
    if envelope.wanted(args):
        envelope.emit(doc)
        return code
    for line in render_removed(doc):
        print(line)
    return code


# --- maps infra (D-075) -------------------------------------------------------------


def _write_infra(gathered: Gathered, args: argparse.Namespace) -> int:
    """Write each gathered layer (a layer with no point is removed instead),
    register every overlay layer, print or emit.  D-075."""
    from hammunition import infra
    from hammunition.interface.infra import (
        InfraDocument,
        InfraInputView,
        InfraLayerView,
        render_infra,
    )
    from hammunition.interface.repeaters import SkipView

    directory = infra.overlay_dir()
    if not any(layer.points for layer in gathered.layers):
        print(
            f"error: no point of any layer asked for was found, so the layers in "
            f"{directory} were left as they are. Nothing was changed.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    views: list[InfraLayerView] = []
    try:
        for layer in gathered.layers:
            if layer.points:
                files = infra.write_layer(directory, layer)
                views.append(
                    InfraLayerView(
                        layer.layer_id,
                        layer.name,
                        len(layer.points),
                        tuple(map(str, files)),
                        (),
                        _layer_active(infra.layer_area(layer.layer_id)),
                        infra.layer_area(layer.layer_id),
                    )
                )
            else:
                gone = infra.remove_layer(directory, layer.layer_id)
                views.append(
                    InfraLayerView(
                        layer.layer_id,
                        layer.name,
                        0,
                        (),
                        tuple(map(str, gone)),
                        _layer_active(infra.layer_area(layer.layer_id)),
                        infra.layer_area(layer.layer_id),
                    )
                )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: cannot write the layers in {directory}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    registered = _register_overlays(directory, present=bool(infra.layer_paths(directory, 1)))
    doc = InfraDocument(
        route=gathered.route,
        licences=gathered.licences,
        inputs=tuple(InfraInputView(*item) for item in gathered.inputs),
        read=gathered.read,
        skipped=tuple(SkipView(r, c, f) for r, c, f in gathered.skipped),
        outside=gathered.outside,
        merged=gathered.merged,
        notes=(*gathered.notes, *_infra_migration_notes(directory, gathered)),
        layers=tuple(views),
        directory=str(directory),
        registered=registered,
    )
    code = EXIT_FAILED if any(r.outcome == "refused" for r in registered) else EXIT_OK
    if envelope.wanted(args):
        envelope.emit(doc)
        return code
    for line in render_infra(doc):
        print(line)
    return code


def _infra_migration_notes(directory: Path, gathered: Gathered) -> tuple[str, ...]:
    """Said once, with the write that makes it true: a theme now written per
    region whose merged layer from before the split (D-075, #327) is still on
    disk and registered. Nothing is deleted here.  Mirrors #335's note for the
    merged RepeaterBook layer."""
    from hammunition import infra

    bases = dict.fromkeys(
        infra.layer_base(layer.layer_id)
        for layer in gathered.layers
        if layer.points and infra.layer_area(layer.layer_id) is not None
    )
    old = [b for b in bases if (directory / infra.layer_files(b)[0]).is_file()]
    if not old:
        return ()
    ids = ", ".join(old)
    return (
        f"the earlier merged layers ({ids}) are still in {directory} and still registered: "
        f"they hold every region together, so a point is drawn twice and they stay on "
        f"whichever area is active. Remove each with `hammunition maps infra remove "
        f"--layer ID` once its per-region layers are in; nothing was deleted.",
    )


def _infra_from_osm(layers: str | None, *, merged_layers: bool = False) -> Gathered:
    """The eight OpenStreetMap layers, or those *layers* names, filtered by
    osmium out of every installed extract as the operator.  D-075, #327.

    One layer per theme and region (the extract's file slug in the id), or
    with *merged_layers* one per theme across every region, where points two
    neighbouring extracts both hold are kept once."""
    from hammunition import infra
    from hammunition import repeater_sources as rs

    given = [name.strip() for name in (layers or ",".join(infra.OSM_LAYERS)).split(",")]
    unknown = [name for name in given if name not in infra.OSM_LAYERS]
    if unknown or not any(given):
        raise infra.InfraInputError(
            f"no OpenStreetMap layer {(unknown or [''])[0]!r}; the layers are "
            f"{', '.join(infra.OSM_LAYERS)}"
        )
    wanted = [key for key in infra.OSM_LAYERS if key in given]
    extracts = rs.installed_extracts(DEFAULT_PREFIX)
    folder = infra.extracts_dir(DEFAULT_PREFIX)
    if not extracts:
        raise infra.InfraInputError(
            f"no map region is installed in {folder}: `hammunition install osm-regions` "
            f"fetches your regions, and this filters the layers out of them"
        )
    points: dict[str, list[infra.Point]] = {key: [] for key in wanted}
    by_region: dict[str, dict[str, list[infra.Point]]] = {}
    seen: set[tuple[str, str, str, float, float]] = set()
    skipped: dict[str, list[int]] = {}
    read = merged = 0
    with tempfile.TemporaryDirectory(prefix="hammunition-osm-infra-") as scratch:
        for number, (pbf, _) in enumerate(extracts, start=1):
            label = f"region extract {number} of {len(extracts)}"
            one = infra.filter_extract(pbf, Path(scratch), wanted, label=label)
            read += one.read
            for reason, numbers in one.skipped.items():
                skipped.setdefault(reason, []).extend(numbers)
            if not merged_layers:
                by_region[pbf.name.removesuffix(".osm.pbf")] = one.points
                continue
            for key, found in one.points.items():
                for point in found:
                    # Neighbouring extracts overlap at their edges.
                    mark = (key, point.name, point.kind, round(point.lat, 5), round(point.lon, 5))
                    if mark in seen:
                        merged += 1
                        continue
                    seen.add(mark)
                    points[key].append(point)
    day = rs.extracts_date(extracts)
    made = [
        infra.InfraLayer(
            f"osm-{key}",
            infra.osm_layer_name(key, day),
            infra.OSM_LICENCE,
            infra.OSM_SOURCE,
            day,
            tuple(points[key]),
        )
        for key in wanted
    ]
    if not merged_layers:
        made = [
            infra.region_layer(layer, slug, by_region[slug].get(key, []))
            for key, layer in zip(wanted, made, strict=True)
            for slug in by_region
        ]
    return infra.Gathered(
        route="osm",
        licences=(infra.OSM_LICENCE,),
        # One input for the whole directory: a region's name, or its digest,
        # says where the operator is (D-057).
        inputs=((str(folder), "osm-extract", ""),),
        read=read,
        skipped=infra.skip_counts(skipped, numbered=len(extracts) == 1),
        layers=tuple(made),
        merged=merged,
        notes=(
            "filtered on this machine from the region extracts already here; nothing downloaded",
        ),
    )


def _infra_boxes() -> tuple[dict[str, tuple[float, float, float, float]], list[str]]:
    """The installed extracts' boxes by region slug, or InfraInputError naming
    the unit."""
    from hammunition import infra

    boxes, notes = infra.region_boxes_by_slug(DEFAULT_PREFIX)
    if not boxes:
        raise infra.InfraInputError(
            f"no map region with a bounding box is installed in "
            f"{infra.extracts_dir(DEFAULT_PREFIX)}: `hammunition install osm-regions` "
            f"fetches your regions, and this keeps what lies in them"
        )
    return boxes, notes


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _infra_from_file(route: str, *, merged: bool = False) -> Gathered:
    """``--from-nasr``, ``--from-eia`` or ``--from-wri``: the installed data
    unit's file, kept to the regions' boxes.  D-075."""
    from hammunition import infra
    from hammunition import infra_sources as src

    unit = src.UNITS[route]
    path = data_root(DEFAULT_PREFIX) / unit.file
    if not path.is_file():
        raise infra.InfraInputError(
            f"no {unit.what} at {path}: `hammunition install {unit.unit}` installs it "
            f"({unit.size}, {unit.licence_short})"
        )
    regions, notes = _infra_boxes()
    found = unit.read(path, list(regions.values()))
    return _gathered(
        route, found, str(path), unit.format, _file_sha256(path), notes, None if merged else regions
    )


def _gathered(
    route: str,
    found: SourceRead,
    where: str,
    fmt: str,
    sha256: str,
    notes: Sequence[str],
    regions: Mapping[str, tuple[float, float, float, float]] | None = None,
) -> Gathered:
    """*found* as layers: one per installed region, each the points inside that
    region's box (a point in two overlapping boxes is in both), or with
    *regions* None the one layer over every region.  #327."""
    from hammunition import infra

    whole = infra.InfraLayer(
        found.layer_id, found.name, found.licence, found.source, found.day, found.points
    )
    layers = (
        (whole,)
        if regions is None
        else tuple(
            infra.region_layer(
                whole,
                slug,
                [p for p in found.points if infra.in_boxes(p.lat, p.lon, [box])],
            )
            for slug, box in regions.items()
        )
    )
    return infra.Gathered(
        route=route,
        licences=(found.licence,),
        inputs=((where, fmt, sha256),),
        read=found.read,
        skipped=infra.skip_counts(found.skipped),
        layers=layers,
        outside=found.outside,
        notes=(*notes, *found.notes),
    )


@envelope.json_capable()
def cmd_maps_infra_import(args: argparse.Namespace) -> int:
    """Infrastructure and EMCOMM layers from one source.  D-075.

    ``--from-osm`` filters the installed region extracts with osmium as the
    operator (eight layers, or ``--layers``), downloading nothing;
    ``--from-nasr``, ``--from-eia`` and ``--from-wri`` read the installed
    ``faa-nasr-airports``, ``eia-860m`` and ``wri-power-plants`` data units
    and keep what lies in the regions' boxes. Each source writes its own
    layers and leaves the others. Refused as root: the files are the
    operator's."""
    from hammunition import infra

    if _refuse_root("infrastructure overlays"):
        return EXIT_FAILED
    route = (
        "osm" if args.from_osm else "nasr" if args.from_nasr else "eia" if args.from_eia else "wri"
    )
    if args.layers is not None and route != "osm":
        print(
            "error: --layers picks OpenStreetMap layers; it goes with --from-osm. "
            "Nothing was written.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        merged = bool(getattr(args, "merged", False))
        gathered = (
            _infra_from_osm(args.layers, merged_layers=merged)
            if route == "osm"
            else _infra_from_file(route, merged=merged)
        )
    except infra.InfraInputError as exc:
        print(f"error: {exc}\nNothing was changed in {infra.overlay_dir()}.", file=sys.stderr)
        return EXIT_FAILED
    return _write_infra(gathered, args)


def _fetch_infra(
    *,
    route: str,
    url: str,
    limit: int,
    disclosure: str,
    parse: Callable[..., SourceRead],
    fmt: str,
    args: argparse.Namespace,
) -> int:
    """A file fetched on request through D-064's fetch (bounded, HTTPS-only
    redirects), parsed from memory, kept to the regions' boxes and written
    as its own layer, marked unverified with its observed sha256.  D-075."""
    from hammunition import infra, repeaters
    from hammunition import infra_sources as src

    if _refuse_root("infrastructure overlays"):
        return EXIT_FAILED
    try:
        regions, notes = _infra_boxes()
    except infra.InfraInputError as exc:
        print(f"error: {exc}. Nothing was fetched.", file=sys.stderr)
        return EXIT_FAILED
    print(disclosure, flush=True)
    try:
        body, digest, when = repeaters.fetch_list(url, limit=limit, user_agent=src.USER_AGENT)
    except repeaters.RepeaterFetchError as exc:
        print(f"error: {exc}. Nothing was written.", file=sys.stderr)
        return EXIT_FAILED
    try:
        found = parse(body, url, list(regions.values()), fetched=when, sha256=digest)
    except infra.InfraInputError as exc:
        print(f"error: {exc}. Nothing was written.", file=sys.stderr)
        return EXIT_FAILED
    finally:
        del body  # never kept: FCC's archive carries owners' contact details
    merged = bool(getattr(args, "merged", False))
    return _write_infra(
        _gathered(route, found, url, fmt, digest, notes, None if merged else regions), args
    )


def cmd_maps_infra_fetch_fcc_asr(args: argparse.Namespace) -> int:
    """Fetch the FCC's Antenna Structure Registration file, on request.  D-075.

    Ruling of 2026-10-01: fetched when asked, unverified, with its observed
    sha256, like the ETCC (D-074), not a data unit whose pin dies weekly.
    Only ``RA.dat`` and ``CO.dat`` are read; ``EN.dat``, the owners'
    contact details, is never opened. No ``--json`` form."""
    from hammunition import infra_sources as src

    return _fetch_infra(
        route="fcc-asr",
        url=src.FCC_ASR_URL,
        limit=src.FCC_ASR_LIMIT,
        disclosure=(
            f"This fetches the FCC's weekly Antenna Structure Registration file from "
            f"{src.FCC_ASR_URL} (about 38 MB), now and only now, and converts it on this "
            f"machine. Only its registration and coordinate records are read; the owners' "
            f"contact records are never opened. The FCC publishes no checksum, so what "
            f"arrives is recorded by its sha256 and marked unverified."
        ),
        parse=src.parse_fcc_asr,
        fmt="fcc-asr",
        args=args,
    )


def cmd_maps_infra_fetch_nwr(args: argparse.Namespace) -> int:
    """Fetch NOAA Weather Radio's transmitter list, on request.  D-075.

    Frequency, power and every county's SAME code are kept; the live
    status is dropped before anything is written (it changes on every
    outage). Unverified, with its observed sha256. No ``--json`` form."""
    from hammunition import infra_sources as src

    return _fetch_infra(
        route="nwr",
        url=src.NWR_URL,
        limit=src.NWR_LIMIT,
        disclosure=(
            f"This fetches NOAA Weather Radio's transmitter list from {src.NWR_URL} (about "
            f"755 kB), now and only now, and converts it on this machine. Each transmitter's "
            f"frequency, power and counties' SAME codes are kept; its live status is dropped. "
            f"NWS publishes no checksum, so what arrives is recorded by its sha256 and "
            f"marked unverified."
        ),
        parse=src.parse_nwr,
        fmt="nwr-ccl",
        args=args,
    )


@envelope.json_capable()
def cmd_maps_infra_remove(args: argparse.Namespace) -> int:
    """Delete infrastructure layers and unregister what is gone.  D-075.

    Every layer by default, one with ``--layer ID``. QMapShack's
    ``poiPaths`` and the operator's Navit copy follow what remains of every
    overlay. Idempotent: nothing to remove is exit 0."""
    from hammunition import infra
    from hammunition.interface.infra import InfraRemovedDocument, render_infra_removed

    if _refuse_root("infrastructure overlays"):
        return EXIT_FAILED
    directory = infra.overlay_dir()
    every = infra.known_layers(directory)
    try:
        removed = infra.remove_layer(directory, args.layer)
    except OSError as exc:
        print(f"error: {exc}. Nothing more was removed.", file=sys.stderr)
        return EXIT_FAILED
    registered = _register_overlays(directory, present=bool(infra.layer_paths(directory, 1)))
    doc = InfraRemovedDocument(
        directory=str(directory),
        layers=(args.layer,) if args.layer else every,
        removed=tuple(str(p) for p in removed),
        unregistered=registered,
    )
    code = EXIT_FAILED if any(r.outcome == "refused" for r in registered) else EXIT_OK
    if envelope.wanted(args):
        envelope.emit(doc)
        return code
    for line in render_infra_removed(doc):
        print(line)
    return code


@envelope.json_capable()
def cmd_maps_areas(args: argparse.Namespace) -> int:
    """Every state and region with files on disk, and whether it is active.
    D-082, D-059. Read-only: nothing is written, fetched or registered."""
    from hammunition import infra
    from hammunition.areas import collect
    from hammunition.interface.areas import build_areas, render_areas
    from hammunition.repeaters import overlay_dir

    try:
        station = load_station(owner=operator(args))
    except StationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    loaded, always = collect(
        overlay_dir(), infra.overlay_dir(), data_root(DEFAULT_PREFIX), station.map_regions
    )
    doc = build_areas(station.active_areas, loaded, always, station.map_regions)
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_areas(doc):
        print(line)
    return EXIT_OK


@envelope.json_capable()
def cmd_maps_activate(args: argparse.Namespace) -> int:
    """Make some states and regions the active ones, or all, or none, and tell
    QMapShack, Navit and the browser map.  D-082.

    Writes the station's ``active_areas``, then re-registers: QMapShack's
    ``[Canvas] poiPaths`` (the same writer ``maps qmapshack`` uses) names a
    directory of links to the active areas' ``.poi`` files, Navit's own copy of
    its configuration lists only the active regions' converted maps and
    layers, and ``reference serve`` lists only the active regions and layers
    the next time it starts. A layer that belongs to no area stays registered
    always. Nothing is deleted: the data stays on disk and ``--all`` brings it
    back. Idempotent; ``--dry-run`` writes nothing."""
    from hammunition import infra
    from hammunition.areas import Active, collect
    from hammunition.interface.areas import (
        ActivateDocument,
        BrowserView,
        render_activate,
    )
    from hammunition.map_page import find_map
    from hammunition.repeaters import overlay_dir

    if _refuse_root("area settings"):
        return EXIT_FAILED
    chosen = [bool(args.areas), bool(args.all), bool(args.none)]
    if sum(chosen) != 1:
        print(
            "error: name the areas to activate (`maps activate OH MI`), or give --all or --none.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    user = operator(args)
    try:
        station = load_station(owner=user)
        after: tuple[str, ...] | None
        if args.all:
            after = None
        elif args.none:
            after = ()
        else:
            after = Station(active_areas=tuple(args.areas)).active_areas
    except StationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    before = station.active_areas
    changed = after != before
    active = Active(after)
    repeater_dir, infra_dir, data = overlay_dir(), infra.overlay_dir(), data_root(DEFAULT_PREFIX)
    registered: tuple[RegistrationView, ...] = ()
    keep, _, plan = _poi_wants(active, sync=False)  # before the links are changed
    if not args.dry_run:
        try:
            if changed:
                save_station(dataclasses.replace(station, active_areas=after), owner=user)
            registered = _register_overlays(
                repeater_dir, present=bool(_repeater_files(repeater_dir, 1))
            )
        except OSError as exc:
            print(f"error: cannot write the station file: {exc}", file=sys.stderr)
            return EXIT_FAILED
    poi_files = (
        [*_repeater_files(repeater_dir, 1), *infra.layer_paths(infra_dir, 1)]
        if active.everything
        else list(plan.wanted.values())
    )
    loaded, _always = collect(repeater_dir, infra_dir, data, station.map_regions)
    unloaded = active.unloaded(
        [e.area for e in loaded if e.kind == "state"],
        [e.area for e in loaded if e.kind == "region"],
    )
    stems: tuple[str, ...] = ()
    generated = _generated_navit_config()
    if generated.is_file():
        with contextlib.suppress(OSError):
            stems = navit_config.binfile_stems(generated.read_text(encoding="utf-8"))
    slugs = active.region_slugs(station.map_regions)
    shown = tuple(t for t in stems if slugs is None or t in slugs)
    shelf = find_map(data, overlays=infra_dir, active=active, universe=station.map_regions)
    notes = [
        (
            "Layers that belong to no area (your own import, ACMA, OpenStreetMap's, "
            "the merged infrastructure themes) stay registered whichever areas are active; "
            "per-region infrastructure follows its region."
        ),
        (
            "A running QMapShack writes its own list back when it exits, and `hammunition "
            "reference serve` reads the list when it starts: restart either to see this."
        ),
    ]
    doc = ActivateDocument(
        dry_run=bool(args.dry_run),
        before=before,
        after=after,
        changed=changed,
        unloaded=unloaded,
        poi_files=tuple(map(str, poi_files)),
        poi_paths=tuple(map(str, keep)),
        links_added=plan.add,
        links_dropped=plan.drop,
        navit_overlays=tuple(str(p) for p in _overlay_navit_maps(active)),
        navit_regions=shown,
        navit_left_out=tuple(t for t in stems if t not in shown),
        browser=BrowserView(
            regions=shelf.regions, overlays=tuple(o.layer_id for o in shelf.overlays)
        ),
        registered=registered,
        notes=tuple(notes),
    )
    code = EXIT_FAILED if any(r.outcome == "refused" for r in registered) else EXIT_OK
    if envelope.wanted(args):
        envelope.emit(doc)
        return code
    for line in render_activate(doc):
        print(line)
    return code


def cmd_maps_navit(args: argparse.Namespace) -> int:
    """Start Navit on the offline maps, with the operator's overlays.  D-064.

    What the ``navit-offline`` launcher runs. The configuration ``osm-navit``
    writes is root's; when the operator has a repeater layer, a copy of it
    with the layer in its mapset is written to their overlay directory (0600)
    and Navit opens that; with none, Navit opens the generated file and a
    stale copy of ours is removed. Under root it opens the generated file and
    writes nothing. No ``--json`` form: it replaces itself with a GUI."""
    from hammunition.repeaters import overlays_root

    generated = _generated_navit_config()
    if not generated.is_file():
        print(
            f"error: no Navit configuration at {generated}. `hammunition install osm-navit` "
            f"converts your map regions and writes it.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    target = generated
    if os.geteuid() != 0:
        active, universe = _active_station()
        overlays = _overlay_navit_maps(active)
        keep_regions = active.region_slugs(universe)
        user = overlays_root() / "navit.xml"
        if overlays or keep_regions is not None:
            view = _navit_user_config(overlays, user, generated, keep_regions)
            if view.outcome != "written":
                print(f"error: {view.detail}. Navit was not started.", file=sys.stderr)
                return EXIT_FAILED
            target = user
        elif user.is_file() and not user.is_symlink():
            user.unlink()
    sys.stdout.flush()
    sys.stderr.flush()  # execvp discards whatever Python still buffers
    try:
        os.execvp("navit", ["navit", str(target)])
    except OSError as exc:
        print(
            f"error: cannot start navit: {exc.strerror or exc}. "
            f"`hammunition install navit` installs it.",
            file=sys.stderr,
        )
    return EXIT_FAILED


@envelope.json_capable()
def cmd_reference_books(args: argparse.Namespace) -> int:
    """The Kiwix books the catalog offers, with size, licence and whether
    chosen and installed.  D-066. Read from the catalog and the disk only."""
    from hammunition.backends.kiwix import book_current
    from hammunition.interface.books import BookRow, BooksDocument

    user = operator(args)
    catalog_root = find_catalog(args.catalog)
    try:
        books = load_book_list(catalog_root)
        pins = load_pin_file(catalog_root)
        station = load_station(owner=user)
    except (KiwixError, StationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    installed = data_root(DEFAULT_PREFIX) / "kiwix-library"
    rows = []
    for book in books.values():
        pin = pins.get(book.id)
        rows.append(
            BookRow(
                id=book.id,
                title=book.title,
                file=pin.file if pin else None,
                size=pin.size if pin else None,
                licence=book.licence,
                licence_url=book.licence_url,
                note=book.note,
                chosen=book.id in station.reference_books,
                installed=pin is not None
                and book_current(installed / pin.file, BookFile(book, pin)),
            )
        )
    if envelope.wanted(args):
        envelope.emit(BooksDocument(books=tuple(rows)))
        return EXIT_OK
    width = max(len(r.id) for r in rows)
    for row in rows:
        size = human_size(row.size) if row.size is not None else "not pinned"
        marks = " ".join(
            m for m, on in (("[chosen]", row.chosen), ("[installed]", row.installed)) if on
        )
        print(f"{row.id:<{width}}  {size:>9}  {row.licence}  {marks}".rstrip())
        print(f"{'':<{width}}  {'':>9}  {row.title}")
    print()
    print(
        "Choose with `hammunition station set --reference-books ID[,ID…]`, then "
        "`hammunition install kiwix-library`. Sizes are the pinned files'."
    )
    return EXIT_OK


def cmd_reference_serve(args: argparse.Namespace) -> int:
    """The offline reference on one loopback page.  D-066.

    Books through kiwix-serve (a child, on 127.0.0.1 only), the ICS forms
    and the dictionaries on a page from the standard library. Runs as the
    operator, never as root; Ctrl-C stops both. With the map and the route
    graph installed, GraphHopper too, on 127.0.0.1, asked through the page
    (D-076). No ``--json`` form: it is a server, not a document (D-059).
    """
    import subprocess

    from hammunition import reference
    from hammunition.aircraft_page import find_aircraft
    from hammunition.backends.source import tree_destination
    from hammunition.graphhopper import GRAPH_UNIT, PROGRAM_UNIT, RouterSpec, plan_router
    from hammunition.map_page import find_map
    from hammunition.paths import owner_aware_dir

    if args.readsb_json is not None and not Path(args.readsb_json).is_absolute():
        print(
            f"error: --readsb-json {args.readsb_json}: give an absolute directory "
            f"(readsb's usual one is /run/readsb).",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        port = reference.PORT if args.port is None else reference.serve_port(args.port)
        position_port = (
            reference.POSITION_PORT
            if args.position_port is None
            else reference.position_port(args.position_port)
        )
    except ValueError as exc:
        print(f"error: {exc}.", file=sys.stderr)
        return EXIT_FAILED
    if os.geteuid() == 0:
        print(
            "error: the reference page reads files anyone can read; run it as yourself, "
            "not as root.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        books = load_book_list(find_catalog(args.catalog))
    except (KiwixError, SystemExit):
        books = {}  # the page still serves every file, named by its file name
    shelf = reference.find_shelf(data_root(DEFAULT_PREFIX), books)
    from hammunition import infra

    # D-071; the operator's infrastructure layers as overlays, D-075.
    # D-082: only the active areas' regions and layers are listed.
    active, universe = _active_station()
    map_shelf = find_map(
        data_root(DEFAULT_PREFIX), overlays=infra.overlay_dir(), active=active, universe=universe
    )
    # tar1090 (D-071, amended 2026-10-02): its page from the installed tree,
    # reading readsb's JSON, with the offline map behind it when there is one.
    aircraft = None
    try:
        aircraft = find_aircraft(
            data_root(DEFAULT_PREFIX),
            map_shelf,
            json_dir=Path(args.readsb_json) if args.readsb_json else None,
        )
    except ValueError as exc:
        print(f"warning: the aircraft page is not served: {exc}", file=sys.stderr)
    if shelf.books:
        missing = [t for t in ("kiwix-serve", "kiwix-manage") if shutil.which(t) is None]
        if missing:
            print(
                f"error: {len(shelf.books)} book(s) are installed and {', '.join(missing)} "
                f"is not on the PATH: `hammunition install kiwix-tools`.",
                file=sys.stderr,
            )
            return EXIT_FAILED
    cache = owner_aware_dir(xdg_var="XDG_CACHE_HOME", home_relative=(".cache",)) / "reference"
    library = cache / "library.xml"
    router, routes_note = plan_router(
        data=data_root(DEFAULT_PREFIX) / GRAPH_UNIT,
        tree=tree_destination(DEFAULT_PREFIX, PROGRAM_UNIT),
        home=cache / "graphhopper",
        map_ready=map_shelf.ready and bool(map_shelf.regions),
        java=shutil.which("java"),
    )

    def start_router(spec: RouterSpec) -> subprocess.Popen[bytes]:
        fd = os.open(
            spec.log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
        with os.fdopen(fd, "wb") as log_file:
            return subprocess.Popen(
                spec.argv,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=spec.config.parent,
                preexec_fn=reference.die_with_parent,
            )

    def manage(path: Path, zims: Sequence[Path]) -> None:
        result = subprocess.run(
            ["kiwix-manage", str(path), "add", *(str(z) for z in zims)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not path.is_file():
            raise SystemExit(
                f"error: kiwix-manage could not build {path} (exit {result.returncode}): "
                f"{(result.stderr or result.stdout).strip()}"
            )

    def spawn(argv: Sequence[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(list(argv), stdout=subprocess.DEVNULL)

    def log(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        return reference.run(
            port,
            shelf=shelf,
            library=library,
            spawn=spawn,
            manage=manage,
            log=log,
            map_shelf=map_shelf,
            position_port=position_port,
            router=router,
            start_router=start_router,
            routes_note=routes_note,
            aircraft=aircraft,
        )
    except OSError as exc:
        print(
            f"error: cannot listen on {reference.HOST} port {port}: {exc.strerror or exc}. "
            f"--port N serves another port.",
            file=sys.stderr,
        )
        return EXIT_FAILED


def resolve_map_regions(
    plan: InstallPlan,
    station: Station,
    catalog_root: Path,
    *,
    probe: Probe,
    today: date,
    installed: Path,
    context: ResolutionContext | None = None,
) -> MapResolution:
    """The station's map regions as dated, verifiable Geofabrik files.  D-057.

    Asked only when the plan holds a map unit -- the plan has already
    deferred or refused them when no regions are set -- and before the plan
    prints, because the dated file, its size and how it is verified are the
    disclosure.

    A region that cannot be resolved (offline, Geofabrik down, a 404) but is
    already installed under *installed* is kept as it is, and the plan says
    so (spec §8: no network leaves installed regions untouched). One that is
    not installed cannot be kept; every such region is named together in one
    :class:`GeofabrikError`.

    A **pinned** region resolves entirely from the pin list, no network
    asked at all (fix round 1, I3): offline, that looked like success, apt
    ran, and only then did the actual fetch fail, mid-transaction. So every
    region about to be fetched -- not already installed at its resolved
    snapshot, pinned or not -- is also HEAD-checked here, before the plan
    ever prints; a region already installed keeps today's behaviour and is
    never probed. Offline and after exhausted publisher retries, a verified
    catalogue supplies the identity and payload checks instead of this HEAD.
    Missing records use whole-unit disposition; installed regions are kept.
    """
    wanted = any(
        isinstance(p.block.install, RegionalDataInstall | DerivedDataInstall) for p in plan.packages
    )
    if not wanted or not station.map_regions:
        return MapResolution()
    notes: list[str] = []
    pins_path = catalog_root / "data" / "geofabrik-pins.yaml"
    if pins_path.is_file():
        pins = load_pins(pins_path)
    else:
        pins = {}
        notes.append(
            f"no Geofabrik pin list at {pins_path}; every map region is verified by "
            f"Geofabrik's MD5 only, and the plan says so beside each one."
        )
    files: list[RegionFile] = []
    kept: list[KeptRegion] = []
    refused: list[str] = []
    bar = Progress()
    bar.start("map regions against Geofabrik", len(station.map_regions))
    try:
        for index, region in enumerate(station.map_regions):
            if index:
                bar.tick()  # the previous region is finished
            try:
                resolved = resolve_region(
                    region, station.freshness, today=today, pins=pins, probe=probe, context=context
                )
            except (GeofabrikError, OSError, CatalogueMiss) as exc:
                slug = region.replace("/", "-")
                pbf = installed / f"{slug}.osm.pbf"
                if pbf.is_file():
                    kept.append(KeptRegion(region, slug, installed_snapshot(pbf), str(exc)))
                elif isinstance(exc, CatalogueMiss):
                    raise
                else:
                    refused.append(f"  {region}: {exc}")
                continue
            pbf = installed / f"{resolved.slug}.osm.pbf"
            catalogue_resolved = context is not None and (
                context.offline or ("osm-regions", region) in context.notes
            )
            if not region_current(pbf, resolved) and not catalogue_resolved:
                try:

                    def reachable(resolved: RegionFile = resolved) -> int:
                        status, _, _ = probe.head(resolved.url)
                        return status

                    def recorded_reachability(
                        resolved: RegionFile = resolved, region: str = region
                    ) -> int:
                        assert context is not None
                        context.require_payload(
                            "osm-regions",
                            region,
                            sha256=resolved.sha256,
                            size=resolved.size,
                            publisher_digest=resolved.md5,
                        )
                        return 200

                    status = (
                        context.choose("osm-regions", region, reachable, recorded_reachability)
                        if context is not None
                        else reachable()
                    )
                    problem = (
                        None if status == 200 else f"{resolved.url} answered HTTP {status}, not 200"
                    )
                except (GeofabrikError, OSError) as exc:
                    # The probe's message already names the URL; not repeated.
                    problem = str(exc)
                if problem is not None:
                    # Spec §8: offline, an installed region stays installed. Only
                    # a region with nothing installed is refused.
                    if pbf.is_file():
                        kept.append(
                            KeptRegion(region, resolved.slug, installed_snapshot(pbf), problem)
                        )
                    else:
                        refused.append(f"  {region}: {problem}")
                    continue
            files.append(resolved)
        bar.tick()
    finally:
        bar.done()
    if refused:
        raise GeofabrikError(
            f"{len(refused)} map region(s) could not be resolved and are not installed "
            f"already:\n" + "\n".join(refused)
        )
    return MapResolution(files=tuple(files), kept=tuple(kept), notes=tuple(notes))


def map_borders(
    plan: InstallPlan,
    catalog: Mapping[str, PackageManifest],
    catalog_root: Path,
    prefix: Path,
) -> tuple[BoundarySource | None, dict[str, tuple[str, ...]], list[str]]:
    """The country-border file and the region -> country table the Navit
    converter merges with (the address-search fix, D-057 amendment).

    Read at plan time with no network: the file is where the plan's
    ``boundaries`` unit installs it, and the table is
    ``catalog/data/geofabrik-countries.yaml``. A missing table is a note in
    the plan, and every region converts unmerged under -U; a boundaries
    unit of the wrong shape raises :class:`CountryBoundaryError`, which
    refuses the plan by name.
    """
    named = [
        p.block.install.boundaries
        for p in plan.packages
        if isinstance(p.block.install, DerivedDataInstall) and p.block.install.boundaries
    ]
    if not named:
        return None, {}, []
    unit = catalog.get(named[0])
    if unit is None:
        raise CountryBoundaryError(
            f"{named[0]} is named for country borders and is not in the catalog"
        )
    border = boundary_source(unit, prefix)
    table = catalog_root / "data" / "geofabrik-countries.yaml"
    if not table.is_file():
        return (
            border,
            {},
            [
                f"no region-to-country table at {table}, so no country border is merged: "
                f"every map converts with maptool -U alone, and address search files "
                f"its towns under Unknown."
            ],
        )
    try:
        return border, load_countries(table), []
    except GeofabrikError as exc:
        raise CountryBoundaryError(str(exc)) from exc


def installed_tile_counts(plan: InstallPlan, prefix: Path) -> dict[str, int]:
    """dem-tiles, offline (D-061): how many tiles each unit has installed,
    never which. Counted from the ``.tif`` files on disk, not the regions'
    ``.tiles`` records, which are written even when a tile failed."""
    return {
        planned.name: sum(1 for _ in (data_root(prefix) / planned.name).glob(f"*{TIF}"))
        for planned in plan.packages
        if isinstance(planned.block.install, DemTilesInstall)
    }


def installed_quad_counts(
    plan: InstallPlan, prefix: Path, catalog_root: Path
) -> dict[str, tuple[int, int]]:
    """topo-quads, offline (D-068): how many sheets each unit has installed,
    and how many of those the carried index has replaced with a newer
    edition. Counts, never names. With no readable index, none is called
    stale: the report does not guess."""
    units = [p for p in plan.packages if isinstance(p.block.install, TopoQuadsInstall)]
    if not units:
        return {}

    def ustopo() -> set[str] | None:
        try:
            return {q.name for q in load_ustopo_index(catalog_root / USTOPO_INDEX).quads}
        except UstopoError:
            return None

    def fstopo() -> set[str] | None:
        try:
            return {q.name for q in load_fstopo_index(catalog_root / FSTOPO_INDEX).quads}
        except FstopoError:
            return None

    counts: dict[str, tuple[int, int]] = {}
    for planned in units:
        block = planned.block.install
        assert isinstance(block, TopoQuadsInstall)
        listed = fstopo() if block.provider == "usfs-fstopo" else ustopo()
        names = [p.stem for p in (data_root(prefix) / planned.name).glob("*.tif")]
        stale = 0 if listed is None else sum(1 for n in names if n not in listed)
        counts[planned.name] = (len(names), stale)
    return counts


def no_terrain_counts(plan: InstallPlan, prefix: Path) -> dict[str, int]:
    """dem-tiles, offline (final review, I1): how many regions' records say
    Copernicus publishes no tile for any of their squares, never which."""
    counts: dict[str, int] = {}
    for planned in plan.packages:
        if not isinstance(planned.block.install, DemTilesInstall):
            continue
        records = sorted((data_root(prefix) / planned.name).glob(f"*{TILES}"))
        counts[planned.name] = sum(
            1
            for path in records
            if (entry := read_record(path, path.stem, path.stem)) is not None and entry.no_terrain
        )
    return counts


def map_work(
    plan: InstallPlan, regions: RegionsBackend, derived: DerivedBackend
) -> tuple[list[RegionFile], list[RegionFile]]:
    """(regions to download, regions to convert) for this plan."""
    downloads = [
        f
        for p in plan.packages
        if isinstance(p.block.install, RegionalDataInstall)
        for f in regions.pending(p.manifest)
    ]
    # Navit's conversions only: piece 2's converters count their own work
    # (hammunition.terrain_plan.TerrainRun), and derived.pending() reads
    # Navit's `.bin` files, which an osm-garmin unit has none of.
    conversions = [
        f
        for p in plan.packages
        if isinstance(p.block.install, DerivedDataInstall)
        and p.block.install.converter == "navit-maptool"
        for f in derived.pending(p.manifest)
    ]
    return downloads, conversions


def leftover_maps_note(plan: InstallPlan, prefix: Path) -> str | None:
    """Map data still installed while no map regions are set, named with its removal."""
    units = sorted(d.subject for d in plan.deferrals if d.why == NO_MAP_REGIONS)
    # Piece 1's regions and Navit maps, piece 2's Garmin maps, Routino
    # database and terrain tiles (D-061), the phone files (D-067), and the
    # vector-tile maps (D-071), and the route graph's record (D-076).
    patterns = (
        "*.osm.pbf",
        "*.bin",
        "*.img",
        "*.mem",
        f"*{TIF}",
        "*.map",
        "*.poi",
        "*.pmtiles",
        "graph.source",
    )
    found = [
        data_root(prefix) / unit
        for unit in units
        if any(any((data_root(prefix) / unit).glob(pattern)) for pattern in patterns)
    ]
    if not found:
        return None
    return (
        f"no map regions are set, and map data from an earlier install is still installed "
        f"under {', '.join(map(str, found))}. `hammunition uninstall {' '.join(units)}` "
        f"removes it; setting regions again keeps it current."
    )


def navit_config_blocker(plan: InstallPlan, stock: Path = navit_config.STOCK) -> str | None:
    """A Navit conversion with no Navit config to write from, found before it runs.

    The conversion can take hours; discovering at its end that
    ``/etc/navit/navit.xml`` is missing is the shape D-016 exists to prevent.
    Satisfied by the file being there, or by navit in this same transaction
    (apt runs before any conversion).
    """
    converting = [
        p.name
        for p in plan.packages
        if isinstance(p.block.install, DerivedDataInstall)
        and p.block.install.converter == "navit-maptool"
    ]
    if not converting or stock.is_file():
        return None
    if any(p.name == "navit" or "navit" in p.apt_packages for p in plan.packages):
        return None
    return (
        f"{', '.join(converting)} writes Navit's config from {stock}, which is not on this "
        f"machine, and navit is not in this transaction. Install navit first "
        f"(`hammunition install navit`), or ask for it in the same run."
    )


@envelope.json_capable(dry_run_only=True)
def cmd_install(args: argparse.Namespace) -> int:
    from hammunition.interface.envelope import target_view
    from hammunition.interface.plan import (
        PlanDocument,
        build_install_view,
        refused_plan,
        render_plan_view,
    )

    def refused(subject: str, reason: str) -> None:
        # A refusal after resolution is still a plan document, with the
        # reason the text printed (D-059); the exit code is unchanged.
        if envelope.wanted(args):
            envelope.emit(
                refused_plan("install", args.names, target_view(target), [Blocker(subject, reason)])
            )

    from hammunition.catalogue import CatalogueError
    from hammunition.signers import SignerError

    def context_refusal(subject: str, message: str) -> int:
        # Before resolution there is no plan, but the refusal is still a plan
        # document under --json (D-059) once the target can be read.
        print(f"error: {message}", file=sys.stderr)
        if envelope.wanted(args):
            try:
                detected = Target.detect()
            except DetectionError:
                return EXIT_UNPLANNABLE
            envelope.emit(
                refused_plan(
                    "install", args.names, target_view(detected), [Blocker(subject, message)]
                )
            )
        return EXIT_UNPLANNABLE

    offline = bool(getattr(args, "offline", False))
    if offline and args.no_mirror:
        return context_refusal(
            "--offline",
            "--offline resolves from the enrolled Bunker and --no-mirror ignores it; "
            "the two cannot be combined",
        )

    # One context for the run: the enrolled Bunker's catalogue, verified once,
    # before any apt probe. Offline with nothing enrolled refuses here by name.
    try:
        owner = operator(args) or None
        rctx, context_notes = _resolution_context(
            offline=offline,
            no_mirror=args.no_mirror,
            owner=owner,
            write_trust=not args.dry_run,
        )
    except (SignerError, StationError, CatalogueError, BackendError) as exc:
        return context_refusal("Bunker" if offline else "mirror", str(exc))

    refresh = args.refresh and not offline

    def plan_refusal(exc: PlanError) -> int:
        print(str(exc), file=sys.stderr)
        print(
            "\nNothing was changed. Resolution happens before installation so that a "
            "failure is a report rather than a half-installed machine (D-016).",
            file=sys.stderr,
        )
        if envelope.wanted(args):
            envelope.emit(refused_plan("install", args.names, target_view(target), exc.blockers))
        return EXIT_UNPLANNABLE

    try:
        target = Target.detect()
    except DetectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if not target.is_debian_family:
        print(
            f"error: {target.describe()} is not Debian-family. Hammunition installs "
            f"through apt and will not pretend to support this system.",
            file=sys.stderr,
        )
        return EXIT_FAILED

    catalog_root = find_catalog(args.catalog)
    packages, profiles = load_all(catalog_root)

    runner = SubprocessRunner()
    apt = AptBackend(runner)
    user = operator(args)

    station = _station_for(args, packages, profiles, user)

    suggested, suggestion_notes = _apply_suggestions(args.names, profiles, assume_yes=args.yes)

    # The repository backend plans from the file system alone, so it is built
    # before resolve; its cache is the operator's, like every other fetch.
    repos = AptRepoBackend(owner=user or None)

    read_log = TransactionLog(owner=user or None)  # read-only until the plan is confirmed
    # The hardware catalog, for resolving a rig-carrying unit's user service
    # (D-073). A malformed hardware manifest is a catalog error reported like
    # any other, never an uncaught crash of every install (review minor).
    try:
        _rig_classes, _rig_device_catalog = _load_hardware_catalog(args)
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    rig_devices: dict[str, DeviceClass | DeviceManifest] = {
        **_rig_classes,
        **_rig_device_catalog,
    }
    try:
        plan = resolve(
            [*args.names, *suggested],
            catalog=packages,
            profiles=profiles,
            target=target,
            apt=apt,
            user=user,
            refresh=refresh,
            station=station,
            resolution_context=rctx,
            # The hardware catalog, so a rig-carrying unit's user service can be
            # resolved against the station's rig (D-073); loaded here, not read
            # in the planner, so the answer is the same under sudo and in a test.
            devices=rig_devices,
            repos=repos,
            # The running kernel is a fact about this machine, not the target
            # (one Pop!_OS 24.04 VM has AX.25 under 7.0.11 and not under 7.1.5).
            kernel=KernelProbe.detect(),
            java=JavaProbe.detect(),
            # Which desktops the session files offer (D-060): files on disk,
            # so the answer under sudo is the answer outside it.
            desktops=scan_sessions(),
            # Read-only here: whether a vendor .deb already on the machine is
            # ours to skip (#63). The same log is written to after the plan.
            log=read_log,
        )
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "\nNothing was changed. Resolution happens before installation so that a "
            "failure is a report rather than a half-installed machine (D-016).",
            file=sys.stderr,
        )
        if envelope.wanted(args):
            envelope.emit(refused_plan("install", args.names, target_view(target), exc.blockers))
        return EXIT_UNPLANNABLE
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    blocked = navit_config_blocker(plan)
    if blocked is not None:
        print(f"error: {blocked}", file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        refused("navit configuration", blocked)
        return EXIT_UNPLANNABLE

    euid = os.geteuid()
    # The artifact cache and the build tree belong to the operator, not to root:
    # under sudo they would otherwise land in /root, invisible to the person who
    # asked for the build and re-downloaded on their next unprivileged run. Same
    # reasoning as the transaction log, and the same helper resolves both.
    builds = build_root(user or None)
    # An installed tree is handed to the same operator (D-043): MSHV and
    # radiosonde-auto-rx write beside their executables, and the hand-over is a
    # planned, logged step rather than a side effect of who unpacked the build.
    # D-070: the station's LAN mirror, unless --no-mirror; only the data
    # backends name a mirror path, so nothing else is ever asked of it.
    mirror = None if args.no_mirror else station.mirror
    from hammunition.mirror import fetch_transport
    from hammunition.signers import load_mirror

    try:
        enrolled_transport = fetch_transport(mirror, load_mirror(owner=user or None))
    except (SignerError, StationError, BackendError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    source = SourceBackend(
        Fetcher(
            owner=user or None,
            mirror=mirror,
            mirror_transport=enrolled_transport,
            offline=offline,
            signed_sha256=lambda path: rctx.signed_sha256(path.unit, path.name),
            bunker=rctx.verified.catalogue.bunker.name if rctx.verified is not None else None,
        ),
        build_root=builds,
        owner=user or None,
    )
    # Set after construction: the constructor stays what a test's stand-in
    # backend replaces. Offline, a payload the Bunker cannot answer for is
    # refused before any step of its unit exists.
    source.context = rctx
    git = GitBackend(
        runner=runner,
        build_root=builds,
        prefix=source.prefix,
        jobs=source.jobs,
        owner=source.owner,
        fetcher=source.fetcher,
        context=rctx,
    )
    helper_attributed = files_installed_by_hammunition(read_log)
    binary = BinaryBackend(
        fetcher=source.fetcher,
        runner=runner,
        build_root=builds,
        prefix=source.prefix,
        owner=source.owner,
        attributed_files=helper_attributed,
        context=rctx,
        dependency_check=lambda path: _deb_unmet_file(apt, path),
        recommends_of=_deb_recommends,
    )
    if offline and any(
        isinstance(p.block.install, SourceInstall)
        or (isinstance(p.block.install, BinaryInstall) and p.block.install.format == "deb")
        for p in plan.packages
    ):
        # Asked once, at plan time, and shared: an offline source build and
        # an offline vendor .deb install both need the same bwrap sandbox,
        # and with none that works the unit is refused below.
        isolation = netiso.detect()
        source.isolation = isolation
        binary = dataclasses.replace(binary, isolation=isolation)  # BinaryBackend is frozen
    venv = VenvBackend(
        venv_root=venv_root(user or None),
        bin_dir=user_bin_dir(user or None),
        fetcher=source.fetcher,
        build_root=builds,
        prefix=source.prefix,
        owner=source.owner,
        context=rctx,
    )
    node = NodeBackend(
        fetcher=source.fetcher,
        build_root=builds,
        node_root=node_root(user or None),
        bin_dir=user_bin_dir(user or None),
        context=rctx,
    )
    data = DataBackend(
        fetcher=source.fetcher,
        prefix=source.prefix,
        runner=runner,
        build_root=builds,
        owner=source.owner,
        provenance=rctx.notes,
    )
    if offline:
        # Before any station resolver: a data unit the Bunker cannot supply in
        # full is deferred (or refused, if typed) here, and nothing below plans
        # for it. What a resolver would ask a publisher is refused by name.
        try:
            plan = preflight_data(
                plan, rctx, cached=lambda unit, pin: cached_data_pin(source.fetcher, pin)
            )
            unrouted = _offline_unrouted(plan, plan_time=True, context=rctx)
            if unrouted:
                raise PlanError(unrouted)
        except PlanError as exc:
            return plan_refusal(exc)
    POLICY.reset()
    map_units = [p for p in plan.packages if isinstance(p.block.install, RegionalDataInstall)]
    try:
        resolution = resolve_map_regions(
            plan,
            station,
            catalog_root,
            probe=RetryingProbe(UrllibProbe()),
            context=rctx,
            today=date.today(),
            installed=data_root(source.prefix)
            / (map_units[0].name if map_units else "osm-regions"),
        )
    except CatalogueMiss as exc:
        try:
            deferrals = tuple(
                catalogue_deferral(p, exc)
                for p in plan.packages
                if isinstance(p.block.install, RegionalDataInstall | DerivedDataInstall)
            )
        except PlanError as error:
            return plan_refusal(error)
        names = {d.subject for d in deferrals}
        plan = _without_units(
            plan,
            names,
            [p for p in plan.packages if p.name not in names],
            [*plan.deferrals, *deferrals],
        )
        resolution = MapResolution()
    except GeofabrikError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        refused("map regions", str(exc))
        return EXIT_UNPLANNABLE
    region_files = list(resolution.files)
    kept = frozenset(k.slug for k in resolution.kept)
    # D-061: terrain tiles for the same regions, resolved before the plan
    # prints for the same reason -- each tile's size and how it is verified
    # are the disclosure. The outlines are asked once for both (D-068).
    #
    # Every probe below is retried on a 5xx, a connection error or a read
    # timeout (#200), and what a publisher still does not answer after that
    # is deferred by name for a profile member (`outages`), refused only for
    # a unit the operator typed.
    outlines = MemoProbe(RetryingProbe(UrllibProbe()))
    outages = Outages()
    # #197: what the log attributes as installed is not asked of its publisher
    # again for a week (`--recheck` asks every one); the real run verifies
    # whatever it fetches either way.
    checks = PublisherChecks.from_log(read_log, recheck=args.recheck, offline=offline)
    terrain_tile_probe = CachingTileProbe(RetryingProbe(S3Probe()), source.fetcher.cache_dir)
    usgs_tile_probe = CachingTileProbe(RetryingProbe(ustopo_probe()), source.fetcher.cache_dir)

    def defer_selection(exc: CatalogueMiss, provider: str) -> None:
        """A missing input defers the whole unit and dependents through Task 5."""
        defer_units(
            exc,
            {
                p.name
                for p in plan.packages
                if isinstance(p.block.install, DemTilesInstall | TopoQuadsInstall)
                and p.block.install.provider == provider
            },
        )

    def defer_units(exc: CatalogueMiss, names: set[str]) -> None:
        """Defer these data units and whatever depends on them (never a reader,
        which does not name its data)."""
        nonlocal plan
        while True:
            dependents = {p.name for p in plan.packages if names.intersection(p.manifest.depends)}
            if dependents <= names:
                break
            names.update(dependents)
        deferrals = [catalogue_deferral(p, exc) for p in plan.packages if p.name in names]
        plan = _without_units(
            plan,
            names,
            [p for p in plan.packages if p.name not in names],
            [*plan.deferrals, *deferrals],
        )

    try:
        dem_resolution = resolve_station_terrain(
            plan,
            resolution,
            catalog_root,
            prefix=source.prefix,
            region_probe=outlines,
            tile_probe=terrain_tile_probe,
            outages=outages,
            checks=checks,
            context=rctx,
        )
    except CatalogueMiss as exc:
        try:
            defer_selection(exc, "copernicus-glo30")
        except PlanError as error:
            return plan_refusal(error)
        dem_resolution = DemResolution()
    except CopernicusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        refused("terrain", str(exc))
        return EXIT_UNPLANNABLE
    finally:
        terrain_tile_probe.flush()
    # Issue #232: the sheets and tiles are bounded by the station (a radius
    # around its grid square by default). The circle needs the grid square;
    # without one the unit defers by name and what is installed is kept (D-035).
    topo_bound: TopoBound | None = ALL
    topo_deferrals: tuple[Deferral, ...] = ()
    try:
        topo_bound = make_bound(
            radius_km=station.topo_radius_km,
            regions=station.topo_regions,
            everything=station.topo_all,
            grid_square=station.grid_square,
        )
    except BoundUnavailable:
        topo_bound = None
        topo_deferrals = tuple(
            missing_grid_deferral(p.name)
            for p in topo_units(plan, bare_earth=station.elevation == "3dep")
        )
    # D-068: the US Topo sheets for the same regions, each HEAD-checked
    # against the ETag the carried index lists.
    try:
        topo_resolution, topo_notes = resolve_station_topo(
            plan,
            resolution,
            catalog_root,
            prefix=source.prefix,
            region_probe=outlines,
            quad_probe=usgs_tile_probe,
            outages=outages,
            checks=checks,
            bound=topo_bound,
            context=rctx,
        )
    except CatalogueMiss as exc:
        try:
            defer_selection(exc, "usgs-ustopo")
        except PlanError as error:
            return plan_refusal(error)
        topo_resolution, topo_notes = TopoResolution(), ()
    except UstopoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        refused("US Topo", str(exc))
        return EXIT_UNPLANNABLE
    finally:
        usgs_tile_probe.flush()
    # D-068, amended 2026-10-01: USGS 3DEP when the station chose it (the same
    # bucket as US Topo, so the same probe), and the Forest Service's FSTopo
    # sheets, each located through the raster gateway's one redirect.
    try:
        bare_resolution, bare_notes = resolve_station_3dep(
            plan,
            resolution,
            catalog_root,
            prefix=source.prefix,
            source=station.elevation,
            region_probe=outlines,
            tile_probe=usgs_tile_probe,
            outages=outages,
            checks=checks,
            bound=topo_bound,
            context=rctx,
        )
    except CatalogueMiss as exc:
        try:
            defer_selection(exc, "usgs-3dep")
        except PlanError as error:
            return plan_refusal(error)
        bare_resolution, bare_notes = DemResolution(), ()
    except CopernicusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        refused("3DEP", str(exc))
        return EXIT_UNPLANNABLE
    finally:
        usgs_tile_probe.flush()
    try:
        fstopo_resolution, fstopo_notes = resolve_station_fstopo(
            plan,
            resolution,
            catalog_root,
            prefix=source.prefix,
            region_probe=outlines,
            gateway=GatewayProbe(),
            outages=outages,
            checks=checks,
            bound=topo_bound,
            context=rctx,
        )
    except CatalogueMiss as exc:
        try:
            defer_selection(exc, "usfs-fstopo")
        except PlanError as error:
            return plan_refusal(error)
        fstopo_resolution, fstopo_notes = FsTopoResolution(), ()
    except FstopoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        refused("FSTopo", str(exc))
        return EXIT_UNPLANNABLE
    # D-066: the chosen Kiwix books, resolved against the book list and its
    # pins, and every one not yet installed HEAD-checked, before the plan
    # prints: each book's size and licence are the disclosure, and a pin
    # Kiwix has dropped refuses here rather than after apt has run.
    book_units = [p for p in plan.packages if isinstance(p.block.install, KiwixBooksInstall)]
    book_files: list[BookFile] = []
    if book_units:
        try:
            book_files = resolve_station_books(
                station.reference_books,
                catalog_root,
                installed=data_root(source.prefix) / book_units[0].name,
                head=retrying_head(KiwixProbe().head),
                on_outage=reporter_for(outages, book_units[0]),
                checks=checks,
                context=rctx,
                unit=book_units[0].name,
            )
        except CatalogueMiss as exc:
            try:
                defer_units(exc, {p.name for p in book_units})
            except PlanError as error:
                return plan_refusal(error)
            book_files = []
        except KiwixError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("\nNothing was changed.", file=sys.stderr)
            refused("reference books", str(exc))
            return EXIT_UNPLANNABLE
    books = KiwixBooksBackend(
        fetcher=source.fetcher,
        prefix=source.prefix,
        files=book_files,
        runner=runner,
        keep_unlisted=any(o.unit == book_units[0].name for o in outages.items)
        if book_units
        else False,
        provenance=rctx.notes,
    )
    region_notes = list(resolution.notes)
    region_notes.extend(topo_notes)
    region_notes.extend(bare_notes)
    region_notes.extend(fstopo_notes)
    # The walk-through line (issue #232): what the bound chose, and how to
    # change it, before the plan rather than 29,000 lines of it.
    ustopo_unit = next(
        (
            p
            for p in plan.packages
            if isinstance(p.block.install, TopoQuadsInstall)
            and p.block.install.provider == "usgs-ustopo"
        ),
        None,
    )
    if ustopo_unit is not None and topo_bound is not None:
        region_notes.insert(
            0,
            selection_note(
                topo_bound,
                topo_resolution,
                outside=outside_installed(
                    data_root(source.prefix) / ustopo_unit.name, topo_resolution
                ),
            ),
        )
    if topo_deferrals:
        plan = dataclasses.replace(plan, deferrals=(*plan.deferrals, *topo_deferrals))
    # D-069: CoMaps' maps for the same regions, from the carried region table,
    # and every map not yet installed HEAD-checked for its pinned size before
    # the plan prints: each map's size, licence and check are the disclosure,
    # and a version the CDN has dropped refuses here rather than after apt.
    mwm_units = [p for p in plan.packages if isinstance(p.block.install, MwmRegionsInstall)]
    mwm_files: list[MapFile] = []
    if mwm_units:
        try:
            mwm_files, mwm_notes = resolve_station_maps(
                station.map_regions,
                catalog_root,
                installed=data_root(source.prefix) / mwm_units[0].name,
                head=retrying_head(CdnProbe().head),
                on_outage=reporter_for(outages, mwm_units[0]),
                checks=checks,
                context=rctx,
                unit=mwm_units[0].name,
            )
        except CatalogueMiss as exc:
            try:
                defer_units(exc, {p.name for p in mwm_units})
            except PlanError as error:
                return plan_refusal(error)
            mwm_files, mwm_notes = [], []
        except ComapsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("\nNothing was changed.", file=sys.stderr)
            refused("CoMaps maps", str(exc))
            return EXIT_UNPLANNABLE
        region_notes.extend(mwm_notes)
    region_notes.extend(checks.notes())
    region_notes[:0] = context_notes
    region_notes.extend(_provenance_notes(rctx))
    mwm = ComapsMapsBackend(
        fetcher=source.fetcher,
        prefix=source.prefix,
        files=mwm_files,
        runner=runner,
        keep_unlisted=any(o.unit == mwm_units[0].name for o in outages.items)
        if mwm_units
        else False,
        provenance=rctx.notes,
    )
    # #200: what a publisher did not answer for becomes a deferral by name in the
    # plan (printed under "Will NOT happen", written to the transaction log, shown
    # by `status`), and one line saying how to try again.
    if outages:
        plan = dataclasses.replace(plan, deferrals=(*plan.deferrals, *outages.deferrals()))
        footer = outages.footer()
        if footer is not None:
            region_notes.append(footer)

    try:
        border, countries, border_notes = map_borders(plan, packages, catalog_root, source.prefix)
    except CountryBoundaryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        refused("country borders", str(exc))
        return EXIT_UNPLANNABLE
    region_notes.extend(border_notes)
    leftover = leftover_maps_note(plan, source.prefix)
    if leftover is not None:
        region_notes.append(leftover)
    # One ledger for both map backends: a region that did not install is not
    # converted, and the transaction ends naming every region that failed.
    ledger = MapLedger()
    regions = RegionsBackend(
        fetcher=source.fetcher,
        prefix=source.prefix,
        files=region_files,
        keep=kept,
        ledger=ledger,
        provenance=rctx.notes,
        runner=runner,
    )
    # maptool runs as the operator into the operator's build tree; only the
    # install of its verified output into the prefix is privileged. Piece 2's
    # converters (D-061) do the same, each in its own staging directory.
    map_staging = builds / "osm-navit"
    terrain = build_terrain_run(
        prefix=source.prefix,
        builds=builds,
        owner=user or None,
        runner=runner,
        fetcher=source.fetcher,
        files=region_files,
        keep=kept,
        regions=ledger,
        resolution=dem_resolution,
        pins=brouter_pins(plan),
        topo=topo_resolution,
        bare_earth=bare_resolution,
        dem_source=station.elevation,
        contour_source=contour_source(plan),
        fstopo=fstopo_resolution,
        splat_source=splat_source(plan),
        topo_bound=topo_bound,
    )
    # D-067: the phone converters, from the same regions, as the operator.
    phone = build_phone_run(
        prefix=source.prefix,
        builds=builds,
        owner=user or None,
        runner=runner,
        fetcher=source.fetcher,
        files=region_files,
        keep=kept,
        regions=ledger,
        context=rctx,
    )
    # D-071: the vector-tile maps for the browser page, from the same regions.
    tiles = build_tiles_run(
        prefix=source.prefix,
        builds=builds,
        owner=user or None,
        runner=runner,
        files=region_files,
        keep=kept,
        regions=ledger,
    )
    # D-076: GraphHopper's route graph for the browser map, from the same regions.
    graph = build_graph_run(
        prefix=source.prefix,
        builds=builds,
        owner=user or None,
        runner=runner,
        files=region_files,
        keep=kept,
        regions=ledger,
        jar=graphhopper_jar(plan),
    )
    derived = DerivedBackend(
        prefix=source.prefix,
        files=region_files,
        keep=kept,
        staging=map_staging,
        ledger=ledger,
        owner=user or None,
        runner=runner,
        boundaries=border,
        countries=countries,
        converters={
            **terrain.converters,
            **phone.converters,
            **tiles.converters,
            **graph.converters,
        },
    )
    # Only regions not already installed at their snapshot are downloaded,
    # counted and listed as downloads (the dry run is the run); a region
    # installed but not yet converted still needs conversion space.
    pending, conversions = map_work(plan, regions, derived)
    changed = frozenset(
        slug
        for p in plan.packages
        if isinstance(p.block.install, DerivedDataInstall)
        for slug in derived.converter_changed(p.manifest)
    )
    maps = (
        resolution.disclosure(
            pending,
            conversions,
            boundaries=border,
            # As the converter will resolve them, parent paths included.
            countries={f.region: derived.codes_for(f) for f in region_files},
            converter_changed=changed,
        )
        if any(
            isinstance(p.block.install, RegionalDataInstall | DerivedDataInstall)
            for p in plan.packages
        )
        else None
    )
    terrain_view = terrain.disclosure(plan)
    terrain_disk = terrain.needs(plan, cache=source.fetcher.cache_dir, prefix=source.prefix)
    phone_disk = phone.needs(plan, cache=source.fetcher.cache_dir, prefix=source.prefix)
    tiles_disk = tiles.needs(plan, prefix=source.prefix)
    graph_disk = graph.needs(plan, prefix=source.prefix)
    if (
        pending
        or conversions
        or any(terrain_disk.values())
        or any(phone_disk.values())
        or any(tiles_disk.values())
        or any(graph_disk.values())
    ):
        # Refused at plan time, before anything is confirmed, with both numbers:
        # piece 1's and piece 2's needs together, per filesystem (D-061).
        short = combined_shortfall(
            disk_needs(
                pending,
                conversions,
                cache=source.fetcher.cache_dir,
                staging=map_staging,
                prefix=source.prefix,
            ),
            terrain_disk,
            phone=phone_disk,
            tiles=tiles_disk,
            graph=graph_disk,
        )
        if short is not None:
            print(f"error: {short}", file=sys.stderr)
            print("\nNothing was changed.", file=sys.stderr)
            refused("disk space", short)
            return EXIT_UNPLANNABLE
    # The same run's map data per file system, which the books' and CoMaps'
    # maps' own checks count beside their own.
    others: dict[Path, int] = {}
    if (
        pending
        or conversions
        or any(terrain_disk.values())
        or any(tiles_disk.values())
        or any(graph_disk.values())
    ):
        others = dict(
            disk_needs(
                pending,
                conversions,
                cache=source.fetcher.cache_dir,
                staging=map_staging,
                prefix=source.prefix,
            )
        )
        for extra in (terrain_disk, tiles_disk, graph_disk):
            for path, amount in extra.items():
                others[path] = others.get(path, 0) + amount
    # The books' own room, with any map data of the same run on the same disk.
    book_pending = [f for p in book_units for f in books.pending(p.manifest)]
    book_disk = books_disk_needs(book_pending, cache=source.fetcher.cache_dir, prefix=source.prefix)
    if book_disk:
        short = books_shortfall(book_disk, others)
        if short is not None:
            print(f"error: {short}", file=sys.stderr)
            print("\nNothing was changed.", file=sys.stderr)
            refused("disk space", short)
            return EXIT_UNPLANNABLE
    # CoMaps' maps' own room (D-069), counting the same run's map data, phone
    # files and books on the same disk too.
    mwm_pending = [f for p in mwm_units for f in mwm.pending(p.manifest)]
    mwm_disk = maps_disk_needs(mwm_pending, cache=source.fetcher.cache_dir, prefix=source.prefix)
    if mwm_disk:
        beside = dict(others)
        for extra in (phone_disk, book_disk):
            for path, amount in extra.items():
                beside[path] = beside.get(path, 0) + amount
        short = maps_shortfall(mwm_disk, beside)
        if short is not None:
            print(f"error: {short}", file=sys.stderr)
            print("\nNothing was changed.", file=sys.stderr)
            refused("disk space", short)
            return EXIT_UNPLANNABLE
    # D-051 and #279: builds and resolved data whose last run completed and
    # whose current inputs and outputs still match need no fetch or conversion.
    resume_states = completion_states(
        plan,
        regions=regions,
        derived=derived,
        dem=terrain.dem,
        topo=terrain.topo,
        mwm=mwm,
    )
    built = already_built(
        plan,
        log=read_log,
        prefix=source.prefix,
        source=source,
        git=git,
        binary=binary,
        states=resume_states,
        on_disk=completion_on_disk(
            plan,
            regions=regions,
            derived=derived,
            dem=terrain.dem,
            topo=terrain.topo,
            mwm=mwm,
        ),
    )
    if offline:
        # Source, binary, venv and Node payloads come from the Bunker: one the
        # catalogue cannot answer for (nor the cache) defers its whole unit, or
        # refuses a unit the operator typed, before any step is built.
        noted = frozenset(rctx.notes)
        for name, miss in payload_misses(
            plan, rctx, built, cached=payload_cached(source.fetcher)
        ).items():
            try:
                defer_units(miss, {name})
            except PlanError as error:
                return plan_refusal(error)
        # Notes the pass earned for payloads that stay in the plan; a deferred
        # unit noted nothing (the pass notes only a unit it can fully answer).
        region_notes.extend(_provenance_notes(rctx, noted))
        sandboxed = sorted(
            p.name
            for p in plan.packages
            if isinstance(p.block.install, SourceInstall) and p.name not in built
        )
        if sandboxed and source.isolation is not None:
            region_notes.append(
                f"offline: {', '.join(sandboxed)} build with no network ("
                f"bwrap --unshare-net, read-only filesystem, private /run, /tmp and the "
                f"operator's home), so upstream's build code cannot fetch anything or "
                f"reach a host socket"
            )
        unreachable = [
            *offline_network_blockers(plan, built),
            *_offline_unrouted(plan, built, plan_time=False, context=rctx),
            *offline_payload_blockers(
                plan,
                built,
                cached=lambda artifact: cached_remote(source.fetcher, artifact),
                deb_unmet=lambda unit: _deb_unmet(apt, source.fetcher, unit),
                isolated=source.isolation is not None,
                deb_isolated=binary.isolation is not None,
            ),
        ]
        if unreachable:
            return plan_refusal(PlanError(unreachable))
    step_owners = StepOwners()
    commands = commands_for(
        plan,
        apt,
        refresh=refresh,
        skip_builds=built,
        owners=step_owners,
        source=source,
        git=git,
        binary=binary,
        venv=venv,
        node=node,
        data=data,
        regions=regions,
        derived=derived,
        dem=terrain.dem,
        topo=terrain.topo,
        books=books,
        mwm=mwm,
        repos=repos,
        config_staging=builds,
        launcher_bin=user_bin_dir(user or None),
        launcher_applications=applications_dir(user or None),
        # The operator's XDG config home, where ~/.config/systemd/user/ lives
        # (D-073 §6c). Under sudo (euid 0) with a known operator, the
        # systemctl --user steps target their manager with --machine.
        user_services_home=user_config_base(user or None),
        user_services_machine=(user if os.geteuid() == 0 and user and user != "root" else None),
    )
    # Disclose the log destination in the plan itself, so the file write (and,
    # under sudo, the chown to the operator) is shown before it happens rather
    # than surfacing after. A handoff only occurs when root is writing into
    # somebody else's home, which is exactly when log_path redirects.
    log_owner = user or None
    log_destination = log_path(log_owner)
    hands_log_to = (
        log_owner
        if (log_owner and euid == 0 and str(log_destination).startswith("/home"))
        else None
    )
    # A book or CoMaps maps unit with nothing to fetch or remove reads
    # "already installed".
    idle_books = frozenset(
        p.name
        for p in book_units
        if not books.steps(p.manifest, cast(KiwixBooksInstall, p.block.install))
    )
    idle_maps = frozenset(
        p.name
        for p in mwm_units
        # With no map for any region there is nothing installed to be current.
        if mwm.files and not mwm.steps(p.manifest, cast(MwmRegionsInstall, p.block.install))
    )
    view = build_install_view(
        plan,
        commands,
        euid=euid,
        built=built | idle_books | idle_maps,
        log_destination=log_destination,
        hands_log_to=hands_log_to,
        suggestion_notes=suggestion_notes,
        maps=maps,
        region_notes=region_notes,
        publisher_checks=checks.lines,
        terrain=terrain_view,
        sudo_keepalive=args.sudo_keepalive,
        mirror=station.mirror,
        mirror_ignored=args.no_mirror,
        idle=phone.idle(plan) | tiles.idle(plan) | graph.idle(plan),
    )
    # The tray's device helper puts a wrapper in front of root (D-056): the
    # interpreter and the engine package it runs are checked the way `hardware
    # apply` checks the same wrapper, and a refusal is a dry run's answer too.
    # A unit that is already installed at its pin writes nothing, so nothing is
    # gated for it either.
    helper_plans = [
        plan_helper(p.block.install.devctl_helper, attributed_files=helper_attributed)
        for p in plan.packages
        if p.name not in built
        and isinstance(p.block.install, BinaryInstall)
        and p.block.install.devctl_helper is not None
    ]
    for helper_plan in helper_plans:
        if helper_plan.must_refuse:
            print(
                f"error: refusing to install the device helper: "
                f"{describe_refusal(helper_plan.refusing_findings)}. That is the escalation "
                f"this project refuses outright rather than merely confirms (D-056) -- fix "
                f"it, then re-run.",
                file=sys.stderr,
            )
            return EXIT_UNPLANNABLE
    if envelope.wanted(args):
        # Reached only with --dry-run: main() refuses a real install under
        # --json before this command runs (D-059).
        envelope.emit(
            PlanDocument(
                action="install",
                requested=tuple(args.names),
                outcome="planned",
                step_count=len(view.commands),
                target=target_view(target),
                blockers=(),
                install=view,
                removal=None,
            )
        )
        return EXIT_OK
    for note in (*suggestion_notes, *region_notes):
        print(f"note: {note}")
    for line in render_plan_view(
        view, target=target_view(plan.target), full=getattr(args, "full", False)
    ):
        print(line)

    print(
        "\nAfterwards: the Hammunition menu is re-applied for this user "
        "(per-user files, unprivileged, D-050)."
    )
    if args.dry_run:
        print("\nDry run: nothing above was executed.")
        return EXIT_OK

    log = TransactionLog(owner=log_owner)

    # Asked at the keyboard once per transaction however many tray units there
    # are, and never answered by --yes (D-021, D-056): root is about to run
    # code reached through a tree one non-root account owns.
    confirmable = sorted({path for h in helper_plans for path in h.confirmable_paths})
    if confirmable and not _confirm_unsafe_interpreter(confirmable):
        print("Aborted: not confirmed. Nothing was changed.", file=sys.stderr)
        return EXIT_CONSENT

    # The size of the US Topo selection (issue #232): asked at the keyboard when
    # it is every sheet (--topo-all) or more than 10 GB, and never answered by
    # --yes. The plan above printed the same sentence.
    asked_size = size_consent(terrain_view.topo if terrain_view is not None else None)
    if asked_size is not None:
        try:
            size_record = resolve_topo_size_consent(
                asked_size.sentence(),
                asked_size.count,
                environ=os.environ,
                prompt=_prompt if sys.stdin.isatty() else None,
                assume_yes=args.yes,
                actor=user or None,
            )
        except ConsentDeclined as exc:
            print(f"\n{exc}. Nothing was changed.", file=sys.stderr)
            return EXIT_CONSENT
        except ConsentUnavailable as exc:
            print(f"\n{exc}", file=sys.stderr)
            return EXIT_CONSENT
        log.append(size_record.to_log_entry())

    # Consent gates come after the plan is printed and before anything runs.
    # --yes is passed so the call site documents that it does not help; the
    # gate never reads it (D-021).
    approved_capabilities: set[str] = set()
    for profile_name, gate in plan.consent_gates:
        capability_unit = (
            profile_name.removeprefix("file-capabilities:")
            if profile_name.startswith("file-capabilities:")
            else None
        )
        capability = next(
            (item for item in plan.file_capabilities if item.package == capability_unit),
            None,
        )
        grant = (
            " ".join(f"{name}=ep" for name in capability.capabilities)
            if capability is not None
            else "1"
        )
        try:
            record = resolve_consent(
                gate,
                profile_name,
                environ=os.environ,
                prompt=(
                    _prompt_capability
                    if capability is not None and sys.stdin.isatty()
                    else _prompt
                    if sys.stdin.isatty()
                    else None
                ),
                assume_yes=args.yes,
                actor=user or None,
                expected_value=grant,
                extra=(
                    {
                        "kind": "file_capability",
                        "unit": capability.package,
                        "binary": str(capability.path),
                        "grant": grant,
                    }
                    if capability is not None
                    else None
                ),
            )
        except ConsentDeclined as exc:
            if capability is not None:
                print(
                    f"\n{exc}; skipping the setcap step for {capability.package}. "
                    f"{capability.path} will remain without {grant}.",
                    file=sys.stderr,
                )
                continue
            print(f"\n{exc}. Nothing was changed.", file=sys.stderr)
            return EXIT_CONSENT
        except ConsentUnavailable as exc:
            if capability is not None:
                print(
                    f"\n{exc}\nSkipping the setcap step for {capability.package}; "
                    f"{capability.path} will remain without {grant}.",
                    file=sys.stderr,
                )
                continue
            print(f"\n{exc}", file=sys.stderr)
            return EXIT_CONSENT
        log.append(record.to_log_entry())
        if capability is not None:
            approved_capabilities.add(capability.package)

    # One gate per repository, after the profile gates and under the same
    # rules: the environment answer is the pinned fingerprint itself, so an
    # automation that affirms it has necessarily read the disclosure. D-040.
    for addition in plan.apt_repos:
        try:
            record = resolve_repo_consent(
                addition.repo,
                addition.unit,
                sources=addition.sources,
                keyring=addition.keyring,
                environ=os.environ,
                prompt=_prompt if sys.stdin.isatty() else None,
                assume_yes=args.yes,
                actor=user or None,
            )
        except ConsentDeclined as exc:
            print(f"\n{exc}. Nothing was changed.", file=sys.stderr)
            return EXIT_CONSENT
        except ConsentUnavailable as exc:
            print(f"\n{exc}", file=sys.stderr)
            return EXIT_CONSENT
        log.append(record.to_log_entry())

    skipped_capabilities = {
        item.package for item in plan.file_capabilities if item.package not in approved_capabilities
    }
    execution_plan = plan
    if skipped_capabilities:
        skipped_paths = {
            item.path for item in plan.file_capabilities if item.package in skipped_capabilities
        }
        commands = [
            command
            for command in commands
            if not (
                isinstance(command, Command)
                and command.argv[0] == "setcap"
                and Path(command.argv[-1]) in skipped_paths
            )
        ]
        execution_plan = dataclasses.replace(
            plan,
            file_capabilities=tuple(
                item for item in plan.file_capabilities if item.package in approved_capabilities
            ),
        )
    if not commands:
        print("\nNothing to do.")
        return EXIT_OK

    if not args.yes:
        print()
        if not _prompt("Proceed with the commands above?"):
            print("Aborted. Nothing was changed.")
            return EXIT_OK

    print("\nRunning:")
    # Pass apt as the prober so the run re-reads what it changed (D-031): an
    # exit code of 0 from apt-get or gpasswd is not evidence the package landed
    # or the membership took, and transaction_end is the record uninstall will
    # trust.
    live = LiveStatus(verbose=args.verbose)
    with activate_live(live):
        report = run_with_sudo_ticket(
            commands,
            euid=euid,
            keepalive=args.sudo_keepalive,
            log=log,
            run=lambda: execute(
                commands,
                runner,
                log=log,
                plan=execution_plan,
                echo=live.print,
                euid=euid,
                prober=apt,
                prefix=source.prefix,
                launcher_bin=user_bin_dir(user or None),
                owners=step_owners,
            ),
        )
    if log.ownership_error:
        # Not fatal — the commands ran — but not silent either. A log the
        # operator cannot append to fails on their next run instead of this one.
        print(f"\nWarning: {log.ownership_error}", file=sys.stderr)
    if report.ok and not report.verified and report.verification is not None:
        # Every command exited 0, but re-reading the effect found something it
        # claimed to do that did not happen. Fail loudly (CLAUDE.md): a green
        # exit code over a machine that did not actually change is the lie
        # D-031 exists to catch.
        print(
            f"\nCommands completed, but {len(report.verification.discrepancies)} "
            f"effect(s) could not be confirmed afterwards:",
            file=sys.stderr,
        )
        for check in report.verification.discrepancies:
            print(f"  {check.subject}: {check.detail}", file=sys.stderr)
        print(
            f"\nThe transaction log records this as unverified ({log.path}). "
            f"An exit code of 0 is not proof the change took (D-031).",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if report.ok:
        print(f"\nDone. {len(report.completed)} command(s) completed and confirmed.")
        for line in refresh_menus_after_install(catalog_root):
            print(line)
        if plan.group_memberships:
            print(
                "Group membership does not apply to a session that is already open — "
                "log out and back in."
            )
        return EXIT_OK

    assert report.failed is not None
    print(
        f"\nFailed: {report.failed.display(euid=euid)}\n{report.stderr.strip()}",
        file=sys.stderr,
    )
    stale = stale_lists_diagnosis(report.failed, report.stderr)
    if stale:
        print(f"\n{stale}", file=sys.stderr)
    retry = apt_fetch_retry_advice(report.failed, report.stderr)
    if retry:
        print(f"\n{retry}", file=sys.stderr)
    print(
        f"{len(report.completed)} command(s) completed before the failure and are "
        f"recorded in {log.path}. Hammunition does not roll back; it tells you what "
        f"it did (D-004).",
        file=sys.stderr,
    )
    return EXIT_FAILED


def run_with_sudo_ticket(
    commands: Sequence[Step],
    *,
    euid: int,
    keepalive: bool,
    log: TransactionLog,
    run: Callable[[], ExecutionReport],
    make_keepalive: Callable[[], SudoKeepalive] | None = None,
) -> ExecutionReport:
    """Run a confirmed transaction, holding sudo's ticket while it runs (D-062).

    Only when the plan said so: a run as a user that mixes root steps with
    steps that are not, and no ``--no-sudo-keepalive``. Reached only after
    the plan printed and the operator confirmed, so a dry run never gets
    here and never runs sudo. ``--yes`` changes nothing about it: the
    password prompt is sudo's own and is asked whether or not the
    confirmation was skipped.

    ``sudo -v`` asks once, before the first step, while the operator is
    still at the keyboard. If it fails, nothing is refreshed and every root
    step prompts as it would have without D-062. The refresh stops when
    ``run`` returns or raises, and the log records how it went.
    """
    if not (keepalive and keepalive_wanted(commands, euid=euid)):
        return run()

    def warn(message: str) -> None:
        # The status line's writer owns the terminal while a step runs, so a
        # keepalive failure cannot land in the middle of it (#270).
        live = current_live()
        text = f"\nwarning: {message}"
        if live is not None:
            live.print(text, err=True)
        else:
            print(text, file=sys.stderr)

    ticket = make_keepalive() if make_keepalive is not None else SudoKeepalive(warn=warn)
    print("\nsudo: asking once, before the first step (D-062).")
    if not ticket.validate():
        print(
            "\nwarning: `sudo -v` did not succeed, so sudo's ticket will not be kept "
            "valid. Each step that needs root asks for itself.",
            file=sys.stderr,
        )
        log.append(
            {
                "event": "sudo_keepalive_begin",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "validated": False,
                "interval_seconds": ticket.interval,
            }
        )
        return run()
    log.append(
        {
            "event": "sudo_keepalive_begin",
            "version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "validated": True,
            "interval_seconds": ticket.interval,
        }
    )
    ticket.start()
    try:
        return run()
    finally:
        failure = ticket.stop()
        log.append(
            {
                "event": "sudo_keepalive_end",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "refreshes": ticket.refreshes,
                "failed": None
                if failure is None
                else {
                    "argv": list(failure.argv),
                    "returncode": failure.returncode,
                    "timestamp": failure.timestamp,
                },
            }
        )


def apt_fetch_retry_advice(failed: Command | Action, stderr: str) -> str | None:
    """Advise what to do when apt's configured archive retries are exhausted."""
    if isinstance(failed, Action) or failed.argv[:1] != ("apt-get",):
        return None
    if not {"install", "update"}.intersection(failed.argv[1:]):
        return None
    if not any(phrase in stderr for phrase in ("Failed to fetch", "Unable to fetch some archives")):
        return None
    return (
        "apt retried archive fetches 3 times with `-o Acquire::Retries=3` and the fetch still "
        "failed. Check the mirror or connection, then run the same command again; cached "
        "downloads are reused."
    )


def stale_lists_diagnosis(failed: Command | Action, stderr: str) -> str | None:
    """What a 404 from ``apt-get install`` means, in the operator's terms.

    The plan's candidate check passed against the package lists on disk;
    the archive has since replaced a version those lists name; apt asked for
    the old file and was told it is gone. The catalog was right and the
    machine is unchanged -- apt fetches every archive before it unpacks
    any, so a fetch failure leaves nothing half-installed -- and the fix is
    the one apt itself hints at under the URLs. Six of fifteen profiles on
    a four-day-old Parrot guest failed exactly this way (2026-09-03), each
    report ending in seven ``Failed to fetch`` lines and no diagnosis.
    """
    if isinstance(failed, Action) or "apt-get" not in failed.argv or "install" not in failed.argv:
        return None
    missing = stale_fetches(stderr)
    if not missing:
        return None
    shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
    return (
        f"The package lists on this machine are older than the archive: apt asked for "
        f"{len(missing)} file(s) the mirror no longer has ({shown}). The plan resolved "
        f"against those lists, so the catalog is not at fault, and apt downloads every "
        f"archive before unpacking any, so this command installed nothing. Refresh the "
        f"lists and run the same install again: `sudo apt-get update`, or run without "
        f"--no-refresh so the transaction's own refresh does it first. If the refresh "
        f"did run, the mirror moved between the update and this fetch; run it again."
    )


@envelope.json_capable(dry_run_only=True)
def cmd_uninstall(args: argparse.Namespace) -> int:
    from hammunition.interface.envelope import target_view
    from hammunition.interface.plan import (
        BlockerLine,
        PlanDocument,
        build_removal_view,
        render_removal_view,
    )

    try:
        target = Target.detect()
    except DetectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if not target.is_debian_family:
        print(
            f"error: {target.describe()} is not Debian-family; there is nothing "
            f"Hammunition could have installed here.",
            file=sys.stderr,
        )
        return EXIT_FAILED

    catalog_root = find_catalog(args.catalog)
    packages, profiles = load_all(catalog_root)
    runner = SubprocessRunner()
    apt = AptBackend(runner)
    user = operator(args)
    log = TransactionLog(owner=user or None)

    attributed = installed_by_hammunition(log)
    attributed_files = files_installed_by_hammunition(log)
    attributed_capabilities = file_capabilities_installed_by_hammunition(log)
    removal_paths = RemovalPaths(
        prefix=DEFAULT_PREFIX,
        venv_root=venv_root(user or None),
        bin_dir=user_bin_dir(user or None),
        applications_dir=applications_dir(user or None),
        node_root=node_root(user or None),
    )
    # Probe every package the request could touch, so the plan partitions on
    # what is installed now rather than on what the log said at install time.
    probe_set: set[str] = set()
    for name in args.names:
        for unit in profiles[name].packages if name in profiles else [name]:
            manifest = packages.get(unit)
            if manifest is None:
                continue
            block = manifest.resolve(target.distro, target.version, target.arch)
            if block is None:
                continue
            if isinstance(block.install, AptInstall):
                probe_set.update(block.install.packages)
            elif isinstance(block.install, BinaryInstall) and block.install.deb_package:
                probe_set.add(block.install.deb_package)
    try:
        states = apt.probe(sorted(probe_set)) if probe_set else {}
        plan = plan_removal(
            args.names,
            catalog=packages,
            profiles=profiles,
            target=target,
            attributed=attributed,
            states=states,
            paths=removal_paths,
            attributed_files=attributed_files,
            attributed_capabilities=attributed_capabilities,
            log=log,
        )
    except RemovalError as exc:
        print(str(exc), file=sys.stderr)
        print("\nNothing was changed.", file=sys.stderr)
        if envelope.wanted(args):
            envelope.emit(
                PlanDocument(
                    action="uninstall",
                    requested=tuple(args.names),
                    outcome="refused",
                    step_count=0,
                    target=target_view(target),
                    blockers=(BlockerLine(subject="uninstall", reason=str(exc), remedy=None),),
                    install=None,
                    removal=None,
                )
            )
        return EXIT_UNPLANNABLE
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    euid = os.geteuid()
    commands: list[Step] = list(apt.remove_commands(plan.apt_packages))
    # User services the removed units wrote (D-073 §6d): disable each, then
    # remove its file only if it still carries our header; a file the operator
    # rewrote is left and named. Linger is untouched — it is a station setting
    # with its own reversal (§5a).
    # Expand a profile name to its members before looking for user services:
    # `uninstall station` must reach rig-service, not just a package literally
    # named "station" (review I2). The removal step itself no-ops on a unit
    # whose file is absent, so a deferred install leaves nothing to disable.
    uninstall_units: list[str] = []
    for name in args.names:
        if name in profiles:
            uninstall_units.extend(profiles[name].packages)
        else:
            uninstall_units.append(name)
    user_service_names = list(
        dict.fromkeys(
            svc.name
            for unit in uninstall_units
            if (unit_manifest := packages.get(unit)) is not None
            for svc in unit_manifest.user_services
        )
    )
    user_service_units = list(
        dict.fromkeys(
            unit
            for unit in uninstall_units
            if (unit_manifest := packages.get(unit)) is not None and unit_manifest.user_services
        )
    )
    if user_service_names:
        uninstall_user = operator(args)
        commands.extend(
            user_service_removal_steps(
                user_service_names,
                units=user_service_units,
                home=user_config_base(uninstall_user or None),
                machine=(
                    uninstall_user if euid == 0 and uninstall_user not in ("", "root") else None
                ),
            )
        )
    commands.extend(artifact_removal_steps(plan))
    if any(r.kind == "apt-repo" for removals in plan.artifacts.values() for r in removals):
        # The files are gone; apt's index still lists the repository until
        # it is read again. Same transaction, so `uninstall` leaves apt in
        # the state it found it (D-040).
        commands.append(apt.refresh_command())

    view = build_removal_view(plan, commands, euid=euid)
    if envelope.wanted(args):
        # Reached only with --dry-run (D-059).
        envelope.emit(
            PlanDocument(
                action="uninstall",
                requested=tuple(args.names),
                outcome="planned",
                step_count=len(view.commands),
                target=target_view(target),
                blockers=(),
                install=None,
                removal=view,
            )
        )
        return EXIT_OK
    for line in render_removal_view(view, target=target_view(target)):
        print(line)
    if not commands:
        return EXIT_OK

    if args.dry_run:
        print("\nDry run: nothing above was executed.")
        return EXIT_OK
    if not args.yes:
        print()
        if not _prompt("Proceed with the commands above?"):
            print("Aborted. Nothing was changed.")
            return EXIT_OK

    print("\nRunning:")
    live = LiveStatus(verbose=args.verbose)
    with activate_live(live):
        report = run_removal(
            commands,
            runner,
            log=log,
            plan=plan,
            target=target,
            echo=live.print,
            euid=euid,
            prober=apt,
        )
    if log.ownership_error:
        print(f"\nWarning: {log.ownership_error}", file=sys.stderr)
    if report.ok and not report.verified and report.verification is not None:
        print(
            f"\nCommands completed, but {len(report.verification.discrepancies)} "
            f"removal(s) could not be confirmed afterwards:",
            file=sys.stderr,
        )
        for check in report.verification.discrepancies:
            print(f"  {check.subject}: {check.detail}", file=sys.stderr)
        print(
            f"\nThe transaction log records this as unverified ({log.path}). "
            f"An exit code of 0 is not proof the change took (D-031).",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if report.ok:
        print(f"\nDone. {len(report.completed)} command(s) completed and confirmed.")
        return EXIT_OK

    assert report.failed is not None
    print(
        f"\nFailed: {report.failed.display(euid=euid)}\n{report.stderr.strip()}",
        file=sys.stderr,
    )
    print(
        f"{len(report.completed)} command(s) completed before the failure and are "
        f"recorded in {log.path}.",
        file=sys.stderr,
    )
    return EXIT_FAILED


def refresh_menus_after_install(catalog_root: Path) -> list[str]:
    """Re-apply the Hammunition menu for this user, quietly. D-050, round 3.

    After the full install on the field laptop, 42 of 60 tagged entries sat
    directly under *Hammunition*: placement happens at apply time, and the
    last apply predated the install. So a real install ends here. Per-user
    files only, unprivileged; a machine where no root menu can be decided
    gets a line saying so, never a failed install.
    """
    from hammunition.menus import (
        APPLICATIONS_DIR,
        LOCAL_APPLICATIONS_DIR,
        MenuPaths,
        MenuPrefixError,
        cli_entries,
        cli_entry_steps,
        decorate_entries,
        load_vocabulary,
        menu_steps,
        missing_launcher_steps,
        place_installed_entries,
        refresh_command,
        refresh_launcher_entries,
        resolve_menu_prefix,
    )

    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    config_dirs = [
        Path(d) for d in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if d
    ]
    try:
        prefix = resolve_menu_prefix(None, os.environ.get("XDG_MENU_PREFIX"), config_dirs)
    except MenuPrefixError as exc:
        return [
            f"Menu: not re-applied -- {exc}. Run `hammunition menus apply` in your desktop session."
        ]
    vocabulary = load_vocabulary(catalog_root / "categories.yaml")
    manifests, _ = load_all(catalog_root)
    hidden = vocabulary.hidden_categories
    placement = place_installed_entries(
        manifests.values(),
        applications_dir=APPLICATIONS_DIR,
        hidden=hidden,
        built_applications_dir=LOCAL_APPLICATIONS_DIR,
    )
    generated = cli_entries(manifests.values(), placement, hidden=hidden, prefix=DEFAULT_PREFIX)
    paths = MenuPaths(
        menus_dir=config_home / "menus", directories_dir=data_home / "desktop-directories"
    )
    applications = data_home / "applications"
    steps = menu_steps(
        vocabulary.categories,
        paths,
        menu_prefix=prefix,
        placement=placement,
        groups=vocabulary.groups,
    )
    steps += cli_entry_steps(generated, applications, vocabulary.icons)
    # Issue #174: a launcher the catalog renamed is written under its new
    # name here; removing the old one is `menus apply`'s, which prints it.
    try:
        steps += missing_launcher_steps(
            manifests.values(),
            bin_dir=user_bin_dir(None),
            applications_dir=applications,
            prefix=DEFAULT_PREFIX,
        )
    except BackendError as exc:
        return [f"Menu: not re-applied -- {exc}"]
    steps += refresh_launcher_entries(manifests.values(), applications)
    steps += decorate_entries(applications, vocabulary.icons)
    for step in steps:
        step.perform()
    refresh = refresh_command(prefix)
    if refresh is not None:
        SubprocessRunner().run(refresh)
    placed = sum(len(v) for v in placement.by_category.values())
    return [
        f"Menu: re-applied for this user -- {len(placement.claimed)} entries placed "
        f"{placed} times, {len(generated.entries)} generated, {len(generated.skipped)} "
        f"without one (`hammunition menus apply` lists them)"
    ]


def cmd_menus_apply(args: argparse.Namespace) -> int:

    from hammunition.launchers import shadowing_launcher_steps
    from hammunition.menus import (
        APPLICATIONS_DIR,
        LOCAL_APPLICATIONS_DIR,
        MenuPaths,
        MenuPrefixError,
        cli_entries,
        cli_entry_steps,
        decorate_entries,
        device_entries,
        device_entry_steps,
        engine_entry_steps,
        gnome_commands,
        load_vocabulary,
        menu_steps,
        missing_launcher_steps,
        place_installed_entries,
        placement_summary,
        refresh_command,
        refresh_launcher_entries,
        resolve_menu_prefix,
    )

    catalog_root = find_catalog(args.catalog)
    vocabulary = load_vocabulary(catalog_root / "categories.yaml")
    categories, groups = vocabulary.categories, vocabulary.groups
    manifests, _ = load_all(catalog_root)
    hidden = vocabulary.hidden_categories
    placement = place_installed_entries(
        manifests.values(),
        applications_dir=APPLICATIONS_DIR,
        hidden=hidden,
        built_applications_dir=LOCAL_APPLICATIONS_DIR,
    )
    # D-050: every installed unit findable. Generated per user, beside the
    # launchers, from what dpkg says is on this machine's path.
    generated = cli_entries(manifests.values(), placement, hidden=hidden, prefix=DEFAULT_PREFIX)

    home = Path.home()
    paths = MenuPaths(
        menus_dir=Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "menus",
        directories_dir=Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
        / "desktop-directories",
    )
    config_dirs = [
        Path(d) for d in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if d
    ]
    try:
        prefix = resolve_menu_prefix(
            args.menu_prefix, os.environ.get("XDG_MENU_PREFIX"), config_dirs
        )
    except MenuPrefixError as exc:
        print(f"Refusing to write a menu that nothing would read: {exc}", file=sys.stderr)
        return EXIT_FAILED
    steps = menu_steps(categories, paths, menu_prefix=prefix, placement=placement, groups=groups)
    steps.extend(
        cli_entry_steps(generated, paths.directories_dir.parent / "applications", vocabulary.icons)
    )
    applications = paths.directories_dir.parent / "applications"
    # Issue #174: a generated launcher named like a PATH binary is removed
    # first, and the catalog's renamed one is written below; both print.
    steps.extend(shadowing_launcher_steps(user_bin_dir(None), applications))
    try:
        steps.extend(
            missing_launcher_steps(
                manifests.values(),
                bin_dir=user_bin_dir(None),
                applications_dir=applications,
                prefix=DEFAULT_PREFIX,
            )
        )
    except BackendError as exc:
        print(f"Refusing to write a launcher: {exc}", file=sys.stderr)
        return EXIT_FAILED
    steps.extend(refresh_launcher_entries(manifests.values(), applications))
    steps.extend(decorate_entries(applications, vocabulary.icons))

    # D-050: a park/wake entry per catalogued device attached *today*. Built
    # from the same hardware match the CLI verbs use, so the menu never
    # offers a device that is not plugged in.
    classes, devices = _load_hardware_catalog(args)
    hw_entries: dict[str, DeviceClass | DeviceManifest] = {**classes, **devices}
    from hammunition.hardware.detect import match_catalog, read_usb_bus
    from hammunition.hardware.power import parkable as parkable_devices

    hw_matches, _ = match_catalog(read_usb_bus(), hw_entries)
    found, _ = parkable_devices(hw_matches, hw_entries)
    device_generated = device_entries(found, hw_entries, manifests, hidden)
    from hammunition.launchers import engine_path

    try:
        engine = engine_path(user_bin_dir(None))
    except BackendError as exc:
        print(f"Refusing to write menu entries that could not start: {exc}", file=sys.stderr)
        return EXIT_FAILED
    steps.extend(
        device_entry_steps(device_generated, applications, vocabulary.icons, engine=engine)
    )
    # #302: `hammunition console` is the engine's own, so its entry is written here,
    # not generated from a unit.
    steps.extend(engine_entry_steps(applications, engine=engine))

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    wants_gnome = "GNOME" in desktop.upper() or args.gnome
    placed = sum(len(v) for v in placement.by_category.values())
    print(
        f"Menu tree: {sum(g.menu for g in groups)} groups shown, {len(categories) - len(hidden)} categories, "
        f"menu prefix {prefix!r}; "
        f"{len(placement.claimed)} desktop entries from installed catalog packages "
        f"placed {placed} times by their manifests' categories (dpkg -L, checked on disk); "
        f"{len(generated.entries)} entries generated for installed units that ship none; "
        f"{len(device_generated)} power-control entries for parkable devices attached now"
    )
    for line in placement_summary(placement):
        print(line)
    for unit, why in generated.skipped:
        print(
            f"  {unit}: no entry generated -- {why}; a `launchers` block in its manifest is the fix"
        )
    for step in steps:
        print(f"  {step.display()}")
        outcome = step.perform()
        print(f"    {outcome}")
    if wants_gnome:
        runner = SubprocessRunner()
        print("GNOME app-folder (needs your session bus):")
        for command in gnome_commands(placement, groups):
            # The two read-modify-write steps are python -c bodies; the
            # description says what they do and the body would fill a screen.
            shown = command.argv[:2] if command.argv[0] == "python3" else command.argv
            print(f"  # {command.description}\n  $ {' '.join(shown)} …")
            result = runner.run(command)
            if result.returncode != 0:
                print(
                    f"error: {result.stderr.strip()[:200]}\n"
                    f"GNOME folders live in dconf; run this inside your desktop "
                    f"session, not over bare SSH.",
                    file=sys.stderr,
                )
                return EXIT_FAILED
    else:
        print(
            "GNOME app-folder skipped: XDG_CURRENT_DESKTOP does not say GNOME "
            "(pass --gnome to force). The menu-spec files above serve Xfce and "
            "friends either way."
        )
    refresh = refresh_command(prefix)
    if refresh is not None:
        print(f"  # {refresh.description}\n  $ {' '.join(refresh.argv)}")
        result = SubprocessRunner().run(refresh)
        if result.returncode != 0:
            print(
                f"warning: {refresh.argv[0]} exited {result.returncode}; the menu shows at next login"
            )
        print("Done.")
    else:
        print("Done. Menus refresh on next login (or `xfce4-panel -r` / GNOME Shell reload).")
    return EXIT_OK


@envelope.json_capable()
def cmd_show(args: argparse.Namespace) -> int:
    """Print a profile, its consent disclosure included, without installing it.

    Under --json a unit's name is accepted too, and emits its manifest (D-059);
    the text form describes profiles only, as it always has.
    """
    from hammunition.interface.catalog import (
        build_profile,
        build_unit,
        detect_target,
        render_profile,
    )

    catalog_root = find_catalog(args.catalog)
    packages, profiles = load_all(catalog_root)
    profile = profiles.get(args.profile)
    if profile is None:
        manifest = packages.get(args.profile)
        if manifest is not None and envelope.wanted(args):
            envelope.emit(build_unit(manifest, detect_target()))
            return EXIT_OK
        print(f"error: no profile named {args.profile!r}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    doc = build_profile(profile)
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_profile(doc):
        print(line)
    return EXIT_OK


def _prompt(text: str) -> bool:
    """Yes/no on the terminal. Anything that is not an explicit yes is a no."""
    print(text)
    try:
        answer = input("Type 'yes' to continue: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"yes", "y"}


def _prompt_capability(text: str) -> bool:
    """Require the exact typed word for an optional file-capability grant."""
    print(text)
    try:
        answer = input("Type 'yes' to grant this capability: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer == "yes"


def _confirm_unsafe_interpreter(paths: list[str]) -> bool:
    """A typed `yes`, never `--yes` (D-021, D-056's ruling as amended).

    Installing the helper is never refused for this — it is the normal shape
    of a venv the operator owns — but it is asked at the keyboard every time,
    and a convenience flag cannot answer it.

    ``paths`` may name more than one offending component (the interpreter and
    the package tree can both be non-root-owned at once). Every one of them is
    disclosed, because a partial disclosure is not a disclosure. The answer is
    `yes`, not the path typed back: the path is printed directly above the
    prompt, so retyping it proved nothing a `yes` does not (2026-09-27
    amendment; D-040's fingerprint stays typed, because checking it against
    the vendor's published value is the point there).
    """
    joined = "\n".join(f"  {p}" for p in paths)
    print(
        f"\nWritable only by the account that owns it, not by root (the ordinary "
        f"shape of a venv the operator created; never refused for this alone):\n"
        f"{joined}\n"
        f"The polkit action about to be installed lets root run code reached "
        f"through one of these. Any active local session can authenticate once "
        f"and run it as root for a few minutes afterwards (auth_self_keep). "
        f"This is not refused, but `--yes` does not satisfy it."
    )
    try:
        answer = input("Proceed? [yes/no]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"yes", "y"}


# ---------------------------------------------------------------------------
# hardware — the permissions and udev half of the device role (D-029)
# ---------------------------------------------------------------------------


def _load_hardware_catalog(
    args: argparse.Namespace,
) -> tuple[dict[str, DeviceClass], dict[str, DeviceManifest]]:
    from hammunition.manifest.load import load_hardware

    catalog_root = find_catalog(args.catalog)
    return load_hardware(catalog_root / "hardware")


def cmd_hardware_list(args: argparse.Namespace) -> int:
    """What is plugged in, what the catalog recognises, and what it would set up."""
    from hammunition.hardware import plan_hardware

    try:
        target = Target.detect()
    except DetectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    classes, devices = _load_hardware_catalog(args)
    user = operator(args)
    groups_now = user_groups(user) if user else frozenset()
    plan = plan_hardware(classes, devices, user=user, user_groups_now=groups_now)

    print(f"Target: {target.describe()}\n")
    if plan.detected:
        print("Recognised devices attached:")
        for match in plan.detected:
            flag = "  (ambiguous identifier — could be another device)" if match.ambiguous else ""
            print(f"  {match.name:20} {match.attached.describe()}{flag}")
    else:
        print("No catalogued devices detected on the USB bus.")
    if plan.unrecognised:
        print("\nAttached but not in the catalog (a device we could add):")
        for dev in plan.unrecognised:
            print(f"  {dev.describe()}")
    print(
        f"\nudev rules: {len(plan.rules_content.splitlines())} lines for the whole "
        f"catalog would go to {plan.rules_path}"
        + (" — already current." if plan.rules_already_current else " (not yet applied).")
    )
    wanted = sorted(set(plan.groups_to_add) | set(plan.groups_present))
    if wanted:
        joined = ", ".join(
            f"{g} ✓" if g in plan.groups_present else f"{g} (missing)" for g in wanted
        )
        print(f"Access groups {user!r} needs: {joined}")
    print("\nRun `hammunition hardware apply` to write the rules and join the groups.")
    return EXIT_OK


def _geoclue_for_apply(args: argparse.Namespace, user: str) -> GeoClueGrants | None:
    """GeoClue's half of `hardware apply` (D-069): None under ``--no-geoclue``.
    Raises GeoClueError, before anything runs, on a file Hammunition did not write."""
    from hammunition import geoclue

    if getattr(args, "no_geoclue", False):
        return None
    return geoclue.plan_geoclue(user)


def _geoclue_disclosure(args: argparse.Namespace, geo: GeoClueGrants | None) -> list[str]:
    from hammunition import geoclue

    if geo is None:
        return (
            ["GeoClue (D-069): left alone (--no-geoclue)."]
            if getattr(args, "no_geoclue", False)
            else []
        )
    return geoclue.disclose(geo)


def _disclose_gps_resume(plan: HardwarePlan) -> None:
    """The resume step's disclosure (issue #177), when the plan carries one."""
    if plan.gps_resume is not None:
        for line in gps_resume.disclose(plan.gps_resume):
            print(line)


def _gps_resume_commands(plan: HardwarePlan, staging_root: str) -> list[Command]:
    if plan.gps_resume is None:
        return []
    return gps_resume.install_commands(plan.gps_resume, staging_root)


def _stage_gps_resume(plan: HardwarePlan, staging_dir: Path) -> list[Command]:
    """Stage the step's two files; return its commands as they will run, so
    the apply loop can log each one (``gps_resume``)."""
    if plan.gps_resume is None:
        return []
    gps_resume.stage(plan.gps_resume, staging_dir)
    return _gps_resume_commands(plan, str(staging_dir))


def _disclose_devctl_export(plan: HardwarePlan) -> None:
    """The helper hand-over, and the two lists' disclosure (D-056, amended 2026-10-02)."""
    if plan.polkit.handed_over is not None:
        print(
            f"The privileged helper at {plan.polkit.helper_path} is hammunition-tray's "
            f"(it answers --version: {plan.polkit.handed_over}). This run does not write "
            f"it, and writes the polkit action only where none exists."
        )
    if plan.devctl_export is not None:
        for line in devctl_export.disclose(plan.devctl_export):
            print(line)


def _devctl_export_commands(plan: HardwarePlan, staging_root: str) -> list[Command]:
    if plan.devctl_export is None:
        return []
    return devctl_export.install_commands(plan.devctl_export, staging_root)


def _stage_devctl_export(plan: HardwarePlan, staging_dir: Path) -> list[Command]:
    """Stage the two lists; return their commands as they will run, so the apply
    loop can log each one (``devctl_export``)."""
    if plan.devctl_export is None:
        return []
    devctl_export.stage(plan.devctl_export, staging_dir)
    return _devctl_export_commands(plan, str(staging_dir))


def cmd_hardware_apply(args: argparse.Namespace) -> int:
    """Write the catalog's udev rules and join the device-access groups."""
    from hammunition.gpstime.grants import disclose, grant_commands, stage_grants, verify_grants
    from hammunition.gpstime.mode import TimeError
    from hammunition.hardware import plan_hardware

    try:
        Target.detect()
    except DetectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    classes, devices = _load_hardware_catalog(args)
    user = operator(args)
    if not user:
        print("error: could not determine which user to set up.", file=sys.stderr)
        return EXIT_FAILED
    groups_now = user_groups(user)
    try:
        plan = plan_hardware(
            classes,
            devices,
            user=user,
            user_groups_now=groups_now,
            with_time=not getattr(args, "no_gps_time", False),
            with_gps_resume=not getattr(args, "no_gps_resume", False),
            with_devctl_export=True,
        )
    except TimeError as exc:
        print(
            f"error: {exc}\n`--no-gps-time` sets up devices without GPS time (D-058).",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    except (gps_resume.GpsResumeError, devctl_export.DevctlExportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    from hammunition import geoclue

    try:
        geo = _geoclue_for_apply(args, user)
    except geoclue.GeoClueError as exc:
        print(
            f"error: {exc}\n`--no-geoclue` sets up devices without GeoClue's GPS socket (D-069).",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE

    print(f"Hardware setup for {user!r}\n")
    if plan.omissions:
        print("Catalogued but deliberately not given a rule (see device-naming.md):")
        for om in plan.omissions[:8]:
            print(f"  {om.render()}")
        if len(plan.omissions) > 8:
            print(f"  … and {len(plan.omissions) - 8} more")
        print()

    if plan.is_noop and (geo is None or geo.is_noop):
        print(
            "Nothing to do: the rules file already matches, you are in every access "
            "group, the power-control helper and its polkit action are installed, and "
            "GPS time's grants and the helper's device and service lists are in place. "
            "Hardware setup is complete."
        )
        if plan.polkit.handed_over is not None:
            print(
                f"The helper is hammunition-tray's ({plan.polkit.handed_over}); "
                f"this engine no longer writes it."
            )
        if geo is not None and geo.installed:
            print("GeoClue already reads the GPS tether's socket (D-069).")
        return EXIT_OK

    def build_commands(
        staging_root: str,
    ) -> tuple[list[Command], Command | None, Command | None, list[Command]]:
        """Commands as they would look staged under ``staging_root``.

        Called twice: once with a placeholder string for the disclosure and
        ``--dry-run`` preview, before any staging directory exists, and once
        for real with the actual `mkdtemp()` path once every gate below has
        been passed. Fix round 2: `--dry-run` must be a true no-op, and the
        old code called `mkdtemp()` -- a real filesystem side effect -- before
        the dry-run check even ran.
        """
        built: list[Command] = []
        if not plan.rules_already_current:
            built += [
                Command(
                    argv=(
                        "install",
                        "-D",
                        "-m",
                        "0644",
                        f"{staging_root}/udev-staging.rules",
                        str(plan.rules_path),
                    ),
                    description=f"Install the generated rules to {plan.rules_path}",
                    requires_root=True,
                ),
                Command(
                    argv=("udevadm", "control", "--reload-rules"),
                    description="Reload udev so the new rules take effect",
                    requires_root=True,
                ),
                Command(
                    argv=("udevadm", "trigger"),
                    description="Apply the rules to devices already attached",
                    requires_root=True,
                ),
            ]
        helper_cmd: Command | None = None
        policy_cmd: Command | None = None
        if not plan.polkit.helper_current:
            helper_cmd = Command(
                argv=(
                    "install",
                    "-D",
                    "-m",
                    "0755",
                    f"{staging_root}/hammunition-devctl",
                    plan.polkit.helper_path,
                ),
                description=f"Install the power-control helper to {plan.polkit.helper_path}",
                requires_root=True,
            )
            built.append(helper_cmd)
        if not plan.polkit.policy_current:
            policy_cmd = Command(
                argv=(
                    "install",
                    "-D",
                    "-m",
                    "0644",
                    f"{staging_root}/devctl.policy",
                    plan.polkit.policy_path,
                ),
                description=(f"Install the polkit action authorising {plan.polkit.helper_path}"),
                requires_root=True,
            )
            built.append(policy_cmd)
        for group in plan.groups_to_add:
            built.append(
                Command(
                    argv=("gpasswd", "--add", user, group),
                    description=f"Add {user} to {group} for device access",
                    requires_root=True,
                )
            )
        time_cmds = (
            grant_commands(plan.time, staging_root, plan.polkit.helper_path)
            if plan.time is not None
            else []
        )
        built += time_cmds
        built += _gps_resume_commands(plan, staging_root)
        built += _devctl_export_commands(plan, staging_root)
        return built, helper_cmd, policy_cmd, time_cmds

    if not plan.rules_already_current:
        print(f"Will write {len(plan.rules_content.splitlines())} lines to {plan.rules_path}")
    else:
        print(f"Rules file at {plan.rules_path} is already current.")
    if not plan.polkit.helper_current:
        print(
            f"Will install the privileged helper to {plan.polkit.helper_path}\n"
            f"  It execs {plan.polkit.interpreter} as root, through a polkit "
            f"action any active local session may satisfy once and keep "
            f"authorised for a few minutes afterwards "
            f"(allow_active=auth_self_keep)."
        )
    if not plan.polkit.policy_current:
        print(f"Will install the polkit action to {plan.polkit.policy_path}")
    for group in plan.groups_to_add:
        print(f"Will add {user!r} to the {group!r} group")
    if plan.time is not None:
        for line in disclose(plan.time):
            print(line)
    for line in _geoclue_disclosure(args, geo):
        print(line)
    _disclose_gps_resume(plan)
    _disclose_devctl_export(plan)

    preview_commands, preview_helper, preview_policy, _preview_time = build_commands("<staging>")
    if geo is not None:
        preview_commands += geoclue.grant_commands(geo, "<staging>")
    installing_polkit = preview_helper is not None or preview_policy is not None
    """Whether this run installs *either* privileged artefact. Fix round 3:
    the helper and the policy are both routes to the same root-exec, and a
    policy-only apply (helper already current, only the action missing or
    stale) is the *worse* case, not a milder one -- installing the policy is
    exactly what turns an already-present helper into something an active
    session can authorise. Both refusal and confirmation below must gate on
    this, not on the helper alone."""
    euid = os.geteuid()
    print(f"\nCommands ({len(preview_commands)}):")
    for command in preview_commands:
        print(f"  # {command.description}")
        print(f"  $ {command.display(euid=euid)}")

    # Fix round 3: evaluated *before* the dry-run return, not after, so a
    # dry run on an unsafe tree reports the refusal a real run would give
    # rather than printing the full plan and exiting 0 -- CLAUDE.md's
    # "--dry-run must be complete and accurate, not approximate."
    #
    # Fix round 2: round 1 conflated "any local account can write it" with
    # "one specific non-root account owns it" into a single refusal, and a
    # devctl startup check that hard-refused on either broke the project's
    # own documented install -- a venv under $HOME is *always* non-root-owned.
    # Only the group/other-writable case is the actual escalation, and only
    # that one is refused outright, here at apply time.
    if installing_polkit and plan.polkit.must_refuse:
        print(
            f"error: refusing to install: {describe_refusal(plan.polkit.refusing_findings)}. "
            f"That is the escalation this project refuses outright rather than merely "
            f"confirms -- fix it, then re-run `hardware apply`.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE

    if args.dry_run:
        print("\nDry run: nothing above was executed.")
        return EXIT_OK

    # D-056's ruling: never refuse a wrapper that bakes in an interpreter or
    # package tree one non-root account owns — that is the normal shape of a
    # venv this project's own operator owns — but never let `--yes` wave it
    # through either (D-021). Asked at the keyboard, and only when either
    # privileged artefact is actually about to be (re)written. A yes here is
    # also the yes to the commands, so the operator is asked once, not twice.
    asked = installing_polkit and plan.polkit.needs_confirmation
    if asked and not _confirm_unsafe_interpreter(plan.polkit.confirmable_paths):
        print("Aborted: not confirmed. Nothing was changed.", file=sys.stderr)
        return EXIT_CONSENT

    if not asked and not args.yes and not _prompt("\nProceed with the commands above?"):
        print("Aborted. Nothing was changed.")
        return EXIT_OK

    # Only now, past every gate that could still end the run with nothing
    # written, does a staging directory actually get created. A fixed name
    # under the shared /tmp is not safe here: a privileged `install` command
    # reads back from this directory, and a predictable path lets a local
    # attacker pre-create it — /tmp's sticky bit does not protect a
    # subdirectory *they* own — and race our write, landing their own content
    # 0755 at the exact path polkit authorises. The D-031 readback further
    # down would only ever catch that after the bad file was already
    # installed as root. mkdtemp's name cannot be guessed in advance, and its
    # 0700 mode keeps every other account out entirely.
    staging_dir = Path(tempfile.mkdtemp(prefix="hammunition-hardware-"))
    try:
        commands, helper_command, policy_command, time_commands = build_commands(str(staging_dir))
        geo_commands = geoclue.grant_commands(geo, str(staging_dir)) if geo is not None else []
        commands += geo_commands

        if not plan.rules_already_current:
            staging = staging_dir / "udev-staging.rules"
            staging.write_text(plan.rules_content)
            os.chmod(staging, 0o644)
        if helper_command is not None:
            helper_staging = staging_dir / "hammunition-devctl"
            helper_staging.write_text(plan.polkit.helper_content)
            # Semgrep: a deliberate mode (0755/0644 on installed files and launchers, 0700 private); nothing group- or world-writable.
            # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
            os.chmod(helper_staging, 0o755)
        if policy_command is not None:
            policy_staging = staging_dir / "devctl.policy"
            policy_staging.write_text(plan.polkit.policy_content)
            os.chmod(policy_staging, 0o644)
        if plan.time is not None:
            stage_grants(plan.time, staging_dir)
        if geo is not None:
            geoclue.stage_grants(geo, staging_dir)
        resume_steps = _stage_gps_resume(plan, staging_dir)
        export_steps = _stage_devctl_export(plan, staging_dir)

        runner = SubprocessRunner()
        print("\nRunning:")
        for command in commands:
            print(f"  $ {command.display(euid=euid)}")
            result = runner.run(command)
            if result.returncode != 0:
                print(f"error: {result.stderr.strip()[:300]}", file=sys.stderr)
                print("Stopped. What ran above is applied; the rest is not.", file=sys.stderr)
                return EXIT_FAILED
            if any(command is step for step in time_commands):
                TransactionLog(owner=user).append(
                    {
                        "event": "time_grants",
                        "version": 1,
                        "description": command.description,
                        "argv": list(command.argv),
                    }
                )
            if any(command is step for step in geo_commands):
                TransactionLog(owner=user).append(
                    {
                        "event": "geoclue_files",
                        "version": 1,
                        "description": command.description,
                        "argv": list(command.argv),
                    }
                )
            if command in resume_steps:
                TransactionLog(owner=user).append(
                    {
                        "event": "gps_resume",
                        "version": 1,
                        "description": command.description,
                        "argv": list(command.argv),
                    }
                )
            if command in export_steps:
                TransactionLog(owner=user).append(
                    {
                        "event": "devctl_export",
                        "version": 1,
                        "description": command.description,
                        "argv": list(command.argv),
                    }
                )

            # Recorded per artefact as soon as its own command succeeds, not
            # batched to the end: a later command in this same run (the
            # policy install, a gpasswd call) can still fail without leaving
            # an unrecorded root-owned file that `unapply` would then report
            # as nothing to remove. Logged *before* the D-031 readback below,
            # not after: a file the install really wrote but whose content we
            # then failed to confirm is exactly the file `unapply` most needs
            # to be able to remove -- fix round 2's residual on F4.
            if command is helper_command:
                TransactionLog(owner=user).append(
                    {
                        "event": "hardware_artifacts",
                        "version": 1,
                        "files": [{"path": plan.polkit.helper_path, "mode": "0755"}],
                    }
                )
                try:
                    matches = (
                        Path(plan.polkit.helper_path).read_text() == plan.polkit.helper_content
                    )
                except OSError as exc:
                    print(
                        f"  unverified: could not read back {plan.polkit.helper_path}: {exc}",
                        file=sys.stderr,
                    )
                    return EXIT_FAILED
                if not matches:
                    print(
                        f"  unverified: {plan.polkit.helper_path} on disk does not match "
                        f"what we wrote",
                        file=sys.stderr,
                    )
                    return EXIT_FAILED
            if command is policy_command:
                TransactionLog(owner=user).append(
                    {
                        "event": "hardware_artifacts",
                        "version": 1,
                        "files": [{"path": plan.polkit.policy_path, "mode": "0644"}],
                    }
                )
                try:
                    matches = (
                        Path(plan.polkit.policy_path).read_text() == plan.polkit.policy_content
                    )
                except OSError as exc:
                    print(
                        f"  unverified: could not read back {plan.polkit.policy_path}: {exc}",
                        file=sys.stderr,
                    )
                    return EXIT_FAILED
                if not matches:
                    print(
                        f"  unverified: {plan.polkit.policy_path} on disk does not match "
                        f"what we wrote",
                        file=sys.stderr,
                    )
                    return EXIT_FAILED

        # D-031 for the rules file and group membership. The polkit
        # artefacts were already verified — and logged — individually above,
        # as soon as their own command ran.
        problems: list[str] = []
        if not plan.rules_already_current:
            try:
                if Path(plan.rules_path).read_text() != plan.rules_content:
                    problems.append(f"{plan.rules_path} on disk does not match what we wrote")
            except OSError as exc:
                problems.append(f"could not read back {plan.rules_path}: {exc}")
        after = user_groups(user)
        for group in plan.groups_to_add:
            if group not in after:
                problems.append(f"{user} is still not in {group}")
        if plan.time is not None:
            problems += verify_grants(plan.time)
        if geo is not None:
            problems += geoclue.verify_grants(geo)
        if plan.gps_resume is not None:
            problems += gps_resume.verify(plan.gps_resume)
        if plan.devctl_export is not None:
            problems += devctl_export.verify(plan.devctl_export)
        if problems:
            for problem in problems:
                print(f"  unverified: {problem}", file=sys.stderr)
            return EXIT_FAILED

        print("\nDone and verified.")
        if plan.groups_to_add:
            print(
                f"Group membership ({', '.join(plan.groups_to_add)}) takes effect at "
                f"your next login — log out and back in before expecting device access."
            )
        return EXIT_OK
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def cmd_hardware_unapply(args: argparse.Namespace) -> int:
    """Remove the privileged artefacts an apply installed, and nothing else.

    Not part of ``uninstall``: that command resolves names against the package
    and profile catalogs and there is no unit named ``hardware`` to give it
    (D-056). This removes exactly what the transaction log records *we* put
    there -- never a path we merely expect to exist, because a file at the
    helper's path that we did not write belongs to whoever did.

    **Narrower still: only a path this command owns.** ``--user`` lets an
    operator read *another* account's transaction log, which that account can
    append to freely -- so a log is data, not an instruction, and a `path`
    entry in it is honoured only when it is exactly the helper or the policy
    path. Anything else the log names is reported and skipped, never removed,
    because a root ``rm -f`` for an arbitrary string an unprivileged account
    once wrote into its own log file is not a promise this command makes.

    The device-access udev rules file (D-029) is deliberately left alone. It
    is declarative, it is harmless for a device that is not attached, and
    removing it would take away device access an operator is still using.
    Power control is the reversible part; permissions are not.

    **The kept-off rules file (D-056) is different and is removed here.** It
    exists only because ``park`` was told to keep a device parked, and its
    entries are power-control intent, not permissions -- so if it is present
    it is removed along with the helper and the policy action, and udev is
    told to reload so every device it was holding parked wakes from the next
    boot.

    **GPS time (D-058) is taken back by content, not by the log.** A file is
    removed only when it starts with the header Hammunition writes, ntp.conf's
    marked lines are restored byte for byte, and only Hammunition's block
    leaves ntpd's AppArmor local file. A hand-edited ntp.conf is refused
    before anything runs.
    """
    from hammunition.hardware import RULES_PATH
    from hammunition.hardware.power import KEPT_RULES

    user = operator(args)
    if not user:
        print("error: could not determine whose transaction log to read.", file=sys.stderr)
        return EXIT_FAILED

    import pwd as _pwd

    from hammunition.hardware.linger import LINGER_RECORD, read_record

    linger_record = read_record()
    linger_ours = linger_record is not None and linger_record.enabled_by_us
    # Act on the uid the record names, not on operator(args): the record is the
    # account Hammunition turned linger on for, which may not be whoever runs
    # unapply (review I3).
    linger_name: str | None = None
    if linger_ours and linger_record is not None:
        try:
            linger_name = _pwd.getpwuid(linger_record.uid).pw_name
        except KeyError:
            linger_ours = False

    kept_present = Path(KEPT_RULES).exists()
    from hammunition.gpstime import files as time_files
    from hammunition.gpstime.grants import (
        plan_time_removal,
        removal_commands,
        stage_removal,
        verify_removal,
    )
    from hammunition.gpstime.mode import TimeError

    try:
        removal = plan_time_removal()
    except TimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    time_present = not removal.is_empty
    from hammunition import geoclue

    geo_removal = geoclue.plan_geoclue_removal()
    geo_present = not geo_removal.is_empty
    resume_removal = gps_resume.plan_gps_resume_removal()
    resume_present = not resume_removal.is_empty
    export_removal = devctl_export.plan_removal()
    export_present = not export_removal.is_empty
    owned = {HELPER_PATH, POLICY_PATH}
    # D-056, amended 2026-10-02: where the installed helper answers --version it is
    # hammunition-tray's, whatever an older log says the engine once wrote there.
    handed_over = hardware_polkit.installed_helper_version(HELPER_PATH)
    recorded: list[str] = []
    skipped: list[str] = []
    left_to_tray: list[str] = []
    for entry in TransactionLog(owner=user).read():
        if entry.get("event") != "hardware_artifacts":
            continue
        files = entry.get("files")
        if not isinstance(files, list):
            # A malformed *known* event, not an unknown one -- state/log.py's
            # tolerance promise covers readers skipping events they don't
            # recognise, not a reader trusting the shape of one it does.
            continue
        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not isinstance(path, str):
                continue
            if path not in owned:
                if path not in recorded and path not in skipped:
                    skipped.append(path)
                continue
            if handed_over is not None:
                if path not in left_to_tray:
                    left_to_tray.append(path)
                continue
            if path not in recorded:
                recorded.append(path)

    if handed_over is not None:
        print(
            f"The privileged helper at {HELPER_PATH} is hammunition-tray's now (it answers "
            f"--version: {handed_over}), so this command leaves it and its polkit action "
            f"alone; removing them is the tray's own uninstall's job."
        )
        for path in left_to_tray:
            print(
                f"The log records {path} as written by an earlier apply; it is left in "
                f"place. (A polkit action this engine wrote because none existed stays "
                f"until it is removed by hand.)"
            )

    if (
        not recorded
        and not skipped
        and not kept_present
        and not time_present
        and not resume_present
        and not geo_present
        and not export_present
        and not linger_ours
        and left_to_tray
    ):
        print(
            "Nothing to remove: the only artefacts the log records are the helper and its "
            "polkit action, left to hammunition-tray (above)."
        )
        return EXIT_OK
    if (
        not recorded
        and not skipped
        and not kept_present
        and not time_present
        and not resume_present
        and not geo_present
        and not export_present
        and not linger_ours
    ):
        print(
            "Nothing to remove: the transaction log records no hardware artefacts "
            "installed by Hammunition for this user."
        )
        return EXIT_OK

    for path in skipped:
        print(
            f"Skipped: the log names {path!r}, which this command does not own "
            f"(only the power-control helper and its polkit action are ever removed)."
        )
    if (
        not recorded
        and not kept_present
        and not time_present
        and not resume_present
        and not geo_present
        and not export_present
        and not linger_ours
    ):
        print("Nothing to do: no artefact this command owns is recorded.")
        return EXIT_OK

    present = [p for p in recorded if Path(p).exists()]
    gone = [p for p in recorded if p not in present]
    for path in gone:
        print(f"Already absent: {path}")
    if (
        not present
        and not kept_present
        and not time_present
        and not resume_present
        and not geo_present
        and not export_present
        and not linger_ours
    ):
        print("Nothing to do: every recorded artefact is already gone.")
        return EXIT_OK

    # Linger (D-073 §5a): disable only the linger this record says Hammunition
    # turned on. Run directly as root; it does not depend on the helper being removed.
    commands = [
        Command(
            argv=("rm", "-f", path),
            description=f"Remove the power-control artefact at {path}",
            requires_root=True,
        )
        for path in present
    ]
    if linger_ours and linger_name is not None:
        commands.insert(
            0,
            Command(
                argv=("loginctl", "disable-linger", linger_name),
                description=f"Turn off linger for {linger_name} (Hammunition turned it on)",
                requires_root=True,
            ),
        )
        commands.append(
            Command(
                argv=("rm", "-f", str(LINGER_RECORD)),
                description="Remove Hammunition's linger record",
                requires_root=True,
            )
        )
    if kept_present:
        commands.append(
            Command(
                argv=("rm", "-f", KEPT_RULES),
                description="Remove the kept-off entries, so every device wakes from the next boot",
                requires_root=True,
            )
        )
        commands.append(
            Command(
                argv=("udevadm", "control", "--reload"),
                description="Reload udev's rules",
                requires_root=True,
            )
        )
        present.append(KEPT_RULES)

    time_preview = removal_commands(removal, "<staging>")
    resume_commands = gps_resume.removal_commands(resume_removal)
    geo_commands = geoclue.removal_commands(geo_removal)
    export_commands = devctl_export.removal_commands(export_removal)
    shown = [*commands, *time_preview, *resume_commands, *geo_commands, *export_commands]
    euid = os.geteuid()
    print(f"\nCommands ({len(shown)}):")
    for command in shown:
        print(f"  # {command.description}")
        print(f"  $ {command.display(euid=euid)}")
    if kept_present:
        print(
            f"\nRemoving {KEPT_RULES} removes the whole file, including any line in it "
            f"that Hammunition did not write."
        )
    if time_present:
        print(
            f"\nGPS time (D-058): {time_files.NTP_CONF}'s marked lines go back exactly as "
            f"they were before Hammunition edited them, and only Hammunition's block leaves "
            f"{time_files.APPARMOR_LOCAL}; the rest of that file stays."
        )
    if geo_present:
        print(
            f"\nGeoClue (D-069): only files that start with Hammunition's header are removed; "
            f"{geoclue.SOCKET_DIR} goes with `rmdir`, which refuses if anything but the "
            f"tether's socket is in it. Stop `hammunition maps gps-tether` first: GeoClue "
            f"loses its socket either way, and TCP 10110 keeps serving until you do."
        )
    print(
        f"\nThe device-access rules file, {RULES_PATH}, is not touched: it is "
        f"declarative, harmless for a device that is not attached, and removing it "
        f"would take away device access you are still using."
    )

    if args.dry_run:
        print("\nDry run: nothing above was executed.")
        return EXIT_OK
    if not args.yes and not _prompt("\nProceed with the commands above?"):
        print("Aborted. Nothing was changed.")
        return EXIT_OK

    staging_dir = Path(tempfile.mkdtemp(prefix="hammunition-unapply-")) if time_present else None
    try:
        to_run = list(commands)
        if staging_dir is not None:
            stage_removal(removal, staging_dir)
            to_run += removal_commands(removal, str(staging_dir))
        to_run += geo_commands
        to_run += resume_commands
        to_run += export_commands
        runner = SubprocessRunner()
        print("\nRunning:")
        for command in to_run:
            print(f"  $ {command.display(euid=euid)}")
            result = runner.run(command)
            if result.returncode != 0:
                print(f"error: {result.stderr.strip()[:300]}", file=sys.stderr)
                return EXIT_FAILED
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)

    # D-031: `rm` exiting 0 is not evidence the file is gone.
    problems = [f"{p} is still present" for p in present if Path(p).exists()]
    if time_present:
        problems += verify_removal(removal)
    if geo_present:
        problems += geoclue.verify_removal(geo_removal)
    problems += gps_resume.verify_removal(resume_removal)
    problems += devctl_export.verify_removal(export_removal)
    if problems:
        for problem in problems:
            print(f"  unverified: {problem}", file=sys.stderr)
        return EXIT_FAILED

    after = []
    if any(p != KEPT_RULES for p in present):
        after.append("`hammunition hardware apply` reinstalls the helper and its polkit action.")
    if kept_present:
        after.append("Kept entries come back with `hammunition hardware park`.")
    if time_present:
        after.append(
            "ntpsec runs on the package's own configuration; `hardware apply` restores GPS time."
        )
    if geo_present:
        after.append("GeoClue no longer reads the tether; `hardware apply` sets it up again.")
    if resume_present:
        after.append("`hardware apply` reinstalls the GPS resume step.")
    if export_present:
        after.append("`hardware apply` writes the helper's device and service lists again.")
    print("\nDone and verified. " + " ".join(after))
    return EXIT_OK


def _survey_parkables(args: argparse.Namespace) -> tuple[list[Parkable], list[tuple[str, str]]]:
    """What is parkable, read unprivileged. The same survey the helper does.

    Reading sysfs and the catalog needs no privilege; only *writing* does. So
    `hardware state` answers without a prompt, and `park`/`wake` can refuse a
    name before raising an authentication dialog for something that was never
    going to work.
    """
    from hammunition.hardware.detect import match_catalog, read_usb_bus
    from hammunition.hardware.power import parkable

    classes, devices = _load_hardware_catalog(args)
    entries: dict[str, DeviceClass | DeviceManifest] = {**classes, **devices}
    matches, _ = match_catalog(read_usb_bus(), entries)
    return parkable(matches, entries)


def _kept_split(
    found: list[Parkable], kept: list[KeptEntry]
) -> tuple[list[tuple[Parkable, bool]], list[KeptEntry]]:
    """Each attached parkable with whether it is kept, and the kept entries
    with nothing attached.

    A device whose values will not validate as an entry is reported not kept
    and matched against nothing -- one odd device never drops the rest, the
    way devctl's ``state`` treats it.
    """
    from hammunition.hardware.power import PowerError, kept_entry

    rows: list[tuple[Parkable, bool]] = []
    mine: list[KeptEntry] = []
    for p in found:
        try:
            entry = kept_entry(p)
        except PowerError:
            rows.append((p, False))
            continue
        mine.append(entry)
        rows.append((p, any(e.same_device(entry) for e in kept)))
    absent = [e for e in kept if not any(e.same_device(m) for m in mine)]
    return rows, absent


@envelope.json_capable()
def cmd_hardware_state(args: argparse.Namespace) -> int:
    """Which catalogued devices can be parked, which are parked now, and which
    are kept parked across reboots — attached or not."""
    from hammunition.hardware.power import PowerError, read_kept
    from hammunition.interface.hardware import build_hardware, render_hardware

    found, skipped = _survey_parkables(args)
    kept_error: str | None = None
    try:
        kept = read_kept()
    except (OSError, PowerError) as exc:
        kept_error, kept = str(exc), []

    rows, absent = _kept_split(sorted(found, key=lambda p: (p.name, p.address)), kept)
    doc = build_hardware(rows, absent, skipped, kept_error)
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_hardware(doc):
        print(line)
    return EXIT_OK


@envelope.json_capable()
def cmd_hardware_gps_resume_report(args: argparse.Namespace) -> int:
    """Is the GPS resume step current, what did its last run do, does the receiver
    deliver data. Reads only: no write, no keying, no power change (issue #177)."""
    from hammunition.hardware.gps_resume_report import gather
    from hammunition.interface.gps_resume import (
        build_gps_resume_report,
        render_gps_resume_report,
    )

    if not envelope.wanted(args):
        print(f"Watching gpsd for {args.data_window:g} s ...", file=sys.stderr)
    doc = build_gps_resume_report(gather(data_window=args.data_window))
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_gps_resume_report(doc):
        print(line)
    return EXIT_OK


def _helper_ready() -> int | None:
    """An exit code when the privileged helper cannot be reached, else None."""
    if not Path(HELPER_PATH).is_file():
        print(
            f"error: the privileged helper is not installed at {HELPER_PATH}.\n"
            f"`hammunition hardware apply` installs it, together with the polkit "
            f"action that authorises it.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    if shutil.which("pkexec") is None:
        print(
            "error: pkexec is not on PATH, so the privileged helper cannot be "
            "authorised. It comes from the `polkit` package (`pkexec` is in "
            "`policykit-1` on Debian-family targets). Without it, park, wake and "
            "time mode have no way to escalate; `hammunition hardware state` and "
            "`hammunition time` still work, because reading needs no privilege.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    return None


def _run_helper(command: Command, done: str) -> int:
    """Run a pkexec call and map its exit to ours: 126/127 is a dismissed prompt."""
    try:
        result = SubprocessRunner().run(command)
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if result.returncode in (126, 127):
        print("The authentication prompt was dismissed; nothing was changed.", file=sys.stderr)
        return EXIT_CONSENT
    if result.returncode == EXIT_UNPLANNABLE:
        print(result.stderr.strip() or "the helper refused the request", file=sys.stderr)
        return EXIT_UNPLANNABLE
    if result.returncode != 0:
        print(result.stderr.strip()[:400] or f"helper exited {result.returncode}", file=sys.stderr)
        return EXIT_FAILED
    print(f"\n{done}")
    return EXIT_OK


def _power_verb(args: argparse.Namespace, verb: str) -> int:
    """Disclose the privileged call and every write it will cause, then run it."""
    from hammunition.hardware.power import (
        KEPT_RULES,
        PowerError,
        plan_forget,
        plan_park,
        plan_wake,
        read_kept,
    )

    ready = _helper_ready()
    if ready is not None:
        return ready

    found, skipped = _survey_parkables(args)
    for unit, why in skipped:
        print(f"note: {unit} is not parkable right now — {why}", file=sys.stderr)
    from hammunition.cli.devctl import attached_named, resolve_kept
    from hammunition.cli.devctl import resolve as resolve_parkable

    keep = not getattr(args, "until_reboot", False)
    absent = False
    try:
        try:
            target = resolve_parkable(args.name, found)
        except PowerError:
            if verb != "wake" or attached_named(args.name, found):
                raise
            entry = resolve_kept(args.name, read_kept())
            plan = plan_forget(entry)
            absent = True
            label, address, summary = entry.name, entry.address, "not attached"
        else:
            plan = plan_park(target, keep=keep) if verb == "park" else plan_wake(target)
            label, address, summary = target.name, target.address, target.summary
    except PowerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE

    argv = ["pkexec", HELPER_PATH, verb]
    if verb == "park" and not keep:
        argv.append("--until-reboot")
    argv.append(f"{label}@{address}")
    command = Command(argv=tuple(argv), description=f"{verb.capitalize()} {label} at {address}")
    print(f"{verb.capitalize()}ing {label} ({summary}) at {address}\n")
    if plan.writes:
        print("Writes this will cause:")
        for write in plan.writes:
            print(f"  {write.path} <- {write.value}")
    if plan.keep is not None:
        print(f"\nIt stays parked across reboots. Added to {KEPT_RULES}:")
        print(f"  {plan.keep.rule()}")
    elif verb == "park":
        print(
            f"\nA reboot wakes it (--until-reboot): no kept entry is written, and "
            f"any kept entry for it is removed from {KEPT_RULES}."
        )
    elif plan.forget is not None:
        note = "It is not attached, so this will only" if absent else "This will also"
        print(f"\n{note} remove its kept entry from {KEPT_RULES}, if present.")
    print(f"\n  # {command.description}\n  $ {command.display()}")

    if args.dry_run:
        print("\nDry run: nothing above was executed.")
        return EXIT_OK

    return _run_helper(
        command, f"Done and verified. `hammunition hardware state` shows {label} now."
    )


def cmd_hardware_park(args: argparse.Namespace) -> int:
    """Detach a device and keep it parked across reboots, unless --until-reboot."""
    return _power_verb(args, "park")


def cmd_hardware_wake(args: argparse.Namespace) -> int:
    """Bring a parked device back."""
    return _power_verb(args, "wake")


# ---------------------------------------------------------------------------
# time — GPS time (D-058)
# ---------------------------------------------------------------------------


def cmd_time(args: argparse.Namespace) -> int:
    """What the clock follows now, and the mode. Reads only; needs no privilege."""
    from hammunition.gpstime import state as time_state

    found, _ = _survey_parkables(args)
    for line in time_state.describe(time_state.gather(gps=time_state.gps_from(found))):
        print(line)
    return EXIT_OK


def cmd_time_measure(args: argparse.Namespace) -> int:
    """Sample ntpd for N minutes and say whether the GPS took over (issue #310). Reads only."""
    from hammunition.gpstime import measure as time_measure
    from hammunition.gpstime.state import ntpsec_installed, run_ntpq

    if args.minutes <= 0 or args.interval <= 0:
        print("error: --minutes and --interval must be positive", file=sys.stderr)
        return EXIT_UNPLANNABLE
    if not ntpsec_installed():
        print(
            "error: ntpsec is not installed, so there is no `ntpq` to sample and nothing "
            "follows a GPS here (D-058).",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    print(
        f"Sampling `ntpq -pn` every {args.interval:g} s for {args.minutes:g} min "
        "(Ctrl-C ends it early). Read-only."
    )
    samples = time_measure.measure(
        lambda: run_ntpq(("-pn",)), minutes=args.minutes, interval=args.interval
    )
    code = EXIT_OK
    if samples is None:
        print("ntpq never answered: is ntpd running? (`systemctl status ntpsec`)")
        code = EXIT_FAILED
    else:
        print("")
        for line in time_measure.verdict(time_measure.summarise(samples)):
            print(line)
    if args.pps:
        print(f"\nPPS: `ppstest /dev/pps0` for {args.pps_seconds:g} s ...")
        pps = time_measure.run_ppstest(args.pps_seconds)
        print(f"  /dev/pps0 {'exists' if pps.device_present else 'does not exist'}")
        for entry in pps.devices:
            print(f"  /sys/class/pps: {entry}")
        if pps.error is not None:
            print(f"  {pps.error}")
        else:
            print(f"  {pps.pulses} pulse(s) in {pps.seconds:g} s: the PPS source is real.")
        for line in pps.lines:
            print(f"    {line}")
    return code


def cmd_time_mode(args: argparse.Namespace) -> int:
    """Disclose the three files a mode change writes and the restart, then ask the helper."""
    from hammunition.gpstime import files
    from hammunition.gpstime.mode import GPS_MODES, TimeError, as_mode
    from hammunition.gpstime.ntpconf import mode_writes
    from hammunition.gpstime.state import gps_from, ntpsec_installed

    ready = _helper_ready()
    if ready is not None:
        return ready
    mode = as_mode(args.mode)
    if not ntpsec_installed():
        print(
            f"error: ntpsec is not installed ({files.NTPD} or {files.NTP_CONF} is missing), "
            f"and only ntpsec can take time from a GPS here (D-058). This target's time "
            f"daemon is left as it is.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    current = Path(files.NTP_CONF).read_text(encoding="utf-8")
    try:
        writes = mode_writes(current, mode)
    except TimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE

    print(f"Setting the time mode to {mode}\n")
    print("Writes this will cause, as root, through the helper:")
    for line in writes:
        print(line)

    found, _ = _survey_parkables(args)
    gps = gps_from(found)
    if mode in GPS_MODES and gps == "absent":
        print("\nNo GPS receiver is attached: the mode is recorded and takes effect when one is.")
    elif mode in GPS_MODES and gps == "parked":
        print("\nThe GPS receiver is parked: GPS time stays off until it is woken.")

    command = Command(
        argv=("pkexec", HELPER_PATH, "time", "mode", mode),
        description=f"Set the time mode to {mode}",
    )
    print(f"\n  # {command.description}\n  $ {command.display()}")
    if args.dry_run:
        print("\nDry run: nothing above was executed.")
        return EXIT_OK
    return _run_helper(
        command, "Done and verified. `hammunition time` shows what the clock follows now."
    )


# ---------------------------------------------------------------------------
# services — the helper's service list (D-056, amended 2026-10-02)
# ---------------------------------------------------------------------------


def _services_helper_missing() -> int | None:
    """An exit code when the installed helper cannot be asked, else None."""
    if Path(HELPER_PATH).is_file():
        return None
    print(
        f"error: the privileged helper is not installed at {HELPER_PATH}. It is "
        f"hammunition-tray's: `hammunition install hammunition-tray` (or "
        f"`hammunition install hammunition-tray-qt` outside Plasma) carries it. "
        f"`hammunition hardware apply` installs the engine's older copy, which has no "
        f"`services` verb.",
        file=sys.stderr,
    )
    return EXIT_UNPLANNABLE


def _read_services() -> tuple[ServicesDocument | None, int]:
    """Ask the installed helper for its services document: unprivileged, one argv.

    Returns the document, or None and the exit code to leave with. The engine
    never asks systemd anything: the helper is the one that does.
    """
    from hammunition.interface.services import ServicesError, parse_helper_services

    missing = _services_helper_missing()
    if missing is not None:
        return None, missing
    command = Command(
        argv=(HELPER_PATH, "services", "state"),
        description="Ask the helper which services it controls and what each is doing",
    )
    try:
        result = SubprocessRunner().run(command)
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None, EXIT_FAILED
    if result.returncode == EXIT_UNPLANNABLE:
        print(
            "error: the installed helper has no `services` verb (it predates contract 1): "
            "update hammunition-tray.",
            file=sys.stderr,
        )
        return None, EXIT_UNPLANNABLE
    if result.returncode != 0:
        print(
            f"error: the helper exited {result.returncode}: "
            f"{result.stderr.strip()[:300] or 'no message'}",
            file=sys.stderr,
        )
        return None, EXIT_FAILED
    try:
        return parse_helper_services(result.stdout), EXIT_OK
    except ServicesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None, EXIT_FAILED


def cmd_console(args: argparse.Namespace) -> int:
    """`hammunition console`: the TUI. It lives in hammunition.console and is
    imported here, not at the top of the module, so the engine never needs urwid
    (the console imports it only when it is about to draw). It drives the engine
    through the --json documents (D-059)."""
    from hammunition.console.__main__ import main as console_main

    return console_main(list(getattr(args, "console_args", [])))


@envelope.json_capable()
def cmd_services(args: argparse.Namespace) -> int:
    """The services the helper may start, stop, enable and disable, and what each is doing."""
    from hammunition.interface.services import render_services

    doc, code = _read_services()
    if doc is None:
        return code
    if envelope.wanted(args):
        envelope.emit(doc)
        return EXIT_OK
    for line in render_services(doc):
        print(line)
    return EXIT_OK


def _service_effect(verb: str, row: ServiceView, unit_journal: str) -> tuple[bool, str]:
    """D-031: whether the unit now is what the verb asked, read back from the helper.

    Returns (ok, message). A start that ends ``inactive`` is a note, not a failure:
    a one-shot unit runs and exits.
    """
    if verb == "start":
        if row.active in ("active", "activating"):
            return True, f"{row.unit} is {row.active}."
        if row.active == "failed":
            return False, f"{row.unit} is failed after the start; `{unit_journal}` says why."
        return True, (
            f"{row.unit} is {row.active} after the start. A one-shot unit runs and exits, "
            f"which reads this way; `{unit_journal}` shows what it did."
        )
    if verb == "stop":
        if row.active in ("inactive", "failed"):
            return True, f"{row.unit} is {row.active}."
        return False, f"{row.unit} is still {row.active} after the stop."
    want = "enabled" if verb == "enable" else "disabled"
    if row.enabled == want:
        return True, f"{row.unit} is {row.enabled}."
    return False, f"{row.unit} reads {row.enabled}, not {want}, after the {verb}."


def cmd_services_act(args: argparse.Namespace) -> int:
    """Start, stop, enable or disable one service, through the helper (D-056 amended)."""
    verb: str = args.action
    doc, code = _read_services()
    if doc is None:
        return code
    row = next((s for s in doc.services if s.name == args.name), None)
    if row is None:
        known = ", ".join(s.name for s in doc.services) or "none"
        print(
            f"error: {args.name!r} is not a service the helper controls. Its list: {known}.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    if row.enabled == "not-found":
        print(
            f"error: {row.unit} ({row.name}) is not installed, so there is nothing to {verb}.",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    already = {
        "start": row.active in ("active", "activating"),
        "stop": row.active == "inactive",
        "enable": row.enabled == "enabled",
        "disable": row.enabled == "disabled",
    }[verb]
    if already:
        state = row.active if verb in ("start", "stop") else row.enabled
        print(f"{row.name} ({row.unit}) is already {state}; nothing to do.")
        return EXIT_OK
    if row.root and shutil.which("pkexec") is None:
        print(
            "error: pkexec is not on PATH, so a system service cannot be changed from here. "
            "It comes from the `polkit` package (`policykit-1` on Debian-family targets).",
            file=sys.stderr,
        )
        return EXIT_UNPLANNABLE
    argv: tuple[str, ...] = (HELPER_PATH, "services", verb, row.name)
    if row.root:
        argv = ("pkexec", *argv)
    command = Command(argv=argv, description=f"{verb.capitalize()} {row.unit} ({row.scope} scope)")
    when = "now" if verb in ("start", "stop") else ("at boot" if row.root else "at login")
    print(f"{verb.capitalize()}ing {row.name}: {row.unit}, {row.scope} scope, {when}\n")
    print(
        "The helper carries the request out; this command never runs systemctl itself, "
        "and passes a name, never a unit."
    )
    print(f"\n  # {command.description}\n  $ {command.display()}")
    if args.dry_run:
        print("\nDry run: nothing above was executed.")
        return EXIT_OK
    try:
        result = SubprocessRunner().run(command)
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if row.root and result.returncode in (126, 127):
        # pkexec's own codes for a dismissed or denied prompt. A user-scope call has
        # no prompt, and 127 there is a missing interpreter: not the same story.
        print("The authentication prompt was dismissed; nothing was changed.", file=sys.stderr)
        return EXIT_CONSENT
    if result.returncode == EXIT_UNPLANNABLE:
        print(result.stderr.strip() or "the helper refused the request", file=sys.stderr)
        return EXIT_UNPLANNABLE
    if result.returncode != 0:
        print(result.stderr.strip()[:400] or f"helper exited {result.returncode}", file=sys.stderr)
        return EXIT_FAILED
    after, code = _read_services()
    now = next((s for s in after.services if s.name == row.name), None) if after else None
    if now is None:
        print(f"unverified: could not read {row.name} back from the helper.", file=sys.stderr)
        return EXIT_FAILED
    journal = ("journalctl " if row.root else "journalctl --user ") + f"-u {row.unit}"
    ok, message = _service_effect(verb, now, journal)
    if not ok:
        print(f"unverified: {message}", file=sys.stderr)
        return EXIT_FAILED
    print(f"\nDone and verified: {message}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# doctor — a read-only health check
# ---------------------------------------------------------------------------


def _user_unit_state(name: str) -> str:
    """``absent``/``disabled``/``active``/``failed`` for a user unit, read-only."""
    runner = SubprocessRunner()

    def ask(verb: str) -> tuple[int, str]:
        try:
            r = runner.run(
                Command(
                    argv=("systemctl", "--user", verb, f"{name}.service"),
                    description=f"Read the {name} unit's {verb}",
                )
            )
        except BackendError:
            return 1, ""
        return r.returncode, r.stdout.strip()

    enabled_rc, enabled = ask("is-enabled")
    if enabled in ("not-found", "") and enabled_rc != 0:
        return "absent"
    _active_rc, active = ask("is-active")
    if active == "failed":
        return "failed"
    if active == "active":
        return "active"
    if enabled == "enabled":
        return "disabled" if active != "active" else "active"
    return "disabled"


def _dump_state_answers() -> bool:
    """Whether rigctld answers \\dump_state on 127.0.0.1:4532. Read-only, never keys."""
    import socket

    from hammunition.rig import parse_dump_state_model

    try:
        conn = socket.create_connection(("127.0.0.1", 4532), timeout=1.0)
    except OSError:
        return False
    try:
        conn.sendall(b"\\dump_state\n")
        conn.settimeout(1.0)
        reply = conn.recv(4096)
    except OSError:
        return False
    finally:
        conn.close()
    return parse_dump_state_model(reply.decode(errors="replace")) is not None


def _linger_state_for_doctor(user: str) -> str | None:
    from hammunition.hardware.linger import read_record

    try:
        result = SubprocessRunner().run(
            Command(
                argv=("loginctl", "show-user", user or "", "--property=Linger", "--value"),
                description="Read linger state",
            )
        )
    except BackendError:
        return None
    if not result.ok:
        return None
    on = result.stdout.strip().lower() in ("yes", "1", "true")
    if not on:
        return "off"
    record = read_record()
    return "ours" if record is not None and record.enabled_by_us else "theirs"


def _port_loopback_only(port: int) -> bool | None:
    """True when *port* is bound only to 127.0.0.1/::1, read from /proc/net/tcp.

    None when the files cannot be read. A listener (state 0A) on any other
    local address is a transmitter reachable off-machine (D-073 §11)."""
    return bound_to_loopback_only(port)


def _rigctld_args_match(station: Station) -> bool | None:
    """Whether a running rigctld's arguments match the station, from /proc.

    None when no rigctld is found or /proc cannot be read. Compares the device
    and, for a CAT rig, the speed — the values a wrong reinstall would leave
    stale (D-073 §9)."""
    try:
        pids = [p for p in Path("/proc").iterdir() if p.name.isdigit()]
    except OSError:
        return None
    for proc in pids:
        try:
            cmdline = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if not cmdline or not cmdline[0].endswith(b"rigctld"):
            continue
        args = [a.decode(errors="replace") for a in cmdline if a]
        if "-t" not in args or "4632" not in args:
            continue  # not our rig service's rigctld
        device_ok = station.rig_device is None or station.rig_device in args
        baud_ok = station.rig_baud is None or str(station.rig_baud) in args
        return device_ok and baud_ok
    return None


def _gather_rig_status(
    args: argparse.Namespace,
    station: Station,
    devices: dict[str, DeviceClass] | dict[str, DeviceManifest] | Mapping[str, object],
) -> RigStatus | None:
    """Read-only facts about the rig service for doctor (D-073 §9). Never keys."""
    from hammunition.rig import RigError, resolve_rig

    if station.rig is None:
        return RigStatus(configured=False)
    kind: str | None = None
    uncatalogued = False
    try:
        res = resolve_rig(station.rig, dict(devices))  # type: ignore[arg-type]
        kind, uncatalogued = res.kind, res.uncatalogued
    except RigError:
        kind = None
    needed = ("rig_device", "rig_baud") if kind == "cat" else ("rig_device", "rig_ptt_line")
    missing = tuple(v for v in needed if getattr(station, v) in (None, "")) if kind else ()
    owner = station.rig_owner or "rigctld"
    vox = station.rig_ptt_line == "vox"
    runs_service = owner != "flrig" and not (kind == "ptt_only" and vox)
    state = _user_unit_state("hammunition-rigctld") if runs_service else None
    proxy_state = _user_unit_state("hammunition-rig-proxy") if runs_service else None
    answering = _dump_state_answers() if state == "active" else None
    loopback_only = _port_loopback_only(4532) if state == "active" else None
    args_match = _rigctld_args_match(station) if state == "active" else None
    device_present = Path(station.rig_device).exists() if station.rig_device is not None else None
    linger = _linger_state_for_doctor(operator(args))
    return RigStatus(
        configured=True,
        missing=missing,
        uncatalogued=uncatalogued,
        kind=kind,
        owner=owner,
        vox=vox,
        service_state=state,
        proxy_state=proxy_state,
        answering=answering,
        loopback_only=loopback_only,
        args_match=args_match,
        device_present=device_present,
        linger=linger,
    )


def _geoclue_state_for_doctor(args: argparse.Namespace) -> GeoClueState | None:
    """GeoClue's files, directory and agent for `doctor` (D-069), read-only.
    The agent is asked of this session's bus, so not as root."""
    from hammunition import geoclue

    try:
        return geoclue.read_state(operator(args), ask_agent=os.geteuid() != 0)
    except OSError:
        return None


def _doctor_gps_resume(args: argparse.Namespace) -> gps_resume.ResumeStatus | None:
    """Issue #177: the resume step's state, when a GPS receiver is attached
    (parked or awake) and gpsd is installed; otherwise None, and no check."""
    from hammunition.gpstime.state import gps_from

    try:
        found, _ = _survey_parkables(args)
    except (OSError, CatalogError, SystemExit):
        return None
    if gps_from(found) == "absent" or not Path(gps_resume.GPSD).exists():
        return None
    return gps_resume.status()


def _security_key_probe(argv: tuple[str, ...]) -> CommandResult:
    """Run one read-only security-key probe argv, bounded and never raising.

    A12's own subprocess adapter, separate from :class:`SubprocessRunner`:
    these commands (`systemctl is-active pcscd`, `ssh -V`, `fido2-token -L`,
    `fido2-token -I <dev>`, `opensc-tool --list-readers`, `opensc-tool
    --reader N --name`) are plain listings and liveness probes, never a PIN
    generation, a touch-signing request, or a login change, and this adapter
    never asks for one. It is timed out at 5 seconds and caps retained output
    at 64 KiB across both streams combined, so a hung or flooding helper
    cannot stall or exhaust `doctor`. A missing program or a timeout is
    reported as a non-zero :class:`CommandResult`, never an exception —
    :func:`hammunition.security_keys.probe_security_keys` always gets a
    result to read.
    """
    import os
    import selectors
    import subprocess
    import time

    cap = 64 * 1024
    data: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return CommandResult(argv=argv, returncode=127, stdout="", stderr=str(exc))
    assert process.stdout is not None and process.stderr is not None
    failure: str | None = None
    deadline = time.monotonic() + 5
    try:
        with selectors.DefaultSelector() as selector:
            for label, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "security-key probe timed out after 5 seconds"
                    break
                for event, _mask in selector.select(remaining):
                    used = sum(len(value) for value in data.values())
                    chunk = os.read(event.fd, min(4096, cap - used + 1))
                    if not chunk:
                        selector.unregister(event.fileobj)
                        continue
                    if len(chunk) > cap - used:
                        failure = "security-key probe exceeded 64 KiB output"
                        break
                    data[str(event.data)].extend(chunk)
                if failure is not None:
                    break
            if failure is None:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure = str(exc)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()
    return CommandResult(
        argv=argv,
        returncode=127 if failure is not None else process.returncode,
        stdout=data["stdout"].decode("utf-8", errors="replace"),
        stderr=failure or data["stderr"].decode("utf-8", errors="replace"),
    )


def _security_keys_for_doctor(
    args: argparse.Namespace,
) -> tuple[SecurityKeyState | None, Check | None]:
    """Gather :class:`SecurityKeyState` for `doctor` (A12), read-only.

    Enrolled keys come from the owner-aware mirror store, already classified
    — this is the one place `doctor` touches it, and it never refreshes trust
    or the accepted serial, and never signs a test message. A corrupt or
    unreadable store is reported as one warn check naming the remedy, not a
    crash; no Bunker is enrolled at all is not an error (``enrolled`` stays
    empty). Root cannot measure the ordinary operator's device access, so the
    probe itself is told as much (D-056's `hardware apply` is the model for
    this euid distinction).
    """
    from hammunition.security_keys import probe_security_keys
    from hammunition.signers import SignerError, load_mirror

    user = operator(args)
    enrolled: tuple[KeyStrength, ...] = ()
    error: Check | None = None
    try:
        mirror_state = load_mirror(owner=user or None)
        if mirror_state is not None:
            enrolled = tuple(classify(k.public_key) for k in mirror_state.keys)
    except (SignerError, ValueError) as exc:
        error = Check(
            "security keys",
            "warn",
            f"the Bunker mirror state could not be read: {exc}",
            "hammunition mirror status",
            ["hammunition", "mirror", "status"],
        )
    state = probe_security_keys(
        _security_key_probe, as_operator=os.geteuid() != 0, enrolled=enrolled
    )
    return state, error


@envelope.json_capable()
def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what is ready and what is not yet set up. Changes nothing."""
    import shutil

    from hammunition.doctor import ROUTINO_TRANSLATIONS, run_checks, writable_or_creatable
    from hammunition.hardware import RULES_PATH, plan_hardware, rules_file
    from hammunition.manifest.load import load_hardware
    from hammunition.paths import state_dir
    from hammunition.selfupdate import version_pair as selfupdate_versions

    classes: dict[str, DeviceClass] = {}
    devices: dict[str, DeviceManifest] = {}

    user = operator(args)

    try:
        target = Target.detect()
        target_describe: str | None = target.describe()
        is_debian = target.is_debian_family
    except DetectionError:
        target_describe, is_debian = None, False

    catalog_counts: tuple[int, int] | None = None
    needed_groups: list[str] = []
    attached_recognised = 0
    try:
        catalog_root = find_catalog(args.catalog)
        packages, profiles = load_all(catalog_root)
        catalog_counts = (len(packages), len(profiles))
        classes, devices = load_hardware(catalog_root / "hardware")
        groups_now = user_groups(user) if user else frozenset()
        hw = plan_hardware(classes, devices, user=user or "", user_groups_now=groups_now)
        needed_groups = sorted(set(hw.groups_to_add) | set(hw.groups_present))
        attached_recognised = len(hw.detected)
    except (SystemExit, CatalogError, OSError):
        groups_now = frozenset()

    # python3 -m venv works iff the venv module imports and ensurepip is present.
    try:
        import ensurepip  # noqa: F401
        import venv  # noqa: F401

        has_venv_module = True
    except ImportError:
        has_venv_module = False

    local_bin = str(user_bin_dir(user or None))
    path_has_local_bin = local_bin in os.environ.get("PATH", "").split(os.pathsep)

    tools = {
        "cc": bool(shutil.which("cc") or shutil.which("gcc")),
        "git": bool(shutil.which("git")),
    }

    from hammunition.station import load_station

    try:
        station = load_station(owner=user or None)
        station_set = station.callsign is not None
    except Exception:
        station_set = False

    rules_applied = False
    rules_file_path = Path(RULES_PATH)
    if rules_file_path.exists() and (classes or devices):
        expected, _ = rules_file([*classes.values(), *devices.values()])
        try:
            rules_applied = rules_file_path.read_text() == expected
        except OSError:
            rules_applied = True  # present but unreadable-as-text: it exists

    log_dir = state_dir(user or None)
    log_dir_writable = writable_or_creatable(log_dir)

    from hammunition import runlog

    runs_dir = runlog.logs_dir(user or None)
    recorded_runs = runlog.list_runs(runs_dir)
    run_logs_summary: tuple[int, int, str, str] | None = None
    if recorded_runs:
        newest = recorded_runs[0]
        run_logs_summary = (
            len(recorded_runs),
            runlog.total_size(runs_dir),
            f"{newest.command}, {newest.started[:19].replace('T', ' ')} UTC",
            newest.result,
        )

    # This checkout's entry point: src/hammunition/cli/main.py -> the checkout
    # root is three parents above the package. Resolved, so a ~/.local/bin
    # link to it compares equal (D-059).
    checkout = Path(__file__).resolve().parents[3]
    engine_expected = str((checkout / ".venv" / "bin" / "hammunition").resolve())
    found_engine = shutil.which("hammunition")
    engine_on_path = str(Path(found_engine).resolve()) if found_engine else None
    # Where it was found, unresolved, and whether that is ~/.local/bin, so doctor
    # offers the relink only for our own link there and never for a file it
    # would clobber or one that shadows it from earlier on PATH.
    engine_found_in_local_bin = (
        found_engine is not None
        and Path(found_engine).parent.resolve() == Path(local_bin).resolve()
    )
    engine_found_link = (
        os.readlink(found_engine) if found_engine and Path(found_engine).is_symlink() else None
    )

    # bootstrap's own link in ~/.local/bin, whether or not that is on PATH yet:
    # a fresh account has the link before its next login puts the dir on PATH.
    linked = Path(local_bin) / "hammunition"
    engine_linked_in_local_bin = linked.is_symlink() and str(linked.resolve()) == engine_expected

    kept_attached: tuple[str, ...] = ()
    kept_absent: tuple[str, ...] = ()
    try:
        from hammunition.hardware.power import PowerError, read_kept

        found, _skipped = _survey_parkables(args)
        kept = read_kept()
        _rows, absent = _kept_split(found, kept)
        attached_now = [f"{e.name}@{e.address}" for e in kept if e not in absent]
        absent_now = [f"{e.name}@{e.address}" for e in absent]
        kept_attached = tuple(attached_now)
        kept_absent = tuple(absent_now)
    except (OSError, PowerError, CatalogError, SystemExit):
        kept_attached, kept_absent = (), ()

    time_state = None
    try:
        from hammunition.gpstime.state import gather, gps_from

        found_now, _ = _survey_parkables(args)
        time_state = gather(gps=gps_from(found_now))
    except (OSError, CatalogError, SystemExit):
        time_state = None

    geoclue_state = _geoclue_state_for_doctor(args)
    gps_resume_state = _doctor_gps_resume(args)

    from hammunition.launchers import survey_engine_launchers, survey_shadowing_launchers

    # Issue #145: every generated launcher that runs the engine can reach it.
    engine_launchers = survey_engine_launchers(Path(local_bin))
    # Issue #174: and none is named like a binary the PATH already has.
    shadowing_launchers = survey_shadowing_launchers(Path(local_bin))

    sessions = scan_sessions()
    rig_status = _gather_rig_status(args, station, devices)
    security_keys_state, security_keys_error = _security_keys_for_doctor(args)
    checks = run_checks(
        target_describe=target_describe,
        is_debian_family=is_debian,
        catalog_counts=catalog_counts,
        has_venv_module=has_venv_module,
        path_has_local_bin=path_has_local_bin,
        tools=tools,
        groups_now=groups_now,
        needed_groups=needed_groups,
        station_set=station_set,
        rules_applied=rules_applied,
        attached_recognised=attached_recognised,
        log_dir_writable=log_dir_writable,
        run_logs=run_logs_summary,
        engine_on_path=engine_on_path,
        engine_expected=engine_expected,
        engine_found=found_engine,
        engine_found_in_local_bin=engine_found_in_local_bin,
        engine_found_link=engine_found_link,
        engine_linked_in_local_bin=engine_linked_in_local_bin,
        engine_versions=selfupdate_versions(),
        kept_attached=kept_attached,
        kept_absent=kept_absent,
        desktops_installed=sessions.desktops,
        sessions_unrecognised=sessions.unrecognised,
        desktop_current=current_desktop(os.environ),
        qmapshack_without_translations=(
            shutil.which("qmapshack") is not None and not Path(ROUTINO_TRANSLATIONS).is_file()
        ),
        time_state=time_state,
        gps_resume=gps_resume_state,
        launchers_ok=engine_launchers.ok,
        launchers_bare=engine_launchers.bare,
        launchers_broken=engine_launchers.broken,
        launchers_shadowing=shadowing_launchers,
        rig=rig_status,
        geoclue_state=geoclue_state,
        security_keys=security_keys_state,
    )
    if security_keys_error is not None:
        checks.append(security_keys_error)

    from hammunition.interface.doctor import build_doctor, render_doctor

    doc = build_doctor(checks)
    if envelope.wanted(args):
        envelope.emit(doc)
    else:
        for line in render_doctor(doc):
            print(line)
    return EXIT_FAILED if doc.fails else EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _engine_version() -> str:
    """What ``--version`` prints: the checkout's version, and both when the venv lags (#311)."""
    from hammunition.selfupdate import version_line

    return version_line()


def _add_json_flag(parser: argparse.ArgumentParser, *, top: bool) -> None:
    """``--json`` on the top-level parser and on every subcommand, recursively.

    Accepted on both sides of the verb, ``hammunition --json status`` and
    ``hammunition status --json``. A subcommand's copy defaults to SUPPRESS,
    so an absent flag after the verb does not overwrite one given before it.
    Walked rather than listed, so a verb added later carries it without
    anyone remembering to (D-059).
    """
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if top else argparse.SUPPRESS,
        help="print one JSON document on stdout instead of text (docs/reference/json-interface.md)",
    )
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _add_json_flag(child, top=False)


def _disallow_abbrev(parser: argparse.ArgumentParser) -> None:
    """No abbreviated long option is ever accepted, here or on any
    subcommand, recursively.  D-059 (review round 1, Important 2).

    ``allow_abbrev`` defaults to True, so without this `--js` or `--dr`
    would silently stand in for `--json` or `--dry-run`. That is a real gate
    to defeat: `main()` separately routes on the *parsed* value of
    ``args.json`` rather than a text scan of argv, but a CLI that guards a
    real install and every consent gate behind an exact flag should not
    depend on that alone. ``allow_abbrev`` is a plain instance attribute
    argparse reads at parse time, so setting it after construction (as every
    subparser here is already built) works the same as passing it in.
    """
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _disallow_abbrev(child)


def _json_requested(arguments: list[str]) -> bool:
    """Whether ``--json`` was actually given -- immune to abbreviation, and
    to every other flag this CLI defines -- with no side effect (no help
    text, no version, no exit).  D-059 (review round 1, Important 2).

    Used by `main()` to route before touching stdout, so it must not risk
    printing anything: a full parse of ``--help``/``--version`` writes to
    stdout before this function could know whether to redirect it. A tiny
    parser that knows only ``--json`` (`add_help=False`, so `-h`/`--help`
    is not even registered; `allow_abbrev=False`, so `--js` matches
    nothing) is the actual parsed answer to "did the operator ask for
    JSON", not a guess from scanning the raw tokens the old code used.
    """
    probe = _Probe(add_help=False, allow_abbrev=False)
    probe.add_argument("--json", action="store_true", default=False)
    try:
        parsed, _ = probe.parse_known_args(arguments)
    except _ProbeError:
        # `--json=VALUE`: the exact flag with a value it does not take. It is
        # a request for JSON all the same, so `_main_json` reports the full
        # parser's own error as a document (final review, Minor 1) rather
        # than the probe exiting 2 with nothing on stdout.
        return True
    return bool(parsed.json)


class _ProbeError(Exception):
    """The `--json` probe could not parse its one flag."""


class _Probe(argparse.ArgumentParser):
    """A parser whose errors raise instead of printing usage and exiting."""

    def error(self, message: str) -> NoReturn:
        raise _ProbeError(message)


def record_mirror_consent(record: ConsentRecord, *, owner: str | None) -> None:
    from hammunition.state.log import TransactionLog

    TransactionLog(owner=owner).append(record.to_log_entry())


def cmd_mirror_enrol(args: argparse.Namespace) -> int:
    from hammunition import mirror
    from hammunition.catalogue import CatalogueError
    from hammunition.signers import SignerError

    user = operator(args) or None
    try:
        station = load_station(owner=user)
        candidate = mirror.read_candidate(args.url, args.enrolment_id)
        interactive = is_interactive()
        mirror.enrol(
            candidate,
            args.url,
            args.enrolment_id,
            choose=input if interactive else None,
            affirm_hardware=(lambda text: input(text).strip() == "yes") if interactive else None,
            owner=user,
            require_hardware=station.mirror_require_hardware_key,
            now=datetime.now(UTC),
            record_consent=lambda record: record_mirror_consent(record, owner=user),
        )
    except (
        SignerError,
        CatalogueError,
        BackendError,
        StationError,
        ConsentUnavailable,
        ConsentDeclined,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    print("Bunker enrolled; hammunition mirror status shows accepted keys and serial")
    return EXIT_OK


@envelope.json_capable()
def cmd_mirror_status(args: argparse.Namespace) -> int:
    from hammunition.interface.mirror import build_mirror, render_mirror
    from hammunition.mirror import consistent_state
    from hammunition.signers import SignerError, load_mirror

    try:
        user = operator(args) or None
        station = load_station(owner=user)
        state = load_mirror(owner=user)
        consistent_state(station.mirror, state)
        doc = build_mirror(
            state, require_hardware=station.mirror_require_hardware_key, now=datetime.now(UTC)
        )
    except (SignerError, StationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    if envelope.wanted(args):
        envelope.emit(doc)
    else:
        for line in render_mirror(doc):
            print(line)
    return EXIT_OK


def cmd_mirror_accept_older(args: argparse.Namespace) -> int:
    from hammunition.catalogue import CatalogueError
    from hammunition.mirror import consistent_state, read_candidate
    from hammunition.signers import SignerError, load_mirror, save_mirror, verify

    user = operator(args) or None
    try:
        state = load_mirror(owner=user)
        if state is None:
            raise SignerError("no Bunker enrolled; hammunition mirror enrol URL")
        station = load_station(owner=user)
        consistent_state(station.mirror, state)
        candidate = read_candidate(state.url, state.enrolment_id)
        checked = verify(
            candidate.raw,
            candidate.signatures,
            dataclasses.replace(state, accepted_serial=0),
            require_hardware=station.mirror_require_hardware_key,
            now=datetime.now(UTC),
        )
        text = (
            f"Bunker {state.name}: accepted serial {state.accepted_serial}, restored catalogue "
            f"serial {checked.catalogue.serial}. Confirm that this Bunker was restored from "
            "a trusted backup. Type yes to accept the older serial: "
        )
        if not is_interactive():
            raise ConsentUnavailable(
                "accept-older requires typed yes at a terminal; --yes cannot answer it"
            )
        if input(text).strip() != "yes":
            raise ConsentDeclined("older Bunker catalogue was not accepted")
        save_mirror(
            dataclasses.replace(
                state,
                accepted_serial=checked.catalogue.serial,
                generated=checked.catalogue.generated,
            ),
            owner=user,
            allow_older=True,
        )
    except (
        SignerError,
        CatalogueError,
        BackendError,
        StationError,
        ConsentUnavailable,
        ConsentDeclined,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    print("Restored catalogue serial accepted")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hammunition",
        description="Turn a Debian-family install into an amateur radio, SDR and RF workstation.",
        epilog=(
            "Alpha. The apt, source, git, binary, venv and node backends exist; the "
            "install/configure/remove cycle is VM-verified on Parrot, Kali and "
            "Debian 13 across the whole catalog. A package needing a third-party "
            "apt repository, pipx or CPAN is refused by name."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"hammunition {_engine_version()}",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        metavar="DIR",
        help="path to the catalog/ directory (default: found from the checkout)",
    )
    # Not required: a bare `hammunition` prints help and exits 0 (main handles
    # it), which is friendlier than argparse's "command is required" error for
    # someone running it for the first time to see what it does.
    sub = parser.add_subparsers(dest="command", required=False)
    p_mirror = sub.add_parser("mirror", help="enrol and inspect a signed Bunker catalogue")
    mirror_sub = p_mirror.add_subparsers(dest="mirror_command", required=True)
    p_enrol = mirror_sub.add_parser("enrol", help="trust explicitly affirmed Bunker signers")
    p_enrol.add_argument("url")
    p_enrol.add_argument("--enrolment-id")
    p_enrol.set_defaults(func=cmd_mirror_enrol)
    p_status = mirror_sub.add_parser(
        "status", help="show stored Bunker trust without contacting it"
    )
    p_status.set_defaults(func=cmd_mirror_status)
    p_older = mirror_sub.add_parser(
        "accept-older", help="affirm a restored catalogue after a backup"
    )
    p_older.set_defaults(func=cmd_mirror_accept_older)

    p_list = sub.add_parser("list", help="show what the catalog contains")
    p_list.add_argument(
        "what",
        nargs="?",
        default="all",
        choices=("all", "packages", "profiles"),
        help="what to list (default: all)",
    )
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="what this machine is, and what has been done to it")
    p_status.set_defaults(func=cmd_status)

    p_update = sub.add_parser(
        "update",
        help="installed versus the catalog, as a report; nothing runs (D-053)",
    )
    p_update.add_argument(
        "names",
        nargs="*",
        help="units or profiles to compare; default: everything the log says was installed here",
    )
    p_update.add_argument("--user", default=None, help="whose log and builds to read")
    p_update.add_argument(
        "--upstream",
        action="store_true",
        help=(
            "also ask upstream (GitHub, git tags, PyPI, a version file, CoMaps' CDN) whether the "
            "catalog's pin is current; the only network the report uses"
        ),
    )
    p_update.add_argument(
        "--offline",
        action="store_true",
        help=(
            "read only the local apt lists and install records, with a verified Bunker "
            "catalogue enrolled; refuses --upstream. Accepting a newer catalogue advances "
            "the local trust state (#381)"
        ),
    )
    p_update.set_defaults(func=cmd_update)

    p_maps = sub.add_parser(
        "maps",
        help="offline maps: Geofabrik's regions (D-057), QMapShack and its GPS (D-061), "
        "Navit and repeaters (D-064), phone files (D-067), CoMaps (D-069)",
    )
    maps_sub = p_maps.add_subparsers(dest="maps_command", required=True)

    p_maps_regions = maps_sub.add_parser(
        "regions",
        help="list Geofabrik's region paths; fetches the index only when run",
    )
    p_maps_regions.add_argument(
        "filter",
        nargs="?",
        default=None,
        help="case-insensitive substring to match; default: every region",
    )
    p_maps_regions.set_defaults(func=cmd_maps_regions)

    p_maps_qms = maps_sub.add_parser(
        "qmapshack",
        help="add Hammunition's maps to your QMapShack configuration, then start it (D-061)",
    )
    p_maps_qms.add_argument(
        "--configure-only",
        action="store_true",
        help="edit the configuration and do not start QMapShack",
    )
    p_maps_qms.set_defaults(func=cmd_maps_qmapshack)

    p_maps_splat = maps_sub.add_parser(
        "splat",
        help="point SPLAT! at Hammunition's terrain through ~/.splat_path, and print "
        "Signal-Server's -sdf argument (D-061)",
    )
    p_maps_splat.set_defaults(func=cmd_maps_splat)

    p_maps_comaps = maps_sub.add_parser(
        "comaps",
        help="accept CoMaps' licence notice and link your maps for it, then start it (D-069)",
    )
    p_maps_comaps.add_argument(
        "--configure-only",
        action="store_true",
        help="prepare the settings and map links and do not start CoMaps",
    )
    p_maps_comaps.set_defaults(func=cmd_maps_comaps)

    p_maps_tether = maps_sub.add_parser(
        "gps-tether",
        help="serve gpsd's position as NMEA on 127.0.0.1:10110 for QMapShack's GPS TCP/IP source "
        "(D-061), and to the browser map on 127.0.0.1:10111 (D-071)",
    )
    p_maps_tether.add_argument(
        "--gpsd",
        metavar="HOST[:PORT]",
        default=None,
        help="the gpsd to read: another machine's, an IPv6 address in brackets "
        "(default 127.0.0.1:2947)",
    )
    p_maps_tether.add_argument(
        "--port",
        metavar="N",
        default=None,
        help="serve on 127.0.0.1 port N, 1024 to 65535, when 10110 is taken (default 10110)",
    )
    p_maps_tether.add_argument(
        "--position-port",
        metavar="N",
        default=None,
        help="serve the browser map's position stream (GET /position) on 127.0.0.1 port N "
        "(default 10111, D-071)",
    )
    p_maps_tether.add_argument(
        "--nmea-socket",
        metavar="PATH",
        default=None,
        help="also serve NMEA on a unix socket at PATH, mode 0660, for GeoClue (D-069); "
        "the default is /run/hammunition-gps/nmea.sock once `hardware apply` has set "
        "GeoClue up, and off otherwise",
    )
    p_maps_tether.add_argument(
        "--no-nmea-socket",
        action="store_true",
        help="do not serve GeoClue's socket, even where `hardware apply` has set it up",
    )
    p_maps_tether.set_defaults(func=cmd_maps_gps_tether)

    p_self_update = sub.add_parser(
        "self-update",
        help="pull the engine's own checkout and re-run bootstrap.sh (touches no apt, unit or station)",
    )
    p_self_update.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, then print the steps and the commits that would arrive; change nothing",
    )
    p_self_update.add_argument("--yes", action="store_true", help="skip the confirmation")
    p_self_update.add_argument(
        "--release",
        action="store_true",
        help="fast-forward to the newest v* tag reachable from origin/main, from any branch",
    )
    p_self_update.set_defaults(func=cmd_self_update)

    p_logs = sub.add_parser(
        "logs",
        help="the log of each run that changed something, newest first (D-077)",
    )
    p_logs.add_argument("--last", action="store_true", help="print the newest log in full")
    p_logs.add_argument("--path", action="store_true", help="print the newest log's path")
    p_logs.add_argument(
        "--user", default=None, help="whose logs to read (default: $SUDO_USER, else $USER)"
    )
    p_logs.set_defaults(func=cmd_logs)

    p_transactions = sub.add_parser(
        "transactions",
        help="the full transaction history, oldest first across archives (D-077)",
    )
    p_transactions.add_argument(
        "--last", type=int, default=None, metavar="N", help="show only the newest N transactions"
    )
    p_transactions.set_defaults(func=cmd_transactions)

    p_artifacts = sub.add_parser(
        "artifacts",
        help="list every remote data artifact for a selection, with no station (D-070)",
    )
    p_artifacts.add_argument(
        "--map-regions",
        default=None,
        metavar="R[,R...]",
        help="comma-separated Geofabrik region paths; none defers the map units",
    )
    p_artifacts.add_argument(
        "--map-freshness", default="yearly", choices=("yearly", "monthly", "latest")
    )
    p_artifacts.add_argument(
        "--units",
        default=None,
        metavar="U[,U...]",
        help="the units to list (default: every data, osm-regions, dem-tiles, mwm-regions "
        "and kiwix-books unit)",
    )
    p_artifacts.add_argument(
        "--reference-books",
        default=None,
        metavar="ID[,ID...]",
        help="comma-separated Kiwix book ids (`hammunition reference books` lists them); "
        "none defers kiwix-library",
    )
    p_artifacts.set_defaults(func=cmd_artifacts)

    p_maps_phone = maps_sub.add_parser(
        "phone",
        help="gather the phone map files into one folder with a SHA256SUMS and print the "
        "ways to carry them to a phone; transfers nothing (D-067)",
    )
    p_maps_phone.set_defaults(func=cmd_maps_phone)
    p_maps_navit = maps_sub.add_parser(
        "navit",
        help="start Navit on your offline maps, with your repeater layer when there is one (D-064)",
    )
    p_maps_navit.set_defaults(func=cmd_maps_navit)

    p_maps_areas = maps_sub.add_parser(
        "areas",
        help="every state and region loaded on this machine: its layers, size, date, and "
        "whether it is active (D-082; read-only)",
    )
    p_maps_areas.set_defaults(func=cmd_maps_areas)
    p_maps_activate = maps_sub.add_parser(
        "activate",
        help="make these states and regions the active ones in QMapShack, Navit and the "
        "browser map; nothing is deleted, --all brings everything back (D-082)",
    )
    p_maps_activate.add_argument(
        "areas",
        nargs="*",
        metavar="CODE|REGION",
        help="US state codes (OH) and map region names (north-america/us/ohio); one that is "
        "not loaded is accepted, with a note",
    )
    p_maps_activate.add_argument(
        "--all", action="store_true", help="every loaded area is active (the default)"
    )
    p_maps_activate.add_argument(
        "--none", action="store_true", help="no area is active; layers with no area stay"
    )
    p_maps_activate.add_argument(
        "--dry-run", action="store_true", help="print what would change and write nothing"
    )
    p_maps_activate.set_defaults(func=cmd_maps_activate)

    p_maps_rep = maps_sub.add_parser(
        "repeaters",
        help="repeaters on the map, one layer per source, converted on this machine (D-064, D-074)",
    )
    rep_sub = p_maps_rep.add_subparsers(dest="maps_repeaters_command", required=True)
    p_rep_import = rep_sub.add_parser(
        "import",
        help="convert a RepeaterBook GPX or CSV export, hearham JSON or your own CSV, or one "
        "of --from-open-repeater, --from-acma, --from-osm, --from-direwolf-log; offline",
    )
    p_rep_import.add_argument("files", nargs="*", metavar="FILE", help="the export(s) to convert")
    p_rep_import.add_argument(
        "--exported",
        metavar="YYYY-MM-DD",
        default=None,
        help="the day you exported it, for the layer's name (default: the file's date)",
    )
    p_rep_import.add_argument(
        "--from-open-repeater",
        nargs="?",
        const="",
        default=None,
        metavar="FILE",
        help="Open Repeater's CC0 list: the installed open-repeater unit's file, or FILE (D-074)",
    )
    p_rep_import.add_argument(
        "--from-acma",
        nargs="?",
        const="",
        default=None,
        metavar="FILE",
        help="the Australian regulator's register: the installed acma-register unit's zip, or "
        "FILE; keeps the repeaters inside your installed regions (D-074)",
    )
    p_rep_import.add_argument(
        "--from-osm",
        action="store_true",
        help="the repeaters tagged in the map regions installed here, filtered by osmium; "
        "downloads nothing (D-074)",
    )
    p_rep_import.add_argument(
        "--from-direwolf-log",
        nargs="+",
        default=None,
        metavar="FILE",
        help="the APRS repeater objects in Direwolf's -l or -L log: what this station "
        "heard, its own layer (D-074)",
    )
    p_rep_import.set_defaults(func=cmd_maps_repeaters_import)
    p_rep_fetch = rep_sub.add_parser(
        "fetch-hearham",
        help="fetch hearham.com's open list now and convert it; recorded as unverified",
    )
    p_rep_fetch.add_argument(
        "--no-mirror",
        action="store_true",
        help="do not ask the station's LAN mirror first; fetch from the publisher (D-078)",
    )
    p_rep_fetch.set_defaults(func=cmd_maps_repeaters_fetch_hearham)
    p_rep_etcc = rep_sub.add_parser(
        "fetch-etcc",
        help="fetch the RSGB ETCC's UK repeater list now and convert it; recorded as "
        "unverified (D-074)",
    )
    p_rep_etcc.add_argument(
        "--no-mirror",
        action="store_true",
        help="do not ask the station's LAN mirror first; fetch from the publisher (D-078)",
    )
    p_rep_etcc.set_defaults(func=cmd_maps_repeaters_fetch_etcc)
    p_rep_bm = rep_sub.add_parser(
        "fetch-brandmeister",
        help="fetch Brandmeister's DMR repeaters now, hotspots dropped; recorded as "
        "unverified (D-074)",
    )
    p_rep_bm.add_argument(
        "--no-mirror",
        action="store_true",
        help="do not ask the station's LAN mirror first; fetch from the publisher (D-078)",
    )
    p_rep_bm.set_defaults(func=cmd_maps_repeaters_fetch_brandmeister)
    p_rep_rb = rep_sub.add_parser(
        "fetch-repeaterbook",
        help="fetch repeaters from RepeaterBook's API with your own token for App #114 "
        "(REPEATERBOOK or Doppler), through the repeaterbook-client unit; personal use, "
        "recorded as unverified (D-081)",
    )
    p_rep_rb.add_argument(
        "--state",
        action="append",
        required=True,
        metavar="NAME|CODE",
        help="a state name or two-letter code, e.g. Delaware or DE; repeat for more",
    )
    p_rep_rb.add_argument(
        "--county",
        action="append",
        metavar="NAME",
        help="narrow to one county of the one --state (RepeaterBook answers at most about "
        "3,500 rows at once); repeatable, merged into that state's layer",
    )
    p_rep_rb.add_argument(
        "--country",
        default="United States",
        help="RepeaterBook's country name (default: United States)",
    )
    p_rep_rb.set_defaults(func=cmd_maps_repeaters_fetch_repeaterbook)
    p_rep_remove = rep_sub.add_parser(
        "remove", help="delete repeater layers and take them out of QMapShack and Navit"
    )
    p_rep_remove.add_argument(
        "--layer",
        type=_repeater_layer_id,
        default=None,
        metavar="ID",
        help="remove only this layer: export, acma, open-repeater, osm, etcc, brandmeister, "
        "repeaterbook (the earlier merged one), aprs-heard, or repeaterbook-AREA for one "
        "state (repeaterbook-OH); default: every layer and the all-sources file",
    )
    p_rep_remove.set_defaults(func=cmd_maps_repeaters_remove)
    p_rep_list = rep_sub.add_parser(
        "list",
        help="the repeater layers as data for a program (read-only; a layer it cannot read is "
        "listed as left out and the rest is returned, exit 0)",
    )
    p_rep_list.add_argument(
        "--layer",
        action="append",
        default=None,
        metavar="ID",
        help="read only this layer; repeatable; an id that is not a layer is reported, not an error",
    )
    p_rep_list.add_argument(
        "--near",
        default=None,
        metavar="GRID|LAT,LON",
        help="measure distance and bearing from here: a Maidenhead locator (four, six or eight "
        "characters: the centre of the square) or LAT,LON in decimal degrees; default the "
        "station's grid square when one is set, else no distances; rows are then nearest first",
    )
    p_rep_list.add_argument(
        "--within",
        type=float,
        default=None,
        metavar="KM",
        help="only repeaters this far or nearer (needs --near or a station grid square)",
    )
    p_rep_list.add_argument(
        "--band",
        action="append",
        default=None,
        choices=[*(name for name, _, _ in _repeater_bands()), "other"],
        help="only this band; repeatable",
    )
    p_rep_list.add_argument(
        "--mode",
        action="append",
        default=None,
        type=_repeater_mode,
        metavar="MODE",
        help="only what speaks this mode (FM, DMR, D-STAR, YSF, P25, NXDN, M17, TETRA, ATV; "
        "a source's spelling such as dstar or fusion is accepted); repeatable, any of them "
        "matches. Naming a place, a band or a mode lists the matching repeaters in the text",
    )
    p_rep_list.set_defaults(func=cmd_maps_repeaters_list)

    from hammunition import infra as infra_layers

    def _infra_layer_id(text: str) -> str:
        if not infra_layers.is_layer_id(text):
            raise argparse.ArgumentTypeError(
                f"no infrastructure layer {text!r}; the layers are "
                f"{', '.join(infra_layers.LAYERS)}, each also as <layer>-<region slug>"
            )
        return text

    p_maps_infra = maps_sub.add_parser(
        "infra",
        help="infrastructure and EMCOMM points on the map, one layer per source (D-075)",
    )
    infra_sub = p_maps_infra.add_subparsers(dest="maps_infra_command", required=True)
    p_inf_import = infra_sub.add_parser(
        "import",
        help="write layers from the map regions installed here (--from-osm) or an installed "
        "data unit (--from-nasr, --from-eia, --from-wri); offline",
    )
    inf_from = p_inf_import.add_mutually_exclusive_group(required=True)
    inf_from.add_argument(
        "--from-osm",
        action="store_true",
        help="medical, responders, supply, shelter candidates, transport, power, telecom and "
        "water from your region extracts, filtered by osmium; downloads nothing",
    )
    inf_from.add_argument(
        "--from-nasr",
        action="store_true",
        help="airports and heliports from the installed faa-nasr-airports unit",
    )
    inf_from.add_argument(
        "--from-eia",
        action="store_true",
        help="US power plants from the installed eia-860m unit",
    )
    inf_from.add_argument(
        "--from-wri",
        action="store_true",
        help="power plants outside the US from the installed wri-power-plants unit",
    )
    p_inf_import.add_argument(
        "--layers",
        metavar="LAYER,LAYER",
        default=None,
        help="with --from-osm, only these: medical, responders, supply, shelter-candidates, "
        "transport, power, telecom, water",
    )
    p_inf_import.add_argument(
        "--merged",
        action="store_true",
        help="one layer per source across every region, as before the per-region split; "
        "default: one per region, so `maps activate` can choose which are drawn",
    )
    p_inf_import.set_defaults(func=cmd_maps_infra_import)
    p_inf_fcc = infra_sub.add_parser(
        "fetch-fcc-asr",
        help="fetch the FCC's antenna structure registrations now (38 MB) and keep your "
        "regions' towers; recorded as unverified",
    )
    p_inf_fcc.add_argument(
        "--merged",
        action="store_true",
        help="keep one layer across every region instead of one per region",
    )
    p_inf_fcc.set_defaults(func=cmd_maps_infra_fetch_fcc_asr)
    p_inf_nwr = infra_sub.add_parser(
        "fetch-nwr",
        help="fetch NOAA Weather Radio's transmitter list now; live status dropped, recorded "
        "as unverified",
    )
    p_inf_nwr.add_argument(
        "--merged",
        action="store_true",
        help="keep one layer across every region instead of one per region",
    )
    p_inf_nwr.set_defaults(func=cmd_maps_infra_fetch_nwr)
    p_inf_remove = infra_sub.add_parser(
        "remove", help="delete infrastructure layers and take them out of QMapShack and Navit"
    )
    p_inf_remove.add_argument(
        "--layer",
        type=_infra_layer_id,
        metavar="ID",
        default=None,
        help="remove only this layer: a theme such as osm-medical (the merged layer) or one "
        f"region's, <theme>-<region slug> ({', '.join(infra_layers.LAYERS)}; default: every "
        "layer)",
    )
    p_inf_remove.set_defaults(func=cmd_maps_infra_remove)

    p_reference = sub.add_parser(
        "reference", help="the offline reference: Kiwix books, ICS forms, dictionaries (D-066)"
    )
    reference_sub = p_reference.add_subparsers(dest="reference_command", required=True)
    p_ref_books = reference_sub.add_parser(
        "books", help="list the Kiwix books the catalog offers, with size and licence"
    )
    p_ref_books.add_argument("--user", default=None, help="whose station configuration to read")
    p_ref_books.set_defaults(func=cmd_reference_books)
    p_ref_serve = reference_sub.add_parser(
        "serve",
        help="serve the books, forms, dictionaries and the offline map on 127.0.0.1:8480 "
        "until Ctrl-C",
    )
    p_ref_serve.add_argument(
        "--port",
        metavar="N",
        default=None,
        help="serve the page on 127.0.0.1 port N (1024 to 65534); kiwix-serve takes N+1 "
        "(default 8480)",
    )
    p_ref_serve.add_argument(
        "--position-port",
        metavar="N",
        default=None,
        help="where the map page asks the GPS tether for your position: 127.0.0.1 port N "
        "(default 10111, the tether's own default, D-071)",
    )
    p_ref_serve.add_argument(
        "--readsb-json",
        metavar="DIR",
        default=None,
        help="the directory readsb writes aircraft.json to, which the aircraft page (tar1090) "
        "reads, read-only (default /run/readsb)",
    )
    p_ref_serve.set_defaults(func=cmd_reference_serve)

    p_show = sub.add_parser("show", help="describe a profile, disclosure included")
    p_show.add_argument("profile")
    p_show.set_defaults(func=cmd_show)

    p_install = sub.add_parser("install", help="install packages or profiles")
    p_install.add_argument("names", nargs="+", metavar="NAME", help="package or profile names")
    p_install.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve everything and print exactly what would run, then stop",
    )
    p_install.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation. Does NOT satisfy a consent gate (D-021)",
    )
    p_install.add_argument(
        "--refresh",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "run `apt-get update` as the first command of the transaction, when the "
            "transaction has apt work (the default; D-044). --no-refresh skips it: a "
            "local mirror, or a station with no uplink"
        ),
    )
    p_install.add_argument(
        "--sudo-keepalive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "when run as a user, ask sudo's password once before the first step and keep "
            "its ticket valid until the run ends, so a long unprivileged step cannot leave "
            "a later root step waiting at a prompt (the default; D-062). "
            "--no-sudo-keepalive turns it off"
        ),
    )
    p_install.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "stream every line each command prints as it arrives, instead of the one "
            "in-place status line shown for a command that runs longer than two seconds "
            "on a terminal; the run log is the same either way"
        ),
    )
    p_install.add_argument(
        "--full",
        action="store_true",
        help=(
            "print every step of the plan expanded; without it a run of steps that "
            "repeat one template for many items (a sheet, a tile, a book) is shown "
            "as the template, one example, every item and the totals (D-016)"
        ),
    )
    p_install.add_argument(
        "--recheck",
        action="store_true",
        help=(
            "ask every data item's publisher at plan time, including the installed "
            "ones the log attributes (otherwise those are trusted for 7 days; "
            "#197, D-049)"
        ),
    )
    p_install.add_argument(
        "--no-mirror",
        action="store_true",
        help=(
            "ignore the LAN mirror set in station config for this run; every data "
            "download comes from its publisher (D-070)"
        ),
    )
    p_install.add_argument(
        "--offline",
        action="store_true",
        help=(
            "resolve everything from the enrolled Bunker's verified catalogue and never ask "
            "a publisher; refuses by name what the Bunker cannot supply. Skips apt-get "
            "update, and cannot be combined with --no-mirror (#381)"
        ),
    )
    p_install.add_argument(
        "--user",
        default=None,
        help="operator to add to groups (default: $SUDO_USER, else $USER)",
    )
    # Station values. Supplying one here overrides the saved file for this run
    # and, if a prompt happens, is remembered.
    p_install.add_argument("--callsign", default=None, help="your callsign — it is transmitted")
    p_install.add_argument("--grid-square", default=None, help="Maidenhead locator, e.g. IO91wm")
    p_install.add_argument(
        "--node-alias", default=None, help="short packet node alias, up to six characters"
    )
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser(
        "uninstall", help="remove what Hammunition itself installed, by backend (D-004)"
    )
    p_uninstall.add_argument("names", nargs="+", metavar="NAME", help="package or profile names")
    p_uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the removal and print exactly what would run, then stop",
    )
    p_uninstall.add_argument("--yes", action="store_true", help="skip the confirmation")
    p_uninstall.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="stream every line each command prints as it arrives (see `install --verbose`)",
    )
    p_uninstall.add_argument(
        "--user",
        default=None,
        help="operator whose transaction log to read (default: $SUDO_USER, else $USER)",
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_menus = sub.add_parser("menus", help="curated desktop menus from the catalog (D-036)")
    menus_sub = p_menus.add_subparsers(dest="menus_command", required=True)
    p_menus_apply = menus_sub.add_parser(
        "apply", help="write the Hammunition menu tree; on GNOME one app-folder per group"
    )
    p_menus_apply.add_argument(
        "--gnome", action="store_true", help="apply the GNOME app-folder even if undetected"
    )
    p_menus_apply.add_argument(
        "--menu-prefix",
        default=None,
        metavar="PREFIX",
        help=(
            "which root menu to merge into (plasma-, xfce-, gnome-, ...); default "
            "$XDG_MENU_PREFIX, else the one root menu installed, else refuse"
        ),
    )
    p_menus_apply.set_defaults(func=cmd_menus_apply)

    p_doctor = sub.add_parser("doctor", help="read-only health check: is this machine ready?")
    p_doctor.add_argument("--user", default=None, help="whose setup to check")
    p_doctor.set_defaults(func=cmd_doctor)

    p_hardware = sub.add_parser(
        "hardware", help="detect devices; apply udev rules and groups (D-029)"
    )
    hardware_sub = p_hardware.add_subparsers(dest="hardware_command", required=True)

    p_hw_list = hardware_sub.add_parser("list", help="what is attached and what setup it needs")
    p_hw_list.add_argument("--user", default=None, help="whose group membership to check")
    p_hw_list.set_defaults(func=cmd_hardware_list)

    p_hw_apply = hardware_sub.add_parser(
        "apply", help="write the udev rules and join the device-access groups"
    )
    p_hw_apply.add_argument("--dry-run", action="store_true", help="print, change nothing")
    p_hw_apply.add_argument("--yes", action="store_true", help="skip the confirmation")
    p_hw_apply.add_argument("--user", default=None, help="whom to set up")
    p_hw_apply.add_argument(
        "--no-gps-time",
        action="store_true",
        help="leave ntpsec, its grants and fake-hwclock alone (D-058)",
    )
    p_hw_apply.add_argument(
        "--no-geoclue",
        action="store_true",
        help="leave GeoClue alone: no socket drop-in, no tmpfiles line (D-069)",
    )
    p_hw_apply.add_argument(
        "--no-gps-resume",
        action="store_true",
        help="leave out the GPS receiver's resume step (issue #177)",
    )
    p_hw_apply.set_defaults(func=cmd_hardware_apply)

    p_hw_unapply = hardware_sub.add_parser(
        "unapply", help="remove the power-control helper and polkit action (D-056)"
    )
    p_hw_unapply.add_argument(
        "--dry-run", action="store_true", help="print what would be removed, then stop"
    )
    p_hw_unapply.add_argument("--yes", action="store_true", help="skip the confirmation")
    p_hw_unapply.add_argument(
        "--user",
        default=None,
        help="operator whose transaction log to read (default: $SUDO_USER, else $USER)",
    )
    p_hw_unapply.set_defaults(func=cmd_hardware_unapply)

    p_hw_state = hardware_sub.add_parser(
        "state", help="which devices can be parked, and which are parked now"
    )
    p_hw_state.set_defaults(func=cmd_hardware_state)

    p_hw_resume = hardware_sub.add_parser(
        "gps-resume-report",
        help="read-only: is the GPS resume step current, what did it do, is data flowing",
    )
    p_hw_resume.add_argument(
        "--data-window",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="how long to watch gpsd for data from each receiver (default 10)",
    )
    p_hw_resume.set_defaults(func=cmd_hardware_gps_resume_report)

    for verb, helptext in (
        ("park", "detach a device and let its port suspend (D-056)"),
        ("wake", "bring a parked device back"),
    ):
        p_verb = hardware_sub.add_parser(verb, help=helptext)
        p_verb.add_argument(
            "name",
            metavar="NAME",
            help="catalog name, or NAME@ADDRESS when two of a kind are attached",
        )
        p_verb.add_argument(
            "--dry-run",
            action="store_true",
            help="print the privileged call and every write it would cause, then stop",
        )
        if verb == "park":
            p_verb.add_argument(
                "--until-reboot",
                action="store_true",
                help="park now, but let a reboot wake it (no kept entry)",
            )
        p_verb.set_defaults(func=cmd_hardware_park if verb == "park" else cmd_hardware_wake)

    from hammunition.gpstime.mode import MODES

    p_time = sub.add_parser(
        "time", help="GPS time (D-058): what the clock follows, and the time mode"
    )
    p_time.set_defaults(func=cmd_time)
    time_sub = p_time.add_subparsers(dest="time_command")
    p_time_mode = time_sub.add_parser(
        "mode", help="auto | prefer-gps | ntp-only | gps-only, through the helper"
    )
    p_time_mode.add_argument("mode", choices=MODES)
    p_time_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="print every write, the restart and the privileged call, then stop",
    )
    p_time_mode.set_defaults(func=cmd_time_mode)
    p_time_measure = time_sub.add_parser(
        "measure",
        help="read-only: sample ntpq and say whether the GPS took over (issue #310)",
    )
    p_time_measure.add_argument(
        "--minutes", type=float, default=10.0, help="how long to sample (default 10)"
    )
    p_time_measure.add_argument(
        "--interval", type=float, default=30.0, help="seconds between samples (default 30)"
    )
    p_time_measure.add_argument(
        "--pps",
        action="store_true",
        help="then run `ppstest /dev/pps0` and say whether the pulses are real",
    )
    p_time_measure.add_argument(
        "--pps-seconds", type=float, default=60.0, help="how long ppstest runs (default 60)"
    )
    p_time_measure.set_defaults(func=cmd_time_measure)

    p_console = sub.add_parser(
        "console",
        help="the full-screen terminal front end (needs urwid; see `hammunition console --help`)",
        add_help=False,
    )
    p_console.set_defaults(func=cmd_console)

    p_services = sub.add_parser(
        "services",
        help="the services the helper controls, and start, stop, enable or disable one",
    )
    p_services.set_defaults(func=cmd_services)
    services_sub = p_services.add_subparsers(dest="services_command")
    for service_verb, service_help in (
        ("start", "start a service now"),
        ("stop", "stop a service now"),
        ("enable", "start a service at boot (system) or login (user)"),
        ("disable", "stop starting a service at boot (system) or login (user)"),
    ):
        p_service_verb = services_sub.add_parser(service_verb, help=service_help)
        p_service_verb.add_argument(
            "name", metavar="NAME", help="a name from `hammunition services`"
        )
        p_service_verb.add_argument(
            "--dry-run",
            action="store_true",
            help="print the helper call, then stop",
        )
        p_service_verb.set_defaults(func=cmd_services_act, action=service_verb)

    p_secrets = sub.add_parser("secrets", help="keys for downloads that need one (D-081)")
    secrets_sub = p_secrets.add_subparsers(dest="secrets_command", required=True)
    p_secrets_status = secrets_sub.add_parser(
        "status", help="where each secret would come from; never its value"
    )
    p_secrets_status.add_argument("--user", default=None, help="whose station to read")
    p_secrets_status.set_defaults(func=cmd_secrets_status)

    p_station = sub.add_parser("station", help="the values only you can supply")
    station_sub = p_station.add_subparsers(dest="station_command", required=True)

    p_station_show = station_sub.add_parser("show", help="print the saved station values")
    p_station_show.add_argument("--user", default=None, help="whose configuration to read")
    p_station_show.set_defaults(func=cmd_station_show)

    p_station_set = station_sub.add_parser("set", help="save station values")
    p_station_set.add_argument("--callsign", default=None)
    p_station_set.add_argument("--grid-square", default=None)
    p_station_set.add_argument("--node-alias", default=None)
    p_station_set.add_argument(
        "--map-regions",
        default=None,
        help="comma-separated Geofabrik regions to carry offline maps for",
    )
    p_station_set.add_argument(
        "--map-freshness", default=None, choices=("yearly", "monthly", "latest")
    )
    p_station_set.add_argument(
        "--reference-books",
        default=None,
        metavar="ID[,ID…]",
        help="comma-separated Kiwix book ids to carry offline; `hammunition reference "
        "books` lists them (D-066)",
    )
    policy = p_station_set.add_mutually_exclusive_group()
    policy.add_argument(
        "--mirror-require-hardware-key",
        dest="mirror_require_hardware_key",
        action="store_true",
        default=None,
    )
    policy.add_argument(
        "--no-mirror-require-hardware-key", dest="mirror_require_hardware_key", action="store_false"
    )
    mirror_flags = p_station_set.add_mutually_exclusive_group()
    mirror_flags.add_argument(
        "--mirror",
        default=None,
        metavar="URL",
        help="a LAN mirror of the data artifacts, tried before the publisher (D-070)",
    )
    mirror_flags.add_argument("--clear-mirror", action="store_true", help="remove the saved mirror")
    p_station_set.add_argument(
        "--doppler-project",
        default=None,
        metavar="PROJECT",
        help="the Doppler project a keyed download's key is read from when its environment "
        "variable is not set: a name, never a token (D-081)",
    )
    p_station_set.add_argument(
        "--doppler-config",
        default=None,
        metavar="CONFIG",
        help="the Doppler config within that project, e.g. dev or prd (D-081)",
    )
    p_station_set.add_argument(
        "--clear-doppler",
        action="store_true",
        help="remove the saved Doppler project and config",
    )
    p_station_set.add_argument(
        "--dem-source",
        default=None,
        choices=("copernicus", "3dep"),
        help="the elevation QMapShack's hillshade and contours are drawn from: copernicus "
        "(the default, a surface model) or 3dep (USGS bare earth, about 10x larger) (D-068)",
    )
    p_station_set.add_argument(
        "--topo-radius-km",
        default=None,
        type=int,
        metavar="N",
        help="how far from your grid square's centre US Topo sheets, FSTopo sheets and 3DEP "
        "tiles are selected: 100 when unset, 0 for none (D-068, issue #232)",
    )
    p_station_set.add_argument(
        "--topo-regions",
        default=None,
        metavar="REGION[,REGION…]",
        help="narrow the topographic selection to these map regions (a subset of "
        "--map-regions); with no --topo-radius-km they are taken whole",
    )
    p_station_set.add_argument(
        "--clear-topo-regions",
        action="store_true",
        help="remove --topo-regions, so the radius applies to every map region again",
    )
    p_station_set.add_argument(
        "--topo-all",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="select every sheet of every region, as before the bound; the install states "
        "the size and asks a typed yes that --yes does not answer",
    )
    p_station_set.add_argument(
        "--active-areas",
        nargs="+",
        default=None,
        metavar="CODE|REGION",
        help="the areas drawn and registered (D-082): US state codes (OH) and map region "
        "names (north-america/us/ohio), several at once; nothing is deleted. "
        "`maps activate` sets this and re-registers QMapShack, Navit and the browser map",
    )
    p_station_set.add_argument(
        "--clear-active-areas",
        action="store_true",
        help="remove --active-areas, so everything loaded is active again",
    )
    p_station_set.add_argument(
        "--rig",
        default=None,
        metavar="DEVICE|hamlib:MODEL",
        help="the station's radio: a catalog device id, or hamlib:<model> for one with no "
        "manifest (D-073)",
    )
    p_station_set.add_argument(
        "--rig-device",
        default=None,
        metavar="PATH",
        help="the serial port the rig is reached on; a /dev/serial/by-id/ path is best",
    )
    p_station_set.add_argument(
        "--rig-baud", default=None, type=int, metavar="RATE", help="the CAT serial speed"
    )
    p_station_set.add_argument(
        "--rig-ptt-line",
        default=None,
        choices=("rts", "dtr", "vox"),
        help="for a radio with no CAT: which line keys it, or vox",
    )
    p_station_set.add_argument(
        "--rig-owner",
        default=None,
        choices=("rigctld", "flrig"),
        help="who holds the serial port (default rigctld when unset)",
    )
    p_station_set.add_argument(
        "--clear-rig",
        action="store_true",
        help="remove rig, rig_device, rig_baud, rig_ptt_line and rig_owner",
    )
    unattended_flags = p_station_set.add_mutually_exclusive_group()
    unattended_flags.add_argument(
        "--unattended",
        dest="unattended",
        action="store_true",
        default=None,
        help="keep the rig service running with nobody logged in (enables linger; D-073 §5a)",
    )
    unattended_flags.add_argument(
        "--no-unattended",
        dest="unattended",
        action="store_false",
        default=None,
        help="stop keeping services running after logout (disables linger if we enabled it)",
    )
    p_station_set.add_argument("--user", default=None, help="whose configuration to write")
    p_station_set.set_defaults(func=cmd_station_set)

    _add_json_flag(parser, top=True)
    _disallow_abbrev(parser)
    return parser


#: The arguments of the run in progress, for its log's header (D-077).
_RUN_ARGV: list[str] = []


def _loggable(args: argparse.Namespace) -> bool:
    """Whether this command leaves a run log (D-077): the ones that change
    state or run long. Readouts (`status`, `list`, `doctor`, `show`) do not,
    and `station set` is not logged because its argv *is* the station values."""
    name = envelope.command_name(args)
    words = name.split()
    if not words:
        return False
    if envelope.wanted(args) and not getattr(args, "dry_run", False):
        return False  # a front end polling `update --json` must not evict real logs
    if words[0] == "services":
        return getattr(args, "action", None) is not None  # the list is a readout
    if words[0] in ("install", "uninstall", "update", "menus", "maps", "self-update"):
        return True
    if words[0] == "hardware":
        return len(words) > 1 and words[1] in ("apply", "unapply", "park", "wake")
    if words[0] == "reference":
        return len(words) > 1 and words[1] == "serve"
    if words[0] == "time":
        return len(words) > 1 and words[1] == "mode"
    return False


def _station_secrets(args: argparse.Namespace) -> list[str]:
    """Every station value this run could print, to scrub from its log (D-077):
    the saved station and what was typed on the command line."""
    values: list[str] = []
    try:
        from hammunition.station import load_station

        station = load_station(owner=operator(args) or None)
        values += [
            v
            for v in (
                station.callsign,
                station.grid_square,
                station.node_alias,
                station.mirror,
                station.rig_device,
                station.rig_owner,
                *station.map_regions,
            )
            if v
        ]
        if station.mirror:
            from urllib.parse import urlparse

            host = urlparse(station.mirror).hostname
            if host:
                values.append(host)
    except Exception:  # a broken station file is the command's to report, not the log's
        pass
    for attr in ("callsign", "grid_square", "node_alias", "map_regions", "mirror", "rig_device"):
        typed = getattr(args, attr, None)
        if isinstance(typed, str) and typed:
            values += [part.strip() for part in typed.split(",")] + [typed]
    return values


def _dispatch(args: argparse.Namespace) -> int:
    """Run the chosen command inside its run log, when it gets one."""
    if not _loggable(args):
        return _dispatch_command(args)
    from hammunition import runlog

    version = envelope.engine_version()
    with runlog.session(
        command=envelope.command_name(args),
        argv=["hammunition", *_RUN_ARGV],
        owner=operator(args) or None,
        version=version,
    ) as run:
        if run is not None:
            run.set_scrub(_station_secrets(args))
        code = _dispatch_command(args)
        if run is not None:
            run.exit_code = code
    if run is not None and not envelope.wanted(args):
        print(f"Log: {run.path}", file=sys.stderr)
    return code


def _dispatch_command(args: argparse.Namespace) -> int:
    """Run the chosen command, turning operator-input errors into exit codes."""
    try:
        result: int = args.func(args)
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    except StationError as exc:
        # A bad --callsign is operator input, not an engine fault: it gets the
        # validator's message and the planning exit code, never a traceback.
        # Found on the first Parrot VM run that passed one.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    except KeyboardInterrupt:
        print("\nInterrupted. Nothing further was run.", file=sys.stderr)
        return EXIT_FAILED
    return result


def _exit_code(exc: SystemExit) -> int:
    """The status the interpreter would exit with for *exc*."""
    if exc.code is None:
        return EXIT_OK
    if isinstance(exc.code, int):
        return exc.code
    return EXIT_FAILED  # SystemExit("message") prints it and exits 1


def _main_json(arguments: list[str]) -> int:
    """``--json``: exactly one document on stdout, on every path.  D-059.

    Both standard streams point at a recording tee over the real stderr
    while the command runs, so nothing it prints can reach stdout; the
    document goes to the real stdout through :func:`envelope.emit`. A run
    that ends without one gets an error document with the same exit code.
    """
    real_stdout, real_stderr = sys.stdout, sys.stderr
    tee = envelope.Tee(real_stderr)
    sys.stdout = sys.stderr = cast(TextIO, tee)
    envelope.begin(real_stdout)
    try:
        try:
            args = build_parser().parse_args(arguments)
        except SystemExit as exc:
            code = _exit_code(exc)
            if code == EXIT_OK:
                return EXIT_OK  # --help or --version: printed to stderr, not a document
            envelope.emit(
                envelope.ErrorDocument(command="", exit_code=code, message=tee.text().strip())
            )
            return code
        command = envelope.command_name(args)
        why = envelope.refusal(args)
        if why is not None:
            # Bare `hammunition --json` keeps bare `hammunition`'s exit 0
            # (final review, Minor 2): no command is not a refused command.
            code = EXIT_OK if getattr(args, "func", None) is None else EXIT_UNPLANNABLE
            print(f"error: {why}", file=sys.stderr)
            envelope.emit(
                envelope.ErrorDocument(command=command, exit_code=code, message=tee.text().strip())
            )
            return code
        try:
            code = _dispatch(args)
        except SystemExit as exc:
            code = _exit_code(exc)
            if isinstance(exc.code, str):
                print(exc.code, file=sys.stderr)
        except Exception:
            # A bug in a command, or emit() itself raising -- json.dumps on
            # a non-serialisable field, say -- must not leave stdout empty
            # (review round 1, Important 1): the module's own promise is
            # "stdout parses as exactly one document on every path", and
            # `_dispatch` only catches CatalogError, StationError and
            # KeyboardInterrupt. The traceback goes to the real stderr
            # through the tee, loud rather than silently swallowed;
            # KeyboardInterrupt keeps its existing handling in `_dispatch`.
            traceback.print_exc(file=sys.stderr)
            code = EXIT_FAILED
        if not envelope.emitted():
            envelope.emit(
                envelope.ErrorDocument(command=command, exit_code=code, message=tee.text().strip())
            )
        return code
    finally:
        envelope.end()
        sys.stdout, sys.stderr = real_stdout, real_stderr


def main(argv: Sequence[str] | None = None) -> int:
    # Line-buffer stdout even when it is not a terminal. A whole-profile
    # install redirected to a file showed 0 bytes for the forty minutes it
    # ran (Kali VM, 2026-09-02): Python block-buffers a pipe, so every `$
    # command` header sat in memory while the child processes, which write
    # to the same descriptor directly, streamed past it -- a log that is
    # empty until exit, and then out of order. An install that is killed
    # mid-way loses the whole record. Line buffering costs nothing an
    # installer notices.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)
    arguments = list(sys.argv[1:] if argv is None else argv)
    _RUN_ARGV[:] = arguments
    if arguments[:1] == ["console"] and "--json" not in arguments:
        # The console owns its own --help and --version, so it is handed its
        # words whole. `console --json` falls through to the parser and is
        # refused like any verb with no document.
        return cmd_console(argparse.Namespace(console_args=arguments[1:]))
    if _json_requested(arguments):
        return _main_json(arguments)
    parser = build_parser()
    args = parser.parse_args(arguments)
    if not getattr(args, "func", None):
        # Bare `hammunition`: print the top-level help and exit cleanly, which
        # is friendlier than argparse's "command is required" error for someone
        # running it for the first time. (Group verbs keep required sub-verbs,
        # so `hammunition hardware` still gets argparse's standard message.)
        parser.print_help()
        return EXIT_OK
    return _dispatch(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
