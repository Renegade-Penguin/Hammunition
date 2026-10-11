# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

import errno
import json
import os
import pwd
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from bunker_fixtures import document, key, signed
from hammunition.catalogue import CatalogueError
from hammunition.keystrength import classify
from hammunition.signers import (
    EnrolledKey,
    MirrorState,
    SignerError,
    advance_mirror,
    allowed_signers,
    clear_mirror,
    load_mirror,
    mirror_path,
    save_mirror,
    validate_state,
    verify,
    write_state,
)

if TYPE_CHECKING:
    from _typeshed import ReadableBuffer


@pytest.mark.parametrize(
    "algorithm,bits", [("ed25519", None), ("ecdsa", 384), ("rsa", 4096), ("rsa", 2048)]
)
def test_real_signatures_and_policy(tmp_path: Path, algorithm: str, bits: int | None) -> None:
    private = key(tmp_path, algorithm, bits)
    public = private.with_suffix(".pub").read_text().strip()
    strength = classify(public)
    enrolled = EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False)
    state = MirrorState("http://bunker.invalid/", "bunker", "personal", None, (enrolled,), 41)
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
    current = load_mirror(stored)
    assert current is not None and current.accepted_serial == 43


def test_concurrent_verifications_preserve_highest_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


@pytest.fixture
def enrolled_state(tmp_path: Path) -> tuple[Path, MirrorState]:
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    measured = classify(public)
    enrolled = EnrolledKey(measured.fingerprint, public, measured.algorithm, measured.bits, False)
    return private, MirrorState(
        "http://bunker.invalid", "bunker", "personal", None, (enrolled,), 42
    )


def test_freshness_and_local_hardware(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState]
) -> None:
    private, state = enrolled_state
    doc = document(state.keys[0].public_key)
    rows = doc["signers"]
    assert isinstance(rows, list)
    rows[0]["hardware"] = True
    raw, sigs = signed(tmp_path, private, doc)
    boundary = datetime(2026, 11, 6, 12, tzinfo=UTC)
    assert verify(raw, sigs, state, now=boundary).warnings == ()
    assert verify(raw, sigs, state, now=boundary + timedelta(microseconds=1)).warnings
    with pytest.raises(SignerError, match="hardware"):
        verify(raw, sigs, state, now=boundary, require_hardware=True)
    affirmed = replace(state, keys=(replace(state.keys[0], hardware=True),))
    assert verify(raw, sigs, affirmed, now=boundary, require_hardware=True).key.hardware
    with pytest.raises(SignerError, match=r"bunker\.name"):
        verify(raw, sigs, replace(state, name="other"), now=boundary)
    with pytest.raises(SignerError, match="signature"):
        verify(raw, {}, state, now=boundary)


@pytest.mark.parametrize("first", [None, b"bad signature"])
def test_later_enrolled_signature_succeeds(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState], first: bytes | None
) -> None:
    _, state = enrolled_state
    other = key(tmp_path / "other")
    public = other.with_suffix(".pub").read_text().strip()
    measured = classify(public)
    enrolled = EnrolledKey(measured.fingerprint, public, measured.algorithm, measured.bits, False)
    doc = document(state.keys[0].public_key)
    rows = doc["signers"]
    assert isinstance(rows, list)
    extra = document(public)["signers"]
    assert isinstance(extra, list)
    extra[0]["signature"] = "catalogue.sig.d/2.sig"
    rows.extend(extra)
    raw, sigs = signed(tmp_path, other, doc)
    sigs["catalogue.sig.d/2.sig"] = sigs.pop("catalogue.sig.d/1.sig")
    if first is not None:
        sigs["catalogue.sig.d/1.sig"] = first
    now = datetime(2026, 10, 7, tzinfo=UTC)
    with pytest.raises(SignerError, match="signature"):
        verify(raw, sigs, state, now=now)
    assert verify(raw, sigs, replace(state, keys=(*state.keys, enrolled)), now=now).key == enrolled


@pytest.mark.parametrize("field", ["id", "public_key"])
def test_forged_signer_metadata(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState], field: str
) -> None:
    private, state = enrolled_state
    other = key(tmp_path / "other")
    doc = document(state.keys[0].public_key)
    rows = doc["signers"]
    assert isinstance(rows, list)
    rows[0][field] = (
        "SHA256:forged" if field == "id" else other.with_suffix(".pub").read_text().strip()
    )
    raw, sigs = signed(tmp_path, private, doc)
    with pytest.raises(CatalogueError, match="id"):
        verify(raw, sigs, state, now=datetime(2026, 10, 7, tzinfo=UTC))


@pytest.mark.parametrize("filename", ["mirror.json", "mirror.lock"])
def test_store_refuses_symlinks(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState], filename: str
) -> None:
    _, state = enrolled_state
    parent = tmp_path / "store"
    parent.mkdir()
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched")
    (parent / filename).symlink_to(victim)
    with pytest.raises(SignerError):
        save_mirror(state, parent / "mirror.json")
    assert victim.read_bytes() == b"untouched"


@pytest.mark.parametrize(
    "change",
    [
        {"accepted_serial": True},
        {"keys": []},
        {"name": "bad,name"},
        {"mode": "bad"},
        {"enrolment_id": "bad id"},
        {"generated": "bad"},
        {"extra": 1},
    ],
)
def test_corrupt_store_refused(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState], change: dict[str, object]
) -> None:
    _, state = enrolled_state
    path = tmp_path / "mirror.json"
    save_mirror(state, path)
    doc = json.loads(path.read_text())
    doc.update(change)
    path.write_text(json.dumps(doc))
    with pytest.raises(SignerError):
        load_mirror(path)


def test_no_touch_requires_sk(enrolled_state: tuple[Path, MirrorState]) -> None:
    _, state = enrolled_state
    with pytest.raises(SignerError, match="no_touch_required"):
        validate_state(replace(state, keys=(replace(state.keys[0], no_touch_required=True),)))
    assert "no-touch-required" not in allowed_signers(state)


def test_clear_and_advance_enrolment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enrolled_state: tuple[Path, MirrorState]
) -> None:
    _, state = enrolled_state
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert load_mirror() is None
    clear_mirror()
    path = save_mirror(state)
    lock = path.with_name("mirror.lock")
    inode = lock.stat().st_ino
    with pytest.raises(SignerError, match="enrolment changed"):
        advance_mirror(replace(state, enrolment_id="other"), 43, "2026-10-07T12:00:00Z")
    advance_mirror(state, 42, "2026-10-07T12:00:00Z")
    current = load_mirror()
    assert current is not None and current.generated == "2026-10-07T12:00:00Z"
    save_mirror(replace(state, accepted_serial=0), allow_older=True)
    clear_mirror()
    clear_mirror()
    assert load_mirror() is None
    assert lock.stat().st_ino == inode


def test_owner_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enrolled_state: tuple[Path, MirrorState]
) -> None:
    _, state = enrolled_state
    uid, gid = os.geteuid(), os.getegid()
    if uid == 0:
        # Already real root (a CI container): fall back to an arbitrary
        # non-root stand-in so owner_aware_dir's refusal of pw_uid == 0
        # (root is never a valid handoff target) does not also catch the
        # simulated operator. Real root's CAP_CHOWN lets fchown reassign to
        # it; a genuinely unprivileged run instead reuses its own uid so the
        # "handoff" fchown is a same-owner no-op requiring no privilege.
        uid, gid = 65534, 65534
    entry = pwd.struct_passwd(("operator", "x", uid, gid, "", str(tmp_path), "/bin/sh"))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: entry)
    store = tmp_path / "store"
    store.mkdir()
    os.chown(store, uid, gid)  # must already belong to the operator; see open_operator_dir
    real_replace = os.replace
    real_fchown = os.fchown
    handoffs: list[tuple[int, int, int]] = []
    observed: list[int] = []

    def record_fchown(fd: int, owner_uid: int, owner_gid: int) -> None:
        handoffs.append((os.fstat(fd).st_ino, owner_uid, owner_gid))
        real_fchown(fd, owner_uid, owner_gid)

    monkeypatch.setattr(os, "fchown", record_fchown)

    def check_replace(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
        observed.append(os.stat(src, dir_fd=src_dir_fd).st_uid)
        real_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(os, "replace", check_replace)
    path = save_mirror(state, tmp_path / "store" / "mirror.json", owner="operator")
    assert observed == [uid]
    assert handoffs == [
        (path.with_name("mirror.lock").stat().st_ino, entry.pw_uid, entry.pw_gid),
        (path.stat().st_ino, entry.pw_uid, entry.pw_gid),
    ]
    assert path.stat().st_uid == uid
    assert load_mirror(path, owner="operator") == state
    assert mirror_path("operator").is_relative_to(tmp_path)


def test_existing_lock_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enrolled_state: tuple[Path, MirrorState]
) -> None:
    _, state = enrolled_state
    path = tmp_path / "mirror.json"
    save_mirror(state, path)
    entry = pwd.struct_passwd(
        ("operator", "x", os.geteuid() + 1, os.getegid(), "", str(tmp_path), "/bin/sh")
    )
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: entry)
    with pytest.raises(SignerError, match="another account"):
        load_mirror(path, owner="operator")


@pytest.mark.parametrize(
    "field,value", [("accepted_serial", True), ("hardware", 1), ("no_touch_required", 0)]
)
def test_direct_state_types_refused(
    enrolled_state: tuple[Path, MirrorState], field: str, value: object
) -> None:
    _, state = enrolled_state
    first = state.keys[0]
    if field == "accepted_serial":
        invalid = replace(state, accepted_serial=cast(int, value))
    elif field == "hardware":
        invalid = replace(state, keys=(replace(first, hardware=cast(bool, value)),))
    else:
        invalid = replace(state, keys=(replace(first, no_touch_required=cast(bool, value)),))
    with pytest.raises(SignerError, match=field):
        validate_state(invalid)


@pytest.mark.parametrize("filename", ["mirror.json", "mirror.lock"])
def test_store_permissions_refused(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState], filename: str
) -> None:
    _, state = enrolled_state
    path = save_mirror(state, tmp_path / "store" / "mirror.json")
    path.with_name(filename).chmod(0o644)
    with pytest.raises(SignerError, match="0600"):
        load_mirror(path)


def test_store_key_metadata_refused(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState]
) -> None:
    _, state = enrolled_state
    path = save_mirror(state, tmp_path / "mirror.json")
    doc = json.loads(path.read_text())
    doc["keys"][0]["bits"] = 4096
    path.write_text(json.dumps(doc))
    with pytest.raises(SignerError, match="metadata"):
        load_mirror(path)


def test_sk_touch_metadata_roundtrip(
    tmp_path: Path, enrolled_state: tuple[Path, MirrorState]
) -> None:
    import base64
    import struct

    _, state = enrolled_state
    blob = base64.b64decode(state.keys[0].public_key.split()[1])
    length = struct.unpack(">I", blob[:4])[0]
    algorithm = "sk-ssh-ed25519@openssh.com"
    application = b"ssh:"
    wrapped = (
        struct.pack(">I", len(algorithm))
        + algorithm.encode()
        + blob[4 + length :]
        + struct.pack(">I", len(application))
        + application
    )
    public = algorithm + " " + base64.b64encode(wrapped).decode()
    measured = classify(public)
    enrolled = EnrolledKey(measured.fingerprint, public, algorithm, 256, True, True)
    state = replace(state, keys=(enrolled,))
    path = save_mirror(state, tmp_path / "mirror.json")
    assert load_mirror(path) == state
    assert (
        allowed_signers(state)
        == f'bunker:bunker namespaces="hammunition-bunker-catalogue" {public}\n'
    )
    with pytest.raises(SignerError, match="hardware"):
        validate_state(replace(state, keys=(replace(enrolled, hardware=False),)))


@pytest.mark.parametrize(
    "operation,filename",
    [
        ("write_text", "allowed-signers"),
        ("write_bytes", "catalogue.sig"),
        ("chmod", "allowed-signers"),
        ("chmod", "catalogue.sig"),
    ],
)
def test_verify_temporary_io_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enrolled_state: tuple[Path, MirrorState],
    operation: str,
    filename: str,
) -> None:
    private, state = enrolled_state
    raw, sigs = signed(tmp_path, private, document(state.keys[0].public_key))
    cause = OSError(errno.ENOSPC, "full temporary directory")

    original = getattr(Path, operation)

    def fail(path: Path, *args: object, **kwargs: object) -> object:
        if path.name == filename:
            raise cause
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(SignerError) as caught:
        verify(raw, sigs, state, now=datetime(2026, 10, 7, tzinfo=UTC))
    assert caught.value.__cause__ is cause
    assert operation in str(caught.value)
    assert filename in str(caught.value)


@pytest.mark.parametrize("operation", ["fchown", "fsync-file", "fsync-directory", "replace"])
def test_write_state_io_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enrolled_state: tuple[Path, MirrorState],
    operation: str,
) -> None:
    _, state = enrolled_state
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    cause = OSError(errno.ENOSPC, "full store")
    real_fsync = os.fsync

    def fail_fsync(fd: int) -> None:
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if is_directory == (operation == "fsync-directory"):
            raise cause
        real_fsync(fd)

    def fail_fchown(fd: int, uid: int, gid: int) -> None:
        raise cause

    def fail_replace(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
        raise cause

    if operation.startswith("fsync"):
        monkeypatch.setattr(os, "fsync", fail_fsync)
    elif operation == "fchown":
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(os, "fchown", fail_fchown)
    else:
        monkeypatch.setattr(os, "replace", fail_replace)
    try:
        with pytest.raises(SignerError) as caught:
            write_state(directory, "mirror.json", state, None)
        assert caught.value.__cause__ is cause
        assert operation.split("-")[0] in str(caught.value)
        assert (".mirror-" if operation in ("fchown", "fsync-file") else "mirror.json") in str(
            caught.value
        )
        assert not list(tmp_path.glob(".mirror-*"))
    finally:
        os.close(directory)


def test_cleanup_preserves_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enrolled_state: tuple[Path, MirrorState]
) -> None:
    _, state = enrolled_state
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original = OSError(errno.ENOSPC, "replace failed")
    secondary = OSError(errno.EACCES, "cleanup failed")

    def fail_replace(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
        raise original

    def fail_unlink(path: str, *, dir_fd: int) -> None:
        raise secondary

    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(os, "unlink", fail_unlink)
    try:
        with pytest.raises(SignerError) as caught:
            write_state(directory, "mirror.json", state, None)
        assert caught.value.__cause__ is original
        assert "replace" in str(caught.value)
    finally:
        os.close(directory)


def test_read_state_validation_message_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enrolled_state: tuple[Path, MirrorState]
) -> None:
    from hammunition import signers

    _, state = enrolled_state
    path = save_mirror(state, tmp_path / "mirror.json")
    error = SignerError("mirror.json: specific validation refusal")

    def refuse(value: MirrorState) -> MirrorState:
        raise error

    monkeypatch.setattr(signers, "validate_state", refuse)
    with pytest.raises(SignerError) as caught:
        load_mirror(path)
    assert caught.value is error
    assert str(caught.value) == "mirror.json: specific validation refusal"


def test_hardware_skips_message(tmp_path: Path, enrolled_state: tuple[Path, MirrorState]) -> None:
    private, state = enrolled_state
    raw, sigs = signed(tmp_path, private, document(state.keys[0].public_key))
    with pytest.raises(SignerError) as caught:
        verify(raw, sigs, state, require_hardware=True, now=datetime(2026, 10, 7, tzinfo=UTC))
    assert str(caught.value) == "no enrolled hardware key signed this catalogue"


@pytest.mark.parametrize("failure", ["missing", "timeout"])
def test_verify_openssh_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enrolled_state: tuple[Path, MirrorState],
    failure: str,
) -> None:
    private, state = enrolled_state
    raw, sigs = signed(tmp_path, private, document(state.keys[0].public_key))
    real_run = subprocess.run
    cause = (
        FileNotFoundError("ssh-keygen missing")
        if failure == "missing"
        else subprocess.TimeoutExpired("ssh-keygen", 15)
    )

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if args[:3] == ["ssh-keygen", "-Y", "verify"]:
            raise cause
        result: subprocess.CompletedProcess[bytes] = real_run(args, **kwargs)
        return result

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SignerError, match=r"needs working OpenSSH 8\.2\+") as caught:
        verify(raw, sigs, state, now=datetime(2026, 10, 7, tzinfo=UTC))
    assert caught.value.__cause__ is cause


@pytest.mark.parametrize("failure", ["short", "full"])
def test_state_unbuffered_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enrolled_state: tuple[Path, MirrorState],
    failure: str,
) -> None:
    import io

    _, state = enrolled_state
    cause = OSError(errno.ENOSPC, "write failed")

    class Writer(io.FileIO):
        def write(self, data: "ReadableBuffer", /) -> int:
            if failure == "full":
                raise cause
            return super().write(memoryview(data)[:7])

    def fdopen(fd: int, mode: str, *, buffering: int) -> Writer:
        assert buffering == 0
        return Writer(fd, mode)

    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "fdopen", fdopen)
            if failure == "full":
                with pytest.raises(SignerError, match="write") as caught:
                    write_state(directory, "mirror.json", state, None)
                assert caught.value.__cause__ is cause
                assert not (tmp_path / "mirror.json").exists()
            else:
                write_state(directory, "mirror.json", state, None)
        if failure == "short":
            assert load_mirror(tmp_path / "mirror.json") == state
    finally:
        os.close(directory)
