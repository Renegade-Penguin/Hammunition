# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Take a URL's user and password out of text before it is shown or logged.

A mirror URL with ``user:password@`` is refused wherever a station or an
enrolment is saved, but the text of the refusal, or of a direct API call, must
not repeat what it refused (#381). The userinfo is what :func:`urllib.parse
.urlsplit` calls it: the part of the authority before its last ``@``. Nothing
after the authority (a path, a query) is touched.

It fails closed. An authority that holds a ``@`` but that ``urlsplit`` refuses,
that does not round-trip through it, or that carries a control character is
replaced whole, host included, rather than trusted to be split correctly.
"""

from __future__ import annotations

import re
import urllib.parse

#: ``scheme://`` then the authority, which ends at the first ``/``, ``?``, ``#``
#: or whitespace.
_AUTHORITY = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)([^/?#\s]*)")

#: What stands in for an authority that could not be split with confidence.
REDACTED = "<redacted>"


def _parses_cleanly(scheme: str, authority: str) -> bool:
    if any(ord(c) < 32 or ord(c) == 127 for c in authority):
        return False
    try:
        parts = urllib.parse.urlsplit(scheme + authority)
        if parts.netloc != authority:
            return False  # urlsplit dropped or changed something
        _ = (parts.hostname, parts.port)  # read for the errors they can raise
    except ValueError:
        return False
    return True


def redact_url_text(text: str) -> str:
    """*text* with the userinfo of every URL in it removed.

    The authority match stops at the first whitespace on purpose: *text* here
    is free-form (an error message, a chained exception), often a URL mixed
    with prose, and matching across a space risks mistaking an unrelated
    ``@`` later in the sentence for part of the URL. That is safe only
    because this function is never handed a string that is *only* a
    candidate URL; :func:`redact_mirror_url` is for that case, where
    ``urlsplit`` itself keeps a raw, un-encoded space inside the userinfo
    rather than treating it as a delimiter (confirmed on CPython: ``netloc``
    round-trips with the space included)."""

    def strip(match: re.Match[str]) -> str:
        scheme, authority = match.group(1), match.group(2)
        if "@" not in authority:
            return match.group(0)
        if not _parses_cleanly(scheme, authority):
            return scheme + REDACTED
        return scheme + authority.rpartition("@")[2]

    return _AUTHORITY.sub(strip, text)


#: Where a real URL's authority ends, per :func:`urllib.parse.urlsplit`:
#: whitespace is not a delimiter there, only these three characters are.
_REAL_DELIMITER = re.compile(r"[/?#]")


def redact_mirror_url(url: str) -> str:
    """*url*, which must be exactly one candidate URL and nothing else (no
    surrounding prose), with its userinfo removed.

    Unlike :func:`redact_url_text`, this is safe to let span a literal space
    inside the userinfo -- there is no unrelated sentence for the authority
    match to run away into, because the whole string *is* the URL being
    checked. Used where an operator's typed ``--mirror`` value, or a station
    refusal that echoes it, must never leak a credential (#381): a space in
    place of the ``%20`` a real client would send still reaches the engine
    verbatim, and :func:`redact_url_text`'s prose-safe matcher would stop
    before the real ``@``, leaving the credential after it unredacted.

    A string with no recognisable ``scheme://`` prefix is not known to
    carry no credential -- ``_check_mirror``'s own rejection of a bare
    ``user:pass@host`` (no scheme at all) or ``http:/user:pass@host/`` (one
    slash, so this match fails) would otherwise echo it back untouched
    (found by an Opus adversarial review of the whole branch, #381): fail
    closed to :data:`REDACTED` whenever an ``@`` is present, rather than
    assuming the absence of a scheme means the absence of userinfo."""
    scheme_match = re.match(r"[A-Za-z][A-Za-z0-9+.\-]*://", url)
    if scheme_match is None:
        return REDACTED if "@" in url else url
    scheme = scheme_match.group(0)
    rest = url[scheme_match.end() :]
    delimiter = _REAL_DELIMITER.search(rest)
    authority, tail = (
        (rest[: delimiter.start()], rest[delimiter.start() :]) if delimiter else (rest, "")
    )
    if "@" not in authority:
        return url
    if not _parses_cleanly(scheme, authority):
        return scheme + REDACTED + tail
    return scheme + authority.rpartition("@")[2] + tail
