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
    / "mesh-inspect"
    / "scripts"
    / "mesh-preview"
    / "cli.py"
)


def _load_cli():
    spec = importlib.util.spec_from_file_location("mesh_preview_cli", CLI_PATH)
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


class MeshPreviewGlbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = _load_cli()

    def test_non_glb_uses_shared_export_and_persists_output(self):
        mesh = trimesh.creation.box(extents=[9.0, 5.0, 2.0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "asymmetric.ply"
            output = root / "input_preview.glb"
            mesh.export(source)

            result = self.cli._ensure_glb(source, root / "tmp", output)
            tree = _glb_tree(result)

        self.assertEqual(result, output)
        self.assertEqual(
            tree["asset"]["extras"]["cadPreview"],
            {
                "sourceUpAxis": "z",
                "storedUpAxis": "y",
                "material": "viewer-default",
            },
        )

    def test_existing_glb_can_be_copied_to_persistent_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.glb"
            output = root / "copy.glb"
            source.write_bytes(b"existing-glb")

            result = self.cli._ensure_glb(source, root / "tmp", output)

            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"existing-glb")

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
