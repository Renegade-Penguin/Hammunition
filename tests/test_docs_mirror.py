# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""D-070 is written down where people look.  CLAUDE.md: a feature is not
done until it is documented."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TITLE = (
    "## D-070 — A data artifact may be taken from a LAN mirror the operator names, "
    "verified the same either way, and the engine can list what it would fetch "
    "without a station"
)


def _flat(path: str) -> str:
    return " ".join((REPO_ROOT / path).read_text().split())


def test_d070_is_recorded_under_its_assigned_title_and_nothing_after_it_is_older() -> None:
    """D-070 was the last entry when it was written; a later decision (D-071)
    is appended after it, never before, and never numbered below it."""
    text = (REPO_ROOT / "docs" / "DECISIONS.md").read_text()
    assert TITLE in text
    after = text[text.index(TITLE) + len(TITLE) :]
    later = [int(n) for n in re.findall(r"\n## D-(\d{3}) ", after)]
    assert all(n > 70 for n in later), f"a decision below D-070 recorded after it: {later}"


def test_the_guide_points_at_the_bunker_and_says_lan_only() -> None:
    guide = _flat("docs/guides/lan-mirror.md")
    assert "https://github.com/Renegade-Penguin/hammunition-bunker" in guide
    assert "never reachable from the internet" in guide
    assert "station set --mirror" in guide and "--no-mirror" in guide


def test_the_cli_reference_documents_every_new_flag() -> None:
    cli = _flat("docs/reference/cli.md")
    for flag in ("--mirror URL", "--clear-mirror", "--no-mirror", "`artifacts` document"):
        assert flag in cli, flag


def test_the_log_reference_documents_the_fetch_facts() -> None:
    log = _flat("docs/reference/transaction-log.md")
    assert "fetched_from" in log and "mirror_failure" in log


def test_claude_md_and_the_changelog_carry_it() -> None:
    assert "**D-070**" in _flat("CLAUDE.md")
    # In whichever release carries it: a test tied to "Unreleased" goes red
    # the day the entry is released, which is what happened to v0.16.0.
    # A change not yet released lives in a changelog.d/ fragment, not under
    # Unreleased (which only ever says "Nothing yet."); look in both.
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
    fragments = "".join(p.read_text() for p in sorted((REPO_ROOT / "changelog.d").glob("*.md")))
    assert "D-070" in changelog[changelog.index("## Unreleased") :] + fragments


def test_the_guide_distinguishes_a_plain_mirror_from_an_enrolled_bunker() -> None:
    """D-070's own plan is still made against the publishers; D-085 is the
    narrower, deliberate exception, and only once its keys are enrolled.
    The old blanket "a mirror does not make an install work offline" line is
    no longer true now that an enrolled Bunker can (D-085); the guide must
    say so without implying a bare `--mirror` grants it."""
    guide = _flat("docs/guides/lan-mirror.md")
    assert "does not enrol keys or authorize using the mirror's metadata" in guide
    assert "does not make an install work offline" not in guide
    assert "hammunition mirror enrol" in guide and "--offline" in guide
