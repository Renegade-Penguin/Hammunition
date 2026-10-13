# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The three D-075 pins are generated into their manifests; nothing here
reaches the network.  D-075.

Every request goes through an injected ``ask``. Every check runs against a
copy of the real manifest, so the file each generator edits is the one it
will edit for real. The fetched bodies are the synthetic files
``test_infra_sources`` builds in the publishers' layouts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import shutil
import sys
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import test_infra_sources as sources_test
from hammunition.manifest.load import load_catalog, load_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str) -> Any:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


nasr = _load("gen_nasr_pin")
eia = _load("gen_eia860m_pin")
wri = _load("gen_wri_pin")
data_pin = _load("data_pin")


def _copy(tmp_path: Path, unit: str) -> Path:
    copy = tmp_path / f"{unit}.yaml"
    shutil.copyfile(REPO_ROOT / "catalog" / "packages" / f"{unit}.yaml", copy)
    return copy


class Web:
    """URL -> (status, headers, body); every request recorded."""

    def __init__(self, pages: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.pages = pages
        self.asked: list[tuple[str, str]] = []

    def __call__(self, url: str, host: str, *, method: str = "GET", limit: int) -> Any:
        assert url.startswith(host)
        self.asked.append((method, url))
        status, headers, body = self.pages.get(url, (404, {}, b""))
        return data_pin.Answer(status, headers, b"" if method == "HEAD" else body)


def _artifact(path: Path) -> tuple[Any, Any]:
    block = load_manifest(path).install[0].install
    return block, block.artifacts[0]  # type: ignore[union-attr]


def _only_owned_lines_changed(before: str, after: str, owned: int) -> None:
    changed = [
        (a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b
    ]
    assert len(changed) <= owned, changed


# --- the shipped pins ---------------------------------------------------------------


@pytest.mark.parametrize("module", [nasr, eia, wri], ids=["nasr", "eia", "wri"])
def test_the_shipped_manifest_is_well_formed(
    module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert module.main(["--check", "--offline"]) == 0, capsys.readouterr().out


def test_the_three_units_are_in_no_profile_and_listed_for_a_mirror() -> None:
    from hammunition.manifest.load import load_profiles

    catalog = load_catalog(REPO_ROOT / "catalog" / "packages")
    profiles = load_profiles(REPO_ROOT / "catalog" / "profiles", catalog)
    for unit in ("faa-nasr-airports", "eia-860m", "wri-power-plants"):
        assert unit in catalog
        assert not any(unit in p.packages for p in profiles.values()), unit


# --- NASR -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "cycle"),
    [
        (date(2026, 10, 1), date(2026, 10, 1)),
        (date(2026, 9, 30), date(2026, 9, 3)),
        (date(2026, 10, 28), date(2026, 10, 1)),
        (date(2026, 10, 29), date(2026, 10, 29)),
        (date(2026, 1, 21), date(2025, 12, 25)),
    ],
)
def test_the_airac_cycle_is_counted_from_the_epoch(day: date, cycle: date) -> None:
    assert nasr.cycle_for(day) == cycle


def test_the_cycle_names_its_url_and_licence() -> None:
    assert nasr.url_for(date(2026, 10, 1)) == (
        "https://nfdc.faa.gov/webContent/28DaySub/extra/01_Oct_2026_APT_CSV.zip"
    )
    assert nasr.licence_for(date(2026, 10, 29)) == "FAA NASR 2026-10-29, public domain"


def _apt(tmp_path: Path, cycle: str) -> bytes:
    rows = [r.replace("2026/10/01", cycle) for r in sources_test.NASR_ROWS]
    return sources_test._nasr(tmp_path, rows).read_bytes()


def test_regenerating_nasr_writes_the_cycles_five_lines(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "faa-nasr-airports")
    body = _apt(tmp_path, "2026/10/29")
    web = Web({nasr.url_for(date(2026, 10, 29)): (200, {}, body)})
    before = manifest.read_text()
    assert nasr.main([], ask=web, today=date(2026, 11, 2), manifest_path=manifest) == 0
    block, artifact = _artifact(manifest)
    assert artifact.url == nasr.url_for(date(2026, 10, 29))
    assert artifact.sha256 == hashlib.sha256(body).hexdigest() and artifact.size == len(body)
    assert block.licence == "FAA NASR 2026-10-29, public domain"
    assert load_manifest(manifest).version == "2026-10-29"
    _only_owned_lines_changed(before, manifest.read_text(), 5)
    assert nasr.main(["--check", "--offline"], manifest_path=manifest) == 0


def test_a_file_of_another_cycle_is_refused(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "faa-nasr-airports")
    body = _apt(tmp_path, "2026/10/01")
    web = Web({nasr.url_for(date(2026, 10, 29)): (200, {}, body)})
    before = manifest.read_text()
    with pytest.raises(SystemExit, match="not 2026/10/29"):
        nasr.main([], ask=web, today=date(2026, 11, 2), manifest_path=manifest)
    assert manifest.read_text() == before


def test_nasr_check_goes_red_on_a_newer_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _copy(tmp_path, "faa-nasr-airports")
    _, artifact = _artifact(manifest)
    web = Web({artifact.url: (200, {}, b"x" * 0)})
    code = nasr.main(["--check"], ask=web, today=date(2026, 10, 29), manifest_path=manifest)
    out = capsys.readouterr().out
    assert code == 1 and "a newer NASR cycle took effect on 2026-10-29" in out


@pytest.mark.parametrize(
    ("edit", "says"),
    [
        (lambda t: t.replace('version: "2026-10-01"', 'version: "2026-10-02"'), "not an AIRAC"),
        (lambda t: t.replace("01_Oct_2026", "29_Oct_2026"), "not the cycle's"),
        (lambda t: t.replace('licence: "FAA NASR', 'licence: "FAA  NASR'), "licence line"),
    ],
    ids=["off-cycle version", "url of another cycle", "licence line"],
)
def test_nasr_offline_check_is_falsifiable(
    edit: Callable[[str], str], says: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _copy(tmp_path, "faa-nasr-airports")
    manifest.write_text(edit(manifest.read_text()))
    assert nasr.main(["--check", "--offline"], manifest_path=manifest) == 1
    assert says in capsys.readouterr().out


# --- EIA-860M -------------------------------------------------------------------------


XLSX = {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
#: What EIA answers (after a redirect to its landing page) for a month it has not
#: published: a page, status 200 when Akamai lets it through (CI, 2026-10-13).
LANDING = (200, {"content-type": "text/html; charset=UTF-8", "content-length": "9"}, b"<html>")


def _workbook(tmp_path: Path, month: str) -> bytes:
    rows = [[f"Inventory of Operating Generators as of {month}"], *sources_test.EIA_ROWS[1:]]
    return sources_test._xlsx(tmp_path, rows).read_bytes()


def test_regenerating_eia_pins_the_newest_month(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    body = _workbook(tmp_path, "September 2026")
    september = eia.url_for(date(2026, 9, 1))
    web = Web({september: (200, XLSX, body)})
    before = manifest.read_text()
    assert eia.main([], ask=web, today=date(2026, 10, 25), manifest_path=manifest) == 0
    assert ("HEAD", eia.url_for(date(2026, 10, 1))) in web.asked  # tried first
    block, artifact = _artifact(manifest)
    assert artifact.url == september and artifact.size == len(body)
    assert block.licence == (
        "Source: U.S. Energy Information Administration (Sep 2026), public domain"
    )
    assert load_manifest(manifest).version == "2026-09"
    _only_owned_lines_changed(before, manifest.read_text(), 5)


def _eia_before_the_next_release(manifest: Path) -> Web:
    """2026-10-13: August is the newest published month (index: released
    September 24, next October 23). The index links September to December
    anyway; their URLs redirect to a landing page that answers 200."""
    _, artifact = _artifact(manifest)
    pages = {
        eia.url_for(date(2026, m, 1), archive=a): LANDING for m in (9, 10) for a in (False, True)
    }
    pages[artifact.url] = (200, {**XLSX, "content-length": str(artifact.size)}, b"")
    return Web(pages)


def test_a_landing_page_for_an_unpublished_month_is_not_a_newer_month(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    web = _eia_before_the_next_release(manifest)
    code = eia.main(["--check"], ask=web, today=date(2026, 10, 13), manifest_path=manifest)
    assert (code, capsys.readouterr().out) == (0, "eia-860m.yaml is up to date\n")
    assert ("HEAD", eia.url_for(date(2026, 10, 1))) in web.asked  # it was looked for


def test_regenerating_before_the_next_release_keeps_the_newest_real_month(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    web = _eia_before_the_next_release(manifest)
    body = _workbook(tmp_path, "August 2026")
    web.pages[eia.url_for(date(2026, 8, 1))] = (200, XLSX, body)
    assert eia.main([], ask=web, today=date(2026, 10, 13), manifest_path=manifest) == 0
    assert load_manifest(manifest).version == "2026-08"


def test_a_page_is_not_a_workbook_whatever_its_status(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    august = eia.url_for(date(2026, 8, 1))
    for headers in ({}, {"content-type": "text/html"}, {"content-type": "application/pdf"}):
        web = Web({august: (200, headers, b"x")})
        with pytest.raises(SystemExit, match="no EIA-860M workbook answered"):
            eia.main([], ask=web, today=date(2026, 10, 13), manifest_path=manifest)


def test_a_workbook_of_another_month_is_refused(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    web = Web({eia.url_for(date(2026, 9, 1)): (200, XLSX, _workbook(tmp_path, "August 2026"))})
    with pytest.raises(SystemExit, match="not September 2026"):
        eia.main([], ask=web, today=date(2026, 10, 25), manifest_path=manifest)


def test_the_move_to_the_archive_is_named_then_followed_with_the_pin_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    _, artifact = _artifact(manifest)
    august = date(2026, 8, 1)
    pinned = b"the pinned bytes"
    manifest.write_text(
        manifest.read_text()
        .replace(artifact.sha256, hashlib.sha256(pinned).hexdigest())
        .replace(f"size: {artifact.size}", f"size: {len(pinned)}")
    )
    archived = eia.url_for(august, archive=True)
    web = Web(
        {
            archived: (200, {"content-length": str(len(pinned))}, pinned),
            eia.url_for(date(2026, 9, 1)): (200, XLSX, b"september"),
        }
    )
    code = eia.main(["--check"], ask=web, today=date(2026, 10, 25), manifest_path=manifest)
    out = capsys.readouterr().out
    assert code == 1
    assert f"August 2026's workbook moved to {archived}" in out
    assert "EIA-860M for September 2026 is out" in out
    assert eia.main(["--follow-move"], ask=web, manifest_path=manifest) == 0
    _, moved = _artifact(manifest)
    assert moved.url == archived and moved.sha256 == hashlib.sha256(pinned).hexdigest()
    assert eia.main(["--check", "--offline"], manifest_path=manifest) == 0


def test_following_a_move_refuses_different_bytes(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    before = manifest.read_text()
    web = Web({eia.url_for(date(2026, 8, 1), archive=True): (200, {}, b"something else")})
    with pytest.raises(SystemExit, match="not the pinned file"):
        eia.main(["--follow-move"], ask=web, manifest_path=manifest)
    assert manifest.read_text() == before


def test_eia_offline_check_is_falsifiable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _copy(tmp_path, "eia-860m")
    manifest.write_text(manifest.read_text().replace("(Aug 2026)", "(August 2026)"))
    assert eia.main(["--check", "--offline"], manifest_path=manifest) == 1
    assert "exactly" in capsys.readouterr().out


# --- WRI -------------------------------------------------------------------------------


def _wri_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("global_power_plant_database.csv", sources_test.WRI_HEAD + "\n")
        z.writestr(
            "README.txt", "License: Creative Commons Attribution 4.0 International -- CC BY 4.0\n"
        )
    return out.getvalue()


def test_regenerating_wri_checks_the_etag_and_writes_the_md5(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "wri-power-plants")
    body = _wri_zip()
    md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
    web = Web({wri.URL: (200, {"etag": f'"{md5}"'}, body)})
    before = manifest.read_text()
    assert wri.main([], ask=web, manifest_path=manifest) == 0
    _, artifact = _artifact(manifest)
    assert artifact.sha256 == hashlib.sha256(body).hexdigest()
    assert wri.pinned_md5(manifest) == md5
    _only_owned_lines_changed(before, manifest.read_text(), 3)


def test_wri_refuses_bytes_whose_md5_is_not_the_etag(tmp_path: Path) -> None:
    manifest = _copy(tmp_path, "wri-power-plants")
    web = Web({wri.URL: (200, {"etag": '"' + "0" * 32 + '"'}, _wri_zip())})
    before = manifest.read_text()
    with pytest.raises(SystemExit, match="is not the ETag"):
        wri.main([], ask=web, manifest_path=manifest)
    assert manifest.read_text() == before


def test_wri_check_compares_the_etag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _copy(tmp_path, "wri-power-plants")
    _, artifact = _artifact(manifest)
    good = {"etag": f'"{wri.pinned_md5(manifest)}"', "content-length": str(artifact.size)}
    assert wri.main(["--check"], ask=Web({wri.URL: (200, good, b"")}), manifest_path=manifest) == 0
    bad = {**good, "etag": '"' + "1" * 32 + '"'}
    assert wri.main(["--check"], ask=Web({wri.URL: (200, bad, b"")}), manifest_path=manifest) == 1
    assert "frozen in 2021" in capsys.readouterr().out


def test_wri_offline_check_needs_the_md5_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _copy(tmp_path, "wri-power-plants")
    manifest.write_text(manifest.read_text().replace("# publisher MD5", "# publisher's MD5"))
    assert wri.main(["--check", "--offline"], manifest_path=manifest) == 1
    assert "md5 line" in capsys.readouterr().out


# --- the shared rewrite ----------------------------------------------------------------


def test_a_value_that_cannot_be_one_line_is_refused() -> None:
    with pytest.raises(SystemExit, match="one line"):
        data_pin.rewrite('      licence: "x"\n', {"licence": 'a "quoted" b'})


def test_a_line_found_twice_writes_nothing() -> None:
    with pytest.raises(SystemExit, match="matched 2 times"):
        data_pin.rewrite("  size: 1\n  size: 2\n", {"size": "3"})
