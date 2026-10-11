# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811
"""Copernicus tiles from repository pins or the recorded publisher MD5.  #381, Task 8."""

from __future__ import annotations

import hashlib
import importlib
import socket
from pathlib import Path
from typing import Any

import pytest

from bunker_fixtures import artifact, make_context
from hammunition.backends.dem import RegionTiles, render_record
from hammunition.backends.regions import KeptRegion, MapResolution
from hammunition.catalogue import CatalogueError, PublisherUnavailable
from hammunition.copernicus import TilePin, resolve_tile, tile_url
from hammunition.resolution import CatalogueMiss
from hammunition.retry import RetryingProbe, RetryPolicy
from hammunition.terrain_plan import resolve_terrain
from json_support import parse_one
from test_offline_context import apt_state, enrol_file_bunker, machine, run  # noqa: F401
from test_terrain_plan import LIST, MD5, A, B, RegionProbe, TileProbe

cli = importlib.import_module("hammunition.cli.main")
CATALOG = Path(__file__).resolve().parents[1] / "catalog"
UNIT = "dem-copernicus"
BODY = b"tif"
REGION = "europe/monaco"
SLUG = "europe-monaco"


def row(name: str = A, /, **changes: object) -> dict[str, object]:
    values: dict[str, object] = dict(
        publisher_name=f"{name}.tif",
        publisher_size=3,
        publisher_check="etag-md5",
        publisher_digest="b" * 32,
        publisher_url=tile_url(name),
    )
    values.update(changes)
    return artifact(UNIT, name, BODY, **values)


def pin(name: str = A, **changes: Any) -> TilePin:
    values: dict[str, Any] = dict(
        name=name, size=3, sha256=hashlib.sha256(BODY).hexdigest(), md5=MD5
    )
    values.update(changes)
    return TilePin(**values)


class DownTiles:
    """A tile publisher that never answers; counts the asks."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def head(self, url: str) -> tuple[int, int, str | None]:
        self.asked.append(url)
        raise TimeoutError("publisher down")


def retrying(probe: object) -> RetryingProbe:
    policy = RetryPolicy(sleep=lambda _: None, notify=lambda _: None)
    return RetryingProbe(probe, policy)


# -- recorded_tile / resolve_tile ------------------------------------------------


def test_offline_tile_uses_publisher_md5_not_bunker_sha256(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row()])
    probe = TileProbe({})
    got = resolve_tile(A, pins={}, probe=probe, unit=UNIT, context=context)
    assert got.md5 == "b" * 32 and got.sha256 is None and got.size == 3
    assert got.url == tile_url(A)
    assert probe.asked == []
    assert "offline; resolved from Bunker bunker" in context.notes[(UNIT, A)]


def test_pinned_tile_takes_digest_and_size_from_the_pin_and_never_heads(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(publisher_digest=None, publisher_check="sha256")])
    probe = TileProbe({})
    got = resolve_tile(A, pins={A: pin()}, probe=probe, unit=UNIT, context=context)
    assert (got.sha256, got.md5, got.size) == (pin().sha256, None, 3)
    assert probe.asked == []


def test_pinned_tile_refuses_a_catalogue_digest_that_differs(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row()])
    with pytest.raises(CatalogueMiss, match="sha256 pin"):
        resolve_tile(
            A, pins={A: pin(sha256="c" * 64)}, probe=TileProbe({}), unit=UNIT, context=context
        )
    assert not context.notes


def test_pinned_tile_refuses_a_catalogue_size_that_differs(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row()])
    with pytest.raises(CatalogueMiss, match="expected size"):
        resolve_tile(A, pins={A: pin(size=4)}, probe=TileProbe({}), unit=UNIT, context=context)
    assert not context.notes


@pytest.mark.parametrize(
    "changes",
    [
        {"publisher_digest": "b" * 32 + "-3"},
        {"publisher_digest": "b" * 31},
        {"publisher_digest": None},
        {"publisher_check": "unverified-fetch"},
        {"publisher_check": "sha256"},
        {"publisher_url": tile_url(B)},
        {"publisher_name": f"{B}.tif"},
        {"publisher_size": 4},
    ],
    ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()),
)
def test_a_bad_unpinned_record_is_refused(tmp_path: Path, changes: dict[str, object]) -> None:
    context = make_context(tmp_path, [row(**changes)])
    with pytest.raises(CatalogueMiss):
        resolve_tile(A, pins={}, probe=TileProbe({}), unit=UNIT, context=context)
    assert not context.notes


def test_a_zero_publisher_size_never_reaches_the_resolver(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError, match="publisher_size: must be positive"):
        make_context(tmp_path, [row(publisher_size=0)])


def test_a_sized_record_with_no_publisher_name_never_reaches_the_resolver(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError, match="publisher_name"):
        make_context(tmp_path, [row(publisher_name=None)])


def test_a_context_without_a_unit_is_refused(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row()])
    with pytest.raises(ValueError, match="unit"):
        resolve_tile(A, pins={}, probe=TileProbe({}), context=context)


def test_without_a_context_resolve_tile_is_unchanged() -> None:
    probe = TileProbe({tile_url(A): (200, 7, f'"{MD5}"')})
    got = resolve_tile(A, pins={}, probe=probe)
    assert (got.md5, got.size) == (MD5, 7)
    assert probe.asked == [tile_url(A)]


def test_online_publisher_is_used_while_it_answers(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row()], offline=False)
    probe = TileProbe({tile_url(A): (200, 7, f'"{MD5}"')})
    got = resolve_tile(A, pins={}, probe=probe, unit=UNIT, context=context)
    assert got.md5 == MD5 and not context.notes


def test_exhausted_retries_fall_back_to_the_record_and_say_so(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row()], offline=False)
    down = DownTiles()
    got = resolve_tile(A, pins={}, probe=retrying(down), unit=UNIT, context=context)
    assert got.md5 == "b" * 32
    assert len(down.asked) > 1
    assert "publisher unreachable; resolved from Bunker bunker" in context.notes[(UNIT, A)]


def test_exhausted_retries_with_no_record_keep_the_original_error(tmp_path: Path) -> None:
    context = make_context(tmp_path, [], offline=False)
    with pytest.raises(PublisherUnavailable, match="not answering right now"):
        resolve_tile(A, pins={}, probe=retrying(DownTiles()), unit=UNIT, context=context)
    assert not context.notes


# -- the terrain worker ------------------------------------------------------------


def installed_record(tmp_path: Path, *tiles: str) -> Path:
    installed = tmp_path / "installed"
    installed.mkdir(exist_ok=True)
    (installed / f"{SLUG}.tiles").write_text(render_record(RegionTiles(REGION, SLUG, tiles, 0)))
    return installed


def terrain(tmp_path: Path, tiles: tuple[str, ...], context: Any, **extra: Any) -> Any:
    kwargs: dict[str, Any] = dict(
        installed=installed_record(tmp_path, *tiles),
        tile_list=LIST,
        pins={},
        region_probe=RegionProbe({}),
        tile_probe=TileProbe({}),
        context=context,
        unit=UNIT,
    )
    kwargs.update(extra)
    return resolve_terrain([(REGION, SLUG)], **kwargs)


def test_offline_terrain_resolves_every_tile_from_the_catalogue(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(A), row(B)])
    probe = TileProbe({})
    got = terrain(tmp_path, (A, B), context, tile_probe=probe)
    assert [t.name for t in got.fetch] == [A, B]
    assert all(t.md5 == "b" * 32 for t in got.fetch)
    assert probe.asked == []


def test_a_pinned_tile_offline_skips_the_reachability_head(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(A)])
    probe = TileProbe({})
    got = terrain(tmp_path, (A,), context, pins={A: pin()}, tile_probe=probe)
    assert got.fetch[0].sha256 == pin().sha256
    assert probe.asked == []


def test_a_pinned_tile_online_still_heads_once(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(A)], offline=False)
    probe = TileProbe({tile_url(A): (200, 3, None)})
    got = terrain(tmp_path, (A,), context, pins={A: pin()}, tile_probe=probe)
    assert got.fetch[0].sha256 == pin().sha256
    assert probe.asked == [tile_url(A)]
    assert not context.notes


def test_pinned_reachability_falls_back_only_after_exhaustion(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(A)], offline=False)
    down = DownTiles()
    got = terrain(tmp_path, (A,), context, pins={A: pin()}, tile_probe=retrying(down))
    assert got.fetch[0].sha256 == pin().sha256
    assert len(down.asked) > 1
    assert "publisher unreachable; resolved from Bunker bunker" in context.notes[(UNIT, A)]


def test_a_reachability_404_is_an_answer_not_an_outage(tmp_path: Path) -> None:
    from hammunition.copernicus import CopernicusError

    context = make_context(tmp_path, [row(A)], offline=False)
    probe = TileProbe({tile_url(A): (404, 0, None)})
    with pytest.raises(CopernicusError, match="HTTP 404"):
        terrain(tmp_path, (A,), context, pins={A: pin()}, tile_probe=retrying(probe))
    assert not context.notes


def test_a_pinned_tile_whose_record_has_another_digest_is_not_used(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(A)])
    with pytest.raises(CatalogueMiss, match="sha256 pin"):
        terrain(tmp_path, (A,), context, pins={A: pin(sha256="d" * 64)})


def test_one_missing_tile_defers_the_whole_selection_offline(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(A)])
    with pytest.raises(CatalogueMiss, match=B):
        terrain(tmp_path, (A, B), context)


def test_one_missing_tile_after_an_outage_defers_the_whole_selection(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row(A)], offline=False)
    seen: list[str] = []
    with pytest.raises(CatalogueMiss, match="not answering right now"):
        terrain(
            tmp_path,
            (A, B),
            context,
            tile_probe=retrying(DownTiles()),
            on_outage=lambda name, exc: seen.append(name),
        )
    assert seen == []


def test_an_outage_without_a_bunker_is_still_a_per_tile_deferral(tmp_path: Path) -> None:
    from hammunition.resolution import ResolutionContext

    seen: list[str] = []
    got = terrain(
        tmp_path,
        (A, B),
        ResolutionContext(),
        tile_probe=retrying(DownTiles()),
        on_outage=lambda name, exc: seen.append(name),
    )
    assert sorted(seen) == [A, B] and got.deferred == (A, B)


def test_an_installed_tile_with_its_record_needs_no_bunker_lookup(tmp_path: Path) -> None:
    context = make_context(tmp_path, [])
    installed = installed_record(tmp_path, A)
    (installed / f"{A}.tif").write_bytes(BODY)
    got = terrain(tmp_path, (A,), context, installed=installed)
    assert got.current == (A,) and got.fetch == ()
    assert not context.notes


class SpyChecks:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def due(self, unit: str, item: str, path: Path, *, digest: str | None = None) -> bool:
        self.asked.append(item)
        return False


@pytest.mark.parametrize(("offline", "asked"), [(True, []), (False, [A])])
def test_offline_skips_the_installed_recheck_and_online_keeps_it(
    tmp_path: Path, offline: bool, asked: list[str]
) -> None:
    context = make_context(tmp_path, [], offline=offline)
    installed = installed_record(tmp_path, A)
    (installed / f"{A}.tif").write_bytes(BODY)
    checks = SpyChecks()
    terrain(tmp_path, (A,), context, installed=installed, checks=checks)
    assert checks.asked == asked


# -- through the CLI ---------------------------------------------------------------


def real_tile() -> str:
    for line in (CATALOG / "data/copernicus-glo30-tiles.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            return line.strip()
    raise AssertionError("no tile in the carried list")


def enrol(tmp_path: Path, tile: str, rows: list[dict[str, object]]) -> None:
    body = render_record(RegionTiles(REGION, SLUG, (tile,), 0)).encode()
    relative = f"inputs/tile-selection/{REGION}.tiles"
    export, _ = enrol_file_bunker(
        tmp_path,
        rows,
        inputs=[
            {
                "kind": "tile-selection",
                "region": REGION,
                "name": f"{REGION}.tiles",
                "path": relative,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
                "fetched": "2026-10-06T03:00:00Z",
            }
        ],
        station={"map_regions": [REGION]},
    )
    target = export / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(body)


def cli_setup(machine: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    apt_state(monkeypatch, installed="1.0")

    def maps(*args: object, **kwargs: object) -> MapResolution:
        return MapResolution(kept=(KeptRegion(REGION, SLUG, None, "installed"),))

    monkeypatch.setattr(cli, "resolve_map_regions", maps)
    asked: list[str] = []

    class Down:
        def head(self, url: str) -> tuple[int, int, str | None]:
            asked.append(url)
            raise TimeoutError("publisher down")

    class DownOutline:
        def text(self, url: str) -> str:
            raise TimeoutError("outline link unavailable")

        def head(self, url: str) -> tuple[int, int, str | None]:
            raise AssertionError("terrain never HEADs Geofabrik")

    monkeypatch.setattr(cli, "S3Probe", Down)
    monkeypatch.setattr(cli, "UrllibProbe", DownOutline)
    monkeypatch.setattr(cli.POLICY, "sleep", lambda _: None)
    cli.POLICY.reset()
    return asked


def test_cli_retries_exhausted_then_the_record_lets_planning_succeed(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tile = real_tile()
    enrol(tmp_path, tile, [row(tile)])
    asked = cli_setup(machine, monkeypatch)
    rc, out, err = run(capsys, CATALOG, "install", "--dry-run", "--json", UNIT)
    assert rc == 0, err
    assert len(asked) == cli.POLICY.attempts
    notes = parse_one(out)["install"]["region_notes"]
    assert any("publisher unreachable; resolved from Bunker bunker" in n for n in notes)


def test_cli_retries_exhausted_with_no_record_keeps_the_original_error(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tile = real_tile()
    enrol(tmp_path, tile, [])
    asked = cli_setup(machine, monkeypatch)
    rc, _out, err = run(capsys, CATALOG, "install", "--dry-run", UNIT)
    assert rc != 0
    assert len(asked) == cli.POLICY.attempts
    assert "the publisher is not answering right now" in err
    assert "last answer after 3 attempts: timed out" in err


def test_cli_offline_terrain_plans_from_the_catalogue_with_zero_sockets(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tile = real_tile()
    enrol(tmp_path, tile, [row(tile)])
    asked = cli_setup(machine, monkeypatch)
    attempts: list[object] = []

    def record(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise OSError("the network is forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", record)
    monkeypatch.setattr(socket, "getaddrinfo", record)
    rc, out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", "--json", UNIT)
    assert rc == 0, err
    assert asked == [] and attempts == []
    assert any(
        "offline; resolved from Bunker bunker" in n
        for n in parse_one(out)["install"]["region_notes"]
    )
