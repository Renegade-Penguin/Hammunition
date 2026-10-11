# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pre-flight resolution.  D-016.

Everything is resolved before anything is done, and every failure is reported
together. This is one module rather than a step inside ``install`` because
D-016 makes it a distinct phase with its own contract:

    Resolve everything for the whole transaction, report every failure
    together, then install — do not discover failures one package at a time,
    mid-run.

The failure this prevents is the defining defect of the prior art. AHRL has no
``set -e`` and checks no exit status across 3,911 lines, so every ``apt
install`` may fail and the script proceeds; ``bin/find_errors_ahrl`` exists to
grep a 2.5-hour transcript for error strings *afterwards*, and its own comment
concedes it does not catch everything.

D-016 also names four dependency lines in AHRL that are suspected to be failing
silently today — ``fftw2`` (FFTW **2**), ``libgtk2.0-dev`` (EOL),
``python3-tksnack``, and an **OCaml** binding fldigi does not use. All four are
apt package names in a dependency list, which is why
:func:`resolve` puts a manifest's ``depends`` through apt rather than trusting
it. A dependency nobody has ever checked is not a dependency, it is a comment.

A plan is data. It knows what would happen and can say so completely; it does
not know how to do any of it. That is what makes ``--dry-run`` complete by
construction rather than by discipline — the dry run prints the same plan the
installer executes.
"""

from __future__ import annotations

import pwd
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition.backends import (
    DISCLOSED_ONLY_MODIFICATIONS,
    IMPLEMENTED_BINARY_FORMATS,
    IMPLEMENTED_METHODS,
    IMPLEMENTED_MODIFICATIONS,
    AptBackend,
    AptPackageState,
    AptSimulation,
)
from hammunition.backends.apt import downgrades_refused
from hammunition.backends.apt_repo import AptRepoBackend, RepoState
from hammunition.backends.pmtiles import TILEMAKER_FLOOR
from hammunition.backends.source import DEFAULT_PREFIX, IMPLEMENTED_BUILD_SYSTEMS
from hammunition.deferral import Deferral
from hammunition.desktop import Desktop, SessionScan, describe_set
from hammunition.distro import Target
from hammunition.java import JRE_CANDIDATES, JavaProbe, jre_package
from hammunition.kernel import (
    DESCRIBE,
    KERNEL_REMOVAL,
    NO_MODULE_BUILD,
    USERSPACE_PATH,
    KernelProbe,
)
from hammunition.manifest.hardware import DeviceClass, DeviceManifest
from hammunition.manifest.schema import (
    UNPINNED_SHA256,
    AptInstall,
    AptRepo,
    BinaryInstall,
    ConfigFile,
    ConsentGate,
    DemTilesInstall,
    DerivedDataInstall,
    GitInstall,
    InstallBlock,
    KiwixBooksInstall,
    MwmRegionsInstall,
    NodeInstall,
    PackageManifest,
    ProfileManifest,
    RegionalDataInstall,
    RiskCategory,
    SourceInstall,
    Status,
    TopoQuadsInstall,
    VenvInstall,
    effective_binaries,
)
from hammunition.resolution import CatalogueMiss
from hammunition.state.log import TransactionLog
from hammunition.state.uninstall import deb_attributed
from hammunition.station import Station
from hammunition.userservice import PlannedUserService, plan_user_services, service_venv_dir

if TYPE_CHECKING:
    from hammunition.fetch import Fetcher
    from hammunition.manifest.schema import DataArtifact, RemoteArtifact
    from hammunition.resolution import ResolutionContext

__all__ = [
    "Blocker",
    "DebDependency",
    "DebDependencyError",
    "Deferral",
    "GroupMembership",
    "InstallPlan",
    "PlanError",
    "PlannedPackage",
    "RepoAddition",
    "cached_data_pin",
    "cached_remote",
    "catalogue_deferral",
    "compare_deb_versions",
    "deb_dependency_met",
    "deb_group_met",
    "deb_probe_names",
    "offline_network_blockers",
    "offline_payload_blockers",
    "parse_deb_dependencies",
    "parse_deb_depends",
    "payload_misses",
    "preflight_data",
    "resolve",
    "valid_deb_version",
]

REQUESTED_DIRECTLY = "requested"


@dataclass(frozen=True)
class Blocker:
    """One reason the transaction cannot proceed.

    ``remedy`` is separate from ``reason`` on purpose: an error that says what
    is wrong without saying what to do about it is the kind of check people
    learn to route around.
    """

    subject: str
    reason: str
    remedy: str | None = None

    def render(self) -> str:
        line = f"{self.subject}: {self.reason}"
        if self.remedy:
            line += f"\n    → {self.remedy}"
        return line


class PlanError(Exception):
    """Resolution failed. Carries every blocker, never just the first."""

    def __init__(self, blockers: Sequence[Blocker]) -> None:
        self.blockers = tuple(blockers)
        body = "\n".join(f"  {b.render()}" for b in self.blockers)
        noun = "problem" if len(self.blockers) == 1 else "problems"
        super().__init__(f"{len(self.blockers)} {noun} block this transaction:\n{body}")


@dataclass(frozen=True)
class GroupMembership:
    """A group the operator will be added to, and why it matters."""

    group: str
    user: str
    package: str
    description: str
    detail: str
    reverse_hint: str | None


@dataclass(frozen=True)
class FileCapability:
    """Capabilities explicitly opted in for one installed prefix binary."""

    path: Path
    capabilities: tuple[str, ...]
    package: str
    detail: str


@dataclass(frozen=True)
class PlannedPackage:
    """One catalog package, resolved against this target."""

    manifest: PackageManifest
    block: InstallBlock
    apt_packages: tuple[str, ...]
    """The distro packages this resolves to, ``depends`` included."""

    already_installed: tuple[str, ...] = ()
    requested_by: tuple[str, ...] = (REQUESTED_DIRECTLY,)

    build_only: tuple[str, ...] = ()
    """Of `apt_packages`, the ones that are `build_depends`.

    They are installed like any other apt package -- a build needs its
    toolchain present -- but they are not the software the operator asked for,
    and the plan says so. Reporting `libgtk2.0-dev` in the same breath as
    `glfer` would misdescribe what was installed and, later, what `uninstall`
    may safely remove."""

    displaces: tuple[str, ...] = ()
    """Declared `conflicts_with_repo_package` entries that are installed NOW.

    D-022: coexist and disclose, never remove silently. A source build that
    shadows the distro's binary on PATH is disclosed in the plan; a vendor
    .deb that would collide at the dpkg file level is refused before anything
    runs -- that split is decided at planning, from the same probe as
    everything else."""

    deb_installed: bool = False
    """A vendor .deb unit whose package dpkg still holds AND whose digest the
    transaction log attributes to this engine (#63). Nothing is planned for
    it. Installed but not ours is left to apt, which no-ops or upgrades as
    it sees fit; ours but removed by hand is installed again."""

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def outstanding(self) -> tuple[str, ...]:
        """apt packages not already present."""
        return tuple(p for p in self.apt_packages if p not in self.already_installed)


@dataclass(frozen=True)
class RepoAddition:
    """One third-party apt repository this transaction will add.  D-040.

    Decided at plan time from the file system and the apt probe: the unit's
    own packages have no candidate in the archive as configured, and the
    repository's two files are absent. A repository already present with
    this engine's content is not added again; one present with anybody
    else's content is a refusal, never an overwrite.
    """

    unit: str
    repo: AptRepo
    sources: str
    """``/etc/apt/sources.list.d/<name>.sources`` -- the path, for disclosure."""
    keyring: str
    """``/etc/apt/keyrings/<name>.gpg`` -- likewise."""
    packages: tuple[str, ...]
    """The apt packages this repository is expected to supply."""


def _opts_out_of_recommends(block: InstallBlock) -> bool:
    """Whether this block asked for ``--no-install-recommends``. D-052.

    True only of an apt block that said so in the manifest. A source or binary
    unit's ``build_depends`` are ordinary apt packages installed the ordinary
    way; nothing but an apt block can opt its own packages out."""
    install = block.install
    return isinstance(install, AptInstall) and not install.install_recommends


def _split_apt_sets(
    packages: Sequence[PlannedPackage],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The outstanding apt work, split into the two commands that will do it.

    One set per apt invocation, because that is what the plan simulates and
    what it prints. The opted-out set is narrowed by the default one: a
    package two units want, one of them normally, is installed normally."""
    default: set[str] = set()
    opted_out: set[str] = set()
    for planned in packages:
        target = opted_out if _opts_out_of_recommends(planned.block) else default
        target.update(planned.outstanding)
    return tuple(sorted(default)), tuple(sorted(opted_out - default))


def _merge_simulations(first: AptSimulation, second: AptSimulation) -> AptSimulation:
    """One reading of the whole apt step, from the per-set simulations.

    The removal check (D-022) and the vendor-`.deb` collision check both read
    a single simulation, and both must see everything the apt step does, so
    the two sets' answers are merged: refused if either was, every ``Remv``
    line from both, and the installs unioned per package so
    :meth:`AptSimulation.from_archive` still measures the target release the
    same way."""
    installs = dict(first.installs)
    for name, archives in second.installs.items():
        installs[name] = installs.get(name, frozenset()) | archives
    return AptSimulation(
        ok=first.ok and second.ok,
        installs=installs,
        removes={**first.removes, **second.removes},
        error="\n".join(e for e in (first.error, second.error) if e),
        release=first.release if first.release is not None else second.release,
    )


@dataclass(frozen=True)
class InstallPlan:
    """Everything that will happen, before any of it does."""

    target: Target
    packages: tuple[PlannedPackage, ...]
    group_memberships: tuple[GroupMembership, ...] = ()
    file_capabilities: tuple[FileCapability, ...] = ()
    consent_gates: tuple[tuple[str, ConsentGate], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    deferrals: tuple[Deferral, ...] = ()
    """Parts of the request that will not happen, and why. D-035."""

    desktops_read: frozenset[Desktop] | None = None
    """The desktops the session files offered, when a unit in the request is
    for particular desktops and they were read (D-060). None otherwise: the
    plan says nothing about desktops it did not decide anything against."""

    sessions_unrecognised: tuple[str, ...] = ()
    """Session files read beside ``desktops_read`` that named no desktop the
    catalog knows (``cosmic.desktop``). Shown so a graphical machine running
    one is not mistaken for a machine with no sessions."""

    config_files: tuple[tuple[str, ConfigFile, str], ...] = ()
    """(package, config file, rendered body) for every file that WILL be written."""

    user_services: tuple[PlannedUserService, ...] = ()
    """systemd user services this transaction will write and enable (D-073). A
    service whose station values are missing is a Deferral in ``deferrals``, not
    here; one skipped (flrig, VOX) is a note in ``notes``."""

    apt_release: str | None = None
    """``--target-release`` for the apt step, when the transaction resolves only
    from a release the target already installs from (D-038). None otherwise."""

    apt_from_release: tuple[str, ...] = ()
    """Of everything the apt step unpacks, the packages that come from
    ``apt_release`` and nowhere else -- measured by apt's simulation, disclosed
    in the plan."""

    apt_repos: tuple[RepoAddition, ...] = ()
    """Third-party repositories added before the apt step, each behind its
    own consent gate (D-040). Their packages are in ``apt_to_install`` but
    were not part of the plan-time simulate: apt cannot resolve from a
    repository it does not have yet, so the executor simulates again after
    ``apt-get update``."""

    @property
    def repo_supplied(self) -> frozenset[str]:
        """apt packages that only exist once a repository above is added."""
        return frozenset(p for addition in self.apt_repos for p in addition.packages)

    @property
    def apt_to_install(self) -> tuple[str, ...]:
        """Outstanding apt packages for the **default** apt command, sorted and
        de-duplicated: everything apt installs the way the distribution does,
        Recommends included."""
        return _split_apt_sets(self.packages)[0]

    @property
    def apt_to_install_no_recommends(self) -> tuple[str, ...]:
        """Outstanding apt packages for the second apt command, the one that
        carries ``--no-install-recommends``. D-052.

        A package named by an opted-out unit *and* by a unit that did not opt
        out stays in the default set: apt's defaults are what the catalog
        deviates from, never the other way round, and the narrower command is
        for packages nothing else in the transaction asked for normally."""
        return _split_apt_sets(self.packages)[1]

    @property
    def apt_no_recommends_units(self) -> tuple[str, ...]:
        """The units that asked for the second command, for the disclosure."""
        return tuple(
            sorted(
                planned.name
                for planned in self.packages
                if _opts_out_of_recommends(planned.block) and planned.outstanding
            )
        )

    @property
    def apt_packages_all(self) -> tuple[str, ...]:
        """Both apt sets together -- what this transaction installs with apt,
        whichever command does it. What needs lists fetched, what the log
        records, and what the effect check asks apt about afterwards."""
        default, no_recommends = _split_apt_sets(self.packages)
        return tuple(sorted({*default, *no_recommends}))

    @property
    def debconf_selections(self) -> tuple[str, ...]:
        """Preseed lines to apply before apt runs, from packages being installed.

        Only from packages with outstanding apt work — preseeding for a package
        that is already installed would answer a question that was answered at
        its install, and re-running would not change what is on disk. Order
        follows the packages; duplicates are dropped keeping first sight.
        """
        seen: dict[str, None] = {}
        for planned in self.packages:
            if planned.outstanding:
                for line in planned.manifest.debconf_selections:
                    seen.setdefault(line, None)
        return tuple(seen)

    @property
    def reconfigure_after(self) -> tuple[str, ...]:
        """Packages to dpkg-reconfigure after the apt install, from installs only."""
        seen: dict[str, None] = {}
        for planned in self.packages:
            if planned.outstanding:
                for pkg in planned.manifest.reconfigure_after:
                    seen.setdefault(pkg, None)
        return tuple(seen)

    @property
    def is_empty(self) -> bool:
        """Nothing to install and nothing to change — a legitimate outcome."""
        return (
            not self.apt_packages_all
            and not self.group_memberships
            and not self.file_capabilities
            and not self.apt_repos
        )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _expand_requests(
    names: Sequence[str],
    catalog: Mapping[str, PackageManifest],
    profiles: Mapping[str, ProfileManifest],
    blockers: list[Blocker],
) -> tuple[dict[str, list[str]], list[tuple[str, ConsentGate]]]:
    """Turn requested names into ``package -> who asked for it``.

    A name that is both a profile and a package would be ambiguous; the loader
    keeps the two namespaces separate and nothing in the catalog collides, so
    profiles are checked first and a collision is reported rather than
    silently preferred.
    """
    wanted: dict[str, list[str]] = {}
    gates: list[tuple[str, ConsentGate]] = []

    for name in names:
        if name in profiles and name in catalog:
            blockers.append(
                Blocker(
                    subject=name,
                    reason="is the name of both a profile and a package, so the request is ambiguous",
                    remedy="rename one of them in the catalog; the engine will not guess",
                )
            )
            continue
        if name in profiles:
            profile = profiles[name]
            if profile.consent is not None:
                gates.append((profile.name, profile.consent))
            for package in profile.packages:
                wanted.setdefault(package, []).append(f"profile {profile.name}")
            continue
        if name in catalog:
            wanted.setdefault(name, []).append(REQUESTED_DIRECTLY)
            continue
        blockers.append(
            Blocker(
                subject=name,
                reason="is not a package or profile in the catalog",
                remedy="`hammunition list` shows everything the catalog contains",
            )
        )
    return wanted, gates


def _pull_catalog_dependencies(
    wanted: dict[str, list[str]], catalog: Mapping[str, PackageManifest]
) -> None:
    """Follow ``depends`` entries that name other catalog packages.

    ``depends`` holds names in two namespaces, which is a wart in the schema
    rather than a design: every entry in the catalog today (``libhamlib4``,
    ``gnuradio``) is a distro package name, and D-016's whole evidence table is
    about distro package names in dependency lists. But a manifest may
    legitimately depend on another manifest, so a name the catalog knows is
    treated as a catalog package and pulled in, and everything else is verified
    against apt by :func:`resolve`. Both are checked; neither is assumed.
    """
    queue = list(wanted)
    while queue:
        current = queue.pop()
        manifest = catalog.get(current)
        if manifest is None:
            continue
        for dependency in manifest.depends:
            if dependency in catalog and dependency not in wanted:
                wanted[dependency] = [f"dependency of {current}"]
                queue.append(dependency)


def _order(
    names: Iterable[str], catalog: Mapping[str, PackageManifest], blockers: list[Blocker]
) -> list[str]:
    """Order by ``after``, which is sequencing and not dependency (D-003 shape).

    ``wsjtx-improved`` is ``after: [wsjtx]`` because both builds emit a binary
    called ``wsjtx`` and the later one must win. An ``after`` naming a package
    not in this transaction is not an error — it is a constraint that is
    already satisfied by absence.
    """
    remaining = sorted(names)
    present = set(remaining)
    ordered: list[str] = []
    placed: set[str] = set()

    while remaining:
        ready = [
            name
            for name in remaining
            if all(
                predecessor in placed
                for predecessor in catalog[name].after
                if predecessor in present
            )
        ]
        if not ready:
            blockers.append(
                Blocker(
                    subject=", ".join(remaining),
                    reason="`after` constraints form a cycle, so no install order exists",
                    remedy="break the cycle in the catalog; `after` is ordering, not dependency",
                )
            )
            ordered.extend(remaining)
            break
        ordered.extend(ready)
        placed.update(ready)
        remaining = [name for name in remaining if name not in placed]

    return ordered


def _build_depends_of(manifest: PackageManifest) -> set[str]:
    """Every apt name that appears as a build dependency anywhere in *manifest*.

    Used only to label a missing package in a blocker, so an operator is told
    that `fftw2` is something glfer needs to *build* rather than something it
    needs to run. Across all blocks rather than the resolved one, because the
    label is cosmetic and a name that is a build dependency on any target is a
    build dependency for the purpose of that sentence.
    """
    return {name for block in manifest.install for name in block.build_depends}


def _check_engine_capability(
    manifest: PackageManifest,
    block: InstallBlock,
    *,
    repos_supported: bool = False,
    applicable_repos: Sequence[AptRepo] | None = None,
) -> list[Blocker]:
    """Refuse, by name, anything this engine build cannot actually do.

    Never a warning and never a skip. CLAUDE.md forbids a shim that makes an
    unsupported combination appear to work, and reporting a package as
    installable when the backend for it does not exist is that shim wearing a
    different hat.
    """
    found: list[Blocker] = []
    method = block.install.method

    if method not in IMPLEMENTED_METHODS:
        found.append(
            Blocker(
                subject=manifest.name,
                reason=(
                    f"resolves to the {method!r} backend on this target, and this engine "
                    f"build implements only {', '.join(sorted(IMPLEMENTED_METHODS))}"
                ),
                remedy=(
                    f"the {method!r} backend is measured and scheduled for 1.0 "
                    f"(DESIGN.md §6); it is not written yet"
                ),
            )
        )

    if isinstance(block.install, VenvInstall):
        zero = [
            line.split()[0]
            for line in block.install.requirements
            if f"--hash=sha256:{UNPINNED_SHA256}" in line
        ]
        if zero:
            found.append(
                Blocker(
                    subject=manifest.name,
                    reason=(
                        f"is unpinned: {zero[0]} carries the all-zero placeholder digest, "
                        f"which no file can match"
                    ),
                    remedy=(
                        "the artifact has not been published and pinned yet; replace the "
                        "digest with what `sha256sum` prints for the release file"
                    ),
                )
            )

    if isinstance(block.install, SourceInstall | GitInstall) and method in IMPLEMENTED_METHODS:
        # D-016: everything the run cannot do is found before anything is done.
        # The backend raises on these too, but discovering them after the apt
        # step has already installed a toolchain is exactly the fix-one-re-run
        # shape resolution exists to prevent.
        source: SourceInstall | GitInstall = block.install
        if source.build_system not in IMPLEMENTED_BUILD_SYSTEMS:
            found.append(
                Blocker(
                    subject=manifest.name,
                    reason=(
                        f"builds with {source.build_system!r}, which this engine build "
                        f"does not implement (it implements "
                        f"{', '.join(sorted(IMPLEMENTED_BUILD_SYSTEMS))})"
                    ),
                    remedy=(
                        "no manifest in the catalog uses it, so this is an unimplemented "
                        "gap rather than a regression (D-014)"
                    ),
                )
            )
        if isinstance(source, SourceInstall | GitInstall):
            undiffed = [p.file for p in source.patches if not p.unified_diff]
            if undiffed:
                found.append(
                    Blocker(
                        subject=manifest.name,
                        reason=(
                            f"declares patches for {', '.join(undiffed)} with no "
                            f"unified_diff to apply"
                        ),
                        remedy=(
                            "a description alone cannot be applied; building unpatched "
                            "source would produce a binary the manifest does not describe"
                        ),
                    )
                )

    if isinstance(block.install, BinaryInstall):
        # The format is checked here rather than only in the backend, so an
        # AppImage is a plan-time refusal naming the gap rather than a failure
        # partway through a transaction.
        if block.install.format not in IMPLEMENTED_BINARY_FORMATS:
            found.append(
                Blocker(
                    subject=manifest.name,
                    reason=(
                        f"is a {block.install.format!r} artifact, which this engine "
                        f"build cannot install"
                    ),
                    remedy=(
                        "AppImage is post-1.0 (SCOPE.md); it is refused by name rather "
                        "than skipped, so the gap stays visible"
                    ),
                )
            )
        elif (
            block.install.format != "deb"
            and not effective_binaries(manifest, block)
            and not block.install.install_tree
            and not block.install.placements
            and block.install.devctl_helper is None
        ):
            found.append(
                Blocker(
                    subject=manifest.name,
                    reason=(
                        "is a prebuilt archive or executable that names no `binaries`, "
                        "no `install_tree`, no `placements` and no `devctl_helper`"
                    ),
                    remedy=(
                        "declare what the artifact contains and what it should be called; "
                        "unpacking it otherwise installs nothing while reporting success"
                    ),
                )
            )

    wanted_repos = list(manifest.apt_repos if applicable_repos is None else applicable_repos)
    if wanted_repos and not repos_supported:
        # The backend exists (D-040); a caller that plans without one -- a
        # test, a bare `resolve` -- still gets the named refusal rather than
        # a plan that silently assumes the repository will appear.
        names = ", ".join(repo.name for repo in wanted_repos)
        found.append(
            Blocker(
                subject=manifest.name,
                reason=f"requires third-party apt repositories ({names}) and this plan has no repository backend",
                remedy=(
                    "adding a repository with a pinned signing key is a disclosed system "
                    "modification of its own; plan with an AptRepoBackend, or install it by "
                    "hand"
                ),
            )
        )

    # `config_files` is deliberately NOT a blocker any more. A templated file
    # whose station values are unknown becomes a Deferral: the package installs
    # and the file is reported as not written. See D-035 and `_plan_config`.

    for modification in manifest.system_modifications:
        if modification.kind not in IMPLEMENTED_MODIFICATIONS:
            found.append(
                Blocker(
                    subject=manifest.name,
                    reason=f"needs a {modification.kind!r} system modification this engine cannot perform",
                    remedy=modification.description.strip(),
                )
            )

    return found


def operator_home(user: str) -> Path | None:
    """The home a manifest's ``~/`` config path means: the operator's, from the
    account database -- never ``$HOME``, which sudo resets to ``/root``. None
    for no operator, root, or an account this machine does not have."""
    if not user:
        return None
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return None
    if entry.pw_uid == 0 or entry.pw_dir in ("", "/"):
        return None
    return Path(entry.pw_dir)


def _plan_config(
    manifest: PackageManifest, station: Station, home: Path | None = None
) -> tuple[list[tuple[str, ConfigFile, str]], list[Deferral]]:
    """Render this manifest's templated config, or defer what cannot be rendered.

    Every file is all-or-nothing: a config file written with some values
    substituted and others left as `{station.callsign}` is worse than no file,
    because it looks configured. So a file missing one value is deferred whole.

    A ``~/`` path is resolved against *home*, the operator's (``operator_home``);
    with none, the file is deferred -- root's home is nobody's station.
    """
    writable: list[tuple[str, ConfigFile, str]] = []
    deferred: list[Deferral] = []
    for config in manifest.config_files:
        if config.in_home:
            if home is None:
                deferred.append(
                    Deferral(
                        subject=manifest.name,
                        what=f"will not write {config.path}",
                        why="it belongs in the operator's home and no operator (other than root) was identified",
                        remedy=(
                            "run the install as yourself, or pass --user <name>. The package "
                            "itself installs either way."
                        ),
                    )
                )
                continue
            config = config.model_copy(update={"path": str(home / config.path[2:])})
        wanted = config.station_variables
        unknown = station.missing(wanted)
        if unknown:
            deferred.append(
                Deferral(
                    subject=manifest.name,
                    what=f"will not write {config.path}",
                    why="station values not set: " + ", ".join(unknown),
                    remedy=(
                        "run `hammunition station set --"
                        + " --".join(f"{v.replace('_', '-')} <value>" for v in unknown)
                        + "` and install again, or write the file by hand. The package "
                        "itself installs either way."
                    ),
                )
            )
            continue
        unusable = station.unusable(wanted)
        if unusable:
            deferred.append(
                Deferral(
                    subject=manifest.name,
                    what=f"will not write {config.path}",
                    why="; ".join(unusable),
                    remedy=(
                        "the value is set but this file cannot use it, and nothing is "
                        "changed to make it fit: write the file by hand. The package "
                        "itself installs either way."
                    ),
                )
            )
            continue
        body = config.template
        patterns = list(config.skip_if_present)
        for variable in wanted:
            value = station.get(variable)
            assert value is not None  # `missing` above proved it
            body = body.replace("{station." + variable + "}", value)
            # A value is matched literally: a callsign is data, never a pattern.
            patterns = [p.replace("{station." + variable + "}", re.escape(value)) for p in patterns]
        if patterns:
            config = config.model_copy(update={"skip_if_present": patterns})
        writable.append((manifest.name, config, body))
    return writable, deferred


def _status_blocker(manifest: PackageManifest) -> Blocker | None:
    """A package we have recorded as not working does not get installed quietly."""
    if manifest.status is Status.supported:
        return None
    verdict = manifest.status_verdict.value if manifest.status_verdict else "unrecorded"
    when = manifest.status_date.isoformat() if manifest.status_date else "no date"
    return Blocker(
        subject=manifest.name,
        reason=f"is marked {manifest.status.value} ({verdict}, {when}): {manifest.status_reason}",
        remedy=(
            "if this verdict is stale, re-test it and update the manifest — "
            "an inherited verdict counts against us (PARITY-POLICY.md, M5)"
        ),
    )


#: A Debian version string's upstream major and minor: an optional epoch, then
#: two dotted numbers. ``20.19.2+dfsg1-1`` -> (20, 19);
#: ``1:18.19.1+dfsg-6ubuntu5`` -> (18, 19). The minor matters: ``require()``
#: of an ES module works from 20.19 and not from 20.18, and openhamclock's
#: server needs it.
_MAJOR_MINOR = re.compile(r"^(?:\d+:)?(\d+)\.(\d+)")


#: D-071: a converter whose output needs a minimum version of the program it
#: runs: (the distribution package, its MAJOR.MINOR floor, what that version
#: is the first to do). The engine owns these, as it owns each converter's argv.
CONVERTER_FLOORS: dict[str, tuple[str, tuple[int, int], str]] = {
    "tilemaker-pmtiles": ("tilemaker", TILEMAKER_FLOOR, "writes PMTiles"),
}


def node_version(version: str) -> tuple[int, int] | None:
    match = _MAJOR_MINOR.match(version.strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _check_node_floor(
    manifest: PackageManifest, install: NodeInstall, nodejs: AptPackageState | None
) -> Blocker | str:
    """The D-037 gate: a Blocker below the floor, a disclosure note at or above it.

    The version that counts is the one the run will have: what is installed
    now, else what apt would install. ``nodejs`` is in the transaction's own
    tool dependencies, so an archive with no candidate at all is already a
    no-candidate blocker by the time this runs; that case is repeated here in
    D-037's words rather than left to the generic one.
    """
    floor = install.node_min_version
    floor_parsed = node_version(floor)
    assert floor_parsed is not None  # the schema's pattern guarantees MAJOR.MINOR
    version = nodejs.installed or nodejs.candidate if nodejs is not None else None
    found = node_version(version) if version else None
    source = "installed" if nodejs is not None and nodejs.installed else "the archive's candidate"
    if version is None or found is None:
        return Blocker(
            subject=manifest.name,
            reason=(
                f"is a Node.js application needing Node {floor} or newer, and this "
                f"distribution offers no nodejs package"
            ),
            remedy=(
                "Node is only ever taken from the distribution, never fetched (D-037); "
                "a release of this distribution that carries nodejs, or skip this unit"
            ),
        )
    if found < floor_parsed:
        return Blocker(
            subject=manifest.name,
            reason=(
                f"is a Node.js application needing Node {floor} or newer, and this "
                f"distribution's nodejs is {version} ({source}): "
                f"{found[0]}.{found[1]} is below the floor"
            ),
            remedy=(
                "Node is only ever taken from the distribution, never fetched (D-037); "
                "a newer release of this distribution carries a newer nodejs, or skip "
                "this unit"
            ),
        )
    return (
        f"{manifest.name} is a Node.js application: it needs Node {floor} or newer "
        f"(this machine: nodejs {version}, {source}) and its build fetches its "
        f"dependency closure from registry.npmjs.org, each package verified against "
        f"the sha512 pins in the lock file inside the sha256-verified source archive. "
        f"No package lifecycle scripts run during the build."
    )


def _check_java_floor(
    manifest: PackageManifest,
    floor: int,
    java: JavaProbe | None,
    states: Mapping[str, AptPackageState],
    notes: list[str],
) -> Blocker | None:
    """The Java gate: a Blocker below the floor, ``None`` when it is met or
    will be met by this transaction's own ``depends`` (a note says so).

    A concrete ``openjdk-N-jre*`` package in ``depends`` with N at or above the
    floor and a candidate in the archive satisfies the floor in this very
    transaction, however old the Java on the machine now. A metapackage
    (``default-jre*``) does not say which Java it brings: with a Java already
    installed the measured one stands, and with none the major cannot be known
    before the install, which is disclosed rather than refused (nothing would
    ever pass otherwise on a clean machine).
    """
    concrete = [
        (int(m.group(1)), d)
        for d in manifest.depends
        if (m := re.fullmatch(r"openjdk-(\d+)-jre(?:-headless)?", d))
    ]
    meets = sorted((n, d) for n, d in concrete if n >= floor and d in states and states[d].known)
    if meets:
        n, d = meets[0]
        notes.append(
            f"{manifest.name} needs Java {floor} or newer; its `depends` installs {d} "
            f"in this transaction, which provides Java {n}."
        )
        return None
    metapackage = any(d in {"default-jre", "default-jre-headless"} for d in manifest.depends)
    if java is None:
        notes.append(
            f"{manifest.name} needs Java {floor} or newer, and the machine's Java was not "
            f"read, so it cannot be checked before this plan executes."
        )
        return None
    found = java.major
    if found is not None and found >= floor:
        notes.append(
            f"{manifest.name} needs Java {floor} or newer (this machine: {java.version_line})."
        )
        return None
    if found is None and java.version_line == "" and metapackage:
        notes.append(
            f"{manifest.name} needs Java {floor} or newer and this machine has no java yet: "
            f"its `depends` installs the distribution's default JRE, whose major is only "
            f"known once it is installed. Check with `java -version` afterwards; below "
            f"{floor}, install a newer JRE by hand."
        )
        return None
    newer = next(
        (
            jre_package(n)
            for n in JRE_CANDIDATES
            if n >= floor and jre_package(n) in states and states[jre_package(n)].known
        ),
        None,
    )
    have = (
        f"this machine's java is {java.version_line} (Java {found}), below the floor"
        if found is not None
        else f"{java.reason}"
    )
    if newer is not None:
        remedy = (
            f"install {newer} from this distribution's archive (`sudo apt-get install "
            f"{newer}`) and select it with `update-alternatives --config java`; nothing "
            f"is fetched to meet the floor (D-037)"
        )
    else:
        remedy = (
            f"a release of this distribution that carries Java {floor} or newer, or skip "
            f"this unit; nothing is fetched to meet the floor (D-037)"
        )
    return Blocker(
        subject=manifest.name,
        reason=f"needs Java {floor} or newer, and {have}",
        remedy=remedy,
    )


def _resolve_from_installed_release(
    apt: AptBackend,
    packages: Sequence[str],
    failed: AptSimulation,
    *,
    no_recommends: bool = False,
) -> tuple[AptSimulation, str, tuple[str, ...]] | Blocker:
    """The D-038 retry: when apt refuses because it would have to downgrade
    something already installed, ask again from the release that installed it.

    A clean Parrot 7.3 has 197 of its 3,801 packages from `parrot-backports`,
    pinned at 599 against the archive's 600 -- newer Qt, GTK, curl, ALSA for
    the desktop. Every `-dev` package for those libraries carries an exact-
    version dependency, so the archive's default `libcurl4-openssl-dev` (8.14)
    needs `libcurl4t64` 8.14 where 8.21 is installed, and apt will not
    downgrade a library to build against it. Nor should it. The `-dev` at 8.21
    is in backports beside the library; `--target-release parrot-backports`
    is apt's own way to prefer it, and it is what a Parrot user types.

    So: the packages apt refused to downgrade name the release, the release
    is tried once, and the result is either a resolved transaction with the
    packages it takes from that release listed by name -- the plan discloses
    them, the operator sees them before anything runs -- or the same refusal,
    reported with apt's own words. Two releases, or none, is a refusal too:
    nothing is guessed. The transaction is never widened silently: `-t`
    prefers that release only for packages this transaction installs, and the
    simulation says exactly which.
    """
    culprits = downgrades_refused(failed.error)
    releases = sorted({r for p in culprits if (r := apt.installed_archive(p)) is not None})
    refusal = Blocker(
        subject="apt",
        reason=f"cannot resolve this transaction as one apt-get install:\n{_indent(failed.error)}",
        remedy=(
            "the packages named above are the chain; `apt-get install --simulate` "
            "with the same list reproduces it. Leave out the unit that needs the "
            "unsatisfiable package, or fix the machine's apt state -- the plan "
            "will not start a transaction apt has already said it cannot finish (D-016)"
        ),
    )
    if len(releases) != 1:
        return refusal
    release = releases[0]
    retried = apt.simulate(packages, release=release, no_recommends=no_recommends)
    if not retried.ok:
        return refusal
    return retried, release, retried.from_archive(release)


def _indent(text: str, prefix: str = "      ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _target_deferral(
    name: str, wanted: Mapping[str, Sequence[str]], why: str, remedy: str | None = None
) -> Deferral:
    """Q-017: a profile member this target does not offer, deferred by name.

    Only for a member the operator did not ask for by name -- ``wanted`` says
    who asked -- and only for a reason true of the *target*: no candidate on
    this release for the unit's own packages, no install block for this
    distro or architecture, a Node floor the distribution's package is below.
    The caller decides the reason qualifies; this only phrases it.
    """
    via = ", ".join(w for w in wanted[name] if w != REQUESTED_DIRECTLY)
    return Deferral(
        subject=name,
        what=f"will not be installed ({via})",
        why=why,
        remedy=(
            f"the rest installs without it; `hammunition install {name}` shows the "
            f"refusal in full, and a release that carries it needs no change here"
            if remedy is None
            else f"the rest installs without it; {remedy}"
        ),
        kind="package",
    )


def _desktop_reason(manifest: PackageManifest, scan: SessionScan) -> str:
    """``for KDE Plasma; this machine has no KDE Plasma session (it has: Xfce)``.

    Three cases for the parenthesis, because "none" said of a graphical
    machine whose only desktop the catalog does not name (COSMIC, Sway) is
    false: recognised desktops, session files that named none of them, and no
    session files at all."""
    want = frozenset(manifest.desktops or ())
    listed = describe_set(want)
    either = " or ".join(listed.split(", "))
    if scan.desktops:
        it_has = f"it has: {describe_set(scan.desktops)}"
    elif scan.unrecognised:
        it_has = f"its session files name none the catalog knows: {', '.join(scan.unrecognised)}"
    else:
        it_has = "it has no session files"
    return f"for {listed}; this machine has no {either} session ({it_has})"


def _serving_alternative(
    manifest: PackageManifest, have: frozenset[Desktop], catalog: Mapping[str, PackageManifest]
) -> tuple[str, frozenset[Desktop]] | None:
    """The declared alternative and the desktops of this machine it serves, if any."""
    name = manifest.desktop_alternative
    other = catalog.get(name) if name is not None else None
    if name is None or other is None:
        return None
    # `other.desktops is None` cannot reach here from a loaded catalog:
    # load_catalog refuses an alternative for every desktop. Kept so a
    # hand-built catalog in a test still answers, not so anything relies on it.
    serves = have if other.desktops is None else have & frozenset(other.desktops)
    return (name, serves) if serves else None


def _desktop_deferral(
    manifest: PackageManifest,
    scan: SessionScan,
    catalog: Mapping[str, PackageManifest],
    wanted: Mapping[str, Sequence[str]],
) -> Deferral:
    """D-060: a profile member for a desktop this machine has no session for.

    The D-039 shape, with its own remedy: the generic one says a release that
    carries the unit needs no change, and a release is not what is missing.
    """
    via = [w for w in wanted[manifest.name] if w != REQUESTED_DIRECTLY]
    profiles = [w.removeprefix("profile ") for w in via if w.startswith("profile ")]
    again = f"`hammunition install {profiles[0]}`" if profiles else "the same install"
    remedy = (
        f"the rest installs without it; on a machine that gains a "
        f"{' or '.join(describe_set(frozenset(manifest.desktops or ())).split(', '))} session, "
        f"{again} again picks it up"
    )
    alternative = _serving_alternative(manifest, scan.desktops, catalog)
    if alternative is not None:
        remedy += f". For {describe_set(alternative[1])}, `hammunition install {alternative[0]}`"
    return Deferral(
        subject=manifest.name,
        what=f"will not be installed ({', '.join(via)})",
        why=_desktop_reason(manifest, scan),
        remedy=remedy,
        kind="package",
    )


def _desktop_blocker(
    manifest: PackageManifest, scan: SessionScan, catalog: Mapping[str, PackageManifest]
) -> Blocker:
    """D-060: the same condition, for a unit the operator typed (D-039)."""
    want = describe_set(frozenset(manifest.desktops or ()))
    alternative = _serving_alternative(manifest, scan.desktops, catalog)
    if alternative is not None:
        remedy = (
            f"`hammunition install {alternative[0]}` is the one for "
            f"{describe_set(alternative[1])}; {manifest.name} needs a {want} session "
            f"installed first"
        )
    else:
        remedy = (
            f"{manifest.name} does nothing without a {want} session; install one first "
            f"and plan again, or leave {manifest.name} out"
        )
    return Blocker(
        subject=manifest.name, reason=f"is {_desktop_reason(manifest, scan)}", remedy=remedy
    )


def _reads_map_regions(
    block: InstallBlock, catalog: Mapping[str, PackageManifest], target: Target
) -> bool:
    """An ``osm-regions``, ``dem-tiles`` or ``topo-quads`` block (D-061, D-068:
    its tiles and sheets follow the regions), an ``mwm-regions`` one (D-069:
    CoMaps' maps for them), or a ``derived`` one converting such a unit's data."""
    install = block.install
    if isinstance(
        install, RegionalDataInstall | DemTilesInstall | TopoQuadsInstall | MwmRegionsInstall
    ):
        return True
    if isinstance(install, DerivedDataInstall):
        source = catalog.get(install.source)
        if source is None:
            return False
        source_block = source.resolve(target.distro, target.version, target.arch)
        return source_block is not None and isinstance(
            source_block.install, RegionalDataInstall | DemTilesInstall | TopoQuadsInstall
        )
    return False


NO_MAP_REGIONS = "no map regions set"
MAP_REGIONS_REMEDY = (
    "run `hammunition station set --map-regions <region>[,<region>…]` and install again"
)


def _map_regions_deferral(name: str) -> Deferral:
    """D-057: the shape `_plan_config` uses for a missing station value."""
    return Deferral(
        subject=name,
        what="will not be installed: it is map data for regions you have not chosen",
        why=NO_MAP_REGIONS,
        remedy=f"{MAP_REGIONS_REMEDY}. Everything else installs either way.",
        kind="package",
    )


NO_REFERENCE_BOOKS = "no reference books chosen"
REFERENCE_BOOKS_REMEDY = (
    "run `hammunition station set --reference-books <id>[,<id>…]` and install again; "
    "`hammunition reference books` lists the ids"
)


def _reference_books_deferral(name: str) -> Deferral:
    """D-066: the books wait for a choice; the readers install regardless."""
    return Deferral(
        subject=name,
        what="will not be installed: it is the reference books you have not chosen",
        why=NO_REFERENCE_BOOKS,
        remedy=f"{REFERENCE_BOOKS_REMEDY}. Everything else installs either way.",
        kind="package",
    )


def _plan_repos(
    manifest: PackageManifest,
    applicable: Sequence[AptRepo],
    install: AptInstall,
    repos: AptRepoBackend,
    missing: Sequence[str],
    own: set[str],
    notes: list[str],
) -> list[RepoAddition] | Blocker:
    """D-040: decide whether this unit's declared repositories are added.

    ``applicable`` is the declared repositories whose ``when`` matches the
    target; the rest are never mentioned to apt. Returns the additions --
    possibly none -- or one blocker. The
    repositories are added only when the unit's *own* apt packages have no
    candidate: a target that already carries them is left as it is and told
    so (D-022), and a missing ``depends`` is never a reason to add somebody
    else's repository. Each repository is read from the file system:

    * absent -- added, and its packages leave the plan-time simulate, which
      cannot see a repository apt does not have yet;
    * ours -- both files carry exactly what this engine writes, so the
      candidate should exist and does not: the archive index is stale, and
      the fix is a refresh of the lists, never a second copy of the file;
    * foreign -- a file of the same name with anybody else's content, which
      is never overwritten.
    """
    own_missing = sorted(p for p in missing if p in own)
    if not own_missing:
        for repo in applicable:
            if repos.state(repo, unit=manifest.name) is RepoState.absent:
                notes.append(
                    f"{manifest.name}: the archive already offers {', '.join(install.packages)}; "
                    f"the {repo.name} repository the manifest declares is not added (D-022)"
                )
        return []
    additions: list[RepoAddition] = []
    for repo in applicable:
        files = repos.files_for(repo)
        state = repos.state(repo, unit=manifest.name)
        if state is RepoState.foreign:
            return Blocker(
                subject=manifest.name,
                reason=(
                    f"{files.sources} or {files.keyring} already exists and is not this "
                    f"engine's work; apt has no candidate for {', '.join(own_missing)}"
                ),
                remedy=(
                    f"a file another tool or operator wrote is never overwritten (D-040): "
                    f"inspect both, and either remove them or install {manifest.name} by hand"
                ),
            )
        if state is RepoState.ours:
            return Blocker(
                subject=manifest.name,
                reason=(
                    f"the {repo.name} repository is already configured at {files.sources} "
                    f"and apt still has no candidate for {', '.join(own_missing)}"
                ),
                remedy=(
                    "the package index is stale or the repository does not carry the "
                    "package for this release; run `sudo apt-get update`, read what apt "
                    "says about the source, and plan again (this run's own refresh comes "
                    "after the plan, so it cannot answer this)"
                ),
            )
        additions.append(
            RepoAddition(
                unit=manifest.name,
                repo=repo,
                sources=str(files.sources),
                keyring=str(files.keyring),
                packages=tuple(own_missing),
            )
        )
    return additions


def _deb_installed(
    block: InstallBlock, states: Mapping[str, AptPackageState], log: TransactionLog | None
) -> bool:
    """dpkg holds the package now, and the log says this digest put it there."""
    method = block.install
    if not isinstance(method, BinaryInstall) or method.format != "deb" or not method.deb_package:
        return False
    state = states.get(method.deb_package)
    if state is None or not state.is_installed or log is None:
        return False
    return deb_attributed(log, sha256=method.artifact.sha256, deb_package=method.deb_package)


# ---------------------------------------------------------------------------
# Offline resolution (#381): what the Bunker's catalogue answers for
# ---------------------------------------------------------------------------

OFFLINE_APT_REMEDY = (
    "install it while the apt archive is reachable, or wait for apt served by the Bunker "
    "(phase 2 of #381); an offline run never calls apt-get update or fetches a package"
)

OFFLINE_PHASE_2 = (
    "serve this unit's dependencies from the Bunker (phase 2 of #381), or install it while "
    "online; phase 1 carries pinned payloads and map data only"
)

OFFLINE_PAYLOAD_REMEDY = (
    "run it once online so the verified download is cached, or wait for the Bunker route "
    "for source and binary payloads (#381)"
)

OFFLINE_ISOLATION_REMEDY = (
    "install bubblewrap (bwrap) and make sure it can create user namespaces while online, "
    "or run this unit online"
)

CATALOGUE_REMEDY = "populate this selection on the Bunker, or retry with the publisher reachable"


def _offline_apt_blockers(
    resolved: Sequence[tuple[PackageManifest, InstallBlock, tuple[str, ...], tuple[str, ...]]],
    outstanding: set[str],
) -> list[Blocker]:
    """One blocker per unit that needs an apt package this machine lacks."""
    out: list[Blocker] = []
    for manifest, _block, packages, _build_only in resolved:
        missing = [p for p in packages if p in outstanding]
        if not missing:
            continue
        shown = ", ".join(missing[:6]) + (
            f" and {len(missing) - 6} more" if len(missing) > 6 else ""
        )
        out.append(
            Blocker(
                subject=manifest.name,
                reason=f"offline: needs apt package(s) this machine does not have: {shown}",
                remedy=OFFLINE_APT_REMEDY,
            )
        )
    return out


def _offline_repo_blockers(additions: Sequence[RepoAddition]) -> list[Blocker]:
    """A third-party apt repository cannot be added without ``apt-get update``."""
    return [
        Blocker(
            subject=addition.unit,
            reason=(
                f"offline: it adds the apt repository {addition.repo.name}, whose index "
                "only `apt-get update` can fetch"
            ),
            remedy=OFFLINE_APT_REMEDY,
        )
        for addition in additions
    ]


def offline_network_blockers(plan: InstallPlan, built: frozenset[str]) -> list[Blocker]:
    """Steps an offline run would have to send to the network, refused by name.

    A pip virtual environment is resolved against PyPI on every install, a git
    block with ``build_python`` lines installs them with pip, and a Node build
    runs ``npm ci``: none of them has a Bunker route in phase 1. A unit whose
    build is already attributed at its pin (``built``) plans none of its build
    steps, so it is not named."""
    out: list[Blocker] = []
    for unit in plan.packages:
        if unit.name in built:
            continue
        block = unit.block.install
        if isinstance(block, VenvInstall):
            tool = "pip"
        elif isinstance(block, GitInstall) and block.build_python:
            tool = "pip (build_python)"
        elif isinstance(block, NodeInstall):
            tool = "npm"
        else:
            continue
        out.append(
            Blocker(
                subject=unit.name,
                reason=f"offline: its install runs {tool}, which fetches from a package index",
                remedy=OFFLINE_PHASE_2,
            )
        )
    return out


def catalogue_deferral(unit: PlannedPackage, exc: CatalogueMiss) -> Deferral:
    """What an unresolved catalogue entry does to *unit*: refuse it by name when
    the operator typed it, otherwise defer the whole unit (D-039)."""
    if REQUESTED_DIRECTLY in unit.requested_by:
        raise PlanError([Blocker(unit.name, str(exc), CATALOGUE_REMEDY)])
    return Deferral(
        unit.name, "will not install this unit this run", str(exc), CATALOGUE_REMEDY, "package"
    )


def cached_remote(fetcher: Fetcher, artifact: RemoteArtifact) -> bool:
    """Whether *artifact*'s verified bytes are already in the artifact cache.

    True only on an exact sha256 match of the content-addressed file; it never
    fetches, and a symlink or unreadable file is not a hit."""
    from hammunition.fetch import _digest_file

    path = fetcher.path_for(artifact)
    try:
        return path.is_file() and not path.is_symlink() and _digest_file(path) == artifact.sha256
    except OSError:
        return False


def cached_data_pin(fetcher: Fetcher, pin: DataArtifact) -> bool:
    """Whether *pin*'s verified bytes are already in the artifact cache: the
    exact size and sha256 of the content-addressed file."""
    from hammunition.manifest.schema import RemoteArtifact

    remote = RemoteArtifact(url=pin.url, sha256=pin.sha256)
    try:
        return (
            cached_remote(fetcher, remote) and fetcher.path_for(remote).stat().st_size == pin.size
        )
    except OSError:
        return False


def _remote_artifacts(node: object) -> list[RemoteArtifact]:
    """Every pinned download a block declares, wherever it sits in the block."""
    from pydantic import BaseModel

    from hammunition.manifest.schema import RemoteArtifact

    if isinstance(node, RemoteArtifact):
        return [node]
    found: list[RemoteArtifact] = []
    if isinstance(node, BaseModel):
        for value in vars(node).values():
            found.extend(_remote_artifacts(value))
    elif isinstance(node, list | tuple):
        for value in node:
            found.extend(_remote_artifacts(value))
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(_remote_artifacts(value))
    return found


class DebDependencyError(ValueError):
    """A Depends/Pre-Depends field that does not parse. Always a refusal: an
    unparsed relation is never read as "no relation"."""


@dataclass(frozen=True)
class DebDependency:
    """One alternative of a ``Depends`` group: a package name, the architecture
    qualifier it carries (``any``, ``native`` or an architecture), and the
    version relation it needs (``<<``, ``<=``, ``=``, ``>=``, ``>>``)."""

    name: str
    relation: str | None = None
    version: str | None = None
    arch: str | None = None

    def text(self) -> str:
        base = self.name if self.arch is None else f"{self.name}:{self.arch}"
        return base if self.relation is None else f"{base} ({self.relation} {self.version})"


#: What a qualifier after the colon may be: ``any``, ``native`` or a Debian
#: architecture name (lowercase letters, digits and hyphens, starting with one).
_DEB_ARCH = re.compile(r"[a-z0-9][a-z0-9\-]*", re.ASCII)

_DEB_ALTERNATIVE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9+.\-]+)"
    r"(?::(?P<arch>[^\s()|,:]*))?"
    r"(?:\s*\(\s*(?P<op><<|<=|>=|>>|=|<|>)\s*(?P<version>[^\s()]+)\s*\))?",
    re.ASCII,
)
_DEB_REVISION = re.compile(r"[A-Za-z0-9+.~]+")
_DEB_UPSTREAM = re.compile(r"[0-9][A-Za-z0-9.+~\-:]*")


def _deb_split(version: str) -> tuple[int, str, str] | None:
    """``(epoch, upstream, revision)`` of a version that follows Debian policy
    (digits-only epoch, upstream starting with a digit, only the allowed
    characters), else None. The empty string is not a policy version."""
    if not version.isascii():
        return None
    epoch = 0
    rest = version
    if ":" in version:
        head, rest = version.split(":", 1)
        if not head.isdigit():
            return None
        epoch = int(head)
    upstream, revision = rest, ""
    if "-" in rest:
        upstream, revision = rest.rsplit("-", 1)
        if not _DEB_REVISION.fullmatch(revision):
            return None
    if not _DEB_UPSTREAM.fullmatch(upstream):
        return None
    return epoch, upstream, revision


def valid_deb_version(version: str) -> bool:
    """Whether *version* follows Debian policy."""
    return _deb_split(version) is not None


def parse_deb_dependencies(field_text: str) -> list[list[DebDependency]]:
    """``Depends``/``Pre-Depends`` text as groups of alternatives.

    A strict grammar: ``name[:arch] [(op version)]`` alternatives joined by
    ``|``, groups joined by ``,``. ``<`` and ``>`` are dpkg's deprecated
    spellings of ``<=`` and ``>=``. An empty field is no dependencies; anything
    else that does not match (an unbalanced parenthesis, a relation that does not
    exist, an empty group or alternative, trailing text, a build-time restriction,
    an unknown architecture qualifier, a version that breaks policy) raises
    :class:`DebDependencyError`."""
    text = " ".join(field_text.split())
    if not text:
        return []
    groups: list[list[DebDependency]] = []
    for clause in text.split(","):
        alternatives: list[DebDependency] = []
        for raw in clause.split("|"):
            found = _DEB_ALTERNATIVE.fullmatch(raw.strip())
            if found is None:
                raise DebDependencyError(f"cannot read {raw.strip()!r} as a dependency")
            arch = found.group("arch")
            if arch is not None and not _DEB_ARCH.fullmatch(arch):
                raise DebDependencyError(
                    f"unknown architecture qualifier :{arch} in {raw.strip()!r}"
                )
            relation = found.group("op")
            version = found.group("version")
            if relation is not None:
                relation = {"<": "<=", ">": ">="}.get(relation, relation)
                assert version is not None
                if not valid_deb_version(version):
                    raise DebDependencyError(
                        f"{version!r} in {raw.strip()!r} is not a valid Debian version"
                    )
            alternatives.append(DebDependency(found.group("name"), relation, version, arch))
        groups.append(alternatives)
    return groups


def parse_deb_depends(field_text: str) -> list[list[str]]:
    """``Depends``/``Pre-Depends`` text as groups of alternatives, names only
    (:func:`parse_deb_dependencies` keeps versions and qualifiers)."""
    return [[d.name for d in group] for group in parse_deb_dependencies(field_text)]


def _deb_order(char: str) -> int:
    if char.isdigit() or not char:
        return 0
    if char.isalpha():
        return ord(char)
    if char == "~":
        return -1
    return ord(char) + 256


def _deb_verrevcmp(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            difference = _deb_order(a[i] if i < len(a) else "") - _deb_order(
                b[j] if j < len(b) else ""
            )
            if difference:
                return difference
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        first = 0
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first:
                first = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first:
            return first
    return 0


def compare_deb_versions(a: str, b: str) -> int:
    """Debian's version order (epoch, upstream, revision; ``~`` sorts before
    everything): negative, zero or positive as *a* is older than, equal to or
    newer than *b*. The empty version is older than any other and equal to
    itself, as dpkg orders it. A non-empty version that breaks Debian policy
    raises :class:`ValueError`."""
    if not a or not b:
        return (a != "") - (b != "")
    parts_a, parts_b = _deb_split(a), _deb_split(b)
    for version, parts in ((a, parts_a), (b, parts_b)):
        if parts is None:
            raise ValueError(f"{version!r} is not a valid Debian version")
    assert parts_a is not None and parts_b is not None
    if parts_a[0] != parts_b[0]:
        return parts_a[0] - parts_b[0]
    return _deb_verrevcmp(parts_a[1], parts_b[1]) or _deb_verrevcmp(parts_a[2], parts_b[2])


def deb_dependency_met(dependency: DebDependency, installed: str | None) -> bool:
    """Whether an installed version (None: not installed) satisfies *dependency*'s
    version relation. An installed version that is not a valid Debian version
    meets nothing."""
    if installed is None or not valid_deb_version(installed):
        return False
    if dependency.relation is None or dependency.version is None:
        return True
    order = compare_deb_versions(installed, dependency.version)
    return {
        "<<": order < 0,
        "<=": order <= 0,
        "=": order == 0,
        ">=": order >= 0,
        ">>": order > 0,
    }[dependency.relation]


def deb_probe_names(
    dependency: DebDependency, native: str, foreign: Sequence[str] = ()
) -> tuple[str, ...]:
    """The names apt is asked about to find *dependency*'s installed package: a
    bare name is the native one; ``:any`` is the native and every foreign
    architecture dpkg knows; ``:native`` and an explicit architecture are that
    one."""
    name = dependency.name
    if dependency.arch is None:
        return (name,)
    if dependency.arch == "any":
        return (name, *(f"{name}:{arch}" for arch in (native, *foreign)))
    arch = native if dependency.arch == "native" else dependency.arch
    return (name, f"{name}:{arch}") if arch == native else (f"{name}:{arch}",)


def deb_group_met(
    group: Sequence[DebDependency],
    installed: Mapping[str, str | None],
    native: str,
    foreign: Sequence[str] = (),
) -> bool:
    """Whether any alternative of *group* has an installed package, at its stated
    architecture, whose version is in range. *installed* maps the names
    :func:`deb_probe_names` gives to the installed version (None: absent)."""
    return any(
        deb_dependency_met(dependency, installed.get(candidate))
        for dependency in group
        for candidate in deb_probe_names(dependency, native, foreign)
    )


def _routed_payload(block: object) -> RemoteArtifact | None:
    """The one pinned download of a source, binary, venv-payload or Node block."""
    if isinstance(block, SourceInstall):
        return block.source
    if isinstance(block, BinaryInstall | NodeInstall):
        return block.artifact
    if isinstance(block, VenvInstall):
        return block.payload
    return None


def payload_misses(
    plan: InstallPlan,
    context: ResolutionContext,
    built: frozenset[str],
    *,
    cached: Callable[[RemoteArtifact], bool],
) -> dict[str, CatalogueMiss]:
    """Offline, the pinned payload of every source, binary, venv and Node unit the
    verified Bunker cannot answer for (and the cache does not hold), by unit.

    The same check each backend makes before returning steps, asked here so a
    missing payload defers the whole unit (or refuses a typed one) through
    :func:`catalogue_deferral` instead of failing while steps are built. A unit
    already built at its pin, or a .deb already installed, is not asked about.
    Online (``preflight_payloads`` asks nothing) the result is empty."""
    from hammunition.payloads import preflight_payloads

    misses: dict[str, CatalogueMiss] = {}
    for unit in plan.packages:
        block = unit.block.install
        pin = _routed_payload(block)
        if pin is None or (unit.name in built and not isinstance(block, VenvInstall | NodeInstall)):
            continue
        if isinstance(block, BinaryInstall) and unit.deb_installed:
            continue
        try:
            preflight_payloads(unit.name, ((pin, None),), context=context, cached=cached)
        except CatalogueMiss as exc:
            misses[unit.name] = exc
    return misses


def offline_payload_blockers(
    plan: InstallPlan,
    built: frozenset[str],
    *,
    cached: Callable[[RemoteArtifact], bool],
    deb_unmet: Callable[[PlannedPackage], list[str]],
    isolated: bool = True,
    deb_isolated: bool = True,
) -> list[Blocker]:
    """Downloads an offline run could not make, refused at plan time by name.

    A derived-data or other pinned payload with no Bunker route proceeds only
    when its bytes are already in the verified cache or the unit is already
    built. Source, binary, venv and Node payloads have one (:func:`payload_misses`).
    A vendor ``.deb`` is installed with ``apt-get install ./file.deb``, which
    resolves its dependencies against the archive: offline it proceeds only when
    every dependency group already has a suitable installed package, version
    included (*deb_unmet* names the ones that do not), checked here when its
    bytes are cached and by the install after its fetch when they are not. Units refused by :func:`offline_network_blockers`, and
    the data kinds :func:`preflight_data` decides, are not repeated here."""
    from hammunition.manifest.schema import BinaryInstall, DataInstall, RegisterInstall

    out: list[Blocker] = []
    for unit in plan.packages:
        block = unit.block.install
        if unit.name in built or isinstance(
            block,
            DataInstall | RegisterInstall | AptInstall | VenvInstall | NodeInstall | GitInstall,
        ):
            continue
        if isinstance(block, SourceInstall):
            # Its payload is routed through the Bunker (payload_misses); its build
            # runs upstream's code, which offline must have no network.
            if not isolated:
                out.append(
                    Blocker(
                        subject=unit.name,
                        reason=(
                            "offline: building it runs upstream's build code, which must have no "
                            "network and no way to reach a host UNIX socket (docker, dbus, a "
                            "proxy), and this machine has no working bwrap sandbox"
                        ),
                        remedy=OFFLINE_ISOLATION_REMEDY,
                    )
                )
            continue
        # A vendor .deb is routed too; only its dependencies are checked here, and
        # only once its bytes are local to read them from. An uncached one is
        # checked by the install itself, after its fetch (BinaryBackend).
        missing = (
            []
            if isinstance(block, BinaryInstall)
            else [a for a in _remote_artifacts(block) if not cached(a)]
        )
        if missing:
            shown = ", ".join(a.url for a in missing[:3]) + (
                f" and {len(missing) - 3} more" if len(missing) > 3 else ""
            )
            out.append(
                Blocker(
                    subject=unit.name,
                    reason=(
                        f"offline: it downloads {shown}, which has no Bunker route yet and "
                        "is not in the local cache"
                    ),
                    remedy=OFFLINE_PAYLOAD_REMEDY,
                )
            )
            continue
        if (
            isinstance(block, BinaryInstall)
            and block.format == "deb"
            and not unit.deb_installed
            and not deb_isolated
        ):
            out.append(
                Blocker(
                    subject=unit.name,
                    reason=(
                        "offline: its .deb is installed with its maintainer scripts and "
                        "triggers inside the bwrap sandbox so they have no network and no "
                        "pathname socket to bridge through, and this machine has none that "
                        "works"
                    ),
                    remedy="install bubblewrap (bwrap) while online, or run this unit online",
                )
            )
            continue
        if (
            isinstance(block, BinaryInstall)
            and block.format == "deb"
            and not unit.deb_installed
            and cached(block.artifact)
        ):
            unmet = deb_unmet(unit)
            if unmet:
                out.append(
                    Blocker(
                        subject=unit.name,
                        reason=(
                            "offline: its .deb is installed by apt, which would fetch what it "
                            f"depends on and this machine lacks: {', '.join(unmet)}"
                        ),
                        remedy=OFFLINE_APT_REMEDY,
                    )
                )
    return out


def preflight_data(
    plan: InstallPlan,
    context: ResolutionContext,
    *,
    cached: Callable[[str, DataArtifact], bool],
) -> InstallPlan:
    """Offline, drop every data unit the Bunker cannot fully supply.

    A unit with any artifact neither cached nor on the Bunker (matching the
    repository's own sha256 and size) gets no steps at all: a profile member is
    deferred by name, a unit the operator typed refuses. A register download
    with no digest is only taken from a catalogue entry labelled unverified.
    Every miss in a unit is named together, and a unit that depends on a
    dropped unit is dropped with it. Online it returns *plan* unchanged."""
    if not context.offline:
        return plan
    from hammunition.acma import FILE_NAME
    from hammunition.backends.data import data_name
    from hammunition.manifest.schema import DataInstall, RegisterInstall

    misses: dict[str, CatalogueMiss] = {}
    for unit in plan.packages:
        block = unit.block.install
        found: list[str] = []
        if isinstance(block, DataInstall):
            for pin in block.artifacts:
                if cached(unit.name, pin):
                    continue
                name = data_name(pin)
                try:
                    context.require_payload(unit.name, name, sha256=pin.sha256, size=pin.size)
                    context.note(unit.name, name, fallback=False)
                except CatalogueMiss as exc:
                    found.append(str(exc))
        elif isinstance(block, RegisterInstall):
            try:
                context.unverified(unit.name, FILE_NAME)
                context.note(unit.name, FILE_NAME, fallback=False)
            except CatalogueMiss as exc:
                found.append(str(exc))
        if found:
            misses[unit.name] = CatalogueMiss("; ".join(dict.fromkeys(found)))
    # A unit that depends on one dropped here cannot run without it.
    changed = True
    while changed:
        changed = False
        for unit in plan.packages:
            gone = sorted(d for d in unit.manifest.depends if d in misses)
            if gone and unit.name not in misses:
                misses[unit.name] = CatalogueMiss(
                    f"depends on {', '.join(gone)}, which the Bunker cannot supply: "
                    + "; ".join(str(misses[d]) for d in gone)
                )
                changed = True
    packages: list[PlannedPackage] = []
    deferrals = list(plan.deferrals)
    blockers: list[Blocker] = []
    for unit in plan.packages:
        miss = misses.get(unit.name)
        if miss is None:
            packages.append(unit)
            continue
        try:
            deferrals.append(catalogue_deferral(unit, miss))
        except PlanError as exc:
            blockers.extend(exc.blockers)
    if blockers:
        raise PlanError(blockers)
    if not misses:
        return plan
    return _without_units(plan, set(misses), packages, deferrals)


def _without_units(
    plan: InstallPlan,
    dropped: set[str],
    packages: list[PlannedPackage],
    deferrals: list[Deferral],
) -> InstallPlan:
    """*plan* with *dropped* units gone from every field that belongs to a unit.

    A unit with no install steps also has no group membership, configuration
    file, user service, file capability, third-party repository, consent gate
    of its own or disclosure note: those would otherwise be performed for
    software that is not being installed."""

    return replace(
        plan,
        packages=tuple(packages),
        deferrals=tuple(deferrals),
        group_memberships=tuple(g for g in plan.group_memberships if g.package not in dropped),
        file_capabilities=tuple(c for c in plan.file_capabilities if c.package not in dropped),
        consent_gates=tuple(
            (name, gate)
            for name, gate in plan.consent_gates
            if name.removeprefix("file-capabilities:") not in dropped
        ),
        config_files=tuple(c for c in plan.config_files if c[0] not in dropped),
        user_services=tuple(s for s in plan.user_services if s.unit not in dropped),
        apt_repos=tuple(r for r in plan.apt_repos if r.unit not in dropped),
        notes=tuple(
            n
            for n in plan.notes
            if not any(n.startswith((f"{name}:", f"{name} ")) for name in dropped)
        ),
    )


def resolve(
    names: Sequence[str],
    *,
    catalog: Mapping[str, PackageManifest],
    profiles: Mapping[str, ProfileManifest],
    target: Target,
    apt: AptBackend,
    user: str,
    refresh: bool = False,
    station: Station | None = None,
    devices: Mapping[str, DeviceManifest | DeviceClass] | None = None,
    repos: AptRepoBackend | None = None,
    kernel: KernelProbe | None = None,
    java: JavaProbe | None = None,
    desktops: SessionScan | frozenset[Desktop] | None = None,
    log: TransactionLog | None = None,
    resolution_context: ResolutionContext | None = None,
) -> InstallPlan:
    """Build a complete plan, or raise :class:`PlanError` listing every blocker.

    The apt probe happens once, at the end, for every distro package the whole
    transaction needs — the manifests' own apt packages and their ``depends``
    together. One probe means one answer about the machine's apt state rather
    than N answers taken at N different moments.

    ``kernel`` is the running kernel's module tree, consulted only for units
    that declare ``requires_kernel``; ``None`` means it was not read, which is
    disclosed on those units rather than assumed either way.

    ``java`` is the machine's Java, measured with ``java -version`` and only
    when a unit declares ``requires_java`` (D-037, amended 2026-10-02); ``None``
    means it was not read, which is disclosed on those units.

    ``desktops`` is what the session files offer (:func:`hammunition.desktop.
    scan_sessions`; a bare set of desktops is read as a scan that saw no
    unrecognised file), consulted only for units that declare ``desktops``;
    ``None`` means they were not read, disclosed on those units the way an
    unreadable kernel is. Passed in, never read here: the planner must answer
    the same in a test, a container and under sudo (D-060).

    ``log`` is the transaction log, consulted only to attribute an installed
    vendor .deb to this engine (#63); ``None`` means no unit can be already
    installed that way, which is the conservative reading.

    ``resolution_context`` is the run's :class:`~hammunition.resolution.
    ResolutionContext`. Offline, apt is not reachable, so an apt package the
    machine does not already have is a blocker by name rather than an
    ``apt-get`` that would try the network (apt on the Bunker is phase 2 of
    #381); the simulation of what is already installed stays local.
    """
    blockers: list[Blocker] = []
    deferrals: list[Deferral] = []
    config_files: list[tuple[str, ConfigFile, str]] = []
    user_services: list[PlannedUserService] = []
    station = station if station is not None else Station()

    wanted, gates = _expand_requests(names, catalog, profiles, blockers)
    _pull_catalog_dependencies(wanted, catalog)

    if not wanted:
        if blockers:
            raise PlanError(blockers)
        return InstallPlan(target=target, packages=())

    ordered = _order(wanted, catalog, blockers)

    # Q-017: a member that reached the request only through a profile (or as a
    # dependency of one) may be deferred when the target does not offer it. A
    # name the operator typed is never deferred -- asking for it by name is
    # asking to see the refusal.
    deferrable = {name for name in ordered if REQUESTED_DIRECTLY not in wanted[name]}
    deferred: dict[str, Deferral] = {}
    notes_early: list[str] = []
    scan = SessionScan(desktops=desktops) if isinstance(desktops, frozenset) else desktops
    # Deferred because of a desktop, directly or through a dependency: their
    # dependents and a profile of nothing else say so, not "this target".
    desktop_caused: set[str] = set()
    repo_additions: list[RepoAddition] = []
    target_name = target.pretty_name or f"{target.distro} {target.version}".strip()

    # (manifest, block, every apt package it needs, which of those are build-only)
    resolved: list[tuple[PackageManifest, InstallBlock, tuple[str, ...], tuple[str, ...]]] = []
    for name in ordered:
        manifest = catalog[name]

        status = _status_blocker(manifest)
        if status is not None:
            blockers.append(status)
            continue

        # D-060: a unit for particular desktops, on a machine whose session
        # files offer none of them. A fact about the machine, so a profile
        # member defers (D-039) and a typed name refuses. Checked before the
        # archive, so the reason given is the one that matters: an Xfce
        # machine is told the tray is for Plasma, not what apt thinks of it.
        if manifest.desktops is not None:
            want = frozenset(manifest.desktops)
            if scan is None:
                notes_early.append(
                    f"{name} is for {describe_set(want)}, and this machine's session files "
                    f"were not read, so whether it has one is not known; it is planned as asked."
                )
            elif not want & scan.desktops:
                if name in deferrable:
                    deferred[name] = _desktop_deferral(manifest, scan, catalog, wanted)
                    desktop_caused.add(name)
                else:
                    blockers.append(_desktop_blocker(manifest, scan, catalog))
                continue

        block = manifest.resolve(target.distro, target.version, target.arch)
        if block is None:
            where = f"{target.distro} {target.version or '(no version)'} on {target.arch}"
            if name in deferrable:
                deferred[name] = _target_deferral(
                    name, wanted, f"the catalog declares no install block matching {where}"
                )
                continue
            blockers.append(
                Blocker(
                    subject=name,
                    reason=f"declares no install block matching {where}",
                    remedy=(
                        "this target is genuinely unsupported for this package; the catalog "
                        "says so rather than pretending otherwise"
                    ),
                )
            )
            continue

        capability = _check_engine_capability(
            manifest,
            block,
            repos_supported=repos is not None,
            applicable_repos=manifest.apt_repos_for(target.distro, target.version, target.arch),
        )
        if capability:
            blockers.extend(capability)
            continue

        # D-057: map data is station data. With no regions set there is
        # nothing to fetch or convert. A profile member is deferred and the
        # rest installs (D-035); a unit the operator typed is refused -- a
        # run that was asked for map data and did nothing is not a success.
        if not station.map_regions and _reads_map_regions(block, catalog, target):
            if name in deferrable:
                deferred[name] = _map_regions_deferral(name)
            else:
                blockers.append(
                    Blocker(
                        subject=name,
                        reason=f"{NO_MAP_REGIONS}, so there is no map data to install",
                        remedy=MAP_REGIONS_REMEDY,
                    )
                )
            continue

        # D-066: the same shape for the Kiwix books. Nothing is chosen by
        # default, so with none chosen there is nothing to fetch.
        if not station.reference_books and isinstance(block.install, KiwixBooksInstall):
            if name in deferrable:
                deferred[name] = _reference_books_deferral(name)
            else:
                blockers.append(
                    Blocker(
                        subject=name,
                        reason=f"{NO_REFERENCE_BOOKS}, so there is nothing to install",
                        remedy=REFERENCE_BOOKS_REMEDY,
                    )
                )
            continue

        writable, unwritable = _plan_config(manifest, station, operator_home(user))
        config_files.extend(writable)
        deferrals.extend(unwritable)

        if manifest.user_services:
            # Plain services need nothing; the rig's need the hardware
            # catalog and defer by name without it (D-035) -- decided in
            # plan_user_services, which knows which is which.
            svc_planned, svc_deferrals, svc_notes = plan_user_services(
                manifest, station, devices, venv_dir=service_venv_dir(manifest, user or None)
            )
            user_services.extend(svc_planned)
            deferrals.extend(svc_deferrals)
            notes_early.extend(svc_notes)

        # apt and source reach here; _check_engine_capability rejects the rest.
        # A source build needs its `build_depends` from apt before it can start,
        # and those go through the same pre-flight candidate check as everything
        # else -- which is the whole point. glfer's build_depends name `fftw2`
        # and `libgtk2.0-dev`, two of D-016's four suspected-stale dependency
        # lines; nothing in AHRL ever asked apt whether they still exist.
        # `depends` holds names in two namespaces (see _pull_catalog_dependencies).
        # One naming another manifest has already been pulled into the plan as a
        # catalog package and must NOT also be asked of apt: `libacars` is ours
        # and apt has never heard of it, so probing it would report the
        # transaction unsatisfiable because a package we are about to build from
        # source is not in the archive.
        distro_depends = tuple(d for d in manifest.depends if d not in catalog)
        # Tools the ENGINE's own method needs, owned here rather than left to
        # every manifest to remember: a git build needs git, an applied patch
        # needs patch(1). Found the hard way — the first campaign against a
        # fresh baseline failed all four git units at `git init`, because
        # every earlier VM had git only as a leftover of manual testing.
        # AHRL's install_source_libs was this idea as a blanket; per-method
        # injection keeps the plan honest about who needs what.
        tool_depends: tuple[str, ...] = ()
        if block.install.method == "git":
            tool_depends = ("git",)
            if getattr(block.install, "patches", None):
                tool_depends = ("git", "patch")
        elif block.install.method == "source" and getattr(block.install, "patches", None):
            tool_depends = ("patch",)
        elif block.install.method == "node":
            # The distribution's Node and npm, never fetched (D-037). Riding
            # the probe below is what makes "absent" a named refusal rather
            # than a failed `npm` exec halfway through.
            tool_depends = ("nodejs", "npm")
            if block.install.patches:
                tool_depends = (*tool_depends, "patch")
        if getattr(block.install, "autoreconf", False):
            tool_depends = (*tool_depends, "autoconf", "automake", "libtool")
        # The toolchain is the engine's tool as much as git is. wsjtx never
        # declared cmake or a compiler and built on five targets anyway,
        # because js8call in the same profile is a git build there and
        # declares both; on Pop!_OS 24.04 js8call comes from apt, nothing
        # else asked for cmake, and digital-modes died at command 27 with
        # 'cmake' not on PATH (2026-09-04). Measured the same day on the
        # Debian 13 baseline, which ships no gcc: 33 of the catalog's 39
        # source/git manifests list build-essential by hand and four C/C++
        # builds (fldigi, glfer, mshv, wsjtx) rely on a neighbour for it --
        # mshv's own note records the order-dependence. So every compiled
        # build gets build-essential, and a cmake build gets cmake. qmake is
        # not injected: all six qmake manifests declare qt5-qmake themselves.
        if block.install.method in ("source", "git"):
            tool_depends = (*tool_depends, "build-essential")
            if getattr(block.install, "build_system", None) == "cmake":
                tool_depends = (*tool_depends, "cmake")
        if block.install.method == "apt":
            packages = (*block.install.packages, *distro_depends)
            build_only: tuple[str, ...] = ()
        else:
            packages = (*block.build_depends, *tool_depends, *distro_depends)
            build_only = tuple(dict.fromkeys((*block.build_depends, *tool_depends)))
        resolved.append((manifest, block, tuple(dict.fromkeys(packages)), build_only))

    # -- one apt probe for the whole transaction ---------------------------
    # Declared repo conflicts ride the same probe: the plan needs to know
    # whether each is installed NOW. They are deliberately excluded from the
    # no-candidate check below -- a conflict package missing from the archive
    # is a fine state, not a blocker.
    all_conflicts = sorted(
        {c for manifest, _, _, _ in resolved for c in manifest.conflicts_with_repo_package}
    )
    all_apt = sorted({p for _, _, packages, _ in resolved for p in packages})
    # Vendor .deb package names ride the same probe: whether each is installed
    # NOW is half of "already installed" for that unit (#63). They are not in
    # `all_apt`, so the no-candidate check never asks the archive for them.
    all_debs = sorted(
        {
            block.install.deb_package
            for _, block, _, _ in resolved
            if isinstance(block.install, BinaryInstall) and block.install.deb_package
        }
    )
    states = {}
    notes: list[str] = notes_early
    # `or all_conflicts`: a unit with nothing to apt-install (a pure vendor
    # .deb) still needs the probe to know whether its declared conflicts are
    # installed -- the gate that skipped it left the wsjtx-improved refusal
    # untested until this line existed.
    if all_apt or all_conflicts or all_debs:
        if not apt.lists_populated():
            if refresh:
                # The refresh (the default, D-044) puts `apt-get update` at the
                # head of this same run, so empty lists are a sequencing fact, not
                # a blocker -- refusing here would tell the operator to pass a
                # flag that is already on.
                # What is genuinely lost is the pre-flight candidate check:
                # nothing can know what apt will offer until update has run, so
                # the plan discloses the gap instead of silently skipping the
                # check (D-016 wants the resolution honest, not decorative).
                notes.append(
                    "apt has no package lists yet; this run's refresh runs apt-get "
                    "update first, so which packages actually have candidates cannot be "
                    "known before this plan executes. A package apt does not "
                    "offer will fail the apt-get install step rather than being "
                    "caught here."
                )
            else:
                blockers.append(
                    Blocker(
                        subject="apt",
                        reason=(
                            "has no package lists, so every package would resolve as unknown. "
                            "Reporting them all as unobtainable would be a confident lie"
                        ),
                        remedy=(
                            "run `sudo apt-get update` while the archive is reachable; an "
                            "offline run never refreshes the lists"
                            if resolution_context is not None and resolution_context.offline
                            else "run `sudo apt-get update`, or drop --no-refresh so this run does it first"
                        ),
                    )
                )
        else:
            # A unit with a Java floor also asks about the archive's concrete
            # JREs, so a refusal can name the one that would meet it.
            jre_names = (
                {jre_package(n) for n in JRE_CANDIDATES}
                if any(m.requires_java for m, _, _, _ in resolved)
                else set()
            )
            states = apt.probe(sorted({*all_apt, *all_conflicts, *all_debs, *jre_names}))
            for manifest, block, packages, _ in resolved:
                missing = [p for p in packages if p not in states or not states[p].known]
                own = (
                    set(block.install.packages) if isinstance(block.install, AptInstall) else set()
                )
                applicable = manifest.apt_repos_for(target.distro, target.version, target.arch)
                if applicable and repos is not None and isinstance(block.install, AptInstall):
                    # D-040: the archive as configured has no candidate for
                    # the unit's own packages, and the manifest names where
                    # they come from. Decided here, from the same probe, so
                    # a target that already offers the package -- Parrot's
                    # own codium -- never gets a repository it does not need
                    # (D-022), and a repository added under our name by an
                    # earlier run is recognised rather than re-added.
                    repo_outcome = _plan_repos(
                        manifest, applicable, block.install, repos, missing, own, notes
                    )
                    if isinstance(repo_outcome, Blocker):
                        blockers.append(repo_outcome)
                        continue
                    repo_additions.extend(repo_outcome)
                    if repo_outcome:
                        missing = [p for p in missing if p not in own]
                if missing:
                    origin = {p: "depends" for p in manifest.depends if p not in catalog}
                    origin.update({p: "build_depends" for p in _build_depends_of(manifest)})
                    detail = ", ".join(f"{p} ({origin.get(p, 'install')})" for p in sorted(missing))
                    # Q-017: the unit's OWN apt packages absent from this
                    # release is the target's gap. A missing `depends` or
                    # `build_depends` is the manifest's, and stays a blocker
                    # -- deferral drawn wider than that swallows defects.
                    if manifest.name in deferrable and set(missing) <= own:
                        deferred[manifest.name] = _target_deferral(
                            manifest.name,
                            wanted,
                            f"apt on {target_name} has no candidate for {', '.join(sorted(missing))}",
                        )
                        continue
                    blockers.append(
                        Blocker(
                            subject=manifest.name,
                            reason=f"apt has no candidate for {detail}",
                            remedy=(
                                "the package may have been renamed, may need a component "
                                "this machine has not enabled, or may not exist on this "
                                "release — D-016 names four AHRL dependency lines that had "
                                "gone stale exactly this way"
                            ),
                        )
                    )

    # -- Node.js floor, from the same probe (D-037) -------------------------
    # Disclosed as a requirement, refused when the distribution's Node is
    # absent or too old. Never fetched: the remedy is a newer release of the
    # distribution or skipping the unit, and the plan says so.
    for manifest, block, _, _ in resolved:
        if not isinstance(block.install, NodeInstall):
            continue
        if not states:
            notes.append(
                f"{manifest.name} needs Node.js {block.install.node_min_version} or newer "
                f"from the distribution's nodejs package, and with no apt lists the "
                f"version on offer cannot be checked before this plan executes."
            )
            continue
        outcome = _check_node_floor(manifest, block.install, states.get("nodejs"))
        if isinstance(outcome, Blocker):
            # Q-017: the distribution's Node being absent or below the floor
            # is true of the target, so a profile member defers on it.
            if manifest.name in deferrable:
                # The blocker's reason reads on from its subject; the deferral
                # prints `why:` on its own line, so name the subject again.
                deferred[manifest.name] = _target_deferral(
                    manifest.name, wanted, f"{manifest.name} {outcome.reason}"
                )
            else:
                blockers.append(outcome)
        else:
            notes.append(outcome)

    # -- Java floor, from `java -version` (D-037, amended 2026-10-02) -------
    # `default-jre-headless` is a metapackage: its version does not say which
    # Java it brings, and Ubuntu 22.04 / Pop!_OS 22.04 resolve it to 11 while
    # GraphHopper's classes are major 61. So the version that counts is the
    # one `java` reports. Measured once per plan, never fetched.
    java_caused: set[str] = set()
    for manifest, _, _, _ in resolved:
        floor_java = manifest.requires_java
        if floor_java is None or manifest.name in deferred:
            continue
        outcome_java = _check_java_floor(manifest, floor_java, java, states, notes)
        if outcome_java is None:
            continue
        if manifest.name in deferrable:
            deferred[manifest.name] = _target_deferral(
                manifest.name,
                wanted,
                f"{manifest.name} {outcome_java.reason}",
                outcome_java.remedy,
            )
            java_caused.add(manifest.name)
        else:
            blockers.append(outcome_java)

    # -- A converter's program below the version its output needs (D-071) ---
    # tilemaker writes PMTiles from 3.0; Ubuntu 24.04 carries 2.4.0. Read from
    # the same probe as Node's floor (D-037): the version the run will have,
    # installed else the archive's candidate. The floor is the engine's, like
    # the converter's argv, and nothing is built or fetched to meet it.
    for manifest, block, _, _ in resolved:
        install = block.install
        if not isinstance(install, DerivedDataInstall) or manifest.name in deferred:
            continue
        floor = CONVERTER_FLOORS.get(install.converter)
        if floor is None:
            continue
        program, (major, minor), does = floor
        if not states:
            notes.append(
                f"{manifest.name} needs {program} {major}.{minor} or newer, the first that "
                f"{does}, and with no apt lists the version on offer cannot be checked "
                f"before this plan executes."
            )
            continue
        state = states.get(program)
        if state is None or not state.known:
            continue  # no candidate at all: the apt check above has named it
        version = state.installed or state.candidate
        found = node_version(version) if version else None
        if found is not None and found >= (major, minor):
            continue
        source = "installed" if state.installed else "the archive's candidate"
        have = f"this distribution's {program} is {version} ({source})"
        why = f"needs {program} {major}.{minor} or newer, the first that {does}; {have}"
        if manifest.name in deferrable:
            deferred[manifest.name] = _target_deferral(
                manifest.name, wanted, f"{manifest.name} {why}"
            )
        else:
            blockers.append(
                Blocker(
                    subject=manifest.name,
                    reason=why,
                    remedy=(
                        f"a release of this distribution that carries {program} "
                        f"{major}.{minor} or newer; nothing is built or fetched to meet it "
                        f"(D-071)"
                    ),
                )
            )

    # -- Kernel subsystems the unit cannot work without ---------------------
    # A fact about the machine, not the target: one Pop!_OS 24.04 VM has
    # AX.25 in its 7.0.11 module tree and not in its 7.1.5 one (2026-09-04).
    # So it is read here, never written into the capability matrix, and a
    # profile member whose kernel lacks it defers the way a target gap does.
    for manifest, _, _, _ in resolved:
        if manifest.name in deferred:
            # Already withheld for a reason the target gave (Kali 2026.3 has
            # no ax25-tools candidate AND no ax25 module). The first reason
            # stands; the typed-name refusal shows every one.
            continue
        for feature in manifest.requires_kernel:
            what = DESCRIBE[feature]
            present = None if kernel is None else kernel.available(feature)
            if present is True:
                continue
            if kernel is None or present is None:
                which = f" {kernel.release}" if kernel is not None else ""
                notes.append(
                    f"{manifest.name} needs {what}, which cannot be checked on this "
                    f"machine: no module tree for the running kernel{which} is readable."
                )
                continue
            why = f"needs {what}, which kernel {kernel.release} does not carry -- {KERNEL_REMOVAL}"
            if manifest.name in deferrable:
                deferred[manifest.name] = _target_deferral(
                    manifest.name, wanted, f"{manifest.name} {why}"
                )
            else:
                blockers.append(
                    Blocker(
                        subject=manifest.name,
                        reason=why,
                        remedy=(
                            f"a distribution kernel that still carries it (Debian 13's 6.12, "
                            f"Parrot 7.3's and Ubuntu 26.04's 7.0 do); {USERSPACE_PATH}; "
                            f"{NO_MODULE_BUILD}"
                        ),
                    )
                )

    # -- Q-017: the deferred set closes over catalog dependencies -----------
    # pythonprop depends on voacapl; a target that lacks voacapl cannot have
    # pythonprop either, however present its own package is. A dependent the
    # operator asked for by name is refused, naming the dependency.
    changed = True
    while changed:
        changed = False
        for manifest, _, _, _ in resolved:
            if manifest.name in deferred:
                continue
            gone = sorted(d for d in manifest.depends if d in deferred)
            if not gone:
                continue
            by_desktop = [d for d in gone if d in desktop_caused or d in java_caused]
            if by_desktop:
                # D-060: the dependency is missing because of the machine's
                # desktop, so the dependent's reason and remedy are its.
                cause = deferred[by_desktop[0]]
                why = f"depends on {', '.join(gone)}, which is deferred here: {cause.why}"
            else:
                why = f"depends on {', '.join(gone)}, which this target does not offer"
            if manifest.name in deferrable:
                if by_desktop:
                    via = ", ".join(w for w in wanted[manifest.name] if w != REQUESTED_DIRECTLY)
                    deferred[manifest.name] = Deferral(
                        subject=manifest.name,
                        what=f"will not be installed ({via})",
                        why=why,
                        remedy=deferred[by_desktop[0]].remedy,
                        kind="package",
                    )
                    if by_desktop[0] in java_caused:
                        java_caused.add(manifest.name)
                    else:
                        desktop_caused.add(manifest.name)
                else:
                    deferred[manifest.name] = _target_deferral(manifest.name, wanted, why)
                changed = True
            else:
                blockers.append(
                    Blocker(
                        subject=manifest.name,
                        reason=why,
                        remedy=(
                            f"`hammunition install {gone[0]}` shows why; there is no "
                            f"installing the dependent without it"
                        ),
                    )
                )
                # Not `changed`: a blocker ends the plan, and its dependents
                # would only repeat the same refusal.

    # A profile whose every member is deferred is refused, not deferred: a
    # plan that installs nothing and reports a success is the shape D-031
    # exists to catch, one layer up.
    for name in names:
        profile = profiles.get(name)
        if profile is None or name in catalog:
            continue
        members = [p for p in profile.packages if p in catalog]
        if members and all(m in deferred for m in members):
            if all(deferred[m].why == NO_MAP_REGIONS for m in members):
                # Not the target's gap: the station has chosen no regions.
                blockers.append(
                    Blocker(
                        subject=name,
                        reason=(
                            f"every member of this profile is map data and {NO_MAP_REGIONS}: "
                            f"{', '.join(members)}"
                        ),
                        remedy=MAP_REGIONS_REMEDY,
                    )
                )
                continue
            if all(m in desktop_caused for m in members):
                # D-060: not the target's gap either; the machine's desktop.
                blockers.append(
                    Blocker(
                        subject=name,
                        reason=(
                            f"every member of this profile is for a desktop this machine has "
                            f"no session for: {', '.join(members)}"
                        ),
                        remedy=(
                            "install a session for the desktop the members are for, then plan "
                            "again; `hammunition install <member>` names the desktop each needs"
                        ),
                    )
                )
                continue
            blockers.append(
                Blocker(
                    subject=name,
                    reason=(
                        f"every member of this profile is unavailable on {target_name}: "
                        f"{', '.join(members)}"
                    ),
                    remedy=(
                        "the profile has nothing to install here; each member's own "
                        "reason is listed by `hammunition install <member>`"
                    ),
                )
            )

    if deferred:
        deferrals.extend(deferred[name] for name in ordered if name in deferred)
        # A deferred member's configuration is neither written nor reported
        # as unwritten: there is no package for the file to belong to.
        config_files = [c for c in config_files if c[0] not in deferred]
        deferrals = [d for d in deferrals if not (d.kind == "config" and d.subject in deferred)]
        resolved = [r for r in resolved if r[0].name not in deferred]
        all_apt = sorted({p for _, _, packages, _ in resolved for p in packages})

    # -- declared conflicts against what is installed now (D-022) ----------
    for manifest, block, _, _ in resolved:
        installed_conflicts = [
            c
            for c in manifest.conflicts_with_repo_package
            if c in states and states[c].is_installed
        ]
        if not installed_conflicts:
            continue
        if isinstance(block.install, BinaryInstall) and block.install.format == "deb":
            names = ", ".join(installed_conflicts)
            blockers.append(
                Blocker(
                    subject=manifest.name,
                    reason=(
                        f"its vendor .deb collides with installed distribution package(s): {names}"
                    ),
                    remedy=(
                        f"remove them first (sudo apt-get remove {names}) or skip this "
                        f"unit -- the alternative is a dpkg file collision partway "
                        f"through the transaction, which is how this rule was measured "
                        f"(wsjtx-improved vs wsjtx-data, 2026-08-30)"
                    ),
                )
            )

    # -- the apt step, resolved by apt itself before anything runs ---------
    # `apt-cache policy` says a package has a candidate; only apt's resolver
    # says the whole set installs together. Two measured ways it does not:
    # a clean Kali, 2026-09-02, where `digital-modes` planned clean and then
    # failed at the dpkg step (jtdx brought `wsjtx-data`, the `wsjtx-improved`
    # .deb collided with it minutes later); and a clean Parrot the same night,
    # where five of fifteen profiles failed at the apt step itself because a
    # `-dev` package's exact-version dependency could not be met without
    # downgrading a library Parrot ships from its backports (D-038). Both are
    # D-016 defects -- the plan passed, the machine was touched, the failure
    # came after -- and one `--simulate` of the outstanding set answers both.
    # It is asked only when everything else has resolved: a simulation of a
    # set with a missing candidate fails for the reason already listed.
    #
    # A unit whose Recommends conflict with the target's desktop stack sets
    # `install_recommends: false` and its packages form a second set, asked
    # and installed with `--no-install-recommends` (D-052, issue #61). Two
    # sets are two apt commands, so they are two simulations: the plan may
    # only print, and refuse on, what will actually be run.
    from_repos = {p for addition in repo_additions for p in addition.packages}
    opted_out_names = {
        p for _, block, packages, _ in resolved if _opts_out_of_recommends(block) for p in packages
    }
    default_names = {
        p
        for _, block, packages, _ in resolved
        if not _opts_out_of_recommends(block)
        for p in packages
    }
    opted_out_names -= default_names

    def _outstanding(names: set[str]) -> list[str]:
        return [
            p
            for p in all_apt
            if p in names and not (p in states and states[p].is_installed) and p not in from_repos
        ]

    apt_sets = ((_outstanding(default_names), False), (_outstanding(opted_out_names), True))
    outstanding_apt = [p for set_packages, _ in apt_sets for p in set_packages]
    if resolution_context is not None and resolution_context.offline:
        blockers.extend(_offline_apt_blockers(resolved, set(outstanding_apt)))
        blockers.extend(_offline_repo_blockers(repo_additions))
    apt_release: str | None = None
    apt_from_release: tuple[str, ...] = ()
    simulation = AptSimulation(ok=True)
    if outstanding_apt and states and not blockers:
        results: list[AptSimulation] = []
        for set_packages, no_recommends in apt_sets:
            if not set_packages:
                results.append(AptSimulation(ok=True))
                continue
            result = apt.simulate(set_packages, no_recommends=no_recommends)
            if not result.ok:
                retried = _resolve_from_installed_release(
                    apt, set_packages, result, no_recommends=no_recommends
                )
                if isinstance(retried, Blocker):
                    blockers.append(retried)
                else:
                    result, _, _ = retried
            results.append(result)
        # `--target-release` is apt's, not one command's: a release measured
        # for either set governs the whole apt step, so the other set is asked
        # again with it rather than the plan disclosing a simulation of
        # something the machine will not be asked to do. Two releases is the
        # D-038 refusal for the same reason none is: nothing is guessed.
        releases = sorted({r.release for r in results if r.release is not None})
        if not blockers and len(releases) > 1:
            blockers.append(
                Blocker(
                    subject="apt",
                    reason=(
                        f"the two apt sets in this transaction resolve from different "
                        f"releases ({', '.join(releases)}), and one apt step cannot run "
                        f"with both"
                    ),
                    remedy=(
                        "install them in separate runs, or leave out the unit whose "
                        "packages need the other release -- the plan will not pick one "
                        "release over the other for you (D-038)"
                    ),
                )
            )
        elif not blockers and releases:
            apt_release = releases[0]
            for index, (set_packages, no_recommends) in enumerate(apt_sets):
                if not set_packages or results[index].release is not None:
                    continue
                again = apt.simulate(set_packages, release=apt_release, no_recommends=no_recommends)
                results[index] = again
                if not again.ok:
                    blockers.append(
                        Blocker(
                            subject="apt",
                            reason=(
                                f"cannot resolve this transaction as one apt-get install: "
                                f"the rest of it needs --target-release {apt_release}, and "
                                f"these packages do not resolve from there:\n"
                                f"{_indent(again.error)}"
                            ),
                            remedy=(
                                "leave out the unit that needs the other release, or "
                                "install the two in separate runs -- the plan will not "
                                "start a transaction apt has already refused (D-016)"
                            ),
                        )
                    )
        simulation = _merge_simulations(results[0], results[1])
        if apt_release is not None:
            apt_from_release = simulation.from_archive(apt_release)

    # -- what the apt step would REMOVE (D-022) ---------------------------
    # apt "resolves" a `Breaks:` against an installed package by removing
    # the installed one, and says so only in `Remv` lines the engine did not
    # read until 2026-09-07 (issue #42). The archive's `wsjtx-improved`
    # breaks `wsjtx`: on a Kali with `wsjtx` installed the simulation passed,
    # the dry run printed no removal, and the runner -- no `--no-remove` --
    # would have removed three packages unseen. Coexist, disclose, never
    # remove silently: every removal is named with its version, attributed
    # to the unit that declared the conflict where one did, and refused. The
    # operator removes the package by hand or leaves the unit out; the
    # engine does neither for them.
    if simulation.ok and simulation.removes:
        removed = ", ".join(
            f"{name} ({v})" if v else name for name, v in sorted(simulation.removes.items())
        )
        names = " ".join(sorted(simulation.removes))
        declaring = sorted(
            manifest.name
            for manifest, _, _, _ in resolved
            if set(manifest.conflicts_with_repo_package) & set(simulation.removes)
        )
        if declaring:
            subject = ", ".join(declaring)
            reason = (
                f"installing it means apt removes installed distribution package(s): {removed} "
                f"(a declared conflicts_with_repo_package that cannot coexist -- the archive "
                f"package Breaks or Conflicts with it)"
            )
        else:
            subject = ", ".join(sorted({m.name for m, _, _, _ in resolved if m.name in wanted}))
            reason = (
                f"apt would remove installed package(s) to install this transaction: {removed} "
                f"-- and no manifest in it declares that conflict in conflicts_with_repo_package, "
                f"which is a catalog defect worth an issue"
            )
        blockers.append(
            Blocker(
                subject=subject,
                reason=reason,
                remedy=(
                    f"remove them yourself first (sudo apt-get remove {names}) or leave the "
                    f"unit out -- the engine never removes a package the operator did not "
                    f"ask it to (D-022), and the install step runs with --no-remove so apt "
                    f"cannot either"
                ),
            )
        )

    # -- declared conflicts against what this transaction itself installs --
    # The check above sees what is installed NOW; on a clean machine that is
    # nothing, and the simulation is the only thing that knows what the apt
    # step pulls in.
    if simulation.ok:
        for manifest, block, _, _ in resolved:
            if not (isinstance(block.install, BinaryInstall) and block.install.format == "deb"):
                continue
            hit = sorted(
                c for c in manifest.conflicts_with_repo_package if c in simulation.installs
            )
            if not hit:
                continue
            names = ", ".join(hit)
            blockers.append(
                Blocker(
                    subject=manifest.name,
                    reason=(
                        f"its vendor .deb collides with distribution package(s) this same "
                        f"transaction would install: {names}"
                    ),
                    remedy=(
                        f"leave out either {manifest.name} or whatever needs {names} (apt-get "
                        f"install --simulate names the chain) -- the alternative is a "
                        f"dpkg file collision after the apt step has already run"
                    ),
                )
            )

    if blockers:
        raise PlanError(blockers)

    planned = tuple(
        PlannedPackage(
            manifest=manifest,
            block=block,
            apt_packages=packages,
            already_installed=tuple(p for p in packages if p in states and states[p].is_installed),
            requested_by=tuple(dict.fromkeys(wanted[manifest.name])),
            build_only=build_only,
            displaces=tuple(
                c
                for c in manifest.conflicts_with_repo_package
                if c in states and states[c].is_installed
            ),
            deb_installed=_deb_installed(block, states, log),
        )
        for manifest, block, packages, build_only in resolved
    )

    # An empty operator name is how `gpasswd --add '' wireshark` got built on the
    # first real run: root in a container with neither $USER nor $SUDO_USER set.
    # A privilege change aimed at nobody is not a no-op worth tolerating, and it
    # is exactly the shape D-016 wants caught during resolution rather than
    # discovered by reading the command that is about to run.
    needs_user = [
        (item.manifest.name, modification.group)
        for item in planned
        for modification in item.manifest.system_modifications
        if modification.kind == "group_membership"
    ]
    if needs_user and not user:
        groups = ", ".join(sorted({g for _, g in needs_user if g}))
        blockers.append(
            Blocker(
                subject=", ".join(sorted({name for name, _ in needs_user})),
                reason=(
                    f"needs the operator added to {groups}, and no operator could be "
                    f"identified ($SUDO_USER and $USER are both unset)"
                ),
                remedy="pass --user <name> to say who should be added to the group",
            )
        )
        raise PlanError(blockers)

    # An operator that names no real account is caught here, not after apt has
    # already run. `gpasswd --add nosuchuser dialout` fails, but user_groups()
    # returns an empty set for an unknown name ("about to be added anyway"), so
    # nothing upstream noticed until the privileged command failed mid-
    # transaction, on a machine apt had already changed. D-016: a failure is a
    # report before anything happens, never a surprise halfway through.
    if needs_user and user:
        try:
            pwd.getpwnam(user)
        except KeyError:
            groups = ", ".join(sorted({g for _, g in needs_user if g}))
            blockers.append(
                Blocker(
                    subject=", ".join(sorted({name for name, _ in needs_user})),
                    reason=(
                        f"needs the operator {user!r} added to {groups}, but {user!r} "
                        f"is not a user on this system"
                    ),
                    remedy="pass --user <name> naming an account that exists",
                )
            )
            raise PlanError(blockers) from None

    memberships = tuple(
        GroupMembership(
            group=modification.group,
            user=user,
            package=item.manifest.name,
            description=modification.description.strip(),
            detail=modification.detail.strip(),
            reverse_hint=modification.reverse_hint,
        )
        for item in planned
        for modification in item.manifest.system_modifications
        if modification.kind == "group_membership" and modification.group is not None
    )

    file_capabilities: list[FileCapability] = []
    capability_paths: dict[Path, str] = {}
    for item in planned:
        for modification in item.manifest.system_modifications:
            if modification.kind != "file_capability":
                continue
            install = item.block.install
            copies_binary = isinstance(install, SourceInstall | GitInstall) and (
                not install.provides_install_target
            )
            copies_binary = copies_binary or (
                isinstance(install, BinaryInstall) and install.format != "deb"
            )
            binary = next(
                (
                    binary
                    for binary in effective_binaries(item.manifest, item.block)
                    if binary.install_as == modification.binary
                ),
                None,
            )
            if binary is None or not copies_binary:
                blockers.append(
                    Blocker(
                        subject=item.name,
                        reason=(
                            f"file_capability names {modification.binary!r}, which is not "
                            "a binary copied into the Hammunition prefix for this target"
                        ),
                        remedy=(
                            "name a binary installed into the prefix by this target's "
                            "source, git or non-deb binary block"
                        ),
                    )
                )
                continue
            path = DEFAULT_PREFIX / "bin" / binary.install_as
            owner = capability_paths.get(path)
            if owner is not None:
                blockers.append(
                    Blocker(
                        subject=item.name,
                        reason=f"file capabilities target {path}, also declared by {owner}",
                        remedy="declare a capability grant for this binary in only one unit",
                    )
                )
                continue
            capability_paths[path] = item.name
            capabilities = tuple(modification.capabilities)
            file_capabilities.append(
                FileCapability(
                    path=path,
                    capabilities=capabilities,
                    package=item.name,
                    detail=modification.detail.strip(),
                )
            )
            grant = " ".join(f"{name}=ep" for name in capabilities)
            unit_env = "".join(
                character if character.isalnum() else "_" for character in item.name.upper()
            )
            gates.append(
                (
                    f"file-capabilities:{item.name}",
                    ConsentGate(
                        risk_categories=[RiskCategory.privileged_execution],
                        env_var=f"HAMMUNITION_ACCEPT_CAPABILITIES_{unit_env}",
                        disclosure=(
                            f"Granting {grant} to {path} gives {item.name} additional Linux "
                            f"privileges. The grant is optional and is applied only after "
                            f"affirmative consent."
                        ),
                        affirmation=(
                            f"Do you authorize Hammunition to grant exactly {grant} to {path}?"
                        ),
                    ),
                )
            )

    if blockers:
        raise PlanError(blockers)

    # What a package's own scripts and files do to the machine, disclosed before
    # the confirmation: the engine performs none of it (dpkg does), and a plan
    # that stayed silent about a service enabled at boot or a world-writable
    # udev rule would be the "approximate dry run" this project refuses.
    for item in planned:
        for modification in item.manifest.system_modifications:
            if modification.kind in DISCLOSED_ONLY_MODIFICATIONS:
                notes.append(f"{item.name}: {modification.description.strip()}")

    decided_desktops = any(catalog[n].desktops is not None for n in ordered if n in catalog)
    return InstallPlan(
        target=target,
        packages=planned,
        group_memberships=memberships,
        file_capabilities=tuple(file_capabilities),
        consent_gates=tuple(gates),
        notes=tuple(notes),
        desktops_read=scan.desktops if decided_desktops and scan is not None else None,
        sessions_unrecognised=(scan.unrecognised if decided_desktops and scan is not None else ()),
        deferrals=tuple(deferrals),
        config_files=tuple(config_files),
        user_services=tuple(user_services),
        apt_release=apt_release,
        apt_from_release=apt_from_release,
        apt_repos=tuple(repo_additions),
    )
