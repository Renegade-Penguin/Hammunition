# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Affirmative consent gates.  D-021."""

from .gate import (
    TOPO_SIZE_ENV,
    ConsentDeclined,
    ConsentRecord,
    ConsentUnavailable,
    Decision,
    render_disclosure,
    render_repo_disclosure,
    render_topo_size_disclosure,
    repo_env_var,
    resolve_consent,
    resolve_mirror_consent,
    resolve_repo_consent,
    resolve_topo_size_consent,
)

__all__ = [
    "TOPO_SIZE_ENV",
    "ConsentDeclined",
    "ConsentRecord",
    "ConsentUnavailable",
    "Decision",
    "render_disclosure",
    "render_repo_disclosure",
    "render_topo_size_disclosure",
    "repo_env_var",
    "resolve_consent",
    "resolve_mirror_consent",
    "resolve_repo_consent",
    "resolve_topo_size_consent",
]
