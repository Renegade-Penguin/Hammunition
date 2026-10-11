# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which engine verbs the console offers, and which it does not yet (#323).

The maintainer's rule is that the console configures and administers all of
Hammunition. This module turns that into a number: every verb in the CLI's
argparse tree is either ``COVERED`` (a console screen runs it, and the module
that does says so in its source), ``UNCOVERED`` (an issue is named beside it)
or ``EXEMPT`` (the console cannot meaningfully offer it, with the reason).
``scripts/gen_console_coverage.py`` renders ``docs/reference/console-coverage.md``
from these tables; ``tests/console/test_coverage.py`` fails when the CLI gains a
verb nobody classified, when a covered verb's screen does not mention it, and
when the uncovered list grows. Pure data: no screen imports this module.
"""

from __future__ import annotations

Verb = tuple[str, ...]

# verb -> (screen, module under hammunition/console/ whose source names the verb)
COVERED: dict[Verb, tuple[str, str]] = {
    ("list",): ("Install", "screens/install.py"),
    ("status",): ("Home", "screens/home.py"),
    ("update",): ("Update", "screens/update.py"),
    ("doctor",): ("Home", "screens/home.py"),
    ("show",): ("Help (profile pages)", "screens/help.py"),
    ("install",): ("Install (plan, then a pane)", "screens/plan.py"),
    ("uninstall",): ("Install (plan, then a pane)", "screens/install.py"),
    ("logs",): ("Logs", "screens/logs.py"),
    ("self-update",): ("Home", "screens/home.py"),
    ("station", "show"): ("Station", "screens/station.py"),
    ("station", "set"): ("Station", "screens/station.py"),
    ("secrets", "status"): ("Secrets", "screens/secrets.py"),
    ("maps", "regions"): ("Station (the region chooser)", "screens/station.py"),
    ("maps", "areas"): ("Repeaters", "screens/repeaters.py"),
    ("maps", "activate"): ("Repeaters", "screens/repeaters.py"),
    ("maps", "repeaters", "import"): ("Repeaters", "screens/repeaters.py"),
    ("maps", "repeaters", "list"): ("Repeaters", "screens/repeaters.py"),
    ("maps", "repeaters", "remove"): ("Repeaters", "screens/repeaters.py"),
    ("maps", "repeaters", "fetch-repeaterbook"): ("Repeaters", "repeaterbook.py"),
    ("reference", "books"): ("Station (the book chooser)", "screens/station.py"),
    ("hardware", "apply"): ("Home (the first-run checklist)", "screens/home.py"),
}

# verb -> (issue that will add it, the screen planned)
UNCOVERED: dict[Verb, tuple[str, str]] = {
    ("mirror", "enrol"): ("#323", "Station (mirror)"),
    ("mirror", "status"): ("#323", "Station (mirror)"),
    ("mirror", "accept-older"): ("#323", "Station (mirror)"),
    ("maps", "qmapshack"): ("#323", "Maps"),
    ("maps", "splat"): ("#323", "Maps"),
    ("maps", "comaps"): ("#323", "Maps"),
    ("maps", "gps-tether"): ("#323", "Maps"),
    ("maps", "phone"): ("#323", "Maps"),
    ("maps", "navit"): ("#323", "Maps"),
    ("maps", "infra", "import"): ("#323", "Maps"),
    ("maps", "infra", "fetch-fcc-asr"): ("#323", "Maps"),
    ("maps", "infra", "fetch-nwr"): ("#323", "Maps"),
    ("maps", "infra", "remove"): ("#323", "Maps"),
    ("maps", "repeaters", "fetch-hearham"): ("#349", "Repeaters, part 2"),
    ("maps", "repeaters", "fetch-etcc"): ("#349", "Repeaters, part 2"),
    ("maps", "repeaters", "fetch-brandmeister"): ("#349", "Repeaters, part 2"),
    ("transactions",): ("#243", "Logs (transactions --json first)"),
    ("artifacts",): ("#323", "Reference"),
    ("reference", "serve"): ("#323", "Reference"),
    ("menus", "apply"): ("#323", "Hardware"),
    ("hardware", "list"): ("#323", "Hardware"),
    ("hardware", "unapply"): ("#323", "Hardware"),
    ("hardware", "state"): ("#323", "Hardware"),
    ("hardware", "gps-resume-report"): ("#323", "Hardware"),
    ("hardware", "park"): ("#323", "Hardware"),
    ("hardware", "wake"): ("#323", "Hardware"),
    ("time",): ("#323", "Services and Time"),
    ("time", "mode"): ("#323", "Services and Time"),
    ("time", "measure"): ("#323", "Services and Time"),
    ("services",): ("#323", "Services and Time"),
    ("services", "start"): ("#323", "Services and Time"),
    ("services", "stop"): ("#323", "Services and Time"),
    ("services", "enable"): ("#323", "Services and Time"),
    ("services", "disable"): ("#323", "Services and Time"),
}

# verb -> why the console does not offer it
EXEMPT: dict[Verb, str] = {
    ("console",): "it is the console itself",
}

# The uncovered count may fall and must never rise: a new verb is covered or
# exempt, or this number is raised in a reviewed change that names the issue.
# #381 adds three mirror verbs; their Station console paths are tracked by #323.
MAX_UNCOVERED = 34
