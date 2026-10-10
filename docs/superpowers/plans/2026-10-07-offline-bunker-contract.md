# Offline Bunker: the contract between engine and Bunker (#381, phase 1)

Both implementation plans build against this file; neither may change it
without changing the other. Spec:
`docs/superpowers/specs/2026-10-07-offline-bunker-catalogue-design.md`.

## Ownership

- The **engine** owns the reader and the shared rules: `hammunition.catalogue`
  (parse and validate a v3 catalogue), `hammunition.keystrength` (classify a
  public key, the #389 order and the weak-key warning) and
  `hammunition.signers` (verify a catalogue against enrolled keys). The
  Bunker imports these from its pinned engine instead of re-implementing them.
- The **Bunker** owns the writer, signing, key management, serving, group
  gating and export.
- Order: Plan A (engine) merges and is released first; Plan B (Bunker) bumps
  its engine pin to that release and its tests validate every catalogue it
  writes with `hammunition.catalogue.parse()`.

## The catalogue file

Path on the volume and on the wire: `catalogue.json` at the Bunker root (the
existing `index.json` file, renamed; the server answers both names for one
release). Signed bytes: exactly the bytes served. UTF-8 JSON.

```json
{
  "kind": "bunker-index",
  "version": 3,
  "serial": 42,
  "generated": "2026-10-07T12:00:00Z",
  "bunker": {"name": "bunker", "mode": "personal"},
  "signers": [
    {
      "id": "SHA256:Z8x0...",
      "public_key": "sk-ssh-ed25519@openssh.com AAAA... bunker-yubikey-1",
      "algorithm": "sk-ssh-ed25519@openssh.com",
      "bits": 256,
      "hardware": true,
      "no_touch_required": false,
      "signature": "catalogue.sig.d/1.sig"
    }
  ],
  "engine_version": "0.22.0",
  "artifacts": [
    {
      "unit": "osm-regions",
      "name": "europe/monaco",
      "path": "osm-regions/europe/monaco-260101.osm.pbf",
      "sha256": "…64 hex…",
      "size": 123456,
      "publisher_check": "md5",
      "publisher_digest": "…32 hex…",
      "publisher_url": "https://download.geofabrik.de/europe/monaco-260101.osm.pbf",
      "publisher_name": "monaco-260101.osm.pbf",
      "publisher_size": 123456,
      "licence": "ODbL-1.0",
      "fetched": "2026-10-06T03:00:00Z",
      "verified": "2026-10-07T03:00:00Z",
      "status": "current",
      "reason": null,
      "previous": null,
      "share": "all"
    }
  ],
  "inputs": [
    {
      "kind": "region-outline",
      "region": "europe/monaco",
      "name": "europe/monaco.poly",
      "path": "inputs/region-outline/europe/monaco.poly",
      "sha256": "…64 hex…",
      "size": 2048,
      "fetched": "2026-10-06T03:00:00Z"
    }
  ],
  "deferred": [],
  "declined": [],
  "last_run": null
}
```

Field rules (the reader refuses a document that breaks one, naming the field):

| Field | Type | Rule |
|---|---|---|
| `kind` | str | exactly `bunker-index` |
| `version` | int | `3`; a reader refuses a higher version by name |
| `serial` | int | ≥ 1, strictly greater on every change |
| `generated` | str | RFC 3339 UTC, `Z` suffix |
| `bunker.name` | str | `[a-z0-9][a-z0-9-]{0,62}`; the signing principal is `bunker:<name>` |
| `bunker.mode` | str | `personal` or `group` |
| `signers` | list | ≥ 1 entry; `signature` is a relative path under `catalogue.sig.d/` |
| `signers[].algorithm` | str | the OpenSSH key type string of `public_key` |
| `signers[].bits` | int | key size as `ssh-keygen -l` reports it |
| `signers[].hardware` | bool | the Bunker's claim; provable only for `sk-*` types |
| `signers[].no_touch_required` | bool | true only for an `sk-*` key created with `-O no-touch-required`; **display only**: OpenSSH's allowed-signers format has no touch option (measured 2026-10-07 on OpenSSH 10.3: `bad options: unknown key option`), so the engine shows it at enrolment and in status and never writes it. Whether `-Y verify` accepts a signature made without touch is a bench item |
| `artifacts` | list | may be empty (`[]`) on a fresh Bunker |
| `inputs` | list | may be empty (`[]`) on a fresh Bunker; `signers` still requires ≥ 1 |
| `artifacts[]` | object | the v2 entry fields unchanged, plus `publisher_name` (str or null), `publisher_size` (int or null), `share` (`all` or `owner:<enrolment id>`) |
| `inputs[].kind` | str | one of `region-outline`, `tile-selection`, `sheet-selection`, `dem3dep-selection`, `fstopo-selection` |
| `inputs[].region` | str | an OSM region name as the engine spells it |

**Selection inputs are the engine's own record files, byte for byte** (the
records `terrain_plan` and `topo_plan` already write and read, e.g.
`render_record(RegionQuads(...))`). The Bunker never implements its own
codec: it produces them by calling the pinned engine's functions, and the
engine re-validates each against its verified outline and the current bound
before using it (amended 2026-10-07 after Plan A's review).

`publisher_name`/`publisher_size` are null for kinds the repository pins by
sha256 (the pin is the trust) and set for publisher-digest kinds (unpinned
OSM, unpinned Copernicus tiles, FSTopo) from what the Bunker saw at fetch.

## Signatures

- Namespace: `hammunition-bunker-catalogue`. Principal: `bunker:<bunker.name>`.
- One file per signer at the path its `signers[].signature` names, made with
  `ssh-keygen -Y sign -n hammunition-bunker-catalogue -f <key> catalogue.json`
  (file and FIDO2 keys) or with `-f <public key>` and the key in `ssh-agent`
  (PIV/PKCS#11).
- The engine keeps its enrolled keys in `~/.config/hammunition/mirror.json`
  (0600: Bunker URL, name, enrolment id, each key's public line, algorithm,
  bits, hardware-as-affirmed, accepted serial) and renders an OpenSSH
  allowed-signers file from it at verify time:
  `bunker:<name> namespaces="hammunition-bunker-catalogue" <public key>`
  (no touch option: the format has none; see `no_touch_required`).
- Verification: for each `signers[]` entry whose key is enrolled, run
  `ssh-keygen -Y verify -f <allowed signers> -I bunker:<name> -n hammunition-bunker-catalogue -s <sig>`
  with `catalogue.json`'s bytes on stdin. Accept when any one succeeds.
  With `mirror_require_hardware_key` on, only a key with `hardware` true in
  `mirror.json` counts; `sk-*` keys are hardware by type, and a non-`sk`
  key is hardware only if the operator affirmed it at enrolment.
- Freshness: refuse `serial` < accepted serial (remedy:
  `hammunition mirror accept-older`); warn when `generated` is older than 30
  days.

## Key strength (`hammunition.keystrength`)

`classify(public_key_line) -> KeyStrength(algorithm, bits, hardware_by_type, rank, weak, warning)`:

| rank | Types | weak |
|---|---|---|
| 1 | `ssh-ed25519`, `sk-ssh-ed25519@openssh.com` | no |
| 2 | `ecdsa-sha2-nistp256/384/521`, `sk-ecdsa-sha2-nistp256@openssh.com` | no |
| 3 | `ssh-rsa` ≥ 3072 bits | no |
| 4 | `ssh-rsa` 2049–3071 bits | no, but below the recommended 3072 |
| 5 | `ssh-rsa` ≤ 2048 bits | **yes**: warning text "RSA <bits>-bit is weak: replace with Ed25519, ECDSA or RSA 3072+" |

Other types (DSA) are refused. The same text appears wherever a key is shown.

## Fetch routes and group gating

- Catalogue: `<mirror>/catalogue.json`, signatures `<mirror>/catalogue.sig.d/<n>.sig`.
  A missing catalogue refuses with exactly: "no catalogue at <url>/catalogue.json: <reason>. Enrol a Bunker that serves one, or run without --offline." (`<url>` has no trailing slash).
- Artifact bytes: `<mirror>/<unit>/<name>` (unchanged, D-070).
- Inputs: `<mirror>/inputs/<kind>/<name>`.
- Git bundles: unit `git-bundles`, name `<unit>@<commit>`; one
  `git bundle create` file with every ref needed for the pinned commit and,
  as separate artifacts named `<unit>@<commit>/<submodule path>@<commit>`,
  each submodule.
- Group mode: every request from an enrolled laptop carries
  `X-Hammunition-Enrolment: <enrolment id>`. The Bunker serves an
  `owner:<id>` artifact's bytes only to a request with the matching header
  and answers 404 otherwise. The catalogue lists every entry; the engine
  ignores entries not shared with it. This is a sharing filter, not access
  control: it is sent in clear over plain HTTP and the docs say so.
- `file://` mirrors read the same paths from a directory; no headers.


## Artifact listing amendments (2026-10-07)

- `artifacts --json` lists US Topo/3DEP sheets with `check: "etag-md5"`
  and `digest` equal to the raw ETag exactly as carried in the repository:
  32 hex for a single-part object, `<hex>-<parts>` for multipart. Each row
  also carries `part_size` (int bytes or null when no part size is recorded).
- Inputs are inline in the document's `inputs` array as UTF-8 `content`.
  Refuse to list any input over 8 MiB (8 × 1024 × 1024 encoded bytes), name
  that input and suggest `--units`; do not truncate or omit its content.
- `--units` filters `inputs` to the selected units' explicitly requested
  regions and filters `git_pins` by the selected catalog unit. No station
  selection is read. A selected static unit needs no regional inputs.
- Every `git_pins` entry carries `licence: str` from the manifest's licence
  field, verbatim. Existing manifests without it report
  "licence not recorded in this manifest"; never infer it from the catalog
  YAML file's own CC0 header.
