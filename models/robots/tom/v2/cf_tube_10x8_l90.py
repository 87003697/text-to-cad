#!/usr/bin/env python3
"""
Generate the tom v2 link carbon fiber tube, authored in the v2 link
frame on the tube line.

Off-the-shelf spec: generic 3K roll-wrapped carbon fiber tube,
10 mm OD x 8 mm ID, cut to 90 mm. step.parts has no carbon fiber tube
entries (search miss recorded 2026-06-13), so this is a documented
stock-part model rather than a catalog download.

It runs from just above the bottom plate (clearing the bottom servo's
buried rear journal stub) up through the SK10 clamp, ending below the
hanging top servo body.

Usage:
  python v2/cf_tube_10x8_l90.py
"""

from __future__ import annotations

import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parent
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

import build123d

import link_common as lc


PART_NAME = Path(__file__).stem


def build_step() -> build123d.Shape:
    tube: build123d.Shape = lc.make_z_cylinder(
        x=lc.TUBE_AXIS_X_MM,
        y=0.0,
        radius=lc.TUBE_OUTER_RADIUS_MM,
        z_min=lc.TUBE_BOTTOM_Z_MM,
        z_max=lc.TUBE_TOP_Z_MM,
    ).cut(
        lc.make_z_cylinder(
            x=lc.TUBE_AXIS_X_MM,
            y=0.0,
            radius=lc.TUBE_INNER_RADIUS_MM,
            z_min=lc.TUBE_BOTTOM_Z_MM - 2.0,
            z_max=lc.TUBE_TOP_Z_MM + 2.0,
        )
    )
    solids = tube.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one tube solid, found {len(solids)}")
    tube.label = PART_NAME
    length = lc.TUBE_TOP_Z_MM - lc.TUBE_BOTTOM_Z_MM
    print(
        f"CF tube D{2 * lc.TUBE_OUTER_RADIUS_MM:.0f}x{2 * lc.TUBE_INNER_RADIUS_MM:.0f} "
        f"L={length:.1f} mm on tube line X={lc.TUBE_AXIS_X_MM:.2f}, "
        f"z=[{lc.TUBE_BOTTOM_Z_MM:.1f}, {lc.TUBE_TOP_Z_MM:.1f}]"
    )
    return tube


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }


if __name__ == "__main__":
    gen_step()
