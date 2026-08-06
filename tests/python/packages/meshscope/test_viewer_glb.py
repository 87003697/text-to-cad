from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
import trimesh

from meshscope.viewer_glb import (
    PREVIEW_BASE_COLOR,
    export_viewer_glb,
    normalize_preview_gltf,
    prepare_viewer_mesh,
)


def _glb_tree(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        raise AssertionError("not a GLB file")
    json_length = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20 : 20 + json_length].decode("utf-8"))


class ViewerGlbTests(unittest.TestCase):
    def test_prepare_viewer_mesh_maps_cad_z_up_to_gltf_y_up(self):
        mesh = trimesh.Trimesh(
            vertices=np.array(
                [
                    [2.0, 3.0, 5.0],
                    [-7.0, 11.0, 13.0],
                    [17.0, -19.0, 23.0],
                ]
            ),
            faces=np.array([[0, 1, 2]]),
            process=False,
        )

        prepared = prepare_viewer_mesh(mesh)

        np.testing.assert_allclose(
            prepared.vertices,
            np.array(
                [
                    [2.0, 5.0, -3.0],
                    [-7.0, 13.0, -11.0],
                    [17.0, 23.0, 19.0],
                ]
            ),
        )
        np.testing.assert_allclose(mesh.vertices[0], [2.0, 3.0, 5.0])

    def test_normalize_preview_gltf_assigns_neutral_material_to_every_primitive(self):
        tree = {
            "asset": {"version": "2.0"},
            "meshes": [
                {
                    "primitives": [
                        {"attributes": {"POSITION": 0, "COLOR_0": 1}},
                        {"attributes": {"POSITION": 2}, "material": 0},
                    ]
                }
            ],
            "materials": [
                {
                    "pbrMetallicRoughness": {
                        "baseColorTexture": {"index": 0},
                        "baseColorFactor": [0.05, 0.05, 0.05, 1.0],
                    }
                }
            ],
        }

        normalize_preview_gltf(tree)

        primitives = tree["meshes"][0]["primitives"]
        self.assertEqual([primitive["material"] for primitive in primitives], [0, 0])
        self.assertNotIn("COLOR_0", primitives[0]["attributes"])
        material = tree["materials"][0]
        pbr = material["pbrMetallicRoughness"]
        self.assertNotIn("baseColorTexture", pbr)
        self.assertEqual(pbr["baseColorFactor"], PREVIEW_BASE_COLOR)
        self.assertIs(material["extras"]["cadSourceColor"], False)

    def test_export_viewer_glb_has_one_coordinate_and_material_contract(self):
        mesh = trimesh.Trimesh(
            vertices=np.array(
                [
                    [2.0, 3.0, 5.0],
                    [-7.0, 11.0, 13.0],
                    [17.0, -19.0, 23.0],
                ]
            ),
            faces=np.array([[0, 1, 2]]),
            process=False,
        )
        mesh.visual.vertex_colors = np.tile([0, 0, 0, 255], (len(mesh.vertices), 1))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "asymmetric.ply"
            output = root / "preview.glb"
            mesh.export(source)

            result = export_viewer_glb(source, output)
            loaded = trimesh.load(result, force="mesh", process=False)
            tree = _glb_tree(result)

        expected = np.array(
            [
                [2.0, 5.0, -3.0],
                [-7.0, 13.0, -11.0],
                [17.0, 23.0, 19.0],
            ]
        )
        np.testing.assert_allclose(
            np.asarray(loaded.vertices)[np.argsort(loaded.vertices[:, 0])],
            expected[np.argsort(expected[:, 0])],
        )
        self.assertEqual(
            tree["asset"]["extras"]["cadPreview"],
            {
                "sourceUpAxis": "z",
                "storedUpAxis": "y",
                "material": "viewer-default",
            },
        )
        primitive = tree["meshes"][0]["primitives"][0]
        self.assertNotIn("COLOR_0", primitive["attributes"])
        self.assertIn("NORMAL", primitive["attributes"])
        self.assertEqual(
            tree["materials"][primitive["material"]]["pbrMetallicRoughness"]["baseColorFactor"],
            PREVIEW_BASE_COLOR,
        )


if __name__ == "__main__":
    unittest.main()
