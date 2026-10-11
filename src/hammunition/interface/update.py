# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``update`` as data.  D-059, D-053.

The report is already a dataclass (:class:`hammunition.update.UpdateReport`),
and the text keeps rendering from it; this document is built from the same
instance. Rows carry what the text rows carry and no more, so the count-only
rule the text follows for anything that says where the operator is (the
`osm-regions` row #121 adds) holds here too, without a special case.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from hammunition.distro import Target
from hammunition.interface.envelope import Strict, TargetView, described, target_view
from hammunition.update import (
    BEHIND_PIN,
    CANDIDATE_DIFFERS,
    MANUAL,
    NOT_INSTALLED,
    ON_INSTALL,
    RETIRED,
    UNKNOWN,
    UP_TO_DATE,
    UpdateReport,
    rebuild_command,
    upgrade_command,
)
from hammunition.upstream import UpstreamRow

__all__ = ["UpdateDocument", "build_update"]


@dataclass(frozen=True)
class UpdateRowView(Strict):
    """One unit: installed versus the catalog."""

    unit: str = described("the catalog unit")
    state: str = described(
        "`up to date`, `candidate differs`, `behind the pin`, `not installed`, `unknown`, "
        "`re-checked on install`, `manual` or `retired`"
    )
    detail: str = described("what was compared, as the text prints it")
    strategy: str = described("the manifest's update strategy")
    upgradable: tuple[str, ...] = described("apt packages whose candidate differs")


@dataclass(frozen=True)
class UpdateCounts(Strict):
    """How many rows are in each state."""

    up_to_date: int = described("up to date")
    candidate_differs: int = described("apt would change them on its next upgrade")
    behind_pin: int = described("built at an earlier pin, or never verified here")
    not_installed: int = described("not on this machine")
    unknown: int = described("nothing on disk can be checked")
    on_install: int = described("resolved again on every install")
    manual: int = described("re-pinned by hand")
    retired: int = described("catalog units retained as retired")


@dataclass(frozen=True)
class UpstreamRowView(Strict):
    """The catalog's pin against what upstream publishes (`--upstream` only)."""

    unit: str = described("the catalog unit")
    method: str = described("the probe used")
    catalog: str = described("the catalog's pin")
    upstream: str | None = described("what upstream publishes; null when it could not be read")
    state: str = described("the verdict")
    detail: str = described("what was found")


@dataclass(frozen=True)
class UpdateDocument(Strict):
    """Installed versus the catalog, as a report. Nothing runs (D-053)."""

    KIND: ClassVar[str] = "update"

    target: TargetView = described("the system")
    from_log: bool = described("the units compared are every unit the transaction log names")
    rows: tuple[UpdateRowView, ...] = described("one per unit compared")
    counts: UpdateCounts = described("rows per state")
    lists_note: str = described("how old the local apt lists are; the report compares against them")
    upgrade_command: str | None = described("takes apt's differing candidates; null when none")
    rebuild_command: str | None = described("rebuilds every unit behind the pin; null when none")
    upstream_declared: tuple[str, ...] = described("units whose probe would ask upstream")
    upstream: tuple[UpstreamRowView, ...] | None = described(
        "the upstream comparison; null unless `--upstream` asked for it"
    )
    offline: str | None = field(
        default=None,
        metadata={
            "doc": "present under `--offline`: that nothing was fetched, which Bunker and "
            "catalogue serial were verified, and whether the local trust state advanced; "
            "null otherwise"
        },
    )


def build_update(
    target: Target,
    report: UpdateReport,
    *,
    lists_note: str,
    from_log: bool,
    upstream: Sequence[UpstreamRow] | None,
    offline: str | None = None,
) -> UpdateDocument:
    return UpdateDocument(
        target=target_view(target),
        from_log=from_log,
        rows=tuple(
            UpdateRowView(
                unit=r.unit,
                state=r.state,
                detail=r.detail,
                strategy=r.strategy,
                upgradable=tuple(r.upgradable),
            )
            for r in report.rows
        ),
        counts=UpdateCounts(
            up_to_date=report.count(UP_TO_DATE),
            candidate_differs=report.count(CANDIDATE_DIFFERS),
            behind_pin=report.count(BEHIND_PIN),
            not_installed=report.count(NOT_INSTALLED),
            unknown=report.count(UNKNOWN),
            on_install=report.count(ON_INSTALL),
            manual=report.count(MANUAL),
            retired=report.count(RETIRED),
        ),
        lists_note=lists_note,
        upgrade_command=upgrade_command(report),
        rebuild_command=rebuild_command(report),
        upstream_declared=tuple(report.upstream_declared),
        upstream=tuple(
            UpstreamRowView(
                unit=r.unit,
                method=r.method,
                catalog=r.catalog,
                upstream=r.upstream,
                state=r.state,
                detail=r.detail,
            )
            for r in upstream
        )
        if upstream is not None
        else None,
        offline=offline,
    )
