<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
SPDX-License-Identifier: GPL-3.0-or-later
-->

# CLI reference

The `hammunition` command, at **v0.7.0 (alpha)**. Six backends are
implemented: **apt**, **source**, **git**, **binary**, **venv** (per-user
virtualenvs, hash-pinned end to end with `pip --require-hashes`) and **node**
(Node.js applications from a verified archive, D-037 — Node only ever from
the distribution's `nodejs`, refused at plan time when absent or too old, and
the registry fetch disclosed in the plan). pipx and CPAN re-measured to zero
users and left the 1.0 list (D-014 amendment, 2026-08-30); a package
declaring one is still **refused by name**. The
install/configure/remove cycle is VM-verified on Parrot, Kali and Debian 13
(`docs/reference/vm-verification-parrot.md` and siblings).

The source backend is the expensive half of the parity target: **57 of AHRL's
95 units cannot be satisfied by apt**, and 35 of those are source builds from
bundled tarballs.

## Installing the engine

A git clone is the supported install. The wheel carries the engine; the catalog
is a separate tree, and the CLI finds `catalog/` by walking up from its own
location, so running it from a checkout needs no configuration.

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
scripts/path-link.sh "$PWD"
hammunition status
```

`scripts/path-link.sh` is what `./bootstrap.sh` runs to put `hammunition`
on the PATH: it links `~/.local/bin/hammunition` to this checkout's
`.venv/bin/hammunition`, and the rules it follows are under `doctor` below.
Every example on this page is written `hammunition ...`. Where the shell
says `command not found` (bootstrap has not run, `~/.local/bin` is not on
the PATH until your next login, or the link was refused), run the checkout's
`.venv/bin/hammunition` by its full path, e.g.
`~/src/Hammunition/.venv/bin/hammunition status`.
`docs/getting-started/install.md` covers each case.

Override the catalog location with `--catalog DIR` or `HAMMUNITION_CATALOG`.
A directory with no `packages/` inside it is an error rather than an empty
catalog, because an empty catalog makes `list` print nothing and look like an
answer.

## Global flags

`--version` prints the engine version and exits. `--catalog DIR` points at a
catalog other than the checkout's own.

`--json`, before or after the verb, prints one JSON document on stdout instead
of text, for a front end to read; diagnostics go to stderr and the exit code is
unchanged. Every document, and which commands have one, is in
[json-interface.md](json-interface.md), generated from the code (**D-059**).
`install` and `uninstall` accept it only with `--dry-run`: a real install is
never driven through JSON. A command with no JSON form refuses it and runs
nothing.

**Progress for slow plans.** A plan asks publishers questions before it prints
anything: one `HEAD` per terrain tile, US Topo or FSTopo sheet, Kiwix book and
CoMaps map the plan would fetch, and a Geofabrik answer per map region (those
are asked one at a time). When
stderr is a terminal the engine says so, on stderr only:

```text
checking 412 terrain tiles against the Copernicus DEM bucket (needs the network)…
  137/412
checked 412 terrain tiles against the Copernicus DEM bucket in 31.2 s
```

The counter rewrites one line, at most twice a second. Nothing is written to
stdout, so `--json` and a piped plan are byte-identical to before.
`HAMMUNITION_PROGRESS=1` forces the lines on when stderr is not a terminal
(a log file gets a counter line every five seconds); otherwise a pipe or CI
run stays silent. The tile, sheet, book and map checks run four at a time and
report exactly as they did one at a time (**D-061**, amended 2026-10-02).

**A publisher that does not answer.** A probe is not final on its first
failure. On an HTTP 5xx, an HTTP 429, a connection error or a read timeout it
is tried up to three times, waiting 1 s, 3 s and 9 s, and each retry is one
line on stderr (under the same rule as the progress lines) naming the host and
the attempt: `prd-tnm.s3.amazonaws.com: HTTP 503 Service Unavailable; retrying
(attempt 2 of 3) in 1 s`. Any other 4xx is final at once. If the publisher
still does not answer:

- for a **profile member** the items it did not answer for are **deferred by
  name** (a US Topo sheet, a terrain tile, a Kiwix book, a CoMaps map, or all of
  a region's items in each unit that needs that region's Geofabrik outline) and
  the rest of the plan goes ahead. They are printed under "Will NOT happen" with
  the publisher's last answer quoted, appear in `--json` as `deferrals` entries
  of kind `package`, are written to the transaction log and shown by `status`,
  and one line at the foot says to run the same command again;
- for a **unit you typed** the plan refuses, saying the publisher is not
  answering right now. A 404 on a listed sheet is not an outage: it still
  refuses, naming `scripts/gen_ustopo_index.py --fetch`, because the carried
  index is stale (**D-039**, amended 2026-10-02).

Successful tile `HEAD` answers are reused from the artifacts cache for six
hours. Non-200 answers and failed requests are not cached.

No long option is accepted abbreviated, with or without `--json`:
`--dry` is `unrecognized arguments`, never `--dry-run` (**D-059**). A CLI
that guards installs and consent gates behind exact flags does not guess
which one was meant.

## Verbs

### `hammunition status`

What this machine is, what the catalog holds, and what has been done here.

```
Target: Debian GNU/Linux 13 (trixie) (ID=debian, version=13, arch=x86_64)
Debian family: yes
Catalog: /home/op/Hammunition/catalog
  58 packages, 56 of which resolve on this target
  4 profiles
Transaction log: /home/op/.local/state/hammunition/transactions.jsonl
  no transactions recorded
```

The target line reports what `/etc/os-release` said, not what we concluded from
it. A system that declares no `ID` is an error, never a guess — see
`docs/DESIGN.md` §8.

With `--json`, prints a `status` document
([json-interface.md](json-interface.md)): the same target, catalog and log,
and every unit a transaction here has named. A front end derives which
profiles those units belong to from `list --json`.

### `hammunition update [NAME...] [--user NAME] [--upstream] [--offline]`

Installed versus the catalog, as a report. Nothing runs, nothing is fetched,
and no network is used (**D-053**). With no names it compares every unit
the transaction log has ever named here; with names it resolves them the
way `install` does, profiles included, and compares those.

```
Comparing the 171 unit(s) the transaction log has ever named here.
Target: Parrot Security 7.3 (echo) (ID=parrot, version=7.3, arch=x86_64)
Units (171):
  a2d                     up to date             a2d 2.0.5-2
  acarsdec                behind the pin         on disk, but not attributed at ref v4.6: built at an earlier pin, or never verified here; `install acarsdec` rebuilds
  artemis                 re-checked on install  pip resolves the venv on every install; nothing to compare offline
  libacars                unknown                declares no binaries and no tree marker, so nothing on disk can be checked
  mshv                    manual                 Upstream posts numbered zips; re-pin by hand. (more in the manifest)
  …
145 up to date, 0 with a different apt candidate, 17 behind the catalog's pin, 0 not installed, 2 unknown, 4 re-checked on install, 3 manual.
apt lists: last refreshed 2026-09-12 06:38 EDT (`sudo apt-get update` refreshes them; this report does not)

To rebuild at the catalog's pin:
  $ hammunition install coil64 cwwav flaa … dumpvdl2

Upstream was not consulted: 27 unit(s) declare a probe that would ask GitHub, PyPI or a version file. …

Nothing above was executed.
```

Each row is one of seven states, and each state is a comparison against a
fact the engine already has:

| State | Which units | What it compared |
|---|---|---|
| `up to date` | apt units; built units | Every apt package installed at apt's candidate; or the build on disk and attributed by the log at the catalog's pin (**D-051**); or a vendor `.deb` this engine installed (#67) |
| `candidate differs` | apt units | An installed version that is not apt's candidate, both printed. The exact `apt-get install --only-upgrade --no-remove` command follows, never run: apt decides, and it never removes or downgrades through that command |
| `behind the pin` | built units; vendor `.deb`s | The effect is on disk but the log does not attribute it at the current pin: built at an earlier pin, or never verified here. `install NAME` rebuilds, and the command is printed |
| `not installed` | any | An apt package missing, or nothing declared is on disk |
| `unknown` | built units | The manifest declares no `binaries` and no tree marker, so there is nothing to check; the same units **D-051** cannot decide |
| `re-checked on install` | venv and node units | pip and npm resolve on every install; there is nothing to compare offline |
| `manual` | strategy `manual` | The first sentence of the manifest's cadence hint |

The apt comparison is against the archive **as the local lists describe
it**, and the report says when those lists were last fetched. It does not
refresh them: a report that ran `apt-get update` would be changing the
machine, and a laptop that last updated before a trip is told which day it
is comparing against.

**`--offline`** (**#381**) asks for the same report with a guarantee: it reads only
the local apt lists and the install records, never asks upstream, and requires
an enrolled Bunker whose catalogue verifies. With none enrolled it stops at
once, naming `hammunition mirror enrol URL`. It refuses `--upstream`, even for
an empty request, before anything else runs. Verifying the catalogue can
advance the serial recorded in the local mirror file; the report says so on its
last line (`offline` in `--json`), because that is a local write even though no
package is touched.

Without `--upstream` it does not ask upstream. Twenty-seven of this
laptop's units declare a GitHub, PyPI or version-file probe; whether the
catalog's *pin* is behind upstream is a question about the catalog, answered
over the network, and that is what the flag adds:

```
$ hammunition update --upstream
…
Upstream (25 asked):
  ais-catcher         current          catalog 0.70  upstream v0.70  (latest release of jvde-github/AIS-catcher)
  hamclock-next       newer upstream   catalog 1.5  upstream v1.6  (latest release of k4drw/hamclock-next)
  linbpq              newer upstream   catalog 25.39  upstream 25.40  (highest of 112 tag(s) at https://github.com/g8bpq/LinBPQ)
  yaac                current          catalog 1.0-beta230(03-Sep-2026)  upstream 1.0-beta230(03-Sep-2026)  (label at …, compared verbatim)
  …
22 current, 3 with a newer upstream, 0 differing in a way the numbers do not order, 0 unanswered.
A newer upstream is a catalog question: re-pin the manifest, measure the build, then
`hammunition install hamclock-next linbpq openhamclock` on a machine rebuilds at the new pin.
Answers came from GitHub, git hosts, PyPI or a version file; nothing was downloaded or written.
```

Each probe method asks one place: `github_release` reads the latest
release's tag from GitHub's API (a `GITHUB_TOKEN` in the environment is sent
there and nowhere else, for the rate limit); `github_tags` lists the tags
with `git ls-remote`, which needs no token on any host, and takes the highest
by its numeric parts; `pypi` reads the project's JSON; `label_file` fetches
the one-line label and compares it verbatim. The repository comes from the
probe's `repo`, else the git block, else a GitHub source URL. *Newer
upstream* is said only when the numbers order that way; anything else the
numbers cannot order is *differs*, with both versions shown. A probe that
cannot be answered (a timeout, a 404, nothing derivable) is an *unanswered*
row, never a crash. `apt_policy` and `binary_version` are not upstream
questions and are not asked.

Measured on the field laptop, 25 probes answered in 7.5 s, none
unanswered, and three pins found behind upstream the first time it ran.

For `osm-regions`, every installed region's `.source` sidecar is compared
to the pinned snapshot the station's own freshness mode would resolve to
*today* — the same one-period-back fallback `resolve()` itself uses when
this year's or this month's file is not yet pinned — never the newest pin
of any snapshot: that reported a yearly install at `260101` behind a
`260901` pin forever, since a yearly install never resolves to a
monthly-shaped snapshot and so never clears it. Offline, like the rest of
this report (D-053). The row is a count, never a region name or path — the
same reason `station show` prints a count (D-057; a region list says where
somebody lives or travels): `2 regions installed; 1 behind the pin (newer
map data pinned: 260101)`. Nothing installed is `not installed`; nothing
behind is `up to date`. `hammunition install osm-regions osm-navit` (the
footer's own command, since `install osm-regions` alone never reconverts
the derived maps) fetches and converts the newer file.

For `dem-copernicus` the row is a count, never a tile name, because a tile
name is a latitude and longitude: `43 terrain tile(s) installed; a tile
changes only when the publisher's tile list does`, or `no terrain tiles
installed`. Regions whose records say Copernicus publishes no tile for any
of their squares are counted on the end, again without a name: `; 1
region(s) with no published tile at Copernicus GLO-30 (sea, or land it does
not release)`. When `osm-regions` is behind, the footer's command also names
`osm-garmin` and `osm-routino` if they are installed, since they are built
from the same regions.

For `usgs-ustopo` (**D-068**) the row is a count too, since a sheet's name is
a place: `12 US Topo quad(s) installed, each at the edition the carried index
lists`, or, *behind the pin*, `...; 3 of them have a newer edition in the
carried index`, and then the footer's command names `ustopo-qmapshack` beside
it, which warps the new sheets.

For `comaps-maps` (**D-069**) the row is a count too, because a CoMaps map
id names a region: every map the station's regions need at its pinned
version and size is `up to date` (`2 map(s) installed at their pinned
version`); one installed under another version directory is `behind the
pin`, with `install comaps-maps` to fetch the pinned one; anything else is
`not installed`. Regions the carried table has no CoMaps map for are counted
on the end. With `--upstream`, a `comaps_maps` probe asks CoMaps' CDN, once
per unit, for the pinned version's `World.mwm` with a `HEAD`, and needs 200
**and** the pinned size, since a CoMaps mirror answers a missing file with
200 and a web page: `current` when it is published, `pin expiring` from 90
days after the version's date (the CDN keeps a version for months, not
forever), and `pin expired` when it answers 404 or 410, or 200 with another
size, which an install would refuse. Any other answer (a 503, a 429) is
`unanswered`, not an expired pin.
Both name `scripts/gen_comaps_pins.py`. The summary line counts the expired
and expiring pins.

With `--json`, prints an `update` document
([json-interface.md](json-interface.md)) with the same rows, counts and
commands. It keeps the text's count-only rule: `osm-regions` is a count
there too, never a region name.

### `hammunition maps regions [FILTER]`

Every region path Geofabrik's region index names, one per line, sorted.
`FILTER` is an optional case-insensitive substring; with none, every region
prints. The index (`index-v1-nogeom.json`, 0.51 MB, measured live
2026-09-28: 555 regions — smaller than `index-v1.json`'s 3.79 MB for the
same `properties.urls.pbf` shape) is fetched only when this command runs —
network on request, the same as `update --upstream`, never as a side
effect of any other command.

```
$ hammunition maps regions vermont
north-america/us/vermont
```

A region path here is what `hammunition station set --map-regions` takes,
comma-separated, and what `catalog/data/geofabrik-pins.yaml` pins. A
network failure (unreachable, a non-2xx response) is a named error and a
non-zero exit; nothing is downloaded or written.

With `--json`, prints a `regions` document
([json-interface.md](json-interface.md)): the filter and the matching region
paths. It is Geofabrik's list, nothing of yours.

### `hammunition artifacts [--map-regions R[,R…]] [--map-freshness MODE] [--reference-books ID[,ID…]] [--units U[,U…]]`

Every remote data artifact the engine would fetch for the selection on the
command line (**D-070**): each `data` unit's files, the Geofabrik extract of
each region, the Copernicus tiles each region's outline touches, each Kiwix
book given (**D-066**, the 2026-10-01 amendment of D-070), the US Topo and
FSTopo sheets and USGS 3DEP tiles each region's outline touches, and --
since **Task 16** -- every catalog-pinned program payload: a `source`
build's archive, a `binary` or `node` artifact, a `venv`'s own payload tree,
a `derived` block's converter tool, and a `git` block's `extra_files`. It
reads no station file and nothing installed on this machine, and installs
nothing; the answer is the same on every machine. It is what
[Hammunition Bunker](https://github.com/Renegade-Penguin/hammunition-bunker), a
LAN mirror of this data, asks to learn what to keep.

```
$ hammunition artifacts --map-regions north-america/us/vermont --units osm-regions,country-files
```

| Flag | Effect |
|---|---|
| `--map-regions R[,R…]` | Geofabrik region paths, as `station set --map-regions` takes them. None defers the map units |
| `--map-freshness MODE` | `yearly` (the default), `monthly` or `latest`: which dated file each region resolves to and how it is verified, exactly as in the plan |
| `--reference-books ID[,ID…]` | Kiwix book ids, as `station set --reference-books` takes them (`hammunition reference books` lists them). Each is listed by its id with the pinned URL, `sha256`, size and the book's own licence line, from the carried pins with no network asked. An id the book list or the pins do not carry is listed as deferred; a malformed one, or an empty list, exits 2. None defers `kiwix-library` as *no books selected* |
| `--units U[,U…]` | The units to list. Default: every unit with a `data`, `osm-regions`, `dem-tiles`, `mwm-regions`, `kiwix-books`, `register` or `topo-quads` install block, every unit with a `source`, `binary`, `venv`, `node` or `git` install block, every `derived` block naming a converter `tool` (Task 16), then `repeater-snapshots` (D-078: not a catalog unit, the on-request repeater lists a Bunker may hold, which can also be named here). A name not in the catalog, or a unit that fetches nothing (`osm-navit`, `navit`: apt-only, or a `derived` block with no `tool`), exits 2 naming it |

The network is asked as the plan asks it, and only for what the selection
names: Geofabrik for a region's dated file, its `.md5` and its `.poly`
outline, the Copernicus bucket for an unpinned tile's size and ETag, the
USGS buckets for a US Topo sheet's or a 3DEP tile's size and S3 ETag, and the
Forest Service's raster gateway for an FSTopo sheet's location and size. A
pinned region or tile asks nothing, and a catalog-pinned program payload
(`source`, `binary`, `venv`, `node`, a `derived` block's `tool`, a `git`
block's `extra_files`) asks nothing either -- its sha256 is already in the
manifest. What cannot be resolved — a region Geofabrik does not have, an
outline that cannot be read, a map unit with no `--map-regions`, a carried
US Topo/FSTopo/3DEP index this checkout lacks — is listed as deferred with
the reason; it does not change the exit code.

With `--json`, prints an `artifacts` document
([json-interface.md](json-interface.md)): per artifact the unit, its stable
name within the unit (what a mirror serves at `<mirror>/<unit>/<name>`), the
publisher URL, the check (`sha256`, `md5-publisher`, `etag-md5`, `sha1-publisher` for CoMaps' maps, D-069, `unverified-zip` for the ACMA register (D-074 amended
2026-10-01), or `unverified-fetch` for a repeater snapshot or an FSTopo sheet
with no carried pin), the
expected digest (null for `unverified-zip` and `unverified-fetch`: neither
is published), a nullable `part_size` (an `etag-md5` multipart part size in
bytes), where a publisher checksum was read, the size, the licence, and
`deferred`. The ACMA register's size is asked of the ACMA with one `HEAD`
each listing, because the file changes daily; when that fails the entry is
deferred. It carries the regions and books given, and nothing of the
station's.

The same document's `inputs` array (Task 16) is, for every region a
regional unit among `--units` needs, the exact UTF-8 bytes of its Geofabrik
outline and its four recorded selections (US Topo, FSTopo, Copernicus and
3DEP) -- the same text the engine's own stateless selectors would compute,
inlined with a sha256 and size, or a `deferred` reason naming why that one
input cannot be produced (a missing carried index, an unreachable or
malformed outline). An inline input over 8 MiB exits 2 naming it, asking to
narrow the selection with `--units` rather than silently truncating it. The
`git_pins` array is, for every `git` install block among `--units`, its
pinned revision for a Bunker to mirror as a verified bundle: the repo, ref,
commit (null and `deferred` for a tag with no recorded commit), whether the
tree has submodules to walk, and the manifest's own licence line. Neither
array clones a repository or asks a Bunker; see
[bunker-catalogue.md](bunker-catalogue.md) for the exact division of
responsibility between this command and the Bunker's own writer.

### `hammunition maps qmapshack [--configure-only]`

What the `qmapshack-offline` launcher runs (**D-061**). It adds
Hammunition's map, elevation and routing directories to QMapShack's own
settings, `$XDG_CONFIG_HOME/QLandkarte/QMapShack.conf` (by default
`~/.config/QLandkarte/QMapShack.conf`), then starts `qmapshack`. The keys
are `mapPath` (the Garmin maps, the contour map and, from **D-068**, the US
Topo mosaic's directory, whose `ustopo.vrt` QMapShack lists) and `demPaths`
(the elevation) under `[Canvas]`, and `Route/routino/paths` (the Routino
database) under `[Route]`: the names read from QMapShack 1.17.1's binary,
the groups measured on the field laptop (2026-09-29). An earlier version
wrote the two lists under `[General]`, which QMapShack ignores; its own
directories are taken out of those two `[General]` keys, a key left empty is
removed, and any other value there stays. Each
directory is added only if absent. Every value already there is kept in its
place, and nothing else in the file changes. A key holding `@Invalid()`,
which is how Qt writes an empty list, counts as empty. A new file is
created mode 0600. `--configure-only` edits and does not start QMapShack.

While your repeater layer exists (`maps repeaters import`), it also keeps
its directory in `poiPaths` under `[Canvas]`, and takes it out once the
layer is gone (**D-064**): a QMapShack left open during an import writes its
own list back when it exits, and this puts the path back before the next
start.

It also sets `routino\database=0` under `[Route]` when that key is absent
or negative, and leaves a value of 0 or more alone, since that is a choice
made in QMapShack. The key is the index of the database selected in the
Routing dock's *Database* list. Measured on the field laptop on 2026-09-29:
with `-1` there, QMapShack loaded the `hammunition` database and selected
nothing, and routing gave up without a message. QMapShack writes the index
back when it exits, so a `-1` stays until something changes it.

**BRouter (D-063).** When `brouter`'s tree holds one `brouter-*-all.jar`
and `brouter-segments` has built at least one routing file, it points
QMapShack's local BRouter at them: under `[Route]`, the keys of QMapShack
1.17.1's `Route/brouter` group (read from its `CRouterBRouterSetup.cpp`),
`brouter\installMode=local`, `brouter\localDir` (the tree),
`brouter\localBRouterJar`, `brouter\localSegmentsDir`,
`brouter\localHost=127.0.0.1` and `brouter\localBindLocalonly=true`, and
`brouter\localJava` (`java` on the `PATH`) only when it is absent or empty.
QMapShack saves every one of these on exit, so a key at QMapShack's default
(`localDir=.`, `installMode=online`) is treated as never chosen and
replaced; a `localDir` naming any other directory is the operator's own
BRouter, and then no BRouter key is touched and a line says so. With the
tree ours, the host and the bind are loopback whatever they held: QMapShack
passes the host to BRouter only with "bind to hostname only" on, and
BRouter otherwise listens on every interface. A quoted or `@`-typed value
in one of these keys leaves BRouter alone, with a line, and does not refuse
the launch. Which router the Routing dock shows (`Route/current`) is left
to the operator. QMapShack starts BRouter itself when its Routing dock uses
it, and stops it with QMapShack. It passes the host to BRouter only once it
has read BRouter's version, by running the jar with a 3 s limit; a probe
that times out would start BRouter on every interface, which the guide
says how to check (`ss -ltnp`) and the bench has still to measure.

It refuses, exit 1, changing nothing and starting nothing:

- under root, whose settings are not the operator's;
- when the file holds a line that is neither a `[section]`, a comment nor
  `key=value`;
- when one of those keys holds a quoted value or any other `@`-typed one;
- when the file is a symbolic link, not a regular file, or not UTF-8.

It prints a line to stderr for each kind of change it made: the
directories, the database selection, and BRouter's registration (with a
line when it switched BRouter from online to local or bound it to
127.0.0.1). A missing
`qmapshack` is a named error, exit 1, after the edit. There is no `--json`
form, because it replaces itself with a GUI (D-059).

### `hammunition maps splat`

Points SPLAT! at the terrain `splat-sdf` makes (**D-061, amended
2026-10-02**) by writing `~/.splat_path`, the one-line file SPLAT! reads
for its terrain directory. Per user: refused as root, exit 1. The file is
written only when it is absent (the directory and a trailing slash, mode
0644); one naming the directory already is left as it is; one naming
another directory is yours and is left alone, with a line saying to pass
`-d /usr/local/share/hammunition/data/splat-sdf/` instead; a symbolic link
or anything but a regular file there is refused with nothing changed,
exit 1. It always prints Signal-Server's `-sdf` argument, and says on
stderr when no terrain file is installed yet. Nothing writes the file
during an install. The coverage guide (`docs/guides/propagation.md`,
*Terrain for coverage plots*) has complete SPLAT! and Signal-Server
commands. No `--json` form.

### `hammunition maps comaps [--configure-only]`

What the `comaps-offline` launcher runs (**D-069**). As the operator, never
as root, it prepares two things CoMaps reads and then starts it:

- **The licence answer.** CoMaps shows a modal dialog with its licence and
  copyright notice until `EulaAccepted=true` is in
  `$XDG_CONFIG_HOME/CoMaps/settings.ini` (by default
  `~/.config/CoMaps/settings.ini`). The line is added only when no line sets
  that key: the file is `key=value` lines, and CoMaps stops on a duplicated
  key. An answer already there, either one, is left. A new file is mode
  0600. A line on stderr says it was recorded and where the notice is.
- **The maps.** Each map `comaps-maps` installed under
  `/usr/local/share/hammunition/data/comaps-maps/<version>/` is linked into
  `$XDG_DATA_HOME/CoMaps/<version>/` (by default `~/.local/share/CoMaps/`),
  where CoMaps looks for them. A regular file of the same name, a map
  downloaded in CoMaps, is left, with a line; a link of ours whose map is
  gone is removed; nothing else there is touched.

It then replaces itself with `/usr/local/bin/CoMaps`, with
`MWM_WRITABLE_DIR` set to that data directory and `MWM_RESOURCES_DIR` to
`/usr/local/share/comaps/data`. `--configure-only` prepares and does not
start it.

It refuses, exit 1, changing nothing and starting nothing: under root; when
`/usr/local/bin/CoMaps` is not installed (naming `hammunition install
comaps`); and when the settings file is a symbolic link, not a regular file,
or not UTF-8. There is no `--json` form, because it replaces itself with a
GUI (D-059). Started from the menu entry, which opens no terminal, its lines
on stderr, the licence answer among them, are not seen; the guide says so.

CoMaps reads its position from GeoClue2 only. Its "you are here" comes from
the GPS tether's unix socket, which GeoClue reads once `hammunition hardware
apply` has written its two files (below), while `hammunition maps
gps-tether` runs (**D-069**, amended 2026-10-01). Not yet measured on the
field laptop's own GeoClue; the navigation guide, section 17, says what the
bench owes.

### `hammunition maps gps-tether [--gpsd HOST[:PORT]] [--port N] [--position-port N] [--nmea-socket PATH | --no-nmea-socket]`

What the `gps-tether` launcher ran (**D-061**), and now the way to run it
once by hand. **The tether is its own project** (D-071 note, 2026-10-02):
`hammunition install gps-tether` installs `hammunition-gps-tether` from
<https://github.com/Renegade-Penguin/hammunition-gps-tether> and a systemd user
service for it. With that tree installed (or a `hammunition-gps-tether` on the PATH or in
`~/.local/bin`), this verb **runs it in its place**, passing every option given through, and prints on
stderr where it is running from; root is refused first. Without it, the verb
refuses (exit 1) and names `hammunition install gps-tether`; the engine carries
no copy. The service and a foreground run cannot share port 10110. The
rest of this section describes the tether itself. It watches gpsd's JSON, as
`xgps` and Navit do, and writes `$GPRMC` and `$GPGGA` for every position
with a 2D or 3D fix. It serves them on **127.0.0.1 port 10110 only**, for
QMapShack's *Realtime → Add source → GPS TCP/IP* and any other NMEA client, and prints
the host and port to enter:

```
Serving gpsd's position as NMEA on 127.0.0.1 port 10110, to this machine only.
In QMapShack: Realtime, Add source, GPS TCP/IP; host 127.0.0.1, port 10110.
The offline browser map (`hammunition reference serve`) reads it from http://127.0.0.1:10111/position.
Reading gpsd at 127.0.0.1 port 2947. Any number of NMEA programs may connect at once.
Options: --gpsd HOST[:PORT] for a gpsd on another machine, --port N if 10110 is taken, --position-port N for the map's.
Ctrl-C stops it. Navit reads gpsd directly and needs none of this.
```

| Option | Default | What it does |
|---|---|---|
| `--gpsd HOST[:PORT]` | `127.0.0.1:2947` | The gpsd to read: a host name or address, port 2947 when none is given. An IPv6 address goes in brackets (`[::1]`, `[2001:db8::7]:2947`); a bare one, an unclosed bracket, an empty host or a port outside 1 to 65535 is refused by name. |
| `--port N` | `10110` | The port to serve on, still on 127.0.0.1 only. 1024 to 65535; below 1024 (only root may listen there, and the tether refuses root) and above 65535 are refused by name, and so is anything that is not a number. |
| `--position-port N` | `10111` | The port of the browser map's position stream (**D-071**), on 127.0.0.1 only, with the same limits. The same port as `--port` is refused by name, so `--port 10111` needs `--position-port` too. |
| `--nmea-socket PATH` | off, or `/run/hammunition-gps/nmea.sock` once `hardware apply` has set GeoClue up | Also serve the same NMEA on a unix stream socket at PATH, for GeoClue (**D-069**). An absolute path of at most 107 bytes. |
| `--no-nmea-socket` | | Do not serve the socket, even where `hardware apply` has set GeoClue up. Given with `--nmea-socket`, refused by name. |

**GeoClue's socket (D-069).** With no option, the tether serves
`/run/hammunition-gps/nmea.sock` whenever Hammunition's GeoClue drop-in,
`/etc/geoclue/conf.d/90-hammunition-gps.conf`, is present (the file is the
marker), so the `gps-tether` launcher needs no second form. The socket is a
client like any NMEA client: the same sentences from the same gpsd watch,
counted in the same fan-out, and dropped by the same rules (more than
64 KiB unsent, or gone). It is mode 0660; the directory `hardware apply`
makes is setgid `geoclue`, so the socket takes GeoClue's group and no other
account can open it. Where the group cannot be had, or there is no
`geoclue` group, a line on stderr says GeoClue cannot read it. A stale
socket from a tether that crashed is replaced; a live one (another tether)
is refused, and so is anything at the path that is not a socket, which is
left alone. The socket is removed when the tether stops, only if it is
still the one this tether made. The default failing (its directory missing,
say) is a line on stderr naming the fix, and TCP and the map are served as
usual; a `--nmea-socket` path that cannot be served stops the tether, exit
1. The startup text gains a line naming the socket.

**The browser map's position (D-071).** A browser cannot read an NMEA
socket, so the tether also answers `GET /position` on 127.0.0.1 port 10111
as Server-Sent Events: one `data: {"lat": …, "lon": …, "mode": 2|3,
"time": …}` event per fix. An event stream is a client like any NMEA client,
counted in the same fan-out, so gpsd is watched while the map page is open
and not after. A request whose `Host` is not `127.0.0.1:<port>` or
`localhost:<port>` (DNS rebinding), or whose `Origin` is not a loopback page,
is refused with 403 before gpsd is asked; `Access-Control-Allow-Origin` is
sent only to a loopback page, so no web page from elsewhere can read your
position through your own browser. A request with no `Origin` must ask for
`Accept: text/event-stream` (so an image tag on some site cannot keep gpsd
watched; `curl -N -H 'Accept: text/event-stream'
http://127.0.0.1:10111/position` tests it from a terminal). Anything but
`GET /position` is 404 or 405. A request not finished within 5 s is closed,
at most 16 are held at once, and with gpsd unreachable the page gets a 503
saying so.

Neither option widens the bind: the feed is a position without
authentication, so another machine reaches it through
`ssh -L 10110:127.0.0.1:10110 <laptop>`, never a wider listener.

Any number of clients may connect at once, and each receives every
sentence. One gpsd connection is opened when the first client connects,
shared while any is connected, and closed when the last one leaves, so
every client gets the same bytes and gpsd is not watched while nobody
listens; if gpsd closes it, every client is closed and may reconnect. A
client that has already gone is noticed before the next is counted. Sends
never block: a client with more than 64 KiB waiting is dropped alone, and
the others keep receiving. A field gpsd did not give is an empty field,
except the time: with none from gpsd, the system clock in UTC is used.
Altitude is given on a 3D fix only. Satellites and HDOP come from gpsd's
latest `SKY`. On stderr it prints a line when a client connects, goes or
is dropped, with how many are connected; when gpsd cannot be reached or
closes the connection; and once when no position with a fix has arrived in
10 s.

It runs in the foreground until Ctrl-C (exit 0), closing every client and
the gpsd connection; nothing is installed as a service, and nothing is
executed. The engine refuses root, and a tether that is not installed,
before running anything: exit 1. After that the installed tether takes over
(`exec`) and its own exit codes pass through unchanged, hammunition-gps-tether
0.1.1's, not the engine's (the engine's 2 and 3 mean other things elsewhere):
a refused option or a port already in use is a named error, exit 3, with
nothing opened; a crash is 1; its own usage error is 2. There is no
`--json` form, because it is a server, not a document (D-059): `--json`
with any options gives the same one error document. The setups these
options are for (a gpsd on a Pi or a phone, a Bluetooth or serial
receiver, a rig's built-in GPS, a second machine) are in
`docs/guides/offline-navigation.md`, section 12.

### `hammunition maps navit`

What the `navit-offline` launcher runs (**D-064**). It starts `navit` on the
configuration `osm-navit` writes,
`/usr/local/share/hammunition/data/osm-navit/navit.xml`. When you have a
repeater layer (`maps repeaters import`), it first writes your own copy of
that configuration, `~/.local/share/hammunition/overlays/navit.xml` (mode
0600; `$XDG_DATA_HOME` honoured), with the layer's textfile map added to its
one enabled mapset, and starts Navit on the copy. It is rebuilt at every
start, so it follows each `osm-navit` reinstall. With no layer it starts
Navit on the generated file and deletes a copy of ours left from an earlier
layer. Under root it starts Navit on the generated file and writes nothing.
Your `~/.navit` directory is never touched.

It refuses, exit 1, starting nothing: when the generated configuration is
absent (`hammunition install osm-navit` writes it), and when the copy cannot
be written (a symbolic link in its place, a generated file with other than
one enabled mapset). A missing `navit` is a named error, exit 1. There is no
`--json` form, because it replaces itself with a GUI (D-059).

### `hammunition maps areas [--json]`

Every state and map region with files on this machine, what is loaded for each
(a state's repeater layers; a region's OpenStreetMap extract, converted Navit
map and browser tiles), the layers' sizes as measured on disk, their dates, and
whether each is **active** (**D-082**). Also the layers that belong to no area,
which are always active, and any `active_areas` entry that matches nothing
loaded. Read-only: nothing is written, fetched or registered. Exit 0; exit 1
only when the station file cannot be read. With `--json`, an `areas` document
([json-interface.md](json-interface.md)).

### `hammunition maps activate (CODE|REGION ... | --all | --none) [--dry-run] [--json]`

Makes the named areas the active ones (**D-082**): US state codes (`OH`) and map
region names (`north-america/us/ohio`, or `ohio`), several at once. `--all`
makes everything loaded active (the default when nothing was ever chosen);
`--none` makes none active, though a layer that belongs to no area stays. It
writes the station's `active_areas`, then re-registers QMapShack's `[Canvas]
poiPaths` (a directory of links to the active areas' `.poi` files,
`overlays/active-poi`, written by the same editor as `maps qmapshack`), your
Navit copy's map set (only the active regions' converted maps and layers) and
the list `reference serve` reads at its next start. A state and the region that
are the same ground (`OH`, `north-america/us/ohio`) switch together. It prints
what changed, is idempotent, and deletes nothing: only the derived links are
ever removed, never a layer or map. An area that is not loaded is accepted, with
a note. `--dry-run` computes everything and writes nothing.

Refused, exit 1: no area and neither `--all` nor `--none`, or more than one of
the three; an entry that is neither a state code nor a region name; under root
(the configuration is per user); a QMapShack settings file it cannot edit (named,
as `maps qmapshack` names it). With `--json`, an `areas-activate` document
([json-interface.md](json-interface.md)).

### `hammunition maps repeaters import [FILE...] [--exported YYYY-MM-DD] [--from-open-repeater [FILE] | --from-acma [FILE] | --from-osm | --from-direwolf-log FILE...]`

Converts your own repeater export into overlays for QMapShack and Navit, on
this machine, with no network (**D-064**). It reads, recognised from the
content:

| Input | What it must have |
|---|---|
| RepeaterBook GPX export | `<wpt>` elements with `lat` and `lon`; the callsign and output frequency are taken from `<name>`, then `<desc>` (there a number written with "MHz" first; any frequency must fall in an amateur repeater band from 10 m to 23 cm, or GMRS, so a tone or a coordinate is not taken for one); a waypoint without both is kept under its own name, or under the callsign found when it has no name |
| RepeaterBook CSV export | a header with `Callsign`, `Frequency`, `Lat` and `Long`; `Input Freq`, `PL`, `TSQ`, `Nearest City`, `Landmark`, `Use`, `Operational Status` and `Last Update` are read when present |
| hearham.com's JSON, as served | the array `https://hearham.com/api/repeaters/v1` returns; an entry without `callsign`, `frequency`, `latitude` and `longitude` is skipped and counted |
| Your own CSV | exactly the header `callsign,output_mhz,offset_mhz,tone,mode,lat,lon,name,notes`; WGS84 decimal degrees, UTF-8; a frequency outside 1 to 10,000 MHz (one typed in Hz, say) is skipped and counted |

It refuses, by name and with the reason, and then writes nothing: a CHIRP
CSV and a CHIRP `.img` (neither has coordinates; CHIRP's RepeaterBook query
keeps only "near <city>"), a RepeaterBook CSV without `Lat` and `Long`, KML
(deferred: export GPX from the same search), and XML carrying a DOCTYPE. One
refused file refuses the whole import. A row with no usable position or no
callsign is skipped and counted by reason, with its first line numbers.

Rows from every file are merged on callsign, output frequency and position
to 0.01° (about 1 km): the same pair on two hills stays two repeaters. When
merged rows both carry `Last Update`, the newer is kept, otherwise the first
read, and the count is printed. The layer is named
`Repeaters (own export YYYY-MM-DD, personal use)`, dated by `--exported`,
else by the oldest file's modification date.

It writes the layer's files into `~/.local/share/hammunition/overlays/repeaters/`
(`$XDG_DATA_HOME` honoured; directory 0700, files 0600). Each file is
written whole under a temporary name and renamed over the old one, so no
file is ever half-written; an import interrupted between two renames can
leave new and old files side by side, which the next import replaces and
`remove` clears, temporaries included:
`repeaters.gpx` (QMapShack's *File → Load*, a phone, a Garmin unit;
symbol `Tall Tower`), `repeaters.poi` (a Mapsforge POI collection) and
`repeaters.navit.txt` (a Navit textfile map, `poi_custom0` with a label and
Navit's tower icon), and `repeaters.rows.json`, the rows as data, which the
all-sources file is rebuilt from (**D-074**). Then it adds that directory to
`poiPaths` under `[Canvas]` in QMapShack's settings, with the same editor and
refusals as `maps qmapshack`, and writes your Navit copy as `maps navit`
does, with a map for every layer present. Each import replaces its own
layer and leaves the others; to combine your export files, give every file
to one import.

**One layer per source (D-074).** An import reads exactly one source and
writes it as its own layer, under its own file stem in the same directory:

| Option | Layer id | File stem | Layer name |
|---|---|---|---|
| `FILE...` (above) | `export` | `repeaters` | `Repeaters (own export …)` or `(hearham …)` |
| `--from-open-repeater [FILE]` | `open-repeater` | `repeaters-open-repeater` | `Repeaters (Open Repeater YYYY-MM-DD, CC0)` |
| `--from-acma [FILE]` | `acma` | `repeaters-acma` | `Repeaters (ACMA, YYYY-MM-DD)` |
| `--from-osm` | `osm` | `repeaters-osm` | `Repeaters (OpenStreetMap, ODbL, YYYY-MM-DD)` |
| `--from-direwolf-log FILE...` | `aprs-heard` | `repeaters-aprs-heard` | `Repeaters heard off the air (APRS objects, YYYY-MM-DD)` |

The options exclude each other and `FILE...`; `--exported` dates your own
export only, and each other source is dated by its own data. An Open
Repeater file, the ACMA register, an ETCC CSV or a Direwolf log given as
`FILE` is refused, naming the option or command that reads it; any other
zip is refused with "unpack it".

- **`--from-open-repeater`** reads the file the `open-repeater` data unit
  installs (`/usr/local/share/hammunition/data/open-repeater/open-repeater.json`;
  refused, naming `hammunition install open-repeater`, when it is not
  there), or `FILE`, a copy you downloaded from openrepeater.org. An entry
  without a position, a callsign or a frequency is skipped and counted; an
  offset under 50 is read as MHz, otherwise kHz (the file holds both). The
  layer is dated by the newest entry's `last_verified`.
- **`--from-osm`** filters every region extract installed under
  `/usr/local/share/hammunition/data/osm-regions/` with `osmium
  tags-filter` (as you, into a temporary directory; `osmium-tool`, which
  `osm-navit` installs) and downloads nothing. A repeater is an object
  tagged `communication:amateur_radio:repeater` (`yes` or a callsign),
  `communication:amateur_radio=repeater`, or with a
  `…:repeater:frequency_out`; `communication:ham_radio:*` is read the same.
  A frequency without a unit is tried as MHz, kHz, Hz and Hz ×10, and the
  first that lands in a repeater band is taken (all four spellings occur);
  a shift without a sign is noted, not claimed. A way is placed at the mean
  of its nodes; a relation is counted and skipped. Dated by the oldest
  extract's snapshot. The text and the document name the extracts'
  directory, never a region, and carry no digest.
- **`--from-acma`** reads the ACMA's Register of Radiocommunications
  Licences as the `acma-register` unit installs it
  (`/usr/local/share/hammunition/data/acma-register/spectra_rrl.zip`;
  refused, naming `hammunition install acma-register`, when it is not
  there), or `FILE`, a copy of `https://cdn.acma.gov.au/rrl/spectra_rrl.zip`
  you downloaded (**D-074**, amended 2026-10-01). A row is a transmitter on a
  licence of sub-service 602, *Amateur Repeater*; its input is the receiver
  of the same licence and `EFL_SYSTEM`, its position its site's. Skipped and
  counted, with their `device_details.csv` line numbers: a licence not
  granted, no site, no position, no callsign, a frequency outside 1 to
  10,000 MHz. Kept only inside the bounding box (from each extract's PBF
  header) of a region extract installed under
  `/usr/local/share/hammunition/data/osm-regions/`; a row outside every box
  is counted without line numbers, since which rows fall outside says where
  your regions are. No region installed, an extract without a readable box
  (named by its number, never its file), or no row inside any box is
  refused, exit 1, and nothing is written; the last says the register
  covers Australia only. `client.csv`, the licensees' names and addresses,
  is never opened. Dated by the register's own `licence.csv` timestamp.
- **`--from-direwolf-log`** reads Direwolf's `-l` daily logs or its `-L`
  file (header measured from Direwolf 1.8.1:
  `chan,utime,isotime,source,heard,level,error,dti,name,symbol,latitude,longitude,speed,course,altitude,frequency,offset,tone,system,status,telemetry,comment`).
  Kept: rows whose `dti` is `;` (an APRS object) with a frequency in a
  repeater band; `offset` is Direwolf's signed kHz, `tone` its Hz. An object
  heard again merges, the newest hearing kept. Dated by the newest hearing.
  The log does not say whether an object was killed, so a killed object is
  shown like a live one. This layer is never part of the all-sources file.

**The all-sources file.** After every import, fetch and remove,
`repeaters-all.gpx` is rebuilt from the directory layers (every layer but
`aprs-heard`) when two or more can be read, and deleted otherwise. Rows are
taken best source first: your export or own list, the RSGB ETCC, Open
Repeater, hearham, Brandmeister, OpenStreetMap. A row joins a row of
another layer with the same output frequency that is within 0.02° (about
2 km), or has the same callsign and is within 0.25°; the first keeps its
position and fields, fills an offset, tone, mode or place it lacks, and its
description names every source that listed it. Rows of one layer never
join each other. The count is printed (`All sources: N repeaters from
layers …, M joined across sources`). GPX only: QMapShack and Navit already
show every layer. A layer imported before D-074 has no `.rows.json`, and
a `.rows.json` with a field of the wrong type is unreadable; either is named
as left out until it is imported again. When the file cannot be rewritten,
the layer just imported is still written and registered, the reason is
printed (`All sources: not rebuilt: …`) and the command exits 1. Every
`--from-osm` message names an extract by its number (`region extract 2 of
3`), never by its region.

Before the counts it prints each source's licence text: for a RepeaterBook
export, "Data courtesy of RepeaterBook.com", personal non-commercial use,
never redistributed, converted on this machine only, positions approximate,
and RepeaterBook's terms at `repeaterbook.com/about/legal`. Every GPX is
treated as a RepeaterBook export, because a real export's layout has not
yet been measured. The text prints counts, paths and the layer name, never a
callsign or a position.

Exit 0 when written and registered; 1 when refused, when no row has a
position, under root, or when QMapShack's settings could not be edited or
your Navit copy could not be written (a symbolic link in its place, a
generated configuration without exactly one enabled mapset); in those last
two the layer is still written, and the reason named.

With `--json`, prints a `repeaters` document
([json-interface.md](json-interface.md)): the layer and its id, each file's
counts and digest, the files written, what each program was told and the
all-sources file. Like the text, it carries no callsign, no position and no
region.

### `hammunition maps repeaters fetch-hearham`

Fetches hearham.com's open repeater list, `https://hearham.com/api/repeaters/v1`
(about 9.5 MB, the whole world, on 2026-09-29), when you run it and at no
other time, and converts it exactly as `import` does (**D-064**). It prints
what it is about to fetch before the request. hearham publishes no checksum
and no dated snapshot, so the sha256 of what arrived is printed and recorded
in the layer, named `Repeaters (hearham YYYY-MM-DD, unverified)`. hearham
states no licence for the data; it is carried under **D-033**, used on your
request and never redistributed, and hearham's own line, that it should not
be relied upon "for medical emergencies, or any other life-and-death
operations", is printed. The answer is bounded at 64 MB and parsed like any
file of yours; anything but hearham's list is refused, exit 1, and nothing
is written. There is no `--json` form: the disclosure is for a person to
read. Nothing is ever fetched from RepeaterBook.

### `hammunition maps repeaters fetch-etcc`

Fetches the RSGB ETCC's UK repeater list, `https://ukrepeater.net/csvcreate_all.php`
(about 62 kB, 803 rows on 2026-10-01; answered 200, `application/csv`, no
`ETag`, `Cache-Control: max-age=0,no-store` when checked the same day), when
you run it and at no other time, through the same bounded, HTTPS-only fetch
as `fetch-hearham`, and writes it as the `etcc` layer, named
`Repeaters (RSGB ETCC YYYY-MM-DD, unverified)` (**D-074**). It prints what it
is about to fetch first. `txMHz` is read as the repeater's output, `rxMHz`
minus it as the offset, the `ANALOG`, `DMR`, `DSTAR` and `FUSION` flags as
the modes. **Positions are at Maidenhead-locator precision**: a
four-character locator puts the repeater at its square's centre, tens of
kilometres from the site, and the description says which locator it was.
ukrepeater.net states no licence; the list is carried under **D-033**,
fetched on your request, never redistributed, and the sha256 of what
arrived is printed and recorded. Anything but the ETCC's CSV is refused,
exit 1, and nothing is written. No `--json` form.

`fetch-etcc`, `fetch-brandmeister` and `fetch-hearham` each take `--no-mirror`.
Without it, when the station names a LAN mirror, each asks
`<mirror>/repeater-snapshots/<name>` first (`etcc.csv`, `brandmeister.json`,
`hearham.json`) and the publisher on any failure there, including bytes that
are not that list; the layer is unverified either way and says where it was
read from (**D-078**).

### `hammunition maps repeaters fetch-brandmeister`

Fetches Brandmeister's DMR device list, `https://api.brandmeister.network/v2/device`
(no key; about 9.5 MB and 31,993 devices on 2026-10-01; answered 200,
`application/json`, no `ETag` when checked the same day), on request only,
and writes the `brandmeister` layer, named
`DMR repeaters (Brandmeister YYYY-MM-DD, unverified)` (**D-074**). It says
first that **most entries are hotspots, which are personal locations**:
only a 6-digit id whose transmit and receive frequencies differ is kept
(2,857 on the day measured); 7- and 9-digit ids and any device whose
transmit equals its receive are dropped from memory before anything is
written, and only their counts are printed. `tx` is the output, `rx` minus
it the offset; the colour code and master are in the description.
Brandmeister publishes no terms for this API; carried under **D-033**, the
observed sha256 recorded. No `--json` form.

### `hammunition maps repeaters fetch-repeaterbook --state NAME|CODE [--state …] [--county NAME …] [--country NAME]`

Fetches repeaters from RepeaterBook's API with the operator's own token, through
the `repeaterbook-client` unit (the unofficial `repeaterbook` 0.13.0 client,
registered with RepeaterBook as "RepeaterBook Python Client", App #114), and
writes one layer per state, `repeaterbook-<AREA>` (`repeaterbook-OH`; RepeaterBook's
`state_id` outside the US, `repeaterbook-CA01`), each named
`Repeaters (RepeaterBook OH, personal use, YYYY-MM-DD, unverified)` (**D-081**,
**D-074** amended, #325), so QMapShack's POI dock has one tick box per state.
**Built against the documentation and the client's source; not yet run against
the live API.**

The unit must be installed (`hammunition install repeaterbook-client`); if it is
not, exit 1 names it before anything else. The token is `REPEATERBOOK` (the client's
own variable name) or, when the station names a Doppler project and config,
Doppler (`resolve_secret`: [station-settings.md](../guides/station-settings.md#secrets-for-downloads));
none is exit 1 with both ways named. It reaches only the runner subprocess's
environment, never argv, a log or a document. The engine runs the unit's venv
python on `src/hammunition/repeaterbook_runner.py`, which asks the client for the
state and prints one JSON document; the client's User-Agent is left as RepeaterBook
approved it. `--state` takes a US state name or two-letter code, repeatable (one
run each, with a pause between), mapped to the FIPS `state_id`; `--country`
defaults to `United States`; Canada and Mexico take `CA01`, `MX14` and the like.
`exportROW.php` is not carried. A `401`, `403` or `429` is exit 1, never retried,
nothing written; so is an answer whose rows lack `Callsign`, `Frequency`, `Lat`
or `Long`. Rows off the air, with no usable position, callsign or frequency are
skipped and counted. RepeaterBook's attribution and personal-use terms are printed
first. A re-fetch of a state replaces that state's layer whole. `--county NAME`
(repeatable; exactly one `--state`) asks the client's own county parameter, one
request per county, merged into that state's layer; an answer near the 3,500-row cut
prints the advice to use it. An earlier merged `repeaterbook` layer is left alone and
mentioned once (`remove --layer repeaterbook` deletes it). The layers are **never
mirrored and never listed by `artifacts`**: they are the operator's own, 0600, and may
not be shared. No `--json` form, no `--no-mirror`.

### `hammunition maps repeaters list [--layer ID]... [--near GRID|LAT,LON] [--within KM] [--band BAND]... [--mode MODE]... [--json]`

Reads back the layers in your repeater directory. Read-only: it writes
nothing, fetches nothing and never rebuilds `repeaters-all.gpx`. The text
lists each layer (id, repeaters, date, name, with `personal use` and
`unverified` where they apply), what it left out and why, how many repeaters
the layers make once joined across sources, and each source's credit; it does
not list the repeaters, unless you name a place, a band or a mode (below).
`--layer` (repeatable) reads only those layers; an id that is not a layer, or
is not there, is reported as left out, not as an error.

To look a repeater up by place and by what it speaks:

```
hammunition maps repeaters list --near FN31pr --within 60 --mode DMR
```

- `--near GRID|LAT,LON`: a Maidenhead locator of four, six or eight characters
  (the centre of the square) or `LAT,LON` in decimal degrees. Default: the
  station's grid square when `station set` has one; with neither, there are no
  distances and that is not an error. A value that is neither is exit 1.
- `--within KM`: only repeaters this far or nearer. Needs a position, from
  `--near` or the station; without one it is exit 1 and says so.
- `--band BAND` (repeatable): `10m`, `6m`, `2m`, `1.25m`, `70cm`, `33cm`,
  `23cm`, `13cm` or `other`, from the output frequency.
- `--mode MODE` (repeatable, any matches): `FM`, `DMR`, `D-STAR`, `YSF`, `P25`,
  `NXDN`, `M17`, `TETRA`, `ATV`; a source's spelling such as `dstar` or
  `fusion` is accepted. Anything else is a usage error, exit 2.

With a position the rows are nearest first, each with its distance and
compass bearing; each line shows the band, the modes, offset, tone, any
digital detail the source gave (`dmr_color_code 1`) and the place. Naming a
place, a band or a mode prints that list in the text; a bare `list` stays the
layers' summary.

A layer it cannot read (written before D-074 kept its rows as data, or a
damaged rows file) is left out with the reason and the rest is returned:
**a partial list is exit 0**, so a program can draw what there is. No
directory, or no layer, is exit 0 and says so. Exit 1 only when the directory
itself cannot be read.

With `--json`, prints a `repeaters-list` document
([json-interface.md](json-interface.md)): the layers, those left out, every
joined repeater with the layer it came from and `personal_use`, and the
credits to print. Every row carries `modes`, `band`, `digital`, `distance_km`
and `bearing_deg`; the document carries `centre` (`argument` or `station`: for
the station's square, the centre of that square) and `within_km`. It is for
local programs, not for pasting.

### `hammunition maps repeaters remove [--layer ID]`

Deletes every layer's files, the all-sources file, your Navit copy, and the
directory when it is left empty; anything else you put there stays. It
takes the directory out of QMapShack's `poiPaths` and changes nothing else
in that file. With `--layer` (`export`, `acma`, `open-repeater`, `osm`,
`etcc`, `brandmeister`, `aprs-heard`, `repeaterbook` (the earlier merged layer) or
`repeaterbook-AREA` for one state, `repeaterbook-OH`) it deletes that layer only, rebuilds the
all-sources file from what is left, and rewrites your Navit copy with the
layers that remain. Nothing to remove is exit 0; a QMapShack settings file
it cannot edit is exit 1, named.

With `--json`, prints a `repeaters-removed` document
([json-interface.md](json-interface.md)): the layers asked for, the files
deleted, what each program was told and the all-sources file.

### `hammunition maps infra import (--from-osm [--layers LAYER,LAYER] | --from-nasr | --from-eia | --from-wri) [--merged]`

Infrastructure and EMCOMM points on your maps, one layer per source
(**D-075**). Each layer is four files in
`~/.local/share/hammunition/overlays/infra/` (directory 0700, files 0600,
each renamed into place): a GPX (`infra-<id>.gpx`) for QMapShack's File >
Load and phones, a Mapsforge `.poi` QMapShack keeps as a POI collection, a
Navit textfile, and a GeoJSON the browser map draws. One import writes its
own layers and leaves every other layer as it is. Refused as root.

**One layer per region** (issue #327, **D-082**). Every theme is written once
for each installed region extract, the extract's file slug ending the layer id
and the file names: `osm-medical-north-america-us-ohio`,
`infra-osm-medical-north-america-us-ohio.poi`. That slug is the one `maps
areas` and `maps activate` read, so activating a region draws that region's
infrastructure in QMapShack, Navit and the browser map with no other switch.
`--merged` keeps the shape from before the split: one layer per theme across
every region, which has no area and is always drawn. A data source's layer
(`--from-nasr`, `--from-eia`, `--from-wri`, `fetch-fcc-asr`, `fetch-nwr`) is
clipped to each installed region's header box, so a point inside two
overlapping boxes is in both regions' layers. Every source carries
coordinates, so none stays region-less except under `--merged`. When a
merged layer from an earlier version is still on disk, the import says once
that it is still registered and draws each point a second time; nothing is
deleted until you run `maps infra remove --layer ID`.

- `--from-osm` filters the region extracts installed here with osmium, as
  you, into eight layers: `osm-medical` (hospitals, clinics and doctors,
  pharmacies), `osm-responders` (fire stations, police, ambulance stations),
  `osm-supply` (fuel, supermarkets, hardware, EV charging, drinking water),
  `osm-shelter-candidates` (schools, community centres and town halls,
  places of worship: **candidate, not a designated shelter**, in the
  layer's name and every description), `osm-transport` (aerodromes,
  helipads, railway stations), `osm-power` (substations and plants),
  `osm-telecom` (communications masts and towers) and `osm-water` (water
  works, wastewater plants, pumping stations, water towers). `--layers
  medical,water` writes only those. Nothing is downloaded. Licence line
  `© OpenStreetMap contributors, ODbL 1.0`.
- `--from-nasr` reads the installed `faa-nasr-airports` unit: every
  airport, heliport and seaplane base in your regions' boxes, with its
  status and use. Licence line `FAA NASR <cycle>, public domain`.
- `--from-eia` reads the installed `eia-860m` unit: one point a plant, its
  operator, technologies and summed nameplate megawatts. Licence line
  `Source: U.S. Energy Information Administration (<Mon YYYY>), public
  domain`.
- `--from-wri` reads the installed `wri-power-plants` unit, outside the US
  only, and says EIA-860M covers the US. Licence line `WRI Global Power
  Plant Database v1.3.0 (2021), CC BY 4.0`.

The three data imports keep what lies in the boxes the installed
extracts' headers carry (a box is a rectangle, so it reaches across a
state line); an extract without one is named by its number and left out,
and none at all is refused, naming `hammunition install osm-regions`. A
layer this import finds empty has its old files deleted and listed; an
import that finds nothing at all changes nothing and exits 1. QMapShack's
`[Canvas] poiPaths` holds the directory while any layer has a `.poi`, and
your Navit copy (`overlays/navit.xml`) carries every repeater and
infrastructure layer.

```
$ hammunition maps infra import --from-osm
© OpenStreetMap contributors, ODbL 1.0

Input: /usr/local/share/hammunition/data/osm-regions (osm-extract)
Read: 2362 objects, 0 skipped
Note: filtered on this machine from the region extracts already here; nothing downloaded
Medical (OpenStreetMap, ODbL, 2026-09-30): 187 points
...
```

With `--json`, prints an `infra` document
([json-interface.md](json-interface.md)): the route, the licence lines,
what was read (the extracts' directory with no digest), counts and skips,
each layer's name, count and files, and what QMapShack and Navit were told.
It carries no place's name or position and no box. Each layer view carries
`area` (the region's file slug, null for a merged layer) and `active`; the
layer names and file paths name the regions, as the repeater layers' name
their states.

### `hammunition maps infra fetch-fcc-asr [--merged]`

Fetches the FCC's weekly Antenna Structure Registration file,
`https://data.fcc.gov/download/pub/uls/complete/r_tower.zip` (37,810,019
bytes on 2026-09-27; no checksum published), when you run it and at no
other time, through the repeaters' bounded, HTTPS-only fetch, and writes
the `fcc-towers` layer, `FCC towers (unverified, YYYY-MM-DD)`, dated by the
file's own `counts` record (**D-075**: on request, unverified, by the
maintainer's delegate's ruling). It prints what it is about to fetch first.
Only `RA.dat` and `CO.dat` are read; **`EN.dat`, the owners' contact names,
e-mail addresses and telephone numbers, is never opened**, and of `RA` the
signature and street-address fields are never kept. A structure is kept
when its registration is constructed or granted, it has no dismantle date,
it has a structure coordinate, and it lies in your regions' boxes. Licence
line `FCC Antenna Structure Registration, US Government work, public
domain`; the sha256 of what arrived is printed and recorded. No `--json`
form.

### `hammunition maps infra fetch-nwr [--merged]`

Fetches NOAA Weather Radio's transmitter list,
`https://www.weather.gov/source/nwr/JS/ccl-data.js` (754,735 bytes on
2026-10-01), on request only, and writes the `nwr` layer, `NOAA Weather
Radio (unverified, fetched YYYY-MM-DD)` (**D-075**). Each transmitter keeps
its callsign, frequency, power, site, forecast office and every county's
SAME code; **its live status is dropped** before anything is written, so
check a transmitter is on the air before you rely on it. A transmitter
within 1.0 degree of a region's box is kept: measured, that keeps every
transmitter serving Delaware's and Vermont's counties. Licence line
`NOAA/NWS, public domain, not an official NWS product`. No `--json` form.

### `hammunition maps infra remove [--layer ID]`

Deletes every infrastructure layer's files, merged and per-region, and the
directory when it is left empty; anything else you put there stays. With
`--layer` (a theme: `osm-medical`, `osm-responders`, `osm-supply`,
`osm-shelter-candidates`, `osm-transport`, `osm-power`, `osm-telecom`,
`osm-water`, `faa-airports`, `eia-plants`, `wri-plants`, `fcc-towers` or `nwr`,
which is the merged layer only; or `<theme>-<region slug>`, one region's) it
deletes that layer only. QMapShack's
`poiPaths` and your Navit copy follow the overlay layers that remain,
repeaters included. Nothing to remove is exit 0.

With `--json`, prints an `infra-removed` document
([json-interface.md](json-interface.md)): the layers asked for, the files
deleted and what each program was told.

### `hammunition reference books`

The Kiwix books the catalog offers (**D-066**), one per entry of the
hand-written `catalog/data/kiwix-books.yaml`: the id `station set
--reference-books` takes, the pinned file's size, the publisher's licence
line, and `[chosen]` / `[installed]` marks. Read from the catalog, the
station file and the disk; nothing is fetched.

```
$ hammunition reference books
...
ham.stackexchange.com_en_all             75.9 MB  CC BY-SA
                                                  Amateur Radio Stack Exchange
...
ifixit_en_all                            3.57 GB  CC BY-NC-SA 3.0 — non-commercial
                                                  iFixit repair guides
```

With `--json`, prints a `books` document ([json-interface.md](json-interface.md)):
every book with its id, title, pinned file and size, licence and licence
URL, and whether it is chosen and installed. Which books somebody reads is
not where they are, so unlike map regions the ids are named everywhere.

### `hammunition reference serve [--port N] [--position-port N] [--readsb-json DIR]`

The offline reference on one page, on **127.0.0.1 only** (**D-066**):

```
$ hammunition reference serve
Offline reference: http://127.0.0.1:8480/  (this machine only; Ctrl-C stops it)
  books: kiwix-serve on http://127.0.0.1:8481/wiki/
  map: http://127.0.0.1:8480/map/  (2 region(s); your position from `hammunition maps gps-tether` on port 10111)
```

The page, from the engine's own standard-library server on port 8480,
lists every installed book (a link into Kiwix, with its licence line), every
ICS form (served from `/forms/`), and how to use the dictionaries (`dict
WORD` in a terminal; goldendict-ng on the desktop). The books are served
by `kiwix-serve`, started as a child on the next port:

    kiwix-serve --library -i 127.0.0.1 -p 8481 -r /wiki -b -M -a <pid> <library.xml>

`-i 127.0.0.1` is always there: without it kiwix-serve listens on every
address of the machine, and its default port, 80, needs root (both
measured 2026-09-29). `-b` blocks links out of the books, `-M` reloads the
library when it changes, `-a` makes kiwix-serve exit if this process dies.
The library is rebuilt by `kiwix-manage` from the installed books every
time the verb starts, in `~/.cache/hammunition/reference/library.xml`, as
you: parsing downloaded files is the reader's business, never root's.

| Option | Default | What it does |
|---|---|---|
| `--port N` | `8480` | The page's port, still on 127.0.0.1; kiwix-serve takes N+1. 1024 to 65534; anything else is refused by name. |
| `--position-port N` | `10111` | Where the map page asks the GPS tether for your position, on 127.0.0.1: the tether's own `--position-port`. 1024 to 65535. |
| `--readsb-json DIR` | `/run/readsb` | The directory readsb writes `aircraft.json` to, which the aircraft page reads, read-only. An absolute path; a relative one is refused by name. |

**The offline map (D-071).** When `vector-map-kit` is installed, the same
server also serves the map (with no `osm-pmtiles` region installed, the
page says so and what to run):
`/map/` (the page), `/map/regions.json` (the installed regions),
`/map/tiles/<slug>.pmtiles` and `/map/kit/<path>` (MapLibre GL JS,
pmtiles.js, the OSM Bright style, sprite and fonts). Each file is served by
its exact installed name only, found when the verb starts: a region built
while it runs appears after a restart. Every file answers a single HTTP
`Range` with 206 and `Content-Range` (pmtiles.js reads the tiles that way,
and Python's plain `http.server`, which ignores ranges, makes it fail:
measured), `HEAD` with its size and `Accept-Ranges: bytes`, and a range past
the end with 416. A request whose `Host` is not `127.0.0.1:<port>` or
`localhost:<port>` is refused with 403, on every path, so a web page whose
name an attacker points at 127.0.0.1 cannot read which regions you carry.
The page loads nothing from anywhere else, draws "© OpenMapTiles ©
OpenStreetMap contributors" on the map as the licences require, and shows
your position when `hammunition maps gps-tether` runs. Without the kit the
landing page says what to install instead.

**Infrastructure on the map (D-075).** Tiles built by the converter's
version 2 carry an `infra` layer (power lines and plants coloured by
voltage after Open Infrastructure Map, masts, pipelines, water works,
hydrants), which the page draws over OSM Bright and credits; the style's
BSD-3-Clause notice is at `/map/infra-style-licence.txt`. Each layer you
wrote with `hammunition maps infra` is served from your own overlay
directory as `/map/overlays/infra-<id>.geojson`, listed at
`/map/overlays.json`, and drawn as a toggled layer with its licence in the
credit; a layer written while the server runs appears after a restart.

**The aircraft map (D-071, amended 2026-10-02).** When the `tar1090` unit is
installed, the same server serves tar1090 at `/aircraft/`: the pinned
archive's `html/` by exact installed name, a `config.js` and an
`hammunition-layers.js` of the engine's own, and `/aircraft/data/<name>.json`
read from readsb's directory (a plain `name.json` of letters, digits, `_` and
`-`, a regular file, never a link, sent `no-store`; nothing else under
`data/`; `receiver.json` is built from readsb's own, reduced to version,
refresh and position, so the page reads plain `aircraft.json` and never asks
for the binary or globe forms). `/aircraft` redirects to `/aircraft/`. The page cannot call out:
tar1090's settings for photographs, routes and overlays are off, its online
map layers do not exist, and every response carries a Content-Security-Policy
naming no host (`default-src 'self'`, `connect-src 'self'`, `img-src 'self'
data: blob:`, `form-action 'none'`, `frame-ancestors 'none'`). The one base map is your PMTiles regions
(with `vector-map-kit` and `osm-pmtiles`); without them there is none, the
aircraft are drawn on a plain background and the page says why. The Host
check applies. The directory is read when a request arrives, so readsb may
start after the page. The verb prints the address, the directory and the
basemap line:

```
  aircraft: http://127.0.0.1:8480/aircraft/  (tar1090 over readsb's JSON in /run/readsb; the basemap is your offline map)
```

A tree that is installed but whose `index.html` is not the pinned one is named
as a warning and not served; the rest of the page goes on.

With no books installed, no kiwix-serve is started and the page says how to
choose some. With books installed and `kiwix-serve` or `kiwix-manage`
missing, it refuses naming `hammunition install kiwix-tools`, exit 1. It
refuses root, exit 1. A port in use is a named error, exit 1. Ctrl-C stops
both servers, exit 0; kiwix-serve exiting on its own stops the page, exit
1. There is no `--json` form: it is a server, not a document (D-059).
`docs/guides/offline-reference.md` is the operator's walk-through.

### `hammunition maps phone`

Gathers the phone files the laptop has built into one folder and prints the
ways to carry them to a phone (**D-067**). **It transfers nothing and serves
nothing**: every route it prints is a command for you to run.

It copies each installed Mapsforge map (`mapsforge-map`), Mapsforge POI file
(`mapsforge-poi`) and Garmin map (`osm-garmin`, from `navigation`) from
`/usr/local/share/hammunition/data/` into `$XDG_DATA_HOME/hammunition/phone/`
(by default `~/.local/share/hammunition/phone/`, created mode 0700), named
`<region slug>.map`, `.poi` and `.img`, and writes `SHA256SUMS` beside them
in the format `sha256sum -c SHA256SUMS` checks. Each source is hashed as it
is copied and each copy is hashed again after it is written. A copy that
already hashes the same is left alone, so a second run copies nothing. A
file the previous run listed in `SHA256SUMS` whose region is no longer
installed is removed; nothing else in the folder is touched, including a
`.map` you put there yourself, symbolic links and subdirectories. With no
phone file installed at all, the folder is left as it is.

```
$ hammunition maps phone
Phone files in /home/you/.local/share/hammunition/phone:
  north-america-us-vermont.map        12345678 bytes  copied
  ...
  SHA256SUMS: check a copy with `sha256sum -c SHA256SUMS` in the folder

Nothing was transferred. To carry them to a phone:

1. Laptop hotspot and a web browser
   ...
     python3 -m http.server 8000 --bind 10.42.0.1 --directory /home/you/.local/share/hammunition/phone
```

The routes: **the laptop's hotspot** (`nmcli device wifi hotspot`) with
`python3 -m http.server` **bound to the hotspot's address** (10.42.0.1,
NetworkManager's default for a hotspot), so the files are served on the
hotspot link and not on any other network the laptop has joined, after a
check bound to 127.0.0.1; **USB file transfer** (MTP: `kio-extras` on KDE
Plasma, which Plasma installs, else `gvfs-backends`, `jmtpfs` or
`mtp-tools`); and two opt-ins, **`adb`** (brings `android-udev-rules`, a
system modification; the phone needs USB debugging) and **KDE Connect**
(the phone needs its app, installed while it had internet, and pairing).
The full walk-through is `docs/guides/offline-navigation.md`, section 14.

It refuses root, exit 1, and changes nothing. It refuses, exit 1, before
copying anything, when the folder is a symbolic link or not a directory,
and when its file system has less room than the copies need. With no phone
file installed it says which units build them and exits 0, touching
nothing.

With `--json`, prints a `phone` document
([json-interface.md](json-interface.md)): the folder, each file with its
unit, size, sha256 and whether this run copied it, the files removed, the
phone units with nothing installed, and the routes as data. File names carry
region slugs: for local programs, not for pasting.

### `hammunition list [all|packages|profiles]`

Everything in the catalog, with each package's install method **on this
machine**. A package that does not resolve here says `unsupported here` rather
than being hidden; a package with a recorded `broken` or `retired` status is
flagged with it.

With `--json`, prints a `catalog` document
([json-interface.md](json-interface.md)): every profile and package it
lists, with each package's method on this machine.

Each profile also carries its install state on this machine (console spec
E1): `members` (the units it names), `installed` (how many of those the
transaction log records as installed, the reading `status` reports as
`completed`; 0 when the log is absent) and `installed_size_bytes` (dpkg's
`Installed-Size`, converted from KiB, summed over the installed members whose
method here is apt, from one `dpkg-query` call; `null` when dpkg is absent or
no installed member is apt). Source, git, binary and data members contribute
nothing to that size in this release, so it is a floor. The text table shows
the same as `installed N of M` and a human size.

### `hammunition show PROFILE`

A profile's documentation, its package list, and — for a gated profile — the
full consent disclosure, printed without installing anything. This is how an
operator reads a disclosure before deciding, rather than while being asked.

With `--json`, `hammunition show PROFILE` prints a `profile` document
([json-interface.md](json-interface.md)), the disclosure included. A unit is
also accepted: `hammunition show UNIT --json` prints a `unit` document carrying
its manifest. Names are resolved against profiles first, then units, so a
profile wins if the same name exists in both. The text form still describes
profiles only.

### `hammunition install NAME... [--dry-run] [--yes] [-v|--verbose] [--no-refresh] [--no-sudo-keepalive] [--no-mirror] [--offline] [--recheck] [--full] [--user NAME] [--callsign CALL] [--grid-square LOC] [--node-alias NAME]`

**A re-run rebuilds nothing it has already built** (**D-051**): a source, git
or prebuilt-archive unit whose binaries are on the machine *and* whose build
the transaction log attributes to this engine at the manifest's current pin
reads `already installed`, and only its launcher and config steps are
planned. A build whose transaction never verified -- it failed after the
build steps -- is rebuilt, because nothing confirmed it.

Names may be packages or profiles, mixed freely.

**What a running command shows** (**#270**). Each step prints its `$` line. On a
terminal, a command that runs longer than two seconds gets one status line
under it, rewritten in place every second:

```
  $ git -C … submodule update --init --recursive --depth 1
    this step can take several minutes
  … 1m 42s  Receiving objects:  41% (3120/7600), 612.00 MiB | 4.10 MiB/s
```

It shows the time the command has been running and the last line it printed
(colour and control characters removed, cut to the terminal's width), and is
erased when the command ends. Nothing is added when stdout is not a terminal
(a pipe, CI, a file), so a transcript there is what it was. With `--verbose`
every output line is written as it arrives instead, indented under the `$`
line, on a terminal or not. The sudo keepalive's warnings (**D-062**) go
through the same writer, so they cannot land in the middle of the status line.
Neither mode changes the run log (**D-077**): it holds every line of every
command either way, and what the terminal shows is never written into it.

`this step can take several minutes` is printed under a step the backend knows
is long: a git block's submodule fetch, a `cmake`, `make` or `qmake` compile, a
virtualenv's `pip install`, a node build. The plan prints the same line under
those steps (`StepView.long_running` in `--json`). It states no duration; none
has been measured.

| Flag | Effect |
|---|---|
| `--dry-run` | Resolve everything, print exactly what would run, change nothing |
| `--yes` | Skip the confirmation. **Does not satisfy a consent gate** (D-021). Also suppresses the station prompt |
| `-v`, `--verbose` | Stream every line each command prints, as it arrives, in place of the status line described below (**#270**). The run log is the same either way |
| `--no-refresh` | Skip the `apt-get update` that otherwise opens every transaction with apt work (**D-044**). For a local mirror, or a station with no uplink. `--refresh` is the default and still parses |
| `--no-sudo-keepalive` | Do not hold sudo's ticket for the run (**D-062**). By default a run as a user that mixes root steps with steps that are not asks the password once, by `sudo -v`, before the first step, and keeps the ticket valid with `sudo -n -v` every 4 minutes until the run ends. With this flag each root step asks for itself, and one that follows a long step may prompt again. `--sudo-keepalive` is the default and still parses |
| `--recheck` | Ask every data item's publisher at plan time, including installed items the transaction log attributes. Without it those are trusted for 7 days (`attributed.RECHECK_AFTER_DAYS`): the plan prints `N installed data item(s) were not re-checked against their publishers` with the oldest attribution date, `--json` carries a `publisher_checks` line per item with `checked: false` and the reason, and an item attributed 7 or more days ago, or whose file is not the one the log recorded, is asked again. A re-check that fails is a `note:`, never a refusal; the real run verifies everything it fetches either way (**D-049**, #197) |
| `--no-mirror` | Ignore the LAN mirror set in station config for this run (**D-070**): every data download comes from its publisher. With no mirror set it changes nothing |
| `--offline` | Resolve everything from the enrolled Bunker's verified catalogue and never ask a publisher (**#381**, phase 1). The catalogue is read and verified once, before any apt probe; with no Bunker enrolled the run stops at once naming `hammunition mirror enrol URL`. `apt-get update` is never planned, and an apt package the machine does not already have, a pip or npm step, a third-party apt repository, and a unit whose map, terrain, topographic-sheet, reference-book, CoMaps-map or git resolution still asks a publisher are each refused by name (apt and pip on the Bunker are phase 2). A data unit counts only if every artifact is already in the cache or on the Bunker at the repository's own sha256 and size: a profile member with a gap is deferred whole, a unit you typed is refused, and a unit that depends on a dropped one is dropped with it. A source, binary, Python-venv payload or Node source download is a Bunker item named `<sha256>/<file name>` in its unit: it counts only if its verified bytes are already in the cache or the Bunker holds it at the repository's own sha256 (a unit already built needs neither), and a profile member without it is deferred whole while a unit you typed is refused. A download whose repository pin is weaker than a sha256 (an md5, SHA-1, ETag, size or nothing) is also checked, when it comes from the Bunker, against the sha256 the signed catalogue lists for it; a mismatch is refused naming both digests (online, the run falls back to the publisher and prints a `WARNING` line naming the Bunker, the item and both digests; a catalogue row that is malformed is a refusal offline and a warning plus publisher-only online). A vendor `.deb` whose dependencies are not all met by an installed package (at the stated architecture, and at a version in the stated range) is refused too, because apt would fetch them: before the run when its bytes are cached, and by the install itself right after the fetch when they are not (the plan says the check is deferred). A `Depends` field that does not parse (an unbalanced parenthesis, an unknown relation or architecture qualifier, an empty group, trailing text, an invalid version) is refused, never read as no dependency. The install runs `apt-get` with `--no-install-recommends --no-download`, so apt can fetch nothing, and the plan lists the Recommends that are not installed. The install also runs in a `bwrap` sandbox with the filesystem left writable (apt is installing real files system-wide) but `/run`, `/var/run`, `/tmp`, `/var/tmp`, `$XDG_RUNTIME_DIR` and the operator's home private and empty, so the package's maintainer scripts and triggers have no network and no pathname UNIX socket (docker, podman, a user's agent or proxy) to bridge through; with no working `bwrap` the vendor `.deb` unit is refused. An offline source build runs in the same sandbox read-only instead (`--ro-bind / /`) with writable binds only for the build tree and the install prefix, and additionally hides `/opt`, `/srv`, `/mnt` and `/media` — a build has no legitimate reason to read any of them, and a read-only bind alone does not stop a connection to a socket that lives there — so upstream's build code can neither fetch anything nor reach the common host UNIX sockets. `/usr/local` is not on that list: it is the engine's one unconfigurable install prefix, so the writable bind re-exposes it regardless, and hiding it first would misreport the guarantee. `unshare --net` alone is not accepted for either: it blocks no pathname socket, only IP and abstract ones; with no working `bwrap` the source unit is refused. Neither sandbox's socket hiding is exhaustive — a socket placed somewhere not on either list (`/usr/local`, `/etc`, `/var/lib`, ...) stays connectable; this closes the common bridges (docker, podman, dbus, a user's own proxy) and is not a complete guarantee. The native architecture comes from `dpkg --print-architecture` (the kernel's name only without dpkg), and `:any` means the native and every foreign architecture `dpkg --print-foreign-architectures` lists. Any other pinned download with no Bunker route yet is refused by name unless its verified bytes are already in the cache. Downloads come from the Bunker alone (`Bunker only` in the plan), a copy that fails its digest is discarded and refused, and no publisher is tried. Installed items are never re-checked against their publishers, even with `--recheck`. Every line the catalogue answered carries `offline; resolved from Bunker NAME (fingerprint), recorded TIME`, with any weak-key or age warning the signer carries. Cannot be combined with `--no-mirror`. With `--json` (and `--dry-run`) a refusal before resolution, such as no Bunker enrolled, is a refused `plan` document like any other. Accepting a newer catalogue advances the serial in the local mirror file, which the plan says as a local write and not a package action; `--dry-run` never writes it, offline or online, and the plan says what a real run would do. It changes no consent: `--yes` still does not satisfy a gate, and a real install is still never driven through `--json` |
| `--full` | Print every step of the plan expanded. Without it, a run of steps that repeat one template for many items (a US Topo sheet, a terrain tile, a Kiwix book) is printed as the template with `<placeholders>`, the first item written out in full, every item's own values on a line, and the totals; `--dry-run --full` prints the plan exactly as it was before grouping (**D-016**, amended 2026-10-02). `--json` always carries every step, with or without it |
| `--user NAME` | Who to add to groups. Defaults to `$SUDO_USER`, then `$USER` |
| `--callsign CALL` | Station callsign for this run. Overrides the saved value |
| `--grid-square LOC` | Maidenhead locator, four or six characters |
| `--node-alias NAME` | Short packet node alias, up to six characters |

**An enrolled Bunker, online.** With a Bunker enrolled and no `--no-mirror`, an
`install` reads and verifies its catalogue once at the start, so a publisher
that stays down after its retries can be answered from what the Bunker
recorded. If that cannot be done (the Bunker is unreachable, a signature fails,
the serial went backwards, the document is malformed, or the trust state cannot
be written) the fallback is off for the run: one `note:` names the reason and
says `--no-mirror` skips the Bunker, and the install goes ahead, since every
download is still checked against its own pinned hash. `--offline` refuses in
the same cases.

**File capabilities (D-079).** When a selected unit declares optional Linux
capabilities, the plan shows the target binary and exact `CAPABILITY=ep` grant.
Before the ordinary confirmation, the installer asks you to type `yes` for
that specific grant. `--yes` does not answer it. Declining (or having no
interactive terminal) skips only the capability step and installs the rest of
the transaction without granting it. For scripts, set
`HAMMUNITION_ACCEPT_CAPABILITIES_<UNIT>` to the exact grant string shown in the
plan, for LinBPQ `CAP_NET_ADMIN=ep CAP_NET_RAW=ep CAP_NET_BIND_SERVICE=ep`; a
value of `1` is refused. Successful
grants are verified with `getcap`, logged, and cleared on uninstall before the
attributed binary is removed. If LinBPQ cannot open a port afterwards, see
[troubleshooting](../troubleshooting/running.md#linbpq-capabilities).

**sudo's ticket, for the length of the run (D-062).** Run as a user, the
engine puts `sudo` in front of each root step and nothing else, and sudo
caches the password for 15 minutes by default (`timestamp_timeout`). A
transaction that alternates root steps with long unprivileged work -- a Navit
conversion, a Garmin map -- outlives that, and the next root step asks again on
a terminal nobody may be watching (issue #137: 7.8 hours at the prompt after 30
minutes of work). When a plan has both kinds of step and is not run as root,
it prints a section saying what happens instead:

```
sudo (D-062):
  sudo's ticket is kept valid for the length of this transaction; it is not extended
  beyond it. The password is asked once, by `sudo -v`, before the first step; then `sudo
  -n -v`, which cannot prompt, refreshes the ticket every 4 minutes from this process
  until the run ends. If a refresh fails it is reported once and not retried, and the
  next root step asks as it would have. --no-sudo-keepalive turns this off.
```

After the confirmation (or `--yes`) and before the first step, `sudo -v`
asks for the password on the terminal, as sudo always has; the engine never
reads, stores or passes it, and `--yes` does not change what sudo asks. A
thread of the same process then runs `sudo -n -v`, with stdin from
`/dev/null`, every 4 minutes, and stops when the transaction ends, succeeds
or fails. sudo's per-terminal tickets (`timestamp_type=tty`, Debian's
default) and global ones behave the same here: the refresh runs from the
same process on the same terminal as every root step, so it refreshes the
ticket those steps use. That is also why a loop in another window does
nothing for the install on a machine with per-terminal tickets. If
`sudo -v` does not succeed, nothing is refreshed and each root step asks as
it would have. If a refresh fails (sudoers changed, `timestamp_timeout` set
below 4 minutes, the ticket revoked with `sudo -k`), one warning says so and
the refreshing stops. The log records `sudo_keepalive_begin` and
`sudo_keepalive_end` ([transaction-log.md](transaction-log.md)).
`--no-sudo-keepalive` turns it off, and the section then says a later root
step may prompt again. A dry run prints the section, because it prints what
the real run would do, and never runs `sudo`. Run as root there is no ticket
to keep and no section. Only `install` holds the ticket: the root steps of
`uninstall` and `hardware apply` are not separated by long unprivileged work.

**Offline data (D-049).** A unit whose `install` method is `data` — a map
tileset, a Wikipedia ZIM, the DX-cluster `cty.dat` — is not software: the
engine fetches and verifies its files like any other download and puts them
under `<prefix>/share/hammunition/data/<name>/`, executing nothing. The plan
prints, before the confirmation, every artifact's size and URL, the unit's
licence and where it is stated, and the install directory, under the heading
*Offline data that will be downloaded and installed*. `uninstall` removes
the directory whole; it is namespaced, so it can only be ours.

**Map regions (D-057).** `osm-regions` and `osm-navit` take their regions
from station config (`station set --map-regions`, below), so before the
plan prints it asks Geofabrik which dated file each region resolves to and
how large it is, and discloses them under *Map regions, from station
config*:

```
Map regions, from station config (D-057):
  will be downloaded and installed:
    north-america/us/vermont        260101    44.4 MB  sha256, pinned by Hammunition
    north-america/us/new-hampshire  260101    68.1 MB  sha256, pinned by Hammunition
  will be converted for Navit (map sizes an estimate, measured on three regions, scratch on one):
    north-america/us/vermont        260101  about 40.0 MB
    north-america/us/new-hampshire  260101  about 61.3 MB
      licence: ODbL-1.0, stated at https://www.openstreetmap.org/copyright
      download total: 0.11 GB; about 0.21 GB of disk with Navit's maps (estimate, measured on three regions, scratch on one)
      installs under <prefix>/share/hammunition/data/
```

Each region line is the region, the snapshot (`YYMMDD`), the size, and how
the download is verified: **`sha256, pinned by Hammunition`** when the
region and snapshot have a row in `catalog/data/geofabrik-pins.yaml`,
otherwise **`MD5 from Geofabrik only; not pinned`**. `--yes` does not change
it. A region already installed at its snapshot is listed under *already
installed, current* and not downloaded again; one that could not be checked
(no network, Geofabrik down) but is installed is kept as it is, with a line
saying so and why. The commands section shows each fetch, each `maptool`
conversion (run as the operator in `~/.cache/hammunition/build/osm-navit/`,
with its output and scratch estimates), each install into the prefix, the
removal of any region no longer in station config, and Navit's
configuration written last.

It refuses at plan time, exit 2, changing nothing, when a region cannot be
resolved and is not already installed (named, with `maps regions` as the
way to check it), when a region that is about to be fetched — pinned or
not — cannot be reached (a pinned region resolves from the pin list with
no network at all, so this is checked explicitly rather than discovered
mid-transaction after apt has already run; an already-installed region is
never probed), when `/etc/navit/navit.xml` is missing and navit is not
in the transaction, and when a file system is short of the estimated space
(the download in the cache and the prefix, the converted map at 0.9× and
maptool's scratch at 2× the download; the map factor measured on three
regions — 0.77× on a country-sized one, 0.874× and 0.856× on two
US-state-sized ones — and the scratch factor on one;
the refusal prints the estimate and what is free). With no regions set the
two units are deferred by name and the rest installs (**D-035**). A region
that fails during the run — a download that does not verify, a conversion
that writes nothing — does not stop the others; the run ends exit 1 naming
every region that did not install.

**Terrain and QMapShack's maps (D-061).** When the plan holds
`dem-copernicus`, `osm-garmin`, `osm-routino`, `dem-qmapshack` or
`brouter-segments`, the map section gains a *Terrain* block. Before the plan prints, each region's tiles
are read from its record (`dem-copernicus/<slug>.tiles`) or, until its
terrain is first installed, chosen from its Geofabrik outline
(`<region>.poly`), fetched again by every plan until then and said so. Each
tile not installed is resolved from its pin or, unpinned, by a `HEAD` to the
bucket for its size and ETag; a pinned tile is asked with a `HEAD` too, so
an unreachable bucket refuses the plan rather than the transaction. From the
golden test's synthetic plan:

```
  Terrain, Copernicus GLO-30 elevation (D-061):
    atlantis/oceania  2 tile(s), 2 square(s) with no published tile (sea, or land Copernicus does not release); 39.1 MB to download
    atlantis/lemuria  1 tile(s); 25.2 MB to download
    atlantis/mu       0 tile(s), 3 square(s) with no published tile (sea, or land Copernicus does not release)
    warning: no terrain available for atlantis/mu from Copernicus GLO-30; its maps still install
    (a region's tiles are read from its outline at Geofabrik, fetched again
    by every plan until its terrain is installed and its record written)
    will be downloaded (2 tile(s), 64.3 MB):
      Copernicus_DSM_COG_10_N00_00_E000_00_DEM    39.1 MB  sha256, pinned by Hammunition
      Copernicus_DSM_COG_10_S01_00_W001_00_DEM    25.2 MB  MD5 from the publisher's object metadata; not pinned by Hammunition
    already installed: 1 tile(s)
      licence: Copernicus DEM licence, stated at https://spacedata.copernicus.eu/
  Built for QMapShack (sizes an estimate, measured on one region):
    Garmin map  atlantis/oceania  260101  about 44.6 MB (0.85x the download)
    Routino database over 2 region(s)  about 42.2 MB (0.67x the downloads together)
    contours for 2 tile(s)  about 11.0 MB, with up to 98.0 MB of scratch at a time
      about 0.16 GB of disk for terrain and QMapShack's maps (measured on one region)
```

Each tile line ends with how it is verified: **`sha256, pinned by
Hammunition`** when it has a row in
`catalog/data/copernicus-glo30-pins.yaml`, otherwise **`MD5 from the
publisher's object metadata; not pinned by Hammunition`**. The pin file
ships empty, so today every tile gets the second.

A square with no published tile is counted as such and never called sea:
the carried list cannot tell open sea from land Copernicus does not release.
A region with no published tile at all gets the `warning:` line, fetches
nothing and does not fail the run; its maps still install (D-061).

When `brouter-segments` (**D-063**) is rebuilt, *Built for QMapShack*
gains one line, from its test's synthetic plan:

```
    BRouter routing files over 2 region(s)  about 12.6 MB (0.2x the downloads together), elevation from 3 tile(s); built here, never downloaded from brouter.de
```

and the disk check counts the routing files under the prefix and, in
`~/.cache/hammunition/build/brouter-segments/`, 3x the downloads of scratch
(an allowance, not measured), the merged input when there are two regions
or more, one 5-degree square's `.hgt` files (25 at most, 25,934,402 bytes
each) and every square's `.bef`. Its steps, all as the operator in
`brouter.work` under one lock: a check that the jar and the two map-creator
filters are installed; `osmium merge` of the regions when there are two or
more; per 5-degree square with an installed tile, `gdalbuildvrt` over the
square and its one-degree ring, `gdalwarp` of each tile into a
one-arc-second `.hgt` and BRouter's `ElevationRasterTileConverter`; then
the map creator's `OsmFastCutter` (with `-DavoidMapPolling=true`),
`PosUnifier` and `WayLinker`; and last the install of every `.rd5`, all or
none, with its record. A failed step fails the routing files by name, keeps
the installed set (a rename failing partway removes it rather than leave it
mixed, and the next run rebuilds), and is reported by the same last
terrain step. The record names the jar and the filters' version the plan
installs, read from the planned manifests, so a BRouter upgraded in the
same run rebuilds the routing files in that run.

When `splat-sdf` (**D-061, amended 2026-10-02**) has tiles to convert, the
Terrain block gains its own heading, from its test's synthetic plan:

```
  Built for SPLAT! and Signal-Server (sizes an estimate, measured on one tile):
    SDF terrain for 3 tile(s)  about 21.0 MB (both resolutions, bzip2), with up to 0.10 GB of scratch at a time
```

and the JSON plan's `terrain` object carries `splat_tiles`,
`splat_estimate` and `splat_estimate_human`. The disk check counts one
tile's scratch in `~/.cache/hammunition/build/splat-sdf/` and every tile's
files under the prefix. Per tile, as the operator in `splat.work` under
one lock: `gdalbuildvrt` over the tile and its installed neighbours,
`gdalwarp` into a one-arc-second `.hgt`, SPLAT's `srtm2sdf-hd -d /dev/null
-n -32767` and `bzip2 -9`, the same at three arc seconds with `srtm2sdf`,
then both files installed with a `.source` sidecar and a link under
Signal-Server's name. A tile whose conversion fails is named by the same
last terrain step. With the station's `dem_source` set to `3dep`, the files
are made from the 3DEP tiles instead, as QMapShack's elevation is.

The commands section shows each tile's fetch (all fetches first, as every
download is), each install, each region's record, each Garmin build and the
Routino build (as the operator, in `~/.cache/hammunition/build/osm-garmin/`
and `.../osm-routino/`), each tile's contours (`.../dem-qmapshack/`), the two
virtual rasters, and a last step that fails the run by name if any of this
did not install. It refuses at plan time, exit 2, changing nothing:

- when a region's outline or a tile not installed cannot be resolved (every
  such one named together), including a tile whose ETag is not a
  single-part MD5 and an outline edge that jumps across ±180 in one segment;
- when the carried tile list is missing, empty or malformed;
- when the carried pins file (`catalog/data/copernicus-glo30-pins.yaml`)
  does not parse, has no `pins:` list, or has a row missing a key or
  carrying a malformed value or a tile pinned twice; the refusal names the
  file, and under `--json` it is one refused plan document;
- when a disk is short of piece 1's and piece 2's estimates together.

With no regions set, all four units are deferred by name with the rest of
the map data.

**US Topo (D-068).** When the plan holds `usgs-ustopo` or
`ustopo-qmapshack`, the Terrain block ends with a *US Topo* part. Each
region's sheets are read from its record (`usgs-ustopo/<slug>.quads`,
whole rows of the index, so an offline plan needs nothing else) or chosen
from its outline, the same fetch the terrain uses: the quads in the carried
index `catalog/data/ustopo-quads.txt` whose box overlaps an eighth-of-a-degree
cell the outline touches. Each sheet not installed is asked for with a
`HEAD` to USGS's bucket, which must answer with the size and ETag the index
carries. From the test suite's synthetic plan:

```
  US Topo, USGS 7.5-minute quads (D-068):
    atlantis/oceania  2 quad(s), 17.0 MB; 9.0 MB to download
    note: no US Topo quad covers atlantis/lemuria (US Topo covers the United States and its territories)
    (a region's quads are read from its outline at Geofabrik until
    they are installed and its record written)
    will be downloaded (1 quad(s), 9.0 MB):
      ZZ_Alpha_20240101     9.0 MB  MD5 from the publisher's object metadata; not pinned by Hammunition
    already installed: 1 quad(s)
      licence: Public domain (USGS), stated at https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits
    warped for QMapShack: 1 quad(s), about 9.0 MB (1.0x each download, measured on one quad)
      about 18.0 MB of disk for US Topo (measured on one quad)
```

Every sheet is checked against its S3 ETag: a single-part upload's is its
MD5, a multipart one's the MD5 of its parts' MD5s, reproduced by trying each
whole-MiB part size. The commands section shows each sheet's fetch and
install, each region's record, each warp and its overviews (as the operator,
in `~/.cache/hammunition/build/ustopo-qmapshack/`), the one `ustopo.vrt`,
and the same last step that fails the run by name if anything did not
install. A region outside the United States gets the `note:` line and does
not fail the run. The plan refuses, exit 2, changing nothing, when the
index is missing or empty, when an outline cannot be read, or when a sheet
not installed is not in the bucket as the index says (every such one named
together, with `scripts/gen_ustopo_index.py --fetch`, which regenerates the
index). Offline, a region whose record names an edition the index has since
replaced keeps its installed sheets, and a `note:` says so.

**FSTopo and 3DEP (D-068, amended 2026-10-01).** With `dem-3dep` planned the
Terrain block gains a *USGS 3DEP bare-earth elevation* part: with
`dem_source` unset or `copernicus` it says 3DEP is not chosen and that any
installed tile is removed; with `3dep` it lists each region's tiles and what
each region downloads (about ten times Copernicus), each tile checked by its
S3 ETag in the US Topo wording. `usfs-fstopo` is in no profile and is planned
only when typed by name (`hammunition install usfs-fstopo`), because the
Forest Service publishes no checksum. Its *FSTopo* part lists each region's
sheets; each sheet's line reads `sha256, pinned by Hammunition (the Forest
Service publishes no checksum)` when the catalog pins it, or `unverified:
the Forest Service publishes no checksum and Hammunition has pinned none;
only the size is checked`; a `warning:` counts the unverified sheets; and
when every sheet the regions need is pinned the part says `every FSTopo
quad your regions need is pinned by Hammunition`. `ustopo-qmapshack` reads
FSTopo sheets that are installed and builds `FSTopo.vrt` beside `ustopo.vrt`;
it never pulls `usfs-fstopo` into a plan. The JSON carries both parts as
`terrain.bare_earth` and `terrain.fstopo` ([json-interface.md](json-interface.md)).

**Recommends, per unit (D-052).** Recommends are not suppressed globally —
that would deviate from what every target distribution does, and several ham
applications get their runtime data that way. A single manifest may opt its
own packages out with `install_recommends: false`, for the measured case
where a package's Recommends conflict with the target's desktop stack:
Debian's `morse` Recommends `pulseaudio`, which `Conflicts: pipewire-alsa`,
so on a PipeWire desktop apt would satisfy the transaction by removing the
machine's audio routing and the plan refuses it (**D-022**, issue #61). Such
a unit's packages become a second apt set, simulated with
`--no-install-recommends` and installed by a second `apt-get install`
carrying it. The plan prints the set under *apt packages installed without
Recommends*, naming the units that asked, and both apt commands appear under
*Commands*. `--no-remove` is on both: the flag buys a unit its own apt
invocation, never an exemption from **D-022**.

**Suggestion groups.** A profile may suggest one-of-several optional
companions (the packet profile's mail client is the first): the run
*detects* first — any of the group's known commands on PATH means the
system's own choice is respected and nothing is offered — and only an
interactive run without `--yes` gets the selection, every option an
open-source catalog manifest, with skip always an answer. Non-interactive
runs note the skip and never block (the D-035 shape). Nothing from a
suggestion group is ever installed silently.

**With `--json` and `--dry-run`**, prints the plan as a `plan` document
([json-interface.md](json-interface.md)): exactly what the text plan prints,
section by section. A plan that refuses is still a `plan`, with `outcome:
"refused"`, every blocker, and exit code 2. **Without `--dry-run`, `--json`
is refused** with an `error` document and nothing runs: a real install is
never driven through JSON (**D-059**). A front end runs the ordinary command
in your terminal, where sudo, every consent gate and every disclosure are
this CLI's, then reads `status --json`. Every plan step has a stable, 1-based
index in execution order; `step_count` gives the total. Text plans show each
step's position, including the covered range of a grouped block, and real runs
print the same `step N/COUNT: DESCRIPTION` line before starting it. The plan
names your account, paths
in your home and the station's map regions, so the document is for a local
program, not for pasting into an issue. It never carries a rendered
configuration file, so the callsign in one is not in it.

**A rerun after a failure** plans only what is not done. When a unit's last
step finishes the engine writes a `unit_end` to the transaction log
([transaction-log.md](transaction-log.md)), so a failure in a later unit leaves
every earlier one recorded and `status` reports it `completed`, with
`completed_in_failed_run` naming the install. Run the same request again and
the plan lists the unit that failed and the ones after it. A built unit is
skipped (planned `already installed`) only when its declared binaries and tree
marker are on disk and the recording is at the manifest's current pin; a
missing file, or a moved pin, plans the build again. Units installed through
apt are decided by apt as before. Regional, derived, DEM, topo and CoMaps units
also resume when their completion fingerprint still matches the resolved
inputs and selection, and their backend confirms the expected files and records
are current. Changing a region, input digest or topo bound—or losing an output—
plans that unit again. The fingerprint is opaque and does not store the station's
raw region selection. Shared map-ledger checks are owned by the units they
validate. On a successful run, completion records are written only after
`verify_effects`; a failed check does not claim completion.

### `hammunition uninstall NAME... [--dry-run] [--yes] [-v|--verbose] [--user NAME]`

Removes what Hammunition itself installed, and only that (**D-004**). Names
may be packages or profiles, mixed freely.

The dry-run and JSON list each removal step with its 1-based execution index
and total count. A real uninstall prints the same `step N/COUNT: DESCRIPTION`
line before starting each step.

| Flag | Effect |
|---|---|
| `--dry-run` | Resolve the removal, print exactly what would run, change nothing |
| `--yes` | Skip the confirmation |
| `-v`, `--verbose` | Stream every output line as it arrives; see `install` |
| `--user NAME` | Whose transaction log to read. Defaults to `$SUDO_USER`, then `$USER` |

"Installed by Hammunition" is read from the transaction log, by replaying the
recorded commands that actually exited 0 — not from what a run *intended*.
Four attribution routes, each exact:

- **apt packages** — the recorded `apt-get install`/`remove` commands.
- **files under `/usr/local`** — the engine's own `install -D` commands (and
  the executable format's recorded destination). A same-named file the
  operator put there is not in the log and is never touched.
- **vendor `.deb`s** — the fetch cache names artifacts by their sha256, so
  the recorded install carries the manifest's own digest; the manifest's
  `deb_package` field names what to hand to `apt-get remove`.
- **namespaced trees and venvs** — `share/hammunition/<name>` and
  `venvs/<name>` can only be ours.
- **third-party apt repositories** — the two `install -D` commands that
  wrote `<name>.sources` and `<name>.gpg` (**D-040**). Both are removed
  and `apt-get update` runs afterwards so the lists forget the repository.
  A same-named file the log does not attribute is left in place and named.

Wrappers and desktop entries in your home are removed only after being read
back: the file must carry the engine's generated marker, or it is reported
and left. Which wrappers and entries to look at comes from two sources: the
files the transaction log says an install wrote (the unit that owns one is
read from its own marker), and the launchers the current manifest lists. The
manifest alone would miss a launcher it has since dropped, or a unit since
retired, so a `uninstall` reverses what the install did, not what the catalog
says today (#336). A recorded file whose marker is gone is the operator's
replacement and is named under *Left in place*. The plan partitions honestly
and prints every part:

- **Removing** — attributed apt packages (one `apt-get remove`, never
  `purge`: configuration a user may have edited stays on disk).
- **Removing artifacts** — venvs, installed trees, copied binaries,
  wrappers, desktop entries — each printed with the *basis* for believing
  it is ours: `namespaced`, `log`, or `marker`.
- **Left in place** — installed, but not installed by Hammunition; or
  present but unattributed by the log. Removing it would exceed the promise.
- **Already absent** — attributed but no longer installed.

What it deliberately does not reverse, and says so in every plan:
dependencies apt pulled in (`sudo apt autoremove` clears orphans), group
memberships, and any configuration files written — all recorded in the log.
A source or git unit whose build ran a real `make install` into `/usr/local`
is refused with the gap named: there is no file manifest to reverse, and a
file sweep pretending otherwise is the shim CLAUDE.md forbids. A staged,
recorded install is the planned fix.

After the commands complete, the removal is **verified** the same way an
install is (**D-031**): apt is re-probed, every removed artifact path is
re-checked absent, and the run is only reported clean when both confirm. A
removal apt quietly declined exits 1 with `verified: false` in the log.

With `--json` and `--dry-run`, prints the removal as a `plan` document
([json-interface.md](json-interface.md)), the same shape as an install's
with `removal` filled in. Without `--dry-run`, `--json` is refused and
nothing is removed, as for `install`.

### `hammunition menus apply [--gnome] [--menu-prefix PREFIX]`

Writes the curated **Hammunition** desktop-menu layer (**D-036**, **D-050**),
generated from the catalog's own category vocabulary — one taxonomy, no
second list. Per-user and unprivileged throughout.

- **Menu-spec desktops (KDE Plasma, Xfce):** a merged `.menu` tree shaped
  like Parrot's own tool menu — *Hammunition*, then eight groups in a
  declared order, titled as activities in plain words (*Operate the
  Station*, *Digital Modes & Morse*, *Packet, Mesh & Emergency Comms*,
  *SDR & Listening*, *Satellites & Propagation*, *Antennas, Bench &
  Programming*, *RF Security & Research*, *Learn & Practise*), then one
  submenu per catalog category — 56 of them (**D-055**'s 55, plus *Navigation & Maps*), each the thing
  a person looks for (*APRS*, *Winlink Email*, *Ships (AIS)*, *SSTV, Fax &
  Amateur TV*) — titled from the vocabulary with a gloss where the tag is
  jargon (*CW (Morse)*, *Rig Control (CAT)*; **D-054**). The groups are `catalog/categories.yaml`'s `groups:` list;
  every category belongs to exactly one, and the order is a menu-spec
  `<Layout>`, not the alphabet. A ninth group, *Workstation*, is declared
  `menu: false`: git, tmux and VS Code are catalog units, not radio
  software, so they get no submenu, no generated entry, and stay where the
  desktop already puts them. A unit whose manifest says `menu_submenu`
  (GNU Radio, 21 entries) gathers everything it ships into one nested
  submenu under its first category instead of listing it inline in every
  category it carries; a generated entry shows its manifest's
  `menu_title` (*Contest logger (tlf)*) with the unit's name kept in
  `Keywords=` for the launcher's search. Every apply also brings the
  launcher entries up to their manifests as they are now (categories,
  title, comment; the wrapper path is kept), generates an entry for a
  built unit from the binaries its manifest declares and the prefix holds,
  and writes a launcher a unit gained in the catalog after it was installed
  (D-050 amendment, 2026-09-13). A launcher of ours that runs `hammunition`
  by any path but today's engine (issue #145: a bare name from before the
  fix, or a checkout that moved) has its wrapper rewritten; its desktop
  entry is left to the refresh above, and a file without the
  `# generated by hammunition for` marker is never rewritten. A launcher of
  ours named like a binary elsewhere on the `PATH` (issue #174: the
  `rigctl` launcher ahead of hamlib's `/usr/bin/rigctl`) is removed, with
  its `hammunition-<name>.desktop` when that carries our package key, and
  the catalog's renamed launcher is written in the same run; both lines
  print, the removal as `[wrapper] <path> (shadows <binary>; removed)`. A
  manifest that still declares a launcher by such a name is refused with
  the clash named, and nothing is written. The park and
  wake entries run the engine by the same absolute path, and so does the engine's own entry for
  `hammunition console` (#302), written on every apply as
  `hammunition-engine-console.desktop` in the desktop's HamRadio category, since the
  `workstation` group draws no submenu. Each
  submenu includes the `X-Hammunition-<category>` markers every generated
  desktop entry carries **and, by `<Filename>`, the desktop entries the
  installed catalog packages ship themselves** — mapped at apply time from
  `dpkg -L` of each manifest's apt package (or `.deb` name) to the
  manifest's categories, **then checked on disk**: Parrot's `parrot-menu`
  rewrites the launcher set from an apt hook after every apt run, so an
  entry that exists is placed as shipped, one that is gone is placed by
  `parrot-<package>.desktop` when that exists, and one with neither is
  reported under the count rather than counted (#64). Whatever else
  carries the freedesktop `HamRadio` category and no manifest claimed is
  gathered at the tree's top level. The desktop's own copies of every
  entry are untouched (D-022).
  **Which root menu it merges into is decided, never guessed:**
  `--menu-prefix` wins, then the session's `$XDG_MENU_PREFIX`, then the
  root menus installed under `$XDG_CONFIG_DIRS/menus/` — exactly one
  `<prefix>applications.menu` means that one; several and no session
  variable is a refusal that names them. **Which directory the root merges
  is measured per desktop:** Xfce's garcon reads `<prefix>applications-merged/`;
  **KDE's kservice ignores the prefix and reads `applications-merged/`** —
  on the field laptop (Plasma 6, 2026-09-12) a tree written to
  `plasma-applications-merged/` produced no menu and every generated
  entry sat in *Lost & Found*. The file goes where the desktop reads, and a
  copy left in a directory it does not read is removed. On Plasma,
  `kbuildsycoca6` runs afterwards (disclosed) so the tree shows now.
- **A real `install` ends by re-applying this**, quietly, for the user who
  ran it: placement happens at apply time, and a tree applied before an
  install left 42 of 60 new entries loose under *Hammunition* on the field
  laptop. The plan discloses it before the confirmation; where no root menu
  can be decided (bare SSH) the install says so in one line and succeeds.
- **An entry for every installed radio unit (D-050).** Parrot's menu does
  this for 572 of its 671 entries, and the launcher's search is only as
  good as what has an entry. For each installed catalog unit that ships no
  desktop entry, declares no `launchers`, and has a category outside the
  hidden group, a per-user `hammunition-cli-<unit>.desktop` is generated:
  the unit's name, its manifest summary as the Comment, its categories as
  Keywords, `Terminal=true`, and the executable — the one named like the
  unit, else the package's only one. Several executables and none named
  like the unit (`rtl-sdr`: eight tools) is a guess this refuses to make;
  the summary names the unit and a `launchers` block in its manifest is
  the fix. A unit whose executables are all under `/usr/sbin` is a
  service, not an application, and is skipped with that reason. Generated
  entries and directory files from an earlier run that this run did not
  produce are removed; a launcher's `hammunition-<name>.desktop` is never
  touched.
- **GNOME:** one app-folder per visible group — *Hammunition · Station*
  and so on, since GNOME cannot nest — populated by the group's
  `X-Hammunition-<category>` markers (no app list to maintain) plus the
  placed entries under those categories unioned into its `apps` list, for
  the ones a distribution tagged some other way (Kali's `gqrx` and `chirp`
  carry `kali-radio-frequency`). Written 2026-09-12; **not yet run on a
  GNOME machine**. Applied only when `XDG_CURRENT_DESKTOP` says GNOME (or
  `--gnome` forces it), and it needs your desktop session's bus: over bare
  SSH it fails loudly rather than pretending. Lists are appended to, never
  replaced.
- **COSMIC:** unmeasured. Nothing is written for it until the Pop!_OS VM
  has been read.

### `hammunition doctor [--user NAME]`

A **read-only** health check: is this machine ready, and what is not yet set
up. It changes nothing, and it is the first thing to run on a fresh machine
or when something misbehaves — it turns the failures the engine would
otherwise hit mid-transaction into a report you read up front, each with the
one command that fixes it. Twenty-eight checks across four severities:

- **fail** — the engine cannot work until fixed (not a Debian-family system;
  no catalog). Exits non-zero.
- **warn** — a whole class of installs will fail or a feature is unavailable
  until fixed (no `python3-venv`, no compiler, no callsign, missing device
  group), but the engine runs and everything else works.
- **info** — a true fact that is not a problem (no ham hardware attached
  right now; udev rules not yet applied on a machine with no radios).
- **ok** — checked and healthy.

The **engine version** check (**#311**, shown when the engine runs from a checkout) compares the version `pyproject.toml` declares with the one the venv's metadata reports. An editable install keeps answering with the version it was installed at until `./bootstrap.sh` re-runs, so a release bump leaves it behind; the check warns and its fix, as argv, is `["hammunition", "self-update"]`.

The **run logs** check (an *info*, shown once a run has left a log) says how
many logs there are, their size, and how the newest ended; `hammunition logs
--last` prints it (**D-077**).

The **desktops** check is always information (**D-060**): the desktops
the session files in `/usr/share/xsessions` and `/usr/share/wayland-sessions`
(and the same under `/usr/local/share`) offer, which is what `install` decides a unit for one desktop against, and
the desktop of the session you are in, from `$XDG_CURRENT_DESKTOP`. Under
`sudo` that variable is usually gone, and the line says the session's
desktop is not known rather than guessing. A machine with no session files
(a server, a container) is reported as such. Session files that name no
desktop the catalog knows (COSMIC, Sway) are named as read, so a graphical
machine is never reported as a server. See `docs/desktops.md`.

The **rig** check (**D-073**) is read-only and never keys the transmitter.
With no rig set it is information. With one set, it names any value the
rig's kind still needs (with the `station set` flag), whether the
`hammunition-rigctld` user service is installed, disabled, failed or active
(`systemctl --user`), whether `rigctld` answers `\dump_state` on
`127.0.0.1:4532` (a read; nothing is set and PTT is never touched), whether
the rig's device is present now, whether the running `rigctld`'s arguments
match the station (from `/proc`), whether port 4532 is bound to loopback only
(a **fail** otherwise — the transmitter would be reachable off-machine), that
the loopback filter is running, and whether linger is on and whether
Hammunition turned it on. For a flrig or VOX station, which runs no `rigctld`
service, it says so rather than telling you to install one. See
`docs/guides/rig-control.md`.

The **time** and **hardware clock** checks (**D-058**) say what the clock
follows (the network or the GPS, with ntpd's offset), or that it follows
nothing and for how long (information under a day, a warning past one), read
with `ntpq -pn` and `ntpq -c rv` and no privilege. It warns when a GPS mode is
set but ntpd lacks the grants `hardware apply` installs (or gpsd its `-n` drop-in), when `gps-only`
is set with the receiver parked, and when ntpd runs on a DHCP-supplied
configuration. A machine with no battery-backed hardware clock (`/sys/class/rtc`
empty) is warned on any target, naming the fix: fit an RTC module. On a target
whose time daemon is not ntpsec the line is information naming the gap. See
`docs/guides/gps-time.md`.

The **gps-resume** check (issue #177) appears only when a GPS receiver is
attached (parked or awake) and gpsd is installed. It is ok when the resume
step `hardware apply` installs is in place as this engine writes it, enabled
for the four sleep targets, and warns, naming `hammunition hardware apply`,
when it is missing, from an older engine, or not enabled. See
`docs/hardware/power-control.md`, "After suspend".

The **hammunition** check (**D-059**) asks whether `hammunition` resolves on
your `PATH`, and to the checkout `doctor` is running from. `./bootstrap.sh`
puts it there as a link, `~/.local/bin/hammunition` pointing at the
checkout's `.venv/bin/hammunition`, made by `scripts/path-link.sh`: it prints
each change before making it, creates `~/.local/bin` (mode 0755) only when
it is absent, never edits a shell rc file, and never replaces a file, or a
link it did not create. The check's fix follows the same rule:

- **Not on `PATH` at all:** re-run `./bootstrap.sh`.
- **The bootstrap's link, pointing at a *different* checkout:** the one
  `ln -sfn` command that switches it, with both paths shell-quoted.
- **Something else at `~/.local/bin/hammunition`** (a pipx install, a
  wrapper): it is named with how to inspect it, and no command that would
  replace it is printed.
- **Another `hammunition` earlier on `PATH`:** that path is named; relinking
  `~/.local/bin` would not clear it, so it is not offered.

Paths are compared resolved, so in a git worktree whose `.venv` is a symlink
to another checkout's, the check names that checkout's venv. When
`~/.local/bin` is itself missing from `PATH`, the bootstrap prints the one
line to add to `~/.profile`. Remove the link with
`rm ~/.local/bin/hammunition`.

The **qmapshack** check (**D-061**) appears only when `qmapshack` is on the
`PATH` and `/usr/share/routino/translations.xml` is missing: QMapShack stops
at startup with "The specified translations XML file did not exist" until
the file is back. It is a warn, with `sudo apt-get install --reinstall
routino-common` as the fix.

The **geoclue** and **geoclue agent** checks (**D-069**) appear only where
GeoClue is installed (`/usr/libexec/geoclue`). *geoclue* is ok when both of
Hammunition's files are in place and `/run/hammunition-gps` exists with mode
2750, the operator as owner and GeoClue's group; information when neither
file is there (the fix is `hammunition hardware apply`); a warn when only one
is, and a warn naming what is wrong when the directory is missing or not as
made, with `sudo systemd-tmpfiles --create /etc/tmpfiles.d/hammunition-gps.conf`
as the fix. *geoclue agent* reads `busctl --user list`, which lists the
session bus's names and starts nothing, for Debian's demo agent
(`org.freedesktop.GeoClue2.DemoAgent`): ok when it is there, a warn when it
is not (without an agent GeoClue holds CoMaps' request and Qt gives up after
about 25 s; GNOME Shell is its own agent and this check does not see it),
and information when it was not asked (run as root, whose bus is not the
session's, or `busctl` did not answer).

The **launchers** check (issue #145) reads back every generated launcher in
`~/.local/bin` that runs `hammunition` itself (today QMapShack's
`qmapshack-offline` and `gps-tether`). A launcher runs the engine by its
absolute path, because the desktop menu starts it without `~/.local/bin` on
`PATH`. It is ok when every one names an engine that exists and is
executable, and a warn naming the launcher when:

- **it names a path that is gone** — the checkout moved or its `.venv` was
  rebuilt elsewhere. The fix is `./bootstrap.sh` in the checkout you use,
  which relinks `~/.local/bin/hammunition`, then `hammunition menus apply`,
  which rewrites the launcher with that engine's path. A launcher that runs
  the `~/.local/bin/hammunition` link is mended by the bootstrap alone.
- **it says bare `hammunition`** — written before this check existed, and
  what the menu reports as "hammunition: not found". The fix is
  `hammunition menus apply`.
- **it shadows a binary on the `PATH`** (issue #174) — any generated
  launcher, not only one that runs the engine, whose file name a program
  elsewhere on the `PATH` also has. `~/.local/bin` comes first, so a shell
  typing that name runs the launcher, which ignores its arguments: the line
  reads `<launcher> shadows <binary>`. Launchers generated before the
  catalog renamed them (`rigctl`, `hackrf_info`, `rtl_test`, yagiuda's
  `input` and thirteen more) are what it finds. The fix is
  `hammunition menus apply`, which removes the old file and writes the
  renamed launcher.

No launcher that runs the engine and none that shadows a binary, no line.

The **pcscd**, **FIDO2 token**, **PIV token**, **OpenSSH signing**, **FIDO2
access**, **PIV access**, **Bunker key** and **Bunker key strength** checks
are hardware signing readiness, read-only: `systemctl is-active pcscd`,
`ssh -V`, `fido2-token -L`/`-I`, and `opensc-tool --list-readers`/`--reader N
--name`, bounded to 5 seconds and 64 KiB of output and run as the operator
invoking `doctor`, never a PIN mint, a touch-signing request or a login
change. A present token is information, not every operator wants one; a
*warn* for `pcscd` inactive, OpenSSH older than 8.2 (needed for `-sk`
signing), or a device enumeration that answered permission-denied (named as
*access*, never a misleading "no token"). Run as root, the two *access*
checks say so is unmeasured rather than claiming a true-as-root result — a
udev rule grants the console user, not root, and `doctor` cannot measure on
the operator's behalf; run it as yourself, without `sudo`. **Bunker key**
lists every key already enrolled in the owner-aware mirror store
(`hammunition mirror status`), its fingerprint, algorithm and bit size; a
weak one (RSA 2048 or under) adds a **Bunker key strength** warning naming
the replacement. A mirror store that cannot be read is a warn naming
`hammunition mirror status`, not a crash. Nothing here signs a test message
or refreshes the mirror's trust or accepted serial.

The closing line counts each, and the exit code is non-zero only when
something is **blocking**. It is the natural first command after installing
from the checkout, and the one to paste when asking for help.

With `--json`, prints a `doctor` document
([json-interface.md](json-interface.md)): each check's name, severity,
detail, prose fix and, when that fix is one command, its `fix_argv` argument
list; advice that is not a command has `fix_argv: null`. `doctor` never runs a
fix. A local front end may offer a command to the operator, but must show it
and wait for explicit confirmation before running it. The text output shows a
single-command fix in a code span. The exit code is the text run's. It keeps
the count-only rule the text follows: no callsign, grid square or region name.

### `hammunition hardware list`

What is plugged in, what the catalog recognises, and what setup a device
needs — the permissions-and-udev half of the device role (**D-029**). Reads
`/sys/bus/usb/devices` directly (never `lsusb`, which may not be installed
and whose output is a screen-scrape), matches against the device catalog,
and reports three things: **recognised** devices attached (an ambiguous
identifier is flagged as a candidate, not a conclusion — **D-028**),
**unrecognised** attached devices (a prompt to contribute one), and whether
the udev rules and your access-group membership are already in place.
Detection drives nothing: it reports, and you decide (**D-020**).

### `hammunition hardware apply [--dry-run] [--yes] [--user NAME] [--no-gps-time] [--no-gps-resume] [--no-geoclue]`

Writes the whole catalog's udev rules to
`/etc/udev/rules.d/65-hammunition.rules`, reloads and triggers udev, adds
you to the device-access groups the catalog needs (`plugdev`, `dialout`),
and — since **D-056** — installs the two artefacts device power control
needs: a root-owned helper at `/usr/local/libexec/hammunition-devctl`
(`0755`) and a polkit action at
`/usr/share/polkit-1/actions/com.chiefgyk3d.hammunition.devctl.policy`
(`0644`), which is what lets `hardware park`/`wake` ask `pkexec` to run that
helper as root. See `docs/hardware/power-control.md` for what those two
files contain and what installing them means. The same helper and action
carry the `linger on|off` verb behind `station set --unattended` (**D-073
§5a**): it acts only on the calling account (the uid polkit reports, never an
argument) and records whether Hammunition turned linger on, so only linger
that is ours is ever turned off.

**The helper is moving to hammunition-tray (D-056, amended 2026-10-02).**
Where the helper already installed at that path answers `--version` with
contract 1's line (`hammunition-devctl contract N`), it is the tray's: `apply` then writes neither its wrapper nor an existing polkit action
(it still writes the action where none exists), says so in the plan, and
leaves the interpreter check out, because the engine's interpreter is not what
that helper runs. The engine's own copy is still written where nothing
answers; it is removed in a later release. What the tray's helper reads
instead of the engine's catalog is the pair of lists below. The two tray units
(`hammunition-tray`, `hammunition-tray-qt`) install the tray's helper
themselves from hammunition-tray v0.5.0's archive (a `devctl_helper` block, with
the interpreter, the files and the owner of any helper already present printed in
the plan; D-056 amended 2026-10-02, later), so `apply` hands over to it.

- **All the rules, not only attached devices' —** a udev rule is declarative
  and harmless for a device that is not present, so applying the whole set
  means a supported device works the moment you plug it in, not only if it
  happened to be attached when you ran this.
- **Idempotent.** A rules file, helper or policy file that already matches
  what would be written is a no-op, and a group you are already in is
  skipped. Re-running when nothing has changed reports "nothing to do".
- **Disclosed and verified.** Every privileged command is printed before it
  runs (`--dry-run` prints and stops); afterwards the rules file and the
  polkit artefacts are re-read against what was written and each group
  re-checked (**D-031**) — an exit code is not taken as proof. The rules file
  is refused a rule for any device whose identifier is ambiguous without a
  distinguishing product string, and each such omission is printed with why
  (`docs/reference/device-naming.md`).
- **Can refuse outright, or ask for a typed confirmation, before installing
  the helper or the policy.** Before writing either polkit artefact, `apply`
  checks whether the Python interpreter it would bake into the helper, and
  the `hammunition` package directory that helper imports, could be
  tampered with by anyone other than root. If either is writable by more
  than its own owner — world-writable, or group-writable by a group another
  account can hold — or could not even be `stat`'d, `apply` refuses
  outright — exit code `2` — because another account could then replace
  what root is about to run. If either is merely owned by one non-root
  account — the ordinary shape of a venv under `$HOME`, and this project's
  own documented install, including one group-writable only by the owner's
  own user-private group under a `0002` umask — `apply` is not refused, but it prints the path
  and asks `Proceed? [yes/no]:` once; `--yes` does not answer it (**D-021**,
  **D-056**), and anything but `yes` exits `3`.
- **Group membership applies at next login.** The command says so; log out
  and back in before expecting device access.
- **GPS time (D-058), where ntpsec is the time daemon.** Installs the two
  grants ntpd needs to read gpsd's time: a systemd drop-in,
  `/etc/systemd/system/ntpsec.service.d/hammunition-gps.conf`, with
  `AmbientCapabilities=CAP_IPC_OWNER`, and `capability ipc_owner,` added as a
  marked block to `/etc/apparmor.d/local/usr.sbin.ntpd`, with the profile
  reloaded. The plan says in so many words that CAP_IPC_OWNER bypasses
  permission checks on all System V IPC. It also writes
  `/etc/systemd/system/gpsd.service.d/hammunition-gps.conf`
  (`Environment=OPTIONS=-n`), without which gpsd publishes no time while no
  client is connected; it is the same file, with the same text, as the
  `chrony` unit writes, a different text there is refused, and gpsd reads it
  at the next boot. Inspect it with `systemctl cat gpsd`. It creates `/etc/ntpsec/ntp.d` and
  sets the first time mode (`auto`, or the mode already recorded) through the
  helper, which restarts ntpsec; when a mode is already applied and only the
  grants change, it restarts ntpsec instead. On a machine with no hardware
  clock (`/sys/class/rtc` empty) and no `fake-hwclock`, it installs
  `fake-hwclock`, disclosed as a stopgap. The plan prints every write that
  first mode causes, including the `ntp.conf` lines it moves and what turning
  off `tos minclock 4 minsane 3` costs, and refuses (exit `2`) before running
  anything when `ntp.conf` lacks the line an edit anchors to. Each GPS time
  step is logged (`time_grants`) and read back afterwards. Nothing of this
  happens without gpsd installed (there is no GPS time to read), and
  `--no-gps-time` leaves ntpsec, its grants and `fake-hwclock` alone. See
  `docs/guides/gps-time.md`.
- **GeoClue reads the GPS tether (D-069), where GeoClue is installed.**
  Writes `/etc/geoclue/conf.d/90-hammunition-gps.conf` (`[network-nmea]`,
  `enable=true`, `nmea-socket=/run/hammunition-gps/nmea.sock`, a drop-in over
  the untouched `geoclue.conf`) and `/etc/tmpfiles.d/hammunition-gps.conf`
  (`d /run/hammunition-gps 2750 <operator> geoclue -`), runs
  `systemd-tmpfiles --create` on the second so the directory exists now
  (systemd makes it at every boot), and `systemctl try-restart geoclue` so a
  running GeoClue reads the drop-in. The plan prints both files, the
  directory, the four facts the operator should know before agreeing (that
  GeoClue reads its configuration only at start; that any native app of a
  user with an agent then gets the fix while the tether runs; that stock
  GeoClue's own beacondb and GeoIP lookups are unchanged; that Qt caches the
  last fix), how to inspect it and how to reverse it. A file at either path
  that does not start with Hammunition's header, or anything but a directory
  at `/run/hammunition-gps`, refuses the run (exit `2`) before anything
  runs. Each step is logged (`geoclue_files`), and afterwards both files are
  read back and the directory's mode, owner and group checked. Nothing of
  this happens where GeoClue (`/usr/libexec/geoclue` and its `geoclue`
  group) is not installed; the plan says so. `--no-geoclue` leaves GeoClue
  alone. See `docs/guides/offline-navigation.md`, section 17.
- **Installs the GPS receiver's resume step (issue #177)** where gpsd is
  installed: `/usr/local/libexec/hammunition-gps-resume` (`0755`) and
  `/etc/systemd/system/hammunition-gps-resume.service`, a oneshot after and
  wanted by the four sleep targets, enabled and not started. After each
  resume it runs `gpsdctl remove` and `add` for each `/dev/gpsN`, and
  `systemctl try-restart gpsd.service` if gpsd then reports no device; with
  no `/dev/gpsN` it does nothing, and it never parks or wakes anything. The
  plan prints both files whole, with how to inspect them
  (`systemctl status hammunition-gps-resume`,
  `journalctl -u hammunition-gps-resume`) and reverse them. Each step is
  logged (`gps_resume`), and both files and the four `.wants` links are read
  back afterwards. A file at either path without Hammunition's header refuses
  the plan (exit `2`). `--no-gps-resume` leaves the step out; `--no-gps-time`
  does not. See `docs/hardware/power-control.md`, "After suspend".
- **Exports the helper's two lists (D-056, amended 2026-10-02):**
  `/etc/hammunition/devctl-devices.yaml` (every catalogued class or device
  that carries `power_control`: its name, summary, method, quiet verbs and
  each confirmed identifier as quoted `vendor`/`product` strings, plus
  `product_string` for an ambiguous one, which is all `state` needs to recognise
  it on the bus without the catalog; hammunition-tray's contract 1) and `/etc/hammunition/devctl-services.yaml`
  (`gpsd` is `gpsd.socket`, `time` is `ntpsec.service` or `chrony.service`
  by whichever daemon the machine has, `gps-resume` is
  `hammunition-gps-resume.service`). Both are root-owned `0644`, printed whole
  in the plan, logged (`devctl_export`) and read back afterwards, and a file at
  either path without Hammunition's header refuses the run (exit `2`). Shapes:
  `docs/reference/devctl-lists.md`. A user-scope service's row is written by
  the install of the unit that runs it, not here.

### `hammunition hardware unapply [--dry-run] [--yes] [--user NAME]`

Removes the power-control helper and its polkit action — the two files
`hardware apply` installs at `/usr/local/libexec/hammunition-devctl` and
`/usr/share/polkit-1/actions/com.chiefgyk3d.hammunition.devctl.policy` — and,
if present, `/etc/udev/rules.d/66-hammunition-kept.rules`, the kept-off rules
file `park` writes to by default (**D-056**, amended 2026-09-28). Removing it
reloads udev, so every device it was holding parked wakes from the next boot
on. GPS time, the GPS resume step and GeoClue's tether socket files are
taken back too (below); nothing else is touched.

- **Not part of `uninstall`.** `uninstall` resolves the names it is given
  against the package and profile catalogs; there is no unit named
  `hardware` to give it. This is its own verb for that reason.
- **Removes only what the transaction log says this engine installed for the
  given operator**, never a path merely expected to exist and never anything
  a log entry names besides those two exact paths — a `path` the log
  contains that is not one of them is reported and skipped, not removed.
- **The udev rules file is never touched.** It is declarative, harmless for a
  device that is not attached, and removing it would take away device access
  still in use — power control is the reversible half of this feature,
  device permissions are not.
- **Disclosed and verified**, the same as `apply`: every command is printed
  before it runs (`--dry-run` prints and stops), and `rm` exiting 0 is not
  trusted — each path is re-checked for absence afterwards (**D-031**).
- **Takes GPS time back exactly (D-058)**, by content rather than by the
  log: `/etc/ntpsec/ntp.conf`'s marked lines go back byte for byte as they
  were before Hammunition edited them (on an `ntp.conf` nobody else edited,
  the package's own, which `dpkg --verify ntpsec` should confirm; not yet
  measured on the bench);
  `/etc/ntpsec/ntp.d/hammunition-gps.conf`, `/etc/hammunition/time.yaml` and
  the ntpsec drop-in are removed only when they start with the header
  Hammunition writes; gpsd's `-n` drop-in only when it holds exactly
  Hammunition's text and `/etc/chrony/conf.d/hammunition-gps.conf` is
  absent, since the `chrony` unit shares it; only Hammunition's block leaves
  `/etc/apparmor.d/local/usr.sbin.ntpd`, the rest of that file stays; then
  systemd and AppArmor are reloaded and ntpsec restarted. `fake-hwclock`, if
  it was installed, stays; `sudo apt remove fake-hwclock` removes it.
- **Takes GeoClue's tether socket back (D-069)**, by content too: each of
  `/etc/geoclue/conf.d/90-hammunition-gps.conf` and
  `/etc/tmpfiles.d/hammunition-gps.conf` only when it starts with
  Hammunition's header, `/run/hammunition-gps/nmea.sock` only when it is a
  socket, then `rmdir /run/hammunition-gps` when the tmpfiles line was ours
  (it fails, loudly, if anything else is in it) and, while GeoClue is
  installed, `systemctl try-restart geoclue`. Stop the tether first; TCP
  10110 keeps serving until you do.
- **Takes the GPS resume step back (issue #177)**, by content:
  `systemctl disable hammunition-gps-resume.service`, then the unit and
  `/usr/local/libexec/hammunition-gps-resume` are removed, each only when it
  starts with the header Hammunition writes, and systemd is reloaded. The
  files and the four `.wants` links are re-checked for absence afterwards.
- **Takes the helper's two lists back (D-056, amended 2026-10-02)**, by
  content: each of `/etc/hammunition/devctl-devices.yaml` and
  `/etc/hammunition/devctl-services.yaml` only when it starts with the header
  Hammunition writes. `/etc/hammunition` stays: `time.yaml` lives there.
- **Leaves a helper that is now the tray's alone.** Where the installed helper
  answers `--version`, the log's older record of the engine's own copy is not
  acted on: the helper and its polkit action belong to hammunition-tray, and
  removing them is its own uninstall's job (not yet measured). A polkit action
  this engine wrote because none existed stays until removed by hand, and the
  run says which logged paths it left.

Exit codes: `0` for a removal that verified absent, nothing recorded to
remove, every recorded artefact already gone, a `--dry-run`, or declining the
confirmation prompt; `1` if the operator could not be determined, a removal
command failed, or a path is still present after the run; `2` when
`ntp.conf`'s marked lines were edited by hand, refused before anything runs.

### `hammunition hardware park NAME [--until-reboot] [--dry-run]`

Detaches a catalogued, attached device and lets its port suspend — writes `0`
to its sysfs `authorized` file, the same effect as unplugging it. `NAME` is
the catalog name (`gps-receiver`), or `NAME@ADDRESS` when two of the same
kind are attached and the plain name would be a guess. Only a device whose
catalog entry carries a `power_control` block is ever offered (**D-056**);
see `docs/hardware/power-control.md` for what parking does and does not do,
and which devices carry that block today.

**Kept parked by default (D-056, amended 2026-09-28).** Alongside the sysfs
write, `park` adds two lines to `/etc/udev/rules.d/66-hammunition-kept.rules`
— a `# kept: NAME` comment, then a rule naming the device's port and
vendor/product pair; udev re-applies `authorized=0` when the device is added
— at boot, and on a replug into the same port — with nothing of
Hammunition's needing to run. A suspend/resume is not claimed: a resume is
normally not a udev `add` event. That mechanism is built; whether the device
actually comes back parked across a real reboot has not yet been measured on
hardware — see "Kept off across reboots" in `docs/hardware/power-control.md`.
`--until-reboot` adds no rule and removes any kept entry an earlier `park`
wrote for the device: it parks now and a reboot wakes it, the pre-amendment
behaviour. `wake` (below) removes the kept entry.

The privileged write goes through one polkit action,
`com.chiefgyk3d.hammunition.devctl`, `hardware apply` installs the helper it
authorises. `--dry-run` prints every write it would make, whether a kept
entry is added, and the `pkexec` call itself, then stops.

Exit codes: `0` parked and verified (or a `--dry-run`); `1` a write did not
verify, or the command otherwise failed to run; `2` unplannable — the helper
is not installed, `pkexec` is not on `PATH`, or `NAME` does not resolve to a
parkable attached device; `3` the authentication prompt was declined or
denied and nothing was changed.

### `hammunition hardware wake NAME [--dry-run]`

The reverse of `park`: writes `1` back to the device's `authorized` file so
the kernel re-enumerates it, and removes the device's kept entry from
`66-hammunition-kept.rules`, if it has one, so a later reboot does not park
it again. Same `NAME` syntax, same `--dry-run`, same exit codes as `park`.
`NAME@ADDRESS` also resolves a kept entry whose device is **not** currently
attached, so a stale entry for something already unplugged can be cleared
without plugging it back in.

### `hammunition hardware state`

Lists every catalogued device that is both attached now and parkable, and
whether each one is parked — read fresh from `/sys/bus/usb/devices` on every
call, never cached — plus any device kept parked (**D-056**) whose entry
names a port nothing answers on right now. Needs no privilege: reading
sysfs is unprivileged, only writing to it is. Always exits `0`; an empty
report is not a failure.

The table shown by the CLI adds a `kept` column next to `state`
(`parked`/`awake`), and lists devices kept-but-absent separately under "Kept
parked, not attached", with the `wake NAME@ADDRESS` command that clears each
one. The JSON the root helper prints (`hammunition-devctl state`, what the
tray applet polls) gives one object per row with `"kept": bool` alongside
`"parked"`; a kept device with nothing attached gets `"attached": false` and
`"parked": null`, since there is no sysfs node to read a live answer from.

With `--json`, prints a `hardware` document
([json-interface.md](json-interface.md)): one object per row with the same
keys the helper prints, plus any error reading the kept-off rules.

### `hammunition hardware gps-resume-report [--data-window SECONDS] [--json]`

Read-only report on the GPS resume step (issue #177), for an operator who is not
in `systemd-journal`. Prints: each of the three installed files (the script, the
unit and its tmpfiles line) compared byte for byte with what this engine would
write now (`current`, `differs`, `wrong-mode`, `absent`, `unreadable`) and a
finding saying to re-run `hammunition hardware apply` when one is not; the
unit's `systemctl show` state (LoadState, ActiveState, Result, ExecMainStatus,
ActiveEnterTimestamp); gpsd's `?DEVICES;` answer and a `?WATCH` data check of
each `/dev/gpsN` for `--data-window` seconds (default 10), by the resume
script's own functions; the USB device the step would cycle (`idVendor`,
`idProduct`, `authorized`) found the way the step finds it; and the last run's
lines from `/run/hammunition/gps-resume.log`. It never writes, never keys a
receiver and never changes a power state. Always exits `0`; the findings say what
is wrong. With `--json`, prints a `gps-resume-report` document
([json-interface.md](json-interface.md)).

### `hammunition time`

What the clock follows now (the network, the GPS, or nothing, with how long it
has been in holdover), the time mode and whether it was ever set, whether a GPS
receiver is attached and awake, and whether ntpd can read it. Reads only:
`ntpq -pn` and `ntpq -c rv` answer any local user, so there is no prompt. On a
target whose time daemon is not ntpsec it says so and names the gap (D-058).

### `hammunition time measure [--minutes N] [--interval SECONDS] [--pps] [--pps-seconds N]`

Read-only GPS-takeover measurement (issue #310). Samples `ntpq -pn` every
`--interval` seconds (30) for `--minutes` (10) and prints, per sample, what
ntpd follows, the GPS refclock's reach and offset and the best network peer's;
then says whether and when the GPS became the system peer and over what offset
range. With the network up ntpd rejects the GPS by design, so measure with the
network off. `--pps` then runs `ppstest /dev/pps0` for `--pps-seconds` (60) and
says whether pulses arrived, naming `/sys/class/pps`; if `ppstest` is not
installed (package `pps-tools`) or the device is root-only, it says so. Exit `2`
when ntpsec is not installed, `1` when `ntpq` never answered. It has no `--json`
form and leaves no run log.

### `hammunition time mode MODE [--dry-run]`

`MODE` is one of `auto` (the default), `prefer-gps`, `ntp-only`, `gps-only`.
Prints every write before it happens: `/etc/hammunition/time.yaml`, the whole of
`/etc/ntpsec/ntp.d/hammunition-gps.conf`, the marked lines of
`/etc/ntpsec/ntp.conf` that move, and the `systemctl restart ntpsec` that
follows; then runs `pkexec /usr/local/libexec/hammunition-devctl time mode MODE`.
A parked receiver never feeds the clock whatever the mode says (nothing is
rewritten; ntpd drops it by its own reachability rules, inferred and not yet
watched on the bench). Refused with
exit 2 when ntpsec is not installed, when the helper is not, or when `ntp.conf`
no longer has the line an edit anchors to. If ntpsec will not restart on the new
files, the helper puts the old ones back and starts ntpsec on them. Exit 3 when
the authentication prompt is dismissed.

### `hammunition services [start|stop|enable|disable NAME] [--dry-run] [--json]`

The services the privileged helper may control, and what each is doing, from
the helper's own `services state` document (**D-056**, amended 2026-10-02): the
GPS daemon's socket (`gpsd`), the clock (`time`, ntpsec or chrony), the GPS
resume step (`gps-resume`) and any user service a catalog unit installed
(`gps-tether`, `rig`, `rns`). A service whose unit is not installed is listed as
`not installed`, never left out. Reads only and asks for no password: it runs
the installed helper unprivileged, one argv. **The engine never runs
`systemctl` itself**, and never passes a unit: it passes a name from that list,
and the helper looks the unit up in `/etc/hammunition/devctl-services.yaml`
(system scope) or `~/.config/hammunition/devctl-services.yaml` (user scope).

`start NAME`, `stop NAME`, `enable NAME` and `disable NAME` change one. A
system service goes through `pkexec` and the one polkit action, as `hardware
park` does; a user service runs as you and never asks for a password. The
command prints the call before it runs (`--dry-run` prints and stops), is a
no-op when the service is already where you asked, refuses a name the helper
does not list and a unit that is not installed (exit `2`, before any prompt),
and reads the result back from the helper afterwards (**D-031**): a start that
ends `failed`, a stop that leaves it running, or an enable that does not read
`enabled` is reported unverified (exit `1`). A start that ends `inactive`
is only a note, because a one-shot unit runs and exits. Exit `3`: the
authentication prompt was dismissed.

With `--json` (on the list only: the four verbs change the machine and have no
JSON form, **D-059**), prints a `services` document
([json-interface.md](json-interface.md)): the helper's document, checked and
re-rendered, so a front end reads one shape from the engine or from the
helper. A helper that predates the `services` verb, or is not installed, is
refused by name: update or install hammunition-tray. Not yet measured on the
bench: the verbs against the tray's helper (hammunition-tray 0.5.0, released
and installed by the tray units), which has been run against fakes only, never
against a real `systemctl`.

### `hammunition self-update [--dry-run] [--yes] [--release]`

Updates the engine's own checkout (**#303**, **#311**): `git fetch origin`,
`git merge --ff-only origin/main`, then `./bootstrap.sh`, each step printed
before it runs. It finds the checkout from where the running package was
imported, and only when that is a git work tree holding `bootstrap.sh`; a
packaged install is told there is nothing to update. It runs as the account
that owns the checkout, never as root, and never touches apt, installed units
or the station. Its run is teed to a run log (**D-077**), and it prints the
version before and after.

`--dry-run` runs the fetch (it changes no file in the tree), then prints the
three steps and `git log --oneline HEAD..origin/main`, the commits that would
arrive, and stops. With no new commits it says so: bootstrap still runs on a
real run, because re-running the editable install is exactly what repairs a venv
whose installed version lags the tree. `--yes` answers the ordinary
confirmation; this is not a consent gate. `--release` fast-forwards to the
newest `v*` tag reachable from `origin/main` instead, from any branch. `--json`
prints a `self-update` document with `--dry-run` only
([json-interface.md](json-interface.md)).

It refuses, with a sentence and exit `2`, and changes nothing: a tree with
uncommitted changes, a detached HEAD or a branch other than `main` (unless
`--release`), a history that is not a fast-forward (it never merges and never
resets), a fetch that fails, and no reachable release tag under `--release`.
Exit `3` is a declined confirmation; exit `1` is a step that failed or a venv
whose installed version still differs from the tree after bootstrap.

`hammunition --version` prints the checkout's `pyproject.toml` version when run
from a checkout, and both when the venv lags it:
`0.20.0 (checkout), 0.19.0 (installed); run `hammunition self-update``. Every
`--json` document's `engine` field is the checkout's version by the same rule,
and `doctor` carries an `engine version` check whose fix is
`["hammunition", "self-update"]`. The console's Home offers it with `U`.

### `hammunition secrets status [--user NAME] [--json]`

Where each secret the engine knows would come from, and never what it is (**D-081**, issue #321). One entry per secret in the
engine's registry (`hammunition.secrets.REGISTRY`; today `REPEATERBOOK`, RepeaterBook's API token for
`maps repeaters fetch-repeaterbook`): its purpose, whether a source would answer now, which (`environment`, `doppler` or
`none`), the unit and command it goes with, where to get one, and the exact ways to provide it: `export REPEATERBOOK=...` for
the shell, or `hammunition station set --doppler-project PROJECT --doppler-config CONFIG`. Also whether the station names a
Doppler project and config, and whether `doppler` is on `PATH`.

It prints **no value, no prefix of one and no length**, and a test sets a fake token and asserts its absence from the text and
the JSON. It does not run `doppler`: `doppler` as the source means the station names a project and config and the CLI is
installed, and the command that needs the secret asks. `--json` prints a `secrets` document
([json-interface.md](json-interface.md#secrets)); the console's Secrets screen reads it. Read-only; no run log.

### `hammunition logs [--last] [--path] [--user NAME] [--json]`

The log each run that changed something left behind (**D-077**), newest first:
when it started, which command, how large the file is and how the run ended
(`ok`, `failed`, `refused`, `not confirmed`, `running` while a live process
still holds the file, `incomplete` for a run that was killed before it could
write its last line). Reads only. `--last` prints the newest in full;
`--path` prints its path, for `tail -f` while the run is going. With `--json`
(the list only) prints a `logs` document
([json-interface.md](json-interface.md)). With no logs yet, the list says so
and `--last` exits `1`. The files, their format and their rotation are
`docs/reference/run-logs.md`.

### `hammunition transactions [--last N] [--json]`

The transaction history, oldest first across every rotated archive and the live
file (**D-077**). Each row gives the begin and end times, command, units,
deferred names, result (`ok`, `failed`, `aborted` or `in-progress`) and the
associated run-log path when one was recorded. A missing end is `in-progress`
only while its run log is still held open; otherwise it is `aborted`. Older
transactions without a recorded run-log path show `—` in text and `null` in
JSON. `--last N` limits the rows to the newest N while keeping them in
chronological order. `--json` prints the `transactions` document
([json-interface.md](json-interface.md)).

### `hammunition console [--help] [--version]`

The full-screen terminal front end (#302, **D-059** amended 2026-10-04): the same release and
version as the engine, started with one subcommand. It reads the engine's `--json` documents and
runs the engine's own commands, for a write inside a terminal pane where **you** type any consent;
it never passes the assume-yes flag and never runs as root. Walkthrough:
[The console](../getting-started/console.md); keys, screens and what it reads:
[the console reference](../console/index.md).

- `--help` prints its keys and exit codes; `--version` prints the engine's version (there is no second one).
- It refuses to start, exit 2, with no terminal on stdin and stdout, with `TERM=dumb` or unset,
  as root, or with an argument it does not know.
- **urwid** is its one dependency and is optional for the engine: when it cannot be imported,
  `console` prints one line naming `sudo apt install python3-urwid` (outside a virtualenv on the
  Debian family) or `pip install 'hammunition[console]'` (anywhere else, including the bootstrap
  virtualenv, which `./bootstrap.sh` already fills), and exits 2.
- It has **no `--json` form**: `hammunition console --json` is refused like any verb with no document.
- It launches the engine as `<its own interpreter> -m hammunition`, never a `hammunition` found on `PATH`.

## mirror

A signed Bunker catalogue (**D-085**, `docs/guides/lan-mirror.md`,
`docs/reference/bunker-catalogue.md`): enrolling it is a separate step from
`station set --mirror`, below, which only gives the D-070 download route and
enrols no trust on its own.

### `hammunition mirror enrol URL [--enrolment-id ID]`

Fetch the Bunker's catalogue and show each signer’s fingerprint, measured
algorithm and size, hardware claim, `no_touch_required` metadata and any weak-key
warning. Type the chosen fingerprints, comma-separated, then confirm each
fingerprint at the terminal. A non-sk key’s hardware claim requires a separate
hardware-origin affirmation and typed fingerprint consent, recorded in the
transaction log. `no_touch_required` is display only.

Enrolment verifies a signature before storing trust and the station URL. Re-enrolling
the same URL and Bunker name retains the accepted serial and only the keys explicitly
chosen this time. A lower serial refuses. A changed URL or name is new enrolment.
There is no `--yes` and no JSON form; `--json` refuses with an `error` document.
A group enrolment id is a sharing filter sent in clear over HTTP, not identity proof.

### `hammunition mirror status [--json]`

Show stored Bunker trust, each key’s strength, hardware assertion and
`no_touch_required`, accepted serial and age of the last verified catalogue.
Nothing is fetched. Missing trust reports no Bunker enrolled. A station URL that
differs from enrolled trust requires re-enrolment.

`--json` prints a `mirror` document ([JSON interface](json-interface.md)).

### `hammunition mirror accept-older`

Verify the current catalogue with enrolled keys and the station hardware policy,
show its serial beside the accepted serial, then ask for typed `yes` at a terminal.
Use this only after restoring the Bunker from a trusted backup. An invalid signature
still refuses. There is no `--yes` and no JSON form; `--json` refuses with an
`error` document.

### `hammunition station show`

The values only you can supply — callsign, grid square, packet node alias,
the regions to carry offline maps for, the LAN mirror to take their data
from, and which elevation QMapShack draws from. Some
manifests write configuration files templated with them: `linbpq` needs a node
callsign, AX.25 needs one in `/etc/ax25/axports`, Direwolf needs one in its
own configuration.

```
hammunition station set --callsign N0TST --grid-square FN31pr
hammunition station show
```

| Flag | Effect |
|---|---|
| `--callsign CALL` | Station callsign |
| `--grid-square LOC` | Maidenhead locator |
| `--node-alias NAME` | Short packet node alias |
| `--map-regions R[,R…]` | Geofabrik region paths for offline maps, e.g. `north-america/us/vermont,north-america/us/new-hampshire`. Replaces the whole list. Checked for shape only (lowercase words joined by `/`); whether Geofabrik has the region is checked at plan time (**D-057**) |
| `--map-freshness MODE` | `yearly` (the default when unset), `monthly` or `latest`: which dated file each region resolves to, and so how it can be verified |
| `--reference-books ID[,ID…]` | Kiwix books for `kiwix-library`, by id (`hammunition reference books` lists them). Replaces the whole list; an id the catalog's book list does not name is refused when you type it, and an empty list is refused (uninstall `kiwix-library` to remove the books) (**D-066**) |
| `--mirror URL` | A LAN mirror of the data artifacts, e.g. `http://bunker.lan:8080/` (**D-070**). Each data download (a `data` unit's files, a map region, a terrain tile, a CoMaps map, a reference book) asks `<URL>/<unit>/<name>` first and the publisher on any failure, the same digest checked either way. `http` or `https` with a host, or `file:///absolute/path` with no host; no user, password, query or fragment. A LAN address, never one reachable from the internet. **Does not enrol signing keys or authorize `--offline` planning** — that is `hammunition mirror enrol URL` (**D-085**); `docs/guides/lan-mirror.md` |
| `--clear-mirror` | Remove the saved mirror URL and all enrolled keys, enrolment id and accepted serial; preserve hardware policy |
| `--mirror-require-hardware-key` / `--no-mirror-require-hardware-key` | Require an enrolled hardware signer for Bunker catalogues, or turn that policy off (default). Hardware means sk type or operator-affirmed origin. `station set --json` reports the policy in its `station-set` document; `station show --json` includes it in the `station` document |
| `--doppler-project PROJECT`, `--doppler-config CONFIG` | Where a keyed download's key is read from when its environment variable is not set (**D-081**): the two names of a Doppler project and config, given together, never a token. Each is letters, digits, `.`, `_` or `-`, starting with a letter or digit. One without the other, or either with `--clear-doppler`, is refused (exit 2) |
| `--clear-doppler` | Remove both Doppler names |
| `--dem-source SOURCE` | `copernicus` (the default when unset) or `3dep`: the elevation QMapShack's hillshade, slope and contours are drawn from (**D-068**, amended 2026-10-01). `3dep` makes `dem-3dep` fetch USGS 3DEP 1/3-arc-second bare-earth tiles for the US regions, about ten times Copernicus's size, and `dem-qmapshack` redraw from them; Copernicus stays installed for BRouter and for regions outside the US. Setting it back to `copernicus` removes the 3DEP tiles and redraws from Copernicus on the next install. `station show` prints it |
| `--topo-radius-km N` | How far from your grid square's centre US Topo sheets, FSTopo sheets and 3DEP tiles are selected (**D-068**, amended 2026-10-02, issue #232): 100 when unset, `0` for none, at most 20000. The grid square's centre is derived, never stored. Copernicus terrain is not bounded. `station show` prints it |
| `--topo-regions R[,R…]` | Narrow the topographic selection to these map regions, which must be a subset of `--map-regions` (refused otherwise, naming them). With no `--topo-radius-km` they are taken whole; with one, the circle is cut to them. `station show` prints a count, never the names |
| `--active-areas CODE\|REGION …` | The areas drawn and registered (**D-082**): US state codes (`OH`) and map region names, several at once. Not checked against what is loaded: one that is not is accepted with a note. `station show` prints a count, never the names. `maps activate` sets this and re-registers the programs |
| `--clear-active-areas` | Remove `--active-areas`: everything loaded is active again (the default) |
| `--clear-topo-regions` | Remove `--topo-regions`. Narrowing `--map-regions` alone drops any `--topo-regions` entry no longer among them, and says so |
| `--topo-all`, `--no-topo-all` | Select every sheet of every region, as before the bound. The install then prints the count, download and disk in one sentence and asks you to type `yes`, which `--yes` does not answer; so does any selection over 10 GB (set `HAMMUNITION_ACCEPT_TOPO_SIZE` to the sheet count to affirm it in a script). Exit 3 when it is not given |
| `--rig DEVICE\|hamlib:MODEL` | The station's radio (**D-073**): a catalog device id (`yaesu-ft-991a`), or `hamlib:<model>` for one with no manifest. Checked against the catalog and this machine's `rigctl -l` when you set it |
| `--rig-device PATH` | The serial port the rig (or its interface) is reached on; an absolute `/dev/` path, a `/dev/serial/by-id/` one for stability. Refused if it carries `..`, whitespace or a shell character |
| `--rig-baud RATE` | The CAT serial speed. For a catalogued CAT rig it must be inside the backend's range (named on refusal); mandatory for `hamlib:<model>`; refused for a PTT-only rig |
| `--rig-ptt-line rts\|dtr\|vox` | For a radio with no CAT: which control line keys it, or `vox`. Required for a PTT-only rig, refused for a CAT rig |
| `--rig-owner rigctld\|flrig` | Who holds the port: `rigctld` (the shared daemon, the default when unset) or `flrig`. `flrig` with a PTT-only rig is refused |
| `--clear-rig` | Remove `rig`, `rig_device`, `rig_baud`, `rig_ptt_line` and `rig_owner` |
| `--unattended` / `--no-unattended` | Keep the operator's user services running with nobody logged in, through `loginctl enable-linger` behind the power-control helper (**D-073 §5a**). The plan lists what linger keeps alive before the prompt. `--no-unattended` disables it, but only if Hammunition turned it on |

A region list says where the operator lives or travels, so `station show`
and `station set` print how many regions are set, never their names; the
install plan is the one place the text prints them, and `station show
--json` carries them for a local front end. `docs/guides/offline-navigation.md`
is the operator's walk-through.

Saved to `$XDG_CONFIG_HOME/hammunition/station.yml`, mode 0600, resolved
owner-aware so that running under `sudo` still writes to the invoking user's
home rather than root's.

**A value you have not supplied does not block an install.** The package is
installed and the file that needed the value is reported under *Will NOT
happen*, with the command that would let it be written. That is deliberate
(**D-035**): a nineteen-package profile refusing entirely because one file
needed a callsign got an operator nowhere.

**Nothing is invented.** There is no default callsign and no placeholder,
because a configuration file written with a made-up callsign would transmit
it. An interactive run offers to prompt for what the request actually needs;
`--yes`, a pipe, or a value that is already known all skip the question.

`station show` prints the mirror URL in full: it is an address on your own
network, and `--no-mirror` or `--clear-mirror` are the way to stop using it.

`station show --json` prints a `station` document
([json-interface.md](json-interface.md)) carrying the values themselves:
callsign, grid square, node alias, and every map region by name, because a
local front end needs them to fill in a form. It is for local programs, not
for pasting into an issue, a forum or a chat: a callsign resolves to a name
and a licence address, and a grid square or a region says where the station
is.

### `hammunition station set`

`station set --json` prints a `station-set` document
([json-interface.md](json-interface.md)) with the values saved,
the given values left unchanged, and one refusal for each rejected flag. Its
exit code is `2` if any flag was refused; when that happens, none of the
requested values are saved. It carries station values too, so it is for local
programs, not for pasting into an issue, a forum or a chat.

## Launchers and menu entries

A manifest may declare `launchers` — programs that need a working directory,
a service-endpoint argument, or that simply have no `.desktop` of their own
(Java jars, run-in-place trees; 14 units measured). For each one the run
generates two per-user artifacts, unprivileged, printed like every other
step: a wrapper script in `~/.local/bin` with `{endpoint:NAME}` substituted
from the manifest's `service_endpoints` (the repointable-backend rule — a
dead upstream is fixed by editing the catalog, not launchers), and a desktop
entry in `~/.local/share/applications` whose `Categories=` are mapped from
the manifest's own category tags, `HamRadio` first (**D-036**). Entries
carry `X-Hammunition-Package` so later tooling can find its own work.

**A launcher never takes the name of a program on the `PATH`** (issue
#174). The wrapper's file name is what a shell finds, and `~/.local/bin`
comes before `/usr/bin` on Debian's `PATH`, so a launcher called `rigctl`
*is* `rigctl` to every terminal: it ignored `rigctl -l` and opened the
dummy-rig shell. A launcher is named for what it does, with the tool's
name first so tab completion finds it beside the tool (`rigctl-dummy`,
`hackrf_info-check`, `yagiuda-input`), and the menu shows its `title`
(**D-054**). Three refusals hold the line:

- **The schema** refuses a launcher named like the bare command its `exec`
  line runs (`exec gpa` named `gpa` ran itself until killed) or like a
  binary the manifest's `binaries` install. A command given by path, such
  as a venv's `{venv}/bin/pygpsclient`, is not on the `PATH` and may share
  the name.
- **The generator** refuses, with an error naming the program it would
  shadow and the fix (rename the launcher in the manifest, keep its
  title), a name `shutil.which` finds on the `PATH` with `~/.local/bin`
  taken out and the standard system directories added, or one in the file
  list (`dpkg-query -L`) of an apt package the manifest names. It asks at
  plan time and again when it writes, because a fresh install's plan runs
  before apt has unpacked the package. A file another hammunition launcher
  directory holds is a wrapper, not a program, and does not count.
- **`hammunition menus apply`** removes a generated launcher that already
  shadows a program (one written before the rename) and writes the renamed
  one; `hammunition doctor` names it until then.

A launcher whose command starts with `hammunition` runs the engine **by
absolute path** (issue #145): a desktop menu starts its entries without
`~/.local/bin` on `PATH` (Plasma runs each as a systemd user service), and
a bare `hammunition` there exits 127, "not found". The path is
`~/.local/bin/hammunition` when that is bootstrap's link and runs the engine
doing the install, so a checkout that moves is mended by re-running
`./bootstrap.sh`; otherwise it is the running engine's own
`.venv/bin/hammunition`. The plan prints which (`calls <path>`), and
`hammunition doctor` names a launcher whose engine is gone. Uninstall is
unchanged: it removes a wrapper that carries the generated marker, which
the new wrapper keeps. The
curated per-DE submenu layer (Xfce `.menu`, GNOME app-folders, COSMIC) is
D-036's next, measured step.

## How a run is ordered

Resolution is a distinct phase that finishes before anything is executed
(**D-016**). In order:

1. **Detect the target** from `/etc/os-release`. A non-Debian-family system is
   refused here; there is no shim that makes it appear to work.
2. **Expand** the requested names — profiles into their packages, and any
   `depends` that names another manifest.
3. **Order** by `after`, which is sequencing rather than dependency. A cycle is
   reported; it does not hang.
4. **Resolve** each manifest against `(distro, version, arch)`. No matching
   install block means this target is genuinely unsupported for that package.
5. **Check what this engine can actually do** — see below.
6. **Ask apt once**, about every distro package the whole transaction needs —
   the manifests' own packages, their `depends`, and the `build_depends` of any
   source build, together. This is how a stale build dependency is caught before
   a compiler is installed rather than after `./configure` fails: glfer's
   `build_depends` name `fftw2` and `libgtk2.0-dev`, two of the four AHRL
   dependency lines **D-016** records as suspected-stale, and nothing in AHRL
   ever asked apt whether they still exist. Then apt is asked a second
   question, once, whenever anything is outstanding: whether the whole set
   installs *together*, and what the apt step would pull in (`apt-get install
   --simulate`, unprivileged, no lock) — because `apt-cache policy` knows
   that `jtdx` exists, not that installing it brings `wsjtx-data`, and knows
   that `libcurl4-openssl-dev` exists, not that this machine's `libcurl4t64`
   is from backports at a version it cannot depend on. If apt refuses because
   an installed package would be downgraded, and every such package came from
   one release, the question is asked a third time with `--target-release`
   naming it; a yes is carried into the apt command and the plan lists what
   that release supplies (**D-038**). Any other refusal is the plan's. The
   same simulate is read for what apt would **remove** (`Remv` lines): a
   package that `Breaks:` an installed one is "resolved" by apt removing
   the installed one, and the plan refuses that by name rather than let
   the apt step do it unseen (**D-022**, issue #42). A unit whose manifest
   sets `install_recommends: false` makes this two questions rather than
   one: its packages are a second set, asked with
   `--no-install-recommends` and installed by a second `apt-get install`
   carrying the same flag, so the `Remv` lines the plan refuses on are the
   ones the command that runs would produce (**D-052**). Both commands
   carry `--no-remove`, both appear under *Commands*, and a measured
   `--target-release` governs both.
7. **Defer what the target does not offer** — but only for a member that
   reached the plan through a *profile*, and only for one of three reasons
   that are facts about the target: no install block matches this
   distro/version/arch, apt on this release has no candidate for the unit's
   *own* packages, or the distribution's Node is below the manifest's floor
   — and for one fact about the *machine*: the running kernel lacks a
   subsystem the manifest's `requires_kernel` names (**D-041**; Linux 7.1
   removed AX.25, and Kali on 7.1.5 defers eight `packet` members) — and
   for another: the unit's `desktops` names none of the desktops the
   session files under `/usr/share/xsessions` and
   `/usr/share/wayland-sessions` (and the same under `/usr/local/share`)
   offer (**D-060**; `station` defers the
   Plasma applet `hammunition-tray` on an Xfce or LXQt machine rather than
   pull in `plasma-workspace`). When a unit declares `desktops`, the plan
   prints *Desktops read from session files* with what they offered, and
   any file it read that named no desktop the catalog knows. A dependent of
   a unit deferred this way, and a profile of nothing else, name the
   desktop as the cause rather than the target.
   The member and its catalog dependents are listed under *Will NOT happen*
   with the reason, and the rest of the profile installs (**D-039**). A
   name you typed is never deferred: `hammunition install satdump` on
   Ubuntu 24.04 shows the refusal in full. An engine gap, a missing
   `depends` or `build_depends`, a retired status, and a profile with every
   member deferred all still refuse the transaction.
8. **Print the plan**, in full, for every run and not only for `--dry-run`.
9. **Present any consent gate**, then confirm, then execute — in this order:
   **every download first**, fetched into the cache and verified against the
   manifest's sha256 (**D-018**), so a wrong hash or a dead URL refuses on a
   machine nothing has touched — a repository signing key is one of these
   downloads, verified against the manifest's pinned fingerprint instead of
   a sha256 (**D-040**); then, when the plan adds a repository, its two
   files are written as root (`install -D -m 0644`); then `apt-get update`
   when the transaction has apt work — an apt step or a vendor `.deb` — and
   `--no-refresh` was not given, or whenever a repository was added
   (**D-044**); then, when the plan
   holds a vendor `.deb` or added a repository, one more `apt-get install
   --simulate` over the apt packages and the downloaded file together,
   because apt can only resolve a `.deb` from its file and can only see a
   repository's packages after the update — the plan-time simulate in step
   6 could include neither; then debconf preseeds, the apt step, the
   builds and installs in catalog order, configuration files, launchers,
   and group membership last (several groups are created by the package
   being installed). A `git` clone is a command that needs `git` from apt,
   so it stays in build order rather than moving up with the fetches.

If anything in steps 2–7 fails, **every** failure is printed together and
nothing is changed. Reporting only the first would have the same shape as the
defect this is built against: fix one, re-run, meet the next.

## What it refuses

Each of these is a named refusal with a remedy, never a silent skip. A
capability matrix that reports coverage the engine does not have is the shim
`CLAUDE.md` forbids.

| Situation | What you see |
|---|---|
| A `pipx` install block | the backend named — re-measured to zero users (D-014 amendment) and unwritten |
| A `source` or `git` block whose `build_system` is `custom` | the build system named. No manifest uses it, so it is an unimplemented gap rather than a regression (**D-014**) |
| A `data` artifact whose download is not the declared `size` | the URL, the declared and the received byte counts — the digest matched, so the manifest's declaration is what is wrong, and the plan printed a size that was not true (**D-049**) |
| A `patches` entry with no `unified_diff` | a description alone cannot be applied — building unpatched source would produce a binary the manifest does not describe. (Declared diffs stage and apply with patch(1) since v0.4.0.) |
| A `build_depends` package apt has no candidate for | which name, marked `build_depends`, **before** the toolchain is installed |
| A manifest declaring third-party `apt_repos` whose `/etc/apt/sources.list.d/<name>.sources` or `/etc/apt/keyrings/<name>.gpg` already exists **with content this engine did not write** | the file by path, marked foreign — a source under our name that somebody else wrote is never overwritten (**D-040**). Both files present with our content and still no candidate means the lists are stale; that says `--refresh` instead |
| A fetched signing key whose primary fingerprint is not the one the manifest pins | both fingerprints, and the key is discarded. A file that is not OpenPGP, fails its armor CRC, is truncated, or carries two primary keys is refused by name |
| A vendor `.deb` whose declared `conflicts_with_repo_package` is installed | the colliding packages by name, with the removal command — a dpkg file collision mid-transaction is the refused alternative |
| An apt step apt can only complete by **removing an installed package** — a `Breaks:` against something already there, the archive's `wsjtx-improved` against `wsjtx` being the measured case | every package apt would remove, with its installed version, attributed to the unit whose `conflicts_with_repo_package` declares it (or, when none does, named as a catalog gap), and the removal command so the operator can do it deliberately. Read from the same `apt-get install --simulate`; before it was read, a Kali guest with `wsjtx` installed planned clean, printed no removal, and would have lost three packages at the apt step (2026-09-07). The apt step itself now runs with `--no-remove`, so apt errors rather than removes if the real solve ever disagrees with the simulation (**D-022**) |
| A vendor `.deb` whose declared conflict is something **this same transaction's apt step would install** — directly, or as a dependency apt resolves | the package by name and both halves of the remedy: leave out the `.deb` unit, or the unit that pulls the conflict in. Found by the one `apt-get install --simulate` every transaction with apt work gets. A clean machine has nothing installed, so the row above is silent there; this one caught `digital-modes` planning clean and failing after forty-four commands (Kali, 2026-09-02) |
| An apt transaction apt itself **cannot resolve** as one `apt-get install` — the packages all exist, and the set of them still does not install | `apt: cannot resolve this transaction as one apt-get install`, then apt's own words, indented, and the simulate command that reproduces it. When the reason is that an installed package would be downgraded and it is installed from one other release, the plan is first retried from that release (**D-038**) and this row is reached only if that fails too. Five Parrot profiles passed the plan and died at the first apt command before this row existed (2026-09-02) |
| A `system_modifications` kind the engine neither performs nor discloses (`udev_rule`, `modprobe_blacklist`, `group_create`, `foreign_arch`, `package_purge`, `file_shadow`) | the kind, by name. `apt_pin`, `group_membership` and `file_capability` are performed; `package_service`, `package_udev_rule` and `package_account` are **disclosed only**: dpkg does them, the plan prints each as a note before the confirmation, and `uninstall` does not reverse them (**D-040**, 2026-10-05) |
| A package whose status is `broken` or `retired` | the recorded reason, verdict and date |
| A dependency apt has no candidate for | which name, and whether it came from `install` or `depends`. A *profile* member whose own `install` packages are the ones missing is deferred instead (**D-039**), and the row above still applies to its `depends` |
| A profile every member of which this target cannot install | the profile by name, with each member's reason — installing nothing and reporting success is not an outcome (**D-039**) |
| No apt package lists at all, and `--no-refresh` | that this is a stale-lists problem, and that dropping `--no-refresh` lets this run fix it. Without the flag, the run's own `apt-get update` comes first and the plan says instead that the candidate check cannot be done before it |
| A group membership with no identifiable operator | that `--user` is needed |
| A unit whose `requires_java` is above the Java this machine has, or no `java` at all | the unit, the measured `java -version` line (or that none was found on PATH or at `/usr/lib/jvm/default-java/bin/java`), the floor, and the archive's `openjdk-N-jre-headless` that would meet it where the plan's apt sweep knows one; nothing is fetched (**D-037**, amended 2026-10-02). A *profile* member is deferred instead, the D-039 shape, and `--json` carries it in `deferrals` as for any other deferral. A concrete `openjdk-N-jre*` in the unit's `depends` that meets the floor is not a deferral and is noted in the plan; no Java at all with only `default-jre-headless` in `depends` is disclosed as *check `java -version` afterwards* and the unit plans |
| A unit whose `requires_kernel` names a subsystem the running kernel's module tree lacks | the unit, the kernel release and the merge that removed the subsystem, with the remedies that exist: a distribution kernel that still carries it, or the userspace path (Direwolf's KISS/AGW ports serve pat, LinBPQ, YAAC and Xastir without kernel AX.25). Never an offer to build the module — no distribution packages one, and Hammunition builds no kernel modules (**D-041**). A *profile* member is deferred instead, the D-039 shape. No module tree for the running kernel at all — a container — is disclosed as *cannot be checked* and the unit plans |
| A unit whose `desktops` names none of the desktops this machine's session files offer | the unit, the desktops it is for and the ones the machine has (`(it has no session files)` on a server or container, and `(its session files name none the catalog knows: …)` on a machine whose only desktop the catalog does not name), and the remedy: the unit its manifest names in `desktop_alternative` when that one serves a desktop the machine has, otherwise installing a session for the unit's desktop first. A *profile* member is deferred instead, the D-039 shape (**D-060**) |

The dependency check is the one that earns its keep. **D-016** names four AHRL
dependency lines suspected of failing silently for years — `fftw2` (FFTW
version 2), `libgtk2.0-dev` (EOL), `python3-tksnack`, and an OCaml binding
fldigi does not use. The only reason nobody knows is that nothing ever asked
apt. This asks.

## Privilege

`requires_root` is a property of each command, not of the run. Unprivileged
commands stay unprivileged, `sudo` is added in exactly one place, and
resolution never asks for it at all — so `--dry-run` works as a normal user.

Three kinds of privileged command exist today: `apt-get`, `gpasswd --add` for a
manifest's declared `group_membership`, and the final install step of a source
build (`make install`, `cmake --install`). Each is printed before it runs and
recorded in the transaction log.

**A source build compiles as the operator, not as root.** Only the install into
`/usr/local` is escalated. A build run wholly as root would leave a tree of
root-owned object files in the operator's own cache for no benefit.

## How a source build works

A `source` install block becomes six steps, all of them printed before any of
them happens.

```
  # Install 3 package(s) with apt
  $ sudo env DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-remove -- fftw2 libgdk-pixbuf-2.0-dev libgtk2.0-dev
  # Download and verify the glfer source archive
  $ [fetch] https://www.qsl.net/in3otd/glfer-0.4.2.tar.gz -> ~/.cache/hammunition/artifacts/06aad6fa…-glfer-0.4.2.tar.gz (sha256 verified)
  # Unpack the glfer source
  $ [extract] ~/.cache/hammunition/artifacts/06aad6fa…-glfer-0.4.2.tar.gz -> ~/.cache/hammunition/build/glfer-06aad6fa/src
  # Configure glfer
  $ cd ~/.cache/hammunition/build/glfer-06aad6fa/src && CFLAGS='-Wno-incompatible-pointer-types …' ./configure --prefix=/usr/local
  # Compile glfer (8 parallel jobs; sized to CPUs and memory)
  $ cd ~/.cache/hammunition/build/glfer-06aad6fa/src && CFLAGS='…' make -j 8
  # Install glfer into /usr/local
  $ cd ~/.cache/hammunition/build/glfer-06aad6fa/src && sudo make install
```

A `[fetch]` or `[extract]` line is a step the engine performs **itself**, in
process, rather than a command you could paste — which is why it is bracketed
rather than rendered as a shell line. Both could have been shelled out to
`sha256sum` and `tar`, and both are safer here: the file handle and the
extraction filter are ours, so a redirect to `file://` and an archive member
named `../../etc/cron.d/x` are refused by construction rather than by whatever
the local tool happens to default to.

Everything else is an ordinary `Command` with a working directory, rendered as a
leading `cd` so the line stays copy-pasteable and an operator reproducing the
plan by hand runs it in the right place.

**Where things go.** Verified archives land in
`$XDG_CACHE_HOME/hammunition/artifacts`, named by their own sha256 — the path
encodes the expectation, so a file at that path can only be content that matched
it. Build trees go in `$XDG_CACHE_HOME/hammunition/build`. Both are caches in
the real sense: deleting them costs a re-download and a rebuild and nothing else.
Under `sudo` they follow the operator, not root, for the same reason the
transaction log does.

**Verification is not optional and cannot be skipped.** The schema requires
`sha256` on every remote artifact, so an unverified download cannot be expressed
in the catalog; the fetcher streams to a temporary file, hashes as it writes, and
moves the result into place only on a match. A mismatch deletes the download and
stops the run. A cached artifact is re-hashed on every use rather than trusted
for having been verified once.

Signature verification is **not** implemented. `signature_url` and
`signing_key_fingerprint` are carried in the catalog and are not checked, so an
artifact declaring them is digest-pinned rather than signed, and the plan says so.

**Build systems:** `cmake`, `autotools`, `qmake` and `make`, which is what the
catalog uses (6 / 2 / 2 / 2). `custom` is a measured zero and is refused by name
(**D-014**).

**Parallelism is sized to memory, not only to CPUs:** one job per CPU, capped
at one per 2 GiB of RAM plus swap, never below one. `-j$(nproc)` assumes the
machine was sized for it; JS8Call's Qt sources were OOM-killed at four jobs on
a 3.9 GB guest without swap and built at four on the same guest with 3 GB of
swap (2026-09-01), and a four-core Raspberry Pi with 4 GB is the same shape.
The job count is in the compile step's comment, so a slow build on a small
machine is explained before it starts.

## How a prebuilt binary is installed

Seven units in the dispositions wait on this and nothing else — QtTermTCP,
QtSoundModem and Pi-APRS from D-008's packet core, GARIM, AntScope2,
GridTracker2, and `sdrangel` on the five targets that do not package it.

Four formats, and the differences are the design:

| Format | What happens |
|---|---|
| `deb` | Fetched, verified, then **`apt-get install ./file.deb`** |
| `tarball`, `zip` | Fetched, verified, unpacked, and the files named in `binaries` installed |
| `executable` | Fetched, verified, installed under the one name `binaries` gives it |
| `appimage` | **Refused by name.** Post-1.0 per `docs/SCOPE.md` |

**A `.deb` goes through apt, never `dpkg -i`.** apt resolves the package's
dependencies; dpkg installs it and leaves them broken, which is the classic way
a vendor package wedges a machine. It also means the result is an ordinary
installed package apt knows about, so removing it later is `apt remove` rather
than archaeology. If apt refuses — usually a `.deb` built for a different
release — that is the correct outcome and the transaction stops there.

**Nothing here is unverified.** `sha256` is mandatory in the schema and the
fetcher refuses a mismatch, leaving nothing usable behind. That matters more
than for a source build, because nobody is going to read a `.deb`.

**An archive naming no `binaries` is refused at plan time**, because unpacking
it would leave a directory in a cache and install nothing while reporting
success. The unpack directory is keyed by the artifact's digest, so a vendor
who republishes under the same URL does not get their new files layered over
the old ones.

## How a git build works

A `git` block builds the same way once the tree is there; only how it *arrives*
differs, and so does the question that has to be answered about it.

```
  # Clear any previous ais-catcher checkout
  $ [prepare] ~/.cache/hammunition/build/ais-catcher-v0.70/src (removed if present, then recreated)
  # Start an empty repository for ais-catcher
  $ git init --quiet ~/.cache/hammunition/build/ais-catcher-v0.70/src
  # Point it at https://github.com/jvde-github/AIS-catcher
  $ git -C … remote add origin https://github.com/jvde-github/AIS-catcher
  # Fetch ais-catcher at v0.70
  $ git -C … fetch --depth 1 origin v0.70
  # Check out v0.70
  $ git -C … checkout --quiet FETCH_HEAD
  # Confirm ais-catcher is at the pinned revision
  $ [verify-pin] git rev-parse HEAD in … must be v0.70
```

**The archive backend asks *are these the right bytes*; this one asks *is this
the right revision*.** A sha256 answers the first. Nothing about a successful
clone answers the second: `git` can exit 0 having handed over a different commit
than the catalog was written against — a re-cut tag, a moved branch, a server
that ignored what was asked for. So the pin is **checked after the checkout and
before the build** (**D-031**). A commit pin must match exactly or the run stops;
a tag has nothing to compare against, so the revision it resolved to is recorded
instead — which is the raw material of the pin database, because the day a tag is
re-cut the log says what it used to be.

**A moving ref cannot be expressed.** The schema refuses `master`, `main`,
`HEAD`, `trunk` and `develop`, and a bare commit SHA requires a `pin_review`
naming who reviewed it, when, and why that commit (**D-024**). A tag carries an
upstream signal that somebody thought a revision worth naming; a SHA carries
none, so pinning one moves a judgement upstream stopped making onto us, and it is
recorded beside the pin rather than implied by it.

The fetch is shallow and by ref, so a pinned commit costs one object walk rather
than a project's whole history.

**A tag may name its commit** (`commit:`). The pin check then compares the
checkout with it and refuses a re-cut tag instead of only recording what it
resolved to. CoMaps pins `v2026.08.31-14` to `72632e4`, the commit Flathub,
nixpkgs and the AUR build (**D-069**).

Four more steps exist for a build that needs them, each catalog data and
each run by the engine (**D-069**; CoMaps is the one user):

- `submodules: true` runs `git submodule update --init --recursive --depth
  1` after the pin check, then `git submodule status --recursive`, and stops
  unless there is at least one submodule and each is at the commit the
  pinned revision records.
- `build_python` makes a venv beside the tree
  (`<build>/build-python`) with the engine's own interpreter and installs
  the hash-pinned lines with `--require-hashes`; the prepare, configure and
  compile commands run with `VIRTUAL_ENV` and the venv first on `PATH`. The
  install command does not.
- `prepare` runs an upstream script in the tree (`./configure.sh
  --skip-map-download` for CoMaps) with its declared environment,
  `CMAKE_BUILD_PARALLEL_LEVEL` set to the job count, then checks that each
  glob in `produces` matches a non-empty regular file: CoMaps' symbol
  generation exits 0 with no symbols when optipng is missing.
- `extra_files` installs, after the build's own install, each file its rule
  leaves out, a sha256-pinned download (fetched with the others, before
  apt) or a file of the built tree, with `rm -f` first so a symlink at the
  destination is replaced, never written through. The effect check then
  requires a regular file there.

## Consent gates

A gated profile presents its disclosure before anything runs. `--yes` is
accepted by the call and deliberately never read: a gate a convenience flag
walks through is not a gate (**D-021**). In a script, set the profile's own
`HAMMUNITION_ACCEPT_*` variable to `1`. With no terminal and no variable, the
run stops — silence is not consent, and "nobody was asked" is recorded
differently from "somebody said no".

**A third-party apt repository has a gate of its own** (**D-040**), presented
after any profile gate and once per repository, whether the unit was
named or reached through a profile. The disclosure names the unit, the
URI, suites and components, the key's primary fingerprint, and the two
files that will be written — `/etc/apt/sources.list.d/<name>.sources` and
`/etc/apt/keyrings/<name>.gpg`, the latter in binary OpenPGP form, the
former with `Signed-By:` naming it and nothing wider. The variable is
`HAMMUNITION_ACCEPT_APT_REPO_<NAME>` (the repository's `name`, upper-cased,
`-` and `.` as `_`), and **its value must be the fingerprint itself**, not
`1`: checking the fingerprint against the publisher's own page is the one
step the engine cannot do for you, and a variable set to `1` would be
`--yes` again under another name. A `1` is refused with the value it
should hold. The plan prints the variable and the fingerprint together.
The affirmation is logged as `consent_affirmed` with profile
`apt-repo:<name>`, so the log records who trusted which key and when.

A repository may be **flat** (**D-040**, amendment of 2026-10-05): the openSUSE
Build Service publishes `Release` and `Packages` directly under the URI, so the
manifest declares `suites: ["./"]` with no components, the `.sources` file has no
`Components:` line, and the disclosure and the plan say "a flat repository".

The repository is added only when the target's own archive offers no
candidate for the unit's packages (**D-022**): on Parrot, `codium` installs
from Parrot's archive and VSCodium's repository is neither added nor asked
about. A `depends` the archive lacks is never a reason to add one.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success — every command ran **and** its effect was confirmed afterwards |
| 1 | A command failed while running, a completed command's effect could not be confirmed (D-031), or the system is unsupported |
| 2 | The transaction could not be planned — every blocker is printed |
| 3 | A consent gate was declined, or could not be presented |

## What is recorded

Two records, for two readers. Every run that changes something also writes a
plain-text **run log** (`hammunition logs`, `docs/reference/run-logs.md`,
**D-077**): what it printed, each command it ran with the command's output and
exit code, how it ended. It ends with a `Log: <path>` line on stderr (not under
`--json`). The **transaction log** below is the machine's record, which
`uninstall` stands on.

Every run appends to the transaction log — format in
`docs/reference/transaction-log.md`. Each command is logged **before** it runs
and its outcome after, so a run killed mid-`apt-get` leaves a record that the
command was started. That is the state an operator needs to see, and a log
written only on success would hide it.

The log is itself a modification, so the plan discloses it: a **Records**
section names the destination path, and under `sudo` — where root writes into
the operator's home — it says the log and the directories created for it are
handed back to that operator (`chown`). The path shown is the path the run
uses, so if the operator cannot be resolved and it falls back to root's home,
the plan says so rather than redirecting in silence.

**A command exiting 0 is not recorded as an effect.** `apt-get install` can
exit 0 having installed nothing a held or broken package quietly refused, and
`gpasswd` exits 0 whether or not the membership took (**D-031**). So after every
command has completed the run **re-reads** what it claimed to change — from the
same sources resolution used pre-flight, `apt-cache policy` for a package,
the group database for a membership, and the filesystem for every binary a
source, git or binary unit declares (`<prefix>/bin/<install_as>` must exist and
be executable — js8call's `cmake --install` exits 0 and installs nothing, and
was recorded confirmed on four targets before this check existed) — and
records the confirmed state, not the exit code, in `transaction_end`. That is the record `uninstall` will trust, and
it must not say "installed" on the strength of a return value. A completed run
whose effect cannot be confirmed prints exactly what did not take and exits 1;
its log entry carries `verified: false`.

`hammunition status` reads that log back and reports how the **most recent
transaction ended** — completed, failed after N commands, or interrupted with
no ending recorded — never just what it set out to do, and for a completed run
whether its effects were **confirmed afterwards** or came back unverified. A run
that died partway is not reported as if it finished. What that run **deferred
by design** — a profile member the target does not offer (**D-039**), a
configuration file a station value was missing for (**D-035**) — is listed
after the packages it intended, from the `deferred` entry the log has carried
since `transaction_begin` version 2, so a profile that landed eighteen of
twenty-two still reads that way a week later.

Hammunition does not roll back. It tells you what it did (**D-004**). On a
failure the run stops at that command, and the count that completed is printed
along with the log's location.

One failure is diagnosed rather than merely printed. When an `apt-get install`
fails with `404 Not Found` on files in the pool, the package lists on the
machine are older than the archive: the plan resolved against those lists,
so the catalog is not at fault, and apt downloads every archive before it
unpacks any, so the command installed nothing. The message says so, names
the files by version, and gives the remedy — `sudo apt-get update`, or the
same run without `--no-refresh`. Six of fifteen profiles on a four-day-old
Parrot guest died this way (2026-09-03), each report ending in seven URLs and
apt's own hint under them; that campaign is why the refresh became the
default (**D-044**). The diagnosis still exists for the two runs it can
reach: one under `--no-refresh`, and one where the mirror moved between the
run's own update and the fetch. A `5xx` or a timeout is a mirror problem and gets
no such diagnosis; sending an operator to refresh lists that are fine would
be a wrong answer with a confident tone.

## What is not here yet

`uninstall` reverses apt, venv and binary installs, copied binaries,
installed trees, wrappers and desktop entries. What it still refuses, by
name: a source or git build that ran a real `make install` into `/usr/local`
— no file manifest exists to reverse, and the planned fix is a staged
install that records one. udev rules and group memberships are recorded but
not yet reversed.
