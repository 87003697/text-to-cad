from pathlib import Path

import numpy as np

from meshscope.io import load


def inspect(path: str | Path) -> dict:
    mesh = load(path)
    result = {}
    result["file"] = {"name": Path(path).name, "format": Path(path).suffix.lower()}
    result["stats"] = _compute_stats(mesh)
    result["quality"] = _compute_quality(mesh)
    result["canonical_frame"] = _compute_canonical_frame(mesh)
    return result


def _compute_stats(mesh) -> dict:
    bb_min = mesh.bounds[0].tolist()
    bb_max = mesh.bounds[1].tolist()
    size = (mesh.bounds[1] - mesh.bounds[0]).tolist()
    return {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "edges": len(mesh.edges_unique),
        "bounding_box": {"min": bb_min, "max": bb_max, "size": size},
        "volume": float(mesh.volume) if mesh.is_volume else None,
        "surface_area": float(mesh.area),
    }


def _compute_quality(mesh) -> dict:
    non_degenerate = mesh.nondegenerate_faces()
    degenerate_count = len(mesh.faces) - int(non_degenerate.sum())
    return {
        "watertight": bool(mesh.is_watertight),
        "volume_valid": bool(mesh.is_volume),
        "degenerate_faces": degenerate_count,
        "euler_number": int(mesh.euler_number),
    }


def _compute_canonical_frame(mesh) -> dict:
    centered = mesh.vertices - mesh.centroid
    cov = np.cov(centered.T)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]
    return {
        "center": mesh.centroid.tolist(),
        "pca_axes": eigenvectors.T.tolist(),
        "eigenvalues": eigenvalues.tolist(),
    }
