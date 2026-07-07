import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

import { buildModel, shouldInstancePackageScene } from "./cadScene.js";
import { resolveInstancePackagesFlag } from "./renderMeshScene.js";

function boxComponent() {
  return {
    vertices: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]),
    normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0]),
    colors: new Float32Array(0),
    indices: new Uint32Array([0, 1, 2, 0, 1, 3]),
    parts: [{ vertexOffset: 0, vertexCount: 4, triangleOffset: 0, triangleCount: 2 }]
  };
}

function translation(x, y, z) {
  return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1];
}

function packageMeshData() {
  const descriptor = {
    components: { A: {}, B: {} },
    occurrences: [
      { id: "o1.1", component: "A", transform: translation(0, 0, 0), color: [1, 0, 0, 1] },
      { id: "o1.2", component: "A", transform: translation(5, 0, 0), color: [0, 1, 0, 1] },
      { id: "o1.3", component: "B", transform: translation(0, 5, 0), color: [0, 0, 1, 1] }
    ]
  };
  return {
    parts: [],
    vertices: new Float32Array(0),
    bounds: { min: [0, 0, 0], max: [6, 6, 1] },
    packageInstancing: { descriptor, componentMeshDataByCid: { A: boxComponent(), B: boxComponent() } }
  };
}

function countInstanced(group) {
  let n = 0;
  group.traverse((obj) => {
    if (obj.isInstancedMesh) {
      n += 1;
    }
  });
  return n;
}

test("instancePackages flag renders the package as InstancedMeshes", () => {
  const model = buildModel(THREE, packageMeshData(), { instancePackages: true });
  // 2 unique components, no mirroring => 2 InstancedMeshes.
  assert.equal(countInstanced(model.modelGroup), 2);
  const records = model.displayRecords;
  assert.ok(records.length >= 2);
  assert.ok(records.every((r) => r.instanced === true));
  // occurrence ids are reachable for the later picking layer.
  const allIds = records.flatMap((r) => r.occurrenceIds);
  assert.deepEqual([...allIds].sort(), ["o1.1", "o1.2", "o1.3"]);
  model.dispose?.();
});

test("without the flag a small package is not instanced (default path unchanged)", () => {
  const model = buildModel(THREE, packageMeshData(), {});
  assert.equal(countInstanced(model.modelGroup), 0);
  model.dispose?.();
});

function largePackageMeshData(occurrenceCount) {
  const occurrences = [];
  for (let i = 0; i < occurrenceCount; i += 1) {
    occurrences.push({ id: `o1.${i + 1}`, component: "A", transform: translation(i, 0, 0) });
  }
  return {
    parts: [],
    vertices: new Float32Array(0),
    bounds: { min: [0, 0, 0], max: [occurrenceCount, 1, 1] },
    packageInstancing: { descriptor: { components: { A: {} }, occurrences }, componentMeshDataByCid: { A: boxComponent() } }
  };
}

test("a large package instances by default (size policy, no flag)", () => {
  const model = buildModel(THREE, largePackageMeshData(200), {});
  assert.equal(countInstanced(model.modelGroup), 1);
  model.dispose?.();
});

test("instancePackages:false forces a large package back to the per-mesh path", () => {
  const model = buildModel(THREE, largePackageMeshData(200), { instancePackages: false });
  assert.equal(countInstanced(model.modelGroup), 0);
  model.dispose?.();
});

test("shouldInstancePackageScene: tri-state flag beats the size policy", () => {
  const small = packageMeshData();
  const large = largePackageMeshData(200);
  // no packageInstancing -> never
  assert.equal(shouldInstancePackageScene({}, { parts: [] }), false);
  // size policy
  assert.equal(shouldInstancePackageScene({}, small), false);
  assert.equal(shouldInstancePackageScene({}, large), true);
  // explicit overrides
  assert.equal(shouldInstancePackageScene({ instancePackages: true }, small), true);
  assert.equal(shouldInstancePackageScene({ instancePackages: false }, large), false);
});

test("resolveInstancePackagesFlag preserves the render-job tri-state", () => {
  assert.equal(resolveInstancePackagesFlag({}), undefined);
  assert.equal(resolveInstancePackagesFlag({ instancePackages: true }), true);
  assert.equal(resolveInstancePackagesFlag({ display: { instancePackages: false } }), false);
  // display block wins over the top-level flag when both are booleans
  assert.equal(resolveInstancePackagesFlag({ display: { instancePackages: true }, instancePackages: false }), true);
});
