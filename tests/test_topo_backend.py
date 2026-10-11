# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The topo-quads backend: fetch, verify, install, record, remove.  D-068.

Synthetic sheets near 0/0 and synthetic regions only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hammunition.backends import Action, Command
from hammunition.backends.topo import (
    TIF,
    RegionQuads,
    TopoQuadsBackend,
    TopoResolution,
    no_quads_line,
    read_record,
    render_record,
)
from hammunition.fetch import Fetcher, FetchResult, MirrorPath, VerificationError, mirror_url
from hammunition.manifest.schema import PackageManifest, TopoQuadsInstall
from hammunition.ustopo import UNPINNED, Quad
from test_fetch_mirror import Routes

BODY = b"q" * 10
MD5 = hashlib.md5(BODY, usedforsecurity=False).hexdigest()
ALPHA = Quad(0.0, 0.0, 0.125, 0.125, 10, MD5, "ZZ/ZZ_Alpha_20240101")
BETA = Quad(0.0, 0.125, 0.125, 0.25, 10, f"{MD5}-2", "ZZ/ZZ_Beta_20240101")
OCEANIA = RegionQuads("atlantis/oceania", "atlantis-oceania", (ALPHA, BETA))
LEMURIA = RegionQuads("atlantis/lemuria", "atlantis-lemuria", ())


class FakeFetcher(Fetcher):
    """The real cache layout, no network. A URL in *bad* fails verification."""

    def __init__(self, cache: Path, *, bad: Sequence[str] = ()) -> None:
        super().__init__(cache)
        self.bad = set(bad)
        self.calls: list[tuple[str, str, int]] = []
        self.mirrors: list[MirrorPath | None] = []

    def fetch_etag(
        self, url: str, etag: str, *, expected_size: int, mirror: MirrorPath | None = None
    ) -> FetchResult:
        self.calls.append((url, etag, expected_size))
        self.mirrors.append(mirror)
        if url in self.bad:
            raise VerificationError(f"{url} does not match the ETag its publisher lists")
        path = self.etag_path_for(url, etag)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BODY)
        return FetchResult(path, hashlib.sha256(BODY).hexdigest(), False, 10)


def manifest() -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": "usgs-ustopo",
            "version": "station",
            "summary": "US Topo sheets for a test",
            "categories": ["navigation-maps"],
            "install": [
                {
                    "install": {
                        "method": "topo-quads",
                        "provider": "usgs-ustopo",
                        "licence": "Public domain (USGS)",
                        "licence_url": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
                    }
                }
            ],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "US Topo sheets for a test, nothing more.",
                "why_you_want_it": "Because the test suite needs a manifest.",
                "upstream_url": "https://www.usgs.gov/programs/national-geospatial-program/us-topo-maps-america",
            },
        }
    )


def _block(m: PackageManifest) -> TopoQuadsInstall:
    block = m.install[0].install
    assert isinstance(block, TopoQuadsInstall)
    return block


def _data(prefix: Path) -> Path:
    return prefix / "share" / "hammunition" / "data" / "usgs-ustopo"


def _actions(steps: list[Action | Command]) -> list[Action]:
    assert all(isinstance(s, Action) for s in steps)
    return [s for s in steps if isinstance(s, Action)]


def _backend(tmp_path: Path, resolution: TopoResolution, **kw: Any) -> TopoQuadsBackend:
    kw.setdefault("fetcher", FakeFetcher(tmp_path / "cache"))
    return TopoQuadsBackend(prefix=tmp_path, resolution=resolution, **kw)


def _run(backend: TopoQuadsBackend) -> list[str]:
    m = manifest()
    return [s.perform() for s in _actions(backend.steps(m, _block(m)))]


def test_each_sheet_is_fetched_says_how_it_is_verified_and_is_installed(tmp_path: Path) -> None:
    fetcher = FakeFetcher(tmp_path / "cache")
    backend = _backend(
        tmp_path, TopoResolution(regions=(OCEANIA,), fetch=(ALPHA, BETA)), fetcher=fetcher
    )
    m = manifest()
    steps = _actions(backend.steps(m, _block(m)))
    fetches = [s for s in steps if s.kind == "fetch"]
    assert len(fetches) == 2
    assert all(UNPINNED in s.description for s in fetches)
    assert "Public domain (USGS)" in fetches[0].description
    outcomes = [s.perform() for s in steps]
    assert fetcher.calls == [(ALPHA.url, MD5, 10), (BETA.url, f"{MD5}-2", 10)]
    for quad in (ALPHA, BETA):
        installed = _data(tmp_path) / f"{quad.name}{TIF}"
        assert installed.read_bytes() == BODY
        # The cached copy is deleted once installed.
        assert not fetcher.etag_path_for(quad.url, quad.etag).exists()
    assert any("ETag" in o and "not pinned" in o for o in outcomes)
    record = read_record(
        _data(tmp_path) / "atlantis-oceania.quads", "atlantis/oceania", "atlantis-oceania"
    )
    assert record == OCEANIA
    assert backend.ledger.failed == {}


def test_a_sheet_that_does_not_verify_is_named_and_the_others_install(tmp_path: Path) -> None:
    fetcher = FakeFetcher(tmp_path / "cache", bad=[ALPHA.url])
    backend = _backend(
        tmp_path, TopoResolution(regions=(OCEANIA,), fetch=(ALPHA, BETA)), fetcher=fetcher
    )
    outcomes = _run(backend)
    assert any(o.startswith("FAILED, the rest continues") for o in outcomes)
    assert any(o == f"skipped: {ALPHA.name} did not verify" for o in outcomes)
    assert not (_data(tmp_path) / f"{ALPHA.name}{TIF}").exists()
    assert (_data(tmp_path) / f"{BETA.name}{TIF}").is_file()
    assert list(backend.ledger.failed) == [f"quad {ALPHA.name}"]


def test_a_region_no_sheet_covers_records_so_and_fetches_nothing(tmp_path: Path) -> None:
    fetcher = FakeFetcher(tmp_path / "cache")
    backend = _backend(tmp_path, TopoResolution(regions=(LEMURIA,)), fetcher=fetcher)
    m = manifest()
    steps = _actions(backend.steps(m, _block(m)))
    assert [s.kind for s in steps] == ["install-data"]
    assert no_quads_line("atlantis/lemuria") in steps[0].description
    assert "United States" in steps[0].description
    steps[0].perform()
    assert fetcher.calls == []
    assert (
        read_record(
            _data(tmp_path) / "atlantis-lemuria.quads", "atlantis/lemuria", "atlantis-lemuria"
        )
        == LEMURIA
    )


def test_a_current_record_is_not_rewritten_and_an_installed_sheet_not_fetched(
    tmp_path: Path,
) -> None:
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / "atlantis-oceania.quads").write_text(render_record(OCEANIA))
    for quad in (ALPHA, BETA):
        (data / f"{quad.name}{TIF}").write_bytes(BODY)
    backend = _backend(tmp_path, TopoResolution(regions=(OCEANIA,), current=(ALPHA, BETA)))
    m = manifest()
    assert backend.steps(m, _block(m)) == []


def test_a_sheet_and_a_record_no_region_needs_are_removed(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / "atlantis-gone.quads").write_text(render_record(LEMURIA))
    (data / "ZZ_Old_20100101.tif").write_bytes(BODY)
    (data / f"{ALPHA.name}{TIF}").write_bytes(BODY)
    lone = RegionQuads("atlantis/oceania", "atlantis-oceania", (ALPHA,))
    backend = _backend(tmp_path, TopoResolution(regions=(lone,), current=(ALPHA,)))
    m = manifest()
    removals = [s for s in _actions(backend.steps(m, _block(m))) if s.kind == "remove-data"]
    assert sorted(Path(s.detail).name for s in removals) == [
        "ZZ_Old_20100101.tif",
        "atlantis-gone.quads",
    ]


def test_a_kept_regions_record_stays(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / "atlantis-lemuria.quads").write_text(render_record(LEMURIA))
    backend = _backend(tmp_path, TopoResolution(), keep=frozenset({"atlantis-lemuria"}))
    m = manifest()
    assert backend.steps(m, _block(m)) == []


def test_a_record_that_is_not_a_record_is_not_trusted(tmp_path: Path) -> None:
    path = tmp_path / "r.quads"
    for text in (
        "",
        "ZZ/ZZ_Alpha_20240101\n",
        f"# US Topo quads: 2\n0 0 0.125 0.125 10 {MD5} ZZ/ZZ_Alpha_20240101\n",
        f"# US Topo quads: 1\n0 0 0.125 0.125 10 {MD5} ../../etc/passwd\n",
    ):
        path.write_text(text)
        assert read_record(path, "r", "r") is None
    assert read_record(tmp_path / "missing.quads", "r", "r") is None


NEW_ALPHA = Quad(0.0, 0.0, 0.125, 0.125, 10, MD5, "ZZ/ZZ_Alpha_20260101")


def test_an_older_edition_is_kept_when_its_replacement_did_not_install(tmp_path: Path) -> None:
    """Review I1: removing it first left a hole until the next online run."""
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / f"{ALPHA.name}{TIF}").write_bytes(BODY)
    fetcher = FakeFetcher(tmp_path / "cache", bad=[NEW_ALPHA.url])
    region = RegionQuads("atlantis/oceania", "atlantis-oceania", (NEW_ALPHA,))
    backend = _backend(
        tmp_path, TopoResolution(regions=(region,), fetch=(NEW_ALPHA,)), fetcher=fetcher
    )
    outcomes = _run(backend)
    assert (data / f"{ALPHA.name}{TIF}").is_file()
    assert any(o.startswith(f"kept {data / ALPHA.name}") for o in outcomes)


def test_an_older_edition_goes_once_its_replacement_is_installed(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / f"{ALPHA.name}{TIF}").write_bytes(BODY)
    region = RegionQuads("atlantis/oceania", "atlantis-oceania", (NEW_ALPHA,))
    backend = _backend(tmp_path, TopoResolution(regions=(region,), fetch=(NEW_ALPHA,)))
    m = manifest()
    steps = _actions(backend.steps(m, _block(m)))
    kinds = [s.kind for s in steps]
    assert kinds.index("remove-data") > kinds.index("install-data"), "removed after the install"
    for step in steps:
        step.perform()
    assert not (data / f"{ALPHA.name}{TIF}").exists()
    assert (data / f"{NEW_ALPHA.name}{TIF}").is_file()


# --- the Bunker mirror (#381, Task 12) -----------------------------------------------

BUNKER = "http://bunker.lan:8080"


def _mirrored(tmp_path: Path, routes: Routes, **kw: Any) -> TopoQuadsBackend:
    fetcher = Fetcher(tmp_path / "cache", transport=routes, mirror=BUNKER, **kw)
    return _backend(tmp_path, TopoResolution(regions=(OCEANIA,), fetch=(ALPHA,)), fetcher=fetcher)


def test_a_sheet_asks_the_mirror_first_by_its_state_path_and_says_so(tmp_path: Path) -> None:
    at_mirror = mirror_url(BUNKER, MirrorPath("usgs-ustopo", ALPHA.path))
    assert at_mirror == f"{BUNKER}/usgs-ustopo/ZZ/ZZ_Alpha_20240101"
    routes = Routes({at_mirror: BODY})
    backend = _mirrored(tmp_path, routes)
    m = manifest()
    steps = _actions(backend.steps(m, _block(m)))
    fetch = next(s for s in steps if s.kind == "fetch")
    assert fetch.sources == (at_mirror, ALPHA.url)
    assert "LAN mirror first" in fetch.description and UNPINNED in fetch.description
    assert fetch.detail.startswith(f"{at_mirror}, then {ALPHA.url} (ETag {MD5}")
    outcomes = [s.perform() for s in steps]
    assert routes.requested == [at_mirror]
    assert fetch.facts == {"source": "mirror", "fetched_from": at_mirror}
    assert any("from the LAN mirror" in o and "ETag" in o for o in outcomes)
    assert (_data(tmp_path) / f"{ALPHA.name}{TIF}").read_bytes() == BODY
    assert backend.ledger.failed == {}


def test_a_sheet_the_mirror_cannot_give_comes_from_the_publisher_and_records_why(
    tmp_path: Path,
) -> None:
    routes = Routes({ALPHA.url: BODY})
    backend = _mirrored(tmp_path, routes)
    m = manifest()
    steps = _actions(backend.steps(m, _block(m)))
    fetch = next(s for s in steps if s.kind == "fetch")
    outcome = fetch.perform()
    assert fetch.facts["source"] == "publisher" and fetch.facts["fetched_from"] == ALPHA.url
    assert "404" in fetch.facts["mirror_failure"] and "mirror was passed over" in outcome


def test_offline_a_sheet_is_the_mirrors_alone_and_the_plan_says_so(tmp_path: Path) -> None:
    at_mirror = mirror_url(BUNKER, MirrorPath("usgs-ustopo", ALPHA.path))
    routes = Routes({at_mirror: BODY, ALPHA.url: BODY})
    backend = _mirrored(tmp_path, routes, offline=True)
    m = manifest()
    fetch = next(s for s in _actions(backend.steps(m, _block(m))) if s.kind == "fetch")
    assert fetch.sources == (at_mirror,) and "Bunker only, offline" in fetch.description
    fetch.perform()
    assert routes.requested == [at_mirror]


def test_without_a_mirror_the_sheet_step_reads_as_it_did(tmp_path: Path) -> None:
    fetcher = FakeFetcher(tmp_path / "cache")
    backend = _backend(
        tmp_path, TopoResolution(regions=(OCEANIA,), fetch=(ALPHA,)), fetcher=fetcher
    )
    m = manifest()
    fetch = next(s for s in _actions(backend.steps(m, _block(m))) if s.kind == "fetch")
    assert fetch.sources == (ALPHA.url,) and "mirror" not in fetch.description
    assert fetch.detail == f"{ALPHA.url} (ETag {ALPHA.etag}, 10 bytes)"
    outcome = fetch.perform()
    assert "mirror" not in outcome and fetch.facts == {"source": "publisher"}
    assert fetcher.mirrors == [MirrorPath("usgs-ustopo", ALPHA.path)]
