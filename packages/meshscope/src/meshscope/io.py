from pathlib import Path

import trimesh

SUPPORTED_EXTENSIONS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".3mf"}


class MeshLoadError(Exception):
    pass


def load(path: str | Path) -> trimesh.Trimesh:
    path = Path(path)
    if not path.exists():
        raise MeshLoadError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise MeshLoadError(
            f"Unsupported format '{suffix}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    try:
        result = trimesh.load(str(path), force="mesh")
    except Exception as e:
        raise MeshLoadError(f"Failed to load {path.name}: {e}") from e

    if not isinstance(result, trimesh.Trimesh):
        raise MeshLoadError(
            f"Unexpected load result: {type(result).__name__} (expected Trimesh)"
        )

    if len(result.vertices) == 0:
        raise MeshLoadError(f"Loaded mesh has no vertices: {path.name}")

    return result
