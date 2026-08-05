# Mesh render modes

Read this file when the `mesh-render` visualization CLI is being
invoked and you need to decide between `heatmap` and `side-by-side`.

Both modes delegate the actual 3D → PNG render to
`skills/cad/scripts/snapshot` (Playwright + Three.js pipeline), so the
visual style matches every other snapshot in the repo (workbench
appearance, same lighting, `$cad-viewer`-aligned).

## Viewer input contract

- Pass the original mesh paths to `mesh-render`. Do not pre-convert a mesh
  with an ad-hoc `trimesh.export`, and do not inject `PYTHONPATH`; the CLI owns
  its bundled runtime and Viewer-only conversion.
- In `side-by-side` mode, non-GLB mesh inputs use the repository CAD Z-up
  convention. The CLI stores their temporary preview geometry as standard
  glTF Y-up, includes normals, and applies a neutral preview material before
  handing it to `cad snapshot`. Existing GLB/GLTF inputs are not rewritten.
- In `heatmap` mode, meshscope normalizes the numeric comparison pair and
  produces its own distance-colored GLB. It does not reuse the raw-input
  conversion performed for `side-by-side`.
- A/B tiles in a `side-by-side` row must use the same camera preset and show
  the same physical view. The visualization conversion affects only the PNG;
  it does not modify Chamfer, directional percentiles, or VoxBlame inputs.
- If the Viewer render fails, report the command failure. Do not substitute a
  point-cloud scatter, Matplotlib, trimesh scene render, or another renderer.

## `heatmap` mode — where does A diverge from B?

```bash
python skills/mesh-compare/scripts/mesh-render heatmap <mesh_a> <mesh_b> \
    --output <path>.png [--samples N] [--camera iso]
```

- Normalizes both meshes to Trellis2 `[-0.5, 0.5]^3`.
- Computes per-vertex distance from `mesh_a` to `mesh_b`.
- Colorizes `mesh_a` (blue = close, red = far, capped at p95 to avoid
  outlier compression) and hands the resulting GLB to `cad snapshot`.
- Output: single-view PNG showing where `mesh_a` diverges spatially.

**Use when**: `chamfer` is above threshold and you need to locate the
divergent region before deciding how to refine — for example, does a
chair reconstruction lose accuracy on the seat, the back, or the legs?

## `side-by-side` mode — multi-view visual verification

```bash
python skills/mesh-compare/scripts/mesh-render side-by-side <mesh_a> <mesh_b> \
    --output <path>.png [--cameras iso,front,right,top]
```

- Renders each mesh independently through `cad snapshot` at the
  chosen camera presets.
- Composites into a single PNG:
  - Two columns (A left, B right), one row per camera preset.
  - Optionally labeled with camera and mesh names.

**Use when**: the loop has accepted a reconstruction and you need an
"authoritative visual verification" artifact (e.g. workflow step 6 of
`$mesh-to-cad`). The multi-view canvas is what human reviewers and AI
vision see when they judge whether the reconstruction is faithful.

## Choosing between modes

| Question | Mode |
|---|---|
| Metrics look bad — where? | `heatmap` |
| Metrics look OK — does it visually match? | `side-by-side` |
| Both — asymmetric divergence AND overall check | run both |
