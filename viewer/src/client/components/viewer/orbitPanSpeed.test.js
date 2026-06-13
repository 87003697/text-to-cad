import assert from "node:assert/strict";
import test from "node:test";
import {
  ORBIT_BASE_PAN_SPEED,
  orbitPanSpeedForCamera,
  orbitPanZoomScale,
  syncOrbitPanSpeed
} from "./orbitPanSpeed.js";

test("orbit pan speed scales with camera zoom", () => {
  assert.equal(orbitPanZoomScale({ zoom: 1 }), 1);
  assert.equal(orbitPanSpeedForCamera({ zoom: 4 }), ORBIT_BASE_PAN_SPEED * 2);
  assert.equal(orbitPanSpeedForCamera({ zoom: 0.25 }), ORBIT_BASE_PAN_SPEED * 0.5);
});

test("orbit pan speed ignores invalid zoom and clamps extremes", () => {
  assert.equal(orbitPanSpeedForCamera({ zoom: 0 }), ORBIT_BASE_PAN_SPEED);
  assert.equal(orbitPanSpeedForCamera({ zoom: NaN }), ORBIT_BASE_PAN_SPEED);
  assert.equal(orbitPanZoomScale({ zoom: 0.001 }), 0.5);
  assert.equal(orbitPanZoomScale({ zoom: 1000 }), 4);
});

test("syncOrbitPanSpeed updates controls in place", () => {
  const controls = { object: { zoom: 9 }, panSpeed: 0 };
  assert.equal(syncOrbitPanSpeed(controls), ORBIT_BASE_PAN_SPEED * 3);
  assert.equal(controls.panSpeed, ORBIT_BASE_PAN_SPEED * 3);
});
