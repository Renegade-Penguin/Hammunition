# Bunker catalogue

The engine and Bunker share `hammunition.catalogue.parse(raw: bytes)` and
`hammunition.keystrength.classify(public_key_line: str)`. The reader accepts
UTF-8 JSON version 3, refuses duplicate JSON fields and invalid required fields
by name, and allows extra fields within version 3. Typed models are frozen;
wire lists become tuples. This wire format has no CLI JSON envelope.

## Fields

| Field | Type | Rule |
|---|---|---|
| `kind` | str | exactly `bunker-index` |
| `version` | int | `3`; a reader refuses a higher version by name |
| `serial` | int | ≥ 1, strictly greater on every change |
| `generated` | str | RFC 3339 UTC, `Z` suffix |
| `bunker.name` | str | `[a-z0-9][a-z0-9-]{0,62}`; the signing principal is `bunker:<name>` |
| `bunker.mode` | str | `personal` or `group` |
| `signers` | list | 1–16 entries; larger lists are refused before key classification; `signature` is a relative path under `catalogue.sig.d/` |
| `signers[].algorithm` | str | the OpenSSH key type string of `public_key` |
| `signers[].bits` | int | key size as `ssh-keygen -l` reports it |
| `signers[].hardware` | bool | the Bunker's claim; provable only for `sk-*` types |
| `signers[].no_touch_required` | bool | true only for an `sk-*` key created with `-O no-touch-required`; **display only**: OpenSSH's allowed-signers format has no touch option (measured 2026-10-07 on OpenSSH 10.3: `bad options: unknown key option`), so the engine shows it at enrolment and in status and never writes it. Whether `-Y verify` accepts a signature made without touch is a bench item |
| `artifacts` | list | may be empty (`[]`) on a fresh Bunker |
| `inputs` | list | may be empty (`[]`) on a fresh Bunker; `signers` still requires ≥ 1 |
| `artifacts[]` | object | the v2 entry fields unchanged, plus `publisher_name` (str or null), `publisher_size` (int or null), `share` (`all` or `owner:<enrolment id>`) |
| `inputs[].kind` | str | one of `region-outline`, `tile-selection`, `sheet-selection`, `dem3dep-selection`, `fstopo-selection` |
| `inputs[].region` | str | an OSM region name as the engine spells it |

All declared fields are required, including nullable fields. `engine_version`
is a string or null; `deferred` and `declined` are JSON lists and `last_run`
is any JSON value. Artifact fields are `unit`, `name`, `path`, `sha256`, `size`,
`publisher_check`, `publisher_digest`, `publisher_url`, `publisher_name`,
`publisher_size`, `licence`, `fetched`, `verified`, `status`, `reason`,
`previous` and `share`. Input fields are `kind`, `region`, `name`, `path`,
`sha256`, `size` and `fetched`. Signers also require `id` (the SHA256 OpenSSH
fingerprint), `public_key` and `signature`.

Held-byte fields (`path`, `sha256`, `size`, `fetched`, `verified`) may be null
for failed or unheld artifacts. Such entries remain in the catalogue, but
artifact lookup refuses entries without path, digest or size. Status and reason
retain the writer's vocabulary. `publisher_digest`, `reason` and `previous`
are also nullable. `publisher_url` is always a string.

Paths and names are safe relative paths without empty, dot or parent segments,
backslashes or CR/LF/NUL. Units have one segment. Digests are 64 lowercase hex
digits; sizes are strict nonnegative integers. Publisher name and size must be
set or null together, with positive size and a safe name. Timestamps must have
valid calendar dates and the RFC 3339 UTC `Z` form. Duplicate signer ids,
signature paths, artifact `(unit, name)` pairs and input `(kind, region)` pairs
are refused. Input paths equal `inputs/<kind>/<name>`; artifact paths may differ
from request names (for example, dated OSM files).

## Key strength

| rank | Types | weak |
|---|---|---|
| 1 | `ssh-ed25519`, `sk-ssh-ed25519@openssh.com` | no |
| 2 | `ecdsa-sha2-nistp256/384/521`, `sk-ecdsa-sha2-nistp256@openssh.com` | no |
| 3 | `ssh-rsa` ≥ 3072 bits | no |
| 4 | `ssh-rsa` 2049–3071 bits | no, but below the recommended 3072 |
| 5 | `ssh-rsa` ≤ 2048 bits | **yes**: warning text "RSA <bits>-bit is weak: replace with Ed25519, ECDSA or RSA 3072+" |

Other types, including DSA, are refused. RSA weak-key warning text is exactly
`RSA <bits>-bit is weak: replace with Ed25519, ECDSA or RSA 3072+`.
`KeyStrength` also carries `fingerprint` metadata. The embedded key type must
match the public line's type; OpenSSH supplies size and fingerprint. `sk-*`
keys must claim hardware; other hardware claims require operator affirmation
at enrolment. `no_touch_required=true` is refused on a non-`sk` type and is
display metadata only, never an allowed-signers option.

## Signatures and trust

Signatures cover exactly the served `catalogue.json` bytes, without reencoding.
Namespace: `hammunition-bunker-catalogue`; principal: `bunker:<bunker.name>`.
Each signer names its file under `catalogue.sig.d/`. The local enrolment store
is `~/.config/hammunition/mirror.json` (0600), carrying URL, Bunker name,
enrolment id, enrolled public lines, algorithm, bits, hardware affirmation and
accepted serial. Verification uses an ephemeral OpenSSH allowed-signers file:

```text
bunker:<name> namespaces="hammunition-bunker-catalogue" <public key>
```

Any enrolled signer that verifies may satisfy trust; hardware-required mode
counts only an enrolled hardware key. An equal serial is accepted; a lower
serial is rollback and requires explicit `mirror accept-older`. The writer
increments serial on every change. A generated timestamp older than 30 days
warns. Parsing alone performs no signature, freshness or cross-run serial check.

Trust tiers remain distinct: a repository-pinned sha256 is publisher-byte
trust; a publisher digest records the publisher's check; a Bunker signature
and stored sha256 authenticate what the enrolled Bunker held, and do not turn
an unverified publisher download into verified publisher bytes.

## Routes and sharing

Catalogue: `<mirror>/catalogue.json`; signatures:
`<mirror>/catalogue.sig.d/<n>.sig`; artifacts: `<mirror>/<unit>/<name>`;
inputs: `<mirror>/inputs/<kind>/<name>`. Git bundles use unit `git-bundles`,
name `<unit>@<commit>`; submodule artifacts use
`<unit>@<commit>/<submodule path>@<commit>`. File mirrors read these same paths
from a directory and use no headers.

A missing catalogue refuses with:
`no catalogue at <url>/catalogue.json: <reason>. Enrol a Bunker that serves one, or run without --offline.`
Here `<url>` has no trailing slash.

In group mode requests carry `X-Hammunition-Enrolment: <enrolment id>`.
The id is nonempty printable ASCII without whitespace or controls. All entries
are parsed and retained; lookup selects `all` or matching `owner:<id>` entries.
Personal mode ignores this sharing filter. The Bunker answers 404 for another
owner's bytes. This is a sharing filter, not access control: the header is sent
in clear over plain HTTP.

## Selection input codecs

Plan B exports selection inputs with the engine's existing record writers;
Plan A reads them with the matching readers. The binding version 3 JSON
contract is unchanged. An input is identified by `kind` and `region`; its
signed `name` and `path` locate the payload. Consumers do not infer its kind
from a filename extension.

| Contract kind | Payload name | Writer and reader APIs |
| --- | --- | --- |
| `region-outline` | `<region>.poly` | Geofabrik Osmosis `.poly` text; `copernicus.parse_poly` |
| `tile-selection` | `<region>.tiles` | `backends.dem.render_record(RegionTiles)` / `read_record` |
| `dem3dep-selection` | `<region>.tiles` | `backends.dem.render_record(RegionTiles)` / `read_record` |
| `sheet-selection` | `<region>.quads` | `backends.topo.render_record(RegionQuads)` / `read_record` |
| `fstopo-selection` | `<region>.quads` | `backends.fstopo.render_record(RegionSheets)` / `read_record` |

Each reader takes `(path: Path, region: str, slug: str)`; the slug is the
region with `/` replaced by `-`. Tile records carry an unpublished-square
count, an optional bound line, and tile names. Sheet records carry a row
count, an optional bound line, and complete index rows. A header-only record
is a valid empty selection. Export the writer's exact UTF-8 bytes, including
its headers, rather than building another codec.

`ResolutionContext.input_bytes` bounds each input to 32 MiB and checks its
bytes against the signed SHA-256 and size through `require_payload` before
caching a successful read for that run. `selection` decodes through the
reader in a temporary directory; planning never writes installed records.
Invalid UTF-8, malformed records, and hash or size mismatches refuse by name.
`outline` validates the recorded polygon with `parse_poly`.

A current installed selection takes precedence. Online, publisher outlines
are tried through the shared `MemoProbe`; after retries exhaust, or offline,
a compatible Bunker selection answers. Otherwise the verified outline is
used to recompute against the carried lists. A bounded subset cannot supply
an `all` request. An `all` selection can be narrowed to the requested bound;
`none` bypasses selection inputs. Selected sheets use the current carried
index rows, sizes and ETags, never those of a Bunker record. A stale selected
sheet requires recomputation from an outline. Neither a missing selection
nor a missing outline permits substituting a rectangular bounding box.

These inputs supply selection only, never the payload. The matching payload
routes (Copernicus and 3DEP elevation tiles, US Topo and FSTopo sheets) now
check the Bunker for the artifact bytes themselves too, against the
publisher's own ETag, size or MD5, or a pinned sha256 where one exists —
the same check the publisher's copy would get, never the Bunker's say-so
alone. A complete offline resolution for a region needs both: a selection
record and every payload artifact the Bunker can answer for. A Bunker
holding one without the other still leaves the unit short, and it is
deferred whole through `catalogue_deferral`, like any other gap, rather
than installed partway.

If neither route supplies a complete selection, a profile member and its
dependents are deferred as whole units through `catalogue_deferral`. An
explicit request refuses by name. No partial selection is treated as a
complete region, and deferred units have no installation steps or owned
configuration changes.

## `artifacts --json` as the listing source (Task 16)

`hammunition artifacts --json` is the one command that answers what to put
at every route above, for an explicit unit and region selection, with no
station read and no install: its `artifacts` array is every payload
(`data`, `osm-regions`, `dem-tiles`, `mwm-regions`, `kiwix-books`,
`register`, both `topo-quads` providers, and now every `source`, `binary`,
`venv`, `node`, `git` and tooled `derived` block), its `inputs` array is
exactly the bytes for the five selection-input kinds above (each is the
engine's own writer's output, byte for byte, via
`hammunition.artifacts.list_inputs`), and its `git_pins` array is every
`git` block's pinned revision (`hammunition.artifacts.list_git_pins`).

**Division of responsibility for a git bundle.** `artifacts --json` never
clones a repository and never names a recursive submodule bundle: a
`git_pins` entry carries only the parent revision (`unit@commit`) and the
manifest's `submodules: bool`, because which submodules exist at which
gitlink commits is not knowable from the manifest alone -- it is a property
of the tree at that commit. The Bunker's own writer is the one that clones
the parent, and when `submodules` is true, walks the pinned gitlinks
through the engine's `hammunition.gitbundles` API (the same walk
`checkout_gitlinks` does for an online install) to produce one bundle per
submodule, named `<unit>@<parent commit>/<submodule path>@<submodule
commit>` as above. Those names appear in the Bunker's own generated
catalogue, never in `artifacts --json`'s `git_pins` array -- that array is
the *parent* pin list a writer starts from, not the finished bundle set.
A `git_pins` entry with `commit: null` (an unpinned tag) is diagnostic only
and is never a bundle name a writer should try to serve.
