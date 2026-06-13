import assert from "node:assert/strict";
import test from "node:test";
import { syncOrbitNavigationMode } from "./orbitNavigationMode.js";

const MOUSE = Object.freeze({
  ROTATE: 0,
  DOLLY: 1,
  PAN: 2
});

const TOUCH = Object.freeze({
  ROTATE: 0,
  PAN: 1,
  DOLLY_PAN: 2
});

test("syncOrbitNavigationMode uses rotate-first controls for perspective", () => {
  const controls = {};
  assert.equal(syncOrbitNavigationMode(controls, { isOrthographicCamera: false }, {
    projection: "perspective",
    mouse: MOUSE,
    touch: TOUCH
  }), true);

  assert.deepEqual(controls.mouseButtons, {
    LEFT: MOUSE.ROTATE,
    MIDDLE: MOUSE.DOLLY,
    RIGHT: MOUSE.PAN
  });
  assert.deepEqual(controls.touches, {
    ONE: TOUCH.ROTATE,
    TWO: TOUCH.DOLLY_PAN
  });
});

test("syncOrbitNavigationMode uses screen-space pan first for orthographic", () => {
  const controls = {
    mouseButtons: {
      LEFT: MOUSE.ROTATE,
      MIDDLE: MOUSE.DOLLY,
      RIGHT: MOUSE.PAN
    },
    touches: {
      ONE: TOUCH.ROTATE,
      TWO: TOUCH.DOLLY_PAN
    }
  };

  syncOrbitNavigationMode(controls, { isOrthographicCamera: false }, {
    projection: "orthographic",
    mouse: MOUSE,
    touch: TOUCH
  });

  assert.deepEqual(controls.mouseButtons, {
    LEFT: MOUSE.PAN,
    MIDDLE: MOUSE.DOLLY,
    RIGHT: MOUSE.ROTATE
  });
  assert.deepEqual(controls.touches, {
    ONE: TOUCH.PAN,
    TWO: TOUCH.DOLLY_PAN
  });
});

test("syncOrbitNavigationMode can infer orthographic mode from the camera", () => {
  const controls = {};
  syncOrbitNavigationMode(controls, { isOrthographicCamera: true }, {
    mouse: MOUSE,
    touch: TOUCH
  });

  assert.equal(controls.mouseButtons.LEFT, MOUSE.PAN);
  assert.equal(controls.touches.ONE, TOUCH.PAN);
});
