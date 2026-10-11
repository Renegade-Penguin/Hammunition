# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The node backend — Node.js applications built from a verified archive (D-037).

One measured user: ``openhamclock``, a Node and Vite web application whose
releases publish no binary (source-build-gaps #7). Q-016 asked whether a
build that fetches its dependency closure from registry.npmjs.org is
acceptable on this project's security posture; the maintainer's answer was
yes, with two conditions this module and the planner enforce between them:

- **Disclosed as a requirement.** The plan names the Node floor, the version
  the distribution offers, and the registry fetch — before anything runs.
- **Refused when Node is absent or too old.** At plan time, by name, from
  ``apt-cache policy`` on the distribution's own ``nodejs``. Node is never
  fetched: no NodeSource, no ``nvm``, no tarball from nodejs.org.

The build itself is three ``npm`` invocations, every one with lifecycle
scripts ignored, so no third-party package ever runs code on this machine
during the build — the closure is *data* until the operator starts the
application. What ``npm ci`` fetches is verified against the sha512
``integrity`` pins in ``package-lock.json``, and that file arrives inside the
sha256-verified source archive, so the whole closure is transitively pinned
from the one hash in the manifest. The backend checks the lock file really
carries those pins rather than assuming it does.

The pruned runtime tree installs per-user under
``$XDG_DATA_HOME/hammunition/node/<name>`` and a wrapper on the operator's
PATH runs ``node <entry>`` from it, bound to ``127.0.0.1``. Nothing needs
root.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition.backends.base import Action, BackendError, Command
from hammunition.backends.source import SourceLayout, extract, patch_steps
from hammunition.manifest.schema import NodeInstall, PackageManifest, RemoteArtifact
from hammunition.payloads import payload_action, payload_cached, preflight_payloads

if TYPE_CHECKING:
    # Type-only: `hammunition.backends/__init__.py` imports this module
    # eagerly, and `hammunition.fetch` imports `hammunition.backends.base`,
    # so a module-level import here is the other half of #158's cycle.
    # `Fetcher` is only ever used in an annotation, which `from __future__
    # import annotations` defers, so it never needs a real import.
    from hammunition.fetch import Fetcher
    from hammunition.resolution import ResolutionContext

__all__ = ["NodeBackend", "check_lockfile", "install_tree", "write_wrapper"]

#: Every npm step runs with these. ``--ignore-scripts`` is the security
#: property; the other two stop npm phoning the registry for advisories and a
#: funding banner — neither is verification, and both are network traffic the
#: plan did not disclose.
NPM_FLAGS = ("--ignore-scripts", "--no-audit", "--no-fund")

#: npm checks the registry for a newer npm on every run unless told not to.
#: Not disclosed in the plan, so not done.
NPM_ENV = {"npm_config_update_notifier": "false"}

#: The one environment value a manifest cannot override (D-037): a dashboard
#: that listens on every interface is not an acceptable default on a machine
#: that also holds security tooling. It is the engine's *default*, not a
#: guarantee — an application that reads its own config file over the
#: environment (openhamclock's ``.env`` does) can be told otherwise by the
#: operator editing that file, and that is theirs to do. See the D-037
#: amendment.
LOOPBACK = {"HOST": "127.0.0.1"}


def check_lockfile(tree: Path) -> str:
    """Confirm ``package-lock.json`` pins every fetched package by digest.

    ``npm ci`` refuses to run without a lock file, so its absence would fail
    the next step anyway — but the *reason* this backend is acceptable at
    all is that the lock file carries an ``integrity`` for everything it
    resolves, and that is a property to check, not to assume. A lockfile
    whose entries name a ``resolved`` URL with no ``integrity`` would let npm
    fetch unverified, and this step refuses it by name.
    """
    lock = tree / "package-lock.json"
    if not lock.is_file():
        raise BackendError(
            f"{tree} has no package-lock.json; without one npm ci cannot run and "
            f"nothing pins the dependency closure — this archive is not one the "
            f"node backend can build"
        )
    try:
        data = json.loads(lock.read_text())
    except (OSError, ValueError) as exc:
        raise BackendError(f"{lock} is not readable as JSON: {exc}") from exc
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise BackendError(
            f"{lock} has no `packages` map — lockfileVersion 1 is not supported; "
            f"regenerate upstream's lock with npm >= 7"
        )
    pinned = 0
    unpinned: list[str] = []
    for path, entry in packages.items():
        if not path or not isinstance(entry, dict):
            continue  # the root package itself
        if entry.get("link"):
            continue  # a workspace symlink, nothing fetched
        if entry.get("integrity"):
            pinned += 1
        elif entry.get("resolved"):
            unpinned.append(path)
    if unpinned:
        shown = ", ".join(unpinned[:3])
        raise BackendError(
            f"{lock} resolves {len(unpinned)} package(s) with no integrity digest "
            f"({shown}{', ...' if len(unpinned) > 3 else ''}); npm would fetch them "
            f"unverified, which the checksum rule forbids"
        )
    return f"{pinned} package(s) pinned by sha512 integrity in {lock.name}"


def install_tree(
    source: Path, destination: Path, *, entry: str, build_output: str | None, preserve: list[str]
) -> str:
    """Copy the pruned tree into place, keeping the files the app wrote for itself.

    Checked before and after (D-031): the build output must exist in the
    source before anything is copied, and the entry script must exist in the
    destination after. A ``cp`` that succeeded at copying an unbuilt tree
    would install a dashboard with nothing to serve.
    """
    if build_output is not None:
        built = source / build_output
        if not built.is_dir() or not any(built.iterdir()):
            raise BackendError(
                f"the build exited 0 but produced no {build_output}/ in {source} — "
                f"nothing to install"
            )
    if not (source / entry).is_file():
        raise BackendError(f"{source} has no {entry} to run; the manifest's entry is wrong")

    kept: dict[str, bytes] = {}
    for rel in preserve:
        path = destination / rel
        if path.is_file():
            kept[rel] = path.read_bytes()
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # symlinks=True: node_modules/.bin is relative symlinks into the tree,
    # and dereferencing them would copy each target twice.
    shutil.copytree(source, destination, symlinks=True)
    for rel, data in kept.items():
        (destination / rel).write_bytes(data)

    if not (destination / entry).is_file():
        raise BackendError(f"copied {source} to {destination} but {entry} is not there")
    restored = f", kept {', '.join(sorted(kept))}" if kept else ""
    return f"installed {destination}{restored}"


def write_wrapper(path: Path, *, tree: Path, entry: str, env: dict[str, str], name: str) -> str:
    """A wrapper on the operator's PATH that runs the app from its tree.

    Carries the same generated-by marker the launcher module writes, so an
    uninstall reads it back and removes only what is ours. ``exec`` so the
    server is the wrapper's own process — a Ctrl-C reaches it.
    """
    assignments = " ".join(f"{k}={shlex.quote(v)}" for k, v in {**env, **LOOPBACK}.items())
    body = (
        "#!/bin/sh\n"
        f"# generated by hammunition for {name}\n"
        f"cd {shlex.quote(str(tree))} || exit 1\n"
        f'{assignments} exec node {shlex.quote(entry)} "$@"\n'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    # Semgrep: a deliberate mode (0755/0644 on installed files and launchers, 0700 private); nothing group- or world-writable.
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    os.chmod(path, 0o755)
    return f"wrote {path} -> node {entry} in {tree}, bound to 127.0.0.1"


class NodeBackend:
    """Plans node installs. Steps only — the runner executes them."""

    def __init__(
        self,
        *,
        fetcher: Fetcher,
        build_root: Path,
        node_root: Path,
        bin_dir: Path,
        context: ResolutionContext | None = None,
    ) -> None:
        self.context = context
        self.fetcher = fetcher
        self.build_root = build_root
        self.node_root = node_root
        self.bin_dir = bin_dir

    def tree_for(self, manifest: PackageManifest) -> Path:
        return self.node_root / manifest.name

    def wrapper_for(self, manifest: PackageManifest, block: NodeInstall) -> Path:
        return self.bin_dir / (block.command or manifest.name)

    def steps(self, manifest: PackageManifest, block: NodeInstall) -> list[Action | Command]:
        artifact = block.artifact
        # The source tarball only: a Node runtime is never fetched (D-037).
        preflight_payloads(
            manifest.name,
            ((artifact, None),),
            context=self.context,
            cached=payload_cached(self.fetcher),
        )
        layout = SourceLayout(root=self.build_root / f"{manifest.name}-{artifact.sha256[:8]}")
        src = layout.src
        tree = self.tree_for(manifest)
        wrapper = self.wrapper_for(manifest, block)
        fetcher = self.fetcher

        steps: list[Action | Command] = [
            payload_action(manifest.name, artifact, fetcher, label="source archive"),
            Action(
                kind="extract",
                description=f"Unpack the {manifest.name} source",
                detail=f"{fetcher.path_for(artifact)} -> {src}",
                perform=partial(_extract, fetcher, artifact, src),
            ),
            # Before the lock-file check and before npm touches the tree: a
            # patch that fails to apply stops the run with nothing fetched.
            *patch_steps(manifest.name, block.patches, layout),
            Action(
                kind="lockfile",
                description=(
                    f"Confirm {manifest.name}'s package-lock.json pins every dependency "
                    f"by digest before npm fetches any"
                ),
                detail=f"{src / 'package-lock.json'}: every resolved package needs an integrity field",
                perform=partial(check_lockfile, src),
            ),
            Command(
                argv=("npm", "ci", *NPM_FLAGS),
                description=(
                    f"Fetch {manifest.name}'s dependency closure from registry.npmjs.org, "
                    f"each package verified against the lock file; no package scripts run"
                ),
                cwd=src,
                env=NPM_ENV,
                long_running=True,
            ),
        ]
        if block.build_script is not None:
            steps.append(
                Command(
                    argv=("npm", "run", block.build_script, *NPM_FLAGS),
                    description=(
                        f"Build {manifest.name} (npm run {block.build_script}); "
                        f"pre/post scripts are not run"
                    ),
                    cwd=src,
                    env=NPM_ENV,
                    long_running=True,
                )
            )
        steps.extend(
            [
                Command(
                    argv=("npm", "prune", "--omit=dev", *NPM_FLAGS),
                    description=f"Drop {manifest.name}'s build-only packages from the runtime tree",
                    cwd=src,
                    env=NPM_ENV,
                ),
                Action(
                    kind="install-tree",
                    description=f"Install {manifest.name}'s runtime tree for this operator",
                    detail=f"{src} -> {tree}"
                    + (
                        f", keeping {', '.join(block.preserve)} if present"
                        if block.preserve
                        else ""
                    ),
                    perform=partial(
                        install_tree,
                        src,
                        tree,
                        entry=block.entry,
                        build_output=block.build_output,
                        preserve=list(block.preserve),
                    ),
                ),
                Action(
                    kind="wrapper",
                    description=f"Put {wrapper.name} on the operator's PATH, bound to 127.0.0.1",
                    detail=f"{wrapper} runs node {block.entry} in {tree}",
                    perform=partial(
                        write_wrapper,
                        wrapper,
                        tree=tree,
                        entry=block.entry,
                        env=dict(block.env),
                        name=manifest.name,
                    ),
                ),
            ]
        )
        return steps


def _extract(fetcher: Fetcher, artifact: RemoteArtifact, dest: Path) -> str:
    return extract(fetcher.path_for(artifact), dest)
