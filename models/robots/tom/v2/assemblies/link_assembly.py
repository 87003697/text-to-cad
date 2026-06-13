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
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

import link_common as lc


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


def gen_step() -> dict[str, object]:
    return {
        "children": [
            {
                "path": "../imports/sts3250.step",
                "name": "sts3250_top",
                "transform": list(lc.TOP_SERVO_CASE_TRANSFORM),
            },
            {
                "path": "../imports/sts3250_no_rear_horn.step",
                "name": "sts3250_bottom",
                "transform": list(lc.BOTTOM_SERVO_TRANSFORM),
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
        ],
    }
