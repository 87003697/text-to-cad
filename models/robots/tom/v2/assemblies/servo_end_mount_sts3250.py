#!/usr/bin/env python3
"""
Verification assembly for the tom v2 servo end mount on an STS3250.

The mount is authored directly in the imported STS3250 local frame, so both
parts use identity transforms here. This keeps the assembly useful as a direct
fit check for the flange wrap clearances and screw-hole placement.
"""

from __future__ import annotations


IDENTITY_TRANSFORM = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def gen_step() -> dict[str, object]:
    return {
        "instances": [
            {
                "path": "../parts/imports/sts3250.step",
                "name": "sts3250",
                "transform": list(IDENTITY_TRANSFORM),
                "use_source_colors": True,
            },
            {
                "path": "../parts/servo_end_mount.step",
                "name": "servo_end_mount",
                "transform": list(IDENTITY_TRANSFORM),
                "use_source_colors": True,
            },
        ],
    }
