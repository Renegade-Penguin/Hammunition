# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""A vendor .deb's dependencies are checked by version, not only by name.  #381, Task 13.

Offline, apt cannot fetch what a .deb depends on, so each dependency group must
already be met by an installed package whose version is in the stated range. The
expected orderings below were measured against ``dpkg --compare-versions``
(and 1,500 random pairs agreed with it); the tests do not need dpkg.
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

import pytest

from bunker_fixtures import make_context
from hammunition.backends.apt import AptBackend, AptPackageState
from hammunition.backends.base import Action, BackendError, Command, CommandResult
from hammunition.backends.binary import BinaryBackend
from hammunition.fetch import Fetcher, mirror_url
from hammunition.manifest.schema import PackageManifest, RemoteArtifact
from hammunition.payloads import payload_path
from hammunition.plan import (
    DebDependency,
    DebDependencyError,
    compare_deb_versions,
    deb_dependency_met,
    parse_deb_dependencies,
    parse_deb_depends,
    valid_deb_version,
)
from test_fetch_mirror import Routes

cli = importlib.import_module("hammunition.cli.main")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("1.0", "1.0-1"),
        ("1.0~rc1", "1.0"),
        ("1.0~~", "1.0~"),
        ("1.0-1~bpo12+1", "1.0-1"),
        ("1.0-1", "1.0-2"),
        ("2.0", "1:0.5"),
        ("1.9", "1.10"),
        ("1.0", "1.0+b1"),
        ("1.0", "1.0a"),
        ("1.0", "1.0.1"),
        ("1.2.3-4-4", "1.2.3-4-5"),
        ("", "0"),
        ("", "1:0"),
    ],
)
def test_the_first_version_is_older(a: str, b: str) -> None:
    assert compare_deb_versions(a, b) < 0 < compare_deb_versions(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [("1.0", "1.00"), ("0:1.0", "1.0"), ("1.0-0", "1.0"), ("2.34-0ubuntu3", "2.34-0ubuntu3")],
)
def test_these_versions_are_equal(a: str, b: str) -> None:
    assert compare_deb_versions(a, b) == 0 == compare_deb_versions(b, a)


@pytest.mark.parametrize(
    ("relation", "installed", "met"),
    [
        (">=", "1.2", True),
        (">=", "1.3", True),
        (">=", "1.1", False),
        (">>", "1.2", False),
        (">>", "1.2.1", True),
        ("<=", "1.2", True),
        ("<=", "1.3", False),
        ("<<", "1.2", False),
        ("<<", "1.1", True),
        ("=", "1.2", True),
        ("=", "1.2-1", False),
        (">=", "1:0.1", True),
    ],
)
def test_each_relation_at_its_boundary(relation: str, installed: str, met: bool) -> None:
    assert deb_dependency_met(DebDependency("libfoo", relation, "1.2"), installed) is met


def test_no_relation_needs_only_the_package_and_not_installed_is_never_met() -> None:
    assert deb_dependency_met(DebDependency("libfoo"), "0.1")
    assert not deb_dependency_met(DebDependency("libfoo"), None)
    assert not deb_dependency_met(DebDependency("libfoo", ">=", "1"), None)


def test_the_parser_keeps_versions_alternatives_and_arch_qualifiers() -> None:
    groups = parse_deb_dependencies(
        "libc6 (>= 2.34), libfoo:amd64, aa | bb (<< 2)\n , cc (= 1:2.0-1), dd:any, ee:native (>> 1)"
    )
    flat = [[d.text() for d in group] for group in groups]
    assert flat == [
        ["libc6 (>= 2.34)"],
        ["libfoo:amd64"],
        ["aa", "bb (<< 2)"],
        ["cc (= 1:2.0-1)"],
        ["dd:any"],
        ["ee:native (>> 1)"],
    ]
    assert parse_deb_depends("libc6 (>= 2.34), aa | bb") == [["libc6"], ["aa", "bb"]]
    assert parse_deb_dependencies("") == [] == parse_deb_dependencies("  \n ")


def test_the_deprecated_spellings_mean_or_equal_as_dpkg_reads_them() -> None:
    lt, gt = (g[0] for g in parse_deb_dependencies("cc (< 1), dd (> 3)"))
    assert (lt.relation, gt.relation) == ("<=", ">=")
    assert deb_dependency_met(lt, "1")  # `(< 1)` is `(<= 1)`: the same version meets it
    assert deb_dependency_met(gt, "3")
    assert not deb_dependency_met(lt, "1.1") and not deb_dependency_met(gt, "2.9")


@pytest.mark.parametrize(
    "field",
    [
        "libfoo (=> 99)",  # a relation that does not exist
        "libfoo (>= 99",  # unbalanced
        "libfoo >= 99)",
        "libfoo (>= )",
        "libfoo (>= 1 2)",
        "(>= 99)",  # no package
        "libfoo,, libbar",  # empty group
        ", libfoo",
        "libfoo,",
        "libfoo | ",
        "libfoo | | libbar",
        "libfoo garbage",  # trailing garbage
        "libfoo (>= 1) garbage",
        "libfoo (>= 1)(<< 2)",
        "libfoo [amd64]",  # a build-time restriction has no place in a binary package
        "libfoo <!nocheck>",
        "Libfoo",  # not a Debian package name
        "x",
        "libfoo:",
        "libfoo:i386:any",
        "libfoo (>= bad:1)",  # an invalid version
        "libfoo (>= a1)",
        "libfoo (>= 1_0)",
        "libfoo (>= 1.0-)",
        "libfoo (>= :1)",
    ],
)
def test_a_malformed_field_is_refused_never_passed_as_unversioned(field: str) -> None:
    with pytest.raises(DebDependencyError):
        parse_deb_dependencies(field)


@pytest.mark.parametrize("qualifier", ["i386", "amd64", "arm64", "any", "native"])
def test_known_arch_qualifiers_parse(qualifier: str) -> None:
    (group,) = parse_deb_dependencies(f"libfoo:{qualifier} (>= 1)")
    assert group[0].arch == qualifier


@pytest.mark.parametrize(
    "qualifier", ["riscv64", "loong64", "x32", "kfreebsd-amd64", "bogus", "all"]
)
def test_any_syntactically_valid_architecture_name_parses(qualifier: str) -> None:
    (group,) = parse_deb_dependencies(f"libfoo:{qualifier}")
    assert group[0].arch == qualifier


@pytest.mark.parametrize("qualifier", ["AMD64", "x_86", "", "-amd64", "a b", "amd64!", "i386:any"])
def test_a_malformed_arch_qualifier_is_refused(qualifier: str) -> None:
    with pytest.raises(DebDependencyError):
        parse_deb_dependencies(f"libfoo:{qualifier}")


@pytest.mark.parametrize(
    ("version", "valid"),
    [
        ("1.0", True),
        ("0", True),
        ("1:2.0-3", True),
        ("0:1", True),
        ("12345:1.0~rc1+b2-1ubuntu0.1", True),
        ("1.0-1-2", True),  # upstream may hold hyphens when a revision follows
        ("", False),
        ("bad:1", False),
        (":1", False),
        ("1:", False),
        ("1:2:3", True),  # colons are allowed in the upstream part once there is an epoch
        ("1:2:", True),
        ("1::", False),
        ("0:a:1", False),
        ("1:a:1", False),
        ("a1", False),
        ("-1", False),
        ("1.0-", False),
        ("1.0_1", False),
        ("1 0", False),
        ("1.0\n", False),
        ("1.0-1_2", False),
        ("\uff11.0", False),  # a fullwidth digit is not a digit
    ],
)
def test_versions_are_validated_per_debian_policy(version: str, valid: bool) -> None:
    assert valid_deb_version(version) is valid


@pytest.mark.parametrize("bad", ["bad:1", "a1", "1.0_1", ":1", "1.0-", "1:", "x:1:2"])
def test_comparing_an_invalid_version_raises_and_the_dependency_is_not_met(bad: str) -> None:
    with pytest.raises(ValueError, match="not a valid Debian version"):
        compare_deb_versions(bad, "1")
    with pytest.raises(ValueError, match="not a valid Debian version"):
        compare_deb_versions("1", bad)
    assert not deb_dependency_met(DebDependency("libfoo", ">=", "1"), bad)


def test_a_colon_in_the_upstream_part_orders_with_the_rest() -> None:
    assert compare_deb_versions("1:2:3", "1:2:4") < 0 < compare_deb_versions("1:2:4", "1:2:3")
    assert compare_deb_versions("1:2:3", "1:2:3") == 0
    assert compare_deb_versions("1:2:3", "2:0") < 0  # the epoch still comes first
    assert deb_dependency_met(DebDependency("libfoo", ">=", "1:2:3"), "1:2:3")
    (group,) = parse_deb_dependencies("libfoo (= 1:2:3)")
    assert group[0].version == "1:2:3"


def test_the_empty_version_orders_below_everything_and_equals_itself() -> None:
    assert compare_deb_versions("", "0") < 0 < compare_deb_versions("0", "")
    assert compare_deb_versions("", "") == 0
    assert compare_deb_versions("", "0:0") < 0


# -- the CLI's check over a real .deb field ------------------------------------


class StubApt(AptBackend):
    def __init__(self, installed: dict[str, str]) -> None:
        self.installed = installed

    def probe(self, packages: Any) -> dict[str, AptPackageState]:
        return {
            p: AptPackageState(name=p, installed=self.installed.get(p), candidate="9.0")
            for p in packages
            if p in self.installed
        }


class Dpkg:
    """A stand-in for subprocess.run that answers dpkg's architecture queries."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.asked: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.asked.append(argv)
        answer = self.answers.get(argv[1])
        if isinstance(answer, Exception):
            raise answer

        class Result:
            returncode = 0 if answer is not None else 1
            stdout = answer or ""
            stderr = "dpkg: error"

        return Result()


@pytest.fixture
def amd64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_native_arch", lambda: "amd64")
    monkeypatch.setattr(cli, "_foreign_architectures", lambda: ("i386", "arm64"))


def _unmet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, installed: dict[str, str]
) -> list[str]:
    monkeypatch.setattr(cli, "_dpkg_depends", lambda path: field)
    return list(cli._deb_unmet_file(StubApt(installed), tmp_path / "x.deb"))


def test_unmet_names_the_group_with_its_version_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, amd64: None
) -> None:
    field = "libfoo (>= 1.2), libbar | libbaz (>= 2)"
    assert _unmet(monkeypatch, tmp_path, field, {"libfoo": "1.1", "libbaz": "2.0"}) == [
        "libfoo (>= 1.2)"
    ]
    assert _unmet(monkeypatch, tmp_path, field, {"libfoo": "1.2", "libbaz": "1.9"}) == [
        "libbar | libbaz (>= 2)"
    ]
    assert _unmet(monkeypatch, tmp_path, field, {"libfoo": "1.2", "libbar": "0.1"}) == []


@pytest.mark.parametrize(
    ("field", "installed", "met"),
    [
        ("libfoo:i386 (>= 1)", {"libfoo": "2.0"}, False),  # the native one is not i386's
        ("libfoo:i386 (>= 1)", {"libfoo:i386": "2.0"}, True),
        ("libfoo:i386 (>= 1)", {"libfoo:i386": "0.5"}, False),
        ("libfoo:any", {"libfoo": "2.0"}, True),
        ("libfoo:any", {"libfoo:arm64": "2.0"}, True),
        ("libfoo:any", {"libfoo:riscv64": "2.0"}, False),  # dpkg knows no riscv64 here
        ("libfoo:riscv64", {"libfoo:riscv64": "2.0"}, True),  # valid syntax; apt is asked
        ("libfoo:any (>= 3)", {"libfoo:arm64": "2.0", "libfoo": "1"}, False),
        ("libfoo:any", {}, False),
        ("libfoo:native", {"libfoo": "2.0"}, True),
        ("libfoo:native", {"libfoo:amd64": "2.0"}, True),
        ("libfoo:native", {"libfoo:i386": "2.0"}, False),
        ("libfoo:amd64", {"libfoo": "2.0"}, True),
        ("libfoo:amd64", {"libfoo:i386": "2.0"}, False),
        ("libfoo", {"libfoo:i386": "2.0"}, False),
    ],
)
def test_arch_qualifiers_are_checked_against_that_arch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amd64: None,
    field: str,
    installed: dict[str, str],
    met: bool,
) -> None:
    assert (_unmet(monkeypatch, tmp_path, field, installed) == []) is met


@pytest.mark.parametrize(
    "field",
    ["libfoo:AMD64", "libfoo (=> 1)", "libfoo (>= 1", "libfoo,, libbar", "libfoo (>= bad:1)"],
)
def test_a_field_that_does_not_parse_is_unmet_not_assumed_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, amd64: None, field: str
) -> None:
    got = _unmet(monkeypatch, tmp_path, field, {"libfoo": "9.0", "libbar": "9.0"})
    assert got and "could not be read" in got[0]


def test_an_installed_version_that_is_not_a_version_does_not_meet_a_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, amd64: None
) -> None:
    assert _unmet(monkeypatch, tmp_path, "libfoo (>= 1)", {"libfoo": "bad:1"}) == ["libfoo (>= 1)"]


def test_unreadable_dependencies_are_reported_not_assumed_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, amd64: None
) -> None:
    def broken(path: Path) -> str:
        raise OSError("not a deb")

    monkeypatch.setattr(cli, "_dpkg_depends", broken)
    got = cli._deb_unmet_file(StubApt({}), tmp_path / "x.deb")
    assert got and "could not be read" in got[0]


@pytest.mark.parametrize(
    ("machine", "arch"),
    [("x86_64", "amd64"), ("aarch64", "arm64"), ("armv7l", "armhf"), ("i686", "i386")],
)
def test_the_kernel_name_fallback_gives_the_debian_name(
    monkeypatch: pytest.MonkeyPatch, machine: str, arch: str
) -> None:
    monkeypatch.setattr(
        cli.subprocess, "run", Dpkg(**{"--print-architecture": FileNotFoundError("dpkg")})
    )
    monkeypatch.setattr(cli.platform, "machine", lambda: machine)
    assert cli._native_arch() == arch


def test_the_depends_text_joins_pre_depends_and_depends_as_one_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Result:
        returncode = 0
        stderr = ""
        stdout = "Pre-Depends: aa (>= 1)\nDepends: bb,\n cc | dd\n"

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Result())
    text = cli._dpkg_depends(tmp_path / "x.deb")
    assert [[d.name for d in g] for g in parse_deb_dependencies(text)] == [
        ["aa"],
        ["bb"],
        ["cc", "dd"],
    ]
    Result.stdout = ""
    assert parse_deb_dependencies(cli._dpkg_depends(tmp_path / "x.deb")) == []


def test_native_architecture_comes_from_dpkg(monkeypatch: pytest.MonkeyPatch) -> None:
    dpkg = Dpkg(**{"--print-architecture": "riscv64\n"})
    monkeypatch.setattr(cli.subprocess, "run", dpkg)
    monkeypatch.setattr(cli.platform, "machine", lambda: "x86_64")  # the kernel disagrees
    assert cli._native_arch() == "riscv64"
    assert dpkg.asked == [["dpkg", "--print-architecture"]]


def test_native_architecture_falls_back_to_the_kernel_name_only_without_dpkg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli.subprocess, "run", Dpkg(**{"--print-architecture": FileNotFoundError("dpkg")})
    )
    monkeypatch.setattr(cli.platform, "machine", lambda: "aarch64")
    assert cli._native_arch() == "arm64"
    monkeypatch.setattr(cli.platform, "machine", lambda: "vax")
    with pytest.raises(BackendError, match="native architecture"):
        cli._native_arch()


@pytest.mark.parametrize("output", ["", "amd64 arm64\n", "AMD64\n", "a b\n"])
def test_a_dpkg_that_fails_or_answers_nonsense_is_not_papered_over(
    monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    monkeypatch.setattr(cli.subprocess, "run", Dpkg(**{"--print-architecture": output or None}))
    monkeypatch.setattr(cli.platform, "machine", lambda: "x86_64")
    with pytest.raises(BackendError):
        cli._native_arch()


def test_foreign_architectures_come_from_dpkg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.subprocess, "run", Dpkg(**{"--print-foreign-architectures": "i386\narm64\n"})
    )
    assert cli._foreign_architectures() == ("i386", "arm64")
    monkeypatch.setattr(cli.subprocess, "run", Dpkg(**{"--print-foreign-architectures": ""}))
    assert cli._foreign_architectures() == ()
    monkeypatch.setattr(
        cli.subprocess, "run", Dpkg(**{"--print-foreign-architectures": FileNotFoundError("x")})
    )
    assert cli._foreign_architectures() == ()
    monkeypatch.setattr(cli.subprocess, "run", Dpkg(**{"--print-foreign-architectures": None}))
    with pytest.raises(BackendError):
        cli._foreign_architectures()


def test_any_probes_exactly_the_architectures_dpkg_knows() -> None:
    from hammunition.plan import deb_probe_names

    dependency = DebDependency("libfoo", arch="any")
    assert deb_probe_names(dependency, "amd64", ("i386",)) == (
        "libfoo",
        "libfoo:amd64",
        "libfoo:i386",
    )
    assert deb_probe_names(dependency, "amd64", ()) == ("libfoo", "libfoo:amd64")


def test_foreign_architectures_are_only_asked_when_a_dependency_says_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_native_arch", lambda: "amd64")

    def never() -> tuple[str, ...]:
        raise AssertionError("dpkg was asked for foreign architectures needlessly")

    monkeypatch.setattr(cli, "_foreign_architectures", never)
    assert _unmet(monkeypatch, tmp_path, "libfoo (>= 1)", {"libfoo": "2.0"}) == []


# -- the install's own check, after the fetch -----------------------------------

BODY = b"deb bytes"
BUNKER = "http://bunker.invalid"


def perform(step: Action | Command) -> str:
    assert isinstance(step, Action)
    return step.perform()


class NeverRuns:
    def run(self, command: Command) -> CommandResult:
        raise AssertionError(f"ran {command.argv}")


PIN = RemoteArtifact(
    url="https://example.invalid/tool_1.0_amd64.deb", sha256=hashlib.sha256(BODY).hexdigest()
)


def _manifest() -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": "debunit",
            "version": "1.0",
            "summary": "Fixture for the deb dependency check",
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


def _deb_steps(
    tmp_path: Path, routes: Routes, *, offline: bool, check: Any, with_context: bool = True
) -> tuple[list[Action | Command], Fetcher]:
    pin = PIN
    manifest = _manifest()
    fetcher = Fetcher(
        tmp_path / "cache",
        transport=routes,
        mirror_transport=routes,
        mirror=BUNKER,
        offline=offline,
    )
    context = make_context(tmp_path / "ctx", [], offline=offline) if with_context else None
    if context is not None and offline:
        # Cached bytes need no Bunker row; the dependency check is what is tested.
        fetcher.path_for(pin).parent.mkdir(parents=True)
        fetcher.path_for(pin).write_bytes(BODY)
    backend = BinaryBackend(
        fetcher=fetcher,
        runner=NeverRuns(),
        build_root=tmp_path / "build",
        prefix=tmp_path / "prefix",
        context=context,
        dependency_check=check,
        isolation="bwrap",
    )
    return backend.steps(manifest, manifest.install[0]), fetcher


def test_offline_the_install_stops_before_apt_when_a_dependency_is_missing(tmp_path: Path) -> None:
    seen: list[Path] = []

    def check(path: Path) -> list[str]:
        seen.append(path)
        return ["libfoo (>= 1.2)"]

    steps, _ = _deb_steps(tmp_path, Routes({}), offline=True, check=check)
    assert [s.kind for s in steps if isinstance(s, Action)] == [
        "fetch",
        "check-deb-depends",
        "install-deb",
    ]
    perform(steps[0])
    with pytest.raises(BackendError, match=r"lacks: libfoo \(>= 1.2\)\. Nothing was installed"):
        perform(steps[1])
    assert seen and seen[0].read_bytes() == BODY


def test_offline_the_install_goes_on_when_every_dependency_is_met(tmp_path: Path) -> None:
    steps, _ = _deb_steps(tmp_path, Routes({}), offline=True, check=lambda path: [])
    perform(steps[0])
    assert "every dependency is met" in perform(steps[1])


def test_online_apt_resolves_its_own_dependencies_so_there_is_no_check_step(
    tmp_path: Path,
) -> None:
    def check(path: Path) -> list[str]:
        raise AssertionError("an online install was checked for dependencies")

    steps, _ = _deb_steps(tmp_path, Routes({}), offline=False, check=check, with_context=False)
    assert [s.kind for s in steps if isinstance(s, Action)] == ["fetch", "install-deb"]
    online, _ = _deb_steps(tmp_path / "o", Routes({}), offline=False, check=check)
    assert [s.kind for s in online if isinstance(s, Action)] == ["fetch", "install-deb"]


def test_the_check_reads_the_file_the_fetch_wrote_from_the_bunker(tmp_path: Path) -> None:
    from bunker_fixtures import artifact
    from hammunition.payloads import payload_name

    at = mirror_url(BUNKER, payload_path("debunit", PIN))
    seen: list[bytes] = []

    def check(path: Path) -> list[str]:
        seen.append(path.read_bytes())
        return []

    # Not cached: the Bunker must hold it, and the check sees what was fetched.
    context = make_context(tmp_path / "ctx", [artifact("debunit", payload_name(PIN), BODY)])
    routes = Routes({at: BODY})
    fetcher = Fetcher(
        tmp_path / "cache",
        transport=routes,
        mirror_transport=routes,
        mirror=BUNKER,
        offline=True,
    )
    backend = BinaryBackend(
        fetcher=fetcher,
        runner=NeverRuns(),
        build_root=tmp_path / "build",
        prefix=tmp_path / "prefix",
        context=context,
        dependency_check=check,
        isolation="bwrap",
    )
    manifest = _manifest()
    steps = backend.steps(manifest, manifest.install[0])
    for step in steps[:2]:
        perform(step)
    assert seen == [BODY] and routes.requested == [at]
