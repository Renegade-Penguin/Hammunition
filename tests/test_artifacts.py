# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``hammunition artifacts``: every remote data artifact the engine would
fetch for an explicit selection, with no station.  D-070.

The Bunker's only source of what to mirror, so the tests hold the contract:
the same resolution as the plan (pins, freshness and its fallback, the
publisher's checksums), deferred entries rather than dropped ones, and
nothing read from the operator's station. Regions are Vermont and Delaware;
every probe is a fake, and the suite refuses any socket that is not
loopback.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from hammunition.artifacts import SelectionError, list_artifacts, select_units
from hammunition.comaps import GENERATED_MARK as COMAPS_MARK
from hammunition.copernicus import tile_name, tile_url
from hammunition.fetch import MirrorPath
from hammunition.geofabrik import BASE, GeofabrikError
from hammunition.interface.artifacts import CHECKS, ArtifactEntry
from hammunition.kiwix import DOWNLOAD
from hammunition.kiwix import GENERATED_MARK as KIWIX_MARK
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import DataInstall
from hammunition.station import Station, save_station
from json_support import REPO_ROOT, assert_golden, parse_one, validate

cli = importlib.import_module("hammunition.cli.main")

CATALOG = load_catalog(REPO_ROOT / "catalog" / "packages")
TODAY = date(2026, 9, 29)
VT = "north-america/us/vermont"
DE = "north-america/us/delaware"
VT_SHA = "a" * 64
DE_MD5 = hashlib.md5(b"delaware", usedforsecurity=False).hexdigest()
TILE_VT = tile_name((43, -73))
TILE_DE = tile_name((38, -76))
TILE_MD5 = "b" * 32
HAM = "ham.stackexchange.com_en_all"
MED = "wikipedia_en_medicine_nopic"
BOOK_PINS: list[dict[str, Any]] = [
    {
        "id": HAM,
        "file": f"{HAM}_2026-08.zim",
        "url": f"{DOWNLOAD}/stack_exchange/{HAM}_2026-08.zim",
        "size": 76_000_000,
        "sha256": "e" * 64,
        "published": "2026-08",
        "measured": "2026-09-29",
    },
    {
        "id": MED,
        "file": f"{MED}_2026-07.zim",
        "url": f"{DOWNLOAD}/wikipedia/{MED}_2026-07.zim",
        "size": 1_100_000_000,
        "sha256": "f" * 64,
        "published": "2026-07",
        "measured": "2026-09-29",
    },
]
POLY = {
    VT: "vermont\n1\n-72.9 43.1\n-72.1 43.1\n-72.1 43.9\n-72.9 43.9\nEND\nEND\n",
    DE: "delaware\n1\n-75.9 38.1\n-75.1 38.1\n-75.1 38.9\n-75.9 38.9\nEND\nEND\n",
}


class Geofabrik:
    """A fake Geofabrik: Vermont pinned for 260101, Delaware not; `latest`
    redirects to a September file; `gone` is a region that 404s."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def head(self, url: str) -> tuple[int, int, str | None]:
        self.asked.append(f"HEAD {url}")
        if url.endswith("-latest.osm.pbf"):
            return 302, 0, url.replace("-latest", "-260928")
        if "/gone-" in url:
            return 404, 0, None
        return 200, 12_345, None

    def text(self, url: str) -> str:
        self.asked.append(f"GET {url}")
        if url.endswith(".md5"):
            return f"{DE_MD5}  {url.rsplit('/', 1)[-1][:-4]}\n"
        for region, poly in POLY.items():
            if url == f"{BASE}/{region}.poly":
                return poly
        raise GeofabrikError(f"{url} returned HTTP 404 (Not Found)")


class Register:
    """A fake ACMA ``HEAD``: the day's size, or no answer."""

    def __init__(self, size: int | None = 67_600_000) -> None:
        self.asked: list[str] = []
        self.answer = size

    def size(self, url: str) -> int:
        from hammunition.acma import AcmaError

        self.asked.append(url)
        if self.answer is None:
            raise AcmaError(f"{url} could not be reached: no route")
        return self.answer


class Head:
    """A fake snapshot ``HEAD``: a size per URL, ``None`` for no length, or
    an OSError for no answer."""

    def __init__(self, sizes: dict[str, int | None] | None = None, fail: bool = False) -> None:
        self.asked: list[str] = []
        self.sizes = sizes or {}
        self.fail = fail

    def size(self, url: str) -> int | None:
        self.asked.append(url)
        if self.fail:
            raise OSError("no route")
        return self.sizes.get(url)


class Bucket:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def head(self, url: str) -> tuple[int, int, str | None]:
        self.asked.append(url)
        return 200, 40_000_000, f'"{TILE_MD5}"'


def _comaps_sha1(body: bytes) -> str:
    return base64.b64encode(hashlib.sha1(body).digest()).decode()


def _write_comaps_pins(root: Path) -> None:
    """A minimal valid ``comaps-pins.yaml``: the mandatory World/WorldCoasts
    leaves plus one map for Vermont, matching the shape gen_comaps_pins.py
    writes (D-069)."""
    (root / "data" / "comaps-pins.yaml").write_text(
        f"# {COMAPS_MARK}\n"
        + yaml.safe_dump(
            {
                "commit": "a" * 40,
                "version": 260830,
                "map_series": "2026.06.28",
                "maps": {
                    "World": {"size": 1, "sha1": _comaps_sha1(b"World")},
                    "WorldCoasts": {"size": 1, "sha1": _comaps_sha1(b"WorldCoasts")},
                    "US_Vermont": {"size": 123, "sha1": _comaps_sha1(b"US_Vermont")},
                },
                "regions": {VT: ["US_Vermont"]},
            }
        )
    )


def _root(tmp_path: Path) -> Path:
    """A catalog root: the real packages, and pin data written here."""
    root = tmp_path / "catalog"
    (root / "data").mkdir(parents=True)
    (root / "packages").symlink_to(REPO_ROOT / "catalog" / "packages")
    (root / "data" / "geofabrik-pins.yaml").write_text(
        yaml.safe_dump(
            {"pins": [{"region": VT, "snapshot": "260101", "size": 99, "sha256": VT_SHA}]}
        )
    )
    (root / "data" / "copernicus-glo30-tiles.txt").write_text(f"{TILE_VT}\n{TILE_DE}\n")
    (root / "data" / "copernicus-glo30-pins.yaml").write_text(
        yaml.safe_dump(
            {"pins": [{"tile": TILE_VT, "size": 41, "sha256": "c" * 64, "md5": "d" * 32}]}
        )
    )
    # Task 16: list_inputs tries US Topo, FSTopo and 3DEP selections for every
    # regional unit's regions, whatever the selected units are, so these three
    # carried indexes must exist here too -- a missing one would otherwise
    # defer with a message naming this fixture's own (unstable, per-run) tmp
    # path, which a committed golden can never match twice. One filler row far
    # from Vermont and Delaware keeps every selection a real, stable "nothing
    # here" record instead of a deferral.
    (root / "data" / "usgs-3dep-tiles.txt").write_text(f"USGS_13_n01w001 1000000 {'a' * 32}\n")
    (root / "data" / "ustopo-quads.txt").write_text(
        f"0.0 -1.0 1.0 0.0 1000000 {'b' * 32} ZZ/filler_20260101\n"
    )
    (root / "data" / "fstopo-quads.txt").write_text("0.0 -1.0 1.0 0.0 999999 26 ZZ FillerCell\n")
    _write_comaps_pins(root)
    # The real book list (its licence lines), and pins written here so the
    # listing does not move every time the real pins are regenerated.
    (root / "data" / "kiwix-books.yaml").symlink_to(
        REPO_ROOT / "catalog" / "data" / "kiwix-books.yaml"
    )
    (root / "data" / "kiwix-pins.yaml").write_text(
        f"# {KIWIX_MARK}\n" + yaml.safe_dump({"pins": BOOK_PINS}, sort_keys=False)
    )
    return root


def _list(
    tmp_path: Path,
    units: tuple[str, ...],
    regions: tuple[str, ...],
    freshness: str = "yearly",
    books: tuple[str, ...] = (),
) -> tuple[tuple[ArtifactEntry, ...], Geofabrik, Bucket]:
    geofabrik, bucket = Geofabrik(), Bucket()
    entries = list_artifacts(
        units,
        regions=regions,
        books=books,
        freshness=freshness,
        catalog=CATALOG,
        catalog_root=_root(tmp_path),
        today=TODAY,
        region_probe=geofabrik,
        tile_probe=bucket,
    )
    return entries, geofabrik, bucket


def _by(entries: tuple[ArtifactEntry, ...], unit: str) -> list[ArtifactEntry]:
    return [e for e in entries if e.unit == unit]


# -- selection ---------------------------------------------------------------


def test_the_default_units_are_every_unit_that_fetches_data() -> None:
    units = select_units(CATALOG, ())
    assert {"country-files", "country-boundaries", "osm-regions", "dem-copernicus"} <= set(units)
    assert "kiwix-library" in units  # deferred when no books are given, never dropped
    assert "osm-navit" not in units and "navit" not in units  # derived, apt


@pytest.mark.parametrize("bad", ["no-such-unit", "osm-navit", "navit"])
def test_a_unit_that_does_not_exist_or_fetches_nothing_is_refused_by_name(bad: str) -> None:
    with pytest.raises(SelectionError, match=bad):
        select_units(CATALOG, ("osm-regions", bad))


# -- data units --------------------------------------------------------------


def test_a_data_unit_lists_each_artifact_with_its_pin(tmp_path: Path) -> None:
    entries, geofabrik, _ = _list(tmp_path, ("country-files", "country-boundaries"), ())
    block = CATALOG["country-files"].install[0].install
    artifact = block.artifacts[0]  # type: ignore[union-attr]
    (files,) = _by(entries, "country-files")
    assert files == ArtifactEntry(
        unit="country-files",
        name=artifact.url.rsplit("/", 1)[-1],
        url=artifact.url,
        check="sha256",
        digest=artifact.sha256,
        checksum_url=None,
        size=artifact.size,
        licence=" ".join(block.licence.split()),  # type: ignore[union-attr]
        deferred=None,
    )
    (borders,) = _by(entries, "country-boundaries")
    assert borders.name == "ne_10m_admin_0_countries.geojson"
    assert geofabrik.asked == [], "a data unit asks nobody"


def test_open_repeater_is_listed_for_a_mirror_like_any_pinned_data(tmp_path: Path) -> None:
    """D-074: the Bunker keeps the Open Repeater file because it is here,
    under the name the fetch asks a mirror for."""
    entries, _, _ = _list(tmp_path, ("open-repeater",), ())
    (entry,) = entries
    artifact = CATALOG["open-repeater"].install[0].install.artifacts[0]  # type: ignore[union-attr]
    assert (entry.name, entry.check, entry.digest, entry.size, entry.licence) == (
        "open-repeater.json",
        "sha256",
        artifact.sha256,
        artifact.size,
        "CC0 1.0",
    )
    assert entry.url == artifact.url and entry.deferred is None


def test_the_acma_register_is_listed_unverified_with_the_days_size(tmp_path: Path) -> None:
    """D-074, amended 2026-10-01: no digest exists, so none is listed; the
    size is the publisher's HEAD today, and the name is the mirror path the
    fetch asks for."""
    from hammunition import acma

    probe = Register()
    (entry,) = list_artifacts(
        ("acma-register",),
        regions=(),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=_root(tmp_path),
        today=TODAY,
        region_probe=Geofabrik(),
        tile_probe=Bucket(),
        register_probe=probe,
    )
    assert entry == ArtifactEntry(
        unit="acma-register",
        name="spectra_rrl.zip",
        url=acma.URL,
        check="unverified-zip",
        digest=None,
        checksum_url=None,
        size=67_600_000,
        licence=(
            "ACMA Register of Radiocommunications Licences, Licence to use the Register of "
            "Radiocommunications Licences, attribution required"
        ),
        deferred=None,
    )
    assert entry.check in CHECKS
    assert probe.asked == [acma.URL]
    assert MirrorPath(entry.unit, entry.name or "") == MirrorPath("acma-register", acma.FILE_NAME)


def test_the_default_units_end_with_the_repeater_snapshots() -> None:
    assert select_units(CATALOG, ())[-1] == "repeater-snapshots"
    assert select_units(CATALOG, ("repeater-snapshots",)) == ("repeater-snapshots",)


def _snapshots(tmp_path: Path, probe: Head | None) -> tuple[ArtifactEntry, ...]:
    return list_artifacts(
        ("repeater-snapshots",),
        regions=(),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=_root(tmp_path),
        today=TODAY,
        region_probe=Geofabrik(),
        tile_probe=Bucket(),
        snapshot_probe=probe,
    )


def test_the_on_request_repeater_lists_are_listed_unverified_for_a_bunker(
    tmp_path: Path,
) -> None:
    """D-078: ETCC, Brandmeister and hearham, as `unverified-fetch`: no digest,
    the publisher's URL, the size from the last HEAD (None when the server
    states none), the licence position, and a name a mirror serves under
    `repeater-snapshots/`."""
    from hammunition import repeater_sources as rs

    urls = {s.name: s.url for s in rs.snapshots()}
    probe = Head({urls["etcc.csv"]: 62_000, urls["brandmeister.json"]: None})
    entries = _snapshots(tmp_path, probe)
    assert [e.name for e in entries] == ["etcc.csv", "brandmeister.json", "hearham.json"]
    assert sorted(probe.asked) == sorted(urls.values())
    for entry in entries:
        assert entry.unit == "repeater-snapshots" and entry.check == "unverified-fetch"
        assert entry.check in CHECKS
        assert entry.digest is None and entry.checksum_url is None and entry.deferred is None
        assert entry.url == urls[entry.name or ""]
        assert "never redistributed by the project" in entry.licence
        assert MirrorPath(entry.unit, entry.name or "").segments == (
            "repeater-snapshots",
            entry.name,
        )
    assert [e.size for e in entries] == [62_000, None, None]


def test_a_head_that_raises_an_http_error_defers_rather_than_crashing(tmp_path: Path) -> None:
    import http.client

    class Broken:
        def size(self, url: str) -> int | None:
            raise http.client.BadStatusLine("garbage")

    entries = _snapshots(tmp_path, Broken())  # type: ignore[arg-type]
    assert len(entries) == 3 and all(e.deferred is not None for e in entries)


def test_an_on_request_list_whose_size_cannot_be_read_is_deferred_by_name(
    tmp_path: Path,
) -> None:
    for probe, why in ((Head(fail=True), "could not be read"), (None, "not asked for")):
        entries = _snapshots(tmp_path / why.replace(" ", "-"), probe)
        assert len(entries) == 3
        assert all(e.deferred is not None and why in e.deferred for e in entries)
        assert [e.name for e in entries] == ["etcc.csv", "brandmeister.json", "hearham.json"]


def test_the_acma_register_is_deferred_when_its_size_cannot_be_read(tmp_path: Path) -> None:
    for probe, why in ((Register(None), "could not be read"), (None, "not asked for")):
        (entry,) = list_artifacts(
            ("acma-register",),
            regions=(),
            freshness="yearly",
            catalog=CATALOG,
            catalog_root=_root(tmp_path / why.replace(" ", "-")),
            today=TODAY,
            region_probe=Geofabrik(),
            tile_probe=Bucket(),
            register_probe=probe,
        )
        assert entry.deferred is not None and why in entry.deferred
        assert entry.name == "spectra_rrl.zip" and entry.size is None


@pytest.mark.parametrize(
    ("unit", "name", "licence"),
    [
        ("faa-nasr-airports", "APT_CSV.zip", None),
        ("eia-860m", "eia860m.xlsx", None),
        ("wri-power-plants", "global_power_plant_database.zip", "CC-BY-4.0"),
    ],
)
def test_the_infrastructure_units_are_listed_for_a_mirror_like_any_pinned_data(
    unit: str, name: str, licence: str | None, tmp_path: Path
) -> None:
    """D-075: the two US federal units and WRI's are pinned data, so a Bunker
    keeps them under the name the fetch asks a mirror for; the FCC and NWR
    fetches are not listed (the ETCC, Brandmeister and hearham ones are, D-078)."""
    entries, _, _ = _list(tmp_path, (unit,), ())
    (entry,) = entries
    block = CATALOG[unit].install[0].install
    artifact = block.artifacts[0]  # type: ignore[union-attr]
    assert (entry.name, entry.check, entry.digest, entry.size) == (
        name,
        "sha256",
        artifact.sha256,
        artifact.size,
    )
    assert entry.licence == (licence or block.licence)  # type: ignore[union-attr]
    assert entry.url == artifact.url and entry.deferred is None


# -- map regions ------------------------------------------------------------


def test_a_pinned_region_is_sha256_and_an_unpinned_one_is_the_publishers_md5(
    tmp_path: Path,
) -> None:
    entries, geofabrik, _ = _list(tmp_path, ("osm-regions",), (VT, DE))
    vermont, delaware = _by(entries, "osm-regions")
    assert (vermont.name, vermont.check, vermont.digest, vermont.checksum_url) == (
        VT,
        "sha256",
        VT_SHA,
        None,
    )
    assert vermont.url == f"{BASE}/{VT}-260101.osm.pbf" and vermont.size == 99
    assert (delaware.check, delaware.digest) == ("md5-publisher", DE_MD5)
    assert delaware.url == f"{BASE}/{DE}-260101.osm.pbf"
    assert delaware.checksum_url == f"{delaware.url}.md5" and delaware.size == 12_345
    assert delaware.licence == "ODbL-1.0" and delaware.deferred is None
    assert not any(VT in a for a in geofabrik.asked), "a pinned region needs no network"


def test_freshness_changes_the_url_and_the_check_as_the_plan_does(tmp_path: Path) -> None:
    monthly, _, _ = _list(tmp_path / "m", ("osm-regions",), (VT,), "monthly")
    assert monthly[0].url == f"{BASE}/{VT}-260901.osm.pbf"
    assert monthly[0].check == "md5-publisher"  # the pin is for 260101 only
    latest, _, _ = _list(tmp_path / "l", ("osm-regions",), (VT,), "latest")
    assert latest[0].url == f"{BASE}/{VT}-260928.osm.pbf"


def test_a_region_that_cannot_be_resolved_is_deferred_by_name(tmp_path: Path) -> None:
    entries, _, _ = _list(tmp_path, ("osm-regions",), ("north-america/us/gone",))
    (gone,) = entries
    assert gone.name == "north-america/us/gone" and gone.url is None
    assert gone.deferred is not None and "no yearly extract" in gone.deferred


def test_map_units_with_no_regions_are_deferred_saying_how(tmp_path: Path) -> None:
    entries, geofabrik, bucket = _list(tmp_path, ("osm-regions", "dem-copernicus"), ())
    assert [(e.unit, e.name) for e in entries] == [("osm-regions", None), ("dem-copernicus", None)]
    assert all(e.deferred is not None and "--map-regions" in e.deferred for e in entries)
    assert geofabrik.asked == [] and bucket.asked == []


# -- terrain ----------------------------------------------------------------


def test_tiles_come_from_each_regions_outline(tmp_path: Path) -> None:
    entries, _, bucket = _list(tmp_path, ("dem-copernicus",), (VT, DE))
    pinned, unpinned = sorted(_by(entries, "dem-copernicus"), key=lambda e: e.name != TILE_VT)
    assert (pinned.name, pinned.check, pinned.digest, pinned.size) == (
        TILE_VT,
        "sha256",
        "c" * 64,
        41,
    )
    assert (unpinned.name, unpinned.check, unpinned.digest) == (TILE_DE, "etag-md5", TILE_MD5)
    assert unpinned.url == tile_url(TILE_DE) and unpinned.checksum_url == tile_url(TILE_DE)
    assert bucket.asked == [tile_url(TILE_DE)], "a pinned tile needs no network"


def test_a_region_whose_outline_cannot_be_read_is_deferred(tmp_path: Path) -> None:
    entries, _, _ = _list(tmp_path, ("dem-copernicus",), (VT, "north-america/us/gone"))
    deferred = [e for e in entries if e.deferred is not None]
    assert [e.name for e in deferred] == ["north-america/us/gone"]
    assert deferred[0].deferred is not None and "outline" in deferred[0].deferred
    assert [e.name for e in entries if e.deferred is None] == [TILE_VT]


def test_tiles_are_not_filtered_by_what_this_machine_has_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The listing is the same on every machine: no install record, no
    installed tile, is ever read."""
    monkeypatch.setattr(Path, "is_file", _refuse_prefix(Path.is_file))
    entries, _, _ = _list(tmp_path, ("dem-copernicus",), (VT,))
    assert [e.name for e in entries] == [TILE_VT]


def _refuse_prefix(real: Any) -> Any:
    def guarded(self: Path) -> Any:
        assert "share/hammunition" not in str(self), f"read {self}"
        return real(self)

    return guarded


# -- reference books (D-066, issue #159) -------------------------------------


def test_books_with_none_given_are_one_deferred_entry_saying_how(tmp_path: Path) -> None:
    entries, _, _ = _list(tmp_path, ("kiwix-library",), (VT,))
    (entry,) = entries
    assert (entry.unit, entry.name, entry.url) == ("kiwix-library", None, None)
    assert entry.deferred is not None and entry.deferred.startswith("no books selected")
    assert "--reference-books" in entry.deferred


def test_each_chosen_book_is_listed_by_its_id_with_its_pin_and_licence(tmp_path: Path) -> None:
    entries, geofabrik, bucket = _list(tmp_path, ("kiwix-library",), (), books=(MED, HAM))
    assert [e.name for e in entries] == [MED, HAM]  # the order chosen
    med, ham = entries
    pin = BOOK_PINS[0]
    assert ham == ArtifactEntry(
        unit="kiwix-library",
        name=HAM,
        url=pin["url"],
        check="sha256",
        digest=pin["sha256"],
        checksum_url=None,
        size=pin["size"],
        licence="CC BY-SA",
        deferred=None,
    )
    assert med.licence.startswith("CC BY-SA 4.0") and med.size == 1_100_000_000
    # Pinned in the catalog: nothing is asked of the network to list a book.
    assert geofabrik.asked == [] and bucket.asked == []


def test_a_book_that_cannot_be_resolved_is_deferred_by_its_id(tmp_path: Path) -> None:
    entries, _, _ = _list(
        tmp_path, ("kiwix-library",), (), books=(HAM, "no.such.book_en_all", "wikem_en_all_nopic")
    )
    assert [(e.name, e.deferred is None) for e in entries] == [
        (HAM, True),
        ("no.such.book_en_all", False),
        ("wikem_en_all_nopic", False),
    ]
    assert "not in the catalog's book list" in (entries[1].deferred or "")
    assert "not pinned" in (entries[2].deferred or "")


def test_unreadable_book_pins_defer_the_unit_naming_the_file(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "data" / "kiwix-pins.yaml").write_text("pins: []\n")
    entries = list_artifacts(
        ("kiwix-library",),
        regions=(),
        books=(HAM,),
        freshness="yearly",
        catalog=CATALOG,
        catalog_root=root,
        today=TODAY,
        region_probe=Geofabrik(),
        tile_probe=Bucket(),
    )
    (entry,) = entries
    assert entry.name is None and "kiwix-pins.yaml" in (entry.deferred or "")


def test_a_listed_book_is_the_mirror_path_the_books_backend_asks_for(tmp_path: Path) -> None:
    """The D-070 contract for books: ``<unit>/<name>`` in the listing is what
    the install asks the mirror for."""
    from hammunition.backends.kiwix import book_mirror_path
    from hammunition.kiwix import load_book_list, load_pin_file, resolve_books

    root = _root(tmp_path)
    entries, _, _ = _list(tmp_path / "again", ("kiwix-library",), (), books=(HAM, MED))
    files = resolve_books((HAM, MED), load_book_list(root), load_pin_file(root))
    assert [MirrorPath(e.unit, e.name or "") for e in entries] == [
        book_mirror_path("kiwix-library", f) for f in files
    ]


# -- the command --------------------------------------------------------------


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *argv: str,
) -> tuple[int, str, str]:
    root = _root(tmp_path)
    monkeypatch.setattr(cli, "UrllibProbe", Geofabrik)
    monkeypatch.setattr(cli, "S3Probe", Bucket)
    monkeypatch.setattr(cli, "AcmaProbe", Register)
    monkeypatch.setattr(cli, "SnapshotHead", Head)
    monkeypatch.setattr(cli, "date", type("D", (), {"today": staticmethod(lambda: TODAY)}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("USER", "op")
    rc = cli.main(["--catalog", str(root), "artifacts", *argv])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_the_document_matches_its_golden_and_its_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = _cli(
        monkeypatch,
        tmp_path,
        capsys,
        "--json",
        "--map-regions",
        f"{VT},{DE}",
        "--units",
        "osm-regions,dem-copernicus",
    )
    doc = parse_one(out)
    validate(doc)
    assert rc == 0 and doc["kind"] == "artifacts"
    assert doc["map_regions"] == [VT, DE] and doc["map_freshness"] == "yearly"
    assert_golden("artifacts", doc)


def test_the_command_never_reads_the_station(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_station(
        Station(map_regions=(DE,), map_freshness="monthly", mirror="http://bunker.lan:8080/"),
        path=tmp_path / "config" / "hammunition" / "station.yml",
    )
    rc, out, _ = _cli(monkeypatch, tmp_path, capsys, "--json", "--units", "osm-regions")
    doc = parse_one(out)
    assert rc == 0 and doc["map_regions"] == [] and doc["map_freshness"] == "yearly"
    assert doc["artifacts"][0]["deferred"] is not None


def test_a_comaps_map_entry_validates_and_its_check_is_in_the_published_enum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-070's `CHECKS` is the Bunker contract's enumeration of `check`
    values. `comaps-maps` (D-069) verifies by the publisher's SHA-1, so an
    entry for it must carry a `check` the document's own published enum
    names, not just one its schema happens to accept as a string."""
    rc, out, _ = _cli(
        monkeypatch, tmp_path, capsys, "--json", "--map-regions", VT, "--units", "comaps-maps"
    )
    doc = parse_one(out)
    validate(doc)  # a bare `str` field: this passes regardless of CHECKS
    assert rc == 0
    (entry,) = doc["artifacts"]
    assert entry["check"] == "sha1-publisher"
    assert entry["check"] in CHECKS, (
        f"{entry['check']!r} is not in CHECKS {CHECKS!r}: D-070's published enum is stale"
    )


def test_the_text_form_lists_the_same_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = _cli(
        monkeypatch, tmp_path, capsys, "--map-regions", VT, "--units", "osm-regions,country-files"
    )
    assert rc == 0
    assert f"{BASE}/{VT}-260101.osm.pbf" in out and "sha256" in out
    assert "country-files" in out


def test_the_books_document_matches_its_golden_and_its_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = _cli(
        monkeypatch,
        tmp_path,
        capsys,
        "--json",
        "--units",
        "kiwix-library",
        "--reference-books",
        f"{HAM}, {MED},no.such.book_en_all",
    )
    doc = parse_one(out)
    validate(doc)
    assert rc == 0 and doc["reference_books"] == [HAM, MED, "no.such.book_en_all"]
    assert_golden("artifacts-books", doc)


def test_books_are_taken_from_the_command_line_never_the_station(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_station(
        Station(reference_books=(HAM,)),
        path=tmp_path / "config" / "hammunition" / "station.yml",
    )
    rc, out, _ = _cli(monkeypatch, tmp_path, capsys, "--json", "--units", "kiwix-library")
    doc = parse_one(out)
    assert rc == 0 and doc["reference_books"] == []
    (entry,) = doc["artifacts"]
    assert entry["deferred"].startswith("no books selected")


def test_the_text_form_lists_the_books(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = _cli(
        monkeypatch, tmp_path, capsys, "--units", "kiwix-library", "--reference-books", HAM
    )
    assert rc == 0
    assert f"kiwix-library  {HAM}  76.0 MB  sha256  {BOOK_PINS[0]['url']}" in out
    assert "1 reference book(s)" in out


@pytest.mark.parametrize(
    "argv",
    [
        ("--reference-books", " , "),
        ("--reference-books", "Not A Book!"),
        ("--units", "osm-navit"),
        ("--units", "no-such-unit"),
        ("--map-regions", "North America/US"),
        ("--map-regions", " , "),
    ],
)
def test_a_bad_selection_is_exit_2_naming_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    rc, out, err = _cli(monkeypatch, tmp_path, capsys, *argv)
    assert rc == 2 and out == ""
    assert "error:" in err


def test_every_listed_name_is_the_mirror_path_the_install_asks_for(tmp_path: Path) -> None:
    """The contract between the two halves of D-070: what the listing names
    `<unit>/<name>` is exactly what the fetch asks the mirror for."""
    from hammunition.backends.data import data_name
    from hammunition.fetch import MirrorPath, mirror_url

    entries, _, _ = _list(
        tmp_path, ("country-files", "country-boundaries", "osm-regions", "dem-copernicus"), (VT,)
    )
    listed = [e for e in entries if e.deferred is None]
    assert len(listed) == 4
    for entry in listed:
        assert entry.name is not None
        url = mirror_url("http://bunker.lan:8080/", MirrorPath(entry.unit, entry.name))
        assert url == f"http://bunker.lan:8080/{entry.unit}/{entry.name}"
    for unit in ("country-files", "country-boundaries"):
        block = CATALOG[unit].install[0].install
        assert isinstance(block, DataInstall)
        assert [e.name for e in listed if e.unit == unit] == [data_name(a) for a in block.artifacts]


def test_the_command_lists_the_snapshots_by_default_and_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = _cli(monkeypatch, tmp_path, capsys, "--json", "--units", "repeater-snapshots")
    doc = parse_one(out)
    validate(doc)
    assert rc == 0 and doc["units"] == ["repeater-snapshots"]
    assert {a["check"] for a in doc["artifacts"]} == {"unverified-fetch"}
    rc, out, _ = _cli(monkeypatch, tmp_path / "all", capsys, "--json")
    assert "repeater-snapshots" in parse_one(out)["units"]
