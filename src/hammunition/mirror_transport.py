# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import errno
import os
import stat
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO
from urllib.parse import quote, unquote, urlsplit

from hammunition.backends import BackendError
from hammunition.catalogue import CatalogueError, parse, safe_relative, valid_enrolment_id
from hammunition.fetch import MIRROR_TIMEOUT, TransportUnreachable
from hammunition.signers import (
    MirrorState,
    SignerError,
    VerifiedCatalogue,
    advance_mirror,
    verify,
)
from hammunition.station import _check_mirror


def _safe(value: str, field: str) -> str:
    """:func:`safe_relative`, refusing as the transport refuses: BackendError."""
    try:
        return safe_relative(value, field)
    except CatalogueError as exc:
        raise BackendError(str(exc)) from exc


def read_catalogue(source: MirrorTransport) -> bytes:
    try:
        return source.read("catalogue.json", max_bytes=32 * 1024 * 1024)
    except BackendError as exc:
        url = source.base.rstrip("/") + "/catalogue.json"
        raise BackendError(
            f"no catalogue at {url}: {exc}. "
            "Enrol a Bunker that serves one, or run without --offline."
        ) from exc


class MirrorTransport:
    def __init__(self, base: str, enrolment_id: str | None = None) -> None:
        self.base = _check_mirror(base).rstrip("/")
        self.parts = urlsplit(self.base)
        if enrolment_id is not None and not valid_enrolment_id(enrolment_id):
            raise BackendError("invalid enrolment id")
        self.enrolment_id = enrolment_id
        self.opener = urllib.request.OpenerDirector()
        for handler in (
            urllib.request.HTTPHandler(),
            urllib.request.HTTPSHandler(),
            urllib.request.HTTPErrorProcessor(),
            urllib.request.HTTPDefaultErrorHandler(),
        ):
            self.opener.add_handler(handler)

    @contextmanager
    def open(self, url: str) -> Iterator[IO[bytes]]:
        parts = urlsplit(url)
        prefix = self.parts.path.rstrip("/") + "/"
        if (
            (parts.scheme, parts.netloc) != (self.parts.scheme, self.parts.netloc)
            or not parts.path.startswith(prefix)
            or parts.query
            or parts.fragment
        ):
            raise BackendError("mirror request leaves the configured base")
        relative = _safe(unquote(parts.path[len(prefix) :]), "mirror request")
        if parts.scheme == "file":
            directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            fd = -1
            try:
                components = [*Path(unquote(self.parts.path)).parts[1:], *relative.split("/")]
                for index, component in enumerate(components):
                    _safe(component, "file mirror component")
                    flags = os.O_RDONLY | os.O_NOFOLLOW
                    if index < len(components) - 1:
                        flags |= os.O_DIRECTORY
                    else:
                        # A FIFO planted in an export would block open() for
                        # ever; non-blocking returns, and fstat refuses it.
                        flags |= os.O_NONBLOCK
                    try:
                        child = os.open(component, flags, dir_fd=directory)
                    except OSError as exc:
                        if exc.errno in (errno.ELOOP, errno.ENOTDIR) and stat.S_ISLNK(
                            os.stat(component, dir_fd=directory, follow_symlinks=False).st_mode
                        ):
                            raise BackendError(
                                "symlinked path components in a file mirror are refused: "
                                f"{component!r} in {relative}"
                            ) from exc
                        raise
                    os.close(directory)
                    directory = child
                fd, directory = directory, -1
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise BackendError("file mirror target is not a regular file")
                stream = os.fdopen(fd, "rb")
                fd = -1
            except OSError as exc:
                raise BackendError(f"file mirror {relative}: {exc}") from exc
            finally:
                if directory >= 0:
                    os.close(directory)
                if fd >= 0:
                    os.close(fd)
            with stream:
                yield stream
            return
        headers = {"User-Agent": "hammunition"}
        if self.enrolment_id is not None:
            headers["X-Hammunition-Enrolment"] = self.enrolment_id
        try:
            response = self.opener.open(
                urllib.request.Request(url, headers=headers), timeout=MIRROR_TIMEOUT
            )
            if response is None:
                raise BackendError("no mirror transport handler")
        except urllib.error.HTTPError as exc:
            note = "; redirects are not followed" if 300 <= exc.code < 400 else ""
            raise BackendError(f"mirror returned HTTP {exc.code}{note}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TransportUnreachable(f"mirror could not be reached: {exc}") from exc

        with response:
            yield response

    def read(self, relative: str, *, max_bytes: int) -> bytes:
        relative = _safe(relative, "mirror path")
        url = self.base.rstrip("/") + "/" + "/".join(quote(p, safe="") for p in relative.split("/"))
        with self.open(url) as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise BackendError(f"{relative}: larger than {max_bytes} bytes")
        return raw


@dataclass(frozen=True)
class CatalogueInputs:
    """The resolution boundary: reads a mirror path, failing as ``OSError``.

    Resolution is a leaf that must not import backend exceptions; this adapter
    turns the transport's refusal into the ``OSError`` an input read raises."""

    transport: MirrorTransport

    def read(self, relative: str, *, max_bytes: int) -> bytes:
        try:
            return self.transport.read(relative, max_bytes=max_bytes)
        except BackendError as exc:
            raise OSError(str(exc)) from exc


def load_catalogue(
    state: MirrorState,
    *,
    require_hardware: bool,
    now: datetime,
    transport: MirrorTransport | None = None,
    owner: str | None = None,
    advance: bool = True,
) -> VerifiedCatalogue:
    """The enrolled Bunker's catalogue, read once and verified against *state*.

    One run makes one of these and shares the returned object; nothing is
    cached across runs, and an unsigned candidate is never reused. Only an
    enrolled signer's signature is fetched. The stored serial advances only
    after verification, and never moves down. ``advance=False`` (a dry run)
    verifies and writes nothing."""
    source = transport or MirrorTransport(state.url, state.enrolment_id)
    raw = read_catalogue(source)
    parsed = parse(raw)
    signatures: dict[str, bytes] = {}
    fetch_failures: list[str] = []
    accepted = {k.id for k in state.keys}
    for signer in parsed.signers:
        if signer.id not in accepted:
            continue
        try:
            signatures[signer.signature] = source.read(signer.signature, max_bytes=64 * 1024)
        except BackendError as exc:
            # Another enrolled signature can still verify; the reason is kept
            # for the refusal if none does.
            fetch_failures.append(f"{signer.signature}: {exc}")
    try:
        verified = verify(raw, signatures, state, require_hardware=require_hardware, now=now)
    except SignerError as exc:
        if not fetch_failures:
            raise
        raise SignerError(f"{exc}; signature fetch failed: " + "; ".join(fetch_failures)) from exc
    if advance:
        advance_mirror(state, verified.catalogue.serial, verified.catalogue.generated, owner=owner)
    return verified
