# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Task 17 (A11): the security-keys profile is apt-only and configures nothing.

Five new manifests (pcscd, opensc, fido2-tools, yubikey-manager, libpam-u2f)
land in one flat profile with no consent gate, no config_files, no
user_services, and no system_modifications anywhere in the set. Installing
`libpam-u2f` makes the PAM module available; it does not enable it --
CLAUDE.md's architecture invariant ("no executable logic in the catalog")
means that distinction has to be enforced here, not left to prose.
"""

from __future__ import annotations

from pathlib import Path

from hammunition.cli.main import load_all
from hammunition.manifest.schema import AptInstall


def test_security_keys_is_apt_only_and_never_configures_pam() -> None:
    root = Path(__file__).resolve().parents[1] / "catalog"
    catalog, profiles = load_all(root)
    profile = profiles["security-keys"]
    names = {"pcscd", "opensc", "fido2-tools", "yubikey-manager", "libpam-u2f"}
    assert set(profile.packages) == names and profile.consent is None
    for name in names:
        manifest = catalog[name]
        assert all(isinstance(block.install, AptInstall) for block in manifest.install)
        assert not manifest.config_files and not manifest.user_services
        doc = manifest.documentation
        assert doc.what_it_does and doc.why_you_want_it and doc.prerequisites
        assert doc.known_problems and doc.upstream_url and doc.upstream_support
    assert "PAM" in profile.documentation.deliberately_excludes


def test_security_keys_profile_page_fields_are_populated() -> None:
    root = Path(__file__).resolve().parents[1] / "catalog"
    _catalog, profiles = load_all(root)
    profile = profiles["security-keys"]
    doc = profile.documentation
    for field in (
        doc.what_it_installs,
        doc.why_together,
        doc.deliberately_excludes,
        doc.manual_configuration,
        doc.who_for,
        doc.hardware_assumed,
        doc.footprint_short,
        doc.excludes_short,
    ):
        assert field
    assert doc.goals
    assert doc.first_ten_minutes


def test_libpam_u2f_has_no_reconfigure_or_system_modifications() -> None:
    root = Path(__file__).resolve().parents[1] / "catalog"
    catalog, _profiles = load_all(root)
    manifest = catalog["libpam-u2f"]
    assert manifest.system_modifications == []
    assert manifest.debconf_selections == []
    assert manifest.reconfigure_after == []
    for block in manifest.install:
        assert isinstance(block.install, AptInstall)


def test_security_keys_rejects_a_package_not_in_the_five() -> None:
    """Negative case: a profile naming a sixth package is not this profile."""
    root = Path(__file__).resolve().parents[1] / "catalog"
    _catalog, profiles = load_all(root)
    profile = profiles["security-keys"]
    names = {"pcscd", "opensc", "fido2-tools", "yubikey-manager", "libpam-u2f"}
    assert "pcsc-tools" not in profile.packages
    assert set(profile.packages) == names
