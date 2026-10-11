# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Mapsforge converter tool and git extra-file fetches share the mirror
and preflight helpers every payload backend uses.  #381, Task 14.

:mod:`test_mapsforge` and :mod:`test_git_comaps` drive the owning backends'
own ``steps()`` to prove the call sites are actually wired; this file proves
the shared :func:`~hammunition.payloads.payload_action` and
:func:`~hammunition.payloads.preflight_payloads` behave the way both call
sites depend on, the way ``tests/test_payload_mirror.py`` does for the other
four backends.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bunker_fixtures import make_context
from hammunition.backends.base import BackendError
from hammunition.fetch import Fetcher, mirror_url
from hammunition.manifest.schema import RemoteArtifact
from hammunition.payloads import payload_action, payload_path, preflight_payloads
from hammunition.resolution import CatalogueMiss
from test_fetch_mirror import Routes


def test_converter_tool_size_check_survives_mirror(tmp_path: Path) -> None:
    body = b"jar payload"
    pin = RemoteArtifact(
        url="https://example.invalid/writer.jar", sha256=hashlib.sha256(body).hexdigest()
    )
    at = mirror_url("http://bunker.invalid", payload_path("mapsforge-poi", pin))
    routes = Routes({at: body})
    fetcher = Fetcher(
        tmp_path,
        transport=routes,
        mirror_transport=routes,
        mirror="http://bunker.invalid",
        offline=True,
    )
    action = payload_action(
        "mapsforge-poi", pin, fetcher, label="POI writer", expected_size=len(body) + 1
    )
    with pytest.raises(BackendError, match="manifest size"):
        action.perform()
    assert routes.requested == [at]


def test_tool_extra_preflight_refuses_before_build(tmp_path: Path) -> None:
    context = make_context(tmp_path, [])
    pin = RemoteArtifact(url="https://example.invalid/writer.jar", sha256="a" * 64)
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        preflight_payloads("mapsforge-poi", ((pin, 7),), context=context)
    with pytest.raises(CatalogueMiss, match="not on Bunker"):
        preflight_payloads("thing", ((pin, 7),), context=context)
