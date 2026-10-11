# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``mapsforge-map`` and ``mapsforge-poi`` converters.  D-067.

Public example regions only (Vermont, Delaware). A fake ``java`` on PATH
records its argv and working directory and writes what the real writer's
output starts with; a fake osmosis tree and ``/usr/share/java`` hold empty
jars, so the classpath is resolved as it would be on a machine with
``osmosis`` and ``libmapsforge-java`` installed. Nothing is downloaded: the
pinned jar comes from a fake transport.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import IO, Any

import pytest

from bunker_fixtures import artifact as catalogue_artifact
from bunker_fixtures import make_context
from fake_tools import calls, install_fakes
from hammunition.backends import Action, Command
from hammunition.backends.base import BackendError
from hammunition.backends.mapsforge import (
    MAIN,
    MAP_JARS,
    POI_JARS,
    MapsforgeConverter,
    PhoneLedger,
    classpath,
)
from hammunition.backends.regions import MapLedger
from hammunition.backends.staging import Staging
from hammunition.fetch import Fetcher, mirror_url
from hammunition.geofabrik import RegionFile
from hammunition.manifest.schema import DerivedDataInstall, PackageManifest, RemoteArtifact
from hammunition.payloads import payload_path
from hammunition.resolution import CatalogueMiss
from test_fetch_mirror import Routes

NOT_ROOT = 1000
BODY = b"pbf" * 4
JAR = b"a jar, as far as this test is concerned"
JAR_SHA = hashlib.sha256(JAR).hexdigest()
JAR_URL = (
    "https://repo1.maven.org/maven2/org/mapsforge/mapsforge-poi-writer/0.25.0/"
    "mapsforge-poi-writer-0.25.0-jar-with-dependencies.jar"
)
JAR_NAME = "mapsforge-poi-writer-0.25.0-jar-with-dependencies.jar"


def _region(path: str, snapshot: str = "260101") -> RegionFile:
    return RegionFile(
        path,
        snapshot,
        f"https://download.geofabrik.de/{path}-{snapshot}.osm.pbf",
        len(BODY),
        hashlib.sha256(BODY).hexdigest(),
        None,
    )


VERMONT = _region("north-america/us/vermont")
DELAWARE = _region("north-america/us/delaware")

#: The output file named after ``--<task> file=`` in the fake's argv.
_OUT = 'o=$(echo "$*" | sed -n "s/.*-writer file=\\([^ ]*\\).*/\\1/p")'
JAVA_OK = (
    f'{_OUT}; case "$*" in *--mapfile-writer*) printf "mapsforge binary OSM, v3" > "$o";; '
    f'*) printf "SQLite format 3\\000rest" > "$o";; esac'
)


def manifest(kind: str) -> PackageManifest:
    block: dict[str, Any] = {
        "method": "derived",
        "converter": f"mapsforge-{kind}",
        "source": "osm-regions",
        "licence": "ODbL-1.0",
        "licence_url": "https://www.openstreetmap.org/copyright",
    }
    if kind == "poi":
        block["tool"] = {
            "artifact": {"url": JAR_URL, "sha256": JAR_SHA, "signature_url": f"{JAR_URL}.asc"},
            "size": len(JAR),
            "licence": "LGPL-3.0-only",
            "licence_url": "https://github.com/mapsforge/mapsforge/blob/master/LICENSE",
        }
    return PackageManifest.model_validate(
        {
            "name": f"mapsforge-{kind}",
            "version": "station",
            "summary": "Phone maps for a test",
            "categories": ["navigation-maps"],
            "depends": ["osm-regions", "osmosis", "libmapsforge-java"],
            "install": [{"install": block}],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Phone maps for a test, nothing more.",
                "why_you_want_it": "Because the test suite needs a manifest.",
                "upstream_url": "https://github.com/mapsforge/mapsforge",
            },
        }
    )


class FakeTransport:
    def __init__(self, body: bytes = JAR) -> None:
        self.body = body
        self.requested: list[str] = []

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:
        self.requested.append(url)
        yield BytesIO(self.body)


def _block(m: PackageManifest) -> DerivedDataInstall:
    block = m.install[0].install
    assert isinstance(block, DerivedDataInstall)
    return block


def _data(prefix: Path, unit: str) -> Path:
    return prefix / "share" / "hammunition" / "data" / unit


def _install_region(prefix: Path, region: RegionFile) -> Path:
    out = _data(prefix, "osm-regions")
    out.mkdir(parents=True, exist_ok=True)
    pbf = out / f"{region.slug}.osm.pbf"
    pbf.write_bytes(BODY)
    return pbf


def _jars(root: Path, skip: frozenset[str] = frozenset()) -> tuple[Path, Path]:
    osmosis, java = root / "usr-share-osmosis", root / "usr-share-java"
    osmosis.mkdir(parents=True, exist_ok=True)
    java.mkdir(parents=True, exist_ok=True)
    for name in ("osmosis-core-0.49.2.jar", "osmosis-pbf-0.49.2.jar"):
        (osmosis / name).write_bytes(b"")
    for name in {*MAP_JARS, *POI_JARS} - skip:
        (java / f"{name}.jar").write_bytes(b"")
    return osmosis, java


def _converter(tmp_path: Path, kind: str, files: list[RegionFile], **kw: Any) -> MapsforgeConverter:
    osmosis, java = kw.pop("dirs", None) or _jars(tmp_path)
    fetcher = kw.pop("fetcher", None)
    if fetcher is None:
        fetcher = Fetcher(tmp_path / "cache", transport=kw.pop("transport", FakeTransport()))
    return MapsforgeConverter(
        kind=kind,  # type: ignore[arg-type]
        prefix=tmp_path,
        files=files,
        staging=Staging(tmp_path / "staging" / f"mapsforge-{kind}", euid=NOT_ROOT),
        fetcher=fetcher,
        osmosis_dir=osmosis,
        java_dir=java,
        **kw,
    )


def _actions(steps: list[Action | Command]) -> list[Action]:
    assert all(isinstance(s, Action) for s in steps)
    return [s for s in steps if isinstance(s, Action)]


def _run(conv: MapsforgeConverter) -> list[str]:
    m = manifest(conv.kind)
    return [step.perform() for step in _actions(conv.steps(m, _block(m)))]


def test_the_map_runs_java_with_the_measured_argv_in_its_own_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    pbf = _install_region(tmp_path, VERMONT)
    conv = _converter(tmp_path, "map", [VERMONT])
    outcomes = _run(conv)
    assert conv.ledger.failed == {}, outcomes
    work = tmp_path / "staging" / "mapsforge-map" / f"{VERMONT.slug}.work"
    ((cwd, argv),) = calls(log)
    assert cwd == str(work)
    words = argv.split()
    assert words[:4] == ["java", "-Xmx2g", f"-Djava.io.tmpdir={work}", "-cp"]
    cp = words[4].split(":")
    assert cp[:2] == [
        str(tmp_path / "usr-share-osmosis" / "osmosis-core-0.49.2.jar"),
        str(tmp_path / "usr-share-osmosis" / "osmosis-pbf-0.49.2.jar"),
    ]
    assert cp[2:] == [str(tmp_path / "usr-share-java" / f"{j}.jar") for j in MAP_JARS]
    assert words[5:] == [
        MAIN,
        "-q",
        "--rbf",
        f"file={pbf}",
        "--mapfile-writer",
        f"file={work / (VERMONT.slug + '.map')}",
        "type=hd",
    ]
    out = _data(tmp_path, "mapsforge-map")
    assert (out / f"{VERMONT.slug}.map").read_bytes() == b"mapsforge binary OSM, v3"
    assert (out / f"{VERMONT.slug}.map.source").read_text() == (
        "260101\nconverter: mapsforge-map 1\n"
    )
    assert list(work.iterdir()) == [], "the scratch is cleared after a successful build"


def test_the_map_step_says_what_it_runs_and_what_it_costs(tmp_path: Path) -> None:
    _install_region(tmp_path, VERMONT)
    conv = _converter(tmp_path, "map", [VERMONT])
    m = manifest("map")
    convert, install = _actions(conv.steps(m, _block(m)))
    assert convert.kind == "convert" and install.kind == "install-data"
    assert "north-america/us/vermont (260101)" in convert.description
    assert "--mapfile-writer" in convert.description and "type=hd" in convert.description
    assert "measured on one region" in convert.description
    assert "0.78x" in convert.description and "about 2 GB" in convert.description
    assert install.detail == str(_data(tmp_path, "mapsforge-map") / f"{VERMONT.slug}.map")


def test_the_poi_writer_is_fetched_verified_installed_and_put_last_on_the_classpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    pbf = _install_region(tmp_path, DELAWARE)
    transport = FakeTransport()
    conv = _converter(tmp_path, "poi", [DELAWARE], transport=transport)
    m = manifest("poi")
    steps = _actions(conv.steps(m, _block(m)))
    assert [s.kind for s in steps] == ["fetch", "install-data", "convert", "install-data"]
    assert "sha256, pinned by Hammunition" in steps[0].description
    assert "does not verify" in steps[0].description, "the unverified .asc is said"
    jar = tmp_path / "share" / "hammunition" / "mapsforge-poi" / JAR_NAME
    assert steps[1].detail == str(jar)
    for step in steps:
        step.perform()
    assert transport.requested == [JAR_URL]
    assert jar.read_bytes() == JAR
    work = tmp_path / "staging" / "mapsforge-poi" / f"{DELAWARE.slug}.work"
    ((cwd, argv),) = calls(log)
    assert cwd == str(work)
    words = argv.split()
    assert words[:5] == [
        "java",
        "-Xmx2g",
        f"-Djava.io.tmpdir={work}",
        f"-Dorg.sqlite.tmpdir={work}",
        "-cp",
    ]
    cp = words[5].split(":")
    assert cp[2:-1] == [str(tmp_path / "usr-share-java" / f"{j}.jar") for j in POI_JARS]
    assert cp[-1] == str(jar)
    assert words[6:] == [
        MAIN,
        "-q",
        "--rbf",
        f"file={pbf}",
        "--poi-writer",
        f"file={work / (DELAWARE.slug + '.poi')}",
    ]
    source = _data(tmp_path, "mapsforge-poi") / f"{DELAWARE.slug}.poi.source"
    assert source.read_text() == f"260101\nconverter: mapsforge-poi 1 {JAR_SHA[:12]}\n"


def test_a_writer_that_does_not_hash_to_the_pin_is_refused(tmp_path: Path) -> None:
    _install_region(tmp_path, DELAWARE)
    conv = _converter(tmp_path, "poi", [DELAWARE], transport=FakeTransport(b"something else"))
    m = manifest("poi")
    fetch = _actions(conv.steps(m, _block(m)))[0]
    with pytest.raises(BackendError, match="does not match the digest"):
        fetch.perform()


def test_a_writer_of_the_wrong_declared_size_is_refused_before_install(tmp_path: Path) -> None:
    """The digest matches, so the manifest's ``size`` is wrong: refuse before
    the jar is ever installed under the prefix. Manifests are frozen, so the
    wrong size is a copy of the real block, not a mutation of it."""
    _install_region(tmp_path, DELAWARE)
    conv = _converter(tmp_path, "poi", [DELAWARE])
    m = manifest("poi")
    block = _block(m)
    assert block.tool is not None
    wrong = block.model_copy(update={"tool": block.tool.model_copy(update={"size": len(JAR) + 1})})
    fetch = _actions(conv.steps(m, wrong))[0]
    with pytest.raises(BackendError, match=rf"manifest size {len(JAR) + 1}, got {len(JAR)}"):
        fetch.perform()
    jar = tmp_path / "share" / "hammunition" / "mapsforge-poi" / JAR_NAME
    assert not jar.exists()


# -- the POI writer tries the Bunker mirror first (#381, Task 14) ------------


def test_the_poi_writer_tries_the_mirror_first(tmp_path: Path) -> None:
    pin = RemoteArtifact(url=JAR_URL, sha256=JAR_SHA)
    at = mirror_url("http://bunker.invalid", payload_path("mapsforge-poi", pin))
    routes = Routes({at: JAR})
    fetcher = Fetcher(
        tmp_path / "cache",
        transport=routes,
        mirror_transport=routes,
        mirror="http://bunker.invalid",
    )
    _install_region(tmp_path, DELAWARE)
    conv = _converter(tmp_path, "poi", [DELAWARE], fetcher=fetcher)
    m = manifest("poi")
    fetch = _actions(conv.steps(m, _block(m)))[0]
    assert fetch.sources == (at, JAR_URL)
    fetch.perform()
    assert routes.requested == [at]
    assert fetch.facts["source"] == "mirror"


def test_the_poi_writer_mirror_miss_falls_back_naming_why(tmp_path: Path) -> None:
    pin = RemoteArtifact(url=JAR_URL, sha256=JAR_SHA)
    at = mirror_url("http://bunker.invalid", payload_path("mapsforge-poi", pin))
    routes = Routes({at: b"tampered", JAR_URL: JAR})
    fetcher = Fetcher(
        tmp_path / "cache",
        transport=routes,
        mirror_transport=routes,
        mirror="http://bunker.invalid",
    )
    _install_region(tmp_path, DELAWARE)
    conv = _converter(tmp_path, "poi", [DELAWARE], fetcher=fetcher)
    m = manifest("poi")
    fetch = _actions(conv.steps(m, _block(m)))[0]
    outcome = fetch.perform()
    assert routes.requested == [at, JAR_URL]
    assert fetch.facts["source"] == "publisher"
    assert "the mirror was passed over" in outcome


def test_offline_the_poi_writer_preflight_refuses_before_any_build_step(tmp_path: Path) -> None:
    """The Bunker lacking the pin refuses before ``steps()`` returns anything,
    before a single region has been touched."""
    _install_region(tmp_path, DELAWARE)
    context = make_context(tmp_path / "ctx", [])
    fetcher = Fetcher(
        tmp_path / "cache", transport=Routes({}), offline=True, mirror="http://bunker.invalid"
    )
    conv = _converter(tmp_path, "poi", [DELAWARE], fetcher=fetcher, context=context)
    m = manifest("poi")
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        conv.steps(m, _block(m))


def test_offline_with_no_context_refuses_before_any_build_step(tmp_path: Path) -> None:
    """A context-less offline run must refuse the same way an offline run
    with an empty Bunker does: ``preflight_payloads`` silently does nothing
    with no context, so this backend has to catch it itself."""
    _install_region(tmp_path, DELAWARE)
    fetcher = Fetcher(
        tmp_path / "cache", transport=Routes({}), offline=True, mirror="http://bunker.invalid"
    )
    conv = _converter(tmp_path, "poi", [DELAWARE], fetcher=fetcher)
    m = manifest("poi")
    with pytest.raises(BackendError, match="offline") as exc:
        conv.steps(m, _block(m))
    assert "none. Nothing was planned" in str(exc.value)


def test_a_context_that_disagrees_and_says_online_also_refuses(tmp_path: Path) -> None:
    """The fetcher and the context are built from the same ``offline`` flag
    in the real CLI, but this backend's API does not enforce that: a context
    that exists but says ``offline=False`` while the fetcher says otherwise
    is the same hole as no context at all -- ``preflight_payloads`` treats
    both as "online" and does nothing."""
    _install_region(tmp_path, DELAWARE)
    context = make_context(tmp_path / "ctx", [], offline=False)
    fetcher = Fetcher(
        tmp_path / "cache", transport=Routes({}), offline=True, mirror="http://bunker.invalid"
    )
    conv = _converter(tmp_path, "poi", [DELAWARE], fetcher=fetcher, context=context)
    m = manifest("poi")
    with pytest.raises(BackendError, match="offline") as exc:
        conv.steps(m, _block(m))
    assert "none. Nothing was planned" in str(exc.value)


def test_offline_the_poi_writer_preflight_passes_with_a_bunker_row(tmp_path: Path) -> None:
    _install_region(tmp_path, DELAWARE)
    row = catalogue_artifact("mapsforge-poi", f"{JAR_SHA}/{JAR_NAME}", JAR)
    context = make_context(tmp_path / "ctx", [row])
    fetcher = Fetcher(
        tmp_path / "cache", transport=Routes({}), offline=True, mirror="http://bunker.invalid"
    )
    conv = _converter(tmp_path, "poi", [DELAWARE], fetcher=fetcher, context=context)
    m = manifest("poi")
    steps = conv.steps(m, _block(m))
    assert _actions(steps)
    assert ("mapsforge-poi", f"{JAR_SHA}/{JAR_NAME}") in context.notes


def test_the_map_kind_never_preflights_anything(tmp_path: Path) -> None:
    """The map converter names no ``tool``, so an empty, offline catalogue
    plans it without complaint."""
    _install_region(tmp_path, VERMONT)
    context = make_context(tmp_path / "ctx", [])
    conv = _converter(tmp_path, "map", [VERMONT], context=context)
    m = manifest("map")
    assert _actions(conv.steps(m, _block(m)))
    assert context.notes == {}


def test_current_regions_and_a_current_writer_mean_no_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    _install_region(tmp_path, VERMONT)
    _install_region(tmp_path, DELAWARE)
    for kind in ("map", "poi"):
        conv = _converter(tmp_path, kind, [VERMONT, DELAWARE])
        _run(conv)
        again = _converter(tmp_path, kind, [VERMONT, DELAWARE])
        m = manifest(kind)
        assert again.steps(m, _block(m)) == []
        assert again.pending(m, _block(m)) == []


def test_a_new_snapshot_rebuilds_and_the_writer_is_not_fetched_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    _install_region(tmp_path, DELAWARE)
    _run(_converter(tmp_path, "poi", [DELAWARE]))
    newer = _region("north-america/us/delaware", "260901")
    conv = _converter(tmp_path, "poi", [newer])
    m = manifest("poi")
    assert [s.kind for s in _actions(conv.steps(m, _block(m)))] == ["convert", "install-data"]


def test_a_missing_jar_fails_that_region_by_name_and_the_next_one_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    _install_region(tmp_path, VERMONT)
    _install_region(tmp_path, DELAWARE)
    dirs = _jars(tmp_path, skip=frozenset({"mapsforge-map-writer"}))
    conv = _converter(tmp_path, "map", [VERMONT, DELAWARE], dirs=dirs)
    outcomes = _run(conv)
    assert calls(log) == [], "java never starts on an incomplete classpath"
    assert set(conv.ledger.failed) == {
        f"mapsforge-map:{VERMONT.slug}",
        f"mapsforge-map:{DELAWARE.slug}",
    }
    message = conv.ledger.failed[f"mapsforge-map:{VERMONT.slug}"]
    assert "mapsforge-map-writer.jar" in message and "libmapsforge-java" in message
    assert any("FAILED" in o for o in outcomes)


def test_no_osmosis_jars_at_all_is_a_missing_classpath_too(tmp_path: Path) -> None:
    java = tmp_path / "java"
    java.mkdir()
    for name in MAP_JARS:
        (java / f"{name}.jar").write_bytes(b"")
    _, missing = classpath("map", tmp_path / "no-osmosis", java, None)
    assert missing and "osmosis" in missing[0]


def test_java_exiting_zero_with_the_wrong_format_fails_the_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    liar = f'{_OUT}; case "$*" in *vermont*) echo "Exception in thread main" > "$o";; *) {JAVA_OK};; esac'
    install_fakes(monkeypatch, tmp_path / "bin", {"java": liar})
    _install_region(tmp_path, VERMONT)
    _install_region(tmp_path, DELAWARE)
    conv = _converter(tmp_path, "map", [VERMONT, DELAWARE])
    _run(conv)
    assert list(conv.ledger.failed) == [f"mapsforge-map:{VERMONT.slug}"]
    assert "not a Mapsforge map" in conv.ledger.failed[f"mapsforge-map:{VERMONT.slug}"]
    out = _data(tmp_path, "mapsforge-map")
    assert not (out / f"{VERMONT.slug}.map").exists()
    assert (out / f"{DELAWARE.slug}.map").exists()
    work = tmp_path / "staging" / "mapsforge-map" / f"{VERMONT.slug}.work"
    assert list(work.iterdir()) == [], "a failed build's scratch goes too"


def test_java_failing_names_the_region_and_the_tail_of_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(
        monkeypatch, tmp_path / "bin", {"java": 'echo "java.lang.OutOfMemoryError" >&2; exit 1'}
    )
    _install_region(tmp_path, VERMONT)
    conv = _converter(tmp_path, "map", [VERMONT])
    _run(conv)
    (message,) = conv.ledger.failed.values()
    assert "north-america/us/vermont" in message and "OutOfMemoryError" in message


def test_a_region_piece_one_failed_is_skipped_and_one_not_installed_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    regions = MapLedger()
    regions.fail(VERMONT.slug, "north-america/us/vermont: did not verify")
    conv = _converter(tmp_path, "map", [VERMONT, DELAWARE], regions=regions)
    outcomes = _run(conv)
    assert outcomes[0].startswith("skipped")
    assert calls(log) == []
    assert list(conv.ledger.failed) == [f"mapsforge-map:{DELAWARE.slug}"]


def test_a_region_dropped_from_the_station_is_removed_and_a_kept_one_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    _install_region(tmp_path, VERMONT)
    _install_region(tmp_path, DELAWARE)
    _run(_converter(tmp_path, "map", [VERMONT, DELAWARE]))
    conv = _converter(tmp_path, "map", [DELAWARE])
    m = manifest("map")
    steps = _actions(conv.steps(m, _block(m)))
    assert [s.kind for s in steps] == ["remove-data"]
    steps[0].perform()
    out = _data(tmp_path, "mapsforge-map")
    assert not (out / f"{VERMONT.slug}.map").exists()
    assert not (out / f"{VERMONT.slug}.map.source").exists()
    kept = _converter(tmp_path, "map", [], keep=frozenset({DELAWARE.slug}))
    assert kept.steps(m, _block(m)) == []


def test_the_ledger_fails_the_run_naming_every_failure() -> None:
    ledger = PhoneLedger()
    assert "every phone map" in ledger.check()
    ledger.fail("mapsforge-map:north-america-us-vermont", "north-america/us/vermont: no map")
    step = ledger.step()
    assert step.kind == "check-phone-maps"
    with pytest.raises(BackendError, match="north-america/us/vermont: no map"):
        step.perform()


def test_the_scratch_and_the_sqlite_library_stay_in_the_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer's `hd` scratch and sqlite-jdbc's unpacked library follow
    java.io.tmpdir and org.sqlite.tmpdir; a run that leaves something there
    has it cleared with the rest, never in /tmp."""
    body = f'echo scratch > "$(pwd)/nodes.tmp"; {JAVA_OK}'
    install_fakes(monkeypatch, tmp_path / "bin", {"java": body})
    _install_region(tmp_path, DELAWARE)
    conv = _converter(tmp_path, "poi", [DELAWARE])
    _run(conv)
    work = tmp_path / "staging" / "mapsforge-poi" / f"{DELAWARE.slug}.work"
    assert list(work.iterdir()) == []


def test_binary_garbage_from_java_fails_the_region_not_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bytes that are not UTF-8 must not be decoded anywhere (review, 2026-09-30):
    a decode error would escape the ledger and kill the transaction."""
    garbage = f"{_OUT}; printf '\\377\\376\\200\\201 not a map at all' > \"$o\""
    install_fakes(monkeypatch, tmp_path / "bin", {"java": garbage})
    _install_region(tmp_path, VERMONT)
    conv = _converter(tmp_path, "map", [VERMONT])
    _run(conv)
    (message,) = conv.ledger.failed.values()
    assert "not a Mapsforge map" in message


def test_the_magic_check_does_not_read_flock_s_lines_in_any_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """flock --verbose writes to stdout; the check reads only an exit status."""
    install_fakes(monkeypatch, tmp_path / "bin", {"java": JAVA_OK})
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    _install_region(tmp_path, VERMONT)
    conv = _converter(tmp_path, "map", [VERMONT])
    _run(conv)
    assert conv.ledger.failed == {}
