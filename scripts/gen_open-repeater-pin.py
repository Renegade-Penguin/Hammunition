#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate the Open Repeater pin.  D-074.

Fetches Open Repeater's whole list (one URL, about 412 kB, no key) and writes
its sha256, its size and the day measured into
``catalog/packages/open-repeater.yaml``: three lines, each found exactly once,
the manifest re-read afterwards to confirm the write did what it says.

Open Repeater publishes no checksum and the URL carries no date, so the trust
is TLS to www.openrepeater.org at the moment this runs, frozen by the sha256.
**The pin dies on Open Repeater's calendar**: the file changes whenever its
data does. ``--check`` fetches it again and exits 1 naming this script when it
no longer matches; the weekly pin-review CI job runs it. ``--check --offline``
checks the manifest's shape only and runs in the test suite.

    scripts/gen_open-repeater-pin.py                    # regenerate (412 kB)
    scripts/gen_open-repeater-pin.py --check            # fetch and compare
    scripts/gen_open-repeater-pin.py --check --offline  # the manifest's shape only
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hammunition.manifest.load import load_manifest  # noqa: E402
from hammunition.manifest.schema import DataInstall  # noqa: E402

MANIFEST = REPO_ROOT / "catalog" / "packages" / "open-repeater.yaml"
URL = (
    "https://www.openrepeater.org/api/downloads?format=json&country=All+countries"
    "&band=All+bands&mode=All+modes&status=All"
)
HOST = "https://www.openrepeater.org/"
INSTALL_AS = "open-repeater.json"
LICENCE = "CC0 1.0"
LIMIT = 8 * 1024 * 1024
TIMEOUT = 60.0
REGENERATE = "regenerate with scripts/gen_open-repeater-pin.py"

Fetch = Callable[[str], bytes]

_VERSION = re.compile(r'^version: "[^"\n]*"$', re.MULTILINE)
_SHA256 = re.compile(r"^(\s+)sha256: [0-9a-f]{64}$", re.MULTILINE)
_SIZE = re.compile(r"^(\s+)size: \d+$", re.MULTILINE)


def checked_body(body: bytes) -> tuple[str, int, int]:
    """sha256, size and repeater count of *body*, or SystemExit when it is not
    Open Repeater's CC0 list."""
    try:
        data: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if (
        not isinstance(data, dict)
        or data.get("source") != "Open Repeater"
        or not isinstance(data.get("repeaters"), list)
    ):
        raise SystemExit(f"{URL} did not answer with Open Repeater's list. Nothing was written.")
    if data.get("license") != "CC0":
        raise SystemExit(
            f"Open Repeater's file now says license {data.get('license')!r}, not CC0: the "
            f"licence this unit declares no longer holds. Nothing was written."
        )
    return hashlib.sha256(body).hexdigest(), len(body), len(data["repeaters"])


def _block(path: Path) -> DataInstall:
    block = load_manifest(path).install[0].install
    if not isinstance(block, DataInstall):
        raise SystemExit(f"{path}: its first install block is not a data block")
    return block


def shape_problems(path: Path) -> list[str]:
    """What is wrong with the manifest, offline."""
    try:
        manifest = load_manifest(path)
        block = _block(path)
    except Exception as exc:  # a schema or YAML error, named
        return [f"{path.name} does not load: {exc}"]
    problems: list[str] = []
    if len(block.artifacts) != 1:
        problems.append(f"{path.name} must carry exactly one artifact")
        return problems
    artifact = block.artifacts[0]
    if artifact.url != URL:
        problems.append(f"the artifact's URL is {artifact.url!r}, not {URL!r}")
    if artifact.install_as != INSTALL_AS:
        problems.append(f"the artifact must install as {INSTALL_AS}, not {artifact.install_as}")
    if block.licence != LICENCE:
        problems.append(f"the licence must be {LICENCE!r} as Open Repeater states it")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", manifest.version):
        problems.append(f"version {manifest.version!r} must be the day measured, YYYY-MM-DD")
    if set(artifact.sha256) == {"0"} or artifact.size <= 1:
        problems.append(f"the pin is a placeholder; {REGENERATE}")
    text = path.read_text()
    for name, pattern in (("version", _VERSION), ("sha256", _SHA256), ("size", _SIZE)):
        if len(pattern.findall(text)) != 1:
            problems.append(f"{path.name} must hold exactly one {name} line the generator owns")
    return problems


def rewrite(text: str, sha256: str, size: int, day: str) -> str:
    """*text* with the three pinned lines replaced; SystemExit when any is
    not found exactly once (a sed that matched nothing exits 0: D-031)."""
    out = text
    for pattern, replacement in (
        (_VERSION, f'version: "{day}"'),
        (_SHA256, rf"\g<1>sha256: {sha256}"),
        (_SIZE, rf"\g<1>size: {size}"),
    ):
        out, count = pattern.subn(replacement, out)
        if count != 1:
            raise SystemExit(
                f"{pattern.pattern!r} matched {count} lines, not 1. Nothing was written."
            )
    return out


class _HostOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.startswith(HOST):
            raise SystemExit(f"refusing a redirect to {newurl!r}: only {HOST} is asked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def real_fetch(url: str) -> bytes:
    if not url.startswith(HOST):
        raise SystemExit(f"refusing {url!r}: only {HOST} is asked")
    director = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(),
        _HostOnlyRedirects(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPDefaultErrorHandler(),
    ):
        director.add_handler(handler)
    request = urllib.request.Request(url, headers={"User-Agent": "hammunition"})
    try:
        with director.open(request, timeout=TIMEOUT) as response:
            body: bytes = response.read(LIMIT + 1)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        # http.client's own exceptions (a truncated answer) are not OSError.
        raise SystemExit(f"could not fetch {url}: {exc!r}") from None
    if len(body) > LIMIT:
        raise SystemExit(f"{url} answered with more than {LIMIT} bytes; refused")
    return body


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetch | None = None,
    today: date | None = None,
    manifest_path: Path = MANIFEST,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="check the pin; write nothing")
    parser.add_argument(
        "--offline", action="store_true", help="with --check: the manifest's shape only"
    )
    args = parser.parse_args(argv)
    get = fetch or real_fetch

    if args.check:
        problems = shape_problems(manifest_path)
        if not problems and not args.offline:
            block = _block(manifest_path)
            pinned = block.artifacts[0]
            sha256, size, count = checked_body(get(URL))
            if (sha256, size) != (pinned.sha256, pinned.size):
                version = load_manifest(manifest_path).version
                problems.append(
                    f"Open Repeater's file changed since the pin of {version}: now {size} bytes, "
                    f"{count} repeaters, sha256 {sha256[:12]}…; pinned {pinned.size} bytes, "
                    f"{pinned.sha256[:12]}…. The install refuses it until you {REGENERATE}"
                )
        if problems:
            print(f"{len(problems)} problem(s):")
            for problem in problems:
                print(f"  {problem}")
            return 1
        if args.offline:
            print(f"{manifest_path.name} is well formed: one pinned artifact, {LICENCE}")
        else:
            print(f"{manifest_path.name} is up to date: the pin matches what Open Repeater serves")
        return 0

    sha256, size, count = checked_body(get(URL))
    day = (today or date.today()).isoformat()
    text = rewrite(manifest_path.read_text(), sha256, size, day)
    _write(manifest_path, text)
    # Verify the effect, not the exit status (D-031): read back what was written.
    block = _block(manifest_path)
    artifact = block.artifacts[0]
    if (artifact.sha256, artifact.size, load_manifest(manifest_path).version) != (
        sha256,
        size,
        day,
    ):
        raise SystemExit(f"{manifest_path} did not take the new pin; check it by hand")
    print(f"wrote {manifest_path.name}: {size:,} bytes, {count} repeaters, sha256 {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
