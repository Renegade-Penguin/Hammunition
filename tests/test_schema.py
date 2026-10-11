# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Manifest schema tests.

Two halves:

* **Shape tests** — each of the seven shapes from the real inventory must be
  expressible, and must resolve to the right thing.
* **Rejection tests** — the schema must make certain mistakes *impossible*, not
  merely discouraged. A schema that accepts an unverified download is not
  enforcing D-004, it is documenting it.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hammunition.manifest.load import CatalogError, load_catalog  # noqa: E402
from hammunition.manifest.schema import (  # noqa: E402
    AptInstall,
    DerivedDataInstall,
    GitInstall,
    ManifestError,
    PackageManifest,
    SourceInstall,
    Status,
    derived_source_method_problem,
)

CATALOG = REPO_ROOT / "catalog" / "packages"

Catalog = dict[str, PackageManifest]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(CATALOG)


def _minimal(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "example",
        "version": "1.0",
        "summary": "An example package",
        "categories": ["digital-modes"],
        "install": [{"install": {"method": "apt", "packages": ["example"]}}],
        "update": {"probe": {"method": "apt_policy"}},
        "documentation": {
            "what_it_does": "Does an example thing for the purposes of testing.",
            "why_you_want_it": "Because the test suite requires a valid manifest.",
            "upstream_url": "https://example.invalid/",
        },
    }
    base.update(overrides)
    return base


# ===========================================================================
# The whole catalog loads
# ===========================================================================


def test_catalog_loads(catalog: Catalog) -> None:
    """Every YAML in catalog/packages/ parses and validates.

    Asserts against the directory rather than a hardcoded count: a literal here
    fails on every manifest added, which trains people to bump the number
    without reading why it changed.
    """
    on_disk = {p.stem for p in CATALOG.glob("*.yaml")}
    assert on_disk, "no manifests found"
    assert set(catalog) == on_disk


def test_every_manifest_has_documentation(catalog: Catalog) -> None:
    """CLAUDE.md: an undocumented package cannot ship."""
    for name, m in catalog.items():
        assert m.documentation.what_it_does, name
        assert m.documentation.why_you_want_it, name
        assert m.documentation.upstream_url, name


def test_every_remote_artifact_is_verified(catalog: Catalog) -> None:
    """D-004: no unverified downloads anywhere in the catalog."""
    for name, m in catalog.items():
        for block in m.install:
            inst = block.install
            if isinstance(inst, DerivedDataInstall):
                # Its `source` names the catalog unit it converts, not a download;
                # that unit's own fetch is what gets verified.
                continue
            artifact = getattr(inst, "source", None) or getattr(inst, "artifact", None)
            if artifact is not None:
                assert len(artifact.sha256) == 64, f"{name} has an unverified artifact"


# ===========================================================================
# Shape 1 — install METHOD varies by distro, not just its argument
# ===========================================================================


def test_shape1_js8call_method_varies_by_distro(catalog: Catalog) -> None:
    js8 = catalog["js8call"]

    mint = js8.resolve("linuxmint", "22.3", "x86_64")
    other = js8.resolve("debian", "13", "x86_64")

    assert mint is not None and other is not None
    assert isinstance(mint.install, AptInstall)
    assert mint.install.packages == ["js8call"]
    assert isinstance(other.install, GitInstall)
    assert other.install.build_system == "cmake"


def test_shape1_selector_precision(catalog: Catalog) -> None:
    """Mint 22.4 must NOT get the Qt-6.4 workaround."""
    js8 = catalog["js8call"]
    block = js8.resolve("linuxmint", "22.4", "x86_64")
    assert block is not None
    # Since 3.0.3 the default path is a pinned git build (upstream stopped
    # publishing source tarballs); the assertion is about the SELECTOR, so
    # what matters is that Mint 22.4 falls through to the build path.
    assert isinstance(block.install, GitInstall)


# ===========================================================================
# Shape 2 — provides + conflicts_with_repo_package
# ===========================================================================


def test_shape2_fldigi_provides_flarq(catalog: Catalog) -> None:
    fldigi = catalog["fldigi"]
    assert "flarq" in fldigi.provides
    assert {b.install_as for b in fldigi.binaries} == {"fldigi", "flarq"}


def test_shape2_fldigi_declares_repo_conflict(catalog: Catalog) -> None:
    """The purge is destructive, so it must be declared, not implicit."""
    assert catalog["fldigi"].conflicts_with_repo_package == ["fldigi"]


def test_provides_may_not_list_self() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(_minimal(provides=["example"]))


# ===========================================================================
# Shape 3 — the wsjtx binary collision
# ===========================================================================


def test_shape3_binary_collision_is_declared_away(catalog: Catalog) -> None:
    """The two WSJT-X builds must not overwrite each other. Since 3.2.0 the
    improved fork ships a vendor .deb owning /usr/bin/wsjtx while stock
    builds from source into /usr/local -- distinct paths by construction,
    and the improved manifest documents the PATH shadowing. What this test
    still owes: stock keeps its explicit name, and the improved unit's
    manifest says how to reach its binary."""
    a, b = catalog["wsjtx"], catalog["wsjtx-improved"]
    assert a.binaries and a.binaries[0].install_as == "wsjtx"
    assert "usr/bin/wsjtx" in (b.documentation.known_problems or "")


def test_shape3_ordering_is_declared(catalog: Catalog) -> None:
    assert catalog["wsjtx-improved"].after == ["wsjtx"]


def test_shape3_only_one_is_the_default(catalog: Catalog) -> None:
    assert catalog["wsjtx"].recommended_default is True
    assert catalog["wsjtx-improved"].recommended_default is False


def test_duplicate_install_as_is_rejected() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                binaries=[
                    {"produced": "a", "install_as": "same"},
                    {"produced": "b", "install_as": "same"},
                ]
            )
        )


# ===========================================================================
# Shape 4 — per-architecture project file
# ===========================================================================


def test_shape4_mshv_project_file_per_arch(catalog: Catalog) -> None:
    mshv = catalog["mshv"]
    arm = mshv.resolve("debian", "13", "aarch64")
    x86 = mshv.resolve("debian", "13", "x86_64")

    assert arm is not None and x86 is not None
    assert isinstance(arm.install, SourceInstall)
    assert isinstance(x86.install, SourceInstall)
    assert arm.install.project_file == "MSHV_ARM_PI.pro"
    assert x86.install.project_file == "MSHV_x86_64.pro"
    # Same archive, same hash — only the project file differs.
    assert arm.install.source.sha256 == x86.install.source.sha256


def test_shape4_unsupported_arch_resolves_to_none(catalog: Catalog) -> None:
    """Honest gaps, not shims. CLAUDE.md capability matrix."""
    assert catalog["mshv"].resolve("debian", "13", "armv7l") is None


# ===========================================================================
# Shape 5 — retired, with a verdict we own
# ===========================================================================


def test_shape5_noaa_apt_is_retired_with_provenance(catalog: Catalog) -> None:
    n = catalog["noaa-apt"]
    assert n.status is Status.retired
    assert n.retire_reason is not None
    assert n.retire_reason.value == "world_changed"
    assert n.status_date == date(2025, 11, 9)
    assert n.status_verdict is not None  # D-005: never inherit a verdict
    assert n.status_verdict.value == "tested"
    assert n.superseded_by == "satdump"


def test_shape5_retired_stays_in_the_catalog(catalog: Catalog) -> None:
    """PARITY-POLICY: users who go looking must find an explanation, not silence."""
    assert "noaa-apt" in catalog
    assert catalog["noaa-apt"].documentation.why_you_want_it


@pytest.mark.parametrize("missing", ["status_reason", "status_date", "status_verdict"])
def test_non_supported_status_requires_provenance(missing: str) -> None:
    fields = {
        "status": "retired",
        "retire_reason": "world_changed",
        "status_reason": "The signal source no longer exists.",
        "status_date": "2025-11-09",
        "status_verdict": "tested",
    }
    fields.pop(missing)
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(_minimal(**fields))


def test_retired_requires_a_reason_code() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                status="retired",
                status_reason="Because.",
                status_date="2025-11-09",
                status_verdict="tested",
            )
        )


# ===========================================================================
# Shape 6 — the remote script is unrepresentable
# ===========================================================================


def test_shape6_ais_catcher_uses_a_pinned_ref(catalog: Catalog) -> None:
    block = catalog["ais-catcher"].install[0]
    assert isinstance(block.install, GitInstall)
    assert block.install.ref == "v0.70"
    assert block.build_depends, "dependencies must be declared, not discovered"


def test_shape6_no_script_method_exists() -> None:
    """There is no way to say 'pipe this URL into bash'."""
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                install=[{"install": {"method": "script", "url": "https://example.invalid/i.sh"}}]
            )
        )


def test_moving_git_refs_are_rejected() -> None:
    for ref in ("master", "main", "HEAD", "develop"):
        with pytest.raises((ValidationError, ManifestError)):
            PackageManifest.model_validate(
                _minimal(
                    install=[
                        {
                            "install": {
                                "method": "git",
                                "repo": "https://example.invalid/x",
                                "ref": ref,
                                "build_system": "cmake",
                            }
                        }
                    ]
                )
            )


def test_source_without_sha256_is_rejected() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                install=[
                    {
                        "install": {
                            "method": "source",
                            "source": {"url": "https://example.invalid/x.tar.gz"},
                            "build_system": "cmake",
                        }
                    }
                ]
            )
        )


def test_malformed_sha256_is_rejected() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                install=[
                    {
                        "install": {
                            "method": "source",
                            "source": {
                                "url": "https://example.invalid/x.tar.gz",
                                "sha256": "deadbeef",
                            },
                            "build_system": "cmake",
                        }
                    }
                ]
            )
        )


# ===========================================================================
# Shape 7 — configurable service endpoint
# ===========================================================================


def test_shape7_backend_is_a_field_not_a_launcher_constant(catalog: Catalog) -> None:
    hc = catalog["hamclock-next"]
    endpoint = next(e for e in hc.service_endpoints if e.name == "backend")

    assert endpoint.user_configurable
    assert endpoint.default_url == "https://ohb.works"
    # The dead host must not appear anywhere in the manifest.
    assert "hamclock.com" not in hc.launchers[0].exec


def test_shape7_launcher_references_endpoint_symbolically(catalog: Catalog) -> None:
    exec_line = catalog["hamclock-next"].launchers[0].exec
    assert "{endpoint:backend}" in exec_line


def test_launcher_may_not_reference_an_undeclared_endpoint() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(launchers=[{"name": "x", "exec": "x --backend {endpoint:nope}"}])
        )


# ===========================================================================
# Shape 8 — templated station-local configuration
#
# Q-005/D-008: the packet core is admitted "with configuration, not merely
# installation". linbpq is the first manifest to need it, which makes
# DESIGN.md §15.5 (station-local config) concrete rather than deferred.
# ===========================================================================


def test_shape8_linbpq_declares_station_variables(catalog: Catalog) -> None:
    bpq = catalog["linbpq"]
    assert bpq.station_variables == {"callsign", "node_alias", "grid_square"}


#: Units whose config files are about the machine, not the operator, and so
#: carry no station variable. Listed by name, with why, so that a new one is
#: a reviewed decision rather than a template that forgot `{station.callsign}`.
STATIONLESS_CONFIG = {
    # D-072: a GPS reference-clock line for chrony and a gpsd.service drop-in
    # giving gpsd `-n`. Nothing in either names the operator or the station.
    "chrony",
}


def test_shape8_no_callsign_is_hardcoded(catalog: Catalog) -> None:
    """Operator identity must be a variable, never a literal in the catalog."""
    stationless = set()
    for name, manifest in catalog.items():
        for cfg in manifest.config_files:
            if "{station." in cfg.template:
                continue
            stationless.add(name)
            assert name in STATIONLESS_CONFIG, (
                f"{name}: config template has no station variable — check it is "
                f"not hardcoding operator-specific data, and list it in "
                f"STATIONLESS_CONFIG with why if it genuinely has none"
            )
    assert stationless == STATIONLESS_CONFIG, (
        f"STATIONLESS_CONFIG names {sorted(STATIONLESS_CONFIG - stationless)}, "
        f"which no longer write a file without a station variable; drop them"
    )


def test_shape8_config_files_are_backed_up(catalog: Catalog) -> None:
    """We write to /etc on the operator's behalf; D-016 says say so and be
    reversible."""
    for name, manifest in catalog.items():
        for cfg in manifest.config_files:
            assert cfg.backup_existing, f"{name}: {cfg.path} would be clobbered"


def test_bpq_builds_from_a_pinned_tag_not_a_mirror(catalog: Catalog) -> None:
    """Q-005: GPL-3.0-or-later with upstream tags, so no mirror and no
    `unverifiable` status is warranted."""
    block = catalog["linbpq"].install[0]
    assert isinstance(block.install, GitInstall)
    # A tag, not a branch or a mirror path: the pin moves on purpose (#95).
    assert re.fullmatch(r"\d+\.\d+", block.install.ref), block.install.ref
    assert "cantab.net" not in block.install.repo
    assert catalog["linbpq"].status is Status.supported


# ===========================================================================
# Cross-cutting rules
# ===========================================================================


def test_unconditional_block_may_not_shadow_later_blocks() -> None:
    """First-match-wins means a default block placed early silently wins."""
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                install=[
                    {"install": {"method": "apt", "packages": ["a"]}},
                    {
                        "when": {"arch": ["aarch64"]},
                        "install": {"method": "apt", "packages": ["b"]},
                    },
                ]
            )
        )


def test_irreversible_modification_must_explain_itself() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                system_modifications=[
                    {
                        "kind": "file_shadow",
                        "description": "Replaces distro librtlsdr",
                        "detail": "rm -fr /usr/lib/librtlsdr*",
                        "reversible": False,
                    }
                ]
            )
        )


def test_file_capability_modification_names_a_binary_and_known_capabilities() -> None:
    manifest = PackageManifest.model_validate(
        _minimal(
            binaries=[{"produced": "radio-node", "install_as": "radio-node"}],
            system_modifications=[
                {
                    "kind": "file_capability",
                    "binary": "radio-node",
                    "capabilities": ["CAP_NET_RAW", "CAP_NET_ADMIN"],
                    "description": "Allow optional Ethernet packet ports",
                    "detail": "These privileges are not needed for KISS ports.",
                    "reversible": True,
                }
            ],
        )
    )

    modification = manifest.system_modifications[0]
    assert modification.binary == "radio-node"
    assert modification.capabilities == ["CAP_NET_RAW", "CAP_NET_ADMIN"]


@pytest.mark.parametrize(
    "modification",
    [
        {
            "kind": "file_capability",
            "capabilities": ["CAP_NET_RAW"],
            "description": "Missing binary",
            "detail": "A binary must be named.",
            "reversible": True,
        },
        {
            "kind": "file_capability",
            "binary": "radio-node",
            "capabilities": ["CAP_NOT_REAL"],
            "description": "Unknown capability",
            "detail": "Only known Linux capabilities are accepted.",
            "reversible": True,
        },
        {
            "kind": "file_capability",
            "binary": "radio-node",
            "capabilities": ["CAP_NET_RAW"],
            "description": "Irreversible grant",
            "detail": "Uninstall must be able to clear this.",
            "reversible": False,
            "reverse_hint": "setcap -r radio-node",
        },
        {
            "kind": "group_membership",
            "group": "dialout",
            "binary": "radio-node",
            "capabilities": ["CAP_NET_RAW"],
            "description": "Misplaced fields",
            "detail": "Capability fields are only valid for file capabilities.",
            "reversible": True,
        },
    ],
)
def test_file_capability_modification_rejects_incomplete_or_misplaced_fields(
    modification: dict[str, Any],
) -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(_minimal(system_modifications=[modification]))


def test_third_party_repo_requires_a_pinned_key() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(
            _minimal(
                apt_repos=[
                    {
                        "name": "example",
                        "uri": "https://ppa.invalid/",
                        "suites": ["stable"],
                        "components": ["main"],
                        "key_url": "https://ppa.invalid/key.asc",
                        "rationale": "Needed for the thing.",
                    }
                ]
            )
        )


def test_categories_are_a_non_empty_list() -> None:
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(_minimal(categories=[]))


def test_unquoted_two_component_version_is_rejected() -> None:
    """YAML parses `version: 1.5` as a float, and `0.70` silently becomes 0.7.
    Fail loudly rather than shipping a wrong version string (D-016)."""
    data = yaml.safe_load("version: 0.70\n")
    assert data["version"] == 0.7, "YAML float coercion (this is the trap)"
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(_minimal(version=0.7))


def test_extra_fields_are_rejected() -> None:
    """Typos in field names must fail, not be silently ignored."""
    with pytest.raises((ValidationError, ManifestError)):
        PackageManifest.model_validate(_minimal(catagories=["typo"]))


# ===========================================================================
# Loader behaviour
# ===========================================================================


def test_loader_reports_all_failures_not_just_the_first(tmp_path: Path) -> None:
    """D-016: resolve everything, then report together."""
    (tmp_path / "a.yaml").write_text("name: 'A'\n")
    (tmp_path / "b.yaml").write_text("name: 'B'\n")
    with pytest.raises(CatalogError) as exc:
        load_catalog(tmp_path)
    assert len(exc.value.failures) == 2


def test_toolkit_risk_register_is_queryable(catalog: Catalog) -> None:
    """D-015: 'what breaks when Debian drops Qt5?' must be answerable from data."""
    at_risk = {
        name for name, m in catalog.items() if any(t.framework == "qt5" for t in m.toolkit_risk)
    }
    assert {"wsjtx", "wsjtx-improved", "mshv"} <= at_risk
    for name in at_risk:
        for risk in catalog[name].toolkit_risk:
            assert risk.checked, f"{name}: register entry must record when it was checked"


def test_toolkit_register_is_multi_framework(catalog: Catalog) -> None:
    """D-015 is generic, not Qt5-specific. The register must prove that."""
    frameworks = {t.framework for m in catalog.values() for t in m.toolkit_risk}
    assert len(frameworks) > 1, f"register only covers {frameworks}"
    assert "gtk2" in frameworks


def test_no_path_is_distinct_from_not_yet_ported(catalog: Catalog) -> None:
    """The distinction that carries the value: glfer's GTK2 has nowhere to go,
    which is a different problem from a port that simply has not happened."""
    glfer = catalog["glfer"]
    gtk2 = next(t for t in glfer.toolkit_risk if t.framework == "gtk2")
    assert gtk2.upstream_port_status == "no_path"

    wsjtx = catalog["wsjtx"]
    qt5 = next(t for t in wsjtx.toolkit_risk if t.framework == "qt5")
    assert qt5.upstream_port_status == "in_progress"

    # The register must actually distinguish states, not just carry one.
    # (Asserting `gtk2.status != qt5.status` directly is tautological once mypy
    # narrows both to distinct literals — strict_equality catches that.)
    states = {t.upstream_port_status for m in catalog.values() for t in m.toolkit_risk}
    assert len(states) > 1, f"register collapses to a single state: {states}"
    assert "no_path" in states


def test_compiler_flags_are_recorded_not_rediscovered(catalog: Catalog) -> None:
    """PARITY-POLICY 'CARRY with attention': the flags are catalog data."""
    install = catalog["glfer"].install[0].install
    assert isinstance(install, SourceInstall)
    flags = install.compiler_flags
    assert "-Wno-incompatible-pointer-types" in flags
    assert len(flags) == 3


# ---------------------------------------------------------------------------
# The catalog cross-checks against itself
# ---------------------------------------------------------------------------


def test_every_profile_package_has_a_manifest() -> None:
    """Q-011 option C, rejected: a profile naming packages nothing defines.

    `load_profiles` has always been able to catch this, but only when handed the
    package catalog -- and nothing handed it one, so the shipped catalog spent a
    round with nine dangling references and a passing test suite. The check is
    worthless unless something runs it against the real catalog.
    """
    from hammunition.manifest.load import load_catalog, load_profiles

    packages = load_catalog(CATALOG)
    profiles = load_profiles(CATALOG.parent / "profiles", packages)
    assert profiles, "no profiles loaded"
    for profile in profiles.values():
        missing = [p for p in profile.packages if p not in packages]
        assert not missing, f"profile {profile.name} names undefined packages: {missing}"


def test_every_profile_package_can_actually_be_installed() -> None:
    """A profile is a promise that `hammunition install <name>` gets you
    somewhere. A package whose only install method this engine cannot perform
    breaks that promise at plan time, after the operator has committed.

    The maintainer's bar for 1.0 is explicit: getting a user most of the way is
    a success, but there must be **no true blocker** other than configuration
    they supply. An unimplementable method is a true blocker, so it is checked
    rather than hoped for. Packages that need an unwritten backend stay out of
    profiles and are named in each profile's `deliberately_excludes`.
    """
    from hammunition.backends import IMPLEMENTED_METHODS
    from hammunition.manifest.load import load_catalog, load_profiles

    packages = load_catalog(CATALOG)
    profiles = load_profiles(CATALOG.parent / "profiles", packages)
    blocked = [
        (profile.name, name, sorted({b.install.method for b in packages[name].install}))
        for profile in profiles.values()
        for name in profile.packages
        if not {b.install.method for b in packages[name].install} & IMPLEMENTED_METHODS
    ]
    assert not blocked, (
        "profile packages with no install method this engine can perform: "
        f"{blocked}. Either implement the backend or take the package out of "
        "the profile and say so in deliberately_excludes."
    )


def test_the_thirteen_1_0_profiles_exist() -> None:
    """docs/reference/profile-sizing.md argued a specific set and the
    maintainer accepted it on 2026-08-29. Renaming a profile later breaks
    every shell history, forum post and document that names it, so the set is
    asserted rather than left to drift. `security-keys` joined the 1.0 set
    under Task 17 (A11) -- an intentional, documented twelfth-to-thirteenth
    addition, not drift."""
    from hammunition.manifest.load import load_catalog, load_profiles

    packages = load_catalog(CATALOG)
    profiles = load_profiles(CATALOG.parent / "profiles", packages)
    shipped = {name for name, p in profiles.items() if p.stage == "1.0"}
    expected = {
        "station",
        "logging",
        "morse",
        "propagation",
        "digital-modes",
        "packet",
        "satellite",
        "antenna",
        "sdr",
        "listening",
        "electronics",
        "rf-security",
        "security-keys",
    }
    assert shipped == expected, (
        f"1.0 profile set changed. missing: {sorted(expected - shipped)}; "
        f"unexpected: {sorted(shipped - expected)}"
    )


# ---------------------------------------------------------------------------
# D-024 — a commit pin carries no upstream signal, so it carries ours
# ---------------------------------------------------------------------------

COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA = "36ea9a143422f5b374371461667ff53fb9387300"


def _git_install(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "method": "git",
        "repo": "https://example.invalid/thing",
        "ref": SHA,
        "build_system": "cmake",
    }
    data.update(overrides)
    return data


def _review(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "last_reviewed": "2026-08-26",
        "reviewed_by": "someone",
        "rationale": "Matches the commit the distributions package, verified to resolve.",
        "basis": "distribution_pin",
        "distributions": ["kali", "parrot"],
    }
    data.update(overrides)
    return data


def test_a_commit_pin_needs_a_review() -> None:
    from hammunition.manifest.schema import GitInstall

    with pytest.raises((ManifestError, ValidationError)):
        GitInstall.model_validate(_git_install())


def test_a_reviewed_commit_pin_is_accepted() -> None:
    from hammunition.manifest.schema import GitInstall

    install = GitInstall.model_validate(_git_install(pin_review=_review()))
    assert install.pin_review is not None


def test_a_tag_needs_no_review_because_upstream_made_the_judgement() -> None:
    from hammunition.manifest.schema import GitInstall

    assert GitInstall.model_validate(_git_install(ref="v4.21611")).pin_review is None


def test_a_tag_may_not_carry_a_review() -> None:
    """Not pedantry: it would read as though someone vetted the revision choice."""
    from hammunition.manifest.schema import GitInstall

    with pytest.raises((ManifestError, ValidationError)):
        GitInstall.model_validate(_git_install(ref="v4.21611", pin_review=_review()))


def test_a_rationale_must_say_something() -> None:
    """'HEAD at the time' is the absence of a rationale, not a short one."""
    from hammunition.manifest.schema import GitInstall

    with pytest.raises((ManifestError, ValidationError)):
        GitInstall.model_validate(_git_install(pin_review=_review(rationale="HEAD")))


def test_overdue_is_computed_from_the_recorded_cadence() -> None:
    from datetime import date as _date

    from hammunition.manifest.schema import PinReview

    review = PinReview.model_validate(_review(cadence_days=30))
    assert review.due == _date(2026, 9, 25)
    assert not review.is_overdue(_date(2026, 9, 25))
    assert review.is_overdue(_date(2026, 9, 26))


def test_every_commit_pin_in_the_catalog_has_a_review() -> None:
    """Time-independent on purpose. Staleness is checked on a schedule, not here."""
    from hammunition.manifest.schema import GitInstall

    for name, manifest in load_catalog(CATALOG).items():
        for block in manifest.install:
            if isinstance(block.install, GitInstall) and COMMIT_SHA_RE.match(block.install.ref):
                assert block.install.pin_review is not None, f"{name} pins a SHA with no review"


def test_a_distribution_pin_must_name_the_distributions() -> None:
    from hammunition.manifest.schema import GitInstall

    with pytest.raises((ManifestError, ValidationError)):
        GitInstall.model_validate(
            _git_install(pin_review=_review(basis="distribution_pin", distributions=[]))
        )


def test_an_own_choice_pin_may_not_name_distributions() -> None:
    """If one packages it, the basis is distribution_pin -- that is the whole point."""
    from hammunition.manifest.schema import GitInstall

    with pytest.raises((ManifestError, ValidationError)):
        GitInstall.model_validate(
            _git_install(pin_review=_review(basis="own_choice", distributions=["kali"]))
        )


def test_an_own_choice_pin_needs_a_fuller_rationale() -> None:
    """Choosing a revision nobody else vetted is the expensive path."""
    from hammunition.manifest.schema import GitInstall

    with pytest.raises((ManifestError, ValidationError)):
        GitInstall.model_validate(
            _git_install(
                pin_review=_review(
                    basis="own_choice",
                    distributions=[],
                    rationale="Latest commit that builds cleanly on Debian 13.",
                )
            )
        )


def test_an_own_choice_pin_is_accepted_when_it_says_what_was_checked() -> None:
    from hammunition.manifest.schema import GitInstall

    install = GitInstall.model_validate(
        _git_install(
            pin_review=_review(
                basis="own_choice",
                distributions=[],
                rationale=(
                    "Checked Debian 13, Kali rolling, Parrot and Ubuntu 26.04 on "
                    "2026-08-26: none packages this project at all. Chosen as the "
                    "last commit before the Qt6 migration, which does not build "
                    "against the Qt5 our stable targets ship."
                ),
            )
        )
    )
    assert install.pin_review is not None
    assert install.pin_review.basis == "own_choice"


def test_the_catalogs_pins_prefer_a_distribution() -> None:
    """Not a hard rule -- own_choice is legitimate -- but worth noticing if it drifts."""
    from hammunition.manifest.schema import GitInstall

    for name, manifest in load_catalog(CATALOG).items():
        for block in manifest.install:
            review = getattr(block.install, "pin_review", None)
            if isinstance(block.install, GitInstall) and review and review.basis == "own_choice":
                assert len(review.rationale) >= 80, name


# ---------------------------------------------------------------------------
# Issue #31 -- corrections from YAAC's author
#
# yaac depended on default-jre-headless and generated a launcher. The install
# confirmed clean on Debian 13, and the launcher died on every click with
# java.awt.HeadlessException: the headless JRE ships no AWT. Nothing in the
# effect checks can run a GUI, so the catalog itself has to refuse the shape.


def _java_deps(manifest: PackageManifest) -> list[str]:
    return [d for d in manifest.depends if "-jre" in d or "-jdk" in d]


def test_a_unit_with_a_launcher_does_not_depend_on_a_headless_jre_alone(catalog: Catalog) -> None:
    offenders: list[str] = []
    for name, manifest in sorted(catalog.items()):
        if not manifest.launchers:
            continue
        java = _java_deps(manifest)
        if java and all(d.endswith("-headless") for d in java):
            offenders.append(f"{name}: depends {java}")
    assert not offenders, (
        "a launcher needs a display and a headless JRE has no AWT -- the wrapper "
        "raises java.awt.HeadlessException on every click (issue #31, measured on "
        "Debian 13 under Xvfb). Depend on the full JRE instead: " + "; ".join(offenders)
    )


def test_a_label_file_probe_names_the_file() -> None:
    data = _minimal(
        update={
            "probe": {
                "method": "label_file",
                "url": "https://www.ka2ddo.org/ka2ddo/YAACBuildLabel.txt",
            },
            "strategy": "reinstall",
        }
    )
    manifest = PackageManifest.model_validate(data)
    assert manifest.update.probe.url == "https://www.ka2ddo.org/ka2ddo/YAACBuildLabel.txt"


def test_a_label_file_probe_without_a_url_is_refused() -> None:
    data = _minimal(update={"probe": {"method": "label_file"}})
    with pytest.raises((ManifestError, ValidationError), match=r"label_file.*url"):
        PackageManifest.model_validate(data)


def test_a_probe_url_belongs_to_a_label_file_probe_only() -> None:
    data = _minimal(update={"probe": {"method": "none", "url": "https://example.invalid/x.txt"}})
    with pytest.raises((ManifestError, ValidationError), match="only a label_file probe"):
        PackageManifest.model_validate(data)


# ---------------------------------------------------------------------------
# Recommends, per unit (D-052, issue #61)
# ---------------------------------------------------------------------------


def test_an_apt_unit_installs_recommends_by_default() -> None:
    """The global default is what every target distribution does, and this
    field does not touch it."""
    block = AptInstall.model_validate({"method": "apt", "packages": ["morse"]})
    assert block.install_recommends is True


def test_an_apt_unit_may_opt_out_of_recommends() -> None:
    """Debian's `morse` Recommends pulseaudio, which Conflicts pipewire-alsa."""
    block = AptInstall.model_validate(
        {"method": "apt", "packages": ["morse"], "install_recommends": False}
    )
    assert block.install_recommends is False


# ===========================================================================
# osm-regions and derived (navigation piece 1, Task 4)
# ===========================================================================


def test_osm_regions_block_parses() -> None:
    m = PackageManifest.model_validate(
        _minimal(
            name="osm-regions",
            install=[
                {
                    "install": {
                        "method": "osm-regions",
                        "provider": "geofabrik",
                        "licence": "ODbL-1.0",
                        "licence_url": "https://www.openstreetmap.org/copyright",
                    }
                }
            ],
        )
    )
    assert m.install[0].install.method == "osm-regions"


def test_derived_block_needs_its_source_in_depends() -> None:
    block = {
        "method": "derived",
        "converter": "navit-maptool",
        "source": "osm-regions",
        "licence": "ODbL-1.0",
        "licence_url": "https://www.openstreetmap.org/copyright",
    }
    with pytest.raises(ValidationError, match="depends"):
        PackageManifest.model_validate(_minimal(name="osm-navit", install=[{"install": block}]))
    ok = _minimal(name="osm-navit", install=[{"install": block}])
    ok["depends"] = ["osm-regions", "maptool"]
    assert PackageManifest.model_validate(ok).install[0].install.method == "derived"


def test_a_converter_outside_the_enum_is_refused() -> None:
    block = {
        "method": "derived",
        "converter": "sh -c 'rm -rf /'",
        "source": "osm-regions",
        "licence": "ODbL-1.0",
        "licence_url": "https://www.openstreetmap.org/copyright",
    }
    bad = _minimal(name="osm-navit", install=[{"install": block}])
    bad["depends"] = ["osm-regions"]
    with pytest.raises(ValidationError):
        PackageManifest.model_validate(bad)


def test_derived_block_needs_its_boundaries_unit_in_depends_too() -> None:
    """The address-search fix (D-057 amendment): the boundary file is another
    unit's data, read at conversion time, so the plan must install it first."""
    block = {
        "method": "derived",
        "converter": "navit-maptool",
        "source": "osm-regions",
        "boundaries": "country-boundaries",
        "licence": "ODbL-1.0",
        "licence_url": "https://www.openstreetmap.org/copyright",
    }
    bad = _minimal(name="osm-navit", install=[{"install": block}])
    bad["depends"] = ["osm-regions"]
    with pytest.raises(ValidationError, match="country-boundaries"):
        PackageManifest.model_validate(bad)
    bad["depends"] = ["osm-regions", "country-boundaries"]
    parsed = PackageManifest.model_validate(bad).install[0].install
    assert isinstance(parsed, DerivedDataInstall) and parsed.boundaries == "country-boundaries"


# ===========================================================================
# dem-tiles and piece 2's converters (D-061)
# ===========================================================================

_COP = {
    "licence": "Copernicus DEM licence",
    "licence_url": "https://copernicus-dem-30m.s3.amazonaws.com/readme.html",
}


def test_dem_tiles_block_parses_and_names_its_provider_by_enum() -> None:
    m = PackageManifest.model_validate(
        _minimal(
            name="dem-copernicus",
            install=[{"install": {"method": "dem-tiles", "provider": "copernicus-glo30", **_COP}}],
        )
    )
    assert m.install[0].install.method == "dem-tiles"
    with pytest.raises(ValidationError):
        PackageManifest.model_validate(
            _minimal(
                name="dem-copernicus",
                install=[{"install": {"method": "dem-tiles", "provider": "srtm", **_COP}}],
            )
        )


def test_a_dem_tiles_licence_url_must_be_https() -> None:
    with pytest.raises(ValidationError, match="https"):
        PackageManifest.model_validate(
            _minimal(
                name="dem-copernicus",
                install=[
                    {
                        "install": {
                            "method": "dem-tiles",
                            "licence": "Copernicus DEM licence",
                            "licence_url": "http://example.invalid/",
                        }
                    }
                ],
            )
        )


@pytest.mark.parametrize(
    ("converter", "source"),
    [
        ("mkgmap", "osm-regions"),
        ("routino-planetsplitter", "osm-regions"),
        ("gdal-dem", "dem-copernicus"),
    ],
)
def test_piece_2_converters_are_enum_members(converter: str, source: str) -> None:
    data = _minimal(
        name="derived-unit",
        install=[
            {
                "install": {
                    "method": "derived",
                    "converter": converter,
                    "source": source,
                    "licence": "ODbL-1.0",
                    "licence_url": "https://www.openstreetmap.org/copyright",
                }
            }
        ],
    )
    data["depends"] = [source]
    block = PackageManifest.model_validate(data).install[0].install
    assert isinstance(block, DerivedDataInstall) and block.converter == converter


def test_dem_tiles_is_an_implemented_method_and_uninstall_removes_its_tree(
    tmp_path: Path,
) -> None:
    from hammunition.backends import IMPLEMENTED_METHODS
    from hammunition.distro import Target
    from hammunition.state.uninstall import RemovalPaths, plan_removal

    assert "dem-tiles" in IMPLEMENTED_METHODS
    manifest = PackageManifest.model_validate(
        _minimal(
            name="dem-copernicus",
            install=[{"install": {"method": "dem-tiles", "provider": "copernicus-glo30", **_COP}}],
        )
    )
    paths = RemovalPaths(
        prefix=tmp_path / "prefix",
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        applications_dir=tmp_path / "applications",
    )
    directory = paths.prefix / "share" / "hammunition" / "data" / "dem-copernicus"
    directory.mkdir(parents=True)
    (directory / "Copernicus_DSM_COG_10_N00_00_E000_00_DEM.tif").write_bytes(b"x")
    plan = plan_removal(
        ["dem-copernicus"],
        catalog={"dem-copernicus": manifest},
        profiles={},
        target=Target(distro="debian", version="13", arch="x86_64"),
        attributed=frozenset(),
        states={},
        paths=paths,
    )
    assert [(a.kind, a.path) for a in plan.artifacts["dem-copernicus"]] == [("tree", directory)]


# ===========================================================================
# converter <-> source-method compatibility, catalog-wide (D-061 review fix)
# ===========================================================================


def _osm_regions_manifest(name: str = "osm-regions") -> PackageManifest:
    return PackageManifest.model_validate(
        _minimal(
            name=name,
            install=[
                {
                    "install": {
                        "method": "osm-regions",
                        "provider": "geofabrik",
                        "licence": "ODbL-1.0",
                        "licence_url": "https://www.openstreetmap.org/copyright",
                    }
                }
            ],
        )
    )


def _dem_tiles_manifest(name: str = "dem-copernicus") -> PackageManifest:
    return PackageManifest.model_validate(
        _minimal(
            name=name,
            install=[{"install": {"method": "dem-tiles", "provider": "copernicus-glo30", **_COP}}],
        )
    )


_USGS = {
    "licence": "Public domain (USGS)",
    "licence_url": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
}


def _topo_quads_manifest(name: str = "usgs-ustopo") -> PackageManifest:
    return PackageManifest.model_validate(
        _minimal(
            name=name,
            install=[{"install": {"method": "topo-quads", "provider": "usgs-ustopo", **_USGS}}],
        )
    )


def _derived_manifest(converter: str, source: str) -> PackageManifest:
    data = _minimal(
        name="derived-unit",
        install=[
            {
                "install": {
                    "method": "derived",
                    "converter": converter,
                    "source": source,
                    "licence": "ODbL-1.0",
                    "licence_url": "https://www.openstreetmap.org/copyright",
                }
            }
        ],
    )
    data["depends"] = [source]
    return PackageManifest.model_validate(data)


@pytest.mark.parametrize(
    ("converter", "source"),
    [
        ("navit-maptool", _osm_regions_manifest()),
        ("mkgmap", _osm_regions_manifest()),
        ("routino-planetsplitter", _osm_regions_manifest()),
        ("gdal-dem", _dem_tiles_manifest()),
        ("ustopo-mosaic", _topo_quads_manifest()),
    ],
)
def test_a_converter_fed_its_required_source_method_is_not_a_problem(
    converter: str, source: PackageManifest
) -> None:
    derived = _derived_manifest(converter, source.name)
    assert derived_source_method_problem(derived, {source.name: source}) is None


def test_gdal_dem_fed_an_osm_regions_source_is_refused_by_name() -> None:
    source = _osm_regions_manifest()
    derived = _derived_manifest("gdal-dem", source.name)
    problem = derived_source_method_problem(derived, {source.name: source})
    assert problem is not None
    assert "derived-unit" in problem
    assert "gdal-dem" in problem
    assert "osm-regions" in problem
    assert "dem-tiles" in problem


def test_mkgmap_fed_a_dem_tiles_source_is_refused_by_name() -> None:
    source = _dem_tiles_manifest()
    derived = _derived_manifest("mkgmap", source.name)
    problem = derived_source_method_problem(derived, {source.name: source})
    assert problem is not None
    assert "derived-unit" in problem
    assert "mkgmap" in problem
    assert "dem-copernicus" in problem
    assert "osm-regions" in problem


def test_a_source_missing_from_the_catalog_is_not_this_functions_problem() -> None:
    """`_derived_source_is_in_depends` (or a plain unresolved name) owns that case."""
    derived = _derived_manifest("gdal-dem", "no-such-unit")
    assert derived_source_method_problem(derived, {}) is None


def test_load_catalog_refuses_a_mismatched_converter_and_source(tmp_path: Path) -> None:
    """Wired catalog-wide through `load.load_catalog`, the same way D-060's
    `_desktop_alternative_problem` is."""
    (tmp_path / "dem-copernicus.yaml").write_text(
        """\
name: dem-copernicus
version: "1.0"
summary: Elevation tiles
categories: [digital-modes]
install:
  - install:
      method: dem-tiles
      provider: copernicus-glo30
      licence: Copernicus DEM licence
      licence_url: https://copernicus-dem-30m.s3.amazonaws.com/readme.html
update:
  probe:
    method: apt_policy
documentation:
  what_it_does: Elevation tiles for the operator's chosen map regions.
  why_you_want_it: Hillshade, slope and contour lines on the map.
  upstream_url: https://example.invalid/
"""
    )
    (tmp_path / "osm-garmin.yaml").write_text(
        """\
name: osm-garmin
version: "1.0"
summary: Garmin image
categories: [digital-modes]
depends: [dem-copernicus]
install:
  - install:
      method: derived
      converter: mkgmap
      source: dem-copernicus
      licence: ODbL-1.0
      licence_url: https://www.openstreetmap.org/copyright
update:
  probe:
    method: apt_policy
documentation:
  what_it_does: A Garmin .img map image built from a region's data.
  why_you_want_it: Trails, tracks and terrain on a handheld GPS.
  upstream_url: https://example.invalid/
"""
    )
    with pytest.raises(CatalogError, match="osm-garmin") as exc:
        load_catalog(tmp_path)
    assert "mkgmap" in str(exc.value) and "osm-regions" in str(exc.value)


# ===========================================================================
# D-068: official topographic sheets
# ===========================================================================


def test_topo_quads_is_an_implemented_method_and_uninstall_removes_its_tree(
    tmp_path: Path,
) -> None:
    from hammunition.backends import IMPLEMENTED_METHODS
    from hammunition.distro import Target
    from hammunition.state.uninstall import RemovalPaths, plan_removal

    assert "topo-quads" in IMPLEMENTED_METHODS
    manifest = _topo_quads_manifest()
    paths = RemovalPaths(
        prefix=tmp_path / "prefix",
        venv_root=tmp_path / "venvs",
        bin_dir=tmp_path / "bin",
        applications_dir=tmp_path / "applications",
    )
    directory = paths.prefix / "share" / "hammunition" / "data" / "usgs-ustopo"
    directory.mkdir(parents=True)
    (directory / "ZZ_Alpha_20240101.tif").write_bytes(b"x")
    plan = plan_removal(
        ["usgs-ustopo"],
        catalog={"usgs-ustopo": manifest},
        profiles={},
        target=Target(distro="debian", version="13", arch="x86_64"),
        attributed=frozenset(),
        states={},
        paths=paths,
    )
    assert [(a.kind, a.path) for a in plan.artifacts["usgs-ustopo"]] == [("tree", directory)]


def test_a_topo_quads_block_takes_no_provider_but_its_enum() -> None:
    with pytest.raises(ValueError):
        PackageManifest.model_validate(
            _minimal(
                name="usgs-ustopo",
                install=[{"install": {"method": "topo-quads", "provider": "usfs-geopdf", **_USGS}}],
            )
        )
    with pytest.raises(ValueError, match="https"):
        PackageManifest.model_validate(
            _minimal(
                name="usgs-ustopo",
                install=[
                    {
                        "install": {
                            "method": "topo-quads",
                            "licence": "Public domain",
                            "licence_url": "http://www.usgs.gov/",
                        }
                    }
                ],
            )
        )


def test_ustopo_mosaic_fed_a_dem_tiles_source_is_refused_by_name() -> None:
    source = _dem_tiles_manifest()
    derived = _derived_manifest("ustopo-mosaic", source.name)
    problem = derived_source_method_problem(derived, {source.name: source})
    assert problem is not None and "topo-quads" in problem and "ustopo-mosaic" in problem
