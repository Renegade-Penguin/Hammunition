# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The phone converters for one install run, and what they cost on disk.  D-067.

Two converters share one :class:`~hammunition.backends.mapsforge.PhoneLedger`,
each stages in its own directory under the operator's build tree, and both
build from the regions piece 1 resolved for the run. Their disk needs are
added to piece 1's and piece 2's before anything is fetched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .backends.base import CommandRunner
from .backends.derived import Converter
from .backends.mapsforge import SCRATCH, Kind, MapsforgeConverter, PhoneLedger, estimate
from .backends.regions import MapLedger
from .backends.staging import Staging
from .fetch import Fetcher
from .geofabrik import RegionFile
from .manifest.schema import DerivedDataInstall
from .plan import InstallPlan

if TYPE_CHECKING:
    from .resolution import ResolutionContext


@dataclass(frozen=True)
class PhoneRun:
    """The phone converters for one run, sharing one :class:`PhoneLedger`."""

    ledger: PhoneLedger
    map: MapsforgeConverter
    poi: MapsforgeConverter

    @property
    def converters(self) -> dict[str, Converter]:
        return {"mapsforge-map": self.map, "mapsforge-poi": self.poi}

    def idle(self, plan: InstallPlan) -> frozenset[str]:
        """The phone units in *plan* with nothing to build or fetch this run."""
        out: set[str] = set()
        for planned in plan.packages:
            block = planned.block.install
            if not isinstance(block, DerivedDataInstall):
                continue
            conv = self.converters.get(block.converter)
            if not isinstance(conv, MapsforgeConverter):
                continue
            if not conv.pending(planned.manifest, block) and conv.tool_current(
                planned.manifest, block
            ):
                out.add(planned.name)
        return frozenset(out)

    def needs(self, plan: InstallPlan, *, cache: Path, prefix: Path) -> dict[Path, int]:
        """Bytes each location needs this run: the largest build's scratch in
        each converter's staging (they run one region at a time and each is
        cleared before the next), every output under the prefix, and the POI
        writer in the cache and under the prefix when it is not installed."""
        needs: dict[Path, int] = {}

        def add(where: Path, amount: float) -> None:
            if amount:
                needs[where] = needs.get(where, 0) + round(amount)

        for planned in plan.packages:
            block = planned.block.install
            if not isinstance(block, DerivedDataInstall):
                continue
            conv = self.converters.get(block.converter)
            if not isinstance(conv, MapsforgeConverter):
                continue
            kind: Kind = conv.kind
            sizes = [f.size for f in conv.pending(planned.manifest, block)]
            add(conv.staging.directory, SCRATCH[kind] * max(sizes, default=0))
            add(prefix, sum(estimate(kind, size) for size in sizes))
            if block.tool is not None and not conv.tool_current(planned.manifest, block):
                add(cache, block.tool.size)
                add(prefix, block.tool.size)
        return needs


def build_phone_run(
    *,
    prefix: Path,
    builds: Path,
    owner: str | None,
    runner: CommandRunner | None,
    fetcher: Fetcher,
    files: Sequence[RegionFile],
    keep: frozenset[str],
    regions: MapLedger,
    context: ResolutionContext | None = None,
) -> PhoneRun:
    """Both phone converters for one run, each staging as the operator."""
    ledger = PhoneLedger()

    def converter(kind: Kind) -> MapsforgeConverter:
        return MapsforgeConverter(
            kind=kind,
            prefix=prefix,
            files=files,
            staging=Staging(builds / f"mapsforge-{kind}", owner=owner),
            fetcher=fetcher,
            keep=keep,
            regions=regions,
            ledger=ledger,
            runner=runner,
            context=context,
        )

    return PhoneRun(ledger=ledger, map=converter("map"), poi=converter("poi"))
