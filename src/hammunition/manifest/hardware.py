# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

"""Device catalog schema.  D-020.

D-020 says detected hardware drives profile resolution. This is the data that
makes that possible: what a device *is*, how the kernel sees it, what groups and
udev rules it needs, and which packages make it useful.

One invariant the type system enforces, for the same reason ``RemoteArtifact``
requires a checksum: **a USB identifier cannot be asserted without saying where
it came from.** A wrong VID:PID produces a udev rule that silently never
matches, and the operator has no way to tell that from a broken cable. So every
``UsbId`` carries evidence, and a device with no confirmed identifier must say
so in ``identification_gap`` rather than ship a guess.
"""

from __future__ import annotations

import re
from datetime import date as date_
from typing import Literal

from pydantic import Field, model_validator

from .schema import SLUG, ManifestError, Strict

__all__ = [
    "AmbiguityBasis",
    "DeviceClass",
    "DeviceManifest",
    "Firmware",
    "GapClosure",
    "MaintainerVerification",
    "NodeKind",
    "PowerControl",
    "PowerMethod",
    "PttLine",
    "QuietVerb",
    "RejectedId",
    "ResumeControl",
    "ResumeStep",
    "RigAudio",
    "RigBlock",
    "RigCat",
    "RigInterfaceKind",
    "RigPtt",
    "RigPttOnly",
    "UdevBinding",
    "UsbAmbiguity",
    "UsbId",
]

HEX4 = re.compile(r"^[0-9a-f]{4}$")

# Evidence that asserts, in the present tense, that the identifier is NOT
# confirmed -- which would contradict `confirmed: true`.
#
# This was a bare `"unconfirmed" in evidence` substring test, and it rejected a
# perfectly good entry whose evidence recorded its own history: "previously
# carried as unconfirmed" is not a contradiction, it is exactly the sentence a
# gap-closing capture should write. A check that punishes the most informative
# prose is a check that teaches people to write less of it.
CONTRADICTS_CONFIRMED = re.compile(
    r"\b(?:is|are|remains?|stays?)\s+unconfirmed\b"
    r"|\bunconfirmed\s+(?:here|as\s+of|by\s+us)\b"
    r"|\bnot\s+(?:confirmed|verified)\b",
    re.IGNORECASE,
)
UDEV_MODE = re.compile(r"^0[0-7]{3}$")


GapClosure = Literal["maintainer_hardware", "unverified_by_maintainer", "not_applicable"]
"""Who can close an ``identification_gap``, and how.

``identification_gap`` records *what* is unknown. It does not record whether
anyone is in a position to find out, and every gap in the catalog was written
ending in some variant of "run lsusb and record it" — advice that is useless
when nobody on the project owns the device. Two entries (LimeSDR, PlutoSDR)
told the reader to attach hardware that does not exist here.

So the disposition is structural:

``maintainer_hardware``
    The device is in the maintainer's kit. One ``lsusb`` closes it; no
    contribution is needed and none should be solicited.
``unverified_by_maintainer``
    We carry the entry because other operators have the device; the maintainer
    does not, so it cannot be verified here and the gap stays open on purpose.
    Named for *why* it is open rather than "pending", which implies someone is
    getting to it. This is the standing treatment for anything the catalog
    supports but cannot test.
``not_applicable``
    No single identifier exists to record — the entry is a host computer, or a
    family of boards that enumerate several different ways. ``lsusb`` against
    one unit would not close it, and treating it as an open task would keep it
    permanently open.
"""


AmbiguityBasis = Literal[
    "distribution_disabled",
    "kernel_generic_driver",
    "shared_across_products",
    "vendor_chip_default",
    "generic_function_name",
]
"""Why an identifier does not identify a *device*.  D-028.

Five evidence classes, in descending order of how citable they are:

``distribution_disabled``
    A distribution shipped a rule for the pair and **commented it out**, saying
    why. The strongest evidence there is, because it is not an inference from a
    driver table or a name -- it is a maintainer stating the conclusion and
    acting on it. Debian's ``60-gpsd.rules`` carries five, each marked "rule
    disabled in Debian as it matches too many other devices": ``0403:6001``,
    ``10c4:ea60``, ``10c4:ea71`` and ``067b:2303`` twice. Those five rows --
    four identifiers, all gpsd's -- are the archive-wide total: a rule counts
    only when it is commented out *with a stated reason*. The raw sweep shows
    more rows from disabled rules, and the first version of this docstring
    repeated the wrong reading D-028's amendment retracts: it said the sweep
    finds 13 and that ``dfu-util`` disables ``0483:df11``, when dfu-util's
    rule for that pair is live and the commented line under it is an
    alternative ``plugdev`` form for older systems.
``kernel_generic_driver``
    The kernel's own ``modules.alias`` binds the pair to a general-purpose
    USB-serial driver -- ``cp210x``, ``ch341``, ``ftdi_sio``, ``pl2303``. The
    kernel maintainers put it in a *bridge* driver's table, which is as close to
    an authoritative statement of "this is a chip, not a product" as exists.
``shared_across_products``
    The archive sweep found the pair in two or more packages' rules naming
    different devices. ``0483:df11`` is in both ``qflipper``'s rules and
    ``dmrconfig``'s, where it is a TYT MD-UV380.
``vendor_chip_default``
    A documented chip-level constant in vendor tooling, shared by every board
    using that silicon -- Espressif's ``303a:1001``, which esptool calls
    ``USB_JTAG_SERIAL_PID``.
``generic_function_name``
    ``usb.ids`` names a function rather than a product: "CP210x UART Bridge",
    "Virtual COM Port", "STM Device in DFU Mode". Weakest of the four and still
    evidence, because that database is written by people looking at descriptors.
"""


class UsbAmbiguity(Strict):
    """Evidence that a VID:PID names a chip or a function, not a device.

    The counterpart to ``UsbId.evidence``. That field stops us asserting an
    identifier we have not seen; this one stops us *acting* on an identifier
    that is real and does not mean what a udev rule would take it to mean.

    Both failures are silent, and they are mirror images. Under-matching: the
    ``rtl-sdr`` entry carried three identifiers where Debian carries 42, so a
    Hauppauge stick got no symlink and no error. Over-matching: a symlink on
    ``10c4:ea60`` names every CP2102 adapter on the machine -- a rig-control
    cable, a GPS receiver -- after whichever badge the rule was written for.
    """

    basis: AmbiguityBasis
    evidence: str = Field(
        min_length=20,
        description="Where this came from: a modules.alias line, a sweep result, a vendor constant.",
    )
    also_used_by: list[str] = Field(
        default_factory=list,
        description="Other devices or packages known to share the pair, where known.",
    )


NodeKind = Literal["serial", "libusb", "storage", "hid", "network", "sound"]
"""What the kernel makes of this interface, which decides how it can be named.

Recorded because the naming accounting cannot be done without it, and the
accounting overturned this project's stated priority. ``/dev/serial/by-id/`` is
systemd's, is already correct, and needs nothing from us -- but it exists only
for ``serial``. An ``libusb`` device has no ``/dev/serial/`` entry at all, and
most of this catalog is ``libusb``.

``serial``
    A USB-serial interface: ``cdc_acm``, ``ftdi_sio``, ``cp210x``, ``ch341``.
    Gets a ``/dev/ttyUSB*`` or ``/dev/ttyACM*``, and a ``/dev/serial/by-id/``
    path composed by systemd from the descriptor strings.
``libusb``
    Claimed by no kernel driver and opened directly through libusb: every SDR
    here, the Ubertooth, the Proxmark in client mode. There is no ``/dev`` node
    but the bus device, so permissions are the whole problem and by-id does not
    apply.
``storage``, ``hid``, ``network``, ``sound``
    Claimed by a class driver. Named by their own subsystem's rules; nothing
    here should be writing symlinks for them.
"""


class UsbId(Strict):
    """One USB vendor/product pair, with its provenance.

    ``product`` may be None to match every product of a vendor — occasionally
    correct (a vendor with one product line) and usually lazy, so it needs the
    same evidence as anything else.

    On a composite device this is one *interface* rather than one device, which
    is why it carries ``node_kind``, ``ports`` and ``port_roles``: a Free-WiLi 2
    is six of these behind an internal hub, and four of its ports live on one.
    """

    vendor: str = Field(description="4 hex digits, lowercase.")
    product: str | None = None
    description: str = Field(min_length=3)
    evidence: str = Field(
        min_length=10,
        description=(
            "Where this identifier came from: a distribution udev rule, an "
            "upstream source file, a URL, or an lsusb capture from real hardware."
        ),
    )
    confirmed: bool = True
    ambiguity: UsbAmbiguity | None = Field(
        default=None,
        description="Set when the pair names a chip or a function rather than a device. D-028.",
    )
    node_kind: NodeKind | None = Field(
        default=None,
        description="What /dev node the kernel makes. None means nobody has recorded it.",
    )
    ports: int = Field(
        default=1,
        ge=1,
        description="How many nodes this one interface presents. >1 only makes sense for serial.",
    )
    port_roles: list[str] = Field(
        default_factory=list,
        description=(
            "What each port is for, in interface order. Empty means unknown, "
            "which is a documented gap on a multi-port device rather than a "
            "detail: a stable path to a port you cannot identify is not an answer."
        ),
    )
    product_string: str | None = Field(
        default=None,
        description=(
            "ATTRS{product} as actually read from the descriptor. The source of "
            "truth for any udev match_product -- writing one nobody has read is "
            "the same defect as guessing a VID:PID, pointed the other way."
        ),
    )
    reports_serial: bool | None = Field(
        default=None,
        description=(
            "Whether the device supplies a per-unit serial. False is a finding, "
            "not a blank: it means two of them collide in /dev/serial/by-id/ too."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> UsbId:
        for value, field in ((self.vendor, "vendor"), (self.product, "product")):
            if value is not None and not HEX4.match(value):
                raise ManifestError(f"USB {field} {value!r} must be 4 lowercase hex digits")
        if self.confirmed and CONTRADICTS_CONFIRMED.search(self.evidence):
            raise ManifestError(
                f"USB id {self.vendor}:{self.product} is marked confirmed but its "
                f"evidence asserts it is not"
            )
        if self.ports > 1 and self.node_kind not in (None, "serial"):
            raise ManifestError(
                f"USB id {self.vendor}:{self.product} claims {self.ports} ports on a "
                f"{self.node_kind} interface; only serial interfaces multiplex ports"
            )
        if self.port_roles and len(self.port_roles) != self.ports:
            raise ManifestError(
                f"USB id {self.vendor}:{self.product} names {len(self.port_roles)} port "
                f"roles for {self.ports} ports. Name all of them or none — a partial "
                f"map reads as a complete one."
            )
        return self

    @property
    def by_id_reachable(self) -> bool:
        """Whether systemd's 60-serial.rules gives this a /dev/serial/by-id/ path."""
        return self.node_kind == "serial"

    @property
    def by_id_distinguishes_units(self) -> bool | None:
        """Whether that path separates two of these attached at once.

        None when unrecorded. False is the Proxmark case: by-id composes from
        manufacturer, product and serial, so a device supplying no serial
        collides there exactly as a naive symlink would.
        """
        if not self.by_id_reachable:
            return False
        return self.reports_serial

    def __str__(self) -> str:
        return f"{self.vendor}:{self.product or '*'}"


class RejectedId(Strict):
    """An identifier considered and deliberately not carried.

    A class propagates every identifier it holds to every device that joins it,
    so an unconfirmed one in a class is not an isolated guess -- it is a guess
    with a distribution mechanism. Two flashed boards proved the point: the
    CatSniffer v3 turned out to be a bare RP2040 with no ESP32 in it, and the C5
    Wardriver a CH343 bridge rather than the native USB the class predicted.
    Both times ``badgelife`` had supplied identifiers that could not match.

    So a class carries only confirmed identifiers, and this is where the
    instructive failures live instead. Negative evidence is worth keeping --
    "we looked at this pair and here is why it is not ours" stops the next
    person re-adding it -- but it must live somewhere that cannot generate a
    rule or be inherited by a device.
    """

    vendor: str = Field(description="4 hex digits, lowercase.")
    product: str | None = None
    description: str = Field(min_length=3)
    assumed_to_be: str = Field(
        min_length=10, description="What it was believed to identify when it was added."
    )
    why_rejected: str = Field(
        min_length=40,
        description="What the evidence turned out to be, and what it cost to find out.",
    )

    @model_validator(mode="after")
    def _check(self) -> RejectedId:
        for value, field in ((self.vendor, "vendor"), (self.product, "product")):
            if value is not None and not HEX4.match(value):
                raise ManifestError(
                    f"rejected USB {field} {value!r} must be 4 lowercase hex digits"
                )
        return self

    def __str__(self) -> str:
        return f"{self.vendor}:{self.product or '*'}"


class MaintainerVerification(Strict):
    """Evidence that someone here actually ran the hardware.

    `status` and this field answer two different questions and are deliberately
    separate:

    ``status: supported``
        The identifiers are right and the install recipe works. `usrp` claims
        this on the strength of Debian's own `uhd-host` udev rule -- a primary
        source -- while nobody on this project owns a USRP. That is a real,
        useful claim and discarding it because we lack the hardware would throw
        away good evidence.
    ``maintainer_verified``
        Somebody plugged it in. A different claim entirely.

    Conflating the two is how projects come to claim support they have never
    tested, which is the failure D-018 and D-025 both exist to prevent, applied
    to hardware instead of to prose. So this is not a boolean: a bare `true`
    would be a claim with no evidence behind it, which is the same defect one
    level down.
    """

    date: date_
    by: str = Field(min_length=2, description="Who ran it. A name or handle.")
    distro: str = Field(min_length=3, description="What it was tested on, e.g. 'parrot rolling'.")
    what_was_tested: str = Field(
        min_length=25,
        description=(
            "What actually happened. 'It works' is not a test result; "
            "'enumerated, rules matched, rtl_test found the tuner' is."
        ),
    )


class UdevBinding(Strict):
    """How the device should appear in /dev.

    The whole point of the hardware role, per CLAUDE.md: a persistent name so
    plug order stops mattering and downstream configuration can reference a
    stable path.
    """

    symlink: str = Field(description="Base name under /dev, e.g. 'catsniffer'.")
    serial_suffix: bool | None = Field(
        default=None,
        description=(
            "Append the device serial, giving /dev/<symlink>-<serial>. Three "
            "states, and the third is the point.\n\n"
            "`true` -- the device supplies a per-unit serial and the symlink "
            "carries it. Requires a confirmed identifier with "
            "`reports_serial: true`; a suffix is not something to assume.\n\n"
            "`false` -- it does not, so the symlink is unsuffixed. Only valid "
            "when at most one identifier is confirmed, or two units would race "
            "for the same /dev name.\n\n"
            "`null` (the default) -- **nobody has checked.** The generator must "
            "refuse to emit a symlink rather than guess. This used to default to "
            "`true`, which meant every device silently claimed a serial nobody "
            "had read, and `serial_suffix` with nothing to append produces a "
            "broken or nondeterministic name -- the proxmark3 failure, armed by "
            "default for every device added afterwards."
        ),
    )
    match_product: str | None = Field(
        default=None,
        description=(
            'Emitted as ATTRS{product}=="..." alongside the identifiers. Required '
            "when any identifier is ambiguous, because VID:PID alone would match "
            "unrelated hardware."
        ),
    )
    match_serial: str | None = Field(
        default=None,
        description=(
            'Emitted as ATTRS{serial}=="...". Pins the rule to one physical unit; '
            "use for an operator's own device, not for a catalog-wide rule."
        ),
    )
    mode: str = "0660"
    group: str = "plugdev"
    tag_uaccess: bool = Field(
        default=True,
        description=(
            'Emit TAG+="uaccess" so the logged-in seat gets access without '
            "group membership. Group membership stays as the fallback for "
            "headless and non-systemd-logind systems."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> UdevBinding:
        if not SLUG.match(self.symlink):
            raise ManifestError(f"udev symlink {self.symlink!r} must be a lowercase slug")
        if not UDEV_MODE.match(self.mode):
            raise ManifestError(f"udev mode {self.mode!r} must be 4-digit octal, e.g. 0660")
        return self


PowerMethod = Literal["usb_deauthorize", "pci_runtime"]
"""How the engine parks a device. A name, never a command.

``usb_deauthorize`` writes ``0`` to the device's ``authorized`` and ``auto`` to
its ``power/control``; the kernel drops the interfaces and every consumer sees
an ordinary unplug. ``pci_runtime`` is reserved for MHI/PCIe cards and is
refused at runtime until a card has proved it here (D-056).
"""

QuietVerb = Literal["networkmanager_autoconnect"]
"""A consumer to hush before parking and restore on wake.

A closed set for the same reason ``PowerMethod`` is one: the catalog is data,
and an open string here would be a place to put a command. Each verb is
implemented by the engine and named by the manifest.
"""


class PowerControl(Strict):
    """What it takes to park this device, and what the operator will observe.

    The whole point of the fixed enums is that a manifest cannot describe *how*
    to park something -- only *which* of the ways the engine already implements
    applies to it. A catalog that could carry a shell line would be the
    intertwined install logic this project exists to replace (D-001).
    """

    method: PowerMethod
    quiet: list[QuietVerb] = Field(
        default_factory=list,
        description="Consumers to hush before parking and restore on wake, in order.",
    )
    note: str = Field(
        min_length=10,
        description=(
            "Prose for the generated device page: what the operator will observe "
            "when this device is parked, and why nothing further is needed."
        ),
    )


RigInterfaceKind = Literal[
    "usb_cp2105_dual",
    "usb_cp210x",
    "usb_ftdi",
    "usb_cdc_acm",
    "usb_ch340",
    "bluetooth_rfcomm",
]
"""How a CAT radio's control port presents on USB (or Bluetooth)."""

RigAudio = Literal["builtin_usb_codec", "external_interface", "none"]
RigPtt = Literal["cat", "rts", "dtr", "vox"]
PttLine = Literal["rts", "dtr"]


class RigCat(Strict):
    """How a CAT radio's control port is reached and driven.  D-073 §3b.

    Every value cites where it came from in a manifest comment, as the rest of
    the hardware catalog does: ``baud`` and ``handshake`` from ``rigctl -m N
    -u``, ``kind`` from the cable, ``factory_baud`` only when read from the
    radio (so the station never defaults the speed).
    """

    kind: RigInterfaceKind
    interface: str | None = Field(
        default=None,
        description="Which -ifNN- port is CAT, when the chip presents several.",
    )
    baud: tuple[int, int] = Field(
        description="The backend's [low, high] serial-speed range, from `rigctl -m N -u`."
    )
    factory_baud: int | None = Field(
        default=None,
        description="The radio's out-of-box CAT rate, only when read from the radio.",
    )
    handshake: Literal["hardware", "software", "none"]

    @model_validator(mode="after")
    def _check(self) -> RigCat:
        low, high = self.baud
        if low <= 0 or high < low:
            raise ManifestError(
                f"rig cat baud {list(self.baud)} must be [low, high] with 0 < low <= high"
            )
        return self


class RigPttOnly(Strict):
    """A radio hamlib cannot drive: the serial lines that key it.  D-073 §3d.

    Mutually exclusive with ``hamlib_model``/``cat`` on :class:`RigBlock`: a
    radio either has a hamlib backend and a CAT port, or it has neither and
    names the lines an interface keys it by.
    """

    lines: list[PttLine] = Field(
        min_length=1,
        description="Serial control lines an interface can key it by (RTS and/or DTR).",
    )
    vox: bool = Field(
        default=False,
        description="The radio can key itself on audio (VOX) instead of a line.",
    )


class RigBlock(Strict):
    """What a radio needs from Linux and from hamlib.  D-073 §3b.

    One radio is one of two shapes: a CAT radio with a hamlib model and a
    control port, or a PTT-only radio with neither, keyed by a serial line.
    """

    hamlib_model: int | None = Field(
        default=None,
        description="`rigctl -l`; the plan checks the target's hamlib lists it.",
    )
    cat: RigCat | None = None
    ptt_only: RigPttOnly | None = None
    audio: RigAudio
    ptt: list[RigPtt] = Field(
        default_factory=list,
        description="What the radio can be keyed by; the first is the default.",
    )
    ptt_interface: str | None = Field(
        default=None,
        description="The -ifNN- port for RTS/DTR keying, where it differs from CAT.",
    )

    @property
    def kind(self) -> Literal["cat", "ptt_only"]:
        return "ptt_only" if self.ptt_only is not None else "cat"

    @model_validator(mode="after")
    def _exclusive(self) -> RigBlock:
        is_cat = self.hamlib_model is not None or self.cat is not None
        if is_cat and self.ptt_only is not None:
            raise ManifestError(
                "a rig has either a hamlib backend and a CAT port, or ptt_only, never both "
                "(D-073 §3b)"
            )
        if not is_cat and self.ptt_only is None:
            raise ManifestError(
                "a rig block must declare either hamlib_model + cat, or ptt_only (D-073 §3b)"
            )
        if is_cat and (self.hamlib_model is None or self.cat is None):
            raise ManifestError("a CAT rig needs both hamlib_model and cat (D-073 §3b)")
        return self


ResumeStep = Literal["gpsd_reopen", "modem_sim_recover"]
"""What the engine does for a device after the machine resumes. A name, never a command.

``gpsd_reopen``: a systemd oneshot ordered after the four sleep targets gives
gpsd a fresh open of each ``/dev/gpsN`` (``gpsdctl remove`` and ``add``, then
``systemctl try-restart gpsd.service`` if gpsd reports no device; no data
check, so a listed but silent receiver is not restarted), and does nothing
when no ``/dev/gpsN`` exists. Installed by ``hardware apply``, removed
by ``hardware unapply`` (issue #177, D-058 amended 2026-10-01).

``modem_sim_recover``: a systemd oneshot, ordered after the four sleep targets and
at boot, reads the SIM application state of a ``2c7c:0620`` modem and, only if it
is ``illegal``, makes one root recovery attempt (stop ModemManager, SIM power off
and on, wait for ``ready``, start ModemManager). Installed by ``hardware apply``,
removed by ``hardware unapply`` (issue #427).
"""


class ResumeControl(Strict):
    """A step the engine runs after every resume, for devices that need one.

    A closed set for the reason ``PowerMethod`` is one: the catalog names a
    step the engine implements and cannot describe how to do it.
    """

    step: ResumeStep
    note: str = Field(
        min_length=10,
        description=(
            'Prose for the generated page\'s "After suspend" section: what goes '
            "wrong without the step, what the step does, and what it leaves to the "
            "operator."
        ),
    )


class Firmware(Strict):
    """Flashing or firmware-management tooling for a device."""

    kind: Literal["dfu", "esptool", "uf2", "bootloader_button", "vendor_tool", "openocd"]
    packages: list[str] = Field(default_factory=list)
    dfu_usb_id: UsbId | None = Field(
        default=None,
        description="Many devices enumerate differently in bootloader mode.",
    )
    note: str = Field(min_length=10)


class HardwareDocumentation(Strict):
    """Required by CLAUDE.md: per-device setup is a documented deliverable."""

    what_it_is: str = Field(min_length=20)
    what_you_can_do_with_it: str = Field(min_length=20)
    setup_steps: str = Field(min_length=20)
    known_problems: str | None = None
    upstream_url: str | None = None


class _DeviceCommon(Strict):
    # `name`, `summary` and `usb_ids` live here rather than on each subclass so
    # that code handling both -- the gap report, the ambiguity cross-check, the
    # package cross-check -- can iterate a merged sequence without widening to a
    # base that knows nothing about identifiers.
    #
    # The invariant that kept `usb_ids` on the subclasses is not lost: a class
    # must carry at least one identifier and a device may carry none. It is
    # expressed where it belongs instead, as a narrowed constraint on
    # `DeviceClass.usb_ids`. Keeping the field down here cost three separate
    # workarounds in call sites before it was worth saying so.
    name: str
    summary: str
    usb_ids: list[UsbId] = Field(default_factory=list)
    rejected_ids: list[RejectedId] = Field(
        default_factory=list,
        description="Identifiers considered and not carried, with why. Cannot generate a rule.",
    )
    distribution_naming: str | None = Field(
        default=None,
        description=(
            "A persistent name a distribution package already provides, e.g. "
            "gpsd's /dev/gpsN. Recorded so the accounting can tell 'we add a "
            "name' apart from 'somebody already did'."
        ),
    )

    groups: list[str] = Field(
        default_factory=list,
        description="Group membership required for non-root access.",
    )
    packages: list[str] = Field(
        default_factory=list, description="Catalog packages that make it useful."
    )
    firmware: list[Firmware] = Field(default_factory=list)
    rig: RigBlock | None = Field(
        default=None,
        description=(
            "Present on a radio: what it needs from hamlib and from Linux. The "
            "operator's selection of this device as the station's rig is station "
            "configuration, not a property read from the hardware (D-073, D-028)."
        ),
    )
    udev: UdevBinding | None = None
    power_control: PowerControl | None = Field(
        default=None,
        description=(
            "Present when this device can be parked and woken (D-056). Absent "
            "means it appears on no power-control surface at all -- there is no "
            "'parkable by default', because parking a rig cable mid-QSO is not "
            "a thing to discover by accident."
        ),
    )
    resume: ResumeControl | None = Field(
        default=None,
        description=(
            "Present when the device needs a step after the machine resumes from "
            "suspend (issue #177). Installed by `hardware apply` as a disclosed "
            "system modification, removed by `hardware unapply`."
        ),
    )
    documentation: HardwareDocumentation


def _check_symlink_safety(name: str, usb_ids: list[UsbId], udev: UdevBinding | None) -> None:
    """A device-specific symlink needs an identifier that specifies a device.

    D-028. Refused structurally rather than reviewed, for the same reason there
    is no ``method: script`` and no unverified download: both halves of this
    failure are silent. A rule matching ``10c4:ea60`` alone names every CP2102
    adapter attached to the machine after whichever badge the rule was written
    for -- a rig-control cable, a GPS puck, a Meshtastic node -- and the operator
    sees a symlink pointing at the wrong device with no error anywhere.
    """
    if udev is None:
        return
    if udev.match_product is not None:
        read = {i.product_string for i in usb_ids if i.product_string}
        if udev.match_product not in read:
            raise ManifestError(
                f"{name!r} matches ATTRS{{product}}=={udev.match_product!r}, which no "
                f"identifier here records having read"
                + (f" (recorded: {sorted(read)})" if read else "")
                + ". A product string nobody has read is the same defect as a guessed "
                "VID:PID, pointed the other way: the rule silently never matches and "
                "looks exactly like a bad cable. Record it in product_string on the "
                "identifier it was read from, with the capture or source in evidence."
            )
    if udev.serial_suffix is True and not any(
        i.reports_serial is True for i in usb_ids if i.confirmed
    ):
        raise ManifestError(
            f"{name!r} sets serial_suffix but no confirmed identifier records "
            f"reports_serial: true. The cited udev rules attest *identifiers*, not "
            f"descriptor strings -- an identifier in a rule is not evidence the "
            f"device fills in iSerial. A suffix with nothing to append produces a "
            f"broken or nondeterministic symlink, which is the proxmark3 failure. "
            f"Set reports_serial on the identifier a capture confirmed it from, or "
            f"leave serial_suffix unset until somebody has plugged one in (D-031)."
        )
    ambiguous = [i for i in usb_ids if i.ambiguity]
    if ambiguous and not (udev.match_product or udev.match_serial):
        pairs = ", ".join(str(i) for i in ambiguous)
        raise ManifestError(
            f"{name!r} pins the symlink /dev/{udev.symlink} to identifiers that do "
            f"not identify a device: {pairs}. A rule matching those alone claims "
            f"every device using that chip. Add match_product (or match_serial), "
            f"or drop the symlink and rely on /dev/serial/by-id/, which systemd "
            f"already populates from the descriptor strings (D-028)."
        )


class DeviceClass(_DeviceCommon):
    """A family of devices with identical Linux-side needs.

    Written because conference badges are the clearest case: an ESP32-S3 badge,
    a CP210x badge and a CH340 badge differ in silicon and not at all in what
    Linux needs to talk to them. Build the class once and every badge works;
    a specific badge then becomes a worked example rather than a special case.
    """

    kind: Literal["class"] = "class"
    # Narrowed from the base: a class exists to carry identifiers shared by a
    # family, so one with none describes nothing.
    usb_ids: list[UsbId] = Field(
        min_length=1, description="Bridge chips and native-USB identifiers."
    )

    @model_validator(mode="after")
    def _check(self) -> DeviceClass:
        if not SLUG.match(self.name):
            raise ManifestError(f"device class name {self.name!r} must be a lowercase slug")
        unconfirmed = [str(i) for i in self.usb_ids if not i.confirmed]
        if unconfirmed:
            raise ManifestError(
                f"device class {self.name!r} carries unconfirmed identifiers "
                f"{unconfirmed}. A class propagates downward to every device that "
                f"joins it, so a guess here is a guess with a distribution mechanism "
                f"— which is how badgelife supplied identifiers that could not match "
                f"to two boards in a row. Evidence flows upward: confirm it on a "
                f"device first. Keep the instructive ones in rejected_ids (D-030)."
            )
        _check_symlink_safety(self.name, self.usb_ids, self.udev)
        return self


class DeviceManifest(_DeviceCommon):
    """One physical device."""

    kind: Literal["device"] = "device"
    vendor: str
    device_class: str | None = Field(
        default=None, description="Inherits that class's ids, groups, packages and tooling."
    )
    identification_gap: str | None = Field(
        default=None,
        description="What is unknown about how this device enumerates, and why.",
    )
    gap_closure: GapClosure | None = Field(
        default=None,
        description="Who can close the gap. Required whenever there is one.",
    )
    status: Literal["supported", "untested", "planned"] = Field(
        default="untested",
        description=(
            "Whether the identifiers and setup recipe are correct. Orthogonal to "
            "`maintainer_verified`, which is whether anyone here ran the hardware."
        ),
    )
    maintainer_verified: MaintainerVerification | None = Field(
        default=None,
        description="Evidence that someone on this project actually ran the device.",
    )
    composite: bool = Field(
        default=False,
        description=(
            "This is one product that enumerates as several USB devices behind an "
            "internal hub. Declaring it obliges every identifier to say what it is."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> DeviceManifest:
        if not SLUG.match(self.name):
            raise ManifestError(f"device name {self.name!r} must be a lowercase slug")

        confirmed = [i for i in self.usb_ids if i.confirmed]
        if not confirmed and not self.device_class and not self.identification_gap:
            raise ManifestError(
                f"device {self.name!r} has no confirmed USB identifier, no device_class "
                f"to inherit one from, and no identification_gap explaining what is "
                f"unknown. Guessing a VID:PID produces a udev rule that silently never "
                f"matches, which is indistinguishable from a bad cable."
            )
        if self.udev and self.udev.serial_suffix is False and len(confirmed) > 1:
            raise ManifestError(
                f"device {self.name!r} matches several USB ids but pins an unsuffixed "
                f"symlink; two attached devices would race for the same /dev name"
            )
        if self.identification_gap and self.gap_closure is None:
            raise ManifestError(
                f"device {self.name!r} records an identification_gap but no "
                f"gap_closure. A gap nobody can close is not the same work item as "
                f"one lsusb would settle, and the reader cannot tell them apart "
                f"from the prose."
            )
        if self.gap_closure and not self.identification_gap:
            raise ManifestError(
                f"device {self.name!r} sets gap_closure with no identification_gap to close"
            )
        if self.maintainer_verified and self.gap_closure == "unverified_by_maintainer":
            raise ManifestError(
                f"device {self.name!r} records a maintainer verification and also says "
                f"the gap is unverified_by_maintainer. Both cannot be true: somebody "
                f"either ran this hardware or did not."
            )
        if self.maintainer_verified and self.status == "planned":
            raise ManifestError(
                f"device {self.name!r} is marked planned but records a maintainer "
                f"verification -- planned means not yet acquired"
            )
        if self.status == "supported" and not confirmed and not self.device_class:
            raise ManifestError(
                f"device {self.name!r} claims status 'supported' with no confirmed "
                f"USB identifier — D-018: do not claim what has not been tested"
            )
        if self.composite:
            if len(self.usb_ids) < 2:
                raise ManifestError(
                    f"device {self.name!r} is marked composite but carries "
                    f"{len(self.usb_ids)} identifier(s). Composite means several USB "
                    f"devices behind one internal hub; one identifier is a plain device."
                )
            unmapped = [str(i) for i in self.usb_ids if i.node_kind is None]
            if unmapped:
                raise ManifestError(
                    f"device {self.name!r} is composite but {unmapped} do not say what "
                    f"kind of interface they are. The point of declaring composite is to "
                    f"answer 'which of these is the debug probe' — an unmapped list of "
                    f"identifiers does not, and /dev/serial/by-id/ already provides the "
                    f"stable paths it cannot label (D-029)."
                )
        _check_symlink_safety(self.name, self.usb_ids, self.udev)
        return self
