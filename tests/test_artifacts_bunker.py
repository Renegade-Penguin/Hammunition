# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Task 16 (A10): ``artifacts --json`` describes every payload, US Topo and
FSTopo sheet, 3DEP tile, selection input and git pin, with no station read.

Builds on ``tests/test_artifacts.py``'s fixtures (``_root``, ``_list``,
``Geofabrik``, ``Bucket``) rather than duplicating them.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest
import yaml

from hammunition.artifacts import (
    SelectionError,
    etag_entry,
    fetching_units,
    input_entry,
    input_regions,
    list_artifacts,
    list_git_pins,
    list_inputs,
    payload_entries,
    select_units,
)
from hammunition.fstopo import FstopoError, GatewayProbe
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import (
    BinaryInstall,
    DerivedDataInstall,
    GitInstall,
    NodeInstall,
    SourceInstall,
    VenvInstall,
)
from hammunition.payloads import payload_name
from hammunition.topo_bound import TopoBound
from json_support import REPO_ROOT
from test_artifacts import TILE_DE, Bucket, Geofabrik, _cli, _root

cli = importlib.import_module("hammunition.cli.main")

CATALOG = load_catalog(REPO_ROOT / "catalog" / "packages")
DE = "north-america/us/delaware"


# -- payload_entries (source/binary/venv/node/derived-tool/git) -------------


def test_all_source_payloads_use_the_install_route() -> None:
    root = Path(__file__).resolve().parents[1] / "catalog/packages"
    catalog = load_catalog(root)
    sources = [
        (m, b.install)
        for m in catalog.values()
        for b in m.install
        if isinstance(b.install, SourceInstall)
    ]
    assert sources
    for manifest, block in sources:
        entries = payload_entries(manifest.name, block, "licence not recorded in this manifest")
        assert len(entries) == 1
        assert entries[0].name == payload_name(block.source)
        assert entries[0].digest == block.source.sha256


def test_binary_venv_node_and_derived_tool_payloads_name_their_backend() -> None:
    binary = CATALOG["brouter"].install[0].install
    assert isinstance(binary, BinaryInstall)
    (entry,) = payload_entries("brouter", binary, "licence not recorded in this manifest")
    assert entry.name == payload_name(binary.artifact)
    assert entry.digest == binary.artifact.sha256
    assert entry.check == "sha256" and entry.deferred is None

    venv = CATALOG["artemis"].install[0].install
    assert isinstance(venv, VenvInstall) and venv.payload is not None
    (entry,) = payload_entries("artemis", venv, "fallback licence")
    assert entry.name == payload_name(venv.payload)
    # The venv block's own licence wins over the fallback when it has one.
    assert entry.licence == (venv.licence or "fallback licence")

    node = CATALOG["openhamclock"].install[0].install
    assert isinstance(node, NodeInstall)
    (entry,) = payload_entries("openhamclock", node, "licence not recorded in this manifest")
    assert entry.name == payload_name(node.artifact)

    derived = CATALOG["mapsforge-poi"].install[0].install
    assert isinstance(derived, DerivedDataInstall) and derived.tool is not None
    (entry,) = payload_entries("mapsforge-poi", derived, "unused: the tool carries its own")
    assert entry.name == payload_name(derived.tool.artifact)
    assert entry.size == derived.tool.size and entry.licence == derived.tool.licence

    git = CATALOG["comaps"].install[0].install
    assert isinstance(git, GitInstall)
    entries = payload_entries("comaps", git, "licence not recorded in this manifest")
    pinned_artifacts = [f.artifact for f in git.extra_files if f.artifact is not None]
    assert len(entries) == len(pinned_artifacts) and pinned_artifacts  # the two .mwm files
    assert {e.digest for e in entries} == {a.sha256 for a in pinned_artifacts}
    assert {e.name for e in entries} == {payload_name(a) for a in pinned_artifacts}


def test_a_derived_block_without_a_tool_has_no_payload() -> None:
    plain = CATALOG["osm-navit"].install[0].install
    assert isinstance(plain, DerivedDataInstall) and plain.tool is None
    assert payload_entries("osm-navit", plain, "licence not recorded in this manifest") == ()


# -- default fetching set and selection errors -------------------------------


def test_default_fetching_set_includes_payload_and_topo_units_but_not_apt_or_bare_derived() -> None:
    units = select_units(CATALOG, ())
    for present in (
        "brouter",
        "artemis",
        "openhamclock",
        "mapsforge-poi",
        "comaps",
        "usgs-ustopo",
        "usfs-fstopo",
        "dem-3dep",
    ):
        assert present in units, present
    # apt-only and derived-without-tool units stay idle, as before Task 16.
    assert "osm-navit" not in units and "navit" not in units


@pytest.mark.parametrize("bad", ["no-such-unit", "osm-navit"])
def test_an_unknown_or_idle_unit_still_refuses_beside_a_fetching_one(bad: str) -> None:
    with pytest.raises(SelectionError, match=bad):
        select_units(CATALOG, ("brouter", bad))


def test_fetching_units_is_a_subset_of_the_catalog_and_sorted() -> None:
    units = fetching_units(CATALOG)
    assert list(units) == sorted(units)
    assert all(u in CATALOG for u in units)


# -- etag_entry: raw ETag, nullable part_size (carried-forward naming) ------


@pytest.mark.parametrize("etag", ["a" * 32, "b" * 32 + "-94"])
def test_sheet_etag_is_raw_and_part_size_is_nullable(etag: str) -> None:
    for unit, name in [
        ("usgs-ustopo", "DE/fixture_20260101"),
        ("dem-3dep", "USGS_13_n39w076"),
    ]:
        row = etag_entry(
            unit, name, "https://example.invalid/sheet.tif", etag, 123, "public domain"
        )
        assert row.check == "etag-md5" and row.digest == etag
        assert row.part_size is None
        assert row.url is not None
        assert (
            etag_entry(
                unit, name, row.url, etag, 123, "public domain", part_size=5 * 1024 * 1024
            ).part_size
            == 5242880
        )


# -- inline input bound ------------------------------------------------------


def test_inline_input_bound_names_the_input() -> None:
    row = input_entry(
        "region-outline", "europe/monaco", "europe/monaco.poly", "x" * (8 * 1024 * 1024)
    )
    assert row.size == 8 * 1024 * 1024 and row.content is not None
    assert row.deferred is None and row.sha256 == hashlib.sha256(row.content.encode()).hexdigest()
    with pytest.raises(SelectionError) as caught:
        input_entry(
            "region-outline",
            "europe/monaco",
            "europe/monaco.poly",
            "é" * (4 * 1024 * 1024 + 1),
        )
    assert str(caught.value) == (
        "input region-outline/europe/monaco.poly is over 8 MiB; narrow the selection with --units"
    )


# -- unit filtering of inputs and git pins; licence carried verbatim --------


def test_units_filter_inputs_and_git_pins_and_carry_licence() -> None:
    catalog = load_catalog(Path(__file__).resolve().parents[1] / "catalog/packages")
    regions = ("europe/monaco", "north-america/us/delaware")
    assert input_regions(("ics-forms",), regions, catalog) == ()
    assert input_regions(("osm-regions",), regions, catalog) == regions
    git_units = [unit for unit in catalog if list_git_pins((unit,), catalog)]
    assert len(git_units) >= 2
    selected = git_units[0]
    catalog[selected] = catalog[selected].model_copy(update={"licence": "GPL-3.0-or-later"})
    pins = list_git_pins((selected,), catalog)
    assert pins and all(p.name.startswith(selected + "@") for p in pins)
    assert all(p.licence == "GPL-3.0-or-later" for p in pins)
    assert not list_git_pins(("ics-forms",), catalog)


def test_a_tag_with_no_recorded_commit_is_named_but_deferred() -> None:
    # acarsdec pins a tag ref with no `commit:` -- a real, uncontrived case
    # of D-024's "a tag carries an upstream signal" path.
    block = next(
        b.install for b in CATALOG["acarsdec"].install if isinstance(b.install, GitInstall)
    )
    assert block.commit is None
    (pin,) = list_git_pins(("acarsdec",), CATALOG)
    assert pin.commit is None and pin.name == f"acarsdec@{block.ref}"
    assert pin.deferred is not None and "no recorded commit" in pin.deferred


def test_a_pinned_commit_names_the_bundle_contract_name() -> None:
    from hammunition.gitbundles import bundle_name

    block = next(b.install for b in CATALOG["comaps"].install if isinstance(b.install, GitInstall))
    assert block.commit is not None
    (pin,) = list_git_pins(("comaps",), CATALOG)
    assert pin.commit == block.commit and pin.deferred is None
    assert pin.name == bundle_name("comaps", block.commit)
    assert pin.submodules == block.submodules


# -- the CLI never reads the station, even with --units narrowing both ------


def test_cli_units_filter_inline_arrays(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli.main(
            [
                "artifacts",
                "--units",
                "ics-forms",
                "--map-regions",
                "europe/monaco",
                "--json",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["inputs"] == []
    assert doc["git_pins"] == []


def test_cli_units_osm_regions_selects_every_input_region(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = _cli(
        monkeypatch,
        tmp_path,
        capsys,
        "--json",
        "--units",
        "osm-regions",
        "--map-regions",
        DE,
    )
    assert rc == 0
    doc = json.loads(out)
    regions_seen = {row["region"] for row in doc["inputs"]}
    assert regions_seen == {DE}
    assert {row["kind"] for row in doc["inputs"]} == {
        "region-outline",
        "tile-selection",
        "sheet-selection",
        "dem3dep-selection",
        "fstopo-selection",
    }
    assert doc["git_pins"] == []


def test_cli_never_reads_the_station_for_inputs_or_git_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from hammunition.station import Station, save_station

    save_station(
        Station(map_regions=(DE,)),
        path=tmp_path / "config" / "hammunition" / "station.yml",
    )
    rc, out, _ = _cli(monkeypatch, tmp_path, capsys, "--json", "--units", "ics-forms")
    assert rc == 0
    doc = json.loads(out)
    assert doc["map_regions"] == [] and doc["inputs"] == [] and doc["git_pins"] == []


# -- list_inputs reuses the exact install-time selection codecs -------------


def test_list_inputs_reuses_install_selection_codecs(tmp_path: Path) -> None:
    from hammunition.backends.dem import render_record as render_tiles
    from hammunition.backends.fstopo import render_record as render_sheets
    from hammunition.backends.topo import render_record as render_quads
    from hammunition.copernicus import load_tile_list
    from hammunition.fstopo import load_index as load_fstopo_index
    from hammunition.geofabrik import BASE
    from hammunition.terrain_plan import THREEDEP_LIST, TILE_LIST, region_tiles, resolve_bare_earth
    from hammunition.topo_bound import ALL as _ALL
    from hammunition.topo_plan import MemoProbe, region_quads, region_sheets
    from hammunition.usgs3dep import load_tile_list as load_3dep_list
    from hammunition.ustopo import load_index as load_ustopo_index
    from test_terrain_plan import RegionProbe

    root = Path(__file__).resolve().parents[1] / "catalog"
    region, slug = "north-america/us/delaware", "north-america-us-delaware"
    text = "test\n1\n -75.51 38.51\n -75.49 38.51\n -75.49 38.53\n -75.51 38.53\nEND\nEND\n"
    probe = MemoProbe(RegionProbe({f"{BASE}/{region}.poly": text}))
    rows = {
        row.kind: row for row in list_inputs((region,), catalog_root=root, probe=probe, bound=_ALL)
    }
    dem = region_tiles(
        region, slug, installed=None, tile_list=load_tile_list(root / TILE_LIST), probe=probe
    )
    bare = resolve_bare_earth(
        ((region, slug),),
        installed=None,
        tiles=load_3dep_list(root / THREEDEP_LIST),
        region_probe=probe,
        tile_probe=None,
        selection_only=True,
    )
    topo = region_quads(
        region,
        slug,
        installed=None,
        index=load_ustopo_index(root / "data/ustopo-quads.txt"),
        probe=probe,
        notes=[],
        bound=_ALL,
    )
    forest = region_sheets(
        region,
        slug,
        installed=None,
        index=load_fstopo_index(root / "data/fstopo-quads.txt"),
        probe=probe,
        notes=[],
        bound=_ALL,
    )
    expected = {
        "region-outline": text,
        "tile-selection": render_tiles(dem),
        "dem3dep-selection": render_tiles(bare.regions[0]),
        "sheet-selection": render_quads(topo),
        "fstopo-selection": render_sheets(forest),
    }
    assert {kind: row.content for kind, row in rows.items()} == expected
    for kind, row in rows.items():
        assert row.deferred is None and row.size == len(expected[kind].encode())
        assert row.sha256 == hashlib.sha256(expected[kind].encode()).hexdigest()


def test_list_inputs_defers_a_missing_carried_index_never_an_empty_record(
    tmp_path: Path,
) -> None:
    """A catalog root with the Copernicus list but no US Topo, FSTopo or
    3DEP index defers those three kinds by name rather than inventing an
    empty selection."""
    root = tmp_path / "catalog"
    (root / "data").mkdir(parents=True)
    (root / "data" / "copernicus-glo30-tiles.txt").write_text(f"{TILE_DE}\n")
    probe = Geofabrik()
    rows = {row.kind: row for row in list_inputs((DE,), catalog_root=root, probe=probe)}
    assert rows["region-outline"].deferred is None
    assert rows["tile-selection"].deferred is None
    for kind in ("sheet-selection", "dem3dep-selection", "fstopo-selection"):
        assert rows[kind].deferred is not None, kind
        assert rows[kind].content is None and rows[kind].sha256 is None


def test_list_inputs_defers_a_malformed_outline_never_an_empty_record(tmp_path: Path) -> None:
    class BadOutline:
        def head(self, url: str) -> tuple[int, int, str | None]:  # pragma: no cover
            raise AssertionError("not used")

        def text(self, url: str) -> str:
            return "not a poly file\n"

    root = _root(tmp_path)
    rows = {row.kind: row for row in list_inputs((DE,), catalog_root=root, probe=BadOutline())}
    assert rows["region-outline"].deferred is not None
    assert rows["region-outline"].content is None


def test_list_inputs_shares_one_outline_fetch_across_five_kinds(tmp_path: Path) -> None:
    root = _root(tmp_path)
    probe = Geofabrik()
    list_inputs((DE,), catalog_root=root, probe=probe)
    # Only one GET of the Delaware outline, however many of the five kinds
    # need it (the US Topo/FSTopo/3DEP indexes are absent from this fixture,
    # so they defer -- but each still asks the shared probe for the outline
    # before giving up, which is exactly what MemoProbe exists to collapse).
    asked_de_outline = [a for a in probe.asked if a.endswith(f"{DE}.poly")]
    assert len(asked_de_outline) == 1


# -- list_artifacts: topo, FSTopo and 3DEP, named by their carried-forward
# identity fields (quad.path / quad.name / row.name) --------------------


def _write_topo_fixtures(root: Path) -> None:
    (root / "data" / "usgs-3dep-tiles.txt").write_text(
        "USGS_13_n39w076 488000000 " + "a" * 32 + "\n"
    )
    (root / "data" / "ustopo-quads.txt").write_text(
        "38.0 -76.0 38.5 -75.5 100000000 " + "b" * 32 + " DE/fixture_20260101\n"
    )
    (root / "data" / "fstopo-quads.txt").write_text(
        "38.0 -76.0 38.5 -75.5 123456 26 DE FixtureCell\n"
    )


def _gateway_file(secoord: int) -> str:
    """The same shape the real gateway redirects to, as ``test_fstopo_plan.py``
    builds it: ``_is_gateway_geotiff`` checks the exact path shape."""
    from hammunition.fstopo import GATEWAY

    return f"{GATEWAY}data3/00000/fstopo/{secoord}.tiff"


class _FakeHead:
    """A gateway ``Head`` callable (Task 16): redirects ``map_url(secoord)``
    to its file for every *sizes* key, like the real gateway's two requests,
    and refuses anything else -- real ``GatewayProbe`` logic, fake transport,
    the pattern ``tests/test_fstopo_plan.py`` already uses."""

    def __init__(self, sizes: dict[int, int]) -> None:
        self.sizes = sizes
        self.asked: list[str] = []

    def __call__(self, url: str) -> tuple[int, int, str | None]:
        from hammunition.fstopo import map_url

        self.asked.append(url)
        for secoord, size in self.sizes.items():
            if url == map_url(secoord):
                return 302, 0, _gateway_file(secoord)
            if url == _gateway_file(secoord):
                return 200, size, None
        raise FstopoError(f"{url} could not be reached: offline")


def _gateway(sizes: dict[int, int]) -> GatewayProbe:
    return GatewayProbe(_FakeHead(sizes))


def test_3dep_and_ustopo_artifacts_use_the_etag_route_named_by_carried_forward_fields(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _write_topo_fixtures(root)
    entries = list_artifacts(
        ("dem-3dep", "usgs-ustopo"),
        regions=(DE,),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=root,
        today=__import__("datetime").date(2026, 9, 29),
        region_probe=Geofabrik(),
        tile_probe=Bucket(),
    )
    threedep = [e for e in entries if e.unit == "dem-3dep"]
    ustopo = [e for e in entries if e.unit == "usgs-ustopo"]
    (tile,) = threedep
    assert tile.name == "USGS_13_n39w076" and tile.check == "etag-md5"
    assert tile.digest == "a" * 32 and tile.part_size is None
    (sheet,) = ustopo
    assert sheet.name == "DE/fixture_20260101" and sheet.check == "etag-md5"
    assert sheet.digest == "b" * 32


def test_fstopo_artifact_is_named_by_quad_name_pinned_sha256_or_unverified_fetch(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _write_topo_fixtures(root)
    entries = list_artifacts(
        ("usfs-fstopo",),
        regions=(DE,),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=root,
        today=__import__("datetime").date(2026, 9, 29),
        region_probe=Geofabrik(),
        tile_probe=Bucket(),
        gateway=_gateway({123456: 999}),
    )
    (sheet,) = entries
    assert sheet.name == "DE_FixtureCell_123456_26"
    assert sheet.check == "unverified-fetch" and sheet.digest is None
    assert sheet.url == _gateway_file(123456) and sheet.size == 999

    pins_root = _root(tmp_path / "pinned")
    _write_topo_fixtures(pins_root)
    (pins_root / "data" / "fstopo-pins.yaml").write_text(
        yaml.safe_dump({"pins": [{"secoord": 123456, "size": 999, "sha256": "c" * 64}]})
    )
    pinned_entries = list_artifacts(
        ("usfs-fstopo",),
        regions=(DE,),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=pins_root,
        today=__import__("datetime").date(2026, 9, 29),
        region_probe=Geofabrik(),
        tile_probe=Bucket(),
        gateway=_gateway({123456: 999}),
    )
    (pinned_sheet,) = pinned_entries
    assert pinned_sheet.check == "sha256" and pinned_sheet.digest == "c" * 64


def test_fstopo_defers_by_name_with_no_gateway_when_a_region_needs_one(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write_topo_fixtures(root)
    entries = list_artifacts(
        ("usfs-fstopo",),
        regions=(DE,),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=root,
        today=__import__("datetime").date(2026, 9, 29),
        region_probe=Geofabrik(),
        tile_probe=Bucket(),
        gateway=None,
    )
    (entry,) = entries
    assert entry.deferred is not None and "gateway" in entry.deferred


def test_missing_carried_topo_index_defers_the_unit_by_name(tmp_path: Path) -> None:
    root = tmp_path / "catalog"  # no usgs-3dep-tiles.txt / ustopo-quads.txt / fstopo-quads.txt
    (root / "data").mkdir(parents=True)
    for units in (("dem-3dep",), ("usgs-ustopo",), ("usfs-fstopo",)):
        entries = list_artifacts(
            units,
            regions=(DE,),
            freshness="yearly",
            catalog=CATALOG,
            catalog_root=root,
            today=__import__("datetime").date(2026, 9, 29),
            region_probe=Geofabrik(),
            tile_probe=Bucket(),
            gateway=_gateway({}),
        )
        (entry,) = entries
        assert entry.deferred is not None, units


def test_a_topo_bound_of_none_selects_nothing_and_asks_no_outline(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write_topo_fixtures(root)
    none_bound = TopoBound("none")
    probe = Geofabrik()
    entries = list_artifacts(
        ("usgs-ustopo",),
        regions=(DE,),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=root,
        today=__import__("datetime").date(2026, 9, 29),
        region_probe=probe,
        tile_probe=Bucket(),
        bound=none_bound,
    )
    assert entries == ()
    assert not any(a.endswith(".poly") for a in probe.asked)
