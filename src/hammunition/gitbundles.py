# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Verifying a git checkout from a mirrored bundle, offline.  D-024, D-070, #381.

The git backend's problem has always been *is this the right revision*
(:mod:`hammunition.backends.git`): a clone can succeed completely while handing
over a different commit than the catalog was written against. That check
already existed for a network clone. This module is the same check applied to
a *bundle* the Bunker mirrors instead of the publisher — ``git bundle`` is a
single file holding the objects a repository needs, and ``git clone`` from one
is identical to cloning from a remote except that nothing is asked of a
network host.

**Why a bundle needs its own module and not just a new step on the existing
backend.** Three things make this security-sensitive in a way plain file
verification is not:

* **A tag can move.** The sha256 the Bunker vouches for is the *bundle's
  bytes*, not the commit inside it — a corrupt or stale bundle is caught by
  that digest, but a bundle that is perfectly intact and simply contains a
  *re-cut* tag is not. So the tag's target is read from the bundle and
  compared against the repository's own pinned commit before anything is
  checked out from it twice (once as ``HEAD``, once as the recreated tag);
  :func:`checkout_bundle` refuses a moved tag before recreating it, never
  after.
* **A submodule is another repository, recursively.** Each ``160000`` gitlink
  in the tree names a commit the *superproject* chose, not one the operator or
  the catalog chose directly, and nothing stops a tree from naming a gitlink
  path like ``../../etc`` or one that collides with ``.git``. Every path is
  checked with the same :func:`~hammunition.catalogue.safe_relative` guard the
  Bunker's own catalogue rows use, and every ancestor component of a gitlink's
  destination — not only the destination itself — is checked for being a
  symlink before anything is cloned into it, because a working tree that
  materializes a tracked symlink at one path and a gitlink nested under what
  looks like the same name on a case-insensitive or Unicode-normalizing
  filesystem is exactly the class of bug CVE-2017-1000117 and its successors
  were about.
* **A malicious ``.gitmodules`` can recurse forever.** A submodule's own
  ``.gitmodules`` is read after *its* checkout, which means the depth of
  recursion is chosen by whatever the bundle's author put there, not by this
  engine. :func:`checkout_gitlinks` refuses past a fixed depth and remembers
  every ``(path, commit)`` it has already walked, so a crafted layout cannot
  exhaust the process even if real repositories never nest anywhere near that
  deep.

**Never the publisher.** :func:`fetch_bundle` builds its own, offline-only
:class:`~hammunition.fetch.Fetcher` that asks the enrolled Bunker's mirror and
nothing else — the ``RemoteArtifact`` it constructs carries a dummy
``bundle.example.invalid`` URL precisely so that URL is never actually dialed;
offline, :meth:`~hammunition.fetch.Fetcher.sources_for` refuses to try it
anyway. The sha256 the Bunker's signed catalogue carries verifies the bundle's
*bytes* arrived intact; it says nothing about which commit is inside, which is
why :func:`clone_checked` and the tag check above exist as a second,
independent check over what the bundle produces after it is trusted as bytes.

Runtime imports are deferred to leaf modules throughout (``BackendError``,
``Command``, ``Fetcher``, ``RemoteArtifact``, ``ResolutionContext``), because
``hammunition.backends`` imports :mod:`hammunition.backends.git` eagerly and
``hammunition.fetch`` imports ``hammunition.backends.base`` — the same cycle
:mod:`hammunition.payloads` was written leaf-first to avoid. Module-level
imports here are stdlib, :mod:`hammunition.catalogue` and
:mod:`hammunition.manifest.schema` only, so this module itself can always be
imported standalone.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition.catalogue import safe_relative
from hammunition.manifest.schema import COMMIT_SHA

if TYPE_CHECKING:
    from hammunition.backends.base import CommandRunner
    from hammunition.fetch import Fetcher
    from hammunition.manifest.schema import GitInstall
    from hammunition.resolution import ResolutionContext

__all__ = [
    "GIT_ENV",
    "MAX_GITLINK_DEPTH",
    "bundle_name",
    "checked_git",
    "checkout_bundle",
    "checkout_gitlinks",
    "clone_checked",
    "fetch_bundle",
]

# Every git the engine runs is unattended. An operator's own git config can turn
# a plain `git tag` into an annotated, signed one (`tag.gpgsign true`), which
# opens an editor; a credential prompt on a fetch would wait the same way. A
# navigation install sat in nano on the bench for that (2026-10-03). Moved
# here from `hammunition.backends.git`, which now imports it from this leaf
# module instead of defining it twice.
GIT_ENV: dict[str, str] = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}

#: How many levels of nested gitlink a single unit's checkout will walk before
#: refusing outright. Real repositories nest nowhere near this deep; the limit
#: exists only so a crafted, cyclical-looking ``.gitmodules`` layout cannot
#: recurse until the process runs out of stack or file descriptors.
MAX_GITLINK_DEPTH = 64


def bundle_name(
    unit: str, commit: str, *, path: str | None = None, subcommit: str | None = None
) -> str:
    """The Bunker's binding name for this unit's bundle, or one of its
    submodules' recursively (D-070's mirror-path shape, naming this engine's
    own bundle writer, never guessed at by a reader)."""
    from hammunition.backends.base import BackendError

    if COMMIT_SHA.fullmatch(commit) is None or "/" in unit:
        raise BackendError("git bundle needs a unit and full pinned commit")
    name = f"{unit}@{commit}"
    if path is not None:
        safe_relative(path, "submodule path")
        if subcommit is None or COMMIT_SHA.fullmatch(subcommit) is None:
            raise BackendError("submodule bundle needs its full gitlink commit")
        name += f"/{path}@{subcommit}"
    elif subcommit is not None:
        raise BackendError("submodule commit has no path")
    return name


def checked_git(runner: CommandRunner, cwd: Path, *argv: str) -> str:
    """Run one ``git`` command through *runner*, refusing a non-zero exit.

    Every step below goes through this rather than :mod:`subprocess` directly,
    so a dry run and a test both see the same :class:`~hammunition.backends.base.Command`
    objects the rest of the engine does, and so git's exit status — never
    trusted bare (D-031) — is checked in exactly one place.
    """
    from hammunition.backends.base import BackendError, Command

    result = runner.run(
        Command(
            argv=("git", "-C", str(cwd), *argv),
            env=GIT_ENV,
            description="Check the pinned Bunker git bundle",
        )
    )
    if not result.ok:
        raise BackendError(f"git bundle check failed: {result.stderr.strip()}")
    return result.stdout.strip()


def fetch_bundle(
    unit: str,
    parent_commit: str,
    *,
    path: str | None,
    subcommit: str | None,
    fetcher: Fetcher,
    context: ResolutionContext,
) -> Path:
    """The verified local bytes of one bundle, mirror-only, never the publisher.

    *parent_commit* is always the top-level unit's own pinned commit, even for
    a deeply nested submodule — :func:`bundle_name`'s binding name folds the
    whole recursive path into one string rather than nesting a bundle name
    inside a bundle name, so every fetch at every depth names the same unit
    and the same root commit.
    """
    from hammunition.backends.base import BackendError
    from hammunition.fetch import Fetcher, MirrorPath
    from hammunition.manifest.schema import RemoteArtifact

    name = bundle_name(unit, parent_commit, path=path, subcommit=subcommit)
    row = context.entry("git-bundles", name)
    if row.sha256 is None or row.size is None:
        raise BackendError(f"git bundle {name}: no held payload")
    # The digest verifies transport; checked HEAD/gitlinks verify code identity.
    identity = RemoteArtifact(
        url="https://bundle.example.invalid/" + row.sha256 + ".bundle", sha256=row.sha256
    )
    only_mirror = Fetcher(
        fetcher.cache_dir,
        mirror=fetcher.mirror,
        mirror_transport=fetcher.mirror_transport,
        offline=True,
    )
    got = only_mirror.fetch(
        identity, max_bytes=row.size + 1024 * 1024, mirror=MirrorPath("git-bundles", name)
    )
    if got.size != row.size:
        raise BackendError(f"git bundle {name}: recorded size differs")
    return got.path


def clone_checked(bundle: Path, destination: Path, commit: str, *, runner: CommandRunner) -> None:
    """Verify *bundle* structurally, clone it with no upstream, and refuse
    anything but the pinned commit at ``HEAD``.

    ``git bundle verify`` runs in a scratch repository rather than
    *destination* itself, so a bundle that fails verification never leaves
    anything behind at the real destination to be mistaken for a checkout.
    ``git clone --no-checkout -- <bundle> <destination>`` names the bundle
    file as the only remote: no URL, no credential, nothing that could reach
    a network host even if one were configured to try.
    """
    from hammunition.backends.base import BackendError

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bundle-verify-", dir=destination.parent) as directory:
        scratch = Path(directory)
        checked_git(runner, scratch, "init", "--quiet")
        checked_git(runner, scratch, "bundle", "verify", str(bundle))
    checked_git(
        runner, destination.parent, "clone", "--no-checkout", "--", str(bundle), str(destination)
    )
    checked_git(runner, destination, "checkout", "--quiet", "--detach", commit)
    head = checked_git(runner, destination, "rev-parse", "HEAD")
    if head != commit:
        raise BackendError(f"git bundle HEAD {head} differs from pinned commit {commit}")


def _ancestors_are_not_symlinks(root: Path, relative: str) -> None:
    """Refuse *relative* (already :func:`~hammunition.catalogue.safe_relative`
    checked) if any component between *root* and its final one is a symlink
    on disk, not only the final destination.

    A git tree cannot represent one path as both a symlink blob and a tree of
    its own, so this guards against filesystem-level aliasing instead — a
    case-insensitive or Unicode-normalizing filesystem where a tracked symlink
    and a later-processed gitlink's ancestor can resolve to the same inode
    path, exactly the shape of CVE-2017-1000117 and the submodule checkout
    CVEs that followed it. Checked with ``is_symlink()`` on each prefix (an
    ``lstat``, never a ``resolve()``), so a symlink is caught at whichever
    level it sits, never silently followed.

    This check and :func:`clone_checked` on its result are two separate
    operations, not one atomic one: a concurrent process with write access to
    the *operator's own* build-cache tree can swap a validated ancestor for a
    symlink in the window between this call returning and the clone that
    follows it (a Codex Daybreak finding, #381 Task 15). The same shape of
    race is already open and documented, not solved, in
    :func:`hammunition.backends.source.prepare_tree` — "root still builds by
    path inside an operator-owned directory afterwards, which an operator-uid
    process can race; that is a separate, open issue, not solved here." That
    reasoning applies here unchanged: closing it needs descriptor-relative,
    no-follow filesystem operations (or build-into-a-sibling-then-rename)
    through this whole module, tracked as Hammunition #399, not attempted
    in this task.
    """
    from hammunition.backends.base import BackendError

    parts = relative.split("/")
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise BackendError(
                f"refusing {relative}: {current.relative_to(root)} is a symlink, not a "
                f"directory, between the superproject and the submodule's own destination"
            )


def checkout_gitlinks(
    root: Path,
    repo: Path,
    unit: str,
    parent_commit: str,
    *,
    fetcher: Fetcher,
    context: ResolutionContext,
    runner: CommandRunner,
    _visited: set[tuple[str, str]] | None = None,
    _depth: int = 0,
) -> int:
    """Walk *repo*'s own gitlinks, checking out each one from its own bundle.

    *root* is always the top-level unit's checkout (unchanged across every
    recursive call); *repo* is whichever directory is being walked right now.
    Returns the total number of submodules checked out, this level and every
    level below it, so the caller can report one combined count.

    *_visited* and *_depth* are not part of this function's public contract —
    every call in this module and every test calls it with neither — but they
    thread a recursion guard through the function's own calls to itself: a
    maximum depth of :data:`MAX_GITLINK_DEPTH`, and a record of every
    ``(path, commit)`` already walked, so a maliciously crafted (or merely
    buggy) nested ``.gitmodules`` layout cannot exhaust the process.
    """
    from hammunition.backends.base import BackendError

    if _depth >= MAX_GITLINK_DEPTH:
        raise BackendError(
            f"git bundle gitlinks nest past {MAX_GITLINK_DEPTH} levels; refusing to keep "
            f"recursing. A real project does not nest submodules this deep, and a layout "
            f"that does is treated as hostile rather than merely unusual."
        )
    visited = _visited if _visited is not None else set()

    output = checked_git(runner, repo, "ls-tree", "-r", "-z", "HEAD")
    links: list[tuple[str, str]] = []
    for record in output.split("\x00"):
        if not record:
            continue
        metadata, separator, path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise BackendError("git bundle contains an unreadable tree record")
        if fields[0] != "160000":
            continue
        safe_relative(path, "submodule path")
        if (
            ".git" in path.split("/")
            or fields[1] != "commit"
            or COMMIT_SHA.fullmatch(fields[2]) is None
        ):
            raise BackendError("git bundle contains an unsafe gitlink")
        links.append((path, fields[2]))
    if not links:
        return 0
    config = checked_git(
        runner, repo, "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"
    )
    names: dict[str, str] = {}
    for line in config.splitlines():
        pair = line.split(maxsplit=1)
        if len(pair) != 2 or pair[1] in names:
            raise BackendError("git bundle .gitmodules has duplicate/unreadable paths")
        names[pair[1]] = pair[0].removesuffix(".path")
    total = 0
    for path, commit in links:
        if path not in names:
            raise BackendError(f"gitlink {path} has no .gitmodules entry")
        destination = repo / path
        relative = destination.relative_to(root).as_posix()
        key = (relative, commit)
        if key in visited:
            raise BackendError(
                f"gitlink {relative}@{commit} was already checked out once in this "
                f"checkout; refusing to walk it again rather than recurse indefinitely"
            )
        visited.add(key)
        _ancestors_are_not_symlinks(root, relative)
        # clone --no-checkout leaves a gitlink directory empty; reject links/files.
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise BackendError(f"unsafe submodule destination {relative}")
        if destination.is_dir():
            if any(destination.iterdir()):
                raise BackendError(f"submodule destination {relative} is not empty")
            destination.rmdir()
        bundle = fetch_bundle(
            unit, parent_commit, path=relative, subcommit=commit, fetcher=fetcher, context=context
        )
        clone_checked(bundle, destination, commit, runner=runner)
        checked_git(runner, repo, "config", names[path] + ".url", str(bundle))
        checked_git(runner, repo, "config", names[path] + ".active", "true")
        total += 1 + checkout_gitlinks(
            root,
            destination,
            unit,
            parent_commit,
            fetcher=fetcher,
            context=context,
            runner=runner,
            _visited=visited,
            _depth=_depth + 1,
        )
    return total


def checkout_bundle(
    unit: str,
    block: GitInstall,
    destination: Path,
    *,
    fetcher: Fetcher,
    context: ResolutionContext,
    runner: CommandRunner,
) -> str:
    """Check out *unit* from its mirrored bundle, verified at every level.

    The repository's pin is ``block.commit`` for a tag, ``block.ref`` for a
    SHA (the same rule the git backend's own pin check uses); a tag lacking a
    recorded commit cannot be offline-verified at all, and is refused by name
    rather than silently trusting whatever the bundle happens to resolve the
    tag to. A moved tag — the bundle is bit-for-bit what the Bunker vouches
    for, and its ``refs/tags/<ref>`` has simply been re-pointed since the pin
    was recorded — is caught here, before anything about the tag is recreated
    or trusted a second time.
    """
    from hammunition.backends.base import BackendError

    commit = block.commit or block.ref
    if COMMIT_SHA.fullmatch(commit) is None:
        raise BackendError(
            f"{unit}: tag {block.ref} has no recorded commit; offline bundle verification "
            f"needs a repository pin"
        )
    bundle = fetch_bundle(unit, commit, path=None, subcommit=None, fetcher=fetcher, context=context)
    clone_checked(bundle, destination, commit, runner=runner)
    if COMMIT_SHA.fullmatch(block.ref) is None:
        actual = checked_git(runner, destination, "rev-parse", f"refs/tags/{block.ref}^{{commit}}")
        if actual != commit:
            raise BackendError(
                f"tag {block.ref} no longer resolves to the commit it is pinned to: "
                f"{commit}, got {actual}"
            )
    count = (
        checkout_gitlinks(
            destination, destination, unit, commit, fetcher=fetcher, context=context, runner=runner
        )
        if block.submodules
        else 0
    )
    context.note("git-bundles", bundle_name(unit, commit), fallback=not context.offline)
    return f"HEAD is {commit}, matching the pin; {count} recursive submodule(s) checked"
