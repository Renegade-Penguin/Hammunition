# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Station-local values: callsign, grid square, and the rest.  DESIGN.md §15.5.

The open design question this closes has been blocking since the 1.0 packet
core was admitted. AX.25 writes a callsign into ``/etc/ax25/axports``, Direwolf
is admitted *with configuration rather than merely installation*, and
``linbpq``'s manifest templates ``NODECALL``, ``NODEALIAS`` and ``LOCATOR``.
Until now any manifest carrying a ``config_files`` block failed the whole
transaction, so the `packet` profile — the reason the 73Linux delta was
acquired at all — could not be installed by anyone.

Three properties, in order of how much they matter.

**Missing values defer, they do not block.** A profile with twenty packages and
one templated config file installs the twenty packages and reports the one file
as outstanding, with the values it needs and how to supply them. Refusing the
whole transaction because a callsign is unknown gets an operator nowhere; a
station that is ninety-five per cent built and honest about the rest is a
success. Refusing was the old behaviour and it is what this replaces.

**Values are never invented.** There is no default callsign, no placeholder, no
"CHANGEME". A file templated with a made-up callsign would transmit it. What
this cannot fill in, it declines to write and says so.

**The file lives outside the repository by construction.** It is written to the
operator's XDG config directory through :mod:`hammunition.paths`, which is
owner-aware — running under ``sudo`` still resolves the invoking user's home
rather than root's. The repository's ``.gitignore`` additionally covers
``station.local.yml`` so that a copy kept beside a checkout for testing cannot
be committed by accident.

Validation is deliberately loose. Callsign formats vary by country far more
than the common regexes admit -- prefixes, suffixes, portable indicators,
special event calls -- so this checks the shape a *file format* needs (no
whitespace, plausible length, no shell metacharacters) and leaves the question
of whether a callsign is real to the licensing authority that issued it.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from .kiwix import BOOK_ID
from .maidenhead import centre
from .paths import owner_aware_dir
from .urlredact import redact_mirror_url

__all__ = [
    "DERIVED",
    "STATION_FIELDS",
    "TEMPLATE_VARIABLES",
    "Station",
    "StationError",
    "config_path",
    "load_station",
    "parse_area",
    "prompt_for",
    "save_station",
]


class StationError(ValueError):
    """A station value is unusable, or the file holding them is malformed."""


#: Loose on purpose. Callsigns carry prefixes, suffixes and portable
#: indicators; a strict pattern rejects real ones. This is the shape a
#: configuration file needs, not a licensing check.
CALLSIGN = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,4}(?:/[A-Z0-9]{1,4})*$")

#: Maidenhead: two letters, two digits, optionally two more letters, and the
#: extended pairs some software wants. Case is normalised before matching.
GRID_SQUARE = re.compile(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?$")

#: How often a downloaded map region is refreshed. ``yearly`` is the default
#: applied when nothing is set, never invented as a stored value.
FRESHNESS = ("yearly", "monthly", "latest")

#: A Geofabrik download path: lowercase words joined by ``/``, e.g.
#: ``north-america/us/vermont``. No leading/trailing slash, no ``..``, no
#: whitespace -- the shape a URL path segment needs, not a check that the
#: region exists in Geofabrik's index.
REGION = re.compile(r"[a-z0-9-]+(/[a-z0-9-]+)*")

#: Station fields not named in this set are also `Station` dataclass fields,
#: but map settings are data the maps subsystem reads directly -- never a
#: `{station.*}` template variable -- so they are excluded here.
_MAP_FIELDS = frozenset({"map_regions", "map_freshness"})

#: Station values that are never a `{station.*}` template variable: the map
#: settings, the LAN mirror the verified fetch tries first (D-070), and the
#: Kiwix books chosen for `kiwix-library` (D-066).
_NOT_TEMPLATES = _MAP_FIELDS | {
    "mirror",
    "mirror_require_hardware_key",
    "reference_books",
    "dem_source",
    "topo_radius_km",
    "topo_regions",
    "topo_all",
    "active_areas",
    "secrets_doppler_project",
    "secrets_doppler_config",
}


def parse_area(text: str) -> str:
    """One ``active_areas`` entry, normalised, or :class:`StationError`.

    A US state code (``oh`` or ``OH`` becomes ``OH``; RepeaterBook's ``CA01`` and
    a bare ``state_id`` pass) or a map region name (``north-america/us/ohio``, or
    the last word of one, ``ohio``). The two cannot be mistaken: a code is two
    letters with up to four digits, or digits alone, and a region is a lowercase
    path. The names are not checked against what is loaded: the operator may
    fetch them next (D-082)."""
    from hammunition.repeaters import is_area_code

    value = text.strip()
    if "/" not in value and is_area_code(value.upper()):
        return value.upper()
    if REGION.fullmatch(value):
        return value
    raise StationError(
        f"active area {text!r} is neither a US state code such as OH nor a map region "
        f"such as north-america/us/ohio. `hammunition maps areas` lists what is loaded."
    )


#: A Doppler project or config name: a slug that cannot read as an option
#: (it is an argv element of the one `doppler` command, D-081).
DOPPLER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Where QMapShack's elevation is drawn from (D-068, amended 2026-10-01):
#: Copernicus GLO-30, a surface model, by default; USGS 3DEP bare earth by
#: choice. Each names the ``dem-tiles`` provider it selects.
#: The furthest a radius can usefully be: half the earth's circumference.
MAX_TOPO_RADIUS_KM = 20000

DEM_SOURCES = ("copernicus", "3dep")
DEM_PROVIDERS = {"copernicus": "copernicus-glo30", "3dep": "usgs-3dep"}

#: The rig owner: who holds the serial port. ``rigctld`` (the shared daemon,
#: the default when unset) or ``flrig`` (the panel). A mode with a stated
#: default like ``map_freshness``, not a station fact (D-073 §4).
RIG_OWNERS = ("rigctld", "flrig")

#: How a PTT-only rig is keyed: a serial control line, or the radio's own VOX.
PTT_LINES = ("rts", "dtr", "vox")

#: A rig value: a catalog device id (lowercase slug) or ``hamlib:<model>`` for a
#: radio with no manifest. Which one it is is the CLI's check against the
#: catalog and this machine's hamlib; the dataclass only checks the shape.
RIG_VALUE = re.compile(r"^(?:hamlib:[0-9]+|[a-z0-9][a-z0-9-]*)$")

#: An absolute path under ``/dev/`` built only from the characters udev leaves
#: in a by-id name. Everything systemd would split or expand in an
#: ``ExecStart=`` — whitespace, quotes, ``\``, ``$``, ``%`` — is excluded by
#: construction, and ``..`` is refused separately (D-073 §4).
RIG_DEVICE = re.compile(r"^/dev/[A-Za-z0-9#+\-.:=@_/]+$")

#: A mirror is fetched over these. Plain http is allowed on purpose: the
#: content is public data and the check is the hash, not the transport.
#: ``file`` is a Bunker export directory, read only by
#: :class:`hammunition.mirror_transport.MirrorTransport`; the publisher
#: transport (:class:`hammunition.fetch.UrllibTransport`) speaks http(s) alone.
MIRROR_SCHEMES = ("http", "https", "file")


def _file_mirror_problem(value: str, parts: urllib.parse.SplitResult) -> str | None:
    """Why *value* is not a ``file:///absolute/dir`` export, or ``None``.

    The path is matched raw by the transport and walked by its decoded
    segments, so the decoded form is what is checked for ``.``, ``..`` and
    control characters; it is decoded once, never twice."""
    if parts.netloc:
        return "a file mirror names no host: write file:///absolute/path"
    if parts.query or parts.fragment or "?" in value or "#" in value:
        return "it has a query or a fragment; a mirror is a base URL"
    decoded = urllib.parse.unquote(parts.path)
    if not parts.path.startswith("/") or not decoded.strip("/"):
        return "a file mirror needs an absolute, non-empty directory path"
    if any(c.isspace() for c in value) or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return "it contains whitespace or a control character"
    if any(segment in (".", "..") for segment in decoded.split("/")):
        return "its path has a . or .. segment"
    return None


def _check_mirror(url: str) -> str:
    """*url* stripped, or a StationError saying what a mirror URL must be.

    The shape a base URL needs and nothing more: whether the host is on the
    LAN cannot be decided from a name without resolving it, and is the
    operator's statement to make (D-070). A user or password is refused,
    because the station file is no place for a credential."""
    value = url.strip()
    shown = redact_mirror_url(value)
    problem = None
    parts: urllib.parse.SplitResult | None = None
    try:
        parts = urllib.parse.urlsplit(value)
        _ = parts.port  # read for its ValueError on a bad port
    except ValueError:
        # urlsplit's own message can quote the whole authority, user and password
        # included, so it is neither shown nor kept as a cause or context: the
        # error is raised below, outside this handler.
        parts = None
    if parts is None:
        raise StationError(
            f"mirror {shown!r} is not usable: it is not a valid URL (or its port is not a "
            f"number). Expected a LAN address such as http://bunker.lan:8080/ (D-070)."
        )
    if parts.scheme not in MIRROR_SCHEMES:
        problem = "it must start with http://, https:// or file://"
    elif parts.scheme == "file":
        problem = _file_mirror_problem(value, parts)
    elif not parts.hostname or any(c.isspace() for c in value):
        problem = "it names no host"
    elif parts.username is not None or parts.password is not None:
        problem = "it carries a user or password, and the station file holds no credentials"
    elif parts.query or parts.fragment or "?" in value or "#" in value:
        problem = "it has a query or a fragment; a mirror is a base URL"
    if problem is not None:
        raise StationError(
            f"mirror {shown!r} is not usable: {problem}. Expected a LAN address such as "
            f"http://bunker.lan:8080/ (D-070)."
        )
    return value


@dataclass(frozen=True)
class Station:
    """What a manifest's `{station.*}` templates can reference.

    Every field is optional. A station being partly known is the normal case
    and is what makes deferral possible: `callsign` alone is enough for several
    manifests, and nothing needs all of them.
    """

    callsign: str | None = None
    grid_square: str | None = None
    node_alias: str | None = None
    """A short name a packet node answers to, distinct from the callsign."""
    map_regions: tuple[str, ...] = ()
    """Geofabrik region paths to carry offline maps for, e.g.
    ``north-america/us/vermont``. Several at once; none means the map data
    units are deferred (D-035)."""
    map_freshness: str | None = None
    """``yearly`` (the default when unset), ``monthly`` or ``latest``."""
    reference_books: tuple[str, ...] = ()
    """Kiwix book ids from ``catalog/data/kiwix-books.yaml``, e.g.
    ``ham.stackexchange.com_en_all``. None means ``kiwix-library`` is
    deferred (D-066). Which books somebody reads is not where they are, so
    these are printed where map regions are only counted."""
    mirror_require_hardware_key: bool = False
    mirror: str | None = None
    """A LAN mirror of the catalog's data artifacts, tried before the
    publisher and verified the same way (D-070). Never an internet address."""
    rig: str | None = None
    """The station's radio: a catalog device id, or ``hamlib:<model>`` for a
    radio with no manifest (D-073 §4). Which decides the user service."""
    rig_device: str | None = None
    """The serial port the rig (or its interface) is reached on, an absolute
    ``/dev/`` path — a ``/dev/serial/by-id/`` one for stability."""
    rig_baud: int | None = None
    """The CAT serial speed, for a CAT rig. Never defaulted: a wrong speed is
    silence, not an error, so the operator reads it from the radio's menu."""
    rig_ptt_line: str | None = None
    """For a PTT-only rig: ``rts``, ``dtr`` or ``vox`` — which line keys it, or
    that it keys itself on audio. Refused for a CAT rig (its PTT is CAT)."""
    rig_owner: str | None = None
    """``rigctld`` (the shared daemon, the default when unset) or ``flrig``."""
    dem_source: str | None = None
    """``copernicus`` (the default when unset) or ``3dep``: the elevation
    QMapShack's hillshade, slope and contours are drawn from (D-068,
    amended 2026-10-01)."""
    topo_radius_km: int | None = None
    """How far from the grid square's centre a US Topo sheet (and an FSTopo
    sheet, and a 3DEP tile) is still selected; 100 when unset, 0 for none
    (D-068, amended 2026-10-02, issue #232)."""
    topo_regions: tuple[str, ...] = ()
    """A subset of :attr:`map_regions` the topographic selection is narrowed to."""
    topo_all: bool | None = None
    """Every sheet of every region, as before the bound; always disclosed with
    its size and asked for by a typed ``yes``."""
    active_areas: tuple[str, ...] | None = None
    """The areas drawn and registered (D-082): US state codes (``OH``, or a RepeaterBook
    ``state_id``) and map region names. None, the default, means everything loaded is
    active, as before the switch; an empty tuple means none is (``maps activate --none``).
    Nothing is deleted either way."""
    secrets_doppler_project: str | None = None
    """The Doppler project a keyed download's secret is read from
    (:func:`hammunition.secrets.resolve_secret`, D-081). A name, never a token."""
    secrets_doppler_config: str | None = None
    """The Doppler config within that project (``dev``, ``prd``). A name."""

    def __post_init__(self) -> None:
        if self.callsign is not None:
            normalised = self.callsign.strip().upper()
            if not CALLSIGN.match(normalised):
                raise StationError(
                    f"callsign {self.callsign!r} does not look like a callsign. "
                    f"Expected something like M0ABC, W1AW/4 or VK2XYZ — letters, "
                    f"digits and optional /suffix, no spaces."
                )
            object.__setattr__(self, "callsign", normalised)
        if self.grid_square is not None:
            normalised = self.grid_square.strip()
            canonical = normalised[:2].upper() + normalised[2:4] + normalised[4:].lower()
            if not GRID_SQUARE.match(canonical.upper()):
                raise StationError(
                    f"grid square {self.grid_square!r} is not a Maidenhead locator. "
                    f"Expected four or six characters like IO91 or IO91wm."
                )
            object.__setattr__(self, "grid_square", canonical)
        if self.node_alias is not None:
            alias = self.node_alias.strip().upper()
            if not alias or len(alias) > 6 or not alias.isalnum():
                raise StationError(
                    f"node alias {self.node_alias!r} must be one to six alphanumeric "
                    f"characters — packet node aliases are short by protocol."
                )
            object.__setattr__(self, "node_alias", alias)
        regions = tuple(r.strip() for r in self.map_regions)
        for region in regions:
            if not REGION.fullmatch(region):
                raise StationError(
                    f"map region {region!r} is not a Geofabrik region path "
                    f"(lowercase words joined by '/', e.g. north-america/us/vermont). "
                    f"`hammunition maps regions` lists them."
                )
        object.__setattr__(self, "map_regions", regions)
        books = tuple(b.strip() for b in self.reference_books)
        for book in books:
            if not BOOK_ID.fullmatch(book):
                raise StationError(
                    f"reference book {book!r} is not a Kiwix book id (lowercase, no spaces, "
                    f"e.g. ham.stackexchange.com_en_all). `hammunition reference books` "
                    f"lists them."
                )
        object.__setattr__(self, "reference_books", tuple(dict.fromkeys(books)))
        if self.map_freshness is not None and self.map_freshness not in FRESHNESS:
            raise StationError(
                f"map freshness {self.map_freshness!r} is not one of {', '.join(FRESHNESS)}"
            )
        if not isinstance(self.mirror_require_hardware_key, bool):
            raise StationError("mirror_require_hardware_key must be true or false")
        if self.mirror is not None:
            object.__setattr__(self, "mirror", _check_mirror(self.mirror))
        if self.rig is not None:
            value = self.rig.strip()
            if not RIG_VALUE.match(value):
                raise StationError(
                    f"rig {self.rig!r} is not a catalog device id or a hamlib:<model> value. "
                    f"`hammunition station set --rig <device>` takes a rig from the catalog, "
                    f"or hamlib:<number> for a radio with no manifest."
                )
            object.__setattr__(self, "rig", value)
        if self.rig_device is not None:
            device = self.rig_device.strip()
            # `..` is refused only as a whole path *segment* (traversal), not as
            # a substring: a by-id name legitimately carries runs of dots, and
            # the documentation placeholder elides the serial as `...`.
            traversal = ".." in device.split("/")
            if traversal or not RIG_DEVICE.match(device):
                raise StationError(
                    f"rig device {self.rig_device!r} must be an absolute /dev/ path with no "
                    f"'..', whitespace, quotes or shell characters — it becomes part of the "
                    f"service's command line. A /dev/serial/by-id/ path is best."
                )
            object.__setattr__(self, "rig_device", device)
        if self.rig_baud is not None:
            try:
                baud = int(self.rig_baud)
            except (TypeError, ValueError) as exc:
                raise StationError(f"rig baud {self.rig_baud!r} is not a number") from exc
            if baud <= 0:
                raise StationError(f"rig baud {self.rig_baud!r} must be a positive integer")
            object.__setattr__(self, "rig_baud", baud)
        if self.rig_ptt_line is not None:
            line = self.rig_ptt_line.strip().lower()
            if line not in PTT_LINES:
                raise StationError(
                    f"rig PTT line {self.rig_ptt_line!r} must be one of {', '.join(PTT_LINES)}"
                )
            object.__setattr__(self, "rig_ptt_line", line)
        if self.rig_owner is not None:
            owner = self.rig_owner.strip().lower()
            if owner not in RIG_OWNERS:
                raise StationError(
                    f"rig owner {self.rig_owner!r} must be one of {', '.join(RIG_OWNERS)}"
                )
            object.__setattr__(self, "rig_owner", owner)
        if self.dem_source is not None and self.dem_source not in DEM_SOURCES:
            raise StationError(
                f"dem source {self.dem_source!r} is not one of {', '.join(DEM_SOURCES)}"
            )
        if self.topo_radius_km is not None:
            try:
                if isinstance(self.topo_radius_km, bool) or self.topo_radius_km != int(
                    self.topo_radius_km
                ):
                    raise ValueError
                radius = int(self.topo_radius_km)
            except (TypeError, ValueError) as exc:
                raise StationError(
                    f"topo radius {self.topo_radius_km!r} is not a whole number of kilometres"
                ) from exc
            if not 0 <= radius <= MAX_TOPO_RADIUS_KM:
                raise StationError(
                    f"topo radius {radius} km is outside 0 to {MAX_TOPO_RADIUS_KM}; "
                    f"0 selects no sheets, and --topo-all selects every sheet"
                )
            object.__setattr__(self, "topo_radius_km", radius)
        topo = tuple(dict.fromkeys(r.strip() for r in self.topo_regions))
        for region in topo:
            if not REGION.fullmatch(region):
                raise StationError(f"topo region {region!r} is not a Geofabrik region path")
        outside = [r for r in topo if r not in self.map_regions]
        if outside:
            raise StationError(
                f"--topo-regions must be a subset of the station's map regions; "
                f"{len(outside)} of the {len(topo)} given "
                f"{'is' if len(outside) == 1 else 'are'} not one ({', '.join(outside)}). "
                f"Set it with --map-regions first, or give a region already set."
            )
        object.__setattr__(self, "topo_regions", topo)
        if self.topo_all is not None and not isinstance(self.topo_all, bool):
            raise StationError(f"topo_all {self.topo_all!r} must be true or false")
        if self.active_areas is not None:
            object.__setattr__(
                self,
                "active_areas",
                tuple(dict.fromkeys(parse_area(a) for a in self.active_areas)),
            )
        for label, doppler_name in (
            ("doppler project", self.secrets_doppler_project),
            ("doppler config", self.secrets_doppler_config),
        ):
            if doppler_name is not None and not DOPPLER_NAME.match(doppler_name):
                raise StationError(
                    f"{label} {doppler_name!r} must be a name of letters, digits, '.', '_' or '-', "
                    f"starting with a letter or digit: it is an argument of the one "
                    f"`doppler secrets get` command, never a token."
                )

    @property
    def topo_radius(self) -> int:
        """The effective radius in kilometres: what is stored, or 100."""
        return 100 if self.topo_radius_km is None else self.topo_radius_km

    @property
    def elevation(self) -> str:
        """The effective elevation source: what is stored, or ``copernicus``."""
        return self.dem_source or "copernicus"

    @property
    def freshness(self) -> str:
        """The effective freshness: what is stored, or ``yearly`` if unset."""
        return self.map_freshness or "yearly"

    def get(self, variable: str) -> str | None:
        """The value a `{station.<variable>}` reference resolves to, or None.

        A derived variable (:data:`DERIVED`) is computed from the stored value
        it names, and is None when that value is unset *or* cannot give it --
        :meth:`missing` and :meth:`unusable` say which.
        """
        if variable in DERIVED:
            source, derive, _why = DERIVED[variable]
            stored = self.get(source)
            return derive(stored) if stored else None
        return getattr(self, variable, None) if variable in STATION_FIELDS else None

    def missing(self, variables: set[str]) -> tuple[str, ...]:
        """Of *variables*, the stored values this station does not have.

        Named as the operator sets them: a derived variable reports the value
        it comes from (``latitude`` needs ``grid_square``), because that is
        what ``station set`` and the install prompt can ask for.
        """
        out: set[str] = set()
        for variable in variables:
            source = DERIVED[variable][0] if variable in DERIVED else variable
            if not self.get(source):
                out.add(source)
        return tuple(sorted(out))

    def unusable(self, variables: set[str]) -> tuple[str, ...]:
        """Why a derived variable cannot be had although its source is set.

        One sentence per variable, sorted. Empty when every derived variable
        in *variables* either resolves or is simply :meth:`missing`.
        """
        reasons: set[str] = set()
        for variable in variables:
            if variable not in DERIVED:
                continue
            source, _derive, why = DERIVED[variable]
            stored = self.get(source)
            if stored and self.get(variable) is None:
                reasons.add(why.format(value=stored))
        return tuple(sorted(reasons))

    def as_dict(self) -> dict[str, str | int | list[str]]:
        # `rig_baud` is an int, so it is excluded from the string comprehension
        # and added explicitly below, as the map and mirror fields are.
        result: dict[str, str | int | list[str]] = {
            f.name: v
            for f in fields(self)
            if f.name not in _NOT_TEMPLATES
            and f.name != "rig_baud"
            and (v := getattr(self, f.name))
        }
        if self.map_regions:
            result["map_regions"] = list(self.map_regions)
        if self.map_freshness is not None:
            result["map_freshness"] = self.map_freshness
        if self.reference_books:
            result["reference_books"] = list(self.reference_books)
        if not isinstance(self.mirror_require_hardware_key, bool):
            raise StationError("mirror_require_hardware_key must be true or false")
        if self.mirror_require_hardware_key:
            result["mirror_require_hardware_key"] = True
        if self.mirror is not None:
            result["mirror"] = self.mirror
        if self.rig_baud is not None:
            result["rig_baud"] = self.rig_baud
        if self.dem_source is not None:
            result["dem_source"] = self.dem_source
        if self.secrets_doppler_project is not None:
            result["secrets_doppler_project"] = self.secrets_doppler_project
        if self.secrets_doppler_config is not None:
            result["secrets_doppler_config"] = self.secrets_doppler_config
        if self.topo_radius_km is not None:
            result["topo_radius_km"] = self.topo_radius_km
        if self.topo_regions:
            result["topo_regions"] = list(self.topo_regions)
        if self.topo_all is not None:
            result["topo_all"] = self.topo_all
        if self.active_areas is not None:
            result["active_areas"] = list(self.active_areas)
        return result


#: The variables a manifest may reference. Kept beside the dataclass so a
#: template naming something unknown is a reportable error rather than an
#: empty substitution. Map settings, the mirror and the books are excluded -- they are
#: read directly by the engine, never templated into a config file.
STATION_FIELDS: frozenset[str] = frozenset(f.name for f in fields(Station)) - _NOT_TEMPLATES

#: An AX.25 address: one to six letters and digits. The SSID is a separate
#: field of the frame, and a ``/P`` or ``W1AW/4`` suffix cannot be carried at
#: all -- Direwolf's example config says "up to 6 letters and digits with an
#: optional ssid", and ``axports`` and aprx take the same address.
AX25_CALLSIGN = re.compile(r"^[A-Z0-9]{1,6}$")


def _ax25(callsign: str) -> str | None:
    return callsign if AX25_CALLSIGN.match(callsign) else None


def _latitude(grid: str) -> str:
    return f"{centre(grid)[0]:.4f}"


def _longitude(grid: str) -> str:
    return f"{centre(grid)[1]:.4f}"


#: Template variables computed from a stored value rather than stored. Each is
#: ``(stored value it comes from, how, why it can be unavailable when that
#: value is set)``. Nothing is invented: a derivation that cannot be made
#: defers the file (D-035), with the reason, exactly as an unset value does.
#: ``latitude``/``longitude`` are the centre of the grid square, to four
#: decimal places (gpredict's own sample file uses four); the precision is the
#: square's, never more (:mod:`hammunition.maidenhead`).
DERIVED: dict[str, tuple[str, Callable[[str], str | None], str]] = {
    "ax25_callsign": (
        "callsign",
        _ax25,
        "the callsign {value} is not an AX.25 address (one to six letters and digits, "
        "no /suffix), so AX.25 and APRS cannot carry it",
    ),
    "latitude": ("grid_square", _latitude, "latitude cannot be derived from {value}"),
    "longitude": ("grid_square", _longitude, "longitude cannot be derived from {value}"),
}

#: Every name a ``{station.<name>}`` reference may use: stored and derived.
TEMPLATE_VARIABLES: frozenset[str] = STATION_FIELDS | frozenset(DERIVED)

#: Every value the station file may hold, template variable or not -- what
#: `load_station` accepts without raising "sets values nothing can use".
_KNOWN_KEYS: frozenset[str] = frozenset(f.name for f in fields(Station))

PROMPTS: dict[str, str] = {
    "callsign": "Your callsign (transmitted, so it must be yours)",
    "grid_square": "Your Maidenhead grid square, four or six characters",
    "node_alias": "Short alias for a packet node, up to six characters",
}


def config_path(owner: str | None = None) -> Path:
    """Where station values live. Outside the repository, by construction."""
    return (
        owner_aware_dir(
            xdg_var="XDG_CONFIG_HOME",
            home_relative=(".config",),
            owner=owner,
        )
        / "station.yml"
    )


def load_station(path: Path | None = None, owner: str | None = None) -> Station:
    """Read the station file, or return an empty station if there is none.

    An absent file is not an error: an operator who has never set a callsign is
    the starting state, not a fault.
    """
    target = path or config_path(owner)
    if not target.exists():
        return Station()
    try:
        data = yaml.safe_load(target.read_text()) or {}
    except yaml.YAMLError as exc:
        raise StationError(f"{target} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise StationError(f"{target} must contain a mapping, not {type(data).__name__}")
    unknown = sorted(str(key) for key in set(data) - _KNOWN_KEYS)
    if unknown:
        raise StationError(
            f"{target} sets values nothing can use: {', '.join(unknown)}. "
            f"Known values: {', '.join(sorted(_KNOWN_KEYS))}."
        )

    def _str(key: str) -> str | None:
        value = data.get(key)
        return str(value) if value is not None else None

    def _str_list(key: str) -> tuple[str, ...]:
        # A missing key is an empty list; anything but a list is refused by name. An
        # int raised TypeError here and a bare string would have iterated its
        # characters (found by the station-config fuzz target, #339).
        value = data.get(key)
        if value is None:
            return ()
        if not isinstance(value, list):
            raise StationError(f"{target}: {key} must be a list, not {type(value).__name__}")
        return tuple(str(item) for item in value)

    raw_baud = data.get("rig_baud")
    try:
        rig_baud = int(raw_baud) if raw_baud is not None else None
    except (TypeError, ValueError) as exc:
        raise StationError(f"{target}: rig_baud {raw_baud!r} is not a number") from exc
    raw_radius = data.get("topo_radius_km")
    try:
        topo_radius = int(raw_radius) if raw_radius is not None else None
    except (TypeError, ValueError) as exc:
        raise StationError(f"{target}: topo_radius_km {raw_radius!r} is not a number") from exc
    topo_all = data.get("topo_all")
    if topo_all is not None and not isinstance(topo_all, bool):
        raise StationError(f"{target}: topo_all {topo_all!r} must be true or false")
    active_areas = data.get("active_areas")
    if active_areas is not None and not isinstance(active_areas, list):
        raise StationError(f"{target}: active_areas {active_areas!r} must be a list")
    return Station(
        callsign=_str("callsign"),
        grid_square=_str("grid_square"),
        node_alias=_str("node_alias"),
        map_regions=_str_list("map_regions"),
        map_freshness=_str("map_freshness"),
        reference_books=_str_list("reference_books"),
        mirror=_str("mirror"),
        mirror_require_hardware_key=data.get("mirror_require_hardware_key", False),
        rig=_str("rig"),
        rig_device=_str("rig_device"),
        rig_baud=rig_baud,
        rig_ptt_line=_str("rig_ptt_line"),
        rig_owner=_str("rig_owner"),
        dem_source=_str("dem_source"),
        topo_radius_km=topo_radius,
        topo_regions=_str_list("topo_regions"),
        topo_all=topo_all,
        active_areas=tuple(str(a) for a in active_areas) if active_areas is not None else None,
        secrets_doppler_project=_str("secrets_doppler_project"),
        secrets_doppler_config=_str("secrets_doppler_config"),
    )


def save_station(station: Station, path: Path | None = None, owner: str | None = None) -> Path:
    """Write the station file, creating its directory. Returns the path."""
    target = path or config_path(owner)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Station-local values, used to fill in configuration this catalog\n"
        "# writes on your behalf. Written by `hammunition station set`.\n"
        "#\n"
        "# Your callsign is transmitted. It identifies you and it must be yours.\n"
        + yaml.safe_dump(station.as_dict(), sort_keys=True, default_flow_style=False)
    )
    target.write_text(body)
    os.chmod(target, 0o600)
    return target


def prompt_for(variables: Sequence[str], station: Station) -> Station:
    """Ask for the values *variables* names that *station* does not have.

    Returns a new Station. Only called when standard input is a terminal --
    a non-interactive run defers instead, which is the whole point.
    """
    # Map settings and the mirror are not template variables (`variable` only ever names one
    # of STATION_FIELDS -- `station.get` gates on that), so they are carried
    # through unchanged rather than passed through this dict of strings.
    # `rig_baud` is the one STATION_FIELD that is an int, so it is carried
    # separately rather than through this string dict.
    values: dict[str, str] = {
        f: v for f in STATION_FIELDS if f != "rig_baud" and (v := station.get(f)) is not None
    }
    for variable in variables:
        if station.get(variable):
            continue
        question = PROMPTS.get(variable, f"Value for {variable}")
        while True:
            answer = input(f"{question}: ").strip()
            if not answer:
                print("  (skipped — the configuration needing it will not be written)")
                break
            try:
                Station(
                    map_regions=station.map_regions,
                    map_freshness=station.map_freshness,
                    reference_books=station.reference_books,
                    mirror=station.mirror,
                    mirror_require_hardware_key=station.mirror_require_hardware_key,
                    rig_baud=station.rig_baud,
                    dem_source=station.dem_source,
                    topo_radius_km=station.topo_radius_km,
                    topo_regions=station.topo_regions,
                    topo_all=station.topo_all,
                    active_areas=station.active_areas,
                    secrets_doppler_project=station.secrets_doppler_project,
                    secrets_doppler_config=station.secrets_doppler_config,
                    **{**values, variable: answer},
                )
            except StationError as exc:
                print(f"  {exc}")
                continue
            values[variable] = answer
            break
    return Station(
        map_regions=station.map_regions,
        map_freshness=station.map_freshness,
        reference_books=station.reference_books,
        mirror=station.mirror,
        mirror_require_hardware_key=station.mirror_require_hardware_key,
        rig_baud=station.rig_baud,
        dem_source=station.dem_source,
        topo_radius_km=station.topo_radius_km,
        topo_regions=station.topo_regions,
        topo_all=station.topo_all,
        active_areas=station.active_areas,
        secrets_doppler_project=station.secrets_doppler_project,
        secrets_doppler_config=station.secrets_doppler_config,
        **values,
    )


def is_interactive() -> bool:
    """Whether it is reasonable to ask a question.

    Both ends matter: a piped stdin cannot answer, and output going nowhere
    visible means the question is never seen.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()
