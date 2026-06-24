import assert from "node:assert/strict";
import test from "node:test";

import {
  implicitGraphicsRenderResolutionScale,
  normalizeImplicitGraphicsSettings
} from "./graphicsSettings.js";

test("implicit graphics render resolution uses idle or interaction scale", () => {
  const settings = normalizeImplicitGraphicsSettings({
    resolutionScale: 2.5,
    interactionResolutionScale: 0.75
  });

  assert.equal(implicitGraphicsRenderResolutionScale(settings), 2.5);
  assert.equal(implicitGraphicsRenderResolutionScale(settings, { interaction: true }), 0.75);
});
