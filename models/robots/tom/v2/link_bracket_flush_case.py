#!/usr/bin/env python3
"""
Generate the tom v2 flush case-mount link bracket (-X side).

A 1.6 mm sheet part authored in the shared Z-up v2 link frame (see
link_common.py), shaped to satisfy SendCutSend's published bending
rules:

- one straight vertical wall flush against the top servo's case-bottom
  face at X = -16.5, two M2 clearance holes at (Y = +/-10.25,
  Z = 102.25), flat top edge below the servo's raised bottom panel
  (single L bend, so the U-channel 2:1 rule does not apply),
- a foot on the bottom servo's case-bottom (up-facing) plane, stopping
  clear of the cable bay, with two M2 clearance holes at
  (X = 8.3, Y = +/-10.25) and a center hole clearing the rear
  journal/screw stub,
- downturned stiffening lips on both foot edges (9.5 mm legs folded
  down at the body edge) that turn the cantilevered foot strip into a
  channel. The lips are protruding tabs of the flat blank, so each lip
  bend line terminates at free edges and needs no bend relief; the
  segments stop clear of the foot screws' die zone and 2 mm clear of
  the wall bend (lip channel 25.8/9.5 = 2.7:1, lip flange 9.5 formed /
  7.0 flat vs 7.7/6.5 minimums).

Usage:
  python v2/link_bracket_flush_case.py
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
    wall = lc.make_box(
        x_min=lc.CASE_FLUSH_PLATE_OUTER_X_MM,
        x_max=lc.CASE_FLUSH_PLATE_INNER_X_MM,
        y_min=-lc.BODY_HALF_WIDTH_MM,
        y_max=lc.BODY_HALF_WIDTH_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.CASE_FLUSH_WALL_TOP_Z_MM,
    )
    foot = lc.make_box(
        x_min=lc.CASE_FLUSH_PLATE_OUTER_X_MM,
        x_max=lc.FLUSH_FOOT_REACH_X_MM,
        y_min=-lc.BODY_HALF_WIDTH_MM,
        y_max=lc.BODY_HALF_WIDTH_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.FOOT_TOP_Z_MM,
    )
    bracket: build123d.Shape = wall.fuse(foot)
    for side in (-1.0, 1.0):
        lip = lc.make_box(
            x_min=lc.LIP_X_MIN_MM,
            x_max=lc.LIP_X_MAX_MM,
            y_min=min(side * lc.BODY_HALF_WIDTH_MM, side * lc.LIP_OUTER_HALF_WIDTH_MM),
            y_max=max(side * lc.BODY_HALF_WIDTH_MM, side * lc.LIP_OUTER_HALF_WIDTH_MM),
            z_min=lc.LIP_BOTTOM_Z_MM,
            z_max=lc.FOOT_TOP_Z_MM,
        )
        bracket = bracket.fuse(lip)

    for hole_y, hole_z in lc.CASE_FLUSH_HOLES_YZ_MM:
        bracket = bracket.cut(
            lc.make_x_cylinder(
                y=hole_y,
                z=hole_z,
                radius=lc.SERVO_MOUNT_HOLE_RADIUS_MM,
                x_min=lc.CASE_FLUSH_PLATE_OUTER_X_MM - CUT_EXTENSION_MM,
                x_max=lc.CASE_FLUSH_PLATE_INNER_X_MM + CUT_EXTENSION_MM,
            )
        )
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

    top_servo, bottom_servo = lc.placed_link_servos_case()
    top_volume = lc.verify_no_interference(bracket, top_servo, label="the top servo")
    bottom_volume = lc.verify_no_interference(bracket, bottom_servo, label="the bottom servo")

    bracket.label = PART_NAME
    bracket.color = lc.BRACKET_COLOR

    bb = bracket.bounding_box()
    lip_flange = lc.FOOT_TOP_Z_MM - lc.LIP_BOTTOM_Z_MM
    lip_channel_base = 2.0 * lc.BODY_HALF_WIDTH_MM
    lip_to_hole = lc.HOLE_NEAR_OFFSET_MM - lc.LIP_X_MAX_MM
    print(
        "Flush case-mount bracket envelope "
        f"X={bb.size.X:.3f} mm, Y={bb.size.Y:.3f} mm, Z={bb.size.Z:.3f} mm"
    )
    print(
        "Sheet setup "
        f"material=5052-H32 (ALU-063), thickness={lc.SHEET_THICKNESS_MM:.4f} mm, bends=3 (wall + 2 lip segments)"
    )
    print(
        "SendCutSend checks: wall is an open L (no channel); "
        f"lip channel {lip_channel_base:.1f}/{lip_flange:.1f} = {lip_channel_base / lip_flange:.2f}:1 (>= 2:1), "
        f"lip flange {lip_flange:.3f} mm (>= {lc.SCS_MIN_FLANGE_AFTER_BEND_MM:.3f}), "
        f"lip-end to foot-hole center {lip_to_hole:.2f} mm (>= half die {lc.SCS_HALF_DIE_WIDTH_MM:.3f})"
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
