import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BUILD_SCHEMA = "mesh-to-cad.build/1";
const REBUILD_SCHEMA = "mesh-to-cad.rebuild-recipe/1";
const ROUTE = "implicit";
const ENTRYPOINT_ID = "implicit-cad.canonical-build/1";
const TOOL = Object.freeze({ id: "implicitjs", version: "0.1.0" });
const CANONICAL_BOUNDS = Object.freeze([
  Object.freeze([-0.5, -0.5, -0.5]),
  Object.freeze([0.5, 0.5, 0.5]),
]);

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) {
      deepFreeze(child);
    }
  }
  return value;
}

export const IMPLICIT_CANONICAL_PROFILE = deepFreeze({
  schema: "mesh-to-cad.implicit-profile/1",
  id: "implicit_voxblame_depth8/1",
  coordinate_contract: "trellis2-canonical/1",
  canonical_bounds: CANONICAL_BOUNDS,
  sampling: {
    resolution: 96,
    max_cells: 2500000,
    normal_epsilon: 0.00001,
  },
  export: {
    format: "glb",
    smooth_normals: true,
  },
  operations: {
    alignment: false,
    bounds_fit: false,
    normalization: false,
    semantic_unit_scaling: false,
  },
});

export const IMPLICIT_CANONICAL_EXECUTION_PROFILE = deepFreeze({
  schema: "mesh-to-cad.implicit-execution-profile/1",
  id: "implicit_canonical_worker/4",
  worker_timeout_ms: 720000,
});

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(",")}}`;
  }
  return JSON.stringify(value);
}

function jsonBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function portableRelativePath(value, label) {
  const raw = String(value || "").trim();
  if (!raw || path.isAbsolute(raw) || raw.includes("\\")) {
    throw new Error(`${label} must be a portable relative path`);
  }
  const normalized = path.posix.normalize(raw);
  if (normalized === "." || normalized === ".." || normalized.startsWith("../")) {
    throw new Error(`${label} must stay within the workspace directory`);
  }
  return normalized;
}

function isWithin(parent, child) {
  const relative = path.relative(parent, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

async function resolveWorkspaceFile(workspaceRoot, relativePath, label) {
  const resolved = path.resolve(workspaceRoot, relativePath);
  if (!isWithin(workspaceRoot, resolved)) {
    throw new Error(`${label} must stay within the workspace directory`);
  }
  const realPath = await fs.realpath(resolved);
  if (!isWithin(workspaceRoot, realPath)) {
    throw new Error(`${label} resolves outside the workspace directory`);
  }
  const stats = await fs.lstat(resolved);
  if (!stats.isFile() || stats.isSymbolicLink()) {
    throw new Error(`${label} must be a regular file`);
  }
  return resolved;
}

function validateExecutionProfile(profile) {
  const expectedKeys = ["id", "schema", "worker_timeout_ms"];
  if (
    profile?.schema !== "mesh-to-cad.implicit-execution-profile/1"
    || typeof profile?.id !== "string"
    || !/^[a-z0-9][a-z0-9_-]*\/[1-9][0-9]*$/u.test(profile.id)
    || !Number.isSafeInteger(profile?.worker_timeout_ms)
    || profile.worker_timeout_ms < 1
    || profile.worker_timeout_ms > 900000
    || canonicalJson(Object.keys(profile || {}).sort()) !== canonicalJson(expectedKeys)
  ) {
    throw new Error("Invalid implicit canonical execution profile");
  }
  return deepFreeze(structuredClone(profile));
}

async function readExecutionProfile(workspaceRoot, executionProfilePath) {
  if (!executionProfilePath) {
    return IMPLICIT_CANONICAL_EXECUTION_PROFILE;
  }
  const relativePath = portableRelativePath(executionProfilePath, "executionProfilePath");
  const absolutePath = await resolveWorkspaceFile(
    workspaceRoot,
    relativePath,
    "executionProfilePath",
  );
  let profile;
  try {
    profile = JSON.parse(await fs.readFile(absolutePath, "utf-8"));
  } catch (error) {
    throw new Error(`Invalid implicit canonical execution profile: ${error instanceof Error ? error.message : String(error)}`);
  }
  return validateExecutionProfile(profile);
}

function validateSelfContainedSource(sourceText) {
  if (
    /\brequire\s*\(/u.test(sourceText)
    || /\bprocess\s*\.\s*getBuiltinModule\s*\(/u.test(sourceText)
  ) {
    throw new Error("Canonical implicit source must be self-contained; import and undeclared file dependencies are not permitted");
  }
  if (
    /\b(?:fetch|WebSocket|EventSource|XMLHttpRequest)\s*\(/u.test(sourceText)
    || /\bnavigator\s*\.\s*sendBeacon\s*\(/u.test(sourceText)
  ) {
    throw new Error("Canonical implicit source must not use a network API");
  }
}

async function prepareConfinedOutputParent(workspaceRoot, outputParent) {
  let existingAncestor = outputParent;
  while (true) {
    try {
      await fs.lstat(existingAncestor);
      break;
    } catch (error) {
      if (error?.code !== "ENOENT") {
        throw error;
      }
      const parent = path.dirname(existingAncestor);
      if (parent === existingAncestor) {
        throw new Error("outputDirectory has no existing ancestor");
      }
      existingAncestor = parent;
    }
  }
  const ancestorReal = await fs.realpath(existingAncestor);
  if (!isWithin(workspaceRoot, ancestorReal)) {
    throw new Error("outputDirectory resolves outside the workspace directory");
  }
  await fs.mkdir(outputParent, { recursive: true });
  const outputParentReal = await fs.realpath(outputParent);
  if (!isWithin(workspaceRoot, outputParentReal)) {
    throw new Error("outputDirectory resolves outside the workspace directory");
  }
  return outputParentReal;
}

function permissionFlag() {
  if (process.allowedNodeEnvironmentFlags?.has("--permission")) {
    return "--permission";
  }
  if (process.allowedNodeEnvironmentFlags?.has("--experimental-permission")) {
    return "--experimental-permission";
  }
  throw new Error("Canonical implicit build requires a Node runtime with the permission model");
}

function restrictedWorkerEnvironment() {
  const runtimeKeys = new Set(["systemroot", "windir"]);
  return Object.fromEntries(Object.entries(process.env).filter(([key, value]) => (
    value !== undefined && runtimeKeys.has(key.toLowerCase())
  )));
}

async function exportInRestrictedProcess(sourceText, outputPath, executionProfile) {
  const packageRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const workerPath = path.join(
    path.dirname(fileURLToPath(import.meta.url)),
    "canonicalBuildWorker.mjs",
  );
  const args = [
    permissionFlag(),
    "--no-warnings",
    "--experimental-vm-modules",
    `--allow-fs-read=${packageRoot}`,
    `--allow-fs-write=${outputPath}`,
    workerPath,
    outputPath,
  ];
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, {
      cwd: packageRoot,
      env: restrictedWorkerEnvironment(),
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    let deadlineExceeded = false;
    const timeout = setTimeout(() => {
      deadlineExceeded = true;
      child.kill("SIGKILL");
    }, executionProfile.worker_timeout_ms);
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (status, signal) => {
      clearTimeout(timeout);
      if (deadlineExceeded) {
        reject(new Error(
          `canonical_build_timeout: worker exceeded execution profile deadline of ${executionProfile.worker_timeout_ms} ms`,
        ));
        return;
      }
      const errorText = Buffer.concat(stderr).toString("utf-8").trim();
      if (status !== 0) {
        const detail = errorText || `worker exited with ${status ?? signal ?? "unknown status"}`;
        reject(new Error(`restricted canonical source execution failed: ${detail}`));
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(stdout).toString("utf-8")));
      } catch (error) {
        reject(new Error(`restricted canonical source execution returned invalid metadata: ${error instanceof Error ? error.message : String(error)}`));
      }
    });
    child.stdin.end(sourceText);
  });
}

function fileRecord(role, relativePath, bytes) {
  return {
    role,
    path: relativePath,
    sha256: sha256(bytes),
    bytes: bytes.length,
  };
}

async function writeJson(filePath, value) {
  const body = jsonBytes(value);
  await fs.writeFile(filePath, body);
  return body;
}

function rejectUnknownOptions(options, permitted, label) {
  const unknown = Object.keys(options).filter((key) => !permitted.has(key)).sort();
  if (unknown.length) {
    throw new Error(`Unsupported ${label} option(s): ${unknown.join(", ")}`);
  }
}

function rebuildOutputs(sourcePath) {
  return [
    { role: "primary_implicit_source", path: sourcePath },
    { role: "measurement_glb", path: "artifacts/model.glb" },
    { role: "frozen_profile", path: "profile.json" },
    { role: "frozen_execution_profile", path: "execution-profile.json" },
    { role: "build_manifest", path: "build.json" },
    { role: "rebuild_recipe", path: "rebuild.json" },
  ];
}

export async function buildCanonicalImplicitCad(options = {}) {
  rejectUnknownOptions(
    options,
    new Set(["workspaceDirectory", "sourcePath", "outputDirectory", "executionProfilePath"]),
    "canonical build",
  );
  const {
    workspaceDirectory = process.cwd(),
    sourcePath,
    outputDirectory,
    executionProfilePath,
  } = options;
  const workspaceRoot = await fs.realpath(path.resolve(workspaceDirectory));
  const executionProfile = await readExecutionProfile(workspaceRoot, executionProfilePath);
  const sourceRelative = portableRelativePath(sourcePath, "sourcePath");
  const outputRelative = portableRelativePath(outputDirectory, "outputDirectory");
  if (!/\.implicit\.(?:js|mjs)$/iu.test(sourceRelative)) {
    throw new Error("sourcePath must end in .implicit.js or .implicit.mjs");
  }
  const sourceAbsolute = await resolveWorkspaceFile(workspaceRoot, sourceRelative, "sourcePath");
  const outputAbsolute = path.resolve(workspaceRoot, outputRelative);
  if (!isWithin(workspaceRoot, outputAbsolute)) {
    throw new Error("outputDirectory must stay within the workspace directory");
  }
  try {
    await fs.lstat(outputAbsolute);
    throw new Error("outputDirectory must not already exist");
  } catch (error) {
    if (error?.code !== "ENOENT") {
      throw error;
    }
  }

  const sourceBytes = await fs.readFile(sourceAbsolute);
  const sourceText = sourceBytes.toString("utf-8");
  validateSelfContainedSource(sourceText);

  const outputParent = path.dirname(outputAbsolute);
  const outputParentReal = await prepareConfinedOutputParent(workspaceRoot, outputParent);
  const stagingRoot = await fs.mkdtemp(path.join(outputParentReal, ".implicit-canonical-build-"));
  try {
    const sourceName = path.basename(sourceRelative);
    const archivedSourceRelative = `source/${sourceName}`;
    const measurementRelative = "artifacts/model.glb";
    await fs.mkdir(path.join(stagingRoot, "source"), { recursive: true });
    await fs.mkdir(path.join(stagingRoot, "artifacts"), { recursive: true });
    await fs.writeFile(path.join(stagingRoot, archivedSourceRelative), sourceBytes);

    const measurementPath = path.join(stagingRoot, measurementRelative);
    const exportSummary = await exportInRestrictedProcess(sourceText, measurementPath, executionProfile);
    const measurementBytes = await fs.readFile(measurementPath);

    const profileBytes = await writeJson(path.join(stagingRoot, "profile.json"), IMPLICIT_CANONICAL_PROFILE);
    const executionProfileBytes = await writeJson(
      path.join(stagingRoot, "execution-profile.json"),
      executionProfile,
    );
    const primary = fileRecord("primary_implicit_source", archivedSourceRelative, sourceBytes);
    const measurement = fileRecord("measurement_glb", measurementRelative, measurementBytes);
    const profile = fileRecord("frozen_profile", "profile.json", profileBytes);
    const executionProfileRecord = fileRecord(
      "frozen_execution_profile",
      "execution-profile.json",
      executionProfileBytes,
    );
    const rebuild = {
      schema: REBUILD_SCHEMA,
      route: ROUTE,
      executable: { id: ENTRYPOINT_ID },
      working_directory: ".",
      argv_template: [
        "--recipe",
        "rebuild.json",
        "--output-dir",
        "{output_directory}",
        "--json",
      ],
      profile: { id: IMPLICIT_CANONICAL_PROFILE.id, sha256: profile.sha256 },
      execution_profile: {
        id: executionProfile.id,
        path: executionProfileRecord.path,
        sha256: executionProfileRecord.sha256,
      },
      inputs: [
        { role: primary.role, path: primary.path, sha256: primary.sha256 },
        {
          role: executionProfileRecord.role,
          path: executionProfileRecord.path,
          sha256: executionProfileRecord.sha256,
        },
      ],
      outputs: rebuildOutputs(primary.path),
      network: false,
    };
    const rebuildBytes = await writeJson(path.join(stagingRoot, "rebuild.json"), rebuild);
    const rebuildRecord = fileRecord("rebuild_recipe", "rebuild.json", rebuildBytes);
    const manifest = {
      schema: BUILD_SCHEMA,
      route: ROUTE,
      entrypoint: { id: ENTRYPOINT_ID },
      adapter: { id: "implicitjs.canonical-build", version: 1 },
      tool: TOOL,
      profile: { id: IMPLICIT_CANONICAL_PROFILE.id, path: profile.path, sha256: profile.sha256 },
      execution_profile: {
        id: executionProfile.id,
        path: executionProfileRecord.path,
        sha256: executionProfileRecord.sha256,
      },
      artifacts: { primary, measurement },
      files: [primary, measurement, profile, executionProfileRecord, rebuildRecord],
      delivery_roots: ["source", "artifacts"],
      derivation: {
        nodes: [primary.sha256, measurement.sha256],
        edges: [{
          from: primary.sha256,
          to: measurement.sha256,
          operation: "sample-implicit-sdf-and-export-glb",
          execution: "same-execution",
        }],
      },
      dependencies: {
        network: false,
        direct: [{ name: TOOL.id, version: TOOL.version }],
      },
      execution_policy: {
        id: "node-permission-vm/1",
        network: false,
        source_imports: false,
        experiment_external_reads: false,
        worker_timeout_ms: executionProfile.worker_timeout_ms,
      },
      platform: {
        node: process.version,
        platform: process.platform,
        arch: process.arch,
        endianness: os.endianness(),
      },
      coordinate_contract: {
        id: "trellis2-canonical/1",
        bounds: CANONICAL_BOUNDS,
        units: "unitless",
        source_coordinates: "preserved",
        operations: IMPLICIT_CANONICAL_PROFILE.operations,
      },
      serialization_units: {
        format: "glb",
        declared: "unitless",
        semantic: false,
        coordinate_scale: 1,
      },
      mesh: {
        triangles: exportSummary.triangleCount,
        vertices: exportSummary.vertexCount,
        grid: exportSummary.grid,
      },
    };
    await writeJson(path.join(stagingRoot, "build.json"), manifest);
    await fs.rename(stagingRoot, outputAbsolute);
    return {
      ok: true,
      outputDirectory: outputRelative,
      manifest,
    };
  } catch (error) {
    await fs.rm(stagingRoot, { recursive: true, force: true });
    throw error;
  }
}

export async function rebuildCanonicalImplicitCad(options = {}) {
  rejectUnknownOptions(
    options,
    new Set(["workspaceDirectory", "recipePath", "outputDirectory"]),
    "canonical rebuild",
  );
  const {
    workspaceDirectory = process.cwd(),
    recipePath,
    outputDirectory,
  } = options;
  const workspaceRoot = await fs.realpath(path.resolve(workspaceDirectory));
  const recipeRelative = portableRelativePath(recipePath, "recipePath");
  if (recipeRelative !== "rebuild.json") {
    throw new Error("recipePath must be rebuild.json in the recipe working directory");
  }
  const recipeAbsolute = await resolveWorkspaceFile(workspaceRoot, recipeRelative, "recipePath");
  const recipe = JSON.parse(await fs.readFile(recipeAbsolute, "utf-8"));
  const expectedRecipeKeys = [
    "argv_template",
    "executable",
    "execution_profile",
    "inputs",
    "network",
    "outputs",
    "profile",
    "route",
    "schema",
    "working_directory",
  ];
  if (
    recipe?.schema !== REBUILD_SCHEMA
    || recipe?.route !== ROUTE
    || canonicalJson(recipe?.executable) !== canonicalJson({ id: ENTRYPOINT_ID })
    || recipe?.network !== false
    || recipe?.working_directory !== "."
    || canonicalJson(Object.keys(recipe || {}).sort()) !== canonicalJson(expectedRecipeKeys)
  ) {
    throw new Error("Unsupported implicit canonical rebuild recipe");
  }
  const expectedArgv = [
    "--recipe",
    "rebuild.json",
    "--output-dir",
    "{output_directory}",
    "--json",
  ];
  if (canonicalJson(recipe.argv_template) !== canonicalJson(expectedArgv)) {
    throw new Error("Unsupported implicit canonical rebuild recipe argv template");
  }
  const expectedProfileSha256 = sha256(jsonBytes(IMPLICIT_CANONICAL_PROFILE));
  if (
    recipe?.profile?.id !== IMPLICIT_CANONICAL_PROFILE.id
    || recipe?.profile?.sha256 !== expectedProfileSha256
    || canonicalJson(Object.keys(recipe?.profile || {}).sort()) !== canonicalJson(["id", "sha256"])
  ) {
    throw new Error("Rebuild recipe does not reference the registered implicit canonical profile");
  }
  if (
    recipe?.execution_profile?.path !== "execution-profile.json"
    || typeof recipe?.execution_profile?.id !== "string"
    || !/^[a-f0-9]{64}$/u.test(String(recipe?.execution_profile?.sha256 || ""))
    || canonicalJson(Object.keys(recipe?.execution_profile || {}).sort())
      !== canonicalJson(["id", "path", "sha256"])
  ) {
    throw new Error("Rebuild recipe does not reference a frozen implicit canonical execution profile");
  }
  const executionProfileAbsolute = await resolveWorkspaceFile(
    workspaceRoot,
    recipe.execution_profile.path,
    "recipe execution profile",
  );
  const executionProfileBytes = await fs.readFile(executionProfileAbsolute);
  if (sha256(executionProfileBytes) !== recipe.execution_profile.sha256) {
    throw new Error("Rebuild recipe execution profile digest does not match its declared input");
  }
  const executionProfile = validateExecutionProfile(JSON.parse(executionProfileBytes.toString("utf-8")));
  if (executionProfile.id !== recipe.execution_profile.id) {
    throw new Error("Rebuild recipe execution profile identity does not match its declared input");
  }
  if (
    !Array.isArray(recipe.inputs)
    || recipe.inputs.length !== 2
    || recipe.inputs[0]?.role !== "primary_implicit_source"
    || recipe.inputs[1]?.role !== "frozen_execution_profile"
  ) {
    throw new Error(
      "Rebuild recipe must declare one primary implicit source and one frozen execution profile input",
    );
  }
  const sourceRelative = portableRelativePath(recipe.inputs[0].path, "recipe source input");
  const expectedSourcePath = `source/${path.basename(sourceRelative)}`;
  if (
    sourceRelative !== expectedSourcePath
    || !/^[a-f0-9]{64}$/u.test(String(recipe.inputs[0].sha256 || ""))
    || canonicalJson(Object.keys(recipe.inputs[0]).sort()) !== canonicalJson(["path", "role", "sha256"])
  ) {
    throw new Error("Rebuild recipe source input does not match the registered implicit route contract");
  }
  const expectedOutputs = rebuildOutputs(sourceRelative);
  if (canonicalJson(recipe.outputs) !== canonicalJson(expectedOutputs)) {
    throw new Error("Rebuild recipe outputs do not match the registered implicit route contract");
  }
  const sourceAbsolute = await resolveWorkspaceFile(workspaceRoot, sourceRelative, "recipe source input");
  const sourceBytes = await fs.readFile(sourceAbsolute);
  if (sha256(sourceBytes) !== recipe.inputs[0].sha256) {
    throw new Error("Rebuild recipe source digest does not match its declared input");
  }
  if (
    recipe.inputs[1].path !== recipe.execution_profile.path
    || recipe.inputs[1].sha256 !== recipe.execution_profile.sha256
    || canonicalJson(Object.keys(recipe.inputs[1]).sort())
      !== canonicalJson(["path", "role", "sha256"])
  ) {
    throw new Error("Rebuild recipe execution profile does not match its declared input");
  }
  return buildCanonicalImplicitCad({
    workspaceDirectory: workspaceRoot,
    sourcePath: sourceRelative,
    outputDirectory,
    executionProfilePath: recipe.execution_profile.path,
  });
}
