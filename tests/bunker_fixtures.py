# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

# tests/bunker_fixtures.py
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition.keystrength import classify
from hammunition.signers import EnrolledKey, MirrorState, verify

if TYPE_CHECKING:
    from hammunition.resolution import ResolutionContext


def key(tmp_path: Path, algorithm: str = "ed25519", bits: int | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"key-{algorithm}-{bits or 0}"
    if path.exists() or path.with_suffix(".pub").exists():
        raise FileExistsError(f"test key already exists: {path}")
    argv = ["ssh-keygen", "-q", "-t", algorithm, "-N", "", "-f", str(path)]
    if bits is not None:
        argv += ["-b", str(bits)]
    subprocess.run(argv, check=True, capture_output=True)
    return path


def document(public_key: str, **changes: object) -> dict[str, object]:
    strength = classify(public_key)
    value: dict[str, object] = {
        "kind": "bunker-index",
        "version": 3,
        "serial": 42,
        "generated": "2026-10-07T12:00:00Z",
        "bunker": {"name": "bunker", "mode": "personal"},
        "signers": [
            {
                "id": strength.fingerprint,
                "public_key": public_key,
                "algorithm": strength.algorithm,
                "bits": strength.bits,
                "hardware": False,
                "no_touch_required": False,
                "signature": "catalogue.sig.d/1.sig",
            }
        ],
        "engine_version": "0.22.0",
        "artifacts": [],
        "inputs": [],
        "deferred": [],
        "declined": [],
        "last_run": None,
    }
    value.update(changes)
    return value


def encode(doc: dict[str, object]) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def artifact(unit: str, name: str, body: bytes, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "unit": unit,
        "name": name,
        "path": f"{unit}/{name}",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
        "publisher_check": "sha256",
        "publisher_digest": None,
        "publisher_url": "https://example.invalid/payload",
        "publisher_name": None,
        "publisher_size": None,
        "licence": "CC0-1.0",
        "fetched": "2026-10-06T03:00:00Z",
        "verified": "2026-10-07T03:00:00Z",
        "status": "current",
        "reason": None,
        "previous": None,
        "share": "all",
    }
    value.update(changes)
    return value


def signed(tmp_path: Path, private: Path, doc: dict[str, object]) -> tuple[bytes, dict[str, bytes]]:
    path = tmp_path / "catalogue.json"
    path.write_bytes(encode(doc))
    path.with_suffix(".json.sig").unlink(missing_ok=True)
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-n",
            "hammunition-bunker-catalogue",
            "-f",
            str(private),
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes(), {"catalogue.sig.d/1.sig": path.with_suffix(".json.sig").read_bytes()}


def make_context(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    offline: bool = True,
    inputs: list[dict[str, object]] | None = None,
    mode: str = "personal",
    enrolment_id: str | None = None,
) -> ResolutionContext:
    """A context over a really signed catalogue of *rows*, verified at 2026-10-07 UTC.

    Each call signs in its own child directory, so no call meets another's key
    files. The transport for input records is supplied by the test that needs it."""
    from hammunition.resolution import ResolutionContext

    tmp_path.mkdir(parents=True, exist_ok=True)
    child = Path(tempfile.mkdtemp(prefix="signing-", dir=tmp_path))
    private = key(child)
    public = private.with_suffix(".pub").read_text().strip()
    strength = classify(public)
    enrolled = EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False)
    state = MirrorState("http://bunker.invalid", "bunker", "personal", None, (enrolled,), 0)
    raw, sigs = signed(
        child,
        private,
        document(
            public,
            artifacts=rows,
            inputs=inputs or [],
            bunker={"name": "bunker", "mode": mode},
        ),
    )
    verified = verify(raw, sigs, state, now=datetime(2026, 10, 7, tzinfo=UTC))
    return ResolutionContext(offline=offline, verified=verified, enrolment_id=enrolment_id)
