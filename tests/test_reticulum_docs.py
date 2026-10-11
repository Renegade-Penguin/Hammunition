# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Reticulum guide and the troubleshooting entries an operator lands on.  D-080.

Docs are a deliverable here, and what an operator hits first is the symptom, so
the symptoms the unit pages and the guide name must have entries that say what
they name. The station is never a real one: placeholders only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hammunition.manifest.load import load_catalog

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "guides" / "mesh-and-reticulum.md"
RUNNING = ROOT / "docs" / "troubleshooting" / "running.md"
TROUBLE_INDEX = ROOT / "docs" / "troubleshooting" / "index.md"

# The exact line rnsd logs when it finds an instance already running (measured
# 2026-10-03, rns 1.5.6). The rns page, the guide and the entry must agree on it.
ANOTHER_INSTANCE = "connected to another shared local instance, this is probably NOT what you want!"

# Anything shaped like a callsign or a grid square that is not a placeholder is a
# station, and a station is never public (N0CALL and FN31pr are the placeholders;
# I2P is the anonymity network, not a callsign).
CALLSIGN = re.compile(r"(?<![A-Za-z0-9_-])[A-Z]{1,2}\d[A-Z]{1,3}(?:-\d{1,2})?(?![A-Za-z0-9_])")
GRID = re.compile(r"(?<![A-Za-z0-9])[A-R]{2}\d\d(?:[a-x]{2})?(?![A-Za-z0-9])")


def _text(path: Path) -> str:
    return path.read_text()


def _flat(text: str) -> str:
    """The text with every run of whitespace, line breaks included, as one space:
    prose is wrapped, and a phrase is not two phrases because it was."""
    return " ".join(text.split())


def test_the_guide_has_its_thirteen_sections_in_order_and_ends_on_what_is_measured() -> None:
    sections = re.findall(r"^## (\d+)\. (.+)$", _text(GUIDE), re.MULTILINE)
    assert [int(number) for number, _ in sections] == list(range(1, 14))
    assert sections[-1][1] == "What is measured, and what is not"


def test_the_guide_and_the_new_pages_name_no_real_station() -> None:
    pages = [GUIDE, RUNNING, ROOT / "docs" / "hardware" / "rnode.md"]
    for page in pages:
        text = _text(page)
        callsigns = set(CALLSIGN.findall(text)) - {"I2P"}
        grids = set(GRID.findall(text)) - {"FN31pr"}
        assert not callsigns, f"{page.name}: callsign-shaped text that is not a placeholder"
        assert not grids, f"{page.name}: grid-square-shaped text that is not a placeholder"
    assert "N0CALL" in _text(GUIDE) and "FN31pr" not in _text(RUNNING)


def test_the_part_97_note_is_a_disclosure_and_never_a_ruling() -> None:
    text = _flat(_text(GUIDE))
    assert "Part 97 forbids messages encoded to obscure their meaning" in text
    assert "not something this guide can tell you is lawful" in text
    assert "as disclosure and not as a ruling" in text
    for ruling in ("is legal", "is lawful on", "you may transmit", "is permitted on"):
        assert ruling not in text


@pytest.mark.parametrize(
    "anchor",
    [
        "reticulum-no-instance",
        "reticulum-another-instance",
        "reticulum-autointerface",
        "rnodeconf-port",
    ],
)
def test_each_symptom_has_an_entry_an_index_line_and_a_link_from_the_guide(anchor: str) -> None:
    assert f'<a name="{anchor}"></a>' in _text(RUNNING), anchor
    assert f"running.md#{anchor}" in _text(TROUBLE_INDEX), anchor
    assert f"troubleshooting/running.md#{anchor}" in _text(GUIDE), f"the guide never links {anchor}"


def test_the_second_instance_symptom_is_quoted_the_same_everywhere() -> None:
    """The log line is what an operator pastes into a search; it is measured, so
    the page, the guide and the entry carry it verbatim."""
    rns = load_catalog(ROOT / "catalog" / "packages")["rns"].documentation.known_problems
    assert rns is not None
    for text in (rns, _text(GUIDE), _text(RUNNING)):
        assert ANOTHER_INSTANCE in _flat(text)


def test_the_guide_says_the_shared_instance_is_a_socket_and_not_a_port() -> None:
    text = _flat(_text(GUIDE))
    assert "It is a local socket, not a TCP port" in text
    assert "@rns/default" in text and "not private to your account" in text


def test_the_guide_does_not_carry_a_list_of_public_entry_points() -> None:
    """Upstream's own manual calls a pasted list of hard-coded entrypoints a
    common mistake, and an address that was up when this was written is not
    evidence that it is now. The one hostname is the manual's own example."""
    hosts = set(re.findall(r"[a-z0-9.-]+\.connect\.reticulum\.network", _text(GUIDE)))
    assert hosts == {"amsterdam.connect.reticulum.network"}


def test_the_guide_lists_what_uninstall_leaves_and_how_to_back_it_up() -> None:
    text = _text(GUIDE)
    for kept in ("~/.reticulum", "~/.nomadnetwork", "~/.lxmd", "~/.rnsh"):
        assert kept in text, kept
    assert "mesh-identities-" in text


def test_the_cli_reference_names_the_user_service_row() -> None:
    cli = _text(ROOT / "docs" / "reference" / "cli.md")
    assert "(`gps-tether`, `rig`, `rns`)" in cli


def test_the_records_carry_the_decision_the_row_and_the_status_line() -> None:
    """D-080 is one decision, in the record, the table the project instructions
    carry and the scope page's Track C, with the licence evidence beside the others."""
    decisions = _text(ROOT / "docs" / "DECISIONS.md")
    assert decisions.count("## D-080 ") == 1
    assert "## D-080 — Reticulum is carried as per-user venvs with one shared instance" in decisions
    assert "(**D-080**)" in _text(ROOT / "CLAUDE.md")
    assert "| Reticulum |" in _text(ROOT / "CLAUDE.md")
    assert "**Status, 2026-10-03 (D-080):**" in _text(ROOT / "docs" / "SCOPE.md")
    licences = _text(ROOT / "docs" / "reference" / "licence-verification.md")
    assert "## Reticulum, LXMF and NomadNet — the Reticulum License" in licences
    for unit in ("`rns` 1.5.6", "`lxmf` 1.2.0", "`nomadnet` 1.4.4"):
        assert unit in licences, unit


def test_the_last_section_records_the_container_run_and_what_it_did_not_cover() -> None:
    """Section 13 is the page's honesty: what the Debian 13 container run measured,
    and what that run could not reach (a radio, an internet hub, a terminal)."""
    raw = _text(GUIDE)
    assert "TASK-11-REPLACE" not in raw
    text = _flat(raw)
    section = text[text.index("## 13. What is measured, and what is not") :]
    for measured in (
        "Debian 13 containers",
        "Peers : 1 reachable",
        "Probe responder at",
        "Valid reply",
        "was delivered and received",
        "Initiator identified",
        "Could not get RNS status",
        "did not exist",
        "29716",
        "29717",
        "42671",
    ):
        assert measured in section, measured
    for not_measured in ("No LoRa link", "NomadNet's text interface", "Any internet link"):
        assert not_measured in section, not_measured
    assert "not yet" not in section  # the pre-container wording is gone


def test_no_record_calls_the_socket_finding_provisional() -> None:
    """The socket and port reading was repeated in a container; none of the places
    that state it still hedges it, and each names the container."""
    claude = _text(ROOT / "CLAUDE.md")
    row = next(line for line in claude.splitlines() if line.startswith("| Reticulum |"))
    assert "Debian 13 container" in row
    assert "Debian 13 container" in _text(ROOT / "catalog" / "packages" / "rns.yaml")
    decisions = _text(ROOT / "docs" / "DECISIONS.md")
    d080 = decisions[decisions.index("## D-080 ") :]
    assert "stays provisional" not in d080
    assert "Debian 13 container" in d080


def test_the_shared_instance_is_per_machine_in_the_record_the_row_and_the_fragment() -> None:
    """The instance is an abstract Unix socket, machine-wide: a second account's
    rnsd attached to the first's (Debian 13 container, 2026-10-03). 'Per operator'
    was wrong, and it was wrong in the authoritative record."""
    fragment = ROOT / "changelog.d" / "reticulum-core.added.md"
    files = [
        ROOT / "docs" / "DECISIONS.md",
        ROOT / "CLAUDE.md",
        # A release assembles the fragment into CHANGELOG.md; then the wording is
        # held in the v0.21.0 section instead.
        fragment if fragment.exists() else ROOT / "CHANGELOG.md",
    ]
    for path in files:
        text = _flat(_text(path))
        if path.name == "CHANGELOG.md":
            text = text[text.index("## v0.21.0") : text.index("## v0.20.0")]
        if path.name == "DECISIONS.md":
            text = text[
                text.index("## D-080 ") : text.index("## D-081 ") if "## D-081 " in text else None
            ]
        assert not re.search(r"shared instance per operator", text, re.IGNORECASE), path.name
        assert not re.search(r"per operator[^.]{0,40}shared instance", text, re.IGNORECASE), (
            path.name
        )
        assert "per machine" in text, path.name
    decision = _flat(_text(files[0]))
    assert "every console script `rns` 1.5.6 declares (14)" in decision


def test_the_guide_and_the_page_say_an_old_pip_or_pipx_reticulum_blocks_the_install() -> None:
    """D-022, #288: the plan refuses by name a ~/.local/bin command the engine did
    not write; the docs must say that, and must not say it overwrites."""
    guide = _flat(_text(GUIDE))
    for needle in (
        "installed Reticulum with pip or pipx",
        "plan stops",
        "never replaces or writes through",
        "pipx uninstall rns",
        "python3 -m pip uninstall rns",
        "does not bring the old one back",
    ):
        assert needle in guide, needle
    rns = load_catalog(ROOT / "catalog" / "packages")["rns"].documentation.known_problems
    assert rns is not None
    flat = _flat(rns)
    assert "blocks the install, by name" in flat and "pipx uninstall rns" in flat
    assert "does not bring the old one back" in flat
    assert "is overwritten" not in flat and "is overwritten" not in guide


def test_nothing_is_claimed_not_to_transmit_on_a_network_and_the_autointerface_can_be_scoped() -> (
    None
):
    rns = load_catalog(ROOT / "catalog" / "packages")["rns"].documentation.prerequisites
    assert rns is not None
    flat = _flat(rns)
    assert "Nothing here transmits" not in flat
    assert "no radio transmits until you attach and configure one" in flat.lower()
    assert "announces on every link-local interface from the first start" in flat
    guide = _flat(_text(GUIDE)).replace("No radio transmits", "no radio transmits")
    for needle in (
        "no radio transmits until you attach and configure one",
        "announces on every link-local interface from the first start",
        "`devices`",
        "`ignored_devices`",
        "`enabled = No`",
        "installed `AutoInterface.py` source (`devices`, `ignored_devices`)",
    ):
        assert needle in guide, needle
