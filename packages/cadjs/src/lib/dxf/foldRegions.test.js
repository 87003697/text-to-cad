import assert from "node:assert/strict";
import { test } from "node:test";
import { Matrix4, Vector3 } from "three";

import {
  buildFoldBridgeGeometry,
  buildFoldLine,
  buildRegionPlacements,
  clipLoopByHalfPlane,
  decomposeFoldRegions,
  foldHingeMatrix,
  foldSignedDistance,
  outerMaterialSpanAlongFold
} from "./foldRegions.js";

const PLATE = [[0, 0], [100, 0], [100, 60], [0, 60]];
const L_BLANK = [[0, 0], [100, 0], [100, 30], [30, 30], [30, 80], [0, 80]];

function fold(start, end, angleDeg, { halfThicknessMm = 1 } = {}) {
  return buildFoldLine({
    bendLine: { start, end },
    angleRadians: (angleDeg * Math.PI) / 180,
    halfThicknessMm
  });
}

function place(matrix, point2d) {
  return new Vector3(point2d[0], 0, point2d[1]).applyMatrix4(matrix).toArray();
}

test("a fold line takes its direction from its own endpoints, at any angle", () => {
  const diagonal = fold([0, 0], [10, 10], 90);
  assert.ok(Math.abs(diagonal.direction[0] - Math.SQRT1_2) < 1e-9);
  assert.ok(Math.abs(diagonal.direction[1] - Math.SQRT1_2) < 1e-9);
  // The normal is the direction turned 90 degrees, so the two are perpendicular.
  assert.ok(Math.abs((diagonal.direction[0] * diagonal.normal[0]) + (diagonal.direction[1] * diagonal.normal[1])) < 1e-9);
  // The band's flat width IS the arc length it becomes: 2 * halfWidth = radius * angle.
  assert.ok(Math.abs((2 * diagonal.halfWidth) - (diagonal.neutralRadius * Math.PI / 2)) < 1e-9);
});

test("a zero-length bend line is not a fold line", () => {
  assert.equal(fold([5, 5], [5, 5], 90), null);
});

test("half-plane clipping keeps the requested side and cuts on the band edge", () => {
  const line = fold([50, 0], [50, 60], 90);
  const positive = clipLoopByHalfPlane(PLATE, line, line.halfWidth, true);
  for (const point of positive) {
    assert.ok(foldSignedDistance(line, point) >= line.halfWidth - 1e-3);
  }
  const negative = clipLoopByHalfPlane(PLATE, line, -line.halfWidth, false);
  for (const point of negative) {
    assert.ok(foldSignedDistance(line, point) <= -line.halfWidth + 1e-3);
  }
  // Between them they account for the plate minus the band.
  assert.ok(positive.length >= 4 && negative.length >= 4);
});

test("material span along a fold is measured where the line crosses the contour", () => {
  assert.deepEqual(outerMaterialSpanAlongFold(PLATE, fold([50, 0], [50, 60], 90)), { min: 0, max: 60 });
  // A horizontal line through the same plate spans its width instead.
  const horizontal = outerMaterialSpanAlongFold(PLATE, fold([0, 30], [100, 30], 90));
  assert.deepEqual(horizontal, { min: 0, max: 100 });
});

test("the hinge agrees with the shipped vertical-bend formula", () => {
  // The vertical case is the one the X-slab mesher already drew correctly, so it is the
  // reference: the general hinge must reproduce it exactly, or every previously-good preview
  // would shift.
  const radius = 2.2;
  const angle = Math.PI / 2;
  const halfWidth = (radius * angle) / 2;
  const x = 50;
  const shipped = new Matrix4()
    .makeTranslation(x - halfWidth + (radius * Math.sin(angle)), radius * (1 - Math.cos(angle)), 0)
    .multiply(new Matrix4().makeRotationZ(angle))
    .multiply(new Matrix4().makeTranslation(-(x + halfWidth), 0, 0));
  // Same fold, expressed with the normal pointing +x so the two agree on which side is which.
  const line = {
    bendLine: { start: [x, 0], end: [x, 60] },
    origin: [x, 0],
    direction: [0, 1],
    normal: [1, 0],
    angleRadians: angle,
    neutralRadius: radius,
    halfWidth
  };
  const hinge = foldHingeMatrix(line, 1);
  for (const point of [[x + halfWidth, 0], [x + 30, 0], [x + 30, -9], [x + halfWidth, 17]]) {
    const fromHinge = place(hinge, point).map((value) => Number(value.toFixed(6)));
    const fromShipped = new Vector3(point[0], 0, point[1]).applyMatrix4(shipped).toArray()
      .map((value) => Number(value.toFixed(6)));
    assert.deepEqual(fromHinge, fromShipped, `point ${JSON.stringify(point)}`);
  }
});

test("a fold lifts the child out of the sheet whichever side it is on", () => {
  // The symmetric case that caught a handedness mistake: a U channel folded one flange up and
  // the other DOWN, because the fold's local frame flipped with the side.
  const line = fold([50, 0], [50, 60], 90);
  for (const side of [1, -1]) {
    const hinge = foldHingeMatrix(line, side);
    const entry = [50 + (line.normal[0] * line.halfWidth * side), 60 * 0.5];
    const beyond = [
      entry[0] + (line.normal[0] * 15.9 * side),
      entry[1] + (line.normal[1] * 15.9 * side)
    ];
    const [, entryY] = place(hinge, entry);
    const [, beyondY] = place(hinge, beyond);
    assert.ok(entryY > 0, `side ${side} entry must rise`);
    assert.ok(Math.abs(beyondY - (entryY + 15.9)) < 1e-6, `side ${side} must stay rigid`);
  }
});

test("the fold is rigid: distances inside the child face survive it", () => {
  const line = fold([0, 20], [100, 20], 120);
  const hinge = foldHingeMatrix(line, 1);
  const a = [10, 50];
  const b = [70, 35];
  const flat = Math.hypot(a[0] - b[0], a[1] - b[1]);
  const [ax, ay, az] = place(hinge, a);
  const [bx, by, bz] = place(hinge, b);
  assert.ok(Math.abs(Math.hypot(ax - bx, ay - by, az - bz) - flat) < 1e-6);
});

test("the curved band ends exactly where the hinge puts the child", () => {
  // The continuity that matters: a gap here is what the reported spikes were made of.
  for (const line of [
    fold([50, 0], [50, 60], 90),
    fold([0, 20], [100, 20], 90),
    fold([0, 0], [60, 60], 90),
    fold([50, 0], [50, 60], -90)
  ]) {
    const hinge = foldHingeMatrix(line, 1);
    const entry = [
      line.origin[0] + (line.normal[0] * line.halfWidth),
      line.origin[1] + (line.normal[1] * line.halfWidth)
    ];
    const hinged = place(hinge, entry);
    const bridge = buildFoldBridgeGeometry({
      foldLine: line,
      side: 1,
      span: { min: 0, max: 1 },
      halfThickness: 0,
      segments: 64
    });
    const nearest = bridge.triangles.flat().reduce((best, point) => {
      const distance = Math.hypot(point[0] - hinged[0], point[1] - hinged[1], point[2] - hinged[2]);
      return distance < best ? distance : best;
    }, Infinity);
    assert.ok(nearest < 1e-6, `band must meet the face (gap ${nearest})`);
  }
});

test("a fold splits only the face its segment crosses", () => {
  // The L blank: one fold per arm. Splitting by each fold's infinite LINE instead cut through
  // the other arm as well, and put the faces in a cycle rather than a tree.
  const folds = [fold([60, 0], [60, 30], 90), fold([0, 55], [30, 55], 90)];
  const { regions, adjacency } = decomposeFoldRegions(L_BLANK, folds);
  assert.equal(regions.length, 3, "two folds, three faces");
  assert.equal(adjacency.length, 2, "two hinges, so a tree and not a cycle");
  const { rootIndex, parents } = buildRegionPlacements(regions, folds, adjacency);
  assert.equal(parents.filter((parent) => parent === null).length, 1, "exactly one root");
  assert.ok(regions[rootIndex].area >= Math.max(...regions.map((region) => region.area)) - 1e-9);
});

test("a fold that cuts no face in two is refused, with the shortfall", () => {
  assert.throws(
    () => decomposeFoldRegions(PLATE, [fold([60, 20], [60, 60], 90)]),
    (error) => {
      assert.match(error.message, /runs edge to edge/u);
      assert.match(error.message, /20\.000 mm short/u);
      return true;
    }
  );
});

test("both faces of a two-fold plate keep their flat area", () => {
  const folds = [fold([30, 0], [30, 60], 90), fold([70, 0], [70, 60], 90)];
  const { regions } = decomposeFoldRegions(PLATE, folds);
  const bandArea = folds.reduce((total, line) => total + (2 * line.halfWidth * 60), 0);
  const total = regions.reduce((sum, region) => sum + region.area, 0);
  assert.ok(Math.abs(total - ((100 * 60) - bandArea)) < 1e-6, "faces plus bands account for the blank");
});
