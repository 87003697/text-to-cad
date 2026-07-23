# Mesh compare metrics

Read this file when interpreting the JSON output of the `mesh-compare`
numeric CLI or when a caller (e.g. `$mesh-to-cad`) needs threshold
interpretation.

## Coordinate-frame prerequisite

`mesh-compare` does NOT align meshes. Before calling this skill, the
caller must guarantee that both meshes share a coordinate frame
(units, origin, up-axis). A large `chamfer` on two meshes that visually
look similar almost always means the upstream coordinate frames
disagree — fix that at the source, not here.

Meshes are then normalized to Trellis2's `[-0.5, 0.5]^3` unit box via
`(vertices - bbox_center) / max(extents)`. All distances below are
reported in this normalized frame, so they are unit-less and
cross-comparable across objects.

## Chamfer distance

Symmetric mean nearest-neighbor distance between point samples of A
and B. This is the primary "how close overall?" number.

Interpretation on normalized meshes:

- `< 0.005` — excellent (self-compare noise floor).
- `< 0.02` — acceptable reconstruction.
- `< 0.05` — degraded but recognizable.
- `> 0.1` — likely a coordinate-frame mismatch, not a fidelity problem.

## Hausdorff distance

The single largest nearest-neighbor distance in either direction.
Sensitive to a single divergent region (bump, missing appendage);
useful for locating spatial outliers, not for overall quality scoring.

## Percentile stats

`stats.p90_a2b` / `stats.p95_a2b` (and symmetric `_b2a`) locate the
tail of the error distribution. When the chamfer is acceptable but
`p95` is much higher than `mean`, a small region diverges strongly —
run `mesh-render heatmap` to see where.

## IoU (bulk shape match)

Optional bulk metric: intersection-over-union of the voxelized
occupancy grids. `iou < 0.75` on a normalized mesh indicates a bulk
shape mismatch and suggests the caller should re-route (choose a
different modeling paradigm) rather than continue refining.

## `compare_metrics.json` schema (authoritative)

The `mesh-compare` numeric CLI emits (top-level, on stdout):

```json
{
  "ok": true,
  "chamfer": 0.018,
  "hausdorff": 0.045,
  "stats": {
    "mean_a2b": 0.014, "mean_b2a": 0.015,
    "median_a2b": 0.010, "median_b2a": 0.011,
    "p90_a2b": 0.032, "p90_b2a": 0.034,
    "p95_a2b": 0.048, "p95_b2a": 0.051,
    "max_a2b": 0.088, "max_b2a": 0.092
  },
  "meta": {
    "n_samples": 10000,
    "normalization": "trellis2",
    "scale_a": 12.4,
    "scale_b": 12.1
  }
}
```

Callers that persist this JSON on disk (for example `$mesh-to-cad`
writing `${EXP_DIR}/compare_metrics.json`) may add a top-level alias
`chamfer_l2` matching `chamfer` and top-level `iou`, `input_mesh`,
`reconstructed_mesh`, `p50`, `p90`, `p95`, `p99`, `sample_count`, and
`normalized: true` fields for convenience. These aliases must not
contradict the numeric CLI's own fields.

## `ai_vision` block (optional, appended by caller)

When qualitative fidelity signal is required beyond numeric metrics,
the caller inspects the `mesh-render side-by-side` PNG with an AI
vision model and appends this block to `compare_metrics.json`:

```json
{
  "ai_vision": {
    "overall_score": 0.75,
    "layer_scores": {
      "silhouette": 0.9,
      "structure": 0.6,
      "form_detail": 0.8,
      "surface": 0.7,
      "proportion": 0.85
    },
    "notes": "tufting pattern absent; overall proportion correct"
  }
}
```

Each layer score is 0-1; the five layers are fixed (do not add or
rename). Skills that own the workflow (e.g. `$mesh-to-cad`) are
responsible for adding this block when needed; `mesh-compare` itself
never invents scores.

## `--include-distances`

When `--include-distances` is set, the CLI appends `distances_a2b`
and `distances_b2a` arrays (one entry per sampled point). Use only
when you need to plot the error distribution or feed it to a
downstream analysis; the arrays add ~80 KB per 10K samples to the
JSON payload.
