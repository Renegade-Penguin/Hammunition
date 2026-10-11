<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
SPDX-License-Identifier: GPL-3.0-or-later
-->

# When an install fails

Every failure the engine reports names what it was doing and stops there —
resolution finishes before installation, so a failure is a report, not a
half-installed machine. What you do next depends on which of these it is.

## <a name="slow-plan"></a>The dry run seems to hang

`hammunition install navigation --dry-run` can take minutes before it prints a
line. It has not hung: the plan checks every terrain tile, map sheet, book and
map it would download against the publisher, one request each, because each
answer (size, checksum) is part of what the plan tells you. On a terminal it
says so on stderr (`checking 412 terrain tiles … (needs the network)…`) with a
count. If you see nothing at all, stderr is not a terminal; run it with
`HAMMUNITION_PROGRESS=1` in front, or in a terminal rather than through a pipe
or a log. A repeat dry run is fast now: an item already installed that the transaction
log attributes to the engine is not asked about again for seven days (the plan
says `N installed data item(s) were not re-checked`, and `--json` lists each in
`publisher_checks`), and only a first install or an older attribution costs the
requests. `hammunition install … --recheck` asks every publisher regardless, for
when you suspect one re-issued a file. With the network down
a request that cannot connect is retried, then the host is given up on after
three such failures in a row and each remaining request is asked once (a
publisher answering 503 for everything is treated the same); a blackholed
connection still waits out its own 30 s timeout, four at a time, so a few
hundred tiles can take a long while to refuse. Interrupt with Ctrl-C, fix the
connection and run again.

## <a name="publisher-not-answering"></a>The plan said a publisher is not answering

```text
Will NOT happen (the rest of the transaction still will):
  usgs-ustopo: will not fetch 3 item(s) this run: <sheet>, <sheet>, <region> (its outline)
      why: the publisher is not answering right now (prd-tnm.s3.amazonaws.com answered
      HTTP 503); 3 attempts each, then given up
```

Nothing is wrong with your station or the catalog. A publisher (a Geofabrik
outline, the USGS bucket, Kiwix, a CDN) answered a 5xx, dropped the connection
or timed out three times running for those items, and the plan left them out
rather than refuse everything else. Run the same command again in a few
minutes: the items are asked again and, once the publisher is back, planned as
usual. Anything of theirs already on disk was kept; when the deferred item is a
Kiwix book or a CoMaps map, the unit removes no unlisted file in that run, so a
book you deselected goes at the next complete one. A certificate that does not
verify is never treated as an outage: it refuses by name.

- `hammunition install <unit>` (the unit named in the line) asks again for just
  that unit and, if the publisher still does not answer, refuses with its last
  answer instead of deferring.
- `hammunition status` keeps listing the deferral for as long as that was your
  latest transaction.
- An **HTTP 404** on a sheet is a different message and a different fix: the
  carried index is stale, and the line names `scripts/gen_ustopo_index.py
  --fetch`. That is not an outage and is never deferred.
- Retry lines on stderr (`retrying (attempt 2 of 3)`) show only on a terminal,
  or with `HAMMUNITION_PROGRESS=1` in front.

## <a name="dead-url"></a>A source build fails to fetch — HTTP 404

```
Failed: [fetch] https://…/foo-1.2.3.tar.gz … returned HTTP 404 (Not Found)
```

A pinned upstream URL moved or the project stopped publishing that artifact.
This is a catalog bug, not your machine — the manifest's URL needs updating.
[Open an issue](https://github.com/Renegade-Penguin/Hammunition/issues) with the package name, or if you
maintain a checkout, run the sweep that catches these:

```sh
scripts/check_artifact_urls.py
```

It knocks on every pinned URL in the catalog and reports the dead ones,
keeping them apart from hosts that merely flaked today.

## <a name="git-editor"></a>A git build stops in a text editor

```
  $ git -C …/build/comaps-…/src tag -f v2026.08.31-14 FETCH_HEAD
  GNU nano …   .git/TAG_EDITMSG
## Write a message for tag:
```

Your own git configuration turned a plain tag into a signed one. With
`tag.gpgsign true` (or `tag.forceSignAnnotated true`) in `~/.gitconfig`,
`git tag NAME` creates an annotated tag, which needs a message, so git opens
your editor and the install waits in it. Nothing is wrong with the build.

Engines from v0.20.0 on run every git step with the operator's signing
settings turned off for that one command and with no editor and no terminal
prompt, so this cannot recur. On an older engine, type any word, save and
leave the editor (`Ctrl+O`, `Enter`, `Ctrl+X` in nano); if signing then fails
for want of a key, the step fails and a rerun of the same command resumes
from the cache once the engine is updated. Your git configuration is never
changed.

## <a name="parrot-backports"></a>apt refuses with "held broken packages" on Parrot

```
E: Unable to correct problems, you have held broken packages.
   … libcurl4t64 … is selected as a downgrade …
```

Seen on **Parrot Security** with backports enabled: its backports stream ships
updated runtime libraries (libcurl, GTK, SDL2, some Qt6), but the *base*
`-dev` packages a source build needs conflict with them. The remedy is to take
the development packages from backports too:

```sh
sudo apt-get install -t echo-backports <the -dev packages the plan named>
```

The engine's failure text lists exactly which packages apt could not reconcile
— those are the ones to pull from backports. This is a distribution-state
issue, not a catalog one; Debian and Kali do not show it.

## <a name="venv"></a>`python3 -m venv` fails with ensurepip

```
The virtual environment was not created successfully because ensurepip is
not available.
```

A **Debian netinst** ships no `python3-venv`, which the engine's source and
hybrid backends need. One command:

```sh
sudo apt install python3-venv
```

Parrot and Kali ship it. A future `.deb` install of Hammunition will carry its
own virtualenv and remove this step entirely.

## <a name="deb-conflict"></a>A vendor .deb is refused for a file collision

```
wsjtx-improved: its vendor .deb collides with installed distribution
package(s): wsjtx-data
   → remove them first (sudo apt-get remove wsjtx-data) …
```

This is **the engine protecting you**, not failing. `wsjtx-improved`'s vendor
`.deb` ships a file that the distribution's `wsjtx-data` (a `jtdx` dependency)
also owns, with no `Replaces` header, so installing it would leave dpkg's
database inconsistent. The engine refuses at plan time and names the remedy —
remove the conflicting package first if you want the improved build, or keep
what you have. It never removes a distribution package silently (D-022).

## <a name="refused"></a>A package is "refused by name" for a backend or repo

```
code: requires third-party apt repositories (microsoft-vscode) that this
engine cannot add yet
```

Also not a failure. The engine will not pretend to support something it cannot
actually do — adding a third-party apt repository with a pinned signing key is
a disclosed modification it does not yet implement, so it refuses by name and
tells you to install that one package by hand. A capability matrix that
reported coverage the engine does not have would be the lie this rule exists to
prevent. The rest of your transaction is unaffected; install the named package
yourself, or choose one that needs no third-party repo.

## Offline Bunker (`--offline`, `mirror enrol`, D-085)

Twelve things a signed Bunker catalogue can refuse on, each with its own
remedy. None of these disable verification, and `hammunition mirror
accept-older` is never the unconditional answer to one of them — read the
message's own reason first. A payload that is simply **not on Bunker** at
all (never mirrored, or mirrored but not yet there) falls back to the
publisher without complaint online; the same gap is refused by name,
"has no Bunker route yet", only under `--offline`.

### <a name="not-enrolled"></a>"no Bunker enrolled"

```text
no Bunker enrolled; hammunition mirror enrol URL
```

`--offline` (or an exhausted publisher retry with no mirror enrolled, below)
needs a Bunker whose keys you have accepted. Setting `station set --mirror
URL` alone is **not** enrolment — it only gives the D-070 download route.
Run the command the message names:

```sh
hammunition mirror enrol http://bunker.lan:8080/
```

type the fingerprint you trust, and try again.

### <a name="unsupported-version"></a>"unsupported Bunker catalogue version"

```text
version: unsupported Bunker catalogue version 4; this reader accepts 3
```

The Bunker is running a newer catalogue format than this engine understands.
This is not Bunker holding bad data — it is a version this engine has never
been taught to read. Update the engine to a release that names the newer
version, or point the Bunker at an older release until you do.

### <a name="bad-signature"></a>"no enrolled signature verified"

```text
no enrolled signature verified
```

Every key you enrolled failed to verify the catalogue's exact bytes. This is
refused, not patched around: do not remove the signature check to get past
it. Causes, in order of likelihood: the Bunker's signing key changed (its
operator re-enrolled or rotated keys) and you have not re-enrolled;
something on the LAN is answering in the Bunker's place; or the catalogue
was corrupted in transit. Confirm with the Bunker's own operator what
signed it, then `hammunition mirror enrol URL` again with the current
fingerprint.

### <a name="url-changed"></a>"station mirror differs from enrolled mirror"

```text
station mirror differs from enrolled mirror; run hammunition mirror enrol URL
```

`station set --mirror` was pointed at a different address than the one you
enrolled keys for. The engine will not silently carry trust for one Bunker
over to a URL it never verified. Enrol the new address, or set the mirror
back to the one you already enrolled.

### <a name="rollback"></a>"mirror serial cannot be lowered"

```text
mirror serial cannot be lowered; hammunition mirror accept-older
```

The catalogue you just fetched has a lower serial than one you have already
accepted from this Bunker — normally a sign the Bunker was restored from an
older backup, or that something is replaying a stale copy. If you *are*
restoring from a trusted backup on purpose:

```sh
hammunition mirror accept-older
```

shows the old and new serial side by side and asks a typed `yes`. Do not run
this because the message is inconvenient; run it because you know why the
serial went backward.

### <a name="stale"></a>A catalogue older than 30 days

```text
WARNING: Bunker catalogue is older than 30 days
```

Not a refusal — the plan or install proceeds. It means the Bunker has not
synced with its publishers in over a month, so its recorded metadata (an
unpinned region's dated file, an unpinned tile's size) may be behind what
the publisher now has. Sha256-pinned bytes are unaffected; this only touches
what the Bunker *observed*. Check the Bunker's own schedule if you did not
expect this.

### <a name="sharing-filter"></a>An entry is missing from a group Bunker

A group Bunker answers 404 for bytes tagged to another operator's enrolment
id: `X-Hammunition-Enrolment: <id>` is **a sharing filter, not access
control** (**D-085**) — it travels in clear text over HTTP, and anything on
the same LAN sending a different id would see different data, not less
data by any cryptographic guarantee. If an artifact you expect is missing,
check with the Bunker's operator which `share` it was given (`all` or
`owner:<enrolment id>`), not the signature or the serial — those are fine.

### <a name="weak-rsa"></a>"RSA … is weak"

```text
RSA 2048-bit is weak: replace with Ed25519, ECDSA or RSA 3072+
```

An enrolled key still verifies and is still trusted — this is a warning, not
a refusal. Generate an Ed25519 or ECDSA key (or an RSA key of 3072 bits or
more) on the Bunker, enrol it alongside or instead of the weak one, and
retire the old one from the Bunker's own signing configuration when you are
ready.

### <a name="hardware-only"></a>"no enrolled hardware key signed this catalogue"

```text
no enrolled hardware key signed this catalogue
```

`station set --mirror-require-hardware-key` is on, and nothing that signed
this catalogue is an enrolled `sk-*` key or a key you affirmed as
hardware-backed at enrolment. Either enrol a hardware signer from this
Bunker, or turn the policy off:

```sh
hammunition station set --no-mirror-require-hardware-key
```

if a file key is an acceptable trade-off for this machine.

### <a name="token-access-denied"></a>`hammunition doctor` says a token is denied or unreachable

```text
token access denied or unmeasured; inspect the archive's device rules
```

`doctor` could not reach a FIDO2 or PIV token as *you*, without `sudo`. If
you ran `doctor` as root or through `sudo`, run it again as yourself first —
that is the other message, "user access unmeasured: run hammunition doctor
as yourself, without sudo". If you already did and it still says this, the
distribution's own udev rules for the token are the thing to inspect; this
page does not write or change them, and `hammunition doctor` never signs a
test message to find out.

### <a name="offline-apt-pip-npm"></a>"apt and pip offline" / an npm step refuses under `--offline`

```text
tlf: offline: needs apt package(s) this machine does not have: libhamlib4
```

Phase 1's Bunker covers data and pinned payloads only. An apt package the
machine does not already have, a pip resolution, an npm install, or a
third-party apt repository addition still needs the network, and
`--offline` refuses each by name rather than fail partway through. Run the
unit online once, or wait for apt and pip Bunker support, which is phase 2.

### <a name="moved-tag"></a>A mirrored git build refuses over a moved tag or a missing submodule pin

```text
tag v2026.08.31 no longer resolves to the commit it is pinned to: a1b2c3…, got d4e5f6…
```

The Bunker's bundle is bit-for-bit what it recorded, but the upstream tag
has since been re-pointed to a different commit — the engine will not
silently trust whichever commit the tag resolves to today. [Open an
issue](https://github.com/Renegade-Penguin/Hammunition/issues) naming the
package; the manifest's pin needs updating to the tag's current commit, or
to a plain SHA. A submodule the manifest marks `submodules: true` but whose
gitlink has no matching `.gitmodules` entry in the mirrored tree refuses the
same way, for the same reason: the Bunker and the manifest disagree about
what the build needs, and the engine will not guess which one is right.
