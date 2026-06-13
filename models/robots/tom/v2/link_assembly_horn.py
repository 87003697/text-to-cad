#!/usr/bin/env python3
"""
Verification assembly for the tom v2 link, horn-mount variation.

The bracket horn plates bolt directly to the top servo's output and rear
horns (four M3-pattern screws per side), so the joint is between the
brackets and the servo body: the body, which belongs to the next link,
is flipped to stand above the pitch axis (Y = 0, Z = 135) and rotates
freely. The bottom servo points its shaft down with its case-bottom
face up at Z = 0 and uses the rear-horn-less variant so the bracket feet
seat flat on it.

Usage:
  python v2/link_assembly_horn.py
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
                "transform": list(lc.TOP_SERVO_HORN_TRANSFORM),
            },
            {
                "path": "imports/sts3250_no_rear_horn.step",
                "name": "sts3250_bottom",
                "transform": list(lc.BOTTOM_SERVO_TRANSFORM),
            },
            {
                "path": "link_bracket_flush_horn.step",
                "name": "link_bracket_flush_horn",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "link_bracket_offset_horn.step",
                "name": "link_bracket_offset_horn",
                "transform": list(IDENTITY_TRANSFORM),
            },
        ],
    }
