import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  catalogHasFile,
  parseEnsureServeArgs,
  selectViewerServer,
} from "./ensure-serve.mjs";

test("parseEnsureServeArgs rejects repeated file flags", () => {
  assert.throws(
    () => parseEnsureServeArgs(["--file", "first.step", "--file", "second.step"]),
    /--file may only be provided once/
  );
  assert.throws(
    () => parseEnsureServeArgs(["--file=first.step", "--file=second.step"]),
    /--file may only be provided once/
  );
});

test("catalogHasFile matches normalized catalog file refs", () => {
  assert.equal(catalogHasFile({ entries: [{ file: "models/part.step" }] }, "models/part.step"), true);
  assert.equal(catalogHasFile({ entries: [{ file: "models/part.step" }] }, "/models\\part.step"), true);
  assert.equal(catalogHasFile({ entries: [{ file: "models/part.step" }] }, "other.step"), false);
});

test("selectViewerServer skips matching roots that cannot serve the requested file", async () => {
  const rootPath = path.resolve("/tmp/work-a");
  const selection = await selectViewerServer({
    rootPath,
    fileParam: "arm.step",
    port: 4178,
    portEnd: 4179,
    probeServer: async (port) => port === 4178
      ? { app: "cad-viewer", rootPath, port, url: "http://127.0.0.1:4178" }
      : null,
    probeCatalogForFile: async () => false,
    canBind: async (port) => port === 4179,
  });

  assert.equal(selection.action, "start");
  assert.equal(selection.port, 4179);
});
