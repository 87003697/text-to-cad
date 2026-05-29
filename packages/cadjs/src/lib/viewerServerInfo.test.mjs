import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  VIEWER_SERVER_API_VERSION,
  VIEWER_SERVER_APP_ID,
  buildViewerServerInfo,
  isViewerServerInfo,
  normalizeViewerPort,
} from "./viewerServerInfo.mjs";

test("buildViewerServerInfo returns dev-server identity without catalog data", () => {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cad-viewer-server-"));
  fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });

  const info = buildViewerServerInfo({
    workspaceRoot,
    rootDir: "models",
    port: 4184,
    pid: 12345,
  });

  assert.deepEqual(info, {
    schemaVersion: 1,
    serverApiVersion: VIEWER_SERVER_API_VERSION,
    app: VIEWER_SERVER_APP_ID,
    viewerVersion: "",
    serverFeatures: [],
    backend: "local-fs",
    dynamicRoot: false,
    workspaceRoot,
    rootDir: "models",
    rootPath: path.join(workspaceRoot, "models"),
    rootName: "models",
    port: 4184,
    pid: 12345,
    stepArtifactGenerationAvailable: true,
    url: "http://127.0.0.1:4184",
  });
  assert.equal("entries" in info, false);
  assert.equal("root" in info, false);
  assert.equal(isViewerServerInfo(info), true);
});

test("buildViewerServerInfo can describe a dynamic rootless local viewer", () => {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cad-viewer-server-"));

  const info = buildViewerServerInfo({
    workspaceRoot,
    rootDir: "",
    port: 4185,
    pid: 12346,
    dynamicRoot: true,
    viewerVersion: "0.1.10",
    serverFeatures: ["dynamic-root", "", "absolute-file-query"],
  });

  assert.deepEqual(info, {
    schemaVersion: 1,
    serverApiVersion: VIEWER_SERVER_API_VERSION,
    app: VIEWER_SERVER_APP_ID,
    viewerVersion: "0.1.10",
    serverFeatures: ["dynamic-root", "absolute-file-query"],
    backend: "local-fs",
    dynamicRoot: true,
    workspaceRoot,
    rootDir: "",
    rootPath: "",
    rootName: "",
    port: 4185,
    pid: 12346,
    stepArtifactGenerationAvailable: true,
    url: "http://127.0.0.1:4185",
  });
  assert.equal(isViewerServerInfo(info), true);
});

test("normalizeViewerPort falls back for invalid values", () => {
  assert.equal(normalizeViewerPort("4180"), 4180);
  assert.equal(normalizeViewerPort("invalid", 4178), 4178);
  assert.equal(normalizeViewerPort("70000", 4178), 4178);
});
