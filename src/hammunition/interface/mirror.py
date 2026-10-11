# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from hammunition.interface.envelope import Strict, described
from hammunition.keystrength import classify
from hammunition.signers import MirrorState


@dataclass(frozen=True)
class MirrorKeyView(Strict):
    """One enrolled Bunker signer, with measured strength and hardware origin.

    The advertised no-touch metadata is informational only; it never changes
    signature verification options.
    """

    id: str = described("SHA256 fingerprint of the enrolled public key")
    algorithm: str = described("OpenSSH algorithm measured from the public key")
    bits: int = described("key size measured by ssh-keygen")
    hardware: bool = described("hardware by sk type or explicitly affirmed by the operator")
    no_touch_required: bool = described("advertised signature metadata; informational only")
    weak: bool = described("true for RSA of 2048 bits or fewer")
    warning: str | None = described("shared weak-key warning, null for a strong key")


@dataclass(frozen=True)
class MirrorDocument(Strict):
    """Stored Bunker trust and station signature policy, without a network request.

    The serial and catalogue age describe the last verified metadata. With no
    enrolled Bunker, its identity and catalogue fields are null and keys are empty;
    the station hardware policy still applies to future enrolment.
    """

    KIND: ClassVar[str] = "mirror"
    url: str | None = described("enrolled mirror URL; null when no Bunker is enrolled")
    name: str | None = described("enrolled Bunker name")
    mode: str | None = described(
        "personal or group mode as of enrolment -- a later verified catalogue can "
        "change its own mode without this field being refreshed to match (an Opus "
        "adversarial review of the whole branch, #381)"
    )
    enrolment_id: str | None = described("opaque group sharing id; not proof of laptop identity")
    accepted_serial: int | None = described(
        "highest accepted serial, except explicit backup restoration"
    )
    generated: str | None = described("UTC generation time from the last verified catalogue")
    age_days: int | None = described("age of last verified metadata, without contacting the Bunker")
    require_hardware: bool = described("station hardware-signature policy, off by default")
    keys: tuple[MirrorKeyView, ...] = described("enrolled key strength and hardware assertions")
    warnings: tuple[str, ...] = described("weak-key and catalogue-age warnings")


def build_mirror(
    state: MirrorState | None, *, require_hardware: bool, now: datetime
) -> MirrorDocument:
    if state is None:
        return MirrorDocument(None, None, None, None, None, None, None, require_hardware, (), ())
    keys: list[MirrorKeyView] = []
    warnings: list[str] = []
    for key in state.keys:
        strength = classify(key.public_key)
        keys.append(
            MirrorKeyView(
                key.id,
                strength.algorithm,
                strength.bits,
                key.hardware,
                key.no_touch_required,
                strength.weak,
                strength.warning,
            )
        )
        if strength.warning:
            warnings.append(strength.warning)
    age: int | None = None
    if state.generated is not None:
        generated = datetime.fromisoformat(state.generated.replace("Z", "+00:00"))
        age = max(0, int((now.astimezone(UTC) - generated).total_seconds() // 86400))
        if now.astimezone(UTC) - generated > timedelta(days=30):
            warnings.append("Bunker catalogue is older than 30 days")
    return MirrorDocument(
        state.url,
        state.name,
        state.mode,
        state.enrolment_id,
        state.accepted_serial,
        state.generated,
        age,
        require_hardware,
        tuple(keys),
        tuple(warnings),
    )


def render_mirror(doc: MirrorDocument) -> list[str]:
    if doc.url is None:
        return ["No Bunker enrolled; hammunition mirror enrol URL"]
    lines = [
        f"Bunker {doc.name}: {doc.url} ({doc.mode})",
        f"Accepted serial: {doc.accepted_serial}; catalogue age: {doc.age_days} days",
        f"Hardware key required: {doc.require_hardware}",
    ]
    for key in doc.keys:
        lines.append(
            f"{key.id}: {key.algorithm}, {key.bits} bits, {'hardware' if key.hardware else 'file key'}, no_touch_required={key.no_touch_required}"
        )
        if key.warning:
            lines.append(key.warning)
    lines += [warning for warning in doc.warnings if warning not in lines]
    return lines
