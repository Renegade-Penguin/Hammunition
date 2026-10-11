# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The data backend: offline datasets whose payload is the point (D-049).

A map tileset, a Wikipedia ZIM, the DX-cluster country file. Each artifact
is fetched into the shared cache and verified against its sha256 like every
other download; the declared ``size`` is checked against the bytes received,
so a manifest that says 0.69 GB and fetches something else is refused rather
than trusted. A ``file`` is copied under the unit's data directory; a ``zip``
or ``tarball`` is extracted into it, or into its own subdirectory ``into``,
through the same guarded extraction the source backend uses, and only its
listed ``members`` when it lists any (D-071). An archive is unpacked into the
operator's build directory first and installed by the plan's own printed
commands (``install -d``, ``cp -aT``, the D-043 ``chown -R -h``), privileged
when the prefix is root's (#271). Nothing here is executed, ever.

The install directory is ``<prefix>/share/hammunition/data/<name>/`` -- a
namespace only this engine writes, which is what lets ``uninstall`` remove
it whole from the log's ``install-data`` records without guessing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from ..manifest.schema import DataArtifact, DataInstall, PackageManifest, RegisterInstall
from .base import Action, BackendError, Command, CommandRunner
from .source import extract, needs_root_for
from .verified import PrefixWriter

if TYPE_CHECKING:
    # Type-only: `hammunition.backends/__init__.py` imports this module
    # eagerly, and `hammunition.fetch` imports `hammunition.backends.base`,
    # so a module-level import here is the other half of #158's cycle.
    # `Fetcher` is only ever used in an annotation, which `from __future__
    # import annotations` defers, so it never needs a real import.
    # `fetch_disclosure`, `record_fetch` and `safe_name` are real runtime
    # dependencies, unlike `Fetcher` and `MirrorPath`, and are imported
    # locally where they are used instead.
    from ..fetch import Fetcher, MirrorPath


def human_size(size: int) -> str:
    """``353536`` -> ``354 KB``; ``690000000`` -> ``0.69 GB``. Decimal, as publishers quote."""
    if size >= 100_000_000:
        # Publishers quote a 690 MB tileset as 0.69 GB; so does Q-021.
        return f"{size / 1e9:.2f} GB"
    if size >= 1_000_000:
        return f"{size / 1e6:.1f} MB"
    return f"{size / 1e3:.0f} KB"


def data_name(artifact: DataArtifact) -> str:
    """The artifact's stable name within its unit, as ``hammunition
    artifacts`` lists it and a LAN mirror serves it (D-070): the name it is
    installed under, or for an archive the file name its URL ends in."""
    # Late import: see the TYPE_CHECKING comment at the top of this module
    # (#158's cycle) -- hammunition.fetch has finished loading by the time any
    # backend actually runs, so this costs a sys.modules lookup, not a reload.
    from ..fetch import safe_name

    return artifact.install_as or safe_name(artifact.url)


@dataclass(frozen=True)
class DataBackend:
    """Turns a ``data`` install block into fetch-and-install steps."""

    fetcher: Fetcher
    prefix: Path
    runner: CommandRunner | None = None
    """Escalates the copy into a root-owned prefix when the engine is not root."""
    build_root: Path | None = None
    """Where an archive is unpacked before it is installed: the operator's
    scratch, never the prefix. None puts it beside the fetch cache."""
    owner: str | None = None
    """The operator an installed tree is handed to (D-043); None keeps it root's."""
    provenance: Mapping[tuple[str, str], str] | None = None
    """Where the Bunker's catalogue vouched for an item this run (the resolution
    context's notes, keyed ``(unit, name)``); each such fetch line says so."""
    method = "data"

    def _vouched(self, unit: str, name: str) -> str:
        note = (self.provenance or {}).get((unit, name))
        return f" [{note}]" if note else ""

    def data_dir(self, manifest: PackageManifest) -> Path:
        return self.prefix / "share" / "hammunition" / "data" / manifest.name

    def staging_dir(self, manifest: PackageManifest, artifact: DataArtifact) -> Path:
        """Where an archive artifact is unpacked first. Keyed by its digest."""
        root = self.build_root or self.fetcher.cache_dir.parent / "builds"
        return root / f"{manifest.name}-data-{artifact.sha256[:12]}"

    def steps(self, manifest: PackageManifest, block: DataInstall) -> list[Action | Command]:
        # Late import: see the TYPE_CHECKING comment at the top of this
        # module (#158's cycle) -- hammunition.fetch has finished loading by
        # the time any backend actually runs, so this costs a sys.modules
        # lookup, not a reload.
        from ..fetch import MirrorPath, fetch_disclosure

        steps: list[Action | Command] = []
        target_dir = self.data_dir(manifest)
        for artifact in block.artifacts:
            fetched: dict[str, Path] = {}
            facts: dict[str, str] = {}
            where = MirrorPath(manifest.name, data_name(artifact))
            note, urls, sources = fetch_disclosure(self.fetcher, artifact.url, where, "sha256")
            steps.append(
                Action(
                    kind="fetch",
                    description=(
                        f"Fetch {manifest.name} data ({human_size(artifact.size)}, "
                        f"{block.licence}){note}{self._vouched(manifest.name, where.name)}"
                    ),
                    detail=f"{urls} (sha256 {artifact.sha256[:12]}…, {artifact.size} bytes)",
                    perform=partial(self._fetch, artifact, fetched, where, facts),
                    sources=sources,
                    facts=facts,
                )
            )
            if artifact.format == "file":
                assert artifact.install_as is not None  # the schema requires it
                dest = target_dir / artifact.install_as
                description = f"Install {manifest.name} data file {artifact.install_as}"
            else:
                dest = target_dir if artifact.into is None else target_dir / artifact.into
                target = "its data directory" if artifact.into is None else str(dest)
                which = (
                    f", only the listed members ({len(artifact.members)})"
                    if artifact.members is not None
                    else ""
                )
                description = (
                    f"Extract {manifest.name} data ({artifact.format}) into {target}{which}"
                )
            if artifact.format == "file":
                steps.append(
                    Action(
                        kind="install-data",
                        description=description,
                        # The destination, verbatim: uninstall's attribution replay
                        # reads it back, as it does install-binary's.
                        detail=str(dest),
                        perform=partial(self._install, artifact, fetched, dest),
                        requires_root=needs_root_for(self.prefix),
                    )
                )
                continue
            staged = self.staging_dir(manifest, artifact)
            steps.append(
                Action(
                    kind="install-data",
                    description=f"{description}, unpacked first under {staged} as you "
                    f"(modes 0644 files, 0755 directories); nothing is written to the "
                    f"prefix by this step",
                    # The destination, verbatim, as for a file (see above).
                    detail=str(dest),
                    perform=partial(self._stage, artifact, fetched, staged),
                )
            )
            steps.extend(self._tree_commands(manifest.name, staged, dest))
        return steps

    def register_steps(
        self, manifest: PackageManifest, block: RegisterInstall
    ) -> list[Action | Command]:
        """A ``register`` block (D-074, amended 2026-10-01): the ACMA's
        register, fetched whole, checked by its own structure only, and
        installed as one file under the unit's data directory."""
        from .. import acma
        from ..fetch import MirrorPath, fetch_disclosure

        where = MirrorPath(manifest.name, acma.FILE_NAME)
        note, urls, sources = fetch_disclosure(self.fetcher, acma.URL, where, "zip's own structure")
        fetched: dict[str, Path] = {}
        digests: dict[str, str] = {}
        facts: dict[str, str] = {}
        dest = self.data_dir(manifest) / acma.FILE_NAME
        return [
            Action(
                kind="fetch",
                description=(
                    f"Fetch {manifest.name} data (about {human_size(acma.MEASURED_SIZE)}, "
                    f"{block.licence}) — UNVERIFIED: no checksum is published{note}"
                    f"{self._vouched(manifest.name, acma.FILE_NAME)}"
                ),
                detail=f"{urls} ({acma.VERIFIED_BY})",
                perform=partial(self._fetch_register, fetched, digests, where, facts),
                sources=sources,
                facts=facts,
            ),
            Action(
                kind="install-data",
                description=f"Install {manifest.name} data file {acma.FILE_NAME}, then delete "
                f"its cached copy",
                detail=str(dest),
                perform=partial(self._install_register, fetched, digests, dest),
                requires_root=needs_root_for(self.prefix),
            ),
        ]

    def _fetch_register(
        self,
        fetched: dict[str, Path],
        digests: dict[str, str],
        where: MirrorPath,
        facts: dict[str, str],
    ) -> str:
        from .. import acma
        from ..fetch import VerificationError, record_fetch

        def check(path: Path) -> None:
            try:
                acma.check_register(path)
            except acma.AcmaError as exc:
                raise VerificationError(str(exc)) from None

        result = self.fetcher.fetch_checked(
            acma.URL, max_bytes=acma.FETCH_LIMIT, check=check, mirror=where
        )
        source = record_fetch(result, facts, mirrored=bool(self.fetcher.mirror))
        fetched["path"] = result.path
        digests["sha256"] = result.sha256
        return (
            f"downloaded {result.size} bytes, unverified: every member's CRC-32 and the "
            f"tables checked, sha256 {result.sha256[:12]}… recorded{source}"
        )

    def _install_register(
        self, fetched: dict[str, Path], digests: dict[str, str], dest: Path
    ) -> str:
        path = fetched.get("path")
        if path is None:  # pragma: no cover
            raise BackendError("the register was not fetched before the install step")
        size = path.stat().st_size
        writer = PrefixWriter(privileged=needs_root_for(self.prefix), runner=self.runner)
        writer.install_verified(path, dest, algorithm="sha256", digest=digests["sha256"])
        path.unlink(missing_ok=True)
        return (
            f"installed {dest} ({human_size(size)}, mode 0644, sha256 "
            f"re-checked against what arrived); deleted the cached copy"
        )

    def _fetch(
        self,
        artifact: DataArtifact,
        fetched: dict[str, Path],
        where: MirrorPath,
        facts: dict[str, str],
    ) -> str:
        # Late import: see the TYPE_CHECKING comment at the top of this
        # module (#158's cycle) -- hammunition.fetch has finished loading by
        # the time any backend actually runs, so this costs a sys.modules
        # lookup, not a reload.
        from ..fetch import record_fetch

        result = self.fetcher.fetch(artifact, mirror=where)
        source = record_fetch(result, facts, mirrored=bool(self.fetcher.mirror))
        if result.size != artifact.size:
            raise BackendError(
                f"{artifact.url}: the manifest declares {artifact.size} bytes and the "
                f"download is {result.size}; the digest matched, so the declaration is "
                f"wrong -- fix the manifest, the plan printed a size that was not true"
            )
        fetched["path"] = result.path
        how = "cached" if result.from_cache else "downloaded"
        return f"{how} {result.size} bytes, sha256 {result.sha256[:12]}… verified{source}"

    def _install(self, artifact: DataArtifact, fetched: dict[str, Path], dest: Path) -> str:
        path = fetched.get("path")
        if path is None:  # pragma: no cover
            raise BackendError("the data artifact was not fetched before the install step")
        if artifact.format == "file":
            # Copy, never move: the cache is content-addressed and shared. And
            # never trusted for having verified once -- the cache is the
            # operator's and the prefix is root's, so the copy is opened
            # without following a symlink and re-hashed on the way in.
            writer = PrefixWriter(privileged=needs_root_for(self.prefix), runner=self.runner)
            writer.install_verified(path, dest, algorithm="sha256", digest=artifact.sha256)
            return f"installed {dest} ({human_size(artifact.size)}, mode 0644, sha256 re-verified)"
        raise BackendError(  # pragma: no cover
            "archive artifacts are staged and installed through _stage and the tree commands"
        )

    def _stage(self, artifact: DataArtifact, fetched: dict[str, Path], staged: Path) -> str:
        """Unpack the archive into *staged*, in the operator's scratch, and fix
        its modes there; the copy into the prefix is the plan's next steps."""
        path = fetched.get("path")
        if path is None:  # pragma: no cover
            raise BackendError("the data artifact was not fetched before the install step")
        staged.parent.mkdir(parents=True, exist_ok=True)
        outcome = extract(path, staged, members=artifact.members)
        installed = sorted(p for p in staged.rglob("*") if p.is_file())
        for p in installed:
            # Semgrep: a deliberate mode (0755/0644 on installed files and launchers, 0700 private); nothing group- or world-writable.
            # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
            os.chmod(p, 0o644)
        for d in (p for p in staged.rglob("*") if p.is_dir()):
            # Semgrep: a deliberate mode (0755/0644 on installed files and launchers, 0700 private); nothing group- or world-writable.
            # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
            os.chmod(d, 0o755)
        # Semgrep: a deliberate mode (0755/0644 on installed files and launchers, 0700 private); nothing group- or world-writable.
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
        os.chmod(staged, 0o755)
        return f"{outcome}; {len(installed)} file(s) staged under {staged}"

    def _tree_commands(self, name: str, staged: Path, dest: Path) -> list[Command]:
        """Put the staged tree at *dest* the way the tree backends do (D-043):
        privileged when the prefix is root's, as the operator when it is theirs."""
        privileged = needs_root_for(self.prefix)
        how = "" if privileged else " (your own prefix, no root needed)"
        commands = [
            Command(
                argv=("rm", "-rf", "--", str(dest)),
                description=f"Clear any previous {name} data at {dest}{how}",
                requires_root=privileged,
            ),
            Command(
                argv=("install", "-d", str(dest.parent)),
                description=f"Ensure {dest.parent} exists{how}",
                requires_root=privileged,
            ),
            Command(
                argv=("cp", "-aT", "--no-preserve=ownership", str(staged), str(dest)),
                description=f"Install the staged {name} data into {dest}{how}",
                requires_root=privileged,
            ),
        ]
        if privileged and self.owner:
            # -h never follows a symlink: a link pointing outside the tree
            # cannot hand root's files to the operator (D-043).
            commands.append(
                Command(
                    argv=("chown", "-R", "-h", "--", f"{self.owner}:", str(dest)),
                    description=f"Hand the {name} data tree to {self.owner}",
                    requires_root=True,
                )
            )
        commands.append(
            Command(
                argv=("rm", "-rf", "--", str(staged)),
                description=f"Delete the staged copy of the {name} data",
            )
        )
        return commands
