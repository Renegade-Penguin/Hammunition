# Offline Bunker catalogue — engine (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the engine half of #381 phase 1: enrol a signed Bunker catalogue, resolve data without publishers, and fetch mirrored data, pinned payloads and git bundles without upgrading their trust.

**Architecture:** The engine owns `keystrength`, the v3 `catalogue` reader and `signers`; the Bunker imports these from the released engine. A run loads one immutable `VerifiedCatalogue` and threads a `ResolutionContext` through existing resolvers. Keep publisher probes and digest verification separate: a catalogue supplies recorded identity and availability, while repository pins continue to govern installed bytes. Existing un-enrolled mirrors keep D-070 behaviour.

**Tech Stack:** Python 3.11+, stdlib subprocess/urllib/pathlib/hashlib, OpenSSH 8.2+, git, pydantic, YAML, pytest, mypy --strict, ruff, MkDocs.

**Spec:** `docs/superpowers/specs/2026-10-07-offline-bunker-catalogue-design.md`

**Contract:** `docs/superpowers/plans/2026-10-07-offline-bunker-contract.md` (binding; do not edit).

## Global Constraints

- Python 3.11+.
- Type hints throughout; `mypy --strict` clean.
- No `shell=True`; subprocesses receive argv lists, never shell strings.
- Owner-aware paths: use `hammunition.paths.owner_aware_dir`, `ensure_operator_dir` and `open_operator_dir`; sudo must resolve the operator's home.
- Never touch the operator's real station config in tests: retain `tests/conftest.py`'s `_isolated_operator_environment`, `_NoRealAccounts`, network and machine-query guards; use `tmp_path` for explicit files.
- Changelog fragment, not `CHANGELOG.md`: `changelog.d/381-offline-bunker.added.md`.
- Generated docs are regenerated, then checked with `--check`; never hand-edit generated output.
- Placeholders `N0CALL` / `FN31pr` only for station examples in this plan.
- No real region names of the maintainer: use `europe/monaco` or `north-america/us/delaware` in new tests and examples.
- The catalog is pure data; the engine never hardcodes the profile's package list as installation logic.
- Sign exact served bytes; namespace `hammunition-bunker-catalogue`, principal `bunker:<name>`.
- RSA ≤ 2048 is accepted and warned: `RSA <bits>-bit is weak: replace with Ed25519, ECDSA or RSA 3072+`.
- Hardware keys are recommended, never required by default; a non-sk hardware claim requires operator affirmation.
- A signature means “this Bunker recorded this”, never more; unverified data stays unverified and behind its existing gate.
- Every new site page goes in `mkdocs.yml` in the same commit.

Read foundation before implementing: `CLAUDE.md:1-909`, the spec `:1-382`, the binding contract `:1-156`, `tests/conftest.py:1-576` (especially `:65-176` isolation and machine-query guards), and `docs/superpowers/plans/2026-09-29-artifacts-and-mirror.md:1-156` for the existing plan style. Module ranges below refer to the checkout before implementation, not post-edit line numbers. New helper APIs in this plan are proposed additions, not claims they already exist.

The task order below is A1–A13. A7 and A8 are split by resolver/download family; none of the requested tasks are merged. Commit commands are instructions for the future implementer, not actions to run while writing this plan. Use the personal commit identity from CLAUDE.md/global instructions; do not commit raw corpus, machine identifiers or station values.

Out of scope: enabling PAM (including `security-keys pam`), apt/pip offline support (phase 2), the Bunker writer/signing/setup/group server/export, and the network-isolated integration test (owned by Plan B). This plan implements the `file://` reader now as requested; producing removable drives remains phase 3. No GYST issue is filed by this writing task; Plan B owns the integration/GYST handoff.

## Review Focus

1. A signed catalogue with duplicate identities, booleans masquerading as integers, or traversal in a signature/input path must refuse with the offending field, before any read outside the mirror — Task 1 tests.
2. Re-enrolment against an old backup, or concurrent acceptance of different serials, must not lower the highest accepted serial; only explicit `accept-older` may do that — Tasks 2–4 tests.
3. A group catalogue can contain another operator's entry, and an HTTP redirect can leave the Bunker; ignore the entry and never leak `X-Hammunition-Enrolment` to a publisher — Task 4 tests.
4. A recorded empty selection or a selection made under another topographic bound must not become “nothing to install” by accident; validate the record, recompute from its verified outline for the current bound, and preserve existing data on deferral — Tasks 7 and 9 tests.
5. A good catalogue with missing/corrupt bytes, a retagged bundle or a missing recursive submodule must refuse before install/build, and offline mode must never fall through to a publisher — Tasks 12 and 15 tests.

---

### Task 1: A1 — Shared key-strength table, strict v3 reader and reference

**Files:**
- Create: `src/hammunition/keystrength.py`, `src/hammunition/catalogue.py`, `docs/reference/bunker-catalogue.md`, `tests/bunker_fixtures.py`, `tests/test_bunker_catalogue.py`.
- Modify: `mkdocs.yml:249-263` (Reference / The engine).
- Test: `tests/test_bunker_catalogue.py`, `tests/test_site.py`.
- Read: binding contract in full; `src/hammunition/station.py:85-112` (`REGION`); `src/hammunition/manifest/schema.py:141-157` (pin format); `src/hammunition/interface/envelope.py:1-145` (do not use the CLI envelope on the wire catalogue).

**Interfaces:**
- Consumes: `station.REGION: re.Pattern[str]`.
- Produces: `classify(public_key_line: str) -> KeyStrength`; immutable `KeyStrength(algorithm: str, bits: int, hardware_by_type: bool, rank: int, weak: bool, warning: str | None, fingerprint: str)` (last field is engine metadata, not a new wire requirement).
- Produces: `parse(raw: bytes) -> Catalogue`, `valid_enrolment_id(value: str) -> bool`, `safe_relative(value: str, field: str) -> str`, `utc(value: str, field: str) -> None`, `CatalogueError(ValueError)`, `Catalogue` with typed `bunker`, `signers`, `artifacts`, `inputs`, `serial`, `generated`, and `artifact(unit: str, name: str, enrolment_id: str | None) -> CatalogueArtifact | None` / `input(kind: str, region: str) -> CatalogueInput | None`.
- Produces test helpers: `key(tmp_path: Path, algorithm: str = "ed25519", bits: int | None = None) -> Path`, `document(public_key: str, **changes: object) -> dict[str, object]`, `encode(doc: dict[str, object]) -> bytes`, `artifact(unit: str, name: str, body: bytes, **changes: object) -> dict[str, object]`. Import through `bunker_fixtures`, following the existing `json_support` import convention (there is no top-level `tests/__init__.py`).

- [ ] Write failing tests and the complete fixture helper. No canned public-key string and no CI token:

```python
# tests/bunker_fixtures.py
import hashlib
import json
import subprocess
from pathlib import Path
from hammunition.keystrength import classify


def key(tmp_path: Path, algorithm: str = "ed25519", bits: int | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"key-{algorithm}-{bits or 0}"
    argv = ["ssh-keygen", "-q", "-t", algorithm, "-N", "", "-f", str(path)]
    if bits is not None:
        argv += ["-b", str(bits)]
    subprocess.run(argv, check=True, capture_output=True)
    return path


def document(public_key: str, **changes: object) -> dict[str, object]:
    strength = classify(public_key)
    value: dict[str, object] = {
        "kind": "bunker-index", "version": 3, "serial": 42,
        "generated": "2026-10-07T12:00:00Z",
        "bunker": {"name": "bunker", "mode": "personal"},
        "signers": [{"id": strength.fingerprint, "public_key": public_key,
                     "algorithm": strength.algorithm, "bits": strength.bits,
                     "hardware": False, "signature": "catalogue.sig.d/1.sig"}],
        "engine_version": "0.22.0", "artifacts": [], "inputs": [],
        "deferred": [], "declined": [], "last_run": None,
    }
    value.update(changes)
    return value


def encode(doc: dict[str, object]) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def artifact(unit: str, name: str, body: bytes, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "unit": unit, "name": name, "path": f"{unit}/{name}",
        "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
        "publisher_check": "sha256", "publisher_digest": None,
        "publisher_url": "https://example.invalid/payload",
        "publisher_name": None, "publisher_size": None,
        "licence": "CC0-1.0", "fetched": "2026-10-06T03:00:00Z",
        "verified": "2026-10-07T03:00:00Z", "status": "current",
        "reason": None, "previous": None, "share": "all",
    }
    value.update(changes)
    return value
```

```python
from pathlib import Path
# tests/test_bunker_catalogue.py
import json
import pytest
from hammunition.catalogue import CatalogueError, parse
from hammunition.keystrength import classify
from bunker_fixtures import artifact, document, encode, key

@pytest.mark.parametrize("algorithm,bits,rank,weak", [
    ("ed25519", None, 1, False), ("ecdsa", 384, 2, False),
    ("rsa", 4096, 3, False), ("rsa", 2048, 5, True),
])
def test_real_key_strength(tmp_path: Path, algorithm: str, bits: int | None, rank: int, weak: bool) -> None:
    private = key(tmp_path, algorithm, bits)
    got = classify(private.with_suffix(".pub").read_text().strip())
    assert (got.rank, got.weak) == (rank, weak)
    assert not got.hardware_by_type
    assert got.warning == (
        "RSA 2048-bit is weak: replace with Ed25519, ECDSA or RSA 3072+"
        if weak else None
    )

@pytest.mark.parametrize("field,value", [
    ("version", 4), ("version", True), ("serial", 0), ("serial", True),
    ("generated", "2026-10-07T12:00:00+01:00"),
    ("bunker", {"name": "../x", "mode": "personal"}),
    ("signers", []),
])
def test_field_refusals(tmp_path: Path, field: str, value: object) -> None:
    public = key(tmp_path).with_suffix(".pub").read_text().strip()
    with pytest.raises(CatalogueError, match=field):
        parse(encode(document(public, **{field: value})))

def test_duplicate_artifact_and_signature_traversal(tmp_path: Path) -> None:
    public = key(tmp_path).with_suffix(".pub").read_text().strip()
    row = artifact("osm-regions", "europe/monaco", b"pbf")
    with pytest.raises(CatalogueError, match="artifacts"):
        parse(encode(document(public, artifacts=[row, row])))
    doc = document(public)
    signers = doc["signers"]
    assert isinstance(signers, list)
    signers[0]["signature"] = "catalogue.sig.d/../escape.sig"
    with pytest.raises(CatalogueError, match="signers"):
        parse(encode(doc))

def test_group_filter_is_not_a_parse_filter(tmp_path: Path) -> None:
    public = key(tmp_path).with_suffix(".pub").read_text().strip()
    row = artifact("acma-register", "spectra_rrl.zip", b"zip", share="owner:laptop-a")
    cat = parse(encode(document(public, bunker={"name": "bunker", "mode": "group"},
                                artifacts=[row])))
    assert len(cat.artifacts) == 1
    assert cat.artifact("acma-register", "spectra_rrl.zip", "laptop-b") is None
    assert cat.artifact("acma-register", "spectra_rrl.zip", "laptop-a") is not None

def test_failed_v2_entry_is_preserved_but_not_usable(tmp_path: Path) -> None:
    public = key(tmp_path).with_suffix(".pub").read_text().strip()
    failed = artifact("osm-regions", "europe/monaco", b"unused", path=None, sha256=None,
                      size=None, fetched=None, verified=None, status="failed", reason="HTTP 503")
    cat = parse(encode(document(public, artifacts=[failed])))
    assert cat.artifacts[0].path is None and cat.artifacts[0].status == "failed"

```

Also parameterize every field in the contract table: missing field, null where forbidden, wrong type, invalid timestamp/calendar date, key algorithm/bits/id mismatch, `inputs[].kind`/region, negative sizes, malformed sha256, unsafe previous/path/name, owner id with CR/LF, and duplicate inputs/signers. Test malformed UTF-8, duplicate JSON keys, non-object root, DSA refusal, all ECDSA ranks and RSA rank 4 (generate RSA 2560). These are field rules, not guesses about v2 status vocabulary: retain status/reason/previous strings without inventing a “current only” format restriction.

- [ ] Hold the fresh-Bunker boundary explicitly in `tests/test_bunker_catalogue.py`:

```python
def test_fresh_bunker_has_empty_payload_lists(tmp_path: Path) -> None:
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    got = parse(encode(document(public, artifacts=[], inputs=[])))
    assert got.artifacts == () and got.inputs == ()
    assert len(got.signers) == 1
    with pytest.raises(CatalogueError, match="signers"):
        parse(encode(document(public, artifacts=[], inputs=[], signers=[])))
```

The `Catalogue` code below deliberately has no minimum length on `artifacts` or `inputs`; only `signers` has `Field(min_length=1)`. JSON empty lists therefore become immutable empty tuples. Expected pre-fix FAIL: `CatalogueError` on an empty payload list; expected PASS after the declarations below.

- [ ] Run: `.venv/bin/pytest tests/test_bunker_catalogue.py -q`. Expected FAIL during collection: `ModuleNotFoundError: No module named 'hammunition.catalogue'`.

- [ ] Implement the classifier as a full function. Validate the key blob's embedded type against the line type, so a substituted prefix cannot change hardware classification:

```python
from dataclasses import dataclass
import base64
import struct
import subprocess

@dataclass(frozen=True)
class KeyStrength:
    algorithm: str
    bits: int
    hardware_by_type: bool
    rank: int
    weak: bool
    warning: str | None
    fingerprint: str


def classify(public_key_line: str) -> KeyStrength:
    parts = public_key_line.strip().split()
    if len(parts) < 2 or any(c in public_key_line for c in "\r\n\x00"):
        raise ValueError("public_key must be one OpenSSH public key line")
    algorithm = parts[0]
    ed = {"ssh-ed25519", "sk-ssh-ed25519@openssh.com"}
    ec = {"ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
          "sk-ecdsa-sha2-nistp256@openssh.com"}
    if algorithm not in ed | ec | {"ssh-rsa"}:
        raise ValueError(f"public_key algorithm {algorithm!r} is refused (DSA is unsupported)")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        size = struct.unpack(">I", blob[:4])[0]
        embedded = blob[4:4 + size].decode("ascii")
    except (ValueError, struct.error, UnicodeError) as exc:
        raise ValueError("public_key is not an OpenSSH key blob") from exc
    if embedded != algorithm:
        raise ValueError("public_key algorithm does not match its encoded key type")
    try:
        result = subprocess.run(
            ["ssh-keygen", "-l", "-E", "sha256", "-f", "/dev/stdin"],
            input=(public_key_line.strip() + "\n").encode(), capture_output=True,
            check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("ssh-keygen could not classify public_key; install OpenSSH 8.2+") from exc
    if result.returncode:
        raise ValueError("public_key is invalid; OpenSSH refuses RSA under 1024 bits")
    fields = result.stdout.decode().split()
    if len(fields) < 2 or not fields[0].isdigit() or not fields[1].startswith("SHA256:"):
        raise ValueError("ssh-keygen returned no public_key size/fingerprint")
    bits = int(fields[0])
    rank = 1 if algorithm in ed else 2 if algorithm in ec else 3 if bits >= 3072 else 4 if bits > 2048 else 5
    weak = algorithm == "ssh-rsa" and bits <= 2048
    warning = f"RSA {bits}-bit is weak: replace with Ed25519, ECDSA or RSA 3072+" if weak else None
    return KeyStrength(algorithm, bits, algorithm.startswith("sk-"), rank, weak, warning, fields[1])
```

Strip the terminal newline when reading `.pub` in fixtures and enrolment; retain rejection of *embedded* newlines. Do not silently strip multiple lines inside `classify`.

Add the shared model base first:

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue

class Wire(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="allow")
```

Implement the wire models with pydantic `BaseModel`, `ConfigDict(strict=True, frozen=True, extra="allow")`: forward-compatible **extra fields** are permitted within v3, required fields are not optional. Use `Field(ge=1)` for serial, `Literal["bunker-index"]`, `Literal[3]`, UTC string validators, strict int/bool types, and the following explicit model fields (do not coerce JSON strings to sizes):

```python
# catalogue.py: models use a shared Wire(BaseModel) with the config above.
class Bunker(Wire):
    name: str
    mode: Literal["personal", "group"]

class Signer(Wire):
    id: str
    public_key: str
    algorithm: str
    bits: int
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
    kind: Literal["region-outline", "tile-selection", "sheet-selection",
                  "dem3dep-selection", "fstopo-selection"]
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
    signers: tuple[Signer, ...] = Field(min_length=1)
    engine_version: str | None
    artifacts: tuple[CatalogueArtifact, ...] = Field(min_length=0)
    inputs: tuple[CatalogueInput, ...] = Field(min_length=0)
    deferred: tuple[JsonValue, ...]
    declined: tuple[JsonValue, ...]
    last_run: JsonValue

    def artifact(self, unit: str, name: str, enrolment_id: str | None) -> CatalogueArtifact | None:
        for row in self.artifacts:
            if (row.unit, row.name) != (unit, name):
                continue
            if self.bunker.mode == "personal" or row.share == "all" or row.share == f"owner:{enrolment_id}" and enrolment_id is not None:
                return row
        return None

    def input(self, kind: str, region: str) -> CatalogueInput | None:
        return next((row for row in self.inputs if (row.kind, row.region) == (kind, region)), None)
```

Use these full validation helpers in `parse`, in addition to pydantic field-type validation:

```python
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from pydantic import ValidationError
from hammunition.station import REGION

class CatalogueError(ValueError):
    pass

def valid_enrolment_id(value: str) -> bool:
    # Opaque, not a path; printable ASCII header value, no whitespace/controls.
    return bool(value) and all(33 <= ord(char) <= 126 for char in value)


def safe_relative(value: str, field: str) -> str:
    if (not value or value.startswith("/") or "\\" in value
        or any(c in value for c in "\r\n\x00")
        or any(p in ("", ".", "..") for p in value.split("/"))):
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
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogueError(f"catalogue: invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogueError("catalogue: expected an object")
    if type(value.get("version")) is not int or value["version"] != 3:
        raise CatalogueError(f"version: unsupported Bunker catalogue version {value.get('version')!r}; this reader accepts 3")
    try:
        cat = Catalogue.model_validate_json(raw)
    except ValidationError as exc:
        raise CatalogueError(str(exc)) from exc
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
        for field, actual in (("algorithm", strength.algorithm), ("bits", strength.bits), ("id", strength.fingerprint)):
            if getattr(signer, field) != actual:
                raise CatalogueError(f"{where}.{field}: does not match public_key")
        if strength.hardware_by_type and not signer.hardware:
            raise CatalogueError(f"{where}.hardware: sk keys are hardware by type")
    for n, row in enumerate([*cat.artifacts, *cat.inputs]):
        where = f"{'artifacts' if isinstance(row, CatalogueArtifact) else 'inputs'}[{n}]"
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
            if row.share != "all" and (not row.share.startswith("owner:") or not valid_enrolment_id(row.share.removeprefix("owner:"))):
                raise CatalogueError(f"{where}.share: expected all or owner:<enrolment id>")
            if row.publisher_size is not None and row.publisher_size <= 0:
                raise CatalogueError(f"{where}.publisher_size: must be positive")
            if (row.publisher_name is None) != (row.publisher_size is None):
                raise CatalogueError(f"{where}.publisher_name/publisher_size: must be set or null together")
            if row.publisher_name is not None:
                safe_relative(row.publisher_name, f"{where}.publisher_name")
    return cat
```

The existing Bunker v2 `Entry` was also read from the sibling checkout's `src/bunker/index.py:65-91,110-132`: path, sha256, size, fetched and verified are nullable because failed/unheld entries remain in the catalogue; publisher_url is a string. Preserve these types exactly, as the contract requires. Parser tests must include a failed entry with null held-byte fields and a current entry beside it; parsing succeeds, lookup refuses only the unheld entry. Do not require the whole catalogue to contain only installable entries. Top-level v3 engine_version is nullable measurement metadata, never the format version. List-like wire fields are typed tuples and parsed with `model_validate_json` for an immutable reader model; raw duplicate-key validation still happens first.

Remove the unused `PurePosixPath` import. `JsonValue` is imported from pydantic. Add unit-specific publisher-digest validation in the resolver tasks; a flat contract entry's stored sha256 does not change its `publisher_check`. A path is a stored volume path, whereas artifact requests use `unit/name`: do not incorrectly require those two strings to be identical (dated OSM files are the counterexample).

- [ ] Write the reference with the contract's entire field table, rank table, routes, namespace/principal, group filter and cleartext caveat; explain byte-for-byte signatures, serial equality vs rollback, 30 days and trust tiers. Document the local `mirror.json` store rather than the spec's superseded permanent `mirror-signers`. Add `- Bunker catalogue: reference/bunker-catalogue.md` under Reference / The engine.
- [ ] Run: `.venv/bin/pytest tests/test_bunker_catalogue.py tests/test_site.py -q`; expected PASS. Run `.venv/bin/mypy --strict` and `python3 scripts/check_doc_links.py`; expected exit 0.
- [ ] Commit:

```bash
git add src/hammunition/keystrength.py src/hammunition/catalogue.py tests/bunker_fixtures.py tests/test_bunker_catalogue.py docs/reference/bunker-catalogue.md mkdocs.yml
git commit -m "feat: define Bunker v3 catalogue and key strength reader"
```

### Task 2: A2 — Enrolled signers, owner-aware store and real OpenSSH verification

**Files:**
- Create: `src/hammunition/signers.py`, `tests/test_bunker_signers.py`.
- Modify: `tests/bunker_fixtures.py` (add real signing helper).
- Read: `src/hammunition/paths.py:38-110,145-345` (owner and descriptor directory guards), `src/hammunition/fetch.py:362-435` (exclusive temporary pattern), `src/hammunition/station.py:574-676` (current config storage).

**Interfaces:**
- Consumes: Task 1 `parse`, `Catalogue`, `classify`, `KeyStrength`; `paths.owner_aware_dir`, `open_operator_dir(path: Path, owner: str | None = None) -> int | None`.
- Produces: frozen `EnrolledKey(id: str, public_key: str, algorithm: str, bits: int, hardware: bool, no_touch_required: bool = False)`; frozen `MirrorState(url: str, name: str, mode: str, enrolment_id: str | None, keys: tuple[EnrolledKey, ...], accepted_serial: int, generated: str | None = None)`.
- Produces: `mirror_path(owner: str | None = None) -> Path`, `load_mirror(path: Path | None = None, *, owner: str | None = None) -> MirrorState | None`, `save_mirror(state: MirrorState, path: Path | None = None, *, owner: str | None = None, allow_older: bool = False) -> Path`, `clear_mirror(*, owner: str | None = None) -> None`, `allowed_signers(state: MirrorState) -> str`, `verify(raw: bytes, signatures: Mapping[str, bytes], state: MirrorState, *, require_hardware: bool = False, now: datetime) -> VerifiedCatalogue`, `SignerError(ValueError)`.
- Produces: frozen `VerifiedCatalogue(catalogue: Catalogue, raw: bytes, key: EnrolledKey, strength: KeyStrength, warnings: tuple[str, ...])`; `signed(tmp_path: Path, private: Path, doc: dict[str, object]) -> tuple[bytes, dict[str, bytes]]` test helper.

- [ ] Write the failing real-key verification tests:

```python
# fixture helper added to tests/bunker_fixtures.py

def signed(tmp_path: Path, private: Path, doc: dict[str, object]) -> tuple[bytes, dict[str, bytes]]:
    path = tmp_path / "catalogue.json"
    path.write_bytes(encode(doc))
    path.with_suffix(".json.sig").unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-n", "hammunition-bunker-catalogue",
                    "-f", str(private), str(path)], check=True, capture_output=True)
    return path.read_bytes(), {"catalogue.sig.d/1.sig": path.with_suffix(".json.sig").read_bytes()}
```

```python
from pathlib import Path
# tests/test_bunker_signers.py
from dataclasses import replace
from datetime import UTC, datetime
import pytest
from hammunition.keystrength import classify
from hammunition.signers import EnrolledKey, MirrorState, SignerError, verify, load_mirror, save_mirror
from bunker_fixtures import document, key, signed

@pytest.mark.parametrize("algorithm,bits", [("ed25519", None), ("ecdsa", 384), ("rsa", 4096), ("rsa", 2048)])
def test_real_signatures_and_policy(tmp_path: Path, algorithm: str, bits: int | None) -> None:
    private = key(tmp_path, algorithm, bits)
    public = private.with_suffix(".pub").read_text().strip()
    strength = classify(public)
    enrolled = EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False)
    state = MirrorState("file:///unused", "bunker", "personal", None, (enrolled,), 41)
    raw, sigs = signed(tmp_path, private, document(public))
    verified = verify(raw, sigs, state, now=datetime(2026, 10, 7, tzinfo=UTC))
    assert verified.key == enrolled
    assert bool(verified.strength.warning) == (bits == 2048)
    with pytest.raises(SignerError, match="hardware"):
        verify(raw, sigs, state, require_hardware=True, now=datetime(2026, 10, 7, tzinfo=UTC))
    with pytest.raises(SignerError, match="signature"):
        verify(raw + b" ", sigs, state, now=datetime(2026, 10, 7, tzinfo=UTC))
    with pytest.raises(SignerError, match="accept-older"):
        verify(raw, sigs, replace(state, accepted_serial=43), now=datetime(2026, 10, 7, tzinfo=UTC))
    stale = verify(raw, sigs, state, now=datetime(2026, 11, 8, tzinfo=UTC))
    assert any("30 days" in w for w in stale.warnings)
    stored = tmp_path / "config" / "mirror.json"
    save_mirror(state, stored)
    assert stored.stat().st_mode & 0o777 == 0o600
    assert load_mirror(stored) == state
    save_mirror(replace(state, accepted_serial=43), stored)
    with pytest.raises(SignerError, match="serial"):
        save_mirror(state, stored)
    assert load_mirror(stored).accepted_serial == 43
```

Add tests for same serial (accepted), exact 30-day boundary (no warning until older), mismatching Bunker name, forged key id/public line, missing/bad signatures followed by a good enrolled signature, an un-enrolled valid key, a file key claiming hardware when the stored key is not affirmed, and an affirmed PIV-shaped file key (`hardware=True` locally) passing the policy without a token. Run concurrent writers under a shared temporary config; assert max serial wins. Plant a symlink for `mirror.json` and the lock file and assert refusal, no target touched. Mock owner passwd/euid as in `tests/test_isolation.py`; assert the temporary and final file are operator-owned under sudo.

- [ ] Run: `.venv/bin/pytest tests/test_bunker_signers.py -q`. Expected FAIL: `ModuleNotFoundError: No module named 'hammunition.signers'`.

- [ ] Add the complete security-state dataclasses before implementing the verifier:

```python
from dataclasses import dataclass, replace
from typing import ClassVar
from pydantic import ConfigDict

class SignerError(ValueError):
    pass

@dataclass(frozen=True)
class EnrolledKey:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")
    id: str
    public_key: str
    algorithm: str
    bits: int
    hardware: bool
    no_touch_required: bool = False

@dataclass(frozen=True)
class MirrorState:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")
    url: str
    name: str
    mode: str
    enrolment_id: str | None
    keys: tuple[EnrolledKey, ...]
    accepted_serial: int
    generated: str | None = None

@dataclass(frozen=True)
class VerifiedCatalogue:
    catalogue: Catalogue
    raw: bytes
    key: EnrolledKey
    strength: KeyStrength
    warnings: tuple[str, ...]
```

- [ ] Implement full verification and rendering functions:

```python
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import tempfile

NAMESPACE = "hammunition-bunker-catalogue"


def allowed_signers(state: MirrorState) -> str:
    lines: list[str] = []
    for key in state.keys:
        # OpenSSH's allowed-signers format has no touch option: measured on
        # OpenSSH 10.3, "no-touch-required" there is "bad options: unknown key
        # option". no_touch_required stays display-only metadata.
        options = f'namespaces="{NAMESPACE}"'
        lines.append(f"bunker:{state.name} {options} {key.public_key.strip()}")
    return "\n".join(lines) + "\n"


def verify(raw: bytes, signatures: Mapping[str, bytes], state: MirrorState,
           *, require_hardware: bool = False, now: datetime) -> VerifiedCatalogue:
    cat = parse(raw)
    if cat.bunker.name != state.name:
        raise SignerError("bunker.name: differs from the enrolled Bunker; enrol this URL explicitly")
    if cat.serial < state.accepted_serial:
        raise SignerError(f"serial {cat.serial} is older than accepted serial {state.accepted_serial}; restore confirmed? hammunition mirror accept-older")
    enrolled = {k.id: k for k in state.keys}
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hammunition-signers-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed-signers"
        sigpath = root / "catalogue.sig"
        for signer in cat.signers:
            key = enrolled.get(signer.id)
            if key is None:
                continue
            if key.public_key.split()[:2] != signer.public_key.split()[:2]:
                failures.append(f"{signer.id}: public_key differs")
                continue
            if require_hardware and not key.hardware:
                continue
            signature = signatures.get(signer.signature)
            if signature is None:
                failures.append(f"{signer.id}: missing signature")
                continue
            allowed.write_text(allowed_signers(replace(state, keys=(key,))), encoding="utf-8")
            allowed.chmod(0o600)
            sigpath.write_bytes(signature)
            sigpath.chmod(0o600)
            try:
                result = subprocess.run(
                    ["ssh-keygen", "-Y", "verify", "-f", str(allowed),
                     "-I", f"bunker:{state.name}", "-n", NAMESPACE, "-s", str(sigpath)],
                    input=raw, capture_output=True, check=False, timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SignerError("signature verification needs working OpenSSH 8.2+") from exc
            if result.returncode:
                failures.append(f"{signer.id}: signature did not verify")
                continue
            strength = classify(key.public_key)
            warnings = [strength.warning] if strength.warning is not None else []
            generated = datetime.fromisoformat(cat.generated.replace("Z", "+00:00"))
            if now.astimezone(UTC) - generated > timedelta(days=30):
                warnings.append("Bunker catalogue is older than 30 days")
            return VerifiedCatalogue(cat, raw, key, strength, tuple(warnings))
    policy = " with an enrolled hardware key" if require_hardware else ""
    raise SignerError("no enrolled signature verified" + policy + ": " + "; ".join(failures))
```

Keep `CatalogueError` distinct but caught together with `SignerError` by the CLI. Use local `replace` from dataclasses, never a wire `hardware` flag to decide policy. Add dataclasses exactly as defined in Interfaces. Keep the verified bytes and parsed model together; later tasks do not reserialize them for verification.

The store is JSON generated from `asdict(state)`, loaded through explicit strict models for `EnrolledKey`/`MirrorState` (pydantic `TypeAdapter`, forbid extra local fields). Classify every stored key and validate its id/algorithm/bits; require nonempty keys, a validated name/URL/id, boolean hardware/no-touch and nonnegative `accepted_serial`. Accepted serial 0 is allowed only locally before first verification. A corrupt store is a refusal, never equivalent to no enrolment.

Full path helper:

```python
def mirror_path(owner: str | None = None) -> Path:
    return owner_aware_dir(xdg_var="XDG_CONFIG_HOME", home_relative=(".config",), owner=owner) / "mirror.json"
```

For `load_mirror`, `save_mirror` and `clear_mirror`, hold `open_operator_dir(parent, owner)` through the operation; use relative `dir_fd` operations when it returns a descriptor, otherwise open the parent directory with `O_DIRECTORY|O_NOFOLLOW`. Lock `mirror.lock` (`O_CREAT|O_RDWR|O_NOFOLLOW`, 0600, `fcntl.flock(LOCK_EX)`). Read `mirror.json` with `O_NOFOLLOW`; reject non-regular files, modes other than 0600 and unexpected owners. Under the lock, compare the existing serial before any write; reject a lower serial unless `allow_older`. Write `asdict(state)` to an exclusive randomly named 0600 temporary, `fchown` it to the owner when root, fsync, `os.replace` by descriptor, then fsync the parent. Unlink only `mirror.json` on clear (and the obsolete `mirror-signers` if present); keep the lock inode to avoid a race between locked processes. Test that clear is idempotent. Do not use `station.save_station` for the security store: it follows an existing symlink and is not atomic.

Complete store implementation (all helpers defined here; import `contextmanager`, `Iterator`, `asdict`, `fcntl`, `json`, `os`, `pwd`, `secrets`, `stat`, `TypeAdapter`, `ValidationError`, `ConfigDict`):

```python
# Add __pydantic_config__ = ConfigDict(strict=True, extra="forbid") as a
# ClassVar to both store dataclasses, so TypeAdapter cannot coerce fields.

def validate_state(state: MirrorState) -> MirrorState:
    Station(mirror=state.url)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", state.name):
        raise SignerError("mirror.name is invalid")
    if state.mode not in ("personal", "group") or state.accepted_serial < 0 or not state.keys:
        raise SignerError("mirror mode/serial/keys are invalid")
    if state.enrolment_id is not None and not valid_enrolment_id(state.enrolment_id):
        raise SignerError("mirror.enrolment_id is invalid")
    if len({k.id for k in state.keys}) != len(state.keys):
        raise SignerError("mirror.keys contains duplicate ids")
    for key in state.keys:
        measured = classify(key.public_key)
        if (key.id, key.algorithm, key.bits) != (measured.fingerprint, measured.algorithm, measured.bits):
            raise SignerError("mirror.keys metadata differs from public_key")
        if measured.hardware_by_type and not key.hardware:
            raise SignerError("mirror.keys sk key must be hardware")
        if key.no_touch_required and not measured.hardware_by_type:
            raise SignerError("mirror.keys no-touch-required needs an sk key")
    if state.generated is not None:
        utc(state.generated, "mirror.generated")
    return state


def store_uid(owner: str | None) -> tuple[int, int]:
    if owner is not None and os.geteuid() == 0:
        entry = pwd.getpwnam(owner)
        return entry.pw_uid, entry.pw_gid
    return os.geteuid(), os.getegid()


@contextmanager
def store_lock(path: Path, owner: str | None) -> Iterator[int]:
    kept = open_operator_dir(path.parent, owner)
    directory = kept if kept is not None else os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = -1
    try:
        created = False
        try:
            lock = os.open("mirror.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            created = True
        except FileExistsError:
            lock = os.open("mirror.lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
        uid, gid = store_uid(owner)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o600:
            raise SignerError("mirror.lock must be a regular 0600 file")
        if created and os.geteuid() == 0:
            os.fchown(lock, uid, gid)
        elif info.st_uid != uid:
            raise SignerError("mirror.lock belongs to another account")
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory
    except OSError as exc:
        raise SignerError(f"mirror store: {exc}") from exc
    finally:
        if lock >= 0:
            os.close(lock)
        os.close(directory)


def read_state(directory: int, filename: str, owner: str | None) -> MirrorState | None:
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        uid, _ = store_uid(owner)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o600 or info.st_uid != uid:
            raise SignerError("mirror.json must be operator-owned, regular and 0600")
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise SignerError("mirror.json exceeds 1 MiB")
    try:
        return validate_state(TypeAdapter(MirrorState).validate_json(raw))
    except (ValidationError, ValueError) as exc:
        raise SignerError(f"mirror.json: {exc}") from exc


def load_mirror(path: Path | None = None, *, owner: str | None = None) -> MirrorState | None:
    target = path if path is not None else mirror_path(owner)
    if not target.parent.exists():
        return None
    with store_lock(target, owner) as directory:
        return read_state(directory, target.name, owner)


def save_mirror(state: MirrorState, path: Path | None = None, *,
                owner: str | None = None, allow_older: bool = False) -> Path:
    state = validate_state(state)
    target = path if path is not None else mirror_path(owner)
    with store_lock(target, owner) as directory:
        old = read_state(directory, target.name, owner)
        if (old is not None and (old.url, old.name) == (state.url, state.name)
            and state.accepted_serial < old.accepted_serial and not allow_older):
            raise SignerError("mirror serial cannot be lowered; hammunition mirror accept-older")
        temporary = f".mirror-{secrets.token_hex(12)}"
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                if os.geteuid() == 0:
                    os.fchown(stream.fileno(), *store_uid(owner))
                stream.write((json.dumps(asdict(state), ensure_ascii=False) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    return target


def clear_mirror(*, owner: str | None = None) -> None:
    target = mirror_path(owner)
    if not target.parent.exists():
        return
    with store_lock(target, owner) as directory:
        for name in (target.name, "mirror-signers"):
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.fsync(directory)
```

Test the lock ownership branch: an existing lock belonging to another account refuses, whereas only a just-created root-owned lock is handed to the operator. Preserve the exclusive lock for all read/modify/write paths. Under concurrent verified loads, serial advancement should merge the greatest serial rather than fail a legitimate lower writer; use a dedicated `advance_mirror(state, serial, generated)` locked updater and keep `save_mirror` refusal for explicit lower-state saves. The updater is defined in Task 4 below.

- [ ] Run: `.venv/bin/pytest tests/test_bunker_catalogue.py tests/test_bunker_signers.py tests/test_isolation.py -q`; expected PASS. `.venv/bin/mypy --strict`; expected exit 0.
- [ ] Commit:

```bash
git add src/hammunition/signers.py tests/bunker_fixtures.py tests/test_bunker_signers.py
git commit -m "feat: verify enrolled Bunker signers and protect serial state"
```

### Task 3: A3 — Mirror CLI, fingerprint consent, station policy and JSON

**Files:**
- Create: `src/hammunition/mirror.py`, `src/hammunition/interface/mirror.py`, `tests/test_mirror_cli.py`.
- Modify: `src/hammunition/cli/main.py:403-417,496-837,8051-8113,8660-8680,8890-8945,9116-9157`; `src/hammunition/consent/gate.py:195-305`, `src/hammunition/consent/__init__.py:1-40`; `src/hammunition/station.py:106-112,169-212,230-275,340-350,478-516,587-660,677-744`; `src/hammunition/interface/station.py:1-257` (stored mirror value and builders); `scripts/gen_json_reference.py:85-125` (command list); `docs/reference/cli.md` (generated blocks), `docs/reference/json-interface.md` (regenerate).
- Test: `tests/test_mirror_cli.py`, `tests/test_station.py`, `tests/test_json_station_hardware.py`, `tests/test_docs_json_interface.py`, `tests/test_docs_generated.py`.

**Interfaces:**
- Consumes: Task 2 store/verification, `consent.ConsentRecord`, `Decision`, `ConsentDeclined`, `ConsentUnavailable`; CLI `operator(args) -> str`, `envelope.emit`, `envelope.wanted`, `EXIT_OK`, `EXIT_UNPLANNABLE`.
- Produces: `Station.mirror_require_hardware_key: bool = False` (excluded from templates); `resolve_mirror_consent(fingerprint: str, disclosure: str, *, prompt: Callable[[str], str] | None, actor: str | None = None, now: Callable[[], datetime] | None = None) -> ConsentRecord`.
- Produces: `Candidate(raw: bytes, signatures: dict[str, bytes])`, `candidate_read(base: str, relative: str, enrolment_id: str | None, *, max_bytes: int) -> bytes`, `read_candidate(url: str, enrolment_id: str | None) -> Candidate` (HTTP implementation here, extended to file in Task 4); `enrol(candidate: Candidate, url: str, enrolment_id: str | None, *, choose: Callable[[str], str] | None, affirm_hardware: Callable[[str], bool] | None, owner: str | None, require_hardware: bool, now: datetime) -> MirrorState`.
- Produces: CLI `cmd_mirror_enrol(args: argparse.Namespace) -> int`, `cmd_mirror_status(args: argparse.Namespace) -> int`, `cmd_mirror_accept_older(args: argparse.Namespace) -> int`; parser `mirror enrol URL [--enrolment-id ID]`, `mirror status [--json]`, `mirror accept-older`, `station set --mirror-require-hardware-key` / `--no-mirror-require-hardware-key`.
- Produces: `MirrorKeyView(id: str, algorithm: str, bits: int, hardware: bool, weak: bool, warning: str | None)`, `MirrorDocument(Strict)` (`KIND="mirror"`, `url: str | None`, `name: str | None`, `mode: str | None`, `enrolment_id: str | None`, `accepted_serial: int | None`, `generated: str | None`, `age_days: int | None`, `require_hardware: bool`, `keys: tuple[MirrorKeyView, ...]`, `warnings: tuple[str, ...]`), `build_mirror(state: MirrorState | None, *, require_hardware: bool, now: datetime) -> MirrorDocument`, `render_mirror(doc: MirrorDocument) -> list[str]`.

- [ ] Write failing tests with `main()` and monkeypatched `read_candidate`, keeping station/store under each test's XDG config:

```python
import pytest
from pathlib import Path
import json
import importlib
cli = importlib.import_module("hammunition.cli.main")
from hammunition import mirror, signers
from hammunition.station import Station, load_station, save_station
from bunker_fixtures import document, key, signed


def test_enrol_requires_fingerprint_and_clear_removes_trust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    private = key(tmp_path, "rsa", 2048)
    public = private.with_suffix(".pub").read_text().strip()
    raw, sigs = signed(tmp_path, private, document(public))
    candidate = mirror.Candidate(raw, sigs)
    monkeypatch.setattr(mirror, "read_candidate", lambda *a: candidate)
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    fingerprint = signers.classify(public).fingerprint
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    assert cli.main(["mirror", "enrol", "http://bunker.invalid/"]) != 0
    assert signers.load_mirror() is None
    monkeypatch.setattr("builtins.input", lambda *a: fingerprint)
    assert cli.main(["mirror", "enrol", "http://bunker.invalid/"]) == 0
    assert load_station().mirror == "http://bunker.invalid/"
    assert signers.load_mirror().accepted_serial == 42
    assert cli.main(["station", "set", "--clear-mirror"]) == 0
    assert signers.load_mirror() is None


def test_status_json_is_one_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert cli.main(["mirror", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "hammunition/1"
    assert result["kind"] == "mirror" and result["url"] is None
```

Add tests that `--yes` cannot enrol or accept-older; noninteractive enrol refuses; wrong/lowercase fingerprint is not normalized (OpenSSH SHA256 fingerprints are case-sensitive); advertised algorithm/bits and RSA warning printed before consent; non-sk `hardware:true` with declined hardware affirmation stores a file key; affirmation stores hardware; bad signature writes neither station nor store. Re-enrolment to the same URL/name preserves old serial, retains only explicitly accepted keys from the new catalogue, and refuses a lower catalogue. Changing URL/name is explicit new enrolment, never automatic trust reuse. `accept-older` prints old/new serial and backup-restoration explanation, requires typed `yes`, verifies the candidate first using a transient state whose serial is 0, then calls `save_mirror(..., allow_older=True)`; no bare reset command bypasses a bad signature. Preserve station policy on clear; clear removes URL/keys/id/serial together. Validate local state/URL consistency on status and install: a manually changed `station.mirror` requires re-enrolment before catalogue use.

- [ ] Run: `.venv/bin/pytest tests/test_mirror_cli.py -q`; expected FAIL: import `hammunition.mirror` missing.
- [ ] Implement typed consent without inventing a permissive `--yes` or environment shortcut:

```python
def resolve_mirror_consent(fingerprint: str, disclosure: str, *,
                           prompt: Callable[[str], str] | None,
                           actor: str | None = None,
                           now: Callable[[], datetime] | None = None) -> ConsentRecord:
    if prompt is None:
        raise ConsentUnavailable("Bunker enrolment requires typing a disclosed fingerprint at a terminal; --yes cannot answer it")
    text = disclosure + f"\nType this fingerprint to trust it: {fingerprint}"
    if prompt(text).strip() != fingerprint:
        raise ConsentDeclined("Bunker signer fingerprint was not affirmed")
    return ConsentRecord(
        profile="bunker-signer", decision=Decision.interactive, risk_categories=(),
        disclosure_text=text, disclosure_sha256=hashlib.sha256(text.encode()).hexdigest(),
        env_var="", timestamp=(now or (lambda: datetime.now(UTC)))(), actor=actor,
        extra={"kind": "bunker_signer", "key_fingerprint": fingerprint},
    )
```

Implement the complete enrollment function (multiple fingerprints may be comma-separated in the one typed response; each is recorded separately). `display_signer(signer: Signer) -> str` is the shared algorithm/bits/hardware/fingerprint/warning formatter; implement it below, not with private-key output:

```python
@dataclass(frozen=True)
class Candidate:
    raw: bytes
    signatures: dict[str, bytes]


def display_signer(signer: Signer) -> str:
    strength = classify(signer.public_key)
    origin = "hardware by type" if strength.hardware_by_type else "hardware claimed, needs affirmation" if signer.hardware else "file key"
    line = f"{signer.id}: {strength.algorithm}, {strength.bits} bits, {origin}"
    return line + (f"; {strength.warning}" if strength.warning else "")


def enrol(candidate: Candidate, url: str, enrolment_id: str | None, *,
          choose: Callable[[str], str] | None,
          affirm_hardware: Callable[[str], bool] | None,
          owner: str | None, require_hardware: bool, now: datetime) -> MirrorState:
    normalized = Station(mirror=url).mirror
    if normalized is None:
        raise SignerError("mirror URL is missing")
    parsed = parse(candidate.raw)
    ordered = sorted(parsed.signers, key=lambda signer: (classify(signer.public_key).rank, signer.id))
    disclosure = "\n".join(display_signer(signer) for signer in ordered)
    disclosure += "\nA signature means this Bunker recorded this; it does not upgrade trust."
    if choose is None:
        raise ConsentUnavailable("Bunker enrolment requires typing fingerprints at a terminal; --yes cannot answer it")
    answer = choose(disclosure + "\nType one or more fingerprints, comma-separated: ").strip()
    ids = tuple(dict.fromkeys(part.strip() for part in answer.split(",") if part.strip()))
    advertised = {signer.id: signer for signer in ordered}
    if not ids or any(identity not in advertised for identity in ids):
        raise ConsentDeclined("Bunker signer fingerprint was not affirmed")
    keys: list[EnrolledKey] = []
    for identity in ids:
        signer = advertised[identity]
        resolve_mirror_consent(identity, display_signer(signer), prompt=lambda _: identity, actor=owner)
        strength = classify(signer.public_key)
        hardware = strength.hardware_by_type
        if not hardware and signer.hardware and affirm_hardware is not None:
            hardware = affirm_hardware(
                "The Bunker claims this key is hardware-backed; the algorithm cannot prove that. "
                "Affirm that you verified its hardware origin? Type yes: "
            )
        keys.append(EnrolledKey(identity, signer.public_key, strength.algorithm, strength.bits, hardware))
    old = load_mirror(owner=owner)
    serial = old.accepted_serial if old is not None and (old.url, old.name) == (normalized, parsed.bunker.name) else 0
    state = MirrorState(normalized, parsed.bunker.name, parsed.bunker.mode, enrolment_id,
                        tuple(keys), serial)
    verified = verify(candidate.raw, candidate.signatures, state,
                      require_hardware=require_hardware, now=now)
    state = replace(state, accepted_serial=verified.catalogue.serial,
                    generated=verified.catalogue.generated)
    current = load_station(owner=owner)
    save_station(replace(current, mirror=normalized), owner=owner)
    save_mirror(state, owner=owner)
    return state


def candidate_read(base: str, relative: str, enrolment_id: str | None, *, max_bytes: int) -> bytes:
    base = _check_mirror(base)
    if urlsplit(base).scheme not in ("http", "https"):
        raise BackendError("this enrolment transport requires an HTTP(S) Bunker")
    relative = safe_relative(relative, "candidate path")
    if enrolment_id is not None and not valid_enrolment_id(enrolment_id):
        raise BackendError("invalid enrolment id")
    headers = {"User-Agent": "hammunition"}
    if enrolment_id is not None:
        headers["X-Hammunition-Enrolment"] = enrolment_id
    url = base.rstrip("/") + "/" + "/".join(quote(part, safe="") for part in relative.split("/"))
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(),
                    urllib.request.HTTPErrorProcessor(), urllib.request.HTTPDefaultErrorHandler()):
        opener.add_handler(handler)
    try:
        response = opener.open(urllib.request.Request(url, headers=headers), timeout=MIRROR_TIMEOUT)
        if response is None:
            raise BackendError("no candidate HTTP handler")
        with response:
            raw: bytes = response.read(max_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise BackendError(f"cannot fetch Bunker candidate {relative}: {exc}") from exc
    if len(raw) > max_bytes:
        raise BackendError(f"Bunker candidate {relative} exceeds {max_bytes} bytes")
    return raw


def read_candidate(url: str, enrolment_id: str | None) -> Candidate:
    raw = candidate_read(url, "catalogue.json", enrolment_id, max_bytes=32 * 1024 * 1024)
    parsed = parse(raw)
    signatures: dict[str, bytes] = {}
    for signer in parsed.signers:
        try:
            signatures[signer.signature] = candidate_read(url, signer.signature, enrolment_id, max_bytes=64 * 1024)
        except BackendError:
            continue
    return Candidate(raw, signatures)
```

For Task 3 use the HTTP-only candidate_read above inside mirror.py; Task 4 moves that IO to MirrorTransport and removes candidate_read. Tests inject `read_candidate` while testing consent, and Task 4 adds actual HTTP/file tests. Do not import a nonexistent future module into the Task 3 implementation. The three functions above use normal local imports for parser/store/types and read no private key.

Record the returned ConsentRecords via the existing transaction/runlog route; do not discard them as the minimal function above does. A store-write failure after station-save is reported as a failed enrolment; the URL/store mismatch is a refusal on the next run, not usable partial trust. No signature failure writes either file. Add a restoration test and preserve the prior mirror URL on a failed store write using `save_station(current)` when safe; never lower the old store's serial as cleanup.

 For non-sk keys claiming hardware, the extra disclosure is “The Bunker claims this key is hardware-backed; the algorithm cannot prove that. Affirm that you verified its hardware origin?”; store true only after a separate typed `yes`. A file key never inherits this claim on refresh. `hardware_by_type` is automatically true for sk keys. For sk signatures without a touch, try verification with the default allowed line first; if it fails, explicitly disclose and ask whether to permit `no-touch-required`, then try that option. This is a local enrollment option; do not invent a required new wire field. Record the option only after its signature verifies. Never weaken an existing key silently.

Implement initial `read_candidate` with a no-redirect HTTP(S)-only `OpenerDirector`, `MIRROR_TIMEOUT`, bounded reads of catalogue (32 MiB) and each signature (64 KiB). Run Task 1 `parse` before requesting the validated signature paths; use every request's `X-Hammunition-Enrolment` header when id is supplied. An id is an opaque nonempty printable value without whitespace, CR/LF/NUL; percent-quote URL path segments, never interpolate an id into a path. Task 4 consolidates this into the reusable transport.

`mirror status` reads stored data, not the network; “catalogue age” is age of the last verified stored `generated`. Missing store prints “not enrolled”, not a failure. Construct text and JSON from `MirrorDocument` with `described()` fields. Decorate status with `@envelope.json_capable()` and add JSON parser flags through `_add_json_flag`. Enrol/accept-older are interactive mutation commands and have no JSON form; the envelope returns one error document when asked. Station policy appears in `StationDocument`, `StationSetDocument` and station round-trip tests. Carry it through both `prompt_for` constructors, `_station_for` and `_cmd_station_set`; `_KNOWN_KEYS` derives it automatically. Add strict bool validation on load, never `bool("false")`. Use `dataclasses.replace(current, **accepted)` for station reconstruction only once accepted values are validated; this avoids dropping the new policy in unrelated CLI branches.

Full status builder and CLI handlers (import all Task 1–4 classes and consent exceptions in their owning modules). Fields on MirrorDocument use `described()` as specified in Interfaces:

```python
# interface/mirror.py
from dataclasses import dataclass
from typing import ClassVar
from hammunition.interface.envelope import Strict, described

@dataclass(frozen=True)
class MirrorKeyView(Strict):
    id: str = described("SHA256 fingerprint of the enrolled public key")
    algorithm: str = described("OpenSSH algorithm measured from the public key")
    bits: int = described("key size measured by ssh-keygen")
    hardware: bool = described("hardware by sk type or explicitly affirmed by the operator")
    weak: bool = described("true for RSA of 2048 bits or fewer")
    warning: str | None = described("shared weak-key warning, null for a strong key")

@dataclass(frozen=True)
class MirrorDocument(Strict):
    KIND: ClassVar[str] = "mirror"
    url: str | None = described("enrolled mirror URL; null when no Bunker is enrolled")
    name: str | None = described("enrolled Bunker name")
    mode: str | None = described("personal or group mode from the last verified catalogue")
    enrolment_id: str | None = described("opaque group sharing id; not proof of laptop identity")
    accepted_serial: int | None = described("highest accepted serial, except explicit backup restoration")
    generated: str | None = described("UTC generation time from the last verified catalogue")
    age_days: int | None = described("age of last verified metadata, without contacting the Bunker")
    require_hardware: bool = described("station hardware-signature policy, off by default")
    keys: tuple[MirrorKeyView, ...] = described("enrolled key strength and hardware assertions")
    warnings: tuple[str, ...] = described("weak-key and catalogue-age warnings")


def build_mirror(state: MirrorState | None, *, require_hardware: bool,
                 now: datetime) -> MirrorDocument:
    if state is None:
        return MirrorDocument(None, None, None, None, None, None, None,
                              require_hardware, (), ())
    keys: list[MirrorKeyView] = []
    warnings: list[str] = []
    for key in state.keys:
        strength = classify(key.public_key)
        keys.append(MirrorKeyView(key.id, strength.algorithm, strength.bits, key.hardware,
                                  strength.weak, strength.warning))
        if strength.warning:
            warnings.append(strength.warning)
    age: int | None = None
    if state.generated is not None:
        generated = datetime.fromisoformat(state.generated.replace("Z", "+00:00"))
        age = max(0, int((now.astimezone(UTC) - generated).total_seconds() // 86400))
        if now.astimezone(UTC) - generated > timedelta(days=30):
            warnings.append("Bunker catalogue is older than 30 days")
    return MirrorDocument(state.url, state.name, state.mode, state.enrolment_id,
                          state.accepted_serial, state.generated, age, require_hardware,
                          tuple(keys), tuple(warnings))


def render_mirror(doc: MirrorDocument) -> list[str]:
    if doc.url is None:
        return ["No Bunker enrolled; hammunition mirror enrol URL"]
    lines = [f"Bunker {doc.name}: {doc.url} ({doc.mode})",
             f"Accepted serial: {doc.accepted_serial}; catalogue age: {doc.age_days} days",
             f"Hardware key required: {doc.require_hardware}"]
    for key in doc.keys:
        lines.append(f"{key.id}: {key.algorithm}, {key.bits} bits, {'hardware' if key.hardware else 'file key'}")
        if key.warning:
            lines.append(key.warning)
    lines += [warning for warning in doc.warnings if warning not in lines]
    return lines

# cli/main.py

def cmd_mirror_enrol(args: argparse.Namespace) -> int:
    from hammunition import mirror
    from hammunition.signers import SignerError
    user = operator(args) or None
    try:
        station = load_station(owner=user)
        candidate = mirror.read_candidate(args.url, args.enrolment_id)
        interactive = is_interactive()
        mirror.enrol(candidate, args.url, args.enrolment_id,
                      choose=input if interactive else None,
                      affirm_hardware=(lambda text: input(text).strip() == "yes") if interactive else None,
                      owner=user, require_hardware=station.mirror_require_hardware_key,
                      now=datetime.now(UTC))
    except (SignerError, CatalogueError, BackendError, StationError, ConsentUnavailable, ConsentDeclined) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    print("Bunker enrolled; hammunition mirror status shows accepted keys and serial")
    return EXIT_OK


@envelope.json_capable()
def cmd_mirror_status(args: argparse.Namespace) -> int:
    from hammunition.interface.mirror import build_mirror, render_mirror
    from hammunition.signers import load_mirror, SignerError
    try:
        user = operator(args) or None
        station = load_station(owner=user)
        state = load_mirror(owner=user)
        doc = build_mirror(state, require_hardware=station.mirror_require_hardware_key,
                           now=datetime.now(UTC))
    except (SignerError, StationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    if envelope.wanted(args):
        envelope.emit(doc)
    else:
        for line in render_mirror(doc):
            print(line)
    return EXIT_OK


def cmd_mirror_accept_older(args: argparse.Namespace) -> int:
    from hammunition.mirror import read_candidate
    from hammunition.signers import load_mirror, save_mirror, verify, SignerError
    user = operator(args) or None
    try:
        state = load_mirror(owner=user)
        if state is None:
            raise SignerError("no Bunker enrolled; hammunition mirror enrol URL")
        station = load_station(owner=user)
        candidate = read_candidate(state.url, state.enrolment_id)
        checked = verify(candidate.raw, candidate.signatures, replace(state, accepted_serial=0),
                         require_hardware=station.mirror_require_hardware_key, now=datetime.now(UTC))
        text = (f"Bunker {state.name}: accepted serial {state.accepted_serial}, restored catalogue "
                f"serial {checked.catalogue.serial}. Confirm that this Bunker was restored from "
                "a trusted backup. Type yes to accept the older serial: ")
        if not is_interactive():
            raise ConsentUnavailable("accept-older requires typed yes at a terminal; --yes cannot answer it")
        if input(text).strip() != "yes":
            raise ConsentDeclined("older Bunker catalogue was not accepted")
        save_mirror(replace(state, accepted_serial=checked.catalogue.serial,
                            generated=checked.catalogue.generated), owner=user, allow_older=True)
    except (SignerError, CatalogueError, BackendError, StationError, ConsentUnavailable, ConsentDeclined) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNPLANNABLE
    print("Restored catalogue serial accepted")
    return EXIT_OK
```

Parser insertion in build_parser:

```python
p_mirror = sub.add_parser("mirror", help="enrol and inspect a signed Bunker catalogue")
mirror_sub = p_mirror.add_subparsers(dest="mirror_command", required=True)
p_enrol = mirror_sub.add_parser("enrol", help="trust explicitly affirmed Bunker signers")
p_enrol.add_argument("url")
p_enrol.add_argument("--enrolment-id")
p_enrol.set_defaults(func=cmd_mirror_enrol)
p_status = mirror_sub.add_parser("status", help="show stored Bunker trust without contacting it")
_add_json_flag(p_status, top=False)
p_status.set_defaults(func=cmd_mirror_status)
p_older = mirror_sub.add_parser("accept-older", help="affirm a restored catalogue after a backup")
p_older.set_defaults(func=cmd_mirror_accept_older)
```

`build_parser` uses `sub = parser.add_subparsers(dest="command", required=False)` at lines 8075–8078; insert into that existing object. Add the mutual-exclusive station hardware policy group and parsed defaults False/None consistently with the other boolean setting flags. Neither mirror mutation command gets a `--yes` option. Global --json still returns an error document rather than performing mutation.

`docs/reference/cli.md` is hand-written (there is no CLI-reference generator, and building one is out of scope for #381). Add a hand-written `## mirror` section for `mirror enrol`, `mirror status` and `mirror accept-older` in the page's existing style, add `--offline` to the `install` and `update` sections and `--mirror-require-hardware-key` / `--no-mirror-require-hardware-key` to `station set`, each naming its JSON kind where it prints one. `scripts/check_doc_links.py` must still pass.

- [ ] Run:

```bash
python3 scripts/gen_json_reference.py
python3 scripts/gen_station_settings.py
python3 scripts/check_doc_links.py
python3 scripts/gen_json_reference.py --check
python3 scripts/gen_station_settings.py --check
.venv/bin/pytest tests/test_mirror_cli.py tests/test_station.py tests/test_json_station_hardware.py tests/test_docs_json_interface.py tests/test_docs_generated.py -q
```

Expected PASS; no `--check` writes. Update station JSON goldens only via the existing `HAMMUNITION_UPDATE_GOLDEN=1` convention and inspect their diffs.
- [ ] Commit:

```bash
git add src/hammunition/mirror.py src/hammunition/interface/mirror.py src/hammunition/interface/station.py src/hammunition/cli/main.py src/hammunition/consent/gate.py src/hammunition/consent/__init__.py src/hammunition/station.py tests/test_mirror_cli.py tests/test_station.py tests/test_json_station_hardware.py tests/test_docs_generated.py tests/fixtures/json scripts/gen_json_reference.py docs/reference/cli.md docs/reference/json-interface.md docs/guides/station-settings.md
git commit -m "feat: enrol Bunker signers with typed fingerprint consent"
```

### Task 4: A4 — One verified catalogue and transport per run

**Files:**
- Create: `src/hammunition/mirror_transport.py`, `tests/test_bunker_transport.py`.
- Modify: `src/hammunition/mirror.py` (Task 3 `read_candidate`), `src/hammunition/fetch.py:211-253,435-470`, `src/hammunition/station.py:169-210`, `src/hammunition/cli/main.py:4655-4695` (transport construction).
- Test: `tests/test_bunker_transport.py`, `tests/test_fetch.py`, `tests/test_mirror_cli.py`.

**Interfaces:**
- Consumes: Tasks 1–3 `safe_relative`, `Candidate`, `verify`, store; existing `fetch.Transport`, `MirrorPath`, `mirror_url`.
- Produces: `MirrorTransport(base: str, enrolment_id: str | None = None)` implementing `open(url: str) -> ContextManager[IO[bytes]]`; `read(relative: str, *, max_bytes: int) -> bytes`; `read_catalogue(source: MirrorTransport) -> bytes`; `load_catalogue(state: MirrorState, *, require_hardware: bool, now: datetime, transport: MirrorTransport | None = None, owner: str | None = None) -> VerifiedCatalogue`.
- Produces: `VerifiedCatalogue` stays the Task 2 type; the CLI creates it once and shares that same object with all resolvers and fetchers. Cache only within this run, not a silently reused persisted unsigned candidate.

- [ ] Write failing transport tests over loopback and a temporary export directory:

```python
from pathlib import Path
from datetime import UTC, datetime
from hammunition.mirror_transport import MirrorTransport, load_catalogue
from hammunition.signers import EnrolledKey, MirrorState
from hammunition.keystrength import classify
from bunker_fixtures import document, key, signed


def test_file_export_verifies_exact_bytes(tmp_path: Path) -> None:
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public))
    export = tmp_path / "export"
    (export / "catalogue.sig.d").mkdir(parents=True)
    (export / "catalogue.json").write_bytes(raw)
    (export / "catalogue.sig.d/1.sig").write_bytes(signatures["catalogue.sig.d/1.sig"])
    strength = classify(public)
    state = MirrorState(export.as_uri(), "bunker", "personal", None,
                        (EnrolledKey(strength.fingerprint, public, strength.algorithm,
                                     strength.bits, False),), 0)
    got = load_catalogue(state, require_hardware=False,
                         now=datetime(2026, 10, 7, tzinfo=UTC))
    assert got.raw == raw and got.catalogue.serial == 42
```

Add the concrete concurrent serial test (in Task 4, which defines advance_mirror):

```python
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from hammunition.signers import EnrolledKey, MirrorState, save_mirror, load_mirror, advance_mirror
from hammunition.keystrength import classify
from bunker_fixtures import key


def test_concurrent_verifications_preserve_highest_serial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    strength = classify(public)
    enrolled = EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False)
    state = MirrorState("http://bunker.invalid", "bunker", "personal", None, (enrolled,), 42)
    save_mirror(state)
    def advance(serial: int) -> None:
        advance_mirror(state, serial, "2026-10-07T12:00:00Z")
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(advance, [44, 43]))
    current = load_mirror()
    assert current is not None and current.accepted_serial == 44
```

Use `ThreadingHTTPServer` bound to `127.0.0.1` in a local fixture defined in this test file, recording `self.headers.get("X-Hammunition-Enrolment")` for catalogue/signature/input/artifact requests. Assert the supplied id on every request. A 302 to a second loopback server must refuse and that server must see **zero requests**. A `file://other-host/path`, query/fragment, symlinked file/parent component, `../`, `%2e%2e`, empty segment, backslash and absolute relative path each refuse. Group filter ignores `owner:other` even when its bytes are present; personal mode uses it. An absent/bad signature refuses without advancing serial; good verification persists `max(old, new)` once. Verify 32 MiB/64 KiB bounds and one catalogue GET when two resolver families use it.

- [ ] Assert the missing-catalogue remedy byte for byte, without platform-dependent filesystem wording:

```python
import pytest
from hammunition.backends import BackendError
from hammunition.mirror import read_candidate


class MissingCatalogue(MirrorTransport):
    def read(self, relative: str, *, max_bytes: int) -> bytes:
        assert relative == "catalogue.json"
        raise BackendError("HTTP 404")

def test_missing_catalogue_has_exact_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    import hammunition.mirror as mirror
    monkeypatch.setattr(mirror, "MirrorTransport", MissingCatalogue)
    with pytest.raises(BackendError) as caught:
        read_candidate("http://bunker.invalid/export/", None)
    assert str(caught.value) == (
        "no catalogue at http://bunker.invalid/export/catalogue.json: HTTP 404. "
        "Enrol a Bunker that serves one, or run without --offline."
    )
```

Also call the shared reader with `MissingCatalogue` in the loader test: enrolment and offline loading must use identical wording. Expected pre-fix FAIL: the message is only `HTTP 404`.

- [ ] Run: `.venv/bin/pytest tests/test_bunker_transport.py -q`; expected FAIL: module missing.
- [ ] Implement the full bounded read method and catalogue loader:

```python
def read_catalogue(source: MirrorTransport) -> bytes:
    try:
        return source.read("catalogue.json", max_bytes=32 * 1024 * 1024)
    except BackendError as exc:
        url = source.base.rstrip("/") + "/catalogue.json"
        raise BackendError(
            f"no catalogue at {url}: {exc}. "
            "Enrol a Bunker that serves one, or run without --offline."
        ) from exc


def read(self, relative: str, *, max_bytes: int) -> bytes:
    relative = safe_relative(relative, "mirror path")
    url = self.base.rstrip("/") + "/" + "/".join(quote(p, safe="") for p in relative.split("/"))
    with self.open(url) as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise BackendError(f"{relative}: larger than {max_bytes} bytes")
    return raw


def load_catalogue(state: MirrorState, *, require_hardware: bool, now: datetime,
                   transport: MirrorTransport | None = None,
                   owner: str | None = None) -> VerifiedCatalogue:
    source = transport or MirrorTransport(state.url, state.enrolment_id)
    raw = read_catalogue(source)
    parsed = parse(raw)
    signatures: dict[str, bytes] = {}
    accepted = {k.id for k in state.keys}
    for signer in parsed.signers:
        if signer.id not in accepted:
            continue
        try:
            signatures[signer.signature] = source.read(signer.signature, max_bytes=64 * 1024)
        except BackendError:
            continue  # another enrolled signature can still verify
    verified = verify(raw, signatures, state, require_hardware=require_hardware, now=now)
    save_mirror(replace(state, accepted_serial=max(state.accepted_serial, parsed.serial),
                        generated=parsed.generated, mode=parsed.bunker.mode), owner=owner)
    return verified
```

`MirrorTransport.open` is separate from `UrllibTransport`: publisher transport still refuses file URLs. For HTTP, require the exact configured scheme/netloc and path-prefix, build an opener with HTTP/HTTPS/error handlers and **no redirect handler**, and create a Request with User-Agent plus the enrolment header. Handle HTTPError/URLError/OSError as existing transport does. For file, require empty netloc, absolute base path, no credentials/query/fragment; validate decoded relative segments after URL parsing, walk each component using `os.open(..., dir_fd=..., O_NOFOLLOW)` from an opened export directory, and yield `os.fdopen(fd, "rb")` only for a regular final file. Close every descriptor on failure. This prevents a signed or unsigned path from reading an arbitrary local file. `http -> file` redirects remain refused.

Full transport implementation (imports `contextmanager`, `Iterator`, `IO`, `os`, `stat`, `urllib.request/error/parse`; `quote`, `urlsplit`, `unquote` are from urllib.parse):

```python
class MirrorTransport:
    def __init__(self, base: str, enrolment_id: str | None = None) -> None:
        self.base = _check_mirror(base).rstrip("/")
        self.parts = urlsplit(self.base)
        if enrolment_id is not None and not valid_enrolment_id(enrolment_id):
            raise BackendError("invalid enrolment id")
        self.enrolment_id = enrolment_id
        self.opener = urllib.request.OpenerDirector()
        for handler in (urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(),
                        urllib.request.HTTPErrorProcessor(), urllib.request.HTTPDefaultErrorHandler()):
            self.opener.add_handler(handler)

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:
        parts = urlsplit(url)
        prefix = self.parts.path.rstrip("/") + "/"
        if (parts.scheme, parts.netloc) != (self.parts.scheme, self.parts.netloc) or not parts.path.startswith(prefix) or parts.query or parts.fragment:
            raise BackendError("mirror request leaves the configured base")
        relative = safe_relative(unquote(parts.path[len(prefix):]), "mirror request")
        if parts.scheme == "file":
            directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            fd = -1
            try:
                components = [*Path(unquote(self.parts.path)).parts[1:], *relative.split("/")]
                for index, component in enumerate(components):
                    safe_relative(component, "file mirror component")
                    flags = os.O_RDONLY | os.O_NOFOLLOW
                    if index < len(components) - 1:
                        flags |= os.O_DIRECTORY
                    child = os.open(component, flags, dir_fd=directory)
                    os.close(directory)
                    directory = child
                fd, directory = directory, -1
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise BackendError("file mirror target is not a regular file")
                stream = os.fdopen(fd, "rb")
                fd = -1
                with stream:
                    yield stream
            except OSError as exc:
                raise BackendError(f"file mirror {relative}: {exc}") from exc
            finally:
                if directory >= 0:
                    os.close(directory)
                if fd >= 0:
                    os.close(fd)
            return
        headers = {"User-Agent": "hammunition"}
        if self.enrolment_id is not None:
            headers["X-Hammunition-Enrolment"] = self.enrolment_id
        try:
            response = self.opener.open(urllib.request.Request(url, headers=headers), timeout=MIRROR_TIMEOUT)
            if response is None:
                raise BackendError("no mirror transport handler")
            with response:
                yield response
        except urllib.error.HTTPError as exc:
            raise BackendError(f"mirror returned HTTP {exc.code}; redirects are not followed") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TransportUnreachable(f"mirror could not be reached: {exc}") from exc

    # Include read() from the preceding block inside this class.
```

Add the file branch to `_check_mirror` before the ordinary host check: require empty netloc, absolute nonempty decoded path with no `.`/`..` segment, no query/fragment, and no whitespace/NUL; return the original normalized URL. Keep `MIRROR_SCHEMES = ("http", "https", "file")`, but keep publisher `fetch.ALLOWED_SCHEMES` HTTP(S)-only. A percent-encoded path prefix is matched before unquoting and is walked by decoded segments; double-encoded traversal remains a literal filename, never decoded twice.

`advance_mirror(state: MirrorState, serial: int, generated: str, *, owner: str | None = None) -> None` must lock/re-read and merge max serial, while refusing if URL/name/keys changed during verification. Implement it by extracting Task 2's atomic write body to `write_state(directory: int, filename: str, state: MirrorState, owner: str | None) -> None` (full body is the temporary/write/replace section above). Under one lock, check current identity/keys, retain the greater serial and its matching generated timestamp, then call write_state. `load_catalogue` calls this updater instead of save_mirror, so a competing verification of serial 44 cannot be overwritten by serial 43.

```python
def write_state(directory: int, filename: str, state: MirrorState,
                owner: str | None) -> None:
    temporary = f".mirror-{secrets.token_hex(12)}"
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as stream:
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), *store_uid(owner))
            stream.write((json.dumps(asdict(state), ensure_ascii=False) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def advance_mirror(state: MirrorState, serial: int, generated: str, *,
                   owner: str | None = None) -> None:
    target = mirror_path(owner)
    with store_lock(target, owner) as directory:
        current = read_state(directory, target.name, owner)
        if current is None or (current.url, current.name, current.keys, current.enrolment_id) != (state.url, state.name, state.keys, state.enrolment_id):
            raise SignerError("mirror enrolment changed during verification; retry the command")
        if serial > current.accepted_serial:
            updated = replace(current, accepted_serial=serial, generated=generated)
            write_state(directory, target.name, validate_state(updated), owner)
```

Replace the `save_mirror(replace(...))` call in load_catalogue with `advance_mirror(state, parsed.serial, parsed.generated, owner=owner)`; retain the same verified return object. For equal serial, preserve stored timestamp if present, filling it only when absent after validation. Never replace stored mode from a catalogue until it has verified.

 An explicit `accept-older` remains the only lowering path.

Extend `_check_mirror` with this explicit file-base branch; maintain old HTTP(S) validation. Replace Task 3 `read_candidate` with a `MirrorTransport` call (same parse/bounds, all advertised signatures; no trust yet).

```python
def read_candidate(url: str, enrolment_id: str | None) -> Candidate:
    source = MirrorTransport(url, enrolment_id)
    raw = read_catalogue(source)
    parsed = parse(raw)
    signatures: dict[str, bytes] = {}
    for signer in parsed.signers:
        try:
            signatures[signer.signature] = source.read(signer.signature, max_bytes=64 * 1024)
        except BackendError:
            continue
    return Candidate(raw, signatures)
```

`mirror.py` imports `read_catalogue` alongside `MirrorTransport`; the loader uses it before parsing, and a signature failure retains its separate verification error.

 In normal download fetchers, use this mirror transport when state URL matches station URL, leaving `transport` as the publisher-only transport. Group filtering is a reader lookup, never a different parse model. Do not add `X-Hammunition-Enrolment` to `UrllibTransport`.

- [ ] Run: `.venv/bin/pytest tests/test_bunker_transport.py tests/test_fetch.py tests/test_mirror_cli.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/mirror_transport.py src/hammunition/mirror.py src/hammunition/fetch.py src/hammunition/station.py src/hammunition/cli/main.py tests/test_bunker_transport.py
git commit -m "feat: read one verified Bunker catalogue over HTTP or file"
```

### Task 5: A5 — Resolution context, offline flags and exhausted-retry fallback

**Files:**
- Create: `src/hammunition/offline.py`, `tests/test_offline_context.py`.
- Modify: `src/hammunition/retry.py:90-110,177-221,287-358`; `src/hammunition/plan.py:110-138,276-299,1273-1325`; `src/hammunition/cli/main.py:1128-1400,4538-4760,4810-4910,8620-8700`; `src/hammunition/interface/plan.py:713-801,1264-1410` (build/render), `src/hammunition/interface/update.py:1-146` (offline disclosure); `src/hammunition/fetch.py:435-531` (offline source enforcement).
- Test: `tests/test_offline_context.py`, `tests/test_retry.py`, `tests/test_json_plan.py`, `tests/test_update.py`.

**Interfaces:**
- Consumes: `VerifiedCatalogue`, `MirrorState`, `load_catalogue`, `retry.PublisherUnavailable(url: str, answer: str, attempts: int)`, `plan.REQUESTED_DIRECTLY`, `plan.Deferral`, `plan.Blocker`, `plan.PlanError`.
- Produces: `CatalogueMiss(ValueError)`; `ResolutionContext(offline: bool = False, verified: VerifiedCatalogue | None = None, enrolment_id: str | None = None, inputs: MirrorTransport | None = None, notes: dict[tuple[str, str], str] = ...)`.
- Produces methods: `entry(unit: str, name: str) -> CatalogueArtifact`, `choose(unit: str, name: str, online: Callable[[], T], recorded: Callable[[], T]) -> T`, `note(unit: str, name: str, *, fallback: bool) -> str`, `require_payload(unit: str, name: str, *, sha256: str | None = None, size: int | None = None) -> CatalogueArtifact`; `resolve(..., resolution_context: ResolutionContext | None = None) -> InstallPlan` (existing other arguments unchanged).
- Produces: `Fetcher(..., offline: bool = False)`; offline `sources_for` is mirror-only; missing mirror/path is a named refusal. `install --offline`, `update --offline`; `update --offline --upstream` and `install --offline --no-mirror` refuse incompatible flags.

- [ ] Write failing test pinning both the exact wording and the final-error boundary:

```python
from hammunition.offline import ResolutionContext
from hammunition.retry import RetryPolicy, PublisherUnavailable
import pytest


def test_fallback_only_after_retry_exhaustion() -> None:
    ctx = ResolutionContext()
    seen = []
    policy = RetryPolicy(attempts=3, sleep=lambda _: None, notify=lambda _: None)
    def unavailable():
        seen.append("publisher")
        raise TimeoutError("dead link")
    with pytest.raises(PublisherUnavailable):
        ctx.choose("osm-regions", "europe/monaco",
                   lambda: policy.call("https://example.invalid/x", unavailable), lambda: "stored")
    assert seen == ["publisher"] * 3


def test_offline_never_calls_online() -> None:
    ctx = ResolutionContext(offline=True)
    def forbidden():
        pytest.fail("offline called a publisher")
    assert ctx.choose("osm-regions", "europe/monaco", forbidden, lambda: "pin") == "pin"
```

Add a real signed-catalogue context fixture in this file using Tasks 1–2 helpers: its `choose` after exhaustion returns stored value; `note` equals `publisher unreachable; resolved from Bunker bunker (<fingerprint>), recorded 2026-10-07T12:00:00Z`. Test a TLS certificate error and HTTP 404 are not fallback events (neither is `PublisherUnavailable`). Missing entry raises `not on Bunker bunker, publisher unreachable`; same event for a profile member creates a D-039 deferral, directly requested unit gives `PlanError`, and a unit with one of two needed artifacts missing gets **no install steps**. With no enrolment and no offline flag, existing outage tests and text goldens stay unchanged. Offline with no enrolment names `hammunition mirror enrol URL` before apt probes. Offline update reads local apt lists/install records and never invokes upstream helpers; even an empty update request validates the flag first. Installed publisher rechecks are skipped in offline mode even under `--recheck`.

- [ ] Run: `.venv/bin/pytest tests/test_offline_context.py -q`; expected FAIL: module missing.
- [ ] Implement complete new context functions (import the Task 1–4 types and `T = TypeVar("T")`):

```python
class CatalogueMiss(ValueError):
    pass


def catalogue_deferral(unit: PlannedPackage, exc: CatalogueMiss) -> Deferral:
    remedy = "populate this selection on the Bunker, or retry with the publisher reachable"
    if REQUESTED_DIRECTLY in unit.requested_by:
        raise PlanError([Blocker(unit.name, str(exc), remedy)])
    return Deferral(unit.name, "will not install this unit this run", str(exc), remedy, "package")
```


```python
@dataclass
class ResolutionContext:
    offline: bool = False
    verified: VerifiedCatalogue | None = None
    enrolment_id: str | None = None
    inputs: MirrorTransport | None = None
    notes: dict[tuple[str, str], str] = field(default_factory=dict)

    def entry(self, unit: str, name: str) -> CatalogueArtifact:
        if self.verified is None:
            raise CatalogueMiss("no Bunker enrolled; hammunition mirror enrol URL")
        row = self.verified.catalogue.artifact(unit, name, self.enrolment_id)
        if row is None:
            raise CatalogueMiss(f"{unit}/{name}: not on Bunker {self.verified.catalogue.bunker.name}, publisher unreachable")
        if row.status != "current" or row.path is None or row.sha256 is None or row.size is None:
            raise CatalogueMiss(f"{unit}/{name}: Bunker entry is {row.status}: {row.reason or 'not current'}")
        return row

    def require_payload(self, unit: str, name: str, *, sha256: str | None = None,
                        size: int | None = None) -> CatalogueArtifact:
        row = self.entry(unit, name)
        if sha256 is not None and row.sha256 != sha256:
            raise CatalogueMiss(f"{unit}/{name}: Bunker copy does not match the repository sha256 pin")
        if size is not None and row.size != size:
            raise CatalogueMiss(f"{unit}/{name}: Bunker copy does not match the expected size")
        return row

    def note(self, unit: str, name: str, *, fallback: bool) -> str:
        if self.verified is None:
            raise CatalogueMiss("no Bunker enrolled; hammunition mirror enrol URL")
        v = self.verified
        prefix = "publisher unreachable; " if fallback else "offline; "
        text = (f"{prefix}resolved from Bunker {v.catalogue.bunker.name} ({v.key.id}), "
                f"recorded {v.catalogue.generated}")
        if v.warnings:
            text += "; " + "; ".join(v.warnings)
        self.notes[(unit, name)] = text
        return text

    def choose(self, unit: str, name: str, online: Callable[[], T],
               recorded: Callable[[], T]) -> T:
        if not self.offline:
            try:
                return online()
            except PublisherUnavailable:
                if self.verified is None:
                    raise
        result = recorded()
        self.note(unit, name, fallback=not self.offline)
        return result
```

Keep `RetryPolicy.call`'s retry policy unchanged: invoke `choose` **outside** the retrying probe and outside resolver catch blocks that turn `OSError` into deferrals. Do not call the catalogue on every failed attempt. Add a `catalogue_deferral(unit: PlannedPackage, exc: CatalogueMiss) -> Deferral` helper using `REQUESTED_DIRECTLY` to raise `PlanError([Blocker(...)])`, or return a whole-unit deferral with the exact missing text and remedy “populate this selection on the Bunker, or retry with the publisher reachable”. Aggregate misses before filtering a unit, preserving dependent-unit refusals and installed/kept records. Expose context notes in the plan's existing `region_notes`/notes and JSON; append the same provenance to each affected fetch/resolution line, not just a footer. Include weak/age warnings on **every** line vouched for by the signer.

Create the context once near the start of `cmd_install`/`cmd_update`, after owner-aware station read, before apt planning. Refuse broken state/bad signatures; do not silently switch to unverified metadata. No-mirror explicitly ignores the enrolment for online planning. Pass `resolution_context` to `plan.resolve` and each station resolver (Tasks 6–11). One verification per run; the context shares input transport and notes across workers (protect note mutation with a lock or collect results before writing; never use a mutable module-global context).

Static-data availability has its own failing test in `tests/test_offline_context.py`; it is not covered merely by a resolver fixture:

```python
from pathlib import Path
from hammunition.distro import Target
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import DataInstall
from hammunition.plan import InstallPlan, PlannedPackage
from hammunition.offline import preflight_data
from bunker_fixtures import make_context


def test_missing_static_profile_data_defers_whole_unit(tmp_path: Path) -> None:
    catalog = load_catalog(Path(__file__).resolve().parents[1] / "catalog/packages")
    manifest = catalog["country-files"]
    block = next(block for block in manifest.install if isinstance(block.install, DataInstall))
    planned = PlannedPackage(manifest, block, (), requested_by=("reference",))
    plan = InstallPlan(Target(distro="debian", version="13", arch="x86_64"), (planned,))
    context = make_context(tmp_path, [])
    got = preflight_data(plan, context, cached=lambda unit, pin: False)
    assert not got.packages
    assert "not on Bunker bunker, publisher unreachable" in got.deferrals[0].why
```

Add the complete preflight helper to offline.py; call it after the existing local attribution determines which data actions are required, before printing/executing any commands:

```python
def preflight_data(plan: InstallPlan, context: ResolutionContext, *,
                   cached: Callable[[str, DataArtifact], bool]) -> InstallPlan:
    if not context.offline:
        return plan
    from hammunition.backends.data import data_name
    from hammunition.manifest.schema import DataInstall, RegisterInstall
    packages: list[PlannedPackage] = []
    deferrals = list(plan.deferrals)
    for unit in plan.packages:
        block = unit.block.install
        try:
            if isinstance(block, DataInstall):
                for pin in block.artifacts:
                    if not cached(unit.name, pin):
                        context.require_payload(unit.name, data_name(pin), sha256=pin.sha256, size=pin.size)
                        context.note(unit.name, data_name(pin), fallback=False)
            elif isinstance(block, RegisterInstall):
                from hammunition.acma import FILE_NAME
                context.unverified(unit.name, FILE_NAME)
                context.note(unit.name, FILE_NAME, fallback=False)
        except CatalogueMiss as exc:
            deferrals.append(catalogue_deferral(unit, exc))
            continue
        packages.append(unit)
    return replace(plan, packages=tuple(packages), deferrals=tuple(deferrals))
```

Import DataArtifact under TYPE_CHECKING for the callback annotation. The CLI's cache callback verifies the existing content-addressed artifact file against the repository sha256 and size (use `Fetcher.path_for`, `_digest_file`, stat) and returns true only on an exact match; it never calls Fetcher.fetch. Add `cached_data_pin(fetcher: Fetcher, pin: DataArtifact) -> bool` as a full helper:

```python
def cached_data_pin(fetcher: Fetcher, pin: DataArtifact) -> bool:
    from hammunition.fetch import _digest_file
    from hammunition.manifest.schema import RemoteArtifact
    path = fetcher.path_for(RemoteArtifact(url=pin.url, sha256=pin.sha256))
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size == pin.size and _digest_file(path) == pin.sha256
    except OSError:
        return False
```

The backend re-verifies cached bytes at execution too. An attributed installed record may also satisfy the existing current-data path; do not invent data/install records or treat an arbitrary prefix file as current. A profile unit with any missing artifact produces no steps, and its dependencies must be revalidated before execution; direct requests raise through catalogue_deferral. Add the static tests to Task 5's test command and interfaces (`preflight_data`, `cached_data_pin`).

Offline scope guard before execution: suppress `apt-get update`; refuse outstanding apt dependencies rather than trying network apt. Refuse network-requiring pip/build_python/npm steps by name with phase-2 remedy, unless the current backend already proves no such step is required. Do not claim this plan implements wheelhouses or npm caches. Dry run still reports these gaps honestly. `update` remains a report of local lists and pins. Accepting verified trust metadata may advance mirror serial; it does not change installed software or refresh apt lists. Report that local trust-state write explicitly, including under dry run, so D-053 is not read as an unlogged package action. The offline flag never bypasses consent or D-059's real-install JSON refusal.

Replace `Fetcher.sources_for` with the full function:

```python
def sources_for(self, url: str, mirror: MirrorPath | None) -> tuple[tuple[str, str], ...]:
    if self.offline:
        if self.mirror is None or mirror is None:
            raise BackendError("offline download has no Bunker route; hammunition mirror enrol URL")
        return (("mirror", mirror_url(self.mirror, mirror)),)
    if self.mirror and mirror is not None:
        return (("mirror", mirror_url(self.mirror, mirror)), ("publisher", url))
    return (("publisher", url),)
```

At the end of `_from_sources`, replace the “publisher always last” assertion with `BackendError(f"offline Bunker download failed: {passed_over or self._mirror_down}")`; retain the assertion only for an impossible online loop fallthrough. Offline mirrors that fail digest verification discard bytes and refuse, never ask publishers. Fix `fetch_disclosure`'s one-source branch: its returned detail/source must be the mirror URL in offline mode, and wording must say “Bunker only”; no phantom publisher in `Action.sources`.

- [ ] Run: `.venv/bin/pytest tests/test_offline_context.py tests/test_retry.py tests/test_fetch_mirror.py tests/test_json_plan.py tests/test_update.py -q`; expected PASS; regenerate JSON/CLI references and check both generators.
- [ ] Commit:

```bash
git add src/hammunition/offline.py src/hammunition/retry.py src/hammunition/plan.py src/hammunition/fetch.py src/hammunition/cli/main.py src/hammunition/interface/plan.py src/hammunition/interface/update.py tests/test_offline_context.py tests/test_retry.py tests/test_json_plan.py tests/test_update.py docs/reference/cli.md docs/reference/json-interface.md
git commit -m "feat: resolve offline and fall back after exhausted publisher retries"
```

### Task 6: A6 — Offline Geofabrik identity and pinned reachability

**Files:**
- Modify: `src/hammunition/geofabrik.py:162-234` (`resolve`); `src/hammunition/cli/main.py:4259-4349` (`resolve_map_regions`), `src/hammunition/backends/regions.py` (fetch-line provenance).
- Create: `tests/test_offline_geofabrik.py`.
- Test: `tests/test_geofabrik.py`, `tests/test_offline_geofabrik.py`, `tests/test_cli.py`, `tests/test_terrain_cli.py` (existing map-resolution tests).

**Interfaces:**
- Consumes: context, `RegionFile`, `Pin`, `_url`, `snapshot_for`, `_previous`, `Probe`.
- Produces: `recorded_region(region: str, freshness: str, *, today: date, pins: Mapping[tuple[str, str], Pin], context: ResolutionContext) -> RegionFile`; `resolve(..., context: ResolutionContext | None = None) -> RegionFile` (existing args preserved); CLI map resolver gets `context: ResolutionContext | None = None`.

- [ ] Write failing tests with real signed catalogue context, parametrized latest/monthly:

```python
from pathlib import Path
# tests/test_offline_geofabrik.py; use the signed factory in bunker_fixtures.
from datetime import date
from hammunition.geofabrik import resolve, Pin, BASE
from test_geofabrik import FakeProbe
from bunker_fixtures import artifact, make_context


def test_recorded_latest_has_no_publisher_requests(tmp_path: Path) -> None:
    row = artifact("osm-regions", "europe/monaco", b"pbf",
                   publisher_check="md5", publisher_digest="a" * 32,
                   publisher_name="monaco-261006.osm.pbf", publisher_size=3,
                   publisher_url=f"{BASE}/europe/monaco-261006.osm.pbf")
    context = make_context(tmp_path, [row], offline=True)
    probe = FakeProbe({}, {})
    got = resolve("europe/monaco", "latest", today=date(2026, 10, 7),
                  pins={}, probe=probe, context=context)
    assert (got.snapshot, got.size, got.md5) == ("261006", 3, "a" * 32)
    assert probe.seen == []
```

Define `make_context(tmp_path: Path, rows: list[dict[str, object]], *, offline: bool = True, inputs: list[dict[str, object]] | None = None) -> ResolutionContext` in `tests/bunker_fixtures.py`: generate an ed25519 key, call `signed(document(..., artifacts=rows, inputs=inputs or []))`, build a Task 2 state, call `verify` with the fixed 2026-10-07 UTC clock and return context; its transport is supplied separately for input tests. Use a unique child tmp directory per call to avoid key-file overwrite prompts.

```python
# tests/bunker_fixtures.py: complete context factory, imported by offline tests.
def make_context(tmp_path: Path, rows: list[dict[str, object]], *, offline: bool = True,
                 inputs: list[dict[str, object]] | None = None) -> ResolutionContext:
    from datetime import UTC, datetime
    import tempfile
    from hammunition.offline import ResolutionContext
    from hammunition.signers import EnrolledKey, MirrorState, verify
    tmp_path.mkdir(parents=True, exist_ok=True)
    child = Path(tempfile.mkdtemp(prefix="signing-", dir=tmp_path))
    private = key(child)
    public = private.with_suffix(".pub").read_text().strip()
    strength = classify(public)
    enrolled = EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False)
    state = MirrorState("http://bunker.invalid", "bunker", "personal", None, (enrolled,), 0)
    raw, sigs = signed(child, private, document(public, artifacts=rows, inputs=inputs or []))
    verified = verify(raw, sigs, state, now=datetime(2026, 10, 7, tzinfo=UTC))
    return ResolutionContext(offline=offline, verified=verified)
```

Import `ResolutionContext` under TYPE_CHECKING at module level for the helper's return annotation, and use `from __future__ import annotations` in the fixture module. Each test owns its signing directory; tests do not put keys in a checked-in fixture folder.

 Tests also cover monthly first/previous only (an unrelated stale snapshot is not silently substituted), publisher-name/URL snapshot disagreement, wrong region, zero size, MD5 of wrong length, explicit missing region refusal, profile whole-unit deferral, installed region kept, and pinned region returned without HEAD in `resolve_map_regions`. A valid signed sha256 that disagrees with the repository pin must refuse rather than replace the pin.

- [ ] Run: `.venv/bin/pytest tests/test_offline_geofabrik.py -q`; expected FAIL: `resolve() got an unexpected keyword argument 'context'`.
- [ ] Implement this full new function; preserve the old online resolver as `_resolve_online` and wrap it with context.choose (same public signature plus context):

```python
def recorded_region(region: str, freshness: str, *, today: date,
                    pins: Mapping[tuple[str, str], Pin],
                    context: ResolutionContext) -> RegionFile:
    if freshness != "latest":
        first = snapshot_for(freshness, today)
        for snapshot in (first, _previous(freshness, first)):
            pin = pins.get((region, snapshot))
            if pin is not None:
                context.require_payload("osm-regions", region, sha256=pin.sha256, size=pin.size)
                return RegionFile(region, snapshot, _url(region, snapshot), pin.size, pin.sha256, None)
    row = context.entry("osm-regions", region)
    match = re.fullmatch(re.escape(region.rsplit("/", 1)[-1]) + r"-(\d{6})\.osm\.pbf", row.publisher_name or "")
    if match is None or row.publisher_size is None or row.publisher_size <= 0:
        raise CatalogueMiss(f"osm-regions/{region}: publisher_name/publisher_size do not name a dated extract")
    snapshot = match.group(1)
    if freshness != "latest":
        first = snapshot_for(freshness, today)
        if snapshot not in (first, _previous(freshness, first)):
            raise CatalogueMiss(f"osm-regions/{region}: Bunker has no {freshness} candidate")
    url = _url(region, snapshot)
    if row.publisher_url != url or row.size != row.publisher_size:
        raise CatalogueMiss(f"osm-regions/{region}: publisher URL/size disagree with dated name")
    pin = pins.get((region, snapshot))
    if pin is not None:
        context.require_payload("osm-regions", region, sha256=pin.sha256, size=pin.size)
        return RegionFile(region, snapshot, url, pin.size, pin.sha256, None)
    if row.publisher_check not in ("md5", "md5-publisher") or re.fullmatch(r"[0-9a-f]{32}", row.publisher_digest or "") is None:
        raise CatalogueMiss(f"osm-regions/{region}: publisher_digest is not MD5")
    return RegionFile(region, snapshot, url, row.publisher_size, None, row.publisher_digest)


def resolve(region: str, freshness: str, *, today: date,
            pins: Mapping[tuple[str, str], Pin], probe: Probe,
            context: ResolutionContext | None = None) -> RegionFile:
    if context is None:
        return _resolve_online(region, freshness, today=today, pins=pins, probe=probe)
    return context.choose("osm-regions", region,
        lambda: _resolve_online(region, freshness, today=today, pins=pins, probe=probe),
        lambda: recorded_region(region, freshness, today=today, pins=pins, context=context))
```

`resolve_map_regions` skips its extra reachability HEAD when context.offline or this region was catalogue-resolved; use `context.notes` identity membership for automatic fallback. A pinned region whose online extra HEAD exhausts retries calls `require_payload` and records provenance, rather than keeping/refusing it before catalogue fallback. Do not catch a final publisher 404 and turn it into fallback. Keep the existing installed-file fallback and aggregated refusals when no catalogue is available. Pass missing entries to Task 5 whole-unit disposition rather than letting raw `CatalogueMiss` escape as a traceback.

- [ ] Run: `.venv/bin/pytest tests/test_geofabrik.py tests/test_offline_geofabrik.py tests/test_offline_context.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/geofabrik.py src/hammunition/cli/main.py src/hammunition/backends/regions.py tests/bunker_fixtures.py tests/test_offline_geofabrik.py tests/test_offline_context.py
git commit -m "feat: resolve Geofabrik snapshots from the enrolled catalogue"
```

### Task 7: A7a — Verified outlines and selection inputs

**Files:**
- Modify: `src/hammunition/offline.py` (Task 5 context), `src/hammunition/terrain_plan.py:88-104,196-245`, `src/hammunition/topo_plan.py:66-144,319-359`.
- Create: `tests/test_offline_inputs.py`.
- Read: `src/hammunition/backends/dem.py:91-141`, `src/hammunition/backends/topo.py:65-101`, `src/hammunition/backends/fstopo.py:46-84` (existing selection codecs), `src/hammunition/topo_bound.py` (`token`, `select`, `wants_region`, `keeps`, `split_bound`).

**Interfaces:**
- Consumes: `Catalogue.input`, `MirrorTransport.read`, existing `parse_poly`, each backend's `read_record(path: Path, region: str, slug: str)` / `render_record(entry)` and bound token.
- Produces context methods: `input_bytes(kind: str, region: str) -> bytes`, `selection(kind: str, region: str, reader: Callable[[Path, str, str], T | None]) -> T | None`, `outline(region: str, probe: Probe) -> str`.
- Produces: resolver optional `context: ResolutionContext | None = None`; these methods are the only route to catalogue inputs. Selection filenames: `<region>.poly` for outline, `<region>.tiles` for Copernicus and 3DEP, `<region>.quads` for US Topo and FSTopo, under distinct contract kinds. The wire entry supplies `name`; consumers look up by kind/region, not extension guesses.

- [ ] Write a failing test using actual bytes and the engine's existing record codec:

```python
from pathlib import Path
import hashlib
import pytest
from hammunition.backends.dem import RegionTiles, render_record, read_record
from hammunition.mirror_transport import MirrorTransport
from hammunition.offline import CatalogueMiss
from bunker_fixtures import make_context


def test_empty_selection_is_valid_and_bad_digest_is_refused(tmp_path: Path) -> None:
    body = render_record(RegionTiles("europe/monaco", "europe-monaco", (), 1)).encode()
    row = {"kind": "tile-selection", "region": "europe/monaco",
           "name": "europe/monaco.tiles", "path": "inputs/tile-selection/europe/monaco.tiles",
           "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
           "fetched": "2026-10-06T03:00:00Z"}
    context = make_context(tmp_path / "keys", [], inputs=[row])
    export = tmp_path / "export"
    target = export / "inputs/tile-selection/europe/monaco.tiles"
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    context.inputs = MirrorTransport(export.as_uri())
    selection = context.selection("tile-selection", "europe/monaco", read_record)
    assert selection is not None and selection.tiles == () and selection.unpublished == 1
    target.write_bytes(body.replace(b"1", b"2"))
    with pytest.raises(CatalogueMiss, match="sha256"):
        context.input_bytes("tile-selection", "europe/monaco")
```

Pin the wrong-bound case to an actual resolver, not only to the input byte reader:

```python
from pathlib import Path
import hashlib
from hammunition.backends.topo import RegionQuads, render_record
from hammunition.topo_plan import region_quads
from hammunition.ustopo import Quad, QuadIndex
from hammunition.topo_bound import ALL, TopoBound
from hammunition.mirror_transport import MirrorTransport
from test_terrain_plan import RegionProbe
from bunker_fixtures import make_context


def test_bounded_selection_is_not_reused_for_all(tmp_path: Path) -> None:
    # Synthetic geometry, not a claim about Monaco's actual outline.
    region = "europe/monaco"
    quad = Quad(0.0, 0.0, 0.125, 0.125, 3, "a" * 32, "DE/Test_20260101")
    narrow = render_record(RegionQuads(region, "europe-monaco", (), TopoBound("none").token))
    outline = "test\n1\n 0.01 0.01\n 0.10 0.01\n 0.10 0.10\n 0.01 0.10\nEND\nEND\n"
    entries: list[dict[str, object]] = []
    export = tmp_path / "export"
    for kind, extension, content in (("sheet-selection", "quads", narrow),
                                      ("region-outline", "poly", outline)):
        name = f"{region}.{extension}"
        relative = f"inputs/{kind}/{name}"
        body = content.encode()
        entries.append({"kind": kind, "region": region, "name": name, "path": relative,
                        "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                        "fetched": "2026-10-06T03:00:00Z"})
        path = export / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    context = make_context(tmp_path / "keys", [], inputs=entries)
    context.inputs = MirrorTransport(export.as_uri())
    result = region_quads(region, "europe-monaco", installed=tmp_path / "installed",
                           index=QuadIndex.of([quad]), probe=RegionProbe({}), notes=[],
                           bound=ALL, context=context)
    assert result.quads == (quad,) and result.bound == "all"
```

Add outline malformed/invalid UTF-8 tests, missing input, duplicate identity (Task 1), malicious file path (Task 4), signed correct sha256 but malformed selection (must refuse, not empty), and selection under wrong bound. Cover all five kinds with actual `render_record` bytes. A stale selected quad not in the carried index requires recomputation from the verified outline, using the carried row/digest; the Bunker selection never replaces repository ETags. A recorded bounded subset cannot widen to `all`; use outline instead. Bound `none` bypasses selections and returns no desired new work while retaining existing removal semantics. When both a selection and outline are unavailable, defer/refuse by name rather than compute a rectangular bounding box.

- [ ] Run: `.venv/bin/pytest tests/test_offline_inputs.py -q`; expected FAIL: `ResolutionContext` has no `selection`.
- [ ] Implement the full input functions inside the context:

```python
def input_bytes(self, kind: str, region: str) -> bytes:
    if self.verified is None or self.inputs is None:
        raise CatalogueMiss("no Bunker input transport; hammunition mirror enrol URL")
    row = self.verified.catalogue.input(kind, region)
    if row is None:
        raise CatalogueMiss(f"inputs/{kind}/{region}: not on Bunker {self.verified.catalogue.bunker.name}, publisher unreachable")
    if row.size > 32 * 1024 * 1024:
        raise CatalogueMiss(f"inputs/{kind}/{region}: larger than the 32 MiB input bound")
    try:
        body = self.inputs.read(row.path, max_bytes=row.size)
    except BackendError as exc:
        raise CatalogueMiss(f"inputs/{kind}/{region}: {exc}") from exc
    if len(body) != row.size or hashlib.sha256(body).hexdigest() != row.sha256:
        raise CatalogueMiss(f"inputs/{kind}/{region}: size or sha256 does not match the recorded input")
    self.note("inputs", f"{kind}/{region}", fallback=not self.offline)
    return body


def selection(self, kind: str, region: str,
              reader: Callable[[Path, str, str], T | None]) -> T | None:
    if self.verified is None or self.verified.catalogue.input(kind, region) is None:
        return None
    body = self.input_bytes(kind, region)
    with tempfile.TemporaryDirectory(prefix="hammunition-input-") as directory:
        path = Path(directory) / "selection"
        path.write_bytes(body)
        result = reader(path, region, region.replace("/", "-"))
    if result is None:
        raise CatalogueMiss(f"inputs/{kind}/{region}: invalid selection record")
    return result


def outline(self, region: str, probe: Probe) -> str:
    def recorded() -> str:
        try:
            return self.input_bytes("region-outline", region).decode("utf-8")
        except UnicodeError as exc:
            raise CatalogueMiss(f"inputs/region-outline/{region}: not UTF-8") from exc
    return self.choose("inputs", f"region-outline/{region}",
                       lambda: probe.text(f"{BASE}/{region}.poly"), recorded)
```

Cache successful input bytes by `(kind, region, sha256)` within context only; recheck before caching, and do not cache failure as an empty input. Avoid touching the prefix/installed selection files during planning. In `region_tiles`, `region_quads`, `region_sheets` and the 3DEP selection branch, prefer a current installed record first; offline or after exhausted outline probe, consult the appropriate catalogue selection. Check it against current carried lists/rows and requested bound; if incompatible, derive from `context.outline` using existing `parse_poly`/index selection functions. Online outline path still uses `MemoProbe` for one fetch. Defer whole units through Task 5 if neither route can supply a complete selection. Document these selection payload codecs in `bunker-catalogue.md` as engine reader APIs for Plan B, without changing the binding JSON contract.

- [ ] Run: `.venv/bin/pytest tests/test_offline_inputs.py tests/test_terrain_plan.py tests/test_topo_plan.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/offline.py src/hammunition/terrain_plan.py src/hammunition/topo_plan.py tests/test_offline_inputs.py docs/reference/bunker-catalogue.md
git commit -m "feat: read verified Bunker outlines and selection records"
```

### Task 8: A7b — Copernicus tiles from repository pins or recorded publisher MD5

**Files:**
- Modify: `src/hammunition/copernicus.py:319-339,512-541`; `src/hammunition/terrain_plan.py:104-194,418-466`; `src/hammunition/cli/main.py:4710-4750`.
- Create: `tests/test_offline_copernicus.py`.
- Test: existing `tests/test_copernicus.py`, `tests/test_terrain_plan.py`.

**Interfaces:**
- Consumes: context, `TilePin`, `TileFile`, `tile_url`; Task 7 inputs; catalogue `publisher_size` / `publisher_digest`.
- Produces: `recorded_tile(name: str, *, unit: str, pins: Mapping[str, TilePin], context: ResolutionContext) -> TileFile`; `resolve_tile(..., context: ResolutionContext | None = None, unit: str | None = None) -> TileFile`; context threaded through `resolve_terrain` and `resolve_station_terrain`.

- [ ] Write failing tests:

```python
from pathlib import Path
from hammunition.copernicus import resolve_tile, tile_url, TilePin
from test_terrain_plan import TileProbe, A
from bunker_fixtures import make_context, artifact


def test_offline_tile_uses_publisher_md5_not_bunker_sha256(tmp_path: Path) -> None:
    row = artifact("dem-copernicus", A, b"tif", publisher_name=f"{A}.tif",
                   publisher_size=3, publisher_check="etag-md5", publisher_digest="b" * 32,
                   publisher_url=tile_url(A))
    context = make_context(tmp_path, [row])
    probe = TileProbe({})
    got = resolve_tile(A, pins={}, probe=probe, unit="dem-copernicus", context=context)
    assert got.md5 == "b" * 32 and got.sha256 is None and got.size == 3
    assert probe.asked == []
```

Add pinned tile test: no HEAD even when new, digest/size from `TilePin`; multipart `publisher_digest` refused for unpinned Copernicus (it is not a whole-object MD5); wrong URL/name/zero publisher size refused; catalogue pin mismatch refused; installed record still read without Bunker lookup. Automatic fallback from the extra pinned-tile reachability HEAD has the exact Task 5 provenance; missing one required tile defers the whole profile unit and preserves its records, rather than recording fewer desired tiles.

- [ ] Run: `.venv/bin/pytest tests/test_offline_copernicus.py -q`; expected FAIL: unexpected `context` keyword.
- [ ] Implement complete recorded resolver:

```python
def recorded_tile(name: str, *, unit: str, pins: Mapping[str, TilePin],
                  context: ResolutionContext) -> TileFile:
    url = tile_url(name)
    pin = pins.get(name)
    if pin is not None:
        context.require_payload(unit, name, sha256=pin.sha256, size=pin.size)
        return TileFile(name, url, pin.size, pin.sha256, None)
    row = context.entry(unit, name)
    if row.publisher_url != url or row.publisher_size is None or row.publisher_size != row.size:
        raise CatalogueMiss(f"{unit}/{name}: publisher URL/size disagree")
    if row.publisher_check not in ("etag-md5", "md5") or _ETAG.fullmatch(row.publisher_digest or "") is None:
        raise CatalogueMiss(f"{unit}/{name}: publisher_digest is not a single-part MD5")
    return TileFile(name, url, row.publisher_size, None, (row.publisher_digest or "").strip('"'))
```

Rename unchanged old `resolve_tile` to `_resolve_tile_online`; new public wrapper uses `context.choose(unit, name, online, recorded)`, rejects missing `unit` when context is supplied, and retains the old route when context is None. In the terrain worker `check`, wrap both `resolve_tile` and the pinned extra HEAD in one `choose`; the catalogue branch returns `recorded_tile`. This ensures a failure during the extra HEAD is still covered and avoids calling context twice. Offline skips `recheck_installed`; online keeps D-197 recheck behaviour. Pass the resolved manifest's name as `unit`, never infer it from a temporary test directory.

- [ ] Run: `.venv/bin/pytest tests/test_offline_copernicus.py tests/test_copernicus.py tests/test_terrain_plan.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/copernicus.py src/hammunition/terrain_plan.py src/hammunition/cli/main.py tests/test_offline_copernicus.py
git commit -m "feat: resolve Copernicus tiles offline without weakening pins"
```

### Task 9: A7c — US Topo and 3DEP keep repository ETags offline

**Files:**
- Modify: `src/hammunition/ustopo.py:265-285`, `src/hammunition/usgs3dep.py:147-164`, `src/hammunition/topo_plan.py:144-285`, `src/hammunition/terrain_plan.py:196-304,342-380`, `src/hammunition/cli/main.py:4765-4810`.
- Create: `tests/test_offline_usgs.py`.
- Test: `tests/test_topo_plan.py`, `tests/test_usgs3dep.py`, `tests/test_ustopo.py`.

**Interfaces:**
- Consumes: carried `Quad` / `TileRow`, `check_quad`, `check_tile`, bound/input codecs from Task 7.
- Produces: `check_quad(quad: Quad, probe: TileProbe, *, context: ResolutionContext | None = None, unit: str | None = None) -> None`; `check_tile(row: TileRow, probe: TileProbe, *, context: ResolutionContext | None = None, unit: str | None = None) -> None`; context/unit passed from station resolvers into their check workers. Artifact identities: topo `unit/<quad.path>` (state included), 3DEP `unit/<row.name>`.

- [ ] Write failing tests (synthetic sheet name and Delaware bounds):

```python
from pathlib import Path
from hammunition.ustopo import Quad, check_quad
from hammunition.usgs3dep import TileRow, check_tile
from test_terrain_plan import TileProbe
from bunker_fixtures import artifact, make_context


def test_carried_etag_is_used_without_live_agreement(tmp_path: Path) -> None:
    quad = Quad(38.5, -75.5, 38.625, -75.375, 3, "b" * 32, "DE/Test_20260101")
    row = artifact("usgs-ustopo", quad.path, b"tif", publisher_check="etag-md5",
                   publisher_digest=quad.etag, publisher_url=quad.url,
                   publisher_name="Test_20260101_TM_geo.tif", publisher_size=3)
    context = make_context(tmp_path, [row])
    probe = TileProbe({})
    check_quad(quad, probe, context=context, unit="usgs-ustopo")
    assert probe.asked == []
```

Add 3DEP test using `USGS_13_n39w076`, multipart ETag `b*32 + "-2"`, and repository row size; no Bunker sha256 substitutes for an ETag. Test same selection narrowed under a new bound uses current carried rows and a verified outline; radius 0 selects none; missing grid still defers D-035; no Bunker sheet/3DEP tile yields named whole-unit deferral/refusal. Test live 404/changed ETag still refuses online and does not fall back. Retain all `s3etag` multipart verification tests unchanged.

- [ ] Run: `.venv/bin/pytest tests/test_offline_usgs.py -q`; expected FAIL: `check_quad()` unexpected keyword `context`.
- [ ] Implement full wrappers around unchanged online checker functions:

```python
def check_quad(quad: Quad, probe: TileProbe, *,
               context: ResolutionContext | None = None, unit: str | None = None) -> None:
    if context is None:
        return _check_quad_online(quad, probe)
    if unit is None:
        raise UstopoError("US Topo catalogue check needs its resolved unit name")
    def recorded() -> None:
        context.require_payload(unit, quad.path, size=quad.size)
    return context.choose(unit, quad.path, lambda: _check_quad_online(quad, probe), recorded)


def check_tile(row: TileRow, probe: TileProbe, *,
               context: ResolutionContext | None = None, unit: str | None = None) -> None:
    if context is None:
        return _check_tile_online(row, probe)
    if unit is None:
        raise CopernicusError("3DEP catalogue check needs its resolved unit name")
    def recorded() -> None:
        context.require_payload(unit, row.name, size=row.size)
    return context.choose(unit, row.name, lambda: _check_tile_online(row, probe), recorded)
```

Do not mutate the Quad/TileRow using catalogue fields: the download checks the **repo-carried** ETag, including multipart reconstruction. No live agreement is attempted offline, and installed rechecks are skipped. Selection inputs cannot invent new sheets: all referenced ids must resolve in carried indices, otherwise recompute from outline. Add context to `resolve_topo`, `resolve_bare_earth`, their station wrappers and each callback. Keep current/deferred/wanted records intact so a missing replacement cannot cause removal of the installed old edition. In `run_checks` outcome handlers catch `CatalogueMiss` separately and invoke Task 5 whole-unit policy.

- [ ] Run: `.venv/bin/pytest tests/test_offline_usgs.py tests/test_topo_plan.py tests/test_usgs3dep.py tests/test_ustopo.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/ustopo.py src/hammunition/usgs3dep.py src/hammunition/topo_plan.py src/hammunition/terrain_plan.py src/hammunition/cli/main.py tests/test_offline_usgs.py
git commit -m "feat: use carried USGS sheet and terrain ETags offline"
```

### Task 10: A7d — FSTopo and digest-less catalogue entries remain unverified

**Files:**
- Modify: `src/hammunition/fstopo.py:257-330`, `src/hammunition/topo_plan.py:360-464,487-539`, `src/hammunition/cli/main.py:4810-4830`.
- Create: `tests/test_offline_fstopo.py`.
- Read: `src/hammunition/backends/fstopo.py:137-260`, `src/hammunition/artifacts.py:234-298` (ACMA/snapshot trust kinds), `src/hammunition/consent/gate.py:116-194` (existing gates).

**Interfaces:**
- Consumes: `FsQuad`, `FsPin`, `FsQuadFile`, context, Task 7 FSTopo selection.
- Produces: `recorded_sheet(quad: FsQuad, *, unit: str, pins: Mapping[int, FsPin], context: ResolutionContext) -> FsQuadFile`; `resolve_fstopo(..., context: ResolutionContext | None = None, unit: str | None = None)` with existing return type. `ResolutionContext.unverified(unit: str, name: str) -> CatalogueArtifact` verifies recorded trust label and availability, **not content trust**.

- [ ] Write failing test:

```python
from pathlib import Path
from hammunition.fstopo import FsQuad, recorded_sheet
from bunker_fixtures import artifact, make_context


def test_signed_unpinned_fstopo_stays_unverified(tmp_path: Path) -> None:
    quad = FsQuad(38.5, -75.5, 38.625, -75.375, 12345, 2026, "DE", "Test")
    row = artifact("usfs-fstopo", quad.name, b"tif", publisher_check="unverified-fetch",
                   publisher_name="Test.tif", publisher_size=3,
                   publisher_url="https://data.fs.usda.gov/geodata/rastergateway/data/Test.tif")
    context = make_context(tmp_path, [row])
    got = recorded_sheet(quad, unit="usfs-fstopo", pins={}, context=context)
    assert got.sha256 is None
    assert "unverified" in got.verified_by.lower()
```

Also test pinned FSTopo sha256 mismatch; size mismatch; bad gateway URL/traversal; another operator's owner entry; missing sheet; unverified row shown with normal disclosure/consent. The presence of Bunker sha256 must not turn unpinned FSTopo into an all-pinned profile unit. Cover `context.unverified` with `acma-register/spectra_rrl.zip` (`unverified-zip`) and `repeater-snapshots/etcc.csv`, `brandmeister.json`, `hearham.json` (`unverified-fetch`): no implicit grant of `hold_unverified`, no new consent bypass. RepeaterBook API data remains unmirrorable and unlisted.

- [ ] Run: `.venv/bin/pytest tests/test_offline_fstopo.py -q`; expected FAIL: cannot import `recorded_sheet`.
- [ ] Implement full recorded resolver:

```python
def recorded_sheet(quad: FsQuad, *, unit: str, pins: Mapping[int, FsPin],
                   context: ResolutionContext) -> FsQuadFile:
    row = context.entry(unit, quad.name)
    url = row.publisher_url or ""
    parts = urllib.parse.urlsplit(url)
    if (not url.startswith(GATEWAY) or not parts.path.lower().endswith((".tif", ".tiff"))
        or ".." in urllib.parse.unquote(parts.path).split("/")):
        raise CatalogueMiss(f"{unit}/{quad.name}: publisher_url is not a Forest Service GeoTIFF")
    pin = pins.get(quad.secoord)
    if pin is not None:
        context.require_payload(unit, quad.name, sha256=pin.sha256, size=pin.size)
        return FsQuadFile(quad, url, pin.size, pin.sha256)
    context.unverified(unit, quad.name)
    if row.publisher_size is None or row.publisher_size <= 0 or row.publisher_size != row.size:
        raise CatalogueMiss(f"{unit}/{quad.name}: publisher_size is absent or inconsistent")
    return FsQuadFile(quad, url, row.publisher_size, None)
```

`unverified` allows only `unverified-fetch` or `unverified-zip` and returns the row; it does not authorize installation.

```python
def unverified(self, unit: str, name: str) -> CatalogueArtifact:
    row = self.entry(unit, name)
    if row.publisher_check not in ("unverified-fetch", "unverified-zip"):
        raise CatalogueMiss(f"{unit}/{name}: expected an explicitly unverified catalogue entry")
    return row
```
 Bunker `hold_unverified` is the server's holding policy; the engine retains existing online consent/disclosure gates and structure checks. No new local policy with that name is silently enabled. Wrap the two-request `gateway.locate` + pin-size agreement inside `context.choose`, returning `FsQuadFile` from both branches; old online errors and final HTTP failures remain unchanged. Skip installed publisher rechecks offline. Wire station wrapper and CLI context. Do not follow a fresh gateway redirect offline.

- [ ] Run: `.venv/bin/pytest tests/test_offline_fstopo.py tests/test_fstopo.py tests/test_topo_plan.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/fstopo.py src/hammunition/topo_plan.py src/hammunition/offline.py src/hammunition/cli/main.py tests/test_offline_fstopo.py
git commit -m "feat: resolve FSTopo without upgrading unverified data trust"
```

### Task 11: A7e — Kiwix and CoMaps skip publisher checks offline

**Files:**
- Modify: `src/hammunition/backends/kiwix.py:61-67,256-328`, `src/hammunition/backends/comaps_maps.py:70-73,256-343`, `src/hammunition/cli/main.py:4830-4920`.
- Create: `tests/test_offline_books_maps.py`.
- Read: `src/hammunition/kiwix.py:94-128,236-280`, `src/hammunition/comaps.py:71-114,278-308`.

**Interfaces:**
- Consumes: `resolve_books`, `book_mirror_path`, `resolve_regions`, `mirror_path`, context; repository `BookPin` sha256 and `MapPin` base64 SHA-1.
- Produces: `resolve_station_books(..., context: ResolutionContext | None = None, unit: str | None = None) -> list[BookFile]`; CoMaps `resolve_station_maps(..., context: ResolutionContext | None = None, unit: str | None = None) -> tuple[list[MapFile], list[str]]`; full helper `require_book(book: BookFile, unit: str, context: ResolutionContext) -> None` and `require_map(f: MapFile, unit: str, context: ResolutionContext) -> None`.

- [ ] Write failing tests using actual repo-carried Monaco map pins and a selected public book, not the operator's station:

```python
from pathlib import Path
from hammunition.backends.kiwix import resolve_station_books, book_mirror_path
from hammunition.kiwix import resolve_books, load_book_list, load_pin_file
from bunker_fixtures import artifact, make_context


def test_pinned_book_does_not_head_publisher(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "catalog"
    book_id = "ham.stackexchange.com_en_all"
    book = resolve_books([book_id], load_book_list(root), load_pin_file(root))[0]
    row = artifact("kiwix-library", book_id, b"unused", sha256=book.pin.sha256,
                   size=book.pin.size, publisher_url=book.pin.url)
    context = make_context(tmp_path / "keys", [row])
    def forbidden(url):
        raise AssertionError("offline Kiwix asked publisher")
    got = resolve_station_books([book_id], root, installed=tmp_path / "books",
                                head=forbidden, context=context, unit="kiwix-library")
    assert got == [book]
```

Use the existing synthetic CoMaps pin writer from `tests/test_artifacts.py:_write_comaps_pins` (copy its Monaco-only fixture into this new file to avoid private-helper imports if it maps other regions). Test SHA-1 stays SHA-1, version/id mirror naming, expired online pin final 404 is not fallback, catalogue mismatch/missing book is whole-unit refusal/defer, a current installed book/map has no probe even with `--recheck --offline`, and automatic fallback from retry-exhausted HEAD. Missing station selection still defers the data unit, never its reader.

- [ ] Run: `.venv/bin/pytest tests/test_offline_books_maps.py -q`; expected FAIL: unexpected keyword `context`.
- [ ] Add these full helpers:

```python
def require_book(book: BookFile, unit: str, context: ResolutionContext) -> None:
    name = book_mirror_path(unit, book).name
    context.require_payload(unit, name, sha256=book.pin.sha256, size=book.pin.size)


def require_map(f: MapFile, unit: str, context: ResolutionContext) -> None:
    name = mirror_path(unit, f).name
    row = context.require_payload(unit, name, size=f.pin.size)
    expected = sha1_hex(f.pin.sha1)
    if row.publisher_digest != expected or row.publisher_check not in ("sha1", "sha1-publisher"):
        raise CatalogueMiss(f"{unit}/{name}: Bunker publisher SHA-1 differs from the repository CoMaps index")
```

For Kiwix `still_served` / batch head checks, use `context.choose(unit, book id, online_check, lambda: require_book(...))`; branch offline before recheck_installed/run_checks so no publisher worker is started. CoMaps wraps its `published(status,size,pin)` check the same way, maintaining final-error expiry logic. Both resolve pins first using existing reader validation. Keep the SHA-1 warning and each book's licence/size; append context provenance to each line. In installed-only paths, avoid requiring a catalogue entry for bytes already verified on disk.

- [ ] Run: `.venv/bin/pytest tests/test_offline_books_maps.py tests/test_kiwix_backend.py tests/test_comaps_maps.py tests/test_reference_update.py tests/test_comaps_update.py -q`; expected PASS. The existing CoMaps tests are `tests/test_comaps_maps.py`.
- [ ] Commit:

```bash
git add src/hammunition/backends/kiwix.py src/hammunition/backends/comaps_maps.py src/hammunition/cli/main.py tests/test_offline_books_maps.py
git commit -m "feat: resolve pinned Kiwix books and CoMaps maps offline"
```

### Task 12: A8a — Mirror-first ETag and size-only downloads

**Files:**
- Modify: `src/hammunition/fetch.py:472-531,744-832`; `src/hammunition/backends/topo.py:170-281`, `src/hammunition/backends/fstopo.py:153-260`, `src/hammunition/backends/dem.py:274-307,355-398`.
- Create: `tests/test_fetch_bunker_etag_sized.py`.
- Test: `tests/test_fetch_mirror.py`, `tests/test_topo_backend.py`, `tests/test_fstopo_backend.py`.

**Interfaces:**
- Consumes: `MirrorPath`, `_from_sources`, `etag_matches`, `record_fetch`, `fetch_disclosure`.
- Produces: `fetch_etag(url: str, etag: str, *, expected_size: int, mirror: MirrorPath | None = None) -> FetchResult`, `fetch_sized(url: str, *, expected_size: int, mirror: MirrorPath | None = None) -> FetchResult`; `_from_sources(..., publisher_transport: Transport | None = None)` so size-only publisher requests retain no-redirect transport.

- [ ] Write failing tests:

```python
from pathlib import Path
import hashlib
import pytest
from hammunition.fetch import Fetcher, MirrorPath, mirror_url
from hammunition.backends.base import BackendError
from test_fetch_mirror import Routes

@pytest.mark.parametrize("kind", ["etag", "sized"])
def test_mirror_good_and_corrupt_offline(tmp_path: Path, kind: str) -> None:
    body = b"II*\x00example TIFF"
    publisher = "https://example.invalid/Test.tif"
    path = MirrorPath("usgs-ustopo", "DE/Test_20260101")
    url = mirror_url("http://bunker.invalid", path)
    routes = Routes({url: body})
    fetcher = Fetcher(tmp_path / "cache", transport=routes, mirror="http://bunker.invalid",
                      mirror_transport=routes, offline=True)
    if kind == "etag":
        result = fetcher.fetch_etag(publisher, hashlib.md5(body, usedforsecurity=False).hexdigest(),
                                    expected_size=len(body), mirror=path)
    else:
        result = fetcher.fetch_sized(publisher, expected_size=len(body), mirror=path)
    assert result.path.read_bytes() == body and result.source == "mirror"
    result.path.unlink()
    routes.routes[url] = b"x" * len(body)
    with pytest.raises(BackendError):
        if kind == "etag":
            fetcher.fetch_etag(publisher, hashlib.md5(body, usedforsecurity=False).hexdigest(),
                               expected_size=len(body), mirror=path)
        else:
            fetcher.fetch_sized(publisher, expected_size=len(body), mirror=path)
    assert publisher not in routes.requested
    assert not list((tmp_path / "cache").glob("*.part.*"))
```

Add online bad mirror → good publisher, both fail (both reasons named), cap hit, wrong size, missing TIFF magic, ETag multipart, cache re-verification with `source="cache"`, and size-only cached file always fetched again. Test strict no-redirect publisher route when mirror misses a size-only sheet. Backend tests assert `Action.sources`, description and `action_end` facts for all three families, and that fetcher path identity equals Task 16 artifacts identity.

- [ ] Run: `.venv/bin/pytest tests/test_fetch_bunker_etag_sized.py -q`; expected FAIL: `fetch_etag()` / `fetch_sized()` unexpected `mirror` keyword.
- [ ] Replace the two long methods with these full functions (class indentation), keeping path helpers unchanged:

```python
def fetch_etag(self, url: str, etag: str, *, expected_size: int,
               mirror: MirrorPath | None = None) -> FetchResult:
    make_dir(self.cache_dir)
    final = self.etag_path_for(url, etag)
    if final.exists() and final.stat().st_size == expected_size and etag_matches(final, etag):
        return FetchResult(final, _digest_file(final), True, expected_size, source="cache")
    final.unlink(missing_ok=True)
    temporary = final.with_name(final.name + f".part.{os.getpid()}")
    def verify(_sha: str, size: int, _other: str | None, where: str) -> None:
        if size != expected_size:
            raise VerificationError(f"{where}: expected {expected_size} bytes, got {size}")
        if not etag_matches(temporary, etag):
            raise VerificationError(f"{where}: no part size reproduces publisher ETag {etag}; discarded")
    done = self._from_sources(url, mirror, temporary, max_bytes=expected_size + 1024 * 1024,
                              md5=False, verify=verify)
    os.replace(temporary, final)
    return FetchResult(final, done.sha256, False, done.size, done.source, done.url, done.mirror_failure)


def fetch_sized(self, url: str, *, expected_size: int,
                mirror: MirrorPath | None = None) -> FetchResult:
    make_dir(self.cache_dir)
    final = self.sized_path_for(url, expected_size)
    final.unlink(missing_ok=True)
    temporary = final.with_name(final.name + f".part.{os.getpid()}")
    def verify(_sha: str, size: int, _other: str | None, where: str) -> None:
        if size != expected_size:
            raise VerificationError(f"{where}: expected {expected_size} bytes, got {size}")
        with temporary.open("rb") as handle:
            magic = handle.read(4)
        if magic not in TIFF_MAGIC:
            raise VerificationError(f"{where}: not a TIFF (starts {magic!r}); discarded")
    done = self._from_sources(url, mirror, temporary, max_bytes=expected_size + 1024 * 1024,
                              md5=False, verify=verify, publisher_transport=self.strict_transport)
    os.replace(temporary, final)
    return FetchResult(final, done.sha256, False, done.size, done.source, done.url, done.mirror_failure)
```

Inside `_from_sources`, use `self.mirror_transport` for mirror, otherwise `publisher_transport or self.transport`; retain existing temporary cleanup and offline refusal from Task 5. Backend call sites use topo `MirrorPath(manifest.name, quad.path)`, FSTopo `MirrorPath(manifest.name, sheet.name)`, 3DEP `MirrorPath(manifest.name, tile.name)` and their existing sha256 branch remains unchanged. Pass facts and source descriptions through the same `fetch_disclosure` / `record_fetch` helpers as regional data. No guessed sha256 for ETag/size-only downloads.

- [ ] Run: `.venv/bin/pytest tests/test_fetch_bunker_etag_sized.py tests/test_fetch_mirror.py tests/test_topo_backend.py tests/test_fstopo_backend.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/fetch.py src/hammunition/backends/topo.py src/hammunition/backends/fstopo.py src/hammunition/backends/dem.py tests/test_fetch_bunker_etag_sized.py
git commit -m "feat: mirror USGS and FSTopo downloads with existing checks"
```

### Task 13: A8b — Source, binary, venv and Node pinned payload routes

**Files:**
- Create: `src/hammunition/payloads.py`, `tests/test_payload_mirror.py`.
- Modify: `src/hammunition/backends/source.py:392-441`, `src/hammunition/backends/binary.py:120-163`, `src/hammunition/backends/venv.py:182-238`, `src/hammunition/backends/node.py:195-222,305-312`.
- Test: existing `tests/test_source_backend.py`, `tests/test_binary_backend.py`, `tests/test_venv_backend.py`, `tests/test_node_backend.py`.

**Interfaces:**
- Consumes: `RemoteArtifact`, `Fetcher`, `Action.facts/sources`, context.
- Produces: `payload_name(artifact: RemoteArtifact) -> str`, `payload_path(unit: str, artifact: RemoteArtifact) -> MirrorPath`, `payload_action(unit: str, artifact: RemoteArtifact, fetcher: Fetcher, *, label: str, max_bytes: int | None = None, expected_size: int | None = None, fetched: dict[str, Path] | None = None) -> Action`.
- Names: `<sha256>/<safe_name(url)>` within the catalog unit. This avoids different per-target payloads with the same basename colliding. Task 16 and Plan B must use this exact exported helper, not their own basename rule. Existing data `install_as` names remain unchanged.

- [ ] Write failing test:

```python
from pathlib import Path
import hashlib
from hammunition.fetch import Fetcher, mirror_url
from hammunition.manifest.schema import RemoteArtifact
from hammunition.payloads import payload_path, payload_action
from test_fetch_mirror import Routes


def test_payload_action_uses_mirror_and_records_facts(tmp_path: Path) -> None:
    body = b"pinned tarball"
    artifact = RemoteArtifact(url="https://example.invalid/release.tar.gz",
                              sha256=hashlib.sha256(body).hexdigest())
    where = payload_path("example-tool", artifact)
    url = mirror_url("http://bunker.invalid", where)
    routes = Routes({url: body})
    fetcher = Fetcher(tmp_path, transport=routes, mirror_transport=routes,
                      mirror="http://bunker.invalid", offline=True)
    step = payload_action("example-tool", artifact, fetcher, label="source archive")
    assert step.sources == (url,)
    step.perform()
    assert step.facts["source"] == "mirror" and step.facts["fetched_from"] == url
    assert routes.requested == [url]
```

Parametrize backend-level tests over the four existing manifest helpers/builders, exercising their **actual** fetch Actions with `Routes`; assert no publisher call on mirror hit, online fallback on wrong digest, offline corrupt/missing refusal, both reasons logged online, and variant-name collision avoided. Ensure a venv payload still carries its existing source/build script and pip steps; this task does not mirror pip dependencies. Node route means its source tarball, never fetching/installing a Node runtime. Keep NPM script protections and disclosed registry gap.

- [ ] Add the real static-data CLI regression in `tests/test_payload_mirror.py`. Fixture bytes replace the PDFs' pins in a copied manifest; no publisher bytes are fetched, and the manifest still uses the real `ics-forms` data shape. The isolated install prefix is `tmp_path / "prefix"`; files land at `<prefix>/share/hammunition/data/ics-forms/<install_as>`, not directly under the prefix:

```python
import importlib
import yaml
import pytest
from hammunition.backends.source import SourceBackend
from hammunition.station import Station, save_station
from hammunition.signers import EnrolledKey, MirrorState, save_mirror
from hammunition.keystrength import classify
from bunker_fixtures import artifact as catalogue_artifact, document, key, signed


def test_ics_forms_offline_install_without_regions(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    cli = importlib.import_module("hammunition.cli.main")
    catalog = tmp_path / "catalog"
    (catalog / "packages").mkdir(parents=True)
    (catalog / "profiles").mkdir()
    source = Path(__file__).resolve().parents[1] / "catalog/packages/ics-forms.yaml"
    manifest = yaml.safe_load(source.read_text())
    block = manifest["install"][0]["install"]
    export = tmp_path / "export"
    (export / "ics-forms").mkdir(parents=True)
    rows = []
    expected = {}
    for index, pin in enumerate(block["artifacts"]):
        body = f"%PDF-1.4 fixture {index}\n".encode()
        pin["sha256"] = hashlib.sha256(body).hexdigest()
        pin["size"] = len(body)
        name = pin["install_as"]
        (export / "ics-forms" / name).write_bytes(body)
        rows.append(catalogue_artifact("ics-forms", name, body,
                    publisher_url=pin["url"], licence=block["licence"]))
        expected[name] = body
    (catalog / "packages/ics-forms.yaml").write_text(yaml.safe_dump(manifest))
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public, artifacts=rows))
    (export / "catalogue.json").write_bytes(raw)
    (export / "catalogue.sig.d").mkdir()
    for name, body in signatures.items():
        (export / name).write_bytes(body)
    strength = classify(public)
    save_mirror(MirrorState(export.as_uri(), "bunker", "personal", None,
                (EnrolledKey(strength.fingerprint, public, strength.algorithm,
                             strength.bits, False),), 0))
    save_station(Station(mirror=export.as_uri()))  # no map_regions
    prefix = tmp_path / "prefix"
    def isolated_source(fetcher: Fetcher, *, build_root: Path,
                        owner: str | None = None) -> SourceBackend:
        return SourceBackend(fetcher, build_root=build_root, prefix=prefix, owner=owner)
    monkeypatch.setattr(cli, "SourceBackend", isolated_source)
    assert cli.main(["--catalog", str(catalog), "install", "ics-forms",
                     "--offline", "--yes"]) == 0
    destination = prefix / "share/hammunition/data/ics-forms"
    assert {path.name: path.read_bytes() for path in destination.glob("*.pdf")} == expected
```

Expected pre-fix FAIL: absent `--offline`, file mirror refusal, or incorrect region deferral. Expected PASS with the Task 5 static preflight and Task 4 file transport plus the existing `DataBackend` SHA-256 fetch path. Keep all transaction/config/cache paths under conftest's isolated environment. Ensure CLI static data dispatch is independent of region resolution; apply the concrete constructor below when creating the data backend, not a regional backend:

```python
# cmd_install's static data constructor (existing independent dispatch):
data = DataBackend(fetcher=source.fetcher, prefix=source.prefix, runner=runner,
                   build_root=builds, owner=source.owner)
# preflight_data's DataInstall branch in Task 5 uses only block.artifacts;
# do not add any station.map_regions precondition to that branch.
```

- [ ] Run: `.venv/bin/pytest tests/test_payload_mirror.py -q`; expected FAIL: module missing.
- [ ] Implement the complete shared action:

```python
def payload_name(artifact: RemoteArtifact) -> str:
    return f"{artifact.sha256}/{safe_name(artifact.url)}"


def payload_path(unit: str, artifact: RemoteArtifact) -> MirrorPath:
    return MirrorPath(unit, payload_name(artifact))


def payload_action(unit: str, artifact: RemoteArtifact, fetcher: Fetcher, *, label: str,
                   max_bytes: int | None = None, expected_size: int | None = None,
                   fetched: dict[str, Path] | None = None) -> Action:
    path = payload_path(unit, artifact)
    suffix, detail, sources = fetch_disclosure(fetcher, artifact.url, path, "sha256")
    facts: dict[str, str] = {}
    def perform() -> str:
        result = fetcher.fetch(artifact, max_bytes=max_bytes, mirror=path)
        if expected_size is not None and result.size != expected_size:
            raise BackendError(f"{unit}/{path.name}: manifest size {expected_size}, got {result.size}")
        if fetched is not None:
            fetched["path"] = result.path
        provenance = record_fetch(result, facts, mirrored=fetcher.mirror is not None)
        where = "cached" if result.from_cache else "downloaded"
        return f"{where} {result.size} bytes, sha256 verified{provenance}"
    return Action(kind="fetch", description=f"Fetch {unit} {label}{suffix}",
                  detail=detail, sources=sources, facts=facts, perform=perform)
```

Replace each backend's existing fetch Action with `payload_action`, preserving its own downstream extraction/path map. Source passes block.source; binary passes block.artifact and `fetched`; venv passes block.payload; Node passes block.artifact. Before building steps offline, call `context.require_payload` for every new payload, with repo sha256 and known size. Skip for a build whose existing attribution already makes it current. Do not add digest-less payloads to `RemoteArtifact` or infer a repository pin from Bunker bytes. Pass the shared context as optional constructor input through CLI; keep TYPE_CHECKING imports where needed to avoid the existing fetch/backends cycle.

- [ ] Run: `.venv/bin/pytest tests/test_payload_mirror.py tests/test_source_backend.py tests/test_binary_backend.py tests/test_venv_backend.py tests/test_node_backend.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/payloads.py src/hammunition/backends/source.py src/hammunition/backends/binary.py src/hammunition/backends/venv.py src/hammunition/backends/node.py src/hammunition/cli/main.py tests/test_payload_mirror.py
git commit -m "feat: mirror pinned source binary venv and Node payloads"
```

### Task 14: A8c — Mapsforge converter and git extra-file payload routes

**Files:**
- Modify: `src/hammunition/backends/mapsforge.py:350-401`, `src/hammunition/backends/git.py:393-451,515-526`, `src/hammunition/cli/main.py:4660-4680` (shared context/fetcher).
- Create: `tests/test_tool_extra_mirror.py`.
- Test: `tests/test_mapsforge.py`, `tests/test_git_comaps.py`.

**Interfaces:**
- Consumes: Task 13 `payload_action`/`payload_path`, existing `ConverterTool`, `ExtraFile` and `GitInstall.extra_files`.
- Produces: Mapsforge `_fetch_tool(..., unit: str)` routes exactly `payload_path(unit, tool.artifact)`; git extra-file fetch uses `payload_path(manifest.name, RemoteArtifact(...))`. Preserve explicit expected sizes and declared-but-unverified upstream signature warning.

- [ ] Write failing test against the Mapsforge Action produced by the shared helper, then the existing backend's `_tool_steps`:

```python
from pathlib import Path
import hashlib
from hammunition.manifest.schema import RemoteArtifact
from hammunition.fetch import Fetcher, mirror_url
from hammunition.payloads import payload_action, payload_path
from hammunition.backends.base import BackendError
from test_fetch_mirror import Routes
import pytest


def test_converter_tool_size_check_survives_mirror(tmp_path: Path) -> None:
    body = b"jar payload"
    pin = RemoteArtifact(url="https://example.invalid/writer.jar",
                         sha256=hashlib.sha256(body).hexdigest())
    at = mirror_url("http://bunker.invalid", payload_path("mapsforge-poi", pin))
    routes = Routes({at: body})
    fetcher = Fetcher(tmp_path, transport=routes, mirror_transport=routes,
                      mirror="http://bunker.invalid", offline=True)
    action = payload_action("mapsforge-poi", pin, fetcher, label="POI writer",
                            expected_size=len(body) + 1)
    with pytest.raises(BackendError, match="manifest size"):
        action.perform()
    assert routes.requested == [at]
```

Add actual Mapsforge tool and git `_extra_file_steps` tests using existing `_manifest`/backend fixtures in their owning test files; the shared-action unit test alone cannot prove call sites were wired. Check mirror hit/failure/offline, installation map includes result.path, extra.from_tree never downloads, wrong known size refuses before copy, and the source lists match emitted artifact names. Test an extra filename shared by two pins gets separate paths.

- [ ] Run: `.venv/bin/pytest tests/test_tool_extra_mirror.py tests/test_mapsforge.py tests/test_git_comaps.py -q`; expected FAIL: backend fetch requests publisher instead of mirror.
- [ ] Replace both fetch Actions with `payload_action(..., expected_size=..., max_bytes=size+MIB, fetched=...)`. Keep each backend's post-fetch install and signature-gap sentence. Complete git fetch helper replacement if it remains used:

```python
def _fetch_extra(fetcher: Fetcher, extra: ExtraFile, unit: str) -> str:
    artifact = extra.artifact
    if artifact is None:
        raise BackendError(f"{unit}: extra file has no remote artifact")
    pin = RemoteArtifact(url=artifact.url, sha256=artifact.sha256)
    step = payload_action(unit, pin, fetcher, label=Path(extra.install_as).name,
                          expected_size=artifact.size, max_bytes=artifact.size + MIB)
    return step.perform()
```

Prefer the Action directly in `_extra_file_steps` so its facts/sources survive, rather than hiding it in `_fetch_extra`. Offline availability validation covers all tool/extra files before the first build; only their repository digest governs execution. Converter licence remains tool.licence; git extra-file licence is not invented.

- [ ] Run: `.venv/bin/pytest tests/test_tool_extra_mirror.py tests/test_mapsforge.py tests/test_git_comaps.py -q`; expected PASS.
- [ ] Commit:

```bash
git add src/hammunition/backends/mapsforge.py src/hammunition/backends/git.py src/hammunition/cli/main.py tests/test_tool_extra_mirror.py
git commit -m "feat: mirror converter tools and git extra-file downloads"
```

### Task 15: A9 — Verified git bundles, tags and recursive gitlinks

**Files:**
- Create: `src/hammunition/gitbundles.py`, `tests/test_git_bundles.py`.
- Modify: `src/hammunition/backends/git.py:84-266,273-336,448-506`; `src/hammunition/cli/main.py:4660-4680`.
- Test: `tests/test_git_backend.py`, `tests/test_git_comaps.py`.

**Interfaces:**
- Consumes: `GitInstall.repo/ref/commit/submodules`, existing `GitBackend.verify_pin`, `GitBackend.verify_submodules`, `GIT_ENV`, context/fetcher, `CommandRunner`.
- Produces: `bundle_name(unit: str, commit: str, *, path: str | None = None, subcommit: str | None = None) -> str`; `checkout_bundle(unit: str, block: GitInstall, destination: Path, *, fetcher: Fetcher, context: ResolutionContext, runner: CommandRunner) -> str`.
- Bundle identity is binding: `git-bundles/<unit>@<commit>`; submodule `git-bundles/<unit>@<commit>/<recursive path>@<subcommit>`. Repository commit is `block.commit` for a tag or `block.ref` for a SHA. A tag lacking recorded `commit` cannot be offline-verified; refuse it by name, do not adopt a Bunker commit as its pin.

- [ ] Write failing tests making **real local git repositories and bundles**:

```python
import subprocess
from pathlib import Path
import pytest
from hammunition.gitbundles import bundle_name


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def repository(tmp_path: Path, name: str) -> tuple[Path, str]:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "file").write_text(name)
    git(repo, "add", "file")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


def test_bundle_names_are_contract_names(tmp_path: Path) -> None:
    _, commit = repository(tmp_path, "parent")
    _, subcommit = repository(tmp_path, "child")
    assert bundle_name("example-tool", commit) == f"example-tool@{commit}"
    assert bundle_name("example-tool", commit, path="vendor/child", subcommit=subcommit) == f"example-tool@{commit}/vendor/child@{subcommit}"
```

Add checkout tests: parent+child+nested grandchild; `git -c protocol.file.allow=always submodule add <local fixture path> ...` only in fixture creation; create bundles with `git bundle create <path> --all`; put bundle bytes at `git-bundles` routes; catalogue entries record actual sha256/size; use `SubprocessRunner` for checkout (not network). Assert checked HEAD and every recursive status mark, and no fetch to a remote. Corrupt bundle, absent child/grandchild, wrong child HEAD, detached correct commit, annotated tag, and moved tag all exercised. A moved tag test changes the tag but preserves the pinned commit in another ref, so merely checking that the commit exists cannot pass. Add direct unit preflight: all known recursive bundle entries required before any build. The backend must not recreate a tag before validating its original target.

- [ ] Test the module's public import in a fresh interpreter, not the warmed pytest process:

```python
import os
import sys


def test_gitbundles_import_without_backend_cycle() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", "import hammunition.gitbundles"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
```

Expected pre-fix FAIL: `ImportError` mentioning a partially initialized module (or missing module before creation). Move `GIT_ENV` to `gitbundles.py`, import it from there in `backends/git.py`, and keep backend classes out of `gitbundles` runtime imports. Use this shared constant in every command below:

```python
# gitbundles.py: runtime imports are leaf modules; annotations only are guarded.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from hammunition.backends.base import CommandRunner
    from hammunition.fetch import Fetcher
    from hammunition.offline import ResolutionContext
    from hammunition.manifest.schema import GitInstall

GIT_ENV: dict[str, str] = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
```

Use `from __future__ import annotations`. Put every runtime import of `BackendError`, `Command`, `Fetcher`, `RemoteArtifact` and `ResolutionContext` inside the function that needs it; importing `backends.base` at module scope initializes `backends/__init__.py` too. The module-level imports are stdlib, `catalogue` and `manifest.schema` only. The constant preserves both existing `GIT_ENV` members. The fresh interpreter test is included in the task's pytest command below; expected PASS after imports are acyclic.

- [ ] Run: `.venv/bin/pytest tests/test_git_bundles.py -q`; expected FAIL: missing module.
- [ ] Implement full naming and command checking helpers:

```python
def bundle_name(unit: str, commit: str, *, path: str | None = None,
                subcommit: str | None = None) -> str:
    from hammunition.backends.base import BackendError
    if COMMIT_SHA.fullmatch(commit) is None or "/" in unit:
        raise BackendError("git bundle needs a unit and full pinned commit")
    name = f"{unit}@{commit}"
    if path is not None:
        safe_relative(path, "submodule path")
        if subcommit is None or COMMIT_SHA.fullmatch(subcommit) is None:
            raise BackendError("submodule bundle needs its full gitlink commit")
        name += f"/{path}@{subcommit}"
    elif subcommit is not None:
        raise BackendError("submodule commit has no path")
    return name


def checked_git(runner: CommandRunner, cwd: Path, *argv: str) -> str:
    from hammunition.backends.base import BackendError, Command
    result = runner.run(Command(argv=("git", "-C", str(cwd), *argv), env=GIT_ENV,
                                description="Check the pinned Bunker git bundle"))
    if not result.ok:
        raise BackendError(f"git bundle check failed: {result.stderr.strip()}")
    return result.stdout.strip()
```

`checkout_bundle` performs these explicit stages using argv/runner and raises on every non-ok result:

1. Determine repo pin (`commit or ref`); require a full SHA. Fetch `context.entry("git-bundles", bundle_name(...))` by mirror-only route as `RemoteArtifact` **with publisher URL set to its mirror HTTP URL only when valid**; file mirrors must use a dummy HTTP identity and mirror transport. Verify catalogue-recorded sha256/size for bundle transport; these are not the code pin.
2. `git bundle verify <cache path>` in a fresh initialized repository; `git clone --no-checkout -- <bundle path> <destination>` with no upstream URL/credentials. For a tag, read `refs/tags/<ref>^{commit}` and compare to repository pin **before** checkout/recreating any tag; missing/moved tag refuses with existing `tag ... no longer resolves` wording.
3. `git checkout --detach <pinned SHA>`; existing `verify_pin` checks HEAD before build/patches. Do not pass a signed-catalogue sha256 as code authenticity.
4. Walk `git ls-tree -r -z HEAD`; parse each NUL record at its first tab; each `160000 commit <sha>\t<path>` is a gitlink. Validate path segments. Refuse a gitlink to `.git` or a path escaping destination. For each, derive full recursive path and binding bundle name, fetch the separate bundle, clone locally into that path, checkout exactly the **parent's pinned gitlink**, verify its HEAD, then recurse into its own gitlinks. The catalogue may not choose a submodule commit. No `git submodule update` against upstream occurs offline.
5. Verify `.gitmodules` maps every gitlink path (parse with `git config --file .gitmodules --get-regexp submodule.*.path`, no shell); register each local clone via its matching `submodule.<name>.url` pointing at its local bundle and `submodule.<name>.active=true`, so existing `git submodule status --recursive` verifies the resulting tree. Never enable file protocol for untrusted network URLs; no remote update is run. All submodule paths must be below the superproject tree.
6. Return an outcome naming HEAD, recursive count and Task 5 provenance. Retain build Python, prepare, patches and extra-file steps **after** checks; prepare scripts that can fetch dependencies remain a disclosed phase-2/offline gap unless their existing declared inputs prove them self-contained.

Implement recursion as `checkout_gitlinks(root: Path, repo: Path, unit: str, parent_commit: str, *, fetcher: Fetcher, context: ResolutionContext, runner: CommandRunner) -> int` in this task, with helper `fetch_bundle(unit: str, parent_commit: str, *, path: str | None, subcommit: str | None, fetcher: Fetcher, context: ResolutionContext) -> Path` and `clone_checked(bundle: Path, destination: Path, commit: str, *, runner: CommandRunner) -> None`. These helpers share the parent pin in names and validate each clone's HEAD; use the complete functions below, preserving argv-based execution. Keep `GitBackend.verify_submodules`'s nonempty/status-mark gate as the final check when block.submodules is true.

Full new git-bundle functions (local import `Fetcher`/`RemoteArtifact` inside `fetch_bundle` prevents the existing eager backend import cycle):

```python
def fetch_bundle(unit: str, parent_commit: str, *, path: str | None,
                 subcommit: str | None, fetcher: Fetcher,
                 context: ResolutionContext) -> Path:
    from hammunition.fetch import Fetcher, MirrorPath
    from hammunition.manifest.schema import RemoteArtifact
    name = bundle_name(unit, parent_commit, path=path, subcommit=subcommit)
    row = context.entry("git-bundles", name)
    if row.sha256 is None or row.size is None:
        raise BackendError(f"git bundle {name}: no held payload")
    # The digest verifies transport; checked HEAD/gitlinks verify code identity.
    identity = RemoteArtifact(url="https://bundle.example.invalid/" + row.sha256 + ".bundle",
                              sha256=row.sha256)
    only_mirror = Fetcher(fetcher.cache_dir, mirror=fetcher.mirror,
                          mirror_transport=fetcher.mirror_transport, offline=True)
    got = only_mirror.fetch(identity, max_bytes=row.size + 1024 * 1024,
                            mirror=MirrorPath("git-bundles", name))
    if got.size != row.size:
        raise BackendError(f"git bundle {name}: recorded size differs")
    return got.path


def clone_checked(bundle: Path, destination: Path, commit: str, *,
                  runner: CommandRunner) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bundle-verify-", dir=destination.parent) as directory:
        scratch = Path(directory)
        checked_git(runner, scratch, "init", "--quiet")
        checked_git(runner, scratch, "bundle", "verify", str(bundle))
    checked_git(runner, destination.parent, "clone", "--no-checkout", "--", str(bundle), str(destination))
    checked_git(runner, destination, "checkout", "--quiet", "--detach", commit)
    head = checked_git(runner, destination, "rev-parse", "HEAD")
    if head != commit:
        raise BackendError(f"git bundle HEAD {head} differs from pinned commit {commit}")


def checkout_gitlinks(root: Path, repo: Path, unit: str, parent_commit: str, *,
                      fetcher: Fetcher, context: ResolutionContext,
                      runner: CommandRunner) -> int:
    output = checked_git(runner, repo, "ls-tree", "-r", "-z", "HEAD")
    links: list[tuple[str, str]] = []
    for record in output.split("\x00"):
        if not record:
            continue
        metadata, separator, path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise BackendError("git bundle contains an unreadable tree record")
        if fields[0] != "160000":
            continue
        safe_relative(path, "submodule path")
        if ".git" in path.split("/") or fields[1] != "commit" or COMMIT_SHA.fullmatch(fields[2]) is None:
            raise BackendError("git bundle contains an unsafe gitlink")
        links.append((path, fields[2]))
    if not links:
        return 0
    config = checked_git(runner, repo, "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    names: dict[str, str] = {}
    for line in config.splitlines():
        pair = line.split(maxsplit=1)
        if len(pair) != 2 or pair[1] in names:
            raise BackendError("git bundle .gitmodules has duplicate/unreadable paths")
        names[pair[1]] = pair[0].removesuffix(".path")
    total = 0
    for path, commit in links:
        if path not in names:
            raise BackendError(f"gitlink {path} has no .gitmodules entry")
        destination = repo / path
        relative = destination.relative_to(root).as_posix()
        # clone --no-checkout leaves a gitlink directory empty; reject links/files.
        if destination.is_symlink() or destination.exists() and not destination.is_dir():
            raise BackendError(f"unsafe submodule destination {relative}")
        if destination.is_dir():
            if any(destination.iterdir()):
                raise BackendError(f"submodule destination {relative} is not empty")
            destination.rmdir()
        bundle = fetch_bundle(unit, parent_commit, path=relative, subcommit=commit,
                              fetcher=fetcher, context=context)
        clone_checked(bundle, destination, commit, runner=runner)
        checked_git(runner, repo, "config", names[path] + ".url", str(bundle))
        checked_git(runner, repo, "config", names[path] + ".active", "true")
        total += 1 + checkout_gitlinks(root, destination, unit, parent_commit,
                                       fetcher=fetcher, context=context, runner=runner)
    return total


def checkout_bundle(unit: str, block: GitInstall, destination: Path, *,
                    fetcher: Fetcher, context: ResolutionContext,
                    runner: CommandRunner) -> str:
    commit = block.commit or block.ref
    if COMMIT_SHA.fullmatch(commit) is None:
        raise BackendError(f"{unit}: tag {block.ref} has no recorded commit; offline bundle verification needs a repository pin")
    bundle = fetch_bundle(unit, commit, path=None, subcommit=None, fetcher=fetcher, context=context)
    clone_checked(bundle, destination, commit, runner=runner)
    if COMMIT_SHA.fullmatch(block.ref) is None:
        actual = checked_git(runner, destination, "rev-parse", f"refs/tags/{block.ref}^{{commit}}")
        if actual != commit:
            raise BackendError(f"tag {block.ref} no longer resolves to the commit it is pinned to: {commit}, got {actual}")
    count = checkout_gitlinks(destination, destination, unit, commit,
                              fetcher=fetcher, context=context, runner=runner) if block.submodules else 0
    context.note("git-bundles", bundle_name(unit, commit), fallback=not context.offline)
    return f"HEAD is {commit}, matching the pin; {count} recursive submodule(s) checked"
```

Before replacing normal clone commands, preserve the existing prepare Action (clears old source tree). The bundle Action takes the place of init/remote/fetch/checkout/tag recreation and precedes the unchanged `verify_pin` Action. When block.submodules is true, bundle mode omits the remote submodule-update Command but retains final `verify_submodules`. Guard recursion with a visited `(repo path, commit)` set and a maximum depth 64 so a malicious recursive `.gitmodules` layout cannot exhaust the process; reject a symlink in **every parent component**, not just the final destination, using descriptor/tree staging guards already used by source extraction. Test both failures. Do not consider the parent complete until all recursive bundles are fetched and verified. A missing child prevents every build step, with the tree left only in the build cache.

Real moved-tag test added to `tests/test_git_bundles.py`:

```python
import pytest
from pathlib import Path
from hammunition.backends.base import BackendError, SubprocessRunner
from hammunition.fetch import Fetcher, mirror_url, MirrorPath
from hammunition.gitbundles import checkout_bundle
from hammunition.manifest.schema import GitInstall
from bunker_fixtures import artifact, make_context
from test_fetch_mirror import Routes


def test_bundle_with_moved_tag_is_refused_before_build(tmp_path: Path) -> None:
    repo, pin = repository(tmp_path, "upstream")
    git(repo, "-c", "tag.gpgSign=false", "tag", "v1.0", pin)
    (repo / "file").write_text("changed")
    git(repo, "add", "file")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "new")
    git(repo, "-c", "tag.gpgSign=false", "tag", "-f", "v1.0", "HEAD")
    bundle = tmp_path / "parent.bundle"
    git(repo, "bundle", "create", str(bundle), "--all")
    name = bundle_name("thing", pin)
    body = bundle.read_bytes()
    context = make_context(tmp_path / "keys", [artifact("git-bundles", name, body)])
    route = mirror_url("http://bunker.invalid", MirrorPath("git-bundles", name))
    transport = Routes({route: body})
    fetcher = Fetcher(tmp_path / "cache", mirror="http://bunker.invalid", mirror_transport=transport,
                      transport=transport, offline=True)
    block = GitInstall(repo="https://example.invalid/thing", ref="v1.0", commit=pin,
                       build_system="cmake")
    with pytest.raises(BackendError, match="tag v1.0 no longer resolves"):
        checkout_bundle("thing", block, tmp_path / "build", fetcher=fetcher,
                        context=context, runner=SubprocessRunner())
    assert transport.requested == [route]
```

Online with an enrolled catalogue tries a bundle when it lists the pin, then publisher on mirror failure (D-070); offline cannot fallback. Only use publisher fallback before build, clear partial checkout first. Automatic planning fallback also uses bundles after the publisher probe exhausts retries. A tag without repo commit is a documented named gap in both `artifacts` and offline planning, not quietly promoted to trust.

- [ ] Run: `.venv/bin/pytest tests/test_git_bundles.py tests/test_git_backend.py tests/test_git_comaps.py -q`; expected PASS, real git only against temporary local repos.
- [ ] Commit:

```bash
git add src/hammunition/gitbundles.py src/hammunition/backends/git.py src/hammunition/cli/main.py tests/test_git_bundles.py
git commit -m "feat: verify mirrored git bundles and every pinned gitlink"
```

### Task 16: A10 — `artifacts --json` describes every payload, sheet, input and git pin

**Files:**
- Modify: `src/hammunition/manifest/schema.py:2453-2460` (additive licence metadata), `src/hammunition/artifacts.py:38-153,167-233,299-344,417-479`, `src/hammunition/interface/artifacts.py:39-151`, `src/hammunition/cli/main.py:1760-1848,8605-8640`.
- Create: `tests/test_artifacts_bunker.py`.
- Regenerate: `docs/reference/json-interface.md`; hand-edit `docs/reference/cli.md` (no CLI generator).
- Read: `tests/test_artifacts.py:171-227,597-616,746-766`, `src/hammunition/manifest/schema.py:228-242,528-628,818-847,945-1016,1070-1090,1517-1554`.

**Interfaces:**
- Consumes: payload helpers, bundle_name, carried indices, existing list_artifacts probes and Task 7 selection codecs. No station reads.
- Produces: `payload_entries(unit: str, block: SourceInstall | BinaryInstall | VenvInstall | NodeInstall | GitInstall | DerivedDataInstall, licence: str) -> tuple[ArtifactEntry, ...]`; existing `list_artifacts(..., bound: TopoBound = ALL, gateway: GatewayProbe | None = None) -> tuple[ArtifactEntry, ...]`.
- Produces: `InputEntry(Strict)` with `kind: str`, `region: str`, `name: str`, `url: str | None`, `sha256: str | None`, `size: int | None`, `content: str | None`, `deferred: str | None`; `GitPinEntry(Strict)` with `unit: str` (always `git-bundles`), `name: str`, `repo: str`, `ref: str`, `commit: str | None`, `submodules: bool`, `licence: str`, `deferred: str | None`.
- Produces: additive `ArtifactsDocument.inputs: tuple[InputEntry, ...] = ()`, `git_pins: tuple[GitPinEntry, ...] = ()`; `list_inputs(regions: Sequence[str], *, catalog_root: Path, probe: Probe, bound: TopoBound = ALL) -> tuple[InputEntry, ...]`; `list_git_pins(units: Sequence[str], catalog: Mapping[str, PackageManifest]) -> tuple[GitPinEntry, ...]`.
- Topo names use `quad.path`, FSTopo `quad.name`, payloads `payload_name`, tools/extras same, inputs `<kind>/<name>` under `inputs/`; git pin names use the binding contract. Bundle sha256 is computed by the Bunker after construction, never fabricated by artifacts.

- [ ] Write failing test using a real existing source manifest and no station:

```python
from pathlib import Path
from hammunition.artifacts import list_git_pins, payload_entries
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import SourceInstall
from hammunition.payloads import payload_name


def test_all_source_payloads_use_the_install_route() -> None:
    root = Path(__file__).resolve().parents[1] / "catalog/packages"
    catalog = load_catalog(root)
    sources = [(m, b.install) for m in catalog.values() for b in m.install
               if isinstance(b.install, SourceInstall)]
    assert sources
    for manifest, block in sources:
        entries = payload_entries(manifest.name, block, "licence not recorded in this manifest")
        assert len(entries) == 1
        assert entries[0].name == payload_name(block.source)
        assert entries[0].digest == block.source.sha256
```

Add backend-name parity tests for binary/venv/Node, converter-tool and git extras; default fetching set includes these but not apt-only/derived-without-tool; unknown explicit unit still refuses. Use `_root`, `_list`, `Geofabrik`, `Bucket` from existing artifact tests (or move shared fixture into `tests/bunker_fixtures.py`). Topo/FSTopo inputs and sheets are selected from the same synthetic Delaware outline and bound as the install; compare sets and exact stable names. All five input kinds listed, input `sha256` and size match actual content, outline requests memoized. Missing/malformed outline becomes a deferred input/sheet entry, never dropped. Book/map/data existing names unchanged. Git parent pin named correctly; tag lacking commit has named deferral; recursive names are generated from pinned gitlinks by the bundle writer using Task 15 helper (not guessed at listing time). `cmd_artifacts` remains station-independent even with another selection saved. Golden JSON uses new additive arrays and validates against `Strict` dataclasses.

- [ ] Add exact ETag, inline-input bound, unit filtering and licence tests:

```python
import pytest
from hammunition.artifacts import (
    SelectionError, input_entry, input_regions, etag_entry, list_git_pins,
)
from hammunition.manifest.schema import (
    PackageManifest, TopoQuadsInstall, DemTilesInstall,
)


@pytest.mark.parametrize("etag", ["a" * 32, "b" * 32 + "-94"])
def test_sheet_etag_is_raw_and_part_size_is_nullable(etag: str) -> None:
    for unit, name in [("usgs-ustopo", "DE/fixture_20260101"),
                       ("dem-3dep", "USGS_13_n39w076")]:
        row = etag_entry(unit, name, "https://example.invalid/sheet.tif",
                         etag, 123, "public domain")
        assert row.check == "etag-md5" and row.digest == etag
        assert row.part_size is None
        assert etag_entry(unit, name, row.url, etag, 123,
                          "public domain", part_size=5 * 1024 * 1024).part_size == 5242880


def test_inline_input_bound_names_the_input() -> None:
    row = input_entry("region-outline", "europe/monaco", "europe/monaco.poly",
                      "x" * (8 * 1024 * 1024))
    assert row.size == 8 * 1024 * 1024 and row.content is not None
    with pytest.raises(SelectionError) as caught:
        input_entry("region-outline", "europe/monaco", "europe/monaco.poly",
                    "é" * (4 * 1024 * 1024 + 1))
    assert str(caught.value) == (
        "input region-outline/europe/monaco.poly is over 8 MiB; "
        "narrow the selection with --units"
    )


def test_units_filter_inputs_and_git_pins_and_carry_licence() -> None:
    catalog = load_catalog(Path(__file__).resolve().parents[1] / "catalog/packages")
    regions = ("europe/monaco", "north-america/us/delaware")
    assert input_regions(("ics-forms",), regions, catalog) == ()
    assert input_regions(("osm-regions",), regions, catalog) == regions
    git_units = [unit for unit in catalog if list_git_pins((unit,), catalog)]
    assert len(git_units) >= 2
    selected = git_units[0]
    catalog[selected] = catalog[selected].model_copy(update={"licence": "GPL-3.0-or-later"})
    pins = list_git_pins((selected,), catalog)
    assert pins and all(p.name.startswith(selected + "@") for p in pins)
    assert all(p.licence == "GPL-3.0-or-later" for p in pins)
    assert not list_git_pins(("ics-forms",), catalog)


def test_cli_units_filter_inline_arrays(capsys: pytest.CaptureFixture[str]) -> None:
    import importlib
    import json
    cli = importlib.import_module("hammunition.cli.main")
    assert cli.main(["artifacts", "--units", "ics-forms", "--map-regions",
                     "europe/monaco", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["inputs"] == []
    assert doc["git_pins"] == []

```

Expected pre-fix FAIL: missing `etag_entry`/`input_regions`, no `part_size`, or over-limit input accepted. Include these tests in both Task 16 pytest runs below. Add a `cmd_artifacts --units ics-forms --map-regions europe/monaco --json` assertion that both arrays are empty; for `--units osm-regions` every input's region is explicitly selected. No station regions may enter either array.

- [ ] Run: `.venv/bin/pytest tests/test_artifacts_bunker.py -q`; expected FAIL: cannot import `payload_entries`.
- [ ] Implement complete payload selector:

```python
def payload_entries(unit: str,
                    block: SourceInstall | BinaryInstall | VenvInstall | NodeInstall | GitInstall | DerivedDataInstall,
                    licence: str) -> tuple[ArtifactEntry, ...]:
    items: list[tuple[RemoteArtifact, int | None, str]] = []
    if isinstance(block, SourceInstall):
        items.append((block.source, None, licence))
    elif isinstance(block, BinaryInstall | NodeInstall):
        items.append((block.artifact, None, licence))
    elif isinstance(block, VenvInstall) and block.payload is not None:
        items.append((block.payload, None, block.licence or licence))
    elif isinstance(block, DerivedDataInstall) and block.tool is not None:
        items.append((block.tool.artifact, block.tool.size, block.tool.licence))
    elif isinstance(block, GitInstall):
        for extra in block.extra_files:
            if extra.artifact is not None:
                items.append((RemoteArtifact(url=extra.artifact.url, sha256=extra.artifact.sha256),
                              extra.artifact.size, licence))
    return tuple(ArtifactEntry(unit, payload_name(pin), pin.url, "sha256", pin.sha256,
                               None, size, licence, None) for pin, size, licence in items)
```

Add an optional manifest `licence` field to `PackageManifest` in `manifest/schema.py` (this checkout has no top-level one), keeping existing manifests readable. Do not infer the program's licence from the YAML file's CC0 header. Missing metadata remains explicit:

```python
# PackageManifest field:
licence: str = Field(default="licence not recorded in this manifest", min_length=1)

# ArtifactEntry: append after required fields, preserving existing positional callers.
part_size: int | None = field(default=None, metadata={"doc": "ETag multipart part size in bytes, or null when not recorded"})

# artifacts.py: imports ArtifactEntry; no stripping quotes, suffixes or rehashing.
def etag_entry(unit: str, name: str, url: str, etag: str, size: int,
               licence: str, *, part_size: int | None = None) -> ArtifactEntry:
    return ArtifactEntry(unit, name, url, "etag-md5", etag, None, size,
                         licence, None, part_size=part_size)


def input_regions(units: Sequence[str], regions: Sequence[str],
                  catalog: Mapping[str, PackageManifest]) -> tuple[str, ...]:
    regional = (RegionalDataInstall, DemTilesInstall, TopoQuadsInstall)
    needs_regions = any(isinstance(entry.install, regional)
                        for unit in units for entry in catalog[unit].install)
    return tuple(dict.fromkeys(regions)) if needs_regions else ()
```

Import `RegionalDataInstall`, `DemTilesInstall` and `TopoQuadsInstall`; `DemTilesInstall.provider` distinguishes Copernicus/3DEP and `TopoQuadsInstall.provider` distinguishes US Topo/FSTopo. Route every selected US Topo quad through `etag_entry(unit, quad.path, quad.url, quad.etag, quad.size, block.licence)` and each 3DEP row through `etag_entry(unit, row.name, tile_url(row.name), row.etag, row.size, block.licence)`; carried rows currently lack a part-size column, so emit null, never guess one from the multipart suffix. Update `ArtifactEntry.digest` documentation to allow the raw `<hex>-<parts>` ETag as well as hex digests. Inputs stay inline (`content` contains the exact UTF-8 text); the 8 MiB bound below counts encoded bytes, names the oversized input and refuses the command, rather than returning a deferred/null row.

In `cmd_artifacts`, construct arrays using the resolved `units` from `select_units`, not all catalogue keys; catch `SelectionError` from input generation through the existing unplannable error path:

```python
inputs = list_inputs(input_regions(units, regions, catalog),
                     catalog_root=catalog_root, probe=region_probe, bound=bound)
git_pins = list_git_pins(units, catalog)
# Pass inputs=inputs, git_pins=git_pins to ArtifactsDocument alongside existing fields.
```

Here `region_probe` is the shared `MemoProbe(UrllibProbe())` and `bound` is the explicit CLI bound or `ALL`, defined before `list_artifacts`; use that same probe and bound for both calls. Include `src/hammunition/manifest/schema.py` in Files/commit, document the additive licence field, and regenerate the JSON reference only.

Extend `FETCHING` / `Block` to these classes and `TopoQuadsInstall` (derived only when it has a tool; git is fetching for bundles even without extras). `_blocks` filters variants explicitly, not truthiness of empty list. List every target variant, deduplicate identical `(unit,name,digest)` and refuse conflicting names using existing `_data` rule. Do not invent payload size/licence where manifests do not carry them; emit null size and “licence not recorded in this manifest” rather than claiming verification metadata exists. `artifacts` reports what is available; Bunker obtains actual size and sha256 when fetching. Its recorded size does not become a repo pin.

For topo/FSTopo, reuse carried index and Task 7 outline selection, then emit each selected sheet using the same unit/name/digest kind as backend. FSTopo unpinned entries are `unverified-fetch`, publisher_digest null; pinned entries retain repo sha256; gateway probe injected. 3DEP entries use carried ETags and size. No install/prefix/local record is read to produce a listing.

`list_inputs` reads each outline once, computes its sha256/size, uses `parse_poly` and carried lists to build the four existing text selection records under `ALL` (or the explicitly supplied bound), and emits UTF-8 `content` for derived records. The Bunker writes these bytes into the contract layout; the engine's Task 7 reader consumes that identical codec. For bounded selections, require explicit CLI grid/bound flags, never station config. If no explicit bound is given, export whole-region selections that the laptop can safely narrow. Inputs unavailable from a probe are entries with `deferred`; they do not silently become zero-byte content.

Add these complete document row types before the listing functions:

```python
@dataclass(frozen=True)
class InputEntry(Strict):
    kind: str = described("one of the five Bunker input kinds")
    region: str = described("explicitly requested Geofabrik region")
    name: str = described("name within inputs/<kind>/, including region path")
    url: str | None = described("publisher URL for an outline; null for locally derived selections")
    sha256: str | None = described("sha256 of the exact content below; null when deferred")
    size: int | None = described("UTF-8 content size in bytes; null when deferred")
    content: str | None = described("exact UTF-8 input bytes for the Bunker writer to store; null when deferred")
    deferred: str | None = described("reason this input cannot be produced; null when available")

@dataclass(frozen=True)
class GitPinEntry(Strict):
    unit: str = described("git-bundles")
    name: str = described("unit@repository-pinned-commit; diagnostic only when deferred")
    repo: str = described("upstream repository the Bunker clones at this pin")
    ref: str = described("repository manifest tag or commit ref")
    commit: str | None = described("full repository commit, null for an unpinned tag")
    submodules: bool = described("writer must recursively enumerate pinned gitlinks and produce separate bundles")
    licence: str = described("manifest licence field, carried verbatim")
    deferred: str | None = described("why no verifiable bundle can be named")
```

Add the two optional arrays after ArtifactsDocument's required fields using `field(default=(), metadata={"doc": ...})`, preserving constructors for existing callers. Text rendering lists each input and git pin or its deferral after the current artifact section; one JSON document contains all three arrays. Keep the artifact array's existing field meanings, extending `digest` to describe raw multipart ETags and adding nullable `part_size`.

Full git listing function:

```python
def list_git_pins(units: Sequence[str], catalog: Mapping[str, PackageManifest]) -> tuple[GitPinEntry, ...]:
    out: dict[str, GitPinEntry] = {}
    for unit in units:
        if unit not in catalog:
            continue
        for entry in catalog[unit].install:
            block = entry.install
            if not isinstance(block, GitInstall):
                continue
            commit = block.commit or (block.ref if COMMIT_SHA.fullmatch(block.ref) else None)
            if commit is None:
                name = f"{unit}@{block.ref}"
                out[name] = GitPinEntry("git-bundles", name, block.repo, block.ref, None,
                                       block.submodules, catalog[unit].licence, "tag has no recorded commit; offline bundle verification needs a repository pin")
            else:
                name = bundle_name(unit, commit)
                out[name] = GitPinEntry("git-bundles", name, block.repo, block.ref, commit,
                                       block.submodules, catalog[unit].licence, None)
    return tuple(out.values())
```

The name with a tag is diagnostic only for a deferred entry and is never a fetchable bundle contract name. Do not present it as a payload the Bunker can serve.

Full input listing implementation, using existing codecs and explicit catalog root (import aliases exactly as named here from their real modules):

```python
from hammunition.backends.dem import RegionTiles, render_record as render_tiles
from hammunition.backends.topo import RegionQuads, render_record as render_quads
from hammunition.backends.fstopo import RegionSheets, render_record as render_sheets
from hammunition.ustopo import load_index as load_ustopo_index
from hammunition.fstopo import load_index as load_fstopo_index
from hammunition.usgs3dep import load_tile_list as load_3dep_list, tile_name as threedep_name


MAX_INPUT_BYTES = 8 * 1024 * 1024


def input_entry(kind: str, region: str, name: str, text: str, url: str | None = None) -> InputEntry:
    body = text.encode("utf-8")
    if len(body) > MAX_INPUT_BYTES:
        raise SelectionError(f"input {kind}/{name} is over 8 MiB; narrow the selection with --units")
    return InputEntry(kind, region, name, url, hashlib.sha256(body).hexdigest(), len(body), text, None)


def list_inputs(regions: Sequence[str], *, catalog_root: Path, probe: Probe,
                bound: TopoBound = ALL) -> tuple[InputEntry, ...]:
    out: list[InputEntry] = []
    kinds = ("region-outline", "tile-selection", "sheet-selection", "dem3dep-selection", "fstopo-selection")
    for region in dict.fromkeys(regions):
        url = f"{BASE}/{region}.poly"
        try:
            text = probe.text(url)
            outer, holes = parse_poly(text)
        except (GeofabrikError, CopernicusError, OSError) as exc:
            for kind in kinds:
                extension = "poly" if kind == "region-outline" else "tiles" if "tile" in kind or kind == "dem3dep-selection" else "quads"
                out.append(InputEntry(kind, region, f"{region}.{extension}", None, None, None, None, str(exc)))
            continue
        out.append(input_entry("region-outline", region, f"{region}.poly", text, url))
        slug = region.replace("/", "-")
        squares = squares_touching(outer, holes)
        for kind in kinds[1:]:
            try:
                if kind == "tile-selection":
                    listed = load_tile_list(catalog_root / TILE_LIST)
                    names, unpublished = select(squares, listed)
                    body = render_tiles(RegionTiles(region, slug, names, unpublished))
                    extension = "tiles"
                elif kind == "dem3dep-selection":
                    listed3 = load_3dep_list(catalog_root / "data/usgs-3dep-tiles.txt")
                    names3 = {threedep_name(square) for square in squares}
                    selected3 = tuple(sorted(name for name in names3 if name in listed3 and bound.keeps(tile_box(name)))) if bound.wants_region(region) and bound.mode != "none" else ()
                    body = render_tiles(RegionTiles(region, slug, selected3, len(names3 - set(listed3)), bound.token))
                    extension = "tiles"
                elif kind == "sheet-selection":
                    quads = load_ustopo_index(catalog_root / "data/ustopo-quads.txt").select(outer, holes)
                    kept = bound.select(quads) if bound.wants_region(region) and bound.mode != "none" else ()
                    body = render_quads(RegionQuads(region, slug, kept, bound.token))
                    extension = "quads"
                else:
                    sheets = load_fstopo_index(catalog_root / "data/fstopo-quads.txt").select(outer, holes)
                    kept_sheets = bound.select(sheets) if bound.wants_region(region) and bound.mode != "none" else ()
                    body = render_sheets(RegionSheets(region, slug, kept_sheets, bound.token))
                    extension = "quads"
                out.append(input_entry(kind, region, f"{region}.{extension}", body))
            except (CopernicusError, UstopoError, FstopoError, OSError) as exc:
                extension = "tiles" if kind in ("tile-selection", "dem3dep-selection") else "quads"
                out.append(InputEntry(kind, region, f"{region}.{extension}", None, None, None, None, str(exc)))
    return tuple(out)
```

Import `tile_box` from `hammunition.terrain_plan`, `ALL`/`TopoBound` from topo_bound, and `UstopoError`/`FstopoError` from their respective modules. The same text codecs are exposed for the Bunker writer; no invented executable “selection” file. Make `MemoProbe` shared between `list_inputs` and artifact sheet/tile listing so the outline is fetched once. Do not use installed records to generate inputs.

`list_git_pins` emits parent pin/ref/repo/submodules with name `bundle_name(manifest.name, block.commit or block.ref)`. For an unpinned tag produce `commit=None`, `deferred="tag has no recorded commit; offline bundle verification needs a repository pin"`. Recursive bundle names are not knowable from current manifests alone (they carry only `submodules: bool`): document that the Bunker enumerates pinned gitlinks through the Task 15 engine API, and its generated catalogue lists every recursive artifact. No network Git clone is hidden in `artifacts`. Add this limitation to the reference so Plan B has the exact division of responsibility.

- [ ] Run:

```bash
.venv/bin/pytest tests/test_artifacts.py tests/test_artifacts_bunker.py -q
python3 scripts/gen_json_reference.py
python3 scripts/gen_json_reference.py --check
python3 scripts/check_doc_links.py
```

Expected PASS; inspect regenerated artifacts goldens, especially old stable names and additive fields.
- [ ] Commit:

```bash
git add src/hammunition/manifest/schema.py src/hammunition/artifacts.py src/hammunition/interface/artifacts.py src/hammunition/cli/main.py tests/test_artifacts_bunker.py tests/fixtures/json/artifacts.json docs/reference/json-interface.md docs/reference/cli.md docs/reference/bunker-catalogue.md
git commit -m "feat: describe Bunker payloads inputs sheets and git pins"
```

### Task 17: A11 — Apt-only security-key profile and complete documentation

**Files:**
- Create: `catalog/profiles/security-keys.yaml`, `catalog/packages/pcscd.yaml`, `catalog/packages/opensc.yaml`, `catalog/packages/fido2-tools.yaml`, `catalog/packages/yubikey-manager.yaml`, `catalog/packages/libpam-u2f.yaml`, `tests/test_security_keys_catalog.py`.
- Regenerate: `docs/packages/pcscd.md`, `opensc.md`, `fido2-tools.md`, `yubikey-manager.md`, `libpam-u2f.md`, package index, `docs/profiles/security-keys.md`, profile index, affected generated wiki/project/category pages.
- Modify: `mkdocs.yml` (profile nav).
- Read: `catalog/packages/pcsc-tools.yaml:1-37`, `catalog/packages/inspectrum.yaml:1-45`, `catalog/profiles/station.yaml:1-121`, `catalog/profiles/rf-security.yaml:1-97`, `src/hammunition/manifest/schema.py:2437-2453,3130-3229`, `scripts/gen_package_reference.py:1-75`, `scripts/gen_profile_reference.py:55-115`.

**Interfaces:**
- Consumes: existing YAML schema and `load_all(catalog_root: Path)`; existing `rfid` category, apt backend.
- Produces: `security-keys` flat profile containing exactly the five new units; pcscd unit also installs `libpcsclite1`, fido2-tools unit also installs `libfido2-1`. Existing `pcsc-tools` is diagnostic tooling and is not a substitute for any requested manifest; none of the five names currently exists. No PAM/system-modification config block, no consent gate on package installation, no new executable logic in catalog.

- [ ] Write failing test:

```python
from pathlib import Path
from hammunition.manifest.load import load_all
from hammunition.manifest.schema import AptInstall


def test_security_keys_is_apt_only_and_never_configures_pam() -> None:
    root = Path(__file__).resolve().parents[1] / "catalog"
    catalog, profiles = load_all(root)
    profile = profiles["security-keys"]
    names = {"pcscd", "opensc", "fido2-tools", "yubikey-manager", "libpam-u2f"}
    assert set(profile.packages) == names and profile.consent is None
    for name in names:
        manifest = catalog[name]
        assert all(isinstance(block.install, AptInstall) for block in manifest.install)
        assert not manifest.config_files and not manifest.user_services
        doc = manifest.documentation
        assert doc.what_it_does and doc.why_you_want_it and doc.prerequisites
        assert doc.known_problems and doc.upstream_url and doc.upstream_support
    assert "PAM" in profile.documentation.deliberately_excludes
```

Also assert every required profile page field/goals/first_ten_minutes is populated, libpam-u2f has no debconf preseeding/reconfigure_after/system_modifications, and apt resolution uses archive availability rather than static target conditions. No fabricated footprint measurement. Run existing schema/catalog/profile documentation tests across targets; package installation success remains measured by target harness, not unit mocks.

- [ ] Run: `.venv/bin/pytest tests/test_security_keys_catalog.py -q`; expected FAIL: `KeyError: 'security-keys'`.
- [ ] Write the profile with this complete content:

```yaml
# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: CC0-1.0
name: security-keys
summary: Hardware signing keys and smartcard tools for your station
stage: "1.0"
packages: [pcscd, opensc, fido2-tools, yubikey-manager, libpam-u2f]
documentation:
  what_it_installs: >-
    PC/SC and OpenSC for PIV smartcards, FIDO2 tools and their device-access
    rules, YubiKey management tools and the U2F PAM module. All are from your
    distribution's archive. Installing the module does not enable token login.
  why_together: >-
    These provide the tools for signing a Bunker catalogue with a hardware key
    and checking that the station can reach its token. FIDO2 is not tied to one
    vendor; PIV signing uses OpenSSH through the agent and OpenSC.
  deliberately_excludes: >-
    Enabling PAM for login or sudo, changing account authentication, generating
    keys or enrolling a Bunker automatically. PAM enablement is a separate task.
  manual_configuration: >-
    Run hammunition doctor, then enrol your Bunker with hammunition mirror enrol
    URL. Generate or load your signing key with the token's tools. OpenSSH 8.2+
    is needed. Ubuntu's ykman 5.2.1 support for newer PIV algorithms is unmeasured;
    use the token's own tools if it cannot generate the required key. No PyPI
    fallback is installed by this profile. Never publish your private key.
  disk_footprint_hint: >-
    Not measured for the complete dependency closure; apt's dry run reports what
    your target needs. Do not treat installed package size as download size.
  who_for: Operators using hardware signing keys or PIV smartcards with a Bunker.
  hardware_assumed: A FIDO2 authenticator or PIV token to use the tools; none to install.
  footprint_short: Not yet measured
  excludes_short: PAM enablement and automatic key generation
  goals:
    - Sign and verify my Bunker's catalogue with a hardware key
  first_ten_minutes:
    - "Read the plan: `hammunition install security-keys --dry-run`."
    - "Install with `hammunition install security-keys`; PAM stays unconfigured."
    - "Connect your token and run `hammunition doctor` as yourself, without sudo."
    - "Read [the Bunker catalogue reference](../reference/bunker-catalogue.md)."
    - "Enrol with `hammunition mirror enrol URL`; type the fingerprint after checking it."
```

Create the manifests from the following **complete data**; write the YAML explicitly (the generator in this step only demonstrates identical required fields, it is not executable catalog data):

```python
# Run at implementation time from repository root, not in this planning turn.
from pathlib import Path
import yaml
rows = {
    "pcscd": ("2.3.3", ["pcscd", "libpcsclite1"],
        "PC/SC smartcard daemon for PIV readers",
        "Runs the service that applications use to talk to PC/SC smartcard readers and PIV tokens.",
        "It lets OpenSC and the SSH agent use a smartcard without each application claiming the reader.",
        "A PC/SC reader or PIV token. Check pcscd with systemctl status pcscd; do not configure PAM.",
        "pcscd and libnfc can claim the same reader; stop the unused service when they conflict.",
        "https://pcsclite.apdu.fr/", "https://github.com/LudovicRousseau/PCSC/issues"),
    "opensc": ("0.26.1", ["opensc"],
        "Smartcard and PKCS#11 tools for hardware signing",
        "Provides smartcard utilities and a PKCS#11 module that OpenSSH can load through ssh-agent.",
        "It is the PIV route for signing with a card instead of leaving an exportable private key on disk.",
        "A supported smartcard and running pcscd. Load its PKCS#11 module into your agent yourself.",
        "Token algorithm support differs by firmware and driver. A reader being listed does not prove a signing key is usable.",
        "https://github.com/OpenSC/OpenSC", "https://github.com/OpenSC/OpenSC/issues"),
    "fido2-tools": ("1.15.0", ["fido2-tools", "libfido2-1"],
        "FIDO2 authenticator discovery and management tools",
        "Lists FIDO2 authenticators and provides the library and device access rules used by OpenSSH security keys.",
        "It checks whether your session can reach a FIDO2 token before you try hardware-backed signing.",
        "A FIDO2 authenticator for detection. OpenSSH 8.2+ is required for security-key signing.",
        "A token may need a touch or PIN. Running tools as root hides missing user device permissions.",
        "https://github.com/Yubico/libfido2", "https://github.com/Yubico/libfido2/issues"),
    "yubikey-manager": ("5.6.1", ["yubikey-manager"],
        "YubiKey FIDO2 and PIV management utility",
        "Provides ykman for inspecting and managing YubiKey FIDO2 and PIV applications, including PINs and supported keys.",
        "It is useful for YubiKey owners; other manufacturers' tokens use their own tools or standard FIDO2 utilities.",
        "A supported YubiKey. Follow the token's own documentation before changing PINs or keys.",
        "Ubuntu ykman 5.2.1 with firmware 5.7 PIV Ed25519 or RSA 3072/4096 is unmeasured. No PyPI upgrade is installed here.",
        "https://github.com/Yubico/yubikey-manager", "https://github.com/Yubico/yubikey-manager/issues"),
    "libpam-u2f": ("1.4.0", ["libpam-u2f"],
        "U2F PAM module installed without enabling token login",
        "Provides the PAM module used by separately configured token-based login or sudo authentication.",
        "It makes the module available for a later reviewed setup; installing this unit alone changes no PAM configuration.",
        "PAM enabling is outside this profile and plan. Keep a working fallback login before any future authentication change.",
        "An incorrect PAM setup can prevent login. Hammunition does not write PAM files or register tokens in this phase.",
        "https://github.com/Yubico/pam-u2f", "https://github.com/Yubico/pam-u2f/issues"),
}
for name, (version, packages, summary, what, why, pre, problems, upstream, support) in rows.items():
    manifest = {"name": name, "version": version, "summary": summary, "categories": ["rfid"],
        "install": [{"install": {"method": "apt", "packages": packages}}],
        "update": {"probe": {"method": "apt_policy"}, "strategy": "apt_upgrade"},
        "documentation": {"what_it_does": what, "why_you_want_it": why,
                          "prerequisites": pre, "known_problems": problems,
                          "upstream_url": upstream, "upstream_support": support}}
    header = "# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC\n# SPDX-License-Identifier: CC0-1.0\n\n"
    Path(f"catalog/packages/{name}.yaml").write_text(header + yaml.safe_dump(manifest, sort_keys=False))
```

Versions here reflect the spec's Debian 13 measurement, not a required version or support guarantee; apt still probes the target. Package rules/daemon behaviour come from archive packages; this task writes no custom udev or service/PAM configuration. Reuse `pcsc-tools` only if adding diagnostic prose links; do not duplicate its manifest.

- [ ] Run:

```bash
python3 scripts/gen_package_reference.py
python3 scripts/gen_profile_reference.py
python3 scripts/gen_projects_page.py
python3 scripts/gen_application_directory.py
python3 scripts/gen_activity_hubs.py
python3 scripts/gen_package_reference.py --check
python3 scripts/gen_profile_reference.py --check
python3 scripts/gen_projects_page.py --check
python3 scripts/gen_application_directory.py --check
python3 scripts/gen_activity_hubs.py --check
.venv/bin/pytest tests/test_security_keys_catalog.py tests/test_profile_docs.py tests/test_categories.py tests/test_docs_generated.py tests/test_site.py -q
```

Expected PASS; inspect generated pages. Target container package probes must report actual candidate/install outcomes; do not mark unmeasured targets verified.
- [ ] Commit all five named manifests, profile and their generated docs:

```bash
git add catalog/profiles/security-keys.yaml catalog/packages/pcscd.yaml catalog/packages/opensc.yaml catalog/packages/fido2-tools.yaml catalog/packages/yubikey-manager.yaml catalog/packages/libpam-u2f.yaml docs/packages docs/profiles docs/projects.md docs/applications.md docs/activities mkdocs.yml tests/test_security_keys_catalog.py
git commit -m "feat: add apt-only security-key tools without enabling PAM"
```

### Task 18: A12 — Doctor's security-key and enrolled-key checks

**Files:**
- Create: `src/hammunition/security_keys.py`, `tests/test_security_keys_doctor.py`.
- Modify: `src/hammunition/doctor.py:51-61,245-295,680-740`, `src/hammunition/cli/main.py:7764-7965`, `src/hammunition/interface/doctor.py:1-93` (existing checks already serialize without new kind). Also modify `tests/conftest.py:177-210` (guard the new IO probe) and `tests/test_json_doctor.py:1-200` (inject the callback in existing CLI tests).
- Test: `tests/test_doctor.py`, `tests/test_json_doctor.py`.

**Interfaces:**
- Consumes: `doctor.Check`, `Status`, `KeyStrength`, owner-aware `load_mirror`, `CommandRunner`/`CommandResult` seams.
- Produces frozen `SecurityKeyState(pcscd_active: bool | None, fido2_present: bool | None, piv_present: bool | None, openssh: tuple[int, int] | None, fido2_access: bool | None, piv_access: bool | None, probed_as_operator: bool, enrolled: tuple[KeyStrength, ...])`.
- Produces: `security_key_checks(state: SecurityKeyState | None) -> list[Check]` (pure in doctor); `probe_security_keys(run: Callable[[tuple[str, ...]], CommandResult], *, as_operator: bool, enrolled: tuple[KeyStrength, ...]) -> SecurityKeyState` (IO seam in security_keys). `doctor.run_checks(..., security_keys: SecurityKeyState | None = None)`.

- [ ] Write failing pure tests:

```python
from pathlib import Path
from hammunition.doctor import security_key_checks
from hammunition.security_keys import SecurityKeyState
from hammunition.keystrength import classify
from bunker_fixtures import key


def test_tokens_absent_are_informational_and_weak_key_warns(tmp_path: Path) -> None:
    private = key(tmp_path, "rsa", 2048)
    strength = classify(private.with_suffix(".pub").read_text().strip())
    state = SecurityKeyState(False, False, False, (8, 1), None, None, True, (strength,))
    checks = security_key_checks(state)
    assert any(c.name == "FIDO2 token" and c.status == "info" for c in checks)
    assert any(c.name == "PIV token" and c.status == "info" for c in checks)
    assert any(c.name == "OpenSSH signing" and c.status == "warn" for c in checks)
    assert any(c.detail == strength.warning and c.status == "warn" for c in checks)
    assert not any(c.status == "fail" for c in checks)
```

Add (8,2) threshold passes, absent tools = unavailable not “no token”, pcscd active/socket activation, permission-denied FIDO2, present PIV driver/card, unrelated smartcard is not PIV, root run = permissions unmeasured not success, file-key-only and affirmed PIV stored strength output, no enrolled keys, malformed `ssh -V`, and stderr-only version output. Inject all subprocess results; no real token/pcsc daemon in CI. Test all probe argv are read-only and bounded, no PIN generation/touch signing/login change. JSON/text carry identical warnings.

- [ ] Run: `.venv/bin/pytest tests/test_security_keys_doctor.py -q`; expected FAIL: cannot import `security_key_checks`.
- [ ] Implement complete pure renderer:

```python
def security_key_checks(state: SecurityKeyState | None) -> list[Check]:
    if state is None:
        return []
    out: list[Check] = []
    fix = "hammunition install security-keys"
    out.append(Check("pcscd", "ok" if state.pcscd_active else "warn",
                     "PC/SC daemon active" if state.pcscd_active else "PC/SC daemon inactive or unavailable", fix if not state.pcscd_active else None))
    for label, present in (("FIDO2 token", state.fido2_present), ("PIV token", state.piv_present)):
        detail = "detected" if present else "not detected" if present is False else "detection unavailable"
        out.append(Check(label, "info", detail))
    ready = state.openssh is not None and state.openssh >= (8, 2)
    out.append(Check("OpenSSH signing", "ok" if ready else "warn",
                     "OpenSSH supports -sk signing" if ready else "OpenSSH 8.2+ required for -sk signing"))
    for label, present, access in (("FIDO2 access", state.fido2_present, state.fido2_access),
                                  ("PIV access", state.piv_present, state.piv_access)):
        if not state.probed_as_operator:
            out.append(Check(label, "info", "user access unmeasured: run hammunition doctor as yourself, without sudo"))
        elif present or access is False:
            out.append(Check(label, "ok" if access else "warn",
                             "token reachable without root" if access else "token access denied or unmeasured; inspect the archive's device rules"))
    for strength in state.enrolled:
        out.append(Check("Bunker key", "info",
                         f"{strength.fingerprint}: {strength.algorithm}, {strength.bits} bits"))
        if strength.warning is not None:
            out.append(Check("Bunker key strength", "warn", strength.warning))
    return out
```

Implement the complete probe function in security_keys.py (the CLI's IO callable returns timeout/missing-tool results as nonzero CommandResult, never raises past doctor):

```python
@dataclass(frozen=True)
class SecurityKeyState:
    pcscd_active: bool | None
    fido2_present: bool | None
    piv_present: bool | None
    openssh: tuple[int, int] | None
    fido2_access: bool | None
    piv_access: bool | None
    probed_as_operator: bool
    enrolled: tuple[KeyStrength, ...]


def probe_security_keys(run: Callable[[tuple[str, ...]], CommandResult], *,
                        as_operator: bool,
                        enrolled: tuple[KeyStrength, ...]) -> SecurityKeyState:
    service = run(("systemctl", "is-active", "pcscd"))
    active = service.stdout.strip() == "active" if service.returncode in (0, 3) else None
    ssh = run(("ssh", "-V"))
    version = re.search(r"OpenSSH_(\d+)\.(\d+)", ssh.stdout + ssh.stderr)
    openssh = (int(version[1]), int(version[2])) if version else None
    # Root cannot measure whether the ordinary operator can reach a token.
    if not as_operator:
        return SecurityKeyState(active, None, None, openssh, None, None, False, enrolled)
    fido = run(("fido2-token", "-L"))
    devices: list[str] = []
    if fido.ok:
        for line in fido.stdout.splitlines():
            first = line.split(":", 1)[0].strip()
            if first.startswith("/dev/hidraw") and first.removeprefix("/dev/hidraw").isdigit():
                devices.append(first)
    fido_present = bool(devices) if fido.ok else None
    fido_access: bool | None = False if not fido.ok and re.search(r"permission|access denied", fido.stderr, re.I) else None
    if devices:
        fido_access = any(run(("fido2-token", "-I", device)).ok for device in devices)
    readers = run(("opensc-tool", "--list-readers"))
    indices: list[str] = []
    if readers.ok:
        for line in readers.stdout.splitlines():
            match = re.match(r"\s*(\d+)\s+", line)
            if match:
                indices.append(match[1])
    piv_present: bool | None = False if readers.ok else None
    piv_access: bool | None = None
    for reader in indices:
        name = run(("opensc-tool", "--reader", reader, "--name"))
        if name.ok and re.search(r"\bpiv(?:-ii)?\b", name.stdout, re.I):
            piv_present, piv_access = True, True
        elif not name.ok and re.search(r"permission|access denied", name.stderr, re.I):
            piv_access = False
    return SecurityKeyState(active, fido_present, piv_present, openssh,
                            fido_access, piv_access, True, enrolled)
```

CLI subprocess adapter:

```python
def _security_key_probe(argv: tuple[str, ...]) -> CommandResult:
    import subprocess
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(argv=argv, returncode=127, stdout="", stderr=str(exc))
    return CommandResult(argv=argv, returncode=result.returncode,
                         stdout=result.stdout[:65536], stderr=result.stderr[:65536])
```

Use a bounded-output Popen adapter if any command can exceed 64 KiB; truncating after capture does not bound allocation. All these diagnostic commands have short outputs, but test a flooded fake command and stop it after the cap rather than asserting the post-capture slice is a memory bound. The callback is replaced in unit tests, including `systemctl`, so conftest's real-machine protections remain intact. The new subprocess adapter must not bypass those guards in existing doctor CLI tests. Add a session autouse fixture that patches `cli.main._security_key_probe` to raise `MachineQueried` unless the particular test replaces it, and update each existing doctor CLI fixture to provide explicit fake results. Keep `_no_machine_queries` unchanged. New probe tests call probe_security_keys with a fake `run`; they never use the CLI's real adapter.

```python
@pytest.fixture(autouse=True)
def _no_host_security_key_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    cli = importlib.import_module("hammunition.cli.main")
    def blocked(argv: tuple[str, ...]) -> CommandResult:
        raise MachineQueried("security-key doctor probe needs an injected result: " + argv[0])
    monkeypatch.setattr(cli, "_security_key_probe", blocked)
```

Use function scope (as shown), because pytest's monkeypatch fixture is function-scoped. Tests patch it after the guard fixture runs. Include `tests/conftest.py` and `tests/test_json_doctor.py` in the Task 18 commit.
 A permission-denied discovery produces a warn detail naming access, not a misleading “no token” success.

Keep IO out of doctor.py. Probe argv: `systemctl is-active pcscd`; `fido2-token -L`; `ssh -V` (parse stdout+stderr with `OpenSSH_(\d+)\.(\d+)`); `opensc-tool --list-readers`, then `opensc-tool --reader N --name` for each numbered reader (only a PIV driver/name counts). For FIDO2 access, `fido2-token -I <listed device>` under the ordinary user; for PIV access, the successful card-name probe under that user. Do not claim `os.access` run as root proves udev access. A failed command or missing program returns None and a helpful check, not a crash. Add a 5-second subprocess timeout and output bound in the IO adapter; commands run as the user invoking doctor. If euid 0, suppress access probes and mark unmeasured; no root token operations. The CLI reads enrolled keys from owner-aware store and passes classify results; catch corrupt state as a warn check naming `mirror status`/enrol remedy. Doctor never refreshes trust or serial and never signs a test message.

- [ ] Run: `.venv/bin/pytest tests/test_security_keys_doctor.py tests/test_doctor.py -q`; expected PASS; JSON golden and docs generator check also pass. Bench-only claims (token passthrough and Ubuntu ykman firmware support) stay explicitly unmeasured.
- [ ] Commit:

```bash
git add src/hammunition/security_keys.py src/hammunition/doctor.py src/hammunition/cli/main.py src/hammunition/interface/doctor.py tests/conftest.py tests/test_json_doctor.py tests/test_security_keys_doctor.py tests/fixtures/json/doctor-ready.json tests/fixtures/json/doctor-warn.json
git commit -m "feat: report hardware signing readiness and enrolled key strength"
```

### Task 19: A13 — D-085, guide, troubleshooting and final verification

**Files:**
- Modify: `docs/DECISIONS.md:10711-end` (D-084 was last when read), `CLAUDE.md:235-250` (decisions row), `docs/guides/lan-mirror.md:1-173`, `docs/troubleshooting/install-failures.md`, `docs/troubleshooting/index.md`, `mkdocs.yml`, `docs/reference/bunker-catalogue.md` (all implemented codecs/routes).
- Create: `changelog.d/381-offline-bunker.added.md`, `tests/test_docs_bunker.py`.
- Regenerate: CLI, JSON, station settings, package/profile pages through their generators. Do not modify the binding contract or CHANGELOG.md.

**Interfaces:**
- Consumes: Tasks 1–18 public commands, warning text, trust matrix and implemented gaps.
- Produces: authoritative D-085, user-facing remedy text and one release fragment. Plan A must be merged **and released** before Plan B updates its pinned engine dependency.

- [ ] Write failing doc tests:

```python
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_d085_and_offline_remedies_are_documented() -> None:
    decisions = (ROOT / "docs/DECISIONS.md").read_text()
    guide = (ROOT / "docs/guides/lan-mirror.md").read_text()
    problems = (ROOT / "docs/troubleshooting/install-failures.md").read_text()
    assert "## D-085" in decisions
    assert "hardware keys are recommended, never required" in decisions
    for command in ("hammunition mirror enrol", "hammunition mirror status",
                    "hammunition mirror accept-older", "--offline", "--clear-mirror"):
        assert command in guide
    assert "apt and pip" in guide and "phase 2" in guide
    assert "not on Bunker" in problems and "30 days" in problems
    assert "PAM" in guide
    fragment = ROOT / "changelog.d/381-offline-bunker.added.md"
    assert "#381" in fragment.read_text() and "D-085" in fragment.read_text()
```

Add tests for reference nav, exact weak warning, group cleartext caveat, `file://`, no claim that every software install works offline, and no permanent `mirror-signers` recommendation. Existing `test_docs_mirror` asserting the old offline limitation must be updated to the new D-085 distinction; do not retain a test asserting now-false prose. Confirm D-085 still free immediately before implementation merge; if occupied, maintainer resolves the decision number and both plans reference the chosen number rather than silently overwriting a decision.

- [ ] Run: `.venv/bin/pytest tests/test_docs_bunker.py -q`; expected FAIL: `assert '## D-085' in decisions`.
- [ ] Append the spec's exact decision text and its implications:

```markdown
## D-085 — A signed Bunker catalogue permits planning with publishers unreachable

A Bunker's signed catalogue lets the engine plan and install with publishers unreachable;
a signature records what the Bunker saw and never upgrades trust; hardware
keys are recommended, never required.

Amends D-070: planning no longer requires publishers when a Bunker is enrolled.
Amends D-078: `share` decides what a group Bunker hands to whom.

The engine verifies the exact catalogue bytes with enrolled OpenSSH keys, remembers
the highest accepted serial, refuses rollback until `hammunition mirror accept-older`
is explicitly confirmed, and warns on catalogues older than 30 days. Repository
pins remain authoritative; publisher digests are the Bunker's recorded observations;
digest-less data retains its existing disclosure and consent. RSA of 2048 bits or
fewer is accepted with the shared weak-key warning. Hardware-only verification is an
operator policy, off by default. Non-sk hardware origin requires operator affirmation.

Installing `security-keys` does not enable PAM. Enabling token login or sudo requires
a separate reviewed step with typed consent, dry-run and a working fallback login
check before anything is written; that step is outside phase 1.
```

CLAUDE table row:

```markdown
| Offline Bunker catalogue | Enrolled OpenSSH signers verify v3 catalogue bytes; offline and exhausted-retry planning use repository pins or recorded publisher metadata; serial rollback refused, age over 30 days warned, hardware policy off by default, weak RSA warned; apt/pip offline remains phase 2 | A populated Bunker must help with publishers unreachable, and its signature records what it saw without upgrading trust (**D-085**, amends **D-070**, **D-078**) |
```

Replace the old “A mirror does not make an install work offline” paragraph with:

```markdown
An **enrolled Bunker** lets the engine plan data and pinned-payload downloads with
publishers unreachable. Run `hammunition mirror enrol URL`, check the displayed
algorithm, size and fingerprint against the Bunker's key, and type that fingerprint.
`hammunition install ... --offline` uses only repository pins, local records and the
verified catalogue. With no flag, an exhausted publisher probe falls back to it and
each affected line says which Bunker and key recorded the metadata and when.

Setting `station set --mirror URL` alone still gives the D-070 download route; it
does not enrol keys or authorize using the mirror's metadata. `hammunition mirror
status --json` reports the accepted serial, age and keys. `--clear-mirror` removes
the URL and enrolment state. A lower serial refuses with `hammunition mirror
accept-older` as the explicit backup-restoration remedy. A catalogue older than
30 days is used with a warning.

Phase 1 covers data and pinned payloads. Apt and pip offline support belongs to
phase 2; git builds may still need archive dependencies, and Node applications may
need npm packages. Offline planning reports those gaps before execution. Installing
the `security-keys` profile never enables PAM.
```

Update the rest of the guide: source/binary/venv/Node/tools/extras now mirror; topo/3DEP/FSTopo checks and bundle gitlink rules; offline mirror failure refuses instead of trying publishers; `file://` trusted keys and export layout; every warning; hardware-origin affirmation; all group requests carry id and filtering is **not access control** over cleartext HTTP. Keep “LAN-only documented, not enforced” and ACMA/RepeaterBook policy accurately stated. Avoid quoting private status output. Reference/user examples use Monaco or Delaware.

Troubleshooting entries (with anchors linked from index): not enrolled; unsupported version; bad/missing signature; changed URL; lower serial after backup; stale catalogue; missing entry/group sharing filter; weak RSA; hardware-only refusal; token access denied; apt/pip/npm offline gap; moved tag or missing submodule. Give the actual commands above; no advice to disable verification, no unconditional `accept-older` recommendation.

Fragment:

```markdown
- #381 (D-085): Enrol signed Bunker catalogues, resolve data with publishers unreachable, fetch pinned payloads and git bundles from the Bunker, and check hardware signing readiness without enabling PAM.
```

- [ ] Regenerate all affected documentation, then run their compare-only checks:

```bash
python3 scripts/gen_json_reference.py
python3 scripts/gen_station_settings.py
python3 scripts/gen_package_reference.py
python3 scripts/gen_profile_reference.py
python3 scripts/check_doc_links.py
python3 scripts/gen_json_reference.py --check
python3 scripts/gen_station_settings.py --check
python3 scripts/gen_package_reference.py --check
python3 scripts/gen_profile_reference.py --check
.venv/bin/pytest tests/test_docs_bunker.py tests/test_docs_mirror.py tests/test_docs_json_interface.py tests/test_docs_generated.py tests/test_site.py -q
make check
```

Expected PASS, exit 0. Run existing target/container checks on their declared targets when implementing (no installs in this plan-writing turn). Do not add the network namespace/container integration test here; Plan B owns it and the GYST reusable network isolation prerequisite. Final acceptance requires offline unit coverage for every family, bad signature/rollback tests with real keys, bundle/recursive pin tests, and no bypass of `tests/conftest.py` isolation.

- [ ] Commit:

```bash
git add docs/DECISIONS.md CLAUDE.md docs/guides/lan-mirror.md docs/troubleshooting/install-failures.md docs/troubleshooting/index.md docs/reference/bunker-catalogue.md docs/reference/cli.md docs/reference/json-interface.md docs/guides/station-settings.md docs/packages docs/profiles mkdocs.yml tests/test_docs_bunker.py tests/test_docs_mirror.py changelog.d/381-offline-bunker.added.md
git commit -m "docs: record D-085 and explain offline Bunker trust and gaps"
```

## Spec coverage

| Spec section / phase-1 requirement | Task |
|---|---|
| Why: plan-time publisher dependency | 5–11 |
| Why: uncovered runtime routes | 12–15 |
| Why: engine reads catalogue | 1–4 |
| Rulings: shared reader, release order, configurable fleet | 1–4, 16, 19; Bunker writer in Plan B |
| Catalogue v3 fields and flat publisher metadata | 1 |
| Inputs: outline and four selections | 7, 16 |
| Freshness, equal serial, rollback, 30 days | 2–5, 19 |
| Signing namespace, principal and exact bytes | 1–4 |
| Key algorithms, rank order, RSA warning | 1–3, 5, 18 |
| Hardware by type, affirmed PIV, optional hardware-only | 2–3, 18 |
| Hybrid any-valid signature; no_touch_required shown, never written to allowed-signers | 2–3 |
| Enrol URL/id, status JSON, clear, accept-older | 3–4 |
| Group header, all entries listed, client sharing filter | 1, 4, 10, 19 |
| HTTP and file transport, immutable per-run verification | 4–5 |
| install/update offline and no-enrolment remedy | 5 |
| Exhausted-retry automatic fallback and exact plan wording | 5–11 |
| D-039 whole-unit deferral/direct refusal | 5–11, 15 |
| Static pins/pinned payloads unchanged | 5, 12–14 |
| OSM latest/monthly recorded name/size/MD5, pinned HEAD skipped | 6 |
| Copernicus pinned and unpinned tiles | 8 |
| US Topo/3DEP repository ETag, no live agreement | 9, 12 |
| FSTopo pins/unverified policy, ACMA/snapshot trust unchanged | 10, 12, 16 |
| Kiwix sha256 and CoMaps SHA-1/size | 11 |
| Source/binary/venv/Node/converter/git-extra mirror fetch | 13–14 |
| Bundles, checked HEAD, recursive gitlinks, moved tag refusal | 15–16 |
| artifacts JSON payloads/sheets/inputs/git pins | 16 |
| security-keys profile, apt-only, all doc fields | 17 |
| doctor: pcscd, FIDO2/PIV, OpenSSH 8.2, user access, key strength | 18 |
| D-085, CLAUDE row, guide/troubleshooting/changelog | 19 |
| D-059 documents and generated references | 3, 5, 16, 18–19 |
| Engine tests, real ssh-keygen keys, no hardware token | 1–18 |
| Bunker writer/signing/key setup/group server | Plan B, not this plan |
| Network-isolated integration/GYST prerequisite | Plan B, not this plan |
| PAM enabling | Separate later step/spec, excluded |
| apt/pip offline | Phase 2, excluded |
| Export/drive writer | Phase 3, excluded; reader implemented in 4 |

## Self-review

The binding contract wins over the spec's permanent `mirror-signers` path and nested
`publisher` illustration: this plan uses `mirror.json`, temporary allowed-signers and
flat fields. No CLI-reference generator exists; `cli.md` is edited by hand (lead's correction: a new generator is out of scope). The sibling Bunker v2 Entry types were inspected to preserve nullable failed entries. Current manifests do
not carry recursive submodule pins or complete payload size/licence metadata; Task 16
reports those limits rather than inventing values, and Task 15 derives gitlinks from
the repository-pinned parent. The contract does not specify selection payload codecs
or a no-touch metadata field: Task 7 exposes existing engine record codecs for Plan B,
and Task 3 obtains no-touch permission locally after verifying a signature with that
option. These are explicit handoff details, not edits to the binding JSON contract.

Not grounded by bench measurements: real hardware signing, PIV/FIDO2 passthrough in a
Bunker container, Immurok algorithms, Ubuntu ykman 5.2.1 with firmware 5.7, and catalogue
scale/performance. No such success is claimed. Unit test snippets are planned tests,
not tests run during writing. Package versions are the spec's supplied measurement;
this plan-writing task does not perform a live archive sweep. Network isolation
integration remains Plan B's acceptance gate. The implementation must retain strict
types, existing station isolation, and document any newly discovered gap by name.

Plan-writing verification: all Python code blocks were syntax-compiled without executing them; task headings, placeholder scan and referenced existing filenames were checked. This does not establish that the proposed tests pass before implementation. Git status showed only this new plan file; no commit, install or other file change was made.
