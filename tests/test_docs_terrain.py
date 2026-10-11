# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Navigation piece 2 is documented where people look, and the docs agree
with the code.  D-061.

CLAUDE.md: "A feature is not done until it is documented", and docs are
tested. These assert the decision, the guide's sections, the CLI reference,
the README row and SCOPE; that the CLI reference's plan excerpt is the golden
test's own output rather than a copy that drifts; that the doctor count is
the number of checks the code has; and that no tile name but the synthetic
ones appears in prose.
"""

from __future__ import annotations

import re
from pathlib import Path

from hammunition.tether_contract import NMEA_PORT as PORT

HOST = "127.0.0.1"

REPO_ROOT = Path(__file__).resolve().parent.parent
DECISIONS = REPO_ROOT / "docs" / "DECISIONS.md"
GUIDE = REPO_ROOT / "docs" / "guides" / "offline-navigation.md"
CLI = REPO_ROOT / "docs" / "reference" / "cli.md"
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "json" / "plan-terrain-text.txt"
DOCTOR = REPO_ROOT / "src" / "hammunition" / "doctor.py"
# The golden test's synthetic tiles: nothing else may appear in prose.
SYNTHETIC = {
    "Copernicus_DSM_COG_10_N00_00_E000_00_DEM",
    "Copernicus_DSM_COG_10_S01_00_W001_00_DEM",
}
TILE = re.compile(r"Copernicus_DSM_COG_10_[NS]\d\d_00_[EW]\d\d\d_00_DEM")


def _d061() -> str:
    text = DECISIONS.read_text()
    start = text.index("## D-061")
    end = text.find("\n## D-", start + 1)
    return text[start : end if end != -1 else len(text)]


def _cli_section(prefix: str) -> str:
    parts = re.split(r"^### (.+)$", CLI.read_text(), flags=re.MULTILINE)
    for i in range(1, len(parts) - 1, 2):
        if parts[i].startswith(prefix):
            return parts[i + 1]
    raise AssertionError(f"cli.md has no section starting {prefix!r}")


def test_d061_follows_d060_in_its_neighbours_shape() -> None:
    text = DECISIONS.read_text()
    assert text.index("## D-060") < text.index("## D-061")
    body = _d061()
    for field in ("**Date:**", "**Status:**", "**Depends on:**", "**Rejected.**"):
        assert field in body, f"D-061 has no {field}"
    assert "**Consequences.**" in body


def test_d061_states_both_verification_wordings_and_the_empty_pin_file() -> None:
    body = " ".join(_d061().split())
    assert "sha256, pinned by Hammunition" in body
    assert "MD5 from the publisher's object metadata; not pinned by Hammunition" in body
    assert "pins: []" in body, "D-061 must say no tile is pinned yet"


def test_d057_points_forward_to_d061() -> None:
    text = DECISIONS.read_text()
    d057 = text[text.index("## D-057") : text.index("## D-059")]
    assert "D-061" in d057


def test_the_guide_has_the_new_sections() -> None:
    text = GUIDE.read_text()
    for heading in (
        "## 9. Trails and terrain: QMapShack",
        "## 10. Find an address in Navit, walk it in QMapShack",
        "## 11. Your position in QMapShack: the GPS tether",
        "## 12. Other setups",
        "## What QMapShack does not do (yet)",
    ):
        assert heading in text, heading
    assert "**D-061**" in text
    assert f"{HOST}" in text and f"{PORT}" in text


def test_the_cli_reference_documents_both_maps_verbs() -> None:
    qms = _cli_section("`hammunition maps qmapshack")
    assert "--configure-only" in qms
    for key in ("mapPath", "demPaths", "Route/routino/paths"):
        assert key in qms, key
    tether = _cli_section("`hammunition maps gps-tether")
    assert f"{HOST} port {PORT}" in " ".join(tether.split())
    for option in ("--gpsd HOST[:PORT]", "--port N", "ssh -L 10110:127.0.0.1:10110"):
        assert option in tether, option


def test_the_other_setups_section_covers_each_setup_and_says_what_is_measured() -> None:
    text = GUIDE.read_text()
    start, end = text.index("## 12. Other setups"), text.index("## What QMapShack does not do")
    section = " ".join(text[start:end].split())
    for words in (
        "--gpsd",
        "--port",
        "ssh -N -L 12947:127.0.0.1:2947",
        "A phone as the GPS source",
        "/etc/default/gpsd",
        "/dev/rfcomm0",
        "A rig with built-in GPS",
        "A serial port has one owner",
        "ssh -N -L 10110:127.0.0.1:10110",
        "hammunition hardware park",
        "Navit and xgps need none of this",
        "nc 127.0.0.1 10110",
        "### What is measured, and what is not",
        "**None has been tested here**",
    ):
        assert words in section, words


def test_the_cli_reference_quotes_the_golden_terrain_block() -> None:
    """The excerpt is the golden test's output, line for line."""
    golden = GOLDEN.read_text().splitlines()
    start = next(i for i, line in enumerate(golden) if "Terrain, Copernicus" in line)
    end = next(i for i, line in enumerate(golden) if "of disk for terrain" in line)
    block = "\n".join(golden[start : end + 1])
    assert block in CLI.read_text(), "cli.md's Terrain excerpt no longer matches the golden"


def test_the_doctor_count_is_the_number_of_checks() -> None:
    names = set(re.findall(r'Check\(\s*"([^"]+)"', DOCTOR.read_text()))
    words = {
        14: "Fourteen",
        15: "Fifteen",
        16: "Sixteen",
        17: "Seventeen",
        18: "Eighteen",
        19: "Nineteen",
        20: "Twenty",
        21: "Twenty-one",
        22: "Twenty-two",
        23: "Twenty-three",
        24: "Twenty-four",
        25: "Twenty-five",
        26: "Twenty-six",
        27: "Twenty-seven",
        28: "Twenty-eight",
    }
    assert f"{words[len(names)]} checks across four severities" in CLI.read_text()


def test_readme_and_scope_carry_piece_2() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    assert "| Offline trails and terrain: QMapShack" in readme
    scope = " ".join((REPO_ROOT / "docs" / "SCOPE.md").read_text().split())
    assert "Piece 2 (**D-061**" in scope


def test_no_real_tile_name_in_prose() -> None:
    """A tile name is a latitude and longitude (spec section 5)."""
    for path in (DECISIONS, GUIDE, CLI, REPO_ROOT / "README.md"):
        found = set(TILE.findall(path.read_text())) - SYNTHETIC
        assert not found, f"{path.name} names {sorted(found)}"


MANIFEST = REPO_ROOT / "catalog" / "packages" / "dem-copernicus.yaml"


def test_the_docs_say_what_was_measured_about_the_antimeridian_and_tile_counts() -> None:
    """Final review, I2 (D-018, D-025): Alaska, Fiji, New Zealand and Russia's
    far east were selected through the code with zero refusals, and a state's
    terrain was measured at up to 449 tiles; the docs must say that."""
    sources = {
        "D-061": _d061(),
        "the guide": GUIDE.read_text(),
        "dem-copernicus": MANIFEST.read_text(),
    }
    for name, raw in sources.items():
        text = " ".join(raw.split())
        for stale in (
            "Alaska and Fiji refuse",
            "Alaska's Aleutians, Fiji) are refused",
            "tens of them for a US state",
            "tens for a US state",
            "a little over a GB",
            "A US state is tens of tiles",
        ):
            assert stale not in text, f"{name} still says {stale!r}"
        for measured in ("449 tiles", "17.5 GB", "90 tiles", "3.6 GB", "1,447"):
            assert measured in text, f"{name} does not state {measured!r}"
    d061 = " ".join(_d061().split())
    assert "jumps across ±180 as a single segment" in d061
    guide = GUIDE.read_text()
    planning = guide[guide.index("## Disk planning") :]
    assert "449 tiles" in planning.split("\n---")[0], "the disk planning section lacks the range"


def test_a_region_written_as_rings_meeting_at_180_is_documented_as_selected() -> None:
    guide = " ".join(GUIDE.read_text().split())
    assert "Alaska, Fiji, New Zealand and Russia's far east" in guide
