# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""A user service's first-start behaviour, and what uninstall leaves in place.

Issue #289 (deferred from the ``reticulum-core`` review, ruling H5).  The plan
for a service says what it will *do on first start*, and the uninstall plan
names what it leaves behind.  Both texts come from a manifest, so they are
data: validated at load, cleaned again before they reach a terminal, capped,
and shown, never acted on.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hammunition.interface.envelope import target_view
from hammunition.interface.plan import (
    PlanDocument,
    build_install_view,
    build_removal_view,
    render_plan_view,
    render_removal_view,
)
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import ManifestError, PackageManifest
from hammunition.station import Station
from hammunition.userservice import plan_user_services

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "packages"

_DOC: dict[str, object] = {
    "what_it_does": "Serves a thing on loopback for the tests.",
    "why_you_want_it": "So the disclosure has a unit to be about.",
    "upstream_url": "https://example.invalid/svc",
}

_SERVICE: dict[str, Any] = {
    "name": "hammunition-fixture",
    "description": "A fixture service",
    "exec": ["/usr/local/bin/fixture"],
}


def _manifest(services: list[dict[str, Any]] | None = None, **extra: Any) -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": "fixture",
            "version": "1.0",
            "summary": "Fixture",
            "categories": ["mesh"],
            "install": [{"install": {"method": "apt", "packages": ["fixture"]}}],
            "update": {"probe": {"method": "apt_policy"}, "strategy": "apt_upgrade"},
            "documentation": _DOC,
            "user_services": services if services is not None else [_SERVICE],
            **extra,
        }
    )


# -- the schema -------------------------------------------------------------


def test_first_start_is_optional_and_defaults_to_none() -> None:
    (svc,) = _manifest().user_services
    assert svc.first_start is None
    assert _manifest().left_in_place == []


def test_first_start_is_carried() -> None:
    (svc,) = _manifest([{**_SERVICE, "first_start": "Writes ~/.fixture/config."}]).user_services
    assert svc.first_start == "Writes ~/.fixture/config."


@pytest.mark.parametrize(
    "bad",
    [
        "\x1b[31mred\x1b[0m",  # CSI
        "\x1b]0;title\x07",  # OSC
        "two\nlines",
        "tab\there",
        "bell\x07",
        "nul\x00byte",
        "csi\x9b31m",  # C1 CSI
        "flip ‮ text",  # bidi override: a Cf character
        "",
        "   ",
        "x" * 401,
    ],
)
def test_first_start_refuses_control_text_blank_and_over_length(bad: str) -> None:
    with pytest.raises((ValidationError, ManifestError)):
        _manifest([{**_SERVICE, "first_start": bad}])


def test_first_start_at_the_cap_loads() -> None:
    (svc,) = _manifest([{**_SERVICE, "first_start": "x" * 400}]).user_services
    assert svc.first_start is not None and len(svc.first_start) == 400


def test_left_in_place_takes_home_relative_paths() -> None:
    manifest = _manifest(left_in_place=["~/.fixture", "~/.config/fixture"])
    assert manifest.left_in_place == ["~/.fixture", "~/.config/fixture"]


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",  # absolute
        "~root/x",  # another user's home
        "~/",  # the whole home
        "~",
        "~/../etc",  # traversal
        "~/a/../b",
        "~/a/./b",
        "~//x",
        "~/x\ny",
        "~/x\x1b[31m",
        "~/x‮y",
        "~/ x",  # whitespace at a component edge
        "$HOME/.x",
        "~/*",  # a glob is not a path
        "",
        "~/" + "a" * 200,
    ],
)
def test_left_in_place_refuses_anything_but_a_plain_home_path(bad: str) -> None:
    with pytest.raises((ValidationError, ManifestError)):
        _manifest(left_in_place=[bad])


def test_left_in_place_refuses_duplicates_and_a_long_list() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        _manifest(left_in_place=["~/.a", "~/.a"])
    with pytest.raises((ValidationError, ManifestError)):
        _manifest(left_in_place=[f"~/.p{i}" for i in range(17)])


# -- the install plan -------------------------------------------------------


def _install_text(*services: Any) -> tuple[Any, str]:
    from test_json_plan import rich_plan

    plan, _commands = rich_plan()
    plan = dataclasses.replace(plan, user_services=tuple(services))
    view = build_install_view(plan, [], euid=1000)
    return view, "\n".join(render_plan_view(view, target=target_view(plan.target)))


def _planned(first_start: str | None) -> Any:
    entry = dict(_SERVICE)
    if first_start is not None:
        entry["first_start"] = first_start
    planned, _d, _n = plan_user_services(_manifest([entry]), Station(), None)
    (svc,) = planned
    return svc


def test_the_planner_carries_first_start() -> None:
    assert _planned("Creates an identity.").first_start == "Creates an identity."
    assert _planned(None).first_start is None


def test_the_plan_prints_first_start_under_user_services() -> None:
    view, text = _install_text(_planned("Writes ~/.fixture/config and creates an identity."))
    (line,) = view.user_services
    assert line.first_start == "Writes ~/.fixture/config and creates an identity."
    block = text.split("User services (D-073)", 1)[1]
    assert "first start" in block
    assert "Writes ~/.fixture/config and creates an identity." in block


def test_a_service_without_the_field_prints_exactly_what_it_did() -> None:
    view, text = _install_text(_planned(None))
    assert view.user_services[0].first_start == ""
    assert "first start" not in text


def test_hostile_first_start_text_is_cleaned_before_it_reaches_the_terminal() -> None:
    """The schema refuses this at load; a manifest built another way (a test, a
    future loader, ``model_construct``) still must not reach a terminal raw."""
    svc = _planned("ok")
    hostile = "pwn\x1b[2J\x1b]0;x\x07 \x9b31m\x00end " + "y" * 2000
    svc = dataclasses.replace(svc, first_start=hostile)
    view, text = _install_text(svc)
    shown = view.user_services[0].first_start
    assert "\x1b" not in text and "\x07" not in text and "\x9b" not in text and "\x00" not in text
    assert "\x1b" not in shown and len(shown) <= 400
    assert "pwn" in shown


def test_json_and_text_agree_on_first_start() -> None:
    from test_json_plan import rich_plan

    plan, _commands = rich_plan()
    plan = dataclasses.replace(plan, user_services=(_planned("Creates an identity."),))
    view = build_install_view(plan, [], euid=1000)
    document = PlanDocument(
        action="install",
        requested=("fixture",),
        outcome="planned",
        step_count=0,
        target=target_view(plan.target),
        blockers=(),
        install=view,
        removal=None,
    )
    assert document.install is not None
    assert document.install.user_services[0].first_start == "Creates an identity."


# -- the uninstall plan -----------------------------------------------------


def _removal(tmp_path: Path, manifest: PackageManifest, *, installed: bool = True) -> Any:
    from hammunition.backends import AptPackageState
    from hammunition.distro import Target
    from hammunition.state import RemovalPaths, plan_removal

    paths = RemovalPaths(
        prefix=tmp_path / "prefix",
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        applications_dir=tmp_path / "applications",
    )
    states = {
        "fixture": AptPackageState(
            "fixture", installed="1.0" if installed else None, candidate="1.0"
        )
    }
    return plan_removal(
        ["fixture"],
        catalog={"fixture": manifest},
        profiles={},
        target=Target(distro="debian", version="13", arch="x86_64"),
        attributed=frozenset({"fixture"}),
        states=states,
        paths=paths,
    )


def test_uninstall_plan_names_what_is_left_in_place(tmp_path: Path) -> None:
    plan = _removal(tmp_path, _manifest(left_in_place=["~/.fixture", "~/.fixture-data"]))
    assert plan.left_in_place == {"fixture": ["~/.fixture", "~/.fixture-data"]}
    view = build_removal_view(plan, [], euid=1000)
    (entry,) = view.left_in_place
    assert entry.unit == "fixture" and entry.paths == ("~/.fixture", "~/.fixture-data")
    text = "\n".join(render_removal_view(view, target=target_view(_target())))
    assert "Left in place" in text and "~/.fixture-data" in text
    assert "Left in place by design" in text and "never removes" in text


def _target() -> Any:
    from hammunition.distro import Target

    return Target(distro="debian", version="13", arch="x86_64")


def test_nothing_is_listed_for_a_unit_that_is_not_installed(tmp_path: Path) -> None:
    plan = _removal(tmp_path, _manifest(left_in_place=["~/.fixture"]), installed=False)
    assert plan.left_in_place == {}
    view = build_removal_view(plan, [], euid=1000)
    assert view.left_in_place == ()
    assert "~/.fixture" not in "\n".join(render_removal_view(view, target=target_view(_target())))


def test_a_unit_without_the_field_removes_exactly_as_before(tmp_path: Path) -> None:
    plan = _removal(tmp_path, _manifest())
    assert plan.left_in_place == {}
    view = build_removal_view(plan, [], euid=1000)
    text = "\n".join(render_removal_view(view, target=target_view(_target())))
    assert "Left in place by design" not in text


def test_left_in_place_is_shown_never_acted_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directory exists, holds a file, and survives the plan: the paths are
    disclosure only, and no removal step names them."""
    from hammunition.execute import artifact_removal_steps

    home = tmp_path / "home"
    (home / ".fixture").mkdir(parents=True)
    keep = home / ".fixture" / "identity"
    keep.write_text("secret")
    monkeypatch.setenv("HOME", str(home))
    plan = _removal(tmp_path, _manifest(left_in_place=["~/.fixture"]))
    steps = artifact_removal_steps(plan)
    blob = " ".join(repr(s) for s in steps)
    assert ".fixture" not in blob
    assert keep.read_text() == "secret"


def test_hostile_left_in_place_text_is_cleaned_on_the_way_out(tmp_path: Path) -> None:
    from hammunition.state import RemovalPlan

    plan = RemovalPlan(
        to_remove={"fixture": ["fixture"]},
        left_foreign={},
        already_absent={},
        left_in_place={"fixture": ["~/.x\x1b[2J\x07" + "z" * 500]},
    )
    view = build_removal_view(plan, [], euid=1000)
    text = "\n".join(render_removal_view(view, target=target_view(_target())))
    assert "\x1b" not in text and "\x07" not in text
    assert all(len(p) <= 120 for e in view.left_in_place for p in e.paths)


# -- the catalog ------------------------------------------------------------


def test_rns_discloses_its_first_start_and_what_uninstall_leaves() -> None:
    rns = load_catalog(CATALOG)["rns"]
    (svc,) = rns.user_services
    assert svc.first_start is not None
    for phrase in ("~/.reticulum/config", "AutoInterface", "link-local", "identity"):
        assert phrase in svc.first_start, phrase
    assert rns.left_in_place == ["~/.reticulum", "~/.nomadnetwork", "~/.lxmd", "~/.rnsh"]


def test_the_other_reticulum_units_name_their_own_data() -> None:
    catalog = load_catalog(CATALOG)
    assert "~/.lxmd" in catalog["lxmf"].left_in_place
    assert "~/.nomadnetwork" in catalog["nomadnet"].left_in_place
