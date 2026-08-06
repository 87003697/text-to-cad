"""Shared GLB preparation for CAD Viewer mesh previews.

Repository mesh workflows treat non-glTF source meshes as CAD Z-up. Generic
glTF stores Y-up coordinates, and cadjs converts those coordinates back to CAD
Z-up when it loads an ordinary GLB. This module owns the forward conversion so
every mesh skill produces the same pose and neutral preview material.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh


CAD_Z_UP_TO_GLTF_Y_UP = np.array(
    [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)

PREVIEW_BASE_COLOR = [0.72, 0.72, 0.72, 1.0]


def prepare_viewer_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Return a copy converted from repository CAD Z-up to glTF Y-up."""

    prepared = mesh.copy()
    prepared.apply_transform(CAD_Z_UP_TO_GLTF_Y_UP)
    return prepared


def normalize_preview_gltf(tree: dict) -> None:
    """Apply the neutral CAD preview material contract to a glTF JSON tree."""

    asset = tree.setdefault("asset", {})
    extras = asset.setdefault("extras", {})
    extras["cadPreview"] = {
        "sourceUpAxis": "z",
        "storedUpAxis": "y",
        "material": "viewer-default",
    }

    primitives = [
        primitive
        for mesh in tree.get("meshes", [])
        for primitive in mesh.get("primitives", [])
    ]
    for primitive in primitives:
        primitive.get("attributes", {}).pop("COLOR_0", None)

    materials = tree.setdefault("materials", [])
    if not materials:
        materials.append({"name": "CAD preview"})
    for primitive in primitives:
        primitive.setdefault("material", 0)

    for material in materials:
        pbr = material.setdefault("pbrMetallicRoughness", {})
        pbr.pop("baseColorTexture", None)
        pbr["baseColorFactor"] = list(PREVIEW_BASE_COLOR)
        material.setdefault("extras", {})["cadSourceColor"] = False


def export_viewer_glb(mesh_path: str | Path, output_path: str | Path) -> Path:
    """Convert a CAD Z-up mesh file into a neutral, standard Y-up preview GLB."""

    source = Path(mesh_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    mesh = trimesh.load(str(source), force="mesh")
    prepared = prepare_viewer_mesh(mesh)
    output.write_bytes(
        trimesh.exchange.gltf.export_glb(
            prepared,
            include_normals=True,
            tree_postprocessor=normalize_preview_gltf,
        )
    )
    return output
