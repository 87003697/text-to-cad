#!/usr/bin/env python3
"""
True-dimension envelope of an SHF10 flange shaft support, authored in
the v2 link frame, seated on the bottom link plate with its bore on the
tube line.

step.parts hosts only a generic block envelope for this SKU
(shf10_round_shaft_support, downloaded alongside for provenance), so
this envelope is built from the standard SHF10 catalog dimensions in
link_common.py: 46.5 mm flange across two M5 bolts at 35 mm pitch,
7 mm flange, 13 mm hub at diameter 20, bore 10. Verify against the
sourced vendor's datasheet before cutting the mating plate.

Usage:
  python v2/imports/shf10_envelope.py
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
    flange = lc.make_box(
        x_min=lc.TUBE_AXIS_X_MM - 10.0,
        x_max=lc.TUBE_AXIS_X_MM + 10.0,
        y_min=-0.5 * lc.SHF10_FLANGE_WIDTH_MM,
        y_max=0.5 * lc.SHF10_FLANGE_WIDTH_MM,
        z_min=lc.BOTTOM_PLATE_TOP_Z_MM,
        z_max=lc.BOTTOM_PLATE_TOP_Z_MM + lc.SHF10_FLANGE_THICKNESS_MM,
    )
    hub = lc.make_z_cylinder(
        x=lc.TUBE_AXIS_X_MM,
        y=0.0,
        radius=lc.SHF10_HUB_RADIUS_MM,
        z_min=lc.BOTTOM_PLATE_TOP_Z_MM,
        z_max=lc.BOTTOM_PLATE_TOP_Z_MM + lc.SHF10_HUB_HEIGHT_MM,
    )
    shape: build123d.Shape = flange.fuse(hub)
    shape = shape.cut(
        lc.make_z_cylinder(
            x=lc.TUBE_AXIS_X_MM,
            y=0.0,
            radius=lc.TUBE_OUTER_RADIUS_MM + lc.TUBE_BORE_CLEARANCE_MM,
            z_min=lc.BOTTOM_PLATE_TOP_Z_MM - 2.0,
            z_max=lc.BOTTOM_PLATE_TOP_Z_MM + lc.SHF10_HUB_HEIGHT_MM + 2.0,
        )
    )
    for side in (-1.0, 1.0):
        shape = shape.cut(
            lc.make_z_cylinder(
                x=lc.TUBE_AXIS_X_MM,
                y=side * 0.5 * lc.SHF10_BOLT_PITCH_MM,
                radius=lc.CLAMP_BOLT_CLEARANCE_RADIUS_MM,
                z_min=lc.BOTTOM_PLATE_TOP_Z_MM - 2.0,
                z_max=lc.BOTTOM_PLATE_TOP_Z_MM + lc.SHF10_FLANGE_THICKNESS_MM + 2.0,
            )
        )
    solids = shape.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one SHF10 envelope solid, found {len(solids)}")
    shape.label = PART_NAME
    bb = shape.bounding_box()
    print(f"SHF10 envelope at tube line X={lc.TUBE_AXIS_X_MM:.2f}, bore D{2 * lc.TUBE_OUTER_RADIUS_MM:.0f}")
    print(f"Envelope X={bb.size.X:.2f} Y={bb.size.Y:.2f} Z={bb.size.Z:.2f} mm")
    return shape


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }


if __name__ == "__main__":
    gen_step()
