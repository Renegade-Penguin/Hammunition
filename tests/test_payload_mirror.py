# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Source, binary, venv and Node payloads try the Bunker mirror first.  #381, Task 13.

The four backends share one fetch step (``payload_action``) and one offline
preflight (``preflight_payloads``). Every case drives each backend's own
``steps()`` and performs only its fetch Action, so no build tool runs.
"""

from __future__ import annotations

import hashlib
import importlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from bunker_fixtures import artifact as catalogue_artifact
from bunker_fixtures import document, key, make_context, signed
from hammunition.backends.base import Action, BackendError, Command, CommandResult
from hammunition.backends.binary import BinaryBackend
from hammunition.backends.node import NodeBackend
from hammunition.backends.source import SourceBackend
from hammunition.backends.venv import VenvBackend
from hammunition.distro import Target
from hammunition.fetch import Fetcher, MirrorPath, mirror_url
from hammunition.keystrength import classify
from hammunition.manifest.schema import (
    NodeInstall,
    PackageManifest,
    RemoteArtifact,
    VenvInstall,
)
from hammunition.payloads import payload_action, payload_name, payload_path
from hammunition.plan import InstallPlan, PlannedPackage, payload_misses
from hammunition.resolution import CatalogueMiss, ResolutionContext
from hammunition.signers import EnrolledKey, MirrorState, save_mirror
from hammunition.station import Station, save_station
from test_fetch_mirror import Routes

BUNKER = "http://bunker.invalid"
BODY = b"pinned tarball"
TARGET = Target(distro="debian", version="13", arch="x86_64")


def test_payload_action_uses_mirror_and_records_facts(tmp_path: Path) -> None:
    body = b"pinned tarball"
    artifact = RemoteArtifact(
        url="https://example.invalid/release.tar.gz", sha256=hashlib.sha256(body).hexdigest()
    )
    where = payload_path("example-tool", artifact)
    url = mirror_url("http://bunker.invalid", where)
    routes = Routes({url: body})
    fetcher = Fetcher(
        tmp_path,
        transport=routes,
        mirror_transport=routes,
        mirror="http://bunker.invalid",
        offline=True,
    )
    step = payload_action("example-tool", artifact, fetcher, label="source archive")
    assert step.sources == (url,)
    step.perform()
    assert step.facts["source"] == "mirror" and step.facts["fetched_from"] == url
    assert routes.requested == [url]


@pytest.mark.parametrize("missing", [False, True])
def test_payload_preflight_checks_every_pin_before_build(tmp_path: Path, missing: bool) -> None:
    from hammunition.payloads import preflight_payloads

    pins = [
        RemoteArtifact(
            url=f"https://example.invalid/{n}.tar", sha256=hashlib.sha256(bytes([n])).hexdigest()
        )
        for n in (1, 2)
    ]
    rows = [
        catalogue_artifact("thing", payload_name(pin), bytes([n]))
        for n, pin in zip((1, 2), pins, strict=True)
    ]
    context = make_context(tmp_path, rows[:1] if missing else rows)
    if missing:
        with pytest.raises(CatalogueMiss, match="not on Bunker"):
            preflight_payloads("thing", tuple((pin, 1) for pin in pins), context=context)
        assert context.notes == {}  # nothing is noted for a unit that will not install
    else:
        preflight_payloads("thing", tuple((pin, 1) for pin in pins), context=context)
        assert len(context.notes) == 2


# -- the names ----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/",
        "https://example.invalid/..",
        "https://example.invalid/%2e%2e",
        "https://example.invalid/a/../../b",
        "https://example.invalid/path%20with%20spaces.tar.gz?token=1#frag",
        "https://example.invalid/a b;c.tar",
    ],
)
def test_a_hostile_url_still_gives_one_safe_path_segment(url: str) -> None:
    pin = RemoteArtifact(url=url, sha256="ab" * 32)
    head, _, tail = payload_name(pin).partition("/")
    assert head == "ab" * 32 and tail and "/" not in tail and not tail.startswith(".")
    assert payload_path("unit", pin).segments == ("unit", head, tail)


def test_same_basename_different_pins_do_not_collide() -> None:
    a = RemoteArtifact(url="https://a.invalid/x86_64/tool.tar.gz", sha256="aa" * 32)
    b = RemoteArtifact(url="https://a.invalid/aarch64/tool.tar.gz", sha256="bb" * 32)
    assert payload_name(a) != payload_name(b)
    assert payload_name(a).endswith("/tool.tar.gz") and payload_name(b).endswith("/tool.tar.gz")


def test_same_bytes_under_two_basenames_are_two_names() -> None:
    a = RemoteArtifact(url="https://a.invalid/one.tar.gz", sha256="aa" * 32)
    b = RemoteArtifact(url="https://a.invalid/two.tar.gz", sha256="aa" * 32)
    assert payload_name(a) != payload_name(b)


# -- the step itself ----------------------------------------------------------


def _pin(body: bytes = BODY, url: str = "https://example.invalid/release.tar.gz") -> RemoteArtifact:
    return RemoteArtifact(url=url, sha256=hashlib.sha256(body).hexdigest())


def _fetcher(tmp_path: Path, routes: Routes, *, offline: bool) -> Fetcher:
    return Fetcher(
        tmp_path / "cache",
        transport=routes,
        mirror_transport=routes,
        mirror=BUNKER,
        offline=offline,
    )


def test_expected_size_must_match_and_a_wrong_one_is_named(tmp_path: Path) -> None:
    pin = _pin()
    at = mirror_url(BUNKER, payload_path("u", pin))
    routes = Routes({at: BODY})
    ok = payload_action(
        "u", pin, _fetcher(tmp_path, routes, offline=True), label="x", expected_size=len(BODY)
    )
    assert "sha256 verified" in ok.perform()
    bad = payload_action(
        "u", pin, _fetcher(tmp_path / "b", routes, offline=True), label="x", expected_size=3
    )
    with pytest.raises(BackendError, match=rf"manifest size 3, got {len(BODY)}"):
        bad.perform()


def test_the_fetched_path_is_handed_back(tmp_path: Path) -> None:
    pin = _pin()
    routes = Routes({mirror_url(BUNKER, payload_path("u", pin)): BODY})
    fetcher = _fetcher(tmp_path, routes, offline=True)
    fetched: dict[str, Path] = {}
    payload_action("u", pin, fetcher, label="x", fetched=fetched).perform()
    assert fetched["path"] == fetcher.path_for(pin) and fetched["path"].read_bytes() == BODY


def test_a_byte_cap_stops_an_oversized_payload(tmp_path: Path) -> None:
    pin = _pin()
    routes = Routes({mirror_url(BUNKER, payload_path("u", pin)): BODY})
    step = payload_action(
        "u", pin, _fetcher(tmp_path, routes, offline=True), label="x", max_bytes=4
    )
    with pytest.raises(BackendError):
        step.perform()
    assert not list((tmp_path / "cache").glob("*"))


def test_online_without_a_mirror_names_the_publisher_only(tmp_path: Path) -> None:
    pin = _pin()
    routes = Routes({pin.url: BODY})
    fetcher = Fetcher(tmp_path / "cache", transport=routes)
    step = payload_action("u", pin, fetcher, label="x")
    assert step.sources == (pin.url,)
    step.perform()
    assert step.facts["source"] == "publisher"


def test_offline_with_no_bunker_route_is_refused_when_the_step_is_built(tmp_path: Path) -> None:
    pin = _pin()
    fetcher = Fetcher(tmp_path / "cache", transport=Routes({}), offline=True)
    with pytest.raises(BackendError, match="no Bunker route"):
        payload_action("u", pin, fetcher, label="x")


# -- the four backends --------------------------------------------------------

HASHED = "example==1.0 --hash=sha256:" + "0" * 64


def _manifest(name: str, install: dict[str, Any], **extra: Any) -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": name,
            "version": "1.0",
            "summary": "Fixture for the payload mirror suite",
            "categories": ["packet"],
            "install": [{"install": install}],
            "update": {"probe": {"method": "none"}, "strategy": "reinstall"},
            "documentation": {
                "what_it_does": "Exists so the payload routes have a unit to plan.",
                "why_you_want_it": "You do not; the suite does.",
                "upstream_url": "https://example.invalid/",
            },
            **extra,
        }
    )


class NeverRuns:
    """A runner that fails the test if a payload step runs a command."""

    def run(self, command: Command) -> CommandResult:
        raise AssertionError(f"a payload step ran {command.argv}")


@dataclass
class Kind:
    unit: str
    steps: Callable[
        [Path, Fetcher, ResolutionContext | None, RemoteArtifact], list[Action | Command]
    ]


def _venv_manifest(pin: RemoteArtifact) -> tuple[PackageManifest, VenvInstall]:
    manifest = _manifest(
        "venvunit",
        {
            "method": "venv",
            "requirements": [HASHED],
            "payload": pin.model_dump(exclude_none=True),
            "payload_build_script": "build.sh",
            "tree_marker": "run.py",
        },
        launchers=[
            {
                "name": "venvunit",
                "exec": "exec {venv}/bin/python run.py",
                "working_directory": "/usr/local/share/hammunition/venvunit",
            }
        ],
    )
    block = manifest.install[0].install
    assert isinstance(block, VenvInstall)
    return manifest, block


def _node_manifest(pin: RemoteArtifact) -> tuple[PackageManifest, NodeInstall]:
    manifest = _manifest(
        "nodeunit",
        {
            "method": "node",
            "artifact": pin.model_dump(exclude_none=True),
            "node_min_version": "20.19",
            "entry": "server.js",
            "command": "nodeunit-server",
        },
    )
    block = manifest.install[0].install
    assert isinstance(block, NodeInstall)
    return manifest, block


def _source(
    tmp_path: Path, fetcher: Fetcher, context: ResolutionContext | None, pin: RemoteArtifact
) -> list[Action | Command]:
    manifest = _manifest(
        "srcunit",
        {"method": "source", "source": pin.model_dump(exclude_none=True), "build_system": "make"},
    )
    backend = SourceBackend(
        fetcher,
        build_root=tmp_path / "build",
        prefix=tmp_path / "prefix",
        context=context,
        isolation="bwrap",
    )
    return backend.steps(manifest, manifest.install[0])


def _binary(
    tmp_path: Path, fetcher: Fetcher, context: ResolutionContext | None, pin: RemoteArtifact
) -> list[Action | Command]:
    manifest = _manifest(
        "binunit",
        {"method": "binary", "artifact": pin.model_dump(exclude_none=True), "format": "tarball"},
        binaries=[{"produced": "bin/x", "install_as": "x"}],
    )
    backend = BinaryBackend(
        fetcher=fetcher,
        runner=NeverRuns(),
        build_root=tmp_path / "build",
        prefix=tmp_path / "prefix",
        context=context,
    )
    return backend.steps(manifest, manifest.install[0])


def _venv(
    tmp_path: Path, fetcher: Fetcher, context: ResolutionContext | None, pin: RemoteArtifact
) -> list[Action | Command]:
    manifest, block = _venv_manifest(pin)
    backend = VenvBackend(
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        fetcher=fetcher,
        build_root=tmp_path / "build",
        prefix=tmp_path / "prefix",
        context=context,
    )
    return backend.steps(manifest, block)


def _node(
    tmp_path: Path, fetcher: Fetcher, context: ResolutionContext | None, pin: RemoteArtifact
) -> list[Action | Command]:
    manifest, block = _node_manifest(pin)
    backend = NodeBackend(
        fetcher=fetcher,
        build_root=tmp_path / "build",
        node_root=tmp_path / "node",
        bin_dir=tmp_path / "bin",
        context=context,
    )
    return backend.steps(manifest, block)


KINDS = [
    pytest.param(Kind("srcunit", _source), id="source"),
    pytest.param(Kind("binunit", _binary), id="binary"),
    pytest.param(Kind("venvunit", _venv), id="venv"),
    pytest.param(Kind("nodeunit", _node), id="node"),
]


def _fetch_step(steps: list[Action | Command]) -> Action:
    fetches = [s for s in steps if isinstance(s, Action) and s.kind == "fetch"]
    assert len(fetches) == 1, "exactly one fetch step, and it is the shared payload step"
    return fetches[0]


@pytest.mark.parametrize("kind", KINDS)
def test_a_mirror_hit_never_asks_the_publisher(tmp_path: Path, kind: Kind) -> None:
    pin = _pin()
    at = mirror_url(BUNKER, payload_path(kind.unit, pin))
    routes = Routes({at: BODY})
    steps = kind.steps(tmp_path, _fetcher(tmp_path, routes, offline=False), None, pin)
    step = _fetch_step(steps)
    assert step.sources == (at, pin.url)
    step.perform()
    assert routes.requested == [at]
    assert step.facts["source"] == "mirror" and step.facts["fetched_from"] == at


@pytest.mark.parametrize("kind", KINDS)
def test_online_a_wrong_mirror_digest_falls_back_naming_the_reason(
    tmp_path: Path, kind: Kind
) -> None:
    pin = _pin()
    at = mirror_url(BUNKER, payload_path(kind.unit, pin))
    routes = Routes({at: b"tampered", pin.url: BODY})
    step = _fetch_step(kind.steps(tmp_path, _fetcher(tmp_path, routes, offline=False), None, pin))
    outcome = step.perform()
    assert routes.requested == [at, pin.url]
    assert step.facts["source"] == "publisher"
    assert "does not match the digest the manifest declares" in step.facts["mirror_failure"]
    assert "the mirror was passed over" in outcome


@pytest.mark.parametrize("corrupt", [True, False], ids=["corrupt", "missing"])
@pytest.mark.parametrize("kind", KINDS)
def test_offline_a_bad_or_missing_mirror_copy_is_refused_and_the_publisher_never_asked(
    tmp_path: Path, kind: Kind, corrupt: bool
) -> None:
    pin = _pin()
    at = mirror_url(BUNKER, payload_path(kind.unit, pin))
    routes = Routes({at: b"tampered", pin.url: BODY} if corrupt else {pin.url: BODY})
    step = _fetch_step(kind.steps(tmp_path, _fetcher(tmp_path, routes, offline=True), None, pin))
    assert step.sources == (at,)
    with pytest.raises(BackendError, match="offline Bunker download failed"):
        step.perform()
    assert pin.url not in routes.requested
    assert not list((tmp_path / "cache").glob("*"))


@pytest.mark.parametrize("kind", KINDS)
def test_online_when_both_fail_the_refusal_names_both_reasons(tmp_path: Path, kind: Kind) -> None:
    pin = _pin()
    routes = Routes({pin.url: b"also tampered"})
    step = _fetch_step(kind.steps(tmp_path, _fetcher(tmp_path, routes, offline=False), None, pin))
    with pytest.raises(BackendError) as caught:
        step.perform()
    text = str(caught.value)
    assert "does not match the digest the manifest declares" in text
    assert "The LAN mirror was tried first and passed over" in text and "404" in text


@pytest.mark.parametrize("kind", KINDS)
def test_two_variants_with_one_basename_fetch_their_own_bytes(tmp_path: Path, kind: Kind) -> None:
    a_body, b_body = b"x86_64 build", b"aarch64 build"
    a = _pin(a_body, "https://example.invalid/x86_64/tool.tar.gz")
    b = _pin(b_body, "https://example.invalid/aarch64/tool.tar.gz")
    at_a = mirror_url(BUNKER, payload_path(kind.unit, a))
    at_b = mirror_url(BUNKER, payload_path(kind.unit, b))
    assert at_a != at_b
    routes = Routes({at_a: a_body, at_b: b_body})
    fa = _fetcher(tmp_path / "a", routes, offline=True)
    fb = _fetcher(tmp_path / "b", routes, offline=True)
    _fetch_step(kind.steps(tmp_path / "a", fa, None, a)).perform()
    _fetch_step(kind.steps(tmp_path / "b", fb, None, b)).perform()
    assert fa.path_for(a).read_bytes() == a_body and fb.path_for(b).read_bytes() == b_body
    # The other variant's bytes under this pin's name are a digest refusal,
    # never the wrong tree.
    cross = _fetcher(tmp_path / "c", Routes({at_a: b_body}), offline=True)
    with pytest.raises(BackendError):
        _fetch_step(kind.steps(tmp_path / "c", cross, None, a)).perform()


# -- offline preflight --------------------------------------------------------


def _context(
    tmp_path: Path, kind: Kind, pin: RemoteArtifact, *, present: bool
) -> ResolutionContext:
    rows = [catalogue_artifact(kind.unit, payload_name(pin), BODY)] if present else []
    return make_context(tmp_path / "ctx", rows)


@pytest.mark.parametrize("kind", KINDS)
def test_offline_a_pin_the_bunker_lacks_is_refused_before_any_step_exists(
    tmp_path: Path, kind: Kind
) -> None:
    pin = _pin()
    routes = Routes({})
    context = _context(tmp_path, kind, pin, present=False)
    expected = f"{kind.unit}/{pin.sha256}/release.tar.gz: not on Bunker"
    with pytest.raises(CatalogueMiss, match=expected):
        kind.steps(tmp_path, _fetcher(tmp_path, routes, offline=True), context, pin)
    assert routes.requested == []
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize("kind", KINDS)
def test_offline_a_row_that_does_not_match_the_repository_pin_is_refused(
    tmp_path: Path, kind: Kind
) -> None:
    """The Bunker record is compared with the REPOSITORY's pin, never with itself."""
    pin = _pin()
    other = hashlib.sha256(b"something else").hexdigest()
    row = catalogue_artifact(kind.unit, payload_name(pin), BODY, sha256=other)
    context = make_context(tmp_path / "ctx", [row])
    with pytest.raises(CatalogueMiss, match="does not match the repository sha256 pin"):
        kind.steps(tmp_path, _fetcher(tmp_path, Routes({}), offline=True), context, pin)


@pytest.mark.parametrize("kind", KINDS)
def test_offline_a_pin_the_bunker_has_gets_steps_and_a_provenance_note(
    tmp_path: Path, kind: Kind
) -> None:
    pin = _pin()
    context = _context(tmp_path, kind, pin, present=True)
    steps = kind.steps(tmp_path, _fetcher(tmp_path, Routes({}), offline=True), context, pin)
    assert _fetch_step(steps)
    assert (kind.unit, payload_name(pin)) in context.notes


@pytest.mark.parametrize("kind", KINDS)
def test_offline_a_pin_already_in_the_cache_needs_no_bunker_row(tmp_path: Path, kind: Kind) -> None:
    pin = _pin()
    fetcher = _fetcher(tmp_path, Routes({}), offline=True)
    fetcher.path_for(pin).parent.mkdir(parents=True)
    fetcher.path_for(pin).write_bytes(BODY)
    context = _context(tmp_path, kind, pin, present=False)
    assert _fetch_step(kind.steps(tmp_path, fetcher, context, pin))
    # ...but a cached file with the right name and the wrong bytes is not a hit.
    fetcher.path_for(pin).write_bytes(b"x" * len(BODY))
    with pytest.raises(CatalogueMiss):
        kind.steps(tmp_path, fetcher, context, pin)


@pytest.mark.parametrize("kind", KINDS)
def test_online_or_without_a_context_nothing_is_preflighted(tmp_path: Path, kind: Kind) -> None:
    pin = _pin()
    fetcher = _fetcher(tmp_path, Routes({}), offline=False)
    assert _fetch_step(kind.steps(tmp_path, fetcher, None, pin))
    online = make_context(tmp_path / "ctx", [], offline=False)
    assert _fetch_step(kind.steps(tmp_path, fetcher, online, pin))
    assert online.notes == {}


def test_a_venv_without_a_payload_has_nothing_to_preflight(tmp_path: Path) -> None:
    manifest = _manifest(
        "plainvenv", {"method": "venv", "requirements": [HASHED], "expose": ["tool"]}
    )
    block = manifest.install[0].install
    assert isinstance(block, VenvInstall)
    backend = VenvBackend(
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        context=make_context(tmp_path / "ctx", []),
    )
    steps = backend.steps(manifest, block)
    assert not [s for s in steps if isinstance(s, Action) and s.kind == "fetch"]


def test_a_venv_payload_keeps_its_build_script_and_pip_steps(tmp_path: Path) -> None:
    steps = _venv(tmp_path, _fetcher(tmp_path, Routes({}), offline=False), None, _pin())
    commands = [s for s in steps if isinstance(s, Command)]
    assert any("--require-hashes" in c.argv for c in commands), "pip's hash-pinned install"
    assert any(c.argv == ("sh", "build.sh") for c in commands), "the payload build script"
    assert any(isinstance(s, Action) and s.kind == "requirements" for s in steps)


def test_a_node_payload_keeps_its_script_protections_and_fetches_no_runtime(
    tmp_path: Path,
) -> None:
    steps = _node(tmp_path, _fetcher(tmp_path, Routes({}), offline=False), None, _pin())
    assert len([s for s in steps if isinstance(s, Action) and s.kind == "fetch"]) == 1
    npm = [s for s in steps if isinstance(s, Command) and s.argv[0] == "npm"]
    assert npm and all("--ignore-scripts" in c.argv for c in npm)
    assert [c for c in npm if c.argv[1] == "ci"]
    assert not [s for s in steps if isinstance(s, Command) and s.argv[0] in {"curl", "wget"}]


# -- the plan-time pass --------------------------------------------------------


def _planned(kind: str, pin: RemoteArtifact, *, deb_installed: bool = False) -> PlannedPackage:
    block: dict[str, Any]
    extra: dict[str, Any] = {}
    if kind == "source":
        block = {"method": "source", "source": pin.model_dump(), "build_system": "make"}
    elif kind == "binary":
        block = {
            "method": "binary",
            "artifact": pin.model_dump(),
            "format": "deb",
            "deb_package": "xunit",
        }
    elif kind == "venv":
        block = {
            "method": "venv",
            "requirements": [HASHED],
            "payload": pin.model_dump(),
            "tree_marker": "r.py",
        }
        extra = {
            "launchers": [
                {"name": "u", "exec": "exec {venv}/bin/python r.py", "working_directory": "/x"}
            ]
        }
    else:
        block = {
            "method": "node",
            "artifact": pin.model_dump(),
            "node_min_version": "20.19",
            "entry": "s.js",
        }
    manifest = _manifest("unit-" + kind, block, **extra)
    return PlannedPackage(
        manifest, manifest.install[0], (), requested_by=("requested",), deb_installed=deb_installed
    )


def never_cached(pin: RemoteArtifact) -> bool:
    return False


def always_cached(pin: RemoteArtifact) -> bool:
    return True


@pytest.mark.parametrize("kind", ["source", "binary", "venv", "node"])
def test_payload_misses_names_the_unit_the_bunker_cannot_answer_for(
    tmp_path: Path, kind: str
) -> None:
    pin = _pin()
    unit = _planned(kind, pin)
    plan = InstallPlan(TARGET, (unit,))
    context = make_context(tmp_path, [])
    misses = payload_misses(plan, context, frozenset(), cached=never_cached)
    assert list(misses) == [unit.name] and "not on Bunker" in str(misses[unit.name])
    # Online it asks nothing.
    online = make_context(tmp_path / "o", [], offline=False)
    assert payload_misses(plan, online, frozenset(), cached=never_cached) == {}
    # A cached payload needs no row.
    assert payload_misses(plan, context, frozenset(), cached=always_cached) == {}
    # A Bunker row for it answers.
    row = catalogue_artifact(unit.name, payload_name(pin), BODY)
    answered = make_context(tmp_path / "a", [row])
    assert payload_misses(plan, answered, frozenset(), cached=never_cached) == {}


def test_payload_misses_skips_a_build_already_current_and_an_installed_deb(
    tmp_path: Path,
) -> None:
    pin = _pin()
    source = _planned("source", pin)
    deb = _planned("binary", pin, deb_installed=True)
    plan = InstallPlan(TARGET, (source, deb))
    context = make_context(tmp_path, [])
    assert set(payload_misses(plan, context, frozenset(), cached=never_cached)) == {source.name}
    assert payload_misses(plan, context, frozenset({source.name}), cached=never_cached) == {}


@pytest.mark.parametrize("kind", ["venv", "node"])
def test_a_venv_or_node_unit_named_built_is_still_asked_because_its_steps_still_run(
    tmp_path: Path, kind: str
) -> None:
    """venv and node take no part in ``already_built`` and ``commands_for`` never
    skips them (pip over a satisfied venv is their cheap idempotency), so their
    steps, and the backend's own preflight, run even for a name in ``built``:
    the plan-time pass must ask for them or the backend would raise unhandled."""
    pin = _pin()
    unit = _planned(kind, pin)
    plan = InstallPlan(TARGET, (unit,))
    context = make_context(tmp_path, [])
    misses = payload_misses(plan, context, frozenset({unit.name}), cached=never_cached)
    assert list(misses) == [unit.name]


# -- the static-data CLI path --------------------------------------------------


def _ics_forms_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, bytes], Path, Path]:
    """Enrol a Bunker holding fixture bytes for every ics-forms PDF in a copied
    manifest. Returns the cli module, the expected files, the catalog and the
    install prefix."""
    from hammunition.backends.apt import AptBackend, AptPackageState, AptSimulation

    cli = importlib.import_module("hammunition.cli.main")
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("USER", "op")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cli, "operator", lambda args: "")
    monkeypatch.setattr(Target, "detect", classmethod(lambda cls: TARGET))
    monkeypatch.setattr(AptBackend, "lists_populated", lambda self: True)
    monkeypatch.setattr(cli, "_apt_lists_note", lambda apt: "apt lists: fixture")
    monkeypatch.setattr(
        AptBackend,
        "probe",
        lambda self, pkgs: {
            p: AptPackageState(name=p, installed=None, candidate="1.0") for p in pkgs
        },
    )
    monkeypatch.setattr(
        AptBackend,
        "simulate",
        lambda self, pkgs, *, release=None, no_recommends=False: AptSimulation(
            ok=True, installs={p: frozenset({"stable"}) for p in pkgs}, release=release
        ),
    )
    catalog = tmp_path / "catalog"
    (catalog / "packages").mkdir(parents=True)
    (catalog / "profiles").mkdir()
    real = Path(__file__).resolve().parents[1] / "catalog"
    shutil.copy(real / "categories.yaml", catalog / "categories.yaml")
    source = real / "packages/ics-forms.yaml"
    manifest = yaml.safe_load(source.read_text())
    block = manifest["install"][0]["install"]
    export = tmp_path / "export"
    (export / "ics-forms").mkdir(parents=True)
    rows: list[dict[str, object]] = []
    expected: dict[str, bytes] = {}
    for index, pin in enumerate(block["artifacts"]):
        body = f"%PDF-1.4 fixture {index}\n".encode()
        pin["sha256"] = hashlib.sha256(body).hexdigest()
        pin["size"] = len(body)
        name = pin["install_as"]
        (export / "ics-forms" / name).write_bytes(body)
        rows.append(
            catalogue_artifact(
                "ics-forms", name, body, publisher_url=pin["url"], licence=block["licence"]
            )
        )
        expected[name] = body
    (catalog / "packages/ics-forms.yaml").write_text(yaml.safe_dump(manifest))
    private = key(tmp_path)
    public = private.with_suffix(".pub").read_text().strip()
    raw, signatures = signed(tmp_path, private, document(public, artifacts=rows))
    (export / "catalogue.json").write_bytes(raw)
    (export / "catalogue.sig.d").mkdir()
    for name, body in signatures.items():
        (export / name).write_bytes(body)
    strength = classify(public)
    save_mirror(
        MirrorState(
            export.as_uri(),
            "bunker",
            "personal",
            None,
            (EnrolledKey(strength.fingerprint, public, strength.algorithm, strength.bits, False),),
            0,
        )
    )
    save_station(Station(mirror=export.as_uri()))  # no map_regions
    return cli, expected, catalog, tmp_path / "prefix"


def test_ics_forms_offline_install_without_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli, expected, catalog, prefix = _ics_forms_offline(tmp_path, monkeypatch)

    def isolated_source(
        fetcher: Fetcher, *, build_root: Path, owner: str | None = None
    ) -> SourceBackend:
        return SourceBackend(fetcher, build_root=build_root, prefix=prefix, owner=owner)

    monkeypatch.setattr(cli, "SourceBackend", isolated_source)
    assert cli.main(["--catalog", str(catalog), "install", "ics-forms", "--offline", "--yes"]) == 0
    destination = prefix / "share/hammunition/data/ics-forms"
    assert {path.name: path.read_bytes() for path in destination.glob("*.pdf")} == expected


def test_the_install_command_gives_its_fetcher_the_signed_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Fetcher cmd_install builds knows the verified catalogue's sha256 for a
    mirror path, and the payload backends share the run's context."""
    cli, expected, catalog, prefix = _ics_forms_offline(tmp_path, monkeypatch)
    seen: dict[str, SourceBackend] = {}

    def isolated_source(
        fetcher: Fetcher, *, build_root: Path, owner: str | None = None
    ) -> SourceBackend:
        backend = SourceBackend(fetcher, build_root=build_root, prefix=prefix, owner=owner)
        seen["backend"] = backend
        return backend

    monkeypatch.setattr(cli, "SourceBackend", isolated_source)
    argv = ["--catalog", str(catalog), "install", "ics-forms", "--offline", "--dry-run"]
    assert cli.main(argv) == 0
    backend = seen["backend"]
    lookup = backend.fetcher.signed_sha256
    assert lookup is not None
    name = next(iter(expected))
    assert lookup(MirrorPath("ics-forms", name)) == hashlib.sha256(expected[name]).hexdigest()
    assert lookup(MirrorPath("ics-forms", "no-such-file.pdf")) is None
    assert backend.fetcher.bunker == "bunker"  # warnings name the Bunker
    assert isinstance(backend.context, ResolutionContext) and backend.context.offline
