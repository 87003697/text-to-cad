#!/usr/bin/env python3
"""Fact-only DfAM geometry measurements for mesh files.

Reports measurements as JSON. It never emits pass/fail, verdicts, or
readiness statuses; comparisons against process limits belong to the
skill workflow using `references/process-limits.md`.

Requires: trimesh, numpy, rtree (pip install trimesh numpy rtree)

Usage:
    python dfam_tool.py measure <mesh> [--samples 2000] [--angle-limit 45]
    python dfam_tool.py orientations <mesh> [--angle-limit 45]

`--angle-limit` only parameterises which faces are *counted* in the
support-area aggregates; per-face angles are always reported so the
agent can re-bin against any process limit.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import trimesh


def _load(path: str) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    return mesh


def _mesh_facts(mesh: trimesh.Trimesh) -> dict:
    return {
        "bbox_mm": [round(float(v), 2) for v in mesh.extents],
        "volume_mm3": round(float(abs(mesh.volume)), 1) if mesh.is_volume else None,
        "surface_area_mm2": round(float(mesh.area), 1),
        "triangle_count": int(len(mesh.faces)),
        "watertight": bool(mesh.is_watertight),
        "euler_number": int(mesh.euler_number),
        "body_count": int(mesh.body_count),
    }


def _overhang_facts(mesh: trimesh.Trimesh, angle_limit: float) -> dict:
    """Face angles measured from horizontal: 0 = flat ceiling, 90 = vertical."""
    normals = mesh.face_normals
    areas = mesh.area_faces
    centers = mesh.triangles_center

    down = normals[:, 2] < -1e-6
    surface_angle = 90.0 - np.degrees(np.arcsin(np.clip(-normals[:, 2], 0, 1)))

    z_min = mesh.bounds[0][2]
    on_plate = centers[:, 2] < (z_min + 0.1)

    counted = down & ~on_plate & (surface_angle < angle_limit)
    total_area = float(areas.sum())

    # angle histogram of down-facing, off-plate faces (10° bins)
    off_plate_down = down & ~on_plate
    hist = {}
    if off_plate_down.any():
        bins = np.arange(0, 100, 10)
        idx = np.digitize(surface_angle[off_plate_down], bins) - 1
        for b in range(len(bins) - 1):
            area = float(areas[off_plate_down][idx == b].sum())
            if area > 0:
                hist[f"{bins[b]}-{bins[b+1]}deg"] = round(area, 2)

    worst = []
    if counted.any():
        w_idx = np.where(counted)[0]
        order = w_idx[np.argsort(-areas[w_idx])][:8]
        worst = [
            {
                "location_xyz": [round(float(v), 2) for v in centers[i]],
                "surface_angle_deg": round(float(surface_angle[i]), 1),
                "area_mm2": round(float(areas[i]), 2),
            }
            for i in order
        ]

    return {
        "angle_limit_used_deg": angle_limit,
        "down_facing_area_below_limit_mm2": round(float(areas[counted].sum()), 2),
        "down_facing_area_below_limit_pct": round(
            100 * float(areas[counted].sum()) / total_area, 1) if total_area else 0.0,
        "face_count_below_limit": int(counted.sum()),
        "down_facing_angle_histogram_mm2": hist,
        "largest_faces_below_limit": worst,
    }


def _wall_facts(mesh: trimesh.Trimesh, samples: int, seed: int = 42) -> dict:
    """Ray-cast thickness field from area-weighted surface samples."""
    rng = np.random.default_rng(seed)
    n = min(samples, max(len(mesh.faces), 1))
    face_idx = rng.choice(len(mesh.faces), size=n,
                          p=mesh.area_faces / mesh.area_faces.sum())

    origins = mesh.triangles_center[face_idx]
    directions = -mesh.face_normals[face_idx]
    origins = origins + directions * 1e-4

    locations, ray_ids, _ = mesh.ray.intersects_location(
        ray_origins=origins, ray_directions=directions, multiple_hits=False)

    if len(ray_ids) == 0:
        return {"error": "ray casting produced no hits", "samples_requested": n}

    thickness = np.linalg.norm(locations - origins[ray_ids], axis=1)
    diag = float(np.linalg.norm(mesh.extents))
    valid = (thickness > 1e-3) & (thickness < diag)
    thickness = thickness[valid]

    if len(thickness) == 0:
        return {"error": "no valid thickness samples", "samples_requested": n}

    hit_origins = origins[ray_ids][valid]
    thin_idx = np.argsort(thickness)[:8]

    return {
        "samples_valid": int(len(thickness)),
        "min_mm": round(float(thickness.min()), 3),
        "p05_mm": round(float(np.percentile(thickness, 5)), 3),
        "p25_mm": round(float(np.percentile(thickness, 25)), 3),
        "median_mm": round(float(np.median(thickness)), 3),
        "max_mm": round(float(thickness.max()), 3),
        "thinnest_samples": [
            {
                "location_xyz": [round(float(v), 2) for v in hit_origins[i]],
                "thickness_mm": round(float(thickness[i]), 3),
            }
            for i in thin_idx
        ],
    }


def _support_volume_facts(mesh: trimesh.Trimesh, angle_limit: float) -> dict:
    """Prism estimate of volume under faces below the given angle."""
    m = mesh.copy()
    m.apply_translation([0, 0, -m.bounds[0][2]])

    normals = m.face_normals
    areas = m.area_faces
    centers = m.triangles_center

    down = normals[:, 2] < -1e-6
    surface_angle = 90.0 - np.degrees(np.arcsin(np.clip(-normals[:, 2], 0, 1)))
    on_plate = centers[:, 2] < 0.1
    needs = down & ~on_plate & (surface_angle < angle_limit)

    proj_area = areas[needs] * np.abs(normals[needs, 2])
    support_vol = float((proj_area * centers[needs, 2]).sum())
    part_vol = float(abs(m.volume)) if m.is_volume else float(m.convex_hull.volume)

    return {
        "angle_limit_used_deg": angle_limit,
        "estimated_support_volume_mm3": round(support_vol, 1),
        "part_volume_mm3": round(part_vol, 1),
        "support_to_part_ratio_pct": round(
            100 * support_vol / part_vol, 1) if part_vol else 0.0,
        "method": "prism from face centroid to build plate; coarse upper-bound estimate",
    }


def _orientation_facts(mesh: trimesh.Trimesh, angle_limit: float) -> dict:
    """Support area + build height for 6 axis-aligned candidate orientations."""
    rotations = {
        "current_plus_z": np.eye(4),
        "flip_180_x": trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]),
        "rot_plus_90_x": trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]),
        "rot_minus_90_x": trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]),
        "rot_plus_90_y": trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]),
        "rot_minus_90_y": trimesh.transformations.rotation_matrix(-np.pi / 2, [0, 1, 0]),
    }
    out = []
    for name, T in rotations.items():
        m = mesh.copy()
        m.apply_transform(T)
        ov = _overhang_facts(m, angle_limit)
        out.append({
            "orientation": name,
            "support_area_mm2": ov["down_facing_area_below_limit_mm2"],
            "support_area_pct": ov["down_facing_area_below_limit_pct"],
            "build_height_mm": round(float(m.extents[2]), 2),
        })
    return {"angle_limit_used_deg": angle_limit, "candidates": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("measure", help="full measurement set for one mesh")
    m.add_argument("mesh")
    m.add_argument("--samples", type=int, default=2000)
    m.add_argument("--angle-limit", type=float, default=45.0)

    o = sub.add_parser("orientations", help="candidate orientation measurements")
    o.add_argument("mesh")
    o.add_argument("--angle-limit", type=float, default=45.0)

    args = ap.parse_args()

    try:
        mesh = _load(args.mesh)
    except Exception as e:
        print(json.dumps({"error": f"failed to load mesh: {e}"}))
        return 1

    if args.command == "measure":
        report = {
            "file": args.mesh,
            "mesh": _mesh_facts(mesh),
            "overhangs": _overhang_facts(mesh, args.angle_limit),
            "wall_thickness": _wall_facts(mesh, args.samples),
            "support_volume": _support_volume_facts(mesh, args.angle_limit),
        }
    else:
        report = {
            "file": args.mesh,
            "orientations": _orientation_facts(mesh, args.angle_limit),
        }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
