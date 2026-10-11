# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fetching a remote artifact, and refusing to hand back one that is not what
the manifest said it would be.

CLAUDE.md, Security requirements: *"Verify checksums/signatures for any non-apt
source; refuse to install if absent."* The schema already makes the absent case
unrepresentable — :class:`~hammunition.manifest.schema.RemoteArtifact` requires
``sha256`` — so this module's job is the other half: that a file which fails
verification never becomes usable by anything downstream.

**Content-addressed by the expected digest.** A verified artifact lands at
``<cache>/<sha256>-<name>``. The path encodes the expectation, so a file sitting
at that path can only ever be content that matched it. There is no metadata
sidecar to fall out of step with the file, and two manifests naming the same
bytes share one entry.

**Nothing unverified is ever at the final path.** The download streams to a
temporary file in the same directory, is hashed as it is written, and is moved
into place with :func:`os.replace` only after the digest matches. A mismatch
deletes the temporary file and raises. This ordering is the whole point: a
verify-after-install, or a verify that leaves the bad file behind for a later
run to find, would satisfy the letter of the requirement and none of it.

**A cached file is re-verified every time, not trusted for having been
verified once.** Re-hashing costs a disk read and catches a cache that was
corrupted, truncated by a full disk, or edited between runs.

**The network is a seam.** :class:`Transport` is the only thing here that
touches it, so the tests inject bytes rather than reaching the internet — and
the suite blocks non-loopback sockets to keep that honest rather than merely
intended.

Two things this deliberately does not do. It does not shell out to ``curl`` or
``wget``: an argv is not a shell, but a redirect-following downloader that
writes where it is told is a larger surface than :mod:`urllib` with the file
handle in our hands. And it does not verify signatures yet —
``signature_url``/``signing_key_fingerprint`` are carried in the schema and are
not read here, so a manifest supplying them gets no more checking than one that
does not. That gap is named rather than papered over; see
:func:`signature_gap`.

**One second, deliberately weaker path exists** (:meth:`Fetcher.fetch_md5`,
D-057, maintainer-approved 2026-09-27): OpenStreetMap region extracts that the
catalog carries no sha256 pin for, verified instead against the publisher's
(Geofabrik's) MD5 plus the size its server reported. The sha256 path above is
unchanged by its existence -- ``fetch()`` still refuses anything that is not
digest-pinned. ``fetch_md5`` is a separate method, named for what it is, and
every plan that uses it says so beside the region.

**A LAN mirror may be asked first** (D-070). With ``mirror`` set, a fetch
given a :class:`MirrorPath` tries ``<mirror>/<unit>/<name>`` and, on any
failure there -- unreachable, an HTTP error, the size cap, a wrong size, a
wrong digest -- discards what the mirror sent and tries the publisher. The
digest checked is the same either way; the mirror is trusted for speed,
never for content. :class:`FetchResult` says which source the bytes came
from, and a mirror that did not answer at all is not asked again by the
same fetcher, so a run of ninety tiles pays one timeout, not ninety.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import shutil
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from hammunition.backends.base import BackendError
from hammunition.manifest.schema import RemoteArtifact
from hammunition.paths import (
    OperatorDirError,
    artifact_cache_dir,
    ensure_operator_dir,
    open_operator_dir,
)
from hammunition.s3etag import etag_matches
from hammunition.urlredact import redact_url_text

__all__ = [
    "DEFAULT_MAX_BYTES",
    "MIRROR_TIMEOUT",
    "FetchResult",
    "Fetcher",
    "MirrorPath",
    "Transport",
    "TransportUnreachable",
    "UrllibTransport",
    "VerificationError",
    "fetch_disclosure",
    "mirror_url",
    "record_fetch",
    "redact_url_text",
    "safe_name",
    "signature_gap",
]

#: How long a LAN mirror gets to answer before the publisher is asked. A
#: machine on the same network answers in milliseconds; this only bounds the
#: wait when the mirror is switched off.
MIRROR_TIMEOUT = 10.0

#: Refuse a download larger than this. A source tarball is single-digit MB and
#: the largest artifact in the catalog is well under this; the limit exists so
#: a redirect to something enormous fills a bounded amount of disk rather than
#: whatever is free. Raise it per-fetch if a genuine artifact ever needs it.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024

_CHUNK = 64 * 1024
#: A classic or BigTIFF header, either byte order.
TIFF_MAGIC = frozenset({b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"})


class VerificationError(BackendError):
    """A fetched artifact did not match the digest the manifest declared.

    A subclass of :class:`~hammunition.backends.BackendError` so it is fatal by
    the same rule everything else is: D-016 forbids continuing past a failure,
    and this is the failure it would be worst to continue past.
    """


class SignedMismatch(VerificationError):
    """Bytes from the Bunker whose sha256 is not the one its signed catalogue
    lists for them."""

    def __init__(self, message: str, *, signed: str, actual: str) -> None:
        super().__init__(message)
        self.signed = signed
        self.actual = actual


class TransportUnreachable(BackendError):
    """The host did not answer at all: refused, timed out, not resolvable.

    Distinct from an HTTP error status, which is an answer. A mirror that
    raised this is not asked again by the same :class:`Fetcher` (D-070)."""


@dataclass(frozen=True)
class MirrorPath:
    """Where an artifact sits on a LAN mirror: ``<unit>/<name>`` (D-070).

    ``unit`` is the catalog unit and ``name`` the artifact's stable name
    within it -- a region path, a tile name, a data file name -- exactly as
    ``hammunition artifacts`` lists them. Refused when a segment could step
    out of the unit's directory on the server."""

    unit: str
    name: str

    def __post_init__(self) -> None:
        segments = [self.unit, *self.name.split("/")]
        if "/" in self.unit or any(s in ("", ".", "..") for s in segments):
            raise BackendError(
                f"mirror path {self.unit!r}/{self.name!r} has an empty, '.' or '..' "
                f"segment, or a unit with a '/'; refusing to ask a mirror for it"
            )

    @property
    def segments(self) -> tuple[str, ...]:
        return (self.unit, *self.name.split("/"))


def mirror_url(mirror: str, path: MirrorPath) -> str:
    """``<mirror>/<unit>/<name>``, each segment percent-quoted.

    A base URL with or without a trailing slash, with or without a path of
    its own (``http://nas.lan/bunker/``), gives the same answer."""
    quoted = "/".join(urllib.parse.quote(s, safe="") for s in path.segments)
    return f"{mirror.rstrip('/')}/{quoted}"


class Transport(Protocol):
    """Where bytes come from. The only part of this module that is network."""

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:  # pragma: no cover - protocol
        """Yield a readable binary stream for *url*, or raise BackendError."""
        ...


ALLOWED_SCHEMES = frozenset({"http", "https"})


class UrllibTransport:
    """The real one: HTTPS (and HTTP) via :mod:`urllib`, and nothing else.

    **Built with :class:`~urllib.request.OpenerDirector` rather than
    :func:`~urllib.request.build_opener`, and that is not a style choice.**
    ``build_opener`` adds urllib's *default* handler set — ``FileHandler``,
    ``FTPHandler``, ``DataHandler`` — to whatever you pass it; the argument list
    only supplements or overrides. An opener built the obvious way therefore
    serves ``file:///etc/anything`` happily, so a redirect off an ordinary https
    URL could hand this module the contents of a local file and it would hash it,
    cache it, and report a successful fetch. Constructing the director directly
    and adding only the HTTP handlers is what actually closes that door. (This
    was not theoretical: the first version of this class used ``build_opener``
    and ``test_the_real_transport_has_no_handler_for_file_urls`` caught it.)

    With no handler registered for a scheme the director returns ``None``
    instead of raising, which would surface later as an attribute error on
    something that is not a stream, so the scheme is also checked up front and
    a ``None`` response is refused by name. Three layers — schema, scheme
    check, handler set — because the redirect chain is the one the schema
    cannot see.

    Plain ``http`` is permitted because the digest, not the transport, is what
    makes an artifact trustworthy here: a tampered download over TLS and one
    over cleartext are both caught by the same comparison. TLS still hides
    *which* artifact was fetched, so https is preferred in manifests.
    """

    def __init__(self, *, timeout: float = 60.0, follow_redirects: bool = True) -> None:
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        director = urllib.request.OpenerDirector()
        # A transport that follows no redirect answers a 30x as an HTTP error
        # (D-068, amended 2026-10-01: an FSTopo sheet is fetched from the
        # Location the plan checked, and nowhere a second redirect points).
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPHandler(),
            urllib.request.HTTPSHandler(),
            urllib.request.HTTPErrorProcessor(),
            urllib.request.HTTPDefaultErrorHandler(),
        ]
        if follow_redirects:
            handlers.append(urllib.request.HTTPRedirectHandler())
        for handler in handlers:
            director.add_handler(handler)
        self._opener = director

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise BackendError(
                f"refusing to fetch {url!r}: only {'/'.join(sorted(ALLOWED_SCHEMES))} "
                f"are fetchable. A 'file' or 'ftp' URL — reached directly or through "
                f"a redirect — is not a download and will not be treated as one."
            )
        request = urllib.request.Request(url, headers={"User-Agent": "hammunition"})
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise BackendError(f"{url} returned HTTP {exc.code} ({exc.reason})") from exc
        except urllib.error.URLError as exc:
            raise TransportUnreachable(f"{url} could not be fetched: {exc.reason}") from exc
        except OSError as exc:  # timeouts, connection resets, DNS
            raise TransportUnreachable(f"{url} could not be fetched: {exc}") from exc
        if response is None:
            # No handler claimed the scheme. Unreachable given the check above,
            # and refused by name anyway rather than returned as a stream.
            raise BackendError(f"no handler would fetch {url!r}")
        with response:
            yield response


@dataclass(frozen=True)
class FetchResult:
    """A verified artifact on local disk."""

    path: Path
    """Where it is. Content-addressed, so this path implies the digest."""

    sha256: str
    """The digest that was actually computed, not the one that was expected.
    They are equal — a mismatch raises rather than returning — but recording the
    computed one means the log says what was measured."""

    from_cache: bool
    """True when a previously-fetched copy was re-verified instead of downloaded."""

    size: int

    source: str = "publisher"
    """Where the bytes came from: ``cache`` (a verified copy already here),
    ``mirror`` (the LAN mirror, D-070) or ``publisher``."""

    url: str | None = None
    """The URL the bytes were downloaded from; None for a cached copy."""

    mirror_failure: str | None = None
    """Why the mirror was passed over for the publisher, when it was."""

    warning: str | None = None
    """A loud warning the fetch printed: the Bunker served bytes that contradict
    its own signed catalogue, or its row for this item is malformed."""


@dataclass(frozen=True)
class _Downloaded:
    sha256: str
    size: int
    md5: str | None
    source: str
    url: str
    mirror_failure: str | None
    warning: str | None = None


def _safe_name(url: str) -> str:
    """A filename for the cache, derived from the URL but never trusting it.

    The digest is what identifies the file; this is only so a human can tell
    what is in the cache. Anything that could escape the directory or confuse a
    shell is dropped rather than escaped, and the result is capped — a URL is
    attacker-influenced input in the general case, and a cache path is not the
    place to find out how long a filename can be.
    """
    tail = url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
    kept = "".join(c for c in tail if c.isalnum() or c in "._-")
    kept = kept.lstrip(".")  # never a dotfile, never `..`
    return kept[:64] or "artifact"


def safe_name(url: str) -> str:
    """The file name a URL ends in, reduced to what is safe as one path
    segment (:func:`_safe_name`): a data artifact's stable name when it has
    no ``install_as`` (D-070)."""
    return _safe_name(url)


def fetch_disclosure(
    fetcher: Fetcher, url: str, path: MirrorPath, check: str
) -> tuple[str, str, tuple[str, ...]]:
    """What a data fetch step says about its sources (D-070): a suffix for
    its description, the URLs for its detail, and the URLs in the order
    tried. With no mirror the suffix is empty and the detail is the URL, so
    the plan reads exactly as it did."""
    urls = tuple(redact_url_text(where) for _, where in fetcher.sources_for(url, path))
    url = redact_url_text(url)
    if fetcher.offline:
        return (
            f" — Bunker only, offline: the publisher is not asked; the {check} is checked",
            urls[0],
            urls,
        )
    if len(urls) == 1:
        return "", url, urls
    return (
        f" — the LAN mirror first, then the publisher; the {check} is checked either way",
        f"{urls[0]}, then {url}",
        urls,
    )


def record_fetch(result: FetchResult, facts: dict[str, str], *, mirrored: bool) -> str:
    """Put where *result*'s bytes came from into *facts*, for the step's
    ``action_end`` entry, and return the words its outcome line adds: none
    when no mirror is set, so the outcome reads as it did."""
    where = redact_url_text(result.url) if result.url is not None else None
    failure = redact_url_text(result.mirror_failure) if result.mirror_failure is not None else None
    facts["source"] = result.source
    if result.warning is not None:
        facts["warning"] = redact_url_text(result.warning)
    if where is not None:
        facts["fetched_from"] = where
    if failure is not None:
        facts["mirror_failure"] = failure
    if not mirrored or result.source == "cache":
        return ""
    if result.source == "mirror":
        return f", from the LAN mirror {where}"
    if failure is not None:
        return f", from the publisher; the mirror was passed over: {failure}"
    return ", from the publisher"


def signature_gap(artifact: RemoteArtifact) -> str | None:
    """What this engine does *not* check about *artifact*, in one sentence.

    Returned rather than logged so the caller can put it in the plan, where a
    gap belongs — CLAUDE.md requires the capability matrix to report honest
    gaps, and a manifest that carries a signature URL the engine ignores is
    exactly the kind of thing that reads as covered when it is not.
    """
    if artifact.signature_url is None and artifact.signing_key_fingerprint is None:
        return None
    return (
        "declares a signature, which this engine build does not verify — only "
        "the sha256 is checked. The signature fields are catalog data waiting "
        "on a verifier; treat this artifact as digest-pinned, not signed."
    )


def create_temporary(path: Path) -> IO[bytes]:
    """A new file at *path* for writing, created -- never opened.

    ``O_CREAT|O_EXCL|O_NOFOLLOW``, mode 0600: under sudo the cache is the
    operator's, and a ``<name>.part.<pid>`` planted there as a symlink to
    ``/etc/shadow`` must not have root's download written through it. Any
    existing entry at *path* (a link, a file) is a refusal naming it."""
    try:
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
    except OSError as exc:
        raise BackendError(
            f"cannot create the download temporary {path}: {exc.strerror or exc}. Something "
            f"already exists at that name (a link planted there is refused, never followed); "
            f"remove it and run the install again."
        ) from exc
    return os.fdopen(fd, "wb")


def make_dir(path: Path) -> None:
    """``mkdir -p`` that keeps the operator's directories the operator's under
    sudo (:func:`hammunition.paths.ensure_operator_dir`); a refusal is a
    :class:`BackendError` naming the path and the fix."""
    try:
        ensure_operator_dir(path)
    except OperatorDirError as exc:
        raise BackendError(str(exc)) from exc


@contextmanager
def operator_dir(path: Path) -> Iterator[int | None]:
    """:func:`make_dir`, holding a descriptor on *path* while the block runs.

    The descriptor is None where :func:`make_dir` is a plain mkdir; otherwise
    it is the directory proven the operator's through ``O_NOFOLLOW`` from
    their home, for removing and creating entries by ``dir_fd``."""
    try:
        fd = open_operator_dir(path)
    except OperatorDirError as exc:
        raise BackendError(str(exc)) from exc
    try:
        yield fd
    finally:
        if fd is not None:
            os.close(fd)


def remove_tree(parent: Path, parent_fd: int | None, name: str) -> bool:
    """Remove *parent*/*name* if present; True if it was.

    Through *parent_fd* when there is one: ``rmtree(name, dir_fd=...)`` walks
    by descriptor and refuses a symlink, so neither *parent* nor anything
    below it can redirect the removal. Without one it is the path-based
    rmtree it always was (the engine is not root on anyone's behalf)."""
    if parent_fd is None:
        target = parent / name
        if not (target.exists() or target.is_symlink()):
            return False
        shutil.rmtree(target)
        return True
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    shutil.rmtree(name, dir_fd=parent_fd)
    return True


class Fetcher:
    """Downloads artifacts into a content-addressed cache, verifying each one."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        transport: Transport | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        owner: str | None = None,
        mirror: str | None = None,
        mirror_transport: Transport | None = None,
        offline: bool = False,
        signed_sha256: Callable[[MirrorPath], str | None] | None = None,
        bunker: str | None = None,
    ) -> None:
        self.bunker = bunker
        """The enrolled Bunker's name, for warnings that must name it."""
        self.offline = offline
        self.signed_sha256 = signed_sha256
        """The Bunker catalogue's signed sha256 for a mirror path, or None: asked
        only for the routes whose repository pin is not a sha256, so bytes that
        pass a weak digest must also be the bytes the signer vouched for."""
        """Mirror only: the publisher is never asked, and a source that fails
        verification refuses the download instead of falling through (#381)."""
        self.cache_dir = cache_dir if cache_dir is not None else artifact_cache_dir(owner)
        self.transport: Transport = transport if transport is not None else UrllibTransport()
        self.max_bytes = max_bytes
        self.mirror = mirror
        """The LAN mirror's base URL (D-070), or None: publisher only."""
        chosen: Transport
        if mirror_transport is not None:
            chosen = mirror_transport
        elif transport is not None:
            chosen = transport
        else:
            chosen = UrllibTransport(timeout=MIRROR_TIMEOUT)
        if isinstance(chosen, UrllibTransport) and chosen.follow_redirects:
            # A mirror that answers a redirect is a mirror failure, never a
            # second place to fetch from: the Location is not the Bunker's.
            chosen = UrllibTransport(timeout=chosen.timeout, follow_redirects=False)
        self.mirror_transport: Transport = chosen
        #: For :meth:`fetch_sized`: the injected transport, else one that
        #: follows no redirect (final review, I1).
        self.strict_transport: Transport = (
            transport if transport is not None else UrllibTransport(follow_redirects=False)
        )
        self._mirror_down: str | None = None
        self._owned: dict[Path, int] = {}
        """Temporaries this fetcher created, by the inode it created: only those
        are ever removed or published (#381)."""

    def sources_for(self, url: str, mirror: MirrorPath | None) -> tuple[tuple[str, str], ...]:
        """``(source, url)`` pairs in the order a fetch tries them. Pure, so
        the plan discloses exactly the order the run will use. Offline it is the
        Bunker alone: with no mirror or no path on it there is no route, and
        that is a refusal, never the publisher."""
        if self.offline:
            if self.mirror is None or mirror is None:
                raise BackendError(
                    "offline download has no Bunker route; hammunition mirror enrol URL"
                )
            return (("mirror", redact_url_text(mirror_url(self.mirror, mirror))),)
        if self.mirror and mirror is not None:
            return (
                ("mirror", redact_url_text(mirror_url(self.mirror, mirror))),
                ("publisher", url),
            )
        return (("publisher", url),)

    def _bunker_label(self) -> str:
        return f"Bunker {self.bunker}" if self.bunker else "Bunker"

    @staticmethod
    def _warn(text: str) -> str:
        """A loud warning, on stderr, in the run log's tee and the step's facts."""
        print(f"WARNING: {text}", file=sys.stderr)
        return text

    def _resolve_signed(
        self, mirror: MirrorPath | None
    ) -> tuple[MirrorPath | None, str | None, str | None]:
        """``(mirror, signed sha256, warning)`` for a route whose repository pin is
        not a sha256. A row carrying a sha256 is always enforced. A row that is
        malformed is a refusal offline; online it is warned about loudly and the
        mirror is not used at all (the publisher alone is asked)."""
        if mirror is None or self.signed_sha256 is None:
            return mirror, None, None
        from hammunition.resolution import MalformedCatalogueRow

        try:
            return mirror, self.signed_sha256(mirror), None
        except MalformedCatalogueRow as exc:
            if self.offline:
                raise BackendError(
                    redact_url_text(
                        f"offline: the {self._bunker_label()}'s signed catalogue row for "
                        f"{mirror.unit}/{mirror.name} is malformed ({exc}); refusing to use it"
                    )
                ) from None
            return (
                None,
                None,
                self._warn(
                    f"the {self._bunker_label()}'s signed catalogue row for "
                    f"{mirror.unit}/{mirror.name} is malformed ({exc}); the Bunker copy is not "
                    f"used and the publisher alone is asked"
                ),
            )

    @staticmethod
    def _check_signed(actual: str, signed: str | None, where: object) -> None:
        """Refuse bytes whose sha256 is not the one the signed catalogue lists."""
        if signed is not None and actual != signed:
            raise SignedMismatch(
                f"{where} does not match the sha256 the Bunker's signed catalogue lists.\n"
                f"  signed catalogue sha256: {signed}\n"
                f"  actually got:            {actual}\n"
                f"The download has been discarded.",
                signed=signed,
                actual=actual,
            )

    def _from_sources(
        self,
        url: str,
        mirror: MirrorPath | None,
        temporary: Path,
        *,
        max_bytes: int | None,
        md5: bool,
        verify: Callable[[str, int, str | None, str], None],
        also: str | None = None,
        publisher_transport: Transport | None = None,
        signed: str | None = None,
        warning: str | None = None,
    ) -> _Downloaded:
        """Download into *temporary* from each source in turn until one
        verifies. *verify* raises :class:`VerificationError` (or
        :class:`BackendError`) for bytes that do not pass. A mirror failure
        of any kind is recorded and the publisher tried; a publisher failure
        is raised, naming the mirror's too. Nothing unverified survives: the
        temporary is removed after every failed attempt. *publisher_transport*
        replaces the publisher's transport (never the mirror's), for a download
        that must follow no redirect. *signed*, the signed catalogue's sha256
        for a route whose repository pin is not a sha256, is checked in addition
        to *verify* on the mirror's bytes only (never instead of it)."""
        passed_over: str | None = None
        for source, where in self.sources_for(url, mirror):
            if source == "mirror" and self._mirror_down is not None:
                passed_over = self._mirror_down
                continue
            transport = (
                self.mirror_transport
                if source == "mirror"
                else (publisher_transport or self.transport)
            )
            failure: tuple[BackendError, BaseException | None] | None = None
            try:
                sha, size, got = self._download(
                    where, temporary, max_bytes=max_bytes, md5=md5, transport=transport, also=also
                )
                verify(sha, size, got, where)
                if source == "mirror":
                    self._check_signed(sha, signed, where)
            except BaseException as exc:
                cleanup_failure = self._try_discard(temporary)
                if not isinstance(exc, Exception):
                    raise  # an interrupt stops the run, whatever else happened
                if cleanup_failure is not None:
                    # The temporary could not be removed, so nothing else can be
                    # tried under its name. Only a sanitised message is raised.
                    failure = (
                        BackendError(
                            redact_url_text(
                                f"{where}: {exc}\n(removing the download temporary "
                                f"{temporary} also failed: {cleanup_failure})"
                            )
                        ),
                        exc,
                    )
                elif source == "mirror":
                    # Any Exception from the mirror is a mirror failure: a
                    # truncated chunked body or a bad status line is an
                    # http.client.HTTPException, not an OSError, and must hand
                    # over to the publisher rather than abort the install.
                    passed_over = redact_url_text(f"{where}: {exc}")
                    if isinstance(exc, SignedMismatch) and mirror is not None and not self.offline:
                        warning = self._warn(
                            redact_url_text(
                                f"the {self._bunker_label()} served bytes for "
                                f"{mirror.unit}/{mirror.name} that contradict its own signed "
                                f"catalogue (signed sha256 {exc.signed}, got {exc.actual}); "
                                f"falling back to the publisher, whose own check still applies"
                            )
                        )
                    if isinstance(exc, TransportUnreachable):
                        self._mirror_down = redact_url_text(
                            f"the mirror {self.mirror} did not answer earlier in this run ({exc})"
                        )
                    continue
                elif passed_over is None:
                    raise
                else:
                    # Whatever the publisher raised (an OSError, a short read, a
                    # timeout), the mirror's reason travels with it. The original
                    # stays as the cause unless it (or its chain) carries a credential.
                    note = f"(The LAN mirror was tried first and passed over: {passed_over})"
                    if isinstance(exc, BackendError):
                        failure = (type(exc)(redact_url_text(f"{exc}\n{note}")), exc)
                    else:
                        failure = (
                            BackendError(
                                redact_url_text(
                                    f"{where} could not be fetched: "
                                    f"{type(exc).__name__}: {exc}\n{note}"
                                )
                            ),
                            exc,
                        )
            if failure is not None:
                # Raised outside the handler so the new error has no implicit context.
                error, cause = failure
                raise error from (cause if _chain_is_clean(cause) else None)
            return _Downloaded(sha, size, got, source, where, passed_over, warning)
        if self.offline:
            raise BackendError(
                f"offline Bunker download failed: {passed_over or self._mirror_down}"
            )
        raise AssertionError("the publisher is always the last source")  # pragma: no cover

    def _temporary_for(self, final: Path) -> Path:
        """A fresh, unguessable temporary name beside *final*. Exclusive creation
        (:func:`create_temporary`) still decides whether it is ours."""
        return final.with_name(f"{final.name}.part.{os.getpid()}.{secrets.token_hex(8)}")

    def _discard(self, temporary: Path) -> None:
        """Remove *temporary* if, and only if, it is still the file this fetcher
        created. A temporary exclusive creation refused, or one another process
        has since put in its place, is not ours to delete."""
        owned = self._owned.pop(temporary, None)
        if owned is None:
            return
        try:
            if os.lstat(temporary).st_ino == owned:
                os.unlink(temporary)
        except FileNotFoundError:
            pass

    def _try_discard(self, temporary: Path) -> Exception | None:
        """:meth:`_discard`, returning the error instead of raising it."""
        try:
            self._discard(temporary)
        except Exception as exc:
            return exc
        return None

    def _publish(self, temporary: Path, final: Path) -> None:
        """Move our verified temporary over *final*; nothing is left behind if
        that fails, and nothing that is not ours is touched."""
        try:
            owned = self._owned.get(temporary)
            if owned is None or os.lstat(temporary).st_ino != owned:
                raise BackendError(
                    f"the download temporary {temporary} is no longer the file that was "
                    f"verified; nothing was installed from it"
                )
            os.replace(temporary, final)
        except BaseException:
            self._discard(temporary)
            raise
        self._owned.pop(temporary, None)

    def path_for(self, artifact: RemoteArtifact) -> Path:
        """Where this artifact lives once verified. Pure; touches no disk.

        Lets the plan disclose the destination before anything is written,
        which is the same contract the transaction log's ``Records:`` section
        keeps.
        """
        return self.cache_dir / f"{artifact.sha256}-{_safe_name(artifact.url)}"

    def md5_path_for(self, url: str, md5: str) -> Path:
        """Where an MD5-verified file lives once verified (:meth:`fetch_md5`). Pure."""
        return self.cache_dir / f"md5-{md5}-{_safe_name(url)}"

    def fetch(
        self,
        artifact: RemoteArtifact,
        *,
        max_bytes: int | None = None,
        mirror: MirrorPath | None = None,
    ) -> FetchResult:
        """Return a verified local copy of *artifact*, downloading if needed.

        Raises :class:`VerificationError` if what arrives does not match the
        manifest's digest, and :class:`~hammunition.backends.BackendError` if it
        could not be fetched at all. There is no return value that means
        "unverified".

        *max_bytes*, if given, overrides this fetcher's instance-level cap for
        this one download; the default (``None``) keeps the instance's own
        :attr:`max_bytes`. It never disables the cap -- there is no value that
        means unlimited.

        *mirror*, with a mirror set, is where this artifact sits on it; the
        mirror is then tried first and the publisher second (D-070).
        """
        final = self.path_for(artifact)

        make_dir(self.cache_dir)

        def cached_ok(sha: str, _size: int, _other: str | None, _copy: Path) -> None:
            if sha != artifact.sha256:
                raise VerificationError(f"{final}: its sha256 is not the manifest's")

        # Content-addressed, so a cached file that does not match is a corrupted
        # or tampered entry, not a stale version: dropped, fetched again, never served.
        hit = self._cache_hit(
            final,
            expected_size=None,
            limit=max_bytes if max_bytes is not None else self.max_bytes,
            extra=None,
            verify=cached_ok,
        )
        if hit is not None:
            return hit

        make_dir(self.cache_dir)
        # Same directory as the destination so the final move is a rename
        # within one filesystem, which is atomic. A temp file in /tmp would
        # make it a copy, and a copy can be interrupted half-written.
        temporary = self._temporary_for(final)

        def verify(actual: str, _size: int, _md5: str | None, where: str) -> None:
            if actual != artifact.sha256:
                raise VerificationError(
                    f"{where} does not match the digest the manifest declares.\n"
                    f"  expected sha256: {artifact.sha256}\n"
                    f"  actually got:    {actual}\n"
                    f"The download has been discarded. This is either a corrupted "
                    f"transfer, an upstream that re-cut a release under the same URL, "
                    f"or an artifact that is not the one the catalog was written "
                    f"against — and none of those may be installed."
                )

        got = self._from_sources(
            artifact.url, mirror, temporary, max_bytes=max_bytes, md5=False, verify=verify
        )
        self._publish(temporary, final)
        return FetchResult(
            path=final,
            sha256=got.sha256,
            from_cache=False,
            size=got.size,
            source=got.source,
            url=got.url,
            mirror_failure=got.mirror_failure,
            warning=got.warning,
        )

    def fetch_md5(
        self, url: str, md5: str, *, expected_size: int, mirror: MirrorPath | None = None
    ) -> FetchResult:
        """A file verified only by its publisher's MD5 (D-057), for map data the
        catalog carries no sha256 pin for. Weaker than :meth:`fetch`, and the plan
        says so beside every region it is used for. The size must match what the
        publisher's server reported, and the cap is that size plus 1 MiB, so a
        server that keeps sending is still stopped.
        """
        mirror, signed, lead = self._resolve_signed(mirror)
        make_dir(self.cache_dir)
        final = self.md5_path_for(url, md5)

        def cached_ok(_sha: str, _size: int, got: str | None, _copy: Path) -> None:
            if got != md5:
                raise VerificationError(f"{final}: its md5 is not the publisher's")

        hit = self._cache_hit(
            final,
            expected_size=expected_size,
            limit=expected_size + 1024 * 1024,
            extra="md5",
            verify=cached_ok,
            signed=signed,
        )
        if hit is not None:
            return hit

        temporary = self._temporary_for(final)

        def verify(_sha: str, size: int, got: str | None, where: str) -> None:
            if size != expected_size:
                raise VerificationError(
                    f"{where}: the server reported {expected_size} bytes and sent "
                    f"{size}; the size check failed"
                )
            if got != md5:
                raise VerificationError(
                    f"{where} does not match the md5 its publisher lists.\n"
                    f"  expected md5: {md5}\n  actually got: {got}\n"
                    f"The download has been discarded."
                )

        done = self._from_sources(
            url,
            mirror,
            temporary,
            max_bytes=expected_size + 1024 * 1024,
            md5=True,
            verify=verify,
            signed=signed,
            warning=lead,
        )
        self._publish(temporary, final)
        return FetchResult(
            path=final,
            sha256=done.sha256,
            from_cache=False,
            size=done.size,
            source=done.source,
            url=done.url,
            mirror_failure=done.mirror_failure,
            warning=done.warning,
        )

    def sha1_path_for(self, url: str, sha1: str) -> Path:
        """Where a SHA-1-verified file lives once verified (:meth:`fetch_sha1`). Pure."""
        return self.cache_dir / f"sha1-{sha1}-{_safe_name(url)}"

    def fetch_sha1(
        self, url: str, sha1: str, *, expected_size: int, mirror: MirrorPath | None = None
    ) -> FetchResult:
        """A file verified by the SHA-1 and exact size its publisher's own index
        gives (D-069: CoMaps' maps), weaker than a sha256 Hammunition measured,
        and the plan says so on every line. The size is checked exactly:
        CoMaps' mirrors answer a missing map with 200 and a web page, so a
        status proves nothing. The sha256 of the bytes is returned so the
        transaction log carries a strong digest of what was installed. A LAN
        mirror (D-070) is asked first when one is set, checked the same way.
        """
        mirror, signed, lead = self._resolve_signed(mirror)
        make_dir(self.cache_dir)
        final = self.sha1_path_for(url, sha1)

        def cached_ok(_sha: str, _size: int, got: str | None, _copy: Path) -> None:
            if got != sha1:
                raise VerificationError(f"{final}: its SHA-1 is not the publisher index's")

        hit = self._cache_hit(
            final,
            expected_size=expected_size,
            limit=expected_size + 1024 * 1024,
            extra="sha1",
            verify=cached_ok,
            signed=signed,
        )
        if hit is not None:
            return hit

        temporary = self._temporary_for(final)

        def verify(_sha: str, size: int, got: str | None, where: str) -> None:
            if size != expected_size:
                raise VerificationError(
                    f"{where}: the publisher's index says {expected_size} bytes and {size} "
                    f"arrived; the size check failed (a mirror answers a missing file with "
                    f"a web page)"
                )
            if got != sha1:
                raise VerificationError(
                    f"{where} does not match the SHA-1 its publisher's index lists.\n"
                    f"  expected SHA-1: {sha1}\n  actually got: {got}\n"
                    f"The download has been discarded."
                )

        done = self._from_sources(
            url,
            mirror,
            temporary,
            max_bytes=expected_size + 1024 * 1024,
            md5=False,
            verify=verify,
            also="sha1",
            signed=signed,
            warning=lead,
        )
        self._publish(temporary, final)
        return FetchResult(
            path=final,
            sha256=done.sha256,
            from_cache=False,
            size=done.size,
            source=done.source,
            url=done.url,
            mirror_failure=done.mirror_failure,
            warning=done.warning,
        )

    def etag_path_for(self, url: str, etag: str) -> Path:
        """Where an ETag-verified file lives once verified (:meth:`fetch_etag`). Pure."""
        return self.cache_dir / f"etag-{etag.strip().strip(chr(34))}-{_safe_name(url)}"

    def fetch_etag(
        self, url: str, etag: str, *, expected_size: int, mirror: MirrorPath | None = None
    ) -> FetchResult:
        """A file verified only by its publisher's S3 ETag (D-068): a
        single-part upload's MD5, or a multipart upload's MD5 of its parts'
        MD5s (:mod:`hammunition.s3etag`). As weak as :meth:`fetch_md5`, and
        the plan says so beside every file it is used for. The size must be
        the one the index and the bucket agree on; the cap is that size plus
        1 MiB. A cached copy is re-verified every time, never trusted for
        having matched once. A LAN mirror (D-070) is asked first when one is
        set and its bytes pass the same size and ETag checks; offline it is the
        only source (#381).
        """
        mirror, signed, lead = self._resolve_signed(mirror)
        make_dir(self.cache_dir)
        final = self.etag_path_for(url, etag)

        def cached_ok(_sha: str, _size: int, _other: str | None, copy: Path) -> None:
            if not etag_matches(copy, etag):
                raise VerificationError(f"{final}: does not reproduce the ETag")

        hit = self._cache_hit(
            final,
            expected_size=expected_size,
            limit=expected_size + 1024 * 1024,
            extra=None,
            verify=cached_ok,
            signed=signed,
        )
        if hit is not None:
            return hit

        temporary = self._temporary_for(final)

        def verify(_sha: str, size: int, _other: str | None, where: str) -> None:
            if size != expected_size:
                raise VerificationError(
                    f"{where}: {expected_size} bytes were expected and {size} arrived; "
                    f"the size check failed"
                )
            if not etag_matches(temporary, etag):
                raise VerificationError(
                    f"{where} does not match the ETag its publisher lists.\n"
                    f"  expected ETag: {etag}\n"
                    f"No part size reproduces it. The download has been discarded."
                )

        done = self._from_sources(
            url,
            mirror,
            temporary,
            max_bytes=expected_size + 1024 * 1024,
            md5=False,
            verify=verify,
            signed=signed,
            warning=lead,
        )
        self._publish(temporary, final)
        return FetchResult(
            path=final,
            sha256=done.sha256,
            from_cache=False,
            size=done.size,
            source=done.source,
            url=done.url,
            mirror_failure=done.mirror_failure,
            warning=done.warning,
        )

    def _cache_hit(
        self,
        final: Path,
        *,
        expected_size: int | None,
        limit: int,
        extra: str | None,
        verify: Callable[[str, int, str | None, Path], None],
        signed: str | None = None,
    ) -> FetchResult | None:
        """The cached copy at *final* when it passes its route's checks, else
        None with the entry removed (a failed check never leaves a final file
        for a failed recovery to leave behind). The one place any route reuses
        a cached file.

        Copy-then-verify: *final* is opened without following a symlink and
        without blocking, must be a regular file (of *expected_size* when the
        route knows it, never over *limit*), and at most its size plus one byte
        is copied into a private temporary this fetcher creates. The sha256 of
        the result and the route's *extra* digest (``md5``/``sha1``) are taken
        from that copy as it is written, *verify* ``(sha256, size, extra,
        copy)`` raises if the copy fails the route's check, and only then is
        the copy moved over *final*. The path, bytes and digests returned are
        one inode of our own that nothing done to the original path afterwards
        can change.
        """
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            fd = os.open(final, flags)
        except FileNotFoundError:
            return None
        except OSError:  # a symlink (ELOOP), a permission problem: not a cache hit
            self._drop_entry(final)
            return None
        temporary = self._temporary_for(final)
        try:
            with os.fdopen(fd, "rb") as source:
                status = os.fstat(source.fileno())
                size = status.st_size
                if (
                    not stat.S_ISREG(status.st_mode)
                    or size > limit
                    or (expected_size is not None and size != expected_size)
                ):
                    raise VerificationError(f"{final}: not a regular file of the expected size")
                digest = hashlib.sha256()
                other = hashlib.new(extra, usedforsecurity=False) if extra else None
                copied = 0
                with create_temporary(temporary) as copy:
                    self._owned[temporary] = os.fstat(copy.fileno()).st_ino
                    while copied <= size:
                        chunk = source.read(min(_CHUNK, size + 1 - copied))
                        if not chunk:
                            break
                        copied += len(chunk)
                        digest.update(chunk)
                        if other is not None:
                            other.update(chunk)
                        copy.write(chunk)
                    copy.flush()
                    os.fsync(copy.fileno())
            if copied != size:
                raise VerificationError(f"{final}: it changed size while it was read")
            sha256 = digest.hexdigest()
            verify(sha256, size, other.hexdigest() if other is not None else None, temporary)
            self._check_signed(sha256, signed, final)
            self._publish(temporary, final)
        except (OSError, BackendError):
            self._discard(temporary)
            self._drop_entry(final)
            return None
        except BaseException:
            self._discard(temporary)
            raise
        return FetchResult(path=final, sha256=sha256, from_cache=True, size=size, source="cache")

    @staticmethod
    def _drop_entry(final: Path) -> None:
        with contextlib.suppress(OSError):
            final.unlink(missing_ok=True)

    def sized_path_for(self, url: str, size: int) -> Path:
        """Where a size-checked file lands (:meth:`fetch_sized`). Pure."""
        return self.cache_dir / f"sized-{size}-{_safe_name(url)}"

    def fetch_sized(
        self, url: str, *, expected_size: int, mirror: MirrorPath | None = None
    ) -> FetchResult:
        """A file nobody publishes a checksum for and Hammunition has not
        pinned (D-068, amended 2026-10-01: FSTopo sheets): only its size, as
        the server announced it at plan time, and its first bytes being a
        TIFF are checked. The weakest fetch here, and the plan says
        "unverified" beside every file it is used for. A cached copy is never
        reused, since nothing could tell a damaged one from a good one; the
        sha256 of what arrived is returned for the transaction log and the
        install's own re-check. No publisher redirect is followed: the URL is
        the one the plan located and checked. A LAN mirror (D-070) is asked
        first when one is set and its bytes pass the same two checks; offline
        it is the only source (#381).
        """
        mirror, signed, lead = self._resolve_signed(mirror)
        make_dir(self.cache_dir)
        final = self.sized_path_for(url, expected_size)
        final.unlink(missing_ok=True)
        temporary = self._temporary_for(final)

        def verify(_sha: str, size: int, _other: str | None, where: str) -> None:
            if size != expected_size:
                raise VerificationError(
                    f"{where}: the server announced {expected_size} bytes and {size} arrived; "
                    f"the size check failed"
                )
            with temporary.open("rb") as handle:
                magic = handle.read(4)
            if magic not in TIFF_MAGIC:
                raise VerificationError(
                    f"{where} is not a TIFF (it starts {magic!r}); a gateway that answers "
                    f"with a web page is not a map sheet. The download has been discarded."
                )

        done = self._from_sources(
            url,
            mirror,
            temporary,
            max_bytes=expected_size + 1024 * 1024,
            md5=False,
            verify=verify,
            publisher_transport=self.strict_transport,
            signed=signed,
            warning=lead,
        )
        self._publish(temporary, final)
        return FetchResult(
            path=final,
            sha256=done.sha256,
            from_cache=False,
            size=done.size,
            source=done.source,
            url=done.url,
            mirror_failure=done.mirror_failure,
            warning=done.warning,
        )

    def checked_path_for(self, url: str) -> Path:
        """Where a structure-checked file lands (:meth:`fetch_checked`). Pure."""
        return self.cache_dir / f"checked-{_safe_name(url)}"

    def fetch_checked(
        self,
        url: str,
        *,
        max_bytes: int,
        check: Callable[[Path], None],
        mirror: MirrorPath | None = None,
    ) -> FetchResult:
        """A file whose publisher offers no digest at all and changes it too
        often for a pin (D-074, amended 2026-10-01: the ACMA register). Only
        *check*, run over the bytes as they arrived, stands between the
        download and the install: it raises :class:`VerificationError` for a
        file that is damaged or not what the reader expects. The weakest
        fetch here beside :meth:`fetch_sized`, and the plan says
        "unverified". A cached copy is never reused; a LAN mirror (D-070) is
        asked first when one is set and checked the same way, which over
        plain http ties the bytes to nothing but their own structure. The
        sha256 of what arrived is returned for the log and the install's
        own re-check.
        """
        mirror, signed, lead = self._resolve_signed(mirror)
        make_dir(self.cache_dir)
        final = self.checked_path_for(url)
        final.unlink(missing_ok=True)
        temporary = self._temporary_for(final)

        def verify(_sha: str, _size: int, _other: str | None, where: str) -> None:
            try:
                check(temporary)
            except VerificationError:
                raise
            except Exception as exc:
                raise VerificationError(
                    f"{where}: {exc}. The download has been discarded."
                ) from exc

        done = self._from_sources(
            url,
            mirror,
            temporary,
            max_bytes=max_bytes,
            md5=False,
            verify=verify,
            signed=signed,
            warning=lead,
        )
        self._publish(temporary, final)
        return FetchResult(
            path=final,
            sha256=done.sha256,
            from_cache=False,
            size=done.size,
            source=done.source,
            url=done.url,
            mirror_failure=done.mirror_failure,
            warning=done.warning,
        )

    def _download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int | None = None,
        md5: bool = False,
        transport: Transport | None = None,
        also: str | None = None,
    ) -> tuple[str, int, str | None]:
        """Stream *url* to *destination*, hashing as it goes.

        Returns ``(sha256, size, other_or_none)``. *max_bytes* overrides this
        fetcher's instance-level cap for this one call; the sha256 digest is
        always computed, and a second digest alongside it only when asked --
        MD5 with *md5* (Geofabrik, D-057), SHA-1 with ``also="sha1"`` (CoMaps,
        D-069) -- so :meth:`fetch`'s sha256-only path pays nothing extra.

        Hashing the bytes as they are written, rather than re-reading the file
        afterwards, means the digest is over what was actually stored and
        leaves no window between the two.
        """
        limit = max_bytes if max_bytes is not None else self.max_bytes
        digest = hashlib.sha256()
        if md5:
            also = "md5"
        extra_digest = hashlib.new(also, usedforsecurity=False) if also in ("md5", "sha1") else None
        size = 0
        source = transport if transport is not None else self.transport
        created = create_temporary(destination)
        self._owned[destination] = os.fstat(created.fileno()).st_ino
        with created as handle, source.open(url) as stream:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise BackendError(
                        f"{url} exceeds the {limit} byte limit and was "
                        f"abandoned part-way. If this artifact is genuinely this "
                        f"large, raise the limit deliberately rather than removing it."
                    )
                digest.update(chunk)
                if extra_digest is not None:
                    extra_digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        return (
            digest.hexdigest(),
            size,
            (extra_digest.hexdigest() if extra_digest is not None else None),
        )


def _chain_is_clean(exc: BaseException | None) -> bool:
    """Whether *exc* and everything it chains (``__cause__`` and ``__context__``,
    however deep, cycles allowed) reads the same after redaction: nothing in
    the chain carries a URL's user or password."""
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        text = str(current)
        if redact_url_text(text) != text:
            return False
        pending.append(current.__cause__)
        pending.append(current.__context__)
    return True


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
