import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  IMPLICIT_CANONICAL_PROFILE,
  buildCanonicalImplicitCad,
  rebuildCanonicalImplicitCad,
} from "./canonicalBuild.js";

function writeCanonicalSphere(root, name = "offset-sphere.implicit.mjs") {
  const sourcePath = path.join(root, name);
  fs.writeFileSync(sourcePath, `
export default {
  schema: "implicit.js/0.1.0",
  name: "canonical offset sphere",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p - vec3(0.2, 0.0, 0.0)) - 0.1; }\`
};
`, "utf-8");
  return sourcePath;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf-8"));
}

function parseGlbJson(filePath) {
  const buffer = fs.readFileSync(filePath);
  assert.equal(buffer.toString("utf-8", 0, 4), "glTF");
  const jsonLength = buffer.readUInt32LE(12);
  return JSON.parse(buffer.toString("utf-8", 20, 20 + jsonLength).trim());
}

function collectStrings(value, result = []) {
  if (typeof value === "string") {
    result.push(value);
  } else if (Array.isArray(value)) {
    value.forEach((child) => collectStrings(child, result));
  } else if (value && typeof value === "object") {
    Object.values(value).forEach((child) => collectStrings(child, result));
  }
  return result;
}

test("canonical implicit build publishes traceable artifacts without moving source geometry", async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-build-"));
  const sourcePath = writeCanonicalSphere(workspaceDirectory);

  const result = await buildCanonicalImplicitCad({
    workspaceDirectory,
    sourcePath: path.basename(sourcePath),
    outputDirectory: "delivery/custom-location",
  });

  assert.equal(result.ok, true);
  assert.equal(result.outputDirectory, "delivery/custom-location");
  const outputRoot = path.join(workspaceDirectory, result.outputDirectory);
  const archivedSource = path.join(outputRoot, "source", path.basename(sourcePath));
  const measurementGlb = path.join(outputRoot, "artifacts", "model.glb");
  for (const relativePath of [
    `source/${path.basename(sourcePath)}`,
    "artifacts/model.glb",
    "profile.json",
    "build.json",
    "rebuild.json",
  ]) {
    assert.equal(fs.existsSync(path.join(outputRoot, relativePath)), true, relativePath);
  }
  assert.equal(fs.readFileSync(archivedSource, "utf-8"), fs.readFileSync(sourcePath, "utf-8"));
  assert.equal(fs.existsSync(path.join(outputRoot, "artifacts", "model.step")), false);

  const profile = readJson(path.join(outputRoot, "profile.json"));
  assert.deepEqual(profile, IMPLICIT_CANONICAL_PROFILE);

  const manifest = readJson(path.join(outputRoot, "build.json"));
  assert.equal(manifest.schema, "mesh-to-cad.build/1");
  assert.equal(manifest.route, "implicit");
  assert.deepEqual(manifest.tool, { id: "implicitjs", version: "0.1.0" });
  assert.equal(manifest.profile.id, "implicit_voxblame_depth8/1");
  assert.equal(manifest.coordinate_contract.id, "trellis2-canonical/1");
  assert.equal(manifest.coordinate_contract.source_coordinates, "preserved");
  assert.deepEqual(manifest.coordinate_contract.operations, {
    alignment: false,
    bounds_fit: false,
    normalization: false,
    semantic_unit_scaling: false,
  });
  assert.equal(manifest.artifacts.primary.path, `source/${path.basename(sourcePath)}`);
  assert.equal(manifest.artifacts.measurement.path, "artifacts/model.glb");
  assert.equal(manifest.derivation.edges[0].from, manifest.artifacts.primary.sha256);
  assert.equal(manifest.derivation.edges[0].to, manifest.artifacts.measurement.sha256);
  assert.equal(manifest.derivation.edges[0].execution, "same-execution");
  assert.equal(manifest.dependencies.network, false);
  assert.deepEqual(manifest.execution_policy, {
    id: "node-permission-vm/1",
    experiment_external_reads: false,
    network: false,
    source_imports: false,
  });
  assert.equal(manifest.serialization_units.semantic, false);

  const gltf = parseGlbJson(measurementGlb);
  const positionBounds = gltf.accessors[gltf.meshes[0].primitives[0].attributes.POSITION];
  assert.ok(positionBounds.min[0] > 0.09, JSON.stringify(positionBounds.min));
  assert.ok(positionBounds.max[0] < 0.31, JSON.stringify(positionBounds.max));
  assert.ok(positionBounds.min[1] > -0.11, JSON.stringify(positionBounds.min));
  assert.ok(positionBounds.max[1] < 0.11, JSON.stringify(positionBounds.max));
  assert.equal(gltf.nodes[0].extras.cadUnits, "unitless");
});

test("registered implicit recipe rebuilds offline through the skill entry", async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-rebuild-"));
  const sourcePath = writeCanonicalSphere(workspaceDirectory, "portable.implicit.js");
  await buildCanonicalImplicitCad({
    workspaceDirectory,
    sourcePath: path.basename(sourcePath),
    outputDirectory: "portable-delivery",
  });
  fs.rmSync(sourcePath);

  const deliveryRoot = path.join(workspaceDirectory, "portable-delivery");
  const recipe = readJson(path.join(deliveryRoot, "rebuild.json"));
  assert.equal(recipe.schema, "mesh-to-cad.rebuild-recipe/1");
  assert.deepEqual(recipe.executable, { id: "implicit-cad.canonical-build/1" });
  assert.equal(recipe.network, false);
  assert.equal(collectStrings(recipe).some((value) => path.isAbsolute(value)), false);
  assert.deepEqual(recipe.inputs.map(({ role, path: inputPath }) => ({ role, path: inputPath })), [{
    role: "primary_implicit_source",
    path: "source/portable.implicit.js",
  }]);

  const repoRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../../../..",
  );
  const entrypoint = path.join(repoRoot, "skills/implicit-cad/scripts/canonical-build.mjs");
  const completed = spawnSync(process.execPath, [
    entrypoint,
    "--recipe",
    "rebuild.json",
    "--output-dir",
    "rebuilt/explicit-destination",
    "--json",
  ], {
    cwd: deliveryRoot,
    encoding: "utf-8",
    env: {
      PATH: process.env.PATH,
      TMPDIR: process.env.TMPDIR,
    },
  });
  assert.equal(completed.status, 0, completed.stderr);
  const result = JSON.parse(completed.stdout);
  assert.equal(result.outputDirectory, "rebuilt/explicit-destination");

  const originalManifest = readJson(path.join(deliveryRoot, "build.json"));
  const rebuiltRoot = path.join(deliveryRoot, result.outputDirectory);
  const rebuiltManifest = readJson(path.join(rebuiltRoot, "build.json"));
  assert.equal(
    rebuiltManifest.artifacts.primary.sha256,
    originalManifest.artifacts.primary.sha256,
  );
  assert.equal(
    rebuiltManifest.artifacts.measurement.sha256,
    originalManifest.artifacts.measurement.sha256,
  );
  assert.deepEqual(
    parseGlbJson(path.join(rebuiltRoot, "artifacts/model.glb")).accessors[0],
    parseGlbJson(path.join(deliveryRoot, "artifacts/model.glb")).accessors[0],
  );
});

test("canonical adapter rejects undeclared geometry-changing build inputs", async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-options-"));
  const sourcePath = writeCanonicalSphere(workspaceDirectory);

  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(sourcePath),
      outputDirectory: "must-not-exist",
      normalization: "fit-to-bounds",
      alignment: "center",
      unitScale: 1000,
      resolution: 24,
    }),
    /Unsupported canonical build option.*alignment.*normalization.*resolution.*unitScale/u,
  );
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "must-not-exist")), false);
});

test("canonical adapter rejects undeclared source imports and network dependencies", async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-dependencies-"));
  const importedSource = path.join(workspaceDirectory, "imported.implicit.mjs");
  fs.writeFileSync(importedSource, `
import { readFileSync } from "node:fs";
export default {
  schema: "implicit.js/0.1.0",
  name: typeof readFileSync === "function" ? "imported" : "missing",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p) - 0.1; }\`
};
`, "utf-8");
  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(importedSource),
      outputDirectory: "import-output",
    }),
    /self-contained.*import/u,
  );

  const networkSource = path.join(workspaceDirectory, "network.implicit.js");
  fs.writeFileSync(networkSource, `
async function unusedNetworkDependency() { return fetch("https://example.invalid/model"); }
export default {
  schema: "implicit.js/0.1.0",
  name: String(unusedNetworkDependency.name),
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p) - 0.1; }\`
};
`, "utf-8");
  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(networkSource),
      outputDirectory: "network-output",
    }),
    /network API/u,
  );

  const computedNetworkSource = path.join(workspaceDirectory, "computed-network.implicit.js");
  fs.writeFileSync(computedNetworkSource, `
await globalThis["fetch"]("data:text/plain,network");
export default {
  schema: "implicit.js/0.1.0",
  name: "computed network",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p) - 0.1; }\`
};
`, "utf-8");
  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(computedNetworkSource),
      outputDirectory: "computed-network-output",
    }),
    /restricted canonical source execution/u,
  );

  const sidecarPath = path.join(workspaceDirectory, "undeclared-sidecar.txt");
  fs.writeFileSync(sidecarPath, "external value", "utf-8");
  const computedAccessSource = path.join(workspaceDirectory, "computed-access.implicit.js");
  fs.writeFileSync(computedAccessSource, `
const externalValue = process["getBuiltinModule"]("fs")["readFileSync"](${JSON.stringify(sidecarPath)}, "utf-8");
export default {
  schema: "implicit.js/0.1.0",
  name: externalValue,
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p) - 0.1; }\`
};
`, "utf-8");
  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(computedAccessSource),
      outputDirectory: "computed-access-output",
    }),
    /restricted canonical source execution/u,
  );

  const nondeterministicSource = path.join(workspaceDirectory, "random.implicit.js");
  fs.writeFileSync(nondeterministicSource, `
const offset = Math.random() * 0.1;
export default {
  schema: "implicit.js/0.1.0",
  name: "random source",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p - vec3(\${offset}, 0.0, 0.0)) - 0.1; }\`
};
`, "utf-8");
  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(nondeterministicSource),
      outputDirectory: "random-output",
    }),
    /deterministic canonical source/u,
  );

  const ambientLocaleSource = path.join(workspaceDirectory, "ambient-locale.implicit.js");
  fs.writeFileSync(ambientLocaleSource, `
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
export default {
  schema: "implicit.js/0.1.0",
  name: timezone,
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p) - 0.1; }\`
};
`, "utf-8");
  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(ambientLocaleSource),
      outputDirectory: "ambient-locale-output",
    }),
    /deterministic canonical source/u,
  );

  const ambientLocaleCompareSource = path.join(workspaceDirectory, "ambient-locale-compare.implicit.js");
  fs.writeFileSync(ambientLocaleCompareSource, `
const offset = "ä".localeCompare("z") < 0 ? 0.1 : 0.2;
export default {
  schema: "implicit.js/0.1.0",
  name: "ambient locale comparison",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p - vec3(\${offset}, 0.0, 0.0)) - 0.1; }\`
};
`, "utf-8");
  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(ambientLocaleCompareSource),
      outputDirectory: "ambient-locale-compare-output",
    }),
    /deterministic canonical source/u,
  );
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "import-output")), false);
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "network-output")), false);
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "computed-network-output")), false);
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "computed-access-output")), false);
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "random-output")), false);
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "ambient-locale-output")), false);
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "ambient-locale-compare-output")), false);
});

test("canonical adapter confines paths and rejects noncanonical source and recipe contracts", async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-rejections-"));
  const outsideDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-outside-"));
  const sourcePath = writeCanonicalSphere(workspaceDirectory);
  fs.symlinkSync(outsideDirectory, path.join(workspaceDirectory, "outside-link"));

  for (const [source, output, message] of [
    [sourcePath, "absolute-source", /sourcePath must be a portable relative path/u],
    [path.basename(sourcePath), path.join(outsideDirectory, "absolute-output"), /outputDirectory must be a portable relative path/u],
    [path.basename(sourcePath), "../traversal-output", /outputDirectory must stay within/u],
    [path.basename(sourcePath), "outside-link/nested-parent/escaped-output", /outputDirectory resolves outside/u],
  ]) {
    await assert.rejects(buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: source,
      outputDirectory: output,
    }), message);
  }
  assert.equal(fs.existsSync(path.join(outsideDirectory, "nested-parent")), false);

  const millimeterSource = path.join(workspaceDirectory, "millimeters.implicit.js");
  fs.writeFileSync(
    millimeterSource,
    fs.readFileSync(sourcePath, "utf-8").replace('units: "unitless"', 'units: "mm"'),
    "utf-8",
  );
  await assert.rejects(buildCanonicalImplicitCad({
    workspaceDirectory,
    sourcePath: path.basename(millimeterSource),
    outputDirectory: "millimeter-output",
  }), /unit conversion is not permitted/u);

  const autoBoundsSource = path.join(workspaceDirectory, "auto-bounds.implicit.js");
  fs.writeFileSync(
    autoBoundsSource,
    fs.readFileSync(sourcePath, "utf-8").replace(
      "bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],",
      'bounds: "auto",',
    ),
    "utf-8",
  );
  await assert.rejects(buildCanonicalImplicitCad({
    workspaceDirectory,
    sourcePath: path.basename(autoBoundsSource),
    outputDirectory: "auto-bounds-output",
  }), /automatic bounds fit is not permitted/u);

  await buildCanonicalImplicitCad({
    workspaceDirectory,
    sourcePath: path.basename(sourcePath),
    outputDirectory: "tampered-delivery",
  });
  const deliveryRoot = path.join(workspaceDirectory, "tampered-delivery");
  const recipePath = path.join(deliveryRoot, "rebuild.json");
  const recipe = readJson(recipePath);
  recipe.outputs.push({ role: "undeclared_sidecar", path: path.join(outsideDirectory, "escape.bin") });
  fs.writeFileSync(recipePath, `${JSON.stringify(recipe, null, 2)}\n`, "utf-8");
  await assert.rejects(rebuildCanonicalImplicitCad({
    workspaceDirectory: deliveryRoot,
    recipePath: "rebuild.json",
    outputDirectory: "must-not-rebuild",
  }), /recipe outputs/u);
  assert.equal(fs.existsSync(path.join(deliveryRoot, "must-not-rebuild")), false);
});
