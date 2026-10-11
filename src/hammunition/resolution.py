# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where a plan's facts come from when the publisher may not be asked.

One :class:`ResolutionContext` is made per run and shared by every resolver.
Online it changes nothing until a publisher has spent its retries
(:class:`~hammunition.catalogue.PublisherUnavailable`); then, if a Bunker is
enrolled and its catalogue verified, the record the Bunker kept answers
instead, and the plan says so on the line it affects. Offline, the publisher
is never asked: the verified catalogue answers, or the item is refused by
name.

This module is a leaf: stdlib, :mod:`hammunition.catalogue` and
:mod:`hammunition.signers` only, so every resolver can import it without a
cycle. What needs the planner or the backends (the deferral an unresolved
item becomes, the data preflight) lives in :mod:`hammunition.plan`.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol, TypeVar, overload

from hammunition.catalogue import CatalogueArtifact, CatalogueInput, PublisherUnavailable
from hammunition.signers import VerifiedCatalogue

__all__ = [
    "NO_BUNKER",
    "CatalogueMiss",
    "InputTransport",
    "MalformedCatalogueRow",
    "ResolutionContext",
    "TextProbe",
]

T = TypeVar("T")

#: The refusal for an offline run with nothing enrolled.
NO_BUNKER = "no Bunker enrolled; hammunition mirror enrol URL"


class InputTransport(Protocol):
    """Reads one mirror path, failing as ``OSError`` (``CatalogueInputs``)."""

    def read(self, relative: str, *, max_bytes: int) -> bytes: ...


class TextProbe(Protocol):
    """A plan-time probe that can fetch a page of text."""

    def text(self, url: str) -> str: ...


class CatalogueMiss(ValueError):
    """The catalogue cannot answer: nothing enrolled, no such entry, or an
    entry that is not current. The message names what was asked for."""


class MalformedCatalogueRow(CatalogueMiss):
    """A signed catalogue row that cannot be used as written (a digest that is not
    64 lowercase hex digits, or a current row with none). Never read as "no row"."""


@dataclass
class ResolutionContext:
    offline: bool = False
    verified: VerifiedCatalogue | None = None
    enrolment_id: str | None = None
    inputs: InputTransport | None = None
    notes: dict[tuple[str, str], str] = field(default_factory=dict)
    _notes_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    _input_cache: dict[tuple[str, str, str], bytes] = field(
        default_factory=dict, init=False, repr=False
    )

    def entry(self, unit: str, name: str) -> CatalogueArtifact:
        if self.verified is None:
            raise CatalogueMiss(NO_BUNKER)
        row = self.verified.catalogue.artifact(unit, name, self.enrolment_id)
        if row is None:
            raise CatalogueMiss(
                f"{unit}/{name}: not on Bunker {self.verified.catalogue.bunker.name}, "
                "publisher unreachable"
            )
        if row.status != "current" or row.path is None or row.sha256 is None or row.size is None:
            raise CatalogueMiss(
                f"{unit}/{name}: Bunker entry is {row.status}: {row.reason or 'not current'}"
            )
        return row

    @overload
    def require_payload(
        self,
        unit: str,
        name: str,
        *,
        sha256: str | None = None,
        size: int | None = None,
        publisher_digest: str | None = None,
        input_kind: None = None,
    ) -> CatalogueArtifact: ...

    @overload
    def require_payload(
        self,
        unit: str,
        name: str,
        *,
        sha256: str | None = None,
        size: int | None = None,
        publisher_digest: str | None = None,
        input_kind: str,
    ) -> CatalogueInput: ...

    def require_payload(
        self,
        unit: str,
        name: str,
        *,
        sha256: str | None = None,
        size: int | None = None,
        publisher_digest: str | None = None,
        input_kind: str | None = None,
    ) -> CatalogueArtifact | CatalogueInput:
        """The entry, matching the caller's payload pins and publisher metadata."""
        if input_kind is None:
            row: CatalogueArtifact | CatalogueInput = self.entry(unit, name)
        else:
            if self.verified is None:
                raise CatalogueMiss(NO_BUNKER)
            found = self.verified.catalogue.input(input_kind, name)
            if found is None:
                raise CatalogueMiss(
                    f"inputs/{input_kind}/{name}: not on Bunker {self.verified.catalogue.bunker.name}, publisher unreachable"
                )
            row = found
        if sha256 is not None and row.sha256 != sha256:
            raise CatalogueMiss(
                f"{unit}/{name}: Bunker copy does not match the repository sha256 pin"
            )
        if publisher_digest is not None and (
            not isinstance(row, CatalogueArtifact) or row.publisher_digest != publisher_digest
        ):
            raise CatalogueMiss(f"{unit}/{name}: Bunker copy does not match the publisher digest")
        if size is not None and row.size != size:
            raise CatalogueMiss(f"{unit}/{name}: Bunker copy does not match the expected size")
        return row

    def signed_sha256(self, unit: str, name: str) -> str | None:
        """The sha256 the verified catalogue lists for this item, or None when
        nothing is enrolled, the row is absent, or the row carries none (and need
        not). The row is found by unit, name and owner alone: a missing size or
        path, or a status that is not current, never hides a digest it carries.
        A row that is malformed raises :class:`MalformedCatalogueRow`."""
        if self.verified is None:
            return None
        row = self.verified.catalogue.artifact_row(unit, name, self.enrolment_id)
        if row is None:
            return None
        if row.sha256 is None:
            if row.status == "current":
                raise MalformedCatalogueRow(f"{unit}/{name}: a current row carries no sha256")
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", row.sha256):
            raise MalformedCatalogueRow(f"{unit}/{name}: its sha256 is not 64 lowercase hex digits")
        return row.sha256

    def input_bytes(self, kind: str, region: str) -> bytes:
        """Read and verify a bounded, signed input; cache successes for this run only."""
        if self.verified is None or self.inputs is None:
            raise CatalogueMiss("no Bunker input transport; hammunition mirror enrol URL")
        row = self.require_payload("inputs", region, input_kind=kind)
        if row.size > 8 * 1024 * 1024:
            raise CatalogueMiss(f"inputs/{kind}/{region}: larger than the 8 MiB input bound")
        key = (kind, region, row.sha256)
        if key in self._input_cache:
            return self._input_cache[key]
        try:
            body = self.inputs.read(row.path, max_bytes=row.size)
        except OSError as exc:
            raise CatalogueMiss(f"inputs/{kind}/{region}: {exc}") from exc
        self.require_payload(
            "inputs",
            region,
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
            input_kind=kind,
        )
        self._input_cache[key] = body
        self.note("inputs", f"{kind}/{region}", fallback=not self.offline)
        return body

    def selection(
        self, kind: str, region: str, reader: Callable[[Path, str, str], T | None]
    ) -> T | None:
        """Decode a verified input using the installed-record reader, without prefix writes."""
        if self.verified is None or self.verified.catalogue.input(kind, region) is None:
            return None
        body = self.input_bytes(kind, region)
        try:
            body.decode("utf-8")
        except UnicodeError as exc:
            raise CatalogueMiss(f"inputs/{kind}/{region}: not UTF-8") from exc
        with tempfile.TemporaryDirectory(prefix="hammunition-input-") as directory:
            path = Path(directory) / "selection"
            path.write_bytes(body)
            result = reader(path, region, region.replace("/", "-"))
        if result is None:
            raise CatalogueMiss(f"inputs/{kind}/{region}: invalid selection record")
        return result

    def outline(self, region: str, probe: TextProbe, *, base: str) -> str:
        """Use the publisher, or a verified and geometrically valid recorded outline."""

        def recorded() -> str:
            from hammunition.copernicus import CopernicusError, parse_poly

            try:
                text = self.input_bytes("region-outline", region).decode("utf-8")
                parse_poly(text)
            except UnicodeError as exc:
                raise CatalogueMiss(f"inputs/region-outline/{region}: not UTF-8") from exc
            except CopernicusError as exc:
                raise CatalogueMiss(
                    f"inputs/region-outline/{region}: invalid outline: {exc}"
                ) from exc
            return text

        return self.choose(
            "inputs",
            f"region-outline/{region}",
            lambda: probe.text(f"{base}/{region}.poly"),
            recorded,
        )

    def unverified(self, unit: str, name: str) -> CatalogueArtifact:
        """An entry whose publisher offers no digest, which the Bunker labels so."""
        row = self.entry(unit, name)
        if row.publisher_check not in ("unverified-fetch", "unverified-zip"):
            raise CatalogueMiss(f"{unit}/{name}: expected an explicitly unverified catalogue entry")
        return row

    def note(self, unit: str, name: str, *, fallback: bool) -> str:
        """The provenance line for an item the catalogue answered, kept for the plan.

        Every line the signer vouches for carries its warnings (a weak key, a
        catalogue older than thirty days)."""
        if self.verified is None:
            raise CatalogueMiss(NO_BUNKER)
        v = self.verified
        prefix = "publisher unreachable; " if fallback else "offline; "
        text = (
            f"{prefix}resolved from Bunker {v.catalogue.bunker.name} ({v.key.id}), "
            f"recorded {v.catalogue.generated}"
        )
        if v.warnings:
            text += "; " + "; ".join(v.warnings)
        with self._notes_lock:
            self.notes[(unit, name)] = text
        return text

    def choose(self, unit: str, name: str, online: Callable[[], T], recorded: Callable[[], T]) -> T:
        """*online* while the publisher answers; *recorded* when it cannot be asked.

        Call it outside the retrying probe, so the catalogue is consulted once
        per item and only after the retries are spent. A certificate that does
        not verify and an HTTP 404 are answers, not outages, and pass through."""
        if self.offline and self.verified is None:
            raise CatalogueMiss(NO_BUNKER)
        if not self.offline:
            try:
                return online()
            except PublisherUnavailable as outage:
                if self.verified is None:
                    raise
                try:
                    result = recorded()
                except CatalogueMiss:
                    if unit == "inputs":
                        kind, separator, region = name.partition("/")
                        missing = (
                            not separator or self.verified.catalogue.input(kind, region) is None
                        )
                    else:
                        missing = (
                            self.verified.catalogue.artifact(unit, name, self.enrolment_id) is None
                        )
                    if missing:
                        raise outage from None
                    raise
                self.note(unit, name, fallback=True)
                return result
        result = recorded()
        self.note(unit, name, fallback=not self.offline)
        return result
