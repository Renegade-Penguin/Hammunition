# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""ETag and size-only downloads try the Bunker mirror first.  #381, Task 12.

The publisher route's checks are kept exactly: the size, and the S3 ETag for
``fetch_etag``; the size and a TIFF's first bytes for ``fetch_sized``. The
mirror is never trusted more than the publisher: its bytes pass the same
checks, a bad copy falls through to the publisher online and is a hard failure
offline, and no ``*.part.*`` file survives any path.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import http.server
import os
import threading
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import IO

import pytest

from hammunition.backends.base import BackendError
from hammunition.fetch import (
    Fetcher,
    FetchResult,
    MirrorPath,
    UrllibTransport,
    mirror_url,
    redact_url_text,
)
from hammunition.manifest.schema import RemoteArtifact
from test_fetch_mirror import Routes

BODY = b"II*\x00example TIFF"
ETAG = hashlib.md5(BODY, usedforsecurity=False).hexdigest()
PUBLISHER = "https://example.invalid/Test.tif"
BUNKER = "http://bunker.invalid"
PATH = MirrorPath("usgs-ustopo", "DE/Test_20260101")
AT_MIRROR = mirror_url(BUNKER, PATH)
MIB = 1024 * 1024

Fetch = Callable[[Fetcher, bytes], FetchResult]


def _etag(fetcher: Fetcher, body: bytes = BODY) -> FetchResult:
    return fetcher.fetch_etag(PUBLISHER, ETAG, expected_size=len(body), mirror=PATH)


def _sized(fetcher: Fetcher, body: bytes = BODY) -> FetchResult:
    return fetcher.fetch_sized(PUBLISHER, expected_size=len(body), mirror=PATH)


KINDS = pytest.mark.parametrize("fetch", [_etag, _sized], ids=["etag", "sized"])


def _fetcher(tmp_path: Path, routes: Routes, *, offline: bool = False) -> Fetcher:
    return Fetcher(
        tmp_path / "cache",
        transport=routes,
        mirror=BUNKER,
        mirror_transport=routes,
        offline=offline,
    )


def _parts(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "cache").glob("*.part.*"))


@KINDS
def test_mirror_good_and_corrupt_offline(tmp_path: Path, fetch: Fetch) -> None:
    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes, offline=True)
    result = fetch(fetcher, BODY)
    assert result.path.read_bytes() == BODY and result.source == "mirror"
    assert result.url == AT_MIRROR and not result.from_cache
    result.path.unlink()
    routes.routes[AT_MIRROR] = b"x" * len(BODY)
    with pytest.raises(BackendError):
        fetch(fetcher, BODY)
    assert PUBLISHER not in routes.requested
    assert not _parts(tmp_path)


@KINDS
def test_offline_without_a_bunker_route_refuses_and_never_asks_the_publisher(
    tmp_path: Path, fetch: Fetch
) -> None:
    routes = Routes({PUBLISHER: BODY})
    fetcher = Fetcher(tmp_path / "cache", transport=routes, offline=True)
    with pytest.raises(BackendError, match="Bunker route"):
        fetch(fetcher, BODY)
    assert routes.requested == [] and not _parts(tmp_path)


@KINDS
def test_online_good_mirror_is_used_and_the_publisher_never_asked(
    tmp_path: Path, fetch: Fetch
) -> None:
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: b"other"})
    result = fetch(_fetcher(tmp_path, routes), BODY)
    assert result.source == "mirror" and routes.requested == [AT_MIRROR]


@KINDS
def test_online_bad_mirror_falls_back_to_the_publisher(tmp_path: Path, fetch: Fetch) -> None:
    routes = Routes({AT_MIRROR: b"x" * len(BODY), PUBLISHER: BODY})
    result = fetch(_fetcher(tmp_path, routes), BODY)
    assert result.path.read_bytes() == BODY
    assert result.source == "publisher" and result.url == PUBLISHER
    assert result.mirror_failure is not None and AT_MIRROR in result.mirror_failure
    assert routes.requested == [AT_MIRROR, PUBLISHER]
    assert not _parts(tmp_path)


@KINDS
def test_a_missing_mirror_file_falls_back_and_says_why(tmp_path: Path, fetch: Fetch) -> None:
    routes = Routes({PUBLISHER: BODY})
    result = fetch(_fetcher(tmp_path, routes), BODY)
    assert result.source == "publisher" and "404" in (result.mirror_failure or "")


@KINDS
def test_when_both_fail_the_error_names_both_reasons(tmp_path: Path, fetch: Fetch) -> None:
    routes = Routes({AT_MIRROR: b"m" * len(BODY), PUBLISHER: b"p" * len(BODY)})
    with pytest.raises(BackendError) as err:
        fetch(_fetcher(tmp_path, routes), BODY)
    text = str(err.value)
    assert PUBLISHER in text
    assert "LAN mirror was tried first and passed over" in text and AT_MIRROR in text
    assert not _parts(tmp_path)


# -- the checks the publisher route already has, kept -------------------------------


@KINDS
def test_wrong_size_is_refused_on_either_route(tmp_path: Path, fetch: Fetch) -> None:
    short = BODY[:-1]
    routes = Routes({AT_MIRROR: short, PUBLISHER: short})
    with pytest.raises(BackendError, match="the size check failed"):
        fetch(_fetcher(tmp_path, routes), BODY)
    assert routes.requested == [AT_MIRROR, PUBLISHER] and not _parts(tmp_path)
    assert not list((tmp_path / "cache").glob("*Test.tif"))


def test_sized_without_tiff_magic_is_refused_though_the_size_matches(tmp_path: Path) -> None:
    page = b"<html>not a map</html>"
    routes = Routes({AT_MIRROR: page, PUBLISHER: page})
    with pytest.raises(BackendError, match="not a TIFF"):
        _sized(_fetcher(tmp_path, routes), page)
    assert routes.requested == [AT_MIRROR, PUBLISHER] and not _parts(tmp_path)


def test_etag_mismatch_with_the_right_size_is_refused(tmp_path: Path) -> None:
    other = b"x" * len(BODY)
    routes = Routes({AT_MIRROR: other, PUBLISHER: other})
    with pytest.raises(BackendError, match="ETag"):
        _etag(_fetcher(tmp_path, routes), BODY)
    assert not _parts(tmp_path)


def test_a_multipart_etag_is_reproduced_from_the_mirror(tmp_path: Path) -> None:
    body = b"II*\x00" + bytes(range(256)) * (6 * MIB // 256)
    first, second = body[: 5 * MIB], body[5 * MIB :]
    inner = hashlib.md5(usedforsecurity=False)
    for part in (first, second):
        inner.update(hashlib.md5(part, usedforsecurity=False).digest())
    etag = f"{inner.hexdigest()}-2"
    routes = Routes({AT_MIRROR: body})
    result = _fetcher(tmp_path, routes).fetch_etag(
        PUBLISHER, etag, expected_size=len(body), mirror=PATH
    )
    assert result.source == "mirror" and result.size == len(body)
    routes.routes[AT_MIRROR] = body[:-1] + b"?"
    result.path.unlink()
    routes.routes[PUBLISHER] = b"nope"
    with pytest.raises(BackendError, match="ETag"):
        _fetcher(tmp_path, routes).fetch_etag(PUBLISHER, etag, expected_size=len(body), mirror=PATH)
    assert not _parts(tmp_path)


def test_a_verified_cached_etag_copy_is_reverified_and_asks_nobody(tmp_path: Path) -> None:
    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes)
    first = _etag(fetcher)
    assert first.source == "mirror"
    routes.requested.clear()
    again = _etag(fetcher)
    assert again.source == "cache" and again.from_cache and again.url is None
    assert routes.requested == []
    # A damaged cached copy of the right size is not trusted: it is fetched again.
    again.path.write_bytes(b"y" * len(BODY))
    healed = _etag(fetcher)
    assert healed.source == "mirror" and healed.path.read_bytes() == BODY


def test_a_size_only_cached_copy_is_never_reused(tmp_path: Path) -> None:
    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes)
    first = _sized(fetcher)
    routes.requested.clear()
    second = _sized(fetcher)
    assert routes.requested == [AT_MIRROR]
    assert not second.from_cache and second.source == "mirror" and first.path == second.path


# -- the cap, mid-stream -------------------------------------------------------------


class _Endless:
    """Streams more than the cap allows, noting whether a temporary existed
    while it was being read (so the cap is hit mid-stream, not up front)."""

    def __init__(self, cache: Path, body: bytes) -> None:
        self.cache = cache
        self.body = body
        self.requested: list[str] = []
        self.part_seen_mid_stream = False

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:
        self.requested.append(url)
        outer = self

        class Stream(BytesIO):
            def read(self, size: int | None = -1) -> bytes:
                if self.tell() > 0 and list(outer.cache.glob("*.part.*")):
                    outer.part_seen_mid_stream = True
                return super().read(size)

        yield Stream(self.body)


@KINDS
def test_the_cap_hit_mid_stream_leaves_no_part_file(tmp_path: Path, fetch: Fetch) -> None:
    flood = BODY + b"z" * (2 * MIB)
    transport = _Endless(tmp_path / "cache", flood)
    fetcher = Fetcher(
        tmp_path / "cache", transport=transport, mirror=BUNKER, mirror_transport=transport
    )
    with pytest.raises(BackendError, match="byte limit"):
        fetch(fetcher, BODY)
    assert transport.requested == [AT_MIRROR, PUBLISHER]
    assert transport.part_seen_mid_stream
    assert not _parts(tmp_path)


@KINDS
def test_the_cap_hit_by_the_mirror_offline_is_a_hard_failure(tmp_path: Path, fetch: Fetch) -> None:
    transport = _Endless(tmp_path / "cache", BODY + b"z" * (2 * MIB))
    fetcher = Fetcher(
        tmp_path / "cache",
        transport=transport,
        mirror=BUNKER,
        mirror_transport=transport,
        offline=True,
    )
    with pytest.raises(BackendError, match="offline Bunker download failed"):
        fetch(fetcher, BODY)
    assert transport.requested == [AT_MIRROR] and transport.part_seen_mid_stream
    assert not _parts(tmp_path)


# -- size-only publisher requests keep the strict, no-redirect transport -------------

SHEET = "/geodata/Test.tif"
MOVED = "/elsewhere/Test.tif"
ON_MIRROR = "/usgs-ustopo/DE/Test_20260101"


@contextmanager
def _server(files: dict[str, bytes], redirects: dict[str, str]) -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            if self.path in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[self.path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = files.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Quiet."""

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _follows_redirects(transport: object) -> bool:
    handlers = vars(vars(transport)["_opener"])["handlers"]
    return any(isinstance(h, urllib.request.HTTPRedirectHandler) for h in handlers)


def test_strict_transport_is_the_real_one_that_follows_no_redirect(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path / "cache")
    assert isinstance(fetcher.strict_transport, UrllibTransport)
    assert fetcher.strict_transport is not fetcher.transport
    assert not _follows_redirects(fetcher.strict_transport)
    assert _follows_redirects(fetcher.transport)


def test_a_publisher_redirect_on_a_size_only_sheet_is_refused_not_followed(
    tmp_path: Path,
) -> None:
    with _server({MOVED: BODY}, {SHEET: MOVED}) as (base, seen):
        fetcher = Fetcher(
            tmp_path / "cache", mirror=base, mirror_transport=UrllibTransport(timeout=5.0)
        )
        with pytest.raises(BackendError, match="302") as err:
            fetcher.fetch_sized(f"{base}{SHEET}", expected_size=len(BODY), mirror=PATH)
    # The mirror missed (404), the publisher answered a redirect, nothing followed it.
    assert seen == [ON_MIRROR, SHEET] and MOVED not in seen
    assert "LAN mirror was tried first" in str(err.value)
    assert not _parts(tmp_path)
    assert not list((tmp_path / "cache").glob("sized-*"))


def test_the_same_redirect_is_followed_by_the_etag_route_as_before(tmp_path: Path) -> None:
    """The ETag route never had the strict transport; this pins that it still has
    the ordinary one, so the size-only test above is about the size-only route."""
    with _server({MOVED: BODY}, {SHEET: MOVED}) as (base, seen):
        fetcher = Fetcher(
            tmp_path / "cache", mirror=base, mirror_transport=UrllibTransport(timeout=5.0)
        )
        result = fetcher.fetch_etag(f"{base}{SHEET}", ETAG, expected_size=len(BODY), mirror=PATH)
    assert result.source == "publisher" and MOVED in seen


def test_a_size_only_mirror_hit_over_real_http_is_checked_like_the_publisher(
    tmp_path: Path,
) -> None:
    with _server({ON_MIRROR: BODY}, {}) as (base, seen):
        fetcher = Fetcher(
            tmp_path / "cache", mirror=base, mirror_transport=UrllibTransport(timeout=5.0)
        )
        result = fetcher.fetch_sized(f"{base}{SHEET}", expected_size=len(BODY), mirror=PATH)
    assert result.source == "mirror" and seen == [ON_MIRROR]


# -- the fetcher's path identity is the catalogue identity the offline plan resolves by --


def _identity_fetcher(tmp_path: Path, routes: Routes) -> Fetcher:
    return Fetcher(
        tmp_path / "cache", transport=routes, mirror=BUNKER, mirror_transport=routes, offline=True
    )


def test_a_us_topo_sheet_is_fetched_under_the_identity_the_offline_plan_resolves_by(
    tmp_path: Path,
) -> None:
    import test_offline_usgs as usgs
    import test_topo_backend as topo
    from bunker_fixtures import make_context
    from hammunition.backends.topo import TopoQuadsBackend, TopoResolution
    from hammunition.ustopo import check_quad
    from test_terrain_plan import TileProbe

    q = usgs.quad()
    context = make_context(tmp_path / "ctx", [usgs.quad_row(q)])
    check_quad(q, TileProbe({}), context=context, unit=usgs.TOPO)
    ((unit, name),) = [n for n in context.notes if n[0] == usgs.TOPO]
    assert (unit, name) == ("usgs-ustopo", "DE/Test_20260101")
    routes = Routes({})
    backend = TopoQuadsBackend(
        fetcher=_identity_fetcher(tmp_path, routes),
        prefix=tmp_path,
        resolution=TopoResolution(fetch=(q,)),
    )
    m = topo.manifest()
    next(s for s in topo._actions(backend.steps(m, topo._block(m))) if s.kind == "fetch").perform()
    assert routes.requested == [mirror_url(BUNKER, MirrorPath(unit, name))]


def test_an_fstopo_sheet_is_fetched_under_the_identity_the_offline_plan_resolves_by(
    tmp_path: Path,
) -> None:
    import test_fstopo_backend as fstopo
    import test_offline_fstopo as offline
    from bunker_fixtures import make_context
    from hammunition.backends.fstopo import FsTopoResolution
    from hammunition.fstopo import recorded_sheet

    q = offline.sheet()
    context = make_context(tmp_path / "ctx", [offline.row(q)])
    got = recorded_sheet(q, unit=offline.UNIT, pins={}, context=context)
    assert got.sha256 is None
    routes = Routes({})
    backend = fstopo._pair(
        tmp_path,
        FsTopoResolution(fetch=(got,)),
        fetcher=_identity_fetcher(tmp_path, routes),
    )
    next(s for s in fstopo._steps(backend) if s.kind == "fetch").perform()
    assert routes.requested == [mirror_url(BUNKER, MirrorPath(offline.UNIT, q.name))]
    assert context.entry(offline.UNIT, q.name).name == q.name


def test_a_3dep_tile_is_fetched_under_the_identity_the_offline_plan_resolves_by(
    tmp_path: Path,
) -> None:
    import test_dem_3dep as dem3
    import test_offline_usgs as usgs
    from bunker_fixtures import make_context
    from hammunition.backends.dem import DemResolution, DemTilesBackend
    from hammunition.copernicus import TileFile
    from hammunition.usgs3dep import check_tile, tile_url
    from test_terrain_plan import TileProbe

    t = usgs.tile()
    context = make_context(tmp_path / "ctx", [usgs.tile_row(t)])
    check_tile(t, TileProbe({}), context=context, unit=usgs.DEM)
    ((unit, name),) = [n for n in context.notes if n[0] == usgs.DEM]
    assert (unit, name) == ("dem-3dep", t.name)
    routes = Routes({})
    three = DemTilesBackend(
        fetcher=_identity_fetcher(tmp_path, routes),
        prefix=tmp_path,
        resolution=DemResolution(
            fetch=(TileFile(t.name, tile_url(t.name), t.size, None, None, t.etag),)
        ),
        provider="usgs-3dep",
    )
    m = dem3.manifest("dem-3dep", "usgs-3dep")
    outer = DemTilesBackend(
        fetcher=three.fetcher, prefix=tmp_path, resolution=DemResolution(), bare_earth=three
    )
    next(s for s in dem3._steps(outer, m) if s.kind == "fetch").perform()
    assert routes.requested == [mirror_url(BUNKER, MirrorPath(unit, name))]


# -- fix round 2 ----------------------------------------------------------------------

LEAK = "/elsewhere/leak.tif"


@KINDS
@pytest.mark.parametrize("offline", [True, False], ids=["offline", "online"])
@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit-following"])
def test_a_mirror_redirect_is_a_mirror_failure_never_followed(
    tmp_path: Path, fetch: Fetch, offline: bool, explicit: bool
) -> None:
    with _server({LEAK: BODY}, {ON_MIRROR: LEAK}) as (base, seen):
        following = UrllibTransport(timeout=5.0) if explicit else None
        fetcher = Fetcher(
            tmp_path / "cache", mirror=base, offline=offline, mirror_transport=following
        )
        # The publisher is the sheet's own URL on the same server, which has no such file.
        with pytest.raises(BackendError) as err:
            if fetch is _etag:
                fetcher.fetch_etag(f"{base}{SHEET}", ETAG, expected_size=len(BODY), mirror=PATH)
            else:
                fetcher.fetch_sized(f"{base}{SHEET}", expected_size=len(BODY), mirror=PATH)
    assert LEAK not in seen
    assert "302" in str(err.value)
    if offline:
        assert seen == [ON_MIRROR] and "offline Bunker download failed" in str(err.value)
    else:
        assert seen == [ON_MIRROR, SHEET]
        assert "LAN mirror was tried first and passed over" in str(err.value)
        assert "404" in str(err.value)
    assert not _parts(tmp_path) and not list((tmp_path / "cache").glob("*Test*"))


def test_the_default_mirror_transport_follows_no_redirect(tmp_path: Path) -> None:
    assert not _follows_redirects(Fetcher(tmp_path / "a", mirror=BUNKER).mirror_transport)
    given = UrllibTransport(timeout=7.0)
    normalised = Fetcher(tmp_path / "b", mirror=BUNKER, mirror_transport=given).mirror_transport
    assert not _follows_redirects(normalised)
    assert isinstance(normalised, UrllibTransport) and normalised.timeout == 7.0


class _ShortRead(Exception):
    """Stands in for http.client.IncompleteRead below."""


@KINDS
@pytest.mark.parametrize(
    "boom",
    [
        OSError("connection reset"),
        http.client.IncompleteRead(b"II", 9),
        TimeoutError("timed out"),
    ],
    ids=["oserror", "incompleteread", "timeout"],
)
def test_a_publisher_failure_after_a_mirror_failure_names_both_and_keeps_the_cause(
    tmp_path: Path, fetch: Fetch, boom: Exception
) -> None:
    routes = Routes({AT_MIRROR: b"x" * len(BODY), PUBLISHER: boom})
    with pytest.raises(BackendError) as err:
        fetch(_fetcher(tmp_path, routes), BODY)
    text = str(err.value)
    assert PUBLISHER in text and AT_MIRROR in text and "passed over" in text
    assert type(boom).__name__ in text
    assert err.value.__cause__ is boom
    assert not _parts(tmp_path)


@dataclass(frozen=True)
class RouteCase:
    """One of the four routes that reuse a cached file."""

    name: str
    final: Callable[[Fetcher], Path]
    run: Callable[[Fetcher], FetchResult]


_ARTIFACT = RemoteArtifact(url=PUBLISHER, sha256=hashlib.sha256(BODY).hexdigest())
_MD5 = ETAG
_SHA1 = hashlib.sha1(
    BODY, usedforsecurity=False
).hexdigest()  # nosemgrep: insecure-hash-algorithm-sha1

ROUTES = [
    RouteCase(
        "sha256",
        lambda f: f.path_for(_ARTIFACT),
        lambda f: f.fetch(_ARTIFACT, mirror=PATH),
    ),
    RouteCase(
        "md5",
        lambda f: f.md5_path_for(PUBLISHER, _MD5),
        lambda f: f.fetch_md5(PUBLISHER, _MD5, expected_size=len(BODY), mirror=PATH),
    ),
    RouteCase(
        "sha1",
        lambda f: f.sha1_path_for(PUBLISHER, _SHA1),
        lambda f: f.fetch_sha1(PUBLISHER, _SHA1, expected_size=len(BODY), mirror=PATH),
    ),
    RouteCase(
        "etag",
        lambda f: f.etag_path_for(PUBLISHER, ETAG),
        lambda f: f.fetch_etag(PUBLISHER, ETAG, expected_size=len(BODY), mirror=PATH),
    ),
]
ALL_ROUTES = pytest.mark.parametrize("case", ROUTES, ids=[c.name for c in ROUTES])


def _cached_entry(
    tmp_path: Path, case: RouteCase, body: bytes = BODY
) -> tuple[Fetcher, Routes, Path]:
    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes)
    cached = case.final(fetcher)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(body)
    return fetcher, routes, cached


def _assert_consistent(result: FetchResult) -> None:
    """The path, its bytes and the digest are one verified thing."""
    data = result.path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == result.sha256
    assert data == BODY


@ALL_ROUTES
def test_a_good_cached_copy_is_reused_and_is_our_own_verified_inode(
    tmp_path: Path, case: RouteCase
) -> None:
    fetcher, routes, cached = _cached_entry(tmp_path, case)
    before = cached.stat().st_ino
    result = case.run(fetcher)
    assert result.source == "cache" and result.from_cache and routes.requested == []
    _assert_consistent(result)
    assert result.path.stat().st_ino != before  # the verified copy, published over the path
    assert not _parts(tmp_path)


@ALL_ROUTES
def test_a_wrong_cached_copy_of_the_right_size_is_replaced(tmp_path: Path, case: RouteCase) -> None:
    fetcher, routes, _cached = _cached_entry(tmp_path, case, b"B" * len(BODY))
    result = case.run(fetcher)
    assert result.source == "mirror" and routes.requested == [AT_MIRROR]
    _assert_consistent(result)


@ALL_ROUTES
def test_a_rename_replacement_after_the_copy_cannot_change_what_is_returned(
    tmp_path: Path, case: RouteCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher, _routes, cached = _cached_entry(tmp_path, case)
    real_fsync = os.fsync
    done: list[int] = []

    def swapping(fd: int) -> None:
        real_fsync(fd)
        if not done and ".part." in os.readlink(f"/proc/self/fd/{fd}"):
            done.append(1)  # the copy is complete; a new inode now sits at the path
            other = cached.with_name("theirs")
            other.write_bytes(b"B" * len(BODY))
            os.replace(other, cached)

    monkeypatch.setattr(os, "fsync", swapping)
    result = case.run(fetcher)
    assert done and result.source == "cache"
    _assert_consistent(result)


@ALL_ROUTES
def test_a_timed_b_a_b_in_place_swap_cannot_be_accepted_as_something_it_is_not(
    tmp_path: Path, case: RouteCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = b"B" * len(BODY)
    fetcher, _routes, cached = _cached_entry(tmp_path, case, bad)
    real_fstat, real_fsync = os.fstat, os.fsync
    steps: list[str] = []

    def measuring(fd: int) -> os.stat_result:
        status = real_fstat(fd)
        if "measured" not in steps and os.readlink(f"/proc/self/fd/{fd}") == str(cached):
            steps.append("measured")
            cached.write_bytes(BODY)  # A while it is copied ...
        return status

    def flushing(fd: int) -> None:
        real_fsync(fd)
        if "flushed" not in steps and ".part." in os.readlink(f"/proc/self/fd/{fd}"):
            steps.append("flushed")
            cached.write_bytes(bad)  # ... B again once the copy is made

    monkeypatch.setattr(os, "fstat", measuring)
    monkeypatch.setattr(os, "fsync", flushing)
    result = case.run(fetcher)
    assert steps == ["measured", "flushed"]
    _assert_consistent(result)  # what was verified is what is returned, never the B on disk
    assert result.path.read_bytes() == BODY


@ALL_ROUTES
def test_a_file_that_grows_after_it_was_measured_is_not_a_cache_hit(
    tmp_path: Path, case: RouteCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher, _routes, cached = _cached_entry(tmp_path, case)
    real_fstat = os.fstat
    grown: list[int] = []

    def growing(fd: int) -> os.stat_result:
        status = real_fstat(fd)
        if not grown and os.readlink(f"/proc/self/fd/{fd}") == str(cached):
            grown.append(1)
            with cached.open("ab") as handle:
                handle.write(b"!")
        return status

    monkeypatch.setattr(os, "fstat", growing)
    result = case.run(fetcher)
    assert grown and result.source == "mirror"
    assert result.path.read_bytes() == BODY and result.size == len(BODY)


@ALL_ROUTES
def test_a_symlink_at_the_final_path_is_not_a_cache_hit(tmp_path: Path, case: RouteCase) -> None:
    fetcher, _routes, cached = _cached_entry(tmp_path, case)
    target = tmp_path / "elsewhere.tif"
    cached.rename(target)
    cached.symlink_to(target)
    result = case.run(fetcher)
    assert result.source == "mirror" and not cached.is_symlink()
    assert target.read_bytes() == BODY  # the link's target was neither used nor removed
    _assert_consistent(result)


@ALL_ROUTES
def test_a_fifo_at_the_final_path_fails_fast_and_does_not_hang(
    tmp_path: Path, case: RouteCase
) -> None:
    import threading

    fetcher, _routes, cached = _cached_entry(tmp_path, case)
    cached.unlink()
    os.mkfifo(cached)
    outcome: list[FetchResult | BaseException] = []

    def attempt() -> None:
        try:
            outcome.append(case.run(fetcher))
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()
    thread.join(timeout=10)
    hung = thread.is_alive()
    if hung:  # release a reader stuck in open() so the test process can end
        with contextlib.suppress(OSError):
            os.close(os.open(cached, os.O_WRONLY | os.O_NONBLOCK))
        thread.join(timeout=2)
    assert not hung, "the cache check blocked on a FIFO"
    (result,) = outcome
    assert isinstance(result, FetchResult) and result.source == "mirror"
    _assert_consistent(result)


def test_a_cached_file_over_the_fetch_cap_is_not_copied_or_served() -> None:
    """The sha256 route knows no size, so the cap bounds what a cache hit may copy."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fetcher, _routes, cached = _cached_entry(tmp_path, ROUTES[0])
        with pytest.raises(BackendError, match="byte limit"):
            fetcher.fetch(_ARTIFACT, max_bytes=len(BODY) - 1, mirror=PATH)
        assert not cached.exists() and not _parts(tmp_path)


def test_the_etag_swap_between_check_and_digest_cannot_return_other_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hammunition.s3etag import etag_matches as real

    case = ROUTES[3]
    fetcher, _routes, cached = _cached_entry(tmp_path, case, b"B" * len(BODY))
    done: list[int] = []

    def timed(path: Path, etag: str) -> bool:
        if done:
            return real(path, etag)
        done.append(1)
        cached.write_bytes(BODY)  # A while the ETag is read ...
        ok = real(path, etag)
        cached.write_bytes(b"B" * len(BODY))  # ... B again for everything else
        return ok

    monkeypatch.setattr("hammunition.fetch.etag_matches", timed)
    result = case.run(fetcher)
    assert result.source == "mirror"
    _assert_consistent(result)


def test_a_wrong_sized_cache_entry_is_removed_even_when_recovery_fails(tmp_path: Path) -> None:
    routes = Routes({})
    fetcher = _fetcher(tmp_path, routes, offline=True)
    poisoned = fetcher.etag_path_for(PUBLISHER, ETAG)
    poisoned.parent.mkdir(parents=True)
    poisoned.write_bytes(BODY + b"!")
    with pytest.raises(BackendError):
        _etag(fetcher)
    assert not poisoned.exists() and not _parts(tmp_path)


def test_a_cache_entry_that_cannot_be_read_is_removed_even_when_recovery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(path: Path, etag: str) -> bool:
        raise OSError("input/output error")

    routes = Routes({})
    fetcher = _fetcher(tmp_path, routes, offline=True)
    poisoned = fetcher.etag_path_for(PUBLISHER, ETAG)
    poisoned.parent.mkdir(parents=True)
    poisoned.write_bytes(BODY)
    monkeypatch.setattr("hammunition.fetch.etag_matches", unreadable)
    with pytest.raises(BackendError):
        _etag(fetcher)
    assert not poisoned.exists() and not _parts(tmp_path)


@KINDS
def test_a_failed_rename_removes_the_part_file(
    tmp_path: Path, fetch: Fetch, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(src: object, dst: object) -> None:
        raise PermissionError("rename refused")

    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes)
    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(PermissionError):
        fetch(fetcher, BODY)
    assert not _parts(tmp_path) and not list((tmp_path / "cache").glob("*Test*"))


SECRET_BUNKER = "http://alice:SECRET@bunker.invalid"
AT_SECRET = mirror_url(SECRET_BUNKER, PATH)
#: What the fetcher actually asks, and shows: the same URL without the credentials.
AT_CLEAN = redact_url_text(AT_SECRET)


@KINDS
def test_mirror_credentials_never_reach_errors_facts_or_the_plan(
    tmp_path: Path, fetch: Fetch
) -> None:
    from hammunition.fetch import TransportUnreachable, fetch_disclosure, record_fetch

    assert "SECRET" in AT_SECRET
    offline = Fetcher(
        tmp_path / "off",
        transport=Routes({}),
        mirror=SECRET_BUNKER,
        mirror_transport=Routes({}),
        offline=True,
    )
    with pytest.raises(BackendError) as err:
        fetch(offline, BODY)
    assert "SECRET" not in str(err.value) and "bunker.invalid" in str(err.value)
    down = Routes(
        {AT_CLEAN: TransportUnreachable(f"{AT_SECRET} could not be fetched"), PUBLISHER: BODY}
    )
    online = Fetcher(tmp_path / "on", transport=down, mirror=SECRET_BUNKER, mirror_transport=down)
    result = fetch(online, BODY)
    facts: dict[str, str] = {}
    words = record_fetch(result, facts, mirrored=True)
    assert result.source == "publisher" and "SECRET" not in words
    assert "SECRET" not in repr(facts) and "bunker.invalid" in facts["mirror_failure"]
    # The "mirror did not answer earlier in this run" note on a second fetch.
    dead = Fetcher(
        tmp_path / "off2",
        transport=Routes({}),
        mirror=SECRET_BUNKER,
        mirror_transport=down,
        offline=True,
    )
    for _ in range(2):
        with pytest.raises(BackendError) as later:
            fetch(dead, BODY)
        assert "SECRET" not in str(later.value)
    assert "did not answer earlier" in str(later.value)
    note, detail, sources = fetch_disclosure(online, PUBLISHER, PATH, "ETag")
    assert "SECRET" not in repr((note, detail, sources)) and "bunker.invalid" in detail


# -- fix round 3: temporary ownership --------------------------------------------------


@KINDS
def test_a_replaced_temporary_is_not_deleted_when_the_rename_fails(
    tmp_path: Path, fetch: Fetch, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes)

    def hostile(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        theirs = Path(src).with_name("theirs")
        theirs.write_bytes(b"another process's file")
        real_replace(theirs, src)  # their file now holds our temporary's name
        raise PermissionError("rename refused")

    monkeypatch.setattr(os, "replace", hostile)
    with pytest.raises(PermissionError):
        fetch(fetcher, BODY)
    (survivor,) = _parts(tmp_path)
    assert survivor.read_bytes() == b"another process's file"


@KINDS
def test_a_temporary_that_exclusive_creation_refused_is_never_deleted(
    tmp_path: Path, fetch: Fetch, monkeypatch: pytest.MonkeyPatch
) -> None:
    import secrets

    monkeypatch.setattr(secrets, "token_hex", lambda nbytes=None: "0" * 16)
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: BODY})
    fetcher = _fetcher(tmp_path, routes)
    final = (
        fetcher.etag_path_for(PUBLISHER, ETAG)
        if fetch is _etag
        else fetcher.sized_path_for(PUBLISHER, len(BODY))
    )
    final.parent.mkdir(parents=True)
    pre_existing = [
        final.with_name(f"{final.name}.part.{os.getpid()}.{'0' * 16}"),
        final.with_name(f"{final.name}.part.{os.getpid()}"),  # the old fixed name
    ]
    for planted in pre_existing:
        planted.write_bytes(b"somebody else's")
    with pytest.raises(BackendError, match="cannot create the download temporary"):
        fetch(fetcher, BODY)
    assert all(p.read_bytes() == b"somebody else's" for p in pre_existing)
    assert not final.exists()


def test_two_fetches_of_one_file_use_different_temporaries(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path, Routes({}))
    final = fetcher.etag_path_for(PUBLISHER, ETAG)
    assert fetcher._temporary_for(final) != fetcher._temporary_for(final)


# -- fix round 3: redaction --------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "shown"),
    [
        ("http://alice:SECRET@bunker.invalid/base", "http://bunker.invalid/base"),
        ("http://alice:pa@SECRET@bunker.invalid/base", "http://bunker.invalid/base"),
        ("http://alice:SECRET@[::1]:8080/x", "http://[::1]:8080/x"),
        ("http://bunker.invalid/base//part@file", "http://bunker.invalid/base//part@file"),
        ("http://bunker.invalid/a@b?c=d@e", "http://bunker.invalid/a@b?c=d@e"),
        ("see https://u:p@h/x: boom (http://v@w)", "see https://h/x: boom (http://w)"),
        ("no url here, user@host stays", "no url here, user@host stays"),
        ("http://alice:SECRET@b\u00fccher.example/x", "http://b\u00fccher.example/x"),
        ("http://alice:SECRET@xn--a-.example/x", "http://xn--a-.example/x"),
        ("http://alice:SECRET@bun ker/x", "http://bun ker/x"),
        # Failing closed: urlsplit refuses it, or it does not round-trip, or it
        # carries a control character, so the whole authority goes.
        ("http://alice:SECRET@bunker\u2100.invalid/x", "http://<redacted>/x"),
        ("http://alice:SECRET@[::1/x", "http://<redacted>/x"),
        ("http://alice:SECRET@bun\x00ker/x", "http://<redacted>/x"),
        ("http://alice:SECRET@bun\x7fker:80/x", "http://<redacted>/x"),
        ("https://alice:SECRET@[fe80::1%25eth0]x/y", "https://<redacted>/y"),
    ],
)
def test_redaction_follows_urlsplit_userinfo_rules(given: str, shown: str) -> None:
    assert redact_url_text(given) == shown


def _chain_text(exc: BaseException | None) -> str:
    out: list[str] = []
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        out.append(str(current))
        pending += [current.__cause__, current.__context__]
    return "\n".join(out)


def _chain(depth: int, secret_at: int) -> Exception:
    """A cause chain *depth* long whose link number *secret_at* names the secret."""
    link: Exception = OSError("end of the chain")
    for number in range(depth):
        outer = OSError(f"link {number}" + (f" for {AT_SECRET}" if number == secret_at else ""))
        outer.__cause__ = link
        link = outer
    return link


@KINDS
@pytest.mark.parametrize("depth", [2, 40], ids=["short", "deeper-than-any-limit"])
def test_a_credential_anywhere_in_an_exception_chain_does_not_survive(
    tmp_path: Path, fetch: Fetch, depth: int
) -> None:
    boom = _chain(depth, secret_at=0)  # the credential sits at the far end
    routes = Routes({AT_CLEAN: b"x" * len(BODY), PUBLISHER: boom})
    fetcher = Fetcher(
        tmp_path / "cache", transport=routes, mirror=SECRET_BUNKER, mirror_transport=routes
    )
    with pytest.raises(BackendError) as err:
        fetch(fetcher, BODY)
    assert "SECRET" not in _chain_text(err.value)
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert "link" in str(err.value)


@KINDS
def test_a_clean_chain_of_any_depth_is_kept_as_the_cause(tmp_path: Path, fetch: Fetch) -> None:
    boom = _chain(40, secret_at=-1)
    routes = Routes({AT_CLEAN: b"x" * len(BODY), PUBLISHER: boom})
    fetcher = Fetcher(
        tmp_path / "cache", transport=routes, mirror=SECRET_BUNKER, mirror_transport=routes
    )
    with pytest.raises(BackendError) as err:
        fetch(fetcher, BODY)
    assert err.value.__cause__ is boom


@KINDS
def test_a_cyclic_exception_chain_terminates_and_is_sanitised(tmp_path: Path, fetch: Fetch) -> None:
    first, second = OSError(f"first {AT_SECRET}"), OSError("second")
    first.__cause__, second.__context__ = second, first
    routes = Routes({AT_CLEAN: b"x" * len(BODY), PUBLISHER: second})
    fetcher = Fetcher(
        tmp_path / "cache", transport=routes, mirror=SECRET_BUNKER, mirror_transport=routes
    )
    with pytest.raises(BackendError) as err:
        fetch(fetcher, BODY)
    assert "SECRET" not in _chain_text(err.value) and err.value.__cause__ is None


@KINDS
def test_a_failing_cleanup_is_sanitised_and_does_not_chain_the_raw_error(
    tmp_path: Path, fetch: Fetch, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unlink_fails(self: Fetcher, temporary: Path) -> None:
        raise PermissionError(f"cannot remove {AT_SECRET}") from RuntimeError(AT_SECRET)

    routes = Routes({})
    fetcher = Fetcher(
        tmp_path / "cache", transport=routes, mirror=SECRET_BUNKER, mirror_transport=routes
    )
    monkeypatch.setattr(Fetcher, "_discard", unlink_fails)
    with pytest.raises(BackendError) as err:
        fetch(fetcher, BODY)
    assert "SECRET" not in _chain_text(err.value)
    assert "also failed" in str(err.value) and "bunker.invalid" in str(err.value)
    assert err.value.__cause__ is None or "SECRET" not in _chain_text(err.value.__cause__)
    assert err.value.__context__ is None


@KINDS
def test_sources_and_results_carry_no_credentials(tmp_path: Path, fetch: Fetch) -> None:
    routes = Routes({AT_CLEAN: BODY})
    fetcher = Fetcher(
        tmp_path / "cache", transport=routes, mirror=SECRET_BUNKER, mirror_transport=routes
    )
    assert "SECRET" not in repr(fetcher.sources_for(PUBLISHER, PATH))
    result = fetch(fetcher, BODY)
    assert result.source == "mirror" and result.url == AT_CLEAN
    assert "SECRET" not in repr(result)


def test_a_station_refusal_does_not_echo_a_credential() -> None:
    from hammunition.station import Station, StationError, _check_mirror

    for given in (
        "http://alice:SECRET@bunker.invalid",
        "http://alice:pa@SECRET@bunker.invalid/base",
        "ftp://alice:SECRET@bunker.invalid",
        "http://alice:SECRET@bunker.invalid:notaport",
    ):
        with pytest.raises(StationError) as err:
            _check_mirror(given)
        assert "SECRET" not in _chain_text(err.value)
        assert "bunker.invalid" in str(err.value) or "<redacted>" in str(err.value)
        with pytest.raises(StationError) as err2:
            Station(mirror=given)
        assert "SECRET" not in str(err2.value)


def test_a_space_inside_userinfo_does_not_leave_a_tail_unredacted() -> None:
    """``redact_url_text``'s authority match stops at the first whitespace, so
    it never saw the literal, un-encoded space some operators type inside a
    username -- ``urlsplit`` itself keeps a raw space inside ``netloc``
    (confirmed: it is not a delimiter), so the real ``@`` and the credential
    after it survived past the narrow match, unredacted, in the station
    refusal's echoed value (#381, Task 19 follow-up)."""
    from hammunition.station import Station, StationError, _check_mirror

    given = "http://ali ce:SECRET@bunker.invalid/base"
    for attempt in (lambda: _check_mirror(given), lambda: Station(mirror=given)):
        with pytest.raises(StationError) as err:
            attempt()
        assert "SECRET" not in _chain_text(err.value)
        assert "bunker.invalid" in str(err.value)


@pytest.mark.parametrize(
    "given",
    [
        "user:SECRET@bunker.lan:8080",  # no scheme at all
        "http:/user:SECRET@bunker.lan/",  # one slash: the scheme regex doesn't match
    ],
)
def test_redact_mirror_url_fails_closed_with_no_recognised_scheme(given: str) -> None:
    """A string with no ``scheme://`` prefix is not known to carry no
    credential: ``_check_mirror`` rejects both of these for a different
    reason (not a valid URL shape) and echoes the value back through
    ``redact_mirror_url`` first, which used to return it completely
    unchanged -- the credential survived into the refusal, and from there
    into the teed run log (an Opus adversarial review of the whole branch,
    #381)."""
    from hammunition.urlredact import REDACTED, redact_mirror_url

    assert redact_mirror_url(given) == REDACTED


def test_redact_mirror_url_passes_through_a_credential_free_non_url() -> None:
    from hammunition.urlredact import redact_mirror_url

    assert redact_mirror_url("not a url at all") == "not a url at all"


@pytest.mark.parametrize(
    "given",
    [
        "http://alice:SECRET@bunker\u2100.invalid",  # NFKC makes urlsplit refuse it
        "http://alice:SECRET@[::1",
        "http://alice:SECRET@bunker\x00.invalid",
    ],
)
def test_a_malformed_host_fails_closed_in_the_station_refusal(given: str) -> None:
    from hammunition.station import Station, StationError, _check_mirror

    for attempt in (lambda: _check_mirror(given), lambda: Station(mirror=given)):
        with pytest.raises(StationError) as err:
            attempt()
        assert "SECRET" not in _chain_text(err.value) and "alice" not in _chain_text(err.value)
        assert err.value.__cause__ is None and err.value.__context__ is None


def test_an_authority_urlsplit_changes_does_not_round_trip_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.parse

    real = urllib.parse.urlsplit

    def altering(url: str) -> urllib.parse.SplitResult:
        parts = real(url)
        return parts._replace(netloc=parts.netloc.replace("bun", "BUN"))

    monkeypatch.setattr(urllib.parse, "urlsplit", altering)
    assert redact_url_text("http://alice:SECRET@bunker/x") == "http://<redacted>/x"


@KINDS
def test_a_clean_cyclic_exception_chain_terminates(tmp_path: Path, fetch: Fetch) -> None:
    first, second = OSError("first"), OSError("second")
    first.__cause__, second.__cause__ = second, first
    routes = Routes({AT_CLEAN: b"x" * len(BODY), PUBLISHER: first})
    fetcher = Fetcher(
        tmp_path / "cache", transport=routes, mirror=SECRET_BUNKER, mirror_transport=routes
    )
    with pytest.raises(BackendError) as err:
        fetch(fetcher, BODY)
    assert err.value.__cause__ is first
