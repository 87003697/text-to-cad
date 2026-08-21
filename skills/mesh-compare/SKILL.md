---
name: mesh-compare
description: Measure canonical surface occupancy, page Repair Targets, produce Region Diff evidence, render residual previews, and verify rebuilt Observable Geometry.
---

# Canonical geometry evidence

## Purpose

`$mesh-compare` is the public geometry boundary for the canonical repair
Workspace. It prepares one immutable Canonical Reference, measures candidates
already expressed in that coordinate system, exposes complete Repair Target
evidence, computes objective Region Diff artifacts, renders formal previews,
and verifies rebuilt Observable Geometry.

VoxBlame reports facts only. The Mesh-to-CAD Agent owns Repair Batch selection,
Planned Edits, assessment, stop decisions, and Selected Step choice.

## Public commands

Use the active project Python. Every command emits one JSON object on stdout;
failures return exit 2 with a stable error classification.

```bash
# Canonical Reference
python skills/mesh-compare/scripts/mesh-compare \
  voxblame-prepare-reference <raw-scene> --output <EXP_DIR>/input

# Measured Step; every nonzero step names an earlier parent
python skills/mesh-compare/scripts/mesh-compare \
  voxblame-measure <canonical-candidate.glb> \
  --reference <EXP_DIR>/input --output <EXP_DIR>/voxblame \
  --step <N> [--compare-to <M>]

# Complete Repair Target paging
python skills/mesh-compare/scripts/mesh-compare \
  voxblame-targets --output <EXP_DIR>/voxblame --step <N> \
  [--offset <OFFSET>]

# One frozen Repair Batch edge
python skills/mesh-compare/scripts/mesh-compare \
  voxblame-diff --workspace <EXP_DIR>/voxblame \
  --from-step <M> --to-step <N> \
  --repair-plan <repair-batch.json> --output <region-diff.json>

# Formal residual preview
python skills/mesh-compare/scripts/mesh-compare \
  voxblame-preview <canonical-candidate.glb> \
  --reference <EXP_DIR>/input --experiment <EXP_DIR>/experiment.json \
  --output <preview-dir> --variant step

# Final preview binds the Selected Step summary
python skills/mesh-compare/scripts/mesh-compare \
  voxblame-preview <rebuilt-candidate.glb> \
  --reference <EXP_DIR>/input --experiment <EXP_DIR>/experiment.json \
  --output <preview-dir> --variant final --selected-step <N> \
  --selected-summary <EXP_DIR>/voxblame/steps/<NNNNNN>/summary.json

# Non-publishing rebuild verification
python skills/mesh-compare/scripts/mesh-compare \
  voxblame-verify <rebuilt-candidate.glb> \
  --reference <EXP_DIR>/input --workspace <EXP_DIR>/voxblame \
  --against-step <N> --output <verification.json>
```

No positional two-mesh command exists. The command token must be one of the six
canonical operations above.

## Required workflow

1. Run `voxblame-prepare-reference` once on the raw evaluated scene. On success,
   treat `input/` as immutable. On failure, return its bounded failure evidence
   and do not create a Workspace.
2. Build candidates directly in `trellis2_canonical/1`. Do not align, fit,
   recenter, rescale, or rotate a candidate.
3. Render the formal step preview for the exact candidate that will be
   measured. Inspect all eight fixed views and retain its identity.
4. Run `voxblame-measure`. Step 0 has `compare_to: null`; a nonzero Measured
   Step requires `--compare-to` naming an earlier published step.
5. Read `objective_facts`, ordered depth-1–8 counts, depth-8 evidence, Exterior
   Surface facts, and Observable Geometry identity. A step is objectively
   accepted only when all three objective facts are true.
6. Call `voxblame-targets`, read `repair_frontier.active_depth`, inspect any
   exterior `alerts`, then follow `repair_targets.next_offset` until every
   interior Repair Target has been inspected. Mesh Compare owns the
   deterministic repair depth; the Agent must not choose or advance it.
   Targets are grouped at that depth while retaining exact depth-8 masks. Item
   order is deterministic attention order by objective error impact, not a CAD
   edit instruction.
7. After an explicit child Measured Step exists, run `voxblame-diff` with the
   frozen Repair Batch. Treat exact-mask, halo, outside-selected, trajectory,
   and exterior evidence as facts; the Agent writes the assessment.
8. During finalization, run `voxblame-verify` against the Selected Step.
   Verification must remain a separate non-step artifact and must not publish
   another Measured Step.

## Evidence contracts

- Canonical Reference preparation performs the one permitted normalization and
  publishes float64 `reference.ply`, dependency and normalization identities,
  and a closed input manifest atomically.
- Measured Steps publish depth-1–8 conservative surface occupancy, exact
  depth-8 missing/excess evidence, Exterior Surface evidence, complete Repair
  Target ordering, and one Observable Geometry identity.
- Formal previews use `cadena_residual_eight_view/1`: reference is green,
  candidate red, shared projected surface yellow, and background black.
- Region Diff binds an explicit parent/child pair and one Agent-authored Repair
  Batch. It never issues a modeling verdict.
- Rebuild verification independently compares interior, exterior,
  multiresolution, and combined Observable Geometry identities.

The closed schemas are bundled at
`scripts/packages/meshscope/src/meshscope/voxblame/CONTRACT.md`.

## Handoff

Return only artifacts from the operation that ran:

- Canonical Reference: `input/` plus its manifest and normalization identity.
- Measured Step: `measurement.json`, `summary.json`, candidate/interior/exterior
  identities, depth-8 evidence, and every Repair Target page inspected.
- Region Diff: the immutable artifact, Repair Batch identity, parent/child
  steps, and objective deltas.
- Preview: `preview.png`, `preview.json`, profile identity, and any exterior
  indicators.
- Verification: `verification.json`, Selected Step, rebuilt identities, and all
  four equality facts.

On failure, report the exit code, classification, and bounded detail. Never
invent missing evidence or infer a modeling decision from objective facts.

## Non-negotiables

- The Canonical Reference is normalized exactly once; candidates are not.
- Every nonzero Measured Step and Region Diff has explicit ancestry.
- Repair Targets partition the complete current error set.
- Each Measured Step recomputes its Active Repair Depth. A child may advance,
  remain at, or return to a coarser depth; final acceptance remains depth-8.
- Preview, measurement, Region Diff, and verification identities must agree
  before their owning Workspace publication.
- Published evidence is immutable; identical retries may be idempotent and
  conflicting retries fail closed.
- Canonical measurement requires the native C++ surface-occupancy backend by
  default and fails closed when it is unavailable. The Python backend is an
  explicit parity/testing choice, never a silent production fallback.
- VoxBlame never owns Planned Edits, Agent assessment, stop decisions, or Final
  Delivery publication.
