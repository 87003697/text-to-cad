import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { scanCadDirectory } from "../catalog/cadDirectoryScanner.mjs";
import {
  ensureStepArtifactsForCatalog,
  ensureStepTopologyArtifact,
  renderPackageDir,
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

function writePythonAssemblyGenerator(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  // Two placed parts -> two occurrences of two distinct components in
  // the cadgen package. The compiler must expose ALL of them; picking
  // one leaf silently would drop the second part.
  fs.writeFileSync(filePath, [
    "from build123d import Box, Cylinder, Compound, Location",
    "",
    "def gen_step():",
    "    box = Box(1, 1, 1)",
    "    cyl = Cylinder(0.4, 1)",
    "    cyl.location = Location((2, 0, 0))",
    "    return Compound(children=[box, cyl])",
    "",
    "gen_step.entry_kind = \"assembly\"",
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

function readAssemblyDescriptor(packageDir) {
  return JSON.parse(fs.readFileSync(path.join(packageDir, "assembly.json"), "utf8"));
}

test("ensureStepArtifactsForCatalog discovers Python generators and publishes the whole package", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/block.step");
  const generatorPath = path.join(repoRoot, "workspace/generated/block.py");
  writePythonBoxGenerator(generatorPath);

  const results = await ensureStepArtifactsForCatalog({ repoRoot, rootDir: "workspace" });

  assert.equal(results.length, 1);
  assert.equal(results[0].ok, true, JSON.stringify(results[0]));
  assert.equal(results[0].sourceKind, "python");
  assert.equal(fs.existsSync(stepPath), true, "Python package publication must also write its bound STEP");

  // The published render artifact is the cadgen render-package
  // directory (identity + components) -- NOT a first-component leaf.
  const packageDir = renderPackageDir(generatorPath);
  assert.equal(results[0].packageDir, packageDir);
  const descriptor = readAssemblyDescriptor(packageDir);
  assert.equal(descriptor.sourceKind, "python");
  assert.equal(descriptor.sourcePath, "block.py");
  assert.equal(descriptor.stepPath, "block.step");

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  assert.equal(catalog.entries.length, 1);
  assert.equal(catalog.entries[0].artifact, undefined);
  // The catalog URL identifies the package directory (matching
  // ``viewer/server_py/scanner.py::create_step_entry`` and the client's
  // ``useCadAssets.js``), not any single component GLB.
  assert.ok(
    catalog.entries[0].url.includes("__cadgen__/models/block.py"),
    `expected package-scoped URL, got ${catalog.entries[0].url}`,
  );
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
    force: true,
  });

  assert.equal(result.ok, true);
  assert.equal(fs.existsSync(stepPath), true, "Python package publication must also write its bound STEP");
  assert.equal(result.validation.ok, true);
  assert.equal(result.validation.sourceKind, "python");
  assert.equal(result.validation.sourcePath, "../sources/assembly.py");

  const packageDir = renderPackageDir(generatorPath);
  const descriptor = readAssemblyDescriptor(packageDir);
  assert.equal(descriptor.sourceKind, "python");
  assert.equal(descriptor.sourcePath, "../sources/assembly.py");
  // ``stepPath`` is now recorded RELATIVE to the generator source's
  // directory (see ``_assembly_provenance_manifest`` in cadgen), so a
  // non-same-stem mapping preserves the STEP output location. That is
  // what lets the scanner uniquely recover
  // ``sources/assembly.py -> generated/robot.step`` from the
  // descriptor alone.
  assert.equal(descriptor.stepPath, "../generated/robot.step");

  const narrowedCatalog = scanCadDirectory({ repoRoot, rootDir: "workspace/generated" });
  const narrowedEntry = narrowedCatalog.entries.find((entry) => entry.file === "robot.step");
  assert.ok(narrowedEntry, "narrowed root must still surface the STEP itself");
  assert.equal(
    String(narrowedEntry.url || "").includes("__cadgen__/models"),
    false,
    "narrowed root must not advertise a sibling generator package it cannot serve",
  );
});

test("ensureStepArtifactsForCatalog preserves and deduplicates a descriptor-bound non-same-stem target", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/assembly.py");
  const fabricatedSameStem = path.join(repoRoot, "workspace/sources/assembly.step");
  writePythonBoxGenerator(generatorPath);

  const initial = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
  });
  assert.equal(initial.ok, true, JSON.stringify(initial));

  const results = await ensureStepArtifactsForCatalog({
    repoRoot,
    rootDir: "workspace",
    force: true,
  });
  assert.equal(results.length, 1, "the physical STEP and its bound generator are one logical output");
  assert.equal(results[0].stepPath, stepPath);
  assert.equal(results[0].ok, true, JSON.stringify(results[0]));
  assert.equal(fs.existsSync(fabricatedSameStem), false, "bulk discovery must not invent assembly.step");
  assert.equal(
    readAssemblyDescriptor(renderPackageDir(generatorPath)).stepPath,
    "../generated/robot.step",
    "bulk rebuilding must not overwrite the descriptor's authoritative target",
  );
});

test("ensureStepTopologyArtifact evaluates the generator exactly once for STEP + package", stepArtifactTestOptions, async (t) => {
  // Red-capable: the generator writes a durable counter file on every
  // invocation. A single ``--step-export`` in cadgen (the one-pass
  // seam) drives ``_generate_part_outputs`` to run ``gen_step`` once
  // and emit BOTH the STEP file and the render package from that one
  // scene. A previous two-process implementation
  // (``cadgen.step_artifact_cli`` then ``cadgen.step_export_target``)
  // would tick the counter twice; this assertion fails in that case.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/robot.py");
  const counterPath = path.join(repoRoot, "gen_step_calls.count");
  fs.mkdirSync(path.dirname(generatorPath), { recursive: true });
  fs.writeFileSync(generatorPath, [
    "from build123d import Box",
    // Durable, cross-process evaluation counter: read the existing
    // count from disk, increment, write back, and use it as the box
    // width. Two evaluations would advance the count AND produce a
    // different geometry, so both the counter file and the STEP
    // digest would drift.
    "import os",
    `_COUNTER = ${JSON.stringify(counterPath)}`,
    "def _tick():",
    "    prior = 0",
    "    try:",
    "        with open(_COUNTER, 'r', encoding='utf-8') as fh:",
    "            prior = int(fh.read().strip() or '0')",
    "    except FileNotFoundError:",
    "        prior = 0",
    "    with open(_COUNTER, 'w', encoding='utf-8') as fh:",
    "        fh.write(str(prior + 1))",
    "    return prior + 1",
    "def gen_step():",
    "    count = _tick()",
    "    return Box(1 + count * 0.05, 1, 1)",
    "",
  ].join("\n"));

  const result = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });

  assert.equal(result.ok, true);
  assert.equal(result.stepWrite?.status, "complete");
  assert.equal(fs.existsSync(stepPath), true);
  // *** Red-capable proof of single evaluation *** -- a second
  // subprocess-based re-evaluation would set the counter to 2.
  const evaluationCount = Number.parseInt(fs.readFileSync(counterPath, "utf8").trim(), 10);
  assert.equal(
    evaluationCount,
    1,
    `gen_step must run exactly once for STEP + package; observed ${evaluationCount} evaluation(s)`,
  );
  // Descriptor stepHash + on-disk STEP hash both come from the same
  // scene write, so they agree.
  const packageDir = renderPackageDir(generatorPath);
  const descriptor = readAssemblyDescriptor(packageDir);
  const cryptoModule = await import("node:crypto");
  const onDiskStepHash = cryptoModule.createHash("sha256").update(fs.readFileSync(stepPath)).digest("hex");
  assert.equal(
    descriptor.stepHash,
    onDiskStepHash,
    "descriptor stepHash must equal on-disk STEP hash",
  );
});

test("ensureStepTopologyArtifact publishes all occurrences of a multi-part assembly", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/assemblies/rig.step");
  const generatorPath = path.join(repoRoot, "workspace/assemblies/rig.py");
  writePythonAssemblyGenerator(generatorPath);

  const result = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
  });
  assert.equal(result.ok, true);
  // Multi-component: the pre-fix "return the first component" behaviour
  // dropped the second part. cadgen's package descriptor records ALL
  // components + their occurrence transforms; the compiler surface
  // must forward that whole set so the viewer client
  // (``useCadAssets.js``) can compose the assembly.
  assert.ok(
    Array.isArray(result.components) && result.components.length >= 2,
    `assembly package must expose >= 2 components (got ${result.components?.length})`,
  );
  const packageDir = renderPackageDir(generatorPath);
  const descriptor = readAssemblyDescriptor(packageDir);
  const occurrences = Array.isArray(descriptor.occurrences) ? descriptor.occurrences : [];
  assert.ok(
    occurrences.length >= 2,
    `descriptor must list every occurrence (got ${occurrences.length})`,
  );
  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  assert.equal(catalog.entries.length, 1);
  // The URL still points at the package directory so the client can
  // fetch ``assembly.json`` and every ``components/<hash>.glb``.
  assert.ok(
    catalog.entries[0].url.includes("__cadgen__/models/rig.py"),
    `expected package URL, got ${catalog.entries[0].url}`,
  );
});

test("scanner marks the package stale when the primary generator source is edited without a rebuild", stepArtifactTestOptions, async (t) => {
  // Red-capable freshness regression: build once, then edit the
  // ``.py`` generator without rebuilding. ``sourceHash`` in the
  // descriptor no longer matches ``sha256(source file)``, so the
  // scanner MUST report ``stale_step_artifact`` and refuse to accept
  // the current package. Fail-open (previous behaviour comparing only
  // when the descriptor field was truthy, and only against the STEP
  // file) would return ``ok`` here.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/generated/robot.py");
  writePythonBoxGenerator(generatorPath);

  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });

  // Edit the generator so its bytes -- and therefore its plain sha256
  // sourceHash -- change. The .py generator file's sourceHash is what
  // cadgen records in the descriptor; the scanner recomputes and
  // demands a match.
  fs.appendFileSync(generatorPath, "\n# post-build edit invalidating sourceHash\n");

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const entry = catalog.entries[0];
  assert.ok(entry, "expected one catalog entry");
  const status = entry.artifact;
  assert.ok(status && !status.ok, "expected artifact status not-ok after source edit");
  assert.equal(
    status.error,
    "stale_step_artifact",
    `expected stale_step_artifact after source edit, got ${status.error} (${status.message})`,
  );
});


test("scanner marks the package stale when an imported STEP file is edited without a rebuild", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/imported/model.step");
  fs.mkdirSync(path.dirname(stepPath), { recursive: true });
  // Build the package from a Python generator, then mutate the STEP
  // file cadgen wrote. The descriptor's ``stepHash`` records the
  // pristine byte hash, so a byte-level mutation invalidates it and
  // the scanner must refuse to accept the stale package.
  const generatorPath = path.join(repoRoot, "workspace/imported/model.py");
  writePythonBoxGenerator(generatorPath);
  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  fs.appendFileSync(stepPath, "\n/* tampered */\n");

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const entry = catalog.entries[0];
  assert.ok(entry, "expected one catalog entry");
  const status = entry.artifact;
  assert.ok(status && !status.ok, "expected artifact status not-ok after STEP edit");
  assert.equal(
    status.error,
    "stale_step_artifact",
    `expected stale_step_artifact after STEP edit, got ${status.error} (${status.message})`,
  );
});

test("existing imported STEP ignores an unrelated same-stem Python generator", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/imported/widget.step");
  const generatorPath = path.join(repoRoot, "workspace/imported/widget.py");
  const markerPath = path.join(repoRoot, "unrelated-generator-ran");
  fs.mkdirSync(path.dirname(stepPath), { recursive: true });
  const exported = spawnSync(
    cadPythonExecutable(process.cwd()),
    ["-c", [
      "from build123d import Box, export_step",
      `export_step(Box(1, 1, 1), ${JSON.stringify(stepPath)})`,
    ].join("\n")],
    { cwd: process.cwd(), env: cadPythonEnv(), encoding: "utf8" },
  );
  assert.equal(exported.status, 0, exported.stderr || exported.stdout);
  fs.writeFileSync(generatorPath, [
    "from build123d import Box",
    "from pathlib import Path",
    `Path(${JSON.stringify(markerPath)}).write_text('ran')`,
    "def gen_step():",
    "    return Box(9, 9, 9)",
    "",
  ].join("\n"));
  const originalStep = fs.readFileSync(stepPath);

  const result = await ensureStepTopologyArtifact({ repoRoot, stepPath, force: true });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.sourceKind, "step");
  assert.equal(fs.existsSync(markerPath), false, "unbound same-stem Python must never execute");
  assert.deepEqual(fs.readFileSync(stepPath), originalStep, "imported STEP bytes must not be overwritten");
});

test("bulk compilation keeps three-way logical STEP ambiguity fail-closed", async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const workspace = path.join(repoRoot, "workspace");
  for (const name of ["a", "b", "c"]) {
    const generatorPath = path.join(workspace, name, "gen.py");
    writePythonBoxGenerator(generatorPath);
    const packageDir = renderPackageDir(generatorPath);
    fs.mkdirSync(packageDir, { recursive: true });
    fs.writeFileSync(path.join(packageDir, "assembly.json"), JSON.stringify({
      sourceKind: "python",
      sourcePath: `../${name}/gen.py`,
      stepPath: "../shared/robot.step",
    }));
  }

  const results = await ensureStepArtifactsForCatalog({ repoRoot, rootDir: "workspace", force: true });
  assert.equal(results.length, 1);
  assert.equal(results[0].ok, false);
  assert.match(results[0].error, /Multiple Python generators claim/);
  assert.equal(fs.existsSync(path.join(workspace, "shared", "robot.step")), false);
});

test("bulk compilation ignores descriptor targets outside the selected rootDir", async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const generatorPath = path.join(repoRoot, "workspace", "gen.py");
  writePythonBoxGenerator(generatorPath);
  const packageDir = renderPackageDir(generatorPath);
  fs.mkdirSync(packageDir, { recursive: true });
  fs.writeFileSync(path.join(packageDir, "assembly.json"), JSON.stringify({
    sourceKind: "python",
    sourcePath: "../workspace/gen.py",
    stepPath: "../other/model.step",
  }));

  const results = await ensureStepArtifactsForCatalog({ repoRoot, rootDir: "workspace", force: true });
  assert.deepEqual(results, []);
  assert.equal(fs.existsSync(path.join(repoRoot, "other", "model.step")), false);
});


test("scanner rejects a package whose directory is a symbolic link", stepArtifactTestOptions, async (t) => {
  // Red-capable: build a valid package, then replace the canonical
  // package directory with a symlink pointing at a fabricated
  // ``__cadgen__/models/<entry>`` under a different tree. Lexical
  // containment would pass; only realpath rejects it. Fail-open would
  // serve arbitrary geometry from the symlink target as if it were
  // the entry's render package.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/block.step");
  const generatorPath = path.join(repoRoot, "workspace/generated/block.py");
  writePythonBoxGenerator(generatorPath);
  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });

  const packageDir = renderPackageDir(generatorPath);
  // Move the real package aside and drop a symlink in its place
  // pointing at unrelated content.
  const attackerPackage = fs.mkdtempSync(path.join(os.tmpdir(), "cad-attacker-"));
  t.after(() => fs.rmSync(attackerPackage, { recursive: true, force: true }));
  fs.writeFileSync(
    path.join(attackerPackage, "assembly.json"),
    JSON.stringify({ components: {}, sourceKind: "python" }),
  );
  fs.rmSync(packageDir, { recursive: true, force: true });
  try {
    fs.symlinkSync(attackerPackage, packageDir, "dir");
  } catch (error) {
    t.skip(`cannot create a directory symlink on this host: ${error?.message || error}`);
    return;
  }

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const entry = catalog.entries[0];
  assert.ok(entry, "expected one catalog entry");
  const status = entry.artifact;
  assert.ok(
    status && !status.ok,
    `symlinked package directory must be rejected; got status ${JSON.stringify(status)}`,
  );
  assert.match(
    status.message || "",
    /symbolic link|realpath|canonical/i,
    `expected symlink/realpath/canonical rejection, got: ${status.message}`,
  );
});


test("scanner rejects a component GLB that is a symbolic link out of the package", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/box.step");
  const generatorPath = path.join(repoRoot, "workspace/generated/box.py");
  writePythonBoxGenerator(generatorPath);
  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  const packageDir = renderPackageDir(generatorPath);
  const descriptorPath = path.join(packageDir, "assembly.json");
  const descriptor = JSON.parse(fs.readFileSync(descriptorPath, "utf8"));
  const [componentId, entry] = Object.entries(descriptor.components)[0];
  const componentPath = path.join(packageDir, entry.glb);

  // Replace the real component GLB with a symlink pointing outside
  // the package. Lexical resolution against ``packageDir`` looks
  // fine; realpath returns the outside target and the scanner must
  // reject it.
  const outside = path.join(repoRoot, "outside.glb");
  fs.writeFileSync(outside, fs.readFileSync(componentPath));
  fs.rmSync(componentPath);
  try {
    fs.symlinkSync(outside, componentPath, "file");
  } catch (error) {
    t.skip(`cannot create a file symlink on this host: ${error?.message || error}`);
    return;
  }

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const artifactStatus = catalog.entries[0].artifact;
  assert.ok(
    artifactStatus && !artifactStatus.ok,
    `symlinked component GLB must be rejected; got ${JSON.stringify(artifactStatus)}`,
  );
  assert.match(
    artifactStatus.message || "",
    new RegExp(`symbolic link|outside package|Component ${componentId}`, "i"),
    `expected symlink or containment rejection, got: ${artifactStatus.message}`,
  );
});


test("catalog surfaces the explicit non-same-stem generator after the STEP is written", stepArtifactTestOptions, async (t) => {
  // Red-capable: an explicit ``sources/assembly.py -> generated/robot.step``
  // mapping cannot be discovered by filename inference. Previously,
  // ``createStepEntry`` looked for ``generated/robot.py`` (same
  // directory + same stem) and reported the entry as missing/stale.
  // The STEP file records its true generator via
  // ``cadgen:sourcePath`` metadata; the scanner reads that to find
  // the canonical package at ``sources/__cadgen__/models/assembly.py``.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/assembly.py");
  writePythonBoxGenerator(generatorPath);

  const result = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  assert.equal(result.ok, true);
  assert.equal(fs.existsSync(stepPath), true);

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const stepEntry = catalog.entries.find((entry) => entry.file === "generated/robot.step");
  assert.ok(stepEntry, `catalog must surface generated/robot.step, got ${JSON.stringify(catalog.entries.map((e) => e.file))}`);
  // Per ``catalogArtifactFromValidation``, an OK artifact is reported
  // as ``undefined``; only a *failing* validation attaches an error
  // object. A pre-fix scanner that looked at ``generated/robot.py``
  // (same-stem inference) would find no package and attach
  // ``{ ok: false, error: "missing_glb" }``.
  assert.equal(
    stepEntry.artifact,
    undefined,
    `explicit non-same-stem STEP must be reported current, got ${JSON.stringify(stepEntry.artifact)}`,
  );
  // The URL must point at the package cadgen actually wrote, which is
  // keyed by the *generator* filename ``assembly.py`` under
  // ``sources/__cadgen__/models/``.
  assert.ok(
    stepEntry.url.includes("sources/__cadgen__/models/assembly.py"),
    `expected package URL keyed by assembly.py, got ${stepEntry.url}`,
  );
});


test("compiler rejects a forged package outside the canonical location", stepArtifactTestOptions, async (t) => {
  // Sanity: even if the CLI were spoofed to report a package path
  // outside ``__cadgen__/models/<entry>``, the compiler must refuse to
  // adopt it. The narrower unit test in
  // ``pythonStepArtifact.test.mjs::validateCadgenPackagePath rejects a
  // package outside the canonical location`` runs the same check
  // directly. Kept here as a viewer-facing integration guard.
  const { validateCadgenPackagePath } = await import("./pythonStepArtifact.mjs");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-forged-package-"));
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  const entry = path.join(tmp, "block.py");
  fs.writeFileSync(entry, "def gen_step(): ...\n");
  assert.throws(
    () => validateCadgenPackagePath(tmp, entry, path.join(tmp, "not_canonical")),
    /canonical location/,
  );
});


test("ensureStepTopologyArtifact routes closure detection through cadgen when a helper module is edited", stepArtifactTestOptions, async (t) => {
  // Red-capable: the JS scanner cannot reproduce cadgen's semantic
  // AST closure hash (see
  // ``packages/cadgen/src/cadgen/_internal/source_hash.py``); a
  // previous iteration short-circuited freshness with plain sha256
  // and accepted a stale package after a helper module edit. The
  // fix: for python packages that record ``sourceClosureFiles``,
  // ``ensureStepTopologyArtifact`` MUST invoke cadgen (which runs
  // ``_generated_assembly_glb_closure_current`` under
  // ``_current_artifact_for_spec``). That's what refreshes the
  // descriptor's ``sourceClosureHash``. This test edits a helper
  // module WITHOUT touching the primary generator, then calls
  // ``ensureStepTopologyArtifact(force=false)`` -- if the JS
  // short-circuit is reintroduced the closure hash stays put and
  // the assertion below fails.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/box.step");
  const generatorPath = path.join(repoRoot, "workspace/generated/box.py");
  const helperPath = path.join(repoRoot, "workspace/generated/box_helper.py");
  fs.mkdirSync(path.dirname(generatorPath), { recursive: true });
  fs.writeFileSync(helperPath, "SIZE = 1.0\n");
  fs.writeFileSync(generatorPath, [
    "from build123d import Box",
    "from box_helper import SIZE",
    "def gen_step():",
    "    return Box(SIZE, 1, 1)",
    "",
  ].join("\n"));

  const first = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  assert.equal(first.ok, true);
  const packageDir = renderPackageDir(generatorPath);
  const beforeDescriptor = readAssemblyDescriptor(packageDir);
  const beforeClosureHash = String(beforeDescriptor.sourceClosureHash || "");
  const beforeClosureFiles = beforeDescriptor.sourceClosureFiles || [];
  assert.ok(
    beforeClosureFiles.includes("box_helper.py") || beforeClosureFiles.includes("box.py"),
    `descriptor must include closure files, got ${JSON.stringify(beforeClosureFiles)}`,
  );
  assert.ok(beforeClosureHash.length >= 32, "descriptor must record a closure hash");

  // Edit the helper without touching the primary generator.
  fs.writeFileSync(helperPath, "SIZE = 2.5\n");

  const staleCatalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const staleEntry = staleCatalog.entries.find((entry) => entry.file === "generated/box.step");
  assert.ok(staleEntry, "catalog must retain the logical STEP after a helper edit");
  assert.equal(
    staleEntry.artifact?.error,
    "stale_source_closure",
    `catalog must fail closed before publishing a stale helper-dependent package; got ${JSON.stringify(staleEntry)}`,
  );
  assert.ok(
    !String(staleEntry.url || "").includes("__cadgen__/models"),
    `catalog must not publish the stale package URL; got ${staleEntry.url}`,
  );

  const second = await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    // NB: no force -- the whole point is that the normal path
    // detects the closure change via cadgen's authoritative gate.
    writeStepAfterArtifact: true,
  });
  assert.equal(second.ok, true, `second call must succeed; got ${JSON.stringify(second.validation || second)}`);
  assert.notEqual(
    second.skipped,
    true,
    "must not short-circuit skip after a helper module edit",
  );
  const afterDescriptor = readAssemblyDescriptor(packageDir);
  assert.notEqual(
    String(afterDescriptor.sourceClosureHash || ""),
    beforeClosureHash,
    "descriptor sourceClosureHash must refresh after helper edit; cadgen was not invoked",
  );
});


test("catalog exposes explicit non-same-stem STEP from the descriptor alone (no STEP on disk)", stepArtifactTestOptions, async (t) => {
  // Reviewer regression: build ``sources/assembly.py -> generated/robot.step``,
  // DELETE the STEP file, then scan. The scanner must discover the
  // logical STEP via cadgen's descriptor mapping (``stepPath`` is now
  // recorded relative to the generator's directory, so
  // ``../generated/robot.step`` is unambiguous), NOT invent
  // ``sources/assembly.step``.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/assembly.py");
  writePythonBoxGenerator(generatorPath);
  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  // Remove the STEP file so this is a strictly package-only test.
  fs.rmSync(stepPath);
  assert.equal(fs.existsSync(stepPath), false);

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const files = catalog.entries.map((entry) => entry.file);
  assert.ok(
    files.includes("generated/robot.step"),
    `catalog must expose generated/robot.step, got ${JSON.stringify(files)}`,
  );
  assert.ok(
    !files.includes("sources/assembly.step"),
    `catalog must NOT invent sources/assembly.step, got ${JSON.stringify(files)}`,
  );
  const stepEntry = catalog.entries.find((entry) => entry.file === "generated/robot.step");
  assert.ok(
    stepEntry.url.includes("sources/__cadgen__/models/assembly.py"),
    `expected package URL keyed by assembly.py, got ${stepEntry.url}`,
  );
});


test("catalog rejects a descriptor-only Python package with missing or malformed stepHash", stepArtifactTestOptions, async (t) => {
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/assembly.py");
  writePythonBoxGenerator(generatorPath);
  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  const packageDir = renderPackageDir(generatorPath);
  const descriptorPath = path.join(packageDir, "assembly.json");
  const descriptor = readAssemblyDescriptor(packageDir);
  delete descriptor.stepHash;
  fs.writeFileSync(descriptorPath, JSON.stringify(descriptor));
  fs.rmSync(stepPath);

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const entry = catalog.entries.find((candidate) => candidate.file === "generated/robot.step");
  assert.ok(entry, "descriptor-only discovery must retain the logical STEP mapping");
  assert.equal(entry.artifact?.error, "missing_step_hash");
  assert.ok(
    !String(entry.url || "").includes("__cadgen__/models"),
    `an unbound package must not be published as an asset; got ${entry.url}`,
  );

  descriptor.stepHash = "not-a-sha256";
  fs.writeFileSync(descriptorPath, JSON.stringify(descriptor));
  const malformedCatalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const malformedEntry = malformedCatalog.entries.find((candidate) => candidate.file === "generated/robot.step");
  assert.equal(malformedEntry?.artifact?.error, "missing_step_hash");
  assert.ok(!String(malformedEntry?.url || "").includes("__cadgen__/models"));

  descriptor.stepHash = "0".repeat(64);
  descriptor.packageSchemaVersion = 2;
  fs.writeFileSync(descriptorPath, JSON.stringify(descriptor));
  const { validateStepTopologyArtifact } = await import("../catalog/cadDirectoryScanner.mjs");
  const oldSchemaValidation = validateStepTopologyArtifact({
    repoRoot,
    sourcePath: stepPath,
    entryPath: generatorPath,
  });
  assert.equal(oldSchemaValidation.stepArtifact?.error?.code, "unsupported_package_schema");
  const oldSchemaCatalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const oldSchemaEntry = oldSchemaCatalog.entries.find((candidate) => candidate.file === "generated/robot.step");
  assert.ok(oldSchemaEntry?.artifact && !oldSchemaEntry.artifact.ok);
  assert.ok(!String(oldSchemaEntry?.url || "").includes("__cadgen__/models"));
});


test("STEP metadata sourcePath cannot escape the repo root", stepArtifactTestOptions, async (t) => {
  // A hostile STEP file can name ``cadgen:sourcePath = /etc/passwd``
  // or ``../../attacker.py``. The scanner/compiler must ignore
  // attacker-controlled metadata that escapes the trusted repo root
  // and fall back to same-stem inference.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const dir = path.join(repoRoot, "workspace/generated");
  fs.mkdirSync(dir, { recursive: true });
  const stepPath = path.join(dir, "victim.step");
  // Absolute POSIX path in metadata -- refuse.
  fs.writeFileSync(stepPath, [
    "ISO-10303-21;",
    "DATA;",
    "#1=DESCRIPTIVE_REPRESENTATION_ITEM('cadgen:sourcePath','/etc/passwd');",
    "#2=REPRESENTATION('cadgen:sourcePath',(#1),#9);",
    "#3=PROPERTY_DEFINITION('cadgen metadata','cadgen:sourcePath',#10);",
    "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
    "ENDSEC;",
    "END-ISO-10303-21;",
    "",
  ].join("\n"));

  const { renderPackageDir: rpd } = await import("./pythonStepArtifact.mjs");
  const cryptoModule = await import("node:crypto");
  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const entry = catalog.entries.find((e) => e.file === "generated/victim.step");
  assert.ok(entry, `catalog must surface the STEP even when metadata is hostile, got ${JSON.stringify(catalog.entries.map((e) => e.file))}`);
  // The scanner must have IGNORED the ``/etc/passwd`` metadata and
  // fallen back to same-stem or stepPath itself. It must NOT bind
  // the entry to ``/etc/passwd`` (which would then be surfaced as
  // the entry's canonical source).
  const source = entry.source || {};
  assert.notEqual(String(source.file || ""), "/etc/passwd");
  assert.notEqual(String(source.sourcePath || ""), "/etc/passwd");
  // Traversal escaping the repo root: same refusal.
  const traversalStep = path.join(dir, "traversal.step");
  fs.writeFileSync(traversalStep, [
    "ISO-10303-21;",
    "DATA;",
    "#1=DESCRIPTIVE_REPRESENTATION_ITEM('cadgen:sourcePath','../../../outside.py');",
    "#2=REPRESENTATION('cadgen:sourcePath',(#1),#9);",
    "#3=PROPERTY_DEFINITION('cadgen metadata','cadgen:sourcePath',#10);",
    "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
    "ENDSEC;",
    "END-ISO-10303-21;",
    "",
  ].join("\n"));
  const catalog2 = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const entry2 = catalog2.entries.find((e) => e.file === "generated/traversal.step");
  const source2 = entry2?.source || {};
  assert.equal(source2.file || "", "");
  // Suppress unused var lint.
  void rpd;
  void cryptoModule;
});


test("scanner rejects a package whose __cadgen__ ancestor is a symlink to outside the repo", stepArtifactTestOptions, async (t) => {
  // Ancestor reparse escape: the immediate parent of the package
  // directory (``__cadgen__/models``) is the natural target for a
  // reparse point that reroutes render output to an attacker-
  // controlled tree while the ``__cadgen__/models/<entry>`` name
  // looks canonical. Comparing only against the immediate parent's
  // realpath (as an earlier iteration did) passes such an escape.
  // Comparing the FULL realpath against the repo's realpath refuses
  // it.
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const generatorDir = path.join(repoRoot, "workspace/generated");
  fs.mkdirSync(generatorDir, { recursive: true });
  const stepPath = path.join(generatorDir, "gadget.step");
  const generatorPath = path.join(generatorDir, "gadget.py");
  writePythonBoxGenerator(generatorPath);
  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  const canonicalPackage = renderPackageDir(generatorPath);
  // Move the whole ``__cadgen__`` tree outside the repo and replace
  // its location inside the repo with a symlink to that outside
  // tree. The rest of the layout looks pristine.
  const outsideRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cad-outside-"));
  t.after(() => fs.rmSync(outsideRoot, { recursive: true, force: true }));
  const originalCadgen = path.join(generatorDir, "__cadgen__");
  const outsideCadgen = path.join(outsideRoot, "__cadgen__");
  fs.renameSync(originalCadgen, outsideCadgen);
  try {
    fs.symlinkSync(outsideCadgen, originalCadgen, "dir");
  } catch (error) {
    t.skip(`cannot create directory symlinks on this host: ${error?.message || error}`);
    return;
  }
  // Sanity: the "canonical" package path still exists on disk via
  // the symlink, but its realpath is outside the repo. The scanner
  // must refuse the package rather than serve it.
  assert.ok(fs.existsSync(path.join(canonicalPackage, "assembly.json")));

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const entry = catalog.entries.find((e) => e.file === "generated/gadget.step");
  assert.ok(entry, "catalog must still surface the STEP entry");
  const status = entry.artifact;
  assert.ok(
    status && !status.ok,
    `symlinked __cadgen__ ancestor must be rejected; got ${JSON.stringify(status)}`,
  );
  assert.match(
    status.message || "",
    /symbolic link|reroute|outside/i,
    `expected ancestor-symlink rejection, got ${status.message}`,
  );
});


test("scanner emits exactly one deterministic ambiguous entry for a shared descriptor-only STEP claimed by two python packages", async (t) => {
  // Reviewer regression: two python packages -- each with a valid
  // descriptor -- pointing at the SAME missing logical STEP made
  // ``collectCadSourceFiles`` push the logical path TWICE. The map
  // downstream produced two identical catalog rows with the same
  // ``file`` field, both marked ``ambiguous_package_binding``. Catalog
  // file identity must be unique: deduplicate before ``createStepEntry``
  // and emit exactly one fail-closed diagnostic per logical STEP.
  const cryptoModule = await import("node:crypto");
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cad-viewer-step-compile-"));
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));

  const makePackage = (subdir, targetRel, sourcePathRel) => {
    const generatorPath = path.join(repoRoot, "workspace", subdir, "gen.py");
    fs.mkdirSync(path.dirname(generatorPath), { recursive: true });
    fs.writeFileSync(generatorPath, "def gen_step():\n    return None\n");
    const packageDir = path.join(repoRoot, "workspace", subdir, "__cadgen__", "models", "gen.py");
    fs.mkdirSync(path.join(packageDir, "components"), { recursive: true });
    fs.writeFileSync(path.join(packageDir, "components", "c.glb"), Buffer.from("glTFbytes"));
    const hash = cryptoModule.createHash("sha256")
      .update(fs.readFileSync(generatorPath))
      .digest("hex");
    // ``sourcePath`` is written relative to the STEP file's directory
    // (cadgen's convention -- see ``_assembly_provenance_manifest``).
    fs.writeFileSync(path.join(packageDir, "assembly.json"), JSON.stringify({
      schemaVersion: 4,
      packageSchemaVersion: 3,
      sourceKind: "python",
      sourcePath: sourcePathRel,
      sourceHash: hash,
      sourceClosureHash: "semantic-fixture",
      sourceClosureFiles: [sourcePathRel],
      sourceClosureByteHashes: { [sourcePathRel]: hash },
      stepPath: targetRel,
      stepHash: "0".repeat(64),
      components: { c: { glb: "components/c.glb", contentHash: "abc" } },
    }));
  };
  // Two generators in sibling subdirectories that both claim the
  // same shared logical STEP at ``workspace/shared/robot.step``.
  fs.mkdirSync(path.join(repoRoot, "workspace", "shared"), { recursive: true });
  makePackage("packageA", "../shared/robot.step", "../packageA/gen.py");
  makePackage("packageB", "../shared/robot.step", "../packageB/gen.py");

  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const matching = catalog.entries.filter((e) => e.file === "shared/robot.step");
  assert.equal(
    matching.length,
    1,
    `catalog must dedupe ambiguous logical STEP entries, got ${matching.length}: ${JSON.stringify(matching.map((m) => m.file))}`,
  );
  assert.equal(
    matching[0].artifact?.error,
    "ambiguous_package_binding",
    `expected ambiguous_package_binding diagnostic, got ${JSON.stringify(matching[0].artifact)}`,
  );
  // No package accepted -- the entry MUST NOT carry a package-scoped
  // URL for either candidate.
  const url = String(matching[0].url || "");
  assert.ok(
    !url.includes("packageA") && !url.includes("packageB"),
    `no package must be selected for an ambiguous mapping, got url=${url}`,
  );
});


test("validator rejects a forged same-stem python package with no sourcePath/sourceHash/closure", async (t) => {
  // Reviewer regression: ``validateStepTopologyArtifact`` used to run
  // its own copy of package acceptance logic, so a python descriptor
  // that omitted ``sourcePath`` (and therefore ``sourceHash`` and the
  // closure files) short-circuited the sourceHash mismatch check --
  // the ``if (descriptorSourcePath)`` guard skipped freshness entirely
  // when the field was blank. Combined with same-stem inference
  // (``block.py`` beside ``__cadgen__/models/block.py/``) the forged
  // package was silently blessed as OK with an arbitrary attacker-
  // supplied component GLB. This test constructs exactly that state
  // and demands both the direct call AND ``scanCadDirectory`` refuse.
  const { validateStepTopologyArtifact, scanCadDirectory: scan } =
    await import("../catalog/cadDirectoryScanner.mjs");
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const genDir = path.join(repoRoot, "workspace/generated");
  fs.mkdirSync(genDir, { recursive: true });
  const generatorPath = path.join(genDir, "block.py");
  fs.writeFileSync(
    generatorPath,
    "from build123d import Box\n\ndef gen_step():\n    return Box(1, 1, 1)\n",
  );
  const packageDir = path.join(genDir, "__cadgen__/models/block.py");
  fs.mkdirSync(path.join(packageDir, "components"), { recursive: true });
  // Forged component: any bytes will do -- the point is that the
  // validator must not trust the descriptor.
  const componentPath = path.join(packageDir, "components/forged.glb");
  fs.writeFileSync(componentPath, Buffer.from("glTFforged bytes"));
  // Forged descriptor: sourceKind=python, but no sourcePath, no
  // sourceHash, no closure, no stepHash. Previously the missing
  // sourcePath silently skipped freshness; new behavior must fail.
  const descriptor = {
    schemaVersion: 4,
    packageSchemaVersion: 3,
    sourceKind: "python",
    stepPath: "block.step",
    components: {
      forged: { glb: "components/forged.glb", contentHash: "0".repeat(64) },
    },
  };
  fs.writeFileSync(
    path.join(packageDir, "assembly.json"),
    JSON.stringify(descriptor),
  );

  const stepPath = path.join(genDir, "block.step");
  const result = validateStepTopologyArtifact({
    repoRoot,
    sourcePath: stepPath,
    entryPath: generatorPath,
  });
  assert.ok(
    !result.stepArtifact?.ok,
    `forged same-stem python package must be rejected, got ${JSON.stringify(result.stepArtifact)}`,
  );
  // Specifically: the missing sourcePath/sourceHash binding must be
  // called out (missing_source_hash), NOT a generic missing_glb that
  // could mask the security failure.
  assert.equal(
    result.stepArtifact?.error?.code,
    "missing_source_hash",
    `expected missing_source_hash for forged descriptor, got ${result.stepArtifact?.error?.code}`,
  );

  // And through the scan / catalog path -- same rejection.
  const catalog = scan({ repoRoot, rootDir: "workspace" });
  const stepEntry = catalog.entries.find((e) => e.file === "generated/block.step");
  if (stepEntry) {
    assert.ok(
      stepEntry.artifact && !stepEntry.artifact.ok,
      `scan must not surface an OK artifact for the forged same-stem package; got ${JSON.stringify(stepEntry.artifact)}`,
    );
  }
});


test("binder and discovery reject an in-repo symlink that reroutes the logical STEP", stepArtifactTestOptions, async (t) => {
  // Reviewer regression: current binder only checks that the
  // deepest-existing ancestor's realpath stays *inside* the trusted
  // root. That lets an attacker (or a broken build) create an in-repo
  // symlink ``workspace/generated -> workspace/actual`` such that the
  // logical STEP path ``workspace/generated/robot.step`` NOW resolves
  // to ``workspace/actual/robot.step`` while the lexical identity used
  // by the catalog and package binding still reads ``generated/*``.
  // A viewer that trusts realpath-under-root would publish the
  // rerouted target as the package's STEP output, letting arbitrary
  // sibling directories masquerade as ``generated/``.
  //
  // Fix: for every logical path the binder / discovery accepts (both
  // the ``stepPath`` derived from the descriptor AND the same-stem /
  // descriptor fallback in ``_logicalStepPathForGenerator``), require
  // lexical relative path from the trusted root to equal the realpath
  // relative path of the deepest existing ancestor (segment-aware).
  const { bindCadgenPackage } = await import("../catalog/cadDirectoryScanner.mjs");
  const repoRoot = makeTempRepo();
  t.after(() => fs.rmSync(repoRoot, { recursive: true, force: true }));
  const stepPath = path.join(repoRoot, "workspace/generated/robot.step");
  const generatorPath = path.join(repoRoot, "workspace/sources/assembly.py");
  writePythonBoxGenerator(generatorPath);
  await ensureStepTopologyArtifact({
    repoRoot,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
  });
  // Baseline: binder accepts and discovery surfaces the mapping.
  const packageDir = renderPackageDir(generatorPath);
  const goodBinding = bindCadgenPackage({ packageDir, trustedRoot: repoRoot });
  assert.ok(goodBinding, "binder must accept the canonical package before the reroute");
  const baselineCatalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  assert.ok(
    baselineCatalog.entries.some((e) => e.file === "generated/robot.step"),
    "baseline must surface generated/robot.step",
  );

  // Reroute: rename ``workspace/generated`` to ``workspace/actual``,
  // then plant an in-repo symlink ``generated -> actual``. Both paths
  // stay inside the trusted root, so a naive realpath-under-root check
  // still passes. Only a lex == real segment-aware check catches this.
  const generatedDir = path.join(repoRoot, "workspace/generated");
  const actualDir = path.join(repoRoot, "workspace/actual");
  fs.renameSync(generatedDir, actualDir);
  try {
    fs.symlinkSync(actualDir, generatedDir, "dir");
  } catch (error) {
    if (error && (error.code === "EPERM" || error.code === "EACCES")) {
      t.skip(`cannot create directory symlinks on this host: ${error?.message || error}`);
      return;
    }
    throw error;
  }

  // Sanity: the rerouted logical STEP still resolves to a file inside
  // the repo (both endpoints are under repoRoot).
  assert.ok(fs.existsSync(stepPath), "the rerouted STEP still resolves via the in-repo symlink");
  assert.ok(fs.realpathSync(stepPath).startsWith(fs.realpathSync(repoRoot)));

  // Binder must reject the descriptor whose logical STEP now traverses
  // an in-repo symlink even though the realpath stays under the root.
  const rerouted = bindCadgenPackage({ packageDir, trustedRoot: repoRoot });
  assert.equal(rerouted, null, "binder must reject an in-repo symlink reroute of the logical STEP");

  // Discovery / scan must not surface a rerouted mapping. It may
  // either drop the entry entirely (no binding) or fall back to same-
  // stem inference; it must NOT preserve the descriptor's rerouted
  // identity.
  const catalog = scanCadDirectory({ repoRoot, rootDir: "workspace" });
  const rerouted_entry = catalog.entries.find((e) => e.file === "generated/robot.step");
  const authoritative = rerouted_entry?.url || "";
  assert.ok(
    !authoritative.includes("__cadgen__/models/assembly.py"),
    `scan must not preserve rerouted-descriptor identity; got url=${authoritative}`,
  );
});
