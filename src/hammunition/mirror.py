# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from hammunition.backends import BackendError
from hammunition.catalogue import Signer, parse
from hammunition.consent import (
    ConsentDeclined,
    ConsentRecord,
    ConsentUnavailable,
    resolve_mirror_consent,
)
from hammunition.keystrength import classify
from hammunition.mirror_transport import MirrorTransport, read_catalogue
from hammunition.signers import (
    EnrolledKey,
    MirrorState,
    SignerError,
    load_mirror,
    save_mirror,
    verify,
)
from hammunition.station import Station, load_station, save_station


@dataclass(frozen=True)
class Candidate:
    raw: bytes
    signatures: dict[str, bytes]


def _same_endpoint(a: str, b: str) -> bool:
    """Whether *a* and *b* name the same Bunker endpoint, ignoring a purely
    cosmetic difference: a trailing or repeated slash, host case, or an
    explicit default port. Used only to decide whether a rollback floor
    carries forward on re-enrolment -- never to decide trust, which rests
    on the catalogue's own signature regardless of which URL fetched it."""
    left, right = urlsplit(a), urlsplit(b)
    default_port = {"http": 80, "https": 443}.get(left.scheme.lower())
    return (
        left.scheme.lower() == right.scheme.lower()
        and (left.hostname or "").lower() == (right.hostname or "").lower()
        and (left.port or default_port) == (right.port or default_port)
        and left.path.rstrip("/") == right.path.rstrip("/")
    )


def display_signer(signer: Signer) -> str:
    strength = classify(signer.public_key)
    origin = (
        "hardware by type"
        if strength.hardware_by_type
        else "hardware claimed, needs affirmation"
        if signer.hardware
        else "file key"
    )
    line = f"{signer.id}: {strength.algorithm}, {strength.bits} bits, {origin}, no_touch_required={signer.no_touch_required}"
    return line + (f"; {strength.warning}" if strength.warning else "")


def enrol(
    candidate: Candidate,
    url: str,
    enrolment_id: str | None,
    *,
    choose: Callable[[str], str] | None,
    affirm_hardware: Callable[[str], bool] | None,
    owner: str | None,
    require_hardware: bool,
    now: datetime,
    record_consent: Callable[[ConsentRecord], None],
) -> MirrorState:
    normalized = Station(mirror=url).mirror
    if normalized is None:
        raise SignerError("mirror URL is missing")
    parsed = parse(candidate.raw)
    ordered = sorted(
        parsed.signers, key=lambda signer: (classify(signer.public_key).rank, signer.id)
    )
    disclosure = "\n".join(display_signer(signer) for signer in ordered)
    disclosure += "\nA signature means this Bunker recorded this; it does not upgrade trust."
    if choose is None:
        raise ConsentUnavailable(
            "Bunker enrolment requires typing fingerprints at a terminal; --yes cannot answer it"
        )
    answer = choose(disclosure + "\nType one or more fingerprints, comma-separated: ").strip()
    ids = tuple(dict.fromkeys(part.strip() for part in answer.split(",") if part.strip()))
    advertised = {signer.id: signer for signer in ordered}
    if not ids or any(identity not in advertised for identity in ids):
        raise ConsentDeclined("Bunker signer fingerprint was not affirmed")
    keys: list[EnrolledKey] = []
    for identity in ids:
        signer = advertised[identity]
        consent = resolve_mirror_consent(
            identity, display_signer(signer), prompt=choose, actor=owner, now=lambda: now
        )
        record_consent(consent)
        strength = classify(signer.public_key)
        hardware = strength.hardware_by_type
        if not hardware and signer.hardware:
            disclosure = "The Bunker claims this key is hardware-backed; the algorithm cannot prove that. Affirm only if you verified its hardware origin."
            # No callback to ask is "could not ask", not "yes" -- the inverted
            # form (`affirm_hardware is not None and not affirm_hardware(...)`)
            # read a missing callback as affirmation, enrolling a plain
            # software key as hardware-verified (found by an Opus adversarial
            # review of the whole branch, #381). Unreachable from the shipped
            # CLI today (cmd_mirror_enrol sets both from the same `interactive`
            # flag), but the inversion is the kind of latent trust default a
            # future caller could reintroduce by supplying one without the
            # other.
            if affirm_hardware is None or not affirm_hardware(disclosure + " Type yes: "):
                hardware = False
            else:
                hardware_record = resolve_mirror_consent(
                    identity, disclosure, prompt=choose, actor=owner, now=lambda: now
                )
                record_consent(hardware_record)
                hardware = True
        keys.append(
            EnrolledKey(
                identity,
                signer.public_key,
                strength.algorithm,
                strength.bits,
                hardware,
                signer.no_touch_required,
            )
        )
    old = load_mirror(owner=owner)
    # Carried forward when *old* names the same endpoint, compared with a
    # cosmetic normalisation (trailing/repeated slashes, host case, an
    # explicit default port) rather than exact string equality: a
    # respelling of the same Bunker URL must not reset the rollback floor
    # to 0 -- or re-enrolling with no real change but spelling would
    # silently accept an older, already-superseded catalogue, and the
    # engine's own "station mirror differs from enrolled mirror; run
    # hammunition mirror enrol URL" message is exactly what would walk an
    # operator into triggering it (found by an Opus adversarial review of
    # the whole branch, #381). A genuinely different host (a real Bunker
    # move, or an unrelated one that happens to share a name) is still
    # treated as fresh, matching the existing, deliberate "a changed URL is
    # a new Bunker" rule -- this only closes the cosmetic-respelling gap in
    # it, not the rule itself.
    serial = (
        old.accepted_serial
        if old is not None
        and old.name == parsed.bunker.name
        and _same_endpoint(old.url, normalized)
        else 0
    )
    state = MirrorState(
        normalized, parsed.bunker.name, parsed.bunker.mode, enrolment_id, tuple(keys), serial
    )
    verified = verify(
        candidate.raw, candidate.signatures, state, require_hardware=require_hardware, now=now
    )
    state = replace(
        state, accepted_serial=verified.catalogue.serial, generated=verified.catalogue.generated
    )
    current = load_station(owner=owner)
    save_mirror(state, owner=owner)
    save_station(replace(current, mirror=normalized), owner=owner)
    return state


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


def consistent_state(url: str | None, state: MirrorState | None) -> None:
    if state is not None and url != state.url:
        raise SignerError(
            "station mirror differs from enrolled mirror; run hammunition mirror enrol URL"
        )


def fetch_transport(station_url: str | None, state: MirrorState | None) -> MirrorTransport | None:
    """The transport for artifact downloads from the station's mirror, or ``None``.

    The enrolled Bunker (station mirror equal to the enrolled URL) gets its
    enrolment header. A ``file://`` mirror that is not enrolled still gets a
    header-less Bunker transport, the only one that can read a file export:
    a mirror is untrusted transport and artifacts stay pinned by the engine's
    own hashes. Any other mirror stays on the publisher transport, which
    never sends the header."""
    if station_url is None:
        return None
    if state is not None and station_url == state.url:
        return MirrorTransport(state.url, state.enrolment_id)
    if urlsplit(station_url).scheme == "file":
        return MirrorTransport(station_url, None)
    return None
