#!/usr/bin/env python3
"""
True-dimension envelope of an SK10 shaft support, authored in the v2
link frame, base against the top link plate's inner face with its bore
on the tube line.

step.parts hosts only a generic block envelope for this SKU
(sk10_round_shaft_support, downloaded alongside for provenance), so
this envelope is built from the standard SK10 catalog dimensions in
link_common.py: 20 mm center height, 42 mm base across two M5 bolts at
32 mm pitch, 14 mm thick, bore 10. Verify against the sourced vendor's
datasheet before cutting the mating plate.

Usage:
  python v2/imports/sk10_envelope.py
"""

from __future__ import annotations

import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parents[1]
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

import build123d

import link_common as lc


PART_NAME = Path(__file__).stem


def build_step() -> build123d.Shape:
    base_slab = lc.make_box(
        x_min=lc.TUBE_TOP_PLATE_INNER_X_MM - lc.SK10_BASE_SLAB_MM,
        x_max=lc.TUBE_TOP_PLATE_INNER_X_MM,
        y_min=-0.5 * lc.SK10_BASE_WIDTH_MM,
        y_max=0.5 * lc.SK10_BASE_WIDTH_MM,
        z_min=lc.SK10_CENTER_Z_MM - 0.5 * lc.SK10_THICKNESS_MM,
        z_max=lc.SK10_CENTER_Z_MM + 0.5 * lc.SK10_THICKNESS_MM,
    )
    column = lc.make_box(
        x_min=lc.TUBE_TOP_PLATE_INNER_X_MM - lc.SK10_DEPTH_MM,
        x_max=lc.TUBE_TOP_PLATE_INNER_X_MM,
        y_min=-10.0,
        y_max=10.0,
        z_min=lc.SK10_CENTER_Z_MM - 0.5 * lc.SK10_THICKNESS_MM,
        z_max=lc.SK10_CENTER_Z_MM + 0.5 * lc.SK10_THICKNESS_MM,
    )
    shape: build123d.Shape = base_slab.fuse(column)
    shape = shape.cut(
        lc.make_z_cylinder(
            x=lc.TUBE_AXIS_X_MM,
            y=0.0,
            radius=lc.TUBE_OUTER_RADIUS_MM + lc.TUBE_BORE_CLEARANCE_MM,
            z_min=lc.SK10_CENTER_Z_MM - 0.5 * lc.SK10_THICKNESS_MM - 2.0,
            z_max=lc.SK10_CENTER_Z_MM + 0.5 * lc.SK10_THICKNESS_MM + 2.0,
        )
    )
    for side in (-1.0, 1.0):
        shape = shape.cut(
            lc.make_x_cylinder(
                y=side * 0.5 * lc.SK10_BOLT_PITCH_MM,
                z=lc.SK10_CENTER_Z_MM,
                radius=lc.CLAMP_BOLT_CLEARANCE_RADIUS_MM,
                x_min=lc.TUBE_TOP_PLATE_INNER_X_MM - lc.SK10_BASE_SLAB_MM - 2.0,
                x_max=lc.TUBE_TOP_PLATE_INNER_X_MM + 2.0,
            )
        )
    solids = shape.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one SK10 envelope solid, found {len(solids)}")
    shape.label = PART_NAME
    bb = shape.bounding_box()
    print(
        f"SK10 envelope: center height {lc.SK10_CENTER_HEIGHT_MM:.1f} mm puts the tube line at "
        f"X={lc.TUBE_AXIS_X_MM:.2f}"
    )
    print(f"Envelope X={bb.size.X:.2f} Y={bb.size.Y:.2f} Z={bb.size.Z:.2f} mm")
    return shape


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }


if __name__ == "__main__":
    gen_step()
