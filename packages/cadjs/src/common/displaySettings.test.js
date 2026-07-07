import assert from "node:assert/strict";
import test from "node:test";

import {
  CAD_DISPLAY_MODE,
  CAMERA_PROJECTION,
  DEFAULT_DISPLAY_EDGE_SETTINGS,
  DEFAULT_DISPLAY_SETTINGS,
  DEFAULT_EXPLODED_VIEW_SETTINGS,
  displayModeForcesEdges,
  displayModeShowsEdges,
  displayModeShowsThroughEdges,
  displayModeSurfaceOpacity,
  displayModeUsesUnlitSurfaces,
  displaySettingsEqual,
  normalizeDisplayEdgeSettings,
  normalizeDisplaySettings,
  normalizeExplodedViewSettings,
  resolveDisplayMode
} from "./displaySettings.js";

test("display settings normalize mode and clip independently from appearance settings", () => {
  assert.deepEqual(normalizeDisplaySettings(), DEFAULT_DISPLAY_SETTINGS);
  assert.equal(resolveDisplayMode({ mode: "wireframe" }), CAD_DISPLAY_MODE.WIREFRAME);
  // Projection is a theme trait now, not a display setting: it is dropped on
  // normalization even when a stale caller still passes it.
  assert.equal(Object.hasOwn(normalizeDisplaySettings(), "projection"), false);
  assert.equal(Object.hasOwn(normalizeDisplaySettings({ projection: "perspective" }), "projection"), false);
  assert.deepEqual(normalizeDisplaySettings({
    projection: "perspective",
    mode: "wireframe",
    clip: {
      enabled: true,
      axis: "z",
      offset: 0.4,
      invert: true
    }
  }), {
    mode: CAD_DISPLAY_MODE.WIREFRAME,
    clip: {
      enabled: true,
      axis: "z",
      offset: 0.4,
      offsets: {
        x: 0,
        y: 0,
        z: 0.4
      },
      invert: true
    },
    exploded: DEFAULT_EXPLODED_VIEW_SETTINGS,
    edges: DEFAULT_DISPLAY_EDGE_SETTINGS
  });
});

test("display settings normalize edge styling independently from appearance settings", () => {
  assert.deepEqual(normalizeDisplayEdgeSettings({
    enabled: false,
    contrastMode: "auto",
    color: "#ABC",
    thickness: 2,
    classes: {
      tangent: {
        enabled: false,
        color: "#456",
        opacity: 0.25,
        thickness: 4
      }
    },
    highlightColor: "#123456",
    highlightOpacity: 0.4,
    highlightThickness: 4,
    silhouette: true,
    silhouetteScale: 0.01
  }), {
    enabled: false,
    contrastMode: "auto",
    color: "#aabbcc",
    thickness: 2,
    classes: {
      feature: { color: "#aabbcc", opacity: 1, thickness: 1.15 },
      tangent: { color: "#445566", opacity: 0.25, thickness: 0 },
      seam: { color: "#aabbcc", opacity: 0.85, thickness: 1.15 },
      degenerate: { color: "#aabbcc", opacity: 1, thickness: 0 }
    },
    highlightColor: "#123456",
    highlightOpacity: 0.4,
    highlightThickness: 4,
    silhouette: true,
    silhouetteScale: 0.01
  });
  assert.deepEqual(normalizeDisplaySettings({
    mode: "solid",
    edges: {
      enabled: false,
      color: "#456"
    }
  }).edges, {
    ...DEFAULT_DISPLAY_EDGE_SETTINGS,
    enabled: false,
    color: "#445566",
    classes: {
      feature: { color: "#445566", opacity: 1, thickness: 1.15 },
      tangent: { color: "#445566", opacity: 0.5, thickness: 1.15 },
      seam: { color: "#445566", opacity: 0.85, thickness: 1.15 },
      degenerate: { color: "#445566", opacity: 1, thickness: 0 }
    }
  });
});

test("display settings normalize the exploded-view step document independently from mode", () => {
  const doc = normalizeExplodedViewSettings({
    enabled: true,
    amount: 0.5,
    order: "sequential",
    trails: true,
    auto: { mode: "x", direction: "negative", depth: 4, gapScale: 2, keepBaseGrounded: false },
    steps: [{ id: "s1", type: "translate", targets: ["o1.2"], axis: [0, 0, 3], distance: 12 }]
  });
  assert.equal(doc.enabled, true);
  assert.equal(doc.amount, 0.5);
  assert.equal(doc.order, "sequential");
  assert.equal(doc.trails, true);
  assert.deepEqual(doc.auto, { mode: "x", direction: "negative", depth: 4, gapScale: 2, keepBaseGrounded: false });
  assert.equal(doc.steps.length, 1);
  assert.deepEqual(doc.steps[0].axis, [0, 0, 1]); // normalized to unit
  assert.equal(doc.steps[0].distance, 12);

  // Unknown auto modes fall back to "auto"; "radial" is preserved.
  assert.equal(normalizeExplodedViewSettings({ auto: { mode: "diagonal" } }).auto.mode, "auto");
  assert.equal(normalizeExplodedViewSettings({ auto: { mode: "radial" } }).auto.mode, "radial");
  // Amount clamps and invalid steps drop.
  assert.equal(normalizeExplodedViewSettings({ amount: 9 }).amount, 1);
  assert.equal(normalizeExplodedViewSettings({ steps: [{ type: "translate", targets: [] }] }).steps.length, 0);

  assert.equal(normalizeDisplaySettings({ exploded: true }).exploded.enabled, false);
  assert.equal(normalizeDisplaySettings({ mode: "exploded", exploded: { enabled: true } }).exploded.enabled, true);
  assert.deepEqual(normalizeDisplaySettings({ mode: "exploded view" }), DEFAULT_DISPLAY_SETTINGS);
});

test("display modes normalize common CAD aliases", () => {
  assert.equal(resolveDisplayMode({ mode: "edges" }), CAD_DISPLAY_MODE.SOLID);
  assert.equal(resolveDisplayMode({ mode: "shaded-with-edges" }), CAD_DISPLAY_MODE.SOLID);
  assert.equal(resolveDisplayMode({ mode: "shaded without edges" }), CAD_DISPLAY_MODE.RENDERED);
  assert.equal(resolveDisplayMode({ mode: "x-ray" }), CAD_DISPLAY_MODE.TRANSPARENT);
  assert.equal(resolveDisplayMode({ mode: "hidden edges visible" }), CAD_DISPLAY_MODE.HIDDEN_EDGES);
  assert.equal(resolveDisplayMode({ mode: "hidden-lines-removed" }), CAD_DISPLAY_MODE.HIDDEN_LINES_REMOVED);
  assert.equal(resolveDisplayMode({ mode: "flat" }), CAD_DISPLAY_MODE.UNSHADED);
  assert.equal(resolveDisplayMode({ mode: "appearance" }), CAD_DISPLAY_MODE.RENDERED);
  assert.equal(resolveDisplayMode({ mode: "wire" }), CAD_DISPLAY_MODE.WIREFRAME);
});

test("display mode policies describe edge and surface behavior", () => {
  assert.equal(displayModeShowsEdges(CAD_DISPLAY_MODE.SOLID, { enabled: false }), true);
  assert.equal(displayModeForcesEdges(CAD_DISPLAY_MODE.SOLID), true);
  assert.equal(displayModeShowsEdges(CAD_DISPLAY_MODE.RENDERED, { enabled: true }), false);
  assert.equal(displayModeShowsThroughEdges(CAD_DISPLAY_MODE.HIDDEN_EDGES), true);
  assert.equal(displayModeShowsThroughEdges(CAD_DISPLAY_MODE.TRANSPARENT), true);
  assert.equal(displayModeUsesUnlitSurfaces(CAD_DISPLAY_MODE.UNSHADED), true);
  assert.equal(displayModeSurfaceOpacity(CAD_DISPLAY_MODE.TRANSPARENT, 1), 0.22);
  assert.equal(displayModeSurfaceOpacity(CAD_DISPLAY_MODE.HIDDEN_LINES_REMOVED, 1), 0.045);
});

test("display settings compare after normalization", () => {
  assert.equal(displaySettingsEqual(
    { mode: "wireframe", clip: { enabled: true, axis: "x", offset: 0.5 } },
    { mode: CAD_DISPLAY_MODE.WIREFRAME, clip: { enabled: true, axis: "x", offsets: { x: 0.5 } } }
  ), true);
  assert.equal(displaySettingsEqual(
    { mode: "solid", clip: { enabled: true } },
    { mode: "wireframe", clip: { enabled: true } }
  ), false);
  assert.equal(displaySettingsEqual(
    { mode: "solid", exploded: { enabled: true } },
    { mode: "solid", exploded: { enabled: false } }
  ), false);
  assert.equal(displaySettingsEqual(
    { mode: "solid", edges: { color: "#111111" } },
    { mode: "solid", edges: { color: "#222222" } }
  ), false);
  // Projection no longer belongs to display settings, so differing projection
  // values do not make two otherwise-equal display settings unequal.
  assert.equal(displaySettingsEqual(
    { mode: "solid", projection: "orthographic" },
    { mode: "solid", projection: "perspective" }
  ), true);
});
