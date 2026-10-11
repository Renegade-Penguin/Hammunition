# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""One verified Bunker catalogue, read over loopback HTTP or a file export.

Every HTTP test runs real ``http.server`` servers on 127.0.0.1 (port 0), shut
down and joined at teardown: a bare ``close`` leaves ``accept()`` stealing a
later test's connections.
"""

from __future__ import annotations

import errno
import hashlib
import http.server
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import pytest

from bunker_fixtures import artifact, document, key, signed
from hammunition.backends import BackendError
from hammunition.catalogue import CatalogueError
from hammunition.fetch import Fetcher, MirrorPath, UrllibTransport
from hammunition.keystrength import classify
from hammunition.manifest.schema import RemoteArtifact
from hammunition.mirror_transport import (
    CatalogueInputs,
    MirrorTransport,
    load_catalogue,
    read_catalogue,
)
from hammunition.signers import (
    EnrolledKey,
    MirrorState,
    SignerError,
    advance_mirror,
    load_mirror,
    save_mirror,
)

NOW = datetime(2026, 10, 7, tzinfo=UTC)
LOCALHOST = "127.0.0.1"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.delenv("SUDO_USER", raising=False)


class Recorder(http.server.ThreadingHTTPServer):
    """Serves *files* from memory and records (path, enrolment header) pairs."""

    daemon_threads = True

    def __init__(
        self,
        files: dict[str, bytes],
        redirect: Callable[[str], str | None] = lambda path: None,
    ) -> None:
        self.files = files
        self.seen: list[tuple[str, str | None]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.seen.append((self.path, self.headers.get("X-Hammunition-Enrolment")))
                target = redirect(self.path)
                if target is not None:
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = outer.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        super().__init__((LOCALHOST, 0), Handler)

    @property
    def base(self) -> str:
        return f"http://{LOCALHOST}:{self.server_port}/bunker/"


@contextmanager
def serving(*servers: Recorder) -> Iterator[None]:
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    try:
        yield
    finally:
        for server in servers:
            server.shutdown()
        for thread in threads:
            thread.join(timeout=10)
        for server in servers:
            server.server_close()


def enrolled(
    tmp_path: Path, url: str, serial: int = 0
) -> tuple[MirrorState, bytes, dict[str, bytes], Path]:
    private = key(tmp_path / "keys")
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public))
    strength = classify(public)
    state = MirrorState(
        url,
        "bunker",
        "personal",
        "laptop",
        (EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False),),
        serial,
    )
    save_mirror(state)
    return state, raw, signatures, private


def export(tmp_path: Path, raw: bytes, signatures: dict[str, bytes]) -> Path:
    root = tmp_path / "export"
    (root / "catalogue.sig.d").mkdir(parents=True)
    (root / "catalogue.json").write_bytes(raw)
    for name, body in signatures.items():
        (root / name).write_bytes(body)
    return root


def served(raw: bytes, signatures: dict[str, bytes]) -> dict[str, bytes]:
    files = {"/bunker/catalogue.json": raw}
    files.update({f"/bunker/{name}": body for name, body in signatures.items()})
    return files


# -- file exports -----------------------------------------------------------


def test_file_export_verifies_exact_bytes(tmp_path: Path) -> None:
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public))
    root = export(tmp_path, raw, signatures)
    strength = classify(public)
    state = MirrorState(
        root.as_uri(),
        "bunker",
        "personal",
        None,
        (EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False),),
        0,
    )
    save_mirror(state)
    got = load_catalogue(state, require_hardware=False, now=NOW)
    assert got.raw == raw and got.catalogue.serial == 42


@pytest.mark.parametrize(
    "relative",
    [
        "../secret",
        "a/../../secret",
        "a//b",
        "a\\b",
        "/etc/passwd",
        "",
        "a/./b",
    ],
)
def test_file_relative_paths_are_confined(tmp_path: Path, relative: str) -> None:
    (tmp_path / "secret").write_bytes(b"outside")
    root = tmp_path / "export"
    root.mkdir()
    source = MirrorTransport(root.as_uri(), None)
    with pytest.raises((BackendError, CatalogueError)):
        source.read(relative, max_bytes=100)


def test_file_percent_encoded_dotdot_in_a_request_url_is_refused(tmp_path: Path) -> None:
    (tmp_path / "secret").write_bytes(b"outside")
    root = tmp_path / "export"
    root.mkdir()
    source = MirrorTransport(root.as_uri(), None)
    with (
        pytest.raises((BackendError, CatalogueError)),
        source.open(f"{root.as_uri()}/%2e%2e/secret"),
    ):
        pass


def test_file_double_encoded_dotdot_is_a_literal_name_never_decoded_twice(tmp_path: Path) -> None:
    (tmp_path / "secret").write_bytes(b"outside")
    root = tmp_path / "export"
    (root / "%2e%2e").mkdir(parents=True)
    (root / "%2e%2e" / "secret").write_bytes(b"inside")
    source = MirrorTransport(root.as_uri(), None)
    assert source.read("%2e%2e/secret", max_bytes=100) == b"inside"


def test_file_symlinked_file_and_parent_are_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"outside")
    root = tmp_path / "export"
    root.mkdir()
    (root / "link").symlink_to(outside / "secret")
    (root / "dir").symlink_to(outside, target_is_directory=True)
    source = MirrorTransport(root.as_uri(), None)
    with pytest.raises(BackendError):
        source.read("link", max_bytes=100)
    with pytest.raises(BackendError):
        source.read("dir/secret", max_bytes=100)


def test_file_base_with_a_symlinked_component_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "catalogue.json").write_bytes(b"x")
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    source = MirrorTransport((tmp_path / "alias").as_uri(), None)
    with pytest.raises(BackendError):
        source.read("catalogue.json", max_bytes=100)


def test_file_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    fifo = root / "catalogue.json"
    os.mkfifo(fifo)
    source = MirrorTransport(root.as_uri(), None)
    outcome: list[BaseException | bytes] = []

    def attempt() -> None:
        try:
            outcome.append(source.read("catalogue.json", max_bytes=100))
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=5)
    if worker.is_alive():
        # Release a reader stuck in a blocking open so the thread can end.
        try:
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError as exc:  # ENXIO: nobody has it open for reading yet
            assert exc.errno == errno.ENXIO
        worker.join(timeout=5)
        pytest.fail("reading a FIFO from a file mirror blocked")
    assert len(outcome) == 1
    assert isinstance(outcome[0], BackendError)
    assert "not a regular file" in str(outcome[0])


def test_file_directory_target_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / "catalogue.json").mkdir(parents=True)
    with pytest.raises(BackendError):
        MirrorTransport(root.as_uri(), None).read("catalogue.json", max_bytes=100)


def test_file_request_outside_the_configured_base_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    other = tmp_path / "export-two"
    other.mkdir()
    (other / "catalogue.json").write_bytes(b"x")
    source = MirrorTransport(root.as_uri(), None)
    for url in (
        f"{other.as_uri()}/catalogue.json",
        f"{root.as_uri()}/catalogue.json?x=1",
        f"{root.as_uri()}/catalogue.json#f",
        "file://other-host" + str(root) + "/catalogue.json",
    ):
        with pytest.raises(BackendError, match="leaves"), source.open(url):
            pass


@pytest.mark.parametrize(
    "bad",
    [
        "file://other-host/srv/bunker",
        "file:///srv/bunker?x=1",
        "file:///srv/bunker#f",
        "file:///srv/../bunker",
        "file:///srv/%2e%2e/bunker",
    ],
)
def test_malformed_file_bases_never_build_a_transport(bad: str) -> None:
    with pytest.raises(Exception, match="mirror"):
        MirrorTransport(bad, None)


def test_file_bounds_are_enforced(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    (root / "big").write_bytes(b"x" * 65)
    source = MirrorTransport(root.as_uri(), None)
    assert source.read("big", max_bytes=65) == b"x" * 65
    with pytest.raises(BackendError, match="larger than 64"):
        source.read("big", max_bytes=64)


# -- HTTP over loopback -----------------------------------------------------


def test_http_header_on_every_request_kind_and_one_catalogue_get(tmp_path: Path) -> None:
    body = b"artifact payload"
    path = "unit/name"
    private = key(tmp_path / "keys")
    public = private.with_suffix(".pub").read_text().strip()
    doc = document(
        public,
        artifacts=[artifact("unit", "name", body, path=path)],
        inputs=[],
    )
    raw, signatures = signed(tmp_path, private, doc)
    files = served(raw, signatures)
    files["/bunker/unit/name"] = body
    files["/bunker/inputs/dem/region"] = b"input bytes"
    server = Recorder(files)
    strength = classify(public)
    with serving(server):
        state = MirrorState(
            server.base,
            "bunker",
            "personal",
            "laptop",
            (EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False),),
            0,
        )
        save_mirror(state)
        transport = MirrorTransport(state.url, state.enrolment_id)
        verified = load_catalogue(state, require_hardware=False, now=NOW, transport=transport)
        # Two resolver families share this one object: neither asks again.
        inputs_a = CatalogueInputs(transport)
        inputs_b = CatalogueInputs(transport)
        assert inputs_a.read("inputs/dem/region", max_bytes=100) == b"input bytes"
        assert inputs_b.read("inputs/dem/region", max_bytes=100) == b"input bytes"
        row = verified.catalogue.artifact("unit", "name", "laptop")
        assert row is not None
        remote = RemoteArtifact(url="https://example.invalid/payload", sha256=row.sha256 or "")
        fetched = Fetcher(
            tmp_path / "cache",
            transport=UrllibTransport(),
            mirror=state.url,
            mirror_transport=transport,
        ).fetch(remote, mirror=MirrorPath("unit", "name"))
        assert fetched.path.read_bytes() == body

    paths = [p for p, _ in server.seen]
    assert paths.count("/bunker/catalogue.json") == 1
    assert "/bunker/catalogue.sig.d/1.sig" in paths
    assert "/bunker/inputs/dem/region" in paths
    assert "/bunker/unit/name" in paths
    assert all(header == "laptop" for _, header in server.seen)
    assert verified.raw == raw


def test_http_redirect_is_refused_and_the_target_sees_nothing(tmp_path: Path) -> None:
    target = Recorder({"/evil": b"leak"})
    first = Recorder(
        {},
        redirect=lambda path: (
            f"http://{LOCALHOST}:{target.server_port}/evil"
            if path.endswith("catalogue.json")
            else None
        ),
    )
    with serving(first, target):
        source = MirrorTransport(first.base, "laptop")
        with pytest.raises(BackendError, match="redirects are not followed"):
            source.read("catalogue.json", max_bytes=100)
    assert first.seen == [("/bunker/catalogue.json", "laptop")]
    assert target.seen == []


def test_http_catalogue_read_redirect_through_the_loader_leaks_nothing(tmp_path: Path) -> None:
    state, _raw, _sigs, _private = enrolled(tmp_path, "http://127.0.0.1:1/bunker/")
    target = Recorder({"/evil": b"leak"})
    first = Recorder({}, redirect=lambda path: f"http://{LOCALHOST}:{target.server_port}/evil")
    with serving(first, target):
        live = replace(state, url=first.base)
        with pytest.raises(BackendError, match="redirects are not followed"):
            load_catalogue(live, require_hardware=False, now=NOW)
    assert target.seen == []


def test_http_redirect_to_a_file_is_refused(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"outside")
    first = Recorder({}, redirect=lambda path: secret.as_uri())
    with serving(first), pytest.raises(BackendError, match="redirects are not followed"):
        MirrorTransport(first.base, "laptop").read("catalogue.json", max_bytes=100)


# -- the loader -------------------------------------------------------------


class Recording(MirrorTransport):
    def __init__(self, base: str, files: dict[str, bytes]) -> None:
        super().__init__(base, None)
        self.files = files
        self.calls: list[tuple[str, int]] = []

    def read(self, relative: str, *, max_bytes: int) -> bytes:
        self.calls.append((relative, max_bytes))
        try:
            return self.files[relative]
        except KeyError:
            raise BackendError("HTTP 404") from None


def test_loader_bounds_catalogue_32_mib_and_signature_64_kib(tmp_path: Path) -> None:
    state, raw, signatures, _ = enrolled(tmp_path, "http://bunker.invalid/")
    recording = Recording(state.url, {"catalogue.json": raw, **signatures})
    load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    assert recording.calls == [
        ("catalogue.json", 32 * 1024 * 1024),
        ("catalogue.sig.d/1.sig", 64 * 1024),
    ]


def test_loader_skips_signatures_of_keys_that_are_not_enrolled(tmp_path: Path) -> None:
    state, raw, signatures, _ = enrolled(tmp_path, "http://bunker.invalid/")
    state = MirrorState(state.url, state.name, state.mode, state.enrolment_id, (), 0)
    recording = Recording(state.url, {"catalogue.json": raw, **signatures})
    with pytest.raises(SignerError):
        load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    assert [c[0] for c in recording.calls] == ["catalogue.json"]


def test_absent_signature_refuses_and_does_not_advance(tmp_path: Path) -> None:
    state, raw, _signatures, _ = enrolled(tmp_path, "http://bunker.invalid/")
    recording = Recording(state.url, {"catalogue.json": raw})
    with pytest.raises(SignerError):
        load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    stored = load_mirror()
    assert stored is not None and stored.accepted_serial == 0


def test_bad_signature_refuses_and_does_not_advance(tmp_path: Path) -> None:
    state, raw, _signatures, _ = enrolled(tmp_path, "http://bunker.invalid/")
    other = key(tmp_path / "other-keys")
    (tmp_path / "other").mkdir()
    _other_raw, other_signatures = signed(
        tmp_path / "other", other, document(other.with_suffix(".pub").read_text().strip())
    )
    recording = Recording(state.url, {"catalogue.json": raw, **other_signatures})
    with pytest.raises(SignerError):
        load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    stored = load_mirror()
    assert stored is not None and stored.accepted_serial == 0


def test_good_verification_persists_the_serial_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, raw, signatures, _ = enrolled(tmp_path, "http://bunker.invalid/", serial=10)
    calls: list[tuple[int, str]] = []
    real = advance_mirror

    def counting(state: MirrorState, serial: int, generated: str, *, owner: str | None) -> None:
        calls.append((serial, generated))
        real(state, serial, generated, owner=owner)

    monkeypatch.setattr("hammunition.mirror_transport.advance_mirror", counting)
    recording = Recording(state.url, {"catalogue.json": raw, **signatures})
    load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    assert calls == [(42, "2026-10-07T12:00:00Z")]
    stored = load_mirror()
    assert stored is not None and stored.accepted_serial == 42
    assert stored.generated == "2026-10-07T12:00:00Z"


def test_a_stale_in_memory_serial_never_lowers_the_stored_one(tmp_path: Path) -> None:
    state, raw, signatures, _ = enrolled(tmp_path, "http://bunker.invalid/", serial=10)
    save_mirror(replace(state, accepted_serial=60))
    recording = Recording(state.url, {"catalogue.json": raw, **signatures})
    load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    stored = load_mirror()
    assert stored is not None and stored.accepted_serial == 60


def test_older_catalogue_than_accepted_refuses(tmp_path: Path) -> None:
    state, raw, signatures, _ = enrolled(tmp_path, "http://bunker.invalid/", serial=50)
    recording = Recording(state.url, {"catalogue.json": raw, **signatures})
    with pytest.raises(SignerError, match="older"):
        load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    stored = load_mirror()
    assert stored is not None and stored.accepted_serial == 50


def test_loader_without_a_transport_builds_one_from_the_state(tmp_path: Path) -> None:
    private = key(tmp_path / "keys")
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public))
    server = Recorder(served(raw, signatures))
    strength = classify(public)
    with serving(server):
        state = MirrorState(
            server.base,
            "bunker",
            "personal",
            "laptop",
            (EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False),),
            0,
        )
        save_mirror(state)
        got = load_catalogue(state, require_hardware=False, now=NOW)
    assert got.raw == raw
    assert {header for _, header in server.seen} == {"laptop"}


# -- group filter -----------------------------------------------------------


def group_export(tmp_path: Path, mode: str) -> tuple[Path, MirrorState]:
    private = key(tmp_path / "keys")
    public = private.with_suffix(".pub").read_text().strip()
    rows = [
        artifact("unit", "shared", b"s", share="all"),
        artifact("unit", "mine", b"m", share="owner:laptop"),
        artifact("unit", "theirs", b"t", share="owner:other"),
    ]
    raw, signatures = signed(
        tmp_path, private, document(public, bunker={"name": "bunker", "mode": mode}, artifacts=rows)
    )
    root = export(tmp_path, raw, signatures)
    for name, body in (("shared", b"s"), ("mine", b"m"), ("theirs", b"t")):
        (root / "unit").mkdir(exist_ok=True)
        (root / "unit" / name).write_bytes(body)
    strength = classify(public)
    state = MirrorState(
        root.as_uri(),
        "bunker",
        mode,
        "laptop",
        (EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False),),
        0,
    )
    save_mirror(state)
    return root, state


def test_group_mode_ignores_another_operators_entry_even_when_bytes_exist(tmp_path: Path) -> None:
    root, state = group_export(tmp_path, "group")
    assert (root / "unit" / "theirs").read_bytes() == b"t"
    verified = load_catalogue(state, require_hardware=False, now=NOW)
    assert verified.catalogue.artifact("unit", "theirs", state.enrolment_id) is None
    assert verified.catalogue.artifact("unit", "shared", state.enrolment_id) is not None
    assert verified.catalogue.artifact("unit", "mine", state.enrolment_id) is not None
    assert verified.catalogue.artifact("unit", "mine", None) is None


def test_personal_mode_uses_every_entry(tmp_path: Path) -> None:
    _root, state = group_export(tmp_path, "personal")
    verified = load_catalogue(state, require_hardware=False, now=NOW)
    assert verified.catalogue.artifact("unit", "theirs", state.enrolment_id) is not None


# -- the exact missing-catalogue remedy -------------------------------------


class MissingCatalogue(MirrorTransport):
    def read(self, relative: str, *, max_bytes: int) -> bytes:
        assert relative == "catalogue.json"
        raise BackendError("HTTP 404")


REMEDY = (
    "no catalogue at http://bunker.invalid/export/catalogue.json: HTTP 404. "
    "Enrol a Bunker that serves one, or run without --offline."
)


def test_missing_catalogue_has_exact_remedy_when_enrolling(monkeypatch: pytest.MonkeyPatch) -> None:
    import hammunition.mirror as mirror

    monkeypatch.setattr(mirror, "MirrorTransport", MissingCatalogue)
    with pytest.raises(BackendError) as caught:
        mirror.read_candidate("http://bunker.invalid/export/", None)
    assert str(caught.value) == REMEDY


def test_missing_catalogue_has_the_same_remedy_when_loading(tmp_path: Path) -> None:
    state, _raw, _signatures, _ = enrolled(tmp_path, "http://bunker.invalid/export/")
    with pytest.raises(BackendError) as caught:
        load_catalogue(
            state,
            require_hardware=False,
            now=NOW,
            transport=MissingCatalogue("http://bunker.invalid/export/", None),
        )
    assert str(caught.value) == REMEDY


def test_missing_file_catalogue_names_the_url_and_the_remedy(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    state = MirrorState(root.as_uri(), "bunker", "personal", None, (), 0)
    with pytest.raises(BackendError) as caught:
        load_catalogue(state, require_hardware=False, now=NOW)
    text = str(caught.value)
    assert text.startswith(f"no catalogue at {root.as_uri()}/catalogue.json: ")
    assert text.endswith(". Enrol a Bunker that serves one, or run without --offline.")


# -- the input adapter ------------------------------------------------------


def test_inputs_adapter_turns_backend_errors_into_oserror() -> None:
    inputs = CatalogueInputs(MissingCatalogue("http://bunker.invalid/export/", None))
    with pytest.raises(OSError, match="HTTP 404") as caught:
        inputs.read("catalogue.json", max_bytes=10)
    assert isinstance(caught.value.__cause__, BackendError)


@pytest.mark.parametrize("relative", ["a\\b", "../x", "a//b", "/abs", "a/./b", ""])
def test_unsafe_relative_paths_stay_backend_errors_and_oserror_at_the_boundary(
    tmp_path: Path, relative: str
) -> None:
    for base in ((tmp_path).as_uri(), "http://bunker.invalid/export/"):
        source = MirrorTransport(base, None)
        with pytest.raises(BackendError) as caught:
            source.read(relative, max_bytes=10)
        assert not isinstance(caught.value, CatalogueError)
        with pytest.raises(OSError):
            CatalogueInputs(source).read(relative, max_bytes=10)


def test_a_file_base_with_a_backslash_gets_the_missing_catalogue_refusal(tmp_path: Path) -> None:
    base = tmp_path.as_uri() + "/a%5Cb"
    state = MirrorState(base, "bunker", "personal", None, (), 0)
    with pytest.raises(BackendError) as caught:
        load_catalogue(state, require_hardware=False, now=NOW)
    text = str(caught.value)
    assert text.startswith(f"no catalogue at {base}/catalogue.json: ")
    assert text.endswith(". Enrol a Bunker that serves one, or run without --offline.")


def test_a_symlinked_component_is_named_in_the_refusal(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "unit").mkdir(parents=True)
    (real / "unit" / "name").write_bytes(b"x")
    root = tmp_path / "export"
    root.mkdir()
    (root / "unit").symlink_to(real / "unit", target_is_directory=True)
    (root / "leaf").symlink_to(real / "unit" / "name")
    source = MirrorTransport(root.as_uri(), None)
    for relative, component in (("unit/name", "unit"), ("leaf", "leaf")):
        with pytest.raises(BackendError) as caught:
            source.read(relative, max_bytes=10)
        text = str(caught.value)
        assert "symlinked path components in a file mirror are refused" in text
        assert repr(component) in text


def test_an_http_404_does_not_claim_redirects_are_not_followed() -> None:
    server = Recorder({})
    with serving(server), pytest.raises(BackendError) as caught:
        read_catalogue(MirrorTransport(server.base, "laptop"))
    text = str(caught.value)
    assert "HTTP 404" in text and "redirect" not in text
    assert text.endswith(". Enrol a Bunker that serves one, or run without --offline.")


def test_signature_fetch_reason_survives_into_the_refusal(tmp_path: Path) -> None:
    state, raw, _signatures, _ = enrolled(tmp_path, "http://bunker.invalid/")
    recording = Recording(state.url, {"catalogue.json": raw})
    with pytest.raises(SignerError, match="no enrolled signature verified") as caught:
        load_catalogue(state, require_hardware=False, now=NOW, transport=recording)
    assert "catalogue.sig.d/1.sig: HTTP 404" in str(caught.value)
    assert isinstance(caught.value.__cause__, SignerError)


# -- wiring -----------------------------------------------------------------


def test_fetch_transport_enrolment_header_only_for_the_enrolled_url(tmp_path: Path) -> None:
    from hammunition import mirror

    state, *_ = enrolled(tmp_path, "http://bunker.invalid/export/")
    got = mirror.fetch_transport(state.url, state)
    assert isinstance(got, MirrorTransport) and got.enrolment_id == "laptop"
    # An un-enrolled http mirror keeps the publisher transport.
    assert mirror.fetch_transport("http://other.invalid/", state) is None
    assert mirror.fetch_transport("http://other.invalid/", None) is None
    assert mirror.fetch_transport(None, state) is None
    assert mirror.fetch_transport(state.url, None) is None


def test_fetch_transport_serves_a_file_export_without_enrolment(tmp_path: Path) -> None:
    from hammunition import mirror

    state, *_ = enrolled(tmp_path, "http://bunker.invalid/export/")
    export_url = (tmp_path / "usb").as_uri()
    for enrolled_state in (None, state):
        got = mirror.fetch_transport(export_url, enrolled_state)
        assert isinstance(got, MirrorTransport)
        assert got.enrolment_id is None and got.base == export_url


def test_an_unenrolled_file_export_serves_an_artifact(tmp_path: Path) -> None:
    from hammunition import mirror

    body = b"artifact from a USB stick"
    root = tmp_path / "usb"
    (root / "unit").mkdir(parents=True)
    (root / "unit" / "name").write_bytes(body)
    transport = mirror.fetch_transport(root.as_uri(), None)
    assert transport is not None
    remote = RemoteArtifact(
        url="https://example.invalid/payload", sha256=hashlib.sha256(body).hexdigest()
    )

    class Publisher(UrllibTransport):
        def open(self, url: str) -> NoReturn:
            raise AssertionError(f"the publisher was asked for {url}")

    fetched = Fetcher(
        tmp_path / "cache",
        transport=Publisher(),
        mirror=root.as_uri(),
        mirror_transport=transport,
    ).fetch(remote, mirror=MirrorPath("unit", "name"))
    assert fetched.path.read_bytes() == body


def test_publisher_transport_never_carries_the_enrolment_header() -> None:
    import inspect

    assert "X-Hammunition-Enrolment" not in inspect.getsource(UrllibTransport)


def test_a_file_mirror_does_not_crash_repeater_snapshots() -> None:
    from hammunition import repeater_sources
    from hammunition.repeaters import RepeaterFetchError

    seen: list[str] = []

    def fetch(url: str, *, limit: int) -> tuple[bytes, str, datetime]:
        seen.append(url)
        raise RepeaterFetchError("publisher down")

    snapshot = repeater_sources.snapshots()[0]
    with pytest.raises(RepeaterFetchError):
        repeater_sources.read_snapshot(
            snapshot,
            lambda body, url: None,  # type: ignore[arg-type,return-value]
            mirror="file:///srv/bunker",
            fetch=fetch,
        )
    assert seen == [snapshot.url]
