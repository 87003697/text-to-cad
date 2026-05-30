import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createLocalAssetBackend } from "./localAssetBackend.mjs";

const STEP_TOPOLOGY_SCHEMA_VERSION = 2;

async function withTempWorkspace(callback) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cad-viewer-backend-"));
  try {
    return await callback(workspaceRoot);
  } finally {
    fs.rmSync(workspaceRoot, { recursive: true, force: true });
  }
}

function sha256File(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function pad4(buffer, byte = 0) {
  const padding = (4 - (buffer.length % 4)) % 4;
  return padding ? Buffer.concat([buffer, Buffer.alloc(padding, byte)]) : buffer;
}

function topologyGlb({ sourcePath, stepHash }) {
  let binary = Buffer.alloc(0);
  const bufferViews = [];
  function addBufferView(payload) {
    binary = pad4(binary);
    const byteOffset = binary.length;
    binary = Buffer.concat([binary, payload]);
    const index = bufferViews.length;
    bufferViews.push({ buffer: 0, byteOffset, byteLength: payload.length });
    return index;
  }
  const edgeRendering = {
    visibilityClasses: ["feature", "tangent", "seam", "degenerate"],
    generatedVisibilityClasses: ["feature"],
    visibilityClassCounts: { feature: 1 },
    generatedVisibilityClassCounts: { feature: 1 },
  };
  const manifest = {
    schemaVersion: STEP_TOPOLOGY_SCHEMA_VERSION,
    profile: "index",
    entryKind: "part",
    sourceKind: "step",
    sourcePath,
    stepHash,
    edgeRendering,
  };
  const indexView = addBufferView(Buffer.from(JSON.stringify(manifest), "utf8"));
  const surfaceHalfEdgesView = addBufferView(Buffer.alloc(0));
  const edgeView = addBufferView(Buffer.from(JSON.stringify({
    schemaVersion: STEP_TOPOLOGY_SCHEMA_VERSION,
    profile: "surface-edges",
    sourceKind: "step",
    sourcePath,
    stepHash,
    edgeRendering,
    buffers: {
      littleEndian: true,
      views: {
        surfaceHalfEdges: {
          dtype: "uint32",
          bufferView: surfaceHalfEdgesView,
          byteOffset: 0,
          byteLength: 0,
          count: 0,
          itemSize: 4,
        },
      },
    },
  }), "utf8"));
  const selectorView = addBufferView(Buffer.from(JSON.stringify({
    schemaVersion: STEP_TOPOLOGY_SCHEMA_VERSION,
    profile: "selector",
    sourceKind: "step",
    sourcePath,
    stepHash,
  }), "utf8"));
  binary = pad4(binary);
  const gltf = {
    asset: { version: "2.0" },
    buffers: [{ byteLength: binary.length }],
    bufferViews,
    meshes: [{
      primitives: [{
        attributes: {
          _CAD_EDGE_BARYCENTRIC: 0,
          _CAD_EDGE_CLASS: 1,
        },
      }],
    }],
    extensionsUsed: ["STEP_topology"],
    extensions: {
      STEP_topology: {
        schemaVersion: STEP_TOPOLOGY_SCHEMA_VERSION,
        entryKind: "part",
        indexView,
        edgeView,
        selectorView,
        encoding: "utf-8",
      },
    },
  };
  const jsonChunk = pad4(Buffer.from(JSON.stringify(gltf), "utf8"), 0x20);
  const header = Buffer.alloc(12);
  const jsonHeader = Buffer.alloc(8);
  const binHeader = Buffer.alloc(8);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonChunk.length + 8 + binary.length, 8);
  jsonHeader.writeUInt32LE(jsonChunk.length, 0);
  jsonHeader.write("JSON", 4, "latin1");
  binHeader.writeUInt32LE(binary.length, 0);
  binHeader.write("BIN\0", 4, "latin1");
  return Buffer.concat([header, jsonHeader, jsonChunk, binHeader, binary]);
}

function writeStepWithSourceMetadata(filePath, sourcePath) {
  fs.writeFileSync(filePath, [
    "ISO-10303-21;",
    "DATA;",
    `#1=DESCRIPTIVE_REPRESENTATION_ITEM('cadpy:sourcePath','${sourcePath}');`,
    "#2=REPRESENTATION('cadpy:sourcePath',(#1),#9);",
    "#3=PROPERTY_DEFINITION('cadpy metadata','cadpy:sourcePath',#10);",
    "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
    "ENDSEC;",
    "END-ISO-10303-21;",
    "",
  ].join("\n"));
}

test("local backend serves catalog from an in-memory scan without writing catalog files", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    const hiddenCatalogPath = path.join(workspaceRoot, ".catalog.json");
    const visibleCatalogPath = path.join(workspaceRoot, "catalog.json");
    const modelCatalogPath = path.join(modelRoot, "catalog.json");
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(path.join(modelRoot, "sample.stl"), "solid sample\nendsolid sample\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    const refreshed = backend.refreshCatalog();
    const catalog = backend.readCatalog();

    assert.deepEqual(catalog, refreshed);
    assert.equal(catalog.schemaVersion, 4);
    assert.equal(fs.existsSync(hiddenCatalogPath), false);
    assert.equal(fs.existsSync(visibleCatalogPath), false);
    assert.equal(fs.existsSync(modelCatalogPath), false);
    assert.deepEqual(catalog.entries.map((entry) => ({
      file: entry.file,
      rootRelativeFile: entry.rootRelativeFile,
      kind: entry.kind,
      hasUrl: Boolean(entry.url),
      hasHash: Boolean(entry.hash),
      bytes: entry.bytes,
    })), [{
      file: path.join(modelRoot, "sample.stl"),
      rootRelativeFile: "sample.stl",
      kind: "stl",
      hasUrl: true,
      hasHash: true,
      bytes: "solid sample\nendsolid sample\n".length,
    }]);
    fs.writeFileSync(path.join(modelRoot, "late.stl"), "solid late\nendsolid late\n");
    assert.equal(backend.readCatalog().entries.some((entry) => entry.rootRelativeFile === "late.stl"), false);
    assert.equal(backend.refreshCatalog().entries.some((entry) => entry.rootRelativeFile === "late.stl"), true);
    assert.equal(fs.existsSync(hiddenCatalogPath), false);
    assert.equal(fs.existsSync(visibleCatalogPath), false);
    assert.equal(fs.existsSync(modelCatalogPath), false);
    assert.equal("writeCatalog" in backend, false);
  });
});

test("local backend defaults directory mode to the workspace root", async () => {
  await withTempWorkspace((workspaceRoot) => {
    fs.writeFileSync(path.join(workspaceRoot, "sample.stl"), "solid sample\nendsolid sample\n");
    const backend = createLocalAssetBackend({ workspaceRoot });

    assert.deepEqual(backend.readCatalog().entries.map((entry) => entry.rootRelativeFile), ["sample.stl"]);
  });
});

test("local backend accepts relative request directories from the workspace root", async () => {
  await withTempWorkspace((workspaceRoot) => {
    fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });
    fs.writeFileSync(path.join(workspaceRoot, "models", "sample.stl"), "solid sample\nendsolid sample\n");
    const backend = createLocalAssetBackend({ workspaceRoot });

    assert.deepEqual(backend.readCatalog({ rootDir: "models" }).entries.map((entry) => entry.rootRelativeFile), ["sample.stl"]);
  });
});

test("local backend keeps directory mode for file params that look absolute", async () => {
  await withTempWorkspace((workspaceRoot) => {
    fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });
    fs.mkdirSync(path.join(workspaceRoot, "other"), { recursive: true });
    fs.writeFileSync(path.join(workspaceRoot, "models", "inside.stl"), "solid inside\nendsolid inside\n");
    const outsidePath = path.join(workspaceRoot, "other", "solo.stl");
    fs.writeFileSync(outsidePath, "solid solo\nendsolid solo\n");
    const backend = createLocalAssetBackend({ workspaceRoot });

    const catalog = backend.readCatalog({ rootDir: "models", fileRef: outsidePath });

    assert.deepEqual(catalog.entries.map((entry) => entry.rootRelativeFile), ["inside.stl"]);
    assert.deepEqual(catalog.entries.map((entry) => entry.file), [path.join(workspaceRoot, "models", "inside.stl")]);
  });
});

test("local backend keeps directory mode for missing relative file params", async () => {
  await withTempWorkspace((workspaceRoot) => {
    fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });
    fs.writeFileSync(path.join(workspaceRoot, "models", "sample.stl"), "solid sample\nendsolid sample\n");
    const backend = createLocalAssetBackend({ workspaceRoot });

    assert.deepEqual(
      backend.readCatalog({ rootDir: "models", fileRef: "missing.stl" }).entries.map((entry) => entry.rootRelativeFile),
      ["sample.stl"]
    );
  });
});

test("local backend reports missing request directories", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const backend = createLocalAssetBackend({ workspaceRoot });

    assert.throws(
      () => backend.readCatalog({ rootDir: "missing-models" }),
      /CAD Viewer directory not found/
    );
  });
});

test("local backend incrementally refreshes changed CAD catalog entries", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    const firstPath = path.join(modelRoot, "first.stl");
    const secondPath = path.join(modelRoot, "second.stl");
    fs.writeFileSync(firstPath, "solid first\nendsolid first\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    assert.deepEqual(backend.readCatalog().entries.map((entry) => entry.rootRelativeFile), ["first.stl"]);

    fs.writeFileSync(secondPath, "solid second\nendsolid second\n");
    assert.deepEqual(backend.readCatalog().entries.map((entry) => entry.rootRelativeFile), ["first.stl"]);
    assert.deepEqual(
      backend.refreshCatalogForPath({ filePath: secondPath }).entries.map((entry) => entry.rootRelativeFile),
      ["first.stl", "second.stl"]
    );

    fs.unlinkSync(firstPath);
    assert.deepEqual(
      backend.refreshCatalogForPath({ filePath: firstPath }).entries.map((entry) => entry.rootRelativeFile),
      ["second.stl"]
    );
  });
});

test("local backend refreshes requested files against a cached dynamic root catalog", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    const firstPath = path.join(modelRoot, "first.stl");
    const secondPath = path.join(modelRoot, "second.stl");
    fs.writeFileSync(firstPath, "solid first\nendsolid first\n");
    const backend = createLocalAssetBackend({ workspaceRoot });

    assert.deepEqual(
      backend.readCatalog({ rootDir: modelRoot, fileRef: "first.stl" }).entries.map((entry) => entry.rootRelativeFile),
      ["first.stl"]
    );

    fs.writeFileSync(secondPath, "solid second\nendsolid second\n");
    assert.deepEqual(
      backend.readCatalog({ rootDir: modelRoot, fileRef: "second.stl" }).entries.map((entry) => entry.rootRelativeFile),
      ["first.stl", "second.stl"]
    );

    fs.unlinkSync(firstPath);
    assert.deepEqual(
      backend.readCatalog({ rootDir: modelRoot, fileRef: "first.stl" }).entries.map((entry) => entry.rootRelativeFile),
      ["second.stl"]
    );
  });
});

test("local backend can auto-refresh cached catalogs on reads", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(path.join(modelRoot, "first.stl"), "solid first\nendsolid first\n");
    let currentTime = 1000;
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      catalogRefreshIntervalMs: 100,
      now: () => currentTime,
    });

    assert.deepEqual(backend.readCatalog().entries.map((entry) => entry.rootRelativeFile), ["first.stl"]);

    fs.writeFileSync(path.join(modelRoot, "second.stl"), "solid second\nendsolid second\n");
    assert.deepEqual(backend.readCatalog().entries.map((entry) => entry.rootRelativeFile), ["first.stl"]);

    currentTime = 1100;
    assert.deepEqual(backend.readCatalog().entries.map((entry) => entry.rootRelativeFile), ["first.stl", "second.stl"]);
  });
});

test("local backend incrementally refreshes STEP entries when sidecars change", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    const stepPath = path.join(modelRoot, "part.step");
    const modulePath = path.join(modelRoot, ".part.step.js");
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    assert.equal(backend.readCatalog().entries[0].moduleUrl, undefined);

    fs.writeFileSync(modulePath, "export default { manifest: { schemaVersion: 1 } };\n");
    const withModule = backend.refreshCatalogForPath({ filePath: modulePath });
    assert.ok(withModule.entries[0].moduleUrl.startsWith("/__cad/asset?file="));
    assert.equal(withModule.entries[0].moduleFile, modulePath);

    fs.unlinkSync(modulePath);
    const withoutModule = backend.refreshCatalogForPath({ filePath: modulePath });
    assert.equal(withoutModule.entries[0].moduleUrl, undefined);
  });
});

test("local backend reports active generator status for the active root", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    const statusPath = path.join(modelRoot, ".part.step.run-1.generation.lock.json");
    const updatedAt = new Date().toISOString();
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(statusPath, JSON.stringify({
      status: "running",
      id: "run-1",
      pid: process.pid,
      startedAt: updatedAt,
      updatedAt,
      sourcePath: "models/part.py",
      generator: "gen_step",
      outputs: [{ path: "models/part.step", kind: "step" }],
    }));
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    const status = backend.readGenerationStatus();

    assert.equal(status.files[path.join(modelRoot, "part.step")].running, true);
    assert.equal(status.files[path.join(modelRoot, "part.step")].generator, "gen_step");
    assert.equal(backend.generationStatusDir(), modelRoot);
    assert.equal(backend.isGenerationStatusPath(statusPath), true);
  });
});

test("local backend resolves same-stem Python generators without requiring a STEP file", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(path.join(modelRoot, "box.py"), "def gen_step():\n    return None\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    const resolved = backend.resolveStepSource("box.py");

    assert.equal(resolved.stepPath, path.join(modelRoot, "box.step"));
    assert.equal(resolved.skipStepWrite, true);
  });
});

test("local backend rejects Viewer artifact regeneration for same-stem Python generators", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    const generatorPath = path.join(modelRoot, "robot", "robot.py");
    fs.mkdirSync(path.dirname(generatorPath), { recursive: true });
    fs.writeFileSync(generatorPath, "def gen_step():\n    return None\n");
    const stepPath = path.join(modelRoot, "robot", "robot.step");
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      stepArtifactGenerator: async () => {
        throw new Error("Python generators should not be invoked by Viewer regeneration.");
      },
    });
    const resolved = backend.resolveStepSource("robot/robot.step");

    assert.equal(resolved.stepPath, stepPath);
    assert.equal(resolved.sourcePath, generatorPath);
    assert.equal(resolved.skipStepWrite, true);
    await assert.rejects(
      () => backend.generateStepArtifact({
        fileRef: "robot/robot.step",
        force: true,
      }),
      /only regenerates GLB artifacts for imported STEP files/
    );
  });
});

test("local backend regenerates GLB artifacts for imported STEP files", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(path.join(modelRoot, "robot"), { recursive: true });
    const stepPath = path.join(modelRoot, "robot", "robot.step");
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      stepArtifactGenerator: async (request) => {
        assert.equal(request.stepPath, stepPath);
        assert.equal(request.sourcePath, "");
        assert.equal(request.skipStepWrite, false);
        assert.equal(request.writeStepAfterArtifact, false);
        assert.equal(request.force, true);
        return { ok: true, validation: { ok: true } };
      },
    });

    const result = await backend.generateStepArtifact({
      fileRef: "robot/robot.step",
      force: true,
    });

    assert.equal(result.ok, true);
    assert.equal(result.stepPath, stepPath);
  });
});

test("local backend rejects Viewer artifact regeneration for Python metadata STEP files", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(path.join(modelRoot, "generated"), { recursive: true });
    fs.writeFileSync(path.join(modelRoot, "generated", "source.py"), "def gen_step():\n    return None\n");
    const stepPath = path.join(modelRoot, "generated", "part.step");
    writeStepWithSourceMetadata(stepPath, "models/generated/source.py");
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      stepArtifactGenerator: async () => {
        throw new Error("Python metadata sources should not be invoked by Viewer regeneration.");
      },
    });
    const catalog = backend.refreshCatalog();

    await assert.rejects(
      () => backend.generateStepArtifact({
        fileRef: "generated/part.step",
        catalog,
      }),
      /only regenerates GLB artifacts for imported STEP files/
    );
  });
});

test("local backend regenerates copied STEP files when Python metadata source is unavailable", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(path.join(modelRoot, "copied"), { recursive: true });
    const stepPath = path.join(modelRoot, "copied", "robot_arm.step");
    writeStepWithSourceMetadata(stepPath, "models/robots/tom/robot_arm.py");
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      stepArtifactGenerator: async (request) => {
        assert.equal(request.stepPath, stepPath);
        assert.equal(request.sourcePath, "");
        assert.equal(request.skipStepWrite, false);
        return { ok: true, validation: { ok: true } };
      },
    });
    const catalog = backend.refreshCatalog();
    const entry = catalog.entries.find((candidate) => candidate.rootRelativeFile === "copied/robot_arm.step");

    assert.equal(entry.sourceKind, "step");
    assert.equal(entry.artifact.error, "missing_glb");

    const result = await backend.generateStepArtifact({
      fileRef: "copied/robot_arm.step",
      catalog,
    });

    assert.equal(result.ok, true);
    assert.equal(result.stepPath, stepPath);
  });
});

test("local backend generates collisions for STEP files on demand", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    const stepPath = path.join(modelRoot, "part.step");
    const calls = [];
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      collisionGenerator: async (request) => {
        calls.push(request);
        return {
          ok: true,
          result: {
            kind: "cadpy-interference-report",
            summary: { reportedPairCount: 0 },
          },
        };
      },
    });
    const catalog = backend.refreshCatalog();

    const result = await backend.generateCollisions({
      fileRef: "part.step",
      catalog,
      options: { bodyDepth: 2, maxPairs: 50 },
    });

    assert.equal(result.ok, true);
    assert.deepEqual(result.result, {
      kind: "cadpy-interference-report",
      summary: { reportedPairCount: 0 },
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].repoRoot, workspaceRoot);
    assert.equal(calls[0].stepPath, stepPath);
    assert.deepEqual(calls[0].options, { bodyDepth: 2, maxPairs: 50 });
  });
});

test("local backend reports missing Python-backed STEP files dynamically", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(path.join(modelRoot, "box.py"), "def gen_step():\n    return None\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    const status = backend.readStepSourceStatus({ fileRef: "box.step" });

    assert.equal(status.ok, false);
    assert.equal(status.sourceKind, "python");
    assert.equal(status.step.status, "missing");
    assert.equal(status.step.missing, true);
  });
});

test("local backend reports missing STEP GLBs in fast catalog reads", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    fs.writeFileSync(path.join(modelRoot, "part.step"), "ISO-10303-21;\nEND-ISO-10303-21;\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    const catalogEntry = backend.readCatalog().entries[0];
    const status = backend.readStepSourceStatus({ fileRef: "part.step" });

    assert.equal(catalogEntry.file, path.join(modelRoot, "part.step"));
    assert.equal(catalogEntry.rootRelativeFile, "part.step");
    assert.equal(catalogEntry.artifact.error, "missing_glb");
    assert.equal(status.artifact.error, "missing_glb");
    assert.equal(status.artifact.glbPath, path.join(modelRoot, ".part.step.glb"));
  });
});

test("local backend resolves selected output files instead of generated GLB artifacts", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    const stepPath = path.join(modelRoot, "part.step");
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    fs.writeFileSync(path.join(modelRoot, ".part.step.glb"), "glb");
    const openedPaths = [];
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      sourceFileOpener: async (filePath) => {
        openedPaths.push(filePath);
      },
    });
    const catalog = backend.refreshCatalog();

    const access = backend.resolveFileAssetAccess({
      fileRef: "part.step",
      asset: "output",
      catalog,
    });
    const opened = await backend.openFileAsset({
      fileRef: "part.step",
      asset: "output",
      catalog,
    });

    assert.equal(access.asset, "output");
    assert.equal(access.path, stepPath);
    assert.equal(access.filename, "part.step");
    assert.equal(access.contentType, "application/step");
    assert.deepEqual(openedPaths, [stepPath]);
    assert.deepEqual(opened, {
      asset: "output",
      file: stepPath,
      filename: "part.step",
      opened: true,
    });
  });
});

test("local backend resolves generated GLB artifact assets from catalog URLs", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    const stepPath = path.join(modelRoot, "part.step");
    const artifactPath = path.join(modelRoot, ".part.step.glb");
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    fs.writeFileSync(artifactPath, "glb");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });
    const catalog = backend.refreshCatalog();

    const access = backend.resolveFileAssetAccess({
      fileRef: "part.step",
      asset: "artifact",
      catalog,
    });

    assert.equal(access.asset, "artifact");
    assert.equal(access.file, artifactPath);
    assert.equal(access.rootRelativeFile, ".part.step.glb");
    assert.equal(access.path, artifactPath);
    assert.equal(access.filename, ".part.step.glb");
    assert.equal(access.contentType, "model/gltf-binary");
    assert.throws(
      () => backend.resolveFileAssetAccess({
        fileRef: "part.step",
        asset: "analysis",
        catalog,
      }),
      /Unsupported file asset/
    );
  });
});

test("local backend resolves catalog asset URLs when rootDir is absolute", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    const stepPath = path.join(modelRoot, "part.step");
    const artifactPath = path.join(modelRoot, ".part.step.glb");
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    fs.writeFileSync(artifactPath, topologyGlb({
      sourcePath: "models/part.step",
      stepHash: sha256File(stepPath),
    }));
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: modelRoot });
    const catalog = backend.refreshCatalog();
    const resolvedRoot = backend.resolveRoot(modelRoot);

    const artifact = backend.resolveFileAssetAccess({
      fileRef: "part.step",
      asset: "artifact",
      rootDir: modelRoot,
      resolvedRoot,
      catalog,
    });

    assert.equal(artifact.file, artifactPath);
    assert.equal(artifact.path, artifactPath);
  });
});

test("local backend resolves catalog output files whose names begin with two dots", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    const stepPath = path.join(modelRoot, "..part.step");
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });
    const catalog = backend.refreshCatalog();

    const access = backend.resolveFileAssetAccess({
      fileRef: "..part.step",
      asset: "output",
      catalog,
    });

    assert.equal(access.path, stepPath);
    assert.equal(access.file, stepPath);
    assert.equal(access.rootRelativeFile, "..part.step");
    assert.equal(access.filename, "..part.step");
  });
});

test("local backend resolves Python source code separately from output files", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(modelRoot, { recursive: true });
    const stepPath = path.join(modelRoot, "part.step");
    const sourcePath = path.join(modelRoot, "part.py");
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    fs.writeFileSync(sourcePath, "def gen_step():\n    return None\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });
    const catalog = backend.refreshCatalog();

    const output = backend.resolveFileAssetAccess({ fileRef: "part.step", asset: "output", catalog });
    const source = backend.resolveFileAssetAccess({ fileRef: "part.step", asset: "source", catalog });

    assert.equal(output.path, stepPath);
    assert.equal(source.asset, "source");
    assert.equal(source.path, sourcePath);
    assert.equal(source.filename, "part.py");
    assert.equal(source.contentType, "text/plain; charset=utf-8");
  });
});

test("local backend resolves workspace-relative catalog source files", async () => {
  await withTempWorkspace((workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    const robotDir = path.join(modelRoot, "robots");
    fs.mkdirSync(robotDir, { recursive: true });
    const urdfPath = path.join(robotDir, "robot.urdf");
    const sourcePath = path.join(robotDir, "robot_urdf.py");
    fs.writeFileSync(urdfPath, "<robot name=\"sample\" />\n");
    fs.writeFileSync(sourcePath, "def gen_urdf():\n    return {'xml': '<robot name=\"sample\" />'}\n");
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });
    const catalog = {
      schemaVersion: 4,
      entries: [{
        file: "robots/robot.urdf",
        kind: "urdf",
        sourceKind: "python",
        source: { file: "models/robots/robot_urdf.py" },
      }],
    };

    const source = backend.resolveFileAssetAccess({ fileRef: "robots/robot.urdf", asset: "source", catalog });

    assert.equal(source.asset, "source");
    assert.equal(source.path, sourcePath);
    assert.equal(source.filename, "robot_urdf.py");
  });
});

test("local backend file asset access requires a catalog entry inside the active root", async () => {
  await withTempWorkspace((workspaceRoot) => {
    fs.writeFileSync(path.join(workspaceRoot, "secret.step"), "ISO-10303-21;\nEND-ISO-10303-21;\n");
    fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    assert.throws(
      () => backend.resolveFileAssetAccess({
        fileRef: "../secret.step",
        asset: "output",
        catalog: { schemaVersion: 4, entries: [{ file: "../secret.step" }] },
      }),
      /outside the active CAD Viewer root|Output file not found/
    );
    assert.throws(
      () => backend.resolveFileAssetAccess({
        fileRef: "part.step",
        asset: "output",
        catalog: { schemaVersion: 4, entries: [] },
      }),
      /CAD catalog entry not found/
    );
  });
});

test("local backend refuses same-stem Python artifacts instead of catalog source metadata", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    const modelRoot = path.join(workspaceRoot, "models");
    fs.mkdirSync(path.join(modelRoot, "robot"), { recursive: true });
    fs.writeFileSync(path.join(modelRoot, "robot", "__init__.py"), "\n");
    const generatorPath = path.join(modelRoot, "robot", "robot.py");
    fs.writeFileSync(generatorPath, "def gen_step():\n    return None\n");
    const stepPath = path.join(modelRoot, "robot", "robot.step");
    fs.writeFileSync(stepPath, "ISO-10303-21;\nEND-ISO-10303-21;\n");
    const backend = createLocalAssetBackend({
      workspaceRoot,
      rootDir: "models",
      stepArtifactGenerator: async () => {
        throw new Error("Python generators should not be invoked by Viewer regeneration.");
      },
    });

    await assert.rejects(
      () => backend.generateStepArtifact({
        fileRef: "robot/robot.step",
      }),
      /only regenerates GLB artifacts for imported STEP files/
    );
  });
});

test("local backend writes only served CAD assets inside the active root", async () => {
  await withTempWorkspace(async (workspaceRoot) => {
    fs.mkdirSync(path.join(workspaceRoot, "models"), { recursive: true });
    const backend = createLocalAssetBackend({ workspaceRoot, rootDir: "models" });

    const written = await backend.writeAsset({ fileRef: ".box.step.glb", body: Buffer.from("glb") });
    const writtenDotDotPrefix = await backend.writeAsset({ fileRef: "..box.step.glb", body: Buffer.from("glb") });

    assert.equal(written.bytes, 3);
    assert.equal(writtenDotDotPrefix.bytes, 3);
    assert.equal(fs.readFileSync(path.join(workspaceRoot, "models", ".box.step.glb"), "utf8"), "glb");
    assert.equal(fs.readFileSync(path.join(workspaceRoot, "models", "..box.step.glb"), "utf8"), "glb");
    await assert.rejects(
      () => backend.writeAsset({ fileRef: "../escape.glb", body: Buffer.from("bad") }),
      /inside the active CAD Viewer root/
    );
  });
});
