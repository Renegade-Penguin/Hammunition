# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Verified git bundles, tags and recursive gitlinks.  D-024, D-070, #381 Task 15.

Every checkout test here runs real ``git`` against real, local, temporary
repositories and bundles — never a network remote, never a mock of git
itself. What is mocked is the Bunker: a signed catalogue row per bundle and a
LAN-mirror transport that serves the bundle's bytes, exactly the shape
``hammunition mirror enrol`` leaves behind. Where a scenario needs a tree git
itself refuses to create (a gitlink literally named ``.git``, a path that
escapes the checkout), a stand-in ``CommandRunner`` answers the one command
that matters instead of real git — those are named so in the test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from bunker_fixtures import artifact, make_context
from hammunition.backends.base import (
    BackendError,
    Command,
    CommandResult,
    CommandRunner,
    SubprocessRunner,
)
from hammunition.catalogue import CatalogueError
from hammunition.fetch import Fetcher, MirrorPath, mirror_url
from hammunition.gitbundles import (
    MAX_GITLINK_DEPTH,
    _ancestors_are_not_symlinks,
    bundle_name,
    checkout_bundle,
    checkout_gitlinks,
    clone_checked,
    fetch_bundle,
)
from hammunition.manifest.schema import GitInstall, PinReview
from hammunition.resolution import CatalogueMiss, ResolutionContext
from test_fetch_mirror import Routes

REPO_ROOT = Path(__file__).resolve().parent.parent
MIRROR = "http://bunker.invalid"

# ---------------------------------------------------------------------------
# Real git fixtures
# ---------------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repository(tmp_path: Path, name: str) -> tuple[Path, str]:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "file").write_text(name)
    git(repo, "add", "file")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


def add_submodule(parent: Path, child: Path, path: str) -> str:
    """Add *child* as a submodule of *parent* at *path* and commit it.

    ``protocol.file.allow=always`` is needed only because git refuses a local
    ``file://``-shaped submodule URL by default since the 2022 CVEs about
    exactly that; it is never set anywhere outside fixture construction."""
    git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(child), path)
    git(parent, "-c", "commit.gpgsign=false", "commit", "-qm", f"add submodule {path}")
    return git(parent, "rev-parse", "HEAD")


def bundle_bytes(repo: Path, tmp_path: Path, label: str) -> bytes:
    path = tmp_path / f"{label}.bundle"
    git(repo, "bundle", "create", str(path), "--all")
    return path.read_bytes()


def _nested_fixture(tmp_path: Path) -> tuple[Path, str, Path, str, Path, str]:
    """parent -> vendor/child -> lib/grandchild, three independent repositories."""
    grandchild, grand_commit = repository(tmp_path, "grandchild")
    child, _ = repository(tmp_path, "child")
    child_commit = add_submodule(child, grandchild, "lib/grandchild")
    parent, _ = repository(tmp_path, "parent")
    parent_commit = add_submodule(parent, child, "vendor/child")
    return parent, parent_commit, child, child_commit, grandchild, grand_commit


def _context_and_fetcher(
    tmp_path: Path, entries: dict[str, bytes]
) -> tuple[ResolutionContext, Fetcher, Routes]:
    """A verified Bunker context and a mirror-only fetcher serving *entries*
    (bundle name -> bundle bytes) at the D-070 mirror paths, offline."""
    rows = [artifact("git-bundles", name, body) for name, body in entries.items()]
    context = make_context(tmp_path / "ctx", rows)
    routes = Routes(
        {
            mirror_url(MIRROR, MirrorPath("git-bundles", name)): body
            for name, body in entries.items()
        }
    )
    fetcher = Fetcher(tmp_path / "cache", mirror=MIRROR, mirror_transport=routes, offline=True)
    return context, fetcher, routes


class _Recording(SubprocessRunner):
    """The real runner, with every argv it actually ran kept for inspection."""

    def __init__(self) -> None:
        super().__init__()
        self.argvs: list[tuple[str, ...]] = []

    def run(self, command: Command) -> CommandResult:
        self.argvs.append(command.argv)
        return super().run(command)


def _pin_review() -> PinReview:
    return PinReview(
        last_reviewed=date(2026, 8, 26),
        reviewed_by="hammunition-maintainers",
        basis="distribution_pin",
        distributions=["kali", "parrot"],
        rationale="Pinned for this test suite's own synthetic fixture repository, nothing else.",
    )


def _sha_block(ref: str, *, submodules: bool = False) -> GitInstall:
    return GitInstall(
        repo="https://example.invalid/thing",
        ref=ref,
        pin_review=_pin_review(),
        build_system="cmake",
        submodules=submodules,
    )


def _tag_block(ref: str, commit: str, *, submodules: bool = False) -> GitInstall:
    return GitInstall(
        repo="https://example.invalid/thing",
        ref=ref,
        commit=commit,
        build_system="cmake",
        submodules=submodules,
    )


# ---------------------------------------------------------------------------
# Naming contract
# ---------------------------------------------------------------------------


def test_bundle_names_are_contract_names(tmp_path: Path) -> None:
    _, commit = repository(tmp_path, "parent")
    _, subcommit = repository(tmp_path, "child")
    assert bundle_name("example-tool", commit) == f"example-tool@{commit}"
    assert (
        bundle_name("example-tool", commit, path="vendor/child", subcommit=subcommit)
        == f"example-tool@{commit}/vendor/child@{subcommit}"
    )


def test_bundle_name_needs_a_full_commit() -> None:
    with pytest.raises(BackendError, match="full pinned commit"):
        bundle_name("thing", "not-a-commit")


def test_bundle_name_refuses_a_unit_with_a_slash(tmp_path: Path) -> None:
    _, commit = repository(tmp_path, "parent")
    with pytest.raises(BackendError, match="unit and full pinned commit"):
        bundle_name("a/b", commit)


def test_bundle_name_submodule_needs_its_own_commit(tmp_path: Path) -> None:
    _, commit = repository(tmp_path, "parent")
    with pytest.raises(BackendError, match="gitlink commit"):
        bundle_name("thing", commit, path="vendor/child")


def test_bundle_name_refuses_a_subcommit_with_no_path(tmp_path: Path) -> None:
    _, commit = repository(tmp_path, "parent")
    with pytest.raises(BackendError, match="commit has no path"):
        bundle_name("thing", commit, subcommit=commit)


# ---------------------------------------------------------------------------
# The fresh-interpreter import: no cycle through hammunition.backends
# ---------------------------------------------------------------------------


def test_gitbundles_import_without_backend_cycle() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", "import hammunition.gitbundles"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# A simple, single-repository checkout
# ---------------------------------------------------------------------------


def test_checkout_bundle_verifies_a_bare_sha_pin(tmp_path: Path) -> None:
    """``ref`` itself a commit SHA: the "detached correct commit" case --
    there is no tag at all, and the pin is the ref."""
    repo, commit = repository(tmp_path, "thing")
    context, fetcher, routes = _context_and_fetcher(
        tmp_path, {bundle_name("thing", commit): bundle_bytes(repo, tmp_path, "thing")}
    )
    outcome = checkout_bundle(
        "thing",
        _sha_block(commit),
        tmp_path / "build",
        fetcher=fetcher,
        context=context,
        runner=SubprocessRunner(),
    )
    assert commit in outcome
    assert git(tmp_path / "build", "rev-parse", "HEAD") == commit
    # Never asked anything but the mirror, and never the dummy publisher URL
    # fetch_bundle constructs on purpose to keep it undialable.
    assert routes.requested == [
        mirror_url(MIRROR, MirrorPath("git-bundles", bundle_name("thing", commit)))
    ]


def test_checkout_bundle_verifies_an_annotated_tag(tmp_path: Path) -> None:
    repo, commit = repository(tmp_path, "thing")
    git(repo, "tag", "-a", "-m", "release", "v2.0", commit)
    context, fetcher, _ = _context_and_fetcher(
        tmp_path, {bundle_name("thing", commit): bundle_bytes(repo, tmp_path, "thing")}
    )
    outcome = checkout_bundle(
        "thing",
        _tag_block("v2.0", commit),
        tmp_path / "build",
        fetcher=fetcher,
        context=context,
        runner=SubprocessRunner(),
    )
    assert commit in outcome


def test_checkout_bundle_refuses_a_tag_with_no_recorded_commit(tmp_path: Path) -> None:
    """A tag the manifest never recorded a `commit` for cannot be verified
    offline; it must refuse by name, never adopt whatever the bundle says."""
    repo, commit = repository(tmp_path, "thing")
    git(repo, "tag", "v1.0", commit)
    context, fetcher, _ = _context_and_fetcher(tmp_path, {})
    block = GitInstall(repo="https://example.invalid/thing", ref="v1.0", build_system="cmake")
    with pytest.raises(BackendError, match="no recorded commit"):
        checkout_bundle(
            "thing",
            block,
            tmp_path / "build",
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )


def test_checkout_bundle_refuses_a_corrupt_bundle(tmp_path: Path) -> None:
    """The bytes match their own recorded sha256 (so transport is "fine") but
    are not a valid bundle: `git bundle verify` must still catch it."""
    repo, commit = repository(tmp_path, "thing")
    good = bundle_bytes(repo, tmp_path, "thing")
    corrupt = bytearray(good)
    # Well past the bundle's text header (the ref list git's own error output
    # would otherwise echo back verbatim) and into the binary pack data, so
    # corrupting it cannot itself turn git's stderr into invalid UTF-8.
    offset = good.find(b"PACK") + 40
    corrupt[offset] ^= 0xFF
    corrupt[offset + 1] ^= 0xFF
    context, fetcher, _ = _context_and_fetcher(
        tmp_path, {bundle_name("thing", commit): bytes(corrupt)}
    )
    with pytest.raises(BackendError, match="git bundle check failed"):
        checkout_bundle(
            "thing",
            _sha_block(commit),
            tmp_path / "build",
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )


# ---------------------------------------------------------------------------
# Tag moved after the pin was recorded
# ---------------------------------------------------------------------------


def test_bundle_with_moved_tag_is_refused_before_build(tmp_path: Path) -> None:
    repo, pin = repository(tmp_path, "upstream")
    git(repo, "-c", "tag.gpgSign=false", "tag", "v1.0", pin)
    (repo / "file").write_text("changed")
    git(repo, "add", "file")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "new")
    git(repo, "-c", "tag.gpgSign=false", "tag", "-f", "v1.0", "HEAD")
    bundle = tmp_path / "parent.bundle"
    git(repo, "bundle", "create", str(bundle), "--all")
    name = bundle_name("thing", pin)
    body = bundle.read_bytes()
    context = make_context(tmp_path / "keys", [artifact("git-bundles", name, body)])
    route = mirror_url("http://bunker.invalid", MirrorPath("git-bundles", name))
    transport = Routes({route: body})
    fetcher = Fetcher(
        tmp_path / "cache",
        mirror="http://bunker.invalid",
        mirror_transport=transport,
        transport=transport,
        offline=True,
    )
    block = GitInstall(
        repo="https://example.invalid/thing", ref="v1.0", commit=pin, build_system="cmake"
    )
    with pytest.raises(BackendError, match=r"tag v1\.0 no longer resolves"):
        checkout_bundle(
            "thing",
            block,
            tmp_path / "build",
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )
    assert transport.requested == [route]


def test_the_backend_never_recreates_a_tag_in_bundle_mode(tmp_path: Path) -> None:
    """Bundle mode has no `git tag -f` step at all -- the tag is only ever
    *read back and compared*, never recreated before (or after) that
    comparison. A recording runner over the moved-tag fixture proves no
    `tag` subcommand is ever issued."""
    repo, pin = repository(tmp_path, "upstream")
    git(repo, "tag", "v1.0", pin)
    context, fetcher, _ = _context_and_fetcher(
        tmp_path, {bundle_name("thing", pin): bundle_bytes(repo, tmp_path, "upstream")}
    )
    recorder = _Recording()
    checkout_bundle(
        "thing",
        _tag_block("v1.0", pin),
        tmp_path / "build",
        fetcher=fetcher,
        context=context,
        runner=recorder,
    )
    assert not any("tag" in argv for argv in recorder.argvs)


# ---------------------------------------------------------------------------
# Recursive gitlinks: parent -> child -> grandchild
# ---------------------------------------------------------------------------


def test_checkout_bundle_verifies_a_nested_submodule_tree(tmp_path: Path) -> None:
    parent, parent_commit, child, child_commit, grandchild, grand_commit = _nested_fixture(tmp_path)
    entries = {
        bundle_name("thing", parent_commit): bundle_bytes(parent, tmp_path, "parent"),
        bundle_name(
            "thing", parent_commit, path="vendor/child", subcommit=child_commit
        ): bundle_bytes(child, tmp_path, "child"),
        bundle_name(
            "thing",
            parent_commit,
            path="vendor/child/lib/grandchild",
            subcommit=grand_commit,
        ): bundle_bytes(grandchild, tmp_path, "grandchild"),
    }
    context, fetcher, routes = _context_and_fetcher(tmp_path, entries)
    destination = tmp_path / "build"
    recorder = _Recording()
    outcome = checkout_bundle(
        "thing",
        _sha_block(parent_commit, submodules=True),
        destination,
        fetcher=fetcher,
        context=context,
        runner=recorder,
    )
    assert "2 recursive submodule(s)" in outcome
    # Never asked the real submodule machinery to reach a remote.
    assert not any("update" in argv and "submodule" in argv for argv in recorder.argvs)
    assert not any("fetch" in argv for argv in recorder.argvs)
    # Every mirror route was asked exactly once, and nothing else.
    assert sorted(routes.requested) == sorted(
        mirror_url(MIRROR, MirrorPath("git-bundles", name)) for name in entries
    )
    # git's own reader confirms the whole tree, read back independently of
    # anything checkout_bundle itself claims. Read with no `.strip()` this
    # time: `git()`'s own strip would eat the first line's leading status
    # mark along with real trailing whitespace, which is exactly the "a
    # space and a dash look the same once you've stripped it" trap.
    raw_status = subprocess.run(
        ["git", "-C", str(destination), "submodule", "status", "--recursive"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = [line for line in raw_status.splitlines() if line.strip()]
    assert len(lines) == 2
    assert all(line.startswith(" ") for line in lines), repr(raw_status)
    # Each submodule's registered url is the local bundle file on disk,
    # never the original upstream URL -- no remote update could run even by
    # accident.
    child_url = git(destination, "config", "submodule.vendor/child.url")
    assert Path(child_url).is_file()
    assert child_url != "https://example.invalid/thing"


def test_checkout_bundle_refuses_when_the_child_bundle_is_absent(tmp_path: Path) -> None:
    """A missing recursive bundle refuses the whole checkout before any build
    step exists -- the parent's own tree is left in the build cache, and the
    outcome never returns."""
    parent, parent_commit, _child, _child_commit, _grandchild, _grand_commit = _nested_fixture(
        tmp_path
    )
    entries = {
        bundle_name("thing", parent_commit): bundle_bytes(parent, tmp_path, "parent"),
        # The child's own entry is never recorded on the Bunker.
    }
    context, fetcher, _ = _context_and_fetcher(tmp_path, entries)
    destination = tmp_path / "build"
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        checkout_bundle(
            "thing",
            _sha_block(parent_commit, submodules=True),
            destination,
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )
    # The parent's own checkout happened (it is the build cache's tree); it
    # was simply never completed, and nothing downstream of this call runs.
    assert (destination / "file").is_file()
    assert (destination / "vendor").is_dir()


def test_checkout_bundle_refuses_when_the_grandchild_bundle_is_absent(tmp_path: Path) -> None:
    parent, parent_commit, child, child_commit, _grandchild, _grand_commit = _nested_fixture(
        tmp_path
    )
    entries = {
        bundle_name("thing", parent_commit): bundle_bytes(parent, tmp_path, "parent"),
        bundle_name(
            "thing", parent_commit, path="vendor/child", subcommit=child_commit
        ): bundle_bytes(child, tmp_path, "child"),
        # The grandchild's own entry is never recorded.
    }
    context, fetcher, _ = _context_and_fetcher(tmp_path, entries)
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        checkout_bundle(
            "thing",
            _sha_block(parent_commit, submodules=True),
            tmp_path / "build",
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )


def test_checkout_bundle_refuses_a_wrong_bundle_for_a_gitlink(tmp_path: Path) -> None:
    """A bundle whose own sha256 matches what the Bunker recorded for a
    gitlink slot, but which is not that gitlink's repository at all (an
    unrelated bundle, correctly digested for itself) must still be refused:
    the digest proves transport, never code identity."""
    parent, parent_commit, _child, child_commit, _grandchild, _grand_commit = _nested_fixture(
        tmp_path
    )
    unrelated, _ = repository(tmp_path, "unrelated")
    entries = {
        bundle_name("thing", parent_commit): bundle_bytes(parent, tmp_path, "parent"),
        bundle_name(
            "thing", parent_commit, path="vendor/child", subcommit=child_commit
        ): bundle_bytes(unrelated, tmp_path, "unrelated"),
    }
    context, fetcher, _ = _context_and_fetcher(tmp_path, entries)
    with pytest.raises(BackendError, match="git bundle check failed"):
        checkout_bundle(
            "thing",
            _sha_block(parent_commit, submodules=True),
            tmp_path / "build",
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )


def test_checkout_bundle_refuses_a_gitlink_missing_from_gitmodules(tmp_path: Path) -> None:
    """`.gitmodules` is read back from the checked-out tree and must map
    every gitlink path; a tree that drops the stanza (by hand, or a
    generator bug) is refused rather than guessed at."""
    parent, _parent_commit, child, child_commit, _grandchild, _grand_commit = _nested_fixture(
        tmp_path
    )
    # Point the stanza at a different path than the one actually gitlinked,
    # rather than removing it outright -- an empty `.gitmodules` makes `git
    # config --get-regexp` itself exit non-zero (no matches), which is a
    # different, less specific failure than the one this test is after.
    rewritten = (parent / ".gitmodules").read_text().replace("vendor/child", "vendor/elsewhere")
    (parent / ".gitmodules").write_text(rewritten)
    git(parent, "add", ".gitmodules")
    git(
        parent,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "point the gitmodules stanza elsewhere",
    )
    new_parent_commit = git(parent, "rev-parse", "HEAD")
    entries = {
        bundle_name("thing", new_parent_commit): bundle_bytes(parent, tmp_path, "parent2"),
        bundle_name(
            "thing", new_parent_commit, path="vendor/child", subcommit=child_commit
        ): bundle_bytes(child, tmp_path, "child"),
    }
    context, fetcher, _ = _context_and_fetcher(tmp_path, entries)
    with pytest.raises(BackendError, match="no \\.gitmodules entry"):
        checkout_bundle(
            "thing",
            _sha_block(new_parent_commit, submodules=True),
            tmp_path / "build",
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )


# ---------------------------------------------------------------------------
# Hostile tree records a real git tree cannot represent, answered by a stub
# ---------------------------------------------------------------------------


class _FakeTreeRunner:
    """Answers `ls-tree` with one crafted record; refuses to be asked for
    anything else, so a test using it proves the guard bites *before* any
    further command (fetch, clone, config) would run."""

    def __init__(self, record: str) -> None:
        self.record = record

    def run(self, command: Command) -> CommandResult:
        if "ls-tree" in command.argv:
            return CommandResult(argv=command.argv, returncode=0, stdout=self.record, stderr="")
        raise AssertionError(f"unexpected command after the unsafe gitlink: {command.argv}")


SOME_SHA = "a" * 40


def test_fake_tree_runner_is_a_command_runner(tmp_path: Path) -> None:
    runner: CommandRunner = _FakeTreeRunner("")
    assert runner is not None


def test_checkout_gitlinks_refuses_a_gitlink_named_dot_git(tmp_path: Path) -> None:
    record = f"160000 commit {SOME_SHA}\tvendor/.git\x00"
    context, fetcher, _ = _context_and_fetcher(tmp_path, {})
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(BackendError, match="unsafe gitlink"):
        checkout_gitlinks(
            repo,
            repo,
            "thing",
            SOME_SHA,
            fetcher=fetcher,
            context=context,
            runner=_FakeTreeRunner(record),
        )


def test_checkout_gitlinks_refuses_a_gitlink_that_escapes_the_tree(tmp_path: Path) -> None:
    record = f"160000 commit {SOME_SHA}\t../escape\x00"
    context, fetcher, _ = _context_and_fetcher(tmp_path, {})
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(CatalogueError, match="unsafe relative path"):
        checkout_gitlinks(
            repo,
            repo,
            "thing",
            SOME_SHA,
            fetcher=fetcher,
            context=context,
            runner=_FakeTreeRunner(record),
        )


# ---------------------------------------------------------------------------
# Recursion guards: depth and the visited set
# ---------------------------------------------------------------------------


def test_checkout_gitlinks_refuses_past_the_depth_limit(tmp_path: Path) -> None:
    context, fetcher, _ = _context_and_fetcher(tmp_path, {})
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(BackendError, match=f"past {MAX_GITLINK_DEPTH} levels"):
        checkout_gitlinks(
            repo,
            repo,
            "thing",
            SOME_SHA,
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
            _depth=MAX_GITLINK_DEPTH,
        )


def test_checkout_gitlinks_refuses_a_gitlink_visited_twice(tmp_path: Path) -> None:
    parent, parent_commit, child, child_commit, _grandchild, _grand_commit = _nested_fixture(
        tmp_path
    )
    entries = {
        bundle_name("thing", parent_commit): bundle_bytes(parent, tmp_path, "parent"),
        bundle_name(
            "thing", parent_commit, path="vendor/child", subcommit=child_commit
        ): bundle_bytes(child, tmp_path, "child"),
    }
    context, fetcher, _ = _context_and_fetcher(tmp_path, entries)
    destination = tmp_path / "build"
    bundle = fetch_bundle(
        "thing", parent_commit, path=None, subcommit=None, fetcher=fetcher, context=context
    )
    clone_checked(bundle, destination, parent_commit, runner=SubprocessRunner())
    with pytest.raises(BackendError, match="already checked out once"):
        checkout_gitlinks(
            destination,
            destination,
            "thing",
            parent_commit,
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
            _visited={("vendor/child", child_commit)},
        )


# ---------------------------------------------------------------------------
# Symlink guards: every ancestor component, not only the final destination
# ---------------------------------------------------------------------------


def test_ancestors_are_not_symlinks_refuses_a_symlinked_prefix(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "vendor" / "libs").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = root / "vendor" / "libs"
    shutil.rmtree(real)
    real.symlink_to(elsewhere)
    with pytest.raises(BackendError, match="is a symlink"):
        _ancestors_are_not_symlinks(root, "vendor/libs/child")


def test_ancestors_are_not_symlinks_accepts_a_plain_tree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "vendor" / "libs").mkdir(parents=True)
    _ancestors_are_not_symlinks(root, "vendor/libs/child")  # must not raise


def test_checkout_gitlinks_refuses_a_real_checkout_with_a_planted_ancestor_symlink(
    tmp_path: Path,
) -> None:
    """The same guard, exercised through a real checkout: a symlink planted
    at a gitlink's ancestor directory (simulating a case-insensitive alias
    or a leftover from an earlier run) is caught before anything is cloned
    into it, never followed."""
    parent, parent_commit, child, child_commit, _grandchild, _grand_commit = _nested_fixture(
        tmp_path
    )
    entries = {
        bundle_name("thing", parent_commit): bundle_bytes(parent, tmp_path, "parent"),
        bundle_name(
            "thing", parent_commit, path="vendor/child", subcommit=child_commit
        ): bundle_bytes(child, tmp_path, "child"),
    }
    context, fetcher, _ = _context_and_fetcher(tmp_path, entries)
    destination = tmp_path / "build"
    bundle = fetch_bundle(
        "thing", parent_commit, path=None, subcommit=None, fetcher=fetcher, context=context
    )
    clone_checked(bundle, destination, parent_commit, runner=SubprocessRunner())
    # "vendor" was materialized empty by the checkout (a gitlink placeholder).
    # Replace it with a symlink, simulating an aliased or previously-left path.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(destination / "vendor")
    (destination / "vendor").symlink_to(elsewhere)
    with pytest.raises(BackendError, match="is a symlink"):
        checkout_gitlinks(
            destination,
            destination,
            "thing",
            parent_commit,
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )


def test_checkout_gitlinks_refuses_a_symlink_destination_itself(tmp_path: Path) -> None:
    parent, parent_commit, child, child_commit, _grandchild, _grand_commit = _nested_fixture(
        tmp_path
    )
    entries = {
        bundle_name("thing", parent_commit): bundle_bytes(parent, tmp_path, "parent"),
        bundle_name(
            "thing", parent_commit, path="vendor/child", subcommit=child_commit
        ): bundle_bytes(child, tmp_path, "child"),
    }
    context, fetcher, _ = _context_and_fetcher(tmp_path, entries)
    destination = tmp_path / "build"
    bundle = fetch_bundle(
        "thing", parent_commit, path=None, subcommit=None, fetcher=fetcher, context=context
    )
    clone_checked(bundle, destination, parent_commit, runner=SubprocessRunner())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (destination / "vendor" / "child").rmdir()
    (destination / "vendor" / "child").symlink_to(elsewhere)
    with pytest.raises(BackendError, match="unsafe submodule destination"):
        checkout_gitlinks(
            destination,
            destination,
            "thing",
            parent_commit,
            fetcher=fetcher,
            context=context,
            runner=SubprocessRunner(),
        )
