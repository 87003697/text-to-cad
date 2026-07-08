import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

import { buildModel, shouldInstancePackageScene } from "./cadScene.js";
import { resolveInstancePackagesFlag } from "./renderMeshScene.js";
import {
  buildInstancedPackageScene,
  buildInstancedOccurrenceRecords,
  syncInstancedOccurrenceTransforms,
  applyInstancedVisualState
} from "../lib/assembly/instancedScene.js";

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

function coloredBoxComponent(r, g, b) {
  const c = [r, g, b];
  return {
    vertices: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]),
    normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0]),
    colors: new Float32Array([...c, ...c, ...c, ...c]),
    indices: new Uint32Array([0, 1, 2, 0, 1, 3]),
    parts: [{ vertexOffset: 0, vertexCount: 4, triangleOffset: 0, triangleCount: 2 }]
  };
}

function firstInstancedMesh(group) {
  let mesh = null;
  group.traverse((obj) => {
    if (obj.isInstancedMesh && !mesh) {
      mesh = obj;
    }
  });
  return mesh;
}

test("instanced bucket honors a component's baked vertex colors when there is no override", () => {
  const occurrences = [];
  for (let i = 0; i < 200; i += 1) {
    occurrences.push({ id: `o1.${i + 1}`, component: "A", transform: translation(i, 0, 0) }); // no override color
  }
  const meshData = {
    parts: [],
    vertices: new Float32Array(0),
    bounds: { min: [0, 0, 0], max: [200, 1, 1] },
    packageInstancing: {
      descriptor: { components: { A: {} }, occurrences },
      componentMeshDataByCid: { A: coloredBoxComponent(1, 0, 0) }
    }
  };
  const model = buildModel(THREE, meshData, { instancePackages: true });
  const mesh = firstInstancedMesh(model.modelGroup);
  assert.ok(mesh, "expected an InstancedMesh");
  // Before the fix instancedBucketMaterial hardcoded useVertexColors:false, flattening
  // the component to white; now the baked COLOR_0 drives the diffuse.
  assert.equal(mesh.material.vertexColors, true);
  model.dispose?.();
});

test("toggling instancePackages via update() rebuilds between the instanced and per-mesh paths", () => {
  const model = buildModel(THREE, largePackageMeshData(200), {}); // instanced by size policy
  assert.equal(countInstanced(model.modelGroup), 1);
  model.update({ instancePackages: false });                       // force per-mesh
  assert.equal(countInstanced(model.modelGroup), 0);
  model.update({ instancePackages: true });                        // force instanced again
  assert.equal(countInstanced(model.modelGroup), 1);
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

// -- per-occurrence transform / effect parity (exploded view + params) --------

function instancedSceneWithRecords() {
  const { descriptor, componentMeshDataByCid } = packageMeshData().packageInstancing;
  const scene = buildInstancedPackageScene(THREE, descriptor, componentMeshDataByCid);
  const records = buildInstancedOccurrenceRecords(THREE, scene.instancedMeshes);
  return { scene, records };
}
function recordById(records, id) {
  return records.find((r) => r.partId === id);
}
function instancePosition(rec) {
  const m = new THREE.Matrix4();
  rec.instanceMesh.getMatrixAt(rec.instanceIndex, m);
  return new THREE.Vector3().setFromMatrixPosition(m);
}
function instanceColor(rec) {
  const c = new THREE.Color();
  rec.instanceMesh.getColorAt(rec.instanceIndex, c);
  return c;
}

test("buildInstancedOccurrenceRecords: one record per occurrence with base matrix + bounds", () => {
  const { records } = instancedSceneWithRecords();
  assert.equal(records.length, 3); // o1.1, o1.2 (cid A) + o1.3 (cid B)
  assert.deepEqual(records.map((r) => r.partId).sort(), ["o1.1", "o1.2", "o1.3"]);
  assert.ok(records.every((r) => r.instanced && r.instanceMesh && Number.isInteger(r.instanceIndex)));
  assert.ok(records.every((r) => r.baseMatrix instanceof THREE.Matrix4 && r.partBounds));
});

test("syncInstancedOccurrenceTransforms: exploded offset writes only that instance's matrix", () => {
  const { records } = instancedSceneWithRecords();
  recordById(records, "o1.1").explodedViewMatrix = new THREE.Matrix4().makeTranslation(0, 0, 10);
  syncInstancedOccurrenceTransforms(THREE, records);
  assert.ok(Math.abs(instancePosition(recordById(records, "o1.1")).z - 10) < 1e-6);
  // sibling in the same bucket (base x=5) is untouched
  const sib = instancePosition(recordById(records, "o1.2"));
  assert.ok(Math.abs(sib.x - 5) < 1e-6 && Math.abs(sib.z) < 1e-6);
});

test("syncInstancedOccurrenceTransforms: effect matrix composes over the base occurrence transform", () => {
  const { records } = instancedSceneWithRecords();
  recordById(records, "o1.2").effectMatrix = new THREE.Matrix4().makeTranslation(0, 3, 0); // base (5,0,0)
  syncInstancedOccurrenceTransforms(THREE, records);
  const pos = instancePosition(recordById(records, "o1.2"));
  assert.ok(Math.abs(pos.x - 5) < 1e-6 && Math.abs(pos.y - 3) < 1e-6);
});

test("syncInstancedOccurrenceTransforms: effectVisible=false collapses; effectStyle.color recolors", () => {
  const { records } = instancedSceneWithRecords();
  recordById(records, "o1.1").effectVisible = false;
  recordById(records, "o1.3").effectStyle = { color: [1, 0, 0] };
  syncInstancedOccurrenceTransforms(THREE, records);
  const hidden = recordById(records, "o1.1");
  const m = new THREE.Matrix4();
  hidden.instanceMesh.getMatrixAt(hidden.instanceIndex, m);
  assert.ok(m.elements.every((e) => Math.abs(e) < 1e-9), "hidden instance collapses to a zero matrix");
  const c = instanceColor(recordById(records, "o1.3"));
  assert.ok(c.r > 0.9 && c.g < 0.1 && c.b < 0.1, `expected red effect colour, got ${c.r},${c.g},${c.b}`);
});

test("selection recolor and exploded pose coexist on one instance (single buffer writer)", () => {
  const { records } = instancedSceneWithRecords();
  const rec = recordById(records, "o1.1");
  rec.explodedViewMatrix = new THREE.Matrix4().makeTranslation(0, 0, 12);
  syncInstancedOccurrenceTransforms(THREE, records);
  applyInstancedVisualState(THREE, rec.instanceMesh, {
    selected: new Set(["o1.1"]),
    selectedColor: { r: 0.3, g: 0.6, b: 1 }
  });
  assert.ok(Math.abs(instancePosition(rec).z - 12) < 1e-6, "exploded pose must survive a selection re-sync");
  const c = instanceColor(rec);
  assert.ok(Math.abs(c.r - 0.3) < 0.05 && Math.abs(c.b - 1) < 0.05, `expected selection blue, got ${c.r},${c.g},${c.b}`);
});
