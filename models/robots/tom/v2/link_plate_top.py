#!/usr/bin/env python3
"""
Generate the tom v2 tube-link top plate.

A flat (zero-bend) 0.125 in 5052 plate against the top servo's case-top
face at X = +15.5:
- two M2 clearance holes into the servo at (Y = +/-10.25, Z = 106.0),
  with the screw-tab outline notched around the case-top center ridge,
- a flared lower lobe carrying the SK10 shaft support on its inner
  face: two M5 clearance holes at the SK bolt pitch around
  Z = SK10_CENTER_Z_MM, which places the tube line at X = -4.25.

Usage:
  python v2/link_plate_top.py
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

CUT_EXTENSION_MM = 2.0


def build_bracket() -> build123d.Shape:
    upper = lc.make_box(
        x_min=lc.TUBE_TOP_PLATE_INNER_X_MM,
        x_max=lc.TUBE_TOP_PLATE_OUTER_X_MM,
        y_min=-lc.BODY_HALF_WIDTH_MM,
        y_max=lc.BODY_HALF_WIDTH_MM,
        z_min=lc.TUBE_TOP_PLATE_BOTTOM_Z_MM,
        z_max=lc.CASE_OFFSET_RELIEF_BOTTOM_Z_MM,
    )
    flare = lc.make_box(
        x_min=lc.TUBE_TOP_PLATE_INNER_X_MM,
        x_max=lc.TUBE_TOP_PLATE_OUTER_X_MM,
        y_min=-lc.TUBE_TOP_PLATE_FLARE_HALF_WIDTH_MM,
        y_max=lc.TUBE_TOP_PLATE_FLARE_HALF_WIDTH_MM,
        z_min=lc.TUBE_TOP_PLATE_BOTTOM_Z_MM,
        z_max=lc.SK10_CENTER_Z_MM + 11.0,
    )
    plate: build123d.Shape = upper.fuse(flare)
    for side in (-1.0, 1.0):
        tab = lc.make_box(
            x_min=lc.TUBE_TOP_PLATE_INNER_X_MM,
            x_max=lc.TUBE_TOP_PLATE_OUTER_X_MM,
            y_min=min(side * lc.CASE_OFFSET_RELIEF_HALF_WIDTH_MM, side * lc.BODY_HALF_WIDTH_MM),
            y_max=max(side * lc.CASE_OFFSET_RELIEF_HALF_WIDTH_MM, side * lc.BODY_HALF_WIDTH_MM),
            z_min=lc.CASE_OFFSET_RELIEF_BOTTOM_Z_MM,
            z_max=lc.CASE_OFFSET_TAB_TOP_Z_MM,
        )
        plate = plate.fuse(tab)

    for hole_y, hole_z in lc.CASE_OFFSET_HOLES_YZ_MM:
        plate = plate.cut(
            lc.make_x_cylinder(
                y=hole_y,
                z=hole_z,
                radius=lc.SERVO_MOUNT_HOLE_RADIUS_MM,
                x_min=lc.TUBE_TOP_PLATE_INNER_X_MM - CUT_EXTENSION_MM,
                x_max=lc.TUBE_TOP_PLATE_OUTER_X_MM + CUT_EXTENSION_MM,
            )
        )
    for side in (-1.0, 1.0):
        plate = plate.cut(
            lc.make_x_cylinder(
                y=side * 0.5 * lc.SK10_BOLT_PITCH_MM,
                z=lc.SK10_CENTER_Z_MM,
                radius=lc.CLAMP_BOLT_CLEARANCE_RADIUS_MM,
                x_min=lc.TUBE_TOP_PLATE_INNER_X_MM - CUT_EXTENSION_MM,
                x_max=lc.TUBE_TOP_PLATE_OUTER_X_MM + CUT_EXTENSION_MM,
            )
        )
    return plate


def build_step() -> build123d.Shape:
    plate = build_bracket()

    solids = plate.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one plate solid, found {len(solids)}")

    top_servo, bottom_servo = lc.placed_link_servos_case()
    top_volume = lc.verify_no_interference(plate, top_servo, label="the top servo")
    bottom_volume = lc.verify_no_interference(plate, bottom_servo, label="the bottom servo")

    plate.label = PART_NAME
    plate.color = lc.BRACKET_COLOR

    bb = plate.bounding_box()
    print(
        "Tube-link top plate envelope "
        f"X={bb.size.X:.3f} mm, Y={bb.size.Y:.3f} mm, Z={bb.size.Z:.3f} mm"
    )
    print(
        "Sheet setup "
        f"material=5052-H32 (ALU-125), thickness={lc.TUBE_PLATE_THICKNESS_MM:.3f} mm, bends=0 (flat)"
    )
    print(
        "Holes: 2x M2 servo at "
        + ", ".join(f"(Y={y:.3f}, Z={z:.3f})" for y, z in lc.CASE_OFFSET_HOLES_YZ_MM)
        + f"; 2x M5 SK10 at (Y=+/-{0.5 * lc.SK10_BOLT_PITCH_MM:.1f}, Z={lc.SK10_CENTER_Z_MM:.1f})"
    )
    print(f"Top servo interference volume (mm^3): {top_volume:.6f}")
    print(f"Bottom servo interference volume (mm^3): {bottom_volume:.6f}")
    return plate


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }


if __name__ == "__main__":
    gen_step()
