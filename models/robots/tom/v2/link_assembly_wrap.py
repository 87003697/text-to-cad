#!/usr/bin/env python3
"""
Verification assembly for the tom v2 wrap-plate link.

Both servos sit exactly as in the case-mount variation. A single
two-bend wrap plate connects them along the +Y side: foot on the bottom
servo's +Y mount holes, riser leg flat against the top servo's +Y side
face, and a wrap flange seated flush on the case-top face (no spacers),
bolted to its +Y screw pair.

Usage:
  python v2/link_assembly_wrap.py
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
                "path": "link_bracket_wrap.step",
                "name": "link_bracket_wrap",
                "transform": list(IDENTITY_TRANSFORM),
            },
        ],
    }
