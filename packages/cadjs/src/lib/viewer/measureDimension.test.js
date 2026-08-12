import assert from "node:assert/strict";
import test from "node:test";

import { PerspectiveCamera } from "three";

import {
  drawMeasureDimension,
  drawMeasureSnapChip,
  drawMeasureSnapMarker,
  drawPulsingEndRing,
  measureLabelText,
  screenDimensionLayout,
  screenSpaceDimensionLayout
} from "./measureDimension.js";
import { measureDimensionSegments } from "./measureLines.js";

function makePerspectiveCamera() {
  const camera = new PerspectiveCamera(60, 1, 0.1, 100);
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

function makeRect() {
  return { left: 0, top: 0, width: 800, height: 600 };
}

function mockContext() {
  const noop = () => {};
  return {
    save: noop,
    restore: noop,
    beginPath: noop,
    moveTo: noop,
    lineTo: noop,
    closePath: noop,
    rect: noop,
    roundRect: noop,
    arc: noop,
    fill: noop,
    stroke: noop,
    fillRect: noop,
    strokeRect: noop,
    fillText: noop,
    measureText: () => ({ width: 30 })
  };
}

test("measureLabelText formats a plain euclidean measurement", () => {
  assert.equal(
    measureLabelText({ euclidean: 15.456, perpendicular: null, unit: "mm" }),
    "15.46 mm"
  );
});

test("measureLabelText prefixes the perpendicular value with a perpendicular sign", () => {
  assert.equal(
    measureLabelText({ euclidean: 16.2, perpendicular: 15.456, unit: "mm" }),
    "⟂ 15.46 mm"
  );
});

test("measureLabelText returns an empty string for missing measurements", () => {
  assert.equal(measureLabelText(null), "");
  assert.equal(measureLabelText({}), "");
  assert.equal(measureLabelText({ euclidean: NaN, unit: "mm" }), "");
});

test("screenSpaceDimensionLayout projects endpoints and builds stable screen-space figure", () => {
  const layout = screenSpaceDimensionLayout(
    { point: [0, 0, 0] },
    { point: [1, 0, 0] },
    null,
    makePerspectiveCamera(),
    makeRect()
  );
  assert.ok(layout);
  assert.equal(layout.rings.length, 2);
  assert.equal(layout.witnesses.length, 2);
  assert.equal(layout.dimensionLine.length, 2);
  assert.equal(layout.arrows.length, 2);
  assert.ok(Number.isFinite(layout.label.x));
  assert.ok(Number.isFinite(layout.label.y));
  assert.deepEqual(layout.rings[0], { x: 400, y: 300 });
  assert.ok(layout.rings[1].x > 400);
  assert.equal(layout.rings[1].y, 300);
});

test("screenSpaceDimensionLayout returns null for invalid or unprojectable points", () => {
  assert.equal(screenSpaceDimensionLayout(null, { point: [1, 0, 0] }, null, makePerspectiveCamera(), makeRect()), null);
  assert.equal(screenSpaceDimensionLayout({ point: [0, 0, 0] }, { point: [0, 0, 9] }, null, makePerspectiveCamera(), makeRect()), null);
});

test("screenDimensionLayout projects a world-space construction into client space", () => {
  const segments = measureDimensionSegments(
    { point: [0, 0, 0] },
    { point: [1, 0, 0] },
    null,
    { camera: makePerspectiveCamera() }
  );
  const layout = screenDimensionLayout(segments, makePerspectiveCamera(), makeRect());
  assert.ok(layout);
  assert.equal(layout.rings.length, 2);
  assert.equal(layout.witnesses.length, 2);
  assert.equal(layout.dimensionLine.length, 2);
  assert.equal(layout.ticks.length, 2);
  assert.ok(Number.isFinite(layout.label.x));
  assert.ok(Number.isFinite(layout.label.y));
  assert.deepEqual(layout.rings[0], { x: 400, y: 300 });
  assert.ok(layout.rings[1].x > 400);
  assert.equal(layout.rings[1].y, 300);
});

test("screenDimensionLayout returns null for missing segments or camera", () => {
  assert.equal(screenDimensionLayout(null, makePerspectiveCamera(), makeRect()), null);
  assert.equal(
    screenDimensionLayout(
      measureDimensionSegments({ point: [0, 0, 0] }, { point: [1, 0, 0] }, null, {
        camera: makePerspectiveCamera()
      }),
      null,
      makeRect()
    ),
    null
  );
});

test("screenDimensionLayout returns null when a construction point is behind the camera", () => {
  const segments = measureDimensionSegments(
    { point: [0, 0, 0] },
    { point: [0, 0, 9] },
    null,
    { camera: makePerspectiveCamera() }
  );
  assert.equal(screenDimensionLayout(segments, makePerspectiveCamera(), makeRect()), null);
});

test("drawMeasureDimension tolerates a missing context or layout", () => {
  assert.doesNotThrow(() => drawMeasureDimension(null, null));
  assert.doesNotThrow(() => drawMeasureDimension(mockContext(), null));
  assert.doesNotThrow(() => drawMeasureDimension(null, { rings: [], witnesses: [] }));
});

test("drawMeasureDimension draws without throwing on a valid layout", () => {
  const layout = screenSpaceDimensionLayout(
    { point: [0, 0, 0] },
    { point: [1, 0, 0] },
    null,
    makePerspectiveCamera(),
    makeRect()
  );
  assert.doesNotThrow(() => {
    drawMeasureDimension(mockContext(), layout, {
      label: "15.46 mm",
      bounds: makeRect()
    });
  });
});

test("drawMeasureDimension clamps the label chip inside the canvas bounds", () => {
  const layout = screenSpaceDimensionLayout(
    { point: [0, 0, 0] },
    { point: [1, 0, 0] },
    null,
    makePerspectiveCamera(),
    makeRect()
  );
  const calls = [];
  const context = mockContext();
  context.roundRect = (x) => {
    calls.push(x);
  };
  context.rect = (x) => {
    calls.push(x);
  };
  context.measureText = () => ({ width: 200 });
  drawMeasureDimension(context, layout, {
    label: "very long label",
    bounds: { width: 240, height: 600 }
  });
  assert.ok(calls.length > 0);
  assert.ok(calls[0] >= 4, `label chip starts at ${calls[0]}, expected >= 4`);
  assert.ok(calls[0] <= 240 - 200 - 12 - 4, `label chip starts at ${calls[0]}, expected <= ${240 - 200 - 12 - 4}`);
});

test("drawPulsingEndRing tolerates missing context or point", () => {
  assert.doesNotThrow(() => drawPulsingEndRing(null, null));
  assert.doesNotThrow(() => drawPulsingEndRing(mockContext(), null));
  assert.doesNotThrow(() => drawPulsingEndRing(mockContext(), { x: 10, y: 10 }));
});

function recordingContext() {
  const calls = [];
  const record = (name) => (...args) => calls.push({ name, args });
  return {
    calls,
    save: record("save"),
    restore: record("restore"),
    beginPath: record("beginPath"),
    moveTo: record("moveTo"),
    lineTo: record("lineTo"),
    arc: record("arc"),
    rect: record("rect"),
    roundRect: record("roundRect"),
    closePath: record("closePath"),
    fill: record("fill"),
    stroke: record("stroke"),
    fillText: record("fillText"),
    measureText: () => ({ width: 60 })
  };
}

test("drawMeasureSnapMarker draws a distinct shape per snap kind", () => {
  const shapeFor = (snapKind) => {
    const context = recordingContext();
    drawMeasureSnapMarker(context, { x: 50, y: 50 }, { snapKind, now: 0 });
    return context.calls.map((call) => call.name);
  };
  // A vertex is a filled square, an edge a filled ring, a face a hollow diamond.
  assert.ok(shapeFor("vertex").includes("rect"));
  assert.ok(shapeFor("edge").includes("arc"));
  assert.ok(shapeFor("face").includes("closePath"));
  // An unsnapped point gets the crosshair only, so it cannot be mistaken for a snap.
  const free = shapeFor("free");
  assert.ok(!free.includes("arc") && !free.includes("rect") && !free.includes("closePath"));
  assert.ok(free.includes("stroke"));
});

test("drawMeasureSnapMarker ignores unusable positions", () => {
  const context = recordingContext();
  drawMeasureSnapMarker(context, { x: NaN, y: 10 }, { snapKind: "edge" });
  drawMeasureSnapMarker(context, null, { snapKind: "edge" });
  assert.equal(context.calls.length, 0);
});

test("drawMeasureSnapChip keeps the caption inside the viewport", () => {
  const context = recordingContext();
  // Near the bottom-right corner the chip would otherwise be drawn off-screen.
  drawMeasureSnapChip(context, { x: 795, y: 595 }, "Edge  L 25.13 mm", { bounds: { width: 800, height: 600 } });
  const chip = context.calls.find((call) => call.name === "roundRect");
  assert.ok(chip);
  const [x, y, width, height] = chip.args;
  assert.ok(x + width <= 800, "chip runs past the right edge");
  assert.ok(y + height <= 600, "chip runs past the bottom edge");

  const empty = recordingContext();
  drawMeasureSnapChip(empty, { x: 10, y: 10 }, "", { bounds: { width: 800, height: 600 } });
  assert.equal(empty.calls.length, 0);
});
