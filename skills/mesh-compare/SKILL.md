---
name: mesh-compare
description: Compute similarity metrics between two 3D mesh files.
---

# Mesh similarity comparison

## Purpose

Compute similarity metrics between two 3D mesh files. Emit numeric
metrics as a JSON object and, optionally, visualization renders as
PNG. See `references/compare-metrics.md` for which metrics are
computed and `references/render-modes.md` for render mode selection.

## Use this skill when

Use this skill when the user has two 3D mesh files and needs an objective
similarity metric — for example, evaluating a reconstruction against
ground truth.

## Tools and paths

Two independent CLIs, sharing the underlying `meshscope` package:

```bash
# Quantitative: numeric similarity metrics → JSON on stdout
python skills/mesh-compare/scripts/mesh-compare <mesh_a> <mesh_b> [--samples N] [--seed N] [--include-distances]

# Optional persistent grading; here mesh A is reference and mesh B is candidate
python skills/mesh-compare/scripts/mesh-compare <reference_mesh> <candidate_mesh> \
  --voxblame-dir "${EXP_DIR}/voxblame" --step <N> --max-depth 8 [--compare-to <M>]

# Canonical measurement against a prepared Canonical Reference
python skills/mesh-compare/scripts/mesh-compare voxblame-measure <candidate_mesh> \
  --reference "${EXP_DIR}/input" --output "${EXP_DIR}/voxblame" \
  --step <N> [--compare-to <M>]

# Page the complete frozen Repair Target order without remeasurement
python skills/mesh-compare/scripts/mesh-compare voxblame-targets \
  --output "${EXP_DIR}/voxblame" --step <N> [--offset <OFFSET>]

# Verify a rebuilt candidate without publishing another Measured Step
python skills/mesh-compare/scripts/mesh-compare voxblame-verify <rebuilt_mesh> \
  --reference "${EXP_DIR}/input" --workspace "${EXP_DIR}/voxblame" \
  --against-step <N> --output "${EXP_DIR}/work/verification.json"

# Publish objective evidence for one frozen multi-target Repair Batch
python skills/mesh-compare/scripts/mesh-compare voxblame-diff \
  --workspace "${EXP_DIR}/voxblame" --from-step <M> --to-step <N> \
  --repair-plan "${EXP_DIR}/work/repair-batch.json" \
  --output "${EXP_DIR}/cycles/<N>/region-diff.json"

# Render one formal eight-view residual preview
python skills/mesh-compare/scripts/mesh-compare voxblame-preview <candidate_mesh> \
  --reference "${EXP_DIR}/input" --experiment "${EXP_DIR}/experiment.json" \
  --output "${EXP_DIR}/preview" \
  --variant step

# Render the high-resolution final variant from the Selected Step
python skills/mesh-compare/scripts/mesh-compare voxblame-preview <candidate_mesh> \
  --reference "${EXP_DIR}/input" --experiment "${EXP_DIR}/experiment.json" \
  --output "${EXP_DIR}/final-preview" --variant final --selected-step <N> \
  --selected-summary "${EXP_DIR}/voxblame/steps/<NNNNNN>/summary.json"

# Visualization: distance-colored or side-by-side render → PNG on disk
python skills/mesh-compare/scripts/mesh-render heatmap <mesh_a> <mesh_b> --output <png>
python skills/mesh-compare/scripts/mesh-render side-by-side <mesh_a> <mesh_b> --output <png>
```

See `references/compare-metrics.md` for metric interpretation (Chamfer,
Hausdorff, percentile thresholds). See `references/render-modes.md` for
when to use `heatmap` vs `side-by-side`.

## Required workflow

1. Confirm both mesh files exist and are readable.
2. Run `mesh-compare` (numeric CLI) with its deterministic defaults
   (`--samples 50000 --seed 0`) to obtain threshold-comparable metrics.
3. Parse the JSON output; read `references/compare-metrics.md` for
   threshold interpretation.
4. If `chamfer > 0.1` on two meshes that visually look similar,
   report to the caller that coordinate frames likely disagree
   upstream — do not attempt to fix it here.
5. If the metrics indicate significant divergence (`chamfer > 0.01`,
   either directional `p95 > 0.03`, or `hausdorff` outliers), invoke
   `mesh-render heatmap`
   to identify where the divergence concentrates spatially. See
   `references/render-modes.md` for mode selection.
6. Include both the numeric summary and any generated PNG paths in
   the final response.
7. **(Optional) Structured qualitative review.** If the caller needs a
   per-layer fidelity signal beyond numeric metrics, inspect the
   side-by-side PNG with AI vision, then extend `compare_metrics.json`
   with an `ai_vision` block per `references/compare-metrics.md`
   (5-layer schema: silhouette / structure / form_detail / surface /
   proportion, each 0-1).
8. **(Optional) Persistent surface localization.** When the caller owns an
   iterative reconstruction EXP, pass `--voxblame-dir` and `--step` together.
   Read only the returned `voxblame.summary/1` and its single `next_action`;
   the complete immutable report remains at the returned `report` path. Step
   0 has no baseline, later steps default to N-1, and `--compare-to` may select
   any earlier published step.
9. **(Incremental canonical measurement surface.)** Use `voxblame-measure`
   only when `--reference` is an already published Canonical Reference and the
   candidate already uses its unitless `trellis2_canonical/1` coordinates.
   Step 0 has no parent; every nonzero step requires an explicit earlier
   `--compare-to`. The command atomically publishes `voxblame.measurement/1`
   plus a compact `voxblame.summary/1` containing objective depth
   1-8 facts and the first Repair Target page. Follow each non-null
   `next_offset` with `voxblame-targets` until every target is inspected. Target
   `display_rank` is a stable display order; use the missing/excess counts as
   direction facts, not as CAD advice or priority. Candidate triangles crossing
   the canonical cube are clipped into interior and exterior fragments. Fully
   exterior candidates are valid bad Measured Steps: the command publishes exact
   containment facts and signed diagnostic exterior occupancy with
   `out_of_frame_clear: false`.
10. **(Repair Batch evidence.)** Before editing, freeze a
    `voxblame.repair-batch/1` plan that selects one or more current Repair
    Targets by key and mask digest and maps stable Planned Edit keys to that
    selection. After publishing the explicit child Measured Step, run
    `voxblame-diff`. Read exact-mask, interior/exterior halo,
    outside-selected, and two-step trajectory evidence as counts and identities
    only. Direction transitions report their before and after ends separately;
    do not infer a one-cell flip. The command does not decide whether to keep,
    revise, or revert edits.
11. **(Formal residual preview.)** Run `voxblame-preview` on the same canonical
    candidate that will be measured. Inspect the frozen `+Z|-Z`, `+Y|-Y`,
    `+X|-X`, `Iso|-Iso` layout: reference is green, candidate red, and shared
    projected surface yellow. The command keeps reference-owned framing and
    publishes `preview.png` plus `voxblame.preview/1` metadata atomically.
    Exterior markers point toward off-frame candidate surface while the bound
    exterior snapshot identity remains objective measurement evidence. Use
    `--variant final --selected-step <N>` only for the Selected Step.
12. **(Final rebuild equivalence.)** Run `voxblame-verify` only on the GLB
    emitted by the registered final rebuild recipe. It recomputes candidate
    evidence in temporary state and compares interior tree, exterior snapshot,
    combined Observable Geometry, and depth-1–8 evidence with the named
    Measured Step. It never publishes a new step; mismatch writes no successful
    verification artifact.

## Handoff

Return outputs based on which CLI(s) were invoked:

- **Numeric CLI (`mesh-compare`)**: return the `compare_metrics.json`
  path in the final response, including all computed fields (numeric
  metrics + `ai_vision` block if added by the caller). When VoxBlame was
  requested, also return the full-report path and one action (or state that
  `next_action` is null).
- **Canonical measurement (`voxblame-measure`)**: return the compact JSON
  summary and the immutable `measurement.json`, depth-8 missing/excess
  snapshots, Repair Target mask paths, candidate tree, and authoritative
  exterior snapshot paths under the published step directory. Page through any
  remaining targets before claiming the target set was inspected. Do not infer
  a modeling decision from these facts.
- **Repair Batch comparison (`voxblame-diff`)**: return the immutable
  `voxblame.region-diff/1` path plus its plan and artifact identities. Report
  objective before/after/delta facts; leave edit assessment to the Agent.
- **Formal preview (`voxblame-preview`)**: return `preview.png` and
  `preview.json`. Report the profile digest, candidate/reference identities,
  render variant, and any exterior directions from the metadata.
- **Final verification (`voxblame-verify`)**: return
  `voxblame.verification/1`, its Selected Step and rebuilt identities, and all
  four equality facts. Keep route build provenance separate; the build manifest
  owns source-to-GLB derivation.
- **Render CLI (`mesh-render`)**: return the PNG path(s) produced
  (`heatmap` and/or `side-by-side` mode).
- **Both invoked (workflow-typical)**: return both JSON and PNG paths.

If any CLI failed (missing file, invalid mesh, unsupported format),
report the exit code and the `errors` field of the JSON output.

Unlike CAD-artifact skills (`$cad`, `$urdf`, `$sdf`), mesh-compare does
not hand off to `$cad-viewer`: its outputs are quantitative data and
static PNG renders, not interactive CAD artifacts.

## Non-negotiables

- Coordinate frames must match; this skill does not align meshes.
- Meshes are normalized to `[-0.5, 0.5]^3` (Trellis2 standard) before
  comparison. Chamfer values are unit-less and cross-comparable.
- Threshold decisions require the fixed 50000-sample, seed-0 protocol.
  The JSON `meta` block records both values; do not compare runs that use
  different sampling protocols as though they were equivalent.
- Pass original mesh paths directly to `mesh-render`; do not perform an ad-hoc
  GLB conversion or inject `PYTHONPATH`. Follow `references/render-modes.md`
  for the Viewer input, camera, material, and failure contracts.
- VoxBlame is opt-in and uses a separate reference-owned frame: the first
  mesh's bbox defines one isotropic lattice shared by all candidates.
  Candidate surface outside that lattice is ignored rather than independently
  normalized; the in-frame result is still graded and persisted.
- `--voxblame-dir` and `--step` are an inseparable pair. Published steps are
  immutable; retrying a step with a different surface tree fails closed. The
  agent consumes the compact JSON summary/report contract, not the binary
  `.vbsvo` snapshot.
- Repair Targets completely partition the current missing/excess evidence.
  Treat `display_rank` as non-prescriptive and keep target selection, repair
  strategy, verdicts, and workflow stop decisions Agent-owned.
- Region Diff accepts only current-step target identities and exact mask
  digests. Its explicit `to_step` must name `from_step` as `compare_to`.
  Interior and exterior grids remain separate, and identical output retries are
  idempotent while conflicting overwrites fail closed. If later exterior
  evidence is coarser than a selected target's frozen grid, comparison also
  fails closed rather than fabricating fine-cell counts.
- Formal previews use `cadena_residual_eight_view/1`. The candidate is never
  fitted or normalized by the renderer. Existing preview outputs are immutable:
  identical reruns are idempotent and identity conflicts fail closed.
- Pass the owning `experiment.json` to every preview; its exact
  `preview_profile: {name, sha256}` identity must match the bundled renderer.
  Final previews additionally require the selected step's canonical
  `voxblame.summary/1`, whose step, reference, and candidate identities are
  checked before rendering.

## Progressive references

- `references/compare-metrics.md` — Chamfer/Hausdorff/percentile
  threshold interpretation and coordinate-frame prerequisite (for
  the `mesh-compare` numeric CLI).
- `references/render-modes.md` — mode selection plus Viewer input,
  coordinate, material, camera-parity, and failure contracts (for the
  `mesh-render` visualization CLI).
