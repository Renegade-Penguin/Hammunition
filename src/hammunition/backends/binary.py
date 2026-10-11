# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Installing something upstream already built.  D-004, D-014.

Measured, not assumed: **eight units in the catalog's dispositions wait on
this and nothing else** — QtTermTCP, QtSoundModem and Pi-APRS from D-008's
packet core, GARIM and ARDOPGUI, AntScope2 and GridTracker2 from AHRL, and
`sdrangel` on the five targets that do not package it. None is available from
apt on any of our six targets, which was checked rather than assumed. That is
the largest single group of units blocked on one missing backend, which is why
it is the one that got written.

Three formats, and the differences between them are the whole design.

**`deb` goes through apt, not dpkg.** ``apt-get install ./file.deb`` resolves
the package's dependencies; ``dpkg -i`` installs it and leaves them broken,
which is the classic way to wedge a machine with a vendor package. Using apt
also means the result is an ordinary installed package that ``apt`` knows
about, so removing it later is `apt remove` rather than archaeology.

**`tarball` and `zip` unpack and then install what the manifest names.** The
archive's own layout is upstream's business; `binaries` says which files matter
and what they should be called. This reuses the extraction the source backend
already does — same tar filter, same zip screening, same one-top-level-directory
rule — because a prebuilt archive is exactly as hostile as a source one.

**`executable` is a single file.** Fetch, verify, chmod, install.

**Nothing here is unverified.** :class:`~hammunition.manifest.schema.RemoteArtifact`
makes `sha256` mandatory and :mod:`hammunition.fetch` refuses a mismatch, so a
vendor binary that changed under us fails closed rather than installing. That
matters more here than for a source build: nobody is going to read a .deb.

**AppImage is deliberately not implemented.** `SCOPE.md` puts it post-1.0 and
its two consumers, HAMRS and Reticulum MeshChat, are both post-1.0 units. It is
refused by name so the gap stays visible (D-014).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hammunition import netiso
from hammunition.manifest.schema import (
    BinaryInstall,
    InstallBlock,
    PackageManifest,
    effective_binaries,
)
from hammunition.payloads import payload_action, payload_cached, preflight_payloads

from .base import Action, BackendError, Command, CommandRunner
from .placements import helper_steps, placement_steps
from .source import (
    SourceLayout,
    extract,
    install_binary_commands,
    needs_root_for,
    prepare_tree,
    tree_install_commands,
)

if TYPE_CHECKING:
    # Type-only: `hammunition.backends/__init__.py` imports this module
    # eagerly, and `hammunition.fetch` imports `hammunition.backends.base`,
    # so a module-level import here is the other half of #158's cycle.
    # `Fetcher` is only ever used in an annotation, which `from __future__
    # import annotations` defers, so it never needs a real import.
    from hammunition.fetch import Fetcher
    from hammunition.manifest.schema import RemoteArtifact
    from hammunition.resolution import ResolutionContext

__all__ = ["IMPLEMENTED_BINARY_FORMATS", "BinaryBackend"]

#: What this backend can install. `appimage` is a measured, deliberate absence.
IMPLEMENTED_BINARY_FORMATS = frozenset({"deb", "tarball", "zip", "executable"})

_ARCHIVE_FORMATS = frozenset({"tarball", "zip"})


@dataclass(frozen=True)
class BinaryBackend:
    """Turns a ``binary`` install block into the steps that install it."""

    fetcher: Fetcher
    runner: CommandRunner
    """Used by the .deb path, which shells out to apt. Going through the runner
    rather than :mod:`subprocess` keeps the one seam every other command uses."""

    build_root: Path
    """Archives unpack here. Named for the source backend's directory because it
    is the same directory: a prebuilt tree and a built one are both scratch."""

    prefix: Path

    owner: str | None = None
    """The operator an installed tree is handed to (D-043); None keeps it root's."""

    context: ResolutionContext | None = None
    """The run's resolution context: offline, an artifact the Bunker cannot
    answer for is refused before any extraction or install step exists."""

    dependency_check: Callable[[Path], list[str]] | None = None
    """Offline only: names the dependency groups of a fetched vendor .deb that no
    suitable installed package meets (version included). apt would have to fetch
    them from an archive the machine cannot reach, so the install stops first."""

    isolation: str | None = None
    """``netiso.BWRAP`` when the bwrap sandbox runs here, probed once at plan
    time; offline a vendor .deb installs inside it (filesystem writable, but
    /run, /tmp and the operator's home hidden) so its maintainer scripts and
    triggers have no network and no pathname socket to bridge through; with
    no working bwrap the install is refused."""

    recommends_of: Callable[[Path], str] | None = None
    """Offline only: the Recommends field of a vendor .deb that is local, as text,
    so the plan can say which ones apt will NOT install (it runs with
    ``--no-install-recommends``)."""

    attributed_files: frozenset[str] = frozenset()
    """The files the transaction log shows this engine installed, so a unit
    that installs the tray's device helper can tell its own earlier copy from
    another installer's (``devctl_helper``)."""

    helper_interpreter: str | None = None
    """The interpreter the helper's wrapper runs as root; None is the one
    running the engine."""

    method = "binary"

    def layout(self, manifest: PackageManifest, block: BinaryInstall) -> SourceLayout:
        """Where an archive unpacks. Pure — touches no disk.

        Keyed by the artifact digest, so a changed artifact unpacks somewhere
        new instead of over the previous one's files.
        """
        return SourceLayout(self.build_root / f"{manifest.name}-{block.artifact.sha256[:12]}")

    def steps(
        self, manifest: PackageManifest, install_block: InstallBlock
    ) -> list[Action | Command]:
        """The steps for one resolved block.

        Takes the whole :class:`InstallBlock`, not just its method, because the
        block may carry its own `binaries` overriding the manifest's: rayhunter
        unpacks a different per-platform path on each architecture (issue #69).
        """
        block = install_block.install
        if not isinstance(block, BinaryInstall):
            raise BackendError(
                f"{manifest.name} resolved to a {block.method} block and this backend "
                f"installs prebuilt artifacts. Installing it anyway would install "
                f"something the plan never named."
            )
        binaries = effective_binaries(manifest, install_block)
        if block.format not in IMPLEMENTED_BINARY_FORMATS:
            raise BackendError(
                f"{manifest.name} is a {block.format!r} artifact and this backend does "
                f"not install one. Implemented: {', '.join(sorted(IMPLEMENTED_BINARY_FORMATS))}. "
                f"AppImage is post-1.0 (SCOPE.md) and is refused by name rather than "
                f"quietly skipped."
            )

        preflight_payloads(
            manifest.name,
            ((block.artifact, None),),
            context=self.context,
            cached=payload_cached(self.fetcher),
        )
        fetched: dict[str, Path] = {}
        steps: list[Action | Command] = [
            payload_action(
                manifest.name, block.artifact, self.fetcher, label="artifact", fetched=fetched
            )
        ]

        if block.format == "deb":
            offline = self.context is not None and self.context.offline
            if offline and self.isolation != netiso.BWRAP:
                raise BackendError(self._no_sandbox(manifest.name))
            local = payload_cached(self.fetcher)(block.artifact) if offline else False
            if offline and self.dependency_check is not None:
                steps.append(
                    self._dependency_action(
                        manifest.name,
                        fetched,
                        self.dependency_check,
                        local=local,
                        recommends_of=self.recommends_of,
                    )
                )
            # The path is not knowable until the fetch has run, so the install
            # is an Action that builds its own command rather than a Command
            # rendered at plan time. The plan still names the URL and digest
            # above, which is what an operator needs to see.
            steps.append(
                Action(
                    kind="install-deb",
                    description=f"Install {manifest.name} from the downloaded .deb",
                    detail=self._install_detail(block.artifact, offline=offline, local=local),
                    perform=lambda: self._install_deb(manifest.name, fetched),
                    requires_root=True,
                )
            )
            return steps

        if block.format in _ARCHIVE_FORMATS:
            layout = self.layout(manifest, block)
            spreads = bool(block.placements or block.devctl_helper)
            if not binaries and not block.install_tree and not spreads:
                raise BackendError(
                    f"{manifest.name} is a prebuilt archive and names no `binaries`, "
                    f"no `install_tree`, no `placements` and no `devctl_helper`. Unpacking it would leave a directory in "
                    f"a cache and install nothing — a run that reports success having "
                    f"done nothing."
                )
            steps.append(
                Action(
                    kind="prepare",
                    description=f"Clear any previous {manifest.name} unpack",
                    detail=f"{layout.src} (removed if present, then recreated)",
                    perform=lambda: prepare_tree(layout.src),
                )
            )
            steps.append(
                Action(
                    kind="extract",
                    description=f"Unpack {manifest.name}",
                    detail=f"into {layout.src}",
                    perform=lambda: extract(fetched["path"], layout.src),
                )
            )
            steps.extend(
                install_binary_commands(
                    name=manifest.name,
                    produced_in=layout.src,
                    prefix=self.prefix,
                    binaries=binaries,
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
            steps.extend(
                placement_steps(
                    name=manifest.name,
                    placements=block.placements,
                    src=layout.src,
                    prefix=self.prefix,
                )
            )
            if block.devctl_helper is not None:
                from hammunition.devctl_helper import plan_helper

                steps.extend(
                    helper_steps(
                        name=manifest.name,
                        helper=block.devctl_helper,
                        plan=plan_helper(
                            block.devctl_helper,
                            attributed_files=self.attributed_files,
                            interpreter=self.helper_interpreter,
                        ),
                        src=layout.src,
                        staging=layout.src.parent / f"{manifest.name}-devctl-staging",
                        prefix=self.prefix,
                        archive=lambda: fetched["path"],
                    )
                )
            return steps

        if block.install_tree:
            # D-076: one file that is not a program (GraphHopper's jar) lands in
            # the unit's tree under its marker, mode 0644, by the commands every
            # tree uses, so uninstall and the effect check need nothing new.
            if binaries:
                raise BackendError(
                    f"{manifest.name} installs its one file as a tree; `binaries` beside "
                    f"it would install the same file twice, once as a program"
                )
            marker = block.tree_marker
            assert marker is not None  # the schema requires it with install_tree
            layout = self.layout(manifest, block)
            staged = layout.src / marker
            steps.append(
                Action(
                    kind="prepare",
                    description=f"Clear any previous {manifest.name} staging",
                    detail=f"{layout.src} (removed if present, then recreated)",
                    perform=lambda: prepare_tree(layout.src),
                )
            )
            steps.append(
                Action(
                    kind="stage",
                    description=f"Stage {manifest.name} as {marker} (mode 0644)",
                    detail=str(staged),
                    perform=lambda: self._stage_file(fetched, staged),
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

        # `executable`: one file, installed under the name the manifest gives it.
        if len(binaries) != 1:
            raise BackendError(
                f"{manifest.name} is a single prebuilt executable, so it must declare "
                f"exactly one `binaries` entry saying what to call it; it declares "
                f"{len(binaries)}."
            )
        target = self.prefix / "bin" / binaries[0].install_as
        steps.append(
            Action(
                kind="install-binary",
                description=f"Install {manifest.name} as {target}",
                # The detail is the destination path, verbatim: action_end
                # records it, and uninstall's file-attribution replay reads
                # it back — an Action leaves no argv for the replay to parse.
                detail=str(target),
                perform=lambda: self._install_executable(fetched, target),
                requires_root=needs_root_for(self.prefix),
            )
        )
        return steps

    @staticmethod
    def _no_sandbox(name: str) -> str:
        return (
            f"{name}: an offline vendor .deb install runs its maintainer scripts and "
            f"triggers under apt, and needs the bwrap sandbox (no network, private /run, "
            f"/tmp and the operator's home) to keep them off the host network, and this "
            f"machine has none that works. Nothing was planned."
        )

    def _install_detail(self, artifact: RemoteArtifact, *, offline: bool, local: bool) -> str:
        if not offline:
            return "apt-get install on the file, so its dependencies resolve"
        detail = (
            "apt-get install on the file under bwrap (no network, private /run, /tmp and "
            "the operator's home, so its maintainer scripts and triggers get no network and "
            "no local socket to bridge through) with --no-install-recommends --no-download, "
            "so apt can fetch nothing; "
        )
        if self.recommends_of is None:
            return detail + "its Recommends are NOT installed offline"
        if not local:
            return (
                detail + "its Recommends are NOT installed offline, and which ones is read "
                "after the fetch (the package is not local yet)"
            )
        try:
            recommends = self.recommends_of(self.fetcher.path_for(artifact))
        except OSError as exc:
            return detail + f"its Recommends are NOT installed offline (could not be read: {exc})"
        if not recommends:
            return detail + "the package has no Recommends"
        return detail + f"Recommends NOT installed offline: {recommends}"

    @staticmethod
    def _dependency_action(
        name: str,
        fetched: dict[str, Path],
        check: Callable[[Path], list[str]],
        *,
        local: bool,
        recommends_of: Callable[[Path], str] | None,
    ) -> Action:
        def perform() -> str:
            path = fetched.get("path")
            if path is None:  # pragma: no cover - the fetch Action always runs first
                raise BackendError(f"{name}: the .deb was not fetched before its dependency check")
            unmet = check(path)
            if unmet:
                raise BackendError(
                    f"offline: {name}'s .deb is installed by apt, which would fetch what it "
                    f"depends on, and this machine lacks: {', '.join(unmet)}. Nothing was "
                    f"installed."
                )
            outcome = "every dependency is met by an installed package"
            if recommends_of is not None:
                recommends = recommends_of(path)
                if recommends:
                    outcome += f"; Recommends not installed: {recommends}"
            return outcome

        when = (
            ""
            if local
            else (
                ", checked after the fetch (the package is not local to read yet); "
                "nothing is installed if a dependency is missing"
            )
        )
        return Action(
            kind="check-deb-depends",
            description=(
                f"Check that {name}'s dependencies are installed (offline: apt cannot fetch "
                f"them){when}"
            ),
            detail=f"the .deb's Depends against the installed packages, versions included{when}",
            perform=perform,
        )

    def _install_deb(self, name: str, fetched: dict[str, Path]) -> str:
        """Hand the file to apt so its dependencies resolve. Offline, apt may
        neither install Recommends nor download anything."""
        path = fetched.get("path")
        if path is None:  # pragma: no cover - the fetch Action always runs first
            raise BackendError(f"{name}: the .deb was not fetched before the install step")
        offline = self.context is not None and self.context.offline
        if offline and self.isolation != netiso.BWRAP:
            raise BackendError(self._no_sandbox(name))
        apt_argv = (
            "apt-get",
            "-o",
            "Acquire::Retries=3",
            "install",
            "--yes",
            "--no-remove",
            *(("--no-install-recommends", "--no-download") if offline else ()),
            "--",
            str(path),
        )
        result = self.runner.run(
            Command(
                argv=netiso.privileged_sandbox(apt_argv) if offline else apt_argv,
                description=f"Install {name} from {path.name}",
                env={"DEBIAN_FRONTEND": "noninteractive"},
                requires_root=True,
            )
        )
        if not result.ok:
            raise BackendError(
                f"apt could not install {path.name}: {result.stderr.strip()[:400]}\n"
                f"A vendor .deb built for a different release is the usual cause, and "
                f"apt refusing it is the correct outcome — dpkg would have installed it "
                f"and left the dependencies broken."
            )
        return f"installed {path.name} through apt"

    @staticmethod
    def _stage_file(fetched: dict[str, Path], staged: Path) -> str:
        path = fetched.get("path")
        if path is None:  # pragma: no cover - the fetch Action always runs first
            raise BackendError("the file was not fetched before the staging step")
        # Copied, never moved: the fetch cache is content-addressed and shared.
        shutil.copyfile(path, staged)
        staged.chmod(0o644)
        return f"staged {staged} (mode 0644)"

    def _install_executable(self, fetched: dict[str, Path], target: Path) -> str:
        path = fetched.get("path")
        if path is None:  # pragma: no cover
            raise BackendError("the executable was not fetched before the install step")
        # `install -D` copies (the fetch cache is content-addressed and shared;
        # moving out of it would make the next run re-download), creates the
        # parent, and sets the mode -- and it runs through the runner, which
        # elevates it for a root-owned prefix. The in-process copy this
        # replaced wrote /usr/local/bin/paracon as the operator and failed
        # with EACCES 165 steps into the field laptop's first full install
        # (2026-09-12); every other install form already went through a
        # privileged command.
        result = self.runner.run(
            Command(
                argv=("install", "-D", "-m", "0755", str(path), str(target)),
                description=f"Install {target.name} as {target}",
                requires_root=needs_root_for(self.prefix),
            )
        )
        if not result.ok:
            raise BackendError(f"could not install {target}: {result.stderr.strip()[:400]}")
        return f"installed {target} (mode 0755)"
