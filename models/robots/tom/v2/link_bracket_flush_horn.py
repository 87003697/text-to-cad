#!/usr/bin/env python3
"""
Generate the tom v2 flush link bracket, horn-mount variation (-X side).

A 1.6 mm sheet part authored in the shared Z-up v2 link frame (see
link_common.py), with no intermediate bend: one straight vertical plate
in the rear-horn mounting plane runs the full link height, flush with
the top servo's rear horn face at X = -18.3, and the foot bends inward
at the bottom, overhanging the bottom servo's rear end face.

- plate: bolts to the rear horn with the four 7 mm-circle screw holes
  around the pitch axis (Y = 0, Z = 135) plus a clearance hole for the
  case's rear journal/screw stub, which protrudes through the plate
  plane,
- foot: seats on the bottom servo's case-bottom (up-facing) plane with
  two M2 clearance holes at (X = 8.3, Y = +/-10.25) and a center hole
  clearing that servo's rear journal/screw stub.

Usage:
  python v2/link_bracket_flush_horn.py
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
    plate = lc.horn_plate(side=-1.0, z_min=lc.FOOT_BOTTOM_Z_MM)
    foot = lc.make_box(
        x_min=-lc.HORN_PLATE_OUTER_X_MM,
        x_max=lc.FLUSH_FOOT_REACH_X_MM,
        y_min=-lc.PLATE_HALF_SPAN_MM,
        y_max=lc.PLATE_HALF_SPAN_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.FOOT_TOP_Z_MM,
    )
    bracket: build123d.Shape = plate.fuse(foot)

    bracket = bracket.cut(
        lc.make_z_cylinder(
            x=0.0,
            y=0.0,
            radius=lc.REAR_JOURNAL_CLEARANCE_RADIUS_MM,
            z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
            z_max=lc.FOOT_TOP_Z_MM + CUT_EXTENSION_MM,
        )
    )
    for side in (-1.0, 1.0):
        bracket = bracket.cut(
            lc.make_z_cylinder(
                x=lc.HOLE_NEAR_OFFSET_MM,
                y=side * lc.HOLE_SIDE_OFFSET_MM,
                radius=lc.SERVO_MOUNT_HOLE_RADIUS_MM,
                z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
                z_max=lc.FOOT_TOP_Z_MM + CUT_EXTENSION_MM,
            )
        )
    return bracket


def build_step() -> build123d.Shape:
    bracket = build_bracket()

    solids = bracket.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one connected bracket solid, found {len(solids)}")

    top_servo, bottom_servo = lc.placed_link_servos_horn()
    top_volume = lc.verify_no_interference(bracket, top_servo, label="the top servo")
    bottom_volume = lc.verify_no_interference(bracket, bottom_servo, label="the bottom servo")

    bracket.label = PART_NAME
    bracket.color = lc.BRACKET_COLOR

    bb = bracket.bounding_box()
    print(
        "Flush horn-mount bracket envelope "
        f"X={bb.size.X:.3f} mm, Y={bb.size.Y:.3f} mm, Z={bb.size.Z:.3f} mm"
    )
    print(
        "Sheet setup "
        f"material=5052-H32, thickness={lc.SHEET_THICKNESS_MM:.4f} mm, "
        f"depth={2 * lc.PLATE_HALF_SPAN_MM:.3f} mm (STS3250 width), bends=1"
    )
    print(
        "Rear horn plate "
        f"x=[{-lc.HORN_PLATE_OUTER_X_MM:.3f}, {-lc.HORN_PLATE_INNER_X_MM:.3f}] mm, "
        f"pitch axis at (Y=0, Z={lc.TOP_PIVOT_Z_MM:.3f}), "
        f"screw circle r={lc.SERVO_HORN_SCREW_CIRCLE_RADIUS_MM:.3f} mm (4x M3-pattern), "
        f"horn face gap={lc.HORN_FACE_GAP_MM:.3f} mm"
    )
    print(f"Top servo interference volume (mm^3): {top_volume:.6f}")
    print(f"Bottom servo interference volume (mm^3): {bottom_volume:.6f}")
    return bracket


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }


if __name__ == "__main__":
    gen_step()
