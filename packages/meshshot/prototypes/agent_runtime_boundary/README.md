# Agent runtime boundary prototype (THROWAWAY)

Question: what is the smallest public seam between an OCI Agent container and
the outer formal-job authority that can replace host-bwrap without transferring
Docker, browser, network, or cross-job authority to the Agent?

This directory is decision evidence for `SAR-003`, not production runtime code.
It does not modify the formal runner and does not establish **Agent Runtime
Verified**.

## Decision

Adopt an outer-owned allocation and two-stage execution protocol:

1. An authority-free immutable request contains no nonce, Broker secret, or
   challenge. The outer allocator generates all three with cryptographic
   randomness. A durable store atomically claims the complete identity and
   challenge; lifecycle execution consumes that claim once before create, so a
   prior valid identity/MAC cannot be replayed.
2. The outer authority admits the exact Agent OCI manifest digest, image-config
   digest, runtime manifest, Source Snapshot, input, fixed workload argv, and
   Broker-authority identities. It compares both OCI identities with outer
   `image inspect` evidence and validates the nonempty absolute workload argv
   against its digest before it publishes ready or creates anything. It then
   creates one inert container by immutable reference with a
   read-only root, read-only
   source/input/control mounts, `--network none`, no capabilities, no Docker
   socket, and job-private writable home/cache/tmp/work/output mounts.
3. The returned ID remains an untrusted candidate while the outer authority
   inspects it. Only an exact 64-hex ID plus matching owner/job labels creates
   exact-ID start/delete authority. A substituted foreign ID is never adopted
   or deleted. Labels are read-only inventory/absence evidence, never delete
   authority. Lost create output therefore fails closed and reports any
   owner-labelled residue without deleting by label or name.
4. The fixed image entrypoint is the only process allowed before release. It
   rechecks the bound job/nonce/digests (including the image-resident runtime
   manifest), read-only surfaces, writable allowlist,
   browser/Docker denial, zero external route, and job-private Broker handshake.
   The outer gives a random challenge to the entrypoint; the Broker returns an
   HMAC bound to that challenge and complete immutable job identity. The HMAC
   secret is supplied by the outer authority only to that job's Broker and is
   never mounted into the Agent. The entrypoint relays the proof over the
   attached container's protocol-only stdout and waits for release on stdin.
5. Only an exact accepted proof releases the identity-bound workload. The
   entrypoint starts it in a new process group, waits for the leader, and tests
   group absence. Any descendant residue triggers bounded `TERM`, then `KILL`;
   residue forces status 125 even if cleanup succeeds, and a group that remains
   after both bounds prevents terminal publication. Main-thread `SIGINT` and
   `SIGTERM` handlers are installed only for the workload interval, relay the
   signal to the complete process group, and are restored afterward. Interrupt,
   descendant, and group-absence state have explicit failure precedence.
   Terminal/output digest are
   published only after group absence. Workload stdout/stderr are files in
   job-private output, so they cannot impersonate protocol records.
6. Workload success is provisional until terminal publication, exact-ID removal,
   owner-label absence, Broker-volume absence, and exact job-private-tree absence
   all succeed independently. Cleanup never receives a source or user-data path
   outside the admitted owned job root. Cleanup failure or any retained-resource
   proof dominates workload success.

The public protocol is deliberately smaller than the existing Browser Sidecar
implementation. It reuses that implementation's exact-ID ownership, proof-only
terminal publication, failure precedence, and absence rules, but does not copy
the host-bwrap machinery into the container.

## Files

- `Dockerfile`: OCI wrapper for an already admitted browser-free Agent runtime
  base, supplied only by digest.
- `entrypoint.py`: fixed two-proof gate; it never receives the Broker HMAC
  secret, calls Docker, or launches a browser.
- `process_group.py`: bounded standard-library workload process-group
  supervision shared by the fixed entrypoint and executable tests.
- `contract.py`: single shared immutable identity, tree-digest, and Broker-MAC
  authority plus canonical workload environment copied into the image.
- `authority.py`: outer cryptographic allocator and durable atomic one-shot
  claim/consume store; Broker secret is never persisted or mounted in Agent.
- `boundary.py`: production-shaped outer lifecycle contract with injectable
  adapters and no canned matrix results.
- `scripts/pilot/browser_surface.py`: the existing formal descriptor/no-follow
  scanner is copied into the image; this prototype does not weaken it to names.
- `tests/python/packages/meshshot/test_agent_runtime_boundary_prototype.py`:
  provider-free production-shaped lifecycle tests.
- `tests/python/packages/meshshot/agent_runtime_boundary_matrix.py`:
  test/evidence-only scripted adapter, unsafe RED comparator, and executable
  adversarial matrix CLI.
- `evidence-summary.json`: committed result of the deterministic matrix and the
  current Colima limitation.

## Run

```sh
./.venv/bin/python \
  tests/python/packages/meshshot/agent_runtime_boundary_matrix.py matrix
./.venv/bin/python -m unittest \
  tests.python.packages.meshshot.test_agent_runtime_boundary_prototype -v
```

The Dockerfile intentionally has no default base. A future real-image run must
pass an already admitted, locally present `linux/amd64` image by full digest and
must not pull:

```sh
docker build --pull=false --network=none \
  --build-arg AGENT_BASE_IMAGE='example.invalid/agent@sha256:<admitted-digest>' \
  --build-arg RUNTIME_MANIFEST_DIGEST='sha256:<admitted-manifest-digest>' \
  -f packages/meshshot/prototypes/agent_runtime_boundary/Dockerfile .
```

No such admitted Agent Runtime Artifact exists at this ticket boundary. Building
from a mutable tag or substituting the Browser Sidecar image would answer a
different question and is rejected.

The executable adapter tests mutate observations and failures behind the public
seam; they establish freshness/replay rejection, ordering,
candidate-versus-owned identity, immutable workload binding, signal relay,
release/terminal precedence, process-group handling, and four
independent cleanup/absence predicates. They also reuse the formal browser
scanner's renamed/distro/product/ELF-marker semantics. They do not establish the
complete SAR-005 scanner, image, SBOM, Colima, CVM, or receipt verification set.

## SAR-007 first-release concurrency decision (THROWAWAY)

`concurrency.py` and
`tests/python/packages/meshshot/agent_runtime_concurrency_matrix.py` extend the
same production-shaped SAR-003 lifecycle through injected adapters and real
private filesystem trees. This is source-level decision evidence, not a real
OCI run. It adopts the following first-release **Agent Concurrency Contract**:

- One admission controller has a hard active cap of exactly four Agent
  Executions. Larger mesh-to-cad batches remain in a FIFO queue. Capacity is
  released only after the complete lifecycle operation returns, including
  cleanup and independent absence observations; workload exit alone does not
  release a slot. Before admission, a queued item holds only its bounded
  immutable request metadata, execution ID, release control, and observation
  event. Authority claim, private filesystem tree, Broker volume, adapter, and
  processes materialize inside the active operation, never in the queue.
- Executions may share only immutable Agent image manifest/config, runtime
  manifest, Cup capability manifest, verification plan, Source/input content,
  Broker protocol definition, and fixed workload identities. Every admitted
  Execution receives a fresh job identity, owner nonce, Broker secret,
  challenge, private root/home/cache/tmp/work/output tree, Broker volume and
  socket authority, output subject, and terminal receipt mapping.
- Broker authority is reserved per job. Its job-private Broker process starts
  lazily only after admission, when the SAR-003 entrypoint reaches the mandatory
  Broker-authenticated preflight; it cannot wait for a residual preview because
  that proof gates workload release. The job-private Sidecar process starts
  later and only when that job emits a residual preview request. A queued job
  has neither process, while an admitted no-preview job has a Broker but no
  Sidecar. There is no global Browser pool and no cross-job Broker volume,
  secret, challenge, owner, process, or Sidecar ownership.
- The Agent never gets Docker authority. One job's failed cleanup or retained
  tree produces its own failed receipt and retained evidence; it cannot delete
  or rewrite the other jobs' resources or receipts.

The executable adapters start separate lightweight provider-free Broker and
Sidecar subprocesses with exact job/owner marker files. Cleanup terminates and
waits for each process independently with a bound, removes its private resource,
and proves both processes absent before admission capacity is released. A
`finally` path performs the same bounded stop if a test assertion itself fails.

The fifth-job case first holds four lifecycle operations at their terminal
boundary, proves the fifth request has no authority marker, job tree, Broker
volume, adapter/process, or receipt, releases exactly one slot only after that
job's processes and volume are absent, then proves the fifth materializes while
active without a peak above four. It finally completes all receipts and absence
checks. A second concurrent case leaves one failed job's real tree behind while
proving the other three trees and every subprocess absent.

The outer test supervisor independently digests the actual output tree, captures
the actual terminal protocol record, validates its observed identity and
`outputDigest`, and creates one job-private receipt outside the cleanup tree in
a supervisor-owned `0700` directory. This local harness proves exclusive first
publication (`O_EXCL`), exact content digest, and `0400` read-only mode. It does
not prove immutability against the same host UID. The supervisor never derives
the observed terminal identity from the expected identity. The closed
substitution matrix rejects cross-job owner, secret, challenge, Broker volume,
source, input, output path,
terminal subject, terminal output digest, and supervisor receipt path while
preserving the foreign receipt and output.

All test authority bytes are labelled
`SYNTHETIC_DETERMINISTIC_TEST_ONLY` and are pseudonymized in committed evidence;
the harness does not claim to sample cryptographic randomness. Five grants use
one persistent supervisor `FileAuthorityStore`. Replaying a consumed grant is
currently rejected after it enters an active slot but before any lifecycle or
private subprocess resource starts; it may briefly consume capacity. A future
implementation may add a sound non-consuming pre-admission check, but this
prototype does not pretend SAR-003's one-shot consume is such a check.
Production freshness remains the SAR-003 cryptographic allocator decision.

This evidence is also constrained by the SAR-005 verification authority design
at commit `16fb4288192e645b91b23e4f724b1420e085b24a`, specifically the exact
Source/input/config/Broker/workload/lifecycle-receipt subject equalities and
truthful failed/not-run states. That design is source-linked rather than
cherry-picked because this branch tests the earlier SAR-003 seam and does not
implement or publish the SAR-005 aggregate Verified receipt.

Run the concurrency matrix and focused tests:

```sh
./.venv/bin/python \
  tests/python/packages/meshshot/agent_runtime_concurrency_matrix.py matrix
./.venv/bin/python -m unittest \
  tests.python.packages.meshshot.test_agent_runtime_concurrency_prototype -v
```

The committed `concurrency-evidence-summary.json` is generated by the same
matrix CLI with `--output`. Its deterministic bytes and SHA-256 are recorded in
the ticket handback.

### Deferred real conformance

The source harness cannot establish real cgroups, CPU/RAM enforcement, Docker
attach behavior, Unix volume-socket behavior through Colima/CVM mounts, real
process signals, four actual containers, image-layer sharing, or host cleanup
after daemon/process failure. Those facts remain `NOT_RUN` until the exact
admitted image and SAR-005 verification plan run on both Colima and CVM. The
harness does not establish **Agent Runtime Verified** or Formal Pilot
Integrated. SAR-005/S3 content-addressed immutable receipt publication is also
`NOT_RUN`; local `O_EXCL` plus mode `0400` is not a substitute.

### SAR-007 rejected alternatives

- A configurable cap above four in the first release: unproven resource and
  failure interactions would be exposed to production batches. Batch size is
  decoupled from active capacity; larger batches queue.
- A shared writable home, cache, tmp, work, output, receipt, Broker volume,
  socket, secret, challenge, or Sidecar: each is either state or authority and
  permits cross-job substitution.
- A global warm Browser/Sidecar pool: it obscures job ownership and makes one
  failure's cleanup destructive to peers. Immutable image layers may be shared;
  live browser state may not.
- Releasing an admission slot at workload exit: terminal publication, cleanup,
  and absence proof would then run outside the active cap.
- Calling injected filesystem evidence OCI or Verified evidence: the missing
  daemon, namespace, cgroup, socket, and cross-environment observations are
  material, not wording details.

## Rejected alternatives

- Docker socket or Docker CLI inside the Agent: collapses outer lifecycle and
  cleanup ownership.
- Name/label deletion after lost or substituted create output: could delete a
  foreign object; only the admitted exact returned ID is destructive authority.
- Caller-selected nonce, secret, or challenge: makes a prior valid proof
  replayable; capability material must come from the outer allocator and claim.
- Entrypoint-only verification: permits a wrong image/source/configuration to
  start before the outer authority has checked the inert object.
- Outer-only verification: cannot prove the final namespace, Broker identity,
  browser absence, or mount state seen by the workload.
- Shared home, socket directory, or output tree: permits cross-job state and
  authority substitution.
- Host Unix sockets for the preflight/terminal channel: not portable through a
  Colima file share. Docker attach is already bound to the exact returned
  container ID and works without another Agent-visible capability.
- Host-bwrap inside or outside the Agent container: duplicates the OCI boundary
  and preserves host-runtime coupling rather than replacing it.
- A successful workload exit as the receipt: loses terminal publication and
  cleanup-failure precedence.
