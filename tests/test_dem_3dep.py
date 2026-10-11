# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""USGS 3DEP as a second dem-tiles provider: resolution, the ETag-verified
tile, and the station's choice.  D-068, amended 2026-10-01.

Synthetic regions near 0/0 and synthetic tiles only; no network.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends import Action
from hammunition.backends.dem import (
    TIF,
    DemResolution,
    DemTilesBackend,
    RegionTiles,
    no_bare_earth_line,
    read_record,
)
from hammunition.copernicus import UNPINNED, CopernicusError, TileFile
from hammunition.fetch import Fetcher, FetchResult, MirrorPath, mirror_url
from hammunition.geofabrik import GeofabrikError
from hammunition.manifest.schema import DemTilesInstall, PackageManifest
from hammunition.terrain_plan import poly_url, resolve_bare_earth
from hammunition.usgs3dep import parse_tile_list, tile_url
from test_fetch_mirror import Routes

BODY = b"e" * 12
MD5 = hashlib.md5(BODY, usedforsecurity=False).hexdigest()
#: Square (0, 0) is named by its north-west corner.
NAME = "USGS_13_n01e000"
LIST = parse_tile_list(f"{NAME} 12 {MD5}-2\nUSGS_13_n11e010 12 {MD5}\n")
OUTLINE = "oceania\n1\n 0.2 0.2\n 0.8 0.2\n 0.8 0.8\n 0.2 0.8\nEND\nEND\n"
ABROAD = "lemuria\n1\n 50.2 50.2\n 50.8 50.2\n 50.8 50.8\nEND\nEND\n"
OCEANIA = ("atlantis/oceania", "atlantis-oceania")
LEMURIA = ("atlantis/lemuria", "atlantis-lemuria")


class RegionProbe:
    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts
        self.asked: list[str] = []

    def head(self, url: str) -> tuple[int, int, str | None]:  # pragma: no cover
        raise AssertionError("3DEP never HEADs Geofabrik")

    def text(self, url: str) -> str:
        self.asked.append(url)
        if url not in self.texts:
            raise GeofabrikError(f"{url} could not be fetched: offline")
        return self.texts[url]


class TileProbe:
    def __init__(self, heads: dict[str, tuple[int, int, str | None]]) -> None:
        self.heads = heads
        self.asked: list[str] = []

    def head(self, url: str) -> tuple[int, int, str | None]:
        self.asked.append(url)
        if url not in self.heads:
            raise CopernicusError(f"{url} could not be reached: offline")
        return self.heads[url]


def _resolve(
    tmp_path: Path, regions: dict[str, str], heads: dict[str, Any], *pairs: tuple[str, str]
) -> tuple[DemResolution, TileProbe]:
    probe = TileProbe(heads)
    return (
        resolve_bare_earth(
            list(pairs or (OCEANIA,)),
            installed=tmp_path,
            tiles=LIST,
            region_probe=RegionProbe(regions),
            tile_probe=probe,
        ),
        probe,
    )


def test_a_region_s_tile_is_chosen_from_its_outline_and_head_checked(tmp_path: Path) -> None:
    got, probe = _resolve(
        tmp_path,
        {poly_url(OCEANIA[0]): OUTLINE},
        {tile_url(NAME): (200, 12, f'"{MD5}-2"')},
    )
    assert got.regions == (RegionTiles(*OCEANIA, (NAME,), 0),)
    assert [(t.name, t.size, t.etag, t.verified_by) for t in got.fetch] == [
        (NAME, 12, f"{MD5}-2", UNPINNED)
    ]
    assert probe.asked == [tile_url(NAME)]


def test_a_region_outside_the_us_gets_no_tile_and_a_warning(tmp_path: Path) -> None:
    got, probe = _resolve(tmp_path, {poly_url(LEMURIA[0]): ABROAD}, {}, LEMURIA)
    assert got.fetch == () and probe.asked == []
    (entry,) = got.regions
    assert entry.no_terrain
    line = no_bare_earth_line(LEMURIA[0])
    assert "3DEP" in line and "Copernicus" in line and "BRouter" in line


def test_a_changed_object_refuses_naming_the_regeneration(tmp_path: Path) -> None:
    with pytest.raises(CopernicusError, match=r"gen_3dep_tiles\.py --fetch"):
        _resolve(
            tmp_path,
            {poly_url(OCEANIA[0]): OUTLINE},
            {tile_url(NAME): (200, 12, f'"{MD5}-3"')},
        )


def test_a_recorded_region_with_its_tile_installed_needs_no_network(tmp_path: Path) -> None:
    (tmp_path / f"{OCEANIA[1]}.tiles").write_text(f"# squares with no published tile: 0\n{NAME}\n")
    (tmp_path / f"{NAME}{TIF}").write_bytes(BODY)
    got, probe = _resolve(tmp_path, {}, {})
    assert got.current == (NAME,) and got.fetch == () and probe.asked == []
    assert read_record(tmp_path / f"{OCEANIA[1]}.tiles", *OCEANIA) == RegionTiles(
        *OCEANIA, (NAME,), 0
    )


# --- the backend --------------------------------------------------------------


class FakeFetcher(Fetcher):
    def __init__(self, cache: Path) -> None:
        super().__init__(cache)
        self.calls: list[tuple[str, str, int]] = []
        self.mirrors: list[MirrorPath | None] = []

    def fetch_etag(
        self, url: str, etag: str, *, expected_size: int, mirror: MirrorPath | None = None
    ) -> FetchResult:
        self.calls.append((url, etag, expected_size))
        self.mirrors.append(mirror)
        path = self.etag_path_for(url, etag)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BODY)
        return FetchResult(path, hashlib.sha256(BODY).hexdigest(), False, len(BODY))


def manifest(name: str, provider: str) -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": name,
            "version": "station",
            "summary": "Elevation for a test",
            "categories": ["navigation-maps"],
            "install": [
                {
                    "install": {
                        "method": "dem-tiles",
                        "provider": provider,
                        "licence": "Public domain (USGS)",
                        "licence_url": "https://www.usgs.gov/",
                    }
                }
            ],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Elevation for a test, nothing more.",
                "why_you_want_it": "Because the test suite needs a manifest.",
                "upstream_url": "https://www.usgs.gov/3d-elevation-program",
            },
        }
    )


def _steps(backend: DemTilesBackend, m: PackageManifest) -> list[Action]:
    block = m.install[0].install
    assert isinstance(block, DemTilesInstall)
    steps = backend.steps(m, block)
    assert all(isinstance(s, Action) for s in steps)
    return [s for s in steps if isinstance(s, Action)]


TILE = TileFile(NAME, tile_url(NAME), 12, None, None, f"{MD5}-2")


def _pair(tmp_path: Path, bare: DemResolution) -> tuple[DemTilesBackend, FakeFetcher]:
    fetcher = FakeFetcher(tmp_path / "cache")
    three = DemTilesBackend(fetcher=fetcher, prefix=tmp_path, resolution=bare, provider="usgs-3dep")
    copernicus = DemTilesBackend(
        fetcher=fetcher, prefix=tmp_path, resolution=DemResolution(), bare_earth=three
    )
    return copernicus, fetcher


def _data(tmp_path: Path) -> Path:
    return tmp_path / "share" / "hammunition" / "data" / "dem-3dep"


def test_a_3dep_block_is_built_by_the_bare_earth_backend_and_checked_by_etag(
    tmp_path: Path,
) -> None:
    backend, fetcher = _pair(
        tmp_path, DemResolution(regions=(RegionTiles(*OCEANIA, (NAME,), 0),), fetch=(TILE,))
    )
    steps = _steps(backend, manifest("dem-3dep", "usgs-3dep"))
    fetch = [s for s in steps if s.kind == "fetch"]
    assert len(fetch) == 1 and UNPINNED in fetch[0].description
    assert "3DEP" in fetch[0].description
    outcomes = [s.perform() for s in steps]
    assert fetcher.calls == [(TILE.url, f"{MD5}-2", 12)]
    assert (_data(tmp_path) / f"{NAME}{TIF}").read_bytes() == BODY
    assert not fetcher.etag_path_for(TILE.url, TILE.etag or "").exists()
    assert any("ETag" in o and "not pinned" in o for o in outcomes)
    assert read_record(_data(tmp_path) / "atlantis-oceania.tiles", *OCEANIA) == RegionTiles(
        *OCEANIA, (NAME,), 0
    )
    assert backend.ledger.failed == {}


def test_with_copernicus_chosen_installed_3dep_tiles_and_records_are_removed(
    tmp_path: Path,
) -> None:
    data = _data(tmp_path)
    data.mkdir(parents=True)
    (data / f"{NAME}{TIF}").write_bytes(BODY)
    (data / "atlantis-oceania.tiles").write_text(f"# squares with no published tile: 0\n{NAME}\n")
    backend, fetcher = _pair(tmp_path, DemResolution())
    for step in _steps(backend, manifest("dem-3dep", "usgs-3dep")):
        step.perform()
    assert fetcher.calls == []
    assert not (data / f"{NAME}{TIF}").exists()
    assert not (data / "atlantis-oceania.tiles").exists()


def test_a_provider_with_no_backend_is_refused_not_skipped(tmp_path: Path) -> None:
    lone = DemTilesBackend(
        fetcher=FakeFetcher(tmp_path), prefix=tmp_path, resolution=DemResolution()
    )
    with pytest.raises(Exception, match="usgs-3dep"):
        _steps(lone, manifest("dem-3dep", "usgs-3dep"))


# --- the Bunker mirror (#381, Task 12) -----------------------------------------------

BUNKER = "http://bunker.lan:8080"
SINGLE = TileFile(NAME, tile_url(NAME), len(BODY), None, None, MD5)


def _mirrored(tmp_path: Path, routes: Routes, **kw: Any) -> DemTilesBackend:
    fetcher = Fetcher(tmp_path / "cache", transport=routes, mirror=BUNKER, **kw)
    three = DemTilesBackend(
        fetcher=fetcher,
        prefix=tmp_path,
        resolution=DemResolution(regions=(RegionTiles(*OCEANIA, (NAME,), 0),), fetch=(SINGLE,)),
        provider="usgs-3dep",
    )
    return DemTilesBackend(
        fetcher=fetcher, prefix=tmp_path, resolution=DemResolution(), bare_earth=three
    )


def test_a_3dep_tile_asks_the_mirror_first_by_its_name_and_keeps_its_etag_check(
    tmp_path: Path,
) -> None:
    at_mirror = mirror_url(BUNKER, MirrorPath("dem-3dep", NAME))
    assert at_mirror == f"{BUNKER}/dem-3dep/{NAME}"
    routes = Routes({at_mirror: BODY})
    backend = _mirrored(tmp_path, routes)
    steps = _steps(backend, manifest("dem-3dep", "usgs-3dep"))
    fetch = next(s for s in steps if s.kind == "fetch")
    assert fetch.sources == (at_mirror, SINGLE.url)
    assert "LAN mirror first" in fetch.description and UNPINNED in fetch.description
    assert "the ETag is checked either way" in fetch.description
    assert fetch.detail.startswith(f"{at_mirror}, then {SINGLE.url} (ETag {MD5}")
    outcomes = [s.perform() for s in steps]
    assert routes.requested == [at_mirror]
    assert fetch.facts == {"source": "mirror", "fetched_from": at_mirror}
    assert any("ETag" in o and "not pinned" in o and "from the LAN mirror" in o for o in outcomes)
    assert (_data(tmp_path) / f"{NAME}{TIF}").read_bytes() == BODY
    assert backend.ledger.failed == {}


def test_a_3dep_tile_with_a_wrong_etag_from_the_mirror_falls_back_online_and_fails_offline(
    tmp_path: Path,
) -> None:
    at_mirror = mirror_url(BUNKER, MirrorPath("dem-3dep", NAME))
    wrong = b"w" * len(BODY)
    online = Routes({at_mirror: wrong, SINGLE.url: BODY})
    fetch = next(
        s
        for s in _steps(_mirrored(tmp_path / "on", online), manifest("dem-3dep", "usgs-3dep"))
        if s.kind == "fetch"
    )
    fetch.perform()
    assert online.requested == [at_mirror, SINGLE.url] and fetch.facts["source"] == "publisher"
    assert "ETag" in fetch.facts["mirror_failure"]
    off = Routes({at_mirror: wrong, SINGLE.url: BODY})
    backend = _mirrored(tmp_path / "off", off, offline=True)
    outcomes = [s.perform() for s in _steps(backend, manifest("dem-3dep", "usgs-3dep"))]
    assert any(o.startswith("FAILED, the rest continues") for o in outcomes)
    assert SINGLE.url not in off.requested
    assert not (_data(tmp_path / "off") / f"{NAME}{TIF}").exists()


def test_without_a_mirror_the_3dep_step_reads_as_it_did(tmp_path: Path) -> None:
    backend, fetcher = _pair(
        tmp_path, DemResolution(regions=(RegionTiles(*OCEANIA, (NAME,), 0),), fetch=(TILE,))
    )
    fetch = next(s for s in _steps(backend, manifest("dem-3dep", "usgs-3dep")) if s.kind == "fetch")
    assert fetch.sources == (TILE.url,) and "mirror" not in fetch.description
    assert fetch.detail == f"{TILE.url} (ETag {MD5}-2, 12 bytes)"
    outcome = fetch.perform()
    assert "mirror" not in outcome and fetch.facts == {"source": "publisher"}
    assert fetcher.mirrors == [MirrorPath("dem-3dep", NAME)]
