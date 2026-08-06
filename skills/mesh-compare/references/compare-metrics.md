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
- `≤ 0.01` — acceptable overall reconstruction under the fixed
  50000-sample, seed-0 protocol.
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
run `mesh-render heatmap` to see where. The mesh-to-cad hard tail gate
requires both directional p95 values to be `≤ 0.03`.

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
    "n_samples": 50000,
    "sample_seed": 0,
    "sampling": "trimesh_surface_seeded",
    "normalization": "trellis2",
    "scale_a": 12.4,
    "scale_b": 12.1
  }
}
```

Callers that persist this JSON on disk (for example `$mesh-to-cad`
writing `${EXP_DIR}/compare_metrics.json`) must preserve every emitted field
unchanged. The documented `ai_vision` extension below may be added when
requested; do not append a separately computed IoU, caller-defined metric, or
alias. Read `chamfer` and the directional percentile fields at their canonical
locations.

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

## `voxblame` block (optional, emitted by numeric CLI)

When `--voxblame-dir` and `--step` are supplied together, the CLI appends one
compact localization summary without changing any legacy numeric field:

```json
{
  "voxblame": {
    "schema": "voxblame.summary/1",
    "step": 2,
    "compare_to": 1,
    "report": "voxblame/steps/000002/report.json",
    "max_depth": 8,
    "frame": {"center": [0, 0, 0], "scale": 1},
    "reference": {
      "storage_schema": "voxblame.svo/1",
      "logical_sha256": "<sha256>"
    },
    "candidate": {
      "storage_schema": "voxblame.svo/1",
      "logical_sha256": "<sha256>"
    },
    "no_observable_geometry_change": false,
    "remaining_error_count": 7,
    "coarsest_first_error_depth": 3,
    "change_counts": {
      "introduced": 0, "regressed": 1, "changed": 0,
      "improved": 2, "resolved": 4
    },
    "next_action": {
      "reason": "regressed",
      "direction": "excess",
      "first_error_depth": 3,
      "region_handle": {"depth": 3, "octant_prefix": "17"},
      "bounds_world": {"min": [0, 0, 0], "max": [1, 1, 1]}
    }
  }
}
```

`next_action` is null when no surface-occupancy error remains. Consumers must
not copy full `current.errors` or `changes` from `report.json` into the prompt;
those arrays are disk evidence. Numeric Chamfer/Hausdorff still use the legacy
per-mesh Trellis2 normalization. VoxBlame instead uses the first positional
mesh as the fixed reference frame. Candidate surface outside that frame is
ignored; its in-frame surface (including an empty result) is still graded and
persisted so geometric failure remains visible in the normal comparison.
VoxBlame uses deterministic conservative triangle/AABB occupancy; it does not
claim voxel-for-voxel equivalence with TRELLIS.2's scan-line/QEF O-Voxel
construction. Its `.vbsvo` snapshots borrow TRELLIS.2's preorder child-mask
hierarchy, then add a validated subtree-span index for early mismatch traversal.
This is a VoxBlame-specific uncompressed container, not VXZ and not a claim of
format compatibility with TRELLIS.2. `session.json` owns the reference frame and
mesh digest; each `.vbsvo` is a complete immutable surface snapshot. Callers and
agents should consume the JSON summary/report and must not dump the binary tree
into the prompt. `region_handle` is a stable logical
`{depth, octant_prefix}` address, never a node row or byte offset. The frame,
storage schema, and logical digests bind a summary to one reference-owned
lattice. `no_observable_geometry_change=true` means the current and selected
previous candidate have identical occupancy at this schema/depth/frame; it does
not prove that the source meshes are exactly equal.

## `--include-distances`

When `--include-distances` is set, the CLI appends `distances_a2b`
and `distances_b2a` arrays (one entry per sampled point). Use only
when you need to plot the error distribution or feed it to a
downstream analysis; the arrays add ~80 KB per 10K samples to the
JSON payload.
