"""Public prepare-reference contract tests for mesh-compare."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")
add_repo_path("skills/mesh-compare/scripts/mesh-compare")

import cli  # noqa: E402

prepare_reference_module = importlib.import_module(
    "meshscope.voxblame.prepare_reference"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_obj(
    path: Path,
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, ...]],
) -> None:
    lines = [*(f"v {x} {y} {z}" for x, y, z in vertices)]
    lines.extend(f"f {' '.join(str(index) for index in face)}" for face in faces)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PrepareReferenceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def invoke(self, source: Path, output: Path) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(
                [
                    "voxblame-prepare-reference",
                    str(source),
                    "--output",
                    str(output),
                ]
            )
        return status, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_evaluates_scene_instances_and_atomically_publishes_canonical_reference(self) -> None:
        source = self.root / "instanced.glb"
        triangle = trimesh.Trimesh(
            vertices=np.array([[0, 0, 0], [2, 0, 0], [0, 1, 0]], dtype=np.float64),
            faces=np.array([[0, 1, 2]], dtype=np.int64),
            process=False,
        )
        scene = trimesh.Scene()
        scene.add_geometry(
            triangle,
            geom_name="shared-triangle",
            node_name="left-instance",
            transform=trimesh.transformations.translation_matrix([-1, 0, 0]),
        )
        scene.graph.update(
            frame_to="right-instance",
            matrix=trimesh.transformations.translation_matrix([3, 0, 0]),
            geometry="shared-triangle",
        )
        source.write_bytes(scene.export(file_type="glb"))
        output = self.root / "published" / "input"

        status, payload, stderr = self.invoke(source, output)

        self.assertEqual(0, status, stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            "voxblame.canonical-reference/1",
            payload["canonical_reference"]["schema"],
        )
        self.assertEqual(output, Path(payload["output"]))
        self.assertEqual(
            {"input.json", "normalization.json", "reference.ply", "original"},
            {path.name for path in output.iterdir()},
        )
        self.assertEqual([source.name], [path.name for path in (output / "original").iterdir()])

        manifest = json.loads((output / "input.json").read_text(encoding="utf-8"))
        normalization = json.loads(
            (output / "normalization.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["canonical_reference"], manifest)
        self.assertEqual("trellis2_canonical/1", normalization["coordinate_contract"])
        self.assertIsNone(normalization["semantic_units"])
        self.assertEqual(2, normalization["input_triangle_count"])
        self.assertEqual(0, normalization["removed_zero_area_triangle_count"])
        self.assertEqual(2, normalization["canonical_triangle_count"])
        self.assertEqual([-1.0, 0.0, 0.0], normalization["raw_bounds"]["min"])
        self.assertEqual([5.0, 1.0, 0.0], normalization["raw_bounds"]["max"])
        self.assertEqual([2.0, 0.5, 0.0], normalization["center"])
        self.assertEqual(1.0 / 6.0, normalization["scale"])
        self.assertEqual([-0.5, -1.0 / 12.0, 0.0], normalization["canonical_bounds"]["min"])
        self.assertEqual([0.5, 1.0 / 12.0, 0.0], normalization["canonical_bounds"]["max"])
        self.assertEqual(
            [
                [1.0 / 6.0, 0.0, 0.0, -1.0 / 3.0],
                [0.0, 1.0 / 6.0, 0.0, -1.0 / 12.0],
                [0.0, 0.0, 1.0 / 6.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            normalization["raw_to_canonical"],
        )
        self.assertEqual(
            [
                [6.0, 0.0, 0.0, 2.0],
                [0.0, 6.0, 0.0, 0.5],
                [0.0, 0.0, 6.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            normalization["canonical_to_raw"],
        )
        self.assertEqual(
            _sha256(source), normalization["raw_entry"]["sha256"]
        )
        self.assertEqual(
            _sha256(output / "reference.ply"),
            normalization["reference_ply"]["sha256"],
        )
        self.assertEqual(
            _sha256(output / "normalization.json"),
            manifest["normalization_json"]["sha256"],
        )
        self.assertEqual("float64", normalization["reference_ply"]["vertex_dtype"])
        self.assertIn(
            b"format binary_little_endian 1.0",
            (output / "reference.ply").read_bytes()[:256],
        )
        self.assertIn(b"property double x", (output / "reference.ply").read_bytes()[:256])
        reloaded = trimesh.load(output / "reference.ply", force="mesh", process=False)
        self.assertEqual(np.dtype("float64"), reloaded.vertices.dtype)
        self.assertEqual(2, len(reloaded.faces))
        self.assertFalse((output / "session.json").exists())
        self.assertFalse((output / "steps").exists())
        self.assertFalse(output.with_suffix(".failure.json").exists())

    def test_removes_only_strictly_zero_area_triangles_and_reports_counts(self) -> None:
        source = self.root / "part.obj"
        _write_obj(
            source,
            [(0, 0, 0), (2, 0, 0), (0, 1, 0), (1, 0, 0), (2, 1e-300, 0)],
            [(1, 2, 3), (1, 2, 4), (1, 2, 5)],
        )
        output = self.root / "input"

        status, _, stderr = self.invoke(source, output)

        self.assertEqual(0, status, stderr)
        normalization = json.loads((output / "normalization.json").read_text())
        self.assertEqual(3, normalization["input_triangle_count"])
        self.assertEqual(1, normalization["removed_zero_area_triangle_count"])
        self.assertEqual(2, normalization["canonical_triangle_count"])

    def test_deterministically_triangulates_polygon_faces(self) -> None:
        source = self.root / "quad.obj"
        _write_obj(
            source,
            [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)],
            [(1, 2, 3, 4)],
        )

        first_status, _, first_stderr = self.invoke(source, self.root / "first")
        second_status, _, second_stderr = self.invoke(source, self.root / "second")

        self.assertEqual(0, first_status, first_stderr)
        self.assertEqual(0, second_status, second_stderr)
        first = json.loads((self.root / "first/normalization.json").read_text())
        second = json.loads((self.root / "second/normalization.json").read_text())
        self.assertEqual(2, first["input_triangle_count"])
        self.assertEqual(first["reference_ply"]["sha256"], second["reference_ply"]["sha256"])

    def test_triangle_set_identity_ignores_order_indexing_and_winding(self) -> None:
        vertices = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
        first = self.root / "first.obj"
        second = self.root / "second.obj"
        _write_obj(first, vertices, [(1, 2, 3), (1, 3, 4)])
        _write_obj(
            second,
            [vertices[2], vertices[0], vertices[3], vertices[1]],
            [(3, 2, 1), (4, 2, 1)],
        )

        first_status, _, first_stderr = self.invoke(first, self.root / "first-input")
        second_status, _, second_stderr = self.invoke(second, self.root / "second-input")

        self.assertEqual(0, first_status, first_stderr)
        self.assertEqual(0, second_status, second_stderr)
        first_normalization = json.loads(
            (self.root / "first-input/normalization.json").read_text()
        )
        second_normalization = json.loads(
            (self.root / "second-input/normalization.json").read_text()
        )
        self.assertEqual(
            first_normalization["triangle_set_sha256"],
            second_normalization["triangle_set_sha256"],
        )

    def test_captures_external_gltf_geometry_buffers_with_digests(self) -> None:
        scene = trimesh.Scene(trimesh.creation.box(extents=(2, 1, 1)))
        exported = trimesh.exchange.gltf.export_gltf(scene, merge_buffers=True)
        source = self.root / "scene.gltf"
        for name, content in exported.items():
            target = source if name.endswith(".gltf") else self.root / name
            target.write_bytes(content)
        output = self.root / "input"

        status, _, stderr = self.invoke(source, output)

        self.assertEqual(0, status, stderr)
        normalization = json.loads((output / "normalization.json").read_text())
        dependencies = normalization["local_geometry_dependencies"]
        self.assertEqual(1, len(dependencies))
        dependency = dependencies[0]
        captured = output / dependency["captured_path"]
        self.assertTrue(captured.is_file())
        self.assertEqual(_sha256(captured), dependency["sha256"])
        self.assertEqual(
            {"entry", "geometry_dependency"},
            {
                item["role"]
                for item in json.loads((output / "input.json").read_text())[
                    "captured_files"
                ]
            },
        )

    def test_identical_rerun_is_idempotent_and_conflict_preserves_publication(self) -> None:
        first = self.root / "first.obj"
        second = self.root / "second.obj"
        _write_obj(first, [(0, 0, 0), (2, 0, 0), (0, 1, 0)], [(1, 2, 3)])
        _write_obj(second, [(0, 0, 0), (3, 0, 0), (0, 1, 0)], [(1, 2, 3)])
        output = self.root / "input"

        self.assertEqual(0, self.invoke(first, output)[0])
        original_manifest = (output / "input.json").read_bytes()
        status, payload, stderr = self.invoke(first, output)
        self.assertEqual(0, status, stderr)
        self.assertTrue(payload["idempotent"])

        status, payload, _ = self.invoke(second, output)
        self.assertNotEqual(0, status)
        self.assertEqual("conflicting_publication", payload["error"]["classification"])
        self.assertEqual(original_manifest, (output / "input.json").read_bytes())

    def test_idempotent_rerun_rejects_a_corrupt_existing_publication(self) -> None:
        source = self.root / "part.obj"
        _write_obj(source, [(0, 0, 0), (2, 0, 0), (0, 1, 0)], [(1, 2, 3)])
        output = self.root / "input"
        self.assertEqual(0, self.invoke(source, output)[0])
        (output / "reference.ply").write_bytes(b"corrupt")

        status, payload, _ = self.invoke(source, output)

        self.assertNotEqual(0, status)
        self.assertEqual("conflicting_publication", payload["error"]["classification"])
        self.assertEqual(b"corrupt", (output / "reference.ply").read_bytes())

    def test_invalid_inputs_publish_bounded_failure_evidence_only(self) -> None:
        missing = self.root / "missing.obj"
        empty = self.root / "empty.obj"
        empty.write_text("# no geometry\n", encoding="utf-8")
        degenerate = self.root / "degenerate.obj"
        _write_obj(degenerate, [(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(1, 2, 3)])
        tiny = self.root / "tiny.obj"
        _write_obj(tiny, [(0, 0, 0), (1e-16, 0, 0), (0, 1e-16, 0)], [(1, 2, 3)])
        unresolved = self.root / "unresolved.gltf"
        unresolved.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"uri": "missing.bin", "byteLength": 12}],
                }
            ),
            encoding="utf-8",
        )
        network = self.root / "network.gltf"
        network.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [
                        {
                            "uri": "https://example.com/mesh.bin",
                            "byteLength": 12,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        traversal_root = self.root / "traversal-root"
        traversal_root.mkdir()
        traversal = traversal_root / "scene.gltf"
        (traversal_root / "buffer.bin").write_bytes(b"geometry")
        traversal.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [
                        {
                            "uri": "../traversal-root/buffer.bin",
                            "byteLength": 8,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        cases = (
            (missing, "unreadable_input"),
            (empty, "empty_geometry"),
            (degenerate, "all_degenerate_geometry"),
            (tiny, "zero_extent_geometry"),
            (unresolved, "unresolved_local_dependency"),
            (network, "network_dependency"),
            (traversal, "unresolved_local_dependency"),
        )

        for index, (source, classification) in enumerate(cases):
            with self.subTest(classification=classification):
                output = self.root / f"invalid-{index}" / "input"
                status, payload, _ = self.invoke(source, output)
                self.assertNotEqual(0, status)
                self.assertFalse(payload["ok"])
                self.assertEqual(classification, payload["error"]["classification"])
                self.assertFalse(output.exists())
                failure_path = Path(payload["failure_evidence"])
                self.assertEqual(output.with_suffix(".failure.json"), failure_path)
                failure = json.loads(failure_path.read_text(encoding="utf-8"))
                self.assertEqual("voxblame.prepare-reference-failure/1", failure["schema"])
                self.assertEqual(classification, failure["classification"])
                self.assertLessEqual(len(failure["detail"]), 2000)
                self.assertLessEqual(len(failure["partial_artifacts"]), 16)

    def test_non_finite_geometry_preserves_captured_input_digest_as_failure_evidence(self) -> None:
        source = self.root / "non-finite.obj"
        source.write_text(
            "v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
            encoding="utf-8",
        )
        output = self.root / "input"

        status, payload, _ = self.invoke(source, output)

        self.assertNotEqual(0, status)
        self.assertEqual("non_finite_geometry", payload["error"]["classification"])
        self.assertFalse(output.exists())
        failure = json.loads(Path(payload["failure_evidence"]).read_text())
        self.assertEqual(
            [{
                "path": f"original/{source.name}",
                "sha256": _sha256(source),
                "size_bytes": source.stat().st_size,
            }],
            failure["partial_artifacts"],
        )

    def test_non_finite_scene_transform_is_an_unevaluable_scene(self) -> None:
        source = self.root / "bad-transform.glb"
        triangle = trimesh.Trimesh(
            vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            faces=[[0, 1, 2]],
            process=False,
        )
        transform = np.eye(4)
        transform[0, 3] = np.nan
        scene = trimesh.Scene()
        scene.add_geometry(triangle, node_name="bad", transform=transform)
        source.write_bytes(scene.export(file_type="glb"))
        output = self.root / "input"

        status, payload, _ = self.invoke(source, output)

        self.assertNotEqual(0, status)
        self.assertEqual("unevaluable_scene", payload["error"]["classification"])
        self.assertFalse(output.exists())

    def test_reload_bounds_violation_blocks_atomic_publication(self) -> None:
        source = self.root / "part.obj"
        _write_obj(source, [(0, 0, 0), (2, 0, 0), (0, 1, 0)], [(1, 2, 3)])
        output = self.root / "input"
        invalid_reload = trimesh.Trimesh(
            vertices=[[0, 0, 0], [0.6, 0, 0], [0, 0.6, 0]],
            faces=[[0, 1, 2]],
            process=False,
        )

        with mock.patch.object(
            prepare_reference_module.trimesh,
            "load",
            return_value=invalid_reload,
        ):
            status, payload, _ = self.invoke(source, output)

        self.assertNotEqual(0, status)
        self.assertEqual(
            "canonical_bounds_violation", payload["error"]["classification"]
        )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
