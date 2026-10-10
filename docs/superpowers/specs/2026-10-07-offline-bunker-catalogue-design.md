# Offline Bunker: a signed catalogue the engine plans against — design (#381, phase 1)

**Status:** draft for the maintainer's review, 2026-10-07.
**Tracking:** #381 (milestone 1.0, first of the 2026-10-07 EMCOMM work).
**Amends:** D-070 (LAN mirror), D-078 (questionable data on an operator's Bunker).
**Adopts:** the key-strength and hardware-key rules of #389 (suite-wide auth and
integrity policy), ahead of that policy's own decision record.
**Spans two repositories:** the engine (this repository) and
`Renegade-Penguin/hammunition-bunker`. The Bunker repository gets a short pointer
spec that links here; this file is the design of record.

## Why

The EMCOMM worst case is zero internet and zero cell service. Today a populated
Bunker does not help then. `docs/guides/lan-mirror.md` says it plainly: "A mirror
does not make an install work offline." A read of the code on 2026-10-07
(Codex research, file:line table kept with the maintainer's research notes)
found three reasons:

1. **Plan time asks publishers.** OSM regions are resolved against Geofabrik
   (`geofabrik.py:189`, `:198`) and every region needing a download gets an
   extra reachability HEAD even when pinned (`cli/main.py:4316`). Region
   outlines (`.poly`) are fetched to select terrain tiles and topo sheets
   (`terrain_plan.py:99`, `topo_plan.py:125`, `terrain_plan.py:226`). Copernicus
   tiles, Kiwix books, CoMaps maps, US Topo, 3DEP and FSTopo are HEAD-checked
   against their publishers (`copernicus.py:514`, `backends/kiwix.py:278`,
   `backends/comaps_maps.py:296`, `ustopo.py:270`, `usgs3dep.py:152`,
   `fstopo.py:310`).
2. **Run time skips the Bunker for many kinds.** US Topo, 3DEP and FSTopo fetch
   paths take no mirror (`fetch.py:744`, `fetch.py:786`), and nor do source
   tarballs, binaries, venv payloads, the Node tarball, converter tools and git
   pins (`backends/source.py:438`, `binary.py:146`, `venv.py:232`, `node.py:306`,
   `mapsforge.py:380`, `git.py:153`).
3. **The engine never reads the Bunker's index.** It downloads bytes from
   `<mirror>/<unit>/<name>` (`fetch.py:465`); the index the Bunker already keeps
   (`bunker/index.py`, version 2: sha256, size, publisher check kind and
   digest, licence, dates) is unused by the engine.

A second, larger gap is out of this phase's reach and stated up front: **230 of
326 units install through apt**, and the 36 git builds need apt build
dependencies. Phase 1 makes maps, data and pinned payloads work offline; most
software becomes installable offline only in phase 2 (apt and pip on the
Bunker).

## Rulings already taken (2026-10-07)

1. **Order.** #381 is 1.0 and comes first, before the critical destinations
   layer (#382) and the repeater keying audit (#383).
2. **Approach B, then C, then USB.** Phase 1: a signed, transport-agnostic
   catalogue and mirror routes for every data kind and pinned payload. Phase 2
   (before 1.0): apt and pip served by the Bunker. Phase 3: export to a drive.
3. **Fleet: both modes, configurable.** `personal` (one operator's machines)
   and `group` (several operators, one Bunker admin).
4. **The Bunker becomes a base station** (#388). The catalogue is the Bunker's
   general record of what it holds, readable by any service (a local LLM,
   Reticulum, Meshtastic queries, a wiki over HaLow), not an install-only
   format.
5. **Hardware keys are built in and recommended, never required.** Offered at
   setup, can be turned on later, and hybrid setups (file key plus hardware
   keys) are normal. Any FIDO2 authenticator, not one vendor; the maintainer
   owns YubiKeys and an Immurok.
6. **Key strength:** Ed25519 → ECDSA → RSA 4096/3072 (selectable) → RSA 2048 as
   a last resort; RSA of 2048 bits or fewer is allowed and warned everywhere.
7. **Security-key components are installed and checked by setup**, with PAM
   never switched on automatically.

## Scope

**In (phase 1):** catalogue version 3 and its signatures (Bunker); key
backends, setup prompt and `bunker keys` (Bunker); `hammunition mirror enrol`
and the station's enrolled-key list (engine); `--offline` and catalogue
fallback in planning (engine); mirror routes for every remaining data kind and
pinned payload, and git bundles (both); the `security-keys` profile and its
`doctor` checks (engine); D-085; docs (lan-mirror guide, Bunker guide,
troubleshooting); tests including one network-isolated integration test.

**Out, with where it goes:** apt and pip offline (phase 2, outline below);
export to a drive (phase 3, format fixed now); laptop identity proved by the
laptop's own hardware key in group mode (#388); the suite-wide policy's audit
of every existing check (#389); everything else in #388.

## The catalogue (Bunker index version 3)

The file keeps `kind: "bunker-index"` so the Bunker's existing upgrade chain
(`bunker/index.py`, v1 → v2) extends to v3 without a new loader. It is served
at `/catalogue.json` and, for one release, at the old `/index.json`. "The
catalogue" in this spec means this file.

Top level, new fields:

- `serial`: an integer that only grows, bumped on every change.
- `generated`: UTC timestamp.
- `bunker`: `{ "name": ..., "mode": "personal" | "group" }`.
- `signers`: the public keys that sign this catalogue, each with algorithm,
  size (RSA), `hardware: true|false`, and an id (the key's SHA256 fingerprint).

Per entry (today's fields kept: unit, name, path, sha256, size, check kind and
digest, licence, fetched, verified, status, previous path), new fields:

- `publisher`: what the Bunker saw when it fetched: the dated filename a
  `-latest` redirect resolved to, the publisher's size, and its digest of the
  check kind (MD5, ETag MD5, SHA-1), with the URL. Empty for kinds the repo
  pins by sha256 (the repo's pin is the trust; the Bunker's copy only says
  "here").
- `share`: `"all"` or `"owner:<enrolment id>"`. Only `group` mode reads it.
  Bring-your-own data (D-078: RepeaterBook export, ACMA register, anything
  under `hold_unverified`) defaults to the owner who brought it.

A new top-level `inputs` list carries plan-time inputs that are not artifacts
an operator installs but that planning needs: each region's `.poly` outline
(sha256 recorded by the Bunker; Geofabrik publishes no digest for it) and the
derived terrain-tile, topo-sheet and 3DEP selections for that outline.

Nothing in the format assumes HTTP. The engine reads a catalogue from a base
URL; `http://` covers a LAN or HaLow link and `file://` covers a drive
(phase 3).

### Freshness and rollback

A laptop records the highest `serial` it has accepted from each enrolled
Bunker. A catalogue with a lower serial is refused (an old, validly signed
catalogue replayed by a stale or tampered Bunker), with the remedy named:
`hammunition mirror accept-older` after the operator confirms the Bunker was
restored from a backup. A catalogue whose `generated` is older than 30 days is
used but warned on every plan line it resolves.

## Signatures and keys

### Signing

The Bunker signs the exact bytes of `catalogue.json` with
`ssh-keygen -Y sign -n hammunition-bunker-catalogue`, once per configured key.
Signatures are served as `/catalogue.sig.d/<key id>.sig`, listed in
`catalogue.json`'s `signers`. A laptop verifies with
`ssh-keygen -Y verify -f <allowed signers> -I bunker:<name> -n hammunition-bunker-catalogue`
and accepts the catalogue when **any** enrolled key's signature verifies,
unless the station requires a hardware key (below). OpenSSH is the only
dependency on both sides. `-Y sign` and FIDO2 `-sk` keys both need OpenSSH
8.2 or newer; measured 10.3 on the maintainer's Parrot 7, and Debian 13 and
Ubuntu 24.04 ship newer than 8.2 (exact versions recorded when the plan is
written).

### Key backends (Bunker config `[signing]`)

1. **`file`.** An OpenSSH private key in the Bunker's data volume. Signs
   unattended. The fallback when a group has no hardware.
2. **`security-key`.** A resident OpenSSH FIDO2 key on any FIDO2 authenticator
   (YubiKey, Immurok, SoloKey, Nitrokey, Token2, Feitian): `ed25519-sk`
   preferred, `ecdsa-sk` (P-256) when the token lacks Ed25519. Created with or
   without `no-touch-required`; with touch, someone taps the token when the
   catalogue changes; without, scheduled refreshes sign unattended. (OpenSSH's
   allowed-signers format has no touch option, measured 2026-10-07; whether
   `-Y verify` accepts a signature made without touch is a bench item. If it
   does not, unattended refreshes use the hybrid: a file key signs, the token
   adds its signature when someone is present.) FIDO2 has no RSA.
3. **`agent`.** Whatever key `ssh-agent` holds, which is how PIV and other
   PKCS#11 tokens sign (`ssh-add -s <pkcs11 module>`): RSA 3072/4096 and P-384
   on YubiKey firmware 5.7+, ECDSA and RSA on older PIV tokens.

Several keys at once is the normal hybrid: for example a file key that signs
every scheduled refresh plus a YubiKey that adds its signature when someone is
present. Losing one token never locks the fleet out.

### Key strength (adopting #389)

Offered in this order at setup and in `bunker keys add`:

1. Ed25519 (`ed25519-sk` or a file key);
2. ECDSA (`ecdsa-sk` P-256; P-384 for file and PIV keys);
3. RSA 4096 or 3072 (file or PIV), the operator picks;
4. RSA 2048, last resort.

Any RSA key of 2048 bits or fewer is accepted and **warned**: when generated,
when a laptop enrols it, on the Bunker status page, in `hammunition doctor`,
and on each plan line it vouches for. OpenSSH itself refuses RSA under 1024
bits; we say so instead of working around it.

### Setup and later

First run of the Bunker (and `bunker keys add` later) checks for a token
(`fido2-token -L` for FIDO2; `pcsc_scan`-equivalent through `pcscd` for PIV).
Found: hardware signing is offered as the recommended choice and the default
answer. Not found: one paragraph on why a hardware key is worth having, then a
file key, and the status page keeps a line saying so. `bunker keys list`,
`bunker keys add`, `bunker keys retire <id>` (stops signing; laptops drop it on
the next enrolment refresh).

**The Bunker runs in a container.** A FIDO2 token is passed in as its
`/dev/hidraw*` node; a PIV token by mounting the host's `pcscd` socket. Both
are bench items on the maintainer's NAS; `compose.yaml` gets commented
examples, never a default device mapping.

## Enrolment and laptop policy (engine)

- `hammunition mirror enrol URL` fetches the catalogue and its signatures,
  shows each signer (algorithm, size, hardware or file, fingerprint, weak-key
  warning), and asks the operator to type the fingerprint of at least one key,
  as the D-040 repository-key step does. Accepted keys go into the station as
  an allowed-signers list (`~/.config/hammunition/mirror-signers`, 0600) and
  the mirror URL into the existing station value. In `group` mode the Bunker
  returns an enrolment id for the laptop; it filters what is shared to this
  laptop and grants nothing else.
- `hammunition mirror status` shows the Bunker, its mode, accepted serial,
  catalogue age, and each key.
- `--clear-mirror` (existing) also removes the signers list and the serial.
- Station value `mirror_require_hardware_key` (default off): when on, a
  catalogue whose only valid signatures come from file keys is refused.

**A signature means "this Bunker recorded this", never more.** It never
upgrades trust: a repo-pinned item must still match the repo's sha256; a
publisher-digest item is verified against the digest the Bunker recorded and
the plan line says so; a digest-less kind (ACMA, unpinned FSTopo, repeater
snapshots) installs only under `hold_unverified` and the same consent gate as
online.

## Offline planning (engine)

- `install --offline` and `update --offline`: no publisher is contacted. Every
  resolution comes from the repo's pins, the local install records, or the
  enrolled Bunker's catalogue. No 30-second timeouts on a dead link.
- **Automatic fallback.** With a Bunker enrolled and no flag, a publisher
  probe that exhausts its retries (D-039's path, `retry.py:287`) resolves from
  the catalogue instead of deferring, and the plan line says
  "publisher unreachable; resolved from Bunker <name> (<key id>), recorded
  <date>".
- With no Bunker enrolled, behaviour is today's, and `--offline` refuses with
  the remedy (`hammunition mirror enrol URL`).

Resolution by kind, offline:

| Kind | Identity comes from | Bytes verified against |
|---|---|---|
| Static data, pinned OSM, pinned Copernicus tiles, Kiwix books, tarballs, binaries, venv payloads, Node tarball | the repo pin | the repo's sha256 (unchanged) |
| Unpinned OSM (`latest`, `monthly`) | catalogue `publisher`: dated name, size | the publisher MD5 the Bunker recorded |
| Unpinned Copernicus tile | catalogue `publisher`: size | the ETag MD5 the Bunker recorded |
| US Topo, 3DEP | the repo-carried ETag and size (live agreement skipped) | the repo-carried ETag |
| CoMaps maps | the repo-carried SHA-1 and size | that SHA-1 (weaker, disclosed) |
| Region outline, tile/sheet selections | catalogue `inputs` | the sha256 the Bunker recorded |
| Unpinned FSTopo, ACMA, repeater snapshots | catalogue | `hold_unverified` + consent, as online |
| Git pins | the repo's commit id | the bundle's checked-out commit and submodule gitlinks |
| Anything not in the catalogue | — | deferred by name (D-039): "not on Bunker <name>, publisher unreachable"; an explicitly named unit is refused, never half-installed |

## Fetch routes (both repositories)

- The mirror-first fetch (`fetch.py:465`) extends to `fetch_etag` and
  `fetch_sized` (US Topo, 3DEP, FSTopo) and to the source, binary, venv,
  Node, converter-tool and git extra-file paths.
- `hammunition artifacts --json` (`artifacts.py:118`) grows to list those
  payloads, US Topo/FSTopo sheets, the `inputs`, and git pins, so the Bunker
  knows what to hold. The Bunker's fetch run gains the matching kinds.
- **Git bundles.** For each git pin the Bunker clones at the pinned commit
  (recursive submodules), writes `git bundle create --all` and records its
  sha256. The engine fetches the bundle, clones from it, and checks HEAD and
  every submodule gitlink against the repo's pins before building; a tag that
  points elsewhere is refused exactly as online.

## The `security-keys` profile and `doctor`

A profile installs, from each target's main archive (all measured present on
2026-10-07 in Debian 13 trixie, Ubuntu 24.04 noble and Parrot 7):

| Package | Debian 13 | Ubuntu 24.04 | Role |
|---|---|---|---|
| `pcscd`, `libpcsclite1` | 2.3.3-1 | 2.0.3-1build1 | smart card / PIV daemon |
| `opensc` | 0.26.1-2 | 0.25.0~rc1-1ubuntu0.2 | PKCS#11 module for `ssh-add -s` |
| `fido2-tools`, `libfido2-1` | 1.15.0-1+b1 | 1.14.0-1build3 | FIDO2 tools; udev access for the logged-in user |
| `yubikey-manager` | 5.6.1+repack1-1 | 5.2.1-1 | `ykman` (PIV key generation, FIDO2 PIN) |
| `libpam-u2f` | 1.4.0-1 | 1.1.0 (security update) | PAM module, installed only |

**Unmeasured:** whether Ubuntu's `ykman` 5.2.1 generates RSA 3072/4096 or
Ed25519 in PIV on firmware 5.7+. If it does not, the plan line says so and
points at the token's own tools; a newer `ykman` from PyPI under the venv
backend is the fallback, decided after measurement.

**PAM is never enabled by the profile.** Enabling login or sudo with a token is
a separate step, `hammunition security-keys pam`, with a typed consent, a
`--dry-run`, and a check that a fallback login still works before anything is
written; it is a D-085 sub-rule and may be split into its own small spec if
the bench shows it needs more than that.

`doctor` checks: `pcscd` active; a FIDO2 token and a PIV token each detected
or not (informational); OpenSSH new enough for `-sk` signing; the user can
reach the token without root (udev); and each enrolled Bunker key's algorithm
and size, with the RSA ≤ 2048 warning.

## Bunker side (summary of its pointer spec)

- Index v3 writer and v2 → v3 upgrade; `publisher`, `share`, `inputs`,
  `serial`, `generated`, `signers`.
- Signing after every catalogue change, per configured key; status page lists
  keys, last signature time, weak-key warnings, and the "no hardware key" line.
- Fetch run gains the new kinds (topo/FSTopo sheets, payloads, git bundles,
  outlines and selections).
- `bunker export --to DIR` writes catalogue, signatures and files in the
  served layout (phase 3 builds the engine's `file://` reader; the layout is
  fixed now).
- Group mode: per-enrolment filtering of `owner:` entries.

## Decision record

**D-085** (next free number on 2026-10-07; confirm at merge): "A Bunker's
signed catalogue lets the engine plan and install with publishers unreachable;
a signature records what the Bunker saw and never upgrades trust; hardware
keys are recommended, never required." Amends D-070 (planning no longer
requires publishers when a Bunker is enrolled) and D-078 (`share` decides what
a group Bunker hands to whom).

## Tests

- **Engine unit tests:** each resolver in offline mode against a fixture
  catalogue; signature verification with `ssh-keygen -Y verify` using keys the
  test generates (Ed25519, ECDSA P-384, RSA 4096, RSA 2048 for the warning
  path; no token in CI); serial rollback refused; stale catalogue warned;
  `mirror_require_hardware_key` refusing a file-key-only catalogue; deferral
  and refusal wording; git bundle commit and submodule checks (a bundle whose
  tag moved is refused).
- **Bunker unit tests:** v2 → v3 upgrade; signing per backend (file key in
  tests; `security-key` and `agent` backends tested against a fake signer
  seam); `share` filtering in group mode.
- **One integration test** (engine repository, Linux runners): a Bunker
  container with fixture data and the engine in a network namespace where only
  the Bunker is reachable (`unshare -n` plus a veth pair, or podman's internal
  network): a data-unit install succeeds offline; a missing catalogue, a bad
  signature and an older serial each refuse.
- **Bench (maintainer):** sign with a YubiKey (`ed25519-sk`, touch and
  no-touch), with the Immurok, and with a PIV RSA key through the agent; token
  passthrough into the Bunker container on the NAS; a laptop with its network
  unplugged installing a region and its terrain from the Bunker.

## CI and GYST

The integration test is specific to these two repositories and stays local.
What it needs that is generic goes to GYST first: a reusable way to run a job
step with no network except named hosts ("works offline" tests), which other
suite repositories can call. Filed as a GYST issue when the plan is written.

## Phase 2 and 3 outline (not designed here)

- **Phase 2, apt and pip (before 1.0).** **This collides with a rejected item:**
  CLAUDE.md's "What this project is NOT" lists "a mirror of upstream Debian
  packages". The maintainer must rule before phase 2 is designed. The reading
  this spec recommends: that rejection is about the *project* hosting or
  re-publishing Debian's archive; an operator's own Bunker caching the
  packages their own laptops already install, with Debian's signatures
  untouched, is the D-078 shape (the operator's LAN, the operator's copy) and
  is allowed; re-signing packages with a Bunker key stays rejected. If the
  ruling goes the other way, offline software installs stay a documented gap
  with the route named. Prefer keeping upstream signatures over re-signing: a
  cache that serves the archive's own signed
  `InRelease` and the pool files the catalog's closure needs per target and
  architecture (computed with `apt-get install --print-uris` on each target),
  or an apt-cacher-ng cache warmed by the Bunker; a reprepro repository that
  the Bunker re-signs would need an OpenPGP key and falls under the rejected
  item above. Pip: a wheelhouse; the repo's
  `--require-hashes` pins already verify it, so no signing is needed. Its own
  spec.
- **Phase 3, drives.** The engine reads `file://` catalogues; the operator
  plugs in a drive written by `bunker export`, the same signatures verify.

## What is measured, and what is not

Measured 2026-10-07: the plan-time and run-time network table (code read with
file:line); install-method counts (230 apt, 36 git, 22 binary, 19 source, 10
venv, 10 data, 1 node); the security-key package versions above; OpenSSH 10.3
on the maintainer's laptop. Not measured: FIDO2 and PIV signing from inside
the Bunker container; the Immurok's algorithms; Ubuntu `ykman` 5.2.1 with
firmware 5.7 PIV; signing time with touch on a scheduled refresh; catalogue
size with tens of thousands of terrain tiles (the `inputs` selections may need
their own files rather than inline lists).

## Open questions for the maintainer

1. **Catalogue age warning:** 30 days, or a different number (a group that
   syncs monthly would warn every plan near the end of the month)?
2. **Default answer at setup when no token is found:** continue with a file
   key (as written), or stop and ask the operator to confirm they want to go
   without one?
3. **Personal-mode `share`:** ignore it entirely (as written), or keep
   recording owners so a personal Bunker can later become a group Bunker
   without re-tagging data?
4. **Phase 2 versus "not a mirror of upstream Debian packages"** (rejected
   list, CLAUDE.md): may an operator's Bunker cache the Debian/Ubuntu packages
   their laptops install, signatures untouched, as recommended above? Not
   needed for phase 1; needed before phase 2 is designed.
