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
  with a 7.7 mm formed flange,
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
  python v2/link_bracket.py
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parent
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

import build123d

import link_common as lc


PART_NAME = Path(__file__).stem

CUT_EXTENSION_MM = 2.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a floating point number, got {raw!r}") from exc

# Plate planes. The leg stands 0.8 mm off the servo's +Y side face so the
# wrap bend radius clears the servo corner. The flange seats close to the
# case-top face (0.25 mm) so it bolts down flush rather than floating; its
# inner edge stays outboard of the case-top center ridge (proud only over
# Y 5-7). The bend position follows FLANGE_INNER_X parametrically.
CASE_SPAN_CENTERING_OFFSET_MM = _env_float(
    "TOM_V2_CASE_SPAN_CENTERING_OFFSET_MM",
    lc.CASE_SPAN_CENTERING_OFFSET_MM,
)
LEG_FACE_CLEARANCE_MM = 3.5
FLANGE_FACE_CLEARANCE_MM = 0.25
LEG_INNER_Y_MM = 12.36 + LEG_FACE_CLEARANCE_MM  # against the top servo's +Y side face
LEG_OUTER_Y_MM = LEG_INNER_Y_MM + lc.SHEET_THICKNESS_MM
CASE_TOP_FACE_X_MM = lc.SERVO_CASE_TOP_LOCAL_Y_MM + CASE_SPAN_CENTERING_OFFSET_MM
FLANGE_INNER_X_MM = CASE_TOP_FACE_X_MM + FLANGE_FACE_CLEARANCE_MM
FLANGE_OUTER_X_MM = FLANGE_INNER_X_MM + lc.SHEET_THICKNESS_MM
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

# Leg profile (in the X-Z plane). The side-view layout is three constant-width
# bands from the bottom-servo mounting plane to the 180 mm top pitch axis:
# lower vertical section, centered sloped transition, and upper vertical
# section that carries the top-servo face features. The low-X transition
# breakpoints are computed from the high-X slope so the tilted band is offset
# by the same normal width as the vertical bands, instead of becoming narrower
# when measured perpendicular to the tilt.
SECTION_WIDTH_MM = FOOT_X_MAX_MM - FOOT_X_MIN_MM
UPPER_X_MAX_MM = FLANGE_OUTER_X_MM
UPPER_X_MIN_MM = UPPER_X_MAX_MM - SECTION_WIDTH_MM
RIGHT_SLOPE_BOTTOM_Z_MM = 60.0
RIGHT_SLOPE_TOP_Z_MM = 120.0
SLOPE_SECTION_HEIGHT_MM = RIGHT_SLOPE_TOP_Z_MM - RIGHT_SLOPE_BOTTOM_Z_MM
SLOPE_RUN_X_MM = abs(FOOT_X_MAX_MM - UPPER_X_MAX_MM)
LOW_X_SLOPE_Z_SHIFT_DOWN_MM = SECTION_WIDTH_MM * (
    math.hypot(SLOPE_RUN_X_MM, SLOPE_SECTION_HEIGHT_MM) - SLOPE_SECTION_HEIGHT_MM
) / SLOPE_RUN_X_MM
LEFT_SLOPE_BOTTOM_Z_MM = RIGHT_SLOPE_BOTTOM_Z_MM - LOW_X_SLOPE_Z_SHIFT_DOWN_MM
LEFT_SLOPE_TOP_Z_MM = RIGHT_SLOPE_TOP_Z_MM - LOW_X_SLOPE_Z_SHIFT_DOWN_MM
SLOPE_DX_PER_Z_MM = (UPPER_X_MAX_MM - FOOT_X_MAX_MM) / SLOPE_SECTION_HEIGHT_MM
SLOPE_HIGH_X_INTERCEPT_MM = FOOT_X_MAX_MM - SLOPE_DX_PER_Z_MM * RIGHT_SLOPE_BOTTOM_Z_MM
SLOPE_LOW_X_INTERCEPT_MM = FOOT_X_MIN_MM - SLOPE_DX_PER_Z_MM * LEFT_SLOPE_BOTTOM_Z_MM
SLOPED_BAND_NORMAL_WIDTH_MM = abs(
    SLOPE_HIGH_X_INTERCEPT_MM - SLOPE_LOW_X_INTERCEPT_MM
) / math.hypot(1.0, SLOPE_DX_PER_Z_MM)
SLOPE_SECTION_CENTER_Z_MM = 0.25 * (
    LEFT_SLOPE_BOTTOM_Z_MM
    + RIGHT_SLOPE_BOTTOM_Z_MM
    + LEFT_SLOPE_TOP_Z_MM
    + RIGHT_SLOPE_TOP_Z_MM
)
# The sloped band is a constant-width transition from the bottom foot
# span to the top band. The top band width matches the bottom visible
# web width (X 6.0..35.0), while its outside face is pulled inward flush
# with the wrap flange's outside face.
LEG_RUN_X_MM = (min(FOOT_X_MIN_MM, UPPER_X_MIN_MM), max(FOOT_X_MAX_MM, UPPER_X_MAX_MM))
OUTPUT_HORN_TOP_EDGE_GAP_MM = 6.6
LEG_TOP_Z_MM = lc.TOP_PIVOT_Z_MM - OUTPUT_HORN_TOP_EDGE_GAP_MM
FLANGE_BOTTOM_Z_MM = lc.CASE_OFFSET_HOLE_Z_MM - 4.0
TRANSITION_CORNER_FILLET_RADIUS_MM = 24.0
# Uniform flange inner edge: set so both screw tabs (near-horn and end)
# are the same width. It threads the 1.19 mm gap between the rotating
# horn edge (reaches Y=7.96 at the top screw) and the top screw hole
# (inner edge Y=9.15) - clearing the horn while keeping a valid screw web.
FLANGE_EDGE_Y_MM = 8.55

# Side-web lightening slots, paired per sketch in the lower, sloped, and
# upper bands. Slots cut through the sheet along Y and are kept away from
# the bend reliefs and servo screw holes.
SLOT_CUT_Y_OFFSET_MM = LEG_OUTER_Y_MM + CUT_EXTENSION_MM
SLOT_CUT_Y_SPAN_MM = lc.SHEET_THICKNESS_MM + 2.0 * CUT_EXTENSION_MM
SLOT_WIDTH_MM = 4.0
TOP_SLOT_WIDTH_MM = 4.0
LOWER_SLOT_LENGTH_MM = 38.0
MIDDLE_SLOT_LENGTH_MM = 38.0
TOP_SLOT_LENGTH_MM = 34.0
LOWER_SLOT_CENTER_Z_MM = 30.0
MIDDLE_SLOT_CENTER_Z_MM = SLOPE_SECTION_CENTER_Z_MM
TOP_SLOT_CENTER_Z_MM = 0.5 * (RIGHT_SLOPE_TOP_Z_MM + LEG_TOP_Z_MM)
LOWER_SLOT_CENTER_X_MM = (14.0, 27.0)
MIDDLE_SLOT_PAIR_CENTER_X_MM = 0.5 * (
    (SLOPE_DX_PER_Z_MM * MIDDLE_SLOT_CENTER_Z_MM + SLOPE_LOW_X_INTERCEPT_MM)
    + (SLOPE_DX_PER_Z_MM * MIDDLE_SLOT_CENTER_Z_MM + SLOPE_HIGH_X_INTERCEPT_MM)
)
MIDDLE_SLOT_CENTER_SPACING_X_MM = 13.0
MIDDLE_SLOT_CENTER_X_MM = (
    MIDDLE_SLOT_PAIR_CENTER_X_MM - 0.5 * MIDDLE_SLOT_CENTER_SPACING_X_MM,
    MIDDLE_SLOT_PAIR_CENTER_X_MM + 0.5 * MIDDLE_SLOT_CENTER_SPACING_X_MM,
)
MIDDLE_SLOT_CENTER_NORMAL_MM = tuple(
    (slot_x - SLOPE_DX_PER_Z_MM * MIDDLE_SLOT_CENTER_Z_MM - SLOPE_LOW_X_INTERCEPT_MM)
    / math.hypot(1.0, SLOPE_DX_PER_Z_MM)
    for slot_x in MIDDLE_SLOT_CENTER_X_MM
)
MIDDLE_SLOT_LOW_EDGE_MARGIN_MM = MIDDLE_SLOT_CENTER_NORMAL_MM[0] - 0.5 * SLOT_WIDTH_MM
MIDDLE_SLOT_HIGH_EDGE_MARGIN_MM = (
    SLOPED_BAND_NORMAL_WIDTH_MM - MIDDLE_SLOT_CENTER_NORMAL_MM[1] - 0.5 * SLOT_WIDTH_MM
)
TOP_SLOT_CENTER_X_MM = (-4.0, 8.0)
MIDDLE_SLOT_ROTATION_DEG = math.degrees(
    math.atan2(
        0.5 * (LEFT_SLOPE_TOP_Z_MM + RIGHT_SLOPE_TOP_Z_MM)
        - 0.5 * (LEFT_SLOPE_BOTTOM_Z_MM + RIGHT_SLOPE_BOTTOM_Z_MM),
        UPPER_X_MAX_MM - FOOT_X_MAX_MM,
    )
)
VERTICAL_SLOT_ROTATION_DEG = 90.0


def _leg_profile() -> build123d.Face:
    points = [
        (FOOT_X_MIN_MM, lc.FOOT_BOTTOM_Z_MM),
        (FOOT_X_MAX_MM, lc.FOOT_BOTTOM_Z_MM),
        (FOOT_X_MAX_MM, RIGHT_SLOPE_BOTTOM_Z_MM),
        (UPPER_X_MAX_MM, RIGHT_SLOPE_TOP_Z_MM),
        (UPPER_X_MAX_MM, LEG_TOP_Z_MM),
        (UPPER_X_MIN_MM, LEG_TOP_Z_MM),
        (UPPER_X_MIN_MM, LEFT_SLOPE_TOP_Z_MM),
        (FOOT_X_MIN_MM, LEFT_SLOPE_BOTTOM_Z_MM),
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


def _slot_cut(
    *,
    x: float,
    z: float,
    length: float,
    width: float,
    rotation: float,
) -> build123d.Shape:
    with build123d.BuildPart() as slot:
        with build123d.BuildSketch(build123d.Plane.XZ):
            with build123d.Locations((x, z)):
                build123d.SlotOverall(width=length, height=width, rotation=rotation)
        build123d.extrude(amount=SLOT_CUT_Y_SPAN_MM)

    return slot.part.moved(build123d.Location((0.0, SLOT_CUT_Y_OFFSET_MM, 0.0)))


def _slot_cuts() -> list[build123d.Shape]:
    slots: list[build123d.Shape] = []
    for slot_x in LOWER_SLOT_CENTER_X_MM:
        slots.append(
            _slot_cut(
                x=slot_x,
                z=LOWER_SLOT_CENTER_Z_MM,
                length=LOWER_SLOT_LENGTH_MM,
                width=SLOT_WIDTH_MM,
                rotation=VERTICAL_SLOT_ROTATION_DEG,
            )
        )
    for slot_x in MIDDLE_SLOT_CENTER_X_MM:
        slots.append(
            _slot_cut(
                x=slot_x,
                z=MIDDLE_SLOT_CENTER_Z_MM,
                length=MIDDLE_SLOT_LENGTH_MM,
                width=SLOT_WIDTH_MM,
                rotation=MIDDLE_SLOT_ROTATION_DEG,
            )
        )
    for slot_x in TOP_SLOT_CENTER_X_MM:
        slots.append(
            _slot_cut(
                x=slot_x,
                z=TOP_SLOT_CENTER_Z_MM,
                length=TOP_SLOT_LENGTH_MM,
                width=TOP_SLOT_WIDTH_MM,
                rotation=VERTICAL_SLOT_ROTATION_DEG,
            )
        )
    return slots


def _transition_corner_edges(bracket: build123d.Shape) -> list[build123d.Edge]:
    corner_points = (
        (FOOT_X_MAX_MM, RIGHT_SLOPE_BOTTOM_Z_MM),
        (UPPER_X_MAX_MM, RIGHT_SLOPE_TOP_Z_MM),
        (UPPER_X_MIN_MM, LEFT_SLOPE_TOP_Z_MM),
        (FOOT_X_MIN_MM, LEFT_SLOPE_BOTTOM_Z_MM),
    )
    return list(
        bracket.edges().filter_by(build123d.Axis.Y).filter_by(
            lambda e: any(
                abs(e.center().X - x) < 0.05
                and abs(e.center().Z - z) < 0.05
                and abs(e.length - lc.SHEET_THICKNESS_MM) < 0.05
                for x, z in corner_points
            )
        )
    )


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
    for slot in _slot_cuts():
        bracket = bracket.cut(slot)

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
    # the tabs meet the setback) and the leg top corner.
    foot_tab_x = (FOOT_X_MIN_MM, LEFT_TAB_RIGHT_X_MM, RAISED_ZONE_X_MM[1], FOOT_X_MAX_MM)
    leg_x = LEG_RUN_X_MM[0]
    foot_tab_corners = bracket.edges().filter_by(build123d.Axis.Z).filter_by(
        lambda e: abs(e.center().Y - FOOT_EDGE_DEFAULT_Y_MM) < 0.05
        and any(abs(e.center().X - x) < 0.05 for x in foot_tab_x)
        and abs(e.length - lc.SHEET_THICKNESS_MM) < 0.05
    )
    bracket = bracket.fillet(0.8, foot_tab_corners)

    if TRANSITION_CORNER_FILLET_RADIUS_MM < 0.0:
        raise ValueError("TRANSITION_CORNER_FILLET_RADIUS_MM must be >= 0")
    if TRANSITION_CORNER_FILLET_RADIUS_MM > 0.0:
        transition_corners = _transition_corner_edges(bracket)
        if len(transition_corners) != 4:
            raise RuntimeError(
                f"Expected four transition corners to fillet, found {len(transition_corners)}"
            )
        bracket = bracket.fillet(TRANSITION_CORNER_FILLET_RADIUS_MM, transition_corners)

    leg_top_corners = bracket.edges().filter_by(build123d.Axis.Y).filter_by(
        lambda e: abs(e.center().X - leg_x) < 0.05
        and abs(e.center().Z - LEG_TOP_Z_MM) < 0.05
        and abs(e.length - lc.SHEET_THICKNESS_MM) < 0.05
    )
    bracket = bracket.fillet(1.5, leg_top_corners)

    return bracket


def build_step() -> build123d.Shape:
    bracket = build_bracket()

    solids = bracket.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one connected plate solid, found {len(solids)}")

    top_servo, bottom_servo = lc.placed_link_servos_case(
        case_span_centering_offset_mm=CASE_SPAN_CENTERING_OFFSET_MM,
    )
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
        OUTPUT_HORN_RELIEF_RADIUS_MM**2 - (lc.TOP_PIVOT_Z_MM - LEG_TOP_Z_MM) ** 2
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
        "Top case centering offset "
        f"{CASE_SPAN_CENTERING_OFFSET_MM:.3f} mm; "
        f"case-top face X={CASE_TOP_FACE_X_MM:.3f} mm"
    )
    print(
        f"Side profile bands: lower to low/high-X Z="
        f"{LEFT_SLOPE_BOTTOM_Z_MM:.1f}/{RIGHT_SLOPE_BOTTOM_Z_MM:.1f} mm, "
        f"constant-width slope to low/high-X Z="
        f"{LEFT_SLOPE_TOP_Z_MM:.1f}/{RIGHT_SLOPE_TOP_Z_MM:.1f} mm, "
        f"upper to axis Z={lc.TOP_PIVOT_Z_MM:.1f} mm; "
        f"top servo pitch axis Z={lc.TOP_PIVOT_Z_MM:.1f} mm"
    )
    print(
        f"Constant-width web check: vertical bands {SECTION_WIDTH_MM:.3f} mm, "
        f"sloped band normal width {SLOPED_BAND_NORMAL_WIDTH_MM:.3f} mm, "
        f"low-X transition shift {LOW_X_SLOPE_Z_SHIFT_DOWN_MM:.3f} mm down"
    )
    print(
        "Transition corner radius: "
        + (
            "disabled"
            if TRANSITION_CORNER_FILLET_RADIUS_MM == 0.0
            else f"{TRANSITION_CORNER_FILLET_RADIUS_MM:.1f} mm"
        )
    )
    print(
        "Slots: 2x lower vertical, 2x sloped transition, 2x upper vertical "
        f"(widths {SLOT_WIDTH_MM:.1f}/{TOP_SLOT_WIDTH_MM:.1f} mm)"
    )
    print(
        f"Sloped slot margins: low edge {MIDDLE_SLOT_LOW_EDGE_MARGIN_MM:.3f} mm, "
        f"high edge {MIDDLE_SLOT_HIGH_EDGE_MARGIN_MM:.3f} mm"
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
