<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
SPDX-License-Identifier: GPL-3.0-or-later
-->

# The Hammunition suite

Hammunition is one engine and a set of companion projects, each in its own
repository with its own releases. This page says what each is, how they fit
together, what each needs, and where to report a problem. **It does not repeat
installation specifics:** each project's README owns those, and every section
links to it, so a copy here cannot drift from the project.

Looking for an activity rather than a project (GPS and time, APRS, Meshtastic,
Reticulum, MeshCore)? The [activity hubs](activities/index.md) say what the
catalog carries for each, how to install it and what has been measured.

## The projects

| Project | What it is | Status | Needs the engine? |
|---|---|---|---|
| [Hammunition](https://github.com/Renegade-Penguin/Hammunition) | The engine and the catalog: installs, configures and documents the software | Pre-1.0 (v0.22.0 in this tree) | It *is* the engine |
| [Hammunition Tray](https://github.com/Renegade-Penguin/hammunition-tray) | System-tray switches to park GPS, modem, Bluetooth and camera and to start services | v0.5.0 pinned; the Qt tray is built, not yet run on any desktop it lists | Yes |
| [Hammunition Hill](https://github.com/Renegade-Penguin/hammunition-hill) | A ham-radio dashboard in your browser | v1.3.0 pinned by the catalog (the repeaters panel with the area selector); upstream may be newer | No |
| [Hammunition Bunker](https://github.com/Renegade-Penguin/hammunition-bunker) | A LAN server that keeps verified copies of the offline data | 0.1.0, unreleased: tested against a fake engine, never run on a NAS | Yes |
| Hammunition Console | A full-screen terminal front end for the engine | Part of the engine since v0.21.0 (#302); the old repository is archived. Not yet run through a real install | It is part of the engine |
| [Hammunition GPS Tether](https://github.com/Renegade-Penguin/hammunition-gps-tether) | Your gpsd position as NMEA and as a browser stream, on this machine only | v0.1.1 pinned; measured on the field laptop as a service, including across a reboot ([bench session 13](reference/bench-verification-5430.md)) | Needs gpsd; the engine installs it |

"Pinned" is the version `catalog/packages/` installs; a project's repository
may be ahead of it. **If a status here disagrees with the project's own README,
the README is newer and this page is a bug: please report it.** A status is
*measured* only where the [bench record](reference/bench-verification-5430.md) or
a VM campaign page records it; everything else is built, not yet measured.

The same maintainer's [Skid-Finder](https://github.com/ChiefGyk3D/Skid-Finder)
is carried like any upstream program (`skid-finder`); it is not part of the
suite's runtime.

## How they fit together

```text
   publishers (Geofabrik, Kiwix, FEMA ...)          your radios, GPS, SDRs
            |                                              |
            v                                              v
  Bunker (on a NAS, LAN) -- optional, read-only --> the engine asks it first
                                                           |
  +--------------------------------------------------------v----+
  | Hammunition: catalog, plans, installs, station values        |
  +--------------------------------------------------------------+
     | installs   | drives it     | hardware apply     | installs, starts
     v            v               v                    v
   Hill        Console        Tray (+ root helper)   GPS Tether -> QMapShack,
 (browser)    (terminal)      park/wake, services     browser map, CoMaps
```

- **The engine is the hub.** Everything else is installed by it, a client of
  it, or a server for its data. The catalog is the one source of what is
  installed and at which version.
- **Hill is independent.** It runs without the engine; the engine installs it.
- **Tray and Console are front ends.** Neither holds install logic. The Tray
  switches what `hammunition hardware park`, `wake` and `hammunition services`
  do; the Console runs the engine's commands.
- **The GPS Tether is a bridge** from gpsd to mapping programs.
- **The Bunker is optional.** Without it every download comes from its
  publisher, with the same checks.

## Hammunition (the engine)

- **Repository and details:** <https://github.com/Renegade-Penguin/Hammunition#readme>;
  the [command reference](reference/cli.md) owns commands and flags.
- **Platforms:** Parrot OS first; Debian 13, Ubuntu 24.04 and 26.04, Kali and
  Linux Mint 22.3 tested in containers; see [desktops](desktops.md).
- **Install and first run:** [Installation](getting-started/installation.md).
- **Example:** `hammunition station set --callsign N0CALL --grid-square FN31pr`
  then `hammunition install station --dry-run`; read the plan, then run it
  without `--dry-run`. `hammunition status` lists what was installed.
- **Report problems:** [issues](https://github.com/Renegade-Penguin/Hammunition/issues);
  attach `hammunition logs --last` with your callsign and grid removed.

## Hammunition Tray

- **Repository and install specifics:** <https://github.com/Renegade-Penguin/hammunition-tray#readme>.
- **Platforms:** `hammunition-tray` for KDE Plasma 6; `hammunition-tray-qt` for
  Xfce, LXQt, LXDE, MATE, Cinnamon. Not for GNOME or COSMIC: use the terminal
  commands.
- **Depends on:** the engine (it reads the lists `hammunition hardware apply`
  writes), a polkit agent, and a device with a `power_control` block.
- **Example:** `hammunition hardware state`, then switch a GPS off in the tray
  or run `hammunition hardware park NAME`; `hammunition hardware wake NAME`
  brings it back. How-to: [the tray's Controls panel](guides/tray-controls.md).
- **Not yet measured:** the Qt tray on any listed desktop.
- **Report problems:** [issues](https://github.com/Renegade-Penguin/hammunition-tray/issues);
  run `hammunition hardware state` first.

## Hammunition Hill

- **Repository and install specifics:** <https://github.com/Renegade-Penguin/hammunition-hill#readme>,
  which also keeps its feature status table.
- **Platforms:** targets whose archive has Python 3.11 or later; Ubuntu 22.04 and
  Pop!_OS 22.04 are refused at plan time.
- **Depends on:** nothing at run time. It is in the `station` profile.
- **Example:** `hammunition install hammunition-hill`, then open
  <http://127.0.0.1:8073/>. Its fetched panels need an internet connection; the
  [package page](packages/hammunition-hill.md) says which tiers do. `hamhill check --fetch` names a source that broke.
- **Report problems:** [issues](https://github.com/Renegade-Penguin/hammunition-hill/issues).

## Hammunition Bunker

- **Repository and install specifics:** <https://github.com/Renegade-Penguin/hammunition-bunker#readme>.
- **Status: experimental.** Tested against a fake engine and a publisher on
  loopback; the image has never been built and no NAS has run it.
- **Depends on:** a NAS or home server with a container runtime (Synology
  Container Manager or rootless Podman, from its documentation, not from a run),
  and the engine, whose `hammunition artifacts --json` says what to keep.
- **Use:** on the laptop, `hammunition station set --mirror http://NAS-ADDRESS:8080/`;
  later data installs name the mirror first and fall back to the publisher,
  with the same digest check. [A LAN mirror](guides/lan-mirror.md) owns the engine side.
- **Report problems:** [issues](https://github.com/Renegade-Penguin/hammunition-bunker/issues).

## Hammunition Console

- **Where it lives:** in the engine, since v0.21.0 (#302): `hammunition console`.
  The old repository, `hammunition-console`, is archived. It ships with the
  engine because it makes no sense on its own: it only drives the engine, and a
  separate repository produced a version floor and a refusal where an update was
  wanted. Tray, Hill, Bunker and the GPS tether stay separate: each runs without
  the engine or serves other clients.
- **Platforms:** Python 3.11 or later; a terminal of at least 80x24; refuses
  `TERM=dumb` and root. SSH and Raspberry Pi use are not measured.
- **Depends on:** the engine it is part of, and `python3-urwid` (or the
  `console` extra in a virtualenv).
- **Example:** `hammunition console`; pick `station` in Install: the plan is
  shown, and nothing changes until you type `yes`. Walkthrough:
  [The console](getting-started/console.md); keys and screens:
  [the console reference](console/index.md).
- **Not yet measured:** a real install driven from it. Hardware and maps
  screens are not in it yet.
- **Report problems:** [issues on the Hammunition repository](https://github.com/Renegade-Penguin/Hammunition/issues).

## Hammunition GPS Tether

- **Repository and install specifics:** <https://github.com/Renegade-Penguin/hammunition-gps-tether#readme>.
- **Depends on:** gpsd and a receiver with a fix. It listens on 127.0.0.1 only:
  NMEA on 10110, a browser event stream on 10111.
- **Example:** `hammunition install gps-tether`, then
  `systemctl --user start hammunition-gps-tether.service`; with a fix
  it writes `$GPRMC` and `$GPGGA` sentences on 127.0.0.1:10110. QMapShack steps:
  [Offline navigation](guides/offline-navigation.md#11-your-position-in-qmapshack-the-gps-tether).
- **Measured:** installed, enabled and active on both ports on the field laptop, and still active at login after a reboot (bench, 2026-10-03). **Not yet measured:** a suspend and resume with it running.
- **Report problems:** [issues](https://github.com/Renegade-Penguin/hammunition-gps-tether/issues);
  `cgps` for a receiver with no fix.

## Who owns which detail

| Detail | Owner | This page |
|---|---|---|
| Commands, flags, exit codes | the [command reference](reference/cli.md) | links, never restates |
| Version installed, from where, how it is checked | `catalog/packages/*.yaml` | states the pinned version |
| A project's internals, options, changelog | that project's repository | one paragraph and a link |
| What an install writes to the machine | the unit's [package page](packages/index.md) | links |

Changing one of these in a repository? Change this page in the same pull request.

## Next

[Applications by activity](applications.md), [EMCOMM field use](guides/emcomm-field.md),
and [what to download before a deployment](getting-started/before-deployment.md).
