import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  DEFAULT_ADAPTIVE_PORT_COUNT,
  DEFAULT_PORT_END,
  buildViewerUrl,
  buildViteSpawnOptions,
  catalogHasFile,
  formatEnsureDevResult,
  parseEnsureDevArgs,
  resolveEnsureDevRequest,
  resolveWorkspaceRoot,
  selectViewerServer,
  waitForMatchingServer,
} from "./ensure-dev.mjs";
import { DEFAULT_VIEWER_PORT } from "cadjs/lib/viewerServerInfo.mjs";
import {
  readViewerServerRegistry,
  removeViewerServerRegistryEntry,
  writeViewerServerRegistry,
} from "cadjs/lib/viewerServerRegistry.mjs";

function makeTempWorkspace() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "cad-viewer-ensure-"));
}

test("parseEnsureDevArgs accepts launcher options", () => {
  assert.deepEqual(
    parseEnsureDevArgs([
      "--workspace-root", "/tmp/work",
      "--root-dir=models",
      "--file", "sample.step",
      "--port=4180",
      "--port-end", "4188",
      "--json",
    ]),
    {
      workspaceRoot: "/tmp/work",
      rootDir: "models",
      file: "sample.step",
      port: 4180,
      portEnd: 4188,
      json: true,
      help: false,
    }
  );
});

test("parseEnsureDevArgs rejects repeated file flags", () => {
  assert.throws(
    () => parseEnsureDevArgs(["--file", "first.step", "--file", "second.step"]),
    /--file may only be provided once/
  );
  assert.throws(
    () => parseEnsureDevArgs(["--file=first.step", "--file=second.step"]),
    /--file may only be provided once/
  );
});

test("resolveWorkspaceRoot prefers explicit root, then INIT_CWD outside the app root", () => {
  const appRoot = path.join(os.tmpdir(), "cad-viewer-app");
  assert.equal(
    resolveWorkspaceRoot({
      workspaceRoot: "explicit",
      cwd: "/tmp",
      appRoot,
      env: { INIT_CWD: "/tmp/from-init" },
    }),
    path.resolve("/tmp", "explicit")
  );
  assert.equal(
    resolveWorkspaceRoot({
      cwd: appRoot,
      appRoot,
      env: { INIT_CWD: "/tmp/from-init" },
    }),
    path.resolve("/tmp/from-init")
  );
});

test("resolveEnsureDevRequest maps workspace-relative files inside root directories", () => {
  const workspaceRoot = makeTempWorkspace();
  fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });
  fs.writeFileSync(path.join(workspaceRoot, "models", "sample.step"), "ISO-10303-21;\nEND-ISO-10303-21;\n");

  const request = resolveEnsureDevRequest({
    options: {
      workspaceRoot,
      rootDir: "models",
      file: "models/sample.step",
      port: 4180,
      portEnd: 4182,
    },
    cwd: workspaceRoot,
    env: {},
  });

  assert.equal(request.workspaceRoot, workspaceRoot);
  assert.equal(request.rootDir, "models");
  assert.equal(request.rootPath, path.join(workspaceRoot, "models"));
  assert.equal(request.fileParam, "sample.step");
  assert.equal(request.port, 4180);
  assert.equal(request.portEnd, 4182);
});

test("resolveEnsureDevRequest expands the default port search window", () => {
  const workspaceRoot = makeTempWorkspace();
  fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });

  const request = resolveEnsureDevRequest({
    options: {
      workspaceRoot,
      rootDir: "models",
    },
    cwd: workspaceRoot,
    env: {},
  });

  assert.equal(request.port, DEFAULT_VIEWER_PORT);
  assert.equal(request.portEnd, Math.max(DEFAULT_PORT_END, DEFAULT_VIEWER_PORT + DEFAULT_ADAPTIVE_PORT_COUNT));

  const configured = resolveEnsureDevRequest({
    options: {
      workspaceRoot,
      rootDir: "models",
    },
    cwd: workspaceRoot,
    env: { VIEWER_PORT_END: "4180" },
  });
  assert.equal(configured.portEnd, 4180);
});

test("resolveEnsureDevRequest maps scan-root-relative and absolute file paths", () => {
  const workspaceRoot = makeTempWorkspace();
  const filePath = path.join(workspaceRoot, "models", "nested", "sample part.step");
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, "ISO-10303-21;\nEND-ISO-10303-21;\n");

  const scanRelative = resolveEnsureDevRequest({
    options: {
      workspaceRoot,
      rootDir: "models",
      file: "nested/sample part.step",
    },
    cwd: workspaceRoot,
    env: {},
  });
  assert.equal(scanRelative.fileParam, "nested/sample part.step");

  const absolute = resolveEnsureDevRequest({
    options: {
      workspaceRoot,
      rootDir: "models",
      file: filePath,
    },
    cwd: workspaceRoot,
    env: {},
  });
  assert.equal(absolute.fileParam, "nested/sample part.step");
});

test("resolveEnsureDevRequest rejects files outside the scan root", () => {
  const workspaceRoot = makeTempWorkspace();
  fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });
  assert.throws(() => resolveEnsureDevRequest({
    options: {
      workspaceRoot,
      rootDir: "models",
      file: "../outside.step",
    },
    cwd: workspaceRoot,
    env: {},
  }), /inside the scan root/);
});

test("buildViewerUrl adds scan-root-relative file params", () => {
  assert.equal(
    buildViewerUrl(
      { url: "http://127.0.0.1:4180", port: 4180 },
      "nested/arm step.step"
    ),
    "http://127.0.0.1:4180/?file=nested/arm%20step.step"
  );
});

test("catalogHasFile matches normalized catalog file refs", () => {
  assert.equal(catalogHasFile({ entries: [{ file: "models/part.step" }] }, "models/part.step"), true);
  assert.equal(catalogHasFile({ entries: [{ file: "models/part.step" }] }, "/models\\part.step"), true);
  assert.equal(catalogHasFile({ entries: [{ file: "models/part.step" }] }, "other.step"), false);
});

test("selectViewerServer reuses matching viewer roots", async () => {
  const rootPath = path.resolve("/tmp/work-a");
  const selection = await selectViewerServer({
    rootPath,
    port: 4178,
    portEnd: 4180,
    probeServer: async (port) => port === 4178
      ? { app: "cad-viewer", rootPath, port, url: "http://127.0.0.1:4178" }
      : null,
    canBind: async () => {
      throw new Error("canBind should not be called for a matching CAD Viewer");
    },
  });

  assert.equal(selection.action, "reuse");
  assert.equal(selection.port, 4178);
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

test("selectViewerServer does not trust registry-only matches for requested files", async () => {
  const rootPath = path.resolve("/tmp/work-a");
  const selection = await selectViewerServer({
    rootPath,
    fileParam: "arm.step",
    port: 4178,
    portEnd: 4179,
    probeServer: async () => null,
    canBind: async (port) => port === 4179,
    registeredServers: [
      { app: "cad-viewer", rootPath, port: 4178, pid: process.pid, url: "http://127.0.0.1:4178" }
    ],
  });

  assert.equal(selection.action, "start");
  assert.equal(selection.port, 4179);
});

test("selectViewerServer scans the full range for reusable viewers before binding", async () => {
  const rootPath = path.resolve("/tmp/work-a");
  const selection = await selectViewerServer({
    rootPath,
    port: 4178,
    portEnd: 4180,
    probeServer: async (port) => port === 4180
      ? { app: "cad-viewer", rootPath, port, url: "http://127.0.0.1:4180" }
      : null,
    canBind: async () => {
      throw new Error("canBind should not be called before a later reusable CAD Viewer is checked");
    },
  });

  assert.equal(selection.action, "reuse");
  assert.equal(selection.port, 4180);
});

test("selectViewerServer reuses registered viewers when live probes are blocked", async () => {
  const rootPath = path.resolve("/tmp/work-a");
  const selection = await selectViewerServer({
    rootPath,
    port: 4178,
    portEnd: 4180,
    probeServer: async () => null,
    canBind: async () => {
      throw new Error("canBind should not be called when a registered CAD Viewer matches");
    },
    registeredServers: [
      { app: "cad-viewer", rootPath, port: 4180, pid: process.pid, url: "http://127.0.0.1:4180" }
    ],
  });

  assert.equal(selection.action, "reuse");
  assert.equal(selection.port, 4180);
});

test("selectViewerServer skips different roots and starts on the first free port", async () => {
  const selection = await selectViewerServer({
    rootPath: path.resolve("/tmp/work-b"),
    port: 4178,
    portEnd: 4180,
    probeServer: async (port) => port === 4178
      ? { app: "cad-viewer", rootPath: path.resolve("/tmp/work-a"), port, url: "http://127.0.0.1:4178" }
      : null,
    canBind: async (port) => port === 4179,
  });

  assert.equal(selection.action, "start");
  assert.equal(selection.port, 4179);
});

test("waitForMatchingServer waits until the requested file is cataloged", async () => {
  const rootPath = path.resolve("/tmp/work-a");
  let catalogChecks = 0;

  const serverInfo = await waitForMatchingServer({
    rootPath,
    fileParam: "arm.step",
    port: 4178,
    timeoutMs: 100,
    intervalMs: 1,
    probeServer: async () => ({
      app: "cad-viewer",
      rootPath,
      port: 4178,
      url: "http://127.0.0.1:4178",
    }),
    probeCatalogForFile: async () => {
      catalogChecks += 1;
      return catalogChecks >= 2;
    },
  });

  assert.equal(serverInfo.port, 4178);
  assert.equal(catalogChecks, 2);
});

test("selectViewerServer explains sandbox binding denials", async () => {
  await assert.rejects(
    selectViewerServer({
      rootPath: path.resolve("/tmp/work-c"),
      port: 4178,
      portEnd: 4179,
      probeServer: async () => null,
      canBind: async (port) => ({ canBind: false, errorCode: "EPERM", message: `bind ${port}` }),
    }),
    /local port binding was denied.*EPERM/
  );
});

test("viewer server registry records live servers and removes stale entries", () => {
  const registryPath = path.join(makeTempWorkspace(), "servers.json");
  const liveServer = {
    schemaVersion: 1,
    app: "cad-viewer",
    workspaceRoot: "/tmp/workspace",
    rootDir: "models",
    rootPath: "/tmp/workspace/models",
    port: 4178,
    pid: process.pid,
    url: "http://127.0.0.1:4178",
  };
  const staleServer = {
    ...liveServer,
    port: 4179,
    pid: 999999999,
    url: "http://127.0.0.1:4179",
  };

  assert.equal(writeViewerServerRegistry(staleServer, { registryPath }), true);
  assert.equal(writeViewerServerRegistry(liveServer, { registryPath }), true);
  assert.deepEqual(
    readViewerServerRegistry({ registryPath }).map((server) => server.port),
    [4178]
  );

  assert.equal(removeViewerServerRegistryEntry(liveServer, { registryPath }), true);
  assert.deepEqual(readViewerServerRegistry({ registryPath }), []);
});

test("buildViteSpawnOptions starts native Vite with explicit CAD Viewer environment", () => {
  const spawnOptions = buildViteSpawnOptions({
    workspaceRoot: "/tmp/workspace",
    rootDir: "models",
    port: 4182,
    env: { PATH: "/bin" },
  });

  assert.equal(spawnOptions.command, process.execPath);
  assert.equal(spawnOptions.args.at(-1), "dev");
  assert.equal(spawnOptions.options.env.VIEWER_ASSET_BACKEND, "local-fs");
  assert.equal(spawnOptions.options.env.VIEWER_LOCAL_WORKSPACE_ROOT, "/tmp/workspace");
  assert.equal(spawnOptions.options.env.VIEWER_LOCAL_ROOT_DIR, "models");
  assert.equal(spawnOptions.options.env.VIEWER_PORT, "4182");
  assert.equal(spawnOptions.options.detached, true);
});

test("formatEnsureDevResult can print JSON payloads", () => {
  assert.deepEqual(
    JSON.parse(formatEnsureDevResult({
      action: "reused",
      serverInfo: {
        app: "cad-viewer",
        rootPath: "/tmp/work",
        port: 4178,
        url: "http://127.0.0.1:4178",
      },
      fileParam: "part.step",
      json: true,
    })),
    {
      action: "reused",
      url: "http://127.0.0.1:4178/?file=part.step",
      server: {
        app: "cad-viewer",
        rootPath: "/tmp/work",
        port: 4178,
        url: "http://127.0.0.1:4178",
      },
    }
  );
});
