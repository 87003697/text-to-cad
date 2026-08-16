# Agent runtime supply, lock, promotion, and rollback contract

Status: proposed design decision; no implementation or runtime evidence is
claimed by this document.

The formal Cup Agent runtime is supplied by an immutable, content-addressed
archive selected through one canonical Agent Runtime Lock. The SHA-256 digest
of the canonical lock bytes is the supply authority. A versioned `current`
channel object atomically selects that lock digest and retains exactly one
predecessor lock digest. Image names and tags are transport conveniences only;
they never select an artifact for execution.

This deliberately separates artifact identity, transport identity, deployment
state, and execution source. Without that separation, a locally loaded image
ID, an S3 tarball, a Git revision, and a mutable tag could all appear to mean
"the runtime" while naming different bytes.

## Source boundary and reusable precedent

The existing Browser Sidecar workflow is useful precedent, but not the Agent
runtime supply implementation:

- [`cvm_sidecar_probe.py`](../../scripts/pilot/cvm_sidecar_probe.py) inspects
  exact source revisions and image IDs, creates a fresh deterministic handle,
  writes a hash-and-size-attested legacy Docker-native archive, and publishes a
  local prepare receipt. That legacy archive format is not selected here.
- Its provision step rechecks the local archive, creates a single-owner
  attempt, transfers the archive and receipt by direct `rsync`, and validates a
  remote provision receipt. This is a bounded prepare/provision/probe pattern,
  not a persistent artifact store.
- The remote side has fail-closed ownership and abort receipts, and verifies
  loaded image inventory. Those lifecycle shapes can be generalized.
- [`browser_sidecar.py`](../../scripts/pilot/browser_sidecar.py) rejects a
  malformed Broker lock and inspects exact pre-provisioned image properties,
  but the current
  [`image-lock.json`](../../packages/meshshot/browser_sidecar_broker/image-lock.json)
  has only `baseImageId`, `imageId`, and `sourceRevision`.
- Sidecar identity also lives separately in
  [`browser_contract.json`](../../packages/meshshot/src/meshshot/browser_contract.json)
  and source constants. A source-configured ID and a host-local loaded ID are
  observations in different namespaces; equality must be proved, not assumed.
- [`snapshot-batch.sh`](../../scripts/pilot/snapshot-batch.sh) copies a working
  tree including `.git`, dereferences symlinks, records dirty/untracked state,
  recursively uploads to an output prefix, and skips any non-empty destination.
  It does not create the closed immutable artifact or Source Snapshot contract
  defined here.

The required artifact verification authority is the immutable
[`text-to-cad.agent-runtime-verification/1`](agent-runtime-verification-receipt.md)
root. In particular, rollback may reuse that root only for unchanged artifact
bytes and lock inputs, while every rollback deployment still needs fresh host
provision and conformance evidence.

## Canonical identities

Every JSON digest below is `sha256:` plus 64 lowercase hexadecimal characters
over the repository's `sha256-canonical-json-v1` bytes. Producers must use one
repository canonicalizer and consumers must reject duplicate keys, unknown
keys, invalid UTF-8, non-integer JSON numbers, and non-canonical encodings.

| Name | Meaning | Authority |
| --- | --- | --- |
| OCI index digest | Canonical `index.json` bytes in the OCI image layout | Portable selection root inside the archive |
| Agent image manifest digest | OCI linux/amd64 manifest selected from that index | Portable runtime image identity |
| Agent image config digest | OCI configuration object referenced by that manifest | Portable entrypoint, labels, and rootfs configuration identity |
| OCI layer digests | Ordered layer descriptors and exact blob bytes referenced by the manifest | Portable rootfs byte closure |
| OCI layout archive digest | SHA-256 of the deterministic tar containing the exact OCI image layout | Transfer integrity only |
| Host-local loaded image ID | Full 64-hex `sha256:` ID independently observed after import | Host execution address for one fresh provision receipt only |
| S3 object version | Bucket, key, and immutable VersionId holding those archive bytes | Storage location, not runtime identity |
| Runtime manifest digest | Closed inventory of runtime programs and versions | Capability content identity |
| Cup capability manifest digest | Closed Cup-only callable capability set | Scope identity |
| Build-input-set digest | Exact admitted base, dependency, recipe, and project runtime inputs | Rebuild identity |
| SBOM digest | SPDX JSON 2.3 bytes for the image | Inventory evidence |
| Verified root digest | Exact immutable verification graph root bytes | `Agent Runtime Verified` evidence identity |
| Agent Runtime Lock digest | Digest of the complete lock below | **Canonical supply authority** |
| Source Snapshot digest | Exact execution source tree manifest and payload | Separate per-execution artifact |
| Channel state VersionId | One atomic version of the `current` object | Promotion history and active selection |

The archive object is therefore not the authority by itself. It is a transport
blob. The Agent Runtime Lock digest selects the archive bytes, OCI
index/manifest/config/layers, admitted outer importer, build/runtime/Cup
identities, SBOM, and Verified root as one closed statement. The `current`
channel selects only an Agent Runtime Lock digest; it never selects an archive,
image tag, Git branch, portable manifest, or host-local image ID independently.

## Immutable S3 layout

The first release uses the already-authorized bucket and an isolated runtime
namespace:

```text
s3://arcwm-code-us-west-2/
  ericzyma/text-to-cad/runtime/agent/v1/
    archives/sha256/<archive-hex>.oci.tar
    locks/sha256/<lock-hex>.json
    sbom/sha256/<sbom-hex>.spdx.json
    provenance/sha256/<provenance-hex>.json
    verification/objects/sha256/<evidence-hex>.json
    verification/roots/sha256/<verified-root-hex>.json
    promotion-authorizations/sha256/<authorization-hex>.json
    channels/cup-formal/current.json
    reconciliations/<reconciliation-handle>/terminal.json
    reconciliation-tombstones/<reconciliation-handle>.json
    operations/<operation-handle>/terminal.json
```

Bucket versioning is a prerequisite. Immutable objects are created with a
create-only precondition. If a content-addressed key already exists, the
publisher must not overwrite it: it must record its VersionId, reread the
exact version, and prove byte count and SHA-256 equality. ETag is recorded only
as an S3 diagnostic; it is never a content digest.

Each lock records `bucket`, `region`, `key`, `versionId`, `bytes`, and `sha256`
for its archive. A consumer fetches that exact version. A same-key later
version, an unversioned GET, a prefix listing, and a Mac-mounted pathname are
not substitutes.

## Agent Runtime Lock

An Agent Runtime Lock has exactly the following top-level keys. `lockDigest`
is intentionally absent because it is the digest of these canonical bytes and
cannot safely be self-referential.

```json
{
  "agentImage": {
    "configBytes": 9012,
    "configDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "configMediaType": "application/vnd.oci.image.config.v1+json",
    "indexBytes": 345,
    "indexDigest": "sha256:abababababababababababababababababababababababababababababababab",
    "layers": [
      {
        "bytes": 1234,
        "digest": "sha256:1212121212121212121212121212121212121212121212121212121212121212",
        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"
      },
      {
        "bytes": 5678,
        "digest": "sha256:3434343434343434343434343434343434343434343434343434343434343434",
        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"
      }
    ],
    "manifestBytes": 678,
    "manifestDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "manifestMediaType": "application/vnd.oci.image.manifest.v1+json",
    "platform": {"architecture": "amd64", "os": "linux"}
  },
  "archive": {
    "admissionReceiptDigest": "sha256:7878787878787878787878787878787878787878787878787878787878787878",
    "bytes": 123456789,
    "format": "oci-image-layout-tar-v1",
    "s3": {
      "bucket": "arcwm-code-us-west-2",
      "key": "ericzyma/text-to-cad/runtime/agent/v1/archives/sha256/cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.oci.tar",
      "region": "us-west-2",
      "versionId": "illustrative-version-id"
    },
    "sha256": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  },
  "build": {
    "baseImageManifestDigest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "buildInputSetDigest": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "buildRecipeDigest": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "builderImageManifestDigest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "projectRuntimeArtifactSetDigest": "sha256:2323232323232323232323232323232323232323232323232323232323232323"
  },
  "codex": {
    "archiveDigest": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
    "executableDigest": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
    "platform": "x86_64-unknown-linux-musl",
    "version": "0.147.0"
  },
  "outerImporter": {
    "admissionReceiptDigest": "sha256:4545454545454545454545454545454545454545454545454545454545454545",
    "name": "text-to-cad-oci-import",
    "platformArtifactSetDigest": "sha256:5656565656565656565656565656565656565656565656565656565656565656",
    "toolClosureDigest": "sha256:6767676767676767676767676767676767676767676767676767676767676767",
    "version": "1"
  },
  "manifests": {
    "cupRuntimeCapabilityManifestDigest": "sha256:5555555555555555555555555555555555555555555555555555555555555555",
    "runtimeManifestDigest": "sha256:6666666666666666666666666666666666666666666666666666666666666666"
  },
  "sbom": {
    "digest": "sha256:7777777777777777777777777777777777777777777777777777777777777777",
    "format": "spdx-json-2.3",
    "s3": {
      "bucket": "arcwm-code-us-west-2",
      "bytes": 456789,
      "key": "ericzyma/text-to-cad/runtime/agent/v1/sbom/sha256/7777777777777777777777777777777777777777777777777777777777777777.spdx.json",
      "region": "us-west-2",
      "versionId": "illustrative-sbom-version-id"
    }
  },
  "schema": "text-to-cad.agent-runtime-lock/1",
  "verification": {
    "rootDigest": "sha256:8888888888888888888888888888888888888888888888888888888888888888",
    "schema": "text-to-cad.agent-runtime-verification/1",
    "s3": {
      "bucket": "arcwm-code-us-west-2",
      "bytes": 567890,
      "key": "ericzyma/text-to-cad/runtime/agent/v1/verification/roots/sha256/8888888888888888888888888888888888888888888888888888888888888888.json",
      "region": "us-west-2",
      "versionId": "illustrative-verification-version-id"
    },
    "subjectDigest": "sha256:9999999999999999999999999999999999999999999999999999999999999999"
  }
}
```

The example is syntax only. It is not a lock for an existing image.

The lock consumer enforces all of these equalities:

1. the lock's manifest/config, base/builder/build-input/recipe/project-runtime,
   Codex, runtime, Cup, SBOM, and verification values equal the fields that
   actually exist in the Verified root subject and its referenced children;
2. the Verified root object is fetched by exact digest and has status
   `verified` under its strict graph validator;
3. archive `sha256` equals both its content-addressed key suffix and the exact
   fetched bytes; `bytes` equals the fetched length;
4. the deterministic archive expands to exactly `oci-layout`, `index.json`,
   and the locked blob set; index bytes select exactly one locked linux/amd64
   manifest whose descriptors select the locked config and ordered layers;
5. Codex and SBOM identities equal their corresponding admitted evidence; and
6. the outer importer admission receipt closes its exact name, version,
   per-platform executable artifacts, and tool closure; and
7. every field and nested field set is exact. Missing or additional fields
   close admission.

The SAR-005 graph has no OCI index, layer-set, or outer-importer fields. This
contract does not invent equalities to absent evidence. Instead, the archive's
`admissionReceiptDigest` closes deterministic packaging, index selection,
manifest/config descriptors, ordered layers, and exact blob bytes, while the
outer importer's admission receipt closes supply tooling. The selected
manifest/config then form the equality seam into the existing SAR-005 image
identity and build-provenance fields. No separate build snapshot field exists
on either side of that seam.

No local tag or host-local image ID is recorded in the lock. The admitted outer
importer imports the exact OCI image-layout archive into the target Docker
engine and emits its own receipt. A provisioner may create a nonce-scoped
temporary import reference, but it must resolve that reference through fresh
host inspection and remove it or mark its residue in the terminal receipt.
The portable manifest digest is not assumed to be a runnable local address.
Execution uses the full host-local image ID from that fresh provision receipt,
after another exact inspect-before-start, with `--pull=never`.

## Source Snapshot is a separate execution artifact

The lock's build identity is closed by the SAR-005 `buildInputSetDigest`,
`buildRecipeDigest`, and `projectRuntimeArtifactSetDigest`; there is no separate
build snapshot concept or lock field. The execution Source Snapshot is a
separate artifact mounted read-only for one Agent Execution and may change
without changing the Agent Runtime Lock.

A formal Source Snapshot must have a strict
`text-to-cad.source-snapshot-lock/1` containing:

- the exact source manifest digest, payload archive SHA-256 and byte count;
- S3 bucket, region, content-addressed key, immutable VersionId, and payload
  format;
- exact Git commit, clean/dirty policy result, path count, total unpacked
  bytes, per-file type/mode/size/SHA-256, and an explicit symlink policy;
- exclusions and normalization version; and
- a visibility receipt digest produced only after exact S3 reread and Mac mount
  reread.

The formal policy accepts a clean exact commit plus declared generated inputs.
Dirty or untracked source is rejected unless a future schema explicitly names
every included byte and a separate approval permits it. `.git` is metadata,
not execution source. Symlinks are either rejected or represented as symlinks
with their link text; silently dereferencing them is forbidden.

Therefore the current snapshot script must be hardened before it can produce
this artifact. “Destination prefix is non-empty” is not idempotence; exact
manifest equality is. A recursive upload without a closed path manifest,
per-file hashes, object versions, aggregate byte/path counts, an exact reread,
and a terminal receipt is not a sealed Source Snapshot.

An execution admission record later binds four independent identities: current
Agent Runtime Lock digest, execution Source Snapshot Lock digest, input
snapshot digest, and exact Broker/Sidecar locks. None may be inferred from
another.

## Closed supply state machine

Each operation has a fresh random 256-bit owner nonce and an immutable handle
of `sarsp-<24 lowercase hex>`. A handle is claimed once and cannot be adopted.
Every stage reads and validates the exact terminal output of its predecessor.

| Stage | Required success output | Fail-closed rule |
| --- | --- | --- |
| Acquire | exact external/build input objects plus retrieval receipts | No mutable URLs, tags, or ambient caches become admitted inputs |
| Admit | closed build-input set, Codex admission, dependency locks, and expected hashes | Any missing byte/hash/signature-policy result stops before build |
| Offline Build | linux/amd64 OCI index/manifest/config/layers and build provenance | Network must be disabled; an undeclared cache hit or fetch fails the build |
| Prepare | deterministic OCI image-layout tar, closed blob set, archive hash/size, SBOM, candidate lock bytes | Any missing/extra/re-encoded OCI object or importer admission gap fails preparation |
| Immutable S3 Publish | archive, lock, SBOM/provenance references, and exact S3 VersionIds | Create-only or exact-byte reuse; reread mismatch retains scratch |
| Provision | target-owned provision receipt for exact lock/archive/importer | Fetch exact VersionId, use only the admitted outer importer, no registry pull, no attempt adoption |
| Verify loaded identity | loaded-identity receipt mapping portable OCI bytes to one full host-local image ID/config observation | Missing, non-unique, truncated, tag-derived, or inconsistent host mapping closes the attempt |
| Consume Verified root | strict validation of the exact lock-linked SAR-005 root | Missing, non-verified, cross-subject, or changed bytes close promotion |
| Deployment conformance | fresh target-host lifecycle and Cup conformance receipt | Required on every promotion, including rollback; does not amend SAR-005 root |
| Atomic Promote | one conditional versioned channel-state write | CAS conflict leaves prior current authoritative; an unreadable successful write makes state unresolved and blocks execution |
| Execute | execution admission binds current lock and uses `--pull=never` | Re-resolve current state and all locks; no fallback to tag/predecessor/network |

The order is normative:

```text
Acquire -> Admit -> Offline Build -> Prepare -> Immutable S3 Publish
        -> Provision -> Verify loaded identity -> Consume Verified root
        -> Deployment conformance -> Atomic Promote -> --pull=never Execute
```

No stage can “repair” an earlier missing proof. In particular, a successful
paid Formal Pilot cannot substitute for artifact admission or promotion.

The lock-linked SAR-005 root is produced by the separate artifact-verification
workflow before the lock is finalized. `Consume Verified root` above means the
supply operation strictly rereads and revalidates that existing root after
host identity is known; it does not create, rewrite, or extend the root.

## Provision and loaded-identity receipts

`text-to-cad.agent-runtime-provision/1` is terminal and exact. On success it
binds operation handle/owner digest, target environment and host fingerprint,
lock digest, archive S3 locator/hash/size, Docker server OS/architecture, exact
outer-importer admission and selected platform-artifact digests, portable OCI
index/manifest/config/ordered-layer observations, the full 64-hex host-local
loaded image ID, its independently inspected local config mapping,
temporary-reference disposition, workflow source/file digests, and resource
disposition. On failure it records the first ordered `failureCheck`,
`retryAllowed: false`, retained-resource disposition, and any separately
validated abort receipt.

`text-to-cad.agent-runtime-loaded-identity/1` is a smaller child receipt that
binds the successful provision receipt digest to exact inspected host-local
image ID/config/platform/runtime-manifest/Cup-manifest values. The runtime lock
consumer recomputes every equality. A Docker image ID that happens to be
present on one host is not portable authority; the receipt proves how this
particular imported host representation resolves to the OCI identities in the
lock. The receipt must not assume that the portable manifest digest equals a
runnable host-local image ID, or that the host-local ID equals the portable
config digest merely because a particular Docker version often represents it
that way.

The exact equality chain is:

```text
archive SHA-256 and length
  -> exact OCI layout index bytes
  -> selected linux/amd64 manifest bytes
  -> referenced config bytes + ordered layer descriptor/blob bytes
  -> exact admitted outer importer invocation and receipt
  -> independently inspected full host-local image ID/config mapping
  -> provision receipt digest + loaded-identity receipt digest
  -> execution inspect-before-start of that same host-local ID/config
  -> docker create/run by full host-local ID with --pull=never
```

Every arrow is checked in the forward direction from locked bytes and again
where the downstream receipt repeats an upstream value. Immediately before
container creation, execution rereads current channel state, selects the fresh
provision receipt bound to its target and generation, inspects the receipt's
full host-local ID, verifies the same local config/platform/runtime/Cup
mapping, and passes that exact local ID to Docker. A tag, truncated ID,
portable manifest digest used as a local address, stale provision receipt, or
registry resolution is rejected.

Promotion also requires a fresh
`text-to-cad.agent-runtime-deployment-conformance/1` for that target. It binds
the loaded-identity receipt, the unchanged Verified root, the exact harness,
and target-host lifecycle/Cup results. It is operational deployment evidence,
not a replacement or amendment of `Agent Runtime Verified`.

## Atomic promotion and channel state

Promotion first writes an immutable
`text-to-cad.agent-runtime-promotion-authorization/1`. It has exactly:

`operationRequestId` is the digest of the immutable canonical promotion request
bytes. The same value must appear in the authorization, intended channel-state
bytes, write attempt, terminal operation record, and any reconciliation; it is
not regenerated after an uncertain response.

```json
{
  "after": {
    "currentLockDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "generation": 8,
    "predecessorLockDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "before": {
    "currentLockDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "generation": 7,
    "pointerDigest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "versionId": "illustrative-prior-version"
  },
  "channel": "cup-formal",
  "deploymentConformanceReceiptDigest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "loadedIdentityReceiptDigest": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "macVisibilityReceiptDigest": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "operationHandle": "sarsp-1234567890abcdef12345678",
  "operationRequestId": "sha256:2424242424242424242424242424242424242424242424242424242424242424",
  "provisionReceiptDigest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "retryAllowed": false,
  "schema": "text-to-cad.agent-runtime-promotion-authorization/1",
  "status": "authorized",
  "verifiedRootDigest": "sha256:2222222222222222222222222222222222222222222222222222222222222222"
}
```

For bootstrap only, all `before` identity fields and
`after.predecessorLockDigest` are JSON `null`, and `generation` moves from 0 to
1. Otherwise `after.generation = before.generation + 1`,
`after.predecessorLockDigest = before.currentLockDigest`, and current and
predecessor are distinct.

The publisher then conditionally replaces exactly
`channels/cup-formal/current.json`, using the previously read S3 object
version/ETag as the compare-and-swap precondition. The new object has exactly:

```json
{
  "channel": "cup-formal",
  "currentLockDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "generation": 8,
  "operationHandle": "sarsp-1234567890abcdef12345678",
  "operationRequestId": "sha256:2424242424242424242424242424242424242424242424242424242424242424",
  "predecessorLockDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "previousPointerDigest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "promotionAuthorizationDigest": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
  "schema": "text-to-cad.agent-runtime-channel-state/1"
}
```

The conditional write is the atomic promotion. That exact object version is
both the active pointer and the durable current/predecessor promotion receipt.
Its S3 VersionId and content digest are recorded in the operation terminal
receipt. The immutable authorization proves the gates; the channel-state
version proves which lock actually became current. There is no second mutable
alias whose update could split authority.

If the service definitively returns a compare-and-swap precondition failure,
the candidate is not current. The operation records `promotion-conflict`,
retains its evidence, and does not retry automatically. A lost write response,
an unreadable returned VersionId, a failed exact S3 reread, or a failed
post-CAS Mac reread enters reconciliation instead. No client may infer success
from a candidate tag, authorization, write request, listing, or cached pointer.

Mac mount visibility is a pre-promotion gate because operators need the
canonical archive, lock, and verification graph to be visible through the
configured mount. The gate rereads those immutable mounted
bytes and compares their digests after S3 publication but before the channel
CAS. It is operational evidence only: it does not prove image identity,
runtime behavior, S3 provenance, or host provisioning. A pre-promotion mount
failure blocks promotion but does not falsify a valid SAR-005 Verified root.
After CAS, mounted visibility of the new channel-state bytes is checked again
before execution and cleanup. Failure of that post-CAS check blocks execution
and requires reconciliation; if the exact S3 version is confirmed, the later
receipt can classify it as promoted-but-not-Mac-visible. It does not silently
restore the prior pointer.

## Append-only channel reconciliation

Reconciliation is a bounded, read-only investigation of one possibly applied
conditional write. It never writes, deletes, copies, or restores
`channels/cup-formal/current.json`. It has a new one-shot handle
`sarcr-<24 lowercase hex>` and consumes the original operation request,
promotion authorization, exact before state, and intended canonical pointer
bytes. Starting reconciliation is not an automatic retry of promotion: it is a
new append-only evidence operation that cannot change channel state.

The terminal `text-to-cad.agent-runtime-channel-reconciliation/1` has exactly
this shape:

```json
{
  "before": {
    "digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "etag": "illustrative-prior-etag",
    "versionId": "illustrative-prior-version"
  },
  "checks": {
    "exactVersionGetsComplete": true,
    "macIntendedBytesVisible": true,
    "s3IntendedBytesVisible": true
  },
  "intended": {
    "operationRequestId": "sha256:2424242424242424242424242424242424242424242424242424242424242424",
    "pointerDigest": "sha256:2525252525252525252525252525252525252525252525252525252525252525",
    "promotionAuthorizationDigest": "sha256:3333333333333333333333333333333333333333333333333333333333333333"
  },
  "observed": {
    "latest": {
      "digest": "sha256:2525252525252525252525252525252525252525252525252525252525252525",
      "etag": "illustrative-intended-etag",
      "versionId": "illustrative-intended-version"
    },
    "versionChain": [
      {
        "digest": "sha256:2525252525252525252525252525252525252525252525252525252525252525",
        "etag": "illustrative-intended-etag",
        "isDeleteMarker": false,
        "isLatest": true,
        "versionId": "illustrative-intended-version"
      },
      {
        "digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "etag": "illustrative-prior-etag",
        "isDeleteMarker": false,
        "isLatest": false,
        "versionId": "illustrative-prior-version"
      }
    ]
  },
  "outcome": "promoted",
  "promotionOperationHandle": "sarsp-1234567890abcdef12345678",
  "reconciliationHandle": "sarcr-abcdef1234567890abcdef12",
  "retryAllowed": false,
  "schema": "text-to-cad.agent-runtime-channel-reconciliation/1",
  "status": "terminal",
  "writeResponse": {
    "etag": null,
    "versionId": null
  }
}
```

`writeResponse` preserves exactly what the original caller received; both
fields may be null after a lost response. `observed.versionChain` is ordered
newest to oldest and covers every channel-state version from current latest
through the exact `before.versionId`, inclusive. Listing is used only to find
candidate VersionIds. Every non-delete version in the bounded chain is fetched
with an exact-version GET, parsed under the strict channel-state schema, and
hashed; a delete marker is recorded with JSON `null` for `digest` and `etag`.
An incomplete, over-limit, changing, or un-fetchable chain cannot be treated as
absence or success.

The outcome is exactly `promoted`, `not-promoted`, or `ambiguous`:

- `promoted`: exactly one exact-version object equals the intended canonical
  pointer digest and repeats its authorization digest and operation request ID,
  and that object is the sole latest version. That state is current authority.
  Execution additionally requires exact S3 and Mac visibility of those pointer
  bytes. If Mac visibility is false, the outcome is still `promoted`, but the
  current channel is execution- and pointer-change-blocked until a later
  read-only reconciliation proves visibility.
- `not-promoted`: no version equals the intended pointer, and the exact before
  VersionId/digest remains the sole latest version with no intervening data or
  delete-marker version. The prior current remains authoritative.
- `ambiguous`: every other result, including duplicate intended versions,
  intended-but-not-latest, a foreign later version, delete marker, incomplete
  chain, exact-GET failure, or inability to prove intended S3 bytes. The
  channel is frozen: neither current nor prior may execute, and no promotion or
  rollback may change the pointer.

The lost-response-after-successful-CAS case resolves to `promoted` when the
single intended version is latest even though `writeResponse` is null. The
confirmed-CAS-but-post-mount-failure case also resolves to `promoted`, with
`macIntendedBytesVisible: false`; current is authoritative but execution and
cleanup remain blocked. A proof that the before version is still latest and no
intended version exists resolves to `not-promoted`. Listing alone proves none
of these outcomes.

The reconciliation terminal record is published append-only at its handle and
reread exactly. If normal publication fails, an independent append-once
reconciliation tombstone binds the handle, original request ID, before digest,
intended pointer digest, last durable observation, and
`retentionRequired: true`. Failure of both leaves the channel frozen with no
authoritative reconciliation receipt. A later bounded reconciliation may use
a new handle and repeat only these reads. Pointer-changing promotion or
rollback may resume only after `not-promoted`, or after `promoted` with both
exact S3 and Mac visibility true. It remains frozen for `ambiguous` and for a
promoted-but-not-visible state. Reconciliation never mutates the pointer to
manufacture an outcome.

## Rollback is a new promotion

Rollback never mutates a lock, rewrites `current`, retags an image, or merely
chooses the predecessor at execution time. It starts a fresh operation whose
candidate is the exact `predecessorLockDigest` from the current channel state.

The rollback operation must:

1. reread and strictly validate current channel state and both locks;
2. prove the predecessor lock bytes, archive object VersionId/hash/size, OCI
   index/manifest/config/layers, outer importer, build/runtime/Cup identities,
   and Verified root are unchanged;
3. provision the predecessor archive afresh on the target host;
4. produce fresh loaded-identity and target deployment-conformance receipts;
5. publish a new promotion authorization; and
6. atomically promote the old predecessor to current with the old current as
   the new predecessor.

The immutable SAR-005 Verified root may be reused only because the bytes and
lock-bound identities are unchanged. Fresh host receipts prove this deployment
and do not mint a new artifact identity. A changed archive, lock, manifest,
config, runtime manifest, Cup manifest, build-input set, or verification root
is a new candidate, not a rollback.

The same CAS conflict and no-auto-retry rules apply. An operator who wants to
retry after any terminal failure creates a new operation handle, rereads
current state, and publishes new receipts. Receipts from the failed handle are
never edited or adopted.

## Retention, cleanup, and future garbage collection

At every valid non-bootstrap generation:

- the channel exposes exactly one current lock and one distinct immediate
  predecessor lock;
- both locks, their exact archive versions, SBOM/provenance inputs, Verified
  roots, provision/conformance receipts, and promotion history remain retained
  and resolvable;
- only current is executable; predecessor is rollback material until promoted;
- older channel versions and operation receipts remain immutable history; and
- no tag, local image inventory, directory listing, “latest” key, or Git ref
  has channel authority.

“Exactly current plus one predecessor” describes the active ready set, not a
license to erase history. Candidate images, failed attempts, restricted
diagnostics, and older immutable objects remain retained under the current
authorization. A future garbage collector requires a separate design,
implementation review, retention age/reference proof, dry run, and explicit
destructive authorization. It must never collect any object reachable from any
current/predecessor state or retained failure/verification/promotion receipt.

Successful local or target-host scratch cleanup is allowed only after the
terminal operation receipt, every immutable S3 object, exact S3 rereads, atomic
channel state, and Mac mount visibility are all verified. An initially
uncertain conditional write additionally requires a `promoted` reconciliation
whose exact S3 and Mac visibility checks are true. `not-promoted` and
`ambiguous` outcomes retain the original candidate and scratch; a `promoted`
outcome with failed visibility also retains them until a later read-only
reconciliation proves visibility. Cleanup is scoped to the operation handle
and must prove absence. Failed operations, unresolved reconciliation,
publication ambiguity, visibility failure, residue, or missing terminal
publication retain all scratch and record `cleanupDisposition` without
attempting broad cleanup.

## Failure and retry contract

Every stage emits one terminal operation record under its fresh handle with
status `succeeded`, `failed`, `reconciliation-required`, or
`publication-failed`. `reconciliation-required` binds the before state,
intended canonical pointer digest, promotion authorization, operation request
ID, and any returned write metadata; it grants no execution authority and
freezes pointer-changing operations. Failure precedence is:

1. retained or unproved owned resources;
2. absence-proof failure;
3. terminal/publication ambiguity;
4. cleanup failure;
5. interrupted operation; then
6. the first stage predicate in the state-machine order above.

All terminal records carry `retryAllowed: false`. This forbids automatic
re-entry or adoption, not a human-authorized new attempt. A new attempt gets a
new owner nonce and handle, validates immutable predecessor outputs again, and
may reuse an already-published content-addressed object only after exact
VersionId/hash/size reread. It cannot overwrite or amend an earlier receipt.

If both normal terminal publication and an independent append-once tombstone
fail, there is no authoritative terminal receipt. The supervisor retains
scratch and external operations must treat the attempt as unresolved.

## Exact invariants for implementation tests

An implementation is conformant only if machine tests prove all of these:

1. the canonical lock digest is the only artifact selected by channel state;
2. the lock closes archive, OCI index/manifest/config/ordered layers/platform,
   outer importer, build inputs, Codex, runtime/Cup manifests, SBOM, and exact
   Verified root;
3. archive and lock publication is create-only or exact-byte reuse by S3
   VersionId, size, and SHA-256;
4. provisioning uses the exact OCI image-layout archive and admitted importer,
   validates the portable-to-host-local identity mapping, and never pulls;
5. execution resolves current once, binds the fresh provision receipt,
   inspect-verifies its full host-local image ID/config immediately before
   start, and uses that local ID with `--pull=never`; tags, portable-manifest
   addressing, and predecessor fallback are rejected;
6. non-bootstrap channel state has one current and one distinct predecessor,
   and each resolves to a valid retained lock/archive/Verified root;
7. promotion is one conditional pointer write, a definite conflict cannot
   change the previous authoritative state, and an uncertain write freezes all
   pointer-changing and execution operations pending append-only reconciliation;
8. rollback is a new promotion with fresh provision, loaded-identity, and
   deployment-conformance receipts;
9. Source Snapshot identity is separate and cannot change the runtime lock;
10. pre-promotion Mac visibility of immutable candidate objects blocks CAS,
    while post-CAS pointer visibility blocks execution and triggers
    reconciliation; neither can create runtime evidence;
11. failed attempts never auto-retry, never adopt a handle, and retain
    diagnostics/resources unless exact absence is proved; and
12. reconciliation classifies exact version chains only as promoted,
    not-promoted, or ambiguous; never mutates the pointer; and cannot infer from
    a list response alone; and
13. cleanup and any future GC cannot act on names, tags, unresolved references,
    active current/predecessor objects, or historical failure evidence.

Required negative fixtures include archive/lock hash substitution, OCI index or
blob omission/re-encoding, manifest/config/layer substitution, unadmitted or
wrong-platform importer, S3 VersionId substitution, lock/Verified-root
cross-subject grafting, portable-manifest-as-local-ID confusion, host-local
image-ID/config substitution, stale or truncated local ID, execution-time
re-inspection drift, source-snapshot/runtime conflation, missing predecessor,
current equal to predecessor, stale-CAS promotion, lost response after a
successful CAS, confirmed CAS plus post-mount failure, duplicate intended
versions, intended-but-not-latest, delete markers, incomplete version chains,
listing-only inference, reconciliation pointer mutation, rollback before
reconciliation, crash before and after pointer write, Mac mount mismatch,
rollback without fresh target receipts, tag-only execution, registry fallback,
handle adoption, terminal-publication failure, cleanup residue, and GC
reachability mistakes.

## Implementation status

Nothing in this document publishes an archive, creates a lock, promotes a
channel, provisions a host, runs Colima/CVM, calls a provider, or proves an
Agent runtime. Follow-on implementation must add strict schemas and
canonicalizers, S3 versioning/precondition checks, immutable publication and
visibility receipts, canonical OCI image-layout producer, admitted outer
importer, lock/root validators, portable-to-host loaded-identity receipts,
execution inspect-before-start, deployment conformance, CAS promotion,
append-only reconciliation, rollback, failure fixtures, and provider-free
Colima/CVM evidence before any runtime can be called supplied or promoted.
