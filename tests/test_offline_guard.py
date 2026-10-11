# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811

"""What ``--offline`` refuses by name at plan time.  #381, Task 5 fix round 1.

The interim guard (``_OFFLINE_UNROUTED``), the payload guard for source and binary
units, the offline network blockers, the per-unit fields a dropped data unit must
take with it, and the ``--json`` shape of a refusal before resolution. Each case
runs in the tmp tree of ``test_offline_context``'s ``machine`` fixture, with the
target and apt stubbed and only ``--dry-run``.
"""

from __future__ import annotations

import hashlib
import importlib
import socket
from pathlib import Path
from typing import Any

import pytest

from bunker_fixtures import artifact, make_context
from hammunition.backends.apt import AptBackend, AptPackageState
from hammunition.fetch import Fetcher
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import (
    ConsentGate,
    DataInstall,
    GitInstall,
    NodeInstall,
    PackageManifest,
    RemoteArtifact,
    RiskCategory,
)
from hammunition.plan import (
    GroupMembership,
    InstallPlan,
    PlannedPackage,
    RepoAddition,
    _offline_repo_blockers,
    offline_network_blockers,
    offline_payload_blockers,
    parse_deb_depends,
    preflight_data,
)
from hammunition.resolution import ResolutionContext
from hammunition.signers import load_mirror
from hammunition.userservice import PlannedUserService
from json_support import FIXTURE_CATALOG, parse_one, validate
from test_offline_context import (  # noqa: F401  (machine is a fixture)
    TARGET,
    apt_state,
    break_signature,
    enrol_file_bunker,
    machine,
    run,
    two_artifact_unit,
)

cli = importlib.import_module("hammunition.cli.main")

REPO = Path(__file__).resolve().parents[1]
REAL_CATALOG = REPO / "catalog"


class Forbidden:
    """Stands in for a publisher probe: constructing or calling one is the failure."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pytest.fail("an offline run constructed a publisher probe")


def forbid_the_network(monkeypatch: pytest.MonkeyPatch, *, probes: bool = True) -> list[object]:
    """Fail on any socket attempt; with *probes*, on constructing a publisher probe too
    (only the units that reach a resolver construct them, and the guard stops those)."""
    if probes:
        for name in (
            "UrllibProbe",
            "S3Probe",
            "KiwixProbe",
            "CdnProbe",
            "GatewayProbe",
            "AcmaProbe",
        ):
            monkeypatch.setattr(cli, name, Forbidden)
        monkeypatch.setattr(cli, "ustopo_probe", Forbidden)
    attempts: list[object] = []

    def record(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise OSError("the network is forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", record)
    monkeypatch.setattr(socket, "getaddrinfo", record)
    monkeypatch.setattr(socket, "create_connection", record)
    return attempts


# -- the interim guard ----------------------------------------------------------


def test_a_git_unit_is_refused_unless_it_is_already_built(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    attempts = forbid_the_network(monkeypatch, probes=False)
    rc, _out, err = run(capsys, REAL_CATALOG, "install", "--offline", "--dry-run", "acarsdec")
    assert rc == cli.EXIT_UNPLANNABLE and attempts == []
    assert "acarsdec: offline: resolving its git sources asks the publisher" in err
    catalog = load_catalog(REAL_CATALOG / "packages")
    manifest = catalog["acarsdec"]
    block = next(b for b in manifest.install if isinstance(b.install, GitInstall))
    plan = InstallPlan(TARGET, (PlannedPackage(manifest, block, ()),))
    assert [b.subject for b in cli._offline_unrouted(plan, plan_time=False)] == ["acarsdec"]
    assert cli._offline_unrouted(plan, frozenset({"acarsdec"}), plan_time=False) == []
    assert cli._offline_unrouted(plan, plan_time=True) == [], "git is not a plan-time kind"


def test_terrain_topo_books_and_maps_are_routed() -> None:
    from hammunition.manifest.schema import (
        DemTilesInstall,
        KiwixBooksInstall,
        MwmRegionsInstall,
        TopoQuadsInstall,
    )

    routed = InstallPlan(
        TARGET,
        (
            planned_from("dem-copernicus", DemTilesInstall),
            planned_from("dem-3dep", DemTilesInstall),
            planned_from("usgs-ustopo", TopoQuadsInstall),
            planned_from("usfs-fstopo", TopoQuadsInstall),
            planned_from("kiwix-library", KiwixBooksInstall),
            planned_from("comaps-maps", MwmRegionsInstall),
        ),
    )
    assert cli._offline_unrouted(routed, plan_time=True) == []


def test_a_unit_of_no_guarded_kind_is_not_named() -> None:
    manifest, _ = two_artifact_unit()
    plan = InstallPlan(TARGET, (PlannedPackage(manifest, manifest.install[0], ()),))
    assert cli._offline_unrouted(plan, plan_time=True) == []
    assert cli._offline_unrouted(plan, plan_time=False) == []


# -- the network blockers --------------------------------------------------------


def planned_from(name: str, kind: type) -> PlannedPackage:
    manifest = load_catalog(REAL_CATALOG / "packages")[name]
    block = next(b for b in manifest.install if isinstance(b.install, kind))
    return PlannedPackage(manifest, block, ())


def test_a_git_build_with_pip_lines_and_a_node_build_are_named(machine: Path) -> None:
    comaps = planned_from("comaps", GitInstall)
    assert isinstance(comaps.block.install, GitInstall) and comaps.block.install.build_python
    node = planned_from("openhamclock", NodeInstall)
    plain_git = planned_from("acarsdec", GitInstall)
    assert not plain_git.block.install.build_python  # type: ignore[union-attr]
    plan = InstallPlan(TARGET, (comaps, node, plain_git))
    got = {b.subject: b.reason for b in offline_network_blockers(plan, frozenset())}
    assert set(got) == {"comaps", "openhamclock"}
    assert "pip (build_python)" in got["comaps"] and "npm" in got["openhamclock"]
    assert offline_network_blockers(plan, frozenset({"comaps"})).__len__() == 1


def test_a_third_party_apt_repository_is_named() -> None:
    manifest = load_catalog(REAL_CATALOG / "packages")["codium"]
    repo = manifest.apt_repos[0]
    addition = RepoAddition(
        "codium", repo, "/etc/apt/sources.list.d/x.sources", "/k.gpg", ("codium",)
    )
    got = _offline_repo_blockers([addition])
    assert [b.subject for b in got] == ["codium"]
    assert repo.name in got[0].reason and "apt-get update" in got[0].reason
    assert _offline_repo_blockers([]) == []


# -- source and binary payloads ---------------------------------------------------

SRC_BODY = b"source tarball bytes"
DEB_BODY = b"deb bytes"
SRC_UNIT = f"""\
name: fixture-src
version: "1.0"
summary: A fixture unit built from a pinned tarball
categories: [digital-modes]
install:
  - install:
      method: source
      source:
        url: https://example.invalid/fixture-src-1.0.tar.gz
        sha256: {hashlib.sha256(SRC_BODY).hexdigest()}
      build_system: autotools
update:
  probe:
    method: none
  strategy: rebuild
documentation:
  what_it_does: Stands in for a source build in the offline payload tests.
  why_you_want_it: The payload guard needs a unit with a pinned tarball.
  upstream_url: https://example.invalid/fixture-src
"""
DEB_UNIT = f"""\
name: fixture-deb
version: "1.0"
summary: A fixture vendor deb
categories: [digital-modes]
install:
  - install:
      method: binary
      artifact:
        url: https://example.invalid/fixture-deb_1.0_amd64.deb
        sha256: {hashlib.sha256(DEB_BODY).hexdigest()}
      format: deb
      deb_package: fixture-deb
update:
  probe:
    method: none
  strategy: reinstall
documentation:
  what_it_does: Stands in for a vendor deb in the offline payload tests.
  why_you_want_it: The payload guard needs a unit apt would resolve dependencies for.
  upstream_url: https://example.invalid/fixture-deb
"""


@pytest.fixture
def payload_catalog(machine: Path) -> Path:
    (machine / "packages" / "fixture-src.yaml").write_text(SRC_UNIT)
    (machine / "packages" / "fixture-deb.yaml").write_text(DEB_UNIT)
    return machine


def cache(url: str, body: bytes) -> Path:
    remote = RemoteArtifact(url=url, sha256=hashlib.sha256(body).hexdigest())
    path = Fetcher().path_for(remote)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def only_installed(
    monkeypatch: pytest.MonkeyPatch,
    *installed: str,
    installed_versions: dict[str, str] | None = None,
) -> None:
    """apt knows exactly *installed* as installed (and the unit's own deb as not),
    each at version 2.0 unless *installed_versions* says otherwise."""
    installed_versions = installed_versions or {}
    monkeypatch.setattr(
        AptBackend,
        "probe",
        lambda self, pkgs: {
            p: AptPackageState(
                name=p,
                installed=installed_versions.get(p, "2.0") if p in installed else None,
                candidate="2.0",
            )
            for p in pkgs
        },
    )


def test_an_uncached_source_unit_is_refused_at_plan_time(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    rc, out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-src")
    assert rc == cli.EXIT_UNPLANNABLE and out == ""
    # Task 13: a source payload has a Bunker route, so the refusal is the
    # catalogue's, naming the item by its <sha256>/<file> name.
    assert f"fixture-src/{hashlib.sha256(SRC_BODY).hexdigest()}/fixture-src-1.0.tar.gz" in err
    assert "not on Bunker bunker" in err


def test_a_cached_source_unit_proceeds_offline_from_its_verified_bytes(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    path = cache("https://example.invalid/fixture-src-1.0.tar.gz", SRC_BODY)
    rc, out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-src")
    assert rc == 0, err
    assert "fixture-src" in out
    path.write_bytes(b"x" * len(SRC_BODY))  # right name, wrong bytes: not a hit
    rc, _out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-src")
    assert rc == cli.EXIT_UNPLANNABLE and "not on Bunker bunker" in err


def test_the_source_guard_names_nothing_online(
    payload_catalog: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    rc, _out, err = run(capsys, payload_catalog, "install", "--dry-run", "fixture-src")
    assert rc == 0, err


DEPENDS = "libfoo (>= 1.2), libbar | libbaz"
SHA_DEB = hashlib.sha256(DEB_BODY).hexdigest()


@pytest.mark.parametrize(
    ("cached", "installed", "versions", "depends", "refused"),
    [
        (False, (), {}, "", [f"fixture-deb/{SHA_DEB}/fixture-deb_1.0_amd64.deb", "not on Bunker"]),
        (True, (), {}, DEPENDS, ["libfoo (>= 1.2), libbar | libbaz", "fixture-deb: offline:"]),
        (True, ("libfoo",), {}, DEPENDS, ["libbar | libbaz"]),
        (True, ("libfoo", "libbaz"), {}, DEPENDS, None),
        (True, (), {}, "", None),
        # The version is checked, not only the name (Task 13).
        (True, ("libfoo", "libbaz"), {"libfoo": "1.0"}, DEPENDS, ["libfoo (>= 1.2)"]),
        (True, ("libfoo", "libbaz"), {"libfoo": "1:0.5"}, DEPENDS, None),
        (True, ("libfoo", "libbaz"), {"libfoo": "1.2"}, DEPENDS, None),
        (
            True,
            ("libbaz",),
            {"libbaz": "0.9"},
            "libbar (>= 3) | libbaz (>= 1)",
            ["libbar (>= 3) | libbaz (>= 1)"],
        ),
        (True, ("libbaz",), {"libbaz": "2.0"}, "libbar (>= 3) | libbaz (>= 1)", None),
    ],
    ids=[
        "not-cached",
        "none-installed",
        "one-group-met",
        "all-met",
        "no-dependencies",
        "installed-too-old",
        "epoch-wins",
        "exactly-the-minimum",
        "alternatives-both-out-of-range",
        "alternative-in-range",
    ],
)
def test_a_vendor_deb_offline_needs_its_bytes_and_its_installed_dependencies(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cached: bool,
    installed: tuple[str, ...],
    versions: dict[str, str],
    depends: str,
    refused: list[str] | None,
) -> None:
    only_installed(monkeypatch, *installed, installed_versions=versions)
    enrol_file_bunker(tmp_path, [])
    if cached:
        cache("https://example.invalid/fixture-deb_1.0_amd64.deb", DEB_BODY)
    monkeypatch.setattr(cli, "_dpkg_depends", lambda path: depends)
    monkeypatch.setattr(cli, "_native_arch", lambda: "amd64")
    rc, _out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-deb")
    if refused is None:
        assert rc == 0, err
    else:
        assert rc == cli.EXIT_UNPLANNABLE
        assert all(text in err for text in refused), err
        if cached:
            assert "apt, which would fetch what it depends on" in err
        if "libfoo" in installed and versions.get("libfoo") != "1.0":
            assert "libfoo (>= 1.2)" not in err


SRC_PIN = RemoteArtifact(
    url="https://example.invalid/fixture-src-1.0.tar.gz",
    sha256=hashlib.sha256(SRC_BODY).hexdigest(),
)
DEB_PIN = RemoteArtifact(url="https://example.invalid/fixture-deb_1.0_amd64.deb", sha256=SHA_DEB)
PAYLOAD_PROFILE = """\
name: fixture-payloads
summary: A source payload and an apt unit
packages: [fixture-src, fixture-apt]
documentation:
  what_it_installs: A source unit and an apt unit, as the payload tests need.
  why_together: They stand in for a profile with a payload member.
  deliberately_excludes: Everything real, because it is a test fixture.
  manual_configuration: Nothing, because it is a test fixture.
"""


def test_a_source_unit_the_bunker_holds_proceeds_offline_without_a_cache(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hammunition.payloads import payload_name

    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [artifact("fixture-src", payload_name(SRC_PIN), SRC_BODY)])
    rc, out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-src")
    assert rc == 0, err
    assert "Bunker only, offline" in out
    assert f"fixture-src/{payload_name(SRC_PIN)}: offline; resolved from Bunker bunker" in out, out


def test_each_provenance_line_is_printed_once_across_the_plan_time_passes(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hammunition.payloads import payload_name
    from test_offline_context import BODY as DATA_BODY

    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(
        tmp_path,
        [
            artifact("fixture-data", "fixture-data.bin", DATA_BODY),
            artifact("fixture-src", payload_name(SRC_PIN), SRC_BODY),
        ],
    )
    rc, out, err = run(
        capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-data", "fixture-src"
    )
    assert rc == 0, err
    notes = [line for line in out.splitlines() if "resolved from Bunker" in line]
    assert sum("fixture-data/fixture-data.bin" in line for line in notes) == 1, notes
    assert sum(f"fixture-src/{payload_name(SRC_PIN)}" in line for line in notes) == 1, notes


def test_the_install_command_shares_its_context_with_every_payload_backend(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Offline, each payload backend carries the run's context, and the .deb one
    its dependency check; online they carry the same context, which asks nothing."""
    from hammunition.backends.binary import BinaryBackend
    from hammunition.backends.node import NodeBackend
    from hammunition.backends.venv import VenvBackend

    seen: dict[str, Any] = {}

    def recording(name: str, base: type) -> type:
        class Recording(base):  # type: ignore[misc]  # a test subclass of a dataclass/class by variable
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                seen[name] = self

        return Recording

    monkeypatch.setattr(cli, "BinaryBackend", recording("binary", BinaryBackend))
    monkeypatch.setattr(cli, "VenvBackend", recording("venv", VenvBackend))
    monkeypatch.setattr(cli, "NodeBackend", recording("node", NodeBackend))
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    cache("https://example.invalid/fixture-src-1.0.tar.gz", SRC_BODY)
    rc, _out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-src")
    assert rc == 0, err
    assert len({id(seen[name].context) for name in ("binary", "venv", "node")}) == 1
    context = seen["binary"].context
    assert isinstance(context, ResolutionContext) and context.offline
    assert callable(seen["binary"].dependency_check)
    assert seen["binary"].recommends_of is cli._deb_recommends


def test_a_profile_member_the_bunker_lacks_is_deferred_whole_not_refused(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (payload_catalog / "profiles" / "fixture-payloads.yaml").write_text(PAYLOAD_PROFILE)
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    rc, out, err = run(
        capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-payloads"
    )
    assert rc == 0, err
    assert "will not install this unit this run" in out + err and "fixture-src" in out + err
    assert "not on Bunker" in out + err
    assert "Unpack the fixture-src source" not in out


def test_an_uncached_deb_the_bunker_holds_gets_its_dependency_check_after_the_fetch(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hammunition.payloads import payload_name

    only_installed(monkeypatch)
    enrol_file_bunker(tmp_path, [artifact("fixture-deb", payload_name(DEB_PIN), DEB_BODY)])
    rc, out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-deb")
    assert rc == 0, err  # its bytes are not local to read: the install checks after the fetch
    assert "Check that fixture-deb's dependencies are installed" in out


@pytest.mark.parametrize("have", [True, False])
def test_an_offline_vendor_deb_needs_bwrap_for_its_maintainer_scripts(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    have: bool,
) -> None:
    from hammunition.payloads import payload_name

    only_installed(monkeypatch)
    enrol_file_bunker(tmp_path, [artifact("fixture-deb", payload_name(DEB_PIN), DEB_BODY)])
    monkeypatch.setattr(cli.netiso, "detect", lambda *a, **k: "bwrap" if have else None)
    rc, out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-deb")
    if have:
        assert rc == 0, err
        assert "bwrap" in out and "maintainer scripts" in out
    else:
        assert rc == cli.EXIT_UNPLANNABLE
        assert "fixture-deb: offline:" in err and "bwrap" in err and "maintainer scripts" in err


def test_an_online_install_never_asks_whether_bwrap_works(
    payload_catalog: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def never(*a: object, **k: object) -> bool:
        raise AssertionError("an online run asked about bwrap")

    only_installed(monkeypatch)
    monkeypatch.setattr(cli.netiso, "detect", never)
    rc, _out, err = run(capsys, payload_catalog, "install", "--dry-run", "fixture-deb")
    assert rc == 0, err


def test_a_shared_catalogue_row_is_signed_for_its_owner_only() -> None:
    import tempfile

    row = artifact("u", "n", b"bytes", share="owner:abc")
    base = Path(tempfile.mkdtemp(prefix="signed-"))
    other = make_context(base / "a", [row], mode="group", enrolment_id="zzz")
    mine = make_context(base / "b", [row], mode="group", enrolment_id="abc")
    assert other.signed_sha256("u", "n") is None
    assert mine.signed_sha256("u", "n") == hashlib.sha256(b"bytes").hexdigest()
    assert mine.signed_sha256("u", "missing") is None
    assert ResolutionContext().signed_sha256("u", "n") is None


def test_an_installed_deb_unit_needs_no_dependency_check(machine: Path) -> None:
    manifest = PackageManifest.model_validate(__import__("yaml").safe_load(DEB_UNIT))
    unit = PlannedPackage(manifest, manifest.install[0], (), deb_installed=True)
    plan = InstallPlan(TARGET, (unit,))

    def never(_: PlannedPackage) -> list[str]:
        pytest.fail("an installed deb was checked for dependencies")

    assert offline_payload_blockers(plan, frozenset(), cached=lambda a: True, deb_unmet=never) == []


def test_the_deb_depends_parser_keeps_names_and_alternatives_and_refuses_junk() -> None:
    assert parse_deb_depends("libc6 (>= 2.34), libfoo:amd64, aa | bb (<< 2)") == [
        ["libc6"],
        ["libfoo"],
        ["aa", "bb"],
    ]
    assert parse_deb_depends("") == []
    # The old reading dropped everything after the first "[" or "(" and accepted
    # an empty trailing group; both are malformed and refuse.
    for junk in ("aa [amd64] | bb (<< 2)\n , ", "libc6 (>= 2.34) trailing", "libfoo,"):
        with pytest.raises(ValueError):
            parse_deb_depends(junk)


# -- a dropped data unit takes everything with it ---------------------------------


def test_a_dropped_unit_and_its_dependent_leave_no_configuration_behind(tmp_path: Path) -> None:
    from hammunition.plan import FileCapability

    catalog = load_catalog(REAL_CATALOG / "packages")
    config = catalog["linbpq"].config_files[0]
    pair, _ = two_artifact_unit()
    fixture_apt = load_catalog(FIXTURE_CATALOG / "packages")["fixture-apt"]
    dependant = fixture_apt.model_copy(update={"name": "fixture-user", "depends": ["fixture-pair"]})
    keeper = fixture_apt.model_copy(update={"name": "fixture-keeper"})
    members = [
        PlannedPackage(pair, pair.install[0], (), requested_by=("profile p",)),
        PlannedPackage(dependant, dependant.install[0], (), requested_by=("profile p",)),
        PlannedPackage(keeper, keeper.install[0], (), requested_by=("profile p",)),
    ]
    gate = ConsentGate(
        risk_categories=[RiskCategory.privileged_execution],
        env_var="HAMMUNITION_ACCEPT_TEST",
        disclosure="Grants a capability to a test binary, for the test only.",
        affirmation="Do you authorize this test grant?",
    )
    repo = catalog["codium"].apt_repos[0]
    service = PlannedUserService(
        name="svc",
        description="d",
        exec_argv=("true",),
        unit_body="",
        device_path=None,
        filled_from=(),
        listens=(),
        unit="fixture-user",
    )
    kept_service = PlannedUserService(
        name="kept",
        description="d",
        exec_argv=("true",),
        unit_body="",
        device_path=None,
        filled_from=(),
        listens=(),
        unit="fixture-keeper",
    )

    def membership(package: str) -> GroupMembership:
        return GroupMembership("dialout", "op", package, "d", "d", None)

    def capability(package: str) -> FileCapability:
        return FileCapability(Path(f"/usr/local/bin/{package}"), ("CAP_NET_RAW",), package, "d")

    plan = InstallPlan(
        TARGET,
        tuple(members),
        group_memberships=(membership("fixture-user"), membership("fixture-keeper")),
        file_capabilities=(capability("fixture-user"), capability("fixture-keeper")),
        consent_gates=(
            ("profile p", gate),
            ("file-capabilities:fixture-user", gate),
            ("file-capabilities:fixture-keeper", gate),
        ),
        config_files=(("fixture-user", config, "body"), ("fixture-keeper", config, "body")),
        user_services=(service, kept_service),
        apt_repos=(
            RepoAddition("fixture-user", repo, "s", "k", ("p",)),
            RepoAddition("fixture-keeper", repo, "s", "k", ("p",)),
        ),
        notes=("fixture-user: opens a port", "fixture-pair is for plasma", "fixture-keeper: x"),
    )
    got = preflight_data(plan, make_context(tmp_path, []), cached=lambda n, p: False)
    assert [p.name for p in got.packages] == ["fixture-keeper"]
    assert {d.subject for d in got.deferrals} == {"fixture-pair", "fixture-user"}
    assert [m.package for m in got.group_memberships] == ["fixture-keeper"]
    assert [c.package for c in got.file_capabilities] == ["fixture-keeper"]
    assert [name for name, _ in got.consent_gates] == [
        "profile p",
        "file-capabilities:fixture-keeper",
    ]
    assert [c[0] for c in got.config_files] == ["fixture-keeper"]
    assert [s.unit for s in got.user_services] == ["fixture-keeper"]
    assert [r.unit for r in got.apt_repos] == ["fixture-keeper"]
    assert got.notes == ("fixture-keeper: x",)


def test_nothing_dropped_means_the_plan_is_returned_whole(tmp_path: Path) -> None:
    manifest, bodies = two_artifact_unit()
    plan = InstallPlan(TARGET, (PlannedPackage(manifest, manifest.install[0], ()),))
    rows = [
        artifact("fixture-pair", "one.bin", bodies[0]),
        artifact("fixture-pair", "two.bin", bodies[1]),
    ]
    assert preflight_data(plan, make_context(tmp_path, rows), cached=lambda n, p: False) is plan
    assert isinstance(manifest.install[0].install, DataInstall)


# -- the trust state ---------------------------------------------------------------


def test_a_dry_run_never_writes_trust_state_online_or_offline(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(tmp_path, [])
    for flags in ((), ("--offline",)):
        rc, out, err = run(capsys, machine, "install", *flags, "--dry-run", "--json", "fixture-apt")
        assert rc == 0, err
        notes = "\n".join(parse_one(out)["install"]["region_notes"])
        assert "dry run: the local trust state was not written" in notes
        state = load_mirror()
        assert state is not None and state.accepted_serial == 0 and state.generated is None


def test_the_trust_state_is_written_when_a_run_is_real(
    machine: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enrol_file_bunker(tmp_path, [])
    ctx, notes = cli._resolution_context(offline=False, no_mirror=False, owner=None)
    assert ctx.verified is not None and "advanced from serial 0 to 42" in notes[0]
    state = load_mirror()
    assert state is not None and state.accepted_serial == 42
    _ctx, again = cli._resolution_context(offline=False, no_mirror=False, owner=None)
    assert "already holds serial 42; unchanged" in again[0]


@pytest.mark.parametrize("offline", [False, True])
def test_a_failed_trust_write_disables_the_fallback_online_and_refuses_offline(
    machine: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: bool
) -> None:
    from hammunition import mirror_transport
    from hammunition.signers import SignerError

    enrol_file_bunker(tmp_path, [])

    def broken(*args: object, **kwargs: object) -> None:
        raise SignerError("cannot write the mirror state: disk full")

    monkeypatch.setattr(mirror_transport, "advance_mirror", broken)
    if offline:
        with pytest.raises(SignerError, match="disk full"):
            cli._resolution_context(offline=True, no_mirror=False, owner=None)
        return
    ctx, notes = cli._resolution_context(offline=False, no_mirror=False, owner=None)
    assert ctx.verified is None and not ctx.offline
    assert len(notes) == 1 and "fallback is off for this run" in notes[0]
    assert "disk full" in notes[0] and "--no-mirror" in notes[0]


# -- refusals before resolution under --json --------------------------------------


def refused_document(
    capsys: pytest.CaptureFixture[str], machine: Path, *argv: str
) -> dict[str, Any]:
    rc, out, _err = run(capsys, machine, "install", *argv, "--dry-run", "--json", "fixture-apt")
    doc = parse_one(out)
    assert rc == cli.EXIT_UNPLANNABLE
    assert doc["kind"] == "plan" and doc["outcome"] == "refused" and doc["install"] is None
    validate(doc)
    return doc


def test_no_enrolment_offline_is_a_refused_plan_document(
    machine: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = refused_document(capsys, machine, "--offline")
    assert "hammunition mirror enrol URL" in doc["blockers"][0]["reason"]


def test_offline_with_no_mirror_is_a_refused_plan_document(
    machine: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = refused_document(capsys, machine, "--offline", "--no-mirror")
    assert doc["blockers"][0]["subject"] == "--offline"
    assert "--no-mirror" in doc["blockers"][0]["reason"]


def test_a_bad_signature_offline_is_a_refused_plan_document(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export, _url = enrol_file_bunker(tmp_path, [])
    break_signature(export)
    doc = refused_document(capsys, machine, "--offline")
    assert "did not verify" in doc["blockers"][0]["reason"]


def test_a_changed_station_mirror_is_a_refused_plan_document_too(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hammunition.station import Station, save_station

    enrol_file_bunker(tmp_path, [])
    save_station(Station(mirror="http://changed.invalid/"))
    doc = refused_document(capsys, machine)
    assert "differs from enrolled mirror" in doc["blockers"][0]["reason"]


def test_map_regions_now_resolve_offline_from_verified_catalogue(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from test_offline_geofabrik import REGION, DownProbe, row

    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(
        tmp_path, [row()], station={"map_regions": [REGION], "map_freshness": "latest"}
    )
    attempts = forbid_the_network(monkeypatch, probes=False)
    monkeypatch.setattr(cli, "UrllibProbe", DownProbe)
    rc, out, err = run(
        capsys, REAL_CATALOG, "install", "--offline", "--dry-run", "--json", "osm-regions"
    )
    assert rc == 0, err
    assert any(
        "resolved from Bunker bunker" in n for n in parse_one(out)["install"]["region_notes"]
    )
    assert attempts == []


def test_copernicus_terrain_now_resolves_offline_from_verified_catalogue(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from test_offline_copernicus import cli_setup, enrol, real_tile, row

    tile = real_tile()
    enrol(tmp_path, tile, [row(tile)])
    cli_setup(machine, monkeypatch)
    attempts = forbid_the_network(monkeypatch, probes=False)
    rc, out, err = run(
        capsys, REAL_CATALOG, "install", "--offline", "--dry-run", "--json", "dem-copernicus"
    )
    assert rc == 0, err
    assert any(
        "offline; resolved from Bunker bunker" in n
        for n in parse_one(out)["install"]["region_notes"]
    )
    assert attempts == []


@pytest.mark.parametrize("unit", ["usgs-ustopo", "dem-3dep"])
def test_usgs_sheets_and_3dep_now_resolve_offline_from_verified_catalogue(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
) -> None:
    from test_offline_usgs import cli_setup, enrol, real_rows

    enrol(tmp_path, real_rows())
    cli_setup(machine, monkeypatch)
    attempts = forbid_the_network(monkeypatch, probes=False)
    rc, out, err = run(capsys, REAL_CATALOG, "install", "--offline", "--dry-run", "--json", unit)
    assert rc == 0, err
    assert any(
        "offline; resolved from Bunker bunker" in n
        for n in parse_one(out)["install"]["region_notes"]
    )
    assert attempts == []
