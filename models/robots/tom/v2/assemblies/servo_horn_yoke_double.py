from __future__ import annotations

import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parents[1]
PARTS_DIR = V2_DIR / "parts"
for path in (V2_DIR, PARTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import link_common as lc


SIDE_BY_SIDE_CLEARANCE_MM = 2.0
SERVO_CENTER_SPACING_Y_MM = (2.0 * abs(lc.SERVO_REAR_EXTREME_LOCAL_Y_MM)) + SIDE_BY_SIDE_CLEARANCE_MM
SERVO_CENTER_OFFSET_Y_MM = 0.5 * SERVO_CENTER_SPACING_Y_MM
YOKE_HORN_FACE_CLEARANCE_MM = 0.0

IDENTITY_TRANSFORM = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _translate_y(y_mm: float) -> list[float]:
    transform = list(IDENTITY_TRANSFORM)
    transform[7] = y_mm
    return transform


def _flip_about_x_then_translate_y(y_mm: float) -> list[float]:
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, -1.0, 0.0, y_mm,
        0.0, 0.0, -1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def _mate(
    source_label: str,
    *,
    fixed: str,
    moving: str,
    clearance_mm: float = YOKE_HORN_FACE_CLEARANCE_MM,
) -> dict[str, object]:
    fixed_part, fixed_frame = fixed.split(":", 1)
    moving_part, moving_frame = moving.split(":", 1)
    return {
        "sourceLabel": source_label,
        "type": "rigid",
        "relation": "rigid",
        "fixed": fixed,
        "moving": moving,
        "parameters": {"clearance_mm": clearance_mm},
        "fixedEndpoint": {
            "part": fixed_part,
            "frame": fixed_frame,
        },
        "movingEndpoint": {
            "part": moving_part,
            "frame": moving_frame,
        },
    }


def _servo_instance(label: str, transform: list[float]) -> dict[str, object]:
    return {
        "path": "../parts/imports/sts3250.step",
        "name": f"sts3250_{label}",
        "transform": transform,
        "use_source_colors": True,
    }


def _servo_yoke_mates() -> list[dict[str, object]]:
    yoke = "servo_horn_yoke_double_horn"
    mount = "servo_end_mount_double"
    return [
        _mate(
            "rear_servo_outer_horn_to_double_horn",
            fixed="sts3250_rear:output_horn_face",
            moving=f"{yoke}:rear_outer_horn_plate",
        ),
        _mate(
            "front_servo_outer_horn_to_double_horn",
            fixed="sts3250_front:output_horn_face",
            moving=f"{yoke}:front_outer_horn_plate",
        ),
        _mate(
            "rear_servo_case_top_to_double_mount",
            fixed="sts3250_rear:case_top_face",
            moving=f"{mount}:rear_case_wrap_flange",
        ),
        _mate(
            "front_servo_case_top_to_double_mount",
            fixed="sts3250_front:case_top_face",
            moving=f"{mount}:front_case_wrap_flange",
        ),
    ]


def gen_step() -> dict[str, object]:
    instances = [
        _servo_instance("rear", _flip_about_x_then_translate_y(-SERVO_CENTER_OFFSET_Y_MM)),
        _servo_instance("front", _translate_y(SERVO_CENTER_OFFSET_Y_MM)),
        {
            "path": "../parts/servo_horn_yoke_double_horn.step",
            "name": "servo_horn_yoke_double_horn",
            "transform": IDENTITY_TRANSFORM,
        },
        {
            "path": "../parts/servo_end_mount_double.step",
            "name": "servo_end_mount_double",
            "transform": IDENTITY_TRANSFORM,
        },
    ]
    return {
        "instances": instances,
        "assembly_mates": _servo_yoke_mates(),
    }
