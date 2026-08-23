import fs from "node:fs";
import path from "node:path";

import {
  VIEWER_SKIPPED_DIRECTORIES,
  bindCadgenPackageIdentity,
  filesystemPathIdentity,
  normalizeViewerRootDir,
  resolveTrustedMetadataPath,
  resolveViewerRoot,
  validateStepTopologyArtifact,
} from "../catalog/cadDirectoryScanner.mjs";
import { readTextToCadStepMetadataFile } from "./stepMetadata.mjs";
import {
  ensurePythonStepTopologyArtifact,
  renderPackageDir,
} from "./pythonStepArtifact.mjs";

const STEP_SUFFIXES = new Set([".step", ".stp"]);

function isHiddenDirectoryName(name) {
  return String(name || "").startsWith(".");
}

function isPerStepViewerDirectoryName(name) {
  const normalized = String(name || "").toLowerCase();
  return normalized.startsWith(".") && (normalized.endsWith(".step") || normalized.endsWith(".stp"));
}

function isPerUrdfViewerDirectoryName(name) {
  const normalized = String(name || "").toLowerCase();
  return normalized.startsWith(".") && normalized.endsWith(".urdf");
}

function shouldSkipDirectory(name) {
  return (
    VIEWER_SKIPPED_DIRECTORIES.has(name) ||
    isHiddenDirectoryName(name) ||
    isPerStepViewerDirectoryName(name) ||
    isPerUrdfViewerDirectoryName(name)
  );
}

function generatorFromStepMetadata(stepPath, { trustedRoot } = {}) {
  // A generated STEP records the generator that wrote it in a
  // DESCRIPTIVE_REPRESENTATION_ITEM keyed ``cadgen:sourcePath`` (see
  // ``packages/cadgen/src/cadgen/_internal/step_metadata.py``). The
  // path is relative to the STEP's own directory, BUT that field
  // lives inside the STEP -- an attacker who can write a STEP file
  // controls it. Fail closed on:
  //   * absolute paths (POSIX ``/``, drive-letter ``C:``, UNC ``\\``)
  //   * null bytes
  //   * ``..`` traversal that escapes the trusted root
  //   * symlinks (metadata itself OR any ancestor along realpath)
  //   * anything outside ``trustedRoot``'s realpath.
  // A trusted root MUST be provided or metadata is refused entirely.
  if (!trustedRoot) {
    return "";
  }
  try {
    if (!fs.existsSync(stepPath) || !fs.statSync(stepPath).isFile()) {
      return "";
    }
    const metadata = readTextToCadStepMetadataFile(stepPath);
    const declaredSource = String(metadata?.sourcePath || "").trim();
    if (!declaredSource) {
      return "";
    }
    return resolveTrustedMetadataPath(stepPath, declaredSource, trustedRoot);
  } catch {
    return "";
  }
}


function sameStemPythonGeneratorPath(stepPath) {
  const extension = path.extname(stepPath).toLowerCase();
  if (!STEP_SUFFIXES.has(extension)) {
    return "";
  }
  const candidatePath = path.join(
    path.dirname(stepPath),
    `${path.basename(stepPath, extension)}.py`,
  );
  try {
    return /\bgen_step\s*\(/.test(fs.readFileSync(candidatePath, "utf-8"))
      ? candidatePath
      : "";
  } catch {
    return "";
  }
}

function collectStepFiles(rootPath, trustedRoot) {
  const byLogicalStep = new Map();
  const add = (stepPath, sourcePath = "") => {
    const logicalStepPath = path.resolve(stepPath);
    const identity = filesystemPathIdentity(logicalStepPath);
    const previous = byLogicalStep.get(identity);
    if (previous?.ambiguous) return;
    if (previous?.sourcePath && sourcePath && previous.sourcePath !== sourcePath) {
      byLogicalStep.set(identity, { stepPath: logicalStepPath, ambiguous: true });
      return;
    }
    if (!previous || sourcePath) {
      byLogicalStep.set(identity, { stepPath: logicalStepPath, sourcePath });
    }
  };
  const walk = (directory) => {
    let entries = [];
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (!shouldSkipDirectory(entry.name)) walk(entryPath);
        continue;
      }
      if (!entry.isFile()) continue;
      const extension = path.extname(entry.name).toLowerCase();
      if (STEP_SUFFIXES.has(extension)) {
        add(entryPath);
        continue;
      }
      if (extension !== ".py" || !/\bgen_step\s*\(/.test(fs.readFileSync(entryPath, "utf-8"))) {
        continue;
      }
      const binding = bindCadgenPackageIdentity({
        packageDir: renderPackageDir(entryPath),
        trustedRoot,
        entryPath,
      });
      const logicalStepPath = binding?.sourceKind === "python"
        ? binding.logicalStepPath
        : path.join(path.dirname(entryPath), `${path.basename(entryPath, extension)}.step`);
      const relative = path.relative(rootPath, logicalStepPath);
      if (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)) {
        add(logicalStepPath, entryPath);
      }
    }
  };
  walk(rootPath);
  return [...byLogicalStep.values()];
}

function canBuildStepArtifact(artifact) {
  const code = String(artifact?.stepArtifact?.error?.code || "");
  return !artifact?.stepArtifact?.ok && [
    "missing_package",
    "missing_glb",
    "missing_step_topology",
    "missing_edge_topology",
    "missing_surface_edge_attributes",
    "missing_selector_topology",
    "missing_source_path",
    "missing_source_closure",
    "missing_step_hash",
    "stale_source_closure",
    "stale_step_artifact",
    "unsupported_step_topology",
    "unsupported_package_schema",
  ].includes(code);
}


/**
 * Build one STEP entry's render package via cadgen. Returns the
 * canonical package directory and the validated list of components
 * (all containment-checked in ``ensurePythonStepTopologyArtifact``).
 *
 * ``writeStepAfterArtifact`` requests a ONE-PASS STEP write from the
 * same generator evaluation that builds the package. There is no
 * second Python subprocess, no post-hoc hash rewrite, and no adjacent
 * ``.identity.json`` sidecar; identity lives inside the descriptor
 * cadgen wrote alongside the components, which the scanner reads
 * directly.
 */
export async function compileStepTopologyArtifact({
  repoRoot,
  stepPath,
  sourcePath = "",
  force = true,
  writeStepAfterArtifact = false,
  meshTolerance = null,
  meshAngularTolerance = null,
} = {}) {
  if (!repoRoot) {
    throw new Error("repoRoot is required");
  }
  if (!stepPath) {
    throw new Error("stepPath is required");
  }
  const resolvedRepoRoot = path.resolve(repoRoot);
  const resolvedStepPath = path.resolve(stepPath);
  const resolvedSourcePath = sourcePath ? path.resolve(sourcePath) : "";
  const pythonResult = await ensurePythonStepTopologyArtifact({
    repoRoot: resolvedRepoRoot,
    stepPath: resolvedStepPath,
    sourcePath: resolvedSourcePath,
    force,
    writeStepAfterArtifact,
    meshTolerance,
    meshAngularTolerance,
  });
  if (!pythonResult?.ok) {
    throw new Error(pythonResult?.error || `Failed to generate STEP topology artifact: ${resolvedStepPath}`);
  }
  return {
    ...pythonResult,
    ok: true,
    stepPath: resolvedStepPath,
    packageDir: pythonResult.packageDir,
    components: pythonResult.components,
  };
}


export async function ensureStepTopologyArtifact({
  repoRoot,
  stepPath,
  sourcePath = "",
  force = false,
  writeStepAfterArtifact = false,
  meshTolerance = null,
  meshAngularTolerance = null,
} = {}) {
  const resolvedRepoRoot = path.resolve(repoRoot);
  const resolvedStepPath = path.resolve(stepPath);
  const resolvedSourcePath = sourcePath ? path.resolve(sourcePath) : "";
  const stepExists = fs.existsSync(resolvedStepPath);
  // Prefer the caller-supplied generator; otherwise read the STEP's
  // own embedded metadata (bounded to ``repoRoot``) to find the
  // declared generator. Only if the STEP is silent or absent do we
  // fall back to same-stem inference. For imported STEP with no
  // generator, ``inferredSourcePath`` stays empty and cadgen enters
  // imported-STEP mode.
  const inferredSourcePath = resolvedSourcePath
    || generatorFromStepMetadata(resolvedStepPath, { trustedRoot: resolvedRepoRoot })
    || (!stepExists || writeStepAfterArtifact ? sameStemPythonGeneratorPath(resolvedStepPath) : "");
  const current = validateStepTopologyArtifact({
    repoRoot: resolvedRepoRoot,
    sourcePath: resolvedStepPath,
    entryPath: inferredSourcePath || resolvedStepPath,
  });
  const hasMeshOverride = (
    (meshTolerance !== null && meshTolerance !== undefined) ||
    (meshAngularTolerance !== null && meshAngularTolerance !== undefined)
  );
  const descriptor = current.topology?.index && typeof current.topology.index === "object"
    ? current.topology.index
    : null;
  const isPythonPackage = String(descriptor?.sourceKind || "").trim().toLowerCase() === "python";
  // Cadgen's semantic AST closure hasher (see
  // ``packages/cadgen/src/cadgen/_internal/source_hash.py``) is the
  // only authoritative freshness gate for python-generated packages.
  // A legacy/truncated/tampered descriptor without ``sourceClosureFiles``
  // must NOT allow the JS layer to short-circuit; cadgen's
  // ``_manifest_source_closure_unchanged`` treats missing provenance
  // as stale and rebuilds. So EVERY python package -- closure
  // fields present or not -- routes through cadgen's freshness gate.
  // Imported STEP packages keep the fast path (their ``stepHash``
  // vs on-disk sha256 is authoritative and re-checked by the
  // scanner).
  const mustDelegateToCadgen = isPythonPackage;
  if (
    !force &&
    !hasMeshOverride &&
    !mustDelegateToCadgen &&
    (
      current.stepArtifact?.ok ||
      (!current.stepArtifact?.ok && !canBuildStepArtifact(current))
    )
  ) {
    return {
      ok: Boolean(current.stepArtifact?.ok),
      skipped: true,
      reason: current.stepArtifact?.error?.code || "",
      stepPath: resolvedStepPath,
      packageDir: current.packageDir,
      validation: current.stepArtifact,
    };
  }
  // ``writeStepAfterArtifact`` only applies to generator mode -- an
  // imported STEP is already on disk and there is nothing to export.
  // A Python package is publishable only when its descriptor binds to a STEP hash.
  // Always request the STEP from the same generator evaluation; package-only Python
  // output cannot satisfy the scanner's fail-closed identity contract.
  const shouldWriteStep = Boolean(inferredSourcePath);
  const result = await compileStepTopologyArtifact({
    repoRoot: resolvedRepoRoot,
    stepPath: resolvedStepPath,
    sourcePath: inferredSourcePath || undefined,
    force,
    writeStepAfterArtifact: shouldWriteStep,
    meshTolerance,
    meshAngularTolerance,
  });
  const next = validateStepTopologyArtifact({
    repoRoot: resolvedRepoRoot,
    sourcePath: resolvedStepPath,
    entryPath: inferredSourcePath || resolvedStepPath,
  });
  return {
    ...result,
    ok: Boolean(next.stepArtifact?.ok),
    validation: next.stepArtifact,
    stepWrite: shouldWriteStep && fs.existsSync(resolvedStepPath)
      ? { ok: true, status: "complete", path: resolvedStepPath }
      : undefined,
  };
}


export async function ensureStepArtifactsForCatalog({
  repoRoot,
  rootDir = "",
  force = false,
  meshTolerance = null,
  meshAngularTolerance = null,
} = {}) {
  const resolvedRepoRoot = path.resolve(repoRoot);
  const resolvedRootDir = normalizeViewerRootDir(rootDir);
  const { rootPath } = resolveViewerRoot(resolvedRepoRoot, resolvedRootDir);
  const results = [];
  for (const candidate of collectStepFiles(rootPath, resolvedRepoRoot)) {
    const { stepPath, sourcePath = "", ambiguous = false } = candidate;
    if (ambiguous) {
      results.push({
        ok: false,
        stepPath,
        error: `Multiple Python generators claim logical STEP ${stepPath}`,
      });
      continue;
    }
    try {
      results.push(await ensureStepTopologyArtifact({
        repoRoot: resolvedRepoRoot,
        stepPath,
        sourcePath,
        force,
        meshTolerance,
        meshAngularTolerance,
      }));
    } catch (error) {
      results.push({
        ok: false,
        stepPath,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return results;
}


// Re-export the canonical package-dir helper so tests and callers can
// find a package location without re-implementing the constants.
export { renderPackageDir };
