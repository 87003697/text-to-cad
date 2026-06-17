#!/usr/bin/env python3
"""
Bent-sheet end bracket for wrapping a 2020 extrusion end and two side faces.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

V1_DIR = Path(__file__).resolve().parents[1]
TOM_DIR = V1_DIR.parent
_CACHE_HOME = V1_DIR / ".cache"
_CACHE_HOME.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_HOME))
os.environ.setdefault("EZDXF_CACHE_HOME", str(_CACHE_HOME / "ezdxf"))

import build123d
import ezdxf
from ezdxf.units import MM as DXF_UNIT_MM

if str(TOM_DIR) not in sys.path:
    sys.path.insert(0, str(TOM_DIR))

from robot_common.keyed_connection import (
    KEYED_CONNECTION_HALF_SPAN_MM,
    KEYED_CONNECTION_REF,
    KEYED_CONNECTION_ROTATION_DEG,
    make_keyed_connection_face,
    sample_keyed_connection_outline,
)


PART_NAME = Path(__file__).stem
DISPLAY_NAME = "Extrusion Bracket 2020"

EXTRUSION_X_SPAN_MM = 20.0
EXTRUSION_X_HALF_SPAN_MM = 0.5 * EXTRUSION_X_SPAN_MM
EXTRUSION_Y_SPAN_MM = 20.0
EXTRUSION_Y_HALF_SPAN_MM = 0.5 * EXTRUSION_Y_SPAN_MM
# Keep the bracket seated tightly on the 2020 extrusion; any additional offset
# should come from a future face redesign rather than opening the full wrap gap.
EXTRUSION_CLEARANCE_MM = 0.5
SHORT_SIDE_MM = EXTRUSION_X_SPAN_MM
SHORT_SIDE_HALF_MM = 0.5 * SHORT_SIDE_MM
SHEET_THICKNESS_MM = 1.6
BRACKET_DEPTH_MM = 30.0
FREE_END_CORNER_RADIUS_MM = 1.0
M5_CLEARANCE_RADIUS_MM = 2.75
# Keep the inner row a bit farther off the bend die region for SendCutSend.
SIDE_FASTENER_Z_POSITIONS_MM = (10.0, 22.0)
CUT_EXTENSION_MM = 1.0
EDGE_MATCH_TOLERANCE_MM = 1e-3

SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM = 25.4 * 0.038
SENDCUTSEND_K_FACTOR = 0.48
SENDCUTSEND_BEND_DEDUCTION_90_MM = (
    2.0 * (SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + SHEET_THICKNESS_MM)
) - (
    (math.pi * 0.5)
    * (SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + (SENDCUTSEND_K_FACTOR * SHEET_THICKNESS_MM))
)
SENDCUTSEND_MIN_FLANGE_FORMED_MM = 25.4 * 0.313
SENDCUTSEND_MIN_FLANGE_FLAT_MM = 25.4 * 0.255
SENDCUTSEND_HALF_DIE_WIDTH_MM = 0.5 * (25.4 * 0.472)

INNER_X_MIN = -(EXTRUSION_X_HALF_SPAN_MM + EXTRUSION_CLEARANCE_MM)
INNER_X_MAX = -INNER_X_MIN
INNER_Y_MIN = -(EXTRUSION_Y_HALF_SPAN_MM + EXTRUSION_CLEARANCE_MM)
INNER_Y_MAX = -INNER_Y_MIN
OUTER_X_MIN = -SHORT_SIDE_HALF_MM
OUTER_X_MAX = SHORT_SIDE_HALF_MM

BASE_Z_MIN = -SHEET_THICKNESS_MM
BASE_Z_MAX = 0.0
SIDE_Z_MIN = BASE_Z_MIN
SIDE_Z_MAX = BRACKET_DEPTH_MM

LOWER_Y_MIN = INNER_Y_MIN - SHEET_THICKNESS_MM
LOWER_Y_MAX = INNER_Y_MIN
UPPER_Y_MIN = INNER_Y_MAX
UPPER_Y_MAX = INNER_Y_MAX + SHEET_THICKNESS_MM


@dataclass(frozen=True)
class FlatPattern:
    total_length_mm: float
    width_mm: float
    flange_flat_length_mm: float
    web_flat_start_mm: float
    web_flat_end_mm: float
    bend_line_positions_mm: tuple[float, float]
    cut_profile_centers: tuple[tuple[float, float], ...]
    cut_circles: tuple[tuple[float, float, float], ...]
    rounded_corner_radius_mm: float
    min_feature_to_bend_mm: float


def _make_box(
    *,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
) -> build123d.Solid:
    return build123d.Solid.make_box(
        x_max - x_min,
        y_max - y_min,
        z_max - z_min,
        plane=build123d.Plane(origin=build123d.Vector(x_min, y_min, z_min)),
    )


def _make_cylinder_along(
    *,
    radius: float,
    height: float,
    origin: build123d.Vector,
    direction: build123d.Vector,
) -> build123d.Solid:
    return build123d.Solid.make_cylinder(
        radius,
        height,
        plane=build123d.Plane(origin=origin, z_dir=direction),
    )


def _make_keyed_connection_cutter_along_z(
    *,
    center_x: float,
    center_y: float,
    start_z: float,
    height: float,
) -> build123d.Solid:
    face = make_keyed_connection_face(
        origin=build123d.Vector(center_x, center_y, start_z),
        u_dir=build123d.Vector(1.0, 0.0, 0.0),
        v_dir=build123d.Vector(0.0, 1.0, 0.0),
    )
    return build123d.Solid.extrude(face, build123d.Vector(0.0, 0.0, height))


def _sendcutsend_outside_setback_90_mm() -> float:
    return SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + SHEET_THICKNESS_MM


def _sendcutsend_bend_allowance_90_mm() -> float:
    return (math.pi * 0.5) * (
        SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + (SENDCUTSEND_K_FACTOR * SHEET_THICKNESS_MM)
    )


def _is_close(value: float, expected: float, tolerance: float = EDGE_MATCH_TOLERANCE_MM) -> bool:
    return abs(value - expected) <= tolerance


def _validate_sendcutsend_bend_rule() -> None:
    bend_deduction_error = abs(
        SENDCUTSEND_BEND_DEDUCTION_90_MM
        - ((2.0 * _sendcutsend_outside_setback_90_mm()) - _sendcutsend_bend_allowance_90_mm())
    )
    if bend_deduction_error > 0.05:
        raise RuntimeError(
            "SendCutSend bend-rule constants are inconsistent: "
            f"bend deduction error {bend_deduction_error:.3f} mm is too large"
        )


def build_bracket() -> build123d.Shape:
    end_face = _make_box(
        x_min=OUTER_X_MIN,
        x_max=OUTER_X_MAX,
        y_min=INNER_Y_MIN,
        y_max=INNER_Y_MAX,
        z_min=BASE_Z_MIN,
        z_max=BASE_Z_MAX,
    )

    lower_flange = _make_box(
        x_min=OUTER_X_MIN,
        x_max=OUTER_X_MAX,
        y_min=LOWER_Y_MIN,
        y_max=LOWER_Y_MAX,
        z_min=SIDE_Z_MIN,
        z_max=SIDE_Z_MAX,
    )

    upper_flange = _make_box(
        x_min=OUTER_X_MIN,
        x_max=OUTER_X_MAX,
        y_min=UPPER_Y_MIN,
        y_max=UPPER_Y_MAX,
        z_min=SIDE_Z_MIN,
        z_max=SIDE_Z_MAX,
    )

    bracket = end_face.fuse(lower_flange).fuse(upper_flange)

    hole_length = SHEET_THICKNESS_MM + (2.0 * CUT_EXTENSION_MM)
    bracket = bracket.cut(
        _make_keyed_connection_cutter_along_z(
            center_x=0.0,
            center_y=0.0,
            start_z=BASE_Z_MIN - CUT_EXTENSION_MM,
            height=hole_length,
        )
    )

    for z_position in SIDE_FASTENER_Z_POSITIONS_MM:
        bracket = bracket.cut(
            _make_cylinder_along(
                radius=M5_CLEARANCE_RADIUS_MM,
                height=hole_length,
                origin=build123d.Vector(0.0, LOWER_Y_MIN - CUT_EXTENSION_MM, z_position),
                direction=build123d.Vector(0.0, 1.0, 0.0),
            )
        )
        bracket = bracket.cut(
            _make_cylinder_along(
                radius=M5_CLEARANCE_RADIUS_MM,
                height=hole_length,
                origin=build123d.Vector(0.0, UPPER_Y_MAX + CUT_EXTENSION_MM, z_position),
                direction=build123d.Vector(0.0, -1.0, 0.0),
            )
        )

    target_edge_centers = (
        build123d.Vector(
            -SHORT_SIDE_HALF_MM,
            LOWER_Y_MIN + (0.5 * SHEET_THICKNESS_MM),
            BRACKET_DEPTH_MM,
        ),
        build123d.Vector(
            SHORT_SIDE_HALF_MM,
            LOWER_Y_MIN + (0.5 * SHEET_THICKNESS_MM),
            BRACKET_DEPTH_MM,
        ),
        build123d.Vector(
            -SHORT_SIDE_HALF_MM,
            UPPER_Y_MAX - (0.5 * SHEET_THICKNESS_MM),
            BRACKET_DEPTH_MM,
        ),
        build123d.Vector(
            SHORT_SIDE_HALF_MM,
            UPPER_Y_MAX - (0.5 * SHEET_THICKNESS_MM),
            BRACKET_DEPTH_MM,
        ),
    )
    target_edges: list[build123d.Edge] = []
    for edge in bracket.edges():
        if edge.geom_type != build123d.GeomType.LINE:
            continue
        center = edge.center()
        if any(
            _is_close(center.X, target_center.X)
            and _is_close(center.Y, target_center.Y)
            and _is_close(center.Z, target_center.Z)
            for target_center in target_edge_centers
        ):
            target_edges.append(edge)

    if len(target_edges) != len(target_edge_centers):
        raise RuntimeError(
            "Expected to find the four flange free-end corners for rounding, "
            f"found {len(target_edges)}"
        )

    bracket = build123d.fillet(target_edges, FREE_END_CORNER_RADIUS_MM)

    solids = list(bracket.solids())
    if len(solids) != 1:
        raise RuntimeError(f"Expected one wrap bracket solid, found {len(solids)}")

    bracket = solids[0]
    bracket.label = PART_NAME

    bbox = bracket.bounding_box()
    print(
        "Bent-sheet 2020 end bracket: "
        f"clearance={EXTRUSION_CLEARANCE_MM:.3f} mm, "
        f"thickness={SHEET_THICKNESS_MM:.3f} mm, "
        f"depth={BRACKET_DEPTH_MM:.3f} mm, "
        f"free_corner_radius={FREE_END_CORNER_RADIUS_MM:.3f} mm, "
        f"size=({bbox.size.X:.3f}, {bbox.size.Y:.3f}, {bbox.size.Z:.3f}) mm, "
        f"plate_fasteners={2 * len(SIDE_FASTENER_Z_POSITIONS_MM)}, "
        f"connection_ref={KEYED_CONNECTION_REF}, "
        f"connection_rotation={KEYED_CONNECTION_ROTATION_DEG:.1f} deg, "
        f"hole_diameter={2.0 * M5_CLEARANCE_RADIUS_MM:.3f} mm"
    )
    return bracket


def _build_flat_pattern() -> FlatPattern:
    _validate_sendcutsend_bend_rule()

    outside_setback = _sendcutsend_outside_setback_90_mm()
    bend_allowance = _sendcutsend_bend_allowance_90_mm()
    flange_outside_length = BRACKET_DEPTH_MM + SHEET_THICKNESS_MM
    flange_flat_length = flange_outside_length - outside_setback
    if flange_outside_length < SENDCUTSEND_MIN_FLANGE_FORMED_MM:
        raise RuntimeError(
            "Formed flange length is below the configured bend-rule minimum: "
            f"{flange_outside_length:.3f} mm < {SENDCUTSEND_MIN_FLANGE_FORMED_MM:.3f} mm"
        )
    if flange_flat_length < SENDCUTSEND_MIN_FLANGE_FLAT_MM:
        raise RuntimeError(
            "Flat flange length is below the configured bend-rule minimum: "
            f"{flange_flat_length:.3f} mm < {SENDCUTSEND_MIN_FLANGE_FLAT_MM:.3f} mm"
        )

    web_outside_length = INNER_Y_MAX - INNER_Y_MIN
    web_flat_length = web_outside_length - (2.0 * SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM)
    if web_flat_length <= 0.0:
        raise RuntimeError(f"Web flat length implied by the bend rule is too short: {web_flat_length:.3f} mm")

    web_flat_start = flange_flat_length + bend_allowance
    web_flat_end = web_flat_start + web_flat_length
    total_length = web_flat_end + bend_allowance + flange_flat_length
    width = OUTER_X_MAX - OUTER_X_MIN
    bend_line_positions = (
        flange_flat_length + (0.5 * bend_allowance),
        web_flat_end + (0.5 * bend_allowance),
    )

    def _v_from_x(x_coord: float) -> float:
        return x_coord - OUTER_X_MIN

    keyed_connection_center_u = web_flat_start + (-INNER_Y_MIN - SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM)
    cut_profile_centers = ((keyed_connection_center_u, _v_from_x(0.0)),)
    cut_circles: list[tuple[float, float, float]] = []
    for z_position in SIDE_FASTENER_Z_POSITIONS_MM:
        lower_u = BRACKET_DEPTH_MM - z_position
        upper_u = total_length - (BRACKET_DEPTH_MM - z_position)
        cut_circles.append((lower_u, _v_from_x(0.0), M5_CLEARANCE_RADIUS_MM))
        cut_circles.append((upper_u, _v_from_x(0.0), M5_CLEARANCE_RADIUS_MM))

    min_feature_to_bend = min(
        abs(keyed_connection_center_u - bend_u) - KEYED_CONNECTION_HALF_SPAN_MM
        for bend_u in bend_line_positions
    )
    min_feature_to_bend = min(
        min_feature_to_bend,
        min(
            abs(center_u - bend_u) - radius
            for center_u, _center_v, radius in cut_circles
            for bend_u in bend_line_positions
        ),
    )
    return FlatPattern(
        total_length_mm=total_length,
        width_mm=width,
        flange_flat_length_mm=flange_flat_length,
        web_flat_start_mm=web_flat_start,
        web_flat_end_mm=web_flat_end,
        bend_line_positions_mm=bend_line_positions,
        cut_profile_centers=cut_profile_centers,
        cut_circles=tuple(cut_circles),
        rounded_corner_radius_mm=FREE_END_CORNER_RADIUS_MM,
        min_feature_to_bend_mm=min_feature_to_bend,
    )


def build_dxf() -> ezdxf.document.Drawing:
    flat_pattern = _build_flat_pattern()

    doc = ezdxf.new("R2010", setup=True)
    doc.units = DXF_UNIT_MM
    if "CUT" not in doc.layers:
        doc.layers.add("CUT")
    if "BEND" not in doc.layers:
        doc.layers.add("BEND", linetype="DASHED")

    msp = doc.modelspace()
    corner_radius = flat_pattern.rounded_corner_radius_mm
    if corner_radius <= 0.0:
        outline = (
            (0.0, 0.0),
            (flat_pattern.total_length_mm, 0.0),
            (flat_pattern.total_length_mm, flat_pattern.width_mm),
            (0.0, flat_pattern.width_mm),
        )
        msp.add_lwpolyline(outline, format="xy", close=True, dxfattribs={"layer": "CUT"})
    else:
        if (2.0 * corner_radius) >= flat_pattern.width_mm:
            raise RuntimeError(
                "Rounded flange corners exceed the flat-pattern width: "
                f"diameter {2.0 * corner_radius:.3f} mm >= width {flat_pattern.width_mm:.3f} mm"
            )
        msp.add_line(
            (corner_radius, 0.0),
            (flat_pattern.total_length_mm - corner_radius, 0.0),
            dxfattribs={"layer": "CUT"},
        )
        msp.add_arc(
            (corner_radius, corner_radius),
            corner_radius,
            180.0,
            270.0,
            dxfattribs={"layer": "CUT"},
        )
        msp.add_arc(
            (flat_pattern.total_length_mm - corner_radius, corner_radius),
            corner_radius,
            270.0,
            360.0,
            dxfattribs={"layer": "CUT"},
        )
        msp.add_line(
            (flat_pattern.total_length_mm, corner_radius),
            (flat_pattern.total_length_mm, flat_pattern.width_mm - corner_radius),
            dxfattribs={"layer": "CUT"},
        )
        msp.add_arc(
            (flat_pattern.total_length_mm - corner_radius, flat_pattern.width_mm - corner_radius),
            corner_radius,
            0.0,
            90.0,
            dxfattribs={"layer": "CUT"},
        )
        msp.add_line(
            (flat_pattern.total_length_mm - corner_radius, flat_pattern.width_mm),
            (corner_radius, flat_pattern.width_mm),
            dxfattribs={"layer": "CUT"},
        )
        msp.add_arc(
            (corner_radius, flat_pattern.width_mm - corner_radius),
            corner_radius,
            90.0,
            180.0,
            dxfattribs={"layer": "CUT"},
        )
        msp.add_line(
            (0.0, flat_pattern.width_mm - corner_radius),
            (0.0, corner_radius),
            dxfattribs={"layer": "CUT"},
        )

    for center_u, center_v, radius in flat_pattern.cut_circles:
        msp.add_circle((center_u, center_v), radius, dxfattribs={"layer": "CUT"})
    for center_u, center_v in flat_pattern.cut_profile_centers:
        msp.add_lwpolyline(
            sample_keyed_connection_outline(center_u=center_u, center_v=center_v, points_per_arc=16),
            format="xy",
            close=True,
            dxfattribs={"layer": "CUT"},
        )

    for bend_line_u in flat_pattern.bend_line_positions_mm:
        msp.add_line(
            (bend_line_u, 0.0),
            (bend_line_u, flat_pattern.width_mm),
            dxfattribs={"layer": "BEND", "linetype": "DASHED"},
        )

    print(
        "Flat pattern "
        f"length={flat_pattern.total_length_mm:.3f} mm, "
        f"width={flat_pattern.width_mm:.3f} mm, "
        f"bend_lines=({flat_pattern.bend_line_positions_mm[0]:.3f}, {flat_pattern.bend_line_positions_mm[1]:.3f}) mm, "
        f"cut_profiles={len(flat_pattern.cut_profile_centers)}, "
        f"cut_circles={len(flat_pattern.cut_circles)}"
    )
    print(
        "DXF bend checks "
        f"flange_flat={flat_pattern.flange_flat_length_mm:.3f} mm, "
        f"flange_flat_required={SENDCUTSEND_MIN_FLANGE_FLAT_MM:.3f} mm, "
        f"feature_to_bend_min={flat_pattern.min_feature_to_bend_mm:.3f} mm, "
        f"feature_to_bend_required={SENDCUTSEND_HALF_DIE_WIDTH_MM:.3f} mm"
    )
    if flat_pattern.min_feature_to_bend_mm < SENDCUTSEND_HALF_DIE_WIDTH_MM:
        print(
            "Warning: flat-pattern keyed feature enters the bend die region by "
            f"{SENDCUTSEND_HALF_DIE_WIDTH_MM - flat_pattern.min_feature_to_bend_mm:.3f} mm"
        )
    return doc


def gen_step() -> dict[str, object]:
    return {
        "shape": build_bracket(),
    }


def gen_dxf() -> dict[str, object]:
    return {
        "document": build_dxf(),
    }


if __name__ == "__main__":
    gen_step()
