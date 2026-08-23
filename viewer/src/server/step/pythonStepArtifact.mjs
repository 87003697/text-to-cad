import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Canonical cadgen package layout constants (see
// packages/cadgen/src/cadgen/catalog.py:16-17). Kept as JS constants so
// the Node backend can compute the exact expected package location
// (``<entry parent>/__cadgen__/models/<entry filename>``) and reject any
// cadgen-reported path that does not match it.
export const CADGEN_DIRNAME = "__cadgen__";
export const CADGEN_MODELS_DIRNAME = "models";
export const CADGEN_PACKAGE_DESCRIPTOR = "assembly.json";

// The Python module the viewer spawns to build one STEP entry's render
// package (formerly ``cadpy.step_artifact``; the Python package was
// renamed to ``cadgen`` and published on PyPI -- see
// packages/cadgen/README.md and viewer/server_py/backend.py).
export const CAD_STEP_ARTIFACT_MODULE = "cadgen.step_artifact_cli";

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = path.resolve(MODULE_DIR, "../../..");

function filesystemTextIdentity(value) {
  const text = String(value);
  return process.platform === "win32" ? text.toLowerCase() : text;
}

function firstExistingFile(paths) {
  return paths.find((candidate) => fs.existsSync(candidate)) || "";
}

function firstExistingDirectory(paths) {
  return paths.find((candidate) => {
    try {
      return fs.statSync(candidate).isDirectory();
    } catch {
      return false;
    }
  }) || "";
}

function findUpFile(relativePath) {
  let current = MODULE_DIR;
  for (;;) {
    const candidate = path.join(current, relativePath);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
    const next = path.dirname(current);
    if (next === current) {
      return "";
    }
    current = next;
  }
}

function findUpDirectory(relativePath) {
  let current = MODULE_DIR;
  for (;;) {
    const candidate = path.join(current, relativePath);
    if (firstExistingDirectory([candidate])) {
      return candidate;
    }
    const next = path.dirname(current);
    if (next === current) {
      return "";
    }
    current = next;
  }
}

export function cadPythonExecutable(repoRoot) {
  const configured = String(process.env.VIEWER_CAD_PYTHON || process.env.CAD_PYTHON || "").trim();
  if (configured) {
    return configured;
  }
  const resolvedRepoRoot = path.resolve(repoRoot || "");
  return firstExistingFile([
    path.join(resolvedRepoRoot, ".venv", "bin", "python"),
    path.join(process.cwd(), ".venv", "bin", "python"),
    path.join(PACKAGE_ROOT, ".venv", "bin", "python"),
    findUpFile(path.join(".venv", "bin", "python")),
  ]) || "python3";
}

export function cadPythonEnv() {
  const pythonPathEntries = [];
  for (const configured of [
    process.env.VIEWER_CAD_PYTHONPATH,
    process.env.CAD_PYTHONPATH,
    // ``VIEWER_CADPY_PYTHONPATH`` is preserved for backward compatibility
    // with pre-rename developer environments; ``cadpy`` is now published
    // as the ``cadgen`` package.
    process.env.VIEWER_CADGEN_PYTHONPATH,
    process.env.VIEWER_CADPY_PYTHONPATH,
  ]) {
    const value = String(configured || "").trim();
    if (value) {
      pythonPathEntries.push(value);
    }
  }
  for (const discovered of [
    findUpDirectory(path.join("scripts", "packages", "cadgen", "src")),
    findUpDirectory(path.join("scripts", "packages")),
    findUpDirectory(path.join("viewer", "packages", "cadgen", "src")),
    findUpDirectory(path.join("packages", "cadgen", "src")),
    path.join(PACKAGE_ROOT, "vendor", "python"),
    findUpDirectory(path.join("runtime", "vendor", "python")),
    findUpDirectory(path.join("vendor", "python")),
  ]) {
    if (discovered) {
      pythonPathEntries.push(discovered);
    }
  }
  const existingPythonPath = String(process.env.PYTHONPATH || "").trim();
  if (existingPythonPath) {
    pythonPathEntries.push(existingPythonPath);
  }
  return {
    ...process.env,
    ...(pythonPathEntries.length ? { PYTHONPATH: pythonPathEntries.join(path.delimiter) } : {}),
  };
}


/**
 * Canonical render-package directory for a STEP entry -- the Node-side
 * mirror of ``cadgen.catalog.render_package_dir``. The entry is either
 * the Python generator source (``.step.py`` -> ``<name>.py``) when
 * one exists, or the STEP file itself for imported models.
 *
 * The return value is the ONLY package location the viewer will honor
 * for a given entry. cadgen's CLI reports its own ``packagePath``, and
 * the viewer refuses to publish an entry whose reported package does
 * not match this canonical path (see ``validateCadgenPackagePath``).
 */
export function renderPackageDir(entryPath) {
  const resolved = path.resolve(entryPath);
  return path.resolve(
    path.dirname(resolved),
    CADGEN_DIRNAME,
    CADGEN_MODELS_DIRNAME,
    path.basename(resolved),
  );
}


/**
 * Fail-closed check that ``candidate`` is exactly the canonical cadgen
 * package directory for ``entryPath`` AND that neither the reported
 * path nor its resolved realpath escapes containment via a symlink,
 * junction, or reparse point.
 *
 * Two checks in one:
 *   1. ``candidate`` (as reported) must equal the canonical expected
 *      path built from ``renderPackageDir(entryPath)`` -- a lexical
 *      guard against a hostile Python worker naming an arbitrary
 *      directory.
 *   2. The package directory must not itself be a symlink and its
 *      realpath must equal its lexical resolution. A caller who
 *      planted a symlink AT the canonical location could otherwise
 *      route reads to any file on disk.
 *
 * Returns the canonical package directory on success. Throws
 * ``Error`` on any failure so callers fail closed.
 */
export function validateCadgenPackagePath(repoRoot, entryPath, candidate) {
  const expected = renderPackageDir(entryPath);
  const raw = String(candidate || "").trim();
  if (!raw) {
    throw new Error(`cadgen did not report a package path for ${entryPath}`);
  }
  const resolved = path.isAbsolute(raw)
    ? path.resolve(raw)
    : path.resolve(repoRoot, raw);
  if (filesystemTextIdentity(resolved) !== filesystemTextIdentity(expected)) {
    throw new Error(
      `cadgen reported package path ${resolved} but the canonical location is ${expected}`,
    );
  }
  // Reject a symlink/junction AT the package directory. A directory
  // reparse point would let a caller redirect the whole render tree.
  let lst;
  try {
    lst = fs.lstatSync(expected);
  } catch (error) {
    throw new Error(`cadgen package directory is missing: ${error?.message || error}`);
  }
  if (lst.isSymbolicLink()) {
    throw new Error(
      `cadgen package path ${expected} is a symbolic link; refusing to follow`,
    );
  }
  if (!lst.isDirectory()) {
    throw new Error(`cadgen package path ${expected} is not a directory`);
  }
  // Real-filesystem ancestor containment: ``fs.realpathSync`` on the
  // package dir resolves EVERY symlink/junction/reparse point along
  // the way; a matching realpath from ``repoRoot`` gives us the
  // trusted real root. The package's realpath must sit under the
  // repo's realpath -- not just under the lexical parent, which was
  // itself possibly a junction. This defeats a planted ``__cadgen__``
  // or ``__cadgen__/models`` reparse ancestor.
  const trustedRepo = fs.realpathSync(path.resolve(repoRoot));
  const realExpected = fs.realpathSync(expected);
  const rel = path.relative(trustedRepo, realExpected);
  if (rel === "" || rel === ".." || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) {
    throw new Error(
      `cadgen package directory ${expected} resolves to ${realExpected}, outside the trusted repo root ${trustedRepo}`,
    );
  }
  // Additionally require the RELATIVE portion of the lexical expected
  // path to equal the relative portion of the realpath. If any
  // ancestor is a junction that re-routes ``__cadgen__/models`` to a
  // sibling folder, the two rels differ.
  const trustedRepoLex = path.resolve(repoRoot);
  const lexRel = path.relative(trustedRepoLex, expected);
  if (filesystemTextIdentity(lexRel) !== filesystemTextIdentity(rel)) {
    throw new Error(
      `cadgen package directory ${expected} has a symlink ancestor rerouting it to ${realExpected}`,
    );
  }
  return expected;
}


/**
 * Fail-closed check for a package-relative component path from
 * ``assembly.json``. The reference must be a non-empty, non-null,
 * relative POSIX-style path that resolves strictly inside
 * ``packageDir`` LEXICALLY and remains inside ``packageDir`` under
 * ``fs.realpathSync`` (rejecting a symlink component that points at
 * arbitrary filesystem content). Absolute paths, ``..`` traversal,
 * drive-anchored spellings, and any component that is itself a
 * symbolic link/junction/reparse point are refused.
 */
export function validateComponentRef(packageDir, glbRelative) {
  const raw = String(glbRelative || "").trim();
  if (!raw) {
    throw new Error(`component descriptor is missing a glb path in ${packageDir}`);
  }
  if (raw.includes("\0")) {
    throw new Error(`component descriptor path contains a null byte in ${packageDir}`);
  }
  // Reject any absolute form (POSIX or Windows drive-anchored) before
  // resolving; ``path.resolve`` with an absolute component would ignore
  // ``packageDir`` and escape the containment root.
  if (path.isAbsolute(raw) || /^[A-Za-z]:/.test(raw) || raw.startsWith("\\\\")) {
    throw new Error(`component descriptor path ${raw} is not package-relative`);
  }
  const resolvedPackage = path.resolve(packageDir);
  const resolved = path.resolve(resolvedPackage, raw);
  const rel = path.relative(resolvedPackage, resolved);
  if (rel === "" || rel === "." || rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new Error(
      `component descriptor path ${raw} escapes package directory ${resolvedPackage}`,
    );
  }
  // Real filesystem containment: refuse a symlink/junction AT the
  // component location and require the realpath to stay inside the
  // package's own realpath. Lexical checks alone let a symlink at
  // ``components/xyz.glb -> /etc/passwd`` slip through.
  try {
    const lstat = fs.lstatSync(resolved);
    if (lstat.isSymbolicLink()) {
      throw new Error(
        `component ${raw} at ${resolved} is a symbolic link; refusing to follow`,
      );
    }
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`component GLB is missing on disk: ${resolved}`);
    }
    throw error;
  }
  const realPackage = fs.realpathSync(resolvedPackage);
  const realResolved = fs.realpathSync(resolved);
  const realRel = path.relative(realPackage, realResolved);
  if (realRel === "" || realRel.startsWith("..") || path.isAbsolute(realRel)) {
    throw new Error(
      `component ${raw} resolves to ${realResolved}, outside package realpath ${realPackage}`,
    );
  }
  return resolved;
}


/**
 * Read + validate the cadgen package descriptor at ``packageDir``.
 * Returns ``{ descriptor, components: [{ id, glbPath }, ...] }`` where
 * every ``glbPath`` is validated with ``validateComponentRef`` and
 * exists on disk. Throws a clear ``Error`` on any structural or
 * containment failure so callers fail closed rather than serving a
 * partial or forged package.
 */
export function readPackageDescriptor(packageDir) {
  const resolved = path.resolve(packageDir);
  const descriptorPath = path.join(resolved, CADGEN_PACKAGE_DESCRIPTOR);
  let raw;
  try {
    raw = fs.readFileSync(descriptorPath, "utf8");
  } catch (error) {
    throw new Error(
      `cadgen package descriptor is missing at ${descriptorPath}: ${error?.message || error}`,
    );
  }
  let descriptor;
  try {
    descriptor = JSON.parse(raw);
  } catch (error) {
    throw new Error(
      `cadgen package descriptor at ${descriptorPath} is not valid JSON: ${error?.message || error}`,
    );
  }
  if (!descriptor || typeof descriptor !== "object" || Array.isArray(descriptor)) {
    throw new Error(`cadgen package descriptor at ${descriptorPath} is not a JSON object`);
  }
  const componentsRaw = descriptor.components && typeof descriptor.components === "object"
    ? descriptor.components
    : null;
  if (!componentsRaw) {
    throw new Error(`cadgen package descriptor at ${descriptorPath} has no components map`);
  }
  const components = [];
  for (const [componentId, entry] of Object.entries(componentsRaw)) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(
        `cadgen component ${componentId} in ${descriptorPath} is not a JSON object`,
      );
    }
    const glbPath = validateComponentRef(resolved, entry.glb);
    if (!fs.existsSync(glbPath) || !fs.statSync(glbPath).isFile()) {
      throw new Error(`cadgen component GLB is missing on disk: ${glbPath}`);
    }
    components.push({ id: componentId, glbPath });
  }
  if (components.length === 0) {
    throw new Error(`cadgen package descriptor at ${descriptorPath} has zero components`);
  }
  return { descriptor, components };
}


/**
 * Run ``cadgen.step_artifact_cli`` for one STEP entry. Passing
 * ``stepExportPath`` requests a ONE-PASS STEP write from the same
 * generator evaluation that builds the render package (via
 * ``EntrySpec.step_export_path`` -- see
 * ``packages/cadgen/src/cadgen/_internal/generation.py::_generate_part_outputs``).
 * That eliminates the double-evaluation window the earlier viewer had,
 * where a nondeterministic generator could produce different STEP and
 * GLB bytes.
 *
 * The result's ``packagePath`` and every component GLB it names are
 * verified against the canonical ``renderPackageDir`` root before this
 * function returns, so no forged or out-of-tree cadgen response can
 * reach the caller.
 */
export function ensurePythonStepTopologyArtifact({
  repoRoot,
  stepPath,
  sourcePath = "",
  force = false,
  writeStepAfterArtifact = false,
  verbose = false,
  meshTolerance = null,
  meshAngularTolerance = null,
  timeoutMs = Number(process.env.VIEWER_STEP_ARTIFACT_TIMEOUT_MS || 600_000),
} = {}) {
  const resolvedRepoRoot = path.resolve(repoRoot || "");
  const resolvedStepPath = path.resolve(stepPath || "");
  const resolvedSourcePath = sourcePath ? path.resolve(sourcePath) : "";
  const args = [
    "-m",
    CAD_STEP_ARTIFACT_MODULE,
    "--repo-root",
    resolvedRepoRoot,
    "--step",
    resolvedStepPath,
  ];
  if (resolvedSourcePath) {
    args.push("--source-path", resolvedSourcePath);
  }
  if (force) {
    args.push("--force");
  }
  if (meshTolerance !== null && meshTolerance !== undefined) {
    args.push("--mesh-tolerance", String(meshTolerance));
  }
  if (meshAngularTolerance !== null && meshAngularTolerance !== undefined) {
    args.push("--mesh-angular-tolerance", String(meshAngularTolerance));
  }
  if (writeStepAfterArtifact) {
    if (!resolvedSourcePath) {
      return Promise.resolve({
        ok: false,
        stepPath: resolvedStepPath,
        error: "writeStepAfterArtifact requires a generator sourcePath",
      });
    }
    // ONE cadgen evaluation writes the STEP file AND the render
    // package. See ``cadgen.step_artifact_cli --step-export`` and
    // ``_generate_part_outputs``'s ``spec.step_export_path`` branch.
    args.push("--step-export", resolvedStepPath);
  }
  if (verbose || process.env.VIEWER_STEP_ARTIFACT_VERBOSE === "1") {
    args.push("--verbose");
  }
  const entryPath = resolvedSourcePath || resolvedStepPath;
  return new Promise((resolve) => {
    const childArgs = process.platform === "win32"
      ? ["-m", "cadgen._internal.windows_job_runner", "--", ...args]
      : args;
    const child = spawn(cadPythonExecutable(resolvedRepoRoot), childArgs, {
      cwd: resolvedRepoRoot,
      env: cadPythonEnv(resolvedRepoRoot),
      stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32",
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let killFallback = null;
    const finish = (payload) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (killFallback) clearTimeout(killFallback);
      resolve(payload);
    };
    const timeoutPayload = () => ({
      ok: false,
      stepPath: resolvedStepPath,
      error: `STEP artifact generator timed out after ${boundedTimeout}ms`,
    });
    const terminateProcessTree = () => {
      if (process.platform === "win32") {
        // The direct child is a kill-on-close Job Object wrapper.
        // Killing it closes the job handle in the kernel, which kills
        // cadgen and every descendant without depending on taskkill.
        try { child.kill("SIGKILL"); } catch {}
      } else {
        try { process.kill(-child.pid, "SIGKILL"); } catch {
          try { child.kill("SIGKILL"); } catch {}
        }
      }
    };
    const boundedTimeout = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 600_000;
    const timer = setTimeout(() => {
      timedOut = true;
      terminateProcessTree();
      // ``close`` normally follows once the whole tree releases its
      // pipes. Keep the public promise bounded even if OS termination
      // itself fails unexpectedly.
      killFallback = setTimeout(() => finish(timeoutPayload()), 5_000);
    }, boundedTimeout);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => {
      finish({
        ok: false,
        stepPath: resolvedStepPath,
        error: error instanceof Error ? error.message : String(error),
      });
    });
    child.on("close", (code) => {
      if (timedOut) {
        finish(timeoutPayload());
        return;
      }
      const lastJsonLine = stdout
        .split(/\r?\n/)
        .reverse()
        .find((line) => line.trim().startsWith("{"));
      if (code !== 0 || !lastJsonLine) {
        finish({
          ok: false,
          stepPath: resolvedStepPath,
          exitCode: code,
          error: (stderr || stdout || `STEP artifact generator exited with code ${code}`).trim(),
        });
        return;
      }
      let payload = null;
      try {
        payload = JSON.parse(lastJsonLine);
      } catch (error) {
        finish({
          ok: false,
          stepPath: resolvedStepPath,
          error: `cadgen returned unreadable JSON: ${error?.message || error}`,
        });
        return;
      }
      if (!payload?.ok) {
        finish(payload);
        return;
      }
      let packageDir;
      let components;
      try {
        packageDir = validateCadgenPackagePath(
          resolvedRepoRoot,
          entryPath,
          payload.packagePath,
        );
        // Reading the descriptor here validates every component
        // reference against ``packageDir`` (see
        // ``readPackageDescriptor``); a broken package is rejected
        // before any URL is exposed to the caller.
        ({ components } = readPackageDescriptor(packageDir));
      } catch (error) {
        finish({
          ok: false,
          stepPath: resolvedStepPath,
          error: error?.message || String(error),
        });
        return;
      }
      payload.stepPath = resolvedStepPath;
      payload.packageDir = packageDir;
      payload.components = components;
      finish(payload);
    });
  });
}
