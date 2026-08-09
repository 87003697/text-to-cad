/**
 * The render-time transform for a DXF preview: thickness, then fold.
 *
 * The baked GLB is a prism at the reference thickness (previewGlb.js), lying flat in CAD
 * Z-up with its bend lines at constant X. Everything a drawing's settings do to it is this
 * one vertex transform:
 *
 *   1. scale Z — thickness. Exact, because a flat pattern is a profile swept perpendicular:
 *      walls move, caps and holes are untouched in XY.
 *   2. accordion fold — for each bend axis, rotate every vertex BEYOND it about that axis,
 *      accumulating down the chain. Geometry past a bend is rigid; only its placement
 *      changes, so nothing is re-meshed and the fold can ride a slider.
 *
 * The ORDER is the correctness condition. Folding first and thickness-scaling after (as a
 * group scale) flattens every folded-up flange back into the sheet plane — at 0 mm the fold
 * becomes invisible, which is exactly the bug this module replaced. Thickness happens in the
 * sheet's own frame, folding in the world's.
 *
 * Pure functions over arrays, deliberately: they run identically in the viewer and in the
 * headless snapshot runtime, and they are testable in node without a scene.
 */

/**
 * The Z scale a zero-thickness drawing renders at. A true 0 collapses top and bottom caps
 * onto one plane (z-fighting) and degenerates normals; a hair keeps the solid valid while
 * reading as a flat face at any sane zoom.
 */
export const DXF_FLAT_THICKNESS_SCALE = 1e-3;

/**
 * Each bend rotates about its axis's CURRENT position — the image of the flat axis under
 * every earlier fold — not its flat one. Deciding membership by the folded position instead
 * (the obvious shortcut) under-folds a chain: after the first 90-degree fold the second
 * axis's strip has moved to lower X than the second axis, so it never receives its second
 * rotation and a U-channel folds into an L. Pivots are precomputed once, per option set.
 */
function computeFoldPivots(axes, angle) {
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const pivots = [];
  for (const flatX of axes) {
    let px = flatX;
    let pz = 0;
    for (const pivot of pivots) {
      const dx = px - pivot.x;
      const dz = pz - pivot.z;
      px = pivot.x + dx * cos - dz * sin;
      pz = pivot.z + dx * sin + dz * cos;
    }
    pivots.push({ flatX, x: px, z: pz });
  }
  return pivots;
}

export function normalizeDxfFoldOptions({
  bendAxesX = null,
  bendAngleRad = 0,
  thicknessScale = 1,
} = {}) {
  const axes = Array.isArray(bendAxesX)
    ? bendAxesX.filter((value) => Number.isFinite(value)).sort((a, b) => a - b)
    : [];
  const angle = Number.isFinite(bendAngleRad) ? bendAngleRad : 0;
  const requestedScale = Number.isFinite(thicknessScale) ? thicknessScale : 1;
  const scale = Math.max(requestedScale, DXF_FLAT_THICKNESS_SCALE);
  const pivots = angle !== 0 && axes.length ? computeFoldPivots(axes, angle) : [];
  return { axes, angle, scale, pivots };
}

/** True when the transform would change anything — the caller's "is there work" gate. */
export function dxfFoldIsIdentity(options) {
  const { axes, angle, scale } = normalizeDxfFoldOptions(options);
  return scale === 1 && (!axes.length || angle === 0);
}

/**
 * Transform one CAD-space point. Exported for the guide lines, which must ride the SAME
 * chain the sheet folds through or they detach from their creases.
 */
export function foldDxfPoint(x, y, z, { angle, scale, pivots }) {
  let px = x;
  let pz = z * scale;
  if (angle !== 0 && pivots.length) {
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    for (const pivot of pivots) {
      // Membership is by FLAT x — where the vertex sits in the unfolded pattern — never by
      // where earlier folds have moved it. A vertex ON the axis stays put; only geometry
      // beyond it folds, which is also what pins a bend line to its own crease while it
      // rides every earlier fold.
      if (x <= pivot.flatX) {
        break;
      }
      const dx = px - pivot.x;
      const dz = pz - pivot.z;
      px = pivot.x + dx * cos - dz * sin;
      pz = pivot.z + dx * sin + dz * cos;
    }
  }
  return [px, y, pz];
}

/**
 * Rewrite `output` (a Float32Array-compatible position buffer, xyz-interleaved) from the
 * FLAT positions in `original`. Always transforms from flat, never from the current pose:
 * folding a folded sheet compounds.
 */
export function transformDxfPreviewPositions(original, output, options) {
  const resolved = normalizeDxfFoldOptions(options);
  for (let index = 0; index < original.length; index += 3) {
    const [px, py, pz] = foldDxfPoint(
      original[index],
      original[index + 1],
      original[index + 2],
      resolved
    );
    output[index] = px;
    output[index + 1] = py;
    output[index + 2] = pz;
  }
  return output;
}

/** The bounds facts the guide lines need, scanned once from the flat positions. */
export function dxfFlatPatternExtents(original) {
  let yMin = Infinity;
  let yMax = -Infinity;
  let zMax = 0;
  for (let index = 0; index < original.length; index += 3) {
    const y = original[index + 1];
    const z = Math.abs(original[index + 2]);
    if (y < yMin) yMin = y;
    if (y > yMax) yMax = y;
    if (z > zMax) zMax = z;
  }
  if (!Number.isFinite(yMin)) {
    yMin = 0;
    yMax = 0;
  }
  return { yMin, yMax, zMax };
}

/** How far above the folded top surface a guide line sits, in mm — enough to never
 *  z-fight the face it annotates, small enough to read as ON it. */
export const DXF_BEND_GUIDE_ELEVATION_MM = 0.3;

/**
 * Endpoints for the dotted bend-line overlay, as xyz-interleaved segment pairs, FOLDED
 * through the same chain as the sheet. Each guide spans the sheet's Y extent at its bend's
 * X, elevated just above the top surface.
 */
export function dxfBendGuideSegments(original, options) {
  const resolved = normalizeDxfFoldOptions(options);
  if (!resolved.axes.length) {
    return new Float32Array(0);
  }
  const { yMin, yMax, zMax } = dxfFlatPatternExtents(original);
  const zTop = zMax + DXF_BEND_GUIDE_ELEVATION_MM / Math.max(resolved.scale, 1e-6);
  const segments = new Float32Array(resolved.axes.length * 6);
  let cursor = 0;
  for (const axisX of resolved.axes) {
    const [ax, ay, az] = foldDxfPoint(axisX, yMin, zTop, resolved);
    const [bx, by, bz] = foldDxfPoint(axisX, yMax, zTop, resolved);
    segments[cursor] = ax;
    segments[cursor + 1] = ay;
    segments[cursor + 2] = az;
    segments[cursor + 3] = bx;
    segments[cursor + 4] = by;
    segments[cursor + 5] = bz;
    cursor += 6;
  }
  return segments;
}
