# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Turning a plan into commands, and running them.

``--dry-run`` must be complete and accurate rather than approximate, so the dry
run and the real run call :func:`commands_for` and get the *same list*. The dry
run prints it; the real run hands it to a runner. There is no code path that
reconstructs what would have happened, because a reconstruction is a thing that
drifts from the original and nobody notices until it matters.

Execution stops at the first failure. D-016: an unresolvable dependency is a
hard error that stops the run, never a warning the run continues past. The
transaction log records what completed, which is what ``uninstall`` reads —
D-004 promises a log and honest reporting, never rollback.
"""

from __future__ import annotations

import contextlib
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Protocol

from hammunition import devctl_services, runlog
from hammunition.backends import (
    Action,
    AptBackend,
    AptPackageState,
    AptRepoBackend,
    BackendError,
    BinaryBackend,
    Command,
    CommandRunner,
    DataBackend,
    DerivedBackend,
    GitBackend,
    NodeBackend,
    RegionsBackend,
    SourceBackend,
    VenvBackend,
)
from hammunition.backends.comaps_maps import ComapsMapsBackend
from hammunition.backends.dem import DemTilesBackend
from hammunition.backends.derived import Ledger
from hammunition.backends.kiwix import KiwixBooksBackend
from hammunition.backends.source import tree_destination
from hammunition.backends.topo import TopoQuadsBackend
from hammunition.devctl_helper import HELPER_PATH, under_prefix
from hammunition.distro import Target
from hammunition.interface.plan_group import LONG_STEP_NOTE
from hammunition.launchers import launcher_steps
from hammunition.manifest.schema import (
    AptInstall,
    BinaryInstall,
    DataInstall,
    DemTilesInstall,
    DerivedDataInstall,
    GitInstall,
    InstallBlock,
    KiwixBooksInstall,
    MwmRegionsInstall,
    NodeInstall,
    RegionalDataInstall,
    RegisterInstall,
    SourceInstall,
    TopoQuadsInstall,
    VenvInstall,
    effective_binaries,
)
from hammunition.paths import OperatorDirError, open_operator_dir, operator_for
from hammunition.plan import InstallPlan, PlannedPackage
from hammunition.state import RemovalPlan, TransactionLog
from hammunition.userservice import PlannedUserService
from hammunition.userservice import is_ours as is_user_service_ours

#: One entry in a plan: a process to run, or something the engine does itself.
#: `--dry-run` prints these and a real run performs them, from the same objects.
Step = Command | Action

__all__ = [
    "EffectCheck",
    "ExecutionReport",
    "PackageProber",
    "Step",
    "StepOwners",
    "Verification",
    "commands_for",
    "completion_on_disk",
    "completion_states",
    "execute",
    "run_removal",
    "user_groups",
    "verify_effects",
]


class PackageProber(Protocol):
    """Anything that can answer what apt knows about a set of packages *now*.

    :class:`~hammunition.backends.AptBackend` satisfies this structurally. The
    protocol exists so :func:`verify_effects` can be tested against a fake that
    returns a chosen state, rather than only against a live apt.
    """

    def probe(self, packages: Sequence[str]) -> dict[str, AptPackageState]: ...


def _declares_installed_binaries(block: InstallBlock) -> bool:
    """Whether a block's `binaries` name files the run puts at ``<prefix>/bin``.

    True of source and git builds, and of binary units other than a ``.deb``
    (whose contents apt places; its ``deb_package`` is what apt is asked
    about instead, in ``verify_effects``). A venv's entry points
    are wrappers in the operator's ``~/.local/bin`` and are not `binaries`.
    """
    method = block.install
    if isinstance(method, SourceInstall | GitInstall):
        return True
    return isinstance(method, BinaryInstall) and method.format != "deb"


def _extra_files(block: InstallBlock) -> tuple[str, ...]:
    """A git block's `extra_files`, as paths under the prefix (D-069)."""
    method = block.install
    if isinstance(method, GitInstall):
        return tuple(extra.install_as for extra in method.extra_files)
    return ()


def _tree_marker(block: InstallBlock) -> str | None:
    """The marker file of a block that installs a tree, else None.

    Source, git and non-deb binary blocks install one when ``install_tree``
    is set; a venv installs one when it carries a ``payload``. The schema
    makes the marker mandatory in exactly those cases, so a None here means
    "no tree", never "a tree nobody named".
    """
    method = block.install
    if isinstance(method, SourceInstall | GitInstall | BinaryInstall | VenvInstall):
        return method.tree_marker
    return None


def user_groups(user: str) -> frozenset[str]:
    """Every group *user* belongs to, primary included.

    Read from the system's group database rather than by shelling out, so it
    works identically in a container with no ``id`` binary. An unknown user is
    not an error here: it means "belongs to nothing we know of", and the caller
    is about to add them to something anyway.
    """
    names: set[str] = set()
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return frozenset()
    with contextlib.suppress(KeyError):
        names.add(grp.getgrgid(entry.pw_gid).gr_name)
    names.update(group.gr_name for group in grp.getgrall() if user in group.gr_mem)
    return frozenset(names)


def already_present(existing: str, patterns: Sequence[str]) -> str | None:
    """The first line of *existing* that one of *patterns* matches, or None.

    An append whose ``skip_if_present`` matches is not made: the line is
    already there (a re-run), or something the file cannot hold twice is --
    ``axports`` refuses a second port with the same name or callsign.
    """
    compiled = [re.compile(p) for p in patterns]
    for line in existing.splitlines():
        if any(c.search(line) for c in compiled):
            return line
    return None


def write_config(
    path: Path,
    body: str,
    mode: int,
    *,
    append: bool,
    backup: bool,
    skip_if_present: Sequence[str] = (),
) -> str:
    """Write one templated configuration file. Returns a one-line outcome.

    Backing up first is the default and matters: these paths belong to the
    distribution's packages as often as to us, and overwriting an operator's
    hand-tuned `/etc/ax25/axports` without a copy would be the kind of damage
    no transaction log can undo.
    """
    if append and skip_if_present and path.exists():
        found = already_present(path.read_text(), skip_if_present)
        if found is not None:
            return f"left {path} as it is: it already has {found.strip()!r}"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = ""
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".hammunition-backup")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
            saved = f", previous contents saved to {backup_path.name}"
        else:
            saved = f", {backup_path.name} already exists and was left alone"
    if append and path.exists():
        with path.open("a") as handle:
            handle.write(body if body.endswith("\n") else body + "\n")
        action = "appended to"
    else:
        path.write_text(body if body.endswith("\n") else body + "\n")
        action = "wrote"
    os.chmod(path, mode)
    return f"{action} {path} (mode {mode:04o}){saved}"


def write_operator_config(
    path: Path,
    body: str,
    mode: int,
    *,
    append: bool,
    backup: bool,
    skip_if_present: Sequence[str] = (),
    owner: str | None = None,
) -> str:
    """:func:`write_config` for a file in an operator's home, written by root.

    ``~/`` in a manifest's path is the operator's home, and under sudo the
    engine is root. Root writing there by path would follow a symlink the
    operator (or anything running as them) planted -- ``~/.config/Gpredict``
    pointing at ``/etc`` -- and would leave root-owned files the operator's
    own program then cannot rewrite. So the parent is opened through
    :func:`~hammunition.paths.open_operator_dir` (``O_NOFOLLOW`` from the
    home down, each component proven the operator's, missing ones created and
    handed over), every file is opened by name relative to that descriptor
    with ``O_NOFOLLOW``, and what root writes is ``fchown``-ed to the
    operator. An existing entry that is not a regular file is refused.
    """
    entry = operator_for(path, owner)
    if entry is None:
        return write_config(
            path, body, mode, append=append, backup=backup, skip_if_present=skip_if_present
        )
    try:
        fd = open_operator_dir(path.parent, entry.pw_name)
    except OperatorDirError as exc:
        raise BackendError(f"{path} not written: {exc}") from exc
    if fd is None:
        # The parent is the home itself, which the walk starts from rather than
        # proves; the account database named it.
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    name = path.name
    backup_name = name + ".hammunition-backup"
    flags = os.O_NOFOLLOW | os.O_CLOEXEC

    def _existing() -> bytes | None:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(st.st_mode):
            raise BackendError(
                f"{path} is not a regular file (a symlink or something else); root will "
                f"not write through it on {entry.pw_name}'s behalf. Move it aside and "
                f"install again."
            )
        handle = os.open(name, os.O_RDONLY | flags, dir_fd=fd)
        with os.fdopen(handle, "rb") as reader:
            return reader.read()

    def _write(target: str, data: bytes, *, how: int, perm: int) -> None:
        handle = os.open(target, os.O_WRONLY | os.O_CREAT | how | flags, perm, dir_fd=fd)
        try:
            os.write(handle, data)
            os.fchmod(handle, perm)
            os.fchown(handle, entry.pw_uid, entry.pw_gid)
        finally:
            os.close(handle)

    try:
        existing = _existing()
        if append and skip_if_present and existing is not None:
            found = already_present(existing.decode(errors="replace"), skip_if_present)
            if found is not None:
                return f"left {path} as it is: it already has {found.strip()!r}"
        saved = ""
        if backup and existing is not None:
            try:
                os.stat(backup_name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                _write(backup_name, existing, how=os.O_EXCL, perm=mode)
                saved = f", previous contents saved to {backup_name}"
            else:
                saved = f", {backup_name} already exists and was left alone"
        text = (body if body.endswith("\n") else body + "\n").encode()
        if append and existing is not None:
            _write(name, text, how=os.O_APPEND, perm=mode)
            action = "appended to"
        else:
            _write(name, text, how=os.O_TRUNC, perm=mode)
            action = "wrote"
    finally:
        os.close(fd)
    return f"{action} {path} (mode {mode:04o}, {entry.pw_name}'s){saved}"


def stage_config(
    staging: Path,
    target: Path,
    body: str,
    *,
    append: bool,
    skip_if_present: Sequence[str] = (),
) -> str:
    """Render the *final* contents of a root-owned config into a staging file.

    The staged file is complete — for an append, the target's current contents
    plus the new block — so the privileged step is a plain ``install`` of a
    finished file, never a shell redirect or an in-place edit run as root.
    When ``skip_if_present`` matches, the staged file is the target unchanged,
    so the ``install`` that follows puts back exactly what was there.
    """
    if append and target.exists():
        try:
            existing = target.read_text()
        except OSError as exc:
            raise BackendError(
                f"{target} must be read to append to it, and that failed: "
                f"{exc.strerror}. Nothing was staged."
            ) from exc
        found = already_present(existing, skip_if_present) if skip_if_present else None
        if found is not None:
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.write_text(existing)
            os.chmod(staging, 0o600)
            return (
                f"staged {target} unchanged at {staging}: it already has "
                f"{found.strip()!r}, so nothing is appended"
            )
        body = existing + ("" if existing.endswith("\n") else "\n") + body
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text(body if body.endswith("\n") else body + "\n")
    os.chmod(staging, 0o600)
    return f"staged {target} contents at {staging}"


def config_steps(plan: InstallPlan, *, staging_root: Path | None = None) -> list[Step]:
    """The steps that write each config file the plan resolved. Never a partial file.

    `plan.config_files` holds only what could be fully rendered — a file
    missing a station value is a Deferral and never reaches here, because a
    config written with `{station.callsign}` still in it looks configured and
    is not.

    A path the operator can write gets one in-process Action, as before. A
    root-owned path (``/etc/bpq32.cfg``) cannot be written in-process by an
    unprivileged engine — the first Parrot VM run proved it with a
    ``PermissionError`` — so it becomes commands the runner can elevate: stage
    the finished contents unprivileged, ``cp -a`` the existing file to its
    backup (only when one exists and no backup does, decided at plan time,
    matching what the in-process path has always done), then
    ``install -m MODE`` the staged file into place. Every step is printed by
    ``--dry-run`` exactly as it will run.
    """
    steps: list[Step] = []
    staging_dir = (staging_root or Path(tempfile.gettempdir())) / "config-staging"
    for package, config, body in plan.config_files:
        path = Path(config.path)
        mode = int(config.mode, 8)
        verb = "Append" if config.append else "Write"
        # Writability of the nearest ancestor that exists, because that is the
        # directory mkdir -p would actually have to write into. Probing "/"
        # when the parent is merely not-yet-created classified a writable
        # tmp-dir target as root-only and routed it through sudo for nothing.
        probe = path.parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        needs_root = not os.access(probe, os.W_OK)
        operator = operator_for(path)
        if operator is not None:
            # Root, writing into somebody's home (a manifest's `~/` path):
            # never by path, and what it writes is theirs.
            steps.append(
                Action(
                    kind="config",
                    description=f"{verb} {config.path} for {package}",
                    detail=(
                        f"mode {config.mode}, "
                        + ("existing file backed up" if config.backup_existing else "no backup")
                        + f", written as root and handed to {operator.pw_name}"
                    ),
                    perform=partial(
                        write_operator_config,
                        path,
                        body,
                        mode,
                        append=config.append,
                        backup=config.backup_existing,
                        skip_if_present=tuple(config.skip_if_present),
                        owner=operator.pw_name,
                    ),
                )
            )
            continue
        if not needs_root:
            steps.append(
                Action(
                    kind="config",
                    description=f"{verb} {config.path} for {package}",
                    detail=(
                        f"mode {config.mode}, "
                        + ("existing file backed up" if config.backup_existing else "no backup")
                    ),
                    perform=partial(
                        write_config,
                        path,
                        body,
                        mode,
                        append=config.append,
                        backup=config.backup_existing,
                        skip_if_present=tuple(config.skip_if_present),
                    ),
                )
            )
            continue

        staging = staging_dir / f"{package}{path.name and '-' + path.name}"
        steps.append(
            Action(
                kind="config",
                description=f"Render {config.path} for {package} into a staging file",
                detail=f"staged at {staging}, mode 0600; installed by the next command",
                perform=partial(
                    stage_config,
                    staging,
                    path,
                    body,
                    append=config.append,
                    skip_if_present=tuple(config.skip_if_present),
                ),
            )
        )
        backup_path = path.with_suffix(path.suffix + ".hammunition-backup")
        if config.backup_existing and path.exists() and not backup_path.exists():
            steps.append(
                Command(
                    argv=("cp", "-a", str(path), str(backup_path)),
                    description=f"Back up the existing {config.path} first",
                    requires_root=True,
                )
            )
        # `install` creates the file, never its directory. A drop-in under a
        # directory nothing has made yet (`/etc/systemd/system/gpsd.service.d/`
        # for the chrony unit's gpsd `-n`) would fail there, so the directory
        # is made first, as its own printed step, only when it is missing now.
        if not path.parent.exists():
            steps.append(
                Command(
                    argv=("mkdir", "-p", "-m", "0755", str(path.parent)),
                    description=f"Create {path.parent} for {config.path}",
                    requires_root=True,
                )
            )
        steps.append(
            Command(
                argv=("install", "-m", config.mode, str(staging), str(path)),
                description=f"{verb} {config.path} for {package} (mode {config.mode})",
                requires_root=True,
            )
        )
    return steps


def _user_unit_path(home: Path, name: str) -> Path:
    return home / "systemd" / "user" / f"{name}.service"


def _systemctl(machine: str | None, *verb: str) -> tuple[str, ...]:
    """``systemctl --user …`` as the operator, with ``--machine`` when root is
    acting for a logged-in operator (D-062, D-073 §6c)."""
    prefix: tuple[str, ...] = ("systemctl", "--user")
    if machine is not None:
        prefix = (*prefix, f"--machine={machine}@.host")
    return (*prefix, *verb)


def user_service_steps(
    plan: InstallPlan, *, home: Path, machine: str | None = None, staging_root: Path | None = None
) -> list[Step]:
    """Write each planned user service into the operator's home, then reload,
    enable and (when the device is present) restart it.  D-073 §6c.

    The file is written like an operator-home config file: in-process as the
    operator, or ``write_operator_config`` when root is writing into their home.
    The ``systemctl --user`` steps are never escalated — the service is the
    operator's (D-062).
    """
    del staging_root  # written in-process, no staging file needed
    steps: list[Step] = []
    for svc in plan.user_services:
        path = _user_unit_path(home, svc.name)
        operator = operator_for(path)
        if operator is not None:
            steps.append(
                Action(
                    kind="user_service",
                    description=f"Write the {svc.name} user service",
                    detail=f"{path}, mode 0644, written as root and handed to {operator.pw_name}",
                    perform=partial(
                        write_operator_config,
                        path,
                        svc.unit_body,
                        0o644,
                        append=False,
                        backup=False,
                        owner=operator.pw_name,
                    ),
                )
            )
        else:
            steps.append(
                Action(
                    kind="user_service",
                    description=f"Write the {svc.name} user service",
                    detail=f"{path}, mode 0644",
                    perform=partial(
                        write_config, path, svc.unit_body, 0o644, append=False, backup=False
                    ),
                )
            )
        steps.append(
            Command(
                argv=_systemctl(machine, "daemon-reload"),
                description="Have systemd read the new user service",
            )
        )
        steps.append(
            Command(
                argv=_systemctl(machine, "enable", f"{svc.name}.service"),
                description=f"Enable {svc.name} so it starts at login",
            )
        )
        if svc.device_path is not None and Path(svc.device_path).exists():
            steps.append(
                Command(
                    argv=_systemctl(machine, "restart", f"{svc.name}.service"),
                    description=f"Start {svc.name} now (the radio's port is present)",
                )
            )
        elif svc.plain:
            # No device to wait for, so no first start here (it starts at
            # login), but a reinstall at a new pin must reach a copy that is
            # already running. try-restart does nothing to a stopped service.
            steps.append(
                Command(
                    argv=_systemctl(machine, "try-restart", f"{svc.name}.service"),
                    description=(
                        f"Restart {svc.name} if it is running, so it runs the installed program"
                    ),
                )
            )
    # One row per catalog unit in the tray's list of user services, after the
    # unit files exist (the helper reads it; nothing here runs as root).
    by_unit: dict[str, list[PlannedUserService]] = {}
    for svc in plan.user_services:
        by_unit.setdefault(svc.unit, []).append(svc)
    list_path = home / "hammunition" / devctl_services.FILE_NAME
    for services in by_unit.values():
        row = devctl_services.row_for(services)
        steps.append(
            Action(
                kind="devctl_row",
                description=f"List {row.name} for the tray's Services group",
                detail=f"{list_path}, mode 0600, row {row.name!r} -> {row.unit}",
                perform=partial(devctl_services.update_file, list_path, add=row),
            )
        )
    return steps


def _remove_user_unit_if_ours(path: Path) -> str:
    """Remove *path* only if it still starts with Hammunition's header; a file
    the operator rewrote is left and named (D-073 §6d)."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return f"{path} was already gone"
    if not is_user_service_ours(text):
        return f"left {path}: it no longer starts with Hammunition's header, so it is not ours"
    path.unlink()
    return f"removed {path}"


def user_service_removal_steps(
    names: Sequence[str],
    *,
    home: Path,
    machine: str | None = None,
    units: Sequence[str] = (),
) -> list[Step]:
    """Disable each named user service whose unit file exists, remove it if it
    is ours, then reload.  D-073 §6d.

    Only a name whose unit file is present now is touched: ``systemctl --user
    disable`` on a unit that was never written exits non-zero and would stop a
    removal at its first step (review I2), and a deferred install wrote no file.
    This also lets a reinstall that now skips a service (flrig, VOX) or a
    cleared rig disable and remove the one left running (review I1): the file is
    present though the service is no longer planned.
    """
    present = [name for name in names if _user_unit_path(home, name).exists()]
    steps: list[Step] = []
    for name in present:
        steps.append(
            Command(
                argv=_systemctl(machine, "disable", "--now", f"{name}.service"),
                description=f"Stop and disable {name}",
            )
        )
        path = _user_unit_path(home, name)
        steps.append(
            Action(
                kind="user_service",
                description=f"Remove the {name} user service if it is ours",
                detail=f"{path}, only if it still carries Hammunition's header",
                perform=partial(_remove_user_unit_if_ours, path),
            )
        )
    if present:
        steps.append(
            Command(
                argv=_systemctl(machine, "daemon-reload"),
                description="Have systemd forget the removed user service",
            )
        )
    list_path = home / "hammunition" / devctl_services.FILE_NAME
    for unit in units:
        row_name = devctl_services.service_name(unit)
        steps.append(
            Action(
                kind="devctl_row",
                description=f"Take {row_name} out of the tray's Services group",
                detail=f"{list_path}, its row only; the file goes when no row is left",
                perform=partial(devctl_services.update_file, list_path, remove_name=row_name),
            )
        )
    return steps


def build_dir(
    planned: PlannedPackage,
    *,
    source: SourceBackend | None = None,
    git: GitBackend | None = None,
    binary: BinaryBackend | None = None,
) -> Path | None:
    """Where this unit's build lives, or None for a unit that has no build.

    The directory name encodes the manifest's pin (digest or ref), which is
    what lets a log entry naming it attribute a build to *this* pin and not
    an earlier one. Pure: the backends' ``layout`` touches no disk.
    """
    method = planned.block.install
    if isinstance(method, SourceInstall) and source is not None:
        return source.layout(planned.manifest, method).src
    if isinstance(method, GitInstall) and git is not None:
        return git.layout(planned.manifest, method).src
    if isinstance(method, BinaryInstall) and method.format != "deb" and binary is not None:
        return binary.layout(planned.manifest, method).src
    return None


def placed_files(block: InstallBlock, prefix: Path) -> tuple[Path, ...]:
    """Every file a ``binary`` block's ``placements`` and ``devctl_helper`` install.

    The helper's files are not listed: where another owner's helper stands (a
    .deb, the tray's own installer) the unit leaves it, so its effect is that
    the helper answers, which :func:`verify_effects` asks of the helper itself.
    """
    method = block.install
    if not isinstance(method, BinaryInstall):
        return ()
    return tuple(under_prefix(p.dest, prefix) for p in method.placements)


def build_effects_present(planned: PlannedPackage, *, prefix: Path) -> bool | None:
    """Whether every effect this build declares is on disk under ``prefix``.

    ``True`` when every declared binary is executable at ``<prefix>/bin/`` and
    the tree marker, where a tree is installed, exists. ``False`` when any is
    missing. ``None`` when the unit declares neither, so nothing can be
    checked: the D-051 rule never decides such a unit, and ``update`` reports
    it as unknown rather than as either installed or not.
    """
    present: list[bool] = []
    for declared in effective_binaries(planned.manifest, planned.block):
        path = prefix / "bin" / declared.install_as
        present.append(path.is_file() and os.access(path, os.X_OK))
    marker = _tree_marker(planned.block)
    if marker is not None:
        present.append((tree_destination(prefix, planned.name) / marker).exists())
    if _declares_installed_binaries(planned.block):
        for declared_file in planned.manifest.installed_files:
            present.append((prefix / declared_file).exists())
    for extra in _extra_files(planned.block):
        path = prefix / extra
        present.append(path.is_file() and not path.is_symlink())
    present.extend(path.is_file() for path in placed_files(planned.block, prefix))
    method = planned.block.install
    if isinstance(method, BinaryInstall) and method.devctl_helper is not None:
        # A helper that answers nothing is not installed, whoever owns it: its
        # files being there is what a deleted venv leaves behind (the wrapper's
        # interpreter), and a re-run must repair that rather than call it done.
        from hammunition.hardware import polkit

        present.append(
            polkit.installed_helper_version(str(under_prefix(HELPER_PATH, prefix))) is not None
        )
    if not present:
        return None
    return all(present)


@dataclass
class StepOwners:
    """Which catalog units each planned step belongs to (#272).

    :func:`commands_for` fills it while it orders the steps; :func:`execute`
    reads it to write a ``unit_end`` when a unit's last step has finished. Keyed
    by the step object's identity: steps are frozen values that compare equal
    when they say the same thing, and two units' steps can.
    """

    owned: dict[int, list[str]] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)
    """A built unit's build directory; its name encodes the pin (D-051)."""
    versions: dict[str, str] = field(default_factory=dict)
    """The manifest's version the unit was planned at."""
    states: dict[str, str] = field(default_factory=dict)
    """Opaque state for regional and derived data units."""
    excluded: set[str] = field(default_factory=set)
    """Units that never get a ``unit_end``: their completion rests on a step
    that is not theirs alone (a map ledger that fails the run by name, a user
    service), so the record would say more than the run knows."""

    def own(self, step: Step, unit: str) -> None:
        units = self.owned.setdefault(id(step), [])
        if unit not in units:
            units.append(unit)

    def own_all(self, steps: Iterable[Step], unit: str) -> None:
        for step in steps:
            self.own(step, unit)

    def units_of(self, step: Step) -> tuple[str, ...]:
        return tuple(self.owned.get(id(step), ()))


def already_built(
    plan: InstallPlan,
    *,
    log: TransactionLog | None,
    prefix: Path,
    source: SourceBackend | None = None,
    git: GitBackend | None = None,
    binary: BinaryBackend | None = None,
    states: Mapping[str, str] | None = None,
    on_disk: Mapping[str, bool] | None = None,
) -> frozenset[str]:
    """Units whose build is already installed at the manifest's pin. D-051.

    Two halves, both required, mirroring the vendor-.deb rule (#67):

    * **The effect is present.** Every declared binary is executable at
      ``<prefix>/bin/<install_as>``; where a tree is installed, its marker
      exists under the tree destination. A unit that declares neither cannot
      be checked and is never decided here.
    * **The log attributes it, at this pin.** A ``verify-pin`` or ``extract``
      action for *exactly this build directory* -- whose name encodes the
      pin, so a moved ref or a re-pinned digest is a different path --
      followed in the same transaction by a verified ``transaction_end``
      that confirmed one of this unit's checks. A transaction that failed
      after the build steps (paracon, 2026-09-12) attributes nothing --
      unless the unit has its own ``unit_end`` at this pin (#272), written
      when its last step finished, which a later failure elsewhere does not
      take back; an ``uninstall`` naming the unit afterwards does.

    apt has its own answer and a .deb has #67's; venv and node units keep
    their cheap idempotency (``pip`` over a satisfied venv verifies and
    exits). Everything else was rebuilt on every re-run until this existed:
    the field laptop rebuilt 21 units twice in one afternoon.
    """
    if log is None:
        return frozenset()
    wanted: dict[str, str] = {}
    for planned in plan.packages:
        src = build_dir(planned, source=source, git=git, binary=binary)
        if src is None:
            continue
        if build_effects_present(planned, prefix=prefix):
            wanted[planned.name] = str(src)
    resumable = states or {}
    if not wanted and not resumable:
        return frozenset()
    attributed: set[str] = set()
    pending: set[str] = set()
    for entry in log.read():
        event = entry.get("event")
        if event == "unit_end":
            # #272: the unit's last step finished, inside a transaction that
            # may have failed afterwards in another unit. Believed only at
            # exactly this pin; the caller's effect check has already run.
            unit = str(entry.get("unit", ""))
            if (
                entry.get("ok") is True and unit in wanted and entry.get("pin") == wanted[unit]
            ) or (
                entry.get("ok") is True
                and unit in resumable
                and entry.get("state") == resumable[unit]
                and (on_disk or {}).get(unit) is True
            ):
                attributed.add(unit)
        elif event == "uninstall_begin":
            attributed.difference_update(str(p) for p in entry.get("packages", ()))
        elif event == "transaction_begin":
            pending.clear()
        elif event == "action_end" and entry.get("kind") in ("verify-pin", "extract"):
            detail = str(entry.get("detail", ""))
            pending.update(name for name, src in wanted.items() if src in detail)
        elif event == "transaction_end":
            if entry.get("verified") is True:
                confirmed = {
                    str(check.get("subject", "")).split(":", 1)[0]
                    for check in entry.get("checks", ())
                    if isinstance(check, dict) and check.get("confirmed")
                }
                attributed.update(name for name in pending if name in confirmed)
            pending.clear()
        elif event == "transaction_failed":
            pending.clear()
    return frozenset(attributed)


def _note_unit(
    track: StepOwners,
    planned: PlannedPackage,
    *,
    source: SourceBackend | None,
    git: GitBackend | None,
    binary: BinaryBackend | None,
    states: Mapping[str, str],
) -> None:
    """Record what a ``unit_end`` for *planned* will say, or that it gets none."""
    track.versions[planned.name] = planned.manifest.version
    src = build_dir(planned, source=source, git=git, binary=binary)
    if src is not None:
        track.pins[planned.name] = str(src)
    if planned.name in states:
        track.states[planned.name] = states[planned.name]


def _jsonable(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return _jsonable(enum_value)
    return value


def completion_states(
    plan: InstallPlan,
    *,
    regions: RegionsBackend | None = None,
    derived: DerivedBackend | None = None,
    dem: DemTilesBackend | None = None,
    topo: TopoQuadsBackend | None = None,
    mwm: ComapsMapsBackend | None = None,
) -> dict[str, str]:
    """Opaque fingerprints for map units, covering their resolved inputs and selection."""
    packages = {package.name: package for package in plan.packages}
    states: dict[str, str] = {}
    for planned in plan.packages:
        block = planned.block.install
        if isinstance(block, RegionalDataInstall):
            context: dict[str, object] = {
                "block": block.model_dump(mode="json"),
                "regions": regions.files if regions is not None else (),
            }
        elif isinstance(block, DerivedDataInstall):
            inputs = {
                name: packages[name].block.install.model_dump(mode="json")
                for name in block.inputs()
                if name in packages
            }
            context = {
                "block": block.model_dump(mode="json"),
                "inputs": inputs,
                "regions": derived.files if derived is not None else (),
            }
            terrain_inputs: dict[str, object] = {}
            if dem is not None:
                for name in block.inputs():
                    source_package = packages.get(name)
                    if source_package is None or not isinstance(
                        source_package.block.install, DemTilesInstall
                    ):
                        continue
                    dem_backend: DemTilesBackend | None = dem
                    if source_package.block.install.provider != dem.provider:
                        dem_backend = dem.bare_earth
                    if dem_backend is not None:
                        terrain_inputs[name] = dem_backend.resolution
            if terrain_inputs:
                context["terrain"] = terrain_inputs
            if topo is not None and (
                block.fstopo is not None
                or any(
                    isinstance(packages[name].block.install, TopoQuadsInstall)
                    for name in block.inputs()
                    if name in packages
                )
            ):
                context["topo"] = topo.resolution
                if block.fstopo is not None and topo.fstopo is not None:
                    context["fstopo"] = topo.fstopo.resolution
        elif isinstance(block, DemTilesInstall):
            install_dem_backend: DemTilesBackend | None = dem
            if install_dem_backend is not None and block.provider != install_dem_backend.provider:
                install_dem_backend = install_dem_backend.bare_earth
            context = {
                "block": block.model_dump(mode="json"),
                "resolution": (
                    install_dem_backend.resolution if install_dem_backend is not None else ()
                ),
            }
        elif isinstance(block, TopoQuadsInstall):
            topo_backend = topo
            resolution: object = ()
            if topo_backend is not None:
                if block.provider == "usfs-fstopo" and topo_backend.fstopo is not None:
                    resolution = topo_backend.fstopo.resolution
                else:
                    resolution = topo_backend.resolution
            context = {"block": block.model_dump(mode="json"), "resolution": resolution}
        elif isinstance(block, MwmRegionsInstall):
            context = {
                "block": block.model_dump(mode="json"),
                "maps": mwm.files if mwm is not None else (),
            }
        else:
            continue
        encoded = json.dumps(_jsonable(context), sort_keys=True, separators=(",", ":")).encode()
        states[planned.name] = hashlib.sha256(encoded).hexdigest()
    return states


def completion_on_disk(
    plan: InstallPlan,
    *,
    regions: RegionsBackend | None = None,
    derived: DerivedBackend | None = None,
    dem: DemTilesBackend | None = None,
    topo: TopoQuadsBackend | None = None,
    mwm: ComapsMapsBackend | None = None,
) -> dict[str, bool]:
    """Whether each resolved map unit's backend confirms all expected outputs current."""
    from hammunition.backends.dem import TIF as DATA_TIF
    from hammunition.backends.dem import TILES as DEM_TILES
    from hammunition.backends.dem import read_record as read_dem_record
    from hammunition.backends.fstopo import read_record as read_fstopo_record
    from hammunition.backends.topo import QUADS as TOPO_QUADS
    from hammunition.backends.topo import TIF as TOPO_TIF
    from hammunition.backends.topo import read_record as read_topo_record

    current: dict[str, bool] = {}
    for planned in plan.packages:
        block = planned.block.install
        if isinstance(block, RegionalDataInstall):
            current[planned.name] = regions is not None and not regions.pending(planned.manifest)
        elif isinstance(block, DerivedDataInstall):
            backend = derived
            if backend is None:
                current[planned.name] = False
                continue
            if block.converter == "navit-maptool":
                current[planned.name] = not backend.pending(planned.manifest)
                continue
            converter = backend.converters.get(block.converter)
            pending = getattr(converter, "pending", None)
            if not callable(pending):
                current[planned.name] = False
                continue
            if block.converter in {
                "routino-planetsplitter",
                "brouter-mapcreator",
                "mapsforge-map",
                "mapsforge-poi",
                "tilemaker-pmtiles",
                "graphhopper-import",
            }:
                has_pending = bool(pending(planned.manifest, block))
            else:
                has_pending = bool(pending(planned.manifest))
            converter_current = getattr(converter, "current", None)
            current[planned.name] = (
                not has_pending and converter_current(planned.manifest)
                if callable(converter_current)
                else not has_pending
            )
        elif isinstance(block, DemTilesInstall):
            dem_backend = dem
            if dem_backend is not None and block.provider != dem_backend.provider:
                dem_backend = dem_backend.bare_earth
            if (
                dem_backend is None
                or dem_backend.resolution.fetch
                or dem_backend.resolution.deferred
            ):
                current[planned.name] = False
                continue
            out = dem_backend.data_dir(planned.manifest)
            records_current = all(
                read_dem_record(out / f"{entry.slug}{DEM_TILES}", entry.region, entry.slug) == entry
                for entry in dem_backend.resolution.regions
            )
            current[planned.name] = records_current and all(
                (out / f"{name}{DATA_TIF}").is_file() for name in dem_backend.resolution.tiles
            )
        elif isinstance(block, TopoQuadsInstall):
            topo_backend = topo
            fs_backend = (
                topo_backend.fstopo
                if topo_backend is not None and block.provider == "usfs-fstopo"
                else None
            )
            if topo_backend is None:
                current[planned.name] = False
                continue
            if block.provider == "usfs-fstopo" and fs_backend is None:
                current[planned.name] = False
                continue
            resolution = (
                fs_backend.resolution if fs_backend is not None else topo_backend.resolution
            )
            if resolution is None or resolution.fetch or resolution.deferred:
                current[planned.name] = False
                continue
            if fs_backend is not None:
                out = fs_backend.data_dir(planned.manifest)
                records_current = all(
                    read_fstopo_record(out / f"{entry.slug}{TOPO_QUADS}", entry.region, entry.slug)
                    == entry
                    for entry in resolution.regions
                )
            else:
                out = topo_backend.data_dir(planned.manifest)
                records_current = all(
                    read_topo_record(out / f"{entry.slug}{TOPO_QUADS}", entry.region, entry.slug)
                    == entry
                    for entry in resolution.regions
                )
            current[planned.name] = records_current and all(
                (out / f"{quad.name}{TOPO_TIF}").is_file() for quad in resolution.wanted
            )
        elif isinstance(block, MwmRegionsInstall):
            current[planned.name] = mwm is not None and not mwm.pending(planned.manifest)
    return current


def commands_for(
    plan: InstallPlan,
    apt: AptBackend,
    *,
    refresh: bool = False,
    current_groups: frozenset[str] | None = None,
    source: SourceBackend | None = None,
    git: GitBackend | None = None,
    binary: BinaryBackend | None = None,
    venv: VenvBackend | None = None,
    node: NodeBackend | None = None,
    data: DataBackend | None = None,
    regions: RegionsBackend | None = None,
    derived: DerivedBackend | None = None,
    dem: DemTilesBackend | None = None,
    topo: TopoQuadsBackend | None = None,
    books: KiwixBooksBackend | None = None,
    mwm: ComapsMapsBackend | None = None,
    repos: AptRepoBackend | None = None,
    config_staging: Path | None = None,
    launcher_bin: Path | None = None,
    launcher_applications: Path | None = None,
    user_services_home: Path | None = None,
    user_services_machine: str | None = None,
    skip_builds: frozenset[str] = frozenset(),
    owners: StepOwners | None = None,
) -> list[Step]:
    """Every step this plan implies, in the order it will run.

    The order is not cosmetic. Every download runs first, before anything
    that changes the machine: a fetch is verified against the manifest's
    sha256 (D-018), so a wrong hash or a dead URL refuses on an untouched
    system rather than after apt has installed a toolchain for a build that
    will never start; and a vendor .deb, which apt can only resolve from
    its file, is simulated against the apt step once fetched and before
    either runs -- the half of the "plan passed, machine touched, then it
    failed" shape that D-038's plan-time simulate could not reach
    (`docs/reference/vm-campaign-ubuntu.md`, 2026-09-02). apt runs next
    because a source build needs its
    ``build_depends`` — its compiler, its headers — present before ``./configure``
    can succeed. Group membership comes last because several of these groups are
    created by the package being installed (Debian's ``wireshark-common`` creates
    ``wireshark``), so adding the operator first would fail on a group that does
    not exist yet.

    ``source`` and ``git`` are required if the plan holds a build of that kind,
    and an absence is an error rather than a silent skip — a plan that quietly dropped the one
    step that installs the software would report success having done nothing.

    ``owners``, when given, is filled with which unit each step belongs to, so
    :func:`execute` can record a unit's completion when its last step ends (#272).
    """
    track = owners if owners is not None else StepOwners()
    states = completion_states(plan, regions=regions, derived=derived, dem=dem, topo=topo, mwm=mwm)
    # Each backend's steps in build order; the fetches are lifted out below.
    builds: list[Step] = []
    # A conversion reads data another unit installs in this same run, and
    # plan order is `after`, not `depends` -- osm-navit sorts before
    # osm-regions, dem-qmapshack before dem-copernicus. So every conversion
    # runs after every other build: the regions and the terrain tiles are
    # installed before mkgmap, planetsplitter or GDAL reads them.
    conversions: list[Step] = []
    # One map region failing does not stop the others (spec §8); the ledger
    # the map backends share fails the transaction by name, as its last step.
    ledgers: dict[int, Ledger] = {}
    ledger_units: dict[int, set[str]] = {}
    marks: list[tuple[PlannedPackage, int, int]] = []
    for planned in plan.packages:
        block = planned.block.install
        marks.append((planned, len(builds), len(conversions)))
        _note_unit(track, planned, source=source, git=git, binary=binary, states=states)
        if planned.name in skip_builds and isinstance(
            block,
            SourceInstall
            | GitInstall
            | BinaryInstall
            | RegionalDataInstall
            | DerivedDataInstall
            | DemTilesInstall
            | TopoQuadsInstall
            | MwmRegionsInstall,
        ):
            # Already built at this pin (D-051, already_built): no fetch, no
            # build, no install. Its launchers and config still run below.
            continue
        if isinstance(block, SourceInstall):
            if source is None:
                raise BackendError(
                    f"{planned.name} is a source build and no source backend was supplied. "
                    f"Skipping it would report a successful run that installed nothing."
                )
            builds.extend(source.steps(planned.manifest, planned.block))
        elif isinstance(block, GitInstall):
            if git is None:
                raise BackendError(
                    f"{planned.name} builds from git and no git backend was supplied. "
                    f"Skipping it would report a successful run that installed nothing."
                )
            builds.extend(git.steps(planned.manifest, planned.block))
        elif isinstance(block, BinaryInstall):
            if planned.deb_installed:
                # The plan attributed this .deb to us and dpkg still holds it
                # (#63): the fetch would be a cache hit and the root apt-get
                # a no-op. Nothing to do is nothing planned.
                continue
            if binary is None:
                raise BackendError(
                    f"{planned.name} installs a prebuilt artifact and no binary backend "
                    f"was supplied. Skipping it would report a successful run that "
                    f"installed nothing."
                )
            builds.extend(binary.steps(planned.manifest, planned.block))
        elif isinstance(block, VenvInstall):
            if venv is None:
                raise BackendError(
                    f"{planned.name} installs into a virtualenv and no venv backend "
                    f"was supplied. Skipping it would report a successful run that "
                    f"installed nothing."
                )
            builds.extend(venv.steps(planned.manifest, block))
        elif isinstance(block, NodeInstall):
            if node is None:
                raise BackendError(
                    f"{planned.name} is a Node.js application and no node backend "
                    f"was supplied. Skipping it would report a successful run that "
                    f"installed nothing."
                )
            builds.extend(node.steps(planned.manifest, block))
        elif isinstance(block, DataInstall):
            if data is None:
                raise BackendError(
                    f"{planned.name} is an offline dataset and no data backend was "
                    f"supplied. Skipping it would report a successful run that "
                    f"installed nothing."
                )
            builds.extend(data.steps(planned.manifest, block))
        elif isinstance(block, RegisterInstall):
            if data is None:
                raise BackendError(
                    f"{planned.name} is an offline dataset and no data backend was "
                    f"supplied. Skipping it would report a successful run that "
                    f"installed nothing."
                )
            builds.extend(data.register_steps(planned.manifest, block))
        elif isinstance(block, RegionalDataInstall):
            if regions is None:
                raise BackendError(
                    f"{planned.name} installs the station's map regions and no regions "
                    f"backend was supplied. Skipping it would report a successful run "
                    f"that installed nothing."
                )
            builds.extend(regions.steps(planned.manifest, block))
            ledgers.setdefault(id(regions.ledger), regions.ledger)
            ledger_units.setdefault(id(regions.ledger), set()).add(planned.name)
        elif isinstance(block, DerivedDataInstall):
            if derived is None:
                raise BackendError(
                    f"{planned.name} converts another unit's data and no derived "
                    f"backend was supplied. Skipping it would report a successful run "
                    f"that installed nothing."
                )
            conversions.extend(derived.steps(planned.manifest, block))
            for ledger in derived.ledgers(block):
                ledgers.setdefault(id(ledger), ledger)
                ledger_units.setdefault(id(ledger), set()).add(planned.name)
        elif isinstance(block, DemTilesInstall):
            if dem is None:
                raise BackendError(
                    f"{planned.name} installs the station's terrain tiles and no dem-tiles "
                    f"backend was supplied. Skipping it would report a successful run "
                    f"that installed nothing."
                )
            builds.extend(dem.steps(planned.manifest, block))
            ledgers.setdefault(id(dem.ledger), dem.ledger)
            ledger_units.setdefault(id(dem.ledger), set()).add(planned.name)
        elif isinstance(block, TopoQuadsInstall):
            if topo is None:
                raise BackendError(
                    f"{planned.name} installs the station's US Topo sheets and no topo-quads "
                    f"backend was supplied. Skipping it would report a successful run "
                    f"that installed nothing."
                )
            builds.extend(topo.steps(planned.manifest, block))
            ledgers.setdefault(id(topo.ledger), topo.ledger)
            ledger_units.setdefault(id(topo.ledger), set()).add(planned.name)
        elif isinstance(block, KiwixBooksInstall):
            if books is None:
                raise BackendError(
                    f"{planned.name} installs the station's reference books and no books "
                    f"backend was supplied. Skipping it would report a successful run "
                    f"that installed nothing."
                )
            builds.extend(books.steps(planned.manifest, block))
        elif isinstance(block, MwmRegionsInstall):
            if mwm is None:
                raise BackendError(
                    f"{planned.name} installs CoMaps' maps for the station's regions and no "
                    f"mwm-regions backend was supplied. Skipping it would report a "
                    f"successful run that installed nothing."
                )
            builds.extend(mwm.steps(planned.manifest, block))
    ends = [*((b, c) for _, b, c in marks[1:]), (len(builds), len(conversions))][: len(marks)]
    for (planned, b0, c0), (b1, c1) in zip(marks, ends, strict=True):
        track.own_all(builds[b0:b1], planned.name)
        track.own_all(conversions[c0:c1], planned.name)
    builds.extend(conversions)

    # A `fetch` is an in-process download into the cache, verified before it
    # is kept; it needs nothing apt installs and touches nothing outside the
    # cache, so every one of them can go first. A git clone is a Command
    # that needs git from apt and stays where its backend put it.
    commands: list[Step] = [
        step for step in builds if isinstance(step, Action) and step.kind == "fetch"
    ]
    builds = [step for step in builds if not (isinstance(step, Action) and step.kind == "fetch")]

    # D-040: a repository's key is a fetch like any other and joins the
    # others at the front -- a key whose fingerprint is not the pinned one
    # refuses before anything is installed. The two files land next, then
    # apt-get update is forced, because a source apt has not read is a
    # source apt does not have.
    if plan.apt_repos and repos is None:
        raise BackendError(
            f"the plan adds the {', '.join(a.repo.name for a in plan.apt_repos)} "
            f"repositor{'y' if len(plan.apt_repos) == 1 else 'ies'} and no repository "
            f"backend was supplied. Skipping it would leave apt unable to install what "
            f"the plan promised."
        )
    if repos is not None:
        repo_steps: list[Step] = []
        for addition in plan.apt_repos:
            added = repos.steps(addition.repo, unit=addition.unit)
            track.own_all(added, addition.unit)
            repo_steps.extend(added)
        commands.extend(s for s in repo_steps if isinstance(s, Action) and s.kind == "fetch")
        commands.extend(s for s in repo_steps if not (isinstance(s, Action) and s.kind == "fetch"))

    # A vendor .deb is resolved by apt from its file, and the file does not
    # exist until its fetch has run -- so the plan's own simulate (D-038)
    # could not include it. The simulate below can: it runs after every
    # fetch and before the apt step, over the apt packages and the downloaded
    # .debs together, and a refusal there is a refusal on an untouched
    # machine. `apt-get install ./file.deb` is what the binary backend runs
    # later; asking the same resolver the same question first is the whole
    # idea.
    debs = [
        str(binary.fetcher.path_for(planned.block.install.artifact))
        for planned in plan.packages
        if binary is not None
        and isinstance(planned.block.install, BinaryInstall)
        and planned.block.install.format == "deb"
        and not planned.deb_installed  # ours and still installed: nothing to ask (#63)
    ]

    # The refresh is the default (D-044): six of fifteen profiles on a
    # four-day-old Parrot guest passed the plan and died at the first fetch
    # because the pool had moved past the lists. It runs only when apt will
    # be asked to resolve something -- the apt step, or a vendor .deb whose
    # dependencies apt resolves from the lists -- so a re-run with nothing
    # to install stays a no-op, and a source-only plan on a station with no
    # uplink never starts with a network command. A just-added repository
    # always needs it, whatever the flag says: apt has no index for it yet.
    needs_lists = bool(plan.apt_packages_all) or bool(debs)
    if (refresh and needs_lists) or plan.apt_repos:
        commands.append(apt.refresh_command())
    # The same question again for a just-added repository: the plan-time
    # simulate left its packages out because apt had no index for them.
    # After the update, the resolver is asked about the whole apt step.
    if debs or plan.apt_repos:
        if debs and plan.apt_repos:
            why = (
                f"Ask apt whether the {len(debs)} downloaded .deb file(s) and the packages "
                f"from the added repositor{'y' if len(plan.apt_repos) == 1 else 'ies'} "
                f"resolve together with the apt step, before anything is installed"
            )
        elif debs:
            why = (
                f"Ask apt whether the {len(debs)} downloaded .deb file(s) resolve "
                f"together with the apt step, before anything is installed"
            )
        else:
            supplied = sorted(plan.repo_supplied)
            why = (
                f"Ask apt whether {', '.join(supplied)} resolve{'s' if len(supplied) == 1 else ''} "
                f"from the added repositor{'y' if len(plan.apt_repos) == 1 else 'ies'} together "
                f"with the apt step, before anything is installed"
            )
        commands.append(
            apt.simulate_command(
                (*plan.apt_to_install, *debs),
                release=plan.apt_release,
                description=why,
                no_remove=True,
            )
        )

    if plan.debconf_selections and plan.apt_packages_all:
        # Before the apt install, never after: a package's postinst reads its
        # preseeded answers as it configures, so wireshark-common creates the
        # wireshark group and grants dumpcap its capabilities in one pass. Fed
        # on stdin — a plain argv with no file left on the machine.
        commands.append(
            Command(
                argv=("debconf-set-selections",),
                description="Preseed debconf answers so non-interactive installs pick the right defaults",
                requires_root=True,
                stdin="\n".join(plan.debconf_selections) + "\n",
            )
        )
    apt_phase_start = len(commands)
    commands.extend(apt.install_commands(plan.apt_to_install, release=plan.apt_release))
    # The second apt command, for the units whose manifests asked for
    # `--no-install-recommends` (D-052). After the default one, because it is
    # the deviation: the ordinary transaction settles first, and the operator
    # reading the plan sees the narrower command as the exception it is. Both
    # carry --no-remove and both were simulated the way they will run.
    commands.extend(
        apt.install_commands(
            plan.apt_to_install_no_recommends,
            release=plan.apt_release,
            recommends=False,
        )
    )

    if plan.reconfigure_after and plan.apt_packages_all:
        # After the whole apt transaction is settled, so a postinst action that
        # needs another just-installed package (wireshark-common's setcap needs
        # libcap2-bin) re-runs with everything present. Non-interactive: the
        # answer is already in the debconf DB from the preseed above.
        for pkg in plan.reconfigure_after:
            commands.append(
                Command(
                    argv=("dpkg-reconfigure", pkg),
                    description=f"Re-run {pkg}'s configuration now the whole transaction is present",
                    requires_root=True,
                    env={"DEBIAN_FRONTEND": "noninteractive"},
                )
            )

    # Every unit installed by apt owns the whole apt phase: it is done when
    # the last apt command has exited 0 (#272).
    apt_units = [p.name for p in plan.packages if isinstance(p.block.install, AptInstall)]
    for step in commands[apt_phase_start:]:
        for name in apt_units:
            track.own(step, name)

    commands.extend(builds)

    # Configuration is written after the software that reads it exists, so a
    # package's own postinst cannot overwrite what we put down, and before
    # group membership for the same reason the comment above gives.
    for package in dict.fromkeys(pkg for pkg, _config, _body in plan.config_files):
        own_files = tuple(c for c in plan.config_files if c[0] == package)
        written = config_steps(replace(plan, config_files=own_files), staging_root=config_staging)
        track.own_all(written, package)
        commands.extend(written)

    # User services after the software and its configuration exist, and only
    # when the caller supplied the operator's home — a caller that does not
    # (older tests, bare planning) plans exactly as before (D-073 §6c).
    if user_services_home is not None:
        if plan.user_services:
            track.excluded.update(svc.unit for svc in plan.user_services)
            commands.extend(
                user_service_steps(plan, home=user_services_home, machine=user_services_machine)
            )
        # A unit a manifest in this transaction can produce but is NOT writing
        # this run — the rig was switched to flrig or VOX, or cleared — is
        # disabled and removed if its file is still present (review I1). The
        # removal step no-ops on a name whose file is absent.
        planned_names = {svc.name for svc in plan.user_services}
        stale = sorted(
            {
                svc.name
                for pkg in plan.packages
                for svc in pkg.manifest.user_services
                if svc.name not in planned_names
            }
        )
        if stale:
            commands.extend(
                user_service_removal_steps(
                    stale, home=user_services_home, machine=user_services_machine
                )
            )

    # Launchers after the software and its configuration exist. Generated
    # only when the caller supplies the per-user directories -- a caller that
    # does not (older tests, bare planning) gets plans identical to before.
    if launcher_bin is not None and launcher_applications is not None:
        for planned in plan.packages:
            made = launcher_steps(
                planned.manifest,
                bin_dir=launcher_bin,
                applications_dir=launcher_applications,
                venv_dir=(
                    venv.venv_root / planned.name
                    if venv is not None and isinstance(planned.block.install, VenvInstall)
                    else None
                ),
                node_wrapper=(
                    node.wrapper_for(planned.manifest, planned.block.install)
                    if node is not None and isinstance(planned.block.install, NodeInstall)
                    else None
                ),
            )
            track.own_all(made, planned.name)
            commands.extend(made)

    cache: dict[str, frozenset[str]] = {}
    for membership in plan.group_memberships:
        if current_groups is not None:
            groups = current_groups
        else:
            groups = cache.setdefault(membership.user, user_groups(membership.user))
        if membership.group in groups:
            # Idempotent: every operation is safe to re-run (CLAUDE.md).
            continue
        member = Command(
            argv=("gpasswd", "--add", membership.user, membership.group),
            description=(
                f"Add {membership.user} to the {membership.group!r} group for {membership.package}"
            ),
            requires_root=True,
        )
        track.own(member, membership.package)
        commands.append(member)
    for capability in plan.file_capabilities:
        expression = " ".join(f"{name}=ep" for name in capability.capabilities)
        setcap = Command(
            argv=("setcap", expression, str(capability.path)),
            description=(f"Grant {expression} to {capability.path} for {capability.package}"),
            requires_root=True,
        )
        track.own(setcap, capability.package)
        commands.append(setcap)
    # Last of all, so every other region, the launchers and the group
    # changes have happened before a partial map install fails the run.
    for key, ledger in ledgers.items():
        checked = ledger.step()
        for unit in ledger_units.get(key, ()):
            track.own(checked, unit)
        commands.append(checked)
    return commands


@dataclass(frozen=True)
class EffectCheck:
    """One thing a command claimed to do, re-probed after it exited 0.

    D-031: a tool reporting success is not evidence it did anything. ``apt-get``
    can exit 0 having installed nothing a held or broken package silently
    denied; ``gpasswd`` exits 0 whether or not the membership took. So the
    effect is read back from the same source resolution used pre-flight —
    ``apt-cache policy`` for a package, the group database for a membership —
    and the answer, not the exit status, is what the log records.
    """

    kind: str
    """``"package"``, ``"group"``, ``"binary"``, ``"tree"`` or ``"launcher"``."""

    subject: str
    """The package name, or ``"user:group"``."""

    confirmed: bool
    detail: str
    """What was found, phrased for the log and the operator alike."""

    def to_log_entry(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "confirmed": self.confirmed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Verification:
    """The post-run effect check across every package and membership."""

    checks: tuple[EffectCheck, ...]

    @property
    def confirmed(self) -> tuple[EffectCheck, ...]:
        return tuple(c for c in self.checks if c.confirmed)

    @property
    def discrepancies(self) -> tuple[EffectCheck, ...]:
        """Commands that exited 0 without producing the effect they claimed."""
        return tuple(c for c in self.checks if not c.confirmed)

    @property
    def ok(self) -> bool:
        return not self.discrepancies


def verify_effects(
    plan: InstallPlan,
    prober: PackageProber | None,
    *,
    group_lookup: Callable[[str], frozenset[str]] = user_groups,
    capability_lookup: Callable[[Path], str] | None = None,
    prefix: Path | None = None,
    launcher_bin: Path | None = None,
) -> Verification:
    """Read back whether the transaction's stated effects actually hold.

    Called only on the success path — after every command exited 0 — because
    that is precisely where D-031 bites: nothing else in the run would notice a
    command that succeeded and did nothing. The end state is checked, not the
    delta, so a membership the run skipped as already-present is confirmed too;
    the question is "is the machine as the plan promised", not "did each command
    move it".

    The package half needs ``prober``; without one, only group memberships are
    checked. Packages are then simply absent from ``checks`` rather than
    reported as failures — an unasked question, not a failed one. Likewise the
    built half needs ``prefix``: every source, git and non-deb binary unit that
    declares ``binaries`` is checked for an executable at
    ``<prefix>/bin/<install_as>``. Added the night js8call was found "confirmed"
    on four targets with nothing installed: its ``cmake --install`` has no
    rule for the executable, exits 0, and writes an empty install manifest.

    Two more halves, each again an unasked question when its argument is
    absent (issue #27). With ``prefix``, every block that installs a *tree*
    is checked for its ``tree_marker`` under ``<prefix>/share/hammunition/
    <name>`` -- ``cp -aT`` exits 0 on any directory, and yaac, mshv,
    js8spotter and the two venv payloads declare no ``binaries``, so they
    ended ``verified: true`` with no check at all. With ``launcher_bin``,
    every launcher the manifest declares is checked for an executable wrapper
    at ``<launcher_bin>/<name>`` and, where it sets one, for its
    ``working_directory`` existing -- a wrapper that ``cd``s into a directory
    the run never made fails on every click.
    """
    checks: list[EffectCheck] = []

    if prefix is not None:
        for planned in plan.packages:
            marker = _tree_marker(planned.block)
            if marker is None:
                continue
            path = tree_destination(prefix, planned.name) / marker
            present = path.exists()
            checks.append(
                EffectCheck(
                    kind="tree",
                    subject=f"{planned.name}:{marker}",
                    confirmed=present,
                    detail=(
                        f"tree marker present at {path}"
                        if present
                        else (
                            f"the tree install exited 0 but {path} does not exist -- the "
                            f"archive is laid out differently from what the launcher "
                            f"expects, or the tree never landed"
                        )
                    ),
                )
            )

        for planned in plan.packages:
            if not _declares_installed_binaries(planned.block):
                continue
            for binary in effective_binaries(planned.manifest, planned.block):
                path = prefix / "bin" / binary.install_as
                present = path.is_file() and os.access(path, os.X_OK)
                checks.append(
                    EffectCheck(
                        kind="binary",
                        subject=f"{planned.name}:{binary.install_as}",
                        confirmed=present,
                        detail=(
                            f"executable at {path}"
                            if present
                            else (
                                f"the install step exited 0 but there is no executable at "
                                f"{path} -- the build has no install rule for it, or installs "
                                f"it under another name"
                            )
                        ),
                    )
                )

        for planned in plan.packages:
            for extra in _extra_files(planned.block):
                path = prefix / extra
                present = path.is_file() and not path.is_symlink()
                checks.append(
                    EffectCheck(
                        kind="file",
                        subject=f"{planned.name}:{extra}",
                        confirmed=present,
                        detail=(
                            f"regular file at {path}"
                            if present
                            else (
                                f"the install step exited 0 but {path} is not a regular "
                                f"file -- missing, or left a symlink (D-069)"
                            )
                        ),
                    )
                )

        for planned in plan.packages:
            if not _declares_installed_binaries(planned.block):
                continue
            for declared_file in planned.manifest.installed_files:
                path = prefix / declared_file
                present = path.exists()
                checks.append(
                    EffectCheck(
                        kind="file",
                        subject=f"{planned.name}:{declared_file}",
                        confirmed=present,
                        detail=(
                            f"installed file present at {path}"
                            if present
                            else (
                                f"the install step exited 0 but {path} does not exist -- the "
                                f"build's install rule put it elsewhere, or installed nothing"
                            )
                        ),
                    )
                )

    if prefix is not None:
        for planned in plan.packages:
            method = planned.block.install
            if not isinstance(method, BinaryInstall):
                continue
            for path in placed_files(planned.block, prefix):
                present = path.is_file()
                checks.append(
                    EffectCheck(
                        kind="file",
                        subject=f"{planned.name}:{path}",
                        confirmed=present,
                        detail=(
                            f"placed file present at {path}"
                            if present
                            else f"the install step exited 0 but {path} is not a file"
                        ),
                    )
                )
            if method.devctl_helper is not None:
                from hammunition.hardware import polkit

                wrapper = under_prefix(HELPER_PATH, prefix)
                answer = polkit.installed_helper_version(str(wrapper))
                checks.append(
                    EffectCheck(
                        kind="helper",
                        subject=f"{planned.name}:{wrapper}",
                        confirmed=answer is not None,
                        detail=(
                            f"the device helper answers {answer!r}"
                            if answer is not None
                            else f"{wrapper} --version does not answer a contract line: the helper "
                            f"the tray's contract requires is not working"
                        ),
                    )
                )

    # A vendor .deb's package is not in apt_to_install -- apt installed it
    # from a file, not from the archive -- and the binaries check above
    # skips deb formats because apt placed the contents. Until 2026-09-03
    # nobody then asked apt about it, and wsjtx-improved on Debian 13 ended
    # `verified: true` with `checks: []`. Its deb_package is the effect.
    deb_packages = tuple(
        sorted(
            {
                planned.block.install.deb_package
                for planned in plan.packages
                if isinstance(planned.block.install, BinaryInstall)
                and planned.block.install.format == "deb"
                and planned.block.install.deb_package is not None
            }
        )
    )
    wanted = tuple(sorted({*plan.apt_packages_all, *deb_packages}))
    if wanted and prober is not None:
        states = prober.probe(wanted)
        for name in wanted:
            state = states.get(name)
            installed = state is not None and state.is_installed
            checks.append(
                EffectCheck(
                    kind="package",
                    subject=name,
                    confirmed=installed,
                    detail=(
                        f"installed {state.installed}"
                        if installed and state is not None
                        else "apt-get exited 0 but apt reports the package not installed"
                    ),
                )
            )

    if launcher_bin is not None:
        for planned in plan.packages:
            for launcher in planned.manifest.launchers:
                wrapper = launcher_bin / launcher.name
                runnable = wrapper.is_file() and os.access(wrapper, os.X_OK)
                workdir = (
                    Path(launcher.working_directory)
                    if launcher.working_directory is not None
                    else None
                )
                workdir_ok = workdir is None or workdir.is_dir()
                if runnable and workdir_ok:
                    detail = f"executable wrapper at {wrapper}" + (
                        f", runs in {workdir}" if workdir is not None else ""
                    )
                elif not runnable:
                    detail = (
                        f"the launcher step exited 0 but there is no executable wrapper "
                        f"at {wrapper}"
                    )
                else:
                    detail = (
                        f"the wrapper at {wrapper} runs in {workdir}, which does not exist "
                        f"-- no install step created it, so the launcher fails on every click"
                    )
                checks.append(
                    EffectCheck(
                        kind="launcher",
                        subject=f"{planned.name}:{launcher.name}",
                        confirmed=runnable and workdir_ok,
                        detail=detail,
                    )
                )

    groups_now = {m.user: group_lookup(m.user) for m in plan.group_memberships}
    for membership in plan.group_memberships:
        present = membership.group in groups_now.get(membership.user, frozenset())
        checks.append(
            EffectCheck(
                kind="group",
                subject=f"{membership.user}:{membership.group}",
                confirmed=present,
                detail=(
                    "membership present in the group database"
                    if present
                    else "gpasswd exited 0 but the membership is absent from the group database"
                ),
            )
        )

    if capability_lookup is None:
        capability_lookup = _file_capability_text
    for capability in plan.file_capabilities:
        expected = {f"{name}=ep" for name in capability.capabilities}
        observed = set(capability_lookup(capability.path).split())
        # getcap writes the pathname before the capability expression.
        actual = set()
        for token in observed:
            if not token.startswith("CAP_") and not token.startswith("cap_"):
                continue
            if "=" in token:
                name, flags = token.split("=", 1)
                actual.add(f"{name.upper()}={flags.lower()}")
        present = expected.issubset(actual)
        checks.append(
            EffectCheck(
                kind="file_capability",
                subject=str(capability.path),
                confirmed=present,
                detail=(
                    f"getcap confirms {' '.join(sorted(expected))}"
                    if present
                    else f"getcap does not confirm {' '.join(sorted(expected))}"
                ),
            )
        )

    return Verification(checks=tuple(checks))


def _file_capability_text(path: Path) -> str:
    """Read a file's Linux capabilities without changing it."""
    try:
        result = subprocess.run(
            ["getcap", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return result.stdout if result.returncode == 0 else ""


@dataclass(frozen=True)
class ExecutionReport:
    """What actually happened. Partial success is reported explicitly (D-016)."""

    completed: tuple[Step, ...]
    failed: Step | None
    stderr: str
    verification: Verification | None = None
    """The post-run effect check, when one was performed (D-031). ``None`` when
    the run failed before completion, or when no prober was supplied."""

    @property
    def ok(self) -> bool:
        return self.failed is None

    @property
    def verified(self) -> bool:
        """Whether every claimed effect was confirmed. A run with no
        verification is *not* verified — the caller must not read a missing
        check as a passing one."""
        return self.verification is not None and self.verification.ok


#: The keys every ``action_end`` entry carries; a step's facts never replace one.
_ACTION_END_KEYS = frozenset({"event", "version", "timestamp", "kind", "detail", "outcome"})


def _facts(action: Action) -> dict[str, str]:
    """*action*'s facts for its ``action_end`` entry (D-070), minus any key
    the entry already has: a fact adds to the record, never rewrites it."""
    return {k: v for k, v in action.facts.items() if k not in _ACTION_END_KEYS}


def execute(
    commands: Sequence[Step],
    runner: CommandRunner,
    *,
    log: TransactionLog,
    plan: InstallPlan,
    echo: Callable[[str], None] | None = None,
    euid: int | None = None,
    prober: PackageProber | None = None,
    group_lookup: Callable[[str], frozenset[str]] = user_groups,
    prefix: Path | None = None,
    launcher_bin: Path | None = None,
    capability_lookup: Callable[[Path], str] | None = None,
    owners: StepOwners | None = None,
) -> ExecutionReport:
    """Run every command, stopping at the first failure.

    Each command is logged *before* it runs and its outcome logged after. That
    ordering matters: a run killed mid-``apt-get`` leaves a record that the
    command was started, which is exactly the state an operator needs to see
    and the state a log written only on success would hide.

    ``euid`` is whose the run is, so the echoed line matches the process
    table -- an unprivileged run says ``sudo apt-get ...``, not ``apt-get
    ...``. Defaults to the real euid.

    ``prober`` re-reads apt after the run to confirm the packages actually
    landed (D-031). When supplied, the effect check runs on the success path
    and its result is recorded in ``transaction_end`` — the record ``uninstall``
    will trust, which must not say "installed" on the strength of an exit code
    alone. Group memberships are always re-read from ``group_lookup``; the
    package half needs the prober, so omitting it verifies groups only, and
    the built half needs ``prefix`` -- the install prefix whose ``bin`` every
    declared binary must be found under and whose ``share/hammunition`` every
    installed tree's marker must be found under -- and the launcher half needs
    ``launcher_bin``, the per-user directory the wrappers were written to.

    ``owners`` (from :func:`commands_for`) says which unit each step belongs
    to. A failed transaction flushes units that finished before the failing
    step; a successful transaction records them only after effect verification.
    """
    write = echo if echo is not None else (lambda _line: None)
    shown_as = os.geteuid() if euid is None else euid

    run_started = datetime.now(UTC).isoformat()
    log.append(
        {
            "event": "transaction_begin",
            # Version 2 adds `deferred` (Q-017 / D-039): what the plan chose
            # NOT to do, so a `status` read later knows the run installed
            # eighteen of a profile's twenty-two on purpose, not by accident.
            "version": 2,
            "timestamp": run_started,
            "target": plan.target.to_log_entry(),
            "packages": [p.name for p in plan.packages],
            "apt_packages": list(plan.apt_packages_all),
            "deferred": [d.to_log_entry() for d in plan.deferrals],
        }
    )

    last_step: dict[str, int] = {}
    if owners is not None:
        for index, step in enumerate(commands, 1):
            for unit in owners.units_of(step):
                if unit not in owners.excluded:
                    last_step[unit] = index
    finished_at: dict[int, list[str]] = {}
    for unit, index in last_step.items():
        finished_at.setdefault(index, []).append(unit)

    finished: set[str] = set()

    def record_finished() -> None:
        if owners is None:
            return
        for unit in sorted(finished):
            entry: dict[str, object] = {
                "event": "unit_end",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "unit": unit,
                "ok": True,
                "run": run_started,
                "catalog_version": owners.versions.get(unit),
            }
            if unit in owners.pins:
                entry["pin"] = owners.pins[unit]
            if unit in owners.states:
                entry["state"] = owners.states[unit]
            log.append(entry)
        finished.clear()

    completed: list[Step] = []
    count = len(commands)
    for index, command in enumerate(commands, 1):
        if (active_run := runlog.current()) is not None:
            active_run.step_start(index, count, command.description)
        write(f"  step {index}/{count}: {command.description}")
        write(f"  $ {command.display(euid=shown_as)}")
        if isinstance(command, Command) and command.long_running:
            write(f"    {LONG_STEP_NOTE}")

        if isinstance(command, Action):
            # An in-process step: same logging shape, same failure contract. It
            # is logged before it runs for the same reason a command is -- a run
            # killed mid-extraction must leave a record that extraction started.
            log.append(
                {
                    "event": "action_begin",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "kind": command.kind,
                    "detail": command.detail,
                    "description": command.description,
                }
            )
            try:
                outcome = command.perform()
            except (BackendError, OSError) as exc:
                record_finished()
                log.append(
                    {
                        "event": "transaction_failed",
                        "version": 1,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "kind": command.kind,
                        "detail": command.detail,
                        "error": str(exc),
                        "completed": len(completed),
                    }
                )
                return ExecutionReport(completed=tuple(completed), failed=command, stderr=str(exc))
            log.append(
                {
                    "event": "action_end",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "kind": command.kind,
                    # Uninstall's file-attribution replay reads install-binary
                    # details back as destinations; an Action has no argv.
                    "detail": command.detail,
                    "outcome": outcome,
                    **_facts(command),
                }
            )
            if outcome:
                write(f"    {outcome}")
            completed.append(command)
            finished.update(finished_at.get(index, ()))
            continue

        log.append(
            {
                "event": "command_begin",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "argv": list(command.argv),
                "requires_root": command.requires_root,
                "description": command.description,
            }
        )
        try:
            result = runner.run(command)
        except BackendError as exc:
            # A missing binary (no sudo in a minimal container, no gpasswd) is
            # a failure of this transaction, not a crash of the engine: it gets
            # the same transaction_failed record and the same exit-code
            # contract as a command that ran and returned non-zero. Letting it
            # escape as a traceback left the log saying command_begin with no
            # ending, which is the log lying by omission.
            record_finished()
            log.append(
                {
                    "event": "transaction_failed",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "argv": list(command.argv),
                    "error": str(exc),
                    "completed": len(completed),
                }
            )
            return ExecutionReport(completed=tuple(completed), failed=command, stderr=str(exc))
        log.append(
            {
                "event": "command_end",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "argv": list(command.argv),
                "returncode": result.returncode,
            }
        )
        if not result.ok:
            record_finished()
            log.append(
                {
                    "event": "transaction_failed",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "argv": list(command.argv),
                    "returncode": result.returncode,
                    "completed": len(completed),
                }
            )
            return ExecutionReport(completed=tuple(completed), failed=command, stderr=result.stderr)
        completed.append(command)
        finished.update(finished_at.get(index, ()))

    # Every command exited 0. D-031: that is not yet evidence the machine
    # changed. Re-read the effects from the same sources resolution used, and
    # let the confirmed state -- not the exit code -- be what the log records.
    verification: Verification | None = None
    if (
        prober is not None
        or plan.group_memberships
        or plan.file_capabilities
        or prefix is not None
        or owners is not None
    ):
        try:
            verification = verify_effects(
                plan,
                prober,
                group_lookup=group_lookup,
                capability_lookup=capability_lookup,
                prefix=prefix,
                launcher_bin=launcher_bin,
            )
        except BackendError as exc:
            # The re-probe itself failed -- apt worked for the install a moment
            # ago and does not now. That is not "the package is missing"; it is
            # "we could not confirm", and the honest record is unverified, not a
            # claim either way. Fail toward flagging it.
            verification = Verification(
                checks=(
                    EffectCheck(
                        kind="verification",
                        subject="apt-cache policy",
                        confirmed=False,
                        detail=f"the post-run effect check could not run: {exc}",
                    ),
                )
            )

    end_entry: dict[str, object] = {
        "event": "transaction_end",
        "version": 2,
        "timestamp": datetime.now(UTC).isoformat(),
        "completed": len(completed),
    }
    if verification is not None:
        end_entry["verified"] = verification.ok
        end_entry["checks"] = [c.to_log_entry() for c in verification.checks]
    if verification is None or verification.ok:
        record_finished()
    log.append(end_entry)
    return ExecutionReport(
        completed=tuple(completed), failed=None, stderr="", verification=verification
    )


#: What proves a file in the operator's home is the engine's own work:
#: the launcher wrapper's generated-by line, a desktop entry's package key,
#: or a venv wrapper's exec into the engine's own venv tree. A same-named
#: file carrying none of these predates us (or is the operator's), and the
#: removal step reports it and leaves it.
OURS_MARKERS = ("generated by hammunition", "X-Hammunition-Package=", "/hammunition/venvs/")


def _remove_if_ours(path: Path) -> str:
    """Unlink a wrapper or desktop entry only after reading our marker back.

    A symbolic link is never ours (#288): the engine writes none, and reading
    through one would let a link to a file that carries our marker (or to
    another tool's script) stand in for a wrapper we wrote.
    """
    if path.is_symlink():
        return f"left in place: {path} is a symbolic link — not ours"
    try:
        content = path.read_text()
    except FileNotFoundError:
        return f"already absent: {path}"
    except (OSError, UnicodeDecodeError):
        return f"left in place: {path} is not readable as a generated file — not ours"
    if not any(marker in content for marker in OURS_MARKERS):
        return f"left in place: {path} carries no hammunition marker — not ours"
    path.unlink()
    return f"removed {path}"


def _remove_venv(path: Path) -> str:
    """Remove a per-user venv tree or its staged requirements file.

    Both are namespaced (``venvs/<unit>`` and ``venvs/<unit>.requirements.txt``).
    """
    if not path.exists():
        return f"already absent: {path}"
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return f"removed {path}"


def artifact_removal_steps(plan: RemovalPlan) -> list[Action | Command]:
    """The non-apt half of an uninstall, from the plan's artifact list.

    Trees and prefix binaries are root ``rm`` commands — the same argv shapes
    the attribution replay un-attributes, so removal and attribution stay one
    vocabulary. Venvs, wrappers and desktop entries are in-process actions:
    the venv path is namespaced, and the marker-based ones read the file back
    before unlinking, which a ``rm`` argv cannot express.
    """
    steps: list[Action | Command] = []
    for unit, removals in plan.artifacts.items():
        for removal in removals:
            if removal.kind == "capability":
                steps.append(
                    Command(
                        argv=("setcap", "-r", str(removal.path)),
                        description=f"Clear {unit}'s logged file capabilities",
                        requires_root=True,
                    )
                )
            elif removal.kind == "tree":
                steps.append(
                    Command(
                        argv=("rm", "-rf", "--", str(removal.path)),
                        description=f"Remove {unit}'s installed tree ({removal.basis})",
                        requires_root=removal.requires_root,
                    )
                )
            elif removal.kind == "binary":
                steps.append(
                    Command(
                        argv=("rm", "-f", "--", str(removal.path)),
                        description=f"Remove {unit}'s installed binary ({removal.basis})",
                        requires_root=removal.requires_root,
                    )
                )
            elif removal.kind == "apt-repo":
                # D-040: the source file and the keyring the install wrote,
                # attributed from its own `install -D -m 0644` commands. The
                # caller runs apt-get update afterwards, so apt forgets the
                # repository in the same transaction that removes its files.
                steps.append(
                    Command(
                        argv=("rm", "-f", "--", str(removal.path)),
                        description=f"Remove the apt repository file {unit} added ({removal.basis})",
                        requires_root=removal.requires_root,
                    )
                )
            elif removal.kind == "venv":
                steps.append(
                    Action(
                        kind="remove-venv",
                        description=f"Remove {unit}'s virtualenv",
                        detail=str(removal.path),
                        perform=partial(_remove_venv, removal.path),
                    )
                )
            else:  # wrapper / desktop-entry: marker-verified
                steps.append(
                    Action(
                        kind=f"remove-{removal.kind}",
                        description=f"Remove {unit}'s {removal.kind} (only if ours)",
                        detail=str(removal.path),
                        perform=partial(_remove_if_ours, removal.path),
                    )
                )
    return steps


def run_removal(
    commands: Sequence[Command | Action],
    runner: CommandRunner,
    *,
    log: TransactionLog,
    plan: RemovalPlan,
    target: Target,
    echo: Callable[[str], None] | None = None,
    euid: int | None = None,
    prober: PackageProber | None = None,
) -> ExecutionReport:
    """Run a removal, with the same logging contract as :func:`execute`.

    Same before/after event ordering, same first-failure stop, and the same
    D-031 ending: the effect check re-reads apt and confirms the packages are
    *absent* — ``apt-get remove`` exiting 0 for a package a held dependency
    kept installed would otherwise be recorded as removed.

    Its terminal events are ``uninstall_begin`` / ``uninstall_end`` /
    ``uninstall_failed`` so a log reader can tell a removal from an install
    without parsing argv; the per-command events are the shared
    ``command_begin`` / ``command_end``, which is what lets attribution replay
    both directions from one event type.
    """
    write = echo if echo is not None else (lambda _line: None)
    shown_as = os.geteuid() if euid is None else euid

    log.append(
        {
            "event": "uninstall_begin",
            "version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "target": target.to_log_entry(),
            "packages": list(plan.to_remove),
            "apt_packages": list(plan.apt_packages),
        }
    )

    completed: list[Step] = []
    declined: set[str] = set()
    count = len(commands)
    for index, command in enumerate(commands, 1):
        if (active_run := runlog.current()) is not None:
            active_run.step_start(index, count, command.description)
        write(f"  step {index}/{count}: {command.description}")
        write(f"  $ {command.display(euid=shown_as)}")
        if isinstance(command, Command) and command.long_running:
            write(f"    {LONG_STEP_NOTE}")

        if isinstance(command, Action):
            # Marker-verified unlinks and venv removals run in-process, with
            # the same before/after logging an install-side Action gets.
            log.append(
                {
                    "event": "action_begin",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "kind": command.kind,
                    "detail": command.detail,
                    "description": command.description,
                }
            )
            try:
                outcome = command.perform()
            except (BackendError, OSError) as exc:
                log.append(
                    {
                        "event": "uninstall_failed",
                        "version": 1,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "kind": command.kind,
                        "detail": command.detail,
                        "error": str(exc),
                        "completed": len(completed),
                    }
                )
                return ExecutionReport(completed=tuple(completed), failed=command, stderr=str(exc))
            log.append(
                {
                    "event": "action_end",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "kind": command.kind,
                    "detail": command.detail,
                    "outcome": outcome,
                    **_facts(command),
                }
            )
            if outcome:
                write(f"    {outcome}")
            if outcome.startswith("left in place"):
                # An honest refusal (no marker) is not a removal; the effect
                # check must not demand the path be gone.
                declined.add(command.detail)
            completed.append(command)
            continue

        log.append(
            {
                "event": "command_begin",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "argv": list(command.argv),
                "requires_root": command.requires_root,
                "description": command.description,
            }
        )
        try:
            result = runner.run(command)
        except BackendError as exc:
            log.append(
                {
                    "event": "uninstall_failed",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "argv": list(command.argv),
                    "error": str(exc),
                    "completed": len(completed),
                }
            )
            return ExecutionReport(completed=tuple(completed), failed=command, stderr=str(exc))
        log.append(
            {
                "event": "command_end",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "argv": list(command.argv),
                "returncode": result.returncode,
            }
        )
        if not result.ok:
            log.append(
                {
                    "event": "uninstall_failed",
                    "version": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "argv": list(command.argv),
                    "returncode": result.returncode,
                    "completed": len(completed),
                }
            )
            return ExecutionReport(completed=tuple(completed), failed=command, stderr=result.stderr)
        completed.append(command)

    verification: Verification | None = None
    artifact_checks: list[EffectCheck] = []
    for unit, removals in plan.artifacts.items():
        for removal in removals:
            if removal.kind == "capability":
                continue
            if str(removal.path) in declined:
                continue
            still_there = removal.path.exists() or removal.path.is_symlink()
            artifact_checks.append(
                EffectCheck(
                    kind=f"{removal.kind}_removed",
                    subject=f"{unit}: {removal.path}",
                    confirmed=not still_there,
                    detail="still present" if still_there else "no longer present",
                )
            )
    if artifact_checks and (prober is None or not plan.apt_packages):
        verification = Verification(checks=tuple(artifact_checks))
    if prober is not None and plan.apt_packages:
        try:
            states = prober.probe(plan.apt_packages)
            checks = list(artifact_checks)
            for package in plan.apt_packages:
                state = states.get(package)
                still_there = state is not None and state.is_installed
                checks.append(
                    EffectCheck(
                        kind="package_removed",
                        subject=package,
                        confirmed=not still_there,
                        detail=(
                            f"still installed at {state.installed}"
                            if still_there and state is not None
                            else "no longer installed"
                        ),
                    )
                )
            verification = Verification(checks=tuple(checks))
        except BackendError as exc:
            verification = Verification(
                checks=(
                    EffectCheck(
                        kind="verification",
                        subject="apt-cache policy",
                        confirmed=False,
                        detail=f"the post-removal effect check could not run: {exc}",
                    ),
                )
            )

    end_entry: dict[str, object] = {
        "event": "uninstall_end",
        "version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "completed": len(completed),
    }
    if verification is not None:
        end_entry["verified"] = verification.ok
        end_entry["checks"] = [c.to_log_entry() for c in verification.checks]
    log.append(end_entry)
    return ExecutionReport(
        completed=tuple(completed), failed=None, stderr="", verification=verification
    )
