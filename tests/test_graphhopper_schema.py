# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""GraphHopper's two blocks: one jar installed as a tree, and the
``graphhopper-import`` converter with its ``program`` input.  D-076."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from hammunition.backends import Action, BackendError, BinaryBackend, Command, RecordingRunner
from hammunition.fetch import Fetcher
from hammunition.manifest.schema import (
    BinaryInstall,
    DerivedDataInstall,
    PackageManifest,
    derived_source_method_problem,
)

SHA = "b" * 64
JAR = "graphhopper-web-11.1.jar"
DOCS = {
    "what_it_does": "Does an example thing for the purposes of testing.",
    "why_you_want_it": "Because the test suite requires a valid manifest.",
    "upstream_url": "https://example.invalid/",
}
OSM = {"licence": "ODbL-1.0", "licence_url": "https://www.openstreetmap.org/copyright"}


def _manifest(
    name: str, install: dict[str, Any], depends: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1.0",
        "summary": "An example package",
        "categories": ["navigation-maps"],
        "depends": depends or [],
        "install": [{"install": install}],
        "update": {"probe": {"method": "none"}},
        "documentation": DOCS,
        **extra,
    }


def _jar_block(**extra: Any) -> dict[str, Any]:
    return {
        "method": "binary",
        "artifact": {"url": f"https://example.invalid/{JAR}", "sha256": SHA},
        "format": "executable",
        "install_tree": True,
        "tree_marker": JAR,
        **extra,
    }


def _graph_block(**extra: Any) -> dict[str, Any]:
    return {
        "method": "derived",
        "converter": "graphhopper-import",
        "source": "osm-regions",
        "program": "graphhopper",
        **OSM,
        **extra,
    }


# -- a single file installed as a tree ---------------------------------------


def test_an_executable_installed_as_a_tree_validates() -> None:
    block = PackageManifest.model_validate(_manifest("graphhopper", _jar_block())).install[0]
    assert isinstance(block.install, BinaryInstall)
    assert block.install.install_tree and block.install.tree_marker == JAR


@pytest.mark.parametrize("marker", ["lib/graphhopper.jar", "./graphhopper.jar"])
def test_a_single_file_tree_names_its_file_by_a_plain_name(marker: str) -> None:
    with pytest.raises(ValidationError, match="plain file name"):
        PackageManifest.model_validate(_manifest("graphhopper", _jar_block(tree_marker=marker)))


def _backend(tmp_path: Path, prefix: Path, owner: str | None = None) -> BinaryBackend:
    return BinaryBackend(
        fetcher=Fetcher(tmp_path / "cache"),
        runner=RecordingRunner(),
        build_root=tmp_path / "build",
        prefix=prefix,
        owner=owner,
    )


def test_the_file_is_staged_under_its_marker_and_installed_as_every_tree_is(
    tmp_path: Path,
) -> None:
    manifest = PackageManifest.model_validate(_manifest("graphhopper", _jar_block()))
    backend = _backend(tmp_path, Path("/usr/local"), owner="alice")
    steps = backend.steps(manifest, manifest.install[0])
    kinds = [s.kind if isinstance(s, Action) else s.argv[0] for s in steps]
    assert kinds == ["fetch", "prepare", "stage", "rm", "install", "cp", "chown"]
    stage = steps[2]
    assert isinstance(stage, Action)
    staged = tmp_path / "build" / f"graphhopper-{SHA[:12]}" / "src" / JAR
    assert stage.detail == str(staged)
    copy = steps[5]
    assert isinstance(copy, Command)
    assert copy.argv[-1] == "/usr/local/share/hammunition/graphhopper"
    assert copy.argv[-2] == str(staged.parent)
    last = steps[-1]
    assert isinstance(last, Command) and last.argv[:5] == ("chown", "-R", "-h", "--", "alice:")


def test_staging_copies_the_fetched_file_readable_and_not_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = PackageManifest.model_validate(_manifest("graphhopper", _jar_block()))
    backend = _backend(tmp_path, tmp_path / "prefix")
    steps = backend.steps(manifest, manifest.install[0])
    fetched = tmp_path / "fetched.jar"
    fetched.write_bytes(b"PK\x03\x04 a jar")
    # The fetch step fills the shared slot the later steps read; stand in for it.
    fetch, prepare, stage = steps[0], steps[1], steps[2]
    assert isinstance(fetch, Action) and isinstance(prepare, Action) and isinstance(stage, Action)
    monkeypatch.setattr(
        backend.fetcher,
        "fetch",
        lambda artifact, **options: SimpleNamespace(
            path=fetched,
            from_cache=True,
            size=11,
            sha256=SHA,
            source="cache",
            warning=None,
            url=None,
            mirror_failure=None,
        ),
    )
    fetch.perform()
    prepare.perform()
    stage.perform()
    staged = tmp_path / "build" / f"graphhopper-{SHA[:12]}" / "src" / JAR
    assert staged.read_bytes() == fetched.read_bytes()
    assert staged.stat().st_mode & 0o777 == 0o644


def test_binaries_beside_a_single_file_tree_are_refused(tmp_path: Path) -> None:
    manifest = PackageManifest.model_validate(
        _manifest(
            "graphhopper",
            _jar_block(),
            binaries=[{"produced": JAR, "install_as": "graphhopper"}],
        )
    )
    with pytest.raises(BackendError, match="tree"):
        _backend(tmp_path, tmp_path / "prefix").steps(manifest, manifest.install[0])


# -- the converter -------------------------------------------------------------


def test_a_graphhopper_import_block_validates_with_its_program() -> None:
    parsed = (
        PackageManifest.model_validate(
            _manifest("graphhopper-graph", _graph_block(), ["osm-regions", "graphhopper"])
        )
        .install[0]
        .install
    )
    assert isinstance(parsed, DerivedDataInstall)
    assert (parsed.converter, parsed.program) == ("graphhopper-import", "graphhopper")


def test_the_program_is_required_on_this_converter() -> None:
    block = _graph_block()
    del block["program"]
    with pytest.raises(ValidationError, match="program"):
        PackageManifest.model_validate(_manifest("graphhopper-graph", block, ["osm-regions"]))


@pytest.mark.parametrize("field", ["profiles", "elevation", "kit"])
def test_brouters_and_tilemakers_inputs_are_refused_here(field: str) -> None:
    block = _graph_block(**{field: "something"})
    with pytest.raises(ValidationError, match=field):
        PackageManifest.model_validate(
            _manifest("graphhopper-graph", block, ["osm-regions", "graphhopper", "something"])
        )


def test_program_is_still_refused_on_a_converter_that_reads_none() -> None:
    block = {
        "method": "derived",
        "converter": "routino-planetsplitter",
        "source": "osm-regions",
        "program": "graphhopper",
        **OSM,
    }
    with pytest.raises(ValidationError, match="program"):
        PackageManifest.model_validate(_manifest("x", block, ["osm-regions", "graphhopper"]))


def test_the_program_must_be_in_depends() -> None:
    with pytest.raises(ValidationError, match="graphhopper"):
        PackageManifest.model_validate(
            _manifest("graphhopper-graph", _graph_block(), ["osm-regions"])
        )


def _catalog(program: dict[str, Any]) -> dict[str, PackageManifest]:
    return {
        "osm-regions": PackageManifest.model_validate(
            _manifest("osm-regions", {"method": "osm-regions", **OSM})
        ),
        "graphhopper": PackageManifest.model_validate(_manifest("graphhopper", program)),
    }


def test_the_catalog_wide_check_accepts_a_binary_tree_as_the_program() -> None:
    graph = PackageManifest.model_validate(
        _manifest("graphhopper-graph", _graph_block(), ["osm-regions", "graphhopper"])
    )
    assert derived_source_method_problem(graph, _catalog(_jar_block())) is None


def test_the_catalog_wide_check_refuses_a_program_that_installs_no_tree() -> None:
    graph = PackageManifest.model_validate(
        _manifest("graphhopper-graph", _graph_block(), ["osm-regions", "graphhopper"])
    )
    flat = _jar_block()
    del flat["install_tree"], flat["tree_marker"]
    problem = derived_source_method_problem(graph, _catalog(flat))
    assert problem is not None and "install_tree" in problem
