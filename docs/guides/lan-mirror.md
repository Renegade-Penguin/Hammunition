<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
SPDX-License-Identifier: GPL-3.0-or-later
-->

# A LAN mirror for offline data

Map regions, elevation tiles, reference books and the other offline data
units are public data, and a laptop rebuilt or given a new region downloads
them again from their publishers: Geofabrik, the Copernicus bucket, Kiwix,
Natural Earth, country-files.com. A US state's terrain alone can be several
gigabytes, and the Kiwix books are the largest case of all: English
Wikipedia with pictures is 127 GB. If
a machine on your own network already holds a verified copy, the laptop can
take it from there instead, in seconds rather than hours, and with the
same checks it would have made against the publisher (**D-070**).

A **signed** Bunker catalogue goes one step further: once you enrol its
keys, the engine can plan and install with no publisher reachable at all
(**D-085**). That is a separate step from pointing the station at a mirror,
covered below.

The server side is a separate project,
[Hammunition Bunker](https://github.com/Renegade-Penguin/hammunition-bunker): a
container for a NAS that asks the engine which artifacts to keep, keeps
them fresh on a schedule, and serves them on the LAN. This page is the
engine side: what to set, what the plan will say, and what is and is not
trusted.

## Point the engine at the mirror

```
hammunition station set --mirror http://bunker.lan:8080/
hammunition station show
```

From then on, `hammunition install` asks the mirror first for every data
download — each `data` unit's files, each map region, each terrain tile,
each of CoMaps' maps (`comaps-maps/<version>/<id>.mwm`, D-069), each
reference book you chose (`kiwix-library/<book id>`, D-066) —
at `<mirror>/<unit>/<name>`, for example
`http://bunker.lan:8080/osm-regions/north-america/us/vermont`. Anything else
the install fetches (a source tarball, a prebuilt binary) still comes from
its publisher — **unless you also enrol the Bunker's signed catalogue**, in
which case those too can be checked against it; see below.

To stop using it for one run, `hammunition install --no-mirror ...`. To
remove it, `hammunition station set --clear-mirror`.

An **enrolled Bunker** lets the engine plan data and pinned-payload downloads with
publishers unreachable. Run `hammunition mirror enrol URL`, check the displayed
algorithm, size and fingerprint against the Bunker's key, and type that fingerprint.
`hammunition install ... --offline` uses only repository pins, local records and the
verified catalogue. With no flag, an exhausted publisher probe falls back to it and
each affected line says which Bunker and key recorded the metadata and when.

Setting `station set --mirror URL` alone still gives the D-070 download route; it
does not enrol keys or authorize using the mirror's metadata. `hammunition mirror
status --json` reports the accepted serial, age and keys. `--clear-mirror` removes
the URL and enrolment state. A lower serial refuses with `hammunition mirror
accept-older` as the explicit backup-restoration remedy. A catalogue older than
30 days is used with a warning.

Phase 1 covers data and pinned payloads. Apt and pip offline support belongs to
phase 2; git builds may still need archive dependencies, and Node applications may
need npm packages. Offline planning reports those gaps before execution. Installing
Hammunition writes no PAM configuration for the `security-keys` profile; archive package side effects are unmeasured across the target matrix.

## Enrol a signed Bunker (D-085)

A Bunker can sign its catalogue with one or more OpenSSH keys. Enrolling it
is a separate, deliberate step from setting `--mirror`, because it changes
what the engine is willing to *believe* about the mirror, not just where it
looks first:

```
hammunition mirror enrol http://bunker.lan:8080/
```

This fetches the catalogue, shows each signer: its fingerprint, measured
algorithm and key size, whether it claims to be hardware-backed, its
`no_touch_required` metadata, and any weak-key warning. You type the
fingerprints you choose to trust, comma-separated, and confirm each one at
the terminal — the same shape as a TOFU (trust-on-first-use) prompt
elsewhere in the engine, never answered by `--yes`. A key the Bunker claims
is hardware but whose type cannot prove it (anything other than an
`sk-ssh-ed25519@openssh.com` or `sk-ecdsa-sha2-nistp256@openssh.com` key)
asks a second question:

```
The Bunker claims this key is hardware-backed; the algorithm cannot prove
that. Affirm only if you verified its hardware origin. Type yes:
```

Both the fingerprint typed and the hardware affirmation are recorded in the
transaction log. There is no `--yes` and no `--json` form for `enrol` or for
`accept-older`, below — these are consent gates, not something a script can
drive unattended.

Enrolled trust is kept in `~/.config/hammunition/mirror.json` (mode 0600):
the URL, the Bunker's name, the chosen keys and their metadata, and the
highest serial the engine has accepted. There is **no permanent
`allowed-signers` file for you to maintain** — the engine builds one
temporary, in-memory file from this record each time it verifies a
catalogue, and discards it afterward. Re-enrolling the same URL and Bunker
name keeps the accepted serial and only the keys you choose this time; a
changed URL or name is treated as a new Bunker.

```
hammunition mirror status --json
```

reports the stored trust: each key's strength, hardware assertion and
`no_touch_required`, the accepted serial, and the age of the last verified
catalogue — nothing is fetched. `hammunition station set --clear-mirror`
removes the URL, every enrolled key, the enrolment id and the accepted
serial, but preserves the hardware-only policy (below) since that is a
standing choice, not part of one Bunker's trust.

### Weak keys, rollback and staleness

The engine ranks key types the way OpenSSH itself would: Ed25519 and its
hardware (`sk-`) form first, ECDSA (hardware or not) second, RSA 3072 bits
or more third. **RSA of 2048 bits or fewer is accepted, but warned on every
plan and in `mirror status`**, with the exact text

```
RSA <bits>-bit is weak: replace with Ed25519, ECDSA or RSA 3072+
```

A signature records what the Bunker saw; it never upgrades trust past that.
The engine remembers the highest serial it has ever accepted from a given
Bunker and refuses a lower one — a Bunker's catalogue should only ever move
forward. If you restore a Bunker from an older backup on purpose, the
remedy is explicit:

```
hammunition mirror accept-older
```

which re-verifies the current catalogue with your enrolled keys, shows its
serial next to the one you had accepted, and asks a typed `yes`. An invalid
signature still refuses even here — this command only relaxes the serial
check, never the signature check.

A catalogue whose `generated` timestamp is more than 30 days old is used
anyway, but warned on every plan: a Bunker that has stopped syncing with its
publishers is still better than nothing, as long as you know.

### Hardware-only policy

```
hammunition station set --mirror-require-hardware-key
hammunition station set --no-mirror-require-hardware-key   # the default
```

turns on a station-wide policy that only an enrolled **hardware** signer (an
`sk-*` key, or a non-`sk` key you explicitly affirmed as hardware-backed at
enrolment) may satisfy trust. It is off by default: a plain file key is a
reasonable choice for a one-operator Bunker, and the engine recommends
hardware without requiring it. With the policy on, a catalogue signed only
by file keys refuses with "no enrolled hardware key signed this catalogue",
naming the policy as the reason.

### What this never does

Enrolling a Bunker, however trusted, never writes PAM configuration and
never enables `sudo` or login by security key on this machine. That is a
separate, later, explicitly-consented step — see
[the `security-keys` profile](../profiles/security-keys.md) and
`hammunition doctor`, below. The archive packages the `security-keys`
profile installs may themselves carry PAM or debconf side effects; those
are unmeasured across the target matrix and are not something enrolling a
Bunker changes either way.

## A mirror URL is a LAN address

**A mirror is a machine on your own network, never reachable from the
internet.** The Bunker serves without authentication and over plain HTTP,
and that is fine only because nothing it sends is trusted: every byte is
checked against a digest the engine already holds, or against an enrolled
signature for the catalogue itself. Do not forward its port,
do not publish it, and do not point the engine at somebody else's.
The engine cannot check this for you: a hostname does not say whether it is
private. This is **documented, not enforced** — nothing in the engine
resolves the hostname to tell a LAN address from a public one.

A mirror URL may not carry a user name or password: the station file holds
no credentials. `http`, `https` and `file` all work as a scheme; a `file://`
mirror is covered on its own below.

## What the plan says

With a mirror set, the plan opens its data sections with

```
Data mirror (D-070):
  Each data download below (offline data, map regions, terrain tiles, CoMaps
  maps, reference books) is asked of the LAN mirror http://bunker.lan:8080/ first, ...
```

and every data download step names both places in order:

```
Fetch map region north-america/us/delaware (260101, ...) — sha256, pinned by Hammunition — the LAN mirror first, then the publisher; the sha256 is checked either way
  $ [fetch] http://bunker.lan:8080/osm-regions/north-america/us/delaware, then https://download.geofabrik.de/north-america/us/delaware-260101.osm.pbf (...)
```

A reference book reads the same way, asked of the mirror by its id:

```
Fetch reference book ham.stackexchange.com_en_all (2026-08, ..., CC BY-SA) — the LAN mirror first, then the publisher; the sha256 is checked either way
  $ [fetch] http://bunker.lan:8080/kiwix-library/ham.stackexchange.com_en_all, then https://download.kiwix.org/zim/stack_exchange/ham.stackexchange.com_en_all_2026-08.zim (...)
```

The id, not the dated file name, so the mirror keeps one path per book
across Kiwix's republications. When the catalog's pin moves to a newer
date and the mirror still holds the older file, its bytes fail the pin's
sha256 and the book comes from Kiwix instead.

`--dry-run` prints the same, and `install --dry-run --json` carries it as
`install.mirror` and each step's `sources`.

## What is trusted, and what happens when the mirror is wrong

Nothing from the mirror is trusted on its own. A download from it is
checked against the same digest the publisher's would be: the sha256
Hammunition pins, or the publisher's own MD5 for an unpinned region or tile,
read from the publisher while the plan is made (the ACMA register, which
has no digest at all, is the exception; see the end of this page). If the
mirror is switched off, does not
have the file, sends too much, sends the wrong size or sends the wrong
bytes, the download is discarded and the publisher is asked instead — **online.**
A mirror that does not answer at all is not asked again in that run, so a
switched-off NAS costs one ten-second wait, not one per tile.

**Offline (`--offline`, below), a mirror miss refuses by name instead.**
There is no publisher to fall back to by definition, so a deferred or
missing artifact is named in the plan rather than attempted.

A signed catalogue adds a second kind of trust, for the metadata the Bunker
*recorded about a publisher* — an unpinned region's dated file name and MD5,
an unpinned tile's size — distinct from a byte the engine itself hashes.
Repository pins stay authoritative either way: a signature records what the
Bunker saw, and never turns an unverified publisher claim into a verified
one.

## Source, binary, venv, Node, tool and git payloads

Once a Bunker is enrolled, the mirror route is not limited to data: a
pinned **source** tarball, **binary** (`.deb`, archive or single
executable), Python **venv** payload or **Node** source download is also a
Bunker item, named `<sha256>/<file name>` inside its own unit (for example
`wsjtx-improved/3fa9…c2/wsjtx-improved_2.8.0.tar.gz`). A `derived` block's
converter **tool** and any extra git-hosted file it downloads follow the
same rule. The plan shows the mirror address first and the publisher
second, exactly as a data download does, and the sha256 is checked either
way — the Bunker is never trusted for the bytes themselves, only asked
first for speed.

A **git** build's pinned revision mirrors as a bundle: unit `git-bundles`,
name `<unit>@<commit>`. When the manifest's `submodules: true`, each pinned
gitlink becomes its own bundle, named
`<unit>@<parent commit>/<submodule path>@<submodule commit>`. Checking out a
mirrored bundle recreates the pinned tag or branch locally and then
verifies it still resolves to the pinned commit — **a tag the Bunker's copy
is bit-for-bit correct for, but whose `refs/tags/<name>` has since been
re-pointed upstream, is refused by name** rather than silently trusted a
second time. A tag with no recorded commit in the manifest cannot be
offline-verified at all and is refused for that reason alone.

## Topographic and elevation checks

US Topo sheets, FSTopo sheets and 3DEP elevation tiles follow the same two
layers as everything else: a **selection** (which sheets or tiles a region
needs, computed from the region's outline) and the **payload** (the sheet
or tile bytes themselves). The engine can take the selection, the payload,
or both from an enrolled Bunker — but a Bunker holding one without the
other still leaves the unit short, and it is deferred whole rather than
installed partway. Each payload is checked the same way the publisher's
copy would be: the USGS and Forest Service's own ETag, or a pinned sha256
where the maintainer has set one, never the Bunker's say-so alone. Copernicus
terrain tiles follow the same rule, pinned where the catalog pins them and
checked by the publisher's size and ETag otherwise.

## Offline planning (`--offline`)

```
hammunition install navigation --offline
```

resolves the whole plan from the enrolled Bunker's verified catalogue and
never asks a publisher. With none enrolled, the run refuses at once:

```
no Bunker enrolled; hammunition mirror enrol URL
```

and with one enrolled but the catalogue itself unreachable:

```
no catalogue at http://bunker.lan:8080/catalogue.json: <reason>. Enrol a Bunker
that serves one, or run without --offline.
```

Apt packages, repository additions, pip resolution and npm installs are
**not** part of phase 1 — `apt-get update` is never planned offline, and an
apt package the machine does not already have, a pip or npm step, or a
third-party apt repository is refused by name. **Apt and pip offline
support is phase 2.** A git build may still need the archive's own build
dependencies even once its source is mirrored, and a Node application may
still need npm packages even once its tarball is. `--offline` reports each
such gap by name before anything runs, rather than failing partway through
a transaction.

This project does not claim that every software install works offline —
only that the data layer, the pinned payloads above, and hardware signing
readiness (next) do. A profile with a gap is deferred by name; the rest of
the transaction still runs.

## `file://` mirrors

A mirror does not have to be served over HTTP. `station set --mirror
file:///mnt/bunker-export` works the same way an `http://` mirror does for
plain D-070 downloads — every byte is still checked against the engine's
own digest, so an un-enrolled `file://` mirror is exactly as trustworthy (or
untrustworthy) as an un-enrolled HTTP one: none at all, by design.

Enrolling a `file://` mirror verifies its signed catalogue the same way:

```
hammunition mirror enrol file:///mnt/bunker-export
```

The export is a plain directory tree, read with no headers and no network:

```
catalogue.json
catalogue.sig.d/0.sig
osm-regions/north-america/us/delaware
kiwix-library/ham.stackexchange.com_en_all
inputs/region-outline/north-america/us/delaware.poly
```

exactly the paths an HTTP Bunker would answer at
`<mirror>/catalogue.json`, `<mirror>/catalogue.sig.d/<n>.sig`,
`<mirror>/<unit>/<name>` and `<mirror>/inputs/<kind>/<name>`. This is how a
drive the Bunker project writes for you (its own export tooling, not this
engine's) can carry a verified catalogue to a machine with no network at
all, not even a LAN.

## Group Bunkers and sharing

A Bunker in `group` mode shares one catalogue between several operators,
each telling data apart that is theirs alone from data meant for everyone.
Every artifact and input entry still appears in the catalogue — the engine
parses and keeps all of them — but each one carries a `share` of `all` or
`owner:<enrolment id>`, and a request to a group Bunker carries
`X-Hammunition-Enrolment: <your enrolment id>` so the Bunker can answer
404 for bytes that are not yours. Lookup on the engine's side keeps only
`all` entries and entries whose owner matches your enrolment id; everything
else is treated as absent.

**This is a sharing filter, not access control.** The enrolment id travels
in a plain HTTP header, in clear text, over a LAN that is (per the rule
above) never meant to be reachable from the internet in the first place —
but it is not a secret, and nothing stops another machine on the same LAN
from sending a different id and reading what that id can see. Personal-mode
Bunkers ignore the filter entirely and answer every entry regardless of its
`share` tag; the Bunker's own writer still tags rows with the operator's
enrolment id even in personal mode, specifically so that a personal Bunker
can later become a group one without re-tagging its data.

## Hardware signing readiness, without PAM

```
hammunition doctor
```

reports, read-only and never as root, whether this machine is ready to sign
with a hardware key, without changing anything:

- **PC/SC daemon** (`pcscd`) active or not.
- **FIDO2 token** / **PIV token** present, informational either way.
- **OpenSSH supports `-sk` signing** (needs OpenSSH 8.2+).
- **FIDO2 access** / **PIV access**: whether *you*, running `doctor` as
  yourself without `sudo`, can reach the token at all — "user access
  unmeasured: run hammunition doctor as yourself, without sudo" if you did
  not, "token access denied or unmeasured; inspect the archive's device
  rules" if you did and it still cannot be reached.
- **Bunker key strength**: every key already enrolled in
  `~/.config/hammunition/mirror.json` (the same list `mirror status`
  shows), its algorithm, size and the weak-RSA warning text above if it
  applies.

None of this signs a test message, refreshes a Bunker's trust or accepted
serial, or writes PAM configuration. Installing the `security-keys` profile
is apt-only and writes no PAM configuration either; what the archive
packages it pulls in do to PAM or debconf on their own is unmeasured across
the target matrix, and is a separate, later, explicitly-consented step from
anything on this page.

## Afterwards

The transaction log records where each download actually came from:
`source` (`mirror`, `publisher` or `cache`), `fetched_from`, and
`mirror_failure` when the mirror was passed over
(`docs/reference/transaction-log.md`).

## What the Bunker asks the engine

`hammunition artifacts --json` lists every artifact the engine would fetch
for a selection given on the command line, with no station and nothing
installed read; it is how the Bunker learns what to keep. Books are given
as `--reference-books ID,ID`, the way regions are given as `--map-regions`;
with none given, `kiwix-library` is listed as deferred, *no books
selected*.
`docs/reference/cli.md` describes the command and
`docs/reference/json-interface.md` its `artifacts` document.

Open Repeater's CC0 repeater list (`open-repeater`, **D-074**) is in that
list like any pinned data, as `open-repeater/open-repeater.json`. A Bunker
that keeps it is worth more than usual here: Open Repeater's download
address carries no date, so its file changes whenever the site does and
the catalog's pin goes stale between regenerations, while the copy the
Bunker took at the pinned digest still installs. The repeater lists fetched
on request (hearham, the ETCC, Brandmeister) are in it too (**D-078**), as
unit `repeater-snapshots`, names `etcc.csv`, `brandmeister.json` and
`hearham.json`, check `unverified-fetch`: no digest, the publisher's URL, the
size from a `HEAD`, only the size and date to check. A Bunker may hold them
under `hold_unverified`, the same switch as the ACMA zip; `fetch-etcc`,
`fetch-brandmeister` and `fetch-hearham` read `<mirror>/repeater-snapshots/<name>`
first and the publisher if it is not there, and the layer says which. Nothing
a mirror serves there can be verified, so the layer is *unverified* either
way. The project never hosts these files: you bring them, or your Bunker
takes them from the publisher at your request. **RepeaterBook's API layer
(`fetch-repeaterbook`, D-081) is not among them**: it is fetched with your own
key for your own personal use, RepeaterBook's policy rules out caching and
re-serving it, so it is never mirrored and `artifacts` never lists it.

The ACMA's register (`acma-register`, **D-074**, amended 2026-10-01) is in
the list as `acma-register/spectra_rrl.zip` with check `unverified-zip`, the
day's size from a `HEAD` to the ACMA, and no digest, because the ACMA
publishes none and rebuilds the file daily. The station checks a mirror's
copy the way it checks the ACMA's: every member's CRC-32 and the tables the
repeater import reads. That is the one entry where the mirror is checked by
nothing but the file's own structure, so over plain http nothing ties the
copy to the ACMA; the plan says "unverified" either way. The file also
holds licensees' names and addresses, which the ACMA's licence does not let
you pass on for a private person: a Bunker serving it to your own machines
is a copy you keep, not one you share.
A Bunker may hold the register zip although it contains `client.csv`,
which clause 8 of the register's licence bars passing on: it exists for
operators to download their own data and set up their station, the mirror is
LAN-only by documented rule, and the engine never opens `client.csv`
(maintainer's ruling of 2026-10-02, **D-074**). If you do not want it on your
NAS, set Bunker's `hold_unverified = false`.

The infrastructure layers' three data units (**D-075**) mirror like any
pinned data: `faa-nasr-airports/APT_CSV.zip`, `eia-860m/eia860m.xlsx` and
`wri-power-plants/global_power_plant_database.zip`, about 26 MB together.
Two of them move under the catalog: the FAA publishes a new NASR cycle
every 28 days, and EIA moves each month's workbook to its archive address
when the next one is out, so a Bunker holding the pinned bytes keeps an
install working between a move and the pin's regeneration. The FCC tower
file and NOAA Weather Radio's list are fetched on request, unverified, and
are not in the list.
