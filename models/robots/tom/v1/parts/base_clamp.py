#!/usr/bin/env python3
"""
Generate a bent base clamp that wraps under the base servo footprint.

The upper plate stays in the servo base frame, a side wall rises from the
positive-x side, and a lower return closes the U so the part can clamp to a
desk or fixture from below.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import sys
from pathlib import Path

from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

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


DISPLAY_NAME = "Base Clamp"
PLATE_THICKNESS_MM = 25.4 * 0.125
SADDLE_FOOTPRINT_X_BUFFER_MM = 2.0
SIDE_WALL_THICKNESS_MM = PLATE_THICKNESS_MM
BOOLEAN_OVERLAP_MM = 0.05
CUT_EXTENSION_MM = 0.5
CORNER_RADIUS_MM = 3.0
RETURN_FACE_LENGTH_RATIO = 2.0 / 3.0
M3_CLEARANCE_RADIUS_MM = 1.6
BASE_CENTER_HORN_FACE_RADIUS_MM = 2.8
BASE_CENTER_HORN_FACE_CLEARANCE_BUFFER_MM = 0.25
BASE_CENTER_HORN_FACE_CLEARANCE_RADIUS_MM = (
    BASE_CENTER_HORN_FACE_RADIUS_MM + BASE_CENTER_HORN_FACE_CLEARANCE_BUFFER_MM
)
M8_CLEARANCE_DIAMETER_MM = 8.5
M8_CLEARANCE_RADIUS_MM = 0.5 * M8_CLEARANCE_DIAMETER_MM
SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM = 25.4 * 0.125
SENDCUTSEND_K_FACTOR = 0.44
SENDCUTSEND_BEND_DEDUCTION_90_MM = 25.4 * 0.216
SENDCUTSEND_HALF_DIE_WIDTH_MM = 0.5 * (25.4 * 0.630)
SENDCUTSEND_MIN_FLANGE_FORMED_MM = 25.4 * 0.476
SENDCUTSEND_MOUNT_HOLE_TO_BEND_BUFFER_MM = 0.1

# Base servo footprint bounds in robot-arm local coordinates.
SADDLE_BASE_FACE_Y_MM = -29.8002
SADDLE_BASE_X_MIN_MM = -35.61
SADDLE_BASE_X_MAX_MM = 6.338516
SADDLE_BASE_Z_MIN_MM = -15.960226
SADDLE_BASE_Z_MAX_MM = 15.960224

# 5x M3 lower-horn clearance pattern on the base plate.
BASE_HORN_CONNECTION_HOLE_SPECS_MM: tuple[tuple[float, float, float], ...] = (
    (-32.5, 0.0, M3_CLEARANCE_RADIUS_MM),
    (-25.5, -7.0, M3_CLEARANCE_RADIUS_MM),
    (-25.5, 0.0, BASE_CENTER_HORN_FACE_CLEARANCE_RADIUS_MM),
    (-25.5, 7.0, M3_CLEARANCE_RADIUS_MM),
    (-18.5, 0.0, M3_CLEARANCE_RADIUS_MM),
)

# Resolved from:
# @cad[STEP/imports/sts3250#o1.2.e920,o1.2.e922]
# @cad[STEP/imports/sts3250#o1.2.e921,o1.2.e923]
M2_CLEARANCE_RADIUS_MM = 1.1
BASE_SERVO_M2_HOLE_SPECS_MM: tuple[tuple[float, float, float], ...] = (
    (-17.2, -10.25, M2_CLEARANCE_RADIUS_MM),
    (7.25, -10.25, M2_CLEARANCE_RADIUS_MM),
    (-17.2, 10.25, M2_CLEARANCE_RADIUS_MM),
    (7.25, 10.25, M2_CLEARANCE_RADIUS_MM),
)
BASE_ATTACHMENT_HOLES_MM: tuple[tuple[float, float, float], ...] = (
    BASE_HORN_CONNECTION_HOLE_SPECS_MM + BASE_SERVO_M2_HOLE_SPECS_MM
)

# Resolved from the four PCB spacer hole centers in the original SO-101 bus board.
SERVO_BUS_BOARD_SPACER_CENTERS_XY_MM: tuple[tuple[float, float], ...] = (
    (2.4930065, 2.4999875),
    (2.4930065, 30.4999925),
    (39.493009, 2.4999875),
    (39.505008000000004, 30.4999925),
)
SERVO_BUS_SPACER_CLEARANCE_RADIUS_MM = M3_CLEARANCE_RADIUS_MM
SERVO_BUS_MOUNT_LOCAL_X_MIN_MM = min(x for x, _y in SERVO_BUS_BOARD_SPACER_CENTERS_XY_MM)
SERVO_BUS_MOUNT_LOCAL_X_MAX_MM = max(x for x, _y in SERVO_BUS_BOARD_SPACER_CENTERS_XY_MM)
SERVO_BUS_MOUNT_LOCAL_X_CENTER_MM = 0.5 * (SERVO_BUS_MOUNT_LOCAL_X_MIN_MM + SERVO_BUS_MOUNT_LOCAL_X_MAX_MM)
SERVO_BUS_MOUNT_LOCAL_Y_MIN_MM = min(y for _x, y in SERVO_BUS_BOARD_SPACER_CENTERS_XY_MM)
SERVO_BUS_MOUNT_LOCAL_Y_MAX_MM = max(y for _x, y in SERVO_BUS_BOARD_SPACER_CENTERS_XY_MM)
SERVO_BUS_MOUNT_LOCAL_Y_CENTER_MM = 0.5 * (SERVO_BUS_MOUNT_LOCAL_Y_MIN_MM + SERVO_BUS_MOUNT_LOCAL_Y_MAX_MM)
SENDCUTSEND_MIN_HOLE_TO_EDGE_QUOTE_MM = 0.97
BASE_CLAMP_HOLE_TO_EDGE_BUFFER_MM = 0.53
BASE_CLAMP_HOLE_TO_EDGE_TARGET_MM = (
    SENDCUTSEND_MIN_HOLE_TO_EDGE_QUOTE_MM + BASE_CLAMP_HOLE_TO_EDGE_BUFFER_MM
)
BASE_CLAMP_SERVO_BUS_SPACER_WIDTH_MM = (
    (SERVO_BUS_MOUNT_LOCAL_Y_MAX_MM - SERVO_BUS_MOUNT_LOCAL_Y_MIN_MM)
    + (2.0 * (SERVO_BUS_SPACER_CLEARANCE_RADIUS_MM + BASE_CLAMP_HOLE_TO_EDGE_TARGET_MM))
)

# SendCutSend's current .125" 5052 guideline uses a 0.630" die width for
# bending; cut features should stay outside half of that die width from the
# bend line. We size the wall from the mount-hole span plus that offset.
SENDCUTSEND_5052_125_DIE_WIDTH_MM = 0.630 * 25.4
SENDCUTSEND_FEATURE_TO_BEND_MIN_MM = (0.5 * SENDCUTSEND_5052_125_DIE_WIDTH_MM) + SERVO_BUS_SPACER_CLEARANCE_RADIUS_MM
U_BRACKET_CLEARANCE_MM = (
    (SERVO_BUS_MOUNT_LOCAL_X_MAX_MM - SERVO_BUS_MOUNT_LOCAL_X_MIN_MM)
    + (2.0 * SENDCUTSEND_FEATURE_TO_BEND_MIN_MM)
    - (2.0 * PLATE_THICKNESS_MM)
)

# Resolved from @cad[STEP/imports/servo_bus].
SERVO_BUS_LOCAL_X_MIN_MM = -0.390013
SERVO_BUS_LOCAL_X_MAX_MM = 42.401989
SERVO_BUS_LOCAL_Y_MIN_MM = -0.012095
SERVO_BUS_LOCAL_Y_MAX_MM = 33.012075
SERVO_BUS_LOCAL_Z_MIN_MM = -6.0
SERVO_BUS_LOCAL_Z_MAX_MM = 1.599999
BASE_PLATE_Z_WIDTH_MM = max(
    SERVO_BUS_LOCAL_Y_MAX_MM - SERVO_BUS_LOCAL_Y_MIN_MM,
    BASE_CLAMP_SERVO_BUS_SPACER_WIDTH_MM,
)


@dataclass(frozen=True)
class BracketLayout:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    width: float
    height: float
    return_width: float
    return_x_min: float
    return_y_min: float
    return_y_max: float
    side_x_min: float
    side_x_max: float
    overall_y_min: float
    face_y: float
    attachment_holes: tuple[tuple[float, float, float], ...]
    return_mount_holes: tuple[tuple[float, float, float], ...]
    servo_bus_mount_holes_yzr: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class FlatPattern:
    total_length_mm: float
    width_mm: float
    top_flange_flat_length_mm: float
    return_flange_flat_length_mm: float
    web_flat_start_mm: float
    web_flat_end_mm: float
    bend_line_positions_mm: tuple[float, float]
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


def _sendcutsend_outside_setback_90_mm() -> float:
    return SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + PLATE_THICKNESS_MM


def _sendcutsend_bend_allowance_90_mm() -> float:
    return (math.pi * 0.5) * (
        SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + (SENDCUTSEND_K_FACTOR * PLATE_THICKNESS_MM)
    )


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


def _cast_shape(obj: object) -> build123d.Shape:
    from build123d.topology import downcast
    from build123d.topology.shape_core import shapetype

    occt_shape = downcast(obj)
    shape_name = build123d.Shape.shape_LUT[shapetype(occt_shape)]
    shape_type = getattr(build123d, shape_name)
    return shape_type.cast(occt_shape)


def _geom_type_name(shape: build123d.Shape) -> str:
    geom_type = shape.geom_type
    return geom_type.name if hasattr(geom_type, "name") else str(geom_type)


def _build_bracket_layout() -> BracketLayout:
    x_min = SADDLE_BASE_X_MIN_MM - SADDLE_FOOTPRINT_X_BUFFER_MM
    x_max = SADDLE_BASE_X_MAX_MM + SADDLE_FOOTPRINT_X_BUFFER_MM
    required_x_max_for_mount_holes = max(
        x
        + radius
        + SENDCUTSEND_HALF_DIE_WIDTH_MM
        + SENDCUTSEND_MOUNT_HOLE_TO_BEND_BUFFER_MM
        for x, _z, radius in BASE_ATTACHMENT_HOLES_MM
    )
    x_max = max(x_max, required_x_max_for_mount_holes)
    z_center = 0.5 * (SADDLE_BASE_Z_MIN_MM + SADDLE_BASE_Z_MAX_MM)
    z_min = z_center - (0.5 * BASE_PLATE_Z_WIDTH_MM)
    z_max = z_center + (0.5 * BASE_PLATE_Z_WIDTH_MM)
    y_min = SADDLE_BASE_FACE_Y_MM - PLATE_THICKNESS_MM
    y_max = SADDLE_BASE_FACE_Y_MM
    return_y_max = y_min - U_BRACKET_CLEARANCE_MM
    return_y_min = return_y_max - PLATE_THICKNESS_MM
    side_x_min = x_max - BOOLEAN_OVERLAP_MM
    side_x_max = x_max + SIDE_WALL_THICKNESS_MM
    return_x_min = side_x_min - (RETURN_FACE_LENGTH_RATIO * (side_x_max - x_min))
    return_mount_holes = (
        (
            0.5 * (return_x_min + side_x_min),
            0.5 * (z_min + z_max),
            M8_CLEARANCE_RADIUS_MM,
        ),
    )

    servo_bus_mount_holes_yzr = _servo_bus_mount_holes_yzr(
        y_min=return_y_min,
        y_max=y_max,
        z_min=z_min,
        z_max=z_max,
    )

    return BracketLayout(
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        z_min=z_min,
        z_max=z_max,
        width=x_max - x_min,
        height=z_max - z_min,
        return_width=side_x_max - return_x_min,
        return_x_min=return_x_min,
        return_y_min=return_y_min,
        return_y_max=return_y_max,
        side_x_min=side_x_min,
        side_x_max=side_x_max,
        overall_y_min=return_y_min,
        face_y=y_max,
        attachment_holes=BASE_ATTACHMENT_HOLES_MM,
        return_mount_holes=return_mount_holes,
        servo_bus_mount_holes_yzr=servo_bus_mount_holes_yzr,
    )


def _fillet_plan_corners(
    plate: build123d.Solid,
    *,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
    include_min_x: bool = True,
    include_max_x: bool = True,
) -> build123d.Shape:
    matches: list[build123d.Edge] = []
    for edge in plate.edges():
        if _geom_type_name(edge) != "LINE":
            continue
        bb = edge.bounding_box()
        if abs((bb.max.Y - bb.min.Y) - (y_max - y_min)) > 1e-3:
            continue
        if (bb.max.X - bb.min.X) > 1e-3 or (bb.max.Z - bb.min.Z) > 1e-3:
            continue
        center = edge.center()
        if abs(abs(center.X - ((x_min + x_max) * 0.5)) - ((x_max - x_min) * 0.5)) > 1e-3:
            continue
        if abs(abs(center.Z - ((z_min + z_max) * 0.5)) - ((z_max - z_min) * 0.5)) > 1e-3:
            continue
        at_min_x = abs(center.X - x_min) <= 1e-3
        at_max_x = abs(center.X - x_max) <= 1e-3
        if at_min_x and not include_min_x:
            continue
        if at_max_x and not include_max_x:
            continue
        matches.append(edge)

    expected_count = (2 if include_min_x else 0) + (2 if include_max_x else 0)
    if len(matches) != expected_count:
        raise RuntimeError(f"Expected {expected_count} plan-view corner edges to fillet, found {len(matches)}")

    mk = BRepFilletAPI_MakeFillet(plate.wrapped)
    for edge in matches:
        mk.Add(CORNER_RADIUS_MM, edge.wrapped)
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError("Failed to round U-bracket plate corners")
    return _cast_shape(mk.Shape())


def _cut_y_holes(
    plate: build123d.Shape,
    *,
    holes_xzr: tuple[tuple[float, float, float], ...],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
) -> build123d.Shape:
    hole_depth = (y_max - y_min) + (2.0 * CUT_EXTENSION_MM)
    for x, z, radius in holes_xzr:
        if not (x_min + radius <= x <= x_max - radius):
            raise RuntimeError(f"Hole at x={x:.3f} mm does not fit within the bracket x span")
        if not (z_min + radius <= z <= z_max - radius):
            raise RuntimeError(f"Hole at z={z:.3f} mm does not fit within the bracket z span")
        plate = plate.cut(
            _make_cylinder_along(
                radius=radius,
                height=hole_depth,
                origin=build123d.Vector(x, y_min - CUT_EXTENSION_MM, z),
                direction=build123d.Vector(0.0, 1.0, 0.0),
            )
        )
    return plate


def _cut_x_holes(
    bracket: build123d.Shape,
    *,
    holes_yzr: tuple[tuple[float, float, float], ...],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
) -> build123d.Shape:
    hole_depth = (x_max - x_min) + (2.0 * CUT_EXTENSION_MM)
    for y, z, radius in holes_yzr:
        if not (y_min + radius <= y <= y_max - radius):
            raise RuntimeError(f"Hole at y={y:.3f} mm does not fit within the bracket y span")
        if not (z_min + radius <= z <= z_max - radius):
            raise RuntimeError(f"Hole at z={z:.3f} mm does not fit within the bracket z span")
        bracket = bracket.cut(
            _make_cylinder_along(
                radius=radius,
                height=hole_depth,
                origin=build123d.Vector(x_min - CUT_EXTENSION_MM, y, z),
                direction=build123d.Vector(1.0, 0.0, 0.0),
            )
        )
    return bracket


def _cut_base_plate_mount_features(
    plate: build123d.Shape,
    *,
    holes_xzr: tuple[tuple[float, float, float], ...],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
) -> build123d.Shape:
    return _cut_y_holes(
        plate,
        holes_xzr=holes_xzr,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        z_min=z_min,
        z_max=z_max,
    )


def _as_single_solid(shape: build123d.Shape) -> build123d.Solid:
    solids = list(shape.solids())
    if len(solids) != 1:
        raise RuntimeError(f"Expected a single solid, found {len(solids)}")
    return solids[0]


def _make_lower_plate(layout: BracketLayout) -> build123d.Shape:
    plate = _make_box(
        x_min=layout.x_min,
        x_max=layout.x_max,
        y_min=layout.y_min,
        y_max=layout.y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )
    plate = _fillet_plan_corners(
        plate,
        x_min=layout.x_min,
        x_max=layout.x_max,
        y_min=layout.y_min,
        y_max=layout.y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
        include_min_x=True,
        include_max_x=False,
    )
    plate = _cut_base_plate_mount_features(
        plate,
        holes_xzr=layout.attachment_holes,
        x_min=layout.x_min,
        x_max=layout.x_max,
        y_min=layout.y_min,
        y_max=layout.y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )
    return _as_single_solid(plate)


def _servo_bus_mount_holes_yzr(
    *,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
) -> tuple[tuple[float, float, float], ...]:
    target_center_y = 0.5 * (y_min + y_max)
    target_center_z = 0.5 * (z_min + z_max)
    bus_to_bracket_y_offset = target_center_y - SERVO_BUS_MOUNT_LOCAL_X_CENTER_MM
    bus_to_bracket_z_offset = target_center_z + SERVO_BUS_MOUNT_LOCAL_Y_CENTER_MM

    return tuple(
        (
            bus_x + bus_to_bracket_y_offset,
            bus_to_bracket_z_offset - bus_y,
            SERVO_BUS_SPACER_CLEARANCE_RADIUS_MM,
        )
        for bus_x, bus_y in SERVO_BUS_BOARD_SPACER_CENTERS_XY_MM
    )


def _make_return_plate(layout: BracketLayout) -> build123d.Shape:
    plate = _make_box(
        x_min=layout.return_x_min,
        x_max=layout.side_x_max,
        y_min=layout.return_y_min,
        y_max=layout.return_y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )
    plate = _fillet_plan_corners(
        plate,
        x_min=layout.return_x_min,
        x_max=layout.side_x_max,
        y_min=layout.return_y_min,
        y_max=layout.return_y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
        include_min_x=True,
        include_max_x=False,
    )
    plate = _cut_y_holes(
        plate,
        holes_xzr=layout.return_mount_holes,
        x_min=layout.return_x_min,
        x_max=layout.side_x_max,
        y_min=layout.return_y_min,
        y_max=layout.return_y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )
    return _as_single_solid(plate)


def _make_side_wall(layout: BracketLayout) -> build123d.Shape:
    return _make_box(
        x_min=layout.side_x_min,
        x_max=layout.side_x_max,
        y_min=layout.return_y_min,
        y_max=layout.y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )


def build_bracket() -> build123d.Solid:
    layout = _build_bracket_layout()

    bracket = _make_lower_plate(layout)
    bracket = _as_single_solid(bracket.fuse(_make_side_wall(layout)))
    bracket = _as_single_solid(bracket.fuse(_make_return_plate(layout)))
    bracket = _as_single_solid(
        _cut_x_holes(
            bracket,
            holes_yzr=layout.servo_bus_mount_holes_yzr,
            x_min=layout.side_x_min,
            x_max=layout.side_x_max,
            y_min=layout.return_y_min,
            y_max=layout.y_max,
            z_min=layout.z_min,
            z_max=layout.z_max,
        )
    )

    print(
        f"{DISPLAY_NAME}: "
        f"lower_width={layout.width:.3f} mm, "
        f"return_width={layout.return_width:.3f} mm, "
        f"lower_height={layout.height:.3f} mm, "
        f"plate_thickness={PLATE_THICKNESS_MM:.3f} mm, "
        f"clearance={U_BRACKET_CLEARANCE_MM:.3f} mm, "
        f"overall_y_min={layout.overall_y_min:.4f} mm, "
        f"horn_m3_holes={len(BASE_HORN_CONNECTION_HOLE_SPECS_MM)}, "
        f"m2_mount_holes={len(BASE_SERVO_M2_HOLE_SPECS_MM)}, "
        f"return_mount_holes={len(layout.return_mount_holes)}, "
        f"servo_bus_holes={len(layout.servo_bus_mount_holes_yzr)}, "
        f"flush_y={layout.face_y:.4f} mm"
    )
    return bracket


def _build_flat_pattern(layout: BracketLayout) -> FlatPattern:
    _validate_sendcutsend_bend_rule()

    outside_setback = _sendcutsend_outside_setback_90_mm()
    bend_allowance = _sendcutsend_bend_allowance_90_mm()
    top_flange_outside_length = layout.side_x_max - layout.x_min
    return_flange_outside_length = layout.return_width
    top_flange_flat_length = top_flange_outside_length - outside_setback
    return_flange_flat_length = return_flange_outside_length - outside_setback
    min_flange_outside_length = min(top_flange_outside_length, return_flange_outside_length)
    min_flange_flat_length = min(top_flange_flat_length, return_flange_flat_length)
    if min_flange_outside_length < SENDCUTSEND_MIN_FLANGE_FORMED_MM:
        raise RuntimeError(
            "Flange length is below the configured bend-rule minimum: "
            f"{min_flange_outside_length:.3f} mm < {SENDCUTSEND_MIN_FLANGE_FORMED_MM:.3f} mm"
        )
    if min_flange_flat_length <= 0.0:
        raise RuntimeError(
            "Flat flange length implied by the bend rule is too short: "
            f"{min_flange_flat_length:.3f} mm"
        )

    web_outside_length = layout.y_max - layout.return_y_min
    web_flat_length = web_outside_length - (2.0 * SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM)
    if web_flat_length <= 0.0:
        raise RuntimeError(
            f"Web flat length implied by the bend rule is too short: {web_flat_length:.3f} mm"
        )

    web_flat_start = top_flange_flat_length + bend_allowance
    web_flat_end = web_flat_start + web_flat_length
    total_length = web_flat_end + bend_allowance + return_flange_flat_length
    width = layout.z_max - layout.z_min
    bend_line_positions = (
        top_flange_flat_length + (0.5 * bend_allowance),
        web_flat_end + (0.5 * bend_allowance),
    )

    top_tangent_y = layout.y_max - SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM
    bottom_tangent_y = layout.return_y_min + SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM

    feature_extents: list[tuple[float, float]] = []
    cut_circles: list[tuple[float, float, float]] = []
    for x, z, radius in layout.attachment_holes:
        circle = (x - layout.x_min, z - layout.z_min, radius)
        cut_circles.append(circle)
        feature_extents.append((circle[0], radius))
    for y, z, radius in layout.servo_bus_mount_holes_yzr:
        circle = (web_flat_start + (top_tangent_y - y), z - layout.z_min, radius)
        cut_circles.append(circle)
        feature_extents.append((circle[0], radius))
    for x, z, radius in layout.return_mount_holes:
        circle = (total_length - (x - layout.return_x_min), z - layout.z_min, radius)
        cut_circles.append(circle)
        feature_extents.append((circle[0], radius))

    min_feature_to_bend = min(
        min(abs(center_u - bend_u) - feature_radius for bend_u in bend_line_positions)
        for center_u, feature_radius in feature_extents
    )

    return FlatPattern(
        total_length_mm=total_length,
        width_mm=width,
        top_flange_flat_length_mm=top_flange_flat_length,
        return_flange_flat_length_mm=return_flange_flat_length,
        web_flat_start_mm=web_flat_start,
        web_flat_end_mm=web_flat_end,
        bend_line_positions_mm=bend_line_positions,
        cut_circles=tuple(cut_circles),
        rounded_corner_radius_mm=CORNER_RADIUS_MM,
        min_feature_to_bend_mm=min_feature_to_bend,
    )


def _add_rounded_rect_outline(
    msp: ezdxf.layouts.Modelspace,
    *,
    length: float,
    width: float,
    radius: float,
) -> None:
    if radius <= 0.0:
        raise RuntimeError(f"Corner radius must be positive, got {radius:.3f} mm")
    if width <= (2.0 * radius):
        raise RuntimeError(
            "Corner radius is too large for the base clamp flat-pattern width: "
            f"{radius:.3f} mm"
        )
    if length <= (2.0 * radius):
        raise RuntimeError(
            "Corner radius is too large for the base clamp flat-pattern length: "
            f"{radius:.3f} mm"
        )

    msp.add_line((radius, 0.0), (length - radius, 0.0), dxfattribs={"layer": "CUT"})
    msp.add_arc(
        center=(length - radius, radius),
        radius=radius,
        start_angle=-90.0,
        end_angle=0.0,
        dxfattribs={"layer": "CUT"},
    )
    msp.add_line((length, radius), (length, width - radius), dxfattribs={"layer": "CUT"})
    msp.add_arc(
        center=(length - radius, width - radius),
        radius=radius,
        start_angle=0.0,
        end_angle=90.0,
        dxfattribs={"layer": "CUT"},
    )
    msp.add_line((length - radius, width), (radius, width), dxfattribs={"layer": "CUT"})
    msp.add_arc(
        center=(radius, width - radius),
        radius=radius,
        start_angle=90.0,
        end_angle=180.0,
        dxfattribs={"layer": "CUT"},
    )
    msp.add_line((0.0, width - radius), (0.0, radius), dxfattribs={"layer": "CUT"})
    msp.add_arc(
        center=(radius, radius),
        radius=radius,
        start_angle=180.0,
        end_angle=270.0,
        dxfattribs={"layer": "CUT"},
    )


def build_dxf() -> ezdxf.document.Drawing:
    layout = _build_bracket_layout()
    flat_pattern = _build_flat_pattern(layout)

    doc = ezdxf.new("R2010", setup=True)
    doc.units = DXF_UNIT_MM
    if "CUT" not in doc.layers:
        doc.layers.add("CUT")
    if "BEND" not in doc.layers:
        doc.layers.add("BEND", linetype="DASHED")

    msp = doc.modelspace()
    _add_rounded_rect_outline(
        msp,
        length=flat_pattern.total_length_mm,
        width=flat_pattern.width_mm,
        radius=flat_pattern.rounded_corner_radius_mm,
    )
    for center_u, center_v, radius in flat_pattern.cut_circles:
        msp.add_circle((center_u, center_v), radius, dxfattribs={"layer": "CUT"})
    for bend_line_u in flat_pattern.bend_line_positions_mm:
        msp.add_line(
            (bend_line_u, 0.0),
            (bend_line_u, flat_pattern.width_mm),
            dxfattribs={"layer": "BEND", "linetype": "DASHED"},
        )

    print(
        f"{DISPLAY_NAME} DXF: "
        f"stock_thickness={PLATE_THICKNESS_MM:.3f} mm, "
        f"flat_length={flat_pattern.total_length_mm:.3f} mm, "
        f"flat_width={flat_pattern.width_mm:.3f} mm, "
        f"bend_lines=({flat_pattern.bend_line_positions_mm[0]:.3f}, {flat_pattern.bend_line_positions_mm[1]:.3f}) mm, "
        f"cut_circles={len(flat_pattern.cut_circles)}"
    )
    print(
        "DXF bend checks "
        f"top_flange_flat={flat_pattern.top_flange_flat_length_mm:.3f} mm, "
        f"return_flange_flat={flat_pattern.return_flange_flat_length_mm:.3f} mm, "
        f"feature_to_bend_min={flat_pattern.min_feature_to_bend_mm:.3f} mm, "
        f"feature_to_bend_required={SENDCUTSEND_HALF_DIE_WIDTH_MM:.3f} mm"
    )
    if flat_pattern.min_feature_to_bend_mm < SENDCUTSEND_HALF_DIE_WIDTH_MM:
        print(
            "Warning: flat-pattern features enter the bend die region by "
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


def gen() -> None:
    gen_step()
    gen_dxf()


if __name__ == "__main__":
    gen()
