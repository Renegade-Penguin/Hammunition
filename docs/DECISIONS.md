# Hammunition — Decision Record

Decisions settled by evidence. Each entry names what was decided, what decided
it, and what it closes. Supersedes anything in `CLAUDE.md` or `DESIGN.md` that
disagrees.

Amendments are appended and dated rather than rewritten, so the reasoning trail
survives. Where an amendment supersedes the original text, it says so.

`PARITY-POLICY.md` governs per-unit disposition (CARRY / SUPERSEDE / REVIVE /
RETIRE / ADD) and carries the M5 exit criteria. Where it and D-005 differ, it
wins — see the D-005 amendment.

---

## D-001 — 73Linux is an inventory source, not a base

**Decided:** Do not fork, port, or build on 73Linux. Treat it as a second
inventory source alongside AHRL.

**Evidence:** No LICENSE or COPYING in the repo, no header on `73.sh`, GitHub's
license API returns null. Default copyright applies — all rights reserved.
GitHub's ToS grants the right to view and fork on GitHub and nothing else.
This is a weaker position than AHRL, which at least carries GPL-3.0-or-later
headers on its installer.

Architecturally it is also the thing our inventory argued against: a `.bapp` is
executable bash with a metadata header. It supplies five easy fields (id, name,
description, website, version string); every hard field still lives inside the
imperative `INSTALL()` body where it cannot be queried, dry-run, diffed, or
consumed by a non-executing tool.

**Closes:** "Could we build on 73Linux?" — no.

**Action:** If we ever want the option open, one email to Jason (KM4ACK) asking
him to add a license file. Cheap, and it either unblocks or closes cleanly.

---

## D-002 — Architecture is a first-class selector, day one

**Decided:** `arch` is a structural selector in the manifest schema, present from
M1. Not deferred, not retrofitted.

**Evidence:** Nine AHRL units are arch-conditional; arch conditionals are
threaded throughout. 73Linux ships arch-partitioned trees (`app/stable/pi/`,
`app/stable/x86_64/`) from the start. Two independent projects in this niche both
treat arch as structural.

The cost of retrofitting is visible in AHRL's `install_gspiceui`, which hardcodes
an `aarch64-linux-gnu` path on all architectures and leaves a dangling symlink on
x86_64.

**Closes:** `DESIGN.md` §8 open question on ARM-as-day-one. Answer: yes.

---

## D-003 — Profiles are flat tags with overlap, not a tree

**Decided:** Packages carry a list of categories/profiles. Profiles do not nest
or depend on other profiles.

**Evidence:** AHRL's menu categories overlap heavily but never nest — 14 programs
appear in two or three. NBEMS is a 4-entry subset of Digital_Modes;
ARRL_Teachers_Institute deliberately cuts across six other categories. The one
nested case (Documentation → Command_Line_Docs) is a doc menu, not a software
grouping. 73Linux uses a flat checklist. These are tags, not a tree.

**Closes:** `DESIGN.md` §15.1.

---

## D-004 — Source builds are the core engineering problem, not an edge case

**Decided:** The source-build backend and its verification story are first-class
M3 scope, sized accordingly.

**Evidence:** 57 of 95 AHRL install units are not apt-installable — 35 source
builds from bundled tarballs, 9 prebuilt binaries and data archives, 4 Python
venv/pipx, 2 Python-run-in-place, 3 infrastructure, 2 launcher-only, 1 network
git clone, 1 remote script piped into bash.

An apt-only tool covers 40% of the parity target. The non-apt packages are
precisely the ones users cannot easily install themselves — the reason to exist.

**Consequence:** We must build and maintain our own pin/hash database. AHRL ships
zero checksums across 63 archives plus three unpinned network fetches, and
73Linux discovers tarballs by scraping web directory listings. Neither upstream
gives us anything to inherit. Sourcing and verifying every non-apt artifact is a
named sub-project, not a field in a YAML file.

### Amendment, 2026-08-25 — backend list corrected by measurement

The original entry inherited a candidate backend list from `CLAUDE.md` and
`DESIGN.md` without checking it. Verified counts across all 3,911 lines of
`bin/install_ahrl`:

| Backend | Occurrences in AHRL v27 | Verdict |
|---|---:|---|
| `cargo` | 0 | **Zero occurrences; not required for parity.** Retained here as a recorded negative, not deleted — `noaa-apt` is written in Rust but ships as a prebuilt binary, so Rust in the tree does not imply a cargo backend. |
| `flatpak` | 0 | **Zero occurrences; not required for parity.** |
| `appimage` | 0 in AHRL | **Not required for AHRL parity. Post-1.0**, required by the 73Linux delta — HAMRS is an AppImage whose upstream is discovered by scraping `hamrs.app`. |
| Wine *prefix* | 0 in AHRL | AHRL's Morse Runner needs bare `wine` only. **Post-1.0**, VARA needs a configured prefix: `WINEARCH=win32`, `winetricks winxp`, `winetricks sound=alsa`. A prefix backend is more than `apt install wine`. |
| **CPAN** | **1 real use** | **Eliminated by supersession, 2026-08-25 — revisit only if a second consumer appears.** Originally: Missing from the original breakdown. `install_aa_analyzer` runs `(export PERL_MM_USE_DEFAULT=1; cpan install Device/SerialPort.pm)`. **Security note:** unpinned CPAN fetch, no checksum, and `PERL_MM_USE_DEFAULT=1` auto-accepts configuration prompts — it is a network install that answers its own questions. Superseded: `aa-analyzer` → `flaa` removes the only CPAN consumer in the inventory, and with it the backend. If `aa-analyzer` is carried as a CLI alternative, satisfy it from Debian's `libdevice-serialport-perl`, never from CPAN. See D-014's worked example. |
| `snap` | 11 occurrences | **Not a backend — an anti-dependency.** Every occurrence is *removal* of snap Firefox, plus an APT pin to keep it off. Belongs in `system_modifications` as a package to purge and pin against, never as an install method. |

**Measured backend set required for 1.0** (AHRL parity + packet core): apt,
source-from-tarball, source-from-git, binary/`.deb`/archive, Python venv, pipx,
and launcher generation. **CPAN is not in the set** — see the amendment above.
Nothing else is justified by data.

### Amendment, 2026-08-25 — 1.0 packet core needs no new backend

Checked against the 73Linux delta admitted to 1.0 by D-008:

| Unit | Install shape | Backend |
|---|---|---|
| PAT | vendor `.deb` from GitHub Releases, per-arch | binary/`.deb` — **have it** |
| ARDOP | prebuilt release asset via GitHub Releases API | binary — **have it** |
| Direwolf | `git clone` + cmake | source-from-git — **have it** |
| AX.25 | apt (`ax25-tools`, `ax25-apps`) + config generation | apt — **have it** |
| BPQ (linbpq) | loose binaries + zips via `wget` | binary — **have it**, but see below |

**No new backend is required.** Three findings that are not backend gaps but do
need decisions:

1. **BPQ breaks pin-and-verify.** linbpq is fetched as individual files from a
   personal website's `/Downloads/Beta/` directory — unversioned URLs, no release
   structure, no checksums, and the word *Beta* in the path. We can hash what we
   download, but the URL's contents change under us with no version to pin to.
   This is the first unit in the catalog that cannot satisfy D-004's verification
   requirement as upstream currently publishes. Needs a policy: mirror it
   ourselves with our own hashes, carry it as `status: unverifiable` with an
   explicit user opt-in, or exclude it from 1.0.

2. **ARDOP's revival path is "don't build it."** AHRL disabled ardop over a
   compile error. 73Linux downloads upstream's *prebuilt release binary* instead.
   The REVIVE in `PARITY-POLICY.md` may need no code fix at all — just a change
   of install method. Test this before spending effort on the build.

3. **AX.25 needs templated config generation, not just installation.** Its
   install writes an operator-specific line into `/etc/ax25/axports`:
   `echo "wl2k ${MYCALL} 1200 255 7 Winlink" | sudo tee -a /etc/ax25/axports`.
   This makes `DESIGN.md` §15.3 (station-local configuration — callsign, grid,
   device paths) **blocking for the 1.0 packet core**, not a deferred question.
   It also exceeds `system_modifications` as scoped in D-012, which covers udev,
   groups, and blacklists but not templated config files.

---

## D-005 — Parity means coverage with honest status, not universal success

**Decided:** M5 parity is: every AHRL (and 73Linux delta) unit resolves to a
manifest carrying a `status`. Not: every unit installs successfully.

**Evidence:** Nine AHRL toggles ship disabled, with reasons in shell comments —
NOAA satellites out of service (noaa-apt, xwxapt), compiler errors (ibp, ardop),
empty stub functions (mfc_gpl, tt3_gpl), Debian 13 dropping Qt5 components
(dream, mvoice), packaging abandoned (radiosonde_auto_rx).

That knowledge is the single most valuable thing in the tree and it currently
lives in comments. In our catalog it is queryable data.

**Schema consequence:** `status` (supported | broken | retired) with a `reason`
string and a `date`.

### Amendment, 2026-08-25 — "has a status" is too weak a bar

Superseded in part by `PARITY-POLICY.md`. Coverage alone is not parity.

M5 requires that every unit **either installs successfully on at least one
supported distro, or carries a `broken`/`retired` status verified by us** — not
inherited from an AHRL shell comment.

- **Never inherit a verdict.** Re-attempt `ardop`, `radiosonde_auto_rx`, and the
  compiler-flag-fragile set (`glfer`, `gsmc`, `owx`, `linrad`, `qgrid`) on
  current sources, on our supported distros, before accepting any verdict.
- **Record the attempt**, not just the conclusion: date tried, version tried,
  distro, and the actual failure. The next person needs to know what was tested.
- **Exit criterion:** our install-success fraction must be **at least as good as
  AHRL's own**. AHRL ships 95 units with 9 disabled. Shipping 95 manifests with
  40 marked broken is not parity, however complete the coverage looks.
- **Inherited verdicts count against us.** Tested-and-confirmed-dead does not.

---

## D-006 — Do not carry forward

**Retired — the world changed, no substitute exists:**
- `noaa-apt`, `xwxapt` — all NOAA APT satellites out of service as of 2025-11-09

**Dead — do not port:**
- `ibp` — upstream 0.21 predates modern C, many compiler errors
- `mfc_gpl`, `tt3_gpl` — empty stub functions, shipped as no-ops for years;
  AHRL's own docs call them obsolete
- `dream`, `mvoice` — architecturally stuck on Qt5 components removed in
  Debian 13

**Revisit, not abandoned:**
- `ardop` — v27 compile error, but upstream (pflarue/ardop) is alive
- `radiosonde_auto_rx` — AHRL gave up packaging it; upstream active. Our venv
  backend should handle what defeated a bash script.

**Alive but fragile — needs `compiler_flags` to build at all:**
- `glfer` (2003-era), `gsmc`, `owx` (2022 snapshot), `linrad`, `qgrid`

**Unpinnable as shipped — needs our own version pinning:**
- SatDump, SDR++, gsmc, cwwav, dump1090, AntScope2 ship as unversioned
  master/main snapshots. (A pinned `SatDump-1.2.2.zip` sits unused in the AHRL
  tarball.)

  *Amendment 2026-08-25:* **three of these six are packaged in Debian** —
  `satdump`, `sdrpp`, and `dump1090`'s supersession target `readsb`. Preferring
  the packaged version resolves the snapshot problem at zero cost rather than by
  building our own pin. See `reference/blend-inventory.md` and
  `reference/overlaps.md`. `gsmc`, `cwwav` and `AntScope2` still need pinning.

**Needs our own recipe:**
- AIS-catcher — good software, installed via the one method our security rules
  flatly prohibit (remote script piped into bash)

---

## D-007 — M17 support is ours to build, not to port

**Decided:** M17 is a genuine gap we fill on our own terms.

**Evidence:** AHRL v27 has zero M17 support — `droidstar` was removed in v26e and
`mvoice` is broken on Debian 13. There is nothing to port.

---

## D-008 — Winlink/packet/EMCOMM is a parity gap AHRL cannot fill

**Decided:** Add the 73Linux delta cluster to the 1.0 target. Reconsider whether
"AHRL parity" alone is a defensible 1.0.

**Evidence:** 73Linux carries PAT, PATMENU3, BPQ, AX25, ARDOP, ARDOPGUI, VARA,
GARIM, VARIM. AHRL has no Winlink client at all, no BPQ node, and its lone
`ardop` is disabled. A large fraction of the EMCOMM audience runs Winlink; an
AHRL-derived catalog leaves them stranded.

Secondary from 73Linux: XYGRIB (GRIB weather), HAMRS (logging), M0IAX. Pi-system
helpers (PISTATS, PITERM, VNC, CONKY, BATT) are out of scope.

### Resolved, 2026-08-25 — split the delta

**In 1.0 — the packet core:** PAT, AX.25 stack, BPQ, ARDOP, and Direwolf *with
configuration, not merely installation*.

**Post-1.0:** VARA (needs a configured Wine prefix; closed-source freeware) and
HAMRS (needs an AppImage backend, and its upstream is discovered by scraping a
webpage).

**1.0 is therefore: AHRL parity + the packet core.** Verified against the
measured backend set — none of the five 1.0 units requires a backend we do not
already need (see the D-004 amendment of the same date), so the split costs no
new engineering. Two caveats recorded there: BPQ cannot satisfy pin-and-verify as
upstream publishes it, and AX.25 makes station-local config generation blocking
rather than deferred.

Secondary 73Linux units (XYGRIB, M0IAX) remain unclassified pending
`PARITY-POLICY.md` disposition.

### Correction, 2026-08-25 — three units were misclassified by their filenames

The original text of this resolution read: *"Pi-system helpers (PISTATS, PITERM,
VNC, CONKY, BATT) are RETIRE-as-out-of-scope."* **Three of those five were wrong,
and the error was mine.** I classified them from the `PI*` filename prefix
without reading the `.bapp` headers. The prefix is 73Linux's naming convention,
not a statement about the software.

| Unit | What I said | What it actually is | Corrected |
|---|---|---|---|
| `PITERM` | Pi system helper | **QtTermTCP** (G8BPQ) — packet terminal over TCP | **1.0 packet core** |
| `QTSOUND` | *(not considered)* | **QtSoundModem** (UZ7HO / Wiseman port) — soundcard packet modem, a direct alternative to Direwolf | **1.0 packet core** |
| `PIAPRS` | *(not considered)* | **Pi-APRS** — APRS messaging client | **1.0 packet core** |
| `PISTATS` | Pi system helper | Pi3/4 stats monitor | RETIRE — correct as stated |
| `CONKY`, `BATT`, `VNC` | Out of scope | System monitor, battery test, RealVNC viewer | RETIRE — correct as stated |

**The 1.0 packet core is therefore eight units, not five:** PAT, AX.25, BPQ,
ARDOP, Direwolf-with-configuration, **QtTermTCP, QtSoundModem, and Pi-APRS**.
QtTermTCP and QtSoundModem are the same author's stack as BPQ, which is why they
belong together.

**Method note:** classify from the manifest header or the source, never from the
filename. The `.bapp` `Comment=` field carried the correct answer the whole time
and cost one HTTP request to read.

### Amendment, 2026-09-10 — the packet core is userspace-primary (D-045)

The resolution above names "the AX.25 stack" as a member of the packet core
and the profile prose described the station as the kernel stack with Direwolf
feeding it. Linux 7.1 removed the kernel stack (**D-041**), and on every 7.1
kernel — Kali today, the maintainer's own laptop, Ubuntu 24.04 the day its
HWE kernel moves — the userspace half is the whole station: Direwolf or
QtSoundModem as the modem, pat, LinBPQ, YAAC and Xastir over KISS or AGW.
**The packet core is that userspace path.** The kernel stack — `ax25-tools`
and what sits on it: `axports`, `kissattach`, `ax25d`, NET/ROM, packet
connections as sockets — is the fuller station where the kernel carries it,
and is planned or deferred by name per D-041. The eight units are unchanged;
what changed is which of them is the foundation. `z8530-utils2`, never a
core member, is retired by the same record.

---

## D-009 — Community side-loading, with review tiers, from day one

**Decided:** Ship a three-tier catalog — core / community / local — where
dropping an entry in the community tier surfaces it automatically, and users
choose whether to enable unreviewed entries.

**Evidence:** This is 73Linux's genuinely good idea and the direct answer to our
founding objection to AHRL (single maintainer, contribution-hostile release
process). It is why 73Linux has contributors and AHRL does not.

**Difference:** signed catalog entries, not unreviewed bash inheriting cached
sudo.

---

## D-010 — Add an `update` block (schema field 15)

**Decided:** Every manifest carries an update descriptor: a version-probe method
(binary `--version` parse, `apt policy`, GitHub releases API, tag list) plus an
upgrade strategy.

**Evidence:** 73Linux's `VERSION()` is a first-class concept and the field our
schema list was missing. AHRL has no update story at all — install once, rot
forever. Being able to answer "installed versus upstream" is what makes the
project maintainable past 1.0.

---

## D-011 — Provenance: facts only, from both sources

**Decided:** Reuse package names, versions, upstream URLs, install mechanisms,
build flags, `-Wno-*` workarounds, and patch sets. These are facts and not
copyrightable. Write the engine from the inventory.

**Do not reuse:** AHRL's `.desktop` files (105), `.directory` files (18), menu
structure, or documentation prose — no license notice on any of them, status
genuinely unclear. `bin/install_ahrl` and `bin/test_menus_debian13.py` are
GPL-3.0-or-later (Copyright 2024/2025, Andy Stewart KB1OIQ); porting their logic
would be viral. Nothing from 73Linux's code — unlicensed.

**Credit both projects in the README.**

---

## D-012 — Schema fields required by real data

The `CLAUDE.md` sketch is insufficient. Required additions:

**Structural:**
- `install` is a **list of typed method blocks**, not a map of distro → package.
  Resolution is (distro, version, arch) → method, and the *method itself* varies:
  js8call needs apt on Mint 22.3 and a cmake source build everywhere else;
  GridTracker2 needs a different `.deb` per architecture; MSHV needs a different
  `.pro` file per arch.
- `arch` as a first-class selector (see D-002)
- `build_depends` separate from `depends` — 34 source builds carry apt
  build-dependency lists (QLog's is 15 packages). Install-time only; must not
  appear in "what this profile installs."
- `provides` — fldigi's source build also produces flarq, which has its own menu
  entry and no install function. Without this, M5 reports a false gap.
- `conflicts_with_repo_package` — five packages require `apt purge` of the repo
  version first (fldigi, flrig, quisk, wfview, wsjtx). Destructive; must be
  declared, printed in `--dry-run`, and logged.
- **Ordering constraints** — `install_wsjtx` renames its binary to `wsjtx_orig`
  and `install_wsjtx_improved` renames it back; run one without the other and a
  binary is wrong. `svxlink` and `xastir` must run before user creation because
  they create groups. A flat dependency list expresses "requires," not "after."

**Security and provenance:**
- `source` block with `url`, `sha256`, `signature`
- `apt_repo` with `key_fingerprint` and `key_url` — AHRL adds mozillateam/ppa
  plus an APT pin file, unpinned and unprompted
- `system_modifications` — udev rules, modprobe blacklists,
  `dpkg --add-architecture`, groups created, files shadowed. AHRL deletes distro
  librtlsdr and hand-symlinks replacements with no record.

**Honesty and operation:**
- `status` + `reason` + `date` (see D-005)
- `compiler_flags` / `patches` — six builds need `-Wno-*` flags or in-place sed
  patches, or they do not build
- `launcher` — 14 units need a generated wrapper: pipx installs to
  `$HOME/.local/bin`; quisk, QtTinySA, and MSHV must `cd` into their source dir;
  Java needs `java -jar`; Morse Runner needs wine
- `scope: system | user` — five Python installs land in a specific user's `$HOME`
  via `pkexec --user`. AHRL asks for one username and hardcodes it; a second user
  gets nothing.
- `categories` as a **list**, not a string (see D-003)
- `update` block (see D-010)

---

## D-013 — The dead-menu-entry bug is the design argument

`install_hamclock_next` is defined, enabled, has a menu entry, has a tarball, is
listed in CHANGES as a v27 feature — and is never called from the main body.
Users get a dead menu entry. Six more defects of that shape exist, including a
`$BROWSER` variable never assigned and an inverted Linux Mint detection that is
always false.

A generated call list makes this class of bug structurally impossible. When
justifying the declarative catalog to anyone, this is the example.

### Worked example, 2026-08-25 — the dead menu entry has a real victim

This stopped being hypothetical. The never-called function is
`install_hamclock_next`, and the software it fails to install is **the
replacement for software that has since been discontinued**.

- HamClock's author, Elwood Downey (WB0OEW), became a Silent Key **2026-01-29**.
- HamClock was reported to stop functioning **end of June 2026**.
- **AHRL v27 shipped May 2026** — after the announcement, before the sunset.
- v27 builds ESPHamClock **four times** (800x480, 1600x960, 2400x1440,
  3200x1920) and every menu entry hardcodes `-b hamclock.com:80`.
- v27 also ships `hamclock-next-1.5.tar.gz`, defines `install_hamclock_next()`,
  installs `hamclock-next.desktop` into the HF_Propagation menu, and lists
  "added hamclock-next" in CHANGES.
- **The call is missing from the main body.**

So AHRL v27 installs four copies of a discontinued client pointed at a
discontinued server, ships the maintained successor in the same tarball, and
never installs it. A user who wanted the working one got a dead menu entry.

Full sourcing in `reference/licence-verification.md`. Two consequences:

1. **The catalog's call list must be generated.** Not reviewed, not linted —
   generated, so a defined unit that is never installed is unrepresentable.
2. **Service endpoints are manifest fields, never launcher constants.** Had the
   backend URL been a field, repointing every HamClock install at the Open
   HamClock Backend would be a one-line catalog change. Hardcoded into four
   generated launchers, it is not. This is shape 7 in the schema.

### Correction to worked example 1, 2026-08-25 — tested, and partly wrong

The example above was written from reporting. It was then tested, per the
maintainer's instruction to test rather than report, and **two of its statements
do not survive**. The original text is left intact above; this is the retraction.

**Retracted:** *"the software it fails to install is the replacement for software
that has since been discontinued"* and *"AHRL v27 installs four copies of a
discontinued client pointed at a discontinued server."*

**What testing found** (full probe results in
`reference/licence-verification.md`):

- `hamclock.com` is **up**: HTTP 200, `Last-Modified 2026-08-07`, and
  `/ham/HamClock/version.pl` returns **4.27** with a changelog of new features.
  HamClock was continued after its author's death, past the 4.23 AHRL ships.
- Elwood's own server, `clearskyinstitute.com`, **is** gone — it refuses TCP.
  The sunset was real; it landed on the original host, not on the hostname AHRL
  points at.
- `hamclock.com` is now a third-party, patron-funded operation.

**What survives, and it is the part that mattered:** `install_hamclock_next` is
still defined, enabled, menu-registered, changelog-announced, and **never
called**. The dead menu entry is real. Only the claim about *what the user lost*
was wrong — they lost access to a maintained fork, not a rescue from a dead one.

**The methodological point is worth more than the example.** A statement about
the world was recorded as settled on the strength of three consistent secondary
sources, and a single `curl` overturned it. `PARITY-POLICY.md` already says never
inherit a `broken` verdict without testing it; this extends that to **every**
external-state claim, including the ones that flatter our argument. Ours did, and
it was wrong.

---

### Worked example 2, 2026-08-25 — the collision that stopped existing

The first example shows the declarative catalog preventing a bug. This one shows
it **removing a problem from the design entirely**, which is the stronger claim.

AHRL installs WSJT-X and WSJT-X-improved in sequence. Both builds emit a binary
called `wsjtx`, so the second overwrites the first. AHRL choreographs around it:

```
install wsjtx          → mv /usr/local/bin/wsjtx  /usr/local/bin/wsjtx_orig
install wsjtx_improved → mv /usr/local/bin/wsjtx  /usr/local/bin/wsjtx_improved
                       → mv /usr/local/bin/wsjtx_orig /usr/local/bin/wsjtx
```

Four renames across two functions, order-dependent in both directions. Run
either half alone — which the `INSTALL_*` toggles explicitly permit — and a
binary ends up under the wrong name. Nothing detects it.

Our schema declares the mapping instead:

```yaml
# wsjtx.yaml                    # wsjtx-improved.yaml
binaries:                       binaries:
  - produced: wsjtx               - produced: wsjtx
    install_as: wsjtx               install_as: wsjtx-improved
```

The builds still emit the same filename. They can no longer collide, because
neither package controls its installed name — the manifest does. The ordering
constraint is not automated or made safe; **it stops existing.** `after:` is
retained for genuine ordering (units that must create groups before user
creation), and `wsjtx-improved` declares it for determinism, but correctness no
longer depends on it. A schema validator rejects duplicate `install_as` outright.

**The general principle:** when imperative install logic needs a careful
sequence, check whether the sequence is inherent or an artifact of the tooling.
AHRL's rename dance looks like a hard ordering requirement and is in fact a
naming collision that better modelling deletes. Prefer making a bad state
unrepresentable over making it survivable — the same reasoning that removed
`method: script` and optional `sha256` from the schema.

---

## D-014 — Backends are justified by measurement, not convention

**Decided:** No backend enters the roadmap without a named package in the
inventory that requires it. Every backend carries its justifying unit.

**Evidence:** `CLAUDE.md` and `DESIGN.md` both listed `cargo`, `flatpak`, and
`appimage` as candidate backends. Measurement found zero occurrences of any of
them in AHRL v27. They were on the list because installers usually have them —
convention, not data.

The same blind spot ran the other way: **CPAN** was in nobody's list and is
genuinely required by `aa-analyzer`. Convention predicted three backends we do
not need and missed one we do.

**Rule:** a backend proposal names the unit(s) requiring it, or it does not ship.
When a backend is considered and rejected, record it as a measured zero rather
than deleting it — the negative result is evidence, and it stops the next person
re-adding it from the same convention.

### Worked example, 2026-08-25 — CPAN, justified by one package, eliminated by one supersession

CPAN entered the backend set correctly: measurement found it, convention had
missed it, and exactly one unit required it — `aa-analyzer`, which needs the
Perl module `Device::SerialPort`.

Then the disposition pass found `flaa`: an actively maintained W1HKJ GUI for the
same RigExpert AA-\* analyzers. Superseding `aa-analyzer` → `flaa` removed the
only CPAN consumer in the inventory, and with it:

- an entire backend we would have had to build, test per-distro, and maintain
- an unpinned network install with no checksum
- `PERL_MM_USE_DEFAULT=1`, which auto-accepts configuration prompts — a network
  install that answers its own questions, in direct conflict with our security
  requirements

**The rule this adds:** measurement justifies a backend; it does not oblige us to
build one. Before committing to a backend whose justification is a single
package, check whether that package has a maintained replacement. A backend with
one consumer is a liability with a dependency.

**Order matters:** run dispositions before finalising backend scope. Had we built
CPAN support first, we would have maintained it for a package we then superseded.

**Closes:** the unexamined backend list in `CLAUDE.md` M3, `DESIGN.md` §6, and
the `backends/` line in the repo layout.

---

## D-015 — Qt5 exposure is a standing register, not a one-time audit

**Decided:** The catalog carries a **queryable Qt5 exposure register** as data:
which units depend on Qt5, which specific Qt5 components, and whether a Qt6 path
exists upstream. It is maintained continuously and reported, not audited once.

**Evidence:** Qt5 is not a per-package risk — it is one systemic risk with many
faces, and it has **already claimed two units**. `dream` died on
`libqt5webkit5-dev` and `mvoice` on `libopendht-dev`, both removed in Debian 13.
AHRL discovered each failure one package at a time, by compile error, after the
fact.

Units still on Qt5 in the inventory: **QLog** (qtbase5, qtwebengine5, qt5charts,
qt5keychain — the heaviest exposure in the catalog), **wsjtx**,
**wsjtx_improved**, **MSHV**, **gqrx**, **qgrid**, **QtTinySA**, **Coil64**,
**AntScope2**, **wfview**.

**Flagged no-migration-path:** `wfview` requires `libqt5gamepad5-dev`. **Qt
Gamepad was deprecated in Qt 5.15 and never carried into Qt6** — there is no Qt6
equivalent to migrate to. Upstream must drop or reimplement the feature. This is
qualitatively different from a module that merely needs porting, and the register
must distinguish the two.

**Register fields, per unit:** `qt_major`, the specific component list, an
upstream Qt6 status (`ported` | `in-progress` | `no-path` | `unknown`), and the
date that status was last checked.

**Why data and not a document:** a document goes stale silently. A register in
the catalog can be queried (*"what breaks when Debian drops Qt5?"*), reported in
CI, and diffed between releases. It is the same argument as **D-005**'s status
field — knowledge that currently lives in a maintainer's head becomes something
the tool can answer.

**Generalises:** Qt5 is the instance, not the rule. Any shared dependency whose
removal would take out multiple units warrants a register — GTK2 is the next
candidate (`glfer` needs `libgtk2.0-dev`, and GTK2 is EOL).

---

## D-016 — The engine fails loudly on any unresolvable dependency

**Decided:** An unresolvable dependency is a hard error that stops the run. Never
a warning, never a log line the run continues past.

**Evidence:** AHRL has **no `set -e` and checks no exit status anywhere** across
3,911 lines. Every `apt install`, `make`, and `cmake` may fail and the script
proceeds to the next program. This is why `bin/find_errors_ahrl` exists — it
greps a 2.5-hour install transcript for error strings *afterwards*, and its own
comment concedes *"It doesn't identify EVERY error...yet(?)."*

The consequence is silent partial installs. Several AHRL dependency lines are
suspected already-failing and nobody would know:

| Dependency | Unit | Problem |
|---|---|---|
| `fftw2` | `glfer` | FFTW **version 2** |
| `libgtk2.0-dev` | `glfer` | GTK2 is EOL |
| `python3-tksnack` | `js8spotter` | Snack toolkit, very old |
| `libportaudio-ocaml-dev` | `fldigi` | An **OCaml** binding fldigi does not use — almost certainly a copy-paste error that has never surfaced because nothing checks |

**This restates `CLAUDE.md`'s "fail loudly, never silently degrade" as a specific,
testable engine requirement**, because the failure mode it prevents is the single
most common defect in the prior art.

**Consequences:**
- Dependency resolution is a distinct pre-flight phase. Resolve everything for
  the whole transaction, report every failure together, then install — do not
  discover failures one package at a time, mid-run.
- `--dry-run` must resolve dependencies for real. A dry run that cannot tell you
  a package is unobtainable is not complete, and **D-004** requires completeness.
- Partial success is reported explicitly: what installed, what did not, why.

### Latent bugs to fix rather than inherit

Carried across from AHRL and corrected in our manifests:

| Bug | Fix |
|---|---|
| `default-jre-headless` for **FoxTelem** and **YAAC** | Both are **Swing GUI** applications; a headless JRE is precisely the one without the AWT display stack. Use a full JRE. |
| `libportaudio-ocaml-dev` in **fldigi** deps | Spurious. Remove. |
| `LIBWXGTK_DEV` resolved by `apt-cache search libwxgtk \| grep dev \| grep -v media \| grep -v webview` | Replace with **explicit per-distro package names**. The wxWidgets 3.2 → 3.3 transition changes the name and the pipeline silently returns the wrong package or nothing. Affects `freedv`, `gspiceui`, `tqsl`, `xwxapt`. |
| `install_gspiceui` hardcodes an `aarch64-linux-gnu` symlink path on every arch | Dangling symlink on x86_64. Use the `arch` selector (**D-002**). |


### Amendment, 2026-10-02 — the text of a plan groups repeated same-shape steps

**Decided:** The plan's *text* collapses a run of steps that share one
template, and `--full` prints every step expanded. Rendering only: the JSON
plan document (D-059), the transaction log and the real run are untouched, and
`--dry-run` stays "complete and accurate, not approximate" because the group
is lossless, not a summary.

**Why:** `ustopo-qmapshack` printed, for every US Topo sheet, a comment, a
full `gdalwarp ... then gdaladdo ...` command and two more lines: hundreds of
near-identical blocks, which is how the one line that differed (a refusal, a
size) went unread. Terrain tiles, contours, SPLAT squares, FSTopo sheets,
Kiwix books and the per-region vector-tile builds have the same shape.

**How:**
- *Generic, from the rendered text, never per unit.* Two steps share a template
  when they are the engine's own in-process steps (a fetch, a convert, an
  install-data) of the same kind and privilege, have the same number of tokens
  and agree on at least half of them (`src/hammunition/interface/plan_group.py`). A command
  never groups: `install -m 0755 a /usr/local/b` then the same for `c` are two
  modifications an operator reads one by one. The tokens that differ are
  the per-item arguments. A repeating unit of up to four steps (fetch, install,
  prune) groups as one block, in execution order. Fewer than four repeats never
  group.
- *Nothing hidden.* A group prints each template once with `<name>`
  placeholders, the first item written out in full, every item's own values
  on a line, and the totals of any size column. A group is kept only if
  rebuilding every step from its template and its item values returns that
  step's text exactly; a run that does not round-trip prints step by step.
- *`--dry-run --full`* (also accepted by a real `install` for its pre-run
  listing) prints every step as the plan always did, byte for byte.
- *What does not group:* steps that differ in anything but arguments (a command,
  another kind, another privilege, another wording such as a verified
  and an unverified fetch among unverified ones), runs shorter than four, and
  steps whose only per-item content is constant (a SPLAT square's output size
  is the same for every square, so there is no size column to total).

**Evidence:** `tests/test_plan_group.py` runs the real backends of
`ustopo-qmapshack`, `dem-qmapshack` (contours), `splat-sdf`, `dem-copernicus`,
`usfs-fstopo`, `kiwix-library` and `osm-pmtiles` on twelve synthetic items each;
all seven group and round-trip.

---

## D-017 — 1.0 is the five-source union, not AHRL parity alone

**Decided:** `docs/SCOPE.md` governs 1.0 scope. **1.0 = Debian Blend + AHRL
parity + 73Linux packet core + Skywave listening delta + DragonOS Tier 1**
(`SCOPE.md` staging 1–5).

**Supersedes** the "1.0 = AHRL parity + packet core" formulation in `CLAUDE.md`
and `DESIGN.md` §14, both reconciled 2026-08-25. **D-008** is unchanged: it
settled the packet-core split, which remains stage 3 of five.

**Evidence gathered since D-008:** the Debian Blend is 152 packages of
team-governed, signed, machine-readable coverage
(`reference/blend-inventory.md`), and **11 of AHRL's 35 source builds are already
packaged there**. Blend-first is not merely cheap — it shrinks the source-backend
problem that D-004 identifies as the core engineering cost. Staging AHRL ahead of
it would have meant building a source backend for software Debian already ships.

**DragonOS is tiered, and the tiers are not one job.** Tier 1 (apt or upstream
`.deb`) is the 1.0 SIGINT profile. Tier 2 is post-1.0. **Tier 3 — GNU Radio
out-of-tree modules — must not be attempted before the source backend and pin
database are solid**, and each module records the GNU Radio version it was built
against. Where nothing maintained exists, document the gap rather than carrying a
fork we cannot sustain.

---

## D-018 — Every external-state claim is tested before it is published

**Decided:** Any claim about the outside world — a service being down, a project
being dead, a package being unavailable — is **tested** before it enters a
document a user might read. Secondary sources establish what to test, never the
conclusion.

**Evidence:** the HamClock case. Three consistent secondary sources — Amateur
Radio Newsline, ARRL Eastern Massachusetts, Amateur Radio Daily — supported the
conclusion that HamClock stopped functioning in June 2026. It was recorded as
settled and written into `dispositions.md`. One `curl` overturned it: the backend
serves version 4.27 with an active changelog.

The claim was wrong in the direction that flattered our argument, which is
precisely when scrutiny is weakest.

**Generalises `PARITY-POLICY.md`'s rule.** That document already forbids
inheriting a `broken` verdict without testing it. This extends the same standard
from package build status to every external fact, and adds: **record what was
not tested.** The HamClock probe used guessed endpoint paths and ran no client
end to end, and the write-up says so.

**Cheap to comply with.** The tests that overturned this were a DNS lookup, a TCP
connect, and two HTTP GETs.

---

## D-019 — Blend task membership is a category, not an install default

**Decided:** A package's presence in a Debian Hamradio Blend task means *"this
belongs to this category."* It does **not** mean *"install this."* Our profiles
import Blend task membership as tagging and decide inclusion separately.

**Evidence:** measured in `docs/reference/blend-inventory.md`. Of 160 task
entries, **155 are `Recommends` and 5 are `Suggests`. There is not one
`Depends`.** The Blend's metapackages are opt-out by construction: `apt install
hamradio-datamodes` pulls every recommendation unless the operator knows to pass
`--no-install-recommends`.

Our profiles are opt-in (**D-003**). Importing task membership as an install
list would make every profile maximal — the exact DragonOS-scale complaint
`SCOPE.md` names — and would do it silently, because the Blend's own metadata
looks like a package list until you read the relation column.

**Second, related finding: "in the Blend" is not "installable."** A probe of all
152 Blend packages inside a `debian:13` container found **8 that do not install
on Debian 13**: `aethersdr`, `dump1090-mutability`, `fbb`, `not1mm`,
`odr-audioenc`, `qlog`, `sdrangel`, `sdrpp`. Seven are present in unstable, so
most of it is ordinary release lag — the Blend tracks unstable and we target
stable. `odr-audioenc` is in neither.

That is 94% coverage on stable, not 100%, and the residual lands on packages we
had already chosen: `qlog` is `overlaps.md`'s recommended logging default, and
`sdrpp` and `sdrangel` are in the Blend's `sdr` task. Per **D-005**, coverage
counts only where it installs.

**Consequences.**

- Profile manifests state their own membership. Blend tasks seed it; they do not
  define it.
- Every Blend package a profile includes is checked against the target before
  the capability matrix claims it.
- `SCOPE.md`'s "cheapest coverage in the project" stands, qualified: cheapest,
  and 94% rather than complete on a stable base.

---

## D-020 — Detected hardware drives profile resolution

**Decided:** Profile resolution consults detected hardware. A profile may declare
packages as *available-not-installed*, selected only when the matching device is
present. This is a structural requirement on M4, not an optimisation.

**Evidence, from two independent sources.** The Blend's `sdr` task is 39
packages, of which **12 are `soapysdr-module-*`** — per-device backends for
airspy, bladerf, hackrf, lms7, mirisdr, osmosdr, redpitaya, remote, rfspace,
rtlsdr, uhd and audio. Skywave Linux ships **the same full set** in its 5.10.0
release, and DragonOS ships it too.

All three do it for the same reason: **a live ISO cannot know what will be
plugged in.** Skywave and DragonOS boot from USB on an unknown machine; the
Blend is a metapackage with no host to inspect.

**We are not in that position.** We run on an installed system with the device
attached, which is the whole premise of the project. Installing eleven backends
for hardware the operator does not own is exactly the bloat all three of those
projects are forced into and we are not.

Measured effect: **11 of the 12 removed from the common case**, from the single
largest profile in the catalog.

**Consequences.**

- The manifest schema needs a way to express "install when this device is
  present" — a hardware selector alongside the existing `distro`, `arch` and
  version selectors (**D-002**).
- `hammunition --dry-run` must show which modules were selected and why, because
  a resolution that depends on hidden state is exactly what the dry-run
  requirement exists to prevent.
- With no device attached, resolution installs `soapysdr-tools` and nothing
  device-specific, and says so rather than failing.
- This generalises past SoapySDR: firmware packages, udev rules and DKMS modules
  have the same shape. It is the same mechanism that makes persistent udev
  symlinks by serial worth building.

**Not a substitute for honesty.** If detection fails or is ambiguous, the engine
reports it and installs the conservative set. Guessing at hardware would be a
silent degradation, which **D-016** forbids.

---

## D-021 — Consent gates disclose a risk category; they never give legal advice

**Decided:** A profile whose lawful use depends on the operator's authorization
is **consent-gated**. Installing it requires an affirmative act that a
convenience flag cannot supply.

### The mechanism

| Requirement | Rule |
|---|---|
| Interactive by default | The gate prompts on a TTY and blocks until answered. |
| `--yes` must not satisfy it | `--yes`/`-y` means *"do not ask me to confirm routine steps."* A gate that a convenience flag walks through is not a gate. |
| Scripted path is separate and explicit | The profile declares its own environment variable, e.g. `HAMMUNITION_ACCEPT_RF_RESEARCH=1`. Nothing else sets it, and setting it is recorded. |
| Recorded | The transaction log stores who affirmed, when, which risk categories were disclosed, the exact disclosure text, and whether it came from a prompt or the variable. |
| Specific | The disclosure names the **risk category**, never a generic warning. |
| No TTY and no variable | Refuse and explain. Never assume consent from silence. |

### What the gate must not do

**It must not tell the user what is legal where they are.** We cannot determine
a user's jurisdiction, their licence class, their employer's authorizations, or
the terms of an engagement they may be operating under. We are not lawyers and
this software is not legal advice.

The gate therefore **discloses and asks**; it does not adjudicate:

- ✅ *"This profile installs software that can cause connected hardware to
  transmit. Transmitting may require a licence or authorization. Do you affirm
  you have the authorization required for how you intend to use it?"*
- ❌ *"Transmitting on these frequencies is illegal without an amateur licence in
  most countries."*

The second sentence is an opinion about law. The first is a disclosure and a
question. **Any wording that reads as legal advice is a defect.** Write it so a
lawyer reading it sees a disclosure, not an opinion — no jurisdictions, no
statutes, no "illegal", no "you may/may not".

The corollary matters as much: **we do not decide for the user either.** A
consent gate that refuses to install because we guessed the user is unauthorized
would be the same error in the opposite direction. The user affirms; we record.

### Risk-category taxonomy

Categories describe **what the software can do**, not what any jurisdiction says
about it. That is what keeps them stable and keeps us out of the advice business.

| Category | The capability being disclosed |
|---|---|
| `unlicensed_transmission` | Can cause connected hardware to emit RF, on frequencies, power levels or modes that may require a licence or authorization. |
| `protected_communications` | Can receive, decode, store or display communications that may be protected from interception. |
| `identifier_collection` | Can collect identifiers associated with people or their devices — IMSI, IMEI, MAC, serial numbers, subscriber records. |
| `third_party_systems` | Can interact with, probe or test systems and networks; doing so needs the owner's authorization. |
| `spectrum_disruption` | Can degrade or deny service to other users of the spectrum, whether or not that is the intent. |
| `credential_recovery` | Can recover, crack or replay authentication material. |

A profile lists every category that applies. `rf-research` under **Q-008** would
carry `unlicensed_transmission`, `protected_communications`,
`identifier_collection` and `spectrum_disruption`.

### Where it applies, and where it deliberately does not

Gates attach to **profiles**, not packages. A gate on every package would train
users to click through, which is the failure mode this exists to avoid — the
prompt has to be rare enough to be read.

`rf-security` as scoped in `profile-sizing.md` — Wireshark, aircrack-ng,
inspectrum, rtl_433 — is **not** gated by this decision on its own. Those tools
ship in Debian and Kali without ceremony and gating them would be theatre.
Gating is for the profile where the capability itself is the hazard.

**This is a mechanism decision, not a scoping one.** Which profiles are gated
follows from **Q-008**, which is open.

### Why a mechanism and not a warning

`--dry-run` already prints every system modification (CLAUDE.md, security
requirements) and the transaction log already records what happened. Neither
records that a human took responsibility. That record is the point: it is what
distinguishes a tool that was used with authorization from one that was not, and
it belongs in the log next to the packages it authorized.


---

## D-014 amendment, 2026-08-26 — cargo tested against its best candidate, and stays at zero

**D-014** records `cargo` at **zero occurrences** and says a backend is added
only when a unit requires it. Rayhunter was the strongest candidate to overturn
that, and it does not.

**Evidence.** `EFForg/rayhunter` is a Rust project — 2.6 MB of Rust, `Cargo.toml`
and `Cargo.lock` at the repository root, GPL-3.0, 5,700 stars, pushed within the
last week. If any unit in scope needed a cargo backend it should have been this
one.

Upstream publishes **prebuilt Linux binaries for x86-64, aarch64 and armv7**, and
the `linux-x64` archive contains the installer, the `rayhunter-check` analyser,
the on-device daemon and its init scripts. Nothing compiles on the user's
machine. The **binary backend, already required for 1.0, covers it completely.**

**cargo stays at zero.** The point of D-014 is that a backend costs maintenance
forever and must be earned by a named unit; the best candidate examined so far
does not need one.

**Second finding, and the more valuable one.** `SCOPE.md` says of the pin/hash
database that *"not one of them publishes checksums we can inherit"* — across
AHRL's 63 archives, 73Linux, Skywave and DragonOS. **Rayhunter publishes a
`.sha256` beside every release asset.** Verified 2026-08-26: the published digest
for `rayhunter-v0.12.0-linux-x64.zip` matches the computed one.

That makes it the first manifest in the catalog that can carry an **inherited**
hash rather than one we pinned ourselves, and it is worth recording which
project made that possible.

**Third, a smaller one.** The installer statically links EFF's fork of the
`adb_client` crate and speaks USB through `nusb`, so deploying to a hotspot needs
**no `adb` package**. The obvious dependency is not a dependency.

See `docs/guides/rayhunter.md`, including what was not tested — no device was
attached and no capture analysed.

---

## D-022 — Displacing software the distribution chose: coexist, disclose, never remove silently

**Decided:** A manifest may offer software that competes with something the
distribution deliberately ships. It may not quietly replace it. Five rules, and
they are general — the VS Code case is the first instance, not the subject.

### 1. Coexistence is the default; replacement is a separate, explicit act

If both can be installed, install both. Wanting package B is not a request to
remove package A, and treating it as one is how a tool ends up making decisions
that were not asked of it. Removal requires the operator to say so, on its own.

### 2. Never remove silently

The displaced package is declared in `conflicts_with_repo_package`, printed by
`--dry-run`, recorded in the transaction log with a `reverse_hint`, and
reversible with one apt command that the documentation states.

**This is the AHRL pattern we exist to fix.** AHRL removes the distribution's
`librtlsdr` to install its own, with no record and no way for the operator to
know it happened. Being newer is no excuse for repeating it.

### 3. A third-party repository gets the full treatment

Declared in the manifest as an `AptRepo`, signing key fingerprint pinned, and
the rationale shown to the operator **before** the repository is added. Already
a security requirement in CLAUDE.md; restated because this is the case that will
tempt someone to skip it.

Adding a vendor's repository is a larger act than installing a package: it grants
that vendor the ability to ship updates to any package name they choose,
forever. The disclosure must say that, not just name the URL.

### 4. State the distribution's reasoning as a reason, not as an obstacle

A distribution that ships B instead of A usually had a reason. Repeat it
accurately and neutrally, then state the counter-argument with equal care.

**Do not editorialise in either direction.** Not "Parrot ships VSCodium for
ideological reasons but most people want real VS Code", and not "VS Code is
proprietary spyware". The operator is choosing for their own machine and needs
the facts, not our opinion. This is the same discipline **D-021** imposes on
consent gates: disclose, do not adjudicate.

### 5. Never the default, never in a base profile

Software that displaces a distribution choice is opt-in, and does not belong in
any profile an operator installs to get started.

### Why this is a decision and not a manifest comment

The first instance is an editor and it feels minor. The pattern is not: it will
recur for `dump1090-mutability` versus `readsb`, for a vendor SDR driver against
the distribution one, for anything where upstream ships a newer build than the
archive. Writing the rule once means the tenth case is not argued from scratch.

**First instance:** `catalog/packages/code.yaml`, offering Microsoft's VS Code
build alongside — never instead of — the `codium` that Parrot ships.

### Amendment, 2026-09-07 — the engine could not see a removal, and now refuses one

Rule 2 was documented and not enforced. `backends/apt.py parse_simulation`
read only the `Inst` lines of `apt-get install --simulate` and its docstring
said so; the install runner was `apt-get install --yes` with no
`--no-remove`. So a package that `Breaks:` an installed one — the archive's
`wsjtx-improved` against `wsjtx`, found while correcting its install notes
(#41, issue #42) — would pass the plan, print no removal under `--dry-run`,
and apt would remove the installed package at the apt step. Measured on a
Kali guest with the archive's `wsjtx 3.0.2+dfsg-2` installed:

```
Remv wsjtx [3.0.2+dfsg-2]
Remv wsjtx-data [3.0.2+dfsg-2]
Remv wsjtx-doc [3.0.2+dfsg-2]
Inst wsjtx-improved-data (3.1.0+260522+repack-1 kali-rolling [all])
```

Three removals, and the engine passed that transcript unchanged.

**What changed.** `parse_removals` reads the `Remv` lines (package and
installed version, architecture qualifier dropped); `AptSimulation` carries
them as `removes`; the plan refuses any transaction whose simulation removes
anything, naming every package with its version, attributing it to the unit
whose `conflicts_with_repo_package` declares it — or, when no unit does,
naming that as the catalog gap — and printing the `apt-get remove` the
operator can run *deliberately*. The apt install step, the vendor-`.deb`
install and the executor's post-fetch simulate all run with `--no-remove`,
so if the real solve ever disagrees with the plan-time simulate apt exits
100 (`Packages need to be removed but remove is disabled`, measured the same
day) rather than removing. The plan-time simulate deliberately runs
*without* it, because the `Remv` lines have to exist to be named.

**Why refuse rather than disclose.** Rule 1 says coexistence is the default
and replacement is a separate act. A `Breaks:` is the case where coexistence
is impossible — apt will not install the one beside the other — so the only
honest shapes are refuse, or remove on the operator's say-so. Removal on
their say-so is `sudo apt-get remove <name>`, which the refusal prints; an
engine flag that removes for them would be `--yes` under another name
(D-021's objection, applied to removal). The refusal keeps the operator's
hand on the one command that takes something off their machine.

**What it unblocks.** Rule 2's mechanism was the reason `wsjtx-improved`
stayed on a vendor `.deb` that cannot install on Kali (issue #24). With
removals seen and refused, an archive `wsjtx-improved` block with
`conflicts_with_repo_package: wsjtx` is exactly the disclosed displacement
this decision describes.

---

## D-023 — Two licences, split on the architectural boundary

**Date:** 2026-08-26. **Status:** accepted. **Closes:** Q-009.

`LICENSE` — **GPL-3.0-or-later**, covering `src/`, `scripts/`, `tests/`, `docs/`
and the repository's own build and CI files.

`catalog/LICENSE` — **CC0-1.0**, covering everything under `catalog/`.

### Why a split rather than one licence

The repository already has an architectural boundary and CLAUDE.md states it as
an invariant: the catalog is data that **must remain usable by an engine that
isn't ours**, and the engine is replaceable software. A single licence would
have to lose one of those two properties.

**Copyleft on the engine is the point.** This project exists because of a
governance problem, not a software problem — AHRL's bus factor of one, 73Linux's
missing licence file, contribution by emailing the maintainer. A permissive
licence would let a fork close the source and reproduce the exact failure mode
the project was founded to answer. GPL-3.0-or-later also matches the ecosystem
this audience already runs: hamlib, fldigi, WSJT-X, GNU Radio, and AHRL's own
installer.

**Copyleft on the catalog would defeat its purpose.** A GPL manifest tree is one
an alternative engine cannot freely consume, which contradicts the invariant
directly. CC0 is not a concession here — it is what the data already is. A
manifest records that `fldigi` is packaged as `fldigi` on Debian and needs
`hamlib` configured first. Those are **facts about the world**, and the thin
copyright interest anyone could claim in an arrangement of them is not worth the
friction it would impose on the thing we most want reused. CC0 removes an
ambiguity rather than making a grant.

### What this does not do

It does not relicense anything the catalog *describes*. Every piece of software
in the inventory keeps its own licence, recorded in
`docs/reference/licence-verification.md`, and CC0 on a manifest says nothing
about the program the manifest installs.

`docs/` is GPL-3.0-or-later by default rather than by argument — it falls under
the repository licence because nothing said otherwise. CC-BY-4.0 would be a
defensible refinement for prose and is not worth a third licence today. The
generated reference under `docs/packages/` is derived from CC0 manifests, which
constrains nothing, since CC0 imposes no conditions to inherit.

### Mechanics

SPDX headers per the REUSE specification: `SPDX-FileCopyrightText` and
`SPDX-License-Identifier` on every source and manifest file, `REUSE.toml` for
formats where a comment is unwelcome, and verbatim texts under `LICENSES/`.

The texts are **copied from Debian `base-files`** (`/usr/share/common-licenses/`)
rather than transcribed, and their checksums are recorded in `REUSE.toml`:
GPL-3 `8ceb4b9e…65b903`, CC0-1.0 `a2010f34…cf0499`. A licence reproduced from
memory is a licence with an unknown diff in it.

`tests/test_licensing.py` asserts every file carries the identifier its tree
requires, so a new manifest cannot arrive unlicensed and a new engine module
cannot arrive under CC0 by copy-paste.

### The reason this could not stay open

D-001 declines to build on 73Linux because it ships no licence file. Q-007 flags
SuperSDR for the same thing. Publishing a public repository in that state while
making that criticism twice in the decision record is not a position that
survives anyone reading both documents. `why-hammunition.md` now answers it in
the same document that raises it.

### Still open

Whether contributions carry a DCO sign-off or a CLA. Recommendation stands from
Q-009: **DCO, not a CLA** — a CLA is a barrier to exactly the drive-by manifest
contributions this project wants.

### Amendment, 2026-08-26 — the holder, and no CLA

Both of the "still open" items above are closed.

**Copyright holder: `Copyright (C) 2026 Renegade Penguin LLC`** (Q-012). An LLC
is a legal person and can enforce a licence; a handle cannot. It also keeps the
maintainer's legal name out of a public repository. Applied to every SPDX header,
`REUSE.toml`, `CONTRIBUTING.md` and the README footer — and deliberately **not**
to `LICENSE` or `catalog/LICENSE`, which are verbatim texts whose checksums are
asserted; a copyright line inserted into a licence corrupts it.

**No CLA and no copyright assignment.** Contributors keep copyright on their own
work, licensed under GPL-3.0-or-later (CC0-1.0 in `catalog/`) by the act of
contributing. This is the ordinary GPL arrangement and is written into
`CONTRIBUTING.md` because a company name in the headers invites the opposite
assumption. A CLA would be a barrier to exactly the drive-by manifest and `lsusb`
contributions this project most wants.

---

## D-024 — A commit pin carries no upstream signal, so it carries ours

**Date:** 2026-08-26. **Status:** accepted. **Resolves:** Q-013, as general
policy rather than as one manifest.

**Rule.** Where upstream has stopped tagging, pin a commit SHA — never a branch,
never a rolling release artifact. A SHA pin **must** carry a `pin_review`
recording `last_reviewed`, `reviewed_by`, a `rationale` for *that* commit, and a
`cadence_days` after which it must be looked at again. A tag must **not** carry
one.

### Why the field, and not just a convention

A tag carries an upstream signal: someone decided that revision was worth
naming. A commit SHA carries none. It is perfectly pinned and perfectly
arbitrary.

That makes the two failure modes symmetric, and both are "nobody looked":

| | |
|---|---|
| **An abandoned tag** | SDR++'s newest release tag is `1.0.4`, 2021-10-18. Master moved in July 2026, 541+ commits later. Pinning the tag ships a five-year-old program nobody runs. |
| **An unreviewed commit** | Fully pinned, fully reproducible, and in four years indistinguishable from the case above. |

Pinning a commit is the right answer to a project that stopped tagging, and it
**moves a judgement upstream stopped making onto us**. Recording that judgement
is what separates a pin from a guess that happens to be reproducible.

### Enforced, not encouraged

`GitInstall` rejects a SHA with no `pin_review` and rejects a tag that has one —
the second because a review on a tag would read as though someone vetted a
revision choice that upstream actually made. `rationale` has a minimum length
because *"HEAD at the time"* is the absence of a rationale rather than a short
one.

**Staleness is checked on a schedule, not on every push.** Whether a pin is
well-formed is a property of the code, asserted in tests that run on every
commit. Whether it is stale is a property of the calendar. Failing an unrelated
pull request because a date rolled over would teach people to ignore the job,
which is the one outcome that makes the mechanism worthless.
`scripts/check_pin_reviews.py` runs weekly in CI and prints what to do.

Its instructions end with the part that matters: **do not bump `last_reviewed`
without reading upstream's log and testing any move.** A date bumped to silence
a check certifies nothing, and would make this worse than having no field.

### The preferred method: check what the distributions pin first

**Before choosing a commit, look at what packages it.** If a distribution ships
a git snapshot, pin *their* commit.

This is not a tiebreaker, it is the main rule, and the reasoning is stronger
than "someone else looked":

1. **It is the review signal upstream stopped providing.** A Debian, Kali or
   Parrot maintainer picked that revision, built it, and shipped it to users who
   would complain. That is a vetting process we do not have and cannot cheaply
   reproduce.
2. **It collapses two revisions into one.** A user who installs from apt and a
   user who builds from source end up running the same code. Without this they
   run different programs under the same name, and a bug report from one does
   not transfer to the other.
3. **Independent agreement is evidence.** Kali and Parrot both landed on
   SDR++ `36ea9a1`. Two maintainers hitting the same missing-tags problem and
   answering it the same way is a stronger signal than either alone.

Recency is not a reason. Master HEAD is newer and nobody has vetted it.

**Choosing our own commit is legitimate and more expensive.** When nothing
packages the project, `basis: own_choice` is correct — and the rationale must
then say *which distributions were checked and what they ship instead*, so the
next reviewer can see whether that has changed. The schema enforces the
difference: `distribution_pin` must name the distributions, `own_choice` must
not name any and needs a fuller rationale.

`scripts/check_pin_reviews.py` prints the basis on every line and flags
own-choice pins with a note to re-check whether anything packages them now.

### First instance — `catalog/packages/sdrpp.yaml`

Apt on Kali and Parrot; a reviewed SHA everywhere else.

`basis: distribution_pin`, `distributions: [kali, parrot]`. Both package SDR++
as a git snapshot at `36ea9a1`, two commits behind master, and taking theirs is
worth those two commits for every reason in the section above.

Same reasoning as `proxmark3.yaml`, which pins `v4.21611` because that is the
release Kali packages, and where a client/firmware mismatch would otherwise be
silent.

**Explicitly not used:** SDR++'s `nightly` release assets. A URL that never
changes with an artifact behind it that does, no version, no published checksum.
`RemoteArtifact` requires a `sha256` and the reason to have a mandatory field is
that it does not bend when bending would be convenient.

---

## D-025 — A claim gets re-verified when it becomes decisive, not only when gathered

**Date:** 2026-08-26. **Status:** accepted.

**Rule.** Gathering standards and decision standards are different bars. A fact
collected in passing may be inherited, cited or estimated. **The moment a claim
is promoted to decisive for a decision, it is re-verified against a primary
source, and the verification is dated in the document that relies on it.**

### The four instances that produced this

Four bugs, one shape: *something was checked once, in a narrower context than
the one it ended up carrying.*

| | What happened |
|---|---|
| **HamClock** | Three secondary sources said it would stop working in June 2026. Never probed. Written into `dispositions.md` as evidence for our own argument. It was live at 4.27. |
| **`check_doc_links.py`** | The checking tool, unchecked. It skipped `docs/reference/` and reported success over seven files it never opened. The first regression test reimplemented the bug and passed. |
| **`src/hammunition/state/`** | Written, tested, type-checked, never committed. mypy, pytest and ruff all read the working tree; only git read the index. |
| **Kali `proxmark3`** | A narrow probe became the decisive argument in Q-010 without re-verification. Kali packages it. |

The first and fourth are the same error at different scales. The second and
third are its reflexive form: *the instrument was never pointed at itself.*

### What this actually requires

Not "verify everything", which is unaffordable and would mean verifying nothing.
Three concrete obligations:

1. **When a claim becomes load-bearing, re-check it then.** The trigger is
   promotion, not age. A fact that was fine as background becomes a different
   kind of object when an argument rests on it.
2. **Date the verification in the document that relies on it**, so the next
   reader can see how old the support is without going looking. This is already
   the house style in `docs/reference/`; D-025 makes it a rule.
3. **Point every checking tool at itself.** A checker gets a test that fails
   when its own bug is reintroduced — verified by reintroducing it, not by
   assuming. `scripts/audit_gitignore.py` was written this way and both
   historical bugs were re-added to confirm it catches them.

### The failure this does not prevent

A claim that was true when verified and became false afterwards. Dating the
verification is what makes that recoverable rather than invisible: a reader can
see the support is two years old and go looking. An undated claim gives them
nothing to be suspicious of.

### Relationship to D-018

D-018 says external claims are tested before published — it governs what we say
outward. D-025 governs what we let ourselves rely on inward. The HamClock
retraction produced the first; Q-010's retraction produced the second, and
should have been prevented by it.

---

## D-026 — We install tooling for a device; we do not install the device's capability

**Date:** 2026-08-26. **Status:** accepted.

**Rule.** A manifest that installs the means of *talking to* a device — a
flasher, a serial console, a udev rule, a driver, a configuration client — is
neutral tooling and is not consent-gated, **regardless of what the device can
do once it is running**. Firmware that comes from upstream and executes on the
device is not something this project installs, and treating it as though we did
would be a claim we cannot support.

### Why this needs stating

Without it, every flasher becomes a gating argument. `esptool` writes an image
to an ESP32; some of those images do things that fall squarely inside the D-021
taxonomy. If the flasher inherits the gate, then so does `tio`, because you can
drive the same firmware over a serial console — and so does `screen`, and so
does `usbutils`, because enumeration is the first step of everything.

That is the reductio, and it lands somewhere worse than "too many prompts": a
gate that appears in front of routine software is one people learn to dismiss,
which is exactly what would make the `rf-research` gate useless at the moment it
matters. **D-021's gates work only because they are rare.** Diluting them is not
a cautious error.

### Where the line actually falls

| | Gated |
|---|---|
| Installing `esptool`, `tio`, a udev rule, a driver | **No** — this is how a computer talks to a peripheral |
| A package whose own function is a capability in the D-021 taxonomy — `gr-gsm` decoding cellular signalling on the host | **Yes** |
| Firmware fetched from upstream and run on the device | **Not installed by us at all**, so there is nothing to gate |

The test is *what does the thing we install do on the machine we install it on*.
`gr-gsm` decodes cellular signalling on the host; that is the capability, and
`rf-research` gates it. `esptool` copies bytes to a serial port.

### Applied

**ESP32 Marauder firmware**, as run by boards such as the C5 Wardriver — it
includes active features (deauthentication,
beacon spam, captive-portal impersonation) that are transmit-side under the
Q-008 tiering. Hammunition installs `esptool` and a serial console. It belongs
in `rf-security`, ungated. This decides the WiFi Pineapple and the USB Rubber
Ducky identically, which is the point of writing it as a rule.

### What the documentation must still do

Neutral tooling is not silent tooling. The device entry states plainly what the
hardware does, including the active features, so nobody discovers them by
surprise. It also says that operating those features against networks you do not
own or are not authorised to test is a separate matter from installing a
flasher.

That sentence is deliberately about *what is being installed*, not about what is
lawful. **Same discipline as D-021: describe capability, do not adjudicate
legality.** The `ConsentGate` wording validator exists because that line is easy
to cross by accident, and it is just as easy to cross in prose.

---

## D-027 — "Supported" and "we have run it" are separate claims

**Date:** 2026-08-26. **Status:** accepted.

**Rule.** A device manifest carries two independent fields:

| Field | The claim |
|---|---|
| `status: supported` | The identifiers are correct and the setup recipe works. |
| `maintainer_verified` | Somebody on this project plugged the hardware in. |

Neither implies the other, and the generated capability reporting shows both.

### Why they must not be one field

`usrp` forced the distinction. Its seven USB identifiers come from Debian's own
`60-uhd-host.rules` — a primary source, maintained by people who ship the driver
— and every rule generated from them will match. That is a real, useful,
evidenced claim. **Nobody on this project owns a USRP.**

Collapse the two and one of two bad things happens:

- **Require hardware for `supported`** and we throw away good evidence. The
  entry would have to say "untested" while holding a citation to the
  distribution's own rule file, which is worse information than the truth.
- **Let `supported` imply verification** and we have claimed support we never
  tested. That is the exact failure D-018 exists to prevent for external claims
  and D-025 for internal ones, applied to hardware.

Two fields cost one column in a table. The alternative costs either evidence or
honesty.

### Not a boolean

`maintainer_verified` is a record, not a flag: date, who, which distribution, and
what actually happened. A bare `true` would be a claim with no evidence behind
it — the same defect one level down, and the reason `UsbId.evidence` and
`PinReview.rationale` exist. *"It works"* fails the minimum length on purpose;
*"enumerated, rules matched, `rtl_test` found the tuner"* is a test result.

Two contradictions are rejected outright: a verification alongside
`gap_closure: unverified_by_maintainer`, and a verification on `status: planned`.
Somebody either ran the hardware or did not.

### What it looks like today

**6 of 20 devices claim `supported`. 0 have been run here.**

That is printed at the top of `docs/reference/hardware-gaps.md`, and the gap is
not a defect to be closed by relaxing either column. It is the honest state of a
project whose hardware layer is built out of distribution udev rules, and saying
so is the point.

### Relationship to `gap_closure`

Three fields now describe a device's evidential position, and they are genuinely
orthogonal:

- `status` — are the identifiers and recipe right?
- `maintainer_verified` — has anyone here run it?
- `gap_closure` — if something is unknown, who could find out?

`usrp` is `supported`, unverified, with no gap. `catsniffer-v3` is `untested`,
unverified, with a gap closable on this bench. `limesdr` is `untested`,
unverified, with a gap closable only by an owner. Each combination means
something different to a user deciding whether to buy the hardware.

---

## D-028 — An identifier that names a chip may not name a `/dev` node

**Date:** 2026-08-26. **Status:** accepted.

**Rule.** A USB identifier that names a *bridge chip* or a *function* rather
than a product cannot be the sole basis for a device-specific udev symlink. A
rule resting on one must also carry `match_product` or `match_serial`, or emit
no symlink at all. Enforced by `DeviceManifest`, `DeviceClass` and
`load_hardware`, not by review.

### The failure, which we had already shipped

The `badgelife` class emitted `/dev/badge-<serial>` for every identifier it
carried. All of them are bridge chips: the kernel binds `10c4:ea60` to `cp210x`
and `1a86:7523` to `ch341`, and `303a:1001` is Espressif's chip-level constant.

So the rule claimed **every CP2102 adapter on the machine** — a rig-control
cable, a GPS puck, a Meshtastic node — after whichever badge it was written for.
The operator gets a symlink pointing at the wrong hardware and no error
anywhere in the chain.

**That is the `rtl-sdr` failure pointed the other way.** There, three
identifiers where Debian had 42 meant a Hauppauge stick got no symlink and no
error. Here, one identifier covering a whole chip family means somebody else's
device gets *our* symlink. **Under-matching is silent and over-matching is
silent**, so neither can be left to review — the same argument that made
`method: script` unrepresentable and `sha256` mandatory.

### Evidence, not opinion

`catalog/hardware/ambiguous-ids.yaml` is generated from two measured sources by
`scripts/gen_usb_ambiguity.py`:

| Basis | Source |
|---|---|
| `kernel_generic_driver` | The kernel's own `modules.alias`, generated by `depmod` from the module tree. A pair in `cp210x`'s or `ftdi_sio`'s table is one the kernel maintainers put in a *bridge* driver. |
| `shared_across_products` | The archive-wide udev sweep found the pair in two or more packages' rules **under different device names**. `0483:df11` is in `qflipper`'s rules and in `dmrconfig`'s, where it is a TYT MD-UV380. |

Two further bases exist for cases no probe reaches and are recorded by hand:
`vendor_chip_default` (Espressif's `303a:1001`, which esptool calls
`USB_JTAG_SERIAL_PID`) and `generic_function_name` (`usb.ids` naming a function
— "Virtual COM Port", "CP210x UART Bridge").

**`303a:1001` stopped being an inference on 2026-08-26.** Three unrelated
products were captured on one machine on one day — a Clip-Boy, a Minino, and the
ESP32-S3 inside a Free-WiLi 2 — and all three report the identical vendor,
product **and product string**: `Espressif` / `USB JTAG/serial debug unit`. Only
the serial differs, and a serial is per-unit.

Three observations beat any argument from a vendor constant, and they settle the
design question underneath the rule: **none of those three devices can carry a
catalog-wide symlink**, because no attribute a rule could match on distinguishes
one from the other two. Their MAC-address serials *are* distinct, so
`/dev/serial/by-id/` separates them — the mechanism systemd already ships works
here and ours would not.

**Deliberately over-inclusive.** A pair in a bridge driver's table is *not*
automatically generic: vendors buy identifier blocks from FTDI and Silicon Labs,
so many are device-specific. The discriminator is what `usb.ids` calls it — a
product name means a vendor bought an id, and **777 pairs are excluded on that
basis**. An *unknown* name counts as chip-like, because the two error directions
cost very different amounts: a false positive costs one `match_product` line in
a manifest, a false negative costs a symlink silently naming the wrong device.

The list is enforced at load: an identifier on it, carried without an
`ambiguity` block, fails the catalog. Without that the downstream symlink check
keys off a block nobody wrote, and passes.

### The corollary — where an ambiguous identifier is still correct

An ambiguous pair is the *right* thing to match on for **permissions** and for
**firmware tools**, because there the operator has already chosen the device. It
is unsafe only as a name in `/dev`, where the kernel matches whatever is
attached.

Stated as a rule so it is not re-derived per entry:

> **Identifiers that select hardware belong in `usb_ids`. Identifiers that
> describe a mode the operator deliberately enters belong in `firmware`.**

`flipper-zero` records `0483:df11` under `firmware` for exactly this reason: as
a DFU target it is correct, and as a symlink rule it would name a TYT radio.

### What replaces the symlink

For USB-serial devices, **the kernel already solved this**. systemd's
`60-serial.rules` populates `/dev/serial/by-id/` from the manufacturer, product
and serial strings in the descriptor, per unit, with no help from us. A board
carrying a serial is already distinguishable there.

That narrows what our own symlinks are *for*, and it is worth being honest that
this is a reduction in scope for the hardware role: **an operator-chosen role
name** — `/dev/rig-991a` is more memorable than any by-id path — which belongs
in station-local configuration with a `match_serial` for that unit, not in a
catalog-wide class rule. For libusb devices such as SDRs, which get no `/dev`
node at all, udev rules were always about *access* rather than naming.

### Consequences applied

- `badgelife` emits no symlink; all four bridge identifiers carry `ambiguity`.
- `flipper-zero` emits no symlink: `0483:5740` is `usb.ids`' "Virtual COM Port",
  ST's reference identifier. One `ATTRS{product}` capture would fix it, and
  writing one we have not read would be guessing.
- `nfc-reader` keeps its symlink: `pn533_usb` is a device driver, not a bridge
  driver. The rule distinguishes them by an explicit list, because `pn533_usb`
  also ends in `_usb` and getting that backwards would suppress a valid symlink.


### D-028 amendment, 2026-08-27 — a distribution says it out loud

The strongest evidence for this decision was not ours and had been sitting in
the archive the whole time. Debian ships `gpsd`'s `60-gpsd.rules` with **five
identifiers commented out**, each under the line:

```
# !!! rule disabled in Debian as it matches too many other devices
```

They are `0403:6001` (FTDI FT232), `10c4:ea60` and `10c4:ea71` (Silicon Labs
CP210x and CP2108), and `067b:2303` (Prolific PL2303) **twice**. Two of those
are identifiers this project had already had to stop claiming, for the same
reason pointed the other way: `10c4:ea60` is what `badgelife` was naming
`/dev/badge`, and `0403:6001` is the pair D-028's opening paragraph uses as its
example. A distribution maintainer reached this conclusion independently, about
the same silicon, and acted on it in a file they ship.

The same rules file opens with the GPSD project stating the principle outright,
in 2010:

> GPSes don't have their own USB device class. They're serial-over-USB devices,
> so what you see is actually the ID of the serial-over-USB chip.

**What this changes.** `AmbiguityBasis` gains `distribution_disabled`, ranked
above every existing basis, because it is not an inference from a driver table
or a name — it is a maintainer's conclusion, with their reason attached.
`scripts/udev-sweep.sh` now extracts commented-out rules deliberately rather
than discarding them as comments.

**Corrected 2026-08-27, same day.** This paragraph first read "13 identifiers
across five packages — `gpsd`, `dfu-util`, `argyll`, `ponyprog` and `knxd`" and
said `dfu-util` disables `0483:df11`. Both were wrong, from reading a sweep row
instead of opening the file. dfu-util's rule for `0483:df11` is **live**, as
`TAG+="uaccess"`; what is commented out below it is an alternative `plugdev`
form offered "on older systems". ponyprog's are CH341 modes it does not use and
knxd's is `dead:beef`.

The distinction was already in the data and went unused: **only a
commented-out rule with a stated reason is evidence.** The generator now
requires one, which is the difference between a maintainer's judgement and a
line of documentation. Archive-wide the honest figure is **5 rows, 4 distinct
identifiers, all in `gpsd`** — `0403:6001`, `10c4:ea60`, `10c4:ea71` and
`067b:2303` twice. Fewer, and every one of them a bridge chip, which is the
claim that mattered.

That this correction is D-031's own failure mode, made in the commit that
recorded a different instance of it, is not a coincidence worth softening: the
check catches commit messages, and nothing catches reading a column and
believing it.

`gps-receiver` carries those five in `rejected_ids` with Debian's own reason,
which is what that field was built for one decision earlier.

**A bug the same file exposed.** The sweep attributed the wrong description to
`1546:01a9`, calling a u-blox 9 a Silicon Labs CP210x. The extractor rejected
any comment of 60 characters or more as boilerplate — the u-blox line is 73 —
and then *kept the previous comment* rather than clearing the field. 156 of
2,750 rows carried a description that long. A rejected comment now clears it:
no description is honest, someone else's is not.

---

## D-029 — The hardware layer is permissions and mapping; stable naming is mostly solved

**Date:** 2026-08-26. **Status:** accepted. **Supersedes** the claim in
`DESIGN.md` §9 that persistent udev symlinks are "the highest-value single
feature in the project", and the same claim in `CLAUDE.md`.

**Rule.** The hardware role's stated purpose is **permissions, composite-device
mapping, firmware-mode identification, and honest documentation of the cases
nothing solves.** A udev symlink is one tactic among those, used where evidence
supports one — not the headline. Every device records what kind of interface it
presents (`node_kind`), and `scripts/gen_device_naming.py` keeps the accounting
current in `docs/reference/device-naming.md`.

### What forced it

D-028 already conceded, at the end, that systemd's `60-serial.rules` populates
`/dev/serial/by-id/` from the descriptor strings with no help from us, and
called that "a reduction in scope for the hardware role". It did not count.

The Proxmark3 capture is what made counting necessary, and it cuts the other
way from what the concession implied. `2d2d:504d` is proxmark.org's own
registered vendor identifier, so the pair is *unambiguous* — D-028's problem
does not apply. What the device supplies is nothing else: no product string and
**no serial**, byte-identical descriptors across two different boards. by-id
composes its path from manufacturer, product and serial, so two Proxmarks
collide *there* exactly as they would under a naive symlink. Only
`/dev/serial/by-path/` separates them, and by-path is topology: it changes when
the operator moves the cable.

So the honest conclusion was neither "by-id wins" nor "symlinks win". It was
that **neither mechanism solves the identical-device case**, and what we can
offer is the documentation that says so, in the entry's known-problems where
somebody with two boards will find it.

### The accounting

Generated, not asserted. 21 devices in `catalog/hardware/devices/`:

| | Devices |
|---|---|
| by-id covers every confirmed identifier | 5 |
| covers some identifiers and not others | 3 |
| covers none at all — nothing they present is serial | 9 |
| not yet recorded either way | 4 |
| **by-id insufficient for at least one reason** | **17 of 21** |
| carry a udev symlink from this catalog | 5 |
| …of which duplicate a path by-id would have given anyway | **0** |

The last row is the finding, and it was not designed for: every symlink written
so far is on a libusb device that systemd's *serial* rule never sees. The two
mechanisms have not overlapped once. Nothing in the catalog is redundant, and
nothing in it was the main event either.

### What by-id does not give

- **Permissions.** A device only root can open is unusable however stable its
  path. This is what actually stops people, and by-id does nothing for it.
- **Non-serial devices.** Every SDR here, the Ubertooth, and the Proxmark in
  client mode are libusb devices with no `/dev/serial/` entry at all. 12 of 21.
- **Identical units.** The Proxmark case above.
- **Knowing which interface is which.** The Free-WiLi 2 presents four CDC ports
  on one interface. by-id gives each a stable path and labels none of them; a
  stable path to a port you cannot identify is not an answer.

### Consequences applied

- `UsbId` records `node_kind`, `ports`, `port_roles`, `product_string` and
  `reports_serial`. Only the first is required to answer the accounting; the
  rest are what make an answer *useful*.
- **`composite: true` is a declared shape.** Declaring it obliges every
  identifier to say what kind of interface it is, because the point of the
  declaration is answering "which of these is the debug probe" — a question
  by-id structurally cannot answer and a catalog can.
- **`port_roles` takes every port or none.** A partial map reads as a complete
  one.
- **A `match_product` must equal a `product_string` some identifier records
  having read.** `hackrf-one` failed this the moment it was enforced: it shipped
  `match_product: HackRF One` on the strength of nothing, the maintainer owning
  a Pro. Guessing a product string is the mirror image of guessing a VID:PID and
  is just as silent. Closed by mining upstream's USB descriptor, not by asking
  for hardware.
- `DESIGN.md` §9, `CLAUDE.md` and `why-hammunition.md` are rewritten to lead
  with access and mapping rather than with symlinks.

### What this does not change

Symlinks stay. An **operator-chosen role name** — `/dev/rig-991a` beats any
by-id path nobody memorises — remains worth having, and for a libusb device a
symlink is the only stable name there is. What changes is that a symlink now
has to earn its place against a mechanism that already exists, rather than being
assumed to be the deliverable.

---

## D-030 — Evidence flows upward: a class carries only confirmed identifiers

**Date:** 2026-08-26. **Status:** accepted.

**Rule.** A `DeviceClass` may carry **only identifiers confirmed against
hardware or cited to a distribution rule**. Devices contribute identifiers
upward into a class; a class never predicts them downward. Enforced by
`DeviceClass`, not by review. Negative evidence lives in `rejected_ids`, which
cannot generate a udev rule and cannot be inherited.

### Two misses, one shape

`badgelife` was written to generalise: ESP32 badges all need a serial console,
a flasher and rules for native USB plus the bridge chips older designs use.
Build the class once and every badge works. That reasoning is sound and the
class still exists. What was wrong was the *direction* of the inference.

Two boards were flashed and captured, and the class had mispredicted both:

| Device | Class predicted | Hardware presented |
|---|---|---|
| CatSniffer v3 | An ESP32 behind a bridge chip | `2e8a:00c0` — a bare RP2040, no ESP32 in it at all |
| C5 Wardriver v1.1 | Espressif native USB, `303a:1001` | `1a86:55d3` — a WCH CH343 bridge |

Neither is a near miss that better guessing would have caught. The CatSniffer
left the class entirely. The C5 Wardriver landed one hex digit from an
identifier the class was *already carrying* on reputation — `1a86:55d4`, "widely
reported" for the CH9102F — which is the worst possible outcome, because a rule
built on `55d4` would silently never match and look exactly like a bad cable.

Two misses is a pattern, not bad luck. An unconfirmed identifier in a class is
not an isolated guess: **it is a guess with a distribution mechanism**, inherited
by every device that joins.

### The C5 capture's real lesson

The stated reason for waiting until the board was flashed was that an unflashed
ESP32 sits in ROM bootloader mode and presents a different identifier. That was
not the payoff. The payoff is that this board never presented `303a:1001` in any
mode — so a pre-flash capture would have been read as *confirming* a false
assumption rather than exposing one. The discipline was right for a better
reason than the one given for it.

### Why `rejected_ids` rather than deletion

Deleting `1a86:55d4` would lose the finding, and the next person would re-add it
from the same forum posts. Keeping it in `usb_ids` with `confirmed: false` keeps
it able to generate a rule and be inherited. So it moves to a field that can do
neither, alongside what it was assumed to be and what it cost to find out. It is
kept as the worked example, and it is **the last identifier this class will
carry on report alone**.

### Consequences applied

- `DeviceClass` refuses any `usb_ids` entry with `confirmed: false`.
- `badgelife`'s six remaining identifiers each name the capture or the Debian
  source they came from; `1a86:55d4` is in `rejected_ids`.
- `test_unconfirmed_identifiers_are_visibly_unconfirmed` has now been pointed at
  three worked examples and lost two of them, which is the healthy direction. It
  no longer asserts that any unconfirmed identifier exists anywhere — an empty
  list is the goal state, not a reason to keep one around to satisfy a test.

---

## D-031 — Verify the effect, not the exit status

**Date:** 2026-08-26. **Status:** accepted.

**Rule.** A tool reporting success is not evidence that the tool did anything.
Before claiming a change, **check the artefact you meant to change**, and where
the claim is made in a commit message, let a hook check it rather than a person
remember to. `scripts/check_commit_claims.py` runs as a `commit-msg` hook and in
CI.

### Three bugs, one shape

| | What happened | What was read instead |
|---|---|---|
| The D-028 amendment | A `sed` anchor matched text that did not exist, so the edit no-oped. The commit message described a change the commit did not contain. | `sed`'s exit status, which is always 0, and a `grep` count checked *after* committing |
| The udev sweep | `dpkg-deb -x` writes files and *then* exits non-zero under rootless podman, so `\|\| continue` fired after the write. All 280 packages logged "bad archive" while 2,750 rows were emitted. | The row count. The log was never opened. |
| The `state/` directory | Written, and never committed, because a `.gitignore` pattern matched it. Nothing errored anywhere. | Nothing — that is the point. Absence of an error was taken as presence of a file. |

D-025 already says a claim gets re-verified when it becomes decisive. This is the
narrower and more embarrassing case: **verifying your own writes**, at the moment
you make them. The distance between them is the distance between "was this still
true a month later" and "did this happen at all".

### What the hook checks

Three things, chosen because each maps to one of the bugs above:

1. **Restated decisions.** A message asserting what a decision *says* — "D-028
   no longer rests on an esptool constant" — must touch that decision's own
   section. A mention of `D-028` elsewhere in the diff does not count, and that
   distinction is load-bearing: the first version of this check passed the very
   commit it was written for, because that commit added schema docstrings which
   happen to say "D-028" while never touching the decision.
2. **Claimed paths.** A message saying it adds
   `catalog/hardware/devices/minino.yaml` requires that path in the commit.
3. **Phantom paths.** Any path the message names that exists on disk but is
   neither tracked nor staged. That is the `state/` bug precisely, and it is the
   one no other tool reports.

Ordinary citation is deliberately untouched: "per D-014" asserts nothing about
the diff and does not fire. A check that flags correct behaviour gets switched
off, which would cost more than the bug it prevents.

### Verified by reintroducing the bug

The check was run against this repository's own history and refuses `717ba26`,
the commit whose amendment silently matched nothing, while passing every other
commit around it. The same method as `scripts/audit_gitignore.py`, and for the
same reason: **a checker that has never been shown to catch the thing it was
written for is itself an unverified claim.**

### The second half, added 2026-08-27

The commit above shipped with a gap stated in its own message: the hook catches
a message describing work a commit does not contain, and **nothing catches
reading a column and believing it.** One commit later that gap produced exactly
the predicted failure — a D-028 amendment asserting that `dfu-util` disables
`0483:df11`, drawn from a sweep row, with the rules file never opened. It does
not; the rule is live as `TAG+="uaccess"`.

`scripts/check_rule_citations.py` closes it for the case where these claims are
load-bearing. Most of the hardware catalog's identifiers cite a shipped rules
file by name, which makes the claim checkable against the same measurement it
came from:

- an identifier citing `60-gpsd.rules` must appear in a file of that name;
- a `rejected_ids` entry saying the distribution disabled a rule must match a
  sweep row that is commented out **and carries a reason**;
- an identifier carried in `usb_ids` must not be one that was commented out;
- and `basis: distribution_disabled` is checked against the whole archive rather
  than against whichever file the prose happens to name.

90 identifiers across 15 rules files, all verified. It runs in the test suite,
and weekly in CI — the sweep is 280 packages and ~264 MB, too expensive per
push, and failing an unrelated pull request because a Debian upload changed a
rule is how a check gets ignored.

**Falsified before being trusted**, and the first version failed that: citing
the wrong file went red and reintroducing the original `dfu-util` claim went
red, but *claiming a live rule was disabled* stayed green. Most of
`gps-receiver`'s `rejected_ids` say "disabled by Debian" without naming a file,
and the check only looked when a file was named — a hole precisely where the
claims are. Fixed, re-falsified, all three red.

**What it does not cover**, said plainly: prose in `docs/`. The wrong claim
appeared there too, and a check that pattern-matched English would be the kind
nobody trusts. The catalog is where a claim becomes load-bearing — it generates
rules — and that is where this is enforced.

### Enabling it

```
git config core.hooksPath .githooks
```

Not enabled automatically — git will not run hooks from a cloned repository, by
design, and a project that works around that is asking contributors to execute
code on clone. CI runs the same script over every commit in a pull request, so
the check is enforced whether or not a contributor opts in locally.

---

## D-032 — Upstream liveness is the default branch's head commit, never GitHub's activity fields

**Date:** 2026-08-28. **Status:** accepted.

**Rule.** When the catalog or an inventory states that an upstream project is
active, dormant, or dead, that claim is measured from **the newest commit on the
project's default branch**. GitHub's `updated_at` and `pushed_at` are not
evidence of development and must not be published as if they were.

```
gh api repos/<owner>/<repo> --jq .default_branch
gh api "repos/<owner>/<repo>/commits?sha=<branch>&per_page=1" --jq '.[0].commit.author.date'
```

### What the two rejected fields actually mean

| Field | Moves when | Why it is wrong here |
|---|---|---|
| `updated_at` | **Somebody stars the repository.** Also on a fork, a description edit, a topic change. | It measures attention, not work. A dead project that gets discovered looks freshly maintained. |
| `pushed_at` | A push to **any** branch, including a fork's. | The near-miss. Overstated 5 of the 9 upstreams in the Skywave delta. |

### What it cost

`skywave-inventory.md` published an "active (date)" column built from
`updated_at`, and `QUESTIONS.md` posed a maintainer decision on top of it:

| Unit | Published as | Actual last commit | Gap |
|---|---|---|---|
| Kalibrate-RTL | active (2026-08-19) | 2022-02-01 | 4.5 years |
| SuperSDR | active (2026-02-18) | 2022-12-31 | 3.7 years |
| directKiwi | last touched 2025-10-09 | 2023-03-03 | 2.5 years |

Both corrections changed something downstream. Kalibrate-RTL has also **never cut
a tag**, so its manifest needs a commit pin and a `pin_review` (**D-024**) rather
than the tag the "active" reading implied. And **Q-007** — whether to carry an
unlicensed KiwiSDR client — was asked with "upstream is active" in its premise;
on the corrected dates the recommended option carries software that is both
unlicensed *and* three and a half years stale, while the option previously
dismissed as "no improvement" is the only maintained client of the three.

The irony is recorded because it is the useful part: the paragraph immediately
below that table claimed the findings were *"tested against the repositories
rather than taken from GitHub's metadata"*. The prose asserting the discipline
sat directly beneath a table that had abandoned it.

### Relationship to the neighbours

**D-018** says an external claim is tested before it is published. **D-025** says
a claim is re-verified when it becomes decisive. This is the third face of the
same coin and the one neither covers: *the field you measured was never the field
you wanted*, so testing it again the same way would have confirmed the error.
When a metric is a proxy, name what it actually counts before publishing it as
what you meant.

**Consequence.** Any generator or document asserting upstream health records the
method beside the number, so the next reader can tell what was counted.
`gen_skywave_inventory.py` now carries the query in a comment and the doc carries
a `UPSTREAM_METHOD` section stating both the right field and the wrong ones.

---

## D-033 — An upstream with no licence is judged on adoption and on what we actually do with it

**Date:** 2026-08-29. **Status:** accepted. **Resolves:** Q-007.
**Decided by:** the maintainer.

**Rule.** A missing licence does not by itself keep software out of the catalog.
Weigh how widely the community already relies on it, and weigh what this project
actually does with the code. Where both point the same way, carry it, record the
licence state plainly in the manifest, and revisit if the situation changes.

### What "no licence" means, precisely

Default copyright: no grant to redistribute or to modify. That is a real
constraint and this decision does not pretend otherwise. What it does is
distinguish the constraint from the risk.

**We do not redistribute.** The catalog is data — a name, a URL, a digest and a
description of what the software does. Facts about third-party software are
freely usable, which `ahrl-inventory.md` already states about AHRL's own
provenance record. The bytes reach the operator's machine from the author's own
server or repository at install time, by the same act the operator would perform
by hand. **D-001** already forbids mirroring for an unrelated reason and that
prohibition does the work here too.

So the exposure of carrying an unlicensed upstream is closer to that of a
bookmark than of a fork. It is not zero, and it is not the exposure a
distribution takes on when it builds and ships a binary.

### Why adoption is the second input

The maintainer's reasoning, recorded because it is the load-bearing half:
**most authors of small ham and SDR utilities are not lawyers and do not
understand licence compliance.** A missing `LICENSE` file is very often an
oversight rather than a reservation of rights, and reading it as a deliberate
refusal would remove from the catalog software the community has depended on
for years — while doing nothing to make anyone safer.

Where a project is already carried by Skywave Linux, by AHRL, by a distribution,
or by a large body of users, that adoption is evidence about the author's actual
posture. It is not permission and the manifest must not imply that it is.

### What a manifest must still do

* **State the licence position in `upstream_support`.** "No LICENSE file, no
  header, checked in-tree on <date>" — the same standard
  `licence-verification.md` already applies. Never write "unlicensed" as if it
  were a licence, and never leave it unsaid.
* **Never mirror the source.** Fetch from the author's own URL, with a digest.
* **Prefer asking.** Where an author is reachable, a request for an explicit
  licence is worth more than this decision is, and one accepted pull request
  retires the question permanently.

### What this does not license

It does not extend to redistributing, vendoring, forking or relicensing, and it
does not apply to the engine — `catalog/` is CC0-1.0 and `src/` is
GPL-3.0-or-later (**D-023**), and neither may absorb third-party code without a
grant. If a rights-holder objects, the entry goes; that is the cost of the
position and it is a cheap one, because removing a manifest costs nothing but
the manifest.

**First consequence.** `supersdr` is carried. It has no LICENSE, no header and a
null GitHub licence field, and its last commit is 2022-12-31 — both measured.
Both facts go in the manifest.

---

## D-034 — Cellular tooling is staged, and the line is transmit

**Date:** 2026-08-29. **Status:** accepted. **Resolves:** Q-008.
**Decided by:** the maintainer.

**Rule.** The cellular cluster is carried in full, **staged rather than
filtered**, and every stage is behind an affirmative consent gate (**D-021**).

| Stage | Contents | Where |
|---|---|---|
| **1.0** | Receive and decode only — `gr-gsm`, `QCSuper`, the LTE decoders, IMSI-catcher-class receivers | the consent-gated `rf-research` profile |
| **post-1.0** | Transmit-capable network stacks — `srsRAN_4G`, the Osmocom core, `osmo-trx`, `OsmocomBB`, `intrusive-lte-mme`, `sni5gect` | a separate consent-gated `cellular` profile |

**The dividing line is transmit, not topic.** Receiving a signal already in the
air and operating a cellular network are different acts with different
authorisations behind them, and grouping them by subject would hide that.

### Why staged rather than excluded

The transmit stacks are legitimate software with legitimate users — authorised
engagements, shielded labs, vendors, academics — and DragonOS ships them. The
objection was never to the software. It was that a one-command installer aimed
at licensed hams is the wrong delivery mechanism *today*, before there is a
`cellular` profile with its own gate and before `docs/rf-security/` carries the
framing CLAUDE.md already requires of it.

So they are **scheduled, not refused**. Each excluded unit gets a catalog entry
recording that it is post-1.0 and why, per `PARITY-POLICY.md`'s rule against
silent drops. "Not yet" and "not ever" are different claims and the docs must
not blur them.

### What the gate does and does not do

Per **D-021** the gate discloses a capability and asks the operator to affirm
the authorisation they hold. It does not adjudicate law, in either direction —
it neither grants permission nor refuses on anyone's behalf, and `--yes` cannot
satisfy it. The post-1.0 `cellular` profile gets its own `env_var`, because a
shared one would let an opt-in to receiving satisfy an opt-in to transmitting.

**Consequence for 1.0.** `rf-research`'s contents are now settled rather than
provisional, and its `deliberately_excludes` says *post-1.0*, not *undecided*.

---

## D-035 — A missing station value defers one file; it does not refuse the transaction

**Date:** 2026-08-29. **Status:** accepted. **Closes:** DESIGN.md §15 question 5,
open and blocking since the 1.0 packet core was admitted.

**Rule.** Configuration this catalog writes on the operator's behalf is
templated from station-local values — callsign, grid square, node alias. When a
value is unknown, the file is **not written and is reported**, and everything
else in the transaction proceeds.

### Why deferral rather than refusal

The old behaviour was a blocker: any manifest with a `config_files` block
failed resolution, and because a profile fails if any member fails, the
**`packet` profile could not be installed by anyone**. Nineteen packages —
Direwolf, the AX.25 stack, Pat, Xastir, LinBPQ — refused because one of them did
not know a callsign. That is the profile the 73Linux delta was acquired for.

The maintainer's bar, recorded because it is the reasoning and not just the
verdict: *getting a user 95% of the way is a success as long as there are no
true blockers to 100% besides some user config.* An unknown callsign is user
config. It is not a true blocker, and treating it as one served nobody.

So `Deferral` sits beside `Blocker` in the planner and the two mean different
things. A blocker means the machine must not be touched. A deferral means most
of what was asked for happens, one part does not, and the report names it
precisely with the command that would let it.

### Three properties that are not negotiable

**Nothing is invented.** There is no default callsign, no placeholder, no
`CHANGEME`. A configuration file written with a made-up callsign would transmit
it, and the station identifier is the operator's legal identity on the air.

**A partial file is never written.** A config missing one of three values is
deferred whole. A file with `{station.callsign}` still in it looks configured
and is not, which is worse than an absent file because the operator has no
reason to look.

**Existing files are backed up before being replaced**, once. These paths
belong to the distribution's packages as often as to us — overwriting a
hand-tuned `/etc/ax25/axports` without a copy is damage no transaction log can
undo. The backup is written only if one does not already exist, so a second run
cannot replace the operator's original with our own previous output.

### How values arrive

Three sources, later winning: `$XDG_CONFIG_HOME/hammunition/station.yml`, then
`--callsign` and its siblings, then an interactive prompt. The prompt happens
only when the request **actually needs a value**, standard input and output are
both a terminal, and `--yes` was not given. Prompting for a callsign to install
a spectrum analyser is how people learn to dismiss prompts, which is precisely
what would make the consent gates of **D-021** worthless.

The file is mode 0600 and its path is resolved owner-aware, so running under
`sudo` still writes to the invoking user's home rather than root's.

### Relationship to D-012

**D-012** scoped `system_modifications` to udev rules, groups and blacklists
and explicitly did not cover templated config. This is that gap filled, and it
is filled as a separate concept rather than a fourth modification kind, because
a config file is the only one of the four that can be *partly* possible.

### Amendment, 2026-09-29 — derived values, home paths and appends that cannot duplicate (Q-022 #1)

Q-022 #1 (the 2026-09 gap analysis, §A1) asked for `config_files` blocks on
the plain-text units that need a callsign. Measuring their formats for it
found three things the mechanism above could not express. Each is filled
without weakening the three properties: nothing is invented, a partial file
is never written, an existing file is backed up once.

- **A value can be derived, never stored twice.** gpredict wants a latitude
  and longitude; the station stores a grid square. `{station.latitude}` and
  `{station.longitude}` are the centre of the square (four decimals, the
  square's precision and no more; `src/hammunition/maidenhead.py`). AX.25
  cannot carry `W1AW/4` or a seven-character call, so `{station.ax25_callsign}`
  is the callsign when it is one to six letters and digits. A derived value
  whose source is unset reports the *source* as missing (the prompt and the
  remedy ask for `grid_square`, which `station set` takes); one whose source
  is set but cannot give it defers the file with that reason. Trimming `/4`
  would be inventing a station identity, so it is never done.
- **`~/` is the operator's home.** gpredict reads `~/.config/Gpredict` and
  tlf the directory it starts in. A path beginning `~/` resolves against the
  account the run is on behalf of (the account database, never `$HOME`,
  which sudo resets to `/root`); with no operator the file is deferred. As
  root, the engine writes there only through `O_NOFOLLOW` descriptors from
  the home down (`paths.open_operator_dir`), refuses an entry that is not a
  regular file, and hands what it writes to the operator. Any other path must
  be absolute.
- **An append names what it must not duplicate.** `skip_if_present` lists
  regular expressions (station values substituted escaped, so a callsign only
  ever matches itself); if a line of the file matches one when the step
  runs, the append is skipped and the outcome names the line. libax25 refuses
  a second `axports` port with the same name or callsign, so the `wl2k` line
  is appended only when neither exists: a re-run changes nothing and a port
  the operator defined is left alone.

The dry run and the JSON plan name the station values each file is filled
from (`fills`), never the values. Uninstall still does not reverse a
written file, and each manifest says how to.

Six of the eight units carry a block: `direwolf`, `ax25-tools` (the
`axports` append), `gpredict`, `tlf`, `aprx` and `uronode`. `linpac` and
`fbb` configure themselves on first run from answers station config does
not hold, and their manifests say so instead.

## D-036 — Desktop integration is curated submenus, generated per desktop environment

**Date:** 2026-08-29. **Status:** accepted (maintainer, during the first VM
verification campaign). **Depends on:** the M3 launcher-generation work, which
is the same unwritten machinery.

**Rule.** Hammunition organizes what it installs into curated submenus, the way
AHRL's menu tree did — and generates that organization from the catalog's
existing `categories` tags, per desktop environment. The DEs that must work,
set by the OS ladder we support: **GNOME** (Debian 13, Ubuntu), **COSMIC**
(Pop!_OS 24.04), **Xfce** (Kali's default, Parrot).

### What is measured and what is not yet

Today's state, measured on the Parrot VM (2026-08-29): apt-installed GUI
packages ship their own `.desktop` entries and appear in the DE's flat menus
(chirp 1, flrig 1, gpsd-clients 2); autotools `make install` frequently
installs one under `/usr/local/share/applications`; the 14 units needing a
*generated* launcher get nothing, because the schema's `Launcher` block has no
consumer. Curation rides on the same generator, so this decision and that gap
are one work item.

What is **not yet measured** and must be before implementation, in the spirit
of D-014 — one mechanism per DE, verified rather than assumed:

- **Xfce** consumes the freedesktop menu spec directly — `.menu` XML,
  `.directory` entries, `xdg-desktop-menu` — the mechanism AHRL already used.
  Expected to be the straightforward case; verify on Kali/Parrot.
- **GNOME Shell renders no nested menus.** Its app grid folders come from
  `org.gnome.desktop.app-folders` gsettings, a different mechanism entirely,
  and per-user rather than system-wide. Generating `.menu` XML alone would
  produce curation Xfce shows and GNOME silently ignores.
- **COSMIC** is new and its app-library folder story must be read from the
  code or tested on Pop 24.04, not inferred.

### Boundaries carried over from existing decisions

The organization is generated **from the catalog's flat `categories` tags**
(D-003: tags overlap, never nest — a program may appear in two submenus, as 13
AHRL programs did). Menu data the engine writes is a system modification like
any other: printed before it happens, recorded in the transaction log, removed
by uninstall. And per D-022, we add our curated tree alongside the DE's own
organization; we never rewrite or suppress what the distribution's packages
put in the standard categories.

## D-014 amendment, 2026-08-30 — pipx and CPAN re-measured, and both are zeros now

**D-014** justifies every backend by a named unit. Two of the backends still
listed as required for 1.0 no longer have one, because the catalog moved out
from under the requirement and nobody re-measured:

- **pipx** was required for exactly one unit: CHIRP, which AHRL installs as a
  bundled wheel through `pipx install --system-site-packages`. This catalog's
  `chirp` resolved to **apt on all five targets** (measured 2026-08-28, and
  the Parrot VM ran the apt install on 2026-08-29). The other AHRL pipx-family
  unit, `pyautogui`, was retired as developer tooling. Zero manifests declare
  `method: pipx`.
- **CPAN** was required for exactly one unit: `aa-analyzer`, whose install
  begins with `cpan install Device/SerialPort.pm`. `aa-analyzer` is
  **SUPERSEDE → `flaa`** (accepted; both are RigExpert analyser front ends).
  Zero manifests need a Perl module from anywhere.

**Consequence.** The 1.0 backend list contracts to: apt, source, git, binary
(all written) plus **venv** (real users: `not1mm`, `nanovna-saver`, and the
`radiosonde_auto_rx` REVIVE waits on it by design) and **launcher
generation** (14 units, now fused with D-036). The `PipxInstall` schema stub
stays — a community-tier manifest may one day declare it, and the engine
refuses it by name exactly as before — but nobody builds a backend for a
measured zero. This is the same motion as the cargo amendment above: the
requirement was real when measured, the world moved, and the measurement was
repeated before the work was done rather than after.

The station profile's `pipx` **apt package** is unrelated and stays: that is
a tool installed *for the operator*, not a backend the engine uses.

## D-036 addendum, 2026-08-30 — two of the three menu mechanisms measured

D-036 named three per-DE mechanisms and called all three unmeasured. Two are
now measured on the target VMs; COSMIC still waits for the Pop!_OS image.

- **GNOME (Debian 13 VM): fully drivable, and better than hoped.** The
  `org.gnome.desktop.app-folders` schema reads and writes headless via
  `dbus-run-session gsettings ...` (persisting through dconf's user
  database), and the relocatable per-folder schema carries `name`, `apps`,
  `excluded-apps` — and **`categories`**. A folder declaring
  `categories=['HamRadio']` populates itself from the same `Categories=`
  values the launcher generator already writes, so GNOME curation is one
  folder declaration, not a maintained app list. Verified round-trip:
  set `['HamRadio']`, read it back, restored the default.
- **Xfce (Kali VM): the classic path, present as expected.**
  `xdg-desktop-menu` is installed, the menu prefix is `xfce-`, and user
  merged menus belong in `~/.config/menus/xfce-applications-merged/`.
  File-level mechanics confirmed; whether the rendered menu looks right is a
  console-lane check, like every GUI claim.
- **COSMIC: still unmeasured.** Nothing asserted until Pop 24.04 exists on
  the ladder.

Implementation note for whoever builds it: both measured mechanisms are
per-user and unprivileged, exactly like the launcher generator's artifacts —
the whole menu layer can honour the privilege rule with zero sudo.

## D-036 addendum, 2026-09-01 — the curated tree was seven entries wide; it now places what the packages ship

The menu layer went into the engine on 2026-08-30 with submenus that
included by the `X-Hammunition-<category>` marker — which only the entries
Hammunition *generates* carry. Rendered on the Kali VM with the whole
catalog installed and parsed back with gnome-menus' implementation of the
spec, the Ham Radio tree held **7 entries** while **43** desktop entries the
distribution's own packages ship with `Categories=…HamRadio…` sat under
Internet (fldigi, xastir), Multimedia (wsjtx), Education (gpredict) and
Other (chirp). GNOME was better off by accident: its folder's
`categories=['HamRadio']` gathers those 43, though not the 23 entries
catalog packages ship under other tags (`kali-radio-frequency`,
`AudioVideo`, `Science`). "Alongside the DE's own organization" was true
and the curation was nearly empty — the "run it and look at it" class of
bug.

**Measured, then built.** `dpkg -S` on those 43 mapped 42 to a manifest
through the apt package that shipped them; the 43rd (`gridtracker2`) is a
`.deb` unit and maps through its `deb_package`. So `menus apply` now asks
`dpkg -L` for every catalog package present on the machine, places each
`.desktop` it ships under the submenu of every category its manifest
carries — the menu spec's `<Filename>` include — and gathers anything
`HamRadio`-tagged that no manifest claimed at the tree's top level, with
the placed ids excluded there so nothing shows twice. GNOME gets the same
ids unioned into the folder's `apps`. Re-measured on Kali: 73 entries
placed 130 times, every one under its submenu; the source-built units
(`fldigi`'s family, `wsjtx`, `qlog`, `garim` — installed by `make install`
under `/usr/local`, so no dpkg record) land at the top level as designed.
The GNOME half round-tripped headless on the Debian 13 VM (71 apps, the
same 71 on a second run).

**Boundaries kept.** One taxonomy: categories still come from
`catalog/categories.yaml`, which now also carries each tag's menu title
(the engine cannot know `sdr` is SDR and rendered "Sdr"). No maintained app
list: the desktop-file ids are read from the machine at apply time, never
typed into the catalog. The whole catalog is consulted rather than the
transaction log, because being in the catalog *is* the curation and an
operator's own earlier `apt install fldigi` deserves the same submenu. The
DE's own copies are untouched (D-022). Still open: a source build's entries
could be placed too, from cmake's `install_manifest.txt` where one exists;
and the menu files are not yet in the transaction log, so `uninstall` does
not remove them — both named here rather than left to be rediscovered.

## D-031 addendum, 2026-09-02 — the effect check now covers what a build installs

**The finding.** `js8call` was recorded `verified: true` on Parrot, Debian,
Kali and Ubuntu 26.04 — a full-catalog pass on each — and none of the four
has a `/usr/local/bin/js8call`. JS8Call-improved v3.0.3's `CMakeLists.txt`
has no install rule for its executable (the only `install()` is in
`resources/debian/CMakeLists.txt`, which nothing adds as a subdirectory), so
`cmake --install` exits 0, writes an empty `install_manifest.txt`, and
installs nothing. Every command in the transaction exited 0, and the effect
check re-read apt for the build dependencies and found them present. It
never asked whether the thing the operator wanted existed, because it had no
check for a built artefact at all — the D-031 shape exactly, one layer up
from the `sed` and `dpkg-deb` cases the decision was written from.

**The check.** `transaction_end` gains a `binary` kind: for every source,
git and non-`.deb` binary unit in the plan, each `binaries` entry must be an
executable at `<prefix>/bin/<install_as>` after the run. A `.deb`'s contents
are apt's to place and apt is asked about them; a venv's entry points are
wrappers in the operator's `~/.local/bin`, a different mechanism with its
own removal check.

**What it found on the first pass, before any VM was touched.** Two of the
26 manifests that declare `binaries` would have failed it. `js8call`, above,
needed `provides_install_target: false` — and that path then needed fixing
too, because the explicit copy looked for the build's output in the *source*
tree, where cmake never writes it; every unit that had used the path so far
was an in-tree qmake or make build, so nothing had noticed. `ais-catcher`
declared `install_as: ais-catcher` while its own install rule places
`AIS-catcher`, the name the manifest's launcher already execs. The `binaries`
field on a unit whose build installs itself is documentation, and this is the
check that makes documentation have to be true: `install_as` now has to say
what the install rule does. Both rebuilt and confirmed on a VM the same
night. Of the other 24 declarations, 23 match what the three full-catalog
VMs hold in `/usr/local/bin`, and the 24th (`nanovna-saver`'s
`NanoVNASaver`) belongs to its ARM-only binary block and is absent from all
three x86 VMs as it should be — the x86 block is a venv, which the check
does not read.

**Still not covered.** A build that installs itself under the name it
declares but is broken at runtime; and a unit that declares no `binaries`
at all, which this check cannot see — the schema does not require the field,
so a source unit with none is trusted on its install step's exit code as
before. The `m5-parity-verified.md` counts predate this check; they are
build-dependency counts for source units until re-run.

## D-036 addendum, 2026-09-02 — the merged file's name is decided from the machine, or refused

`menus apply` took the merged file's prefix from `$XDG_MENU_PREFIX` and
wrote `applications-merged/hammunition.menu` when the variable was unset.
Measured across every machine to hand — the four VMs and the maintainer's
laptop — **none has a bare `applications.menu`**: Parrot's `/etc/xdg/menus`
holds `kf5-`, `mate-`, `plasma-` and `xfce-` roots, Debian and the laptop
`gnome-`, Kali `xfce-`, the Ubuntu server images none. So every run made
over SSH, under `sudo`, or from a shell older than the login had written a
file no root menu merges, and reported success. The Debian "71 apps,
idempotent" measurement in the previous addendum was the GNOME gsettings
half; its file half had merged into nothing.

Now: an explicit `--menu-prefix`, else the session variable, else the one
prefixed root installed, else a refusal naming the candidates. Guessing
between Parrot's four would write the menu for the desktops the operator
does not log into. Verified on Parrot: the bare-SSH run refuses and lists
the four; `--menu-prefix plasma-` writes a file gnome-menus' spec parser
reads back from `plasma-applications.menu` as 25 populated submenus with
the source-built entries gathered at the top.

## D-037 — A `node` build is acceptable when it is disclosed as a requirement and refused when Node is absent or too old

**Date:** 2026-09-02. **Status:** accepted (maintainer, closing Q-016).
**Depends on:** D-014 (a backend is justified by a named unit), D-016
(refuse at plan time, never partway), D-021's spirit (disclose, do not
decide for the operator), Q-006 (openhamclock is the HamClock default).

**Rule.** The engine may build a unit with Node and npm. Two conditions,
both the maintainer's:

1. **Node is disclosed as a requirement of that unit**, in the manifest's
   documentation and in the plan the operator reads before anything runs —
   not discovered as a build failure. A unit that needs Node says so the way
   a unit that needs a transceiver says so.
2. **When Node does not exist on the machine, or the version is not new
   enough, the engine refuses at plan time** and says which: the floor the
   unit declares, the version found (or that none was), and where a
   qualifying one comes from. It never fetches Node itself.

**Where Node comes from.** The distribution's own `nodejs` and `npm`
packages, declared as build dependencies like any toolchain, so apt installs
them inside the transaction and `--dry-run` prints them. The floor is
checked against the archive's candidate (or the installed version), never
assumed — and the first unit needed that check on the first day: see the
second amendment, where Ubuntu 24.04's 18.19 turned out not to clear it.
NodeSource and every
other third-party Node repository are out: that is the third-party-apt-repo
backend, which is a separate security decision the maintainer has not made.

**What the build is.** The three npm invocations Q-016 measured, every one
with `--ignore-scripts` so no third-party lifecycle code runs: `npm ci`
against the lock file inside the sha256-pinned source tarball (728 tarballs,
each pinned by its sha512 `integrity` field — the closure is transitively
verified from one manifest hash), `npm run build`, `npm prune --omit=dev`.
The tree installs per-user like a venv unit; the launcher binds loopback
(`HOST=127.0.0.1`) because the code's own default is every interface.

**Why the conditions matter.** A registry fetch during a build is new here;
the operator reading the plan must see that a unit will do it and what it
needs before they say yes. And "install Node from somewhere" is exactly the
`curl | bash` habit AHRL's `aiscatcher-install` line shows and this project
exists to refuse — so the engine names the gap and stops.

**What this does not decide.** Whether the P.533 propagation WASM
(upstream's `prebuild` fetches it from a moving tag, skipped here) is carried
as a pinned artefact. The built-in model serves until an operator asks.

**Amendment, 2026-10-02 — the Java floor beside the Node floor.** `graphhopper`
and `brouter` depend on `default-jre-headless`, a metapackage whose version says
nothing about the Java major it brings: Ubuntu 22.04 and Pop!_OS 22.04 resolve it
to Java 11, GraphHopper's classes are major 61 (Java 17), and both units planned
cleanly and failed at run time (the deferred minor from PRs #176 and #191). The
rule is D-037's, with a probe in place of a package version. A manifest names
`requires_java: <major>` (a positive integer); the plan runs `java -version`
once, only if some unit in the transaction declares the field, on the `java` on
PATH or else `/usr/lib/jvm/default-java/bin/java`, and parses the major from the
first line (`"17.0.12"` is 17, `"21"` is 21, `"1.8.0_392"` is 8). Below the
floor, or no `java` at all: a profile member is deferred by name, a unit the
operator typed is refused with the same text (D-039), either one stating the
measured version, the floor, and the archive's `openjdk-N-jre-headless` that
would meet it where the plan's own `apt-cache policy` sweep knows one. Nothing is
fetched or installed to meet a floor. Two cases are not deferrals. A concrete
`openjdk-N-jre*` in the unit's own `depends` with N at or above the floor and an
archive candidate meets it in this very transaction, and the plan says so. And a
machine with *no* Java whose unit depends only on a JRE metapackage cannot be
measured before the install (refusing would defer both units on every clean
Debian 13), so the plan discloses that and says to check `java -version`
afterwards; a metapackage with a too-old Java already installed is the reported
case and still defers. The floors are measured, per D-037's last sentence:
GraphHopper 17 (its pom's `<release>17</release>` and the jar's class-file
major 61); BRouter **11** (its build sets `options.release = 11`, the jar is
major 55; upstream's Docker image uses 17, which is an image choice, not a
floor); the Mapsforge writer (target 8) and the archive's osmosis carry their
own JRE dependency, so they declare none. The capability matrix has no column
for floors (it records archive candidates, and Node's floor is not there either),
so nothing changes in it.

**Amendment, 2026-09-02 — what the loopback bind is and is not.** Written
while carrying the first unit. The engine's wrapper sets `HOST=127.0.0.1`
and the schema refuses a manifest that sets `HOST` at all, so the *engine's*
default is loopback and no catalog entry can widen it. openhamclock's own
config loader then reads the `.env` it creates beside `server.js` and writes
every key into the environment over whatever was there — its `.env.example`
says `HOST=localhost`, so the result is still loopback, but an operator who
edits that one line to `0.0.0.0` has widened it, and the wrapper does not
stop them. That is the right split: the engine chooses the safe default and
the operator's own config file is theirs to change (D-021's shape — disclose,
do not adjudicate). The manifest's `known_problems` says so, and says the
other thing `HOST` does not govern: the WSJT-X integration binds UDP 2237 on
every interface at start, and only `WSJTX_ENABLED=false` in that `.env` stops
it. Read from `server/config.js` and `server/routes/wsjtx.js` at v26.7.0, not
assumed from `.env.example`'s comments.

**Second amendment, 2026-09-02 — two claims above were false, and the fixes
are in the schema.** Both found by installing through the engine and
measuring, the same day the rule was written.

1. *"The code's own default is every interface"* understated it: v26.7.0
   ignores `HOST` entirely. `server.js:364` is `app.listen(PORT, '0.0.0.0',
   ...)`, so with the wrapper's `HOST=127.0.0.1` in force the first real
   install still showed `00000000:0BB9` in `/proc/net/tcp`. Upstream main
   has the same line. The fix is one token in one line, so `NodeInstall`
   gained `patches` — the same `Patch` model the source backend takes,
   applied after extraction and before the lock-file check, `patch` joining
   the build dependencies — and the manifest carries the diff with its
   evidence. With it, and the `.env` loader's `HOST=localhost`, the bind
   measures `[::1]:3001` only: Node binds `localhost` as IPv6 loopback, so
   the launcher opens `http://localhost:3001` rather than an IPv4 address.
   The rule stands; what changed is that the engine's loopback default now
   *has an effect* on this unit.
2. *"Every target's archive Node clears the floor"* was read from Vite's
   `engines` range, which describes the bundler. The server needs
   `require()` of an ES module (`axios-cookiejar-support` 6.x), which is Node
   20.19+, and on Ubuntu 24.04's 18.19 the build succeeds and the server
   dies at first start with `ERR_REQUIRE_ESM`. So the floor is **20.19**,
   and because it is a minor the schema field became `node_min_version`, a
   `MAJOR.MINOR` string compared on both numbers — `node_min_major: 20`
   would have admitted 20.18. Ubuntu 24.04 is now refused at plan time by
   name. That is the rule working as written: the requirement is disclosed,
   the refusal is specific, and Node is not fetched to meet it. A floor is
   measured by running the unit, never read from `engines`.

## D-038 — On a target that installs from more than one release, an apt transaction the default release cannot resolve is resolved from the release the machine already installs from, disclosed by name

**Date:** 2026-09-02. **Status:** accepted.
**Depends on:** D-016 (refuse at plan time, never partway), D-022 (coexist
and disclose, never remove or replace silently), D-025 (re-verify a claim
when it becomes decisive), D-031 (verify the effect, not the exit status).

**What was measured.** A clean Parrot 7.3 VM, restored to its
`clean-baseline` snapshot, ran every profile as one transaction. Eight
installed and confirmed, one stopped at its consent gate as it should, one
was refused by name for its `apt_repos` units, and **five passed the plan
and failed at the first apt command**, in about one second each —
`digital-modes`, `electronics`, `listening`, `logging` and `propagation`.
Every failure was the same text from apt 3.0.3:

```
E: Unable to correct problems, you have held broken packages.
   Unable to satisfy dependencies. Reached two conflicting decisions:
   1. libcurl4t64:amd64=8.14.1-2+deb13u5 is not selected for install
   2. libcurl4t64:amd64=8.14.1-2+deb13u5 is selected as a downgrade because:
      1. libcurl4-openssl-dev:amd64=8.14.1-2+deb13u5 is selected for install
      2. libcurl4-openssl-dev:amd64=8.14.1-2+deb13u5 Depends libcurl4t64 (= 8.14.1-2+deb13u5)
```

The cause is the target, not the catalog. Parrot's baseline installs
**197 of its 3,801 packages from `echo-backports`** — `apt-cache policy`
reads it as `o=Parrot,a=parrot-backports,n=echo-backports`, pinned 599
against the main release's 600 in `/etc/apt/preferences.d/`. The runtimes
came from backports; the `-dev` packages the catalog's build dependencies
name are still the main release's, and a Debian `-dev` package depends on
its runtime at an **exact** version. Three chains were confirmed on the
machine: `libcurl4t64` 8.21 (backports) against `libcurl4-openssl-dev` 8.14
(main); `gir1.2-atk-1.0` 2.61 against `libatk1.0-dev` 2.56;
`libqt6webenginecore6` 6.10 against `qt6-webengine-dev` 6.8. Every failed
profile pulls at least one of those three in. apt will not downgrade a
pinned-higher package to satisfy a dependency, and it is right not to.

This was not the first sighting. `vm-campaign-digital-modes.md` recorded
the same skew on 2026-08-30 — glfer and xwefax against the GTK dev chain —
and worked around it by hand on the VM. A workaround applied to an image
is exactly the evidence D-025 says to re-verify when it becomes decisive;
here it became decisive when a clean snapshot took five profiles down.

**What resolves it.** `apt-get install --simulate --yes --target-release
parrot-backports <the same list>` resolves all five failed lines that were
re-run by hand — digital-modes at 249 packages with 19 from backports,
electronics 110/3, listening 263/10, logging 124/12, propagation 531/11.
`-t` raises the named release's pin to 990 for candidate selection, so the
`-dev` package is taken from the release its runtime already came from and
the exact-version dependency is met by the version that is installed. The
dozen-or-so packages that move are the `-dev` halves of runtimes the target
chose to take from backports before this engine ran; nothing installed is
downgraded and nothing is replaced.

**Rule.** When apt refuses the transaction because an installed package
would have to be downgraded:

1. The engine reads **which** package from apt's own words (the `is
   selected as a downgrade` line) and **where it is installed from** with
   `apt-cache policy` — the archive on the `***` row of the version table.
2. If every such package is installed from **one** release, the plan is
   simulated again with `--target-release` naming that release. If apt
   accepts it, the apt step runs with that flag, and the plan the operator
   reads lists **every package that will be taken from that release and
   from nowhere else** under a heading that says why.
3. Otherwise — no downgrade line to read, more than one release, or apt
   still refusing — the plan is refused with apt's text and the simulate
   command that reproduces it (D-016). The engine does not try a second
   release, does not try releases the culprit is not installed from, and
   does not guess.

**What was rejected.**

- `-o APT::Solver::Strict-Pinning=false` also resolves the transaction —
  by **downgrading** `libcurl4t64` to main's 8.14. That satisfies the
  `-dev` package by changing what the target installed, which is D-022's
  forbidden shape: a distribution's choice, replaced silently.
- A narrow `libcurl4-openssl-dev/parrot-backports` pin instead of `-t`.
  Tried; the chain moves. Pinning the `-dev` package to backports made its
  own dependency the next exact-version mismatch, and the fix iterates
  through the dependency graph one refusal at a time — the "fix one, re-run,
  meet the next" loop D-016 exists to end.
- Declaring the backports release in the catalog. A manifest saying
  "on Parrot, take `libcurl4-openssl-dev` from backports" would freeze one
  evening's measurement of one machine's package state into data that is
  supposed to describe software. The capability-matrix note already records
  why `when:` selectors are not used for what apt can answer at plan time;
  this is the same argument.

**The general check this produced.** Until this measurement the engine
asked `apt-get install --simulate` only when a vendor `.deb` in the plan
declared a conflict. It now asks it **once, for every transaction with
outstanding apt work**, and refuses at plan time on any answer apt gives —
`apt-cache policy` proves that each package exists, not that the set of
them installs together. Five profiles reached the apt step and died there
with a plan that had said they would install; that is the gap D-016 was
written to close, and it was open because nothing had measured a target
whose baseline installs from two releases.

**What this does not decide.** Whether a target's *own* configuration is
sensible — Parrot's backports pin is Parrot's decision and this engine
neither judges nor changes it. And whether a release-specific plan should
be recorded in the transaction log beyond the argv it already carries; the
`--target-release` flag is in the printed and logged command, which is the
disclosure D-031 asks for.

## D-039 — A profile member the target does not offer is deferred by name and the rest of the profile installs; a member the operator named, an engine gap, or a manifest defect still refuses the transaction

**Date:** 2026-09-03. **Status:** accepted; resolves Q-017.
**Depends on:** D-016 (refuse at plan time, never partway), D-035 (a
missing value defers one file, never the transaction), D-037 (Node only from
the distribution), D-031 (verify the effect, not the exit status).
**Amends:** D-016, which held that a transaction resolves whole or not at
all. It still does, for everything the operator asked for by name. The
exception is drawn around one thing: a *profile* member that the *target*
does not offer.

**What was measured.** The full-catalog campaigns on Ubuntu 24.04 and 26.04
(`docs/reference/vm-campaign-ubuntu.md`, 2026-09-02): every unit planned
and installed by name, and then **five of fifteen profiles refused whole on
24.04, two on 26.04**, each over members refused for a reason true of the
target — `listening` withheld nineteen installable units because the 24.04
archive carries neither `readsb`, `rtl-ais`, `satdump` nor
`mlat-client-adsbfi`; `propagation` withheld everything because Node 18.19
is below openhamclock's 20.19 floor and 24.04 carries no `voacapl`. The
operator's remedy was to install the nineteen by name, which is the
fix-one-re-run loop D-016 exists to end, arriving from the other side.

**The rule.** A package that reached the plan only through a profile, or as
a catalog dependency of one, is **deferred** rather than refused when the
reason is one of exactly three, each a fact about the target:

1. the catalog declares no install block matching this distro, version and
   architecture (D-002's selector, honestly negative);
2. apt on this release has no candidate for **the unit's own packages** —
   the `packages:` of its apt block, and only those;
3. the distribution's `nodejs` is absent or below the manifest's floor
   (D-037).

A deferred member's catalog dependents defer with it, each naming the
dependency it lost. Deferrals are printed under *Will NOT happen*, written
to the transaction log (`transaction_begin` version 2, `deferred`), and
reported by `hammunition status` for as long as that transaction is the most
recent one.

**What still refuses, by name.** Everything that is not one of those three:

- **A name the operator typed.** `hammunition install satdump` on 24.04 is
  a request to see the refusal, and it sees it in full. A unit named both
  directly and through a profile is a named request.
- **An engine gap.** When this was written, `code` and `codium` were
  refused on every target because the engine had no `apt_repos` backend,
  not because any archive lacked them; deferring them would have made the
  missing backend invisible, and `workstation` refused whole until that
  backend existed — the test Q-017 set for the classification. D-040 built
  the backend the same day and moved both editors to their own opt-in
  `editors` profile; the classification stands for the next gap.
- **A missing `depends` or `build_depends`.** Those are the manifest's, not
  the target's: D-016 names four AHRL dependency lines that went stale
  exactly this way, and a deferral that swallowed them would hide the defect
  the check was written to find.
- **A retired or broken status**, a declined consent gate (D-021), a
  verification failure (D-018), a declared package conflict (D-022), and an
  apt simulation that cannot resolve (D-038). None is a fact about what the
  archive offers.
- **A profile whose every member is deferred.** Installing nothing and
  reporting success is the shape D-031 exists to catch; it refuses naming
  the profile and each member's reason.

**Why the line is here and not wider.** Q-017's three options were: defer
(this), fix each gap in the catalog with `when:` selectors, or a
`--defer-unavailable` flag. The selectors would freeze one evening's
`apt-cache policy` into data meant to describe software, and would still
produce the same partial install, made silently in the catalog instead of
reported by the engine. The flag is `--yes` for consent gates over again:
the option everyone passes, at which point it is the default with a worse
name (D-021). What makes deferral safe is not the mechanism but the
classification, so the classification is the decision.

**Rejected while building it:** deferring on any no-candidate result. A
unit's `depends` line naming a package the archive lacks is indistinguishable
from its own package being absent unless the plan checks which list the
name came from — so it checks, and defers only when every missing name is
in the unit's own `packages:`.

### Amendment, 2026-10-02 — a publisher that does not answer is a fourth reason (#200)

D-039 listed three reasons a profile member defers, each a fact about the
*target*. `hammunition install navigation --dry-run` refused whole with "3 US
Topo item(s) could not be resolved" over a Geofabrik outline answering 502, a
second outline timing out and one US Topo sheet answering 503, and the same
command a minute later was expected to pass. A plan-time probe made one request
and read any failure as final. One request is not a measurement, and nothing
about the target, the catalog or the operator's request was wrong.

**The rule.** A plan-time probe (every `HEAD` and outline `GET` the plan makes
for terrain, 3DEP, US Topo, FSTopo, Kiwix and CoMaps) is tried up to three
times on an HTTP 5xx, an HTTP 429, a connection error or a read timeout,
waiting 1 s, 3 s and 9 s, and says so on stderr at each retry (the host and
the attempt). Any other 4xx is final at once: a 404 on a listed sheet is the
stale-index case and keeps its remedy (`scripts/gen_ustopo_index.py --fetch`),
because that remedy is wrong for a 503. After the last try the probe raises
`PublisherUnavailable` carrying the publisher's last answer. Then:

- **A profile member defers what that publisher did not answer, by name.**
  The item (one sheet, one tile, one book, one map, or one region's outline)
  is left out, the rest of the unit and of the plan resolve, and the plan
  prints one deferral per unit under "Will NOT happen" with the answer quoted
  ("prd-tnm.s3.amazonaws.com answered HTTP 503"), writes it to the transaction
  log (`kind: package`, as D-039's other deferrals), `status` shows it, and a
  note at the foot says to run the same command again. An outline that does
  not answer defers that region's items in **every** unit that needs it and no
  other region's.
- **A unit the operator typed still refuses**, in full, with the publisher's
  answer and "the publisher is not answering right now" in place of the
  stale-index remedy, exactly as D-039 treats a typed name for a unit the
  target lacks.
- **Nothing installed is lost to a deferral.** A deferred sheet whose older
  edition is installed keeps that edition (raw and warped) and the VRT still
  draws it; a deferred terrain tile (Copernicus or 3DEP) stays in its
  region's record and in every keep set but is left out of what contours,
  SPLAT terrain and BRouter's elevation are drawn or built over (it is not
  there to read); a deferred book or map suspends **all** of that unit's
  removal of unlisted files for the run, a coarse guard that also holds back a
  book you really did deselect until the next complete run. Without this an
  outage would have deleted what it could not replace.

**Why D-039 and not D-049.** D-049 shaped the data unit; this changes which
failures a *profile member* defers on, which is D-039's classification and
D-035's principle (one part of the plan does not happen, named, and the rest
does). D-061, D-066, D-068 and D-069 keep owning what each probe checks.

**Two choices recorded.** A failure is remembered for the rest of the run only
once the retries are spent (`MemoProbe`), so three units needing one dead
outline ask it three times in all, not nine. And a host whose probes have
exhausted its retries three times running (an offline host, a bucket answering
503 for everything) is asked once each, with no backoff, for the rest of the
run, so a dead publisher costs seconds where retrying every item would cost
minutes; a success resets it. A certificate that does not verify is final at
once, never an outage. **Not done:** a
deferred item is not retried later in the same run, and the Geofabrik region
index (`resolve_map_regions`) is not under the policy, because it already keeps
a region that is installed rather than deferring. Code `src/hammunition/retry.py`,
`topo_plan.py`, `terrain_plan.py`; tests `tests/test_retry.py`,
`tests/test_plan_retry.py`.

---

## D-040 — A third-party apt repository is added only against a key the manifest pins by fingerprint, only when the target's own archive offers nothing, only after the operator affirms that fingerprint, and it comes out whole on uninstall

**Date:** 2026-09-03. **Status:** accepted; built the same day.
**Depends on:** D-021 (consent gates disclose capability; `--yes` cannot
satisfy one), D-022 (coexist with a distribution's choice, never displace
it silently), D-031 (verify the effect, not the exit status), D-018
(external claims tested before published), D-039 (a member the target does
not offer is deferred; an engine gap refuses).
**Resolves:** the `apt_repos` engine gap that D-039 named and the `cli.md`
refusal table listed by name.

**What was measured.** Two manifests carry an `apt_repos` block: `code`
(Microsoft, `packages.microsoft.com/repos/code`) and `codium` (VSCodium,
`download.vscodium.com/debs`). Neither publisher's package is in Debian,
Ubuntu, Kali or Mint; Parrot carries `codium` in its own archive. The
Ubuntu campaigns (`vm-campaign-ubuntu.md`) showed the cost of not having
the backend: both units refused on every target as an engine gap, and
`workstation` — seven packages that need nothing beyond the archive —
refused whole with them. The two publishers also differ in a way that
decided the design below: Microsoft serves an armored `microsoft.asc`,
VSCodium a binary `pub.gpg`.

**The rule.**

1. **The manifest pins the key.** An `apt_repos` entry names the `uri`,
   `suites`, `components`, the `key_url` the key is fetched from, and the
   **primary key fingerprint** (40 hex for v4, 64 for v6). Both URLs must
   be `https://`. The fingerprint is the pin; the URL is only where to
   look. A key whose primary fingerprint is not the pinned one is
   discarded with both values printed. A file that is not OpenPGP, has no
   public-key packet, fails its armor CRC, is truncated, or carries two
   primaries is refused by name. The check is this engine's own packet
   parser (`openpgp.py`), so it does not depend on `gpg` being installed
   and cannot be satisfied by anything `gpg` would import silently.
2. **Two files, both named for the repository, both disclosed in the
   plan.** `/etc/apt/keyrings/<name>.gpg` holds the key in **binary**
   OpenPGP form — an armored file is dearmored before the fingerprint is
   computed, so the bytes on disk are exactly the bytes that were checked,
   and a file called `.gpg` never holds text (VSCodium's is already
   binary; calling Microsoft's dearmored key `.asc` would have been a
   false claim about its contents, which is D-018 applied to a filename).
   `/etc/apt/sources.list.d/<name>.sources` is deb822 with `Signed-By:`
   naming that keyring and nothing wider, so the key is trusted for this
   repository only, never archive-wide, and a marker comment names the
   unit that wrote it. Nothing is written to `/etc/apt/trusted.gpg.d/`,
   ever.
3. **Only when the archive offers nothing** (D-022). The repository is
   added when apt has no candidate for the unit's *own* packages and
   neither file exists. A candidate in the archive means the repository is
   not added and the plan says so; on Parrot, `codium` installs from
   Parrot's archive and VSCodium's repository is never mentioned to apt. A
   missing `depends` is never a reason to add a repository — that is the
   manifest's defect, and it stays a blocker (D-039's reasoning).
4. **Someone else's file of the same name is a refusal.** Plan-time
   reads the file system, never apt: *ours* when both files hold what this
   engine would write, *foreign* when a same-named file holds anything
   else, *absent* otherwise. Foreign refuses — overwriting a source under
   our name would silently change what that machine trusts. Ours with no
   candidate points at `--refresh`, because the repository is there and
   the lists are stale.
5. **The gate is per repository and the answer is the fingerprint.**
   `HAMMUNITION_ACCEPT_APT_REPO_<NAME>` must equal the pinned fingerprint.
   A bare `1` is refused with the value it should hold; `--yes` never
   satisfies it (D-021); an interactive prompt shows the disclosure —
   URI, suites, components, fingerprint, both file paths, the unit that
   wants it — and asks. The affirmation is logged as a `consent_affirmed`
   record with profile `apt-repo:<name>` so the log says who trusted
   which key and when. Making the operator type the fingerprint is the
   point: it is the one check the engine cannot do for them, and an
   environment variable that is just `1` is `--yes` with extra steps.
6. **Order is the security property.** The key fetch is a `fetch` action
   and runs with the other fetches, before anything touches the machine.
   Then the staged source, then `install -D -m 0644` of both files as
   root, then a forced `apt-get update`, then `apt-get install --simulate`
   over the whole apt step, then the install. A repository that does not
   carry what it promised refuses before the apt step, not partway.
7. **Reversal is whole.** `uninstall` attributes both files to the
   transaction that wrote them (every successful `install -D -m` in the
   log), removes them, and refreshes apt. A same-named file this engine
   did not write is left alone.

**What this is not.** Not a general repository manager: there is no
`add-repo` verb, no PPA shorthand, no way to add a repository the catalog
does not declare. Not `apt-key` — that is deprecated and archive-wide,
which is the property this decision exists to refuse. Not a fetch of
`gpg`'s output: the fingerprint is computed here from the packets, so a
key that `gpg --import` would accept with a warning is refused with a
reason.

**Consequences.** `code` and `codium` left `workstation` for an opt-in
`editors` profile, so an operator who wants `lsusb` never sees a
repository disclosure they did not ask for. The `cli.md` refusal table
loses its `apt_repos` row. `code.yaml`'s stated keyring path was corrected
to `microsoft-vscode.gpg` — the repository's `name`, which is what the
engine writes — from `microsoft.gpg`, which was a guess written before the
backend existed.

**Measured on 2026-09-03**, on the dev VMs, with this engine at the commit
that records it:

- **Ubuntu 24.04.4, apt 2.8.3.** `install code` with no variable and no
  terminal: exit 3, nothing written. With the variable set to `1`: exit 3,
  the refusal naming the fingerprint it should hold, nothing written. With
  the variable set to the fingerprint and `--yes`: seven commands
  completed and confirmed; Microsoft's armored `microsoft.asc` was kept as
  **640 bytes** of binary v4 OpenPGP (`file` reads it as *OpenPGP Public
  Key Version 4, RSA 2048*), `apt-get update` accepted the repository
  through `Signed-By`, the simulate resolved, and `code 1.136.0-1788342447`
  installed from `packages.microsoft.com/repos/code stable/main`. A second
  `install code` planned zero commands. `uninstall code` removed the
  package, both files and refreshed apt: `apt-cache policy code` reports
  no candidate afterwards. The log holds one `consent_affirmed` per
  affirmation with `profile: apt-repo:microsoft-vscode` and the
  fingerprint in `extra`.
- **Debian 13, apt 3.0.3.** `install editors --yes` with both variables
  set: two gates, two keys — VSCodium's binary `pub.gpg` kept untouched at
  **2256 bytes** (RSA 4096) — eleven commands confirmed, `code 1.136.0`
  and `codium 1.126.04524` installed from their publishers. `uninstall
  editors` removed both packages and all four files in six commands. A
  hand-written `vscodium.sources` with different content then made
  `install codium` refuse at plan time as foreign, exit 2, nothing changed.

- **Parrot 6.x.** `install codium --yes` with `codium` removed first and
  no variable set: one command, `apt-get install --yes -- codium`,
  confirmed, from `deb.parrot.sh/parrot echo/main`; the plan carried the
  note *the archive already offers codium; the vscodium repository the
  manifest declares is not added (D-022)*, no gate was presented, no
  file was written under `/etc/apt/keyrings/` or `sources.list.d/`, and
  the log holds no `consent_affirmed`. Rule 3, measured.

### Amendment, 2026-09-30 — a repository may be narrowed to some targets

**No new number; this extends rule 1 and changes nothing else.** An
`apt_repos` entry may carry `when:`, the same selector an install block
uses (distro, distro version, architecture). Unset, it applies to every
target, which is what `code` and `codium` have always meant. The plan
considers only the repositories whose `when` matches the running target;
the rest are never mentioned to apt, never disclosed, and never gated. A
target that no repository applies to falls to the ordinary D-039 path: a
profile member is deferred by name, a name the operator typed is refused.
Two entries of one `name` are refused at load, because they would write the
same two files.

**Why.** Kismet (Q-022 #4) is the first publisher whose repository is one
tree per release under a different URI: `…/repos/apt/release/trixie` with
suite `trixie`, and `…/release/noble` with suite `noble`, each with its own
`Release` file. The URI differs, not only the suite, so one declaration
cannot serve both, and declaring both unconditionally would add Ubuntu
noble's packages to a Debian 13 machine. Measured 2026-09-30 from the
repository's own index: `release/` carries `trixie` and `noble` and no
`resolute`, so Ubuntu 26.04 gets no repository and Kismet is deferred
there by name.

`uninstall` is unchanged: it removes the files the log attributes to a
unit's declared repositories, whichever target wrote them.

### Amendment, 2026-10-05 — a repository may be flat; a package's own effects are disclosed, not performed

**No new number; two additions to rule 1 and to what a plan prints, and
nothing else changes.** Issue #308 (`meshtasticd`, D-080's Track C part 2) is
the first unit whose publisher is the openSUSE Build Service.

1. **A flat repository is declarable.** The Build Service publishes
   `Release` and `Packages` directly under the repository URI, with no
   `dists/` tree (measured 2026-10-05, `network:Meshtastic:beta/Debian_13/`).
   sources.list(5) writes that as a suite ending in `/` and no `Components:`
   line, and an empty `Components:` line is a malformed stanza that apt
   refuses whole (measured, Debian 13 container). An `apt_repos` entry may
   therefore carry `suites: ["./"]` and `components: []`; the two shapes are
   never mixed in one entry (`components` empty only when every suite ends in
   `/`, and none given when one does). The source file omits the line, the
   consent disclosure says "a flat repository", and the plan line says so.
   Nothing about the key, the gate, the two files or the removal changes.
2. **`system_modifications` gains three disclosure-only kinds**,
   `package_service`, `package_udev_rule` and `package_account`, for what a
   *package's own* maintainer scripts and files do and the engine does not.
   `meshtasticd`'s package enables and starts a boot service, ships a udev
   rule with a world-writable line (`1a86:5512`, mode 0666) and creates a
   system user and groups; before this there was no honest place for that
   (`udev_rule` and `group_create` are kinds the engine does not perform and
   refuses by name, which is the right reading of those two). The three are
   in `IMPLEMENTED_MODIFICATIONS` because the plan prints each as a note
   before the confirmation; the engine runs nothing for them and
   `uninstall` does not reverse them, which each entry's `reverse_hint` says.
   The disclosure is the point: a dry run that omitted a world-writable rule
   would be the approximation this project refuses.

The first unit to use both is `meshtasticd` (D-080's amendment, the pull
request for #308). The same pull request fixes a defect found by its
uninstall run, recorded here because the evidence is this decision's rule 7:
since #236 the engine's apt commands read `apt-get -o Acquire::Retries=3
install ...`, and the attribution that decides what `uninstall` may remove
read `argv[1]` as the verb, so it saw `-o`, attributed nothing, and left every
apt package behind as "installed, but not installed by Hammunition". The verb
is now the first non-option argument, and the engine's own `install
--simulate` pre-flight (rule 6), which exits 0 and installs nothing, never
attributes.


---

## D-041 — A manifest declares the kernel subsystems it cannot work without; the plan reads the running kernel's module tree and refuses or defers by name, never from the capability matrix, and never by building a module

**Date:** 2026-09-04. **Status:** accepted; built the same day, awaiting
the maintainer's review on the pull request.
**Depends on:** D-018 (external claims tested before published), D-024
(carry only what a distribution packages), D-031 (verify the effect, not
the exit status), D-039 (a profile member the target lacks is deferred by
name; a typed name still refuses), the rejected-list entry *a custom kernel*.
**Amends:** D-008's description of the packet core as resting on "the
kernel AX.25 stack" — see Q-019.

**What was measured.** Linux 7.1 removed the amateur-radio networking
subsystem — `net/ax25`, `net/netrom`, `net/rose`, every driver in
`drivers/net/hamradio` and their uapi headers — in merge
`64edfa65062dc4509ba75978116b2f6d392346f5` (2026-04-24). Debian removed
`ax25-tools` from testing on 2026-09-01 (#1143282: it no longer builds
without `linux/hdlcdrv.h`), which is the archive gap the Kali campaign
reported. On 2026-09-04, on the seven machines this project has:

| Kernel | `ax25.ko` |
|---|---|
| Debian 13 6.12.107, Parrot 7.3 7.0.13, Ubuntu 24.04 6.8.0, Ubuntu 26.04 7.0.0 | module; `socket(AF_AX25)` opens after `modprobe ax25` |
| Kali 7.1.5, Pop!_OS 24.04 VM 7.1.5, Pop!_OS 22.04 laptop 7.1.1 | absent; errno 97, `modprobe: FATAL: Module ax25 not found` |

The Pop!_OS VM still has its previous 7.0.11 tree installed, with
`ax25.ko.zst` in it, beside the 7.1.5 tree without. **The kernel is a fact
about the machine, not the distribution.** The overnight campaign
(`hammunition-overnight-2026-09-04`) had filed `packet` as installing
whole on Pop!_OS; every package arrived, and `kissattach` could never have
worked. Confirming by packages is necessary and not sufficient — issue
#27's shape, one layer down.

**The rule.**

1. **The manifest says what it needs.** `requires_kernel` is a list drawn
   from a closed vocabulary (`KernelFeature`, today `ax25` only). The
   vocabulary is exactly the set the probe can find — a test asserts the
   schema's `Literal`, the probe's module map and its description map are
   the same set — so a manifest can never name a subsystem the plan cannot
   check. It is declared only where the software opens `AF_AX25` sockets
   or configures the kernel stack and has no other mode: ten units, listed
   in `docs/reference/kernel-ax25.md`. Software with a userspace mode
   (Direwolf, pat, LinBPQ, YAAC, Xastir, QtSoundModem) declares nothing
   and its `known_problems` says which of its interfaces needs the kernel
   — for pat and Xastir read from their source, not their README.
2. **The plan reads `/lib/modules/<uname -r>/`**, never `lsmod` or
   `/proc/net/ax25`: on every kernel that carries it, `ax25` is a module
   nothing loads until a root `kissattach` does, and an unloaded module is
   not a missing one. Present as `kernel/net/ax25/ax25.ko*` or in
   `modules.builtin` → the unit plans. Present tree, absent module → a
   name the operator typed **refuses**, naming the unit, the release and
   the merge; a profile member **defers** with the same reason and the
   rest installs (D-039's shape, for a fact about the machine rather than
   the target). No module tree for the running kernel at all — a container
   on the host's kernel, which is every CI target — is **disclosed as
   unchecked** and the unit plans; absence of evidence is not evidence.
3. **The remedies are the ones that exist.** A distribution kernel that
   still carries the stack, or the userspace path. The refusal never
   offers to build the module: the out-of-tree `mod-orphan` suggested
   upstream is packaged by no distribution (D-024), and a kernel module
   we compile is a custom kernel by another name.
4. **It never enters the capability matrix.** The matrix is per target;
   this is per machine, and per reboot. Writing "Kali: ✗" would be false
   on a Kali box that kept its 7.0 kernel and would hide the same truth
   about an Ubuntu 24.04 box the day its HWE kernel crosses 7.1.

**What this is not.** Not a kernel-version check — a version number is a
proxy, and the module tree is the fact. Not a check that the module is
loaded, for the reason in rule 2. Not a general hardware-capability probe;
the vocabulary grows one measured entry at a time, and `netrom`, `rose`
and the `scc` driver are not in it because no manifest yet needs one that
`ax25` does not already settle.

**Consequences.** `z8530-utils2` declares `ax25` today and Q-019 asks
whether to retire it: its `scc` driver left in the same merge and the
hardware predates PCI Express. The `packet` profile page says which
members it withholds on such a kernel and that the Direwolf–pat–APRS
station still installs. `docs/reference/kernel-ax25.md` is the record and
carries the reproduction commands.

**Measured on 2026-09-04** with this engine (commit 383cf27) through
`scripts/vm_campaign.py`, each VM restored to its clean snapshot first:

| Machine | Unit | Outcome |
|---|---|---|
| Kali 2026.3, `7.1.5+kali-amd64` | `ax25-tools` | refused at plan time, two blockers: no apt candidate, *and* the kernel blocker naming `7.1.5+kali-amd64`, merge 64edfa65, the userspace path and D-024 |
| Kali 2026.3 | `linpac` | refused at plan time on the kernel blocker alone — the package is in Kali's archive, so the archive check would have let it through |
| Kali 2026.3 | `direwolf` | installed and confirmed, 5 s |
| Debian 13, `6.12.107+deb13-amd64` | `ax25-tools` | installed and confirmed, 4 s |
| Debian 13 | `linpac` | installed and confirmed, 2 s |

The `packet` profile as a whole, dry-run on the same Kali VM: eight members
deferred (`ax25-tools`, `linpac`, `aprsdigi`, `ax25-apps`, `ax25-xtools`,
`ax25mail-utils`, `axmail`, `uronode`), 23 apt packages and the four git
builds (`ardopcf`, `linbpq`, `qtsoundmodem`, `qttermtcp`) still planned,
exit 0. That run found one defect the unit tests had not: a member the
archive had already deferred (`ax25-tools`) had its reason *replaced* by the
kernel's, and the deferral's "a release that carries it needs no change
here" was then false of Kali's archive. A reason already recorded now
stands; the typed-name refusal shows every one. The other two declaring
units, `fbb` and `z8530-utils2`, are not `packet` members.

**Installed for real on 2026-09-05** (engine 6b8c080, the fix included,
both VMs restored to their clean snapshot first, `--whole-profiles`):

| Machine | `packet` | Deferred by name | Installed and confirmed |
|---|---|---|---|
| Kali 2026.3, `7.1.5+kali-amd64` | exit 0, 39 s | eight — `ax25-tools` and `ax25-xtools` on the archive (*apt on Kali GNU/Linux Rolling has no candidate*; both build from the `ax25-tools` source Debian removed), `linpac`, `aprsdigi`, `ax25-apps`, `ax25mail-utils`, `axmail`, `uronode` on the kernel | 29 checks: 24 apt packages, the four git builds executable under `/usr/local/bin`, `dialout` membership |
| Debian 13, `6.12.107+deb13-amd64` | exit 0, 61 s | none | 40 checks, the eight above among them |

Two things the real run corrected. The dry-run paragraph above had
`ax25-xtools` deferred on the kernel; that reading came from the pre-fix
output, where the kernel had overwritten every archive reason, and the
fresh run shows the archive reason it keeps. And the harness's own report
read *`packet` installed+confirmed* for Kali with no mention of the eight
— the deferrals are in the transaction log it filed (`transaction_begin`,
version 2), not in its table. That is the D-041 problem statement in
miniature, on the tool that exists to prevent it, and is fixed separately.
The campaign reports are under `~/.local/state/hammunition-campaigns/`
and the row-level evidence is in `docs/reference/kernel-ax25.md`.

## D-014 addendum, 2026-09-06 — venv loses a user, and three install notes are corrected by the archive

The landscape survey's Parrot probe (`docs/reference/coverage-matrix.md`)
contradicted three manifests' stated reasons for not using the archive.
Each was re-measured across the seven container targets and, where a
manifest changed, the change was run on a VM the same day.

- **`nanovna-saver` is apt on every target.** The manifest hash-pinned a
  venv on the claim that *no distribution packages it*; every target does
  (Debian 13 and Parrot 7 `0.7.3-1.1`, Kali and Ubuntu 26.04 `0.7.4~pre1-1`,
  Ubuntu 24.04 and Pop `0.6.3-1`). Debian's `0.7.3-1.1` is missing its
  PySide6 dependency (Debian #1112747, fixed in `0.7.3-2`, which trixie will
  not receive): the package installs and `NanoVNASaver` dies with
  `ModuleNotFoundError: No module named 'PySide6'`. The Debian/Parrot block
  installs `python3-pyside6.qtwidgets` beside it — what `0.7.3-2`'s control
  file adds — and cannot do so as a manifest-level `depends`, because Ubuntu
  24.04's `0.6.3` is PyQt6 and its archive has no `python3-pyside6.qtwidgets`
  (the plan refused on exactly that). Engine install and Xvfb launch on
  Debian 13, Parrot 7.3 and Ubuntu 24.04. The venv block and the ARM-only
  binary block the D-031 addendum above mentions are both gone; **venv's real
  users are now `not1mm`, `supersdr` and the `radiosonde-auto-rx` REVIVE.**
- **`qlog` is apt on Parrot and Kali** (`0.52.0-1~bpo13+1` from
  echo-backports, `0.52.0-1`), the same tag it builds from git elsewhere.
  The manifest had said only Kali packages it. Engine install and launch on
  both.
- **`wsjtx-improved` stays on the vendor `.deb`, and now says why.** Parrot
  and Debian 13 offer `wsjtx-improved` `2.8.0+250501+repack-1`, Kali
  `3.1.0+260522+repack-1`; the archive package carries `Provides: wsjtx`,
  `Breaks: wsjtx`, and its `-data` breaks `wsjtx-data`. Installing it beside
  the archive's `wsjtx` is exactly the D-022 displacement — and the engine
  cannot see it: `backends/apt.py parse_simulation` reads only `Inst` lines,
  `Remv` lines are ignored, and the runner is `apt-get install --yes` with
  no `--no-remove`. An apt block here would remove `wsjtx` silently. That
  was an engine gap, filed as issue #42 and closed by the D-022 amendment of
  2026-09-07; the manifest's archive block followed (issue #24).

Same shape as D-025: each claim was true, or believed, when written, and
became decisive only when a probe read the archive against it.

---

## D-042 — EmComm Tools OS Community is the sixth inventory source: its delta is measured, its rig model is studied and reimplemented as catalog data, and none of its code is taken

**Date:** 2026-09-06. **Status:** accepted (maintainer, from the landscape
survey in `docs/reference/prior-art.md`); the inventory is built and this
record accompanies it on the pull request. The four follow-on sub-projects
named under *Consequences* are each their own pull request and are not
decided by this record beyond their order.
**Depends on:** D-001 (an inventory source is never a base), D-011
(provenance rules), D-014 (backends by measurement), D-018 (external claims
tested before published), D-024 (pin what a distribution packages), D-028
(an identifier naming a chip may not name a `/dev` node), D-029 (the
hardware role), D-033 (weigh adoption, state the position).
**Amends:** D-017. "The five-source union" becomes six. Nothing in D-017's
staging moves: ETC's software delta is a sixth stage after the five it
lists — eleven units, most of them apt — and the 1.0 scope table in
`docs/SCOPE.md` gains a row. The rig model and the offline-data layer are
approved work in the order below and are not 1.0 gates unless the
maintainer stages them.

**What was measured.** EmComm Tools OS Community (ETC), by Gaston Gonzalez
(KT7RUN, The Tech Prepper LLC), studied at commit `4ec08ce` (2026-05-02),
release 2026.04.01.R6 (6.0.0), from a clone in the gitignored
`reference/` tree. `scripts/gen_etc_inventory.py` reads every script the
installer can run and renders `docs/reference/etc-inventory.md`; the
numbers below are that page's.

- **62 units** in `scripts/`; `install.sh` runs 59, one only with
  `ET_EXPERT` set. Curated: **11 delta, 21 overlap, 9 glue, 4 data, 17
  base**. The delta is the offline-cyberdeck layer — Navit with
  `maptool`, kiwix and the zim tools, `dict`/`dictd`/GCIDE, QGIS,
  mbtileserver, mbutil — plus two packet clients (Paracon, Chattervox),
  Artemis, GPA and Paranoia Text Encryption for the AmRRON signed-traffic
  workflow. Every overlap unit resolves to a manifest the catalog already
  carries; the inventory names the 24.
- **The base is Ubuntu 22.10 (kinetic).** `update-apt.sh` repoints apt at
  `old-releases.ubuntu.com`; kinetic reached end of life on 2023-07-20.
  ETC is an ISO built with Cubic on a release that receives no security
  updates, and the 140 apt package names it installs are that release's.
- **34 units fetch from the network; none verifies what it fetched.**
  `download_with_retries` in `et-common` accepts a sha256 as its third
  argument. Sixteen scripts call it; all sixteen pass two arguments.
  Seven fetch at `latest`, `master`, `nightly` or with no version at all —
  `linbpq` and `QtTermTCP` from cantab.net's download directory,
  `YAAC.zip`, SDR++'s nightly `.deb`, ETC's own dump1090 fork at
  `master.zip`, osmocom `rtl-sdr` at the branch head (after purging and
  `rm -rf`-ing the archive's `librtlsdr` — the D-022 pattern done harder),
  and the unused `install-qttermtcp-from-source.sh`.
- **The licence is split.** `LICENSE` carries Apache-2.0 for the scripts
  and overlay (Copyright 2024 The Tech Prepper LLC) behind a separate
  non-commercial, no-modification notice for the logos and images.
- **The rig model is not the naive symlink trap.** Sixteen udev rule files
  write four role symlinks: `/dev/et-cat` (11 rules), `et-audio` (13),
  `et-gps` (4), `et-sdr` (3). Chip identifiers repeat across rigs —
  `0d8c:0012` in four files, `08bb:2901` in four, `10c4:ea70` in three —
  and the rules disambiguate by `PROGRAM="udev-tester.sh <radio>"`, which
  reads `conf/radios.d/active-radio.json`: **the operator has said which
  radio is connected, and udev trusts the operator.** Twenty-one radio
  definitions, 40 `et-*` wrappers that configure each application for the
  active radio, and `et-radio`/`et-mode` to select them. An empty
  `85-brltty.rules` shadows the system one and `brltty-udev.service` is
  masked, because brltty claims serial adapters ETC's operators use.

**The rule.**

1. **ETC is an inventory source under D-001**, credited in the README and
   listed in `docs/SCOPE.md` and `CLAUDE.md` beside the five. Its
   inventory is generated and regenerable, never typed; the generator's
   curation is tested against the clone both ways — every shipped script
   curated, every catalog name real.
2. **No code is taken.** Apache-2.0 permits it, and `prior-art.md`'s survey
   called `et-radio`/`et-mode` the best borrow available. It is the best
   *idea* available. The code is 40 bash wrappers, each of which knows
   which application it wraps and where that application keeps its
   configuration — install logic and package list intertwined, the AHRL
   architecture this project exists to replace (D-001 gave the same answer
   for 73Linux for a different reason). What is reimplemented is the
   model: a radio is data (`catalog/hardware/devices/`, a `rig` class), an
   operator's selection is station configuration (the open question the
   D-004 amendment records), and an application's rig settings are a
   templated `config_files` block on the manifest that already carries
   its install. The reimplementation is sub-project 3 below and is not
   decided here.
3. **The role symlinks are not carried.** `/dev/et-cat` on `10c4:ea70` is a
   CP2105 claim, and the same identifier is a Digirig DR-891, an FTX-1 and
   an FT-991A in ETC's own rules (`08bb:2901` is four Icoms). ETC's answer — trust the operator's
   selection — is a real answer, and it is one radio at a time by
   construction. D-028's answer is the by-id path systemd already
   provides, plus permissions, plus a label only where the evidence
   supports one. The operator-selection idea survives in the station
   configuration, where it can name a `/dev/serial/by-id/` path without a
   symlink that lies when a second radio is plugged in.
4. **The delta is dispositioned one unit at a time**, not carried as a
   set, and every unit that arrives arrives pinned and verified (the
   security requirements are unchanged by the source having none). GPA is
   the first that cannot arrive by apt: Debian 13 offers no `gpa`
   candidate (measured 2026-09-06 on the campaign VM), so it is a tarball
   build or nothing. `mbutil` needs python2, which left Debian with
   bullseye; its disposition is not CARRY as it stands.
5. **The offline-data layer is a category of its own.** Maps, Wikipedia
   ZIMs and reference PDFs are not packages; they are large, versioned
   downloads with their own licences (Geofabrik ODbL, Wikimedia CC BY-SA)
   that belong in `/etc/skel` for ETC because ETC ships an image. What
   they are here — a `data` backend, a profile, or documentation that
   names the sources — is sub-project 5.

**Why the source is worth a decision.** ETC is the only project in the
landscape that treats *the rig* as the thing being configured rather than
the application, and the only one with an offline-data story. Both are the
questions this project's hardware role (D-029) and station configuration
(D-035) are circling, answered by someone who has shipped five recorded releases of
an answer. Reading it cost a clone and a parser; not reading it would have
meant rediscovering, in the field, that brltty claims a MicroFox-50.

**Consequences.** Five sub-projects, in order, one pull request each:

1. *This record and the inventory* — `gen_etc_inventory.py`,
   `etc-inventory.md`, the SCOPE row, the README credit, the `CLAUDE.md`
   source section, the dispositions section.
2. *brltty* — measure first whether any of the seven targets ships a
   brltty rule that claims a catalogued identifier, then decide whether a
   `file_shadow` system modification (an empty rule file in
   `/etc/udev/rules.d/` masking `/lib/udev/rules.d/`'s) is the right
   shape or whether the existing `distribution_disabled` basis covers it.
   **Done 2026-09-12, D-047:** neither; measured on all seven targets in
   `docs/reference/brltty-inventory.md`.
3. *Rig plug-and-play* — a `rig` hardware class, the FT-991A first because
   it is owned and on the bench, station configuration carrying the
   operator's selection.
4. *The software delta* — Paracon, Chattervox, Artemis, GPA and the
   `et-*` ideas as `config_files`, each dispositioned in
   `dispositions.md` with its licence and liveness re-verified (D-018,
   D-032: Chattervox's last tag is 2019-03 and its last push 2020-01).
   **Software done 2026-09-12, D-048:** three manifests, two retirements,
   Chattervox left to the maintainer with its test results. The `et-*`
   config ideas wait on station config and the rig class (sub-project 3).
5. *The offline-data layer.*

`docs/reference/prior-art.md`'s recommendation 4 ("lift `et-radio`
under Apache-2.0") is superseded by rule 2: same finding, different
conclusion, and this record says why.

## D-043 — An installed tree belongs to the operator the run is on behalf of, by an explicit step the log shows; never by what `cp -a` happens to preserve

**Date:** 2026-09-07. **Status:** accepted (maintainer, issue #38, option 1
of the two offered there). **Depends on:** D-018 (the claim was measured
before this record was written), D-031 (verify the effect, not the exit
status), CLAUDE.md's privilege rule (drop to user where possible).
**Amends:** nothing. It records what the engine already did and makes the
engine say so.

**What was measured.** On the Debian 13 guest, 2026-09-05, after
`hammunition install yaac` (binary, `install_tree: true`),
`radiosonde-auto-rx` (venv payload) and `mshv` (source,
`install_tree: true`):

```
root:root             755 /usr/local/share/hammunition
chiefgyk3d:chiefgyk3d 775 /usr/local/share/hammunition/yaac
drwxrwxr-x chiefgyk3d     /usr/local/share/hammunition/mshv
drwxrwxr-x chiefgyk3d     /usr/local/share/hammunition/radiosonde-auto-rx
```

The transaction log explained it: `["cp", "-aT", "<build>/src",
"/usr/local/share/hammunition/yaac"]` with `requires_root: true`. `-a`
preserves ownership, the build tree is unpacked by the operator, and root
copying it keeps the operator as owner. A `make install` under the same
prefix produces root-owned files. So tree units and binary units differed,
nobody had chosen it, and nothing in the docstring, this record,
`DESIGN.md` or `transaction-log.md` said trees were meant to be anyone's.
PR #35 originally claimed the tree was root-owned and was corrected after
this measurement (D-018).

Two units run *because* of the accident. MSHV reads settings, resources and
logs from directories beside its executable (`source-build-gaps.md` #6);
`radiosonde-auto-rx` writes `log/` under its working directory. A
root-owned tree would break both launchers on their first write.

Measured again on 2026-09-07, on the same guest, for this record:

- `sudo cp -aT --no-preserve=ownership <build>/src /tmp/np` gives
  `root:root` throughout; `sudo cp -aT` alone gives `chiefgyk3d:chiefgyk3d`
  throughout. The flag is what removes the accident.
- `sudo chown -R -h user: <tree>` changes the tree, its files and its own
  symlinks; a symlink inside the tree pointing at a root-owned file or
  directory outside it leaves the target root-owned. Without `-h`,
  `chown -R` also left targets alone, but `-h` is the documented guarantee
  and the one the command carries.
- With this change, `hammunition install yaac` on the guest: the plan
  prints `sudo chown -R -h -- chiefgyk3d: /usr/local/share/hammunition/yaac`
  under a description that says why; the run completes 10 of 10 commands
  confirmed; `/usr/local/share/hammunition` stays `root:root 755`; the tree
  is `chiefgyk3d:chiefgyk3d 775` and **0 of 526** entries under it are owned
  by anyone else; `hammunition uninstall yaac` removes it.

**The rule.**

1. **A tree installed under a privileged prefix is handed to the operator
   the run is on behalf of** — `--user`, else `$SUDO_USER`, else `$USER`,
   the same resolution that owns the transaction log, the artifact cache
   and the build tree. It is consistent with `paths.py`'s owner-aware
   directories and with "drop to user where possible", and it is what two
   shipped units need.
2. **The hand-over is its own step.** `tree_install_commands` copies with
   `cp -aT --no-preserve=ownership` and then plans
   `chown -R -h -- <operator>: <tree>` as a fourth, root-requiring command
   with a description saying who runs the software and why the tree is
   theirs. The plan prints it, `--dry-run` shows it, the transaction log
   records it. Ownership is never again a property of who unpacked the
   build.
3. **`-h` is not optional.** A tree may carry symlinks; `-h` changes the
   link and never follows it, so a link pointing outside the tree cannot
   hand root's files to the operator.
4. **Without an operator, root keeps the tree.** A run with nobody to hand
   the tree to (no `--user`, no `$SUDO_USER`, no `$USER`) plans no chown and
   gets what `--no-preserve=ownership` under root produces. A prefix that
   needs no root is written as the operator already and plans no chown
   either.
5. **The parent stays root's.** `/usr/local/share/hammunition` is created
   by `install -d` under root and is not chowned; only the unit's own tree
   is. An operator can replace the contents of their tree, not add or
   remove trees.
6. **Every tree unit discloses it.** `scripts/gen_package_reference.py`
   renders an *installed tree* bullet under "What it changes on your
   machine" for any manifest whose block carries a `tree_marker` — the
   schema makes that field mandatory exactly when a tree is installed, so
   the generator cannot miss one and nobody types it by hand. The bullet
   names the path, the hand-over, the reason, the shared-machine
   consequence, that the tree is replaced whole on every install, and the
   undo. `tests/test_docs_generated.py` asserts it for all five units:
   `yaac`, `mshv`, `js8spotter`, `radiosonde-auto-rx`, `supersdr`.
7. **`uninstall` has nothing new to undo.** The chown is a property of a
   tree the existing `rm -rf` step removes; the log records it for the
   reader, not for rollback.

**What this admits.** A launcher under `/usr/local` executes code the
installing user can modify. On a single-operator workstation that is one
trust domain; on a shared machine it is not, and the generated page says
so before anyone installs. An application's own updater can also rewrite
the tree (YAAC's *Help > Check for Updates*), after which the transaction
log describes a tree that no longer exists, and the next install of the
manifest replaces it whole — settings MSHV kept beside its executable
included. Both are consequences of the design that made these units run at
all, and this record's job is to have them written down rather than
discovered. Option 2 in the issue — root-owned trees with each
beside-the-executable writer relaunched from an operator-owned copy or an
XDG state directory — costs a manifest field and per-unit work for at
least four units, and is the shape to reach for if a shared-machine
deployment ever becomes a target. It is not one now.

**Consequences.** `tree_install_commands` takes `owner`; `SourceBackend`,
`GitBackend`, `BinaryBackend` and `VenvBackend` carry it and
`cmd_install` passes the operator it already resolves. Eight tests cover
the step, its absence, and all four backends; `docs/packages/` is
regenerated; `source-build-gaps.md` #6 records the closure. Issue #38 is
closed by this record.

## D-044 — `install` refreshes the apt lists by default, when the transaction has apt work; `--no-refresh` is the opt-out

**Date:** 2026-09-10. **Status:** accepted (maintainer, Q-018, option A as
recommended). **Depends on:** D-016 (the plan resolves before anything runs),
D-038 (one automatic retry of the *plan* is the precedent; a retry of a
*command* the dry run never printed is not), D-010 (install-once-and-rot is
the pattern to refuse, at every scale). **Amends:** nothing. `--refresh`
existed; this makes it the default.

**What was measured.** On the Parrot whole-profile campaign of 2026-09-03
(`docs/reference/vm-campaign-profiles.md`), the guest's `clean-baseline`
was four days old. Its lists named `glib2.0 2.84.4-3~deb13u3`; the pool
had moved to the next revision; and six of fifteen profiles — `digital-modes`,
`electronics`, `listening`, `logging`, `packet`, `propagation` — passed
the plan and died four commands in with `404 Not Found` on the pool. The
catalog was right and the machine was unchanged, because apt fetches every
archive before it unpacks any. Four days is an ordinary age for an
operator's lists. AHRL and 73Linux both run `apt update` unconditionally
before anything else, which is why neither has ever reported this failure.

Whether the lists are stale is **not measurable on Debian**: list files
carry the archive's own `Last-Modified` as their mtime, an `apt-get update`
that finds nothing changed touches nothing, and the
`/var/lib/apt/periodic/update-success-stamp` that Ubuntu writes comes from
a conf.d snippet Debian does not ship. So "refresh when old" (Q-018 option
B) was rejected on the evidence, and "retry on 404" (option C) was rejected
because it would run a command `--dry-run` never printed.

**Decision.**

1. **The refresh is the default.** `hammunition install` puts `apt-get
   update` at the head of the transaction. It is disclosed in the plan
   like every other command, so `--dry-run` shows it and the real run
   cannot differ.
2. **Only when apt will be asked to resolve something.** The update runs
   when the plan has an apt step or a vendor `.deb` (whose dependencies apt
   resolves from the lists). A re-run with everything already installed
   stays a no-op, and a source-only plan on a station with no uplink never
   opens with a network command. A just-added third-party repository
   (D-040) always gets the update, whatever the flag says — apt has no
   index for it until one runs.
3. **`--no-refresh` turns it off.** For a local mirror, or a field station
   with no uplink that knows its lists are current. `--refresh` still
   parses, so every documented example from before this record still runs.
4. **Empty lists under `--no-refresh` still refuse**, and the blocker
   names the flag to drop rather than one to add. Without the flag, empty
   lists are a sequencing fact: the plan says the candidate check cannot
   be done before the update, and proceeds.

**What this does not do.** The plan's candidate check still resolves
against the lists as they are when the plan is built; the refresh runs
after the plan is shown and confirmed. A package the fresh lists would
newly offer is still refused at plan time, and one they would newly lack
still fails at the fetch, one step later, with the stale-lists diagnosis
that already exists. Re-planning against the fresh lists — a second,
better plan, disclosed as such — is the follow-on Q-018 named, and it is
worth doing; it is not this record. The stale-lists diagnosis keeps the
two cases it can still reach: a `--no-refresh` run, and a mirror that
moved between the run's own update and the fetch.

**Consequences.** `--refresh` became `argparse.BooleanOptionalAction`
defaulting to true; `commands_for` emits the update only when
`apt_to_install` or a `.deb` is present, or a repository is added; the
empty-lists blocker and note, the stale-lists diagnosis and the
already-configured-repository remedy say what is now true; five tests
cover the default, the opt-out, the no-apt-work skip, and both empty-lists
paths through `main()`. `docs/reference/cli.md` documents `--no-refresh`
and the step order. Q-018 is closed by this record.
## D-045 — The packet core is userspace-primary; `z8530-utils2` is retired on tested evidence; the out-of-tree AX.25 module is measured, named, and not built until a distribution packages it

**Date:** 2026-09-10. **Status:** accepted (maintainer, Q-019: both
recommendations taken, with the module question asked and answered).
**Depends on:** D-041 (the plan reads the running kernel; never a module we
build), D-024 (carry what a distribution packages), D-018 (external claims
tested before published), D-032 (liveness is the head commit, never
`updated_at`), `PARITY-POLICY.md` (every unit gets a verdict we tested).
**Amends:** D-008 (the packet-core statement; amendment recorded there).

**What was measured.** Two things Q-019 rested on and one it did not ask.

1. `z8530-utils2` configures the `scc` driver for Z8530 HDLC cards. The
   driver left the kernel in the same merge as `net/ax25`
   (`64edfa65`, 2026-04-24; `git show --stat` lists
   `drivers/net/hamradio/scc.c`), the cards are ISA and early PCI, and the
   manifest already called both \"museum conditions\". Seven machines' module
   trees were read on 2026-09-04 (`docs/reference/kernel-ax25.md`). That is
   a verdict tested by us, not an AHRL comment inherited.
2. On every kernel this project has that is 7.1 or newer, the userspace
   packet path — Direwolf or QtSoundModem as the modem; pat, LinBPQ, YAAC
   and Xastir over KISS or AGW — installs and is the whole station. The
   `packet` profile was installed whole on Kali 7.1.5 and Debian 13 6.12 on
   2026-09-05 with that result (D-041's table). D-008 and the profile prose
   still described the core as the kernel stack fed by Direwolf.
3. The maintainer asked whether the kernel stack can be added back as a
   module. **It can, and it was measured on 2026-09-10** rather than
   assumed either way. The netdev maintainer who removed the subsystem
   publishes it as an out-of-tree tree, `linux-netdev/mod-orphan`, created
   2026-04-20: one `Kbuild` covering `net/ax25`, `net/netrom`, `net/rose`,
   `drivers/net/hamradio` and the merge's other orphans; `make` against
   the running kernel's headers with a force-included compatibility header;
   sources keep their kernel SPDX headers (GPL-2.0-or-later on `af_ax25.c`);
   **no tags, no releases, no README, no top-level licence file**; head
   commit 2026-06-16, last AX.25-family change 2026-06-01 (two ROSE fixes,
   Bernard Pidoux); **packaged by no distribution** — not Debian, not
   Ubuntu, not the AUR, on that date. **Built here, not loaded:** against
   the maintainer's laptop's 7.1.1 headers, `ax25.ko`, `netrom.ko` and all
   ten `drivers/net/hamradio` modules — `mkiss`, `6pack`, `bpqether`,
   `scc` among them — compile and link with a matching `vermagic`; the
   whole tree's `make` fails, because `net/rose` calls a kernel function
   whose signature changed and `net/atm` needs a header 7.1 no longer
   ships. Nothing was `insmod`ed and no socket was opened; the measurement
   is that the code compiles, not that it works.

**Decision.**

1. **`z8530-utils2` is retired**, `retire_reason: world_changed`,
   `status_verdict: tested`, with the merge as the evidence. The manifest
   stays in the catalog so an operator who looks finds the reason (D-005's
   shape, as `noaa-apt`); a 6.12 machine with an ISA slot can still install
   it by hand, and the manifest says so. That `scc.ko` compiles out of tree
   (point 3) does not move this: the driver is packaged by nobody and the
   cards have no modern host.
2. **D-008 is amended: the packet core is userspace-primary.** The kernel
   stack is the fuller station where the kernel carries it, planned or
   deferred by name per D-041, and a bonus rather than the foundation. The
   getting-started packet guide, when written, teaches Direwolf-to-pat first
   and `kissattach` second.
3. **The out-of-tree module is noted, not carried.** D-041's \"never a
   module we build\" stands, for three reasons that survive the module
   existing: a kernel module is the one artefact this project has said it
   will never build, and building one that has to be rebuilt for every
   kernel the machine boots means a DKMS wrapper that would be *our*
   packaging of code nobody else packages — precisely what D-024 refuses,
   and the tree's own `make` already fails on 7.1.1 without a `Kbuild` we
   would have to edit, which is the first patch of a fork;
   the code's own maintainers removed it for lack of maintenance, and a
   tree with no tags and no releases offers nothing to pin (D-024's review
   signal is absent by construction); and `ax25-tools` is leaving the
   archives regardless (#1143282), so the module would restore the sockets
   and not the tools that use them. **The condition that reopens this** is
   a distribution packaging the module — a `-dkms` package in Debian is the
   obvious shape. That is the signal D-024 asks for, and the day it exists
   the `requires_kernel` refusal gains a third remedy and this record is
   amended. Nobody watches GitHub for it; `kernel-ax25.md` says where to
   look.

**Consequences.** `z8530-utils2.yaml` carries the retired status block;
`catalog/profiles/packet.yaml` describes the station userspace-first;
`docs/reference/kernel-ax25.md` carries the module measurement and the
reopening condition; generated pages regenerated. Q-019 is closed by this
record. The remaining kernel-side question — the `scc`/`netrom`/`rose`
vocabulary — stays closed by D-041 until a manifest needs an entry `ax25`
does not settle.

## D-046 — Post-1.0: listening before repeaters; decoders in `listening`, recorders in `rf-security`; Pi-Star's binary set is a D-024 pin source; ASL3's repository is the D-040 case with no gate beyond the fingerprint

**Date:** 2026-09-12. **Status:** accepted (maintainer, Q-020, all four
calls as recommended). **Depends on:** D-003 (flat tags with overlap),
D-021 (disclose, never adjudicate), D-024 (pin what a distribution
packages), D-034 (the line is transmit, not topic), D-035 (a missing
station value defers one file), D-040 (third-party archives on the
fingerprint alone). **Amends:** nothing; it fills in the four blanks
`docs/SCOPE.md` stages 9 and 10 left open on 2026-09-07.

**What was measured** is in SCOPE.md's post-1.0 section and is not
repeated here: seventeen names across seven targets, two in any archive,
both already carried; OP25's live fork, Trunk Recorder, SDRTrunk and
DSD-FME's liveness and licences; the G4KLX suite untagged and unpackaged;
Pi-Star V4.3.7 (2026-05-01) as the one maintained thing that builds and
ships it; AllStarLink ASL3 only from its own repository.

**Decision.**

1. **Track A before Track B.** Trunked and digital-voice listening — OP25,
   Trunk Recorder, SDRTrunk, DSD-FME — is four units on backends 1.0
   ships, in profiles that exist, with no station-config dependency and
   nothing that transmits. Track B waits on station config (SvxLink), a
   D-040 manifest (ASL3) and a pin source plus hardware (MMDVM). The one
   piece of B that rides for free is SvxLink's `config_files` block, taken
   the day station config exists rather than when B begins.
2. **The decoders go in `listening`; OP25 and Trunk Recorder in
   `rf-security`.** An operator with one dongle who wants to hear the local
   P25 system belongs in the on-ramp profile; whole-network recording of
   trunked systems is the posture `rf-security` already frames. D-003's
   flat tags allow a unit in both where it fits both. SDRTrunk and Trunk
   Recorder do the same job on different stacks, and that overlap is one
   `overlaps.md` row, written by whichever manifest lands first.
3. **Pi-Star's shipped binary set counts as "a distribution packages it"
   for D-024.** The G4KLX suite pins the commits the current Pi-Star
   release ships. Pi-Star chose those commits, built them, and shipped
   them to the largest hotspot install base, which is the review signal
   D-024 asks for; its successor is CI-built and accepts pull requests, so
   the choice is reviewable. Reading the commits out of an image is a
   measurement to script and record, not a field to cite, and the script
   is part of the first G4KLX manifest. Pi-Star's ARM builds prove the
   commits, not our x86 builds; those are measured on our targets as every
   source unit is.
4. **ASL3's own apt repository is the D-040 case**, and the landscape
   survey's "never add a third-party APT archive" is read through D-040 as
   CLAUDE.md already reads it: the distribution offers nothing, the
   manifest pins the signing-key fingerprint, the archive is added only on
   the operator typing that fingerprint, never on `--yes`, and both files
   come out on uninstall. **No consent gate beyond the fingerprint.** A
   node on the amateur bands is licensed operation, the same thing `flrig`
   keying a transceiver is; the profile prose discloses coordination and
   unattended-station conditions (D-021's half) and adjudicates nothing.
   This is the second manifest family after `code`/`codium` to rely on the
   D-040 reading and the first with transmit behind it, and that fact is
   what this record exists to have written down.

**Consequences.** SCOPE.md stages 9 and 10 carry the rulings in place. No
manifest changes yet: everything here is post-1.0, and the first Track A
manifest is where the work starts. Q-020 is closed by this record, and no
question is open in `docs/QUESTIONS.md` on this date.

## D-047 — brltty is measured per target, not purged or shadowed; the sweep reads every syntax a rule can name a pair in

**Date:** 2026-09-12. **Status:** accepted; built the same day, awaiting
the maintainer's review on the pull request. **Depends on:** D-042
(sub-project 2 asked this), D-028 (an identifier naming a chip may not name
a device), D-029 (the hardware role includes honest documentation of what
nothing solves), D-031 (verify the effect, not the exit status), D-022
(coexist, disclose, never remove silently), CLAUDE.md's rejected-list entry
*anything that reconfigures the user's OS wholesale*. **Amends:** the
udev-inventory page's "nothing is filtered" claim, which was true of
packages and false of syntax.

**What was measured.** `scripts/run-brltty-probe.sh` downloaded each
target's `brltty` (never installed it) and read what it ships;
`scripts/gen_brltty_inventory.py` renders `docs/reference/brltty-inventory.md`
from the probes, with the catalog intersection computed live.

1. **Four of seven targets ship no brltty udev rules at all.** Debian 13
   (6.7-3.1+deb13u3, amd64 and arm64), Parrot 7.3 (the same package) and
   Kali (6.9.1+repack-1) put only *examples* under `/usr/share/doc/brltty/`.
   Nothing in those archives Depends on or Recommends brltty; `orca` and
   `speechd-el` Suggest it. There is nothing to shadow and nothing arrives
   by default.
2. **The Ubuntu family ships `85-brltty.rules` and installs brltty by
   default** — Recommends of `ubuntu-desktop`, `ubuntu-desktop-minimal`,
   `xubuntu-desktop` and six more desktops (Mint's own `mint-meta-*`
   metapackages do not name it; the rules arrive there through Ubuntu's).
   Ubuntu commented out the generic-bridge lines in 2022 (bug #1958224) and
   the file says so beside each one.
3. **What is still enabled and in our catalog.** On Ubuntu 24.04 and Mint
   22.3 (brltty 6.6-4ubuntu5): `0403:6001` only when the USB manufacturer
   string is `Hedo Reha Technik GmbH` or `Tivomatic Oy` — an FTDI rig
   cable's string is `FTDI` and never matches — and **`1a86:7523`, the
   CH340, only behind a `1a40:0101` parent hub.** That hub is the Terminus
   FE 1.1 inside the Zoomax display, and also inside a great many cheap
   four-port hubs. A CH340 cable through such a hub is claimed. On Ubuntu
   26.04 (6.7-1ubuntu6) the CH340 line is commented out too, and the two
   vendor-string FTDI lines are all that remain.
4. **The maintainer's own laptop** (Pop!_OS 22.04, brltty 6.4-4ubuntu3,
   not a target) carries the unqualified `ENV{PRODUCT}=="1a86/7523/*"`
   line, enabled: every CH340 on that machine is claimed, hub or no hub.
   Its `brltty-udev.service` is a static unit started by the rule.
5. **The udev sweep could not have seen any of this.** brltty writes
   `ENV{PRODUCT}=="403/de58/*"`; the sweep's parser read only
   `ATTRS{idVendor}`, and brltty had zero rows in an inventory that said
   nothing was filtered. Reading the two other syntaxes added 25 rows from
   six Debian 13 packages (tlp-rdw, udisks2, libhackrf0's rad1o lines among
   them), none touching a catalogued identifier — and one precedence bug
   caught by its own test: brltty's CH340 line names the device by
   `PRODUCT` and its *hub* by `ATTRS`, and a parser that tried `ATTRS`
   first filed the hub as a braille display.

**Decision.**

1. **No `file_shadow`, no `package_purge`, no engine change.** ETC's empty
   `85-brltty.rules` and AHRL's unconditional purge each remove a blind
   operator's braille support from every machine to fix a collision that
   exists on two of seven targets, for one chip, behind one hub. On the four
   Debian-family targets there is no file to shadow. A project that augments
   an existing system does not delete accessibility software as a side
   effect of installing a rig-control program.
2. **`distribution_disabled` does not cover it either.** That basis says an
   identifier does not name a device, citing a rule a distribution
   commented out. The colliding rule is *enabled*; the basis is the wrong
   shape for it, and D-028 already carries `1a86:7523` as
   `kernel_generic_driver`.
3. **The answer is the measurement and the troubleshooting entry.**
   `brltty-inventory.md` is generated and `--check`ed like every other
   reference page, so the day Ubuntu changes the file the page goes stale by
   name. `docs/troubleshooting/running.md` tells an operator whose CH340
   vanished on Ubuntu 24.04 or Mint what happened and the two fixes that
   exist — unplug from the hub, or remove `brltty` *if no one on the
   machine needs it*, which is the operator's call and never the engine's.
   The `badgelife` class entry for `1a86:7523` names brltty among the
   claimants.
4. **The sweep parser is a module with a test.** `scripts/udev_rule_pairs.py`
   reads `ATTRS{idVendor}`, `ENV{PRODUCT}` and `ENV{ID_VENDOR_ID}`, device
   syntaxes before parent-walking ones, mounted into the sweep container by
   the runner. Its tests carry the brltty lines that proved each case and
   the falsification that the old regex returns nothing for them.

**What would change this.** A target whose *default* brltty carries an
unqualified generic-bridge line again — the 2022 state — is the case for
carrying a targeted rule of our own that unsets what brltty's set for the
catalogued device, file-ordered after `85-brltty.rules`; a `udev_rule`
modification the schema already has, never a shadow of the whole file. No
target does today, and the page will say when one does.

**Consequences.** `scripts/brltty-probe.sh`, `scripts/run-brltty-probe.sh`,
`scripts/gen_brltty_inventory.py` (`--check`, in the no-op test),
`docs/reference/brltty-inventory.md`, seven probe files under
`reference/probes/`; `scripts/udev_rule_pairs.py` and
`tests/test_udev_rule_pairs.py`; the Debian 13 sweep re-run and
`udev-inventory.md`, `usb-ambiguity.md`, `ambiguous-ids.yaml` and
`programmer.yaml` regenerated from it; the troubleshooting entry; the class
note. D-042 sub-project 2 is closed by this record.

## D-048 — The EmComm Tools software delta: Paracon, Artemis and GPA carried on measured routes; Artemis's vendor `.deb` refused by name; Chattervox left to the maintainer with its test results

**Date:** 2026-09-12. **Status:** accepted; built the same day, awaiting
the maintainer's review on the pull request. **Depends on:** D-042
(sub-project 4), D-018 (claims tested before published), D-024 (never
build what apt provides; own pins only when nothing packages it), D-032
(liveness is the head commit), D-037 (Node only from the distribution,
never fetched), D-045 (the packet core is userspace-primary), D-022
(coexist, never displace silently), `PARITY-POLICY.md`. **Amends:**
nothing.

**What was measured, 2026-09-12.** Every upstream re-read by API (licence,
default-branch head, releases and their assets); every artifact fetched
and hashed from the download itself; `apt-cache policy` for every apt name
the three manifests use, on all seven targets; and each unit exercised on
a Debian 13 container.

1. **Paracon** (MIT, head 2025-10-12, release 1.3.0 the same day) is one
   `.pyz` with its dependencies inside. `paracon --version` printed
   `Paracon 1.3.0` on Python 3.13.5. It speaks AGWPE to Direwolf and never
   opens an `AF_AX25` socket, so it is the packet terminal that works on a
   7.1 kernel — the reason D-045 needed one.
2. **Artemis** (GPL-3.0, head 2026-07-22, release 4.2.0) stopped shipping
   the Linux zip ETC used; 4.2.0's Linux assets are a `.deb`, an Arch
   package and an RPM. The `.deb` was fetched (190 MB) and read: its
   control file says `Package: artemis`, and **every target's archive
   already has an `artemis`** — the Sanger genome browser, at 18.2.0.
   Installing the vendor file would put this program under that name one
   version *below* the archive's, and the next `apt upgrade` would replace
   it with a genome browser. It also Depends on `libpython3.12`, which
   Debian 13, Parrot, Kali and Ubuntu 26.04 do not carry. The upstream
   tree is a plain Python package with four PyPI dependencies, so it is
   carried as a venv — the 4.2.0 source tarball as payload, requirements
   compiled with hashes by uv — and the pinned set installed and
   `import artemis` succeeded on Python 3.13.5. The window has not been
   opened from that install; the manifest says so.
3. **GPA** 0.11.1 (2026-02-12, the release that builds against gpgme 2.x)
   is in Ubuntu 24.04 and Mint at 0.10.0 and Ubuntu 26.04 at 0.11.0, and in
   no Debian-family archive. The gnupg.org tarball's detached signature
   verified against the GnuPG distribution signing key; the four `-dev`
   packages configure.ac names have candidates on all seven targets;
   configure and make exit 0 on Debian 13 and `gpa --version` prints
   0.11.1.
4. **Chattervox** (GPL-3.0 by its LICENSE file, head 2019-03-17, every
   release a prerelease): the 0.7.0 bundle runs `--help`; the source
   builds on Debian 13's Node 20 with `npm ci --ignore-scripts` and runs
   `--version`; `kiss-tnc` loads without serialport's native build.
   Whether a KISS port *opens* without that build is untested — the
   container has no TNC — and two of its dependencies are git commits
   rather than registry packages.

**Decision.**

1. **Paracon, Artemis and GPA are ADD**, with manifests, on the routes
   above. GPA takes the archive's package where one exists and the tarball
   elsewhere, so its version differs by target and the manifest says why.
   Paracon joins `packet`; Artemis joins `listening` and `rf-security`
   (D-046's split, applied: a reference is for both); GPA joins no profile
   — `workstation`'s contents were fixed at acceptance (Q-011), and which
   EMCOMM profile a PGP front end belongs to is a placement question for
   the maintainer, so it installs by name.
2. **Artemis's vendor `.deb` is refused by name**, for the two reasons
   measured, and the refusal is written into the manifest so the next
   person to find the asset does not re-derive it.
3. **mbutil and pfte are RETIRE**, `not-carried.md` carries the reasons:
   a python2 script nothing calls, and a proprietary unsigned binary the
   security requirements refuse by name.
4. **Chattervox stays NEEDS-DECISION**, and the index says so. Both
   routes to it cross a rule: the bundle is a fetched Node runtime, which
   D-037 refuses; the source is the node backend fetching two git commits
   the registry does not carry, with a native serial layer the
   `--ignore-scripts` rule will not build. It is dormant six and a half
   years with only prereleases. The recommendation is not to carry it in
   1.0; the test results are recorded so the maintainer decides from
   evidence rather than from the dormancy alone.
5. **The five offline-data units keep their ADD and stay outstanding**
   with a recorded reason each, until sub-project 5 gives them a category.

**Consequences.** `paracon.yaml`, `artemis.yaml`, `gpa.yaml`; `packet`,
`listening` and `rf-security` gain a member each; the dispositions index
gains an `EmComm Tools OS delta (11)` block and the summary table a
column (the hygiene test holds both to each other); `not-carried.md` and
`parity-coverage.md` regenerated with the two retirements and five
reasons; the apt policy sweep re-run for the new names and the capability
matrix regenerated. D-042 sub-project 4's software half is closed by this
record; the `et-*` config ideas wait on station config and sub-project 3.

### Amendment, 2026-09-30 — Chattervox is RETIRE

Decision 4 left Chattervox to the maintainer with its test results. The
maintainer ruled on **Q-022** #5 (the gap analysis, A8): retire it as
abandoned. Re-measured before recording, so the verdict rests on more
than the dormancy: head 2019-03-17 by D-032 (the 2020-01-04 date carried
until now was GitHub's push field), nine releases all prereleases, the
newest open issue a 2024 build failure the author has not answered; and
the npm 0.7.0 package, installed with `--ignore-scripts` on Debian 13's
Node 20, runs `--version` and cannot load `serialport`, its link to a
KISS TNC. `dispositions.md` (EmComm Tools OS delta) carries the record
and what covers the use; `not-carried.md` the reason. Applied in pull request
#172 (`gap-07-rerulings`).

## D-049 — Offline data is a catalog unit: a `data` install method whose payload is the point, disclosed by size and licence before the confirmation, selected through station config

**Date:** 2026-09-12. **Status:** accepted (maintainer, Q-021, option A as
recommended, both rules). **Depends on:** D-042 (rule 5 named the layer),
D-048 (five readers decided ADD and unwritable), D-018 (every fetch
verified), D-035 (a missing station value defers one file, never the
transaction), D-021 (disclose, never adjudicate), Q-015 decision 8
(`country_files` deferred to exactly this question). **Amends:** nothing.

**What was measured** is in Q-021: ETC's three interactive, unverified
downloads into `/etc/skel`; its own tilesets at 0.69 GB (US) and 0.52 GB
(Canada) under ODbL; Geofabrik extracts from 0.05 GB (Vermont) to 1.33 GB
(California); the English Wikipedia ZIM unmeasured from here on the day.

**Decision.**

1. **A data artifact is a catalog unit**, on the package manifest, with
   `method: data`: one or more pinned, sha256'd artifacts installed under
   `<prefix>/share/hammunition/data/<name>/`, recorded in the transaction
   log like every other artefact so `uninstall` removes them, with an
   `update` block like every other unit. The reader (`kiwix`,
   `mbtileserver`, the logger that reads cty.dat) names the data unit in
   `depends`; the data unit names nothing.
2. **Size and licence are printed in the plan, before the confirmation.**
   Each artifact declares its `size` in bytes and the unit declares its
   `licence` and a `licence_url`; `--dry-run` shows both, and the real run
   shows the same text. A 1.33 GB download on a field connection is a
   decision, and a dataset under ODbL or CC BY-SA carries obligations the
   engine states and does not adjudicate (D-021's half).
3. **The operator's selection lives in station config.** Which state,
   which country, which language: a data unit whose artifact depends on a
   station value is deferred by name when the value is missing, and its
   reader installs regardless (D-035's shape). Nothing is guessed and no
   default region is invented.
4. **Built in this order**, each step its own measurement: the schema,
   fetcher and plan disclosure, proven on `country_files` — cty.dat, about
   200 KB, the smallest and oldest case; then ETC's tileset with
   `mbtileserver`; then Navit's extract with the station selection; then
   the ZIM with `kiwix`. `dict` is apt and needs none of this.

**What this is not.** Not a mirror: every artifact is fetched from its
publisher's own URL, never redistributed. Not `/etc/skel`: the data is
installed once, system-wide, for the operator who asked. Not a backend for
software — a `.pyz` is `binary`, a venv payload is `venv`; `data` is for
files the reader opens and the engine never executes.

**Consequences.** `DataInstall` in the schema (`artifacts`, each with
`url`, `sha256`, `size`, `install_as`; `licence`, `licence_url`); a data
backend that fetches, verifies and installs; the plan's disclosure; the
docs generator's rendering; `capability_matrix.py` knows the method.
`country_files.yaml` is the proof. Q-021 is closed by this record; Q-015
decision 8's deferral ends with it.

**Amended 2026-10-02 (maintainer's ruling on #197): an installed item the log
attributes is not re-asked of its publisher at plan time, and is re-checked
after seven days.** A plan used to ask the publisher about every item it would
fetch and nothing about an item already on disk, so a repeated dry run cost
what the first did only while the first had not installed; the ruling adds a
bound on trust in the other direction. The rule, for a terrain or 3DEP tile, a
US Topo or FSTopo sheet, a Kiwix book and a CoMaps map:

1. An item on disk that the transaction log attributes (the last
   `install-data` `action_end` for its destination, replayed through
   `TransactionLog.read()` so rotated archives count, D-077, and cancelled by a
   later `remove-data`) and whose attribution is **younger than seven days**
   (`attributed.RECHECK_AFTER_DAYS`) is planned as "installed, not re-checked
   (attributed DATE)" and makes no request.
2. One attributed **seven days or more ago**, or any attributed item under
   `install --recheck`, is asked again, as a missing one is. That re-check
   **never refuses and never defers**: the item is installed. A publisher that
   no longer serves it as pinned, or does not answer, becomes a `note:` line
   and the installed copy is kept; replacing it is the operator's decision.
3. An item on disk the log does **not** attribute (copied in by hand, or
   installed before the log recorded it) keeps what it had: its file counts and
   nothing is asked. An attributed item whose file is not the one the log
   recorded (a different size, or a catalog pin that is no longer the digest
   attributed) is not trusted and is asked again. `install-data` entries now
   carry `size` (and `digest` for books and maps); an older entry has neither
   and is trusted on its timestamp alone.
4. The plan says how many items were not re-checked and the oldest attribution
   among them; `install --json` carries `publisher_checks`, one line per item on
   disk with `checked`, the `reason` and the `attributed` date.
5. Nothing about the real run changes: whatever it fetches it verifies, so a
   skipped item can only have been skipped when there was nothing to fetch.

## D-050 — The menu is shaped like Parrot's: one top menu, ordered groups, one submenu per category, and an entry for every installed unit

**Date:** 2026-09-12. **Status:** accepted (maintainer, in the field-laptop
session: "that solution for menus looks correct"). **Depends on:** D-036
(curated submenus generated from `categories`), D-003 (categories are flat
tags and stay so), D-022 (the distribution's own entries are untouched),
D-031 (a count is not evidence; the file on disk is). **Amends:** the
D-036 addendum's measured menu-spec tree, which was one level.

**What was measured.** On the field target (Dell Latitude 5430 Rugged,
Parrot Security 7.3, KDE Plasma, 2026-09-12, `docs/reference/bench-verification-5430.md`):

- The D-036 tree rendered *Ham Radio* as **27 sibling submenus** in
  alphabetical order — more than Plasma's own top level carries — with
  visible overlap (Station / Timing / Tracking; CW / Training; Contest /
  Logging; Hardware / Programmer / Electronics). The maintainer's words:
  nobody can find anything in it.
- **Parrot's own tool menu**, the thing the rest of that desktop already
  uses: one top menu (*Parrot Security*), **14 numbered groups**
  (*01 Information Gathering* … *14 AI Tools*), numbered subcategories
  under each, the order fixed by a menu-spec `<Layout>` with separators,
  a hand-curated *Most Used Tools* group at the top, and entries free to
  appear in several places. **671 entries; 670 carry a `Comment`, 572
  launch in a terminal**, 6 carry `Keywords`. Categories in its desktop
  files are the group and subcategory tags (`01-info-gathering`,
  `01-04-network-scanners`).
- Of the **32 catalog units installed** on the laptop, **25 had no menu
  entry at all** — gpsd, rigctl, the RTL-SDR tools, tcpdump. An entry is
  what the launcher's search indexes; a unit without one is unfindable by
  name or by what it does, whatever the tree looks like.
- `parrot-menu` had removed the packaged `chirp.desktop` and
  `org.wireshark.Wireshark.desktop` from an apt `DPkg::Post-Invoke` hook
  and written `parrot-chirp.desktop` / `parrot-wireshark.desktop`; the tree
  placed the dead filenames and the summary counted them (#64).

**Rule.**

1. **Two levels, in a declared order.** `catalog/categories.yaml` gains a
   `groups:` list — `order`, `name`, `title`, `summary`, `categories` —
   and the tree is *Ham Radio* → group → category submenu. Eight groups:
   Station; Digital Modes; Packet & EMCOMM; SDR & Listening; RF Security;
   Hardware & Bench; Learning; Workstation (48 / 46 / 43 / 109 / 22 / 77 /
   13 / 23 catalog units respectively, measured 2026-09-12). Every category
   belongs to **exactly one** group (`tests/test_categories.py`); the
   order is a `<Layout>`, never the alphabet. A category no group claims
   still renders, beside the groups — the vocabulary test forbids the
   state, and the renderer is not a second place that silently drops it.
   **A group is where a submenu sits, not what a package is**: manifests
   keep tagging categories (D-003), and no other reader of the vocabulary
   consults groups.
2. **An entry for every installed unit.** `hammunition menus apply`
   generates, per user, `hammunition-cli-<unit>.desktop` for each
   installed catalog unit that ships no desktop entry and declares no
   `launchers`: the unit's name, the manifest summary as `Comment`, the
   categories as `Keywords`, `Terminal=true`, the `X-Hammunition-<category>`
   markers, and the executable. **The executable is the one named like the
   unit, else the package's only one; anything else is not guessed.**
   Several and none named like the unit (`rtl-sdr`: eight tools) is
   reported under the count with the fix named — a `launchers` block in
   the manifest. A unit whose executables are all under `/usr/sbin` is a
   service, not an application, and is skipped with that reason (`gpsd`'s
   generated entry ran the daemon in a terminal before this rule existed).
   Entries from an earlier run whose unit is gone are removed; a
   launcher's `hammunition-<name>.desktop` is never touched.
3. **Placed filenames are checked on disk** (#64): an entry that exists is
   placed as shipped, one that is gone is placed by
   `parrot-<package>.desktop` when that exists, otherwise it is reported,
   never counted. `dpkg -L` is what a package shipped, not what a
   distribution's hook left.
4. **KDE gets its cache rebuilt, and its file where KDE reads.** On a
   `plasma-` or `kf5-` prefix, `kbuildsycoca6` (or 5) runs afterwards as a
   disclosed, unprivileged command, so the tree shows now. And the merged
   file goes to `applications-merged/`, **not** `plasma-applications-merged/`:
   the spec says the prefixed directory and Xfce's garcon reads it, but
   KDE's kservice ignores the prefix — measured on the field laptop the
   same day, when the first apply of this rule wrote the prefixed file,
   `kbuildsycoca6` reported only `/etc/xdg/menus/applications-merged/*`
   as found, no *Ham Radio* menu appeared, and every generated entry sat in
   Kickoff's *Lost & Found* (an entry no menu allocates). Parrot's own menu
   ships in `applications-merged` for the same reason. A copy left in a
   directory the desktop does not read is removed on the next apply.
5. **Not taken from Parrot:** a *Most Used* group. Parrot curates it by
   hand; this project has no measurement to build one from, and a
   hand-kept list is the second taxonomy D-036 refuses.

**Measured after the rule, same machine, same day.** `menus apply`: 8
groups, 27 categories, 20 placed entries (CHIRP and Wireshark by Parrot's
replacements, zero dead filenames), **12 entries generated, 12 skipped
with the reason named** (11 with several executables and none named like
the unit; `gpsd` under the sbin rule), the earlier `gpsd` entry pruned,
`kbuildsycoca6` run. `hammunition-hill` gained a `service_endpoints` +
`launchers` pair (the browser at the loopback dashboard), so the
family's own dashboard is in *Station*, *HF Propagation* and *Satellite*
rather than nowhere.

**Second round, same day (maintainer, after seeing the tree in the
launcher).** Three amendments to the rule above:

6. **The menu is called *Hammunition*, not *Ham Radio*.** It is the
   project's tree, the way Parrot's is *Parrot Security*.
7. **A group can be hidden: `menu: false`.** *Workstation* is: git, tmux,
   screen, VS Code and VSCodium are catalog units because a station needs
   them, not because they are radio software, and the maintainer did not
   want them under Hammunition. A hidden group gets no submenu, no folder,
   no generated entry, and the packaged entries of a unit tagged only there
   are left where the desktop already puts them (VS Code stays under
   *Development*). A unit tagged workstation *and* a radio category
   (wireshark, esptool, tcpdump) still shows under the radio one, and the
   hidden tag is not a keyword on its entry. Measured on the laptop: 7
   groups shown, the git, screen and tmux entries pruned, 9 generated
   entries remain.
8. **Desktop parity is per mechanism, measured where it can be.** What
   Parrot already carries was measured too: under *Pentesting → Wireless
   Attacks* it lists 71 RF entries in five submenus and 15 of those
   packages are catalog units (gqrx, GNU Radio, the RTL-SDR and HackRF
   tools, Ubertooth, inspectrum, the NFC tools, aircrack, Wireshark, CHIRP,
   GPA, gr-air-modes); its KDE root has no `HamRadio` category at all, so
   the ham side had nowhere to go. Both copies stay (D-022), the way
   Parrot lists a tool in several places.

   | Desktop | Mechanism | State |
   |---|---|---|
   | KDE Plasma (Parrot) | menu-spec merge into `applications-merged/` (KDE ignores the prefix), `kbuildsycoca6` after | **Measured on the field laptop** 2026-09-12: tree present, groups in order, Lost & Found emptied |
   | Xfce (Kali, Parrot's alternative) | menu-spec merge into `<prefix>applications-merged/` | Measured on the Kali VM for the flat tree (2026-09-02); the grouped tree and `<Layout>` are the same mechanism and **await a re-run there** |
   | GNOME (Debian 13, Ubuntu) | one app-folder per visible group, *Hammunition · <title>*, populated by the group's `X-Hammunition-*` markers plus placed entries by name; GNOME cannot nest | Code and tests written this round; **awaits the Debian 13 VM** — nothing asserted until it has run |
   | COSMIC (Pop!_OS) | unknown; its app-library groups are not menu-spec | **Unmeasured.** The Pop VM exists; nothing is claimed until it is read |

   The three VM checks run from the hypervisor host, not the field laptop.

**Consequences.** The eleven skipped units on the laptop — `rtl-sdr`,
`libhamlib-utils`, `hackrf`, `ubertooth`, `libnfc-bin`, `libfreefare-bin`,
`hcxtools`, `gpsd-tools`, `pciutils`, `usbutils` and `gpsd`'s tools — are
the next `launchers` work, one manifest each, choosing which tool a menu
entry should open. Whether every terminal entry *makes sense* to open
(`git`, `tmux`) is the same question Parrot answered yes to for 572
entries; it is revisited only with a measurement of what operators
actually open. COSMIC stays unmeasured. The GNOME app-folder is unchanged:
it cannot nest, and one folder populated by `HamRadio` is what it can do.

## D-051 — A build already installed at its pin is already installed: the effect on disk plus the log's attribution, never one without the other

**Date:** 2026-09-12. **Status:** accepted (maintainer: "focus on delivering
more features"; this was the gap that had just cost him two rebuild rounds).
**Depends on:** D-031 (the effect, not the exit status), D-004 (the
transaction log is the record), the #67 rule for vendor .debs, which this
generalises. **Amends:** the Parrot VM page's finding 4 (2026-08-29), which
queued exactly this as engine work.

**What was measured.** The field laptop's first full-catalog install
(2026-09-12) ran 21 source and git builds, then failed at paracon (#72). The
resume rebuilt all 21, then failed at a virtualenv step (#74). The second
resume rebuilt them a third time. apt units reported *already installed*
throughout; every build unit reported *will build*, because
`_plan_state`'s rule was "already installed is apt's answer and only apt's"
and the executor had no way to know a build had happened.

**Rule.** `execute.already_built()` names the units whose build steps are
skipped, and it requires both halves:

1. **The effect is present.** Every declared `binaries` entry is executable
   at `<prefix>/bin/<install_as>`; where the block installs a tree, its
   `tree_marker` exists under the tree destination. A unit declaring neither
   cannot be checked and is never decided here -- it rebuilds.
2. **The log attributes it, at this pin.** A `verify-pin` or `extract`
   action whose detail names *exactly this build directory* -- the
   backends' `layout()` puts the ref or the digest prefix in that
   directory's name, so a moved ref or a re-pinned artifact is a different
   path and does not match -- followed, in the same transaction, by a
   `transaction_end` with `verified: true` that confirmed one of this unit's
   checks. A transaction that failed after the build steps attributes
   nothing: the paracon run's 21 builds are on disk and were still rebuilt
   once more, correctly, because nothing ever verified them.

Both halves come from what already existed: the effect checks are
`verify_effects`'s own probes, and the log entries are the ones every build
already writes. No log format change; the laptop's log from before this
rule attributes builds the moment a verified transaction has covered them.

A skipped unit keeps its launcher and config steps -- those are cheap,
idempotent, and the reason a profile is re-run in the first place. The plan
line reads *already installed*, the executor plans no fetch, unpack, build
or install for it, and `verify_effects` still checks the binary at the end,
so a unit deleted by hand between plan and run is caught there.

**Not decided here.** apt has `already_installed`; a .deb has #67; venv and
node units keep pip's and npm's own cheap idempotency. A unit whose pin
moved rebuilds, and that is the point: *at its pin*, not *at some pin*.

**Measured after the rule** (to be recorded on
`docs/reference/bench-verification-5430.md` once the laptop's second
resume ends with a verified transaction): a dry run of the profiles that
carry the 21 builds should plan launcher steps only.

---

## D-052 — A unit whose Recommends conflict with the target's desktop may opt itself out; the global default does not move, and the opt-out is a second apt command, simulated like the first

**Date:** 2026-09-12. **Status:** accepted. **Depends on:** D-022 (never
remove what the operator did not ask to remove), D-016 (resolution completes
before anything runs), D-038 (a measured `--target-release` governs the apt
step). **Closes:** issue #61, option A.

**What was measured.** On the field laptop — Parrot Security 7.3, KDE,
`pipewire-alsa 1.4.9-1~bpo13+2` installed — `hammunition install morse
--dry-run --no-refresh` refused the whole profile, correctly, because:

```
$ apt-get install -s morse | grep -E '^(Inst|Remv)'
Remv pipewire-alsa [1.4.9-1~bpo13+2]
Inst morse (2.6-2 Parrot 7 Echo Parakeet:parrot [amd64])
Inst libasound2-plugins (...)
Inst pulseaudio (17.0+dfsg1-2+b1 ...)

$ apt-cache depends morse | grep Recommends
  Recommends: pulseaudio
$ apt-cache show pipewire-alsa | grep Conflicts
Conflicts: pulseaudio
```

`morse` *Depends* `libpulse0`, which `pipewire-pulse` satisfies; PulseAudio
itself is only a Recommends. So the software does not need PulseAudio —
Debian's metadata prefers it, and apt's default of installing Recommends turns
that preference into the removal of the desktop's audio routing.

**Rule.** An apt install block may carry `install_recommends: false`. It is a
per-unit opt-out and nothing else:

1. **The global default is untouched.** Recommends are still installed for
   every other unit, on every target, exactly as the distribution does. The
   apt backend's module docstring remains the authority on why.
2. **The opted-out packages are a second set**, and two sets are two
   `apt-get install` commands: the default one first, then one carrying
   `--no-install-recommends` for the units that asked. A package named by an
   opted-out unit *and* by a unit that did not opt out stays in the default
   set — apt's defaults are what a manifest deviates from, never the reverse.
3. **Both sets are simulated the way they will be installed**, the second
   with `--no-install-recommends` on the `--simulate`. The two `AptSimulation`
   results are merged — refused if either was, `Remv` lines from both — so the
   D-022 removal check reads exactly what apt will do and not a simulation of
   a different transaction. A `--target-release` measured for either set under
   D-038 governs the whole apt step, so the other set is simulated again with
   it; two different releases is a refusal, because one apt step cannot run
   with both and nothing is guessed.
4. **`--no-remove` stays on both commands.** The flag buys a unit its own apt
   invocation, never an exemption from D-022: if apt still plans a removal
   with Recommends suppressed, the plan refuses by name as before.
5. **It is disclosed.** The plan prints the second set under *apt packages
   installed without Recommends*, naming the units that asked and why, and
   both commands appear under *Commands* before the confirmation.

**Carried in the catalog by:** `morse-classic`, the case that produced the
rule. Its `conflicts_with_repo_package: [pipewire-alsa]` declaration stays:
the flag is what avoids the removal, the declaration is what names this unit
as the reason if a future apt plans one anyway. Whether it returns to the
`morse` profile is the maintainer's call and is not decided here.

**Not decided here.** Nothing global, and no second use: a manifest that wants
this flag needs the same shape of measurement — the Recommends named, the
conflict named, the `apt-get install -s` output that shows the removal.

---

## D-053 — `update` is a report: installed versus the catalog, from facts the engine already has, with nothing run and nothing asked over the network

**Decided:** `hammunition update` compares what is installed with what the
catalog describes and prints the result. It runs no command that changes the
machine, fetches nothing, and does not consult upstream. The first release of
the command; the second half, comparing the catalog's pin to upstream through
the probes D-010 declared, is deferred and named.

**Evidence:** Every manifest has carried an `update` block since D-010 — a
probe and a strategy — and until 2026-09-13 nothing in the engine read it.
D-010's own justification was that AHRL had no update story ("install once,
rot forever") and that answering *installed versus upstream* is what keeps a
project maintainable past 1.0. Measured on the field laptop the night the
command was written, against 171 units the log had ever named: 145 up to
date, 17 behind the catalog's pin, 2 unknown, 4 re-checked on install, 3
manual, 0 with a different apt candidate — and the 17 + 2 + 2 manual builds
are exactly the 21 units the same evening's `install --dry-run` planned to
build, which is the consistency the command has to have to be trusted.

**Rule.**

1. **Two comparisons, both offline.** An apt unit is compared against apt's
   candidate in the local lists, through the same `apt-cache policy` call
   resolution makes; a built unit is compared against the catalog's pin
   through D-051's attribution (effect on disk, and a verified transaction
   that built exactly this pin). A vendor `.deb` uses #67's attribution. A
   venv or node unit is *re-checked on install*, because pip and npm resolve
   every time and there is nothing to compare without running them.
2. **The lists' age is disclosed, not refreshed.** The report prints when
   the local package lists were last fetched. D-044 refreshes them at the
   start of an install because an install is about to act on them; a report
   that refreshed would be changing the machine to describe it.
3. **A different candidate is not called an upgrade.** apt's candidate can
   be lower than the installed version (a backport, a pin, a removed
   repository). The row prints both versions, and the command the report
   offers is `apt-get install --only-upgrade --no-remove`, which never
   removes and never downgrades. apt decides; the report describes.
4. **Unknown is a state.** A build that declares no `binaries` and no tree
   marker is reported as unknown, the same units D-051 cannot decide, rather
   than guessed at from the log. The fix is catalog data, as it was for
   D-051.
5. **The default set is everything the log ever named**, including units
   from transactions that failed or were interrupted. The field laptop's
   first full install failed after its apt step and still installed a
   thousand packages; a default that read only clean endings hid ninety
   units the machine has. The comparison looks at apt and the disk, so a
   unit that never landed reads *not installed*.
6. **Nothing is executed, and the report says so** on its last line. The
   commands it prints are the operator's to run.

**Second half, decided the same night: `update --upstream`.** Opt-in,
because it is the one thing the engine does that talks to someone else's
server, and additive: the offline report prints first, unchanged.

7. **One place per probe, nothing else.** `github_release` reads the latest
   release's tag from GitHub's API; `github_tags` lists tags with
   `git ls-remote --tags --refs` on any host and takes the highest by its
   numeric parts; `pypi` reads the project's JSON (the unit's name, or the
   probe's `package`); `label_file` fetches one line and compares it
   verbatim, as the schema always said. `binary_version` reads the installed
   program and is not an upstream question. Nothing is downloaded beyond the
   answer and nothing is written.
8. **A token goes to GitHub only.** `GITHUB_TOKEN`, if set, is sent to
   `api.github.com` for the rate limit and to no other host; tags need none.
9. **Newer is said only when the numbers say it.** The comparison strips a
   `v` or `release-` prefix, calls equal strings and substrings *current*,
   orders by numeric parts where both sides have them, and calls everything
   else *differs* with both versions shown. An older upstream is *differs*,
   never *newer*.
10. **Unanswered is a row.** A timeout, a 404, a repository that cannot be
    derived: the row says which, the rest of the report stands.

Measured on the field laptop, 2026-09-13, 25 probes answered in 7.5 s, none
unanswered, and three pins found behind upstream on the first run
(hamclock-next 1.5 → 1.6, linbpq 25.39 → 25.40, openhamclock 26.7.0 →
26.7.3), which is the maintainer's re-pin queue and the tool's reason to exist.

---

## D-054 — The menu is built for the person who does not know the vocabulary: activity groups in plain words, a toolkit is one line, a generated entry says what it does

**Decided:** Three changes to the D-050 tree, all measured on the field
laptop on 2026-09-13 after the maintainer's own read of it: "a great start,
and we can organize them better so the average person can locate what they
want".

**Evidence.** The D-050 tree placed every installed entry under every
category its manifest carries, named the top-level groups after the catalog's
own vocabulary, and named each generated entry after its unit. On the field
laptop that produced: GNU Radio's **21** desktop entries (`gr_plot_*`,
`uhd_*`, `grcc`, `gr-modtool`, `tags_demo`) inline in *both* SDR and Digital
Modes, so the two submenus a newcomer opens first were two-thirds developer
utilities; **47** generated entries reading `tlf`, `wwl`, `atlc`, `splat`,
`tio`, `m2kcli`; and groups called *Station* and *Hardware & Bench* that
say nothing to someone who has not learned the words yet.

**Rule.**

1. **Groups are activities, titled in plain words, in the order a newcomer
   reads them:** *Operate the Station*, *Digital Modes & Morse*, *Packet,
   Mesh & Emergency Comms*, *SDR & Listening*, *Satellites & Propagation*,
   *Antennas, Bench & Programming*, *RF Security & Research*, *Learn &
   Practise*. The category submenus inside them keep the ham vocabulary and
   carry a gloss where the tag is jargon: *CW (Morse)*, *Tracking (APRS,
   ADS-B, AIS)*, *Rig Control (CAT)*, *Radio Memories (Codeplugs)*. Every
   category sits in exactly one group; the vocabulary test enforces it.
2. **A unit that ships a toolkit gathers it into one submenu.** A manifest's
   `menu_submenu: <title>` puts every desktop entry the unit ships into a
   nested submenu of that title under the unit's *first* category and
   nowhere else. The category then lists the unit as one line. It is catalog
   data, not a threshold: the one measured case is GNU Radio (21 entries),
   the next largest unit ships 2, and a test pins that GNU Radio declares
   it. GNOME cannot nest folders, so there the submenu flattens into its
   group's folder, and the docstring says so.
3. **A generated entry may carry a title.** A manifest's `menu_title` is the
   `Name=` of the entry the engine generates for a unit that ships none, in
   one shape: *what it does, then the command in parentheses* — `Contest
   logger (tlf)`. The unit's name stays in `Keywords=`, so the launcher's
   search still finds `tlf`. The same shape launchers' titles took the night
   before (#89).
4. **Nothing of the distribution's is renamed or moved.** The desktop's own
   copies of every entry stay where the desktop puts them (D-036); this tree
   is the additive one, and it is the only place the shape above exists.

**Measured after the change**, same laptop, `hammunition menus apply`: 8
groups, 26 categories, 99 placed entries placed **100** times (they were
placed 142 times before; GNU Radio's 21 no longer land twice), 47 generated,
GNU Radio one nested submenu under *SDR Receivers & Toolkits*, the largest
submenu now 20 entries where it was 33.

**Not decided here.** Whether an entry should appear under every category
its manifest carries or only the first: Parrot's own menu duplicates freely
and the duplication is what makes a tool findable from two directions, so
it stays until a measurement says otherwise. Titles for the distribution's
own cryptic entries (`twclock`, `comptext`, `jtdx`) are Debian's to give.

---

## D-055 — The vocabulary is cut to the thing a person looks for: 55 tags under the eight activity groups, one place for each of APRS, Winlink, ships, aircraft, SSTV and amateur TV

**Decided:** The category vocabulary is recut from 26 coarse tags to 55 fine
ones. A tag is the smallest thing a person looks for; a group (D-054) is the
activity it belongs to. Every unit is retagged under the fine set, by a
mapping written from each manifest's own summary, and the coarse names that
were really groups (`sdr`, `listening`, `tracking`, `packet`, `station`,
`digital-modes`, `hf-propagation`, `rf-security` as a tag) are gone.

**Evidence.** The maintainer, 2026-09-13, on the D-054 tree: "there is no
Ham Radio TV; we should have things broken down as much as possible so
people can find what they are looking for, especially as we build towards a
wiki". Measured, he was right about the shape: `listening` held aircraft
datalink, ship AIS, DAB radio, pagers, radiosondes and weather fax in one
submenu of 19; `tracking` held APRS, ADS-B and AIS; `packet` held terminals,
modems, nodes and Winlink; and SSTV sat inside a *Digital Modes* submenu of
13 with nothing to say it was television. A wiki built on those tags would
have the same sections and the same problem.

**Rule.**

1. **A tag names one thing a person would type into a search box**: *APRS*,
   *Winlink Email*, *Ships (AIS)*, *Aircraft (ADS-B, ACARS, Airband)*,
   *SSTV, Fax & Amateur TV*, *Soundcard Modems & TNCs*, *Nodes, BBS &
   Gateways*, *Cellular & IMSI-Catcher Detection*. Where the name is an
   acronym the title glosses it. The full list is `catalog/categories.yaml`;
   every tag is used by at least one unit and every unit carries at least one
   (`tests/test_categories.py`, unchanged).
2. **The groups stay as D-054 cut them**, one per activity, and every tag
   belongs to exactly one group. The tags under a group are the wiki's
   sections under that chapter; the menu and the wiki read the same file.
3. **A unit carries the tags that are true of it, usually one or two.**
   Retagging halved the placements: on the field laptop the same 99 desktop
   entries land 70 times where they landed 100 under the coarse tags, because
   `direwolf` is a modem and an APRS tool and no longer also "tracking",
   "packet" and "emcomm". The largest submenu is 11 entries (CW), where it
   was 20 after D-054 and 33 before it.
4. **The presentation map follows the tags**, not the reverse: the
   freedesktop categories a generated entry carries are looked up per fine
   tag, so a *Serial Terminals* entry says `TerminalEmulator` and an
   *Aircraft* entry says `Geography`, and the desktop's own search improves
   with the menu's.
5. **An empty submenu is not an error.** Seven of the 55 are empty on the
   field laptop tonight (*Nodes, BBS & Gateways*, *LoRa Mesh*, *Mail
   Clients*, *DMR Codeplugs*, *Device Support & Drivers*, *Cellular &
   IMSI-Catcher Detection*, *References & Guides*): the units exist and are
   not installed, or ship no entry yet. The menu spec hides an empty submenu
   on its own, and the vocabulary is not trimmed to one machine's install.

**Found while measuring, not fixed here.** Ten desktop entries under
`/usr/local/share/applications` — the fldigi family, `wsjtx`, `gpa`, and
WSJT-X's message aggregator, every one a source build's own install rule —
are never placed: D-050's placer reads `dpkg -L`, which knows nothing about
a source build. Nine of the ten match a unit's name, a declared binary or a
`provides` entry, so the fix is a second placer over the prefix keyed on
those names. That is the next change, and *FT8, JS8 & Weak Signal* showing
one entry on a laptop with WSJT-X, JS8Call and JTDX built is the measurement
that found it.

---

## D-050 amendment (2026-09-13) — the menu covers every installed unit on the day it is applied, not the day each unit was installed

**Found:** the maintainer, reading the D-055 tree on the field laptop:
"Hammunition Hill is missing from the menu." Measured: its launcher entry,
written at install time, still carried `X-Hammunition-station`, a marker no
submenu has included since the vocabulary was recut; thirteen other launcher
entries were in the same state. Auditing every installed, visible unit against
the menu found **46 of 168 with no presence at all**, in three classes.

**Amended.** `hammunition menus apply`, and the install tail that calls the
same code, now do three things D-050 left to install time:

1. **Every launcher entry is re-rendered from its manifest as it is now** —
   categories, title, comment — keeping the wrapper path from its own
   `Exec=` line, and only when the text differs. The icon is left to the
   decoration step. Fourteen entries changed on the field laptop; none on a
   second pass.
2. **A built unit generates an entry from its declared binaries.** The
   generator read only dpkg's executable list, so a source, git or prebuilt
   unit had nothing unless it shipped a desktop file or a launcher. The
   binaries a manifest declares, present under the prefix, are read the same
   way: the one named like the unit, else the sole one, else the unit is
   reported and a `launchers` block is the fix. Twenty built units gained an
   entry (46 generated entries became 66).
3. **A launcher declared after a unit was installed is written at apply
   time**, wrapper and entry, when one of the unit's apt packages is
   installed or a declared binary is on disk, and not for venv or node units,
   whose wrappers need what only `install` knows. Four units on the field
   laptop (rtl-sdr, libhamlib-utils, libnfc-bin, gpsd-tools) had gained a
   launcher in the catalog after they were installed and never had it.

**Measured after:** 168 installed visible units, **19 without a menu
presence**, every one an honest omission the summary names: seven services
that ship only `/usr/sbin` programs, five toolkits with no executable named
like the unit (`ax25mail-utils`, `hcxtools`, `libfreefare-bin`, `pciutils`,
`usbutils`), seven libraries and driver modules with nothing to launch.

---

## D-056 — Device power control: one helper behind one polkit action, parked kept as intent in one udev rule per device (amended 2026-09-28), and every unbuilt capability ships schema-valid and refused

**Decided:** A catalogued device can be **parked** (detached so its port
suspends) and **woken** (brought back) through a `power_control` block on its
manifest naming a fixed method. Three callers — the CLI, generated menu
entries, and the Plasma applet in a separate repository — all reach the
kernel through one small root-owned helper, authorised by one polkit action.
Whether a device *is* parked right now is always read from sysfs, never from
a cache. What *is* written to disk, by default and only as of the amendment
below, is intent — one udev rule per kept device, naming its port and model,
so udev reapplies the park the next time that device is added; `--until-reboot`
opts out and nothing is written for that park.

### Why a helper behind polkit, not `sudo hammunition`

`pkexec` authorises an **absolute executable path**, not an argument list —
the action names the one program root may run and polkit checks nothing about
what that program is then asked to do. Pointing it at the whole engine
(`sudo hammunition`, or a polkit action wrapping it) would authorise every
verb the CLI has ever grown or will grow — `install`, `uninstall`, arbitrary
catalog-driven apt and source-build execution — through an `auth_self_keep`
grant meant for one two-line sysfs write. (`auth_self_keep` keeps that grant
for a few minutes after the one authentication, per polkit's own manual
page, not for the rest of the session — a claim this feature's own docs got
wrong on the first pass and had to correct.) The engine is not
a thing to authorise wholesale. `hammunition-devctl` is a separate,
deliberately small program: three verbs, no argv that carries a path or a
plan, no station config, nothing an unprivileged caller supplies except a
device *name* it re-resolves for itself against a bus it re-reads fresh. What
runs as root is reviewable in one file.

### Why not a D-Bus service yet

Three callers and one action is small enough that the helper's shape —
re-derive everything from a name, on every call, with no held state — is
simpler than a long-running service with a socket to secure and a state
machine to keep honest. A D-Bus service is the shape to grow into if this
ever needs to carry more than a handful of device controls with genuinely
live state (a service watching for hot-plug and pushing signals, say); for
one action and three small writes it would be infrastructure the feature does
not need yet.

### Why parked state is not persisted

sysfs is already the single source of truth: `authorized` reads back `0` or
`1` right now, on the device, and a reboot resets every device to woken
regardless of anything recorded elsewhere. A state file would only ever be
able to disagree with sysfs — after an unplug and replug, after a firmware
crash, after `apply` has never been run at all — and reconciling "the file
says parked but the device is not" is a whole failure mode this design has no
reason to build. `hammunition hardware state` answers by reading the bus, not
a cache.

### Amendment (2026-09-28): parked is kept, as intent, in one udev rule per device

The maintainer, once park and wake had proven out on the field laptop's GPS
receiver, asked for the switch to *stay* where it is set: "we may not always
want GPS but maybe cell, or maybe we want cell off and GPS to save battery or
whatever for an extended period." The reasoning just above still holds for
what it actually argued: sysfs remains the single place that answers "is this
device parked right now", and there is still no cache of that answer to go
stale. What it did not anticipate is that an operator might want a second
thing recorded alongside it — not "is it parked" but "should it come back
parked" — and that a reboot resetting every device is a loss for a device
someone deliberately left off, not a simplification. This amendment adds
exactly that second thing, and only that.

**The mechanism.** `park` now writes two lines, by default, to
`/etc/udev/rules.d/66-hammunition-kept.rules` — after Hammunition's own
`65-hammunition.rules`, so the permission rules have already run: a
`# kept: NAME` comment, then the rule. The rule names the device's port and its vendor/product pair together
(`KERNEL=="3-5.1", ATTR{idVendor}=="1546", ATTR{idProduct}=="01a9"`), each
value checked against the shape a USB port address and a USB ID actually
have before it is written; nothing else reaches the file. The file is
rewritten whole from scratch on every change, by the helper alone, never
appended to by hand — a line it did not write refuses the entire rewrite,
naming the offending line and the file, rather than discarding whatever put
it there. The write itself is atomic (a uniquely named temp file in the same
directory, `fsync`, rename, the temp file removed on any failure) and mode
`0644`, the whole read-modify-write held under an `flock` on the rules
directory so two helper runs cannot lose each other's entry, followed by `udevadm control --reload` —
never `trigger`, because the park that led to the write already happened
through the sysfs write a moment earlier, and re-triggering would re-run
every udev rule against every device on the bus for a change that only
concerns one of them. `hammunition hardware park --until-reboot NAME` is the
way back to this decision's original behaviour: the sysfs write happens,
nothing is added to the file, and any entry an earlier `park` wrote for the
device is removed, so the device parks now and a reboot wakes it, exactly as
first specified above.

**The disagreement this section worried about is now shown, not avoided.**
Reconciling "the file says parked but the device is not" was the whole
failure mode the original decision declined to build a state file for. That
problem has not gone away — a kept device authorised by hand while its rule
still exists is now a real state — but the answer is not a reconciliation
step; it is `hammunition hardware state` reporting `kept` and the live sysfs
`parked` reading as two separate fields, so a disagreement between intent and
reality is visible in one line instead of hidden behind a single boolean that
would have to pick a side. The next time the device is added, udev applies
the rule again regardless of what a hand-authorisation left behind.

**What the mechanism does not yet answer, stated as unmeasured, not assumed.**
The rule is a udev `ACTION=="add"` rule, which fires once the kernel has
already enumerated the device, not before — so the belief that it beats every
consumer to the device, before a tty node like `/dev/ttyACM0` can appear at
all, is not yet checked against real hardware. Whether the field laptop's GPS
receiver ever shows a fleeting `/dev/ttyACM0` across a reboot before the rule
reasserts `authorized=0`, and whether the port address a kept entry names
(`3-5.1`) is actually stable across reboots on that machine, are exactly what
this design's own bench test (Task 7, recorded in
`docs/reference/bench-verification-5430.md`) measures — not a claim this
amendment or `docs/hardware/power-control.md` makes ahead of it. See
`docs/superpowers/specs/2026-09-27-device-kept-off-design.md` §3, which is
corrected alongside this amendment for the same reason.

### Why `pci_runtime` ships refused

`PowerMethod` is `Literal["usb_deauthorize", "pci_runtime"]` so a future
`wwan-modem` class can carry `pci_runtime` in its manifest today and be
schema-valid, but no manifest in the catalog declares it, and the engine
raises `PowerError` the moment a plan is asked for one. The project's
standing rule (this file, throughout) is that nothing ships that has not been
run — an MHI/PCIe power-control path has no card here to prove it against,
so it is carried as a named, documented gap rather than shipped on the
strength of reading a kernel doc.

### Why the quiet verbs ship refused too

`QuietVerb` carries `networkmanager_autoconnect` for the same reason:
schema-valid, refused at plan time, `PowerError` naming why. This one changed
shape during implementation. The verb was first written to set
`connection.autoconnect no` on every NetworkManager profile before a park and
restore it on wake — and a review round caught that "every profile" includes
ones the operator had deliberately set to `no` for reasons of their own, so a
park/wake cycle was not reversible; it silently turned autoconnect back on
for something the operator had turned off. The spec's actual intent — hush
only the profiles *bound to the device's own interface* — cannot be
implemented honestly yet: `Parkable` carries no interface at all, because the
one device parkable today is a USB GPS receiver, which has no interface to
bind to. The only device that would need this verb is a WWAN modem, and that
device's own method (`pci_runtime`) is already refused above for want of a
card. So the enum value stays, the implementation waits for the card that
would prove both halves together, and nothing is shipped that quietly
mishandles a setting the operator made on purpose.

### Why `guard()` is lexical, not `resolve()`

`hammunition.hardware.power.guard()` checks that every path it is asked to
write sits inside `/sys/bus/usb/devices` or `/sys/bus/pci/devices`, at
exactly `<address>/authorized` or `<address>/power/control` — but it does the
containment check with `os.path.normpath`, textual `..`-collapsing, and
never with `Path.resolve()`. A real USB device node **is itself a symlink**
— `/sys/bus/usb/devices/1-4` points into `/sys/devices/pci0000:00/…` — so
the obvious-looking check, `path.resolve().is_relative_to(root)`, refuses
every device on the machine, because the resolved path never sits under
`/sys/bus/...` at all. The equally obvious alternative, comparing the path as
given with no normalisation whatsoever, accepts
`/sys/bus/usb/devices/../../../etc/shadow`. Neither is safe; normalising the
literal path without following it is the one check that is.

**Sitting under a root is not the whole guard.** Every USB device node also
carries kernel-made symlinks of its own — `driver`, `subsystem`, `remove` —
and `<address>/driver/unbind` sits under the same allowed root while having
nothing to do with power control. So `guard()` also pins the **leaf**: the
path under the root must be exactly one address component followed by
`authorized` or `power/control`, structurally, not merely ending in one of
those names — `<address>/driver/authorized` ends in a permitted leaf while
riding `driver`'s symlink clean out of the node. `guard()` is called again
inside `execute()`, the function that actually runs as root, not only by
whoever assembled the plan — a plan built safely today is not a guarantee
about how one is built tomorrow.

### Why the wrapper's writability gate has two classes, not one

`hardware apply` checks, before installing the helper, whether the
interpreter it will bake in and the `hammunition` package directory it
imports are safe for root to run. An early version treated "owned by a
non-root account" and "writable by *any* local account" as the same finding
and refused on either — which broke the project's own documented install,
because a venv under `$HOME` (`docs/getting-started/install.md`'s own
instructions) is *always* owned by one specific non-root account, never by
root. The two facts are not the same risk. `WritabilityRisk.GROUP_OR_OTHER_WRITABLE`
— any local account, not only the owner, can replace what root is about to
run — is the actual escalation and is refused outright, at both `apply` time
and again at the moment `hammunition-devctl` itself starts running as root.
`WritabilityRisk.OWNED_BY_NON_ROOT` — the tree belongs to one account, the
ordinary shape of an operator's own venv — is never refused; it is disclosed
by path and requires a **typed confirmation** at `apply` (originally the
exact path retyped; `yes` since the amendment below) and prints a warning at
runtime.

**The typed confirmation cannot be satisfied by `--yes`.** This is D-021's
rule pointed at a different subsystem: `--yes` means "skip routine
confirmations", and a gate that a convenience flag walks through is not a
gate. Authorising root to run code from a tree one non-root account controls
is a decision to record deliberately, the same way an unlicensed-transmission
consent gate is.

**Amended 2026-09-27: the answer is `yes`, not the path.** The maintainer,
on running it: retyping a path printed directly above the prompt proves
nothing a `yes` does not, and it was the one step of the install that felt
like a hoop. The prompt is now `Proceed? [yes/no]:`, asked at the keyboard,
still unanswerable by `--yes`, and it replaces the generic "Proceed?" rather
than adding to it, so the operator is asked once. D-040's typed fingerprint
is unchanged: there the operator checks the value against the vendor's
published one, and typing it is that check.

### Amendment (2026-09-27): group write by the owner's private group is not "any local account"

Measured on the field laptop: Parrot 7's stock session umask is `0002` with
`USERGROUPS_ENAB yes` and no override anywhere in the profile, so every
checkout and venv the operator creates is group-writable — by the
operator's own user-private group, which lists no member and is nobody
else's primary group. The gate read that as `GROUP_OR_OTHER_WRITABLE` and
refused, with a sentence ("writable by any local account") that was false of
the machine it ran on, so the helper could not be installed on the field
target at all and the tray applet had no switch to show.

Group write now counts as the severe class only when somebody other than
the owner can hold the group. `group_is_private_to()` in
`src/hammunition/hardware/polkit.py` calls a group private to the
component's owner when it lists no member and the owner is the only account
holding it as a primary group; such a component falls through to the
ownership pass and is `OWNED_BY_NON_ROOT`, the typed confirmation, exactly as
an owner-only tree is. Other-write still refuses whatever the group. A group
with a member, a group another account holds as primary, a group the owner
does not hold as primary, and an unknown gid all still refuse — the check
fails closed. The same rule applies at runtime, when `hammunition-devctl`
starts as root.

What it cannot see: `getpwall()` reads the local account database and
whatever NSS enumerates. A directory service configured not to enumerate
could hold an account with that primary gid that this check never sees.
Adding anyone to the group needs root in any case, and root is already
past every gate this protects.

### Why the wrapper execs the interpreter with `-I`

The wrapper's exec line is `exec <interpreter> -I -m hammunition.cli.devctl
"$@"`, and `-I` is load-bearing, not a stray flag a future edit should tidy
away. `python -m <pkg>` inserts `os.getcwd()` at `sys.path[0]` before
resolving the module. `pkexec` ordinarily hides that by `chdir()`-ing to the
target user's home directory before it execs the authorised program — but
`pkexec --keep-cwd` does not, and the polkit action above authorises an
*executable path*, not an argument list, so nothing about the action stops a
caller from adding that flag. A local user with an active session, calling
`pkexec --keep-cwd /usr/local/libexec/hammunition-devctl state` from a
directory holding their own `src/hammunition/cli/devctl.py`, authenticates with
**their own** password under `auth_self_keep` and gets their module imported
and run as root in place of the real one — the two-class writability gate
above never runs, because it lives inside the module that just got replaced.
This was reproduced end to end against the generated wrapper before the fix
was written, and it is exactly the property this decision's own opening
promises and the two-class gate exists to hold: "nothing an unprivileged
caller supplies except a device *name*." The working directory is a second
value crossing that boundary, missed because `pkexec`'s ordinary `chdir()`
made it look closed. `-I` (Python's isolated mode) drops `sys.path[0]`
entirely, along with `PYTHONPATH`, `PYTHONHOME`, and user site-packages,
while still resolving `hammunition` from the interpreter's own venv — the
hijack import fails instead of succeeding, confirmed against the real
generated wrapper both ways. `cd /` immediately before the `exec` is added as
defence in depth on top of it.

### Why removal is `hardware unapply` and not `uninstall`

`hammunition uninstall NAME...` resolves every name it is given against the
package catalog and the profile catalog; there is no unit named `hardware`
to hand it, and teaching `uninstall` to also understand a third, hardware-only
namespace would be a special case bent into a command whose whole contract is
"resolve a catalog name." `hardware unapply` is the symmetric counterpart to
`hardware apply` instead — same subcommand family, same disclosure and
verification shape, and it removes precisely what the transaction log records
`apply` having installed for the operator, never a path it merely expects to
find. It deliberately never touches the udev rules file: those are
declarative, harmless for a device that is not attached, and removing them
would take away device access the operator may still be using. Power control
is the reversible half of this feature; device permissions are not.

### Why the applet is a separate repository

`hammunition-tray` is a client of the engine, exactly as the CLI and the
generated menu entries are: it calls `pkexec
/usr/local/libexec/hammunition-devctl park|wake|state`, the same helper
through the same one polkit action, and needs nothing installed as root that
`hardware apply` does not already provide. Keeping it out of this repository
means a KDE-specific dependency never enters this engine's own dependency
tree, and — more to the point — a Plasma user who has no interest in the rest
of Hammunition's catalog can be pointed straight at a small, single-purpose
tray applet without being handed a 249-package ham-radio catalog to get one
power switch.

### Amendment (2026-10-02): three more controllable classes, and the field laptop's entries

The suite's design gave the tray one place to switch every device that has a
control, and the catalog had exactly one `power_control` block. Three classes
join it, each `usb_deauthorize` with no quiet verbs: **`wwan-modem`**,
**`bluetooth-controller`** and **`camera`**. Four device entries carry the
field laptop's own hardware, their identifiers read-only on 2026-10-01
(`lsusb`, `udevadm info`, sysfs reads; nothing written, nothing parked):
`dell-dw5821e` (`413c:81d7`, the modem fitted now), `intel-ax210-bluetooth`
(`8087:0032`), `sunplus-integrated-webcam-fhd` (`1bcf:2a03`, named from
`udevadm`'s model string), and `dell-dw5930e`, the 5G card that is MHI/PCIe,
not USB.

What the amendment does not claim. **No park has been run on any of the three
new classes**, so none of the entries carries `maintainer_verified`, and each
class note says so. A USB park leaves the device unconfigured, not unpowered
at the port; the radios have a lighter switch (`nmcli radio wwan off`,
`bluetoothctl power off`) that the tray's planned Radios panel will wrap, and
the camera has none. **The DW5930e is the documented gap**: `pci_runtime`
stays schema-valid and refused (nothing is shipped that has not been run),
the entry exists so the gap, its reason (no USB node; the hardware report
reads USB only, so the entry is never offered for parking) and its route (the
radio switch) are on the page, and its PCI identifier is prose from the
maintainer's bring-up notes, not re-read on 2026-10-01 because the card is out
of the machine. No `udev` block is carried for any of them (D-028, D-029): an
identifier naming a chip or a module never names a `/dev` node, and
ModemManager, BlueZ and the kernel already name what they own.
### Amendment (2026-10-02): the helper moves to hammunition-tray; the engine exports two lists and a hand-over

**Decided, by the maintainer:** hammunition-tray is the device project, and
everything device-shaped lives there; components are split into their own
repositories, and the engine is the installer. The privileged helper
(`hammunition-devctl`, with its `power`, `polkit` and `linger` modules) is
therefore moving out of this repository into hammunition-tray, which gains
verbs this decision never had: `services` (start, stop, enable and disable by
name) and `radio`. **This change is the engine's half.** It does not move any
code and does not release anything.

1. **The helper reads two data files, not the engine's catalog.** `hardware
   apply` writes `/etc/hammunition/devctl-devices.yaml` (every class or
   device with `power_control`: name, summary, method, quiet verbs, each
   confirmed identifier as quoted `vendor`/`product` strings plus
   `product_string` for an ambiguous one, which is everything `state` needs to
   recognise a device on the bus without the catalog; hammunition-tray's
   contract 1, whose own reader loaded the real catalog's file without a note)
   and
   `/etc/hammunition/devctl-services.yaml` (`gpsd` is `gpsd.socket`, `time`
   is `ntpsec.service` or `chrony.service` by the daemon the machine has,
   `gps-resume` is `hammunition-gps-resume.service`). Root-owned `0644`, the
   header-owned shape of the GPS resume step (issue #177): disclosed whole in
   the plan, logged as `devctl_export`, read back (D-031), removed by
   `unapply` only when they start with the header, a foreign file refusing the
   run. An allow-list is data the helper reads and never an argument, so a
   name the files do not carry is refused by name and nothing from the caller
   is ever a unit or a path. Shapes: `docs/reference/devctl-lists.md`. The
   `time` row follows the machine: ntpsec first (the daemon D-058 disciplines),
   chrony where only it exists (D-072), and ntpsec named anyway where neither
   is found, said so in the plan, because a missing row would hide the switch
   rather than report it not installed.
2. **`hammunition services`** (document kind `services`, D-059) lists the
   helper's services and what each is doing; `services start|stop|enable|disable
   NAME` changes one. The engine asks the *installed helper*
   (`hammunition-devctl services state`, unprivileged) and never systemd; it
   passes a name, never a unit; a system service goes through `pkexec` and
   the one polkit action exactly as `hardware park` does, a user service
   runs as the operator; the effect is read back from the helper (D-031). The
   four verbs change the machine and have no JSON form.
3. **The hand-over.** Where the helper installed at
   `/usr/local/libexec/hammunition-devctl` answers `--version` with contract 1's
   one line (`hammunition-devctl contract N`, N at least 1), it is the tray's: `hardware apply` writes neither its wrapper nor an existing polkit
   action, skips the interpreter-writability gate (the engine's interpreter is
   not what that helper runs), and says so; `hardware unapply` leaves it and
   the action alone whatever an older log records. The probe is one argv,
   no shell, a fixed environment and its own process group, and never as root
   (as the invoking operator under sudo, as `nobody` otherwise), because
   planning must not run a user-owned tree as root before the gate that
   judges it has run. The engine's own copies of `devctl`, `power`, `polkit`
   and `linger` stay until the next release; where nothing answers `--version`
   the engine's helper is still written, so nothing regresses today.
4. **The tray units write neither the wrapper nor the action, and must not.** The
   first draft of this change put both in `hammunition-tray` and
   `hammunition-tray-qt` as `config_files`, and the review found it live against a
   pin (v0.4.0) that ships no helper: it would have written a wrapper over the
   working one, which exits 2, and the two writers would have traded the path.
   The tray repository then settled the question by its own design: its wrapper
   bakes in an interpreter (the engine's venv, so `time` verbs can import the
   engine), which no catalog file can name; and its `hammunition-devctl` `.deb`
   owns the polkit file, so a catalog write over it is the clash this decision's
   hand-over exists to avoid. So the units declare no `config_files` for either
   (`tests/test_tray_unit_files.py` holds the absence, and the manifests say
   why), and at the re-pin to the tray release that ships the helper each gains the
   `hammunition-devctl` `.deb` as a hash-pinned install step, disclosed in the plan.
   The engine's own `policy_xml()` now carries the tray's policy text byte for
   byte, so the two never read as drift.
5. **The probe's own safety.** The `--version` probe also clears the child's
   supplementary groups (`extra_groups=[]`), so "not as root" is true of the
   gid list and not only of the uid and gid; it is a deliberately weak identity
   test (any root-installed executable there that answers passes), which only
   decides whether the engine stops writing its own copy and authenticates
   nothing.

**Not measured:** the tray's helper reading these files as installed (it is not
released; its reader, run by hand against the exporter's output, took them
without a note); `hardware apply` and the `services` verbs on a real machine;
that the wrapper the tray's `.deb` writes answers `--version` here (the probe is
tested against scripts that print contract 1's line, not against the tray's
wrapper); and that its `time` verbs reach the engine under a `.deb`'s
`/usr/bin/python3` wrapper, which cannot import a venv (the tray's `install.sh
--interpreter` is the route that can).

**See also:** `docs/hardware/power-control.md` for the operator-facing page
— what parking changes, how to inspect it, and how to reverse it.

### Amendment (2026-10-02, later): the tray units install the tray's helper, from the tray's own archive

The re-pin the amendment above promised, to hammunition-tray v0.5.0, the
release that carries the helper (`devctl/`, contract 1). **Which route, and
why.** The release workflow had not published the `.deb` assets (its Parrot
mirror answered 502), so the `.deb` route this decision named was not
available; both units pin the tag's source archive instead, and the engine
places its files. Measured, 2026-10-02: tag `v0.5.0` is annotated
(`17f4715c…`) and peels to commit `b49e342b4c6883f6de061c5e6d66946ea53bdf8f`;
the archive's sha256 is `614148fb…bfc438` on two separate downloads and again
as the engine's fetcher verified it; 190,444 bytes, one top-level directory.
When the `.deb`s are published each unit can go back to them.

1. **What the engine does.** A `binary` block gains `devctl_helper` (the
   archive directory and the module list; the paths and the interpreter are not
   catalog data) and `placements`/`placement_dirs` (the files of the tray and the
   Qt tray, one printed `install -D -m MODE SRC DEST` each). The helper is copied
   from the unpacked archive to `/usr/local/lib/hammunition-devctl` (root never
   runs the unpack), the wrapper goes to `/usr/local/libexec/hammunition-devctl`
   with the engine's own venv interpreter baked in, exactly as `install.sh
   --helper-only --interpreter` writes it, and the polkit action is
   `policy_xml()`, which carries the tray's text. Wrapper and policy are
   compared byte for byte with what the pinned archive's own
   `render_helper_files.py` prints (`tests/test_devctl_helper.py`).
   After the install the helper is asked `--version` and a missing contract line
   fails the step (D-031); the effect check asks it again.
2. **It does not fight another owner, and says whose it is.** A helper that
   already answers contract 1 or newer is left alone, the plan naming the owner:
   a package (dpkg owns the policy), the tray's own installer (the wrapper
   carries its mark), an earlier run of this engine (the log attributes the
   entry script, and then it is refreshed to this pin), or an installer that did
   not mark its wrapper. A wrapper that answers nothing is replaced only when it
   is the engine's own old `hardware apply` one (that replacement is the
   hand-over); any other is left, by name.
3. **The gates are `hardware apply`'s.** The engine's interpreter, and the
   `hammunition` package its `time` verbs import, are checked as given and
   resolved: a tree any account can write refuses the install (a dry run
   reports the refusal too), a tree only its owner can write asks one typed
   `yes` that `--yes` does not answer. Nothing is checked when nothing is
   written.
4. **Where files may go is an allow-list in the schema** (`/usr/local/bin/`
   and `/usr/local/share/`, the Plasma applet directory,
   `/usr/share/hammunition-tray-qt/` because that script puts exactly that
   directory on its import path, hicolor icons, application and autostart
   entries), and **every destination must be named for the project** (a path
   component containing `hammunition` or `chiefgyk3d`). A destination outside
   the list, one not named for the project, a mode other than 0644 or 0755, a
   directory shared with other software, and anything at the helper's own three
   paths (the wrapper polkit authorises, its code, its action: `/usr/local/lib`,
   `/usr/local/libexec` and `/usr/local/sbin` are not on the list) do not load.
   The review found that an open `/usr/local/` let a catalog entry write the
   wrapper itself or a program on root's path. **The catalog has no tiers yet**
   (D-009 is a design, not code), so `devctl_helper` cannot be core-only today;
   when tiers exist it should be, and this is the follow-up.
5. **Uninstall rests on the log, and asks dpkg again.** Each `install -D` is
   attributed by the existing replay; a file the log does not attribute is
   reported and left. The helper is removed only when the log attributes its
   entry script, **and** no other tray unit still installed (one of its placed
   files is on disk) needs it, **and** no package owns the polkit action now
   (the log says what this engine once wrote, and a `.deb` may have taken the
   path over since); otherwise the plan says so. A placed file a package owns is
   left the same way. Every attributed module goes by its own `rm -f --`, so the
   replay un-attributes each before the directory goes whole.
5a. **A package's files are never written over.** `dpkg-query -S` is asked about
   every destination while the steps are built, and a clash is refused by name
   with the remedy (`apt-get remove hammunition-tray`): the 0.4.0 units were
   `.deb`s, so a machine that installed one meets this exactly once. A package
   that owns the policy but whose helper answers no contract 1 is refused at
   plan time too, naming the package.
5b. **The copy is checked against the archive.** The archive is hashed when
   fetched; what root copies is the unpacked tree in the operator's cache. The
   installed code is read back against the archive's own bytes before the
   wrapper polkit authorises is installed (a mismatch leaves no wrapper), the
   wrapper and the action are read back against what was staged, and the staging
   directory is made fresh through its parent's descriptor and written by
   descriptor, so a symlink cannot redirect a root run's write or `unlink`. What
   this does not close is the interpreter: the wrapper runs the engine's venv
   as root, an operator-owned tree, which is exactly the trade D-056 already
   makes and asks a typed `yes` for; the copy check adds no new exposure beyond
   it and is not a substitute for that consent.
5c. **A re-run repairs a helper that answers nothing.** A unit with
   `devctl_helper` is "already installed at its pin" only when the helper answers
   `--version` (its files remaining after the engine's venv was deleted is the
   common way to break one); and the gates are asked only for a unit that will
   write.
6. **`depends` replaces the `.deb`'s Depends line**, which apt no longer pulls
   in (`pkexec` where the `.deb` said `pkexec | policykit-1`, because
   `policykit-1` has no candidate on Debian 13).
7. **Still not catalog `config_files`.** `tests/test_tray_unit_files.py` still
   holds that absence, and now also holds the pin, the module list and the
   placed files against the cached archive.

**Not measured:** an install as root on a real machine (every step is printed
and the real-run test writes into a temporary prefix as an ordinary user; as
root the helper refuses a tree under `/tmp`, so that test is skipped there);
that Plasma lists an applet placed under `/usr/share/plasma/plasmoids` with no
package behind it; the helper's `time` verbs reaching the engine through the
venv under `pkexec`; the polkit prompt itself; the Xfce, LXQt, LXDE, MATE and
Cinnamon trays (as before); a target with no `pkexec` package (Debian 12), where
the unit is deferred by name; an uninstall on a real machine; and the icon cache,
the desktop database and Plasma's service cache, which the `.deb`'s triggers
refreshed and this route does not (a new login or `kbuildsycoca6` is the
untested way). Two tray units in one transaction each plan the helper, so the
plan prints it twice and the second run refreshes what the first wrote; the
install is not atomic (a failure between the module copies leaves a half-written
code directory and no rollback, which Hammunition does not promise, D-004).

---

## D-057 — Offline navigation: map regions are station data, fetched at a chosen freshness with the check named per region, and converted for Navit by a converter the engine owns

**Date:** 2026-09-28. **Status:** accepted (maintainer, 2026-09-27, on the
design in `docs/superpowers/specs/2026-09-27-navigation-maps-design.md`).
**Depends on:** D-049 (offline data is a catalog unit), D-035 (a missing
station value defers, never refuses), D-039 (one member failing does not
withhold the rest), D-031 (verify the effect), D-043 (the operator owns
what is built on their behalf), D-021 (state the licence, never adjudicate
it). **Amends:** D-049, whose `data` method installs fixed, pinned
artifacts only, with two new install methods; D-055, whose vocabulary was
fixed at 55 tags, with a 56th.

**Why.** The maintainer, 2026-09-27: turn the field laptop into a GPS
navigator "for pure emergency situations, phone and everything is down",
and "for daily use as well as EMCOMM". Maps have to be on the machine
before anything goes wrong, so the daily path and the emergency path are
the same path: install ahead of time, refresh as routine, and nothing at
the moment of use needs a network. D-049 had already named Navit's extract
as its third case, and the extract did not fit D-049's shape twice over:
which file to fetch depends on the operator's region and on the date, so
it cannot be pinned in a manifest; and Navit cannot read what Geofabrik
publishes, so the file that is useful is one the engine has to make.

### Regions are station data, and printed only where the operator sees them

`hammunition station set --map-regions` takes Geofabrik's own region paths
(`north-america/us/vermont`), comma-separated, and replaces the list;
`--map-freshness` takes `yearly`, `monthly` or `latest`. Both are stored in
station config beside the callsign, mode 0600. A region list says where
somebody lives or travels, which is the same class of fact as a grid
square, so `station show` and `station set` print how many regions are set
and never their names. The install plan prints them, because the plan is
the disclosure and is on the operator's own terminal.

With no regions set, `osm-regions` and `osm-navit` are deferred by name and
Navit and gpsd install (D-035's shape, D-049 rule 3); the plan names the
command to run. No region is guessed or defaulted.

### Three freshness modes, and why yearly is the default

Measured on Geofabrik 2026-09-27: a dated extract every 1 January back to
2014, the 1st of each of the last three months, the last seven days, and a
`-latest` name that is a 302 to today's dated file.

| Mode | File | How long it stays published |
|---|---|---|
| `yearly` (default) | `<region>-YY0101.osm.pbf`, this year's 1 January | Years |
| `monthly` | `<region>-YYMM01.osm.pbf`, this month's 1st | About three months |
| `latest` | the dated file `-latest` redirects to, resolved when the plan is made | About a week |

Yearly is the default because it is the only one that can be pinned for
long enough to matter, and because the roads a navigator needs change
slowly: a map from 1 January is a good map for a year, and a file that
stays published for years can be checked against a hash measured once. A
snapshot not yet published is looked for one period back, so a machine on
2 January is not refused while Geofabrik catches up. `monthly` and `latest`
are there for the operator who wants newer data and accepts the weaker
check that comes with it most of the time.

### MD5 is the weaker path, and it is disclosed, not refused

Geofabrik publishes an MD5 beside every file and nothing stronger. For a
region and snapshot the catalog pins (`catalog/data/geofabrik-pins.yaml`),
the file is checked against the sha256 Hammunition measured, and the plan
says **"sha256, pinned by Hammunition"**. For every other region, every
snapshot not in the pin list, and every region in `latest` mode, it is
checked against Geofabrik's MD5, and the plan says **"MD5 from Geofabrik
only; not pinned"**, on that region's line, every time. `--yes` does not
change what is printed.

MD5 fetched from the same server as the file catches a damaged or
truncated download. It does not catch a deliberately altered file, because
whoever can alter the file can alter the MD5 beside it. The alternatives
were to refuse every unpinned region, which makes the feature US-only and
`latest` impossible, or to mirror the files, which this project does not
do. The maintainer approved the MD5 path on the condition that it is never
silent (2026-09-27). The project's rule that a non-apt
download is checked or refused still holds: every region is checked, and
the plan says by what.

### The pin list is generated, measured, and regenerated on a calendar

`scripts/gen_geofabrik_pins.py` streams each pinned region's current yearly
and monthly file, hashes and sizes it, and discards the bytes; nothing is
kept or mirrored. The first pass, 2026-09-28, pinned the 50 US states and
DC at `260101` and `260901`: 102 rows, about 10 GB downloaded. Adding a
region is one line in the generator's list and a regeneration, so the
pinned set is always what the generator says it is.

A pin names one dated file, so it is only current while that file is the
one a mode resolves to. The yearly pins cover the whole of 2026 and are
regenerated once a year, after 1 January; until they are, a yearly region
in the new year resolves to a file with no pin and is checked by MD5, which
its plan line says. A monthly pin is current for one month; monthly mode
falls back to MD5 on the 1st of the next month unless the list has been
regenerated. The weekly CI pin-review job runs `--check`, which asks each
pinned URL for its `HEAD` and goes red when one no longer answers 200 or
its size has changed; a monthly pin that Geofabrik has aged out fails
there, with the command to regenerate.

### Derived data: a converter named by enum, run as the operator

Navit reads its own binary format, not `.osm.pbf`. A new install method,
`derived`, produces files by running a converter over another unit's
installed data; the manifest names the converter by enum
(`converter: navit-maptool`) and the source unit (`source: osm-regions`,
which must also be in `depends`), and the engine owns the command line
(`maptool --protobuf -i <input> <output>`), exactly as
`build_system: cmake` is an enum the source backend implements. No command line comes
from the catalog, and a new converter is implemented in the engine before a
manifest can name it.

maptool parses downloaded data, and a parser of downloaded data does not
run as root where it need not. It runs as the operator, in a staging
directory under the operator's cache
(`~/.cache/hammunition/build/osm-navit/`), and writes its scratch files
there. Under `sudo`, root never
creates, reads, hashes or removes anything in that directory itself: each
of those is a process dropped to the operator, and root's one look is an
`lstat` that refuses a staging directory which is a symlink. The effect is
checked, not the exit status (D-031): the output must exist and be
non-empty, and its sha256 is taken. Root then publishes it into
`<prefix>/share/hammunition/data/osm-navit/<slug>.bin`, and the copy is
verified against that sha256 before it replaces anything, without following
a symlink to make it. The same
boundary for the source backend's builds, which under `sudo` still run as
root in an operator-owned build directory, was found during this work and
is issue #125; it is not changed here.

A region already converted from the same snapshot is not converted again. A
region dropped from station config has its `.osm.pbf` and `.bin` removed on
the next install, each as its own disclosed step. Navit's configuration is
written last, beside the maps, from the installed `/etc/navit/navit.xml`
with two anchored changes: speech through `espeak-ng`, and one enabled
mapset listing the maps that exist. It lives in the data directory, not in
`~/.config` as the spec first had it, so it is system-wide and `uninstall`
removes it with the maps. The launcher is `navit-offline`, so plain `navit`
still runs Debian's own configuration, and `~/.navit` is never touched.

### One region failing does not stop the others

A region whose download does not verify, whose install fails, or whose
conversion fails is recorded, its later steps are skipped, and every other
region installs and converts; Navit's configuration lists only the maps
that exist. The last step of the transaction then fails the run by name,
exit 1, listing each region that did not install. A partial map install is
never reported as a success.

With no network, a region already installed is kept as it is and the plan
says it could not check for a newer map; a region not installed and not
resolvable refuses the plan, naming it, exit 2. A **pinned** region
resolves entirely from the pin list with no network asked at all, so a
region about to be fetched — pinned or not, not already installed at its
resolved snapshot — is also HEAD-checked before the plan prints; one that
cannot be reached refuses the same way, named, rather than surfacing later
as a fetch failure after apt has already run (fix round 1, I3). Not enough
disk space refuses the plan too, with the estimate and what is free, for
each file system that is short — one with enough room is not named.

### The disk estimate is measured on one region, and says so

Per region the plan counts the download twice (the verified cache copy and
the installed copy), Navit's map at **0.8×** the download (staged, then
installed), and **2×** the download of maptool's scratch while it runs.
Measured on one region on the field laptop, 2026-09-28: a 6.1 GB
country-sized region, converted to a 4.7 GB `.bin` in 75 minutes on an
i7-1185G7, peak memory about 2.4 GB, more than 12 GB of scratch. The plan
calls its figures "an estimate, measured on one region" until more regions
are measured.

### The `navigation-maps` tag, amending D-055

D-055 cut the vocabulary to 55 tags and meant it to be fixed. Offline maps
and turn-by-turn navigation had no place among them: `gps-gnss` is
receivers and the daemon that shares one, not the thing a person looking
for a map would search for. One tag is added, `navigation-maps`
(*Navigation & Maps*), in the *Operate the Station* group beside
`gps-gnss`. The rule that a tag is added by a decision, not in passing,
stands.

### The profile is post-1.0

`navigation` (`gpsd`, `gpsd-clients`, `navit`, `osm-regions`, `osm-navit`)
ships at `stage: post-1.0`. The 1.0 profile set is the one the maintainer
accepted and the tests assert; adding to it is his decision, not a side
effect of a new profile.

### What is measured, and what is not yet

Measured: Geofabrik's snapshot layout and redirects (2026-09-27); every
tool in Parrot 7.3's archive (`navit` and `maptool` 0.5.6, `espeak-ng`
1.52.0); the 102 pins (2026-09-28, `--check` passing); one conversion (a
6.1 GB country-sized region, above), by hand, not through the engine;
`UrllibProbe.text` against Geofabrik's live region index (2026-09-28,
`index-v1-nogeom.json`, 555 regions parsed by `region_ids`).

Not yet measured, and not claimed until the field laptop's bench page
records it:

- the whole install through the engine, with the maintainer's regions;
- Navit routing across two separately converted regions, a trip from one
  state into the next (if it does not, routing gets one merged map and the
  display keeps per-region files);
- the whole path with networking off: launch, position from gpsd, a route,
  voice;
- voice with a street name containing an apostrophe, which Navit's stock
  `'%s'` quoting may break;
- `UrllibProbe.head` against the live server, which a fetch and the I3
  plan-time reachability check both depend on; the tests replace it.

### Deferred

Hiking and topographic maps (QMapShack, `mkgmap`, contours, GPSPrune) are
the next piece; Kiwix with a Wikipedia ZIM and ETC's tileset with a tile
server are the one after, and D-049's order changes to put Navit's extract
before the tileset. Each gets its own specification.

(2026-09-28: the next piece is built as **D-061**: QMapShack, Garmin maps,
one Routino database and Copernicus elevation from the same regions.
GPSPrune was measured then and is not carried, because it uses online
tiles.)

**Consequences.** `RegionalDataInstall` (`method: osm-regions`) and
`DerivedDataInstall` (`method: derived`) in the schema;
`src/hammunition/geofabrik.py`, `src/hammunition/backends/regions.py`,
`src/hammunition/backends/derived.py`, `src/hammunition/navit_config.py`;
`map_regions` and `map_freshness` in station config; the plan's *Map
regions* section; `catalog/packages/navit.yaml`,
`catalog/packages/osm-regions.yaml`, `catalog/packages/osm-navit.yaml`,
`catalog/profiles/navigation.yaml`; the generated
`catalog/data/geofabrik-pins.yaml` and its generator; the weekly `--check`.
The operator's page is `docs/guides/offline-navigation.md`.

### Amendment (2026-09-28): Navit opens on the maps and follows the GPS; the map factor is 0.9

The maintainer installed two US-state-sized regions on the field laptop
with `hammunition install navigation`. Every step verified, and Navit
opened on a blank screen. Three findings, each fixed where it arose:

**Navit opened on Munich.** The generated configuration kept the stock
`<navit center="11.5666 48.1333">`, and no map existed there. The two
anchored changes above are now four. The configuration step reads the
bounding box from the first installed region's `.osm.pbf` header and
centres Navit on its midpoint, written as the stock file writes it
(`"lon lat"`, four decimals). `src/hammunition/osm_pbf.py` reads only the
file's first blob, the `OSMHeader`: a 4-byte length, a `BlobHeader` that
must say `OSMHeader`, and a raw or zlib `Blob`. It uses the standard
library only, and both the packed and the inflated size are capped at
1 MiB. A region is gigabytes and its header a few hundred bytes. A region
that did not convert gives way to the next. So does one whose `.osm.pbf`
is gone, one whose header has no bbox (the format allows that), and one
whose header cannot be read or gives a bbox off the globe. Each is named in
the step's outcome, with the region the centre came from, and none fails
the step: a configuration that opens on the stock centre still loads every
map, and failing the step would leave Navit no configuration and hide the
ledger's report of a region that did fail. The stock centre is used only on Navit's
first start. After that Navit restores its last view from
`~/.navit/center.txt`. A machine that has already opened on Munich goes
back there until that file is removed, which the guide's troubleshooting
entry says, with the command.

**The view never moved to the GPS**, even with a 3D fix. The gpsd
`<vehicle>` lacked `follow="1"`, which Navit's own stock comment says to
add "to have the view centered on your position". It is added to the one
enabled vehicle reading `gpsd://`. Anchors are matched outside comments,
because the stock file comments out two vehicles of its own, one of them on
gpsd. A vehicle that already says `follow=` is left as it is. Not exactly
one enabled gpsd vehicle does not fail the step: the configuration is
written without `follow`, and the outcome says "Navit will not follow the
GPS" and why. An operator who pointed the conffile at a serial receiver, or
added a second gpsd vehicle, loses following, never the configuration. The
mapset refusal stays hard, because two enabled mapsets are mis-loaded.

**The disk estimate was low.** The two regions converted at **0.874×** and
**0.856×** the download, against the 0.8× set above from the country-sized
region's 0.77×. The factor is now **0.9×**, which is above all three. The plan's wording
is "an estimate, measured on three regions, scratch on one", because the
2× scratch factor still comes from the country-sized region alone.

**maptool left scratch behind** after a successful conversion:
`country_*_broken_.tmp` and `country_*_poly_.tmp` in
`~/.cache/hammunition/build/osm-navit/`. Once a region's `.bin` is
installed, exactly those are removed: regular files whose whole name has
one of the two shapes, in the staging directory only. They are removed by
name through a descriptor from `open_operator_dir`, which walks
`O_NOFOLLOW` from the operator's home. A symlink or directory with such a
name, any other `.tmp`, and another region's `.bin.part` stay. The plan
line for each map install says this. A failure to clear is named in the
outcome and does not fail the run, because the map is installed and the
files are the operator's.

Not yet measured: a configuration written by this change, on the field
laptop. The maintainer is testing a hand-edited copy with the same two
edits.

### Amendment (2026-09-28): address search, and a closed country border merged into every region

After the fixes above, Navit ran on the field laptop on both regions and
followed the GPS, and address search found almost nothing: Actions → Town
listed a handful of towns and not the largest city. Measured from the
installed maps: one US-state-sized region's search index held **about a
dozen items for a map that draws a few thousand places**.

**Cause.** maptool files a town under a country with a point-in-polygon
test against that country's boundary relation, and treats a relation as a
country only when it is `admin_level=2` with `ISO3166-1`
(`maptool/boundaries.c`, `process_boundaries_setup`). A town inside no
country is dropped from the index ("Lost town"), though it is still drawn.
A Geofabrik state extract carries the US relation with only the member
ways inside the state, a small fraction of them, so the polygon
is open, maptool logs "Broken country polygon", and only the few towns
still carrying an `is_in` or GNIS tag get a country. maptool has no option
to read a boundary from anywhere else; `-U` is the only switch that
touches the problem. Upstream documents the same thing: its `osm.rst` says
to concatenate the whole country boundary with a sub-country excerpt.

**Options weighed.**

- **`-U` alone.** Measured: over four thousand index items, every town found, but all
  under a pseudo-country "* Unknown, add is_in tags to those cities",
  with no state or county, and reachable only after changing the country
  to it. The maintainer rebuilt his maps this way as a stopgap. A fix that
  makes the operator search a country called Unknown is not the fix.
- **The whole country's extract.** Its boundary is complete. The US file
  is 12.2 GB; asked of every operator for the sake of one polygon.
  **Rejected.**
- **The relation from the OSM API** (`relation/148838/full`, 31.6 MB).
  Online, and it changes continually, so it cannot be pinned or checked.
  **Refused**, under the rule that every non-apt download is checked.
- **The extract's own state relation, cloned as the country.** The
  state's `admin_level=4` relation is complete in its own extract, and a
  synthetic `admin_level=2` relation reusing its ways worked with no
  download at all (within a percent of the Natural Earth build). It relies on the extract happening to
  carry one complete sub-country relation, and on picking it, which is a
  heuristic per region shape. **Kept in reserve, not carried.**
- **A closed border from Natural Earth**, merged into the extract.
  Measured: **over four thousand items, a few hundred times as many, all
  under the USA**, almost all with a state, and no build time added (phase 9, the multipolygons, dominates as before).

**The choice.** A new data unit, `country-boundaries`: Natural Earth's
1:10m admin-0 countries, GeoJSON, public domain, pinned to the file at
the v5.1.2 tag's commit. Natural Earth publishes no release assets and no
checksum, so the sha256 is Hammunition's own, measured twice, and the
manifest says so. `osm-navit` names it in a new `boundaries` field of the
`derived` block (like `source`, it must be in `depends`) and depends on
`osmium-tool` from the archive. Before maptool runs, the converter:

1. Finds the region's countries in `catalog/data/geofabrik-countries.yaml`,
   generated by `scripts/gen_geofabrik_countries.py` from Geofabrik's
   region index: a region's own `iso3166-1:alpha2` and `iso3166-2`
   prefixes, else its nearest ancestor's (Bayern takes Germany's). The
   plan reads the table with no network. A region with no country (a
   continent, `dach`, `us-northeast`) is converted without a merge, and
   its plan line says `border: none known`.
2. Writes, with the standard library only, one closed relation per
   country (`type=boundary`, `boundary=administrative`, `admin_level=2`,
   `name`, `ISO3166-1`), ids above 9×10^15 so none collides with an OSM
   id, found by Natural Earth's `ISO_A2_EH` (its `ISO_A2` is `-99` for
   France and Norway). `src/hammunition/country_boundaries.py`.
3. Pipes that XML into `osmium cat` on stdin and `osmium merge`s it with
   the region in the staging directory, as the operator; nothing root
   composes is written into the operator's directory.
4. Runs `maptool --protobuf -U -i <merged> <out>`. `-U` is always on: it
   catches the places the coarse border misses.
5. Reads maptool's log. "Broken country polygon" for the extract's own
   partial relation is expected and always there; for the merged border's
   relation it fails the region by name. The merged copies are removed
   whether the map built or not.

Regions convert **one at a time, never in parallel**: maptool writes
fixed-name temp files (`coords.tmp`, `ways_.tmp`, ...) into its working
directory, all regions share one staging directory, and two concurrent
runs there segfaulted (measured 2026-09-28). A test asserts it.

**Maps built before this are converted again.** The `.bin.source`
sidecar gains a second line, `converter: navit-maptool 2`. A map at the
right snapshot without it is pending, and the plan says `(converter
changed)` beside it; the region is not downloaded again. An engine older than
this one reads the whole two-line sidecar as the snapshot, so after a
downgrade every map looks stale and is converted again: slow, not wrong. The plan also
discloses the border file (13.3 MB, its licence, "sha256, pinned by
Hammunition"), the merge, and `-U`. The disk estimate counts the merged
copy, one more times the download, in the staging directory while it
converts.

**Residual gaps, measured on one region.** A few places close to the
national border fall on the other side of Natural Earth's 1:10m line and
are indexed under Unknown by `-U`, not lost. A handful of places at the
extract's edges, in the neighbouring states, are under the USA with no
state, because those states' own relations are incomplete in the
extract. The places across the national border that the extract carries
are correctly not filed under the US, and sit under Unknown too.

**Privacy.** Exact per-region counts are not recorded here or anywhere in
the repository: each can be reproduced for every US state, and a match
would say which state the maintainer's maps cover, the same class of fact
as the grid square (review, 2026-09-28). Figures are rounded or given as
ratios; the one synthetic region below keeps its exact numbers.

**Measured and not.** The Natural Earth build, the `-U` build and the
state clone were built by hand from the installed region, in scratch
(2026-09-28). The converter's steps were run end to end in scratch with
the archive's maptool and osmium-tool 1.18 on a synthetic three-town
region carrying an open US relation: unmerged, 0 index items; merged, 3
under the US, with the open relation's warning the only broken polygon.
Not yet run: `hammunition install navigation` with this change on the
field laptop, and the search on the maps it builds;
`docs/reference/bench-verification-5430.md` (session 11) names the steps.

## D-058 — GPS time: four modes, `auto` by default; the clock follows the GPS through ntpsec only while the receiver is awake; the one helper writes the time files and `hardware apply` installs ntpd's two grants

**Date:** 2026-09-29. **Status:** accepted (maintainer, 2026-09-28, "#124
works", on the design in `docs/superpowers/specs/2026-09-28-gps-time-design.md`);
built, **not yet run on the field laptop** — every behaviour this record
marks *bench* is unmeasured until `docs/reference/bench-verification-5430.md`
carries it. **Depends on:** D-056 (the one helper behind one polkit action,
and a parked device), D-022 (coexist with the distribution's choice, never
displace it), D-031 (verify the effect), D-021 (disclose a capability in
plain words). **Extends:** D-056, to the time source that depends on the
device it parks.

**Why.** The maintainer, 2026-09-28: "a configuration to use the GPS for
time vs standard NTP if it's in the laptop, think EMCOMM situation where
internet may not be available". Without the network nothing corrects the
clock; FT8 and the other weak-signal modes stop decoding beyond about a
second of error, logs carry wrong times, and an EMCOMM station loses the one
clock everybody else trusts. The field laptop's USB GNSS receiver knows the
time to far better than a second.

### The four modes, and the rules they keep

| Mode | GPS awake: the clock follows | GPS parked |
|---|---|---|
| `auto` (default) | the network and the GPS, the network preferred where ntpsec's selection allows (*bench*, below) | the network only |
| `prefer-gps` | the GPS first; the network is a check and the fallback | the network only |
| `ntp-only` | the network only; the GPS is never used | the network only |
| `gps-only` | the GPS only; network servers are never used | nothing: holdover, and `doctor` warns |

The mode is optional (`auto` when unset) and persists across reboots in
`/etc/hammunition/time.yaml`, root-owned 0644, written only by the helper.
**The device state wins**: a parked receiver never feeds the clock, whatever
the mode. Nothing is rewritten on a park: a parked receiver's shared-memory
segment stops updating and ntpd drops it from selection by its own
reachability rules, so the rule holds by construction and a receiver kept
parked across reboots keeps GPS time off too, with no second setting.
(*Bench*: the decay without a restart is inferred from ntpsec's documented
reachability model, not yet watched.)

### Mechanism: ntpsec only, one file of our own, marked edits to the conffile

Measured read-only on Parrot 7.3 with ntpsec 1.2.3 (2026-09-28, spec §4b):
ntpsec is the time daemon; gpsd publishes the first receiver's time to
System V shared memory units 0 and 1, root-owned 0600; ntpd reads
`/etc/ntpsec/ntp.d/*.conf` after `ntp.conf` when the directory exists (the
package does not ship it); `tos minclock 4 minsane 3` in the shipped
`ntp.conf` stops a lone refclock disciplining the clock; `restrict nopeer`
no longer holds `pool` associations back; and SIGHUP does not reread the
configuration.

So:

1. **`/etc/ntpsec/ntp.d/hammunition-gps.conf`**, rewritten whole per mode
   and generated from the mode alone: `refclock shm unit 0 refid GPS time1
   0.000`, plus `stratum 10` in `auto` and `prefer` in `prefer-gps`; no
   refclock line in `ntp-only`.
2. **`/etc/ntpsec/ntp.conf`**, a dpkg conffile, edited only on marked lines:
   `#hammunition-gps:off# ` disables a line (the `tos minclock … minsane …`
   line in every GPS mode, the `pool`/`server` lines in `gps-only`) and
   `#hammunition-gps:was# ` keeps an original whose `prefer`red copy follows
   it (each source in `auto`). Every edit is restored exactly, so the
   conffile's checksum matches the package's again; an edit whose anchor
   line is missing is refused, never guessed; a preferred copy edited by
   hand is refused with the line numbers, before anything is written.
3. **`systemctl restart ntpsec`** after a change: a fixed argv, never SIGHUP
   and never `ntpq :config` (documented experimental, with a silent-failure
   mode). If ntpsec will not restart on the new files, the previous three
   are put back and ntpsec is started on them, so a mode change cannot leave
   the machine without a time daemon; the message names
   `journalctl -u ntpsec -n 20`.

Both open questions in the spec are carried as one value,
`hammunition.gpstime.mode.ROUTE`, and both answers are implemented and
tested: whether `tos minclock 1 minsane 1` in the ntp.d file overrides the
conffile's line (it would leave `ntp.conf` untouched outside `gps-only` and
`auto`'s preference), and whether `prefer` on the pools makes ntpsec follow
the network while it is reachable. Until the bench answers, `ROUTE` takes
the documented route (the conffile edit) and the spec's description of
`auto` (the pools preferred), and `time1` is `0.000`, no correction.

### Privilege: no new action, and the helper never widens a daemon

`hammunition-devctl` gains `time mode auto|prefer-gps|ntp-only|gps-only`
and `time state`. The mode is a fixed enum; every byte written derives from
it; the three paths are admitted by an exact-string guard, written
atomically (`hammunition.rootfiles`, lifted from the kept-off rules file of
D-056) under a directory lock on `/etc/hammunition`, so a tray click and a
CLI call cannot splice. `time state` is unprivileged JSON the tray polls.
The one polkit action's wording widens to "or set the clock's time source";
no second action exists.

ntpd drops to `ntpsec:ntpsec` with `cap_net_bind_service`, `cap_sys_nice`
and `cap_sys_time` (measured from `/proc/<pid>/status`), so it cannot attach
gpsd's 0600 segment. Two grants, installed by `hammunition hardware apply`
under sudo and never by the helper:

- `/etc/systemd/system/ntpsec.service.d/hammunition-gps.conf`,
  `AmbientCapabilities=CAP_IPC_OWNER`. **CAP_IPC_OWNER bypasses permission
  checks on all System V IPC**, and ntpd faces the network; the plan prints
  that sentence before it runs.
- `capability ipc_owner,` in `/etc/apparmor.d/local/usr.sbin.ntpd`, the file
  Debian reserves for local additions (ntpsec's `README.Debian` documents
  this half), as a marked block, with the profile reloaded by
  `apparmor_parser -r`.

*Bench*, and the first thing to run: whether ntpd keeps the ambient
capability after it drops privileges, so that `SHM(0)` is actually reached.
If it does not, this route is revisited before anything else ships.

`hardware apply` then sets the first mode through the helper, so one code
path writes the time files; the plan prints every write that mode causes,
the `ntp.conf` lines included, and refuses before anything runs when an
anchor is missing. **Only where gpsd is installed**: without it there is no
GPS time to read, so ntpd's privilege and `ntp.conf` are left alone and the
plan says why. `hardware apply --no-gps-time` leaves ntpsec, its grants and
`fake-hwclock` alone on any machine, so device setup never forces GPS time
on an operator who does not want it (final review, 2026-09-29).

**What the GPS modes cost.** Turning off `tos minclock 4 minsane 3` lets a
lone GPS set the clock, and also drops ntpd's `minsane` to its default of 1
for network sources: one source can set the clock alone. `ntp.conf(5)`:
minsane "should be at least 4 in order to detect and discard a single
falseticker". The plan quotes that sentence under the edit; `ntp-only`
keeps Debian's floor. Whether `tos minclock 1 minsane 1` in ntp.d would
avoid the conffile edit changes nothing about this cost, only where it is
written. `hardware unapply` takes all of it back by
content, not by the log: a file is removed only when it starts with the
header Hammunition writes, `ntp.conf`'s marked lines are restored byte for
byte to what they were before Hammunition's edits (read back against that
text; that `dpkg --verify ntpsec` is then clean is not yet measured), only the marked block
leaves the AppArmor local file (which ntpsec's maintainer script created
and the profile's `#include` needs, so it is never deleted), and a
hand-edited `ntp.conf` refuses the whole unapply before anything runs.

### Reading it: `ntpq -pn`, always numeric

`hammunition time`, `doctor` and the helper's `time state` share one reader:
`ntpq -pn` (the peer marked `*` or `o` is what the clock follows; `SHM(0)`
with refid `.GPS.` is the receiver) and `ntpq -c rv` (`reftime` and
`clock`; holdover age is `clock - reftime`). Always `-n`: without it ntpq
resolves every peer address, and with the network down that waits on DNS
that is not there, the one situation this exists for. `doctor` states
holdover under a day as information and warns past 86 400 s, and warns on
`gps-only` with the receiver parked, on missing grants in a GPS mode, and on
ntpd started from a DHCP-supplied configuration (whether ntpd reads the
ntp.d directory then is *bench*).

### Holdover and the hardware clock

Measured on the field laptop (2026-09-28): a battery-backed `rtc0` in UTC,
written back every 11 minutes by the kernel (`CONFIG_RTC_SYSTOHC`), ntpsec's
drift file, and ntpd's `-g`. Holdover therefore already works on a machine
with an RTC; this decision only reports it. **The docs recommend a
battery-backed RTC first** (the maintainer, 2026-09-28), and `doctor` warns
on any target where `/sys/class/rtc` is empty, naming the fix. There,
`hardware apply` offers `fake-hwclock` from the archive, disclosed as a
stopgap (it restores the last saved time, wrong by however long the machine
was off); it is never offered where a real clock exists, and dpkg is not
even asked. The hour-long holdover drift figure is *bench*.

### Amendment, 2026-09-30: gpsd runs with `-n`, from the same drop-in as the `chrony` unit

**Measured** on PR #162 (branch `gap-03-gps-time`, the `chrony` unit,
D-072), in a container with a script serving NMEA: gpsd put **no samples**
into the shared-memory segment in 8 s without `-n`, and **nine** with it,
both with the device on gpsd's command line and added through `gpsdctl
add`. gpsd polls a receiver only while a client is connected unless it runs
with `-n` (gpsd(8)). The field laptop's `/etc/default/gpsd` has
`GPSD_OPTIONS=""`, so as first built, ntpd would have found nothing to
read: the plan's bench Step 1 could not have passed.

So `hardware apply` writes
`/etc/systemd/system/gpsd.service.d/hammunition-gps.conf`:

```
# Written by Hammunition (catalog unit `chrony`, D-072).
# Poll the receiver with no client connected, so chrony gets its time.
[Service]
Environment=OPTIONS=-n
```

Debian's gpsd.service runs `gpsd $GPSD_OPTIONS $OPTIONS $DEVICES`, and
`/etc/default/gpsd` sets no `OPTIONS`, so the drop-in adds the flag without
touching gpsd's conffile. **The path and the text are the `chrony` unit's,
byte for byte**, header included: the two units then never rewrite each
other's file, and the second to arrive finds it current and does nothing.
The header names the other unit because its text came first; a neutral
header would have to change in both at once. A test pins the text, and a
second test compares it with the chrony unit's manifest (catalog/packages/chrony.yaml on #162) once that unit
is in the catalog. A different text at that path is refused at plan time,
never overwritten. It is disclosed with how to inspect it (`systemctl cat
gpsd`), and gpsd takes it at the next boot: the disclosure says to reboot,
because restarting gpsd in place left two gpsd processes in #162's test.
It counts as one of the grants `hammunition time` and `doctor` check.

**Ruling on removal: shared, removed only when no other unit needs it.**
`hardware unapply` removes the drop-in only when it holds exactly this text
**and** the `chrony` unit's other file, `/etc/chrony/conf.d/hammunition-gps.conf`,
is absent; otherwise it stays and the rest of GPS time is still taken back.
ntpsec and chrony cannot both be installed, so the case is a machine that
switched daemons; the file that says the chrony unit is still configured is
the evidence, read by content like everything else unapply removes. Not yet
measured on the field laptop, like the rest of this record.

### Amendment, 2026-10-01: a resume step for the GPS receiver, installed with the grants (issue #177)

**Measured** read-only on the field laptop (issue #177's measurement
comment, 2026-10-01). After a suspend the GPS had no fix until the receiver
was parked and woken. Across 19 suspend/resume cycles in one boot the u-blox
receiver kept the same USB device number, and its `connected_duration`
equalled the time the machine was awake. It was enumerated once, at boot,
and never re-enumerated. So no `gpsdctl` remove or add follows a resume, and
gpsd keeps a tty that can go quiet without an error or a hangup. Every
recovery that worked gave gpsd a fresh open: session 12's gpsd restart (a
fix within 1 s) and a park and wake. The kept-off rule was absent and
nothing writes the port's `power/control`. Without `-n` and with no client
watching, gpsd closes the receiver before the sleep and opens it fresh after
it, so the fault is intermittent there. **With this record's `-n` drop-in
it would follow every suspend.** On a laptop that sleeps, the resume step is
therefore a prerequisite for GPS time.

**Ruling: recorded here, not under D-056.** The fault is in gpsd's open
handle, not in power control: the measurement rules out the kept-off rule,
autosuspend and park/wake as causes, and the step never parks or wakes
anything. What it shares is this record's shape: a root-owned file
installed by `hardware apply`, disclosed in the plan, read back afterwards,
removed by `hardware unapply` by content, with an opt-out flag. And it
exists because of this record's `-n`.

**The step.** A device class names it, `resume: {step: gpsd_reopen}`, an
enum the engine implements, as `power_control.method` is. The catalog
carries no command. Where gpsd is installed and a catalog entry names the
step, `hardware apply` installs:

- `/usr/local/libexec/hammunition-gps-resume`, `0755`, staged and installed
  by `install -D` like the helper, beginning with Hammunition's header. A
  standard-library Python script (`src/hammunition/hardware/gps_resume_script.py`)
  run by `/usr/bin/python3 -I`, which gpsd's package depends on. With no
  `/dev/gpsN` it does nothing, so a parked receiver is never woken. With no
  gpsd control socket it does nothing, because `gpsdctl add` would start a
  gpsd of its own outside systemd. Otherwise, for each receiver it runs
  `gpsdctl remove` then `gpsdctl add`, by the path gpsd reports for it
  (`?DEVICES;`): the tty as `gpsdctl@` registers it, or `/dev/gpsN` where
  gpsd was configured with that name, so gpsd never holds two handles on
  one port. Then it sends `?DEVICES;` on 127.0.0.1:2947 with a 2 s limit. No
  device, or no answer: `systemctl try-restart gpsd.service`. One journal
  line per action; a failed restart or a failed `gpsdctl add` fails the
  unit. **The restart does not cover the measured fault on its own terms:**
  in #177 gpsd still listed the device while the tty was silent, and after
  the re-add it lists it again whether or not data flows. The script makes
  no data check (the design asked for `?DEVICES`, and a watch for reports
  after the re-add is the follow-up if the bench shows the re-add alone is
  not enough). So the restart catches gpsd losing the device or hanging, and
  a receiver that stays silent is left to the manual steps.
- `/etc/systemd/system/hammunition-gps-resume.service`, a oneshot with
  `After=` and `WantedBy=` `suspend.target hibernate.target
  hybrid-sleep.target suspend-then-hibernate.target`, enabled and never
  started. It is a unit rather than a `system-sleep` hook so that
  `systemctl cat` shows it and its runs land in the journal.

Both are printed whole in the plan, with `systemctl status`,
`journalctl -u` and `hardware unapply`. Each step is logged (`gps_resume`).
Afterwards the files are read back and the four `.wants` links checked: an
exit code from `systemctl enable` is not taken as proof (D-031). A file at
either path without the header refuses the plan and is never overwritten.
`hardware unapply` disables the unit and removes both files, each only when
it starts with the header. `hardware apply --no-gps-resume` leaves the step
out. `--no-gps-time` does not, because the step also serves `cgps`, `xgps`
and the map tether. `doctor` reports whether it is installed when a GPS
receiver is attached and gpsd is installed.

**The heavier recoveries stay manual.** They are a gpsd restart while gpsd
still lists the receiver, and park and wake through the helper, which is the
heaviest (74 s to a fix from cold, bench session 10). The step never parks
or wakes anything. The operator does, by hand, and the docs say so
(`docs/hardware/power-control.md`, "After suspend"). *(Superseded 2026-10-04 below.)*

**Not yet measured:** whether `gpsdctl remove` and `add` bring the fix back
after a real suspend; whether the unit, which runs as soon as the system is
resumed, can run before the USB port has finished resuming; whether
the stall is in gpsd's handle or in `cdc_acm` (the issue's bench step 5);
and the step running at all on the field laptop. The bench steps are on
issue #177. Until they are recorded in
`docs/reference/bench-verification-5430.md`, the docs call this the design
the measurement points to, not a measured recovery.

### Amendment, 2026-10-04: the resume step checks that data flows and power-cycles a silent receiver once (issue #177)

**The gap.** The maintainer reported the GPS still had issues on waking with
the step installed. The step re-added the device and restarted gpsd only when
gpsd listed none, so a receiver gpsd still listed that stayed silent was left
alone, which is the measured fault, and the one recovery known to work in
every case (the USB power cycle, `authorized` 0 then 1, a 3D fix 74 s after
the wake on the bench) was the operator's by hand. The paragraph above
("The heavier recoveries stay manual") is superseded for the power cycle.

**The change.** The script now watches gpsd's socket (`?WATCH`) for up to 20 s
after the re-add: a `SKY` or `TPV` report for the receiver, or any report with
`mode` of 1 or more, is alive (satellites without a fix are alive; silence is
the fault). Only on silence it power-cycles that receiver once: it walks up
from `/sys/class/tty/<tty>/device` to the first directory with `idVendor` and
`idProduct` whose child is the tty's own interface (a hub is never taken for
the receiver), passes the path through a lexical guard of `power.py`'s kind
(under `/sys/devices/`, no `..`, a USB address, the leaf `authorized`
exactly), writes `0`, waits 3 s, writes `1`, waits up to 10 s for the tty,
`gpsdctl add` if gpsd does not list it within 5 s, and checks for data once
more. With no `authorized` file it falls back to `try-restart`. The unit
exits 0 only when data was seen; otherwise 1, its last line naming `hammunition
hardware park gps-receiver` and `wake`. Never a loop.

**Why park/wake is no longer only the operator's.** The disclosed cost is a
lost warm start, the cost is paid only when the receiver is already silent,
and the unit that runs unattended after every resume is the only thing in a
position to do it before the operator sits down to a dead GPS. It writes the
same file `hardware park`/`wake` write and no other, and the plan discloses
it. **Not measured on hardware:** the maintainer suspends the field laptop
and reads the journal.

### Amendment, 2026-10-04 (later): the step keeps its last run's lines in `/run`, and two read-only commands read them (issues #177, #310)

The maintainer's account is not in `systemd-journal`, so the step's journal
lines were out of reach of the person who needed them. The script now also
writes every line to `/run/hammunition/gps-resume.log` (0644, replaced at the
start of each run, a symlink never followed, a log that cannot be written
never stops the run). `hardware apply` adds a **third** root file for it,
`/etc/tmpfiles.d/hammunition-gps-resume.conf` (one `d /run/hammunition 0755
root root -` line, made now by `systemd-tmpfiles --create`), disclosed whole
in the plan like the other two, written by the same mechanism D-069 uses for
`/run/hammunition-gps`, and taken back by `hardware unapply` together with the
log and the directory when it is empty. A machine applied before this shows
the step as `stale` in `doctor` until `hardware apply` is re-run.

`hammunition hardware gps-resume-report` (a `gps-resume-report` document,
D-059) compares the three installed files byte for byte with what this engine
would write, reads `systemctl show` of the unit, asks gpsd `?DEVICES;` and
watches `?WATCH` with the script's own functions (imported, not copied), reads
the receiver's USB facts as the step finds them, and prints the log. `hammunition time
measure --minutes N [--pps]` samples `ntpq -pn` and optionally runs `ppstest`
(#310). Both are read-only: **no write, no keying, no power change**; tests
hold that. Neither is a bench result: the maintainer runs them
(`docs/reference/bench-verification-5430.md`, session 14).

### What is refused, and what is out of scope

A target whose time daemon is not ntpsec (Debian 13, Ubuntu and Kali
default to `systemd-timesyncd`, which cannot read a refclock) is refused by
name with the gap stated; switching daemons is a D-022 question for later,
never a silent swap. Accuracy is NMEA over USB, tens of milliseconds, fine
for FT8 and logs and not for lab timing; no PPS line exists on the fitted
receiver. With the network down and a GPS mode set the clock follows one
source, and a spoofed or faulty receiver could move it; online, ntpsec
weighs it against the network. `ntp-only` never trusts the GPS and
`gps-only` never trusts the network. PPS, chrony, timesyncd targets and
serving time to the LAN are out of scope.

### The tray

`hammunition-tray` gains a Time section (its 0.4.0, corrected 2026-10-01 --
first written here as 0.3.0 before the section shipped) that reads through
`hammunition-devctl time state` without `pkexec` and changes the mode only
through `pkexec hammunition-devctl time mode MODE`, from a fixed list of the
four. It is built in its own repository, and the catalog's
`hammunition-tray` manifest was re-pinned to it once released.

### Not yet measured

All not yet measured, on the field laptop, steps in the plan's Task 9: that the grants let
ntpd reach `SHM(0)` at all; which source `auto` follows with the network up,
and so whether it may be described as preferring the network or only as
"the daemon chooses between them"; whether the ntp.d `tos` override works;
the `time1` offset; the parked decay without a restart; each mode's
`ntpq -pn`; an hour of holdover's drift; the DHCP case; and `dpkg --verify
ntpsec` clean after `unapply`. Until each is recorded, nothing in the docs
claims it.

## D-059 — The engine has a machine-readable interface: one JSON document per command on stdout, rendered from the same objects as the text; a real install is never driven through JSON; and `hammunition` is put on the PATH

**Date:** 2026-09-28. **Status:** accepted (maintainer, 2026-09-28: option A
of the console design, front ends are separate projects driving the engine
through a stable interface). **Spec:**
`docs/superpowers/specs/2026-09-28-engine-json-interface-design.md`, piece 1
of 4. **Depends on:** D-021 (consent is never answered by a flag), D-053
and D-057 (`update` and `station` print a count of map regions, never their
names), D-056 (the helper's `state` array the tray already reads).

**Why.** The maintainer, 2026-09-28: "we will need to make it easier with
like an ncurses or whatever menu … the whole point of the project is to
make it easier. And the installer should be able to track the ones already
installed when we open it again to add more." That console
(`hammunition-console`) is its own project, as `hammunition-tray` is, and a
client of the engine, never part of it. It needs to read what the engine
knows. Parsing the text would make every change of wording a break for a
front end, and a second code path producing "the same" data would drift
from what the operator reads.

The second half came from the field laptop: `bootstrap.sh` installed the
engine into the checkout's `.venv` and only suggested
`source .venv/bin/activate`. `hammunition` was not on the PATH, and a short
command copied from the docs failed with "command not found".

### The rule

1. **One flag, one document.** `--json` is accepted before or after the
   verb, on every subcommand; it is added by walking the parser, so a verb
   added later carries it. With it a command prints one JSON document on
   stdout and nothing else there. Under `--json` both `sys.stdout` and
   `sys.stderr` point at a recording tee over the real stderr, so a note, a
   warning or a stray line of text is a diagnostic, never a second thing on
   stdout. The tee answers "not a terminal", so a `--json` run never
   prompts. The exit code is the text run's.
2. **A run that refuses still prints a document.** Its own kind when it got
   far enough to have one (a refused plan is a `plan` with
   `outcome: "refused"`, every blocker, exit 2), otherwise an `error`
   document carrying the exit code and everything written to stderr. That
   covers arguments that do not parse, a command with no JSON form (exit 2,
   nothing run), a missing catalog, and an unexpected exception, whose
   traceback goes to stderr. `--help` and `--version` print to stderr and
   emit no document.
3. **The envelope.** Every document is
   `{"schema": "hammunition/1", "kind": ..., "engine": ...}` plus the
   fields of one dataclass under `src/hammunition/interface/`. `schema`
   versions the whole interface: a field may be added within a major
   version; removing one or changing what it means bumps the major, and a
   front end refuses a major it does not know, by name. The published
   schema forbids a field it does not name.
4. **The text and the document render from the same object.** Each command
   builds one dataclass instance, and both the text renderer and the JSON
   encoder read it. Every text refactor this needed was held byte for byte
   by a golden captured from the code before it moved. A shared check,
   `tests/json_support.py`, asserts per command that every value the text
   shows is in the document; it was made to fail on purpose before it was
   trusted, and it splits tokens at `@` so `NAME@ADDRESS` rows are checked
   as the two values they are.
5. **A real install is never driven through JSON.** `install` and
   `uninstall` accept `--json` only with `--dry-run`, and the document is
   the plan. Without `--dry-run` the run is refused with an `error`
   document and nothing runs. A front end runs the ordinary command in the
   operator's terminal, where sudo, every consent gate (D-021) and every
   disclosure are the CLI's own, then reads `status --json` again.
6. **Privacy.** `station show --json` carries the station values
   themselves, the callsign, grid square, node alias and every map region
   by name, because a local front end needs them to fill in a form. `plan`
   carries exactly what the text plan prints: the operator's account, paths
   in their home, and the map regions. It never carries a rendered
   configuration file, so the callsign in one is not in it (the spec's §6
   was amended to match: less leaves the machine by accident). Both are for
   local programs, not for pasting into an issue, and
   `docs/reference/json-interface.md` and `docs/reference/cli.md` say so.
   `doctor --json` and `update --json` keep the count-only rule their text
   follows; a test runs `update` end to end with regions installed and
   asserts that neither form names one.
7. **The reference is generated.** `scripts/gen_json_reference.py` finds
   every document class by its `KIND`, not from a list, and writes
   `docs/reference/json-interface.md`: the commands that have a JSON form
   (from the `@envelope.json_capable()` registry), each kind's fields with
   their descriptions, and the JSON Schema pydantic derives from the same
   class. Every golden document validates against that schema, and
   `tests/test_docs_generated.py` runs the generator's `--check`.
8. **No abbreviated flags.** `allow_abbrev` is off on every parser, text
   runs included: `--js` is not `--json` and `--dry` is not `--dry-run`. A
   CLI that guards a real install and every consent gate behind an exact
   flag does not guess.
9. **`hammunition` on the PATH.** `bootstrap.sh` runs
   `scripts/path-link.sh`, which links `~/.local/bin/hammunition` to the
   checkout's `.venv/bin/hammunition`. It prints each change before making
   it, creates `~/.local/bin` (mode 0755) only when it is absent, judges a
   link by what it says and never follows it, and never edits a shell rc
   file: when `~/.local/bin` is not on the PATH it prints the line to add.
   It never replaces a file, or a link, it did not make. A link to another
   checkout is also left alone, so two worktrees do not fight over the PATH,
   and the one `ln -sfn` command that switches it is printed. The only link
   it replaces is its own whose checkout is gone. `doctor` gains a
   *hammunition* check: on the PATH, and resolving to this checkout; its fix
   never offers to overwrite what the link script would refuse to.
10. **The docs say `hammunition …`.** Every current example runs the
    engine by that bare name, and a test keeps it so. The pages a reader
    meets first say what to run when the shell says "command not found":
    the checkout's `.venv/bin/hammunition` by its full path. Historical
    records (this file, bench and campaign pages, the changelog) keep what
    was typed at the time.

### What landed

| Command | Document `kind` |
|---|---|
| `status` | `status`: target, catalog, the log, every unit a transaction here named |
| `list` | `catalog`: every profile and package, and what resolves here |
| `show NAME` | `profile`; under `--json` only, a unit's name gives `unit`, its manifest |
| `install … --dry-run` | `plan`, with `install` filled in |
| `uninstall … --dry-run` | `plan`, with `removal` filled in |
| `station show` | `station`, values included |
| `hardware state` | `hardware`: the helper's keys, `kept` and `attached` real (D-056 amended) |
| `maps regions [FILTER]` | `regions`: Geofabrik's list, nothing of the operator's |
| `update` | `update`: the same rows and counts, regions counted |
| `doctor` | `doctor`: each check, severity, detail and fix; D-060's *desktops* check included with no per-check code |

Eleven kinds with `error`. The plan's map section (D-057) and the desktops
read from session files (D-060, merged from `main` during this work) are in
the plan document, and `render_plan` is now a wrapper over the view; the
text of both plan fixtures was compared with `main`'s own renderer and is
byte identical.

### What changed in the text, and why

The spec kept every command's text as it was. Five things moved:

- `status` with no catalog no longer prints its `Target:` and
  `Debian family:` lines before the error. Every successful run is byte
  identical.
- `osm-regions` and `osm-navit` read `already installed` in the plan's
  package list when the *Map regions* section says there is nothing to
  fetch or convert, where they read `will fetch+install` or
  `will convert` before. A region that could not be checked (*kept*) keeps
  the old wording, because it is not known to be current. That last
  choice is open to the maintainer: nothing is fetched for a kept region
  either.
- Abbreviated long options are refused (rule 8).
- `usage:` and `--help` show the global `[--json]` flag, on every verb.
- `doctor` gains its *hammunition* check (rule 9), which also moves the
  summary counts and the Ready line. On a fresh account whose
  `~/.local/bin` is linked but not yet on the PATH, its fix is to log out
  and back in, not to re-run bootstrap.

### Rulings made on the way

- `show NAME --json` accepts a package name while the text `show` refuses
  one: accepted, JSON only, documented, because the spec forbids changing
  the text.
- `status --json` lists recorded units, not requested profiles. The
  transaction log format does not change here; a front end derives profile
  membership from `list --json`. Recording profiles is a separate,
  log-versioned change if the console needs it.
- The plan's Task 10 (`hardware state`'s `kept` and `attached`) was done
  inside the `station`/`hardware` task, because the kept-off work (D-056
  amended) had already reached `main`.

### What has run

The test suite: a golden per command against a fixture catalog and a fake
target, schema validation of each, one-document-on-stdout on every refusal
path, the link script against a scratch home (created, idempotent, every
refusal, mode 0755 under umask 077, rc files untouched), and `doctor`'s
four shadow cases. `doctor --json` was run by hand against a scratch home
for each. No run on the field laptop or a VM is recorded yet.

**Rejected.** Parsing the text in the console: every wording change would
break it. A separate JSON code path: it would drift from what the operator
reads. Driving a real install through JSON with consent passed in: that is
the flag D-021 says cannot answer a gate. A network API: the interface is a
local process's stdout. Editing `~/.profile` from bootstrap: a tool that
augments a system does not rewrite a person's shell setup; it prints the
line.

**Consequences.** A new command gets a document by adding a module to
`src/hammunition/interface/` and `@envelope.json_capable()` to its
function; the reference page, the `--json` flag and the page's command list
follow with no list to edit. `docs/reference/cli.md` names each command's
document in its own section, and `tests/test_docs_json_interface.py` fails
when one is missing. Files: `src/hammunition/interface/` (`envelope.py`,
`text.py`, one module per command), `src/hammunition/cli/main.py`,
`src/hammunition/doctor.py`, `scripts/gen_json_reference.py`,
`scripts/path-link.sh`, `bootstrap.sh`; tests `tests/test_json_*.py`,
`tests/test_path_link.py`, `tests/test_doctor.py`,
`tests/test_docs_json_interface.py`, goldens under `tests/fixtures/json/`.

### Amendment (2026-10-04): the console ships with the engine (#302)

The terminal console (`hammunition-console`) moved into this repository as
`hammunition console`. It is the one front end that makes no sense on its own: it
has no install logic, no package names and no catalog parser, and does nothing
the engine's `--json` documents and commands do not. Its separate repository
produced the failure the maintainer hit on 2026-10-04: a version floor
(`ENGINE_FLOOR`), a stale version stamp on the engine's virtualenv, and a refusal
where an update was wanted. There is now one release and one version, so the
floor check, the `EngineTooOld` refusal and the fixtures' `engine` field it read
are gone; the `engine` field of a document is shown, never compared. The console
still drives the engine as a subprocess through the documents (the interface
this decision created is unchanged, and the console imports nothing of the
engine's but one launcher, `hammunition.console.launch.engine_argv()`, which
runs the interpreter that is running it with `-m hammunition`, so a checkout's
virtualenv, the bootstrap-linked `~/.local/bin/hammunition` and a packaged
install each drive their own engine). urwid is an optional extra
(`hammunition[console]`; `python3-urwid` on the Debian family), imported only
when the screen is drawn: `hammunition` itself never needs it, and without it
`hammunition console` prints one line naming the command and exits 2. The
standalone unit is retired (its install block stays so an uninstall can find
what v0.1.0 placed); `menus apply` writes the engine's own entry for
`hammunition console`. `tests/console/capture_fixtures.py` refuses to run unless
the HOME the engine would see is a temporary directory it created itself (console
issue #5, the 2026-10-03 incident). Tray, Hill, Bunker and the GPS tether stay
separate: each runs without the engine or serves other clients, which is the
test for a separate project. The console's design record is
`docs/superpowers/specs/2026-10-03-console-design.md`, amended the same day.

## D-060 — A unit may be for particular desktops: read from the session files, deferred from a profile on a machine with none of them, refused by name; Plasma first, Xfce and LXQt welcomed

**Date:** 2026-09-28. **Status:** accepted (maintainer, 2026-09-28, on the
design in `docs/superpowers/specs/2026-09-28-desktops-design.md`: "work
through 1-3"; the VM runs come later, on his word). **Depends on:** D-039
(a profile member the machine cannot use is deferred by name, a typed name
refuses), D-036 and D-050 (menus per mechanism, per desktop), D-056 (the
tray is a client of one helper), D-031 (verify the effect). **Amends:**
D-039's list of deferrable reasons, with one more fact about the machine.

**Why.** The maintainer, 2026-09-28: "Do we have Xfce and lxde support
built in? Maybe we should add those in so we have lower powered systems be
able to use this project … for simplicity while we test against those we
are Parrot OS and KDE first … but we want to welcome more environments."
Read against the engine, almost nothing depends on the desktop. Three
things do: the menu (per mechanism, D-036/D-050), the tray (a Plasma applet
only, D-056), and `station`, which included `hammunition-tray`
unconditionally. That applet's `.deb` depends on `plasma-workspace`, so on
Xubuntu or Lubuntu `station` pulled the whole Plasma shell onto the machine
chosen for being light. That was a defect, and this decision fixes it.

**Lubuntu is LXQt, not LXDE**, and has been since 18.10. Testing Lubuntu
tests LXQt. Plain LXDE is still in Debian (`openbox-lxde-session`); no
current Ubuntu flavour ships it.

### What was measured

Which desktops a machine has is readable from the session files every
display manager lists, `/usr/share/xsessions/*.desktop` and
`/usr/share/wayland-sessions/*.desktop`. They are files, so they survive
`sudo`, which `XDG_CURRENT_DESKTOP` does not. Read from the Debian 13
packages (`apt-get download`, then `dpkg-deb -x`, 2026-09-28) and from the
field laptop:

| Session file | Package | `DesktopNames=` |
|---|---|---|
| `plasma.desktop`, `plasmax11.desktop` | plasma-workspace | `KDE` (field laptop) |
| `xfce.desktop`, `xfce-wayland.desktop` | xfce4-session | `XFCE` |
| `lxqt.desktop` | lxqt-session | `LXQt` |
| `mate.desktop` | mate-session-manager | `MATE` |
| `gnome.desktop`, `gnome-wayland.desktop` | gnome-session(-xsession) | `GNOME` |
| `LXDE.desktop` | openbox-lxde-session | **none** (Exec `/usr/bin/startlxde`) |
| `cinnamon.desktop`, `cinnamon2d.desktop`, `cinnamon-wayland.desktop` | cinnamon-common | **none** |

The menu roots the four newly listed desktops ship were read the same way:
`lxqt-menu-data` ships `lxqt-applications.menu`, `lxmenu-data`
`lxde-applications.menu`, `mate-menus` `mate-applications.menu` (plus an
explicit `applications-merged` merge directory), `cinnamon-common`
`cinnamon-applications.menu`, and all four carry `<DefaultMergeDirs/>`.
That is which file the spec says each reads; what each panel draws is
unmeasured.

### The rule

1. **Detection.** `src/hammunition/desktop.py` reads `DesktopNames` first,
   `;`-separated and case-insensitive (`GNOME;GNOME-Classic`: any element
   recognised counts). A file without the key falls back to a table keyed
   by its filename stem, holding exactly the two measured gaps: `LXDE` →
   lxde; `cinnamon`, `cinnamon2d`, `cinnamon-wayland` → cinnamon. Anything
   else without the key (`lightdm-xsession`, `openbox`) is ignored rather
   than guessed at. `/usr/local/share/xsessions` and
   `/usr/local/share/wayland-sessions` are read too, since SDDM and LightDM
   search them. Only regular files are read, after following a symlink,
   and at most 64 KiB each, with a UTF-8 BOM stripped: a FIFO or device
   node named `*.desktop` would otherwise block the planner. A missing
   directory is an empty set: a container or a server has none. A file
   read that names no desktop listed here (COSMIC, Sway, Budgie) is
   reported as **read but unrecognised**, never dropped, so a graphical
   machine running one is not described as a server. `XDG_CURRENT_DESKTOP` is read only where the session
   matters (menus, `doctor`), **never by the planner**.
2. **A manifest may name its desktops.** `desktops:` is a non-empty list
   of `kde`, `gnome`, `xfce`, `lxqt`, `lxde`, `mate`, `cinnamon` with no
   duplicates; omitted means any desktop, which is every unit but one
   today. `desktop_alternative:` optionally names the unit that does the
   same job elsewhere; the catalog refuses to load unless that unit exists
   and its `desktops` share none with this one's.
3. **The plan decides from what it is handed.** `resolve()` takes the
   installed desktops as an argument; the CLI passes what the session
   files say. A unit whose `desktops` shares nothing with them is, as a
   **profile member, deferred by name** with the reason `for KDE Plasma;
   this machine has no KDE Plasma session (it has: Xfce)` (or `(it has no
   session files)`, or `(its session files name none the catalog knows:
   cosmic.desktop)`) and the rest of the profile installs; **typed by name, it is
   refused** with that reason and a remedy that names the alternative when
   one serves a desktop the machine has. Both follow D-039: logged, shown
   by `status`, a dependent deferring with it, a profile of nothing but
   deferrals refused. A dependent deferred through such a unit, and a
   profile whose members are all deferred this way, give the desktop as
   the cause and not D-039's "this target does not offer", which would be
   false. Desktops that were not read (a caller passing none)
   are disclosed as a note and the unit plans, the way an unreadable
   kernel is.
4. **The dry run shows it.** Whenever a unit in the request declares
   `desktops`, the plan prints *Desktops read from session files* with
   what they offered and any file read that named none, so the decision is
   visible before anything runs. `doctor` reports the same, and the
   current session's desktop.
5. **Installing a desktop later needs nothing special.** Re-running
   `hammunition install station` picks the unit up; that is the whole
   story, because install is idempotent.

### Order and scope

Parrot OS with KDE Plasma stays first: it is the field laptop, and it is
where things are measured. Xfce (Xubuntu 26.04) and LXQt (Lubuntu 26.04)
are next; their checklist is `docs/reference/vm-campaign-desktops.md`,
**not yet run**. `docs/desktops.md` is the user-facing
account, and says *unmeasured* wherever nothing has run.

### D-050 amendment (2026-09-28): four more rows for the per-desktop table

D-050 item 8's table is left as it was accepted. These rows extend it,
dated here rather than written into it. The menu root each desktop ships
was read from the Debian 13 package on 2026-09-28; nothing has been
rendered on any of them.

| Desktop | Mechanism | State |
|---|---|---|
| LXQt (Lubuntu) | menu-spec merge into `lxqt-applications-merged/`; `lxqt-applications.menu` carries `<DefaultMergeDirs/>` | **Unmeasured.** The Lubuntu 26.04 run is its checklist |
| LXDE | menu-spec merge into `lxde-applications-merged/`; `lxde-applications.menu` carries `<DefaultMergeDirs/>` | **Unmeasured** |
| MATE | menu-spec merge into `mate-applications-merged/`; Parrot carries a `mate-` root | **Unmeasured** |
| Cinnamon (Linux Mint) | menu-spec merge into `cinnamon-applications-merged/`; `cinnamon-applications.menu` carries `<DefaultMergeDirs/>` | **Unmeasured** |

### The machine that already ran `station`

A machine that ran `station` at v0.10.0 or earlier got the applet and
`plasma-workspace` with it, and `plasma-workspace` ships `plasma.desktop`.
Detection now reads KDE Plasma as installed there, which is true of the
disk, and the applet stays planned. The engine does not remove a desktop
on a guess about why it is there. `docs/desktops.md` gives the operator the
two commands (`hammunition uninstall hammunition-tray`, then `apt
autoremove`) and says to read the list before agreeing.

### The Qt tray

`hammunition-tray-qt` is the same switch for the other panels: a PyQt6
`QSystemTrayIcon`, a second binary package from the tray's own repository,
released with the applet as hammunition-tray v0.3.0 (2026-09-28) and pinned
here from that release's `SHA256SUMS` (13784 bytes). It lists `xfce`,
`lxqt`, `lxde`, `mate` and `cinnamon`. Its autostart entry carries
`NotShowIn=KDE;`, so a machine with Plasma and another desktop shows one tray
in each. The two units name each other in `desktop_alternative`, so a
refusal names the one to install instead. Both front ends run
`/usr/bin/pkexec` by its absolute path, and both say when no polkit
authentication agent is running. The same release fixed a bug the second
front end exposed: the applet cleared a failed park's error on the poll that
follows every action. The release workflow installed and removed both
packages on Parrot; **neither has run on the desktops the Qt tray lists**,
and `docs/reference/vm-campaign-desktops.md` is that check.

**Rejected.** Reading `XDG_CURRENT_DESKTOP` in the planner: `sudo` drops
it, and the plan would answer differently under `sudo` than in the dry
run. Wider filename matching for files without `DesktopNames`: the two
packages measured are the whole of the gap, and a pattern would tell a
machine it has a desktop it does not. Removing `hammunition-tray` from
`station`: Plasma is the first desktop, and the unit is right there.

**Consequences.** `src/hammunition/desktop.py`; `desktops` and
`desktop_alternative` in `src/hammunition/manifest/schema.py`, the
cross-manifest check in `src/hammunition/manifest/load.py`; the deferral,
refusal and `desktops_read` in `src/hammunition/plan.py`; the *desktops*
check in `src/hammunition/doctor.py`; `desktops: [kde]` on
`catalog/packages/hammunition-tray.yaml`. Tests:
`tests/test_desktop.py`, `tests/test_desktop_units.py`, and the two
`station`/`hammunition-tray` cases in `tests/test_cli.py`, which set the
desktops explicitly so they answer the same on a desktop and in a
container.

## D-061 — Trails, terrain and routing on foot: QMapShack over Garmin maps and one Routino database built from the station's regions, and Copernicus elevation checked tile by tile

**Date:** 2026-09-28. **Status:** accepted (maintainer, 2026-09-28, "sounds
good go for it", on `docs/superpowers/specs/2026-09-28-hiking-maps-design.md`),
with one departure from that specification, measured and recorded in its
own dated amendment: the tiles follow each region's outline, not its file's
bounding box (below). **Depends on:** D-057 (regions are station data;
derived data by a converter enum; MD5 disclosed, never silent), D-049
(offline data is a catalog unit), D-035 and D-039 (a missing station value
defers by name), D-031 (verify the effect), D-043 (the operator owns what is
built on their behalf), D-021 (state the licence), D-059 (the launchers run
`hammunition` from the `PATH`). **Amends:** D-057's converter enum, with
three members (`mkgmap`, `routino-planetsplitter`, `gdal-dem`), and D-049's
install methods, with `dem-tiles`.

**Why.** The maintainer, 2026-09-28, after Navit ran on the field laptop:
"need maps for trails, and such too. Think survival and EMCOMM." Navit is a
road navigator. It has no terrain and draws trails as an afterthought. A
search, a hike to a repeater site or a deployment off the road network
needs trails, contours, a route on foot and the position on one map.

### What is carried, and what was measured and left out

All of it comes from the archive and none of it is built from source:
QMapShack (Parrot echo 1.17.1; 1.21.1 in echo-backports, which D-038 takes
where the machine already installs from there), Routino 3.4.3, GDAL 3.10.3,
mkgmap r4923 with mkgmap-splitter r654, and socat (retired 2026-09-29 by
the amendment below). Viking, Marble, GPSPrune
and JOSM were measured and are not carried. Debian's Viking links no Mapnik,
so it renders nothing offline, and it routes only through web services.
Marble's offline place index has no packaged builder. GPSPrune and JOSM use
online tiles.

| Unit | What | Where |
|---|---|---|
| `osm-garmin` | converter `mkgmap`: mkgmap-splitter, then mkgmap `--style=default --route --add-pois-to-areas --unicode --gmapsupp` | `data/osm-garmin/<slug>.img` |
| `osm-routino` | converter `routino-planetsplitter`: `--parse-only` per region (`--append` after the first), then one `--process-only`, prefix `hammunition` | `data/osm-routino/hammunition-*.mem` |
| `dem-copernicus` | method `dem-tiles`, provider `copernicus-glo30` | `data/dem-copernicus/<tile>.tif` |
| `dem-qmapshack` | converter `gdal-dem`: per tile `gdal_contour -i 20`, then `gdal_rasterize -ts 7200 7200`; `gdalbuildvrt` over the tiles and over the contour rasters | `data/dem-qmapshack/{dem,contours}/` |

The `navigation` profile gains the ten units: the six programs and the
four data units. Piece 1's units are unchanged. With no regions set, the
four data units are deferred by name with the rest of the map data, and the
programs still install.

`--index` and `--housenumbers` are not passed to mkgmap. They need its
upstream bounds files, which have no published checksum, and QMapShack does
not read that index.

### One Routino database over every region, installed whole

A route can cross from one region into the next only inside one database;
QMapShack's own help says Routino cannot route across two. The trade is
stated in the plan. A region that fails to parse fails the database, not
only itself, and the step names the region and why. The four `.mem` files
are one database, so they are published under temporary names and only
renamed into place once all four verify. A publish that fails partway
leaves the installed database and its record (`hammunition.source`, the
regions and snapshots it was built from) exactly as they were. A rename
failing after that removes the database and its record rather than leave
QMapShack a mixed set, and the next run rebuilds it.

### Tiles follow each region's outline

A tile is one 1°×1° square named by its south-west corner. Which squares
have a tile is the bucket's `tileList.txt`, carried as
`catalog/data/copernicus-glo30-tiles.txt` (26,450 names, generated). A
square not in it has **no published tile**, and the plan needs no network to
know that. It does not know why. Such a square is ocean, or land the
publisher withholds from the public 30 m release: measured against the
carried list at the final review, the squares of Yerevan and Baku are absent
while their neighbours to the north and south are listed. The list cannot
tell the two apart, and Hammunition never guesses, so the plan counts these
squares as "square(s) with no published tile (sea, or land Copernicus does
not release)" and never calls them sea. An empty or comment-only list is an
error naming the file, never "every square is unpublished", because a
truncated file would otherwise take a region's terrain away with no warning.

A region whose outline touches squares, none of them published, gets no
terrain, and that is never silent. The plan prints **"warning: no terrain
available for `<region>` from Copernicus GLO-30; its maps still install"**,
nothing is fetched for it, and the step that records the region names it
the same way. The transaction does not fail over it: the operator asked for
the region's maps, not its terrain, and the maps install. The record's
header is `# squares with no published tile: N`; a record written with the
earlier `# sea squares: N` still reads as the same count, so it is not
rewritten. `update` counts such regions, never naming one.

The specification chose each region's squares from the bounding box in its
`.osm.pbf` header. That was measured before the build on the two regions
installed on the development host. For one of them the header box spans
hundreds of squares where the region's Geofabrik outline touches tens, and
it does not even contain the outline's own box. The header box also does
not exist before a region's first download, and that is when the plan must
print the tiles. So the squares are the ones the outline touches, edge or
interior: `<region>.poly`, the file Geofabrik cut the extract with. The
outline is fetched through the same probe that resolves the regions, and the
answer is recorded in `data/dem-copernicus/<slug>.tiles` when the terrain
installs. Until that first install, every plan fetches the outline again, a
dry run included, and the plan says so. It is not cached at plan time,
because a plan run under `sudo` would leave a root-owned file in the
operator's cache.

The antimeridian refusal applies only to an outline edge that jumps across
±180 as a single segment, and no Geofabrik outline measured does that.
Measured on 2026-09-28 through `parse_poly` and `squares_touching` on the
live outlines: Geofabrik writes Alaska, Fiji, New Zealand and Russia's far
east as separate rings that stop at ±180, and all four select their tiles
with zero refusals (Alaska: 449 tiles, about 17.5 GB). An edge that does
jump would still be refused by name, not unwrapped on a guess.

A region's terrain is tens to hundreds of tiles, at about 39 MB a tile.
Measured the same day over `gen_geofabrik_pins.REGIONS`: a contiguous US
state can exceed 90 tiles (about 3.6 GB), Alaska is 449 tiles (about
17.5 GB), and the 50 states and DC together touch 1,447 tiles. The plan
prints each region's tile count and download size before anything is
fetched, and the disk check counts them.

### Every tile is verified in one of D-057's two modes, and the plan names which

A tile with a row in `catalog/data/copernicus-glo30-pins.yaml` is checked
against the sha256 Hammunition measured, and the plan says **"sha256, pinned
by Hammunition"**. Any other tile is checked against the MD5 in the object's
S3 ETag, and the plan says **"MD5 from the publisher's object metadata; not
pinned by Hammunition"**, tile by tile. A single-part upload's ETag is its
MD5, measured equal to `md5sum` on one tile. A multipart ETag is not an MD5,
and such a tile is refused by name. A pinned tile is still asked for with a
`HEAD` at plan time, so an unreachable bucket refuses the plan before apt
runs rather than halfway through the transaction.

**The pin file ships empty** (`pins: []`). Until the maintainer grows it,
every tile gets the MD5 wording. Each pin costs a 39 MB download, and
`scripts/gen_copernicus_pins.py --max-tiles N` pins the next N tiles of one
fixed order: the 1,447 tiles that the outlines of the 50 US states and DC
touch, state by state (counted on 2026-09-28, when one tile was also pinned
live and its body's MD5 matched its ETag; that pin was not kept). The pin
file is always a prefix of that order, whoever runs the generator, so it
never says whose region was pinned first, and the generator has no
`--region`. The weekly job diffs the carried list against the bucket's and
`HEAD`s every pin for its size and ETag.

Each tile is fetched into the shared cache, verified, installed and
re-verified on the way in, and its cached copy is then deleted: a tile never
changes, and a second copy of tens to hundreds of tiles is gigabytes. A tile no region
needs any more is removed.

### Converters run as the operator, one lock per build, and fail into their own ledger

Every converter runs in a working directory under the operator's
`~/.cache/hammunition/build/<unit>/`, through one `Staging` object
(`src/hammunition/backends/staging.py`). Under `sudo`, every staging-side
operation is a process dropped to the operator, with the operator's minimal
environment (`PATH`, `HOME`, `USER`, `LOGNAME`, the locale, and the
converter's own variables), never root's. The staged output is published
into the prefix only if it still hashes to what the operator's process
measured. That was piece 1's rule for maptool, and it is now one class. The
same review found piece 1's maptool children inheriting root's environment,
and they now get the same minimal one.

The working directory is fixed per region (`<slug>.work`) or per build
(`routino.work`, `dem.work`) and is held under one `flock` for every phase
of that build. Scratch is cleared only under that lock, by the operator.
Root never removes a working directory, and a run refused because another
holds the lock removes nothing. The lock is there for the reason D-057's
amendment found: maptool writes fixed-name temp files, and two runs in one
directory crashed.

Outputs are checked, not exit statuses (D-031). mkgmap exits 0 while it
logs SEVERE, and `gdal_contour` exits 0 appending to an existing file, so
the working directory is emptied before every tile. Debian's `mkgmap`
wrapper ignores `JAVA_OPTS`, which the splitter's honours, so mkgmap's
6000 MB heap arrives through `JAVA_TOOL_OPTIONS` and the splitter's 4000 MB
through `JAVA_OPTS`. Each output carries a `.source` sidecar naming its
input and its converter's version (`mkgmap 1`, `gdal-dem 1`), so a changed
converter rebuilds, as `navit-maptool 2` does. `gdal_rasterize -ts 7200
7200` draws each tile's contours at about 5.4 MB, measured.

A failure is recorded in a terrain ledger separate from piece 1's: a region
whose Garmin map did not build is still a region Navit converts. The run's
last step fails it by name if anything terrain did not install.

### QMapShack is told where the maps are, and nothing else changes

`hammunition maps qmapshack` is what the `qmapshack-offline` launcher runs.
It adds Hammunition's directories to QMapShack's own settings,
`~/.config/QLandkarte/QMapShack.conf`, if they are absent. The keys are
`mapPath` and `demPaths` under `[General]` and `routino\paths` under
`[Route]`, read from QMapShack 1.17.1's binary. (Amended 2026-09-29, below:
the first two belong under `[Canvas]`.) It keeps every existing
value in its place and touches nothing else, byte for byte. A value of
`@Invalid()` is how Qt writes an empty list, so it reads as empty and is
replaced. Any other `@`-typed or quoted value in those keys is refused, and
so is a line that is neither a section nor `key=value`, or a symbolic link
in the file's place. A refusal changes nothing and does not start
QMapShack. A new file is created 0600. The command refuses root, whose
settings are not the operator's. It is per-user and unprivileged, like the
menu files (D-050). A refusal from a menu click is not visible, so the
guide tells the operator to run the launcher in a terminal to read it.

QMapShack stops at startup when `/usr/share/routino/translations.xml` is
missing. apt supplies it, and `doctor` gains a *qmapshack* warning naming
it for the case where it is gone.

### The GPS tether is loopback only

QMapShack has no gpsd client; its GPS Tether reads NMEA over TCP.
`hammunition maps gps-tether`, the `gps-tether` launcher, runs `gpspipe -r`
behind `socat` listening on 127.0.0.1 port 10110 only, one client at a
time, and only while the operator runs it. (Amended 2026-09-29, below: the
engine now writes the NMEA from gpsd's JSON itself, and `socat` is retired.) A position is where the operator
is, so it is never served to the network, and nothing is installed as a
service. The listening socket was measured on loopback only by
`tests/test_gps_tether.py`, which runs the real `socat` where it is
installed.

### The plan, disk and privacy

The plan's *Map regions* section gains a *Terrain* block. It shows tiles per
region, how many squares have no published tile and what the region's tiles cost to
download, each tile to fetch with its size and verification, and what is
built for QMapShack with its estimate. Each factor is printed with
"measured on one region":

- Garmin map: 0.85× the download, with 3× scratch.
- Routino database: 0.67× all the downloads together, with 6× scratch
  (5.02× sampled at the peak once a second, rounded up).
- Contours: about 5.5 MB a tile, with up to 98 MB of scratch at a time.

The disk check counts piece 1 and piece 2 together and refuses before
anything is fetched. Tile names encode latitude and longitude, so they are
printed in the plan only. `update` prints how many tiles are installed and
nothing else, and names `osm-garmin` and `osm-routino` in its command when
`osm-regions` is behind. The tests use synthetic outlines and tile names,
and no maintainer region, tile, size or digest is recorded in the
repository: figures from his regions appear here as ratios and as "tens"
or "hundreds".

### Gaps, documented and carried

- **Offline address search is Navit's.** QMapShack's search is online only,
  and Routino takes coordinates. Find the address in Navit, then walk it in
  QMapShack.
- **Trail difficulty is not routed on.** Routino's foot profile ignores
  `sac_scale`.
- **No hiking cartography.** No hiking style or TYP file is in the archive.
  OpenTopoMap's and Freizeitkarte's are unmeasured upstream projects.
- **The Garmin address index is off**, because its bounds files are not
  pinned.
- **Labelled contours inside the map** need a DEM-to-OSM tool the archive
  lacks (`pyhgtmap` on PyPI is the candidate). The raster overlay is legible,
  not pretty.
- **SRTM is refused** (it needs an Earthdata login). **viewfinderpanoramas
  is not carried** (no checksum, unclear licence). **USGS 3DEP** is the
  alternative provider if anyone asks, and the `provider` enum has room for
  it.
- **BRouter stays out**: its jar is pinnable, and its weekly routing data is
  not. (Amended 2026-09-29 by **D-063**: BRouter is carried, and its
  routing files are built on the machine from the station's own regions and
  tiles; brouter.de's weekly files are still never fetched.)
- **An outline edge that jumps across ±180 as one segment is refused** by
  name. None measured does (above): Alaska, Fiji, New Zealand and Russia's
  far east select their tiles.
- **Land Copernicus does not release at 30 m has no terrain.** The region's
  maps install, and the plan warns by name (above). The route is a second
  provider: Copernicus GLO-90, published separately at 90 m, is the
  candidate, unmeasured here, and the `provider` enum has room for it.

### What is measured, and what is not yet

Measured on the development host on 2026-09-28: every converter's argv and
factor above, on one region or tile; the tile list; one live pin, not kept;
the tether's loopback socket. Not yet run on a desktop, and not claimed
until bench session 12 in `docs/reference/bench-verification-5430.md`
records it:

- QMapShack reading the directories the launcher writes under those keys;
- listing the `hammunition` Routino database;
- drawing hillshade and slope from `dem.vrt`;
- a route on foot across the boundary between two regions;
- the tether feeding QMapShack's GPS Tether from a real receiver;
- the whole install on the field laptop.

The guide's *What has not been measured yet* is the operator's copy of this
list.

**Measured on the field laptop, 2026-09-29 (bench session 12).** The whole
install on two regions, every effect verified, 294 commands. QMapShack
lists both regions' maps, the contour map and the elevation once the lists
are under `[Canvas]` (the amendment below). Hillshade draws, seen at the
3 km and 10 km scales; slope was not tried. The rewritten tether fed
QMapShack's GPS TCP/IP source from the fitted receiver, and "center to
position" moved the map there. Still not measured: slope, and a route on
foot, within a region or across a boundary.

**Amended 2026-09-29 (bench session 12): the Routino database was loaded
and not selected.** QMapShack 1.17.1 read `routino\paths`, loaded the
`hammunition` database (its four `.mem` files were mapped in the process),
and then selected the Database entry at the index in `[Route]
routino\database`, which held `-1`: nothing selected, and routing gave up
without a message. It writes the index back on exit, so the `-1` persisted.
`hammunition maps qmapshack` now sets that key to 0 when it is absent or
negative, and leaves 0 or more, the operator's choice, alone
(`select_database()` in `src/hammunition/qmapshack_config.py`).

**Rejected.** The `.osm.pbf` header box for tile selection (measured wrong,
above). Caching the outline at plan time (root-owned files in the operator's
cache under `sudo`). One Routino database per region (no route across a
boundary). Unwrapping an antimeridian edge on a guess. A `--region` flag on
the pin generator (the pin file would say whose region it was). A systemd
service for the tether (a position served while nobody is using it). Garmin
device export, and online maps of any kind, which are out of scope.

**Consequences.** `DemTilesInstall` and the three converters in
`src/hammunition/manifest/schema.py`; `src/hammunition/copernicus.py`,
`src/hammunition/terrain_plan.py`, `src/hammunition/qmapshack_config.py`,
`src/hammunition/gps_tether.py`; `src/hammunition/backends/staging.py`,
`garmin.py`, `routino.py`, `dem.py`, `gdal_dem.py` and `terrain.py`; the
*Terrain* block in the plan and its JSON form (`TerrainSectionView`,
`docs/reference/json-interface.md`); the tile count in `update`; the
*qmapshack* check in `src/hammunition/doctor.py`; `maps qmapshack` and
`maps gps-tether` in `src/hammunition/cli/main.py`; the generated
`catalog/data/copernicus-glo30-tiles.txt` and
`catalog/data/copernicus-glo30-pins.yaml` and their generator
`scripts/gen_copernicus_pins.py`, checked weekly;
`catalog/packages/qmapshack.yaml`, `catalog/packages/osm-garmin.yaml`,
`catalog/packages/osm-routino.yaml`, `catalog/packages/dem-copernicus.yaml`,
`catalog/packages/dem-qmapshack.yaml` and the programs' manifests;
`catalog/profiles/navigation.yaml`. The operator's page is
`docs/guides/offline-navigation.md` (sections 9 to 11), and the CLI's is
`docs/reference/cli.md`. Tests: `tests/test_copernicus.py`,
`tests/test_terrain_plan.py`, `tests/test_staging.py`,
`tests/test_garmin.py`, `tests/test_routino.py`,
`tests/test_dem_backend.py`, `tests/test_gdal_dem.py`,
`tests/test_terrain.py`, `tests/test_terrain_cli.py`,
`tests/test_terrain_execute.py`, `tests/test_json_plan_terrain.py`,
`tests/test_qmapshack_config.py`, `tests/test_gps_tether.py`,
`tests/test_gen_copernicus_pins.py`, `tests/test_navigation_catalog.py`
and `tests/test_docs_terrain.py`.

### Amendment (2026-09-29): measured on the bench, the settings group was wrong and the tether is rewritten

QMapShack 1.17.1 ran on the field laptop for the first time on 2026-09-29.
The settings group this decision shipped was wrong, and is fixed. The
tether gave QMapShack nothing, for a reason not established, and is
rewritten on its own merits.

**The map and elevation lists belong under `[Canvas]`.** After QMapShack
exited, `~/.config/QLandkarte/QMapShack.conf` held `mapPath=@Invalid()` and
`demPaths=@Invalid()` under `[Canvas]`: that is where QMapShack keeps them,
and it had ignored the same keys the launcher wrote under `[General]`.
`routino\paths` under `[Route]` it kept, so that one was right. The key
names came from the binary; the group was inferred from it, and the
inference was wrong. `hammunition maps qmapshack` now adds
both lists under `[Canvas]`, creating the group if absent. Where an earlier
run left them under `[General]`, it takes out exactly its own directories
from those two keys and removes a key left empty; any other value there and
every other key stay, and a `[General]` value it cannot read is not ours and
is left alone rather than refused. It says on stderr when it moved anything.

**The tether makes its own NMEA.** The shipped tether was `gpspipe -r`, gpsd's
raw NMEA watch, behind `socat`. What was measured on 2026-09-29: with it
connected, `gpspipe -r` printed gpsd's three JSON header lines and then no
NMEA for 12 s, and QMapShack's GPS Tether saw nothing; at the same time
gpsd's JSON watch was sending no `TPV` either (`?POLL` answered
`active: 0`). gpsd had nothing to report, so no cause for the old tether's
silence is established, and `gpspipe(1)` says `-r` emits pseudo-NMEA built
from binary data, so it may well have worked with a fix. The rewrite stands
on its own merits: no `socat` or `gpspipe` dependency, the loopback bind
made and tested in the engine's own code, `RMC` and `GGA` built from the
JSON feed every gpsd client (`xgps`, Navit) uses, and a plain message when
gpsd has no fix, which the old tether could not give.
`hammunition maps gps-tether` now connects to gpsd at 127.0.0.1:2947 itself,
sends `?WATCH={"enable":true,"json":true}`, and for every `TPV` with
`mode` 2 or 3 writes `$GPRMC` then `$GPGGA`: the time, the position as
`ddmm.mmmm` with its hemisphere, speed in knots, track, altitude (from
`altMSL`, else `alt`) on a 3D fix only, fix quality 1 (2 and RMC mode `D`
where gpsd's `status` says DGPS), and the satellites used and HDOP from the
latest `SKY`. Every sentence ends with its XOR checksum and CRLF. A field
gpsd did not give is an empty field, except the time: a TPV with no time,
or one that does not parse, is stamped with the system clock in UTC, since a
reader may drop a sentence without one. It is the standard
library in the engine, and nothing is executed: `socat` and `gpspipe` are no
longer involved.

What stays: 127.0.0.1 port 10110 only; one client at a time, now a second
client closed at once with a line on the terminal rather than left waiting
(amended 2026-09-29, below: any number of clients, and `--gpsd`/`--port`);
only while the operator runs it; Ctrl-C stops it; no `--json` form; the
`gps-tether` launcher. Each client gets its own gpsd watch, opened when it
connects and closed when it goes. The terminal shows a line when a client
comes or goes, when gpsd cannot be reached or closes the connection, and
once when no position with a fix has arrived in 10 s, naming `xgps`. It
refuses root.

`socat` was carried only for the tether, and nothing else in the catalog
uses it, so it leaves the `navigation` profile and `qmapshack`'s
dependencies. Its manifest stays, with status `retired` (`out_of_scope`):
`hammunition uninstall` resolves names against the catalog, and a machine
that installed socat under v0.14.0 must still be able to remove it by name.
`qmapshack` keeps its dependency on `gpsd-clients`, for `xgps`.

Measured: the sentence conversion against checksums computed by hand, both
hemispheres, no fix and missing fields; the server against a fake gpsd on a
random loopback port, bound to 127.0.0.1 only and turning a second client
away (`tests/test_gps_tether.py`, `tests/test_qmapshack_config.py`,
`tests/test_maps_tools.py`). On the development host, against its own gpsd,
which was sending only its header lines at the time, the tether logged the
client and then "no position with a fix". Later the same day, with that
gpsd streaming fixes, the controller's run counted one client served and
every later one turned away as a second. That was not reproduced here, but
the loop had the hazards review found, and they are fixed:
- every socket is registered with the session it belongs to, and an event
  for a session that has ended is dropped;
- in each batch the current session's events come before a new connection;
- a new connection first checks, without reading, whether the current
  client has closed;
- sends are non-blocking, with at most 64 KiB queued, so a client that
  stops reading is dropped rather than holding the loop;
- ending a session closes its gpsd socket.

Each is tested against a fake gpsd: immediate reconnects, a close mid-stream
with the gpsd watch seen closed, a close and a new connection in one batch,
and a client that never reads. Run live through `hammunition maps gps-tether`
on a spare port against that gpsd, 11 raw-socket connections were all
served, with RMC and GGA arriving within about 1 s each time. They were
three reconnects 2 s apart, six immediate ones, one closed mid-stream and
the one after it. Not yet measured, and still bench session 12's to record:
QMapShack reading the lists under `[Canvas]`, and QMapShack's own GPS Tether
taking a position from the tether. (Both measured in bench session 12 the same day;
see *What is measured, and what is not yet* above.)

**Rejected.** Keeping `gpspipe -r` behind `socat` (not shown to fail for
want of a fix, but two external programs where the engine's own code can
bind loopback, be tested, and say when gpsd has no fix). Keeping `socat` in
front of an engine-made stream (a second program for what the standard
library does, with its comma-separated option syntax in the argv).
Leaving the `[General]` keys where they were (harmless to QMapShack, but a
file that says two different things about where the maps are).

### Amendment (2026-09-29): fan-out, `--gpsd` and `--port`

**Amended 2026-09-29: the tether serves any number of clients, reads any
gpsd, and serves any unprivileged port, still on loopback only.** One
client at a time kept a terminal check (`nc`) away while QMapShack was
open, and pinned the tether to the maintainer's own setup.
`--gpsd HOST[:PORT]` (default `127.0.0.1:2947`; IPv6 in brackets, a bare
one refused) reads a gpsd on a Pi, a phone or a shack computer; `--port N`
(default 10110) serves another port, refused below 1024 and above 65535
by name. The bind stays 127.0.0.1 whatever either says: the feed is a
position without authentication, and another machine reaches it through
`ssh -L 10110:127.0.0.1:10110 <laptop>`. Every connected client gets every
sentence. **One gpsd connection is kept open while any client is
connected**, opened for the first and closed when the last leaves, rather
than one per client: every client then gets identical bytes from one
`Feed`, gpsd is watched once, and nothing is held open while nobody
listens, which is the property the per-client watch existed for. A client
with more than 64 KiB unsent is dropped alone. Measured: the tests in
`tests/test_gps_tether.py` and `tests/test_maps_tools.py`, broken on purpose
and seen red first; and live on the development host, two raw clients on a
spare port receiving the same 32 sentences in the same order, through
`--gpsd` as `127.0.0.1` and as `[::1]:2947`. Not measured: a gpsd on
another machine, a phone, a Bluetooth receiver or a rig's GPS; the guide's
section 12 says so for each. **Rejected:** a `--bind` option or any
listener beyond loopback (the position to anyone who asks), and a watch
per client (N copies of gpsd's stream for identical output).

### Amendment (2026-10-02): the terrain layer serves three readers, and SPLAT! and Signal-Server join QMapShack

The gap report's A5 (`docs/reference/catalog-gaps-2026-09.md`) said the
terrain this decision fetches serves one reader and could serve four.
Built on branch `terrain-readers` from
`docs/superpowers/specs/2026-10-02-terrain-readers-design.md`, on the
architectural path; Q-022 needed no ruling for it. **Amends** this
decision's converter enum with `splat-sdf`, and D-068's amendment, whose
`alternative` input `splat-sdf` now reads as `gdal-dem` does.

**The readers, measured.** QMapShack was served (`dem-qmapshack`). SPLAT!
(carried, apt) and Signal-Server (added here) read SPLAT Data Files, and
are now served by `splat-sdf`. Xastir (carried) draws a GeoTIFF or a
`.geo`-described image, which an SDF is not: its route is a shaded-relief
image per tile with a `.geo` beside it, `gdal-dem`'s shape again, not built
because nobody has measured Xastir drawing one, and SPLAT's own `-geo`
output is an Xastir layer today. `gpredict` and `hamclock-next` read no
terrain; a horizon mask is post-1.0, as A5 says. So three of the four A5
counts are served, and the fourth is written down with its route.

**The format, from SPLAT's own tools.** Run on 2026-10-01 over synthetic
`.hgt` files whose samples encode their row and column: four header lines
(`max_west`, `min_lat`, `min_west`, `max_lat`, longitudes west and
positive), then 1200 x 1200 or 3600 x 3600 integers, the south row first
and each row from east to west, the `.hgt`'s north row and east column
dropped. Names as `srtm2sdf` writes them, measured on nine squares in four
hemispheres (`36:37:116:117`, `-34:-33:208:209`, and `0:1:359:0` for the
square east of Greenwich). `srtm2sdf-hd` reads the one-arc-second `.hgt`
for 30 m data.

**Measured finding: the tools erase land below sea level by default.**
`-n`, "the elevation below which SRTM data is replaced", defaults to 0; a
synthetic block at -50 m came out at 104 m, and the real tile's -91 m
survived only with `-n -32767`. The converter passes `-d /dev/null -n
-32767`: `/dev/null` is the manual's "prevents data replacement" and keeps
the tool from reading `~/.splat_path`, which names this converter's own
output.

**Ruling: SPLAT's tools, not a writer of our own.** Over one HD tile a
Python writer took 1.06 s where `srtm2sdf-hd` took 4.53 s, and its output
was byte-identical except at a void. The tools define the format, fill
voids the way SPLAT expects, and keep the rule that a converter is an
archive program run as the operator in staging, never the engine parsing
downloaded data under `sudo`.

**Ruling: compressed, both resolutions.** SPLAT! reads `.sdf.bz2` and
Signal-Server `.sdf.bz2` and `.sdf.gz`. On Death Valley's Copernicus tile
(the square at 36 N 117 W, a public example chosen for its land below sea
level, verified against its ETag and deleted after) the HD file
is 57,033,174 bytes as text and 5,668,563 compressed; the standard one
6,336,641 and 867,797. `splat-hd` is built for 4 square degrees and
`splat` for 8, and a 20 km coverage map took them 22 s and 1.3 s, so both
files are made.

**The converter.** Per tile, as the operator in `splat.work` under one
lock: `gdalbuildvrt -resolution highest` over the tile and its installed
neighbours (a Copernicus tile's south row and east column are theirs:
its origin is half a sample beyond its north-west corner, measured);
`gdalwarp -r average -dstnodata -32768` to a 3601-sample `.hgt` centred
on whole arc seconds, a sample no tile covers becoming a void that
`srtm2sdf-hd` fills from its neighbours; `srtm2sdf-hd`, `bzip2 -9`; the
same at 1201 samples with `srtm2sdf`; both files published with a
`.source` sidecar (the tile, its window, `splat-sdf 1`) and a link
beside each under Signal-Server's name (`36_37_116_117-hd.sdf.bz2`; for
the square east of Greenwich `0_1_359_360`, from its `LoadTopoData`, not
run). A changed source tile (`--dem-source`), a changed window or a new
converter remakes a square; a square no region needs loses its files.
`PrefixWriter.link` makes the links: a sibling name only. Through the
engine's own code on that tile: 16.3 s, 151.2 MB peak in the children,
the same bytes as the manual run, and the next plan found it current;
`splat-hd` and `splat` then reported 699 m and -17 m at two sites where
the source tile has 699.2 and -16.7.

**Signal-Server.** Cloud-RF's repository was reduced to a history README
on 2025-08-28; its code was deleted in 2023 and it names two forks. Cloned
once each on 2026-10-01 (D-032): N9OZB's head is 2019-07-30, W3AXL's
2026-01-30, GPL-2.0 both, no tags in either, no distribution package.
**Ruling: W3AXL's head, `7f6242a`, a D-024 pin with `basis: own_choice`.**
Built in rootless Podman on Debian 13: configure 0.4 s, compile 38.4 s at
420 MB peak. **Measured: a release build crashes on every plot.** The
engine configures CMake as Release, which adds `-DNDEBUG`, and
Signal-Server allocates ITWOM's arrays inside `assert()`
(`src/models/itwom3.0.cc`, lines 2143 and 2196): `-O2` ran clean, `-O2
-DNDEBUG` crashed in `d1thx`. The manifest passes
`-DCMAKE_CXX_FLAGS_RELEASE=-O2`, no patch. **Measured: the threaded plot
races**: 2 crashes in 10 at 30 m and 1 in 20 at 90 m with threads, none in
the unthreaded runs; the guide's command passes `-nothreads`. Its
`CMakeLists.txt` is in `src/`, and the CMake path now passes `-S
<tree>/<project_file>`, which the schema had always documented and the
engine had ignored; a `project_file` must stay inside the tree.

**Pointing the readers.** `hammunition maps splat`, per user and refused
as root, writes `~/.splat_path` only when it is absent, leaves another
directory alone with the `-d` to pass, refuses a symbolic link, and
prints Signal-Server's `-sdf`. Nothing writes it during an install.

**Rejected.** A writer of our own (above). Uncompressed files (ten times
the disk). Only the HD files (`splat-hd`'s 4 square degrees). A patch to
Signal-Server for the `assert()` (a configure argument does it, and
`patches` stay a measured zero). N9OZB's fork (seven years still).
Signal-Server's LIDAR mode and the web front ends (not fed, unmeasured).

**Not measured, and owed by the bench:** `splat-sdf` through `hammunition
install` on a real region; SPLAT! and Signal-Server plots over it on the
field laptop; the threaded crash on other hardware.

**Consequences.** `src/hammunition/backends/splat_sdf.py`;
`PrefixWriter.link` in `src/hammunition/backends/verified.py`; the CMake
`-S` in `src/hammunition/backends/source.py`; `INPUT_OWNERS` and the enum
member in `src/hammunition/manifest/schema.py`; `TerrainRun.splat` and
`splat_source` in `src/hammunition/terrain_plan.py`; the SDF estimates in
`src/hammunition/backends/terrain.py`; the plan's lines and JSON fields in
`src/hammunition/interface/plan.py`; `src/hammunition/splat_path.py` and
`maps splat` in `src/hammunition/cli/main.py`;
`catalog/packages/splat-sdf.yaml`, `catalog/packages/signal-server.yaml`,
`catalog/profiles/antenna.yaml`. The operator's page is
`docs/guides/propagation.md`, *Terrain for coverage plots*. Tests:
`tests/test_splat_sdf.py`, `tests/test_splat_sdf_schema.py`,
`tests/test_splat_sdf_plan.py`, `tests/test_splat_sdf_catalog.py`,
`tests/test_splat_path.py`, and additions to
`tests/test_verified_install.py` and `tests/test_source_backend.py`.

### Amendment, 2026-10-02 — the plan reports its network work (#197)

D-061's plan-time checks (and D-066's, D-068's, D-069's, which follow the same
shape: a `HEAD` per item the plan discloses) ran one request at a time before
a single line was printed. `hammunition install navigation --dry-run` sat
silent for over two minutes on one request per Copernicus tile; the operator
could not tell that from a hang. Now: each batch of checks (terrain tiles,
3DEP tiles, US Topo sheets, FSTopo sheets, Kiwix books, CoMaps maps, and the
map regions' Geofabrik answers) announces itself on **stderr** (`checking N
<kind> against <publisher> (needs the network)…`), counts on one line, and
closes with the elapsed time. It speaks only when stderr is a terminal or
`HAMMUNITION_PROGRESS=1` forces it, so `--json`, pipes, logs and CI are
byte-identical to before. The tile, sheet, book and map checks run four at a
time (`hammunition.progress.CHECK_WORKERS`), results taken in input order and
errors reported per item as before; the probes hold no state, so no lock. On a
synthetic 50-tile list with a 0.2 s fake server: 10.0 s sequential, 2.6 s with
four. **Not done:** a tile already on disk was never asked about (it is
"current"), so the issue's third bullet could only mean consulting the log's
D-053 attribution as well, which is the maintainer's call; a per-tile ETag
cache is not built either. Code `src/hammunition/progress.py`; tests
`tests/test_progress.py`.

---

## D-062 — An install run as a user asks sudo once and keeps its ticket valid until the run ends, and no longer

**Date:** 2026-09-29. **Status:** proposed (implemented for issue #137 on
branch `sudo-keepalive`; the maintainer decides it at review). **Depends
on:** D-021 (`--yes` never answers for a person, and does not here), D-044
(an engine-run step disclosed in the plan, with a `--no-` opt-out), D-061
(the long operator-side conversions that exposed this), D-031 (verify the
effect). **Amends:** nothing; it adds the first privilege the engine holds
for longer than one command.

**Why.** Measured on the field laptop, 2026-09-29, bench session 12:
`hammunition install navigation`, run as the operator, did about 30 minutes
of work and then waited **7.8 hours** at a `sudo` password prompt. Run as a
user, the engine puts `sudo` in front of each root step and nothing else
(`Command.argv_for`). sudo caches the credential for `timestamp_timeout`
minutes, 15 by default, and D-061's conversions run as the operator for
longer than that, so the root step that publishes the first converted map
asked again, with nobody at the keyboard. The plan did not say it would.
The workaround used since, `sudo -v && (while sudo -n -v; do sleep 240;
done &) && hammunition install navigation`, works only in the install's own
terminal, because the laptop's sudo keeps a ticket per terminal.

**Decided.**

1. **When.** A real `install` run as a user (euid not 0) whose steps
   include at least one root command and at least one step that is not
   root. Not a dry run, not a run as root, not a run where every step is
   root or none is, and not with `--no-sudo-keepalive`. Only `install`:
   `uninstall` and `hardware apply` have no long unprivileged step between
   root ones.
2. **The plan says so first.** A `sudo (D-062)` section, printed by the dry
   run and the real run alike and carried in the JSON plan as
   `install.sudo`, opens with "sudo's ticket is kept valid for the length
   of this transaction; it is not extended beyond it", and says how. With
   `--no-sudo-keepalive` it says instead that a root step after a long step
   may ask again, and not to leave the run unattended. This answers the
   issue's first ask: the plan says whether the run can prompt again.
3. **One prompt, while the operator is watching.** After the confirmation
   (or `--yes`) and before the first step, `sudo -v` runs on the terminal.
   The password goes to sudo; the engine never reads, stores or passes it,
   and `--yes` does not change what sudo asks.
4. **A refresh that cannot prompt, stopped with the run.** A thread of the
   same process runs `sudo -n -v`, stdin `/dev/null`, every 240 seconds,
   and is stopped and joined when the transaction returns or raises. The
   ticket then expires on sudo's own schedule.
5. **Failure is reported once, never retried.** If `sudo -v` fails, nothing
   is refreshed and each root step asks as it did before this decision. If
   a refresh fails, one warning says so and the refreshing stops. The log
   records `sudo_keepalive_begin` (whether `sudo -v` succeeded) and
   `sudo_keepalive_end` (refreshes, and the failure if any)
   (`docs/reference/transaction-log.md`).

**Why this works under `timestamp_type=tty`.** The refresh is a child of
the engine's own process on the same controlling terminal as every root
step, so it refreshes the ticket those steps use, whether sudo keeps tickets
per terminal, per parent process or globally. That is what a loop in another
window cannot do.

**Why 240 seconds and not a measured timeout.** sudo's default is 15
minutes, and `sudo -l` prints only `Defaults` lines somebody wrote, never a
compiled-in value, so the real timeout is not readable without root. Four
minutes is inside any timeout of five minutes or more. A site that sets it
lower gets the failed refresh reported once, and its prompts as before.

**Measured.** `tests/test_sudo_keepalive.py`, against a fake `sudo` on the
test's `PATH` recording its argv: `sudo -v` runs once before the first step
and every refresh is `sudo -n -v`; the refreshing stops when the run
returns, when it raises, and after the first failure, with nothing run in
the ten intervals after; a failed `sudo -v` refreshes nothing; a dry run,
`--no-sudo-keepalive`, a run as root and an all-root plan run no `sudo` at
all. `tests/conftest.py` now refuses any `sudo` the keepalive would run that
does not resolve under the temporary directory, so no test can prompt on a
developer's terminal. **Not yet measured:** a real run on the field laptop
past sudo's timeout with the operator away, which is the evidence this
decision needs before it is accepted.

**Rejected.** Reordering the transaction so root steps come first and last
(the issue's second route): each map is published into the prefix as
soon as it is converted, one region at a time; batching the publishes to
the end would hold every converted map in staging until all conversions
finish and change what a failed region leaves behind, and the steps that
follow root ones (a build after its `build_depends`) cannot move. Running
the whole install under `sudo` (the third route): not measured as safe, and
the engine's default is to drop to the operator, not to start as root.
Setting `timestamp_timeout` or writing sudoers: that changes the machine's
policy for every program, which a transaction has no business doing.
`sudo -S` or an askpass helper: the engine would then handle the password.

---

## D-063 — BRouter is carried, and its routing files are built on the machine from the station's regions and elevation, never downloaded

**Date:** 2026-09-29. **Status:** proposed (design approved by the
maintainer in conversation on 2026-09-29, as recorded in
`docs/superpowers/specs/2026-09-29-brouter-design.md`; implemented on
branch `brouter`; the maintainer decides it at review). **Depends on:**
D-057 (regions are station data; derived data by a converter enum run as
the operator), D-061 (Copernicus tiles; the Routino converter's shape; the
QMapShack launcher; loopback only), D-049 (pinned data units), D-035 and
D-039 (a missing station value defers by name), D-031 (verify the effect),
D-024 (pin what upstream tags). **Amends:** D-061's gaps list, whose
"BRouter stays out" line now points here; D-061's converter enum, with
`brouter-mapcreator`.

**Why.** D-061 carries one offline router, Routino, whose foot profile
ignores trail difficulty (`sac_scale`) and whose profiles ignore climbs.
The routing spike of 2026-09-29 measured five engines on Delaware, a
public example state. BRouter was the only one with an offline desktop
consumer Hammunition already carries: QMapShack 1.17.1 has a local BRouter
backend (`CRouterBRouterLocal`) that starts `java ... btools.server.RouteServer`
itself as a subprocess and talks to it over HTTP. Its `hiking-mountain`
profile reads `sac_scale`, and every profile weighs elevation. D-061 left
it out for one reason: brouter.de's `segments4/` publishes 1,142 routing
files, rebuilt weekly, with no checksum of any kind. The spike measured
that BRouter's own map creator, inside the same pinned jar, builds those
files from a Geofabrik extract in minutes, and that the Copernicus tiles
D-061 already verifies convert into its elevation format. Built on the
machine, from inputs already checked, the objection is gone.

### Three units

| Unit | What | Where |
|---|---|---|
| `brouter` | binary: upstream's `brouter-1.7.10.zip`, 6,724,983 bytes, sha256 `023fec3b…1532` (GitHub's asset digest, measured equal), installed as a tree, marker `brouter-1.7.10-all.jar`, `depends: [default-jre-headless]` (class files major 55, Java 11) | `/usr/local/share/hammunition/brouter/` |
| `brouter-mapcreator-profiles` | data: `all.brf` (511 bytes) and `softaccess.brf` (631 bytes), which the zip lacks, from `raw.githubusercontent.com` at the tag's commit `4d2639af` | `data/brouter-mapcreator-profiles/` |
| `brouter-segments` | derived, converter `brouter-mapcreator`: `source: osm-regions`, `program: brouter`, `profiles: brouter-mapcreator-profiles`, `elevation: dem-copernicus`; depends on `osmium-tool` and `gdal-bin` from the archive | `data/brouter-segments/*.rd5` |

The `navigation` profile gains all three. Licence MIT (the GitHub API;
QMapShack's About text still says GPLv3, which is stale). No launcher and
no service: QMapShack starts BRouter while its Routing dock uses it and
stops it with itself.

**Ruling: the two filter files by commit, not the source tarball.** The
approved design named the v1.7.10 source tarball. It was downloaded
(`archive/refs/tags/v1.7.10.tar.gz`, 2,037,047 bytes, sha256
`cb83f220332de8223476e029a9b075157fb74401c69a991ceaa013486c29760f`, our own
measurement: GitHub publishes none for an archive), and its two members'
sha256 are the pins. The files themselves are fetched by the tag's commit,
the way `country-boundaries` is (D-057's amendment): the same bytes,
measured equal, from a commit-addressed URL that cannot change, where
GitHub's generated archives have not been guaranteed byte-stable; and a
`data` tarball would install a whole source tree, under a versioned top
directory, for 1.1 KB. They are map-creator filters, not routing profiles,
so they are kept out of `profiles2`, where QMapShack would list "all" and
"softaccess" as profiles to route with.

**The block's three new fields.** `program` (a `binary` unit that installs
a tree) and `profiles` (a `data` unit) are required on `brouter-mapcreator`
and refused on every other converter; `elevation` (a `dem-tiles` unit) is
optional, and without it the routes are flat. Each must be in `depends`,
checked per manifest, and each is checked catalog-wide for its method,
as `source` is (`BROUTER_INPUTS` in `src/hammunition/manifest/schema.py`).

### One build over every region

**Ruling: one set, never one per region.** A routing file is named by its
5-degree square, so two regions in one square would each write the same
file. `OsmFastCutter` takes one input, so two regions or more are merged
first with `osmium merge`. This is Routino's shape (D-061): one working
directory, `brouter.work`, under the operator's staging directory, one
lock held by every step and every clear, run as the operator through
`Staging`; a region that did not install or a step that fails fails the
set by name in the terrain ledger, the installed set is kept, and the
ledger's last step fails the run. The steps, each checked by its output,
never its exit status:

1. a check that the jar, `lookups.dat`, `trekking.brf` and the two filters
   are installed, then the working directory emptied;
2. `osmium merge <pbf>... -o merged.osm.pbf --overwrite`, with two regions
   or more;
3. per 5-degree square holding a tile the regions need: `gdalbuildvrt` over
   the installed tiles in the square and its one-degree ring, `gdalwarp -te
   <lon-0.5"> <lat-0.5"> <lon+1+0.5"> <lat+1+0.5"> -ts 3601 3601 -ot Int16
   -of SRTMHGT` of each of the square's tiles out of that mosaic (so a
   tile's edge rows come from its neighbour, not no-data; the mosaic at
   `-resolution highest`), then
   `ElevationRasterTileConverter srtm_XX_YY hgt bef 1`, naming the square as
   `PosUnifier` does (`-1` north of 65 degrees); the `.hgt` files are
   removed before the next square;
4. `OsmFastCutter` with `-DavoidMapPolling=true -DuseDenseMaps=true
   -Ddeletetmpfiles=true`, `PosUnifier` over the `.bef` directory, and
   `WayLinker ... segments rd5`, as upstream's `process_pbf_planet.sh` runs
   them less its database pseudo-tags; every Java step at `-Xmx4000m`;
5. every `.rd5` published under a temporary name, verified, renamed in, the
   files no longer built removed, and the record written: all or none.

**`-DavoidMapPolling=true` is a measured finding.** `OsmParser` waits for
its input to grow, 10 s at a time, until 120 s have passed, for any file
within 100 MB of its end: upstream runs `osmupdate` beside it. On the
synthetic region the cut took 120.1 s without the property and 0.1 s with
it; most of the spike's "128 s" for Delaware was that wait.

**The record**, `segments.source`: a `<slug> <snapshot>` line per region,
`elevation <tile>` per tile folded in, `program <jar>`, `profiles
<version>` (the filters unit's), `segment <file>` per routing file, and
last `converter: brouter-mapcreator 1`. The set is
current when the record, less its `segment` lines, is what this run would
write and every segment exists. A new region or snapshot, a tile gained or
lost, a new jar or filters version (as planned, so in the same run as the
upgrade) or a bumped converter rebuilds it; a tile that failed to
download is built in next run, because the record names the tiles that
were on disk.

With no regions set, `brouter-segments` is deferred by name with the other
map units, and `brouter` and its filters install.

### The plan and the disk

*Built for QMapShack* gains "BRouter routing files over N region(s) about X
(0.2x the downloads together), elevation from T tile(s); built here, never
downloaded from brouter.de", and the JSON plan `brouter_regions`,
`brouter_tiles` and `brouter_estimate`. The disk check counts, in the
staging directory, 3x the downloads (**an allowance, not measured**), the
merged input with two regions or more, one square's `.hgt` files (25 at
most, 25,934,402 bytes each, measured) and every square's `.bef` (8 MB,
Delaware's 7,987,865 bytes rounded up), and under the prefix 0.2x the
downloads (Delaware's 3.3 MB from 22.1 MB, 0.15, rounded up). `update`
names `brouter-segments` in its rebuild command beside the other derived
units.

### QMapShack is told where BRouter is

`hammunition maps qmapshack` registers it when the tree holds one
`brouter-*-all.jar` and at least one `.rd5` is built. The group is
`Route/brouter`, read from QMapShack 1.17.1's `CRouterBRouterSetup.cpp` at
tag `V_1.17.1`: under `[Route]`, `brouter\installMode=local`,
`brouter\localDir` (the tree), `brouter\localBRouterJar`,
`brouter\localSegmentsDir`, `brouter\localHost=127.0.0.1`,
`brouter\localBindLocalonly=true`, and `brouter\localJava` only when
absent or empty. `localProfileDir` stays at QMapShack's default,
`profiles2`, relative to the tree.

QMapShack's `save()` writes every one of these on exit, so an absent key
and one at QMapShack's default are the same fact: nobody chose it. A
`localDir` that is absent, `.` or already ours is set, with the rest; any
other is the operator's own BRouter, and nothing is touched and one line
says so. **Ruling: the host and the bind are forced to loopback when the
tree is ours.** QMapShack passes the host to BRouter only when "bind to
hostname only" is on, and BRouter otherwise listens on every interface,
which QMapShack itself warns about; the engine's rule since D-061 is
loopback only. `localJava` matters because a QMapShack run before Java was
installed saves it empty, and BRouter then reads as "not installed" for
good. A quoted or `@`-typed value in one of these keys leaves BRouter alone
with a line, and never refuses the launch: BRouter must not stop the maps
from opening. `Route/current`, which router the dock shows, stays the
operator's; Routino remains the default.

### What is measured, and what is not

Measured on the development host on 2026-09-29, in scratch: the zip's
digest and the two filters' against the tarball's members; the engine's
converter, the real jar and the archive's `osmium` and GDAL, on two
synthetic regions (a 4-by-4 grid of roads and paths near Wilmington, and
a track sharing a node with it) and a synthetic flat 42 m tile on the
Copernicus grid: the `.hgt` (25,934,402 bytes), `srtm_21_05.bef` (2.8 s
through the engine), the cut (0.1 s), unify (0.6 s), link (0.1 s),
`W80_N35.rd5` installed with its record, and the next plan finding it
current; then `RouteServer` started as QMapShack starts it (in the tree,
the jar and `profiles2` relative, the segments absolute, a custom-profile
directory that does not exist, `127.0.0.1`): `ss` showed one loopback
listener, and `trekking` and `hiking-mountain` routes crossed both regions
with 42 m on every point. The jar run with no arguments printed
`BRouter 1.7.10`, the line QMapShack's version check reads, in 0.09 s.
From the spike, on Delaware: the cut 128 s and 593 MB (most of it the
polling above), the elevation square 9.3 s and 1.33 GB, the link 7 s and
445 MB, 3.3 MB of routing files.

**Loopback depends on QMapShack reading BRouter's version.** QMapShack
1.17.1 passes the host to `RouteServer` only when "bind to hostname only" is
on *and* it has parsed BRouter's version (`usesLocalBindaddress()`), which
it reads by running the jar with a 3 s limit; and its synchronous route
request starts the server in local mode without checking that the install
was found valid (`CRouterBRouter::synchronousRequest`). A probe that times
out would therefore start BRouter on every interface. The jar printed its
version in 0.09 s on the development host, so this is not expected, but it
is QMapShack's behaviour and not the engine's to prevent; the guide says
how to check with `ss -ltnp` (final review, I2).

**Final review, 2026-09-29.** The record's `program` line and a new
`profiles` line now come from the plan (`brouter_pins()` in
`src/hammunition/terrain_plan.py`: the planned `brouter` unit's tree marker
and the planned filters unit's version), not the disk, because steps are
planned before the binary step replaces the tree: read from the disk, a
BRouter bumped in the same run left the old routing files in place until
the next install (I1). `update` names `brouter-segments` in its rebuild
command when `brouter` or the filters are behind. `gdalbuildvrt` takes
`-resolution highest`, so a window crossing Copernicus's 50-degree
longitude-spacing change is not resampled to an average grid; a
`<square>.rd5.new` a crashed publish left is removed with the stale files;
an empty `localDir` reads as never set.

**Deferred.** A square beside the 180-degree meridian does not take its
ring tile from across it, so its edge column has no neighbour value (no
Geofabrik region measured sits there; recorded, not handled). The launcher
names the `brouter` and `brouter-segments` units, as it names
`osm-routino` (D-061's precedent). An operator who switches QMapShack's
BRouter back to online, keeping Hammunition's directory, is switched to
local again by the next launch, with a line saying so: QMapShack saves
`online` both as its default and as a choice, and the two cannot be told
apart.

**Not measured, and owed by the bench:** a QMapShack route drawn through
this BRouter (no desktop runs on the development host), with `ss -ltnp`
showing it on loopback only while it routes, and QMapShack's own
validation of the tree; the build on a real region through `hammunition
install`, with its time, memory and scratch; heaps and scratch on anything
larger than Delaware; Java on the targets other than Parrot. The guide's
*What has not been measured yet* is the operator's copy of this list.

**Rejected.** brouter.de's routing files (no checksum, rebuilt weekly).
The source tarball as the filters' artifact (above). One build per region
(two regions in one square collide). Selecting BRouter as QMapShack's
router (the operator's choice). A launcher or a service for BRouter
(QMapShack starts it, and only while it is used). Putting `all.brf` and
`softaccess.brf` in `profiles2` (QMapShack would offer them as routing
profiles). GraphHopper, OSRM, Valhalla and OpenRouteService, measured in
the same spike: none has an offline desktop consumer here (QMapShack has
only Routino and BRouter, and Marble's and GNOME Maps' URLs for the others
are hardcoded online); OSRM 26.x is in Debian forky and sid only and fails
to build on trixie; Valhalla is in no archive and its wheel vendors an
end-of-life OpenSSL; GraphHopper, the best-verified (Maven Central's PGP
signature), is the candidate once a local tile server gives it a map.

**Consequences.** `BROUTER_INPUTS` and the `program`, `profiles` and
`elevation` fields in `src/hammunition/manifest/schema.py`;
`src/hammunition/backends/brouter.py`; the BRouter factors and needs in
`src/hammunition/backends/terrain.py`; `TerrainDisclosure`'s BRouter
fields in `src/hammunition/backends/dem.py`; `TerrainRun.brouter` in
`src/hammunition/terrain_plan.py`; the plan line and JSON fields in
`src/hammunition/interface/plan.py` (`docs/reference/json-interface.md`
regenerated); `update`'s rebuild command; `register_brouter` in
`src/hammunition/qmapshack_config.py` and its call in `maps qmapshack`;
`catalog/packages/brouter.yaml`,
`catalog/packages/brouter-mapcreator-profiles.yaml`,
`catalog/packages/brouter-segments.yaml` and
`catalog/profiles/navigation.yaml`. The operator's page is
`docs/guides/offline-navigation.md` (section 9, *Route with BRouter*), and
the CLI's is `docs/reference/cli.md`. Tests: `tests/test_brouter.py`,
`tests/test_brouter_schema.py`, `tests/test_brouter_plan.py`,
`tests/test_qmapshack_brouter.py`, and the pins and profile in
`tests/test_navigation_catalog.py`.

## D-064 — Repeaters on the map come from the operator's own export, converted on this machine; nothing is fetched from RepeaterBook, and hearham's open list only on request, unverified

**Date:** 2026-09-29. **Status:** proposed (the design is the spike's
recommendation, approved by the maintainer; implemented on branch
`repeaters`; the maintainer decides it at review). **Spec:**
`docs/superpowers/specs/2026-09-29-repeaters-design.md`. **Depends on:**
D-021 (disclose, never adjudicate; YAAC's objects can transmit), D-033 (an
unlicensed source judged on what we do with it), D-049 (why this is not a
data unit), D-057 and D-061 (the Navit and QMapShack configurations this
adds to), D-059 (the documents), D-031 (the input's date, not the run's).

**Why.** The maintainer wants repeaters on the offline maps. The spike of
2026-09-29 measured every route an operator has without an API key.
RepeaterBook's API is gated, and its data-use page forbids "bulk
extraction, mirroring, redistribution, offline bundling" without written
permission. Its *website export*, though, is granted to a registered user
"for their own personal use", and its GPX page describes loading the file
into navigation tools, offline. CHIRP's RepeaterBook query was run headless
and its CSV drops Lat/Long, keeping only "near <city>". hearham.com serves
the whole world, 22,698 rows, unauthenticated, with no licence and no
ETag. The FCC's licence database has no coordinates.

### The rule

1. **The operator's export, converted here.**
   `hammunition maps repeaters import FILE... [--exported YYYY-MM-DD]`
   reads a RepeaterBook GPX, a RepeaterBook CSV that has `Lat` and `Long`,
   hearham's JSON as served, or a hand-typed CSV with the header
   `callsign,output_mhz,offset_mhz,tone,mode,lat,lon,name,notes`, recognised
   from the content. No network.
2. **What has no coordinates is refused by name, with the reason:** a CHIRP
   CSV, a CHIRP `.img` (by suffix, or CHIRP's own metadata marker read from
   `chirp_common.py`), and a RepeaterBook CSV without `Lat`/`Long`. KML is
   deferred: it is the same data as the GPX. XML with a DOCTYPE is refused
   before parsing. One refused file refuses the import, and nothing is
   written. A position is never guessed from a town name.
3. **Merged on callsign + output Hz + position to 0.01°.** Callsign and
   frequency alone would have merged 1,050 multi-site keys in hearham's
   data. The newer `Last Update` wins where both rows carry one, else the
   first read; every merge is counted and printed.
4. **Three outputs, the operator's own.** A GPX (`<name>` `CALL FREQ`,
   `<desc>` offset, tone, mode, use, status, place and source,
   `<sym>Tall Tower</sym>`, a QMapShack built-in), a Mapsforge `.poi`, and a
   Navit textfile (`poi_custom0`, labelled, Navit's own `tower.png`), in
   `~/.local/share/hammunition/overlays/repeaters/`: directory 0700, files
   0600, each renamed into place. Never under the root prefix, never in the
   catalog, never in the transaction log. Refused as root. The layer is
   named `Repeaters (own export YYYY-MM-DD, personal use)`, dated by
   `--exported`, else by the oldest input's modification date.
5. **Registered where each program reads it.** QMapShack: the directory in
   `[Canvas] poiPaths`, by the editor `maps qmapshack` uses, with its
   refusals; `maps qmapshack` keeps the path there exactly while a `.poi`
   exists, because a QMapShack open during the import writes its own list
   back on exit. Navit: its generated configuration is root's and
   `~/.navit` is never touched, so the `navit-offline` launcher now runs
   `hammunition maps navit`, which opens the generated file, or, when there
   is a layer, the operator's copy of it
   (`~/.local/share/hammunition/overlays/navit.xml`, 0600) with a textfile
   map added to its one enabled mapset by `navit_config.add_maps`.
   `maps repeaters remove` deletes the files and unregisters both;
   removing nothing is exit 0.
6. **The licence text is printed at import, before the counts.**
   RepeaterBook: "Data courtesy of RepeaterBook.com", personal
   non-commercial use, never redistributed, converted on this machine only,
   positions approximate, terms at `repeaterbook.com/about/legal`. That
   attribution is also in the GPX's metadata and in every waypoint's
   `<desc>`, as RepeaterBook's terms require of an overlay. A hand list:
   "Your own data." A disclosure of terms, not a ruling on them (D-021).
7. **hearham on request only.** `maps repeaters fetch-hearham` prints what
   it will fetch, fetches once (bounded at 64 MB, redirects to HTTPS only),
   records the sha256 it observed in the layer and the output, and names
   the layer `Repeaters (hearham YYYY-MM-DD, unverified)`. It prints
   hearham's own line that the data should not be relied upon "for medical
   emergencies, or any other life-and-death operations". Carried under
   D-033: no licence, used on the operator's request, never redistributed.
8. **Documents.** `import --json` prints a `repeaters` document and
   `remove --json` a `repeaters-removed` document, from the same objects as
   the text. Both carry counts, paths and the layer name, never a
   repeater's callsign or position: the export says where the operator
   operates. `fetch-hearham` and `maps navit` have no JSON form.

### Not a D-049 data unit

A data unit is an artifact the engine fetches from its publisher and pins by
sha256. An export is neither fetched by the engine nor pinnable, and
RepeaterBook's terms forbid anyone but the exporting user holding it. hearham
is live, with no ETag and no dated snapshot: a pin would be wrong the day it
was taken. So the export is a command's input, and the fetch records what it
observed and says it is unverified.

### Not carried, and why

- **Any fetch from RepeaterBook**: the API is gated, and bulk extraction and
  offline bundling need written permission.
- **Navit address search of repeaters**: the textfile driver has no search
  method; they list under POIs → Other, with distance.
- **Xastir `.gnis`**: its point layers live in a root-owned map tree under
  `/usr/share/xastir`.
- **YAAC `.pos`**: YAAC imports APRS objects, which it can transmit. That is
  a D-021 matter, not a map layer.
- **FCC ULS**: no coordinates.
- **KML**: deferred; the GPX carries the same data.

### Rulings made on the way

- **Every GPX gets RepeaterBook's attribution and terms.** A real export's
  `<name>` and `<desc>` are unmeasured, so an export cannot be told from
  another GPX; the attribution is the cautious default. Callsign and output
  frequency are found in `<name>`, then `<desc>`; a waypoint with neither is
  kept under its own name, keyed on that name.
- **The POI writer moves a point whose box straddles a 0.1° line into the
  tile north or east of it.** QMapShack asks for each 0.1° tile with
  `min >= tile` and `max < tile + 0.1`, and SQLite's rtree stores float32
  boxes rounded outwards, so a point on a line is in neither tile (the
  spike). The spike's note said "widen each box"; widening makes it worse,
  so the point is moved instead, to the float32 one step inside the tile,
  a few metres at most. The first version used a fixed 1e-5° band; the
  final review measured the straddling band growing with the coordinate, to
  about ±2e-5° at 180°, and 16 points past about 64° lost. It is now
  computed with SQLite's own rounding (`rtreeValueDown`/`rtreeValueUp`), and
  the tests run QMapShack's verbatim query over 61 offsets around twelve
  lines from −179.9° to 179.9°, and show a point on a line lost without the
  move. The bounds are padded 0.001°, because a one-repeater file's
  zero-area bounds intersect no tile.
- **A frequency is looked for where it can be.** In a GPX it must fall in
  an amateur repeater band from 10 m to 23 cm, or GMRS, and in `<desc>` a
  number written with "MHz" comes first, so a tone (`PL 100.0`) or a
  coordinate is not taken for one (review). A typed frequency outside 1 to
  10,000 MHz is skipped and counted, not labelled `146940000.000`. A
  hearham entry of another shape is skipped and counted rather than
  refusing the whole list.
- **The fetch names every HTTP failure.** `http.client`'s own exceptions
  are not `OSError`; a malformed answer is now "could not fetch", not a
  traceback (review). A redirect is followed to HTTPS only; both refusals
  are tested on loopback.
- **Each import replaces the layer.** Combining sources is one import of
  every file. A fetch does not keep hearham's raw JSON; to combine it with
  an export, the operator saves the JSON and imports both.
- **The Navit launcher changes.** The alternatives were writing
  `~/.navit/navit.xml`, which the navit manifest promises never to touch
  and which `navit-offline` does not read, or writing into the root prefix,
  which the operator cannot do without sudo. `hammunition menus apply`
  rewrites an installed `navit-offline` to the new form (tested).
- **A refused QMapShack edit still writes the layer**, names the reason and
  exits 1; the files are the operator's either way.
- **`command_name` reads a third level**, so an error document says
  `maps repeaters import`.

### What has run

The test suite only: every parser against synthetic fixtures (N0CALL,
N0TST, Springfield IL), every refusal, the merge, the three writers, the POI
against QMapShack's own SQL, `add_maps`, the QMapShack edit byte for byte,
modes 0600 and 0700, root refused, both documents validated against their
schema with the text's values carried and no callsign or coordinate in
either, `remove` idempotent, `maps navit` with `execvp` stubbed, and
`fetch-hearham` against a loopback server only. No GUI was started.

**Owed to the bench:** QMapShack drawing the GPX and the POI collection, and
reading `poiPaths` under `[Canvas]`; Navit's `poi_custom0` label and tower
icon, and the POIs → Other listing; one real RepeaterBook export, GPX and
CSV, by a logged-in operator, for its columns and `<desc>` layout.

**Rejected.** RepeaterBook's API (gated, and forbidden for this use).
Geocoding CHIRP's "near <city>" (an invented position). A catalog data unit
(above). A callsign plus frequency key (drops real repeaters). A custom
QMapShack icon (would write into QMapShack's own directories). Navit's
`poi_communication` type (no label in the stock layout).

**Consequences.** `src/hammunition/repeaters.py`,
`src/hammunition/interface/repeaters.py`, `navit_config.add_maps`, four
commands in `src/hammunition/cli/main.py`, the `navit-offline` launcher in
`catalog/packages/navit.yaml`; tests `tests/test_repeaters.py`,
`tests/test_repeaters_cli.py` and `tests/test_navit_config.py`; the
offline-navigation guide's section 13 and `docs/reference/cli.md`.

**Amended 2026-10-04 (#324): the name and the tooltip carry what an operator keys in.** QMapShack 1.17.1 draws only a POI's `name` on the map and, on hover, every other `poi_data` key as `key: value` (`CPoiFilePOI::getToolTip`, `CPoiItemPOI::getDesc`; read from source, not run), so the name is now `N0CALL 146.940 2m -0.600 T100.0 FM` (callsign, output MHz, band, signed offset, `T`/`D` tone or `CSQ`, modes), the POI gains a `keying=` line, the GPX gains `<cmt>`, Navit's label and `maps repeaters list` use the same line, and an `=` in a value is written as `:` because QMapShack drops a line with two. A tone is `CSQ` only where the source gave an offset. Nothing is invented: a row with neither offset nor tone keeps the old name. On-screen confirmation is the maintainer's.

## D-065 — The documentation is published as a static site built from `docs/` by MkDocs and Material, pinned exactly, strict on every link, with the project records left in the repository

**Date:** 2026-09-30. **Status:** the site is the maintainer's ask (2026-09-30:
"do we need a .io or whatever? There's a lot we need to walk users through
with this as well as at least link to each of the projects"); the tooling,
the URL and the layout below are this record's recommendation, and the
maintainer decides them at review. **Depends on:** issue #76 (sub-project 1,
"a published site built from `docs/`"), D-036/D-050 (generated, never
hand-kept copies), D-031 (verify the effect), D-021 (disclose, never
adjudicate). **Amends:** issue #76's "not before 1.0", at the maintainer's
request.

**Why.** Issue #76 recorded the intent: a published site, static, searchable,
regenerated from the catalog rather than a wiki people edit in place. The
pages under `docs/` already existed as Markdown with checked links; what was
missing was somewhere a person who has never seen a git repository could
read them, the walk-throughs CLAUDE.md's standard asks for, and one place that
links every upstream project the catalog installs.

### What is published, where

**https://renegade-penguin.github.io/Hammunition/**, by GitHub Pages from `main`
(`.github/workflows/pages.yml`). No domain is bought: the `github.io` address
costs nothing, and a custom domain can be pointed at the same site later
with one `CNAME` file and a DNS record, which is a decision about money and
naming that is the maintainer's. The repository owner enables Pages once
(Settings → Pages → Source: GitHub Actions); until then the deploy job fails
naming that setting and nothing else in CI is affected.

The site carries getting started, the guides, the profiles, the package and
hardware references, troubleshooting, RF security, the reference
measurements and contributing. It does **not** carry the project records:
CLAUDE.md says `DECISIONS.md`, `PARITY-POLICY.md`, `DESIGN.md` and
`why-hammunition.md` are not part of the user-facing site, and `QUESTIONS.md`,
`SCOPE.md`, `SESSION-LOG.md` and `superpowers/` are the same kind of thing.
They stay in the repository and a link to one from a published page opens it
on GitHub.

### How it is built

- **MkDocs 1.6.1 and Material for MkDocs 9.7.7, pinned exactly** in the `dev`
  and `docs` extras of `pyproject.toml`, as ruff is, and for the same reason:
  the build is a gate, and a gate on a floating version drifts red on its own.
  MkDocs 2.0 removes the plugin and theme systems (Material's authors' own
  notice, printed by 9.7.7 on every build), so an unpinned `mkdocs` would
  break this site on the day 2.0 lands.
- **`mkdocs build --strict`, with link, anchor and absolute-link validation at
  warning level.** A broken link or a missing anchor fails the build, in
  `tests/test_site.py` on every test run and in the Pages workflow before
  anything is published. Measured on the 2026-09-30 tree: 269 warnings before
  the hook below, 268 of them links leaving `docs/`, and zero after.
- **One build hook, `scripts/site_hooks.py`.** Pages are written to read on
  GitHub, where a package page's relative link to its manifest opens it. At
  build time only, a relative link whose target is not part of the site is
  pointed at the same file on GitHub. A link whose target does not exist in
  the repository either is left as written, so the strict build reports it:
  the hook repairs where a link points and never hides that it points at
  nothing. Its tests break it on purpose.
- **No page is orphaned.** Every page is in `mkdocs.yml`'s nav, excluded, or a
  package page reached from the generated package index; `tests/test_site.py`
  fails naming any page that is none of the three.
- **No "edit this page" button.** Half the site is generated, and an edit to a
  generated page is reverted by its generator.

### Every upstream project, linked

`docs/projects.md` is generated by `scripts/gen_projects_page.py` from every
manifest's `documentation.upstream_url`, laid out by the vocabulary's groups
and categories as the menu is (D-050, D-054), one row per unit under its first
category. A URL that is a distribution's package tracker rather than the
author's site is labelled so: 15 of 269 on 2026-09-30, after the BRouter units of D-063 landed. It takes `--check` and
is in `tests/test_docs_generated.py`'s list like every other generator.
`docs/credits.md` names the six inventory sources and the four data sets.

### The guides

Nine task guides, written to CLAUDE.md's standard and linked from the
getting-started path: rig control, radio audio, time and position, the
callsign in each program, FT8 and the digital modes, packet and Winlink,
APRS, SDR first steps, satellites. Every default, port, file name and option
they quote was read from the installed program on 2026-09-30 (an Ubuntu
24.04 container: hamlib 4.5.5, Pat 0.15.1, Direwolf 1.7, Xastir 2.2.0, gpsd
3.25, chrony 4.5, PipeWire's tools, the `rtl-sdr` 2.0.1 archives), and each
guide ends with what was measured and what was not. Nothing in them has been
run end to end against a radio on the field laptop yet, and each says so.

Three things the measurement turned up, recorded where they belong:

- **Debian and Ubuntu install Pat as `pat-winlink`**, and Pat 0.15.1's default
  AX.25 engine is `linux`, the kernel stack Linux 7.1 removed. The packet guide
  sets `engine` to `agwpe`, which is Direwolf's port 8000 (D-045).
- **`rigctld` listens on every interface by default** ("default ANY" in its
  own help). The rig-control guide passes `-T 127.0.0.1` everywhere.
- **Ubuntu 24.04's `librtlsdr2` ships no DVB-driver blacklist**, only an empty
  `/etc/modprobe.d`, which contradicted the `rtl-sdr` device entry. The entry
  now says what was measured and that the other targets are not. It also
  said `kalibrate-rtl` was not carried; it is, and the entry now says so.

### The move to Zensical, measured before it is needed

Material for MkDocs enters maintenance mode on 2026-11-05, and its authors'
successor, Zensical, reads `mkdocs.yml` directly. Measured 2026-09-30:
Zensical 0.0.66 builds this whole tree in 16 seconds, and **runs no MkDocs
hook**, so on that version the out-of-site links would publish as dead links.
The migration therefore waits for one of two things: Zensical running hooks,
or the link rewriting moving into the page generators. Until then the exact
pins keep the site building; a maintained-mode Material on a frozen MkDocs is
a working site, and a version bump is a deliberate commit that runs the same
strict build.

### Not done

- **A custom domain.** The maintainer's decision; one file when made.
- **Versioned docs per release** (`mike` or Zensical's equivalent). Issue #76
  asks for it; one version from `main` is the first step, and a release that
  changes behaviour is when versions start to matter.
- **Per-tool depth pages and the launcher "Documentation" action** (issue #76
  items 2 and 3). The guides name the programs that most need depth.

## D-066 — The offline reference layer: Kiwix books chosen by name and pinned from their `.meta4`, dictd on loopback, FEMA's ICS forms, and one loopback page

**Date:** 2026-09-29. **Status:** accepted (maintainer, 2026-09-29, the
design of the day's spike, relayed with the task). **Spec:**
`docs/superpowers/specs/2026-09-29-reference-layer-design.md`. **Depends
on:** D-049 (offline data is a catalog unit; this is its fourth case, "then
the ZIM with `kiwix`"), D-042 rule 5 (ETC sub-project 5 named the layer),
D-035 (a missing station value defers), D-053 (`update` and
`--upstream`), D-059 (a `books` document; a server has no JSON form),
D-021 (state a licence, never adjudicate it). **Amends:** D-053, whose
upstream states gain *pin expired*.
**Numbering:** written as D-065 and renumbered D-066 before merging,
because D-065 went to the documentation site (PR #154) the same morning;
the spec, plan and commits before the renumbering say D-065.

### What was measured

On 2026-09-29, on the development host (Parrot 7.3, Debian 13 archive and
echo-backports), nothing installed: packages unpacked with `dpkg-deb -x`
and run from there, nine ZIMs downloaded and checked against the
publisher's sha256 (nine matched).

- **`kiwix-serve` listens on every address unless told otherwise.** `-p
  18480` alone gave `LISTEN *:18480` and printed a LAN URL; `-i 127.0.0.1`
  gave `127.0.0.1:18480`. Its default port is 80, which a user cannot
  bind. dictd, by contrast, ships `listen_to 127.0.0.1` in Debian's own
  `/etc/dictd/dictd.conf`.
- **A ZIM does not say its licence.** Of nine read with `zimdump`, one
  (ham.stackexchange) carried a `License` metadata entry.
- **Nothing Kiwix publishes is signed** (`.asc`, `.sig` 404 for the index
  and every file). Each file's `.meta4` carries its exact size and sha-256;
  the `.meta4` sha256 equalled the `.sha256` sidecar and the download for
  all nine. A pin costs one ~5 KB `.meta4`, not a download.
- **Kiwix keeps the two newest dated files of each book**; the archive keeps
  a few old categories only. A pinned URL dies about two publications after
  it is pinned.
- FEMA's ICS forms: 39 PDFs, **8,046,865 bytes** (the page's own size column
  said about 4.9 MB), no checksum published, byte-identical on two fetches.

### The rule

1. **Readers are apt units; payloads are data units; serving is an engine
   verb.** `kiwix-tools`, `kiwix` (the desktop reader; Debian's `kiwix`
   package *is* kiwix-desktop), `dictionaries` (`dictd dict dict-gcide
   dict-wn dict-foldoc dict-vera`, nothing reconfigured) and
   `goldendict-ng`; `kiwix-library` and `ics-forms`; and `hammunition
   reference serve`. The `reference` profile holds the six, post-1.0.
2. **Books are chosen by id in station config, from a hand-written
   allow-list.** `hammunition station set --reference-books` takes Kiwix's
   file stem without its date (`ham.stackexchange.com_en_all`,
   `wikipedia_en_medicine_nopic`); nothing is chosen by default; with none,
   `kiwix-library` is deferred from a profile and refused when typed.
   `catalog/data/kiwix-books.yaml` lists 27 books, each with the
   publisher's licence line, because the file does not carry one and D-049
   rule 2 needs it in the plan: Wikimedia "CC BY-SA 4.0 (text; media
   individually licensed)", Stack Exchange "CC BY-SA", Appropedia and WikEM
   "CC BY-SA 4.0", iFixit "CC BY-NC-SA 3.0 — non-commercial", US federal
   works "US federal work, public domain (17 USC 105)". An id not on the
   list is refused when it is set. Book ids are not location data, so
   `station show` names them.
3. **Pins are generated from the `.meta4`, and a dead pin is an error.**
   `scripts/gen_kiwix_pins.py` reads `library_zim.xml` for each allowed
   book's current `.meta4` and writes `catalog/data/kiwix-pins.yaml` (file,
   URL, exact size, sha256). Trust is TLS to download.kiwix.org at the
   moment it runs, frozen by the sha256. `--check` asks every pinned
   `.meta4` again (the weekly pin review); `--check --offline` checks the
   file against the list in the test suite. At plan time every book not yet
   installed is asked for by `HEAD`; a 404 refuses the plan, exit 2, naming
   the generator, and the newer file is **never** taken in its place.
4. **The plan prints each book's date, size and licence before the
   confirmation**, as the fetch step's description. Each book is fetched,
   checked against its pin, copied into
   `<prefix>/share/hammunition/data/kiwix-library/` re-hashed, and its
   cached download deleted as its own step (a book can be 127 GB). A book
   present at its pinned name and size is not fetched again; a `.zim` no
   chosen book names is removed as its own step. Disk is checked with each
   book counted twice, together with the same run's map data.
5. **`hammunition reference serve` binds 127.0.0.1 only.** A
   standard-library page on port 8480 lists the books, the forms (served
   from `/forms/` by exact name only) and how to use the dictionaries; it
   starts `kiwix-serve --library -i 127.0.0.1 -p 8481 -r /wiki -b -M -a
   <pid> <library.xml>` as a child, **always with `-i 127.0.0.1`** (a test
   holds it), and stops it on Ctrl-C; a child that exits stops the page,
   exit 1. The library file is rebuilt by `kiwix-manage` in the operator's
   cache on every start: parsing a downloaded file is the reader's
   business, as the operator, never root's (D-057's line). It refuses root.
6. **`update`**: offline, `kiwix-library` is up to date when every chosen
   book's pinned file is installed and behind the pin when a chosen book is
   installed at another date. `--upstream` (probe method `kiwix`) asks
   Kiwix's OPDS catalogue per chosen book: the pinned file still newest is
   *current*; a newer one is *newer upstream*, warning that the pin goes at
   the next publication; a pinned `.meta4` answering 404 is the new state
   *pin expired*.
7. **The ICS forms are pinned by Hammunition's own sha256**, measured on
   two fetches, FEMA's URLs kept as it serves them; the licence is "US
   federal work, public domain (17 USC 105)". Both published versions of
   the 221 are carried.

### Not carried, each for its reason

wikiHow (no current Kiwix file; the archive's copies stopped in 2023; CC
BY-NC-SA). The `zimgit-*` prepper collections (post-disaster, medicine,
water, knots, food preparation): no licence stated in the files or by their
publisher, `Creator=Various`; a documented gap until one is stated.
energypedia: its licence page returned nothing that could be verified.
Project Gutenberg whole: 221 GB; its military-science class
(`gutenberg_en_lcc-u`) is offered. Video-channel ZIMs and MedlinePlus:
mixed licences (MedlinePlus includes A.D.A.M. content that is not public
domain). nhs.uk: Crown copyright, not verified. ARRL and ARES material,
ETC's ARRL band chart among it: ARRL copyright. A sigidwiki ZIM: none
exists, and `artemis` carries the database. Debian's Direwolf manual: the
`+dfsg` package strips upstream's PDFs; upstream's `wb2osz/direwolf-doc` is
the route, not measured. A `docs` profile of the 16 radio `-doc` packages
(about 420 MB, 309 MB of it `gnuradio-doc`) was measured and is left for
later.

### Corrections the spike made

- `zim-tools` 3.5.0 in Debian 13 ships `/usr/bin/zimwriterfs`: "no
  `zimwriterfs` in Debian 13" was true of the package name only.
  `scripts/gen_etc_inventory.py`'s note says so, and
  `docs/reference/etc-inventory.md` takes it at its next regeneration (it is
  generated from a gitignored clone this run did not have);
  `docs/reference/dispositions.md` is corrected in place.
- libzim 9.2.3, Debian 13's, reads the format-6.3 ZIMs Kiwix publishes today
  (`zimcheck -C` passed on the ham ZIM); nothing measured needs a newer one.
- `hamradio-maintguide` is the Debian Hamradio team's packaging guide, not
  operator documentation; `docs/reference/prior-art.md` says so.

### What has run

The test suite: the allow-list and pin parsers, the generator against a
faked library and `.meta4`s (and its offline check falsified by an unpinned
book), the plan's deferral and refusal, the backend against a loopback-served
file, `install --dry-run` refusing a 404 pin and printing each book's size
and licence, `update` offline and upstream, the landing server on loopback
(forms by exact name only, `..` refused), `run` with a fake child through
Ctrl-C and a child that exits. On the development host: the pin file
generated (27 rows; 26 equal to the spike's own measurement, the 27th new);
`--check` against Kiwix; the 76 MB ham ZIM fetched, verified, installed and
its cache pruned through the backend, a second plan empty; all 39 ICS forms
through the data backend; and `reference serve` against the archive's
kiwix-tools 3.7.0 unpacked from its `.deb`: both listeners on 127.0.0.1
only, a book, a full-text search and a form answering 200, Ctrl-C stopping
kiwix-serve, and SIGKILL of the parent stopping it through `-a`. **Not yet
run:** a real `install reference` on a target, and the pages opened in a
desktop browser; that is the field laptop's bench session.

**Rejected.** One manifest per ZIM (27 manifests for one list, and the
file names change on Kiwix's calendar). Offering the whole 3,630-book
library (a book with no known licence has nothing to print). Following
Kiwix's newest file when a pin dies (an unmeasured sha256). Writing
`library.xml` at install time (runs a parser of downloaded data as root).
Reverse-proxying kiwix-serve through the page for one port (more code for
nothing a person sees). Binding anything but 127.0.0.1, including behind a
flag: another machine reaches it over `ssh -L`.

---

## D-067 — Phone maps from the laptop: Mapsforge maps and POI files per region, one pinned writer, a folder with a SHA256SUMS, and routes the engine never runs

**Date:** 2026-09-29. **Status:** accepted (the design approved in
conversation on 2026-09-29, from the spike's report; written for the record
in `docs/superpowers/specs/2026-09-29-phone-maps-design.md`). **Depends on:**
D-057 (regions are station data; derived data by a converter enum), D-061
(converters run as the operator through one `Staging`, with a ledger of their
own), D-024 (a pin carries our judgement), D-049 (data is read, never
executed), D-059 (one JSON document per command), D-003 (profiles are flat
and overlap). **Amends:** D-057's converter enum, with two members
(`mapsforge-map`, `mapsforge-poi`), and the `derived` block, with an optional
`tool`.

**Why.** Phones lean on the network for their maps. The field laptop already
downloads and verifies the station's regions; with the phone network down, a
team's phones should navigate on the same maps, made ahead of time on the
laptop from the same downloads. The spike (2026-09-29) measured every phone
format there is a generator for, on Geofabrik's Delaware extract (22,139,742
bytes) in a rootless container from `parrotsec/core` with no network, and no
maintainer region.

### What is carried

| Unit | What | Measured on Delaware |
|---|---|---|
| `mapsforge-map` | converter `mapsforge-map`: the archive's osmosis 0.49.2 with `libmapsforge-java` 0.20.0's map writer, `type=hd` | 3:38, 1.04 GB with `-Xmx2g`, 17,127,551 bytes (0.78×), map file version 3 |
| `mapsforge-poi` | converter `mapsforge-poi`: the same osmosis with Maven Central's `mapsforge-poi-writer` 0.25.0 | 0:23, 0.47 GB, 4,546,560 bytes (0.21×), 22,785 POIs |

Both are derived from `osm-regions` and depend on `osmosis` and
`libmapsforge-java` from the archive; Java arrives through osmosis's own
dependency, as `mkgmap`'s does. The Garmin `.img` that `osm-garmin` (D-061)
already builds is the third phone file; nothing new is built for it.

**Debian's osmosis does not load either writer.** `/usr/bin/osmosis` loads
`/usr/share/osmosis/*.jar` through its classworlds file, and the writers are
in `/usr/share/java/`, so `--mapfile-writer` fails with "Task type
mapfile-writer doesn't exist". The engine runs `java -Xmx2g
-Djava.io.tmpdir=<work> -cp <classpath> org.openstreetmap.osmosis.core.Osmosis
-q --rbf file=<pbf> --mapfile-writer file=<out> type=hd` (and `--poi-writer
file=<out>`, with `-Dorg.sqlite.tmpdir=<work>` too), the classpath being
osmosis's jars and the named `/usr/share/java` jars the spike's scripts ran
with and reported none missing (`MAP_JARS`, `POI_JARS` in
`src/hammunition/backends/mapsforge.py`). No system file is changed. The
temporary-directory properties put the writer's scratch, and the native
library sqlite-jdbc unpacks at run time, into the working directory, where
they are counted and cleared, never in `/tmp`. The classpath is resolved when
the conversion runs, after apt; a jar that is missing fails the region by
name before `java` starts, because at plan time on a fresh machine none of it
exists yet.

Everything else is D-061's shape: one working directory per region under one
lock, as the operator; the output checked by its effect (non-empty, and
starting with `mapsforge binary OSM` or `SQLite format 3`, read by the
operator's own `head -c` and compared as hex in the same shell, so only an exit
status comes back: nothing java wrote is decoded, and `flock --verbose`'s own
lines, which it writes to stdout, are never compared); published into
`<prefix>/share/hammunition/data/<unit>/<slug>.{map,poi}` re-verified, with a
`.source` sidecar naming the snapshot and the converter; a region dropped
from station config removed as its own step; a failure recorded in a phone
ledger whose step fails the run by name, last. With no regions set both units
are deferred by name, with no new code.

### One pin, carried on the block

The POI writer is packaged nowhere: Debian's `mapsforge-poi` jar holds only
the storage classes. `mapsforge-poi` pins Maven Central's
`mapsforge-poi-writer-0.25.0-jar-with-dependencies.jar`, 18,827,962 bytes,
by sha256 `85dd23488511f51a710139dffc8c622d184ea93e817c4ec27dde1c222b7432a7`,
measured for this decision by downloading it once (2026-09-29); it matched
Central's own `.sha256`. Central also serves an `.asc` by key ID
`B51D6498DA0031B6`, whose owner was not verified: the manifest records it,
the engine does not check it, and the fetch step says so in the words every
declared-but-unverified signature gets (`signature_gap`). It is a release
tag, so no `pin_review` is needed (D-024); a new release is a manifest change,
and every POI file is rebuilt because its sidecar names the jar's digest.

**The map's converter record does not follow the archive's writer.** It is
`mapsforge-map 1` whatever `libmapsforge-java` version is installed, so an
apt upgrade of the writer does not rebuild existing maps; the record is bumped
when Hammunition's argv changes. Folding the installed package version in is
the route if an upgrade is ever measured to change the output.

**The spike's summary gave the wrong digest.** It listed `c132d97d…4b68` for
the POI writer; its own saved `.sha256` files show that digest is the
**map-writer** jar's. The pin is the one measured here.

The pin travels as a new optional field on the `derived` block, `tool`
(`ConverterTool`: an artifact with url and sha256, a size, a licence), required
for `mapsforge-poi` and refused on every other converter
(`CONVERTERS_WITH_TOOL`). The catalog supplies where the jar is and what it
hashes to; the engine owns how it is run, as it owns every converter's
command line. It is fetched into the shared cache and verified, its size
checked, and installed at `<prefix>/share/hammunition/mapsforge-poi/<jar>`,
0644, re-verified on the way in. **Not under the unit's data directory**,
because D-049 says a data directory holds files that are read and never
executed; `uninstall` removes the unit's own directory beside it. The jar
bundles sqlite-jdbc 3.43.0 with native libraries for x86_64, aarch64 and
arm, and Guava; each is under its own licence in the jar's `META-INF`.

### Disk and time

Output factors from the one region: 0.78× for a map, 0.21× for a POI file.
Scratch was not measured directly; the best number there is is GNU time's
count of blocks each run wrote, output included: 318 MB for the map (14.4×
the download) and 9.4 MB for the POI file (0.43×). A temporary file deleted
before it reached disk would not add to that count, so it is not a bound. The
plan allows about **15×** and **0.5×**, prints "measured on one region" beside
every factor, and adds them, with the jar's 18.8 MB once, to the disk check
piece 1 and piece 2 already make; a refusal names the phone factors. At the
measured rate (Delaware's 22 MB in 3 min 38 s) a large region's map takes
hours; that is the reason for the profile below.

### `hammunition maps phone`

Copies every installed `.map`, `.poi` and Garmin `.img` into
`$XDG_DATA_HOME/hammunition/phone/` (0700) as `<slug>.<ext>`, hashing each
source as it is copied and each copy after, and writes `SHA256SUMS` in
`sha256sum -c` format. A copy that already hashes the same is left alone; a
file the previous run listed in its `SHA256SUMS` whose region is gone is
removed; nothing else in the folder is touched, a `.map` the operator put
there included. The sums are written before any removal, so a removal that
fails leaves them matching the files. Refused before any copy: root, a symbolic link or a non-directory
in the folder's place, too little room. `--json` prints a `phone` document.

It then prints four routes, **none of which the engine runs**:

1. **The laptop's hotspot and a web server bound to the hotspot's
   address.** `nmcli device wifi hotspot`, then `python3 -m http.server 8000
   --bind 10.42.0.1 --directory <folder>`, after the same line with
   `--bind 127.0.0.1` to check the listing on the laptop. 10.42.0.1 is what
   NetworkManager's shared mode gives the laptop unless it is configured
   otherwise, and the text says to confirm it with `ip -4 addr show`. It is
   bound to that one address so the files go out on the hotspot link and not
   on a hotel or office network the laptop has also joined; `http.server`'s
   own default is every interface, and the text says never to run it without
   `--bind`. Nothing to install, every phone has a browser, many phones at
   once; plain HTTP on a local link, which is why `SHA256SUMS` is served
   beside the files.
2. **USB, MTP.** `kio-extras`, which Plasma installs (Dolphin shows the
   phone set to "File transfer"); elsewhere `gvfs-backends`, `jmtpfs` or
   `mtp-tools`.
3. **`adb`, opt-in.** The package `adb` brings `android-udev-rules`, a
   system modification the text names; the phone needs USB debugging.
4. **KDE Connect, opt-in.** The package `kdeconnect`; the phone needs the
   app, installed while it had internet, and pairing.

Which apps read the files is quoted from their own documentation only:
Cruiser, Locus Map, OruxMaps and c:geo for Mapsforge maps and POI files; a
Garmin handheld, and OruxMaps, for `.img`.

### The profile: `phone-maps`, not `navigation`

A new flat profile, `phone-maps` (`osm-regions`, `mapsforge-map`,
`mapsforge-poi`), post-1.0 like `navigation`. The two units do not join
`navigation`: that profile is the laptop's own navigator, and every member of
a profile is built for every region, so joining would add hours per large
region for phones an operator may not have. Profiles overlap (D-003); the
regions are shared, so a machine with both downloads each region once.
`navigation` is unchanged.

### Not carried, each with its route

- **OsmAnd `.obf`.** The best single phone file (map, routing, address and
  POI: 3:40, 3.1 GB, 50 MB on Delaware, offline), but its generator,
  `OsmAndMapCreator-main.zip`, is a 152 MB nightly replaced daily with no
  checksum, no signature and no tag (`osmandapp/OsmAnd-tools` has neither
  tags nor releases). It cannot be pinned, and the project does not mirror.
  **Route:** build OsmAnd-tools from a source commit under D-024; a Gradle
  build pulling a large Maven graph, not attempted.
- **Organic Maps and CoMaps `.mwm`.** The generator must come from the same
  release as the app ("the application does not support maps built by a
  generator_tool newer than the app"), and a coastal region needs the whole
  planet's coastline or has no sea. A pinned C++ tree per app release is a
  maintenance line this project refuses. **Route:** the publishers' own
  `.mwm` files, checked by their per-file hashes (BLAKE3 truncated to 9
  bytes for Organic Maps, SHA-1 for CoMaps, whose mirrors answer HTTP 200
  with an HTML page for a missing file); that is separate work, not yet
  decided.
- **PocketMaps.** Measured possible (GraphHopper 0.13.0 with a `.map`: 1:34,
  0.9 GB, 47 MB zipped), not carried: a 2019 engine for an app with no
  commit since 2024-10.
- **Transportr.** It asks online transit services for every query and
  stores nothing on the phone; there is nothing to build.

### What is measured, and what is not

Measured: the spike's runs above, in a container, offline, on one region;
the POI writer's digest and size (2026-09-29); the engine's argv, classpath,
effect checks, ledger, disk arithmetic, the phone folder and its
`SHA256SUMS` (`sha256sum --check` run in the tests), against a fake `java`
on `PATH` (`tests/test_mapsforge.py`, `tests/test_phone_plan.py`,
`tests/test_phone.py`, `tests/test_phone_catalog.py`,
`tests/test_mapsforge_schema.py`).

Not measured, and not claimed until the bench records it:

- **any of these files loaded on a real phone**, in any app;
- the converters through the engine on a real osmosis: `osmosis` and
  `libmapsforge-java` are not installed on the development host, and this
  work installs nothing there;
- the factors on a second region, or on one larger than Delaware;
- the hotspot route's address on the field laptop, and each transfer route
  end to end;
- which Android apps read Mapsforge files, beyond their documentation.

**Found on the way.** `scripts/check_artifact_urls.py` read every block's
`source` as an artifact; a derived block's `source` is a unit name, so the
sweep raised `AttributeError` on the first map unit (since D-057). It now
takes only artifacts, and the POI writer's pin is swept with the rest; a test
runs it over the catalog.

**Rejected.** Joining `navigation` (hours per region for phones the operator
may not have). Installing the jar under the data directory (D-049). Running
Debian's `/usr/bin/osmosis` with a changed classworlds file (a system file
changed for one program). Resolving the classpath at plan time (it does not
exist before apt). Serving the files from the engine, or `http.server`
without `--bind`. OsmAnd's nightly as a binary pin.

**Consequences.** `ConverterTool`, `CONVERTERS_WITH_TOOL` and the two
converters in `src/hammunition/manifest/schema.py`;
`src/hammunition/backends/mapsforge.py`; `src/hammunition/phone_plan.py`;
`src/hammunition/phone.py` and `src/hammunition/interface/phone.py`; `maps
phone` in `src/hammunition/cli/main.py`; the phone note in
`combined_shortfall`; the tool directory in `uninstall`; the phone units in
`update`'s rebuild command; `catalog/packages/mapsforge-map.yaml`,
`catalog/packages/mapsforge-poi.yaml`, `catalog/profiles/phone-maps.yaml`.
The operator's page is `docs/guides/offline-navigation.md`, section 14, and
the CLI's is `docs/reference/cli.md`.

---

## D-068 — Official topographic maps: USGS US Topo sheets for the station's US regions, chosen from a carried index, checked against the publisher's ETag, and made one QMapShack map; FSTopo and 3DEP next; second trail layers not carried

**Date:** 2026-09-29. **Status:** accepted (maintainer, 2026-09-29, on the
spike report on official park, forest and topographic data beyond OSM; the
design is `docs/superpowers/specs/2026-09-29-official-topo-design.md`).
**Depends on:** D-061 (QMapShack, terrain, converters run as the operator
through `Staging`, one terrain ledger), D-057 (regions are station data;
derived data by a converter enum; the publisher's MD5 disclosed, never
silent), D-049 (offline data is a catalog unit), D-031 (verify the effect),
D-021 (state the licence). **Amends:** D-049's install methods, with
`topo-quads`; D-061's converter enum, with `ustopo-mosaic`.

**Why.** OpenStreetMap already carries the trails' lines, and the spike
measured how completely: 97.5 % of Shenandoah National Park's official
trail mileage lies within 25 m of an OSM way, 96 % of a George Washington
and Jefferson National Forest sample. What official sources add is
authority: the name on the signpost, the printed-map look people trust,
and, for elevation, bare earth. The USGS US Topo sheet carries the first
two at once, public domain, with no schema for the engine to interpret.

### What is carried

| Unit | Method | Installs |
|---|---|---|
| `usgs-ustopo` | `topo-quads`, `provider: usgs-ustopo` | `data/usgs-ustopo/<stem>_<date>.tif`, and `<slug>.quads` per region |
| `ustopo-qmapshack` | `derived`, `converter: ustopo-mosaic` | `data/ustopo-qmapshack/quads/<stem>_<date>.tif`, one `ustopo.vrt`, `quads.source` |

Both join the `navigation` profile. A region outside the United States and
its territories touches no sheet: its plan line is a `note:` naming it, and
nothing fails.

### Which sheets: a carried index, selected at an eighth of a degree

USGS's `ustopo_current.csv` (in `ustopo_current.zip`, 10.5 MB, rebuilt
daily) lists every current quad with its box and dated file name; the TNM
Access API lists only the PDFs, and the GeoTIFFs sit beside them in the
`prd-tnm` bucket. `scripts/gen_ustopo_index.py --fetch` joins the CSV with
a listing of the bucket's GeoTIFF prefix (273 pages, 272,910 objects, every
edition since 2009) and writes `catalog/data/ustopo-quads.txt`: one line
per quad, its box, size, S3 ETag and `<ST>/<stem>_<date>`. The 2026-09-29
index: 65,240 quads, 64,423 at their current edition, and **817 whose
current (2026) edition is published as a PDF only**, carried at their
newest GeoTIFF edition and counted in the header; none left out. The box
is carried as decimals, because 6,638 quads (Alaska, oversized and
off-grid sheets) are not on the 1/8-degree grid. The file is 6.3 MB of
plain text: `*.gz` is ignored repository-wide, and a line-based file keeps
a regeneration reviewable.

A region's sheets are those whose box overlaps a 1/8-degree cell its
Geofabrik outline touches: `copernicus.squares_touching` gained a
`per_degree` argument and is called at 8. The outline is the one the
terrain already fetches, and one plan asks for it once (`MemoProbe`). The
answer is recorded as whole index rows in `<slug>.quads`, so an offline
plan can verify, warp and remove from the record alone. A record naming an
edition the index has since replaced is re-selected from the outline;
offline it is kept, with a note, and what is installed stays installed.

`--check --offline` checks the carried file's shape and header counts and
runs in the test suite. `--check` lists the bucket and fails naming each
carried quad that is gone or whose size or ETag changed, since that breaks
an install; a newer edition appearing is counted and does not fail it, or
the weekly job would be red on USGS's publishing calendar and be ignored.

### Verification: the publisher's ETag, carried and re-asked

Every sheet about to be fetched is asked for with a `HEAD` at plan time and
must answer 200 with the size and ETag the index carries; otherwise the
plan refuses, naming it and `scripts/gen_ustopo_index.py --fetch`. The
download must reproduce the ETag (`src/hammunition/s3etag.py`,
`Fetcher.fetch_etag`): a single-part upload's is its MD5; a multipart
upload's is the MD5 of its parts' binary MD5s, and since the part size is
not published, each whole-MiB size from S3's 5 MiB minimum that splits the
object into that many parts is tried, 8 and 5 MiB first. None reproducing
it is a named failure, never an unverified pass. 13,209 of the current
GeoTIFFs are multipart. The installed copy is re-verified on the way in
against the sha256 taken as the bytes arrived, and the cached copy deleted.

The plan says **"MD5 from the publisher's object metadata; not pinned by
Hammunition"** for every sheet, the Copernicus wording: the ETag is the
publisher's, and no sha256 was measured by Hammunition. Editions are years
old (Vermont 2024, Delaware 2023), so sha256 pins could be grown later as
`gen_copernicus_pins.py` grows them; this decision does not.

### The converter: warp each sheet, crop its collar, one VRT

A US Topo GeoTIFF is the whole 300 dpi page, collar included, each sheet in
its own Transverse Mercator: `gdalbuildvrt` cannot mosaic them as they
come, and stacked uncropped each collar hides its neighbour. Per sheet, as
the operator, in `~/.cache/hammunition/build/ustopo-qmapshack/ustopo.work/`
under one lock:

```
gdalwarp -q -overwrite -t_srs EPSG:3857 -te_srs EPSG:4269 -te <w> <s> <e> <n> \
  -r bilinear -co COMPRESS=JPEG -co PHOTOMETRIC=YCBCR -co TILED=YES <sheet> <out>
gdaladdo -q -r average --config COMPRESS_OVERVIEW JPEG \
  --config PHOTOMETRIC_OVERVIEW YCBCR <out> 2 4 8 16
```

then one `gdalbuildvrt` over every warped sheet by absolute path, published
as `ustopo.vrt`. `hammunition maps qmapshack` adds the directory under
`[Canvas] mapPath`; QMapShack lists `*.vrt` there and does not look below.
Outputs are checked, not exit statuses; failures go into D-061's terrain
ledger, whose one last step fails the run by name; a sheet no region needs
loses its warped copy, and with no sheet needed at all the VRT goes too,
rather than name files just removed.

### Measured, and not

Measured on 2026-09-29 on the development host: the index and the join
above; one Delaware sheet end to end through the engine's own code
(`DE_Newark_East_20230603`, 9,227,638 bytes, a two-part upload): the `HEAD`
check, the download, the ETag reproduced at **8 MiB** parts, then the argv
above: `gdalwarp` 2.5 s to 6.0 MB, `gdaladdo` 0.9 s to 8.9 MB, the corners
exactly the sheet's box (75°45′–75°37′30″ W, 39°37′30″–39°45′ N), the
collar gone in a rendered thumbnail. The files were deleted after. The disk
factor, 1.0× the download for the warped sheet and as much again of
scratch, is printed "measured on one quad". A test runs the real GDAL on a
synthetic Transverse Mercator page and was seen red with the crop broken.

**Not measured: QMapShack drawing the mosaic.** The spike's one run opened
the VRT and listed it in the Maps dock, and the canvas stayed blank at the
configured focus; why is unknown. It is owed by the bench, **in a virtual
machine, never on the maintainer's desktop**: a scratch `HOME` does not
isolate QMapShack, whose single-instance socket `/tmp/QMapShack-<user>` is
shared with the desktop's instance, and the spike's `pkill -x qmapshack`
killed the maintainer's own QMapShack twice. Also not run: the whole
install of the sheets through `hammunition install` on any machine.

### Next: FSTopo and 3DEP

Not built on this branch; the spec's section 8 is their plan.

- **`usfs-fstopo`**, a second `topo-quads` provider: the Forest Service's
  7.5′ series over National Forest land, with **trail numbers**. Index from
  `FSTopo_Index_GTAC`; fetched by `downloadMap.php?mapID=<secoord>`. **No
  checksum is published**, so each sheet is sha256-pinned by Hammunition
  where the maintainer has measured one, and otherwise disclosed as
  unverified in the plan, by name. The generator starts with no pins. It is
  collarless in EPSG:4269, so its converter is a plain `gdalbuildvrt`.
- **`dem-3dep`**, a second `dem-tiles` provider, opt-in through
  `--dem-source copernicus|3dep` (default `copernicus`). Copernicus GLO-30 is
  a surface model: over a forested window in Shenandoah it reads **11.8 m
  above 3DEP on average** (σ 6.6 m), the canopy; 3DEP is bare earth. 488 MB
  a tile against 46 MB. `gdal-dem` needs three changes: tiles named by the
  north-west corner, its own tile list, and `PIXELS` of at least 10,812.

### Not carried, and why

- **NPS, USFS and state trail lines as a second vector layer.** OSM has
  97.5 % (SHEN) and 96 % (GW&J sample) of the official mileage within 25 m,
  plus more; a duplicate layer draws every trail twice, and the sheets
  already show the official names. REST output has no fixed bytes, and the
  USFS national files are rebuilt weekly with no checksum.
- **1 m lidar**: 26.9 GB for one SHEN project.
- **GeoPDF**: six times the GeoTIFF, and rasterised anyway.
- **Historical Topo (HTMC)**: historical interest; 9.4 GB for Vermont.
- **Park PDF maps**: no coordinate system and no neatline; placing them by
  hand is not the engine's job.
- **BLM and USFWS layers**: REST queries only, no checksum.
- **PAD-US**, for now: ScienceBase's checksum field is null.
- **NPS points of interest and boundaries as GPX**: deferred to a later
  piece, pending a checksum (the Data Store boundary FGDB can be
  sha256-pinned; the REST points cannot).

Outside the US, OSM stays the trail layer. OS OpenData publishes an MD5 per
file, which is the pattern a UK source would follow; CanVec publishes none;
the EU has no single source.

**Final review (2026-09-29), fixed before merge.** An older edition was
removed before its newer one had installed, so a failed fetch left a hole in
the map until the next online run: it is now removed only once the new file
is on disk, the step runs after the installs, and the mosaic keeps the older
warped copy in the VRT meanwhile (I1). A current record now takes the
index's size and ETag, so a re-uploaded object under the same name is not
checked against a stale ETag forever (M1). A VRT whose every sheet failed to
warp is removed (M3). `--check --offline` also checks the header's CSV row
count (M5), and a GeoTIFF newer than the CSV's own edition is counted as
current, not older (M6). Left as known gaps: a region's record never picks
up a sheet USGS adds inside it while every recorded sheet is still indexed
(rare; changing the region re-selects), and `usgs-ustopo` reads "already
installed" in the plan when only a record or a removal is written, the
`dem-tiles` rule of D-061.

**Rejected.** The TNM Access API at plan time (online, and it lists only
PDFs). A gzipped index (`*.gz` is ignored repository-wide, and a regenerated
diff would be unreadable). Failing the weekly check on a newer edition.
Keeping only the warped copies (a converter change would need every sheet
downloaded again).

**Consequences.** `TopoQuadsInstall` and the `ustopo-mosaic` converter in
`src/hammunition/manifest/schema.py`; `src/hammunition/ustopo.py`,
`src/hammunition/s3etag.py`, `src/hammunition/topo_plan.py`,
`src/hammunition/backends/topo.py`, `src/hammunition/backends/topo_mosaic.py`;
`Fetcher.fetch_etag`; `squares_touching(per_degree=)` and
`S3Probe(bucket=)` in `src/hammunition/copernicus.py`; the US Topo part of
the plan's Terrain block and its JSON (`TopoSectionView`); the sheet count
in `update`; the mosaic directory in `maps qmapshack`; the generated
`catalog/data/ustopo-quads.txt` and `scripts/gen_ustopo_index.py`, checked
weekly; `catalog/packages/usgs-ustopo.yaml`,
`catalog/packages/ustopo-qmapshack.yaml`, `catalog/profiles/navigation.yaml`.
The operator's page is section 15 of `docs/guides/offline-navigation.md`.

### D-068 amendment (2026-10-01): FSTopo and 3DEP, built

The "Next" above, built on branch `official-topo-2` from the spec's
section 8 and its dated note (plan:
`docs/superpowers/plans/2026-10-01-official-topo-2.md`). Two provider
members on the existing methods, not new methods: `topo-quads` gains
`usfs-fstopo`, `dem-tiles` gains `usgs-3dep`. `dem-3dep` joins
`navigation`; `usfs-fstopo` does not (below). **Amends** D-061's `gdal-dem`
with an optional `alternative` input and this decision's `ustopo-mosaic`
with an optional `fstopo` input, each checked catalog-wide for its provider;
`fstopo` is read when its unit is installed and is never a dependency.

**FSTopo is installed by name only, until pinned (maintainer, 2026-10-01).**
CLAUDE.md's security rule, "verify checksums/signatures for any non-apt
source; refuse to install if absent", is the maintainer's standing rule, and
a profile default does not route around it. So `usfs-fstopo` is in no
profile and is planned only when the operator types it, with the per-sheet
"unverified" plan lines and the warning that counts them. `ustopo-qmapshack`
does not depend on it: it builds and keeps `FSTopo.vrt` only from FSTopo
sheets already installed (read from their regions' records, offline, when
the unit is not in the plan), so US Topo works alone. **The condition to
rejoin `navigation`:** every sheet a region needs is pinned in
`catalog/data/fstopo-pins.yaml` (`scripts/gen_fstopo_index.py --pin`), and
the plan then says "every FSTopo quad your regions need is pinned by
Hammunition"; the unit may rejoin by a further dated amendment to this
decision, never as a side effect. `dem-3dep` stays in the profile: its S3
ETag is the publisher's own check, the Copernicus precedent.

**FSTopo (`usfs-fstopo`).** `scripts/gen_fstopo_index.py` reads the ArcGIS
layer `FSTopo_Index_GTAC` (19 pages) and writes
`catalog/data/fstopo-quads.txt`: box, `secoord`, vintage (the layer's
two-digit edition, 0 for the 6,560 of 18,188 it leaves empty), postal code
and name. Generated 2026-10-01 from the layer as last edited 2025-08-08:
18,187 quads, one left out with no `secoord` (an Arkansas cell with no
polygon). A region's sheets are chosen by eighth-degree cell as US Topo's
are. At plan time each sheet to fetch is located through the raster
gateway's one redirect, followed by the plan itself and refused unless it
leads to a `.tif`/`.tiff` under `https://data.fs.usda.gov/geodata/rastergateway/`,
and the file is asked its size. **The Forest Service publishes no
checksum.** `catalog/data/fstopo-pins.yaml` carries a sha256 and size per
sheet the maintainer measured (`--pin SECOORD`, which downloads); it starts
empty. A pinned sheet is checked against its pin ("sha256, pinned by
Hammunition (the Forest Service publishes no checksum)") and refused by
name when the gateway announces another size. Any other sheet is fetched
**unverified** by `Fetcher.fetch_sized` -- its announced size and a TIFF
header checked, no cached copy ever reused -- and the plan says so on the
sheet's own line and counts them in a warning. That is why the unit is
installed by name only (above): disclosure alone does not satisfy the
checksum rule for a default.

**The converter changed from the spec's plain `gdalbuildvrt`** (the
spec's section 8 note of the same date): the spike's sheet is stored in
strips (`Block=8951x1`) with no overviews, and `gdalbuildvrt` over two
paletted GeoTIFFs whose palettes differ exits 0, keeps the first palette for
both and warns "The end result might produce weird colors" (measured on
synthetic sheets). So `ustopo-mosaic`, when its block names `fstopo`, runs
per sheet `gdal_translate -q -expand rgb -co TILED=YES -co COMPRESS=JPEG
-co PHOTOMETRIC=YCBCR` and US Topo's `gdaladdo` 2 4 8 16, publishes
`<data>/ustopo-qmapshack/fstopo/<name>.tif` with a sidecar
(`ustopo-mosaic fstopo 1`), and builds `FSTopo.vrt` beside `ustopo.vrt`,
recorded in `fstopo.source`. `maps qmapshack` already lists that directory,
so QMapShack gets a second map, `FSTopo`, with no change to its
registration. A test over two synthetic paletted sheets keeps each sheet's
colour and goes red without `-expand rgb`.

**3DEP (`dem-3dep`).** `scripts/gen_3dep_tiles.py` lists the bucket's
`StagedProducts/Elevation/13/TIFF/current/` prefix (5,967 objects, six
pages) and writes `catalog/data/usgs-3dep-tiles.txt`: 1,449 tiles with size
and ETag, the newest object dated 2026-09-17. Tiles are named by the
north-west corner (`src/hammunition/usgs3dep.py`); a region's squares are
its outline's, as for Copernicus. Each tile to fetch is HEAD-checked against
the list and the download must reproduce the S3 ETag; the plan says "MD5
from the publisher's object metadata; not pinned by Hammunition". The
CRC-64/NVME the 2025 objects also carry is not used: no standard-library
implementation, a pure-Python one ran at 6.6 MB/s (over a minute a tile),
and the ETag covers every object. 3DEP is fetched **only** when the
station's new `dem_source` is `3dep` (`hammunition station set
--dem-source copernicus|3dep`, default `copernicus`); otherwise
`dem-3dep` resolves nothing, asks nothing, removes any 3DEP tile it
installed, and the plan says so. With `3dep`, `gdal-dem` draws from the
unit its block names as `alternative`, rasterises a tile at 10,812 pixels
(`PIXELS` per provider), and writes `elevation: usgs-3dep` into
`tiles.source`, so a change of source never reads as current; a Copernicus
record is byte for byte what it was, so no existing install rebuilds. The
plan prints 3DEP's size per region before the confirmation.
**Copernicus stays the default and stays installed either way**: it is a
tenth the size, BRouter's elevation reads it (its builder parses Copernicus
names), and it covers regions 3DEP does not; a region outside the US gets a
warning that QMapShack has no elevation for it while `3dep` is chosen.

**Measured on 2026-10-01**, through the engine's own code, files deleted
after: one George Washington National Forest FSTopo sheet (`secoord`
382207907), gateway located in 1.0 s, 21,194,737 bytes, the same sha256 as
the spike's copy of 2026-09-29, expanded in 7.0 s and given overviews in
3.6 s to 24,546,537 bytes (1.16, carried as 1.2, "measured on one sheet");
one Shenandoah 3DEP tile (`USGS_13_n39w079`), HEAD 0.4 s, 488,059,861
bytes, ETag `…-94` reproduced and the spike's sha256 again, traced in
17.3 s to a 211.5 MB GeoPackage and rasterised at 10,812 pixels in 3.4 s to
8.4 MB, its corners exactly the degree. The first attempt at the tile
stalled mid-download (a 60 s read timeout); the partial was deleted and the
retry completed.

**Not measured:** QMapShack drawing `FSTopo.vrt`, or drawing hillshade
from a 3DEP `dem.vrt`; owed by the bench, in a virtual machine, never on
the maintainer's desktop (the spike's `pkill` incident above). Whether
every index quad has a GeoTIFF at the gateway (a quad without one refuses
the plan by name); whether any FSTopo sheet is already RGB (`-expand rgb`
would then fail it by name in the ledger); a whole install through
`hammunition install` on any machine.

**Not carried, as above:** 1 m lidar (26.9 GB for one Shenandoah
project), GeoPDF (six times the GeoTIFF, rasterised anyway), Historical
Topo (9.4 GB for Vermont).

**Consequences.** `src/hammunition/fstopo.py`, `src/hammunition/usgs3dep.py`,
`src/hammunition/backends/fstopo.py`; `Fetcher.fetch_sized`;
`TopoQuadsBackend.fstopo`, `DemTilesBackend.bare_earth`; the FSTopo half of
`src/hammunition/backends/topo_mosaic.py`; provider-aware
`src/hammunition/backends/gdal_dem.py`; `resolve_station_fstopo`,
`resolve_station_3dep`; `Station.dem_source`; the plan's `bare_earth`,
`fstopo` and `contours_from` in text and JSON; `scripts/gen_fstopo_index.py`
and `scripts/gen_3dep_tiles.py`, checked weekly;
`catalog/packages/usfs-fstopo.yaml`, `catalog/packages/dem-3dep.yaml`.


---

### D-068 amendment (2026-10-02): the selection is bounded, and a large one is asked about

**Measured (bench session 13, issue #232).** `hammunition install
navigation --dry-run` on three whole-state regions planned 7,284 US Topo
sheets, about 55 GB to download and 111 GB of disk with the warped copies,
out of 29,387 commands, and took 9 m 49 s, nearly all of it the 7,284
publisher checks. The profile cannot be installed without them, because
`install` has no exclude. "The station's US regions" read as every sheet of
every region was a defect.

**Ruling (maintainer, 2026-10-02):** "radius default, topo-all, and a
topo-regions, and can call it out specifically with a disclaimer they
consent to about the size during install; the installer should be easy to
walk through." Option 3 of the issue.

**Decision.**

- **Three station values**, none a template variable: `topo_radius_km`
  (100 when unset, `0` for none, 20000 at most), `topo_regions` (a subset of
  `map_regions`, refused otherwise, naming them) and `topo_all` (boolean).
  `station set --topo-radius-km N`, `--topo-regions a,b`, `--topo-all` /
  `--no-topo-all`; `station show` and the station document carry them
  (`--topo-regions` as a count in the text, as the map regions are).
- **The selection rule** (`hammunition.topo_bound`, pure). In order:
  `topo_all` selects every sheet of every region as before; else
  `topo_regions` with no radius set takes those regions whole; else a radius
  of 0 selects none; else the sheets whose box comes within N km (haversine,
  to the nearest point of the box) of the centre of the station's grid square
  (`hammunition.maidenhead.centre`), of the station's regions, cut to
  `topo_regions` when they are set. The index holds only US sheets, so
  non-US regions select none, as before.
- **It applies to** US Topo (`usgs-ustopo`), FSTopo (`usfs-fstopo`) and 3DEP
  (`dem-3dep`, only when chosen). **Not** to Copernicus terrain: it is a
  tenth of the size and BRouter needs every region whole.
- **No grid square** (and neither `topo_all` nor `topo_regions`): the unit
  is deferred by name (**D-035**), nothing invented, with the fix stated
  (`station set --grid-square`, `--topo-regions`, `--topo-all`), and what is
  installed is kept: the plan resolves from the regions' records, offline,
  and removes nothing.
- **Records are per bound.** A region's record (`<slug>.quads`, `.tiles`)
  gains a `# bound:` line (a digest of the circle, never the position; absent
  for a whole region, so every earlier record reads as one). A record is
  reused only under the bound it was made under: a radius's record is never
  read as the whole region by `--topo-all`. Offline, a whole-region record
  narrows to a bound with no outline.
- **Narrowing removes.** A selection smaller than the last install removes
  the installed sheets outside it, as any region no longer wanted always
  did; the plan's first note says how many ("N installed sheets lie outside
  it and are removed by this install; `--topo-all` keeps them"), so it is
  never silent (**D-022**).
- **The size consent.** When the US Topo selection is `--topo-all`, or the
  download plus the warped copies exceeds **10 GB** (decimal; the sheets a
  run downloads, the warp at the measured 1.0x), and there is something to
  download, the plan's US Topo block prints one sentence ("This installs
  7,284 US Topo sheets: about 55.4 GB to download and about 110.8 GB of disk
  once the warped copies QMapShack reads are added.") and the install asks a
  typed `yes` to that sentence, after the plan and before the ordinary
  confirmation. It has **D-021**'s shape and is not a risk gate: `--yes` is
  never read, no terminal is a refusal (exit 3), and the scripted answer is
  `HAMMUNITION_ACCEPT_TOPO_SIZE` set to the number of sheets shown (as
  **D-040**'s names the fingerprint), so a selection that grew stops the
  script. The answer is logged as `consent_affirmed` with the sentence and
  its digest. Under 10 GB the ordinary confirmation covers it.
- **The walk-through.** Before the plan, one `note:` line says what was
  chosen and how to change it: "US Topo: using a 100 km radius around your
  grid square (N sheets, about X GB); `hammunition station set
  --topo-radius-km`, `--topo-regions` or `--topo-all` change this". The
  plan's grouped rendering (**D-016**, #229) is unchanged; the issue's
  per-group cap is not taken, because a bounded default makes the lists a few
  hundred lines.

**Measured (2026-10-02).** On a synthetic index of 576 sheets around a
placeholder square, 100 km selects 253 and `--topo-all` 576. On the
maintainer's own regions, from the carried index and Geofabrik's outlines:
248 sheets, 1.74 GB to download, about 3.5 GB of disk at the default; 7,284
sheets, 55.4 GB and 110.8 GB with `--topo-all`. The default is under 10 GB
and asks nothing extra. **Not measured:** QMapShack drawing either, and the
antimeridian (an index row never wraps it, so the Aleutians east of 180 are
not reached from the west). Tests: `tests/test_topo_bound.py`,
`tests/test_topo_bound_cli.py`.

**Limits, stated.** The size consent and the "installed sheets lie outside
it" note read the US Topo selection only; FSTopo (by name, unverified) and
3DEP follow the bound but are not separately asked about, and their removals
are in the plan's steps, not in that note. `--clear-topo-regions` removes
`topo_regions`, and narrowing `--map-regions` drops a `topo_regions` entry
that is gone, with a note, so a station is never stranded invalid.

## D-069 — CoMaps is carried as a pinned source build over CoMaps' own maps for the station's regions, checked by CoMaps' own index; its missing position is written down, not faked

**Date:** 2026-09-30. **Status:** proposed (design approved by the
maintainer on 2026-09-29, as recorded in
`docs/superpowers/specs/2026-09-29-comaps-design.md`; implemented on branch
`comaps`; the maintainer decides it at review). **Depends on:** D-024 (pin
the commit a distribution builds), D-014 (backends by measurement), D-049
(offline data is a catalog unit, disclosed by size and licence), D-057
(regions are station data; a weaker check is disclosed, never silent),
D-053 (`update` is a report; `--upstream` opts in), D-043 (who owns an
installed tree), D-031 (verify the effect), D-040 (third-party archives,
for the record below), D-059 (a GUI verb has no `--json` form). **Amends:**
the git backend's scope (five build fields), D-049's install methods (with
`mwm-regions`), and D-053's upstream kinds (with `comaps_maps`).

**Why.** The navigation spike of 2026-09-29 looked for a phone-style
navigator for the laptop: one program with offline address search and
routing by car, bike and foot. Navit routes and follows the GPS; QMapShack
does trails and terrain; neither searches addresses the way a phone app
does. CoMaps and Organic Maps both do, from an index inside each map file.
Neither is in any Debian-family archive, and upstream publishes no Linux
binary; Flathub is the one upstream-endorsed Linux build.

### What was measured

On the development host (Parrot 7, Debian 13 base, 8 cores, 31 GiB), by
hand, 2026-09-29, with nothing installed system-wide:

- **The build.** `git clone --recurse-submodules --branch v2026.08.31-14`
  gave `72632e4de65a98dfed827d8e447f0287168639d0` and 193 submodule entries,
  each at its gitlink; 9.7 GB with full history, ICU's alone 5.6 GB.
  `configure.sh --skip-map-download` took 3 min 21 s; `cmake --build -j2`
  8 min 20 s, peak 1.9 GiB for one compiler process, about 3.4 cores busy
  at `-j2`; `cmake --install` 348 files, 63 MB without the World maps.
- **What the archive lacked.** `qt6-positioning-dev`, `qt6-svg-dev`,
  `optipng` and `ninja-build`, all in trixie. Python `protobuf`: Debian's
  `python3-protobuf` reports 4.21.12 and CoMaps' CMake wants >= 3.20,
  < 4.0; `protobuf==3.20.3` from PyPI with the wheel's published sha256.
- **Two traps.** CoMaps' `generate_symbols.sh` calls a bare `exit` when
  optipng is missing, so configure exits 0 with no symbols. And the World
  maps: `qt/CMakeLists.txt` installs `World.mwm` and `WorldCoasts.mwm` only
  if the tree has them, and a tree that had them as configure.sh's
  symlinks installed two symlinks into a directory it does not install.
- **It runs.** Under Xvfb, isolated from the network, D-Bus and the real
  home, it loaded Vermont and drew it (screenshot mode), and the main
  window opened on the World map. Without a pre-written
  `EulaAccepted=true` a modal licence dialog blocks the first start.
- **Position.** From the source: `location_service.cpp` builds exactly one
  Linux source, Qt Positioning's `geoclue2` plugin by name. No gpsd client,
  no NMEA reader.
- **The maps.** `https://cdn-fi-1.comaps.app/maps/2026.06.28/260830/`:
  World (53,387,231 bytes) and WorldCoasts (8,494,206) match Flathub's
  sha256 pins and the SHA-1 in `countries.txt` at the pinned commit; Vermont
  (60,883,711) matches its SHA-1. On 2026-09-29, for this record: the index
  fetched from Codeberg at the commit is byte-identical to the spike tree's
  (363,788 bytes), and a `HEAD` of `US_Delaware.mwm` answered 200 with the
  index's size (32,804,869).

### The decision

1. **`comaps` is a `git` build** at tag `v2026.08.31-14`, which must
   resolve to `72632e4…` (a new `commit` field: a tag's resolution was only
   recorded, and is now compared). D-024: Flathub, nixpkgs and the AUR build
   this commit. Apache-2.0.
2. **The git backend grows four fields, each engine-owned and each named by
   this unit** (source-build-gaps #8): `submodules` (shallow, upstream's own
   `git submodule update --init --recursive --depth 1`, then `git submodule
   status --recursive` read back and refused off a gitlink); `build_python`
   (hash-pinned lines in a venv beside the tree, on the `PATH` of prepare,
   configure and compile, never the install); `prepare` (upstream's
   `configure.sh --skip-map-download` with `SKIP_PYTHON_VENV=1`, and the
   files it must `produce`, checked); `extra_files` (the World maps from the
   CDN by sha256, and `categories_brands.txt` from the tree, installed after
   `cmake --install` with `rm -f` first and checked as regular files).
3. **Rulings.**
   - *The build Python is a field on the git block, not the venv backend.*
     The venv backend installs a program for the operator, with wrappers on
     the `PATH`; a build dependency lives and dies with the build directory.
   - *Build parallelism is the existing rule*: one job per CPU, capped at
     one per 2 GiB of memory and swap. The measured peak is 1.9 GiB; no
     per-unit override. The brief's `-j2` was the fallback for no rule.
   - *The licence answer and the map links are made at launch, per user*, by
     `hammunition maps comaps`, not at install, where root would be writing
     into a home. The answer is added only when the key is absent: the file
     is `key=value` lines and a duplicated key fails CoMaps' `VERIFY`.
   - *CoMaps writes only under XDG*, measured from `platform_linux.cpp`: its
     resource directory is read-only, and its writable directory falls back
     to the operator's data directory when the resources are not writable.
     So no D-043 hand-over; the install stays root's.
   - *`comaps` does not depend on `comaps-maps`*, as Navit and QMapShack do
     not depend on their map units; the profile carries both.
4. **`comaps-maps` is a D-049 data unit with a new method, `mwm-regions`**
   (`provider: comaps`). Its maps follow the station's map regions through
   `catalog/data/comaps-pins.yaml`, generated by `scripts/gen_comaps_pins.py`
   from `countries.txt` at the commit `comaps` pins: all 1,150 maps with size
   and SHA-1, and 262 Geofabrik regions placed by a rule (a region's last
   path component matched against its parent's CoMaps children, or a
   continent's child against the top level) plus two reviewed aliases
   (`north-america/us`, and District of Columbia as `US_Maryland_and_DC`).
   Every map is pinned, so the file says nothing about whose region
   matters. A region the rule cannot place is named in the plan and fetches
   nothing.
5. **The check is the publisher's, and says so.** Each map is fetched with
   the SHA-1 and exact size from CoMaps' index at the pinned commit; the
   plan's line reads "SHA-1 and size from CoMaps' own map index at the
   pinned commit (the publisher's check)". The size is compared exactly
   because CoMaps' mirrors answer a missing file with 200 and an HTML page,
   so a status proves nothing. The sha256 of the bytes is written into the
   step's outcome, so the transaction log carries it. Every map not
   installed is `HEAD`-checked at plan time for 200 and the pinned size,
   and an expired pin refuses the plan before apt runs.
6. **Expiry is reported.** CoMaps' CDN keeps a map version for months, not
   forever (Organic Maps' CDN kept about four months on 2026-09-29;
   CoMaps' own retention is unmeasured). `update --upstream` gains
   `comaps_maps`: it `HEAD`s the pinned `World.mwm` and reports `current`,
   `pin expiring` from 90 days after the version's date, or `pin expired`.
   The offline reference layer (D-066) added `pin expired` to the same
   module for Kiwix; this uses the same constant and wording. The weekly pin-review
   job runs `gen_comaps_pins.py --check`, which re-fetches the index and
   `HEAD`s World.mwm.
7. **The position comes through GeoClue, fed by the tether** (replaced
   2026-10-01; the amendment below has the measurement). CoMaps reads
   GeoClue2 only. The tether serves its NMEA on a unix socket as well as TCP
   10110, GeoClue's network-NMEA source reads that socket, and `hardware
   apply` writes the two root files that set it up, disclosed and reversed
   like GPS time's (D-058). A native CoMaps is a system app to GeoClue and
   needs no `[app.comaps.comaps]` entry; the entry this item first named was
   wrong. No CoMaps patch. What the bench still owes is listed below.

### Not carried

- **Organic Maps.** The same program family, built the same way; one of the
  two is enough. CoMaps is the one Flathub keeps current (Organic Maps'
  Flathub build was four months behind on 2026-09-29), and CoMaps' index
  carries a full SHA-1 per map where Organic Maps' carries a 72-bit BLAKE3.
- **Flatpak.** D-014 measured it at zero users, and nothing here changes
  that: a second Qt runtime stack of about 1.5 GB, and updates from
  Flathub's builders rather than a pin reviewed here. For a future D-040
  case, Flathub's `.flatpakrepo` embeds the signing key **6E5C 05D9 79C7
  6DAF 93C0 8135 4184 DD4D 907A 7CAE** (rsa4096, expires 2027-06-14), as
  measured on 2026-09-29.
- **Self-generated maps** from the Geofabrik extracts: possible with
  CoMaps' own generator, built from the same tree, but a state map built
  here has no coastline (that needs a planet build), no US postcodes and no
  contours. Documented, not built.

### Measured, and not

Measured: everything under "What was measured" above; the region table's
coverage (all 50 US states and DC, the whole US 15.7 GB, matching the
spike); every step of the engine's build, fetch, verification, install,
removal, launch preparation and report against fakes in the test suite.

Not measured, and owed by the bench:

- **The build through `hammunition install`**, including the shallow
  submodule fetch, its time, memory and disk.
- **CoMaps reading the linked maps** on a running desktop.
- **US address-search quality.** The desktop app has no scriptable search;
  a person at the screen has to judge it.
- The GeoClue route to a position on a real desktop (amended 2026-10-01:
  built, and the bench's list is in the amendment below).

**Consequences.** `commit`, `submodules`, `build_python`, `prepare`
(`PrepareStep`) and `extra_files` (`ExtraFile`) on `GitInstall`, and
`MwmRegionsInstall` in `src/hammunition/manifest/schema.py`; the git
backend's steps and checks in `src/hammunition/backends/git.py`;
`build_env` in `build_commands`; `Fetcher.fetch_sha1`;
`src/hammunition/comaps.py`, `src/hammunition/backends/comaps_maps.py`,
`src/hammunition/comaps_launch.py`; the extra-file effect check in
`src/hammunition/execute.py`; the deferral, the install wiring, the
`comaps_maps` upstream probe and the offline row; `hammunition maps comaps`;
`catalog/packages/comaps.yaml`, `catalog/packages/comaps-maps.yaml`, the
generated `catalog/data/comaps-pins.yaml` and its generator, and the
`navigation` profile. The operator's page is
`docs/guides/offline-navigation.md` (section 17), the CLI's
`docs/reference/cli.md`, and the build gap
`docs/reference/source-build-gaps.md` #8. Tests: `tests/test_comaps_schema.py`,
`tests/test_git_comaps.py`, `tests/test_comaps_pins.py`,
`tests/test_comaps_maps.py`, `tests/test_comaps_update.py`,
`tests/test_comaps_launch.py`.

### Amendment (2026-09-30): after the final review, and beside D-070

The branch was brought up to main, where D-064, D-066, D-067 and D-070 had
landed. What changed with it:

- **CoMaps' maps use the LAN mirror (D-070).** `Fetcher.fetch_sha1` goes
  through the same sources as every other data download: the mirror first
  at `<mirror>/comaps-maps/<version>/<id>.mwm`, then the CDN, the SHA-1 and
  exact size checked either way, and where the bytes came from recorded in
  the log. `hammunition artifacts` lists them with a new check kind,
  `sha1-publisher`, from the carried pins with no network; a region with no
  CoMaps map is listed as deferred.
- **An expired pin is told from a busy server.** Only 404, 410, or a 200 of
  another size is "pin expired", at plan time, in `--upstream` and in the
  generator's `--check`; any other answer is a server that did not say
  (`unanswered`, or a refusal saying to try again), and the weekly check
  reports an unreachable index or CDN as a problem line, not a traceback.
- **The app's World maps are tied to the index.** `gen_comaps_pins.py
  --check`, offline too, refuses when `comaps.yaml`'s World and WorldCoasts
  are not the URL and size the pinned index names, and the cadence hint says
  to move them with the tag. The weekly `check_pin_reviews.py --verify-refs`
  now fetches each tag with a `commit` and refuses one that resolves
  elsewhere.
- **A map id must be one file name** (no `/`, `\`, NUL, control character
  or leading dot), in the index and in the pin file: it becomes a path under
  the data directory and a link name in the operator's home.
- **No region with a CoMaps map** is said in the plan, never shown as
  "already installed", and `update` says why nothing is installed.
- **Coverage, counted:** 262 of the 532 Geofabrik regions Hammunition
  carries, 165 of the 197 country-level ones; not China, Russia, Ireland and
  Northern Ireland, Israel and Palestine, the DR Congo or Ivory Coast, among
  others.
- **Source-build gap #8 is not closed** until the engine's build runs on the
  bench; the fields exist and are tested against fakes.
- D-069 is recorded in number order, between D-068 and D-070, which
  landed on main first (maintainer's direction, 2026-09-30).

### Amendment (2026-10-01): "you are here" through GeoClue's NMEA socket

**Status:** built on branch `comaps-position`; the maintainer decides it at
review. It replaces item 7's position gap.

**Measured** (the GeoClue spike, 2026-10-01, on the development host, Parrot
7 on Debian 13; nothing installed, no root, the system GeoClue never
called). Debian's GeoClue 2.7.2 (`geoclue-2.0` 2.7.2-2, upstream tag
`2.7.2`, no NMEA patches in the Debian changelog) reads `nmea-socket=` from
`[network-nmea]` as a static unix-socket service (`gclue-nmea-source.c`);
there is no static TCP key. It reads `/etc/geoclue/geoclue.conf` and then
every `conf.d/*.conf`, the later winning. The whole chain was run in a
private user, mount and network namespace with only loopback: Debian's own
`/usr/libexec/geoclue` on a private system bus, its demo agent, and a
headless Qt 6.8.2 client making CoMaps' calls verbatim
(`createSource("geoclue2")` with `desktopId` `app.comaps.comaps`,
`AllPositioningMethods`, a 1 s interval). A fake NMEA feed of a fixed
public position (Montpelier, Vermont) on the socket reached the client
within milliseconds, with `NMEA service connected.` in GeoClue's log (a
`g_debug` line, seen because the spike ran GeoClue with
`G_MESSAGES_DEBUG=all`; the stock service does not log it); a
stopped feed gave no fix and GeoClue retried every 5 s; a restarted feed was
picked up with no GeoClue restart; and the same held over Debian's
**unmodified** `geoclue.conf` plus one drop-in. With no agent, GeoClue held
the request (`Client waiting for agent`) and Qt reported an access error
after its 25 s D-Bus timeout. No `[app.comaps.comaps]` section was written
in any run: `gclue-service-client.c` treats a client with no Flatpak app id
as a system app and starts it (`'app.comaps.comaps' not in configuration`,
then the start). An app section never bypasses the agent either.

**Also measured, and why the disclosures exist.** Disabling `[wifi]` does
not stop network lookups: GeoClue keeps a GeoIP-only source at city
accuracy unless `[static-source]` is enabled, and it asked beacondb when a
client asked below exact accuracy. Qt's plugin keeps the last fix in
`$XDG_DATA_HOME/qtposition-geoclue2`. The spike's first, unisolated run let
a private GeoClue reach beacondb and Qt cache an IP-derived fix in the
maintainer's home; the file was deleted and every later run had no network.
Hammunition's tests never start a GeoClue.

**Ruling: where the files belong.** They serve the receiver's position to
every GeoClue client, not to CoMaps alone (no app entry is written), so
they are a class-level set installed by `hardware apply` beside GPS time
(D-058), gated on GeoClue being installed (`/usr/libexec/geoclue` and the
`geoclue` group), and not a `system_modifications` entry on `comaps`: no
kind fits and the engine performs none for this. `comaps` carries the apt
dependencies, `geoclue-2.0` and `libqt6positioning6-plugins` (the package
with `libqtposition_geoclue2.so`).

**What `hardware apply` does**, each step printed first and read back after
(D-031), logged as `geoclue_files`:

1. `/etc/geoclue/conf.d/90-hammunition-gps.conf`: Hammunition's header,
   `[network-nmea]`, `enable=true`, `nmea-socket=/run/hammunition-gps/nmea.sock`.
   A drop-in; Debian's conffile is not edited.
2. `/etc/tmpfiles.d/hammunition-gps.conf`: `d /run/hammunition-gps 2750
   <operator> geoclue -`, then `systemd-tmpfiles --create` on that file.
   GeoClue's unit runs as `geoclue` with `ProtectSystem=strict`,
   `ProtectHome=true` and `PrivateTmp=true`, so the socket cannot be under
   /home, /tmp or /run/user; the setgid bit gives the socket GeoClue's
   group.
3. `systemctl try-restart geoclue`, because GeoClue reads its configuration
   only at start.

A file at either path without Hammunition's header, or anything but a
directory at `/run/hammunition-gps`, refuses the run before anything runs;
the operator's name is checked before it is written into a root file.
`--no-geoclue` leaves GeoClue alone. The plan prints both files, the
inspect steps (`cat` both, `ls -ld /run/hammunition-gps`, `journalctl -u
geoclue | grep -i nmea`), the reverse steps, and four sentences held word
for word in `hammunition.geoclue.DISCLOSURES` and in the navigation guide
(a test compares them): that GeoClue reads its configuration only at start
(it exits after 60 s idle, or `try-restart`); that while the tether runs
any native app of a user with an agent gets the fix and the demo agent does
not prompt; that stock GeoClue also asks beacondb and GeoIP whenever CoMaps
asks, so with the tether stopped the map shows that coarse location, and
only `[static-source]` would stop the GeoIP lookups, which Hammunition does
not change; and that Qt caches the last fix under
`~/.local/share/qtposition-geoclue2`. `hardware unapply` removes both files
by their header, the socket only when it is a socket, then `rmdir` and
`systemctl try-restart geoclue`.

**The tether.** `maps gps-tether --nmea-socket PATH` adds a unix stream
listener, mode 0660, served by the same fan-out as TCP with the same stall
and disconnect rules. It is on by default, at the path above, when the
drop-in with Hammunition's header is present, so the `gps-tether` launcher
needs no second form; `--no-nmea-socket` turns it off. A stale socket is
replaced, a live one or a non-socket refused, and only the inode the
tether bound is removed when it stops. The default failing (no directory
before tmpfiles ran, say) is a line on stderr and TCP still serves. TCP
10110 and `/position` on 10111 are unchanged. The socket is tighter than
TCP 10110: only GeoClue's group can open it.

**`doctor`** reports the two files, the directory's mode, owner and group,
and whether `org.freedesktop.GeoClue2.DemoAgent` is on the session bus
(`busctl --user list`, read-only, with a 5 s timeout, not asked as root).
It does not read other `conf.d` drop-ins, so a later one that sets
`nmea-socket` itself would win unseen; the guide says how to look.

**Removal, after the final review.** `unapply` runs `rmdir` only with our
tmpfiles line present (the evidence the directory is ours), and asks
`systemctl try-restart geoclue` only while GeoClue's daemon is installed;
a run whose operator resolves to root says the directory would be root's,
which the tether, never run as root, could not use. Debian's
`geoclue-2.0` autostarts that agent on every desktop but GNOME from
`/etc/xdg/autostart/geoclue-demo-agent.desktop`; it was running on the
development host's Plasma session.

**Rejected.**

- *Avahi* (`_nmea-0183._tcp`, GeoClue's other network-NMEA route):
  avahi-daemon and its socket are disabled on Parrot, `lo` has no
  MULTICAST so nothing is announced on it, and GeoClue connects to the
  advertised `<host>.local`, which resolves to a LAN address: the tether
  would have to listen beyond loopback, which D-061 refuses.
- *A CoMaps patch to read NMEA itself*: `patches` is a measured zero the
  engine refuses, and upstream closed Codeberg #3734 with "nothing to fix
  on CoMaps side", GeoClue being the standard.
- *Qt's `nmea` plugin chosen from outside*: it reads a loopback NMEA stream
  (measured), but `createSource` loads only the provider it names, CoMaps
  names `geoclue2`, and no environment variable selects another, so this is
  the same patch. A fake plugin claiming the name `geoclue2` would be a
  shim, which this project refuses.

**Owed by the bench** (`docs/reference/bench-verification-5430.md`), none
claimed until recorded there, and no agent added to any desktop to get
there:

- the demo agent present in the field laptop's Plasma session;
- the real, sandboxed GeoClue connecting to the socket in `/run`; from the
  kernel's rules, which exempt sockets from read-only mounts, not seen. The
  evidence is the tether's `A client on the socket connected` while CoMaps
  asks, with no `Failed to connect to NMEA service` warning in the journal:
  `NMEA service connected.`, which the brief named, is `g_debug` in
  `gclue-nmea-source.c` and Debian's `geoclue.service` does not enable
  debug output, so `journalctl -u geoclue | grep -i nmea` shows only
  failures (review, 2026-10-01);
- CoMaps' dot following the tether, and what it shows with the tether
  stopped;
- `/run/hammunition-gps` made again after a reboot;
- the demo agent autostarting on the Xfce and LXQt VMs.

**Still a gap.** A Flatpak CoMaps would need an `[app.comaps.comaps]` entry
or a desktop that prompts; Hammunition builds the native one. Direct NMEA
without GeoClue needs the patch nobody carries.

**Consequences.** `src/hammunition/geoclue.py`; `listen_unix`,
`close_unix` and the `unix` listener in `src/hammunition/gps_tether.py`;
`--nmea-socket`, `--no-nmea-socket` and `--no-geoclue` and the apply,
unapply and doctor wiring in `src/hammunition/cli/main.py`; the
`geoclue` checks in `src/hammunition/doctor.py`; `depends` and the known
problems in `catalog/packages/comaps.yaml`; the guide's section 17,
`docs/reference/cli.md`. Tests: `tests/test_geoclue.py`,
`tests/test_geoclue_apply.py`, `tests/test_doctor_geoclue.py`,
`tests/test_docs_geoclue.py`, and the socket cases in
`tests/test_gps_tether.py` and `tests/test_maps_tools.py`.

**Note 2026-10-02: four constants are shared and change together.** The GeoClue
drop-in's path, its header line, the socket's path and the group are written by
`hammunition hardware apply` (`src/hammunition/geoclue.py`) and read by the
tether, which is now its own project (`hammunition-gps-tether`, D-071 note) and
carries its own copy of all four to decide whether to serve the unix socket by
default. Changing one here without the other leaves the tether silently not
serving GeoClue (or serving it where nothing reads it); both repositories change
in the same step, and the tether's own tests pin its copy.

## D-070 — A data artifact may be taken from a LAN mirror the operator names, verified the same either way, and the engine can list what it would fetch without a station

**Date:** 2026-09-29. **Status:** proposed (implemented on branch
`artifacts-mirror`; the maintainer decides it at review). **Depends on:**
D-049 (a `data` unit's artifacts are pinned and hashed), D-057 (map regions,
verified by a pin or by Geofabrik's MD5, the plan saying which), D-061
(terrain tiles, by a pin or by the object's ETag MD5), D-035 (station values
are the operator's and defer, never invent), D-059 (one JSON document per
command). **Amends:** nothing; it adds a second source to the verified
fetch, never a second verifier.

**Why.** A field machine re-downloads the same public data every time it is
rebuilt or its regions change: gigabytes of Geofabrik extracts and
Copernicus tiles over whatever connection it has. The maintainer's NAS sits
on the same LAN. Hammunition Bunker
(<https://github.com/Renegade-Penguin/hammunition-bunker>, spec approved
2026-09-29) keeps a verified copy of that data there and serves it. It holds
no pins and no verifier of its own; it needs the engine to tell it what to
keep, and the engine to take from it without trusting it.

**Decided.**

1. **`hammunition artifacts [--json]`** lists every remote data artifact the
   engine would fetch for a selection given on the command line:
   `--map-regions`, `--map-freshness` (default `yearly`) and `--units`
   (default: every unit with a `data`, `osm-regions` or `dem-tiles` block).
   One entry per `data` file, per region, and per tile a region's outline
   touches, resolved by the plan's own code: the same pins, the same
   freshness and fallback, the publisher's MD5 or ETag read. **No station
   file is read and nothing installed here is read**, so the listing is the
   same on every machine. A region, outline or tile that cannot be resolved,
   and a map unit given no regions, is an entry whose `deferred` says why,
   never dropped. A unit that is not in the catalog, or fetches nothing, is
   refused with exit 2 by name.
2. **The `artifacts` document** is the Bunker's contract: `unit`, `name`,
   `url`, `check` (`sha256`, `md5-publisher`, `etag-md5`; `sha256-publisher`
   is reserved and no unit produces it), `digest`, `checksum_url`, `size`,
   `licence`, `deferred`. `name` is the artifact's stable name within its
   unit: the region path, the tile name, a data file's `install_as` or the
   file name its URL ends in.
3. **`digest` is always a digest.** The Bunker spec allowed it to hold the
   URL of a publisher checksum until read. A field that is sometimes a URL
   and sometimes hex is a parse ambiguity in a contract, and the engine
   reads the checksum while resolving anyway; where it read it from is the
   added `checksum_url`.
4. **`station set --mirror URL`** stores one optional key, removed by
   `--clear-mirror`. It must be `http` or `https` with a host, no user or
   password (the station file holds no credentials), no query or fragment.
   It is not a template variable. **Plain http is allowed on purpose**: the
   content is public data and the check is the hash, not the transport.
5. **A mirror URL is a LAN address, never something reachable from the
   internet.** The docs say so. The engine does not enforce it: whether a
   name is private cannot be decided without resolving it, and nothing the
   mirror could send gets past the digest.
6. **The verified fetch tries `<mirror>/<unit>/<name>` first** (each segment
   percent-quoted; an empty, `.` or `..` segment refused) for the three kinds
   of artifact the listing names, and the publisher second. **Any failure at
   the mirror** — unreachable, an HTTP error, the size cap, a wrong size, a
   wrong digest — discards what it sent and asks the publisher. The digest
   checked is the same either way; nothing unverified reaches the cache. A
   mirror that did not answer at all is not asked again in that run (one
   10-second timeout, not one per tile). Source tarballs, prebuilt binaries,
   wheels and npm packages are not mirrored: they are not in the contract.
7. **The plan says so first.** A *Data mirror (D-070)* section names the
   mirror; each data fetch step says the LAN mirror is tried first and its
   digest checked either way, and its detail names both URLs in order. The
   JSON plan carries `install.mirror` and each step's `sources`. With no
   mirror set the text plan is unchanged. **`install --no-mirror`** ignores
   the key for one run, and the plan says it is ignored.
8. **The log records the actual source.** A data fetch's `action_end`
   carries `source` (`cache`, `mirror` or `publisher`), `fetched_from` and,
   when the mirror was passed over, `mirror_failure`
   (`docs/reference/transaction-log.md`).

**Measured.** `tests/test_fetch_mirror.py` against fakes, and
`tests/test_mirror_loopback.py` against two real HTTP servers on 127.0.0.1
through the real transport: a mirror hit asks the publisher nothing; a 404,
wrong bytes, a wrong size and the cap each hand over with the same digest
and leave nothing of the mirror's in the cache; a stopped mirror is asked
once per run; both failing is one refusal naming both.
`tests/test_artifacts.py`: pinned and unpinned regions, the three
freshness modes, tiles from an outline, every deferral, no station read, no
install record read, and the listing's `<unit>/<name>` being exactly what
the fetch asks a mirror for. **Not yet measured:** an install on the field
laptop against a running Bunker, which is the evidence this decision needs
before it is accepted.

**Rejected.** A mirror trusted for its own hashes (the Bunker writes
sidecars): that would make the NAS a second source of truth, which it is
not. Mirroring every fetch, source builds included: not in the Bunker's
contract, and a build's pin is a commit the mirror has no name for.
Enforcing a private address: a hostname proves nothing, and refusing a
routable LAN address would refuse real networks. Asking the mirror for its
index first: one request per artifact, falling back on a 404, needs no
agreement about an index format the engine would then have to parse and
trust. `artifacts` reading the station by default: the Bunker runs on a NAS
with no station, and a listing that changed with whoever ran it would not
be a contract.

**Amended 2026-10-01 (issue #159): Kiwix books (D-066) go through the mirror
too, and `artifacts` lists them.** The books backend had fetched from
download.kiwix.org only; it now asks `<mirror>/kiwix-library/<book id>`
first, the id as `catalog/data/kiwix-pins.yaml` names it, with the same
pinned sha256 and size, the same plan wording and the same log facts as a
`data` file. `artifacts --reference-books ID,ID` lists each book (`check:
sha256`, its own licence line) from the carried pins alone, no station read;
with none given, `kiwix-library` is one entry deferred as *no books
selected*. Books are the largest data the catalog fetches (up to 127 GB), so
they are the mirror's most valuable case. Measured in
`tests/test_mirror_books.py` (two loopback servers: a mirror hit, and wrong
bytes falling back to the publisher) and `tests/test_artifacts.py`.

---

## D-071 — The offline browser map: vector tiles from the station's regions by the archive's tilemaker, served with byte ranges by `reference serve` on loopback, drawn by a pinned MapLibre, with the position as a loopback event stream

**Date:** 2026-09-30. **Status:** accepted (the design approved by the
maintainer from the tile spike's report, 2026-09-29, relayed with the task;
three measured departures below). **Spec:**
`docs/superpowers/specs/2026-09-30-map-server-design.md`. **Depends on:**
D-057 (regions are station data; derived data by a converter enum), D-061
(converters run as the operator through one `Staging`, with their own
ledger; the tether), D-066 (`reference serve`, 127.0.0.1 only), D-049 (a
`data` unit's files are pinned, sized and licensed in the plan), D-039 (a
target gap defers a profile member by name), D-037 (a floor read from the
archive's candidate, nothing fetched to meet it), D-024 (pin the tag a
distribution packages), D-070 (a data artifact may come from the LAN
mirror, verified the same). **Amends:** D-057's converter enum, with
`tilemaker-pmtiles`; D-049's `data` method, with an archive's `members` and
`into`; D-039's deferral reasons, with a converter's program below the
version its output needs; D-061's tether, with a second loopback listener.

### What was measured

The spike (2026-09-29, the development host, Geofabrik's Delaware extract,
nothing installed): the archive's tilemaker 3.0.0 wrote a 20.1 MB PMTiles
file from the 22.1 MB extract in 10 to 37 s, 0.49 GB of memory with
`--store` and 2.8 GB without; Python's `http.server` ignores `Range` and
pmtiles.js fails on it, while a stdlib handler that answers with 206 works;
headless Chromium with every non-loopback lookup blocked drew the map with
MapLibre GL JS 6.11.2, pmtiles.js, OSM Bright, a local sprite and local
fonts, with no error and every request local. Ubuntu 24.04 carries
tilemaker 2.4.0, which writes no PMTiles. For this decision (2026-09-30):
every pin downloaded once and hashed; the ocean clip below.

### The rule

1. **Two units.** `vector-map-kit` (`data`) holds every fixed file: members
   of Debian's pool copy of tilemaker 3.0.0's tarball (the profile, the
   OSM Bright style and three KlokanTech Noto fonts, byte-identical to
   upstream's v3.0.0 tag), MapLibre GL JS 6.11.2's `dist.zip` (sha256 equal
   to GitHub's asset digest), pmtiles.js 4.5.0 from npm (sha512 equal to the
   registry's integrity), OSM Bright's sprite at its gh-pages commit, and
   Natural Earth's ocean, urban areas, glaciers and Antarctic ice shelves at
   the v5.1.2 commit `country-boundaries` pins. `osm-pmtiles` (`derived`,
   converter `tilemaker-pmtiles`, `source: osm-regions`, `kit:
   vector-map-kit`) builds one `<slug>.pmtiles` per region. Both join
   `navigation`; `tilemaker` and `gdal-bin` are the archive's.
2. **The converter is the engine's**, like every converter: per region, as
   the operator in `<builds>/osm-pmtiles/<slug>.work/` under its lock, the
   kit's files checked, Natural Earth's ocean clipped to the region's
   header box plus 0.1° with `ogr2ogr -clipsrc` (the whole polygon, said in
   the outcome, when the header has no box or it crosses the antimeridian),
   the land-cover layers linked where the pinned config looks, then
   `tilemaker --input --output --config --process --store`; the output must
   start with `PMTiles`, is published re-verified with a `.source` sidecar
   (`converter: tilemaker-pmtiles 1`), and a failure goes to a tiles ledger
   whose step fails the run by name, last.
3. **tilemaker 3.0 is a floor the plan reads**, from the apt probe it
   already makes: the installed version, else the candidate. Below it,
   `osm-pmtiles` is deferred from a profile naming the version found, and
   refused when typed; without lists it is a note. `vector-map-kit` is not
   deferred with it: on Ubuntu 24.04 `navigation` still downloads the kit
   (about 79 MB) and the page says no maps are installed. Deferring a unit
   with its only consumer is a mechanism the plan does not have; the kit's
   manifest says so. The floor is the
   engine's (`CONVERTER_FLOORS`), not the manifest's, because the output
   format is the engine's argv. A `when:` selector naming Ubuntu 24.04 was
   the other way; it would freeze one evening's archive into the catalog.
4. **`reference serve` serves the map** on its own port: `/map/`,
   `/map/regions.json`, `/map/tiles/<slug>.pmtiles`, `/map/kit/<path>`, each
   by its decoded installed name only. Every file takes one `Range` (206,
   `Content-Range`), `HEAD` gives the size, a range past the end is 416.
   **A request whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>`
   is refused (403) on every path**: the tiles say where the operator's
   regions are, and a site whose name an attacker points at 127.0.0.1 (DNS
   rebinding) sends its own name.
5. **The page loads nothing from elsewhere**: the style's sprite, fonts and
   source are pointed at this server before MapLibre reads it, and the
   attribution control, not collapsed, reads "© OpenMapTiles ©
   OpenStreetMap contributors", which the OpenMapTiles schema's CC-BY 4.0
   and the data's ODbL require on the map. A test asserts both.
6. **The position is a Server-Sent Event stream**, `GET /position` on
   127.0.0.1 port 10111 from `hammunition maps gps-tether`
   (`--position-port` on both commands). **Ruling: SSE, not a JSON poll.**
   The tether watches gpsd only while a client is connected (D-061); an
   event stream is a connected client and rides that fan-out unchanged,
   while a poll would need a gpsd connection per request or a watch left
   open on a timer. The same Host rule applies, a non-loopback `Origin` is
   refused, a request with no `Origin` must ask for `text/event-stream` (an
   image tag on any site sends neither, and would otherwise hold gpsd
   watched), and `Access-Control-Allow-Origin` is sent only to a loopback
   page, so no web page from elsewhere can read the position through the
   operator's own browser. An unfinished request is closed after 5 s, at
   most 16 are held, and an unreachable gpsd is a 503, not silence (review,
   2026-09-30).

### Departures from the approved design, each measured

- **The ocean is Natural Earth's, not the simplified water polygons.**
  `simplified-water-polygons-split-3857.zip` was 23,732,315 bytes on
  2026-09-29 and 23,730,235 bytes on 2026-09-30, `Last-Modified` 03:43 GMT
  that day: rebuilt daily with no checksum, like the full 906 MB set, so a
  pin would fail within a day. `ne_10m_ocean` is public domain and fixed at
  a commit; it is one world-sized polygon, so it is clipped per region
  (0.115 s on Delaware's box, measured with the archive's GDAL 3.10.3). At
  street zoom a coast follows Natural Earth's 1:10m line. The route to the
  accurate ocean is the unpinnable daily set; documented, not carried.
- **Four shapefiles, not one.** The pinned config names Natural Earth's
  urban areas, glaciers and ice shelves beside the ocean. All four are
  carried so the config runs unmodified; the three land layers at the
  pinned commit are byte-identical to the naciscdn zips the spike used.
- **The glyphs come from one archive.** Three fonts are 768 files. They,
  the style and the profile are members of one 43.7 MB tarball, so a
  `data` archive gains `members` (extract only these; one that matches
  nothing refuses) and `into` (a subdirectory; required on every archive
  of a unit with two archives or files beside one, because an archive
  replaces the directory it is extracted into).

### Consumers, from the spike's measurement

The page itself. AIS-catcher reads `.mbtiles` itself; tilemaker writes one
output per run, so only PMTiles is made and AIS-catcher is not served.
QMapShack, Xastir and SDRangel want raster PNG, which only PostGIS,
osm2pgsql, renderd and carto produce (Delaware imported in 53 s into
155 MB; the render not measured; carto wants the daily ocean again):
documented, not built. YAAC imports the region's `.osm.pbf` itself. pat and
the Winlink standard forms embed no maps.

### Licences

MapLibre GL JS, pmtiles.js and OSM Bright's code BSD-3-Clause; tilemaker's
profile FTWPL; the Noto fonts OFL-1.1, kept under KlokanTech's names as
their README asks; OSM data ODbL; the OpenMapTiles schema and OSM Bright's
design CC-BY 4.0 with the visible credit; Natural Earth public domain. All
printed in the plan with the kit's size (D-049); nothing is redistributed.

### Not carried

planetiler (not in any archive, Java 21+, about 1.45 GB of side inputs
before the first tile, slower than tilemaker on a region). martin,
go-pmtiles and mbtileserver (GitHub binaries with no publisher checksum,
and nothing they add the page needs; go-pmtiles binds every address by
default). tileserver-gl and tileserver-gl-light (npm install scripts that
fetch native binaries, refused under D-037). `libjs-leaflet` (raster only)
and `libjs-openlayers` (the dead 2.13 line). `.mbtiles`. The raster stack.
The CJK fonts (64 MB; CJK labels do not draw).

### What has run, and what is owed

The suite: the members extraction against synthetic archives shaped like the real ones, the
schema, the converter against fake `tilemaker` and `ogr2ogr` (argv, clip
box, links, effect check, sidecar, ledger, removal), the floor as deferral
and refusal, the install dry run, the range server on loopback (206, HEAD,
416, exact names, the Host rule), the position stream against a fake gpsd
(an event, shared watch, rebinding refused), and the page under headless
Chromium from the pinned kit and a synthetic tile with every non-loopback
host unresolvable: idle, no error, the credit drawn, a glyph range asked
for, every request the page made on 127.0.0.1 by its net log. **That test
is local only:** it needs the pinned files on disk (`HAMMUNITION_MAP_KIT_DIR`)
and `chromium`, and is skipped by name without either, which CI has neither
of; it passed on the development host on 2026-09-30, with Chromium 154, and
went red when the page's sprite was pointed back at GitHub. A CI job that
restores the pinned files from a cache keyed on their sha256 is the route to
running it on every change. **Owed by the bench:** a real
tilemaker run through the engine with the clipped Natural Earth ocean (its
time, memory and `--store` scratch; the plan allows three times the
download, unmeasured); the page in a desktop browser with a real receiver's
position; tilemaker 3.1 and 3.2 with the 3.0 profile.

**Rejected.** Pinning the daily water polygons (dead in a day). Taking
them unpinned on TLS alone (every non-apt download is checked). A `when:`
selector for Ubuntu 24.04 (a frozen measurement). A second HTTP server for
the map (`reference serve` already binds loopback). A JSON poll for the
position (above). Binding either listener beyond 127.0.0.1, including
behind a flag: another machine uses `ssh -L`. `navigator.geolocation` (on
Linux it is GeoClue's network guess, not the receiver).

**Consequences.** `DataArtifact.members` and `into`, `extract(members=)`;
`tilemaker-pmtiles`, `kit` and `CONVERTER_INPUTS` in
`src/hammunition/manifest/schema.py`; `src/hammunition/backends/pmtiles.py`,
`src/hammunition/tiles_plan.py`, `CONVERTER_FLOORS` in
`src/hammunition/plan.py`; `src/hammunition/map_page.py` and the range
server, Host rule and map routes in `src/hammunition/reference.py`; the
position listener in `src/hammunition/gps_tether.py`; `--position-port` on
`reference serve` and `maps gps-tether`; `catalog/packages/vector-map-kit.yaml`,
`catalog/packages/osm-pmtiles.yaml`, `catalog/profiles/navigation.yaml`. The
operator's page is `docs/guides/offline-navigation.md`, section 16.

**Note 2026-10-02: the tether's new home.** The position listener on
127.0.0.1:10111, and the NMEA server beside it, no longer live in this engine's
source as the thing to run: they are `hammunition-gps-tether`
(<https://github.com/Renegade-Penguin/hammunition-gps-tether>, GPL-3.0-or-later, its
own releases and tests), installed by the `gps-tether` catalog unit and run as a
systemd user service (D-073, amended 2026-10-02). Nothing about the decision
changes: the same two loopback ports, the same `GET /position` stream, the
same flags. `hammunition maps gps-tether` runs the installed program and, where it is
absent, refuses with `hammunition install gps-tether`. **Retired 2026-10-02
(later):** the engine's `gps_tether.py` and its tests are deleted;
`src/hammunition/tether_contract.py` holds the only values the engine shares
with the tether (the two ports and the four GeoClue constants), and
`tests/test_tether_contract.py` asserts they equal the tether project's
(skipped, saying so, where its source is not beside the checkout or
installed). `reference serve` validates `--position-port` with
`reference.position_port`. The service and a foreground run cannot share
port 10110. Not yet run as a service on a machine.

---

### Amendment (2026-10-02): tar1090 is a page on this server, at `/aircraft/`

**Status:** accepted (the route chosen by the D.10b report, 2026-10-01; the
maintainer asked for it to be built). **Depends on:** D-024, D-032, D-049
(a `data` unit), D-066 (the Host check), D-039 (a missing `depends` defers
the unit by name).

tar1090, the ADS-B aircraft map for readsb, is carried as the `tar1090` data
unit and served by `hammunition reference serve` at `/aircraft/`. Its own
installer is not run: a root script piped from `wget` that clones `master`
and the aircraft database, writes a lighttpd stanza (port 80, every address)
and a systemd unit. What the page needs is its `html/` directory.

**The pin (D-024).** No target's archive offers tar1090 (the seven-target
sweep, 2026-10-01; the AUR builds `master`), and upstream publishes no tags
and bumps its version file in every commit, so D-024's first rule has nothing
to pin and the second applies: our own pin, the commit the page was measured
at, `e784ee5ae82948f41efe3ef5c235ade0943ab8ff` (default branch head on
2026-09-29, D-032; version 3.14.1823), as GitHub's archive of that commit
(2,812,358 bytes, sha256 `aad8017d…`, downloaded twice on 2026-10-02 with
the same digest both times). GitHub does not promise the bytes of a
generated archive for ever; a change makes the fetch refuse on the hash, and
the pin is then moved by hand. Licence: GPL-2.0-or-later, from its LICENSE
("the GPL, v2 or later"); its flag icons are MIT. Only `html/` and the
licence are kept.

**The page may not call out, in three layers.** tar1090 reaches the internet
in many places (its online tile, weather and airspace layers; aircraft
photographs; a route service; FAA, weather and airspace overlays; a
heywhatsthat range file): `layers.js` alone names more than thirty hosts in
its text (attribution links included; the number depends on how the
`{a-d}.` sub-domain templates are counted). So (1) the served `config.js` (ours; upstream's is
all comments) sets tar1090's own switches off (`planespottersAPI`,
`planespottingAPI`, `showPictures`, `useRouteAPI`, `routeApiUrl`, `tfrs`,
the picture links); (2) `hammunition-layers.js`, loaded after tar1090's
`layers.js`, replaces `createBaseLayers` so that no remote layer exists;
(3) every response under `/aircraft/` carries a Content-Security-Policy that
names no host (and `frame-ancestors 'none'`, so no other site can frame it),
so the browser refuses a request to anywhere else. The tests
show each half: the page served as upstream ships it asks `openfreemap`,
`arcgis`, `carto` and `api.planespotters.net` for things, and that same page
with only the policy asks nobody and the console says it was refused
(`tests/test_aircraft_render.py`). What none of it stops is the operator
clicking one of tar1090's outbound links (FlightAware, planespotters) in an
aircraft's detail panel: that is a navigation they chose. Chromium's own
form-autofill lookup to Google, started by the browser because the page has
inputs, is the browser's and not the page's; the test turns it off, and the
page has no way to.

**The basemap.** tar1090's base map is replaced, not configured: the one base
layer is a vector-tile layer over the station's PMTiles regions
(`/map/tiles/*.pmtiles`, read with pmtiles.js from the `vector-map-kit`, the
same files and the same byte-range reads as `/map/`), decoded by the
`ol.format.MVT` already in tar1090's bundled OpenLayers and styled by a small
style of the page's own (water, green land, boundaries, roads, place names),
plainer than OSM Bright. Every region whose header box meets a tile
contributes to it. The credit "© OpenMapTiles © OpenStreetMap contributors"
is the layer's attribution. When `osm-pmtiles` is not installed there is
**no basemap**: the base layer is an empty one, the aircraft are drawn on a
plain background, and the page says why, in words that name the command
(`vector-map-kit` missing, no region built, or no map installed). Nothing is
ever fetched in its place. The map shelf's `ready` test is the one the map
page uses, so the two pages cannot disagree about whether the kit is there.
OpenLayers' base-layer choice is by name from browser storage, so the page
sets `MapType_tar1090` itself.

**Where the data comes from.** `/aircraft/data/<name>.json` is read from
readsb's output directory: `/run/readsb` (Debian's service writes it there,
measured in a `debian:13` container on 2026-10-01; `/var/run/readsb` is the
same directory), or the directory `--readsb-json DIR` names (absolute). One
plain name of letters, digits, `_` and `-` ending `.json`, a regular file and
never a link (opened without following one, and checked on the descriptor),
read-only and sent `no-store`; nothing joins a path. No `chunks/` or
`traces/`: tar1090's history service is not run, so a track is what the page
saw since it opened.

**`receiver.json` is rewritten, to five keys.** tar1090 reads it first and
chooses from it how to read everything else, and measured on the installed
Debian readsb 3.14.1630 (run with no SDR, 2026-10-02) the decoder writes
`aircraft.json` *and* `aircraft.binCraft.zst`, and the binary's own strings
show `binCraft`, `zstd` and `globeIndexGrid` keys in the file it writes:
tar1090 asks for `aircraft.binCraft.zst` when it sees the first two, for
globe files on the third, and for history chunks when `history` is above one.
None of those is served, and a page that asked would get 404s and show
nothing (and a page with no receiver.json at all reloads itself every ten
seconds and never draws: measured). So `/aircraft/data/receiver.json` is
built, not relayed: `version`, `refresh` and `lat`/`lon` from readsb's file
when it parses (validated, at most 1 MiB, never a link), `readsb` when it
says so, `history: 0`, and nothing else; the page then reads
`aircraft.json`, which readsb always writes. When readsb's file is missing
or unusable but `aircraft.json` is there, defaults stand in (a dump978-fa
JSON directory gets a page too); with no `aircraft.json` the answer is 404
and the page asks again. The render test gives the page a receiver.json that
says `binCraft`, `zstd` and `history: 120`, and a junk
`aircraft.binCraft.zst`; the mutation that passes those keys through turns
it red. The directory is read on each request:
readsb may start after the page does.

**Not carried.** `wiedehopf/tar1090-db`, the aircraft database. Upstream
replaces its single commit regularly, so it cannot be pinned (D-024, D-032),
and no stable hashed source was found. With no database the type, operator
and registration columns hold only what readsb itself decoded, and the
page's requests for `db2/` answer 404 (measured; the aircraft draw
regardless). A published pin would add it without any engine change.

**Safeguards.** The Host check of D-066 applies to every path here as to
`/map/`; the data directory is the operator's receiver's, and a page on
another site whose name points at 127.0.0.1 must not read what is overhead.
The unit's `depends: [readsb]` makes it defer by name wherever readsb does
(Ubuntu 24.04, Mint 22.3). `find_aircraft` refuses, naming the file, an
installed tree whose `index.html` does not load `layers.js` where the pinned
page does, because serving it unrewritten would let it ask for tiles online
(`hammunition reference serve` then says so and serves the rest).

**Measured:** in headless Chromium 154 with every host but 127.0.0.1
unresolvable and its network log read back: with a synthetic `aircraft.json`
and a synthetic one-tile region, both aircraft appear in the table and the
selected one in the panel; the tile is decoded (features counted by the
page); no request left loopback; the policy never fired; without the map the
page says why. **Not measured:** a live readsb with an SDR (the data is
synthetic; the installed readsb writes no `receiver.json` without a
receiver, so the real file's contents were read only as strings in the
binary; the earlier spike drew 21 real decoded aircraft through a static
server); the page in Firefox; a
real region at street zoom (the fixture has one tile at zoom 0, and the tile
loader's zoom, header-box and several-region paths are exercised only by
reading); the page on a phone.

**Consequences.** `src/hammunition/aircraft_page.py`; the `/aircraft/`
handler, the per-response header hook and the landing section in
`src/hammunition/reference.py`; `--readsb-json` in `cmd_reference_serve`;
`catalog/packages/tar1090.yaml` and its line in
`catalog/profiles/listening.yaml`. The operator's page is
`docs/guides/sdr.md` section 3; the CLI's is `docs/reference/cli.md`.
Tests: `tests/test_aircraft_page.py`, `tests/test_aircraft_catalog.py`,
`tests/test_aircraft_render.py` (local: needs Chromium and the pinned
files, `HAMMUNITION_TAR1090_DIR` and `HAMMUNITION_MAP_KIT_DIR`, and is
skipped by name without them).

---

## D-072 — GPS time where the daemon is not ntpsec: a `chrony` unit, installed by name and never in a profile; the time daemon a machine has is the operator's to replace

**Date:** 2026-09-30. **Status:** proposed (branch `gap-03-gps-time`),
awaiting the maintainer. **Answers:** Q-022 #3 (gap analysis §A4), which
ruled "yes: a chrony unit in `station` … displacing systemd-timesyncd by
disclosure; measure which targets already ship chrony first". **Depends
on:** D-022 (coexist, disclose, never remove silently; its 2026-09-07
amendment refuses every removal), D-035 (a file with no station value is
never deferred), D-058 (GPS time through ntpsec, pull request #124, not yet
merged). **Evidence:** `docs/reference/time-daemons.md`.

**What was measured, and what it changed.** The recommendation was written
without D-058 in view and without the archive; both changed it.

| Fact | Measured |
|---|---|
| D-058 installs no time daemon | It disciplines ntpsec where ntpsec already runs and refuses by name everywhere else; chrony and timesyncd targets are out of its scope |
| Which daemon a machine has | ntpsec on Parrot's security edition (`parrot-tools-full` pulls it in; the field laptop has it, auto-installed); chrony on Ubuntu 26.04 (`ubuntu-minimal`); systemd-timesyncd on Debian 13, Ubuntu 24.04, Mint 22.3 and a Kali whose `kali-linux-core` came first |
| Coexistence | chrony, ntpsec and systemd-timesyncd each `Provides:` and `Conflicts: time-daemon`; on Debian 13 installing chrony or ntpsec simulates `Remv systemd-timesyncd`, and chrony over ntpsec `Remv ntpsec` |
| The engine | refuses any transaction whose simulation removes a package, and names the `apt-get remove` (measured with this unit: refused over timesyncd, refused over ntpsec, planned cleanly with chrony present on Ubuntu 26.04 and with no daemon on Debian 13) |
| gpsd | publishes no time while no client is connected unless it runs with `-n`, on the command line and through `gpsdctl add` alike; Debian's `/etc/default/gpsd` ships without it |
| chrony reading gpsd | `refclock SHM 0` selected the GPS in a container, either start order, no grant; the SOCK driver was not made to work |

**The ruling.**

1. **A `chrony` unit is carried** (`catalog/packages/chrony.yaml`): chrony
   from the archive, `refclock SHM 0 refid GPS poll 2 delay 0.2` in
   `/etc/chrony/conf.d/hammunition-gps.conf`, and a gpsd.service drop-in
   setting `OPTIONS=-n`. Neither file takes a station value, so neither is
   ever deferred. It declares `systemd-timesyncd` and `ntpsec` in
   `conflicts_with_repo_package`.
2. **It is in no profile, `station` included** — the one point on which this
   departs from Q-022 #3. D-022 rule 5 keeps what displaces a distribution's
   choice out of a base profile, and the measurement shows why that rule is
   not a formality here: apt keeps one time daemon, the engine refuses the
   removal, and a member that is refused refuses its whole transaction. As a
   `station` member it would have refused `station` on every machine that
   has a time daemon other than chrony: the field laptop (ntpsec), and by
   the metapackage simulations every target's default install except
   Ubuntu 26.04's.
3. **Displacing timesyncd is the operator's act.** The plan's refusal names
   the package and prints `sudo apt-get remove systemd-timesyncd`; after
   that, `hammunition install chrony` plans cleanly. No `system_modifications`
   kind removes a package — `package_purge` is schema-valid and unperformed,
   and declaring it would refuse the unit — so `conflicts_with_repo_package`
   is how the displacement is disclosed, as D-022 rule 2 says it should be.
4. **Never over ntpsec.** On an ntpsec machine the unit is refused like any
   other displacement, and the docs send the operator to D-058. The catalog
   therefore never carries two daemons on one machine; apt would not allow
   it anyway.

**What it means for D-058.** ntpsec's `refclock shm unit 0` reads the same
segment, so D-058 needs gpsd running with `-n` too, or a client held open.
The field laptop's `/etc/default/gpsd` has `GPSD_OPTIONS=""` (read, not
changed). Whether its gpsd has a client connected is not measured; the
bench run D-058 already owes is where to check, with `ntpshmmon`.

**Rejected.**

- *chrony in `station`*: point 2.
- *The SOCK refclock*, which chrony's manual prefers: its path names the
  serial device, which differs by receiver and machine, gpsd must start
  after chronyd, and it did not run here. SHM unit 0 is the first receiver
  gpsd opens, and chronyd starts at boot before anyone logs in, which is the
  window the manual's warning is about.
- *ntpsec instead of chrony on timesyncd machines*, which would put every
  machine on D-058's one mechanism (modes, `doctor`, the tray). Q-022 #3
  named chrony, and D-058 declined to switch daemons; this is a question for
  the maintainer, not a thing to decide here.
- *Editing `/etc/default/gpsd`*: it is gpsd's conffile; the drop-in changes
  the same thing without touching it.

**Not measured.** The installer order on Kali and Ubuntu 26.04; the SOCK
driver; anything with a real receiver (NMEA's offset from UTC, how `delay
0.2` weighs the GPS against network servers, chrony's AppArmor profile on a
host); `chrony.service` itself, which needs `CAP_SYS_TIME` a rootless
container lacks; the root `mkdir` step for a missing drop-in directory,
which is unit-tested only (the container runs wrote as root in-process).

## D-073 — The rig is station data: a `rig` device class and five station values, driven by one shared `rigctld` as a systemd user service the engine renders, enables, discloses and reverses

**Status: proposed; the bench is owed.** The maintainer ruled on all nine of
the design's questions on 2026-09-30, one at a time, and the implementation
carries each; the FT-991A and the UV-50PRO on the field laptop are the evidence
D-073 needs before it is accepted (§12 of the design,
`docs/superpowers/specs/2026-10-01-rig-station-data-design.md`). Both hardware
pages say `untested` until then.

**The shape.** A radio is a device manifest carrying a `rig` block, inheriting
a new `rig` hardware class (`dialout`, `libhamlib-utils`, `flrig`, the CP2105
`10c4:ea70` with its ambiguity, no udev rule, no symlink — a CAT port needs
access, and `/dev/serial/by-id/` already names it). A CAT radio's block names a
hamlib model and a CAT port; a radio with no CAT carries a `ptt_only` shape
naming the serial lines that key it — the two are mutually exclusive. The
operator's selection is five station values — `rig`, `rig_device`, `rig_baud`,
`rig_ptt_line`, `rig_owner` — validated against the catalog by the CLI (the
station module stays free of the hardware catalog) and against this machine's
hamlib for `hamlib:<model>`. Nothing is defaulted but `rig_owner` (**D-035**).

**One `rigctld` for everyone, as a user service.** `rigctld` cannot take a
socket from systemd (measured: no `LISTEN_FDS`/`sd_listen_fds` in the binary or
`libhamlib`), so a plain **user** service, not socket activation — the station
values are the operator's and live in their home, and nothing here needs root.
A new carrying unit `rig-service` in the `station` profile holds a
`user_services` block (ruled beside `config_files`, not a
`system_modifications` kind and not plain `config_files`): the engine renders
the unit file from fixed fields — the catalog never carries unit syntax —
substitutes the station and catalog-derived values, re-checks each exec word is
one safe argv word, writes it as the operator through the `~/` writer, and runs
`daemon-reload`/`enable`/`restart` as the operator (never sudo; `--machine` when
root acts for an operator). A missing value defers the service by name and the
rest of the transaction installs (**D-035**); `rig_owner: flrig` and
`rig_ptt_line: vox` skip it by name; `uninstall rig-service` disables it and
removes the file only if it still carries Hammunition's header. `-T 127.0.0.1`
is fixed and `listens` refuses any non-loopback address at load.

**The gate (ruling 8), measured.** `rigctld` reads newline-separated commands
and a browser's POST body is newline-separated text, so the question was
whether a web page could key the transmitter on loopback. Measured against
hamlib 4.7.2's dummy model on an ephemeral port: a browser-producible POST,
sent as one write, **did key it** — `rigctld` works through the request line
and the header lines and then acts on body lines that follow, a recovery that
takes a second or so, and it keeps parsing buffered input even after the client
closes. A first measurement that polled PTT once, too soon, read it as unchanged
and was wrong; the published claim was corrected here (that reading was a D-018
failure, caught by the final review). `rigctld` has no password (its `-A` is not
implemented), so **ruling 8 is satisfied by the filter, not by its absence**:
`rigctld` binds `127.0.0.1:4632`, and a loopback filter
(`hammunition.rigproxy`, run by `rig-service` as a second user service) binds
the port programs use, `127.0.0.1:4532`, forwarding every byte unchanged except
that it drops, unread, any connection whose first line is an HTTP request line —
which a real rig client never sends. `tests/test_rig_gate.py` keys the dummy
only to prove the filter stops it: through the filter the HTTP request does not
key, and a real command still does. `doctor` checks both services.

**Unattended (ruling 7).** `station set --unattended` turns on linger for the
calling operator through the D-056 helper's new `linger on|off` verb, behind the
existing one polkit action (its wording widened to keeping services running
after logout). It acts only on the uid polkit reports, never an argument, and
records in `/etc/hammunition/linger.yaml` whether Hammunition turned linger on,
so only linger that is ours is ever turned off; `--no-unattended` and
`hardware unapply` reverse it from the same record.

**`doctor`** checks the service read-only and never keys: station completeness,
the unit's enabled/active/failed state, whether `rigctld` answers `\dump_state`
on loopback (the reply parse checked against a dummy `rigctld` in the suite),
the device's presence, and linger.

**The nine rulings (2026-09-30), folded in:** (1) a `user_services` block
beside `config_files`; (2) a new `rig-service` unit in `station`, not the
service on `libhamlib-utils`; (3) `rig_owner`, `rigctld` or `flrig`; (4) no
ordered variants in `config_files` (the two `user_services` entries are two
complete services chosen by `rig_kind`); (5) no JSON-merge (Pat stays on the
guide page); (6) PTT-only is in scope, with the `ptt_only` shape, `rig_ptt_line`
and the UV-50PRO as the first member; (7) opt-in `--unattended`; (8) the gate
and its filter-if-it-keys response; (9) `--rig hamlib:<model>` for radios with
no manifest, baud mandatory.

**What the bench owes** (§12): the FT-991A's USB shape (the two CP2105 ports'
strings, `reports_serial`, the codec id — closing the manifest's
`identification_gap` and letting it become `composite`); the service at login,
`\dump_state`, `doctor` clean; whether `BindsTo`/`.wants` stop and start it with
the radio; the program table against the service; the flrig route; the
UV-50PRO's keying line and that nothing is written to the serial line, plus the
start-up keying question and VOX; `--unattended` across a logout. Found while
measuring and fixed first: issue #174, the launcher that shadowed `rigctl`.

**Amended 2026-10-02: user services generalised.** The block was rig-shaped
only because the rig was its first user. Measured on the second (the GPS
tether, which needs no station value and binds no device), four rig
assumptions came out and the rig's behaviour and tests did not move.

1. **Plain services.** An entry with no `when_station`, no `unless_station`, no
   `{station.*}` and no `binds_to_device` is *plain*: always planned, needing
   neither the station nor the hardware catalog, never deferred. An empty
   `when_station` means always. A manifest may mix plain and rig entries; the
   rig group is decided on its own as before, deferring by name without a
   rig or a catalog (the catalog check moved from `plan.py` into
   `plan_user_services`, same wording).
2. **The header names the unit.** `# Written by Hammunition (catalog unit
   `gps-tether`, D-073).` Removal recognises any header of that shape and
   still leaves a file the operator rewrote. The rig's file is byte for byte
   what it was (a test holds it).
3. **Several units in a plan**, each named in the plan view with only what is
   true of it: the "can key the transmitter" warning stays the rig's, and a
   service with no device says it is enabled and starts at next login (nothing
   is started during an install that has no device to wait for); a reinstall
   runs `systemctl --user try-restart`, which reaches a copy that is running
   and leaves a stopped one stopped, so a new pin does not leave the old code
   holding the ports. A venv requirement carrying the all-zero digest
   (`UNPINNED_SHA256`) is refused by name at plan time, before any step.
4. **`restart`, `restart_sec` and `restart_prevent_exit_status`** are manifest
   fields from fixed sets (`on-failure`, `always`, `no`; 1 to 300 seconds;
   exit codes 1 to 255), defaulting to what every unit already had; a unit
   carries `RestartPreventExitStatus=` only when asked.

5. **The tray's list.** Installing a unit with `user_services` also writes one
   row for it, `{name, unit, scope: user, description}` (the name is the
   catalog unit without a `-service` suffix: `gps-tether`, `rig`; the unit is
   its first service), to `~/.config/hammunition/devctl-services.yaml`, which
   hammunition-tray's helper reads (contract v1) to know which user services
   it may switch. Mode 0600, written atomically through a temporary file
   renamed over the name, header `# Written by `hammunition install` (D-073,
   amended 2026-10-02).`, through the operator-home walk when root acts for an
   operator; a file it cannot parse is left alone and said so. The plan
   discloses it and uninstall removes the row, deleting the file when none is
   left. Not yet read by the helper on a machine.

The first plain service is the `gps-tether` unit: `hammunition-gps-tether`
(its own repository) installed as the tag's source tree, a `binary` tarball
pinned by sha256 and unpacked with `install_tree` beside skid-finder's, run in
place by the unit with `/usr/bin/env PYTHONPATH=… /usr/bin/python3 -P -m
hammunition_gps_tether` on 127.0.0.1:10110 and :10111 (D-071 note). Pin: tag
`v0.1.1`, commit `d68bc888d1a44ecce74604d6299f991a72a7ae54`, tarball sha256
`8d2c78b760a622bf682ecd3616af5b807e6653fd1bb05fd6fa28a5761ef456f6` (fetched
twice, identical). **Amended 2026-10-02 (v0.1.1, the maintainer's ruling):** the
tether exits 3 on a refusal (a taken port or socket, root, an unusable option),
1 on an uncaught crash and 2 on a usage error, and the unit carries
`RestartPreventExitStatus=3`, so a refusal is not retried and a crash is; v0.1.0
exited 1 for both and the unit stopped retrying both. The project publishes no
wheel or release file for v0.1.1, which is why the pin is the tag's own tarball; a wheel would be the
better artifact. A venv requirement carrying the all-zero digest
(`UNPINNED_SHA256`), the convention for an unfinished pin, is refused by name at
plan time (a binary artifact is not: fixtures across the suite use zeros for a
dummy .deb). Not yet run on a machine; the bench owes the service at login and after
a reboot.

---

## D-074 — Repeater data beyond RepeaterBook: one layer per source, Open Repeater pinned as data, OpenStreetMap filtered from the extracts already here, the ETCC and Brandmeister on request with hotspots dropped, and what the station heard kept apart

**Date:** 2026-10-01. **Status:** proposed (the spike's recommendation,
approved by the maintainer with three items left to him; implemented on
branch `repeater-sources`; the maintainer decides it at review).
**Spec:** `docs/superpowers/specs/2026-10-01-repeater-sources-design.md`.
**Numbering:** assigned with the task; D-073 is not on this branch.
**Depends on:** D-064 (the overlays this adds layers to), D-049 (a data
unit), D-066 (a generated pin), D-070 (the mirror and `artifacts`), D-033
(an unlicensed source judged on what we do with it), D-035 (a station value
is the operator's), D-021 (disclose, never adjudicate), D-031 (the input's
date), D-057 (a region says where the operator is), D-059 (documents).
**Amends:** D-064: an import now replaces its own layer, not every layer,
and `remove` takes `--layer`.

### What was measured

The spike of 2026-10-01 (scratchpad, not committed) measured every bulk
source it could find on three public example areas (Delaware, Vermont,
Shenandoah), with hearham as the baseline: hearham's bytes changed six rows
in ten minutes and cannot be pinned. **Open bulk data for the US barely
exists**: all the sources below together added about one repeater over
hearham in the three areas (one Brandmeister repeater in Vermont).

- **Open Repeater**: one URL, no key, 241 kB, 461 repeaters, byte-identical
  over three fetches, "Data licensed under CC0 1.0" and `"license": "CC0"`
  in the file. Sweden 243, Malaysia 121, India 96, Canada 1, US 0. The
  only CC0 bulk directory found. Pinned on this branch at sha256
  `07be1cba…`, 240,723 bytes: the same digest the spike saw, and twice
  more by `--check`.
- **OpenStreetMap**: the keys in use are `communication:amateur_radio:*`
  (925 objects worldwide); the keys guessed beforehand have zero uses. The
  pinned Delaware extract has none, Vermont's one (a duplicate of hearham's),
  the whole US 47 by Overpass. The frequency is written as `145350000`,
  `146.685`, `146685` and `1466100000` (146.61 MHz ×10).
- **UK ETCC** (`ukrepeater.net/csvcreate_all.php`): 803 rows, 62 kB,
  identical over three fetches, positions at locator precision, no licence
  or terms stated, openly offered.
- **Brandmeister** (`api.brandmeister.network/v2/device`, no key): 31,993
  devices; 2,857 are 6-digit ids with transmit ≠ receive. The rest are
  personal ids and simplex hotspots: somebody's house. No terms published.
- **Direwolf 1.8.1's `-l` log**, measured here by decoding synthetic
  objects (`gen_packets` into `direwolf -l`): the header is
  `chan,utime,isotime,source,heard,level,error,dti,name,symbol,latitude,longitude,speed,course,altitude,frequency,offset,tone,system,status,telemetry,comment`,
  with the frequency (MHz), offset (signed kHz) and tone (Hz) already
  decoded from the object. The log shows no killed flag.
- The HEADs for the docs, 2026-10-01: both URLs answered 200, no `ETag`,
  no length; ETCC `application/csv`, `Cache-Control: max-age=0,no-store`,
  Brandmeister `application/json`, `no-cache, private`.

### The rule

1. **One layer per source**, each three files plus its rows as data
   (`.rows.json`) under its own stem in D-064's directory:
   `export` (D-064's, unchanged names), `open-repeater`, `osm`, `etcc`,
   `brandmeister`, `aprs-heard`. Each name carries the source, the date and
   the licence or the status (`unverified`, heard off the air). One command
   writes one layer and leaves the others.
   QMapShack's `poiPaths` holds the directory while any layer has a `.poi`;
   the operator's Navit copy has one textfile map per layer present.
2. **Open Repeater is a D-049 data unit**, `open-repeater`, `CC0 1.0`, in no
   profile. Its sha256, size and date are written into the manifest by
   `scripts/gen_open-repeater-pin.py`; `--check` fetches and compares (the
   weekly pin review), `--check --offline` runs in the suite. The URL is
   not dated, so the pin dies whenever the site changes and the install
   refuses the file by its digest until it is regenerated; a mirror holding
   the pinned bytes still serves it, and `artifacts` lists it for the
   Bunker as `open-repeater/open-repeater.json`. `import
   --from-open-repeater [FILE]` reads the installed file or a downloaded
   copy; the layer is dated by the newest `last_verified`.
3. **OpenStreetMap is an explicit `import --from-osm`**, not a converter
   run with the maps (ruling): conversions run as root inside `install`,
   this layer is the operator's file, D-057 keeps parsing downloaded data
   out of root, and the measured yield is close to nothing in the US.
   `osmium tags-filter` runs as the operator over every installed extract;
   nothing is downloaded. A bare frequency is tried as MHz, kHz, Hz and Hz
   ×10 and the first landing in a repeater band is taken (no number lands
   in a band under two of them); an unsigned shift is noted, not claimed.
   The document names the extracts' directory with no digest, and skips are
   numbered in reading order, never by OSM id: a region, a region's digest
   and an OSM id each say where the operator is.
4. **ETCC and Brandmeister are fetched on request only**, through D-064's
   fetch (bounded, HTTPS-only redirects), parsed in memory, marked
   *unverified* with the observed sha256, carried under D-033, no JSON form.
   **Brandmeister keeps only a 6-digit id whose transmit and receive
   differ**; every hotspot is dropped before anything is written, and only
   the counts are printed. The ETCC's locator precision is in every
   description.
5. **Direwolf's log is its own layer**, `Repeaters heard off the air (APRS
   objects, YYYY-MM-DD)`: rows with `dti` `;` and a frequency in a repeater
   band, the newest hearing kept, dated by the newest hearing. **Never
   merged into the directories.** No network and no login.
6. **The all-sources file**, `repeaters-all.gpx`, is rebuilt after every
   import, fetch and remove from the directory layers when two or more can
   be read, and deleted otherwise. Precedence, best first: the operator's
   export or list; the ETCC; Open Repeater; hearham; Brandmeister; OSM. A
   row joins a kept row **of another layer** with the same output frequency
   that is within 0.02°, or has the same callsign and is within 0.25°; the
   kept row keeps its position and fields, fills an offset, tone, mode or
   place it lacks, and names every source in its description. Every join is
   counted and printed. GPX only. A layer without `.rows.json` (written
   before this) is named as left out.
7. **D-064's `import FILE...` refuses** an Open Repeater file, an ETCC CSV
   and a Direwolf log by name, pointing at the route that makes each its own
   layer; the `--from-*` options exclude each other and files; `--exported`
   dates an export only.
8. **Documents.** `repeaters` gains `layer_id` and `all_sources`;
   `repeaters-removed` gains `layers` and `all_sources`. Fields added within
   schema 1. Neither carries a callsign, a position, a region or a region's
   digest.

### Rulings made on the way

- **The spike's cross-source rule was bounded.** As written ("same Hz and
  either the same callsign or within 0.02°") it joined two rows of one hand
  list, the same call and frequency 120 km apart, on the first CLI run: the
  case D-064 measured 1,050 times in hearham alone. Rows of one layer now
  never join each other (D-064's key already decided them), and a callsign
  match counts within 0.25° (about 25 km), which still catches a directory
  that places a repeater at its town.
- **The generator writes into the manifest**, not a separate pin file: a
  data artifact carries its sha256 inline (the schema), and a second copy
  in `catalog/data/` would be duplicated data. It finds each of its three
  lines exactly once and re-reads the manifest after the write.
- **The script keeps the name the task gave it**, `gen_open-repeater-pin.py`;
  ruff and mypy accept the hyphen, and its tests load it by path.
- **The capability matrix took only this manifest's delta.** The checkout
  has no probe sweep (`reference/probes/` is gitignored, and the only copy
  found was older than the committed page). The generator was run with and
  without the new manifest over that copy, and its difference (one `data`
  row, `build` +1 per target, 296 manifests) applied to the committed page;
  the page is otherwise unchanged until the next sweep.
- **The Direwolf fixtures are Direwolf's own output** for synthetic packets
  (`tests/fixtures/repeaters/direwolf-packets.txt`), so the columns and
  units are measured, not recalled.
- **Open Repeater's layer is dated by its data**, the newest
  `last_verified`; the installed file's modification time is the install's.
- **From the final review.** A `.rows.json` is type-checked field by field
  and a wrong one skipped by name (a string latitude had crashed the merge
  and `true` was read as 1 Hz). The all-sources rebuild has its own failure
  path: the layer is written and registered, the view carries `error`, and
  the command exits 1. Every `--from-osm` message names an extract by its
  number, never its file, osmium's stderr included. An unsigned OSM shift
  falls back to `frequency_in` for its direction. A `remove --layer` that
  leaves one directory layer lists the all-sources file it deleted. The
  generator catches `http.client`'s exceptions and never leaves its
  temporary file.

### Not carried, and why

RepeaterBook bulk (written permission needed for bulk extraction,
mirroring and offline bundling); RadioReference (private viewing only
without a licence); RFinder (a paid app, no bulk data); the ARRL directory
(RepeaterBook's data, same terms); FCC ULS (repeaters are not licensed
individually; records carry a mailing address); RadioID (its terms exclude
mapping and re-publication; no coordinates); the WIA CSV (all rights
reserved; no coordinates); repeatermap.de (a token on request only); D-STAR,
YSF and NXDN lists (personal-use HTML, or internet reflectors without
coordinates). **ACMA's register** (Australia) is a route named, not built:
a 67.5 MB daily file whose licence permits derivatives with attribution,
493 repeaters with positions; a later data unit, with a generated extract
and a decision on who hosts it.

### Left to the maintainer, not built

Whether the Bunker may hold unverified snapshots of hearham-class data
(hearham, the ETCC, Brandmeister); writing to the RSGB, the US councils
and Brandmeister for an explicit licence, the only route to real US gain;
any APRS-IS capture (aprsc refused `N0CALL` and `NOCALL`, and whether a
non-callsign login is acceptable is his call).

### What has run

The test suite: every parser against synthetic fixtures, each skip and
refusal, the four frequency spellings, the hotspot filter, the Direwolf log
captured from Direwolf, osmium over a synthetic extract built by `osmium
cat` (skipped where osmium is absent), the cross-source join and its
bounds, the layers side by side and removed by id, both fetches against a
loopback server, both documents validated with no private value in either,
`artifacts` listing the unit, and the generator against a faked fetch, its
offline check falsified three ways. On the development host: the pin
generated from openrepeater.org once and checked twice. No GUI was started
and nothing was installed.

**Owed to the bench:** `install open-repeater` from the publisher and from
a Bunker; QMapShack listing each layer's `.poi` as its own collection and
loading `repeaters-all.gpx`; Navit drawing several textfile maps from one
mapset; a real `fetch-etcc` and `fetch-brandmeister`; `--from-osm` over the
laptop's own extracts; a day of Direwolf's real log.

**Consequences.** `src/hammunition/repeater_sources.py`; layers, rows
files and the all-sources file in `src/hammunition/repeaters.py`; the
import options, two fetch commands and `remove --layer` in
`src/hammunition/cli/main.py`; the documents in
`src/hammunition/interface/repeaters.py`;
`catalog/packages/open-repeater.yaml`; `scripts/gen_open-repeater-pin.py`
and its weekly CI step; tests `tests/test_repeater_sources.py`,
`tests/test_repeater_sources_cli.py`, `tests/test_repeater_layers.py`,
`tests/test_gen_open_repeater_pin.py`; the offline-navigation guide's
section 13, `docs/guides/lan-mirror.md` and `docs/reference/cli.md`.

### Amendment, 2026-10-01 — the ACMA register is built: a `register` unit, unverified because nothing else is possible, and a layer filtered to the installed regions

**Status:** proposed (the task named the route; implemented on branch
`acma-register`; the maintainer decides it at review). **Supersedes** the
"Not carried" sentence above that named the ACMA register as a route not
built, and rule 6's precedence list, which gains the regulator.

**What was measured** on 2026-10-01, with two requests to
`https://cdn.acma.gov.au/rrl/spectra_rrl.zip` (a `HEAD`, then one `GET`
asking for a current Azure storage API version):

- 67,513,955 bytes, `Last-Modified` 21:31:17 GMT, served from Azure Blob
  storage behind Front Door. **The ETag, `0x8DF200353F0B4AF`, is Azure's
  version stamp, not an MD5**, and no `Content-MD5` is stored, even when
  the 2021-08-06 storage API is asked for. The ACMA publishes no checksum
  anywhere the file names. The spike's "a dated, hashable file" was true of
  the bytes and not of any check the publisher offers, so the Copernicus
  route ("from the publisher's object metadata; not pinned by Hammunition")
  does not exist here, and a sha256 pin dies with the next day's rebuild.
- 31 members. `LICENCE.TXT`, "LICENCE TO USE THE REGISTER OF
  RADIOCOMMUNICATIONS LICENCES": clause 5 licenses use, reproduction,
  adaptation, derivatives and their distribution; clause 8 forbids a
  natural person's Client Information in a derivative; clause 9 requires
  "Based on Australian Communications and Media Authority information" on
  anything derived. The manifest quotes clauses 5, 8 and 9 verbatim.
- `licence.csv`: sub-service 602, *Amateur Repeater*: 501 granted, 12
  expired, 1 not granted. `device_details.csv` (386 MB uncompressed): 3,939
  devices on those licences, 1,981 transmitters and 1,958 receivers; a
  transmitter pairs with the receiver of the same licence and `EFL_SYSTEM`
  in 1,853 cases. 1,784 granted transmitters have a site, 473 callsigns.
  `site.csv`: 506 sites, every one with a position; 347 "Within 10
  metres", 94 "Within 100 metres", 65 "Unknown". The member timestamps
  (2026-10-02 07:31, the ACMA's local time) are the register's own date.
- Read through the new reader on this machine: every Australian
  transmitter in one box kept 1,768 rows, 1,696 after D-064's merge, in
  about 16 seconds; a Tasmanian box kept 109 (106 merged).
- A third request, a `HEAD` for a licence page on `www.acma.gov.au`, was
  reset by the server; the licence is stated only inside the file, which
  is why the manifest's `licence_url` is the file itself.

**The rule.**

1. **A new install method, `register`**, `provider: acma-rrl`, with the
   URL, the file name and the check in the engine (`src/hammunition/acma.py`),
   never the catalog: the `dem-tiles` shape. `data`'s rule that every
   artifact carries a sha256 is left exactly as it was.
2. **The check is the file's own structure**: the members the import reads
   and their header columns, a cap on what the members declare
   uncompressed, and every member's CRC-32. It catches a damaged, cut-off
   or substituted-by-a-web-page download, never a file altered at the
   source. `Fetcher.fetch_checked` runs it on what arrived, from a LAN
   mirror first and the publisher second, and never reuses a cached copy.
   The plan prints "about 67.5 MB" (the size measured here; the file moves
   daily) and "unverified" with what is checked.
3. **In no profile, installed by name only**: the FSTopo ruling of
   2026-10-01 (D-068, amended) applies as written, and unlike FSTopo there
   is no condition under which it can rejoin one, since nothing can be
   pinned.
4. **`artifacts` lists it** as `acma-register/spectra_rrl.zip`, check
   `unverified-zip`, digest null, the size from one `HEAD` to the ACMA per
   listing (deferred, with the reason, when that fails).
5. **`maps repeaters import --from-acma [FILE]`** writes the `acma` layer,
   *Repeaters (ACMA, YYYY-MM-DD)*, dated by the register: transmitters on
   granted 602 licences with a site, a callsign and a frequency from 1 to
   10,000 MHz, whose site lies in the bounding box of an installed region
   extract (each box read from the extract's PBF header, as Navit's centre
   is, D-057). A row outside every box is counted, never numbered.
   No region, an extract with no readable box (named by its number), or no
   row inside any box is refused and nothing is written, the last saying
   the register covers Australia only. The emission designator is kept
   with plain words for the classes the 2026-10-01 file uses (`FM
   (16K0F3E)`); there are no tones in the register.
6. **`client.csv` is never opened**, which a test proves; the layer carries
   the site's name and state, the licence number and the site precision.
   The installed file still holds licensees' names and addresses, and the
   guide and the Bunker page say to keep it to the operator's own machines.
7. **Precedence, best first:** the operator's export or list; the
   regulator (the ACMA); the ETCC; Open Repeater; hearham; Brandmeister;
   OSM. The ACMA and the ETCC never cover the same place, so their order
   decides nothing today.

**Rulings made on the way.**

- **The task's `check: etag-md5` was measured away**, not adopted: an
  Azure ETag is not a digest, and carrying it as one would print a check
  the engine never makes.
- **No plan-time network.** The plan prints the measured size as "about";
  a dry run stays offline. Only `artifacts`, which already asks Geofabrik
  and the Copernicus bucket, asks the ACMA for the day's size.
- **Bounding boxes from the installed extracts**, not station config: the
  same source `--from-osm` reads, offline, and what the station actually
  has. A box is a rectangle, so it can take in sites over a state border;
  the guide says so.
- **An empty layer is refused, not written**, the existing rule for an
  import that keeps nothing; an older `acma` layer is left as it was.
- **The import does not re-run the CRC pass** over 600 MB the install
  already checked; it checks the tables and columns, then reads.
- **From the final review.** A table stored in a compression `zipfile`
  cannot read (or encrypted) raised `NotImplementedError` (or
  `RuntimeError`) past the check and the import, a traceback rather than a
  named refusal; both are now the check's "damaged" error, and a test
  crafts such a member.

**What has run.** The test suite: the check against a synthetic register
in the real file's column layout, falsified by a flipped byte in a member
nothing else reads (caught only by the CRC pass, which the test proves by
passing the same file without it), a truncated zip, a web page, a missing
table, a renamed column and the inflation cap; the reader's every skip and
its pairing, the antimeridian, `client.csv` never opened; the backend from
the publisher and from a mirror, a damaged mirror copy passed over; the
plan's text and document; `artifacts` with a fake `HEAD`; the CLI end to
end with real PBF headers around Tasmania and Victoria. On the
maintainer's laptop, from a scratch directory in the worktree: the reader
over the real 2026-10-01 file (about 16 seconds a pass), the file deleted
after. Nothing was installed through the engine and no GUI started.

**Owed to the bench:** `install acma-register` through the engine, from
the ACMA and from a Bunker; `--from-acma` over a real Australian extract's
header (only synthetic headers have been read); QMapShack and Navit drawing
the layer; and the maintainer's decision whether a Bunker may hold a file
that carries licensees' client information.

**Ruling, 2026-10-02 (maintainer).** A Hammunition Bunker may hold the
register zip although it contains `client.csv` (licensees' names and
addresses, which clause 8 of the register's licence bars passing on),
because the Bunker exists for operators to download their own data and set
up their station, the mirror is LAN-only by documented rule, and the engine
never opens `client.csv`. An operator who does not want it on their NAS sets
Bunker's `hold_unverified = false` (a switch being added to Bunker now).
The "owed to the bench" question above is closed by this ruling; the
install-through-the-engine and drawing checks are still owed.

### Amendment, 2026-10-03 — Canada measured and not carried; the on-request lists a Bunker may hold (maintainer's rulings)

**Canada.** Measured, and not carried, for the same reason each time:
- **TAFL** (ISED's Technical and Administrative Frequency Lists) is the one
  Canadian bulk file with positions, under the Open Government Licence -
  Canada. It has **no amateur rows**, and every row it does have carries a
  licensee's name and address; it is also monthly and unversioned. Not carried.
- **ISED's amateur call-sign file** is names and addresses only: no repeater,
  no frequency, no position. Not carried.
- **No open bulk Canadian repeater source exists.** The routes are the
  operator's own RepeaterBook export (`maps repeaters import`) and hearham
  (`fetch-hearham`); the guide says so in one sentence.

**Snapshots on a Bunker (D-078).** The open question in "Left to the
maintainer" above, whether a Bunker may hold the unverified on-request lists
(hearham, the ETCC, Brandmeister), is ruled: it may, under Bunker's
`hold_unverified`, the same switch as the ACMA zip. `hammunition artifacts
--json` lists them as unit `repeater-snapshots`, check `unverified-fetch`, and
`fetch-etcc`, `fetch-brandmeister` and `fetch-hearham` read
`<mirror>/repeater-snapshots/<name>` first. The writing-to-the-councils item
in the same section is also closed: no licence letters are sent on the
project's behalf (D-078).

**Consequences.** `src/hammunition/acma.py`; `RegisterInstall` in
`src/hammunition/manifest/schema.py`; `Fetcher.fetch_checked`;
`DataBackend.register_steps`; the plan's `data` lines gain `verified_by`
and `approximate`; `unverified-zip` in the `artifacts` contract;
`read_acma` in `src/hammunition/repeater_sources.py`; `--from-acma` and
`remove --layer acma` in `src/hammunition/cli/main.py`;
`catalog/packages/acma-register.yaml`; tests `tests/test_acma.py`,
`tests/test_acma_backend.py`, `tests/test_acma_cli.py`, with
`tests/acma_support.py`; the guide's section 13, `docs/guides/lan-mirror.md`,
`docs/reference/cli.md`.

**Amendment, 2026-10-04: front ends read the layers through
`maps repeaters list --json`, never the files.** A front end (Hammunition Hill's
repeaters panel is the first) reads the layers through the `repeaters-list`
document: every readable layer with its date, sources and files, the layers
joined in memory as `repeaters-all.gpx` is (the heard layer apart), each row
with the layer it came from, and one credit text per source present. The
command is read-only and returns a partial list with exit 0 when a layer
cannot be read, naming it in `skipped`. The document carries `personal_use`
per layer and per row (true for any RepeaterBook source, D-081) so a front end
can refuse to serve those rows beyond the machine: D-081 keeps RepeaterBook
data off a Bunker, and a program that repeats the rows over a network breaks
that as surely as a mirror would. The position of the station is not in this
document; a front end reads it from `station show --json`.

**Amendment, 2026-10-04 (#313): a mode vocabulary, a band, digital details, and
a lookup by place.** The maintainer asked for resources to "look up and key in
on in an emergency": a repeater near a place, by what it speaks. `mode` was
free text filled differently by each source, so nothing could filter on it.
`Repeater.modes` is now a tuple from a fixed vocabulary (`FM`, `DMR`, `D-STAR`,
`YSF`, `P25`, `NXDN`, `M17`, `TETRA`, `ATV`), each source's spelling mapped
onto it by `normalise_modes` (RepeaterBook's `FM Analog` and `System Fusion`,
hearham's `D-star`, ETCC's `C4FM`, ACMA's emission words); a word outside the
list stays in `mode` and never reaches `modes`, and a test asserts every
spelling the fixtures carry maps somewhere, so a new source cannot add one
silently. `mode` stays and the document renders it from `modes` unless the
source said more than the vocabulary holds (`FM voice (F3E)`). `digital` holds
only what a source supplies, under eight fixed keys: RepeaterBook's API gives
`DMR Color Code`, `DMR ID` and `P-25 NAC` (measured from the client's
`RepeaterJSON`, no D-STAR module, YSF DG-ID or NXDN RAN key exists there),
Brandmeister gives its colour code, id and the network, and hearham, OSM, ETCC,
ACMA and the hand CSV give none; nothing is inferred. `band` comes from the
output frequency by the ITU/US amateur table in `BANDS`. `.rows.json` gains
`modes` and `digital`; a file written before them still reads, `modes` derived
from `mode`. Cross-source joins keep the union of `modes` and the first
non-empty `digital`.

`maps repeaters list` gains `--near GRID|LAT,LON`, `--within KM`, `--band` and
`--mode`. With a centre (the argument, else the station's grid square when one
is set; neither is not an error, there are simply no distances) every row
carries `distance_km` and `bearing_deg` (haversine on the 6371.0088 km sphere,
initial bearing) and rows are nearest first. This **supersedes the last
sentence of the amendment above**: the document now carries `centre`, which
for the station's square is the centre of that square, so the `repeaters-list`
document is a document for local programs and not for pasting, like `station`.
The text lists the matching repeaters only when a place, band or mode is named;
a bare `list` is still the layers' summary.

QMapShack: the `.poi` has one child category per mode present under *Amateur
radio repeaters*, plus *Unknown mode*, and a repeater is in every category it
speaks (`poi_category_map` is many to many), so ticking a category in the POI
dock is the mode filter. QMapShack 1.17.1 (source at `V_1.17.1`, read, not run)
builds the tree from `poi_categories` in descending id order and attaches a
child only to a parent already seen, so a parent's id must exceed its
children's: the writer numbers children 1..n and the layer category n+1.
The POI dock has no text search; the Workspace dock's filter does, and its
default full-text mode and its name-only mode are case-insensitive substring
matches (`CSearch.cpp`, `CGisItemWpt.cpp`), so the waypoint name now carries
`N0CALL 146.940 2m FM DMR` and the GPX `<name>`, the POI name and the Navit
label all carry it. A GUI run is the maintainer's: this is measured from the
source, not observed.

**Amended 2026-10-04 (#325, epic #326): RepeaterBook is one layer per state.**
The maintainer, after fetching several states: "it's taking a long time to
load a few states." Every `fetch-repeaterbook --state` had merged into the one
`repeaterbook` layer, one `.poi` for every state fetched, and QMapShack's POI
dock has one tick box per file. The layer id is now `repeaterbook-<AREA>`:
the US postal code upper-cased (`repeaterbook-OH`), or RepeaterBook's
`state_id` outside the US (`repeaterbook-CA01`), each with its own four files
(`repeaters-repeaterbook-OH.*`) and title `Repeaters (RepeaterBook OH, personal
use, YYYY-MM-DD, unverified)`. The id is validated (two letters and up to four
digits, or digits alone, after upper-casing), so a state's name cannot pass for
a code and nothing but letters and digits reaches a file name. `LAYERS` keeps
its fixed ids and a pattern covers the areas (`is_layer_id`, `layer_stem`,
`known_layers`); `present_layers`, `remove --layer`, `list --layer`, the
all-sources rebuild and both registrations take them. A re-fetch of a state
replaces that state's layer whole: the newer fetch is the truth, and the
merge across runs is gone (the merge inside one run, over counties, stays).
`--county NAME` (repeatable, with exactly one `--state`) uses the client's own
`ExportQuery.counties`, read in 0.13.0's source, one request per county; an
answer near the 3,500-row cut prints the way to ask by county. The earlier
merged `repeaterbook` layer stays registered until the operator removes it;
the fetch says so once. `LayerView` gains `area`. Measured from QMapShack
1.17.1's source, not run: drawing is viewport-bound, not load-everything (see
the guide), so the split buys smaller files, one tick box per state, and a
state outside the view costing one bounds test per cell; it does not make a
big state faster to draw. On-screen timing is the maintainer's. The
per-region option for the other POI layers (`--per-region`) is a separate
change.

## D-075 — Infrastructure and EMCOMM layers: eight OpenStreetMap layers from the extracts already here, FAA NASR, EIA-860M and WRI as pinned data, FCC ASR and NOAA Weather Radio on request, an `infra` tile layer and GeoJSON overlays on the browser map

**Date:** 2026-10-01. **Status:** proposed (the spike's recommendation,
built with the maintainer's delegate's two rulings below; implemented on
branch `infra-layers`; the maintainer decides it at review).
**Spec:** `docs/superpowers/specs/2026-10-01-infra-layers-design.md`.
**Numbering:** assigned with the task, after D-073 and D-074.
**Depends on:** D-074 (one layer per source, the shape every piece here
copies), D-064 (the overlays and their registration), D-049 (a data
unit), D-066 (a generated pin), D-070 (the mirror and `artifacts`), D-071
(the browser map and its converter), D-057 (a region says where the
operator is), D-033 and D-021 (an unverified source, carried on what we
do with it), D-031 (the input's date), D-059 (documents).
**Amends:** D-071: the tile converter's profile gains an `infra` layer
(`tilemaker-pmtiles 2`), and the map page draws it and the overlays.
D-064: the operator's Navit copy carries the infrastructure layers too, and
`maps qmapshack` keeps both overlay directories in `poiPaths`.

### What was measured

The spike of 2026-10-01 (scratchpad, not committed) measured every source
it could find on Delaware and Vermont (Geofabrik extracts of 2026-09-30,
both MD5-checked), the counts per OpenStreetMap tag set, every federal
file's size, licence and byte stability, and Open Infrastructure Map's
pipeline. Measured again on this branch:

- **The eight OpenStreetMap layers through the engine's own filter and real
  osmium** (2.4 s and 3.5 s): Delaware medical 187, responders 158, supply
  573, shelter candidates 671, transport 72, power 225, telecom 314, water
  163; Vermont 199, 367, 885, 1,801, 123, 874, 174, 105. Every count is
  the spike's but Delaware's responders: two objects carry both a fire
  station's and an ambulance station's tag and are one point here, two
  rows in the spike's sum.
- **The federal files the spike kept, read by the engine's parsers with
  each extract's header box** (no network): FAA NASR 115 and 190 sites (in
  the boxes; 38 and 90 in the states, the spike's), EIA-860M 138 and 164
  operating plants, WRI 0 and 2 plants outside the US, FCC ASR 668 and 489
  structures in the boxes (266 and 189 registered in the states, exactly
  the spike's count of constructed or granted, not dismantled, with a
  structure coordinate), NOAA Weather Radio 12 and 21 transmitters. The
  EIA workbook's 56 MB sheet streamed in 4.1 s; the process peaked at
  138 MB with the FCC archive in memory.
- **The pins, generated once from each publisher and their bytes fetched
  and matched twice more:** NASR cycle 2026-10-01, 8,030,968 bytes, sha256
  `aba48ea8…`; EIA-860M August 2026, 13,955,142 bytes, `b4b70abb…`; WRI
  v1.3.0, 4,178,889 bytes, `59f3e573…`, MD5 `8b4e4715…` equal to S3's
  ETag. All three digests are the spike's.
- **HEADs, 2026-10-01:** nfdc.faa.gov answered a HEAD of the pinned NASR
  file with 503 twice while serving it by GET. data.fcc.gov answered a HEAD
  of `r_tower.zip` from User-Agent `hammunition` with 403, from curl and
  from `hammunition (+https://github.com/Renegade-Penguin/Hammunition)` with 200
  (37,810,019 bytes, Last-Modified 2026-09-27); one request each, so the
  User-Agent is the observed difference, not a proven cause. weather.gov
  served `ccl-data.js` with 200, no length, Last-Modified that day,
  `max-age=180`.
- **The tile layer:** Debian's tilemaker 3.0.0 (the spike's extracted
  package) ran the generated profile on the Delaware extract: exit 0 in
  27 s at `--threads 2`, 20,602,464 bytes against the spike's 20,113,393
  without it (+2.4 %), `infra` the 17th layer with `class`, `subclass`,
  `voltage_kv` (a number), `operator`, `name`, `plant:source` and
  `generator:source`.
- **The browser map**, drawn by headless Chromium against 127.0.0.1 with
  the spike's local copy of the kit (not the pinned archives), those tiles
  and three real overlays (Delaware's medical and power layers and its
  NASR sites): idle at zoom 12, 119 infra tile features and 45 overlay
  points rendered, no map error, no request off loopback, every licence
  line in the credit.
- QMapShack 1.17.1's built-in waypoint symbols, from
  `helpers/CWptIconManager.cpp` (the source D-064 measured): 97 distinct
  names; the spike's `Radio Beacon` is not one, and would draw as
  `Default`. Navit 0.5.6's stock layout draws and labels `poi_custom0` to
  `poi_customf` with the item's own `icon_src`; every icon used is a file
  the `navit` package ships.

### The rule

1. **One layer per source**, four files under its own name in
   `~/.local/share/hammunition/overlays/infra/` (directory 0700, files
   0600, each renamed into place): `infra-<id>.gpx`, `.poi`, `.navit.txt`
   and `.geojson`. Thirteen layer ids: `osm-medical`, `osm-responders`,
   `osm-supply`, `osm-shelter-candidates`, `osm-transport`, `osm-power`,
   `osm-telecom`, `osm-water`, `faa-airports`, `eia-plants`, `wri-plants`,
   `fcc-towers`, `nwr`. Each has a QMapShack built-in symbol and its own
   Navit `poi_custom1` to `poi_customd` type with a stock icon. One command
   writes its own layers and leaves the others; refused as root.
2. **OpenStreetMap, the tag sets exactly as the spike measured them**,
   filtered by `maps infra import --from-osm [--layers …]` with one
   `osmium tags-filter` per installed extract, as the operator; nothing is
   downloaded. A node is placed where it is, a way at the mean of its
   distinct nodes, a relation at the mean of its member nodes and member
   ways' nodes. An object in two kinds of one layer is one point; a point
   two overlapping extracts both hold is kept once and counted. Licence
   line `© OpenStreetMap contributors, ODbL 1.0`. **Not points:**
   `amenity=shelter`, sirens, defibrillators, assembly points, emergency
   phones and water points, `emergency=shelter`, social facilities, power
   towers, poles and generators, hydrants, bridges and lines.
3. **Three D-049 data units, in no profile** (the maintainer decides),
   mirror-aware and listed by `artifacts` like any pinned data:
   `faa-nasr-airports` (the NASR APT CSV zip at its dated 28-day URL;
   `scripts/gen_nasr_pin.py` computes the AIRAC cycle from 2026-01-22 and
   writes URL, sha256, size, the cycle as `version` and the licence line
   `FAA NASR <cycle>, public domain`, which is what the plan prints;
   `--check` fetches the file by GET and goes red on a newer cycle),
   `eia-860m` (the month's workbook; `scripts/gen_eia860m_pin.py` pins the
   newest month with EIA's acknowledgment, exactly `Source: U.S. Energy
   Information Administration (<Mon YYYY>), public domain`; `--check` names
   a newer month and the move to `archive/xls/`; `--follow-move` keeps the
   pinned month at its archive address after matching its digest) and
   `wri-power-plants` (WRI v1.3.0, CC BY 4.0; `scripts/gen_wri_pin.py`
   checks the bytes' MD5 against S3's ETag, the publisher's checksum, and
   records it beside our sha256; `--check` HEADs the ETag). The weekly pin
   review runs all three. `--from-nasr`, `--from-eia` and `--from-wri` read
   the installed file only and keep what lies in the installed extracts'
   header boxes. **WRI is used outside the US only**: its US rows are
   EIA's of 2019, every one is dropped, and the import says EIA-860M
   covers the US.
4. **FCC ASR and NOAA Weather Radio are fetched on request**
   (`maps infra fetch-fcc-asr`, `fetch-nwr`), through D-064's bounded,
   HTTPS-only fetch with a descriptive User-Agent, parsed in memory, kept
   to the boxes, marked unverified with the observed sha256, no `--json`
   form. **FCC: only `RA.dat` and `CO.dat` are opened**; `EN.dat`, the
   owners' contact names, e-mail addresses and telephone numbers, never
   is, and of `RA` the signature and street-address fields are never kept.
   A structure is kept when constructed or granted, not dismantled, with a
   structure coordinate. Layer `FCC towers (unverified, <file date>)`,
   licence line `FCC Antenna Structure Registration, US Government work,
   public domain`. **NWR: frequency, power, WFO and every county's SAME
   code are kept; `status` is never read.** A transmitter within 1.0° of a
   box is kept (0.5° missed one of Vermont's twelve covering transmitters;
   1.0° missed none). Licence line `NOAA/NWS, public domain, not an
   official NWS product`.
5. **Registration as D-074's.** QMapShack's `[Canvas] poiPaths` holds the
   directory while any layer has a `.poi`; the operator's Navit copy
   carries every repeater and infrastructure layer; `maps qmapshack` and
   `navit-offline` keep both kinds. `maps infra remove [--layer ID]` is
   idempotent. A layer an import finds empty has its old files deleted and
   listed; an import finding nothing changes nothing and exits 1.
6. **The browser map.** The converter writes, as the operator, a wrapper
   Lua that `dofile`s the kit's unchanged `process-openmaptiles.lua` and
   adds an `infra` layer at zoom 10 to 14, and the kit's config with that
   layer; `CONVERTER` is `tilemaker-pmtiles 2`, so every region is rebuilt
   once. Style layers written here draw it over OSM Bright, power coloured
   by Open Infrastructure Map's `voltage_scale` (commit 5f20a29a) under its
   BSD-3-Clause notice, copied verbatim to
   `src/hammunition/map_style/LICENSE.openinframap`, served at
   `/map/infra-style-licence.txt` and named in the credit. Each infra
   layer's GeoJSON is found in the operator's directory when `reference
   serve` starts, served by exact name at `/map/overlays/<file>`, listed at
   `/map/overlays.json`, drawn as a toggled circle layer with its licence
   (escaped) in the attribution. A new pin or fetch never rebuilds tiles.
7. **Documents** `infra` and `infra-removed`, D-074's shape: route,
   licence lines, inputs (the extracts' directory with no digest), counts,
   skips, each layer's name, count and files, registration. No place's
   name or position, no region, no box, no extract digest.

### Rulings

- **From the maintainer's delegate (2026-10-01):** shelter-candidate
  layers carry "candidate, not a designated shelter" in the layer name and
  every waypoint description; FCC ASR is an on-request unverified fetch
  with an observed sha256, like the ETCC, not a data unit whose pin dies
  weekly.
- **Made on the way:** one QMapShack symbol per layer (the brief's), the
  kind in each name and description; NWR's symbol is `Information`, not
  the spike's `Radio Beacon`; the regions are the installed extracts'
  header boxes, never printed, so a layer reaches across a state line;
  `voltage_kv` (the first value, in kV) replaces the raw voltage so the
  ramp's step has a number; towers and poles stay out of the tile layer,
  as in the spike's measured Lua; no label layers on the tiles (glyph
  stacks unmeasured); NASR's check fetches by GET because the FAA refused
  HEAD; WRI has a weekly HEAD check though the brief named only NASR and
  EIA; the generators' licence lines are written quoted (EIA's is not plain
  YAML); months are named from our own table, never the locale; overlays
  appear after a server restart, as region maps do.
- **From the final review:** the converter first handed the profile's text
  to the operator's shell as one argument, which Linux caps at 128 KiB: a
  kit config past that failed the region with "Argument list too long"
  (reproduced by a test before the fix). The text now goes to the
  operator's `cat` on standard input; `Staging.run` takes `stdin`, and
  every other call is unchanged.

### Not carried, and why

**HIFLD Open**: DHS retired it; its page describes only HIFLD Secure ("a
GII account, a profile, and an approved Data Use Agreement"); what remains
are REST copies on other organisations and a cell-tower layer under the
Esri Master License Agreement. **OpenGridWorks**: `robots.txt` and its
plant page answered 429 behind a Vercel security checkpoint, no terms
could be read and no bulk file or API was found; its stated sources (EIA,
OpenStreetMap) are carried directly. **FEMA NSS open shelters**: live only,
five open nationwide on the day measured; useless offline outside an
event. **911 PSAP boundaries**: not public nationally. **USGS National
Structures**: 82 MB for Delaware's 998 points, which OpenStreetMap matches
or beats. **OpenFEMA**: county areas, not points, with no licence in its
metadata and a terms page that returned 403. Named routes, not built:
Canada's ISED TAFL file, the UK's OS OpenData, Europe's INSPIRE services,
Global Energy Monitor's trackers.

### What has run

The test suite: every tag set and placement on a synthetic extract (and
through real osmium where it is installed), each federal parser on
synthetic files in the publishers' layouts, the FCC contacts canary
(falsified: the street field put back turns it red), the NWR status never
written, the generators against faked fetches with their offline checks
falsified, both fetches against a loopback server, both documents
validated with nothing private in them, the converter's profile written as
the operator, the overlays served and listed. On the development host: the
pins generated and matched, the parsers and the OSM filter on the spike's
real files, tilemaker on Delaware, and the page in headless Chromium, as
above. No GUI was started and nothing was installed.

**Owed to the bench:** QMapShack drawing each layer's POI collection and
GPX with these symbols (in a VM, never on the desktop); Navit drawing the
`poi_custom1` to `poi_customd` layers with their icons; the browser map
with the pinned kit and a rebuilt region; `install` of the three units
from the publishers and from a Bunker; a real `fetch-fcc-asr` (the
User-Agent finding above) and `fetch-nwr`; `--from-osm` over the laptop's
own extracts; FCC ASR's bytes across a weekly rollover.

**Consequences.** `src/hammunition/infra.py`, `src/hammunition/infra_sources.py`,
`src/hammunition/interface/infra.py`, `src/hammunition/map_style/`; the
`maps infra` verbs and shared overlay registration in
`src/hammunition/cli/main.py`; the generic POI writer in
`src/hammunition/repeaters.py`; the converter in
`src/hammunition/backends/pmtiles.py`; the overlays in
`src/hammunition/map_page.py` and `src/hammunition/reference.py`;
`catalog/packages/faa-nasr-airports.yaml`, `catalog/packages/eia-860m.yaml`,
`catalog/packages/wri-power-plants.yaml`; `scripts/data_pin.py`,
`scripts/gen_nasr_pin.py`, `scripts/gen_eia860m_pin.py`,
`scripts/gen_wri_pin.py` and their weekly CI steps; tests
`tests/test_infra.py`, `tests/test_infra_cli.py`,
`tests/test_infra_sources.py`, `tests/test_gen_infra_pins.py`,
`tests/test_map_style.py`, `tests/test_map_overlays.py`; the
offline-navigation guide's section 18, `docs/guides/lan-mirror.md` and
`docs/reference/cli.md`.
**Amendment, 2026-10-04 (issue #327, epic #326; D-082).** The layers are kept
per region. `maps infra import` and the two fetches write one layer per theme
and installed region, the extract's file slug (the slug `hammunition.areas`
derives, `north-america-us-ohio`) ending the layer id and the four file names;
`infra.layer_area()` returns it, so D-082's rule takes the active regions'
files into QMapShack, Navit and the browser map with no other switch. The
OpenStreetMap themes split by the extract they are filtered from; the FAA,
EIA, WRI, FCC and NOAA lists, all of which carry coordinates, by clipping to
each region's header box as `--from-acma` does (a point in two overlapping boxes
is in both). `--merged` keeps the earlier shape, one layer across every region
with no area, always drawn. A merged file from before the split is left alone
and a note says once, in the import's `notes`, that it is still registered and
draws each point twice; the engine deletes nothing, `maps infra remove --layer
ID` does, and accepts a theme's id (the merged layer) or `<theme>-<region>`.
The documents' layer views carry `area`. The earlier ruling that no layer view,
path or name carries a region is withdrawn for these layers: their names and
paths name the regions, as the repeater layers' name their states. Tests:
`tests/test_infra_regions.py`.

---

## D-076 — GraphHopper routes on the browser map: a pinned jar, one graph built here from the station's regions, started by `reference serve` on loopback and reached only through its Host-checked `/map/route`; installed by name, never in a profile

**Date:** 2026-10-01. **Status:** proposed (the coordinator's brief of
2026-10-01, building the routing spike's recommendation 3 now that D-071's
page exists to consume it; the design was taken without questions and the
rulings below are this record's; the maintainer decides them at review).
**Numbering:** D-073 (the rig, merged while this was built) and D-075 (on
another branch) were taken, so this is D-076. **Spec:** `docs/superpowers/specs/2026-10-02-graphhopper-design.md`.
**Depends on:** D-024 (our own pin where no distribution packages it),
D-037 (a requirement disclosed, nothing fetched to meet it), D-039 (a typed
name the station cannot satisfy refuses), D-049 (a pinned artifact, sized
and licensed), D-057 (regions are station data; derived data by a converter
enum run as the operator), D-063 (BRouter: a pinned Java tool and a
converter building its data from the regions, one build over all of them),
D-066 (`reference serve`, 127.0.0.1 only), D-071 (the map page, its Host
rule, the tether's `/position`). **Amends:** D-057's converter enum, with
`graphhopper-import`; D-063's rejected list, whose "GraphHopper ... is the
candidate once a local tile server gives it a map" is now this; the binary
backend's `executable` format, which may install as a tree.

**Why.** D-071 gave the station a map in any browser; it could show where
things are and not how to get there. The routing spike of 2026-09-29
measured GraphHopper as the best-verified of five engines (Maven Central's
sha256, sha512 and PGP signature), Apache-2.0, Java 17+, with a `hike`
profile that routes on `sac_scale`: Delaware imported in 58.5 s at a peak
of 1.18 GB under `-Xmx2g` into a 78 MB graph, served at 316 MB. It had no
offline consumer then: QMapShack knows only Routino and BRouter, GNOME
Maps' GraphHopper URL is hardcoded online, and GraphHopper's own page loads
only online base maps. The browser map is that consumer.

### Two units

| Unit | What | Where |
|---|---|---|
| `graphhopper` | binary: Maven Central's `graphhopper-web-11.1.jar`, 47,331,301 bytes, sha256 `8462f758…4ea33` (equal to Central's `.sha256` and to GitHub's digest for the release asset; Central's `.sha512` equal too; downloaded once on 2026-10-01), `signature_url` its `.asc` by key ID `11FA9E0B0E2FBADB` (issuer fingerprint `43BFB8BF924D7792531DC63F11FA9E0B0E2FBADB`), recorded and not verified; `format: executable`, `install_tree: true`, marker the jar; `depends: [default-jre-headless]` (`Build-Jdk 17.0.20.1`, class files major 61) | `/usr/local/share/hammunition/graphhopper/` |
| `graphhopper-graph` | derived, converter `graphhopper-import`: `source: osm-regions`, `program: graphhopper`; depends on `osmium-tool` from the archive | `data/graphhopper-graph/` |

Licence Apache-2.0 for the program, ODbL for the graph. No launcher and no
service.

**Ruling: one jar installed as a tree.** `executable` put its file in
`bin/` with mode 0755, and a jar is not a program the shell runs; D-067's
converter `tool` is fetched for a converter only, and `reference serve`
runs this jar too. So an `executable` block with `install_tree: true` and no
`binaries` now stages its file under the marker, mode 0644, and installs it
through `tree_install_commands`, so uninstall, the effect check and
`already_built` need nothing new. The schema requires that marker to be a
plain file name; the backend refuses `binaries` beside it.

**Ruling: one graph over every region, merged first.** D-063's reasoning:
a route must cross from one region into the next, and `import` reads one
file, so two regions or more are merged with `osmium merge`. The steps, as
the operator in `graphhopper.work` under one lock, each checked by its
output: the jar and the regions checked and the directory emptied; the
merge; `config.yml` written by the operator's `sh` and checked against the
digest of the engine's text, then `java -Xmx4000m -jar <jar> import
config.yml`, whose graph must hold `properties` with every file digested;
the files published under temporary names, renamed in, files no longer
built removed, and the record `graph.source` written: all or none. The
record names each region and snapshot, the jar (from the plan, so a
GraphHopper bumped in the same run rebuilds the graph), the profiles and
each file, last `converter: graphhopper-import 1`.

**The profiles are the engine's.** car under contraction hierarchies;
bike, foot and hike under landmarks; GraphHopper's bundled custom models;
the encoded values, `import.osm.ignored_highways: ""` (now mandatory) and
`prepare.min_network_size: 200` from the spike's configuration. GraphHopper
refuses to load a graph whose profiles changed, so the import's and the
server's configurations come from one function
(`src/hammunition/graphhopper.py`), never from the catalog. Elevation is
off: GraphHopper's providers download SRTM or CGIAR online and none reads
the Copernicus GeoTIFFs; recorded as a gap, and the routes are flat.

**Ruling: installed by name, never in a profile.** The graph is 3.7 times
the downloads (78 MB from 22.1 MB is 3.5; rounded up in case the spike's
MB were MiB), the largest factor of any map unit (Navit 0.9, Garmin 0.85,
Routino 0.67, vector tiles 0.91, BRouter 0.2); the import took 1.2 GB of
memory on Delaware and is unmeasured on anything larger; `navigation`
already carries Routino, BRouter and CoMaps' own router; and its one
consumer is the browser page. The plan counts the graph in the staging
directory (with the merged input for two regions or more) and again under
the prefix, says each figure is from one region, and states the memory.
Typed with no regions set, `graphhopper-graph` is refused with the remedy,
as D-039 rules for a typed name; `graphhopper` alone installs.

### Serving

**Ruling: `reference serve`, not a separate `maps router` command.** The
page is served there; one process is one Ctrl-C and one Host rule, and the
route request stays on the page's own origin. A separate command would
leave the page calling another origin, which is the problem below.

With the map holding a region, a graph installed and current for the
installed jar, and `java` on the PATH, `reference serve` takes a free port
on 127.0.0.1 from the system, writes `config.yml` (0600, never through a
link left in its place) in `~/.cache/hammunition/reference/graphhopper/`
with one application connector on that port and `admin_connectors: []`,
refills `graph/` there with one link per installed graph file, and starts
`java -Xmx4000m -jar <jar> server config.yml` as a child with its output in
`graphhopper.log` and `PR_SET_PDEATHSIG`, since GraphHopper has no `-a PID`
as kiwix-serve does. The page asks `GET /map/route` of the reference
server, which applies the Host rule, rebuilds GraphHopper's query from
exactly two points (finite, on the globe) and a profile the graph has, adds
`points_encoded=false`, English instructions and, for every profile but
car, `ch.disable=true`, and relays the answer. 400 for a refused request,
503 while GraphHopper starts or after it exited (naming the exit and the
log), 502 otherwise.

**Ruling: served through links, because GraphHopper locks.** Measured: a
read-only graph refuses to start ("To avoid reading partial data we need
to obtain the read lock but it failed"), because 11.1 creates `gh.lock` in
the graph directory whenever writes are allowed, and `allow_writes` is not
a configuration key (`GraphHopper.setAllowWrites` only, read with `javap`).
A directory of links in the operator's cache, the files themselves root's
and read-only, started, routed and left no lock after SIGTERM. Anything in
that directory the engine did not make (not a link, not the lock) refuses
the router and is left alone.

**Ruling: a router that fails never stops the page.** Its exit is reported
once in the terminal and the Route control says so; the books and the map
keep serving. kiwix-serve's exit still stops the page (D-066).

**Residual exposure, recorded.** GraphHopper 11.1 sends
`Access-Control-Allow-Origin: *` on every answer (`CORSFilter`, hardcoded,
measured) and checks no Host header. While `reference serve` runs, a page
from elsewhere in the operator's browser that found GraphHopper's port
could ask it for routes over the operator's regions, which says where they
are. The port is the system's choice each run and never linked; the page
never calls it. GraphHopper's own `/maps/` is reachable on that port and
loads online base maps; it is never linked, and the guide says so. The
alternatives were a network namespace per serve (more machinery than the
exposure) or patching GraphHopper (a fork, CLAUDE.md's rejected list).

### The page

With a router, the bar gains *Route for* (the record's profiles), *Route*
and *Clear*. *Route* then a click routes from the tether's position when one
has arrived; otherwise two clicks. The line is drawn from GraphHopper's
GeoJSON `LineString` (measured with `points_encoded=false`), both ends
pinned, framed clear of the bar, with the length, time and instructions.
A changed profile routes the same points again. `#route=LAT,LON;LAT,LON;PROFILE`
routes on load. Without a router none of this is in the page.

### What is measured, and what is not

On the development host on 2026-10-01, against the pinned jar and the
archive's OpenJDK 25 and osmium, in scratch: a synthetic 20 by 20 grid of
roads near Montpelier, VT with one `sac_scale=mountain_hiking` path, and a
second region sharing its corner node. GraphHopper alone: import 2.8 s at
198 MB; the read-only refusal above; the links; `admin_connectors: []`
accepted; one listener, 127.0.0.1; `/health`; CORS `*`; `/route` with
`points_encoded=false` giving a `LineString`; non-car profiles refused
without `ch.disable`; `foot` going round the path (4.5 km) and `hike` taking
it (4.1 km). Through the engine: `GraphConverter` over both regions (merge,
import, 16 files and the record installed, 4.6 s, 207 MB, the next plan
current); `plan_router` over the read-only result; GraphHopper started as
the CLI starts it, answering the first `/map/route` through the reference
server after 5.2 s on one 127.0.0.1 listener, car, bike, foot and hike
routes, and a car route crossing into the second region; 244 MB resident;
no lock left after SIGTERM. The page with a route from `#route=` was drawn
by headless Chromium against a loopback stand-in for GraphHopper, every
request on 127.0.0.1, with a kit assembled from the tile spike's extracted
files, because the pinned tilemaker tarball is no longer on disk; the
committed render test for it is skipped without `HAMMUNITION_MAP_KIT_DIR`,
as D-071's is. The jar was then deleted.

**Final review, 2026-10-01.** A GraphHopper that failed to start (its
`Popen` or its log raising) escaped `run()` and was reported as the page's
port, stopping the books and the map; it is now caught, `/map/route` and
the landing page say routes are off and why, and the page keeps serving
(tested). `graphhopper-graph`'s prerequisites said "deferred" where a typed
name is refused; corrected.

**Owed by the bench:** a real region through `hammunition install
graphhopper-graph` (time, memory, scratch); the Route control in a desktop
browser with a real receiver's position; Java on the targets other than
Parrot. **Deferred:** a plan-time check of Java's version against the
floor (the targets' default JREs are 21 or newer by their archives, not
measured here; BRouter has the same gap); elevation for GraphHopper from
the Copernicus tiles.

**Rejected.** A `maps router` command (above). The page calling GraphHopper
directly (its CORS). Serving the graph read-write in the prefix, or copying
it to the cache each start (links cost nothing). A `tool` on the derived
block (the server needs the jar too). Putting the units in `navigation`
(above). GraphHopper's bundled web page (online base maps).
Elevation from GraphHopper's online providers.

**Consequences.** `GRAPHHOPPER_INPUTS`, the `graphhopper-import` converter
and the shared-field refusal in `src/hammunition/manifest/schema.py`; the
single-file tree in `src/hammunition/backends/binary.py`;
`src/hammunition/graphhopper.py`; `src/hammunition/backends/graphhopper.py`;
`src/hammunition/routing_plan.py`; `combined_shortfall`'s graph note in
`src/hammunition/backends/terrain.py`; the install wiring and
`cmd_reference_serve` in `src/hammunition/cli/main.py`; the route handler,
`RouterState` and `die_with_parent` in `src/hammunition/reference.py`; the
route control in `src/hammunition/map_page.py`; `update.rebuild_command`;
`catalog/packages/graphhopper.yaml` and
`catalog/packages/graphhopper-graph.yaml`. The operator's page is
`docs/guides/offline-navigation.md`, section 16, *Routes on the browser
map*; the CLI's is `docs/reference/cli.md`. Tests:
`tests/test_graphhopper_schema.py`, `tests/test_graphhopper.py`,
`tests/test_graphhopper_converter.py`, `tests/test_graphhopper_catalog.py`,
`tests/test_graphhopper_cli.py`, `tests/test_reference_router.py`, and the
route render in `tests/test_map_render.py`.

---

## D-077 — Every run leaves a log: one plain-text file per run under `<state dir>/logs/`, rotated by count and size; the transaction log rotates into archives its readers walk in order

**Date:** 2026-10-02. **Status:** proposed (the maintainer's request that
every run "make a log and even if necessary log rotation", after a
`navigation` dry run printed nothing for twenty-five minutes, #197, fixed by
the progress lines of #199; the maintainer decides the rulings below at
review). **Depends on:** D-004 (the transaction log is what `uninstall`
stands on), D-031 (the artefact, not the exit status), D-043 (owner-aware
directories: a run under sudo logs for the operator), D-059 (`--json` is one
document on stdout, nothing else), D-062 (a long run's structure).

**Problem.** The transaction log records *what was done to the machine*, as
JSON, with no command output, and writes nothing for a dry run. An install can
run for hours with everything it says only in a terminal that closes, scrolls
or is a pipe the operator forgot to tee. Nothing on disk answers "what did that
run just do, and why did it stop?". Separately, `transactions.jsonl` is
append-only and every reader (`status`, `update`, `uninstall`, the deferral
list, the helper-artifact replay) walks the whole file through
`TransactionLog.read()`; on the maintainer's station it holds about 2,800
events after a month and has no bound.

**Ruling 1: a per-run log file.** Every command that changes state or runs
long writes `<state dir>/logs/<UTC timestamp>-<command>-<pid>.log`
(`20261002T181500Z-install-12345.log`): `install`, `uninstall`, `update`,
`menus apply`, `hardware apply|unapply|park|wake`, every `maps ...`,
`reference serve`, `services ...`, `time mode`; `--dry-run` runs too. The
readouts (`status`, `list`, `show`, `doctor`, `hardware list|state`,
`artifacts`, `logs`, `station show`) do not. `station set` does not either:
its argv *is* the station values. The file holds the engine version, the argv
with the value of every station flag (`--callsign`, `--grid-square`,
`--node-alias`, `--map-regions`, `--rig-device`, `--rig-owner`, `--mirror`)
replaced by `<redacted>`, everything the run printed on stdout and stderr
(teed: the terminal sees the same bytes), each command the engine ran with its
output as it arrives and its exit code and duration, and a last `result` line.
Mode 0600, directory 0700, flushed per line so a killed run leaves a usable
log, owner-aware under sudo. A log that cannot be written is said once on
stderr and the run goes on: the log must not fail the run it describes. The
last line of a run on a terminal is `Log: <path>`, on stderr; a `--json` run
prints no such line (its stderr is diagnostics for the document) and its
document is unchanged.

**Ruling 2: rotation.** At the start of each logged run, the oldest logs are
removed until the new one fits under 30 files and 200 MB. A run in progress is
never removed: every run holds an exclusive `flock` on its own file for its
life, which the kernel drops however the process dies, so there is no pid file
to go stale. Files whose names are not ours are not counted or touched. Both
limits are constants in `src/hammunition/runlog.py`, not station config:
station values are the things only the operator can supply (D-035) and a
retention policy is not one.

**Ruling 3: `hammunition logs`.** Lists the logs (date, command, size, result:
`ok`, `failed`, `refused`, `not confirmed`, `running`, or `incomplete` for a
killed run), `--last` prints the newest, `--path` prints its path for
`tail -f`, `--json` prints a `logs` document. `doctor` reports the number and
size of the logs and how the newest ended.

**Ruling 4: the transaction log rotates, and nothing is deleted.** When a
`transaction_begin` is about to be appended and the live file is over 1 MiB,
every whole transaction older than the newest 20 moves into
`transactions-<NNNNNN>-<UTC>.jsonl` beside it, numbered by sequence and not by the clock (D-058: a station's clock can step back); `TransactionLog.read()` yields the
archives in name order and then the live file, which is the same events in the
same order, so every replay is unchanged by construction. A test runs `status`
(text and `--json`) and the two `uninstall` replays before and after a rotation
on a fixture log and compares. Appends take a shared `flock` and a rotation an
exclusive one, so a line is never written to the file a rotation replaces; a
rotation killed between writing the archive and shrinking the file is
recovered by a `.rotating` intent file whose claim is checked against the
file's own first lines, never trusted. An archive that exists and cannot be
read raises: skipping history would report installed units as not installed.
The transaction log is the one record `uninstall` can use, so it is never
pruned; the archives are small and the gain is a live file that stays small
for the appender and for `tail`, not a smaller history.

**Scrubbing.** The saved station's values and any typed on the command line
are replaced by `<redacted>` in every logged line, not only the argv (review:
a data plan prints a region, a rejected callsign is echoed). Exact text, three
characters or more; command output the engine cannot know about is logged as
printed. A run log stops at 50 MB; `services` with no verb and non-dry-run
`--json` runs are not logged.

**Rejected.** A redaction that stops at the argv. Per-run logs as JSON (the transaction log is
that; this is the one an operator reads). Rotating by age (a quiet station
would lose its only record of the last install). Compressing archives (a
reader needing history would have to decompress; they are small). A log
directory setting in station config (a path the operator already controls
with `XDG_STATE_HOME`).

**Not measured.** A run under real sudo handing the file to the operator was
exercised with the same `paths.py` helpers as the transaction log and an
injected `geteuid`, not on a machine; the bench owes one `sudo hammunition
hardware apply --dry-run` and a look at the log's owner.

**Consequences.** `src/hammunition/runlog.py`; the `SubprocessRunner`'s
streaming path in `src/hammunition/backends/base.py` (only while a run log is
active); `TransactionLog.rotate`, `.archives` and the locked append in
`src/hammunition/state/log.py`; `cmd_logs` and the dispatch wrapper in
`src/hammunition/cli/main.py`; `src/hammunition/interface/logs.py`;
`docs/reference/run-logs.md`. Tests: `tests/test_runlog.py`,
`tests/test_transaction_log_rotation.py`; `tests/conftest.py` sends every
test's run logs to a temporary directory.

---

## D-078 — Questionable or personal data is the operator's own to bring: their export or their own key; the project never hosts it; an operator's Bunker may hold it for their LAN, and the on-request repeater lists are mirrorable as `unverified-fetch`

**Date:** 2026-10-03. **Status:** accepted (the maintainer's rulings of
2026-10-03, recorded and implemented on branch `repeater-rulings`; the
implementation is proposed until merged). **Depends on:** D-033 (an
unlicensed source judged on what we do with it), D-064 and D-074 (the
repeater layers), D-070 (the mirror and `artifacts`), D-021 (disclose, never
adjudicate).

**Principle.** For data whose terms are questionable or personal, the operator
brings their own export or their own API key. The project never hosts or
redistributes such files: not in the repository, not in a release, not on a
server of its own. An operator's own Bunker may hold them for their LAN, under
Bunker's `hold_unverified` switch. **No licence letters are sent to data owners
on the project's behalf**: the councils', the RSGB's and Brandmeister's explicit
licences, left to the maintainer in D-074, are not asked for, and the layers
stay what they are, carried under D-033 and marked unverified. Instances: the
ACMA register's `client.csv` (D-074, the ruling of 2026-10-02: a Bunker may hold
the zip, the engine never opens that file) and these rulings of 2026-10-03.

**Ruling B: the on-request lists are listable.** `hammunition artifacts --json`
lists the three on-request repeater lists (`etcc.csv`, `brandmeister.json`,
`hearham.json`) under the unit `repeater-snapshots` (not a catalog unit: nothing
is installed from it; it is listed by default and when named with `--units`),
check `unverified-fetch` in the contract: `digest` null, `url` the publisher's,
`size` from one `HEAD` (null when the server answers with an error or states no
length, deferred when it does not answer at all). The default listing therefore
sends three `HEAD`s, to ukrepeater.net, Brandmeister and hearham, as the ACMA
probe sends one, `licence` the project's position text, never a licence. The
check is size and date only; nothing a mirror serves under that name can be
verified, and everything read from one is marked unverified, as when read from
the publisher.

`fetch-etcc`, `fetch-brandmeister` and `fetch-hearham` read the station's mirror
first, at `<mirror>/repeater-snapshots/<name>` (D-070's shape), and the
publisher on any failure there: unreachable, an error status, too large, or
bytes that do not parse as that list. `--no-mirror` skips it. The layer's
licence line says the snapshot came from the mirror and that its own date is
unknown; the layer is dated the day it was read. `import` takes the operator's
own files and fetches nothing, so it has no mirror to read.

**Ruling C** is the 2026-10-03 amendment to D-074: Canada measured, not carried.

**Rejected.** A snapshot hosted by the project. Writing to data owners (above).
A digest for a snapshot: the lists change under their URLs, and a digest the
Bunker took would be pinned to nobody's publication.

**Not measured.** A real Bunker holding a snapshot and the engine reading it
over the LAN; the tests use loopback servers.

**Consequences.** `snapshots()`, `read_snapshot()` and `SnapshotHead` in
`src/hammunition/repeater_sources.py`; `list_artifacts(snapshot_probe=...)` in
`src/hammunition/artifacts.py`; `unverified-fetch` in `CHECKS`
(`src/hammunition/interface/artifacts.py`); `--no-mirror` on the three fetch
commands in `src/hammunition/cli/main.py`; `docs/guides/lan-mirror.md`,
`docs/guides/offline-navigation.md`, `docs/reference/json-interface.md`.
Tests: `tests/test_artifacts.py`, `tests/test_repeater_sources_cli.py`.

---

## D-079 — File capabilities require typed consent and are reversible

**Date:** 2026-10-03. **Status:** decided. **Depends on:** D-004 (transaction
log and uninstall), D-016 (complete plan before changes), D-031 (verify the
effect rather than trusting an exit code).

**Decision.** A `file_capability` system modification names an installed
`binaries[].install_as` and a non-empty list from the engine's supported Linux
capability vocabulary. The plan always shows the exact binary and grants as an
optional, consent-gated step. Before the ordinary transaction confirmation, the
operator must type `yes` specifically for that grant; `--yes` never answers this
gate. A script may set
`HAMMUNITION_ACCEPT_CAPABILITIES_<UNIT>` to the exact grant string printed in
the plan, never to `1`. A declined or unavailable gate skips only the `setcap`
step and the rest of the install proceeds with the binary unprivileged. After
affirmation, the engine runs `setcap` as root and verifies the result with
`getcap`. The command and outcome use the transaction log's ordinary command
events. `uninstall` clears a capability only when the log attributes its grant
to Hammunition and the corresponding binary is itself an attributed file;
`setcap -r` runs before the binary is removed.

**Evidence.** Upstream LinBPQ runs `sudo setcap` inside its build, which is
neither disclosed nor reliable with a cold sudo ticket (#96). Its default KISS
and web-interface configuration does not need these capabilities. Applying
them unconditionally would grant network privileges that the selected station
does not use. The opt-in keeps the ordinary packet profile unprivileged while
providing a planned, logged and reversible route for Ethernet, tun and
privileged-port configurations. Following D-021 and D-040, the consent gate is
typed and binds scripted acceptance to the exact grant rather than a separate
flag or generic affirmative value.

**Constraints.** Capability targets are engine-installed binaries under the
shared prefix, not arbitrary paths or commands. The manifest declares
`libcap2-bin`; the engine does not guess an apt package name. Only permitted
and effective flags are applied. The plan's existing consent-gate shape carries
the capability step in text and JSON.

**Closes:** #96 route 2, without restoring `sudo setcap` to the build.

**Consequences:** `SystemModification`'s `file_capability`, the install-plan
view, `commands_for`, the log attribution replay and removal plan; LinBPQ
documents its default and opt-in behavior. The package reference and JSON
reference are generated from those declarations.


## D-080 — Reticulum is carried as per-user venvs with one shared instance per machine; the Reticulum License is stated, not gated; the engine writes no Reticulum configuration

**Date:** 2026-10-03. **Status:** accepted (the maintainer's rulings of
2026-10-03 on the design and on its four open points, recorded in
`docs/superpowers/specs/2026-10-03-reticulum-core-design.md`); the
implementation is proposed until merged (branch `reticulum-core`, Track C PR 2,
issue #105). **Depends on:** D-033 (a licence judged on what we do with it),
D-021 (disclose, never adjudicate), D-026 (the means of talking to a device),
D-039 (a member the target lacks is deferred by name), D-073 (user services),
D-078 (the operator's own data stays theirs), D-010 (one update block per
upstream), D-027 and D-018 (a hardware claim is earned).

**The shape.** Three catalog units, `rns`, `lxmf` and `nomadnet`, each a
hash-pinned `venv` from PyPI, because no archive on any of the seven targets
carries any of it (`docs/reference/mesh-inventory.md`, 2026-10-03). `rns`
exposes every console script `rns` 1.5.6 declares (14): `rnsd`, `rnstatus`,
`rnpath`, `rnprobe`, `rnid`, `rncp`, `rnx`, `rnsh`, `rnodeconf`, `rnir`,
`rnpkg`, `rngit`, `rngcs` and `git-remote-rns`; there is **no `rnsh` unit**, because `rns` 1.5.x installs its
own and PyPI's separate `rnsh` would be a second owner of one command name.
`lxmf` exposes `lxmd` and starts nothing: a propagation node stores other
people's messages, and running one is the operator's decision. `nomadnet`
exposes `nomadnet` and ships a terminal launcher named `nomadnet-terminal`
(one name for both would have one overwrite the other in `~/.local/bin`). The
three venvs each carry their own `rns`, so they are pinned to one version and
bumped as a set; `tests/test_reticulum_catalog.py` fails if they are not.

**One shared instance per machine.** The instance is run by the first
operator's service; another account's service attaches to it as a client
(measured in a Debian 13 container, 2026-10-03: a second account's
`hammunition-rnsd` attached to the first account's `@rns/default` with the
"another shared local instance" warning). `rns` carries a `user_services` block,
`hammunition-rnsd` (`{venv}/bin/rnsd --service`), a plain service in D-073's
sense: no station value, nothing to defer. It is **enabled at install** and,
as every plain service does, starts at the operator's next login (the guide
prints `systemctl --user start hammunition-rnsd` for now). Its purpose is that
the first Reticulum program to start owns the interfaces and the rest attach, so
a service that is always first means NomadNet quitting does not take the
network with it. **It is not a TCP port.** Measured 2026-10-03 (Parrot 7.4, and again in a
Debian 13 container, rns 1.5.6, `ss -xl` and `ss -ltn`): `rnsd` binds the abstract Unix sockets `@rns/<instance>` and
`@rns/<instance>/rpc` and no TCP port; upstream's example configuration names
37428 only for platforms without domain sockets. So the unit declares no
`listens`: a declaration would print a loopback TCP listener the plan does not
have. An abstract socket has no file permissions and is machine-wide, so it is
not private to one account; the page and the guide say so, and which of an
operator's interfaces another account could use through it was not measured
(a second account's `rnsd` did attach, in the container run). A
second `rnsd` that finds the instance taken does not exit: it attaches, logs
"connected to another shared local instance, this is probably NOT what you
want!" and keeps running (measured, exit 124 under `timeout`), so there is no exit status to refuse a
restart on and none is set.

**The licence is stated on the plan line and not gated.** `rns` and `lxmf` are
under the Reticulum License, MIT plus two use restrictions (harm to human
beings; AI and machine-learning training data), not OSI-approved. Carried under
D-033's shape, as LinBPQ is: fetched from PyPI at the operator's direction,
never mirrored or vendored, the terms printed before the confirmation and quoted
on the package page, no judgement of the operator's use. A venv block gains
`licence` and `licence_url` (set together, https) and the plan line that
installs the venv carries them. There is **no consent gate**: nothing here
transmits until the operator attaches and configures a radio, and `rnodeconf`,
the RNode flasher, is the means of talking to a device (D-026); the firmware it
fetches is upstream's, what `rnodeconf` verifies was not measured, and the page
says so. `nomadnet`'s wheel classifier says MIT while the licence text it ships
(identical to its repository's, 35,149 bytes) is the GNU GPL v3; **the shipped
text governs** (the maintainer's ruling), so the unit says `GPL-3.0-only` and
its page notes the disagreement. `docs/reference/licence-verification.md` has
the file read and the date for each.

**The engine writes no Reticulum configuration, and uninstall removes none.**
`~/.reticulum/config` is created by `rnsd` on first start with its defaults
(the AutoInterface, which finds peers on a local network by IPv6 multicast and
UDP) under the operator's account. Hammunition never edits it. `hammunition
uninstall` removes the service, the venvs and the wrappers and **leaves
`~/.reticulum`, `~/.nomadnetwork`, `~/.lxmd` and `~/.rnsh`**, which hold the
operator's identities and conversations; the guide says so and shows a backup.

**The `mesh` profile** is post-1.0 and carries `rns`, `lxmf`, `nomadnet`,
`python3-meshtastic` and `gtk-meshtastic-client` (the maintainer's ruling: the
Meshtastic units belong here, and D-039 defers by name where the archive lacks
one, as it does for `python3-meshtastic` on Ubuntu 24.04 and Pop!_OS 24.04).
`docs/SCOPE.md` says a `mesh` profile ships when its units have run against a
node on the bench; that is not yet true, and the profile says it is post-1.0 and
that no LoRa link has been run through it.

**The `rnode` hardware entry** records upstream's board lists, not an
identifier: Reticulum's manual lists fifteen supported boards and
`RNode_Firmware`'s `Boards.h` defines 21 board identifiers, and neither names a
USB vendor or product id. It inherits the `badgelife` class (`dialout`), carries
no identifier and no symlink (D-028), and is `untested`, not `supported`:
`supported` is earned by an identifier of the entry's own (D-018, D-027), and
there is none to confirm.

**Where the design's assumptions met the engine and the measurement.** The
design assumed `{venv}` was substituted in a user service's exec (it was not;
`UserService` accepts `{venv}/...` now and `plan_user_services` fills it from
the operator's own venv, under `sudo` as well); that a venv unit could name its
licence (it could not; the plan printed nothing, and `note` is read by no
plan line); that the shared instance listens on TCP 127.0.0.1:37428 (it does
not); that the `rnode` entry could be `supported` (the catalog's own test and
D-018 refuse it); and that Reticulum's manual lists public entry points (it
recommends against a pasted list and points to `directory.rns.recipes` and
`rmap.world`, so the guide carries the manual's own example and no list).

**Rejected.** A consent gate (nothing transmits at install). A Reticulum
configuration written from station values: the file is the operator's, and the
engine cannot know their interfaces. One unit holding all three programs: each
upstream has its own update block (D-010). A separate `rnsh` unit. A `listens:`
entry for a port that is not bound. Starting the service during install: D-073
starts a plain service at login, and changing that is its own decision. Sideband
(a 293 MB Kivy environment under a non-commercial Creative Commons licence, an
arm64 build from source) and Reticulum MeshChat (an AppImage) are their own
decisions.

**Measured in a Debian 13 container (2026-10-03).** The engine installed the
three units unprivileged; the unit's `ExecStart` ran and `ss` showed the
abstract sockets, no TCP listener, and UDP 29716 (multicast), 29717 and 42671
(link-local); two containers on one bridge saw each other over the
AutoInterface, answered `rnprobe`, exchanged an LXMF message and ran an `rnsh`
command; uninstall left `~/.reticulum`, `~/.lxmd`, `~/.nomadnetwork` and
`~/.rnsh`. **Not measured.** Any LoRa link: no RNode has been run, and the
maintainer's LoRa boards were lost in a flood. A public hub (not run from CI by
policy). The service under a real systemd user manager. NomadNet's text
interface. What an attached client of another account can do. The last section
of `docs/guides/mesh-and-reticulum.md` has the detail.

### Amendment, 2026-10-05 — `meshtasticd` joins the `mesh` profile from the Meshtastic project's repositories

**Issue #308, Track C part 2.** `meshtasticd` is in no distribution archive;
the Meshtastic project publishes it in the openSUSE Build Service (a flat
repository per Debian release, no Ubuntu) and in Launchpad PPAs (Ubuntu), on
channels `beta`, `alpha` and `daily` (there is no `stable`). The unit carries
the `beta` channel, which is upstream's own name for its recommended channel. It
is a D-040 case with the archive offering nothing on any target, declared as
five repositories, one per kind of target (`when:`), each pinning the key
fingerprint read from the published key on 2026-10-05 with the engine's own
`openpgp.py` and `gpg --show-keys`: OBS `426AA6B0285C2096B70D9FC2528423A469A77D9A`
(Debian 13, Parrot 7 from `Debian_13`; Kali from `Debian_Testing`, whose build
needs glibc 2.43 and does not install on Parrot) and PPA
`5E0A0F83F3DDE7AC55915B14F40C93FFA2CD17E3` (Ubuntu 24.04 and Mint 22.3 from
`noble`, Ubuntu 26.04 from `resolute`). The OBS key is the whole `network:`
project's, not Meshtastic's, and **expires 2027-08-26**; the manifest says both.
Q-023 asks whether the unit wants a consent gate beyond the repository's and
whether the profile should be split.

**It is disclosed, not gated, beyond the repository.** Nothing transmits at
install: the shipped `config.d` is empty, the region is unset and `Module:
auto` finds no radio, and the daemon then exits within seconds (measured). The
D-040 gate on the repository is the typed fingerprint. The package's boot
service, its udev rule with a world-writable line for USB `1a86:5512`, its
system user and groups, and the API it serves on TCP 4403 (all interfaces, no
login, measured under `--sim`) are printed in the plan as notes and recorded in
the manifest's `system_modifications` and `service_endpoints` (D-040's
2026-10-05 amendment gives the three disclosure kinds).

**Measured 2026-10-05** in rootless Podman containers of the harness's own
target images, with the engine installing through the typed fingerprint: Debian
13, Parrot, Kali, Ubuntu 24.04 and 26.04 and Linux Mint 22.3 (the pull request
has the per-target table and what each run covered). **Not measured:** any
radio, any systemd boot of the unit, arm64 and armhf installs, and what the
CH341 udev rule does to a plugged-in module.

**Consequences.** `catalog/packages/{rns,lxmf,nomadnet}.yaml`,
`catalog/hardware/devices/rnode.yaml`, `catalog/profiles/mesh.yaml`;
`UserService`'s `{venv}` and `VenvInstall.licence` in
`src/hammunition/manifest/schema.py`, `service_venv_dir` and the `venv_dir`
argument in `src/hammunition/userservice.py`, the call in
`src/hammunition/plan.py`, the licence clause in
`src/hammunition/backends/venv.py`; `docs/guides/mesh-and-reticulum.md`,
`docs/hardware/rnode.md` (generated), four entries in
`docs/troubleshooting/running.md`. Tests: `tests/test_reticulum_catalog.py`,
`tests/test_user_services_venv.py`, `tests/test_venv_licence.py`,
`tests/test_rnode_catalog.py`, `tests/test_mesh_profile.py`,
`tests/test_reticulum_docs.py`.

---

## D-081 — Operator secrets come from an environment variable or Doppler, through one helper, never the repo, station config, argv or logs; RepeaterBook's API is fetched only with the operator's own key, personal use, never mirrored or listed for a Bunker

**Date:** 2026-10-04. **Status:** accepted (the maintainer's rulings of
2026-10-04, implemented on branch `repeaterbook-api`; the implementation is
proposed until merged, and the RepeaterBook half is **built against the
documented format and not yet run against the live API**). **Depends on:**
D-078 (bring your own export or key), D-064 and D-074 (the repeater layers),
D-070 (the mirror), D-077 (run logs), D-021 (disclose, never adjudicate).

**The helper.** `resolve_secret(name, *, env, station)` in
`src/hammunition/secrets.py`, the one way any keyed download gets its key:
the environment variable `name` when set and not empty; otherwise, when the
station config carries `secrets_doppler_project` and `secrets_doppler_config`
(`station set --doppler-project P --doppler-config C`, cleared by
`--clear-doppler`; names only, validated as slugs that cannot read as
options), exactly `doppler secrets get NAME --plain --project P --config C`
with a fixed argv and its stripped stdout; otherwise `SecretUnavailable`,
which says both ways. A missing `doppler` or a non-zero exit is named, quoting
only stderr's first line, never stdout. The value is printed nowhere; the run
log redacts it by value (`RunLog.add_scrub`) and redacts every `Authorization`,
`X-RB-App-Token` and `Proxy-Authorization` header line whatever its value.

**RepeaterBook.** `maps repeaters fetch-repeaterbook --state CODE
[--country NAME]` fetches `api/export.php` (`country`, `state_id`). The first
draft had the engine speak HTTP to RepeaterBook under an application of
Hammunition's own; **the maintainer will not apply for one** (it would attach
his identity to it), so the engine instead drives the unofficial `repeaterbook`
0.13.0 client (PyPI, MIT, Micael Jarniac, `MicaelJarniac/repeaterbook`), which
RepeaterBook approved as "RepeaterBook Python Client", **App #114**: each
operator generates a token for App #114 on their own account at
`/user/api_apps.php`, and the client sends it with its own User-Agent, which
RepeaterBook matches literally (`403 ua_mismatch`) and which is therefore left
untouched. The client is the catalog unit `repeaterbook-client`
(`method: venv`, the closure of 23 packages and 983 hashes resolved with `uv pip
compile --generate-hashes` on 2026-10-04 and recorded in
`docs/reference/repeaterbook-client-closure.txt`; about 76 MB; MIT; installed by
name, in no profile; nothing exposed, because its two console scripts are an
MCP server that needs an extra the closure omits and a schema writer). It is
**not a dependency of the engine**: its aiohttp, pydantic and sqlmodel closure is
not something the engine carries. The verb requires the unit installed (refused
by name otherwise), resolves the token through
`resolve_secret("REPEATERBOOK")`, the client's own variable name, and runs
`<venv>/bin/python -I src/hammunition/repeaterbook_runner.py --country C
--state-id N` (`-I` because a `secrets.py` beside the script would shadow the
standard library's). The runner uses the client's API (`RepeaterBookAPI.urls_export`
and `export_multi_json`), prints one JSON document, and the engine parses it
into the layer. The token is in that subprocess's environment only: never argv,
never the run log (the resolved value, any credential header and any
`REPEATERBOOK=` assignment are redacted), never a document. The client caches
responses on disk; the runner gives it a private temporary directory and a zero
cache age and removes the directory on exit. One request per state with a pause of
our own; a `401`, `403` or `429` stops the run and is never retried.
What RepeaterBook's wiki said when read on 2026-10-04: since 2026-03-03 access
needs an approved application and a per-user `rbuapp_` token (header
`X-RB-App-Token`, preferred; `Authorization: Bearer` second choice); limits are
unpublished and `429` means back off at once; `exportROW.php` (`country`,
`region`, no `state_id`) is documented and **not carried**, the verb being per
state. The client's source (read the same day) gives what the wiki does not
print: the export is `{"count", "results"}`, the row keys the parser reads,
`state_id` as the zero-padded US FIPS code (`"06"`) and `CA##`/`MX##` for Canada
and Mexico, `Retry-After` on a `429`, an answer cut near 3,500 rows. **Policy
summary (2026-10-04):** "Personal/internal use limits data to approved
purposes"; a public application must credit RepeaterBook with a link; approval
is "less likely" for a public repeater search page, map or directory, a
nearby-repeater finder as a standalone feature, republishing for others to
browse, a shadow site or mirror, an app that "caches and re-serves repeater data
to multiple users", a "redistributable offline database", or one that uses
RepeaterBook "as just one data source inside a broader mapping, preparedness, or
discovery platform". So **the `repeaterbook` layer is never mirrored and never
listed by `artifacts`** (not under `repeater-snapshots`; a Bunker must not hold
it, even on a LAN), is written 0600 under the operator's own overlays, and every
rendering carries RepeaterBook's name with a link: the GPX description and each
waypoint's `<src>`, and the POI collection's comment. Navit's textfile has no
place for a title or a link, so that file is named for RepeaterBook and the link
is in the others; the browser map draws no repeater layer.

**The layer.** `repeaterbook`, titled `Repeaters (RepeaterBook, personal use,
YYYY-MM-DD, unverified)`; a second state merges into it (D-074's merge, new
rows first). Rows off the air, without a position, a callsign or a frequency
are skipped and counted. Its terms print before the fetch and are recorded in
the layer.

**Not established.** That the client behaves against the live API as its
source reads (**built against the documentation and the source; not yet run
against the live API**, no token existed on 2026-10-04: the tests use a
hand-written fixture and a fake client); RepeaterBook's rate limit; the
3,500-row cut (from the client). The wiki prints no JSON key names and no
`state_id` table; both come from the client. A key rename
fails with a named error and writes nothing. No request has been made: the
tests serve a hand-written fixture from loopback.

**Rejected.** A key in the station file, the repository or argv. The
`repeaterbook` package as a dependency of the engine. An application of
Hammunition's own (the maintainer's identity). Changing the client's User-Agent.
A Bunker, a mirror or `artifacts` holding or listing the layer. `exportROW.php`.

**Consequences.** `src/hammunition/secrets.py`, `src/hammunition/repeaterbook.py`,
`src/hammunition/repeaterbook_runner.py`, `catalog/packages/repeaterbook-client.yaml`,
`docs/reference/repeaterbook-client-closure.txt`,
`Station.secrets_doppler_project`/`_config`, `RunLog.add_scrub`,
`docs/guides/offline-navigation.md` §13, `docs/guides/station-settings.md`,
`docs/reference/cli.md`, `docs/reference/json-interface.md`. Tests:
`tests/test_secrets.py`, `tests/test_repeaterbook.py`,
`tests/test_repeaterbook_runner.py`, `tests/test_repeaterbook_client_catalog.py`.

---

## D-082 — The area of operations: what is loaded is kept per area, and `active_areas` says which areas QMapShack, Navit, the browser map and the JSON documents show; deactivating deletes nothing

**Date:** 2026-10-04. **Status:** accepted (the maintainer's principle of
2026-10-04, epic #326; this is its switch, issue #328, implemented on branch
`maps-activate`; **built and tested against synthetic layers, never run in a
QMapShack, Navit or browser, and not yet used on the field laptop**).
**Depends on:** D-064 and D-074 (the repeater layers), #325 (one layer per
state), D-075 (the infrastructure layers), D-057 and D-071 (the region maps and
the browser map), D-059 (one document per command), D-035 (a missing value
defers).

**The principle** (maintainer, 2026-10-04). "People will want to load data as
much as possible before an emergency, then be able to select the data they
need, because there is so much. You are in Ohio one day, Michigan the next, then
fly down to Florida." An EMCOMM operator with ARES, RACES or a FEMA task force
prepares the laptop at home with every state or region on the possible roster,
and on arrival one action makes *that* area the active one, without deleting
anything, and the next day another. Until this decision the engine conflated
the two: what was downloaded was what was drawn.

**The value.** `active_areas` in the station file, a list of *areas*: a US state
code (`OH`; a RepeaterBook `state_id` such as `CA01` elsewhere) or a map region
name (`north-america/us/ohio`, or its last word, `ohio`). **Unset, the default,
means everything loaded is active**, which is what the engine did before, so
nothing changes for anyone until they use it. An **empty list means none**, and
is kept apart from unset, because `--none` and "never used this" are different
(`active_areas: []` in the file). `station set --active-areas OH MI <region>`,
`--clear-active-areas`. A code or region that is not loaded is **accepted with a
note**, since the operator may fetch it next (D-035: a missing thing defers, it
does not refuse). `station show` prints a count, as it does for map regions: the
areas say where the operator may be sent.

**One ground, two names.** `OH` and `north-america/us/ohio` are the same ground,
tied through the postal-code table the RepeaterBook fetch already carries, so
`maps activate OH` draws Ohio's repeaters *and* Ohio's map. Nothing else is
inferred: a region that merely contains a state (a Geofabrik `midwest`) is
activated by its own name, and `west-virginia` is not `virginia`.

**The switch.** `hammunition maps activate CODE|REGION ... | --all | --none
[--dry-run] [--json]` writes the value and re-registers:

- **QMapShack** reads a *directory* of `.poi` files, so a subset is a directory
  of symbolic links, `overlays/active-poi`, to the active areas' files. `[Canvas]
  poiPaths` names that directory instead of the layer directories while a subset
  is active, through the same writer `maps qmapshack` uses, so the launcher and
  every import keep it in step. Everything active is the layer directories
  again, and the links are removed. The links are derived state; only links are
  ever removed, and never the files they point at.
- **Navit**'s own copy of its configuration (the one `maps navit` opens) lists
  the active areas' textfile layers and only the active regions' converted
  maps; root's generated file is untouched.
- **The browser map** (`reference serve`) lists and serves only the active
  regions' vector tiles and layers, read when it starts.
- **The documents** carry `active: bool` per layer in `maps repeaters list
  --json` and the infrastructure import documents.
- **`maps areas [--json]`** lists every area with files on disk, its layers,
  sizes as the engine measures them, dates and whether it is active, and the
  layers that belong to no area: the console's and Hammunition Hill's source of
  truth.

**Layers that belong to no area stay registered always**: the operator's own
import, ACMA, the OpenStreetMap and other source layers of D-074, and every
infrastructure theme today. They are not Ohio's or Michigan's, so no area can
switch them off; the guides say so.

**Infrastructure per region.** Each infrastructure theme was one file across
every region (D-075), so none was an area's. Issue #327 split them per region
(D-075's 2026-10-04 amendment): `infra.layer_area()` names the region of a
per-region layer, and the same rule, in `hammunition.areas`, takes only the
active regions' files into QMapShack, Navit and the browser map. A merged layer
(from before the split, or `--merged`) has no area and is always active.

**Rejected.** Deleting on deactivation (the principle is the opposite).
Moving files between directories (breaks the layer commands and a Bunker's
copy). One QMapShack project per area (the operator would manage them by hand).
Inferring a state from a region by geometry (an unmeasured guess; the name table
is exact). Treating an empty list as unset.

**Not established.** That QMapShack lists a `.poi` through a symbolic link in a
`poiPaths` directory as it does a plain file (every test here reads the
configuration, not QMapShack; the first bench run settles it, and the route if it
does not is copying the active files, 0600, into that directory); that a running
QMapShack, which writes its list back when it exits, keeps the list this wrote.

**Consequences.** `src/hammunition/areas.py`,
`src/hammunition/interface/areas.py`, `Station.active_areas`,
`navit_config.select_regions`, `map_page.find_map(active=...)`, a fix to
`qmapshack_config.ensure_paths` (a path swapped for another under one key),
`docs/guides/offline-navigation.md` section 19, `docs/guides/emcomm-field.md`,
`docs/reference/cli.md`, `docs/reference/json-interface.md`. Tests:
`tests/test_areas.py`, `tests/test_qmapshack_config.py`. Siblings: #327 (per-region
infrastructure), #329, hammunition-hill#87 (the area selector).

## D-083 — Review policy under one maintainer: a pull request is required on `main` with zero approvals, a CODEOWNERS file names the maintainer, administrators are not enforced, and the OpenSSF Scorecard's Code-Review score stays 0 by record rather than by trick

**Date:** 2026-10-05. **Status:** accepted (maintainer's ruling, issue #333, the
day the suite moved to the Renegade-Penguin organization, #357).
**Depends on:** the git workflow of 2026-09-02 (feature branches, pull requests,
the maintainer merges), D-025 (a claim is re-verified when it becomes decisive).

**The question.** The weekly OpenSSF Scorecard run leaves two policy findings
open on every suite repository: *Branch-Protection* wants approving reviewers
and a code-owner review, and *Code-Review* scores 0 because none of the last
twelve merged changesets carried an approval. Both are true. The project has
one maintainer, and GitHub does not let an author approve their own pull
request.

**The options, as put to the maintainer.** (A) keep zero required approvals,
add a `CODEOWNERS` file, record the state; (B) require one approval and merge
every pull request with an administrator bypass, which raises the
Branch-Protection tier while Code-Review stays 0 and every merge becomes a
recorded bypass; (C) require one approval once a second human reviews, which is
not a choice available today; (D) a second account or an App approving, which
is gaming the metric and was not offered as a serious option.

**The ruling.** **A.** A pull request is required on `main` with
`required_approving_review_count: 0` and stale reviews dismissed; force-push
and deletion are blocked; `.github/CODEOWNERS` names the maintainer for every
path; `enforce_admins` stays **off**, because the maintainer is the only member
and chose to keep the administrator path open for now. The Code-Review score is
0 and the record says why; the two Scorecard alerts are dismissed as *won't
fix* with this decision as the reason, and the auto-filed issues close against
#333. **Revisit** when the organization gains a second member: C becomes
available, and `enforce_admins` is reconsidered at the same time. The OpenSSF
best-practices badge is pursued separately (the registration is the
maintainer's login; nothing in it is untrue of the repository).

**Why not B.** It buys a tier with a lie in it: the number says reviewers are
required, the merge log says every one was bypassed. The project's rule is that
a check nobody trusts is worse than none.

**Measured.** Protection on all five suite repositories on 2026-10-05 after the
move: pull request required, approvals 0, dismiss stale on, admins not
enforced, every required check kept (the fuzz gate and, on Hammunition, the
seven distro jobs, the docs links, repo hygiene and the site build). The
2026-10-04 protection script had set `required_pull_request_reviews` to null
and dropped the pull-request requirement for a day; both scripts now carry it.
## D-084 — Targets are releases their distribution still supports in full; the uConsole image is the one exception to "no custom ISO", and only if Debian 13 cannot be documented onto the device

**Date:** 2026-10-05. **Status:** accepted (the maintainer's ruling of
2026-10-05; the image itself is **proposed, not designed**, epic #372).
**Depends on:** D-002 (ARM is a day-one target), D-023 (the licence split),
D-038 (resolve from the release the machine installs from), D-039 (a member a
target lacks is deferred), D-041 (kernel subsystems are measured, never built),
Q-003 (`debian-13-arm64`, Pi untested until verified on uConsole hardware).
**Overrides:** the "no distribution, custom ISO or derivative" line of the
rejected list, for one device, under the conditions below. Everything else on
that list stands.

**Two rulings, one reason.**

### 1. Supported targets

Hammunition builds against operating systems in **regular support**: the
release's own security team still publishes advisories for the whole archive.
The targets are Parrot 7, Debian 13 (amd64 and arm64), Ubuntu 24.04 and 26.04
(the LTS line; interim Ubuntu releases are not added), Linux Mint 22.3 on
Ubuntu's LTS, and Kali rolling. A release **leaves** the matrix when its
distribution ends regular support, and it **never enters** it under long-term
support alone: LTS is a volunteer subset of the archive, not the archive, and a
catalog resolved against it would claim coverage the security team no longer
gives.

**Debian 12 is not planned.** Regular support ended in 2026 and LTS ends in
2028; the one test target that ever named it (hammunition-bunker's `debian:12`
container row) is that project's to decide and is not a declared Hammunition
target. **Rolling releases are carried as best effort** ("your mileage may
vary"): Kali's archive moves under the catalog between releases, so a Kali
failure is reported and fixed when it can be, and never holds a release. The
preference, where there is a choice, is the LTS or stable release of a
distribution, because that is what an operator installs on a machine meant to
work for years.

### 2. The uConsole image

The ClockworkPi uConsole with the Hacker Gadgets AIO v2 is owned, is Skid
Finder's target hardware (the foxhunt side), and is the one machine in the
hardware list that ships from its vendor on an image Hammunition may not be
able to build against: what the current official images are based on, per
core, is **unmeasured** and is the first thing #372 measures. Ruling 1 says
Hammunition does not meet the device on a Debian 12 derivative. Two routes, in
order:

- **Route 1, preferred: document.** A page under `docs/hardware/uconsole.md`
  that takes a stock CM4 uConsole to Debian 13 (Raspberry Pi OS trixie, or
  Debian arm64 with ClockworkPi's kernel and device tree, whichever measures
  as workable) and then runs `hammunition install uconsole`. If every step
  can be written and reproduced from the page, **no image is built**.
  **The first candidate to measure is
  [crossplatformdev/uConsole-Image-Builder](https://github.com/crossplatformdev/uConsole-Image-Builder)**
  (named by the maintainer 2026-10-05; read, not run). As read on that date:
  GPL-3.0, head 2026-02-27, releases of Debian 13 trixie images for the CM4
  and CM5 dated 2026-01-14 (plus bookworm and Ubuntu 22.04, which ruling 1
  excludes), built with Raspberry Pi's own `rpi-image-gen`; the kernel is
  ClockworkPi's prebuilt package from `github.com/clockworkpi/apt`, with an
  optional build mode of `rpi-6.12.y` plus ak-rex's uConsole commits. If a
  stock trixie image from it boots the CM4 with the AIO v2 enumerating, route
  1 is "flash that image, then `hammunition install uconsole`", and the
  project builds nothing. Three things the page would have to say about it:
  the ClockworkPi archive key is fetched from a raw GitHub URL without a
  pinned fingerprint (D-040 pins one; the page gives it); the images ship a
  default `uconsole`/`uconsole` account with passwordless sudo and SSH on,
  which the operator changes before the radio goes on the air; and a kernel
  with out-of-tree commits is still not ours to maintain, only to pin and
  measure.
- **Route 2, only if route 1 fails: build.** An image, in its own repository
  with its own catalog unit like every separable component, built by a
  reproducible script with every input pinned by sha256, signed and verified
  like any other artifact. Debian 13 arm64 base; the image **is Debian 13 with
  the engine run once**: the `uconsole` profile installed through
  `hammunition install`, transaction log and all, never a parallel install
  path. The AIO v2 setup is **optional**, because not every uConsole has the
  board.

**What the exception does not relax.** No custom kernel: the image pins a
packaged or published ClockworkPi kernel by commit and measures it, and never
maintains one (D-041's rule for subsystems applies to the whole kernel). No
mirror of upstream packages, no fork. An installation medium for one device,
never a general distribution, never offered for the laptop targets. The CM4
core only; A06 and R01 are not targets until someone owns one.

**The Skid Finder overlap is accepted, with one owner per layer.** Hardware
bring-up for the AIO v2 (udev, power, display, audio) is written once, in
Hammunition's hardware catalog (`catalog/hardware/devices/uconsole.yaml`,
which today says "not characterised"), and Skid Finder stays the catalog unit
it already is (`catalog/packages/skid-finder.yaml`). The image carries both;
neither re-implements the other's half.

**Consequences.** `docs/SCOPE.md` gains stage 15 and a targets paragraph;
`CLAUDE.md`'s rejected list names the exception; the README's targets
paragraph states the policy in one sentence; `docs/hardware/uconsole.md`
points at the epic. The `uconsole` profile named in SCOPE since D-003 is
defined as part of #372. Nothing in the engine changes under this decision.

## D-085 — A signed Bunker catalogue permits planning with publishers unreachable

A Bunker's signed catalogue lets the engine plan and install with publishers unreachable;
a signature records what the Bunker saw and never upgrades trust; hardware
keys are recommended, never required.

Amends D-070: planning no longer requires publishers when a Bunker is enrolled.
Amends D-078: `share` decides what a group Bunker hands to whom.

The engine verifies the exact catalogue bytes with enrolled OpenSSH keys, remembers
the highest accepted serial, refuses rollback until `hammunition mirror accept-older`
is explicitly confirmed, and warns on catalogues older than 30 days. Repository
pins remain authoritative; publisher digests are the Bunker's recorded observations;
digest-less data retains its existing disclosure and consent. RSA of 2048 bits or
fewer is accepted with the shared weak-key warning. Hardware-only verification is an
operator policy, off by default. Non-sk hardware origin requires operator affirmation.

Hammunition writes no PAM configuration when installing `security-keys`; archive package side effects are unmeasured across the target matrix. Enabling token login or sudo requires
a separate reviewed step with typed consent, dry-run and a working fallback login
check before anything is written; that step is outside phase 1.
