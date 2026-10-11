# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the engine keeps things, and whose they are.

Two directories matter: the transaction log under ``$XDG_STATE_HOME`` and the
verified-artifact cache under ``$XDG_CACHE_HOME``. Both face the same problem
and it is subtle enough that two copies of the answer would drift, which is why
it is written once here.

**The problem is sudo.** ``hammunition install`` needs root for ``apt-get`` and
for ``make install``, so it is usually run under sudo — and sudo resets ``HOME``
to ``/root``. Following ``$HOME`` would put the operator's transaction history
and their downloaded artifacts somewhere they cannot read, while the same
command run as themselves reports an empty log and an empty cache. The engine
already works out who the operator is, because ``gpasswd`` needs a name; this
uses that name.

``$XDG_*`` is deliberately **not** consulted in the root-with-an-owner case: it
either does not survive ``env_reset`` or it belongs to root, and neither is the
operator's.
"""

from __future__ import annotations

import contextlib
import errno
import os
import pwd
import stat
from pathlib import Path

__all__ = [
    "APP",
    "OperatorDirError",
    "artifact_cache_dir",
    "build_root",
    "ensure_operator_dir",
    "open_operator_dir",
    "operator_dir_problem",
    "operator_for",
    "owner_aware_dir",
    "state_dir",
    "user_config_base",
]

APP = "hammunition"


def owner_aware_dir(
    *,
    xdg_var: str,
    home_relative: tuple[str, ...],
    owner: str | None = None,
) -> Path:
    """The per-user ``hammunition`` directory under one XDG base.

    ``owner`` is the operator the run is *on behalf of*. It changes the answer
    only when this process is root and that operator is somebody else, which is
    exactly the ``sudo hammunition ...`` case.

    ``home_relative`` is the XDG default path from a home directory, e.g.
    ``(".local", "state")`` or ``(".cache",)``.
    """
    if owner and os.geteuid() == 0:
        entry = None
        with contextlib.suppress(KeyError):
            entry = pwd.getpwnam(owner)
        # A root-named owner is not somebody else, so it takes the ordinary
        # path rather than being treated as a handoff.
        if entry is not None and entry.pw_uid != 0:
            return Path(entry.pw_dir).joinpath(*home_relative) / APP
    base = os.environ.get(xdg_var) or str(Path.home().joinpath(*home_relative))
    return Path(base) / APP


def user_config_base(owner: str | None = None) -> Path:
    """``$XDG_CONFIG_HOME`` (or ``~/.config``) itself — NOT the hammunition
    subdirectory under it.

    This is where systemd looks for user units (``<base>/systemd/user/``), so a
    rig service written under ``owner_aware_dir``'s ``.../hammunition/`` would
    never be found (D-073 §6c). Owner-aware the same way: under ``sudo`` for
    another operator it resolves their home, never root's.
    """
    if owner and os.geteuid() == 0:
        entry = None
        with contextlib.suppress(KeyError):
            entry = pwd.getpwnam(owner)
        if entry is not None and entry.pw_uid != 0:
            return Path(entry.pw_dir) / ".config"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base)


def state_dir(owner: str | None = None) -> Path:
    """``$XDG_STATE_HOME/hammunition`` — the transaction log lives here."""
    return owner_aware_dir(xdg_var="XDG_STATE_HOME", home_relative=(".local", "state"), owner=owner)


def artifact_cache_dir(owner: str | None = None) -> Path:
    """``$XDG_CACHE_HOME/hammunition/artifacts`` — verified downloads live here.

    A *cache* rather than state: every file in it is content-addressed by its
    verified digest and can be deleted at any time, costing only a re-download.
    Nothing here is a record of what was done — that is the transaction log's
    job, and conflating the two would put something un-deletable in a directory
    users and cleaners treat as disposable.
    """
    return owner_aware_dir(xdg_var="XDG_CACHE_HOME", home_relative=(".cache",), owner=owner) / (
        "artifacts"
    )


def build_root(owner: str | None = None) -> Path:
    """``$XDG_CACHE_HOME/hammunition/build`` — where source trees are unpacked
    and compiled.

    A cache for the same reason the artifact store is: it is reproducible from
    the catalog plus a verified download, and deleting it costs only time. It is
    deliberately *not* under the artifact cache — that directory is
    content-addressed and every name in it is a digest, which is a property
    worth keeping true.
    """
    return owner_aware_dir(xdg_var="XDG_CACHE_HOME", home_relative=(".cache",), owner=owner) / (
        "build"
    )


def data_dir(owner: str | None = None) -> Path:
    """``$XDG_DATA_HOME/hammunition`` — installed per-user payloads live here.

    *Data*, not cache: a virtualenv the operator's launchers point into is not
    reproducible-for-free the way a build tree is — deleting it breaks
    installed software until a reinstall. That distinction is why venvs do not
    live under ``build_root``.
    """
    return owner_aware_dir(xdg_var="XDG_DATA_HOME", home_relative=(".local", "share"), owner=owner)


def venv_root(owner: str | None = None) -> Path:
    """``$XDG_DATA_HOME/hammunition/venvs`` — one virtualenv per manifest."""
    return data_dir(owner) / "venvs"


def node_root(owner: str | None = None) -> Path:
    """``$XDG_DATA_HOME/hammunition/node`` — one built Node.js tree per manifest.

    Per-user for the same reason venvs are, plus one of its own: a node
    application writes into its own directory (openhamclock creates ``.env``
    beside ``server.js`` on first start), which a tree under ``/opt`` would
    have to be world-writable to allow (D-037).
    """
    return data_dir(owner) / "node"


def user_bin_dir(owner: str | None = None) -> Path:
    """``~/.local/bin`` for the operator — where venv wrappers land.

    Debian-family shells put it on PATH when it exists. Deliberately not
    ``/usr/local/bin``: a per-user venv reached through a system-wide wrapper
    would break for every user but one, and writing it needs no root, which is
    the privilege rule (CLAUDE.md) doing its job.
    """
    if owner and os.geteuid() == 0:
        entry = None
        with contextlib.suppress(KeyError):
            entry = pwd.getpwnam(owner)
        if entry is not None and entry.pw_uid != 0:
            return Path(entry.pw_dir) / ".local" / "bin"
    return Path.home() / ".local" / "bin"


def applications_dir(owner: str | None = None) -> Path:
    """``~/.local/share/applications`` for the operator — desktop entries.

    Deliberately not hammunition-scoped: this is the freedesktop per-user
    applications directory, the one every DE already reads. Our files are
    recognisable anyway — ``hammunition-*.desktop``, each carrying
    ``X-Hammunition-Package``.
    """
    if owner and os.geteuid() == 0:
        entry = None
        with contextlib.suppress(KeyError):
            entry = pwd.getpwnam(owner)
        if entry is not None and entry.pw_uid != 0:
            return Path(entry.pw_dir) / ".local" / "share" / "applications"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


# ---------------------------------------------------------------------------
# Creating the operator's directories as root
# ---------------------------------------------------------------------------


class OperatorDirError(Exception):
    """A directory under the operator's home that root must not use or create through."""


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _operator_home(path: Path, owner: str | None) -> pwd.struct_passwd | None:
    """The operator whose home *path* lies under, when this process is root.

    Named by *owner* when the caller knows it; otherwise the account whose home
    contains *path* (the deepest, should homes nest). None when not root, or
    when *path* is under no other account's home -- root's own, or a system
    directory -- which is a plain mkdir.
    """
    if os.geteuid() != 0:
        return None
    if owner:
        with contextlib.suppress(KeyError):
            entry = pwd.getpwnam(owner)
            if entry.pw_uid != 0 and _under(path, Path(entry.pw_dir)):
                return entry
        return None
    best: pwd.struct_passwd | None = None
    for entry in pwd.getpwall():
        if entry.pw_uid == 0 or entry.pw_dir in ("", "/"):
            continue
        if _under(path, Path(entry.pw_dir)) and (
            best is None or len(entry.pw_dir) > len(best.pw_dir)
        ):
            best = entry
    return best


def operator_for(path: Path, owner: str | None = None) -> pwd.struct_passwd | None:
    """The account whose home *path* is under, when this process is root and
    it is somebody else's: the case where root must write there on their
    behalf, through :func:`open_operator_dir`, and hand what it writes over.
    None otherwise -- not root, or not under another account's home."""
    return _operator_home(path, owner)


def _under(path: Path, home: Path) -> bool:
    return path != home and home in path.parents


def _refusal(where: Path, st: os.stat_result, entry: pwd.struct_passwd) -> str | None:
    """Read from ``fstat`` on a descriptor opened ``O_NOFOLLOW``, so never a symlink."""
    if not stat.S_ISDIR(st.st_mode):  # pragma: no cover - O_DIRECTORY refuses first
        return f"{where} is not a directory; move it aside and run the install again."
    if st.st_uid != entry.pw_uid:
        return (
            f"{where} is not owned by {entry.pw_name} (an earlier run under sudo can leave "
            f"it root-owned), so {entry.pw_name}'s own processes cannot write under it. "
            f"Fix: sudo chown -R {entry.pw_name}: {where}"
        )
    return None


def _open_failure(where: Path, exc: OSError, entry: pwd.struct_passwd, *, is_link: bool) -> str:
    """Name why ``open(O_DIRECTORY|O_NOFOLLOW)`` on an existing component failed.

    Linux answers a symlink with ELOOP, or with ENOTDIR when ``O_DIRECTORY``
    is checked first; *is_link* (an lstat, read only for the wording) tells
    the two ENOTDIR cases apart. The refusal itself never depends on it."""
    if exc.errno == errno.ELOOP or is_link:
        return (
            f"{where} is a symlink; root will not create, remove or write through it on "
            f"{entry.pw_name}'s behalf. Remove it and run the install again."
        )
    if exc.errno == errno.ENOTDIR:
        return f"{where} is not a directory; move it aside and run the install again."
    return f"cannot open {where}: {exc.strerror or exc}"


def _race(where: Path, exc: OSError) -> str:
    return (
        f"{where} changed while it was being created ({exc.strerror or exc}); something "
        f"else is writing there. Check it and run the install again."
    )


def _walk(path: Path, entry: pwd.struct_passwd, *, create: bool) -> tuple[int | None, str | None]:
    """Walk *path* from the operator's home by descriptor, never following a link.

    Each existing component is opened ``O_DIRECTORY|O_NOFOLLOW`` and checked
    with ``fstat`` on that descriptor: it must be a directory the operator
    owns. A missing one is created when *create* (0755, handed to the operator
    with fchown on the descriptor root just opened on it); otherwise the walk
    ends there, fine. Returns ``(descriptor of path, None)`` -- the caller
    closes it -- or ``(None, refusal)``; a race while creating (EEXIST, a link
    swapped in) or a failed hand-over is a refusal, never a raw OSError.
    """
    home = Path(entry.pw_dir)
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        where = home
        for part in path.relative_to(home).parts:
            where = where / part
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    return None, None
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                    child = os.open(part, _DIR_FLAGS, dir_fd=fd)
                except OSError as exc:
                    return None, _race(where, exc)
                try:
                    os.fchown(child, entry.pw_uid, entry.pw_gid)
                except OSError as exc:
                    os.close(child)
                    return None, (
                        f"could not hand {where} to {entry.pw_name}: "
                        f"{exc.strerror or exc}. Fix: sudo chown -R {entry.pw_name}: {where}"
                    )
            except OSError as exc:
                try:
                    is_link = stat.S_ISLNK(os.stat(part, dir_fd=fd, follow_symlinks=False).st_mode)
                except OSError:
                    is_link = False
                return None, _open_failure(where, exc, entry, is_link=is_link)
            else:
                refusal = _refusal(where, os.fstat(child), entry)
                if refusal is not None:
                    os.close(child)
                    return None, refusal
            os.close(fd)
            fd = child
        kept, fd = fd, -1
        return kept, None
    finally:
        if fd >= 0:
            os.close(fd)


def open_operator_dir(path: Path, owner: str | None = None) -> int | None:
    """:func:`ensure_operator_dir`, returning a descriptor on *path* when it walked.

    The descriptor is the directory the walk proved the operator's, opened
    ``O_NOFOLLOW`` all the way down: removing or creating an entry through it
    (``rmtree(name, dir_fd=...)``) cannot be redirected by a symlink planted in
    the path afterwards. None when the plain-mkdir case applies (not root, or
    not under another account's home). The caller closes it.
    """
    entry = _operator_home(path, owner)
    if entry is None:
        path.mkdir(parents=True, exist_ok=True)
        return None
    fd, refusal = _walk(path, entry, create=True)
    if refusal is not None:
        raise OperatorDirError(refusal)
    return fd


def open_existing_operator_dir(path: Path, owner: str | None = None) -> int | None:
    """A descriptor on *path* when it exists, walked the way :func:`open_operator_dir` walks.

    For reading or removing what is already there on the operator's behalf
    (uninstall): every component under their home is opened ``O_NOFOLLOW`` and
    checked, nothing is created, and None means *path* (or an ancestor) does
    not exist. Raises :class:`OperatorDirError` for a link or a directory that
    is not the operator's. Not root, or not under another account's home: the
    plain case, the path is opened as given and a symlink is followed -- the
    operator's own layout. The caller closes the descriptor.
    """
    entry = _operator_home(path, owner)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if entry is None:
        try:
            return os.open(path, flags)
        except (FileNotFoundError, NotADirectoryError):
            return None
    fd, refusal = _walk(path, entry, create=False)
    if refusal is not None:
        raise OperatorDirError(refusal)
    return fd


def ensure_operator_dir(path: Path, owner: str | None = None) -> None:
    """``mkdir -p`` that leaves the operator's directories the operator's.

    As root on an operator's behalf, a directory under their home is created a
    component at a time through ``O_NOFOLLOW`` descriptors and each one root
    made is handed over with ``fchown``: a root-owned ``~/.cache/hammunition``
    left by a fetch under sudo made the operator's own later ``install -d``
    fail with EACCES. An existing component that is a symlink, not a
    directory, or not the operator's raises :class:`OperatorDirError` naming it
    and the fix. Anywhere else, and when not root, it is a plain mkdir.
    """
    fd = open_operator_dir(path, owner)
    if fd is not None:
        os.close(fd)


def operator_dir_problem(path: Path, owner: str | None = None) -> str | None:
    """Why the operator could not create or use *path*, read-only; None if nothing.

    The same checks as :func:`ensure_operator_dir` on the components that
    exist, creating nothing -- for a path the operator's own process will
    create, where root only looks first so a failure is named rather than a
    bare EACCES.
    """
    entry = _operator_home(path, owner)
    if entry is None:
        return None
    fd, refusal = _walk(path, entry, create=False)
    if fd is not None:
        os.close(fd)
    return refusal
