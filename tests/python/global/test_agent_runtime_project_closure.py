from __future__ import annotations

import base64
import copy
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
import tomllib
import unittest
from unittest import mock
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


def _write_synthetic_meshscope_wheel(destination: Path, source: dict) -> None:
    native_name = "meshscope/voxblame/_native.cpython-312-x86_64-linux-gnu.so"
    entries = {
        record["path"].removeprefix("src/"): (
            REPO_ROOT / "packages/meshscope" / record["path"]
        ).read_bytes()
        for record in source["files"]
        if record["path"].startswith("src/meshscope/")
        and record["path"].endswith(".py")
    }
    entries[native_name] = b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\0" + b"native"
    dist_info = "meshscope-0.1.0.dist-info"
    entries[f"{dist_info}/METADATA"] = (
        b"Metadata-Version: 2.4\nName: meshscope\nVersion: 0.1.0\n"
        b"Requires-Python: <3.13,>=3.12\nRequires-Dist: numpy==2.4.6\n"
        b"Requires-Dist: Pillow==12.2.0\nRequires-Dist: trimesh==4.12.2\n"
    )
    entries[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: setuptools (82.0.1)\n"
        b"Root-Is-Purelib: false\nTag: cp312-cp312-linux_x86_64\n"
    )
    entries[f"{dist_info}/top_level.txt"] = b"meshscope\n"
    record_name = f"{dist_info}/RECORD"
    rows = []
    for name, payload in entries.items():
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        rows.append((name, f"sha256={digest}", str(len(payload))))
    rows.append((record_name, "", ""))
    stream = StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    entries[record_name] = stream.getvalue().encode("utf-8")
    with zipfile.ZipFile(destination, "w") as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name, (2025, 8, 16, 0, 0, 0))
            info.create_system = 3
            permissions = 0o755 if name == native_name else 0o644
            info.external_attr = (stat.S_IFREG | permissions) << 16
            archive.writestr(info, payload)


class AgentRuntimeProjectClosureTests(unittest.TestCase):
    def test_snapshot_skills_do_not_install_agent_local_playwright(self) -> None:
        for skill in ("cad", "dxf", "sdf", "srdf", "urdf"):
            requirements = REPO_ROOT / "skills" / skill / "requirements.txt"
            if requirements.is_file():
                self.assertNotIn(
                    "playwright", requirements.read_text(encoding="utf-8").lower(), skill
                )
        snapshot_core = (
            REPO_ROOT / "packages" / "cadgen" / "src" / "cadgen" / "snapshot_core.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("playwright.async_api", "chromium.launch", "playwright install"):
            self.assertNotIn(forbidden, snapshot_core)

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
                    "== 'meshshot.runtime_client'; "
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
                    "assert str(e) == 'browser runtime capability is required'"
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

    def test_meshscope_distribution_and_native_wheel_are_exactly_closed(self) -> None:
        closure = _load_project_closure()
        metadata = tomllib.loads(
            (REPO_ROOT / "packages/meshscope/pyproject.toml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["build-system"]["requires"], ["setuptools==82.0.1"])
        self.assertEqual(metadata["project"]["requires-python"], ">=3.12,<3.13")
        self.assertEqual(
            metadata["project"]["dependencies"],
            ["numpy==2.4.6", "Pillow==12.2.0", "trimesh==4.12.2"],
        )
        self.assertEqual(
            (REPO_ROOT / "packages/meshscope/MANIFEST.in").read_text(
                encoding="utf-8"
            ),
            "include src/meshscope/voxblame/_native.cpp\n",
        )
        self.assertIs(metadata["tool"]["setuptools"]["include-package-data"], False)
        self.assertIn(
            'compile_args = ["-O3", "-std=c++17", "-g0"]',
            (REPO_ROOT / "packages/meshscope/setup.py").read_text(encoding="utf-8"),
        )
        with tempfile.TemporaryDirectory(prefix="meshscope-closed-wheel-") as directory:
            root = Path(directory)
            clean_repo = root / "source-repo"
            shutil.copytree(
                REPO_ROOT / "packages/meshscope",
                clean_repo / "packages/meshscope",
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.egg-info", "_native.*.so"
                ),
            )
            source = closure.meshscope_source_record(clean_repo)
            wheel = root / "meshscope-0.1.0-cp312-cp312-linux_x86_64.whl"
            libraries = {
                name: root / name
                for name in (
                    "libc.so.6",
                    "libgcc_s.so.1",
                    "libm.so.6",
                    "libstdc++.so.6",
                    "ld-linux-x86-64.so.2",
                )
            }
            for name, path in libraries.items():
                path.write_bytes(name.encode("ascii"))
            _write_synthetic_meshscope_wheel(wheel, source)
            ldd_output = "\n".join(
                [
                    f"libstdc++.so.6 => {libraries['libstdc++.so.6']} (0x1)",
                    f"libgcc_s.so.1 => {libraries['libgcc_s.so.1']} (0x2)",
                    f"libc.so.6 => {libraries['libc.so.6']} (0x3)",
                    f"libm.so.6 => {libraries['libm.so.6']} (0x4)",
                    f"/lib64/ld-linux-x86-64.so.2 => {libraries['ld-linux-x86-64.so.2']} (0x5)",
                ]
            )

            def tool(command, **kwargs):
                del kwargs
                if command[0] == "auditwheel":
                    output = 'platform tag: "manylinux_2_24_x86_64"\n'
                elif command[0] == "ldd":
                    output = ldd_output
                elif command[1] == "-dW":
                    output = "\n".join(
                        f"Shared library: [{name}]"
                        for name in ("libc.so.6", "libgcc_s.so.1", "libstdc++.so.6")
                    )
                elif command[1] == "-VW":
                    output = (
                        "GLIBC_2.2.5 GLIBC_2.14 GLIBC_2.4 GCC_3.0 "
                        "GLIBCXX_3.4 GLIBCXX_3.4.21 CXXABI_1.3 CXXABI_1.3.9"
                    )
                elif command[1] == "-sW":
                    output = "PyInit__native"
                else:
                    output = "ELF64 little endian x86-64"
                return subprocess.CompletedProcess(command, 0, output, "")

            with mock.patch.object(closure.subprocess, "run", side_effect=tool):
                audit = closure.audit_meshscope_wheel(
                    wheel,
                    source,
                    readelf="readelf",
                    ldd="ldd",
                    auditwheel="auditwheel",
                )
            self.assertEqual(audit["sourceTreeDigest"], source["sourceTreeDigest"])
            self.assertEqual(
                [item["soname"] for item in audit["resolvedLibraries"]],
                [
                    "ld-linux-x86-64.so.2",
                    "libc.so.6",
                    "libgcc_s.so.1",
                    "libm.so.6",
                    "libstdc++.so.6",
                ],
            )
            self.assertEqual(audit["auditwheelPlatformTag"], "manylinux_2_24_x86_64")

            attacked = root / "attacked.whl"
            _rewrite_wheel(wheel, attacked, extra=("meshscope/rogue.py", b"pass\n"))
            attacked_exact_name = root / "meshscope-0.1.0-cp312-cp312-manylinux_2_24_x86_64.whl"
            attacked.rename(attacked_exact_name)
            with self.assertRaises(closure.ProjectClosureError):
                closure.audit_meshscope_wheel(attacked_exact_name, source)

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

    def test_meshscope_cup_conformance_record_is_closed_over_native_facts(self) -> None:
        closure = _load_project_closure()
        with tempfile.TemporaryDirectory(prefix="meshscope-cup-conformance-") as directory:
            root = Path(directory)
            wheel = root / "meshscope-0.1.0-cp312-cp312-linux_x86_64.whl"
            wheel.write_bytes(b"project-wheel")
            dependencies = []
            for name in ("numpy.whl", "pillow.whl", "trimesh.whl"):
                path = root / name
                path.write_bytes(name.encode("ascii"))
                dependencies.append(path)

            def run(command, **kwargs):
                expected_environment = {
                    "PATH", "LANG", "LC_ALL", "TZ", "PYTHONDONTWRITEBYTECODE",
                    "PYTHONNOUSERSITE",
                }
                if "install" in command:
                    self.assertEqual(set(kwargs["env"]), expected_environment)
                    return subprocess.CompletedProcess(command, 0, "", "")
                self.assertEqual(
                    set(kwargs["env"]), expected_environment | {"PYTHONPATH"}
                )
                cup_root = Path(command[-1])
                target = Path(kwargs["env"]["PYTHONPATH"])
                native = (
                    target
                    / "meshscope/voxblame/_native.cpython-312-x86_64-linux-gnu.so"
                )
                native.parent.mkdir(parents=True)
                native.write_bytes(b"native")
                (cup_root / "input").mkdir(parents=True)
                (cup_root / "input/input.json").write_text(
                    json.dumps(
                        {"input_triangle_count": 3764, "canonical_triangle_count": 3764}
                    ),
                    encoding="utf-8",
                )
                step = cup_root / "voxblame/steps/000000"
                step.mkdir(parents=True)
                (step / "summary.json").write_text(
                    json.dumps(
                        {
                            "max_depth": 8,
                            "step": 0,
                            "errors_by_depth": [
                                {
                                    "depth": 8,
                                    "reference_surface_count": 452682,
                                    "candidate_surface_count": 452682,
                                    "missing_surface_count": 0,
                                    "excess_surface_count": 0,
                                    "union_surface_count": 452682,
                                    "surface_error_count": 0,
                                    "surface_error_rate": 0.0,
                                }
                            ],
                            "objective_facts": {
                                "global_depth_8_zero": True,
                                "out_of_frame_clear": True,
                                "no_evidence_conflict": True,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command, 0, "meshscope-cup-native-ok\n", ""
                )

            with mock.patch.object(closure.subprocess, "run", side_effect=run):
                result = closure.verify_meshscope_native_install(
                    wheel,
                    REPO_ROOT
                    / "models/agent-runtime/cup_cup_033/input/cup_cup_033.ply",
                    tuple(dependencies),
                    python="python3.12",
                )
            self.assertEqual(result["status"], "development-candidate")
            self.assertEqual(result["fixture"]["inputTriangleCount"], 3764)
            self.assertEqual(
                result["measurement"]["depthEight"]["reference_surface_count"],
                452682,
            )
            self.assertNotIn("surface_error_rate", result["measurement"]["depthEight"])
            self.assertEqual(
                result["providerExecution"],
                {"mode": "no-provider-configured", "dispatchCount": 0},
            )
            self.assertTrue(result["nativeConformanceDigest"].startswith("sha256:"))

    def test_meshscope_development_evidence_reassembles_and_rejects_mutation(self) -> None:
        closure = _load_project_closure()
        root = (
            REPO_ROOT
            / "models/agent-runtime/cup_cup_033/meshscope-development"
        )
        builds = tuple(
            json.loads((root / name).read_bytes())
            for name in (
                "build-1.json",
                "build-2.json",
                "build-alternate-root.json",
            )
        )
        audit = json.loads((root / "wheel-audit.json").read_bytes())
        conformance = json.loads(
            (root / "cup-native-conformance.json").read_bytes()
        )
        local_admission = json.loads(
            (root / "local-development-admission.json").read_bytes()
        )
        candidate = closure.assemble_meshscope_development_candidate(
            builds, audit, conformance, local_admission
        )
        self.assertEqual(
            closure.canonical_json_bytes(candidate) + b"\n",
            (root / "candidate.json").read_bytes(),
        )
        conformance["providerExecution"]["dispatchCount"] = 1
        with self.assertRaises(closure.ProjectClosureError):
            closure.assemble_meshscope_development_candidate(
                builds, audit, conformance, local_admission
            )

    def test_meshscope_candidate_rejects_build_context_and_toolchain_substitution(self) -> None:
        closure = _load_project_closure()
        root = REPO_ROOT / "models/agent-runtime/cup_cup_033/meshscope-development"
        builds = tuple(
            json.loads((root / name).read_bytes())
            for name in ("build-1.json", "build-2.json", "build-alternate-root.json")
        )
        audit = json.loads((root / "wheel-audit.json").read_bytes())
        conformance = json.loads((root / "cup-native-conformance.json").read_bytes())
        local_admission = json.loads(
            (root / "local-development-admission.json").read_bytes()
        )

        mutations = []
        compiler = copy.deepcopy(builds)
        compiler[0]["toolchain"]["compiler"] = "arbitrary compiler"
        mutations.append(compiler)
        platform = copy.deepcopy(builds)
        platform[0]["platform"]["architecture"] = "arm64"
        mutations.append(platform)
        environment = copy.deepcopy(builds)
        environment[0]["environment"]["TZ"] = "local"
        mutations.append(environment)
        for command_index in (0, 1):
            command = copy.deepcopy(builds)
            command[0]["commands"][command_index] += " -funsafe-math-optimizations"
            mutations.append(command)
        epoch = copy.deepcopy(builds)
        epoch[0]["sourceDateEpoch"] = 1
        mutations.append(epoch)
        source = copy.deepcopy(builds)
        source[0]["source"]["sourceTreeDigest"] = "sha256:" + "0" * 64
        mutations.append(source)
        wheel = copy.deepcopy(builds)
        wheel[0]["wheel"]["sha256"] = "sha256:" + "0" * 64
        mutations.append(wheel)
        launch = copy.deepcopy(builds)
        launch[0]["executionContext"]["launchSpec"]["networkMode"] = "bridge"
        mutations.append(launch)
        duplicate = copy.deepcopy(builds)
        duplicate = (duplicate[0], duplicate[0], duplicate[2])
        mutations.append(duplicate)
        for index, mutated in enumerate(mutations):
            with self.subTest(mutation=index), self.assertRaises(
                closure.ProjectClosureError
            ):
                closure.assemble_meshscope_development_candidate(
                    mutated, audit, conformance, local_admission
                )

    def test_meshscope_candidate_rejects_audit_and_conformance_substitution(self) -> None:
        closure = _load_project_closure()
        root = REPO_ROOT / "models/agent-runtime/cup_cup_033/meshscope-development"
        builds = tuple(
            json.loads((root / name).read_bytes())
            for name in ("build-1.json", "build-2.json", "build-alternate-root.json")
        )
        audit = json.loads((root / "wheel-audit.json").read_bytes())
        conformance = json.loads((root / "cup-native-conformance.json").read_bytes())
        local_admission = json.loads(
            (root / "local-development-admission.json").read_bytes()
        )

        audit_mutations = []
        for path, value in (
            (("files",), audit["files"][:-1]),
            (("nativePath",), "meshscope/arbitrary.so"),
            (("nativeSha256",), "sha256:" + "0" * 64),
            (("wheelRecordDigest",), "sha256:" + "0" * 64),
            (("auditwheelReportDigest",), "sha256:" + "0" * 64),
            (("elfReportsDigest",), "sha256:" + "0" * 64),
            (("auditwheelPlatformTag",), "linux_x86_64"),
            (("needed",), ["libc.so.6"]),
            (("versionRequirements",), ["GLIBC_2.2.5"]),
            (("resolvedLibraries", 0, "path"), "/arbitrary/loader"),
        ):
            attacked = copy.deepcopy(audit)
            target = attacked
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            without_digest = dict(attacked)
            without_digest.pop("nativeAuditDigest")
            attacked["nativeAuditDigest"] = closure.canonical_json_digest(
                without_digest
            )
            audit_mutations.append((path, attacked))
        for path, attacked in audit_mutations:
            with self.subTest(audit_path=path), self.assertRaises(
                closure.ProjectClosureError
            ):
                closure.assemble_meshscope_development_candidate(
                    builds, attacked, conformance, local_admission
                )

        for path, value in (
            ("dependencyWheels", [{"path": "arbitrary.whl", "sha256": "sha256:" + "0" * 64, "bytes": 1}] * 3),
            ("imports", ["meshscope"]),
            ("meshscopeWheel", {"path": "arbitrary.whl", "sha256": "sha256:" + "0" * 64, "bytes": 1}),
            ("fixture.id", "arbitrary"),
            ("fixture.inputTriangleCount", 1),
            ("nativeModuleBasename", "arbitrary.so"),
            ("nativeModuleSha256", "sha256:" + "0" * 64),
            ("nativeBackendCallable", False),
            ("measurement.depthEight.depth", 7),
            ("measurement.depthEight.reference_surface_count", 1),
            ("measurement.depthEight.missing_surface_count", 1),
            ("measurement.summarySha256", "sha256:" + "0" * 64),
            ("measurement.objectiveFacts.global_depth_8_zero", False),
            ("providerExecution.mode", "network-denied"),
            ("providerExecution.dispatchCount", 1),
        ):
            attacked = copy.deepcopy(conformance)
            target = attacked
            parts = path.split(".")
            for key in parts[:-1]:
                target = target[key]
            target[parts[-1]] = value
            without_digest = dict(attacked)
            without_digest.pop("nativeConformanceDigest")
            attacked["nativeConformanceDigest"] = closure.canonical_json_digest(
                without_digest
            )
            with self.subTest(path=path), self.assertRaises(
                closure.ProjectClosureError
            ):
                closure.assemble_meshscope_development_candidate(
                    builds, audit, attacked, local_admission
                )

    def test_meshscope_candidate_closes_local_development_admission_without_formal_upgrade(self) -> None:
        closure = _load_project_closure()
        root = REPO_ROOT / "models/agent-runtime/cup_cup_033/meshscope-development"
        builds = tuple(
            json.loads((root / name).read_bytes())
            for name in ("build-1.json", "build-2.json", "build-alternate-root.json")
        )
        audit = json.loads((root / "wheel-audit.json").read_bytes())
        conformance = json.loads((root / "cup-native-conformance.json").read_bytes())
        local_admission = json.loads(
            (root / "local-development-admission.json").read_bytes()
        )
        candidate = closure.assemble_meshscope_development_candidate(
            builds, audit, conformance, local_admission
        )
        self.assertEqual(
            candidate["localDevelopmentAdmission"],
            {
                "status": "qualified-local-candidate",
                "formalAdmission": False,
                "immutableMirrorVisible": False,
                "digest": local_admission["localDevelopmentAdmissionDigest"],
            },
        )
        self.assertFalse(candidate["admission"]["admitted"])

        for path, value in (
            ("sai004CandidateCommit", "0" * 40),
            ("builder.imageId", "sha256:" + "0" * 64),
            ("builder.dockerArchive.digest", "sha256:" + "0" * 64),
            ("runtimeWheels.0.sha256", "sha256:" + "0" * 64),
            ("sourceDocuments.0.digest", "sha256:" + "0" * 64),
            ("formalAdmission", True),
            ("immutableMirrorVisible", True),
        ):
            attacked = copy.deepcopy(local_admission)
            target = attacked
            parts = path.split(".")
            for key in parts[:-1]:
                target = target[int(key)] if key.isdigit() else target[key]
            last = parts[-1]
            if last.isdigit():
                target[int(last)] = value
            else:
                target[last] = value
            without_digest = dict(attacked)
            without_digest.pop("localDevelopmentAdmissionDigest")
            attacked["localDevelopmentAdmissionDigest"] = (
                closure.canonical_json_digest(without_digest)
            )
            with self.subTest(path=path), self.assertRaises(
                closure.ProjectClosureError
            ):
                closure.assemble_meshscope_development_candidate(
                    builds, audit, conformance, attacked
                )

    def test_project_manifest_closes_all_project_artifacts(self) -> None:
        closure = _load_project_closure()
        with tempfile.TemporaryDirectory(prefix="agent-project-closure-") as directory:
            output = Path(directory)
            meshshot_source = closure.generate_meshshot_distribution(REPO_ROOT, output)
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
            )
            self.assertEqual(
                set(manifest),
                {"schema", "platform", "pythonArtifacts"},
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
