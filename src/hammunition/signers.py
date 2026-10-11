# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

import fcntl
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict, TypeAdapter, ValidationError

from hammunition.catalogue import Catalogue, parse, utc, valid_enrolment_id
from hammunition.keystrength import KeyStrength, classify
from hammunition.paths import OperatorDirError, open_operator_dir, owner_aware_dir
from hammunition.station import Station


class SignerError(ValueError):
    pass


@dataclass(frozen=True)
class EnrolledKey:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(
        strict=True, extra="forbid", revalidate_instances="always"
    )
    id: str
    public_key: str
    algorithm: str
    bits: int
    hardware: bool
    no_touch_required: bool = False


@dataclass(frozen=True)
class MirrorState:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(
        strict=True, extra="forbid", revalidate_instances="always"
    )
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


def verify(
    raw: bytes,
    signatures: Mapping[str, bytes],
    state: MirrorState,
    *,
    require_hardware: bool = False,
    now: datetime,
) -> VerifiedCatalogue:
    state = validate_state(state)
    cat = parse(raw)
    if cat.bunker.name != state.name:
        raise SignerError(
            "bunker.name: differs from the enrolled Bunker; enrol this URL explicitly"
        )
    if cat.serial < state.accepted_serial:
        raise SignerError(
            f"serial {cat.serial} is older than accepted serial {state.accepted_serial}; restore confirmed? hammunition mirror accept-older"
        )
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
            rendered = allowed_signers(replace(state, keys=(key,)))
            try:
                allowed.write_text(rendered, encoding="utf-8")
            except OSError as exc:
                raise SignerError(f"write_text {allowed}: {exc}") from exc
            try:
                allowed.chmod(0o600)
            except OSError as exc:
                raise SignerError(f"chmod {allowed}: {exc}") from exc
            try:
                sigpath.write_bytes(signature)
            except OSError as exc:
                raise SignerError(f"write_bytes {sigpath}: {exc}") from exc
            try:
                sigpath.chmod(0o600)
            except OSError as exc:
                raise SignerError(f"chmod {sigpath}: {exc}") from exc
            try:
                result = subprocess.run(
                    [
                        "ssh-keygen",
                        "-Y",
                        "verify",
                        "-f",
                        str(allowed),
                        "-I",
                        f"bunker:{state.name}",
                        "-n",
                        NAMESPACE,
                        "-s",
                        str(sigpath),
                    ],
                    input=raw,
                    capture_output=True,
                    check=False,
                    timeout=15,
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
    if require_hardware and not failures:
        raise SignerError("no enrolled hardware key signed this catalogue")
    policy = " with an enrolled hardware key" if require_hardware else ""
    details = ": " + "; ".join(failures) if failures else ""
    raise SignerError("no enrolled signature verified" + policy + details)


def mirror_path(owner: str | None = None) -> Path:
    return (
        owner_aware_dir(xdg_var="XDG_CONFIG_HOME", home_relative=(".config",), owner=owner)
        / "mirror.json"
    )


def validate_state(state: MirrorState) -> MirrorState:
    try:
        state = TypeAdapter(MirrorState).validate_python(state)
        Station(mirror=state.url)
    except ValueError as exc:
        raise SignerError(f"mirror: {exc}") from exc
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", state.name):
        raise SignerError("mirror.name is invalid")
    if state.mode not in ("personal", "group") or state.accepted_serial < 0 or not state.keys:
        raise SignerError("mirror mode/serial/keys are invalid")
    if state.enrolment_id is not None and not valid_enrolment_id(state.enrolment_id):
        raise SignerError("mirror.enrolment_id is invalid")
    if len({k.id for k in state.keys}) != len(state.keys):
        raise SignerError("mirror.keys contains duplicate ids")
    for key in state.keys:
        try:
            measured = classify(key.public_key)
        except ValueError as exc:
            raise SignerError(f"mirror.keys.public_key: {exc}") from exc
        if (key.id, key.algorithm, key.bits) != (
            measured.fingerprint,
            measured.algorithm,
            measured.bits,
        ):
            raise SignerError("mirror.keys metadata differs from public_key")
        if measured.hardware_by_type and not key.hardware:
            raise SignerError("mirror.keys sk key must be hardware")
        if key.no_touch_required and not measured.hardware_by_type:
            raise SignerError("mirror.keys.no_touch_required needs an sk key")
    if state.generated is not None:
        try:
            utc(state.generated, "mirror.generated")
        except ValueError as exc:
            raise SignerError(str(exc)) from exc
    return state


def store_uid(owner: str | None) -> tuple[int, int]:
    """The account mirror store files should belong to.

    Real, not effective, ids: hammunition is never setuid/setgid, so for this
    process the two are always equal in genuine use -- a sudo invocation sets
    both to 0 together -- and the real id is what a test fixture mocking
    ``os.geteuid`` for unrelated CLI-privilege branching does *not* also
    mock, so this bookkeeping stays correct instead of chasing a value that
    was never achievable on disk in the first place.
    """
    if owner is not None and os.getuid() == 0:
        with suppress(KeyError):
            entry = pwd.getpwnam(owner)
            return entry.pw_uid, entry.pw_gid
        # owner does not resolve (e.g. a stale $SUDO_USER); not elevated for
        # anyone in particular, matching paths.open_operator_dir's own fallback.
    return os.getuid(), os.getgid()


def open_store_file(directory: int, name: str, flags: int) -> int:
    try:
        return os.open(
            name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=directory
        )
    except (FileExistsError, FileNotFoundError):
        raise
    except OSError as exc:
        raise SignerError(f"{name}: {exc}") from exc


@contextmanager
def store_lock(path: Path, owner: str | None) -> Iterator[int]:
    try:
        kept = open_operator_dir(path.parent, owner)
    except (OSError, OperatorDirError) as exc:
        raise SignerError(f"mirror store: {exc}") from exc
    if kept is None:
        try:
            directory = os.open(
                path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
        except OSError as exc:
            raise SignerError(f"mirror store: {exc}") from exc
    else:
        directory = kept
    lock = -1
    try:
        created = False
        try:
            lock = open_store_file(directory, "mirror.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR)
            created = True
        except FileExistsError:
            lock = open_store_file(directory, "mirror.lock", os.O_RDWR)
        uid, gid = store_uid(owner)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise SignerError("mirror.lock must be a regular 0600 file")
        if created:
            try:
                os.fchown(lock, uid, gid)
            except PermissionError:
                pass  # not actually privileged enough to hand off; it is ours as created
            except OSError as exc:
                raise SignerError(f"mirror.lock: {exc}") from exc
        elif info.st_uid != uid:
            raise SignerError("mirror.lock belongs to another account")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError as exc:
            raise SignerError(f"mirror.lock: {exc}") from exc
        yield directory
    finally:
        if lock >= 0:
            os.close(lock)
        os.close(directory)


def read_state(directory: int, filename: str, owner: str | None) -> MirrorState | None:
    try:
        fd = open_store_file(directory, filename, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        uid, _ = store_uid(owner)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != uid
        ):
            raise SignerError("mirror.json must be operator-owned, regular and 0600")
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise SignerError("mirror.json exceeds 1 MiB")
    try:
        state = TypeAdapter(MirrorState).validate_json(raw)
    except ValidationError as exc:
        raise SignerError(f"mirror.json: {exc}") from exc
    return validate_state(state)


def load_mirror(path: Path | None = None, *, owner: str | None = None) -> MirrorState | None:
    target = path if path is not None else mirror_path(owner)
    if not target.parent.exists():
        return None
    with store_lock(target, owner) as directory:
        return read_state(directory, target.name, owner)


def save_mirror(
    state: MirrorState,
    path: Path | None = None,
    *,
    owner: str | None = None,
    allow_older: bool = False,
) -> Path:
    state = validate_state(state)
    target = path if path is not None else mirror_path(owner)
    with store_lock(target, owner) as directory:
        old = read_state(directory, target.name, owner)
        if (
            old is not None
            and (old.url, old.name) == (state.url, state.name)
            and state.accepted_serial < old.accepted_serial
            and not allow_older
        ):
            raise SignerError("mirror serial cannot be lowered; hammunition mirror accept-older")
        write_state(directory, target.name, state, owner)
    return target


def write_state(directory: int, filename: str, state: MirrorState, owner: str | None) -> None:
    state = validate_state(state)
    temporary = f".mirror-{secrets.token_hex(12)}"
    fd = open_store_file(directory, temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        # Unbuffered writes avoid a second flush during close masking a write error.
        with os.fdopen(fd, "wb", buffering=0) as stream:
            uid, gid = store_uid(owner)
            try:
                os.fchown(stream.fileno(), uid, gid)
            except PermissionError:
                pass  # not actually privileged enough to hand off; it is ours as created
            except OSError as exc:
                raise SignerError(f"fchown {temporary}: {exc}") from exc
            payload = (json.dumps(asdict(state), ensure_ascii=False) + "\n").encode()
            remaining = memoryview(payload)
            while remaining:
                try:
                    written = stream.write(remaining)
                except OSError as exc:
                    raise SignerError(f"write {temporary}: {exc}") from exc
                if written is None or written <= 0:
                    raise SignerError(f"write {temporary}: no progress")
                remaining = remaining[written:]
            try:
                stream.flush()
            except OSError as exc:
                raise SignerError(f"flush {temporary}: {exc}") from exc
            try:
                os.fsync(stream.fileno())
            except OSError as exc:
                raise SignerError(f"fsync {temporary}: {exc}") from exc
        try:
            os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
        except OSError as exc:
            raise SignerError(f"replace {temporary} -> {filename}: {exc}") from exc
        try:
            os.fsync(directory)
        except OSError as exc:
            raise SignerError(f"fsync directory containing {filename}: {exc}") from exc
    finally:
        # A secondary cleanup failure must never replace the operation's cause.
        with suppress(OSError):
            os.unlink(temporary, dir_fd=directory)


def advance_mirror(
    state: MirrorState, serial: int, generated: str, *, owner: str | None = None
) -> None:
    target = mirror_path(owner)
    with store_lock(target, owner) as directory:
        current = read_state(directory, target.name, owner)
        if current is None or (current.url, current.name, current.keys, current.enrolment_id) != (
            state.url,
            state.name,
            state.keys,
            state.enrolment_id,
        ):
            raise SignerError("mirror enrolment changed during verification; retry the command")
        if serial > current.accepted_serial or (
            serial == current.accepted_serial and current.generated is None
        ):
            updated = replace(current, accepted_serial=serial, generated=generated)
            write_state(directory, target.name, validate_state(updated), owner)


def clear_mirror(*, owner: str | None = None) -> None:
    target = mirror_path(owner)
    if not target.parent.exists():
        return
    try:
        with store_lock(target, owner) as directory:
            for name in (target.name, "mirror-signers"):
                with suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=directory)
            os.fsync(directory)
    except OSError as exc:
        raise SignerError(f"cannot clear {target}: {exc}") from exc
