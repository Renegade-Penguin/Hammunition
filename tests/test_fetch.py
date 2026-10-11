# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The verified fetcher.

The property worth asserting is not that a good download succeeds. It is that a
**bad one leaves nothing usable behind** — CLAUDE.md requires checksum
verification and a refusal to install without it, and the way that requirement
fails in practice is not "we forgot to check" but "we checked, refused, and left
the file where the next run would find it".

So the tests below are written to the failure: a corrupted transfer, a tampered
cache, an oversized body, a dead host. Each asserts the refusal *and* the state
of the cache directory afterwards.
"""

from __future__ import annotations

import hashlib
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import IO

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hammunition.backends import BackendError  # noqa: E402
from hammunition.fetch import (  # noqa: E402
    Fetcher,
    FetchResult,
    UrllibTransport,
    VerificationError,
    signature_gap,
)
from hammunition.manifest.schema import RemoteArtifact  # noqa: E402

PAYLOAD = b"the artifact bytes\n"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
URL = "https://example.invalid/glfer-0.4.2.tar.gz"


class FakeTransport:
    """Serves fixed bytes. Records every URL it was asked for."""

    def __init__(self, body: bytes = PAYLOAD) -> None:
        self.body = body
        self.requested: list[str] = []

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:
        self.requested.append(url)
        yield BytesIO(self.body)


def _artifact(sha256: str = DIGEST, url: str = URL, **kwargs: object) -> RemoteArtifact:
    return RemoteArtifact(url=url, sha256=sha256, **kwargs)  # type: ignore[arg-type]


def _cache_files(cache: Path) -> list[str]:
    return sorted(p.name for p in cache.iterdir()) if cache.exists() else []


# ---------------------------------------------------------------------------
# The happy path, only enough of it to make the failures meaningful
# ---------------------------------------------------------------------------


def test_a_matching_download_is_verified_and_cached(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, transport=FakeTransport())
    result = fetcher.fetch(_artifact())

    assert isinstance(result, FetchResult)
    assert result.sha256 == DIGEST
    assert result.size == len(PAYLOAD)
    assert not result.from_cache
    assert result.path.read_bytes() == PAYLOAD
    assert result.path.name.startswith(DIGEST), "the cache path must encode the digest"


def test_a_second_fetch_reuses_the_cache_without_downloading(tmp_path: Path) -> None:
    transport = FakeTransport()
    fetcher = Fetcher(tmp_path, transport=transport)
    fetcher.fetch(_artifact())
    again = fetcher.fetch(_artifact())

    assert again.from_cache
    assert len(transport.requested) == 1, "a cached artifact was downloaded again"


def test_path_for_predicts_the_destination_without_touching_disk(tmp_path: Path) -> None:
    """The plan discloses where a download will land before it lands."""
    fetcher = Fetcher(tmp_path, transport=FakeTransport())
    predicted = fetcher.path_for(_artifact())
    assert not tmp_path.exists() or _cache_files(tmp_path) == []
    assert fetcher.fetch(_artifact()).path == predicted


# ---------------------------------------------------------------------------
# The refusals. These are the reason the module exists.
# ---------------------------------------------------------------------------


def test_a_mismatched_digest_is_refused(tmp_path: Path) -> None:
    wrong = hashlib.sha256(b"something else entirely").hexdigest()
    fetcher = Fetcher(tmp_path, transport=FakeTransport())

    with pytest.raises(VerificationError) as caught:
        fetcher.fetch(_artifact(sha256=wrong))

    message = str(caught.value)
    assert wrong in message and DIGEST in message, "the error must show both digests"


def test_a_mismatched_download_leaves_nothing_behind(tmp_path: Path) -> None:
    """The failure mode that matters. Refusing and then leaving the bad file in
    the cache would let the *next* run pick it up as if it had been verified."""
    wrong = hashlib.sha256(b"something else entirely").hexdigest()
    fetcher = Fetcher(tmp_path, transport=FakeTransport())

    with pytest.raises(VerificationError):
        fetcher.fetch(_artifact(sha256=wrong))

    assert _cache_files(tmp_path) == [], (
        f"a rejected download was left in the cache: {_cache_files(tmp_path)}"
    )


def test_a_tampered_cache_entry_is_not_trusted(tmp_path: Path) -> None:
    """A file that was verified once is re-verified, not trusted for having
    been. Content-addressing means a mismatch here is corruption or tampering,
    never a stale version — so it is replaced, and never served."""
    transport = FakeTransport()
    fetcher = Fetcher(tmp_path, transport=transport)
    cached = fetcher.fetch(_artifact()).path

    cached.write_bytes(b"replaced after verification")
    result = fetcher.fetch(_artifact())

    assert result.path.read_bytes() == PAYLOAD, "tampered cache content was served"
    assert not result.from_cache
    assert len(transport.requested) == 2, "the tampered entry was not re-downloaded"


def test_an_oversized_body_is_abandoned(tmp_path: Path) -> None:
    big = b"x" * 4096
    fetcher = Fetcher(
        tmp_path,
        transport=FakeTransport(big),
        max_bytes=1024,
    )

    with pytest.raises(BackendError, match="byte limit"):
        fetcher.fetch(_artifact(sha256=hashlib.sha256(big).hexdigest()))

    assert _cache_files(tmp_path) == [], "an abandoned oversized download was left behind"


def test_a_transport_failure_leaves_nothing_behind(tmp_path: Path) -> None:
    class Failing:
        @contextmanager
        def open(self, url: str) -> Iterator[IO[bytes]]:
            raise BackendError("host is unreachable")
            # Unreachable by design: this fake fails at open(), before any
            # stream exists. The yield is what makes it a generator.
            yield BytesIO(b"")  # type: ignore[unreachable]

    fetcher = Fetcher(tmp_path, transport=Failing())
    with pytest.raises(BackendError, match="unreachable"):
        fetcher.fetch(_artifact())
    assert _cache_files(tmp_path) == []


def test_a_partial_transfer_leaves_nothing_behind(tmp_path: Path) -> None:
    """A stream that dies mid-body must not leave a `.part` file that a later
    run could mistake for anything."""

    class Truncating:
        @contextmanager
        def open(self, url: str) -> Iterator[IO[bytes]]:
            class Stream:
                def read(self, _n: int = -1) -> bytes:
                    raise OSError("connection reset")

            yield Stream()  # type: ignore[misc]

    fetcher = Fetcher(tmp_path, transport=Truncating())
    with pytest.raises(OSError, match="connection reset"):
        fetcher.fetch(_artifact())
    assert _cache_files(tmp_path) == []


# ---------------------------------------------------------------------------
# Cache naming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/../../etc/passwd",
        "https://example.invalid/..%2f..%2fetc%2fpasswd",
        "https://example.invalid/",
        "https://example.invalid/name?query=1#frag",
        "https://example.invalid/" + "a" * 500,
    ],
)
def test_the_cache_name_stays_inside_the_cache(tmp_path: Path, url: str) -> None:
    """The digest identifies the file; the readable half is derived from the URL
    and must never be able to escape the directory or grow unboundedly.

    The property is where the path *resolves*, not whether the name contains a
    scary substring: `..` between two ordinary characters, with no separator
    anywhere, traverses nothing. Asserting on the substring instead flagged
    `..%2f..%2fetc%2fpasswd` — which lands harmlessly inside the cache — while
    proving nothing about traversal.
    """
    fetcher = Fetcher(tmp_path, transport=FakeTransport())
    path = fetcher.path_for(_artifact(url=url))

    assert path.resolve().parent == tmp_path.resolve(), "the cache name escaped the cache"
    assert path.name not in {".", ".."}
    assert not path.name.startswith("."), "a cache entry must not be a dotfile"
    assert "/" not in path.name and "\0" not in path.name
    assert len(path.name) <= 64 + len(DIGEST) + 1


def test_two_urls_with_the_same_bytes_share_one_entry(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, transport=FakeTransport())
    first = fetcher.fetch(_artifact(url="https://a.invalid/pkg.tar.gz"))
    second = fetcher.fetch(_artifact(url="https://b.invalid/pkg.tar.gz"))
    assert first.path == second.path
    assert second.from_cache


# ---------------------------------------------------------------------------
# The gap we do not paper over
# ---------------------------------------------------------------------------


def test_an_unsigned_artifact_reports_no_gap() -> None:
    assert signature_gap(_artifact()) is None


def test_a_signed_artifact_reports_that_we_do_not_check_it() -> None:
    """CLAUDE.md requires honest gaps. A manifest carrying a signature URL the
    engine ignores reads as covered when it is not, so it says so."""
    artifact = _artifact(signature_url="https://example.invalid/pkg.tar.gz.asc")
    gap = signature_gap(artifact)
    assert gap is not None
    assert "does not verify" in gap


# ---------------------------------------------------------------------------
# The transport itself
# ---------------------------------------------------------------------------


def test_the_real_transport_has_no_handler_for_file_urls(tmp_path: Path) -> None:
    """A redirect to `file://` must not be followed into the local filesystem.
    The schema refuses a non-http(s) `url`; it cannot see the redirect chain,
    so the opener is built without those handlers at all."""
    secret = tmp_path / "secret"
    secret.write_text("not yours")

    transport = UrllibTransport()
    with pytest.raises(BackendError), transport.open(secret.as_uri()) as _stream:
        pass  # pragma: no cover


def test_the_suite_blocks_the_network() -> None:
    """The seam is only a seam if nothing can go around it. Asserted here so
    the guard itself is tested rather than assumed (CLAUDE.md: a check nobody
    falsified is a check nobody should trust)."""
    # A literal address, never a name: resolving a name is a DNS query, and on
    # a runner whose egress is blocked (GYST's python-ci) that query fails
    # first, with its own message, before the guard on connect() is reached
    # (#284). 192.0.2.1 is TEST-NET-1 (RFC 5737): routable nowhere, loopback
    # never, so only the guard can answer.
    with pytest.raises(Exception, match="blocked a connection"):
        socket.create_connection(("192.0.2.1", 80), timeout=1)


# ---------------------------------------------------------------------------
# MD5-verified fetch (D-057): the disclosed, weaker path for map data the
# catalog carries no sha256 pin for. `sha256`-pinned `fetch()` above must stay
# exactly as strict as it already is; these tests are only about the second,
# clearly-separate method.
# ---------------------------------------------------------------------------


def test_fetch_md5_accepts_a_matching_file(tmp_path: Path) -> None:
    body = b"osm" * 1000
    fetcher = Fetcher(tmp_path, transport=FakeTransport(body))
    result = fetcher.fetch_md5(
        "https://x/v.osm.pbf", hashlib.md5(body).hexdigest(), expected_size=len(body)
    )
    assert result.path.read_bytes() == body
    assert result.sha256 == hashlib.sha256(body).hexdigest()


def test_fetch_md5_refuses_a_mismatch_and_keeps_nothing(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, transport=FakeTransport(b"evil"))
    with pytest.raises(VerificationError, match="md5"):
        fetcher.fetch_md5("https://x/v.osm.pbf", "0" * 32, expected_size=4)
    assert _cache_files(tmp_path) == []


def test_fetch_md5_refuses_a_size_that_is_not_the_published_one(tmp_path: Path) -> None:
    body = b"x" * 10
    fetcher = Fetcher(tmp_path, transport=FakeTransport(body))
    with pytest.raises(VerificationError, match="size"):
        fetcher.fetch_md5("https://x/v.osm.pbf", hashlib.md5(body).hexdigest(), expected_size=11)
    assert _cache_files(tmp_path) == []


def test_a_declared_size_above_the_default_cap_raises_the_cap_not_removes_it(
    tmp_path: Path,
) -> None:
    """`fetch_md5`'s cap is the declared size plus 1 MiB, applied per call, not
    the instance's `max_bytes` (here deliberately set below the declared size)
    and never removed outright. The first half proves the cap was *raised*
    high enough to let a legitimately large, pinned-by-size file through; the
    second proves a server that keeps sending past that raised cap is still
    stopped, by a body that overruns `expected_size + 1 MiB` -- three bytes
    over `expected_size` alone would still fit comfortably under the +1 MiB
    allowance and prove nothing about the cap itself."""
    body = b"y" * 2048
    fetcher = Fetcher(tmp_path, transport=FakeTransport(body), max_bytes=1024)
    ok = fetcher.fetch_md5("https://x/big", hashlib.md5(body).hexdigest(), expected_size=len(body))
    assert ok.size == len(body)

    overflow = body + b"y" * (1024 * 1024 + 1)  # 1 byte past expected_size + 1 MiB
    liar = Fetcher(tmp_path / "b", transport=FakeTransport(overflow), max_bytes=1024)
    with pytest.raises(BackendError, match="byte limit"):
        liar.fetch_md5("https://x/big", hashlib.md5(overflow).hexdigest(), expected_size=len(body))


# ---------------------------------------------------------------------------
# Final review, item 2: the download temporary never follows a planted link
# ---------------------------------------------------------------------------


class _Bytes:
    def __init__(self, body: bytes) -> None:
        self.body = body

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:
        yield BytesIO(self.body)


@pytest.mark.parametrize("method", ["sha256", "md5"])
def test_a_symlink_planted_at_the_temporary_is_refused_and_its_target_untouched(
    tmp_path: Path, method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import os
    import secrets

    monkeypatch.setattr(secrets, "token_hex", lambda nbytes=None: "0" * 16)

    body = b"map bytes"
    url = "https://download.geofabrik.de/x-260101.osm.pbf"
    cache = tmp_path / "cache"
    cache.mkdir()
    fetcher = Fetcher(cache, transport=_Bytes(body))
    victim = tmp_path / "shadow"
    victim.write_text("root:secret\n")
    if method == "sha256":
        artifact = RemoteArtifact(url=url, sha256=hashlib.sha256(body).hexdigest())
        final = fetcher.path_for(artifact)

        def attempt() -> object:
            return fetcher.fetch(artifact)

    else:
        md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
        final = fetcher.md5_path_for(url, md5)

        def attempt() -> object:
            return fetcher.fetch_md5(url, md5, expected_size=len(body))

    planted = final.with_name(final.name + f".part.{os.getpid()}.{'0' * 16}")
    planted.symlink_to(victim)
    with pytest.raises(BackendError, match="temporary"):
        attempt()
    assert victim.read_text() == "root:secret\n"
    assert not final.exists()
    assert planted.is_symlink()  # exclusive creation refused it, so it is not ours to delete


# ---------------------------------------------------------------------------
# ETag-verified fetch (D-068): US Topo quads, checked against the publisher's
# S3 ETag, single-part or multipart.
# ---------------------------------------------------------------------------


def _multipart_etag(body: bytes, part: int) -> str:
    digests = b"".join(hashlib.md5(body[i : i + part]).digest() for i in range(0, len(body), part))
    return f"{hashlib.md5(digests).hexdigest()}-{-(-len(body) // part)}"


def test_fetch_etag_accepts_a_single_part_md5(tmp_path: Path) -> None:
    body = b"quad" * 1000
    fetcher = Fetcher(tmp_path, transport=FakeTransport(body))
    result = fetcher.fetch_etag(
        "https://x/q.tif", f'"{hashlib.md5(body).hexdigest()}"', expected_size=len(body)
    )
    assert result.path.read_bytes() == body
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert not result.from_cache


def test_fetch_etag_accepts_a_multipart_etag_and_rechecks_the_cached_copy(
    tmp_path: Path,
) -> None:
    mib = 1024 * 1024
    body = b"t" * (9 * mib + 5)
    etag = _multipart_etag(body, 8 * mib)
    fetcher = Fetcher(tmp_path, transport=FakeTransport(body), max_bytes=1)
    first = fetcher.fetch_etag("https://x/q.tif", etag, expected_size=len(body))
    second = fetcher.fetch_etag("https://x/q.tif", etag, expected_size=len(body))
    assert second.from_cache and second.path == first.path
    # A corrupted cached copy is not trusted for having matched once.
    first.path.write_bytes(b"t" * (len(body) - 1) + b"u")
    third = fetcher.fetch_etag("https://x/q.tif", etag, expected_size=len(body))
    assert not third.from_cache
    assert third.path.read_bytes() == body


def test_fetch_etag_refuses_a_mismatch_and_keeps_nothing(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, transport=FakeTransport(b"evil"))
    with pytest.raises(VerificationError, match="ETag"):
        fetcher.fetch_etag("https://x/q.tif", "0" * 32 + "-2", expected_size=4)
    assert _cache_files(tmp_path) == []


def test_fetch_etag_refuses_the_wrong_size_and_keeps_nothing(tmp_path: Path) -> None:
    body = b"x" * 10
    fetcher = Fetcher(tmp_path, transport=FakeTransport(body))
    with pytest.raises(VerificationError, match="size"):
        fetcher.fetch_etag("https://x/q.tif", hashlib.md5(body).hexdigest(), expected_size=11)
    assert _cache_files(tmp_path) == []


# ---------------------------------------------------------------------------
# Size-checked fetch (D-068, amended 2026-10-01): FSTopo sheets the Forest
# Service publishes no checksum for and Hammunition has not pinned.
# ---------------------------------------------------------------------------

TIFF = b"II*\x00" + b"\x00" * 60


def test_fetch_sized_keeps_a_tiff_of_the_announced_size_and_reports_its_sha256(
    tmp_path: Path,
) -> None:
    fetcher = Fetcher(tmp_path, transport=FakeTransport(TIFF))
    result = fetcher.fetch_sized("https://x/s.tiff", expected_size=len(TIFF))
    assert result.path.read_bytes() == TIFF
    assert result.sha256 == hashlib.sha256(TIFF).hexdigest()
    assert result.path == fetcher.sized_path_for("https://x/s.tiff", len(TIFF))


def test_fetch_sized_never_trusts_a_cached_copy(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, transport=FakeTransport(TIFF))
    path = fetcher.sized_path_for("https://x/s.tiff", len(TIFF))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MM\x00*" + b"\x01" * 60)
    result = fetcher.fetch_sized("https://x/s.tiff", expected_size=len(TIFF))
    assert not result.from_cache and result.path.read_bytes() == TIFF


@pytest.mark.parametrize(
    ("body", "size", "match"),
    [(TIFF, len(TIFF) + 1, "size"), (b"<html>" + b" " * 58, 64, "TIFF")],
)
def test_fetch_sized_refuses_the_wrong_size_or_not_a_tiff_and_keeps_nothing(
    tmp_path: Path, body: bytes, size: int, match: str
) -> None:
    fetcher = Fetcher(tmp_path, transport=FakeTransport(body))
    with pytest.raises(VerificationError, match=match):
        fetcher.fetch_sized("https://x/s.tiff", expected_size=size)
    assert _cache_files(tmp_path) == []


def test_fetch_sized_follows_no_redirect_at_run_time(tmp_path: Path) -> None:
    """Final review I1: the plan checked the gateway's one redirect; a second
    one answered during the run is refused, never followed to another host."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/sheet.tiff":
                self.send_response(302)
                self.send_header("Location", "/evil")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(TIFF)))
            self.end_headers()
            self.wfile.write(TIFF)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/sheet.tiff"
        with pytest.raises(BackendError, match="302"):
            Fetcher(tmp_path).fetch_sized(url, expected_size=len(TIFF))
    finally:
        server.shutdown()
    assert _cache_files(tmp_path) == []
