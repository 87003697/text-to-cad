from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT_CLOSURE = REPO_ROOT / "packages" / "agent_runtime" / "project_closure.py"


def _load_project_closure():
    spec = importlib.util.spec_from_file_location("project_closure", PROJECT_CLOSURE)
    if spec is None or spec.loader is None:
        raise RuntimeError("project closure module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AgentRuntimeProjectClosureTests(unittest.TestCase):
    def test_browser_free_meshshot_is_a_real_closed_distribution(self) -> None:
        closure = _load_project_closure()
        with tempfile.TemporaryDirectory(prefix="meshshot-agent-runtime-") as directory:
            output = Path(directory)
            record = closure.generate_meshshot_distribution(REPO_ROOT, output)

            self.assertEqual(record["distribution"], "meshshot-agent-runtime")
            self.assertEqual(record["importName"], "meshshot")
            self.assertEqual(record["publicCallable"], "meshshot.render_residual_preview")
            self.assertEqual(record["runtimeDependencies"], ["Pillow==12.2.0"])
            source_root = output / "meshshot-agent-runtime"
            pyproject = (source_root / "pyproject.toml").read_text(encoding="utf-8")
            self.assertNotIn("playwright", pyproject.lower())
            self.assertEqual(
                sorted(
                    path.relative_to(source_root).as_posix()
                    for path in source_root.rglob("*")
                    if path.is_file()
                ),
                [entry["path"] for entry in record["files"]],
            )
            for entry in record["files"]:
                payload = (source_root / entry["path"]).read_bytes()
                self.assertEqual(entry["bytes"], len(payload))
                self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())

            joined = b"\n".join(
                path.read_bytes().lower()
                for path in source_root.rglob("*")
                if path.is_file()
            )
            for forbidden in (
                b"playwright",
                b"chromium",
                b"sync_playwright",
                b"node_modules",
            ):
                self.assertNotIn(forbidden, joined)
            self.assertFalse((source_root / "src/meshshot/runtime").exists())

            wheel_paths = []
            for build_index in (1, 2):
                wheel_dir = output / f"wheels-{build_index}"
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "wheel",
                        "--no-build-isolation",
                        "--no-deps",
                        "--wheel-dir",
                        str(wheel_dir),
                        str(source_root),
                    ],
                    env={
                        **os.environ,
                        "SOURCE_DATE_EPOCH": "1755302400",
                        "PYTHONHASHSEED": "0",
                    },
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertIn("Successfully built", completed.stdout)
                wheel_paths.append(next(wheel_dir.glob("meshshot_agent_runtime-*.whl")))
            self.assertEqual(wheel_paths[0].read_bytes(), wheel_paths[1].read_bytes())
            wheel = wheel_paths[0]
            wheel_record = closure.audit_meshshot_wheel(wheel)
            self.assertEqual(
                wheel_record["wheelSha256"],
                hashlib.sha256(wheel.read_bytes()).hexdigest(),
            )
            self.assertTrue(wheel_record["browserInventoryEmpty"])
            self.assertEqual(
                wheel_record["browserDenial"],
                {
                    "playwrightPackageOrImportAbsent": True,
                    "browserExecutableAbsent": True,
                    "browserCachePathAbsent": True,
                    "localBrowserRuntimeAbsent": True,
                },
            )
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
                self.assertIn("meshshot/__init__.py", names)
                self.assertNotIn("meshshot/renderer.py", names)

            target = output / "installed"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--target",
                    str(target),
                    str(wheel),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import meshshot; "
                    "assert meshshot.render_residual_preview.__module__ "
                    "== 'meshshot.broker_client'; "
                    "assert set(meshshot.__all__) == "
                    "{'LoadedProfile','MeshGeometry','MeshshotError',"
                    "'RenderedPreview','load_profile','render_residual_preview'}",
                ],
                env={**os.environ, "PYTHONPATH": str(target)},
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(probe.stderr, "")
            fail_closed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import meshshot; "
                    "g=meshshot.MeshGeometry([[0,0,0],[1,0,0],[0,1,0]],"
                    "[[0,1,2]]); "
                    "\ntry: meshshot.render_residual_preview(g,g)"
                    "\nexcept meshshot.MeshshotError as e: "
                    "assert str(e) == 'formal browser authority file is required'"
                    "\nelse: raise AssertionError('missing authority did not fail closed')",
                ],
                env={**os.environ, "PYTHONPATH": str(target)},
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(fail_closed.stderr, "")

    def test_canonical_implicit_subset_is_exact_and_executes_cup_build(self) -> None:
        closure = _load_project_closure()
        with tempfile.TemporaryDirectory(prefix="implicit-agent-runtime-") as directory:
            output = Path(directory)
            record = closure.generate_implicit_runtime(REPO_ROOT, output)
            runtime = output / "implicit-runtime"
            self.assertEqual(record["schema"], "text-to-cad.implicit-runtime-files/1")
            self.assertEqual(record["entrypoint"], "scripts/canonical-build.mjs")
            self.assertEqual(record["bundlePath"], "implicit-runtime")
            self.assertEqual(record["bundleDigest"], record["filesDigest"])
            self.assertEqual(record["fileManifestDigest"], record["filesDigest"])
            self.assertEqual(record["fileCount"], 11)
            self.assertEqual(record["runtimeDependencies"], [])
            paths = [entry["path"] for entry in record["files"]]
            self.assertEqual(paths, sorted(paths))
            self.assertNotIn("package.json", paths)
            joined = b"\n".join((runtime / path).read_bytes().lower() for path in paths)
            for forbidden in (b"playwright", b"chromium", b"snapshot", b"browser.js"):
                self.assertNotIn(forbidden, joined)
            manifest_bytes = (runtime / "implicit-runtime-manifest.json").read_bytes()
            self.assertEqual(manifest_bytes, closure.canonical_json_bytes(record) + b"\n")

            workspace = output / "workspace"
            workspace.mkdir()
            shutil.copyfile(
                REPO_ROOT / "models/agent-runtime/cup_cup_033/source/cup_cup_033.implicit.js",
                workspace / "cup.implicit.js",
            )
            subprocess.run(
                [
                    "node",
                    str(runtime / "scripts/canonical-build.mjs"),
                    "--source", "cup.implicit.js",
                    "--output-dir", "built",
                ],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            expected = json.loads((REPO_ROOT / "models/agent-runtime/cup_cup_033/canonical-build-record.json").read_bytes())
            build = json.loads((workspace / "built/build.json").read_bytes())
            self.assertEqual(build["files"][1]["sha256"], expected["measurementGlbDigest"].removeprefix("sha256:"))

    def test_meshscope_audit_rejects_non_linux_or_non_native_wheels(self) -> None:
        closure = _load_project_closure()
        with tempfile.TemporaryDirectory(prefix="meshscope-wheel-audit-") as directory:
            root = Path(directory)
            wrong_platform = root / "meshscope-0.1.0-cp312-cp312-macosx_14_0_arm64.whl"
            wrong_platform.write_bytes(b"not a wheel")
            with self.assertRaisesRegex(closure.ProjectClosureError, "linux_x86_64"):
                closure.audit_meshscope_wheel(wrong_platform)

            pure = root / "meshscope-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(pure, "w") as archive:
                archive.writestr("meshscope/__init__.py", b"")
            with self.assertRaisesRegex(closure.ProjectClosureError, "cp312"):
                closure.audit_meshscope_wheel(pure)

    def test_project_manifest_closes_all_project_artifacts(self) -> None:
        closure = _load_project_closure()
        with tempfile.TemporaryDirectory(prefix="agent-project-closure-") as directory:
            output = Path(directory)
            meshshot = closure.generate_meshshot_distribution(REPO_ROOT, output)
            implicit = closure.generate_implicit_runtime(REPO_ROOT, output)
            meshshot = closure.assemble_python_artifact(
                meshshot,
                {
                    "distribution": "meshshot-agent-runtime",
                    "version": "0.1.0",
                    "wheelPath": "wheels/meshshot_agent_runtime-0.1.0-py3-none-any.whl",
                    "wheelSha256": "e" * 64,
                    "wheelBytes": 456,
                    "browserInventoryEmpty": True,
                    "browserDenial": {
                        "playwrightPackageOrImportAbsent": True,
                        "browserExecutableAbsent": True,
                        "browserCachePathAbsent": True,
                        "localBrowserRuntimeAbsent": True,
                    },
                },
            )
            manifest = closure.build_project_manifest(
                meshshot=meshshot,
                meshscope={
                    "distribution": "meshscope",
                    "version": "0.1.0",
                    "wheelPath": "wheels/meshscope-0.1.0-cp312-cp312-linux_x86_64.whl",
                    "wheelSha256": "a" * 64,
                    "wheelBytes": 123,
                    "sourceTreeDigest": "b" * 64,
                    "fileManifestDigest": "c" * 64,
                    "nativeAuditDigest": "d" * 64,
                    "nativeConformanceDigest": "f" * 64,
                    "needed": ["libc.so.6", "libgcc_s.so.1", "libstdc++.so.6"],
                },
                implicit=implicit,
            )
            self.assertEqual(set(manifest), {"schema", "platform", "pythonArtifacts", "implicitRuntime"})
            self.assertEqual(manifest["platform"], {"architecture": "amd64", "os": "linux", "pythonAbi": "cp312"})
            self.assertEqual([item["distribution"] for item in manifest["pythonArtifacts"]], ["meshscope", "meshshot-agent-runtime"])
            encoded = closure.canonical_json_bytes(manifest)
            self.assertEqual(encoded, closure.canonical_json_bytes(json.loads(encoded)))


if __name__ == "__main__":
    unittest.main()
