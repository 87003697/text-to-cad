"""Coordinate and material contract for mesh-compare Viewer renders."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import trimesh


REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_PATH = (
    REPO_ROOT
    / "skills"
    / "mesh-compare"
    / "scripts"
    / "mesh-render"
    / "cli.py"
)


def _load_cli():
    spec = importlib.util.spec_from_file_location("mesh_render_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _glb_tree(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        raise AssertionError("not a GLB file")
    json_length = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20 : 20 + json_length].decode("utf-8"))


class MeshRenderGlbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = _load_cli()

    def test_non_glb_uses_shared_viewer_export(self):
        mesh = trimesh.creation.box(extents=[9.0, 5.0, 2.0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "asymmetric.ply"
            mesh.export(source)
            result = self.cli._ensure_glb(source, root / "glb")
            tree = _glb_tree(result)

        self.assertEqual(tree["asset"]["extras"]["cadPreview"]["storedUpAxis"], "y")

    def test_existing_glb_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "native.glb"
            source.write_bytes(b"already-a-glb")
            self.assertEqual(source, self.cli._ensure_glb(source, Path(temp) / "glb"))

    def test_side_by_side_keeps_same_basename_inputs_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_a = root / "reference" / "model.ply"
            source_b = root / "candidate" / "model.ply"
            source_a.parent.mkdir()
            source_b.parent.mkdir()
            trimesh.creation.box(extents=[9.0, 5.0, 2.0]).export(source_a)
            trimesh.creation.icosphere(radius=3.0).export(source_b)

            glb_a, glb_b = self.cli._prepare_side_by_side_inputs(
                source_a,
                source_b,
                root / "tmp",
            )

            self.assertNotEqual(glb_a, glb_b)
            self.assertEqual(glb_a.parent.name, "a")
            self.assertEqual(glb_b.parent.name, "b")
            self.assertNotEqual(glb_a.read_bytes(), glb_b.read_bytes())

    def test_cli_bootstraps_shared_meshscope_runtime(self):
        self.assertTrue(self.cli._BUNDLED_MESHSCOPE.is_dir())
        self.assertIn(str(self.cli._BUNDLED_MESHSCOPE), sys.path)
        module = sys.modules[self.cli.export_viewer_glb.__module__]
        self.assertEqual(
            Path(module.__file__).resolve(),
            (self.cli._BUNDLED_MESHSCOPE / "meshscope" / "viewer_glb.py").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
