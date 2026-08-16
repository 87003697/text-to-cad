# Agent Runtime Verification receipt contract

Status: decision for SAR-005; implementation and evidence are not present

This document defines the only receipt that may promote one exact Agent Runtime
Artifact to **Agent Runtime Verified**. It does not promote a source branch, a
tag, a host installation, or a pilot result.

## Decision and authority

The root schema is `text-to-cad.agent-runtime-verification/1`. A strict
consumer accepts `status: "verified"` only when the complete content-addressed
evidence graph described below is available and valid. Producers cannot omit a
node, add an extension field, weaken a predicate, substitute a tag for a
digest, or turn a failed attempt into a retry.

This contract specializes existing repository seams instead of replacing
them:

- The Agent lifecycle identity and absence vocabulary comes from the reviewed
  SAR-003 decision at commit
  `22d2a35dc1c852dbca3fd19c48fc55a504cb496c`: its
  [`contract.py`](https://github.com/87003697/text-to-cad/blob/22d2a35dc1c852dbca3fd19c48fc55a504cb496c/packages/meshshot/prototypes/agent_runtime_boundary/contract.py),
  [`boundary.py`](https://github.com/87003697/text-to-cad/blob/22d2a35dc1c852dbca3fd19c48fc55a504cb496c/packages/meshshot/prototypes/agent_runtime_boundary/boundary.py),
  and
  [`README.md`](https://github.com/87003697/text-to-cad/blob/22d2a35dc1c852dbca3fd19c48fc55a504cb496c/packages/meshshot/prototypes/agent_runtime_boundary/README.md).
  Those paths are decision evidence on that commit and are deliberately not
  present on this document's fixed base.
- The Browser Sidecar proof-only receipt, exact identity, terminal cleanup, and
  nested-gate split are specified in
  [the formal-pilot integration spec](../specs/browser-sidecar-formal-pilot-integration.md)
  and enforced by
  [`runner.py`](../../scripts/pilot/runner.py),
  [`browser_sidecar.py`](../../scripts/pilot/browser_sidecar.py), and
  [`browser_sidecar_conformance.py`](../../scripts/pilot/browser_sidecar_conformance.py).
- Registered Browser programs and artifact identities remain fixed by
  [`browser_contract.json`](../../packages/meshshot/src/meshshot/browser_contract.json)
  and
  [`image-lock.json`](../../packages/meshshot/browser_sidecar_broker/image-lock.json).
- CVM terminal publication uses a manifest-before-report transaction and
  retains failed scratch in
  [`cvm_agent.py`](../../scripts/pilot/cvm_agent.py). CVM image provisioning
  already rejects duplicate JSON keys and hashes canonical request bytes in
  [`cvm_sidecar_probe.py`](../../scripts/pilot/cvm_sidecar_probe.py).

These sources are implementation precedents, not evidence that this new schema
has been emitted.

## Exact root subject

The root has exactly six keys, in the following semantic shape:

| Key | Exact requirement |
| --- | --- |
| `schema` | Literal `text-to-cad.agent-runtime-verification/1` |
| `status` | `verified` or `failed` |
| `subject` | Exact object below |
| `graph` | Exact closed child-reference object below |
| `failureCheck` | `null` for `verified`; otherwise one closed check from this document |
| `retryAllowed` | Literal `false` |

`subject` has exactly these keys:

| Key | Meaning |
| --- | --- |
| `agentImageManifestDigest` | Full OCI manifest digest of the promoted `linux/amd64` Agent image |
| `agentImageConfigDigest` | Full OCI image-config digest observed independently from the manifest |
| `platform` | Exactly `{"architecture":"amd64","os":"linux"}` |
| `runtimeManifestDigest` | Digest of the image-resident sealed runtime manifest |
| `cupRuntimeCapabilityManifestDigest` | Digest of the closed first-release Cup capability manifest |
| `buildInputSetDigest` | Digest of the closed offline build-input set: recipe, base, dependency locks, and project runtime artifacts |

Every digest is exactly `sha256:` plus 64 lowercase hexadecimal characters.
`buildInputSetDigest` is an artifact-construction identity, not a Source
Snapshot. A **Source Snapshot** is the separately identified project source
mounted read-only for one Agent Execution. Changing an execution Source
Snapshot or its revision does not rebuild or rename the sealed runtime. Each
environment-qualified verification execution binds its own Source Snapshot
below; none is part of the artifact root subject.

The digest of canonical `subject` is `subjectDigest`. Every child repeats that
digest, not the subject fields, so a child from another artifact, configuration,
capability manifest, or build-input lock cannot be grafted into the graph.

## Closed Merkle-style graph

`graph` has exactly:

```json
{
  "algorithm": "sha256-canonical-json-v1",
  "children": [],
  "subjectDigest": "sha256:<64 lowercase hex>"
}
```

`children` contains exactly the fourteen role/environment references in the
table below, sorted first by `kind` and then by `environment` (`null` sorts
before strings). Each reference has exactly `kind`, `environment`, and
`digest`. No pair may repeat and no digest may repeat. An environment-neutral
node uses JSON `null`; environment-qualified nodes use only `colima` or `cvm`.

| `kind` | `environment` | Required direct dependencies |
| --- | --- | --- |
| `agent-lifecycle` | `colima` | `image-identity`, `browser-deny`, matching `source-snapshot` |
| `agent-lifecycle` | `cvm` | `image-identity`, `browser-deny`, matching `source-snapshot` |
| `browser-deny` | `null` | `image-identity` |
| `build-input-set` | `null` | none |
| `build-provenance` | `null` | `build-input-set` |
| `capability-conformance` | `colima` | matching `agent-lifecycle`, `cup-golden` |
| `capability-conformance` | `cvm` | matching `agent-lifecycle`, `cup-golden` |
| `codex-admission` | `null` | `dependency-admission` |
| `cup-golden` | `null` | `image-identity` |
| `dependency-admission` | `null` | `build-input-set` |
| `image-identity` | `null` | `build-provenance`, `sbom` |
| `sbom` | `null` | `build-provenance` |
| `source-snapshot` | `colima` | none |
| `source-snapshot` | `cvm` | none |

Each referenced document has exactly:

```json
{
  "blockedBy": null,
  "dependsOn": [],
  "environment": null,
  "failureCheck": null,
  "kind": "<closed kind>",
  "predicates": {},
  "retryAllowed": false,
  "schema": "text-to-cad.agent-runtime-evidence/1",
  "status": "succeeded",
  "subject": {},
  "subjectDigest": "sha256:<subject digest>"
}
```

Its `status` is exactly `succeeded`, `failed`, or `not-run`. `subject` is the
exact kind-specific observation object defined below. It carries the immutable
digests and closed scalar results to which the predicates refer; a producer
cannot self-attest with an unbound boolean map.

`dependsOn` is the sorted list of the direct child document digests required by
the table. A node's own digest is the SHA-256 of its canonical document. The
root lists every node, not only leaves. A graph is closed only if all fourteen
documents exist, every reference digest matches bytes, every `dependsOn`
reference resolves within those fourteen documents, all nodes share the root
`subjectDigest`, dependencies match the table exactly, and the graph is
acyclic. Unreachable, duplicate, additional, or externally referenced nodes
are rejection conditions.

`blockedBy` is `null` for `succeeded` and `failed`. For `not-run`, it is exactly
one child reference object (`kind`, `environment`, `digest`) already present in
`dependsOn`. It is the first non-succeeded direct dependency in the dependency
order printed in the table (ties between matching environments use `colima`
before `cvm`). This is a reference to an already hashed child, not a digest of
the node being written, so it creates no logical self-reference.

The exact `subject` keys are:

| Node | Exact subject fields |
| --- | --- |
| `build-input-set` | `buildInputSetDigest`, `buildRecipeDigest`, `baseImageManifestDigest`, `ubuntuSnapshotManifestDigest`, `dependencyLockDigest`, `projectRuntimeArtifactSetDigest` |
| `build-provenance` | `buildInputSetDigest`, `builderImageManifestDigest`, `buildRecipeDigest`, `baseImageManifestDigest`, `outputImageManifestDigest`, `outputImageConfigDigest` |
| `dependency-admission` | `buildInputSetDigest`, `dependencyLockDigest`, `aptClosureManifestDigest`, `pythonWheelManifestDigest`, `nodeArtifactDigest`, `implicitBundleDigest` |
| `codex-admission` | `codexVersion` (literal `0.147.0`), `platform` (literal `x86_64-unknown-linux-musl`), `retrievalReceiptDigest`, `archiveDigest`, `executableDigest`, `elfClosureDigest` |
| `sbom` | `agentImageManifestDigest`, `sbomDigest`, `format` (literal `spdx-json-2.3`) |
| `image-identity` | `agentImageManifestDigest`, `agentImageConfigDigest`, `runtimeManifestDigest`, `cupRuntimeCapabilityManifestDigest`, `platform` |
| `browser-deny` | `agentImageManifestDigest`, `scannerDigest`, `inventoryDigest`, `browserFindingCount`, `chromiumProcessCount` |
| `cup-golden` | `agentImageManifestDigest`, `fixtureDigest`, `routerManifestDigest`, `expectedOutputDigest`, `observedOutputDigest`, `faceCount`, `watertight`, `eulerNumber` |
| `source-snapshot` | `executionSourceSnapshotDigest`, `sourceManifestDigest`, `pathCount`, `totalBytes` |
| `agent-lifecycle` | `agentImageManifestDigest`, `agentImageConfigDigest`, `runtimeManifestDigest`, `executionSourceSnapshotDigest`, `inputSnapshotDigest`, `agentConfigDigest`, `brokerAuthorityDigest`, `workloadDigest`, `resourceDisposition`, `cleanupDisposition` |
| `capability-conformance` | `agentImageManifestDigest`, `runtimeManifestDigest`, `cupRuntimeCapabilityManifestDigest`, `executionSourceSnapshotDigest`, `inputSnapshotDigest`, `conformanceFixtureDigest`, `observedOutputDigest` |

All non-null fields ending in `Digest` use the full digest grammar. `pathCount`,
`totalBytes`, `browserFindingCount`, `chromiumProcessCount`, `faceCount`, and
`eulerNumber` are signed 64-bit integers; counts are nonnegative.
`watertight` is Boolean. `platform` in image evidence is the exact root
platform object. Each child field that names a root identity must equal the
root value; each dependent field must equal the corresponding dependency's
subject value.

`resourceDisposition` has exactly `agentContainer`, `ownerLabels`,
`brokerVolume`, `jobPrivateTree`, and `workloadProcessGroup`; every value is
`absent`, `retained`, or `unproved`. `cleanupDisposition` has exactly
`agentContainer`, `brokerVolume`, and `jobPrivateTree`; every value is
`succeeded`, `failed`, or `not-required`. These closed observations distinguish
positive residue from failure to prove absence without exposing IDs or paths.

## Closed predicates

The following arrays are normative order. A node's `predicates` object has
exactly its listed keys. Successful nodes contain literal `true` for every key.
Failed nodes contain `true` for completed earlier checks, literal `false` for
the one selected `failureCheck`, and `null` for checks not established after
that failure. For a failed node, `failureCheck` is exactly that one false
predicate key; aliases and arbitrary error strings are forbidden. A `not-run`
node has every predicate `null`, `failureCheck: "dependency-failed"`, and the
deterministic `blockedBy` reference defined above. Booleans are not integers
and no truthy substitute is accepted.

### Artifact admission nodes

| Node | Predicate keys, in order |
| --- | --- |
| `build-input-set` | `manifestSchemaExact`, `recipeBound`, `baseManifestBound`, `ubuntuSnapshotBound`, `dependencyLockBound`, `projectRuntimeArtifactsBound`, `pathSetClosed`, `fileDigestsBound`, `immutableObjectVisible` |
| `build-provenance` | `builderIdentityExact`, `buildRecipeDigestExact`, `baseManifestDigestExact`, `platformLinuxAmd64`, `buildInputSetBound`, `networkDisabled`, `pullDisabled`, `cleanContextAllowlisted`, `outputManifestDigestExact`, `outputConfigDigestExact` |
| `dependency-admission` | `ubuntuSnapshotPinned`, `ubuntuMetadataAuthenticated`, `debClosureComplete`, `pythonWheelClosureComplete`, `nativeMeshscopeWheelAdmitted`, `browserFreeMeshshotWheelAdmitted`, `nodeArtifactAdmitted`, `canonicalImplicitBundleClosed`, `runtimeFilesByteLocked`, `offlineRebuildSucceeded` |
| `codex-admission` | `versionExact`, `platformArtifactExact`, `retrievalMetadataRecorded`, `archiveDigestExact`, `executableDigestExact`, `elfClosureClosed`, `nodeAbsentSmokePassed`, `noninteractiveSmokePassed`, `immutableMirrorVisible`, `publisherSignatureClaimAbsent` |
| `sbom` | `formatExact`, `subjectManifestDigestExact`, `allRuntimeFilesCovered`, `packageVersionsExact`, `nativeLibrariesCovered`, `licensesRecorded`, `sbomDigestBound` |
| `image-identity` | `immutableReferenceExact`, `manifestDigestObserved`, `configDigestObserved`, `runtimeManifestInsideImageExact`, `cupManifestInsideImageExact`, `osLinux`, `architectureAmd64`, `entrypointExact`, `userNonRoot`, `noMutableTagAuthority` |
| `browser-deny` | `packageInventoryEmpty`, `executableInventoryEmpty`, `cacheInventoryEmpty`, `elfMarkerInventoryEmpty`, `productMarkerInventoryEmpty`, `playwrightInventoryEmpty`, `chromiumProcessZero`, `browserLifecycleAuthorityAbsent` |
| `cup-golden` | `fixtureDigestExact`, `formalRouterImplicitOnly`, `faceCount3764`, `watertightFalse`, `eulerNumber144`, `nodeImplicitSubsetExact`, `meshscopeAccepted`, `voxBlameAccepted`, `residualBrokerPreviewAccepted`, `outputDigestRepeatable` |
| `source-snapshot` | `manifestSchemaExact`, `pathSetClosed`, `regularFilesOnly`, `fileModesBound`, `fileSizesBound`, `fileDigestsBound`, `treeDigestMatchesObservation`, `readOnlyMountEligible` |

`publisherSignatureClaimAbsent` is intentionally positive only when the receipt
does **not** claim upstream publisher signing for the Codex executable. Hash and
retrieval provenance are admissible; an unavailable publisher signature cannot
be synthesized.

### Environment-qualified nodes

`agent-lifecycle` in both `colima` and `cvm` uses the reviewed SAR-003 lifecycle
vocabulary and order:

1. `adapterOperationsClosed`
2. `authorityFresh`
3. `jobPrivateLayoutExact`
4. `snapshotIdentityExact`
5. `workloadIdentityExact`
6. `imageIdentityOuterAttested`
7. `returnedContainerIdExact`
8. `containerOwnershipExact`
9. `inertContainerConfigExact`
10. `readOnlyRoot`
11. `sourceReadOnly`
12. `inputReadOnly`
13. `writableMountAllowlistExact`
14. `dockerSocketAbsent`
15. `capabilitiesEmpty`
16. `noNewPrivileges`
17. `externalNetworkAbsent`
18. `entrypointPreflightExact`
19. `brokerProofIdentityBound`
20. `workloadReleasedOnce`
21. `terminalPublicationExact`
22. `workloadProcessGroupAbsent`
23. `descendantResidueFalse`
24. `workloadNotInterrupted`
25. `workloadTerminalZero`
26. `containerCleanupSucceeded`
27. `brokerVolumeCleanupSucceeded`
28. `jobPrivateTreeCleanupSucceeded`
29. `agentContainerAbsent`
30. `ownerLabelsAbsent`
31. `brokerVolumeAbsent`
32. `jobPrivateTreeAbsent`

The four resource-absence predicates are independent. Labels are inventory
evidence and never deletion authority. Only the exact returned and
owner-attested container ID authorizes removal. A positive observation of any
owned residue maps to `retained-resource`; inability to establish all required
absence maps to `absence-proof`.

`capability-conformance` in both environments has exactly:

1. `runtimeManifestBound`
2. `cupManifestBound`
3. `sourceSnapshotBound`
4. `inputSnapshotBound`
5. `codexExecutableExact`
6. `python312Exact`
7. `nodeRuntimeExact`
8. `canonicalImplicitSubsetExact`
9. `meshscopeCallable`
10. `voxBlameCallable`
11. `brokerAuthorityJobPrivate`
12. `residualPublicParity`
13. `browserInventoryEmpty`
14. `browserProcessZero`
15. `dockerAuthorityAbsent`
16. `credentialSurfaceEmpty`
17. `providerNetworkDenied`
18. `cupGoldenAccepted`
19. `outputDigestBound`
20. `terminalAndCleanupBound`

The conformance job is provider-free. Its Source Snapshot is the exact verifier
fixture snapshot bound in the lifecycle identity; that execution snapshot does
not enter or replace the artifact `buildInputSetDigest` in the root subject.

### Exact child state machine and cascade

The node state transition is one-way: `pending` (never published) to exactly one
published terminal state. There is no published `pending` child.

| Status | `blockedBy` | `failureCheck` | Predicates | Execution rule |
| --- | --- | --- | --- | --- |
| `succeeded` | `null` | `null` | all `true` | All direct dependencies succeeded; producer executed every check |
| `failed` | `null` | exactly the selected false predicate key | earlier `true`, selected `false`, later `null` | All direct dependencies succeeded; producer attempted this node |
| `not-run` | first non-succeeded direct dependency reference | literal `dependency-failed` | all `null` | Producer must not execute this node |

For `failed` and `not-run`, request-bound identity fields in `subject` remain
exact. A field copied from the root, a direct dependency, or the immutable
attempt request is request-bound and never `null`. Observations are bound as
follows: browser scan predicates bind `inventoryDigest` and both counts; Cup
predicates bind `observedOutputDigest` and its three metrics; Source Snapshot
closure binds its manifest digest and counts; lifecycle cleanup/absence binds
the two disposition objects; conformance output binds
`observedOutputDigest`. An observation is `null` exactly when its establishing
predicate is `null`; an observed mismatch remains a concrete value alongside a
false predicate. A succeeded node therefore forbids `null`. A not-run node has
all observation fields `null`. A failed lifecycle node uses its closed
disposition objects after any resource mutation and otherwise uses `null`.
Null observations cannot satisfy predicates.

The supervisor processes the dependency DAG in the root child order, while
independent ready nodes may execute concurrently. Before starting a node it
examines direct dependencies in that node's printed dependency order. The
first non-succeeded dependency deterministically produces `not-run`; downstream
nodes repeat this rule, so the cascade terminates without executing through a
failed gate. Environment-matching dependencies never cross from `colima` to
`cvm` or vice versa. A `verified` root forbids `failed` and `not-run`. A
complete `failed` root requires all fourteen terminal documents, including
truthful not-run descendants.

## Canonical JSON and strict consumption

`sha256-canonical-json-v1` is deliberately narrower than arbitrary JSON:

1. Parse one UTF-8 JSON value and reject duplicate object keys, a byte-order
   mark, trailing values, invalid Unicode, floats, and integers outside signed
   64-bit range.
2. Reject every key not explicitly allowed by the selected schema, kind, and
   status. Public strings in this schema are ASCII and must match their closed
   literals or digest grammar.
3. Encode with object keys sorted by ASCII code point, arrays in their specified
   order, no insignificant whitespace, lowercase `true`/`false`/`null`, and
   decimal integers with no leading zero. Digest those bytes without a trailing
   newline. A stored file may add exactly one newline after the digested bytes.
4. Re-encode after parsing and require byte equality with the stored payload
   after removing that one permitted newline. This rejects alternate encodings
   instead of normalizing them silently.

A strict consumer independently computes `subjectDigest`, every child digest,
and the root receipt digest. It checks shapes before values, closes the graph,
applies the ordered predicate lists, and rejects `verified` unless every child
is `succeeded`, every predicate is `true`, every `failureCheck` is `null`, and
every `retryAllowed` is `false`. It never follows a tag, path, URL redirect,
"latest" pointer, or producer-provided success summary as identity.

For `failed`, the consumer requires at least one failed child, recomputes every
not-run cascade and `blockedBy`, and independently derives the root
`failureCheck` from the precedence rules. A leaf cannot be `not-run`. A child
with all succeeded dependencies cannot be `not-run`; one with any
non-succeeded dependency cannot be `failed` or `succeeded`. Any mismatch is a
schema rejection, not a new failure result.

## Public proof versus restricted diagnostics

Root and child documents are proof-only. They contain no timestamp, hostname,
path, command line, environment value, PID, container/job/owner identifier,
nonce, secret, token, raw error, stderr, Docker state, S3 URL, or local mount
path. A child may have a separately stored restricted diagnostic bundle. The
public child binds it only through an optional operational index outside this
Merkle graph; diagnostics can explain a failure but cannot satisfy a predicate
or alter `failureCheck`.

In particular, S3 object presence, upload counts, and Mac mount visibility are
**supply and promotion operational evidence**, not Agent runtime correctness.
They must not appear as replacements for image, lifecycle, or conformance
predicates. The one exception is a node's narrowly named immutable-object
visibility predicate, which proves that the exact already-hashed evidence input
can be retrieved; it does not prove runtime behavior.

## Terminal publication, failure, retry, and retention

Every attempt is single-shot. An evidence-complete attempt ends in one immutable
`verified` or `failed` root; `retryAllowed` is always `false`. A later try
allocates fresh runtime authority, creates new environment evidence documents,
and therefore produces a different graph and root digest. It never overwrites,
appends to, or upgrades the failed graph.

An attempt may publish its root only after all child documents have reached a
terminal state and their bytes have been re-read by digest. Failure selection
uses this global precedence:

1. `retained-resource` when an owned Agent container, owner label, Broker
   volume, job-private tree, or workload process group is positively observed;
2. `absence-proof` when required absence cannot be established;
3. `terminal-publication` when the Agent lifecycle terminal record is invalid
   or absent but the failed evidence node and complete graph can still be
   published;
4. the first closed `cleanup-*` check in the order container, Broker volume,
   then job-private tree;
5. `workload-interrupted`;
6. the first false identity, admission, lifecycle, or conformance predicate in
   the normative node/predicate order above.

For case 6 the root `failureCheck` is exactly
`<kind>[:<environment>].<predicate>`, omitting the environment and colon for an
environment-neutral node. The only root failure values outside that grammar
are the precedence aliases in items 1-5. Child documents never contain these
aliases. The SAR-003 adapter has this exact alias-to-child mapping; when an
alias covers several predicates, the first false predicate in the printed
order is selected:

| SAR-003/public alias | Exact `agent-lifecycle` child predicate or condition |
| --- | --- |
| `adapter-failure` | `adapterOperationsClosed` |
| `authority-replay` | `authorityFresh` |
| `job-private-layout` | `jobPrivateLayoutExact` |
| `snapshot-identity` | `snapshotIdentityExact` |
| `workload-identity` | `workloadIdentityExact` |
| `image-identity` | `imageIdentityOuterAttested` |
| `returned-container-id` | `returnedContainerIdExact` |
| `container-ownership` | `containerOwnershipExact` |
| `inert-container` | `inertContainerConfigExact` |
| `entrypoint-preflight` | `entrypointPreflightExact` |
| `broker-proof` | `brokerProofIdentityBound` |
| `terminal-publication` | `terminalPublicationExact` |
| `workload-process-group` | first false of `workloadProcessGroupAbsent`, `descendantResidueFalse` |
| `workload-interrupted` | `workloadNotInterrupted` |
| `workload-terminal` | `workloadTerminalZero` |
| `cleanup-container` | `containerCleanupSucceeded` |
| `cleanup-broker-volume` | `brokerVolumeCleanupSucceeded` |
| `cleanup-private-tree` | `jobPrivateTreeCleanupSucceeded` |
| `retained-resource` | first false resource predicate in `workloadProcessGroupAbsent`, `agentContainerAbsent`, `ownerLabelsAbsent`, `brokerVolumeAbsent`, `jobPrivateTreeAbsent` order whose matching disposition is `retained` |
| `absence-proof` | first false resource predicate in the same order whose matching disposition is `unproved` |

The child `failureCheck` is always the right-hand predicate, never the alias.
If multiple independent child nodes fail, the root first applies the global
precedence above and then chooses the first failed child in root child order and
its one false predicate. `not-run` descendants never outrank the failed ancestor
that blocked them. Arbitrary error text is restricted diagnostic material.

### Publication-failure tombstone

A graph cannot truthfully contain or hash a root-publication failure about
itself. Before any mutation, the supervisor allocates a fresh 32-byte attempt
authority and records
`attemptAuthorityDigest = sha256(attempt-authority-bytes || subjectDigest)` in
its own mode-private state. Independently of the graph destination, it owns a
preconfigured append-once terminal path keyed by that digest. If child-graph,
root, or visibility publication fails, it may publish exactly this minimal
tombstone there:

```json
{
  "attemptAuthorityDigest": "sha256:<64 lowercase hex>",
  "failureCheck": "graph-publication",
  "lastDurableStage": "children-partial",
  "retentionRequired": true,
  "retryAllowed": false,
  "schema": "text-to-cad.agent-runtime-verification-attempt/1",
  "status": "publication-failed",
  "subjectDigest": "sha256:<subject digest>"
}
```

The tombstone has exactly those eight keys. `failureCheck` is exactly one of
`graph-publication`, `root-publication`, or `visibility-verification`.
`lastDurableStage` is `none` or `children-partial` for graph publication,
`children-complete` for root publication, and `root-written` for visibility
verification. The stricter stage/failure pairing is mandatory.
It does not reference its own digest, does not claim a complete evidence graph,
and can never promote an artifact. If both normal publication and this
independent supervisor publication fail, there is **no authoritative terminal
receipt**. The supervisor retains all scratch and may record only an external
operational absence/failure; consumers must not infer a tombstone or root.

Successful evidence scratch cleanup is permitted only after the complete
immutable graph and root are published, every object is re-read by digest, and
the configured supply path plus Mac mount visibility are verified. A failure at
any of those operational steps retains the scratch tree and records a separate
failed promotion operation or the tombstone above; it does not rewrite runtime
predicates. Failed runtime attempts, restricted diagnostics, graph objects,
candidate images, and
history are retained. Cleanup never removes an unverified or name-selected
resource.

## Promotion, rollback, and formal-pilot boundary

`Agent Runtime Verified` is an artifact-level statement about the exact root
subject. A rollback may reuse its immutable Verified receipt only when the
Agent image manifest/config bytes, runtime manifest, Cup capability manifest,
and build-input-set lock are unchanged. A changed execution Source Snapshot
does not invalidate this artifact receipt. Reuse does not attest a new
machine: each rollback target must produce fresh host provision and
environment-qualified lifecycle/conformance evidence before execution. Those
deployment receipts refer to the existing Verified root digest; they do not
mint a different artifact identity or silently amend the old graph.

`Formal Pilot Integrated` is a later execution-level statement. It additionally
binds a particular input, runtime job authority, Agent execution, Broker and
Browser Sidecar artifacts, model/provider usage, outputs, and the formal Gate
receipt. A real paid Cup pilot can establish that later statement, but cannot
repair a missing Agent Runtime Verified predicate. Conversely, a Verified
Agent artifact makes no claim that Venus was contacted, a paid job ran, a CAD
candidate was accepted, or the formal pilot path is integrated.

## Illustrative canonical records

The following values are syntactically machine-checkable examples only. The
repeated placeholder digests do not assert that evidence exists.

```json
{"failureCheck":null,"graph":{"algorithm":"sha256-canonical-json-v1","children":[{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","environment":"colima","kind":"agent-lifecycle"},{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","environment":"cvm","kind":"agent-lifecycle"},{"digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","environment":null,"kind":"browser-deny"},{"digest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","environment":null,"kind":"build-input-set"},{"digest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","environment":null,"kind":"build-provenance"},{"digest":"sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","environment":"colima","kind":"capability-conformance"},{"digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","environment":"cvm","kind":"capability-conformance"},{"digest":"sha256:2222222222222222222222222222222222222222222222222222222222222222","environment":null,"kind":"codex-admission"},{"digest":"sha256:3333333333333333333333333333333333333333333333333333333333333333","environment":null,"kind":"cup-golden"},{"digest":"sha256:4444444444444444444444444444444444444444444444444444444444444444","environment":null,"kind":"dependency-admission"},{"digest":"sha256:5555555555555555555555555555555555555555555555555555555555555555","environment":null,"kind":"image-identity"},{"digest":"sha256:6666666666666666666666666666666666666666666666666666666666666666","environment":null,"kind":"sbom"},{"digest":"sha256:7777777777777777777777777777777777777777777777777777777777777777","environment":"colima","kind":"source-snapshot"},{"digest":"sha256:8888888888888888888888888888888888888888888888888888888888888888","environment":"cvm","kind":"source-snapshot"}],"subjectDigest":"sha256:9999999999999999999999999999999999999999999999999999999999999999"},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-verification/1","status":"verified","subject":{"agentImageConfigDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","agentImageManifestDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","buildInputSetDigest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","cupRuntimeCapabilityManifestDigest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","platform":{"architecture":"amd64","os":"linux"},"runtimeManifestDigest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}}
```

Example succeeded environment-neutral child:

```json
{"blockedBy":null,"dependsOn":[],"environment":null,"failureCheck":null,"kind":"build-input-set","predicates":{"baseManifestBound":true,"dependencyLockBound":true,"fileDigestsBound":true,"immutableObjectVisible":true,"manifestSchemaExact":true,"pathSetClosed":true,"projectRuntimeArtifactsBound":true,"recipeBound":true,"ubuntuSnapshotBound":true},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-evidence/1","status":"succeeded","subject":{"baseImageManifestDigest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","buildInputSetDigest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","buildRecipeDigest":"sha256:2222222222222222222222222222222222222222222222222222222222222222","dependencyLockDigest":"sha256:3333333333333333333333333333333333333333333333333333333333333333","projectRuntimeArtifactSetDigest":"sha256:4444444444444444444444444444444444444444444444444444444444444444","ubuntuSnapshotManifestDigest":"sha256:5555555555555555555555555555555555555555555555555555555555555555"},"subjectDigest":"sha256:9999999999999999999999999999999999999999999999999999999999999999"}
```

Example downstream node that truthfully did not run:

```json
{"blockedBy":{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","environment":"colima","kind":"agent-lifecycle"},"dependsOn":["sha256:3333333333333333333333333333333333333333333333333333333333333333","sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"environment":"colima","failureCheck":"dependency-failed","kind":"capability-conformance","predicates":{"brokerAuthorityJobPrivate":null,"browserInventoryEmpty":null,"browserProcessZero":null,"canonicalImplicitSubsetExact":null,"codexExecutableExact":null,"credentialSurfaceEmpty":null,"cupGoldenAccepted":null,"cupManifestBound":null,"dockerAuthorityAbsent":null,"inputSnapshotBound":null,"meshscopeCallable":null,"nodeRuntimeExact":null,"outputDigestBound":null,"providerNetworkDenied":null,"python312Exact":null,"residualPublicParity":null,"runtimeManifestBound":null,"sourceSnapshotBound":null,"terminalAndCleanupBound":null,"voxBlameCallable":null},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-evidence/1","status":"not-run","subject":{"agentImageManifestDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","conformanceFixtureDigest":"sha256:6666666666666666666666666666666666666666666666666666666666666666","cupRuntimeCapabilityManifestDigest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","executionSourceSnapshotDigest":"sha256:7777777777777777777777777777777777777777777777777777777777777777","inputSnapshotDigest":"sha256:8888888888888888888888888888888888888888888888888888888888888888","observedOutputDigest":null,"runtimeManifestDigest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},"subjectDigest":"sha256:9999999999999999999999999999999999999999999999999999999999999999"}
```

The example root is not a valid Verified receipt until each displayed digest
is replaced by the actual canonical digest of a valid child, the displayed
`subjectDigest` is recomputed, and the complete graph passes strict
consumption.

## Implementation still required

No code in this decision implements the schema. Follow-on work must add the
strict parser/canonicalizer, schema and graph validator, evidence producers,
immutable publication/visibility and independent tombstone operations,
restricted diagnostic index, rollback deployment receipt, fixtures for
duplicate/extra/missing/cyclic, cross-subject substitution, failed/not-run
cascade, alias mapping, and double-publication failure, plus independent
Colima/CVM provider-free runs. A
real paid pilot and `Formal Pilot Integrated` remain separate later work.
