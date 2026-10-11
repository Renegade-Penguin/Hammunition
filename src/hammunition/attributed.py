# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which installed data items the plan asks their publisher about again.
#197, D-049 (amended 2026-10-02).

A data item (a terrain or 3DEP tile, a US Topo or FSTopo sheet, a Kiwix book, a
CoMaps map) the log attributes as installed by the engine is not re-asked of
its publisher at plan time while its attribution is younger than
:data:`RECHECK_AFTER_DAYS`; the real run still verifies everything it fetches.
Past that age, or under ``install --recheck``, the publisher is asked again.

That re-check **never refuses and never defers**: the item is installed. A
publisher that no longer serves it as pinned, or does not answer, becomes a
line in the plan and the installed copy is kept; fetching a replacement is the
operator's decision (remove it, then install).

An item on disk that the log does not attribute (copied in by hand, or
installed before the log recorded it) keeps its earlier behaviour: its file
counts, and nothing is asked. An attributed item whose file is not the one the
log recorded (a different size, or a pin that has since moved) is not trusted:
it is asked again.

The attribution is read through :meth:`TransactionLog.read`, which walks the
rotated archives in order (D-077), so a rotation does not lose it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar

from .progress import run_checks

#: An attribution older than this many days is asked of the publisher again.
RECHECK_AFTER_DAYS = 7

T = TypeVar("T")


class _Readable(Protocol):
    def read(self) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class Attribution:
    """The last ``install-data`` the log records for one destination."""

    path: str
    when: datetime
    #: Bytes and digest the install recorded, ``None`` for an entry written
    #: before the log carried them.
    size: int | None = None
    digest: str | None = None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def read_attributions(log: _Readable) -> dict[str, Attribution]:
    """Every destination the log attributes as installed, with when it was
    last installed: an ``install-data`` ``action_end`` attributes its detail
    path and a later ``remove-data`` takes it away, replayed oldest first."""
    found: dict[str, Attribution] = {}
    for entry in log.read():
        if entry.get("event") != "action_end":
            continue
        kind, detail = entry.get("kind"), entry.get("detail")
        if not isinstance(detail, str) or not detail.startswith("/"):
            continue
        if kind == "remove-data":
            found.pop(detail, None)
        elif kind == "install-data":
            when = _timestamp(entry.get("timestamp"))
            if when is None:
                continue
            size = entry.get("size")
            digest = entry.get("digest")
            found[detail] = Attribution(
                detail,
                when,
                int(size) if isinstance(size, (int, str)) and str(size).isdigit() else None,
                digest if isinstance(digest, str) else None,
            )
    return found


@dataclass(frozen=True)
class CheckLine:
    """What the plan did about one installed item: the ``--json`` line."""

    unit: str
    item: str
    checked: bool
    reason: str
    attributed: str | None = None
    failed: bool = False


class PublisherChecks:
    """Decides, per installed item, whether its publisher is asked, and keeps
    what it decided for the plan's text and its ``--json``."""

    def __init__(
        self,
        attributions: Mapping[str, Attribution] | None = None,
        *,
        now: datetime | None = None,
        recheck: bool = False,
        offline: bool = False,
    ) -> None:
        self.attributions = dict(attributions or {})
        self.now = now if now is not None else datetime.now(UTC)
        self.recheck = recheck
        self.offline = offline
        """Offline no publisher is asked about anything, whatever ``recheck`` says."""
        self.lines: list[CheckLine] = []

    @classmethod
    def from_log(
        cls,
        log: _Readable,
        *,
        now: datetime | None = None,
        recheck: bool = False,
        offline: bool = False,
    ) -> PublisherChecks:
        return cls(read_attributions(log), now=now, recheck=recheck, offline=offline)

    def due(self, unit: str, item: str, path: Path, *, digest: str | None = None) -> bool:
        """Whether the item installed at *path* is asked of its publisher.
        Records the decision either way."""
        attribution = self.attributions.get(str(path))
        if self.offline:
            stamp = attribution.when.date().isoformat() if attribution is not None else None
            return self._note(
                unit,
                item,
                False,
                "offline: the installed copy is kept; the publisher is not asked",
                stamp,
            )
        if attribution is None:
            reason = "on disk, not attributed in the log: its file counts, nothing is asked"
            return self._note(unit, item, False, reason, None)
        stamp = attribution.when.date().isoformat()
        if self.recheck:
            return self._note(unit, item, True, "re-checked: --recheck", stamp)
        problem = self._mismatch(attribution, path, digest)
        if problem is not None:
            return self._note(unit, item, True, f"re-checked: {problem}", stamp)
        age = self.now - attribution.when
        # A future-dated attribution (a clock that was wrong) is not trusted either.
        if age >= timedelta(days=RECHECK_AFTER_DAYS) or age < timedelta(0):
            reason = f"re-checked: attributed {stamp}, more than {RECHECK_AFTER_DAYS} days ago"
            return self._note(unit, item, True, reason, stamp)
        return self._note(
            unit, item, False, f"installed, not re-checked (attributed {stamp})", stamp
        )

    @staticmethod
    def _mismatch(attribution: Attribution, path: Path, digest: str | None) -> str | None:
        if attribution.size is not None:
            try:
                size = path.stat().st_size
            except OSError:
                return "the file cannot be read"
            if size != attribution.size:
                return (
                    f"the file is {size} bytes where the log attributed {attribution.size}, "
                    f"so it is not the one the log recorded"
                )
        if digest is not None and attribution.digest is not None and digest != attribution.digest:
            return "the catalog's pin is not the digest the log attributed"
        return None

    def _note(self, unit: str, item: str, checked: bool, reason: str, stamp: str | None) -> bool:
        self.lines.append(CheckLine(unit, item, checked, reason, stamp))
        return checked

    def report(self, unit: str, item: str, error: object | None) -> None:
        """The outcome of an item's re-check: a failure is kept on its line."""
        if error is None:
            return
        for index in range(len(self.lines) - 1, -1, -1):
            line = self.lines[index]
            if line.unit == unit and line.item == item and line.checked:
                self.lines[index] = replace(
                    line,
                    failed=True,
                    reason=f"{line.reason}; the publisher check failed: {error}; "
                    f"the installed copy is kept",
                )
                return

    def mark_cached(self, unit: str, item: str) -> None:
        """The item's re-check was answered from the plan-time HEAD cache
        (#214), not by the publisher: still a check, and the line says so."""
        for index in range(len(self.lines) - 1, -1, -1):
            line = self.lines[index]
            if line.unit == unit and line.item == item and line.checked:
                self.lines[index] = replace(
                    line, reason=f"{line.reason}; answered from the cache of an earlier check"
                )
                return

    @property
    def skipped(self) -> list[CheckLine]:
        return [line for line in self.lines if not line.checked and line.attributed is not None]

    def notes(self) -> list[str]:
        """The plan's lines: how many were not re-checked and the oldest
        attribution among them, then each re-check that failed."""
        out: list[str] = []
        skipped = self.skipped
        if skipped:
            oldest = min(line.attributed for line in skipped if line.attributed)
            out.append(
                f"{len(skipped)} installed data item(s) were not re-checked against their "
                f"publishers: the log attributes them (the oldest, {oldest}) and an "
                f"attribution is trusted for {RECHECK_AFTER_DAYS} days. `hammunition install "
                f"--recheck` asks every publisher; the real run verifies whatever it fetches."
            )
        failed = [line for line in self.lines if line.failed]
        out.extend(f"{line.unit}: {line.item}: {line.reason}" for line in failed)
        return out


def recheck_installed(
    checks: PublisherChecks | None,
    unit: str,
    items: Sequence[T],
    *,
    name: Callable[[T], str],
    path: Callable[[T], Path],
    check: Callable[[T], object],
    label: str,
    digest: Callable[[T], str | None] | None = None,
    probe: object | None = None,
) -> None:
    """Ask the publisher about the installed *items* that are due, through
    :func:`~hammunition.progress.run_checks`. Nothing happens without
    *checks* (a caller that never asked for this behaves as before); an item
    the publisher fails on is reported to *checks* and kept."""
    if checks is None:
        return
    due = [
        item
        for item in items
        if checks.due(unit, name(item), path(item), digest=digest(item) if digest else None)
    ]
    cached: set[str] = set()
    thread_hits = getattr(probe, "thread_hits", None)
    thread_misses = getattr(probe, "thread_misses", None)

    def counted(item: T) -> object:
        # A probe that caches (#214) counts per thread, and run_checks runs
        # each check in one thread: an item whose every HEAD was a cache hit
        # is marked as answered from the cache.
        if thread_hits is None or thread_misses is None:
            return check(item)
        hits, misses = thread_hits(), thread_misses()
        try:
            return check(item)
        finally:
            if thread_hits() > hits and thread_misses() == misses:
                cached.add(name(item))

    outcomes = run_checks(due, counted, label=label)
    for item, outcome in zip(due, outcomes, strict=True):
        checks.report(unit, name(item), outcome.error)
        if name(item) in cached and outcome.error is None:
            checks.mark_cached(unit, name(item))
