# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Building from a verified source archive.  DESIGN.md §6, D-004.

**This is the backend the project exists for.** 57 of AHRL's 95 units cannot be
satisfied by apt and 35 of those are source builds from bundled tarballs; an
apt-only tool covers roughly 40% of the parity target and the missing 60% is
precisely what users cannot install for themselves.

The build systems implemented here are the ones the catalog actually needs,
counted rather than assumed (**D-014**): **cmake 11, autotools 9, make 4,
qmake 3, qmake6 1** across the twenty-eight `source` and `git` blocks in the
catalog today. ``qmake6`` is a separate entry rather than a flag on ``qmake``
because it is a different binary: Debian 13 with only ``qt6-base-dev``
installed has no ``/usr/bin/qmake`` at all, and installing ``qt5-qmake`` to
supply the name would hand a Qt6 project the Qt5 tool.
Two fields are **measured zeros** and are refused by name rather than
speculatively implemented — no manifest uses ``custom``, and none carries
``patches``. Recording the zero is the point: it stops either being re-added
by convention, and it means the refusal names a real gap if one ever arrives.

Three properties worth stating up front.

**Nothing is built from bytes that were not verified.** The archive arrives
through :mod:`hammunition.fetch`, which refuses anything whose digest does not
match the manifest and leaves nothing usable behind when it does. The build
steps below take a path that verification already vouched for.

**The tree lands in a predictable place, so the plan can be printed before the
archive exists.** Extraction strips a single top-level directory into
``<build>/src`` when the archive has exactly one — nearly every release tarball
does — and unpacks as-is when it does not, which some zips (linrad's, for one)
require. Either way the source root is ``<build>/src``, so ``./configure`` and
``cmake -S`` can be rendered with real
paths at plan time rather than described in prose. A dry run that said "then
configure in whatever directory the tarball unpacks to" would be the
approximate dry run CLAUDE.md forbids.

**Extraction is done here, not by ``tar``.** An archive member named
``../../etc/cron.d/x``, an absolute path, or a symlink pointing out of the tree
is a well-known way to turn "unpack this" into "write anywhere", and the
defaults of the extractor decide whether that works. :func:`extract` uses
Python's ``data`` filter for tar and screens every zip member itself, then
verifies that what landed is inside the destination — checking the result and
not only the intent (**D-031**).
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition import netiso
from hammunition.manifest.schema import (
    Binary,
    InstallBlock,
    PackageManifest,
    Patch,
    SourceInstall,
    effective_binaries,
)
from hammunition.payloads import payload_action, payload_cached, preflight_payloads

from .base import Action, BackendError, Command

if TYPE_CHECKING:
    # Type-only: `hammunition.backends/__init__.py` imports this module
    # eagerly, and `hammunition.fetch` imports `hammunition.backends.base`,
    # so a module-level import here is the other half of #158's cycle.
    # `Fetcher` is only ever used in an annotation, which `from __future__
    # import annotations` defers, so it never needs a real import.
    # `operator_dir` and `remove_tree` are real runtime dependencies, unlike
    # `Fetcher`, and are imported locally where they are used instead.
    from hammunition.fetch import Fetcher
    from hammunition.resolution import ResolutionContext

__all__ = [
    "DEFAULT_PREFIX",
    "GIB_PER_JOB",
    "IMPLEMENTED_BUILD_SYSTEMS",
    "SourceBackend",
    "SourceLayout",
    "build_commands",
    "default_jobs",
    "extract",
    "install_binary_commands",
    "needs_root_for",
    "patch_steps",
    "prepare_tree",
    "tree_destination",
    "tree_install_commands",
]

#: FHS: locally-built software goes in /usr/local, where it does not collide
#: with anything the distribution's package manager owns.
DEFAULT_PREFIX = Path("/usr/local")

#: Counted from the catalog, not assumed (D-014). `custom` is a measured zero
#: and is refused by name.
IMPLEMENTED_BUILD_SYSTEMS = frozenset({"autotools", "cmake", "qmake", "qmake6", "make"})

_TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tbz", ".tar.xz", ".txz", ".tar")


#: Memory budget per parallel compiler job. JS8Call's Qt translation units
#: were OOM-killed at four jobs on a 3.9 GB guest with no swap (Ubuntu 26.04
#: cloud image, 2026-09-01) and built at four on a 3.9 GB guest with 3 GB of
#: swap — so the budget counts swap, and 2 GiB is the round number between.
GIB_PER_JOB = 2 * 1024**3


def default_jobs(cpu_count: int | None = None, meminfo: Path = Path("/proc/meminfo")) -> int:
    """Parallel build jobs: one per CPU, capped at one per :data:`GIB_PER_JOB`
    of memory plus swap, never below one.

    A build tool's ``-j$(nproc)`` assumes the machine was sized for it. A
    Raspberry Pi with four cores and 4 GB is not, and neither is a cloud image
    with no swap; the kernel then kills the compiler mid-file and the failure
    reads as a broken unit rather than an oversubscribed one.
    """
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    try:
        fields = dict(
            (line.split(":")[0], int(line.split()[1]) * 1024)
            for line in meminfo.read_text().splitlines()
            if line.startswith(("MemTotal:", "SwapTotal:"))
        )
    except (OSError, ValueError, IndexError):
        return cpus
    budget = fields.get("MemTotal", 0) + fields.get("SwapTotal", 0)
    if not budget:
        return cpus
    return max(1, min(cpus, budget // GIB_PER_JOB))


@dataclass(frozen=True)
class SourceLayout:
    """Where one package's build happens. Derived, so it is knowable at plan time."""

    root: Path
    """``<cache>/build/<package>-<digest8>``."""

    @property
    def src(self) -> Path:
        """The extracted tree. Always this path — see the module docstring."""
        return self.root / "src"

    @property
    def build(self) -> Path:
        """Out-of-tree build directory. cmake uses it; autotools builds in ``src``."""
        return self.root / "build"


def _is_tar(name: str) -> bool:
    return name.lower().endswith(_TAR_SUFFIXES)


def _is_zip(name: str) -> bool:
    return name.lower().endswith(".zip")


def _sniff(archive: Path) -> str | None:
    """``"tar"``, ``"zip"``, or None — from the bytes, not the name.

    The artifact cache names files by digest plus whatever the URL ended in,
    and a SourceForge URL ends in ``/download`` — MSHV's fetch verified
    cleanly and then could not be unpacked because the *name* said nothing
    (found 2026-08-30). The content is the authority; the suffix check
    remains only as the fallback for a bare uncompressed tar, whose 257-byte
    magic offset an empty-ish file may not reach.
    """
    with archive.open("rb") as handle:
        head = handle.read(6)
        handle.seek(257)
        ustar = handle.read(5)
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:2] == b"\x1f\x8b" or head[:3] == b"BZh" or head[:6] == b"\xfd7zXZ\x00":
        return "tar"
    if ustar == b"ustar":
        return "tar"
    return None


def _safe_members(names: list[str], destination: Path) -> None:
    """Refuse any archive member that would land outside *destination*.

    Used for zip, where there is no equivalent of tar's ``data`` filter. An
    absolute path, a ``..`` component, or a drive-style prefix is refused for
    the whole archive rather than skipped: a partially-extracted tree is not a
    thing to build from, and quietly dropping members would produce a build
    failure that says nothing about why.
    """
    for name in names:
        if name.startswith("/") or name.startswith("\\") or ":" in name.split("/", 1)[0]:
            raise BackendError(
                f"refusing to extract {name!r}: an archive member with an absolute "
                f"path would write outside the build directory"
            )
        target = (destination / name).resolve()
        if target != destination.resolve() and destination.resolve() not in target.parents:
            raise BackendError(
                f"refusing to extract {name!r}: it resolves outside the build "
                f"directory, which is how an archive turns 'unpack' into "
                f"'write anywhere'"
            )


def prepare_tree(destination: Path) -> str:
    """Remove any previous tree at *destination* and recreate it empty.

    Idempotent (CLAUDE.md): a re-run builds from a clean tree rather than
    layering a new checkout or archive over a half-built one, where a stale
    object file outlives the source it came from.

    Under sudo, the parent -- the unit's directory in the operator's build
    root -- is walked from the operator's home and proven theirs *before*
    anything is removed, and the old tree is removed and the new one made
    through that directory's descriptor: a symlink planted in the path
    (``build/unit-abc -> /somewhere``) is refused, never followed. That is
    all this protects. Root still builds by path inside an operator-owned
    directory afterwards, which an operator-uid process can race; that is a
    separate, open issue, not solved here. The git-bundle backend's recursive
    gitlink checkout (:func:`hammunition.gitbundles._ancestors_are_not_symlinks`)
    has the identical shape, tracked together as Hammunition #399.
    """
    # Late import: see the TYPE_CHECKING comment at the top of this module
    # (#158's cycle) -- hammunition.fetch has finished loading by the time any
    # backend actually runs, so this costs a sys.modules lookup, not a reload.
    from hammunition.fetch import operator_dir, remove_tree

    # The build root and the unit's directory are the operator's even under
    # sudo, or the operator's own later steps there fail with EACCES.
    with operator_dir(destination.parent) as parent_fd:
        existed = remove_tree(destination.parent, parent_fd, destination.name)
        if parent_fd is None:
            destination.mkdir()
        else:
            os.mkdir(destination.name, 0o755, dir_fd=parent_fd)
    return f"{'cleared and recreated' if existed else 'created'} {destination}"


def _selected(names: Sequence[str], members: Sequence[str] | None, archive: str) -> list[str]:
    """The archive names *members* select, every name when it is None.

    A member is an exact name, or ends in ``/`` and takes everything below
    it (the directory entry itself included, which a tar writes without
    the slash). One that selects nothing is refused by name: the archive is
    not the one the manifest describes (D-071).
    """
    if members is None:
        return list(names)
    chosen: list[str] = []
    for member in members:
        if member.endswith("/"):
            hits = [n for n in names if n.startswith(member) or n == member.rstrip("/")]
        else:
            hits = [n for n in names if n == member]
        if not hits:
            raise BackendError(
                f"{archive}: the member {member!r} matched nothing in the archive; the "
                f"manifest names a file this archive does not carry"
            )
        chosen.extend(h for h in hits if h not in chosen)
    return chosen


def extract(archive: Path, destination: Path, *, members: Sequence[str] | None = None) -> str:
    """Unpack *archive* into *destination*, stripping one top-level directory.

    *members*, when given, limits the unpacking to those names (D-071; see
    :func:`_selected`); the strip applies to what is unpacked.

    Returns a one-line outcome. Raises :class:`BackendError` on anything it
    will not unpack — an unknown format, or a member that would escape.

    The strip is what makes the source root predictable: nearly every release
    tarball contains exactly one top-level directory whose name carries the
    version, so keeping it would put the version in every subsequent path and
    make the plan unprintable before the download. When an archive has more
    than one top-level entry it is unpacked as-is, and the root is still
    ``destination``.
    """
    # Late import: see the TYPE_CHECKING comment at the top of this module
    # (#158's cycle) -- hammunition.fetch has finished loading by the time any
    # backend actually runs, so this costs a sys.modules lookup, not a reload.
    from hammunition.fetch import operator_dir, remove_tree

    staging = destination.parent / (destination.name + ".unpack")
    # Idempotent: a re-run rebuilds from a clean tree rather than layering a
    # new archive over a half-built one, where a stale object file outlives
    # the source it came from. Under sudo the parent is proven the
    # operator's first and both removals go through its descriptor, as in
    # prepare_tree -- and with the same limit: the unpacking below is by path.
    with operator_dir(destination.parent) as parent_fd:
        remove_tree(destination.parent, parent_fd, destination.name)
        remove_tree(destination.parent, parent_fd, staging.name)
        if parent_fd is None:
            staging.mkdir()
        else:
            os.mkdir(staging.name, 0o755, dir_fd=parent_fd)

    try:
        kind = _sniff(archive)
        if kind == "tar" or (kind is None and _is_tar(archive.name)):
            with tarfile.open(archive) as tar:
                infos = tar.getmembers()
                wanted = set(_selected([i.name for i in infos], members, archive.name))
                chosen = [i for i in infos if i.name in wanted]
                # PEP 706. Refuses absolute paths, `..`, links pointing outside
                # the destination, device nodes, and setuid/setgid bits.
                tar.extractall(staging, members=chosen, filter="data")
                count, total = len(chosen), len(infos)
        elif kind == "zip" or (kind is None and _is_zip(archive.name)):
            with zipfile.ZipFile(archive) as archive_zip:
                all_names = archive_zip.namelist()
                names = _selected(all_names, members, archive.name)
                _safe_members(names, staging)
                archive_zip.extractall(staging, members=names)
                count, total = len(names), len(all_names)
                # Python's zipfile discards the unix mode bits a zip records
                # in external_attr, so an executable `configure` arrives
                # unrunnable (linrad's zip proved it -- AHRL's script chmods
                # by hand for the same reason). Restore what the archive
                # actually recorded; never invent a bit it did not carry.
                for info in archive_zip.infolist():
                    mode = (info.external_attr >> 16) & 0o777
                    if mode and info.filename in names:
                        os.chmod(staging / info.filename, mode)
        else:
            raise BackendError(
                f"{archive.name} is not an archive this backend unpacks. "
                f"Supported: {', '.join(_TAR_SUFFIXES)}, .zip"
            )

        entries = list(staging.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            os.replace(entries[0], destination)
            stripped = entries[0].name
        else:
            os.replace(staging, destination)
            stripped = ""
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    # D-031: check what landed, not that the extractor returned. A filter that
    # silently dropped everything and one that worked both "succeed".
    if not destination.exists() or not any(destination.iterdir()):
        raise BackendError(
            f"{archive.name} unpacked to nothing. The archive is empty, or every "
            f"member was refused by the extraction filter."
        )
    note = f", stripped {stripped}/" if stripped else ""
    if members is not None:
        return f"unpacked {count} of {total} entries (the listed members only){note}"
    return f"unpacked {count} entries{note}"


class SourceBackend:
    """Turns a ``source`` install block into the steps that build it."""

    method = "source"

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        build_root: Path,
        prefix: Path = DEFAULT_PREFIX,
        jobs: int | None = None,
        owner: str | None = None,
        context: ResolutionContext | None = None,
        isolation: str | None = None,
    ) -> None:
        #: The network sandbox (``netiso``) offline builds run in; None when
        #: the machine has none, which refuses an offline build.
        self.isolation = isolation
        self.fetcher = fetcher
        self.build_root = build_root
        self.prefix = prefix
        #: The run's resolution context: offline, a payload the Bunker cannot
        #: answer for is refused before any build step exists.
        self.context = context
        self.jobs = jobs if jobs is not None else default_jobs()
        #: The operator an installed tree is handed to (D-043); None keeps it root's.
        self.owner = owner

    def layout(self, manifest: PackageManifest, block: SourceInstall) -> SourceLayout:
        """Where this package builds. Pure — touches no disk, so the plan can
        print every path before anything is fetched."""
        return SourceLayout(self.build_root / f"{manifest.name}-{block.source.sha256[:8]}")

    def steps(
        self, manifest: PackageManifest, install_block: InstallBlock
    ) -> list[Action | Command]:
        """Fetch, verify, unpack, configure, build, install — in that order.

        Only the final install needs root. CLAUDE.md drops to the operator
        wherever possible, and a build that ran wholly as root would leave a
        tree of root-owned objects in the operator's cache for no benefit.

        Takes the whole :class:`InstallBlock` rather than its method alone: a
        block may declare its own `binaries`, and the list this backend copies
        must be the one the effect check reads back (issue #69).
        """
        block = install_block.install
        if not isinstance(block, SourceInstall):
            raise BackendError(
                f"{manifest.name} resolved to a {block.method} block and this backend "
                f"builds from a source archive. Building it anyway would install "
                f"something the plan never named."
            )
        if block.build_system not in IMPLEMENTED_BUILD_SYSTEMS:
            raise BackendError(
                f"{manifest.name} declares build_system {block.build_system!r}, which "
                f"this engine build does not implement (it implements "
                f"{', '.join(sorted(IMPLEMENTED_BUILD_SYSTEMS))}). No manifest in the "
                f"catalog uses it, so it is an unimplemented gap rather than a "
                f"regression — D-014 records the zero rather than building for it."
            )

        preflight_payloads(
            manifest.name,
            ((block.source, None),),
            context=self.context,
            cached=payload_cached(self.fetcher),
        )
        layout = self.layout(manifest, block)
        artifact = block.source
        steps: list[Action | Command] = [
            payload_action(manifest.name, artifact, self.fetcher, label="source archive"),
            Action(
                kind="extract",
                description=f"Unpack the {manifest.name} source",
                detail=f"{self.fetcher.path_for(artifact)} -> {layout.src}",
                perform=lambda: extract(self.fetcher.path_for(artifact), layout.src),
            ),
        ]
        steps.extend(patch_steps(manifest.name, block.patches, layout))
        steps.extend(self._build_commands(manifest, install_block, block, layout))
        return steps

    def _isolated(self, name: str, commands: list[Command], layout: SourceLayout) -> list[Command]:
        """Offline, upstream's build runs in the bwrap sandbox (no network, a
        read-only filesystem but the build tree and the install prefix, and
        /run, /tmp, the operator's home, /opt and the other places a build
        has no reason to read all private — not /usr/local, the one writable
        prefix, which stays exposed under its own bind-try either way) so it
        cannot fetch anything the Bunker did not vouch for, nor reach the
        common host sockets (docker, podman, dbus); online it is unchanged."""
        if not self.fetcher.offline:
            return commands
        if self.isolation != netiso.BWRAP:
            raise BackendError(
                f"{name}: an offline source build runs upstream's build code and needs the "
                f"bwrap sandbox (no network, read-only filesystem, private /run, /tmp and "
                f"the operator's home), and this machine has none that works. Nothing was "
                f"planned."
            )
        writable = [layout.root, self.prefix]
        return [
            dataclasses.replace(command, argv=netiso.sandbox(command.argv, writable=writable))
            for command in commands
        ]

    def _build_commands(
        self,
        manifest: PackageManifest,
        install_block: InstallBlock,
        block: SourceInstall,
        layout: SourceLayout,
    ) -> list[Command]:
        commands = self._isolated(
            manifest.name,
            build_commands(
                name=manifest.name,
                build_system=block.build_system,
                layout=layout,
                prefix=self.prefix,
                jobs=self.jobs,
                configure_args=block.configure_args,
                compiler_flags=block.compiler_flags,
                project_file=block.project_file,
                build_args=block.build_args,
                provides_install_target=block.provides_install_target,
                binaries=effective_binaries(manifest, install_block),
                autoreconf=block.autoreconf,
            ),
            layout,
        )
        if block.install_tree:
            commands.extend(
                tree_install_commands(
                    name=manifest.name,
                    source_tree=layout.src,
                    prefix=self.prefix,
                    owner=self.owner,
                )
            )
        return commands


#: Directories where installing is a system-wide change. FHS, plus /opt.
SYSTEM_ROOTS = (Path("/usr"), Path("/opt"), Path("/srv"), Path("/etc"), Path("/var"))


def needs_root_for(prefix: Path) -> bool:
    """Whether installing into *prefix* is a privileged, system-wide change.

    A property of the **destination**, not of the process asking. That
    distinction is the whole point, and the first version of this function got
    it wrong: it asked ``os.access(prefix, W_OK)``, which answers "can *I* write
    here" — so under sudo, and in every one of our root-running target
    containers, ``/usr/local`` came back writable and the install step declared
    it needed no privilege. Six container jobs caught it that the dev machine
    could not, because the dev machine is not root.

    :class:`Command` already states the rule this restores: *already being root
    is not the same as not needing root*, so the flag stays true and only the
    ``sudo`` prefix disappears when the euid is 0.

    Deciding from the path also makes the answer the same on every machine,
    which a plan that must be printed and compared needs it to be.
    """
    resolved = prefix.resolve()
    return any(resolved == root or root in resolved.parents for root in SYSTEM_ROOTS)


def _compiler_env(compiler_flags: Sequence[str]) -> dict[str, str]:
    """``compiler_flags`` as CFLAGS/CXXFLAGS/CPPFLAGS in the environment.

    Six AHRL units need ``-Wno-*`` to build against a modern toolchain, and AHRL
    carries them as shell string-mangling. Declaring them means the flags are
    catalog data a reviewer can see, and the day a compiler stops needing one it
    is deleted from a manifest rather than hunted for in a script.

    All three variables, because make's precedence makes any single one a
    gamble: a Makefile that assigns ``CFLAGS = -g`` (ardopcf does) silently
    discards the environment's CFLAGS, but the same Makefile *appends* with
    ``CPPFLAGS += -Isrc``, and an appended variable starts from the
    environment — so the flag arrives through CPPFLAGS with the include path
    intact. Found when ardopcf's first real engine build failed on Parrot with
    the exact error its flag exists to silence. Passing flags as command-line
    ``make`` arguments instead would override appends entirely and drop
    upstream's own values, which is worse.
    """
    if not compiler_flags:
        return {}
    flags = " ".join(compiler_flags)
    return {"CFLAGS": flags, "CXXFLAGS": flags, "CPPFLAGS": flags}


def install_binary_commands(
    *,
    name: str,
    produced_in: Path,
    prefix: Path,
    binaries: Sequence[Binary],
) -> list[Command]:
    """Install named build outputs, for a project whose build has no install rule.

    Two of the catalog's qmake units -- MSHV and Coil64 -- ship a ``.pro`` with
    no ``INSTALLS``, so ``make install`` has nothing to do and fails. AHRL's
    answer is to leave the binary in the build tree and generate a launcher
    that ``cd``s into it, which is why its menu entries carry working
    directories. Copying the declared binary into the prefix is the better
    answer and it is what makes the `binaries` field mean something rather than
    being documentation.

    ``produced_in`` is the directory the build emits into -- cmake's
    out-of-tree build directory, the source tree for everything else, and the
    extracted tree for a binary archive. It was the source tree unconditionally
    until js8call, a cmake build with no install rule, needed this path and
    would have looked for ``JS8Call`` where cmake never writes it (2026-09-02).

    `install -D` creates the target directory, so no separate mkdir is needed.
    """
    return [
        Command(
            argv=(
                "install",
                "-D",
                "-m",
                "0755",
                str(produced_in / binary.produced),
                str(prefix / "bin" / binary.install_as),
            ),
            description=f"Install {name}'s {binary.produced} as {binary.install_as}",
            requires_root=needs_root_for(prefix),
        )
        for binary in binaries
    ]


def tree_install_commands(
    *, name: str, source_tree: Path, prefix: Path, owner: str | None = None
) -> list[Command]:
    """Install the whole tree to ``<prefix>/share/hammunition/<name>``.

    For software that reads settings, resources or data beside its executable
    (source-build-gaps #6 and #8 -- MSHV, run-in-place Python and Java
    trees). ``cp -aT`` replaces content in place; the target is wholly ours,
    under our own share/ namespace, so clearing it first is safe and keeps a
    re-run from accumulating files upstream deleted.

    The tree belongs to *owner*, the operator the run is on behalf of, and
    says so (D-043). Two of these units write beside their executable --
    MSHV its settings and logs, radiosonde-auto-rx its ``log/`` -- so they
    only run because the tree is theirs. The first version got that by
    accident: ``cp -a`` under root preserves the build tree's owner, which is
    whoever unpacked it, and nothing in the log said so (issue #38). The copy
    now preserves nothing about ownership and the hand-over is its own step,
    so the transaction log records it and ``uninstall`` has nothing extra to
    undo. Without an owner root keeps the tree, as with any ``make install``.
    A prefix that needs no root is written as the operator already, so no
    hand-over is planned there.
    """
    destination = prefix / "share" / "hammunition" / name
    privileged = needs_root_for(prefix)
    commands = [
        Command(
            argv=("rm", "-rf", "--", str(destination)),
            description=f"Clear any previous {name} tree",
            requires_root=privileged,
        ),
        Command(
            argv=("install", "-d", str(destination.parent)),
            description=f"Ensure {destination.parent} exists",
            requires_root=privileged,
        ),
        Command(
            argv=("cp", "-aT", "--no-preserve=ownership", str(source_tree), str(destination)),
            description=f"Install the {name} tree into {destination}",
            requires_root=privileged,
        ),
    ]
    if privileged and owner:
        # -h changes the tree's own symlinks and never follows one: a link
        # pointing outside the tree cannot hand root's files to the operator
        # (measured on Debian 13, 2026-09-07).
        commands.append(
            Command(
                argv=("chown", "-R", "-h", "--", f"{owner}:", str(destination)),
                description=(
                    f"Hand the {name} tree to {owner}, who runs it and whose "
                    f"settings and logs it keeps beside its executable"
                ),
                requires_root=True,
            )
        )
    return commands


def tree_destination(prefix: Path, name: str) -> Path:
    """Where :func:`tree_install_commands` puts *name*'s tree."""
    return prefix / "share" / "hammunition" / name


def write_patch(path: Path, diff: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(diff if diff.endswith("\n") else diff + "\n")
    return f"staged patch at {path}"


def patch_steps(
    name: str,
    patches: Sequence[Patch],
    layout: SourceLayout,
) -> list[Action | Command]:
    """Stage each declared diff and apply it with patch(1), in order.

    Implemented the day the measured zero ended: linrad's Makefile bakes
    -Werror into a literal flag string with no variable to override, so it
    cannot ship without an in-tree edit (source-build-gaps #2). AHRL does
    these edits with sed at install time; a declared unified diff is
    reviewable in the catalog and fails loudly when upstream moves the code
    it touches. Idempotent the same way builds are: the tree is cleared and
    re-extracted every run, so a patch never applies twice. Manifests with
    patches must carry `patch` in build_depends.
    """
    from functools import partial

    steps: list[Action | Command] = []
    for index, declared in enumerate(patches):
        if not declared.unified_diff:
            raise BackendError(
                f"{name}: patch for {declared.file!r} declares no unified_diff — a "
                f"description alone cannot be applied, and building unpatched source "
                f"would produce a binary the manifest does not describe"
            )
        staged = layout.root / "patches" / f"{index:02d}-{Path(declared.file).name}.diff"
        steps.append(
            Action(
                kind="patch",
                description=f"Stage the {declared.file} patch: {declared.description}",
                detail=str(staged),
                perform=partial(write_patch, staged, declared.unified_diff),
            )
        )
        steps.append(
            Command(
                argv=("patch", "-p1", "-i", str(staged)),
                description=f"Apply it to {declared.file}",
                cwd=layout.src,
            )
        )
    return steps


def build_commands(
    *,
    name: str,
    build_system: str,
    layout: SourceLayout,
    prefix: Path,
    jobs: int,
    configure_args: Sequence[str] = (),
    compiler_flags: Sequence[str] = (),
    project_file: str | None = None,
    build_args: Sequence[str] = (),
    provides_install_target: bool = True,
    binaries: Sequence[Binary] = (),
    autoreconf: bool = False,
    build_env: Mapping[str, str] | None = None,
) -> list[Command]:
    """Configure, compile and install, for one build system.

    Shared by the source and git backends, because how a tree *arrived* — an
    archive verified by digest, or a clone pinned to a revision — says nothing
    about how it is built. Keeping one copy is what stops the two drifting into
    subtly different builds of the same software.

    Only the final command is privileged. CLAUDE.md drops to the operator
    wherever possible, and a build run wholly as root would leave a tree of
    root-owned objects in the operator's cache for no benefit.

    ``build_env`` reaches the configure and compile steps and never the
    install: a git block's build Python (D-069) is a build dependency, and
    the privileged step has no use for it.
    """
    env = {**_compiler_env(compiler_flags), **(build_env or {})}
    args = list(configure_args)
    jobs_arg = str(jobs)
    privileged = needs_root_for(prefix)
    # A project with no install rule gets an explicit copy of what it emits
    # instead of a `make install` that would fail. The schema refuses the
    # combination of no-install-target and no declared binaries.
    explicit = (
        install_binary_commands(
            name=name,
            produced_in=layout.build if build_system == "cmake" else layout.src,
            prefix=prefix,
            binaries=binaries,
        )
        if not provides_install_target
        else None
    )
    if build_system == "cmake":
        return [
            Command(
                argv=(
                    "cmake",
                    # --fresh: the source tree is cleared and re-extracted
                    # every run, but the build dir survives with cmake's
                    # cache -- including cached try_run verdicts, so a fixed
                    # source kept failing on the previous source's cached
                    # failure (dumphfdl's liquid check, Kali, 2026-08-30).
                    # Idempotent re-runs need the configure to be fresh too.
                    "--fresh",
                    "-S",
                    # `project_file` names the subdirectory holding the
                    # project's CMakeLists.txt (Signal-Server's is in src/,
                    # D-061 amended 2026-10-02); the schema documented it so
                    # long before this path read it.
                    str(layout.src / project_file) if project_file else str(layout.src),
                    "-B",
                    str(layout.build),
                    f"-DCMAKE_INSTALL_PREFIX={prefix}",
                    "-DCMAKE_BUILD_TYPE=Release",
                    *args,
                ),
                description=f"Configure {name} with cmake",
                env=env,
                # cwd matters even with -S/-B: execute_process() children in
                # the project's CMakeLists inherit it, and rtlsdr-airband's
                # version script runs `git describe` from wherever that is.
                # Undefined cwd made the answer depend on where the engine
                # happened to be started (measured 2026-08-30).
                cwd=layout.src,
            ),
            Command(
                argv=("cmake", "--build", str(layout.build), "--parallel", jobs_arg),
                description=f"Compile {name} ({jobs} parallel {'job' if jobs == 1 else 'jobs'}; sized to CPUs and memory)",
                long_running=True,
                env=env,
            ),
            *(
                explicit
                if explicit is not None
                else [
                    Command(
                        argv=("cmake", "--install", str(layout.build)),
                        description=f"Install {name} into {prefix}",
                        requires_root=privileged,
                    )
                ]
            ),
        ]

    if build_system == "autotools":
        return [
            *(
                [
                    Command(
                        argv=("autoreconf", "-fi"),
                        description=f"Generate {name}'s configure (autoreconf -fi)",
                        env=env,
                        cwd=layout.src,
                    )
                ]
                if autoreconf
                else []
            ),
            Command(
                argv=("./configure", f"--prefix={prefix}", *args),
                description=f"Configure {name}",
                env=env,
                cwd=layout.src,
            ),
            Command(
                # build_args reach make here too (gap #1: linrad's build is
                # ./configure then `make xlinrad64` -- a bare make prints usage
                # and stops).
                argv=("make", "-j", jobs_arg, *build_args),
                description=f"Compile {name} ({jobs} parallel {'job' if jobs == 1 else 'jobs'}; sized to CPUs and memory)",
                long_running=True,
                env=env,
                cwd=layout.src,
            ),
            *(
                explicit
                if explicit is not None
                else [
                    Command(
                        argv=("make", "install"),
                        description=f"Install {name} into {prefix}",
                        requires_root=privileged,
                        cwd=layout.src,
                    )
                ]
            ),
        ]

    if build_system in {"qmake", "qmake6"}:
        # `project_file` exists because MSHV needs a different .pro per
        # architecture; without it qmake picks the only one in the tree.
        #
        # qmake6 is a separate build system rather than a detail, because it is
        # a different binary and the choice is a fact about the project. On
        # Debian 13 with only qt6-base-dev installed there is no `qmake` at all
        # -- only `/usr/bin/qmake6` -- and installing `qt5-qmake` to provide the
        # name would hand a Qt6 project the Qt5 tool. Measured 2026-08-28.
        project = [project_file] if project_file else []
        return [
            Command(
                argv=(build_system, *project, f"PREFIX={prefix}", *args),
                description=f"Configure {name} with {build_system}",
                env=env,
                cwd=layout.src,
            ),
            Command(
                # build_args reach make here too (gap #1: linrad's build is
                # ./configure then `make xlinrad64` -- a bare make prints usage
                # and stops).
                argv=("make", "-j", jobs_arg, *build_args),
                description=f"Compile {name} ({jobs} parallel {'job' if jobs == 1 else 'jobs'}; sized to CPUs and memory)",
                long_running=True,
                env=env,
                cwd=layout.src,
            ),
            *(
                explicit
                if explicit is not None
                else [
                    Command(
                        argv=("make", "install"),
                        description=f"Install {name} into {prefix}",
                        requires_root=privileged,
                        cwd=layout.src,
                    )
                ]
            ),
        ]

    # `make`: no configure step at all.
    return [
        Command(
            argv=("make", "-j", jobs_arg, *build_args),
            description=f"Compile {name} ({jobs} parallel {'job' if jobs == 1 else 'jobs'}; sized to CPUs and memory)",
            long_running=True,
            env=env,
            cwd=layout.src,
        ),
        *(
            explicit
            if explicit is not None
            else [
                Command(
                    argv=("make", "install", f"PREFIX={prefix}"),
                    description=f"Install {name} into {prefix}",
                    requires_root=privileged,
                    cwd=layout.src,
                )
            ]
        ),
    ]
