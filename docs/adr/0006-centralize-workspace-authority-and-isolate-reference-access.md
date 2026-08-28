# Centralize Workspace authority and isolate Reference access

Status: Accepted

Date: 2026-08-25

## Decision

One deep Workspace module is the sole reader and publisher of Workspace Authority while retaining the existing `attempts/`, `steps/`, `cycles/`, `final/`, and index layout. A separate Agent Surface exposes a small set of safe CLI and MCP operations backed by the same handler. A Modeling Agent understands the reconstruction lifecycle but does not see authority paths, staging, Git/LFS, validation, recovery, raw or canonical PLY files, or equivalent reference representations.

The Agent writes only its job-private candidate work area and observes the Canonical Reference through a bounded Reference Capability owned outside the Agent execution. The first Agent Surface reuses the existing Workspace operations behind a safety facade rather than introducing a new intent state machine, storage format, transaction log, Merkle structure, or compatibility framework.

After pilot execution reaches a terminal state, the Workspace module performs one complete validation and compiles a reusable closed Terminal Validation bundle containing the validated graph, review facts, objective evaluation facts, and exact content manifest. The Workspace copy does not authenticate that bundle: compilation returns a stable terminal identity covering the bundle, and the trusted outer supervisor retains that identity and bundle out of band. Verification requires the expected identity and fails closed on absence or mismatch; it does not rerun complete validation or use Git. Workspace persistence, handoff storage, and crash retry belong to the outer runner. A hard crash may cause another compilation; exactly-once means one validator call per successful compilation. Transfer verifies the manifest while moving bytes; review consumes retained facts instead of rerunning the complete Workspace validator. Historical Workspace readability is not a product requirement.

The first vertical slice must complete one synthetic reconstruction through the Agent Surface, prove that the simulated Agent cannot resolve raw or canonical reference files or Workspace Authority paths, publish through the retained Workspace layout, produce one Terminal Validation Result, and let review consume it without invoking the complete validator or reconstructing the Workspace graph. The result carries existing objective facts needed by evaluation, but does not introduce a scoring framework. CLI and the minimal Agent-facing MCP adapter share one handler.

Development occurs in isolated worktrees and lands serially into `develop` after validation and independent review.

## Consequences

The first implementation concentrates existing behavior behind one interface and deletes duplicated Workspace interpretation from runner and review. Existing `workspace_core.py`, atomic staging, recovery, Git/LFS publication, and full validation remain internal until migration or profiling justifies extraction. Reference isolation must be enforced by the outer process and filesystem capability seam; read-only mounts and source scans are insufficient. Review verdicts and evaluation policy remain outside Workspace Authority. New generalized receipt chains, event storage, database state, arbitrary geometry-query frameworks, CVM or paid pilots, S3 publication, production deployment, and historical-output cleanup are out of scope for the first vertical slice.

## Accepted Addendum — 2026-08-25

The following architecture decisions are recorded as part of this ADR without changing the earlier Decision or Consequences text.

1. Landing remains serialized. Correctness work may replace or delete an
   overbuilt mechanism; it does not have to preserve accidental complexity
   until a later cleanup phase. Deepening is performed only for a proven
   remaining Authority bypass, followed by deletion/integration.

2. [[Trusted Candidate Execution]] absorbs build, measurement, preview, and
   diff. Its fixed tools are shipped once as a read-only release subset. No
   separate Agent build, Agent measure, Agent preview, Agent diff, per-pilot
   tool cache, or tool lease is introduced.

3. [[Terminal Validation Handoff]] remains runner-owned and travels on its own independent trust lineage. No signatures, KMS integration, or receipt framework is introduced to authenticate the bundle.

4. Bundle and release materialize an explicit five-file [[Agent Source
   Projection]] with an exact manifest. The runner verifies that installed
   projection; it does not rebuild it or mount a complete installed skill
   tree.

5. The terminal compiler emits a consumer-ready [[Workspace View]]. Full-audit and default review share that compiler. [[Consumer Verdict]] remains outside Workspace Authority.

6. MCP transports share one session module, and Reference Observation policy and identity have one contract source, so that transport variation cannot diverge from a single behavioral contract.

7. Linux `bwrap` is the formal isolation and publication gate. macOS may run
   provider-free development tests. Unsupported publication platforms,
   including Windows, fail before compilation or filesystem mutation; no
   production-only compatibility framework is required for them.

8. Correctness Phase D binds the production Reference Capability to exactly the Workspace [[Canonical Reference]] via a Workspace-derived [[Reference Binding]] proven before the Agent Surface starts. Ambient `AGENT_REFERENCE_PATH` overrides are removed from production; test-only injection uses the pre-existing internal dependency seam.

9. [[Candidate Runtime]] is the common execution identity for Agent-authored
   canonical builds and the registered rebuild that publishes Final Delivery.
   By default, the Agent Surface runner starts from the repository `.venv`
   used to materialize Candidate Runtime. An explicit `PYTHON_BIN` remains an
   operator override; if it diverges from the recipe runtime, replay fails
   closed. Finalization does not weaken the recipe contract or silently fall
   back to an ambient system interpreter.
