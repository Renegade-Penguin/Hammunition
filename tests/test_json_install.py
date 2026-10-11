# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``install``/``uninstall --dry-run --json`` through main().  D-059."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends.apt import AptBackend, AptPackageState, AptSimulation
from hammunition.backends.base import CommandResult
from hammunition.distro import Target
from hammunition.state import ArtifactRemoval, RemovalPlan
from json_support import (
    FIXTURE_CATALOG,
    assert_golden,
    assert_golden_text,
    assert_text_values_in_json,
    parse_one,
    validate,
)

cli = importlib.import_module("hammunition.cli.main")

TARGET = Target(
    distro="debian", version="13", arch="x86_64", pretty_name="Debian GNU/Linux 13 (trixie)"
)


def _machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """A target and an apt that need no real machine, as an unprivileged
    operator `op`; returns the placeholders for the machine paths."""
    monkeypatch.setattr(Target, "detect", classmethod(lambda cls: TARGET))
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("USER", "op")
    monkeypatch.setattr(AptBackend, "lists_populated", lambda self: True)
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
    # A12: test_runlog's `doctor` calls go through the real security-key
    # gather path (its CHECKS are not stubbed the way test_json_doctor's are),
    # so this shared machine fixture needs its own explicit, inert result.
    monkeypatch.setattr(
        cli,
        "_security_key_probe",
        lambda argv: CommandResult(
            argv=argv, returncode=127, stdout="", stderr="diagnostic tool unavailable in fixture"
        ),
    )
    return {str(tmp_path): "<tmp>", str(FIXTURE_CATALOG): "<catalog>"}


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    rc = cli.main(["--catalog", str(FIXTURE_CATALOG), *argv])
    return rc, capsys.readouterr().out


def _placeholders(text: str, replacements: dict[str, str]) -> str:
    for real, placeholder in replacements.items():
        text = text.replace(real, placeholder)
    return text


def test_the_install_text_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _machine(monkeypatch, tmp_path)
    rc, out = _run(capsys, "install", "--dry-run", "fixture-apt")
    assert rc == 0
    assert_golden_text("install-dry-run", _placeholders(out, paths))


def test_the_install_plan_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from hammunition.interface.plan import render_plan_view

    paths = _machine(monkeypatch, tmp_path)
    rc, out = _run(capsys, "install", "--dry-run", "--json", "fixture-apt")
    doc = parse_one(out)
    assert rc == 0 and doc["kind"] == "plan" and doc["outcome"] == "planned"
    validate(doc)
    assert doc["step_count"] == len(doc["install"]["commands"])
    assert [step["index"] for step in doc["install"]["commands"]] == list(
        range(1, doc["step_count"] + 1)
    )
    assert_golden("install-dry-run", doc, paths)
    _rc, text = _run(capsys, "install", "--dry-run", "fixture-apt")
    assert_text_values_in_json(text, doc, render_plan_view, cli.cmd_install)


def test_a_refused_plan_is_a_plan_document_with_every_blocker_and_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _machine(monkeypatch, tmp_path)
    rc, out = _run(capsys, "install", "--dry-run", "--json", "no-such-unit")
    doc = parse_one(out)
    assert rc == cli.EXIT_UNPLANNABLE
    assert doc["kind"] == "plan" and doc["outcome"] == "refused"
    assert doc["install"] is None
    assert any(b["subject"] == "no-such-unit" for b in doc["blockers"])
    validate(doc)


def test_a_real_install_is_never_driven_through_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review focus: without --dry-run, --json is refused before resolution,
    so no consent gate, sudo prompt or command can be reached."""
    _machine(monkeypatch, tmp_path)

    def must_not_resolve(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a --json install without --dry-run reached resolution")

    monkeypatch.setattr(cli, "resolve", must_not_resolve)
    for verb in ("install", "uninstall"):
        rc, out = _run(capsys, verb, "--json", "--yes", "fixture-apt")
        doc = parse_one(out)
        assert rc == cli.EXIT_UNPLANNABLE and doc["kind"] == "error"
        assert "never driven through --json" in doc["message"]


def _removal(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = RemovalPlan(
        to_remove={"fixture-apt": ["fixture-apt"]},
        left_foreign={"fixture-station": ["libfixture1"]},
        already_absent={"fixture-source": []},
        artifacts={
            "fixture-source": [
                ArtifactRemoval(
                    kind="binary", path=Path("/usr/local/bin/fixture-source"), basis="log"
                )
            ]
        },
        left_unattributed={"fixture-source": ["/usr/local/share/fixture-source/extra"]},
    )
    monkeypatch.setattr(cli, "plan_removal", lambda *args, **kwargs: plan)


def test_the_uninstall_text_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _machine(monkeypatch, tmp_path)
    _removal(monkeypatch)
    rc, out = _run(capsys, "uninstall", "--dry-run", "fixture-apt")
    assert rc == 0
    assert_golden_text("uninstall-dry-run", _placeholders(out, paths))


def test_the_uninstall_plan_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from hammunition.interface.plan import render_removal_view

    paths = _machine(monkeypatch, tmp_path)
    _removal(monkeypatch)
    rc, out = _run(capsys, "uninstall", "--dry-run", "--json", "fixture-apt")
    doc = parse_one(out)
    assert rc == 0 and doc["kind"] == "plan" and doc["action"] == "uninstall"
    validate(doc)
    assert doc["step_count"] == len(doc["removal"]["commands"])
    assert [step["index"] for step in doc["removal"]["commands"]] == list(
        range(1, doc["step_count"] + 1)
    )
    assert_golden("uninstall-dry-run", doc, paths)
    _rc, text = _run(capsys, "uninstall", "--dry-run", "fixture-apt")
    assert_text_values_in_json(text, doc, render_removal_view, cli.cmd_uninstall)


def test_a_refusal_after_resolution_is_a_refused_plan_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not only PlanError: every exit-2 refusal of `install` is a plan document."""
    _machine(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "navit_config_blocker", lambda plan: "the stock file is missing")
    rc, out = _run(capsys, "install", "--dry-run", "--json", "fixture-apt")
    doc = parse_one(out)
    validate(doc)
    assert rc == cli.EXIT_UNPLANNABLE
    assert doc["outcome"] == "refused" and doc["install"] is None
    assert doc["blockers"] == [
        {"subject": "navit configuration", "reason": "the stock file is missing", "remedy": None}
    ]


# D-060: a unit for one desktop, on a machine whose session files name another.
# A throwaway catalog, so the shared fixture catalog's goldens do not move.

_DESK_UNIT = """name: {name}
version: "1.0"
summary: The {name} fixture
categories: [digital-modes]
{extra}install:
  - install:
      method: apt
      packages: [{name}]
update:
  probe:
    method: apt_policy
documentation:
  what_it_does: Stands in for a unit in the desktop plan golden test.
  why_you_want_it: The desktop deferral needs a profile to be deferred from.
  upstream_url: https://example.invalid/{name}
"""


def _desktop_catalog(root: Path) -> Path:
    (root / "packages").mkdir(parents=True)
    (root / "profiles").mkdir()
    (root / "packages" / "fixture-gps.yaml").write_text(
        _DESK_UNIT.format(name="fixture-gps", extra="")
    )
    (root / "packages" / "fixture-tray.yaml").write_text(
        _DESK_UNIT.format(name="fixture-tray", extra="desktops: [kde]\n")
    )
    (root / "profiles" / "fixture-desk.yaml").write_text(
        """name: fixture-desk
summary: A GPS unit and a Plasma tray, as a profile
packages: [fixture-gps, fixture-tray]
documentation:
  what_it_installs: A fixture unit for any desktop and one for KDE Plasma only.
  why_together: The desktop deferral needs a profile member it can defer.
  deliberately_excludes: Everything real.
  manual_configuration: Nothing; it is a fixture.
"""
    )
    return root


def test_the_plasma_tray_deferred_on_an_xfce_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The session files name Xfce: the desktops read and the tray's deferral,
    with its reason and remedy, are in the document as the text prints them."""
    from hammunition.desktop import Desktop, SessionScan
    from hammunition.interface.plan import describe_sessions, render_plan_view

    paths = _machine(monkeypatch, tmp_path)
    catalog = _desktop_catalog(tmp_path / "catalog")
    paths[str(catalog)] = "<catalog>"
    monkeypatch.setattr(
        cli,
        "scan_sessions",
        lambda: SessionScan(desktops=frozenset({Desktop.xfce}), unrecognised=("sway.desktop",)),
    )

    def run(*argv: str) -> tuple[int, str]:
        rc = cli.main(["--catalog", str(catalog), *argv])
        return rc, capsys.readouterr().out

    rc, out = run("install", "--dry-run", "--json", "fixture-desk")
    doc = parse_one(out)
    assert rc == 0 and doc["outcome"] == "planned"
    validate(doc)
    install = doc["install"]
    assert install["desktops_read"] == {
        "desktops": ["xfce"],
        "unrecognised": ["sway.desktop"],
        "summary": "Xfce; also sway.desktop, which names no desktop the catalog knows",
    }
    (tray,) = [d for d in install["deferrals"] if d["subject"] == "fixture-tray"]
    assert tray["kind"] == "package"
    assert "no KDE Plasma session (it has: Xfce)" in tray["why"]
    assert [p["name"] for p in install["packages"]] == ["fixture-gps"]
    assert_golden("install-desktop-deferred", doc, paths)

    _rc, text = run("install", "--dry-run", "fixture-desk")
    assert_golden_text("install-desktop-deferred", _placeholders(text, paths))
    assert "Desktops read from session files" in text
    assert_text_values_in_json(text, doc, render_plan_view, describe_sessions, cli.cmd_install)


def test_the_plasma_tray_typed_by_name_is_refused_with_the_desktop_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from hammunition.desktop import Desktop, SessionScan

    _machine(monkeypatch, tmp_path)
    catalog = _desktop_catalog(tmp_path / "catalog")
    monkeypatch.setattr(
        cli, "scan_sessions", lambda: SessionScan(desktops=frozenset({Desktop.xfce}))
    )
    rc = cli.main(["--catalog", str(catalog), "install", "--dry-run", "--json", "fixture-tray"])
    doc = parse_one(capsys.readouterr().out)
    assert rc == cli.EXIT_UNPLANNABLE and doc["outcome"] == "refused"
    validate(doc)
    (blocker,) = doc["blockers"]
    assert blocker["subject"] == "fixture-tray"
    assert "no KDE Plasma session (it has: Xfce)" in blocker["reason"]
    assert blocker["remedy"].startswith("fixture-tray does nothing without a KDE Plasma session")
