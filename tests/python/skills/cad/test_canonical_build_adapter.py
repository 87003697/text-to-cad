from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Callable
import unittest

from tests.python.support.paths import repo_path
from tests.python.support.tmp_root import temporary_directory


ADAPTER = repo_path("skills/cad/scripts/canonical-build")
CADPY_SRC = repo_path("packages/cadpy/src")


def _write_canonical_source(root: Path, *, body: str | None = None) -> Path:
    source_dir = root / "source"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "model.py"
    source_path.write_text(
        body
        or "\n".join(
            (
                "from build123d import Align, Box",
                "",
                "WIDTH = 0.4",
                "DEPTH = 0.2",
                "HEIGHT = 0.1",
                "",
                "def gen_step():",
                "    return Box(WIDTH, DEPTH, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.CENTER))",
                "",
            )
        ),
        encoding="utf-8",
    )
    return source_path


def _run_adapter(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        (str(CADPY_SRC), env["PYTHONPATH"]) if env.get("PYTHONPATH") else (str(CADPY_SRC),)
    )
    return subprocess.run(
        [sys.executable, str(ADAPTER), *args],
        cwd=root,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_adapter_with_post_policy_mutation(
    root: Path,
    *,
    source_body: str,
    mutate: Callable[[Path, Path], None],
) -> subprocess.CompletedProcess[str]:
    source = _write_canonical_source(root, body=source_body)
    helper = source.parent / "helper.py"
    helper.write_text(
        "from build123d import Box\n"
        "def make_shape():\n"
        "    return Box(0.4, 0.2, 0.1)\n",
        encoding="utf-8",
    )
    control = source.parent / "control.lock"
    control.write_bytes(b"locked\n")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(CADPY_SRC), environment["PYTHONPATH"])
        if environment.get("PYTHONPATH")
        else (str(CADPY_SRC),)
    )
    with control.open("rb") as control_stream:
        fcntl.flock(control_stream.fileno(), fcntl.LOCK_EX)
        process = subprocess.Popen(
            [
                sys.executable,
                str(ADAPTER),
                "build",
                "--source",
                "source/model.py",
                "--input",
                "source/helper.py",
                "--input",
                "source/control.lock",
                "--output-dir",
                "candidate",
            ],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ready = root / "candidate/race-ready"
        deadline = time.monotonic() + 15
        while not ready.is_file():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"adapter exited before post-policy mutation: {stdout}\n{stderr}"
                )
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise AssertionError("adapter did not reach post-policy mutation gate")
            time.sleep(0.01)
        mutate(source.parent, helper)
        fcntl.flock(control_stream.fileno(), fcntl.LOCK_UN)
        stdout, stderr = process.communicate(timeout=30)
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout,
        stderr,
    )


def _run_adapter_with_pre_snapshot_replacement(
    root: Path,
) -> subprocess.CompletedProcess[str]:
    source = _write_canonical_source(
        root,
        body=(
            "from helper import make_shape\n"
            "def gen_step():\n"
            "    return make_shape()\n"
        ),
    )
    helper = source.parent / "helper.py"
    helper.write_text(
        "from build123d import Box\n"
        "def make_shape():\n"
        "    return Box(0.4, 0.2, 0.1)\n",
        encoding="utf-8",
    )
    hook = root / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "from cadpy import canonical_build\n"
        "_original = canonical_build._declared_python_module_finder\n"
        "def _replace_before_snapshot(*args, **kwargs):\n"
        "    helper = Path(kwargs['root']) / 'source/helper.py'\n"
        "    replacement = helper.with_name('replacement.py')\n"
        "    replacement.write_text(\n"
        "        'from build123d import Box\\n'\n"
        "        'def make_shape():\\n'\n"
        "        '    length_mm = 0.8\\n'\n"
        "        '    return Box(length_mm, 0.2, 0.1)\\n',\n"
        "        encoding='utf-8',\n"
        "    )\n"
        "    replacement.replace(helper)\n"
        "    return _original(*args, **kwargs)\n"
        "canonical_build._declared_python_module_finder = _replace_before_snapshot\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(hook), str(CADPY_SRC)))
    return subprocess.run(
        [
            sys.executable,
            str(ADAPTER),
            "build",
            "--source",
            "source/model.py",
            "--input",
            "source/helper.py",
            "--output-dir",
            "candidate",
        ],
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _read_glb_json(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    magic, version, _length = struct.unpack_from("<III", payload, 0)
    if magic != 0x46546C67 or version != 2:
        raise AssertionError("Not a GLB v2 file")
    chunk_length, chunk_type = struct.unpack_from("<I4s", payload, 12)
    if chunk_type != b"JSON":
        raise AssertionError("First GLB chunk is not JSON")
    return json.loads(payload[20 : 20 + chunk_length].decode("utf-8"))


def _matrix_multiply(left: list[float], right: list[float]) -> list[float]:
    return [
        sum(left[row + inner * 4] * right[inner + column * 4] for inner in range(4))
        for column in range(4)
        for row in range(4)
    ]


def _node_matrix(node: dict[str, object]) -> list[float]:
    if "matrix" in node:
        return [float(value) for value in node["matrix"]]
    x, y, z, w = (float(value) for value in node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    sx, sy, sz = (float(value) for value in node.get("scale", [1.0, 1.0, 1.0]))
    tx, ty, tz = (float(value) for value in node.get("translation", [0.0, 0.0, 0.0]))
    return [
        (1 - 2 * (y * y + z * z)) * sx,
        (2 * (x * y + z * w)) * sx,
        (2 * (x * z - y * w)) * sx,
        0.0,
        (2 * (x * y - z * w)) * sy,
        (1 - 2 * (x * x + z * z)) * sy,
        (2 * (y * z + x * w)) * sy,
        0.0,
        (2 * (x * z + y * w)) * sz,
        (2 * (y * z - x * w)) * sz,
        (1 - 2 * (x * x + y * y)) * sz,
        0.0,
        tx,
        ty,
        tz,
        1.0,
    ]


def _position_bounds(path: Path) -> tuple[list[float], list[float]]:
    gltf = _read_glb_json(path)
    accessors = gltf["accessors"]
    meshes = gltf["meshes"]
    nodes = gltf["nodes"]
    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    points: list[list[float]] = []

    def visit(node_index: int, parent_matrix: list[float]) -> None:
        node = nodes[node_index]
        world = _matrix_multiply(parent_matrix, _node_matrix(node))
        if "mesh" in node:
            for primitive in meshes[node["mesh"]]["primitives"]:
                accessor = accessors[primitive["attributes"]["POSITION"]]
                for x in (float(accessor["min"][0]), float(accessor["max"][0])):
                    for y in (float(accessor["min"][1]), float(accessor["max"][1])):
                        for z in (float(accessor["min"][2]), float(accessor["max"][2])):
                            points.append(
                                [
                                    world[0] * x + world[4] * y + world[8] * z + world[12],
                                    world[1] * x + world[5] * y + world[9] * z + world[13],
                                    world[2] * x + world[6] * y + world[10] * z + world[14],
                                ]
                            )
        for child in node.get("children", []):
            visit(child, world)

    for root_node in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
        visit(root_node, identity)
    if not points:
        raise AssertionError("GLB contains no placed mesh geometry")
    return (
        [min(point[axis] for point in points) for axis in range(3)],
        [max(point[axis] for point in points) for axis in range(3)],
    )


class CanonicalBuildAdapterTests(unittest.TestCase):
    def test_public_adapter_preserves_world_placement_and_excludes_unreturned_helper_geometry(self) -> None:
        with temporary_directory(prefix="cad-canonical-placement-") as temp_dir:
            root = Path(temp_dir)
            _write_canonical_source(
                root,
                body="\n".join(
                    (
                        "from build123d import Box, Pos",
                        "def gen_step():",
                        "    debug_helper = Pos(10.0, 0.0, 0.0) * Box(1.0, 1.0, 1.0)",
                        "    return Pos(0.3, -0.2, 0.1) * Box(0.2, 0.1, 0.05)",
                    )
                ),
            )

            result = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "candidate",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            minimum, maximum = _position_bounds(root / "candidate/measurement.glb")
            self.assertEqual([0.2, -0.25, 0.075], [round(value, 6) for value in minimum])
            self.assertEqual([0.4, -0.15, 0.125], [round(value, 6) for value in maximum])

    def test_public_adapter_builds_canonical_step_measurement_and_provenance(self) -> None:
        with temporary_directory(prefix="cad-canonical-build-") as temp_dir:
            root = Path(temp_dir)
            source_path = _write_canonical_source(root)

            result = _run_adapter(
                root,
                "build",
                "--source",
                source_path.relative_to(root).as_posix(),
                "--output-dir",
                "candidate",
            )

            self.assertEqual("", result.stderr)
            self.assertEqual(0, result.returncode)
            self.assertEqual(1, len(result.stdout.splitlines()))
            self.assertIs(json.loads(result.stdout)["ok"], True)
            manifest = json.loads((root / "candidate/build.json").read_text(encoding="utf-8"))
            profile = json.loads((root / "candidate/profile.json").read_text(encoding="utf-8"))
            recipe = json.loads((root / "candidate/rebuild.json").read_text(encoding="utf-8"))

            self.assertEqual("mesh-to-cad.build/1", manifest["schema"])
            self.assertEqual("cad", manifest["route"])
            self.assertEqual("cad.canonical-build/1", manifest["adapter"]["id"])
            self.assertEqual("trellis2-canonical", profile["coordinateProfile"])
            self.assertEqual("voxblame-depth8", profile["tessellationProfile"])
            self.assertEqual(2**-11, profile["linearDeflection"])
            self.assertFalse(profile["relativeDeflection"])
            self.assertFalse(profile["semanticUnitScaling"])
            self.assertEqual("non-semantic", profile["stepNominalUnitContext"]["meaning"])
            self.assertEqual(profile["digest"], manifest["profile"]["digest"])

            step_path = root / "candidate/canonical.step"
            measurement_path = root / "candidate/measurement.glb"
            self.assertTrue(step_path.is_file())
            self.assertTrue(measurement_path.is_file())
            self.assertEqual("canonical.step", manifest["primaryArtifact"]["path"])
            self.assertEqual("measurement.glb", manifest["measurementGlb"]["path"])
            self.assertEqual(
                ["input:source", "artifact:primary", "artifact:measurement"],
                [edge["from"] for edge in manifest["derivation"][:2]]
                + [manifest["derivation"][1]["to"]],
            )

            minimum, maximum = _position_bounds(measurement_path)
            self.assertEqual([-0.2, -0.1, -0.05], [round(value, 6) for value in minimum])
            self.assertEqual([0.2, 0.1, 0.05], [round(value, 6) for value in maximum])
            self.assertEqual("mesh-to-cad.rebuild-recipe/1", recipe["schema"])
            self.assertEqual("cad.canonical-build/1", recipe["executable"])
            self.assertEqual(".", recipe["workingDirectory"])
            self.assertEqual("forbidden", recipe["network"])
            self.assertEqual("forbidden", recipe["ambientInputs"])
            self.assertEqual(
                "declared-inputs-read-only; output-directory-write-only",
                recipe["filesystem"],
            )
            self.assertEqual(
                {"id": manifest["profile"]["id"], "digest": manifest["profile"]["digest"]},
                recipe["profile"],
            )
            self.assertEqual(manifest["dependencies"], recipe["runtime"])
            self.assertNotIn(str(root), json.dumps(manifest))
            self.assertNotIn(str(root), json.dumps(recipe))

    def test_saved_recipe_rebuilds_offline_from_only_declared_inputs(self) -> None:
        with temporary_directory(prefix="cad-canonical-rebuild-") as temp_dir:
            temp_root = Path(temp_dir)
            initial_root = temp_root / "initial"
            initial_root.mkdir()
            _write_canonical_source(initial_root)
            initial = _run_adapter(
                initial_root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "candidate",
            )
            self.assertEqual(0, initial.returncode, initial.stderr)
            initial_manifest = json.loads((initial_root / "candidate/build.json").read_text(encoding="utf-8"))

            rebuild_root = temp_root / "isolated"
            (rebuild_root / "source").mkdir(parents=True)
            shutil.copy2(initial_root / "source/model.py", rebuild_root / "source/model.py")
            shutil.copy2(initial_root / "candidate/rebuild.json", rebuild_root / "rebuild.json")

            rebuilt = _run_adapter(
                rebuild_root,
                "rebuild",
                "--recipe",
                "rebuild.json",
                "--output-dir",
                "rebuilt",
            )

            self.assertEqual("", rebuilt.stderr)
            self.assertEqual(0, rebuilt.returncode)
            rebuilt_manifest = json.loads((rebuild_root / "rebuilt/build.json").read_text(encoding="utf-8"))
            self.assertEqual(initial_manifest["profile"], rebuilt_manifest["profile"])
            self.assertEqual(
                initial_manifest["measurementGlb"]["sha256"],
                rebuilt_manifest["measurementGlb"]["sha256"],
            )
            self.assertEqual(
                _position_bounds(initial_root / "candidate/measurement.glb"),
                _position_bounds(rebuild_root / "rebuilt/measurement.glb"),
            )
            self.assertNotIn(str(initial_root), json.dumps(rebuilt_manifest))

    def test_adapter_rejects_absolute_and_out_of_root_paths(self) -> None:
        with temporary_directory(prefix="cad-canonical-paths-") as temp_dir:
            root = Path(temp_dir)
            source_path = _write_canonical_source(root)

            absolute_source = _run_adapter(
                root,
                "build",
                "--source",
                str(source_path),
                "--output-dir",
                "candidate-a",
            )
            absolute_output = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                str(root / "candidate-b"),
            )
            escaped_output = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "../candidate-c",
            )

            for result in (absolute_source, absolute_output, escaped_output):
                self.assertNotEqual(0, result.returncode)
                self.assertIn("confined relative path", result.stderr)

    def test_adapter_requires_every_local_source_input_to_be_declared(self) -> None:
        with temporary_directory(prefix="cad-canonical-inputs-") as temp_dir:
            root = Path(temp_dir)
            _write_canonical_source(
                root,
                body="\n".join(
                    (
                        "from pathlib import Path",
                        "from build123d import Align, Box",
                        "",
                        "def gen_step():",
                        "    width = float(Path('source/width.txt').read_text(encoding='utf-8'))",
                        "    return Box(width, 0.2, 0.1, align=(Align.CENTER, Align.CENTER, Align.CENTER))",
                        "",
                    )
                ),
            )
            (root / "source/width.txt").write_text("0.4\n", encoding="utf-8")

            rejected = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "rejected",
            )
            accepted = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--input",
                "source/width.txt",
                "--output-dir",
                "accepted",
            )

            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("undeclared input", rejected.stderr)
            self.assertEqual(0, accepted.returncode, accepted.stderr)
            recipe = json.loads((root / "accepted/rebuild.json").read_text(encoding="utf-8"))
            self.assertEqual(
                ["source/model.py", "source/width.txt"],
                [item["path"] for item in recipe["inputs"]],
            )

            isolated_root = root / "isolated"
            (isolated_root / "source").mkdir(parents=True)
            shutil.copy2(root / "source/model.py", isolated_root / "source/model.py")
            shutil.copy2(root / "source/width.txt", isolated_root / "source/width.txt")
            (isolated_root / "rebuild.json").write_text(json.dumps(recipe), encoding="utf-8")
            rebuilt = _run_adapter(
                isolated_root,
                "rebuild",
                "--recipe",
                "rebuild.json",
                "--output-dir",
                "rebuilt",
            )
            self.assertEqual(0, rebuilt.returncode, rebuilt.stderr)
            rebuilt_recipe = json.loads((isolated_root / "rebuilt/rebuild.json").read_text(encoding="utf-8"))
            self.assertEqual(recipe["inputs"], rebuilt_recipe["inputs"])

    def test_public_adapter_builds_durable_model_with_declared_python_helper(self) -> None:
        with temporary_directory(prefix="cad-canonical-import-") as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            shutil.copy2(
                repo_path("models/simple/rectangular_clamp_block.py"),
                source / "model.py",
            )
            shutil.copy2(
                repo_path("models/simple/simple_model_library.py"),
                source / "simple_model_library.py",
            )

            result = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--input",
                "source/simple_model_library.py",
                "--output-dir",
                "candidate",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((root / "candidate/measurement.glb").is_file())
            recipe = json.loads(
                (root / "candidate/rebuild.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                ["source/model.py", "source/simple_model_library.py"],
                [item["path"] for item in recipe["inputs"]],
            )

    def test_declared_helper_import_ignores_undeclared_siblings(self) -> None:
        with temporary_directory(prefix="cad-canonical-import-closure-") as temp_dir:
            root = Path(temp_dir)
            _write_canonical_source(
                root,
                body=(
                    "from helper import make_shape\n"
                    "def gen_step():\n"
                    "    return make_shape()\n"
                ),
            )
            (root / "source/helper.py").write_text(
                "from build123d import Box\n"
                "def make_shape():\n"
                "    return Box(0.4, 0.2, 0.1)\n",
                encoding="utf-8",
            )
            (root / "source/ambient.py").write_text(
                "SECRET = 'undeclared'\n",
                encoding="utf-8",
            )

            result = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--input",
                "source/helper.py",
                "--output-dir",
                "candidate",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((root / "candidate/measurement.glb").is_file())
            self.assertTrue((root / "candidate/build.json").is_file())

    def test_declared_module_directory_stays_unobservable_after_policy_entry(
        self,
    ) -> None:
        source_body = (
            "import fcntl\n"
            "import os\n"
            "from pathlib import Path\n"
            "Path('candidate/race-ready').write_text('ready', encoding='utf-8')\n"
            "with Path('source/control.lock').open('rb') as control:\n"
            "    fcntl.flock(control.fileno(), fcntl.LOCK_SH)\n"
            "Path('candidate/race-ready').unlink()\n"
            "try:\n"
            "    os.listdir('source')\n"
            "except PermissionError:\n"
            "    pass\n"
            "else:\n"
            "    raise RuntimeError('declared module directory became observable')\n"
            "from helper import make_shape\n"
            "def gen_step():\n"
            "    return make_shape()\n"
        )

        def add_regular(source: Path, _helper: Path) -> None:
            (source / "ambient.py").write_text(
                "SECRET = 'undeclared'\n",
                encoding="utf-8",
            )

        def add_symlink(source: Path, _helper: Path) -> None:
            outside = source.parent / "outside.py"
            outside.write_text("SECRET = 'outside'\n", encoding="utf-8")
            (source / "ambient.py").symlink_to(outside)

        for name, mutate in (("regular", add_regular), ("symlink", add_symlink)):
            with self.subTest(name=name), temporary_directory(
                prefix=f"cad-canonical-import-race-{name}-"
            ) as temp_dir:
                root = Path(temp_dir)
                result = _run_adapter_with_post_policy_mutation(
                    root,
                    source_body=source_body,
                    mutate=mutate,
                )

                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue((root / "candidate/measurement.glb").is_file())

    def test_declared_helper_replacement_after_policy_entry_fails_closed(self) -> None:
        source_body = (
            "import fcntl\n"
            "from pathlib import Path\n"
            "Path('candidate/race-ready').write_text('ready', encoding='utf-8')\n"
            "with Path('source/control.lock').open('rb') as control:\n"
            "    fcntl.flock(control.fileno(), fcntl.LOCK_SH)\n"
            "Path('candidate/race-ready').unlink()\n"
            "from helper import make_shape\n"
            "def gen_step():\n"
            "    return make_shape()\n"
        )

        def replace_helper(source: Path, helper: Path) -> None:
            replacement = source.parent / "replacement.py"
            replacement.write_text(
                "from build123d import Box\n"
                "def make_shape():\n"
                "    return Box(0.8, 0.2, 0.1)\n",
                encoding="utf-8",
            )
            replacement.replace(helper)

        with temporary_directory(prefix="cad-canonical-import-replace-") as temp_dir:
            root = Path(temp_dir)
            result = _run_adapter_with_post_policy_mutation(
                root,
                source_body=source_body,
                mutate=replace_helper,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("declared Python helper changed", result.stderr)
            self.assertFalse((root / "candidate/measurement.glb").exists())
            self.assertFalse((root / "candidate/build.json").exists())

    def test_declared_helper_replacement_before_snapshot_fails_closed(self) -> None:
        with temporary_directory(prefix="cad-canonical-import-presnapshot-") as temp_dir:
            root = Path(temp_dir)
            result = _run_adapter_with_pre_snapshot_replacement(root)

            self.assertNotEqual(0, result.returncode)
            self.assertFalse((root / "candidate/measurement.glb").exists())
            self.assertFalse((root / "candidate/build.json").exists())

    def test_source_cannot_disable_internal_execution_policy(self) -> None:
        for bypass in (
            "canonical_build._ACTIVE_SOURCE_POLICY.set(None)",
            "canonical_build._INTERNAL_PHYSICAL_READ.set(True)",
        ):
            with self.subTest(bypass=bypass), temporary_directory(
                prefix="cad-canonical-policy-bypass-"
            ) as temp_dir:
                root = Path(temp_dir)
                (root / "secret.txt").write_text("undeclared\n", encoding="utf-8")
                source = _write_canonical_source(
                    root,
                    body=(
                        "from build123d import Box\n"
                        "from cadpy import canonical_build\n"
                        f"{bypass}\n"
                        "canonical_build.Path('secret.txt').read_text(encoding='utf-8')\n"
                        "canonical_build.Path('breach.txt').write_text('written', encoding='utf-8')\n"
                        "canonical_build.sys.modules['socket'].socket().close()\n"
                        "canonical_build.sys.modules['subprocess'].run(\n"
                        "    [canonical_build.sys.executable, '-c', 'pass'], check=True\n"
                        ")\n"
                        "def gen_step():\n"
                        "    return Box(0.4, 0.2, 0.1)\n"
                    ),
                )

                result = _run_adapter(
                    root,
                    "build",
                    "--source",
                    source.relative_to(root).as_posix(),
                    "--output-dir",
                    "candidate",
                )

                self.assertNotEqual(0, result.returncode)
                self.assertFalse((root / "breach.txt").exists())
                self.assertFalse((root / "candidate/measurement.glb").exists())
                self.assertFalse((root / "candidate/build.json").exists())

    def test_source_stdout_is_rejected_before_formal_publication(self) -> None:
        with temporary_directory(prefix="cad-canonical-source-stdout-") as temp_dir:
            root = Path(temp_dir)
            source = _write_canonical_source(
                root,
                body=(
                    "from build123d import Box\n"
                    "print('candidate noise')\n"
                    "def gen_step():\n"
                    "    return Box(0.4, 0.2, 0.1)\n"
                ),
            )

            result = _run_adapter(
                root,
                "build",
                "--source",
                source.relative_to(root).as_posix(),
                "--output-dir",
                "candidate",
            )

            self.assertNotEqual(0, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertFalse((root / "candidate/measurement.glb").exists())
            self.assertFalse((root / "candidate/build.json").exists())

    def test_source_cannot_call_host_physical_reader(self) -> None:
        with temporary_directory(prefix="cad-canonical-host-reader-") as temp_dir:
            root = Path(temp_dir)
            (root / "secret.txt").write_text("undeclared\n", encoding="utf-8")
            source = _write_canonical_source(
                root,
                body=(
                    "from build123d import Box\n"
                    "from cadpy import canonical_build\n"
                    "canonical_build._read_physical_file(\n"
                    "    canonical_build.Path('.').resolve(),\n"
                    "    canonical_build.Path('secret.txt'),\n"
                    ")\n"
                    "def gen_step():\n"
                    "    return Box(0.4, 0.2, 0.1)\n"
                ),
            )

            result = _run_adapter(
                root,
                "build",
                "--source",
                source.relative_to(root).as_posix(),
                "--output-dir",
                "candidate",
            )

            self.assertNotEqual(0, result.returncode)
            self.assertFalse((root / "candidate/measurement.glb").exists())
            self.assertFalse((root / "candidate/build.json").exists())

    def test_adapter_forbids_network_and_child_processes_during_source_execution(self) -> None:
        cases = {
            "network": "\n".join(
                (
                    "import socket",
                    "from build123d import Box",
                    "def gen_step():",
                    "    socket.create_connection(('127.0.0.1', 9), timeout=0.01)",
                    "    return Box(0.1, 0.1, 0.1)",
                )
            ),
            "process": "\n".join(
                (
                    "import subprocess",
                    "from build123d import Box",
                    "def gen_step():",
                    "    subprocess.run(['true'], check=True)",
                    "    return Box(0.1, 0.1, 0.1)",
                )
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name), temporary_directory(prefix=f"cad-canonical-{name}-") as temp_dir:
                root = Path(temp_dir)
                _write_canonical_source(root, body=source)
                result = _run_adapter(
                    root,
                    "build",
                    "--source",
                    "source/model.py",
                    "--output-dir",
                    "candidate",
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("canonical build forbids", result.stderr)

    def test_invalid_or_unit_annotated_cad_source_cannot_publish_a_build(self) -> None:
        cases = {
            "invalid": "\n".join(
                (
                    "from build123d import Shape",
                    "def gen_step():",
                    "    return Shape()",
                )
            ),
            "semantic-unit": "\n".join(
                (
                    "from build123d import Box",
                    "WIDTH_MM = 0.2",
                    "def gen_step():",
                    "    return Box(WIDTH_MM, 0.1, 0.1)",
                )
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name), temporary_directory(prefix=f"cad-canonical-{name}-") as temp_dir:
                root = Path(temp_dir)
                _write_canonical_source(root, body=source)
                result = _run_adapter(
                    root,
                    "build",
                    "--source",
                    "source/model.py",
                    "--output-dir",
                    "candidate",
                )
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((root / "candidate/build.json").exists())
                if name == "semantic-unit":
                    self.assertIn("unitless parameter names", result.stderr)

    def test_adapter_rejects_undeclared_output_files(self) -> None:
        with temporary_directory(prefix="cad-canonical-outputs-") as temp_dir:
            root = Path(temp_dir)
            _write_canonical_source(
                root,
                body="\n".join(
                    (
                        "from pathlib import Path",
                        "from build123d import Box",
                        "def gen_step():",
                        "    Path('candidate/debug.txt').write_text('debug', encoding='utf-8')",
                        "    return Box(0.2, 0.1, 0.1)",
                    )
                ),
            )
            result = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "candidate",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("undeclared output", result.stderr)
            self.assertFalse((root / "candidate/build.json").exists())

    def test_adapter_keeps_declared_inputs_read_only(self) -> None:
        with temporary_directory(prefix="cad-canonical-read-only-input-") as temp_dir:
            root = Path(temp_dir)
            _write_canonical_source(
                root,
                body="\n".join(
                    (
                        "from pathlib import Path",
                        "from build123d import Box",
                        "def gen_step():",
                        "    sidecar = Path('source/width.txt')",
                        "    width = float(sidecar.read_text(encoding='utf-8'))",
                        "    sidecar.write_text('0.8', encoding='utf-8')",
                        "    return Box(width, 0.1, 0.1)",
                    )
                ),
            )
            sidecar = root / "source/width.txt"
            sidecar.write_text("0.2\n", encoding="utf-8")
            result = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--input",
                "source/width.txt",
                "--output-dir",
                "candidate",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("outside the output directory", result.stderr)
            self.assertEqual("0.2\n", sidecar.read_text(encoding="utf-8"))

    def test_adapter_confines_non_open_filesystem_mutations_to_output_directory(self) -> None:
        cases = {
            "unlink": "Path('victim.txt').unlink()",
            "rename": "Path('victim.txt').rename('moved.txt')",
            "mkdir": "Path('outside').mkdir()",
            "mkfifo": "os.mkfifo('outside.fifo')",
            "hardlink": (
                "os.link('victim.txt', 'candidate/hardlink.txt'); "
                "Path('candidate/hardlink.txt').write_text('changed', encoding='utf-8')"
            ),
        }
        for name, mutation in cases.items():
            with self.subTest(name=name), temporary_directory(prefix=f"cad-canonical-{name}-") as temp_dir:
                root = Path(temp_dir)
                _write_canonical_source(
                    root,
                    body="\n".join(
                        (
                            "import os",
                            "from pathlib import Path",
                            "from build123d import Box",
                            "def gen_step():",
                            f"    {mutation}",
                            "    return Box(0.1, 0.1, 0.1)",
                        )
                    ),
                )
                victim = root / "victim.txt"
                victim.write_text("keep\n", encoding="utf-8")

                result = _run_adapter(
                    root,
                    "build",
                    "--source",
                    "source/model.py",
                    "--output-dir",
                    "candidate",
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn("outside the output directory", result.stderr)
                self.assertEqual("keep\n", victim.read_text(encoding="utf-8"))
                self.assertFalse((root / "moved.txt").exists())
                self.assertFalse((root / "outside").exists())
                self.assertFalse((root / "outside.fifo").exists())

    def test_adapter_rejects_ambient_nondeterministic_inputs(self) -> None:
        runtime_file = Path(sys.base_prefix) / "pyvenv.cfg"
        if not runtime_file.is_file():
            runtime_file = Path(tempfile.__file__)
        cases = {
            "environment": (
                "import os\nfrom build123d import Box\ndef gen_step():\n"
                "    return Box(float(os.getenv('CANONICAL_WIDTH', '0.1')), 0.1, 0.1)\n"
            ),
            "clock": (
                "import time\nfrom build123d import Box\ndef gen_step():\n"
                "    return Box(0.1 + time.time() * 0.0, 0.1, 0.1)\n"
            ),
            "random": (
                "import random\nfrom build123d import Box\ndef gen_step():\n"
                "    return Box(0.1 + random.random() * 0.0, 0.1, 0.1)\n"
            ),
            "runtime-file": (
                "from pathlib import Path\nfrom build123d import Box\ndef gen_step():\n"
                f"    Path({str(runtime_file)!r}).read_bytes()\n"
                "    return Box(0.1, 0.1, 0.1)\n"
            ),
            "sys-argv": (
                "import sys\nfrom build123d import Box\ndef gen_step():\n"
                "    return Box(0.1 if sys.argv[1] == 'build' else 0.2, 0.1, 0.1)\n"
            ),
            "file-metadata": (
                "from pathlib import Path\nfrom build123d import Box\ndef gen_step():\n"
                "    modified = Path('source/model.py').stat().st_mtime\n"
                "    return Box(0.1 + modified * 0.0, 0.1, 0.1)\n"
            ),
            "hash-alias": (
                "from build123d import Box\nambient_hash = hash\ndef gen_step():\n"
                "    return Box(0.1 if ambient_hash('canonical') % 2 else 0.2, 0.1, 0.1)\n"
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name), temporary_directory(prefix=f"cad-canonical-{name}-") as temp_dir:
                root = Path(temp_dir)
                _write_canonical_source(root, body=source)
                result = _run_adapter(
                    root,
                    "build",
                    "--source",
                    "source/model.py",
                    "--output-dir",
                    "candidate",
                )
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((root / "candidate/build.json").exists())

    def test_adapter_applies_determinism_policy_to_declared_python_helpers(self) -> None:
        with temporary_directory(prefix="cad-canonical-helper-policy-") as temp_dir:
            root = Path(temp_dir)
            _write_canonical_source(
                root,
                body=(
                    "from build123d import Box\n"
                    "from helper import ambient_hash\n"
                    "def gen_step():\n"
                    "    return Box(0.1 if ambient_hash('canonical') % 2 else 0.2, 0.1, 0.1)\n"
                ),
            )
            (root / "source/helper.py").write_text("ambient_hash = hash\n", encoding="utf-8")

            result = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--input",
                "source/helper.py",
                "--output-dir",
                "candidate",
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("ambient nondeterministic input", result.stderr)
            self.assertFalse((root / "candidate/build.json").exists())

    def test_rebuild_rejects_unregistered_or_network_enabled_recipe(self) -> None:
        with temporary_directory(prefix="cad-canonical-recipe-policy-") as temp_dir:
            root = Path(temp_dir)
            _write_canonical_source(root)
            initial = _run_adapter(
                root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "candidate",
            )
            self.assertEqual(0, initial.returncode, initial.stderr)
            original = json.loads((root / "candidate/rebuild.json").read_text(encoding="utf-8"))

            for name, mutation in (
                ("executable", {"executable": "/bin/sh"}),
                ("network", {"network": "allowed"}),
                ("ambient", {"ambientInputs": "allowed"}),
                ("filesystem", {"filesystem": "unconfined"}),
                ("profile", {"profile": {"id": "different", "digest": "0" * 64}}),
                ("runtime", {"runtime": {}}),
                ("fields", {"shell": "python source/model.py"}),
            ):
                with self.subTest(name=name):
                    recipe = {**original, **mutation}
                    recipe_path = root / f"{name}.json"
                    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
                    result = _run_adapter(
                        root,
                        "rebuild",
                        "--recipe",
                        recipe_path.name,
                        "--output-dir",
                        f"rebuilt-{name}",
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("unsupported", result.stderr)


if __name__ == "__main__":
    unittest.main()
