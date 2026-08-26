from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from tests.python.support.paths import repo_path
from tests.python.support.tmp_root import temporary_directory


ADAPTER = repo_path("skills/cad/scripts/canonical-build")
CADGEN_SRC = repo_path("packages/cadgen/src")


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
        (str(CADGEN_SRC), env["PYTHONPATH"]) if env.get("PYTHONPATH") else (str(CADGEN_SRC),)
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
    def test_registered_entrypoint_bootstraps_its_vendored_cadgen_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_text:
            root = Path(temp_text)
            entrypoint_dir = root / "scripts/canonical-build"
            package_dir = root / "scripts/packages/cadgen/src/cadgen"
            ambient_dir = root / "ambient/cadgen"
            entrypoint_dir.mkdir(parents=True)
            package_dir.mkdir(parents=True)
            ambient_dir.mkdir(parents=True)
            shutil.copy2(ADAPTER / "__main__.py", entrypoint_dir / "__main__.py")
            (package_dir / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "canonical_build.py").write_text(
                "def main():\n    print('vendored-cadgen-loaded')\n    return 0\n",
                encoding="utf-8",
            )
            (ambient_dir / "__init__.py").write_text("", encoding="utf-8")
            (ambient_dir / "canonical_build.py").write_text(
                "def main():\n    print('ambient-cadgen-loaded')\n    return 0\n",
                encoding="utf-8",
            )

            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            entrypoint = entrypoint_dir / "__main__.py"
            runtime_root = package_dir.parent
            bootstrap = "\n".join(
                (
                    "import runpy, sys",
                    f"sys.path[:0] = [{str(ambient_dir.parent)!r}, {str(runtime_root)!r}]",
                    f"runpy.run_path({str(entrypoint)!r}, run_name='__main__')",
                )
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", bootstrap],
                cwd=root,
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("vendored-cadgen-loaded", result.stdout.strip())

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

    def test_location_transform_uses_supported_composition(self) -> None:
        invalid_source = "\n".join(
            (
                "from build123d import BuildPart, Box, Location",
                "def gen_step():",
                "    with BuildPart() as part:",
                "        with Location((0.1, 0.2, 0.3)):",
                "            Box(0.2, 0.1, 0.05)",
                "    return part.part",
            )
        )
        with temporary_directory(prefix="cad-location-transform-") as invalid_root_text:
            invalid_root = Path(invalid_root_text)
            _write_canonical_source(invalid_root, body=invalid_source)
            invalid = _run_adapter(
                invalid_root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "candidate",
            )
            self.assertNotEqual(0, invalid.returncode)
            self.assertIn(
                "Location' object does not support the context manager protocol",
                invalid.stderr,
            )

        valid_source = "\n".join(
            (
                "from build123d import Align, Box, BuildPart, Locations",
                "def gen_step():",
                "    with BuildPart() as part:",
                "        with Locations((0.1, 0.2, 0.3)):",
                "            Box(0.2, 0.1, 0.05, align=(Align.CENTER, Align.CENTER, Align.CENTER))",
                "    return part.part",
            )
        )
        with temporary_directory(prefix="cad-location-transform-") as valid_root_text:
            valid_root = Path(valid_root_text)
            _write_canonical_source(valid_root, body=valid_source)
            valid = _run_adapter(
                valid_root,
                "build",
                "--source",
                "source/model.py",
                "--output-dir",
                "candidate",
            )
            self.assertEqual(0, valid.returncode, valid.stderr)
            minimum, maximum = _position_bounds(valid_root / "candidate/measurement.glb")
            minimum = [round(value, 6) for value in minimum]
            maximum = [round(value, 6) for value in maximum]
            self.assertEqual([0.0, 0.15, 0.275], minimum)
            self.assertEqual([0.2, 0.25, 0.325], maximum)
            self.assertEqual(
                [0.1, 0.2, 0.3],
                [round((low + high) / 2.0, 6) for low, high in zip(minimum, maximum)],
            )

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
            manifest = json.loads((root / "candidate/build.json").read_text(encoding="utf-8"))
            profile = json.loads((root / "candidate/profile.json").read_text(encoding="utf-8"))
            recipe = json.loads((root / "candidate/rebuild.json").read_text(encoding="utf-8"))

            self.assertEqual("mesh-to-cad.build/1", manifest["schema"])
            self.assertNotIn("route", manifest)
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

    def test_build_records_recipe_inputs_relative_to_nested_candidate_bundle(self) -> None:
        with temporary_directory(prefix="cad-canonical-nested-bundle-") as temp_dir:
            root = Path(temp_dir)
            candidate = root / "work/attempts/000004/candidate"
            _write_canonical_source(candidate)

            result = _run_adapter(
                root,
                "build",
                "--source",
                "work/attempts/000004/candidate/source/model.py",
                "--output-dir",
                "work/attempts/000004/candidate/artifacts",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            recipe = json.loads(
                (candidate / "artifacts/rebuild.json").read_text(encoding="utf-8")
            )
            self.assertEqual("source/model.py", recipe["inputs"][0]["path"])

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
                # The adapter has two adjacent rejection branches for a
                # path that would escape the build root, both defined in
                # ``packages/cadgen/src/cadgen/canonical_build.py``:
                # ``must be a confined relative path`` fires for a
                # POSIX-form absolute path or a segment in
                # ``{"", ".", ".."}``; ``must be a non-empty POSIX
                # relative path`` fires for any input containing the
                # native Windows path separator ``\``. Which of the two
                # a caller hits depends on ``str(Path)`` on the running
                # host -- POSIX renders absolute paths as ``/...`` and
                # trips the first branch, Windows renders them as
                # ``C:\...`` and trips the second. Both branches are the
                # same confinement contract: the adapter refuses inputs
                # that are not root-relative POSIX paths. Assert against
                # the contract, not the platform-specific message.
                self.assertRegex(
                    result.stderr,
                    r"must be a (confined relative path|non-empty POSIX relative path)",
                )

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
            "hardlink": (
                "os.link('victim.txt', 'candidate/hardlink.txt'); "
                "Path('candidate/hardlink.txt').write_text('changed', encoding='utf-8')"
            ),
        }
        # ``os.mkfifo`` is POSIX-only -- Windows does not expose it, and
        # the source policy in ``packages/cadgen/src/cadgen/canonical_build.py``
        # patches it via ``setattr(os, mutation_name, ...)``. Enumerate
        # the platform-supported mutation APIs so the test still asserts
        # the same confinement contract for every wrapper present on the
        # host without raising ``AttributeError`` inside the child
        # process. POSIX still covers mkfifo; Windows still covers
        # unlink/rename/mkdir/hardlink.
        if hasattr(os, "mkfifo"):
            cases["mkfifo"] = "os.mkfifo('outside.fifo')"
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
