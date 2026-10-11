# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The FSTopo half of the topo-quads backend: fetch, check, install, record,
remove.  D-068, amended 2026-10-01. Synthetic sheets near 0/0."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends import Action, BackendError
from hammunition.backends.fstopo import (
    FsTopoBackend,
    FsTopoResolution,
    RegionSheets,
    no_sheets_line,
    read_record,
)
from hammunition.backends.topo import TIF, TopoQuadsBackend, TopoResolution
from hammunition.fetch import Fetcher, FetchResult, MirrorPath, VerificationError, mirror_url
from hammunition.fstopo import PINNED, UNVERIFIED, FsQuadFile, parse_row
from hammunition.manifest.schema import PackageManifest, TopoQuadsInstall
from test_fetch_mirror import Routes

BODY = b"II*\x00" + b"f" * 12
SHA = hashlib.sha256(BODY).hexdigest()
ALPHA = parse_row("0 0 0.125 0.125 1230000 11 ZZ Alpha")
BETA = parse_row("0 0.125 0.125 0.25 1230001 0 ZZ Beta Knob")
ALPHA_NEW = parse_row("0 0 0.125 0.125 1230000 12 ZZ Alpha")
URL_A = "https://data.fs.usda.gov/geodata/rastergateway/data3/a.tiff"
URL_B = "https://data.fs.usda.gov/geodata/rastergateway/data3/b.tiff"
OCEANIA = RegionSheets("atlantis/oceania", "atlantis-oceania", (ALPHA, BETA))
LEMURIA = RegionSheets("atlantis/lemuria", "atlantis-lemuria", ())


class FakeFetcher(Fetcher):
    def __init__(self, cache: Path, *, bad: tuple[str, ...] = ()) -> None:
        super().__init__(cache)
        self.bad = set(bad)
        self.calls: list[tuple[str, str]] = []
        self.mirrors: list[MirrorPath | None] = []

    def _land(self, path: Path) -> FetchResult:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BODY)
        return FetchResult(path, SHA, False, len(BODY))

    def fetch_sized(
        self, url: str, *, expected_size: int, mirror: MirrorPath | None = None
    ) -> FetchResult:
        self.calls.append(("sized", url))
        self.mirrors.append(mirror)
        if url in self.bad:
            raise VerificationError(f"{url} is not a TIFF")
        return self._land(self.sized_path_for(url, expected_size))

    def fetch(
        self, artifact: Any, *, max_bytes: int | None = None, mirror: Any = None
    ) -> FetchResult:
        self.calls.append(("sha256", artifact.url))
        return self._land(self.path_for(artifact))


def manifest() -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": "usfs-fstopo",
            "version": "station",
            "summary": "FSTopo sheets for a test",
            "categories": ["navigation-maps"],
            "install": [
                {
                    "install": {
                        "method": "topo-quads",
                        "provider": "usfs-fstopo",
                        "licence": "Public domain (USDA Forest Service)",
                        "licence_url": "https://www.fs.usda.gov/",
                    }
                }
            ],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "FSTopo sheets for a test, nothing more.",
                "why_you_want_it": "Because the test suite needs a manifest.",
                "upstream_url": "https://data.fs.usda.gov/geodata/rastergateway/",
            },
        }
    )


def _data(prefix: Path) -> Path:
    return prefix / "share" / "hammunition" / "data" / "usfs-fstopo"


def _steps(backend: TopoQuadsBackend | FsTopoBackend) -> list[Action]:
    m = manifest()
    block = m.install[0].install
    assert isinstance(block, TopoQuadsInstall)
    steps = backend.steps(m, block)
    assert all(isinstance(s, Action) for s in steps)
    return [s for s in steps if isinstance(s, Action)]


def _pair(tmp_path: Path, resolution: FsTopoResolution, **kw: Any) -> TopoQuadsBackend:
    fetcher = kw.pop("fetcher", FakeFetcher(tmp_path / "cache"))
    fs = FsTopoBackend(fetcher=fetcher, prefix=tmp_path, resolution=resolution, **kw)
    return TopoQuadsBackend(
        fetcher=fetcher, prefix=tmp_path, resolution=TopoResolution(), fstopo=fs, ledger=fs.ledger
    )


def test_a_pinned_sheet_is_checked_by_sha256_an_unpinned_one_is_disclosed_unverified(
    tmp_path: Path,
) -> None:
    fetcher = FakeFetcher(tmp_path / "cache")
    backend = _pair(
        tmp_path,
        FsTopoResolution(
            regions=(OCEANIA,),
            fetch=(
                FsQuadFile(ALPHA, URL_A, len(BODY), SHA),
                FsQuadFile(BETA, URL_B, len(BODY), None),
            ),
        ),
        fetcher=fetcher,
    )
    steps = _steps(backend)
    fetches = [s for s in steps if s.kind == "fetch"]
    assert PINNED in fetches[0].description and UNVERIFIED in fetches[1].description
    assert ALPHA.name in fetches[0].description
    outcomes = [s.perform() for s in steps]
    assert fetcher.calls == [("sha256", URL_A), ("sized", URL_B)]
    for quad in (ALPHA, BETA):
        assert (_data(tmp_path) / f"{quad.name}{TIF}").read_bytes() == BODY
    assert any("unverified" in o for o in outcomes)
    assert (
        read_record(_data(tmp_path) / "atlantis-oceania.quads", OCEANIA.region, OCEANIA.slug)
        == OCEANIA
    )
    assert backend.ledger.failed == {}


def test_a_sheet_that_fails_is_named_and_the_others_install(tmp_path: Path) -> None:
    fetcher = FakeFetcher(tmp_path / "cache", bad=(URL_A,))
    backend = _pair(
        tmp_path,
        FsTopoResolution(
            regions=(OCEANIA,),
            fetch=(
                FsQuadFile(ALPHA, URL_A, len(BODY), None),
                FsQuadFile(BETA, URL_B, len(BODY), None),
            ),
        ),
        fetcher=fetcher,
    )
    outcomes = [s.perform() for s in _steps(backend)]
    assert any(o.startswith("FAILED, the rest continues") for o in outcomes)
    assert not (_data(tmp_path) / f"{ALPHA.name}{TIF}").exists()
    assert (_data(tmp_path) / f"{BETA.name}{TIF}").is_file()
    assert list(backend.ledger.failed) == [f"quad {ALPHA.name}"]


def test_a_new_vintage_replaces_the_old_sheet_only_once_it_is_on_disk(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / f"{ALPHA.name}{TIF}").write_bytes(BODY)
    region = RegionSheets("atlantis/oceania", "atlantis-oceania", (ALPHA_NEW,))
    backend = _pair(
        tmp_path,
        FsTopoResolution(regions=(region,), fetch=(FsQuadFile(ALPHA_NEW, URL_A, len(BODY), None),)),
    )
    [s.perform() for s in _steps(backend)]
    assert (data / f"{ALPHA_NEW.name}{TIF}").is_file()
    assert not (data / f"{ALPHA.name}{TIF}").exists()


def test_a_region_with_no_sheet_records_an_empty_set_and_says_so(tmp_path: Path) -> None:
    backend = _pair(tmp_path, FsTopoResolution(regions=(LEMURIA,)))
    steps = _steps(backend)
    assert any(no_sheets_line(LEMURIA.region) in s.description for s in steps)
    [s.perform() for s in steps]
    assert (
        read_record(_data(tmp_path) / "atlantis-lemuria.quads", LEMURIA.region, LEMURIA.slug)
        == LEMURIA
    )


def test_a_sheet_no_region_needs_is_removed(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / f"{BETA.name}{TIF}").write_bytes(BODY)
    (data / "atlantis-gone.quads").write_text("# FSTopo quads: 0\n")
    [s.perform() for s in _steps(_pair(tmp_path, FsTopoResolution()))]
    assert not (data / f"{BETA.name}{TIF}").exists()
    assert not (data / "atlantis-gone.quads").exists()


def test_without_an_fstopo_backend_the_block_is_refused(tmp_path: Path) -> None:
    lone = TopoQuadsBackend(
        fetcher=FakeFetcher(tmp_path), prefix=tmp_path, resolution=TopoResolution()
    )
    with pytest.raises(BackendError, match="usfs-fstopo"):
        _steps(lone)


# --- the Bunker mirror (#381, Task 12) -----------------------------------------------

BUNKER = "http://bunker.lan:8080"
UNPINNED_SHEET = FsQuadFile(BETA, URL_B, len(BODY), None)
PINNED_SHEET = FsQuadFile(ALPHA, URL_A, len(BODY), SHA)


def _mirrored(tmp_path: Path, routes: Routes, **kw: Any) -> TopoQuadsBackend:
    fetcher = Fetcher(tmp_path / "cache", transport=routes, mirror=BUNKER, **kw)
    return _pair(
        tmp_path, FsTopoResolution(regions=(OCEANIA,), fetch=(UNPINNED_SHEET,)), fetcher=fetcher
    )


def test_an_unpinned_sheet_asks_the_mirror_first_by_its_name_and_stays_unverified(
    tmp_path: Path,
) -> None:
    at_mirror = mirror_url(BUNKER, MirrorPath("usfs-fstopo", BETA.name))
    routes = Routes({at_mirror: BODY})
    backend = _mirrored(tmp_path, routes)
    steps = _steps(backend)
    fetch = next(s for s in steps if s.kind == "fetch")
    assert fetch.sources == (at_mirror, URL_B)
    assert "LAN mirror first" in fetch.description and UNVERIFIED in fetch.description
    assert "size is checked either way" in fetch.description
    assert fetch.detail.startswith(f"{at_mirror}, then {URL_B} (")
    outcomes = [s.perform() for s in steps]
    assert routes.requested == [at_mirror]
    assert fetch.facts == {"source": "mirror", "fetched_from": at_mirror}
    assert any("unverified" in o and "from the LAN mirror" in o for o in outcomes)
    assert (_data(tmp_path) / f"{BETA.name}{TIF}").read_bytes() == BODY
    assert backend.ledger.failed == {}


def test_a_mirror_sheet_that_is_not_a_tiff_is_not_installed_online_or_off(tmp_path: Path) -> None:
    page = b"<html>" + b"x" * (len(BODY) - 6)
    at_mirror = mirror_url(BUNKER, MirrorPath("usfs-fstopo", BETA.name))
    for offline in (False, True):
        routes = Routes({at_mirror: page, URL_B: page})
        backend = _mirrored(tmp_path / str(offline), routes, offline=offline)
        outcomes = [s.perform() for s in _steps(backend)]
        assert any(o.startswith("FAILED, the rest continues") for o in outcomes)
        assert not (_data(tmp_path / str(offline)) / f"{BETA.name}{TIF}").exists()
        assert (URL_B in routes.requested) is (not offline)


def test_a_sheet_the_mirror_lacks_comes_from_the_publisher_and_records_why(tmp_path: Path) -> None:
    routes = Routes({URL_B: BODY})
    backend = _mirrored(tmp_path, routes)
    fetch = next(s for s in _steps(backend) if s.kind == "fetch")
    outcome = fetch.perform()
    assert fetch.facts["source"] == "publisher" and fetch.facts["fetched_from"] == URL_B
    assert "404" in fetch.facts["mirror_failure"] and "mirror was passed over" in outcome


def _pinned(tmp_path: Path, routes: Routes, **kw: Any) -> TopoQuadsBackend:
    fetcher = Fetcher(tmp_path / "cache", transport=routes, mirror=BUNKER, **kw)
    return _pair(
        tmp_path, FsTopoResolution(regions=(OCEANIA,), fetch=(PINNED_SHEET,)), fetcher=fetcher
    )


PINNED_AT_MIRROR = mirror_url(BUNKER, MirrorPath("usfs-fstopo", ALPHA.name))


def test_an_offline_pinned_sheet_from_a_good_mirror_installs(tmp_path: Path) -> None:
    routes = Routes({PINNED_AT_MIRROR: BODY, URL_A: BODY})
    backend = _pinned(tmp_path, routes, offline=True)
    steps = _steps(backend)
    fetch = next(s for s in steps if s.kind == "fetch")
    assert fetch.sources == (PINNED_AT_MIRROR,) and PINNED in fetch.description
    assert "Bunker only, offline" in fetch.description
    outcomes = [s.perform() for s in steps]
    assert routes.requested == [PINNED_AT_MIRROR]
    assert fetch.facts == {"source": "mirror", "fetched_from": PINNED_AT_MIRROR}
    assert any("verified against the pin" in o and "from the LAN mirror" in o for o in outcomes)
    assert (_data(tmp_path) / f"{ALPHA.name}{TIF}").read_bytes() == BODY
    assert backend.ledger.failed == {}


def test_an_offline_corrupt_mirror_copy_of_a_pinned_sheet_is_refused_and_leaves_no_part(
    tmp_path: Path,
) -> None:
    routes = Routes({PINNED_AT_MIRROR: b"II*\x00" + b"x" * (len(BODY) - 4), URL_A: BODY})
    backend = _pinned(tmp_path, routes, offline=True)
    outcomes = [s.perform() for s in _steps(backend)]
    assert any(o.startswith("FAILED, the rest continues") for o in outcomes)
    assert routes.requested == [PINNED_AT_MIRROR]
    assert not (_data(tmp_path) / f"{ALPHA.name}{TIF}").exists()
    assert not list((tmp_path / "cache").glob("*.part.*"))
    assert not list((tmp_path / "cache").glob("*a.tiff"))


def test_an_online_bad_mirror_copy_of_a_pinned_sheet_falls_back_to_the_publisher(
    tmp_path: Path,
) -> None:
    routes = Routes({PINNED_AT_MIRROR: b"II*\x00" + b"x" * (len(BODY) - 4), URL_A: BODY})
    backend = _pinned(tmp_path, routes)
    fetch = next(s for s in _steps(backend) if s.kind == "fetch")
    outcome = fetch.perform()
    assert routes.requested == [PINNED_AT_MIRROR, URL_A]
    assert fetch.facts["source"] == "publisher" and "sha256" in fetch.facts["mirror_failure"]
    assert "mirror was passed over" in outcome


def test_when_the_mirror_and_the_publisher_both_fail_the_pin_the_error_names_both(
    tmp_path: Path,
) -> None:
    bad = b"II*\x00" + b"x" * (len(BODY) - 4)
    routes = Routes({PINNED_AT_MIRROR: bad, URL_A: bad})
    backend = _pinned(tmp_path, routes)
    outcome = next(s for s in _steps(backend) if s.kind == "fetch").perform()
    assert "LAN mirror was tried first and passed over" in outcome and URL_A in outcome
    assert PINNED_AT_MIRROR in outcome
    assert not list((tmp_path / "cache").glob("*.part.*"))


def test_a_pinned_sheet_without_a_mirror_reads_as_before(tmp_path: Path) -> None:
    fetcher = FakeFetcher(tmp_path / "cache")
    backend = _pair(
        tmp_path, FsTopoResolution(regions=(OCEANIA,), fetch=(PINNED_SHEET,)), fetcher=fetcher
    )
    fetch = next(s for s in _steps(backend) if s.kind == "fetch")
    assert PINNED in fetch.description and "mirror" not in fetch.description
    assert fetch.sources == (URL_A,) and fetch.detail == f"{URL_A} ({len(BODY)} bytes)"
    assert fetch.perform().startswith("downloaded") and fetcher.calls == [("sha256", URL_A)]
    assert fetcher.mirrors == []


def test_without_a_mirror_the_unpinned_step_reads_as_it_did(tmp_path: Path) -> None:
    fetcher = FakeFetcher(tmp_path / "cache")
    backend = _pair(
        tmp_path, FsTopoResolution(regions=(OCEANIA,), fetch=(UNPINNED_SHEET,)), fetcher=fetcher
    )
    fetch = next(s for s in _steps(backend) if s.kind == "fetch")
    assert fetch.sources == (URL_B,) and "mirror" not in fetch.description
    assert fetch.detail == f"{URL_B} ({len(BODY)} bytes)"
    outcome = fetch.perform()
    assert "mirror" not in outcome and fetch.facts == {"source": "publisher"}
    assert fetcher.mirrors == [MirrorPath("usfs-fstopo", BETA.name)]
