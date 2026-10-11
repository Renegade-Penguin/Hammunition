# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811
"""US Topo and 3DEP keep their repository ETags offline.  #381, Task 9.

The Bunker's record is only ever compared with the repository's own index or
list (the independent side); the bytes are verified by the fetch against that
repository ETag and size, never against the record.
"""

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
from hammunition.backends.topo import RegionQuads
from hammunition.backends.topo import render_record as render_quads
from hammunition.catalogue import PublisherUnavailable
from hammunition.copernicus import CopernicusError
from hammunition.fetch import Fetcher, VerificationError
from hammunition.resolution import CatalogueMiss, ResolutionContext
from hammunition.retry import RetryingProbe, RetryPolicy
from hammunition.terrain_plan import resolve_bare_earth
from hammunition.topo_bound import TopoBound
from hammunition.topo_plan import resolve_topo
from hammunition.usgs3dep import TileRow, check_tile, parse_tile_list
from hammunition.usgs3dep import tile_url as threedep_url
from hammunition.ustopo import Quad, UstopoError, check_quad, parse_index
from json_support import parse_one
from test_fetch import FakeTransport
from test_offline_context import apt_state, enrol_file_bunker, machine, run  # noqa: F401
from test_terrain_plan import RegionProbe, TileProbe

cli = importlib.import_module("hammunition.cli.main")
CATALOG = Path(__file__).resolve().parents[1] / "catalog"
TOPO = "usgs-ustopo"
DEM = "dem-3dep"
ETAG = "b" * 32
MULTI = "b" * 32 + "-2"
REGION = "north-america/us/delaware"
SLUG = "north-america-us-delaware"


def quad(**changes: Any) -> Quad:
    values: dict[str, Any] = dict(
        south=38.5, west=-75.5, north=38.625, east=-75.375, size=3, etag=ETAG,
        path="DE/Test_20260101",
    )  # fmt: skip
    values.update(changes)
    return Quad(**values)


def quad_row(q: Quad | None = None, **changes: Any) -> dict[str, object]:
    q = q or quad()
    values: dict[str, Any] = dict(
        publisher_check="etag-md5",
        publisher_digest=q.etag,
        publisher_url=q.url,
        publisher_name=f"{q.name}_TM_geo.tif",
        publisher_size=q.size,
        size=q.size,
    )
    values.update(changes)
    return artifact(TOPO, q.path, b"tif", **values)


def tile(**changes: Any) -> TileRow:
    values: dict[str, Any] = dict(name="USGS_13_n39w076", size=3, etag=MULTI)
    values.update(changes)
    return TileRow(**values)


def tile_row(t: TileRow | None = None, **changes: Any) -> dict[str, object]:
    t = t or tile()
    values: dict[str, Any] = dict(
        publisher_check="etag-md5",
        publisher_digest=t.etag,
        publisher_url=threedep_url(t.name),
        publisher_name=f"{t.name}.tif",
        publisher_size=t.size,
        size=t.size,
    )
    values.update(changes)
    return artifact(DEM, t.name, b"tif", **values)


class Down:
    """A publisher that never answers; counts the asks."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def head(self, url: str) -> tuple[int, int, str | None]:
        self.asked.append(url)
        raise TimeoutError("publisher down")


def retrying(probe: object) -> RetryingProbe:
    return RetryingProbe(probe, RetryPolicy(sleep=lambda _: None, notify=lambda _: None))


# -- check_quad / check_tile --------------------------------------------------------


def test_carried_etag_is_used_without_live_agreement(tmp_path: Path) -> None:
    q = quad()
    context = make_context(tmp_path, [quad_row(q)])
    probe = TileProbe({})
    check_quad(q, probe, context=context, unit=TOPO)
    assert probe.asked == []
    assert (q.size, q.etag) == (3, ETAG)
    assert (TOPO, q.path) in context.notes
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        check_quad(q, probe, context=make_context(tmp_path, []), unit=TOPO)


def test_3dep_keeps_repository_multipart_etag(tmp_path: Path) -> None:
    t = tile()
    context = make_context(tmp_path, [tile_row(t)])
    probe = TileProbe({})
    check_tile(t, probe, context=context, unit=DEM)
    assert t.etag == MULTI and t.size == 3
    assert probe.asked == [] and (DEM, t.name) in context.notes


@pytest.mark.parametrize(("rows", "reason"), [(False, "not on Bunker"), (True, "expected size")])
def test_3dep_catalogue_refuses_missing_or_wrong_size(
    tmp_path: Path, rows: bool, reason: str
) -> None:
    t = tile()
    context = make_context(tmp_path, [artifact(DEM, t.name, b"wrong")] if rows else [])
    probe = TileProbe({})
    with pytest.raises(CatalogueMiss, match=reason):
        check_tile(t, probe, context=context, unit=DEM)
    assert probe.asked == [] and not context.notes


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"publisher_digest": "c" * 32}, "publisher digest"),
        (
            {"publisher_digest": ETAG},
            "publisher digest",
        ),  # a single-part MD5 is not the multipart ETag
        ({"publisher_digest": None}, "publisher digest"),
        ({"size": 4}, "expected size"),
        ({"publisher_size": 4}, "publisher size"),
        ({"publisher_url": "https://example.invalid/x.tif"}, "publisher URL"),
    ],
    ids=lambda v: str(v)[:40],
)
def test_3dep_mismatch_against_the_carried_list_is_refused(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    t = tile()
    context = make_context(tmp_path, [tile_row(t, **changes)])
    with pytest.raises(CatalogueMiss, match=reason):
        check_tile(t, TileProbe({}), context=context, unit=DEM)
    assert not context.notes


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"publisher_digest": "c" * 32}, "publisher digest"),
        ({"publisher_digest": None}, "publisher digest"),
        ({"size": 4}, "expected size"),
        ({"publisher_size": 4}, "publisher size"),
        ({"publisher_url": "https://example.invalid/x.tif"}, "publisher URL"),
    ],
    ids=lambda v: str(v)[:40],
)
def test_ustopo_mismatch_against_the_carried_index_is_refused(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    q = quad()
    context = make_context(tmp_path, [quad_row(q, **changes)])
    with pytest.raises(CatalogueMiss, match=reason):
        check_quad(q, TileProbe({}), context=context, unit=TOPO)
    assert not context.notes


def test_a_context_without_a_unit_is_refused(tmp_path: Path) -> None:
    context = make_context(tmp_path, [quad_row(), tile_row()])
    with pytest.raises(UstopoError, match="unit name"):
        check_quad(quad(), TileProbe({}), context=context)
    with pytest.raises(CopernicusError, match="unit name"):
        check_tile(tile(), TileProbe({}), context=context)


def test_without_a_context_the_online_check_is_unchanged() -> None:
    q = quad()
    ok = TileProbe({q.url: (200, 3, f'"{ETAG}"')})
    check_quad(q, ok)
    assert ok.asked == [q.url]
    with pytest.raises(UstopoError, match="changed"):
        check_quad(q, TileProbe({q.url: (200, 3, '"' + "d" * 32 + '"')}))
    t = tile()
    with pytest.raises(CopernicusError, match="HTTP 404"):
        check_tile(t, TileProbe({threedep_url(t.name): (404, 0, None)}))


def test_online_a_404_is_an_answer_not_an_outage_and_never_falls_back(tmp_path: Path) -> None:
    q = quad()
    context = make_context(tmp_path, [quad_row(q)], offline=False)
    with pytest.raises(UstopoError, match="HTTP 404"):
        check_quad(q, retrying(TileProbe({q.url: (404, 0, None)})), context=context, unit=TOPO)
    assert not context.notes


def test_online_a_changed_etag_still_refuses_and_never_falls_back(tmp_path: Path) -> None:
    t = tile()
    context = make_context(tmp_path, [tile_row(t)], offline=False)
    heads: dict[str, tuple[int, int, str | None]] = {
        threedep_url(t.name): (200, 3, '"' + "d" * 32 + '-2"')
    }
    with pytest.raises(CopernicusError, match="changed"):
        check_tile(t, retrying(TileProbe(heads)), context=context, unit=DEM)
    assert not context.notes


def test_exhausted_retries_fall_back_to_the_record_and_say_so(tmp_path: Path) -> None:
    q = quad()
    context = make_context(tmp_path, [quad_row(q)], offline=False)
    down = Down()
    check_quad(q, retrying(down), context=context, unit=TOPO)
    assert len(down.asked) > 1
    assert "publisher unreachable; resolved from Bunker bunker" in context.notes[(TOPO, q.path)]


def test_exhausted_retries_with_no_record_keep_the_original_error(tmp_path: Path) -> None:
    context = make_context(tmp_path, [], offline=False)
    with pytest.raises(PublisherUnavailable, match="not answering right now"):
        check_tile(tile(), retrying(Down()), context=context, unit=DEM)
    assert not context.notes


# -- the byte check is the fetch's, against the repository's ETag and size -------------


def test_the_fetch_verifies_bytes_against_the_repository_etag_not_the_record(
    tmp_path: Path,
) -> None:
    """The recorded check proves the record agrees with the repository; what
    makes the downloaded bytes trusted is :meth:`Fetcher.fetch_etag` against
    the repository's ETag and size, which the resolver hands on unchanged."""
    body = b"tif"
    etag = hashlib.md5(body).hexdigest()
    t = tile(etag=etag, size=len(body))
    context = make_context(tmp_path / "ctx", [tile_row(t)])
    tiles = {t.name: t}
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / f"{SLUG}.tiles").write_text(render_record(RegionTiles(REGION, SLUG, (t.name,), 0)))
    got = resolve_bare_earth(
        [(REGION, SLUG)],
        installed=installed,
        tiles=tiles,
        region_probe=RegionProbe({}),
        tile_probe=TileProbe({}),
        context=context,
        unit=DEM,
    )
    (fetched,) = got.fetch
    assert (fetched.etag, fetched.size) == (etag, len(body))
    good = Fetcher(tmp_path / "good", transport=FakeTransport(body))
    assert good.fetch_etag(fetched.url, fetched.etag or "", expected_size=fetched.size).path
    for bad_body, match in ((b"tiX", "ETag"), (b"toolong!", "size")):
        bad = Fetcher(tmp_path / match, transport=FakeTransport(bad_body))
        with pytest.raises(VerificationError, match=match):
            bad.fetch_etag(fetched.url, fetched.etag or "", expected_size=fetched.size)


# -- the planners' workers -------------------------------------------------------------


def installed_quads(tmp_path: Path, *quads: Quad, bound: str = "all") -> Path:
    installed = tmp_path / "installed"
    installed.mkdir(exist_ok=True)
    (installed / f"{SLUG}.quads").write_text(render_quads(RegionQuads(REGION, SLUG, quads, bound)))
    return installed


def topo(tmp_path: Path, quads: tuple[Quad, ...], context: Any, **extra: Any) -> Any:
    index = parse_index(
        "\n".join(
            f"{q.south} {q.west} {q.north} {q.east} {q.size} {q.etag} {q.path}" for q in quads
        )
    )
    kwargs: dict[str, Any] = dict(
        installed=installed_quads(tmp_path, *quads),
        index=index,
        region_probe=RegionProbe({}),
        quad_probe=TileProbe({}),
        context=context,
        unit=TOPO,
    )
    kwargs.update(extra)
    return resolve_topo([(REGION, SLUG)], **kwargs)


def bare(
    tmp_path: Path, names: tuple[str, ...], tiles: dict[str, TileRow], context: Any, **extra: Any
) -> Any:
    installed = tmp_path / "installed"
    installed.mkdir(exist_ok=True)
    (installed / f"{SLUG}.tiles").write_text(render_record(RegionTiles(REGION, SLUG, names, 0)))
    kwargs: dict[str, Any] = dict(
        installed=installed,
        tiles=tiles,
        region_probe=RegionProbe({}),
        tile_probe=TileProbe({}),
        context=context,
        unit=DEM,
    )
    kwargs.update(extra)
    return resolve_bare_earth([(REGION, SLUG)], **kwargs)


def test_offline_topo_resolves_every_sheet_from_the_catalogue(tmp_path: Path) -> None:
    a, b = quad(path="DE/A_20260101"), quad(path="DE/B_20260101", west=-75.375, east=-75.25)
    context = make_context(tmp_path / "ctx", [quad_row(a), quad_row(b)])
    probe = TileProbe({})
    got, _notes = topo(tmp_path, (a, b), context, quad_probe=probe)
    assert [q.path for q in got.fetch] == [a.path, b.path]
    assert got.fetch[0] is not None and got.fetch[0].etag == ETAG
    assert probe.asked == []


def test_one_missing_sheet_defers_the_whole_selection_offline(tmp_path: Path) -> None:
    a, b = quad(path="DE/A_20260101"), quad(path="DE/B_20260101", west=-75.375, east=-75.25)
    context = make_context(tmp_path / "ctx", [quad_row(a)])
    with pytest.raises(CatalogueMiss, match="B_20260101"):
        topo(tmp_path, (a, b), context)


def test_one_missing_sheet_after_an_outage_defers_the_whole_selection(tmp_path: Path) -> None:
    a, b = quad(path="DE/A_20260101"), quad(path="DE/B_20260101", west=-75.375, east=-75.25)
    context = make_context(tmp_path / "ctx", [quad_row(a)], offline=False)
    seen: list[str] = []
    with pytest.raises(CatalogueMiss, match="not answering right now"):
        topo(
            tmp_path, (a, b), context,
            quad_probe=retrying(Down()), on_outage=lambda name, exc: seen.append(name),
        )  # fmt: skip
    assert seen == []


def test_an_outage_without_a_bunker_is_still_a_per_sheet_deferral(tmp_path: Path) -> None:
    a = quad()
    seen: list[str] = []
    got, _ = topo(
        tmp_path, (a,), ResolutionContext(),
        quad_probe=retrying(Down()), on_outage=lambda name, exc: seen.append(name),
    )  # fmt: skip
    assert seen == [a.name] and got.deferred == (a,)


def test_a_recorded_digest_that_differs_from_the_index_defers_not_fetches(tmp_path: Path) -> None:
    a = quad()
    context = make_context(tmp_path / "ctx", [quad_row(a, publisher_digest="c" * 32)])
    with pytest.raises(CatalogueMiss, match="publisher digest"):
        topo(tmp_path, (a,), context)


def test_radius_zero_selects_no_sheet_and_asks_nothing(tmp_path: Path) -> None:
    a = quad()
    context = make_context(tmp_path / "ctx", [])
    got, _ = topo(tmp_path, (a,), context, bound=TopoBound("none"))
    assert got.fetch == () and got.regions == ()


def test_an_installed_sheet_needs_no_bunker_lookup(tmp_path: Path) -> None:
    a = quad()
    context = make_context(tmp_path / "ctx", [])
    installed = installed_quads(tmp_path, a)
    (installed / f"{a.name}.tif").write_bytes(b"tif")
    got, _ = topo(tmp_path, (a,), context, installed=installed)
    assert got.current == (a,) and got.fetch == () and not context.notes


class SpyChecks:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def due(self, unit: str, item: str, path: Path, *, digest: str | None = None) -> bool:
        self.asked.append(item)
        return False


@pytest.mark.parametrize("kind", ["topo", "3dep"])
@pytest.mark.parametrize(("offline", "asked"), [(True, False), (False, True)])
def test_offline_skips_the_installed_recheck_and_online_keeps_it(
    tmp_path: Path, kind: str, offline: bool, asked: bool
) -> None:
    context = make_context(tmp_path / "ctx", [], offline=offline)
    checks = SpyChecks()
    if kind == "topo":
        a = quad()
        installed = installed_quads(tmp_path, a)
        (installed / f"{a.name}.tif").write_bytes(b"tif")
        topo(tmp_path, (a,), context, installed=installed, checks=checks)
    else:
        t = tile()
        (tmp_path / "installed").mkdir()
        (tmp_path / "installed" / f"{t.name}.tif").write_bytes(b"tif")
        bare(tmp_path, (t.name,), {t.name: t}, context, checks=checks)
    assert bool(checks.asked) is asked


def test_offline_3dep_resolves_every_tile_with_the_carried_etag(tmp_path: Path) -> None:
    t = tile()
    context = make_context(tmp_path / "ctx", [tile_row(t)])
    probe = TileProbe({})
    got = bare(tmp_path, (t.name,), {t.name: t}, context, tile_probe=probe)
    (fetched,) = got.fetch
    assert (fetched.etag, fetched.size, fetched.md5, fetched.sha256) == (MULTI, 3, None, None)
    assert probe.asked == []


def test_one_missing_3dep_tile_defers_the_whole_selection_offline(tmp_path: Path) -> None:
    a, b = tile(), tile(name="USGS_13_n40w076")
    context = make_context(tmp_path / "ctx", [tile_row(a)])
    with pytest.raises(CatalogueMiss, match="n40w076"):
        bare(tmp_path, (a.name, b.name), {a.name: a, b.name: b}, context)


def test_one_missing_3dep_tile_after_an_outage_defers_the_whole_selection(tmp_path: Path) -> None:
    a, b = tile(), tile(name="USGS_13_n40w076")
    context = make_context(tmp_path / "ctx", [tile_row(a)], offline=False)
    seen: list[str] = []
    with pytest.raises(CatalogueMiss, match="not answering right now"):
        bare(
            tmp_path, (a.name, b.name), {a.name: a, b.name: b}, context,
            tile_probe=retrying(Down()), on_outage=lambda name, exc: seen.append(name),
        )  # fmt: skip
    assert seen == []


def test_a_context_without_a_unit_is_refused_by_the_workers(tmp_path: Path) -> None:
    context = make_context(tmp_path, [])
    with pytest.raises(ValueError, match="unit"):
        resolve_topo(
            [], installed=tmp_path, index=parse_index("0 0 0.125 0.125 1 " + ETAG + " ZZ/ZZ_A_20240101"),
            region_probe=RegionProbe({}), quad_probe=TileProbe({}), context=context,
        )  # fmt: skip
    with pytest.raises(ValueError, match="unit"):
        resolve_bare_earth(
            [], installed=tmp_path, tiles=parse_tile_list("USGS_13_n39w076 3 " + ETAG),
            region_probe=RegionProbe({}), tile_probe=TileProbe({}), context=context,
        )  # fmt: skip


# -- through the CLI -----------------------------------------------------------------


def real_quad() -> Quad:
    index = (CATALOG / "data/ustopo-quads.txt").read_text()
    return next(q for q in parse_index(index).quads if q.path == "DE/DE_Bethany_Beach_20230530")


def real_tile() -> TileRow:
    row = next(
        r for r in parse_tile_list((CATALOG / "data/usgs-3dep-tiles.txt").read_text()).values()
        if r.name == "USGS_13_n39w076"
    )  # fmt: skip
    assert "-" in row.etag
    return row


def enrol(
    tmp_path: Path, rows: list[dict[str, object]], station: dict[str, Any] | None = None
) -> None:
    q, t = real_quad(), real_tile()
    selections = {
        "sheet-selection": (f"{REGION}.quads", render_quads(RegionQuads(REGION, SLUG, (q,), "all"))),
        "dem3dep-selection": (f"{REGION}.tiles", render_record(RegionTiles(REGION, SLUG, (t.name,), 0))),
    }  # fmt: skip
    inputs: list[dict[str, object]] = []
    bodies: dict[str, bytes] = {}
    for kind, (name, text) in selections.items():
        body, relative = text.encode(), f"inputs/{kind}/{name}"
        bodies[relative] = body
        inputs.append(
            {
                "kind": kind, "region": REGION, "name": name, "path": relative,
                "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                "fetched": "2026-10-06T03:00:00Z",
            }
        )  # fmt: skip
    export, _ = enrol_file_bunker(
        tmp_path, rows, inputs=inputs,
        station=station
        or {"map_regions": [REGION], "topo_all": True, "dem_source": "3dep"},
    )  # fmt: skip
    for relative, body in bodies.items():
        target = export / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)


def real_rows() -> list[dict[str, object]]:
    q, t = real_quad(), real_tile()
    return [
        quad_row(q, publisher_name=f"{q.name}_TM_geo.tif"),
        tile_row(t, publisher_name=f"{t.name}.tif", publisher_size=t.size),
    ]


def cli_setup(machine: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    apt_state(monkeypatch, installed="1.0")

    def maps(*args: object, **kwargs: object) -> MapResolution:
        return MapResolution(kept=(KeptRegion(REGION, SLUG, None, "installed"),))

    monkeypatch.setattr(cli, "resolve_map_regions", maps)
    down = Down()

    class DownOutline:
        def text(self, url: str) -> str:
            raise TimeoutError("outline link unavailable")

        def head(self, url: str) -> tuple[int, int, str | None]:
            raise AssertionError("never HEADs Geofabrik")

    monkeypatch.setattr(cli, "ustopo_probe", lambda: down)
    monkeypatch.setattr(cli, "UrllibProbe", DownOutline)
    monkeypatch.setattr(cli.POLICY, "sleep", lambda _: None)
    cli.POLICY.reset()
    return down.asked


UNITS = [TOPO, DEM]


@pytest.mark.parametrize("unit", UNITS)
def test_cli_retries_exhausted_then_the_record_lets_planning_succeed(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
) -> None:
    enrol(tmp_path, real_rows())
    asked = cli_setup(machine, monkeypatch)
    rc, out, err = run(capsys, CATALOG, "install", "--dry-run", "--json", unit)
    assert rc == 0, err
    assert len(asked) == cli.POLICY.attempts
    notes = parse_one(out)["install"]["region_notes"]
    assert any("publisher unreachable; resolved from Bunker bunker" in n for n in notes)


@pytest.mark.parametrize("unit", UNITS)
def test_cli_retries_exhausted_with_no_record_keeps_the_original_error(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
) -> None:
    enrol(tmp_path, [])
    asked = cli_setup(machine, monkeypatch)
    rc, _out, err = run(capsys, CATALOG, "install", "--dry-run", unit)
    assert rc != 0
    assert len(asked) == cli.POLICY.attempts
    assert "the publisher is not answering right now" in err


@pytest.mark.parametrize("unit", UNITS)
def test_cli_offline_plans_from_the_catalogue_with_zero_sockets(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
) -> None:
    enrol(tmp_path, real_rows())
    asked = cli_setup(machine, monkeypatch)
    attempts: list[object] = []

    def record(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise OSError("the network is forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", record)
    monkeypatch.setattr(socket, "getaddrinfo", record)
    rc, out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", "--json", unit)
    assert rc == 0, err
    assert asked == [] and attempts == []
    assert any(
        "offline; resolved from Bunker bunker" in n
        for n in parse_one(out)["install"]["region_notes"]
    )


@pytest.mark.parametrize("unit", UNITS)
def test_cli_a_missing_grid_square_still_defers_by_name_offline(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
) -> None:
    """D-035: with no grid square (and no topo_all/topo_regions) the radius
    cannot be drawn; nothing is invented, the unit defers by name, and the
    Bunker's records are not consulted to choose for it."""
    enrol(tmp_path, real_rows(), {"map_regions": [REGION], "dem_source": "3dep"})
    asked = cli_setup(machine, monkeypatch)
    _rc, out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", "--json", unit)
    assert asked == []
    text = out + err
    assert "no grid square is set" in text, text
    assert "resolved from Bunker" not in text


# -- narrowing under a new bound uses the current carried rows -------------------------

NEAR = (38.55, -75.45)  # inside the near sheet and tile; the far ones are tens of km away
CIRCLE = TopoBound("radius", 10, NEAR)
MONACO = "europe/monaco"
MONACO_SLUG = "europe-monaco"


def outline_for(west: float, south: float, east: float, north: float) -> bytes:
    ring = f" {west} {south}\n {east} {south}\n {east} {north}\n {west} {north}\n"
    return f"test\n1\n{ring}END\nEND\n".encode()


def bunker_inputs(
    tmp_path: Path, bodies: dict[str, bytes], rows: list[dict[str, object]]
) -> ResolutionContext:
    """A verified context holding artifact *rows* and signed inputs read through
    a file transport (monaco is the region the inputs are filed under; the
    resolvers take any region)."""
    from hammunition.mirror_transport import CatalogueInputs, MirrorTransport

    export = tmp_path / "export"
    entries: list[dict[str, object]] = []
    for kind, body in bodies.items():
        extension = "poly" if kind == "region-outline" else "tiles" if "3dep" in kind else "quads"
        name = f"{MONACO}.{extension}"
        relative = f"inputs/{kind}/{name}"
        entries.append(
            {
                "kind": kind, "region": MONACO, "name": name, "path": relative,
                "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                "fetched": "2026-10-06T03:00:00Z",
            }
        )  # fmt: skip
        target = export / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    context = make_context(tmp_path / "keys", rows, inputs=entries)
    context.inputs = CatalogueInputs(MirrorTransport(export.as_uri()))
    return context


def sheet_pair() -> tuple[Quad, Quad, Quad]:
    near = quad(path="DE/Near_20260101")
    stale = quad(path="DE/Near_20260101", etag="a" * 32)  # what the old record carried
    far = quad(path="DE/Far_20260101", west=-75.125, east=-75.0)
    return near, stale, far


@pytest.mark.parametrize("via", ["selection", "outline"])
def test_a_narrower_bound_picks_current_index_rows_and_a_verified_input(
    tmp_path: Path, via: str
) -> None:
    near, stale, far = sheet_pair()
    index = parse_index(
        "\n".join(
            f"{q.south} {q.west} {q.north} {q.east} {q.size} {q.etag} {q.path}" for q in (near, far)
        )
    )
    if via == "selection":
        bodies = {
            "sheet-selection": render_quads(
                RegionQuads(MONACO, MONACO_SLUG, (stale, far), "all")
            ).encode()
        }
    else:
        bodies = {"region-outline": outline_for(-75.49, 38.51, -75.01, 38.61)}
    # Only the current index row is on the Bunker with its current ETag: a
    # stale record row would not agree with it and would be refused.
    context = bunker_inputs(tmp_path, bodies, [quad_row(near)])
    probe = TileProbe({})
    got, _ = resolve_topo(
        [(MONACO, MONACO_SLUG)],
        installed=tmp_path / "installed",
        index=index,
        region_probe=RegionProbe({}),
        quad_probe=probe,
        bound=CIRCLE,
        context=context,
        unit=TOPO,
    )
    assert [(q.path, q.etag) for q in got.fetch] == [(near.path, ETAG)]
    assert got.regions[0].bound == CIRCLE.token
    assert probe.asked == []
    assert (
        "inputs",
        f"{'sheet-selection' if via == 'selection' else 'region-outline'}/{MONACO}",
    ) in context.notes


@pytest.mark.parametrize("via", ["selection", "outline"])
def test_a_narrower_bound_picks_3dep_tiles_from_a_verified_input(tmp_path: Path, via: str) -> None:
    near, far = tile(), tile(name="USGS_13_n42w076", etag="c" * 32 + "-2")
    between = [tile(name=f"USGS_13_n{n}w076") for n in (40, 41)]
    tiles = {t.name: t for t in (near, *between, far)}
    if via == "selection":
        body = render_record(RegionTiles(MONACO, MONACO_SLUG, (near.name, far.name), 0)).encode()
        bodies = {"dem3dep-selection": body}
    else:
        bodies = {"region-outline": outline_for(-75.9, 38.1, -75.1, 41.9)}
    context = bunker_inputs(tmp_path, bodies, [tile_row(near)])
    probe = TileProbe({})
    got = resolve_bare_earth(
        [(MONACO, MONACO_SLUG)],
        installed=tmp_path / "installed",
        tiles=tiles,
        region_probe=RegionProbe({}),
        tile_probe=probe,
        bound=CIRCLE,
        context=context,
        unit=DEM,
    )
    assert [(t.name, t.etag) for t in got.fetch] == [(near.name, MULTI)]
    assert got.regions[0].bound == CIRCLE.token
    assert probe.asked == []
