# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Repeater sources beyond the operator's export: the parsers, the
OpenStreetMap filter and the cross-source merge.  D-074.

Every row is synthetic: N0CALL, N0TST, Springfield IL. The Direwolf log is
Direwolf 1.8.1's own output for the synthetic packets in
``fixtures/repeaters/direwolf-packets.txt`` (``gen_packets`` into
``direwolf -l``, 2026-10-01), so its columns are measured, not recalled.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from hammunition import repeater_sources as rs
from hammunition import repeaters
from hammunition.repeaters import Repeater, RepeaterInputError, merge, read_input

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "repeaters"


def _reasons(parsed: repeaters.ParsedInput) -> dict[str, int]:
    return {s.reason: s.count for s in parsed.skipped}


# --- Open Repeater ----------------------------------------------------------------


def test_open_repeater_is_read_with_both_offset_units_and_its_skips() -> None:
    parsed = rs.read_open_repeater(FIXTURES / "open-repeater.json")
    assert parsed.format == repeaters.OPEN_REPEATER and parsed.read == 5
    first, second = parsed.rows
    assert (first.callsign, first.output_hz, first.offset_hz) == ("N0CALL", 146_940_000, -600_000)
    assert first.tone == "100.0" and first.mode == "FM (analog)" and first.place == "Springfield"
    assert first.status == "active" and first.updated == "2026-09-26"
    # 5000 is kHz (a magnitude of 50 or more), the callsign is upper-cased,
    # a DCS code is written as one.
    assert (second.callsign, second.offset_hz, second.tone) == ("N0TST", 5_000_000, "DCS 023")
    assert second.status == "inactive"
    assert _reasons(parsed) == {
        "no usable position": 1,
        "no callsign": 1,
        "no usable output frequency": 1,
    }


def test_open_repeater_bulk_imports_without_positions_are_skipped_not_guessed(
    tmp_path: Path,
) -> None:
    """The 2026-10-13 list grew from 461 to 769 repeaters when Austria and the
    Netherlands arrived as bulk imports: no coordinates, no offset, a free-text
    mode, an integer frequency. They must be counted as skipped for want of a
    position, never placed, while a positioned row beside them is kept."""
    bulk = {
        "id": "x",
        "callsign": "n0call-a",
        "frequency": 438,
        "offset": None,
        "ctcss": None,
        "dcs": None,
        "mode": "FSK 9k6bps",
        "band": "70cm (430 MHz)",
        "country": "Nowhere",
        "city": "TOWN",
        "lat": None,
        "lng": None,
        "status": "active",
        "last_verified": "2026-10-08",
    }
    placed = {**bulk, "callsign": "N0TST", "lat": 3.171, "lng": 101, "offset": -5000}
    body = {
        "source": "Open Repeater",
        "license": "CC0",
        "count": 3,
        "repeaters": [bulk, bulk, placed],
    }
    path = tmp_path / "open-repeater.json"
    path.write_text(json.dumps(body))
    parsed = rs.read_open_repeater(path)
    assert parsed.read == 3
    assert [r.callsign for r in parsed.rows] == ["N0TST"]
    assert parsed.rows[0].offset_hz == -5_000_000
    assert _reasons(parsed) == {"no usable position": 2}
    assert rs.open_repeater_date(path) == date(2026, 10, 8)


def test_open_repeater_is_dated_by_its_newest_verification() -> None:
    parsed = rs.read_open_repeater(FIXTURES / "open-repeater.json")
    assert rs.open_repeater_date(FIXTURES / "open-repeater.json") == date(2026, 9, 28)
    assert parsed.rows  # the date is the data's, not the run's


def test_open_repeater_without_any_verification_date_falls_back_to_the_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "or.json"
    path.write_text('{"source":"Open Repeater","license":"CC0","repeaters":[]}')
    import os

    os.utime(path, (1_790_000_000, 1_790_000_000))
    assert rs.open_repeater_date(path) == date.fromtimestamp(1_790_000_000)


@pytest.mark.parametrize(
    "body",
    [
        '{"source":"Someone else","repeaters":[]}',
        "[1, 2]",
        "{not json",
        '{"source":"Open Repeater"}',
    ],
)
def test_what_is_not_open_repeaters_file_is_refused(tmp_path: Path, body: str) -> None:
    path = tmp_path / "x.json"
    path.write_text(body)
    with pytest.raises(RepeaterInputError, match="not Open Repeater's"):
        rs.read_open_repeater(path)


# --- ETCC ----------------------------------------------------------------------------


def test_etcc_csv_is_read_with_its_locator_precision_named() -> None:
    raw = (FIXTURES / "etcc.csv").read_bytes()
    parsed = rs.parse_etcc(raw, "https://example.invalid/etcc.csv")
    assert parsed.format == repeaters.ETCC and parsed.read == 4
    first, second = parsed.rows
    assert (first.callsign, first.output_hz, first.offset_hz) == ("N0CALL", 145_725_000, -600_000)
    assert first.tone == "100.0" and first.mode == "FM"
    assert "channel RV58" in first.notes
    assert "locator EN59" in first.notes and "square's centre" in first.notes
    assert second.mode == "DMR, D-STAR" and second.offset_hz == -9_000_000
    assert "locator EN59QT" in second.notes and "square's centre" not in second.notes
    assert _reasons(parsed) == {"no usable position": 1, "no usable output frequency": 1}


def test_what_is_not_the_etcc_csv_is_refused() -> None:
    with pytest.raises(RepeaterInputError, match="not the ETCC"):
        rs.parse_etcc(b"a,b,c\n1,2,3\n", "https://example.invalid/etcc.csv")


# --- Brandmeister -----------------------------------------------------------------


def test_brandmeister_keeps_only_six_digit_repeaters_whose_tx_and_rx_differ() -> None:
    raw = (FIXTURES / "brandmeister.json").read_bytes()
    parsed = rs.parse_brandmeister(raw, "https://example.invalid/v2/device")
    assert parsed.format == repeaters.BRANDMEISTER and parsed.read == 6
    (only,) = parsed.rows
    assert (only.callsign, only.output_hz, only.offset_hz) == ("N0CALL", 442_100_000, 5_000_000)
    assert only.mode == "DMR" and "colour code 1" in only.notes
    assert only.updated == "2026-10-01 01:45:26"
    assert _reasons(parsed) == {
        rs.HOTSPOT_ID: 2,
        rs.HOTSPOT_SIMPLEX: 1,
        "no usable position": 1,
        "no usable output frequency": 1,
    }


def test_a_hotspots_position_never_reaches_a_kept_row() -> None:
    raw = (FIXTURES / "brandmeister.json").read_bytes()
    parsed = rs.parse_brandmeister(raw, "https://example.invalid/v2/device")
    for row in parsed.rows:
        assert (round(row.lat, 2), round(row.lon, 2)) not in {
            (39.71, -89.61),
            (39.72, -89.62),
            (39.73, -89.63),
        }


def test_what_is_not_brandmeisters_device_list_is_refused() -> None:
    with pytest.raises(RepeaterInputError, match="not Brandmeister's"):
        rs.parse_brandmeister(b'{"devices": []}', "https://example.invalid/v2/device")


# --- Direwolf ------------------------------------------------------------------------


def test_direwolf_log_keeps_objects_in_a_repeater_band() -> None:
    parsed = rs.read_direwolf_logs([FIXTURES / "direwolf-2026-10-01.csv"])
    assert parsed.format == repeaters.DIREWOLF and parsed.read == 8
    by_name = {r.label or r.callsign: r for r in parsed.rows}
    assert set(by_name) == {"146.940NE", "147.000-N", "N0TST-R", "N0TST", "443.100+N", "224.580"}
    named = by_name["146.940NE"]
    assert named.callsign == "" and named.output_hz == 146_940_000
    assert named.offset_hz == -600_000 and named.tone == "100.0"
    assert "sent by N0TST" in named.notes and named.updated == "2026-10-01T13:32:05Z"
    assert by_name["443.100+N"].offset_hz == 5_000_000
    dstar = by_name["N0TST"]
    assert dstar.output_hz == 442_875_000 and dstar.offset_hz is None
    assert "D-Star" in dstar.notes
    assert by_name["N0TST-R"].callsign == "N0TST-R"
    assert _reasons(parsed) == {"not an APRS object": 2}


def test_direwolf_logs_heard_again_merge_to_the_newest_hearing() -> None:
    parsed = rs.read_direwolf_logs(
        [FIXTURES / "direwolf-2026-10-01.csv", FIXTURES / "direwolf-2026-10-02.csv"]
    )
    assert parsed.read == 11
    assert _reasons(parsed) == {
        "not an APRS object": 2,
        "frequency outside the repeater bands": 1,
        "no usable position": 1,
    }
    rows, merged = merge(parsed.rows)
    assert merged == 1
    kept = next(r for r in rows if r.label == "146.940NE")
    assert "net Tuesdays" in kept.notes  # the second day's hearing won
    assert rs.direwolf_date(parsed.rows) == date(2026, 10, 2)


def test_a_file_that_is_not_a_direwolf_log_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.log"
    path.write_text("hello,world\n")
    with pytest.raises(RepeaterInputError, match="not a Direwolf log"):
        rs.read_direwolf_logs([path])


# --- D-064's import refuses the new shapes by name ----------------------------------------


@pytest.mark.parametrize(
    ("name", "says"),
    [
        ("open-repeater.json", "--from-open-repeater"),
        ("etcc.csv", "fetch-etcc"),
        ("direwolf-2026-10-01.csv", "--from-direwolf-log"),
    ],
)
def test_the_export_import_names_the_route_for_a_new_source(name: str, says: str) -> None:
    with pytest.raises(RepeaterInputError, match=says):
        read_input(FIXTURES / name)


# --- OpenStreetMap ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "hz"),
    [
        ("145350000", 145_350_000),  # Hz
        ("146.685", 146_685_000),  # MHz
        ("146685", 146_685_000),  # kHz
        ("1466100000", 146_610_000),  # Hz written x10
        ("146.94 MHz", 146_940_000),
        ("146940 kHz", 146_940_000),
        ("146,940", 146_940_000),
        ("1293000000", 1_293_000_000),
        ("146.94;147.00", 146_940_000),
        ("3.5", None),
        ("3.5 MHz", None),
        ("abc", None),
        ("", None),
    ],
)
def test_an_osm_frequency_is_read_in_whatever_unit_it_lands_in_a_band(
    text: str, hz: int | None
) -> None:
    assert rs.osm_frequency(text) == hz


@pytest.mark.parametrize(
    ("shift", "freq_in", "offset"),
    [
        ("-600 kHz", "", -600_000),
        ("-0.6", "", -600_000),
        ("+5", "", 5_000_000),
        ("-600", "", -600_000),
        ("-600000", "", -600_000),
        ("+5 MHz", "", 5_000_000),
        ("0.6", "", None),
        ("0.6", "147.600", 600_000),  # no sign: the input frequency says which way
        ("", "147.600", 600_000),
        ("", "", None),
    ],
)
def test_an_osm_offset_takes_its_sign_or_the_input_frequency(
    shift: str, freq_in: str, offset: int | None
) -> None:
    assert rs.osm_offset(shift, 147_000_000, freq_in) == offset


def test_osm_xml_gives_nodes_ways_and_counts_what_is_not_a_repeater() -> None:
    parsed = rs.read_osm_xml((FIXTURES / "osm-repeaters.osm").read_text(), Path("x.osm"))
    assert parsed.format == repeaters.OSM and parsed.read == 8
    by_hz = {r.output_hz: r for r in parsed.rows}
    assert set(by_hz) == {146_940_000, 146_610_000, 147_000_000, 146_685_000}
    tower = by_hz[146_940_000]
    assert tower.callsign == "N0CALL" and tower.offset_hz == -600_000 and tower.tone == "100.0"
    assert tower.mode == "FM" and tower.place == "Springfield water tower"
    x10 = by_hz[146_610_000]
    assert x10.callsign == "N0TST" and x10.offset_hz is None and "direction not given" in x10.notes
    variant = by_hz[147_000_000]
    assert variant.offset_hz == 600_000 and variant.label == "147.000"
    way = by_hz[146_685_000]
    assert (round(way.lat, 4), round(way.lon, 4)) == (39.81, -89.61)
    assert _reasons(parsed) == {
        "not marked as a repeater": 2,
        "no usable output frequency": 1,
        "a relation, which has no single position": 1,
    }


def test_osm_xml_with_a_doctype_is_refused() -> None:
    with pytest.raises(RepeaterInputError, match="DOCTYPE"):
        rs.read_osm_xml('<!DOCTYPE osm [<!ENTITY a "b">]><osm/>', Path("x.osm"))


def test_installed_extracts_are_listed_with_their_snapshot_dates(tmp_path: Path) -> None:
    folder = tmp_path / "share" / "hammunition" / "data" / "osm-regions"
    folder.mkdir(parents=True)
    (folder / "springfield.osm.pbf").write_bytes(b"x")
    (folder / "springfield.osm.pbf.source").write_text("260901\n")
    (folder / "shelbyville.osm.pbf").write_bytes(b"x")
    (folder / "shelbyville.osm.pbf.source").write_text("260815\n")
    (folder / "notes.txt").write_text("not a region")
    extracts = rs.installed_extracts(tmp_path)
    assert [p.name for p, _ in extracts] == ["shelbyville.osm.pbf", "springfield.osm.pbf"]
    assert rs.extracts_date(extracts) == date(2026, 8, 15)


def test_the_osm_filter_runs_osmium_as_given(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        Path(argv[argv.index("-o") + 1]).write_text((FIXTURES / "osm-repeaters.osm").read_text())
        return subprocess.CompletedProcess(argv, 0, "", "")

    pbf = tmp_path / "springfield.osm.pbf"
    pbf.write_bytes(b"x")
    parsed = rs.filter_extract(pbf, tmp_path, run=run)
    assert seen[0][:3] == ["osmium", "tags-filter", str(pbf)]
    assert "nwr/communication:amateur_radio*" in seen[0]
    assert "nwr/communication:ham_radio*" in seen[0]
    assert len(parsed.rows) == 4


def test_a_failed_osmium_is_named(tmp_path: Path) -> None:
    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    pbf = tmp_path / "springfield.osm.pbf"
    pbf.write_bytes(b"x")
    with pytest.raises(RepeaterInputError, match="osmium tags-filter failed"):
        rs.filter_extract(pbf, tmp_path, run=run)


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool is not installed")
def test_real_osmium_over_a_tiny_synthetic_extract(tmp_path: Path) -> None:
    pbf = tmp_path / "springfield.osm.pbf"
    subprocess.run(
        ["osmium", "cat", str(FIXTURES / "osm-repeaters.osm"), "-o", str(pbf), "--overwrite"],
        check=True,
        capture_output=True,
    )
    parsed = rs.filter_extract(pbf, tmp_path)
    assert {r.output_hz for r in parsed.rows} == {
        146_940_000,
        146_610_000,
        147_000_000,
        146_685_000,
    }
    assert _reasons(parsed)["not marked as a repeater"] == 2


# --- across sources --------------------------------------------------------------------


def _row(source: str, **over: object) -> Repeater:
    base: dict[str, object] = {
        "callsign": "N0CALL",
        "output_hz": 146_940_000,
        "lat": 39.80,
        "lon": -89.64,
        "source": source,
    }
    base.update(over)
    return Repeater(**base)  # type: ignore[arg-type]


def _layers(*rows: Repeater) -> list[list[Repeater]]:
    """Each row its own layer."""
    return [[row] for row in rows]


def test_the_same_machine_from_two_sources_merges_on_callsign_or_distance() -> None:
    rows, merges = rs.cross_merge(
        _layers(
            _row(repeaters.OSM, callsign="", label="146.940", lat=39.81, lon=-89.63),
            _row(repeaters.HEARHAM, lat=39.85, lon=-89.70, tone="100.0"),
            _row(repeaters.HAND, offset_hz=None),
            _row(repeaters.BRANDMEISTER, callsign="N0TST", lat=39.83, lon=-89.64),
        )
    )
    # The hand list leads; hearham joins by callsign 6 km away; OSM by
    # 0.01 degree; Brandmeister's other callsign 0.03 degree away stays apart.
    assert merges == 2
    assert [r.source for r in rows] == [repeaters.HAND, repeaters.BRANDMEISTER]
    lead = rows[0]
    assert (lead.lat, lead.lon) == (39.80, -89.64)  # the best source's position
    assert lead.tone == "100.0"  # filled from hearham
    assert lead.also == (repeaters.HEARHAM, repeaters.OSM)
    assert "also listed by" in lead.description()


def test_a_different_frequency_never_merges() -> None:
    rows, merges = rs.cross_merge(
        _layers(_row(repeaters.HAND), _row(repeaters.ETCC, output_hz=146_970_000))
    )
    assert merges == 0 and len(rows) == 2


def test_the_same_callsign_far_away_is_another_site() -> None:
    """D-064 measured 1,050 callsign-and-frequency keys in hearham naming
    more than one site; the callsign test is bounded at 0.25 degree."""
    rows, merges = rs.cross_merge(
        _layers(_row(repeaters.HAND), _row(repeaters.HEARHAM, lat=40.90, lon=-89.10))
    )
    assert merges == 0 and len(rows) == 2


def test_rows_of_one_layer_never_join_each_other() -> None:
    """What D-064's key kept apart within a layer stays apart; a row of
    another layer near both joins the first."""
    rows, merges = rs.cross_merge(
        [
            [_row(repeaters.HAND), _row(repeaters.HAND, lat=39.81)],
            [_row(repeaters.OSM, callsign="", label="146.940", lat=39.805)],
        ]
    )
    assert merges == 1 and len(rows) == 2
    assert rows[0].also == (repeaters.OSM,) and rows[1].also == ()


def test_precedence_follows_the_order_whatever_the_input_order() -> None:
    order = [
        repeaters.OSM,
        repeaters.BRANDMEISTER,
        repeaters.HEARHAM,
        repeaters.OPEN_REPEATER,
        repeaters.ETCC,
        repeaters.REPEATERBOOK_CSV,
    ]
    rows, merges = rs.cross_merge(_layers(*(_row(s) for s in order)))
    assert merges == 5
    assert rows[0].source == repeaters.REPEATERBOOK_CSV
    assert rows[0].also == (
        repeaters.ETCC,
        repeaters.OPEN_REPEATER,
        repeaters.HEARHAM,
        repeaters.BRANDMEISTER,
        repeaters.OSM,
    )


def test_an_aprs_object_is_never_an_input_to_the_merge() -> None:
    with pytest.raises(ValueError, match="never merged"):
        rs.cross_merge(_layers(_row(repeaters.DIREWOLF)))


def test_a_row_without_a_frequency_is_kept_apart() -> None:
    rows, merges = rs.cross_merge(
        _layers(_row(repeaters.HAND, output_hz=0), _row(repeaters.HEARHAM, output_hz=0))
    )
    assert merges == 0 and len(rows) == 2
