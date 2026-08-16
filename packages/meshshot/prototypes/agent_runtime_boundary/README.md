# Agent runtime boundary prototype (THROWAWAY)

Question: what is the smallest public seam between an OCI Agent container and
the outer formal-job authority that can replace host-bwrap without transferring
Docker, browser, network, or cross-job authority to the Agent?

This directory is decision evidence for `SAR-003`, not production runtime code.
It does not modify the formal runner and does not establish **Agent Runtime
Verified**.

## Decision

Adopt a two-stage, outer-owned protocol:

1. The outer authority admits the exact Agent OCI manifest digest, image-config
   digest, runtime manifest, Source Snapshot, input, and Broker-authority
   identities. It compares both OCI identities with outer `image inspect`
   evidence, then creates one inert container by immutable reference with a
   read-only root, read-only
   source/input/control mounts, `--network none`, no capabilities, no Docker
   socket, and job-private writable home/cache/tmp/work/output mounts.
2. The outer authority inspects the returned immutable container ID and exact
   owner/job labels before it starts that ID. A name is never lifecycle or
   cleanup authority.
3. The fixed image entrypoint is the only process allowed before release. It
   rechecks the bound job/nonce/digests (including the image-resident runtime
   manifest), read-only surfaces, writable allowlist,
   browser/Docker denial, zero external route, and job-private Broker handshake.
   The outer gives a random challenge to the entrypoint; the Broker returns an
   HMAC bound to that challenge and complete immutable job identity. The HMAC
   secret is supplied by the outer authority only to that job's Broker and is
   never mounted into the Agent. The entrypoint relays the proof over the
   attached container's protocol-only stdout and waits for release on stdin.
4. Only an exact accepted proof releases the already-fixed workload. The
   entrypoint supervises that workload, publishes an identity-bound terminal
   proof and output digest over the same attach channel, and waits for the outer
   acknowledgement. Workload stdout/stderr are files in job-private output, so
   they cannot impersonate protocol records.
5. Workload success is provisional until terminal publication, exact-ID removal,
   owner-label absence, and private-tree absence all succeed. Cleanup or retained
   resource proof dominates workload success.

The public protocol is deliberately smaller than the existing Browser Sidecar
implementation. It reuses that implementation's exact-ID ownership, proof-only
terminal publication, failure precedence, and absence rules, but does not copy
the host-bwrap machinery into the container.

## Files

- `Dockerfile`: OCI wrapper for an already admitted browser-free Agent runtime
  base, supplied only by digest.
- `entrypoint.py`: fixed two-proof gate; it never receives the Broker HMAC
  secret, calls Docker, or launches a browser.
- `contract.py`: single shared immutable identity, tree-digest, and Broker-MAC
  authority copied into the image.
- `boundary.py`: executable outer lifecycle with injectable adapters plus the
  adversarial RED/GREEN harness.
- `scripts/pilot/browser_surface.py`: the existing formal descriptor/no-follow
  scanner is copied into the image; this prototype does not weaken it to names.
- `tests/python/packages/meshshot/test_agent_runtime_boundary_prototype.py`:
  provider-free production-shaped lifecycle tests.
- `evidence-summary.json`: committed result of the deterministic matrix and the
  current Colima limitation.

## Run

```sh
./.venv/bin/python packages/meshshot/prototypes/agent_runtime_boundary/boundary.py matrix
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

The executable adapter tests establish the SAR-003 public lifecycle and reuse
the formal browser scanner's renamed/distro/product/ELF-marker semantics. They
do not establish the complete SAR-005 scanner, image, SBOM, Colima, CVM, or
receipt verification set.

## Rejected alternatives

- Docker socket or Docker CLI inside the Agent: collapses outer lifecycle and
  cleanup ownership.
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
