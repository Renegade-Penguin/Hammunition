# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""``hammunition doctor`` — is this machine ready, and what is not yet set up.

A read-only health check. It changes nothing and it is the first thing to run
on a fresh machine or when something misbehaves: it turns the failures the
engine would otherwise hit mid-transaction into a report you read up front,
each with the one command that fixes it.

The checks are a **pure function** of explicit inputs so they can be tested
without a real machine; the CLI gathers the inputs (detects the target, probes
for tools, reads the catalog and station) and renders the result. Nothing here
runs a subprocess or touches the filesystem.

Severity has four levels, and the distinction is the point:

- ``fail`` — the engine cannot work until this is fixed (no catalog, not a
  Debian-family system).
- ``warn`` — a whole class of installs will fail or a feature is unavailable
  until this is fixed (no venv support, no compiler, callsign unset), but the
  engine runs and other installs work.
- ``info`` — a true fact worth stating that is not a problem (no ham hardware
  attached right now; udev rules not yet applied on a machine with no radios).
- ``ok`` — checked and healthy.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hammunition import geoclue
from hammunition.desktop import Desktop, describe, describe_set
from hammunition.geoclue import GeoClueState
from hammunition.gpstime.mode import GPS_MODES
from hammunition.gpstime.state import HOLDOVER_WARN_SECONDS, TimeState, format_duration
from hammunition.hardware.gps_resume import ResumeStatus
from hammunition.security_keys import SecurityKeyState

#: routino-common's file QMapShack reads at startup (D-061).
ROUTINO_TRANSLATIONS = "/usr/share/routino/translations.xml"

# What scripts/path-link.sh links to: a link ending here is ours (D-059).
ENGINE_LINK_SUFFIX = "/.venv/bin/hammunition"

# Executables used as the first argv element of doctor command fixes.
FIX_PROGRAMS = frozenset({"hammunition", "journalctl", "ln", "sudo", "systemctl"})

__all__ = [
    "FIX_PROGRAMS",
    "Check",
    "RigStatus",
    "Status",
    "rig_checks",
    "run_checks",
    "security_key_checks",
    "summarize",
    "writable_or_creatable",
]

Status = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True)
class Check:
    """One thing looked at, its verdict, and how to fix it if it is not ok."""

    name: str
    status: Status
    detail: str
    fix: str | None = None
    fix_argv: list[str] | None = None


@dataclass(frozen=True)
class RigStatus:
    """What the CLI gathered about the rig service, read-only.  D-073 §9.

    Every field is a fact read without keying the transmitter; the CLI fills
    them (systemctl --user, /proc, a \\dump_state probe), and :func:`rig_checks`
    turns them into checks. Fields are None when not applicable or not read.
    """

    configured: bool
    """Whether the station names a rig at all."""
    missing: tuple[str, ...] = ()
    """Station values the rig's kind needs that are unset."""
    uncatalogued: bool = False
    kind: str | None = None
    """``cat`` or ``ptt_only`` when known."""
    owner: str = "rigctld"
    """``rigctld`` (the shared daemon) or ``flrig``. A flrig station runs no
    rigctld service, so its absence is expected, not a fault."""
    vox: bool = False
    """A PTT-only rig keyed by VOX runs no service either."""
    service_state: str | None = None
    """``absent``, ``disabled``, ``active`` or ``failed``; None when not read."""
    proxy_state: str | None = None
    """The loopback filter's state, same values; None when not read (D-073 §11)."""
    args_match: bool | None = None
    """Whether the running rigctld's arguments match the station; None if not running."""
    answering: bool | None = None
    """Whether \\dump_state got a reply on the proxy port 4532; None if not probed."""
    loopback_only: bool | None = None
    """Whether 4532 is bound to 127.0.0.1 only; None if not read (/proc/net/tcp)."""
    device_present: bool | None = None
    """Whether the rig_device path resolves to a node now; None if not checked."""
    linger: str | None = None
    """``off``, ``ours`` or ``theirs``; None when not read."""


def _runs_a_service(status: RigStatus) -> bool:
    """Whether this station is expected to run the rigctld user service at all.

    flrig owns the port itself, and a VOX-keyed PTT-only rig needs nothing
    running — so for either, the service being absent is correct, not a fault
    (review I5)."""
    return status.owner != "flrig" and not (status.kind == "ptt_only" and status.vox)


def rig_checks(status: RigStatus | None) -> list[Check]:
    """The rig service's health, read-only and never keying.  D-073 §9."""
    if status is None:
        return []
    if not status.configured:
        return [
            Check(
                "rig",
                "info",
                "no station rig is set; the shared rigctld is not configured",
                "hammunition station set --rig <device> --rig-device <path> …",
            )
        ]
    checks: list[Check] = []
    if status.missing:
        checks.append(
            Check(
                "rig",
                "warn",
                f"the rig is set but these values are not: {', '.join(status.missing)}",
                "hammunition station set "
                + " ".join(f"--{m.replace('_', '-')} …" for m in status.missing),
            )
        )
    if status.uncatalogued:
        checks.append(
            Check("rig", "info", "the rig is a hamlib:<model> value, unmeasured here", None)
        )
    if not _runs_a_service(status):
        # flrig or VOX: no rigctld service is expected, so its state is not a
        # fault and the "install rig-service" fix would do nothing (review I5).
        if status.owner == "flrig":
            checks.append(
                Check("rig", "info", "the rig is owned by flrig; no rigctld service runs")
            )
        else:
            checks.append(Check("rig", "info", "the rig is keyed by VOX; no rigctld service runs"))
        return checks
    state = status.service_state
    if state == "absent":
        checks.append(
            Check(
                "rig",
                "warn",
                "hammunition-rigctld is not installed",
                "hammunition install rig-service",
                ["hammunition", "install", "rig-service"],
            )
        )
    elif state == "disabled":
        checks.append(
            Check(
                "rig",
                "warn",
                "hammunition-rigctld is disabled",
                "hammunition install rig-service",
                ["hammunition", "install", "rig-service"],
            )
        )
    elif state == "failed":
        checks.append(
            Check(
                "rig",
                "warn",
                "hammunition-rigctld failed",
                "journalctl --user -u hammunition-rigctld -n 20",
                ["journalctl", "--user", "-u", "hammunition-rigctld", "-n", "20"],
            )
        )
    elif state == "active":
        checks.append(Check("rig", "ok", "hammunition-rigctld is active"))
    if status.proxy_state == "active":
        checks.append(Check("rig", "ok", "the loopback filter is active on 127.0.0.1:4532"))
    elif status.proxy_state in ("absent", "disabled", "failed"):
        checks.append(
            Check(
                "rig",
                "warn",
                f"the loopback filter (hammunition-rig-proxy) is {status.proxy_state}; "
                f"without it a web page can reach rigctld",
                "hammunition install rig-service",
                ["hammunition", "install", "rig-service"],
            )
        )
    if status.args_match is False:
        checks.append(
            Check(
                "rig",
                "warn",
                "the running rigctld's arguments do not match the station",
                "reinstall rig-service: hammunition install rig-service",
                ["hammunition", "install", "rig-service"],
            )
        )
    if status.answering is True:
        checks.append(Check("rig", "ok", "rigctld answers through the filter on 127.0.0.1:4532"))
    elif status.answering is False and state == "active":
        checks.append(
            Check(
                "rig",
                "warn",
                "rigctld is active but did not answer \\dump_state on 127.0.0.1:4532",
                "journalctl --user -u hammunition-rigctld -n 20",
                ["journalctl", "--user", "-u", "hammunition-rigctld", "-n", "20"],
            )
        )
    if status.loopback_only is False:
        checks.append(
            Check(
                "rig",
                "fail",
                "port 4532 is bound beyond loopback — the transmitter is reachable off-machine",
                "reinstall rig-service: hammunition install rig-service",
                ["hammunition", "install", "rig-service"],
            )
        )
    if status.device_present is False:
        checks.append(
            Check("rig", "info", "the rig's device is not present now", "switch the radio on")
        )
    if status.linger == "ours":
        checks.append(Check("rig", "info", "linger is on (Hammunition turned it on)"))
    elif status.linger == "theirs":
        checks.append(Check("rig", "info", "linger is on (not turned on by Hammunition)"))
    return checks


def security_key_checks(state: SecurityKeyState | None) -> list[Check]:
    """Hardware signing readiness and enrolled key strength, read-only.  A12.

    Pure: every fact is already in *state*, gathered by the CLI's probe. A
    present FIDO2/PIV token is informational (not every operator wants one);
    a *weak* enrolled key, or OpenSSH too old for `-sk` signing, is a warn."""
    if state is None:
        return []
    out: list[Check] = []
    fix = "hammunition install security-keys"
    out.append(
        Check(
            "pcscd",
            "ok" if state.pcscd_active else "warn",
            "PC/SC daemon active" if state.pcscd_active else "PC/SC daemon inactive or unavailable",
            fix if not state.pcscd_active else None,
        )
    )
    for label, present in (("FIDO2 token", state.fido2_present), ("PIV token", state.piv_present)):
        if present is None:
            detail = "detection unavailable"
        else:
            detail = "detected" if present else "not detected"
        out.append(Check(label, "info", detail))
    ready = state.openssh is not None and state.openssh >= (8, 2)
    out.append(
        Check(
            "OpenSSH signing",
            "ok" if ready else "warn",
            "OpenSSH supports -sk signing" if ready else "OpenSSH 8.2+ required for -sk signing",
        )
    )
    for label, present, access in (
        ("FIDO2 access", state.fido2_present, state.fido2_access),
        ("PIV access", state.piv_present, state.piv_access),
    ):
        if not state.probed_as_operator:
            out.append(
                Check(
                    label,
                    "info",
                    "user access unmeasured: run hammunition doctor as yourself, without sudo",
                )
            )
        elif present or access is False:
            out.append(
                Check(
                    label,
                    "ok" if access else "warn",
                    "token reachable without root"
                    if access
                    else "token access denied or unmeasured; inspect the archive's device rules",
                )
            )
    for strength in state.enrolled:
        out.append(
            Check(
                "Bunker key",
                "info",
                f"{strength.fingerprint}: {strength.algorithm}, {strength.bits} bits",
            )
        )
        if strength.warning is not None:
            out.append(Check("Bunker key strength", "warn", strength.warning))
    return out


def run_checks(
    *,
    target_describe: str | None,
    is_debian_family: bool,
    catalog_counts: tuple[int, int] | None,
    has_venv_module: bool,
    path_has_local_bin: bool,
    tools: dict[str, bool],
    groups_now: frozenset[str],
    needed_groups: list[str],
    station_set: bool,
    rules_applied: bool,
    attached_recognised: int,
    log_dir_writable: bool,
    run_logs: tuple[int, int, str, str] | None = None,
    engine_on_path: str | None,
    engine_expected: str,
    engine_found: str | None,
    engine_found_in_local_bin: bool,
    engine_found_link: str | None,
    engine_linked_in_local_bin: bool = False,
    engine_versions: tuple[str | None, str | None] | None = None,
    kept_attached: tuple[str, ...] = (),
    kept_absent: tuple[str, ...] = (),
    desktops_installed: frozenset[Desktop] | None = None,
    desktop_current: Desktop | None = None,
    sessions_unrecognised: tuple[str, ...] = (),
    qmapshack_without_translations: bool = False,
    time_state: TimeState | None = None,
    gps_resume: ResumeStatus | None = None,
    launchers_ok: tuple[str, ...] = (),
    launchers_bare: tuple[str, ...] = (),
    launchers_broken: tuple[tuple[str, str], ...] = (),
    launchers_shadowing: tuple[tuple[str, str], ...] = (),
    rig: RigStatus | None = None,
    geoclue_state: GeoClueState | None = None,
    security_keys: SecurityKeyState | None = None,
) -> list[Check]:
    """Every check, in the order a person should read them. Pure; see module docstring."""
    checks: list[Check] = []

    if target_describe is None:
        checks.append(
            Check(
                "system",
                "fail",
                "could not read /etc/os-release — cannot tell what this machine is",
                "run on a Debian-family system (Parrot, Debian, Ubuntu, Kali, Raspberry Pi OS)",
            )
        )
    elif not is_debian_family:
        checks.append(
            Check(
                "system",
                "fail",
                f"{target_describe} is not Debian-family; nothing here applies",
                "Hammunition augments a Debian-family install; use one of the supported targets",
            )
        )
    else:
        checks.append(Check("system", "ok", target_describe))

    if catalog_counts is None:
        checks.append(
            Check(
                "catalog",
                "fail",
                "the catalog could not be found or loaded",
                "run from the git checkout, or pass --catalog / set HAMMUNITION_CATALOG",
            )
        )
    else:
        packages, profiles = catalog_counts
        checks.append(Check("catalog", "ok", f"{packages} packages, {profiles} profiles loaded"))

    if has_venv_module:
        checks.append(Check("python venv", "ok", "python3 -m venv is available"))
    else:
        checks.append(
            Check(
                "python venv",
                "warn",
                "python3 -m venv is missing — venv and hybrid installs will fail",
                "sudo apt install python3-venv",
                ["sudo", "apt", "install", "python3-venv"],
            )
        )

    if path_has_local_bin:
        checks.append(Check("PATH", "ok", "~/.local/bin is on PATH"))
    else:
        checks.append(
            Check(
                "PATH",
                "warn",
                "~/.local/bin is not on PATH — venv-installed programs will look missing",
                "log out and back in, or add ~/.local/bin to PATH; it is added when the dir first appears",
            )
        )

    # `hammunition` itself on the PATH, and resolving to this checkout (D-059).
    # Without it every short command in the docs says "command not found",
    # which is how the field laptop met it; with it pointing at a different
    # checkout, a fix made here is not the engine that runs.
    if engine_on_path == engine_expected:
        checks.append(Check("hammunition", "ok", f"on PATH: {engine_expected}"))
    elif engine_on_path is None and engine_linked_in_local_bin and not path_has_local_bin:
        # A fresh account: bootstrap made the link, and ~/.local/bin reaches
        # PATH only at the next login. Re-running bootstrap changes nothing.
        checks.append(
            Check(
                "hammunition",
                "warn",
                "~/.local/bin/hammunition links to this checkout, "
                "but ~/.local/bin is not on PATH yet",
                'log out and back in, or run export PATH="$HOME/.local/bin:$PATH" for this shell',
            )
        )
    elif engine_on_path is None:
        checks.append(
            Check(
                "hammunition",
                "warn",
                "`hammunition` is not on PATH — commands in the docs will say command not found",
                "re-run ./bootstrap.sh, which links ~/.local/bin/hammunition to this checkout",
            )
        )
    elif engine_found is not None and not engine_found_in_local_bin:
        # Shadowed from earlier on PATH: relinking ~/.local/bin would not clear it.
        where = "before ~/.local/bin on PATH" if path_has_local_bin else "on PATH"
        checks.append(
            Check(
                "hammunition",
                "warn",
                f"`hammunition` is {engine_found}, found {where}, "
                f"and runs {engine_on_path}, not this checkout's {engine_expected}",
                f"inspect it with `ls -l {shlex.quote(engine_found)}`; remove or rename it "
                "yourself, or put ~/.local/bin ahead of its directory on PATH",
            )
        )
    elif engine_found is not None and (engine_found_link or "").endswith(ENGINE_LINK_SUFFIX):
        # Our own link, to another checkout: the one case where switching is safe.
        checks.append(
            Check(
                "hammunition",
                "warn",
                f"`hammunition` on PATH runs {engine_on_path}, not this checkout's {engine_expected}",
                f"ln -sfn {shlex.quote(engine_expected)} {shlex.quote(engine_found)}",
                ["ln", "-sfn", engine_expected, engine_found],
            )
        )
    else:
        # A file or link bootstrap did not make (a pipx install, a wrapper):
        # named, never replaced, exactly as scripts/path-link.sh leaves it.
        shown = engine_found or "~/.local/bin/hammunition"
        runs = "" if engine_on_path == shown else f" (it runs {engine_on_path})"
        checks.append(
            Check(
                "hammunition",
                "warn",
                f"{shown} is not a link bootstrap made and shadows this checkout{runs}",
                f"inspect it with `ls -l {shlex.quote(shown)}`; if you no longer want it, "
                "move it aside yourself, then re-run ./bootstrap.sh",
            )
        )

    # The venv's installed version against the checkout's pyproject (#311): an
    # editable install keeps the version it was installed at until bootstrap
    # re-runs, so a release bump leaves it behind and the console refuses to start.
    if engine_versions is not None and engine_versions[0] is not None:
        tree, installed = engine_versions
        if installed == tree:
            checks.append(Check("engine version", "ok", f"{tree}, installed and checkout agree"))
        else:
            checks.append(
                Check(
                    "engine version",
                    "warn",
                    f"{tree} (checkout), {installed or 'not installed'} (installed): "
                    "the venv lags the tree",
                    "hammunition self-update",
                    ["hammunition", "self-update"],
                )
            )

    if tools.get("cc", False):
        checks.append(Check("compiler", "ok", "a C toolchain is present for source builds"))
    else:
        checks.append(
            Check(
                "compiler",
                "warn",
                "no C compiler found — the ~57 source-built units cannot build",
                "sudo apt install build-essential (the engine also pulls per-build deps at plan time)",
                ["sudo", "apt", "install", "build-essential"],
            )
        )

    if tools.get("git", False):
        checks.append(Check("git", "ok", "git is present for git-source builds"))
    else:
        checks.append(
            Check(
                "git",
                "warn",
                "git is missing — git-source units cannot be fetched",
                "sudo apt install git (the planner also injects it as a build dep)",
                ["sudo", "apt", "install", "git"],
            )
        )

    if station_set:
        checks.append(Check("station", "ok", "callsign and grid are set"))
    else:
        checks.append(
            Check(
                "station",
                "warn",
                "no callsign/grid set — packet and logging configs are deferred until you set them",
                "hammunition station set --callsign YOURCALL --grid-square AB12cd",
            )
        )

    missing_groups = [g for g in needed_groups if g not in groups_now]
    if not needed_groups:
        pass
    elif not missing_groups:
        checks.append(Check("device groups", "ok", "in every device-access group"))
    else:
        checks.append(
            Check(
                "device groups",
                "warn",
                f"not in: {', '.join(missing_groups)} — devices needing them will be permission-denied",
                "hammunition hardware apply (then log out and back in)",
                ["hammunition", "hardware", "apply"],
            )
        )

    if rules_applied:
        checks.append(Check("udev rules", "ok", "the catalog's udev rules are installed"))
    else:
        checks.append(
            Check(
                "udev rules",
                "info",
                "udev rules not yet applied (fine until you connect a supported device)",
                "hammunition hardware apply",
                ["hammunition", "hardware", "apply"],
            )
        )

    if kept_absent:
        names = ", ".join(kept_absent)
        checks.append(
            Check(
                "kept off",
                "warn",
                f"kept parked but not attached: {names}",
                "; ".join(f"`hammunition hardware wake {n}` clears it" for n in kept_absent),
            )
        )
    elif kept_attached:
        checks.append(
            Check("kept off", "info", f"parked across reboots: {', '.join(kept_attached)}")
        )

    if attached_recognised > 0:
        checks.append(
            Check(
                "hardware",
                "ok",
                f"{attached_recognised} catalogued device(s) attached — see `hardware list`",
            )
        )
    else:
        checks.append(Check("hardware", "info", "no catalogued devices attached right now"))

    # D-060. Information either way: which desktops is a fact, not a fault.
    # The session files are what the planner decides against; the session's
    # own desktop is what the menu and the tray are about, and sudo drops it.
    # Files that name no desktop the catalog knows (COSMIC, Sway) are named,
    # so a graphical machine is never described as a server.
    if desktops_installed is not None:
        consequence = (
            "a unit for one desktop, such as the Plasma tray, is deferred from a "
            "profile and refused by name here"
        )
        files = ", ".join(sessions_unrecognised)
        if not desktops_installed and sessions_unrecognised:
            detail = (
                f"session files name none of the desktops the catalog knows "
                f"(read: {files}); {consequence}"
            )
        elif not desktops_installed:
            detail = f"no desktop session files (a server or a container); {consequence}"
        else:
            offer = f"session files offer {describe_set(desktops_installed)}"
            if sessions_unrecognised:
                names = "names" if len(sessions_unrecognised) == 1 else "name"
                offer += f"; also {files}, which {names} no desktop the catalog knows"
            if desktop_current is not None:
                detail = f"{offer}; this session is {describe(desktop_current)}"
            else:
                detail = (
                    f"{offer}; this session's desktop is not known (XDG_CURRENT_DESKTOP "
                    f"is unset or unrecognised, and sudo usually drops it)"
                )
        checks.append(Check("desktops", "info", detail))

    # D-061: QMapShack stops at startup with a modal "The specified
    # translations XML file did not exist" when routino-common's file is
    # missing. apt supplies it through libroutino0; this names it when not.
    if qmapshack_without_translations:
        checks.append(
            Check(
                "qmapshack",
                "warn",
                f"QMapShack is installed and {ROUTINO_TRANSLATIONS} is missing; QMapShack "
                f"stops at startup until it is back",
                "sudo apt-get install --reinstall routino-common",
                ["sudo", "apt-get", "install", "--reinstall", "routino-common"],
            )
        )

    if time_state is not None:
        checks += _time_checks(time_state)

    if geoclue_state is not None:
        checks += _geoclue_checks(geoclue_state)
    # Issue #177: with a GPS receiver attached and gpsd installed, whether the
    # resume step `hardware apply` installs is there. None: not applicable.
    if gps_resume is not None:
        checks.append(_gps_resume_check(gps_resume))

    # Issue #145: a generated launcher runs the engine by absolute path,
    # because a menu entry started as a systemd user service has no
    # ~/.local/bin on PATH. One written before that says bare `hammunition`;
    # one whose checkout moved names a path that is gone. Either fails from
    # the menu with "not found", status 127, and nothing else says so.
    # Issue #174: a generated launcher named like a binary on the PATH runs
    # in that binary's place from every shell -- `rigctl -l` opened the
    # dummy-rig shell on the field laptop.
    if launchers_broken or launchers_bare or launchers_shadowing:
        parts = [
            f"{launcher} runs {target}, which is gone or not executable"
            for launcher, target in launchers_broken
        ]
        parts += [
            f"{launcher} runs `hammunition` by bare name, which the desktop menu "
            f"reports as not found"
            for launcher in launchers_bare
        ]
        parts += [
            f"{launcher} shadows {binary}: a shell typing `{Path(launcher).name}` runs "
            f"the launcher, which ignores its arguments"
            for launcher, binary in launchers_shadowing
        ]
        fixes: list[str] = []
        if launchers_broken:
            fixes.append(
                "run ./bootstrap.sh in the checkout you use (it relinks "
                "~/.local/bin/hammunition), then `hammunition menus apply`, which "
                "rewrites the launchers with that engine's path"
            )
        elif launchers_bare:
            fixes.append("hammunition menus apply (it rewrites them with the engine's full path)")
        if launchers_shadowing:
            fixes.append(
                "hammunition menus apply (it removes a launcher that shadows a PATH binary "
                "and writes the catalog's renamed one)"
            )
        checks.append(Check("launchers", "warn", "; ".join(parts), "; ".join(fixes)))
    elif launchers_ok:
        count = len(launchers_ok)
        noun = "launcher runs" if count == 1 else "launchers run"
        checks.append(Check("launchers", "ok", f"{count} {noun} hammunition by a path that exists"))

    if log_dir_writable:
        checks.append(Check("state dir", "ok", "the transaction log directory is writable"))
    else:
        checks.append(
            Check(
                "state dir",
                "warn",
                "the transaction-log directory is not writable — history and uninstall will not record",
                "check ownership of ~/.local/state/hammunition (do not run install as root)",
            )
        )

    if run_logs is not None:
        count, size, newest, result = run_logs
        checks.append(
            Check(
                "run logs",
                "info",
                f"{count} run log(s), {size / 1024 / 1024:.1f} MB; the newest ({newest}) {result}",
                "hammunition logs --last prints it" if count else None,
                ["hammunition", "logs", "--last"] if count else None,
            )
        )

    checks.extend(rig_checks(rig))
    checks.extend(security_key_checks(security_keys))
    return checks


def _geoclue_checks(g: GeoClueState) -> list[Check]:
    """D-069: GeoClue's tether socket files and directory, and the agent CoMaps
    needs. Read without privilege; `busctl --user list` only lists names."""
    checks: list[Check] = []
    apply = "hammunition hardware apply"
    if not g.dropin_ours and not g.tmpfiles_ours:
        checks.append(
            Check(
                "geoclue",
                "info",
                f"GeoClue is installed and does not read the GPS tether, so CoMaps shows no "
                f"position from the receiver; `{apply}` sets it up",
            )
        )
    elif not (g.dropin_ours and g.tmpfiles_ours):
        missing = geoclue.TMPFILES if g.dropin_ours else geoclue.DROPIN
        checks.append(
            Check(
                "geoclue",
                "warn",
                f"half set up: {missing} is missing",
                apply,
                ["hammunition", "hardware", "apply"],
            )
        )
    elif g.directory != "current":
        what = "is missing" if g.directory == "absent" else f"is wrong: {g.directory}"
        checks.append(
            Check(
                "geoclue",
                "warn",
                f"{geoclue.SOCKET_DIR} {what}, so the tether cannot give GeoClue its socket",
                f"sudo systemd-tmpfiles --create {geoclue.TMPFILES}",
                ["sudo", "systemd-tmpfiles", "--create", str(geoclue.TMPFILES)],
            )
        )
    else:
        checks.append(
            Check(
                "geoclue",
                "ok",
                f"GeoClue reads the tether's socket at {geoclue.SOCKET} while "
                f"`hammunition maps gps-tether` runs",
            )
        )
    if g.agent is None:
        checks.append(
            Check("geoclue agent", "info", "not checked (busctl did not answer, or run as root)")
        )
    elif g.agent:
        checks.append(Check("geoclue agent", "ok", "Debian's GeoClue demo agent is running"))
    else:
        checks.append(
            Check(
                "geoclue agent",
                "warn",
                f"no {geoclue.DEMO_AGENT} in `busctl --user list`: without an agent GeoClue "
                f"holds CoMaps' request and Qt gives up after about 25 s (GNOME Shell is its "
                f"own agent, and this check does not see it)",
                "log out and back in: geoclue-2.0 starts the demo agent at login from "
                "/etc/xdg/autostart/geoclue-demo-agent.desktop",
            )
        )
    return checks


def _gps_resume_check(state: ResumeStatus) -> Check:
    if state == "installed":
        return Check(
            "gps-resume",
            "ok",
            "the resume step is installed: gpsd gets a fresh open of the receiver after a suspend",
        )
    detail = (
        "a GPS receiver is attached and its resume step is not installed"
        if state == "absent"
        else "the GPS resume step is not as this engine writes it, or is not enabled"
    )
    return Check(
        "gps-resume",
        "warn",
        f"{detail}: after a suspend gpsd can keep a receiver that has gone quiet (issue #177)",
        "hammunition hardware apply",
        ["hammunition", "hardware", "apply"],
    )


def _held(t: TimeState) -> str:
    if t.last_sync is None or t.holdover_seconds is None:
        return ""
    source = "the GPS" if t.last_source == "gps" else "the network"
    return f": holdover for {format_duration(t.holdover_seconds)}, last synchronised from {source}"


def _time_checks(t: TimeState) -> list[Check]:
    """D-058. A missing RTC is a weakness on any target; the rest needs ntpsec."""
    checks: list[Check] = []
    if not t.rtc:
        checks.append(
            Check(
                "hardware clock",
                "warn",
                "no battery-backed clock (RTC): after a power-off with no network and no "
                "GPS the date is wrong",
                "fit an RTC module (Raspberry Pi: a supported RTC HAT, or a battery on the "
                "Pi 5's RTC connector); fake-hwclock, offered by `hammunition hardware "
                "apply`, is only a stopgap",
            )
        )
    if t.daemon is None:
        if t.gps != "absent":
            checks.append(
                Check(
                    "time",
                    "info",
                    "the time daemon here is not ntpsec, so the GPS cannot set the clock (D-058)",
                )
            )
        return checks
    if t.mode in GPS_MODES and t.gps != "absent" and not t.grants:
        checks.append(
            Check(
                "time",
                "warn",
                "ntpd cannot read gpsd's time: its grants or gpsd's -n drop-in are not installed",
                "hammunition hardware apply",
                ["hammunition", "hardware", "apply"],
            )
        )
    if t.dhcp_config:
        checks.append(
            Check(
                "time",
                "warn",
                "ntpd runs on a DHCP-supplied configuration, which may not read the time mode",
                'set IGNORE_DHCP="yes" in /etc/default/ntpsec, then sudo systemctl restart ntpsec',
            )
        )
    restore_fix = "restore the network, or wake a GPS receiver in a GPS mode"
    if t.following in ("network", "gps"):
        where = "the network" if t.following == "network" else "the GPS"
        offset = "" if t.offset_ms is None else f", offset {t.offset_ms:+.1f} ms"
        checks.append(Check("time", "ok", f"the clock follows {where} (mode {t.mode}{offset})"))
    elif t.following == "unknown":
        checks.append(
            Check(
                "time",
                "warn",
                "could not ask ntpd what the clock follows",
                "systemctl status ntpsec",
                ["systemctl", "status", "ntpsec"],
            )
        )
    elif t.mode == "gps-only" and t.gps == "parked":
        checks.append(
            Check(
                "time",
                "warn",
                "gps-only with the receiver parked: nothing sets the clock" + _held(t),
                "hammunition hardware wake gps-receiver, or hammunition time mode auto",
            )
        )
    elif t.holdover_seconds is None:
        checks.append(
            Check(
                "time",
                "warn",
                "the clock follows nothing and ntpd has not synchronised since it started",
                restore_fix,
            )
        )
    elif t.holdover_seconds >= HOLDOVER_WARN_SECONDS:
        checks.append(Check("time", "warn", "the clock follows nothing" + _held(t), restore_fix))
    else:
        checks.append(
            Check(
                "time",
                "info",
                "the clock follows nothing"
                + _held(t)
                + "; the hardware clock and ntpd's drift file carry it",
            )
        )
    return checks


def writable_or_creatable(path: Path) -> bool:
    """Can ``path`` be written, or created and then written?

    The state directory does not exist until the first transaction, and on a
    fresh account neither does ``~/.local`` above it. Checking only the
    immediate parent reported a brand-new Ubuntu 24.04 VM as unable to record
    history (2026-09-01) when nothing was wrong beyond nothing having run yet.
    The nearest ancestor that exists is what decides whether a ``mkdir -p``
    will succeed.
    """
    for candidate in (path, *path.parents):
        if candidate.exists():
            return os.access(candidate, os.W_OK)
    return False


def summarize(checks: list[Check]) -> tuple[int, int, int]:
    """(fails, warns, oks+infos) — for the closing line and the exit code."""
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    healthy = sum(1 for c in checks if c.status in ("ok", "info"))
    return fails, warns, healthy
