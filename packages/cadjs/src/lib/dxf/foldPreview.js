/**
 * The render-time transform for a DXF preview: thickness, then fold.
 *
 * The baked GLB is a prism at the reference thickness (previewGlb.js), lying flat in CAD
 * Z-up with its bend lines at constant X. Everything a drawing's settings do to it is this
 * one vertex transform:
 *
 *   1. scale Z — thickness. Exact, because a flat pattern is a profile swept perpendicular:
 *      walls move, caps and holes are untouched in XY.
 *   2. accordion fold — each bend rotates every vertex BEYOND its axis, accumulating down
 *      the chain, each bend by ITS OWN angle. Geometry past a bend is rigid; only its
 *      placement changes, so nothing is re-meshed and every angle can ride a slider.
 *   3. miter — vertices ON a crease belong to both strips at once. Left in place they
 *      stretch the wall from thin to thick around the bend (the "fan"); the sharp-bend
 *      geometry that keeps thickness constant is the miter, which moves a crease vertex
 *      onto the bend's bisector at offset z/cos(θ/2) — the intersection of the two cap
 *      planes.
 *
 * ORDER is the correctness condition. Folding first and thickness-scaling after (as a group
 * scale) flattens every folded-up flange back into the sheet plane — at 0 mm the fold
 * becomes invisible, which is exactly the bug this module replaced.
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
 * How close to a bend axis a vertex must sit (mm, flat frame) to count as ON the crease and
 * take the miter. The bake splits strips exactly at the axes and quantization moves vertices
 * by ~0.005 mm, so 0.05 mm is an order of magnitude of slack without capturing real feature
 * geometry near a bend.
 */
export const DXF_MITER_EPSILON_MM = 0.05;

/**
 * The miter factor 1/cos(θ/2) diverges as a bend approaches 180° (the cap planes go
 * parallel). Clamped at the 150° value: past that the sheet is folding onto itself and a
 * finite corner reads better than geometry racing to infinity.
 */
const MITER_FACTOR_MAX = 1 / Math.cos((150 / 2) * (Math.PI / 180));

/**
 * Each bend rotates about its axis's CURRENT position — the image of the flat axis under
 * every earlier fold — not its flat one. Deciding membership by the folded position instead
 * (the obvious shortcut) under-folds a chain: after the first 90-degree fold the second
 * axis's strip has moved to lower X than the second axis, so it never receives its second
 * rotation and a U-channel folds into an L. Pivots are precomputed once, per option set,
 * carrying each bend's own angle and the cumulative rotation of everything before it.
 */
function computeFoldPivots(bends) {
  const pivots = [];
  let phi = 0;
  for (const { flatX, angle } of bends) {
    let px = flatX;
    let pz = 0;
    for (const pivot of pivots) {
      const dx = px - pivot.x;
      const dz = pz - pivot.z;
      px = pivot.x + dx * pivot.cos - dz * pivot.sin;
      pz = pivot.z + dx * pivot.sin + dz * pivot.cos;
    }
    pivots.push({
      flatX,
      angle,
      x: px,
      z: pz,
      cos: Math.cos(angle),
      sin: Math.sin(angle),
      // The frame this bend happens in: the sum of every earlier rotation. The miter's
      // bisector direction lives in this frame.
      phiBefore: phi,
    });
    phi += angle;
  }
  return pivots;
}

export function normalizeDxfFoldOptions({
  bendAxesX = null,
  bendAnglesRad = null,
  thicknessScale = 1,
  miterEpsilon = DXF_MITER_EPSILON_MM,
} = {}) {
  const axes = Array.isArray(bendAxesX) ? bendAxesX : [];
  const angles = Array.isArray(bendAnglesRad) ? bendAnglesRad : [];
  // Axis/angle pairs travel together through the sort — bend 2 must keep ITS angle when the
  // axes arrive unsorted.
  const bends = axes
    .map((flatX, index) => ({
      flatX,
      angle: Number.isFinite(angles[index]) ? angles[index] : 0,
    }))
    .filter((bend) => Number.isFinite(bend.flatX))
    .sort((a, b) => a.flatX - b.flatX);
  const requestedScale = Number.isFinite(thicknessScale) ? thicknessScale : 1;
  const scale = Math.max(requestedScale, DXF_FLAT_THICKNESS_SCALE);
  const anyAngle = bends.some((bend) => bend.angle !== 0);
  const pivots = anyAngle ? computeFoldPivots(bends) : [];
  return { bends, scale, anyAngle, pivots, miterEpsilon };
}

/** True when the transform would change nothing — the caller's "is there work" gate. */
export function dxfFoldIsIdentity(options) {
  const { scale, anyAngle } = normalizeDxfFoldOptions(options);
  return scale === 1 && !anyAngle;
}

/**
 * Transform one CAD-space point. Exported for the guide lines, which must ride the SAME
 * chain the sheet folds through or they detach from their creases.
 */
export function foldDxfPoint(x, y, z, { scale, anyAngle, pivots, miterEpsilon }) {
  let px = x;
  let pz = z * scale;
  if (!anyAngle) {
    return [px, y, pz];
  }
  for (const pivot of pivots) {
    // Membership is by FLAT x — where the vertex sits in the unfolded pattern — never by
    // where earlier folds have moved it.
    if (x < pivot.flatX - miterEpsilon) {
      break;
    }
    if (x <= pivot.flatX + miterEpsilon) {
      // ON the crease: the vertex belongs to both strips, so it takes the miter — offset
      // along the bend's bisector, stretched by 1/cos(θ/2), which is where the two
      // constant-thickness cap planes intersect. Without this the wall fans from thin to
      // thick around every bend.
      const factor = Math.min(1 / Math.max(Math.cos(pivot.angle / 2), 1e-6), MITER_FACTOR_MAX);
      const bisector = pivot.phiBefore + pivot.angle / 2 + Math.PI / 2;
      // The cap offset is the vertex's distance from the neutral plane — invariant under the
      // earlier rigid folds, so it is read from the FLAT z; the pivot position already
      // carries the chain.
      const offset = z * scale;
      px = pivot.x + Math.cos(bisector) * offset * factor;
      pz = pivot.z + Math.sin(bisector) * offset * factor;
      return [px, y, pz];
    }
    if (pivot.angle !== 0) {
      const dx = px - pivot.x;
      const dz = pz - pivot.z;
      px = pivot.x + dx * pivot.cos - dz * pivot.sin;
      pz = pivot.z + dx * pivot.sin + dz * pivot.cos;
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
 * X, elevated just above the top surface — riding the miter, so at any angle it sits on the
 * outer corner of its own crease.
 */
export function dxfBendGuideSegments(original, options) {
  const resolved = normalizeDxfFoldOptions(options);
  if (!resolved.bends.length) {
    return new Float32Array(0);
  }
  const { yMin, yMax, zMax } = dxfFlatPatternExtents(original);
  const zTop = zMax + DXF_BEND_GUIDE_ELEVATION_MM / Math.max(resolved.scale, 1e-6);
  const segments = new Float32Array(resolved.bends.length * 6);
  let cursor = 0;
  for (const bend of resolved.bends) {
    const [ax, ay, az] = foldDxfPoint(bend.flatX, yMin, zTop, resolved);
    const [bx, by, bz] = foldDxfPoint(bend.flatX, yMax, zTop, resolved);
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
