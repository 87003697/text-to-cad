import assert from "node:assert/strict";
import test from "node:test";

import {
  activateFileSheetTab,
  clampSplitRatio,
  defaultFileSheetTabArrangement,
  kindSupportsSplit,
  moveFileSheetTab,
  normalizeFileSheetTabArrangement,
  readFileSheetTabLayoutStore,
  resolveFileSheetTabPanes,
  setFileSheetTabRatio,
  setFileSheetTabSplit,
  writeFileSheetTabLayoutStore,
  FILE_SHEET_TAB_PANES,
  MAX_FILE_SHEET_SPLIT_RATIO,
  MIN_FILE_SHEET_SPLIT_RATIO
} from "./fileSheetTabLayout.js";

const STEP_SECTIONS = ["tree", "parameters", "display", "appearance", "metadata"];

test("only step supports the split", () => {
  assert.equal(kindSupportsSplit("step"), true);
  assert.equal(kindSupportsSplit("dxf"), false);
  assert.equal(kindSupportsSplit("gcode"), false);
});

test("default step arrangement puts the tree on top and the rest on the bottom", () => {
  const arrangement = defaultFileSheetTabArrangement("step", STEP_SECTIONS);
  assert.equal(arrangement.split, true);
  assert.deepEqual(arrangement.top, ["tree"]);
  assert.deepEqual(arrangement.bottom, ["parameters", "display", "appearance", "metadata"]);
  assert.equal(arrangement.ratio, 0.5);
});

test("default non-step arrangement is a single strip", () => {
  const arrangement = defaultFileSheetTabArrangement("dxf", ["dxf", "display", "appearance", "metadata"]);
  assert.equal(arrangement.split, false);
  assert.deepEqual(arrangement.top, ["dxf", "display", "appearance", "metadata"]);
  assert.deepEqual(arrangement.bottom, []);
});

test("step with only a tree collapses to a single strip", () => {
  const arrangement = defaultFileSheetTabArrangement("step", ["tree"]);
  assert.equal(arrangement.split, false);
  assert.deepEqual(arrangement.top, ["tree"]);
});

test("normalize drops missing tabs and appends new ones to their default pane", () => {
  const stored = { split: true, top: ["tree"], bottom: ["display", "appearance"], ratio: 0.6 };
  const normalized = normalizeFileSheetTabArrangement(stored, "step", STEP_SECTIONS);
  assert.deepEqual(normalized.top, ["tree"]);
  // parameters + metadata are newly rendered, appended to the bottom default pane.
  assert.deepEqual(normalized.bottom, ["display", "appearance", "parameters", "metadata"]);
  assert.equal(normalized.ratio, 0.6);
});

test("normalize de-dupes a tab present in both panes (top wins)", () => {
  const stored = { split: true, top: ["tree", "display"], bottom: ["display", "metadata"] };
  const normalized = normalizeFileSheetTabArrangement(stored, "step", ["tree", "display", "metadata"]);
  assert.deepEqual(normalized.top, ["tree", "display"]);
  assert.deepEqual(normalized.bottom, ["metadata"]);
});

test("normalize re-derives the default split when a requested split has an empty pane", () => {
  const stored = { split: true, top: ["tree", "parameters", "display", "appearance", "metadata"], bottom: [] };
  const normalized = normalizeFileSheetTabArrangement(stored, "step", STEP_SECTIONS);
  assert.equal(normalized.split, true);
  assert.deepEqual(normalized.top, ["tree"]);
  assert.deepEqual(normalized.bottom, ["parameters", "display", "appearance", "metadata"]);
});

test("normalize forces a single strip for non-split kinds", () => {
  const stored = { split: true, top: ["dxf"], bottom: ["display"] };
  const normalized = normalizeFileSheetTabArrangement(stored, "dxf", ["dxf", "display", "metadata"]);
  assert.equal(normalized.split, false);
  assert.deepEqual(normalized.top, ["dxf", "display", "metadata"]);
  assert.deepEqual(normalized.bottom, []);
});

test("moving a tab across panes updates assignment", () => {
  const arrangement = defaultFileSheetTabArrangement("step", STEP_SECTIONS);
  const next = moveFileSheetTab(arrangement, "step", "display", FILE_SHEET_TAB_PANES.TOP, 1);
  assert.deepEqual(next.top, ["tree", "display"]);
  assert.deepEqual(next.bottom, ["parameters", "appearance", "metadata"]);
  assert.equal(next.split, true);
});

test("moving the last tab out of a pane collapses the split", () => {
  const arrangement = { split: true, top: ["tree"], bottom: ["display", "metadata"], ratio: 0.5 };
  const next = moveFileSheetTab(arrangement, "step", "tree", FILE_SHEET_TAB_PANES.BOTTOM, 0);
  assert.equal(next.split, false);
  assert.deepEqual(next.top, ["tree", "display", "metadata"]);
  assert.deepEqual(next.bottom, []);
});

test("toggling the split off merges panes, on restores the default split", () => {
  const arrangement = defaultFileSheetTabArrangement("step", STEP_SECTIONS);
  const merged = setFileSheetTabSplit(arrangement, "step", false, STEP_SECTIONS);
  assert.equal(merged.split, false);
  assert.deepEqual(merged.top, ["tree", "parameters", "display", "appearance", "metadata"]);

  const reSplit = setFileSheetTabSplit(merged, "step", true, STEP_SECTIONS);
  assert.equal(reSplit.split, true);
  assert.deepEqual(reSplit.top, ["tree"]);
  assert.deepEqual(reSplit.bottom, ["parameters", "display", "appearance", "metadata"]);
});

test("split ratio is clamped", () => {
  assert.equal(clampSplitRatio(0.05), MIN_FILE_SHEET_SPLIT_RATIO);
  assert.equal(clampSplitRatio(0.95), MAX_FILE_SHEET_SPLIT_RATIO);
  assert.equal(clampSplitRatio("nope"), 0.5);
  assert.equal(setFileSheetTabRatio({ top: [], bottom: [] }, 0.7).ratio, 0.7);
});

test("resolve panes: default active is tree on top, reference on bottom", () => {
  const sections = ["tree", "reference", "parameters", "display", "appearance", "metadata"];
  const arrangement = defaultFileSheetTabArrangement("step", sections);
  const resolved = resolveFileSheetTabPanes(arrangement, "step", []);
  assert.equal(resolved.split, true);
  assert.equal(resolved.panes[0].activeId, "tree");
  assert.equal(resolved.panes[1].activeId, "reference");
});

test("resolve panes: an open id activates its tab (last in pane wins)", () => {
  const arrangement = defaultFileSheetTabArrangement("step", STEP_SECTIONS);
  const resolved = resolveFileSheetTabPanes(arrangement, "step", ["tree", "metadata"]);
  assert.equal(resolved.panes[1].activeId, "metadata");
});

test("resolve panes: single strip for non-step uses first tab by default", () => {
  const arrangement = defaultFileSheetTabArrangement("gcode", ["toolpath", "features", "display"]);
  const resolved = resolveFileSheetTabPanes(arrangement, "gcode", []);
  assert.equal(resolved.split, false);
  assert.equal(resolved.panes.length, 1);
  assert.equal(resolved.panes[0].activeId, "toolpath");
});

test("activating a tab prunes pane siblings from the open list", () => {
  const arrangement = defaultFileSheetTabArrangement("step", STEP_SECTIONS);
  let open = ["tree", "display"];
  open = activateFileSheetTab(open, arrangement, "step", FILE_SHEET_TAB_PANES.BOTTOM, "metadata");
  // display (a bottom sibling) is dropped; metadata appended.
  assert.deepEqual(open, ["tree", "metadata"]);
  // tree (top pane) is untouched.
  const resolved = resolveFileSheetTabPanes(arrangement, "step", open);
  assert.equal(resolved.panes[0].activeId, "tree");
  assert.equal(resolved.panes[1].activeId, "metadata");
});

test("layout store round-trips through storage", () => {
  const data = {};
  const storage = {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = value; }
  };
  assert.deepEqual(readFileSheetTabLayoutStore(storage), {});
  writeFileSheetTabLayoutStore(storage, { step: { split: true, top: ["tree"], bottom: ["display"], ratio: 0.4 } });
  const restored = readFileSheetTabLayoutStore(storage);
  assert.deepEqual(restored.step.top, ["tree"]);
  assert.equal(restored.step.ratio, 0.4);
});

test("layout store tolerates missing storage and bad json", () => {
  assert.deepEqual(readFileSheetTabLayoutStore(null), {});
  const storage = { getItem: () => "{not json", setItem: () => {} };
  assert.deepEqual(readFileSheetTabLayoutStore(storage), {});
});
