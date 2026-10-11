# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Suite-wide guarantees.

**No test reaches the network.** `hammunition.fetch` is the first code here that
can make an outbound connection, and a fetch test that quietly fell through to
the real internet would be slow, flaky, and — worse — would stop testing the
thing it names. So every socket to anywhere but loopback is blocked for the
whole suite: a test that tries gets a clear failure rather than a timeout, and
the network seam stays a seam because nothing can bypass it.

This is a property of the suite, not of any one test, which is why it is
enforced here rather than asserted in one place (CLAUDE.md: *prove properties,
not just behaviour*). Loopback stays open so a future test may bind a local
server if it needs one.

**No test asks the machine's package manager.** The same shape one layer down:
`apt-get --simulate` and `apt-cache policy` are unprivileged and answer at
plan time, so a test that mocks part of the apt backend and not all of it
runs the rest against whatever machine it is on. That is exactly what
happened when D-038 added the simulate step — the CLI dry-run test passed on
every dev box and GitHub runner, where a `git` package exists, and failed in
all four target containers, whose apt lists are empty. Blocking `apt-get`,
`apt-cache`, `apt`, `dpkg`, `dpkg-query` and `sudo` at the real runner makes
that a failure everywhere, naming the mock to add. Compilers, tar and the
rest stay open: the source-build tests run real builds on purpose.

**No test runs the real sudo, by either route.** The keepalive (D-062) runs
``sudo -v`` and ``sudo -n -v`` itself rather than through a runner, so it is
guarded separately: a ``sudo`` that resolves anywhere but under the test's
temporary directory -- a fake written by ``fake_tools.install_fakes`` --
fails the test. ``sudo -v`` on a developer's terminal would otherwise stop
the suite at a password prompt, and in CI it would quietly test nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import pwd
import shutil
import socket
import tarfile
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

import hammunition.sudo_ticket as sudo_ticket
from hammunition import runlog
from hammunition.backends.base import Command, CommandResult, SubprocessRunner
from hammunition.paths import artifact_cache_dir

# What the operator's real files are, captured before anything is changed.
# The guard test (tests/test_isolation.py) reads these; nothing else may.
_REAL_HOME_ENV = os.environ.get("HOME")
REAL_LOGIN: str = (
    os.environ.get("SUDO_USER") or os.environ.get("USER") or os.environ.get("LOGNAME") or "root"
)


def _real_homes() -> tuple[Path, ...]:
    homes = [_REAL_HOME_ENV, pwd.getpwuid(os.getuid()).pw_dir, pwd.getpwuid(os.geteuid()).pw_dir]
    with contextlib.suppress(KeyError):
        homes.append(pwd.getpwnam(REAL_LOGIN).pw_dir)
    return tuple(dict.fromkeys(Path(h) for h in homes if h and h != "/"))


REAL_HOMES: tuple[Path, ...] = _real_homes()
_REAL_STATION = REAL_HOMES[0] / ".config" / "hammunition" / "station.yml" if REAL_HOMES else None
_ISOLATED_ROOT: Path | None = None


def session_root() -> Path:
    """The directory every default path of this run lives under."""
    assert _ISOLATED_ROOT is not None, "the isolation fixture has not run"
    return _ISOLATED_ROOT


def _station_fingerprint() -> tuple[int, str] | None:
    path = _REAL_STATION
    if path is None or not path.is_file():
        return None
    return path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


class _NoRealAccounts:
    """Stands in for ``pwd`` inside ``hammunition.paths``: an account whose
    home is a real one does not exist, so an owner under euid 0 falls back to
    the (isolated) environment. Tests that fake ``pwd.getpwnam`` still work:
    the lookup goes through the module attribute at call time."""

    @staticmethod
    def getpwnam(name: str) -> pwd.struct_passwd:
        entry = pwd.getpwnam(name)
        if Path(entry.pw_dir) in REAL_HOMES:
            raise KeyError(name)
        return entry

    @staticmethod
    def getpwall() -> list[pwd.struct_passwd]:
        return [e for e in pwd.getpwall() if Path(e.pw_dir) not in REAL_HOMES]

    def __getattr__(self, name: str) -> Any:
        return getattr(pwd, name)


@pytest.fixture(autouse=True, scope="session")
def _isolated_operator_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """No test can read or write the operator's files (2026-10-03, twice).

    Points HOME and every XDG base at one temporary root, removes the
    variables owner detection reads, and blinds ``paths`` to the passwd
    database, so even euid 0 (``unshare -r``) with ``owner`` set cannot
    resolve a real home. At the end, fails the run if the real station file
    changed.
    """
    global _ISOLATED_ROOT
    from hammunition import paths as hpaths

    root = tmp_path_factory.mktemp("operator-isolation")
    _ISOLATED_ROOT = root
    saved = dict(os.environ)
    before = _station_fingerprint()
    env = {
        "HOME": root / "home",
        "XDG_CONFIG_HOME": root / "config",
        "XDG_STATE_HOME": root / "state",
        "XDG_CACHE_HOME": root / "cache",
        "XDG_DATA_HOME": root / "data",
        "XDG_RUNTIME_DIR": root / "runtime",
    }
    for key, value in env.items():
        value.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(value)
    for key in ("USER", "SUDO_USER", "LOGNAME"):
        os.environ.pop(key, None)
    real_pwd = vars(hpaths)["pwd"]
    vars(hpaths)["pwd"] = _NoRealAccounts()
    try:
        yield
    finally:
        vars(hpaths)["pwd"] = real_pwd
        os.environ.clear()
        os.environ.update(saved)
    after = _station_fingerprint()
    if before != after:
        pytest.fail(
            f"the test run changed the operator's real station file {_REAL_STATION}; "
            "a test is reaching outside its isolated HOME",
            pytrace=False,
        )


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_run = SubprocessRunner.run
_real_sudo_run = sudo_ticket._run
_real_logs_dir = runlog.logs_dir

MACHINE_QUERIES = frozenset(
    {
        "apt-get",
        "apt-cache",
        "apt",
        "dpkg",
        "dpkg-query",
        "sudo",
        "systemctl",
        "ntpq",
        "apparmor_parser",
    }
)


class MachineQueried(RuntimeError):
    """A test ran a package-manager or privileged command on the host."""


@pytest.fixture(autouse=True, scope="session")
def _no_machine_queries() -> Any:
    def guard(self: SubprocessRunner, command: Command) -> CommandResult:
        if command.argv and command.argv[0] in MACHINE_QUERIES:
            raise MachineQueried(
                f"the test suite blocked {command.argv[0]!r} ({command.description}). "
                f"Tests must not ask the machine's package manager: give the backend a "
                f"RecordingRunner, or monkeypatch every AptBackend method the code "
                f"under test reaches (lists_populated, probe and simulate at plan "
                f"time), so the result is the same in every target container."
            )
        return _real_run(self, command)

    SubprocessRunner.run = guard  # type: ignore[method-assign]
    try:
        yield
    finally:
        SubprocessRunner.run = _real_run  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _no_host_security_key_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test asks a real FIDO2/PIV token, pcscd or OpenSSH binary for
    doctor's security-key checks (A12).

    `doctor`'s `_security_key_probe` is a different seam than
    `SubprocessRunner.run` above: it runs `systemctl`, `ssh`, `fido2-token`
    and `opensc-tool` directly with its own bounded subprocess adapter, not
    through a `Command`/`CommandRunner`, so `_no_machine_queries` does not see
    it. Blocked here instead, with the same failure a test should hit
    immediately rather than silently asking the host's own tokens and pcscd
    daemon. Function-scoped, because `monkeypatch` is function-scoped and a
    test that wants a real result patches `_security_key_probe` again, after
    this fixture has already run.
    """
    import importlib

    cli = importlib.import_module("hammunition.cli.main")

    def blocked(argv: tuple[str, ...]) -> CommandResult:
        raise MachineQueried(
            f"the test suite blocked the real {argv[0]!r} for doctor's security-key probe. "
            f"A doctor CLI test must inject an explicit CommandResult: "
            f"monkeypatch.setattr(cli, '_security_key_probe', lambda argv: CommandResult(...))."
        )

    monkeypatch.setattr(cli, "_security_key_probe", blocked)


def _fake(program: str) -> bool:
    """Whether *program* resolves on PATH to a file under the temp directory,
    or to nothing at all (the missing-sudo case is a test of its own)."""
    found = shutil.which(program)
    if found is None:
        return True
    return Path(found).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


@pytest.fixture(autouse=True, scope="session")
def _no_real_sudo() -> Any:
    def guard(argv: Sequence[str], interactive: bool) -> int:
        if argv and not _fake(argv[0]):
            raise MachineQueried(
                f"the test suite blocked the real {argv[0]!r} ({' '.join(argv)}). Put a "
                f"fake sudo first on PATH with fake_tools.install_fakes, or pass "
                f"SudoKeepalive a run= function."
            )
        return _real_sudo_run(argv, interactive)

    sudo_ticket._run = guard
    try:
        yield
    finally:
        sudo_ticket._run = _real_sudo_run


def _loopback(address: Any) -> bool:
    """Whether *address* is loopback, for the address families that have one."""
    if not isinstance(address, tuple) or not address:
        # AF_UNIX and friends: a filesystem path, not the network.
        return True
    host = address[0]
    if not isinstance(host, str):
        return False
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


class NetworkBlocked(RuntimeError):
    """A test tried to open a non-loopback connection."""


@pytest.fixture(autouse=True, scope="session")
def _no_network() -> Any:
    def guard(self: socket.socket, address: Any) -> Any:
        if not _loopback(address):
            raise NetworkBlocked(
                f"the test suite blocked a connection to {address!r}. Tests must not "
                f"reach the network: inject a fake Transport (hammunition.fetch) or "
                f"a RecordingRunner instead of letting a fetch fall through to the "
                f"real internet."
            )
        return _real_connect(self, address)

    def guard_ex(self: socket.socket, address: Any) -> Any:
        if not _loopback(address):
            raise NetworkBlocked(f"the test suite blocked a connection to {address!r}")
        return _real_connect_ex(self, address)

    socket.socket.connect = guard  # type: ignore[assignment,method-assign]
    socket.socket.connect_ex = guard_ex  # type: ignore[assignment,method-assign]
    try:
        yield
    finally:
        socket.socket.connect = _real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _real_connect_ex  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# GPS time (D-058): no test touches the host's time configuration
# ---------------------------------------------------------------------------

DEBIAN_NTP_CONF = """\
# /etc/ntpsec/ntp.conf, configuration for ntpd; see ntp.conf(5) for help

driftfile /var/lib/ntpsec/ntp.drift
leapfile /usr/share/zoneinfo/leap-seconds.list

# This should be maxclock 7, but the pool entries count towards maxclock.
tos maxclock 11

# Comment this out if you have a refclock and want it to be able to discipline
# the clock by itself (e.g. if the system is not connected to the network).
tos minclock 4 minsane 3

# Specify one or more NTP servers.

# Public NTP servers supporting Network Time Security:
# server time.cloudflare.com nts

# pool.ntp.org maps to about 1000 low-stratum NTP servers.  Your server will
# pick a different set every time it starts up.  Please consider joining the
# pool: <https://www.pool.ntp.org/join.html>
pool 0.debian.pool.ntp.org iburst
pool 1.debian.pool.ntp.org iburst
pool 2.debian.pool.ntp.org iburst
pool 3.debian.pool.ntp.org iburst

# By default, exchange time with everybody, but don't allow configuration.
restrict default kod nomodify noquery limited

# Local users may interrogate the ntp server more closely.
restrict 127.0.0.1
restrict ::1
"""
"""The lines of ntpsec 1.2.3's shipped ntp.conf that GPS time reads or edits,
copied from the package with the unrelated NTS and statistics comments left out."""


@pytest.fixture
def debian_ntp_conf() -> str:
    return DEBIAN_NTP_CONF


@pytest.fixture(autouse=True)
def _no_host_time_files(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Every GPS-time path points somewhere that does not exist, for every test.

    The field laptop runs this suite and has GPS time applied. A test that read
    its real /etc/ntpsec/ntp.conf would pass or fail by what that machine holds
    (CLAUDE.md: test the matrix, not your machine), and one that wrote there
    would be worse. Tests that want files request `time_files`. A test that
    writes here without it fails, naming the fixture to use.
    """
    from hammunition.gpstime import files

    root = tmp_path_factory.getbasetemp() / "host-time-files-absent"
    for name, default in files.PATHS.items():
        monkeypatch.setattr(files, name, str(root) + default)
    yield root
    if root.exists():
        shutil.rmtree(root)
        pytest.fail(
            "a test wrote GPS time files without the time_files fixture; request it "
            "so they land in that test's own tmp_path"
        )


@pytest.fixture
def time_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    debian_ntp_conf: str,
    _no_host_time_files: Path,
) -> Path:
    """A machine with ntpsec installed (its shipped ntp.conf and its daemon) and one
    hardware clock, under tmp_path."""
    from hammunition.gpstime import files

    root = tmp_path / "root"
    for name, default in files.PATHS.items():
        monkeypatch.setattr(files, name, str(root) + default)
    conf = Path(files.NTP_CONF)
    conf.parent.mkdir(parents=True)
    conf.write_text(debian_ntp_conf)
    ntpd = Path(files.NTPD)
    ntpd.parent.mkdir(parents=True)
    ntpd.write_text("")
    (Path(files.RTC_CLASS) / "rtc0").mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def _no_host_geoclue_files(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Every GeoClue path (D-069) points somewhere that does not exist, for every test.

    The field laptop runs this suite with GeoClue installed and, once
    `hardware apply` has run there, Hammunition's drop-in in place: a test that
    read the real /etc/geoclue would answer by what that machine holds, and
    the tether would pick up the real socket path. Tests that want the files
    request `geoclue_files`; one that writes here without it fails.
    """
    from hammunition import geoclue

    root = tmp_path_factory.getbasetemp() / "host-geoclue-files-absent"
    for name, default in geoclue.PATHS.items():
        monkeypatch.setattr(geoclue, name, str(root) + default)
    yield root
    if root.exists():
        shutil.rmtree(root)
        pytest.fail(
            "a test wrote GeoClue files without the geoclue_files fixture; request it "
            "so they land in that test's own tmp_path"
        )


@pytest.fixture
def geoclue_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_host_geoclue_files: Path
) -> Path:
    """A machine with GeoClue installed (its daemon present), under tmp_path,
    whose ``geoclue`` group is the test account's own group, so the directory
    checks can pass without root."""
    import grp
    import os

    from hammunition import geoclue

    root = tmp_path / "root"
    for name, default in geoclue.PATHS.items():
        monkeypatch.setattr(geoclue, name, str(root) + default)
    monkeypatch.setattr(geoclue, "GROUP", grp.getgrgid(os.getgid()).gr_name)
    daemon = Path(geoclue.DAEMON)
    daemon.parent.mkdir(parents=True)
    daemon.write_text("")
    return root


# ---------------------------------------------------------------------------
# The GPS resume step (issue #177): no test reads or writes the host's unit
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_host_resume_files(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """The resume step's paths point somewhere that does not exist, for every
    test, for the reason `_no_host_time_files` gives: the field laptop runs this
    suite, and a plan that read its real /usr/local/libexec or /etc/systemd
    would test that machine. Tests that want files request `resume_files`."""
    from hammunition.hardware import gps_resume

    root = tmp_path_factory.getbasetemp() / "host-resume-files-absent"
    for name, default in gps_resume.PATHS.items():
        monkeypatch.setattr(gps_resume, name, str(root) + default)
    yield root
    if root.exists():
        shutil.rmtree(root)
        pytest.fail(
            "a test wrote GPS resume files without the resume_files fixture; request it "
            "so they land in that test's own tmp_path"
        )


@pytest.fixture(autouse=True)
def _no_host_devctl_export(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """The exported list files point somewhere that does not exist, for every
    test, for the reason `_no_host_resume_files` gives: the field laptop runs this
    suite and holds the real ones. Also stops the handover probe from running
    the installed helper: `polkit.installed_helper_version` answers None unless a
    test says otherwise. Tests that want files request `devctl_export_files`."""
    from hammunition.hardware import devctl_export, polkit

    root = tmp_path_factory.getbasetemp() / "host-devctl-export-absent"
    for name, default in devctl_export.PATHS.items():
        monkeypatch.setattr(devctl_export, name, str(root) + default)
    monkeypatch.setattr(polkit, "installed_helper_version", lambda *a, **k: None)
    yield root
    if root.exists():
        shutil.rmtree(root)
        pytest.fail(
            "a test wrote devctl export files without the devctl_export_files fixture; "
            "request it so they land in that test's own tmp_path"
        )


@pytest.fixture
def devctl_export_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_host_devctl_export: Path
) -> Path:
    """The exported list files' paths, under tmp_path, with nothing written yet."""
    from hammunition.hardware import devctl_export

    root = tmp_path / "export-root"
    for name, default in devctl_export.PATHS.items():
        monkeypatch.setattr(devctl_export, name, str(root) + default)
    return root


@pytest.fixture
def resume_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_host_resume_files: Path
) -> Path:
    """A machine with gpsd installed and no resume step yet, under tmp_path."""
    from hammunition.hardware import gps_resume

    root = tmp_path / "resume-root"
    for name, default in gps_resume.PATHS.items():
        monkeypatch.setattr(gps_resume, name, str(root) + default)
    gpsd = Path(gps_resume.GPSD)
    gpsd.parent.mkdir(parents=True)
    gpsd.write_text("")
    return root


TRAY_SHA = "614148fb4241e88ca007885d01ef97d0752c8cd47b6e57337e2ee12ab3bfc438"
"""The sha256 both tray units pin (hammunition-tray v0.5.0's source archive)."""


@pytest.fixture(scope="module")
def pinned_tray_archive() -> Path:
    """The pinned archive in the fetch cache, re-hashed, or a skip that says why."""
    cached = sorted(artifact_cache_dir().glob(f"{TRAY_SHA}-*"))
    if not cached:
        pytest.skip(
            f"the pinned hammunition-tray archive is not in {artifact_cache_dir()}: fetch it "
            f"with hammunition.fetch.Fetcher on a networked machine (an install of "
            f"hammunition-tray-qt does), then these checks run"
        )
    import hashlib

    assert hashlib.sha256(cached[0].read_bytes()).hexdigest() == TRAY_SHA, (
        "the cached archive is not the pinned one"
    )
    return cached[0]


@pytest.fixture(scope="module")
def pinned_tray(pinned_tray_archive: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The v0.5.0 tree, unpacked from the fetch cache."""
    root = tmp_path_factory.mktemp("tray")
    with tarfile.open(pinned_tray_archive) as tar:
        tar.extractall(root, filter="data")
    (top,) = [p for p in root.iterdir() if p.is_dir()]
    return top


@pytest.fixture(autouse=True)
def _no_host_dpkg_for_the_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test asks the host's dpkg who owns a path the tray units would write:
    this machine may have the tray's .deb installed, and an answer would make
    every plan that includes a tray unit depend on it. Tests that want an owner
    patch these two functions themselves."""
    from hammunition import devctl_helper

    monkeypatch.setattr(devctl_helper, "dpkg_owner", lambda _path: None)
    monkeypatch.setattr(devctl_helper, "dpkg_owners", lambda _paths: {})
    # Nor the permissions of the interpreter running the suite: the hosted
    # runner's toolcache Python sits in a group-writable tree, the gate rightly
    # refuses it (rc 2), and plan tests would pass on a dev venv and fail in CI.
    # The gate itself is tested with injected stat functions
    # (test_polkit_artifacts) and with explicit findings (test_cli, test_devctl_helper).
    from hammunition.hardware import polkit

    monkeypatch.setattr(polkit, "writable_including_symlink_target", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _run_logs_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Every command that leaves a run log (D-077) writes it under a temporary
    directory, never the real ~/.local/state/hammunition/logs of the machine
    running the suite. Tests of the directory's own resolution use
    `real_logs_dir`."""
    root = tmp_path_factory.mktemp("run-logs")
    monkeypatch.setattr(runlog, "logs_dir", lambda owner=None: root)
    return root


@pytest.fixture
def real_logs_dir(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The genuine :func:`hammunition.runlog.logs_dir`, un-patched."""
    return _real_logs_dir


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan-time retry policy (#200) never really waits in a test, and each
    test starts with no host given up on."""
    from hammunition import retry

    monkeypatch.setattr(retry.POLICY, "sleep", lambda _seconds: None)
    retry.POLICY.reset()
