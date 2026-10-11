# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``artifacts`` as data.  D-070, D-059.

The contract Hammunition Bunker mirrors from: every remote data artifact
the engine would fetch for an explicit selection. Nothing in it is the
operator's; the selection is what the command was given.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from hammunition.backends.data import human_size
from hammunition.interface.envelope import Strict, described

__all__ = [
    "CHECKS",
    "ArtifactEntry",
    "ArtifactsDocument",
    "GitPinEntry",
    "InputEntry",
    "render_artifacts",
]

#: How an artifact is verified. ``sha1-publisher`` is the SHA-1 and size from
#: CoMaps' own map index at the pinned commit (D-069's ``comaps-maps``
#: entries); ``sha256-publisher`` (a ``.sha256`` or ``.meta4`` the publisher
#: serves) is in the Bunker contract but no catalog unit produces it today.
#: ``unverified-zip`` is the ACMA register (D-074, amended 2026-10-01): no
#: digest is published and the file changes daily, so ``digest`` is null and
#: the zip's own CRC-32s and tables are what is checked, from either source.
#: ``unverified-fetch`` is no digest at all, size and freshness the only
#: check: the three on-request repeater lists (D-078, unit
#: ``repeater-snapshots``: ETCC, Brandmeister, hearham), and, since Task 16,
#: a US Forest Service FSTopo sheet Hammunition has not pinned a sha256 for
#: -- the Forest Service publishes no checksum of its own (D-068, amended
#: 2026-10-01); the licence line is the project's position, never a licence,
#: for the repeater lists, and the manifest's stated licence for a sheet.
CHECKS = (
    "sha256",
    "md5-publisher",
    "etag-md5",
    "sha1-publisher",
    "sha256-publisher",
    "unverified-zip",
    "unverified-fetch",
)


@dataclass(frozen=True)
class ArtifactEntry(Strict):
    """One remote artifact, or one the selection cannot list and why."""

    unit: str = described(
        "the catalog unit (`osm-regions`, `dem-copernicus`, `country-files`, `kiwix-library`)"
    )
    name: str | None = described(
        "the artifact's stable name within the unit: a region path, a tile name, a data "
        "file's name, a Kiwix book id as the pin file names it. A LAN mirror serves it at `<mirror>/<unit>/<name>`. Null only for a "
        "deferred entry that covers the whole unit"
    )
    url: str | None = described("the publisher URL the engine itself fetches; null when deferred")
    check: str | None = described(
        "how the download is verified: `sha256` (pinned by Hammunition), `md5-publisher` "
        "(Geofabrik's published MD5), `etag-md5` (the Copernicus object's ETag), "
        "`sha1-publisher` (the SHA-1 and size in CoMaps' own map index at the pinned "
        "commit, carried in the catalog), `sha256-publisher` (no unit uses it today) or "
        "`unverified-zip` (the ACMA register: no digest exists, so the zip's own CRC-32s "
        "and the tables its reader needs are checked; D-074, amended 2026-10-01) or "
        "`unverified-fetch` (an on-request repeater list of unit `repeater-snapshots`: "
        "no digest, no licence stated by its publisher, only size and date can be "
        "checked; D-078); null when deferred"
    )
    digest: str | None = described(
        "the expected digest, in hex, of the kind `check` names: the pin, or the publisher's "
        "checksum as the engine read it while resolving -- for `etag-md5` the raw ETag, "
        "which is a bare 32 hex-digit MD5 for a single-part upload or `<hex>-<parts>` for a "
        "multipart one (see `part_size`), never stripped of its quotes or suffix; null when "
        "deferred and for `unverified-zip` and `unverified-fetch`, which have none"
    )
    checksum_url: str | None = described(
        "where a publisher checksum is read: the `.md5` beside a Geofabrik file, or the tile "
        "URL whose `HEAD` carries the ETag; null for a pinned sha256 and when deferred"
    )
    size: int | None = described(
        "bytes, known before the fetch (for `unverified-zip` and `unverified-fetch`, the "
        "publisher's `HEAD` today: the file changes; null for `unverified-fetch` when the "
        "server states no length); null when deferred"
    )
    licence: str = described(
        "the licence line the plan prints for the unit, or for a Kiwix book that book's own"
    )
    deferred: str | None = described(
        "null, or why this artifact cannot be listed for this selection"
    )
    part_size: int | None = field(
        default=None,
        metadata={"doc": "ETag multipart part size in bytes, or null when not recorded"},
    )


@dataclass(frozen=True)
class InputEntry(Strict):
    """One Bunker selection input (Task 16): the outline or the recorded
    selection text a stateless re-run of the engine's own codecs produces for
    one of `osm-regions`, `dem-copernicus`, `dem-3dep`, `usgs-ustopo` or
    `usfs-fstopo`'s regions. Never read from an installed record."""

    kind: str = described("one of the five Bunker input kinds")
    region: str = described("explicitly requested Geofabrik region")
    name: str = described("name within inputs/<kind>/, including region path")
    url: str | None = described("publisher URL for an outline; null for locally derived selections")
    sha256: str | None = described("sha256 of the exact content below; null when deferred")
    size: int | None = described("UTF-8 content size in bytes; null when deferred")
    content: str | None = described(
        "exact UTF-8 input bytes for the Bunker writer to store; null when deferred"
    )
    deferred: str | None = described("reason this input cannot be produced; null when available")


@dataclass(frozen=True)
class GitPinEntry(Strict):
    """One git revision a `git` install block pins, for the Bunker to mirror
    as a verified bundle (Task 16, D-070, D-024). Recursive submodule
    bundles are not named here: the manifest carries only `submodules: bool`,
    and the Bunker's own writer enumerates the pinned gitlinks through
    :mod:`hammunition.gitbundles` after cloning, never guessed at listing
    time."""

    unit: str = described("git-bundles")
    name: str = described("unit@repository-pinned-commit; diagnostic only when deferred")
    repo: str = described("upstream repository the Bunker clones at this pin")
    ref: str = described("repository manifest tag or commit ref")
    commit: str | None = described("full repository commit, null for an unpinned tag")
    submodules: bool = described(
        "writer must recursively enumerate pinned gitlinks and produce separate bundles"
    )
    licence: str = described("manifest licence field, carried verbatim")
    deferred: str | None = described("why no verifiable bundle can be named")


@dataclass(frozen=True)
class ArtifactsDocument(Strict):
    """Every remote data artifact the engine would fetch for the selection
    given (D-070): `data` units, map regions, terrain tiles and reference
    books. No station file is read; the regions and books are the ones on
    the command line. What cannot be listed is listed as deferred, with the
    reason, never dropped."""

    KIND: ClassVar[str] = "artifacts"

    map_regions: tuple[str, ...] = described("the `--map-regions` given; empty when none")
    map_freshness: str = described("the `--map-freshness` given, `yearly` when none")
    reference_books: tuple[str, ...] = described(
        "the `--reference-books` given (Kiwix book ids, D-066); empty when none"
    )
    units: tuple[str, ...] = described("the units listed, in order")
    artifacts: tuple[ArtifactEntry, ...] = described(
        "one entry per artifact, deferred ones included"
    )
    inputs: tuple[InputEntry, ...] = field(
        default=(),
        metadata={
            "doc": "selection inputs (Task 16) the regional units among `units` need: "
            "each region's outline and its four recorded selections, or a deferral; "
            "empty when no selected unit is regional"
        },
    )
    git_pins: tuple[GitPinEntry, ...] = field(
        default=(),
        metadata={
            "doc": "one entry per `git` install block among `units` (Task 16), its pinned "
            "revision for the Bunker to bundle; empty when no selected unit builds from git"
        },
    )


def render_artifacts(doc: ArtifactsDocument) -> list[str]:
    """``artifacts`` as the terminal shows it: one line per artifact, then
    inputs and git pins."""
    lines = [
        f"Remote data artifacts for {len(doc.units)} unit(s), "
        f"{len(doc.map_regions)} map region(s), {len(doc.reference_books)} reference book(s), "
        f"freshness {doc.map_freshness}:"
    ]
    listed = [e for e in doc.artifacts if e.deferred is None]
    deferred: Sequence[ArtifactEntry] = [e for e in doc.artifacts if e.deferred is not None]
    for entry in listed:
        size = human_size(entry.size) if entry.size is not None else "?"
        lines.append(f"  {entry.unit}  {entry.name}  {size}  {entry.check}  {entry.url}")
    if not listed:
        lines.append("  (none)")
    if deferred:
        lines.append("Deferred:")
        for entry in deferred:
            lines.append(f"  {entry.unit}  {entry.name or '(all)'}: {entry.deferred}")
    if doc.inputs:
        lines.append("Inputs:")
        for row in doc.inputs:
            if row.deferred is not None:
                lines.append(f"  {row.kind}/{row.name}: {row.deferred}")
            else:
                size = human_size(row.size) if row.size is not None else "?"
                lines.append(f"  {row.kind}/{row.name}  {size}")
    if doc.git_pins:
        lines.append("Git pins:")
        for pin in doc.git_pins:
            if pin.deferred is not None:
                lines.append(f"  {pin.name}: {pin.deferred}")
            else:
                lines.append(f"  {pin.name}  {pin.repo}@{pin.commit}")
    return lines
