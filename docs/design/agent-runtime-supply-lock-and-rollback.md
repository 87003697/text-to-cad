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
    candidates/sha256/<candidate-hex>.json
    candidate-publications/sha256/<candidate-hex>.json
    locks/sha256/<lock-hex>.json
    sbom/sha256/<sbom-hex>.spdx.json
    provenance/sha256/<provenance-hex>.json
    verification/objects/sha256/<evidence-hex>.json
    verification/roots/sha256/<verified-root-hex>.json
    promotion-authorizations/sha256/<authorization-hex>.json
    channels/cup-formal/current.json
    reconciliations/by-request/sha256/<request-hex>/sha256/<receipt-hex>.json
    reconciliation-tombstones/by-request/sha256/<request-hex>/<reconciliation-handle>.json
    operations/<operation-handle>/stages/<ordinal>-<stage>.json
    operations/<operation-handle>/stage-outputs/sha256/<output-hex>.json
    operations/<operation-handle>/terminal.json
    operations/<operation-handle>/cleanup.json
    operation-tombstones/<operation-handle>.json
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

### Pre-verification candidate descriptor

The verification workflow cannot consume the final lock because the final lock
contains the Verified root produced by that workflow. It instead consumes a
canonical, immutable `text-to-cad.agent-runtime-candidate/1` descriptor. The
candidate has exactly the lock's `agentImage`, `archive`, `build`, `codex`,
`outerImporter`, `manifests`, and `sbom` values and has no `verification` value.
Its canonical digest is recorded by the candidate publication,
candidate-bound provision/loaded-identity, and Workflow A Verify Candidate
receipts. The exact-key SAR-005 root and child schemas are unchanged and never
gain a `candidateDigest` field.

Only the verification orchestrator and its target provisioner accept the
candidate. It is not a supply lock, channel value, downstream execution
authority, rollback target, or paid-pilot authority. The provisioner still
fetches exact immutable objects, uses the admitted importer, and produces the
portable-to-host-local identity proof required below.

After the strict graph is published, an outer cross-binding receipt checks the
exact overlap projection plus independent supply-only predicates described
below. The finalizer consumes that receipt, adds only the exact
`verification` object, and emits the canonical Agent Runtime Lock. Changing
any candidate field requires a new candidate, new environment evidence, and a
new Verified root. This is the only bootstrap path; final lock authority is not
weakened.

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

## Closed construction, verification, and promotion workflows

There is no monolithic operation that refers to a final lock before verification.
Two closed workflows meet at one immutable final-lock publication.

### Workflow A: candidate construction and artifact verification

A fresh `sarcv-<24 lowercase hex>` handle owns these ordered create-once stage
receipts:

| Stage | Required success output | Fail-closed rule |
| --- | --- | --- |
| Acquire | exact external/build input objects plus retrieval receipts | No mutable URL, tag, or ambient cache is admitted |
| Admit | closed build-input set, dependency and Codex admission | Any missing byte/hash/policy result stops before build |
| Offline Build | linux/amd64 OCI index/manifest/config/layers and provenance | Network is disabled; undeclared cache/fetch fails |
| Publish Build Objects | archive, SBOM and provenance exact S3 VersionIds after exact reread | No candidate exists before these locators are fixed |
| Construct Candidate | canonical candidate descriptor containing those exact object locators and every non-verification lock field | Any missing or changed locator/identity fails construction |
| Publish Candidate | create-once content-addressed candidate plus publication receipt and exact VersionId/hash/size reread | Listing, latest-key reads, or Mac path alone are not authority |
| Candidate Provision | candidate-bound provision receipt on each verification target | Purpose is exactly `artifact-verification`; no channel/general/paid execution |
| Verify Loaded Identity | candidate-bound loaded-identity receipt for each target | Missing/non-unique/truncated/tag-derived mapping fails |
| Verify Candidate | strict external cross-binding receipt for the candidate and exact 15-node SAR-005 root | Colima/CVM plan, overlap projection, or supply-only identity drift fails |
| Finalize Lock | add only the exact verification object, publish the content-addressed final lock, and exact-reread its VersionId/hash/size | Any recomputation or candidate/root mismatch fails |

The candidate publication receipt schema is
`text-to-cad.agent-runtime-candidate-publication/1`. It binds candidate
digest, bucket, region, content-addressed key, VersionId, byte count, SHA-256,
the archive/SBOM/provenance exact locators repeated inside the candidate, and
the create-only-or-exact-reuse reread result. Mac visibility is a separate
operational predicate and cannot replace exact-version reread.

The Verify Candidate stage output is exactly
`text-to-cad.agent-runtime-candidate-verification-binding/1`. It contains
`candidateDigest`, `candidatePublicationReceiptDigest`, `verifiedRootDigest`,
`verifiedSubjectDigest`, and closed predicates for every shared projection:
OCI manifest/config/platform, base/builder/build-input/recipe/project-runtime,
Codex, runtime/Cup manifests, and SBOM. The binding also requires the root's
strict graph validator, including its verification-plan identity, to pass;
that plan identity is not falsely presented as a candidate field. Separate predicates
verify candidate-only archive/index/layer locators, admitted outer importer,
and exact candidate-bound provision/loaded-identity receipt digests for Colima
and CVM. It derives these observations from strict documents; it does not add a
field to or amend the root, any child, or raw lifecycle/capability evidence.
Negative fixtures must reject a grafted root, candidate, publication receipt,
archive/importer identity, or environment provision receipt.

The finalizer does not rebuild, re-encode, import, or rerun verification. It
strictly rereads the candidate, Verified root, and external binding receipt,
recomputes every closed projection/predicate, adds only `verification`,
canonicalizes the final lock, publishes
it create-once, and exact-rereads the returned/reused VersionId. Only this final
lock may become a channel value or general execution authority.

### Workflow B: final-lock promotion or rollback

A fresh `sarsp-<24 lowercase hex>` handle owns these ordered create-once stage
receipts:

| Stage | Required success output | Fail-closed rule |
| --- | --- | --- |
| Resolve Final Lock | exact final-lock VersionId/hash/size plus candidate and Verified-root closure | Candidate alone is rejected |
| Provision Final Lock | fresh target-owned lock-bound provision receipt | Exact archive/importer only; no registry pull or attempt adoption |
| Verify Loaded Identity | fresh lock-bound portable-to-host identity receipt | Any local mapping drift fails |
| Consume Verified Root | strict validation of the exact lock-linked root | Missing, non-verified or cross-subject root fails |
| Release Qualification | fresh target lifecycle/Cup conformance plus required first-release concurrency receipt | Does not rewrite the Verified root |
| Atomic Promote | one conditional versioned channel-state write | Conflict retains prior current; uncertain write requires reconciliation |

Rollback uses the same Workflow B with a previous final lock and entirely fresh
target provision, identity and release-qualification receipts. Downstream
execution is a separate consumer that resolves the authoritative channel,
validates the final lock and its successful promotion terminal or supplied
reconciliation receipt, then uses the freshly inspected full host-local ID with
`--pull=never`.

Both workflows use the common stage envelope
`text-to-cad.agent-runtime-supply-stage/1`. Status is exactly
`succeeded|failed|not-run|uncertain`; `uncertain` is allowed only for the
possibly applied Atomic Promote write. Every later stage after a non-success is
a deterministic `not-run` bound to the first non-succeeded predecessor.
Exactly one create-once terminal closes all ordered stage receipts for its
workflow. Stage, cleanup, reconciliation and tombstone documents are not
operation terminals. Terminal publication failure cannot self-report and uses
the independent tombstone already defined below.

## Provision and loaded-identity receipts

`text-to-cad.agent-runtime-provision/1` is terminal and exact. On success it
binds operation handle/owner digest, target environment and host fingerprint,
`subjectKind: candidate|lock`, the matching canonical `subjectDigest`, archive
S3 locator/hash/size, Docker server OS/architecture, exact
outer-importer admission and selected platform-artifact digests, portable OCI
index/manifest/config/ordered-layer observations, the full 64-hex host-local
loaded image ID, its independently inspected local config mapping,
temporary-reference disposition, workflow source/file digests, and resource
disposition. On failure it records the first ordered `failureCheck`,
`retryAllowed: false`, retained-resource disposition, and any separately
validated abort receipt.

`text-to-cad.agent-runtime-loaded-identity/1` is a smaller child receipt that
binds the same subject kind/digest and successful provision receipt digest to
exact inspected host-local
image ID/config/platform/runtime-manifest/Cup-manifest values. The runtime lock
or verification candidate consumer recomputes every equality. A candidate
subject is valid only for `artifact-verification`; a lock subject is required
for promotion, rollback and downstream execution. A Docker image ID that happens to be
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
    "etag": "illustrative-prior-etag",
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

For bootstrap only, `before.currentLockDigest`, `before.etag`,
`before.pointerDigest`, `before.versionId`, and
`after.predecessorLockDigest` are JSON `null`, `before.generation` is 0, and
`after.generation` is 1. The publisher creates
`channels/cup-formal/current.json` with `If-None-Match: *`. It does not invent a
prior VersionId or ETag.

For every later generation, the authorization binds the exact previously
reread current `versionId`, ETag, content digest, current lock, and generation
as evidence. `after.generation = before.generation + 1`,
`after.predecessorLockDigest = before.currentLockDigest`, and current and
predecessor are distinct. The publisher replaces the key with `If-Match` on
the exact prior ETag. S3 `PutObject` is not claimed to condition on VersionId;
VersionId and digest are independently bound evidence that the ETag came from
the intended prior bytes.

Both paths write exactly these channel-state bytes:

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
On a successful response, the publisher records the returned VersionId and
ETag, performs an exact-version GET, and requires byte-for-byte equality with
the intended canonical pointer before it may write a succeeded operation
terminal. The immutable authorization proves the gates; the channel-state
version proves which lock actually became current. There is no second mutable
alias whose update could split authority.

If bootstrap definitively fails `If-None-Match: *`, the key already exists; if
a later generation definitively fails `If-Match`, the prior ETag is stale. In
either definite precondition-failure case this request did not promote the
promotion target lock. The operation records `promotion-conflict`, retains its evidence,
does not retry automatically, and validates the independently existing current
pointer before any downstream use. A lost write response, an unreadable
returned VersionId, a failed exact S3 reread, or a failed post-CAS Mac reread
enters reconciliation instead. No client may infer success from an image tag,
intended final-lock request, authorization, listing, or cached pointer.

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

Its canonical receipt digest is not embedded in the receipt. It is published
create-once under the deterministic namespace formed from the original
`operationRequestId` and that receipt digest. A caller selects reconciliation
only by supplying that exact digest; no consumer selects a receipt by handle,
prefix, timestamp, or listing.

The terminal `text-to-cad.agent-runtime-channel-reconciliation/1` receipt has
exactly this shape:

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
  "resolvedCurrent": {
    "digest": "sha256:2525252525252525252525252525252525252525252525252525252525252525",
    "etag": "illustrative-intended-etag",
    "versionId": "illustrative-intended-version"
  },
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
channel-state VersionIds. Every non-delete version in the bounded chain is fetched
with an exact-version GET, parsed under the strict channel-state schema, and
hashed; a delete marker is recorded with JSON `null` for `digest` and `etag`.
An incomplete, over-limit, changing, or un-fetchable chain cannot be treated as
absence or success. `resolvedCurrent` equals `observed.latest` for a resolved
`promoted` or `not-promoted` outcome; it is JSON `null` when no exact latest
object can be established. For `promoted`, both must equal the intended pointer
digest and exact VersionId.

For bootstrap reconciliation the three `before` identity fields are null and
the bounded chain covers every observed version of the channel key. `promoted`
then requires exactly one intended version and no other data or delete-marker
version. `not-promoted` requires that the key remains absent, with
`resolvedCurrent: null`; the channel remains uninitialized and grants no
authority. Any existing non-intended version is `ambiguous` for that bootstrap
request and must instead be consumed under its own originating authority.

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
  delete-marker version. The prior current remains authoritative under its own
  originating operation authority; this receipt never authorizes or unfreezes
  the intended final-lock pointer. The bootstrap absence case follows the rule above.
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

The reconciliation receipt is published create-once at
`reconciliations/by-request/sha256/<request-hex>/sha256/<receipt-hex>.json` and
reread exactly. If normal publication fails, an independent append-once
reconciliation tombstone binds the handle, original request ID, before digest,
intended pointer digest, last durable observation, and
`retentionRequired: true`. Failure of both leaves the intended operation frozen
with no authoritative reconciliation receipt. A later bounded reconciliation
may use a new handle and repeat only these reads. `ambiguous`, `not-promoted`,
and promoted-but-not-visible receipts never grant authority to the intended
pointer. Reconciliation never mutates the pointer to manufacture an outcome.

### Deterministic consumption and unfreeze

Every downstream execution, promotion, or rollback validates the operation
that wrote the exact current pointer. The admission input binds the current
pointer VersionId and digest and has `reconciliationReceiptDigest`, which is
JSON `null` on the normal path. The consumer then applies exactly one rule:

1. Fetch `operations/<operationHandle>/terminal.json` at its deterministic
   path. If it exists, strictly validates, has `status: succeeded`, and its
   `06-atomic-promote` stage binds the exact current pointer digest and
   VersionId, the consumer requires `reconciliationReceiptDigest: null`.
2. If that operation terminal is missing or has
   `status: reconciliation-required`, the caller must supply one exact
   reconciliation receipt digest. The consumer derives the content-addressed
   key from the pointer's `operationRequestId` and the supplied digest, fetches
   those exact bytes without listing, recomputes the digest, and rejects any
   other path or object.
3. That receipt must bind the pointer's operation handle/request ID, original
   promotion authorization, intended canonical pointer bytes and digest,
   before digest/VersionId/ETag, exact observed latest digest/VersionId/ETag,
   and the current pointer digest/VersionId. It must have `outcome: promoted`
   and all of `exactVersionGetsComplete`, `s3IntendedBytesVisible`, and
   `macIntendedBytesVisible` true.
4. Independently of the receipt, the consumer exact-version GETs the supplied
   current VersionId and hashes its bytes, then fetches current latest without
   using a listing-selected channel version. Both reads must return those same exact
   canonical pointer bytes and identify that VersionId as current latest.

Only those two success paths grant authority. A failed original terminal, a
missing caller-supplied digest, or an `ambiguous`, `not-promoted`, or
promoted-but-not-visible receipt never unfreezes the intended pointer. Multiple
receipts for one request are harmless because no receipt is auto-selected: a
caller supplies one digest, and it grants authority only if it fully validates
the exact current pointer under the rule above. The prior pointer after a
`not-promoted` attempt is evaluated through its own originating succeeded
terminal or its own qualifying resolution, never through the failed attempt's
receipt.

This also closes CAS-success-before-terminal-publication. If the conditional
write created the current pointer but the operation terminal was never
published, a supplied exact `promoted` reconciliation receipt with both
visibility checks true acts as the immutable terminal resolution for that
original operation. The consumer still rereads current independently. Neither
the missing operation terminal nor the pointer is created, amended, or
backfilled during reconciliation.

## Rollback is a new promotion

Rollback never mutates a lock, rewrites `current`, retags an image, or merely
chooses the predecessor at execution time. It starts a fresh operation whose
promotion target lock is the exact `predecessorLockDigest` from current state.

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
creates a new runtime artifact and final lock; it is not a rollback.

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
`ambiguous` outcomes retain the intended final-lock operation and scratch; a `promoted`
outcome with failed visibility also retains them until a later read-only
reconciliation proves visibility. Cleanup is scoped to the operation handle
and must prove absence. Failed operations, unresolved reconciliation,
publication ambiguity, visibility failure, residue, or missing terminal
publication retain all scratch; any attempted post-terminal cleanup records its
disposition only in `cleanup.json` without attempting broad cleanup.

Post-terminal cleanup writes at most one create-once
`operations/<handle>/cleanup.json`. It is an operational cleanup receipt, not a
stage receipt and not a second operation terminal. It binds the immutable
operation terminal digest, any required qualifying reconciliation digest,
exact removed handle-owned paths/references, and absence observations. Failure
or absence uncertainty is recorded there and retains remaining scratch; it
never edits the operation terminal.

## Failure and retry contract

Each stage emits exactly one immutable stage receipt at its deterministic path.
After all receipts for the selected workflow exist (ten for Workflow A, six
for Workflow B), the supervisor attempts exactly one
create-once immutable operation terminal; the path can therefore contain at
most one. Its status is exactly `succeeded`, `failed`, or
`reconciliation-required`. The latter requires an `uncertain`
`06-atomic-promote` receipt and binds the before state, intended canonical
pointer digest, promotion authorization, operation request ID, and any returned
write metadata; it grants no downstream authority by itself.

The operation terminal derives its status, ordered stage digest list, and
failure check from the stage receipts. For a failed operation, precedence is:

1. retained or unproved owned resources;
2. absence-proof failure;
3. interrupted operation; then
4. the first failed stage predicate in state-machine order.

Terminal-publication failure cannot appear as a status inside the terminal
that failed to publish. In that case there is no operation terminal; the
independent append-once operation tombstone records the attempted terminal
digest, last durable stage digest, original request ID, and retention required.
Post-terminal cleanup success or failure appears only in `cleanup.json` and
never changes terminal status or failure precedence.

All stage receipts and operation terminals carry `retryAllowed: false`. This
forbids automatic
re-entry or adoption, not a human-authorized new attempt. A new attempt gets a
new owner nonce and handle, validates immutable predecessor outputs again, and
may reuse an already-published content-addressed object only after exact
VersionId/hash/size reread. It cannot overwrite or amend an earlier receipt.

If both normal terminal publication and the independent append-once operation
tombstone fail, there is no authoritative operation terminal or tombstone. The
supervisor retains scratch and external operations must treat the attempt as
unresolved. If the pointer write may have succeeded, only the exact
reconciliation consumption rule above can later authorize that current
pointer.

## Exact invariants for implementation tests

An implementation is conformant only if machine tests prove all of these:

1. the canonical lock digest is the only artifact selected by channel state;
2. the lock closes archive, OCI index/manifest/config/ordered layers/platform,
   outer importer, build inputs, Codex, runtime/Cup manifests, SBOM, and exact
   Verified root;
3. archive/SBOM/provenance publication precedes candidate construction;
   candidate and final-lock publication are separately create-only or
   exact-byte reuse by S3 VersionId, size, and SHA-256;
4. provisioning uses the exact OCI image-layout archive and admitted importer,
   validates the portable-to-host-local identity mapping, and never pulls;
5. execution resolves current once, binds the fresh provision receipt,
   inspect-verifies its full host-local image ID/config immediately before
   start, and uses that local ID with `--pull=never`; tags, portable-manifest
   addressing, and predecessor fallback are rejected;
6. non-bootstrap channel state has one current and one distinct predecessor,
   and each resolves to a valid retained lock/archive/Verified root;
7. bootstrap promotion uses create-only `If-None-Match: *`; later promotion
   uses `If-Match` only on the exact prior ETag while binding prior VersionId
   and digest as evidence; both exact-version reread successful bytes;
8. rollback is a new promotion with fresh provision, loaded-identity, and
   deployment-conformance receipts;
9. Source Snapshot identity is separate and cannot change the runtime lock;
10. pre-promotion Mac visibility of immutable promotion-target objects blocks CAS,
    while post-CAS pointer visibility blocks execution and triggers
    reconciliation; neither can create runtime evidence;
11. failed attempts never auto-retry, never adopt a handle, and retain
    diagnostics/resources unless exact absence is proved; and
12. reconciliation classifies exact version chains only as promoted,
    not-promoted, or ambiguous; never mutates the pointer; and cannot infer from
    a list response alone;
13. every current-pointer consumer validates the originating succeeded
    operation terminal or one caller-supplied exact content-addressed promoted
    reconciliation receipt with both visibility checks true, then independently
    rereads exact current bytes;
14. each resolved Workflow A operation has exactly ten immutable stage
    receipts, each resolved Workflow B operation has exactly six, and each has
    one immutable operation terminal; only terminal-publication failure leaves no
    terminal, and stage receipts, cleanup receipts, and tombstones are never
    additional terminals; and
15. cleanup and any future GC cannot act on names, tags, unresolved references,
    active current/predecessor objects, or historical failure evidence.

Required negative fixtures include archive/lock hash substitution, OCI index or
blob omission/re-encoding, manifest/config/layer substitution, unadmitted or
wrong-platform importer, S3 VersionId substitution, lock/Verified-root
cross-subject grafting, portable-manifest-as-local-ID confusion, host-local
image-ID/config substitution, stale or truncated local ID, execution-time
re-inspection drift, source-snapshot/runtime conflation, missing predecessor,
current equal to predecessor, stale-CAS promotion, lost response after a
successful CAS, bootstrap create against an existing key, treating VersionId as
a PUT precondition, CAS success before operation-terminal publication,
confirmed CAS plus post-mount failure, duplicate intended versions,
intended-but-not-latest, delete markers, incomplete version chains,
listing-selected reconciliation, missing or wrong caller-supplied resolution
digest, a valid receipt for a different current version, reconciliation pointer
mutation, rollback before reconciliation, missing/extra/reordered stage
receipts, a stage receipt presented as an operation terminal, duplicate
operation terminals, terminal-publication failure, crash before and after
pointer write, Mac mount mismatch, rollback without fresh target receipts,
tag-only execution, registry fallback, handle adoption, cleanup residue, and
GC reachability mistakes.

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
