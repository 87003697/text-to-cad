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
