# Sealed Agent runtime implementation specification

Status: implementation-ready design; no production runtime or verification claim.

This document turns the reviewed SAR-001 through SAR-007 decisions into the
implementation boundary for the first sealed Agent runtime. It deliberately
does not claim `Agent Runtime Verified` or `Formal Pilot Integrated`.

## Product boundary

The first release runs the `cup_cup_033` implicit-SDF route on `linux/amd64`.
It contains exactly the execution surface needed by that route:

- Python 3.12 on a digest-pinned glibc Ubuntu/Noble base;
- exact admitted NumPy, trimesh, Pillow, meshscope/VoxBlame and browser-free
  meshshot Broker client bytes;
- the canonical implicit-JS subset and its exact Node runtime;
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
manifest/config, runtime manifest, Cup capability manifest, build-input set,
verification plan and admitted dependencies. The Source Snapshot is a separate
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
admit_codex(retrieval, archive, executable, signature_bundle,
            signature_policy, trust_anchor_approval, tuf_acquisition,
            trusted_clock, verifier, trusted_root) -> codex-admission evidence
```

SAI-004 owns this producer and the closed Codex signature-policy and
signature-verification receipt schemas. It consumes only mirrored bytes and
exact acquisition receipts. It must not use an ambient Sigstore cache, download
trust material during an image build, accept a wildcard certificate identity,
or substitute a verifier, bundle, trusted-root object, tag, ref, workflow, or
commit. The producer validates those schema-neutral documents and computes
their digests through the one canonical JSON seam before constructing the typed
`codex-admission` evidence child.

The bootstrap is the versioned SAR-004 trust-anchor approval provisioned
through the independently authenticated text-to-cad release-input channel. A
same-origin verifier checksum, TUF-object digest, or release-asset hash is not
bootstrap authentication. The producer validates the closed trusted-clock and
TUF-acquisition receipts, persists accepted time and role versions, and rejects
clock rollback, role rollback, same-version digest substitution, frozen or
expired metadata, and any offline-expiry grace. The fixed timestamp metadata
expires at `2026-08-23T01:53:11Z`; at or after that instant this policy is
inadmissible until a newly reviewed policy pins a fresh TUF-verified closure.

For Codex 0.147.0, the fixed Sigstore bundle signs the single extracted
`x86_64-unknown-linux-musl` executable. The producer separately proves that the
fixed archive contains exactly that one regular member with the expected name,
byte length, and digest and no link or traversal. It records and enforces the
negative archive verification control; it never calls the archive signed or
allows a valid executable signature to stand in for the archive-member binding.
The policy and receipt use the identical closed `archive` projection. The
formal receipt also copies the independently observed annotated tag object and
peeled commit from the closed authenticated retrieval receipt and checks them
against the certificate workflow ref/SHA and policy; policy expectations alone
are not an observation.
The committed research proof fixes the policy inputs but is not itself an
admission result: immutable mirroring, ELF closure, Node-absent and
noninteractive smoke, and the complete successful evidence child remain
mandatory.

### Artifact builder

```text
build(admitted_build_input_set) -> candidate OCI closure + build receipts
```

It consumes only admitted, locally mirrored bytes, performs a network-disabled
build, emits deterministic OCI image-layout bytes, runtime and Cup manifests,
SBOM and browser-deny evidence, and never reads the execution Source Snapshot.

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
fixed entrypoint. The entrypoint validates namespaces and a challenge-bound
Broker HMAC before releasing the immutable workload. It supervises the whole
process group and publishes terminal evidence only after descendant absence.

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
  cup-capability-manifest.json
  implicit-runtime-manifest.json
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
