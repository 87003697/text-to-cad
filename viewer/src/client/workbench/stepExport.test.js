import assert from "node:assert/strict";
import test from "node:test";

import {
  STEP_EXPORT_FORMATS,
  isImportedStepEntry,
  stepExportItemLabel,
  requestStepExport,
} from "./stepExport.js";

test("STEP export formats are the four supported kinds", () => {
  assert.deepEqual([...STEP_EXPORT_FORMATS].sort(), ["3mf", "glb", "step", "stl"]);
});

test("isImportedStepEntry is false for a generator-backed entry, true for an imported one", () => {
  assert.equal(isImportedStepEntry({ source: { sourcePath: "/m/tom.step.py" } }), false);
  assert.equal(isImportedStepEntry({ source: null }), true);
  assert.equal(isImportedStepEntry({}), true);
});

test("stepExportItemLabel: STEP is always a download, other formats are exports", () => {
  assert.equal(stepExportItemLabel("step"), "Download STEP");
  assert.equal(stepExportItemLabel("3mf"), "Export 3MF");
  assert.equal(stepExportItemLabel("stl"), "Export STL");
  assert.equal(stepExportItemLabel("glb"), "Export GLB");
});

test("requestStepExport preserves an absolute file ref (leading slash kept)", async () => {
  // Regression: catalog entry.file refs are absolute; stripping the leading "/" made the
  // server resolve a bogus relative path → "STEP file not found".
  const absolute = "/Users/me/models/mech/widget.step";
  let capturedUrl = "";
  let capturedBody = null;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    capturedUrl = String(url);
    capturedBody = JSON.parse(opts.body);
    return {
      ok: true,
      json: async () => ({ ok: true, path: "/dest/widget.glb", filename: "widget.glb", format: "glb" }),
    };
  };
  try {
    const result = await requestStepExport({ file: absolute, format: "glb" });
    assert.equal(result.ok, true);
    assert.ok(capturedUrl.includes(encodeURIComponent(absolute)), `absolute ref not preserved: ${capturedUrl}`);
    assert.equal(capturedBody.file, absolute);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("requestStepExport surfaces a cancelled dialog without throwing", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: false, cancelled: true }) });
  try {
    const result = await requestStepExport({ file: "/m/x.step", format: "stl" });
    assert.deepEqual(result, { cancelled: true });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
