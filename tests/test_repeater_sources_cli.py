# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``maps repeaters`` with D-074's sources, end to end.

Every run is against a scratch home, config and prefix. The ETCC and
Brandmeister fetches are served from 127.0.0.1 in this process; osmium is
stubbed with the synthetic OSM file, except where a test says it runs the
real one. No GUI starts. Every row is synthetic (N0CALL, N0TST,
Springfield IL); the extract's region is called `springfield`, which must
appear in no output.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from hammunition import repeater_sources, repeaters
from hammunition.interface import repeaters as repeater_docs
from hammunition.interface.repeaters import render_removed, render_repeaters
from hammunition.navit_config import rewrite
from json_support import assert_text_values_in_json, parse_one, validate

cli = importlib.import_module("hammunition.cli.main")

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPEATERS = FIXTURES / "repeaters"
NAVIT_STOCK = (FIXTURES / "navit.xml").read_text()

#: Nothing positional or personal may reach the terminal or the document,
#: and no region either (D-057).
PRIVATE = ("N0CALL", "N0TST", "39.8", "-89.6", "146.94", "Springfield", "springfield")
#: The synthetic hotspots' positions, as a GPX or Navit file would write them.
HOTSPOTS = ("39.71000", "39.72000", "39.73000", "-89.61000", "-89.62000", "-89.63000")


class Station:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.data = tmp_path / "data"
        self.config = tmp_path / "config"
        self.prefix = tmp_path / "prefix"
        self.overlays = self.data / "hammunition" / "overlays"
        self.layer = self.overlays / "repeaters"
        self.qms = self.config / "QLandkarte" / "QMapShack.conf"
        self.share = self.prefix / "share" / "hammunition" / "data"
        self.generated = self.share / "osm-navit" / "navit.xml"
        self.user_navit = self.overlays / "navit.xml"

    def install_navit(self) -> None:
        self.generated.parent.mkdir(parents=True, exist_ok=True)
        maps = [self.generated.parent / "atlantis-oceania.bin"]
        self.generated.write_text(rewrite(NAVIT_STOCK, maps))

    def install_open_repeater(self) -> Path:
        path = self.share / "open-repeater" / "open-repeater.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPEATERS / "open-repeater.json", path)
        return path

    def install_region(self, slug: str = "springfield", snapshot: str = "260901") -> Path:
        folder = self.share / "osm-regions"
        folder.mkdir(parents=True, exist_ok=True)
        pbf = folder / f"{slug}.osm.pbf"
        pbf.write_bytes(b"not read: osmium is stubbed")
        (folder / f"{slug}.osm.pbf.source").write_text(f"{snapshot}\n")
        return pbf

    def copy(self, name: str) -> Path:
        dest = self.root / "in" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPEATERS / name, dest)
        return dest

    def layer_text(self) -> str:
        return "".join(
            p.read_text(errors="replace") for p in self.layer.iterdir() if p.suffix != ".poi"
        )


@pytest.fixture
def station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Station:
    here = Station(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(here.data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(here.config))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "DEFAULT_PREFIX", here.prefix)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    return here


@pytest.fixture
def osmium(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """osmium stubbed: every extract filters to the synthetic OSM file."""
    seen: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        Path(argv[argv.index("-o") + 1]).write_text((REPEATERS / "osm-repeaters.osm").read_text())
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(repeater_sources, "_run", run)
    return seen


def _no_private(text: str) -> None:
    leaked = [p for p in PRIVATE if p in text]
    assert not leaked, f"printed {leaked}"


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


# --- Open Repeater ------------------------------------------------------------------


def test_open_repeater_from_the_installed_unit_is_its_own_layer(
    station: Station, capsys: pytest.CaptureFixture[str]
) -> None:
    station.install_navit()
    station.install_open_repeater()
    code, out, _ = _run(["maps", "repeaters", "import", "--from-open-repeater"], capsys)
    assert code == 0
    assert repeater_sources.open_repeater_licence() in out.replace("\n", " ")
    assert "Repeaters (Open Repeater 2026-09-28, CC0)" in out
    assert "Written: 2 repeaters" in out
    _no_private(out)
    for name in repeaters.layer_files("open-repeater"):
        assert (station.layer / name).is_file()
    assert not (station.layer / repeaters.FILES[0]).exists()
    assert f"poiPaths={station.layer}" in station.qms.read_text()
    assert repeaters.layer_files("open-repeater")[2] in station.user_navit.read_text()


def test_open_repeater_not_installed_names_the_unit(
    station: Station, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(["maps", "repeaters", "import", "--from-open-repeater"], capsys)
    assert code == cli.EXIT_FAILED
    assert "hammunition install open-repeater" in err
    assert not station.layer.exists()


def test_open_repeater_from_a_downloaded_file(
    station: Station, capsys: pytest.CaptureFixture[str]
) -> None:
    path = station.copy("open-repeater.json")
    code, out, _ = _run(["maps", "repeaters", "import", "--from-open-repeater", str(path)], capsys)
    assert code == 0 and "Repeaters (Open Repeater 2026-09-28, CC0)" in out


# --- exclusivity -------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--from-osm", "--from-open-repeater"],
        ["FILE", "--from-osm"],
    ],
)
def test_one_source_per_import(
    extra: list[str], station: Station, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [str(station.copy("hand.csv")) if a == "FILE" else a for a in extra]
    code, _, err = _run(["maps", "repeaters", "import", *argv], capsys)
    assert code == cli.EXIT_FAILED and "one source per import" in err
    assert not station.layer.exists()


def test_exported_dates_only_an_export(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_region()
    code, _, err = _run(
        ["maps", "repeaters", "import", "--from-osm", "--exported", "2026-09-01"], capsys
    )
    assert code == cli.EXIT_FAILED and "dated by its own data" in err
    assert osmium == []


# --- OpenStreetMap -----------------------------------------------------------------


def test_osm_filters_every_installed_extract_and_names_no_region(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_region("springfield", "260901")
    station.install_region("shelbyville", "260815")
    code, out, _ = _run(["maps", "repeaters", "import", "--from-osm"], capsys)
    assert code == 0, out
    assert len(osmium) == 2 and all(a[:2] == ["osmium", "tags-filter"] for a in osmium)
    assert "Repeaters (OpenStreetMap, ODbL, 2026-08-15)" in out
    assert "ODbL 1.0" in out.replace("\n", " ")
    assert "16 rows read" in out and "not marked as a repeater: 4" in out
    _no_private(out)
    assert "shelbyville" not in out


def test_a_failing_osmium_names_the_extract_by_number_never_by_region(
    station: Station, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final review: osmium's stderr and the old message carried the file."""
    pbf = station.install_region("springfield")

    def fails(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, "", f"Open failed for '{pbf}': springfield.osm.pbf is truncated"
        )

    monkeypatch.setattr(repeater_sources, "_run", fails)
    code, out, err = _run(["maps", "repeaters", "import", "--from-osm"], capsys)
    assert code == cli.EXIT_FAILED and "region extract 1 of 1" in err
    _no_private(out + err)


def test_a_failed_rebuild_still_registers_the_written_layer_and_exits_1(
    station: Station,
    capsys: pytest.CaptureFixture[str],
    osmium: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final review: the rebuild shared the layer's try, so its failure said
    'cannot write the layer' about a layer already written, and skipped
    registration."""
    station.install_region()
    assert cli.main(["maps", "repeaters", "import", str(station.copy("hand.csv"))]) == 0
    capsys.readouterr()

    def broken(where: Path) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(repeaters, "rebuild_all", broken)
    code, out, err = _run(["maps", "repeaters", "import", "--from-osm"], capsys)
    assert code == cli.EXIT_FAILED
    assert "cannot write the layer" not in err
    assert "All sources: not rebuilt: disk full" in out
    assert (station.layer / repeaters.layer_files("osm")[0]).is_file()
    assert f"poiPaths={station.layer}" in station.qms.read_text()


def test_remove_one_layer_lists_the_all_sources_file_it_deleted(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_region()
    assert cli.main(["maps", "repeaters", "import", str(station.copy("hand.csv"))]) == 0
    assert cli.main(["maps", "repeaters", "import", "--from-osm"]) == 0
    assert (station.layer / repeaters.ALL_SOURCES).is_file()
    capsys.readouterr()
    assert cli.main(["maps", "repeaters", "remove", "--layer", "osm", "--json"]) == 0
    doc = parse_one(capsys.readouterr().out)
    assert repeaters.ALL_SOURCES in {Path(p).name for p in doc["removed"]}
    assert not (station.layer / repeaters.ALL_SOURCES).exists()


def test_osm_with_no_region_installed_says_what_to_install(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    code, _, err = _run(["maps", "repeaters", "import", "--from-osm"], capsys)
    assert code == cli.EXIT_FAILED and "hammunition install osm-regions" in err


def test_osm_without_osmium_names_the_package(
    station: Station, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    station.install_region()

    def missing(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(repeater_sources, "_run", missing)
    code, _, err = _run(["maps", "repeaters", "import", "--from-osm"], capsys)
    assert code == cli.EXIT_FAILED and "osmium-tool" in err


def test_osm_json_document_names_the_directory_with_no_digest(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_region()
    code = cli.main(["maps", "repeaters", "import", "--from-osm", "--json"])
    raw = capsys.readouterr().out
    assert code == 0
    doc = parse_one(raw)
    validate(doc)
    assert doc["kind"] == "repeaters" and doc["layer_id"] == "osm"
    (only,) = doc["inputs"]
    assert only["path"].endswith("osm-regions") and only["sha256"] == ""
    _no_private(raw)


# --- Direwolf ----------------------------------------------------------------------


def test_direwolf_logs_are_their_own_heard_layer_and_never_in_all_sources(
    station: Station, capsys: pytest.CaptureFixture[str]
) -> None:
    one, two = station.copy("direwolf-2026-10-01.csv"), station.copy("direwolf-2026-10-02.csv")
    hand = station.copy("hand.csv")
    assert cli.main(["maps", "repeaters", "import", str(hand)]) == 0
    capsys.readouterr()
    code, out, _ = _run(
        ["maps", "repeaters", "import", "--from-direwolf-log", str(one), str(two)], capsys
    )
    assert code == 0
    assert "Repeaters heard off the air (APRS objects, 2026-10-02)" in out
    assert "Merged: 1" in out and "Written: 6 repeaters" in out
    assert "Received by this station" in out
    assert repeaters.present_layers(station.layer) == ("export", "aprs-heard")
    assert not (station.layer / repeaters.ALL_SOURCES).exists()  # one directory layer
    _no_private(out)


# --- the fetches -------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b""
    status = 200

    def do_GET(self) -> None:
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Handler]]:
    handler = type("Handler", (_Handler,), {})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(repeater_sources, "ETCC_URL", f"{base}/csvcreate_all.php")
    monkeypatch.setattr(repeater_sources, "BRANDMEISTER_URL", f"{base}/v2/device")
    try:
        yield handler
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_etcc_discloses_first_and_records_what_it_saw(
    station: Station, served: type[_Handler], capsys: pytest.CaptureFixture[str]
) -> None:
    served.body = (REPEATERS / "etcc.csv").read_bytes()
    digest = hashlib.sha256(served.body).hexdigest()
    code, out, _ = _run(["maps", "repeaters", "fetch-etcc"], capsys)
    assert code == 0, out
    assert out.index("This fetches") < out.index("states no licence for this list")
    assert digest in out and "not verifiable" in out and "locator precision" in out
    assert "Repeaters (RSGB ETCC " in out and ", unverified)" in out
    assert (station.layer / repeaters.layer_files("etcc")[0]).is_file()
    _no_private(out)


def test_fetch_brandmeister_drops_hotspots_before_anything_is_written(
    station: Station, served: type[_Handler], capsys: pytest.CaptureFixture[str]
) -> None:
    served.body = (REPEATERS / "brandmeister.json").read_bytes()
    code, out, _ = _run(["maps", "repeaters", "fetch-brandmeister"], capsys)
    assert code == 0, out
    assert out.index("Most entries are hotspots") < out.index("BrandMeister publishes no terms")
    assert f"{repeater_sources.HOTSPOT_ID}: 2" in out
    assert f"{repeater_sources.HOTSPOT_SIMPLEX}: 1" in out
    assert "DMR repeaters (Brandmeister " in out and "Written: 1 repeaters" in out
    written = station.layer_text()
    leaked = [h for h in HOTSPOTS if h in written]
    assert not leaked, f"a hotspot's position reached the layer: {leaked}"
    _no_private(out)


class _Mirror(http.server.BaseHTTPRequestHandler):
    """A fake LAN mirror: serves what is in `files` by path, 404 otherwise,
    and records every path asked."""

    files: ClassVar[dict[str, bytes]]
    asked: ClassVar[list[str]]

    def do_GET(self) -> None:
        self.asked.append(self.path)
        body = self.files.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def mirror(station: Station, monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Mirror]]:
    """A loopback mirror set as the station's, which the publisher's `served`
    handler never sees."""
    from hammunition.station import Station as Saved
    from hammunition.station import save_station

    handler = type("Mirror", (_Mirror,), {"files": {}, "asked": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("USER", "op")
    save_station(
        Saved(mirror=f"http://127.0.0.1:{server.server_address[1]}/"),
        path=station.config / "hammunition" / "station.yml",
    )
    try:
        yield handler
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("verb", "name", "fixture"),
    [
        ("fetch-etcc", "etcc.csv", "etcc.csv"),
        ("fetch-brandmeister", "brandmeister.json", "brandmeister.json"),
        ("fetch-hearham", "hearham.json", "hearham.json"),
    ],
)
def test_a_fetch_reads_the_stations_mirror_first_and_stays_unverified(
    verb: str,
    name: str,
    fixture: str,
    station: Station,
    served: type[_Handler],
    mirror: type[_Mirror],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-078: `<mirror>/repeater-snapshots/<name>` is asked first; the
    publisher (here a handler that would serve a login page) is never reached
    when the mirror has it."""
    body = (REPEATERS / fixture).read_bytes()
    mirror.files[f"/repeater-snapshots/{name}"] = body
    served.body = b"<html>the publisher was asked</html>"
    monkeypatch.setattr(
        repeaters, "HEARHAM_URL", repeater_sources.ETCC_URL.replace("csvcreate_all.php", "h")
    )
    code, out, err = _run(["maps", "repeaters", verb], capsys)
    assert code == 0, out + err
    assert mirror.asked == [f"/repeater-snapshots/{name}"]
    assert "Read from your LAN mirror" in out and "Still unverified" in out
    assert "Read from the LAN mirror http://127.0.0.1" in out.replace("\n", " ")
    assert hashlib.sha256(body).hexdigest() in out and "not verifiable" in out
    assert "unverified)" in out
    _no_private(out)


def test_a_fetch_falls_back_to_the_publisher_when_the_mirror_lacks_it_or_sends_the_wrong_list(
    station: Station,
    served: type[_Handler],
    mirror: type[_Mirror],
    capsys: pytest.CaptureFixture[str],
) -> None:
    served.body = (REPEATERS / "etcc.csv").read_bytes()
    code, out, _ = _run(["maps", "repeaters", "fetch-etcc"], capsys)  # mirror 404
    assert code == 0 and mirror.asked == ["/repeater-snapshots/etcc.csv"]
    assert "did not supply it" in out and "asking the publisher" in out
    flat = out.replace("\n", " ")
    assert "Fetched 20" in flat and "Read from the LAN mirror" not in flat
    mirror.files["/repeater-snapshots/etcc.csv"] = b"<html>a login page</html>"
    code, out, _ = _run(["maps", "repeaters", "fetch-etcc"], capsys)  # mirror sends junk
    assert code == 0 and "did not supply it" in out and "Fetched 20" in out.replace("\n", " ")


def test_no_mirror_and_no_station_mirror_leave_the_publisher_path_unchanged(
    station: Station,
    served: type[_Handler],
    mirror: type[_Mirror],
    capsys: pytest.CaptureFixture[str],
) -> None:
    served.body = (REPEATERS / "etcc.csv").read_bytes()
    mirror.files["/repeater-snapshots/etcc.csv"] = served.body
    code, out, _ = _run(["maps", "repeaters", "fetch-etcc", "--no-mirror"], capsys)
    assert code == 0 and mirror.asked == [] and "LAN mirror" not in out


@pytest.mark.parametrize("verb", ["fetch-etcc", "fetch-brandmeister"])
def test_a_fetch_refuses_what_is_not_its_list(
    verb: str, station: Station, served: type[_Handler], capsys: pytest.CaptureFixture[str]
) -> None:
    served.body = b"<html>a login page</html>"
    code, _, err = _run(["maps", "repeaters", verb], capsys)
    assert code == cli.EXIT_FAILED and "not " in err and "Nothing was written" in err
    assert not station.layer.exists()


@pytest.mark.parametrize("verb", ["fetch-etcc", "fetch-brandmeister"])
def test_a_fetch_has_no_json_form(
    verb: str, station: Station, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["maps", "repeaters", verb, "--json"]) == cli.EXIT_UNPLANNABLE
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == f"maps repeaters {verb}" and "no --json form" in doc["message"]


# --- layers together -------------------------------------------------------------------


def test_two_directory_layers_make_the_all_sources_file_and_both_register(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_navit()
    station.install_region()
    hand = station.copy("hand.csv")
    assert cli.main(["maps", "repeaters", "import", str(hand)]) == 0
    capsys.readouterr()
    code, out, _ = _run(["maps", "repeaters", "import", "--from-osm"], capsys)
    assert code == 0
    assert "All sources: 6 repeaters from layers export, osm, 1 joined across sources" in out
    assert (station.layer / repeaters.ALL_SOURCES).is_file()
    navit = station.user_navit.read_text()
    for layer_id in ("export", "osm"):
        assert str(station.layer / repeaters.layer_files(layer_id)[2]) in navit
    _no_private(out)


def test_import_json_carries_the_all_sources_view(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_region()
    assert cli.main(["maps", "repeaters", "import", str(station.copy("hand.csv"))]) == 0
    capsys.readouterr()
    assert cli.main(["maps", "repeaters", "import", "--from-osm", "--json"]) == 0
    raw = capsys.readouterr().out
    assert cli.main(["maps", "repeaters", "import", "--from-osm"]) == 0
    text = capsys.readouterr().out
    doc = parse_one(raw)
    validate(doc)
    assert doc["all_sources"]["layers"] == ["export", "osm"]
    assert doc["all_sources"]["merged"] == 1
    assert_text_values_in_json(
        text,
        doc,
        render_repeaters,
        repeater_docs.render_all_sources,
        repeater_docs._registration,
    )
    _no_private(raw)


def test_remove_one_layer_keeps_the_rest_and_rebuilds(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_navit()
    station.install_region()
    station.install_open_repeater()
    assert cli.main(["maps", "repeaters", "import", str(station.copy("hand.csv"))]) == 0
    assert cli.main(["maps", "repeaters", "import", "--from-osm"]) == 0
    assert cli.main(["maps", "repeaters", "import", "--from-open-repeater"]) == 0
    capsys.readouterr()
    code, out, _ = _run(["maps", "repeaters", "remove", "--layer", "osm"], capsys)
    assert code == 0
    assert repeaters.present_layers(station.layer) == ("export", "open-repeater")
    assert "All sources:" in out and "export, open-repeater" in out
    assert repeaters.layer_files("osm")[2] not in station.user_navit.read_text()
    assert f"poiPaths={station.layer}" in station.qms.read_text()


def test_remove_json_names_the_layers_and_everything_goes(
    station: Station, capsys: pytest.CaptureFixture[str], osmium: list[list[str]]
) -> None:
    station.install_navit()
    station.install_region()
    assert cli.main(["maps", "repeaters", "import", str(station.copy("hand.csv"))]) == 0
    assert cli.main(["maps", "repeaters", "import", "--from-osm"]) == 0
    capsys.readouterr()
    assert cli.main(["maps", "repeaters", "remove", "--json"]) == 0
    doc = parse_one(capsys.readouterr().out)
    validate(doc)
    assert doc["kind"] == "repeaters-removed"
    assert doc["layers"] == list(repeaters.LAYERS)
    assert repeaters.ALL_SOURCES in {Path(p).name for p in doc["removed"]}
    assert doc["all_sources"]["file"] is None
    assert not station.layer.exists() and not station.user_navit.exists()
    lines = render_removed(
        repeater_docs.RepeatersRemovedDocument(
            directory="d",
            layers=("osm",),
            removed=(),
            unregistered=(),
            all_sources=repeater_docs.AllSourcesView(None, "", (), 0, 0, (), None),
        )
    )
    assert lines == ["Nothing to remove in d."]


def test_maps_navit_opens_every_layer(
    station: Station,
    capsys: pytest.CaptureFixture[str],
    osmium: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    station.install_navit()
    station.install_region()
    assert cli.main(["maps", "repeaters", "import", str(station.copy("hand.csv"))]) == 0
    assert cli.main(["maps", "repeaters", "import", "--from-osm"]) == 0
    station.user_navit.unlink()
    seen: list[list[str]] = []

    class Exec(Exception):
        pass

    def execvp(file: str, argv: list[str]) -> None:
        seen.append(argv)
        raise Exec

    monkeypatch.setattr(os, "execvp", execvp)
    with pytest.raises(Exec):
        cli.main(["maps", "navit"])
    assert seen == [["navit", str(station.user_navit)]]
    navit = station.user_navit.read_text()
    assert navit.count('type="textfile"') == 2


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool is not installed")
def test_from_osm_with_the_real_osmium_over_a_synthetic_extract(
    station: Station, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = station.share / "osm-regions"
    folder.mkdir(parents=True)
    pbf = folder / "springfield.osm.pbf"
    subprocess.run(
        ["osmium", "cat", str(REPEATERS / "osm-repeaters.osm"), "-o", str(pbf), "--overwrite"],
        check=True,
        capture_output=True,
    )
    (folder / "springfield.osm.pbf.source").write_text("260901\n")
    code, out, _ = _run(["maps", "repeaters", "import", "--from-osm"], capsys)
    assert code == 0, out
    assert "Written: 4 repeaters" in out
    _no_private(out)


def test_both_sources_failing_names_both_reasons_and_the_publisher_url(
    station: Station,
    served: type[_Handler],
    mirror: type[_Mirror],
    capsys: pytest.CaptureFixture[str],
) -> None:
    served.body = b"<html>a login page</html>"
    code, _, err = _run(["maps", "repeaters", "fetch-etcc"], capsys)
    assert code == cli.EXIT_FAILED and "the LAN mirror had failed first" in err
    assert "Nothing was written" in err


# --- a file:// Bunker export and --offline (#394) ------------------------------------

_SNAPSHOT_VERBS = [
    ("fetch-etcc", "etcc.csv"),
    ("fetch-brandmeister", "brandmeister.json"),
    ("fetch-hearham", "hearham.json"),
]


def _file_export_station(station: Station, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The station's mirror is a ``file://`` export; the publisher stays unreachable
    (the suite's socket guard) and `fetch_list` fails the test if asked."""
    from hammunition.station import Station as Saved
    from hammunition.station import save_station

    export = station.root / "media" / "bunker-export"
    (export / "repeater-snapshots").mkdir(parents=True)
    monkeypatch.setenv("USER", "op")
    save_station(Saved(mirror=export.as_uri()), path=station.config / "hammunition" / "station.yml")

    def never(url: str, *, limit: int) -> object:
        pytest.fail(f"the publisher was asked: {url}")

    monkeypatch.setattr(repeaters, "fetch_list", never)
    return export


@pytest.mark.parametrize(("verb", "name"), _SNAPSHOT_VERBS)
def test_offline_with_a_missing_export_snapshot_is_refused_and_asks_no_publisher(
    verb: str,
    name: str,
    station: Station,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export = _file_export_station(station, monkeypatch)
    code, out, err = _run(["maps", "repeaters", verb, "--offline"], capsys)
    assert code != 0
    assert f"repeater-snapshots/{name}" in err and "--offline" in err
    assert "Nothing was written" in err
    assert not station.layer.exists() or not any(station.layer.iterdir())
    # The removable media's location is not echoed: only the export's own name.
    assert str(export.parent) not in out + err


@pytest.mark.parametrize(("verb", "name"), _SNAPSHOT_VERBS)
def test_offline_reads_the_snapshot_from_the_file_export(
    verb: str,
    name: str,
    station: Station,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export = _file_export_station(station, monkeypatch)
    (export / "repeater-snapshots" / name).write_bytes((REPEATERS / name).read_bytes())
    code, out, err = _run(["maps", "repeaters", verb, "--offline"], capsys)
    assert code == 0, out + err
    assert "Read from your LAN mirror" in out and "Still unverified" in out
    assert str(export.parent) not in out + err


@pytest.mark.parametrize(("verb", "name"), _SNAPSHOT_VERBS)
def test_offline_without_a_station_mirror_is_refused_naming_the_input(
    verb: str,
    name: str,
    station: Station,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def never(url: str, *, limit: int) -> object:
        pytest.fail(f"the publisher was asked: {url}")

    monkeypatch.setattr(repeaters, "fetch_list", never)
    code, _, err = _run(["maps", "repeaters", verb, "--offline"], capsys)
    assert code != 0 and f"repeater-snapshots/{name}" in err


@pytest.mark.parametrize(("verb", "name"), _SNAPSHOT_VERBS)
def test_offline_and_no_mirror_cannot_be_combined(
    verb: str,
    name: str,
    station: Station,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _file_export_station(station, monkeypatch)
    code, _, err = _run(["maps", "repeaters", verb, "--offline", "--no-mirror"], capsys)
    assert code != 0 and "--no-mirror" in err


@pytest.mark.parametrize(("verb", "name"), _SNAPSHOT_VERBS)
def test_without_offline_a_missing_export_still_falls_back_to_the_publisher(
    verb: str,
    name: str,
    station: Station,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _file_export_station(station, monkeypatch)
    asked: list[str] = []

    def publisher(url: str, *, limit: int) -> object:
        asked.append(url)
        raise repeaters.RepeaterFetchError("publisher down")

    monkeypatch.setattr(repeaters, "fetch_list", publisher)
    code, _, err = _run(["maps", "repeaters", verb], capsys)
    assert code != 0 and len(asked) == 1
    assert "repeater-snapshots" in err
