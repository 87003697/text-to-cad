#!/usr/bin/env python3
"""
Generate the tom v2 tube-link bottom plate.

A flat (zero-bend) 0.125 in 5052 plate on the bottom servo's case-bottom
(up-facing) plane:
- four M2 clearance holes into the servo at (X = 8.3 | 32.75,
  Y = +/-10.25),
- the plate thickness buries the servo's rear journal/screw stub inside
  its through hole,
- windows expose the cable bay and clear the raised center zone,
- a flared lobe carries the SHF10 flange support: two M5 clearance
  holes at the SHF bolt pitch, centered on the tube line at X = -4.25.

Usage:
  python v2/link_plate_bottom.py
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
    main = lc.make_box(
        x_min=lc.BOTTOM_PLATE_X_MIN_MM,
        x_max=lc.BOTTOM_PLATE_X_MAX_MM,
        y_min=-lc.BODY_HALF_WIDTH_MM,
        y_max=lc.BODY_HALF_WIDTH_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.BOTTOM_PLATE_TOP_Z_MM,
    )
    flare = lc.make_box(
        x_min=lc.BOTTOM_PLATE_X_MIN_MM,
        x_max=lc.BOTTOM_PLATE_FLARE_X_MAX_MM,
        y_min=-lc.BOTTOM_PLATE_FLARE_HALF_WIDTH_MM,
        y_max=lc.BOTTOM_PLATE_FLARE_HALF_WIDTH_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.BOTTOM_PLATE_TOP_Z_MM,
    )
    plate: build123d.Shape = main.fuse(flare)

    # Cable bay window and raised-zone window (same extents as the
    # bracket designs; the raised zone runs from the bay through the
    # raised panel).
    plate = plate.cut(
        lc.make_box(
            x_min=lc.CABLE_BAY_X_MIN_MM,
            x_max=lc.CABLE_BAY_X_MAX_MM,
            y_min=-lc.CABLE_BAY_HALF_WIDTH_MM,
            y_max=lc.CABLE_BAY_HALF_WIDTH_MM,
            z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
            z_max=lc.BOTTOM_PLATE_TOP_Z_MM + CUT_EXTENSION_MM,
        )
    )
    plate = plate.cut(
        lc.make_box(
            x_min=lc.CABLE_BAY_X_MAX_MM + 0.5,
            x_max=lc.BOTTOM_PANEL_X_MAX_MM,
            y_min=-lc.OFFSET_WINDOW_HALF_WIDTH_MM,
            y_max=lc.OFFSET_WINDOW_HALF_WIDTH_MM,
            z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
            z_max=lc.BOTTOM_PLATE_TOP_Z_MM + CUT_EXTENSION_MM,
        )
    )

    # Rear journal/screw stub pocket (the stub is shorter than the plate
    # is thick, so it stays buried).
    plate = plate.cut(
        lc.make_z_cylinder(
            x=0.0,
            y=0.0,
            radius=lc.REAR_JOURNAL_CLEARANCE_RADIUS_MM,
            z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
            z_max=lc.BOTTOM_PLATE_TOP_Z_MM + CUT_EXTENSION_MM,
        )
    )
    # Servo M2 holes.
    for hole_x in (lc.HOLE_NEAR_OFFSET_MM, lc.HOLE_FAR_OFFSET_MM):
        for side in (-1.0, 1.0):
            plate = plate.cut(
                lc.make_z_cylinder(
                    x=hole_x,
                    y=side * lc.HOLE_SIDE_OFFSET_MM,
                    radius=lc.SERVO_MOUNT_HOLE_RADIUS_MM,
                    z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
                    z_max=lc.BOTTOM_PLATE_TOP_Z_MM + CUT_EXTENSION_MM,
                )
            )
    # SHF10 flange bolts.
    for side in (-1.0, 1.0):
        plate = plate.cut(
            lc.make_z_cylinder(
                x=lc.TUBE_AXIS_X_MM,
                y=side * 0.5 * lc.SHF10_BOLT_PITCH_MM,
                radius=lc.CLAMP_BOLT_CLEARANCE_RADIUS_MM,
                z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
                z_max=lc.BOTTOM_PLATE_TOP_Z_MM + CUT_EXTENSION_MM,
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
        "Tube-link bottom plate envelope "
        f"X={bb.size.X:.3f} mm, Y={bb.size.Y:.3f} mm, Z={bb.size.Z:.3f} mm"
    )
    print(
        "Sheet setup "
        f"material=5052-H32 (ALU-125), thickness={lc.TUBE_PLATE_THICKNESS_MM:.3f} mm, bends=0 (flat)"
    )
    print(
        f"Journal stub buried: stub 2.6 mm < plate top {lc.BOTTOM_PLATE_TOP_Z_MM - lc.BOTTOM_FACE_Z_MM:.3f} mm"
    )
    print(
        "Holes: 4x M2 servo, 2x M5 SHF10 at "
        f"(X={lc.TUBE_AXIS_X_MM:.2f}, Y=+/-{0.5 * lc.SHF10_BOLT_PITCH_MM:.1f})"
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
