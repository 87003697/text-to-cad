import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

import { buildInstancedPackageScene, applyInstancedVisualState, instancedOccurrenceBounds } from "./instancedScene.js";

function boxComponent() {
  // A trivial 3-vertex component; geometry content is irrelevant to the
  // instancing structure being tested.
  return {
    vertices: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
    colors: new Float32Array(0),
    indices: new Uint32Array([0, 1, 2]),
    parts: [{ vertexOffset: 0, vertexCount: 3, triangleOffset: 0, triangleCount: 1 }]
  };
}

function translation(x, y, z) {
  // row-major 4×4 pure translation
  return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1];
}

function mirrorX() {
  // reflection through X (negative determinant)
  return [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
}

test("instances one InstancedMesh per (component, mirror) bucket", () => {
  const descriptor = {
    components: { A: {}, B: {} },
    occurrences: [
      { id: "o1.1", component: "A", transform: translation(10, 0, 0) },
      { id: "o1.2", component: "A", transform: translation(20, 0, 0) },
      { id: "o1.3", component: "A", transform: mirrorX() },
      { id: "o1.4", component: "B", transform: translation(0, 5, 0) }
    ]
  };
  const scene = buildInstancedPackageScene(THREE, descriptor, { A: boxComponent(), B: boxComponent() });

  // A-normal (2) + A-mirror (1) + B-normal (1) = 3 draw calls; 2 unique geometries.
  assert.equal(scene.drawCalls, 3);
  assert.equal(scene.componentCount, 2);
  assert.equal(scene.occurrenceCount, 4);
  // GPU vertices == unique component vertices (3 + 3), not the 12 a composed path would bake.
  assert.equal(scene.gpuVertices, 6);

  const counts = scene.instancedMeshes.map((m) => m.count).sort();
  assert.deepEqual(counts, [1, 1, 2]);

  // The mirrored bucket is DoubleSide.
  const mirrorMesh = scene.instancedMeshes.find((m) => m.name.includes(":mirror"));
  assert.ok(mirrorMesh, "expected a mirror bucket");
  assert.equal(mirrorMesh.material.side, THREE.DoubleSide);
});

test("per-instance matrix equals the occurrence transform", () => {
  const descriptor = {
    components: { A: {} },
    occurrences: [
      { id: "o1.1", component: "A", transform: translation(10, 20, 30) },
      { id: "o1.2", component: "A", transform: translation(-4, 0, 7) }
    ]
  };
  const scene = buildInstancedPackageScene(THREE, descriptor, { A: boxComponent() });
  assert.equal(scene.instancedMeshes.length, 1);
  const mesh = scene.instancedMeshes[0];

  const expected0 = new THREE.Matrix4().set(...translation(10, 20, 30));
  const expected1 = new THREE.Matrix4().set(...translation(-4, 0, 7));
  const got = new THREE.Matrix4();
  mesh.getMatrixAt(0, got);
  assert.deepEqual([...got.elements], [...expected0.elements]);
  mesh.getMatrixAt(1, got);
  assert.deepEqual([...got.elements], [...expected1.elements]);

  // occurrence id mapping preserved in placement order.
  assert.deepEqual(mesh.userData.cadInstanceOccurrenceIds, ["o1.1", "o1.2"]);
});

test("occurrence override color becomes a per-instance color", () => {
  const descriptor = {
    components: { A: {} },
    occurrences: [
      { id: "o1.1", component: "A", transform: translation(0, 0, 0), color: [1, 0, 0, 1] },
      { id: "o1.2", component: "A", transform: translation(1, 0, 0), color: [0, 1, 0, 1] }
    ]
  };
  const scene = buildInstancedPackageScene(THREE, descriptor, { A: boxComponent() });
  const mesh = scene.instancedMeshes[0];
  assert.ok(mesh.instanceColor, "expected instanceColor to be allocated");
  const c = new THREE.Color();
  mesh.getColorAt(0, c);
  assert.ok(Math.abs(c.r - 1) < 1e-6 && c.g < 1e-6, "instance 0 is red");
  mesh.getColorAt(1, c);
  assert.ok(Math.abs(c.g - 1) < 1e-6 && c.r < 1e-6, "instance 1 is green");
});

test("build stores base color + base matrix and always allocates instanceColor", () => {
  const descriptor = {
    components: { A: {} },
    occurrences: [
      { id: "o1.1", component: "A", transform: translation(3, 0, 0) },
      { id: "o1.2", component: "A", transform: translation(6, 0, 0) }
    ]
  };
  const scene = buildInstancedPackageScene(THREE, descriptor, { A: boxComponent() });
  const mesh = scene.instancedMeshes[0];
  // instanceColor allocated even with no override colors, defaulting to white.
  assert.ok(mesh.instanceColor, "instanceColor allocated");
  const c = new THREE.Color();
  mesh.getColorAt(0, c);
  assert.ok(Math.abs(c.r - 1) < 1e-6 && Math.abs(c.g - 1) < 1e-6 && Math.abs(c.b - 1) < 1e-6, "base white");
  assert.equal(mesh.userData.cadInstanceBaseColors.length, 6);
  assert.equal(mesh.userData.cadInstanceBaseMatrices.length, 32);
});

function twoInstanceScene() {
  const descriptor = {
    components: { A: {} },
    occurrences: [
      { id: "o1.1", component: "A", transform: translation(0, 0, 0), color: [0.5, 0.5, 0.5, 1] },
      { id: "o1.2", component: "A", transform: translation(5, 0, 0), color: [0.5, 0.5, 0.5, 1] }
    ]
  };
  return buildInstancedPackageScene(THREE, descriptor, { A: boxComponent() }).instancedMeshes[0];
}

test("selection recolors only the selected instance", () => {
  const mesh = twoInstanceScene();
  const selectedColor = new THREE.Color(0.31, 0.615, 1);
  const changed = applyInstancedVisualState(THREE, mesh, {
    selected: new Set(["o1.1"]),
    selectedColor
  });
  assert.equal(changed, true);
  const c = new THREE.Color();
  mesh.getColorAt(0, c);
  assert.ok(Math.abs(c.r - 0.31) < 1e-3 && Math.abs(c.g - 0.615) < 1e-3 && Math.abs(c.b - 1) < 1e-3, "o1.1 is selection color");
  mesh.getColorAt(1, c);
  assert.ok(Math.abs(c.r - 0.5) < 1e-3 && Math.abs(c.g - 0.5) < 1e-3, "o1.2 keeps its base color");
});

test("applyInstancedVisualState only rewrites instances whose state changed", () => {
  const mesh = twoInstanceScene();
  const selectedColor = new THREE.Color(0.31, 0.615, 1);
  // First apply initializes both instances (unknown -> known).
  assert.equal(applyInstancedVisualState(THREE, mesh, { selected: new Set(["o1.1"]), selectedColor }), true);

  // A second identical apply must touch nothing and report no change.
  let colorWrites = 0;
  const origSetColor = mesh.setColorAt.bind(mesh);
  mesh.setColorAt = (i, c) => { colorWrites += 1; return origSetColor(i, c); };
  const changed = applyInstancedVisualState(THREE, mesh, { selected: new Set(["o1.1"]), selectedColor });
  assert.equal(changed, false, "identical state is a no-op");
  assert.equal(colorWrites, 0, "no instance rewritten when nothing changed");

  // Moving the selection rewrites exactly the two affected instances, not the bucket.
  colorWrites = 0;
  applyInstancedVisualState(THREE, mesh, { selected: new Set(["o1.2"]), selectedColor });
  assert.equal(colorWrites, 2, "only the deselected + newly-selected instances rewritten");
  mesh.setColorAt = origSetColor;
});

test("hidden instance collapses to a zero matrix and restores when unhidden", () => {
  const mesh = twoInstanceScene();
  applyInstancedVisualState(THREE, mesh, { hidden: new Set(["o1.2"]) });
  const m = new THREE.Matrix4();
  mesh.getMatrixAt(1, m);
  assert.ok(m.elements.every((v) => v === 0), "hidden instance matrix is zeroed");
  // Unhide: base pose (translation +5 in X) is restored from the stored base matrix.
  applyInstancedVisualState(THREE, mesh, { hidden: new Set() });
  mesh.getMatrixAt(1, m);
  const expected = new THREE.Matrix4().set(...translation(5, 0, 0));
  assert.deepEqual([...m.elements], [...expected.elements]);
});

test("focus dims the non-focused instances", () => {
  const mesh = twoInstanceScene();
  applyInstancedVisualState(THREE, mesh, {
    focusIds: new Set(["o1.1"]),
    hasFocus: true
  });
  const c = new THREE.Color();
  mesh.getColorAt(0, c);
  assert.ok(Math.abs(c.r - 0.5) < 1e-3, "focused instance keeps base color");
  mesh.getColorAt(1, c);
  assert.ok(c.r < 0.5 && c.r > 0, "non-focused instance is dimmed toward black");
});

test("visual state is a no-op on a mesh without instanced base metadata", () => {
  const changed = applyInstancedVisualState(THREE, { userData: {} }, { selected: new Set(["x"]) });
  assert.equal(changed, false);
});

test("instancedOccurrenceBounds transforms the component box per matching instance", () => {
  const descriptor = {
    components: { A: {} },
    occurrences: [
      { id: "o1.1", component: "A", transform: translation(10, 0, 0) },
      { id: "o1.2", component: "A", transform: translation(20, 0, 0) }
    ]
  };
  const mesh = buildInstancedPackageScene(THREE, descriptor, { A: boxComponent() }).instancedMeshes[0];
  // boxComponent's local AABB is min[0,0,0] max[1,1,0].
  const one = instancedOccurrenceBounds(mesh, (id) => id === "o1.1");
  assert.deepEqual(one.min, [10, 0, 0]);
  assert.deepEqual(one.max, [11, 1, 0]);
  // no predicate => union over every instance in the bucket.
  const all = instancedOccurrenceBounds(mesh, null);
  assert.deepEqual(all.min, [10, 0, 0]);
  assert.deepEqual(all.max, [21, 1, 0]);
  // nothing matches => null (so callers fall back cleanly).
  assert.equal(instancedOccurrenceBounds(mesh, () => false), null);
  // missing metadata => null.
  assert.equal(instancedOccurrenceBounds({ userData: {} }, null), null);
});
