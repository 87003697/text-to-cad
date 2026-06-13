#!/usr/bin/env python3
"""
Generate the tom v2 wrap link plate: one sheet part with two bend lines
connecting the two servos along the +Y side of the link.

Geometry per the side-view draft and the referenced features:
- foot: lies on the bottom servo's case-bottom (up-facing) plane,
  connecting the two +Y mount holes at (X = 8.3 | 32.75, Y = 10.25),
  its inner edge wrapping around the cable bay with a 0.4 mm overhang
  and clearing the raised center zone,
- bend 1 (axis X): split into two segments over the hole zones, each
  with a 7.7 mm formed flange; a relief slit severs the corner across
  the bay/raised-zone span,
- riser leg: lies flat against the top servo's +Y side face (the
  referenced face spans X 7.9..15.2, Z 102.1..129.3), full foot width
  at the bottom and cut partway up to the face's width ("finishing
  width"), with a corner extension past the servo's case-top corner,
- bend 2 (axis Z, full length): wraps onto the case-top face plane and
  connects to its two +Y screw holes at (Y = 10.25, Z = 106 | 126.7).
  The flange stands 1.5 mm off the face on spacers (5x M2 washers or a
  1.5 mm spacer per screw) so it clears the case-top center ridge
  (1.1 mm proud) and the output horn boss (1.3 mm proud), allowing a
  full-depth rectangular flange that meets the published minimum
  flange length.

Advisory (not in SendCutSend's published ruleset): the servo's holes
sit 2.1 mm from its case corners, so both bends run 2.7-3.2 mm from
screw holes - inside the press-brake die span. Expect minor hole
distortion; ream after forming if screws bind.

Usage:
  python v2/link_bracket_wrap.py
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

# Plate planes. The leg stands 0.8 mm off the servo's +Y side face so the
# wrap bend radius clears the servo corner. The flange seats close to the
# case-top face (0.25 mm) so it bolts down flush rather than floating; its
# inner edge stays outboard of the case-top center ridge (proud only over
# Y 5-7). The bend position follows FLANGE_INNER_X parametrically.
LEG_FACE_CLEARANCE_MM = 3.5
FLANGE_FACE_CLEARANCE_MM = 0.25
LEG_INNER_Y_MM = 12.36 + LEG_FACE_CLEARANCE_MM  # against the top servo's +Y side face
LEG_OUTER_Y_MM = LEG_INNER_Y_MM + lc.SHEET_THICKNESS_MM
FLANGE_INNER_X_MM = lc.CASE_TOP_FACE_X_MM + FLANGE_FACE_CLEARANCE_MM
FLANGE_OUTER_X_MM = FLANGE_INNER_X_MM + lc.SHEET_THICKNESS_MM
# Bend-relief notch at the bottom of the wrap bend: a slot that severs the
# flange's lower free edge from the leg neck so f16/f17 have clearance for
# the fold (the bend line terminates at a free edge, not into solid metal).
WRAP_RELIEF_GAP_MM = 1.6  # >= sheet thickness for safe forming
WRAP_RELIEF_DEPTH_MM = 3.1  # >= SCS bend_relief_depth 2.997 mm
# Output-horn relief: concentric with the servo's output horn edge
# (radius 11.5 on the pitch axis) plus a rotating-clearance margin, so the
# flange's curved cut is inline with the horn and symmetric about the axis.
# Relief radius: horn edge (11.5) and top flange screw hole edge sit only
# ~0.59 mm apart radially. 11.6 maximises the screw web (0.49 mm, near
# the SCS 0.51 mm minimum) at the cost of tight 0.10 mm horn clearance -
# the screw web is structural sheet metal, the horn side is rotating
# clearance against a smooth aluminum edge.
OUTPUT_HORN_RADIUS_MM = 11.5
OUTPUT_HORN_RELIEF_RADIUS_MM = 11.5  # = horn radius exactly, prioritising screw web

# Foot outline. The foot keeps a short Y=7.5 tab around each servo screw
# (left X 6.0-10.6, right X 30.4-35.0 - matching widths) and is set back to
# Y=9.6 everywhere between them, clearing both the cable hole and the
# raised center panel. The left tab is sized to match the right tab.
FOOT_X_MIN_MM = 6.0
FOOT_X_MAX_MM = 35.0
FOOT_EDGE_DEFAULT_Y_MM = 7.5
FOOT_EDGE_RAISED_Y_MM = 9.6
LEFT_TAB_RIGHT_X_MM = 10.6  # 4.6 mm tab (X 6.0-10.6) around the left screw at 8.3
RAISED_ZONE_X_MM = (LEFT_TAB_RIGHT_X_MM, 30.4)

# Leg profile (in the X-Z plane). The right edge is a constant-stress
# cantilever taper: a short vertical zone above the foot bend (clean
# forming), then a single straight diagonal from the full foot width
# down to the neck width. Width tracks the local bending moment so no
# material sits where it does not earn its keep. The left edge runs
# straight up at X = FOOT_X_MIN (the high-stress edge of the bending
# beam, kept solid).
# 15 mm vertical flat zones bracket the taper, sitting above the foot bend
# (Z=1.85) and below the wrap bend (Z=102). Both are above SCS's 6.477 mm
# min_flange_length_before_bend with comfortable margin.
TAPER_BOTTOM_Z_MM = lc.FOOT_TOP_Z_MM + 15.0  # 16.85
LEG_NECK_Z_MM = 87.0  # FLANGE_BOTTOM_Z_MM (102) - 15.0
LEG_NECK_RIGHT_X_MM = FLANGE_INNER_X_MM - WRAP_RELIEF_GAP_MM
LEG_RUN_X_MM = (FOOT_X_MIN_MM, LEG_NECK_RIGHT_X_MM)
LEG_TOP_Z_MM = 128.4  # shortened from 129.3 to keep horn relief from intruding past the SCS min flange
FLANGE_BOTTOM_Z_MM = 102.0
# Uniform flange inner edge: set so both screw tabs (near-horn and end)
# are the same width. It threads the 1.19 mm gap between the rotating
# horn edge (reaches Y=7.96 at the top screw) and the top screw hole
# (inner edge Y=9.15) - clearing the horn while keeping a valid screw web.
FLANGE_EDGE_Y_MM = 8.55


def _leg_profile() -> build123d.Face:
    points = [
        (FOOT_X_MIN_MM, lc.FOOT_BOTTOM_Z_MM),
        (FOOT_X_MAX_MM, lc.FOOT_BOTTOM_Z_MM),
        (FOOT_X_MAX_MM, TAPER_BOTTOM_Z_MM),
        (LEG_RUN_X_MM[1], LEG_NECK_Z_MM),
        (LEG_RUN_X_MM[1], FLANGE_BOTTOM_Z_MM),
        (FLANGE_OUTER_X_MM, FLANGE_BOTTOM_Z_MM),
        (FLANGE_OUTER_X_MM, LEG_TOP_Z_MM),
        (LEG_RUN_X_MM[0], LEG_TOP_Z_MM),
    ]
    lines = []
    for index in range(len(points)):
        x0, z0 = points[index]
        x1, z1 = points[(index + 1) % len(points)]
        lines.append(
            build123d.Edge.make_line(
                (x0, LEG_INNER_Y_MM, z0), (x1, LEG_INNER_Y_MM, z1)
            )
        )
    wires = list(build123d.Wire.combine(lines))
    if len(wires) != 1:
        raise RuntimeError(f"Leg profile produced {len(wires)} wires")
    return build123d.Face(wires[0])


def build_bracket() -> build123d.Shape:
    leg = build123d.Solid.extrude(
        _leg_profile(), build123d.Vector(0.0, lc.SHEET_THICKNESS_MM, 0.0)
    )
    foot = lc.make_box(
        x_min=FOOT_X_MIN_MM,
        x_max=FOOT_X_MAX_MM,
        y_min=FOOT_EDGE_DEFAULT_Y_MM,
        y_max=LEG_OUTER_Y_MM,
        z_min=lc.FOOT_BOTTOM_Z_MM,
        z_max=lc.FOOT_TOP_Z_MM,
    )
    flange = lc.make_box(
        x_min=FLANGE_INNER_X_MM,
        x_max=FLANGE_OUTER_X_MM,
        y_min=FLANGE_EDGE_Y_MM,
        y_max=LEG_OUTER_Y_MM,
        z_min=FLANGE_BOTTOM_Z_MM,
        z_max=LEG_TOP_Z_MM,
    )
    bracket: build123d.Shape = leg.fuse(foot).fuse(flange)

    # Wrap-bend relief notch: sever the leg neck's upper-right corner just
    # below the flange so the wrap bend terminates at a free edge instead of
    # interpenetrating the neck (clearance between the flange bottom and the
    # leg neck for the fold radius).
    bracket = bracket.cut(
        lc.make_box(
            x_min=FLANGE_INNER_X_MM - WRAP_RELIEF_GAP_MM,
            x_max=LEG_RUN_X_MM[1] + WRAP_RELIEF_GAP_MM,
            y_min=LEG_INNER_Y_MM - CUT_EXTENSION_MM,
            y_max=LEG_OUTER_Y_MM + CUT_EXTENSION_MM,
            z_min=FLANGE_BOTTOM_Z_MM - WRAP_RELIEF_DEPTH_MM,
            z_max=FLANGE_BOTTOM_Z_MM,
        )
    )

    # Raised-panel clearance: the only setback in the otherwise straight,
    # flush foot inner edge. The servo's raised center panel protrudes to
    # Z=1.9 over this X-span, so the foot edge steps out to clear it. The
    # bay is a recess, so the foot covers it flush with no setback.
    bracket = bracket.cut(
        lc.make_box(
            x_min=RAISED_ZONE_X_MM[0],
            x_max=RAISED_ZONE_X_MM[1],
            y_min=FOOT_EDGE_DEFAULT_Y_MM - CUT_EXTENSION_MM,
            y_max=FOOT_EDGE_RAISED_Y_MM,
            z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
            z_max=lc.FOOT_TOP_Z_MM + CUT_EXTENSION_MM,
        )
    )

    # Foot servo holes.
    for hole_x in (lc.HOLE_NEAR_OFFSET_MM, lc.HOLE_FAR_OFFSET_MM):
        bracket = bracket.cut(
            lc.make_z_cylinder(
                x=hole_x,
                y=lc.HOLE_SIDE_OFFSET_MM,
                radius=lc.SERVO_MOUNT_HOLE_RADIUS_MM,
                z_min=lc.FOOT_BOTTOM_Z_MM - CUT_EXTENSION_MM,
                z_max=lc.FOOT_TOP_Z_MM + CUT_EXTENSION_MM,
            )
        )
    # Horn clearance: the output horn disk (radius 11.5, on the pitch axis
    # at Z = TOP_PIVOT_Z) sits in the flange's X-plane and rotates. The
    # relief arc is concentric with that horn edge (inline with it, plus a
    # clearance margin) and centered on the axis, so it is symmetric.
    bracket = bracket.cut(
        lc.make_x_cylinder(
            y=0.0,
            z=lc.TOP_PIVOT_Z_MM,
            radius=OUTPUT_HORN_RELIEF_RADIUS_MM,
            x_min=FLANGE_INNER_X_MM - CUT_EXTENSION_MM,
            x_max=FLANGE_OUTER_X_MM + CUT_EXTENSION_MM,
        )
    )
    # Flange servo holes (the case-top +Y pair).
    for hole_z in (lc.CASE_OFFSET_HOLE_Z_MM, lc.TOP_BODY_DOWN_Z_OFFSET_MM + 17.2):
        bracket = bracket.cut(
            lc.make_x_cylinder(
                y=lc.HOLE_SIDE_OFFSET_MM,
                z=hole_z,
                radius=lc.SERVO_MOUNT_HOLE_RADIUS_MM,
                x_min=FLANGE_INNER_X_MM - CUT_EXTENSION_MM,
                x_max=FLANGE_OUTER_X_MM + CUT_EXTENSION_MM,
            )
        )

    # Round the foot-tab inside corners (Z-direction edges at Y=7.5 where
    # the tabs meet the setback) and the leg/relief corners.
    foot_tab_x = (FOOT_X_MIN_MM, LEFT_TAB_RIGHT_X_MM, RAISED_ZONE_X_MM[1], FOOT_X_MAX_MM)
    leg_x = LEG_RUN_X_MM[0]
    relief_corner_x = LEG_RUN_X_MM[1] + WRAP_RELIEF_GAP_MM
    foot_tab_corners = bracket.edges().filter_by(build123d.Axis.Z).filter_by(
        lambda e: abs(e.center().Y - FOOT_EDGE_DEFAULT_Y_MM) < 0.05
        and any(abs(e.center().X - x) < 0.05 for x in foot_tab_x)
        and abs(e.length - lc.SHEET_THICKNESS_MM) < 0.05
    )
    bracket = bracket.fillet(0.8, foot_tab_corners)

    leg_top_corners = bracket.edges().filter_by(build123d.Axis.Y).filter_by(
        lambda e: abs(e.center().X - leg_x) < 0.05
        and abs(e.center().Z - LEG_TOP_Z_MM) < 0.05
        and abs(e.length - lc.SHEET_THICKNESS_MM) < 0.05
    )
    relief_corners = bracket.edges().filter_by(build123d.Axis.X).filter_by(
        lambda e: abs(e.center().X - relief_corner_x) < 0.05
        and abs(e.center().Z - FLANGE_BOTTOM_Z_MM) < 0.05
        and abs(e.length - lc.SHEET_THICKNESS_MM) < 0.05
    )
    bracket = bracket.fillet(1.5, list(leg_top_corners) + list(relief_corners))

    return bracket


def build_step() -> build123d.Shape:
    bracket = build_bracket()

    solids = bracket.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one connected plate solid, found {len(solids)}")

    top_servo, bottom_servo = lc.placed_link_servos_case()
    top_volume = lc.verify_no_interference(bracket, top_servo, label="the top servo")
    bottom_volume = lc.verify_no_interference(bracket, bottom_servo, label="the bottom servo")

    bracket.label = PART_NAME
    bracket.color = lc.BRACKET_COLOR

    bb = bracket.bounding_box()
    foot_flange = LEG_OUTER_Y_MM - FOOT_EDGE_DEFAULT_Y_MM
    foot_flange_raised = LEG_OUTER_Y_MM - FOOT_EDGE_RAISED_Y_MM
    wrap_flange = LEG_OUTER_Y_MM - FLANGE_EDGE_Y_MM
    # Worst-case wrap-flange depth at the horn-relief intrusion (top of flange).
    horn_intrusion_y = (
        OUTPUT_HORN_RELIEF_RADIUS_MM**2 - (135.0 - LEG_TOP_Z_MM) ** 2
    ) ** 0.5
    wrap_flange_at_horn = LEG_OUTER_Y_MM - horn_intrusion_y
    hole_to_bend = LEG_OUTER_Y_MM - lc.HOLE_SIDE_OFFSET_MM
    half_die = lc.SCS_HALF_DIE_WIDTH_MM
    min_flange = lc.SCS_MIN_FLANGE_AFTER_BEND_MM
    print(
        "Wrap plate envelope "
        f"X={bb.size.X:.3f} mm, Y={bb.size.Y:.3f} mm, Z={bb.size.Z:.3f} mm"
    )
    print(
        "Sheet setup "
        f"material=5052-H32 (ALU-063), thickness={lc.SHEET_THICKNESS_MM:.4f} mm, "
        "bends=2 (continuous foot bend, full-length wrap bend)"
    )
    print(
        "SendCutSend flange checks (min after-bend "
        f"{min_flange:.3f} mm): "
        f"foot default {foot_flange:.2f} mm, "
        f"foot raised-zone setback {foot_flange_raised:.2f} mm, "
        f"wrap main {wrap_flange:.2f} mm, "
        f"wrap at horn-relief Z={LEG_TOP_Z_MM:.1f} {wrap_flange_at_horn:.2f} mm "
        f"-- all PASS"
    )
    print(
        f"SendCutSend hole-to-bend checks (half die {half_die:.3f} mm): "
        f"foot screws {hole_to_bend:.2f} mm, wrap-flange screws {hole_to_bend:.2f} mm -- both PASS"
    )
    print(
        f"Clearances: leg gap {LEG_FACE_CLEARANCE_MM:.1f} mm off the servo side face; "
        f"flange gap {FLANGE_FACE_CLEARANCE_MM:.2f} mm off the case-top face; "
        f"flange inner edge at Y={FLANGE_EDGE_Y_MM} clears the case-top ridge"
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
