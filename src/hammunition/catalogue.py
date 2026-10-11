# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import re
import urllib.parse
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hammunition.keystrength import classify
from hammunition.station import REGION


class Wire(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="allow")


class Bunker(Wire):
    name: str
    mode: Literal["personal", "group"]


class Signer(Wire):
    id: str
    public_key: str
    algorithm: str
    bits: int
    no_touch_required: bool
    hardware: bool
    signature: str


class CatalogueArtifact(Wire):
    unit: str
    name: str
    path: str | None
    sha256: str | None
    size: int | None
    publisher_check: str
    publisher_digest: str | None
    publisher_url: str
    publisher_name: str | None
    publisher_size: int | None
    licence: str
    fetched: str | None
    verified: str | None
    status: str
    reason: str | None
    previous: str | None
    share: str


class CatalogueInput(Wire):
    kind: Literal[
        "region-outline",
        "tile-selection",
        "sheet-selection",
        "dem3dep-selection",
        "fstopo-selection",
    ]
    region: str
    name: str
    path: str
    sha256: str
    size: int
    fetched: str


class Catalogue(Wire):
    kind: Literal["bunker-index"]
    version: Literal[3]
    serial: int = Field(ge=1)
    generated: str
    bunker: Bunker
    signers: tuple[Signer, ...] = Field(min_length=1, max_length=16)
    engine_version: str | None
    artifacts: tuple[CatalogueArtifact, ...] = Field(min_length=0)
    inputs: tuple[CatalogueInput, ...] = Field(min_length=0)
    deferred: tuple[JsonValue, ...]
    declined: tuple[JsonValue, ...]
    last_run: JsonValue

    def artifact_row(
        self, unit: str, name: str, enrolment_id: str | None
    ) -> CatalogueArtifact | None:
        """The row for this unit and name that this enrolment may see, whatever
        else it lacks (a missing size or path, a status that is not current)."""
        for row in self.artifacts:
            if (row.unit, row.name) != (unit, name):
                continue
            if (
                self.bunker.mode == "personal"
                or row.share == "all"
                or (row.share == f"owner:{enrolment_id}" and enrolment_id is not None)
            ):
                return row
        return None

    def artifact(self, unit: str, name: str, enrolment_id: str | None) -> CatalogueArtifact | None:
        for row in self.artifacts:
            if (
                (row.unit, row.name) != (unit, name)
                or row.path is None
                or row.sha256 is None
                or row.size is None
            ):
                continue
            if (
                self.bunker.mode == "personal"
                or row.share == "all"
                or (row.share == f"owner:{enrolment_id}" and enrolment_id is not None)
            ):
                return row
        return None

    def input(self, kind: str, region: str) -> CatalogueInput | None:
        return next((row for row in self.inputs if (row.kind, row.region) == (kind, region)), None)


class CatalogueError(ValueError):
    pass


class PublisherUnavailable(OSError):
    """A publisher did not answer a probe after every retry.

    ``answer`` is the last thing it said (``HTTP 503 Service Unavailable``,
    ``timed out``); ``attempts`` how many times it was asked."""

    def __init__(self, url: str, answer: str, attempts: int) -> None:
        self.url = url
        self.host = urllib.parse.urlsplit(url).hostname or url
        self.answer = answer
        self.attempts = attempts
        tries = f"{attempts} attempt{'s' if attempts != 1 else ''}"
        super().__init__(
            f"{url}: the publisher is not answering right now (its last answer after "
            f"{tries}: {answer})"
        )


def valid_enrolment_id(value: str) -> bool:
    # Opaque, not a path; printable ASCII header value, no whitespace/controls.
    return bool(value) and all(33 <= ord(char) <= 126 for char in value)


def safe_relative(value: str, field: str) -> str:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(c in value for c in "\r\n\x00")
        or any(p in ("", ".", "..") for p in value.split("/"))
    ):
        raise CatalogueError(f"{field}: unsafe relative path {value!r}")
    return value


def utc(value: str, field: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        raise CatalogueError(f"{field}: expected RFC 3339 UTC with Z suffix")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogueError(f"{field}: invalid UTC calendar timestamp") from exc


def unique(items: list[tuple[str, ...]], field: str) -> None:
    if len(items) != len(set(items)):
        raise CatalogueError(f"{field}: duplicate identity")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, value in pairs:
        if name in out:
            raise CatalogueError(f"{name}: duplicate JSON field")
        out[name] = value
    return out


def parse(raw: bytes) -> Catalogue:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except CatalogueError:
        raise
    except (RecursionError, ValueError) as exc:
        raise CatalogueError(f"catalogue: invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogueError("catalogue: expected an object")
    if type(value.get("version")) is not int or value["version"] != 3:
        raise CatalogueError(
            f"version: unsupported Bunker catalogue version {value.get('version')!r}; this reader accepts 3"
        )
    try:
        cat = Catalogue.model_validate_json(raw)
    except (RecursionError, ValueError) as exc:
        raise CatalogueError(f"catalogue: invalid v3 document: {exc}") from exc
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", cat.bunker.name):
        raise CatalogueError("bunker.name: expected a lowercase Bunker name")
    utc(cat.generated, "generated")
    unique([(s.id,) for s in cat.signers], "signers")
    unique([(s.signature,) for s in cat.signers], "signers.signature")
    unique([(a.unit, a.name) for a in cat.artifacts], "artifacts")
    unique([(i.kind, i.region) for i in cat.inputs], "inputs")
    for n, signer in enumerate(cat.signers):
        where = f"signers[{n}]"
        safe_relative(signer.signature, f"{where}.signature")
        if not signer.signature.startswith("catalogue.sig.d/"):
            raise CatalogueError(f"{where}.signature: must be under catalogue.sig.d/")
        try:
            strength = classify(signer.public_key)
        except ValueError as exc:
            raise CatalogueError(f"{where}.public_key: {exc}") from exc
        for field, actual in (
            ("algorithm", strength.algorithm),
            ("bits", strength.bits),
            ("id", strength.fingerprint),
        ):
            if getattr(signer, field) != actual:
                raise CatalogueError(f"{where}.{field}: does not match public_key")
        if signer.no_touch_required and not strength.hardware_by_type:
            raise CatalogueError(f"{where}.no_touch_required: true requires an sk key")
        if strength.hardware_by_type and not signer.hardware:
            raise CatalogueError(f"{where}.hardware: sk keys are hardware by type")
    rows: list[tuple[str, int, CatalogueArtifact | CatalogueInput]] = [
        ("artifacts", n, row) for n, row in enumerate(cat.artifacts)
    ]
    rows += [("inputs", n, row) for n, row in enumerate(cat.inputs)]
    for field_name, n, row in rows:
        where = f"{field_name}[{n}]"
        if row.path is not None:
            safe_relative(row.path, f"{where}.path")
        safe_relative(row.name, f"{where}.name")
        if row.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", row.sha256):
            raise CatalogueError(f"{where}.sha256: expected 64 lowercase hex digits")
        if row.size is not None and row.size < 0:
            raise CatalogueError(f"{where}.size: must be nonnegative")
        if row.fetched is not None:
            utc(row.fetched, f"{where}.fetched")
        if isinstance(row, CatalogueInput):
            if REGION.fullmatch(row.region) is None:
                raise CatalogueError(f"{where}.region: invalid region name")
            if row.path != f"inputs/{row.kind}/{row.name}":
                raise CatalogueError(f"{where}.path: does not match inputs/kind/name")
        else:
            safe_relative(row.unit, f"{where}.unit")
            if "/" in row.unit:
                raise CatalogueError(f"{where}.unit: must be one segment")
            if row.verified is not None:
                utc(row.verified, f"{where}.verified")
            if row.previous is not None:
                safe_relative(row.previous, f"{where}.previous")
            if row.share != "all" and (
                not row.share.startswith("owner:")
                or not valid_enrolment_id(row.share.removeprefix("owner:"))
            ):
                raise CatalogueError(f"{where}.share: expected all or owner:<enrolment id>")
            if row.publisher_size is not None and row.publisher_size <= 0:
                raise CatalogueError(f"{where}.publisher_size: must be positive")
            if (row.publisher_name is None) != (row.publisher_size is None):
                raise CatalogueError(
                    f"{where}.publisher_name/publisher_size: must be set or null together"
                )
            if row.publisher_name is not None:
                safe_relative(row.publisher_name, f"{where}.publisher_name")
    return cat
