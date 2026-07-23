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
python skills/mesh-compare/scripts/mesh-compare <mesh_a> <mesh_b> [--samples N] [--include-distances]

# Visualization: distance-colored or side-by-side render → PNG on disk
python skills/mesh-compare/scripts/mesh-render heatmap <mesh_a> <mesh_b> --output <png>
python skills/mesh-compare/scripts/mesh-render side-by-side <mesh_a> <mesh_b> --output <png>
```

See `references/compare-metrics.md` for metric interpretation (Chamfer,
Hausdorff, percentile thresholds). See `references/render-modes.md` for
when to use `heatmap` vs `side-by-side`.

## Required workflow

1. Confirm both mesh files exist and are readable.
2. Run `mesh-compare` (numeric CLI) with default samples (10000) or
   higher for large meshes to obtain quantitative metrics.
3. Parse the JSON output; read `references/compare-metrics.md` for
   threshold interpretation.
4. If `chamfer > 0.1` on two meshes that visually look similar,
   report to the caller that coordinate frames likely disagree
   upstream — do not attempt to fix it here.
5. If the metrics indicate significant divergence (`chamfer > 0.02`,
   high `p95`, or `hausdorff` outliers), invoke `mesh-render heatmap`
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

## Handoff

Return outputs based on which CLI(s) were invoked:

- **Numeric CLI (`mesh-compare`)**: return the `compare_metrics.json`
  path in the final response, including all computed fields (numeric
  metrics + `ai_vision` block if added by the caller).
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

## Progressive references

- `references/compare-metrics.md` — Chamfer/Hausdorff/percentile
  threshold interpretation and coordinate-frame prerequisite (for
  the `mesh-compare` numeric CLI).
- `references/render-modes.md` — when to use `heatmap` vs
  `side-by-side` mode (for the `mesh-render` visualization CLI).
