#!/usr/bin/env python3
"""
Generate a flat base plate derived from the top face of base_clamp.

This part keeps the exact top-plate footprint and base-servo mounting pattern
from the bent clamp, but strips away the side wall and lower return so it
remains a single flat plate in the same local frame.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from pathlib import Path

V1_DIR = Path(__file__).resolve().parents[1]
TOM_DIR = V1_DIR.parent
PARTS_DIR = Path(__file__).resolve().parent
_CACHE_HOME = V1_DIR / ".cache"
_CACHE_HOME.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_HOME))

import build123d

for path in (TOM_DIR, PARTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from base_clamp import (
    BASE_HORN_CONNECTION_HOLE_SPECS_MM,
    BASE_SERVO_M2_HOLE_SPECS_MM,
    CORNER_RADIUS_MM,
    PLATE_THICKNESS_MM,
    _as_single_solid,
    _build_bracket_layout,
    _cut_base_plate_mount_features,
    _fillet_plan_corners,
    _make_box,
)


DISPLAY_NAME = "Base Plate"


@dataclass(frozen=True)
class PlateLayout:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    width: float
    height: float
    face_y: float
    attachment_holes: tuple[tuple[float, float, float], ...]


def _build_plate_layout() -> PlateLayout:
    bracket_layout = _build_bracket_layout()
    x_min = bracket_layout.x_min
    x_max = bracket_layout.side_x_max
    y_min = bracket_layout.y_min
    y_max = bracket_layout.y_max
    z_min = bracket_layout.z_min
    z_max = bracket_layout.z_max

    return PlateLayout(
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        z_min=z_min,
        z_max=z_max,
        width=x_max - x_min,
        height=z_max - z_min,
        face_y=y_max,
        attachment_holes=bracket_layout.attachment_holes,
    )


def build_plate() -> build123d.Solid:
    layout = _build_plate_layout()

    plate = _make_box(
        x_min=layout.x_min,
        x_max=layout.x_max,
        y_min=layout.y_min,
        y_max=layout.y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )
    plate = _fillet_plan_corners(
        plate,
        x_min=layout.x_min,
        x_max=layout.x_max,
        y_min=layout.y_min,
        y_max=layout.y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
        include_min_x=True,
        include_max_x=False,
    )
    plate = _cut_base_plate_mount_features(
        plate,
        holes_xzr=layout.attachment_holes,
        x_min=layout.x_min,
        x_max=layout.x_max,
        y_min=layout.y_min,
        y_max=layout.y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )
    plate = _as_single_solid(plate)

    print(
        f"{DISPLAY_NAME}: "
        f"width={layout.width:.3f} mm, "
        f"height={layout.height:.3f} mm, "
        f"thickness={PLATE_THICKNESS_MM:.3f} mm, "
        f"corner_radius={CORNER_RADIUS_MM:.3f} mm, "
        f"horn_m3_holes={len(BASE_HORN_CONNECTION_HOLE_SPECS_MM)}, "
        f"m2_holes={len(BASE_SERVO_M2_HOLE_SPECS_MM)}, "
        f"flush_y={layout.face_y:.4f} mm"
    )
    return plate


def gen_step() -> dict[str, object]:
    return {
        "shape": build_plate(),
    }


def gen() -> None:
    gen_step()


if __name__ == "__main__":
    gen()
