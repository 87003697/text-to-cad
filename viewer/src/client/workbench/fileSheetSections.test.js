import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultOpenFileSheetSectionIds,
  renderedFileSheetSectionIds
} from "./fileSheetSections.js";

test("file sheet section defaults match current sheet behavior", () => {
  assert.deepEqual(defaultOpenFileSheetSectionIds("dxf"), ["plate", "bends"]);
  assert.deepEqual(defaultOpenFileSheetSectionIds("gcode"), ["toolpath"]);
  assert.deepEqual(defaultOpenFileSheetSectionIds("step"), ["tree"]);
  assert.deepEqual(defaultOpenFileSheetSectionIds("step", { hasFileStatus: true }), ["status", "tree"]);
  assert.deepEqual(defaultOpenFileSheetSectionIds("step", { hasStepModulePanel: true }), ["tree", "parameters"]);
  assert.deepEqual(defaultOpenFileSheetSectionIds("mesh", { hasFileStatus: true }), ["status"]);
  assert.deepEqual(defaultOpenFileSheetSectionIds("srdf", { motionEnabled: true }), ["motion", "joints"]);
  assert.deepEqual(defaultOpenFileSheetSectionIds("sdf"), ["sdf", "joints"]);
});

test("rendered file sheet sections include closed-by-default sections", () => {
  assert.deepEqual(renderedFileSheetSectionIds("gcode", { hasFileStatus: true }), [
    "status",
    "toolpath",
    "features",
    "stats",
    "bounds",
    "display",
    "appearance",
    "metadata"
  ]);
  assert.deepEqual(renderedFileSheetSectionIds("step", { hasFileStatus: true, hasStepModulePanel: true }), [
    "status",
    "tree",
    "parameters",
    "display",
    "appearance",
    "metadata"
  ]);
  assert.deepEqual(renderedFileSheetSectionIds("mesh"), ["display", "appearance", "metadata"]);
});
