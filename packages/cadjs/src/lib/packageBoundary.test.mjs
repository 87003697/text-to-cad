import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

test("viewer-only server workflow modules stay out of cadjs", () => {
  for (const relativePath of [
    "src/lib/cadDirectoryScanner.mjs",
    "src/lib/generationStatus.mjs",
    "src/lib/step/stepArtifactCompiler.mjs",
    "src/lib/cadManifestStore.js",
    "src/lib/cadViewerDirectorySession.mjs",
    "src/lib/viewerConfig.mjs",
    "src/lib/viewerServerInfo.mjs",
    "src/lib/viewerServerRegistry.mjs",
  ]) {
    assert.equal(fs.existsSync(path.join(packageRoot, relativePath)), false, relativePath);
  }
});

test("viewer workspace resolution stays in viewer", () => {
  const pathUtilsSource = fs.readFileSync(path.join(packageRoot, "src/lib/pathUtils.mjs"), "utf8");
  assert.equal(/\bresolveWorkspaceRoot\b/u.test(pathUtilsSource), false);
});
