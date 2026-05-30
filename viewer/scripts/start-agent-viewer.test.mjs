import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildAgentStartCommand,
  parseAgentStartArgs,
  resolveAgentStartCommand,
  selectAgentStartMode,
  stripShutdownAfterArgs,
} from "./start-agent-viewer.mjs";

const twelveHoursMs = 12 * 60 * 60 * 1000;

test("parseAgentStartArgs consumes launcher mode and preserves server flags", () => {
  assert.deepEqual(
    parseAgentStartArgs([
      "--viewer-start-mode=dev",
      "--host",
      "127.0.0.1",
      "--port=4178",
      "--shutdown-after",
      "12h",
    ]),
    {
      startMode: "dev",
      forwardedArgs: [
        "--host",
        "127.0.0.1",
        "--port=4178",
        "--shutdown-after",
        "12h",
      ],
      shutdownAfterMs: twelveHoursMs,
    }
  );
});

test("parseAgentStartArgs rejects invalid launcher modes", () => {
  assert.throws(
    () => parseAgentStartArgs(["--viewer-start-mode", "test"]),
    /must be one of/
  );
});

test("stripShutdownAfterArgs removes lifetime flags before starting Vite", () => {
  assert.deepEqual(
    stripShutdownAfterArgs(["--host", "127.0.0.1", "--shutdown-after=30m", "--port", "4178"]),
    ["--host", "127.0.0.1", "--port", "4178"]
  );
});

test("selectAgentStartMode uses dev mode for symlinked npm prefixes", async (t) => {
  const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "cad-viewer-agent-start-"));
  t.after(() => fs.rm(tmpDir, { recursive: true, force: true }));
  const realViewer = path.join(tmpDir, "viewer");
  const linkedViewer = path.join(tmpDir, "skills", "cad-viewer", "scripts", "viewer");
  await fs.mkdir(realViewer, { recursive: true });
  await fs.mkdir(path.dirname(linkedViewer), { recursive: true });
  await fs.symlink(realViewer, linkedViewer, "dir");

  assert.equal(selectAgentStartMode({ npmConfigPrefix: linkedViewer }), "dev");
  assert.equal(selectAgentStartMode({ npmPackageJson: path.join(linkedViewer, "package.json") }), "dev");
  assert.equal(selectAgentStartMode({ npmConfigPrefix: realViewer }), "serve");
  assert.equal(selectAgentStartMode({ requestedMode: "serve", npmConfigPrefix: linkedViewer }), "serve");
});

test("buildAgentStartCommand translates shutdown-after for dev mode", () => {
  const command = buildAgentStartCommand({
    mode: "dev",
    packageRoot: "/workspace/viewer",
    forwardedArgs: ["--host", "127.0.0.1", "--shutdown-after", "12h", "--port", "4178"],
    shutdownAfterMs: twelveHoursMs,
    env: {},
    nodePath: "/node",
  });

  assert.equal(command.command, "/node");
  assert.deepEqual(command.args, [
    "/workspace/viewer/node_modules/vite/bin/vite.js",
    "dev",
    "--host",
    "127.0.0.1",
    "--port",
    "4178",
  ]);
  assert.equal(command.env.VIEWER_SERVER_LIFETIME_MS, String(twelveHoursMs));
});

test("resolveAgentStartCommand keeps shutdown-after on the production server path", () => {
  const command = resolveAgentStartCommand({
    argv: ["--viewer-start-mode", "serve", "--shutdown-after", "12h"],
    env: {},
    packageRoot: "/workspace/viewer",
    nodePath: "/node",
  });

  assert.equal(command.mode, "serve");
  assert.deepEqual(command.args, [
    "/workspace/viewer/src/server/server.mjs",
    "--shutdown-after",
    "12h",
  ]);
});
