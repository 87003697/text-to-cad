#!/usr/bin/env python3
"""
Verification assembly for the tom v2 carbon-tube link (RoArm-style).

Both servos sit exactly as in the case-mount variation: bottom servo
shaft down with its case-bottom face up at Z = 0 (rear-horn-less
variant), top servo forming the horizontal pitch joint with its horn
axis along X at (Y = 0, Z = 135). The structure between them is a
generic 10 x 8 mm carbon fiber tube on the tube line at X = -4.25,
held by an SHF10 flange shaft support on a flat bottom plate and an
SK10 shaft support on a flat top plate. Both plates are zero-bend
0.125 in 5052. Clamp envelopes use standard SK10/SHF10 dimensions;
verify against the sourced vendor's datasheet before cutting.

Usage:
  python v2/link_assembly_tube.py
"""

from __future__ import annotations

import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parent
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

import link_common as lc


IDENTITY_TRANSFORM = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def gen_step() -> dict[str, object]:
    return {
        "children": [
            {
                "path": "imports/sts3250.step",
                "name": "sts3250_top",
                "transform": list(lc.TOP_SERVO_CASE_TRANSFORM),
            },
            {
                "path": "imports/sts3250_no_rear_horn.step",
                "name": "sts3250_bottom",
                "transform": list(lc.BOTTOM_SERVO_TRANSFORM),
            },
            {
                "path": "link_plate_bottom.step",
                "name": "link_plate_bottom",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "link_plate_top.step",
                "name": "link_plate_top",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "imports/shf10_envelope.step",
                "name": "shf10_flange_support",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "imports/sk10_envelope.step",
                "name": "sk10_shaft_support",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "cf_tube_10x8_l90.step",
                "name": "cf_tube",
                "transform": list(IDENTITY_TRANSFORM),
            },
        ],
    }
