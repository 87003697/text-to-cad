import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createIgnoreMatcher,
  parseIgnorePatterns,
  parseUploadArgs,
  rewriteCatalogForBlob,
  uploadCatalogDirectoryToVercelBlob,
} from "./upload-catalog-to-blob.mjs";

function makeTempRepo() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "cad-viewer-blob-upload-"));
}

function writeFile(filePath, content = "") {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}

test("upload ignore patterns support comments, directory patterns, globs, and negation", () => {
  const patterns = parseIgnorePatterns(`
# comment
/mechbench/
*.tmp
!keep.tmp
`);
  const ignored = createIgnoreMatcher(patterns);

  assert.equal(ignored({ relativePath: "mechbench", isDirectory: true }), true);
  assert.equal(ignored({ relativePath: "mechbench/part.step" }), true);
  assert.equal(ignored({ relativePath: "nested/file.tmp" }), true);
  assert.equal(ignored({ relativePath: "keep.tmp" }), false);
  assert.equal(ignored({ relativePath: "parts/keep.step" }), false);
});

test("parseUploadArgs accepts a directory and repeated ignore options", () => {
  assert.deepEqual(
    parseUploadArgs([
      "models",
      "--workspace-root",
      "/repo",
      "--ignore-file",
      ".vieweruploadignore",
      "--exclude",
      "/mechbench/",
      "--exclude",
      "/mechbench2/",
      "--concurrency",
      "2",
    ], {}),
    {
      directory: "models",
      workspaceRoot: "/repo",
      rootDir: "",
      ignoreFiles: [".vieweruploadignore"],
      excludePatterns: ["/mechbench/", "/mechbench2/"],
      concurrency: 2,
    }
  );
});

test("rewriteCatalogForBlob annotates STEP sources and generated source files with Blob URLs", () => {
  const repoRoot = makeTempRepo();
  const rootPath = path.join(repoRoot, "models");
  writeFile(path.join(rootPath, "parts/bracket.step"), "ISO-10303-21;\nEND-ISO-10303-21;\n");
  writeFile(path.join(rootPath, "parts/bracket.py"), "def gen_step():\n    return None\n");
  const uploads = new Map([
    ["parts/bracket.step", {
      fileRef: "parts/bracket.step",
      filePath: path.join(rootPath, "parts/bracket.step"),
      url: "https://blob.test/models2/parts/bracket.step",
      hash: "step-hash",
      bytes: 12,
    }],
    ["parts/bracket.py", {
      fileRef: "parts/bracket.py",
      filePath: path.join(rootPath, "parts/bracket.py"),
      url: "https://blob.test/models2/parts/bracket.py",
      hash: "py-hash",
      bytes: 34,
    }],
  ]);

  const catalog = rewriteCatalogForBlob({
    schemaVersion: 4,
    entries: [
      {
        file: "parts/bracket.step",
        kind: "part",
        sourceKind: "python",
        source: {
          file: "models/parts/bracket.py",
          sourcePath: "models/parts/bracket.py",
          sourceHash: "source-hash",
        },
      },
    ],
  }, {
    uploads,
    repoRoot,
    rootPath,
  });

  assert.deepEqual(catalog.entries[0].step, {
    file: "parts/bracket.step",
    url: "https://blob.test/models2/parts/bracket.step",
    hash: "step-hash",
    bytes: 12,
  });
  assert.deepEqual(catalog.entries[0].source, {
    file: "parts/bracket.py",
    sourcePath: "parts/bracket.py",
    sourceHash: "source-hash",
    url: "https://blob.test/models2/parts/bracket.py",
    hash: "py-hash",
    bytes: 34,
  });
});

test("uploadCatalogDirectoryToVercelBlob uploads viewer assets and catalog with exclusions", async () => {
  const repoRoot = makeTempRepo();
  writeFile(path.join(repoRoot, "models/keep.stl"), "solid keep\nendsolid keep\n");
  writeFile(path.join(repoRoot, "models/part.step"), "ISO-10303-21;\nEND-ISO-10303-21;\n");
  writeFile(path.join(repoRoot, "models/mechbench/skipped.stl"), "solid skip\nendsolid skip\n");
  const putCalls = [];

  const result = await uploadCatalogDirectoryToVercelBlob({
    directory: "models",
    workspaceRoot: repoRoot,
    excludePatterns: ["/mechbench/"],
    env: {
      VIEWER_ASSET_BACKEND: "vercel-blob",
      VIEWER_VERCEL_BLOB_PREFIX: "models2",
      VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN: "test-token",
    },
    cwd: repoRoot,
    client: {
      put: async (pathname, body, options) => {
        putCalls.push({ pathname, body, options });
        return { pathname, url: `https://blob.test/${pathname}` };
      },
    },
    logger: { log() {} },
  });

  assert.deepEqual(putCalls.map((call) => call.pathname).sort(), [
    "models2/catalog.json",
    "models2/keep.stl",
    "models2/part.step",
  ]);
  assert.equal(putCalls.find((call) => call.pathname === "models2/keep.stl").options.contentType, "model/stl");
  assert.equal(result.uploadedFiles, 2);
  assert.equal(result.catalogEntries, 2);

  const catalogUpload = putCalls.find((call) => call.pathname === "models2/catalog.json");
  const uploadedCatalog = JSON.parse(catalogUpload.body);
  assert.deepEqual(uploadedCatalog.entries.map((entry) => entry.file), ["keep.stl", "part.step"]);
  assert.equal(uploadedCatalog.entries[0].url, "https://blob.test/models2/keep.stl");
  assert.equal(uploadedCatalog.entries[1].step.url, "https://blob.test/models2/part.step");
});
