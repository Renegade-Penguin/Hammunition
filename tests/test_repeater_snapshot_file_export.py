# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Repeater snapshots read from a ``file://`` Bunker export (#394).

The export is read with :class:`MirrorTransport`'s confinement: ``safe_relative``,
an ``O_NOFOLLOW`` walk, regular files only, size-bounded. Under ``--offline`` the
publisher is never asked; the refusal names the input that was missing.
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hammunition import repeater_sources
from hammunition.repeater_sources import SNAPSHOT_UNIT, Snapshot
from hammunition.repeaters import RepeaterFetchError, RepeaterInputError

BODY = b"id,callsign\n1,N0TST\n"


def _snapshot() -> Snapshot:
    return repeater_sources.snapshots()[0]


def _export(tmp_path: Path, snapshot: Snapshot, body: bytes | None = BODY) -> Path:
    root = tmp_path / "export"
    (root / SNAPSHOT_UNIT).mkdir(parents=True)
    if body is not None:
        (root / SNAPSHOT_UNIT / snapshot.name).write_bytes(body)
    return root


def _parse(body: bytes, url: str) -> Any:
    return ("parsed", body)


class _Publisher:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, url: str, *, limit: int) -> tuple[bytes, str, datetime]:
        self.asked.append(url)
        raise RepeaterFetchError("publisher unreachable")


def _refused_offline(mirror: str, snapshot: Snapshot, publisher: _Publisher) -> str:
    with pytest.raises(RepeaterFetchError) as refused:
        repeater_sources.read_snapshot(
            snapshot, _parse, mirror=mirror, fetch=publisher, offline=True
        )
    assert publisher.asked == []  # no silent fallback
    return str(refused.value)


def test_a_file_export_with_a_snapshot_is_served_from_the_export(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot)
    publisher = _Publisher()
    for offline in (False, True):
        read = repeater_sources.read_snapshot(
            snapshot, _parse, mirror=root.as_uri(), fetch=publisher, offline=offline
        )
        assert read.source == "mirror"
        parsed: Any = read.parsed
        assert parsed == ("parsed", BODY)
        assert read.mirror_failure is None
        assert read.where.endswith(f"{SNAPSHOT_UNIT}/{snapshot.name}")
        assert read.sha256 == hashlib.sha256(BODY).hexdigest()
    assert publisher.asked == []


def test_a_file_export_without_the_snapshot_is_refused_offline_naming_it(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot, body=None)
    text = _refused_offline(root.as_uri(), snapshot, _Publisher())
    assert f"{SNAPSHOT_UNIT}/{snapshot.name}" in text
    assert "not asked under --offline" in text


def test_a_file_export_without_the_snapshot_online_falls_back_and_says_why(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot, body=None)
    publisher = _Publisher()
    with pytest.raises(RepeaterFetchError, match="the LAN mirror had failed first"):
        repeater_sources.read_snapshot(snapshot, _parse, mirror=root.as_uri(), fetch=publisher)
    assert publisher.asked == [snapshot.url]


def test_an_export_snapshot_that_does_not_parse_is_refused_offline(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot)

    def bad(body: bytes, url: str) -> Any:
        raise RepeaterInputError("not a repeater list")

    publisher = _Publisher()
    with pytest.raises(RepeaterFetchError, match="not a repeater list"):
        repeater_sources.read_snapshot(
            snapshot, bad, mirror=root.as_uri(), fetch=publisher, offline=True
        )
    assert publisher.asked == []


# -- hostile exports ------------------------------------------------------------


def test_a_symlinked_snapshot_file_is_refused(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot, body=None)
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    (root / SNAPSHOT_UNIT / snapshot.name).symlink_to(outside)
    assert "symlinked path components" in _refused_offline(root.as_uri(), snapshot, _Publisher())


def test_a_symlinked_unit_directory_is_refused(tmp_path: Path) -> None:
    snapshot = _snapshot()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / snapshot.name).write_bytes(b"secret")
    root = tmp_path / "export"
    root.mkdir()
    (root / SNAPSHOT_UNIT).symlink_to(outside, target_is_directory=True)
    assert "symlinked path components" in _refused_offline(root.as_uri(), snapshot, _Publisher())


def test_a_symlinked_component_of_the_export_base_is_refused(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot)
    (tmp_path / "alias").symlink_to(root, target_is_directory=True)
    text = _refused_offline((tmp_path / "alias").as_uri(), snapshot, _Publisher())
    assert "symlinked path components" in text


@pytest.mark.parametrize("name", ["../etcc.csv", "/etc/passwd", "a/../../b", ".."])
def test_a_snapshot_name_that_leaves_the_unit_is_refused(tmp_path: Path, name: str) -> None:
    snapshot = dataclasses.replace(_snapshot(), name=name)
    root = _export(tmp_path, _snapshot())
    (tmp_path / "etcc.csv").write_bytes(b"outside the export")
    publisher = _Publisher()
    text = _refused_offline(root.as_uri(), snapshot, publisher)
    assert "unsafe relative path" in text


def test_a_fifo_snapshot_is_refused_without_blocking(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot, body=None)
    fifo = root / SNAPSHOT_UNIT / snapshot.name
    os.mkfifo(fifo)
    outcome: list[object] = []

    def attempt() -> None:
        try:
            outcome.append(_refused_offline(root.as_uri(), snapshot, _Publisher()))
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=5)
    if worker.is_alive():
        try:  # release a reader stuck in a blocking open
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError as exc:
            assert exc.errno == errno.ENXIO
        worker.join(timeout=5)
        pytest.fail("reading a FIFO snapshot blocked")
    assert outcome and isinstance(outcome[0], str), outcome
    assert "not a regular file" in outcome[0]


def test_a_directory_in_place_of_the_snapshot_is_refused(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot, body=None)
    (root / SNAPSHOT_UNIT / snapshot.name).mkdir()
    text = _refused_offline(root.as_uri(), snapshot, _Publisher())
    assert text  # refused, named; never read as a list


def test_an_oversized_snapshot_is_refused_at_its_limit(tmp_path: Path) -> None:
    snapshot = dataclasses.replace(_snapshot(), limit=16)
    root = _export(tmp_path, snapshot, body=b"x" * 17)
    assert "larger than 16 bytes" in _refused_offline(root.as_uri(), snapshot, _Publisher())
    # exactly at the limit is accepted
    (root / SNAPSHOT_UNIT / snapshot.name).write_bytes(b"x" * 16)
    read = repeater_sources.read_snapshot(
        snapshot, _parse, mirror=root.as_uri(), fetch=_Publisher(), offline=True
    )
    assert read.source == "mirror"


def test_the_export_read_stamps_utc_time(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = _export(tmp_path, snapshot)
    read = repeater_sources.read_snapshot(
        snapshot, _parse, mirror=root.as_uri(), fetch=_Publisher()
    )
    assert read.when.tzinfo is not None and read.when.utcoffset() == UTC.utcoffset(None)


def test_a_refusal_names_the_input_and_the_export_not_its_location(tmp_path: Path) -> None:
    snapshot = _snapshot()
    root = tmp_path / "removable-media" / "bunker-export"
    (root / SNAPSHOT_UNIT).mkdir(parents=True)
    text = _refused_offline(root.as_uri(), snapshot, _Publisher())
    assert f"{SNAPSHOT_UNIT}/{snapshot.name}" in text
    assert "bunker-export" in text
    assert str(tmp_path) not in text and "removable-media" not in text
    # the same for a read that works: `where` carries no location either
    (root / SNAPSHOT_UNIT / snapshot.name).write_bytes(BODY)
    read = repeater_sources.read_snapshot(
        snapshot, _parse, mirror=root.as_uri(), fetch=_Publisher()
    )
    assert str(tmp_path) not in read.where and "bunker-export" in read.where


NESTED = b"[" * 10_000 + b"]" * 10_000


def test_a_deeply_nested_brandmeister_export_is_a_typed_input_error() -> None:
    with pytest.raises(RepeaterInputError, match="not Brandmeister's device list"):
        repeater_sources.parse_brandmeister(NESTED, "x")


def test_a_deeply_nested_open_repeater_file_is_a_typed_input_error(tmp_path: Path) -> None:
    path = tmp_path / "open-repeater.json"
    path.write_bytes(NESTED)
    with pytest.raises(RepeaterInputError):
        repeater_sources.read_open_repeater(path)


def test_a_deeply_nested_hearham_export_is_a_typed_input_error(tmp_path: Path) -> None:
    from hammunition import repeaters

    path = tmp_path / "hearham.json"
    path.write_bytes(NESTED)
    with pytest.raises(RepeaterInputError):
        repeaters.read_input(path)
