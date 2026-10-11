<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Changelog

One entry per release, assembled from the fragments each merged pull request
adds under `changelog.d/` (never edited here in a pull request), each line
naming the PR and the decision it rests on. Decisions are authoritative in
`docs/DECISIONS.md`; this file is the map from a version to them.

## Unreleased

Nothing yet.

## v0.22.0 — 2026-10-11 — offline Bunker catalogue, venv wrapper ownership, user-service disclosure

- #381 (D-085): Enrol signed Bunker catalogues, resolve data with publishers unreachable, fetch pinned payloads and git bundles from the Bunker, and check hardware signing readiness without enabling PAM.

- `meshtasticd`, the Meshtastic node daemon for Linux, from the Meshtastic project's own repositories (the openSUSE Build Service on Debian 13, Parrot and Kali; the Launchpad PPA on Ubuntu 24.04, Mint 22.3 and Ubuntu 26.04) under D-040: the key fingerprints are pinned from the published keys, consent is typing the fingerprint, and the package's boot service, its world-writable udev line and its system user are disclosed in the plan. It joins the `mesh` profile. A flat repository (`Suites: ./`, no `Components:`) and three disclosure-only `system_modifications` kinds are the engine additions (D-040's amendment of 2026-10-05); the question of a gate of its own is Q-023 (#308, #105).

- Keep the suite board current: a `project-sync` caller for GYST v1.12.0 (Hammunition #362) adds this repository's issues and pull requests to the Renegade-Penguin board, sets status and done date, and reconciles weekly.

- The plan now discloses a user service's first-start behaviour (manifest field `first_start`) under *User services*, and the uninstall plan names what it leaves in place (manifest field `left_in_place`), filled in for `rns`, `lxmf` and `nomadnet`; both are validated, cleaned of control characters, capped, and shown without ever being acted on (issue #289, D-086).

- CI: the security workflow pins GYST v1.13.0, which runs gitleaks as a sha256-pinned binary instead of the licence-gated action; Security runs are green again under the organization (#362)
- `.gitleaks.toml` allowlists OpenPGP v4 key fingerprints (the D-040 `key_fingerprint:` pins); the binary scan had flagged nine of them as generic API keys, and a planted AWS key is still caught (#369).

- `hammunition-hill` pinned to v1.3.0: the repeaters panel follows the engine's active areas with per-session chips (hill #90, D-082), Atheris fuzz targets through GYST (hill #89) and the security sweep (hill #88); digest read from the release's SHA256SUMS and confirmed against the downloaded file.

- The project moved from the `ChiefGyk3D` user to the `Renegade-Penguin` organization (#359, epic #357): every suite repository URL, the Pages site URL, the engine's `User-Agent` strings, the polkit `vendor_url` and the catalog manifests that pin the suite's own releases now point at the organization. `CHANGELOG.md` and the session log keep their history as written.

- GYST callers to v1.12.0 (every input they pass exists in v1.12.0; v1.11.0 to v1.12.0 changed only `project-sync.yml`).

- CI: the project-sync caller no longer runs on pull request events; Doppler OIDC cannot cover a pull-request subject (git-your-ship-together#93), so the `sync / Sync project` check was red on every PR. Pull requests reach the board through the weekly reconcile (#376).

- A venv unit no longer replaces a `~/.local/bin` entry Hammunition did not write (#288, D-022, amending D-080): the plan refuses by name, with a remedy, any command that is a pipx symlink, a pip script or anything without Hammunition's wrapper header, and the write re-checks and never writes through a link; re-installing over its own wrappers (also the pre-header form) still works. Review round 2: an `expose` or node `command` name must be one plain file name (no `../`, no absolute path, no control character), a wrapper is mode 0755 whatever the umask, and `uninstall` opens the entry through a directory descriptor, refuses a symlinked ancestor when running as root for an operator, and removes only the inode it read.

- `uninstall` left every apt package behind as "installed, but not installed by Hammunition" since #236: the attribution read `argv[1]` of `apt-get -o Acquire::Retries=3 install ...` as the verb and saw `-o`. The verb is now the first non-option argument, and the engine's own `install --simulate` pre-flight never attributes a package (found installing and removing `meshtasticd`, #308).

- Code scanning, second pass: removed the unused `SELF_UPDATE_ARGV`, parenthesised the split update-dialog paragraph, a direct reference in place of a wrapper lambda in the self-update test, a reason on the fuzz target's expected-refusal catch. The `station show` CodeQL "secret" alert and the `US_STATE_NAMES` unused-variable alert are false positives, dismissed, and the links directory's 0700 `chmod` alert (marked in source, which Semgrep honours: its SARIF carries the finding as suppressed) is dismissed as by design.

- Activity hub pages for GPS, position and time, APRS, Meshtastic, Reticulum and MeshCore (a stub), under `docs/activities/`: what the activity is, the catalog's programs and hardware (generated by `scripts/gen_activity_hubs.py`, held by `tests/test_docs_generated.py`), how to install, a first task, what works offline, and what was measured and not; linked from the suite page and the application directory (#305, part of #298).

- `docs/reference/cli.md`: the `artifacts` default unit list now names `repeater-snapshots`, which the command has listed since D-078 (branch `artifacts-doc-default`).

- The Hammunition logo (drawn by the maintainer 2026-10-05) and a brand package in Hill's layout: `brand/` marks, favicons, social preview and a sampled palette, the README and site headers, the site favicon, and `docs/contributing/branding.md` (#374).

- Console coverage (#323): `docs/reference/console-coverage.md` is generated from the CLI's argparse tree against `hammunition.console.coverage`, and `tests/console/test_coverage.py` fails on an unclassified verb, a covered verb its screen does not name, or a growing uncovered list (31 verbs still have no screen, each with its issue).

- README carries the OpenSSF Best Practices badge; project 15243 passes all 67 Passing criteria (#370).

- D-083: review policy under one maintainer: a pull request with zero approvals on `main`, a `CODEOWNERS` file, administrators not enforced, the Scorecard Code-Review score left at 0 by record; revisited when a second maintainer joins (#333, PR #368)

- D-084: targets are releases in regular support (Debian 12 not planned, Kali best effort, LTS and stable preferred); the uConsole image is the one exception to "no custom ISO", conditional on Debian 13 not being documentable onto the device (epic #372).

## v0.21.0 — 2026-10-04

- The console's **Repeaters** screen (`7` on Home, #322): the repeater layers grouped by area with the active mark, the areas, and keys to fetch RepeaterBook by state (opening Secrets first when `REPEATERBOOK` cannot be supplied), import an export, choose the active areas, remove a layer and look repeaters up by place, distance, band and mode; and, from both it and Secrets, an offer to install `repeaterbook-client` when it is missing, the fetch running after a successful install (#344). D-059, D-064, D-074, D-081, D-082.

- `hammunition secrets status [--json]` says where each secret the engine knows would come from (the environment, Doppler or nowhere), the unit and first command it unlocks and the exact ways to provide it, and never a value, a prefix or a length; the console gains a Secrets screen (`3` on Home) that reads it, names a Doppler project and config through `station set`, keeps a value entered for the session in the console's memory only (handed to the engine commands it starts, written nowhere, cleared on exit) and runs `maps repeaters fetch-repeaterbook` once a source answers. Built against fixtures and the fake engine; not yet run against the live RepeaterBook API or on the field laptop (#321, D-081, D-059).

- **`hammunition-console` is a catalog unit** (**D-059**, **D-021**; branch `console-unit`): a terminal front end in its own repository, pinned by its tag's source-archive sha256 like the tray, installed by name and in no profile, with a terminal menu entry. It reads only `--json` documents and runs every change in a pane where a person types any consent. `docs/getting-started/console.md` walks the first run. Rename this fragment to `<PR number>.added.md` when the pull request has a number. Superseded within this same release: the console moved into the engine as `hammunition console` (#302, below) and the unit is retired.

- **Atheris fuzz targets for the parsers, run by GYST's `python-fuzz.yml`** (part of #333; branch `fuzz-targets`): `fuzz/` holds six targets (manifest loaders, repeater imports, Maidenhead, gpsd JSON, `os-release`, station config); a `fuzz` job in `ci.yml` runs them 30 s each on a pull request and 600 s on the weekly schedule; `tests/test_fuzz_targets.py` keeps them runnable. Found and fixed: a bare carriage return in a CSV row escaped `maps repeaters import` and the ETCC and Direwolf readers as `csv.Error` instead of a `RepeaterInputError`, and a station file with a non-text key died with a `TypeError` instead of a `StationError`. Rename this fragment to `<PR number>.added.md` when the pull request has a number.

- `hammunition hardware gps-resume-report` (read-only, `--json` as a `gps-resume-report` document) shows whether the installed GPS resume step matches the engine's, the unit's last result, gpsd's device list and a data check, the receiver's USB facts and the step's last run's lines; the step now keeps those lines in `/run/hammunition/gps-resume.log` through a third file `hardware apply` writes (`/etc/tmpfiles.d/hammunition-gps-resume.conf`), so no `systemd-journal` membership is needed. `hammunition time measure --minutes N [--pps]` samples `ntpq -pn` and `ppstest` for the GPS-takeover bench. Part of #177 and #310 (D-058, D-059; the bench is session 14 of `docs/reference/bench-verification-5430.md`, not yet run).

- **Infrastructure and EMCOMM layers are kept per region** (issue #327, part of #326; **D-075** amended, **D-082**): `maps infra import` and `fetch-fcc-asr`/`fetch-nwr` write one layer per theme and installed region (`infra-osm-medical-<region slug>`), data-source layers clipped to each region's box, so `maps activate` draws only the active regions' infrastructure in QMapShack, Navit and the browser map. `--merged` keeps one layer across every region; an older merged layer is left alone with a note, once; `maps infra remove --layer` takes a theme or `<theme>-<region>`; the `infra` documents' layer views carry `area`.

- `hammunition maps activate CODE|REGION ... | --all | --none [--dry-run]` and `maps areas` switch the active area of operations (the station value `active_areas`, `station set --active-areas`, `--clear-active-areas`): QMapShack's `poiPaths` (a directory of links to the active areas' `.poi` files), Navit's map set and the browser map's list follow it, `maps repeaters list --json` and the infrastructure documents carry `active` per layer, and deactivating deletes nothing; also fixes `ensure_paths` swapping one path for another under one key. Built and tested on synthetic layers, not yet run in QMapShack, Navit or a browser (#328, part of #326, D-082).

- The `gpsd` unit now installs `pps-tools` with it, so `hammunition time measure --pps` can run `ppstest` on a machine set up for GPS time on either route (ntpsec, D-058, or the `chrony` unit, D-072); closes #319.

- **`hammunition maps repeaters fetch-repeaterbook`, the `repeaterbook-client` unit and one secrets helper** (**D-081**, **D-078**; branch `repeaterbook-api`): `resolve_secret(name)` reads a key from an environment variable, then Doppler (`station set --doppler-project/--doppler-config`, `--clear-doppler`), and never from the repository, station file, argv or a log; the run log redacts it and any credential header. The verb runs the unofficial `repeaterbook` client (a hash-pinned venv unit, registered with RepeaterBook as App #114; the operator generates their own token for it) through an engine-owned runner, with the token in that subprocess's environment only, into an unverified, personal-use `repeaterbook` layer (0600, credited with a link, never mirrored, never listed by `artifacts`). Built against the documentation and the client's source; not yet run against the live API. Rename this fragment to `<PR number>.added.md` when the pull request has a number.

- `hammunition maps repeaters list [--layer ID] [--json]` reads the repeater layers back as a `repeaters-list` document for front ends: layers with `personal_use` and `unverified`, the joined rows with their layer, the credits to print, and the layers it could not read, exit 0 on a partial list; read-only (#312; D-074 amended 2026-10-04, D-059, D-081).

- Repeaters have a mode vocabulary (`FM`, `DMR`, `D-STAR`, `YSF`, `P25`, `NXDN`, `M17`, `TETRA`, `ATV`), a band and the digital details a source actually supplies; `maps repeaters list` gains `--near GRID|LAT,LON`, `--within KM`, `--band` and `--mode` with distance and bearing, nearest first; the QMapShack POI has one category per mode and every name carries `N0CALL 146.940 2m FM DMR` for a text search (#313; D-074 amended 2026-10-04).

- **Reticulum, NomadNet and LXMF** (Track C, PR 2, issue #105, **D-080**). Three
  hash-pinned per-user venvs, `rns`, `lxmf` and `nomadnet`, because no archive
  carries any of them: `rns` exposes every console script Reticulum declares
  (`rnsd`, `rnstatus`, `rnpath`, `rnprobe`, `rnid`, `rncp`, `rnx`, `rnsh`, the
  RNode flasher `rnodeconf` and the rest) and installs a user service,
  `hammunition-rnsd`, that keeps one shared instance per machine (the first
  operator's service runs it and another account's attaches as a client; enabled at
  install, started at next login; an abstract local socket, not a TCP port,
  measured in a Debian 13 container); `lxmf` gives `lxmd` and starts nothing;
  `nomadnet` gives the terminal messenger and a menu entry. The Reticulum
  License (MIT plus two use restrictions, not OSI-approved) is printed on the
  plan line that installs the venv and never gated; NomadNet is `GPL-3.0-only`
  by its shipped text. The engine writes no Reticulum configuration and
  uninstall leaves `~/.reticulum`, `~/.nomadnetwork`, `~/.lxmd` and `~/.rnsh`.
  New post-1.0 `mesh` profile (the three plus `python3-meshtastic` and
  `gtk-meshtastic-client`), a `rnode` hardware entry (`untested`: no identifier
  of its own), and `docs/guides/mesh-and-reticulum.md` with four
  troubleshooting entries. Engine: a user service's `exec` may start
  `{venv}/...`, and a venv block may state its `licence` and `licence_url` on
  its plan line. Two containers on one bridge found each other, exchanged an
  LXMF message and ran `rnsh`; no LoRa link has been run.

- **`hammunition self-update` pulls the engine's own checkout and re-runs `bootstrap.sh`, and `--version`, every `--json` document and `doctor` say when the venv lags the checkout (#303, #311).** The verb fetches, fast-forwards to `origin/main` (or the newest `v*` tag with `--release`) and re-runs bootstrap, each step printed before it runs, `--dry-run` printing the commits that would arrive; it refuses a dirty tree, a detached or non-`main` branch and a non-fast-forward, and never touches apt, units or the station; its run is teed to a D-077 log. `hammunition --version` prints `0.20.0 (checkout), 0.19.0 (installed); run `hammunition self-update`` when the editable install's metadata is behind `pyproject.toml`, the `engine` field of every document is the checkout's version, `doctor` gains an `engine version` check with the fix as argv, and the console's Home offers the update with `U`.

- **`hammunition console`: the terminal console is part of the engine** (#302, **D-059** amended 2026-10-04): `hammunition_console` moved to `src/hammunition/console/` with its tests in `tests/console/`, one release and one version, so `ENGINE_FLOOR`, `EngineTooOld` and the fixtures' `engine` field are gone. urwid is the optional `console` extra; without it the subcommand prints one line naming the install command and exits 2. The console runs the engine as `<interpreter> -m hammunition`. The `hammunition-console` unit is retired, `menus apply` writes the engine's own entry for it, and `tests/console/capture_fixtures.py` refuses to run unless HOME is a temporary directory it made itself. The old repository is archived; see the console reference at `docs/console/index.md`.

- `hammunition-hill` pinned to v1.2.0 (the repeaters panel with any-centre lookup and mode filters, hill #83/#85), its digest from the release's SHA256SUMS; the suite page's status row follows (#314).

- **RepeaterBook is one layer per state (#325, epic #326, D-074 amended).** `fetch-repeaterbook --state OH --state PA` writes `repeaterbook-OH` and `repeaterbook-PA`, each with its own four files, so QMapShack's POI dock has one tick box per state; a re-fetch replaces that state's layer whole, `remove --layer repeaterbook-OH` and `list --layer repeaterbook-OH` take one, `--county` fetches per county where an answer is cut at the 3,500-row limit, `maps repeaters list --json` layers carry `area`, and the earlier merged `repeaterbook` layer stays until removed (the fetch says so once). The guide records how QMapShack 1.17.1 loads a POI file (lazy, per viewport cell, read from source).

- **Repeater POIs show what an operator keys in (#324, D-064 amended).** QMapShack draws only a POI's name, so the name is now `N0CALL 146.940 2m -0.600 T100.0 FM` (offset, tone as `T`/`D`/`CSQ`, modes), the POI gets a `keying=` line for the hover tooltip, the GPX a `<cmt>`, and Navit's label and `maps repeaters list` the same line; an `=` in a POI value no longer makes QMapShack drop the description.

- **Every open CodeQL and Semgrep alert was read; the real ones are fixed and the rest dismissed with a written reason.** Fixed: eight unsafe import cycles (`Deferral` moved to `src/hammunition/deferral.py`, the shared topo helpers to `src/hammunition/backends/topo_common.py`), 79 wrapped string literals in lists now joined with an explicit `+`, repeated and doubled imports, five empty `except` blocks that now say what they ignore, one `except OSError` that now exits explicitly, and a handful of dead reads. One real defect: a package manifest's `config_files` `mode` reached `chmod` and `install -m` unchecked, so a manifest of any tier could ask for a setuid or world-writable file; the schema now refuses a mode with a special bit or world write, and a test holds it. The remaining alerts (Protocol stubs, deliberate 0644/0755/0700 modes, SHA-1 where the format defines it, cleanup-and-reraise on `BaseException`) are dismissed one by one with the file and reason in the pull request.

- The GPS resume script says why a gpsd socket error ends its watch early instead of swallowing it silently; the two file-mode scanner alerts on the staged sleep hook are dismissed with the reason (the unprivileged engine reads root's copy back to verify it, D-031) (CodeQL sweep).

- The GPS resume step now checks that gpsd delivers data after the re-open and, when the receiver stays silent, power-cycles it once through its USB `authorized` switch (the one `hardware park`/`wake` use), then checks again; it exits 0 only when data was seen and otherwise names the manual steps in its last journal line. The plan's disclosure says so (issue #177, D-058 amendment 2026-10-04).

- Code scanning tail: the last `side-effect-in-assert` in the OSM header test and the digest refresher's token parse (no download piped into an interpreter) are fixed; the one remaining CodeQL note is a re-export two modules import and is dismissed with that reason.

- OpenSSF Scorecard remediations: a `SECURITY.md` (private reporting, supported versions), every container base image pinned by multi-arch index digest with `containers/refresh-digests.sh`, and hash-pinned `requirements/{runtime,dev,docs}.txt` installed with `--require-hashes` by CI, `bootstrap.sh` and the target Dockerfile, kept current by `scripts/check_locks.py` (PR scorecard-remediations).

- The Semgrep suppression on the active-area links directory sits on the lines it covers (the previous-line form was not honoured); no behaviour change.

- `load_station` refuses a `map_regions`, `reference_books` or `topo_regions` value that is not a list by name instead of raising `TypeError` (an int) or iterating a bare string's characters; found by the station-config fuzz target on its second CI run (#339).

- **The test suite is isolated from the operator's own files, and fails the run if the real station file changes.** A session fixture in `tests/conftest.py` points `HOME` and every `XDG_*` base at a temporary root, drops `USER`, `SUDO_USER` and `LOGNAME`, and hides passwd from `hammunition.paths`, so even euid 0 under `unshare -r` cannot resolve a real home; a session-end check compares the real station file's mtime and sha256. Two runs on 2026-10-03 had rewritten it. `tests/test_isolation.py`, `docs/contributing/ci.md`.

- `uninstall` removes the launcher wrapper and generated menu entry an install recorded in the transaction log, not only those the current manifest still lists, so a unit whose manifest lost its `launchers` block or was retired no longer leaves them behind; a file whose generated marker is gone is still kept and reported (#336).

- A guide for the area of operations, "Load every area ahead, activate where you are": the pre-deployment walk (regions, RepeaterBook per state with the token from the Secrets screen or the environment, infrastructure per region, `maps areas`), the on-arrival `maps activate`, what each program then shows, and what is unmeasured (the symlink `poiPaths` directory); the EMCOMM guide's datasets table gains a per-area column. Closes #329, completes #326 (D-082).

- The documentation gains `docs/suite.md` (the suite: engine, Tray, Hill, Bunker, Console and GPS Tether, with what each owns), an EMCOMM preparation and field-use guide, a "before a deployment" download page, and a generated application directory (`scripts/gen_application_directory.py`). The documentation site stops requesting Google Fonts (`font: false`), so the release tarball reads with no network (#300, part of issue #298).

## v0.20.0 — 2026-10-03 — the run log and live feedback, an offline aircraft map, bounded US Topo, retries and deferrals when a publisher is down, SDR entries for the HydraSDR RFOne and Fobos, and a wiki and a documentation pass for newcomers

- **tar1090, the ADS-B aircraft map, as a page in `reference serve`**
  (**D-071** amended 2026-10-02; D.10b's route). A new `tar1090` unit
  (`listening`, depends on `readsb`, so deferred by name where readsb is) is a
  `data` unit: GitHub's archive of upstream's commit `e784ee5` (no tags, no
  distribution packages it: own-choice pin, D-024), sha256 pinned, `html/` and
  the GPL-2.0-or-later licence kept. Its root `wget | bash` installer and
  lighttpd on port 80 are not used. `hammunition reference serve` serves the
  page at `/aircraft/` on 127.0.0.1, with `data/` read from readsb's directory
  (`/run/readsb`, or `--readsb-json DIR`), read-only. The page cannot call
  out: tar1090's settings for photographs, routes and overlays are switched
  off, its online layers are replaced by the station's PMTiles map (a blank
  background, with the reason on the page, when `osm-pmtiles` is absent), and
  every response carries a Content-Security-Policy that names no host. The
  page is given a reduced `receiver.json` so it reads plain `aircraft.json`
  (readsb 3.14.1630 also writes `aircraft.binCraft.zst`, which tar1090 would
  otherwise ask for). The aircraft database is not carried (upstream's one commit cannot be pinned).
  Tested in headless Chromium: the aircraft appear, no request leaves
  loopback, the same page as upstream ships it does ask other hosts, and the
  policy alone refuses them. Not measured: a live receiver, Firefox, a real
  region at street zoom.

- **Every run leaves a log** (**D-077**). `install`, `uninstall`, `update`,
  `menus apply`, `hardware apply|unapply|park|wake`, every `maps ...`,
  `reference serve`, `services ...` and `time mode` -- `--dry-run` included --
  write `<state dir>/logs/<UTC>-<command>-<pid>.log`: the argv with station
  flags redacted, everything printed on stdout and stderr, each command run with
  its output as it arrives and its exit code, a `result` line; 0600, flushed
  per line, owner-aware under sudo. Rotated at the start of each run to 30
  files and 200 MB, never a run in progress. New `hammunition logs [--last |
  --path | --json]`; `doctor` reports the logs; a run ends `Log: <path>`. The
  transaction log now rotates past 1 MiB into `transactions-<NNNNNN>-<UTC>.jsonl`
  archives that every reader walks in order (never deleted); `status` is tested
  identical before and after. `docs/reference/run-logs.md`.

- **SuperSDR joins the `listening` profile.** Its profile documentation states
  the D-033 position for its missing upstream licence and that it is never
  mirrored.

- **Transaction history is now readable** (#243, **D-077**). `hammunition
  transactions [--last N] [--json]` lists the live and archived transaction
  records in chronological order, including deferred units and whether a run
  completed, failed, aborted or is still in progress.

- **Live feedback while an install step runs** (#270, **D-077**). On a terminal a
  command that runs longer than two seconds gets one in-place status line under
  its `$` line, `… 1m 42s  <the command's last output line>`, erased when the
  command ends; `install -v` / `--verbose` (and `uninstall`) stream every output
  line as it arrives instead. Nothing is added when stdout is not a terminal,
  and the run log is byte-for-byte what it was in every mode. The status line and
  the sudo keepalive's warnings share one terminal writer (`progress.LiveStatus`),
  so they cannot interleave. A step the backend knows is long (a submodule fetch,
  a `cmake`/`make`/`qmake` compile, a venv `pip install`, a node build) says
  `this step can take several minutes` in the plan and at step start, with no
  invented duration; `StepView` gains `long_running` in `--json`.

- **HydraSDR RFOne and RigExpert Fobos SDR: host software and device entries,
  from upstream's own files, for hardware nobody here owns** (**D-024**,
  **D-027**, **D-028**, **D-029**, **D-032**). Five units: `hydrasdr-host` (the
  library and its seventeen tools) and `soapysdr-module-hydrasdr`, and
  `libfobos`, `libfobos-sdr-agile` and `soapysdr-module-fobos`, the last
  needing both libraries to build. Kali and Ubuntu 26.04 take the HydraSDR
  units from apt; every other target builds the tags Debian packages
  (v1.1.1, v1.0.1). Nothing Fobos is packaged anywhere, so `libfobos` (head
  1e0fab3) and `soapysdr-module-fobos` (aa8d486) are commit pins with a
  `pin_review`, `libfobos-sdr-agile` is upstream's tag. Two entries under
  `catalog/hardware/devices/`: `hydrasdr-rfone` (38af:0001, `status:
  supported` in D-027's shape, with 1d50:60a1 recorded in `rejected_ids`
  because the Airspy R2 and Mini use it and no rule can tell them apart, and
  the NXP DFU recovery id marked shared with the HackRF) and `fobos-sdr`
  (16d0:132e, `untested`; both firmware families present it and differ only
  in bcdDevice). Identifiers are cited as commit-pinned URLs into the vendors'
  repositories; `scripts/check_rule_citations.py` now ignores a rules file
  named inside a URL, since the sweep covers distribution packages only.
  Built in rootless containers on Debian 13, Ubuntu 24.04, Ubuntu 26.04 and
  Kali (x86_64): all five units build and install, `hydrasdr_info` and
  `fobos_devinfo` run without a board, `SoapySDRUtil --info` lists both
  modules. Three defects of upstream's builds are handled in the manifests,
  each measured: libfobos-sdr-agile wrote `/etc/udev/rules.d` as root (a
  CRLF-preserving patch removes it), SoapyHydraSDR also wrote into dpkg's
  module directory (a define), and nothing a build into `/usr/local` links
  was found without `ldconfig` (an embedded run path). Not owned, not run
  against a board; the units stay out of the `sdr` profile (D-020). The
  HydraSDR tree states two licences (per-directory LICENSE.md files, and a
  debian/copyright reading "licensed exclusively for HydraSDR products"); the
  unit's page says so and the catalog follows the LICENSE.md files (D-033).

- **`requires_java`: a plan-time Java floor measured with `java -version`**
  (**D-037**, amended 2026-10-02). `default-jre-headless` is a metapackage that
  says nothing about the Java major, so GraphHopper (17, from its pom and the
  jar's class-file major) planned cleanly on Ubuntu 22.04 and Pop!_OS 22.04 and
  failed at run time. A manifest names `requires_java`; the plan runs `java
  -version` once and defers a profile member, or refuses a typed unit, below the
  floor, stating the measured version and the archive's `openjdk-N-jre-headless`
  that would meet it. Nothing is fetched. BRouter is set to 11, its measured
  build target (the ruling said 17; the build file says 11).

- **Profile state in `list --json`** (#259). `list --json` profile entries gain `members`, `installed` and `installed_size_bytes` (apt members only, from one `dpkg-query` call), and the text table shows `installed N of M` with a size: engine prerequisite E1 of the hammunition-console design spec (branch console-spec).

- **Unverified repeater snapshots can sit on a Bunker; the bring-your-own-data
  principle is recorded; Canada's sources measured and not carried**
  (**D-078**, **D-074** amendment of 2026-10-03). `hammunition artifacts
  --json` lists the three on-request lists (ETCC, Brandmeister, hearham) as
  unit `repeater-snapshots`, check `unverified-fetch` (no digest, the
  publisher's URL, the size from a `HEAD`, the licence position), so a Bunker
  can hold them under `hold_unverified`; `maps repeaters fetch-etcc`,
  `fetch-brandmeister` and `fetch-hearham` read the station's mirror first at
  `<mirror>/repeater-snapshots/<name>` (`--no-mirror` skips it) and the
  publisher on any failure, still marked unverified. Operators bring their own
  export or key for questionable or personal data, the project never hosts it,
  and no licence letters are sent on its behalf. Canada's TAFL (no amateur
  rows, licensee names and addresses) and ISED's call-sign file (names and
  addresses) are not carried.

- **A GitHub wiki generated from `docs/`** (`wiki-mirror`). `scripts/gen_wiki.py` writes one flat wiki page per nav document and package page, a Home banner, a sidebar from the nav, a footer naming the source commit, and `Software-by-activity` from the menu vocabulary (D-055); links to project records the site excludes point at GitHub. `.github/workflows/wiki.yml` publishes it on every push to `main`, and fails naming the one-time first-page step while the wiki has none. `tests/test_wiki.py` covers it.

- **A repeat plan no longer asks publishers about data it already installed**
  (#197, **D-049** amended 2026-10-02). A terrain or 3DEP tile, US Topo or
  FSTopo sheet, Kiwix book or CoMaps map that the transaction log attributes
  as installed is planned as "installed, not re-checked (attributed DATE)" and
  makes no request while the attribution is under seven days old
  (`RECHECK_AFTER_DAYS`); one older than that, or whose file is not the one the
  log recorded, is asked again, and a re-check that fails is a note, never a
  refusal. The plan counts what it skipped and names the oldest attribution;
  `install --json` gains `publisher_checks` (`checked`, `reason`,
  `attributed`). New `install --recheck` asks every publisher regardless. An
  item on disk the log does not attribute behaves as before. `install-data`
  log entries now carry `size` (and `digest` for books and maps).

- **The plan groups repeated same-shape steps; `--dry-run --full` expands them**
  (**D-016** amendment, 2026-10-02). A unit that repeats one step per sheet,
  tile or book (`ustopo-qmapshack`, contours, SPLAT, terrain tiles, FSTopo,
  Kiwix, the vector-tile builds) printed hundreds of near-identical blocks. The
  text now prints the template once with `<placeholders>`, the first item in
  full, every item's own values and the totals; a group is kept only if it
  rebuilds every step exactly. `--full` prints every step as before; the JSON
  document, the transaction log and the real run are unchanged.

- **Plan and run steps now have stable 1-based indices** (#244): JSON plans carry
  each step's index and the transaction's `step_count`; grouped text shows the
  covered range; real-run progress and run logs identify each step as it starts.

- **A failed install keeps the units that finished** (#272). Each unit's
  completion is written to the transaction log as a `unit_end` when its last
  step exits 0, inside the transaction, so a failure in a later unit no longer
  forgets the earlier ones: the run still ends `transaction_failed`, `status`
  reports the finished units `completed` (new `completed_in_failed_run` on each
  recorded unit in `status --json`), and a rerun of the same request plans the
  failing unit and those after it. A built unit is skipped only after the D-051
  check, the declared files on disk and the recording at the manifest's current
  pin; map units that share a ledger are not recorded per unit. Documented in
  `docs/reference/transaction-log.md` and `docs/reference/cli.md`.

- **`station set --clear-rig` refuses rig-setting flags instead of silently
  discarding them** (#217, issue #206, **D-073**). `--clear-rig` with `--rig`,
  `--rig-baud` or any other rig-setting flag exits with an error saying to
  clear first and set in a second command; clear-only behaviour is unchanged
  and the saved station values are preserved.

- **Switching rigs resets the baud and PTT line that belonged to the old
  radio** (#218, issue #207, **D-073**). `station set --rig` naming a different
  rig starts baud and PTT line unset, so a setting that does not apply to the
  new rig no longer makes `station set` reject the command; values given in the
  same command are applied, the rig device and owner carry over, and
  re-selecting the saved rig, or changing baud without `--rig`, behaves as
  before.

- **`hardware unapply` reverses an owned unattended linger even when no other
  hardware artefact is recorded** (#219, issue #208, **D-073**). The recorded uid
  is resolved before the no-op checks and an owned linger record counts as
  pending work, so the plan includes `loginctl disable-linger` and removal of
  `/etc/hammunition/linger.yaml`; with no owned record nothing changes.

- **FCC ASR construction-date field deliberately not read** (#221, issue #210,
  **D-075**). The unused `_RA_BUILT` constant is removed and the field map says
  field 12 is intentionally not read; a test requires every `_RA_*` constant to
  be loaded.

- **NWS transmitter lists ignore trailing JavaScript after their data array**
  (#222, issue #211, **D-075**). `parse_nwr` decodes from the `cclData` opening
  bracket with `JSONDecoder.raw_decode` instead of ending at the file's last
  `]`; invalid or non-list data still errors as before.

- **Tests cover boxless map-region extracts in `maps infra import`** (#223,
  issue #212, **D-075**). No code change: a CLI test for text and one for
  `--json` assert that an installed extract with no bounding box is left out
  with a numbered note, the usable extracts still import, and neither extract's
  name appears in the output.

- **Books and CoMaps disk checks include the same run's vector tiles and route
  graphs** (#224, issue #213, **D-076**). Tile and graph
  needs join the per-path map-needs total, including when either is the only
  pending work, so a plan that would exceed free disk is refused with the
  combined figures.

- **Terrain tile HEAD answers are cached for six hours** (#214, **D-061**). A repeated install plan reuses successful publisher answers; failed and non-200 checks are asked again. The cache sits below #261's attribution rule and #260's bound: only a candidate they select is probed, and a cached answer counts as a check in `publisher_checks` (its reason says it came from the cache).

- **TCP listener inspection shared by the CLI and map-server test** (#227,
  issue #216). New `hammunition.listening` (`listening_addresses`, `LOOPBACK`,
  `bound_to_loopback_only`) reads `/proc/net/tcp` and `tcp6` once for both, skips
  missing tables and malformed or non-listening rows, and returns `None` when a
  table cannot be read.

- **SPLAT Signal-Server links are repaired without rebuilding terrain when
  only a link is missing or wrong** (#220, issue #209, **D-061**). The converter
  separates `pending()` (data and sidecar faults, which still convert) from
  `links_to_repair()`; each bad link gets its own `install-data` action,
  keeping uninstall attribution.

- **gps-tether 0.1.1: refusals exit 3, the unit retries crashes only** (**D-073**
  amendment, the maintainer's ruling). hammunition-gps-tether v0.1.1 exits 3 on
  a refusal (a taken port or socket, root, an unusable option) and 1 on an
  uncaught crash; the `gps-tether` unit is re-pinned to it and carries
  `RestartPreventExitStatus=3`, so systemd stops retrying refusals and keeps
  retrying crashes. `hammunition maps gps-tether` passes the tether's exit code
  through.

- **US Topo is bounded: a radius around your grid square by default,
  `--topo-regions`, `--topo-all`, and a typed `yes` to a size above 10 GB**
  (**D-068** amendment, 2026-10-02, #232). Every sheet of three whole-state
  regions was 7,284 sheets, 55 GB of download and 111 GB of disk, and the
  `navigation` profile could not leave them out. `station set
  --topo-radius-km N` (100 when unset, 0 for none), `--topo-regions a,b` (a
  subset of `--map-regions`) and `--topo-all` / `--no-topo-all` choose; FSTopo
  and 3DEP follow, Copernicus terrain does not. The install prints a `note:`
  line before the plan saying what was chosen and how to change it. With
  `--topo-all`, or more than 10 GB, the plan prints the count, download and
  disk in one sentence and asks you to type `yes`, which `--yes` does not
  answer. With no grid square the unit defers by name and keeps what is
  installed.

- **A publisher outage at plan time retries, then defers by name instead of
  refusing the install** (#200, **D-039** amended 2026-10-02). `install
  navigation --dry-run` refused whole over a Geofabrik outline answering 502, one
  timing out and one US Topo sheet answering 503, none of which was a fault in
  the plan. Every plan-time probe (terrain, 3DEP, US Topo, FSTopo, Kiwix, CoMaps
  and the outlines they share) now retries an HTTP 5xx or 429, a connection
  error and a read timeout three times, waiting 1 s, 3 s, 9 s, one stderr line
  per retry; any other 4xx is final. A profile member's items a publisher still
  does not answer for are deferred by name with its last answer quoted (printed
  under "Will NOT happen", in `--json` `deferrals`, in the log and `status`), a
  dead outline defers that region in every unit that needs it and no other, and
  a line at the foot says to run the command again; a unit you typed refuses,
  saying the publisher is not answering rather than naming the stale-index
  remedy, which a 404 keeps. A deferred sheet leaves its older edition
  installed, and a deferred book or map suspends the unit's removals for the
  run. New `hammunition.retry`; tests `tests/test_retry.py`,
  `tests/test_plan_retry.py`.

- CI's `tests` job now checks out `hammunition-gps-tether` at the tag `catalog/packages/gps-tether.yaml` pins, and `tests/test_tether_contract.py` fails instead of skipping in CI when the source is absent, so the shared-constants drift check actually runs (#215; replaces the empty #226).

- **Fix: the weekly ref check reported three correct pins as missing; changed
  pins are now verified per pull request** (**D-024**, **D-031**; affects the
  report on v0.19.0, not its installs). `check_pin_reviews.py --verify-refs`
  compared the tag object's id (what `FETCH_HEAD` is after fetching an
  *annotated* tag) with the pinned commit, so `librevna` v1.6.5, `nrsc5` v3.2.0
  and `pihpsdr` v3.0 failed it although each pin is exactly the commit the tag
  peels to. The install checks out `FETCH_HEAD` and compares `rev-parse HEAD`,
  the commit, so it was never affected; the check now peels with `^{commit}`
  and a test with an annotated tag proves it (red before, green after). It also
  now rejects a pin that is the tag object's id. New `--only` flag (needs `--verify-refs`, skips the calendar review) and a
  pull-request job, `changed git pins resolve upstream`, run the check for the
  manifests a diff changed. No manifest changed.

- **The tether call-through test no longer finds a tether installed on the
  host** (#230). `test_the_installed_program_is_really_run_and_its_exit_follows`
  ran the real `hammunition-gps-tether` tree when `hammunition install
  gps-tether` had been run on the machine, and failed under `unshare -r`; it
  now points the engine's `TETHER_TREE` at an empty place, so only its stand-in
  program on the PATH is found.

- **Transient apt archive failures are retried before a step fails** (#236; **D-016**, **D-044**). `apt-get update`, installs and simulations now use apt's three-retry setting. If a fetch still fails, install output names the option and advises rerunning the same command; apt reuses cached downloads. `--no-remove`, noninteractive apt and the refresh opt-out are unchanged.

- **`update` reports retired installed units instead of failing to plan** (#239).
  Text and JSON identify each retired unit and include its catalog reason.

- **The GraphHopper startup test no longer races for a free port** (#262). It
  keeps a loopback socket bound without listening while requesting a route, so
  the tested connection-refused case cannot be claimed by another process.

- A tarball or zip data artifact under a root-owned prefix is now unpacked into the operator's build directory and installed through the tree steps the plan prints (`rm -rf`, `install -d`, `cp -aT`, the D-043 `chown -R -h` hand-over, and removal of the staged copy), instead of being written to the prefix by the unprivileged process; `hammunition install vector-map-kit` no longer fails with `Permission denied` at its last step (#271).

- **Issue #123:** allow four-character callsign suffixes so the documented placeholder passes station validation; add a regression test.

- #245: expose doctor command fixes as argv in JSON and render them as code in text output.

- **A git build no longer stops in a text editor on a machine whose git signs
  tags.** (#238, **D-031**) `tag.gpgsign true` in the operator's `~/.gitconfig` turned the git
  backend's plain `git tag -f` into an annotated, signed tag, and nano opened
  inside a navigation install on the bench (2026-10-03). Every git step now
  runs with `GIT_TERMINAL_PROMPT=0` and `GIT_EDITOR=true`, and the tag step
  disables `tag.gpgSign` and `tag.forceSignAnnotated` for that one command;
  the operator's configuration is never changed. Two tests hold it, and
  `docs/troubleshooting/install-failures.md#git-editor` names the symptom.

- **The engine's own GPS tether copy retired; the ACMA Bunker ruling recorded**
  (**D-071** note, **D-074**, 2026-10-02). `gps_tether.py` and its tests are
  deleted: the tether is hammunition-gps-tether, installed by the `gps-tether`
  unit. `hammunition maps gps-tether` runs the installed program and, absent,
  refuses naming `hammunition install gps-tether`. `tether_contract.py` holds
  the ports and the four GeoClue constants shared with the tether, asserted
  equal to its source by `tests/test_tether_contract.py` (skipped where that
  source is absent). `reference serve` checks `--position-port` itself. The
  maintainer's ruling that a Bunker may hold the ACMA register zip (it contains
  `client.csv`, never opened by the engine) is recorded, with Bunker's
  `hold_unverified = false` as the opt-out.

- **Docs sweep after the 2026-10-02 batch** (#205; documentation only, no code). A
  guide for the tray's Controls panel and `hammunition services`
  (`docs/guides/tray-controls.md`); contradictions between pages resolved in
  favour of the code, the bench record and `docs/DECISIONS.md` (park and wake
  on hardware, the helper's lists, the rig's default owner, the tether as a
  service, desktop tray versions); the README status table gains the rows
  v0.19.0 left out; the nav and entry pages reach every guide; and
  `docs/contributing/docs-sweep.md` is the checklist, now a release step.

- **Document the unit JSON command** (#246). The unit manifest was already
  emitted by `hammunition show UNIT --json`; the JSON command list and CLI
  reference now name it and explain profile-first resolution.

- **Docs: bench session 13 on the field laptop** (#233, **D-056**, **D-058**). `docs/reference/bench-verification-5430.md` records the tray
  0.5.0 helper installed and answering contract 1, `services` and `doctor`
  after it, the tether enabled at login, a 3D fix from a cold boot and the
  whole-`navigation` dry run, with what is still owed; the tray-controls,
  gps-time and offline-navigation guides move their measured lines only where
  this session measured them.

- **Documentation for newcomers.** (#256) A new page, `docs/getting-started/disk-space.md`, says how much disk a profile, the whole catalog and each offline data layer need, with three tiers (about 5 GB, about 55 GB, about 220 to 300 GB) and each figure marked measured, pinned, quoted or unmeasured; the README opens with what it is, who it is for, targets, disk space, a five-command quick start and where to go next, ahead of the status table; the home page routes by situation; the getting-started pages link back and forward in order; the `status` sample and the backend count are corrected.

- **Docs: a verbose installation guide and newcomer-ready profile pages** (#276). A verbose installation guide (`docs/getting-started/installation.md`, linked from the short `install.md`) and every profile laid out for newcomers. The profiles index is now a full orientation table (stage, units, disk, hardware assumed, consent gates, what each leaves out) with a "which profile do I want" table by goal, and each profile page gains who it is for, an at-a-glance table, install and uninstall steps and a first-ten-minutes list. The new prose lives in the profile manifests (`who_for`, `hardware_assumed`, `footprint_short`, `excludes_short`, `goals`, `first_ten_minutes`, all optional in the schema and required of every shipped profile by `tests/test_profile_docs.py`), so the pages stay generated. The nav now leads with Installation, Profiles, Guides and Troubleshooting.
- #242: Add the local-only `station set --json` result with saved values and per-flag refusals (D-059).
- CI starts again: the GYST `python-ci` caller job grants `id-token: write`, which the called workflow declares; without it GitHub refused the workflow file at startup and no `CI green` check ever appeared (#281 follow-up).
- **Resume completed regional and derived work safely** (#279, completing
  #272). Regional, derived, DEM, topo and CoMaps units now record completion
  and resume only when their state fingerprint and on-disk checks still match.
  Completion records on successful runs follow effect verification.
- CI calls ChiefGyk3D/git-your-ship-together v1.7.1 for ruff, `mypy --strict`, the pytest matrix (3.11 to 3.14) and workflow lint (`ci / CI green`), adds a `security.yml` caller (CodeQL, gitleaks, Scorecard, pip-audit) and a Dependabot entry for the pins; `pages.yml` and `wiki.yml` become callers of its `docs-pages.yml` and `wiki-publish.yml`; the per-distribution containers, commit claims, pin and citation checks, link check and repo hygiene stay local; the tether checkout moves into `scripts/ci-test.sh`; required checks are listed in `docs/contributing/ci.md`.
- A release workflow: a `v*` tag builds the documentation site and a source archive through GYST's `artifact-release`, checks the tag against `pyproject.toml`, takes the notes from that version's changelog section, signs and publishes them; the site tarball is what the coming `hammunition-docs` offline unit pins.
- **The failed apt-fetch regression test reads the transaction log from the engine's resolved state path** (#268). It pins the run to root so `XDG_STATE_HOME` is honored under `unshare -r`.
- Correct the station-settings guide with a generated, drift-checked table of every manifest's configuration files, station values and per-file deferrals; distinguish chrony's fixed drop-ins from ntpsec's helper-managed files (#277, D-035, D-058, D-072).
- Add typed consent for planned file-capability grants, with exact-value scripted affirmation and uninstall reversal for #96; declining keeps LinBPQ unprivileged and installs the rest.
- Isolate the real-catalog `list` test from the caller's transaction log and assert the empty-state path does not run `dpkg-query` (fixes #265).
- **Mesh and Reticulum inventory** (documentation only, Track C, issue #105).
  `docs/reference/mesh-inventory.md` measures the Reticulum, Meshtastic and MeshCore
  tools on 2026-10-03: versions, licences (the non-OSI Reticulum License, Sideband
  and LXST under Creative Commons non-commercial terms), hash-pinned closures
  (`docs/reference/mesh-venv-closures.txt`), venv sizes, arm64 wheel gaps, archive
  presence per target, and the `meshtasticd` repository keys for D-040.

## v0.19.0 — 2026-10-02 — the rig as station data, repeater sources, infrastructure layers, GraphHopper, terrain for coverage plots, the tether as its own project, device and service control for the tray

Twenty pull requests since v0.18.0 (#176, #178-#196), 20 entries.

- **The rig is station data: one shared `rigctld` for every program**
  (**D-073**, status proposed, bench owed). A `rig` hardware class and a
  `rig` block on a radio's manifest describe the radio — a hamlib model and
  CAT port, or a `ptt_only` shape for a radio with no CAT — and five station
  values (`rig`, `rig_device`, `rig_baud`, `rig_ptt_line`, `rig_owner`),
  checked against the catalog and this machine's hamlib as you set them, say
  which radio is on the station. A new `rig-service` unit in the `station`
  profile carries a `user_services` manifest block: the engine renders one
  `rigctld` as a systemd **user** service bound to `127.0.0.1:4532`, enables
  it, discloses the unit file and the no-password warning in the plan, and
  removes only its own file on uninstall. A missing value defers the service
  and the rest installs (D-035); `rig_owner: flrig` and VOX skip it. The
  FT-991A (CAT) and the UV-50PRO (PTT-only) ship `untested`. Opt-in
  `hammunition station set --unattended` keeps user services running after
  logout through a new `hammunition-devctl linger` verb behind the existing
  polkit action. A browser-producible HTTP POST was measured to key hamlib's
  dummy model over loopback, so a small loopback filter (`hammunition.rigproxy`)
  sits in front: `rigctld` binds `127.0.0.1:4632` and the filter binds the port
  programs use, 4532, dropping any connection that opens with an HTTP request
  line; a regression test guards it. `doctor` checks both services read-only and
  never keys the transmitter. gpredict's radio file is written for the shared
  `rigctld`; `docs/guides/rig-control.md` is rewritten around the service.

- **Repeater data beyond RepeaterBook, one layer per source** (D-074).
  `open-repeater` is a new data unit: Open Repeater's CC0 list (241 kB,
  461 repeaters, none yet in the US), pinned by sha256 in its manifest by
  `scripts/gen_open-repeater-pin.py`, checked weekly, mirrorable through a
  Bunker. `hammunition maps repeaters import` gains
  `--from-open-repeater [FILE]`, `--from-osm` (the repeaters tagged in the
  region extracts already installed, filtered by osmium, nothing
  downloaded) and `--from-direwolf-log FILE...` (the APRS repeater objects
  your station heard, its own layer, never merged). `fetch-etcc` (the
  UK's RSGB list) and `fetch-brandmeister` (DMR repeaters only; every
  hotspot dropped before anything is written) fetch on request and are
  marked unverified. Each source is its own layer; with two or more,
  `repeaters-all.gpx` joins them by a stated precedence and counts every
  join. `maps repeaters remove --layer ID` removes one. The `repeaters`
  and `repeaters-removed` documents gain `layer_id`, `layers` and
  `all_sources`. RepeaterBook bulk, RadioReference, RFinder, the ARRL
  directory, FCC ULS, RadioID, the WIA list, repeatermap.de and the D-STAR,
  YSF and NXDN lists are documented as not carried; ACMA's register is
  named as a later data unit.

- **Australia's repeaters from the regulator** (D-074, amended
  2026-10-01). `acma-register` installs the ACMA's Register of
  Radiocommunications Licences, one 67.5 MB zip rebuilt daily, through a
  new `register` install method. The ACMA publishes no checksum (its ETag
  is a storage version stamp, not a digest), so nothing is pinned: the
  zip's own CRC-32s and the tables the import reads are checked, the plan
  says **unverified**, and the unit is in no profile. `hammunition maps
  repeaters import --from-acma` writes the `acma` layer, *Repeaters (ACMA,
  YYYY-MM-DD)*, from the granted amateur repeater licences whose site lies
  inside an installed map region's bounding box, with the attribution the
  licence requires; it never opens the licensees' table. The regulator
  comes second in the all-sources precedence, after your own export.
  `hammunition artifacts` lists it for a Bunker as `unverified-zip`, with
  the day's size and no digest.

- **Infrastructure and EMCOMM layers on the maps** (D-075). `hammunition
  maps infra import --from-osm` filters the installed region extracts with
  osmium into eight layers (medical, responders, supply, shelter
  candidates, which say "candidate, not a designated shelter", transport,
  power, telecom, water), each a GPX, a QMapShack POI collection, a Navit
  map and a GeoJSON in `~/.local/share/hammunition/overlays/infra/`;
  nothing is downloaded. Three new data units in no profile:
  `faa-nasr-airports` (FAA NASR cycle 2026-10-01), `eia-860m` (August 2026)
  and `wri-power-plants` (v1.3.0, outside the US only), read by
  `--from-nasr`, `--from-eia` and `--from-wri` and kept to the regions'
  boxes; their pins are written by `scripts/gen_nasr_pin.py`,
  `scripts/gen_eia860m_pin.py` (with `--follow-move` for EIA's monthly
  archive move) and `scripts/gen_wri_pin.py`, all three in the weekly pin
  review. `fetch-fcc-asr` (only the registration and coordinate records;
  the owners' contact file is never opened) and `fetch-nwr` (the live
  status dropped) fetch on request, unverified. `maps infra remove
  [--layer ID]`; documents `infra` and `infra-removed`. The browser map's
  tiles gain an `infra` layer (the converter is `tilemaker-pmtiles 2`, so
  each region is rebuilt once), power coloured by Open Infrastructure Map's
  voltage ramp under its BSD-3-Clause notice, and every infra layer is a
  toggled overlay with its licence in the credit. The operator's Navit copy
  and QMapShack's `poiPaths` now carry repeater and infrastructure layers
  together.

- **Routes on the browser map: GraphHopper** (D-076). Two units, installed
  by name only and in no profile: `graphhopper`, Maven Central's
  `graphhopper-web-11.1.jar` pinned by the sha256 measured equal to
  Central's own (its PGP signature recorded, not verified), installed as a
  tree of one file now that a binary `executable` block may install as a
  tree; and `graphhopper-graph`, a new `graphhopper-import` converter that
  merges the station's regions with `osmium` and builds one graph with car,
  bike, foot and hike profiles as the operator, the plan disclosing 3.7x
  the downloads on disk and 1.2 GB of memory from one measured region.
  `hammunition reference serve` starts GraphHopper on a loopback port the
  system chooses, through links to the read-only graph in the operator's
  cache, and answers `/map/route` itself after its Host rule, rebuilding
  the request; a GraphHopper that exits is reported once and the page keeps
  serving. The map gains *Route for*, *Route* and *Clear*, starting from the
  tether's position, and `#route=` in the address. `update` names the graph
  in its rebuild command. Measured on the development host with synthetic
  regions; a real region and a desktop browser are owed by the bench.

- **SPLAT! and Signal-Server terrain from the station's elevation**
  (D-061, amended 2026-10-02; the gap report's A5). `splat-sdf` makes
  SPLAT Data Files for every square the map regions touch, at one and three
  arc seconds, from the Copernicus tiles or from 3DEP when the station chose
  it, with SPLAT's own `srtm2sdf` tools run as `-n -32767` (by default they
  replace every elevation below zero) and kept bzip2-compressed, which both
  readers read; a link beside each file carries Signal-Server's name.
  `signal-server` is W3AXL's fork at 7f6242a: Cloud-RF's repository now
  holds only a history README. It is built with the release flags without
  `NDEBUG`, because its ITWOM arrays are allocated inside `assert()`, and the
  guide's command passes `-nothreads`, because the threaded plot crashed 2
  times in 10. Both join `antenna`, where `splat` is. `hammunition maps
  splat` writes `~/.splat_path` when there is none. The plan's Terrain block
  says what is made, in text and JSON. CMake builds now honour
  `project_file` as the source subdirectory, as the schema always said.
  Measured on one Copernicus tile and in a container; not yet run through
  an install on a real region.
- **The plan says what it is waiting on, and asks four things at a time**
  (#197, **D-061** amended 2026-10-02). A dry run that checks hundreds of terrain
  tiles against a publisher printed nothing for minutes. Terrain and 3DEP
  tiles, US Topo and FSTopo sheets, Kiwix books, CoMaps maps and the map
  regions now announce the check on stderr (`checking N … (needs the network)…`),
  count on one line and close with the elapsed time, only on a terminal or with
  `HAMMUNITION_PROGRESS=1`; stdout and `--json` are unchanged. The tile, sheet,
  book and map checks run four at a time (`hammunition.progress`), results in
  order, errors per item as before (50 fake tiles at 0.2 s: 10.0 s down to
  2.6 s). Not done: consulting the log's attribution for an installed tile (one on
  disk was never asked about), and an ETag cache.
- **The tray units re-pinned to hammunition-tray v0.5.0, and they install its
  device helper** (**D-056** amended 2026-10-02, later). The release publishes
  no `.deb` yet, so both units pin the tag's archive (sha256 `614148fb…`,
  measured twice) and the engine places its files: the Plasma applet, the Qt
  tray, and the helper (`devctl_helper`: code copied to
  `/usr/local/lib/hammunition-devctl`, the tray's wrapper with the engine's venv
  interpreter, the polkit action), all printed in the plan, read back with
  `--version`, removed by `uninstall` on the log's say-so and only when no other
  tray unit still needs the helper. A helper another installer owns (a `.deb`,
  the tray's `install.sh`) is left alone and the plan names the owner, an earlier
  run of this engine's is refreshed, and a file a package owns is never written
  over or removed; an interpreter tree any account can write refuses, one only its
  owner can write asks a `yes` that `--yes` does not answer. New manifest
  fields `placements`, `placement_dirs` and `devctl_helper` on a `binary`
  archive, with an allow-list of destinations; `depends` carries what the
  `.deb`'s Depends line did.

- **User services generalised; the GPS tether installed from its own project
  as one** (**D-073** amended 2026-10-02, **D-071** note). A `user_services`
  entry with no station condition, no station value and no device is *plain*:
  always planned, needing neither the station nor the hardware catalog. The
  unit file's header names the catalog unit that wrote it, several units can
  share a plan, and `restart` / `restart_sec` are fixed-set manifest fields;
  the rig's unit renders byte for byte as before; `restart_prevent_exit_status`
  keeps systemd from retrying a refusal. A new `gps-tether` unit (in
  `navigation`) installs the project's v0.1.0 source tree, pinned by tag,
  commit and tarball sha256, as `hammunition-gps-tether` (now its own
  repository), and runs it as `hammunition-gps-tether.service` on 127.0.0.1
  ports 10110 and 10111, enabled at install and started at next login (a
  reinstall `try-restart`s a running one). A venv requirement
  with the all-zero placeholder digest is refused by name at plan time. `hammunition maps gps-tether` runs
  the installed tether when there is one (and says so) and its own copy, with a
  deprecation note, otherwise. The service and a foreground run cannot share
  port 10110. Installing any `user_services` unit also writes its row to
  `~/.config/hammunition/devctl-services.yaml` (0600), the list the tray's
  helper reads for its Services group; uninstall removes it. Not yet run on a
  machine.

- **WWAN modem, Bluetooth and camera as controllable devices** (**D-056**,
  amended 2026-10-02). Three hardware classes, `wwan-modem`,
  `bluetooth-controller` and `camera`, each `power_control:
  usb_deauthorize`, and four entries for the field laptop's own hardware read
  with `lsusb`, `udevadm` and sysfs only: `dell-dw5821e` (`413c:81d7`),
  `intel-ax210-bluetooth` (`8087:0032`), `sunplus-integrated-webcam-fhd`
  (`1bcf:2a03`) and `dell-dw5930e`, the PCIe/MHI 5G card, carried as the
  documented gap (`pci_runtime` stays refused; the radio switch is the
  route). No park has been run on any of them, so none is
  maintainer-verified. Catalog is now 31 devices, 9 classes.

- **The device helper moves to hammunition-tray: the engine exports its lists
  and gains `hammunition services`** (**D-056**, amended 2026-10-02). `hardware
  apply` now writes `/etc/hammunition/devctl-devices.yaml` (every catalogued
  device with a `power_control` block, with what `state` needs to match it on
  the bus) and `/etc/hammunition/devctl-services.yaml` (`gpsd`, `time` by
  whichever of ntpsec or chrony the machine has, `gps-resume`): root-owned,
  disclosed whole in the plan, logged as `devctl_export`, read back, and
  removed by `unapply` only when they carry Hammunition's header. Where the
  installed helper answers `--version` it is the tray's, and `apply` writes
  neither its wrapper nor an existing polkit action while `unapply` leaves
  both. `hammunition services [--json]` (document kind `services`) lists the
  helper's services and `services start|stop|enable|disable NAME` changes one,
  by asking the installed helper, through `pkexec` for a system service and
  never through `systemctl` from the engine. `hammunition-tray` and
  `hammunition-tray-qt` write neither (a test holds the absence; the tray's own
  release installs them, and the re-pin adds its `.deb`), and the engine's own
  polkit action now carries the tray's wording. Shapes: `docs/reference/devctl-lists.md`. Not measured: the
  tray's helper reading the lists, and `apply` and `services` on a machine.

- **`hammunition-tray` and `hammunition-tray-qt` re-pinned to v0.4.0**
  (GPS-time plan Task 11, D-058). Both catalog manifests moved from v0.3.0
  to the v0.4.0 release assets, measured against the published SHA256SUMS:
  `hammunition-tray_0.4.0_all.deb` (15270 bytes,
  `451bea9322376dbb9cd00834834f96e0f5d5ce487735d5fbe2349e2ae41e97bd`) and
  `hammunition-tray-qt_0.4.0_all.deb` (18198 bytes,
  `21ccc91e2f8a46a5213c9200fc0f33661d2075bfaee360cc50b0158981a46527`); both
  Depends lines are unchanged from 0.3.0. 0.4.0 adds the Time section to
  both trays: what the clock follows and the four GPS-time modes, read
  without a password and changed through one polkit prompt to
  `hammunition-devctl`, needing Hammunition 0.18.0 or later. Both
  `what_it_does` fields gain a sentence for it, and the two places that had
  written "its 0.3.0" for the still-unreleased Time section
  (`docs/guides/gps-time.md` §5, D-058's "The tray" paragraph) are corrected
  to 0.4.0.

- **The tray units re-pinned to hammunition-tray v0.5.0, and they install its
  device helper** (**D-056** amended 2026-10-02, later). The release publishes
  no `.deb` yet, so both units pin the tag's archive (sha256 `614148fb…`,
  measured twice) and the engine places its files: the Plasma applet, the Qt
  tray, and the helper (`devctl_helper`: code copied to
  `/usr/local/lib/hammunition-devctl`, the tray's wrapper with the engine's venv
  interpreter, the polkit action), all printed in the plan, read back with
  `--version`, removed by `uninstall` on the log's say-so and only when no other
  tray unit still needs the helper. A helper another installer owns (a `.deb`,
  the tray's `install.sh`) is left alone and the plan names the owner, an earlier
  run of this engine's is refreshed, and a file a package owns is never written
  over or removed; an interpreter tree any account can write refuses, one only its
  owner can write asks a `yes` that `--yes` does not answer. New manifest
  fields `placements`, `placement_dirs` and `devctl_helper` on a `binary`
  archive, with an allow-list of destinations; `depends` carries what the
  `.deb`'s Depends line did.

- **piHPSDR and DroidStar are carried; FreeDV 2.x is not yet** (gap
  analysis D.10, first batch; Q-022 says these need a D-032 check and a
  measured build, not a ruling). `pihpsdr` is the operating program for
  OpenHPSDR transceivers (ANAN, Hermes Lite 2), a `make` build of DL1YCF's
  tag v3.0 in the `sdr` profile beside Quisk. `droidstar` is a reflector
  client for M17, DMR, D-STAR, Fusion, P25 and NXDN with its vocoders
  compiled in, an own-choice commit pin in `digital-modes` beside `qtel`, on
  Debian 13, Parrot, Kali and Ubuntu 26.04 (Ubuntu 24.04 and Mint have Qt
  6.4, below its floor); a two-line patch turns off its ARM-only MD-380
  firmware vocoder, and only its executable is installed because its
  install rule deploys Qt into the prefix. Both transmit when keyed, and
  their pages and profiles say so. FreeDV 2.4.0 builds, but fetches RADE's
  C port and RNNoise from `main` while building: `source-build-gaps.md` #9,
  a row in `not-carried.md` (whose generator now validates the
  gap-analysis "Not added" rows too), and a note on `freedv`, which is
  1.8.11 without RADE on every target.

- **dump978-fa, nrsc5, LibreVNA, k5prog and radio_tool are carried;
  tar1090 is not yet** (gap analysis D.10, second batch). `dump978-fa`
  decodes 978 MHz UAT, the second ADS-B link, beside `readsb` in
  `listening`, which readsb can take as a second input. `nrsc5` receives HD
  Radio in `listening`; its build's FAAD2 download is made sha256-checked
  by a two-line patch (`source-build-gaps.md` #10). `librevna` is the
  program for the open-hardware LibreVNA in `electronics`, with a device
  entry that is not owned; its SCPI server listens on every address by
  default, and its page says so. `k5prog` (Quansheng UV-K5) and
  `radio-tool` (OpenRTX's Linux flasher for TYT, Baofeng and Radioddity
  DMR radios) write firmware to radios: ungated by D-026, in no profile,
  and their pages say what they write. radio_tool is pinned past its last
  tag, which cannot flash. tar1090's installer was read and not run; its
  page works from a static server on loopback over readsb's JSON, so its
  route is a `reference serve` page, which is engine work (`not-carried.md`).
  Every build was run in rootless containers; no radio, dongle or VNA was.

- **The GPS receiver gets a resume step** (issue #177, D-058 amended
  2026-10-01). Measured on the field laptop: a USB receiver is not
  re-enumerated across a suspend, so gpsd can keep a tty that has gone quiet
  and the fix does not come back. Where gpsd is installed,
  `hammunition hardware apply` now installs
  `/usr/local/libexec/hammunition-gps-resume` and
  `hammunition-gps-resume.service`, a oneshot that runs after every suspend
  or hibernation. It does nothing when no `/dev/gpsN` exists, so a parked
  receiver stays parked. Otherwise it runs `gpsdctl remove` and `add` for
  each receiver, and `systemctl try-restart gpsd.service` if gpsd then
  reports no device, logging one journal line per action. It makes no
  check that data flows, so a receiver gpsd still lists but that stays
  silent is left to the manual steps: a gpsd restart, then park and wake.
  Both files are printed in the plan, read back after the run and removed
  by `hardware unapply`. `--no-gps-resume` opts
  out, and `doctor` reports the step when a receiver is attached. The
  catalog names the step (`resume: {step: gpsd_reopen}` on the
  `gps-receiver` class), and that class's page gains "After suspend". It
  has not yet run on the field laptop; the bench steps are on the issue.
  After merge: `hammunition hardware apply`.

- **CoMaps can show "you are here", through GeoClue** (D-069, amended
  2026-10-01). `hammunition maps gps-tether` gains `--nmea-socket PATH`: a
  unix socket, mode 0660 in GeoClue's group, fed by the same fan-out as TCP
  10110; it is on by default once GeoClue is set up, and `--no-nmea-socket`
  turns it off. `hammunition hardware apply` writes
  `/etc/geoclue/conf.d/90-hammunition-gps.conf` (GeoClue's network-NMEA
  source reads that socket) and `/etc/tmpfiles.d/hammunition-gps.conf`
  (`/run/hammunition-gps`, 2750, setgid `geoclue`), with every file, the
  inspect and reverse steps and four disclosures in the plan; GeoClue's own
  beacondb and GeoIP lookups are not changed, and the plan says so.
  `--no-geoclue` skips it, and `hardware unapply` removes it by content.
  `doctor` reports the files, the directory and whether GeoClue's demo
  agent is running. `comaps` depends on `geoclue-2.0` and
  `libqt6positioning6-plugins`. No `[app.comaps.comaps]` entry: a native
  CoMaps is a system app to GeoClue. Measured with Debian's GeoClue in a
  private namespace, not yet on a desktop; the guide's section 17 lists
  what the bench owes. After merge: `hammunition hardware apply`, then log
  out and back in. Where CoMaps is already built, `hammunition install
  comaps --dry-run` shows whether the two new packages are planned (not
  checked on a machine with CoMaps at its pin); if they are not,
  `sudo apt install geoclue-2.0 libqt6positioning6-plugins` adds them.

- **Forest Service FSTopo sheets and USGS 3DEP bare-earth elevation**
  (D-068, amended 2026-10-01). `usfs-fstopo` installs the FSTopo
  7.5-minute sheets, with trail numbers, for the station's regions over
  National Forest land, chosen offline from a carried index of 18,187 quads
  (`scripts/gen_fstopo_index.py`) and located through the raster gateway's
  one redirect at plan time. The Forest Service publishes no checksum: a
  sheet the maintainer pinned is checked by its sha256, and every other is
  fetched **unverified**, its size and TIFF header only, said on its plan
  line and counted in a warning; the pins file starts empty. So it is in
  no profile and installs only when typed by name, until every sheet a
  region needs is pinned and the plan says so. When FSTopo sheets are
  installed, `ustopo-qmapshack` expands each one's palette to tiled RGB
  with overviews and builds a second QMapShack map, `FSTopo.vrt`; it never
  pulls them in, and US Topo works alone. `dem-3dep`, in `navigation`,
  installs 3DEP 1/3-arc-second tiles, about ten times Copernicus's size,
  only after `hammunition station set --dem-source 3dep`, checked against
  each object's S3 ETag from a carried list of 1,449 tiles
  (`scripts/gen_3dep_tiles.py`); `dem-qmapshack` then draws its elevation
  and contours from 3DEP at 10,812 pixels a tile, and setting the source
  back removes the tiles and redraws from Copernicus. Copernicus stays the
  default and stays installed. The plan prints both, per region, in text
  and JSON. QMapShack drawing either is not yet measured.

- **Kiwix books come through the LAN mirror, and `artifacts` lists them**
  (issue #159, D-070 amended 2026-10-01, D-066). The books backend fetched
  from download.kiwix.org only, whatever the station's mirror; it now asks
  `<mirror>/kiwix-library/<book id>` first and Kiwix second, the same pinned
  sha256 and size checked either way, the plan line saying so and the log
  recording `source`, `fetched_from` and `mirror_failure` as a `data` fetch
  does. `hammunition artifacts --reference-books ID,ID` lists each book
  (`check: sha256`, its own licence line) from the carried pins, no station
  read; with none given `kiwix-library` is deferred as *no books selected*,
  and it is now among the default units. The `artifacts` document gains
  `reference_books`. Books are the largest data the catalog fetches, up to
  127 GB.

- **A generated launcher no longer takes a PATH binary's name** (issue
  #174). `~/.local/bin/rigctl`, libhamlib-utils' launcher, ran ahead of
  hamlib's `/usr/bin/rigctl`, ignored its arguments and opened the
  dummy-rig shell, so every guide's `rigctl -l | grep …` waited for Enter.
  Sixteen more catalog launchers had the same shape, and `gpa`'s
  `exec gpa` ran itself. All seventeen are renamed for what they do, titles
  kept (D-054): `rigctl-dummy`, `hackrf_info-check`, `rtl_test-tuner`,
  `yagiuda-input`, `axlisten-all`, `mheard-stations`, `bladeRF-cli-shell`,
  `cgps-fix`, `gpsctl-device`, `grgsm_livemon-window`,
  `osmocom_fft-spectrum`, `gr_satellites-list`, `nfc-list-check`,
  `st-info-probe`, `ubertooth-util-version`, `gpa-window` and
  `hamclock-next-window` (the last two gain titles). The schema refuses a
  launcher named like the bare command it runs or a binary its manifest
  installs; the generator refuses, at plan time and again when it writes, a
  name found on the PATH outside `~/.local/bin` or in the unit's apt file
  list. `hammunition menus apply` removes a generated launcher that shadows
  a PATH binary, with its menu entry, and writes the renamed one; `doctor`'s
  launchers check names one. After merge: `hammunition menus apply`.

- **`import hammunition.fetch` no longer raises a circular `ImportError`
  when it is the first `hammunition` import in the process** (issue #158).
  `src/hammunition/backends/__init__.py` eagerly imports every backend, six of
  which (`apt_repo`, `binary`, `data`, `git`, `node`, `regions`, `source`,
  `venv`) named `hammunition.fetch` symbols at module scope purely for type
  annotations or late-needed helpers; `hammunition.cli.main`'s own import
  order happened to prime `sys.modules` around it, which is why the engine's
  CLI never hit it while Hammunition Bunker's `enginelib.py` did. Those
  imports now move under `TYPE_CHECKING` (annotations, deferred by
  `from __future__ import annotations` anyway) or become local imports at
  their one or two call sites (`MirrorPath`, `fetch_disclosure`,
  `record_fetch`, `safe_name`, `operator_dir`, `remove_tree`,
  `UrllibTransport`) — never a bare `try`/`except ImportError`.
  `tests/test_import_isolation.py` imports every `hammunition` module alone
  in a fresh subprocess so this cannot come back silently.

- **`CHECKS` now names `sha1-publisher`** (**D-070**, **D-069**). The
  `artifacts` document's `check` field was already described as able to
  carry `sha1-publisher` — the SHA-1 and size `comaps-maps` reads from
  CoMaps' own map index — but the `CHECKS` tuple Hammunition Bunker imports
  as the enumeration of valid values never listed it, so a `comaps-maps`
  entry carried a value its own published contract did not name.

## v0.18.0 — 2026-10-01 — the 2026-09 gap analysis: guides, station config for the packet units, GPS time, chrony, Kismet, the HF modems, six apt units, three re-rulings

Ten pull requests since v0.17.0 (#149, #153, #124, #162, #165, #167, #168, #170, #171, #172), 7 entries.

- **Three re-rulings from the gap analysis** (Q-022 #5, report section
  A8). `ARDOPGUI` is superseded by `ardopcf`'s own web GUI (`-G 8514`),
  which the catalog's pin already serves; the packet profile and the
  Winlink guide say so, and that both of ardopcf's ports listen on every
  interface. **Morse Runner is carried natively** as `morse-runner`:
  the Community Edition (v1.86) does not build outside Windows, and the
  Lazarus port of 1.68 built, drew its window and made sound on Debian
  13, which is the second clause of the maintainer's 2026-08-25
  conditional. It installs by name, not on Kali, x86-64 only. `chattervox`
  is retired: no upstream since 2019, and under D-037's `--ignore-scripts`
  its serial layer cannot load (D-048 amended). Nothing awaits the
  maintainer in `dispositions.md` any more.

- **Kismet in `rf-security`** (Q-022 #4; **D-040**, amended). `kismet` is
  apt from the archive on Kali and Parrot, and from the Kismet project's own
  signed release repository on Debian 13 (`release/trixie`), Ubuntu 24.04
  and Mint 22.3 (`release/noble`), behind the D-040 fingerprint gate
  (`ADA09A0E9B80ACCCE8FE6BB65345B8BF43403B93`). Ubuntu 26.04 has no release
  tree and defers it. An `apt_repos` entry may now carry `when:`, so one
  manifest can name one repository per release. Adds the operator to the
  `kismet` group; discloses that Kali's and Parrot's kismet-core enables a
  root `kismet.service` and that the web interface listens on every
  interface by default. The CatSniffer V3's Kismet helpers are in no
  release yet. New page `docs/rf-security/kismet.md`; generated package
  pages now list a unit's third-party repositories and fingerprints.
- **GPS time where the daemon is not ntpsec: the `chrony` unit** (**D-072**,
  proposed; gap analysis §A4, Q-022 #3). `hammunition install chrony` writes
  `refclock SHM 0 refid GPS poll 2 delay 0.2` to
  `/etc/chrony/conf.d/hammunition-gps.conf` and a gpsd.service drop-in
  giving gpsd `-n`, without which gpsd publishes no time to anybody. Not in
  `station`: the three time daemons conflict, and the plan refuses to remove
  systemd-timesyncd or ntpsec, printing the `apt-get remove` for the operator
  instead. Root config writes now `mkdir -p` a missing directory as a
  printed step. **PyGPSClient** 1.7.7 is a hash-pinned venv unit for the
  `gps-gnss` tag. The *Time and position* guide routes by daemon (ntpsec to
  D-058), and troubleshooting gains *FT8 decodes nothing: check the clock*.
  Evidence: `docs/reference/time-daemons.md`.

- **GPS time** (D-058). `hammunition time` says what the clock follows (the
  network, the GPS, or holdover and for how long) without a password;
  `hammunition time mode auto|prefer-gps|ntp-only|gps-only` prints every
  write, then sets it through the one helper and polkit action. A parked
  receiver never feeds the clock. `hardware apply` installs the two grants
  ntpd needs to read gpsd's time, disclosed as widening a network-facing
  daemon's privilege, only where gpsd is installed, and prints every
  `ntp.conf` line the first mode moves and what that costs; `--no-gps-time`
  leaves all of it alone. It also runs gpsd with `-n` (the drop-in
  `/etc/systemd/system/gpsd.service.d/hammunition-gps.conf`, the same file
  and text as the `chrony` unit's), because gpsd publishes no time while no
  client is connected (measured on #162); a reboot after apply lets gpsd
  take it, and `unapply` leaves it while the `chrony` unit still uses it.
  It installs `fake-hwclock` only where there is no hardware clock;
  `hardware unapply` takes all of it back, `ntp.conf` byte for byte. `doctor` reports the time source, holdover, and a machine
  with no RTC. Parrot (ntpsec) only; built and not yet run on the field
  laptop. Guide: `docs/guides/gps-time.md`.

- **Two free HF modems join the packet core** (gap analysis section B
  Group 3, **Q-022** #6, branch `gap-05-hf-modems`). `mercury`
  (Rhizomatica, GPL-3.0) speaks VARA HF's TCP interface, so Pat's
  `varahf://` transport drives it unchanged: apt on Kali (1.9.13), a `make`
  build of v1.9.15 elsewhere, the release Debian unstable packages
  (**D-024**). `freedata` (DJ2LS, GPL-3.0) is station-to-station messaging
  and file transfer with a browser interface, a hash-pinned venv of the
  0.18.2 wheel, x86-64 only. Both are in `packet` and both transmit when a
  client keys them; the pages say so. `overlaps.md` gains an HF-modem
  section, `dispositions.md` records both as ADD outside the six sources,
  and `SCOPE.md` re-ranks VARA's Wine prefix as post-1.0 and optional. The
  packet-and-Winlink guide gains the modem choice. Measured: Pat 0.16.0 to
  Mercury 1.9.15 peer to peer, two instances back to back with no radio.
  Not measured: either modem over the air, FreeDATA's server and window.
- **Station config reaches six more units** (Q-022 #1, gap analysis A1;
  D-035 amendment of 2026-09-29). `direwolf` writes `/etc/direwolf.conf`
  (MYCALL and the KISS/AGW ports; the sound card and PTT stay yours),
  `ax25-tools` appends the `wl2k` port to `/etc/ax25/axports` once and never
  as a duplicate libax25 would refuse, `gpredict` writes its default ground
  station from the grid square, `tlf` writes `~/tlf/logcfg.dat` (CALL,
  MYQRA), `aprx` sets `mycall` on Debian's receive-only login, and
  `uronode` sets the node identity. Each was measured from the package's
  own files; each manifest says what it writes, how to inspect it and how
  to undo it. `linpac` and `fbb` configure themselves and get no block.
  The engine gains derived station values (`latitude`, `longitude` from the
  grid square; `ax25_callsign`, which defers a file rather than trim
  `W1AW/4`), `~/` paths written into the operator's home without following
  a symlink, `skip_if_present` for appends, and a dry run that names the
  station values each file is filled from.

- **Apt units from the gap analysis** (section D.6, branch
  `gap-06-apt-adds`). The eight candidates were swept on the seven targets
  first; `catalog-gaps-2026-09.md` carries the table and corrects the
  report where it was wrong. `predict` is in no archive (not in Debian 12,
  13 or unstable either) and is not added. `gr-fosphor` joins `sdr`: GNU
  Radio's GPU spectrum and waterfall blocks, offered on all seven targets;
  its .deb pulls `gnuradio-dev` and only the OpenCL loader, so the page
  says an OpenCL driver is the operator's to install. Not run on any GPU.
  `libiio-utils` joins `electronics` beside `m2kcli` and is linked from
  the `plutosdr` device: `iio_info -S`, as the `iio_scan` launcher, is
  the first check that libiio sees a Pluto or an ADALM2000. No such
  device is owned; nothing was scanned.
  `stm32flash` joins `electronics` beside `stlink-tools`: the STM32's
  built-in serial bootloader over a USB-serial adapter. No device the
  hardware catalog carries flashes that way (its STM32 entries use USB
  DFU), so none links to it; nothing was flashed.
  `ser2net` is carried but in no profile: installing the package enables
  a root `ser2net.service` whose shipped configuration listens on four
  loopback ports for `/dev/ttyS0` and `/dev/ttyS1` (read from the .deb,
  measured in a Debian 13 container), which a base profile should not
  do to every station; its page says how to inspect and disable it, and
  the rig-control guide gains a section on a radio on another machine.
  `qpwgraph`, the PipeWire patchbay the radio-audio guide already told
  people to install with apt, joins `digital-modes`, and the guide now
  links its page.
  `gpsprune` is offered everywhere and is not added: D-061 already
  measured it and left it out for its online tiles, and the report now
  says so.
  `opencpn`, the marine chart plotter that draws AIS ships from
  `rtl-ais` or `ais-catcher` and the position from gpsd or `gps-tether`,
  joins `navigation`; not opened on any target, so its OpenGL display on
  the field laptop and every one of those connections are unmeasured.

## v0.17.0 — 2026-09-30 — official topo sheets, CoMaps, and the offline browser map

Three pull requests since v0.16.0 (#161, #163, #164), 3 entries.

- **CoMaps: offline address search and routing like a phone app, with its
  own maps for your regions** (D-069). Two units join the `navigation`
  profile. `comaps` is built from source at the tag Flathub, nixpkgs and
  the AUR build (`v2026.08.31-14`), refused if the tag no longer resolves
  to its commit; the git backend gains what it needed, each a catalog
  field the engine runs: submodules fetched shallow and read back at their
  recorded commits, a hash-pinned build Python (CoMaps' CMake refuses
  Debian's protobuf 4.x), upstream's `configure.sh` with the files it must
  produce (its symbol step exits 0 without optipng), and files the install
  rule leaves out (the World maps, sha256-pinned). `comaps-maps` fetches
  CoMaps' own maps for your map regions through a generated table
  (`catalog/data/comaps-pins.yaml`, all 1,150 maps, 262 Geofabrik regions
  including every US state and DC), each checked by the SHA-1 and exact
  size in CoMaps' own index, which the plan says on every line, and from a
  LAN mirror first when one is set (D-070; `hammunition artifacts` lists
  them as `sha1-publisher`); an expired pin refuses the plan, and `update
  --upstream` says `pin expiring` or `pin expired`. `hammunition maps
  comaps`, the `comaps-offline` launcher, records CoMaps' licence answer and links your maps where it looks. CoMaps
  reads its position from GeoClue only, so there is no "you are here" on
  the laptop yet; the guide says what the route would be. Organic Maps and
  Flatpak are not carried. The build through the engine and US address
  search are owed by the bench.

- **An offline map in the browser** (**D-071**, branch `map-server`).
  `osm-pmtiles` turns each map region into vector tiles with the archive's
  tilemaker (3.0 or newer; Ubuntu 24.04's 2.4 defers it by name), and
  `vector-map-kit` pins the fixed files: tilemaker's OpenMapTiles profile,
  Natural Earth's ocean and land cover, the OSM Bright style, sprite and
  fonts, MapLibre GL JS 6.11.2 and pmtiles.js 4.5.0. Both join
  `navigation`. `hammunition reference serve` now also serves the map at
  `http://127.0.0.1:8480/map/`, with HTTP byte ranges, each file by its
  installed name, and refuses any request that does not name 127.0.0.1 or
  localhost. The page draws "© OpenMapTiles © OpenStreetMap contributors" on
  the map. `hammunition maps gps-tether` also serves your position to it as
  an event stream on 127.0.0.1:10111 (`--position-port`); a tether started
  with `--port 10111` now needs `--position-port` as well. A `data` archive
  can list its `members` and the subdirectory it goes `into`. Not yet run
  with a real tilemaker; the bench owes it.

- **Official topographic maps: USGS US Topo** (**D-068**). For US map
  regions, the `navigation` profile gains `usgs-ustopo` (the 7.5-minute
  sheets a region's outline touches, chosen offline from the carried index
  `catalog/data/ustopo-quads.txt`, generated by
  `scripts/gen_ustopo_index.py`, each checked against the publisher's S3
  ETag, multipart included) and `ustopo-qmapshack` (each sheet warped to Web
  Mercator with its collar cropped, and one `ustopo.vrt` that
  `hammunition maps qmapshack` adds to QMapShack's maps). The plan's Terrain
  block gains a US Topo part; `update` counts sheets with a newer indexed
  edition. QMapShack drawing the mosaic is not yet measured. FSTopo and USGS
  3DEP are next.

## v0.16.0 — 2026-09-30 — the navigation sweep: BRouter, repeaters, phone maps, the reference layer, the LAN mirror, and the documentation site

Six pull requests since v0.15.0 (#151, #150, #154, #157, #152, #155), 5 entries.

- **A LAN mirror for offline data, and `hammunition artifacts`** (**D-070**,
  branch `artifacts-mirror`). `hammunition station set --mirror URL` names a
  machine on your own network; every data download (a `data` unit's files, a
  map region, a terrain tile) asks it first at `<mirror>/<unit>/<name>` and
  its publisher on any failure, the same sha256 or publisher MD5 checked
  either way. The plan gains a *Data mirror* section and names both sources
  per download; `install --no-mirror` ignores the mirror for a run;
  `--clear-mirror` removes it; the transaction log records where each
  download came from. `hammunition artifacts [--json]` lists every remote
  data artifact for regions, a freshness and units given on the command
  line, reading no station: the contract
  [Hammunition Bunker](https://github.com/ChiefGyk3D/hammunition-bunker)
  mirrors from. `station show` gains a `mirror` line and its document a
  `mirror` field; plan steps gain `sources`. Guide:
  `docs/guides/lan-mirror.md`.

- **Repeaters on the offline maps** (**D-064**). `hammunition maps repeaters
  import FILE...` converts your own export, with no network, into a GPX, a
  QMapShack POI collection and a Navit layer in
  `~/.local/share/hammunition/overlays/repeaters/` (mode 0600). It reads a
  RepeaterBook GPX export, a RepeaterBook CSV export with Lat and Long,
  hearham.com's JSON or a CSV you type; CHIRP files and CSVs without
  positions are refused by name, since they carry no coordinates. Rows are
  merged on callsign, output frequency and position to 0.01°, and the
  merges are counted. RepeaterBook's attribution and terms are printed at
  import; nothing is ever fetched from RepeaterBook. `maps repeaters
  fetch-hearham` fetches hearham's open list on request, records the
  sha256 it saw and marks the layer unverified. `maps repeaters remove`
  undoes it. `import` and `remove` take `--json` (`repeaters`,
  `repeaters-removed`), carrying counts and paths, never a callsign or a
  position. The `navit-offline` launcher now runs `hammunition maps
  navit`, which adds the layer to your own copy of Navit's configuration;
  `hammunition menus apply` updates an installed launcher. Drawing on
  screen is not yet measured.
- **BRouter: a second offline router for QMapShack, with trail difficulty
  and climbs** (D-063, amending D-061's "BRouter stays out"). Three units
  join the `navigation` profile: `brouter` (upstream's v1.7.10 zip, checked
  by its sha256, with Java from the archive), `brouter-mapcreator-profiles`
  (the two map-creator filters the zip lacks, pinned by the tag's commit)
  and `brouter-segments`, whose new `brouter-mapcreator` converter builds
  BRouter's routing files on the machine from your regions with the
  Copernicus elevation folded in, as you, in one locked staging directory,
  and rebuilds them when a region, snapshot, tile or BRouter changes.
  brouter.de's routing files are never downloaded: they are rebuilt weekly
  with no checksum. The plan's *Terrain* block and its JSON say what is
  built; `hammunition maps qmapshack` points QMapShack's local BRouter at
  it, set to 127.0.0.1 with bind-to-host on, and leaves a BRouter you set
  up yourself alone (QMapShack honours the bind only once it has read
  BRouter's version; the guide says how to check). The
  map creator now runs with `-DavoidMapPolling=true`, which removes a 120 s
  wait its parser adds to every input under 100 MB. Measured end to end on
  synthetic regions against the pinned jar; a route drawn in QMapShack is
  owed by the bench.
- **The offline reference layer** (D-066). A `reference` profile (post-1.0)
  with `kiwix-tools`, `kiwix`, `dictionaries` (dictd with GCIDE, WordNet,
  FOLDOC and VERA acronyms, on 127.0.0.1 as Debian ships it) and
  `goldendict-ng` from the archive; `kiwix-library`, the Kiwix books chosen
  with `hammunition station set --reference-books`, each pinned by size and
  sha256 from its `.meta4` (`catalog/data/kiwix-pins.yaml`, generated by
  `scripts/gen_kiwix_pins.py` from the hand-written 27-book list in
  `catalog/data/kiwix-books.yaml`) with its licence printed in the plan;
  and `ics-forms`, FEMA's 39 ICS PDFs pinned by Hammunition's own sha256.
  A pin Kiwix has dropped refuses the plan by name and is never replaced by
  the newer file. `hammunition reference books` lists the books (`--json`:
  a `books` document); `hammunition reference serve` serves the books, the
  forms and the dictionaries on 127.0.0.1:8480, with kiwix-serve as a
  child always started with `-i 127.0.0.1`. `hammunition update` reports
  the books against their pins, and `--upstream` warns before a pin
  expires. The weekly pin review runs the new generator's `--check`.
  `docs/guides/offline-reference.md` is the walk-through. Three corrections
  from the spike: `zim-tools` ships `zimwriterfs` in Debian 13; libzim
  9.2.3 reads today's format-6.3 ZIMs; `hamradio-maintguide` is a packaging
  guide.
- **Phone maps from the laptop** (D-067). Two new units build, from the
  station's regions, a Mapsforge map (`mapsforge-map`) and a Mapsforge
  points-of-interest file (`mapsforge-poi`) per region, as the operator,
  with the archive's osmosis; the POI writer, packaged nowhere, is fetched
  from Maven Central and checked against a sha256 Hammunition measured. They
  are a new post-1.0 profile, `phone-maps`; `navigation` is unchanged.
  `hammunition maps phone` copies the phone files, Garmin maps included,
  into one folder with a `SHA256SUMS` and prints the ways to carry them to a
  phone: the laptop's hotspot with a web server bound to the hotspot's
  address, USB, and `adb` or KDE Connect as opt-ins. It transfers nothing.
  None of the files has been loaded on a phone yet. OsmAnd `.obf`,
  Organic Maps and CoMaps `.mwm`, PocketMaps and Transportr are not
  carried, each with its reason and route in D-067.
- `scripts/check_artifact_urls.py` no longer crashes on a derived unit, whose
  `source` is a unit name, and sweeps a converter's pinned tool (D-067).

## v0.15.0 — 2026-09-29 — sudo asked once per run; launchers by absolute path

Two pull requests since v0.14.3 (#146, #147).

- **Generated launchers run Hammunition by its full path** (issue #145).
  `qmapshack-offline` and `gps-tether` said `hammunition maps ...`, and
  Plasma starts a menu entry as a systemd user service whose `PATH` has no
  `~/.local/bin`: both failed from the menu with "hammunition: not found".
  A launcher now names `~/.local/bin/hammunition` when that is bootstrap's
  link to the engine that wrote it (so a moved checkout is mended by
  `./bootstrap.sh`), otherwise the engine's own `.venv/bin/hammunition`; the
  plan prints which. The device park and wake menu entries do the same.
  `hammunition menus apply` rewrites a launcher written before this, and
  `hammunition doctor` gains a **launchers** check naming any launcher whose
  engine is a bare name or a path that is gone, with the fix. The offline
  navigation guide's "QMapShack does not start from the menu" says what
  "not found" means now.
- **An install run as a user asks sudo once and keeps its ticket valid
  until the run ends** (D-062, issue #137). A `navigation` install waited
  7.8 hours at a second `sudo` prompt after 30 minutes of work: sudo's
  cached password had expired during a long conversion that runs as the
  operator. When a plan mixes root steps with unprivileged ones, it now
  prints a *sudo* section, runs `sudo -v` once before the first step, and
  refreshes the ticket with `sudo -n -v` every 4 minutes from the same
  process, stopping when the run ends. A refresh that fails is reported
  once and not retried. `--no-sudo-keepalive` turns it off, and the plan
  then says a later step may prompt again. The JSON plan carries it as
  `install.sudo`; the log records `sudo_keepalive_begin` and
  `sudo_keepalive_end`. The offline navigation guide's new troubleshooting
  entry keeps the manual refresh loop as the fallback.

## v0.14.3 — 2026-09-29 — the GPS tether on other setups

One pull request since v0.14.2 (#142).

- **The GPS tether takes `--gpsd` and `--port`, and any number of
  clients** (D-061, amended 2026-09-29). `--gpsd HOST[:PORT]` reads a gpsd
  on a Pi, a phone or a shack computer (IPv6 in brackets); `--port N` serves
  another port, 1024 to 65535. It still listens on 127.0.0.1 only; another
  machine reaches it through `ssh -L`. Every connected client gets every
  sentence from one shared gpsd watch, closed when the last client leaves,
  and a client that stops reading is dropped alone. The offline navigation
  guide gains section 12, *Other setups*: a remote gpsd, a phone, a
  Bluetooth or serial receiver, a rig's built-in GPS and the port it then
  cannot share with rig control, a parked receiver, and what of each is
  measured.

## v0.14.2 — 2026-09-29 — QMapShack selects its routing database; bench session 12

One pull request since v0.14.1.

- **QMapShack's routing database is selected** (D-061, amended
  2026-09-29). With `[Route] routino\database=-1` in its settings,
  QMapShack 1.17.1 loaded the `hammunition` Routino database and selected
  nothing, so routing did nothing without a message, and it wrote the `-1`
  back on exit. `hammunition maps qmapshack` now sets the key to 0 when it
  is absent or negative and leaves 0 or more alone.
- **Bench session 12 recorded** in
  `docs/reference/bench-verification-5430.md`: the two-region
  `install navigation` verified whole (294 commands, a 7.8-hour wait at an
  expired `sudo` prompt, issue #137), QMapShack listing the maps and
  drawing hillshade, and the rewritten GPS tether giving QMapShack a
  position from a real receiver. The guide's QMapShack section says where
  the Routino path dialog is, that the *Database* dock is not the routing
  database, that QMapShack reconnects to the tether by itself, and what
  the hatching is. A route on foot is still not recorded.

## v0.14.1 — 2026-09-29 — bench fixes: QMapShack's paths, the GPS tether

One pull request since v0.14.0, from bench session 12 on the field laptop.

- **The GPS tether makes its own NMEA** (D-061, amended 2026-09-29).
  `hammunition maps gps-tether` reads gpsd's JSON and writes `$GPRMC` and
  `$GPGGA` on 127.0.0.1:10110; no socat or gpspipe, the loopback bind
  in its own code, and a plain message when gpsd has no fix. The old
  tether sent nothing on the field laptop while gpsd reported no position. `socat` leaves the `navigation`
  profile and is kept in the catalog as retired, so `hammunition uninstall
  socat` still works where v0.14.0 installed it.
- **QMapShack's map and elevation lists go under `[Canvas]`** (D-061,
  amended 2026-09-29), where QMapShack reads them; the launcher moves its
  own directories out of `[General]`, where the first version put them.

## v0.14.0 — 2026-09-28 — trails, terrain and offline routing on foot

One pull request since v0.13.0: navigation piece 2, for daily hiking and
for the day nothing else works.

### Engine

- Three new converters, run as the operator under one lock per region or
  per build, scratch cleared only by the operator under that lock, root
  never removing a working directory: `mkgmap` (a Garmin image per region,
  0.85× the extract), `routino-planetsplitter` (one Routino database over
  every region, installed all-or-nothing, 0.67× of the sum) and `gdal-dem`
  (a VRT for hillshade and slope, and a 20 m contour overlay, 5.4 MB per
  tile measured). Converter versions in sidecars trigger exactly one
  rebuild; mkgmap's heap arrives through `JAVA_TOOL_OPTIONS`, because
  Debian's wrapper ignores `JAVA_OPTS` (#135, D-061).
- A `dem-tiles` install method: Copernicus GLO-30 elevation tiles for every
  square a region's Geofabrik outline touches, each verified by a sha256
  pinned by Hammunition or by the publisher's single-part ETag MD5, said
  which per tile in the plan; a carried tile list of 26,450 names with a
  weekly check; a square with no published tile is reported as exactly
  that, never "sea", and a region with no terrain available is warned
  about while its maps still install (#135).
- `hammunition maps qmapshack` (starts QMapShack after additively adding
  our directories to its per-user config) and `hammunition maps
  gps-tether` (gpsd → NMEA over TCP on 127.0.0.1:10110 only); a `doctor`
  check for Routino's translations file (#135).
- The plan's Terrain block, text and JSON from one object; the disk check
  counts tiles, images, databases, contours and scratch; `update` reports
  terrain as counts only (#135).
- Navit's dropped children now get the operator's minimal environment,
  not root's (#135).

### Catalog

- `qmapshack`, `routino`, `gdal-bin`, `mkgmap`, `mkgmap-splitter`, `socat`
  from apt; `dem-copernicus`, `osm-garmin`, `osm-routino`, `dem-qmapshack`;
  all in `navigation` (#135).

### Documentation

- D-061; the guide's trails-and-terrain sections, "find an address in
  Navit, walk it in QMapShack", the bridge, and the gaps; the CLI
  reference. What has not run on a desktop yet is marked so: bench
  session 12 on the field laptop is the check (#135).

## v0.13.0 — 2026-09-28 — Navit finds your towns

One pull request since v0.12.0, from the first address search on the bench.

### Engine

- Navit's town index was nearly empty on every Geofabrik sub-country extract:
  the extract carries only the in-extract pieces of the country's boundary
  relation, so `maptool` logged "Broken country polygon" and filed almost no
  town while still drawing them. The `navit-maptool` converter now merges a
  closed country border, synthesised from Natural Earth's admin-0 data for
  the region's country, into the extract with `osmium` before running
  `maptool -U`; a region passes only when maptool recognises each merged
  border as a country. On one US-state-sized region the index went from
  about a dozen items to over four thousand, nearly all with state and
  county (#133, D-057 amendment).
- Maps built by the old converter rebuild once: the sidecar records a
  converter version, and the plan says "will convert (converter changed)"
  (#133).
- maptool runs stay sequential: its fixed-name temp files make two runs in
  one directory crash, measured on the bench (#133).

### Catalog

- `country-boundaries`: Natural Earth 1:10m admin-0 countries, public
  domain, sha256 computed and pinned by Hammunition because upstream
  publishes none; a dependency of `osm-navit`, with `osmium-tool` (#133).
- `catalog/data/geofabrik-countries.yaml`: each Geofabrik region's country,
  generated from Geofabrik's index and checked weekly (#133).

### Documentation

- The guide's "Find an address" section (Actions → Town, the flag button,
  street and house-number icons, Set as destination); bench session 11
  (#133).

## v0.12.0 — 2026-09-28 — the engine speaks JSON, and `hammunition` is on the PATH

One pull request since v0.11.0: piece 1 of the console. The front ends that
follow (`hammunition-console`, its own project) drive the engine through
this interface and never import it.

### Engine

- A global `--json` flag: with it a command prints exactly one document on
  stdout (`schema: hammunition/1`, a `kind`, the engine version), diagnostics
  go to stderr, and the exit code is the text form's. That holds on success,
  on a refusal, on an argparse error and on an unexpected exception. Covered:
  `status`, `list`, `show`, `install`/`uninstall --dry-run`, `station show`,
  `hardware state`, `maps regions`, `update`, `doctor`. Text and JSON are
  rendered from one object per command, so they cannot drift; golden tests
  pin both (#127, D-059).
- A real install is never driven through `--json`; only `--dry-run` is
  accepted. Abbreviated flags are refused on every parser, because `--js`
  used to route around that guard (#127).
- `status --json` records removals: `removed`, `removal failed`,
  `removal interrupted`; a reinstall reads `completed` again (#127).
- Privacy: the `plan` document never carries rendered config or the
  callsign; `update` and `doctor` never name a map region in either output,
  and tests fail if they do; `station show --json` carries station values
  for local front ends and the reference says it is not for pasting (#127).
- `bootstrap.sh` links `~/.local/bin/hammunition` to the checkout's venv
  through `scripts/path-link.sh`: every change printed first, nothing it did
  not create replaced, no shell rc file touched. `doctor` checks where
  `hammunition` resolves; it offers a quoted switch only for our own link,
  names a foreign file or an earlier PATH entry without touching it, and on
  a fresh install says to log out and back in (#127).
- The Packages list and the Map regions section agree: `osm-regions` and
  `osm-navit` read "already installed" when nothing is left to fetch or
  convert (#127).

### Documentation

- `docs/reference/json-interface.md`, generated from the dataclasses and
  checked in CI; D-059; examples written as bare `hammunition`, with the
  full `.venv/bin/hammunition` path wherever a reader could meet the command
  before bootstrap has linked it; a "command not found" section in
  getting-started (#127).

## v0.11.0 — 2026-09-28 — lighter desktops welcomed, and Navit opens on your maps

Two pull requests merged since v0.10.0. Parrot and KDE Plasma stay first;
Xfce and LXQt (Lubuntu) are welcomed, and nothing about them is claimed
until the VM runs.

### Engine

- Desktop detection from the session files a display manager lists
  (`DesktopNames=`, with a measured fallback for LXDE and Cinnamon, whose
  files carry none). A new `desktops` / `desktop_alternative` manifest pair
  lets a unit serve particular desktops: a profile member for a desktop the
  machine lacks is deferred by name and the rest installs; typed by name, it
  is refused with the alternative named. `doctor` reports the desktops read
  (#129, D-060).
- Navit opens on the maps and follows the GPS. The generated config used to
  keep Navit's stock start point, Munich, and never followed the gpsd
  vehicle, so it showed a blank screen even with a 3D fix. It now centres on
  the first region's bounding box, read from the PBF header (bounded, and
  only the header), and adds `follow="1"`. Both are soft: when either cannot
  be done, the config is still written and the step says why (#130, D-057
  amendment).
- Navit's map size estimate goes from 0.8x to 0.9x of the download, after
  two US-state-sized regions converted at 0.874x and 0.856x on the field
  laptop. maptool's scratch files are removed once a region's map is
  installed (#130).

### Catalog

- `hammunition-tray-qt`, the tray for Xfce, LXQt, LXDE, MATE and Cinnamon,
  pinned from hammunition-tray v0.3.0, and `hammunition-tray` re-pinned to
  v0.3.0 with `desktops: [kde]`; both in `station`, each naming the other.
  `station` no longer pulls the Plasma shell onto a machine without it
  (#129).

### Documentation

- `docs/desktops.md`: every desktop's menu mechanism, tray and weight,
  unmeasured marked unmeasured; Lubuntu is LXQt, not LXDE.
  `docs/reference/vm-campaign-desktops.md` is the Xubuntu and Lubuntu
  checklist, not yet run (#129).
- `docs/guides/offline-navigation.md`: "Navit opens on a blank map", and
  that scratch stays after a failed conversion (#130).

## v0.10.0 — 2026-09-28 — device power control, offline navigation, the family's own units

14 pull requests merged between 2026-09-13 and 2026-09-28. Still beta: the
1.0 checklist in `docs/reference/release-1.0-checklist.md` is unchanged in
kind. What is new is the station switching its own radios off, finding its
way with no network, and carrying the other Hammunition projects as ordinary
pinned units.

### Engine

- Device power control: park and wake a catalogued device from the CLI,
  generated menu entries and a tray applet, through one root helper at
  `/usr/local/libexec/hammunition-devctl` behind one polkit action;
  `power_control` on a manifest names a method from a fixed enum, so the
  catalog never carries a command (#117, D-056). Installing the helper asks
  one `yes` that `--yes` cannot answer (#118), and refuses a tree another
  account can write, the owner's private group excepted.
- Kept off: a parked device stays parked across reboots, one rewritten-whole
  udev rule per device; `--until-reboot` opts out; `state` shows intent and
  reality side by side (#119, D-056 amendment). The reboot itself is not yet
  measured on the bench.
- Offline navigation: `osm-regions` fetches OpenStreetMap extracts from
  Geofabrik, pinned by sha256 for the yearly snapshot of every US state and
  DC, MD5-checked from Geofabrik for anything else the operator picks;
  `osm-navit` converts them with Navit's `maptool` as the operator, one
  region at a time, a failed region never taking the others with it; the
  selection lives in station config (#121, D-057). `hammunition maps
  regions` lists what can be chosen.
- Menus: every installed unit is placed on the day the menu is applied, not
  only those installed through the engine (#114, D-050 amendment).

### Catalog

- `skid-finder`, the maintainer's passive BLE-spam and Wi-Fi-attack
  detector, carried as an ungated `rf-security` unit from its tagged
  pre-release tarball, v0.6.0-alpha.1 (#116).
- `hammunition-tray`, the Plasma applet for park and wake, carried as a
  pinned `.deb` in `station` (#122); re-pinned to v0.2.0 here, which adds
  the kept-off label, Forget, and one notice per login.
- `navit`, `osm-regions`, `osm-navit`, and a post-1.0 `navigation` profile
  under a new `navigation-maps` category (#121).
- `wsjtx` claims its aggregator entry in the menu (#115).

### Documentation

- The Hammunition family (hill, tray, skid-finder) described from the main
  README, and power control in the status (#120).
- `docs/guides/offline-navigation.md`: choosing regions, disk and time
  measured on a 6.1 GB country-sized region, and what works with the network
  off (#121). `docs/hardware/power-control.md` for park, wake and kept off
  (#117, #119).
- The rebuilt menu verified as data on GNOME and Xfce (no one has opened
  either on a VM yet), and COSMIC and Pop!_OS
  recommendations toward 1.0 (#115).
- README: socials and donation rows (#109–#113).

## v0.9.0 — 2026-09-13 — beta: feature-complete for 1.0, verification remains

79 pull requests merged between 2026-09-02 and 2026-09-13. Every 1.0 stage
of SCOPE.md is in the catalog, the seven backends are written and reversed
by `uninstall`, M5 is met on six VMs, and the whole catalog installed and
verified on the field target. What separates this from the 1.0 tag is
verification and one decision, listed with owners in
`docs/reference/release-1.0-checklist.md`; that is the 0.1 that is missing.

### Engine

- Every download is fetched and verified before anything installs; a
  vendor `.deb` is simulated with the apt step before either runs (#15).
- Third-party apt repositories against a pinned key fingerprint; consent is
  the fingerprint itself; both files come out on uninstall (#16, D-040).
- A profile member the target does not offer is deferred by name and the
  rest installs (#14, D-039); a whole-profile plan names every deferral (#32).
- Mixed-release targets resolve from the release the machine already
  installs from, never downgrading (#12, D-038); an apt 404 from the pool is
  diagnosed as stale lists (#20); `install` refreshes the lists by default,
  `--no-refresh` opts out (#54, D-044).
- `requires_kernel`: the plan reads the running kernel's module tree and
  refuses or defers by name; Linux 7.1 removed AX.25 (#28, D-041). The packet
  core is userspace-primary and installs without `ax25.ko` (#55, D-045).
- The effect check reads back installed trees and launchers (#33), declared
  binaries (#79) and, for a library, its declared `installed_files` (#92).
- An installed tree is handed to the operator by an explicit `chown` step the
  plan prints (#52, D-043); a single prebuilt executable is installed by a
  privileged `install` command, not an in-process copy (#72); a venv is built
  with the resolved interpreter (#74).
- apt removals are refused by name, never performed (#50, D-022 enforced).
- A build already installed at its pin is already installed: the effect on
  disk plus the log's attribution (#75, D-051); per-block `binaries` for
  per-architecture archives (#86); a git block may carry patches (#97).
- A unit may install its apt packages without Recommends, as a second
  simulated apt command (#85, D-052).
- `hammunition update`: installed against the catalog, offline, nothing run
  (#91, D-053); `update --upstream` asks GitHub, git tags, PyPI or a label
  file whether the pin is current (#94). The first run found three pins
  behind and they were re-pinned the same night (#97).
- Node backend: a Vite application from the distribution's Node, refused when
  Node is absent or too old, never fetched (#11, D-037).
- A cmake build pulls cmake into the apt set (#21); `.venv` symlink untracked
  and the ignore rule tested (#25).

### Catalog

- 249 manifests. The EmComm Tools OS Community delta: paracon, artemis and
  gpa carried, chattervox left with test results (#58, D-048); the offline
  `data` install method for maps and reference sets (#59, D-049).
- `hammunition-hill`, the family's own dashboard (#39); rayhunter on
  x86_64, aarch64 and armv7l with upstream's digests (#71, #86); nanovna-saver
  and qlog from the archive (#41); wsjtx-improved from Kali's archive (#50);
  yaac re-pinned to the author's build label as its update probe (#23, #35).
- The vocabulary recut from 26 to 55 tags, each the thing a person looks for,
  every unit retagged (#103, D-055).
- morse installs on a PipeWire desktop again (#65, #85, #87).
- 26 units carry launchers; each launcher has a title; a terminal launcher
  holds its window, never shadows its own tool, and exits with the tool's
  status (#71, #82, #89, #100). 47 generated entries carry a `menu_title`
  (#101).

### Hardware

- brltty measured on all seven targets; the udev sweep reads every rule
  syntax (#57, D-047). limesdr's identifier from an owner's capture (#36).
  orbic-rc400l, the Rayhunter hotspot, unconfirmed until it is on the bench
  (#73). The Kali archive gaps say why (#29).

### Desktop menus

- Parrot's shape: one top menu, ordered groups, one submenu per category, a
  generated entry for every installed unit that ships none, placed filenames
  checked on disk, Parrot's replacement entries honoured (#66, #68, #77,
  D-050). Icons on every entry and directory; an install ends by re-applying
  the menu (#80, #83).
- Activity groups in plain words, a toolkit unit nested as one line, titled
  entries (#102, D-054); 55 submenus (#103, D-055); a source build's own
  desktop entry placed from the local prefix (#104).

### Targets, CI and verification

- Ubuntu 24.04 declared; the matrix stops reporting unswept names as absent
  (#17). The arm64 target runs natively (#60). The target image's apt steps
  retry when the mirror is mid-sync (#93). The weekly udev-citation job
  imports its parser again; the orphan test measured under load (#88).
- Every page generator takes `--check`; a missing probe is an error (#51).
- Campaign reports carry their evidence (#26); the GUI smoke lane reads
  stderr and owns its deadline (#37, #43).
- The field target is the Dell Latitude 5430 Rugged: nine bench sessions
  recorded, the whole catalog installed and verified there (#62, #78, #90,
  #99).

### Documentation and governance

- Release procedure with SSH-signed tags (#13). The landscape survey and the
  AHRL-versus-Blend coverage matrix (#40). EmComm Tools OS Community as the
  sixth inventory source (#44, D-042). Post-1.0 tracks for trunked and
  digital-voice listening and for repeaters (#53, #56, D-046); the mesh and
  Reticulum track (#106, SCOPE stage 11, #105).
- Hammunition is the one-stop shop for everything radio: nothing is out of
  scope, and what cannot work yet is a documented gap (#70).
- README and CLAUDE.md brought current against measured state; the 1.0
  checklist (#18, #47, #49, #84, #106).

## v0.7.0 — 2026-09-02

The last release cut from a direct push to `main`. The core cycle — resolve,
disclose, install, configure, verify, remove — VM-verified on Parrot, Kali,
Debian 13 and Ubuntu 26.04 with zero hard install failures across the
catalog. Annotated, not signed: no key existed.
