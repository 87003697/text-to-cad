import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { explodedViewGroupKey, generateExplodedViewDocument } from "./explodedView.js";
import { applyExplodedViewProgress, compileExplodedView } from "./explodedViewSteps.js";

function record(partId, bounds) {
  return {
    partId,
    mesh: new THREE.Object3D(),
    partBounds: bounds
  };
}

// Resolve a generated document back into per-record z/x/y displacement at full
// explode, so tests can assert on where parts actually end up.
function explode(records, bounds, hints) {
  const doc = generateExplodedViewDocument(THREE, records, bounds, hints);
  const compiled = compileExplodedView(THREE, doc, records, bounds);
  applyExplodedViewProgress(THREE, compiled, 1);
  const offsets = new Map();
  for (const r of records) {
    const m = r.explodedViewMatrix;
    offsets.set(r.partId, m ? [m.elements[12], m.elements[13], m.elements[14]] : [0, 0, 0]);
  }
  return { doc, offsets };
}

test("explodedViewGroupKey groups by occurrence prefix at depth", () => {
  assert.equal(explodedViewGroupKey("o1.2.3", { depth: 1, commonPrefix: ["o1"] }), "o1.2");
  assert.equal(explodedViewGroupKey("o1.2.3", { depth: 2, commonPrefix: ["o1"] }), "o1.2.3");
});

test("generator groups first-level components and explodes along Z", () => {
  const records = [
    record("o1.1.1", { min: [-1, -1, 0], max: [1, 1, 2] }),
    record("o1.1.2", { min: [-1, -1, 2], max: [1, 1, 4] }),
    record("o1.2", { min: [-1, -1, 6], max: [1, 1, 8] })
  ];
  const bounds = { min: [-1, -1, 0], max: [1, 1, 8] };
  const { doc, offsets } = explode(records, bounds, { mode: "z" });

  // o1.1.1 and o1.1.2 collapse into group o1.1 (depth 1), which is the grounded
  // base layer -> stays put; o1.2 moves up in Z.
  assert.ok(doc.steps.length >= 1);
  assert.deepEqual(offsets.get("o1.1.1"), offsets.get("o1.1.2")); // rigid group
  assert.equal(offsets.get("o1.1.1")[2], 0);
  assert.ok(offsets.get("o1.2")[2] > 0);
});

test("generator separates coplanar groups laterally instead of stacking", () => {
  // Three side-by-side parts at the same height: the old solver stacked them
  // into a vertical column; the new one spreads them in the horizontal plane.
  const records = [
    record("o1.1", { min: [-1, -1, 0], max: [1, 1, 2] }),
    record("o1.2", { min: [-1, -1, 0], max: [1, 1, 2] }),
    record("o1.3", { min: [-1, -1, 0], max: [1, 1, 2] })
  ];
  const bounds = { min: [-1, -1, 0], max: [1, 1, 2] };
  const { offsets } = explode(records, bounds, { mode: "z" });

  for (const key of ["o1.1", "o1.2", "o1.3"]) {
    const [x, y, z] = offsets.get(key);
    assert.ok(Math.abs(z) < 1e-6, `${key} should not move vertically`);
    assert.ok(Math.hypot(x, y) > 0, `${key} should separate laterally`);
  }
  // Directions are distinct (not all piled the same way): the three lateral
  // offsets are not identical vectors.
  const vectors = ["o1.1", "o1.2", "o1.3"].map((k) => offsets.get(k).join(","));
  assert.ok(new Set(vectors).size >= 2, "coplanar groups fan to distinct directions");
});

test("generator depth can break subassemblies into deeper components", () => {
  const records = [
    record("o1.1.1", { min: [0, 0, 0], max: [1, 1, 2] }),
    record("o1.1.2", { min: [0, 0, 3], max: [1, 1, 5] }),
    record("o1.2", { min: [0, 0, 6], max: [1, 1, 8] })
  ];
  const bounds = { min: [0, 0, 0], max: [1, 1, 8] };
  const { doc, offsets } = explode(records, bounds, { mode: "z", depth: 2 });
  const targets = doc.steps.flatMap((step) => step.targets);
  // At depth 2 the two children of o1.1 are distinct targets.
  assert.ok(targets.includes("o1.1.2"));
  assert.ok(offsets.get("o1.1.2")[2] > offsets.get("o1.1.1")[2]);
});

test("generator auto axis picks the direction of greatest spread", () => {
  // Parts spread along X, thin in Y/Z -> auto axis should be X.
  const records = [
    record("o1.1", { min: [0, 0, 0], max: [1, 1, 1] }),
    record("o1.2", { min: [5, 0, 0], max: [6, 1, 1] }),
    record("o1.3", { min: [10, 0, 0], max: [11, 1, 1] })
  ];
  const bounds = { min: [0, 0, 0], max: [11, 1, 1] };
  const { offsets } = explode(records, bounds, { mode: "auto" });
  // Non-base groups travel primarily along X.
  const moved = ["o1.2", "o1.3"].map((k) => offsets.get(k));
  for (const [x, y, z] of moved) {
    assert.ok(Math.abs(x) >= Math.abs(y) && Math.abs(x) >= Math.abs(z));
  }
});

test("generator radial mode blooms groups outward and preserves height", () => {
  const records = [
    record("o1.1", { min: [-3, -1, 0], max: [-1, 1, 2] }),
    record("o1.2", { min: [1, -1, 0], max: [3, 1, 2] })
  ];
  const bounds = { min: [-3, -1, 0], max: [3, 1, 2] };
  const { doc, offsets } = explode(records, bounds, { mode: "radial", keepBaseGrounded: false });
  assert.ok(doc.steps.length >= 2);
  assert.ok(offsets.get("o1.1")[0] < 0);
  assert.ok(offsets.get("o1.2")[0] > 0);
  for (const key of ["o1.1", "o1.2"]) {
    assert.ok(Math.abs(offsets.get(key)[2]) < 1e-6, "radial keeps height");
  }
});

test("generator radial mode fans coaxial groups to distinct directions", () => {
  // Two groups both centered on the vertical axis (stacked in Z): the radial
  // bloom must fan them to different directions instead of the same fallback.
  const records = [
    record("o1.1", { min: [-1, -1, 0], max: [1, 1, 1] }),
    record("o1.2", { min: [-1, -1, 4], max: [1, 1, 5] })
  ];
  const bounds = { min: [-1, -1, 0], max: [1, 1, 5] };
  const doc = generateExplodedViewDocument(THREE, records, bounds, { mode: "radial", keepBaseGrounded: false });
  assert.equal(doc.steps.length, 2);
  const [a, b] = doc.steps.map((step) => step.axis);
  assert.ok(
    Math.abs(a[0] - b[0]) > 1e-6 || Math.abs(a[1] - b[1]) > 1e-6,
    "coaxial groups fan to distinct directions"
  );
  // Purely horizontal bloom preserves height.
  assert.ok(doc.steps.every((step) => Math.abs(step.axis[2]) < 1e-6));
});

test("generator returns no steps for degenerate input", () => {
  const single = generateExplodedViewDocument(THREE, [record("o1.1", { min: [0, 0, 0], max: [1, 1, 1] })], null, {});
  assert.equal(single.steps.length, 0);
  const none = generateExplodedViewDocument(THREE, [], null, {});
  assert.equal(none.steps.length, 0);
});
