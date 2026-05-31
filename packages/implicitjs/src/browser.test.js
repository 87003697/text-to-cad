import assert from "node:assert/strict";
import test from "node:test";

import {
  createImplicitMaterial,
  loadImplicitSource,
  normalizeImplicitDefinition,
  normalizeParameterValue
} from "./browser.js";

test("browser entry exposes editable-source, parameter, model, and render APIs", () => {
  assert.equal(typeof loadImplicitSource, "function");
  assert.equal(typeof normalizeImplicitDefinition, "function");
  assert.equal(typeof normalizeParameterValue, "function");
  assert.equal(typeof createImplicitMaterial, "function");
});
