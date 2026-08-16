# Sealed Agent runtime implementation ticket graph

Status: implementation queue; all listed outputs remain unimplemented until
their ticket lands and passes its stated evidence.

The reviewed architecture decisions are complete. These tickets convert them
to production code without reopening the public seams in
[the implementation specification](sealed-agent-runtime-implementation-spec.md).
The operational handoff is the
[copyable implementation goal](sealed-agent-runtime-implementation-runbook.md).

| ID | Deliverable | Depends on | Required evidence |
|---|---|---|---|
| SAI-001 | Canonical evidence kernel, strict schemas, graph validator, lifecycle dominance and tombstones | none | adversarial malformed/duplicate/cascade vectors; no second encoder |
| SAI-002 | Verification-plan and Cup capability manifests; provider-free durable Cup golden fixture and numeric-only route inspection | SAI-001 | exact manifest equality and router fixture tests |
| SAI-003 | Browser-free project closure: split meshshot Broker client, build meshscope cp312 native wheel, vendor canonical implicit subset | SAI-002 | wheel/ELF audit, browser import/executable/cache denial, Cup imports |
| SAI-004 | External-byte admission for Noble/debs, Python wheels, Node 24.13.0 and Codex 0.147.0 | SAI-001 | immutable mirror metadata, hashes, signature/checksum facts without overstated provenance, Node-absent Codex smoke |
| SAI-005 | Network-disabled deterministic OCI builder; production image-resident fixed entrypoint; runtime/Cup manifests; external SBOM and browser-inventory/receipt artifacts | SAI-003, SAI-004 | two byte-identical builds; exact entrypoint/config/runtime-manifest identity; gzip blob plus uncompressed DiffID closure; external post-manifest inventories |
| SAI-006 | Execution Source Snapshot builder and immutable publication/visibility contract | SAI-001 | no-follow manifest, exact digest/count/size, S3 exact-version and Mac visibility checks |
| SAI-007 | Candidate descriptor, artifact supply/provision, final-lock finalizer and admitted OCI importer | SAI-001, SAI-005 | non-circular candidate-to-Verified-to-final-lock chain, portable-to-local identity, inspect-before-start, `--pull=never` |
| SAI-008 | CAS promotion, deterministic reconciliation, predecessor and rollback module | SAI-007 | bootstrap/later CAS, lost-response, mount-failure, unfreeze and fresh rollback drills |
| SAI-009 | Production outer Agent execution supervisor replacing host bwrap; consumes and rechecks the SAI-005 entrypoint through the SAI-007 candidate/lock | SAI-006, SAI-007 | full SAR-003 adversarial suite, fixed-entrypoint substitution denial, plus one real Colima container lifecycle |
| SAI-010 | Job-private dual-homed Venus Proxy capability and provider-free mock conformance | SAI-001, SAI-009 | per-job internal Agent network, Proxy-only allowlisted egress, namespace/route/DNS/firewall identity, 48-request/time/token ceilings, cross-job denial, cleanup/absence, zero real dispatch |
| SAI-011 | Colima/CVM typed evidence producers and 15-node orchestrator, including validation of SAI-005 external SBOM and browser artifacts | SAI-002, SAI-005, SAI-006, SAI-009 | candidate-bound identical-plan evidence, existing typed `sbom`/`browser-deny` nodes, canonical Verified root, then final lock |
| SAI-012 | Production four-active FIFO admission and separate real isolation qualification | SAI-009, SAI-011 | four real containers, zero-allocation fifth queue, failure isolation, resource absence; no Verified-root rewrite |
| SAI-013 | End-to-end supply, promotion, reconciliation and rollback acceptance | SAI-008, SAI-011, SAI-012 | exact S3/CVM/Mac receipts; current plus distinct predecessor retained |
| SAI-014 | Provider-free production pilot integration with Agent + Venus Proxy + Broker + Sidecar | SAI-010, SAI-013 | fixed Source Snapshot/input, full Gate terminal/cleanup receipts, mock upstream and zero provider dispatch |
| SAI-015 | Paid `cup_cup_033` pilot and retained review loop | SAI-014 | executable pre-dispatch cost admission; at most 20 model-reaching jobs and USD 1000 total; exact Formal receipt or closed failure |

## Execution groups

Only one group is active at a time. Inside a group, lanes may run concurrently
only in isolated worktrees with stable ticket owners.

1. `G1 foundation`: SAI-001.
2. `G2 manifests`: SAI-002 and SAI-006 may run in parallel after SAI-001.
3. `G3 dependency closure`: SAI-003 and SAI-004 may run in parallel; they own
   project packaging and external admission respectively.
4. `G4 artifact`: SAI-005 owns the image entrypoint and raw post-manifest
   inventory artifacts; it does not implement the SAI-009 supervisor or
   SAI-011 typed evidence nodes.
5. `G5 supply and execution`: SAI-007, then SAI-008 and SAI-009 may run in
   parallel because promotion and execution own separate modules.
6. `G6 provider and verification`: SAI-010 and SAI-011 may run in parallel
   after their dependencies. SAI-011 publishes Verified and finalizes the lock
   without waiting for the separate concurrency qualification.
7. `G7 concurrency`: SAI-012.
8. `G8 release drill`: SAI-013.
9. `G9 pilot integration`: SAI-014.
10. `G10 paid acceptance`: SAI-015.

Every ticket receives Standards and Spec review from its fixed merge base.
Corrections remain with the same owner and use new commits. A group is not
complete until all ticket worktrees are clean, reviewed, ancestry-checked and
integrated serially into the planning integration branch.

## Budget and stop gates

SAI-001 through SAI-014 are provider-free. Downloads, image builds, S3, Colima
and CVM operations may incur ordinary infrastructure cost but must not dispatch
the model. SAI-015 alone may consume the user-authorized paid allowance:

- maximum 20 model-reaching jobs across all attempts;
- maximum USD 1000 in total;
- maximum 45 minutes and 48 upstream model requests per job;
- every dispatch that may have reached the model counts, even if telemetry or
  the terminal receipt is missing;
- no automatic retry; any retry has a fresh handle and ledger entry.

“No automatic retry” means no automatic rerun of a job/handle. The Proxy may
perform only policy-bounded upstream transport attempts. Every attempt,
including an ambiguous may-have-reached attempt, consumes one of the 48 request
slots and its full token/cost reservation. It never changes the job handle.

Before each SAI-015 dispatch, the proxy admits an immutable pricing and token
cap policy. Authorization requires either trusted cumulative USD telemetry or
a conservative reservation computed from the exact rates for every token class
multiplied by proxy-enforced hard input/output/cache token ceilings across all
remaining requests. The ledger reserves this worst-case amount before start;
an unresolved reservation is never released. If neither proof is available,
the permitted dispatch count is zero.

Stop before dispatch if spend telemetry is unavailable and another attempt
could exceed the ceiling. Stop on identity drift, non-clean worktree, failed
absence proof, ambiguous S3 channel state, missing Mac visibility, CVM disk
pressure, credential leakage, or a disagreement between the reviewed contract
and implementation.
