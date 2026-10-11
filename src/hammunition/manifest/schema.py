# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hammunition package manifest schema.

Shaped by measurement, not convention. Every field here exists because a real
unit in `docs/reference/ahrl-inventory.md` requires it; see `docs/DECISIONS.md`
D-010, D-012, D-015 and D-016 for the evidence behind each.

Two invariants the type system itself enforces, because they are security
requirements rather than preferences:

* There is no ``method: script``. Piping remote content into a shell is
  unrepresentable, not merely discouraged.
* ``RemoteArtifact`` requires ``sha256``. An unverified download cannot be
  expressed at all.
* A ``ConsentGate`` disclosure cannot contain legal-advice wording. D-021 says
  such wording is a defect; here it is a validation error.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hammunition.desktop import Desktop

__all__ = [
    "PLACEMENT_ROOTS",
    "Binary",
    "ConsentGate",
    "DevctlHelper",
    "InstallBlock",
    "ManifestError",
    "PackageManifest",
    "PinBasis",
    "PinReview",
    "Placement",
    "ProfileManifest",
    "RiskCategory",
    "Selector",
    "Status",
    "UserService",
    "UserServiceListen",
    "derived_source_method_problem",
    "effective_binaries",
]

SHA256 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")

# Debian policy §5.6.1: at least two characters, starting alphanumeric, from
# lowercase letters, digits, plus, minus and dot. The optional suffix is an
# architecture qualifier (`libc6:i386`, `foo:any`).
#
# Validated rather than taken on trust because these strings become argv for a
# root-privileged `apt-get install`. A manifest saying
#
#     packages: ["-o", "APT::Get::AllowUnauthenticated=true", "tio"]
#
# is not a package list; it is two apt options and a package, and D-009's
# community and local tiers mean manifests will arrive from people this project
# has not met. The pre-flight probe happens to catch that one -- apt-cache
# consumes the option and returns no stanza for it, so the "asked for, not
# returned" comparison reports it unobtainable -- but that is an incidental
# property of one code path, not an invariant. This project's posture elsewhere
# is to make the bad state unrepresentable (`method: script`, mandatory
# `sha256`), and a package name is a package name.
DEB_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]+(?::[a-z0-9][a-z0-9-]*)?$")


def _check_package_names(names: Sequence[str], field: str) -> None:
    bad = [n for n in names if not DEB_PACKAGE.match(n)]
    if bad:
        raise ManifestError(
            f"{field} contains {bad!r}, which are not Debian package names. "
            f"These become argv for a privileged apt-get, so anything that is "
            f"not a package name is refused here rather than discovered later."
        )


ENDPOINT_REF = re.compile(r"\{endpoint:([a-z0-9_-]+)\}")
CONSENT_ENV = re.compile(r"^HAMMUNITION_ACCEPT_[A-Z0-9_]+$")
STATION_REF = re.compile(r"\{station\.([a-z0-9_]+)\}")


class ManifestError(ValueError):
    """Raised when a manifest is structurally valid but semantically wrong."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Selectors — (distro, version, arch) -> method.  D-002, D-012.
# ---------------------------------------------------------------------------


class Arch(StrEnum):
    x86_64 = "x86_64"
    aarch64 = "aarch64"
    armv7l = "armv7l"


class Selector(Strict):
    """Restricts an install block to some subset of targets.

    An empty selector matches everything and acts as the default. Resolution is
    first-match-wins in list order, so defaults belong last.
    """

    distro: list[str] | None = None
    distro_version: list[str] | None = None
    arch: list[Arch] | None = None

    def matches(self, distro: str, version: str, arch: str) -> bool:
        """An unset dimension matches anything; a set one must contain the value."""
        return (
            (not self.distro or distro in self.distro)
            and (not self.distro_version or version in self.distro_version)
            and (not self.arch or arch in [a.value for a in self.arch])
        )

    @property
    def is_default(self) -> bool:
        return not (self.distro or self.distro_version or self.arch)


# ---------------------------------------------------------------------------
# Verified artifacts.  D-004: no unverified downloads, ever.
# ---------------------------------------------------------------------------


class RemoteArtifact(Strict):
    """A file fetched over the network. Verification is not optional."""

    url: str
    sha256: str = Field(description="Mandatory. There is no unverified path.")
    signature_url: str | None = None
    signing_key_fingerprint: str | None = None

    @model_validator(mode="after")
    def _check(self) -> RemoteArtifact:
        if not SHA256.match(self.sha256):
            raise ManifestError(f"sha256 must be 64 lowercase hex chars: {self.sha256!r}")
        if not self.url.startswith(("https://", "http://")):
            raise ManifestError(f"url must be http(s): {self.url!r}")
        return self


class Patch(Strict):
    """An in-tree source edit. AHRL does these with sed; we declare them."""

    file: str
    description: str
    unified_diff: str | None = None


# ---------------------------------------------------------------------------
# Install methods.  Discriminated union on `method`.
# ---------------------------------------------------------------------------

TREE_MARKER_DESCRIPTION = (
    "One file, relative to the installed tree, whose presence proves the "
    "tree is what the launcher expects: yaac's YAAC.jar, js8spotter's "
    "js8spotter.py. The effect check reads it back after the run; `cp -aT` "
    "exits 0 on any directory, so without it a tree unit ended `verified: "
    "true` with no check at all (issue #27). Required exactly when the "
    "block installs a tree."
)


def _check_tree_marker(installs_tree: bool, marker: str | None, how: str) -> None:
    """A tree needs a marker and a marker needs a tree, and it stays inside."""
    if installs_tree and marker is None:
        raise ManifestError(
            f"a {how} block installs a tree but names no tree_marker -- the effect "
            f"check has nothing to read back, and `cp -aT` exits 0 on any directory"
        )
    if not installs_tree and marker is not None:
        raise ManifestError(
            f"tree_marker {marker!r} declared but this {how} block installs no tree; "
            f"nothing would ever be checked against it"
        )
    if marker is not None:
        parts = PurePosixPath(marker).parts
        if not parts or marker.startswith("/") or ".." in parts:
            raise ManifestError(
                f"tree_marker {marker!r} must be a relative path inside the tree, "
                f"with no `..` components"
            )


class AptInstall(Strict):
    method: Literal["apt"] = "apt"
    packages: list[str] = Field(min_length=1)
    install_recommends: bool = Field(
        default=True,
        description=(
            "Whether apt installs this unit's Recommends. The global default "
            "stays what every target distribution does -- Recommends are not "
            "suppressed catalog-wide -- and this is a per-unit opt-out for a "
            "package whose Recommends conflict with the target's desktop "
            "stack. Debian's `morse` Recommends pulseaudio, which Conflicts "
            "pipewire-alsa, so on a PipeWire desktop apt satisfies the "
            "transaction by removing the machine's audio routing and the plan "
            "refuses it (D-022, issue #61, measured on Parrot 7.3 + KDE "
            "2026-09-12). Set false and this unit's packages are installed by "
            "a second `apt-get install --no-install-recommends`, simulated "
            "separately and disclosed in the plan (D-052); everything else in "
            "the transaction keeps apt's defaults."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> AptInstall:
        _check_package_names(self.packages, "apt packages")
        return self


class SourceInstall(Strict):
    """Build from a verified source archive."""

    method: Literal["source"] = "source"
    source: RemoteArtifact
    build_system: Literal["autotools", "cmake", "qmake", "qmake6", "make", "custom"]
    configure_args: list[str] = Field(default_factory=list)
    build_args: list[str] = Field(default_factory=list)
    compiler_flags: list[str] = Field(
        default_factory=list,
        description="e.g. -Wno-incompatible-pointer-types. Six AHRL units need these.",
    )
    project_file: str | None = Field(
        default=None,
        description="qmake .pro / cmake subdir. MSHV needs a different one per arch.",
    )

    @field_validator("project_file")
    @classmethod
    def _project_file_inside_the_tree(cls, value: str | None) -> str | None:
        return _project_file_inside(value)

    patches: list[Patch] = Field(default_factory=list)
    build_dir: str | None = None
    autoreconf: bool = Field(
        default=False,
        description=(
            "Run autoreconf -fi before configure -- for autotools projects "
            "shipped without a generated configure (git checkouts, mostly). "
            "kalibrate-rtl proved the need (source-build-gaps #3); the "
            "planner injects the autotools toolchain when set."
        ),
    )

    provides_install_target: bool = Field(
        default=True,
        description=(
            "False when the project's build system has no install rule. "
            "The backend then installs the manifest's `binaries` explicitly "
            "instead of running `make install`, which would fail. Requires "
            "`binaries` to be declared."
        ),
    )
    install_tree: bool = Field(
        default=False,
        description=(
            "Install the whole built/extracted tree to "
            "<prefix>/share/hammunition/<name> instead of (or beside) named "
            "binaries. For software that reads settings, resources or data "
            "beside its executable -- MSHV, run-in-place trees (gaps #6/#8). "
            "Requires a launcher (or binaries) so the tree is reachable."
        ),
    )
    tree_marker: str | None = Field(default=None, description=TREE_MARKER_DESCRIPTION)

    @model_validator(mode="after")
    def _tree_marker(self) -> SourceInstall:
        _check_tree_marker(self.install_tree, self.tree_marker, self.method)
        return self


COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

PinBasis = Literal["distribution_pin", "own_choice"]
"""Where a commit pin's choice of revision came from.

``distribution_pin``
    A distribution already packages this exact commit. **This is the preferred
    basis.** Kali and Parrot both package SDR++ at ``36ea9a1``: two
    distributions independently hit the same missing-tags problem and answered
    it the same way, which makes their commit a review signal upstream stopped
    providing. Pinning it also means a source build and an apt install are the
    same revision rather than two.

``own_choice``
    Nothing packages it and we had to choose. Legitimate, and more expensive: it
    is a judgement nobody else has vetted, so the rationale must say which
    distributions were checked and what they ship instead. Recording that we
    *had to* is the point -- the next reviewer should be able to see whether the
    situation has changed.
"""

# The `requires_kernel` vocabulary. One entry per subsystem the probe in
# `hammunition.kernel` knows how to find; a name here without a module path
# there is a test failure, so a manifest can never name a feature the plan
# cannot check.
KernelFeature = Literal["ax25"]


class PinReview(Strict):
    """When a commit pin was last looked at, and by whom.  D-024.

    A tag carries an upstream signal: someone decided that revision was worth
    naming. A commit SHA carries none — it is perfectly pinned and perfectly
    arbitrary. When a project stops tagging, pinning a commit is the right
    answer, but it moves a judgement upstream stopped making onto us, and an
    unreviewed commit pin from four years ago is the same failure as an
    abandoned tag pointed the other way.

    So the judgement is recorded rather than implied. This is metadata about
    *our* decision, not about the software, which is why it lives beside the
    ref rather than in documentation.
    """

    last_reviewed: date
    reviewed_by: str = Field(
        min_length=2,
        description="Who looked. A name or handle, so the next reviewer knows who to ask.",
    )
    basis: PinBasis = Field(
        description=(
            "Where the choice of commit came from. `distribution_pin` is strongly "
            "preferred and must name the distributions; `own_choice` requires "
            "saying what was checked and found nothing."
        )
    )
    distributions: list[str] = Field(
        default_factory=list,
        description="Distributions packaging this exact commit. Required for `distribution_pin`.",
    )
    rationale: str = Field(
        min_length=30,
        description=(
            "Why THIS commit rather than any other, and what was checked. "
            "'HEAD at the time' is not a rationale; it is the absence of one."
        ),
    )
    cadence_days: int = Field(
        default=180,
        ge=30,
        le=730,
        description="How long this pin may stand before it must be looked at again.",
    )

    @model_validator(mode="after")
    def _basis(self) -> PinReview:
        if self.basis == "distribution_pin" and not self.distributions:
            raise ManifestError(
                "pin_review basis is 'distribution_pin' but names no distributions. "
                "The whole value of this basis is that somebody else vetted the "
                "revision; say who."
            )
        if self.basis == "own_choice":
            if self.distributions:
                raise ManifestError(
                    "pin_review basis is 'own_choice' but names distributions. If a "
                    "distribution packages this commit, the basis is distribution_pin."
                )
            if len(self.rationale) < 80:
                raise ManifestError(
                    "an 'own_choice' pin_review needs a fuller rationale: which "
                    "distributions were checked, what they ship instead, and why "
                    "this commit. Choosing a revision nobody else vetted is the "
                    "expensive path and the reasoning has to survive the next "
                    "reviewer (D-024)."
                )
        return self

    @property
    def due(self) -> date:
        from datetime import timedelta

        return self.last_reviewed + timedelta(days=self.cadence_days)

    def is_overdue(self, today: date) -> bool:
        return today > self.due


#: The digest a manifest carries while the artifact it will pin has not been
#: published: 64 zeros. pip can never match it, and the planner refuses it by
#: name before any step runs, so an unfinished pin fails at `--dry-run`, not
#: at the pip step after the apt work.
UNPINNED_SHA256 = "0" * 64


def _unhashed(lines: Sequence[str]) -> list[str]:
    """Requirement lines that carry no ``--hash=sha256:`` pin."""
    return [
        line
        for line in lines
        if line.strip()
        and not line.lstrip().startswith(("#", "--"))
        and "--hash=sha256:" not in line
    ]


def _project_file_inside(value: str | None) -> str | None:
    """A ``project_file`` is a path inside the unpacked tree: the cmake path
    configures ``<tree>/<project_file>`` (D-061, amended 2026-10-02)."""
    if value is not None and not _inside_tree(value):
        raise ManifestError(
            f"project_file must be a relative path inside the source tree, with no '..': {value!r}"
        )
    return value


def _inside_tree(path: str) -> bool:
    """A relative path with no ``..`` component: it cannot leave the tree."""
    parts = PurePosixPath(path).parts
    return bool(path.strip()) and not path.startswith("/") and ".." not in parts


#: The variables the git backend sets on a prepare step itself (D-069): the
#: build Python's ``PATH`` and ``VIRTUAL_ENV``, and the job count. A manifest
#: setting one would silently undo the engine's.
ENGINE_BUILD_ENV = frozenset({"PATH", "VIRTUAL_ENV", "CMAKE_BUILD_PARALLEL_LEVEL"})


class PrepareStep(Strict):
    """An upstream script run in the checked-out tree before configure (D-069).

    CoMaps' ``configure.sh`` generates the symbols, drawing rules and strings
    the CMake build reads, and builds a helper tool to do it. The script is
    upstream's, named by path, never a command line the catalog writes; the
    engine owns how it runs. ``produces`` is what makes it checkable:
    CoMaps' ``generate_symbols.sh`` exits 0 with no symbols when optipng is
    missing, so a script's exit status is not evidence of anything (D-031).
    """

    script: str = Field(description="Path of the script, relative to the tree; run as ./<script>.")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Upstream's own switches, e.g. SKIP_PYTHON_VENV=1 so CoMaps' script does "
            "not pip-install an unpinned protobuf. Never secrets: the plan prints it."
        ),
    )
    produces: list[str] = Field(
        min_length=1,
        description=(
            "Globs relative to the tree; each must match at least one non-empty "
            "regular file after the script, or the step fails naming it."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> PrepareStep:
        for path in (self.script, *self.produces):
            if not _inside_tree(path):
                raise ManifestError(
                    f"prepare path {path!r} must be a relative path inside the tree, "
                    f"with no `..` components"
                )
        owned = sorted(ENGINE_BUILD_ENV & set(self.env))
        if owned:
            raise ManifestError(
                f"prepare env sets {', '.join(owned)}, which the engine sets itself on "
                f"this step (the build Python and the job count)"
            )
        return self


class ExtraArtifact(RemoteArtifact):
    """A pinned file installed beside a build, with its size (D-069)."""

    size: int = Field(
        gt=0, description="Bytes, as published. Printed in the plan, checked on fetch."
    )


class ExtraFile(Strict):
    """A file a build's install rule leaves out, installed after it (D-069).

    Either a pinned artifact or a file from the built tree, installed at
    ``<prefix>/<install_as>`` with mode 0644, after an ``rm -f`` so a symlink
    at the destination is replaced and never written through. CoMaps'
    install rule skips ``World.mwm`` and ``WorldCoasts.mwm`` when the tree has
    none (they are downloaded, not built), and leaves out
    ``categories_brands.txt``; Flathub's manifest installs all three by hand.
    """

    artifact: ExtraArtifact | None = None
    from_tree: str | None = Field(
        default=None, description="A file in the checked-out tree, relative to it."
    )
    install_as: str = Field(description="Relative to the prefix, under share/.")

    @model_validator(mode="after")
    def _check(self) -> ExtraFile:
        if (self.artifact is None) == (self.from_tree is None):
            raise ManifestError(
                "an extra file names exactly one of `artifact` (a pinned download) or "
                "`from_tree` (a file of the built tree)"
            )
        if self.from_tree is not None and not _inside_tree(self.from_tree):
            raise ManifestError(
                f"from_tree {self.from_tree!r} must be a relative path inside the tree, "
                f"with no `..` components"
            )
        parts = PurePosixPath(self.install_as).parts
        if not _inside_tree(self.install_as) or len(parts) < 2 or parts[0] != "share":
            raise ManifestError(
                f"extra file install_as {self.install_as!r} must be a relative path under "
                f"share/ in the prefix: data beside a build, never an executable or a "
                f"library, and never outside the prefix"
            )
        return self


class GitInstall(Strict):
    """Build from a pinned git revision. `ref` must be immutable."""

    method: Literal["git"] = "git"
    repo: str
    ref: str = Field(description="Commit SHA or tag. Never a branch name.")
    build_system: Literal["autotools", "cmake", "qmake", "qmake6", "make", "custom"]
    configure_args: list[str] = Field(default_factory=list)
    compiler_flags: list[str] = Field(default_factory=list)
    project_file: str | None = Field(
        default=None,
        description="qmake .pro / cmake subdir, as for a source build.",
    )

    @field_validator("project_file")
    @classmethod
    def _project_file_inside_the_tree(cls, value: str | None) -> str | None:
        return _project_file_inside(value)

    build_args: list[str] = Field(default_factory=list)
    autoreconf: bool = Field(
        default=False,
        description=(
            "Run autoreconf -fi before configure -- for autotools projects "
            "shipped without a generated configure (git checkouts, mostly). "
            "kalibrate-rtl proved the need (source-build-gaps #3); the "
            "planner injects the autotools toolchain when set."
        ),
    )
    provides_install_target: bool = Field(
        default=True,
        description=(
            "False when the project's build system has no install rule. See "
            "SourceInstall for the full note."
        ),
    )
    install_tree: bool = Field(
        default=False,
        description=(
            "Install the whole built/extracted tree to "
            "<prefix>/share/hammunition/<name> instead of (or beside) named "
            "binaries. For software that reads settings, resources or data "
            "beside its executable -- MSHV, run-in-place trees (gaps #6/#8). "
            "Requires a launcher (or binaries) so the tree is reachable."
        ),
    )
    patches: list[Patch] = Field(
        default_factory=list,
        description=(
            "Unified diffs applied after the checkout and before the build, in "
            "order, exactly as a source block's. linbpq's makefile runs `sudo "
            "setcap` inside the build (#96); the patch that removes it is the "
            "first use."
        ),
    )
    tree_marker: str | None = Field(default=None, description=TREE_MARKER_DESCRIPTION)
    pin_review: PinReview | None = Field(
        default=None,
        description="Required when `ref` is a commit SHA rather than a tag. D-024.",
    )
    commit: str | None = Field(
        default=None,
        description=(
            "For a tag `ref`: the commit it must resolve to. The pin check then "
            "refuses a re-cut tag instead of only recording what it resolved to. "
            "CoMaps' tag is the one Flathub, nixpkgs and the AUR build, at this "
            "commit (D-024, D-069)."
        ),
    )
    submodules: bool = Field(
        default=False,
        description=(
            "Check out every submodule, recursively, at the superproject's gitlinks, "
            "shallow (`git submodule update --init --recursive --depth 1`, upstream "
            "CoMaps' own command), then refuse unless `git submodule status "
            "--recursive` shows each one at its gitlink. D-069."
        ),
    )
    build_python: list[str] = Field(
        default_factory=list,
        description=(
            "Hash-pinned requirement lines for a Python the build needs, installed "
            "into a venv in the build directory with --require-hashes; prepare, "
            "configure and compile run with it first on the PATH. CoMaps' CMake "
            "refuses Debian's protobuf 4.x (D-069). Build-only: it is discarded "
            "with the build directory and never reaches the operator."
        ),
    )
    prepare: PrepareStep | None = Field(
        default=None,
        description="An upstream script run in the tree before configure. D-069.",
    )
    extra_files: list[ExtraFile] = Field(
        default_factory=list,
        description="Files the install rule leaves out, installed after it. D-069.",
    )

    @model_validator(mode="after")
    def _pinned(self) -> GitInstall:
        if self.ref in {"master", "main", "HEAD", "trunk", "develop"}:
            raise ManifestError(f"ref {self.ref!r} is a moving branch; pin a commit SHA or tag")
        if COMMIT_SHA.match(self.ref) and self.pin_review is None:
            raise ManifestError(
                f"ref {self.ref!r} is a commit SHA and needs a pin_review. A tag carries "
                f"an upstream signal that a revision was worth naming; a SHA carries none, "
                f"so pinning one moves a judgement upstream stopped making onto us. "
                f"Record when it was reviewed, by whom, and why this commit (D-024)."
            )
        if not COMMIT_SHA.match(self.ref) and self.pin_review is not None:
            raise ManifestError(
                f"ref {self.ref!r} is a tag, so pin_review does not apply -- upstream "
                f"already made the judgement this field exists to record"
            )
        return self

    @model_validator(mode="after")
    def _tree_marker(self) -> GitInstall:
        _check_tree_marker(self.install_tree, self.tree_marker, self.method)
        return self

    @model_validator(mode="after")
    def _build_fields(self) -> GitInstall:
        if self.commit is not None:
            if COMMIT_SHA.match(self.ref):
                raise ManifestError(
                    f"commit is for a tag ref; ref {self.ref!r} is already a commit"
                )
            if not COMMIT_SHA.match(self.commit):
                raise ManifestError(
                    f"commit must be 40 lowercase hex characters, got {self.commit!r}"
                )
        unhashed = _unhashed(self.build_python)
        if unhashed:
            raise ManifestError(
                f"build_python lines without a --hash=sha256: pin: {unhashed[:3]} -- "
                f"non-apt sources are verified or refused, at build time too"
            )
        names = [f.install_as for f in self.extra_files]
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            raise ManifestError(f"extra files would install {', '.join(twice)} twice")
        return self


PLACEMENT_ROOTS: tuple[str, ...] = (
    "/usr/local/bin/",
    "/usr/local/share/",
    "/usr/share/plasma/plasmoids/",
    "/usr/share/hammunition-tray-qt/",
    "/usr/share/icons/hicolor/",
    "/usr/share/applications/",
    "/etc/xdg/autostart/",
)
"""The only places a prebuilt tree's files may be spread to (hammunition-tray
0.5.0, which publishes no .deb yet). Each is a directory a .deb of the same
software would have owned. **Not** ``/usr/local/libexec``, ``/usr/local/sbin``
or ``/usr/local/lib``: the first holds the wrapper polkit authorises to run as
root, the others are on root's path or hold the helper's code, and the helper
has a block of its own (``devctl_helper``) for exactly that reason. And every
destination must also be *named for this project* (a path component containing
``hammunition`` or ``chiefgyk3d``), so a catalog entry cannot place an
arbitrary autostart entry, a menu entry or a program on the path under a name
of its choosing. ``/usr/local/`` follows the engine's prefix (the planner maps
it). A catalog is data: it names a destination from this list or it does not
load."""

PROJECT_NAMES = ("hammunition", "chiefgyk3d")

PLACEMENT_MODES = frozenset({"0644", "0755"})


class Placement(Strict):
    """One file of an unpacked archive, installed at an absolute path.

    The same shape as a ``.deb``'s file list: a source inside the tree, a
    destination, a mode. Never a command, never a glob -- every file is named,
    so the plan prints each one and a file upstream adds later is not installed
    by surprise (a test compares the list with the pinned archive).
    """

    source: str = Field(description="A file inside the unpacked tree, relative to its root.")
    dest: str = Field(
        description=(
            "Where it is installed: an absolute path under one of PLACEMENT_ROOTS, with a "
            "path component named for this project. Under /usr/local/ it follows the "
            "engine's prefix."
        )
    )
    mode: str = "0644"

    @model_validator(mode="after")
    def _check(self) -> Placement:
        parts = PurePosixPath(self.source).parts
        if not parts or self.source.startswith("/") or ".." in parts:
            raise ManifestError(
                f"placement source {self.source!r} must be a relative path inside the "
                f"archive, with no `..` components"
            )
        dest = PurePosixPath(self.dest)
        if (
            not self.dest.startswith("/")
            or ".." in dest.parts
            or "//" in self.dest
            or self.dest.endswith("/")
            or "\n" in self.dest
            or "\0" in self.dest
        ):
            raise ManifestError(
                f"placement dest {self.dest!r} must be a normal absolute path naming a file"
            )
        if not any(self.dest.startswith(root) and self.dest != root for root in PLACEMENT_ROOTS):
            raise ManifestError(
                f"placement dest {self.dest!r} is outside the places a prebuilt archive may be "
                f"spread to ({', '.join(PLACEMENT_ROOTS)})"
            )
        if not any(name in part for part in dest.parts for name in PROJECT_NAMES):
            raise ManifestError(
                f"placement dest {self.dest!r} is not named for this project (a path component "
                f"containing {' or '.join(PROJECT_NAMES)}): a catalog entry does not get to "
                f"place a program, a menu entry or an autostart entry under any name it likes"
            )
        if self.mode not in PLACEMENT_MODES:
            raise ManifestError(
                f"placement mode {self.mode!r} must be one of {sorted(PLACEMENT_MODES)}: "
                f"nothing placed here is writable, setuid or private"
            )
        return self


MODULE_FILE = re.compile(r"^[a-z_][a-z0-9_]*\.py$")


class DevctlHelper(Strict):
    """The privileged device helper that ships inside hammunition-tray's archive.

    **A block that names an interpreter nobody wrote down.** The helper is root
    code behind one polkit action (D-056); the engine copies it from the unpacked
    archive to the paths the tray's contract fixes, bakes the engine's own venv
    interpreter into the wrapper (the helper's ``time`` verbs import the
    engine), and writes the tray's polkit action. None of those paths or that
    interpreter is catalog data, so the block carries only what varies by pin:
    where the code sits in the archive and which modules it has.
    """

    source: str = Field(
        default="devctl",
        description=(
            "The directory in the archive holding the `hammunition-devctl` entry "
            "script and the `hammunition_devctl/` package."
        ),
    )
    modules: list[str] = Field(
        min_length=1,
        description=(
            "Every module of the package, by file name. Explicit, so the plan lists "
            "exactly the files root will run, and a test compares it with the pinned "
            "archive."
        ),
    )
    min_contract: int = Field(
        default=1,
        ge=1,
        description=(
            "The contract number (`hammunition-devctl --version`) an already-installed "
            "helper must answer for the engine to leave it alone."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> DevctlHelper:
        parts = PurePosixPath(self.source).parts
        if not parts or self.source.startswith("/") or ".." in parts:
            raise ManifestError(
                f"devctl_helper source {self.source!r} must be a relative directory inside "
                f"the archive, with no `..` components"
            )
        bad = [m for m in self.modules if not MODULE_FILE.match(m)]
        if bad:
            raise ManifestError(f"devctl_helper modules {bad!r} are not plain .py file names")
        if len(set(self.modules)) != len(self.modules):
            raise ManifestError("devctl_helper modules lists a file twice")
        missing = {"__init__.py", "devctl.py"} - set(self.modules)
        if missing:
            raise ManifestError(
                f"devctl_helper modules lacks {sorted(missing)}: the package cannot be "
                f"imported, and the wrapper would answer nothing"
            )
        return self


class BinaryInstall(Strict):
    """Vendor .deb, archive, or prebuilt executable."""

    method: Literal["binary"] = "binary"
    artifact: RemoteArtifact
    format: Literal["deb", "tarball", "zip", "executable", "appimage"]
    placements: list[Placement] = Field(
        default_factory=list,
        description=(
            "Files of the unpacked archive installed at absolute paths, as a .deb's "
            "file list would, for an archive upstream publishes before it publishes "
            "a package (hammunition-tray 0.5.0). Root-owned, printed one by one in "
            "the plan, removed on uninstall on the log's attribution."
        ),
    )
    placement_dirs: list[str] = Field(
        default_factory=list,
        description=(
            "Directories under PLACEMENT_ROOTS that hold nothing but this unit's "
            "placements; uninstall removes them whole, and only when the log "
            "attributes a placement inside them."
        ),
    )
    devctl_helper: DevctlHelper | None = Field(
        default=None,
        description="The tray's privileged device helper, installed from this archive.",
    )

    @model_validator(mode="after")
    def _placements_belong_to_an_archive(self) -> BinaryInstall:
        if not (self.placements or self.placement_dirs or self.devctl_helper):
            return self
        if self.format not in ("tarball", "zip"):
            raise ManifestError(
                f"placements, placement_dirs and devctl_helper unpack an archive; "
                f"format: {self.format} has none (a .deb places its own files)"
            )
        dests = [p.dest for p in self.placements]
        twice = sorted({d for d in dests if dests.count(d) > 1})
        if twice:
            raise ManifestError(f"placements would install {', '.join(twice)} twice")
        for directory in self.placement_dirs:
            inside = directory.rstrip("/") + "/"
            if (
                not directory.startswith("/")
                or ".." in PurePosixPath(directory).parts
                or directory.endswith("/")
                or not any(
                    directory.startswith(root) or directory + "/" == root
                    for root in PLACEMENT_ROOTS
                )
            ):
                raise ManifestError(
                    f"placement_dirs {directory!r} must be a directory below one of "
                    f"{', '.join(PLACEMENT_ROOTS)}"
                )
            leaf = PurePosixPath(directory).name
            if not any(name in leaf for name in PROJECT_NAMES):
                raise ManifestError(
                    f"placement_dirs {directory!r} is not named for this project: a directory "
                    f"removed whole must be one nothing else shares (a hicolor `apps` directory "
                    f"holds every program's icons)"
                )
            if not any(d.startswith(inside) for d in dests):
                raise ManifestError(
                    f"placement_dirs {directory!r} holds none of this unit's placements; "
                    f"uninstall would remove a directory nothing here put there"
                )
        return self

    deb_package: str | None = Field(
        default=None,
        description=(
            "The control-file Package name a `deb` artifact installs, read "
            "from the .deb itself (`dpkg-deb -f file.deb Package`), never "
            "assumed from the filename — wsjtx-improved's vendor deb installs "
            "as `wsjtx`, and GridTracker2's filename casing matches nothing. "
            "Required for format: deb; it is what `uninstall` hands to "
            "`apt-get remove` and what `status` probes."
        ),
    )
    strip_components: int = 0
    install_tree: bool = Field(
        default=False,
        description=(
            "Install the whole built/extracted tree to "
            "<prefix>/share/hammunition/<name> instead of (or beside) named "
            "binaries. For software that reads settings, resources or data "
            "beside its executable -- MSHV, run-in-place trees (gaps #6/#8). "
            "Requires a launcher (or binaries) so the tree is reachable."
        ),
    )
    tree_marker: str | None = Field(default=None, description=TREE_MARKER_DESCRIPTION)

    @model_validator(mode="after")
    def _tree_marker(self) -> BinaryInstall:
        _check_tree_marker(self.install_tree, self.tree_marker, self.method)
        if self.format == "executable" and self.install_tree:
            # D-076: one file installed as a tree (GraphHopper's jar) is staged
            # under its marker's name, so the marker is that file's plain name.
            marker = self.tree_marker or ""
            if PurePosixPath(marker).name != marker or marker in (".", ".."):
                raise ManifestError(
                    f"a single executable installed as a tree is installed under its "
                    f"tree_marker, so the marker must be a plain file name, not {marker!r}"
                )
        return self

    @model_validator(mode="after")
    def _deb_package_matches_format(self) -> BinaryInstall:
        if self.format == "deb":
            if self.deb_package is None:
                raise ManifestError(
                    "format: deb requires deb_package — the control-file name "
                    "the archive installs, read with `dpkg-deb -f file.deb "
                    "Package`. Without it, uninstall and status have nothing "
                    "true to hand to apt."
                )
            _check_package_names([self.deb_package], "deb_package")
        elif self.deb_package is not None:
            raise ManifestError(
                f"deb_package is only meaningful for format: deb, not "
                f"format: {self.format} — nothing here reaches dpkg's database."
            )
        return self


class VenvInstall(Strict):
    """A per-user Python virtualenv, hash-pinned end to end.

    ``requirements`` lines are requirements-file syntax and **every package
    line must carry at least one ``--hash=sha256:``** — pip then runs with
    ``--require-hashes``, which extends the demand to the whole dependency
    tree. That is CLAUDE.md's checksum rule applied to PyPI: apt packages are
    distribution-signed, a bare ``pip install name`` is neither signed nor
    pinned, and the difference is exactly what the rule exists for. Generate
    the lines with ``uv pip compile --universal --generate-hashes``.

    Environment-marker lines (``; python_version >= "3.10"``) and blank or
    comment lines pass through untouched.
    """

    method: Literal["venv"] = "venv"
    requirements: list[str] = Field(min_length=1)
    python: str = ">=3.11"
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Build-time environment for pip. Exists for one measured case: a "
            "project using setuptools-scm, installed from a hashed release "
            "archive, has no .git to read its version from and needs "
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<NAME> (nanovna-saver proved "
            "it, 2026-08-30). Never secrets -- the plan prints this."
        ),
    )
    payload: RemoteArtifact | None = Field(
        default=None,
        description=(
            "A verified archive whose extracted tree installs to "
            "<prefix>/share/hammunition/<name>, for software that is a data "
            "tree run by a venv rather than a pip-installable package. The "
            "two-unit demand (source-build-gaps #9): radiosonde_auto_rx and "
            "supersdr. Launchers reach the venv with the {venv} placeholder."
        ),
    )
    payload_build_script: str | None = Field(
        default=None,
        description=(
            "A script inside the verified payload tree, run with sh before "
            "the tree installs -- radiosonde_auto_rx compiles its C "
            "demodulators via auto_rx/build.sh. Requires payload; declare "
            "its toolchain in the block's build_depends."
        ),
    )
    tree_marker: str | None = Field(default=None, description=TREE_MARKER_DESCRIPTION)
    expose: list[str] = Field(
        default_factory=list,
        description=(
            "Console-script names from the venv's bin/ to wrap onto the "
            "operator's PATH (~/.local/bin). A venv nobody can invoke installs "
            "nothing while reporting success."
        ),
    )
    licence: str | None = Field(
        default=None,
        min_length=2,
        description=(
            "The terms the installed software is under, when they are not a "
            "licence the operator would assume (SPDX where one exists, else the "
            "publisher's own words). Printed on the plan line that installs the "
            "venv, before the confirmation, and stated, never adjudicated (D-021, "
            "D-033). Requires licence_url."
        ),
    )
    licence_url: str | None = Field(
        default=None,
        description="Where those terms are stated, on the publisher's site. Requires licence.",
    )

    @model_validator(mode="after")
    def _licence_has_its_url(self) -> VenvInstall:
        if (self.licence is None) != (self.licence_url is None):
            raise ManifestError(
                "a venv block's licence and licence_url are set together or not at all"
            )
        if self.licence_url is not None and not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        return self

    @model_validator(mode="after")
    def _payload_script_needs_payload(self) -> VenvInstall:
        if self.payload_build_script and not self.payload:
            raise ManifestError(
                "payload_build_script declared with no payload — there is no tree to run it in"
            )
        return self

    @model_validator(mode="after")
    def _tree_marker(self) -> VenvInstall:
        _check_tree_marker(self.payload is not None, self.tree_marker, "venv payload")
        return self

    @model_validator(mode="after")
    def _hashes(self) -> VenvInstall:
        unhashed = _unhashed(self.requirements)
        if unhashed:
            raise ManifestError(
                f"venv requirements without a --hash=sha256: pin: {unhashed[:3]} — "
                f"non-apt sources are verified or refused (CLAUDE.md security "
                f"requirements); regenerate with uv pip compile --generate-hashes"
            )
        return self


class NodeInstall(Strict):
    """A Node.js application built from a verified source archive (D-037).

    One measured user: ``openhamclock``, a Node and Vite web application whose
    release publishes no binary. The build is ``npm ci`` -> ``npm run
    <build_script>`` -> ``npm prune --omit=dev``, every step with lifecycle
    scripts ignored, and the pruned tree installs per-user under
    ``$XDG_DATA_HOME/hammunition/node/<name>`` with a wrapper on the
    operator's PATH that runs ``node <entry>`` from it.

    What it costs and how it is disclosed: Node comes only from the
    distribution's ``nodejs``/``npm`` packages, never fetched, and the plan
    refuses when they are absent or older than ``node_min_version``. ``npm ci``
    fetches the dependency closure from registry.npmjs.org, each tarball
    verified against the sha512 in ``package-lock.json`` — which lives inside
    the sha256-verified archive, so the whole closure is transitively pinned
    from one manifest hash. Both facts are printed in the plan.
    """

    method: Literal["node"] = "node"
    artifact: RemoteArtifact = Field(
        description="The sha256-pinned source archive. Must contain package-lock.json."
    )
    node_min_version: str = Field(
        pattern=r"^\d+\.\d+$",
        description=(
            "The lowest Node.js version, as `MAJOR.MINOR`, the application "
            "(its bundler included) runs on; the plan refuses below it. "
            "Measured by running it, never read from `engines` alone: "
            "openhamclock's dependencies satisfy Vite's `^18` floor and its "
            "server still dies on Node 18 at start, because `require()` of an "
            "ES module needs 20.19 -- a minor, which is why this is not a major."
        ),
    )
    entry: str = Field(description="The script `node` runs from the tree root, e.g. server.js.")
    build_script: str | None = Field(
        default="build",
        description=(
            "The package.json script that produces the runtime tree (Vite's "
            "`build`). None for an application that runs from source unbuilt."
        ),
    )
    build_output: str | None = Field(
        default="dist",
        description=(
            "A directory build_script must produce, checked after the build "
            "(D-031: an `npm run build` exiting 0 is not evidence it built). "
            "None only when build_script is None."
        ),
    )
    command: str | None = Field(
        default=None,
        description=(
            "Name of the wrapper put on the operator's PATH (~/.local/bin). "
            "Defaults to the manifest name."
        ),
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Runtime environment baked into the wrapper, e.g. PORT. HOST is "
            "refused: the engine always binds a node application to "
            "127.0.0.1 (D-037), and a manifest cannot widen that."
        ),
    )
    preserve: list[str] = Field(
        default_factory=lambda: [".env"],
        description=(
            "Files inside the installed tree a reinstall keeps: an application "
            "that writes its configuration beside itself (openhamclock's .env) "
            "would otherwise lose it on every update."
        ),
    )
    patches: list[Patch] = Field(
        default_factory=list,
        description=(
            "Unified diffs applied to the unpacked tree before npm runs, the "
            "source backend's mechanism. First user: openhamclock 26.7.0 reads "
            "HOST and then listens on 0.0.0.0 anyway, so the loopback bind the "
            "engine sets is one line of upstream away from meaning nothing."
        ),
    )

    @model_validator(mode="after")
    def _host_is_ours(self) -> NodeInstall:
        if "HOST" in self.env:
            raise ManifestError(
                "a node block may not set HOST — the engine binds every node "
                "application to 127.0.0.1 and the manifest cannot widen it (D-037)"
            )
        if self.build_script is None and self.build_output is not None:
            raise ManifestError("build_output declared with no build_script to produce it")
        if self.build_script is not None and self.build_output is None:
            raise ManifestError(
                "a build_script must name its build_output — the tree it produces is "
                "what the run checks for, not the script's exit status (D-031)"
            )
        if self.command is not None and not SLUG.match(self.command):
            raise ManifestError(f"command must be a lowercase name: {self.command!r}")
        return self


#: One path component a data unit's archive is extracted into (D-071).
_PLAIN_NAME = re.compile(r"(?!\.{1,2}$)[A-Za-z0-9._-]+")


class DataArtifact(RemoteArtifact):
    """One file of an offline dataset: a map tileset, a Wikipedia ZIM, cty.dat.

    ``size`` is declared so the plan can print it before the confirmation
    (D-049): a 1.33 GB Geofabrik extract on a field connection is a decision,
    and the number belongs in front of the operator, not in the download's
    progress bar. The fetch verifies the declared size against the bytes it
    received, so a wrong declaration is a refused manifest rather than a
    surprise.
    """

    size: int = Field(
        gt=0, description="Bytes, as published. Printed in the plan, verified on fetch."
    )
    format: Literal["file", "zip", "tarball"] = "file"
    install_as: str | None = Field(
        default=None,
        description=(
            "For format: file, the name the file is installed under inside the "
            "unit's data directory. Archives extract their members and take none."
        ),
    )
    members: list[str] | None = Field(
        default=None,
        description=(
            "For an archive: extract only these paths, as the archive names them "
            "(its top directory included); one ending in `/` takes everything "
            "below it. A member that matches nothing refuses the install. "
            "Without it the whole archive is extracted (D-071)."
        ),
    )
    into: str | None = Field(
        default=None,
        description=(
            "For an archive: the subdirectory of the unit's data directory it is "
            "extracted into, one plain name. Required on every archive of a unit "
            "with more than one archive, or with files beside an archive: an "
            "archive replaces the directory it is extracted into (D-071)."
        ),
    )

    @model_validator(mode="after")
    def _install_name(self) -> DataArtifact:
        if self.format == "file":
            if not self.install_as:
                raise ManifestError("a data file needs install_as: the name it is kept under")
            if "/" in self.install_as or self.install_as in {".", ".."}:
                raise ManifestError(f"install_as must be a bare file name, got {self.install_as!r}")
            if self.members is not None or self.into is not None:
                raise ManifestError(
                    "members and into are for an archive only; a data file is kept under install_as"
                )
        elif self.install_as is not None:
            raise ManifestError(
                f"install_as is only meaningful for format: file, not {self.format}: an archive's "
                f"members keep their own names"
            )
        if self.into is not None and not _PLAIN_NAME.fullmatch(self.into):
            raise ManifestError(
                f"into must be one plain name (letters, digits, . _ -), got {self.into!r}"
            )
        for member in self.members or ():
            if not member or member.startswith("/") or "\\" in member or ".." in member.split("/"):
                raise ManifestError(
                    f"an archive member is a relative path inside the archive, with no '..', "
                    f"got {member!r}"
                )
        if self.members is not None and not self.members:
            raise ManifestError("an empty members list would extract nothing; omit it for all")
        return self


class DataInstall(Strict):
    """Offline data whose payload is the point (D-049).

    Not software: the engine never executes what it installs here. The files
    land under ``<prefix>/share/hammunition/data/<name>/`` and the reader --
    ``kiwix``, ``mbtileserver``, a logger reading cty.dat -- names the data
    unit in its own ``depends``. Every artifact is pinned and hashed like any
    other fetch; nothing is mirrored.

    ``licence`` and ``licence_url`` are disclosed in the plan beside the
    size, because a dataset under ODbL or CC BY-SA carries obligations the
    engine states and does not adjudicate (D-021).
    """

    method: Literal["data"] = "data"
    artifacts: list[DataArtifact] = Field(min_length=1)
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")

    @model_validator(mode="after")
    def _check(self) -> DataInstall:
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        names = [a.install_as for a in self.artifacts if a.install_as]
        if len(set(names)) != len(names):
            raise ManifestError("two data artifacts would install under the same name")
        # D-071: an archive rebuilds the directory it is extracted into, so a
        # second archive, or a file installed beside one, would be wiped by it.
        archives = [a for a in self.artifacts if a.format != "file"]
        if len(archives) > 1 or (archives and names):
            if any(a.into is None for a in archives):
                raise ManifestError(
                    "a unit with more than one archive, or files beside an archive, needs "
                    "into on every archive: each is extracted into its own subdirectory, "
                    "which it replaces"
                )
            intos = [a.into for a in archives]
            if len(set(intos)) != len(intos) or set(intos) & set(names):
                raise ManifestError(
                    "two data artifacts would install under the same name (an archive's into "
                    "and another's into or install_as)"
                )
        return self


class RegionalDataInstall(Strict):
    """An offline map region, fetched by the station's own selection.

    Unlike `DataInstall`, no artifact is pinned in the manifest: a Geofabrik
    extract is one of hundreds of regions, and the operator's choice lives in
    station config (D-035), not the catalog. The catalog states the provider
    and the licence; the engine resolves the region and its checksum at plan
    time from the operator's selection, the same "a missing value defers one
    file, never the transaction" rule as any other station-dependent unit.
    """

    method: Literal["osm-regions"] = "osm-regions"
    provider: Literal["geofabrik"] = "geofabrik"
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")

    @model_validator(mode="after")
    def _check(self) -> RegionalDataInstall:
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        return self


class DemTilesInstall(Strict):
    """Elevation tiles for the squares the station's map regions cover (D-061).

    Like `RegionalDataInstall`, nothing is pinned in the manifest: which
    tiles are needed follows the operator's regions in station config, and
    each tile is resolved at plan time and verified by a sha256 the catalog
    carries (``catalog/data/copernicus-glo30-pins.yaml``) or by the MD5 in
    the publisher's object metadata, the plan saying which, tile by tile.
    `provider` is an enum so another source is a new member the engine
    implements, never a URL in the catalog: ``usgs-3dep`` (D-068, amended
    2026-10-01) is USGS 3DEP 1/3-arc-second bare earth, chosen by the
    station's ``dem_source`` and checked by the S3 ETag its publisher lists.
    """

    method: Literal["dem-tiles"] = "dem-tiles"
    provider: Literal["copernicus-glo30", "usgs-3dep"] = "copernicus-glo30"
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")

    @model_validator(mode="after")
    def _check(self) -> DemTilesInstall:
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        return self


class TopoQuadsInstall(Strict):
    """Official topographic map sheets for the station's map regions (D-068).

    Like `DemTilesInstall`, nothing is pinned in the manifest: which sheets
    are needed follows the operator's regions in station config, chosen at
    plan time from a carried, generated index
    (``catalog/data/ustopo-quads.txt``), each checked against the S3 ETag
    its publisher lists, the plan saying so sheet by sheet. `provider` is an
    enum, never a URL in the catalog: ``usfs-fstopo`` (D-068, amended
    2026-10-01) is the Forest Service's FSTopo series, chosen from
    ``catalog/data/fstopo-quads.txt``; the Forest Service publishes no
    checksum, so a sheet is checked by a sha256 Hammunition pinned where one
    was measured and is otherwise disclosed as unverified, by name.
    """

    method: Literal["topo-quads"] = "topo-quads"
    provider: Literal["usgs-ustopo", "usfs-fstopo"] = "usgs-ustopo"
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")

    @model_validator(mode="after")
    def _check(self) -> TopoQuadsInstall:
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        return self


class RegisterInstall(Strict):
    """A publisher's whole file, rebuilt so often that no pin survives and
    checked by no digest the publisher offers (D-074, amended 2026-10-01).

    The one member today is ``acma-rrl``: the ACMA's Register of
    Radiocommunications Licences, one zip at one URL, rebuilt daily, whose
    ETag is a storage version stamp rather than a digest. `provider` is an
    enum so the URL, the file's name and its check live in the engine
    (:mod:`hammunition.acma`), never in the catalog, as with `dem-tiles`.
    What is checked is the file's own structure (every member's CRC-32 and
    the tables a reader needs), and the plan says "unverified" beside it:
    such a unit is installed by name only, in no profile (the FSTopo ruling,
    D-068 amended 2026-10-01).
    """

    method: Literal["register"] = "register"
    provider: Literal["acma-rrl"] = "acma-rrl"
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")

    @model_validator(mode="after")
    def _check(self) -> RegisterInstall:
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        return self


class KiwixBooksInstall(Strict):
    """The Kiwix books the operator chose in station config (D-066).

    Like `DemTilesInstall`, nothing is pinned in the manifest: which books
    follows ``reference_books`` in station config, and each book resolves at
    plan time to a pin in ``catalog/data/kiwix-pins.yaml``, generated from
    Kiwix's own ``.meta4`` files. There is no ``licence`` here because the
    books do not share one: each book's licence line is in the hand-written
    ``catalog/data/kiwix-books.yaml``, and the plan prints it beside the
    book's size before the confirmation (D-049 rule 2). `provider` is an
    enum, as `dem-tiles`' is, so another library is a new member the engine
    implements, never a URL in the catalog.
    """

    method: Literal["kiwix-books"] = "kiwix-books"
    provider: Literal["kiwix"] = "kiwix"


class MwmRegionsInstall(Strict):
    """CoMaps' own map files for the station's map regions (D-069).

    Like `DemTilesInstall`, nothing is pinned in the manifest: which maps
    follows the operator's regions in station config, through the region
    table in ``catalog/data/comaps-pins.yaml``, generated from CoMaps' map
    index at the commit the `comaps` unit pins. Each map is checked against
    that index's SHA-1 and exact size, the publisher's own check, and the plan
    says so. `provider` is an enum, so Organic Maps' CDN would be a new member
    the engine implements, never a URL in the catalog.
    """

    method: Literal["mwm-regions"] = "mwm-regions"
    provider: Literal["comaps"] = "comaps"
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")

    @model_validator(mode="after")
    def _check(self) -> MwmRegionsInstall:
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        return self


#: D-061: the install method a `derived` block's `source` unit must actually
#: resolve to, keyed by `converter`. A single-manifest validator cannot check
#: this -- it would need another manifest's own install block, which is why
#: `derived_source_method_problem` below checks it catalog-wide, called from
#: `load.load_catalog` the same way `_desktop_alternative_problem` is.
CONVERTER_SOURCE_METHOD: dict[str, str] = {
    "navit-maptool": "osm-regions",
    "mkgmap": "osm-regions",
    "routino-planetsplitter": "osm-regions",
    "gdal-dem": "dem-tiles",
    "brouter-mapcreator": "osm-regions",
    "mapsforge-map": "osm-regions",
    "mapsforge-poi": "osm-regions",
    "ustopo-mosaic": "topo-quads",
    "tilemaker-pmtiles": "osm-regions",
    "graphhopper-import": "osm-regions",
    "splat-sdf": "dem-tiles",
}

#: D-063: the other units a ``brouter-mapcreator`` block reads, the install
#: method each must resolve to, and whether it is required. Checked per
#: manifest (named in `depends`, refused on any other converter) and
#: catalog-wide (the method) the way `CONVERTER_SOURCE_METHOD` is.
BROUTER_INPUTS: dict[str, tuple[str, bool]] = {
    "program": ("binary", True),
    "profiles": ("data", True),
    "elevation": ("dem-tiles", False),
}

#: D-071: the unit a ``tilemaker-pmtiles`` block reads besides its regions: the
#: kit holding tilemaker's OpenMapTiles profile and the Natural Earth layers.
TILEMAKER_INPUTS: dict[str, tuple[str, bool]] = {"kit": ("data", True)}

#: D-076: the unit a ``graphhopper-import`` block reads besides its regions:
#: the ``binary`` unit whose installed tree holds GraphHopper's jar.
GRAPHHOPPER_INPUTS: dict[str, tuple[str, bool]] = {"program": ("binary", True)}

#: D-068 (amended 2026-10-01): ``gdal-dem`` may name the ``dem-tiles`` unit it
#: draws from instead of `source` when the station's ``dem_source`` selects
#: that unit's provider; ``ustopo-mosaic`` may name the Forest Service's
#: sheets, mosaicked beside US Topo as a second map. D-061 (amended
#: 2026-10-02): ``splat-sdf`` reads the same ``alternative`` the same way.
GDAL_DEM_INPUTS: dict[str, tuple[str, bool]] = {"alternative": ("dem-tiles", False)}
MOSAIC_INPUTS: dict[str, tuple[str, bool]] = {"fstopo": ("topo-quads", False)}

#: Every converter that reads units besides its `source`, and those inputs:
#: field -> (the install method it must resolve to, whether it is required).
#: A field is refused on every converter not listed against it.
CONVERTER_INPUTS: dict[str, dict[str, tuple[str, bool]]] = {
    "brouter-mapcreator": BROUTER_INPUTS,
    "tilemaker-pmtiles": TILEMAKER_INPUTS,
    "graphhopper-import": GRAPHHOPPER_INPUTS,
    "gdal-dem": GDAL_DEM_INPUTS,
    "ustopo-mosaic": MOSAIC_INPUTS,
    "splat-sdf": GDAL_DEM_INPUTS,
}

#: Each input field and the converters that read it: a field is refused on
#: every converter not among its owners.
INPUT_OWNERS: dict[str, frozenset[str]] = {
    name: frozenset(owner for owner, fields in CONVERTER_INPUTS.items() if name in fields)
    for fields in CONVERTER_INPUTS.values()
    for name in fields
}

#: The provider an input of these two must have, checked catalog-wide: an
#: `alternative` that were Copernicus again, or an `fstopo` that were US Topo
#: again, would draw the same data twice under another name.
INPUT_PROVIDER: dict[str, str] = {"alternative": "usgs-3dep", "fstopo": "usfs-fstopo"}

#: D-067: the converters that run a program no archive packages, carried as a
#: pinned `tool` on the derived block. Required for these, refused for the rest.
CONVERTERS_WITH_TOOL = frozenset({"mapsforge-poi"})

#: A tool's file name, from the last component of its URL: a bare name the
#: engine can install under the unit's own directory, never a path.
_TOOL_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ConverterTool(Strict):
    """A program a converter runs that no archive packages, pinned.  D-067.

    The catalog supplies where it is and what it hashes to; the engine owns
    how it is run, exactly as it owns every converter's command line. It is
    fetched, verified against `artifact.sha256`, checked against `size`, and
    installed under ``<prefix>/share/hammunition/<unit>/`` -- not under the
    unit's data directory, which holds only files that are read, never run
    (D-049). A `signature_url` is recorded and not verified; the fetch step
    says so, in the words every declared-but-unverified signature gets.
    """

    artifact: RemoteArtifact
    size: int = Field(gt=0, description="Bytes, measured; a download of another size is refused.")
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")

    @property
    def file_name(self) -> str:
        return self.artifact.url.rsplit("/", 1)[-1]

    @model_validator(mode="after")
    def _check(self) -> ConverterTool:
        if not self.artifact.url.startswith("https://"):
            raise ManifestError(f"a converter tool's url must be https: {self.artifact.url!r}")
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        if not _TOOL_FILE.fullmatch(self.file_name):
            raise ManifestError(
                f"a converter tool's url must end in a plain file name, got {self.artifact.url!r}"
            )
        return self


class DerivedDataInstall(Strict):
    """Data produced by running a converter over another catalog unit's data.

    `converter` names the transformation by enum, never a command line --
    the catalog stays pure data (CLAUDE.md's founding invariant) and the
    engine owns what each enum member means. `source` names the catalog
    package whose data this is derived from; the manifest's own validator
    requires it to also appear in `depends`, so the plan always installs the
    source data before running the converter over it. Which install method
    `source` must actually be is `CONVERTER_SOURCE_METHOD`, checked catalog-
    wide because only the catalog knows what `source` resolves to (D-061).
    """

    method: Literal["derived"] = "derived"
    converter: Literal[
        "navit-maptool",
        "mkgmap",
        "routino-planetsplitter",
        "gdal-dem",
        "brouter-mapcreator",
        "mapsforge-map",
        "mapsforge-poi",
        "ustopo-mosaic",
        "tilemaker-pmtiles",
        "graphhopper-import",
        "splat-sdf",
    ] = Field(
        description=(
            "The transformation to run. Each needs a `source` of one particular "
            "install method (`CONVERTER_SOURCE_METHOD`, checked catalog-wide, D-061): "
            "`navit-maptool`, `mkgmap`, `routino-planetsplitter`, `brouter-mapcreator`, "
            "`mapsforge-map`, `mapsforge-poi`, `tilemaker-pmtiles` and `graphhopper-import` "
            "need an `osm-regions` source; `gdal-dem` and `splat-sdf` (D-061, amended 2026-10-02) "
            "need a `dem-tiles` source; `ustopo-mosaic` needs a `topo-quads` source (D-068)."
        )
    )
    source: str = Field(
        description=(
            "The catalog package name this is derived from: an `osm-regions` unit, "
            "for `gdal-dem` and `splat-sdf` a `dem-tiles` unit, for `ustopo-mosaic` a "
            "`topo-quads` unit."
        )
    )
    boundaries: str | None = Field(
        default=None,
        description=(
            "The catalog data unit holding country boundaries (one GeoJSON file) "
            "that `navit-maptool` merges into each region before conversion, so "
            "maptool files towns under a country and address search finds them "
            "(D-057 amendment, 2026-09-28). Must also be in `depends`."
        ),
    )
    program: str | None = Field(
        default=None,
        description=(
            "`brouter-mapcreator` and `graphhopper-import` only, and required on "
            "both: the `binary` unit whose installed tree holds the jar the "
            "converter runs, BRouter's with its map creator (D-063) or GraphHopper's "
            "(D-076). Must also be in `depends`."
        ),
    )
    profiles: str | None = Field(
        default=None,
        description=(
            "`brouter-mapcreator` only, and required there (D-063): the `data` unit "
            "holding `all.brf` and `softaccess.brf`, the map creator's filters, which "
            "BRouter's release zip does not carry. Must also be in `depends`."
        ),
    )
    elevation: str | None = Field(
        default=None,
        description=(
            "`brouter-mapcreator` only, optional (D-063): the `dem-tiles` unit whose "
            "installed tiles are folded into the routing files as elevation. Without "
            "it the routes are flat. Must also be in `depends`."
        ),
    )
    alternative: str | None = Field(
        default=None,
        description=(
            "`gdal-dem` and `splat-sdf` only, optional (D-068, amended 2026-10-01; D-061, "
            "amended 2026-10-02): the `dem-tiles` unit of provider `usgs-3dep` drawn from "
            "instead of `source` when the station's `dem_source` is `3dep`. Must also be "
            "in `depends`."
        ),
    )
    fstopo: str | None = Field(
        default=None,
        description=(
            "`ustopo-mosaic` only, optional (D-068, amended 2026-10-01): the `topo-quads` "
            "unit of provider `usfs-fstopo` whose sheets, when installed, are made a second "
            "QMapShack map, `FSTopo.vrt`, beside `ustopo.vrt`. Read, never depended on: the "
            "Forest Service publishes no checksum, so its sheets are installed only by name "
            "(CLAUDE.md's checksum rule), and US Topo's map works without them."
        ),
    )
    kit: str | None = Field(
        default=None,
        description=(
            "`tilemaker-pmtiles` only, and required there (D-071): the `data` unit "
            "holding tilemaker's OpenMapTiles profile (config and Lua) and the Natural "
            "Earth shapefiles the profile names. Must also be in `depends`."
        ),
    )
    licence: str = Field(
        min_length=2,
        description="SPDX identifier where one exists, else the publisher's own words.",
    )
    licence_url: str = Field(description="Where the licence is stated, on the publisher's site.")
    tool: ConverterTool | None = Field(
        default=None,
        description=(
            "The pinned program the converter runs, for a converter in "
            "`CONVERTERS_WITH_TOOL` (`mapsforge-poi`: Maven Central's "
            "mapsforge-poi-writer, which no archive packages, D-067). Required for "
            "those converters and refused for every other."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> DerivedDataInstall:
        if not self.licence_url.startswith("https://"):
            raise ManifestError(f"licence_url must be https, got {self.licence_url!r}")
        if self.converter in CONVERTERS_WITH_TOOL and self.tool is None:
            raise ManifestError(
                f"converter {self.converter!r} runs a program no archive packages, so the "
                f"block must pin it as `tool` (url, sha256, size, licence)"
            )
        if self.converter not in CONVERTERS_WITH_TOOL and self.tool is not None:
            raise ManifestError(
                f"converter {self.converter!r} runs only what the archive installs; a `tool` "
                f"on it would be fetched for nothing"
            )
        for name, owners in INPUT_OWNERS.items():
            if self.converter not in owners and getattr(self, name) is not None:
                readers = " and ".join(sorted(owners))
                raise ManifestError(
                    f"{name} is read only by the {readers} converter"
                    f"{'s' if len(owners) > 1 else ''}, not {self.converter!r}"
                )
        for name, (_, required) in CONVERTER_INPUTS.get(self.converter, {}).items():
            if required and getattr(self, name) is None:
                raise ManifestError(f"converter {self.converter} needs {name}: the unit it reads")
        return self

    def inputs(self) -> tuple[str, ...]:
        """Every other unit this block needs at run time; each must be in
        `depends`. `fstopo` is not one: it is read when it is installed and
        never pulled in (D-068, amended 2026-10-01)."""
        return tuple(
            unit
            for unit in (
                self.source,
                self.boundaries,
                self.program,
                self.profiles,
                self.elevation,
                self.kit,
                self.alternative,
            )
            if unit is not None
        )


class PipxInstall(Strict):
    method: Literal["pipx"] = "pipx"
    spec: str
    system_site_packages: bool = False


InstallMethod = Annotated[
    AptInstall
    | SourceInstall
    | GitInstall
    | BinaryInstall
    | VenvInstall
    | NodeInstall
    | PipxInstall
    | DataInstall
    | RegionalDataInstall
    | DemTilesInstall
    | TopoQuadsInstall
    | RegisterInstall
    | DerivedDataInstall
    | KiwixBooksInstall
    | MwmRegionsInstall,
    Field(discriminator="method"),
]


# ---------------------------------------------------------------------------
# Outputs: binaries, launchers, service endpoints.  `Binary` is defined here,
# ahead of `InstallBlock`, because a block may override the manifest's list.
# ---------------------------------------------------------------------------


class Binary(Strict):
    """Explicit build-output -> installed-name mapping.

    This is what dissolves the wsjtx / wsjtx_improved rename dance: both builds
    emit `wsjtx`, so AHRL renames around them. Declaring `install_as` makes the
    collision impossible instead of choreographed.
    """

    produced: str = Field(
        description=(
            "Path the build emits, relative to the directory it builds in: "
            "cmake's out-of-tree build directory, the source tree for the rest."
        )
    )
    """With `provides_install_target` this is documentation and the install
    rule decides the name; `install_as` must then match what the rule installs
    (AIS-catcher's rule installs `AIS-catcher`, whatever the manifest says),
    because the post-run effect check looks for `<prefix>/bin/<install_as>`."""
    install_as: str = Field(description="Final name in the install prefix.")


class InstallBlock(Strict):
    """One (selector -> method) pair. The method itself varies, not just its
    argument — js8call is apt on Linux Mint 22.3 and a cmake build elsewhere."""

    when: Selector = Field(default_factory=Selector)
    install: InstallMethod
    build_depends: list[str] = Field(
        default_factory=list,
        description="apt packages needed to BUILD only. Never reported as installed.",
    )
    binaries: list[Binary] | None = Field(
        default=None,
        description=(
            "This block's own build outputs, replacing the manifest's "
            "`binaries` wherever this block is the one that resolves. A "
            "prebuilt archive selected by `arch` can carry a different path "
            "per architecture -- rayhunter's zip has `installer` at the top "
            "level and one `rayhunter-check` under a per-platform directory -- "
            "and one manifest-level list cannot describe both. Omit the key to "
            "use the manifest's list; an empty list is refused, because it "
            "reads as an override to nothing."
        ),
    )
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> InstallBlock:
        _check_package_names(self.build_depends, "build_depends")
        self._check_binaries()
        return self

    def _check_binaries(self) -> None:
        """A block's `binaries` is only meaningful where the engine copies files.

        Source, git and non-deb binary blocks install each declared binary into
        the prefix by name. apt and a vendor `.deb` place their own contents,
        and a venv, node or data block installs something that is not a build
        output at all -- a list there would be read by nothing, which is the
        silent-no-op shape D-031 exists to refuse.
        """
        if self.binaries is None:
            return
        where = self._describe()
        if not self.binaries:
            raise ManifestError(
                f"{where}: `binaries` is empty. An empty list overrides the manifest's "
                f"`binaries` with nothing, so the block would install nothing while "
                f"reporting success; omit the key to use the manifest's list."
            )
        method = self.install
        installs_binaries = isinstance(method, SourceInstall | GitInstall) or (
            isinstance(method, BinaryInstall) and method.format != "deb"
        )
        if not installs_binaries:
            raise ManifestError(
                f"{where}: `binaries` names build outputs the engine copies into the "
                f"prefix, and a {method.method} block copies none -- apt places the "
                f"package's own files. Drop the list."
            )
        names = [b.install_as for b in self.binaries]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ManifestError(f"{where}: duplicate install_as: {sorted(dupes)}")

    def _describe(self) -> str:
        """This block, named the way a manifest author sees it in the YAML."""
        parts = [f"method={self.install.method}"]
        if self.when.distro:
            parts.append(f"distro={','.join(self.when.distro)}")
        if self.when.distro_version:
            parts.append(f"distro_version={','.join(self.when.distro_version)}")
        if self.when.arch:
            parts.append(f"arch={','.join(a.value for a in self.when.arch)}")
        return f"install block ({', '.join(parts)})"


class ServiceEndpoint(Strict):
    """A remote service the software talks to.

    Exists because AHRL hardcodes `-b hamclock.com:80` into four generated
    launchers, and that host was reported to stop serving in June 2026. A dead
    upstream must be repointable by editing the catalog, not the launchers.
    """

    name: str
    default_url: str
    description: str
    user_configurable: bool = True
    note: str | None = None


class Launcher(Strict):
    """A generated wrapper script. 14 AHRL units need one."""

    name: str = Field(
        description=(
            "The wrapper's filename in ~/.local/bin, so the launcher is also what a "
            "shell finds by that name -- ahead of /usr/bin. It therefore never takes "
            "the name of the command it runs, of a binary the manifest installs, or "
            "of anything on the system PATH or in the unit's apt file list: name it "
            "for what it does (rigctl-dummy, hackrf_info-check; issue #174)."
        ),
    )
    exec: str = Field(
        description=(
            "Command template. May reference {endpoint:NAME}. A line starting with "
            "`hammunition` runs the engine, written into the wrapper as the absolute "
            "path of the hammunition that generated it (issue #145)."
        ),
    )
    title: str | None = Field(
        default=None,
        description=(
            "What the desktop menu shows for this launcher. Defaults to `name`, "
            "which a file name rarely says well (`rigctl-dummy`). The convention "
            "is what it does, then the command in parentheses (D-054)."
        ),
    )
    working_directory: str | None = None
    terminal: bool = False

    @property
    def display_name(self) -> str:
        return self.title if self.title is not None else self.name

    @model_validator(mode="after")
    def _title_is_not_blank(self) -> Launcher:
        if self.title is not None and not self.title.strip():
            raise ManifestError(
                f"launcher {self.name!r} has a blank title; omit `title` to show the "
                f"name, or give the entry a title someone can read"
            )
        return self

    @model_validator(mode="after")
    def _terminal_launchers_keep_their_shell(self) -> Launcher:
        # The wrapper holds a terminal window open after the command exits;
        # `exec` would replace the shell and the hold would never run.
        if self.terminal and self.exec.lstrip().startswith("exec "):
            raise ManifestError(
                f"launcher {self.name!r} is a terminal launcher and starts with `exec`; "
                f"the wrapper must outlive the command to hold the window, so drop the "
                f"`exec`"
            )
        return self


# ---------------------------------------------------------------------------
# System modifications and config.  D-012, D-016.
# ---------------------------------------------------------------------------

LinuxCapability = Literal["CAP_NET_ADMIN", "CAP_NET_RAW", "CAP_NET_BIND_SERVICE"]


class SystemModification(Strict):
    kind: Literal[
        "udev_rule",
        "modprobe_blacklist",
        "group_create",
        "group_membership",
        "foreign_arch",
        "package_purge",
        "apt_pin",
        "file_shadow",
        "file_capability",
        "package_udev_rule",
        "package_service",
        "package_account",
    ]
    description: str
    detail: str
    reversible: bool
    reverse_hint: str | None = None
    group: str | None = Field(
        default=None,
        description=(
            "For `group_membership`: the group to add the operator to. Required "
            "there, and forbidden elsewhere."
        ),
    )
    binary: str | None = Field(
        default=None,
        description="For `file_capability`: an installed binary's `install_as` name.",
    )
    capabilities: list[LinuxCapability] = Field(
        default_factory=list,
        description=(
            "For `file_capability`: the Linux capabilities set with permitted and "
            "effective flags, applied only after typed consent; `--yes` cannot satisfy it."
        ),
    )

    @model_validator(mode="after")
    def _group_is_named(self) -> SystemModification:
        """A group membership names its group in a field, never in prose.

        This field exists because the engine needed the name and the only place
        it appeared was inside `detail`, where both manifests happened to write
        it in backticks. Scraping it back out worked on both, which is exactly
        what makes that kind of parser dangerous: adding an operator to the
        wrong group is a privilege change that does not announce itself, and
        the prose is free text that no test constrains.
        """
        if self.kind == "group_membership":
            if not self.group:
                raise ManifestError(
                    "a group_membership modification must name its group in `group`; "
                    "the engine adds the operator to it and will not infer the name "
                    "from the prose in `detail`"
                )
            if not SLUG.match(self.group):
                raise ManifestError(f"group must be a lowercase name: {self.group!r}")
        elif self.group is not None:
            raise ManifestError(
                f"`group` is only meaningful for group_membership, not {self.kind!r}"
            )
        if self.kind == "file_capability":
            if not self.binary or not _TOOL_FILE.fullmatch(self.binary):
                raise ManifestError(
                    "a file_capability modification must name an installed binary in `binary`"
                )
            if not self.capabilities:
                raise ManifestError(
                    "a file_capability modification must name at least one capability"
                )
            if len(set(self.capabilities)) != len(self.capabilities):
                raise ManifestError("a file_capability modification must not repeat capabilities")
            if not self.reversible:
                raise ManifestError("a file_capability modification must be reversible")
        elif self.binary is not None or self.capabilities:
            raise ManifestError(
                f"`binary` and `capabilities` are only meaningful for file_capability, "
                f"not {self.kind!r}"
            )
        return self

    @model_validator(mode="after")
    def _irreversible_explained(self) -> SystemModification:
        if not self.reversible and not self.reverse_hint:
            raise ManifestError(
                f"irreversible modification {self.kind!r} must explain why in reverse_hint"
            )
        return self


class ConfigFile(Strict):
    """Templated configuration written on the operator's behalf.

    AX.25 forces this into 1.0: its install appends
    `wl2k ${MYCALL} 1200 255 7 Winlink` to /etc/ax25/axports.
    """

    path: str
    template: str = Field(description="May reference {station.callsign} etc.")
    mode: str = "0644"
    append: bool = False
    backup_existing: bool = True
    skip_if_present: list[str] = Field(
        default_factory=list,
        description=(
            "Append only: regular expressions, matched per line against the file as it "
            "is when the step runs. If any line matches any of them the append is "
            "skipped and the outcome says which -- the idempotence and the "
            "no-duplicate rule of a file like axports, where a second port with the "
            "same name or callsign is an error. May reference {station.*}; a value is "
            "matched literally (escaped), never as a pattern."
        ),
    )

    @property
    def station_variables(self) -> set[str]:
        found = set(STATION_REF.findall(self.template))
        for pattern in self.skip_if_present:
            found |= set(STATION_REF.findall(pattern))
        return found

    @property
    def in_home(self) -> bool:
        """``~/``: a file in the operator's home, resolved at plan time."""
        return self.path.startswith("~/")

    @model_validator(mode="after")
    def _path_is_absolute_or_home(self) -> ConfigFile:
        # `~/` is the operator's home -- the owner-aware one `paths` resolves,
        # never $HOME, which is /root under sudo. Nothing else is expanded.
        rest = self.path[2:] if self.in_home else self.path
        if not (self.in_home or self.path.startswith("/")):
            raise ManifestError(
                f"config path {self.path!r} must be absolute or start with ~/ (the operator's home)"
            )
        parts = rest.split("/")
        if not rest.strip("/") or rest.endswith("/") or ".." in parts or "~" in rest:
            raise ManifestError(f"config path {self.path!r} must name a file, with no '..' or '~'")
        return self

    @model_validator(mode="after")
    def _mode_is_a_plain_permission(self) -> ConfigFile:
        # The mode reaches os.chmod and `install -m` as written, so a manifest
        # (a community or local one included) must not be able to ask for a
        # setuid, setgid or sticky bit or a world-writable file.
        if not re.fullmatch(r"0?[0-7]{3}", self.mode) or int(self.mode, 8) & 0o7002:
            raise ManifestError(
                f"{self.path}: mode {self.mode!r} must be three octal digits with no "
                f"setuid, setgid or sticky bit and no world write (for example 0644)"
            )
        return self

    @model_validator(mode="after")
    def _skip_only_when_appending(self) -> ConfigFile:
        if self.skip_if_present and not self.append:
            raise ManifestError(
                f"{self.path}: skip_if_present only means something for an append; a "
                f"whole-file write replaces the file (with a backup) regardless"
            )
        for pattern in self.skip_if_present:
            try:
                re.compile(STATION_REF.sub("X", pattern))
            except re.error as exc:
                raise ManifestError(
                    f"{self.path}: skip_if_present {pattern!r} is not a regular expression: {exc}"
                ) from exc
        return self


#: What may not appear in a `user_services` exec element after station
#: substitution: shell metacharacters and any whitespace. A unit file's
#: `ExecStart=` is split on whitespace by systemd, so an element carrying a
#: space would silently become two arguments; a `;`, `|`, `&`, `$`, backtick,
#: `%`, `<` or `>` would be an injection point if the file were ever run
#: through a shell. The block forbids them at load and the plan re-checks after
#: substitution (D-073 §6a).
_EXEC_FORBIDDEN = set(" \t\n\r;|&$`%<>")


class UserServiceListen(Strict):
    """One address a user service binds. Loopback only, refused otherwise."""

    protocol: Literal["tcp"] = "tcp"
    address: str
    port: int = Field(ge=1, le=65535)

    @model_validator(mode="after")
    def _loopback(self) -> UserServiceListen:
        if self.address not in ("127.0.0.1", "::1"):
            raise ManifestError(
                f"user service listens on {self.address}:{self.port}; a rig control "
                f"port must bind loopback (127.0.0.1 or ::1), never a routable address, "
                f"because rigctld has no password and anyone it is reachable by can key "
                f"the transmitter (D-073 §11)"
            )
        return self


class UserService(Strict):
    """A systemd *user* service the engine renders from station values.  D-073 §6.

    Not a ``config_files`` path into ``~/.config/systemd/user/``: something must
    also tell systemd to read it, enable it, and stop it on uninstall, and the
    catalog would then carry free-form unit text — the place a command line
    quietly grows a ``sh -c``. Not a ``system_modifications`` kind either: those
    are descriptions the engine does not render, and a user service lives in one
    operator's home and needs no root. A block of its own keeps the D-035
    deferral, the ``~/`` writer and the plan's disclosure, and adds enable,
    start, stop and disable.

    ``when_station``/``unless_station`` select the entry by station value, so two
    entries can share a ``name`` as long as their conditions cannot both hold —
    the CAT service and the PTT-only service are two complete services chosen by
    ``rig_kind``, never one file written two ways (ruling 4).
    """

    name: str
    description: str
    when_station: dict[str, str] = Field(default_factory=dict)
    unless_station: dict[str, str] = Field(default_factory=dict)
    exec: list[str] = Field(min_length=1)
    binds_to_device: str | None = None
    listens: list[UserServiceListen] = Field(default_factory=list)
    restart: Literal["on-failure", "always", "no"] = Field(
        default="on-failure",
        description="systemd's Restart=, from a fixed set; never free text in a unit file.",
    )
    restart_sec: int = Field(
        default=5, ge=1, le=300, description="RestartSec=, in seconds (1 to 300)."
    )
    restart_prevent_exit_status: list[Annotated[int, Field(ge=1, le=255)]] = Field(
        default_factory=list,
        description=(
            "Exit codes systemd must not restart after (RestartPreventExitStatus=): "
            "a program that refuses by exiting 1, such as the tether on a taken "
            "port, is not retried forever."
        ),
    )

    @property
    def is_plain(self) -> bool:
        """True when the service needs nothing from the station or the hardware
        catalog: no ``when_station``, no ``unless_station``, no ``{station.*}``
        and no ``binds_to_device``. A plain service is always planned (D-073,
        amended 2026-10-02: the GPS tether)."""
        return not (
            self.when_station
            or self.unless_station
            or self.binds_to_device
            or self.station_variables
        )

    @property
    def station_variables(self) -> set[str]:
        """Every ``{station.*}`` name referenced in ``exec`` or ``binds_to_device``."""
        found: set[str] = set()
        for word in self.exec:
            found |= set(STATION_REF.findall(word))
        if self.binds_to_device:
            found |= set(STATION_REF.findall(self.binds_to_device))
        return found

    @model_validator(mode="after")
    def _check(self) -> UserService:
        if not SLUG.match(self.name):
            raise ManifestError(
                f"user service name {self.name!r} must be a lowercase unit-file base name"
            )
        # exec[0] is an absolute path, the engine placeholder {python} (the
        # interpreter that rendered the unit, as a launcher embeds the engine
        # path, #145), or a file inside the unit's own virtualenv
        # ({venv}/bin/rnsd). Nothing else: a bare name resolves against
        # systemd's own PATH, not ours.
        if (
            self.exec[0] != "{python}"
            and not self.exec[0].startswith("/")
            and not self.exec[0].startswith("{venv}/")
        ):
            raise ManifestError(
                f"user service {self.name!r}: exec[0] {self.exec[0]!r} must be an absolute path, "
                f"{{python}} or a file under {{venv}}/"
            )
        for word in self.exec:
            # A {station.*} reference stands in for a value re-checked after
            # substitution (D-073 §4, §6a); strip it before the word check so a
            # legitimate reference is not mistaken for a metacharacter. {python}
            # and {venv} are likewise engine placeholders, not shell tokens.
            bare = STATION_REF.sub("X", word).replace("{python}", "X").replace("{venv}", "X")
            if any(c in bare for c in _EXEC_FORBIDDEN):
                raise ManifestError(
                    f"user service {self.name!r}: exec element {word!r} is not one argv word; "
                    f"no whitespace and no shell metacharacter reaches the unit file (D-073 §6a)"
                )
        # The loopback guarantee is not declarative-only (final review I4): a
        # hamlib rigctld service must actually bind the address and port it
        # declares, or a dropped `-T` would bind every interface while the plan
        # still printed "listens 127.0.0.1:…".
        if self.exec[0].endswith("rigctld") and self.listens:
            want = self.listens[0]
            joined = " ".join(self.exec)
            if f"-T {want.address}" not in joined or f"-t {want.port}" not in joined:
                raise ManifestError(
                    f"user service {self.name!r} runs rigctld and declares it listens on "
                    f"{want.address}:{want.port}, but its exec does not pass "
                    f"-T {want.address} -t {want.port} (D-073 §11, review I4)"
                )
        return self


def _user_services_names_are_exclusive(services: list[UserService]) -> None:
    """Two entries sharing a ``name`` must have conditions that cannot both hold.

    The only disjointness the catalog can prove without the station is a shared
    key in ``when_station`` with different required values — ``rig_kind: cat``
    against ``rig_kind: ptt_only``. Same name, same ``when_station``: refused,
    because both would select at once and the engine would render one file
    twice (ruling 4, D-073 §6a).
    """
    by_name: dict[str, list[UserService]] = {}
    for svc in services:
        by_name.setdefault(svc.name, []).append(svc)
    for name, group in by_name.items():
        for i, first in enumerate(group):
            for second in group[i + 1 :]:
                shared = set(first.when_station) & set(second.when_station)
                if not any(first.when_station[k] != second.when_station[k] for k in shared):
                    raise ManifestError(
                        f"two user services named {name!r} have conditions that can both "
                        f"hold; give them when_station values that disagree (e.g. "
                        f"rig_kind: cat vs rig_kind: ptt_only), never one file written "
                        f"two ways (D-073 §6a, ruling 4)"
                    )


_REPO_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FINGERPRINT = re.compile(r"^(?:[0-9A-F]{40}|[0-9A-F]{64})$")


class AptRepo(Strict):
    """Third-party apt source. Key pinning is mandatory.  D-040.

    ``name`` becomes two file names under ``/etc/apt`` and is restricted to
    what one can safely be. ``key_fingerprint`` is the *primary* key's,
    forty hex digits for a v4 key or sixty-four for a v6 one, spaces
    permitted; the engine computes the same thing from the file it fetches
    and refuses anything else. ``key_url`` is https: the fingerprint check
    is what makes the key trustworthy, but the transport still decides who
    can *see* the request.

    ``when`` narrows the repository to some targets, with the same selector
    an install block uses; unset, it applies everywhere. It exists because a
    publisher may serve one tree per release under a different URI -- Kismet
    serves ``.../release/trixie`` and ``.../release/noble``, each its own
    ``Release`` file -- and a manifest that declared both unconditionally
    would add a noble repository to a Debian 13 machine. A target no
    repository applies to gets no repository, and the unit falls to the
    ordinary "the archive does not offer it" path (D-039), deferred by name.

    A **flat** repository has no ``dists/`` tree: its ``Release`` and
    ``Packages`` sit directly under ``uri``. The openSUSE Build Service
    publishes every repository that way (``meshtasticd``, D-040 amendment of
    2026-10-05). It is declared as ``suites: ["./"]`` with ``components: []``,
    which is how sources.list(5) writes one: a suite ending in ``/`` and no
    ``Components:`` line. A mix of the two shapes in one entry is refused.
    """

    name: str
    uri: str
    suites: list[str]
    components: list[str]
    key_url: str
    key_fingerprint: str
    rationale: str = Field(description="Shown to the user before the repo is added.")
    when: Selector = Field(
        default_factory=Selector,
        description="The targets this repository applies to; unset means every target.",
    )

    @model_validator(mode="after")
    def _well_formed(self) -> AptRepo:
        if not _REPO_NAME.match(self.name):
            raise ManifestError(
                f"apt repo name {self.name!r} must be lower-case letters, digits, "
                f"'.', '_' or '-', starting with a letter or digit: it names files "
                f"under /etc/apt"
            )
        digits = "".join(self.key_fingerprint.split()).upper()
        if not _FINGERPRINT.match(digits):
            raise ManifestError(
                f"apt repo {self.name!r}: key_fingerprint must be 40 (v4) or 64 (v6) "
                f"hex digits, got {self.key_fingerprint!r}"
            )
        for label, url in (("uri", self.uri), ("key_url", self.key_url)):
            if not url.startswith("https://"):
                raise ManifestError(f"apt repo {self.name!r}: {label} must be https, got {url!r}")
        if not self.suites:
            raise ManifestError(f"apt repo {self.name!r}: suites and components are required")
        if not self.components and not all(suite.endswith("/") for suite in self.suites):
            raise ManifestError(
                f"apt repo {self.name!r}: suites and components are required "
                f"(components may be empty only for a flat repository, whose every "
                f"suite ends in '/', sources.list(5))"
            )
        if self.components and any(suite.endswith("/") for suite in self.suites):
            raise ManifestError(
                f"apt repo {self.name!r}: a flat repository (a suite ending in '/') "
                f"takes no components"
            )
        return self


# ---------------------------------------------------------------------------
# Status, updates, toolkit risk.
# ---------------------------------------------------------------------------


class Status(StrEnum):
    supported = "supported"
    broken = "broken"
    retired = "retired"
    unverifiable = "unverifiable"


class VerdictSource(StrEnum):
    tested = "tested"
    inherited = "inherited"


class RetireReason(StrEnum):
    world_changed = "world_changed"
    never_worked = "never_worked"
    out_of_scope = "out_of_scope"


class UpdateProbe(Strict):
    """How to learn the upstream version.  D-010.

    ``label_file`` is the one probe an upstream told us about directly: YAAC
    publishes no tags and no releases, but its own Help > Check for Updates
    fetches a one-line text file and compares it to the compiled-in build
    label (issue #31, from the author). The catalog records that file as data
    so an engine can make the same comparison; one measured user, like ``pypi``.
    """

    method: Literal[
        "apt_policy",
        "github_release",
        "github_tags",
        "binary_version",
        "label_file",
        "pypi",
        "kiwix",
        "comaps_maps",
        "none",
    ]
    repo: str | None = None
    command: str | None = None
    pattern: str | None = None
    url: str | None = Field(
        default=None,
        description=(
            "For `label_file`: the plain-text file whose content is upstream's "
            "current version label, compared verbatim against `version`."
        ),
    )
    package: str | None = Field(
        default=None,
        description=(
            "For `pypi`: the PyPI project name when it is not the unit's name. "
            "`update --upstream` asks pypi.org for it."
        ),
    )

    @model_validator(mode="after")
    def _label_file_has_a_url(self) -> UpdateProbe:
        if self.method == "label_file" and not self.url:
            raise ManifestError(
                "a label_file probe names no url -- there is nothing to fetch and compare"
            )
        if self.method != "label_file" and self.url is not None:
            raise ManifestError(
                f"probe url {self.url!r} is set on a {self.method!r} probe; "
                "only a label_file probe reads a url"
            )
        if self.method != "pypi" and self.package is not None:
            raise ManifestError(
                f"probe package {self.package!r} is set on a {self.method!r} probe; "
                "only a pypi probe names a project"
            )
        return self


class UpdateBlock(Strict):
    probe: UpdateProbe
    strategy: Literal["reinstall", "apt_upgrade", "rebuild", "manual"] = "reinstall"
    cadence_hint: str | None = None


class ToolkitRisk(Strict):
    """Standing exposure register.  D-015.

    The component list is derivable from build_depends; upstream port status and
    the date it was checked are not, which is the whole reason this exists.
    """

    framework: Literal["qt5", "qt6", "gtk2", "gtk3", "gtk4", "wx3.0", "wx3.2", "mono"]
    upstream_port_status: Literal["ported", "in_progress", "no_path", "unknown"]
    checked: date
    note: str | None = None


class Documentation(Strict):
    """Required by CLAUDE.md. A manifest without these cannot ship."""

    what_it_does: str = Field(min_length=20)
    why_you_want_it: str = Field(min_length=20)
    prerequisites: str | None = None
    known_problems: str | None = None
    upstream_url: str
    upstream_support: str | None = None


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


class PackageManifest(Strict):
    name: str
    version: str
    summary: str
    categories: list[str] = Field(min_length=1, description="Flat tags, D-003.")
    licence: str = Field(
        default="licence not recorded in this manifest",
        min_length=1,
        description=(
            "The terms the installed software itself is under -- never inferred from this "
            "YAML file's own CC0-1.0 header, which licenses the catalog entry describing the "
            "software, not the software (Task 16, D-070). Read by `hammunition artifacts` as "
            "the licence line for a `source`, `binary`, `node` or `git` payload and for a "
            "`derived` block's converter tool, and by `hammunition.artifacts.list_git_pins` "
            "for the git pin it lists, where the install block itself carries none. A missing "
            "value stays the explicit default above rather than a guess."
        ),
    )

    install: list[InstallBlock] = Field(min_length=1)

    depends: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    conflicts_with_repo_package: list[str] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list, description="Ordering, not dependency.")
    requires_kernel: list[KernelFeature] = Field(
        default_factory=list,
        description=(
            "Kernel subsystems the software cannot work without, checked against "
            "the running kernel at plan time. A machine whose kernel lacks one "
            "defers the unit in a profile and refuses it by name. Linux 7.1 "
            "removed AX.25 (merge 64edfa65, 2026-04-24); `hammunition.kernel` "
            "reads the module tree. The vocabulary is what has been measured."
        ),
    )

    requires_java: int | None = Field(
        default=None,
        gt=0,
        strict=True,
        description=(
            "The lowest Java major version the software runs on, checked at plan "
            "time with `java -version` (D-037, amended 2026-10-02). "
            "`default-jre-headless` is a metapackage whose version says nothing "
            "about the Java major, so the floor is read from the `java` on this "
            "machine, never from the archive. A machine below it (or with no "
            "java, when this unit's `depends` would not install one that meets "
            "it) defers the unit in a profile and refuses it by name; nothing "
            "is fetched to meet it. Measured from the upstream's build file or "
            "its jar's class-file major, never copied from a README."
        ),
    )

    desktops: list[Desktop] | None = Field(
        default=None,
        description=(
            "The desktops this unit is for, when it is for some and not others: "
            "`kde`, `gnome`, `xfce`, `lxqt`, `lxde`, `mate`, `cinnamon`. Omitted "
            "means any desktop, which is every unit that is not a panel applet or "
            "the like. Decided at plan time against the session files under "
            "/usr/share/xsessions and /usr/share/wayland-sessions (and the same "
            "under /usr/local/share), never "
            "XDG_CURRENT_DESKTOP (sudo drops it): a machine with no session for "
            "any listed desktop defers the unit in a profile and refuses it by "
            "name (D-060). The case it exists for is `hammunition-tray`, a Plasma "
            "applet whose .deb pulls plasma-workspace onto an Xfce machine."
        ),
    )
    desktop_alternative: str | None = Field(
        default=None,
        description=(
            "The unit that does this job on the desktops this one is not for, "
            "named in the refusal and the deferral so the operator is told what "
            "to install instead. It must exist in the catalog, and its "
            "`desktops` must share none with this unit's (checked when the "
            "catalog loads). Requires `desktops`."
        ),
    )

    menu_title: str | None = Field(
        default=None,
        description=(
            "What the desktop menu shows for the entry the engine generates when "
            "this unit ships none of its own: what it does, then the command in "
            "parentheses (`Contest logger (tlf)`). Defaults to the unit's name, "
            "which is fine for a name people know (gqrx) and not for one they do "
            "not (wwl). Ignored for a unit that ships its own desktop entries."
        ),
    )
    menu_submenu: str | None = Field(
        default=None,
        description=(
            "Gather every desktop entry this unit ships into one submenu of this "
            "title, under the unit's first category only, instead of listing them "
            "beside everything else in every category it carries. For a unit that "
            "ships a toolkit: GNU Radio puts 21 entries into a submenu, and the "
            "SDR and Digital Modes menus were unreadable with them inline."
        ),
    )
    binaries: list[Binary] = Field(default_factory=list)
    installed_files: list[str] = Field(
        default_factory=list,
        description=(
            "Files the build's own install rule puts under the prefix, as paths "
            "relative to it (`lib/libacars-2.so.2`, `lib/pkgconfig/libacars-2.pc`). "
            "Declared effects only: they are checked after the run (D-031) and "
            "are what lets a build with no executable be decided as already "
            "installed (D-051) or compared by `update`; the engine never copies "
            "or removes them. An executable belongs in `binaries`, not here."
        ),
    )
    launchers: list[Launcher] = Field(default_factory=list)
    service_endpoints: list[ServiceEndpoint] = Field(default_factory=list)

    apt_repos: list[AptRepo] = Field(default_factory=list)
    system_modifications: list[SystemModification] = Field(default_factory=list)
    config_files: list[ConfigFile] = Field(default_factory=list)
    user_services: list[UserService] = Field(
        default_factory=list,
        description=(
            "systemd user services the engine renders from station values, "
            "enables, and reverses on uninstall (D-073 §6). Rendered into the "
            "operator's ~/.config/systemd/user/; deferred when a station value "
            "is missing, like config_files."
        ),
    )
    debconf_selections: list[str] = Field(
        default_factory=list,
        description=(
            "debconf preseed lines applied BEFORE the apt install, so a "
            "package's postinst reads them instead of taking a default that "
            "needs an interactive answer. Each line is "
            "'<package> <question> <type> <value>', the debconf-set-selections "
            "format. The one measured need: wireshark, whose non-root capture "
            "is off by default and whose group and dumpcap capabilities are "
            "only created when wireshark-common/install-setuid is preseeded "
            "true (measured on Debian 13, 2026-09-01)."
        ),
    )
    reconfigure_after: list[str] = Field(
        default_factory=list,
        description=(
            "Packages to `dpkg-reconfigure` non-interactively AFTER the apt "
            "install. Paired with `debconf_selections` for the case where a "
            "postinst action depends on another package in the same "
            "transaction: wireshark-common's setcap of dumpcap needs "
            "libcap2-bin, and apt does not guarantee it is configured first, so "
            "the reconfigure re-runs the action once the whole transaction is "
            "settled (measured on Debian 13, 2026-09-01)."
        ),
    )

    scope: Literal["system", "user"] = "system"
    status: Status = Status.supported
    status_reason: str | None = None
    status_date: date | None = None
    status_verdict: VerdictSource | None = None
    retire_reason: RetireReason | None = None
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    recommended_default: bool = True

    toolkit_risk: list[ToolkitRisk] = Field(default_factory=list)
    update: UpdateBlock
    documentation: Documentation

    # -- validators ---------------------------------------------------------

    @model_validator(mode="after")
    def _menu_fields_are_readable(self) -> PackageManifest:
        for field in ("menu_title", "menu_submenu"):
            value = getattr(self, field)
            if value is not None and not value.strip():
                raise ManifestError(f"{field} is blank; omit it or give the menu something to show")
        return self

    @model_validator(mode="after")
    def _user_services_names_exclusive(self) -> PackageManifest:
        if self.user_services:
            _user_services_names_are_exclusive(self.user_services)
        return self

    @model_validator(mode="after")
    def _user_service_venv_needs_a_venv(self) -> PackageManifest:
        """``{venv}`` in a service's exec is the unit's own virtualenv; a manifest
        with no venv block has none to name, and the service would start a path
        that does not exist."""
        uses = [svc.name for svc in self.user_services if any("{venv}" in w for w in svc.exec)]
        if uses and not any(isinstance(b.install, VenvInstall) for b in self.install):
            raise ManifestError(
                f"{self.name}: user service {uses[0]!r} runs a file under {{venv}}/ but no "
                f"install block of this manifest is a venv, so there is no virtualenv to name"
            )
        return self

    @model_validator(mode="after")
    def _desktops_are_a_real_list(self) -> PackageManifest:
        """D-060. An empty list would be a unit for no desktop -- never
        installable, and silently so -- and a duplicate is a typo."""
        if self.desktops is not None:
            if not self.desktops:
                raise ManifestError(
                    f"{self.name}: desktops is empty; omit it for a unit that works on any desktop"
                )
            dupes = sorted({d.value for d in self.desktops if self.desktops.count(d) > 1})
            if dupes:
                raise ManifestError(f"{self.name}: desktops lists {', '.join(dupes)} twice")
        if self.desktop_alternative is not None:
            if self.desktops is None:
                raise ManifestError(
                    f"{self.name}: desktop_alternative needs desktops; a unit for every "
                    f"desktop has no desktop for an alternative to serve"
                )
            if self.desktop_alternative == self.name:
                raise ManifestError(f"{self.name}: desktop_alternative names the unit itself")
        return self

    @model_validator(mode="after")
    def _installed_files_are_relative_effects(self) -> PackageManifest:
        for declared in self.installed_files:
            path = PurePosixPath(declared)
            if path.is_absolute() or ".." in path.parts or not declared.strip():
                raise ManifestError(
                    f"installed_files entry {declared!r} must be a relative path under "
                    f"the prefix with no `..`"
                )
            if path.parts[0] == "bin":
                raise ManifestError(
                    f"installed_files entry {declared!r} is under bin/; an executable is "
                    f"declared in `binaries`, which checks it the same way"
                )
        if self.installed_files and not any(
            isinstance(block.install, SourceInstall | GitInstall) for block in self.install
        ):
            raise ManifestError(
                "installed_files declared but no source or git block installs anything "
                "by its own rule; the field names what `make install` (or equivalent) "
                "leaves under the prefix"
            )
        return self

    @model_validator(mode="after")
    def _name_is_slug(self) -> PackageManifest:
        if not SLUG.match(self.name):
            raise ManifestError(f"name must be a lowercase slug: {self.name!r}")
        return self

    @model_validator(mode="after")
    def _debconf_selections_are_well_formed(self) -> PackageManifest:
        """Each line is '<package> <question> <type> <value>' — four fields.

        Validated because the whole line becomes stdin to a root
        debconf-set-selections; a malformed line silently sets nothing, which
        is the quiet failure this project keeps writing checks for.
        """
        for line in self.debconf_selections:
            if len(line.split()) < 4:
                raise ManifestError(
                    f"debconf selection {line!r} is malformed: expected "
                    f"'<package> <question> <type> <value>'."
                )
        return self

    @model_validator(mode="after")
    def _dependency_names_are_package_names(self) -> PackageManifest:
        """`depends` reaches apt, so it is held to apt's naming rules.

        The field spans two namespaces — a catalog package or a distro one, and
        `plan._pull_catalog_dependencies` resolves which. Both are checked here
        because every catalog name is already a valid Debian package name, and
        the entries that are *not* catalog names go to `apt-cache policy` as
        argv. `conflicts_with_repo_package` names distro packages by
        definition; `provides` names what a build emits, which a later backend
        will resolve against apt the same way.
        """
        _check_package_names(self.depends, f"{self.name}: depends")
        _check_package_names(
            self.conflicts_with_repo_package, f"{self.name}: conflicts_with_repo_package"
        )
        return self

    @model_validator(mode="after")
    def _derived_source_is_in_depends(self) -> PackageManifest:
        """A `derived` block reads another unit's data at run time (D-049-adjacent);
        `depends` is what makes the plan install that unit first.

        This only checks the *name* is listed; it cannot check what `source`
        actually *is*, because a single-manifest validator has no other
        manifest to look at. `derived_source_method_problem`, right after this
        class, is the catalog-wide companion that checks the method (D-061).
        """
        for entry in self.install:
            block = entry.install
            if not isinstance(block, DerivedDataInstall):
                continue
            for unit in block.inputs():
                if unit not in self.depends:
                    raise ManifestError(
                        f"{self.name}: a derived block reads {unit!r}, which must be "
                        f"in depends so it is installed first"
                    )
        return self

    @model_validator(mode="after")
    def _default_block_last(self) -> PackageManifest:
        for i, block in enumerate(self.install[:-1]):
            if block.when.is_default:
                raise ManifestError(
                    f"{self.name}: unconditional install block at index {i} shadows "
                    f"{len(self.install) - i - 1} later block(s); defaults go last"
                )
        return self

    @model_validator(mode="after")
    def _status_is_explained(self) -> PackageManifest:
        """D-005: a verdict without provenance is not a verdict."""
        if self.status is Status.supported:
            return self
        missing = [
            f
            for f, v in (
                ("status_reason", self.status_reason),
                ("status_date", self.status_date),
                ("status_verdict", self.status_verdict),
            )
            if v is None
        ]
        if missing:
            raise ManifestError(
                f"{self.name}: status={self.status.value} requires {', '.join(missing)}"
            )
        if self.status is Status.retired and self.retire_reason is None:
            raise ManifestError(
                f"{self.name}: retired requires retire_reason (world_changed | "
                f"never_worked | out_of_scope)"
            )
        return self

    @model_validator(mode="after")
    def _no_install_target_needs_binaries(self) -> PackageManifest:
        """A build with no install rule has to be told what to install.

        `provides_install_target: false` replaces `make install` with an
        explicit copy of each declared binary. With no `binaries` that leaves a
        compiled tree and nothing on the PATH -- a build that succeeds and
        installs nothing, which is the silent-success failure this project
        keeps writing checks against (D-031).
        """
        for block in self.install:
            if getattr(block.install, "provides_install_target", True):
                continue
            if not effective_binaries(self, block) and not getattr(
                block.install, "install_tree", False
            ):
                raise ManifestError(
                    f"{self.name}: provides_install_target is false, so nothing would "
                    f"be installed. Declare `binaries` naming what the build emits and "
                    f"what each should be called in the prefix, or install_tree with a "
                    f"launcher into it."
                )
        return self

    @model_validator(mode="after")
    def _provides_excludes_self(self) -> PackageManifest:
        if self.name in self.provides:
            raise ManifestError(f"{self.name}: provides must not list the package itself")
        return self

    @model_validator(mode="after")
    def _endpoint_refs_declared(self) -> PackageManifest:
        """A launcher may not reference an endpoint the manifest does not declare."""
        declared = {e.name for e in self.service_endpoints}
        for launcher in self.launchers:
            for ref in ENDPOINT_REF.findall(launcher.exec):
                if ref not in declared:
                    raise ManifestError(
                        f"{self.name}: launcher {launcher.name!r} references "
                        f"undeclared endpoint {ref!r}"
                    )
        return self

    @model_validator(mode="after")
    def _launcher_does_not_shadow_node_wrapper(self) -> PackageManifest:
        """A launcher and a node block's wrapper share ``~/.local/bin``.

        Both are written there by name, so a launcher named like the node
        command overwrites the wrapper it composes through ``{node}`` — and
        then runs itself. Found on the first dry run of ``openhamclock``,
        whose launcher and manifest were both called ``openhamclock``.
        """
        commands = {
            block.install.command or self.name
            for block in self.install
            if isinstance(block.install, NodeInstall)
        }
        for launcher in self.launchers:
            if launcher.name in commands:
                raise ManifestError(
                    f"{self.name}: launcher {launcher.name!r} has the same name as the "
                    f"node wrapper it would overwrite in ~/.local/bin — name the "
                    f"launcher differently or set the node block's `command`"
                )
        return self

    @model_validator(mode="after")
    def _launcher_does_not_shadow_its_command(self) -> PackageManifest:
        """A launcher is never named like the program it runs (issue #174).

        The wrapper is written to ``~/.local/bin``, which comes before
        ``/usr/bin`` on Debian's PATH, so a launcher called ``rigctl`` *is*
        ``rigctl`` to every shell: it swallowed ``rigctl -l`` and opened the
        dummy-rig shell on the field laptop, and ``gpa``'s ``exec gpa`` ran
        itself until killed. The same holds for a name the manifest's own
        ``binaries`` install. The generator also refuses a name it finds on
        the system PATH or in the unit's apt file list; this is the half that
        needs no machine to measure.
        """
        installed = {b.install_as for b in self.binaries}
        for launcher in self.launchers:
            words = launcher.exec.split()
            if words[:1] == ["exec"]:
                words = words[1:]
            command = words[0] if words else ""
            # A bare name is found on PATH, where the launcher now comes
            # first. A path ({venv}/bin/pygpsclient) runs that file; whether
            # the path is on PATH is the generator's measurement, not this.
            if "/" not in command and launcher.name == command:
                raise ManifestError(
                    f"{self.name}: launcher {launcher.name!r} is named like the command it "
                    f"runs; the wrapper lands in ~/.local/bin, ahead of /usr/bin on PATH, so "
                    f"it would shadow the real {command} and swallow its arguments -- rename "
                    f"the launcher to say what it does (e.g. {command}-<what>) and keep its title"
                )
            if launcher.name in installed:
                raise ManifestError(
                    f"{self.name}: launcher {launcher.name!r} is named like a binary this "
                    f"manifest's `binaries` install, which it would shadow from ~/.local/bin "
                    f"-- rename the launcher to say what it does and keep its title"
                )
        return self

    @model_validator(mode="after")
    def _binaries_unique(self) -> PackageManifest:
        names = [b.install_as for b in self.binaries]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ManifestError(f"{self.name}: duplicate install_as: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _apt_repo_names_unique(self) -> PackageManifest:
        """Two repositories of one name would write the same two files."""
        names = [repo.name for repo in self.apt_repos]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ManifestError(f"{self.name}: duplicate apt repo names: {dupes}")
        return self

    def apt_repos_for(self, distro: str, distro_version: str, arch: str) -> list[AptRepo]:
        """The declared repositories whose ``when`` matches this target."""
        return [r for r in self.apt_repos if r.when.matches(distro, distro_version, arch)]

    @model_validator(mode="after")
    def _apt_repo_needs_rationale(self) -> PackageManifest:
        for repo in self.apt_repos:
            if len(repo.rationale) < 20:
                raise ManifestError(
                    f"{self.name}: apt repo {repo.name!r} needs a real rationale; "
                    f"it is shown to the user before the repo is added"
                )
        return self

    # -- resolution ---------------------------------------------------------

    def resolve(self, distro: str, distro_version: str, arch: str) -> InstallBlock | None:
        """First matching install block, or None if this target is unsupported."""
        for block in self.install:
            if block.when.matches(distro, distro_version, arch):
                return block
        return None

    @property
    def station_variables(self) -> set[str]:
        out: set[str] = set()
        for cfg in self.config_files:
            out |= cfg.station_variables
        return out


def derived_source_method_problem(
    manifest: PackageManifest, catalog: Mapping[str, PackageManifest]
) -> str | None:
    """D-061: a `derived` block's `converter` needs its `source` to be a unit
    of a particular install method (`CONVERTER_SOURCE_METHOD`) -- `gdal-dem`
    fed an `osm-regions` unit, or `mkgmap` fed a `dem-tiles` one, would resolve
    and install cleanly and fail only at run time, deep inside the converter.

    Catalog-wide, like `load._desktop_alternative_problem`: only the catalog
    knows what method the named `source` unit actually resolves to, which
    `_derived_source_is_in_depends` above cannot see from one manifest alone.
    Called from `load.load_catalog`. A `source` this function cannot find is
    not its problem -- that is `_derived_source_is_in_depends`'s "must be in
    depends" refusal, or a plain missing-manifest one; this only judges a
    `source` that resolved to something.
    """
    for entry in manifest.install:
        block = entry.install
        if not isinstance(block, DerivedDataInstall):
            continue
        needed = CONVERTER_SOURCE_METHOD[block.converter]
        source = catalog.get(block.source)
        if source is not None:
            methods = {b.install.method for b in source.install}
            if needed not in methods:
                return (
                    f"{manifest.name}: derived block with converter {block.converter!r} "
                    f"needs source {block.source!r} to be a {needed!r} unit, but "
                    f"{block.source!r} is {sorted(methods)!r}"
                )
        for field_name, (method, _) in CONVERTER_INPUTS.get(block.converter, {}).items():
            unit = getattr(block, field_name)
            found = catalog.get(unit) if unit is not None else None
            if found is None:
                continue
            installs = [b.install for b in found.install]
            if method not in {i.method for i in installs}:
                return (
                    f"{manifest.name}: derived block with converter {block.converter!r} "
                    f"needs {field_name} {unit!r} to be a {method!r} unit, but {unit!r} is "
                    f"{sorted({i.method for i in installs})!r}"
                )
            wanted = INPUT_PROVIDER.get(field_name)
            if wanted is not None and not any(
                getattr(i, "provider", None) == wanted for i in installs
            ):
                return (
                    f"{manifest.name}: {field_name} {unit!r} must be a {method!r} unit of "
                    f"provider {wanted!r}, but its provider is "
                    f"{sorted({str(getattr(i, 'provider', None)) for i in installs})!r}"
                )
            if field_name == "program" and not any(
                getattr(i, "install_tree", False) for i in installs
            ):
                return (
                    f"{manifest.name}: program {unit!r} must install a tree "
                    f"(install_tree: true): the converter reads its jar from there"
                )
    return None


def effective_binaries(manifest: PackageManifest, block: InstallBlock) -> Sequence[Binary]:
    """The binaries *this* block installs: its own list, else the manifest's.

    Every reader goes through here -- the three step builders, the post-run
    effect check, ``already_built``, ``uninstall``'s removal planning and the
    generated package reference -- because a manifest with per-block lists has
    two answers to "what does this install" and they must not disagree. One
    reader left on ``manifest.binaries`` would install the aarch64 block's
    files and then confirm the x86_64 block's names, which is the D-031 shape
    the block-level field exists to close (issue #69).
    """
    return block.binaries if block.binaries is not None else manifest.binaries


# ---------------------------------------------------------------------------
# Consent gates and profiles.  D-021.
# ---------------------------------------------------------------------------


class RiskCategory(StrEnum):
    """What the software *can do* — never what any jurisdiction says about it.

    Capability is stable and observable. Legality is neither, and asserting it
    would make this project give legal advice it is not qualified to give.
    """

    unlicensed_transmission = "unlicensed_transmission"
    protected_communications = "protected_communications"
    identifier_collection = "identifier_collection"
    third_party_systems = "third_party_systems"
    spectrum_disruption = "spectrum_disruption"
    credential_recovery = "credential_recovery"
    privileged_execution = "privileged_execution"


RISK_DISCLOSURES: dict[RiskCategory, str] = {
    RiskCategory.unlicensed_transmission: (
        "Can cause connected hardware to emit radio frequency energy, including on "
        "frequencies, at power levels, or in modes that may require a licence or "
        "other authorization."
    ),
    RiskCategory.protected_communications: (
        "Can receive, decode, store or display communications that may be protected "
        "from interception."
    ),
    RiskCategory.identifier_collection: (
        "Can collect identifiers associated with people or their devices, such as "
        "IMSI, IMEI, MAC addresses, or subscriber records."
    ),
    RiskCategory.third_party_systems: (
        "Can interact with, probe or test systems and networks that belong to someone else."
    ),
    RiskCategory.spectrum_disruption: (
        "Can degrade or deny service to other users of the radio spectrum, whether "
        "or not that is the intent."
    ),
    RiskCategory.credential_recovery: (
        "Can recover, crack or replay authentication material such as keys, "
        "passphrases or handshakes."
    ),
    RiskCategory.privileged_execution: (
        "Can exercise additional Linux privileges beyond the program owner's ordinary "
        "permissions, including network administration, raw packets, or privileged ports."
    ),
}

# Wording that turns a disclosure into an opinion about law. D-021 calls such
# wording a defect; making it a validation error means it cannot ship.
#
# Deliberately narrow. It targets adjudication ("this is illegal", "you may
# not") and jurisdiction ("under FCC rules"). It does NOT ban the words
# "licence" or "authorization", which are exactly what a disclosure must be
# able to say.
LEGAL_ADVICE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:is|are|would be|will be)\s+(?:il)?legal\b", "asserts what is or is not legal"),
    (r"\bunlawful\b", "asserts unlawfulness"),
    (r"\billegal\b", "asserts illegality"),
    (r"\bprohibited\b", "asserts prohibition"),
    (r"\bfelony\b|\bcriminal offen[cs]e\b", "characterises an offence"),
    (r"\byou may not\b|\byou are not allowed\b", "tells the user what they may do"),
    (r"\b(?:FCC|Ofcom|ETSI|ITU|CALEA|GDPR)\b", "names a specific regulator or statute"),
    (r"\bin (?:most|all|many) (?:countries|jurisdictions)\b", "generalises across jurisdictions"),
    (r"\bunder .{0,24}\blaw\b", "cites a body of law"),
    (r"\brequires? a licen[cs]e\b(?!\s+or)", "states a legal requirement as fact"),
)


class ConsentGate(Strict):
    """An affirmative opt-in that `--yes` cannot supply.  D-021.

    The gate discloses a capability and asks the operator to affirm they have
    the authorization they need. It does not decide for them in either
    direction — neither granting permission nor refusing on their behalf.
    """

    risk_categories: list[RiskCategory] = Field(min_length=1)
    env_var: str = Field(description="Scripted path. Separate from --yes, and recorded when used.")
    disclosure: str = Field(
        min_length=40,
        description="What the software can do. Capability, never legality.",
    )
    affirmation: str = Field(
        min_length=20,
        description="The question. Must ask about the operator's authorization.",
    )

    @model_validator(mode="after")
    def _check(self) -> ConsentGate:
        if not CONSENT_ENV.match(self.env_var):
            raise ManifestError(
                f"consent env_var {self.env_var!r} must match HAMMUNITION_ACCEPT_<NAME>; "
                f"a shared or generic variable would let one opt-in satisfy another gate"
            )
        if len(set(self.risk_categories)) != len(self.risk_categories):
            raise ManifestError("consent risk_categories contains duplicates")
        for text, field in ((self.disclosure, "disclosure"), (self.affirmation, "affirmation")):
            for pattern, why in LEGAL_ADVICE_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    raise ManifestError(
                        f"consent {field} reads as legal advice — it {why}. "
                        f"D-021: disclose the capability and ask about authorization; "
                        f"do not adjudicate. Offending text: {text[:80]!r}"
                    )
        if (
            RiskCategory.privileged_execution not in self.risk_categories
            and "authoriz" not in self.affirmation.lower()
            and "authoris" not in self.affirmation.lower()
        ):
            raise ManifestError(
                "consent affirmation must ask the operator to affirm their "
                "authorization; anything else is a warning, not a gate"
            )
        return self

    @property
    def risk_lines(self) -> list[str]:
        """Canonical one-line disclosure per declared category."""
        return [f"{c.value}: {RISK_DISCLOSURES[c]}" for c in self.risk_categories]


class ProfileDocumentation(Strict):
    """Required by CLAUDE.md for every profile."""

    what_it_installs: str = Field(min_length=20)
    why_together: str = Field(min_length=20)
    deliberately_excludes: str = Field(min_length=10)
    manual_configuration: str = Field(min_length=10)
    disk_footprint_hint: str | None = None
    # The fields below feed the generated profile pages and the profiles index
    # (scripts/gen_profile_reference.py). They are optional in the schema so a
    # community profile is not refused for lacking them, and
    # tests/test_profile_docs.py requires every profile this repository ships
    # to carry all of them.
    who_for: str | None = Field(
        default=None, description="Who installs this, in a sentence or two."
    )
    hardware_assumed: str | None = Field(
        default=None,
        description="What hardware the profile assumes, or says it needs none.",
    )
    footprint_short: str | None = Field(
        default=None, description="Disk footprint in a few words, for the index table."
    )
    excludes_short: str | None = Field(
        default=None, description="What it leaves out, in a phrase, for the index table."
    )
    goals: list[str] = Field(
        default_factory=list,
        description=(
            "Goals in an operator's words ('Make FT8 contacts'). The profiles "
            "index inverts these into its 'which profile do I want' table, so "
            "the same wording on two profiles puts both on one row."
        ),
    )
    first_ten_minutes: list[str] = Field(
        default_factory=list,
        description="Ordered steps for the ten minutes after install, Markdown.",
    )


class SuggestionGroup(Strict):
    """One-of-several optional companions, offered only when nothing serves.

    Born from the claws-mail decision (Q-015 #1, resolved 2026-08-30): the
    EMCOMM stack wants a local mail client, but choosing one for the
    operator is desktop-distribution work — so the engine *detects* first
    (any of ``detect_commands`` on PATH means the need is already met and
    the system's own choice is respected), and only when nothing is found
    does an interactive run offer ``options``, every one an open-source
    catalog manifest. ``--yes`` and non-interactive runs skip with a note,
    never block — the station-prompt precedent (D-035).
    """

    name: str
    reason: str = Field(
        min_length=20, description="Why the profile wants one, shown at the prompt."
    )
    detect_commands: list[str] = Field(
        min_length=1,
        description="Binaries whose presence means the need is already met.",
    )
    options: list[str] = Field(
        min_length=2,
        description="Catalog manifests to offer, in display order.",
    )
    recommended: str | None = Field(
        default=None,
        description="One of `options`, flagged at the prompt as the suggested pick.",
    )

    @model_validator(mode="after")
    def _recommended_is_an_option(self) -> SuggestionGroup:
        if self.recommended is not None and self.recommended not in self.options:
            raise ManifestError(
                f"suggestion group {self.name!r}: recommended {self.recommended!r} "
                f"is not one of its options"
            )
        return self


class ProfileManifest(Strict):
    """A named bundle of packages.  Flat tags with overlap, D-003."""

    name: str
    summary: str
    packages: list[str] = Field(min_length=1)
    stage: Literal["1.0", "post-1.0"] = "1.0"
    consent: ConsentGate | None = None
    documentation: ProfileDocumentation
    suggests_one_of: list[SuggestionGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> ProfileManifest:
        if not SLUG.match(self.name):
            raise ManifestError(f"profile name {self.name!r} must be a lowercase slug")
        if len(set(self.packages)) != len(self.packages):
            raise ManifestError(f"profile {self.name}: duplicate package entries")
        return self
