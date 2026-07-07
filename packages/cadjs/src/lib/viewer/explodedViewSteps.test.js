import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import {
  applyExplodedViewProgress,
  clearExplodedViewRecords,
  compileExplodedView,
  easeExplodedViewProgress,
  explodedViewBounds,
  explodedViewStepProgress,
  explodedViewTrailSegments,
  normalizeExplodedViewDocument,
  normalizeExplodeStep,
  targetMatchesRecord
} from "./explodedViewSteps.js";

function record(partId, bounds) {
  return {
    partId,
    mesh: new THREE.Object3D(),
    partBounds: bounds
  };
}

function translateStep(id, targets, axis, distance) {
  return { id, type: "translate", targets, axis, distance };
}

test("normalizeExplodedViewDocument fills defaults and drops invalid steps", () => {
  const doc = normalizeExplodedViewDocument({
    enabled: true,
    amount: 5,
    order: "sequential",
    trails: true,
    steps: [
      translateStep("s1", ["o1.2"], [0, 0, 2], 10), // axis normalized to unit
      { type: "translate", targets: [] }, // no targets -> dropped
      null // dropped
    ]
  });
  assert.equal(doc.enabled, true);
  assert.equal(doc.amount, 1); // clamped 0..1
  assert.equal(doc.order, "sequential");
  assert.equal(doc.trails, true);
  assert.equal(doc.steps.length, 1);
  assert.deepEqual(doc.steps[0].axis, [0, 0, 1]);
  assert.equal(doc.steps[0].distance, 10);
  assert.equal(doc.auto.mode, "auto");
  assert.equal(doc.auto.depth, 1);
});

test("normalizeExplodedViewDocument is deterministic for equality via JSON", () => {
  const input = { enabled: true, steps: [translateStep("s1", ["o1.2"], [1, 0, 0], 3)] };
  const a = normalizeExplodedViewDocument(input);
  const b = normalizeExplodedViewDocument(JSON.parse(JSON.stringify(input)));
  assert.equal(JSON.stringify(a), JSON.stringify(b));
});

test("normalizeExplodeStep canonicalizes each step type", () => {
  const rotate = normalizeExplodeStep({ type: "rotate", targets: "o1.3", angle: 90 });
  assert.equal(rotate.type, "rotate");
  assert.deepEqual(rotate.targets, ["o1.3"]);
  assert.equal(rotate.angleDeg, 90);
  const radial = normalizeExplodeStep({ type: "radial", targets: ["o1.3"], distance: 4 });
  assert.equal(radial.type, "radial");
  assert.equal(radial.distance, 4);
  const bad = normalizeExplodeStep({ type: "translate", targets: [] });
  assert.equal(bad, null);
});

test("targetMatchesRecord matches occurrence-path prefixes", () => {
  assert.equal(targetMatchesRecord(["o1", "2"], "o1.2"), true);
  assert.equal(targetMatchesRecord(["o1", "2"], "o1.2.5"), true); // subtree
  assert.equal(targetMatchesRecord(["o1", "2"], "o1.3"), false);
  assert.equal(targetMatchesRecord(["o1", "2"], "o1"), false); // shorter than target
  assert.equal(targetMatchesRecord(["part:3"], "part:3"), true); // non-occurrence exact
});

test("translate step moves matched subtree and clears the rest", () => {
  const records = [
    record("o1.1", { min: [-1, -1, 0], max: [1, 1, 2] }),
    record("o1.2", { min: [-1, -1, 0], max: [1, 1, 2] }),
    record("o1.2.5", { min: [-1, -1, 0], max: [1, 1, 2] })
  ];
  const doc = {
    enabled: true,
    steps: [translateStep("s1", ["o1.2"], [0, 0, 1], 10)]
  };
  const compiled = compileExplodedView(THREE, doc, records, { min: [-1, -1, 0], max: [1, 1, 2] });
  assert.equal(compiled.entries.length, 2); // o1.2 and o1.2.5

  applyExplodedViewProgress(THREE, compiled, 1);
  assert.equal(records[0].explodedViewMatrix, null); // o1.1 untouched
  assert.equal(records[1].explodedViewMatrix.elements[14], 10);
  assert.equal(records[2].explodedViewMatrix.elements[14], 10);

  applyExplodedViewProgress(THREE, compiled, 0.5);
  assert.equal(records[1].explodedViewMatrix.elements[14], 5);

  applyExplodedViewProgress(THREE, compiled, 0);
  assert.equal(records[1].explodedViewMatrix, null); // fully collapsed -> null
});

test("previously-set matrices are cleared for records outside the active steps", () => {
  const records = [record("o1.1", { min: [0, 0, 0], max: [1, 1, 1] })];
  records[0].explodedViewMatrix = new THREE.Matrix4().makeTranslation(9, 9, 9);
  const compiled = compileExplodedView(
    THREE,
    { enabled: true, steps: [translateStep("s1", ["o1.9"], [0, 0, 1], 5)] },
    records,
    null
  );
  applyExplodedViewProgress(THREE, compiled, 1);
  assert.equal(records[0].explodedViewMatrix, null);
});

test("explodedViewStepProgress slices sequential timeline", () => {
  assert.equal(explodedViewStepProgress("simultaneous", 0, 3, 0.4), 0.4);
  // sequential, 2 steps: step0 covers [0,0.5], step1 covers [0.5,1]
  assert.equal(explodedViewStepProgress("sequential", 0, 2, 0.25), 0.5);
  assert.equal(explodedViewStepProgress("sequential", 1, 2, 0.25), 0);
  assert.equal(explodedViewStepProgress("sequential", 1, 2, 0.75), 0.5);
});

test("sequential order animates steps over their own sub-range", () => {
  const records = [
    record("o1.1", { min: [0, 0, 0], max: [1, 1, 1] }),
    record("o1.2", { min: [0, 0, 0], max: [1, 1, 1] })
  ];
  const doc = {
    enabled: true,
    order: "sequential",
    steps: [
      translateStep("s1", ["o1.1"], [1, 0, 0], 10),
      translateStep("s2", ["o1.2"], [0, 1, 0], 10)
    ]
  };
  const compiled = compileExplodedView(THREE, doc, records, null);
  applyExplodedViewProgress(THREE, compiled, 0.25);
  assert.equal(records[0].explodedViewMatrix.elements[12], 5); // step0 half done
  assert.equal(records[1].explodedViewMatrix, null); // step1 not started
  applyExplodedViewProgress(THREE, compiled, 0.75);
  assert.equal(records[0].explodedViewMatrix.elements[12], 10); // step0 complete
  assert.equal(records[1].explodedViewMatrix.elements[13], 5); // step1 half done
});

test("radial step pushes opposing groups outward in the perpendicular plane", () => {
  const records = [
    record("o1.1", { min: [-3, -1, -1], max: [-1, 1, 1] }), // left of axis
    record("o1.2", { min: [1, -1, -1], max: [3, 1, 1] }) // right of axis
  ];
  const doc = {
    enabled: true,
    steps: [{ id: "s1", type: "radial", targets: ["o1.1", "o1.2"], axis: [0, 0, 1], center: [0, 0, 0], distance: 5 }]
  };
  const compiled = compileExplodedView(THREE, doc, records, { min: [-3, -1, -1], max: [3, 1, 1] });
  applyExplodedViewProgress(THREE, compiled, 1);
  assert.ok(records[0].explodedViewMatrix.elements[12] < 0); // left goes -x
  assert.ok(records[1].explodedViewMatrix.elements[12] > 0); // right goes +x
  assert.equal(records[0].explodedViewMatrix.elements[14], 0); // height preserved
});

test("rotate step produces a rotation about its origin", () => {
  const records = [record("o1.1", { min: [1, -1, -1], max: [3, 1, 1] })];
  const doc = {
    enabled: true,
    steps: [{ id: "s1", type: "rotate", targets: ["o1.1"], axis: [0, 0, 1], origin: [0, 0, 0], angleDeg: 90 }]
  };
  const compiled = compileExplodedView(THREE, doc, records, null);
  applyExplodedViewProgress(THREE, compiled, 1);
  const m = records[0].explodedViewMatrix;
  // 90deg about Z: (1,0,0) -> (0,1,0), so upper-left is ~0.
  assert.ok(Math.abs(m.elements[0]) < 1e-6);
  assert.ok(Math.abs(m.elements[1] - 1) < 1e-6);
});

test("explodedViewBounds expands along the explode translation", () => {
  const records = [
    record("o1.1", { min: [-1, -1, 0], max: [1, 1, 2] }),
    record("o1.2", { min: [-1, -1, 0], max: [1, 1, 2] })
  ];
  const doc = { enabled: true, steps: [translateStep("s1", ["o1.2"], [0, 0, 1], 10)] };
  const compiled = compileExplodedView(THREE, doc, records, { min: [-1, -1, 0], max: [1, 1, 2] });
  const bounds = explodedViewBounds(THREE, compiled, { min: [-1, -1, 0], max: [1, 1, 2] }, 1);
  assert.ok(bounds.max[2] > 8);
});

test("explodedViewTrailSegments trace assembled -> exploded per group", () => {
  const records = [record("o1.2", { min: [-1, -1, 0], max: [1, 1, 2] })];
  const doc = { enabled: true, trails: true, steps: [translateStep("s1", ["o1.2"], [0, 0, 1], 10)] };
  const compiled = compileExplodedView(THREE, doc, records, null);
  const full = explodedViewTrailSegments(THREE, compiled, 1);
  assert.equal(full.length, 1);
  assert.equal(full[0].from[2], 1); // group center z
  assert.equal(full[0].to[2], 11);
  const half = explodedViewTrailSegments(THREE, compiled, 0.5);
  assert.equal(half[0].to[2], 6);
});

test("clearExplodedViewRecords nulls every record matrix", () => {
  const records = [record("o1.1", { min: [0, 0, 0], max: [1, 1, 1] })];
  records[0].explodedViewMatrix = new THREE.Matrix4();
  clearExplodedViewRecords(records);
  assert.equal(records[0].explodedViewMatrix, null);
});

test("easeExplodedViewProgress clamps to animation bounds", () => {
  assert.equal(easeExplodedViewProgress(-1), 0);
  assert.equal(easeExplodedViewProgress(1.5), 1);
  assert.ok(easeExplodedViewProgress(0.5) > 0.5);
});

test("compile is a no-op without steps or without >=1 explodable record", () => {
  const empty = compileExplodedView(THREE, { enabled: true, steps: [] }, [record("o1.1", null)], null);
  assert.equal(empty.entries.length, 0);
  const noRecords = compileExplodedView(THREE, { enabled: true, steps: [translateStep("s1", ["o1.1"], [0, 0, 1], 1)] }, [], null);
  assert.equal(noRecords.entries.length, 0);
});
