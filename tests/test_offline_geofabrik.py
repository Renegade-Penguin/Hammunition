# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811
from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path

import pytest

from bunker_fixtures import artifact, make_context
from hammunition.catalogue import PublisherUnavailable
from hammunition.geofabrik import BASE, Pin, resolve
from hammunition.resolution import CatalogueMiss
from hammunition.retry import RetryingProbe, RetryPolicy
from json_support import parse_one
from test_geofabrik import FakeProbe
from test_offline_context import apt_state, enrol_file_bunker, machine, run  # noqa: F401

cli = importlib.import_module("hammunition.cli.main")
REGION = "europe/monaco"
TODAY = date(2026, 10, 7)
CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def row(snapshot: str = "261006", /, **changes: object) -> dict[str, object]:
    values: dict[str, object] = dict(
        publisher_check="md5",
        publisher_digest="a" * 32,
        publisher_name=f"monaco-{snapshot}.osm.pbf",
        publisher_size=3,
        publisher_url=f"{BASE}/{REGION}-{snapshot}.osm.pbf",
    )
    values.update(changes)
    return artifact("osm-regions", REGION, b"pbf", **values)


@pytest.mark.parametrize(
    ("freshness", "snapshot"),
    [("latest", "261006"), ("monthly", "261001"), ("monthly", "260901"), ("yearly", "260101")],
)
def test_recorded_extract_never_asks_publisher(
    tmp_path: Path, freshness: str, snapshot: str
) -> None:
    context = make_context(tmp_path, [row(snapshot)])
    probe = FakeProbe({}, {})
    got = resolve(REGION, freshness, today=TODAY, pins={}, probe=probe, context=context)
    assert (got.snapshot, got.size, got.md5) == (snapshot, 3, "a" * 32)
    assert probe.seen == []
    assert ("osm-regions", REGION) in context.notes


@pytest.mark.parametrize(
    "changes",
    [
        {"publisher_name": "other-261006.osm.pbf"},
        {"publisher_url": f"{BASE}/{REGION}-261005.osm.pbf"},
        {"publisher_size": 4},
        {"publisher_digest": "a" * 31},
        {"publisher_check": "unverified-fetch"},
    ],
)
def test_bad_record_is_refused(tmp_path: Path, changes: dict[str, object]) -> None:
    context = make_context(tmp_path, [row(**changes)])
    with pytest.raises(CatalogueMiss):
        resolve(REGION, "latest", today=TODAY, pins={}, probe=FakeProbe({}, {}), context=context)
    assert not context.notes


def test_monthly_does_not_substitute_stale_snapshot(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row("260801")])
    with pytest.raises(CatalogueMiss, match="no monthly candidate"):
        resolve(REGION, "monthly", today=TODAY, pins={}, probe=FakeProbe({}, {}), context=context)


def test_repository_pin_mismatch_refuses_without_using_record(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row("261001")])
    pin = Pin(REGION, "261001", 3, "b" * 64)
    with pytest.raises(CatalogueMiss, match="sha256 pin"):
        resolve(
            REGION,
            "monthly",
            today=TODAY,
            pins={(REGION, pin.snapshot): pin},
            probe=FakeProbe({}, {}),
            context=context,
        )
    assert not context.notes


class DownProbe:
    def head(self, url: str) -> tuple[int, int, str | None]:
        raise TimeoutError("publisher down")

    def text(self, url: str) -> str:
        raise TimeoutError("publisher down")


def test_missing_fallback_preserves_original_publisher_error(tmp_path: Path) -> None:
    context = make_context(tmp_path, [], offline=False)
    policy = RetryPolicy(sleep=lambda _: None, notify=lambda _: None)
    with pytest.raises(PublisherUnavailable, match=r"publisher|timed out") as caught:
        resolve(
            REGION,
            "latest",
            today=TODAY,
            pins={},
            probe=RetryingProbe(DownProbe(), policy),
            context=context,
        )
    assert caught.value.attempts == policy.attempts
    assert not context.notes


@pytest.mark.parametrize("offline", [True, False])
def test_cli_plans_verified_regions_with_provenance(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    offline: bool,
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(
        tmp_path, [row()], station={"map_regions": [REGION], "map_freshness": "latest"}
    )
    probe_calls: list[str] = []

    class TrackedProbe(DownProbe):
        def head(self, url: str) -> tuple[int, int, str | None]:
            probe_calls.append(url)
            return super().head(url)

    monkeypatch.setattr(cli, "UrllibProbe", TrackedProbe)
    monkeypatch.setattr(cli.POLICY, "sleep", lambda _: None)
    cli.POLICY.reset()
    flags = ("--offline",) if offline else ()
    rc, out, err = run(capsys, CATALOG, "install", *flags, "--dry-run", "--json", "osm-regions")
    assert rc == 0, err
    assert len(probe_calls) == (0 if offline else cli.POLICY.attempts)
    doc = parse_one(out)["install"]
    note = "offline;" if offline else "publisher unreachable;"
    assert any(note in n and "resolved from Bunker bunker" in n for n in doc["region_notes"])
    fetch = [c for c in doc["commands"] if c["action"] == "fetch"]
    assert any("resolved from Bunker bunker" in c["description"] for c in fetch)


def test_pinned_offline_region_skips_reachability_head(tmp_path: Path) -> None:
    import hashlib

    from hammunition.cli.main import resolve_map_regions
    from hammunition.manifest.load import load_catalog
    from hammunition.plan import InstallPlan, PlannedPackage
    from hammunition.station import Station
    from test_offline_context import TARGET

    snapshot = "261001"
    pin = Pin(REGION, snapshot, 3, hashlib.sha256(b"pbf").hexdigest())
    root = tmp_path / "catalog"
    (root / "data").mkdir(parents=True)
    (root / "data/geofabrik-pins.yaml").write_text(
        f"pins:\n  - region: {REGION}\n    snapshot: '{snapshot}'\n    size: 3\n    sha256: {pin.sha256}\n"
    )
    manifest = load_catalog(CATALOG / "packages")["osm-regions"]
    plan = InstallPlan(TARGET, (PlannedPackage(manifest, manifest.install[0], ()),))
    context = make_context(tmp_path, [row(snapshot)])
    probe = FakeProbe({}, {})
    got = resolve_map_regions(
        plan,
        Station(map_regions=(REGION,), map_freshness="monthly"),
        root,
        probe=probe,
        today=TODAY,
        installed=tmp_path / "installed",
        context=context,
    )
    assert got.files[0].sha256 == pin.sha256
    assert probe.seen == []


def test_installed_region_is_kept_when_catalogue_has_no_record(tmp_path: Path) -> None:
    from hammunition.cli.main import resolve_map_regions
    from hammunition.manifest.load import load_catalog
    from hammunition.plan import InstallPlan, PlannedPackage
    from hammunition.station import Station
    from test_offline_context import TARGET

    manifest = load_catalog(CATALOG / "packages")["osm-regions"]
    plan = InstallPlan(TARGET, (PlannedPackage(manifest, manifest.install[0], ()),))
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "europe-monaco.osm.pbf").write_bytes(b"installed")
    context = make_context(tmp_path, [])
    got = resolve_map_regions(
        plan,
        Station(map_regions=(REGION,), map_freshness="latest"),
        tmp_path,
        probe=DownProbe(),
        today=TODAY,
        installed=installed,
        context=context,
    )
    assert not got.files and got.kept[0].region == REGION
    assert not context.notes


@pytest.mark.parametrize("status", [404, 503])
def test_pinned_reachability_falls_back_only_after_exhaustion(
    tmp_path: Path,
    status: int,
) -> None:
    import hashlib

    from hammunition.cli.main import resolve_map_regions
    from hammunition.geofabrik import GeofabrikError
    from hammunition.manifest.load import load_catalog
    from hammunition.plan import InstallPlan, PlannedPackage
    from hammunition.station import Station
    from test_offline_context import TARGET

    snapshot = "261001"
    root = tmp_path / "catalog"
    (root / "data").mkdir(parents=True)
    (root / "data/geofabrik-pins.yaml").write_text(
        f"pins:\n  - region: {REGION}\n    snapshot: '{snapshot}'\n    size: 3\n    sha256: {hashlib.sha256(b'pbf').hexdigest()}\n"
    )
    manifest = load_catalog(CATALOG / "packages")["osm-regions"]
    plan = InstallPlan(TARGET, (PlannedPackage(manifest, manifest.install[0], ()),))
    context = make_context(tmp_path, [row(snapshot)], offline=False)
    url = f"{BASE}/{REGION}-{snapshot}.osm.pbf"
    probe = FakeProbe({url: (status, 0, None)}, {})
    policy = RetryPolicy(sleep=lambda _: None, notify=lambda _: None)
    station = Station(map_regions=(REGION,), map_freshness="monthly")
    if status == 404:
        with pytest.raises(GeofabrikError, match="HTTP 404"):
            resolve_map_regions(
                plan,
                station,
                root,
                probe=RetryingProbe(probe, policy),
                today=TODAY,
                installed=tmp_path / "installed",
                context=context,
            )
        assert not context.notes
        assert probe.seen == [url]
    else:
        got = resolve_map_regions(
            plan,
            station,
            root,
            probe=RetryingProbe(probe, policy),
            today=TODAY,
            installed=tmp_path / "installed",
            context=context,
        )
        assert got.files[0].snapshot == snapshot
        assert "publisher unreachable" in context.notes[("osm-regions", REGION)]
        assert probe.seen == [url] * policy.attempts


def test_zero_publisher_size_is_refused_before_resolution(tmp_path: Path) -> None:
    from hammunition.catalogue import CatalogueError

    with pytest.raises(CatalogueError, match="publisher_size: must be positive"):
        make_context(tmp_path, [row(publisher_size=0)])


def test_require_payload_checks_recorded_publisher_digest(tmp_path: Path) -> None:
    context = make_context(tmp_path, [row()])
    with pytest.raises(CatalogueMiss, match="publisher digest"):
        context.require_payload("osm-regions", REGION, publisher_digest="b" * 32, size=3)
    assert not context.notes


def test_reachability_fallback_refuses_a_different_recorded_publisher_digest(
    tmp_path: Path,
) -> None:
    from hammunition.cli.main import resolve_map_regions
    from hammunition.manifest.load import load_catalog
    from hammunition.plan import InstallPlan, PlannedPackage
    from hammunition.station import Station
    from test_offline_context import TARGET

    url = f"{BASE}/{REGION}-261006.osm.pbf"

    class Probe:
        def __init__(self) -> None:
            self.dated_calls = 0

        def head(self, requested: str) -> tuple[int, int, str | None]:
            if requested.endswith("-latest.osm.pbf"):
                return 302, 0, url
            self.dated_calls += 1
            if self.dated_calls == 1:
                return 200, 3, None
            raise TimeoutError("reachability down")

        def text(self, requested: str) -> str:
            return "a" * 32 + "  monaco-261006.osm.pbf"

    context = make_context(tmp_path, [row(publisher_digest="b" * 32)], offline=False)
    manifest = load_catalog(CATALOG / "packages")["osm-regions"]
    plan = InstallPlan(TARGET, (PlannedPackage(manifest, manifest.install[0], ()),))
    probe = Probe()
    policy = RetryPolicy(sleep=lambda _: None, notify=lambda _: None)
    with pytest.raises(CatalogueMiss, match="publisher digest"):
        resolve_map_regions(
            plan,
            Station(map_regions=(REGION,), map_freshness="latest"),
            tmp_path,
            probe=RetryingProbe(probe, policy),
            today=TODAY,
            installed=tmp_path / "installed",
            context=context,
        )
    assert probe.dated_calls == 1 + policy.attempts
    assert not context.notes
