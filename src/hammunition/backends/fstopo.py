# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The FSTopo half of the topo-quads backend: the Forest Service's sheets
for the station's regions.  D-068, amended 2026-10-01.

The sheets come from :func:`hammunition.topo_plan.resolve_fstopo`, run
before the plan prints, because each sheet's size and how it is checked are
the disclosure. A sheet with a pin is fetched against the sha256 the
maintainer measured (:meth:`Fetcher.fetch`); any other through
:meth:`Fetcher.fetch_sized`, which checks only the size the gateway
announced and that the bytes are a TIFF -- **unverified**, and the step
says so by name. Either way it is installed as ``<data>/<unit>/<name>.tif``,
re-verified on the way in against the sha256 measured as it arrived, and
its cached copy deleted.

``<slug>.quads`` records a region's sheets as whole index rows, as US
Topo's records do; a sheet no region needs is removed, and an older vintage
only once its newer one is on disk (:func:`hammunition.backends.topo.replaced_steps`).
Failures go into D-061's terrain ledger; one sheet failing does not stop
the others. Nothing here is ever executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from ..fetch import Fetcher, MirrorPath, fetch_disclosure, record_fetch
from ..fstopo import FsQuad, FsQuadFile, FstopoError, parse_row, render_row
from ..manifest.schema import PackageManifest, RemoteArtifact, TopoQuadsInstall
from ..topo_bound import bound_line, split_bound
from .base import Action, BackendError, Command, CommandRunner
from .data import human_size
from .regions import MIB, data_root, prefix_writer, removal_steps
from .terrain import TerrainLedger
from .topo_common import QUADS, TIF, quad_key, replaced_steps
from .verified import PrefixWriter

_HEADER = "# FSTopo quads: "


def no_sheets_line(region: str) -> str:
    """How a region no FSTopo sheet covers is named, in the plan and its step."""
    return f"no FSTopo quad covers {region} (FSTopo covers National Forest land)"


@dataclass(frozen=True)
class RegionSheets:
    """The FSTopo sheets one region needs, by secoord."""

    region: str
    slug: str
    quads: tuple[FsQuad, ...]
    bound: str = "all"
    """What the selection was made under (:attr:`TopoBound.token`)."""


def render_record(entry: RegionSheets) -> str:
    return (
        f"{_HEADER}{len(entry.quads)}\n"
        + bound_line(entry.bound)
        + "".join(f"{render_row(q)}\n" for q in entry.quads)
    )


def read_record(path: Path, region: str, slug: str) -> RegionSheets | None:
    """A region's recorded sheets, or None when there is no trustworthy record."""
    try:
        text = path.read_text()
    except OSError:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith(_HEADER):
        return None
    count = lines[0][len(_HEADER) :]
    bound, rows = split_bound(lines[1:])
    if not count.isdigit() or int(count) != len(rows):
        return None
    try:
        quads = [parse_row(row, number) for number, row in enumerate(rows, 2)]
    except FstopoError:
        return None
    return RegionSheets(region, slug, tuple(sorted(quads, key=lambda q: q.secoord)), bound)


@dataclass(frozen=True)
class FsTopoResolution:
    """The station's FSTopo sheets, resolved at plan time."""

    regions: tuple[RegionSheets, ...] = ()
    fetch: tuple[FsQuadFile, ...] = ()
    """Sheets not installed yet: downloaded this run."""
    current: tuple[FsQuad, ...] = ()
    """Sheets already installed; nothing happens to them."""
    pinned: frozenset[int] = frozenset()
    """The secoords among the regions' sheets with a pin in the catalog."""
    deferred: tuple[FsQuad, ...] = ()
    """Sheets a region needs that this run does not fetch because the publisher
    is not answering (#200); see :attr:`TopoResolution.deferred`."""

    def all_pinned(self, entry: RegionSheets) -> bool:
        """Whether every sheet *entry* needs is pinned (the condition for the
        unit to rejoin a profile, D-068 amended 2026-10-01)."""
        return bool(entry.quads) and all(q.secoord in self.pinned for q in entry.quads)

    @property
    def quads(self) -> tuple[FsQuad, ...]:
        found = {q.secoord: q for q in (*(f.quad for f in self.fetch), *self.current)}
        return tuple(found[s] for s in sorted(found))

    @property
    def wanted(self) -> tuple[FsQuad, ...]:
        """:attr:`quads` and the deferred sheets (see :attr:`TopoResolution.wanted`)."""
        found = {
            q.secoord: q for q in (*(f.quad for f in self.fetch), *self.current, *self.deferred)
        }
        return tuple(found[s] for s in sorted(found))


@dataclass(frozen=True)
class FsTopoDisclosure:
    """What the plan says about the FSTopo sheets (D-068, amended 2026-10-01)."""

    resolution: FsTopoResolution
    licence: str
    licence_url: str
    convert: tuple[int, ...] = ()
    """The sheet size of each sheet ``ustopo-mosaic`` converts this run."""
    building: bool = False
    """Whether the FSTopo half of ``ustopo-mosaic`` has anything to do."""


@dataclass(frozen=True)
class FsTopoBackend:
    """Turns a ``topo-quads`` block of provider ``usfs-fstopo`` into steps."""

    fetcher: Fetcher
    prefix: Path
    resolution: FsTopoResolution
    keep: frozenset[str] = frozenset()
    ledger: TerrainLedger = field(default_factory=TerrainLedger)
    runner: CommandRunner | None = None
    euid: int | None = None
    privileged: bool | None = None

    @property
    def writer(self) -> PrefixWriter:
        return prefix_writer(self.prefix, self.privileged, self.runner, self.euid)

    def data_dir(self, manifest: PackageManifest) -> Path:
        return data_root(self.prefix) / manifest.name

    def cache_path(self, sheet: FsQuadFile) -> Path:
        if sheet.sha256:
            return self.fetcher.path_for(RemoteArtifact(url=sheet.url, sha256=sheet.sha256))
        return self.fetcher.sized_path_for(sheet.url, sheet.size)

    def steps(self, manifest: PackageManifest, block: TopoQuadsInstall) -> list[Action | Command]:
        out = self.data_dir(manifest)
        writer = self.writer
        steps: list[Action | Command] = []
        for sheet in self.resolution.fetch:
            fetched: dict[str, str | Path] = {}
            facts: dict[str, str] = {}
            where = MirrorPath(manifest.name, sheet.name)
            # The sheet's check is its pin (sha256 and size) or, unpinned, its size
            # and a TIFF's first bytes; the Bunker's copy meets the same one.
            note, urls, sources = fetch_disclosure(
                self.fetcher, sheet.url, where, "sha256" if sheet.sha256 else "size"
            )
            steps.append(
                Action(
                    kind="fetch",
                    description=(
                        f"Fetch FSTopo quad {sheet.name} ({human_size(sheet.size)}, "
                        f"{block.licence}) — {sheet.verified_by}{note}"
                    ),
                    detail=f"{urls} ({sheet.size} bytes)",
                    perform=partial(self._fetch, sheet, fetched, where, facts),
                    sources=sources,
                    facts=facts,
                )
            )
            dest = out / f"{sheet.name}{TIF}"
            steps.append(
                Action(
                    kind="install-data",
                    description=(
                        f"Install FSTopo quad {sheet.name}, then delete its cached copy "
                        f"{self.cache_path(sheet)}"
                    ),
                    detail=str(dest),
                    perform=partial(self._install, sheet, fetched, dest, writer),
                    requires_root=writer.privileged,
                    facts={"size": str(sheet.size)},
                )
            )
        for entry in self.resolution.regions:
            record = out / f"{entry.slug}{QUADS}"
            if read_record(record, entry.region, entry.slug) == entry:
                continue
            description = (
                f"Record that {no_sheets_line(entry.region)}, so nothing is installed for it"
                if not entry.quads
                else f"Record the {len(entry.quads)} FSTopo quad(s) {entry.region} needs, "
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
        steps.extend(removal_steps(out, QUADS, slugs, writer))
        wanted = [q.name for q in self.resolution.wanted]
        old, replacing = replaced_steps(out, wanted, writer, "FSTopo quad")
        steps.extend(removal_steps(out, TIF, set(wanted) | old, writer))
        steps.extend(replacing)
        return steps

    @staticmethod
    def _record(entry: RegionSheets, record: Path, writer: PrefixWriter) -> str:
        writer.write_text(record, render_record(entry))
        if not entry.quads:
            return f"wrote {record}; {no_sheets_line(entry.region)}"
        return f"wrote {record}"

    def _fetch(
        self,
        sheet: FsQuadFile,
        fetched: dict[str, str | Path],
        where: MirrorPath | None = None,
        facts: dict[str, str] | None = None,
    ) -> str:
        try:
            if sheet.sha256:
                result = self.fetcher.fetch(
                    RemoteArtifact(url=sheet.url, sha256=sheet.sha256),
                    max_bytes=sheet.size + MIB,
                    mirror=where,
                )
                how = f"sha256 {sheet.sha256[:12]}… verified against the pin"
            else:
                result = self.fetcher.fetch_sized(sheet.url, expected_size=sheet.size, mirror=where)
                how = (
                    f"unverified: {result.size} bytes as announced and a TIFF, sha256 "
                    f"{result.sha256[:12]}… recorded"
                )
            if result.size != sheet.size:
                raise BackendError(
                    f"{sheet.url}: {sheet.size} bytes were expected and {result.size} arrived"
                )
        except (BackendError, OSError) as exc:
            return self.ledger.fail(quad_key(sheet.name), f"{sheet.name}: {exc}")
        fetched["path"] = result.path
        fetched["sha256"] = result.sha256
        source = record_fetch(
            result, facts if facts is not None else {}, mirrored=bool(self.fetcher.mirror)
        )
        state = "cached" if result.from_cache else "downloaded"
        return f"{state} {result.size} bytes, {how}{source}"

    def _install(
        self, sheet: FsQuadFile, fetched: dict[str, str | Path], dest: Path, writer: PrefixWriter
    ) -> str:
        path, sha256 = fetched.get("path"), fetched.get("sha256")
        if quad_key(sheet.name) in self.ledger.failed or not isinstance(path, Path):
            return f"skipped: {sheet.name} did not arrive"
        try:
            writer.install_verified(path, dest, algorithm="sha256", digest=str(sha256))
        except (BackendError, OSError) as exc:
            return self.ledger.fail(quad_key(sheet.name), f"{sheet.name}: {exc}")
        path.unlink(missing_ok=True)
        return f"installed {dest} ({human_size(sheet.size)}); deleted the cached copy"
