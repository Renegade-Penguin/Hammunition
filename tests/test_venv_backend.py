# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The venv backend: hash-pinned, unprivileged, and reachable afterwards."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from hammunition.backends import Command, VenvBackend
from hammunition.backends.base import Action
from hammunition.manifest.schema import ManifestError, PackageManifest, VenvInstall

HASHED = (
    "example==1.0 --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000"
)


def manifest(requirements: list[str], expose: list[str]) -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": "venvunit",
            "version": "1.0",
            "summary": "Fixture for the venv backend suite",
            "categories": ["logging"],
            "install": [
                {"install": {"method": "venv", "requirements": requirements, "expose": expose}}
            ],
            "update": {"probe": {"method": "pypi"}, "strategy": "reinstall"},
            "documentation": {
                "what_it_does": "Exists so the venv backend has a unit to plan.",
                "why_you_want_it": "You do not; the suite does.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def block(m: PackageManifest) -> VenvInstall:
    install = m.install[0].install
    assert isinstance(install, VenvInstall)
    return install


def test_an_unhashed_requirement_is_refused_at_the_schema() -> None:
    """The security rule enforced before a plan exists: no hash, no manifest."""
    with pytest.raises((ManifestError, ValueError), match="--hash=sha256"):
        manifest(["example==1.0"], [])


def test_marker_and_comment_lines_need_no_hash() -> None:
    m = manifest([HASHED, "# a comment", "--no-binary :none:"], [])
    assert len(block(m).requirements) == 3


def test_the_steps_are_staged_venv_pip_wrapper_in_that_order(tmp_path: Path) -> None:
    backend = VenvBackend(venv_root=tmp_path / "venvs", bin_dir=tmp_path / "bin")
    m = manifest([HASHED], ["venvunit"])
    steps = backend.steps(m, block(m))
    kinds: list[Any] = [s.kind if isinstance(s, Action) else s.argv[:3] for s in steps]
    assert kinds[0] == "requirements"
    # The venv is built with the engine's own interpreter (>=3.11 guaranteed),
    # never bare python3 which is 3.10 on Ubuntu/Pop 22.04.
    assert kinds[1] == (str(Path(sys.executable).resolve()), "-m", "venv")
    assert "--require-hashes" in steps[2].argv  # type: ignore[union-attr]
    assert kinds[3] == "wrapper"
    assert all(not getattr(s, "requires_root", False) for s in steps), (
        "nothing in a venv install may need root"
    )


def test_staging_and_wrapper_actions_actually_produce_their_files(tmp_path: Path) -> None:
    """Perform the in-process halves for real (D-031: the effect, not the plan)."""
    backend = VenvBackend(venv_root=tmp_path / "venvs", bin_dir=tmp_path / "bin")
    m = manifest([HASHED], ["venvunit"])
    steps = backend.steps(m, block(m))

    outcome = steps[0].perform()  # type: ignore[union-attr]
    staged = tmp_path / "venvs" / "venvunit.requirements.txt"
    assert staged.read_text() == HASHED + "\n"
    assert "1 pinned requirement line(s)" in outcome

    steps[3].perform()  # type: ignore[union-attr]
    wrapper = tmp_path / "bin" / "venvunit"
    body = wrapper.read_text()
    assert body.startswith("#!/bin/sh\n")
    assert str(tmp_path / "venvs" / "venvunit" / "bin" / "venvunit") in body
    assert os.access(wrapper, os.X_OK)


def test_pip_runs_from_the_venv_it_installs_into(tmp_path: Path) -> None:
    backend = VenvBackend(venv_root=tmp_path / "venvs", bin_dir=tmp_path / "bin")
    m = manifest([HASHED], [])
    pip_cmd = [s for s in backend.steps(m, block(m)) if isinstance(s, Command)][1]
    assert pip_cmd.argv[0] == str(tmp_path / "venvs" / "venvunit" / "bin" / "pip")


def test_declared_env_reaches_the_pip_command_and_only_that(tmp_path: Path) -> None:
    """The setuptools-scm-from-archive case: the version var must ride the pip
    invocation, and the plan shows it because Command env is printed."""
    m = PackageManifest.model_validate(
        {
            "name": "venvunit",
            "version": "1.0",
            "summary": "Fixture for the env-passing test",
            "categories": ["logging"],
            "install": [
                {
                    "install": {
                        "method": "venv",
                        "requirements": [HASHED],
                        "env": {"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VENVUNIT": "1.0"},
                    }
                }
            ],
            "update": {"probe": {"method": "pypi"}, "strategy": "reinstall"},
            "documentation": {
                "what_it_does": "Exists so the env-passing path has a unit.",
                "why_you_want_it": "You do not; the suite does.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )
    backend = VenvBackend(venv_root=tmp_path / "venvs", bin_dir=tmp_path / "bin")
    commands = [s for s in backend.steps(m, block(m)) if isinstance(s, Command)]
    venv_create, pip = commands
    assert not venv_create.env
    assert pip.env == {"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VENVUNIT": "1.0"}


# ---------------------------------------------------------------------------
# The payload half of the hybrid (source-build-gaps #9)
# ---------------------------------------------------------------------------


def hybrid_manifest(script: str | None) -> PackageManifest:
    block: dict[str, Any] = {
        "method": "venv",
        "requirements": [HASHED],
        "payload": {"url": "https://example.org/tree.tar.gz", "sha256": "1" * 64},
        "tree_marker": "run.py",
    }
    if script:
        block["payload_build_script"] = script
    return PackageManifest.model_validate(
        {
            "name": "hybridunit",
            "version": "1.0",
            "summary": "Fixture for the venv payload hybrid",
            "categories": ["listening"],
            "install": [{"install": block}],
            "launchers": [
                {
                    "name": "hybridunit",
                    "exec": "exec {venv}/bin/python run.py",
                    "working_directory": "/usr/local/share/hammunition/hybridunit",
                }
            ],
            "update": {"probe": {"method": "none"}, "strategy": "reinstall"},
            "documentation": {
                "what_it_does": "Exists so the hybrid path has a unit to plan.",
                "why_you_want_it": "You do not; the suite does.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def test_payload_plans_fetch_extract_build_and_tree_install(tmp_path: Path) -> None:
    class StubFetcher:
        offline = False
        mirror = None

        def sources_for(self, url: str, mirror: Any) -> tuple[tuple[str, str], ...]:
            return (("publisher", url),)

        def path_for(self, artifact: Any) -> Path:
            return tmp_path / "cache" / "tree.tar.gz"

        def fetch(self, artifact: Any) -> Any:
            raise AssertionError("planning must not fetch")

    backend = VenvBackend(
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        fetcher=StubFetcher(),  # type: ignore[arg-type]
        build_root=tmp_path / "build",
    )
    m = hybrid_manifest("auto_rx/build.sh")
    steps = backend.steps(m, block(m))
    kinds = [s.kind if isinstance(s, Action) else s.argv[0] for s in steps]
    assert kinds[:3] == [
        "requirements",
        str(Path(sys.executable).resolve()),
        str(tmp_path / "venvs" / "hybridunit" / "bin" / "pip"),
    ]
    assert "fetch" in kinds and "extract" in kinds
    assert ("sh") in kinds, kinds
    assert kinds[-3:] == ["rm", "install", "cp"], "tree install must be last"
    build = next(s for s in steps if not isinstance(s, Action) and s.argv[0] == "sh")
    assert build.argv == ("sh", "build.sh")
    assert build.cwd is not None and build.cwd.name == "auto_rx"
    assert not build.requires_root


def test_a_payload_without_a_fetcher_is_refused_by_name(tmp_path: Path) -> None:
    from hammunition.backends import BackendError

    backend = VenvBackend(venv_root=tmp_path / "venvs", bin_dir=tmp_path / "bin")
    m = hybrid_manifest(None)
    with pytest.raises(BackendError, match="fetcher"):
        backend.steps(m, block(m))


def test_the_venv_placeholder_resolves_in_launchers(tmp_path: Path) -> None:
    from hammunition.launchers import wrapper_body

    m = hybrid_manifest(None)
    body = wrapper_body(m, m.launchers[0], venv_dir=tmp_path / "venvs" / "hybridunit")
    assert f"exec {tmp_path}/venvs/hybridunit/bin/python run.py" in body


def test_a_venv_launcher_with_no_venv_dir_fails_loudly(tmp_path: Path) -> None:
    from hammunition.backends import BackendError
    from hammunition.launchers import wrapper_body

    m = hybrid_manifest(None)
    with pytest.raises(BackendError, match="diverged"):
        wrapper_body(m, m.launchers[0])


def test_a_build_script_without_a_payload_is_refused_at_the_schema() -> None:
    with pytest.raises((ValueError,), match="no payload"):
        PackageManifest.model_validate(
            {
                "name": "broken",
                "version": "1.0",
                "summary": "Script with no tree to run in",
                "categories": ["listening"],
                "install": [
                    {
                        "install": {
                            "method": "venv",
                            "requirements": [HASHED],
                            "payload_build_script": "build.sh",
                        }
                    }
                ],
                "update": {"probe": {"method": "none"}, "strategy": "reinstall"},
                "documentation": {
                    "what_it_does": "Exists to be refused by the validator.",
                    "why_you_want_it": "You do not; the suite does.",
                    "upstream_url": "https://example.invalid/",
                },
            }
        )


def test_the_venv_backend_passes_the_operator_to_the_payload_tree(tmp_path: Path) -> None:
    """Issue #38, D-043: radiosonde-auto-rx writes `log/` beside its payload,
    so the payload tree is handed to the operator by an explicit step."""

    class StubFetcher:
        offline = False
        mirror = None

        def sources_for(self, url: str, mirror: Any) -> tuple[tuple[str, str], ...]:
            return (("publisher", url),)

        def path_for(self, artifact: Any) -> Path:
            return tmp_path / "cache" / "tree.tar.gz"

        def fetch(self, artifact: Any) -> Any:
            raise AssertionError("planning must not fetch")

    backend = VenvBackend(
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        fetcher=StubFetcher(),  # type: ignore[arg-type]
        build_root=tmp_path / "build",
        owner="alice",
    )
    m = hybrid_manifest(None)
    steps = backend.steps(m, block(m))
    last = steps[-1]
    assert isinstance(last, Command)
    assert last.argv[:5] == ("chown", "-R", "-h", "--", "alice:")
    assert last.argv[-1] == "/usr/local/share/hammunition/hybridunit"
