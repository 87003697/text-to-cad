import assert from "node:assert/strict";
import test from "node:test";

import {
  DXF_FLAT_THICKNESS_SCALE,
  dxfBendGuideSegments,
  dxfFoldIsIdentity,
  foldDxfPoint,
  normalizeDxfFoldOptions,
  transformDxfPreviewPositions,
} from "./foldPreview.js";

// foldDxfPoint takes RESOLVED options (with precomputed pivots); normalize is the API.
const resolve = (bendAxesX, bendAngleRad, thicknessScale = 1) =>
  normalizeDxfFoldOptions({ bendAxesX, bendAngleRad, thicknessScale });

const close = (actual, expected, label) => {
  assert.ok(Math.abs(actual - expected) < 1e-9, `${label}: ${actual} should be ~${expected}`);
};

test("a 90-degree fold stands geometry beyond the axis straight up", () => {
  const [x, y, z] = foldDxfPoint(1, 5, 0, resolve([0], Math.PI / 2));
  close(x, 0, "x lands on the axis");
  assert.equal(y, 5, "y is untouched by a fold about an X axis");
  close(z, 1, "the run beyond the axis becomes rise");
});

test("a vertex on or before the axis does not move", () => {
  const options = resolve([10], Math.PI / 2);
  assert.deepEqual(foldDxfPoint(10, 0, 0, options), [10, 0, 0]);
  assert.deepEqual(foldDxfPoint(3, 0, 0, options), [3, 0, 0]);
});

test("two bends accumulate down the chain", () => {
  // Axes at 0 and 1, both 90 degrees: the strip past the SECOND bend has been folded twice,
  // so a point 1 beyond it comes back over the top — x returns toward the first axis.
  const [x, , z] = foldDxfPoint(2, 0, 0, resolve([0, 1], Math.PI / 2));
  close(x, -1, "folded twice: up, then back over");
  close(z, 1, "sits at the height of the first fold");
});

test("direction is the sign of the angle", () => {
  const up = foldDxfPoint(1, 0, 0, resolve([0], Math.PI / 2));
  const down = foldDxfPoint(1, 0, 0, resolve([0], -Math.PI / 2));
  close(up[2], 1, "up folds +Z");
  close(down[2], -1, "down folds -Z");
});

test("thickness scales Z BEFORE the fold, so folded walls keep their thickness", () => {
  // The bug this module exists to prevent: with thickness applied AFTER the fold as a group
  // scale, a folded-up flange (which now extends in Z) is flattened back into the sheet
  // plane. Applied before, the cap offset rides the fold instead.
  const [x, , z] = foldDxfPoint(1, 0, 0.5, resolve([0], Math.PI / 2, 4));
  close(x, -2, "the scaled cap offset folds with the strip");
  close(z, 1, "the run still becomes rise");
});

test("transformDxfPreviewPositions always folds from the FLAT buffer", () => {
  const flat = Float32Array.from([2, 0, 0]);
  const output = new Float32Array(3);
  const options = { bendAxesX: [0], bendAngleRad: Math.PI / 2 };
  transformDxfPreviewPositions(flat, output, options);
  const once = [...output];
  // Re-running with the same options over the same flat source is idempotent — the pose
  // never compounds, which is what lets a slider re-run this every change.
  transformDxfPreviewPositions(flat, output, options);
  assert.deepEqual([...output], once);
  assert.deepEqual([...flat], [2, 0, 0], "the flat source is never written");
});

test("identity is only identity", () => {
  assert.ok(dxfFoldIsIdentity({ thicknessScale: 1 }));
  assert.ok(dxfFoldIsIdentity({ bendAxesX: [5], bendAngleRad: 0, thicknessScale: 1 }));
  assert.ok(!dxfFoldIsIdentity({ thicknessScale: 3 }));
  assert.ok(!dxfFoldIsIdentity({ bendAxesX: [5], bendAngleRad: 0.1, thicknessScale: 1 }));
  // Zero thickness is a transform (the flat collapse), not identity.
  assert.ok(!dxfFoldIsIdentity({ thicknessScale: 0 }));
});

test("zero thickness collapses to a hair, never to a singular scale", () => {
  const [, , z] = foldDxfPoint(0, 0, 0.5, resolve([], 0, 0));
  assert.ok(z > 0 && z <= 0.5 * DXF_FLAT_THICKNESS_SCALE + 1e-12, `hair-thin, got ${z}`);
});

test("bend guides ride their creases through earlier folds", () => {
  // Sheet spanning y 0..70 with axes at 30 and 110, folded 90 degrees. The FIRST guide sits
  // at its own axis unmoved; the SECOND has ridden the first fold, so its x collapses to the
  // first axis and its height is the strip length between the bends.
  const flat = Float32Array.from([0, 0, 0.5, 140, 70, -0.5]);
  const segments = dxfBendGuideSegments(flat, {
    bendAxesX: [30, 110],
    bendAngleRad: Math.PI / 2,
    thicknessScale: 1,
  });
  assert.equal(segments.length, 12, "two guides, two endpoints each");
  close(segments[0], 30, "first guide stays on its axis");
  assert.equal(segments[1], 0, "spans from yMin");
  assert.equal(segments[4], 70, "to yMax");
  assert.ok(
    segments[6] > 29 && segments[6] < 30,
    `second guide folds back beside the first axis (offset by its elevation), got ${segments[6]}`
  );
  assert.ok(segments[8] > 79 && segments[8] < 81, `second guide rises ~80, got ${segments[8]}`);
});

test("no axes means no guides", () => {
  assert.equal(dxfBendGuideSegments(Float32Array.from([0, 0, 0]), { bendAxesX: [] }).length, 0);
});
