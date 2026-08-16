from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT_CLOSURE = REPO_ROOT / "packages" / "agent_runtime" / "project_closure.py"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _canonical_digest(label: str) -> str:
    return f"sha256:{_digest(label)}"


def _load_project_closure():
    spec = importlib.util.spec_from_file_location("project_closure", PROJECT_CLOSURE)
    if spec is None or spec.loader is None:
        raise RuntimeError("project closure module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rewrite_wheel(
    source: Path,
    destination: Path,
    *,
    replacements: dict[str, bytes] | None = None,
    extra: tuple[str, bytes] | None = None,
    duplicate: str | None = None,
    executable: str | None = None,
    record_without: str | None = None,
    rebuild_record: bool = False,
) -> None:
    replacements = replacements or {}
    with zipfile.ZipFile(source) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    values = {info.filename: replacements.get(info.filename, payload) for info, payload in entries}
    record_name = next(name for name in values if name.endswith(".dist-info/RECORD"))
    if extra is not None:
        values[extra[0]] = extra[1]
    if rebuild_record:
        rows = []
        for name, payload in values.items():
            if name == record_name or name == record_without:
                continue
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            rows.append((name, f"sha256={digest}", str(len(payload))))
        rows.append((record_name, "", ""))
        stream = StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows(rows)
        values[record_name] = stream.getvalue().encode("utf-8")
    with zipfile.ZipFile(destination, "w") as output:
        for original, _ in entries:
            info = zipfile.ZipInfo(original.filename, original.date_time)
            info.compress_type = original.compress_type
            info.external_attr = original.external_attr
            info.create_system = original.create_system
            if executable == original.filename:
                info.external_attr = (stat.S_IFREG | 0o755) << 16
            output.writestr(info, values[original.filename])
        if extra is not None:
            info = zipfile.ZipInfo(extra[0], entries[0][0].date_time)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            output.writestr(info, extra[1])
        if duplicate is not None:
            output.writestr(duplicate, values[duplicate])


class AgentRuntimeProjectClosureTests(unittest.TestCase):
    def test_project_closure_uses_the_single_shared_canonical_json_seam(self) -> None:
        closure = _load_project_closure()
        from scripts.pilot.agent_runtime import canonical_json_bytes

        self.assertIs(closure.canonical_json_bytes, canonical_json_bytes)

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
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("Successfully built", completed.stdout)
                wheel_paths.append(next(wheel_dir.glob("meshshot_agent_runtime-*.whl")))
            self.assertEqual(wheel_paths[0].read_bytes(), wheel_paths[1].read_bytes())
            wheel = wheel_paths[0]
            record = closure.parse_canonical_json(
                closure.canonical_json_bytes(record)
            )
            wheel_record = closure.audit_meshshot_wheel(wheel, record)
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

            attacks = {
                "rogue member": {"extra": ("meshshot/rogue.py", b"pass\n")},
                "duplicate member": {"duplicate": "meshshot/profile.py"},
                "executable member": {"executable": "meshshot/profile.py"},
                "stale RECORD": {
                    "replacements": {"meshshot/profile.py": b"tampered = True\n"}
                },
                "incomplete RECORD": {
                    "record_without": "meshshot/profile.py",
                    "rebuild_record": True,
                },
            }
            for label, options in attacks.items():
                with self.subTest(attack=label):
                    attacked = output / f"{label.replace(' ', '-')}.whl"
                    _rewrite_wheel(wheel, attacked, **options)
                    attacked = attacked.with_name(wheel.name)
                    shutil.move(output / f"{label.replace(' ', '-')}.whl", attacked)
                    with self.assertRaises(closure.ProjectClosureError):
                        closure.audit_meshshot_wheel(attacked, record)
                    attacked.unlink()

            bound_tamper = output / wheel.name
            _rewrite_wheel(
                wheel,
                bound_tamper,
                replacements={"meshshot/profile.py": b"tampered = True\n"},
                rebuild_record=True,
            )
            with self.assertRaisesRegex(closure.ProjectClosureError, "source"):
                closure.audit_meshshot_wheel(bound_tamper, record)

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
            expected = json.loads(
                (
                    REPO_ROOT
                    / "models/agent-runtime/cup_cup_033/canonical-build-record.json"
                ).read_bytes()
            )
            build = json.loads((workspace / "built/build.json").read_bytes())
            self.assertEqual(
                build["files"][1]["sha256"],
                expected["measurementGlbDigest"].removeprefix("sha256:"),
            )

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

    def test_meshscope_source_enumeration_rejects_unexpected_and_symlink_entries(self) -> None:
        closure = _load_project_closure()
        for attack in ("unexpected", "symlink"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory(
                prefix="meshscope-source-closure-"
            ) as directory:
                repo = Path(directory)
                target = repo / "packages/meshscope"
                shutil.copytree(REPO_ROOT / "packages/meshscope", target)
                if attack == "unexpected":
                    (target / "dist").mkdir()
                    (target / "dist/rogue.whl").write_bytes(b"rogue")
                else:
                    (target / "src/meshscope/rogue.py").symlink_to("io.py")
                with self.assertRaises(closure.ProjectClosureError):
                    closure.meshscope_source_record(repo)

    def test_project_manifest_closes_all_project_artifacts(self) -> None:
        closure = _load_project_closure()
        with tempfile.TemporaryDirectory(prefix="agent-project-closure-") as directory:
            output = Path(directory)
            meshshot_source = closure.generate_meshshot_distribution(REPO_ROOT, output)
            implicit = closure.generate_implicit_runtime(REPO_ROOT, output)
            meshshot = closure.assemble_python_artifact(
                meshshot_source,
                {
                    "distribution": "meshshot-agent-runtime",
                    "version": "0.1.0",
                    "wheelPath": "wheels/meshshot_agent_runtime-0.1.0-py3-none-any.whl",
                    "wheelSha256": _digest("meshshot-wheel"),
                    "wheelBytes": 456,
                    "sourceTreeDigest": meshshot_source["sourceTreeDigest"],
                    "fileManifestDigest": meshshot_source["fileManifestDigest"],
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
                    "wheelSha256": _digest("meshscope-wheel"),
                    "wheelBytes": 123,
                    "sourceTreeDigest": _canonical_digest("meshscope-source"),
                    "fileManifestDigest": _canonical_digest("meshscope-source"),
                    "nativeAuditDigest": _canonical_digest("meshscope-audit"),
                    "nativeConformanceDigest": _canonical_digest("meshscope-conformance"),
                    "needed": ["libc.so.6", "libgcc_s.so.1", "libstdc++.so.6"],
                },
                implicit=implicit,
            )
            self.assertEqual(
                set(manifest),
                {"schema", "platform", "pythonArtifacts", "implicitRuntime"},
            )
            self.assertEqual(
                manifest["platform"],
                {"architecture": "amd64", "os": "linux", "pythonAbi": "cp312"},
            )
            self.assertEqual(
                [item["distribution"] for item in manifest["pythonArtifacts"]],
                ["meshscope", "meshshot-agent-runtime"],
            )
            encoded = closure.canonical_json_bytes(manifest)
            self.assertEqual(encoded, closure.canonical_json_bytes(json.loads(encoded)))
            closure.validate_project_manifest(closure.parse_canonical_json(encoded))

            mutations = []
            placeholder = json.loads(encoded)
            placeholder["pythonArtifacts"][0]["wheelSha256"] = "a" * 64
            mutations.append(placeholder)
            escaping = json.loads(encoded)
            escaping["pythonArtifacts"][1]["wheelPath"] = "../escape.whl"
            mutations.append(escaping)
            boolean_size = json.loads(encoded)
            boolean_size["pythonArtifacts"][0]["wheelBytes"] = True
            mutations.append(boolean_size)
            implicit_extra = json.loads(encoded)
            implicit_extra["implicitRuntime"]["rogue"] = True
            mutations.append(implicit_extra)
            implicit_digest = json.loads(encoded)
            implicit_digest["implicitRuntime"]["fileManifestDigest"] = "0" * 64
            mutations.append(implicit_digest)
            implicit_file = json.loads(encoded)
            implicit_file["implicitRuntime"]["files"][0]["sha256"] = _digest(
                "substituted-implicit-file"
            )
            mutations.append(implicit_file)
            for index, mutated in enumerate(mutations):
                with self.subTest(invalid_manifest=index), self.assertRaises(
                    closure.ProjectClosureError
                ):
                    closure.validate_project_manifest(mutated)

            with self.assertRaisesRegex(closure.ProjectClosureError, "source"):
                closure.assemble_python_artifact(
                    meshshot_source,
                    {
                        "distribution": "meshshot-agent-runtime",
                        "version": "0.1.0",
                        "wheelPath": meshshot["wheelPath"],
                        "wheelSha256": meshshot["wheelSha256"],
                        "wheelBytes": meshshot["wheelBytes"],
                        "sourceTreeDigest": _canonical_digest("wrong-source"),
                        "fileManifestDigest": meshshot_source["fileManifestDigest"],
                        "browserInventoryEmpty": True,
                        "browserDenial": meshshot["browserDenial"],
                    },
                )


if __name__ == "__main__":
    unittest.main()
