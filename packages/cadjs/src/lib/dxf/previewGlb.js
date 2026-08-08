/**
 * Bake a DXF flat pattern into render-preset GLB bytes.
 *
 * This is the build-time half of the DXF render path: `buildDxfPreviewMeshData` is reused
 * VERBATIM as an input (it is ~1,300 lines of meshing nobody is rewriting), and its output is
 * handed to the shared `writeGlb`. Once the package carries `preview.glb` the browser stops
 * parsing and extruding DXF at open time -- the viewport is fed a GLB like every other entry
 * (design/unified-glb-render-artifacts.md §7.4.2).
 *
 * It lives in `src/` rather than in `bin/dxf-artifact.mjs` so it is testable without spawning
 * a process: the builder script owns argv, file IO and the NDJSON protocol, and this owns the
 * geometry.
 *
 * **Baked in the FLAT (unfolded) state.** `bendSettings = null` gives every bend line the
 * default 0-degree angle, which is the unfolded pattern. Bend angles were a live client
 * control over a mesh rebuilt in the browser; with the mesh baked they are an accepted loss
 * (§2, §7.4.3) rather than something to bake a combinatorial set of GLBs for.
 */

import { writeGlb } from "../../glb/writeGlb.js";

import { buildDxfPreviewMeshData } from "./buildPreviewMesh.js";

/**
 * Millimetres to glTF metres. The viewer's loader multiplies by 1000 on the way back in
 * (`GLB_CAD_UNIT_SCALE`, `render/glbMeshData.js`), and cadgen's Python GLB writer applies the
 * same 0.001 (`_internal/glb_mesh_payload.py`). Writing raw millimetres would load a part at
 * 1000x its size.
 */
export const DXF_MM_TO_GLB_SCALE = 0.001;

/** Identity of this bake's geometry contract, recorded in the descriptor's bake block. */
// v2: positions are CAD Z-up. v1 wrote the mesher's Y-up straight through, so every
// package baked before this renders on its edge — the bump is what makes them rebuild.
export const DXF_PREVIEW_BAKE_FORMAT = "dxf-preview-glb-v2";

/**
 * The mesher returns an INDEXED triangle list whose vertex array also carries the edge-overlay
 * vertices (unreferenced by `indices`), and no normals. Expand to a non-indexed soup in glTF
 * metres: `writeGlb` then welds it and derives per-face normals, which both drops the
 * unreferenced tail and gives the flat pattern crisp creases.
 */
export function dxfPreviewPositions(meshData) {
  const vertices = meshData?.vertices;
  const indices = meshData?.indices;
  if (!vertices?.length || !indices?.length) {
    throw new Error("DXF preview produced no triangles");
  }
  const positions = new Float32Array(indices.length * 3);
  for (let slot = 0; slot < indices.length; slot += 1) {
    const source = indices[slot] * 3;
    const target = slot * 3;
    // Y-up -> CAD Z-up: (x, y, z) -> (x, -z, y).
    //
    // The flat-pattern mesher builds Y-up (thickness on Y), but this GLB carries
    // cadOccurrenceId extras, and the viewer's loader reads those as "already CAD space" and
    // skips its own conversion. The drawing therefore arrived in a Z-up scene still Y-up and
    // stood on its edge. Converting HERE keeps that convention true — a GLB with occurrence
    // ids is CAD-space — instead of teaching the loader a per-format exception.
    //
    // (x, -z, y) rather than (x, z, y): the latter has determinant -1 and would mirror the
    // part, quietly flipping every asymmetric profile.
    positions[target] = vertices[source] * DXF_MM_TO_GLB_SCALE;
    positions[target + 1] = -vertices[source + 2] * DXF_MM_TO_GLB_SCALE;
    positions[target + 2] = vertices[source + 1] * DXF_MM_TO_GLB_SCALE;
  }
  return positions;
}

/**
 * `dxfData` (from `parseDxf`) -> `{ bytes, stats }`, where `bytes` is a render-preset GLB.
 *
 * `encoder` is meshoptimizer's `MeshoptEncoder`, already awaited on `.ready`; the render
 * preset requires it. `thicknessMm` is the producer's bake setting, passed in rather than
 * read from the drawing so ONE authority decides it and the descriptor's `bakeHash` can be
 * computed from the same number.
 */
export function buildDxfPreviewGlb(dxfData, { thicknessMm, encoder, name = "drawing" } = {}) {
  const meshData = buildDxfPreviewMeshData(dxfData, thicknessMm, null);
  const positions = dxfPreviewPositions(meshData);
  const bytes = writeGlb(
    { primitives: [{ positions, name }], name, units: "mm" },
    { preset: "render", encoder, sourceKind: "dxf", occurrenceIdPrefix: "dxf" }
  );
  return {
    bytes,
    stats: {
      triangleCount: meshData.triangle_count,
      vertexCount: meshData.vertex_count,
      bounds: meshData.bounds,
    },
    meshData,
  };
}
