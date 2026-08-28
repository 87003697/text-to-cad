# Text-to-CAD Roadmap

Status: **Current authority for implementation progress**  
Updated: 2026-08-28
Integration baseline: `develop@4e52c868`

This file records current product progress. Historical plans and handoffs retain
design and execution evidence, but their old `Planning`, `not started`, or
`paused` labels do not override this roadmap. When status conflicts, use this
order:

1. current code, tests, and exact-head receipts;
2. this roadmap;
3. accepted design documents and ADRs;
4. historical plans, handoffs, and deprecated Wayfinder maps.

## Current outcome

The core Mesh-to-CAD reconstruction workflow is implemented. The remaining
mainline work is exact-head release closure and production-shaped acceptance,
not another Workspace or Agent-interface implementation program.

## Implemented

### Reconstruction workflow

- Three-skill mesh workflow: `mesh-inspect`, `mesh-compare`, and
  `mesh-to-cad`.
- Trellis2-style normalization, quantitative mesh comparison, preview, and
  heatmap support.
- VoxBlame surface localization, active repair depth, Repair Frontier, Repair
  Targets, Region Diff, Attempts, Measured Steps, Repair Cycles, selection,
  and Final Delivery.
- Reconstruction Spec support, enabled by default with an explicit opt-out for
  matched experiments.

### Workspace and Agent boundary

- One Workspace module owns Workspace Authority interpretation and mutation.
- A closed Agent Surface exposes opaque workspace, attempt, candidate, step,
  cycle, and delivery handles rather than authority paths.
- The bridge-mediated lifecycle covers workspace status, bounded reference
  observation, attempt start, registered candidate operations, Step 0,
  repairs, selection, and finalization.
- W1 owns repair-source seeding from the immutable parent Step into the fixed,
  isolated candidate work tree.
- Trusted providers own canonical build, preview, measurement, Region Diff,
  source-change evidence, and publication.
- The bounded Reference Capability exposes one approved summary observation
  without exposing raw reference meshes, `reference.vbsvo`, Workspace
  Authority, or arbitrary geometry queries.
- Adversarial and vertical-slice coverage exercises the real seven-intent and
  nine-call paths.

### Terminal evidence, transfer, and review

- Terminal Validation compiles once through the Workspace facade and is bound
  to an external expected identity.
- Pilot review consumes the verified Terminal Validation result instead of
  rebuilding the Workspace graph or repeating complete validation.
- Terminal content manifests bind retained experiment bytes.
- `cvm-pull` rejects missing, changed, extra, corrupt, and stale destination
  content before source cleanup.

### Shipped runtime surface

- The Agent receives an exact five-file source projection with bundle-time
  content policy and runtime identity verification.
- Trusted tools ship once as a fixed read-only installed-plugin subset; pilot
  execution does not fall back to the source checkout.
- Installed-plugin discovery and provider-free smoke paths exercise the real
  Codex plugin installation surface.
- The Development Browser Runtime uses a job-scoped, provider-free container
  with bounded MCP access and has local and CVM concurrency smoke evidence.

## Validation and release closure

These items do not require a new product architecture.

| Item | Status | Completion evidence |
|---|---|---|
| Exact-head integration gate | **Complete — 2026-08-28** | `4e52c868`: bundle freshness, symlink layout, installed-plugin smoke, independent code review, CVM authority publication, provider-free installed-plugin discovery, and the real Linux `bwrap` Agent Surface pilot all passed. Current repository policy prohibited unit-test execution; the gate used real entry-path integration evidence. |
| Publish integrated `develop` | **Complete — 2026-08-28** | `origin/develop` advanced through exact integration head `4e52c868` without a force push. |
| Current-stack paid acceptance | **Partial — airplane operational path complete with residual 2026-08-28** | Exact-head airplane handle `20260828-071800-workspace-agent-surface-paid-4e52c868/20260827-231618-airplane_airplane_016` completed with runner status 0, published Final Delivery and Terminal Validation Handoff, transferred successfully, and passed `pilot-review` with all 10 protocol checks observed. Reconstruction was `delivered_with_residual`: selected Step 4 remained `accepted=false` with `stop_reason=no_feasible_strategy`. The broader bicycle sample remains unrun. |
| Reconstruction Spec matched comparison | **Pending evidence** | A Spec-enabled and Spec-disabled matched run supports or rejects a causal quality claim. The feature itself is implemented. |

The exact-head installed-plugin smoke, CVM deployment authority, and
provider-free discovery gate separately bind `4e52c868`. The paid airplane
pilot observed a registered rebuild exit code of 0 and an atomic Final Delivery;
its archive does not independently audit shipped-runtime parity because it
contains no shipped snapshot or parity receipt.

## Deferred, non-blocking roadmap

These are real future capabilities, but they do not block the implemented core
workflow or the release-closure items above.

| Capability | Current boundary |
|---|---|
| Batch job-monitor protocol | Single-pilot submit/monitor exists; batch remains on the legacy FIFO path. |
| Larger concurrency and capacity policy | Development two-job isolation is proven; higher capacity remains an operational milestone. |
| Browser warm pool and snapshot resume | Deliberately deferred beyond the completed Development Browser Runtime milestone. |
| Thirty-object benchmark | Curated samples and small pilots exist; the complete cost, latency, and success-rate campaign has not run. |
| Dataset expansion | Toys4K is available; GSO, Sketchfab/Objaverse, OmniObject3D, and a unified Gallery are optional future expansion. |

## Historical plans that are not current gaps

- `.agents/wayfinder/sealed-agent-runtime/` is deprecated planning evidence.
  Its closed tickets are neither current requirements nor proof of a shipped
  sealed runtime.
- `.agents/plans/workspace-safe-candidate-editing.md` describes an unadopted
  `materialize-candidate` / `replace_literal` design. The implemented product
  instead uses W1-owned parent-source seeding, an isolated candidate work tree,
  registered operations, and trusted evidence providers.
- The proposed sealed Agent Runtime, Formal/Verified artifact program, generic
  Codex subagent harnesses, and alternate tap runtime plans are not active
  blockers unless separately re-authorized.
- Old plan headers that say `Planning` or `implementation not started` are
  historical metadata when contradicted by this roadmap and current code.

## Next execution order

1. Run the remaining bicycle acceptance sample if the broader two-object
   acceptance claim is still required.
2. Run the Reconstruction Spec matched comparison if a quality-benefit claim is
   still desired.
3. Choose deferred scale work only from measured operational need.
