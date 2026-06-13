#!/usr/bin/env python3
"""
Generate the localized end mount.

The part is authored in the imported STS3250 local frame so assemblies can place
it with the same transform as the reference servo geometry.

Usage:
  python STEP/servo_end_mount.py
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Keep CAD library caches inside the workspace to avoid permission issues.
REPO_ROOT = Path(__file__).resolve().parents[1]
_CACHE_HOME = REPO_ROOT / ".cache"
_CACHE_HOME.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_HOME))
os.environ.setdefault("EZDXF_CACHE_HOME", str(_CACHE_HOME / "ezdxf"))

import ezdxf
import build123d
from ezdxf.units import MM as DXF_UNIT_MM

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_common.materials import GRAY_ALUMINUM_COLOR
from robot_common.step_import import import_as_shape


CAD_DIR = Path(__file__).resolve().parent
PART_NAME = Path(__file__).stem
DISPLAY_NAME = "Servo End Mount"

SERVO_STEP = REPO_ROOT / "STEP" / "imports" / "sts3250.step"


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if parsed <= 0.0:
        raise ValueError(f"{name} must be positive, got {parsed!r}")
    return parsed


SENDCUTSEND_MIN_BEND_LINE_EDGE_CLEARANCE_MM = 25.4 * 0.255
SENDCUTSEND_SHEET_THICKNESS_MM = _env_float("TOM_V2_SHEET_THICKNESS_MM", 25.4 * 0.080)
CENTER_BRIDGE_BEND_LINE_EDGE_BUFFER_MM = 25.4 * 0.010
CENTER_BRIDGE_BEND_LINE_EDGE_TARGET_MM = (
    SENDCUTSEND_MIN_BEND_LINE_EDGE_CLEARANCE_MM
    + CENTER_BRIDGE_BEND_LINE_EDGE_BUFFER_MM
)
BRACKET_FACE_CLEARANCE_MM = CENTER_BRIDGE_BEND_LINE_EDGE_TARGET_MM - SENDCUTSEND_SHEET_THICKNESS_MM
SENDCUTSEND_REFERENCE_SHEET_THICKNESS_MM = 25.4 * 0.080
SENDCUTSEND_DIE_REGION_RULE_SCALE = SENDCUTSEND_SHEET_THICKNESS_MM / SENDCUTSEND_REFERENCE_SHEET_THICKNESS_MM
SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM = 25.4 * 0.038
SENDCUTSEND_K_FACTOR = 0.48
SENDCUTSEND_BEND_DEDUCTION_90_MM = (2.0 * (SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + SENDCUTSEND_SHEET_THICKNESS_MM)) - (
    (math.pi * 0.5)
    * (SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + (SENDCUTSEND_K_FACTOR * SENDCUTSEND_SHEET_THICKNESS_MM))
)
SENDCUTSEND_DIE_WIDTH_MM = 25.4 * 0.472
SENDCUTSEND_HALF_DIE_WIDTH_MM = (0.5 * SENDCUTSEND_DIE_WIDTH_MM) * SENDCUTSEND_DIE_REGION_RULE_SCALE
SENDCUTSEND_MIN_FLANGE_FLAT_MM = SENDCUTSEND_MIN_BEND_LINE_EDGE_CLEARANCE_MM
SENDCUTSEND_MIN_FLANGE_FORMED_MM = (
    SENDCUTSEND_MIN_FLANGE_FLAT_MM
    + SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM
    + SENDCUTSEND_SHEET_THICKNESS_MM
)
SENDCUTSEND_BEND_RELIEF_DEPTH_MM = 25.4 * 0.150
SENDCUTSEND_MIN_CORNER_RELIEF_DISTANCE_MM = 25.4 * 0.037
SENDCUTSEND_MIN_ANODIZE_SHORTEST_DIM_MM = 25.4
BEND_DIE_REGION_TOLERANCE_MM = 0.1
# The theoretical outer radius is inner radius + thickness, but the split tab
# geometry cannot accept the full value as a direct solid fillet. Clamp the
# modeled exterior bend to a stable radius that still reads as a formed bend.
BEND_OUTER_RADIUS_MM = min(2.0, SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + SENDCUTSEND_SHEET_THICKNESS_MM)
FLANGE_FREE_CORNER_RADIUS_MM = 1.0
FLANGE_FREE_CORNER_SAMPLE_COUNT = 8
MOUNT_PLANE_CLEARANCE_MM = 0.25
MOUNT_HOLE_EDGE_X_WEB_MM = 1.55
MOUNT_HOLE_OUTBOARD_Z_WEB_MM = 2.15
MOUNT_HOLE_INBOARD_Z_WEB_MM = 2.15
MOUNT_HOLE_TRIMMED_OUTBOARD_Z_WEB_WARN_MM = 1.0
CUT_EXTENSION_MM = 2.0
MAX_FACE_TARGET_ERROR_MM = 1.0
FACE_TARGET_CENTERS_MM: dict[str, tuple[float, float, float]] = {
    "end_face": (9.61, -7.65, 0.0),
    "end_wrap_neg": (8.843, -7.65, -11.593),
    "end_wrap_pos": (8.843, -7.65, 11.593),
    "upper_top_neg": (-6.561, 6.4, -9.484),
    "upper_top_pos": (-6.561, 6.4, 9.484),
    "main_bottom": (-0.881, -25.6, 0.0),
}
@dataclass(frozen=True)
class BracketLayout:
    z_min: float
    z_max: float
    top_holes: tuple[tuple[build123d.Vector, float], ...]
    bottom_holes: tuple[tuple[build123d.Vector, float], ...]
    top_tab_x_min: float
    bottom_tab_x_min: float
    bracket_face_inner_x: float
    bracket_face_outer_x: float
    top_tab_y_min: float
    top_tab_y_max: float
    bottom_tab_y_min: float
    bottom_tab_y_max: float
    tab_bands: tuple[tuple[float, float], ...]
    bend_bands: tuple[tuple[float, float], ...]
    center_bridge_x_min: float | None
    front_face_center_y: float
    outer_flush_gap: float


@dataclass(frozen=True)
class FlatPattern:
    total_length_mm: float
    width_mm: float
    top_flat_length_mm: float
    web_flat_start_mm: float
    web_flat_end_mm: float
    bottom_flat_start_mm: float
    bend_line_positions_mm: tuple[float, float]
    bend_line_segments_mm: tuple[tuple[float, float, float], ...]
    cut_circles: tuple[tuple[float, float, float], ...]
    cut_segments: tuple[tuple[str, tuple[tuple[float, float], ...]], ...]


def _geom_type_name(shape: build123d.Shape) -> str:
    geom_type = shape.geom_type
    return geom_type.name if hasattr(geom_type, "name") else str(geom_type)


def _face_from_wire(
    exterior: build123d.Wire,
    interior_wires: list[build123d.Wire] | None = None,
) -> build123d.Face:
    holes = list(interior_wires or [])
    return build123d.Face(exterior, holes) if holes else build123d.Face(exterior)


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


def _vector_to_tuple(vector: build123d.Vector) -> tuple[float, float, float]:
    return (vector.X, vector.Y, vector.Z)


def _make_box(*, x_min: float, x_max: float, y_min: float, y_max: float, z_min: float, z_max: float) -> build123d.Solid:
    return build123d.Solid.make_box(
        x_max - x_min,
        y_max - y_min,
        z_max - z_min,
        plane=build123d.Plane(origin=build123d.Vector(x_min, y_min, z_min)),
    )


def _assemble_wire(edges: list[build123d.Edge]) -> build123d.Wire:
    wires = list(build123d.Wire.combine(edges))
    if len(wires) != 1:
        raise RuntimeError(f"Expected a single assembled wire, found {len(wires)}")
    return wires[0]


def _arc_angles_xz(
    *,
    center_x: float,
    center_z: float,
    start_point: tuple[float, float],
    end_point: tuple[float, float],
) -> tuple[float, float, build123d.AngularDirection]:
    start_angle = math.degrees(math.atan2(start_point[1] - center_z, start_point[0] - center_x))
    end_angle = math.degrees(math.atan2(end_point[1] - center_z, end_point[0] - center_x))
    ccw_span = (end_angle - start_angle) % 360.0
    if ccw_span <= 180.0:
        return start_angle, end_angle, build123d.AngularDirection.COUNTER_CLOCKWISE
    return start_angle, end_angle, build123d.AngularDirection.CLOCKWISE


def _make_arc_edge_xz(
    *,
    center_x: float,
    center_z: float,
    radius: float,
    start_point: tuple[float, float],
    end_point: tuple[float, float],
) -> build123d.Edge:
    start_angle, end_angle, angular_direction = _arc_angles_xz(
        center_x=center_x,
        center_z=center_z,
        start_point=start_point,
        end_point=end_point,
    )
    return build123d.Edge.make_circle(
        radius,
        plane=build123d.Plane(origin=(center_x, 0.0, center_z), x_dir=(1.0, 0.0, 0.0), z_dir=(0.0, -1.0, 0.0)),
        start_angle=start_angle,
        end_angle=end_angle,
        angular_direction=angular_direction,
    )


def _make_wire_prism_y(
    *,
    edges_xz: list[build123d.Edge],
    y_min: float,
    y_max: float,
) -> build123d.Solid:
    face = _face_from_wire(_assemble_wire(edges_xz))
    solid = build123d.Solid.extrude(face, build123d.Vector(0.0, y_max - y_min, 0.0))
    return solid.translate((0.0, y_min, 0.0))


def _make_rounded_flange_tab(
    *,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
    radius: float,
) -> build123d.Shape:
    radius = min(radius, 0.5 * (z_max - z_min), x_max - x_min)
    if radius <= 1e-6:
        return _make_box(x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, z_min=z_min, z_max=z_max)

    lower_tangent = (x_min + radius, z_min)
    right_lower = (x_max, z_min)
    right_upper = (x_max, z_max)
    upper_tangent = (x_min + radius, z_max)
    left_upper = (x_min, z_max - radius)
    left_lower = (x_min, z_min + radius)

    return _make_wire_prism_y(
        edges_xz=[
            build123d.Edge.make_line((lower_tangent[0], 0.0, lower_tangent[1]), (right_lower[0], 0.0, right_lower[1])),
            build123d.Edge.make_line((right_lower[0], 0.0, right_lower[1]), (right_upper[0], 0.0, right_upper[1])),
            build123d.Edge.make_line((right_upper[0], 0.0, right_upper[1]), (upper_tangent[0], 0.0, upper_tangent[1])),
            _make_arc_edge_xz(
                center_x=x_min + radius,
                center_z=z_max - radius,
                radius=radius,
                start_point=upper_tangent,
                end_point=left_upper,
            ),
            build123d.Edge.make_line((left_upper[0], 0.0, left_upper[1]), (left_lower[0], 0.0, left_lower[1])),
            _make_arc_edge_xz(
                center_x=x_min + radius,
                center_z=z_min + radius,
                radius=radius,
                start_point=left_lower,
                end_point=lower_tangent,
            ),
        ],
        y_min=y_min,
        y_max=y_max,
    )


def _find_face_near(shape: build123d.Shape, target: tuple[float, float, float], *, geom_type: str | None = None) -> build123d.Face:
    target_vec = build123d.Vector(*target)
    best: tuple[float, build123d.Face] | None = None
    for face in shape.faces():
        if geom_type is not None and _geom_type_name(face) != geom_type:
            continue
        dist = face.center().sub(target_vec).length
        if best is None or dist < best[0]:
            best = (dist, face)

    if best is None:
        raise RuntimeError(f"No planar face found near {target}")
    if best[0] > MAX_FACE_TARGET_ERROR_MM:
        raise RuntimeError(
            f"Resolved face near {target} was {best[0]:.3f} mm away, which exceeds the tolerance"
        )
    return best[1]


def _wire_area(wire: build123d.Wire) -> float:
    return abs(_face_from_wire(wire).area)


def _outer_wire_and_inner_wires(face: build123d.Face) -> tuple[build123d.Wire, list[build123d.Wire]]:
    wires = list(face.wires())
    if not wires:
        raise RuntimeError("Face has no wires")

    outer_wire = max(wires, key=_wire_area)
    inner_wires = [wire for wire in wires if not wire.is_same(outer_wire)]
    return outer_wire, inner_wires


def _extract_inner_hole_profiles(face: build123d.Face) -> list[tuple[build123d.Vector, float]]:
    _outer_wire, inner_wires = _outer_wire_and_inner_wires(face)
    profiles: list[tuple[build123d.Vector, float]] = []
    for wire in inner_wires:
        circle_edges = [edge for edge in wire.edges() if _geom_type_name(edge) == "CIRCLE"]
        if not circle_edges:
            continue
        center = _face_from_wire(wire).center()
        radius = max(edge.radius for edge in circle_edges)
        profiles.append((center, radius))
    return profiles


def _select_near_end_holes(
    profiles: list[tuple[build123d.Vector, float]],
    *,
    count: int,
) -> list[tuple[build123d.Vector, float]]:
    if len(profiles) < count:
        raise RuntimeError(f"Expected at least {count} mount holes, found {len(profiles)}")

    selected = sorted(profiles, key=lambda item: (item[0].X, abs(item[0].Z)), reverse=True)[:count]
    selected.sort(key=lambda item: item[0].Z)
    return selected


def _cut_mount_holes(
    body: build123d.Shape,
    *,
    profiles: list[tuple[build123d.Vector, float]],
    start_y: float,
    direction_y: float,
) -> build123d.Shape:
    hole_depth = SENDCUTSEND_SHEET_THICKNESS_MM + (2.0 * CUT_EXTENSION_MM)
    for center, radius in profiles:
        cutter = _make_cylinder_along(
            radius=radius,
            height=hole_depth,
            origin=build123d.Vector(center.X, start_y, center.Z),
            direction=build123d.Vector(0.0, direction_y, 0.0),
        )
        body = body.cut(cutter)
    return body


def _apply_bends_and_end_rounds(
    bracket: build123d.Shape,
    *,
    bracket_face_inner_x: float,
    bracket_face_outer_x: float,
    top_tab_x_min: float,
    bottom_tab_x_min: float,
    top_tab_y_min: float,
    top_tab_y_max: float,
    bottom_tab_y_min: float,
    bottom_tab_y_max: float,
    bend_bands: tuple[tuple[float, float], ...],
) -> build123d.Shape:
    return bracket


def _min_mount_hole_distance_to_bend_line(
    profiles: list[tuple[build123d.Vector, float]],
    *,
    bend_line_x: float,
) -> float:
    return min(abs(center.X - bend_line_x) for center, _radius in profiles)


def _merge_z_bands(
    bands: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    *,
    tolerance: float = 1e-6,
) -> tuple[tuple[float, float], ...]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(bands, key=lambda band: band[0]):
        if end - start <= tolerance:
            continue
        if not merged or start > merged[-1][1] + tolerance:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return tuple(merged)


def _build_layout(
    servo_shape: build123d.Shape,
    *,
    include_top_center_bridge: bool = True,
    validate_bend_rule: bool = True,
) -> BracketLayout:
    faces = {
        "end_face": _find_face_near(servo_shape, FACE_TARGET_CENTERS_MM["end_face"], geom_type="PLANE"),
        "end_wrap_neg": _find_face_near(servo_shape, FACE_TARGET_CENTERS_MM["end_wrap_neg"]),
        "end_wrap_pos": _find_face_near(servo_shape, FACE_TARGET_CENTERS_MM["end_wrap_pos"]),
        "upper_top_neg": _find_face_near(servo_shape, FACE_TARGET_CENTERS_MM["upper_top_neg"], geom_type="PLANE"),
        "upper_top_pos": _find_face_near(servo_shape, FACE_TARGET_CENTERS_MM["upper_top_pos"], geom_type="PLANE"),
        "main_bottom": _find_face_near(servo_shape, FACE_TARGET_CENTERS_MM["main_bottom"], geom_type="PLANE"),
    }

    end_face_bb = faces["end_face"].bounding_box()
    end_wrap_neg_bb = faces["end_wrap_neg"].bounding_box()
    end_wrap_pos_bb = faces["end_wrap_pos"].bounding_box()
    z_min = min(
        end_face_bb.min.Z,
        end_wrap_neg_bb.min.Z,
        end_wrap_pos_bb.min.Z,
    )
    z_max = max(
        end_face_bb.max.Z,
        end_wrap_neg_bb.max.Z,
        end_wrap_pos_bb.max.Z,
    )

    top_holes = _select_near_end_holes(_extract_inner_hole_profiles(faces["upper_top_neg"]), count=1)
    top_holes += _select_near_end_holes(_extract_inner_hole_profiles(faces["upper_top_pos"]), count=1)
    top_holes.sort(key=lambda item: item[0].Z)

    bottom_holes = _select_near_end_holes(_extract_inner_hole_profiles(faces["main_bottom"]), count=2)

    top_mount_y = sum(center.Y for center, _radius in top_holes) / len(top_holes)
    bottom_mount_y = sum(center.Y for center, _radius in bottom_holes) / len(bottom_holes)
    top_tab_y_min = top_mount_y + MOUNT_PLANE_CLEARANCE_MM
    top_tab_y_max = top_tab_y_min + SENDCUTSEND_SHEET_THICKNESS_MM
    bottom_tab_y_max = bottom_mount_y - MOUNT_PLANE_CLEARANCE_MM
    bottom_tab_y_min = bottom_tab_y_max - SENDCUTSEND_SHEET_THICKNESS_MM

    side_profiles = {
        "neg": [top_holes[0], bottom_holes[0]],
        "pos": [top_holes[1], bottom_holes[1]],
    }
    tab_bands: list[tuple[float, float]] = []
    for top_profile, bottom_profile in side_profiles.values():
        profiles = (top_profile, bottom_profile)
        if sum(center.Z for center, _radius in profiles) < 0.0:
            z_band_min = min(center.Z - radius - MOUNT_HOLE_OUTBOARD_Z_WEB_MM for center, radius in profiles)
            z_band_max = max(center.Z + radius + MOUNT_HOLE_INBOARD_Z_WEB_MM for center, radius in profiles)
        else:
            z_band_min = min(center.Z - radius - MOUNT_HOLE_INBOARD_Z_WEB_MM for center, radius in profiles)
            z_band_max = max(center.Z + radius + MOUNT_HOLE_OUTBOARD_Z_WEB_MM for center, radius in profiles)
        z_band_min = max(z_band_min, z_min)
        z_band_max = min(z_band_max, z_max)
        tab_bands.append((z_band_min, z_band_max))

    sorted_tab_bands = sorted(tab_bands, key=lambda band: band[0])
    bend_bands = _merge_z_bands(sorted_tab_bands)

    top_tab_x_min = min(center.X - radius - MOUNT_HOLE_EDGE_X_WEB_MM for center, radius in top_holes)
    bottom_tab_x_min = min(center.X - radius - MOUNT_HOLE_EDGE_X_WEB_MM for center, radius in bottom_holes)
    bracket_face_clearance = BRACKET_FACE_CLEARANCE_MM
    bracket_face_inner_x = end_face_bb.max.X + bracket_face_clearance
    bracket_face_outer_x = bracket_face_inner_x + SENDCUTSEND_SHEET_THICKNESS_MM
    center_bridge_x_min: float | None = None
    if include_top_center_bridge and len(sorted_tab_bands) >= 2:
        center_gap_z_min = sorted_tab_bands[0][1]
        center_gap_z_max = sorted_tab_bands[-1][0]
        if center_gap_z_max - center_gap_z_min > 1e-3:
            candidate_center_bridge_x_min = max(
                bracket_face_outer_x - CENTER_BRIDGE_BEND_LINE_EDGE_TARGET_MM,
                top_tab_x_min,
            )
            if candidate_center_bridge_x_min < bracket_face_outer_x - 1e-3:
                center_bridge_x_min = candidate_center_bridge_x_min
                bend_bands = _merge_z_bands((*sorted_tab_bands, (center_gap_z_min, center_gap_z_max)))

    min_hole_to_bend_line = min(
        _min_mount_hole_distance_to_bend_line(top_holes, bend_line_x=bracket_face_inner_x),
        _min_mount_hole_distance_to_bend_line(bottom_holes, bend_line_x=bracket_face_inner_x),
    )
    if (
        validate_bend_rule
        and min_hole_to_bend_line + BEND_DIE_REGION_TOLERANCE_MM < SENDCUTSEND_HALF_DIE_WIDTH_MM
    ):
        raise RuntimeError(
            "Mount holes are too close to the bend line for the configured sheet-metal bend rule: "
            f"{min_hole_to_bend_line:.3f} mm < {SENDCUTSEND_HALF_DIE_WIDTH_MM:.3f} mm"
        )

    front_face_center_y = 0.5 * (bottom_tab_y_min + top_tab_y_max)
    outer_flush_gap = min(
        min(
            z_max - (center.Z + radius) if center.Z >= 0.0 else (center.Z - radius) - z_min
            for center, radius in top_holes
        ),
        min(
            z_max - (center.Z + radius) if center.Z >= 0.0 else (center.Z - radius) - z_min
            for center, radius in bottom_holes
        ),
    )
    if outer_flush_gap <= 0.0:
        raise RuntimeError(f"Trimmed mount width cuts into a mounting hole: outboard web {outer_flush_gap:.3f} mm")

    return BracketLayout(
        z_min=z_min,
        z_max=z_max,
        top_holes=tuple(top_holes),
        bottom_holes=tuple(bottom_holes),
        top_tab_x_min=top_tab_x_min,
        bottom_tab_x_min=bottom_tab_x_min,
        bracket_face_inner_x=bracket_face_inner_x,
        bracket_face_outer_x=bracket_face_outer_x,
        top_tab_y_min=top_tab_y_min,
        top_tab_y_max=top_tab_y_max,
        bottom_tab_y_min=bottom_tab_y_min,
        bottom_tab_y_max=bottom_tab_y_max,
        tab_bands=tuple(sorted_tab_bands),
        bend_bands=bend_bands,
        center_bridge_x_min=center_bridge_x_min,
        front_face_center_y=front_face_center_y,
        outer_flush_gap=outer_flush_gap,
    )


def build_bracket(
    servo_shape: build123d.Shape,
    *,
    include_top_center_bridge: bool = True,
    validate_bend_rule: bool = True,
) -> tuple[build123d.Shape, BracketLayout]:
    layout = _build_layout(
        servo_shape,
        include_top_center_bridge=include_top_center_bridge,
        validate_bend_rule=validate_bend_rule,
    )

    bracket: build123d.Shape = _make_box(
        x_min=layout.bracket_face_inner_x,
        x_max=layout.bracket_face_outer_x,
        y_min=layout.bottom_tab_y_min,
        y_max=layout.top_tab_y_max,
        z_min=layout.z_min,
        z_max=layout.z_max,
    )

    for z_band_min, z_band_max in layout.tab_bands:
        bracket = bracket.fuse(
            _make_rounded_flange_tab(
                x_min=layout.top_tab_x_min,
                x_max=layout.bracket_face_outer_x,
                y_min=layout.top_tab_y_min,
                y_max=layout.top_tab_y_max,
                z_min=z_band_min,
                z_max=z_band_max,
                radius=FLANGE_FREE_CORNER_RADIUS_MM,
            )
        )

    if layout.center_bridge_x_min is not None and len(layout.tab_bands) >= 2:
        center_gap_z_min = layout.tab_bands[0][1]
        center_gap_z_max = layout.tab_bands[-1][0]
        bracket = bracket.fuse(
            _make_box(
                x_min=layout.center_bridge_x_min,
                x_max=layout.bracket_face_outer_x,
                y_min=layout.top_tab_y_min,
                y_max=layout.top_tab_y_max,
                z_min=center_gap_z_min,
                z_max=center_gap_z_max,
            )
        )

    bracket = bracket.fuse(
        _make_rounded_flange_tab(
            x_min=layout.bottom_tab_x_min,
            x_max=layout.bracket_face_outer_x,
            y_min=layout.bottom_tab_y_min,
            y_max=layout.bottom_tab_y_max,
            z_min=layout.z_min,
            z_max=layout.z_max,
            radius=FLANGE_FREE_CORNER_RADIUS_MM,
        )
    )

    bracket = _apply_bends_and_end_rounds(
        bracket,
        bracket_face_inner_x=layout.bracket_face_inner_x,
        bracket_face_outer_x=layout.bracket_face_outer_x,
        top_tab_x_min=layout.top_tab_x_min,
        bottom_tab_x_min=layout.bottom_tab_x_min,
        top_tab_y_min=layout.top_tab_y_min,
        top_tab_y_max=layout.top_tab_y_max,
        bottom_tab_y_min=layout.bottom_tab_y_min,
        bottom_tab_y_max=layout.bottom_tab_y_max,
        bend_bands=layout.bend_bands,
    )

    bracket = _cut_mount_holes(
        bracket,
        profiles=list(layout.top_holes),
        start_y=layout.top_tab_y_max + CUT_EXTENSION_MM,
        direction_y=-1.0,
    )
    bracket = _cut_mount_holes(
        bracket,
        profiles=list(layout.bottom_holes),
        start_y=layout.bottom_tab_y_min - CUT_EXTENSION_MM,
        direction_y=1.0,
    )

    return bracket, layout


def _sendcutsend_outside_setback_90_mm() -> float:
    return SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + SENDCUTSEND_SHEET_THICKNESS_MM


def _sendcutsend_bend_allowance_90_mm() -> float:
    return (math.pi * 0.5) * (
        SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM + (SENDCUTSEND_K_FACTOR * SENDCUTSEND_SHEET_THICKNESS_MM)
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


def _min_flange_outside_length(layout: BracketLayout) -> float:
    return min(
        layout.bracket_face_outer_x - layout.top_tab_x_min,
        layout.bracket_face_outer_x - layout.bottom_tab_x_min,
    )


def _supported_bend_fraction(layout: BracketLayout) -> float:
    bend_width = layout.z_max - layout.z_min
    if bend_width <= 0.0:
        return 0.0
    supported_width = sum(z_band_max - z_band_min for z_band_min, z_band_max in layout.bend_bands)
    return supported_width / bend_width


def _min_bend_segment_length(layout: BracketLayout) -> float:
    return min(z_band_max - z_band_min for z_band_min, z_band_max in layout.bend_bands)


def _center_bridge_z_range(layout: BracketLayout) -> tuple[float, float] | None:
    if layout.center_bridge_x_min is None or len(layout.tab_bands) < 2:
        return None

    z_min = layout.tab_bands[0][1]
    z_max = layout.tab_bands[-1][0]
    if z_max - z_min <= 1e-6:
        return None
    return z_min, z_max


def _center_bridge_formed_length(layout: BracketLayout) -> float | None:
    if layout.center_bridge_x_min is None:
        return None
    return layout.bracket_face_inner_x - layout.center_bridge_x_min


def _center_bridge_bend_edge_distance(layout: BracketLayout) -> float | None:
    if layout.center_bridge_x_min is None:
        return None
    return layout.bracket_face_outer_x - layout.center_bridge_x_min


def _center_bridge_flat_length(layout: BracketLayout, flat_pattern: FlatPattern) -> float | None:
    formed_length = _center_bridge_formed_length(layout)
    if formed_length is None:
        return None

    # Keep the flat-pattern edge at least the required distance from the drawn
    # bend line while preserving the formed center-bridge length in the 3D part.
    bend_line_clearance_flat_length = (
        CENTER_BRIDGE_BEND_LINE_EDGE_TARGET_MM
        - (flat_pattern.bend_line_positions_mm[0] - flat_pattern.top_flat_length_mm)
    )
    return max(formed_length, bend_line_clearance_flat_length)


def _center_bridge_flat_bend_line_to_edge_distance(layout: BracketLayout, flat_pattern: FlatPattern) -> float | None:
    flat_length = _center_bridge_flat_length(layout, flat_pattern)
    if flat_length is None:
        return None
    return flat_length + (flat_pattern.bend_line_positions_mm[0] - flat_pattern.top_flat_length_mm)


def _build_flat_pattern(
    layout: BracketLayout,
) -> FlatPattern:
    _validate_sendcutsend_bend_rule()

    outside_setback = _sendcutsend_outside_setback_90_mm()
    bend_allowance = _sendcutsend_bend_allowance_90_mm()
    top_outside_length = layout.bracket_face_outer_x - layout.top_tab_x_min
    bottom_outside_length = layout.bracket_face_outer_x - layout.bottom_tab_x_min
    top_flat_length = top_outside_length - outside_setback
    bottom_flat_length = bottom_outside_length - outside_setback
    if top_flat_length <= 0.0 or bottom_flat_length <= 0.0:
        raise RuntimeError(
            "Flat flange length implied by the bend rule is too short: "
            f"top={top_flat_length:.3f} mm, bottom={bottom_flat_length:.3f} mm"
        )
    flange_outside_min = _min_flange_outside_length(layout)
    flange_flat_min = flange_outside_min - outside_setback
    center_bridge_bend_edge_distance = _center_bridge_bend_edge_distance(layout)
    if (
        center_bridge_bend_edge_distance is not None
        and center_bridge_bend_edge_distance < CENTER_BRIDGE_BEND_LINE_EDGE_TARGET_MM
    ):
        raise RuntimeError(
            "Top center bridge bend-edge distance is below the configured clearance target: "
            f"{center_bridge_bend_edge_distance:.3f} mm < {CENTER_BRIDGE_BEND_LINE_EDGE_TARGET_MM:.3f} mm"
        )
    if flange_outside_min < SENDCUTSEND_MIN_FLANGE_FORMED_MM:
        raise RuntimeError(
            "Formed flange length is below the configured bend-rule minimum: "
            f"{flange_outside_min:.3f} mm < {SENDCUTSEND_MIN_FLANGE_FORMED_MM:.3f} mm"
        )
    if flange_flat_min < SENDCUTSEND_MIN_FLANGE_FLAT_MM:
        raise RuntimeError(
            "Flat flange length is below the configured bend-rule minimum: "
            f"{flange_flat_min:.3f} mm < {SENDCUTSEND_MIN_FLANGE_FLAT_MM:.3f} mm"
        )

    top_web_tangent_y = layout.top_tab_y_min - SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM
    bottom_web_tangent_y = layout.bottom_tab_y_max + SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM
    web_flat_length = top_web_tangent_y - bottom_web_tangent_y
    if web_flat_length <= 0.0:
        raise RuntimeError(f"Web flat length implied by the bend rule is too short: {web_flat_length:.3f} mm")

    web_flat_start = top_flat_length + bend_allowance
    web_flat_end = web_flat_start + web_flat_length
    bottom_flat_start = web_flat_end + bend_allowance
    total_length = bottom_flat_start + bottom_flat_length
    width = layout.z_max - layout.z_min
    top_bend_line_x = top_flat_length + (0.5 * bend_allowance)
    bottom_bend_line_x = web_flat_end + (0.5 * bend_allowance)
    bend_line_segments = (
        (top_bend_line_x, 0.0, width),
        (bottom_bend_line_x, 0.0, width),
    )

    cut_circles: list[tuple[float, float, float]] = []
    for center, radius in layout.top_holes:
        cut_circles.append(
            (
                center.X - layout.top_tab_x_min,
                center.Z - layout.z_min,
                radius,
            )
        )
    for center, radius in layout.bottom_holes:
        cut_circles.append(
            (
                total_length - (center.X - layout.bottom_tab_x_min),
                center.Z - layout.z_min,
                radius,
            )
        )
    return FlatPattern(
        total_length_mm=total_length,
        width_mm=width,
        top_flat_length_mm=top_flat_length,
        web_flat_start_mm=web_flat_start,
        web_flat_end_mm=web_flat_end,
        bottom_flat_start_mm=bottom_flat_start,
        bend_line_positions_mm=(top_bend_line_x, bottom_bend_line_x),
        bend_line_segments_mm=bend_line_segments,
        cut_circles=tuple(cut_circles),
        cut_segments=(),
    )


def _flat_rectangles(layout: BracketLayout, flat_pattern: FlatPattern) -> tuple[tuple[float, float, float, float], ...]:
    rectangles: list[tuple[float, float, float, float]] = [
        (
            flat_pattern.top_flat_length_mm,
            0.0,
            flat_pattern.bottom_flat_start_mm,
            flat_pattern.width_mm,
        )
    ]

    for z_band_min, z_band_max in layout.tab_bands:
        y_min = z_band_min - layout.z_min
        y_max = z_band_max - layout.z_min
        rectangles.append((0.0, y_min, flat_pattern.top_flat_length_mm, y_max))

    center_bridge_z_range = _center_bridge_z_range(layout)
    center_bridge_flat_length = _center_bridge_flat_length(layout, flat_pattern)
    if center_bridge_z_range is not None and center_bridge_flat_length is not None:
        y_min = center_bridge_z_range[0] - layout.z_min
        y_max = center_bridge_z_range[1] - layout.z_min
        x_min = flat_pattern.top_flat_length_mm - center_bridge_flat_length
        rectangles.append((x_min, y_min, flat_pattern.top_flat_length_mm, y_max))

    rectangles.append((flat_pattern.bottom_flat_start_mm, 0.0, flat_pattern.total_length_mm, flat_pattern.width_mm))

    return tuple(rectangle for rectangle in rectangles if rectangle[2] - rectangle[0] > 1e-6 and rectangle[3] - rectangle[1] > 1e-6)


def _flange_free_corner_points(
    layout: BracketLayout,
    flat_pattern: FlatPattern,
) -> set[tuple[float, float]]:
    points: set[tuple[float, float]] = set()
    for z_band_min, z_band_max in layout.tab_bands:
        flat_z_min = z_band_min - layout.z_min
        flat_z_max = z_band_max - layout.z_min
        points.add((0.0, flat_z_min))
        points.add((0.0, flat_z_max))
    points.add((flat_pattern.total_length_mm, 0.0))
    points.add((flat_pattern.total_length_mm, flat_pattern.width_mm))
    return points


def _sample_short_arc_2d(
    *,
    center: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
    count: int,
) -> list[tuple[float, float]]:
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    ccw_span = (end_angle - start_angle) % (2.0 * math.pi)
    if ccw_span <= math.pi:
        span = ccw_span
    else:
        span = -((start_angle - end_angle) % (2.0 * math.pi))

    samples = max(count, 2)
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    return [
        (
            center[0] + (radius * math.cos(start_angle + (span * index / (samples - 1)))),
            center[1] + (radius * math.sin(start_angle + (span * index / (samples - 1)))),
        )
        for index in range(samples)
    ]


def _rounded_outline_loop_points(
    loop: list[tuple[float, float]],
    *,
    rounded_corners: set[tuple[float, float]],
    radius: float,
) -> list[tuple[float, float]]:
    if radius <= 1e-6:
        return loop

    rounded_loop: list[tuple[float, float]] = []
    for index, point in enumerate(loop):
        if point not in rounded_corners:
            rounded_loop.append(point)
            continue

        previous_point = loop[index - 1]
        next_point = loop[(index + 1) % len(loop)]
        prev_vec = (previous_point[0] - point[0], previous_point[1] - point[1])
        next_vec = (next_point[0] - point[0], next_point[1] - point[1])
        prev_len = math.hypot(*prev_vec)
        next_len = math.hypot(*next_vec)
        corner_radius = min(radius, 0.5 * prev_len, 0.5 * next_len)
        if corner_radius <= 1e-6:
            rounded_loop.append(point)
            continue

        prev_unit = (prev_vec[0] / prev_len, prev_vec[1] / prev_len)
        next_unit = (next_vec[0] / next_len, next_vec[1] / next_len)
        tangent_in = (
            point[0] + (prev_unit[0] * corner_radius),
            point[1] + (prev_unit[1] * corner_radius),
        )
        tangent_out = (
            point[0] + (next_unit[0] * corner_radius),
            point[1] + (next_unit[1] * corner_radius),
        )
        center = (
            point[0] + ((prev_unit[0] + next_unit[0]) * corner_radius),
            point[1] + ((prev_unit[1] + next_unit[1]) * corner_radius),
        )
        arc_points = _sample_short_arc_2d(
            center=center,
            start=tangent_in,
            end=tangent_out,
            count=FLANGE_FREE_CORNER_SAMPLE_COUNT,
        )
        rounded_loop.extend(arc_points)

    return rounded_loop


def _outline_loops_from_rectangles(
    rectangles: tuple[tuple[float, float, float, float], ...],
) -> list[list[tuple[float, float]]]:
    x_coords = sorted({coord for x0, _y0, x1, _y1 in rectangles for coord in (x0, x1)})
    y_coords = sorted({coord for _x0, y0, _x1, y1 in rectangles for coord in (y0, y1)})
    filled_cells: set[tuple[int, int]] = set()

    for x_index in range(len(x_coords) - 1):
        for y_index in range(len(y_coords) - 1):
            x_mid = 0.5 * (x_coords[x_index] + x_coords[x_index + 1])
            y_mid = 0.5 * (y_coords[y_index] + y_coords[y_index + 1])
            if any(x0 < x_mid < x1 and y0 < y_mid < y1 for x0, y0, x1, y1 in rectangles):
                filled_cells.add((x_index, y_index))

    edges: dict[tuple[tuple[float, float], tuple[float, float]], None] = {}

    def add_edge(start: tuple[float, float], end: tuple[float, float]) -> None:
        reverse = (end, start)
        if reverse in edges:
            del edges[reverse]
            return
        edges[(start, end)] = None

    for x_index, y_index in filled_cells:
        x0 = x_coords[x_index]
        x1 = x_coords[x_index + 1]
        y0 = y_coords[y_index]
        y1 = y_coords[y_index + 1]
        add_edge((x0, y0), (x1, y0))
        add_edge((x1, y0), (x1, y1))
        add_edge((x1, y1), (x0, y1))
        add_edge((x0, y1), (x0, y0))

    outgoing: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for start, end in edges:
        outgoing.setdefault(start, []).append(end)

    loops: list[list[tuple[float, float]]] = []
    while outgoing:
        start = min(outgoing)
        loop = [start]
        current = start
        while True:
            next_points = outgoing.get(current)
            if not next_points:
                raise RuntimeError("Flat-pattern outline could not be assembled from panel rectangles")
            next_point = next_points.pop()
            if not next_points:
                del outgoing[current]
            if next_point == start:
                break
            loop.append(next_point)
            current = next_point

        area = _polygon_area(loop)
        if area < 0.0:
            loop.reverse()
        loops.append(loop)

    return loops


def _polygon_area(points: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        (points[index][0] * points[(index + 1) % len(points)][1])
        - (points[(index + 1) % len(points)][0] * points[index][1])
        for index in range(len(points))
    )


def _add_reference_arc(
    msp: ezdxf.layouts.Modelspace,
    *,
    start: tuple[float, float],
    midpoint: tuple[float, float],
    end: tuple[float, float],
) -> None:
    from ezdxf.math import ConstructionArc

    arc = ConstructionArc.from_3p(start, end, def_point=midpoint, ccw=True)
    center = (arc.center.x, arc.center.y)
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    mid_angle = math.atan2(midpoint[1] - center[1], midpoint[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    ccw_sweep = (end_angle - start_angle) % (2.0 * math.pi)
    mid_sweep = (mid_angle - start_angle) % (2.0 * math.pi)
    if mid_sweep <= ccw_sweep + 1e-6:
        msp.add_arc(
            center=center,
            radius=arc.radius,
            start_angle=math.degrees(start_angle),
            end_angle=math.degrees(end_angle),
            dxfattribs={"layer": "CUT"},
        )
        return

    msp.add_arc(
        center=center,
        radius=arc.radius,
        start_angle=math.degrees(end_angle),
        end_angle=math.degrees(start_angle),
        dxfattribs={"layer": "CUT"},
    )


def _build_flat_pattern_dxf(flat_pattern: FlatPattern, layout: BracketLayout) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2010", setup=True)
    doc.units = DXF_UNIT_MM
    if "CUT" not in doc.layers:
        doc.layers.add("CUT")
    if "BEND" not in doc.layers:
        doc.layers.add("BEND", linetype="DASHED")

    msp = doc.modelspace()
    rounded_corners = _flange_free_corner_points(layout, flat_pattern)
    for loop in _outline_loops_from_rectangles(_flat_rectangles(layout, flat_pattern)):
        rounded_loop = _rounded_outline_loop_points(
            loop,
            rounded_corners=rounded_corners,
            radius=FLANGE_FREE_CORNER_RADIUS_MM,
        )
        for index, point in enumerate(rounded_loop):
            msp.add_line(point, rounded_loop[(index + 1) % len(rounded_loop)], dxfattribs={"layer": "CUT"})

    for x, y, cut_radius in flat_pattern.cut_circles:
        msp.add_circle((x, y), cut_radius, dxfattribs={"layer": "CUT"})

    for segment_type, points in flat_pattern.cut_segments:
        if segment_type == "LINE":
            msp.add_line(points[0], points[-1], dxfattribs={"layer": "CUT"})
        elif segment_type == "ARC":
            _add_reference_arc(msp, start=points[0], midpoint=points[1], end=points[-1])
        else:
            raise RuntimeError(f"Unsupported flat-pattern cut segment type: {segment_type}")

    for bend_line_x, bend_y_min, bend_y_max in flat_pattern.bend_line_segments_mm:
        msp.add_line(
            (bend_line_x, bend_y_min),
            (bend_line_x, bend_y_max),
            dxfattribs={"layer": "BEND", "linetype": "DASHED"},
        )
    return doc


def build_step(
    *,
    include_top_center_bridge: bool = True,
) -> build123d.Shape:
    CAD_DIR.mkdir(parents=True, exist_ok=True)

    if not SERVO_STEP.exists():
        raise FileNotFoundError(f"Missing STS3250 servo STEP: {SERVO_STEP}")

    servo_shape = import_as_shape(SERVO_STEP)
    bracket, layout = build_bracket(servo_shape, include_top_center_bridge=include_top_center_bridge)
    flat_pattern = _build_flat_pattern(layout)

    solids = bracket.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one connected bracket solid, found {len(solids)}")

    intersection_volume = servo_shape.intersect(bracket).volume
    if intersection_volume > 1e-3:
        raise RuntimeError(f"Bracket intersects the servo by {intersection_volume:.6f} mm^3")

    bracket.label = PART_NAME
    bracket.color = GRAY_ALUMINUM_COLOR

    bracket_bb = bracket.bounding_box()
    print(
        "Bracket envelope "
        f"X={bracket_bb.size.X:.3f} mm, "
        f"Y={bracket_bb.size.Y:.3f} mm, "
        f"Z={bracket_bb.size.Z:.3f} mm"
    )
    print(
        "SendCutSend setup "
        f"material=5052-H32, thickness={SENDCUTSEND_SHEET_THICKNESS_MM:.3f} mm, "
        f"bend_radius={SENDCUTSEND_EFFECTIVE_BEND_RADIUS_MM:.3f} mm, "
        f"k_factor={SENDCUTSEND_K_FACTOR:.2f}, "
        f"bend_deduction_90={SENDCUTSEND_BEND_DEDUCTION_90_MM:.3f} mm, "
        f"anodize_min_short_dim={SENDCUTSEND_MIN_ANODIZE_SHORTEST_DIM_MM:.3f} mm"
    )
    sendcutsend_checks = (
        "SendCutSend checks "
        f"hole_to_bend_min={min(abs(center.X - layout.bracket_face_inner_x) for center, _radius in [*layout.top_holes, *layout.bottom_holes]):.3f} mm, "
        f"bend_segment_min={_min_bend_segment_length(layout):.3f} mm, "
        f"bend_support={_supported_bend_fraction(layout):.1%}, "
        f"outboard_hole_edge_web={layout.outer_flush_gap:.3f} mm, "
        f"flange_free_corner_radius={FLANGE_FREE_CORNER_RADIUS_MM:.3f} mm"
    )
    print(sendcutsend_checks)
    if layout.outer_flush_gap < MOUNT_HOLE_TRIMMED_OUTBOARD_Z_WEB_WARN_MM:
        print(
            "Warning: trimmed outer side leaves a small hole edge web: "
            f"{layout.outer_flush_gap:.3f} mm < {MOUNT_HOLE_TRIMMED_OUTBOARD_Z_WEB_WARN_MM:.3f} mm"
        )
    end_face_x = _find_face_near(servo_shape, FACE_TARGET_CENTERS_MM["end_face"], geom_type="PLANE").bounding_box().max.X
    print(f"Bracket face clearance: {layout.bracket_face_inner_x - end_face_x:.3f} mm")
    center_bridge_bend_edge_distance = _center_bridge_bend_edge_distance(layout)
    center_bridge_flat_clearance = _center_bridge_flat_bend_line_to_edge_distance(layout, flat_pattern)
    if center_bridge_bend_edge_distance is not None and center_bridge_flat_clearance is not None:
        print(
            "Top center bridge "
            f"bend_edge_distance={center_bridge_bend_edge_distance:.3f} mm, "
            f"flat_bend_line_to_edge={center_bridge_flat_clearance:.3f} mm"
        )
    print(f"Bracket face inner X: {layout.bracket_face_inner_x:.3f} mm")
    print(
        "Top mount holes used (x, y, z) mm: "
        + ", ".join(str(tuple(round(value, 6) for value in _vector_to_tuple(center))) for center, _radius in layout.top_holes)
    )
    print(
        "Bottom mount holes used (x, y, z) mm: "
        + ", ".join(str(tuple(round(value, 6) for value in _vector_to_tuple(center))) for center, _radius in layout.bottom_holes)
    )
    print(f"Servo/bracket interference volume (mm^3): {intersection_volume:.6f}")
    return bracket


def build_dxf(
    *,
    include_top_center_bridge: bool = True,
) -> ezdxf.document.Drawing:
    CAD_DIR.mkdir(parents=True, exist_ok=True)

    if not SERVO_STEP.exists():
        raise FileNotFoundError(f"Missing STS3250 servo STEP: {SERVO_STEP}")

    servo_shape = import_as_shape(SERVO_STEP)
    layout = _build_layout(servo_shape, include_top_center_bridge=include_top_center_bridge)
    flat_pattern = _build_flat_pattern(layout)
    doc = _build_flat_pattern_dxf(flat_pattern, layout)

    outside_setback = _sendcutsend_outside_setback_90_mm()
    flange_outside_min = _min_flange_outside_length(layout)
    print(
        "Flat pattern "
        f"length={flat_pattern.total_length_mm:.3f} mm, "
        f"width={flat_pattern.width_mm:.3f} mm, "
        f"bend_lines=({flat_pattern.bend_line_positions_mm[0]:.3f}, {flat_pattern.bend_line_positions_mm[1]:.3f}) mm, "
        f"bend_segments={len(flat_pattern.bend_line_segments_mm)}, "
        f"cut_circles={len(flat_pattern.cut_circles)}, "
        f"front_cut_segments={len(flat_pattern.cut_segments)}"
    )
    dxf_bend_checks = (
        "DXF bend checks "
        f"flange_outside_min={flange_outside_min:.3f} mm, "
        f"flange_flat_min={flange_outside_min - outside_setback:.3f} mm, "
        f"flange_flat_required={SENDCUTSEND_MIN_FLANGE_FLAT_MM:.3f} mm, "
        f"bend_segment_min={_min_bend_segment_length(layout):.3f} mm, "
        f"bend_support={_supported_bend_fraction(layout):.1%}, "
        f"hole_to_bend_min={min(abs(center.X - layout.bracket_face_inner_x) for center, _radius in [*layout.top_holes, *layout.bottom_holes]):.3f} mm, "
        f"outboard_hole_edge_web={layout.outer_flush_gap:.3f} mm"
    )
    print(dxf_bend_checks)
    center_bridge_bend_edge_distance = _center_bridge_bend_edge_distance(layout)
    center_bridge_flat_clearance = _center_bridge_flat_bend_line_to_edge_distance(layout, flat_pattern)
    if center_bridge_bend_edge_distance is not None and center_bridge_flat_clearance is not None:
        print(
            "DXF center bridge "
            f"bend_edge_distance={center_bridge_bend_edge_distance:.3f} mm, "
            f"flat_bend_line_to_edge={center_bridge_flat_clearance:.3f} mm"
        )
    return doc


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
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
