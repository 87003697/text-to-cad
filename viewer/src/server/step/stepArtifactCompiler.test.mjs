import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { inlineStepGlbArtifactPathForSource } from "cadjs/common/stepSidecars.mjs";
import { scanCadDirectory, scanCadFile } from "../catalog/cadDirectoryScanner.mjs";
import {
  ensureStepArtifactsForCatalog,
  ensureStepTopologyArtifact,
} from "./stepArtifactCompiler.mjs";
import { readTextToCadStepMetadataFile } from "./stepMetadata.mjs";
import { cadPythonEnv, cadPythonExecutable } from "./pythonStepArtifact.mjs";

const stepArtifactSkipReason = (() => {
  const result = spawnSync(
    cadPythonExecutable(process.cwd()),
    ["-c", "import OCP, build123d"],
    {
      cwd: process.cwd(),
      env: cadPythonEnv(),
      encoding: "utf8",
    }
  );
  return result.status === 0 ? "" : "STEP artifact Python dependencies are unavailable";
})();
const stepArtifactTestOptions = stepArtifactSkipReason ? { skip: stepArtifactSkipReason } : {};

function makeTempRepo() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "cad-viewer-step-compile-"));
}

function writePythonBoxGenerator(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, [
    "from build123d import Box",
    "",
    "def gen_step():",
    "    return Box(1, 1, 1)",
    "",
  ].join("\n"));
}

async function waitForStepMetadata(filePath, predicate, { timeoutMs = 10000 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (fs.existsSync(filePath)) {
      try {
        const metadata = readTextToCadStepMetadataFile(filePath);
        if (predicate(metadata)) {
          return metadata;
        }
      } catch {
        // The background writer may still be flushing the STEP file.
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for STEP metadata in ${filePath}`);
}

function readGlbChunks(filePath) {
  const bytes = fs.readFileSync(filePath);
  const jsonLength = bytes.readUInt32LE(12);
  const jsonStart = 20;
  const jsonEnd = jsonStart + jsonLength;
  const json = JSON.parse(bytes.subarray(jsonStart, jsonEnd).toString("utf8"));
  const binaryHeaderStart = jsonEnd;
  const binaryLength = binaryHeaderStart + 8 <= bytes.length ? bytes.readUInt32LE(binaryHeaderStart) : 0;
  const binaryStart = binaryHeaderStart + 8;
  const binary = bytes.subarray(binaryStart, binaryStart + binaryLength);
  return { json, binary };
}

function readStepTopologyIndexManifest(filePath) {
  // The canonical render artifact is a component-GLB package directory whose
  // assembly.json descriptor IS the index manifest.
  if (fs.statSync(filePath).isDirectory()) {
    return JSON.parse(fs.readFileSync(path.join(filePath, "assembly.json"), "utf8"));
  }
  const { json, binary } = readGlbChunks(filePath);
  const indexViewIndex = json.extensions?.STEP_topology?.indexView;
  assert.equal(Number.isInteger(indexViewIndex), true);
  const indexView = json.bufferViews?.[indexViewIndex];
  assert.equal(Boolean(indexView), true);
  const start = Number(indexView.byteOffset || 0);
  const end = start + Number(indexView.byteLength || 0);
  return JSON.parse(binary.subarray(start, end).toString("utf8"));
}

test("ensureStepArtifactsForCatalog discovers Python generators without fixture STEP files", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/block.step");
  const generatorPath = path.join(repoRoot, "workspace/generated/block.step.py");
  writePythonBoxGenerator(generatorPath);

  const results = await ensureStepArtifactsForCatalog({ repoRoot, rootDir: "workspace" });

  assert.equal(results.length, 1);
  assert.equal(results[0].ok, true);
  assert.equal(results[0].sourceKind, "python");
  assert.equal(fs.existsSync(stepPath), false);

  const indexTopology = readStepTopologyIndexManifest(inlineStepGlbArtifactPathForSource(stepPath));
  assert.equal(indexTopology.sourceKind, "python");
  assert.equal(indexTopology.sourcePath, "block.step.py");
  assert.equal(indexTopology.stepPath, "block.step");
  assert.equal(indexTopology.sourceFiles, undefined);

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  assert.equal(catalog.entries.length, 1);
  assert.equal(catalog.entries[0].artifact, undefined);
  assert.ok(catalog.entries[0].url.includes("__cadcache__/models/block.step"));
  assert.equal(catalog.entries[0].hash.length, 64);
});

test("ensureStepTopologyArtifact records explicit non-same-stem Python sourcePath", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/assembly.py");
  writePythonBoxGenerator(generatorPath);

  const result = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    skipStepWrite: true,
    force: true,
  });

  assert.equal(result.ok, true);
  assert.equal(fs.existsSync(stepPath), false);
  assert.equal(result.validation.ok, true);
  assert.equal(result.validation.sourceKind, "python");
  assert.equal(result.validation.sourcePath, "workspace/sources/assembly.py");

  // The descriptor (assembly.json) carries the model-level source provenance; per-edge
  // display topology is embedded in each clean component GLB, not the package descriptor.
  const indexTopology = readStepTopologyIndexManifest(inlineStepGlbArtifactPathForSource(stepPath));
  assert.equal(indexTopology.sourceKind, "python");
  assert.equal(indexTopology.sourcePath, "../sources/assembly.py");
  assert.equal(indexTopology.stepPath, "robot.step");

  // A non-same-stem generated-only model leaves no anchor in the source tree (the package
  // lives inside the skipped __cadcache__, the generator stem differs, and no STEP is
  // written), so a directory walk cannot rediscover it. The per-file scan API that the
  // viewer uses to map the logical STEP path to its package still resolves the entry.
  const entry = scanCadFile({ repoRoot, rootDir: "workspace", filePath: stepPath });
  assert.equal(entry.file, "generated/robot.step");
  assert.equal(entry.artifact, undefined);
  assert.equal(entry.sourceKind, "python");
  assert.equal(entry.source.sourcePath, "workspace/sources/assembly.py");
  assert.ok(entry.url.includes("__cadcache__/models/robot.step"));
  assert.equal(entry.hash.length, 64);
});

test("ensureStepTopologyArtifact can write Python STEP after the GLB is ready", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/robot.py");
  writePythonBoxGenerator(generatorPath);

  const result = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    skipStepWrite: true,
    force: true,
    writeStepAfterArtifact: true,
  });

  assert.equal(result.ok, true);
  assert.equal(result.stepWrite?.status, "complete");
  const glbPath = inlineStepGlbArtifactPathForSource(stepPath);
  assert.equal(fs.existsSync(glbPath), true);
  const indexTopology = readStepTopologyIndexManifest(glbPath);
  assert.equal(indexTopology.sourceKind, "python");
  const metadata = await waitForStepMetadata(stepPath, (candidate) => (
    candidate.sourcePath === "../sources/robot.py" &&
    candidate.sourceHash === indexTopology.sourceHash
  ));
  assert.equal(metadata.sourcePath, "../sources/robot.py");
  assert.equal(metadata.sourceHash, indexTopology.sourceHash);
});

test("ensureStepTopologyArtifact regenerates existing same-stem STEP artifacts from STEP bytes", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/generated/robot.py");
  writePythonBoxGenerator(generatorPath);

  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    skipStepWrite: true,
    force: true,
    writeStepAfterArtifact: true,
  });
  await waitForStepMetadata(stepPath, (candidate) => candidate.sourcePath === "robot.py");

  const glbPath = inlineStepGlbArtifactPathForSource(stepPath);
  // The render artifact is a component-GLB package directory; remove it recursively.
  fs.rmSync(glbPath, { recursive: true, force: true });

  const result = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    force: true,
  });

  assert.equal(result.ok, true);
  assert.equal(result.validation.ok, true);
  assert.equal(result.validation.sourceKind, "step");

  const indexTopology = readStepTopologyIndexManifest(glbPath);
  assert.equal(indexTopology.sourceKind, "step");
  assert.equal(indexTopology.stepPath, "robot.step");
});
