# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

import importlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from bunker_fixtures import document, key, signed
from hammunition import mirror, signers
from hammunition.consent import ConsentRecord
from hammunition.keystrength import classify
from hammunition.signers import MirrorState
from hammunition.station import load_station

cli = importlib.import_module("hammunition.cli.main")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(cli, "operator", lambda args: "")


def test_enrol_requires_fingerprint_and_clear_removes_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    private = key(tmp_path, "rsa", 2048)
    public = private.with_suffix(".pub").read_text().strip()
    raw, sigs = signed(tmp_path, private, document(public))
    candidate = mirror.Candidate(raw, sigs)
    monkeypatch.setattr(mirror, "read_candidate", lambda *a: candidate)
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    fingerprint = classify(public).fingerprint
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    assert cli.main(["mirror", "enrol", "http://bunker.invalid/"]) != 0
    assert signers.load_mirror() is None
    monkeypatch.setattr("builtins.input", lambda *a: fingerprint)
    assert cli.main(["mirror", "enrol", "http://bunker.invalid/"]) == 0
    assert load_station().mirror == "http://bunker.invalid/"
    current = signers.load_mirror()
    assert current is not None and current.accepted_serial == 42
    assert cli.main(["station", "set", "--clear-mirror"]) == 0
    assert signers.load_mirror() is None


def test_status_json_is_one_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    assert cli.main(["mirror", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "hammunition/1"
    assert result["kind"] == "mirror" and result["url"] is None


def test_second_fingerprint_prompt_is_real(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from hammunition.consent import ConsentDeclined, ConsentRecord

    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public))
    identity = classify(public).fingerprint
    responses = iter((identity, "wrong"))
    records: list[ConsentRecord] = []
    with pytest.raises(ConsentDeclined):
        mirror.enrol(
            mirror.Candidate(raw, signatures),
            "http://bunker.invalid/",
            None,
            choose=lambda text: next(responses),
            affirm_hardware=None,
            owner=None,
            require_hardware=False,
            now=datetime(2026, 10, 7, tzinfo=UTC),
            record_consent=records.append,
        )
    assert records == []


def candidate(tmp_path: Path, *, serial: int = 42, hardware: bool = False) -> mirror.Candidate:
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    doc = document(public, serial=serial)
    rows = doc["signers"]
    assert isinstance(rows, list)
    rows[0]["hardware"] = hardware
    raw, signatures = signed(tmp_path, private, doc)
    return mirror.Candidate(raw, signatures)


def enroll(
    value: mirror.Candidate,
    records: list[ConsentRecord],
    choose: Callable[[str], str] | None = None,
    affirm: Callable[[str], bool] | None = None,
) -> MirrorState:
    from datetime import UTC, datetime

    identity = classify(
        __import__("hammunition.catalogue", fromlist=["parse"])
        .parse(value.raw)
        .signers[0]
        .public_key
    ).fingerprint
    return mirror.enrol(
        value,
        "http://bunker.invalid/",
        None,
        choose=choose or (lambda text: identity),
        affirm_hardware=affirm,
        owner=None,
        require_hardware=False,
        now=datetime(2026, 10, 7, 13, tzinfo=UTC),
        record_consent=records.append,
    )


def test_store_failure_does_not_change_station(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hammunition.station import Station, save_station

    save_station(Station(mirror="http://previous.invalid/"))
    value = candidate(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise signers.SignerError("cannot save mirror.json")

    monkeypatch.setattr(mirror, "save_mirror", fail)
    with pytest.raises(signers.SignerError):
        enroll(value, [])
    assert load_station().mirror == "http://previous.invalid/"


def test_hardware_consent_is_real_and_logged(tmp_path: Path) -> None:
    from hammunition.catalogue import parse
    from hammunition.consent import ConsentDeclined

    value = candidate(tmp_path, hardware=True)
    identity = parse(value.raw).signers[0].id
    records: list[ConsentRecord] = []
    # Reached through a genuine affirm_hardware=True (real affirmation that
    # it is hardware), not its absence: the stronger hardware-consent step
    # below is what this test declines, and only a real "yes" to the first
    # question reaches it -- an absent callback must never stand in for one
    # (an Opus adversarial review of the whole branch, #381 -- see
    # test_no_affirm_hardware_callback_is_not_affirmation).
    responses = iter((identity, identity, "wrong"))
    with pytest.raises(ConsentDeclined):
        enroll(value, records, choose=lambda text: next(responses), affirm=lambda text: True)
    assert signers.load_mirror() is None
    assert len(records) == 1
    records.clear()
    state = enroll(value, records, affirm=lambda text: True)
    assert state.keys[0].hardware
    assert len(records) == 2
    assert all(record.extra["key_fingerprint"] == identity for record in records)
    assert records[0].disclosure_sha256 != records[1].disclosure_sha256


def test_no_affirm_hardware_callback_is_not_affirmation(tmp_path: Path) -> None:
    """A missing ``affirm_hardware`` must read as "could not ask", never as
    "yes" -- the inverted form enrolled a plain software key the Bunker
    merely claimed was hardware as hardware-verified with no real
    confirmation (an Opus adversarial review of the whole branch, #381)."""
    value = candidate(tmp_path, hardware=True)
    records: list[ConsentRecord] = []
    state = enroll(value, records)
    assert state.keys[0].hardware is False


def test_bad_signature_writes_no_state(tmp_path: Path) -> None:
    value = candidate(tmp_path)
    with pytest.raises(signers.SignerError):
        enroll(mirror.Candidate(value.raw, {}), [])
    assert signers.load_mirror() is None
    assert load_station().mirror is None


def test_clear_oserror_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    enroll(candidate(tmp_path), [])

    def fail(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("hammunition.signers.os.unlink", fail)
    with pytest.raises(signers.SignerError) as caught:
        signers.clear_mirror()
    assert isinstance(caught.value.__cause__, PermissionError)
    assert str(signers.mirror_path()) in str(caught.value)
    assert cli.main(["station", "set", "--clear-mirror"]) == 2
    assert "mirror.json" in capsys.readouterr().err


def test_policy_round_trip_clear_and_strict_bool(tmp_path: Path) -> None:
    from hammunition.station import StationError, config_path

    assert cli.main(["station", "set", "--mirror-require-hardware-key"]) == 0
    assert load_station().mirror_require_hardware_key is True
    assert cli.main(["station", "set", "--clear-mirror"]) == 0
    assert load_station().mirror_require_hardware_key is True
    config_path().write_text('mirror_require_hardware_key: "false"\n')
    with pytest.raises(StationError):
        load_station()


def test_mutations_refuse_json_and_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for command in (["mirror", "enrol", "http://bunker.invalid/"], ["mirror", "accept-older"]):
        assert cli.main([*command, "--json"]) != 0
        assert json.loads(capsys.readouterr().out)["kind"] == "error"
        with pytest.raises(SystemExit):
            cli.main([*command, "--yes"])


def test_noninteractive_enrol_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = candidate(tmp_path)
    monkeypatch.setattr(mirror, "read_candidate", lambda *args: value)
    monkeypatch.setattr(cli, "is_interactive", lambda: False)
    assert cli.main(["mirror", "enrol", "http://bunker.invalid/"]) == 2
    assert signers.load_mirror() is None


def test_status_refuses_url_mismatch(tmp_path: Path) -> None:
    from hammunition.station import Station, save_station

    enroll(candidate(tmp_path), [])
    save_station(Station(mirror="http://changed.invalid/"))
    assert cli.main(["mirror", "status"]) == 2


def test_clear_fsync_failure_is_chained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enroll(candidate(tmp_path), [])
    error = OSError("fsync failed")
    monkeypatch.setattr("hammunition.signers.os.fsync", lambda *args: (_ for _ in ()).throw(error))
    with pytest.raises(signers.SignerError) as caught:
        signers.clear_mirror()
    assert caught.value.__cause__ is error


def test_declined_hardware_is_file_key(tmp_path: Path) -> None:
    state = enroll(candidate(tmp_path, hardware=True), [], affirm=lambda text: False)
    assert not state.keys[0].hardware


def test_lower_serial_requires_verified_backup_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hammunition.catalogue import parse

    original = candidate(tmp_path)
    state = enroll(original, [])
    private = next(tmp_path.glob("key-ed25519-0"))
    doc = document(parse(original.raw).signers[0].public_key, serial=12)
    raw, signatures = signed(tmp_path, private, doc)
    older = mirror.Candidate(raw, signatures)
    with pytest.raises(signers.SignerError, match="serial"):
        enroll(older, [])
    kept = signers.load_mirror()
    assert kept is not None and kept.accepted_serial == state.accepted_serial
    monkeypatch.setattr(mirror, "read_candidate", lambda *args: older)
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    prompts: list[str] = []

    def answer(text: str) -> str:
        prompts.append(text)
        return "yes"

    monkeypatch.setattr("builtins.input", answer)
    assert cli.main(["mirror", "accept-older"]) == 0
    assert "42" in prompts[0] and "12" in prompts[0] and "trusted backup" in prompts[0]
    accepted = signers.load_mirror()
    assert accepted is not None and accepted.accepted_serial == 12
    monkeypatch.setattr(mirror, "read_candidate", lambda *args: mirror.Candidate(raw, {}))
    prompts.clear()
    assert cli.main(["mirror", "accept-older"]) == 2
    assert prompts == []


def test_cli_logs_fingerprint_timestamp_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hammunition.catalogue import parse

    value = candidate(tmp_path)
    identity = parse(value.raw).signers[0].id
    monkeypatch.setattr(mirror, "read_candidate", lambda *args: value)
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: identity)
    assert cli.main(["mirror", "enrol", "http://bunker.invalid/"]) == 0
    entries = [
        json.loads(line)
        for file in (tmp_path / "xdg_state_home").rglob("*.jsonl")
        for line in file.read_text().splitlines()
    ]
    consent = next(
        entry for entry in entries if entry.get("extra", {}).get("kind") == "bunker_signer"
    )
    assert consent["extra"]["key_fingerprint"] == identity
    assert consent["timestamp"]
    assert len(consent["disclosure_sha256"]) == 64


def test_status_displays_no_touch_and_age(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    from hammunition.interface.mirror import build_mirror, render_mirror

    state = enroll(candidate(tmp_path), [])
    state = replace(state, keys=(replace(state.keys[0], no_touch_required=True),))
    doc = build_mirror(state, require_hardware=False, now=datetime(2026, 12, 7, tzinfo=UTC))
    assert doc.keys[0].no_touch_required is True
    assert "no_touch_required=True" in "\n".join(render_mirror(doc))
    assert "Bunker catalogue is older than 30 days" in doc.warnings


def test_transport_header_bounds_redirect_and_body_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error
    import urllib.request
    from email.message import Message

    from hammunition.backends import BackendError
    from hammunition.mirror_transport import MirrorTransport

    source = MirrorTransport("http://bunker.invalid/base/", "laptop")
    requests: list[urllib.request.Request] = []

    def opened(request: urllib.request.Request, **kwargs: object) -> io.BytesIO:
        requests.append(request)
        return io.BytesIO(b"payload")

    monkeypatch.setattr(source.opener, "open", opened)
    assert source.read("catalogue.json", max_bytes=8) == b"payload"
    assert requests[0].get_header("X-hammunition-enrolment") == "laptop"
    with pytest.raises(BackendError, match="larger"):
        source.read("catalogue.json", max_bytes=2)
    with (
        pytest.raises(BackendError, match="leaves"),
        source.open("http://else.invalid/base/catalogue.json"),
    ):
        pass
    error = OSError("body error")
    with pytest.raises(OSError) as caught, source.open("http://bunker.invalid/base/catalogue.json"):
        raise error
    assert caught.value is error

    def redirected(request: urllib.request.Request, **kwargs: object) -> None:
        raise urllib.error.HTTPError(request.full_url, 302, "redirect", Message(), None)

    monkeypatch.setattr(source.opener, "open", redirected)
    with pytest.raises(BackendError, match="redirects are not followed"):
        source.read("catalogue.json", max_bytes=8)
    handlers: list[object] = vars(source.opener)["handlers"]
    assert not any(isinstance(handler, urllib.request.HTTPRedirectHandler) for handler in handlers)


def test_read_candidate_uses_shared_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hammunition.mirror_transport import MirrorTransport

    value = candidate(tmp_path)
    requests: list[tuple[str, str | None, int]] = []

    def read(self: MirrorTransport, relative: str, *, max_bytes: int) -> bytes:
        requests.append((relative, self.enrolment_id, max_bytes))
        return value.raw if relative == "catalogue.json" else value.signatures[relative]

    monkeypatch.setattr(MirrorTransport, "read", read)
    assert mirror.read_candidate("http://bunker.invalid/", "laptop") == value
    assert requests == [
        ("catalogue.json", "laptop", 32 * 1024 * 1024),
        ("catalogue.sig.d/1.sig", "laptop", 64 * 1024),
    ]


def test_install_refuses_changed_station_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hammunition.station import Station, save_station

    enroll(candidate(tmp_path), [])
    save_station(Station(mirror="http://changed.invalid/"))
    assert cli.main(["install", "--dry-run", "--yes", "--no-refresh", "fldigi"]) == 2


def test_fingerprint_case_and_disclosure(tmp_path: Path) -> None:
    from hammunition.catalogue import parse
    from hammunition.consent import ConsentDeclined

    private = key(tmp_path, "rsa", 2048)
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public))
    value = mirror.Candidate(raw, signatures)
    prompts = []

    def lower(text: str) -> str:
        prompts.append(text)
        return parse(raw).signers[0].id.lower()

    with pytest.raises(ConsentDeclined):
        enroll(value, [], choose=lower)
    assert "ssh-rsa, 2048 bits" in prompts[0]
    assert "RSA 2048-bit is weak" in prompts[0]
    assert "no_touch_required=False" in prompts[0]


def test_mirror_empty_golden(capsys: pytest.CaptureFixture[str]) -> None:
    from json_support import assert_golden, assert_golden_text, parse_one, validate

    assert cli.main(["mirror", "status", "--json"]) == 0
    doc = parse_one(capsys.readouterr().out)
    validate(doc)
    assert_golden("mirror-none", doc)
    assert cli.main(["mirror", "status"]) == 0
    assert_golden_text("mirror-none", capsys.readouterr().out)


def test_policy_json_set_and_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["station", "set", "--mirror-require-hardware-key", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["saved"]["mirror_require_hardware_key"] is True
    assert cli.main(["station", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mirror_require_hardware_key"] is True
    assert cli.main(["station", "set", "--no-mirror-require-hardware-key"]) == 0
    assert not load_station().mirror_require_hardware_key


def test_status_sk_metadata_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import base64
    import struct
    from dataclasses import replace

    from hammunition.catalogue import parse
    from json_support import assert_golden, parse_one, validate

    original = candidate(tmp_path)
    state = enroll(original, [])
    blob = base64.b64decode(state.keys[0].public_key.split()[1])
    length = struct.unpack(">I", blob[:4])[0]
    algorithm = "sk-ssh-ed25519@openssh.com"
    wrapped = (
        struct.pack(">I", len(algorithm))
        + algorithm.encode()
        + blob[4 + length :]
        + struct.pack(">I", 4)
        + b"ssh:"
    )
    public = algorithm + " " + base64.b64encode(wrapped).decode()
    strength = classify(public)
    enrolled = signers.EnrolledKey(strength.fingerprint, public, algorithm, 256, True, True)
    state = replace(state, keys=(enrolled,))
    signers.save_mirror(state)
    from datetime import UTC, datetime
    from types import SimpleNamespace

    monkeypatch.setattr(
        cli, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 8, tzinfo=UTC))
    )
    capsys.readouterr()
    assert cli.main(["mirror", "status", "--json"]) == 0
    doc = parse_one(capsys.readouterr().out)
    validate(doc)
    assert doc["keys"][0]["no_touch_required"] is True
    assert doc["age_days"] == 0
    assert_golden("mirror-sk", doc, {strength.fingerprint: "<fingerprint>"})
    assert cli.main(["mirror", "status"]) == 0
    assert "no_touch_required=True" in capsys.readouterr().out
    from hammunition.mirror import display_signer

    signer = (
        parse(original.raw)
        .signers[0]
        .model_copy(
            update=dict(
                id=strength.fingerprint,
                public_key=public,
                algorithm=algorithm,
                bits=256,
                hardware=True,
                no_touch_required=True,
            )
        )
    )
    assert "no_touch_required=True" in display_signer(signer)


def test_reenrol_retains_only_selected_keys_and_preserves_serial(tmp_path: Path) -> None:
    from hammunition.catalogue import parse

    first = candidate(tmp_path / "first")
    state = enroll(first, [])
    private = key(tmp_path / "second")
    public = private.with_suffix(".pub").read_text().strip()
    doc = document(public, serial=43)
    extra = doc["signers"]
    assert isinstance(extra, list)
    extra.append(parse(first.raw).signers[0].model_dump() | {"signature": "catalogue.sig.d/2.sig"})
    raw, signatures = signed(tmp_path / "second", private, doc)
    newer = enroll(mirror.Candidate(raw, signatures), [])
    assert len(newer.keys) == 1
    assert newer.keys[0].id != state.keys[0].id
    assert newer.accepted_serial == 43
    assert signers.load_mirror() == newer


def test_explicit_new_url_does_not_reuse_serial(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from hammunition.catalogue import parse

    first = candidate(tmp_path / "first")
    enroll(first, [])
    second = candidate(tmp_path / "second", serial=1)
    identity = parse(second.raw).signers[0].id
    state = mirror.enrol(
        second,
        "http://new.invalid/",
        None,
        choose=lambda text: identity,
        affirm_hardware=None,
        owner=None,
        require_hardware=False,
        now=datetime(2026, 10, 7, 13, tzinfo=UTC),
        record_consent=lambda record: None,
    )
    assert state.accepted_serial == 1
    assert load_station().mirror == state.url


@pytest.mark.parametrize(
    "respelled",
    [
        "http://bunker.invalid:8080",  # no trailing slash
        "http://bunker.invalid:8080//",  # extra slash
        "http://BUNKER.invalid:8080/",  # host case
    ],
)
def test_a_cosmetically_respelled_url_cannot_reset_the_serial_floor(
    tmp_path: Path, respelled: str
) -> None:
    """A trailing slash, a repeated one, or host case must not reset the
    rollback floor to 0 for the same Bunker name: that would let a LAN
    position attacker, or a Bunker rolled back to an old snapshot, serve a
    genuinely-signed but older catalogue and have it accepted with no
    `accept-older` prompt (an Opus adversarial review of the whole branch,
    #381)."""
    from datetime import UTC, datetime

    from hammunition.catalogue import parse

    first = candidate(tmp_path / "first", serial=500)
    identity = parse(first.raw).signers[0].id
    mirror.enrol(
        first,
        "http://bunker.invalid:8080/",
        None,
        choose=lambda text: identity,
        affirm_hardware=None,
        owner=None,
        require_hardware=False,
        now=datetime(2026, 10, 7, 13, tzinfo=UTC),
        record_consent=lambda record: None,
    )
    older = candidate(tmp_path / "older", serial=1)
    older_identity = parse(older.raw).signers[0].id
    with pytest.raises(signers.SignerError, match="accept-older"):
        mirror.enrol(
            older,
            respelled,
            None,
            choose=lambda text: older_identity,
            affirm_hardware=None,
            owner=None,
            require_hardware=False,
            now=datetime(2026, 10, 7, 14, tzinfo=UTC),
            record_consent=lambda record: None,
        )
    unchanged = signers.load_mirror(owner=None)
    assert unchanged is not None and unchanged.accepted_serial == 500


def test_hardware_policy_refuses_file_key(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from hammunition.catalogue import parse

    value = candidate(tmp_path)
    identity = parse(value.raw).signers[0].id
    with pytest.raises(signers.SignerError, match="hardware"):
        mirror.enrol(
            value,
            "http://bunker.invalid/",
            None,
            choose=lambda text: identity,
            affirm_hardware=None,
            owner=None,
            require_hardware=True,
            now=datetime(2026, 10, 7, 13, tzinfo=UTC),
            record_consent=lambda record: None,
        )
    assert signers.load_mirror() is None
