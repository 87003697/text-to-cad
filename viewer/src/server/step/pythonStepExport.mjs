import { spawn } from "node:child_process";
import path from "node:path";

import { cadPythonExecutable, cadPythonEnv } from "./pythonStepArtifact.mjs";

// Spawn the venv Python on cadpy.step_export_target to write one model to one file at an
// explicit destination. Reuses the exact venv discovery + PYTHONPATH construction proven by
// the STEP-artifact pipeline (cadPythonExecutable / cadPythonEnv). Resolves to the Python
// module's final stdout JSON line: { ok, path, filename, format } or { ok: false, error }.
export function runPythonStepExport({
  repoRoot,
  stepPath,
  sourcePath = "",
  format,
  outPath,
  meshTolerance = null,
  meshAngularTolerance = null,
  verbose = false,
} = {}) {
  const resolvedRepoRoot = path.resolve(repoRoot || "");
  const args = [
    "-m",
    "cadpy.step_export_target",
    "--repo-root",
    resolvedRepoRoot,
    "--step",
    path.resolve(stepPath || ""),
    "--format",
    String(format),
    "--out",
    path.resolve(outPath || ""),
  ];
  const resolvedSourcePath = sourcePath ? path.resolve(sourcePath) : "";
  if (resolvedSourcePath) {
    args.push("--source-path", resolvedSourcePath);
  }
  if (meshTolerance !== null && meshTolerance !== undefined) {
    args.push("--mesh-tolerance", String(meshTolerance));
  }
  if (meshAngularTolerance !== null && meshAngularTolerance !== undefined) {
    args.push("--mesh-angular-tolerance", String(meshAngularTolerance));
  }
  if (verbose || process.env.VIEWER_STEP_EXPORT_VERBOSE === "1") {
    args.push("--verbose");
  }
  return new Promise((resolve) => {
    const child = spawn(cadPythonExecutable(resolvedRepoRoot), args, {
      cwd: resolvedRepoRoot,
      env: cadPythonEnv(resolvedRepoRoot),
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      resolve({ ok: false, error: error instanceof Error ? error.message : String(error) });
    });
    child.on("close", (code) => {
      // Parse the last JSON line regardless of exit code so a clean { ok: false, error }
      // from the Python side is surfaced verbatim; otherwise fall back to stderr/stdout.
      const lastJsonLine = stdout
        .trim()
        .split(/\r?\n/)
        .reverse()
        .find((line) => line.trim().startsWith("{"));
      if (lastJsonLine) {
        try {
          resolve(JSON.parse(lastJsonLine));
          return;
        } catch {
          // fall through to the structured failure below
        }
      }
      resolve({
        ok: false,
        exitCode: code,
        error: (stderr || stdout || `STEP export exited with code ${code}`).trim(),
      });
    });
  });
}
