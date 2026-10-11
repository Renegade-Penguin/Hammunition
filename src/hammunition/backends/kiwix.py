# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Kiwix books backend: the station's chosen books.  D-066.

The books come from station config, resolved against the catalog's book list
and its generated pins (:func:`hammunition.kiwix.resolve_books`) before this
backend is built, because the plan has to print each one's size and licence
before anything is confirmed (D-049 rule 2). The fetch step's description is
that disclosure: *Fetch reference book ID (DATE, SIZE, LICENCE)*.

Each book is fetched into the shared cache, verified against its pinned
sha256 and exact size, and copied into
``<prefix>/share/hammunition/data/<unit>/<file>.zim``, re-hashed on the way
in and never through a symlink (:class:`~hammunition.backends.verified.PrefixWriter`).
The cached download is then deleted, as its own disclosed step: a book can be
50 GB, and keeping a second copy of it in the cache would double that for the
sake of a re-install that the installed copy already answers.

With a LAN mirror in station config (D-070), each book is asked of
``<mirror>/<unit>/<book id>`` first and of download.kiwix.org second, the
same pinned sha256 and size checked either way, and the step records which
answered (:func:`book_mirror_path`). Books are the largest data the catalog
fetches, so they are the mirror's most valuable case (issue #159).

A book whose pinned file is already installed at its pinned size is not
fetched again: the file name carries the date, and the size is exact. A
``.zim`` in the directory that no chosen book's pin names -- a book dropped
from station config, or an older date of one still chosen -- is removed as
its own ``remove-data`` step.

No ``library.xml`` is written here. Building one runs ``kiwix-manage``, which
parses the downloaded file; that belongs to the reader, run as the operator
by ``hammunition reference serve``, not to a data unit under root (D-057's
line for a parser of downloaded data).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from ..attributed import PublisherChecks, recheck_installed
from ..fetch import Fetcher, MirrorPath, fetch_disclosure, record_fetch
from ..kiwix import BookFile, KiwixError, load_book_list, load_pin_file, resolve_books
from ..manifest.schema import KiwixBooksInstall, PackageManifest, RemoteArtifact
from ..progress import run_checks
from ..resolution import CatalogueMiss, ResolutionContext
from ..retry import OnOutage, PublisherUnavailable, hint_for
from .base import Action, BackendError, Command, CommandRunner
from .data import human_size
from .regions import data_root, device_at, free_bytes_at
from .source import needs_root_for
from .verified import PrefixWriter

MIB = 1024 * 1024
ZIM = ".zim"


def book_mirror_path(unit: str, book: BookFile) -> MirrorPath:
    """Where a LAN mirror serves this book (D-070): ``<unit>/<book id>``, the
    id as the pin file names it and as ``hammunition artifacts`` lists it.
    The id, not the dated file name, so a mirror keeps one path per book
    across Kiwix's republications; a stale copy there fails the pin's sha256
    and the publisher is asked instead."""
    return MirrorPath(unit, book.pin.id)


def require_book(book: BookFile, unit: str, context: ResolutionContext) -> None:
    """The Bunker's record for *book* agrees with the repository's own pin.

    The pin is the repository's (size, then sha256); the record is never
    compared with itself. A miss defers the whole book selection."""
    name = book_mirror_path(unit, book).name
    context.require_payload(unit, name, size=book.pin.size)
    context.require_payload(unit, name, sha256=book.pin.sha256)


def book_current(dest: Path, book: BookFile) -> bool:
    """Whether *dest* holds this book's pinned file: a regular file at its exact size."""
    return dest.is_file() and not dest.is_symlink() and dest.stat().st_size == book.pin.size


def books_disk_needs(pending: Sequence[BookFile], *, cache: Path, prefix: Path) -> dict[Path, int]:
    """Each download once in the fetch cache and once under the prefix: both
    exist between the install and the cache prune."""
    total = sum(f.pin.size for f in pending)
    return {cache: total, prefix: total} if total else {}


def books_shortfall(
    needs: Mapping[Path, int],
    others: Mapping[Path, int] | None = None,
    *,
    free_at: Callable[[Path], int] = free_bytes_at,
    device_of: Callable[[Path], int] = device_at,
) -> str | None:
    """A refusal naming both numbers for every file system short of room for
    the books, counting the same run's map data (*others*) on it too."""
    merged: dict[Path, int] = dict(others or {})
    for path, amount in needs.items():
        merged[path] = merged.get(path, 0) + amount
    by_device: dict[int, tuple[list[Path], int]] = {}
    for path, amount in merged.items():
        paths, total = by_device.get(device_of(path), ([], 0))
        by_device[device_of(path)] = ([*paths, path], total + amount)
    short: list[str] = []
    for paths, need in by_device.values():
        free = free_at(paths[0])
        if free < need:
            short.append(
                f"{', '.join(str(p) for p in paths)}: {human_size(need)} ({need} bytes) is "
                f"needed and {human_size(free)} ({free} bytes) is free"
            )
    if not short:
        return None
    return (
        "not enough disk space for the reference books (each counted twice: the verified "
        "download in the cache and the installed copy; any map data in the same run is "
        "counted too):\n  " + "\n  ".join(short)
    )


@dataclass(frozen=True)
class KiwixBooksBackend:
    """Turns the chosen books into fetch, install, prune and remove steps."""

    fetcher: Fetcher
    prefix: Path
    files: Sequence[BookFile]
    runner: CommandRunner | None = None
    """Escalates the copy into a root-owned prefix when the engine is not root."""
    keep_unlisted: bool = False
    """Remove nothing this run: a book or map was deferred because its publisher
    is not answering (#200), and the installed file it would have replaced
    must not be removed with nothing arriving in its place."""
    provenance: Mapping[tuple[str, str], str] = field(default_factory=dict)
    """What the Bunker's catalogue answered for (unit, book id): said on the fetch line."""
    method = "kiwix-books"

    def data_dir(self, manifest: PackageManifest) -> Path:
        return data_root(self.prefix) / manifest.name

    @property
    def writer(self) -> PrefixWriter:
        return PrefixWriter(privileged=needs_root_for(self.prefix), runner=self.runner)

    def pending(self, manifest: PackageManifest) -> list[BookFile]:
        """The chosen books not installed at their pin: what this run downloads."""
        out = self.data_dir(manifest)
        return [f for f in self.files if not book_current(out / f.pin.file, f)]

    def steps(self, manifest: PackageManifest, block: KiwixBooksInstall) -> list[Action | Command]:
        del block  # nothing in it varies the steps; the books come from station config
        out = self.data_dir(manifest)
        writer = self.writer
        steps: list[Action | Command] = []
        for book in self.pending(manifest):
            fetched: dict[str, Path] = {}
            facts: dict[str, str] = {}
            pin = book.pin
            dest = out / pin.file
            where = book_mirror_path(manifest.name, book)
            note, urls, sources = fetch_disclosure(self.fetcher, pin.url, where, "sha256")
            steps.append(
                Action(
                    kind="fetch",
                    description=(
                        f"Fetch reference book {pin.id} ({pin.published}, "
                        f"{human_size(pin.size)}, {book.book.licence}){note}"
                        + (
                            f"; {self.provenance[(manifest.name, pin.id)]}"
                            if (manifest.name, pin.id) in self.provenance
                            else ""
                        )
                    ),
                    detail=f"{urls} (sha256 {pin.sha256[:12]}…, {pin.size} bytes)",
                    perform=partial(self._fetch, book, fetched, where, facts),
                    sources=sources,
                    facts=facts,
                )
            )
            steps.append(
                Action(
                    kind="install-data",
                    description=f"Install reference book {pin.file}",
                    # The destination, verbatim, for uninstall's attribution replay.
                    detail=str(dest),
                    perform=partial(self._install, book, fetched, dest, writer),
                    requires_root=writer.privileged,
                    facts={"size": str(pin.size), "digest": pin.sha256},
                )
            )
            cached = self.fetcher.path_for(RemoteArtifact(url=pin.url, sha256=pin.sha256))
            steps.append(
                Action(
                    kind="prune-cache",
                    description=f"Delete the cached download of {pin.id} once it is installed",
                    detail=str(cached),
                    perform=partial(self._prune, fetched, dest),
                )
            )
        wanted = {f.pin.file for f in self.files}
        if out.is_dir() and not self.keep_unlisted:
            for path in sorted(out.glob(f"*{ZIM}")):
                if path.name in wanted or not path.is_file():
                    continue
                steps.append(
                    Action(
                        kind="remove-data",
                        description=f"Remove {path.name}: no longer among your reference books",
                        # The path, verbatim: uninstall stops attributing it.
                        detail=str(path),
                        perform=partial(_remove, writer, path),
                        requires_root=writer.privileged,
                    )
                )
        return steps

    def _fetch(
        self,
        book: BookFile,
        fetched: dict[str, Path],
        where: MirrorPath,
        facts: dict[str, str],
    ) -> str:
        pin = book.pin
        # The cap is raised to the pinned size plus a margin, never removed.
        result = self.fetcher.fetch(
            RemoteArtifact(url=pin.url, sha256=pin.sha256), max_bytes=pin.size + MIB, mirror=where
        )
        source = record_fetch(result, facts, mirrored=bool(self.fetcher.mirror))
        if result.size != pin.size:
            raise BackendError(
                f"{pin.url}: the pin says {pin.size} bytes and {result.size} arrived; the "
                f"digest matched, so the pin's size is wrong -- regenerate the pins with "
                f"scripts/gen_kiwix_pins.py"
            )
        fetched["path"] = result.path
        how = "cached" if result.from_cache else "downloaded"
        return (
            f"{how} {result.size} bytes, sha256 {result.sha256[:12]}… verified against the "
            f"pin{source}"
        )

    def _install(
        self, book: BookFile, fetched: dict[str, Path], dest: Path, writer: PrefixWriter
    ) -> str:
        path = fetched.get("path")
        if path is None:  # pragma: no cover
            raise BackendError(f"{book.pin.id} was not fetched before its install step")
        # Copy, never move: the cache is content-addressed and shared.
        writer.install_verified(path, dest, algorithm="sha256", digest=book.pin.sha256)
        return f"installed {dest} ({human_size(book.pin.size)}, mode 0644, sha256 re-verified)"

    def _prune(self, fetched: dict[str, Path], dest: Path) -> str:
        path = fetched.get("path")
        if path is None or not dest.is_file():  # pragma: no cover - a failed step stops the run
            return "kept: the book did not install"
        path.unlink(missing_ok=True)
        return f"deleted {path}"


def _remove(writer: PrefixWriter, path: Path) -> str:
    writer.remove([path])
    return f"removed {path}"


def resolve_station_books(
    selection: Sequence[str],
    catalog_root: Path,
    *,
    installed: Path,
    head: Callable[[str], int],
    on_outage: OnOutage | None = None,
    checks: PublisherChecks | None = None,
    context: ResolutionContext | None = None,
    unit: str | None = None,
) -> list[BookFile]:
    """The chosen books as pinned files, checked before the plan prints.

    A book installed at its pin asks nothing of the network. Every other one
    is asked for once, by ``HEAD``: a pin Kiwix has dropped (it keeps two
    dated files per book) refuses the plan here, naming the regeneration,
    rather than failing a fetch after apt has run; so does a book that
    cannot be reached, offline. Every such book is named together.

    A publisher that did not answer after the retries (#200) goes to
    *on_outage* and that book is left out of the returned list, so the rest
    of the books install; without it, it is refused with the others.

    With *checks* (#197), an installed book the log attributes is not asked
    again until the attribution is a week old; a book that is asked and no
    longer served is a note in the plan, never a refusal.

    With a *context* (#381), a book the publisher cannot be asked about is
    answered by the Bunker's record, checked against the repository's own pin
    (:func:`require_book`); *unit* (the catalogue key) is then required. A book
    the Bunker cannot answer for, while a Bunker is verified, defers the whole
    selection (:class:`~hammunition.resolution.CatalogueMiss`). Offline the
    publisher is never asked, and a book installed at its pin needs no record
    and no recheck.
    """
    if context is not None and unit is None:
        raise ValueError("resolve_station_books needs the unit name when given a context")
    books = resolve_books(selection, load_book_list(catalog_root), load_pin_file(catalog_root))
    problems: list[str] = []
    unavailable: set[str] = set()

    def still_served(book: BookFile) -> None:
        status = head(book.pin.url)
        if status != 200:
            raise KiwixError(f"{book.pin.url} answered HTTP {status}, not 200")

    if context is None or not context.offline:
        recheck_installed(
            checks,
            installed.name,
            [b for b in books if book_current(installed / b.pin.file, b)],
            name=lambda book: book.pin.id,
            path=lambda book: installed / book.pin.file,
            check=still_served,
            digest=lambda book: book.pin.sha256,
            label="installed Kiwix books against download.kiwix.org",
        )
    todo = [b for b in books if not book_current(installed / b.pin.file, b)]

    def served(book: BookFile) -> int:
        if context is None or unit is None:
            return head(book.pin.url)

        def recorded() -> int:
            require_book(book, unit, context)
            return 200

        return context.choose(
            unit, book_mirror_path(unit, book).name, lambda: head(book.pin.url), recorded
        )

    outcomes = run_checks(todo, served, label="Kiwix books against download.kiwix.org")
    for book, outcome in zip(todo, outcomes, strict=True):
        try:
            status = outcome.get()
        except CatalogueMiss:
            raise
        except PublisherUnavailable as exc:
            if context is not None and context.verified is not None:
                raise CatalogueMiss(f"{book.pin.id}: no complete book set: {exc}") from exc
            if on_outage is None:
                problems.append(f"  {book.pin.id}: {exc}")
            else:
                on_outage(book.pin.id, exc)
                unavailable.add(book.pin.id)
            continue
        except KiwixError as exc:
            problems.append(f"  {book.pin.id}: {exc}")
            continue
        if status in (404, 410):
            problems.append(
                f"  {book.pin.id}: {book.pin.url} answered HTTP {status}. Kiwix keeps the two "
                f"newest dated files of a book, and this pin is older; regenerate the pins "
                f"with scripts/gen_kiwix_pins.py. The newer file is not taken in its place: "
                f"nobody has measured its sha256."
            )
        elif status != 200:
            problems.append(f"  {book.pin.id}: {book.pin.url} answered HTTP {status}, not 200")
    if problems:
        raise KiwixError(
            f"{len(problems)} reference book(s) cannot be fetched and are not installed "
            f"already:\n" + "\n".join(problems) + hint_for(problems)
        )
    return [b for b in books if b.pin.id not in unavailable]
