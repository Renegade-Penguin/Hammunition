<p align="center">
  <img src="https://raw.githubusercontent.com/Renegade-Penguin/Hammunition/main/docs/images/logo.png"
       alt="Hammunition" width="360">
</p>

# Hammunition

> Pick your RF arsenal.

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15243/badge)](https://www.bestpractices.dev/projects/15243)

Hammunition turns an existing Debian-family install into an amateur radio,
SDR and RF experimentation workstation. It adds software to the system you
already run, using your distribution's own packages wherever they exist, and
prints every change before it makes it. It is not a distribution, and it does
not replace AHRL or 73Linux; [Credit](#credit) says what each gave it.

**Who it is for.** A licensed ham, an SDR hobbyist or an RF-security
researcher with moderate Linux experience, who wants one machine to do
FT8, packet, SDR listening, offline maps and wireless auditing without
assembling each piece by hand. The standard the docs are held to: from a
fresh install to a working digital-modes station without asking anyone a
question or reading a forum thread.

**Supported targets.** Parrot OS first (the field laptop runs Parrot
Security 7.4), then Debian 13, Ubuntu 24.04 and 26.04, Kali and Linux Mint
22.3, each tested in a container, plus a Debian 13 arm64 target for Raspberry
Pi OS. Pop!_OS 24.04 passed the VM campaign but is not declared yet. Parrot with KDE Plasma comes first for the desktop tray;
[what works on which desktop](docs/desktops.md) says the rest. Targets are releases their distribution
still supports in full: Debian 12 is not planned, and rolling releases such as
Kali are best effort (D-084).

## How much disk you need

| Tier | For | Free space on top of your OS |
|---|---|---|
| Minimum | One or two software profiles, such as `station` and `digital-modes` | about 5 GB, suggested SSD 128 GB |
| Recommended | The whole catalog and a few map regions | about 55 GB, suggested SSD 256 GB |
| Large | Official topo sheets, full Wikipedia, many regions | about 220 to 300 GB, suggested SSD 512 GB or more |

The 55 GB is measured on the field laptop; the large tier leans on figures
the maintainer quoted, and each is marked in
[How much disk you need](docs/getting-started/disk-space.md), which lists
every figure with its source and what is unmeasured. The plan prints the size
of every data download before it asks you to agree.

## Quick start

```sh
git clone https://github.com/Renegade-Penguin/Hammunition.git
cd Hammunition
./bootstrap.sh                                  # engine, PATH link, then `hammunition doctor`
hammunition install station --dry-run           # read the whole plan; nothing changes
hammunition station set --callsign N0CALL --grid-square FN31pr   # your own values
hammunition install station                     # then do it
hammunition install digital-modes --dry-run     # next: a mode profile, planned first
```

Use your own callsign and grid square; `N0CALL` and `FN31pr` are
placeholders. If the shell answers `command not found`, log out and back in
or run `.venv/bin/hammunition` by its full path;
[installing the engine](docs/getting-started/install.md) covers each case.
Every command and flag is in [the CLI reference](docs/reference/cli.md).

## Where to go next

- **New here:** [Getting started](docs/getting-started/index.md), in order:
  install, disk, first profile, first contact.
- **I want to operate:** [the guides](docs/guides/index.md), one task each,
  from FT8 to offline maps.
- **I have a radio or an SDR:** [the hardware pages](docs/hardware/index.md).
- **Something broke:** [troubleshooting](docs/troubleshooting/index.md), by symptom.
- **I want the facts behind a claim:** [the reference](docs/reference/cli.md)
  and [the decision record](docs/DECISIONS.md).

The whole manual is at <https://renegade-penguin.github.io/Hammunition/>. The
projects this stands on are credited in full [below](#credit): Andy's Ham
Radio Linux, 73Linux, Skywave Linux, DragonOS, EmComm Tools OS Community and
the Debian Hamradio Blend.

---

## Where things stand: beta, v0.21.0 — feature-complete for 1.0; what remains is verification on the bench

**Status: beta, v0.21.0 — every 1.0 stage is in the catalog; the 0.1 that is missing is measured, not written.** The core cycle —
resolve, disclose, install, configure, verify, remove — runs end to end and is
**VM-verified on Parrot, Kali, Debian 13, Ubuntu 24.04, Ubuntu 26.04 and
Pop!_OS 24.04**, with **zero hard install failures across the whole catalog on
all six** ([M5 parity verified](docs/reference/m5-parity-verified.md); the
Pop run is [its own page](docs/reference/vm-campaign-pop.md), because Pop is
not a declared target yet). Eight backends are
written (apt, source, git, binary, venv — including a venv+payload hybrid —
node, third-party apt repositories against a pinned key, and the `data`
method for offline maps and books), and
`uninstall` reverses every one of them, not just apt. `./bootstrap.sh`
installs the engine in one command and links `hammunition` onto your PATH;
`hammunition doctor` reports what is ready;
`hammunition hardware` detects your radios and applies the udev rules and
group membership they need; `hammunition update` reports what is installed
against the catalog and, with `--upstream`, the catalog against what
upstream publishes; launchers and a curated desktop menu generate from the
catalog — eight activity groups, 55 submenus named for the thing a person
looks for, a toolkit nested as one line, every generated entry titled by
what it does (D-050, D-054, D-055) — measured on the field laptop's KDE
Plasma, written for Xfce through the menu spec and for GNOME through its
app-folders, and not yet looked at on either of those desktops since the
rebuild. Parrot with KDE Plasma comes first, and Xfce and LXQt are welcome
next; [the desktops page](docs/desktops.md) says what is measured on each,
and on a machine with no Plasma session `station` now defers the Plasma
tray by name instead of pulling Plasma in (D-060). **What remains for 1.0** is listed with owners in
[the 1.0 checklist](docs/reference/release-1.0-checklist.md): the Pop!_OS
declaration decision, the rebuilt menu seen on GNOME, Xfce and COSMIC,
the attached-hardware ladder on the bench (the field target is a Dell
Latitude 5430 Rugged; [twelve sessions](docs/reference/bench-verification-5430.md)
have run there, the whole catalog installed and verified, its GPS receiver
parked and woken from the tray, the radios not yet plugged in), and a signing key — the first signed tag waits on one
that does not exist yet.

Being honest about this up front matters more than looking finished, so here is
exactly where things stand:

| | Status |
|---|---|
| Catalog schema (Pydantic, `mypy --strict`) | ✅ working |
| Package manifests | 🟡 **332**, up from 71 |
| …Debian Blend coverage | ✅ **152 of 152** — SCOPE.md's first 1.0 stage, complete |
| …parity coverage | 🟡 **111 of the 125 units that owe a manifest** — [every gap has a recorded reason](docs/reference/parity-coverage.md) |
| Hardware catalog | 🟡 34 devices, 9 classes, 302 confirmed USB identifiers |
| …of which **supported** / **run on hardware here** | **19** / **7** — [two different claims](docs/DECISIONS.md), kept apart on purpose |
| Profiles | ✅ **all 13 of the 1.0 set**, plus 8 post-1.0 — every package installable, asserted by test; a member a target's archive lacks is deferred by name, never the whole profile (D-039) |
| Inventories of all six upstream sources | ✅ complete and measured |
| Consent gates for RF-research tooling | ✅ working |
| Distro detection from `/etc/os-release` | ✅ working |
| `install` / `uninstall` / `list` / `status` / `show` / `doctor` / `hardware` / `menus` / `station` CLI | ✅ working |
| apt backend, with real pre-flight resolution | ✅ working |
| Group membership from a manifest | ✅ working |
| Source builds from a verified tarball (cmake, autotools, qmake, make) | ✅ working |
| Builds from a pinned git revision, with the pin verified after checkout | ✅ working |
| Prebuilt binaries: `.deb`, tarball, zip, executable | ✅ working — `.deb` through apt, never `dpkg -i` |
| Per-user venv installs, hash-pinned end to end (`--require-hashes`) | ✅ working — not1mm and NanoVNASaver run from them |
| Launcher + desktop-entry generation from manifests (D-036) | ✅ working — 37 units carry launchers; terminal launchers hold their window; a wrapper never shadows its own tool (found and fixed 2026-09-12) |
| Idempotent re-runs for builds (D-051) | ✅ a source, git or prebuilt unit already installed at its pin is skipped; measured on the field laptop: 143 of 165 units plan nothing on a re-run |
| AppImage backend | ❌ post-1.0 (SCOPE.md) — refused by name |
| pipx / CPAN backends | ⚪ re-measured to **zero users** and dropped from 1.0 (D-014 amendment) |
| Templated config files, from station values | ✅ working — a missing value defers one file, not the transaction |
| Third-party apt repos | ✅ working — manifest pins the key fingerprint, consent is that fingerprint and `--yes` cannot give it, both files reversed by `uninstall` (D-040); `code`/`codium` in the opt-in `editors` profile |
| Device power control: park and wake a catalogued device (D-056) | ✅ working — `hammunition hardware park`/`wake`, generated menu entries, and the [hammunition-tray](https://github.com/Renegade-Penguin/hammunition-tray) Plasma applet, all through one root helper behind one polkit action; measured on the field laptop's GPS receiver 2026-09-27; staying parked across a reboot (one udev rule per device, D-056 amended) is built; reboot not yet measured |
| Offline navigation: Navit over your own OpenStreetMap regions (D-057) | 🟡 built, not yet measured on hardware end to end — regions from station config; each download checked by a sha256 Hammunition pinned (the 50 US states and DC) or by Geofabrik's MD5, and the plan says which; converted for Navit as the operator; post-1.0 `navigation` profile, [guide](docs/guides/offline-navigation.md) |
| Offline trails and terrain: QMapShack, routing on foot, Copernicus elevation (D-061) | 🟡 built, not yet measured on hardware — Garmin maps and one Routino database built from your regions as the operator; elevation tiles checked by a sha256 Hammunition pinned or by the object's MD5 (no tile pinned yet), and the plan says which; contours at 20 m; `gps-tether` serves the position on 127.0.0.1 only; post-1.0 `navigation` profile, [guide](docs/guides/offline-navigation.md) |
| Offline reference: Kiwix books, dictionaries, ICS forms (D-066) | 🟡 built, not yet measured on hardware — books chosen by id in station config from a 27-book list with each licence stated, each pinned by size and sha256 from Kiwix's `.meta4` and printed in the plan; dictd on 127.0.0.1; FEMA's 39 ICS forms by Hammunition's own sha256; `hammunition reference serve` on 127.0.0.1 only; post-1.0 `reference` profile, [guide](docs/guides/offline-reference.md) |
| A LAN mirror for offline data, and `hammunition artifacts` (D-070) | 🟡 built, not yet measured on hardware — `station set --mirror` names a machine on your own network; each data download asks it first and its publisher on any failure, the same digest checked either way; `artifacts --json` lists what to mirror for [Hammunition Bunker](https://github.com/Renegade-Penguin/hammunition-bunker), [guide](docs/guides/lan-mirror.md) |
| The rig as station data: `station set --rig`, one shared `rigctld` (D-073) | 🟡 built, not yet run against a radio — a `rig` hardware class and five station values; `rig-service` (in `station`) renders one `rigctld` as a systemd user service on 127.0.0.1:4632 behind a loopback filter on 4532 that drops a browser's HTTP request (a browser POST keyed hamlib's dummy, measured); flrig stays the other route; `--unattended` is opt-in; the FT-991A and UV-50PRO benches are owed; [guide](docs/guides/rig-control.md) |
| GPS time: the clock follows the GPS when the network is gone (D-058) | 🟡 built and tested in containers and fakes, not yet run on the bench — four modes, `auto` by default, ntpsec only (a `chrony` unit covers the rest, D-072); `hammunition time`, [guide](docs/guides/gps-time.md) |
| The terminal console: `hammunition console` (D-059 amended 2026-10-04) | 🟡 built and tested against recorded engine documents, a fake engine and a real pseudo-terminal; part of the engine since v0.21.0 (#302), no version floor; not yet run on the field laptop or through a real install — [reference](docs/console/index.md) |
| Tray switches, the Controls panel and `hammunition services` (D-056 amended) | 🟡 the GPS receiver was parked and woken from the Plasma applet on the field laptop 2026-09-27; the Controls panel (devices, services, radios), `hammunition services` and the tray's own helper were exercised against fakes only; [guide](docs/guides/tray-controls.md) |
| Repeaters, infrastructure and EMCOMM points on the offline maps (D-064, D-074, D-075) | 🟡 built, not yet drawn on a desktop — repeater layers from your own export, Open Repeater, OpenStreetMap, the ACMA register and on-request lists (unverified ones named as such); eight OpenStreetMap infrastructure layers and FAA, EIA and WRI data; none of it seen in QMapShack or Navit yet; [guide](docs/guides/offline-navigation.md) |
| The offline browser map and GraphHopper routes (D-071, D-076) | 🟡 built, not yet measured on a desktop — vector tiles from your regions served on 127.0.0.1 only; GraphHopper (installed by name) routes car, bike, foot and hike offline; measured on synthetic and Delaware-sized regions on the development host with a headless browser; [guide](docs/guides/offline-navigation.md) |
| Terrain for SPLAT! and Signal-Server from your regions' elevation (D-061 amended) | 🟡 built, measured on the development host, not on a real region through `install` — `splat-sdf` and `hammunition maps splat`; [guide](docs/guides/propagation.md) |
| udev rule generation from the hardware catalog | ✅ generated and applied by `hammunition hardware apply`; applied on the field laptop and byte-identical to the catalog's set; not yet exercised against an attached device |
| `uninstall` | ✅ working — reverses apt, venv, binary, .deb, trees and launchers; marker-verified, VM-proven; a real `make install` is refused by name |
| End-to-end VM verification (install / configure / remove) | ✅ Parrot, Kali, Debian 13, Ubuntu 24.04, Ubuntu 26.04, and [Pop!_OS 24.04](docs/reference/vm-campaign-pop.md) as an undeclared target |
| M5 install-success across the full catalog, six targets | ✅ **zero hard failures**; every unit installs on ≥1 target or is refused with a reason |
| Curated desktop menus (Xfce/KDE menu-spec + GNOME app-folders) | ✅ *Hammunition*, eight activity groups in a newcomer's reading order, 55 submenus named for the thing a person looks for, a toolkit unit nested as one line (GNU Radio's 21 entries), a titled entry for every installed unit that ships none, and a source build's own entry placed from the local prefix (D-050, D-054, D-055); measured on the field laptop's Plasma 2026-09-13: 108 entries placed, largest submenu 11; GNOME's eight folders written and read back from gsettings on the same machine; Xfce and COSMIC not yet looked at since the rebuild |
| `update` and `update --upstream` (D-053) | ✅ installed against the catalog offline, the catalog against upstream on request; measured on the field laptop: 171 units in 0.7 s, 25 upstream probes in 7.5 s, three pins found behind and re-pinned the same night |
| Profile companion offers (mail client, serial terminal) | ✅ detect → respect → offer, never silent |
| Getting-started, profile, troubleshooting docs | ✅ written and generated |

**The hard part is covered.** Of AHRL's 95 units, **57 cannot be satisfied
by apt at all** — that 60% is precisely what users cannot install themselves,
which is the reason the project exists — and the source, git, prebuilt-binary,
venv and node backends now cover it, verified on six VMs and on the field
target. What remains out of reach is AppImage and a configured Wine prefix,
both post-1.0 and each refused by name. Until the bench ladder has run against
attached radios ([the checklist](docs/reference/release-1.0-checklist.md)),
the projects in [Credit](#credit) below are the ones with years of field use
behind them; this project exists because of them, not instead of them.

What it does do, it does completely: `--dry-run` prints every command and every
system change before anything happens, resolution finishes before installation
begins so a failure is a report rather than a half-installed machine, and a
package this engine cannot handle is **refused by name with the reason**, never
skipped. See [`docs/reference/cli.md`](docs/reference/cli.md). The
commands a front end reads (`status`, `list`, `show`, `update`, `doctor`,
`station show`, `hardware state`, `maps regions`, `services` (the list), `artifacts`, and the `install` and
`uninstall` plans under `--dry-run`) also print JSON with `--json`; see
[`docs/reference/json-interface.md`](docs/reference/json-interface.md)
(D-059).

```
hammunition install rf-security --dry-run
```

If the shell answers `command not found`, `./bootstrap.sh` has not run yet,
or `~/.local/bin` is not on your PATH until you next log in. Run the
checkout's `.venv/bin/hammunition` by its full path meanwhile;
[installing the engine](docs/getting-started/install.md) covers each case.

The other thing that is usable today is the research. `docs/reference/` contains
complete, generated inventories of six upstream projects, with per-package
availability measured inside real containers rather than assumed — and,
increasingly, with packages actually installed rather than merely reported as
available, because those two turned out to disagree.

**The family.** Three separate projects by the same maintainer sit beside
this one. All three are carried in the catalog on exactly the same terms as
everything else — a released, pinned artefact, installed through the engine
before the manifest merged — and each manifest discloses that its upstream is
this project's own maintainer.

| Project | What it is | How you get it |
|---|---|---|
| [Hammunition Hill](https://github.com/Renegade-Penguin/hammunition-hill) | A local-first operating-position dashboard — clocks, band plan, solar and propagation dials, DX spots coloured by your log, satellites, a CW trainer — served from your own machine to your own browser on loopback. | [`hammunition-hill`](docs/packages/hammunition-hill.md), in the `station` profile: a digest-pinned `.deb`. |
| [Skid Finder](https://github.com/ChiefGyk3D/Skid-Finder) | A passive detector for BLE-spam and Wi-Fi attacks, built for foxhunting at a con. It listens and never transmits. Upstream is alpha. | [`skid-finder`](docs/packages/skid-finder.md), in the `rf-security` profile: a sha256-pinned tag tarball. |
| [hammunition-tray](https://github.com/Renegade-Penguin/hammunition-tray) | A KDE Plasma tray applet: a switch per parkable device, and a Controls panel for services and radios, calling the helper this project installs (D-056). | [`hammunition-tray`](docs/packages/hammunition-tray.md), in the `station` profile: the release's source archive, digest-pinned, installing the helper too (0.5.0 published no `.deb`); `hammunition-tray-qt` is the same for Xfce, LXQt, LXDE, MATE and Cinnamon. KDE Plasma 6 only, deferred from `station` on a machine with no Plasma session ([desktops](docs/desktops.md)); run `hammunition hardware apply` first. |

**There is one thing you can help with right now**, and it needs no code:
[contributing hardware identifiers](docs/contributing/hardware.md). Nineteen of
the 34 catalogued devices still have something unknown about them, and eleven of
those are waiting on somebody who owns the hardware — the maintainer does not.
Sixty-seven Meshtastic and MeshCore boards are waiting on one line each. It
takes thirty seconds, there is a read-only script for it, and there are
[issue forms](.github/ISSUE_TEMPLATE/) that say exactly what to paste and, just
as usefully, which boards are **not** worth your time.

---

## What this is

Two separable halves, and keeping them separate is the point:

1. **The catalog** (`catalog/`) — YAML manifests describing software: what it is,
   what it's for, how to install it per distro, which profiles include it. Pure
   data, no executable logic. Usable by an engine that isn't ours.
2. **The engine** (`src/hammunition/`) — a Python CLI that reads manifests and
   performs installation, configuration and hardware setup.

The catalog is the durable asset. The engine is replaceable.

## What this is not

Considered and rejected, so nobody has to ask:

- A Linux distribution, custom ISO, or derivative
- A custom kernel
- A mirror of upstream Debian packages
- Forks of upstream ham/SDR software
- Anything that replaces or reconfigures your OS wholesale

We **augment** an existing system and use upstream packages wherever they exist.

---

## Credit

Hammunition is built on other people's curation. These are inventory sources and
prior art, not things we are replacing — they worked long before this one did.

### Andy's Ham Radio Linux — Andy Stewart, KB1OIQ

The direct inspiration, and the closest existing thing to what we are building.
AHRL has served the amateur radio community for well over a decade, and **its
package curation is the single most valuable artifact in this space** — which
software is worth installing, which actually works, which is abandonware. That
judgement took years to accumulate and would be foolish to discard.

AHRL also arrived at the layered-onto-an-existing-OS model after years as a
distribution. That migration is strong evidence the approach is right, and it is
why building a distro is on our rejected list.

<https://sourceforge.net/projects/kb1oiq-andysham/>

### 73Linux — Jason Oleham, KM4ACK

Covers Winlink, packet and EMCOMM — PAT, BPQ, AX.25, ARDOP, Direwolf with real
configuration — a domain AHRL does not touch at all. Its community side-loading
model also shaped our three-tier catalog.

<https://github.com/km4ack/73Linux>

### Skywave Linux — Philip Collier, AB9IL

Shortwave and utility listening, remote SDR receivers, and the aeronautical
decoder cluster (ACARS, HFDL, VDL2) that is absent from Debian entirely.

<https://skywavelinux.com/>

### DragonOS — cemaxecuter

The SDR and SIGINT reference. Far larger than anything else in this space.

<https://cemaxecuter.com/>

### EmComm Tools OS Community — Gaston Gonzalez, KT7RUN

The EMCOMM station done as a system: pick the radio once and every application
is configured for it, with offline maps, Wikipedia and reference data for when
the network is gone. Its rig model and offline-data layer are the two ideas we
reimplement as catalog data; none of its code is taken, and its role symlinks
are not carried. Apache-2.0 code; the logos are under a separate notice.

<https://github.com/thetechprepper/emcomm-tools-os-community>

### The Debian Hamradio Blend

Team-governed, signed and machine-readable — the best provenance in the
landscape and the cheapest coverage in this project.

<https://blends.debian.org/hamradio/>

---

## Why it exists

Not because the projects above are wrong. Because they share a governance shape
that worries us more than any technical problem: install logic and package lists
tangled together in shell, a single maintainer, and contribution by email.

| Prior art | Hammunition |
|---|---|
| Tarballs and ISOs | Git, tagged releases, signed |
| Single maintainer | Multiple maintainers, documented governance |
| Contribute by email | Pull requests, issues, public review |
| Install logic and package list intertwined | Declarative catalog, separate engine |
| Bash | Python — idempotent, dry-run, transaction log |
| No cross-distro testing | CI containers per target distro |
| Ham radio | Ham radio **plus** SDR, RF security and mesh |

The full argument, with evidence, is in [`docs/why-hammunition.md`](docs/why-hammunition.md).

---

## What the hardware layer is actually for

Not persistent device symlinks. That is what this project used to say, and the
[generated accounting](docs/reference/device-naming.md) does not support it:
systemd's `60-serial.rules` already gives every USB-*serial* device a stable
`/dev/serial/by-id/` path, per unit, with no help from anybody. Stable naming
was never the hard part.

Of 34 catalogued devices, **29 are ones `by-id` does not settle**, and the
reasons are the work:

| What `by-id` cannot do | Where it bites |
|---|---|
| **Permissions** | A device only root can open is unusable however stable its path. This is what actually stops people. |
| **Non-serial devices** | 15 of 34 present nothing serial at all — every SDR, the Ubertooth, the Proxmark in client mode. `libusb` devices get no `/dev/serial/` entry to name. |
| **Identical units** | A Proxmark3 ships no product string and no serial. `by-id` builds its path from exactly those, so two of them collide there too. Only `by-path` separates them, and `by-path` changes when you move the cable. |
| **Which interface is which** | A Free-WiLi 2 is six USB devices behind an internal hub, four serial ports on one of them. `by-id` gives each a stable path and labels none. |

All seven symlinks this catalog emits are on devices in the second row — none
duplicates a path `by-id` would have given anyway. That was not designed for,
and it is the clearest statement of where the two mechanisms actually divide.

Two rules follow, both enforced by the schema rather than by review:

- **An identifier that names a chip may not name a `/dev` node.** `10c4:ea60` is
  a CP2102 bridge; a symlink on it claims your rig cable, your GPS puck and your
  Meshtastic node alike. A rule resting on one must carry `ATTRS{product}` or
  `ATTRS{serial}`, or emit no symlink ([D-028](docs/DECISIONS.md)).
- **A product string nobody has read is as bad as a guessed VID:PID.** Both
  produce a rule that silently never matches, which looks exactly like a bad
  cable. `hackrf-one` failed this the day it was enforced and was closed by
  reading upstream's USB descriptor, not by guessing ([D-029](docs/DECISIONS.md)).

---

## Where the identifiers come from

Mined from primary sources, never curated by hand — a shortlist is how `rtl-sdr`
came to carry 3 identifiers where Debian carries 42.

| Source | What it yielded |
|---|---|
| [Every udev rule in the Debian archive](docs/reference/udev-inventory.md) | 280 packages swept, 1,947 identifiers from the 122 whose rules name USB devices, no shortlist — **including 4 a distribution shipped and switched off with a reason**, which is the strongest evidence in the dataset. The `programmer` class is generated from it: 180 identifiers nobody typed |
| [The kernel's own `modules.alias`](docs/reference/usb-ambiguity.md) | Which pairs the kernel binds to a *bridge* driver — the closest thing to an authoritative "this is a chip, not a product" |
| [Meshtastic and MeshCore board definitions](docs/reference/lora-inventory.md) | 107 boards, 26 identifiers, the top one covering 49 — which closed the `meshtastic` entry with no hardware, and had to, since the maintainer's nodes were lost to flooding |
| Upstream USB descriptors | `hackrf` states in C that the One and the Pro share `1d50:6089`, which had rested on comparing one capture |

---

## Security posture

This is designed to run on machines that also hold security tooling. These are
requirements, not aspirations:

- **Never pipe remote content into a shell.** There is no `method: script` in
  the schema — it is unrepresentable, not merely discouraged.
- **Checksums are mandatory** for any non-apt download. The schema requires
  `sha256`; an unverified download cannot be expressed.
- **Third-party apt repos** are declared in the manifest with the signing key
  fingerprint pinned, and shown to you before being added.
- **Every system modification is printed before it happens.** `--dry-run` is
  complete, not approximate.
- **RF tooling whose lawful use depends on your authorization** sits behind a
  profile with an affirmative consent gate that `--yes` cannot satisfy. It
  discloses what the software can do and asks you to affirm your authorization.
  It does not tell you what is legal where you are — we cannot know that, and we
  are not lawyers.
- **Tests run in rootless Podman**, never Docker. Docker group membership is
  root-equivalent host access, which is not a trade this project will make.
- **Releases are SSH-signed tags**, verifiable against
  [`.github/allowed_signers`](.github/allowed_signers), which records every
  release key with the dates it was trusted. Check that file against
  `https://api.github.com/users/ChiefGyk3D/ssh_signing_keys` before trusting
  it; [`docs/contributing/releasing.md`](docs/contributing/releasing.md) is
  the procedure. **No key exists yet**: `v0.7.0`, `v0.9.0`, `v0.10.0`, `v0.11.0`, `v0.12.0`, `v0.13.0`, `v0.14.0`, `v0.14.1`, `v0.14.2`, `v0.14.3`, `v0.15.0`, `v0.16.0`, `v0.17.0`, `v0.18.0`, `v0.19.0`, `v0.20.0` and `v0.21.0` are annotated
  and unsigned, and the file says so; the first signed tag is 1.0.

---

## Documentation

**Read it at <https://renegade-penguin.github.io/Hammunition/>** — the
documentation site, *Hacker's Ham Shack*: getting started, step-by-step
guides (rig control, audio, the clock, FT8, Winlink, APRS, SDR, satellites),
every profile, package and device, and [every project we
install](docs/projects.md), linked to its home. It is built from `docs/` by
`mkdocs build --strict` and published from `main` (**D-065**).

The [GitHub wiki](https://github.com/Renegade-Penguin/Hammunition/wiki) is a mirror of the same pages, generated from `docs/` on every push to `main`; edit `docs/`, never the wiki.

The decision record and the policies below were written before the code
they describe, deliberately; the reference pages are generated from the
catalog and the measurements, so they cannot say what the code does not.

| | |
|---|---|
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Authoritative decision record. Where anything disagrees with it, it wins. |
| [`docs/SCOPE.md`](docs/SCOPE.md) | The six-source union and what 1.0 covers |
| [`docs/PARITY-POLICY.md`](docs/PARITY-POLICY.md) | What we carry, replace, revive, retire and add |
| [`docs/QUESTIONS.md`](docs/QUESTIONS.md) | Open questions, with recommendations |
| [`docs/reference/`](docs/reference/) | The measured inventories everything rests on |
| [`docs/reference/hardware-gaps.md`](docs/reference/hardware-gaps.md) | Every USB identifier we don't have, who can close it, and what it blocks |
| [`docs/reference/device-naming.md`](docs/reference/device-naming.md) | What `/dev/serial/by-id/` already covers, and the 29 of 34 devices where it does not |
| [`docs/contributing/hardware.md`](docs/contributing/hardware.md) | How to send one, and what we do and don't store |
| [`docs/reference/bench-verification-5430.md`](docs/reference/bench-verification-5430.md) | What has run on the field target itself, a Dell Latitude 5430 Rugged, and what has not |
| [`docs/reference/release-1.0-checklist.md`](docs/reference/release-1.0-checklist.md) | What stands between here and a 1.0 tag, each item with its owner and its measurement |
| [`CHANGELOG.md`](CHANGELOG.md) | One entry per release, assembled from the fragments in `changelog.d/`, each line naming the decision it rests on |
| [`docs/contributing/releasing.md`](docs/contributing/releasing.md) | How a release is cut and signed, and why v0.7.0 and v0.9.0 are not |

The documentation site is *Hacker's Ham Shack*. Its standard: a
licensed ham with moderate Linux experience should get from a fresh install to a
working digital-modes station without asking anyone a question or reading a forum
thread. A step that needs knowledge not in our docs is a documentation bug.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full version (security
issues go to [`SECURITY.md`](SECURITY.md), privately), including what
the copyright headers do and do not mean — **there is no CLA and no copyright
assignment; you keep copyright on what you write.**

Too early for large code contributions — the engine's install path is not
merged yet. What is useful now, roughly in order:

- **`lsusb` output for a device we don't own.** The most useful thing anyone can
  send, and it needs no code. A device whose USB identifier is guessed produces a
  udev rule that *silently never matches* — indistinguishable from a bad cable —
  so this catalog refuses to guess, and a dozen entries are waiting on one fact
  that takes thirty seconds to produce. There is a read-only script for it:
  [`docs/contributing/hardware.md`](docs/contributing/hardware.md).
- **Corrections to the inventories.** If `docs/reference/` says something wrong
  about a package you maintain or use, that is the most valuable issue you can
  file. Several of our own claims have already turned out to be wrong; each
  correction is recorded in the document that made the claim rather than quietly
  edited out.
- **Answers to [`docs/QUESTIONS.md`](docs/QUESTIONS.md).**
- **Telling us what AHRL or 73Linux got right that we have not noticed.**

An entry for hardware nobody here owns is welcome too. `identification_gap`
exists precisely so that *"this device exists and here is what Linux needs for
it"* is a shippable state rather than a blocker.

## Licence

**Two licences, split on the architectural boundary** (see
[D-023](docs/DECISIONS.md)):

| Tree | Licence | Why |
|---|---|---|
| `src/`, `scripts/`, `tests/`, `docs/` | **GPL-3.0-or-later** | Copyleft is the governance argument this project was founded on: a fork cannot close the source. It is also what the ham ecosystem already runs. |
| `catalog/` | **CC0-1.0** | The catalog must stay usable by an engine that isn't ours. Manifests record facts — that `fldigi` is packaged as `fldigi` and needs `hamlib` configured first — and CC0 removes an ambiguity rather than making a grant. |

SPDX headers throughout, per the [REUSE](https://reuse.software/)
specification; verbatim texts in [`LICENSES/`](LICENSES/).

This relicenses nothing the catalog *describes*. Every program in the inventory
keeps its own licence, recorded in
[`docs/reference/licence-verification.md`](docs/reference/licence-verification.md).

---

Copyright (C) 2026 Renegade Penguin LLC. Hammunition is free software: the engine
under GPL-3.0-or-later, the catalog under CC0-1.0. There is
[no CLA](CONTRIBUTING.md#you-keep-your-copyright).

---

## 💝 Support This Project

If you find Hammunition useful, consider supporting continued development.
Everything is also collected at **[support.chiefgyk3d.com](https://support.chiefgyk3d.com)**.

### Recurring Support

<div align="center">
<table>
  <tr>
    <td align="center" width="150">
      <a href="https://patreon.com/chiefgyk3d" title="Patreon">
        <img src="media/icons/patreon.svg" width="36" height="36" alt="Patreon"><br>
        <sub><b>Patreon</b></sub>
      </a>
    </td>
    <td align="center" width="150">
      <a href="https://streamelements.com/chiefgyk3d/tip" title="StreamElements">
        <img src="media/streamelements.png" width="36" height="36" alt="StreamElements"><br>
        <sub><b>StreamElements</b></sub>
      </a>
    </td>
    <td align="center" width="150">
      <a href="https://shop.chiefgyk3d.com/" title="Merch Store">
        <img src="media/icons/merch.svg" width="36" height="36" alt="Merch"><br>
        <sub><b>Merch Store</b></sub>
      </a>
    </td>
  </tr>
</table>
</div>

### Cryptocurrency Tips

<div align="center">
<table>
  <tr>
    <td><img src="media/icons/bitcoin.svg" width="28" height="28" alt="Bitcoin">&nbsp;<b>Bitcoin</b><br><code>bc1qztdzcy2wyavj2tsuandu4p0tcklzttvdnzalla</code></td>
  </tr>
  <tr>
    <td><img src="media/icons/monero.svg" width="28" height="28" alt="Monero">&nbsp;<b>Monero</b><br><code>84Y34QubRwQYK2HNviezeH9r6aRcPvgWmKtDkN3EwiuVbp6sNLhm9ffRgs6BA9X1n9jY7wEN16ZEpiEngZbecXseUrW8SeQ</code></td>
  </tr>
  <tr>
    <td><img src="media/icons/ethereum.svg" width="28" height="28" alt="Ethereum">&nbsp;<b>Ethereum</b><br><code>0x554f18cfB684889c3A60219BDBE7b050C39335ED</code></td>
  </tr>
  <tr>
    <td><img src="media/icons/solana.svg" width="28" height="28" alt="Solana">&nbsp;<b>Solana</b><br><code>5T8h3HbyvHgLxwXgchRYbHSqRjZyAr8J7uwjLN9Fh8Jh</code></td>
  </tr>
</table>
</div>

---

## 👤 Author & Socials

<div align="center">
<table>
  <tr>
    <td align="center" width="90"><a href="https://social.chiefgyk3d.com/@chiefgyk3d" title="Mastodon"><img src="media/icons/mastodon.svg" width="30" height="30" alt="Mastodon"><br><sub>Mastodon</sub></a></td>
    <td align="center" width="90"><a href="https://bsky.app/profile/chiefgyk3d.com" title="Bluesky"><img src="media/icons/bluesky.svg" width="30" height="30" alt="Bluesky"><br><sub>Bluesky</sub></a></td>
    <td align="center" width="90"><a href="https://twitch.tv/chiefgyk3d" title="Twitch"><img src="media/icons/twitch.svg" width="30" height="30" alt="Twitch"><br><sub>Twitch</sub></a></td>
    <td align="center" width="90"><a href="https://www.youtube.com/channel/UCvFY4KyqVBuYd7JAl3NRyiQ" title="YouTube"><img src="media/icons/youtube.svg" width="30" height="30" alt="YouTube"><br><sub>YouTube</sub></a></td>
    <td align="center" width="90"><a href="https://kick.com/chiefgyk3d" title="Kick"><img src="media/icons/kick.svg" width="30" height="30" alt="Kick"><br><sub>Kick</sub></a></td>
    <td align="center" width="90"><a href="https://www.tiktok.com/@chiefgyk3d" title="TikTok"><img src="media/icons/tiktok.svg" width="30" height="30" alt="TikTok"><br><sub>TikTok</sub></a></td>
    <td align="center" width="90"><a href="https://www.instagram.com/chiefgyk3d" title="Instagram"><img src="media/icons/instagram.svg" width="30" height="30" alt="Instagram"><br><sub>Instagram</sub></a></td>
    <td align="center" width="90"><a href="https://www.threads.net/@chiefgyk3d" title="Threads"><img src="media/icons/threads.svg" width="30" height="30" alt="Threads"><br><sub>Threads</sub></a></td>
    <td align="center" width="90"><a href="https://discord.chiefgyk3d.com" title="Discord"><img src="media/icons/discord.svg" width="30" height="30" alt="Discord"><br><sub>Discord</sub></a></td>
    <td align="center" width="90"><a href="https://matrix-invite.chiefgyk3d.com" title="Matrix"><img src="media/icons/matrix.svg" width="30" height="30" alt="Matrix"><br><sub>Matrix</sub></a></td>
  </tr>
</table>
</div>

<div align="center"><sub>Made with ❤️ by <a href="https://github.com/ChiefGyk3D">ChiefGyk3D</a></sub></div>
