# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811
"""Kiwix books and CoMaps maps resolve offline from a verified Bunker.  #381, Task 11.

The Bunker's record is only ever compared with the repository's own pins (a
book's size and sha256, a map's size and publisher SHA-1); the bytes are verified
by the fetch against those pins, never against the record. Pins here are the
repository's real ones (the ham Stack Exchange book, the Monaco map); nothing
touches the operator's station config.
"""

from __future__ import annotations

import importlib
import shutil
import socket
from pathlib import Path
from typing import Any, ClassVar

import pytest

from bunker_fixtures import artifact, make_context
from hammunition.backends.base import Action
from hammunition.backends.comaps_maps import (
    ComapsMapsBackend,
    map_dest,
    mirror_path,
    require_map,
    resolve_station_maps,
)
from hammunition.backends.data import human_size
from hammunition.backends.kiwix import (
    KiwixBooksBackend,
    book_mirror_path,
    require_book,
    resolve_station_books,
)
from hammunition.comaps import ComapsError, MapFile, load_pins, resolve_regions, sha1_hex
from hammunition.fetch import Fetcher
from hammunition.kiwix import BookFile, KiwixError, load_book_list, load_pin_file, resolve_books
from hammunition.manifest.load import load_catalog
from hammunition.manifest.schema import KiwixBooksInstall, MwmRegionsInstall
from hammunition.resolution import CatalogueMiss, ResolutionContext
from hammunition.retry import RetryPolicy, retrying_head
from json_support import parse_one
from test_offline_context import apt_state, enrol_file_bunker, machine, run  # noqa: F401
from test_offline_usgs import SpyChecks

cli = importlib.import_module("hammunition.cli.main")
CATALOG = Path(__file__).resolve().parents[1] / "catalog"
BOOK_UNIT = "kiwix-library"
MAP_UNIT = "comaps-maps"
HAM = "ham.stackexchange.com_en_all"
DSP = "dsp.stackexchange.com_en_all"
MONACO = "europe/monaco"
ANDORRA = "europe/andorra"


def books(*ids: str) -> list[BookFile]:
    return resolve_books(list(ids), load_book_list(CATALOG), load_pin_file(CATALOG))


def maps(*regions: str) -> list[MapFile]:
    files, unmapped = resolve_regions(list(regions), load_pins(CATALOG))
    assert unmapped == []
    return files


def book_row(book: BookFile, **changes: Any) -> dict[str, object]:
    values: dict[str, Any] = dict(
        sha256=book.pin.sha256, size=book.pin.size, publisher_url=book.pin.url
    )
    values.update(changes)
    return artifact(BOOK_UNIT, book_mirror_path(BOOK_UNIT, book).name, b"unused", **values)


def map_row(f: MapFile, *, name: str | None = None, **changes: Any) -> dict[str, object]:
    values: dict[str, Any] = dict(
        size=f.pin.size,
        publisher_check="sha1-publisher",
        publisher_digest=sha1_hex(f.pin.sha1),
        publisher_url=f.url,
    )
    values.update(changes)
    return artifact(MAP_UNIT, name or mirror_path(MAP_UNIT, f).name, b"unused", **values)


class Down:
    """A publisher that never answers; counts the asks."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, url: str) -> Any:
        self.asked.append(url)
        raise TimeoutError("publisher down")


class Forbidden:
    """Any ask of the publisher is the failure."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, url: str) -> Any:
        self.asked.append(url)
        raise AssertionError("offline run asked a publisher")


def retrying(head: Any) -> Any:
    return retrying_head(head, RetryPolicy(sleep=lambda _: None, notify=lambda _: None))


def sparse(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


def resolve_books_offline(
    tmp_path: Path, context: ResolutionContext, *ids: str, **extra: Any
) -> list[BookFile]:
    kwargs: dict[str, Any] = dict(
        installed=tmp_path / "books", head=Forbidden(), context=context, unit=BOOK_UNIT
    )
    kwargs.update(extra)
    return resolve_station_books(list(ids), CATALOG, **kwargs)


def resolve_maps_offline(
    tmp_path: Path, context: ResolutionContext, *regions: str, **extra: Any
) -> tuple[list[MapFile], list[str]]:
    kwargs: dict[str, Any] = dict(
        installed=tmp_path / "maps", head=Forbidden(), context=context, unit=MAP_UNIT
    )
    kwargs.update(extra)
    return resolve_station_maps(list(regions), CATALOG, **kwargs)


# -- Kiwix ---------------------------------------------------------------------------


def test_pinned_book_does_not_head_publisher(tmp_path: Path) -> None:
    (book,) = books(HAM)
    context = make_context(tmp_path / "ctx", [book_row(book)])
    head = Forbidden()
    got = resolve_books_offline(tmp_path, context, HAM, head=head)
    assert got == [book]
    assert head.asked == []
    assert (BOOK_UNIT, HAM) in context.notes
    assert context.notes[(BOOK_UNIT, HAM)].startswith("offline; resolved from Bunker bunker")


def test_a_book_record_of_another_size_is_refused_before_its_digest(tmp_path: Path) -> None:
    (book,) = books(HAM)
    context = make_context(
        tmp_path / "ctx", [book_row(book, size=book.pin.size + 1, sha256="0" * 64)]
    )
    with pytest.raises(CatalogueMiss, match="expected size"):
        require_book(book, BOOK_UNIT, context)
    with pytest.raises(CatalogueMiss, match="expected size"):
        resolve_books_offline(tmp_path, context, HAM)


def test_a_book_record_of_another_digest_is_refused_not_trusted(tmp_path: Path) -> None:
    """A record that is self-consistent but is not the repository's pin."""
    (book,) = books(HAM)
    context = make_context(tmp_path / "ctx", [book_row(book, sha256="0" * 64)])
    with pytest.raises(CatalogueMiss, match="sha256 pin"):
        require_book(book, BOOK_UNIT, context)
    with pytest.raises(CatalogueMiss, match="sha256 pin"):
        resolve_books_offline(tmp_path, context, HAM)


def test_a_missing_book_is_a_miss_by_name(tmp_path: Path) -> None:
    with pytest.raises(CatalogueMiss, match=f"{BOOK_UNIT}/{HAM}: not on Bunker"):
        resolve_books_offline(tmp_path, make_context(tmp_path / "ctx", []), HAM)
    with pytest.raises(CatalogueMiss, match="no Bunker enrolled"):
        resolve_books_offline(tmp_path, ResolutionContext(offline=True), HAM)


def test_a_book_record_that_is_not_current_is_a_miss(tmp_path: Path) -> None:
    (book,) = books(HAM)
    context = make_context(
        tmp_path / "ctx", [book_row(book, status="stale", reason="gone upstream")]
    )
    with pytest.raises(CatalogueMiss, match="stale"):
        resolve_books_offline(tmp_path, context, HAM)


def test_one_missing_book_defers_the_whole_selection(tmp_path: Path) -> None:
    ham, _dsp = books(HAM, DSP)
    context = make_context(tmp_path / "ctx", [book_row(ham)])
    with pytest.raises(CatalogueMiss, match=DSP):
        resolve_books_offline(tmp_path, context, HAM, DSP)


def test_a_context_without_a_unit_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unit"):
        resolve_station_books(
            [HAM], CATALOG, installed=tmp_path, head=Forbidden(),
            context=make_context(tmp_path / "ctx", []),
        )  # fmt: skip


def test_an_installed_book_needs_no_record_no_probe_and_no_recheck_offline(tmp_path: Path) -> None:
    (book,) = books(HAM)
    installed = tmp_path / "books"
    sparse(installed / book.pin.file, book.pin.size)
    context = make_context(tmp_path / "ctx", [])
    head, checks = Forbidden(), SpyChecks()
    got = resolve_books_offline(tmp_path, context, HAM, head=head, checks=checks)
    assert got == [book]
    assert head.asked == [] and checks.asked == [] and not context.notes


def test_online_the_installed_book_recheck_is_still_asked(tmp_path: Path) -> None:
    (book,) = books(HAM)
    sparse(tmp_path / "books" / book.pin.file, book.pin.size)
    context = make_context(tmp_path / "ctx", [], offline=False)
    checks = SpyChecks()
    resolve_books_offline(tmp_path, context, HAM, head=lambda url: 200, checks=checks)
    assert checks.asked == [HAM]


def test_online_a_404_is_an_answer_and_never_falls_back(tmp_path: Path) -> None:
    (book,) = books(HAM)
    context = make_context(tmp_path / "ctx", [book_row(book)], offline=False)
    with pytest.raises(KiwixError, match="HTTP 404"):
        resolve_books_offline(tmp_path, context, HAM, head=lambda url: 404)
    assert not context.notes


def test_online_a_live_200_does_not_consult_the_record(tmp_path: Path) -> None:
    (book,) = books(HAM)
    context = make_context(tmp_path / "ctx", [], offline=False)
    assert resolve_books_offline(tmp_path, context, HAM, head=lambda url: 200) == [book]
    assert not context.notes


def test_exhausted_retries_fall_back_to_the_record_and_say_so(tmp_path: Path) -> None:
    (book,) = books(HAM)
    context = make_context(tmp_path / "ctx", [book_row(book)], offline=False)
    down = Down()
    got = resolve_books_offline(tmp_path, context, HAM, head=retrying(down))
    assert got == [book] and down.asked
    assert context.notes[(BOOK_UNIT, HAM)].startswith("publisher unreachable; resolved from Bunker")


def test_exhausted_retries_with_a_verified_bunker_lacking_the_book_defer_the_unit(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path / "ctx", [], offline=False)
    seen: list[str] = []
    with pytest.raises(CatalogueMiss, match="not answering right now"):
        resolve_books_offline(
            tmp_path, context, HAM, head=retrying(Down()),
            on_outage=lambda name, exc: seen.append(name),
        )  # fmt: skip
    assert seen == []


def test_an_outage_without_a_bunker_is_still_a_per_book_deferral(tmp_path: Path) -> None:
    seen: list[str] = []
    got = resolve_station_books(
        [HAM], CATALOG, installed=tmp_path, head=retrying(Down()),
        on_outage=lambda name, exc: seen.append(name),
        context=ResolutionContext(), unit=BOOK_UNIT,
    )  # fmt: skip
    assert got == [] and seen == [HAM]


def test_a_recorded_book_in_a_group_bunker_is_this_operators_only(tmp_path: Path) -> None:
    (book,) = books(HAM)
    row = book_row(book, share="owner:laptop-a")
    mine = make_context(tmp_path / "a", [row], mode="group", enrolment_id="laptop-a")
    assert resolve_books_offline(tmp_path, mine, HAM) == [book]
    theirs = make_context(tmp_path / "b", [row], mode="group", enrolment_id="laptop-b")
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        resolve_books_offline(tmp_path, theirs, HAM)


def test_the_book_fetch_line_carries_the_provenance(tmp_path: Path) -> None:
    (book,) = books(HAM)
    manifest = load_catalog(CATALOG / "packages")[BOOK_UNIT]
    install = manifest.install[0].install
    assert isinstance(install, KiwixBooksInstall)
    note = "offline; resolved from Bunker bunker (K), recorded 2026-10-07T12:00:00Z"

    def fetch_line(provenance: dict[tuple[str, str], str]) -> str:
        backend = KiwixBooksBackend(
            fetcher=Fetcher(cache_dir=tmp_path / "cache"),
            prefix=tmp_path / "prefix",
            files=[book],
            provenance=provenance,
        )
        return next(
            a.description
            for a in backend.steps(manifest, install)
            if isinstance(a, Action) and a.kind == "fetch"
        )

    plain = fetch_line({})
    assert human_size(book.pin.size) in plain and book.book.licence in plain
    assert fetch_line({(BOOK_UNIT, HAM): note}) == f"{plain}; {note}"


# -- CoMaps --------------------------------------------------------------------------


def test_a_pinned_map_does_not_head_the_cdn(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [map_row(monaco)])
    head = Forbidden()
    files, notes = resolve_maps_offline(tmp_path, context, MONACO, head=head)
    assert files == [monaco] and notes == [] and head.asked == []
    name = f"{monaco.version}/{monaco.file}"
    assert name == mirror_path(MAP_UNIT, monaco).name
    assert context.notes[(MAP_UNIT, name)].startswith("offline; resolved from Bunker bunker")


def test_the_map_record_is_named_by_version_and_id(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    bare = artifact(
        MAP_UNIT, monaco.file, b"x", size=monaco.pin.size, publisher_check="sha1-publisher",
        publisher_digest=sha1_hex(monaco.pin.sha1),
    )  # fmt: skip
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        resolve_maps_offline(tmp_path, make_context(tmp_path / "bare", [bare]), MONACO)
    older = map_row(monaco, name=f"{monaco.version - 1}/{monaco.file}")
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        resolve_maps_offline(tmp_path, make_context(tmp_path / "old", [older]), MONACO)


def test_a_map_record_of_another_size_is_refused_before_its_digest(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    row = map_row(monaco, size=monaco.pin.size + 1, publisher_digest="0" * 40)
    context = make_context(tmp_path / "ctx", [row])
    with pytest.raises(CatalogueMiss, match="expected size"):
        require_map(monaco, MAP_UNIT, context)


def test_a_map_record_of_another_sha1_is_refused(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [map_row(monaco, publisher_digest="0" * 40)])
    with pytest.raises(CatalogueMiss, match="publisher digest"):
        require_map(monaco, MAP_UNIT, context)
    with pytest.raises(CatalogueMiss, match="publisher digest"):
        resolve_maps_offline(tmp_path, context, MONACO)


@pytest.mark.parametrize(
    "check", ["sha256", "md5-publisher", "etag-md5", "sha1", "unverified-fetch"]
)
def test_a_map_record_must_say_sha1_publisher(tmp_path: Path, check: str) -> None:
    """SHA-1 stays SHA-1: a record that labels the check anything else is refused
    even when its digest is the right 40 hex digits ("sha1" is not in the
    catalogue's vocabulary; only "sha1-publisher" is written)."""
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [map_row(monaco, publisher_check=check)])
    with pytest.raises(CatalogueMiss, match="SHA-1 publisher check"):
        require_map(monaco, MAP_UNIT, context)


def test_a_map_record_is_not_trusted_for_a_sha256(tmp_path: Path) -> None:
    """The record's sha256 plays no part: a map has only the publisher's SHA-1."""
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [map_row(monaco, sha256="0" * 64)])
    require_map(monaco, MAP_UNIT, context)


def test_a_missing_map_is_a_miss_by_name(tmp_path: Path) -> None:
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        resolve_maps_offline(tmp_path, make_context(tmp_path / "ctx", []), MONACO)
    with pytest.raises(CatalogueMiss, match="no Bunker enrolled"):
        resolve_maps_offline(tmp_path, ResolutionContext(offline=True), MONACO)


def test_one_missing_map_defers_the_whole_selection(tmp_path: Path) -> None:
    monaco, andorra = maps(MONACO, ANDORRA)
    context = make_context(tmp_path / "ctx", [map_row(monaco)])
    with pytest.raises(CatalogueMiss, match=andorra.file):
        resolve_maps_offline(tmp_path, context, MONACO, ANDORRA)


def test_an_unmapped_region_is_still_a_note_not_a_miss(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [map_row(monaco)])
    files, notes = resolve_maps_offline(tmp_path, context, MONACO, "nowhere/at-all")
    assert files == [monaco] and len(notes) == 1 and "nowhere/at-all" in notes[0]


def test_a_context_without_a_unit_is_refused_for_maps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unit"):
        resolve_station_maps(
            [MONACO], CATALOG, installed=tmp_path, head=Forbidden(),
            context=make_context(tmp_path / "ctx", []),
        )  # fmt: skip


def test_an_installed_map_needs_no_record_no_probe_and_no_recheck_offline(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    installed = tmp_path / "maps"
    sparse(map_dest(installed, monaco), monaco.pin.size)
    context = make_context(tmp_path / "ctx", [])
    head, checks = Forbidden(), SpyChecks()
    files, _ = resolve_maps_offline(tmp_path, context, MONACO, head=head, checks=checks)
    assert files == [monaco]
    assert head.asked == [] and checks.asked == [] and not context.notes


def test_online_the_installed_map_recheck_is_still_asked(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    sparse(map_dest(tmp_path / "maps", monaco), monaco.pin.size)
    context = make_context(tmp_path / "ctx", [], offline=False)
    checks = SpyChecks()
    resolve_maps_offline(
        tmp_path, context, MONACO, head=lambda url: (200, monaco.pin.size), checks=checks
    )
    assert checks.asked == [monaco.id]


@pytest.mark.parametrize("answer", [(404, 0), (410, 0), (200, 1234)], ids=["404", "410", "page"])
def test_online_an_expired_pin_is_an_answer_and_never_falls_back(
    tmp_path: Path, answer: tuple[int, int]
) -> None:
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [map_row(monaco)], offline=False)
    with pytest.raises(ComapsError, match="pin expired"):
        resolve_maps_offline(tmp_path, context, MONACO, head=lambda url: answer)
    assert not context.notes


def test_online_a_live_answer_does_not_consult_the_record(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [], offline=False)
    files, _ = resolve_maps_offline(
        tmp_path, context, MONACO, head=lambda url: (200, monaco.pin.size)
    )
    assert files == [monaco] and not context.notes


def test_exhausted_map_retries_fall_back_to_the_record_and_say_so(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    context = make_context(tmp_path / "ctx", [map_row(monaco)], offline=False)
    down = Down()
    files, _ = resolve_maps_offline(tmp_path, context, MONACO, head=retrying(down))
    assert files == [monaco] and down.asked
    note = context.notes[(MAP_UNIT, mirror_path(MAP_UNIT, monaco).name)]
    assert note.startswith("publisher unreachable; resolved from Bunker")


def test_exhausted_map_retries_with_a_verified_bunker_lacking_the_map_defer_the_unit(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path / "ctx", [], offline=False)
    seen: list[str] = []
    with pytest.raises(CatalogueMiss, match="not answering right now"):
        resolve_maps_offline(
            tmp_path, context, MONACO, head=retrying(Down()),
            on_outage=lambda name, exc: seen.append(name),
        )  # fmt: skip
    assert seen == []


def test_a_map_outage_without_a_bunker_is_still_a_per_map_deferral(tmp_path: Path) -> None:
    (monaco,) = maps(MONACO)
    seen: list[str] = []
    files, _ = resolve_station_maps(
        [MONACO], CATALOG, installed=tmp_path, head=retrying(Down()),
        on_outage=lambda name, exc: seen.append(name),
        context=ResolutionContext(), unit=MAP_UNIT,
    )  # fmt: skip
    assert files == [] and seen == [monaco.id]


def test_the_map_fetch_line_carries_the_provenance_and_keeps_the_sha1_disclosure(
    tmp_path: Path,
) -> None:
    (monaco,) = maps(MONACO)
    manifest = load_catalog(CATALOG / "packages")[MAP_UNIT]
    install = manifest.install[0].install
    assert isinstance(install, MwmRegionsInstall)
    name = mirror_path(MAP_UNIT, monaco).name
    note = "offline; resolved from Bunker bunker (K), recorded 2026-10-07T12:00:00Z"

    def steps(provenance: dict[tuple[str, str], str]) -> list[Action]:
        backend = ComapsMapsBackend(
            fetcher=Fetcher(cache_dir=tmp_path / "cache"),
            prefix=tmp_path / "prefix",
            files=[monaco],
            provenance=provenance,
        )
        return [a for a in backend.steps(manifest, install) if isinstance(a, Action)]

    plain = next(a for a in steps({}) if a.kind == "fetch")
    noted = next(a for a in steps({(MAP_UNIT, name): note}) if a.kind == "fetch")
    assert "SHA-1" in plain.detail and "CoMaps" in plain.description
    assert noted.description == f"{plain.description}; {note}"
    assert noted.detail == plain.detail


# -- through the CLI -----------------------------------------------------------------


class ProbeSpy:
    """Stands in for KiwixProbe / CdnProbe: records, and fails, every ask."""

    asked: ClassVar[list[str]] = []

    def head(self, url: str) -> Any:
        type(self).asked.append(url)
        raise AssertionError("offline run asked a publisher")


def enrol(
    tmp_path: Path, rows: list[dict[str, object]], station: dict[str, Any] | None = None
) -> None:
    enrol_file_bunker(
        tmp_path,
        rows,
        station=station
        if station is not None
        else {"map_regions": [MONACO], "reference_books": [HAM]},
    )


def real_rows() -> list[dict[str, object]]:
    (book,) = books(HAM)
    (monaco,) = maps(MONACO)
    return [book_row(book), map_row(monaco)]


def cli_setup(machine: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from hammunition.backends.regions import KeptRegion, MapResolution

    apt_state(monkeypatch, installed="1.0")
    ProbeSpy.asked = []

    def resolved(*args: object, **kwargs: object) -> MapResolution:
        return MapResolution(kept=(KeptRegion(MONACO, "europe-monaco", None, "installed"),))

    monkeypatch.setattr(cli, "resolve_map_regions", resolved)
    monkeypatch.setattr(cli, "KiwixProbe", ProbeSpy)
    monkeypatch.setattr(cli, "CdnProbe", ProbeSpy)
    monkeypatch.setattr(cli.POLICY, "sleep", lambda _: None)
    cli.POLICY.reset()
    return ProbeSpy.asked


def forbid_sockets(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def record(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise OSError("the network is forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", record)
    monkeypatch.setattr(socket, "getaddrinfo", record)
    monkeypatch.setattr(socket, "create_connection", record)
    return attempts


@pytest.mark.parametrize(
    ("unit", "item"), [(BOOK_UNIT, "Fetch reference book"), (MAP_UNIT, "Fetch CoMaps map")]
)
def test_cli_offline_plans_from_the_catalogue_with_zero_sockets(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
    item: str,
) -> None:
    enrol(tmp_path, real_rows())
    asked = cli_setup(machine, monkeypatch)
    attempts = forbid_sockets(monkeypatch)
    rc, out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", "--json", unit)
    assert rc == 0, err
    assert asked == [] and attempts == []
    notes = parse_one(out)["install"]["region_notes"]
    assert any("offline; resolved from Bunker bunker" in n for n in notes), notes
    rc, text, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", unit)
    assert rc == 0, err
    fetch = next(line for line in text.splitlines() if item in line)
    assert "offline; resolved from Bunker bunker" in fetch
    if unit == BOOK_UNIT:
        (book,) = books(HAM)
        assert book.book.licence in fetch and human_size(book.pin.size) in fetch
    else:
        assert "SHA-1 and size from CoMaps' own map index" in fetch


@pytest.mark.parametrize("unit", [BOOK_UNIT, MAP_UNIT])
def test_cli_offline_a_missing_record_refuses_the_typed_unit_by_name(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unit: str,
) -> None:
    enrol(tmp_path, [])
    asked = cli_setup(machine, monkeypatch)
    attempts = forbid_sockets(monkeypatch)
    rc, _out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", unit)
    assert rc == cli.EXIT_UNPLANNABLE, err
    assert f"{unit}: " in err and "not on Bunker bunker" in err
    assert asked == [] and attempts == []


def deferrals(document: dict[str, Any]) -> dict[str, str]:
    return {d["subject"]: d["why"] for d in document["install"]["deferrals"]}


def planned(document: dict[str, Any]) -> set[str]:
    return {p["name"] for p in document["install"]["packages"]}


@pytest.mark.parametrize(
    ("station", "reason"),
    [
        ({}, "no reference books chosen"),
        ({"reference_books": [HAM]}, "not on Bunker bunker"),
    ],
    ids=["no selection", "book missing from the Bunker"],
)
def test_cli_offline_defers_the_data_unit_never_its_reader(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    station: dict[str, Any],
    reason: str,
) -> None:
    enrol(tmp_path, [], station=station)
    asked = cli_setup(machine, monkeypatch)
    attempts = forbid_sockets(monkeypatch)
    rc, out, err = run(capsys, CATALOG, "install", "--offline", "--dry-run", "--json", "reference")
    assert rc == 0, err
    document = parse_one(out)
    assert reason in deferrals(document).get(BOOK_UNIT, ""), deferrals(document)
    assert BOOK_UNIT not in planned(document)
    assert "kiwix-tools" in planned(document) and "kiwix-tools" not in deferrals(document)
    assert asked == [] and attempts == []


def test_cli_offline_a_missing_map_record_defers_the_map_unit_by_name(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A profile naming the map unit beside a reader: the map unit defers, the reader stays."""
    catalog = tmp_path / "real-catalog"
    shutil.copytree(CATALOG, catalog)
    profile = (catalog / "profiles" / "reference.yaml").read_text()
    start, end = profile.index("packages:\n"), profile.index("\ndocumentation:")
    (catalog / "profiles" / "mapref.yaml").write_text(
        profile[:start].replace("name: reference", "name: mapref")
        + "packages:\n  - kiwix-tools\n  - comaps-maps\n"
        + profile[end:]
    )
    enrol(tmp_path, [])
    asked = cli_setup(machine, monkeypatch)
    attempts = forbid_sockets(monkeypatch)
    rc, out, err = run(capsys, catalog, "install", "--offline", "--dry-run", "--json", "mapref")
    assert rc == 0, err
    document = parse_one(out)
    assert "not on Bunker bunker" in deferrals(document).get(MAP_UNIT, ""), deferrals(document)
    assert MAP_UNIT not in planned(document)
    assert "kiwix-tools" in planned(document) and "kiwix-tools" not in deferrals(document)
    assert asked == [] and attempts == []


def test_cli_retries_exhausted_then_the_record_lets_planning_succeed(
    machine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    enrol(tmp_path, real_rows())
    cli_setup(machine, monkeypatch)
    down = Down()

    class DownProbe:
        def head(self, url: str) -> Any:
            return down(url)

    monkeypatch.setattr(cli, "KiwixProbe", DownProbe)
    monkeypatch.setattr(cli, "CdnProbe", DownProbe)
    for unit in (BOOK_UNIT, MAP_UNIT):
        cli.POLICY.reset()
        rc, out, err = run(capsys, CATALOG, "install", "--dry-run", "--json", unit)
        assert rc == 0, err
        notes = parse_one(out)["install"]["region_notes"]
        assert any("publisher unreachable; resolved from Bunker bunker" in n for n in notes)
    assert len(down.asked) == 2 * cli.POLICY.attempts
