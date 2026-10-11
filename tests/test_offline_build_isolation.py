# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811

"""An offline source build has no network.  #381, Task 13 fix round 1, round 3.

Only the pinned tarball comes from the Bunker. A Makefile that runs curl must
fail, and so must one that connects to a pathname UNIX socket somewhere on the
host (a local proxy, docker, podman) to get there instead — ``--unshare-net``
blocks IP and abstract sockets, not those. So every offline build command runs
in bwrap: a fresh network namespace, a read-only filesystem, and the likely
places for such a socket (/run, /tmp, the operator's home, /opt, /srv, /mnt,
/media) replaced with an empty private tmpfs. With no working bwrap the
offline source build is refused at plan time. Online is untouched.

Not /usr/local: it is the engine's one unconfigurable install prefix
(``source.DEFAULT_PREFIX``), so every real build's writable bind re-exposes
it regardless of whether it was hidden first — hiding it would be a no-op
that misreports the guarantee, so it stays off the hidden list, and a real
test below proves a socket under the writable bind is reachable precisely
because it is real estate the sandbox must hand back, not an oversight.
"""

from __future__ import annotations

import importlib
import itertools
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from bunker_fixtures import artifact
from hammunition import netiso
from hammunition.backends.base import Action, BackendError, Command
from hammunition.backends.source import SourceBackend
from hammunition.fetch import Fetcher
from hammunition.manifest.schema import PackageManifest, SourceInstall
from hammunition.plan import InstallPlan, PlannedPackage, offline_payload_blockers
from test_offline_context import TARGET, apt_state, enrol_file_bunker, machine, run  # noqa: F401
from test_offline_guard import SRC_BODY, SRC_PIN, payload_catalog  # noqa: F401

cli = importlib.import_module("hammunition.cli.main")
SRC_BLOCK = SourceInstall.model_validate(
    {
        "method": "source",
        "source": SRC_PIN.model_dump(exclude_none=True),
        "build_system": "autotools",
    }
)


def _result(code: int) -> CompletedProcess[bytes]:
    return CompletedProcess(args=[], returncode=code, stdout=b"", stderr=b"")


# -- the bwrap invocation -------------------------------------------------------


_BUILD_ONLY_HIDDEN = ["/opt", "/srv", "/mnt", "/media"]


def _expected_prefix(
    writable: list[str],
    runtime_dir: str | None,
    *,
    var_run: bool,
    home: str | None = None,
    build_only_hidden: bool = True,
    bind_flag: str = "--ro-bind",
) -> tuple[str, ...]:
    home_hidden = [home] if home and home != "/root" else []
    return (
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        bind_flag,
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/run",
        *(("--tmpfs", "/var/run") if var_run else ()),
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/var/tmp",
        *(("--tmpfs", runtime_dir) if runtime_dir else ()),
        *(arg for path in home_hidden for arg in ("--tmpfs", path)),
        "--tmpfs",
        "/root",
        *(
            (arg for path in _BUILD_ONLY_HIDDEN for arg in ("--tmpfs", path))
            if build_only_hidden
            else ()
        ),
        *(arg for path in writable for arg in ("--bind-try", path, path)),
        "--",
    )


@pytest.mark.parametrize("home", [None, "/home/op", "/root"])
@pytest.mark.parametrize("var_run", [True, False])
@pytest.mark.parametrize("runtime_dir", [None, "/run/user/1000", "/elsewhere/runtime"])
def test_the_bwrap_invocation_is_read_only_with_private_run_tmp_home_and_opt(
    runtime_dir: str | None, var_run: bool, home: str | None
) -> None:
    got = netiso.bwrap_prefix(
        [Path("/home/op/build/thing-1"), Path("/usr/local")],
        runtime_dir=runtime_dir,
        var_run_is_directory=var_run,
        home=home,
    )
    assert got == _expected_prefix(
        ["/home/op/build/thing-1", "/usr/local"], runtime_dir, var_run=var_run, home=home
    )


@pytest.mark.parametrize("home", [None, "/home/op", "/root"])
@pytest.mark.parametrize("var_run", [True, False])
@pytest.mark.parametrize("runtime_dir", [None, "/run/user/1000", "/elsewhere/runtime"])
def test_the_privileged_invocation_is_writable_except_run_tmp_home_and_root(
    runtime_dir: str | None, var_run: bool, home: str | None
) -> None:
    """Unlike the build sandbox: no /opt, /srv, /mnt or /media — a .deb may
    legitimately install files there, and a private tmpfs would silently
    discard them. (/usr/local is excluded from both, for a different reason:
    see the module docstring.)"""
    got = netiso.bwrap_writable_prefix(
        runtime_dir=runtime_dir, var_run_is_directory=var_run, home=home
    )
    assert got == _expected_prefix(
        [], runtime_dir, var_run=var_run, home=home, build_only_hidden=False, bind_flag="--bind"
    )
    assert "--ro-bind" not in got
    for hidden in _BUILD_ONLY_HIDDEN:
        assert hidden not in got


def test_home_dir_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/op")
    assert netiso.home_dir() == "/home/op"
    monkeypatch.delenv("HOME")
    assert netiso.home_dir() is None


def test_sandbox_and_privileged_sandbox_pick_up_this_environments_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", "/home/op")
    assert "/home/op" in netiso.sandbox(("true",), writable=[], var_run_is_directory=False)
    assert "/home/op" in netiso.privileged_sandbox(("true",))


def test_the_command_follows_the_double_dash_and_nothing_else_is_writable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    argv = netiso.sandbox(("make", "-j2"), writable=[Path("/b")], var_run_is_directory=False)
    assert argv[-3:] == ("--", "make", "-j2")
    assert argv.count("--bind-try") == 1 and "--bind" not in argv and "--dev-bind" not in argv
    assert argv[argv.index("--ro-bind") : argv.index("--ro-bind") + 3] == ("--ro-bind", "/", "/")


def test_sandbox_hides_this_environments_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/77")
    argv = netiso.sandbox(("true",), writable=[], var_run_is_directory=False)
    assert ("--tmpfs", "/run/user/77") in list(itertools.pairwise(argv))


def test_the_default_runtime_dir_is_the_environments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    assert netiso.runtime_dir() == "/run/user/4242"
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert netiso.runtime_dir() is None


@pytest.mark.parametrize(
    ("have", "code", "expected"),
    [(True, 0, "bwrap"), (True, 1, None), (False, 0, None)],
)
def test_detect_needs_bwrap_to_run_the_full_sandbox(
    monkeypatch: pytest.MonkeyPatch, have: bool, code: int, expected: str | None
) -> None:
    monkeypatch.setattr(
        "hammunition.netiso.shutil.which", lambda name: "/bin/bwrap" if have else None
    )
    asked: list[tuple[str, ...]] = []

    def fake(argv: Any, **kwargs: Any) -> CompletedProcess[bytes]:
        asked.append(tuple(argv))
        return _result(code)

    assert netiso.detect(fake) == expected
    for argv in asked:
        # it probes the same sandbox a build gets, not a lighter one
        assert argv[-1] == "true" and "--ro-bind" in argv and "--unshare-net" in argv


def test_detect_survives_a_probe_that_raises_or_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hammunition.netiso.shutil.which", lambda name: f"/bin/{name}")

    def broken(argv: Any, **kwargs: Any) -> CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 10)

    assert netiso.detect(broken) is None

    def missing(argv: Any, **kwargs: Any) -> CompletedProcess[bytes]:
        raise OSError("exec format error")

    assert netiso.detect(missing) is None


# -- the source backend -------------------------------------------------------


def _manifest() -> PackageManifest:
    return PackageManifest.model_validate(
        {
            "name": "thing",
            "version": "1.0",
            "summary": "A thing built from source",
            "categories": ["digital-modes"],
            "install": [
                {
                    "install": {
                        "method": "source",
                        "source": SRC_PIN.model_dump(exclude_none=True),
                        "build_system": "autotools",
                    }
                }
            ],
            "update": {"probe": {"method": "none"}},
            "documentation": {
                "what_it_does": "Does a thing for the purposes of testing the backend.",
                "why_you_want_it": "Because the source backend needs a manifest to act on.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def _backend(tmp_path: Path, *, offline: bool, isolation: str | None) -> SourceBackend:
    fetcher = Fetcher(
        tmp_path / "cache", offline=offline, mirror="http://bunker.invalid" if offline else None
    )
    return SourceBackend(
        fetcher,
        build_root=tmp_path / "build",
        prefix=tmp_path / "prefix",
        isolation=isolation,
    )


def _commands(backend: SourceBackend) -> list[Command]:
    manifest = _manifest()
    return [s for s in backend.steps(manifest, manifest.install[0]) if isinstance(s, Command)]


def test_every_offline_build_command_runs_inside_the_sandbox(tmp_path: Path) -> None:
    backend = _backend(tmp_path, offline=True, isolation="bwrap")
    manifest = _manifest()
    layout = backend.layout(manifest, SRC_BLOCK)
    commands = _commands(backend)
    assert [c for c in commands if "./configure" in c.argv]
    for command in commands:
        split = command.argv.index("--", command.argv.index("--unshare-net"))
        prefix = command.argv[: split + 1]
        assert prefix[0] == "bwrap" and "--ro-bind" in prefix and "--new-session" in prefix
        # writable: the build tree and the install prefix, nothing else
        binds = [prefix[i + 1] for i, a in enumerate(prefix) if a == "--bind-try"]
        assert binds == [str(layout.root), str(tmp_path / "prefix")]
        assert command.argv[split + 1] in {"./configure", "make", "autoreconf", "install"}


def test_the_sandbox_is_part_of_the_plan_text(tmp_path: Path) -> None:
    commands = _commands(_backend(tmp_path, offline=True, isolation="bwrap"))
    shown = [c.display(euid=1000) for c in commands]
    assert any(
        d.startswith("cd ")
        and "--unshare-net" in d
        and "--ro-bind / /" in d
        and "--tmpfs /run" in d
        for d in shown
    )


def test_the_root_install_step_is_sandboxed_under_sudo(tmp_path: Path) -> None:
    backend = SourceBackend(
        Fetcher(tmp_path / "cache", offline=True, mirror="http://bunker.invalid"),
        build_root=tmp_path / "build",
        isolation="bwrap",
    )  # the default prefix needs root
    manifest = _manifest()
    install = [s for s in backend.steps(manifest, manifest.install[0]) if isinstance(s, Command)][
        -1
    ]
    assert install.requires_root
    assert install.argv[0] == "bwrap" and install.argv[-2:] == ("make", "install")
    assert str(Path("/usr/local")) in install.argv  # the prefix is the one writable system path
    assert install.argv_for(euid=1000)[0] == "sudo"


def test_online_builds_are_not_sandboxed_even_when_a_sandbox_is_known(tmp_path: Path) -> None:
    for isolation in (None, "bwrap"):
        for command in _commands(
            _backend(tmp_path / str(isolation), offline=False, isolation=isolation)
        ):
            assert command.argv[0] != "bwrap", command.argv


def test_the_unpack_and_tree_steps_are_not_wrapped(tmp_path: Path) -> None:
    manifest = _manifest()
    steps = _backend(tmp_path, offline=True, isolation="bwrap").steps(manifest, manifest.install[0])
    assert [s.kind for s in steps if isinstance(s, Action)][:2] == ["fetch", "extract"]


def test_offline_with_no_sandbox_the_backend_refuses_to_plan_a_build(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="bwrap"):
        _commands(_backend(tmp_path, offline=True, isolation=None))


def test_unshare_is_not_an_accepted_build_sandbox(tmp_path: Path) -> None:
    """It cannot hide the host's UNIX sockets or make / read-only."""
    with pytest.raises(BackendError, match="bwrap"):
        _commands(_backend(tmp_path, offline=True, isolation="unshare"))


# -- plan time -----------------------------------------------------------------


def _planned() -> PlannedPackage:
    manifest = _manifest()
    return PlannedPackage(manifest, manifest.install[0], (), requested_by=("requested",))


def test_the_plan_refuses_an_offline_source_build_without_isolation() -> None:
    plan = InstallPlan(TARGET, (_planned(),))
    found = offline_payload_blockers(
        plan, frozenset(), cached=lambda a: True, deb_unmet=lambda u: [], isolated=False
    )
    assert [b.subject for b in found] == ["thing"]
    assert "bwrap" in found[0].reason and "UNIX socket" in found[0].reason
    ok = offline_payload_blockers(
        plan, frozenset(), cached=lambda a: True, deb_unmet=lambda u: [], isolated=True
    )
    assert ok == []
    built = offline_payload_blockers(
        plan, frozenset({"thing"}), cached=lambda a: True, deb_unmet=lambda u: [], isolated=False
    )
    assert built == []


def test_cli_offline_install_refuses_a_source_unit_when_no_sandbox_works(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(
        tmp_path, [artifact("fixture-src", f"{SRC_PIN.sha256}/fixture-src-1.0.tar.gz", SRC_BODY)]
    )
    monkeypatch.setattr(cli.netiso, "detect", lambda *a, **k: None)
    rc, out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-src")
    assert rc == cli.EXIT_UNPLANNABLE and out == ""
    assert "fixture-src" in err and "bwrap" in err


def test_cli_offline_install_plans_the_sandboxed_build_when_one_works(
    payload_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")
    enrol_file_bunker(
        tmp_path, [artifact("fixture-src", f"{SRC_PIN.sha256}/fixture-src-1.0.tar.gz", SRC_BODY)]
    )
    monkeypatch.setattr(cli.netiso, "detect", lambda *a, **k: "bwrap")
    rc, out, err = run(capsys, payload_catalog, "install", "--offline", "--dry-run", "fixture-src")
    assert rc == 0, err
    assert "--unshare-net" in out and "--ro-bind / /" in out and "-- ./configure" in out
    assert "build with no network" in out


def test_cli_online_install_does_not_probe_or_wrap(
    payload_catalog: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apt_state(monkeypatch, installed="1.0")

    def never(*a: object, **k: object) -> None:
        raise AssertionError("an online run probed for a sandbox")

    monkeypatch.setattr(cli.netiso, "detect", never)
    rc, out, err = run(capsys, payload_catalog, "install", "--dry-run", "fixture-src")
    assert rc == 0, err
    assert "unshare" not in out and "bwrap" not in out


# -- real sockets ----------------------------------------------------------------

PROBE = (
    "import os,socket,sys\n"
    "def attempt(kind, target):\n"
    "    s = socket.socket(kind)\n"
    "    s.settimeout(3)\n"
    "    try:\n"
    "        s.connect(target)\n"
    "        return 'connected'\n"
    "    except OSError:\n"
    "        return 'blocked'\n"
    "port, unix_tmp, unix_run, unix_home, unix_writable, writable, readonly = sys.argv[1:8]\n"
    "def can_write(directory):\n"
    "    try:\n"
    "        open(os.path.join(directory, 'probe'), 'w').close()\n"
    "        return 'writable'\n"
    "    except OSError:\n"
    "        return 'read-only'\n"
    "print(attempt(socket.AF_INET, ('127.0.0.1', int(port))))\n"
    "print(attempt(socket.AF_UNIX, unix_tmp))\n"
    "print(attempt(socket.AF_UNIX, unix_run))\n"
    "print(attempt(socket.AF_UNIX, unix_home))\n"
    "print(attempt(socket.AF_UNIX, unix_writable))\n"
    "print(can_write(writable))\n"
    "print(can_write(readonly))\n"
)

# Same four socket checks, without the write-capability pair: the writable
# (.deb-install) sandbox leaves the rest of the real filesystem writable by
# design (apt.py:_isolated's docstring), which an unprivileged test has no
# other-writable-directory to prove against — the argv-shape tests above
# (--bind, not --ro-bind; no /opt or /usr/local in the hidden set) cover that
# half instead.
SOCKET_PROBE = (
    "import socket,sys\n"
    "def attempt(kind, target):\n"
    "    s = socket.socket(kind)\n"
    "    s.settimeout(3)\n"
    "    try:\n"
    "        s.connect(target)\n"
    "        return 'connected'\n"
    "    except OSError:\n"
    "        return 'blocked'\n"
    "port, unix_tmp, unix_run, unix_home = sys.argv[1:5]\n"
    "print(attempt(socket.AF_INET, ('127.0.0.1', int(port))))\n"
    "print(attempt(socket.AF_UNIX, unix_tmp))\n"
    "print(attempt(socket.AF_UNIX, unix_run))\n"
    "print(attempt(socket.AF_UNIX, unix_home))\n"
)


def _serve(path: str | None = None) -> tuple[socket.socket, str]:
    server = socket.socket(socket.AF_UNIX if path else socket.AF_INET)
    if path:
        server.bind(path)
    else:
        server.bind(("127.0.0.1", 0))
    server.listen(8)
    threading.Thread(target=lambda: [server.accept() for _ in range(8)], daemon=True).start()
    return server, path or str(server.getsockname()[1])


def test_a_real_sandbox_hides_ip_and_unix_sockets_and_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    if netiso.detect() is None:
        pytest.skip("bwrap does not run the build sandbox on this machine")
    runtime = Path(tempfile.mkdtemp(prefix="xdg-runtime-"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    fake_home = Path(tempfile.mkdtemp(prefix="fake-home-"))
    monkeypatch.setenv("HOME", str(fake_home))
    tmp_dir = Path(tempfile.mkdtemp(prefix="bridge-", dir="/tmp"))
    tmp_socket = str(tmp_dir / "bridge.sock")
    run_socket = str(runtime / "bridge.sock")
    home_socket = str(fake_home / "bridge.sock")
    writable = tmp_path / "build"
    writable.mkdir()
    writable_socket = str(writable / "bridge.sock")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    servers = []
    try:
        ip, port = _serve()
        servers.append(ip)
        for path in (tmp_socket, run_socket, home_socket, writable_socket):
            servers.append(_serve(path)[0])
        argv = (
            sys.executable,
            "-I",
            "-c",
            PROBE,
            port,
            tmp_socket,
            run_socket,
            home_socket,
            writable_socket,
            str(writable),
            str(outside),
        )
        plain = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
        assert plain.stdout.split() == [
            "connected",
            "connected",
            "connected",
            "connected",
            "connected",
            "writable",
            "writable",
        ], plain
        boxed = subprocess.run(
            netiso.sandbox(argv, writable=[writable]),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # The writable bind is the documented residual (netiso.py's module
        # docstring): it must stay real, so a socket planted there -- same
        # as a real /usr/local's would be, since that is always one of this
        # engine's writable binds -- is reachable from inside the sandbox
        # too. Everything else the common-hiding-places list covers is not.
        assert boxed.stdout.split() == [
            "blocked",
            "blocked",
            "blocked",
            "blocked",
            "connected",
            "writable",
            "read-only",
        ], boxed
    finally:
        for server in servers:
            server.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(runtime, ignore_errors=True)
        shutil.rmtree(fake_home, ignore_errors=True)


def test_a_real_privileged_sandbox_hides_the_same_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The .deb-install sandbox (``netiso.privileged_sandbox``) blocks the
    same IP and pathname-socket bridges as the build sandbox, even though its
    filesystem stays writable."""
    import tempfile

    if netiso.detect() is None:
        pytest.skip("bwrap does not run the sandbox on this machine")
    runtime = Path(tempfile.mkdtemp(prefix="xdg-runtime-"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    fake_home = Path(tempfile.mkdtemp(prefix="fake-home-"))
    monkeypatch.setenv("HOME", str(fake_home))
    tmp_dir = Path(tempfile.mkdtemp(prefix="bridge-", dir="/tmp"))
    tmp_socket = str(tmp_dir / "bridge.sock")
    run_socket = str(runtime / "bridge.sock")
    home_socket = str(fake_home / "bridge.sock")
    servers = []
    try:
        ip, port = _serve()
        servers.append(ip)
        for path in (tmp_socket, run_socket, home_socket):
            servers.append(_serve(path)[0])
        argv = (sys.executable, "-I", "-c", SOCKET_PROBE, port, tmp_socket, run_socket, home_socket)
        plain = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
        assert plain.stdout.split() == ["connected", "connected", "connected", "connected"], plain
        boxed = subprocess.run(
            netiso.privileged_sandbox(argv), capture_output=True, text=True, timeout=60, check=False
        )
        assert boxed.stdout.split() == ["blocked", "blocked", "blocked", "blocked"], boxed
    finally:
        for server in servers:
            server.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(runtime, ignore_errors=True)
        shutil.rmtree(fake_home, ignore_errors=True)
