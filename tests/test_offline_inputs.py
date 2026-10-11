# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811
"""Verified selection inputs use the engine's installed-record codecs."""

from __future__ import annotations

import dataclasses
import gc
import hashlib
import importlib
import socket
from pathlib import Path

import pytest

from bunker_fixtures import make_context
from hammunition.backends.dem import RegionTiles
from hammunition.backends.dem import read_record as read_tiles
from hammunition.backends.dem import render_record as render_tiles
from hammunition.backends.fstopo import RegionSheets
from hammunition.backends.fstopo import read_record as read_sheets
from hammunition.backends.fstopo import render_record as render_sheets
from hammunition.backends.regions import KeptRegion, MapResolution
from hammunition.backends.topo import RegionQuads
from hammunition.backends.topo import render_record as render_quads
from hammunition.fstopo import FsIndex
from hammunition.mirror_transport import CatalogueInputs, MirrorTransport
from hammunition.plan import InstallPlan
from hammunition.resolution import CatalogueMiss, ResolutionContext
from hammunition.terrain_plan import region_bare_earth, region_tiles
from hammunition.topo_bound import ALL, TopoBound
from hammunition.topo_plan import region_quads, region_sheets
from hammunition.ustopo import Quad, QuadIndex
from json_support import parse_one
from test_offline_context import apt_state, enrol_file_bunker, machine, run  # noqa: F401
from test_terrain_plan import MD5, OUTLINE, A, RegionProbe

REGION = "europe/monaco"
SLUG = "europe-monaco"


def inputs(tmp_path: Path, bodies: dict[str, bytes], *, offline: bool = True) -> ResolutionContext:
    rows: list[dict[str, object]] = []
    export = tmp_path / "export"
    for kind, body in bodies.items():
        extension = (
            "poly"
            if kind == "region-outline"
            else "tiles"
            if kind in ("tile-selection", "dem3dep-selection")
            else "quads"
        )
        name = f"{REGION}.{extension}"
        relative = f"inputs/{kind}/{name}"
        rows.append(
            {
                "kind": kind,
                "region": REGION,
                "name": name,
                "path": relative,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
                "fetched": "2026-10-06T03:00:00Z",
            }
        )
        target = export / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    context = make_context(tmp_path / "keys", [], inputs=rows, offline=offline)
    context.inputs = CatalogueInputs(MirrorTransport(export.as_uri()))
    return context


def test_empty_selection_is_valid(tmp_path: Path) -> None:
    entry = RegionTiles(REGION, SLUG, (), 1)
    context = inputs(tmp_path, {"tile-selection": render_tiles(entry).encode()})
    assert context.selection("tile-selection", REGION, read_tiles) == entry


@pytest.mark.parametrize("body", [b"broken", b"\xff"])
def test_malformed_selection_refuses(tmp_path: Path, body: bytes) -> None:
    context = inputs(tmp_path, {"tile-selection": body})
    with pytest.raises(CatalogueMiss, match=r"invalid selection|UTF-8"):
        context.selection("tile-selection", REGION, read_tiles)


@pytest.mark.parametrize("replacement", [b"x", b"x" * 100])
def test_input_digest_and_size_checked(tmp_path: Path, replacement: bytes) -> None:
    context = inputs(tmp_path, {"region-outline": b"y"})
    (tmp_path / "export/inputs/region-outline/europe/monaco.poly").write_bytes(replacement)
    with pytest.raises(CatalogueMiss, match=r"sha256|size|bound|larger"):
        context.input_bytes("region-outline", REGION)


def test_missing_input(tmp_path: Path) -> None:
    context = inputs(tmp_path, {})
    with pytest.raises(CatalogueMiss, match="region-outline/europe/monaco"):
        context.input_bytes("region-outline", REGION)


def test_tiles_offline_from_selection(tmp_path: Path) -> None:
    entry = RegionTiles(REGION, SLUG, (A,), 1)
    context = inputs(tmp_path, {"tile-selection": render_tiles(entry).encode()})
    assert (
        region_tiles(
            REGION,
            SLUG,
            installed=tmp_path / "installed",
            tile_list=frozenset({A}),
            probe=RegionProbe({}),
            context=context,
        )
        == entry
    )


def test_bounded_selection_is_not_reused_for_all(tmp_path: Path) -> None:
    quad = Quad(0.0, 0.0, 0.125, 0.125, 3, "a" * 32, "DE/Test_20260101")
    narrow = render_quads(RegionQuads(REGION, SLUG, (), TopoBound("none").token))
    outline = "test\n1\n 0.01 0.01\n 0.10 0.01\n 0.10 0.10\n 0.01 0.10\nEND\nEND\n"
    context = inputs(
        tmp_path, {"sheet-selection": narrow.encode(), "region-outline": outline.encode()}
    )
    got = region_quads(
        REGION,
        SLUG,
        installed=tmp_path / "installed",
        index=QuadIndex.of([quad]),
        probe=RegionProbe({}),
        notes=[],
        bound=ALL,
        context=context,
    )
    assert got.quads == (quad,) and got.bound == "all"


@pytest.mark.parametrize("body", [b"bad polygon", b"\xff"])
def test_outline_invalid(tmp_path: Path, body: bytes) -> None:
    context = inputs(tmp_path, {"region-outline": body})
    with pytest.raises(CatalogueMiss, match=r"outline|UTF-8"):
        context.outline(REGION, RegionProbe({}), base="https://example.invalid")


@pytest.mark.parametrize(
    "kind", ["tile-selection", "dem3dep-selection", "sheet-selection", "fstopo-selection"]
)
def test_all_selection_codecs_and_resolvers(
    tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("offline selection opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    if kind in ("tile-selection", "dem3dep-selection"):
        body = render_tiles(RegionTiles(REGION, SLUG, (), 2)).encode()
    elif kind == "sheet-selection":
        body = render_quads(RegionQuads(REGION, SLUG, ())).encode()
    else:
        body = render_sheets(RegionSheets(REGION, SLUG, ())).encode()
    context = inputs(tmp_path, {kind: body})
    probe = RegionProbe({})
    if kind == "tile-selection":
        assert (
            region_tiles(
                REGION,
                SLUG,
                installed=tmp_path,
                tile_list=frozenset(),
                probe=probe,
                context=context,
            ).tiles
            == ()
        )
    elif kind == "dem3dep-selection":
        assert (
            region_bare_earth(
                REGION, SLUG, installed=tmp_path, tiles={}, region_probe=probe, context=context
            ).tiles
            == ()
        )
    elif kind == "sheet-selection":
        assert (
            region_quads(
                REGION,
                SLUG,
                installed=tmp_path,
                index=QuadIndex.of([]),
                probe=probe,
                notes=[],
                context=context,
            ).quads
            == ()
        )
    else:
        assert context.selection(kind, REGION, read_sheets) == RegionSheets(REGION, SLUG, ())
        assert (
            region_sheets(
                REGION,
                SLUG,
                installed=tmp_path,
                index=FsIndex.of([]),
                probe=probe,
                notes=[],
                context=context,
            ).quads
            == ()
        )
    assert probe.asked == []


@pytest.mark.parametrize("kind", ["sheet-selection", "fstopo-selection", "dem3dep-selection"])
def test_none_bypasses_even_malformed_selection(tmp_path: Path, kind: str) -> None:
    context = inputs(tmp_path, {kind: b"broken"})
    bound = TopoBound("none")
    probe = RegionProbe({})
    if kind == "sheet-selection":
        assert not region_quads(
            REGION,
            SLUG,
            installed=tmp_path,
            index=QuadIndex.of([]),
            probe=probe,
            notes=[],
            bound=bound,
            context=context,
        ).quads
    elif kind == "fstopo-selection":
        assert not region_sheets(
            REGION,
            SLUG,
            installed=tmp_path,
            index=FsIndex.of([]),
            probe=probe,
            notes=[],
            bound=bound,
            context=context,
        ).quads
    else:
        assert not region_bare_earth(
            REGION,
            SLUG,
            installed=tmp_path,
            tiles={},
            region_probe=probe,
            bound=bound,
            context=context,
        ).tiles
    assert not context.notes


class DownProbe:
    def head(self, url: str) -> tuple[int, int, str | None]:
        raise AssertionError("no payload checks for an empty selection")

    def text(self, url: str) -> str:
        raise TimeoutError("outline link unavailable")


@pytest.mark.parametrize(
    "unit,kind",
    [
        ("dem-copernicus", "tile-selection"),
        ("dem-3dep", "dem3dep-selection"),
        ("usgs-ustopo", "sheet-selection"),
        ("usfs-fstopo", "fstopo-selection"),
    ],
)
@pytest.mark.parametrize("record", [True, False])
@pytest.mark.parametrize("profile", [False, True])
def test_cli_exhausted_outline_retries_fall_back_or_preserve_error(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
    kind: str,
    record: bool,
    profile: bool,
) -> None:
    cli = importlib.import_module("hammunition.cli.main")
    apt_state(monkeypatch, installed="1.0")
    original_resolve = cli.resolve

    def resolve_profile(*args: object, **kwargs: object) -> InstallPlan:
        plan: InstallPlan = original_resolve(*args, **kwargs)
        return dataclasses.replace(
            plan,
            packages=tuple(
                dataclasses.replace(p, requested_by=("profile:test",)) if p.name == unit else p
                for p in plan.packages
            ),
        )

    if profile:
        monkeypatch.setattr(cli, "resolve", resolve_profile)

    if kind in ("tile-selection", "dem3dep-selection"):
        body = render_tiles(RegionTiles(REGION, SLUG, (), 1)).encode()
    elif kind == "sheet-selection":
        body = render_quads(RegionQuads(REGION, SLUG, ())).encode()
    else:
        body = render_sheets(RegionSheets(REGION, SLUG, ())).encode()
    extension = "tiles" if "dep" in kind or kind == "tile-selection" else "quads"
    relative = f"inputs/{kind}/{REGION}.{extension}"
    rows = (
        [
            {
                "kind": kind,
                "region": REGION,
                "name": f"{REGION}.{extension}",
                "path": relative,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
                "fetched": "2026-10-06T03:00:00Z",
            }
        ]
        if record
        else []
    )
    export, _ = enrol_file_bunker(
        tmp_path,
        [],
        inputs=rows,
        station={"map_regions": [REGION], "topo_all": True, "dem_source": "3dep"},
    )
    if record:
        target = export / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(body)

    def maps(*args: object, **kwargs: object) -> MapResolution:
        return MapResolution(kept=(KeptRegion(REGION, SLUG, None, "installed"),))

    monkeypatch.setattr(cli, "resolve_map_regions", maps)
    attempts: list[str] = []

    class Tracked(DownProbe):
        def text(self, url: str) -> str:
            attempts.append(url)
            return super().text(url)

    monkeypatch.setattr(cli, "UrllibProbe", Tracked)
    monkeypatch.setattr(cli.POLICY, "sleep", lambda _: None)
    cli.POLICY.reset()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("test opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    rc, out, err = run(
        capsys,
        Path(__file__).resolve().parents[1] / "catalog",
        "install",
        "--dry-run",
        *(("--json",) if record or profile else ()),
        unit,
    )
    # Outage tracebacks can retain the JSON stderr Tee; collect while capsys is open.
    gc.collect()
    assert len(attempts) == cli.POLICY.attempts
    if record:
        assert rc == 0, err
        assert any(
            "publisher unreachable; resolved from Bunker bunker" in n
            for n in parse_one(out)["install"]["region_notes"]
        )
    elif profile:
        assert rc == 0, err
        doc = parse_one(out)["install"]
        assert any(
            d["subject"] == unit and "will not install this unit" in d["what"]
            for d in doc["deferrals"]
        )
    else:
        assert rc != 0
        assert "the publisher is not answering right now" in err
        assert "last answer after 3 attempts: timed out" in err


def test_verified_outline_selects_tiles_without_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("offline outline opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    context = inputs(tmp_path, {"region-outline": OUTLINE.encode()})
    got = region_tiles(
        REGION,
        SLUG,
        installed=tmp_path,
        tile_list=frozenset({A}),
        probe=RegionProbe({}),
        context=context,
    )
    assert got.tiles == (A,) and got.unpublished == 3


@pytest.mark.parametrize("kind", ["sheet-selection", "fstopo-selection", "dem3dep-selection"])
def test_other_malformed_codecs_refuse(tmp_path: Path, kind: str) -> None:
    context = inputs(tmp_path, {kind: b"not a record"})
    probe = RegionProbe({})
    with pytest.raises(CatalogueMiss, match="invalid selection"):
        if kind == "sheet-selection":
            region_quads(
                REGION,
                SLUG,
                installed=tmp_path,
                index=QuadIndex.of([]),
                probe=probe,
                notes=[],
                context=context,
            )
        elif kind == "fstopo-selection":
            region_sheets(
                REGION,
                SLUG,
                installed=tmp_path,
                index=FsIndex.of([]),
                probe=probe,
                notes=[],
                context=context,
            )
        else:
            region_bare_earth(
                REGION, SLUG, installed=tmp_path, tiles={}, region_probe=probe, context=context
            )


def test_input_size_mismatch_and_failure_not_cached(tmp_path: Path) -> None:
    context = inputs(tmp_path, {"region-outline": b"abc"})
    path = tmp_path / "export/inputs/region-outline/europe/monaco.poly"
    path.write_bytes(b"ab")
    with pytest.raises(CatalogueMiss):
        context.input_bytes("region-outline", REGION)
    path.write_bytes(b"abc")
    assert context.input_bytes("region-outline", REGION) == b"abc"
    path.unlink()
    assert context.input_bytes("region-outline", REGION) == b"abc"


def test_inputs_are_bounded_before_transport(tmp_path: Path) -> None:
    class ForbiddenTransport:
        def read(self, relative: str, *, max_bytes: int) -> bytes:
            pytest.fail("oversized input reached transport")

    context = inputs(tmp_path, {})
    row: dict[str, object] = {
        "kind": "region-outline",
        "region": REGION,
        "name": f"{REGION}.poly",
        "path": f"inputs/region-outline/{REGION}.poly",
        "sha256": "a" * 64,
        "size": 8 * 1024 * 1024 + 1,
        "fetched": "2026-10-06T03:00:00Z",
    }
    context = make_context(tmp_path / "large", [], inputs=[row])
    context.inputs = ForbiddenTransport()
    with pytest.raises(CatalogueMiss, match="8 MiB"):
        context.input_bytes("region-outline", REGION)


@pytest.mark.parametrize("malicious", [False, True])
def test_signed_inputs_reject_duplicate_identity_and_malicious_path(
    tmp_path: Path, malicious: bool
) -> None:
    row: dict[str, object] = {
        "kind": "region-outline",
        "region": REGION,
        "name": f"{REGION}.poly",
        "path": "../outside" if malicious else f"inputs/region-outline/{REGION}.poly",
        "sha256": "a" * 64,
        "size": 1,
        "fetched": "2026-10-06T03:00:00Z",
    }
    from hammunition.catalogue import CatalogueError

    with pytest.raises(CatalogueError, match=r"path|duplicate"):
        make_context(tmp_path, [], inputs=[row] if malicious else [row, row])


@pytest.mark.parametrize("stale", [False, True])
def test_selection_uses_current_carried_quad_or_recomputes(tmp_path: Path, stale: bool) -> None:
    selected = Quad(0.0, 0.0, 0.125, 0.125, 3, "a" * 32, "DE/Test_20260101")
    current = Quad(
        0.0, 0.0, 0.125, 0.125, 7, "b" * 32, "DE/Test_20260201" if stale else selected.path
    )
    outline = "test\n1\n 0.01 0.01\n 0.10 0.01\n 0.10 0.10\n 0.01 0.10\nEND\nEND\n"
    context = inputs(
        tmp_path,
        {
            "sheet-selection": render_quads(RegionQuads(REGION, SLUG, (selected,))).encode(),
            "region-outline": outline.encode(),
        },
    )
    got = region_quads(
        REGION,
        SLUG,
        installed=tmp_path,
        index=QuadIndex.of([current]),
        probe=RegionProbe({}),
        notes=[],
        context=context,
    )
    assert got.quads == (current,)
    assert (("inputs", f"region-outline/{REGION}") in context.notes) == stale


def test_current_installed_record_precedes_bunker(tmp_path: Path) -> None:
    entry = RegionTiles(REGION, SLUG, (A,), 1)
    (tmp_path / f"{SLUG}.tiles").write_text(render_tiles(entry))
    context = inputs(tmp_path, {"tile-selection": b"broken"})
    assert (
        region_tiles(
            REGION,
            SLUG,
            installed=tmp_path,
            tile_list=frozenset({A}),
            probe=RegionProbe({}),
            context=context,
        )
        == entry
    )
    assert context.notes == {}


def test_matching_sha256_with_wrong_recorded_size_refuses(tmp_path: Path) -> None:
    body = b"abc"
    row: dict[str, object] = {
        "kind": "region-outline",
        "region": REGION,
        "name": f"{REGION}.poly",
        "path": f"inputs/region-outline/{REGION}.poly",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body) + 1,
        "fetched": "2026-10-06T03:00:00Z",
    }
    context = make_context(tmp_path / "keys", [], inputs=[row])
    target = tmp_path / str(row["path"])
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    context.inputs = CatalogueInputs(MirrorTransport(tmp_path.as_uri()))
    with pytest.raises(CatalogueMiss, match="expected size"):
        context.input_bytes("region-outline", REGION)


def test_fallback_resets_unpublished_when_narrowing_all_selection_keeps_nothing(
    tmp_path: Path,
) -> None:
    """Fix 1: fallback must reset unpublished to 0 when narrowing an 'all'
    3DEP selection to a radius bound that keeps no tiles (issue #232, D-068).

    The test uses:
    - A 3DEP tile USGS_13_n41w075 (at coordinates 40°N, -75°E)
    - An 'all' selection containing that tile with unpublished=2
    - A 1 km radius bound centered at 50°N, 10°E (6,400+ km away)
    - The narrowed result should be empty and unpublished reset to 0
    """
    from hammunition.usgs3dep import TileRow

    # A USGS 3DEP tile far from where the radius bound is centered
    tile = "USGS_13_n41w075"
    tile_row = TileRow(tile, 39_000_000, MD5)

    # Create a selection with bound="all" and unpublished=2
    # This simulates a stored selection covering this tile plus 1 other tile
    entry = RegionTiles(REGION, SLUG, (tile,), 2, "all")
    context = inputs(tmp_path, {"dem3dep-selection": render_tiles(entry).encode()})

    # Narrow to a 1 km radius centered at 50°N, 10°E
    # This is 6,400+ km from the tile, so it will keep no tiles
    bound = TopoBound("radius", radius_km=1, centre=(50.0, 10.0))

    got = region_bare_earth(
        REGION,
        SLUG,
        installed=tmp_path,
        tiles={tile: tile_row},
        region_probe=RegionProbe({}),
        bound=bound,
        context=context,
    )
    # Before the fix: unpublished=2, tiles=()
    # After the fix: unpublished=0, tiles=()
    assert got.tiles == ()
    assert got.unpublished == 0


def test_region_tiles_without_context_accepts_stale_record(tmp_path: Path) -> None:
    """Fix 2: Without a Bunker context, the installed-record fast path
    must work exactly as before Task 7: a record naming a since-unpublished
    tile is returned, with no outline fetch."""
    # Create a record with a tile that is NOT in the current tile_list
    entry = RegionTiles(REGION, SLUG, (A,), 1, "all")
    (tmp_path / f"{SLUG}.tiles").write_text(render_tiles(entry))
    # Call region_tiles with no context and an empty tile_list
    # (so the record's tile A is no longer available)
    probe = RegionProbe({})
    got = region_tiles(
        REGION,
        SLUG,
        installed=tmp_path,
        tile_list=frozenset(),  # A is not in this list anymore
        probe=probe,
        context=None,  # No Bunker context
    )
    # Without context, the stale record should be returned as-is
    assert got == entry
    # And no outline should be fetched
    assert probe.asked == []


def test_region_bare_earth_without_context_accepts_stale_record(tmp_path: Path) -> None:
    """Fix 2: Without a Bunker context, the installed-record fast path
    in region_bare_earth must accept a stale record, not fetch the outline."""
    # Create a record with a tile that is NOT in the current tiles dict
    entry = RegionTiles(REGION, SLUG, (A,), 1, "all")
    (tmp_path / f"{SLUG}.tiles").write_text(render_tiles(entry))
    # Call region_bare_earth with no context and empty tiles
    probe = RegionProbe({})
    got = region_bare_earth(
        REGION,
        SLUG,
        installed=tmp_path,
        tiles={},  # A is not in this dict anymore
        region_probe=probe,
        context=None,  # No Bunker context
    )
    # Without context, the stale record should be returned as-is
    assert got == entry
    # And no outline should be fetched
    assert probe.asked == []
