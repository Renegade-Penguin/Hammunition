# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recover a Quectel modem whose SIM wedged across a resume or boot.  Issue #427.

Run as root by ``hammunition-modem-resume.service`` after the machine is back
from suspend or hibernation, and once at boot. Measured on the field laptop
(2026-10-26 boot, 2026-10-27 resume): the Quectel EM160R-GL (``2c7c:0620``, QMI
mode, ``/dev/cdc-wdm0``) comes back with its USIM application in QMI state
``illegal`` while ``AT+CPIN?`` says READY. ModemManager then fails the modem
(``sim-missing``) and never retries. What worked: stop ModemManager,
``qmicli --uim-sim-power-off=1`` then ``--uim-sim-power-on=1``, wait for the
application state ``ready``, start ModemManager.

What it does, one line per action, and the last line says which steps ran:

1. No Quectel ``cdc-wdm`` node (the modem is parked, unplugged or another
   model): nothing. A parked modem is never woken. The node is waited for up to
   ``--settle`` seconds, because the USB side may still be enumerating.
2. ``qmicli --uim-get-card-status`` through ``qmi-proxy`` (``-p``, so it does
   not fight ModemManager for the node). Not ``illegal``: nothing to do.
3. ``illegal`` is **the wedge**. **One** recovery attempt, never repeated:
   ``systemctl stop ModemManager`` (only if it was active), then
   ``quectel-usbnet sim-recover`` when that command is installed, else
   ``qmicli --uim-sim-power-off=1`` and ``--uim-sim-power-on=1`` (also the
   fallback if the command fails), then the card status polled for up to
   ``--ready-window`` seconds until the application is ``ready``, then
   ``systemctl start ModemManager``. ModemManager is started again whatever
   the outcome.

The result is also written to ``/run/hammunition/modem-sim.state`` (0644,
``key=value`` lines) so ``hammunition hardware state``, which runs
unprivileged and cannot open the node, can say "SIM wedged". The lines of the
run go to ``/run/hammunition/modem-resume.log`` (0644).

The exit status is 0 when there was nothing to do or the application came back
``ready``. Otherwise it is 1 and the last line names the manual steps, so
``systemctl status`` shows them.

The only commands run are ``qmicli``, ``quectel-usbnet`` and ``systemctl`` by
fixed argv with fixed verbs; nothing read from the modem becomes a path or a
command. Standard library only: installed whole under ``/usr/local/libexec/``
and run by ``/usr/bin/python3 -I``, importing nothing from Hammunition. The
options exist for the test suite; the unit passes none.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

USB_ID = "2c7c:0620"
_NODE = re.compile(r"cdc-wdm[0-9]+")
_APP = re.compile(r"Application \[[0-9]+\]:")
_APP_TYPE = re.compile(r"Application type:\s*'([a-z0-9-]+)")
_APP_STATE = re.compile(r"Application state:\s*'([a-z0-9-]+)")
_CARD_STATE = re.compile(r"Card state:\s*'([a-z0-9-]+)")
_MANUAL = (
    "recovery failed: run `sudo systemctl stop ModemManager`, then "
    "`sudo qmicli -d {dev} --uim-sim-power-off=1` and `--uim-sim-power-on=1`, "
    "check `qmicli -d {dev} --uim-get-card-status` shows application state 'ready', "
    "then `sudo systemctl start ModemManager`"
)

_LOG: list[int] = []
"""The open log file's descriptor, when there is one (a list, not a global)."""


def open_log(path: str) -> None:
    """Replace the log with a new one, 0644. Never raises: no log is not a fault."""
    close_log()
    try:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    except OSError:
        return
    with contextlib.suppress(OSError):
        os.fchmod(fd, 0o644)
    _LOG.append(fd)


def close_log() -> None:
    while _LOG:
        with contextlib.suppress(OSError):
            os.close(_LOG.pop())


def log(message: str) -> None:
    print(message, flush=True)
    if _LOG:
        with contextlib.suppress(OSError):
            os.write(_LOG[0], (message + "\n").encode("utf-8", "replace"))


def run(argv: list[str], timeout: float = 30.0) -> tuple[int, str]:
    """``(exit status, stdout and stderr)``; 127 for a missing command, 124 for a timeout."""
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: no answer in {timeout:g} s"
    except OSError as exc:
        return 126, f"{argv[0]}: {exc}"
    return done.returncode, (done.stdout + done.stderr).strip()


def card_status(text: str) -> tuple[str | None, str | None]:
    """``(card state, USIM application state)`` from ``--uim-get-card-status``.

    The application of type ``usim``, else the first one; ``None`` where the
    output does not say.
    """
    card = _CARD_STATE.search(text)
    chunks = _APP.split(text)[1:]
    states: list[tuple[str | None, str]] = []
    for chunk in chunks:
        kind = _APP_TYPE.search(chunk)
        state = _APP_STATE.search(chunk)
        if state is not None:
            states.append((kind.group(1) if kind else None, state.group(1)))
    chosen = next((s for k, s in states if k == "usim"), states[0][1] if states else None)
    return (card.group(1) if card else None, chosen)


def find_modem(dev: Path, sysfs: Path, usb_id: str) -> str | None:
    """The ``/dev/cdc-wdmN`` whose USB device is ``usb_id``, lowest number first."""
    try:
        names = sorted(
            (p.name for p in dev.iterdir() if _NODE.fullmatch(p.name)),
            key=lambda n: int(n[len("cdc-wdm") :]),
        )
    except OSError:
        return None
    vendor, product = usb_id.split(":")
    for name in names:
        # /sys/class/usbmisc/cdc-wdmN/device is the USB interface; its parent
        # directory is the device, which carries idVendor and idProduct.
        iface = Path(os.path.realpath(sysfs / "class" / "usbmisc" / name / "device"))
        try:
            got = (
                (iface.parent / "idVendor").read_text().strip(),
                (iface.parent / "idProduct").read_text().strip(),
            )
        except OSError:
            continue
        if got == (vendor, product):
            return str(dev / name)
    return None


def write_state(path: str, state: str, steps: list[str], device: str | None) -> None:
    """Replace the state file, 0644. Never raises."""
    text = (
        f"state={state}\n"
        f"steps={','.join(steps)}\n"
        f"device={device or ''}\n"
        f"time={datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    )
    try:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        tmp = f"{path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, text.encode())
            os.fchmod(fd, 0o644)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError:
        return


def main(argv: list[str] | None = None, sleep: Callable[[float], None] = time.sleep) -> int:
    parser = argparse.ArgumentParser(prog="hammunition-modem-resume")
    parser.add_argument("--dev", default="/dev")
    parser.add_argument("--sysfs", default="/sys")
    parser.add_argument("--usb-id", default=USB_ID)
    parser.add_argument("--qmicli", default="/usr/bin/qmicli")
    parser.add_argument("--systemctl", default="/usr/bin/systemctl")
    parser.add_argument(
        "--quectel-usbnet",
        default="",
        help="path of quectel-usbnet; empty looks for it on PATH",
    )
    parser.add_argument("--settle", type=float, default=8.0)
    parser.add_argument("--ready-window", type=float, default=15.0)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--log-file", default="/run/hammunition/modem-resume.log")
    parser.add_argument("--state-file", default="/run/hammunition/modem-sim.state")
    args = parser.parse_args(argv)
    open_log(args.log_file)
    try:
        log(f"modem-resume run started {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        code = _run(args, sleep)
        log(f"modem-resume run finished: exit {code}")
        return code
    finally:
        close_log()


def _run(args: argparse.Namespace, sleep: Callable[[float], None]) -> int:
    deadline = time.monotonic() + args.settle
    device = find_modem(Path(args.dev), Path(args.sysfs), args.usb_id)
    while device is None and time.monotonic() < deadline:
        sleep(args.poll)
        device = find_modem(Path(args.dev), Path(args.sysfs), args.usb_id)
    if device is None:
        log(f"no {args.usb_id} cdc-wdm node: no such modem, or it is parked; nothing to do")
        with contextlib.suppress(OSError):
            os.unlink(args.state_file)
        return 0

    def status(extra: tuple[str, ...] = ("-p",)) -> tuple[int, str | None, str]:
        rc, out = run([args.qmicli, *extra, "-d", device, "--uim-get-card-status"], 20.0)
        if rc != 0:
            return rc, None, out
        return rc, card_status(out)[1], out

    rc, state, out = status()
    while rc != 0 and time.monotonic() < deadline:
        sleep(args.poll)
        rc, state, out = status()
    steps: list[str] = []
    if rc != 0 or state is None:
        log(f"{device}: could not read the SIM application state ({out[:200] or 'no output'})")
        write_state(args.state_file, "unknown", steps, device)
        return 1
    if state != "illegal":
        log(f"{device}: SIM application state is '{state}'; nothing to do")
        write_state(args.state_file, "ok", steps, device)
        return 0

    log(
        f"{device}: SIM application state is 'illegal' (the wedge, issue #427); one recovery attempt"
    )
    steps.append("detected")
    write_state(args.state_file, "wedged", steps, device)

    was_active = run([args.systemctl, "is-active", "--quiet", "ModemManager.service"])[0] == 0
    if was_active:
        rc, out = run([args.systemctl, "stop", "ModemManager.service"], 60.0)
        steps.append("stop-modemmanager" if rc == 0 else "stop-modemmanager-failed")
        log(
            f"systemctl stop ModemManager.service: {'ok' if rc == 0 else f'exit {rc}: {out[:200]}'}"
        )

    recovered = False
    try:
        tool = args.quectel_usbnet or shutil.which("quectel-usbnet") or ""
        done = False
        if tool and os.access(tool, os.X_OK):
            rc, out = run([tool, "sim-recover"], 90.0)
            steps.append("quectel-usbnet-sim-recover" if rc == 0 else "quectel-usbnet-failed")
            log(f"quectel-usbnet sim-recover: {'ok' if rc == 0 else f'exit {rc}: {out[:200]}'}")
            done = rc == 0
        if not done:
            if tool:
                log("falling back to qmicli directly")
            for verb in ("off", "on"):
                rc, out = run([args.qmicli, "-d", device, f"--uim-sim-power-{verb}=1"], 30.0)
                steps.append(f"sim-power-{verb}" if rc == 0 else f"sim-power-{verb}-failed")
                log(
                    f"qmicli --uim-sim-power-{verb}=1: {'ok' if rc == 0 else f'exit {rc}: {out[:200]}'}"
                )
                if rc != 0:
                    break
        ready_by = time.monotonic() + args.ready_window
        while True:
            rc, state, out = status(extra=())
            if rc == 0 and state == "ready":
                recovered = True
                break
            if time.monotonic() >= ready_by:
                break
            sleep(args.poll)
        steps.append("app-ready" if recovered else "app-not-ready")
        log(f"SIM application state after recovery: {state or 'unreadable'}")
    finally:
        if was_active:
            rc, out = run([args.systemctl, "start", "ModemManager.service"], 60.0)
            steps.append("start-modemmanager" if rc == 0 else "start-modemmanager-failed")
            log(
                f"systemctl start ModemManager.service: {'ok' if rc == 0 else f'exit {rc}: {out[:200]}'}"
            )

    log(f"steps run: {', '.join(steps)}")
    write_state(args.state_file, "recovered" if recovered else "failed", steps, device)
    if recovered:
        return 0
    log(_MANUAL.format(dev=device))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
