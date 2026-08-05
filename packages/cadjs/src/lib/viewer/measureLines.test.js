import assert from "node:assert/strict";
import test from "node:test";

import {
  MEASURE_LINE_DRAFT_ID,
  clampMeasureChipPosition,
  createMeasureLineSegments,
  measureRulerLineSegments,
  midpointOfPoints
} from "./measureLines.js";

const PICK_A = { point: [0, 0, 0] };
const PICK_B = { point: [3, 4, 0] };

test("measureRulerLineSegments returns committed segments from measurements", () => {
  const segments = measureRulerLineSegments({
    measurements: [
      { id: "m1", pickA: PICK_A, pickB: PICK_B, measurement: { euclidean: 5 } }
    ]
  });
  assert.deepEqual(segments, [
    { id: "m1", start: [0, 0, 0], end: [3, 4, 0], committed: true }
  ]);
});

test("measureRulerLineSegments includes the draft hover segment", () => {
  const segments = measureRulerLineSegments({
    draft: { anchor: PICK_A, hover: PICK_B },
    measurements: []
  });
  assert.deepEqual(segments, [
    { id: MEASURE_LINE_DRAFT_ID, start: [0, 0, 0], end: [3, 4, 0], committed: false }
  ]);
});

test("measureRulerLineSegments skips malformed entries", () => {
  const segments = measureRulerLineSegments({
    measurements: [
      { id: "bad", pickA: null, pickB: PICK_B, measurement: { euclidean: 1 } },
      { id: "bad2", pickA: PICK_A, pickB: { point: [NaN, 0, 0] }, measurement: { euclidean: 1 } }
    ],
    draft: { anchor: null, hover: PICK_B }
  });
  assert.deepEqual(segments, []);
});

test("measureRulerLineSegments returns an empty list without state", () => {
  assert.deepEqual(measureRulerLineSegments(null), []);
  assert.deepEqual(measureRulerLineSegments({}), []);
});

test("midpointOfPoints averages finite point pairs", () => {
  assert.deepEqual(midpointOfPoints([0, 0, 0], [2, 4, 6]), [1, 2, 3]);
  assert.equal(midpointOfPoints(null, [1, 2, 3]), null);
  assert.equal(midpointOfPoints([0, 0, 0], [1, 2]), null);
});

test("clampMeasureChipPosition keeps chips inside the viewport", () => {
  const clamped = clampMeasureChipPosition({ x: -50, y: 500 }, { width: 400, height: 300 }, { width: 100, height: 40 });
  assert.equal(clamped.x, 50);
  assert.equal(clamped.y, 300 - 20);
  assert.deepEqual(clampMeasureChipPosition({ x: 200, y: 150 }, { width: 400, height: 300 }, { width: 100, height: 40 }), { x: 200, y: 150 });
});

test("clampMeasureChipPosition rejects invalid inputs", () => {
  assert.equal(clampMeasureChipPosition(null, { width: 400, height: 300 }), null);
  assert.equal(clampMeasureChipPosition({ x: 1, y: 1 }, null), null);
  assert.equal(clampMeasureChipPosition({ x: 1, y: 1 }, { width: 0, height: 300 }), null);
});

test("createMeasureLineSegments tolerates runtimes without three line primitives", () => {
  assert.deepEqual(createMeasureLineSegments({}, null), { committed: null, draft: null });
  assert.deepEqual(
    createMeasureLineSegments({ THREE: {} }, { measurements: [], draft: null }),
    { committed: null, draft: null }
  );
});
