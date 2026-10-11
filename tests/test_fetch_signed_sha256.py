# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bunker bytes are also checked against the signed catalogue's sha256.  #381, Task 13.

A repository pin that is not a sha256 (an md5, a SHA-1, an ETag, a size, or
nothing) lets bytes through that the signed catalogue says are not the ones the
Bunker holds. For those routes the mirror's bytes (and a cached copy) must also
match the catalogue row's sha256, when the row has one. It is in addition to the
publisher check, never instead of it, and a sha256-pinned route is untouched.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends.base import BackendError
from hammunition.fetch import Fetcher, FetchResult, MirrorPath, mirror_url
from hammunition.resolution import MalformedCatalogueRow
from test_fetch_mirror import Routes

BODY = b"II*\x00pinned by a weak digest"
OTHER = b"II*\x00what the signed catalogue says it holds"
MD5 = hashlib.md5(BODY, usedforsecurity=False).hexdigest()
SHA1 = hashlib.sha1(BODY, usedforsecurity=False).hexdigest()
SHA256 = hashlib.sha256(BODY).hexdigest()
SIGNED = hashlib.sha256(OTHER).hexdigest()
PUBLISHER = "https://example.invalid/Test.tif"
BUNKER = "http://bunker.invalid"
PATH = MirrorPath("unit", "name/Test.tif")
AT_MIRROR = mirror_url(BUNKER, PATH)


def _md5(fetcher: Fetcher) -> FetchResult:
    return fetcher.fetch_md5(PUBLISHER, MD5, expected_size=len(BODY), mirror=PATH)


def _sha1(fetcher: Fetcher) -> FetchResult:
    return fetcher.fetch_sha1(PUBLISHER, SHA1, expected_size=len(BODY), mirror=PATH)


def _etag(fetcher: Fetcher) -> FetchResult:
    return fetcher.fetch_etag(PUBLISHER, MD5, expected_size=len(BODY), mirror=PATH)


def _sized(fetcher: Fetcher) -> FetchResult:
    return fetcher.fetch_sized(PUBLISHER, expected_size=len(BODY), mirror=PATH)


def _checked(fetcher: Fetcher) -> FetchResult:
    return fetcher.fetch_checked(PUBLISHER, max_bytes=1024, check=lambda path: None, mirror=PATH)


ROUTES = pytest.mark.parametrize(
    "fetch", [_md5, _sha1, _etag, _sized, _checked], ids=["md5", "sha1", "etag", "sized", "checked"]
)
Fetch = Callable[[Fetcher], FetchResult]


def _fetcher(
    tmp_path: Path,
    routes: Routes,
    signed: Callable[[MirrorPath], str | None] | None,
    *,
    offline: bool,
    bunker: str | None = "bunker",
) -> Fetcher:
    return Fetcher(
        tmp_path / "cache",
        transport=routes,
        mirror=BUNKER,
        mirror_transport=routes,
        offline=offline,
        signed_sha256=signed,
        bunker=bunker,
    )


def _parts(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "cache").glob("*.part.*"))


@ROUTES
def test_offline_mirror_bytes_failing_the_signed_sha256_are_refused_naming_both(
    tmp_path: Path, fetch: Fetch
) -> None:
    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes, lambda path: SIGNED, offline=True)
    with pytest.raises(BackendError) as caught:
        fetch(fetcher)
    text = str(caught.value)
    assert SIGNED in text and SHA256 in text
    assert routes.requested == [AT_MIRROR]
    assert _parts(tmp_path) == []
    assert not [p for p in (tmp_path / "cache").iterdir()]


@ROUTES
def test_online_a_signed_mismatch_falls_back_to_the_publisher_naming_the_reason(
    tmp_path: Path, fetch: Fetch
) -> None:
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: BODY})
    fetcher = _fetcher(tmp_path, routes, lambda path: SIGNED, offline=False)
    result = fetch(fetcher)
    assert result.source == "publisher"
    assert result.mirror_failure is not None
    assert SIGNED in result.mirror_failure and SHA256 in result.mirror_failure
    assert routes.requested == [AT_MIRROR, PUBLISHER]


@ROUTES
def test_online_the_publisher_is_still_checked_when_both_fail(tmp_path: Path, fetch: Fetch) -> None:
    # In addition to the publisher's own check, never instead of it.
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: b"x" * len(BODY)})
    fetcher = _fetcher(tmp_path, routes, lambda path: SIGNED, offline=False)
    with pytest.raises(BackendError):
        if fetch is _checked:
            fetcher.fetch_checked(PUBLISHER, max_bytes=1024, check=_reject, mirror=PATH)
        else:
            fetch(fetcher)


def _reject(path: Path) -> None:
    raise BackendError("the structure check failed")


@ROUTES
def test_a_matching_signed_sha256_passes_and_is_asked_for_this_path(
    tmp_path: Path, fetch: Fetch
) -> None:
    asked: list[MirrorPath] = []

    def signed(path: MirrorPath) -> str | None:
        asked.append(path)
        return SHA256

    routes = Routes({AT_MIRROR: BODY})
    result = fetch(_fetcher(tmp_path, routes, signed, offline=True))
    assert result.source == "mirror" and result.sha256 == SHA256
    assert asked and set(asked) == {PATH}


@ROUTES
def test_no_signed_row_means_the_publisher_pin_alone_decides(tmp_path: Path, fetch: Fetch) -> None:
    routes = Routes({AT_MIRROR: BODY})
    result = fetch(_fetcher(tmp_path, routes, lambda path: None, offline=True))
    assert result.source == "mirror"


def test_a_sha256_pinned_route_does_not_consult_the_signed_row(tmp_path: Path) -> None:
    from hammunition.manifest.schema import RemoteArtifact

    def never(path: MirrorPath) -> str | None:
        pytest.fail("a sha256 pin was checked against the signed row as well")

    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes, never, offline=True)
    result = fetcher.fetch(RemoteArtifact(url=PUBLISHER, sha256=SHA256), mirror=PATH)
    assert result.source == "mirror"


@pytest.mark.parametrize("fetch", [_md5, _sha1, _etag], ids=["md5", "sha1", "etag"])
def test_a_cached_copy_that_fails_the_signed_sha256_is_dropped_not_served(
    tmp_path: Path, fetch: Fetch
) -> None:
    routes = Routes({AT_MIRROR: BODY})
    fetcher = _fetcher(tmp_path, routes, lambda path: None, offline=True)
    first = fetch(fetcher)
    assert first.source == "mirror"
    # The signed catalogue now says something else: the cache hit must not be served.
    routes.requested.clear()
    fetcher = _fetcher(tmp_path, routes, lambda path: SIGNED, offline=True)
    with pytest.raises(BackendError):
        fetch(fetcher)
    assert routes.requested == [AT_MIRROR]
    assert not list((tmp_path / "cache").glob("*Test.tif"))


@pytest.mark.parametrize("fetch", [_md5, _sha1, _etag], ids=["md5", "sha1", "etag"])
def test_a_cached_copy_that_matches_the_signed_sha256_is_served(
    tmp_path: Path, fetch: Fetch
) -> None:
    routes = Routes({AT_MIRROR: BODY})
    fetch(_fetcher(tmp_path, routes, lambda path: SHA256, offline=True))
    routes.requested.clear()
    again = fetch(_fetcher(tmp_path, routes, lambda path: SHA256, offline=True))
    assert again.from_cache and routes.requested == []


# -- ruling 8: a loud online warning -------------------------------------------


@ROUTES
def test_online_a_signed_mismatch_prints_a_loud_warning_naming_the_bunker_item_and_digests(
    tmp_path: Path, fetch: Fetch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: BODY})
    result = fetch(_fetcher(tmp_path, routes, lambda path: SIGNED, offline=False))
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("WARNING")]
    assert len(lines) == 1
    line = lines[0]
    assert "Bunker bunker" in line and "unit/name/Test.tif" in line
    assert SIGNED in line and SHA256 in line
    assert "contradict its own signed catalogue" in line and "publisher" in line
    assert result.warning is not None and SIGNED in result.warning


@ROUTES
def test_the_warning_is_recorded_in_the_step_facts(tmp_path: Path, fetch: Fetch) -> None:
    from hammunition.fetch import record_fetch

    routes = Routes({AT_MIRROR: BODY, PUBLISHER: BODY})
    result = fetch(_fetcher(tmp_path, routes, lambda path: SIGNED, offline=False))
    facts: dict[str, str] = {}
    record_fetch(result, facts, mirrored=True)
    assert "contradict its own signed catalogue" in facts["warning"]
    assert SIGNED in facts["warning"] and SHA256 in facts["warning"]


@ROUTES
def test_no_warning_when_the_bytes_agree_or_there_is_no_signed_row(
    tmp_path: Path, fetch: Fetch, capsys: pytest.CaptureFixture[str]
) -> None:
    for signed, root in ((SHA256, "a"), (None, "b")):

        def lookup(path: MirrorPath, answer: str | None = signed) -> str | None:
            return answer

        routes = Routes({AT_MIRROR: BODY})
        result = fetch(_fetcher(tmp_path / root, routes, lookup, offline=False))
        assert result.warning is None
    assert "WARNING" not in capsys.readouterr().err


@ROUTES
def test_offline_a_mismatch_is_a_refusal_and_prints_no_fallback_warning(
    tmp_path: Path, fetch: Fetch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: BODY})
    with pytest.raises(BackendError):
        fetch(_fetcher(tmp_path, routes, lambda path: SIGNED, offline=True))
    assert "falling back" not in capsys.readouterr().err


# -- ruling 5: a row with a sha256 is always enforced --------------------------


def _row_context(tmp_path: Path, **changes: object) -> Any:
    from bunker_fixtures import artifact, make_context

    return make_context(tmp_path, [artifact("u", "n", b"bytes", **changes)])


@pytest.mark.parametrize(
    "changes",
    [{"size": None}, {"path": None}, {"size": None, "path": None}, {"status": "withheld"}],
    ids=["no-size", "no-path", "neither", "not-current"],
)
def test_a_row_with_a_sha256_is_found_whatever_else_it_lacks(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    context = _row_context(tmp_path, **changes)
    assert context.signed_sha256("u", "n") == hashlib.sha256(b"bytes").hexdigest()


def test_the_row_is_found_by_unit_name_and_owner(tmp_path: Path) -> None:
    from bunker_fixtures import artifact, make_context

    rows = [
        artifact("u", "n", b"mine", share="owner:abc", size=None),
        artifact("u", "other", b"x"),
        artifact("v", "n", b"y"),
    ]
    mine = make_context(tmp_path / "a", rows, mode="group", enrolment_id="abc")
    theirs = make_context(tmp_path / "b", rows, mode="group", enrolment_id="zzz")
    assert mine.signed_sha256("u", "n") == hashlib.sha256(b"mine").hexdigest()
    assert theirs.signed_sha256("u", "n") is None
    assert mine.signed_sha256("v", "n") == hashlib.sha256(b"y").hexdigest()
    assert mine.signed_sha256("u", "nope") is None


def test_a_row_with_no_sha256_has_nothing_to_enforce(tmp_path: Path) -> None:
    context = _row_context(tmp_path, sha256=None, status="withheld", path=None, size=None)
    assert context.signed_sha256("u", "n") is None


def _damaged(tmp_path: Path, **changes: object) -> Any:
    import dataclasses

    context = _row_context(tmp_path)
    assert context.verified is not None
    row = context.verified.catalogue.artifacts[0].model_copy(update=changes)
    catalogue = context.verified.catalogue.model_copy(update={"artifacts": (row,)})
    context.verified = dataclasses.replace(context.verified, catalogue=catalogue)
    return context


@pytest.mark.parametrize(
    "changes",
    [{"sha256": "XYZ"}, {"sha256": "A" * 64}, {"sha256": "ab" * 31}, {"sha256": None}],
    ids=["not-hex", "uppercase", "short", "current-without-digest"],
)
def test_a_malformed_row_is_an_error_not_no_row(tmp_path: Path, changes: dict[str, object]) -> None:
    context = _damaged(tmp_path, **changes)
    with pytest.raises(MalformedCatalogueRow, match="u/n"):
        context.signed_sha256("u", "n")


def _malformed(path: MirrorPath) -> str | None:
    raise MalformedCatalogueRow("u/n: its sha256 is not 64 lowercase hex digits")


@ROUTES
def test_offline_a_malformed_row_refuses_and_nothing_is_asked(tmp_path: Path, fetch: Fetch) -> None:
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: BODY})
    with pytest.raises(BackendError, match="malformed"):
        fetch(_fetcher(tmp_path, routes, _malformed, offline=True))
    assert routes.requested == []


@ROUTES
def test_online_a_malformed_row_warns_loudly_and_asks_the_publisher_only(
    tmp_path: Path, fetch: Fetch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = Routes({AT_MIRROR: BODY, PUBLISHER: BODY})
    result = fetch(_fetcher(tmp_path, routes, _malformed, offline=False))
    assert routes.requested == [PUBLISHER]
    assert result.source == "publisher"
    err = capsys.readouterr().err
    assert "WARNING" in err and "malformed" in err and "Bunker bunker" in err
    assert result.warning is not None and "malformed" in result.warning
