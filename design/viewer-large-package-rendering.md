# Viewer: large-package rendering plan

Recorded 2026-07-06. Target workload: falcon_heavy-class component-GLB
packages — 2,142 occurrences over 141 unique components (top cid ×108,
seven more ×54), 24 distinct override colors; starship_stack is 2,562/282.
All findings below were adversarially verified against the code and the
actual falcon_heavy descriptor.

## Where the time and memory go today

- `packages/cadjs/src/lib/assembly/meshData.js:399-599`
  (`buildComposedPackageMeshData`): bakes every occurrence transform into
  fresh arrays in a synchronous main-thread per-vertex JS loop — 1,397,652
  composed vertices (12.3× the 113,748 unique ones), ~77 MB.
- `packages/cadjs/src/common/cadScene.js:670-744, 1784-1833, 1898-1909`:
  re-slices those arrays into one BufferGeometry + one physical material +
  one `THREE.Mesh` **per occurrence** (colors copied three times; 2,142
  `computeBoundingSphere` calls) → ~2,142 draw calls, ~66 MB GPU buffers.
  No `InstancedMesh`/`BatchedMesh`/merge anywhere in cadjs or viewer.
- `viewer/src/client/components/workbench/hooks/useCadAssets.js:527-536`:
  component GLBs parsed on the main thread (`loadRenderGlb` without
  `preferWorker`, though the worker path exists) at concurrency 3.
- Barycentric edge attributes force un-indexed 3-verts-per-triangle
  geometry that is composed and uploaded even when the display mode draws
  no edges (~42 MB copies, ~21 MB GPU upload).
- `useViewerPicking.js:638-663`: hover raycasts rebuild the visible-mesh
  array and intersect all 2,142 meshes per rAF; hover changes rewrite
  material state across all records.

## Phases

**Phase 0 — benchmark harness (prerequisite). DONE.**
`packages/cadjs/bench/composePackageBench.mjs` — a Node harness (no GPU) that
loads a warmed `__cadgen__` package, parses every component GLB, and times +
measures the meshData-layer structural costs the plan's wins are stated in.
Run: `cd packages/cadjs && node bench/composePackageBench.mjs [package-dir]`
(needs `three` resolvable — dev: symlink `viewer/node_modules/three` into
`packages/cadjs/node_modules/`; and a warmed package on disk).
Structural metrics (draw calls, vertex inflation, compose ms) are covered
here; GPU upload / frame rate / felt main-thread stall still need an
in-browser check per phase.

**falcon_heavy baseline (pre-Phase-1):** 2,142 occurrences · 141 unique
components · **2,142 draw calls** · 113,748 unique → **1,397,652 composed
vertices (12.29×)** · 465,884 composed triangles · 53.3 MiB composed
buffers · 140 ms compose. These are the numbers Phases 2–3 must move.

**Phase 1 — parallel worker parsing (small).** `preferWorker: true` for
package component loads, concurrency 3 → min(hardwareConcurrency, 8), fix
the serial shared path (`source.js`) to `Promise.all`. Win: est. 2–4×
time-to-first-render; zero render-path risk. Ship first.

**Phase 2 — compose off the main thread, once (medium).** Move
`buildComposedPackageMeshData` into the existing GLB worker (transferable
arrays); stop the per-part re-slice by sharing one geometry with draw
ranges until Phase 3 replaces it; skip barycentric/class composition and
upload when the display mode draws no edges. Win: removes the 0.5–2 s
main-thread stall and ~150 MB of peak heap; −21 MB GPU upload with edges
off.

**Phase 3 — cid-keyed instancing (large, the headline).** One
`InstancedMesh` per (component cid × material bucket): per-instance matrix
from `occurrence.transform`, per-instance color from override colors.
Win: 2,142 → ~141 draw calls (15×), GPU vertices 1.40M → 114k (12.3×),
~60 MB GPU saved, hover/visibility loops shrink 15×, and the Phase-2
compose loop disappears entirely (instances need no baked vertices).
Verified obligations:
- mirrored occurrences (negative-determinant transforms) get their own
  bucket with flipped winding or DoubleSide;
- picking moves to `InstancedMesh` raycast `instanceId` → occurrence id;
- exploded view updates per-instance matrices;
- hover/highlight via per-instance color or a single overlay mesh;
- translucent parts keep the per-mesh path as a fallback bucket
  (three.js cannot sort transparency per instance);
- per-part edge/silhouette overlays render only for selection/hover
  instead of per-occurrence.

**Phase 4 — interaction polish (small; partly subsumed by Phase 3).**
Cache the visible-mesh set (`useViewerPicking.js:638` rebuilds a
2,142-element filtered array per pick), diff hover state changes (touch ~2
records, not all), three-mesh-bvh if picking is still hot afterward.
NOTE: `matrixAutoUpdate = false` for baked transforms is ALREADY applied
(`packages/cadjs/src/common/displayRecordTransform.js:9`, tested in
`modelRuntime.test.js`) — not a remaining item.

## Phase 3 scoping reality (checked against the code)

The record system in `cadScene.js` is one-mesh-per-occurrence end to end:
`makeRecord` (`:1784-1833`) builds a per-part `BufferGeometry` slice + its
own `MeshPhysicalMaterial` + `THREE.Mesh` + optional edge and silhouette
child objects, and every downstream behavior keys on the per-record
`partId` — selection/hover/hidden/focus (`partIdMatchesSet`, `:1235-1238`),
exploded `baseTransform`, per-record effect/opacity/highlight, and pick via
`mesh.userData.partId` + `intersectObjects(perPartMeshes)`. Converting to
`InstancedMesh` is therefore not a local swap: it needs an instance-aware
selection/pick/explode/edge layer (instanceId ↔ occurrence, per-instance
color/visibility, selection-only edges) living beside or replacing the
record system, and it changes the shared module that also drives headless
snapshot rendering (bundled into `skills/cad/scripts/snapshot/runtime/
snapshot-render.js` via `bundle.sh`). Recommended execution: a dedicated
effort landing behind a default-off `instancePackages` render setting,
gated by (a) this bench (draw calls/vertices), (b) snapshot pixel-parity
across all six display modes, (c) an in-browser pass (Preview MCP or
manual) for hover/pick/explode/transparency before the default flips.
Strategy to bound blast radius: instance only the common opaque,
non-mirrored occurrences; fall back to the existing per-mesh record for
transparent / negative-determinant / edge-heavy parts so nothing regresses.

## Constraints

- Instancing lives in `packages/cadjs` (framework-agnostic); the viewer app
  keeps only UI state. Snapshot rendering shares cadScene, so Phase 3 must
  keep snapshot output pixel-equivalent (harness includes a snapshot diff).
- Verification per phase: screenshot parity across display modes
  (solid/rendered/transparent/edges), picking + exploded + params-sidecar
  regression on falcon_heavy, starship_stack, tom, and one small model.
