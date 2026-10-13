#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate the EIA-860M pin.  D-075.

EIA publishes its monthly generator inventory as one workbook a month,
``https://www.eia.gov/electricity/data/eia860m/xls/<month>_generator<YYYY>.xlsx``
(13,955,142 bytes for August 2026, released 2026-09-24, byte-identical over
two fetches). **When the next month lands, the file moves** to
``.../eia860m/archive/xls/<month>_generator<YYYY>.xlsx`` with the same bytes.

With no arguments this finds the newest month (``xls/`` for this month and
the three before it, newest first, by HEAD), fetches it, checks it is the
workbook of that month (its ``Operating`` sheet's title), and writes its
URL, sha256, size, the month as ``version`` (YYYY-MM) and EIA's own
acknowledgment as the licence line, ``Source: U.S. Energy Information
Administration (Aug 2026), public domain``.

``--follow-move`` keeps the pinned month and rewrites its URL to the
archive, after fetching the archive's copy and checking it has the pinned
sha256: the pin survives the move without moving to a new month.
``--check`` goes red when the pinned URL no longer answers with the pinned
size (naming the move when the archive has it) or when a newer month is
out; ``--check --offline`` checks the manifest's shape only.

    scripts/gen_eia860m_pin.py                    # regenerate (14 MB)
    scripts/gen_eia860m_pin.py --follow-move      # same month, archive URL
    scripts/gen_eia860m_pin.py --check            # HEADs only
    scripts/gen_eia860m_pin.py --check --offline  # the manifest's shape only
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_pin  # noqa: E402
from hammunition.infra import InfraInputError  # noqa: E402
from hammunition.infra_sources import read_eia  # noqa: E402
from hammunition.manifest.load import load_manifest  # noqa: E402
from hammunition.manifest.schema import DataInstall  # noqa: E402

MANIFEST = REPO_ROOT / "catalog" / "packages" / "eia-860m.yaml"
HOST = "https://www.eia.gov/"
BASE = f"{HOST}electricity/data/eia860m/"
INSTALL_AS = "eia860m.xlsx"
LIMIT = 64 * 1024 * 1024
MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip
REGENERATE = "regenerate with scripts/gen_eia860m_pin.py"
OWNED = ("version", "url", "sha256", "size", "licence")

Ask = Callable[..., data_pin.Answer]


def url_for(month: date, *, archive: bool = False) -> str:
    where = "archive/xls" if archive else "xls"
    return f"{BASE}{where}/{MONTHS[month.month - 1]}_generator{month.year}.xlsx"


def licence_for(month: date) -> str:
    """EIA's acknowledgment, with the publication's month: exactly."""
    stamp = f"{MONTHS[month.month - 1][:3].title()} {month.year}"
    return f"Source: U.S. Energy Information Administration ({stamp}), public domain"


def _back(month: date, n: int) -> date:
    index = month.year * 12 + month.month - 1 - n
    return date(index // 12, index % 12 + 1, 1)


def checked_body(body: bytes, month: date) -> tuple[str, int, int]:
    """sha256, size and plant count of *body*, or SystemExit when it is not
    EIA-860M's workbook of *month*. Read by the engine's own reader with an
    everywhere box, so the generator and the import agree on the layout."""
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / INSTALL_AS
        path.write_bytes(body)
        try:
            read = read_eia(path, [(-180.0, -90.0, 180.0, 90.0)])
        except InfraInputError as exc:
            raise SystemExit(f"not EIA-860M's workbook: {exc}. Nothing was written.") from None
    if read.day != month:
        raise SystemExit(
            f"the workbook is of {read.day:%B %Y}, not {month:%B %Y}. Nothing was written."
        )
    return hashlib.sha256(body).hexdigest(), len(body), len(read.points)


def _block(path: Path) -> DataInstall:
    block = load_manifest(path).install[0].install
    if not isinstance(block, DataInstall):
        raise SystemExit(f"{path}: its first install block is not a data block")
    return block


def _pinned_month(version: str) -> date | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})", version)
    if not match or not 1 <= int(match.group(2)) <= 12:
        return None
    return date(int(match.group(1)), int(match.group(2)), 1)


def shape_problems(path: Path) -> list[str]:
    """What is wrong with the manifest, offline."""
    try:
        manifest = load_manifest(path)
        block = _block(path)
    except Exception as exc:  # a schema or YAML error, named
        return [f"{path.name} does not load: {exc}"]
    if len(block.artifacts) != 1:
        return [f"{path.name} must carry exactly one artifact"]
    artifact = block.artifacts[0]
    month = _pinned_month(manifest.version)
    if month is None:
        return [f"version {manifest.version!r} must be the month, YYYY-MM"]
    problems: list[str] = []
    if artifact.url not in (url_for(month), url_for(month, archive=True)):
        problems.append(f"the URL is {artifact.url!r}, not {month:%B %Y}'s, current or archived")
    if block.licence != licence_for(month):
        problems.append(f"the licence line must be exactly {licence_for(month)!r}")
    if artifact.install_as != INSTALL_AS:
        problems.append(f"the artifact must install as {INSTALL_AS}")
    if set(artifact.sha256) == {"0"} or artifact.size <= 1:
        problems.append(f"the pin is a placeholder; {REGENERATE}")
    problems += data_pin.owned_problems(path.read_text(), OWNED, path.name)
    return problems


def _is_workbook(answer: data_pin.Answer) -> bool:
    """A 200 that is a spreadsheet. EIA's index links months it has not
    published (2026-10-13: September to December), and those URLs redirect
    to a landing page that answers 200 text/html when Akamai lets it through
    and 503 when it does not, so status alone made the weekly review go red
    for an October workbook that does not exist."""
    return answer.status == 200 and "spreadsheetml" in answer.headers.get("content-type", "")


def newest(get: Ask, today: date) -> date:
    """The newest month whose ``xls/`` file answers with a workbook, of this
    month and the three before it."""
    for n in range(4):
        month = _back(date(today.year, today.month, 1), n)
        if _is_workbook(get(url_for(month), HOST, method="HEAD", limit=0)):
            return month
    raise SystemExit(f"no EIA-860M workbook answered for the four months to {today:%B %Y}")


def _write(manifest_path: Path, values: dict[str, str]) -> None:
    data_pin.write(manifest_path, data_pin.rewrite(manifest_path.read_text(), values))
    # Verify the effect, not the exit status (D-031): read back what was written.
    block = _block(manifest_path)
    artifact = block.artifacts[0]
    got = {
        "url": artifact.url,
        "sha256": artifact.sha256,
        "size": str(artifact.size),
        "licence": block.licence,
        "version": load_manifest(manifest_path).version,
    }
    if any(got[k] != v for k, v in values.items()):
        raise SystemExit(f"{manifest_path} did not take the new pin; check it by hand")


def main(
    argv: Sequence[str] | None = None,
    *,
    ask: Ask | None = None,
    today: date | None = None,
    manifest_path: Path = MANIFEST,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="check the pin; write nothing")
    parser.add_argument(
        "--offline", action="store_true", help="with --check: the manifest's shape only"
    )
    parser.add_argument(
        "--follow-move",
        action="store_true",
        help="keep the pinned month and point the URL at its archive copy",
    )
    args = parser.parse_args(argv)
    get = ask or data_pin.ask
    now = today or date.today()

    if args.check:
        problems = shape_problems(manifest_path)
        if not problems and not args.offline:
            month = _pinned_month(load_manifest(manifest_path).version)
            assert month is not None  # shape_problems checked it
            artifact = _block(manifest_path).artifacts[0]
            head = get(artifact.url, HOST, method="HEAD", limit=0)
            length = head.headers.get("content-length", "")
            if head.status != 200 or (length and int(length) != artifact.size):
                moved = get(url_for(month, archive=True), HOST, method="HEAD", limit=0)
                if artifact.url == url_for(month) and moved.status == 200:
                    problems.append(
                        f"{month:%B %Y}'s workbook moved to {url_for(month, archive=True)}; "
                        f"scripts/gen_eia860m_pin.py --follow-move keeps the pin at its new "
                        f"address, or {REGENERATE} for the newest month"
                    )
                else:
                    problems.append(
                        f"{artifact.url} answered {head.status}, not 200 with "
                        f"{artifact.size} bytes; {REGENERATE}"
                    )
            latest = newest(get, now)
            if latest > month:
                problems.append(
                    f"EIA-860M for {latest:%B %Y} is out; the pin is {month:%B %Y}. {REGENERATE}"
                )
        if problems:
            print(f"{len(problems)} problem(s):")
            for problem in problems:
                print(f"  {problem}")
            return 1
        print(f"{manifest_path.name} is " + ("well formed" if args.offline else "up to date"))
        return 0

    if args.follow_move:
        month = _pinned_month(load_manifest(manifest_path).version)
        if month is None:
            raise SystemExit(f"{manifest_path.name}'s version is not a month. Nothing was written.")
        pinned = _block(manifest_path).artifacts[0]
        url = url_for(month, archive=True)
        answer = get(url, HOST, limit=LIMIT)
        if answer.status != 200:
            raise SystemExit(f"{url} answered {answer.status}. Nothing was written.")
        sha256 = hashlib.sha256(answer.body).hexdigest()
        if (sha256, len(answer.body)) != (pinned.sha256, pinned.size):
            raise SystemExit(
                f"the archive's copy of {month:%B %Y} is not the pinned file (sha256 "
                f"{sha256[:12]}…, pinned {pinned.sha256[:12]}…); {REGENERATE}. Nothing was written."
            )
        _write(manifest_path, {"url": url})
        print(f"wrote {manifest_path.name}: {month:%B %Y} now at {url}, the pin unchanged")
        return 0

    month = newest(get, now)
    url = url_for(month)
    answer = get(url, HOST, limit=LIMIT)
    if answer.status != 200:
        raise SystemExit(f"{url} answered {answer.status}. Nothing was written.")
    sha256, size, count = checked_body(answer.body, month)
    _write(
        manifest_path,
        {
            "version": f"{month:%Y-%m}",
            "url": url,
            "sha256": sha256,
            "size": str(size),
            "licence": licence_for(month),
        },
    )
    print(
        f"wrote {manifest_path.name}: {month:%B %Y}, {size:,} bytes, {count} operating plants, "
        f"sha256 {sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
