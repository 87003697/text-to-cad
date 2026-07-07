import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

function readJson(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(packageRoot, relativePath), "utf8"));
}

function collectSourceFiles(directory, files = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      collectSourceFiles(entryPath, files);
    } else if (/\.[cm]?js$/u.test(entry.name)) {
      files.push(entryPath);
    }
  }
  return files;
}

test("cadjs depends on implicitjs", () => {
  // The viewer installs cadjs only; cadjs pulls implicitjs in transitively and
  // re-exports its shared render/runtime primitives. The dependency flows
  // cadjs -> implicitjs (never the reverse), so the two packages stay a DAG.
  const manifest = readJson("package.json");
  assert.equal(manifest.dependencies?.implicitjs, "file:../implicitjs");
});

test("implicitjs does not depend on cadjs (dependency flows cadjs -> implicitjs)", () => {
  const implicitRoot = path.resolve(packageRoot, "..", "implicitjs");
  const manifest = JSON.parse(fs.readFileSync(path.join(implicitRoot, "package.json"), "utf8"));
  assert.equal(manifest.dependencies?.cadjs, undefined);

  const sourceFiles = collectSourceFiles(path.join(implicitRoot, "src"));
  for (const filePath of sourceFiles) {
    const source = fs.readFileSync(filePath, "utf8");
    assert.equal(
      /\bfrom\s+["']cadjs(?:\/[^"']*)?["']|export\s+\*\s+from\s+["']cadjs(?:\/[^"']*)?["']/u.test(source),
      false,
      `${path.relative(implicitRoot, filePath)} imports cadjs`
    );
  }
});

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
