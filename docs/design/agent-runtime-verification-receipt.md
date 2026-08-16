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
| `sourceSnapshotDigest` | Digest of the immutable Source Snapshot used to build the artifact |

Every digest is exactly `sha256:` plus 64 lowercase hexadecimal characters.
The OCI artifact and Source Snapshot are separate identities: equal Git
revisions do not make them interchangeable, and neither digest can be inferred
from the other. Their explicit coexistence in `subject` binds build provenance
without pretending that source bytes are the runtime image.

The digest of canonical `subject` is `subjectDigest`. Every child repeats that
digest, not the subject fields, so a child from another artifact, configuration,
capability manifest, or Source Snapshot cannot be grafted into the graph.

## Closed Merkle-style graph

`graph` has exactly:

```json
{
  "algorithm": "sha256-canonical-json-v1",
  "children": [],
  "subjectDigest": "sha256:<64 lowercase hex>"
}
```

`children` contains exactly the twelve role/environment references in the
table below, sorted first by `kind` and then by `environment` (`null` sorts
before strings). Each reference has exactly `kind`, `environment`, and
`digest`. No pair may repeat and no digest may repeat. An environment-neutral
node uses JSON `null`; environment-qualified nodes use only `colima` or `cvm`.

| `kind` | `environment` | Required direct dependencies |
| --- | --- | --- |
| `agent-lifecycle` | `colima` | `image-identity`, `browser-deny` |
| `agent-lifecycle` | `cvm` | `image-identity`, `browser-deny` |
| `browser-deny` | `null` | `image-identity` |
| `build-provenance` | `null` | `source-snapshot` |
| `capability-conformance` | `colima` | matching `agent-lifecycle`, `cup-golden` |
| `capability-conformance` | `cvm` | matching `agent-lifecycle`, `cup-golden` |
| `codex-admission` | `null` | `dependency-admission` |
| `cup-golden` | `null` | `image-identity` |
| `dependency-admission` | `null` | `source-snapshot` |
| `image-identity` | `null` | `build-provenance`, `sbom` |
| `sbom` | `null` | `build-provenance` |
| `source-snapshot` | `null` | none |

Each referenced document has exactly:

```json
{
  "schema": "text-to-cad.agent-runtime-evidence/1",
  "status": "succeeded",
  "kind": "<closed kind>",
  "environment": null,
  "subjectDigest": "sha256:<subject digest>",
  "dependsOn": [],
  "predicates": {},
  "failureCheck": null,
  "retryAllowed": false
}
```

Its `status` is exactly `succeeded` or `failed`.

`dependsOn` is the sorted list of the direct child document digests required by
the table. A node's own digest is the SHA-256 of its canonical document. The
root lists every node, not only leaves. A graph is closed only if all twelve
documents exist, every reference digest matches bytes, every `dependsOn`
reference resolves within those twelve documents, all nodes share the root
`subjectDigest`, dependencies match the table exactly, and the graph is
acyclic. Unreachable, duplicate, additional, or externally referenced nodes
are rejection conditions.

## Closed predicates

The following arrays are normative order. A node's `predicates` object has
exactly its listed keys. Successful nodes contain literal `true` for every key.
Failed nodes contain `true` for completed earlier checks, literal `false` for
the one selected `failureCheck`, and `null` for checks not established after
that failure. Booleans are not integers and no truthy substitute is accepted.

### Artifact admission nodes

| Node | Predicate keys, in order |
| --- | --- |
| `source-snapshot` | `manifestSchemaExact`, `pathSetClosed`, `regularFilesOnly`, `fileModesBound`, `fileSizesBound`, `fileDigestsBound`, `treeDigestMatchesSubject`, `immutableObjectVisible` |
| `build-provenance` | `builderIdentityExact`, `buildRecipeDigestExact`, `baseManifestDigestExact`, `platformLinuxAmd64`, `sourceSnapshotBound`, `networkDisabled`, `pullDisabled`, `cleanContextAllowlisted`, `outputManifestDigestExact`, `outputConfigDigestExact` |
| `dependency-admission` | `ubuntuSnapshotPinned`, `ubuntuMetadataAuthenticated`, `debClosureComplete`, `pythonWheelClosureComplete`, `nativeMeshscopeWheelAdmitted`, `browserFreeMeshshotWheelAdmitted`, `nodeArtifactAdmitted`, `canonicalImplicitBundleClosed`, `runtimeFilesByteLocked`, `offlineRebuildSucceeded` |
| `codex-admission` | `versionExact`, `platformArtifactExact`, `retrievalMetadataRecorded`, `archiveDigestExact`, `executableDigestExact`, `elfClosureClosed`, `nodeAbsentSmokePassed`, `noninteractiveSmokePassed`, `immutableMirrorVisible`, `publisherSignatureClaimAbsent` |
| `sbom` | `formatExact`, `subjectManifestDigestExact`, `allRuntimeFilesCovered`, `packageVersionsExact`, `nativeLibrariesCovered`, `licensesRecorded`, `sbomDigestBound` |
| `image-identity` | `immutableReferenceExact`, `manifestDigestObserved`, `configDigestObserved`, `runtimeManifestInsideImageExact`, `cupManifestInsideImageExact`, `osLinux`, `architectureAmd64`, `entrypointExact`, `userNonRoot`, `noMutableTagAuthority` |
| `browser-deny` | `packageInventoryEmpty`, `executableInventoryEmpty`, `cacheInventoryEmpty`, `elfMarkerInventoryEmpty`, `productMarkerInventoryEmpty`, `playwrightInventoryEmpty`, `chromiumProcessZero`, `browserLifecycleAuthorityAbsent` |
| `cup-golden` | `fixtureDigestExact`, `formalRouterImplicitOnly`, `faceCount3764`, `watertightFalse`, `eulerNumber144`, `nodeImplicitSubsetExact`, `meshscopeAccepted`, `voxBlameAccepted`, `residualBrokerPreviewAccepted`, `outputDigestRepeatable` |

`publisherSignatureClaimAbsent` is intentionally positive only when the receipt
does **not** claim upstream publisher signing for the Codex executable. Hash and
retrieval provenance are admissible; an unavailable publisher signature cannot
be synthesized.

### Environment-qualified nodes

`agent-lifecycle` in both `colima` and `cvm` uses the reviewed SAR-003 lifecycle
vocabulary and order:

1. `authorityFresh`
2. `jobPrivateLayoutExact`
3. `snapshotIdentityExact`
4. `workloadIdentityExact`
5. `imageIdentityOuterAttested`
6. `returnedContainerIdExact`
7. `containerOwnershipExact`
8. `inertContainerConfigExact`
9. `readOnlyRoot`
10. `sourceReadOnly`
11. `inputReadOnly`
12. `writableMountAllowlistExact`
13. `dockerSocketAbsent`
14. `capabilitiesEmpty`
15. `noNewPrivileges`
16. `externalNetworkAbsent`
17. `entrypointPreflightExact`
18. `brokerProofIdentityBound`
19. `workloadReleasedOnce`
20. `terminalPublicationExact`
21. `workloadProcessGroupAbsent`
22. `descendantResidueFalse`
23. `workloadTerminalZero`
24. `agentContainerAbsent`
25. `ownerLabelsAbsent`
26. `brokerVolumeAbsent`
27. `jobPrivateTreeAbsent`

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
not replace the artifact-build `sourceSnapshotDigest` in the root subject.

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

Every attempt is single-shot. It ends in one immutable `verified` or `failed`
root; `retryAllowed` is always `false`. A later try allocates fresh runtime
authority, creates new environment evidence documents, and therefore produces
a different graph and root digest. It never overwrites, appends to, or upgrades
the failed graph.

An attempt may publish its root only after all child documents have reached a
terminal state and their bytes have been re-read by digest. Failure selection
uses this global precedence:

1. `retained-resource` when an owned Agent container, owner label, Broker
   volume, job-private tree, or workload process group is positively observed;
2. `absence-proof` when required absence cannot be established;
3. `terminal-publication` when a required terminal record or immutable graph
   publication cannot be established;
4. the first closed `cleanup-*` check in the order container, Broker volume,
   job-private tree, then evidence scratch;
5. `workload-interrupted`;
6. the first false identity, admission, lifecycle, or conformance predicate in
   the normative node/predicate order above.

For case 6 the root `failureCheck` is exactly
`<kind>[:<environment>].<predicate>`, omitting the environment and colon for an
environment-neutral node. The only root failure values outside that grammar
are the precedence aliases in items 1-5. Graph shape, digest, dependency, or
publication failures use `terminal-publication`; they do not invent a dynamic
parser error string.

Within the SAR-003 lifecycle, the public failure names remain closed aliases:
`authority-replay`, `job-private-layout`, `snapshot-identity`,
`workload-identity`, `image-identity`, `returned-container-id`,
`container-ownership`, `inert-container`, `entrypoint-preflight`,
`broker-proof`, `terminal-publication`, `workload-process-group`,
`workload-interrupted`, `workload-terminal`, `adapter-failure`,
`retained-resource`, `absence-proof`, `cleanup-container`,
`cleanup-broker-volume`, `cleanup-private-tree`, and
`cleanup-evidence-scratch`. Implementations map an alias to its one exact false
predicate; arbitrary error text is restricted diagnostic material.

Successful evidence scratch cleanup is permitted only after the complete
immutable graph and root are published, every object is re-read by digest, and
the configured supply path plus Mac mount visibility are verified. A failure at
any of those operational steps retains the scratch tree and records a separate
failed promotion operation; it does not rewrite runtime predicates. Failed
runtime attempts, restricted diagnostics, graph objects, candidate images, and
history are retained. Cleanup never removes an unverified or name-selected
resource.

## Promotion, rollback, and formal-pilot boundary

`Agent Runtime Verified` is an artifact-level statement about the exact root
subject. A rollback may reuse its immutable Verified receipt only when the
Agent image manifest/config bytes, runtime manifest, Cup capability manifest,
and build Source Snapshot lock are unchanged. Reuse does not attest a new
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
{"failureCheck":null,"graph":{"algorithm":"sha256-canonical-json-v1","children":[{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","environment":"colima","kind":"agent-lifecycle"},{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","environment":"cvm","kind":"agent-lifecycle"},{"digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","environment":null,"kind":"browser-deny"},{"digest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","environment":null,"kind":"build-provenance"},{"digest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","environment":"colima","kind":"capability-conformance"},{"digest":"sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","environment":"cvm","kind":"capability-conformance"},{"digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","environment":null,"kind":"codex-admission"},{"digest":"sha256:2222222222222222222222222222222222222222222222222222222222222222","environment":null,"kind":"cup-golden"},{"digest":"sha256:3333333333333333333333333333333333333333333333333333333333333333","environment":null,"kind":"dependency-admission"},{"digest":"sha256:4444444444444444444444444444444444444444444444444444444444444444","environment":null,"kind":"image-identity"},{"digest":"sha256:5555555555555555555555555555555555555555555555555555555555555555","environment":null,"kind":"sbom"},{"digest":"sha256:6666666666666666666666666666666666666666666666666666666666666666","environment":null,"kind":"source-snapshot"}],"subjectDigest":"sha256:7777777777777777777777777777777777777777777777777777777777777777"},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-verification/1","status":"verified","subject":{"agentImageConfigDigest":"sha256:8888888888888888888888888888888888888888888888888888888888888888","agentImageManifestDigest":"sha256:9999999999999999999999999999999999999999999999999999999999999999","cupRuntimeCapabilityManifestDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","platform":{"architecture":"amd64","os":"linux"},"runtimeManifestDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","sourceSnapshotDigest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}}
```

Example environment-neutral child shape:

```json
{"dependsOn":[],"environment":null,"failureCheck":null,"kind":"source-snapshot","predicates":{"fileDigestsBound":true,"fileModesBound":true,"fileSizesBound":true,"immutableObjectVisible":true,"manifestSchemaExact":true,"pathSetClosed":true,"regularFilesOnly":true,"treeDigestMatchesSubject":true},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-evidence/1","status":"succeeded","subjectDigest":"sha256:7777777777777777777777777777777777777777777777777777777777777777"}
```

The example root is not a valid Verified receipt until each displayed digest
is replaced by the actual canonical digest of a valid child, the displayed
`subjectDigest` is recomputed, and the complete graph passes strict
consumption.

## Implementation still required

No code in this decision implements the schema. Follow-on work must add the
strict parser/canonicalizer, schema and graph validator, evidence producers,
immutable publication and visibility operation, restricted diagnostic index,
rollback deployment receipt, fixtures for duplicate/extra/missing/cyclic and
cross-subject substitution, and independent Colima/CVM provider-free runs. A
real paid pilot and `Formal Pilot Integrated` remain separate later work.
