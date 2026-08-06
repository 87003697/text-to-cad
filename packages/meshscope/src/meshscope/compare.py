"""Similarity metrics between two 3D meshes (Trellis2-normalized).

The caller guarantees coordinate frames match; this module only removes
scale differences via bbox-max normalization to `[-0.5, 0.5]^3`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from meshscope.io import load


def normalize(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, float, np.ndarray]:
    """Normalize a mesh into the Trellis2 unit box `[-0.5, 0.5]^3`.

    Uses `(vertices - bbox_center) / max(extents)`, matching Trellis2
    `trellis/pipelines/trellis_text_to_3d.py` so chamfer values here are
    cross-comparable with Trellis2 / Point-E / Shap-E / DSO results.
    """
    bbox_min, bbox_max = mesh.bounds[0], mesh.bounds[1]
    center = (bbox_min + bbox_max) / 2
    extents = bbox_max - bbox_min
    scale = float(extents.max())
    if scale < 1e-10:
        scale = 1.0
    result = mesh.copy()
    result.vertices = (mesh.vertices - center) / scale
    return result, scale, center


@dataclass
class PreparedPair:
    """Shared normalized state that threads through compare/viz."""

    norm_a: trimesh.Trimesh
    norm_b: trimesh.Trimesh
    scale_a: float
    scale_b: float
    center_a: np.ndarray
    center_b: np.ndarray


def _sample_surface(mesh: trimesh.Trimesh, count: int, seed: int) -> np.ndarray:
    """Sample deterministically across supported trimesh releases.

    ``Trimesh.sample(seed=...)`` was added after the CVM-pinned trimesh
    release.  The module-level sampler already accepts ``seed`` there, so use
    that stable API directly instead of relying on the newer convenience
    method.
    """
    points, _ = trimesh.sample.sample_surface(mesh, count, seed=seed)
    return points


def prepare(path_a: str | Path, path_b: str | Path) -> PreparedPair:
    """Load + normalize two meshes; return the shared prepared state.

    Does NOT align: the caller is responsible for consistent coordinate
    frames. This function only removes scale differences.
    """
    mesh_a = load(path_a)
    mesh_b = load(path_b)

    norm_a, scale_a, center_a = normalize(mesh_a)
    norm_b, scale_b, center_b = normalize(mesh_b)

    return PreparedPair(
        norm_a=norm_a,
        norm_b=norm_b,
        scale_a=scale_a,
        scale_b=scale_b,
        center_a=center_a,
        center_b=center_b,
    )


def compare(
    pair: PreparedPair,
    n_samples: int = 50000,
    include_distances: bool = False,
    seed: int = 0,
) -> dict:
    """Compute deterministic Chamfer / tail stats from a PreparedPair."""
    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    if seed < 0:
        raise ValueError("seed must be non-negative")

    # Use different fixed streams for the two surfaces.  Reusing one stream
    # can make identical-topology meshes look artificially perfect, while
    # leaving the streams implicit makes threshold decisions non-reproducible.
    pts_a = _sample_surface(pair.norm_a, n_samples, seed)
    pts_b = _sample_surface(pair.norm_b, n_samples, seed + 1)

    tree_a = cKDTree(pts_a)
    tree_b = cKDTree(pts_b)

    dist_a2b, _ = tree_b.query(pts_a)
    dist_b2a, _ = tree_a.query(pts_b)

    chamfer = float((dist_a2b.mean() + dist_b2a.mean()) / 2)
    hausdorff = float(max(dist_a2b.max(), dist_b2a.max()))

    result = {
        "chamfer": chamfer,
        "hausdorff": hausdorff,
        "stats": {
            "mean_a2b": float(dist_a2b.mean()),
            "mean_b2a": float(dist_b2a.mean()),
            "median_a2b": float(np.median(dist_a2b)),
            "median_b2a": float(np.median(dist_b2a)),
            "p90_a2b": float(np.percentile(dist_a2b, 90)),
            "p90_b2a": float(np.percentile(dist_b2a, 90)),
            "p95_a2b": float(np.percentile(dist_a2b, 95)),
            "p95_b2a": float(np.percentile(dist_b2a, 95)),
            "max_a2b": float(dist_a2b.max()),
            "max_b2a": float(dist_b2a.max()),
        },
        "meta": {
            "n_samples": n_samples,
            "sample_seed": seed,
            "sampling": "trimesh_surface_seeded",
            "normalization": "trellis2",
            "scale_a": float(pair.scale_a),
            "scale_b": float(pair.scale_b),
        },
    }

    if include_distances:
        result["distances_a2b"] = dist_a2b.tolist()
        result["distances_b2a"] = dist_b2a.tolist()

    return result


def vertex_distances(
    pair: PreparedPair,
    n_samples: int = 50000,
    seed: int = 0,
) -> np.ndarray:
    """Per-vertex distance from `norm_a.vertices` to the sampled `norm_b` surface.

    Shares the same PreparedPair and B-surface random stream with `compare()`,
    so the heatmap values and chamfer number use the same normalized frame and
    deterministic target sample.
    """
    pts_b = _sample_surface(pair.norm_b, n_samples, seed + 1)
    tree_b = cKDTree(pts_b)
    dists, _ = tree_b.query(pair.norm_a.vertices)
    return dists
