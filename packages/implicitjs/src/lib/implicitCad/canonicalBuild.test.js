import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  IMPLICIT_CANONICAL_EXECUTION_PROFILE,
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

function writeCanonicalTaperedCup(root) {
  const sourcePath = path.join(root, "canonical-tapered-cup.implicit.js");
  fs.writeFileSync(sourcePath, `
export default {
  schema: "implicit.js/0.1.0",
  name: "canonical tapered cup",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`
float outerRadius(float z) {
  float t = clamp((z + 0.50) / 1.00, 0.0, 1.0);
  float body = mix(0.246, 0.338, t);
  float foot = 0.028 * exp(-pow((z + 0.455) / 0.040, 2.0));
  float shoulder = 0.030 * smoothstep(0.27, 0.37, z);
  float rim = 0.022 * exp(-pow((z - 0.405) / 0.050, 2.0));
  return body + foot + shoulder + rim;
}

float sdf(vec3 p) {
  float radial = length(p.xy);
  float outer = max(radial - outerRadius(p.z), max(-0.50 - p.z, p.z - 0.46));
  float innerRadius = outerRadius(p.z) - 0.034;
  float inner = max(innerRadius - radial, max(-0.435 - p.z, p.z - 0.50));
  return max(outer, -inner);
}

vec3 color(vec3 p, vec3 normal) {
  return vec3(0.72, 0.50, 0.26);
}
\`,
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

test("canonical implicit execution profile carries the calibrated CVM deadline", () => {
  assert.deepEqual(IMPLICIT_CANONICAL_EXECUTION_PROFILE, {
    schema: "mesh-to-cad.implicit-execution-profile/1",
    id: "implicit_canonical_worker/4",
    worker_timeout_ms: 720000,
  });
});

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
    "execution-profile.json",
    "build.json",
    "rebuild.json",
  ]) {
    assert.equal(fs.existsSync(path.join(outputRoot, relativePath)), true, relativePath);
  }
  assert.equal(fs.readFileSync(archivedSource, "utf-8"), fs.readFileSync(sourcePath, "utf-8"));
  assert.equal(fs.existsSync(path.join(outputRoot, "artifacts", "model.step")), false);

  const profile = readJson(path.join(outputRoot, "profile.json"));
  assert.deepEqual(profile, IMPLICIT_CANONICAL_PROFILE);
  const executionProfile = readJson(path.join(outputRoot, "execution-profile.json"));
  assert.deepEqual(executionProfile, IMPLICIT_CANONICAL_EXECUTION_PROFILE);

  const manifest = readJson(path.join(outputRoot, "build.json"));
  assert.equal(manifest.schema, "mesh-to-cad.build/1");
  assert.equal(manifest.route, "implicit");
  assert.deepEqual(manifest.tool, { id: "implicitjs", version: "0.1.0" });
  assert.equal(manifest.profile.id, "implicit_voxblame_depth8/1");
  assert.equal(manifest.execution_profile.id, "implicit_canonical_worker/4");
  assert.equal(manifest.execution_profile.path, "execution-profile.json");
  assert.match(manifest.execution_profile.sha256, /^[a-f0-9]{64}$/u);
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
    worker_timeout_ms: 720000,
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

test("canonical implicit build classifies a tapered source deadline through its execution profile", async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-timeout-"));
  const sourcePath = writeCanonicalTaperedCup(workspaceDirectory);
  const executionProfilePath = path.join(workspaceDirectory, "execution-profile.json");
  fs.writeFileSync(executionProfilePath, `${JSON.stringify({
    schema: "mesh-to-cad.implicit-execution-profile/1",
    id: "implicit_canonical_worker_test/1",
    worker_timeout_ms: 1,
  }, null, 2)}\n`, "utf-8");

  await assert.rejects(
    buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(sourcePath),
      outputDirectory: "must-time-out",
      executionProfilePath: path.basename(executionProfilePath),
    }),
    (error) => {
      assert.match(error.message, /^canonical_build_timeout: worker exceeded execution profile deadline of 1 ms$/u);
      assert.doesNotMatch(error.message, /SIGKILL/u);
      return true;
    },
  );
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "must-time-out")), false);
});

test("canonical build CLI injects a frozen execution profile", () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-timeout-cli-"));
  const sourcePath = writeCanonicalTaperedCup(workspaceDirectory);
  const executionProfilePath = path.join(workspaceDirectory, "execution-profile.json");
  fs.writeFileSync(executionProfilePath, `${JSON.stringify({
    schema: "mesh-to-cad.implicit-execution-profile/1",
    id: "implicit_canonical_worker_test/1",
    worker_timeout_ms: 1,
  }, null, 2)}\n`, "utf-8");
  const cliPath = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../../scripts/canonical-build.mjs",
  );

  const completed = spawnSync(process.execPath, [
    cliPath,
    "--source",
    path.basename(sourcePath),
    "--output-dir",
    "must-time-out",
    "--execution-profile",
    path.basename(executionProfilePath),
    "--json",
  ], { cwd: workspaceDirectory, encoding: "utf-8" });

  assert.equal(completed.status, 1);
  assert.match(completed.stderr, /^canonical_build_timeout: worker exceeded execution profile deadline of 1 ms\n$/u);
  assert.doesNotMatch(completed.stderr, /SIGKILL/u);
});

test("canonical implicit build accepts import-like text that is not a module dependency", async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-import-text-"));
  const sourcePath = path.join(workspaceDirectory, "documented.implicit.mjs");
  fs.writeFileSync(sourcePath, `
const importantRadius = 0.1;
const documentation = "import is not a module dependency";
// The word import in documentation must not change source validity.
export default {
  schema: "implicit.js/0.1.0",
  name: documentation,
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: \`float sdf(vec3 p) { return length(p) - 0.1; }\`
};
void importantRadius;
`, "utf-8");

  const result = await buildCanonicalImplicitCad({
    workspaceDirectory,
    sourcePath: path.basename(sourcePath),
    outputDirectory: "documented-output",
  });

  assert.equal(result.ok, true);
  assert.equal(fs.existsSync(path.join(workspaceDirectory, "documented-output", "build.json")), true);
});

test("canonical implicit build preserves runtime-critical worker environment", {
  skip: process.platform === "win32",
}, async () => {
  const workspaceDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "implicit-canonical-worker-env-"));
  const sourcePath = writeCanonicalSphere(workspaceDirectory);
  const wrapperPath = path.join(workspaceDirectory, "node-runtime-wrapper");
  const originalExecPathDescriptor = Object.getOwnPropertyDescriptor(process, "execPath");
  const originalSystemRoot = process.env.SystemRoot;
  fs.writeFileSync(wrapperPath, `#!/bin/sh
if [ "$SystemRoot" != "canonical-required-root" ]; then
  echo "missing runtime-critical SystemRoot" >&2
  exit 91
fi
exec ${JSON.stringify(process.execPath)} "$@"
`, { encoding: "utf-8", mode: 0o755 });
  process.env.SystemRoot = "canonical-required-root";
  Object.defineProperty(process, "execPath", {
    ...originalExecPathDescriptor,
    value: wrapperPath,
  });

  try {
    const result = await buildCanonicalImplicitCad({
      workspaceDirectory,
      sourcePath: path.basename(sourcePath),
      outputDirectory: "worker-env-output",
    });
    assert.equal(result.ok, true);
  } finally {
    Object.defineProperty(process, "execPath", originalExecPathDescriptor);
    if (originalSystemRoot === undefined) {
      delete process.env.SystemRoot;
    } else {
      process.env.SystemRoot = originalSystemRoot;
    }
  }
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
  assert.deepEqual(recipe.execution_profile, {
    id: "implicit_canonical_worker/4",
    path: "execution-profile.json",
    sha256: readJson(path.join(deliveryRoot, "build.json")).execution_profile.sha256,
  });
  assert.equal(collectStrings(recipe).some((value) => path.isAbsolute(value)), false);
  assert.deepEqual(recipe.inputs.map(({ role, path: inputPath }) => ({ role, path: inputPath })), [
    {
      role: "primary_implicit_source",
      path: "source/portable.implicit.js",
    },
    {
      role: "frozen_execution_profile",
      path: "execution-profile.json",
    },
  ]);

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
  assert.deepEqual(rebuiltManifest.execution_profile, originalManifest.execution_profile);
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
    /imports are not permitted/u,
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

  const degenerateBoundsSource = path.join(workspaceDirectory, "degenerate-bounds.implicit.js");
  fs.writeFileSync(
    degenerateBoundsSource,
    fs.readFileSync(sourcePath, "utf-8").replace(
      "bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],",
      "bounds: [[0, -0.5, -0.5], [0, 0.5, 0.5]],",
    ),
    "utf-8",
  );
  await assert.rejects(buildCanonicalImplicitCad({
    workspaceDirectory,
    sourcePath: path.basename(degenerateBoundsSource),
    outputDirectory: "degenerate-bounds-output",
  }), /exact.*bounds/u);

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
