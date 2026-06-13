#!/usr/bin/env python3
"""
Verification assembly for the tom v2 link, case-mount variation.

Two separate formed brackets (split to satisfy SendCutSend's U/C-channel
2:1 forming rule) bolt to the top servo's case with two M2 screws per
side (flush wall into the case-bottom face holes, offset tabs into the
case-top face holes), so the servo body is rigid to this link and hangs
down into it; the next link's yoke grabs the free output and rear horns
at the pitch axis (Y = 0, Z = 135). The bottom servo points its shaft
down with its case-bottom face up at Z = 0 and uses the rear-horn-less
variant; each bracket foot seats flat on it, stopping clear of the
cable bay and raised center panel.

Usage:
  python v2/link_assembly_case.py
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
                "path": "link_bracket_flush_case.step",
                "name": "link_bracket_flush_case",
                "transform": list(IDENTITY_TRANSFORM),
            },
            {
                "path": "link_bracket_offset_case.step",
                "name": "link_bracket_offset_case",
                "transform": list(IDENTITY_TRANSFORM),
            },
        ],
    }
