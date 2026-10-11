# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811
"""FSTopo sheets resolve offline without ever becoming verified.  #381, Task 10.

The Forest Service publishes no checksum.  A sheet with a pin in the repository
is checked against that pin (never against the Bunker's record of itself); any
other stays unverified, by name, however the Bunker labels it.  Nothing here
grants ``hold_unverified`` or walks through a consent or disclosure gate.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import socket
from pathlib import Path
from typing import Any

import pytest

from bunker_fixtures import artifact, make_context
from hammunition.backends.fstopo import RegionSheets, render_record
from hammunition.backends.regions import KeptRegion, MapResolution
from hammunition.catalogue import PublisherUnavailable
from hammunition.consent.gate import ConsentUnavailable, resolve_consent
from hammunition.fstopo import (
    GATEWAY,
    PINNED,
    UNVERIFIED,
    FsPin,
    FsQuad,
    FstopoError,
    GatewayProbe,
    map_url,
    parse_index,
    parse_row,
    recorded_sheet,
)
from hammunition.manifest.schema import ConsentGate, RiskCategory
from hammunition.resolution import CatalogueMiss, ResolutionContext
from hammunition.topo_plan import resolve_fstopo
from json_support import parse_one
from test_fstopo_plan import RegionProbe
from test_offline_context import apt_state, enrol_file_bunker, machine, run  # noqa: F401
from test_offline_usgs import SpyChecks

cli = importlib.import_module("hammunition.cli.main")
CATALOG = Path(__file__).resolve().parents[1] / "catalog"
UNIT = "usfs-fstopo"
REGION = "north-america/us/delaware"
SLUG = "north-america-us-delaware"
GOOD_URL = f"{GATEWAY}data/Test.tif"
BODY = b"tif"
SHA = hashlib.sha256(BODY).hexdigest()


def sheet(**changes: Any) -> FsQuad:
    values: dict[str, Any] = dict(
        south=38.5, west=-75.5, north=38.625, east=-75.375,
        secoord=12345, vintage=2026, state="DE", cell="Test",
    )  # fmt: skip
    values.update(changes)
    return FsQuad(**values)


def row(q: FsQuad | None = None, **changes: Any) -> dict[str, object]:
    q = q or sheet()
    values: dict[str, Any] = dict(
        publisher_check="unverified-fetch",
        publisher_name="Test.tif",
        publisher_size=len(BODY),
        publisher_url=GOOD_URL,
    )
    values.update(changes)
    return artifact(UNIT, q.name, BODY, **values)


# -- recorded_sheet: unpinned stays unverified ----------------------------------------


def test_signed_unpinned_fstopo_stays_unverified(tmp_path: Path) -> None:
    q = sheet()
    context = make_context(tmp_path, [row(q)])
    got = recorded_sheet(q, unit=UNIT, pins={}, context=context)
    assert got.sha256 is None
    assert got.verified_by == UNVERIFIED and "unverified" in got.verified_by.lower()
    assert (got.url, got.size) == (GOOD_URL, len(BODY))


def test_a_bunker_sha256_does_not_make_an_unpinned_sheet_verified(tmp_path: Path) -> None:
    q = sheet()
    context = make_context(tmp_path, [row(q)])
    # The record carries a real sha256, and still the sheet has none of its own.
    assert context.entry(UNIT, q.name).sha256 == SHA
    got = recorded_sheet(q, unit=UNIT, pins={}, context=context)
    assert got.sha256 is None and got.verified_by != PINNED


@pytest.mark.parametrize("check", ["sha256", "etag-md5", "unverified-snapshot"])
def test_an_unpinned_sheet_needs_an_explicitly_unverified_row(tmp_path: Path, check: str) -> None:
    q = sheet()
    context = make_context(tmp_path, [row(q, publisher_check=check)])
    with pytest.raises(CatalogueMiss, match="explicitly unverified"):
        recorded_sheet(q, unit=UNIT, pins={}, context=context)


def test_an_unverified_zip_row_is_accepted_too(tmp_path: Path) -> None:
    q = sheet()
    context = make_context(tmp_path, [row(q, publisher_check="unverified-zip")])
    assert recorded_sheet(q, unit=UNIT, pins={}, context=context).sha256 is None


def test_an_inconsistent_publisher_size_is_a_miss(tmp_path: Path) -> None:
    q = sheet()
    context = make_context(tmp_path, [row(q, publisher_size=len(BODY) + 1)])
    with pytest.raises(CatalogueMiss, match="publisher_size"):
        recorded_sheet(q, unit=UNIT, pins={}, context=context)


def test_an_absent_publisher_size_is_a_miss(tmp_path: Path) -> None:
    """The catalogue schema lets a name and size be absent together."""
    q = sheet()
    context = make_context(tmp_path, [row(q, publisher_name=None, publisher_size=None)])
    with pytest.raises(CatalogueMiss, match="publisher_size"):
        recorded_sheet(q, unit=UNIT, pins={}, context=context)


def test_a_missing_sheet_is_a_miss(tmp_path: Path) -> None:
    q = sheet()
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        recorded_sheet(q, unit=UNIT, pins={}, context=make_context(tmp_path, []))
    with pytest.raises(CatalogueMiss, match="no Bunker enrolled"):
        recorded_sheet(q, unit=UNIT, pins={}, context=ResolutionContext(offline=True))


def test_another_operators_entry_is_not_this_operators(tmp_path: Path) -> None:
    q = sheet()
    mine = make_context(
        tmp_path / "mine", [row(q, share="owner:laptop-a")], mode="group", enrolment_id="laptop-a"
    )
    assert recorded_sheet(q, unit=UNIT, pins={}, context=mine).url == GOOD_URL
    theirs = make_context(
        tmp_path / "theirs", [row(q, share="owner:laptop-a")], mode="group", enrolment_id="laptop-b"
    )
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        recorded_sheet(q, unit=UNIT, pins={}, context=theirs)


# -- recorded_sheet: pins are the repository's, the record is compared with them ------


def test_a_pinned_sheet_takes_its_pin_and_is_verified_by_it(tmp_path: Path) -> None:
    q = sheet()
    pins = {q.secoord: FsPin(q.secoord, len(BODY), SHA)}
    context = make_context(tmp_path, [row(q)])
    got = recorded_sheet(q, unit=UNIT, pins=pins, context=context)
    assert (got.sha256, got.size, got.verified_by) == (SHA, len(BODY), PINNED)


def test_a_pinned_sheet_whose_bunker_digest_differs_is_a_miss(tmp_path: Path) -> None:
    q = sheet()
    pins = {q.secoord: FsPin(q.secoord, len(BODY), "e" * 64)}
    context = make_context(tmp_path, [row(q)])
    with pytest.raises(CatalogueMiss, match="repository sha256 pin"):
        recorded_sheet(q, unit=UNIT, pins=pins, context=context)


def test_a_pinned_sheet_whose_bunker_size_differs_is_a_miss_by_size_first(tmp_path: Path) -> None:
    q = sheet()
    pins = {q.secoord: FsPin(q.secoord, len(BODY) + 5, "e" * 64)}
    context = make_context(tmp_path, [row(q)])
    with pytest.raises(CatalogueMiss, match="expected size"):
        recorded_sheet(q, unit=UNIT, pins=pins, context=context)


def test_the_record_is_never_compared_with_itself(tmp_path: Path) -> None:
    """A record whose own size and digest agree still loses to a different pin."""
    q = sheet()
    context = make_context(tmp_path, [row(q)])
    other = {q.secoord: FsPin(q.secoord, len(BODY), hashlib.sha256(b"other").hexdigest())}
    with pytest.raises(CatalogueMiss):
        recorded_sheet(q, unit=UNIT, pins=other, context=context)


# -- the publisher URL: parsed, not prefix-matched -------------------------------------

BAD_URLS = [
    pytest.param("https://data.fs.usda.gov.evil.example/geodata/rastergateway/a.tif", id="suffix"),
    pytest.param("https://evildata.fs.usda.gov/geodata/rastergateway/a.tif", id="prefix-host"),
    pytest.param("https://user@data.fs.usda.gov/geodata/rastergateway/a.tif", id="userinfo"),
    pytest.param("https://user:pw@data.fs.usda.gov/geodata/rastergateway/a.tif", id="password"),
    pytest.param("https://data.fs.usda.gov:8443/geodata/rastergateway/a.tif", id="port"),
    pytest.param("http://data.fs.usda.gov/geodata/rastergateway/a.tif", id="http"),
    pytest.param("ftp://data.fs.usda.gov/geodata/rastergateway/a.tif", id="ftp"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergatewayX/a.tif", id="no-boundary"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway", id="bare-prefix"),
    pytest.param("https://data.fs.usda.gov/other/a.tif", id="other-path"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/%2e%2e/%2e%2e/a.tif", id="%2e%2e"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/%2E%2E/a.tif", id="%2E%2E"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/../a.tif", id="dotdot"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/%252e%252e/a.tif", id="double"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/..\\a.tif", id="backslash"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/%5Ca.tif", id="%5C"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/a.zip", id="not-a-tiff"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/a b.tif", id="space"),
    pytest.param("https://data.fs.usda.gov/geodata/rastergateway/a.tif\n", id="newline"),
    pytest.param(GATEWAY + "a.tif?u=http://evil.example/x.tif", id="query"),
    pytest.param(GATEWAY + "a.tif#frag", id="fragment"),
    pytest.param(GATEWAY + "x%2Fa.tif", id="%2F"),
    pytest.param(GATEWAY + "x%2fa.tif", id="%2f"),
    pytest.param(GATEWAY + "x%252fa.tif", id="double-%2f"),
    pytest.param(GATEWAY + "a//b.tif", id="empty-segment"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_a_publisher_url_outside_the_gateway_is_a_miss(tmp_path: Path, url: str) -> None:
    q = sheet()
    context = make_context(tmp_path, [row(q, publisher_url=url)])
    with pytest.raises(CatalogueMiss, match="not a Forest Service GeoTIFF"):
        recorded_sheet(q, unit=UNIT, pins={}, context=context)
    pins = {q.secoord: FsPin(q.secoord, len(BODY), SHA)}
    with pytest.raises(CatalogueMiss, match="not a Forest Service GeoTIFF"):
        recorded_sheet(q, unit=UNIT, pins=pins, context=context)


@pytest.mark.parametrize("suffix", ["a.tif", "a.TIFF", "data3/00000/fstopo/1230000.tiff"])
def test_a_gateway_geotiff_url_is_accepted(tmp_path: Path, suffix: str) -> None:
    q = sheet()
    context = make_context(tmp_path, [row(q, publisher_url=GATEWAY + suffix)])
    assert recorded_sheet(q, unit=UNIT, pins={}, context=context).url == GATEWAY + suffix


# -- other unverified catalogue entries get no new powers ------------------------------

UNVERIFIED_ROWS = [
    ("acma-register", "spectra_rrl.zip", "unverified-zip"),
    ("repeater-snapshots", "etcc.csv", "unverified-fetch"),
    ("repeater-snapshots", "brandmeister.json", "unverified-fetch"),
    ("repeater-snapshots", "hearham.json", "unverified-fetch"),
]


@pytest.mark.parametrize(("unit", "name", "check"), UNVERIFIED_ROWS)
def test_context_unverified_returns_the_row_and_grants_nothing(
    tmp_path: Path, unit: str, name: str, check: str
) -> None:
    context = make_context(tmp_path, [artifact(unit, name, b"x", publisher_check=check)])
    before = dict(vars(context))
    got = context.unverified(unit, name)
    assert (got.unit, got.name, got.publisher_check) == (unit, name, check)
    assert not hasattr(context, "hold_unverified")
    assert {k: v for k, v in vars(context).items() if k != "_notes_lock"} == {
        k: v for k, v in before.items() if k != "_notes_lock"
    }
    # a digest-checked row is not an unverified one
    other = make_context(tmp_path / "o", [artifact(unit, name, b"x", publisher_check="sha256")])
    with pytest.raises(CatalogueMiss, match="explicitly unverified"):
        other.unverified(unit, name)


def test_unverified_rows_do_not_satisfy_a_consent_gate(tmp_path: Path) -> None:
    gate = ConsentGate(
        risk_categories=[RiskCategory.unlicensed_transmission],
        env_var="HAMMUNITION_ACCEPT_TEST_GATE",
        disclosure="This software can transmit on frequencies you choose, at power you set.",
        affirmation="Do you hold the authorization you need to transmit with it?",
    )
    rows = [artifact(u, n, b"x", publisher_check=c) for u, n, c in UNVERIFIED_ROWS]
    context = make_context(tmp_path, rows)
    for unit, name, _ in UNVERIFIED_ROWS:
        context.unverified(unit, name)
        with pytest.raises(ConsentUnavailable):
            resolve_consent(gate, "test", environ={}, prompt=None, assume_yes=True)


# -- resolve_fstopo ------------------------------------------------------------------

ALPHA = parse_row("0 0 0.125 0.125 1230000 11 ZZ Alpha")
BETA = parse_row("0 0.125 0.125 0.25 1230001 0 ZZ Beta")
INDEX = parse_index("0 0 0.125 0.125 1230000 11 ZZ Alpha\n0 0.125 0.125 0.25 1230001 0 ZZ Beta\n")
ZZ = ("atlantis/oceania", "atlantis-oceania")


def file_url(secoord: int) -> str:
    return f"{GATEWAY}data3/00000/fstopo/{secoord}.tiff"


class Head:
    """The gateway: a redirect, then a size.  Counts the asks."""

    def __init__(self, size: int = len(BODY)) -> None:
        self.size = size
        self.asked: list[str] = []

    def __call__(self, url: str) -> tuple[int, int, str | None]:
        self.asked.append(url)
        for q in (ALPHA, BETA):
            if url == map_url(q.secoord):
                return 302, 0, file_url(q.secoord)
            if url == file_url(q.secoord):
                return 200, self.size, None
        raise FstopoError(f"{url}: nothing here")


class Down:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, url: str) -> tuple[int, int, str | None]:
        self.asked.append(url)
        raise PublisherUnavailable(url, "timed out", 4)


def zrow(q: FsQuad, **changes: Any) -> dict[str, object]:
    return row(q, publisher_url=file_url(q.secoord), **changes)


def installed_for(tmp_path: Path, *quads: FsQuad) -> Path:
    installed = tmp_path / "installed"
    installed.mkdir(exist_ok=True)
    (installed / f"{ZZ[1]}.quads").write_text(render_record(RegionSheets(*ZZ, quads, "all")))
    return installed


def planned(tmp_path: Path, context: Any, head: Any, **extra: Any) -> Any:
    kwargs: dict[str, Any] = dict(
        installed=None,
        index=INDEX,
        pins={},
        region_probe=RegionProbe({}),
        gateway=GatewayProbe(head),
        context=context,
        unit=UNIT,
    )
    kwargs.update(extra)
    if kwargs["installed"] is None:
        kwargs["installed"] = installed_for(tmp_path, ALPHA, BETA)
    return resolve_fstopo([ZZ], **kwargs)


def test_offline_every_sheet_resolves_from_the_catalogue_and_asks_nothing(tmp_path: Path) -> None:
    context = make_context(tmp_path / "ctx", [zrow(ALPHA), zrow(BETA)])
    head = Head()
    got, _ = planned(tmp_path, context, head)
    assert [(f.quad, f.sha256, f.url) for f in got.fetch] == [
        (ALPHA, None, file_url(ALPHA.secoord)),
        (BETA, None, file_url(BETA.secoord)),
    ]
    assert head.asked == []
    assert (UNIT, ALPHA.name) in context.notes


def test_offline_an_unpinned_sheet_does_not_make_the_unit_all_pinned(tmp_path: Path) -> None:
    context = make_context(tmp_path / "ctx", [zrow(ALPHA), zrow(BETA)])
    pins = {ALPHA.secoord: FsPin(ALPHA.secoord, len(BODY), SHA)}
    got, _ = planned(tmp_path, context, Head(), pins=pins)
    assert got.pinned == frozenset({ALPHA.secoord})
    assert not got.all_pinned(got.regions[0])
    by_name = {f.quad: f for f in got.fetch}
    assert by_name[ALPHA].sha256 == SHA and by_name[BETA].sha256 is None


def test_offline_every_sheet_pinned_still_checks_the_repository_pin(tmp_path: Path) -> None:
    pins = {s.secoord: FsPin(s.secoord, len(BODY), SHA) for s in (ALPHA, BETA)}
    good = make_context(tmp_path / "g", [zrow(ALPHA), zrow(BETA)])
    got, _ = planned(tmp_path, good, Head(), pins=pins)
    assert got.all_pinned(got.regions[0])
    bad = {s.secoord: FsPin(s.secoord, len(BODY), "f" * 64) for s in (ALPHA, BETA)}
    with pytest.raises(CatalogueMiss, match="repository sha256 pin"):
        planned(tmp_path, make_context(tmp_path / "b", [zrow(ALPHA), zrow(BETA)]), Head(), pins=bad)


def test_one_missing_sheet_defers_the_whole_selection_offline(tmp_path: Path) -> None:
    context = make_context(tmp_path / "ctx", [zrow(ALPHA)])
    with pytest.raises(CatalogueMiss, match="Beta"):
        planned(tmp_path, context, Head())


def test_one_missing_sheet_after_an_outage_defers_the_whole_selection(tmp_path: Path) -> None:
    context = make_context(tmp_path / "ctx", [zrow(ALPHA)], offline=False)
    seen: list[str] = []
    with pytest.raises(CatalogueMiss, match="not answering right now"):
        planned(tmp_path, context, Down(), on_outage=lambda name, exc: seen.append(name))
    assert seen == []


def test_an_outage_with_a_record_resolves_from_it_and_says_so(tmp_path: Path) -> None:
    context = make_context(tmp_path / "ctx", [zrow(ALPHA), zrow(BETA)], offline=False)
    got, _ = planned(tmp_path, context, Down())
    assert len(got.fetch) == 2 and all(f.sha256 is None for f in got.fetch)
    assert "publisher unreachable" in context.notes[(UNIT, ALPHA.name)]


def test_an_outage_without_a_bunker_is_still_a_per_sheet_deferral(tmp_path: Path) -> None:
    seen: list[str] = []
    got, _ = planned(
        tmp_path, ResolutionContext(), Down(), on_outage=lambda name, exc: seen.append(name)
    )
    assert sorted(seen) == sorted([ALPHA.name, BETA.name]) and got.deferred


def test_online_the_gateway_answers_and_the_record_is_not_consulted(tmp_path: Path) -> None:
    context = make_context(tmp_path / "ctx", [], offline=False)
    head = Head()
    got, _ = planned(tmp_path, context, head)
    assert [f.size for f in got.fetch] == [len(BODY), len(BODY)]
    assert context.notes == {} and head.asked


def test_online_a_changed_pin_size_still_refuses_and_never_falls_back(tmp_path: Path) -> None:
    context = make_context(tmp_path / "ctx", [zrow(ALPHA), zrow(BETA)], offline=False)
    pins = {ALPHA.secoord: FsPin(ALPHA.secoord, len(BODY), SHA)}
    with pytest.raises(FstopoError, match=r"--pin"):
        planned(tmp_path, context, Head(size=99), pins=pins)
    assert context.notes == {}


def test_a_context_without_a_unit_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unit"):
        planned(tmp_path, make_context(tmp_path / "ctx", []), Head(), unit=None)


def test_without_a_context_nothing_changes(tmp_path: Path) -> None:
    head = Head()
    got, _ = planned(tmp_path, None, head, unit=None)
    assert len(got.fetch) == 2 and head.asked


@pytest.mark.parametrize(("offline", "asked"), [(True, False), (False, True)])
def test_offline_skips_the_installed_recheck_and_online_keeps_it(
    tmp_path: Path, offline: bool, asked: bool
) -> None:
    installed = installed_for(tmp_path, ALPHA, BETA)
    for q in (ALPHA, BETA):
        (installed / f"{q.name}.tif").write_bytes(b"II*\x00")
    context = make_context(tmp_path / "ctx", [], offline=offline)
    checks = SpyChecks()
    got, _ = planned(tmp_path, context, Head(), installed=installed, checks=checks)
    assert got.current == (ALPHA, BETA) and got.fetch == ()
    assert bool(checks.asked) is asked


# -- through the CLI -----------------------------------------------------------------


def real_quad() -> FsQuad:
    from hammunition.fstopo import load_index

    return load_index(CATALOG / "data/fstopo-quads.txt").quads[0]


def enrol(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[Path, str]:
    q = real_quad()
    body = render_record(RegionSheets(REGION, SLUG, (q,), "all")).encode()
    relative = f"inputs/fstopo-selection/{REGION}.quads"
    inputs = [
        {
            "kind": "fstopo-selection", "region": REGION, "name": f"{REGION}.quads",
            "path": relative, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
            "fetched": "2026-10-06T03:00:00Z",
        }
    ]  # fmt: skip
    export, mirror = enrol_file_bunker(
        tmp_path, rows, inputs=inputs, station={"map_regions": [REGION], "topo_all": True}
    )
    target = export / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return export, mirror


def cli_setup(machine: Path, monkeypatch: pytest.MonkeyPatch) -> Head:
    apt_state(monkeypatch, installed="1.0")
    q = real_quad()

    class Gateway(Head):
        def __call__(self, url: str) -> tuple[int, int, str | None]:
            self.asked.append(url)
            if url == map_url(q.secoord):
                return 302, 0, file_url(q.secoord)
            if url == file_url(q.secoord):
                return 200, self.size, None
            raise AssertionError(f"unexpected gateway ask {url}")

    head = Gateway()

    def maps(*args: object, **kwargs: object) -> MapResolution:
        return MapResolution(kept=(KeptRegion(REGION, SLUG, None, "installed"),))

    class DownOutline:
        def text(self, url: str) -> str:
            raise TimeoutError("outline link unavailable")

        def head(self, url: str) -> tuple[int, int, str | None]:
            raise AssertionError("never HEADs Geofabrik")

    monkeypatch.setattr(cli, "resolve_map_regions", maps)
    monkeypatch.setattr(cli, "GatewayProbe", lambda: GatewayProbe(head))
    monkeypatch.setattr(cli, "UrllibProbe", DownOutline)
    monkeypatch.setattr(cli.POLICY, "sleep", lambda _: None)
    cli.POLICY.reset()
    return head


def cli_rows() -> list[dict[str, object]]:
    return [zrow(real_quad())]


def warning_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "unverified" in line]


def routing(line: str) -> str:
    """*line* without the words that say which source a fetch asks, and nothing
    else: the clause naming the check ("the size is checked") is kept, so the
    online and offline lines are still compared on it exactly."""
    line = re.sub(
        r" — the LAN mirror first, then the publisher; (the \w+ is checked) either way",
        r" — \1",
        line,
    )
    line = re.sub(
        r" — Bunker only, offline: the publisher is not asked; (the \w+ is checked)",
        r" — \1",
        line,
    )
    return re.sub(r", then \S+", "", line)


def test_cli_offline_plans_unverified_with_zero_sockets(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    enrol(tmp_path, cli_rows())
    head = cli_setup(machine, monkeypatch)
    attempts: list[object] = []

    def record(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise OSError("the network is forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", record)
    monkeypatch.setattr(socket, "getaddrinfo", record)
    rc, out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", "--json", UNIT)
    assert rc == 0, err
    assert head.asked == [] and attempts == []
    assert any(
        "offline; resolved from Bunker bunker" in n
        for n in parse_one(out)["install"]["region_notes"]
    )


def test_cli_an_unverified_row_meets_the_same_disclosure_offline_as_online(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The plan's unverified warning and the fetch line's verified_by are the
    disclosure an unverified FSTopo sheet always meets; the Bunker's sha256 and
    ``--offline`` change neither, and nothing records a consent."""
    enrol(tmp_path, cli_rows())
    head = cli_setup(machine, monkeypatch)
    rc, online, err = run(capsys, CATALOG, "install", "--dry-run", UNIT)
    assert rc == 0, err
    assert head.asked  # the gateway really was asked online
    rc, offline, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", UNIT)
    assert rc == 0, err
    on, off = warning_lines(online), warning_lines(offline)
    assert any("1 quad(s) unverified" in line for line in off), offline
    assert any(UNVERIFIED in line for line in off), offline
    # Where the bytes come from is the one thing the fetch line adds (the LAN
    # mirror first online, the Bunker alone offline, Task 12); the disclosure
    # in front of it is identical.
    assert any("Bunker only, offline" in line for line in off), offline
    assert [routing(line) for line in off] == [routing(line) for line in on]
    assert "every FSTopo quad your regions need is pinned" not in offline
    assert "hold_unverified" not in offline


def test_no_hold_unverified_policy_exists_in_the_engine() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    hits = [
        p.name
        for p in root.rglob("*.py")
        if "hold_unverified" in p.read_text() and p.name != "artifacts.py"
    ]
    assert hits == []


# -- the live Location is held to the same validator --------------------------------


def _locate(location: str) -> tuple[str, int]:
    def head(url: str) -> tuple[int, int, str | None]:
        if url == map_url(1230000):
            return 302, 0, location
        return 200, 7, None

    return GatewayProbe(head).locate(1230000)


def test_online_a_normal_gateway_location_still_resolves() -> None:
    assert _locate(file_url(1230000)) == (file_url(1230000), 7)


@pytest.mark.parametrize(
    "location",
    [
        GATEWAY + "%2e%2e/%2e%2e/outside.tif",
        GATEWAY + "a.tif?u=http://evil.example/x.tif",
        GATEWAY + "x%2Fa.tif",
        GATEWAY + "a//b.tif",
    ],
)
def test_online_a_hostile_location_is_refused(location: str) -> None:
    with pytest.raises(FstopoError, match="refused, not followed"):
        _locate(location)


# -- personal mode ignores share (design open question 3) ---------------------------


def test_personal_mode_ignores_share(tmp_path: Path) -> None:
    """Design open question 3, decided: in a personal-mode Bunker a row shared
    ``owner:laptop-a`` still answers laptop-b, because personal mode ignores
    ``share``; only group mode filters by owner. Changing that rule must change
    this test (and the group-mode test above)."""
    q = sheet()
    context = make_context(
        tmp_path, [row(q, share="owner:laptop-a")], mode="personal", enrolment_id="laptop-b"
    )
    assert recorded_sheet(q, unit=UNIT, pins={}, context=context).url == GOOD_URL


def test_cli_offline_a_pinned_sheet_the_plan_resolves_is_downloaded_through_the_mirror(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Task 12 fix round: the offline plan resolves a pinned sheet, and the install
    then fetches it from the Bunker (never the publisher), against the repository's
    own pin. The fetch step is the one the CLI built, run with every socket refused."""
    from hammunition.backends.base import Action
    from hammunition.backends.fstopo import FsTopoBackend
    from test_offline_context import put

    q = real_quad()
    export, mirror = enrol(tmp_path, [zrow(q)])
    head = cli_setup(machine, monkeypatch)
    monkeypatch.setattr(
        "hammunition.topo_plan.load_fstopo_pins",
        lambda path: {q.secoord: FsPin(q.secoord, len(BODY), SHA)},
    )
    built: list[Action] = []
    real_steps = FsTopoBackend.steps

    def capture(self: FsTopoBackend, manifest: Any, block: Any) -> Any:
        steps = real_steps(self, manifest, block)
        built.extend(s for s in steps if isinstance(s, Action) and s.kind == "fetch")
        return steps

    monkeypatch.setattr(FsTopoBackend, "steps", capture)
    attempts: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise OSError("the network is forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    rc, out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", UNIT)
    assert rc == 0, err
    assert "unverified" not in out.split("Fetch FSTopo quad")[1].splitlines()[0]
    (fetch,) = built
    assert PINNED in fetch.description and "Bunker only, offline" in fetch.description
    at_mirror = f"{mirror}/{UNIT}/{q.name}"
    assert fetch.sources == (at_mirror,)
    put(export, UNIT, q.name, BODY)
    outcome = fetch.perform()
    assert outcome.startswith("downloaded") and "verified against the pin" in outcome
    assert fetch.facts["source"] == "mirror" and fetch.facts["fetched_from"] == at_mirror
    assert head.asked == [] and attempts == []


def test_routing_keeps_the_check_clause_so_a_changed_check_is_seen() -> None:
    online = "# 1: Fetch X — unverified — the LAN mirror first, then the publisher; the size is checked either way"
    offline = "# 1: Fetch X — unverified — Bunker only, offline: the publisher is not asked; the size is checked"
    assert routing(online) == routing(offline) == "# 1: Fetch X — unverified — the size is checked"
    weaker = offline.replace("the size is checked", "nothing is checked")
    assert routing(weaker) != routing(online)
