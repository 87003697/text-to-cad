import assert from "node:assert/strict";
import test from "node:test";

import {
  applyServerArgsToEnv,
  parseServerArgs,
} from "./serverArgs.mjs";

test("parseServerArgs accepts direct backend root and port flags", () => {
  assert.deepEqual(
    parseServerArgs(["--root-dir", "/tmp/models", "--port=4190", "--host", "0.0.0.0"]),
    {
      rootDir: "/tmp/models",
      port: 4190,
      host: "0.0.0.0",
      help: false,
    }
  );
});

test("applyServerArgsToEnv gives CLI root and port priority over env", () => {
  const result = applyServerArgsToEnv({
    argv: ["--root-dir", "models", "--port", "4190"],
    cwd: "/tmp/workspace",
    env: {
      VIEWER_LOCAL_WORKSPACE_ROOT: "/tmp/old-workspace",
      VIEWER_LOCAL_ROOT_DIR: "old-models",
      VIEWER_PORT: "4178",
    },
  });

  assert.equal(result.env.VIEWER_ASSET_BACKEND, "local-fs");
  assert.equal(result.env.VIEWER_LOCAL_WORKSPACE_ROOT, "/tmp/workspace/models");
  assert.equal(result.env.VIEWER_LOCAL_ROOT_DIR, "");
  assert.equal(result.env.VIEWER_PORT, "4190");
});

test("parseServerArgs rejects invalid ports", () => {
  assert.throws(() => parseServerArgs(["--port", "99999"]), /TCP port/);
});
