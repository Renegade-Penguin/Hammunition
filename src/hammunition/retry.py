# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""One request is not a measurement: the plan's publisher probes retry (#200).

A plan asks Geofabrik for an outline, a USGS bucket for each sheet, Kiwix for
each book, and a publisher that answers 502 or drops a connection once is
answering as publishers do. Treating that as final refused a whole
``navigation`` install over three requests that passed a minute later.

The policy, in one place (:class:`RetryPolicy`):

* HTTP 5xx, HTTP 429, a connection error and a read timeout are tried up to
  :data:`ATTEMPTS` times, waiting :data:`DELAYS` (1 s, 3 s, 9 s) between, and
  each retry says so on stderr through :func:`hammunition.progress.say`,
  naming the host and the attempt.
* Any other 4xx is final at once: a 404 is the publisher's answer about a
  listed sheet, and it is the stale-index remedy's business, not ours.
* After the last attempt the probe raises :class:`PublisherUnavailable`
  (an ``OSError``, so every resolver that already catches ``OSError`` for
  "could not be reached" sees it) carrying the publisher's last answer.
  Whether that refuses the plan or defers the item is the caller's
  (D-035, D-039): see :class:`Outages`.
* A host whose probes keep failing at the connection level is not waited on
  for every remaining item: after :data:`BREAKER` exhausted probes in a row
  to one host, the rest of the run asks it once each, without backoff. An
  offline plan therefore costs seconds, not minutes.

Plan-time probes are shared across the workers of
:func:`hammunition.progress.run_checks`; the policy's state is behind a lock.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from hammunition.catalogue import PublisherUnavailable as PublisherUnavailable

from .progress import say

__all__ = [
    "ATTEMPTS",
    "BREAKER",
    "DELAYS",
    "OUTAGE_HINT",
    "POLICY",
    "OnOutage",
    "Outage",
    "Outages",
    "PublisherUnavailable",
    "RetryPolicy",
    "RetryingProbe",
    "hint_for",
    "reporter_for",
    "retrying_head",
    "transient_answer",
]

#: Tries per probe, the first included.
ATTEMPTS = 3
#: Seconds waited before the second and third tries.
DELAYS: tuple[float, ...] = (1.0, 3.0, 9.0)
#: Exhausted connection-level probes in a row to one host before it is no
#: longer waited on for the rest of the run.
BREAKER = 3

T = TypeVar("T")

#: What a resolver calls with an item it could not resolve for an outage.
OnOutage = Callable[[str, "PublisherUnavailable"], None]

#: Appended to a refusal that includes an outage, so a 503 is never read as a
#: stale index.
OUTAGE_HINT = (
    "A publisher that is not answering is not a stale index: run the same command "
    "again in a few minutes."
)


def hint_for(refused: Sequence[str]) -> str:
    """:data:`OUTAGE_HINT` on its own line when any refusal is an outage."""
    return f"\n{OUTAGE_HINT}" if any("not answering right now" in r for r in refused) else ""


def transient_answer(exc: BaseException) -> str | None:
    """What a *retryable* failure said, or None when the failure is final.

    Walks the ``__cause__`` chain because the probes wrap the transport's
    exception in their own (``GeofabrikError`` from ``HTTPError``): the
    wrapper names the URL, the cause is the fact. An HTTP status is retryable
    at 5xx and 429; any other status is final. A timeout, a refused or reset
    connection and a name that did not resolve are retryable. An exception
    with no transport cause (a probe refusing a URL it is not allowed to ask)
    is final."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, PublisherUnavailable):
            return cur.answer
        if isinstance(cur, urllib.error.HTTPError):
            if cur.code >= 500 or cur.code == 429:
                return f"HTTP {cur.code} {cur.reason}".strip()
            return None
        if isinstance(cur, ssl.SSLCertVerificationError | ssl.CertificateError):
            return None  # a certificate that does not verify is not an outage
        if isinstance(cur, TimeoutError | socket.timeout):
            return "timed out"
        if isinstance(cur, urllib.error.URLError):
            reason = cur.reason
            if isinstance(reason, ssl.SSLCertVerificationError | ssl.CertificateError):
                return None
            if isinstance(reason, TimeoutError | socket.timeout):
                return "timed out"
            return f"connection failed: {reason}"
        if isinstance(cur, OSError):
            return f"connection failed: {cur}"
        cur = cur.__cause__
    return None


def _status_of(result: Any) -> int | None:
    if isinstance(result, bool):  # pragma: no cover - not a probe answer
        return None
    if isinstance(result, int):
        return result
    if isinstance(result, tuple) and result and isinstance(result[0], int):
        return result[0]
    return None


def _retryable_status(status: int | None) -> bool:
    return status is not None and (status >= 500 or status == 429)


@dataclass
class RetryPolicy:
    """The retry constants, an injectable sleep, and the per-host breaker."""

    attempts: int = ATTEMPTS
    delays: Sequence[float] = DELAYS
    breaker: int = BREAKER
    sleep: Callable[[float], None] = time.sleep
    notify: Callable[[str], None] = say
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _failures: dict[str, int] = field(default_factory=dict, repr=False)

    def reset(self) -> None:
        """Forget which hosts were given up on: one plan, one run."""
        with self._lock:
            self._failures.clear()

    def call(self, url: str, fn: Callable[[], T]) -> T:
        host = urllib.parse.urlsplit(url).hostname or url
        with self._lock:
            tripped = self._failures.get(host, 0) >= self.breaker
        attempts = 1 if tripped else self.attempts
        for attempt in range(1, attempts + 1):
            try:
                result = fn()
            except Exception as exc:
                said = transient_answer(exc)
                if said is None:
                    raise
                answer = said
                error: Exception | None = exc
            else:
                status = _status_of(result)
                if not _retryable_status(status):
                    with self._lock:
                        self._failures.pop(host, None)
                    return result
                answer = f"HTTP {status}"
                error = None
            if attempt < attempts:
                delay = self.delays[min(attempt - 1, len(self.delays) - 1)]
                self.notify(
                    f"{host}: {answer}; retrying (attempt {attempt + 1} of {attempts}) "
                    f"in {delay:g} s"
                )
                self.sleep(delay)
                continue
            with self._lock:
                self._failures[host] = self._failures.get(host, 0) + 1
            raise PublisherUnavailable(url, answer, attempts) from error
        raise AssertionError("unreachable")  # pragma: no cover


#: The policy every plan-time probe in a real run uses. Tests replace
#: ``POLICY.sleep`` (``conftest`` does, for the whole suite) so no test waits.
POLICY = RetryPolicy()


class RetryingProbe:
    """Any plan-time probe (``head`` and, where it has one, ``text``) under
    :data:`POLICY`. Wraps rather than subclasses: the real probes keep their
    own checks (the host allow-list, the redirect rules) and know nothing of
    retries."""

    def __init__(self, probe: Any, policy: RetryPolicy | None = None) -> None:
        self.probe = probe
        self._policy = policy

    @property
    def policy(self) -> RetryPolicy:
        return self._policy if self._policy is not None else POLICY

    def head(self, url: str) -> Any:
        return self.policy.call(url, lambda: self.probe.head(url))

    def text(self, url: str) -> str:
        text: str = self.policy.call(url, lambda: self.probe.text(url))
        return text

    def __getattr__(self, name: str) -> Any:
        # Whatever else the real probe carries (a bucket, a timeout). Never
        # `probe` itself: before __init__ has run (copy, pickle) that would recurse.
        if name == "probe":
            raise AttributeError(name)
        return getattr(self.probe, name)


def retrying_head(
    head: Callable[[str], Any], policy: RetryPolicy | None = None
) -> Callable[[str], Any]:
    """A bare ``head`` callable (Kiwix's, CoMaps', the Forest Service
    gateway's) under the policy."""

    def wrapped(url: str) -> Any:
        return (policy if policy is not None else POLICY).call(url, lambda: head(url))

    return wrapped


# ---------------------------------------------------------------------------
# What a resolver does with an outage once the retries are spent (D-035, D-039)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outage:
    """One item a plan could not resolve because a publisher did not answer."""

    unit: str
    item: str
    answer: str
    host: str
    attempts: int = ATTEMPTS


@dataclass
class Outages:
    """The items a plan deferred because a publisher is not answering.

    One collector per plan. A resolver asks :meth:`reporter` for the unit it
    is resolving: for a unit the operator typed by name it gets ``None`` and
    refuses as it always did (D-039: asking by name is asking to see the
    refusal); for a profile member it gets a callable that records the item
    and lets the rest of the unit resolve."""

    items: list[Outage] = field(default_factory=list)

    def reporter(self, unit: str, *, requested: bool) -> OnOutage | None:
        if requested:
            return None

        def report(item: str, exc: PublisherUnavailable) -> None:
            self.items.append(Outage(unit, item, exc.answer, exc.host, exc.attempts))

        return report

    def __bool__(self) -> bool:
        return bool(self.items)

    def units(self) -> list[str]:
        return list(dict.fromkeys(o.unit for o in self.items))

    def deferrals(self) -> tuple[Any, ...]:
        """One :class:`~hammunition.plan.Deferral` per unit, the publisher's
        last answer quoted, the items named."""
        from .plan import Deferral

        out = []
        for unit in self.units():
            mine = [o for o in self.items if o.unit == unit]
            names = list(dict.fromkeys(o.item for o in mine))
            shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
            answers = "; ".join(dict.fromkeys(f"{o.host} answered {o.answer}" for o in mine))
            tries = sorted({o.attempts for o in mine})
            each = (
                f"{tries[0]} attempt{'s' if tries[0] != 1 else ''} each"
                if len(tries) == 1
                else f"{tries[0]} to {tries[-1]} attempts each"
            )
            out.append(
                Deferral(
                    subject=unit,
                    what=f"will not fetch {len(names)} item(s) this run: {shown}",
                    why=(
                        f"the publisher is not answering right now ({answers}); "
                        f"{each}, then given up"
                    ),
                    remedy=RETRY_REMEDY.format(unit=unit),
                    kind="package",
                )
            )
        return tuple(out)

    def footer(self) -> str | None:
        if not self.items:
            return None
        units = " ".join(self.units())
        return (
            f"{len(self.items)} item(s) were deferred because a publisher was not answering "
            f"right now. Nothing is wrong with the catalog: run the same command again in a "
            f"few minutes (`hammunition install {units}` installs just the deferred "
            f"unit(s), and names the publisher's answer if it still refuses)."
        )


def reporter_for(outages: Outages | None, unit: Any) -> OnOutage | None:
    """The outage callback for the planned *unit*, or None -- no collector, or
    the operator typed the unit by name (D-039), so an outage refuses."""
    from .plan import REQUESTED_DIRECTLY

    if outages is None:
        return None
    return outages.reporter(unit.name, requested=REQUESTED_DIRECTLY in unit.requested_by)


RETRY_REMEDY = (
    "run the same command again once the publisher answers; the rest of the plan does "
    "not wait on it, and `hammunition install {unit}` shows the refusal in full"
)
