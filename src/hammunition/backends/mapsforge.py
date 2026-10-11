# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``mapsforge-map`` and ``mapsforge-poi`` converters: phone files per region.  D-067.

Both run the archive's osmosis with a Mapsforge writer on its classpath. The
map writer is the archive's own (``libmapsforge-java`` 0.20.0); the POI writer
is not packaged anywhere, so ``mapsforge-poi`` fetches Maven Central's
``mapsforge-poi-writer`` jar-with-dependencies, pinned by a sha256 Hammunition
measured (the block's ``tool``), and installs it under
``<prefix>/share/hammunition/mapsforge-poi/``.

Debian's ``/usr/bin/osmosis`` does not load either writer: its classworlds
file loads ``/usr/share/osmosis/*.jar``, and the writers live in
``/usr/share/java/``, so ``--mapfile-writer`` fails with "Task type
mapfile-writer doesn't exist" (spike, 2026-09-29). The engine therefore runs
``java -cp`` over osmosis's jars and the named jars the spike's scripts used
(:data:`MAP_JARS`, :data:`POI_JARS`), with osmosis's own main class. No
system file is changed. The classpath is resolved when the conversion runs,
after apt, never at plan time, where on a fresh machine none of it exists
yet; a jar that is missing fails the region by name before ``java`` starts.

Each region takes two steps, as :mod:`hammunition.backends.garmin`'s do.
**convert**, as the operator in ``<staging>/<slug>.work/`` under its lock:
``java`` writes ``<slug>.map`` or ``<slug>.poi`` there, with
``java.io.tmpdir`` (and, for the POI writer, ``org.sqlite.tmpdir``) pointed at
the same directory, so the writer's ``type=hd`` scratch and the native
library sqlite-jdbc unpacks at run time land in staging, where they are
counted and cleared, not in ``/tmp``. The effect is checked, not the exit
status (D-031): the output must be non-empty and start with its format's
magic, read by the operator's own ``head -c`` and compared by exit status. **install**, as root only
where the prefix needs it: the file is published into
``<data>/<unit>/<slug>.<ext>`` re-verified against the operator's digest,
with a ``.source`` sidecar holding the snapshot and the converter, and the
working directory is emptied. A failed build empties it too.

A failure is recorded in the :class:`PhoneLedger`, the other regions
continue, and the ledger's step, last in the transaction, fails it by name.
"""

from __future__ import annotations

import dataclasses
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..fetch import Fetcher, signature_gap
from ..geofabrik import PINNED, RegionFile
from ..manifest.schema import ConverterTool, DerivedDataInstall, PackageManifest
from ..payloads import payload_action, preflight_payloads
from .base import Action, BackendError, Command, CommandRunner
from .data import human_size
from .regions import (
    PBF,
    SOURCE,
    MapLedger,
    data_root,
    installed_converter,
    installed_snapshot,
    prefix_writer,
    removal_steps,
)
from .staging import REFUSED, Staging
from .verified import PrefixWriter, digest_of

if TYPE_CHECKING:
    # Type-only: a module-level import of `hammunition.resolution` is not
    # known to be free of #158's cycle, and the field only ever needs an
    # annotation, which `from __future__ import annotations` defers.
    from ..resolution import ResolutionContext

Kind = Literal["map", "poi"]

#: The explicit size check's cap above the manifest's declared bytes, as the
#: shared payload fetch's other call sites use (D-070, #381 Task 13).
MIB = 1024 * 1024

#: osmosis's own entry point, the class ``/usr/bin/osmosis`` starts.
MAIN = "org.openstreetmap.osmosis.core.Osmosis"
#: The heap both runs were measured with (spike, 2026-09-29): the map writer
#: peaked at 1.04 GB resident, the POI writer at 0.47 GB, on one region.
HEAP = "-Xmx2g"
HEAP_NEED = "needs about 2 GB of memory for Java"
OSMOSIS_DIR = Path("/usr/share/osmosis")
JAVA_DIR = Path("/usr/share/java")
#: ``/usr/share/java/<name>.jar`` after osmosis's own jars, for the map writer:
#: the list the spike's ``mfw.sh`` ran with, which reported none missing.
MAP_JARS: tuple[str, ...] = (
    "commons-codec",
    "commons-compress",
    "commons-csv",
    "commons-dbcp",
    "commons-io",
    "commons-logging",
    "commons-pool",
    "guava",
    "jpf",
    "osmpbf",
    "protobuf",
    "spring3-beans",
    "spring3-core",
    "spring3-jdbc",
    "spring3-transaction",
    "mapsforge-core",
    "mapsforge-map",
    "mapsforge-map-reader",
    "mapsforge-map-writer",
    "mapsforge-poi",
    "jts-core",
    "kxml2",
    "trove-3",
    "sqlite-jdbc",
)
#: The same for the POI writer, before the pinned jar (the spike's ``poiw.sh``).
POI_JARS: tuple[str, ...] = (
    "commons-codec",
    "commons-compress",
    "commons-io",
    "commons-logging",
    "guava",
    "jpf",
    "osmpbf",
    "protobuf",
)
#: What each output starts with: the Mapsforge header, and SQLite's.
MAGIC: dict[Kind, str] = {"map": "mapsforge binary OSM", "poi": "SQLite format 3"}
WHAT: dict[Kind, str] = {"map": "Mapsforge map", "poi": "Mapsforge POI file"}
TASK: dict[Kind, str] = {"map": "--mapfile-writer", "poi": "--poi-writer"}

MEASURED = "measured on one region"
#: ``.map`` against its ``.osm.pbf``: 17,127,551 / 22,139,742 bytes, 0.774.
MAP_FACTOR = 0.78
#: The map run's blocks written (GNU time's file-system outputs), output
#: included: 318 MB against the 22 MB download, 14.4, rounded up. A temporary
#: file deleted before it reached disk would not add to that count, so it is
#: the best measurement there is, not a bound.
MAP_SCRATCH_FACTOR = 15
#: ``.poi`` against its ``.osm.pbf``: 4,546,560 / 22,139,742 bytes, 0.205.
POI_FACTOR = 0.21
#: The POI run's blocks written, output included: 9.4 MB, 0.43, rounded up.
POI_SCRATCH_FACTOR = 0.5
FACTOR: dict[Kind, float] = {"map": MAP_FACTOR, "poi": POI_FACTOR}
SCRATCH: dict[Kind, float] = {"map": MAP_SCRATCH_FACTOR, "poi": POI_SCRATCH_FACTOR}
#: The converter recorded in each output's ``.source`` sidecar. Bumped when
#: the argv or the writer changes the output; the POI line also carries the
#: pinned jar's digest, so a new pin rebuilds every file.
CONVERTER: dict[Kind, str] = {"map": "mapsforge-map 1", "poi": "mapsforge-poi 1"}

PHONE_NOTE = (
    f"the phone maps at {MAP_FACTOR}x each download with about {MAP_SCRATCH_FACTOR}x of "
    f"scratch, and the POI files at {POI_FACTOR}x with about {POI_SCRATCH_FACTOR}x, {MEASURED}"
)


def estimate(kind: Kind, size: int) -> int:
    return round(size * FACTOR[kind])


def classpath(
    kind: Kind, osmosis_dir: Path, java_dir: Path, tool: Path | None
) -> tuple[list[Path], list[str]]:
    """(the classpath, what is missing from it). osmosis's jars first, as its
    own launcher loads them, then the named jars, then the pinned tool."""
    missing: list[str] = []
    osmosis = sorted(osmosis_dir.glob("*.jar")) if osmosis_dir.is_dir() else []
    if not osmosis:
        missing.append(f"osmosis's jars in {osmosis_dir}")
    named = [java_dir / f"{name}.jar" for name in (MAP_JARS if kind == "map" else POI_JARS)]
    missing.extend(str(p) for p in named if not p.is_file())
    tail: list[Path] = []
    if kind == "poi":
        if tool is None or not tool.is_file():
            missing.append(f"the POI writer {tool}")
        else:
            tail = [tool]
    return [*osmosis, *named, *tail], missing


def java_argv(kind: Kind, pbf: Path, out: Path, work: Path, cp: Sequence[Path]) -> list[str]:
    """The fixed argv of one run. Nothing in it comes from a manifest."""
    props = [f"-Djava.io.tmpdir={work}"]
    if kind == "poi":
        props.append(f"-Dorg.sqlite.tmpdir={work}")
    tail = ["type=hd"] if kind == "map" else []
    return [
        "java",
        HEAP,
        *props,
        "-cp",
        ":".join(str(p) for p in cp),
        MAIN,
        "-q",
        "--rbf",
        f"file={pbf}",
        TASK[kind],
        f"file={out}",
        *tail,
    ]


def _tail(text: str) -> str:
    return text.strip()[-300:]


@dataclass
class PhoneLedger:
    """Which phone map or POI file failed this run, keyed by unit and region."""

    failed: dict[str, str] = field(default_factory=dict)

    def fail(self, key: str, message: str) -> str:
        self.failed.setdefault(key, message)
        return f"FAILED, the rest continues: {message}"

    def check(self) -> str:
        if not self.failed:
            return "every phone map and POI file installed"
        lines = "\n".join(f"  {message}" for message in self.failed.values())
        raise BackendError(
            f"{len(self.failed)} phone map(s) or POI file(s) did not install; "
            f"everything else did:\n{lines}"
        )

    def step(self) -> Action:
        return Action(
            kind="check-phone-maps",
            description="Fail the transaction by name if any phone map or POI file did not install",
            detail="phone maps",
            perform=self.check,
        )


@dataclass(frozen=True)
class MapsforgeConverter:
    """Turns a ``derived`` block with ``converter: mapsforge-map`` or
    ``mapsforge-poi`` into steps."""

    kind: Kind
    prefix: Path
    files: Sequence[RegionFile]
    staging: Staging
    fetcher: Fetcher | None = None
    """Fetches the pinned POI writer; the map converter needs none."""
    keep: frozenset[str] = frozenset()
    regions: MapLedger = field(default_factory=MapLedger)
    """Piece 1's ledger, read only: a region that did not install is not built."""
    ledger: PhoneLedger = field(default_factory=PhoneLedger)
    runner: CommandRunner | None = None
    euid: int | None = None
    privileged: bool | None = None
    osmosis_dir: Path = OSMOSIS_DIR
    java_dir: Path = JAVA_DIR
    context: ResolutionContext | None = None
    """The run's resolution context: offline, the pinned POI writer is
    refused before any build step exists unless the Bunker can answer for it."""

    @property
    def suffix(self) -> str:
        return f".{self.kind}"

    @property
    def writer(self) -> PrefixWriter:
        return prefix_writer(self.prefix, self.privileged, self.runner, self.euid)

    def data_dir(self, manifest: PackageManifest) -> Path:
        return data_root(self.prefix) / manifest.name

    def tool_path(self, manifest: PackageManifest, block: DerivedDataInstall) -> Path | None:
        """Where the pinned tool is installed: the unit's own directory beside
        the data tree, never inside it (D-049: data is never executed)."""
        if block.tool is None:
            return None
        return self.prefix / "share" / "hammunition" / manifest.name / block.tool.file_name

    def tool_current(self, manifest: PackageManifest, block: DerivedDataInstall) -> bool:
        path = self.tool_path(manifest, block)
        if path is None or block.tool is None:
            return True
        try:
            return path.is_file() and digest_of(path) == block.tool.artifact.sha256
        except (OSError, BackendError):
            return False

    def converter_id(self, block: DerivedDataInstall) -> str:
        if block.tool is None:
            return CONVERTER[self.kind]
        return f"{CONVERTER[self.kind]} {block.tool.artifact.sha256[:12]}"

    def _current(self, dest: Path, region: RegionFile, block: DerivedDataInstall) -> bool:
        return (
            dest.is_file()
            and installed_snapshot(dest) == region.snapshot
            and installed_converter(dest) == self.converter_id(block)
        )

    def pending(self, manifest: PackageManifest, block: DerivedDataInstall) -> list[RegionFile]:
        """Regions this run builds: no file, or one from another snapshot or converter."""
        out = self.data_dir(manifest)
        return [
            f for f in self.files if not self._current(out / f"{f.slug}{self.suffix}", f, block)
        ]

    def steps(self, manifest: PackageManifest, block: DerivedDataInstall) -> list[Action | Command]:
        out = self.data_dir(manifest)
        source_dir = data_root(self.prefix) / block.source
        writer = self.writer
        steps: list[Action | Command] = []
        tool = self.tool_path(manifest, block)
        if block.tool is not None and tool is not None and not self.tool_current(manifest, block):
            steps.extend(self._tool_steps(manifest, block.tool, tool))
        for region in self.files:
            dest = out / f"{region.slug}{self.suffix}"
            if self._current(dest, region, block):
                continue
            pbf = source_dir / f"{region.slug}{PBF}"
            work = self.staging.workdir(region.slug)
            staged = work / f"{region.slug}{self.suffix}"
            key = f"{manifest.name}:{region.slug}"
            built: dict[str, str] = {}
            shown = java_argv(self.kind, pbf, staged, work, [Path("<classpath>")])
            where = (
                f"osmosis's jars, {len(MAP_JARS if self.kind == 'map' else POI_JARS)} jars "
                f"from {self.java_dir}" + (f" and {tool}" if tool is not None else "")
            )
            steps.append(
                Action(
                    kind="convert",
                    description=(
                        f"Build the {WHAT[self.kind]} of {region.region} ({region.snapshot}) "
                        f"for phones, as the operator, in {work}: {shlex.join(shown)}, the "
                        f"classpath being {where}; {HEAP_NEED}; output about "
                        f"{human_size(estimate(self.kind, region.size))} "
                        f"({FACTOR[self.kind]}x the download) and about "
                        f"{human_size(round(SCRATCH[self.kind] * region.size))} of scratch "
                        f"while it runs ({MEASURED})"
                    ),
                    detail=str(work),
                    perform=partial(self._convert, region, key, pbf, work, staged, tool, built),
                )
            )
            steps.append(
                Action(
                    kind="install-data",
                    description=(
                        f"Install the {WHAT[self.kind]} of {region.region} ({region.snapshot}), "
                        f"then clear its scratch {work}"
                    ),
                    # The destination, verbatim, for uninstall's attribution replay.
                    detail=str(dest),
                    perform=partial(
                        self._install, region, key, work, staged, dest, built, writer, block
                    ),
                    requires_root=writer.privileged,
                )
            )
        keep = {f.slug for f in self.files} | set(self.keep)
        steps.extend(removal_steps(out, self.suffix, keep, writer))
        return steps

    def _tool_steps(
        self, manifest: PackageManifest, tool: ConverterTool, dest: Path
    ) -> list[Action | Command]:
        if self.fetcher is None:
            raise BackendError(f"{manifest.name}: the POI writer has no fetcher in this run")
        if self.fetcher.offline and (self.context is None or not self.context.offline):
            # preflight_payloads silently does nothing with no context, or
            # with one that disagrees and says online (it treats either the
            # same as "online"), so an offline run must refuse here itself
            # rather than rely on that call to catch either mismatch.
            raise BackendError(
                f"{manifest.name}: offline, the pinned POI writer needs the Bunker's "
                f"verified catalogue to check before any step runs, and this run has "
                f"none. Nothing was planned."
            )
        # Offline, the Bunker is required to answer for the pinned writer
        # before any step of this unit exists (D-070, #381 Task 13).
        preflight_payloads(manifest.name, ((tool.artifact, tool.size),), context=self.context)
        fetched: dict[str, Path] = {}
        gap = signature_gap(tool.artifact)
        fetch = payload_action(
            manifest.name,
            tool.artifact,
            self.fetcher,
            label="the Mapsforge POI writer",
            expected_size=tool.size,
            max_bytes=tool.size + MIB,
            fetched=fetched,
        )
        # The mirror routing and the explicit size check come from the
        # shared helper; the licence and signature-gap sentence are this
        # unit's own disclosure and survive on top of it.
        fetch = dataclasses.replace(
            fetch,
            description=(
                f"Fetch the Mapsforge POI writer for {manifest.name} "
                f"({human_size(tool.size)}, {tool.licence}), checked by "
                f"{PINNED}"
                + (f"; its signature is recorded, and {manifest.name} {gap}" if gap else "")
            ),
        )
        return [
            fetch,
            Action(
                kind="install-data",
                description=f"Install the Mapsforge POI writer as {dest} (mode 0644)",
                # The destination, verbatim, for uninstall's attribution replay.
                detail=str(dest),
                perform=partial(self._install_tool, tool, fetched, dest),
                requires_root=self.writer.privileged,
            ),
        ]

    def _install_tool(self, tool: ConverterTool, fetched: dict[str, Path], dest: Path) -> str:
        path = fetched.get("path")
        if path is None:  # pragma: no cover - the fetch Action always runs first
            raise BackendError("the POI writer was not fetched before the install step")
        self.writer.install_verified(path, dest, algorithm="sha256", digest=tool.artifact.sha256)
        return f"installed {dest} ({human_size(tool.size)}, mode 0644, sha256 re-verified)"

    def _convert(
        self,
        region: RegionFile,
        key: str,
        pbf: Path,
        work: Path,
        staged: Path,
        tool: Path | None,
        built: dict[str, str],
    ) -> str:
        if region.slug in self.regions.failed:
            return f"skipped: {region.region} did not install"
        if not pbf.is_file():
            return self.ledger.fail(key, f"{region.region}: {pbf} is not installed")
        refusal = self.staging.prepare(work)
        if refusal is not None:
            return self.ledger.fail(key, f"{region.region}: {refusal}")
        cleared = self.staging.clear(work)  # a failed run's leftovers, under the lock
        if cleared.returncode != 0:
            why = "refused" if cleared.returncode == REFUSED else "failed"
            return self.ledger.fail(
                key, f"{region.region}: clearing {work} {why}: {_tail(cleared.stderr)}"
            )
        cp, missing = classpath(self.kind, self.osmosis_dir, self.java_dir, tool)
        if missing:
            return self.ledger.fail(
                key,
                f"{region.region}: java was not started, because the classpath needs "
                f"{', '.join(missing)}; the archive's osmosis and libmapsforge-java install "
                f"them with their dependencies (`hammunition install {key.split(':')[0]}`)",
            )
        made = self.staging.run(java_argv(self.kind, pbf, staged, work, cp), cwd=work)
        if made.returncode == REFUSED:
            return self.ledger.fail(
                key, f"{region.region}: java was not started: {_tail(made.stderr) or 'refused'}"
            )
        digest = self.staging.digest(staged)
        if made.returncode != 0 or digest is None:
            return self.ledger.fail(
                key,
                f"{region.region}: java wrote no {WHAT[self.kind]} from {pbf.name} "
                f"(exit {made.returncode}): {_tail(made.stderr or made.stdout)}"
                f"{self._scratch(work, '; ')}",
            )
        magic = MAGIC[self.kind]
        if not self._starts_with(staged, work, magic):
            return self.ledger.fail(
                key,
                f"{region.region}: {staged.name} is not a {WHAT[self.kind]} (it does not "
                f"start with {magic!r}), though java exited {made.returncode}"
                f"{self._scratch(work, '; ')}",
            )
        built["sha256"] = digest
        who = self.staging.who()
        return (
            f"built the {WHAT[self.kind]} of {region.region}{' ' + who if who else ''} "
            f"(staged, {staged})"
        )

    def _starts_with(self, path: Path, work: Path, magic: str) -> bool:
        """Whether *path* starts with *magic*, read by the operator's own
        ``head`` and compared as hex by ``od`` in the same shell, so only the
        exit status is read: nothing is decoded, whatever bytes java wrote,
        and ``flock --verbose``'s lines on stdout, in whatever language the
        locale gives them, are never compared (review, 2026-09-30)."""
        want = magic.encode("ascii").hex()
        check = self.staging.run(
            [
                "sh",
                "-c",
                'test "$(head -c "$1" -- "$2" | od -An -v -tx1 | tr -d " \\n")" = "$3"',
                "sh",
                str(len(magic)),
                str(path),
                want,
            ],
            cwd=work,
        )
        return check.returncode == 0

    def _install(
        self,
        region: RegionFile,
        key: str,
        work: Path,
        staged: Path,
        dest: Path,
        built: dict[str, str],
        writer: PrefixWriter,
        block: DerivedDataInstall,
    ) -> str:
        if "sha256" not in built:
            return f"skipped: the {WHAT[self.kind]} of {region.region} was not built"
        failure = None
        try:
            self.staging.publish(staged, dest, digest=built["sha256"], writer=writer)
            writer.write_text(
                dest.with_name(dest.name + SOURCE),
                f"{region.snapshot}\nconverter: {self.converter_id(block)}\n",
            )
        except (BackendError, OSError) as exc:
            failure = str(exc)
        scratch = self._scratch(work, "")
        if failure is not None:
            return self.ledger.fail(
                key, f"{region.region}: {failure}{'; ' + scratch if scratch else ''}"
            )
        if scratch:
            return self.ledger.fail(key, f"{region.region}: installed {dest}, but {scratch}")
        return f"installed {dest}; cleared {work}"

    def _scratch(self, work: Path, lead: str) -> str:
        """Clear *work* under its lock: "" when it cleared, else *lead* and why not."""
        cleared: subprocess.CompletedProcess[str] = self.staging.clear(work)
        if cleared.returncode == 0:
            return ""
        return f"{lead}its scratch {work} was not cleared: {_tail(cleared.stderr) or 'no reason given'}"
