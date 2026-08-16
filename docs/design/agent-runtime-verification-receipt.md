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

The root has exactly six top-level keys, in the following semantic shape:

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
| `verificationPlanDigest` | Digest of the independently approved, immutable provider-free verification plan |

Every digest is exactly `sha256:` plus 64 lowercase hexadecimal characters.
`buildInputSetDigest` is an artifact-construction identity, not a Source
Snapshot. A **Source Snapshot** is the separately identified project source
mounted read-only for one Agent Execution. Changing an execution Source
Snapshot or its revision does not rebuild or rename the sealed runtime. Each
environment-qualified verification execution binds the same plan-approved
Source Snapshot below; none is a production Source Snapshot or part of the
artifact build-input identity.

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

`children` contains exactly the fifteen role/environment references in the
table below, sorted first by `kind` and then by `environment` (`null` sorts
before strings). Each reference has exactly `kind`, `environment`, and
`digest`. No pair may repeat and no digest may repeat. An environment-neutral
node uses JSON `null`; environment-qualified nodes use only `colima` or `cvm`.

| `kind` | `environment` | Required direct dependencies |
| --- | --- | --- |
| `agent-lifecycle` | `colima` | `image-identity`, `browser-deny`, matching `source-snapshot`, `verification-plan` |
| `agent-lifecycle` | `cvm` | `image-identity`, `browser-deny`, matching `source-snapshot`, `verification-plan` |
| `browser-deny` | `null` | `image-identity`, `verification-plan` |
| `build-input-set` | `null` | none |
| `build-provenance` | `null` | `build-input-set` |
| `capability-conformance` | `colima` | matching `agent-lifecycle`, `cup-golden`, `verification-plan` |
| `capability-conformance` | `cvm` | matching `agent-lifecycle`, `cup-golden`, `verification-plan` |
| `codex-admission` | `null` | `dependency-admission` |
| `cup-golden` | `null` | `image-identity`, `verification-plan` |
| `dependency-admission` | `null` | `build-input-set` |
| `image-identity` | `null` | `build-provenance`, `sbom` |
| `sbom` | `null` | `build-provenance` |
| `source-snapshot` | `colima` | `verification-plan` |
| `source-snapshot` | `cvm` | `verification-plan` |
| `verification-plan` | `null` | none |

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
root lists every node, not only leaves. A graph is closed only if all fifteen
documents exist, every reference digest matches bytes, every `dependsOn`
reference resolves within those fifteen documents, all nodes share the root
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
| `codex-admission` | `codexVersion` (literal `0.147.0`), `platform` (literal `x86_64-unknown-linux-musl`), `retrievalReceiptDigest`, `archiveDigest`, `executableDigest`, `signatureBundleDigest`, `signaturePolicyDigest`, `signatureVerificationReceiptDigest`, `elfClosureDigest` |
| `sbom` | `agentImageManifestDigest`, `sbomDigest`, `format` (literal `spdx-json-2.3`) |
| `image-identity` | `agentImageManifestDigest`, `agentImageConfigDigest`, `runtimeManifestDigest`, `cupRuntimeCapabilityManifestDigest`, `platform` |
| `browser-deny` | `agentImageManifestDigest`, `scannerDigest`, `inventoryDigest`, `browserFindingCount`, `chromiumProcessCount` |
| `cup-golden` | `agentImageManifestDigest`, `fixtureDigest`, `routerManifestDigest`, `expectedOutputDigest`, `observedOutputDigest`, `faceCount`, `watertight`, `eulerNumber` |
| `source-snapshot` | `executionSourceSnapshotDigest`, `sourceManifestDigest`, `pathCount`, `totalBytes` |
| `agent-lifecycle` | `agentImageManifestDigest`, `agentImageConfigDigest`, `runtimeManifestDigest`, `executionSourceSnapshotDigest`, `inputSnapshotDigest`, `agentConfigDigest`, `brokerAuthorityDigest`, `workloadDigest`, `lifecycleHarnessDigest`, `entrypointDigest`, `lifecycleReceiptSchemaDigest`, `resourceDisposition`, `cleanupDisposition` |
| `capability-conformance` | `agentImageManifestDigest`, `runtimeManifestDigest`, `cupRuntimeCapabilityManifestDigest`, `executionSourceSnapshotDigest`, `inputSnapshotDigest`, `conformanceFixtureDigest`, `expectedOutputDigest`, `observedOutputDigest` |
| `verification-plan` | `verificationPlanDigest`, `scannerDigest`, `verificationSourceSnapshotDigest`, `verificationSourceManifestDigest`, `verificationInputSnapshotDigest`, `cupFixtureDigest`, `routerManifestDigest`, `expectedOutputDigest`, `conformanceFixtureDigest`, `lifecycleHarnessDigest`, `entrypointDigest`, `lifecycleReceiptSchemaDigest`, `agentConfigDigest`, `brokerAuthorityDigest`, `workloadDigest` |

All non-null fields ending in `Digest` use the full digest grammar. `pathCount`,
`totalBytes`, `browserFindingCount`, `chromiumProcessCount`, `faceCount`, and
`eulerNumber` are signed 64-bit integers; counts are nonnegative.
`watertight` is Boolean. `platform` in image evidence is the exact root
platform object. Each child field that names a root identity must equal the
root value; each dependent field must equal the corresponding dependency's
subject value.

For every `succeeded` or `failed` lifecycle child, `resourceDisposition` is a
concrete object with exactly `agentContainer`, `ownerLabels`, `brokerVolume`,
`jobPrivateTree`, and `workloadProcessGroup`; every value is `absent`,
`retained`, or `unproved`. `cleanupDisposition` is a concrete object with
exactly `agentContainer`, `brokerVolume`, and `jobPrivateTree`; every value is
`succeeded`, `failed`, or `not-required`. A `not-run` lifecycle child has both
fields set to `null`. These closed observations distinguish positive residue
from failure to prove absence without exposing IDs or paths.

### Verification authority and cross-field equality

The environment-neutral `verification-plan` is approved before either host
attempt and is immutable by `verificationPlanDigest`. Its producer only proves
the closed predicates below; it cannot fill the plan from runtime observations.
The plan artifact is the canonical object containing all verification-plan
subject fields except `verificationPlanDigest`; that digest is SHA-256 of those
bytes. The evidence node then copies the digest and fields, so neither the plan
nor the evidence node hashes itself.
Colima and CVM therefore run the same verifier bytes and inputs. The strict
consumer enforces every equality in this matrix:

| Plan field | Must equal |
| --- | --- |
| `verificationPlanDigest` | root `subject.verificationPlanDigest`; `verification-plan.subject.verificationPlanDigest` |
| `scannerDigest` | `browser-deny.subject.scannerDigest` |
| `verificationSourceSnapshotDigest` | both `source-snapshot.subject.executionSourceSnapshotDigest`; both lifecycle and both conformance `executionSourceSnapshotDigest` |
| `verificationSourceManifestDigest` | each `source-snapshot.subject.sourceManifestDigest` whose node is `succeeded` |
| `verificationInputSnapshotDigest` | both lifecycle and both conformance `inputSnapshotDigest` |
| `cupFixtureDigest` | `cup-golden.subject.fixtureDigest` |
| `routerManifestDigest` | `cup-golden.subject.routerManifestDigest` |
| `expectedOutputDigest` | `cup-golden.subject.expectedOutputDigest`; both conformance `expectedOutputDigest` |
| `conformanceFixtureDigest` | both conformance `conformanceFixtureDigest` |
| `lifecycleHarnessDigest` | both lifecycle `lifecycleHarnessDigest` |
| `entrypointDigest` | both lifecycle `entrypointDigest` |
| `lifecycleReceiptSchemaDigest` | both lifecycle `lifecycleReceiptSchemaDigest` |
| `agentConfigDigest` | both lifecycle `agentConfigDigest` |
| `brokerAuthorityDigest` | both lifecycle `brokerAuthorityDigest` |
| `workloadDigest` | both lifecycle `workloadDigest` |

`verificationSourceSnapshotDigest` is request-bound identity. Every Source
Snapshot, lifecycle, and conformance child repeats it as the non-null
`executionSourceSnapshotDigest` in every status, including `failed` and
`not-run`, and it always equals the plan value. The three Source Snapshot
observations have this closed establishing-predicate map:

| Observation | Establishing predicate | Exact concrete value rule |
| --- | --- | --- |
| `sourceManifestDigest` | `treeDigestMatchesObservation` | Equal to `verificationSourceManifestDigest` when `true`; a concrete full digest different from the plan value when `false` |
| `pathCount` | `pathSetClosed` | Any permitted nonnegative integer when `true` or `false` |
| `totalBytes` | `fileSizesBound` | Any permitted nonnegative integer when `true` or `false` |

- A `succeeded` Source Snapshot has all three observations concrete and its
  `sourceManifestDigest` equals `verificationSourceManifestDigest`.
- A `failed` Source Snapshot applies each row independently. An observation is
  `null` if and only if its establishing predicate is `null`; a `true` or
  `false` predicate retains the concrete observed value required by the row.
  This makes observations after an earlier first failure deterministically
  `null` without erasing observations already made.
- A `not-run` Source Snapshot has `sourceManifestDigest`, `pathCount`, and
  `totalBytes` set to `null`.

A strict consumer rejects a succeeded manifest mismatch, any null succeeded
observation, any non-null not-run observation, a failed observation whose
null/concrete state disagrees with its establishing predicate, or a concrete
value that violates its row. It must not reject a failed Source Snapshot merely
because its concrete observed manifest differs from the plan. The two Source
Snapshot node subjects are required to be byte-for-byte equal, including counts
after environment is excluded, only when both nodes succeeded; their
request-bound fields remain equal in every status.

In addition, root image/runtime/Cup identities equal every same-named child
field, and `cup-golden.observedOutputDigest` plus each conformance
`observedOutputDigest` must equal the plan's `expectedOutputDigest` on success.
A producer-selected scanner, harness, entrypoint, receipt schema, fixture,
input, router, expected output, or verification Source Snapshot is a schema
rejection.

This plan controls verification only. A later production Agent Execution may
mount a different Source Snapshot by its own execution identity without
changing the sealed image, `buildInputSetDigest`, or existing artifact-level
Verified receipt.

## Closed predicates

The following arrays are normative order. A node's `predicates` object has
exactly its listed keys. Successful nodes contain literal `true` for every key.
Failed admission/conformance nodes stop at their first failure: they contain
`true` for completed earlier checks, literal `false` for the one selected
`failureCheck`, and `null` for checks not established after that failure. The
environment-qualified `agent-lifecycle` node is explicitly specialized below:
it preserves every executed false observation and may contain multiple false
predicates. In either form, `failureCheck` is exactly one dominant false
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
| `codex-admission` | `versionExact`, `platformArtifactExact`, `retrievalMetadataRecorded`, `archiveDigestExact`, `executableDigestExact`, `archiveSingleExecutableExact`, `signatureBundleDigestExact`, `signaturePolicyExact`, `signatureVerified`, `certificateIdentityExact`, `certificateIssuerExact`, `transparencyLogVerified`, `elfClosureClosed`, `nodeAbsentSmokePassed`, `noninteractiveSmokePassed`, `immutableMirrorVisible` |
| `sbom` | `formatExact`, `subjectManifestDigestExact`, `allRuntimeFilesCovered`, `packageVersionsExact`, `nativeLibrariesCovered`, `licensesRecorded`, `sbomDigestBound` |
| `image-identity` | `immutableReferenceExact`, `manifestDigestObserved`, `configDigestObserved`, `runtimeManifestInsideImageExact`, `cupManifestInsideImageExact`, `osLinux`, `architectureAmd64`, `entrypointExact`, `userNonRoot`, `noMutableTagAuthority` |
| `browser-deny` | `packageInventoryEmpty`, `executableInventoryEmpty`, `cacheInventoryEmpty`, `elfMarkerInventoryEmpty`, `productMarkerInventoryEmpty`, `playwrightInventoryEmpty`, `chromiumProcessZero`, `browserLifecycleAuthorityAbsent` |
| `cup-golden` | `fixtureDigestExact`, `formalRouterImplicitOnly`, `faceCount3764`, `watertightFalse`, `eulerNumber144`, `nodeImplicitSubsetExact`, `meshscopeAccepted`, `voxBlameAccepted`, `residualBrokerPreviewAccepted`, `outputDigestRepeatable` |
| `source-snapshot` | `manifestSchemaExact`, `pathSetClosed`, `regularFilesOnly`, `fileModesBound`, `fileSizesBound`, `fileDigestsBound`, `treeDigestMatchesObservation`, `readOnlyMountEligible` |
| `verification-plan` | `planSchemaExact`, `planDigestExact`, `scannerApproved`, `sourceSnapshotApproved`, `sourceManifestApproved`, `inputSnapshotApproved`, `cupFixtureApproved`, `routerManifestApproved`, `expectedOutputApproved`, `conformanceFixtureApproved`, `lifecycleHarnessApproved`, `entrypointApproved`, `receiptSchemaApproved`, `agentConfigApproved`, `brokerAuthorityApproved`, `workloadApproved` |

The Codex signature predicates bind a signature over the extracted executable,
not over the archive. `archiveSingleExecutableExact` requires the exact archive
digest and byte length, exactly one regular member with the fixed executable
name/digest/byte length, no link, and no path traversal. `signatureVerified`
then verifies that executable against the fixed bundle and policy. A producer
must not infer or claim that the `.tar.gz` itself was signed.

#### Codex 0.147.0 signature policy and receipt

Trust starts from this exact versioned out-of-band approval object. The
reviewed specification bytes are the authority: they are not downloaded from
the origins that serve the approved artifacts. Its canonical digest is
`sha256:85bf8165e3ded898ec4892c8ae3ab48172566d871c79add02c96f389f663d5c4`:

```json
{
  "approvalAuthority": "text-to-cad/SAR-004-reviewed-spec",
  "approvalVersion": 2,
  "approvedBytes": {
    "archive": {
      "bytes": 98970270,
      "digest": "sha256:0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36"
    },
    "executable": {
      "bytes": 258278208,
      "digest": "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40"
    },
    "legacyTrustMaterial": {
      "ctfeKeyDigest": "sha256:270488a309d22e804eeb245493e87c667658d749006b9fee9cc614572d4fbbdc",
      "fulcioIntermediateDigest": "sha256:f8cbecf186db7714624a5f4e99da31a917cbef70a94dd6921f5c3ca969dfe30a",
      "fulcioRootDigest": "sha256:f989aa23def87c549404eadba767768d2a3c8d6d30a8b793f9f518a8eafd2cf5",
      "rekorKeyDigest": "sha256:dce5ef715502ec9f3cdfd11f8cc384b31a6141023d3e7595e9908a81cb6241bd"
    },
    "signatureBundle": {
      "bytes": 8585,
      "digest": "sha256:8ea31ab792fe0cfc7ba55c9dfc1836edf166dabf2d564ed7391eed6c7d422b3d"
    },
    "trustedRoot": {
      "rootBytes": 5630,
      "rootDigest": "sha256:73747011d0857ada15479a16c4cae0f3ed03aac698b523b97e1de314ac9d9ca8",
      "snapshotBytes": 1760,
      "snapshotDigest": "sha256:8f784ab614ec62bfdd5f568eb2a2e3011668449ba235ed4eb7befa99f8469933",
      "targetsBytes": 4942,
      "targetsDigest": "sha256:6a697f7f8908c8ab26c11786ecb490b54acec97fa8c802e399f065f8a0cc1acd",
      "timestampBytes": 449,
      "timestampDigest": "sha256:367992e4f09fbdb98f05cbf4433a3e6d3830d34c230eebd955fb20ccb5c0a956",
      "trustedRootBytes": 6787,
      "trustedRootDigest": "sha256:6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66"
    },
    "verifier": {
      "binaryBytes": 108805570,
      "binaryDigest": "sha256:13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62",
      "checksumsBytes": 3906,
      "checksumsDigest": "sha256:5020625e52f7041b9e4a21ee7ef4e2d085d767e72f86e2458443b012b0200362"
    }
  },
  "deliveryChannel": "text-to-cad-reviewed-release-input",
  "sameOriginHashAuthenticationAllowed": false,
  "schema": "text-to-cad.sigstore-trust-anchor-approval/2",
  "scope": "codex-0.147.0-x86_64-unknown-linux-musl",
  "signaturePolicyDigest": "sha256:8bfd47abc5c13845f82a218fe79ac2378adb29c4cb302a8b9a41eb631f3451d2"
}
```

The approval is a versioned normative release input. A consumer accepts only
the exact object and digest printed here; an implementation, retrieval receipt,
ambient cache, or same-origin checksum cannot replace or extend it. Updating,
revoking, or superseding these approved bytes requires a reviewed specification
change. The approval deliberately binds the archive, extracted executable,
signature bundle, verifier, verifier checksum file, Sigstore trusted-root
target, and the TUF metadata observed during research. The TUF hashes establish
the approved byte set, not a self-authenticating online bootstrap. For the
legacy Cosign bundle, the four `legacyTrustMaterial` digests are the exact
Fulcio root/intermediate and Rekor/CTFE keys deterministically extracted from
the approved `trusted_root.json`; independently downloaded or ambient copies
are never authority.

The version-specific policy is the following exact closed object. Its canonical
digest is
`sha256:8bfd47abc5c13845f82a218fe79ac2378adb29c4cb302a8b9a41eb631f3451d2`,
which is the only admitted `signaturePolicyDigest` for this Codex version and
platform:

```json
{
  "archive": {
    "assetId": 504450426,
    "bytes": 98970270,
    "digest": "sha256:0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36",
    "linksAllowed": false,
    "memberBytes": 258278208,
    "memberCount": 1,
    "memberDigest": "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40",
    "memberName": "codex-x86_64-unknown-linux-musl",
    "memberType": "regular-file",
    "name": "codex-x86_64-unknown-linux-musl.tar.gz",
    "pathTraversalAllowed": false,
    "signedDirectly": false
  },
  "certificate": {
    "chainIssuer": "O=sigstore.dev,CN=sigstore-intermediate",
    "fingerprintDigest": "sha256:0cd70c48dbbb777f1910538d62604b16be271028b8195325bb8eae58fcf255c8",
    "notAfter": "2026-08-07T01:12:23Z",
    "notBefore": "2026-08-07T01:02:23Z",
    "oidcIssuer": "https://token.actions.githubusercontent.com",
    "sanUri": "https://github.com/openai/codex/.github/workflows/rust-release.yml@refs/tags/rust-v0.147.0"
  },
  "codexVersion": "0.147.0",
  "executable": {
    "bytes": 258278208,
    "digest": "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40",
    "name": "codex-x86_64-unknown-linux-musl",
    "platform": "x86_64-unknown-linux-musl"
  },
  "githubWorkflow": {
    "linuxSigningActionDigest": "sha256:4e5fa040cf838f087ce4a0c585f651e90111b4a02973458b926d6938a24108e5",
    "name": "rust-release",
    "ref": "refs/tags/rust-v0.147.0",
    "repository": "openai/codex",
    "sha": "be6e8eac029b183056b7e4402879f15d2c85f61b",
    "trigger": "push",
    "wildcardsAllowed": false,
    "workflowDigest": "sha256:62367daacaabcc8972b6f0a60d2f964bd957e7ec68cab5d62756fd494041d183"
  },
  "schema": "text-to-cad.agent-runtime-codex-signature-policy/1",
  "signatureBundle": {
    "assetId": 504450400,
    "bytes": 8585,
    "digest": "sha256:8ea31ab792fe0cfc7ba55c9dfc1836edf166dabf2d564ed7391eed6c7d422b3d",
    "name": "codex-x86_64-unknown-linux-musl.sigstore",
    "payloadDigest": "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40"
  },
  "transparencyLog": {
    "integratedTime": "2026-08-07T01:02:25Z",
    "logId": "c0d23d6ad406973f9559f3ba2d1ca01f84147d8ffc5b8445c224f98b9591801d",
    "logIndex": 2363083279
  },
  "trustedRoot": {
    "legacyTrustMaterial": {
      "ctfeKeyDigest": "sha256:270488a309d22e804eeb245493e87c667658d749006b9fee9cc614572d4fbbdc",
      "fulcioIntermediateDigest": "sha256:f8cbecf186db7714624a5f4e99da31a917cbef70a94dd6921f5c3ca969dfe30a",
      "fulcioRootDigest": "sha256:f989aa23def87c549404eadba767768d2a3c8d6d30a8b793f9f518a8eafd2cf5",
      "rekorKeyDigest": "sha256:dce5ef715502ec9f3cdfd11f8cc384b31a6141023d3e7595e9908a81cb6241bd"
    },
    "rootBytes": 5630,
    "rootDigest": "sha256:73747011d0857ada15479a16c4cae0f3ed03aac698b523b97e1de314ac9d9ca8",
    "rootExpires": "2026-11-20T13:58:18Z",
    "rootVersion": 15,
    "snapshotBytes": 1760,
    "snapshotDigest": "sha256:8f784ab614ec62bfdd5f568eb2a2e3011668449ba235ed4eb7befa99f8469933",
    "snapshotExpires": "2036-05-15T08:09:16Z",
    "snapshotVersion": 165,
    "targetsBytes": 4942,
    "targetsDigest": "sha256:6a697f7f8908c8ab26c11786ecb490b54acec97fa8c802e399f065f8a0cc1acd",
    "targetsExpires": "2036-05-09T09:00:52Z",
    "targetsVersion": 14,
    "timestampBytes": 449,
    "timestampDigest": "sha256:367992e4f09fbdb98f05cbf4433a3e6d3830d34c230eebd955fb20ccb5c0a956",
    "timestampExpires": "2026-08-23T01:53:11Z",
    "timestampVersion": 757,
    "trustedRootBytes": 6787,
    "trustedRootDigest": "sha256:6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66"
  },
  "verifier": {
    "assetId": 196693093,
    "binaryBytes": 108805570,
    "binaryDigest": "sha256:13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62",
    "checksumsBytes": 3906,
    "checksumsDigest": "sha256:5020625e52f7041b9e4a21ee7ef4e2d085d767e72f86e2458443b012b0200362",
    "commitSha": "9a4cfe1aae777984c07ce373d97a65428bbff734",
    "name": "cosign",
    "platform": "darwin/arm64",
    "releaseId": 178267850,
    "sourcePackaging": "raw-executable-no-archive",
    "tagObjectSha": "531befdf6581582e22eda7cda084565bb106efa6",
    "version": "2.4.1"
  }
}
```

The signature verification producer emits the following exact proof-only
object after offline verification. Its canonical digest is
`sha256:ee8632e8d7e9610014e0d59e0b074414540e6e6b5e8feca04d2ef519db488e84`.
It records the replay but is not the required formal
`signatureVerificationReceiptDigest` because `result` and
`trustBootstrap.status` remain proof-only:

```json
{
  "archive": {
    "assetId": 504450426,
    "bytes": 98970270,
    "digest": "sha256:0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36",
    "linksAllowed": false,
    "memberBytes": 258278208,
    "memberCount": 1,
    "memberDigest": "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40",
    "memberName": "codex-x86_64-unknown-linux-musl",
    "memberType": "regular-file",
    "name": "codex-x86_64-unknown-linux-musl.tar.gz",
    "pathTraversalAllowed": false,
    "signedDirectly": false
  },
  "archiveNegativeControl": {
    "bundlePayloadDigest": "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40",
    "exitCode": 1,
    "result": "rejected-payload-mismatch",
    "testedPayloadDigest": "sha256:0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36"
  },
  "certificate": {
    "chainIssuer": "O=sigstore.dev,CN=sigstore-intermediate",
    "fingerprintDigest": "sha256:0cd70c48dbbb777f1910538d62604b16be271028b8195325bb8eae58fcf255c8",
    "notAfter": "2026-08-07T01:12:23Z",
    "notBefore": "2026-08-07T01:02:23Z",
    "oidcIssuer": "https://token.actions.githubusercontent.com",
    "sanUri": "https://github.com/openai/codex/.github/workflows/rust-release.yml@refs/tags/rust-v0.147.0"
  },
  "cryptographicResult": "verified",
  "githubWorkflow": {
    "linuxSigningActionDigest": "sha256:4e5fa040cf838f087ce4a0c585f651e90111b4a02973458b926d6938a24108e5",
    "name": "rust-release",
    "ref": "refs/tags/rust-v0.147.0",
    "repository": "openai/codex",
    "sha": "be6e8eac029b183056b7e4402879f15d2c85f61b",
    "trigger": "push",
    "wildcardsAllowed": false,
    "workflowDigest": "sha256:62367daacaabcc8972b6f0a60d2f964bd957e7ec68cab5d62756fd494041d183"
  },
  "result": "proof-only",
  "schema": "text-to-cad.agent-runtime-codex-signature-verification/1",
  "signatureBundleDigest": "sha256:8ea31ab792fe0cfc7ba55c9dfc1836edf166dabf2d564ed7391eed6c7d422b3d",
  "signaturePolicyDigest": "sha256:8bfd47abc5c13845f82a218fe79ac2378adb29c4cb302a8b9a41eb631f3451d2",
  "signedPayloadDigest": "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40",
  "transparencyLog": {
    "integratedTime": "2026-08-07T01:02:25Z",
    "logId": "c0d23d6ad406973f9559f3ba2d1ca01f84147d8ffc5b8445c224f98b9591801d",
    "logIndex": 2363083279
  },
  "trustBootstrap": {
    "approvalDigest": "sha256:85bf8165e3ded898ec4892c8ae3ab48172566d871c79add02c96f389f663d5c4",
    "status": "not-formal-admission"
  },
  "trustedRoot": {
    "legacyTrustMaterial": {
      "ctfeKeyDigest": "sha256:270488a309d22e804eeb245493e87c667658d749006b9fee9cc614572d4fbbdc",
      "fulcioIntermediateDigest": "sha256:f8cbecf186db7714624a5f4e99da31a917cbef70a94dd6921f5c3ca969dfe30a",
      "fulcioRootDigest": "sha256:f989aa23def87c549404eadba767768d2a3c8d6d30a8b793f9f518a8eafd2cf5",
      "rekorKeyDigest": "sha256:dce5ef715502ec9f3cdfd11f8cc384b31a6141023d3e7595e9908a81cb6241bd"
    },
    "rootDigest": "sha256:73747011d0857ada15479a16c4cae0f3ed03aac698b523b97e1de314ac9d9ca8",
    "snapshotDigest": "sha256:8f784ab614ec62bfdd5f568eb2a2e3011668449ba235ed4eb7befa99f8469933",
    "targetsDigest": "sha256:6a697f7f8908c8ab26c11786ecb490b54acec97fa8c802e399f065f8a0cc1acd",
    "timestampDigest": "sha256:367992e4f09fbdb98f05cbf4433a3e6d3830d34c230eebd955fb20ccb5c0a956",
    "trustedRootDigest": "sha256:6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66"
  },
  "verifier": {
    "binaryDigest": "sha256:13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62",
    "checksumsDigest": "sha256:5020625e52f7041b9e4a21ee7ef4e2d085d767e72f86e2458443b012b0200362",
    "commitSha": "9a4cfe1aae777984c07ce373d97a65428bbff734",
    "name": "cosign",
    "platform": "darwin/arm64",
    "tagObjectSha": "531befdf6581582e22eda7cda084565bb106efa6",
    "version": "2.4.1"
  }
}
```

The formal receipt uses that same closed schema and differs in exactly two
leaves: `result` and `trustBootstrap.status` are both `verified`. Every other
leaf equals the proof-only receipt and policy projection. The formal receipt's
canonical digest is the subject's `signatureVerificationReceiptDigest`; the
proof-only digest above is forbidden there. The verifier receives the exact
approved executable, bundle, verifier, and trusted-root bytes and runs offline.
No network observation, mutable controller key, ambient trust cache, or locally
updated TUF state can change an approved byte or satisfy a signature predicate.

The subject's separate `retrievalReceiptDigest` records non-authoritative
provenance only. Its document uses schema literal
`text-to-cad.codex-retrieval-metadata/1` and exactly `schema`, `observedAt`, and
`objects`. `observedAt` is UTC RFC 3339 at whole-second precision. `objects` is
an array in this exact order: `archive`, `signatureBundle`, `verifierBinary`,
`verifierChecksums`, `root`, `timestamp`, `snapshot`, `targets`,
`trustedRoot`. Each object has exactly `kind`, `requestedUrl`, `finalUrl`,
`redirects`, `bytes`, `digest`, and `responseMetadataDigest`. `kind` is its
printed literal; URLs are nonempty HTTPS strings without userinfo or fragment;
`redirects` is an array of HTTPS URLs in observed order; `bytes` is a
nonnegative integer; both digests use the full digest grammar. The received
length and digest for every object must equal the approval object where that
object is approved. Missing, extra, reordered, or substituted objects are
rejected.

This metadata says where bytes were observed; it is not publisher
authentication, a trust anchor, or an input to `signatureVerified`. TLS and
response details may be retained in restricted diagnostics, but no transport
claim can replace the normative approval or the offline Sigstore proof.

For this version/platform, a `codex-admission` subject must carry exactly the
archive, executable, and bundle digests printed above and the printed policy
digest. Its `signatureVerificationReceiptDigest` is instead the independently
computed digest of the concrete formal receipt; the proof-only digest is
forbidden. `retrievalReceiptDigest` and `elfClosureDigest` bind the separately
produced acquisition/mirror and Noble ELF observations. The signature checks
have these exact meanings:

| Predicate | Exact success condition |
| --- | --- |
| `archiveSingleExecutableExact` | The receipt's complete `archive` object equals the policy, the raw archive matches `archiveDigest`, and safe listing/extraction proves its one fixed regular member equals `executableDigest`; both `linksAllowed` and `pathTraversalAllowed` are false and `signedDirectly` is false |
| `signatureBundleDigestExact` | The raw 8,585-byte bundle digest equals subject, policy, and receipt `signatureBundleDigest`, and its payload digest equals `executableDigest` |
| `signaturePolicyExact` | The policy has exactly the closed object above and its independently recomputed digest equals subject and receipt `signaturePolicyDigest` |
| `signatureVerified` | The formal receipt has `cryptographicResult:verified`, `result:verified`, `trustBootstrap.status:verified`, and the fixed approved Cosign 2.4.1 verifier in legacy-bundle mode verifies the approved bundle over `executableDigest` using only the four approved trust-material projections; `--offline`, an empty ambient cache, and denied network are mandatory, and the archive negative control is exactly `rejected-payload-mismatch` with exit code `1` |
| `certificateIdentityExact` | Certificate SAN plus repository, workflow name/ref/SHA/trigger, upstream workflow digest, and Linux signing-action digest equal policy, with `wildcardsAllowed:false` |
| `certificateIssuerExact` | OIDC issuer, certificate-chain issuer, certificate fingerprint, and certificate validity bounds equal policy; the Fulcio chain verifies under the approved root/intermediate and the embedded SCT verifies under the approved CTFE key |
| `transparencyLogVerified` | The legacy bundle's Rekor Signed Entry Timestamp (inclusion promise) verifies under the approved Rekor key; its body binds `executableDigest` and equals the fixed log ID, index, and integrated time, which is within the fixed certificate validity interval. This predicate does not claim a Merkle inclusion proof |

The policy and verification receipt are immutable inputs to the
`codex-admission` child, not additional graph nodes. The child subject copies
the raw bundle digest, fixed policy digest, and concrete formal-receipt digest.
A strict consumer requires exact equality between those subject fields and the policy
and receipt, checks every nested key/literal/digest, and rejects an ambient
verifier or trust cache, wildcard identity, alternate ref/workflow/commit,
missing or substituted Rekor entry, any byte not fixed by the approval,
archive-as-signed claim, multi-entry/link/traversal archive, or signature over
bytes other than `executableDigest`.

The approved upstream bundle is Cosign's legacy format. Verification supplies
the extracted Fulcio root and intermediate through `--ca-roots` and
`--ca-intermediates`, and supplies the extracted Rekor and CTFE keys through
`SIGSTORE_REKOR_PUBLIC_KEY` and `SIGSTORE_CT_LOG_PUBLIC_KEY_FILE`. Omitting
either explicit key, accepting system roots, or permitting Cosign to enter its
default TUF/network path is a failure. The SET proves the log's signed inclusion
promise; full Merkle inclusion enrichment is deferred and may not be inferred
from this receipt.

It also rejects the proof-only receipt or its digest, an unapproved or
substituted approval object, same-origin hashes offered as additional authority,
or any mismatch among the approved bytes, certificate workflow claims, policy,
and formal receipt. Retrieval metadata remains mandatory for provenance but is
never consulted to decide a signature predicate.

The proof-only receipt fixes the observed cryptographic verification facts. The
committed proof candidate is not a formal receipt and does not complete mirror,
ELF, Node-absent, noninteractive, or `codex-admission` evidence.

The seven signature predicates are evaluated in their printed order. Each is a
normal admission predicate under the generic stop-at-first-false grammar; no
signature-specific retry, alias, status, or sixteenth graph node exists.

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

The lifecycle phases are exact:

| Phase | Predicates | Rule after the node starts |
| --- | --- | --- |
| primary/startup | 1-20 | Execute in order until primary failure; retain every Boolean already observed |
| terminal/workload | 21-25 | Mandatory observation suffix; inspect every applicable terminal/process/interrupt/workload fact even after primary failure |
| cleanup | 26-28 | Mandatory bounded cleanup suffix for every possibly owned resource; `not-required` makes its predicate true |
| absence | 29-32, plus process-group absence at 22 | Mandatory independent absence observation after cleanup |

The lifecycle producer never applies generic stop-at-first-false to the
mandatory suffix. It appends each fact it actually establishes and never
changes an earlier `false` to `null` or `true`. A terminal/workload predicate is
`null` only when the corresponding fact genuinely cannot exist or be observed;
for example, `workloadTerminalZero` is null if no workload was released.
After a lifecycle node starts, its cleanup and resource-absence predicates are
never null and both disposition objects are always concrete. For each cleanup
pair, `containerCleanupSucceeded`, `brokerVolumeCleanupSucceeded`, or
`jobPrivateTreeCleanupSucceeded` is `true` exactly when its matching
`cleanupDisposition` value is `succeeded` or `not-required`, and is `false`
exactly when that value is `failed`. For each absence pair,
`workloadProcessGroupAbsent`, `agentContainerAbsent`, `ownerLabelsAbsent`,
`brokerVolumeAbsent`, or `jobPrivateTreeAbsent` is `true` exactly when its
matching `resourceDisposition` value is `absent`, and is `false` exactly when
that value is `retained` or `unproved`.

When no matching resource was ever allocated, cleanup is
`not-required`/true and absence is `absent`/true. An unexpected early adapter
exception sets `adapterOperationsClosed:false` but still enters every safe
mandatory suffix and records each cleanup as `succeeded`, `failed`, or
`not-required` and each absence as `absent`, `retained`, or `unproved`, as
applicable. A strict consumer rejects either disposition object as null in a
`succeeded` or `failed` lifecycle child, either object as concrete in a
`not-run` lifecycle child, or any predicate/disposition mismatch above.

After all phases, the lifecycle child selects one of its observed false
predicates without deleting any others:

1. first resource predicate in `workloadProcessGroupAbsent`,
   `agentContainerAbsent`, `ownerLabelsAbsent`, `brokerVolumeAbsent`,
   `jobPrivateTreeAbsent` order whose disposition is `retained`;
2. first false resource predicate in that order whose disposition is
   `unproved`;
3. `terminalPublicationExact` if false;
4. first false cleanup predicate in container, Broker volume, private tree
   order;
5. `workloadNotInterrupted` if false;
6. otherwise the first false predicate in the complete normative lifecycle
   order.

That selected predicate alone is `failureCheck`; every other executed false
remains in `predicates`. The root maps the selected predicate plus its closed
disposition to the environment-qualified SAR-003 alias table below.

For example, if `entrypointPreflightExact:false` is recorded and cleanup later
leaves the owned Agent container present, the final child retains both
`entrypointPreflightExact:false` and `agentContainerAbsent:false`, records
`resourceDisposition.agentContainer:"retained"`, and selects
`failureCheck:"agentContainerAbsent"`. The root failure is
`agent-lifecycle:<environment>.retained-resource`; the preflight failure remains
auditable and is not overwritten.

The five resource-absence predicates are independent. Labels are inventory
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
| `failed` | `null` | exactly the selected dominant false predicate key | generic nodes stop after one false; lifecycle preserves every executed Boolean and uses null only for unexecuted/unestablished checks | All direct dependencies succeeded; producer attempted this node |
| `not-run` | first non-succeeded direct dependency reference | literal `dependency-failed` | all `null` | Producer must not execute this node |

For `failed` and `not-run`, request-bound identity fields in `subject` remain
exact. A field copied from the root, a direct dependency, or the immutable
attempt request is request-bound and never `null`.

For every observation below, the establishing predicate is `null` if and only
if the observation is `null`. A `true` or `false` establishing predicate
therefore requires a concrete observation, and a concrete false observation is
retained rather than erased. A succeeded node forbids null observations; a
not-run node has every observation field `null`. The Source Snapshot manifest
and count rules remain exactly as specified under verification authority and
cross-field equality above. A succeeded or failed lifecycle node has both
closed disposition objects concrete; a not-run lifecycle node has both `null`,
exactly as specified in the lifecycle phase rules above.

The remaining observation map is closed:

| Node and observation | Establishing predicate | Exact concrete value rule |
| --- | --- | --- |
| `browser-deny.inventoryDigest` | `packageInventoryEmpty` | Full digest of the one complete closed inventory scan, whether the predicate is `true` or `false` |
| `browser-deny.browserFindingCount` | `packageInventoryEmpty` | `0` when all six inventory predicates are `true`; at least `1` when any of them is `false` |
| `browser-deny.chromiumProcessCount` | `chromiumProcessZero` | `0` when `true`; at least `1` when `false` |
| `cup-golden.faceCount` | `faceCount3764` | `3764` when `true`; any permitted nonnegative integer other than `3764` when `false` |
| `cup-golden.watertight` | `watertightFalse` | literal `false` when `true`; literal `true` when `false` |
| `cup-golden.eulerNumber` | `eulerNumber144` | `144` when `true`; any signed 64-bit integer other than `144` when `false` |
| `cup-golden.observedOutputDigest` | `outputDigestRepeatable` | Equal to `expectedOutputDigest` when `true`; any concrete full digest when `false`, including the expected digest |
| `capability-conformance.observedOutputDigest` | `outputDigestBound` | Equal to `expectedOutputDigest` when `true`; a concrete full digest different from `expectedOutputDigest` when `false` |

The six Browser inventory predicates are, in order,
`packageInventoryEmpty`, `executableInventoryEmpty`, `cacheInventoryEmpty`,
`elfMarkerInventoryEmpty`, `productMarkerInventoryEmpty`, and
`playwrightInventoryEmpty`. Evaluating the first predicate establishes the one
complete closed inventory scan, its `inventoryDigest`, and its aggregate
`browserFindingCount`; the later five predicates query that same scan and never
replace or partially re-run it. A Browser failure at any inventory predicate
occurs before `chromiumProcessZero`, so `chromiumProcessCount` remains `null`.
A succeeded Browser node has a concrete `inventoryDigest` and both counts equal
to `0`.

A strict consumer rejects any observation that violates the null equivalence or
concrete value rule in the table. In particular, it rejects a Browser inventory
digest or finding count without an evaluated `packageInventoryEmpty`, a null
scan result after that predicate was evaluated, a zero aggregate count when an
inventory predicate is false, a nonzero aggregate count when all six are true,
a process count whose nullness disagrees with `chromiumProcessZero`, or a
process count inconsistent with that Boolean. It rejects a null Cup metric or
output after its establishing predicate was evaluated, any Cup metric value
inconsistent with its Boolean, a true Cup output digest unequal to expected, a
null conformance output after `outputDigestBound` was evaluated, a true
conformance output unequal to expected, or a false conformance output equal to
expected. It does not reject a false `outputDigestRepeatable` merely because
the one retained Cup output digest equals expected: repeatability can fail even
when that observed run produced the expected bytes. Null observations cannot
satisfy predicates.

The supervisor processes the dependency DAG in the root child order, while
independent ready nodes may execute concurrently. Before starting a node it
examines direct dependencies in that node's printed dependency order. The
first non-succeeded dependency deterministically produces `not-run`; downstream
nodes repeat this rule, so the cascade terminates without executing through a
failed gate. Environment-matching dependencies never cross from `colima` to
`cvm` or vice versa. A `verified` root forbids `failed` and `not-run`. A
complete `failed` root requires all fifteen terminal documents, including
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

For a failed non-lifecycle node the root `failureCheck` is exactly
`<kind>[:<environment>].<predicate>`, omitting the environment and colon for an
environment-neutral node. For lifecycle, it is exactly
`agent-lifecycle:<environment>.<alias>`. Child documents never contain aliases.
The root derives the alias from the lifecycle child's already selected
predicate and closed disposition using this exact SAR-003 mapping:

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
| `inert-container` | first false of `inertContainerConfigExact`, `readOnlyRoot`, `sourceReadOnly`, `inputReadOnly`, `writableMountAllowlistExact`, `dockerSocketAbsent`, `capabilitiesEmpty`, `noNewPrivileges`, `externalNetworkAbsent` in normative predicate order |
| `entrypoint-preflight` | `entrypointPreflightExact` |
| `broker-proof` | `brokerProofIdentityBound` |
| `workload-release` | `workloadReleasedOnce` |
| `terminal-publication` | `terminalPublicationExact` |
| `workload-process-group` | `descendantResidueFalse` |
| `workload-interrupted` | `workloadNotInterrupted` |
| `workload-terminal` | `workloadTerminalZero` |
| `cleanup-container` | `containerCleanupSucceeded` |
| `cleanup-broker-volume` | `brokerVolumeCleanupSucceeded` |
| `cleanup-private-tree` | `jobPrivateTreeCleanupSucceeded` |
| `retained-resource` | first false resource predicate in `workloadProcessGroupAbsent`, `agentContainerAbsent`, `ownerLabelsAbsent`, `brokerVolumeAbsent`, `jobPrivateTreeAbsent` order whose matching disposition is `retained` |
| `absence-proof` | first false resource predicate in the same order whose matching disposition is `unproved` |

The child `failureCheck` is always the right-hand selected predicate, never the
alias. This table is a closed, exact mapping over all 32 lifecycle predicates,
not a list of examples. For every legal selected `failureCheck` and its required
condition, a strict consumer must derive exactly one alias. It must reject a
missing or overlapping mapping, a wildcard or fallback rule, an alias named
after an otherwise unmapped child predicate, and any child predicate used
directly as the public alias.
`workloadReleasedOnce` maps only to `workload-release`; it is neither
`workload-identity` nor `workload-terminal`.

The five resource-absence predicates have no non-disposition fallback. When
one is the selected false predicate, its matching closed disposition must be
`retained` or `unproved`, and it maps only to `retained-resource` or
`absence-proof`, respectively. A selected false resource-absence predicate
paired with `absent`, a missing disposition, or any other alias is a schema
rejection. In particular, `workloadProcessGroupAbsent` is governed only by
that disposition split; `workload-process-group` names
`descendantResidueFalse`, not a resource-absence fallback.

If multiple independent child nodes fail, the root first compares their
selected predicates using the global precedence above and then chooses the
first tied failed child in root child order. It emits that node's qualified
predicate or qualified lifecycle alias. `not-run` descendants never outrank the
failed ancestor that blocked them. Arbitrary error text is restricted
diagnostic material.

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
build-input-set lock, and approved verification-plan digest are unchanged. A
changed production execution Source Snapshot does not invalidate this artifact
receipt. Reuse does not attest a new
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
{"failureCheck":null,"graph":{"algorithm":"sha256-canonical-json-v1","children":[{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","environment":"colima","kind":"agent-lifecycle"},{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","environment":"cvm","kind":"agent-lifecycle"},{"digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","environment":null,"kind":"browser-deny"},{"digest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","environment":null,"kind":"build-input-set"},{"digest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","environment":null,"kind":"build-provenance"},{"digest":"sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","environment":"colima","kind":"capability-conformance"},{"digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","environment":"cvm","kind":"capability-conformance"},{"digest":"sha256:2222222222222222222222222222222222222222222222222222222222222222","environment":null,"kind":"codex-admission"},{"digest":"sha256:3333333333333333333333333333333333333333333333333333333333333333","environment":null,"kind":"cup-golden"},{"digest":"sha256:4444444444444444444444444444444444444444444444444444444444444444","environment":null,"kind":"dependency-admission"},{"digest":"sha256:5555555555555555555555555555555555555555555555555555555555555555","environment":null,"kind":"image-identity"},{"digest":"sha256:6666666666666666666666666666666666666666666666666666666666666666","environment":null,"kind":"sbom"},{"digest":"sha256:7777777777777777777777777777777777777777777777777777777777777777","environment":"colima","kind":"source-snapshot"},{"digest":"sha256:8888888888888888888888888888888888888888888888888888888888888888","environment":"cvm","kind":"source-snapshot"},{"digest":"sha256:abababababababababababababababababababababababababababababababab","environment":null,"kind":"verification-plan"}],"subjectDigest":"sha256:9999999999999999999999999999999999999999999999999999999999999999"},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-verification/1","status":"verified","subject":{"agentImageConfigDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","agentImageManifestDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","buildInputSetDigest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","cupRuntimeCapabilityManifestDigest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","platform":{"architecture":"amd64","os":"linux"},"runtimeManifestDigest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","verificationPlanDigest":"sha256:cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"}}
```

Example succeeded environment-neutral child:

```json
{"blockedBy":null,"dependsOn":[],"environment":null,"failureCheck":null,"kind":"build-input-set","predicates":{"baseManifestBound":true,"dependencyLockBound":true,"fileDigestsBound":true,"immutableObjectVisible":true,"manifestSchemaExact":true,"pathSetClosed":true,"projectRuntimeArtifactsBound":true,"recipeBound":true,"ubuntuSnapshotBound":true},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-evidence/1","status":"succeeded","subject":{"baseImageManifestDigest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","buildInputSetDigest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","buildRecipeDigest":"sha256:2222222222222222222222222222222222222222222222222222222222222222","dependencyLockDigest":"sha256:3333333333333333333333333333333333333333333333333333333333333333","projectRuntimeArtifactSetDigest":"sha256:4444444444444444444444444444444444444444444444444444444444444444","ubuntuSnapshotManifestDigest":"sha256:5555555555555555555555555555555555555555555555555555555555555555"},"subjectDigest":"sha256:9999999999999999999999999999999999999999999999999999999999999999"}
```

Example downstream node that truthfully did not run:

```json
{"blockedBy":{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","environment":"colima","kind":"agent-lifecycle"},"dependsOn":["sha256:3333333333333333333333333333333333333333333333333333333333333333","sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sha256:abababababababababababababababababababababababababababababababab"],"environment":"colima","failureCheck":"dependency-failed","kind":"capability-conformance","predicates":{"brokerAuthorityJobPrivate":null,"browserInventoryEmpty":null,"browserProcessZero":null,"canonicalImplicitSubsetExact":null,"codexExecutableExact":null,"credentialSurfaceEmpty":null,"cupGoldenAccepted":null,"cupManifestBound":null,"dockerAuthorityAbsent":null,"inputSnapshotBound":null,"meshscopeCallable":null,"nodeRuntimeExact":null,"outputDigestBound":null,"providerNetworkDenied":null,"python312Exact":null,"residualPublicParity":null,"runtimeManifestBound":null,"sourceSnapshotBound":null,"terminalAndCleanupBound":null,"voxBlameCallable":null},"retryAllowed":false,"schema":"text-to-cad.agent-runtime-evidence/1","status":"not-run","subject":{"agentImageManifestDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","conformanceFixtureDigest":"sha256:6666666666666666666666666666666666666666666666666666666666666666","cupRuntimeCapabilityManifestDigest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","executionSourceSnapshotDigest":"sha256:7777777777777777777777777777777777777777777777777777777777777777","expectedOutputDigest":"sha256:efefefefefefefefefefefefefefefefefefefefefefefefefefefefefefefef","inputSnapshotDigest":"sha256:8888888888888888888888888888888888888888888888888888888888888888","observedOutputDigest":null,"runtimeManifestDigest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},"subjectDigest":"sha256:9999999999999999999999999999999999999999999999999999999999999999"}
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
cascade, lifecycle multi-failure/mandatory-suffix precedence, verification-plan
cross-host substitution, alias mapping, and double-publication failure, plus
independent Colima/CVM provider-free runs. A
real paid pilot and `Formal Pilot Integrated` remain separate later work.
