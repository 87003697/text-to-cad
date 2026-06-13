#!/usr/bin/env python3
"""
Generate the tom v2 offset case-mount link bracket (+X side), single-bend
version.

A 1.6 mm sheet part authored in the shared Z-up v2 link frame (see
link_common.py). The former three-bend jog (foot, shelf, tab bend) is
replaced by one continuous plate in the top servo's case-top plane and a
single bend at the bottom:

- plate: flush against the top servo's case-top face at X = +15.5, full
  body width below the case-top center ridge, splitting into the two
  screw tabs around the ridge above; each tab carries one M2 clearance
  hole at (Y = +/-10.25, Z = 106.0). Tabs are coplanar with the plate,
  so no tab bend exists.
- one bend at the bottom turns the plate outboard across the bottom
  servo's case-bottom (up-facing) plane.
- foot: two side rails seated on the servo's Z = 0 rim strips (along
  the rim edges at Y = +/-12.06) around a window that clears the raised
  center zone (proud up to Z = 1.9) and rejoins to a full-width pad
  with two M2 clearance holes at (X = 32.75, Y = +/-10.25). The window
  extends up the plate's bottom edge as the bend relief between the two
  rail bend segments, and doubles as the cable exit slot over the
  bottom servo's cable bay.

Usage:
  python v2/link_bracket_offset_case.py
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
    plate = lc.make_box(
        x_min=lc.CASE_OFFSET_PLATE_INNER_X_MM,
        x_max=lc.CASE_OFFSET_PLATE_OUTER_X_MM,
        y_min=-lc.BODY_HALF_WIDTH_MM,
        y_max=lc.BODY_HALF_WIDTH_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.CASE_OFFSET_RELIEF_BOTTOM_Z_MM,
    )
    bracket: build123d.Shape = plate
    for side in (-1.0, 1.0):
        tab = lc.make_box(
            x_min=lc.CASE_OFFSET_PLATE_INNER_X_MM,
            x_max=lc.CASE_OFFSET_PLATE_OUTER_X_MM,
            y_min=min(side * lc.CASE_OFFSET_RELIEF_HALF_WIDTH_MM, side * lc.BODY_HALF_WIDTH_MM),
            y_max=max(side * lc.CASE_OFFSET_RELIEF_HALF_WIDTH_MM, side * lc.BODY_HALF_WIDTH_MM),
            z_min=lc.CASE_OFFSET_RELIEF_BOTTOM_Z_MM,
            z_max=lc.CASE_OFFSET_TAB_TOP_Z_MM,
        )
        bracket = bracket.fuse(tab)
    foot = lc.make_box(
        x_min=lc.CASE_OFFSET_PLATE_OUTER_X_MM,
        x_max=lc.OFFSET_FOOT_END_X_MM,
        y_min=-lc.OFFSET_FOOT_FLARE_HALF_WIDTH_MM,
        y_max=lc.OFFSET_FOOT_FLARE_HALF_WIDTH_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.FOOT_TOP_Z_MM,
    )
    # Flare wings widen the bend zone so each bend rail is 3t wide; they
    # end at free edges above the bend.
    flare = lc.make_box(
        x_min=lc.CASE_OFFSET_PLATE_INNER_X_MM,
        x_max=lc.CASE_OFFSET_PLATE_OUTER_X_MM,
        y_min=-lc.OFFSET_FOOT_FLARE_HALF_WIDTH_MM,
        y_max=lc.OFFSET_FOOT_FLARE_HALF_WIDTH_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.OFFSET_FLARE_TOP_Z_MM,
    )
    bracket = bracket.fuse(foot).fuse(flare)

    # Window around the raised center zone, extended through the plate's
    # bottom edge as the split-bend relief between the two foot rails.
    bracket = bracket.cut(
        lc.make_box(
            x_min=lc.CASE_OFFSET_PLATE_INNER_X_MM - CUT_EXTENSION_MM,
            x_max=lc.BOTTOM_PANEL_X_MAX_MM,
            y_min=-lc.OFFSET_WINDOW_HALF_WIDTH_MM,
            y_max=lc.OFFSET_WINDOW_HALF_WIDTH_MM,
            z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
            z_max=lc.OFFSET_PLATE_RELIEF_TOP_Z_MM,
        )
    )

    for hole_y, hole_z in lc.CASE_OFFSET_HOLES_YZ_MM:
        bracket = bracket.cut(
            lc.make_x_cylinder(
                y=hole_y,
                z=hole_z,
                radius=lc.SERVO_MOUNT_HOLE_RADIUS_MM,
                x_min=lc.CASE_OFFSET_PLATE_INNER_X_MM - CUT_EXTENSION_MM,
                x_max=lc.CASE_OFFSET_PLATE_OUTER_X_MM + CUT_EXTENSION_MM,
            )
        )
    for side in (-1.0, 1.0):
        bracket = bracket.cut(
            lc.make_z_cylinder(
                x=lc.HOLE_FAR_OFFSET_MM,
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
    foot_flange = lc.OFFSET_FOOT_END_X_MM - lc.CASE_OFFSET_PLATE_OUTER_X_MM
    rail_width = lc.OFFSET_FOOT_FLARE_HALF_WIDTH_MM - lc.OFFSET_WINDOW_HALF_WIDTH_MM
    foot_hole_to_bend = lc.HOLE_FAR_OFFSET_MM - lc.CASE_OFFSET_PLATE_OUTER_X_MM
    relief_above_foot = lc.OFFSET_PLATE_RELIEF_TOP_Z_MM - lc.FOOT_TOP_Z_MM
    print(
        "Offset case-mount bracket (single-bend) envelope "
        f"X={bb.size.X:.3f} mm, Y={bb.size.Y:.3f} mm, Z={bb.size.Z:.3f} mm"
    )
    print(
        "Sheet setup "
        f"material=5052-H32 (ALU-063), thickness={lc.SHEET_THICKNESS_MM:.4f} mm, "
        "bends=1 (split into 2 rail segments by the window)"
    )
    print(
        "SendCutSend checks: no channels (single bend); "
        f"foot flange {foot_flange:.2f} mm (>= {lc.SCS_MIN_FLANGE_AFTER_BEND_MM:.3f}); "
        f"foot hole center to bend {foot_hole_to_bend:.2f} mm (>= half die {lc.SCS_HALF_DIE_WIDTH_MM:.3f}); "
        f"rail bend segments 2 x {rail_width:.2f} mm wide, "
        f"window relief {relief_above_foot:.2f} mm past the bend (>= {lc.SCS_BEND_RELIEF_DEPTH_MM:.3f})"
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
