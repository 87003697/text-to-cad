# Sealed Agent runtime implementation specification

Status: superseded; no CAD workload is admitted by this design after the
retirement of its original geometry backend.

This document turns the reviewed SAR-001 through SAR-007 decisions into the
implementation boundary for the first sealed Agent runtime. It deliberately
does not claim `Agent Runtime Verified` or `Formal Pilot Integrated`.

## Product boundary

There is currently no sealed CAD runtime release target. The reusable
`linux/amd64` infrastructure described below is retained only as design
reference for source snapshots, evidence, admission, and isolation:

- Python 3.12 on a digest-pinned glibc Ubuntu/Noble base;
- exact admitted NumPy, trimesh, Pillow, meshscope/VoxBlame and browser-free
  meshshot Broker client bytes;
- exact Codex CLI 0.147.0 native `x86_64-unknown-linux-musl` bytes, admitted
  outside the image build and mirrored unchanged;
- git, git-lfs and a bounded allowlist of shell utilities.

It excludes build123d, cadquery-ocp/OCP, cadpy, FreeCAD, ROS/MoveIt,
Playwright, Chromium, browser caches and a Docker socket. Direct
`$cad snapshot` remains out of scope because it currently imports Playwright
and starts Chromium directly. Preview is only the registered, job-private
Broker/Sidecar route.

The deployment therefore has two heavy images, Agent and Sidecar. Broker is a
separate lightweight authority artifact/process and remains a distinct role;
it must not be folded into the Agent or confused with the sealed Agent client.

## Identity split

The Agent Runtime Artifact is long-lived and identified by its canonical OCI
manifest/config, runtime manifest, build-input set, and admitted dependencies.
The Source Snapshot is a separate
execution-scoped, read-only project artifact. A source edit does not rebuild or
rename the Agent image.

The only public supply authority is the canonical Agent Runtime Lock digest.
Tags, local names, archive filenames, a Git revision alone, and an image's own
self-report are not authority.

Verification bootstraps from a separate immutable Agent Runtime Candidate
Descriptor containing every final lock field except `verification`. Only the
verification orchestrator and its target provisioner may consume it. Once the
candidate passes the exact dual-environment graph, the finalizer adds the
Verified root reference and emits the final lock. The candidate is never a
channel value, rollback target, general execution authority or paid-pilot
authority.

The exact receipt and supply contracts are normative:

- [Cup capability surface research](../research/sealed-agent-runtime-cup-capability-surface.md)
- [offline dependency closure research](../research/sealed-agent-runtime-cup-dependency-closure.md)
- [Codex artifact admission research](../research/sealed-agent-runtime-codex-artifact.md)
- [verification receipt contract](agent-runtime-verification-receipt.md)
- [supply, lock, promotion and rollback contract](agent-runtime-supply-lock-and-rollback.md)
- [implementation ticket graph](sealed-agent-runtime-ticket-graph.md)
- [copyable implementation goal](sealed-agent-runtime-implementation-runbook.md)
- [SAR-003 boundary decision](../../packages/meshshot/prototypes/agent_runtime_boundary/README.md)
- [SAR-007 deterministic concurrency evidence](../../packages/meshshot/prototypes/agent_runtime_boundary/concurrency-evidence-summary.json)

## Deep modules and public seams

The implementation should create `scripts/pilot/agent_runtime/` as the
orchestration package and `packages/agent_runtime/` as the image/build-input
package. CLI wrappers remain under `scripts/pilot/`; callers must not import
individual receipt producers or storage adapters.

### Canonical evidence kernel

Small interface, large hidden responsibility:

```text
parse_canonical_json(bytes) -> immutable canonical JSON value
canonical_json_bytes(value) -> bytes
canonical_json_digest(value) -> sha256

parse_strict(kind, bytes) -> typed document
canonical_bytes(document) -> bytes
digest(document) -> sha256
validate_graph(root, children) -> verified | closed failure
```

The first three functions are the only lower-level canonical JSON seam. They
are schema-neutral and enforce the same byte/value grammar for every caller:
one canonical UTF-8 JSON value; no byte-order mark, duplicate object key,
trailing value, float, non-finite number, or integer outside signed 64-bit
range; ASCII object keys and string values; at most 1 MiB of input or output;
and maximum nesting depth 64. Objects sort keys by ASCII code point, arrays
retain their specified order, and the encoding has no insignificant whitespace.
Input may have exactly one trailing newline, which is removed before canonical
byte comparison; output and digests never include it. The parsed mapping and
array graph is recursively immutable.

`parse_canonical_json` proves only that the bytes satisfy this canonical JSON
grammar and returns that immutable value. It makes no schema, evidence, supply,
identity, state, or validity claim. `canonical_json_bytes` rechecks the same
value/depth/ASCII/integer/size grammar and is the one encoder.
`canonical_json_digest` is exactly `sha256:` plus the lowercase SHA-256 of
`canonical_json_bytes(value)`; it never hashes caller-supplied serialization.

The existing `parse_strict`, `canonical_bytes`, and `digest` functions remain
typed evidence wrappers. They must use exactly the three primitives above:
`parse_strict` parses through `parse_canonical_json`, selects one closed
`EvidenceDocument` kind, validates its exact schema, and only then returns a
typed document; `canonical_bytes` revalidates the typed evidence schema before
calling `canonical_json_bytes`; and `digest` delegates to
`canonical_json_digest` after that same typed validation. The kernel additionally
owns the 15-node DAG, child state/cascade rules, lifecycle failure dominance,
tombstones, and proof-only publication.

Every non-evidence producer must likewise run its own exact closed schema
validator before emitting or digesting a value through the lower-level seam.
The required order is parse or construct, validate the producer-owned closed
schema, then canonicalize or digest. A raw canonical JSON value, including a
valid supply document, can never enter the 15-node evidence graph; graph APIs
accept only the closed typed evidence wrappers. A consumer rejects an unknown
evidence kind, an untyped/raw graph node, schema validation after emission or
digest, a digest over input bytes rather than canonical output, and any private
or second JSON encoder. Canonical syntax success must never be reported as
schema or evidence success.

This schema-neutral seam allows SAI-002, SAI-006, SAI-007, and future producers
to share one encoder without adding their schemas to SAI-001's evidence-kind
registry or transferring schema ownership. SAI-002 owns the exact closed
verification-plan and Cup capability manifest schemas. SAI-006 owns only the
exact closed Execution Source Snapshot local manifest and lock schemas and that
snapshot's own publication and visibility receipt schemas. SAI-007 owns the
exact closed Candidate Descriptor; artifact and candidate publication,
provision, and import receipts; final Agent Runtime Lock and finalizer; and
downstream supply schemas assigned to it by the fixed supply contract. Future
producers own their schemas only through their assigning ticket and normative
specification, not by using these canonical primitives.

Each owner may close its assigned representation details; none may improvise or
weaken artifact identity, authority, state-transition, failure/retry,
publication/visibility, promotion, rollback, or reconciliation semantics. No
producer may implement another canonical encoder.

### External-byte admission

```text
admit_codex(retrieval_metadata, archive, executable, signature_bundle,
            signature_policy, trust_anchor_approval,
            verifier, trusted_root) -> codex-admission evidence
```

SAI-004 owns this producer and the closed Codex signature-policy,
signature-verification, and non-authoritative retrieval-metadata schemas. It
consumes only mirrored bytes fixed by the exact versioned OOB approval. The
retrieval document records provenance but is not publisher authentication and
cannot authorize or substitute bytes. The producer must not use an ambient
Sigstore cache, download trust material during an image build, accept a
wildcard certificate identity, or substitute a verifier, bundle, trusted-root
object, ref, workflow, or commit. It validates those schema-neutral documents and computes
their digests through the one canonical JSON seam before constructing the typed
`codex-admission` evidence child.

Codex 0.147.0 uses Cosign's legacy bundle format. The producer deterministically
extracts the exact Fulcio root/intermediate and Rekor/CTFE keys from the approved
`trusted_root.json`, checks their four approved digests, and invokes the exact
Cosign 2.4.1 bytes with explicit CA/key inputs, `--offline`, an empty cache and
denied network. It verifies the Fulcio chain, embedded SCT, artifact signature,
identity claims and Rekor Signed Entry Timestamp/body binding. The SET is an
inclusion promise, not a Merkle inclusion proof; the receipt and public claim
must preserve that distinction.

The bootstrap is the exact version-2 SAR-004 approval object printed in the
reviewed receipt contract. It binds every accepted archive, executable, bundle,
verifier, checksum, TUF metadata, and trusted-root byte. A same-origin checksum,
retrieval observation, ambient cache, or runtime-updated TUF state cannot amend
that authority. Updating or revoking the byte set requires a reviewed new
approval and policy; the producer has no automatic trust-root update path.

For Codex 0.147.0, the fixed Sigstore bundle signs the single extracted
`x86_64-unknown-linux-musl` executable. The producer separately proves that the
fixed archive contains exactly that one regular member with the expected name,
byte length, and digest and no link or traversal. It records and enforces the
negative archive verification control; it never calls the archive signed or
allows a valid executable signature to stand in for the archive-member binding.
The policy and receipt use the identical closed `archive` projection. The
formal receipt verifies the repository, workflow, ref, commit, and trigger
claims carried by the signed Sigstore bundle against the exact policy; release
API or Git tag observations are research provenance, not trust authority.
The committed research proof fixes the policy inputs but is not itself an
admission result: immutable mirroring, ELF closure, Node-absent and
noninteractive smoke, and the complete successful evidence child remain
mandatory.

### Artifact builder

```text
build(admitted_build_input_set) -> candidate OCI closure + build receipts
```

SAI-005 owns this producer and the production image-resident entrypoint. It
consumes only admitted, locally mirrored bytes, performs a network-disabled
build, and never reads the execution Source Snapshot. The exact OCI image
config has `Entrypoint` equal to the one-element array
`["/usr/local/libexec/text-to-cad-agent-entrypoint"]` and `Cmd` equal to the
empty array. SAI-005 fixes that regular executable's bytes, mode `0555`, and
SHA-256 in the runtime manifest; the same digest becomes the approved
`entrypointDigest` in the verification plan and both lifecycle subjects.

SAI-009 owns the outer execution supervisor and consumes that SAI-005 output
through the SAI-007 candidate/lock. It may pass the immutable workload and
job-private capabilities to the fixed entrypoint but may not generate, patch,
mount over, or select alternate entrypoint bytes. This direction is
SAI-005 -> SAI-007 -> SAI-009 and introduces no reverse dependency. The
throwaway SAR-003 prototype entrypoint is decision evidence only: SAI-005 must
implement the reviewed production contract in `packages/agent_runtime/` and
must not copy, vendor, import, or execute the prototype as production bytes.

The image-resident runtime manifest is the regular file
`/usr/share/text-to-cad/runtime-manifest.json`, mode `0444`, and is canonical
JSON with schema literal
`text-to-cad.agent-runtime-manifest/1` and exactly these top-level keys:
`schema`, `platform`, `entrypoint`, `programs`, `nativeLibraries`, and
`runtimeFiles`. `platform` is exactly
`{"architecture":"amd64","os":"linux"}`. `entrypoint` has exactly `path`,
`mode`, `bytes`, `digest`, and `argv`; its values are the fixed path/mode above,
the observed nonnegative byte length, its full SHA-256, and the exact
one-element image-config `Entrypoint` array.

`programs` is a path-sorted array whose entries have exactly `name`, `path`,
`version`, and `digest`. `nativeLibraries` is a path-sorted array whose entries
have exactly `path`, `soname`, and `digest`. `runtimeFiles` is a path-sorted
complete inventory of sealed runtime payload files except the runtime manifest
itself; entries have exactly `path`, `mode`, `bytes`, and `digest`. Every path
is absolute, normalized, unique, is not the manifest path above, and names one
regular file in the final rootfs; every mode is an unsigned integer containing
only permission bits, every byte count is nonnegative, and every digest is full
SHA-256. Symlinks, directories, devices, sockets, and other non-regular entries
are represented only by the OCI rootfs and are not permitted as manifest file
records. Every program, native library, and entrypoint file must also appear
with identical identity in `runtimeFiles`. The inventory boundary
is exactly the union of those referenced files and every regular file admitted
by `projectRuntimeArtifactSetDigest`; no file outside that union may appear.
`programs` contains every executable invoked by the entrypoint or immutable
workload, and `nativeLibraries` contains their complete resolved ELF
`DT_NEEDED` closure. OS/dependency package membership remains bound by the
dependency lock and external SBOM rather than being silently reclassified as a
project runtime artifact.

All three arrays reject duplicate paths, unknown keys, non-canonical ordering,
or a path/digest mismatch against the final rootfs. Program `name` and
`version`, and library `soname`, are non-empty ASCII strings observed from the
admitted bytes; they are not mutable labels. Empty arrays are permitted only
when their exact membership rule yields no entry, never as an unknown or
not-scanned sentinel.

The runtime manifest does not contain its own digest: it contains neither
`runtimeManifestDigest` nor any OCI index, manifest, config, layer, SBOM,
browser-inventory, receipt, candidate,
lock, or Verified-root digest. Its identity is the SHA-256 of its complete
canonical bytes, computed externally and then recorded in the OCI config label,
`org.text-to-cad.agent-runtime-manifest.digest`, and copied unchanged into the
candidate/lock, verification root, and typed evidence. The manifest is written
into the rootfs before the final layer/config/manifest digests are computed, so
it never hashes itself or a downstream container.

The portable image uses gzip-compressed OCI layers only, with media type
`application/vnd.oci.image.layer.v1.tar+gzip`, matching the fixed supply
contract. Each descriptor digest/size covers the exact gzip blob; the config
`rootfs.diff_ids` entry at the same ordinal covers the exact uncompressed tar
bytes. Deterministic gzip has MTIME zero, no original-name/comment/extra fields,
and one fixed compression implementation/level recorded in the build recipe.
Mixing uncompressed-layer media types, recompressing a layer, or equating a blob
digest with its DiffID is rejected. Two clean builds must reproduce index,
manifest, config, ordered gzip blob, and ordered DiffID bytes exactly.

After the final OCI manifest digest exists, SAI-005 produces the SPDX JSON 2.3
SBOM plus browser inventory/scan receipt as separate canonical artifacts. They
are not copied into any image layer. Their digests are absent from every image
layer, the runtime manifest, and OCI config, preventing image/SBOM/browser
self-reference. They bind the already-final `agentImageManifestDigest`.
SAI-005 owns these artifact producers and their raw closed outputs; SAI-011
owns the typed `sbom` and `browser-deny` evidence producers that validate those
outputs and insert the existing two nodes into the 15-node graph. Neither
ticket may create an additional node or move either external artifact into the
image.

The SBOM producer and typed validator share no ambient SPDX registry. Each
consumes the admitted local
`text-to-cad.spdx-license-catalog/1` version 3.28.0 bytes fixed by the receipt
contract (12,540 canonical bytes,
`sha256:5865e5d860a9278d30d22eb5522952f85eb620b2a6a3e68e02a5df7449835a31`),
recomputes the catalog independently, and applies the exact License List,
Exception List, `WITH`, `LicenseRef`, and `NOASSERTION` rules there. The
SAI-005 build-input closure binds this catalog and its admitted source wheel;
SAI-011 validates against the same literal catalog identity. Neither performs a
network refresh or imports a host-installed catalog.

### Artifact supply

```text
publish(candidate) -> immutable object receipt
provision(candidate_or_final_lock, target, purpose) -> host-local identity receipt
finalize(candidate, verified_root) -> final Agent Runtime Lock
promote(lock, expected_channel) -> channel terminal or reconciliation authority
rollback(lock, expected_channel) -> fresh promotion result
```

S3 and Docker are adapters behind this interface. Provisioning imports the
portable OCI closure through an admitted outer importer, then independently
records the full host-local image ID/config. Execution uses that exact local ID
with `--pull=never` after immediate reinspection; it never attempts to execute
the portable manifest digest as a Docker-local reference.

### Agent execution supervisor

```text
execute(subject, purpose, source_snapshot, input, provider_capability,
        broker_capability, workload) -> terminal receipt
```

`subject` is a final lock for normal/formal work. The only exception is a
candidate with `purpose=artifact-verification`, the exact verification plan,
mock/no provider capability, and no channel authority.

The outer supervisor allocates fresh authority, validates all immutable
identities, creates an inert container, admits the returned exact container ID
before obtaining delete authority, verifies inert configuration, then starts a
fixed entrypoint whose path/config/digest are SAI-005 image outputs repeated by
the candidate, lock, verification plan, and lifecycle subject. SAI-009 only
consumes and rechecks that identity. The entrypoint validates namespaces and a
challenge-bound Broker HMAC before releasing the immutable workload. It
supervises the whole process group and publishes terminal evidence only after
descendant absence.

The provider capability is a job-private Proxy container. The Agent and Proxy
share one newly created Docker `--internal` network containing no other job;
the Agent has no other interface, route or DNS. The Proxy alone is dual-homed
to a separately controlled egress network whose allowlist is the exact Venus
endpoint set. It holds provider credentials outside the Agent, enforces the exact
model/route plus request, time and token ceilings, records whether a dispatch
may have reached the model, and denies direct provider/general network egress.
The Agent receives only a pinned Proxy container identity, internal IP/port and
one-shot capability; no credential, Docker authority, egress gateway or host
socket. Proxy and Agent network namespace/interface/route/DNS/firewall state,
cross-job rejection, both network removals, Proxy termination and absence are
mandatory evidence. Provider-free conformance replaces the egress peer with a
deterministic mock upstream; real Venus dispatch is forbidden until the final
paid ticket. This is a deliberate production refinement of SAR-003's
provider-free `--network none`; all SAR-003 ownership, release, cleanup and
absence rules remain unchanged.

Cleanup and absence are independent observations for the exact container,
owner-label inventory, Broker volume and private tree. Labels may prove absence
but may never authorize deletion. Host bwrap is replaced, not nested inside the
Agent container. The Agent receives neither Docker API nor CLI authority.

### Verification orchestrator

```text
verify(candidate, verification_plan, environment) -> child evidence set
assemble(colima_set, cvm_set) -> Agent Runtime Verified root
```

It fixes identical source/input/fixture/harness/entrypoint/schema/scanner bytes
for Colima and CVM, invokes internal producers, and exposes only the final root
or closed terminal failure. The 15 individual producers remain internal.

### Concurrency admission

```text
submit(immutable request) -> queued execution
```

The first release has a hard active cap of four and FIFO overflow. A queued
request contains bounded immutable metadata only. Authority, private trees,
volumes, processes and receipts are allocated only after admission. A slot is
released only after terminal publication, cleanup and independent absence.
Agent filesystems, homes, caches, tmp, outputs, Broker volumes/sockets,
Sidecars, secrets and receipt mappings are job-private. Immutable image layers
and validated content identities may be shared.

## Suggested file placement

```text
packages/agent_runtime/
  Dockerfile
  build-input.lock.json
  runtime-manifest.json
  browser-deny-policy.json
scripts/pilot/agent_runtime/
  contracts.py
  evidence.py
  builder.py
  supply.py
  authority.py
  supervisor.py
  verification.py
  admission.py
scripts/pilot/agent-runtime-{build,supply,verify,run}.py
tests/python/packages/agent_runtime/
tests/python/global/test_agent_runtime_*.py
```

The final names may change if the first implementation ticket finds a stronger
existing module boundary, but the public seams and ownership rules above may
not be weakened. The throwaway prototype under
`packages/meshshot/prototypes/agent_runtime_boundary/` is evidence for the
decision, not production code to vendor wholesale.

## Acceptance ladder

1. Strict unit and adversarial schema tests pass with no network/provider use.
2. Every external byte has an immutable admission record; the build succeeds
   with network disabled and regenerates the same OCI closure.
3. Browser-deny, SBOM, native dependency closure and exact Codex/Node/Python
   admission pass.
4. One real Agent container passes the SAR-003 lifecycle on dedicated
   `linux/amd64` Colima, including interruption and retained-resource cases.
5. The same immutable verification plan and artifact pass independently on CVM.
6. The canonical 15-node graph publishes one `Agent Runtime Verified` root and
   the candidate is finalized into the only executable Agent Runtime Lock.
7. Four real Agent containers then pass a separate first-release concurrency
   qualification; the fifth is FIFO queued with zero mutable pre-admission
   allocation. This is not a sixteenth graph node and does not rewrite the
   Verified root.
8. The job-private mock Venus proxy passes request/token/time enforcement,
   direct-egress denial, cross-job substitution and cleanup/absence tests.
9. Immutable supply, promotion, lost-response reconciliation and rollback are
   drilled against exact objects and fresh target-host receipts.
10. Only then may the production pilot integrate Agent + Venus Proxy + Broker +
   Sidecar and
   attempt paid Cup work. `Formal Pilot Integrated` requires its separate exact
   Gate and receipt; it is not implied by Agent Runtime Verified.

## Failure and retention

No automatic retry is allowed. A new attempt gets a new handle. Any identity,
canonicalization, browser-deny, cleanup, absence, visibility, reconciliation or
budget ambiguity stops dependent work. Failed evidence and owned resources are
retained unless the relevant contract proves safe cleanup. Historical image,
receipt and output deletion is future GC work requiring separate review and
destructive authorization.
