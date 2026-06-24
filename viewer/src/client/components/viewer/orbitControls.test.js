import assert from "node:assert/strict";
import test from "node:test";

import {
  orbitControlsDeltaSeconds,
  PREVIEW_AUTO_ROTATE_SPEED,
  updateOrbitControls
} from "./orbitControls.js";

test("preview auto-rotate speed stays intentionally calm", () => {
  assert.equal(PREVIEW_AUTO_ROTATE_SPEED, 0.25);
});

test("orbitControlsDeltaSeconds converts animation timestamps from ms to seconds", () => {
  assert.equal(orbitControlsDeltaSeconds(1016, 1000), 0.016);
});

test("orbitControlsDeltaSeconds clamps long frame gaps", () => {
  assert.equal(orbitControlsDeltaSeconds(2000, 1000), 0.05);
});

test("updateOrbitControls passes seconds while auto-rotate is active", () => {
  const updateArgs = [];
  const controls = {
    autoRotate: true,
    update(...args) {
      updateArgs.push(args);
      return true;
    }
  };
  const state = { orbitControlsLastTimestamp: 1000 };

  assert.equal(updateOrbitControls(controls, 1016, state), true);
  assert.deepEqual(updateArgs, [[0.016]]);
  assert.equal(state.orbitControlsLastTimestamp, 1016);
});

test("updateOrbitControls resets timing when auto-rotate is inactive", () => {
  const updateArgs = [];
  const controls = {
    autoRotate: false,
    update(...args) {
      updateArgs.push(args);
      return false;
    }
  };
  const state = { orbitControlsLastTimestamp: 1016 };

  assert.equal(updateOrbitControls(controls, 1032, state), false);
  assert.deepEqual(updateArgs, [[]]);
  assert.equal(state.orbitControlsLastTimestamp, 0);
});
