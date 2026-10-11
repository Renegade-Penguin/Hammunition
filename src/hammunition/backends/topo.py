# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The topo-quads backend: USGS US Topo map sheets for the station's
regions.  D-068.

The sheets come from :func:`hammunition.topo_plan.resolve_topo`, run before
the plan prints, because each sheet's size and how it is verified are the
disclosure. Each is fetched into the shared cache, checked against the S3
ETag the carried index lists and the bucket confirmed at plan time
(:meth:`hammunition.fetch.Fetcher.fetch_etag`), installed as
``<data>/<unit>/<stem>_<date>.tif`` re-verified on the way in against the
sha256 measured as it arrived, and its cached copy deleted: a sheet's
object never changes (a new edition is a new name), so a second copy is
only disk.

``<data>/<unit>/<slug>.quads`` records which sheets a region needs -- the
answer from its outline, kept so a later plan needs no network to know it.
A sheet no region needs any more is removed, and so is the record of a
region no longer set; a kept region's (offline) are not.

One sheet failing does not stop the others: it is recorded in D-061's
:class:`~hammunition.backends.terrain.TerrainLedger`, which fails the
transaction naming it at the end. Nothing here is ever executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from ..fetch import Fetcher, MirrorPath, fetch_disclosure, record_fetch
from ..manifest.schema import PackageManifest, TopoQuadsInstall
from ..topo_bound import bound_line, split_bound
from ..ustopo import Quad, UstopoError, parse_row, render_row
from .base import Action, BackendError, Command, CommandRunner
from .data import human_size
from .regions import data_root, prefix_writer, removal_steps
from .terrain import TerrainLedger
from .topo_common import QUADS as QUADS  # re-exported: callers import these from here
from .topo_common import TIF as TIF
from .topo_common import quad_key as quad_key
from .topo_common import replaced_steps as replaced_steps
from .topo_common import stem_of as stem_of
from .verified import PrefixWriter

if TYPE_CHECKING:
    from .fstopo import FsTopoBackend

_HEADER = "# US Topo quads: "


def no_quads_line(region: str) -> str:
    """How a region no US Topo sheet covers is named, in the plan and its step."""
    return f"no US Topo quad covers {region} (US Topo covers the United States and its territories)"


@dataclass(frozen=True)
class RegionQuads:
    """The sheets one region needs, sorted by index path."""

    region: str
    slug: str
    quads: tuple[Quad, ...]
    bound: str = "all"
    """What the selection was made under (:attr:`TopoBound.token`): ``all`` for
    the whole region, a digest of the circle, or ``none`` (issue #232)."""


def render_record(entry: RegionQuads) -> str:
    """The region's sheets as whole index rows, box, size and ETag included,
    so an offline plan can warp and verify from the record alone even after
    the carried index has moved on to a newer edition."""
    return (
        f"{_HEADER}{len(entry.quads)}\n"
        + bound_line(entry.bound)
        + "".join(f"{render_row(q)}\n" for q in entry.quads)
    )


def read_record(path: Path, region: str, slug: str) -> RegionQuads | None:
    """A region's recorded sheets, or None when there is no readable record.

    A region no sheet covers records a header and nothing else: a complete
    record, or its outline would be fetched again on every plan. A record
    whose count does not match its rows, or with a row that is not an index
    row, is not trusted."""
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
    except UstopoError:
        return None
    return RegionQuads(region, slug, tuple(sorted(quads, key=lambda q: q.path)), bound)


@dataclass(frozen=True)
class TopoResolution:
    """The station's US Topo sheets, resolved at plan time."""

    regions: tuple[RegionQuads, ...] = ()
    fetch: tuple[Quad, ...] = ()
    """Sheets not installed yet: downloaded this run."""
    current: tuple[Quad, ...] = ()
    """Sheets already installed; nothing happens to them."""
    deferred: tuple[Quad, ...] = ()
    """Sheets a region needs that this run does not fetch because their
    publisher is not answering (#200). Not downloaded and not in :attr:`quads`;
    named in :attr:`wanted` so that an older edition already installed is
    neither removed nor dropped from the map for want of its successor."""

    @property
    def quads(self) -> tuple[Quad, ...]:
        """Every sheet any region needs, by path."""
        found = {q.path: q for q in (*self.fetch, *self.current)}
        return tuple(found[path] for path in sorted(found))

    @property
    def wanted(self) -> tuple[Quad, ...]:
        """:attr:`quads` and the deferred sheets: what the maps are built over
        and what removal must not touch."""
        found = {q.path: q for q in (*self.fetch, *self.current, *self.deferred)}
        return tuple(found[path] for path in sorted(found))


@dataclass(frozen=True)
class TopoDisclosure:
    """What the plan says about the US Topo sheets (D-068)."""

    resolution: TopoResolution
    licence: str
    licence_url: str
    warp: tuple[Quad, ...] = ()
    """Sheets ``ustopo-mosaic`` warps this run."""
    building: bool = False
    """Whether ``ustopo-mosaic`` has anything to do this run: a warp, a
    removal, or the VRT rebuilt because the set of sheets changed."""
    selection: str = ""
    """The station's bound in words (:meth:`TopoBound.describe`); empty when
    the disclosure was not built under one."""
    everything: bool = False
    """Whether ``--topo-all`` chose every sheet of every region."""


@dataclass(frozen=True)
class TopoQuadsBackend:
    """Turns a ``topo-quads`` block and the resolved sheets into steps."""

    fetcher: Fetcher
    prefix: Path
    resolution: TopoResolution
    keep: frozenset[str] = frozenset()
    """Slugs kept as installed because a newer map could not be checked for."""
    ledger: TerrainLedger = field(default_factory=TerrainLedger)
    runner: CommandRunner | None = None
    euid: int | None = None
    privileged: bool | None = None
    fstopo: FsTopoBackend | None = None
    """The Forest Service's sheets: a ``usfs-fstopo`` block is handed to it
    (D-068, amended 2026-10-01), so ``execute.commands_for`` keeps one ``topo``."""
    method = "topo-quads"

    @property
    def writer(self) -> PrefixWriter:
        return prefix_writer(self.prefix, self.privileged, self.runner, self.euid)

    def data_dir(self, manifest: PackageManifest) -> Path:
        return data_root(self.prefix) / manifest.name

    def steps(self, manifest: PackageManifest, block: TopoQuadsInstall) -> list[Action | Command]:
        if block.provider == "usfs-fstopo":
            if self.fstopo is None:
                raise BackendError(
                    f"{manifest.name} installs usfs-fstopo sheets and no FSTopo backend was "
                    f"supplied. Skipping it would report a successful run that installed "
                    f"nothing."
                )
            return self.fstopo.steps(manifest, block)
        out = self.data_dir(manifest)
        writer = self.writer
        steps: list[Action | Command] = []
        for quad in self.resolution.fetch:
            fetched: dict[str, str | Path] = {}
            facts: dict[str, str] = {}
            # The Bunker's name for a sheet is its state path, <ST>/<stem>_<date>
            # (the index's own key, the one the offline plan resolves by).
            where = MirrorPath(manifest.name, quad.path)
            note, urls, sources = fetch_disclosure(self.fetcher, quad.url, where, "ETag")
            steps.append(
                Action(
                    kind="fetch",
                    description=(
                        f"Fetch US Topo quad {quad.name} ({human_size(quad.size)}, "
                        f"{block.licence}) — {quad.verified_by}{note}"
                    ),
                    detail=f"{urls} (ETag {quad.etag}, {quad.size} bytes)",
                    perform=partial(self._fetch, quad, fetched, where, facts),
                    sources=sources,
                    facts=facts,
                )
            )
            dest = out / f"{quad.name}{TIF}"
            steps.append(
                Action(
                    kind="install-data",
                    description=(
                        f"Install US Topo quad {quad.name}, then delete its cached copy "
                        f"{self.fetcher.etag_path_for(quad.url, quad.etag)}"
                    ),
                    # The destination, verbatim, for uninstall's attribution replay.
                    detail=str(dest),
                    perform=partial(self._install, quad, fetched, dest, writer),
                    requires_root=writer.privileged,
                    facts={"size": str(quad.size)},
                )
            )
        for entry in self.resolution.regions:
            record = out / f"{entry.slug}{QUADS}"
            if read_record(record, entry.region, entry.slug) == entry:
                continue
            description = (
                f"Record that {no_quads_line(entry.region)}, so nothing is installed for it"
                if not entry.quads
                else f"Record the {len(entry.quads)} US Topo quad(s) {entry.region} needs, "
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
        old, replacing = replaced_steps(out, wanted, writer, "US Topo quad")
        steps.extend(removal_steps(out, TIF, set(wanted) | old, writer))
        steps.extend(replacing)
        return steps

    @staticmethod
    def _record(entry: RegionQuads, record: Path, writer: PrefixWriter) -> str:
        writer.write_text(record, render_record(entry))
        if not entry.quads:
            return f"wrote {record}; {no_quads_line(entry.region)}"
        return f"wrote {record}"

    def _fetch(
        self,
        quad: Quad,
        fetched: dict[str, str | Path],
        where: MirrorPath | None = None,
        facts: dict[str, str] | None = None,
    ) -> str:
        try:
            result = self.fetcher.fetch_etag(
                quad.url, quad.etag, expected_size=quad.size, mirror=where
            )
        except (BackendError, OSError) as exc:
            return self.ledger.fail(quad_key(quad.name), f"{quad.name}: {exc}")
        fetched["path"] = result.path
        fetched["sha256"] = result.sha256
        source = record_fetch(
            result, facts if facts is not None else {}, mirrored=bool(self.fetcher.mirror)
        )
        state = "cached" if result.from_cache else "downloaded"
        return (
            f"{state} {result.size} bytes, ETag {quad.etag} reproduced "
            f"(the publisher's, not pinned){source}"
        )

    def _install(
        self, quad: Quad, fetched: dict[str, str | Path], dest: Path, writer: PrefixWriter
    ) -> str:
        path, sha256 = fetched.get("path"), fetched.get("sha256")
        if quad_key(quad.name) in self.ledger.failed or not isinstance(path, Path):
            return f"skipped: {quad.name} did not verify"
        try:
            # Re-verified on the way in against the sha256 taken as the bytes
            # arrived, after the ETag matched: the cache is the operator's.
            writer.install_verified(path, dest, algorithm="sha256", digest=str(sha256))
        except (BackendError, OSError) as exc:
            return self.ledger.fail(quad_key(quad.name), f"{quad.name}: {exc}")
        path.unlink(missing_ok=True)
        return f"installed {dest} ({human_size(quad.size)}); deleted the cached copy"
