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
  writes a hash-and-size-attested `docker save` archive, and publishes a local
  prepare receipt.
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
| Agent image manifest digest | OCI linux/amd64 image manifest selected by `docker create` | Runtime image identity |
| Agent image config digest | OCI configuration object referenced by that manifest | Entrypoint, labels, rootfs configuration identity |
| Docker archive digest | SHA-256 of the exact `docker save` transport bytes | Transfer integrity only |
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
manifest/config, build/runtime/Cup identities, SBOM, and Verified root as one
closed statement. The `current` channel selects only an Agent Runtime Lock
digest; it never selects an archive, image tag, Git branch, or image ID
independently.

## Immutable S3 layout

The first release uses the already-authorized bucket and an isolated runtime
namespace:

```text
s3://arcwm-code-us-west-2/
  ericzyma/text-to-cad/runtime/agent/v1/
    archives/sha256/<archive-hex>.docker.tar
    locks/sha256/<lock-hex>.json
    sbom/sha256/<sbom-hex>.spdx.json
    provenance/sha256/<provenance-hex>.json
    verification/objects/sha256/<evidence-hex>.json
    verification/roots/sha256/<verified-root-hex>.json
    promotion-authorizations/sha256/<authorization-hex>.json
    channels/cup-formal/current.json
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
    "configDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "manifestDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "platform": {"architecture": "amd64", "os": "linux"}
  },
  "archive": {
    "bytes": 123456789,
    "format": "docker-archive",
    "s3": {
      "bucket": "arcwm-code-us-west-2",
      "key": "ericzyma/text-to-cad/runtime/agent/v1/archives/sha256/cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.docker.tar",
      "region": "us-west-2",
      "versionId": "illustrative-version-id"
    },
    "sha256": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  },
  "build": {
    "baseImageManifestDigest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "buildInputSetDigest": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "buildRecipeDigest": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "buildSourceSnapshotDigest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "builderImageManifestDigest": "sha256:2222222222222222222222222222222222222222222222222222222222222222"
  },
  "codex": {
    "archiveDigest": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
    "executableDigest": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
    "platform": "x86_64-unknown-linux-musl",
    "version": "0.147.0"
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

1. the lock's image, build, runtime, Cup, and verification values equal the
   Verified root subject and its referenced child subjects;
2. the Verified root object is fetched by exact digest and has status
   `verified` under its strict graph validator;
3. archive `sha256` equals both its content-addressed key suffix and the exact
   fetched bytes; `bytes` equals the fetched length;
4. `docker load` followed by OCI/Docker inspection yields exactly the locked
   image manifest, config, platform, runtime labels, and no additional selected
   image;
5. Codex and SBOM identities equal their corresponding admitted evidence; and
6. every field and nested field set is exact. Missing or additional fields
   close admission.

No local tag is recorded in the lock. A provisioner may create a nonce-scoped
temporary tag to make `docker save` or `docker load` practical, but it must
resolve that tag back to the locked manifest/config and remove it or mark its
residue in the terminal receipt. Execution uses the locked manifest digest
with `--pull=never`.

## Source Snapshot is a separate execution artifact

The build source snapshot in the lock explains how the image was built. The
execution Source Snapshot is different: it is mounted read-only for one Agent
Execution and may change without changing the Agent Runtime Lock.

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
| Offline Build | linux/amd64 image manifest/config and build provenance | Network must be disabled; an undeclared cache hit or fetch fails the build |
| Prepare | exact `docker-archive`, archive hash/size, SBOM, candidate lock bytes | Temporary tags resolve to the built manifest or preparation fails |
| Immutable S3 Publish | archive, lock, SBOM/provenance references, and exact S3 VersionIds | Create-only or exact-byte reuse; reread mismatch retains scratch |
| Provision | target-owned provision receipt for exact lock/archive | Fetch exact VersionId, `docker load`, no registry pull, no attempt adoption |
| Verify loaded identity | loaded-identity receipt for manifest/config/platform/runtime/Cup labels | A host-local ID or tag mismatch closes the attempt |
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
lock digest, archive S3 locator/hash/size, Docker server OS/architecture,
loaded manifest/config digests, temporary-reference disposition, workflow
source/file digests, and resource disposition. On failure it records the first
ordered `failureCheck`, `retryAllowed: false`, retained-resource disposition,
and any separately validated abort receipt.

`text-to-cad.agent-runtime-loaded-identity/1` is a smaller child receipt that
binds the successful provision receipt digest to exact inspected
manifest/config/platform/runtime-manifest/Cup-manifest values. The runtime lock
consumer recomputes every equality. A Docker image ID that happens to be
present on one host is not portable authority; the receipt proves how the
loaded host representation resolves to the OCI identities in the lock.

Promotion also requires a fresh
`text-to-cad.agent-runtime-deployment-conformance/1` for that target. It binds
the loaded-identity receipt, the unchanged Verified root, the exact harness,
and target-host lifecycle/Cup results. It is operational deployment evidence,
not a replacement or amendment of `Agent Runtime Verified`.

## Atomic promotion and channel state

Promotion first writes an immutable
`text-to-cad.agent-runtime-promotion-authorization/1`. It has exactly:

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

If compare-and-swap fails, the candidate is not current. The operation records
`promotion-conflict`, retains its evidence, and does not retry automatically.
If the S3 write succeeds but the returned version cannot be reread exactly,
execution remains prohibited and all scratch is retained for reconciliation.
No client may infer success from a candidate tag or authorization alone.

Mac mount visibility is a pre-promotion gate because operators need the
canonical archive, lock, and verification graph to be visible through the
configured mount. The gate rereads those immutable mounted
bytes and compares their digests after S3 publication but before the channel
CAS. It is operational evidence only: it does not prove image identity,
runtime behavior, S3 provenance, or host provisioning. A pre-promotion mount
failure blocks promotion but does not falsify a valid SAR-005 Verified root.
After CAS, mounted visibility of the new channel-state bytes is checked again
before execution and cleanup. Failure of that post-CAS check makes the channel
operationally unresolved; it does not silently restore the prior pointer.

## Rollback is a new promotion

Rollback never mutates a lock, rewrites `current`, retags an image, or merely
chooses the predecessor at execution time. It starts a fresh operation whose
candidate is the exact `predecessorLockDigest` from the current channel state.

The rollback operation must:

1. reread and strictly validate current channel state and both locks;
2. prove the predecessor lock bytes, archive object VersionId/hash/size, image
   manifest/config, build/runtime/Cup identities, and Verified root are
   unchanged;
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
channel state, and Mac mount visibility are all verified. Cleanup is scoped to
the operation handle and must prove absence. Failed operations, publication
ambiguity, visibility failure, residue, or missing terminal publication retain
all scratch and record `cleanupDisposition` without attempting broad cleanup.

## Failure and retry contract

Every stage emits one terminal operation record under its fresh handle with
status `succeeded`, `failed`, or `publication-failed`. Failure precedence is:

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
2. the lock closes archive, OCI manifest/config/platform, build inputs, Codex,
   runtime/Cup manifests, SBOM, and exact Verified root;
3. archive and lock publication is create-only or exact-byte reuse by S3
   VersionId, size, and SHA-256;
4. provisioning uses the exact archive version, validates loaded identity, and
   never pulls;
5. execution resolves current once, revalidates it, and uses the manifest with
   `--pull=never`; tags and predecessor fallback are rejected;
6. non-bootstrap channel state has one current and one distinct predecessor,
   and each resolves to a valid retained lock/archive/Verified root;
7. promotion is one conditional pointer write and a conflict cannot change the
   previous authoritative state;
8. rollback is a new promotion with fresh provision, loaded-identity, and
   deployment-conformance receipts;
9. Source Snapshot identity is separate and cannot change the runtime lock;
10. Mac visibility can block promotion but cannot create runtime evidence;
11. failed attempts never auto-retry, never adopt a handle, and retain
    diagnostics/resources unless exact absence is proved; and
12. cleanup and any future GC cannot act on names, tags, unresolved references,
    active current/predecessor objects, or historical failure evidence.

Required negative fixtures include archive/lock hash substitution, S3
VersionId substitution, lock/Verified-root cross-subject grafting, config vs.
manifest confusion, host-local image-ID substitution, source-snapshot/runtime
conflation, missing predecessor, current equal to predecessor, stale-CAS
promotion, crash before and after pointer write, Mac mount mismatch, rollback
without fresh target receipts, tag-only execution, registry fallback, handle
adoption, terminal-publication failure, cleanup residue, and GC reachability
mistakes.

## Implementation status

Nothing in this document publishes an archive, creates a lock, promotes a
channel, provisions a host, runs Colima/CVM, calls a provider, or proves an
Agent runtime. Follow-on implementation must add strict schemas and
canonicalizers, S3 versioning/precondition checks, immutable publication and
visibility receipts, lock/root validators, exact provision and loaded-identity
receipts, deployment conformance, CAS promotion, rollback, failure fixtures,
and provider-free Colima/CVM evidence before any runtime can be called supplied
or promoted.
