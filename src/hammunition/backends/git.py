# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Building from a pinned git revision.  DESIGN.md §6, D-024.

The archive backend's problem is *are these the right bytes*, and a sha256
answers it. This backend's problem is *is this the right revision*, and it is
not the same question: a clone can succeed, and succeed completely, while
handing you a different commit than the one the catalog was written against —
a re-cut tag, a moved branch, a server that quietly ignored what was asked for.

So the pin is checked after the checkout rather than assumed from it. ``git``
exiting 0 is not evidence you got the revision you named (**D-031**), and this
is the backend where that distinction has teeth: what gets compiled and
installed into ``/usr/local`` is decided entirely by which commit landed.

**A moving ref is unrepresentable.** The schema refuses ``master``, ``main``,
``HEAD``, ``trunk`` and ``develop`` outright, and requires a
:class:`~hammunition.manifest.schema.PinReview` — who looked, when, and why this
commit — whenever the ref is a bare SHA. A tag carries an upstream signal that
somebody thought a revision worth naming; a SHA carries none, so pinning one
moves a judgement upstream stopped making onto us and that judgement is recorded
beside the pin rather than implied by it.

**The fetch is shallow and by ref**, so a pinned commit costs one object walk
rather than a project's whole history. ``git fetch --depth 1 origin <ref>``
works for both a tag and a SHA against every forge the catalog names.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition.gitbundles import GIT_ENV, bundle_name, checkout_bundle
from hammunition.manifest.schema import (
    COMMIT_SHA,
    GitInstall,
    InstallBlock,
    PackageManifest,
    PrepareStep,
    RemoteArtifact,
    effective_binaries,
)
from hammunition.payloads import payload_action, preflight_payloads
from hammunition.resolution import CatalogueMiss

from .base import Action, BackendError, Command, CommandRunner
from .source import (
    SourceLayout,
    build_commands,
    needs_root_for,
    patch_steps,
    prepare_tree,
    tree_install_commands,
)

# GIT_ENV moved to `hammunition.gitbundles` (#381 Task 15), which is a leaf
# module both this backend and the bundle-verification code import from --
# the one constant, not defined twice. Every git the engine runs is
# unattended: an operator's own git config can turn a plain `git tag` into an
# annotated, signed one (`tag.gpgsign true`), which opens an editor; a
# credential prompt on a fetch would wait the same way. A navigation install
# sat in nano on the bench for that (2026-10-03).

if TYPE_CHECKING:
    # Type-only: `hammunition.backends/__init__.py` imports this module
    # eagerly, and `hammunition.fetch` imports `hammunition.backends.base`,
    # so a module-level import here is the other half of #158's cycle.
    # `Fetcher` is only ever used in an annotation, which `from __future__
    # import annotations` defers, so it never needs a real import.
    from hammunition.fetch import Fetcher
    from hammunition.resolution import ResolutionContext

MIB = 1024 * 1024

#: `git submodule status` marks: a leading space is "checked out at the
#: gitlink", and every other mark is a submodule the build must not start on.
_SUBMODULE_MARKS = {
    "-": "not checked out",
    "+": "checked out, but not at the commit the superproject records",
    "U": "in a merge conflict",
}

__all__ = ["GitBackend"]


@dataclass(frozen=True)
class GitBackend:
    """Turns a ``git`` install block into the steps that build it."""

    runner: CommandRunner
    """Used by the pin check, which has to ask git what actually landed. Going
    through the runner rather than :mod:`subprocess` keeps the one seam every
    other command uses, so the check is testable without a network or a clone."""

    build_root: Path
    prefix: Path
    jobs: int
    owner: str | None = None
    """The operator an installed tree is handed to (D-043); None keeps it root's."""
    fetcher: Fetcher | None = None
    """Fetches a block's pinned `extra_files` (D-069). A block that names one
    and a backend built without it is refused by name, never half-planned."""

    context: ResolutionContext | None = None
    """The run's resolution context: offline, every pinned `extra_files`
    artifact is required on the Bunker before any build step exists."""

    method = "git"

    def layout(self, manifest: PackageManifest, block: GitInstall) -> SourceLayout:
        """Where this package builds. Pure — touches no disk.

        Keyed by the ref rather than a digest, so switching a pin builds in a
        new directory instead of on top of the previous revision's objects.
        """
        return SourceLayout(self.build_root / f"{manifest.name}-{block.ref[:12]}")

    def steps(
        self, manifest: PackageManifest, install_block: InstallBlock
    ) -> list[Action | Command]:
        """The steps for one resolved block.

        Takes the whole :class:`InstallBlock` rather than its method alone: a
        block may declare its own `binaries`, and the list this backend copies
        must be the one the effect check reads back (issue #69).
        """
        block = install_block.install
        if not isinstance(block, GitInstall):
            raise BackendError(
                f"{manifest.name} resolved to a {block.method} block and this backend "
                f"builds from a git checkout. Building it anyway would install "
                f"something the plan never named."
            )
        # Checked here, before the checkout, build and install steps below
        # are even constructed, never only when _extra_file_steps builds its
        # own steps at the end of this list (D-070, #381 Task 13): a build
        # that runs to completion before discovering the Bunker cannot
        # answer for one of its extras is not "refused before any build
        # step", whatever _extra_file_steps itself goes on to check.
        remote_extras = tuple(
            (
                RemoteArtifact(url=extra.artifact.url, sha256=extra.artifact.sha256),
                extra.artifact.size,
            )
            for extra in block.extra_files
            if extra.artifact is not None
        )
        if (
            remote_extras
            and self.fetcher is not None
            and self.fetcher.offline
            and (self.context is None or not self.context.offline)
        ):
            # preflight_payloads silently does nothing with no context, or
            # with one that disagrees and says online (it treats either the
            # same as "online"), so an offline run must refuse here itself
            # rather than rely on that call to catch either mismatch.
            raise BackendError(
                f"{manifest.name}: offline, its pinned extra_files need the Bunker's "
                f"verified catalogue to check before any build step runs, and this run "
                f"has none. Nothing was planned."
            )
        preflight_payloads(manifest.name, remote_extras, context=self.context)
        layout = self.layout(manifest, block)
        src = layout.src

        # The repository's own pin (D-070, #381 Task 15): `block.commit` for a
        # tag, `block.ref` itself when it already is a SHA. A tag the manifest
        # never recorded a commit for cannot be bundle-verified at all.
        repo_commit = block.ref if COMMIT_SHA.match(block.ref) else block.commit
        truly_offline = self.context is not None and self.context.offline
        bundle_listed = (
            repo_commit is not None
            and self.context is not None
            and self.context.verified is not None
            and self.context.verified.catalogue.artifact(
                "git-bundles",
                bundle_name(manifest.name, repo_commit),
                self.context.enrolment_id,
            )
            is not None
        )
        if truly_offline:
            assert self.context is not None  # truly_offline's own condition
            if self.fetcher is None:
                raise BackendError(
                    f"{manifest.name}: offline, and this git backend was built without "
                    f"a fetcher; its pinned revision cannot be checked out from a "
                    f"mirrored bundle without one. Nothing was planned."
                )
            if repo_commit is None:
                raise BackendError(
                    f"{manifest.name}: tag {block.ref} has no recorded commit; offline "
                    f"bundle verification needs a repository pin. Nothing was planned."
                )
            # Checked here, before any step below exists, the same reason the
            # extra_files preflight above runs first: the root bundle itself
            # must be on the Bunker. A recursive gitlink's own bundle cannot be
            # named yet -- its commit is not in the manifest, only in the
            # parent's own tree -- so it is still checked, just later, inside
            # the one Action that walks the checkout (D-070's enumeration gap,
            # Task 16).
            self.context.entry("git-bundles", bundle_name(manifest.name, repo_commit))
        use_bundle = self.fetcher is not None and (truly_offline or bundle_listed)

        if use_bundle:
            assert repo_commit is not None  # truly_offline refused above; bundle_listed implies it
            steps: list[Action | Command] = [
                Action(
                    kind="prepare",
                    description=f"Clear any previous {manifest.name} checkout",
                    detail=f"{src} (removed if present, then recreated)",
                    perform=lambda: prepare_tree(src),
                ),
                Action(
                    kind="checkout",
                    description=f"Check out {manifest.name} from its mirrored git bundle",
                    detail=(
                        f"git-bundles/{bundle_name(manifest.name, repo_commit)} -> {src}, "
                        + (
                            "verified offline; no publisher is asked"
                            if truly_offline
                            else f"tried first, {block.repo} on failure (D-070)"
                        )
                    ),
                    perform=lambda: self._checkout_from_bundle(
                        manifest, block, src, truly_offline=truly_offline
                    ),
                ),
                Action(
                    kind="verify-pin",
                    description=f"Confirm {manifest.name} is at the pinned revision",
                    detail=(
                        f"git rev-parse HEAD in {src} must be {block.commit}, the commit "
                        f"tag {block.ref} is pinned to"
                        if block.commit
                        else f"git rev-parse HEAD in {src} must be {block.ref}"
                    ),
                    perform=lambda: self.verify_pin(src, block.ref, commit=block.commit),
                ),
            ]
            if block.submodules:
                # The bundle checkout above already populated every submodule
                # from its own local bundle (or, on an online fallback, the
                # network step just run did the equivalent `submodule update`);
                # no second, network-reaching Command is added here.
                steps.append(
                    Action(
                        kind="verify-submodules",
                        description=(
                            f"Confirm every {manifest.name} submodule is at its recorded commit"
                        ),
                        detail=(
                            f"git submodule status --recursive in {src}: each line must "
                            f"start with a space"
                        ),
                        perform=partial(self.verify_submodules, src),
                    )
                )
        else:
            steps = [
                Action(
                    kind="prepare",
                    description=f"Clear any previous {manifest.name} checkout",
                    detail=f"{src} (removed if present, then recreated)",
                    perform=lambda: prepare_tree(src),
                ),
                Command(
                    argv=("git", "init", "--quiet", str(src)),
                    env=GIT_ENV,
                    description=f"Start an empty repository for {manifest.name}",
                ),
                Command(
                    argv=("git", "-C", str(src), "remote", "add", "origin", block.repo),
                    env=GIT_ENV,
                    description=f"Point it at {block.repo}",
                ),
                Command(
                    # Shallow and by ref: a pinned commit costs one object walk, not
                    # the project's whole history.
                    argv=("git", "-C", str(src), "fetch", "--depth", "1", "origin", block.ref),
                    env=GIT_ENV,
                    description=f"Fetch {manifest.name} at {block.ref}",
                ),
                Command(
                    argv=("git", "-C", str(src), "checkout", "--quiet", "FETCH_HEAD"),
                    env=GIT_ENV,
                    description=f"Check out {block.ref}",
                ),
                *(
                    [
                        Command(
                            # A shallow tag fetch leaves only FETCH_HEAD; builds
                            # that version themselves with `git describe` then see
                            # no tag at all. Recreating the ref locally costs
                            # nothing and makes describe answer with the pin.
                            # Lightweight on purpose, whatever the operator's git
                            # config says: a signed tag needs a message and a key.
                            argv=(
                                "git",
                                "-c",
                                "tag.gpgSign=false",
                                "-c",
                                "tag.forceSignAnnotated=false",
                                "-C",
                                str(src),
                                "tag",
                                "-f",
                                block.ref,
                                "FETCH_HEAD",
                            ),
                            env=GIT_ENV,
                            description=f"Recreate the {block.ref} tag for describe-based versioning",
                        )
                    ]
                    if not COMMIT_SHA.match(block.ref)
                    else []
                ),
                Action(
                    kind="verify-pin",
                    description=f"Confirm {manifest.name} is at the pinned revision",
                    detail=(
                        f"git rev-parse HEAD in {src} must be {block.commit}, the commit "
                        f"tag {block.ref} is pinned to"
                        if block.commit
                        else f"git rev-parse HEAD in {src} must be {block.ref}"
                    ),
                    perform=lambda: self.verify_pin(src, block.ref, commit=block.commit),
                ),
            ]
            if block.submodules:
                steps.extend(self._submodule_steps(manifest, src))
        # After the pin is confirmed and before anything is compiled, the same
        # way a source block is patched: the checkout is cleared and re-fetched
        # every run, so a patch never applies twice.
        steps.extend(patch_steps(manifest.name, block.patches, layout))
        build_env = self._build_python_env(layout) if block.build_python else {}
        if block.build_python:
            steps.extend(self._build_python_steps(manifest, block, layout))
        if block.prepare is not None:
            steps.extend(self._prepare_steps(manifest, block.prepare, layout, build_env))
        steps.extend(
            build_commands(
                name=manifest.name,
                build_system=block.build_system,
                layout=layout,
                prefix=self.prefix,
                jobs=self.jobs,
                configure_args=block.configure_args,
                compiler_flags=block.compiler_flags,
                project_file=block.project_file,
                build_args=block.build_args,
                provides_install_target=block.provides_install_target,
                binaries=effective_binaries(manifest, install_block),
                autoreconf=block.autoreconf,
                build_env=build_env,
            )
        )
        if block.install_tree:
            steps.extend(
                tree_install_commands(
                    name=manifest.name,
                    source_tree=layout.src,
                    prefix=self.prefix,
                    owner=self.owner,
                )
            )
        steps.extend(self._extra_file_steps(manifest, block, layout))
        return steps

    # -- the mirrored bundle checkout (D-070, #381 Task 15) -----------------

    def _checkout_from_bundle(
        self, manifest: PackageManifest, block: GitInstall, src: Path, *, truly_offline: bool
    ) -> str:
        """Verify and check out *src* from the Bunker's mirrored bundle.

        Offline there is no fallback: whatever :func:`checkout_bundle` raises
        propagates as-is (as a :class:`BackendError`, converting a bare
        :class:`~hammunition.resolution.CatalogueMiss` from a missing
        recursive gitlink bundle into one too, so the failure is reported the
        same way every other step's failure is, rather than escaping as an
        uncaught exception the transaction log has no entry for). Online, the
        Bunker's bundle is tried first and a direct network clone from
        ``block.repo`` is the fallback (D-070's publisher-after-mirror shape,
        applied here to a verified checkout rather than a plain file).
        """
        assert self.fetcher is not None and self.context is not None  # steps() guarantees both
        try:
            return checkout_bundle(
                manifest.name,
                block,
                src,
                fetcher=self.fetcher,
                context=self.context,
                runner=self.runner,
            )
        except (BackendError, CatalogueMiss) as exc:
            if truly_offline:
                if isinstance(exc, CatalogueMiss):
                    raise BackendError(str(exc)) from exc
                raise
            prepare_tree(src)
            return self._clone_from_network(manifest, block, src)

    def _clone_from_network(self, manifest: PackageManifest, block: GitInstall, src: Path) -> str:
        """The plain network clone (init/remote/fetch/checkout/tag), run
        directly rather than through pre-built steps: whether it is needed at
        all is a run-time decision (the Bunker's bundle route failed), not a
        plan-time one."""

        def run(argv: tuple[str, ...], description: str) -> None:
            result = self.runner.run(Command(argv=argv, env=GIT_ENV, description=description))
            if not result.ok:
                raise BackendError(f"{description}: {result.stderr.strip()}")

        run(("git", "init", "--quiet", str(src)), f"Start an empty repository for {manifest.name}")
        run(
            ("git", "-C", str(src), "remote", "add", "origin", block.repo),
            f"Point it at {block.repo}",
        )
        run(
            ("git", "-C", str(src), "fetch", "--depth", "1", "origin", block.ref),
            f"Fetch {manifest.name} at {block.ref}",
        )
        run(
            ("git", "-C", str(src), "checkout", "--quiet", "FETCH_HEAD"),
            f"Check out {block.ref}",
        )
        # Checked here, before the tag is recreated or a single submodule is
        # fetched from the network: this fallback is one function rather than
        # separate steps, so the pin's own separate verify-pin Action (next
        # in the plan) would otherwise run too late to stop either (found by
        # a Codex Daybreak review, #381 Task 15 -- a re-cut tag whose
        # publisher also serves hostile submodules would have had them fetched
        # before the mismatch was ever noticed). `block.commit` is never None
        # here: steps() only reaches this fallback through `use_bundle`, which
        # requires `repo_commit` (== `block.commit` for a tag) to be set.
        self.verify_pin(src, block.ref, commit=block.commit)
        if not COMMIT_SHA.match(block.ref):
            run(
                (
                    "git",
                    "-c",
                    "tag.gpgSign=false",
                    "-c",
                    "tag.forceSignAnnotated=false",
                    "-C",
                    str(src),
                    "tag",
                    "-f",
                    block.ref,
                    "FETCH_HEAD",
                ),
                f"Recreate the {block.ref} tag for describe-based versioning",
            )
        if block.submodules:
            run(
                (
                    "git",
                    "-C",
                    str(src),
                    "submodule",
                    "update",
                    "--init",
                    "--recursive",
                    "--depth",
                    "1",
                ),
                (
                    f"Check out {manifest.name}'s submodules at the commits the pinned "
                    f"revision records (shallow, recursive)"
                ),
            )
        return (
            f"the mirrored bundle route failed; fell back to a direct network clone of "
            f"{block.repo} at {block.ref}"
        )

    # -- submodules (D-069) -------------------------------------------------

    def _submodule_steps(self, manifest: PackageManifest, src: Path) -> list[Action | Command]:
        return [
            Command(
                # Upstream CoMaps' own configure.sh runs exactly this. Shallow:
                # the spike's full-history clone was 9.7 GB, 5.6 GB of it ICU.
                argv=(
                    "git",
                    "-C",
                    str(src),
                    "submodule",
                    "update",
                    "--init",
                    "--recursive",
                    "--depth",
                    "1",
                ),
                env=GIT_ENV,
                description=(
                    f"Check out {manifest.name}'s submodules at the commits the pinned "
                    f"revision records (shallow, recursive)"
                ),
                long_running=True,
            ),
            Action(
                kind="verify-submodules",
                description=f"Confirm every {manifest.name} submodule is at its recorded commit",
                detail=f"git submodule status --recursive in {src}: each line must start with a space",
                perform=partial(self.verify_submodules, src),
            ),
        ]

    def verify_submodules(self, src: Path) -> str:
        """Read back what `git submodule update` left, and refuse anything off
        its gitlink. git exits 0 with nothing to do on a tree whose
        `.gitmodules` it could not read, so an empty answer is refused too."""
        result = self.runner.run(
            Command(
                argv=("git", "-C", str(src), "submodule", "status", "--recursive"),
                description="Read the submodules' checked-out commits",
            )
        )
        if not result.ok:
            raise BackendError(
                f"could not read the submodules in {src}: {result.stderr.strip()[:300]}"
            )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            raise BackendError(
                f"the manifest says {src} has submodules and git reports no submodule at "
                f"all; building it would fail far from the cause (D-031)"
            )
        wrong = []
        for line in lines:
            mark = line[0]
            fields = line[1:].split()
            path = fields[1] if len(fields) > 1 else line.strip()
            if mark != " ":
                wrong.append(f"  {path}: {_SUBMODULE_MARKS.get(mark, f'marked {mark!r}')}")
        if wrong:
            raise BackendError(
                f"{len(wrong)} submodule(s) are not at the commit the pinned revision "
                f"records, and git exited 0:\n" + "\n".join(wrong)
            )
        return f"{len(lines)} submodule(s), each at the commit the pinned revision records"

    # -- the build Python (D-069) -------------------------------------------

    @staticmethod
    def build_python_dir(layout: SourceLayout) -> Path:
        """Beside the tree, not in it: the tree is cleared every run and the
        venv, like cmake's build directory, need not be."""
        return layout.root / "build-python"

    def _build_python_env(self, layout: SourceLayout) -> dict[str, str]:
        venv = self.build_python_dir(layout)
        return {
            "VIRTUAL_ENV": str(venv),
            "PATH": f"{venv}/bin:{os.environ.get('PATH', os.defpath)}",
        }

    def _build_python_steps(
        self, manifest: PackageManifest, block: GitInstall, layout: SourceLayout
    ) -> list[Action | Command]:
        from .venv import write_requirements

        venv = self.build_python_dir(layout)
        requirements = layout.root / "build-python.requirements.txt"
        return [
            Action(
                kind="requirements",
                description=f"Stage the Python {manifest.name}'s build needs",
                detail=f"{len(block.build_python)} hash-pinned line(s) -> {requirements}",
                perform=partial(write_requirements, requirements, list(block.build_python)),
            ),
            Command(
                # The engine's own interpreter, resolved, as the venv backend
                # does: a venv on a removed worktree's symlink died mid-run.
                argv=(str(Path(sys.executable).resolve()), "-m", "venv", str(venv)),
                description=f"Create (or reuse) the build Python for {manifest.name}",
            ),
            Command(
                argv=(
                    str(venv / "bin" / "pip"),
                    "install",
                    "--require-hashes",
                    "--no-input",
                    "--quiet",
                    "-r",
                    str(requirements),
                ),
                description=(
                    f"Install {manifest.name}'s build Python packages, each verified against "
                    f"the manifest's sha256 pins (build-only; discarded with the build)"
                ),
                long_running=True,
            ),
        ]

    # -- the prepare script (D-069) -----------------------------------------

    def _prepare_steps(
        self,
        manifest: PackageManifest,
        prepare: PrepareStep,
        layout: SourceLayout,
        build_env: dict[str, str],
    ) -> list[Action | Command]:
        env = {
            **prepare.env,
            **build_env,
            # The script builds a helper with a bare `cmake --build`, which
            # would otherwise take every CPU regardless of memory.
            "CMAKE_BUILD_PARALLEL_LEVEL": str(self.jobs),
        }
        return [
            Command(
                argv=(f"./{prepare.script}", *prepare.args),
                description=f"Run {manifest.name}'s own {prepare.script} in the tree",
                env=env,
                cwd=layout.src,
            ),
            Action(
                kind="check-prepared",
                description=f"Confirm {prepare.script} produced what the build reads",
                detail=", ".join(prepare.produces),
                perform=partial(check_produced, layout.src, prepare),
            ),
        ]

    # -- extra files (D-069) --------------------------------------------------

    def _extra_file_steps(
        self, manifest: PackageManifest, block: GitInstall, layout: SourceLayout
    ) -> list[Action | Command]:
        # The preflight check (every pinned extra required on the Bunker;
        # an extra from the built tree names no remote pin and is not asked
        # for) runs in steps() itself, before any step -- this method's own
        # included -- is constructed. Not repeated here.
        steps: list[Action | Command] = []
        privileged = needs_root_for(self.prefix)
        for extra in block.extra_files:
            dest = self.prefix / extra.install_as
            if extra.artifact is not None:
                if self.fetcher is None:
                    raise BackendError(
                        f"{manifest.name} installs {extra.install_as} from a pinned "
                        f"download and this git backend was built without a fetcher. "
                        f"Skipping it would install a build missing a file its manifest names."
                    )
                artifact = extra.artifact
                pin = RemoteArtifact(url=artifact.url, sha256=artifact.sha256)
                source = self.fetcher.path_for(pin)
                steps.append(
                    payload_action(
                        manifest.name,
                        pin,
                        self.fetcher,
                        label=Path(extra.install_as).name,
                        expected_size=artifact.size,
                        max_bytes=artifact.size + MIB,
                    )
                )
            else:
                assert extra.from_tree is not None  # the schema requires one of the two
                source = layout.src / extra.from_tree
            steps.append(
                Command(
                    # A symlink at the destination is replaced, never written
                    # through: CoMaps' own install once left World.mwm as one.
                    argv=("rm", "-f", "--", str(dest)),
                    description=f"Remove whatever is at {dest}, a symlink included",
                    requires_root=privileged,
                )
            )
            steps.append(
                Command(
                    argv=("install", "-D", "-m", "0644", str(source), str(dest)),
                    description=f"Install {manifest.name}'s {extra.install_as}, which its install rule leaves out",
                    requires_root=privileged,
                )
            )
        return steps

    def verify_pin(self, src: Path, ref: str, *, commit: str | None = None) -> str:
        """Ask git what actually landed, and refuse anything else.

        For a SHA the comparison is exact. For a tag there is nothing to compare
        against — the point of a tag is that upstream chose it — so the resolved
        commit is *recorded* instead, which is the raw material the pin database
        is made of: the day a tag is re-cut, the log says what it used to be.
        """
        result = self.runner.run(
            Command(
                argv=("git", "-C", str(src), "rev-parse", "HEAD"),
                description="Read the checked-out revision",
            )
        )
        if not result.ok:
            raise BackendError(
                f"could not read the checked-out revision in {src}: {result.stderr.strip()}"
            )
        head = result.stdout.strip()
        if COMMIT_SHA.match(ref):
            if head != ref:
                raise BackendError(
                    f"the checkout is not the pinned revision.\n"
                    f"  pinned:  {ref}\n"
                    f"  checked out: {head}\n"
                    f"git exited 0, so nothing else in this run would have noticed. "
                    f"Building this would install a revision the catalog was not "
                    f"written against (D-024, D-031)."
                )
            return f"HEAD is {head}, matching the pin"
        if commit is not None:
            if head != commit:
                raise BackendError(
                    f"tag {ref} no longer resolves to the commit it is pinned to: the tag "
                    f"was re-cut, or the server answered with something else.\n"
                    f"  pinned:  {commit}\n"
                    f"  checked out: {head}\n"
                    f"git exited 0, so nothing else in this run would have noticed "
                    f"(D-024, D-031)."
                )
            return f"tag {ref} resolved to {head}, matching the pinned commit"
        return f"tag {ref} resolved to {head}"


def check_produced(src: Path, prepare: PrepareStep) -> str:
    """Each ``produces`` glob matches a non-empty regular file, or refuse.

    The script's exit status is not the evidence: CoMaps'
    ``generate_symbols.sh`` calls a bare ``exit`` when optipng is missing, so
    configure succeeds and the build later has no symbols to draw with.
    """
    found: list[str] = []
    missing: list[str] = []
    for pattern in prepare.produces:
        hits = [
            p
            for p in sorted(src.glob(pattern))
            if p.is_file() and not p.is_symlink() and p.stat().st_size > 0
        ]
        (found if hits else missing).append(pattern)
    if missing:
        raise BackendError(
            f"{prepare.script} exited 0 and produced no non-empty file for "
            f"{', '.join(missing)} in {src}. A tool it needs is missing (CoMaps' symbol "
            f"generation stops silently without optipng); check the block's build_depends."
        )
    return f"produced {', '.join(found)}"
