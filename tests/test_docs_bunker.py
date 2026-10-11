# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""D-085 is written down where people look.  #381, Task 19.

CLAUDE.md: a feature is not done until it is documented. These tests collect
loosely (whitespace-collapsed, case-insensitive text matches) and assert
strictly: each one names the exact phrase it needs, and a negative case
(``test_the_guide_makes_no_false_universal_claim``,
``test_no_permanent_mirror_signers_file_is_recommended``) proves the guard
would catch a regression, not just a presence check that always passes."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _flat(path: str) -> str:
    return " ".join((ROOT / path).read_text().split())


def test_d085_and_offline_remedies_are_documented() -> None:
    decisions = " ".join((ROOT / "docs/DECISIONS.md").read_text().split()).casefold()
    guide = " ".join((ROOT / "docs/guides/lan-mirror.md").read_text().split()).casefold()
    problems = " ".join(
        (ROOT / "docs/troubleshooting/install-failures.md").read_text().split()
    ).casefold()
    assert "## d-085" in decisions
    assert "hardware keys are recommended, never required" in decisions
    for command in (
        "hammunition mirror enrol",
        "hammunition mirror status",
        "hammunition mirror accept-older",
        "--offline",
        "--clear-mirror",
    ):
        assert command in guide
    assert "apt and pip" in guide and "phase 2" in guide
    assert "not on bunker" in problems and "30 days" in problems
    assert "pam" in guide
    fragment = ROOT / "changelog.d/381-offline-bunker.added.md"
    assert "#381" in fragment.read_text() and "D-085" in fragment.read_text()


def test_the_claude_md_decisions_table_carries_d085() -> None:
    claude = _flat("CLAUDE.md").casefold()
    assert "offline bunker catalogue" in claude
    assert "(**d-085**" in claude


def test_bunker_catalogue_reference_is_in_the_site_nav() -> None:
    """D-059: the schema-adjacent reference page is reachable from the nav, not
    an orphan `tests/test_site.py` would have to special-case."""
    mkdocs = _flat("mkdocs.yml")
    assert "reference/bunker-catalogue.md" in mkdocs


def test_the_troubleshooting_index_links_every_new_anchor() -> None:
    index = (ROOT / "docs/troubleshooting/index.md").read_text()
    problems = (ROOT / "docs/troubleshooting/install-failures.md").read_text()
    for anchor in (
        "not-enrolled",
        "unsupported-version",
        "bad-signature",
        "url-changed",
        "rollback",
        "stale",
        "sharing-filter",
        "weak-rsa",
        "hardware-only",
        "token-access-denied",
        "offline-apt-pip-npm",
        "moved-tag",
    ):
        assert f'name="{anchor}"' in problems, anchor
        assert f"install-failures.md#{anchor}" in index, anchor


def test_the_exact_weak_rsa_warning_is_quoted() -> None:
    exact = "RSA <bits>-bit is weak: replace with Ed25519, ECDSA or RSA 3072+"
    guide = (ROOT / "docs/guides/lan-mirror.md").read_text()
    reference = (ROOT / "docs/reference/bunker-catalogue.md").read_text()
    assert exact in guide
    assert exact in reference


def test_group_sharing_is_called_a_filter_not_access_control_over_cleartext() -> None:
    guide = _flat("docs/guides/lan-mirror.md").casefold()
    assert "sharing filter, not access control" in guide
    assert "clear text" in guide or "cleartext" in guide
    assert "x-hammunition-enrolment" in guide


def test_file_scheme_mirrors_are_documented_with_their_export_layout() -> None:
    guide = (ROOT / "docs/guides/lan-mirror.md").read_text()
    assert "file://" in guide
    assert "catalogue.json" in guide
    assert "catalogue.sig.d/" in guide
    assert "inputs/" in guide


def test_the_guide_makes_no_false_universal_claim() -> None:
    """Falsifiability: a guide that said 'every install works offline' would
    make this assertion fail; confirm the positive guard phrase is present
    and the false claim it guards against is not."""
    guide = _flat("docs/guides/lan-mirror.md").casefold()
    assert "does not claim that every software install works offline" in guide
    for false_claim in ("everything works offline", "works fully offline", "with no exceptions"):
        assert false_claim not in guide


def test_no_permanent_mirror_signers_file_is_recommended() -> None:
    """The binding contract uses `mirror.json` and a temporary, per-run
    allowed-signers file (the self-review's correction of the spec's
    permanent `mirror-signers` path). None of the user-facing pages may tell
    an operator to create or maintain one."""
    for path in (
        "docs/guides/lan-mirror.md",
        "docs/reference/cli.md",
        "docs/troubleshooting/install-failures.md",
        "docs/reference/bunker-catalogue.md",
    ):
        assert "mirror-signers" not in (ROOT / path).read_text()
    assert "mirror.json" in (ROOT / "docs/guides/lan-mirror.md").read_text()


def test_the_spec_open_question_3_is_resolved_not_left_open() -> None:
    spec = _flat("docs/superpowers/specs/2026-10-07-offline-bunker-catalogue-design.md")
    assert "resolved" in spec.casefold()
    assert "owner:<enrolment-id>" in spec or "owner:<enrolment id>" in spec
