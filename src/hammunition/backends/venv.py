# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The venv backend — per-user Python installs, hash-pinned end to end.

The last backend the 1.0 measurement requires (D-014, re-measured 2026-08-30:
pipx and CPAN fell to zero users; venv kept three — not1mm, nanovna-saver,
and the radiosonde_auto_rx REVIVE that waits on it by design. nanovna-saver
went apt on 2026-09-06 once the archive turned out to carry it everywhere;
supersdr had arrived by then, so the count is still three).

What AHRL does for these units is a generated bash script that builds a venv
in the operator's home and pip-installs an unpinned name from PyPI. Two
things change here, both of them this project's standing rules rather than
taste:

- **Everything is pinned and hashed.** The manifest carries the full
  dependency tree as requirements lines with ``--hash=sha256:`` pins, and pip
  runs with ``--require-hashes`` — the checksum rule for non-apt sources,
  applied to PyPI. The schema refuses an unhashed line before a plan exists.
- **Nothing needs root.** The venv lives in the operator's XDG data dir, the
  wrapper in ``~/.local/bin``, and every step runs unprivileged — the
  privilege rule with zero exceptions, because nothing here touches the
  system.

The venv is *data*, not cache: launchers point into it, so it lives under
``$XDG_DATA_HOME/hammunition/venvs/<name>`` rather than the build root.
Idempotency is the cheap kind for now: ``python3 -m venv`` over an existing
venv is a no-op, and a fully-hashed ``pip install`` over a satisfied venv
verifies and exits — the run is safe to repeat, though not yet free (the
same already-installed-at-pin gap the git backend records).
"""

from __future__ import annotations

import os
import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition.backends.base import Action, Command
from hammunition.backends.source import SourceLayout, extract, tree_install_commands
from hammunition.manifest.schema import PackageManifest, RemoteArtifact, VenvInstall
from hammunition.payloads import payload_action, payload_cached, preflight_payloads

if TYPE_CHECKING:
    # Type-only: `hammunition.backends/__init__.py` imports this module
    # eagerly, and `hammunition.fetch` imports `hammunition.backends.base`,
    # so a module-level import here is the other half of #158's cycle.
    # `Fetcher` is only ever used in an annotation, which `from __future__
    # import annotations` defers, so it never needs a real import.
    from hammunition.fetch import Fetcher
    from hammunition.resolution import ResolutionContext

__all__ = ["VenvBackend"]


def write_requirements(path: Path, lines: list[str]) -> str:
    """Stage the manifest's pinned requirements as a file pip can read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return f"wrote {len(lines)} pinned requirement line(s) to {path}"


def write_wrapper(path: Path, target: Path) -> str:
    """A two-line exec wrapper onto the operator's PATH.

    Generated rather than symlinked: a symlink into a venv makes some
    entry-point loaders resolve ``sys.prefix`` to the link's home and miss the
    venv's packages; ``exec`` of the venv's own script never does.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n')
    # Semgrep: a deliberate mode (0755/0644 on installed files and launchers, 0700 private); nothing group- or world-writable.
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    os.chmod(path, 0o755)
    return f"wrote {path} -> {target}"


def _licence_clause(block: VenvInstall) -> str:
    """The licence, stated on the plan line that installs the venv (D-033), or
    nothing for a block that declares none."""
    if block.licence is None:
        return ""
    return f"; licence: {block.licence} ({block.licence_url})"


class VenvBackend:
    """Plans venv installs. Steps only — the runner executes them.

    ``fetcher``/``build_root``/``prefix`` exist for the payload half of the
    hybrid (source-build-gaps #9): a verified archive extracted and
    tree-installed beside the venv that runs it. A backend built without
    them refuses a payload manifest by name rather than planning half an
    install.
    """

    def __init__(
        self,
        *,
        venv_root: Path,
        bin_dir: Path,
        fetcher: Fetcher | None = None,
        build_root: Path | None = None,
        prefix: Path = Path("/usr/local"),
        owner: str | None = None,
        context: ResolutionContext | None = None,
    ) -> None:
        self.context = context
        self.venv_root = venv_root
        self.bin_dir = bin_dir
        self.fetcher = fetcher
        #: The operator a payload tree is handed to (D-043); None keeps it root's.
        self.owner = owner
        self.build_root = build_root
        self.prefix = prefix

    def steps(self, manifest: PackageManifest, block: VenvInstall) -> list[Action | Command]:
        if block.payload is not None:
            # Before the venv, build and pip steps exist: pip's own dependencies
            # are not mirrored here, only the payload tree this block pins.
            self._preflight_payload(manifest, block.payload)
        venv = self.venv_root / manifest.name
        requirements = self.venv_root / f"{manifest.name}.requirements.txt"
        pip = venv / "bin" / "pip"

        steps: list[Action | Command] = [
            Action(
                kind="requirements",
                description=f"Stage {manifest.name}'s pinned requirements",
                detail=f"{len(block.requirements)} hash-pinned line(s) -> {requirements}",
                perform=partial(write_requirements, requirements, list(block.requirements)),
            ),
            Command(
                # The engine's own interpreter, never bare `python3`. The
                # engine requires Python >= 3.11, so sys.executable is
                # guaranteed to clear that bar; bare `python3` is the system
                # one, which on Ubuntu/Pop 22.04 is 3.10 — and a unit venv
                # built on 3.10 cannot install a modern hash-pinned tree
                # (numpy 2.5 dropped 3.10). Found deploying to a Pop 22.04
                # laptop, where nanovna-saver's numpy pin had no 3.10 wheel.
                # Resolved, not the engine venv's own symlink: on the field
                # laptop (2026-09-12) the engine ran from a worktree's .venv
                # that was removed mid-transaction, and 118 steps later this
                # spawned `<gone>/.venv/bin/python3 -m venv` and the run died.
                # The symlink pointed at /usr/bin/python3.13 the whole time.
                argv=(str(Path(sys.executable).resolve()), "-m", "venv", str(venv)),
                description=f"Create (or reuse) {manifest.name}'s virtualenv",
                requires_root=False,
            ),
            Command(
                argv=(
                    str(pip),
                    "install",
                    "--require-hashes",
                    "--no-input",
                    "--quiet",
                    "-r",
                    str(requirements),
                ),
                description=(
                    f"Install {manifest.name} into its venv — every wheel verified "
                    f"against the manifest's sha256 pins{_licence_clause(block)}"
                ),
                requires_root=False,
                env=dict(block.env),
                long_running=True,
            ),
        ]
        if block.payload is not None:
            steps.extend(self._payload_steps(manifest, block))
        for script in block.expose:
            wrapper = self.bin_dir / script
            target = venv / "bin" / script
            steps.append(
                Action(
                    kind="wrapper",
                    description=f"Put {script} on the operator's PATH",
                    detail=f"{wrapper} execs {target}",
                    perform=partial(write_wrapper, wrapper, target),
                )
            )
        return steps

    def _preflight_payload(self, manifest: PackageManifest, payload: RemoteArtifact) -> None:
        """The existing payload presence check, then the Bunker's answer for it."""
        from hammunition.backends.base import BackendError

        if self.fetcher is None or self.build_root is None:
            raise BackendError(
                f"{manifest.name} declares a venv payload and this backend was "
                f"built without a fetcher/build root. Skipping it would install "
                f"a venv that runs nothing."
            )
        preflight_payloads(
            manifest.name,
            ((payload, None),),
            context=self.context,
            cached=payload_cached(self.fetcher),
        )

    def _payload_steps(
        self, manifest: PackageManifest, block: VenvInstall
    ) -> list[Action | Command]:
        from hammunition.backends.base import BackendError

        payload = block.payload
        assert payload is not None
        if self.fetcher is None or self.build_root is None:
            raise BackendError(
                f"{manifest.name} declares a venv payload and this backend was "
                f"built without a fetcher/build root. Skipping it would install "
                f"a venv that runs nothing."
            )
        layout = SourceLayout(root=self.build_root / f"{manifest.name}-{payload.sha256[:8]}")
        fetcher = self.fetcher
        steps: list[Action | Command] = [
            payload_action(manifest.name, payload, fetcher, label="payload tree"),
            Action(
                kind="extract",
                description=f"Unpack the {manifest.name} payload",
                detail=f"{fetcher.path_for(payload)} -> {layout.src}",
                perform=partial(_extract_payload, fetcher, payload, layout.src),
            ),
        ]
        if block.payload_build_script:
            # Run from the script's own directory: upstream build scripts
            # address their tree relative to themselves (auto_rx/build.sh
            # invokes `python3 -m autorx.version`, which only resolves from
            # auto_rx/ — measured on the first Debian run, 2026-08-30).
            script = Path(block.payload_build_script)
            steps.append(
                Command(
                    argv=("sh", script.name),
                    description=f"Run {manifest.name}'s payload build ({block.payload_build_script})",
                    cwd=layout.src / script.parent,
                )
            )
        steps.extend(
            tree_install_commands(
                name=manifest.name,
                source_tree=layout.src,
                prefix=self.prefix,
                owner=self.owner,
            )
        )
        return steps


def _extract_payload(fetcher: Fetcher, payload: RemoteArtifact, dest: Path) -> str:
    return extract(fetcher.path_for(payload), dest)
