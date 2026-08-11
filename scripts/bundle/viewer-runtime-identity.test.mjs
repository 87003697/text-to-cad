import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildViewerRuntimeIdentity,
  verifyViewerRuntimeIdentity,
  writeViewerRuntimeIdentity,
} from "./viewer-runtime-identity.mjs";

async function fixture(t) {
  const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), "viewer-runtime-identity-"));
  t.after(() => fs.rm(repoRoot, { recursive: true, force: true }));
  const runtimeRoot = path.join(repoRoot, "skills/cad-viewer/scripts/viewer");
  const files = {
    "viewer/scripts/start-agent-viewer.mjs": "source launcher\n",
    "viewer/src/server/server.mjs": "source server\n",
    "viewer/src/client/main.jsx": "source client\n",
    "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs": "bundle launcher\n",
    "skills/cad-viewer/scripts/viewer/backend/server.mjs": "bundle server\n",
    "skills/cad-viewer/scripts/viewer/dist/index.html": "bundle client\n",
  };
  for (const [relative, content] of Object.entries(files)) {
    const target = path.join(repoRoot, relative);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, content);
  }
  return { repoRoot, runtimeRoot };
}

test("Viewer runtime identity records exact canonical source and generated bundle digests", async (t) => {
  const { repoRoot, runtimeRoot } = await fixture(t);
  const identity = await buildViewerRuntimeIdentity({
    repoRoot,
    runtimeRoot,
    viewerVersion: "0.3.9",
  });

  assert.equal(identity.schema, "cad-viewer.runtime-identity/1");
  assert.equal(identity.viewer_version, "0.3.9");
  assert.deepEqual(
    identity.artifacts.map(({ role, source, bundle }) => ({
      role,
      source: source.path,
      bundle: bundle.path,
    })),
    [
      {
        role: "launcher",
        source: "viewer/scripts/start-agent-viewer.mjs",
        bundle: "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
      },
      {
        role: "server",
        source: "viewer/src/server/server.mjs",
        bundle: "skills/cad-viewer/scripts/viewer/backend/server.mjs",
      },
      {
        role: "client",
        source: "viewer/src/client/main.jsx",
        bundle: "skills/cad-viewer/scripts/viewer/dist/index.html",
      },
    ]
  );
  for (const artifact of identity.artifacts) {
    assert.match(artifact.source.sha256, /^[0-9a-f]{64}$/);
    assert.match(artifact.bundle.sha256, /^[0-9a-f]{64}$/);
  }
});

test("Viewer runtime identity check rejects a stale generated bundle", async (t) => {
  const { repoRoot, runtimeRoot } = await fixture(t);
  await writeViewerRuntimeIdentity({ repoRoot, runtimeRoot, viewerVersion: "0.3.9" });
  await fs.writeFile(path.join(runtimeRoot, "scripts/start-agent-viewer.mjs"), "stale bundle\n");

  await assert.rejects(
    verifyViewerRuntimeIdentity({ repoRoot, runtimeRoot, viewerVersion: "0.3.9" }),
    /Viewer runtime identity is stale/
  );
});
