# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``doctor``'s security-key and enrolled-key-strength checks (A12).

:func:`hammunition.doctor.security_key_checks` is a pure function of
:class:`hammunition.security_keys.SecurityKeyState`, tested here the same
way the rest of ``doctor`` is: fix one input, assert the verdict.
:func:`hammunition.security_keys.probe_security_keys` is the IO-shaped
counterpart, tested with a fake ``run`` that never touches a real token or
``pcscd``. The CLI's own subprocess adapter (``hammunition.cli.main.
_security_key_probe``) is bounded and read-only by construction; the tests
near the bottom of this file prove that against a real, temporary child
process, which is the one place in this file real subprocesses run — never a
real `fido2-token`, `opensc-tool` or `pcscd`.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from bunker_fixtures import key
from hammunition.backends.base import CommandResult
from hammunition.doctor import Check, security_key_checks
from hammunition.keystrength import KeyStrength, classify
from hammunition.security_keys import SecurityKeyState, probe_security_keys

cli = importlib.import_module("hammunition.cli.main")

# Captured at import time, before `_no_host_security_key_probe` (function
# scoped) has run for any test, so this alias keeps pointing at the real
# adapter even while that fixture replaces the module attribute for the
# duration of every other test in the suite (conftest.py).
BOUNDED_PROBE = cli._security_key_probe


def _by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def _runner(
    responses: dict[tuple[str, ...], CommandResult],
) -> tuple[Callable[[tuple[str, ...]], CommandResult], list[tuple[str, ...]]]:
    """A fake ``run`` that answers from *responses* and records every argv it
    was asked, so a test can assert the probe never went further than a
    read-only listing or liveness check."""
    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...]) -> CommandResult:
        calls.append(argv)
        try:
            return responses[argv]
        except KeyError:
            return CommandResult(argv=argv, returncode=127, stdout="", stderr="not stubbed")

    return run, calls


# -- doctor.security_key_checks: pure, given the brief's own test ------------


def test_tokens_absent_are_informational_and_weak_key_warns(tmp_path: Path) -> None:
    private = key(tmp_path, "rsa", 2048)
    strength = classify(private.with_suffix(".pub").read_text().strip())
    state = SecurityKeyState(False, False, False, (8, 1), None, None, True, (strength,))
    checks = security_key_checks(state)
    assert any(c.name == "FIDO2 token" and c.status == "info" for c in checks)
    assert any(c.name == "PIV token" and c.status == "info" for c in checks)
    assert any(c.name == "OpenSSH signing" and c.status == "warn" for c in checks)
    assert any(c.detail == strength.warning and c.status == "warn" for c in checks)
    assert not any(c.status == "fail" for c in checks)


def test_no_state_is_no_checks_at_all() -> None:
    assert security_key_checks(None) == []


def test_openssh_8_2_is_the_sk_signing_threshold() -> None:
    below = security_key_checks(SecurityKeyState(None, None, None, (8, 1), None, None, True, ()))
    at = security_key_checks(SecurityKeyState(None, None, None, (8, 2), None, None, True, ()))
    above = security_key_checks(SecurityKeyState(None, None, None, (9, 6), None, None, True, ()))
    assert _by_name(below)["OpenSSH signing"].status == "warn"
    assert _by_name(at)["OpenSSH signing"].status == "ok"
    assert _by_name(above)["OpenSSH signing"].status == "ok"


def test_malformed_ssh_dash_v_is_unparsed_and_warns() -> None:
    # The adapter could not make sense of what `ssh -V` printed, so there is
    # nothing to compare to the floor — that is a warn, not a crash.
    checks = security_key_checks(SecurityKeyState(None, None, None, None, None, None, True, ()))
    assert _by_name(checks)["OpenSSH signing"].status == "warn"


def test_no_enrolled_keys_produces_no_bunker_key_checks() -> None:
    checks = security_key_checks(SecurityKeyState(True, True, False, (9, 6), True, None, True, ()))
    assert "Bunker key" not in _by_name(checks)
    assert "Bunker key strength" not in _by_name(checks)


def test_file_key_and_hardware_stored_key_render_the_same_shape(tmp_path: Path) -> None:
    """`hardware_by_type` is how the key is *stored*, not something
    `security_key_checks` reads: a plain file key and a touch-affirmed
    hardware-stored key both render as one informational "Bunker key" line
    naming the fingerprint, algorithm and bit size."""
    file_key = KeyStrength("ssh-ed25519", 256, False, 1, False, None, "SHA256:file-key")
    hardware_key = KeyStrength(
        "sk-ecdsa-sha2-nistp256@openssh.com", 256, True, 2, False, None, "SHA256:piv-key"
    )
    checks = security_key_checks(
        SecurityKeyState(True, True, True, (9, 6), True, True, True, (file_key, hardware_key))
    )
    bunker_lines = [c for c in checks if c.name == "Bunker key"]
    assert len(bunker_lines) == 2
    assert {c.detail for c in bunker_lines} == {
        "SHA256:file-key: ssh-ed25519, 256 bits",
        "SHA256:piv-key: sk-ecdsa-sha2-nistp256@openssh.com, 256 bits",
    }
    assert not any(c.name == "Bunker key strength" for c in checks)


def test_root_run_reports_access_unmeasured_not_success_or_failure() -> None:
    """A root `doctor` cannot speak for the ordinary operator's udev access —
    this is "unmeasured", never a misleading ok or a misleading warn."""
    state = SecurityKeyState(True, True, True, (9, 6), None, None, False, ())
    checks = security_key_checks(state)
    fido_access = [c for c in checks if c.name == "FIDO2 access"]
    piv_access = [c for c in checks if c.name == "PIV access"]
    assert len(fido_access) == 1 and len(piv_access) == 1
    for c in (*fido_access, *piv_access):
        assert c.status == "info"
        assert "unmeasured" in c.detail
        assert "without sudo" in c.detail


def test_permission_denied_access_warns_naming_access_not_absence() -> None:
    """A permission-denied enumeration means the token's presence could not
    be measured either (`fido2_present=None`), but the denial itself is a
    warn naming access -- never a misleading "no token" info line standing
    in for a failure to even ask."""
    state = SecurityKeyState(True, None, None, (9, 6), False, None, True, ())
    checks = security_key_checks(state)
    assert _by_name(checks)["FIDO2 token"].detail == "detection unavailable"
    access = _by_name(checks)["FIDO2 access"]
    assert access.status == "warn"
    assert "access denied or unmeasured" in access.detail


def test_present_token_with_access_is_ok() -> None:
    state = SecurityKeyState(True, True, True, (9, 6), True, True, True, ())
    checks = security_key_checks(state)
    assert _by_name(checks)["FIDO2 access"].status == "ok"
    assert _by_name(checks)["PIV access"].status == "ok"


def test_json_and_text_render_the_same_warning_text() -> None:
    """D-059: nothing in the text is chrome the JSON view drops."""
    from hammunition.interface.doctor import build_doctor, render_doctor

    state = SecurityKeyState(False, None, None, (7, 9), False, None, True, ())
    checks = security_key_checks(state)
    doc = build_doctor(checks)
    text = "\n".join(render_doctor(doc))
    warn_details = [c.detail for c in checks if c.status == "warn"]
    assert warn_details, "this state should produce at least one warning"
    for detail in warn_details:
        assert detail in text
        assert any(v.detail == detail and v.status == "warn" for v in doc.checks)


# -- security_keys.probe_security_keys: the IO seam, with a fake `run` -------


def test_pcscd_active_is_measured_true() -> None:
    run, _ = _runner(
        {("systemctl", "is-active", "pcscd"): CommandResult(("systemctl",), 0, "active\n", "")}
    )
    state = probe_security_keys(run, as_operator=False, enrolled=())
    assert state.pcscd_active is True


def test_pcscd_inactive_but_socket_activatable_is_measured_false() -> None:
    # `systemctl is-active` on a socket-activated-but-not-yet-triggered unit
    # still answers rc 3 / "inactive": the socket exists, the daemon has not
    # spawned, and the measurement is honestly "not active right now".
    run, _ = _runner(
        {("systemctl", "is-active", "pcscd"): CommandResult(("systemctl",), 3, "inactive\n", "")}
    )
    state = probe_security_keys(run, as_operator=False, enrolled=())
    assert state.pcscd_active is False


def test_pcscd_unmeasurable_when_systemctl_itself_fails() -> None:
    run, _ = _runner(
        {
            ("systemctl", "is-active", "pcscd"): CommandResult(
                ("systemctl",), 127, "", "command not found"
            )
        }
    )
    state = probe_security_keys(run, as_operator=False, enrolled=())
    assert state.pcscd_active is None


def test_absent_tools_are_unavailable_not_no_token() -> None:
    """`fido2-token`/`opensc-tool` missing entirely must not be reported as
    "no token present" -- that is a different, false claim."""
    run, _ = _runner(
        {
            ("fido2-token", "-L"): CommandResult(
                ("fido2-token", "-L"), 127, "", "fido2-token: command not found"
            ),
            ("opensc-tool", "--list-readers"): CommandResult(
                ("opensc-tool", "--list-readers"), 127, "", "opensc-tool: command not found"
            ),
        }
    )
    state = probe_security_keys(run, as_operator=True, enrolled=())
    assert state.fido2_present is None
    assert state.piv_present is None
    assert state.fido2_access is None
    assert state.piv_access is None


def test_no_devices_listed_is_present_false_not_unavailable() -> None:
    run, _ = _runner(
        {
            ("fido2-token", "-L"): CommandResult(("fido2-token", "-L"), 0, "", ""),
            ("opensc-tool", "--list-readers"): CommandResult(
                ("opensc-tool", "--list-readers"), 0, "", ""
            ),
        }
    )
    state = probe_security_keys(run, as_operator=True, enrolled=())
    assert state.fido2_present is False
    assert state.piv_present is False


def test_permission_denied_fido2_listing_is_access_false_not_present_false() -> None:
    run, _ = _runner(
        {
            ("fido2-token", "-L"): CommandResult(
                ("fido2-token", "-L"), 1, "", "fido2-token: error: Permission denied opening device"
            ),
            ("opensc-tool", "--list-readers"): CommandResult(
                ("opensc-tool", "--list-readers"), 0, "", ""
            ),
        }
    )
    state = probe_security_keys(run, as_operator=True, enrolled=())
    assert state.fido2_present is None  # could not even measure presence
    assert state.fido2_access is False  # but the denial itself is known


def test_present_piv_driver_and_card_is_measured() -> None:
    run, _ = _runner(
        {
            ("fido2-token", "-L"): CommandResult(("fido2-token", "-L"), 0, "", ""),
            ("opensc-tool", "--list-readers"): CommandResult(
                ("opensc-tool", "--list-readers"),
                0,
                "0  Yubico YubiKey OTP+FIDO+CCID 00 00\n",
                "",
            ),
            ("opensc-tool", "--reader", "0", "--name"): CommandResult(
                ("opensc-tool", "--reader", "0", "--name"), 0, "PIV-II\n", ""
            ),
        }
    )
    state = probe_security_keys(run, as_operator=True, enrolled=())
    assert state.piv_present is True
    assert state.piv_access is True


def test_unrelated_smartcard_reader_is_not_counted_as_piv() -> None:
    run, _ = _runner(
        {
            ("fido2-token", "-L"): CommandResult(("fido2-token", "-L"), 0, "", ""),
            ("opensc-tool", "--list-readers"): CommandResult(
                ("opensc-tool", "--list-readers"),
                0,
                "0  Some Bank EMV Reader 00 00\n",
                "",
            ),
            ("opensc-tool", "--reader", "0", "--name"): CommandResult(
                ("opensc-tool", "--reader", "0", "--name"), 0, "EMV Payment Card\n", ""
            ),
        }
    )
    state = probe_security_keys(run, as_operator=True, enrolled=())
    assert state.piv_present is False
    assert state.piv_access is None


def test_permission_denied_piv_card_name_is_access_false() -> None:
    run, _ = _runner(
        {
            ("fido2-token", "-L"): CommandResult(("fido2-token", "-L"), 0, "", ""),
            ("opensc-tool", "--list-readers"): CommandResult(
                ("opensc-tool", "--list-readers"),
                0,
                "0  Some PIV Card 00 00\n",
                "",
            ),
            ("opensc-tool", "--reader", "0", "--name"): CommandResult(
                ("opensc-tool", "--reader", "0", "--name"), 1, "", "Permission denied"
            ),
        }
    )
    state = probe_security_keys(run, as_operator=True, enrolled=())
    assert state.piv_present is False
    assert state.piv_access is False


def test_root_cannot_measure_operator_access_so_as_operator_false_skips_probes() -> None:
    run, calls = _runner({})
    state = probe_security_keys(run, as_operator=False, enrolled=())
    assert state.probed_as_operator is False
    assert state.fido2_present is None and state.piv_present is None
    assert state.fido2_access is None and state.piv_access is None
    # Neither fido2-token nor opensc-tool was asked at all as root.
    assert all(argv[0] not in ("fido2-token", "opensc-tool") for argv in calls)


def test_openssh_version_parsed_from_stdout() -> None:
    run, _ = _runner(
        {
            ("ssh", "-V"): CommandResult(
                ("ssh", "-V"), 0, "OpenSSH_9.6p1 Debian-3, OpenSSL 3.0.13\n", ""
            )
        }
    )
    state = probe_security_keys(run, as_operator=False, enrolled=())
    assert state.openssh == (9, 6)


def test_openssh_version_parsed_from_stderr_only() -> None:
    # Most OpenSSH builds write the -V banner to stderr, not stdout.
    run, _ = _runner(
        {
            ("ssh", "-V"): CommandResult(
                ("ssh", "-V"), 0, "", "OpenSSH_8.2p1 Ubuntu-4ubuntu0.11, OpenSSL 1.1.1f\n"
            )
        }
    )
    state = probe_security_keys(run, as_operator=False, enrolled=())
    assert state.openssh == (8, 2)


def test_openssh_version_unparsed_is_none() -> None:
    run, _ = _runner({("ssh", "-V"): CommandResult(("ssh", "-V"), 0, "", "ssh: unknown option")})
    state = probe_security_keys(run, as_operator=False, enrolled=())
    assert state.openssh is None


def test_enrolled_keys_pass_through_untouched(tmp_path: Path) -> None:
    private = key(tmp_path)
    strength = classify(private.with_suffix(".pub").read_text().strip())
    run, _ = _runner({})
    state = probe_security_keys(run, as_operator=False, enrolled=(strength,))
    assert state.enrolled == (strength,)


def test_every_argv_is_a_read_only_listing_or_liveness_probe() -> None:
    """No argv this module ever builds can mint a PIN, request a touch
    signature, or change login/trust state -- it only lists and asks whether
    a listed device or reader answers."""
    run, calls = _runner(
        {
            ("systemctl", "is-active", "pcscd"): CommandResult(("x",), 0, "active\n", ""),
            ("ssh", "-V"): CommandResult(("x",), 0, "", "OpenSSH_9.6p1\n"),
            ("fido2-token", "-L"): CommandResult(
                ("x",), 0, "/dev/hidraw3: vendor=0x1050, product=0x0407\n", ""
            ),
            ("fido2-token", "-I", "/dev/hidraw3"): CommandResult(("x",), 0, "", ""),
            ("opensc-tool", "--list-readers"): CommandResult(
                ("x",), 0, "0  Yubico YubiKey 00 00\n", ""
            ),
            ("opensc-tool", "--reader", "0", "--name"): CommandResult(("x",), 0, "PIV-II\n", ""),
        }
    )
    probe_security_keys(run, as_operator=True, enrolled=())
    assert calls, "the probe made no calls at all"
    for argv in calls:
        assert argv[0] in {"systemctl", "ssh", "fido2-token", "opensc-tool"}, argv
        if argv[0] == "fido2-token":
            assert argv[1] in ("-L", "-I"), argv
        if argv[0] == "opensc-tool":
            assert argv[1] in ("--list-readers", "--reader"), argv
        if argv[0] == "systemctl":
            assert argv[1:] == ("is-active", "pcscd"), argv
        if argv[0] == "ssh":
            assert argv[1:] == ("-V",), argv


# -- the CLI's gather function: the owner-aware mirror store ----------------


def test_no_bunker_enrolled_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli,
        "_security_key_probe",
        lambda argv: CommandResult(argv=argv, returncode=127, stdout="", stderr="unavailable"),
    )
    state, error = cli._security_keys_for_doctor(argparse.Namespace())
    assert error is None
    assert state is not None and state.enrolled == ()


def test_a_corrupt_mirror_store_is_a_warn_check_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A12: `doctor` never crashes on a mirror.json it cannot trust -- it
    names the problem and the remedy (`hammunition mirror status`)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli,
        "_security_key_probe",
        lambda argv: CommandResult(argv=argv, returncode=127, stdout="", stderr="unavailable"),
    )
    store = tmp_path / "hammunition"
    store.mkdir(parents=True)
    bad = store / "mirror.json"
    bad.write_text("{}")
    bad.chmod(0o644)  # signers.read_state requires exactly 0600
    state, error = cli._security_keys_for_doctor(argparse.Namespace())
    assert state is not None  # the other probes still ran
    assert error is not None
    assert error.status == "warn"
    assert "hammunition mirror status" in (error.fix or "")


# -- the CLI's own bounded subprocess adapter --------------------------------
#
# These are the one place in this file a real subprocess runs -- a short-
# lived, temporary Python child this test writes itself, never a real
# `fido2-token`/`opensc-tool`/`pcscd`. The autouse `_no_host_security_key_probe`
# fixture in conftest.py blocks the guarded `cli.main._security_key_probe`
# attribute for every other test in the suite; these call the real function
# directly through the module-level alias captured above, before that fixture
# ever ran.


def test_probe_flood_stops_at_output_cap(tmp_path: Path) -> None:
    child = tmp_path / "flood.py"
    child.write_text(
        "import os\nwhile True:\n os.write(1, b'x' * 4096)\n os.write(2, b'y' * 4096)\n"
    )
    result = BOUNDED_PROBE((sys.executable, str(child)))
    assert result.returncode != 0 and "64 KiB" in result.stderr
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 65536 + 128


def test_probe_missing_program_is_a_nonzero_result_not_an_exception() -> None:
    result = BOUNDED_PROBE(("hammunition-doctor-probe-does-not-exist-xyz",))
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr != ""


def test_probe_times_out_rather_than_hanging(tmp_path: Path) -> None:
    started = time.monotonic()
    result = BOUNDED_PROBE((sys.executable, "-c", "import time; time.sleep(30)"))
    elapsed = time.monotonic() - started
    assert result.returncode != 0
    assert "timed out" in result.stderr
    assert elapsed < 10, f"the probe ran for {elapsed:.1f}s past its 5-second deadline"


def test_probe_returns_the_childs_real_output_and_status(tmp_path: Path) -> None:
    child = tmp_path / "echo.py"
    child.write_text(
        "import sys\nsys.stdout.write('hello-out')\nsys.stderr.write('hello-err')\nsys.exit(3)\n"
    )
    result = BOUNDED_PROBE((sys.executable, str(child)))
    assert result.returncode == 3
    assert result.stdout == "hello-out"
    assert result.stderr == "hello-err"
