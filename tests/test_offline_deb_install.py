# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""An offline vendor .deb install never lets apt download.  #381, Task 13 fix round 1.

Offline apt runs with ``--no-install-recommends --no-download``, so nothing it
would otherwise fetch (a Recommends, a dependency a bad plan missed) can reach an
archive. The plan says which Recommends are not installed; for an uncached package
it says the dependency check waits for the fetch. Online is exactly as before.
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import pytest

from bunker_fixtures import artifact as catalogue_artifact
from bunker_fixtures import make_context
from hammunition.backends.base import Action, BackendError, Command, CommandResult
from hammunition.backends.binary import BinaryBackend
from hammunition.fetch import Fetcher
from hammunition.manifest.schema import PackageManifest, RemoteArtifact
from hammunition.payloads import payload_name

cli = importlib.import_module("hammunition.cli.main")

BODY = b"deb bytes"
PIN = RemoteArtifact(
    url="https://example.invalid/tool_1.0_amd64.deb", sha256=hashlib.sha256(BODY).hexdigest()
)
ONLINE_ARGV = (
    "apt-get",
    "-o",
    "Acquire::Retries=3",
    "install",
    "--yes",
    "--no-remove",
    "--",
    "/cache/x.deb",
)
APT_OFFLINE_ARGV = (
    "apt-get",
    "-o",
    "Acquire::Retries=3",
    "install",
    "--yes",
    "--no-remove",
    "--no-install-recommends",
    "--no-download",
    "--",
    "/cache/x.deb",
)


class Recording:
    def __init__(self) -> None:
        self.commands: list[Command] = []

    def run(self, command: Command) -> CommandResult:
        self.commands.append(command)
        return CommandResult(argv=command.argv, returncode=0, stdout="", stderr="")


def _manifest() -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": "debunit",
            "version": "1.0",
            "summary": "Fixture for the offline deb install",
            "categories": ["packet"],
            "install": [
                {
                    "install": {
                        "method": "binary",
                        "artifact": PIN.model_dump(exclude_none=True),
                        "format": "deb",
                        "deb_package": "xunit",
                    }
                }
            ],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Exists so the deb path has a unit to plan.",
                "why_you_want_it": "You do not; the suite does.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def _backend(
    tmp_path: Path,
    *,
    offline: bool,
    cached: bool = False,
    recommends: str | None = None,
    isolation: str | None = "bwrap",
) -> tuple[BinaryBackend, Recording]:
    runner = Recording()
    fetcher = Fetcher(
        tmp_path / "cache", offline=offline, mirror="http://bunker.invalid" if offline else None
    )
    if cached:
        fetcher.path_for(PIN).parent.mkdir(parents=True)
        fetcher.path_for(PIN).write_bytes(BODY)
    rows = [catalogue_artifact("debunit", payload_name(PIN), BODY)]
    context = make_context(tmp_path / "ctx", rows, offline=offline) if offline else None
    return (
        BinaryBackend(
            fetcher=fetcher,
            runner=runner,
            build_root=tmp_path / "build",
            prefix=tmp_path / "prefix",
            context=context,
            dependency_check=(lambda path: []) if offline else None,
            recommends_of=(lambda path: recommends or "") if offline else None,
            isolation=isolation,
        ),
        runner,
    )


def test_online_apt_argv_is_unchanged(tmp_path: Path) -> None:
    backend, runner = _backend(tmp_path, offline=False)
    backend._install_deb("debunit", {"path": Path("/cache/x.deb")})
    assert runner.commands[-1].argv == ONLINE_ARGV


def test_offline_apt_can_neither_install_recommends_nor_download(tmp_path: Path) -> None:
    backend, runner = _backend(tmp_path, offline=True)
    backend._install_deb("debunit", {"path": Path("/cache/x.deb")})
    argv = runner.commands[-1].argv
    assert argv[0] == "bwrap" and "--bind" in argv and "--ro-bind" not in argv
    assert argv[argv.index("--") + 1 :] == APT_OFFLINE_ARGV
    assert runner.commands[-1].requires_root


def _steps(backend: BinaryBackend) -> list[Action]:
    manifest = _manifest()
    steps = backend.steps(manifest, manifest.install[0])
    assert all(isinstance(s, Action) for s in steps)
    return [s for s in steps if isinstance(s, Action)]


def test_offline_maintainer_scripts_run_in_a_sandbox_as_root(tmp_path: Path) -> None:
    backend, runner = _backend(tmp_path, offline=True)
    backend._install_deb("debunit", {"path": Path("/cache/x.deb")})
    command = runner.commands[-1]
    assert command.argv[0] == "bwrap"
    assert command.argv[command.argv.index("--") + 1] == "apt-get"
    assert command.argv_for(euid=1000)[:2] == ("sudo", "env")  # elevated by the usual path
    assert command.argv_for(euid=1000).index("bwrap") < command.argv_for(euid=1000).index("apt-get")


def test_offline_without_bwrap_the_backend_refuses_the_deb_install(tmp_path: Path) -> None:
    backend, _ = _backend(tmp_path, offline=True, isolation=None)
    manifest = _manifest()
    with pytest.raises(BackendError, match="bwrap"):
        backend.steps(manifest, manifest.install[0])
    with pytest.raises(BackendError, match="bwrap"):
        backend._install_deb("debunit", {"path": Path("/cache/x.deb")})


def test_online_needs_no_sandbox(tmp_path: Path) -> None:
    backend, _ = _backend(tmp_path, offline=False, isolation=None)
    assert _steps(backend)


def test_the_plan_refuses_an_offline_vendor_deb_when_bwrap_is_missing() -> None:
    from hammunition.distro import Target
    from hammunition.plan import Blocker, InstallPlan, PlannedPackage, offline_payload_blockers

    manifest = _manifest()
    unit = PlannedPackage(manifest, manifest.install[0], (), requested_by=("requested",))
    plan = InstallPlan(Target(distro="debian", version="13", arch="x86_64"), (unit,))

    def blockers(p: InstallPlan, *, deb_isolated: bool) -> list[Blocker]:
        return offline_payload_blockers(
            p,
            frozenset(),
            cached=lambda a: True,
            deb_unmet=lambda u: [],
            deb_isolated=deb_isolated,
        )

    found = blockers(plan, deb_isolated=False)
    assert [b.subject for b in found] == ["debunit"]
    assert "bwrap" in found[0].reason and "maintainer scripts" in found[0].reason
    assert blockers(plan, deb_isolated=True) == []
    installed = PlannedPackage(
        manifest, manifest.install[0], (), requested_by=("requested",), deb_installed=True
    )
    again = InstallPlan(plan.target, (installed,))
    assert blockers(again, deb_isolated=False) == []


def test_the_planned_install_step_runs_the_offline_argv_when_performed(tmp_path: Path) -> None:
    backend, runner = _backend(tmp_path, offline=True, cached=True)
    fetch, check, install = _steps(backend)
    fetch.perform()
    check.perform()
    install.perform()
    argv = runner.commands[-1].argv
    assert argv[0] == "bwrap" and "--bind" in argv and "--ro-bind" not in argv
    assert argv[argv.index("--") + 1 : -1] == APT_OFFLINE_ARGV[:-1]
    assert argv[-1].endswith(".deb")


def test_the_offline_plan_says_apt_may_not_download_or_install_recommends(tmp_path: Path) -> None:
    backend, _ = _backend(tmp_path, offline=True, cached=True)
    install = _steps(backend)[2]
    text = install.description + " " + install.detail
    assert "--no-install-recommends" in text and "--no-download" in text
    assert "bwrap" in text and "maintainer scripts" in text
    online, _ = _backend(tmp_path / "o", offline=False)
    online_install = _steps(online)[1]
    assert "--no-download" not in online_install.description + online_install.detail


def test_the_plan_lists_the_recommends_that_will_not_be_installed(tmp_path: Path) -> None:
    backend, _ = _backend(
        tmp_path, offline=True, cached=True, recommends="extra-tool, other (>= 2)"
    )
    install = _steps(backend)[2]
    assert "NOT installed offline: extra-tool, other (>= 2)" in install.detail


def test_a_cached_package_with_no_recommends_says_none(tmp_path: Path) -> None:
    backend, _ = _backend(tmp_path, offline=True, cached=True, recommends="")
    assert "no Recommends" in _steps(backend)[2].detail


def test_an_uncached_package_defers_both_the_check_and_the_recommends_list(tmp_path: Path) -> None:
    backend, _ = _backend(tmp_path, offline=True, cached=False, recommends="extra-tool")
    _fetch, check, install = _steps(backend)
    shown = check.description + " " + check.detail
    assert "after the fetch" in shown and "nothing is installed if a dependency is missing" in shown
    assert "extra-tool" not in install.detail  # unknown until the package is local
    assert "Recommends" in install.detail and "after the fetch" in install.detail
    rendered = check.display(euid=1000)
    assert "after the fetch" in rendered


def test_the_check_step_reports_the_recommends_once_the_package_is_local(tmp_path: Path) -> None:
    backend, _ = _backend(tmp_path, offline=True, cached=True, recommends="extra-tool")
    fetch, check, _install = _steps(backend)
    fetch.perform()
    assert "Recommends not installed: extra-tool" in check.perform()


def test_a_cached_package_is_checked_in_the_plan_not_deferred(tmp_path: Path) -> None:
    backend, _ = _backend(tmp_path, offline=True, cached=True)
    check = _steps(backend)[1]
    assert "after the fetch" not in check.description + check.detail


def test_deb_recommends_reads_the_field_as_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Result:
        returncode = 0
        stderr = ""
        stdout = "Recommends: aa,\n bb | cc\n"

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Result())
    assert cli._deb_recommends(tmp_path / "x.deb") == "aa, bb | cc"
    Result.stdout = ""
    assert cli._deb_recommends(tmp_path / "x.deb") == ""
    Result.returncode = 2
    Result.stderr = "no such file"
    assert "could not be read" in cli._deb_recommends(tmp_path / "x.deb")
