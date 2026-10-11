# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The resolution context, ``--offline`` and the exhausted-retry fallback.  #381.

Every catalogue here is really signed with OpenSSH (``bunker_fixtures``), and
every CLI case runs in a temporary XDG tree with the target stubbed: nothing
reads or writes the machine's own station, state or apt.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import shutil
import ssl
import subprocess
import sys
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from bunker_fixtures import artifact, document, key, make_context, signed
from hammunition.backends.apt import AptBackend, AptPackageState, AptSimulation
from hammunition.catalogue import PublisherUnavailable
from hammunition.distro import Target
from hammunition.keystrength import classify
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import DataInstall, PackageManifest, RegisterInstall
from hammunition.plan import (
    Blocker,
    InstallPlan,
    PlanError,
    PlannedPackage,
    cached_data_pin,
    catalogue_deferral,
    preflight_data,
)
from hammunition.resolution import CatalogueMiss, ResolutionContext
from hammunition.retry import RetryPolicy
from hammunition.signers import EnrolledKey, MirrorState, save_mirror
from hammunition.station import Station, save_station
from json_support import FIXTURE_CATALOG, parse_one

cli = importlib.import_module("hammunition.cli.main")

REPO = Path(__file__).resolve().parents[1]
TARGET = Target(
    distro="debian", version="13", arch="x86_64", pretty_name="Debian GNU/Linux 13 (trixie)"
)
RECORDED = "2026-10-07T12:00:00Z"


# -- the context itself -------------------------------------------------------


def test_fallback_only_after_retry_exhaustion(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path, [artifact("osm-regions", "europe/monaco", b"stored")], offline=False
    )
    seen: list[str] = []
    policy = RetryPolicy(attempts=3, sleep=lambda _: None, notify=lambda _: None)

    def unavailable() -> int:
        seen.append("publisher")
        raise TimeoutError("dead link")

    def recorded() -> int:
        return ctx.require_payload("osm-regions", "europe/monaco").size or 0

    got = ctx.choose(
        "osm-regions",
        "europe/monaco",
        lambda: policy.call("https://example.invalid/x", unavailable),
        recorded,
    )
    assert got == len(b"stored")
    assert seen == ["publisher"] * 3
    assert ctx.verified is not None
    assert ctx.notes[("osm-regions", "europe/monaco")] == (
        f"publisher unreachable; resolved from Bunker bunker ({ctx.verified.key.id}), "
        f"recorded {RECORDED}"
    )


def test_offline_never_calls_online(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, [], offline=True)

    def forbidden() -> str:
        pytest.fail("offline called a publisher")

    assert ctx.choose("osm-regions", "europe/monaco", forbidden, lambda: "pin") == "pin"
    assert ctx.verified is not None
    assert ctx.notes[("osm-regions", "europe/monaco")].startswith("offline; resolved from Bunker")


def test_offline_without_catalogue_refuses_before_callbacks() -> None:
    def forbidden() -> str:
        pytest.fail("a missing catalogue must refuse before any callback")

    with pytest.raises(CatalogueMiss, match="hammunition mirror enrol URL"):
        ResolutionContext(offline=True).choose("osm-regions", "europe/monaco", forbidden, forbidden)


def test_online_without_a_catalogue_keeps_the_publisher_error() -> None:
    """No enrolment and no flag: an outage is the outage it always was."""

    def down() -> str:
        raise PublisherUnavailable("https://example.invalid/x", "HTTP 503", 3)

    with pytest.raises(PublisherUnavailable):
        ResolutionContext().choose("osm-regions", "europe/monaco", down, lambda: "never")


def test_the_catalogue_is_asked_once_and_only_after_exhaustion(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, [artifact("u", "n", b"x")], offline=False)
    asked: list[str] = []
    attempts: list[int] = []
    real = ctx.entry

    def counting(unit: str, name: str) -> Any:
        asked.append(f"{unit}/{name}")
        return real(unit, name)

    ctx.entry = counting  # type: ignore[method-assign]
    policy = RetryPolicy(attempts=3, sleep=lambda _: None, notify=lambda _: None)

    def down() -> int:
        attempts.append(1)
        assert asked == [], "the catalogue was read before the retries were spent"
        raise TimeoutError("dead link")

    def recorded() -> int:
        assert len(attempts) == 3, "recorded() ran before the third attempt failed"
        return ctx.entry("u", "n").size or 0

    got = ctx.choose("u", "n", lambda: policy.call("https://example.invalid/x", down), recorded)
    assert got == 1 and asked == ["u/n"]
    assert ctx.notes


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://example.invalid/x", 404, "Not Found", None, None),  # type: ignore[arg-type]
        ssl.SSLCertVerificationError("certificate verify failed"),
    ],
    ids=["http-404", "tls-certificate"],
)
def test_a_404_and_a_bad_certificate_are_answers_not_outages(
    tmp_path: Path, error: Exception
) -> None:
    ctx = make_context(tmp_path, [artifact("u", "n", b"x")], offline=False)
    policy = RetryPolicy(attempts=3, sleep=lambda _: None, notify=lambda _: None)
    calls: list[int] = []

    def answers() -> int:
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        ctx.choose(
            "u",
            "n",
            lambda: policy.call("https://example.invalid/x", answers),
            lambda: pytest.fail("a final answer is not an outage"),
        )
    assert calls == [1]
    assert not ctx.notes


def test_a_missing_entry_names_the_bunker(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, [], offline=False)
    with pytest.raises(CatalogueMiss, match="not on Bunker bunker, publisher unreachable"):
        ctx.entry("osm-regions", "europe/monaco")


def test_a_withdrawn_entry_is_not_current(tmp_path: Path) -> None:
    row = artifact("u", "n", b"x", status="withdrawn", reason="licence changed")
    ctx = make_context(tmp_path, [row])
    with pytest.raises(CatalogueMiss, match="withdrawn: licence changed"):
        ctx.entry("u", "n")


def test_require_payload_checks_the_repository_pin(tmp_path: Path) -> None:
    body = b"payload"
    ctx = make_context(tmp_path, [artifact("u", "n", body)])
    digest = hashlib.sha256(body).hexdigest()
    assert ctx.require_payload("u", "n", sha256=digest, size=len(body)).size == len(body)
    with pytest.raises(CatalogueMiss, match="sha256 pin"):
        ctx.require_payload("u", "n", sha256="0" * 64)
    with pytest.raises(CatalogueMiss, match="expected size"):
        ctx.require_payload("u", "n", size=1)


def test_unverified_needs_the_explicit_label(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        [
            artifact("u", "plain", b"x"),
            artifact("u", "labelled", b"x", publisher_check="unverified-fetch"),
        ],
    )
    assert ctx.unverified("u", "labelled").publisher_check == "unverified-fetch"
    with pytest.raises(CatalogueMiss, match="explicitly unverified"):
        ctx.unverified("u", "plain")


def test_every_vouched_line_carries_the_signer_warnings(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, [artifact("u", "a", b"x"), artifact("u", "b", b"y")])
    assert ctx.verified is not None
    ctx.verified = dataclasses.replace(ctx.verified, warnings=("weak key", "catalogue is old"))
    for name in ("a", "b"):
        assert ctx.note("u", name, fallback=False).endswith("; weak key; catalogue is old")


def test_same_event_for_a_profile_member_a_typed_unit_and_a_half_supplied_unit(
    tmp_path: Path,
) -> None:
    catalog = load_catalog(REPO / "catalog/packages")
    manifest = catalog["country-files"]
    block = next(b for b in manifest.install if isinstance(b.install, DataInstall))
    miss = CatalogueMiss("country-files/x: not on Bunker bunker, publisher unreachable")
    member = PlannedPackage(manifest, block, (), requested_by=("profile reference",))
    typed = PlannedPackage(manifest, block, ())
    deferral = catalogue_deferral(member, miss)
    assert deferral.kind == "package" and "not on Bunker bunker" in deferral.why
    assert "populate this selection on the Bunker" in deferral.remedy
    with pytest.raises(PlanError) as refused:
        catalogue_deferral(typed, miss)
    assert "not on Bunker bunker, publisher unreachable" in str(refused.value)


def two_artifact_unit() -> tuple[PackageManifest, list[bytes]]:
    bodies = [b"first payload", b"second payload"]
    manifest = PackageManifest.model_validate(
        {
            "name": "fixture-pair",
            "version": "1.0",
            "summary": "Two data files",
            "categories": ["references"],
            "install": [
                {
                    "install": {
                        "method": "data",
                        "artifacts": [
                            {
                                "url": f"https://example.invalid/{n}.bin",
                                "sha256": hashlib.sha256(b).hexdigest(),
                                "size": len(b),
                                "format": "file",
                                "install_as": f"{n}.bin",
                            }
                            for n, b in zip(("one", "two"), bodies, strict=True)
                        ],
                        "licence": "CC0-1.0",
                        "licence_url": "https://example.invalid/licence",
                    }
                }
            ],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Stands in for a unit with two data files.",
                "why_you_want_it": "The preflight needs a unit with two artifacts.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )
    return manifest, bodies


def test_a_unit_with_one_of_two_artifacts_missing_gets_no_install_steps(tmp_path: Path) -> None:
    manifest, bodies = two_artifact_unit()
    block = manifest.install[0]
    unit = PlannedPackage(manifest, block, (), requested_by=("profile reference",))
    plan = InstallPlan(TARGET, (unit,))
    ctx = make_context(tmp_path, [artifact("fixture-pair", "one.bin", bodies[0])])
    got = preflight_data(plan, ctx, cached=lambda name, pin: False)
    assert got.packages == ()
    assert len(got.deferrals) == 1
    assert (
        "fixture-pair/two.bin: not on Bunker bunker, publisher unreachable" in got.deferrals[0].why
    )
    full = make_context(
        tmp_path,
        [
            artifact("fixture-pair", "one.bin", bodies[0]),
            artifact("fixture-pair", "two.bin", bodies[1]),
        ],
    )
    assert preflight_data(plan, full, cached=lambda name, pin: False).packages == (unit,)
    assert ("fixture-pair", "one.bin") in full.notes


def test_a_cached_artifact_needs_no_catalogue_entry(tmp_path: Path) -> None:
    manifest, _ = two_artifact_unit()
    unit = PlannedPackage(manifest, manifest.install[0], (), requested_by=("profile reference",))
    plan = InstallPlan(TARGET, (unit,))
    ctx = make_context(tmp_path, [])
    got = preflight_data(plan, ctx, cached=lambda name, pin: True)
    assert got.packages == (unit,) and not ctx.notes


def test_a_typed_data_unit_with_a_gap_refuses_by_name(tmp_path: Path) -> None:
    manifest, _ = two_artifact_unit()
    unit = PlannedPackage(manifest, manifest.install[0], ())
    with pytest.raises(PlanError, match=r"fixture-pair/one\.bin: not on Bunker bunker"):
        preflight_data(
            InstallPlan(TARGET, (unit,)), make_context(tmp_path, []), cached=lambda n, p: False
        )


def test_a_unit_that_depends_on_a_dropped_unit_is_dropped_with_it(tmp_path: Path) -> None:
    manifest, _ = two_artifact_unit()
    apt = load_catalog(FIXTURE_CATALOG / "packages")["fixture-apt"]
    dependant = apt.model_copy(update={"name": "fixture-user", "depends": ["fixture-pair"]})
    pair = PlannedPackage(manifest, manifest.install[0], (), requested_by=("profile p",))
    user = PlannedPackage(dependant, dependant.install[0], (), requested_by=("profile p",))
    got = preflight_data(
        InstallPlan(TARGET, (pair, user)), make_context(tmp_path, []), cached=lambda n, p: False
    )
    assert got.packages == ()
    assert {d.subject for d in got.deferrals} == {"fixture-pair", "fixture-user"}
    assert any("depends on fixture-pair" in d.why for d in got.deferrals)


def test_online_preflight_changes_nothing(tmp_path: Path) -> None:
    manifest, _ = two_artifact_unit()
    unit = PlannedPackage(manifest, manifest.install[0], ())
    plan = InstallPlan(TARGET, (unit,))
    assert (
        preflight_data(plan, make_context(tmp_path, [], offline=False), cached=lambda n, p: False)
        is plan
    )


def test_missing_static_profile_data_defers_whole_unit(tmp_path: Path) -> None:
    catalog = load_catalog(REPO / "catalog/packages")
    manifest = catalog["country-files"]
    block = next(block for block in manifest.install if isinstance(block.install, DataInstall))
    planned = PlannedPackage(manifest, block, (), requested_by=("reference",))
    plan = InstallPlan(Target(distro="debian", version="13", arch="x86_64"), (planned,))
    context = make_context(tmp_path, [])
    got = preflight_data(plan, context, cached=lambda unit, pin: False)
    assert not got.packages
    assert "not on Bunker bunker, publisher unreachable" in got.deferrals[0].why


def test_register_preflight_requires_explicit_unverified_label(tmp_path: Path) -> None:
    catalog = load_catalog(REPO / "catalog/packages")
    manifest = catalog["acma-register"]
    block = next(block for block in manifest.install if isinstance(block.install, RegisterInstall))
    unit = PlannedPackage(manifest, block, (), requested_by=("reference",))
    plan = InstallPlan(Target(distro="debian", version="13", arch="x86_64"), (unit,))
    row = artifact("acma-register", "spectra_rrl.zip", b"zip", publisher_check="unverified-zip")
    context = make_context(tmp_path, [row])
    result = preflight_data(plan, context, cached=lambda unit, pin: False)
    assert result.packages == (unit,) and not result.deferrals
    assert (
        context.unverified("acma-register", "spectra_rrl.zip").publisher_check == "unverified-zip"
    )
    wrong = make_context(tmp_path, [artifact("acma-register", "spectra_rrl.zip", b"zip")])
    refused = preflight_data(plan, wrong, cached=lambda unit, pin: False)
    assert not refused.packages and "explicitly unverified" in refused.deferrals[0].why


def test_cached_data_pin_needs_the_exact_bytes(tmp_path: Path) -> None:
    from hammunition.fetch import Fetcher
    from hammunition.manifest.schema import RemoteArtifact

    manifest, bodies = two_artifact_unit()
    pin = manifest.install[0].install.artifacts[0]  # type: ignore[union-attr]
    fetcher = Fetcher(tmp_path / "cache", offline=True)
    assert not cached_data_pin(fetcher, pin)
    path = fetcher.path_for(RemoteArtifact(url=pin.url, sha256=pin.sha256))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * len(bodies[0]))
    assert not cached_data_pin(fetcher, pin), "right size, wrong bytes"
    path.write_bytes(bodies[0])
    assert cached_data_pin(fetcher, pin)
    path.unlink()
    path.symlink_to(tmp_path)
    assert not cached_data_pin(fetcher, pin), "a symlink is never a hit"


@pytest.mark.parametrize("reverse", [False, True])
def test_resolution_imports_have_no_cycle(reverse: bool) -> None:
    modules = ["geofabrik", "copernicus", "ustopo", "usgs3dep", "fstopo", "resolution", "plan"]
    if reverse:
        modules.reverse()
    script = "; ".join("import hammunition." + module for module in modules)
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


def test_publisher_unavailable_is_one_class_in_both_modules() -> None:
    from hammunition import catalogue, retry

    assert retry.PublisherUnavailable is catalogue.PublisherUnavailable


def test_offline_publisher_checks_are_never_asked_even_under_recheck(tmp_path: Path) -> None:
    from hammunition.attributed import PublisherChecks

    item = tmp_path / "file"
    item.write_bytes(b"x")
    checks = PublisherChecks({}, recheck=True, offline=True)
    assert checks.due("u", "n", item) is False
    assert checks.lines[-1].checked is False
    assert "offline" in checks.lines[-1].reason
    assert PublisherChecks({}, recheck=True).due("u", "n", item) is False  # unattributed: unchanged


# -- the fetcher --------------------------------------------------------------


def test_offline_sources_are_the_bunker_alone_and_disclosed_as_such() -> None:
    from hammunition.backends import BackendError
    from hammunition.fetch import Fetcher, MirrorPath, fetch_disclosure

    path = MirrorPath("osm-regions", "europe/monaco")
    fetcher = Fetcher(Path("/nonexistent-cache"), mirror="http://bunker.lan:8080/", offline=True)
    assert fetcher.sources_for("https://publisher.invalid/x", path) == (
        ("mirror", "http://bunker.lan:8080/osm-regions/europe/monaco"),
    )
    note, detail, urls = fetch_disclosure(fetcher, "https://publisher.invalid/x", path, "sha256")
    assert "Bunker only" in note
    assert detail == urls[0] == "http://bunker.lan:8080/osm-regions/europe/monaco"
    assert "publisher.invalid" not in note + detail
    with pytest.raises(BackendError, match="no Bunker route"):
        fetcher.sources_for("https://publisher.invalid/x", None)
    with pytest.raises(BackendError, match="hammunition mirror enrol URL"):
        Fetcher(Path("/nonexistent-cache"), offline=True).sources_for(
            "https://publisher.invalid/x", path
        )


def test_an_offline_mirror_that_fails_verification_never_asks_the_publisher(
    tmp_path: Path,
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from io import BytesIO
    from typing import IO

    from hammunition.backends import BackendError
    from hammunition.fetch import Fetcher, MirrorPath
    from hammunition.manifest.schema import RemoteArtifact

    asked: list[str] = []
    publisher_asked: list[str] = []

    class Wrong:
        @contextmanager
        def open(self, url: str) -> Iterator[IO[bytes]]:
            asked.append(url)
            yield BytesIO(b"tampered")

    class Publisher:
        @contextmanager
        def open(self, url: str) -> Iterator[IO[bytes]]:
            publisher_asked.append(url)
            yield BytesIO(b"")

    fetcher = Fetcher(
        tmp_path / "cache",
        transport=Publisher(),
        mirror="http://bunker.lan:8080/",
        mirror_transport=Wrong(),
        offline=True,
    )
    remote = RemoteArtifact(
        url="https://publisher.invalid/x", sha256=hashlib.sha256(b"real").hexdigest()
    )
    with pytest.raises(BackendError, match="offline Bunker download failed"):
        fetcher.fetch(remote, mirror=MirrorPath("u", "n"))
    assert asked == ["http://bunker.lan:8080/u/n"]
    assert publisher_asked == []
    assert not any(tmp_path.glob("cache/*"))


def test_offline_etag_and_size_only_downloads_with_no_bunker_path_refuse_the_publisher(
    tmp_path: Path,
) -> None:
    from hammunition.backends import BackendError
    from hammunition.fetch import Fetcher

    fetcher = Fetcher(tmp_path / "cache", mirror="http://bunker.lan/", offline=True)
    with pytest.raises(BackendError, match="no Bunker route"):
        fetcher.fetch_etag("https://publisher.invalid/x", "abc", expected_size=1)
    with pytest.raises(BackendError, match="no Bunker route"):
        fetcher.fetch_sized("https://publisher.invalid/x", expected_size=1)


# -- repeater snapshots (#394) --------------------------------------------------


def test_offline_repeater_snapshots_refuse_the_publisher_leg() -> None:
    from hammunition import repeater_sources
    from hammunition.repeaters import RepeaterFetchError

    snapshot = repeater_sources.snapshots()[0]

    def fetch(url: str, *, limit: int) -> tuple[bytes, str, datetime]:
        pytest.fail(f"offline asked {url}")

    for mirror in (None, "file:///srv/bunker"):
        with pytest.raises(RepeaterFetchError, match=r"not asked under --offline") as refused:
            repeater_sources.read_snapshot(
                snapshot,
                lambda body, url: None,  # type: ignore[arg-type,return-value]
                mirror=mirror,
                fetch=fetch,
                offline=True,
            )
        assert snapshot.name in str(refused.value)


def test_offline_repeater_snapshot_from_a_failing_http_mirror_does_not_fall_back() -> None:
    from hammunition import repeater_sources
    from hammunition.repeaters import RepeaterFetchError

    snapshot = repeater_sources.snapshots()[0]
    asked: list[str] = []

    def fetch(url: str, *, limit: int) -> tuple[bytes, str, datetime]:
        asked.append(url)
        raise RepeaterFetchError("mirror down")

    with pytest.raises(RepeaterFetchError, match="not asked under --offline"):
        repeater_sources.read_snapshot(
            snapshot,
            lambda body, url: None,  # type: ignore[arg-type,return-value]
            mirror="http://bunker.lan:8080/",
            fetch=fetch,
            offline=True,
        )
    assert len(asked) == 1 and "bunker.lan" in asked[0]


# -- the CLI, in a temporary tree ---------------------------------------------

BODY = b"offline country file"
DATA_UNIT = f"""\
name: fixture-data
version: "1.0"
summary: A fixture data unit
categories: [references]
install:
  - install:
      method: data
      artifacts:
        - url: https://example.invalid/fixture-data.bin
          sha256: {hashlib.sha256(BODY).hexdigest()}
          size: {len(BODY)}
          format: file
          install_as: fixture-data.bin
      licence: CC0-1.0
      licence_url: https://example.invalid/licence
update:
  probe:
    method: none
documentation:
  what_it_does: Stands in for a data unit.
  why_you_want_it: The offline tests need one.
  upstream_url: https://example.invalid/
"""
VENV_UNIT = """\
name: fixture-venv
version: "1.0"
summary: A fixture pip unit
categories: [references]
install:
  - install:
      method: venv
      requirements:
        - "foo==1.0 --hash=sha256:abababababababababababababababababababababababababababababababab"
update:
  probe:
    method: none
documentation:
  what_it_does: Stands in for a pip unit.
  why_you_want_it: The offline tests need one.
  upstream_url: https://example.invalid/
"""
PROFILE = """\
name: fixture-offline
summary: Data, pip and apt
packages: [fixture-data, fixture-apt]
documentation:
  what_it_installs: A data unit, as the offline tests need one.
  why_together: They stand in for a profile with a data member and an apt member.
  deliberately_excludes: Everything real, because it is a test fixture.
  manual_configuration: Nothing, because it is a test fixture.
"""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A catalogue with a data unit and a pip unit, a stubbed target and apt, and a
    temporary XDG tree; returns the catalogue root."""
    monkeypatch.setenv("HOME", str(tmp_path))
    catalog = tmp_path / "catalog"
    shutil.copytree(FIXTURE_CATALOG, catalog)
    (catalog / "packages" / "fixture-data.yaml").write_text(DATA_UNIT)
    (catalog / "packages" / "fixture-venv.yaml").write_text(VENV_UNIT)
    (catalog / "profiles" / "fixture-offline.yaml").write_text(PROFILE)
    monkeypatch.setattr(Target, "detect", classmethod(lambda cls: TARGET))
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("USER", "op")
    monkeypatch.setattr(cli, "operator", lambda args: "")
    monkeypatch.setattr(AptBackend, "lists_populated", lambda self: True)
    monkeypatch.setattr(cli, "_apt_lists_note", lambda apt: "apt lists: fixture")
    return catalog


def apt_state(monkeypatch: pytest.MonkeyPatch, *, installed: str | None) -> None:
    monkeypatch.setattr(
        AptBackend,
        "probe",
        lambda self, pkgs: {
            p: AptPackageState(name=p, installed=installed, candidate="1.0") for p in pkgs
        },
    )
    monkeypatch.setattr(
        AptBackend,
        "simulate",
        lambda self, pkgs, *, release=None, no_recommends=False: AptSimulation(
            ok=True, installs={p: frozenset({"stable"}) for p in pkgs}, release=release
        ),
    )


def enrol_file_bunker(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    serial: int = 42,
    inputs: list[dict[str, object]] | None = None,
    accepted_serial: int = 0,
    station: dict[str, Any] | None = None,
) -> tuple[Path, str]:
    """A file:// export holding a really signed catalogue and its artifacts, enrolled
    for this tmp tree and set as the station mirror. Returns (export dir, mirror URL)."""
    export = tmp_path / "export"
    (export / "catalogue.sig.d").mkdir(parents=True)
    signing = tmp_path / "signing"
    private = key(signing)
    public = private.with_suffix(".pub").read_text().strip()
    raw, sigs = signed(
        signing, private, document(public, serial=serial, artifacts=rows, inputs=inputs or [])
    )
    (export / "catalogue.json").write_bytes(raw)
    for name, sig in sigs.items():
        (export / name).write_bytes(sig)
    url = Station(mirror=export.as_uri()).mirror
    assert url is not None
    strength = classify(public)
    state = MirrorState(
        url,
        "bunker",
        "personal",
        None,
        (EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False),),
        accepted_serial,
    )
    save_mirror(state)
    save_station(Station(mirror=url, **(station or {})))
    return export, url


def put(export: Path, unit: str, name: str, body: bytes) -> None:
    target = export / unit / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)


def run(capsys: pytest.CaptureFixture[str], catalog: Path, *argv: str) -> tuple[int, str, str]:
    rc = cli.main(["--catalog", str(catalog), *argv])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_install_offline_without_an_enrolment_names_the_remedy_before_any_apt_probe(
    machine: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_apt(*args: object, **kwargs: object) -> None:
        raise AssertionError("an apt probe ran before the offline refusal")

    monkeypatch.setattr(AptBackend, "probe", no_apt)
    monkeypatch.setattr(AptBackend, "simulate", no_apt)
    monkeypatch.setattr(AptBackend, "lists_populated", no_apt)
    rc, _out, err = run(capsys, machine, "install", "--offline", "--dry-run", "fixture-data")
    assert rc == cli.EXIT_UNPLANNABLE
    assert "hammunition mirror enrol URL" in err


def test_install_offline_cannot_be_combined_with_no_mirror(
    machine: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _out, err = run(
        capsys, machine, "install", "--offline", "--no-mirror", "--dry-run", "fixture-data"
    )
    assert rc == cli.EXIT_UNPLANNABLE and "--no-mirror" in err


def test_update_offline_refuses_upstream_even_for_an_empty_request(
    machine: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apt_state(monkeypatch, installed="1.0")
    rc, _out, err = run(capsys, machine, "update", "--offline", "--upstream")
    assert rc == cli.EXIT_UNPLANNABLE and "--upstream" in err


def test_install_offline_plans_data_from_the_bunker_only(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    export, url = enrol_file_bunker(tmp_path, [artifact("fixture-data", "fixture-data.bin", BODY)])
    put(export, "fixture-data", "fixture-data.bin", BODY)
    rc, out, err = run(
        capsys, machine, "install", "--offline", "--dry-run", "--json", "fixture-data"
    )
    assert rc == 0, err
    doc = parse_one(out)
    fetch = [c for c in doc["install"]["commands"] if c["action"] == "fetch"]
    assert [c["sources"] for c in fetch] == [[f"{url.rstrip('/')}/fixture-data/fixture-data.bin"]]
    assert "Bunker only" in fetch[0]["description"]
    assert "resolved from Bunker bunker" in fetch[0]["description"]
    assert "example.invalid" not in "".join(c["display"] + "".join(c["sources"]) for c in fetch)
    notes = "\n".join(doc["install"]["region_notes"])
    assert "fixture-data/fixture-data.bin: offline; resolved from Bunker bunker" in notes
    assert "catalogue serial 42 verified" in notes
    assert "dry run: the local trust state was not written" in notes
    assert "advances it from serial 0 to 42" in notes
    assert not any(
        "apt-get" in c["display"] and "update" in c["display"] for c in doc["install"]["commands"]
    )


def test_install_offline_defers_a_profile_member_the_bunker_cannot_supply(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    rc, out, err = run(
        capsys, machine, "install", "--offline", "--dry-run", "--json", "fixture-offline"
    )
    assert rc == 0, err
    doc = parse_one(out)
    deferred = {d["subject"]: d for d in doc["install"]["deferrals"]}
    assert "not on Bunker bunker, publisher unreachable" in deferred["fixture-data"]["why"]
    assert [p["name"] for p in doc["install"]["packages"]] == ["fixture-apt"]


def test_install_offline_refuses_a_typed_unit_the_bunker_cannot_supply(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    rc, _out, err = run(capsys, machine, "install", "--offline", "--dry-run", "fixture-data")
    assert rc == cli.EXIT_UNPLANNABLE
    assert "not on Bunker bunker, publisher unreachable" in err
    assert "Nothing was changed" in err


def test_install_offline_refuses_apt_packages_the_machine_lacks_and_never_updates(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed=None)
    enrol_file_bunker(tmp_path, [])
    rc, _out, err = run(capsys, machine, "install", "--offline", "--dry-run", "fixture-apt")
    assert rc == cli.EXIT_UNPLANNABLE
    assert "fixture-apt: offline: needs apt package(s) this machine does not have" in err
    assert "never calls apt-get update" in err


@pytest.mark.parametrize(("flags", "refresh"), [(("--offline",), False), ((), True)])
def test_install_asks_for_the_apt_refresh_only_online(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[str, ...],
    refresh: bool,
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    seen: list[bool] = []
    real = cli.commands_for

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["refresh"])
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "commands_for", spy)
    rc, _out, err = run(capsys, machine, "install", *flags, "--dry-run", "fixture-apt")
    assert rc == 0, err
    assert seen == [refresh]


def test_install_offline_with_apt_already_present_plans_no_refresh(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    rc, out, err = run(
        capsys, machine, "install", "--offline", "--dry-run", "--json", "fixture-apt"
    )
    assert rc == 0, err
    steps = parse_one(out)["install"]["commands"]
    assert all("update" not in s["argv"] for s in steps)


def test_install_offline_refuses_a_pip_unit_by_name(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    rc, _out, err = run(capsys, machine, "install", "--offline", "--dry-run", "fixture-venv")
    assert rc == cli.EXIT_UNPLANNABLE
    assert "fixture-venv: offline: its install runs pip" in err
    assert "phase 2 of #381" in err


def test_online_install_is_unchanged_without_an_enrolment(
    machine: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed=None)
    rc, out, err = run(capsys, machine, "install", "--dry-run", "--json", "fixture-apt")
    assert rc == 0, err
    doc = parse_one(out)
    assert any(
        s["argv"][-2:] == ["apt-get", "update"] or "update" in s["argv"]
        for s in doc["install"]["commands"]
    )
    assert not any("Bunker" in n for n in doc["install"]["region_notes"])


def test_update_offline_reports_the_trust_write_and_never_asks_upstream(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])

    def no_upstream(*args: object, **kwargs: object) -> None:
        raise AssertionError("an offline update asked upstream")

    monkeypatch.setattr(cli, "_upstream_rows", no_upstream)
    rc, out, err = run(capsys, machine, "update", "--offline", "--json", "fixture-apt")
    assert rc == 0, err
    doc = parse_one(out)
    assert doc["upstream"] is None
    assert "no publisher was asked; the Bunker's catalogue was read" in doc["offline"]
    assert "advanced from serial 0 to 42" in doc["offline"]
    again = parse_one(run(capsys, machine, "update", "--offline", "--json", "fixture-apt")[1])
    assert "already holds serial 42; unchanged" in again["offline"]
    rc, text, _err = run(capsys, machine, "update", "--offline", "fixture-apt")
    assert rc == 0 and "offline: no publisher was asked; the Bunker's catalogue was read" in text


def test_update_offline_without_an_enrolment_refuses_before_apt(
    machine: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_apt(*args: object, **kwargs: object) -> None:
        raise AssertionError("an apt probe ran before the offline refusal")

    monkeypatch.setattr(AptBackend, "probe", no_apt)
    rc, _out, err = run(capsys, machine, "update", "--offline", "fixture-apt")
    assert rc == cli.EXIT_UNPLANNABLE and "hammunition mirror enrol URL" in err


def break_signature(export: Path) -> None:
    sig = export / "catalogue.sig.d" / "1.sig"
    sig.write_bytes(sig.read_bytes()[:-30] + b"A" * 30)


def break_document(export: Path) -> None:
    (export / "catalogue.json").write_text("{ not json")


def remove_document(export: Path) -> None:
    (export / "catalogue.json").unlink()


@pytest.mark.parametrize(
    ("damage", "accepted", "reason"),
    [
        (break_signature, 0, "did not verify"),
        (break_document, 0, "invalid"),
        (remove_document, 0, "no catalogue at"),
        (None, 99, "older than accepted serial 99"),
    ],
    ids=["bad-signature", "malformed", "unreachable", "rollback"],
)
def test_a_bunker_that_cannot_be_trusted_turns_the_fallback_off_online_and_refuses_offline(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    damage: Any,
    accepted: int,
    reason: str,
) -> None:
    from hammunition.signers import load_mirror

    apt_state(monkeypatch, installed="1.0")
    export, _url = enrol_file_bunker(tmp_path, [], accepted_serial=accepted)
    if damage is not None:
        damage(export)
    rc, out, err = run(capsys, machine, "install", "--dry-run", "--json", "fixture-apt")
    assert rc == 0, err
    notes = [n for n in parse_one(out)["install"]["region_notes"] if "fallback is off" in n]
    assert len(notes) == 1, "one warning, not one per failure"
    assert reason in notes[0] and "--no-mirror" in notes[0]
    assert "proceeds" in notes[0]
    rc, _out, err = run(capsys, machine, "install", "--offline", "--dry-run", "fixture-apt")
    assert rc == cli.EXIT_UNPLANNABLE and reason in err
    rc, _out, err = run(capsys, machine, "update", "--offline", "fixture-apt")
    assert rc == cli.EXIT_UNPLANNABLE and reason in err
    state = load_mirror()
    assert state is not None and state.accepted_serial == accepted


def test_the_blocker_dataclass_is_the_planner_s_own() -> None:
    assert Blocker("u", "r").render() == "u: r"


@pytest.mark.parametrize("selection", ["osm-regions", "fixture-offline"])
def test_geofabrik_missing_record_refuses_typed_or_defers_whole_profile_unit(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selection: str,
) -> None:
    from test_offline_geofabrik import CATALOG, REGION, DownProbe

    (machine / "packages" / "osm-regions.yaml").write_bytes(
        (CATALOG / "packages" / "osm-regions.yaml").read_bytes()
    )
    profile = machine / "profiles" / "fixture-offline.yaml"
    profile.write_text(PROFILE.replace("fixture-data", "osm-regions"))
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [], station={"map_regions": [REGION], "map_freshness": "latest"})
    monkeypatch.setattr(cli, "UrllibProbe", DownProbe)
    rc, out, err = run(capsys, machine, "install", "--offline", "--dry-run", "--json", selection)
    if selection == "osm-regions":
        assert rc == cli.EXIT_UNPLANNABLE
        assert "not on Bunker bunker" in out + err
    else:
        assert rc == 0, err
        doc = parse_one(out)["install"]
        assert [p["name"] for p in doc["packages"]] == ["fixture-apt"]
        assert any(
            d["subject"] == "osm-regions" and "not on Bunker bunker" in d["why"]
            for d in doc["deferrals"]
        )
        assert not any(c["action"] == "fetch" for c in doc["commands"])
