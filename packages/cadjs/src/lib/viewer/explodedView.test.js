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

test("generator separates side-by-side coplanar groups laterally, not stacked", () => {
  // Three parts side-by-side in X at the same height (disjoint footprints): the
  // old solver stacked them into a vertical column; the new one spreads them in
  // the horizontal plane.
  const records = [
    record("o1.1", { min: [-5, -1, 0], max: [-3, 1, 2] }),
    record("o1.2", { min: [-1, -1, 0], max: [1, 1, 2] }),
    record("o1.3", { min: [3, -1, 0], max: [5, 1, 2] })
  ];
  const bounds = { min: [-5, -1, 0], max: [5, 1, 2] };
  const { offsets } = explode(records, bounds, { mode: "z" });
  for (const key of ["o1.1", "o1.2", "o1.3"]) {
    const [x, y, z] = offsets.get(key);
    assert.ok(Math.abs(z) < 1e-6, `${key} should not move vertically`);
    assert.ok(Math.hypot(x, y) > 0, `${key} should separate laterally`);
  }
});

test("generator telescopes concentric coplanar parts along the axis", () => {
  // Nested rings at the same height (a shaft/housing case): they must separate
  // along the explode axis, not scatter sideways into each other.
  const records = [
    record("o1.1", { min: [-6, -6, 0], max: [6, 6, 2] }),
    record("o1.2", { min: [-4, -4, 0], max: [4, 4, 2] }),
    record("o1.3", { min: [-2, -2, 0], max: [2, 2, 2] })
  ];
  const bounds = { min: [-6, -6, 0], max: [6, 6, 2] };
  const { offsets } = explode(records, bounds, { mode: "z" });
  const z = ["o1.1", "o1.2", "o1.3"].map((k) => offsets.get(k)[2]);
  // Distinct axial stations, no lateral scatter.
  assert.ok(z[1] > z[0] && z[2] > z[1], "nested parts telescope along the axis");
  for (const key of ["o1.1", "o1.2", "o1.3"]) {
    const [x, y] = offsets.get(key);
    assert.ok(Math.hypot(x, y) < 1e-6, `${key} should stay on the axis`);
  }
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

test("generator radial mode anchors the core and blooms the ring outward", () => {
  // A central hub with a ring of parts around it: the hub (on the axis) stays,
  // and each ring part blooms straight outward, preserving height.
  const ring = [];
  for (let i = 0; i < 4; i += 1) {
    const a = (i / 4) * Math.PI * 2;
    const cx = 6 * Math.cos(a);
    const cy = 6 * Math.sin(a);
    ring.push(record(`o1.${i + 2}`, { min: [cx - 1, cy - 1, 0], max: [cx + 1, cy + 1, 2] }));
  }
  const records = [record("o1.1", { min: [-2, -2, 0], max: [2, 2, 2] }), ...ring];
  const bounds = { min: [-8, -8, 0], max: [8, 8, 2] };
  const { doc, offsets } = explode(records, bounds, { mode: "radial" });

  // Hub stays put; every ring part moves outward with height preserved.
  assert.deepEqual(offsets.get("o1.1"), [0, 0, 0]);
  for (let i = 0; i < 4; i += 1) {
    const [x, y, z] = offsets.get(`o1.${i + 2}`);
    assert.ok(Math.hypot(x, y) > 0, "ring part blooms outward");
    assert.ok(Math.abs(z) < 1e-6, "radial preserves height");
  }
  // The four ring parts move in four distinct directions.
  const dirs = new Set([0, 1, 2, 3].map((i) => {
    const [x, y] = offsets.get(`o1.${i + 2}`);
    return `${Math.round(x)},${Math.round(y)}`;
  }));
  assert.ok(doc.steps.length >= 4);
  assert.ok(dirs.size >= 3, "ring parts bloom in distinct directions");
});

test("generator returns no steps for degenerate input", () => {
  const single = generateExplodedViewDocument(THREE, [record("o1.1", { min: [0, 0, 0], max: [1, 1, 1] })], null, {});
  assert.equal(single.steps.length, 0);
  const none = generateExplodedViewDocument(THREE, [], null, {});
  assert.equal(none.steps.length, 0);
});
