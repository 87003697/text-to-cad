import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import {
  autoZoomFrameForBounds,
  displayRecordsBounds,
  fitDistanceForRadius,
  mergeBoundsList,
  shiftedBounds
} from "./autoZoom.js";

test("auto zoom merges and shifts display record bounds", () => {
  const left = {
    partId: "left",
    partBounds: { min: [0, 0, 0], max: [1, 1, 1] }
  };
  const right = {
    partId: "right",
    partBounds: { min: [2, 0, 0], max: [3, 1, 1] }
  };
  const translationByRecord = new Map([
    [right, new THREE.Vector3(10, 0, 0)]
  ]);

  assert.deepEqual(
    shiftedBounds(left.partBounds, [1, 2, 3]),
    { min: [1, 2, 3], max: [2, 3, 4] }
  );
  assert.deepEqual(
    displayRecordsBounds([left, right], { translationByRecord }),
    { min: [0, 0, 0], max: [13, 1, 1] }
  );
  assert.deepEqual(
    displayRecordsBounds([left, right], { partIds: new Set(["right"]), translationByRecord }),
    { min: [12, 0, 0], max: [13, 1, 1] }
  );
  assert.deepEqual(
    mergeBoundsList([left.partBounds, null, right.partBounds]),
    { min: [0, 0, 0], max: [3, 1, 1] }
  );
});

test("auto zoom frame preserves orientation while fitting padded radius", () => {
  const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 1000);
  camera.position.set(0, -10, 0);
  camera.up.set(0, 0, 1);
  const controls = {
    target: new THREE.Vector3(0, 0, 0)
  };
  const bounds = { min: [-1, -1, -1], max: [1, 1, 1] };
  const distance = fitDistanceForRadius(camera, Math.sqrt(3), {
    aspect: 1,
    padding: 1.2
  });
  const frame = autoZoomFrameForBounds(THREE, {
    camera,
    controls,
    bounds,
    frameAspect: 1,
    padding: 1.2
  });

  assert.ok(frame);
  assert.ok(Math.abs(frame.distance - distance) < 1e-8);
  assert.deepEqual(frame.target.toArray(), [0, 0, 0]);
  assert.ok(Math.abs(frame.position.x) < 1e-8);
  assert.ok(frame.position.y < 0);
  assert.ok(Math.abs(frame.position.length() - distance) < 1e-8);
  assert.deepEqual(frame.up.toArray(), [0, 0, 1]);
});
