# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pinned payloads (source archives, prebuilt binaries, venv payload trees, Node
source tarballs) fetched mirror-first.  #381, Task 13.

One name and one fetch step for every backend that downloads a repository-pinned
archive: the Bunker keeps it at ``<unit>/<sha256>/<file name>``. The sha256 in
the name is what keeps two targets' same-named archives apart. Every download
goes through :class:`~hammunition.fetch.Fetcher` (``fetch``), so the cache-hit,
temporary-file, redirect and redaction rules are the ones every other route has.

Imports are deferred to call time: ``hammunition.backends`` imports this module
and ``hammunition.fetch`` imports ``hammunition.backends.base``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hammunition.backends.base import Action
    from hammunition.fetch import Fetcher, MirrorPath
    from hammunition.manifest.schema import RemoteArtifact
    from hammunition.resolution import ResolutionContext

__all__ = [
    "payload_action",
    "payload_cached",
    "payload_name",
    "payload_path",
    "preflight_payloads",
]


def payload_name(artifact: RemoteArtifact) -> str:
    """``<sha256>/<file name>``: the artifact's stable name within its catalog unit."""
    from hammunition.fetch import safe_name

    return f"{artifact.sha256}/{safe_name(artifact.url)}"


def payload_path(unit: str, artifact: RemoteArtifact) -> MirrorPath:
    from hammunition.fetch import MirrorPath

    return MirrorPath(unit, payload_name(artifact))


def payload_action(
    unit: str,
    artifact: RemoteArtifact,
    fetcher: Fetcher,
    *,
    label: str,
    max_bytes: int | None = None,
    expected_size: int | None = None,
    fetched: dict[str, Path] | None = None,
) -> Action:
    """The one fetch step for a pinned payload: Bunker first, publisher second
    (never, offline), the sha256 pin checked either way."""
    from hammunition.backends.base import Action, BackendError
    from hammunition.fetch import fetch_disclosure, record_fetch

    path = payload_path(unit, artifact)
    suffix, detail, sources = fetch_disclosure(fetcher, artifact.url, path, "sha256")
    facts: dict[str, str] = {}

    def perform() -> str:
        result = fetcher.fetch(artifact, max_bytes=max_bytes, mirror=path)
        if expected_size is not None and result.size != expected_size:
            raise BackendError(
                f"{unit}/{path.name}: manifest size {expected_size}, got {result.size}"
            )
        if fetched is not None:
            fetched["path"] = result.path
        provenance = record_fetch(result, facts, mirrored=fetcher.mirror is not None)
        where = "cached" if result.from_cache else "downloaded"
        return f"{where} {result.size} bytes, sha256 verified{provenance}"

    return Action(
        kind="fetch",
        description=f"Fetch {unit} {label}{suffix}",
        detail=detail,
        sources=sources,
        facts=facts,
        perform=perform,
    )


def preflight_payloads(
    unit: str,
    pins: Sequence[tuple[RemoteArtifact, int | None]],
    *,
    context: ResolutionContext | None,
    cached: Callable[[RemoteArtifact], bool] | None = None,
) -> None:
    """Offline, require every pin on the verified Bunker (matching the repository's
    own sha256, and size where it has one) before any build step is returned.
    A pin whose verified bytes are already in the local cache needs no Bunker
    row (*cached*), as for the data units.

    Raises :class:`~hammunition.resolution.CatalogueMiss` naming the first item
    the catalogue cannot answer for. Online, or with no context, it does nothing."""
    if context is None or not context.offline:
        return
    names = [
        (payload_name(pin), pin, size) for pin, size in pins if cached is None or not cached(pin)
    ]
    # Every pin is required before any is noted, so a unit the Bunker cannot
    # fully answer for leaves no provenance line behind.
    for name, pin, size in names:
        context.require_payload(unit, name, sha256=pin.sha256, size=size)
    for name, _pin, _size in names:
        context.note(unit, name, fallback=False)


def payload_cached(fetcher: Fetcher) -> Callable[[RemoteArtifact], bool]:
    """Whether a pin's verified bytes are already in *fetcher*'s artifact cache
    (an exact sha256 match; it never fetches)."""
    from hammunition.plan import cached_remote

    return lambda pin: cached_remote(fetcher, pin)
