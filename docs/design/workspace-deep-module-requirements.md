# Workspace deep module requirements

Status: **Implemented; exact-head integration gate passed 2026-08-28**

Date: 2026-08-25

## Outcome

Concentrate the existing Mesh-to-CAD Workspace behavior behind one deep module and place one separate Agent Surface in front of it. The first vertical slice succeeds when an isolated Agent can complete a synthetic reconstruction without access to raw reference data or Workspace Authority, the retained Workspace layout publishes normally, and review reuses the pilot's terminal validation rather than repeating it.

## Module seams

```text
Modeling Agent
  -> Agent Surface handler
       -> bounded Reference Capability
       -> Workspace module
            -> existing Workspace Authority implementation

Runner / Review / Evaluation
  -> Workspace module
       -> Workspace View or Terminal Validation Result
```

### Workspace module

The Workspace module is the only interface through which repository code may interpret or publish Workspace Authority. It retains the current `attempts/`, `steps/`, `cycles/`, `final/`, `step_index.json`, staging, recovery, Git, and LFS behavior. The existing implementation begins as an internal dependency rather than being rewritten or mechanically split.

The first interface needs only these capabilities:

- perform the existing Workspace mutations;
- return the current validated workflow state;
- run the complete terminal validation once;
- publish and read a Terminal Validation Result;
- expose the validated facts required by review and current evaluation.

Callers must not parse authority directories, rebuild the graph, repeat Workspace schema validation, or write authority files directly.

### Agent Surface

The Agent Surface is a separate module with one shared request handler and thin CLI/MCP adapters. It exposes the existing reconstruction workflow in a small safe facade:

- inspect current workflow state;
- start an Attempt;
- run a bounded candidate tool;
- submit Step 0;
- submit a repair result;
- select and finalize;
- request a bounded Reference Observation.

The Agent understands Attempts, Measured Steps, Repair Cycles, Repair Frontiers, and Selected Steps. It does not see authority paths, staging, recovery, Git/LFS, validator implementation, raw or canonical PLY, captured originals, or reference-derived storage such as `reference.vbsvo`.

The Agent writes only a job-private candidate area. Reference observations have fixed operations, fixed output profiles, bounded response sizes, and no raw mesh, export, arbitrary ROI, arbitrary camera, raycast, vertex, face, or filesystem-path access.

## Terminal Validation Result

After pilot execution reaches a terminal state, the Workspace module runs the existing complete validator once and atomically writes a closed, versioned result containing:

- Workspace identity and validator version;
- the validated Workspace graph;
- deterministic review facts;
- existing objective facts needed by evaluation;
- the identity of an exact content manifest.

The Workspace copy does not authenticate the bundle by itself. Compilation
returns a stable terminal identity covering the closed bundle and exact
manifest; the trusted outer supervisor retains that identity and bundle out of
band. Verification requires the expected identity, fails closed when it is
absent or mismatched, and does not rerun complete validation or use Git.
Workspace persistence and crash retry belong to the outer W4 runner. A hard
crash may cause another compilation; exactly-once means one validator call per
successful compilation. W4/W5 carries the bundle and identity to transfer and
review. This is an identity handoff, not a generalized receipt framework.

The transfer path verifies every retained file's size and SHA-256 before it
removes the source copy. Review verifies and consumes the retained result; it
does not run the complete Workspace validator or reconstruct the graph. Review
verdicts and evaluation scores remain Consumer Verdicts outside Workspace
Authority.

The handoff implementation stays small. The runner-owned handoff directory is
not visible to the Agent, so publication needs one host lock, one atomic
no-replace file publication, one locator, and crash reconciliation for that
pair. Platforms without those primitives fail before compilation or mutation.
Windows publication is not required.

## Compatibility and migration

- Historical Workspace readability is not required.
- Keep the current authority layout and behavior during the first slice.
- Introduce the Workspace module before deleting consumer logic.
- Cut consumers over one at a time with equivalence tests.
- Delete duplicated runner/review parsing only after the new path passes its fixed tests.
- Further incremental validation is permitted only after profiling demonstrates that publication-time validation is still a material bottleneck.

## Acceptance criteria

1. On Linux, one `bwrap` vertical slice completes Step 0, one Repair Cycle,
   finalization, terminal compilation, and review through the Agent Surface and
   publishes a valid Final Delivery through the retained Workspace
   implementation.
2. An adversarial simulated Agent cannot resolve or copy raw PLY, canonical PLY, captured originals, `reference.vbsvo`, Workspace Authority roots, or the outer Git repository.
3. Fixed Reference Observations remain usable from the isolated Agent execution.
4. Pilot terminal handling invokes the complete Workspace validator exactly once and produces a valid Terminal Validation Result plus exact content manifest.
5. Pilot review consumes that retained result and neither invokes the complete validator nor reconstructs the Workspace graph from authority directories.
6. The Terminal Validation Result exposes current objective evaluation facts without defining a new score or evaluation policy.
7. Permitted focused Workspace, Agent Surface, runner, and review integration
   checks pass, followed by the symlink check, bundle freshness check, and
   installed-plugin smoke. Under the current repository policy, unit tests are
   not added, modified, or run.
8. Independent code review reports no unresolved findings against this document and ADR 0006.

## Explicit non-goals

- New Workspace storage layout, transaction log, event store, database, Merkle tree, or general receipt framework.
- Historical Workspace compatibility or migration.
- A new evaluation or scoring system.
- Arbitrary geometry queries or a general mesh SDK.
- Broad decomposition of `workspace_core.py` before stable responsibilities emerge.
- General CVM/S3 synchronization, paid pilots, production deployment, or
  cleanup of historical outputs. The existing terminal-handoff transfer path
  is in scope only far enough to verify exact bytes before deleting its source.

## Implementation record and remaining acceptance

The original W1–W5 behavior and the R1–R5 reduction work are represented by
the current `develop` branch. This table is now an implementation record. R6
was the final gate and did not add a second framework around the implemented
module.

| Ticket | Status | Implementation | Exit evidence |
|---|---|---|---|
| R1 — Reduce terminal publication | **Implemented** | `81d25804` | Concurrent/retry coverage; unsupported hosts fail before mutation. |
| R2 — Ship trusted tools once | **Implemented** | `1bebe1e6` plus follow-up runtime fixes | Finalized publish-tree and installed-plugin execution use the shipped tool authority without source-checkout fallback. |
| R3 — Reduce Agent projection | **Implemented** | `e1365bba` plus type/nonregular-file hardening | Five-file inventory, digest, policy, and no-symlink coverage. |
| R4 — Verify transferred bytes | **Implemented** | `ed2850d7` | Missing, changed, extra, corrupt, and stale destinations fail before CVM cleanup. |
| R5 — Authority-boundary review | **Implemented** | `66bd1851` and the subsequent deep-module integration range | Review consumes Terminal Validation; the vertical slice uses the public Workspace/Agent seams. |
| R6 — Integration gate | **Complete — 2026-08-28** | `4e52c868`; final rebuild runtime alignment in `scripts/pilot/toys4k-pilot.sh` | Exact-head bundle, symlink, installed-plugin smoke, independent review, CVM authority, provider-free discovery, real Linux `bwrap` Agent Surface execution, Final Delivery, Terminal Validation Handoff, transfer, and `pilot-review` passed. |

R1–R6 are complete. R6 remained a verification and release gate and introduced
no second framework. Current cross-project progress is tracked in
[`docs/roadmap.md`](../roadmap.md).
