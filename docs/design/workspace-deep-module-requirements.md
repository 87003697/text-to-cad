# Workspace deep module requirements

Status: Accepted design input

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

The transfer path verifies the content manifest while moving files. Review verifies and consumes the retained result; it does not run the complete Workspace validator or reconstruct the graph. Review verdicts and evaluation scores remain Consumer Verdicts outside Workspace Authority.

## Compatibility and migration

- Historical Workspace readability is not required.
- Keep the current authority layout and behavior during the first slice.
- Introduce the Workspace module before deleting consumer logic.
- Cut consumers over one at a time with equivalence tests.
- Delete duplicated runner/review parsing only after the new path passes its fixed tests.
- Further incremental validation is permitted only after profiling demonstrates that publication-time validation is still a material bottleneck.

## Acceptance criteria

1. A synthetic reconstruction completes through the Agent Surface and publishes a valid Final Delivery through the retained Workspace implementation.
2. An adversarial simulated Agent cannot resolve or copy raw PLY, canonical PLY, captured originals, `reference.vbsvo`, Workspace Authority roots, or the outer Git repository.
3. Fixed Reference Observations remain usable from the isolated Agent execution.
4. Pilot terminal handling invokes the complete Workspace validator exactly once and produces a valid Terminal Validation Result plus exact content manifest.
5. Pilot review consumes that retained result and neither invokes the complete validator nor reconstructs the Workspace graph from authority directories.
6. The Terminal Validation Result exposes current objective evaluation facts without defining a new score or evaluation policy.
7. Focused Workspace, Agent Surface, runner, and review tests pass, followed by the symlink check, bundle freshness check, and installed-plugin smoke.
8. Independent code review reports no unresolved findings against this document and ADR 0006.

## Explicit non-goals

- New Workspace storage layout, transaction log, event store, database, Merkle tree, or general receipt framework.
- Historical Workspace compatibility or migration.
- A new evaluation or scoring system.
- Arbitrary geometry queries or a general mesh SDK.
- Broad decomposition of `workspace_core.py` before stable responsibilities emerge.
- CVM push/pull, paid pilot, S3 publication, production deployment, or cleanup of historical outputs.

## Blockers-first implementation tickets

| Ticket | Owned seam and principal paths | Depends on | Exit evidence |
|---|---|---|---|
| W1 — Workspace facade and terminal result | Workspace helper internals and CLI under `skills/mesh-to-cad/scripts/mesh-to-cad-workspace/`; focused Workspace tests | None | Existing mutations pass through one facade; Terminal Validation Result and manifest validate; existing Workspace tests pass. |
| W2 — Restricted Reference Capability | Reference-observation implementation in `packages/meshscope`; focused capability tests | Accepted observation contract | Fixed observations are deterministic and bounded; raw/export/arbitrary-query requests fail closed. |
| W3 — Agent Surface handler and adapters | New Agent Surface handler plus thin CLI/MCP adapters in the Mesh-to-CAD skill runtime | W1 interface and W2 contract | Shared handler drives the existing workflow; adapters contain no authority logic; closed errors and bounded state are tested. |
| W4 — Runner execution isolation | `scripts/pilot/runner.py`, pilot launcher, and focused runner/integration tests | W2 and W3 | Simulated Agent sees only candidate work and capability endpoints; adversarial path probes fail; synthetic workflow completes. |
| W5 — Review and evaluation-fact cutover | Mesh-to-CAD review compiler and focused pilot-review tests | W1 | Review consumes Terminal Validation Result, does not invoke full validation or rebuild the graph, and retains current output semantics. |
| W6 — Integration and deletion gate | Runner/review duplicates, bundle scripts, shipped plugin checks | W4 and W5 | Duplicated consumer interpretation is removed; focused suites, symlink check, bundle check, installed-plugin smoke, and independent review pass. |

W1 and W2 may be implemented in separate worktrees from the same verified `develop` base. W3 follows their frozen interfaces. W4 and W5 own disjoint primary files and may proceed independently after their prerequisites. W6 is serialized and performs the only cross-cutting deletion and shipped-surface gate.
