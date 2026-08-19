import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const viewerSrcRoot = path.dirname(fileURLToPath(import.meta.url));
const selfPath = fileURLToPath(import.meta.url);

function collectSourceFiles(directory, files = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      collectSourceFiles(entryPath, files);
    } else if (/\.[cm]?jsx?$/u.test(entry.name)) {
      files.push(entryPath);
    }
  }
  return files;
}

test("viewer does not import retired implicit CAD runtimes", () => {
  const offenders = [];
  for (const filePath of collectSourceFiles(viewerSrcRoot)) {
    if (filePath === selfPath) {
      continue;
    }
    const source = fs.readFileSync(filePath, "utf8");
    if (/["'](?:implicitjs|cadjs\/implicit)(?:\/[^"']*)?["']/u.test(source)) {
      offenders.push(path.relative(viewerSrcRoot, filePath));
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `viewer sources import retired implicit CAD runtimes: ${offenders.join(", ")}`
  );
});
