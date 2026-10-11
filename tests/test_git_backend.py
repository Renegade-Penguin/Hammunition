# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The git backend, and the pin check that is its reason for existing.

The archive backend asks *are these the right bytes* and a sha256 answers it.
This one asks *is this the right revision*, which a successful clone does not
answer: git can exit 0 having handed over a different commit than the catalog
was written against — a re-cut tag, a moved branch, a server that ignored what
was asked for. That is D-031 with teeth, because whatever landed is what gets
compiled and installed into `/usr/local`.

So most of what follows is about the check, not the clone.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from bunker_fixtures import artifact as catalogue_artifact
from bunker_fixtures import make_context

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hammunition.backends import (  # noqa: E402
    Action,
    BackendError,
    Command,
    CommandResult,
    GitBackend,
    RecordingRunner,
    SubprocessRunner,
)
from hammunition.fetch import Fetcher, MirrorPath, mirror_url  # noqa: E402
from hammunition.gitbundles import bundle_name  # noqa: E402
from hammunition.manifest.schema import ManifestError, PackageManifest  # noqa: E402
from hammunition.resolution import CatalogueMiss  # noqa: E402
from test_fetch_mirror import Routes  # noqa: E402

SHA = "36ea9a143422f5b374371461667ff53fb9387300"
OTHER_SHA = "0" * 40


def _manifest(**install: Any) -> PackageManifest:
    block: dict[str, Any] = {
        "method": "git",
        "repo": "https://example.invalid/thing",
        "ref": "v1.0",
        "build_system": "cmake",
    }
    block.update(install)
    return PackageManifest.model_validate(
        {
            "name": "thing",
            "version": "1.0",
            "summary": "A thing built from a pinned revision",
            "categories": ["digital-modes"],
            "install": [{"install": block, "build_depends": ["cmake"]}],
            "update": {"probe": {"method": "github_tags"}},
            "documentation": {
                "what_it_does": "Does a thing for the purposes of testing the backend.",
                "why_you_want_it": "Because the git backend needs a manifest to act on.",
                "upstream_url": "https://example.invalid/",
            },
        }
    )


def _pin_review(**overrides: Any) -> dict[str, Any]:
    base = {
        "last_reviewed": "2026-08-26",
        "reviewed_by": "hammunition-maintainers",
        "basis": "distribution_pin",
        "distributions": ["kali", "parrot"],
        "rationale": (
            "Chosen to match the commit Kali and Parrot package, so a source build "
            "and an apt install are the same revision rather than two."
        ),
    }
    base.update(overrides)
    return base


class _HeadRunner(RecordingRunner):
    """Answers `git rev-parse HEAD` with a chosen revision."""

    def __init__(self, head: str) -> None:
        super().__init__()
        self.head = head

    def run(self, command: Command) -> CommandResult:
        super().run(command)
        if "rev-parse" in command.argv:
            return CommandResult(
                argv=command.argv, returncode=0, stdout=self.head + "\n", stderr=""
            )
        return CommandResult(argv=command.argv, returncode=0, stdout="", stderr="")


def _backend(runner: Any, tmp_path: Path) -> GitBackend:
    return GitBackend(
        runner=runner, build_root=tmp_path / "build", prefix=Path("/usr/local"), jobs=2
    )


# ---------------------------------------------------------------------------
# The pin check
# ---------------------------------------------------------------------------


def test_a_checkout_that_is_not_the_pinned_commit_is_refused(tmp_path: Path) -> None:
    """git exited 0, so nothing else in the run would have noticed. Building
    this would install a revision the catalog was not written against."""
    backend = _backend(_HeadRunner(OTHER_SHA), tmp_path)

    with pytest.raises(BackendError) as caught:
        backend.verify_pin(tmp_path / "src", SHA)

    message = str(caught.value)
    assert SHA in message and OTHER_SHA in message, "the error must show both revisions"


def test_a_checkout_at_the_pinned_commit_is_confirmed(tmp_path: Path) -> None:
    backend = _backend(_HeadRunner(SHA), tmp_path)
    assert SHA in backend.verify_pin(tmp_path / "src", SHA)


def test_a_tag_records_what_it_resolved_to(tmp_path: Path) -> None:
    """There is nothing to compare a tag against — the point of a tag is that
    upstream chose it — so the resolved commit is recorded instead. That record
    is the raw material of the pin database: the day a tag is re-cut, the log
    says what it used to be."""
    backend = _backend(_HeadRunner(SHA), tmp_path)
    outcome = backend.verify_pin(tmp_path / "src", "v1.0")
    assert "v1.0" in outcome
    assert SHA in outcome


def test_an_unreadable_revision_is_an_error_not_a_pass(tmp_path: Path) -> None:
    class Failing(RecordingRunner):
        def run(self, command: Command) -> CommandResult:
            super().run(command)
            return CommandResult(
                argv=command.argv, returncode=128, stdout="", stderr="not a repository"
            )

    backend = _backend(Failing(), tmp_path)
    with pytest.raises(BackendError, match="could not read"):
        backend.verify_pin(tmp_path / "src", SHA)


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------


def test_the_pin_is_checked_after_the_checkout_and_before_the_build(tmp_path: Path) -> None:
    """Order is the whole point: a check after the build would confirm a
    revision that had already been installed."""
    manifest = _manifest()
    backend = _backend(_HeadRunner(SHA), tmp_path)
    steps = backend.steps(manifest, manifest.install[0])

    labels = [s.kind if isinstance(s, Action) else " ".join(s.argv[:2]) for s in steps]
    assert labels.index("verify-pin") > labels.index("git -C"), "the pin was checked too early"
    assert labels.index("verify-pin") < labels.index("cmake --fresh"), (
        "the build ran before the check"
    )


def test_the_fetch_is_shallow_and_by_ref(tmp_path: Path) -> None:
    """A pinned commit should cost one object walk, not a project's history."""
    manifest = _manifest()
    backend = _backend(_HeadRunner(SHA), tmp_path)
    steps = backend.steps(manifest, manifest.install[0])

    fetch = next(s for s in steps if isinstance(s, Command) and "fetch" in s.argv)
    assert "--depth" in fetch.argv and "1" in fetch.argv
    assert fetch.argv[-1] == "v1.0"


def test_only_the_install_step_is_privileged(tmp_path: Path) -> None:
    manifest = _manifest()
    backend = _backend(_HeadRunner(SHA), tmp_path)
    steps = backend.steps(manifest, manifest.install[0])

    privileged = [s for s in steps if s.requires_root]
    assert len(privileged) == 1
    assert isinstance(privileged[0], Command)
    assert privileged[0].argv[:2] == ("cmake", "--install")


def test_a_different_pin_builds_in_a_different_directory(tmp_path: Path) -> None:
    """Switching a pin must not build on top of the previous revision's objects."""
    backend = _backend(_HeadRunner(SHA), tmp_path)
    first = _manifest()
    second = _manifest(ref=SHA, pin_review=_pin_review())

    a = backend.layout(first, first.install[0].install)  # type: ignore[arg-type]
    b = backend.layout(second, second.install[0].install)  # type: ignore[arg-type]
    assert a.root != b.root


# ---------------------------------------------------------------------------
# What the schema makes unrepresentable — asserted here because the backend
# relies on it rather than re-checking it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ["master", "main", "HEAD", "trunk", "develop"])
def test_a_moving_ref_cannot_be_expressed(ref: str) -> None:
    with pytest.raises((ValidationError, ManifestError), match="moving branch"):
        _manifest(ref=ref)


def test_a_bare_sha_needs_a_pin_review() -> None:
    """A tag carries an upstream signal that a revision was worth naming; a SHA
    carries none, so pinning one moves a judgement onto us and it is recorded
    beside the pin rather than implied (D-024)."""
    with pytest.raises((ValidationError, ManifestError), match="pin_review"):
        _manifest(ref=SHA)


def test_a_sha_with_a_pin_review_is_accepted() -> None:
    manifest = _manifest(ref=SHA, pin_review=_pin_review())
    block = manifest.install[0].install
    assert block.pin_review is not None  # type: ignore[union-attr]
    assert block.pin_review.basis == "distribution_pin"  # type: ignore[union-attr]


def test_the_git_backend_passes_the_operator_to_the_tree_install(tmp_path: Path) -> None:
    """Issue #38, D-043: a git tree unit hands its tree to the operator by an
    explicit, logged step, the same as source, binary and venv."""
    backend = GitBackend(
        runner=RecordingRunner(),
        build_root=tmp_path / "build",
        prefix=Path("/usr/local"),
        jobs=2,
        owner="alice",
    )
    manifest = _manifest(install_tree=True, tree_marker="thing")
    steps = backend.steps(manifest, manifest.install[0])
    last = steps[-1]
    assert isinstance(last, Command)
    assert last.argv[:5] == ("chown", "-R", "-h", "--", "alice:")


def test_the_tag_step_ignores_the_operators_signing_config(tmp_path: Path) -> None:
    """`tag.gpgsign true` turns a plain `git tag` into an annotated, signed one and
    opens an editor; a navigation install sat in nano on the bench (2026-10-03)."""
    manifest = _manifest()
    backend = _backend(_HeadRunner(SHA), tmp_path)
    steps = backend.steps(manifest, manifest.install[0])

    tag = next(s for s in steps if isinstance(s, Command) and "tag" in s.argv)
    pairs = list(zip(tag.argv, tag.argv[1:], strict=False))
    assert ("-c", "tag.gpgSign=false") in pairs
    assert ("-c", "tag.forceSignAnnotated=false") in pairs
    assert tag.env.get("GIT_EDITOR") == "true"


def test_git_never_prompts_on_a_terminal(tmp_path: Path) -> None:
    """A credential or editor prompt inside a run is a hang, never a question."""
    manifest = _manifest()
    backend = _backend(_HeadRunner(SHA), tmp_path)
    steps = backend.steps(manifest, manifest.install[0])

    for step in steps:
        if isinstance(step, Command) and step.argv[0] == "git":
            assert step.env.get("GIT_TERMINAL_PROMPT") == "0", step.argv


# ---------------------------------------------------------------------------
# The mirrored bundle route (D-070, #381 Task 15): a Bunker-enrolled run
# tries the verified bundle before (or instead of) a network clone. Real git
# against real, local, temporary repositories throughout -- the plain-clone
# tests above never set `fetcher`/`context` at all, which is exactly what
# keeps every one of them running the unchanged path.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repository(tmp_path: Path, name: str) -> tuple[Path, str]:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "file").write_text(name)
    _git(repo, "add", "file")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _bundle_bytes(repo: Path, tmp_path: Path, label: str) -> bytes:
    path = tmp_path / f"{label}.bundle"
    _git(repo, "bundle", "create", str(path), "--all")
    return path.read_bytes()


def _bundle_backend(
    tmp_path: Path,
    *,
    offline: bool,
    rows: list[dict[str, Any]],
    routes: Routes,
    with_fetcher: bool = True,
) -> GitBackend:
    context = make_context(tmp_path / "ctx", rows, offline=offline)
    fetcher = (
        Fetcher(
            tmp_path / "cache",
            mirror="http://bunker.invalid",
            mirror_transport=routes,
            offline=offline,
        )
        if with_fetcher
        else None
    )
    return GitBackend(
        runner=SubprocessRunner(),
        build_root=tmp_path / "build",
        prefix=Path("/usr/local"),
        jobs=2,
        fetcher=fetcher,
        context=context,
    )


def test_backend_uses_the_bundle_route_when_the_bunker_lists_the_pin(tmp_path: Path) -> None:
    repo, commit = _repository(tmp_path, "upstream")
    _git(repo, "tag", "v1.0", commit)
    body = _bundle_bytes(repo, tmp_path, "upstream")
    name = bundle_name("thing", commit)
    route = mirror_url("http://bunker.invalid", MirrorPath("git-bundles", name))
    routes = Routes({route: body})
    backend = _bundle_backend(
        tmp_path, offline=False, rows=[catalogue_artifact("git-bundles", name, body)], routes=routes
    )
    manifest = _manifest(ref="v1.0", commit=commit)
    steps = backend.steps(manifest, manifest.install[0])
    kinds = [s.kind for s in steps if isinstance(s, Action)]
    assert "checkout" in kinds
    # Never built an `init`/`remote`/`fetch` Command at all in this mode.
    assert not [s for s in steps if isinstance(s, Command) and s.argv[0] == "git"]
    [prepare] = [s for s in steps if isinstance(s, Action) and s.kind == "prepare"]
    prepare.perform()
    [checkout] = [s for s in steps if isinstance(s, Action) and s.kind == "checkout"]
    outcome = checkout.perform()
    assert commit in outcome
    assert routes.requested == [route]


def test_backend_falls_back_to_a_network_clone_when_the_bundle_route_fails_online(
    tmp_path: Path,
) -> None:
    """The bundle's own sha256 matches (so transport "passed"), but it is not
    a valid bundle at all; online, this is a mirror failure, not a refusal --
    the fallback must actually run, not merely exist."""
    repo, commit = _repository(tmp_path, "upstream")
    body = bytearray(_bundle_bytes(repo, tmp_path, "upstream"))
    offset = bytes(body).find(b"PACK") + 40
    body[offset] ^= 0xFF
    body[offset + 1] ^= 0xFF
    name = bundle_name("thing", commit)
    route = mirror_url("http://bunker.invalid", MirrorPath("git-bundles", name))
    routes = Routes({route: bytes(body)})
    backend = _bundle_backend(
        tmp_path,
        offline=False,
        rows=[catalogue_artifact("git-bundles", name, bytes(body))],
        routes=routes,
    )
    manifest = _manifest(ref="v1.0", commit=commit)
    steps = backend.steps(manifest, manifest.install[0])
    [prepare] = [s for s in steps if isinstance(s, Action) and s.kind == "prepare"]
    prepare.perform()
    [checkout] = [s for s in steps if isinstance(s, Action) and s.kind == "checkout"]
    with pytest.raises(BackendError) as caught:
        checkout.perform()
    # The failure that escaped is the *fallback's* own (a real DNS failure
    # against the reserved, never-resolving example.invalid), not the
    # bundle route's -- proof the fallback actually ran rather than the
    # first failure simply propagating unchanged.
    assert "git bundle" not in str(caught.value)
    assert "Fetch thing at v1.0" in str(caught.value)


def test_backend_checks_the_pin_before_recreating_a_moved_tag_or_touching_submodules(
    tmp_path: Path,
) -> None:
    """A Codex Daybreak finding (#381 Task 15): the network fallback is one
    function, not separate steps, so it has to check the pin itself before
    recreating a tag or fetching a single submodule from the network --
    otherwise a re-cut tag whose publisher also serves a hostile submodule
    would have it fetched before the mismatch was ever noticed, with the
    separate verify-pin Action (next in the plan) catching it only too late."""
    upstream, old_commit = _repository(tmp_path, "upstream")
    _git(upstream, "-c", "tag.gpgSign=false", "tag", "v1.0", old_commit)
    (upstream / "file").write_text("moved")
    _git(upstream, "add", "file")
    _git(upstream, "-c", "commit.gpgsign=false", "commit", "-qm", "moved")
    # A submodule the fallback must never reach: `submodule update` against
    # this URL would fail loudly (DNS), which is exactly how this test tells
    # the two failure modes apart.
    (upstream / ".gitmodules").write_text(
        '[submodule "child"]\n\tpath = child\n\turl = https://example.invalid/never-reached\n'
    )
    _git(upstream, "add", ".gitmodules")
    _git(upstream, "-c", "commit.gpgsign=false", "commit", "-qm", "add a submodule entry")
    _git(upstream, "-c", "tag.gpgSign=false", "tag", "-f", "v1.0", "HEAD")

    body = bytearray(_bundle_bytes(upstream, tmp_path, "upstream"))
    offset = bytes(body).find(b"PACK") + 40
    body[offset] ^= 0xFF
    body[offset + 1] ^= 0xFF
    name = bundle_name("thing", old_commit)
    route = mirror_url("http://bunker.invalid", MirrorPath("git-bundles", name))
    routes = Routes({route: bytes(body)})
    backend = _bundle_backend(
        tmp_path,
        offline=False,
        rows=[catalogue_artifact("git-bundles", name, bytes(body))],
        routes=routes,
    )
    manifest = _manifest(repo=str(upstream), ref="v1.0", commit=old_commit, submodules=True)
    steps = backend.steps(manifest, manifest.install[0])
    [prepare] = [s for s in steps if isinstance(s, Action) and s.kind == "prepare"]
    prepare.perform()
    [checkout] = [s for s in steps if isinstance(s, Action) and s.kind == "checkout"]
    with pytest.raises(BackendError, match=r"tag v1\.0 no longer resolves") as caught:
        checkout.perform()
    # Not the submodule's own DNS failure: the pin check stopped the fallback
    # before `submodule update` ever ran.
    assert "never-reached" not in str(caught.value)
    src = tmp_path / "build" / "thing-v1.0"
    assert not (src / "child").exists()


def test_backend_refuses_offline_without_a_bundle_route(tmp_path: Path) -> None:
    """Before any build step is returned, not only when the checkout Action
    is later performed (D-031: a plan must be complete and accurate)."""
    backend = _bundle_backend(tmp_path, offline=True, rows=[], routes=Routes({}))
    manifest = _manifest(ref="v1.0", commit=SHA)
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        backend.steps(manifest, manifest.install[0])


def test_backend_refuses_offline_with_no_fetcher(tmp_path: Path) -> None:
    backend = _bundle_backend(
        tmp_path, offline=True, rows=[], routes=Routes({}), with_fetcher=False
    )
    manifest = _manifest(ref="v1.0", commit=SHA)
    with pytest.raises(BackendError, match="without a fetcher"):
        backend.steps(manifest, manifest.install[0])


def test_backend_refuses_offline_a_tag_with_no_recorded_commit(tmp_path: Path) -> None:
    backend = _bundle_backend(tmp_path, offline=True, rows=[], routes=Routes({}))
    manifest = _manifest()  # the default block: ref="v1.0", no `commit`
    with pytest.raises(BackendError, match="no recorded commit"):
        backend.steps(manifest, manifest.install[0])


def test_backend_plain_clone_is_unaffected_with_no_context_at_all(tmp_path: Path) -> None:
    """The existing, context-less path (every test above this section) is
    exactly what every operator not enrolled in a Bunker still gets."""
    manifest = _manifest()
    backend = _backend(_HeadRunner(SHA), tmp_path)
    steps = backend.steps(manifest, manifest.install[0])
    assert not [s for s in steps if isinstance(s, Action) and s.kind == "checkout"]
    assert [s for s in steps if isinstance(s, Command) and s.argv[:2] == ("git", "init")]
