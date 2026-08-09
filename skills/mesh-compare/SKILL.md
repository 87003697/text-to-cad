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
   plus a compact `voxblame.measurement-summary/1` containing objective depth
   1-8 facts and the first Repair Target page. Follow each non-null
   `next_offset` with `voxblame-targets` until every target is inspected. Target
   `display_rank` is a stable display order; use the missing/excess counts as
   direction facts, not as CAD advice or priority. Candidate triangles crossing
   the canonical cube are clipped into interior and exterior fragments. Fully
   exterior candidates are valid bad Measured Steps: the command publishes exact
   containment facts and signed diagnostic exterior occupancy with
   `out_of_frame_clear: false`.

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

## Progressive references

- `references/compare-metrics.md` — Chamfer/Hausdorff/percentile
  threshold interpretation and coordinate-frame prerequisite (for
  the `mesh-compare` numeric CLI).
- `references/render-modes.md` — mode selection plus Viewer input,
  coordinate, material, camera-parity, and failure contracts (for the
  `mesh-render` visualization CLI).
