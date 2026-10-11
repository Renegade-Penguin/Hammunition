# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hardware signing readiness: the IO seam ``doctor`` reads to render
`FIDO2`/PIV security-key and OpenSSH ``-sk`` signing checks (A12).

Every probe here is **read-only and bounded**: it lists tokens and readers
and asks whether a listed device answers, never asks a key to sign, mint a
PIN, or touch-confirm, and never changes login or trust state. The subprocess
that actually runs a command is injected (``run``), so this module is a pure
function of its results and is testable without a real token or a running
``pcscd`` — the CLI supplies the real adapter (``_security_key_probe`` in
``hammunition.cli.main``), which bounds output and wall time.

:func:`probe_security_keys` is IO-free itself; the Callable it is given is the
only thing that touches a process. ``doctor.py`` stays pure by consuming only
the :class:`SecurityKeyState` this module produces.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from hammunition.backends.base import CommandResult
from hammunition.keystrength import KeyStrength

__all__ = ["SecurityKeyState", "probe_security_keys"]

#: Matches `permission denied`/`access denied` phrasing from fido2-token and
#: opensc-tool, case-insensitively, wherever it appears in stderr.
_ACCESS_DENIED = re.compile(r"permission|access denied", re.I)

#: OpenSSH's `-V` banner: `OpenSSH_9.6p1 ...` (and some builds write it to
#: stdout instead of stderr, so the caller searches both joined together).
_OPENSSH_VERSION = re.compile(r"OpenSSH_(\d+)\.(\d+)")

#: A `fido2-token -L` line starts with the device path, then a colon.
_HIDRAW_DEVICE = "/dev/hidraw"

#: An `opensc-tool --list-readers` line starts with the reader's index.
_READER_INDEX = re.compile(r"\s*(\d+)\s+")

#: A PIV applet's card/driver name, from `opensc-tool --reader N --name`.
_PIV_NAME = re.compile(r"\bpiv(?:-ii)?\b", re.I)


@dataclass(frozen=True)
class SecurityKeyState:
    """What was measured about hardware signing, read-only.  A12.

    Every field is a fact read without keying a token: ``None`` means
    *unmeasured* (the tool is missing, the probe failed, or — for the two
    ``*_access`` fields — doctor ran as root and so cannot speak for the
    ordinary operator), not *absent*.
    """

    pcscd_active: bool | None
    """Whether the PC/SC daemon answers `systemctl is-active` as active."""
    fido2_present: bool | None
    """Whether `fido2-token -L` lists at least one device; None when the
    tool is missing or the listing itself failed (permission or otherwise)."""
    piv_present: bool | None
    """Whether a reader names a PIV applet; None when `opensc-tool` itself
    could not list readers (missing, or pcscd unreachable)."""
    openssh: tuple[int, int] | None
    """The OpenSSH client's (major, minor); None when `ssh -V` could not be
    parsed. `-sk` signing needs at least (8, 2)."""
    fido2_access: bool | None
    """Whether the ordinary operator could open a listed FIDO2 device."""
    piv_access: bool | None
    """Whether the ordinary operator could read a PIV reader's name."""
    probed_as_operator: bool
    """Whether the access probes ran as the ordinary operator. False (root)
    means the two `*_access` fields above are unmeasured, not failing."""
    enrolled: tuple[KeyStrength, ...]
    """The Bunker's enrolled signing keys, already classified by the CLI from
    the owner-aware mirror store — this module never reads that store."""


def probe_security_keys(
    run: Callable[[tuple[str, ...]], CommandResult],
    *,
    as_operator: bool,
    enrolled: tuple[KeyStrength, ...],
) -> SecurityKeyState:
    """Measure hardware signing readiness through *run*, never raising.

    *run* is the only thing that touches a process; every argv it is handed
    here is a read-only listing or a liveness probe. Root cannot measure
    whether the *ordinary operator* can reach a token — a udev rule grants
    the console user, not root — so with ``as_operator=False`` the two
    access fields stay unmeasured rather than claiming a true-as-root result.
    """
    service = run(("systemctl", "is-active", "pcscd"))
    # `is-active` answers 0 (active) or 3 (inactive) for a real unit; any
    # other code means the unit could not be asked at all.
    active = service.stdout.strip() == "active" if service.returncode in (0, 3) else None

    ssh = run(("ssh", "-V"))
    version = _OPENSSH_VERSION.search(ssh.stdout + ssh.stderr)
    openssh = (int(version[1]), int(version[2])) if version else None

    if not as_operator:
        return SecurityKeyState(active, None, None, openssh, None, None, False, enrolled)

    fido = run(("fido2-token", "-L"))
    devices: list[str] = []
    if fido.ok:
        for line in fido.stdout.splitlines():
            first = line.split(":", 1)[0].strip()
            if first.startswith(_HIDRAW_DEVICE) and first.removeprefix(_HIDRAW_DEVICE).isdigit():
                devices.append(first)
    fido_present = bool(devices) if fido.ok else None
    fido_access: bool | None = False if not fido.ok and _ACCESS_DENIED.search(fido.stderr) else None
    if devices:
        fido_access = any(run(("fido2-token", "-I", device)).ok for device in devices)

    readers = run(("opensc-tool", "--list-readers"))
    indices: list[str] = []
    if readers.ok:
        for line in readers.stdout.splitlines():
            match = _READER_INDEX.match(line)
            if match:
                indices.append(match[1])
    piv_present: bool | None = False if readers.ok else None
    piv_access: bool | None = None
    for reader in indices:
        name = run(("opensc-tool", "--reader", reader, "--name"))
        if name.ok and _PIV_NAME.search(name.stdout):
            piv_present, piv_access = True, True
        elif not name.ok and _ACCESS_DENIED.search(name.stderr):
            piv_access = False

    return SecurityKeyState(
        active, fido_present, piv_present, openssh, fido_access, piv_access, True, enrolled
    )
