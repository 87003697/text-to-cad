import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  CAD_STEP_ARTIFACT_MODULE,
  CADGEN_DIRNAME,
  CADGEN_MODELS_DIRNAME,
  CADGEN_PACKAGE_DESCRIPTOR,
  cadPythonEnv,
  cadPythonExecutable,
  ensurePythonStepTopologyArtifact,
  readPackageDescriptor,
  renderPackageDir,
  validateCadgenPackagePath,
  validateComponentRef,
} from "./pythonStepArtifact.mjs";

// Regression: run 32590267019 failed with
// ``ModuleNotFoundError: No module named 'cadpy'`` because the viewer's
// Python step-artifact seam still invoked the pre-rename module name.
// The Python package was renamed from ``cadpy`` to ``cadgen`` and
// published on PyPI (see packages/cadgen/README.md); tests must lock
// in the current name so a future rename cannot silently reintroduce
// the removed one.
test("Python STEP artifact module uses the current cadgen name", () => {
  assert.equal(CAD_STEP_ARTIFACT_MODULE, "cadgen.step_artifact_cli");
  assert.ok(
    !CAD_STEP_ARTIFACT_MODULE.startsWith("cadpy."),
    "must not reintroduce removed cadpy package name",
  );
});

test("Python STEP artifact timeout kills descendants that inherit output pipes", async (t) => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-step-timeout-"));
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  const generatorPath = path.join(tmp, "model.py");
  const stepPath = path.join(tmp, "model.step");
  const pidPath = path.join(tmp, "descendant.pid");
  fs.writeFileSync(generatorPath, [
    "from build123d import Box",
    "from pathlib import Path",
    "import subprocess, sys",
    `p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])`,
    `Path(${JSON.stringify(pidPath)}).write_text(str(p.pid))`,
    "def gen_step():",
    "    return Box(1, 1, 1)",
    "",
  ].join("\n"));

  const started = Date.now();
  const result = await ensurePythonStepTopologyArtifact({
    repoRoot: tmp,
    stepPath,
    sourcePath: generatorPath,
    force: true,
    writeStepAfterArtifact: true,
    timeoutMs: 8_000,
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /timed out/);
  assert.ok(Date.now() - started < 14_000, "timeout must remain bounded when a descendant owns the pipes");
  assert.equal(fs.existsSync(pidPath), true, "fixture descendant must have started");
  const descendantPid = Number(fs.readFileSync(pidPath, "utf8"));
  let alive = true;
  for (let attempt = 0; attempt < 20 && alive; attempt += 1) {
    try {
      process.kill(descendantPid, 0);
      await new Promise((resolve) => setTimeout(resolve, 100));
    } catch {
      alive = false;
    }
  }
  assert.equal(alive, false, "timed-out descendant process must be terminated with its parent");
});

// The setup-deps environment installs ``packages/cadgen`` as an editable
// package (requirements-dev.txt line ``--editable ./packages/cadgen``),
// so ``python -m cadgen.step_artifact_cli --help`` must resolve without
// needing a manual PYTHONPATH tweak.
test("cadgen.step_artifact_cli is resolvable under cadPythonEnv", () => {
  const result = spawnSync(
    cadPythonExecutable(process.cwd()),
    ["-c", "import importlib; importlib.import_module('cadgen.step_artifact_cli')"],
    {
      cwd: process.cwd(),
      env: cadPythonEnv(process.cwd()),
      encoding: "utf8",
    },
  );
  assert.equal(
    result.status,
    0,
    `cadgen.step_artifact_cli must be importable under cadPythonEnv.\nstdout: ${result.stdout}\nstderr: ${result.stderr}`,
  );
});

test("renderPackageDir mirrors cadgen.catalog.render_package_dir", () => {
  const dir = "/repo/models";
  const entry = path.join(dir, "block.step");
  assert.equal(
    renderPackageDir(entry),
    path.resolve(dir, CADGEN_DIRNAME, CADGEN_MODELS_DIRNAME, "block.step"),
  );
});

test("validateCadgenPackagePath rejects a package outside the canonical location", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-package-canon-"));
  try {
    const entry = path.join(tmp, "block.py");
    fs.writeFileSync(entry, "def gen_step(): ...\n");
    const wrongPackage = path.join(tmp, "not_the_canonical_place");
    assert.throws(
      () => validateCadgenPackagePath(tmp, entry, wrongPackage),
      /canonical location/,
    );
    const canonical = renderPackageDir(entry);
    fs.mkdirSync(canonical, { recursive: true });
    assert.equal(
      validateCadgenPackagePath(tmp, entry, canonical),
      canonical,
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("validateCadgenPackagePath rejects a symbolic-link package directory", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-package-symlink-"));
  try {
    const entry = path.join(tmp, "block.py");
    fs.writeFileSync(entry, "def gen_step(): ...\n");
    const canonical = renderPackageDir(entry);
    fs.mkdirSync(path.dirname(canonical), { recursive: true });
    const attacker = fs.mkdtempSync(path.join(os.tmpdir(), "cad-attacker-"));
    try {
      try {
        fs.symlinkSync(attacker, canonical, "dir");
      } catch (error) {
        // Directory symlinks require SeCreateSymbolicLink on Windows;
        // skip on hosts that refuse them.
        return;
      }
      assert.throws(
        () => validateCadgenPackagePath(tmp, entry, canonical),
        /symbolic link/,
      );
    } finally {
      fs.rmSync(attacker, { recursive: true, force: true });
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("validateCadgenPackagePath accepts a contained directory name beginning with two dots", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-package-dotdot-prefix-"));
  try {
    const entry = path.join(tmp, "..models", "widget.py");
    fs.mkdirSync(path.dirname(entry), { recursive: true });
    fs.writeFileSync(entry, "def gen_step(): ...\n");
    const canonical = renderPackageDir(entry);
    fs.mkdirSync(canonical, { recursive: true });
    assert.equal(validateCadgenPackagePath(tmp, entry, canonical), canonical);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("Windows validateCadgenPackagePath accepts canonical paths with case-variant spelling", {
  skip: process.platform === "win32" ? false : "Windows filesystem casing regression",
}, () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-package-case-"));
  try {
    const entry = path.join(tmp, "model.py");
    fs.writeFileSync(entry, "def gen_step(): ...\n");
    const canonical = renderPackageDir(entry);
    fs.mkdirSync(canonical, { recursive: true });
    const caseVariantRoot = tmp.toUpperCase();
    const caseVariantEntry = path.join(caseVariantRoot, "MODEL.PY");
    assert.equal(
      validateCadgenPackagePath(caseVariantRoot, caseVariantEntry, canonical),
      renderPackageDir(caseVariantEntry),
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("validateComponentRef rejects absolute and traversal references", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-component-ref-"));
  try {
    const packageDir = path.join(tmp, "__cadgen__/models/part.py");
    fs.mkdirSync(path.join(packageDir, "components"), { recursive: true });
    // Lexical rejections (fail before touching the filesystem).
    assert.throws(() => validateComponentRef(packageDir, "/etc/passwd"), /package-relative/);
    assert.throws(() => validateComponentRef(packageDir, "..\\..\\evil.glb"), /escapes package/);
    assert.throws(() => validateComponentRef(packageDir, "../elsewhere.glb"), /escapes package/);
    assert.throws(() => validateComponentRef(packageDir, ""), /missing a glb path/);
    assert.throws(() => validateComponentRef(packageDir, "with\0null.glb"), /null byte/);
    assert.throws(() => validateComponentRef(packageDir, "C:/absolute.glb"), /package-relative/);
    // A well-formed package-relative ref requires the component to
    // actually exist on disk (the validator now runs realpath).
    fs.writeFileSync(path.join(packageDir, "components/abc.glb"), "glb");
    assert.equal(
      validateComponentRef(packageDir, "components/abc.glb"),
      path.join(packageDir, "components/abc.glb"),
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("validateComponentRef rejects a symlinked component pointing outside the package", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-component-symlink-"));
  try {
    const packageDir = path.join(tmp, "pkg");
    fs.mkdirSync(path.join(packageDir, "components"), { recursive: true });
    const outside = path.join(tmp, "outside.glb");
    fs.writeFileSync(outside, "outside glb");
    const linkPath = path.join(packageDir, "components/evil.glb");
    fs.symlinkSync(outside, linkPath, "file");
    assert.throws(
      () => validateComponentRef(packageDir, "components/evil.glb"),
      /symbolic link/,
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("readPackageDescriptor rejects a descriptor whose component GLB escapes", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cad-package-escape-"));
  try {
    const entry = path.join(tmp, "block.py");
    fs.writeFileSync(entry, "def gen_step(): ...\n");
    const packageDir = renderPackageDir(entry);
    fs.mkdirSync(packageDir, { recursive: true });
    fs.mkdirSync(path.join(packageDir, "components"), { recursive: true });
    // A benign component + a malicious traversal component. Reading
    // the descriptor MUST fail closed rather than silently exposing the
    // out-of-package GLB.
    fs.writeFileSync(path.join(packageDir, "components", "safe.glb"), "glTF stub\n");
    // Plant an out-of-package target so the reference is not just
    // syntactically bad but points at real content.
    const outside = path.join(tmp, "outside.glb");
    fs.writeFileSync(outside, "glTF outside\n");
    fs.writeFileSync(
      path.join(packageDir, CADGEN_PACKAGE_DESCRIPTOR),
      JSON.stringify({
        components: {
          safe: { glb: "components/safe.glb" },
          evil: { glb: "../../../outside.glb" },
        },
      }),
    );
    assert.throws(
      () => readPackageDescriptor(packageDir),
      /escapes package directory/,
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
