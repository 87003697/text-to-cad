#!/usr/bin/env node
// Capture real catalog JSON from the Node scanner for fixtures, so the Python
// scanner port can be diffed against it. Paths are relativized to <repoRoot> so
// the golden is location-independent.
// Run: node viewer/server_py/tests/gen_catalog_golden.mjs <abs-models-root> [subdir ...]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { scanCadDirectory } from "../../src/server/catalog/cadDirectoryScanner.mjs";

const [, , repoRootArg, ...subdirs] = process.argv;
if (!repoRootArg) {
  console.error("usage: gen_catalog_golden.mjs <abs-models-root> [subdir ...]");
  process.exit(1);
}
const repoRoot = path.resolve(repoRootArg);
const targets = subdirs.length ? subdirs : [""];

function relativizeEntry(entry, rootPath) {
  const out = {};
  for (const [key, value] of Object.entries(entry)) {
    if (typeof value === "string" && value.startsWith(rootPath)) {
      out[key] = "<root>" + value.slice(rootPath.length);
    } else if (typeof value === "string" && value.includes(encodeURIComponent(rootPath))) {
      out[key] = value.split(encodeURIComponent(rootPath)).join("<root>");
    } else {
      out[key] = value;
    }
  }
  return out;
}

const golden = {};
for (const subdir of targets) {
  const rootPath = subdir ? path.join(repoRoot, subdir) : repoRoot;
  // Match what /__cad/catalog actually serves (refreshCatalog passes
  // includeArtifactStatus:false), so STEP entries take the descriptor-read path.
  const catalog = scanCadDirectory({ repoRoot, rootDir: subdir, includeArtifactStatus: false });
  golden[subdir || "."] = {
    schemaVersion: catalog.schemaVersion,
    entries: catalog.entries.map((entry) => relativizeEntry(entry, rootPath)),
  };
}

const outPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "catalog_golden.json");
fs.writeFileSync(outPath, JSON.stringify(golden, null, 2) + "\n");
console.log(`wrote ${outPath}`);
for (const [k, v] of Object.entries(golden)) {
  console.log(`  ${k}: ${v.entries.length} entries`);
}
