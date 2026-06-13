#!/usr/bin/env python3
"""
Verification assembly for the tom v2 link bracket pair.

Both servos sit exactly as in the case-mount variation. One two-bend wrap
plate connects them along the +Y side; a mirrored instance of the same
plate connects the -Y side.

Usage:
  python v2/assemblies/link_assembly.py
"""

from __future__ import annotations

import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parents[1]
ASSEMBLIES_DIR = Path(__file__).resolve().parent
for path in (V2_DIR, ASSEMBLIES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import link_common as lc
import pitch_link_sts3215


YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM = -9.1
IDENTITY_TRANSFORM = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

MIRROR_Y_TRANSFORM = (
    1.0, 0.0, 0.0, 0.0,
    0.0, -1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


YOKE_180_ABOUT_WEB_AXIS_TRANSFORM = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -1.0,
    0.0,
    2.0 * YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM,
    0.0,
    0.0,
    -1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


def _matrix(transform: list[float] | tuple[float, ...]) -> list[list[float]]:
    return [list(transform[index : index + 4]) for index in range(0, 16, 4)]


def _flatten(matrix: list[list[float]]) -> list[float]:
    return [value for row in matrix for value in row]


def _matmul(
    left: list[float] | tuple[float, ...],
    right: list[float] | tuple[float, ...],
) -> list[float]:
    left_matrix = _matrix(left)
    right_matrix = _matrix(right)
    return _flatten(
        [
            [
                sum(left_matrix[row][inner] * right_matrix[inner][col] for inner in range(4))
                for col in range(4)
            ]
            for row in range(4)
        ]
    )


YOKE_ON_BOTTOM_SERVO_TRANSFORM = _matmul(
    lc.BOTTOM_SERVO_TRANSFORM,
    YOKE_180_ABOUT_WEB_AXIS_TRANSFORM,
)

THIRD_SERVO_ON_BOTTOM_YOKE_TRANSFORM = _matmul(
    YOKE_ON_BOTTOM_SERVO_TRANSFORM,
    pitch_link_sts3215.STS3215_TRANSFORM,
)


def _mate(
    source_label: str,
    *,
    fixed: str,
    moving: str,
    relation: str = "rigid",
) -> dict[str, object]:
    fixed_part, fixed_frame = fixed.split(":", 1)
    moving_part, moving_frame = moving.split(":", 1)
    return {
        "sourceLabel": source_label,
        "type": relation,
        "relation": relation,
        "fixed": fixed,
        "moving": moving,
        "parameters": {},
        "fixedEndpoint": {
            "part": fixed_part,
            "frame": fixed_frame,
        },
        "movingEndpoint": {
            "part": moving_part,
            "frame": moving_frame,
        },
    }


def gen_step() -> dict[str, object]:
    return {
        "children": [
            {
                "path": "../imports/sts3250.step",
                "name": "sts3250_top",
                "transform": list(lc.TOP_SERVO_CASE_TRANSFORM),
            },
            {
                "path": "../imports/sts3250.step",
                "name": "sts3250_bottom",
                "transform": list(lc.BOTTOM_SERVO_TRANSFORM),
                "use_source_colors": True,
            },
            {
                "path": "../link_bracket_right.step",
                "name": "link_bracket_right",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "../link_bracket_left.step",
                "name": "link_bracket_left",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "../servo_horn_yoke.step",
                "name": "servo_horn_yoke",
                "transform": YOKE_ON_BOTTOM_SERVO_TRANSFORM,
            },
            {
                "path": "../imports/sts3215.step",
                "name": "sts3215_yoke_servo",
                "transform": THIRD_SERVO_ON_BOTTOM_YOKE_TRANSFORM,
                "use_source_colors": True,
            },
        ],
        "assembly_mates": [
            _mate(
                "bottom_servo_to_right_bracket",
                fixed="sts3250_bottom:upstream_case",
                moving="link_bracket_right:bottom_servo_mount",
            ),
            _mate(
                "bottom_servo_to_left_bracket",
                fixed="sts3250_bottom:upstream_case",
                moving="link_bracket_left:bottom_servo_mount",
            ),
            _mate(
                "right_bracket_to_top_servo",
                fixed="link_bracket_right:top_servo_mount",
                moving="sts3250_top:case_mount",
            ),
            _mate(
                "left_bracket_to_top_servo",
                fixed="link_bracket_left:top_servo_mount",
                moving="sts3250_top:case_mount",
            ),
            _mate(
                "bottom_servo_horns_to_yoke",
                fixed="sts3250_bottom:horn_axis",
                moving="servo_horn_yoke:horn_axis",
            ),
            _mate(
                "bottom_yoke_to_third_servo",
                fixed="servo_horn_yoke:horn_axis",
                moving="sts3215_yoke_servo:horn_axis",
            ),
        ],
    }
