import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

import { buildModel } from "./cadScene.js";

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

test("without the flag the package is not instanced (default path unchanged)", () => {
  const model = buildModel(THREE, packageMeshData(), {});
  assert.equal(countInstanced(model.modelGroup), 0);
  model.dispose?.();
});
