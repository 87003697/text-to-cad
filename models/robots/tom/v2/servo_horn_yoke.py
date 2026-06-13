#!/usr/bin/env python3
"""
Generate the localized horn-mounted yoke.

This variant preserves the existing horn interface geometry and repeats the
servo horn's 4x M3 screw pattern plus a larger center clearance hole on the
horn flanges and closing web.

Usage:
  python STEP/servo_horn_yoke.py
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
DISPLAY_NAME = "Servo Horn Yoke"

SERVO_STEP = REPO_ROOT / "STEP" / "imports" / "sts3250.step"

UPPER_HORN_SELECTOR = "xcaf:1.4"
LOWER_HORN_SELECTOR = "xcaf:1.5"
UPPER_HORN_NAME = "4"
LOWER_HORN_NAME = "5"

# Keep the rotating web outside the primary servo's left-side corner sweep.
# This is measured from the flange servo horn axis to the yoke web's outer face.
YOKE_HORN_AXIS_TO_WEB_OUTER_LENGTH_MM = 32.0
SERVO_HORN_AXIS_X_MM = -25.5
SERVO_REFERENCE_FACE_TARGET_CENTER_MM = (-35.61, -7.65, 0.0)
SERVO_REFERENCE_FACE_MAX_ERROR_MM = 0.5
HORN_FACE_GAP_MM = 0.25
MOUNT_HOLE_MIN_OFFSET_MM = 3.0
CUT_EXTENSION_MM = 2.0
M3_CLEARANCE_RADIUS_MM = 1.7
HORN_CENTER_CLEARANCE_RADIUS_MM = 3.25
REFERENCE_SHEET_THICKNESS_MM = 25.4 * 0.080


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


SHEET_THICKNESS_MM = _env_float("TOM_V2_SHEET_THICKNESS_MM", 25.4 * 0.080)
SHEET_RULE_SCALE = SHEET_THICKNESS_MM / REFERENCE_SHEET_THICKNESS_MM


@dataclass(frozen=True)
class BendRule:
    material_name: str
    thickness_mm: float
    effective_inside_radius_mm: float
    k_factor: float
    bend_deduction_90_mm: float
    half_die_width_mm: float
    min_flange_flat_mm: float
    min_flange_formed_mm: float
    min_acute_centerline_length_mm: float
    channel_one_to_one_max_flange_mm: float
    channel_narrow_min_base_mm: float
    channel_base_spacing_min_mm: float
    max_bend_angle_deg: float

    @property
    def outside_setback_90_mm(self) -> float:
        return self.effective_inside_radius_mm + self.thickness_mm

    @property
    def bend_allowance_90_mm(self) -> float:
        return (math.pi * 0.5) * (self.effective_inside_radius_mm + (self.k_factor * self.thickness_mm))

    @property
    def outer_radius_mm(self) -> float:
        return self.effective_inside_radius_mm + self.thickness_mm

    def flat_length_from_outside_length_90(self, outside_length_mm: float) -> float:
        return outside_length_mm - self.outside_setback_90_mm


SHEET_RULE = BendRule(
    material_name="5052-H32 ALU-080",
    thickness_mm=SHEET_THICKNESS_MM,
    effective_inside_radius_mm=25.4 * 0.038,
    k_factor=0.48,
    bend_deduction_90_mm=(2.0 * ((25.4 * 0.038) + SHEET_THICKNESS_MM))
    - ((math.pi * 0.5) * ((25.4 * 0.038) + (0.48 * SHEET_THICKNESS_MM))),
    half_die_width_mm=(0.5 * (25.4 * 0.472)) * SHEET_RULE_SCALE,
    min_flange_flat_mm=(25.4 * 0.255) * SHEET_RULE_SCALE,
    min_flange_formed_mm=(25.4 * 0.313) * SHEET_RULE_SCALE,
    min_acute_centerline_length_mm=(25.4 * 0.332) * SHEET_RULE_SCALE,
    channel_one_to_one_max_flange_mm=25.4 * 3.000,
    channel_narrow_min_base_mm=25.4 * 1.000,
    channel_base_spacing_min_mm=(2.0 * (25.4 * 0.255)) * SHEET_RULE_SCALE,
    max_bend_angle_deg=130.0,
)


@dataclass(frozen=True)
class HornMountSpec:
    name: str
    mount_face_y: float
    mount_face_center: build123d.Vector
    mount_face_bbox: build123d.BoundBox
    half_width_z: float
    center_clearance_radius: float
    mount_holes: tuple[tuple[build123d.Vector, float], ...]


@dataclass(frozen=True)
class BracketLayout:
    web_inner_x: float
    web_outer_x: float
    top_y_min: float
    top_y_max: float
    bottom_y_min: float
    bottom_y_max: float
    top_z_min: float
    top_z_max: float
    bottom_z_min: float
    bottom_z_max: float
    global_z_min: float
    global_z_max: float


@dataclass(frozen=True)
class BracketMetrics:
    min_flange_outside_length_mm: float
    min_flange_flat_length_mm: float
    bend_line_spacing_mm: float
    min_hole_edge_to_bend_mm: float
    min_web_feature_edge_to_bend_mm: float
    base_to_flange_ratio: float
    bend_deduction_formula_mm: float


@dataclass(frozen=True)
class GeneratorInputs:
    servo_shape: build123d.Shape
    reference_face_x: float
    sheet_half_width_z: float
    upper_horn: HornMountSpec
    lower_horn: HornMountSpec


@dataclass(frozen=True)
class WebHoleSpec:
    center_y: float
    center_z: float
    radius: float


@dataclass(frozen=True)
class CircleProfileSpec:
    center_x: float
    center_y: float
    radius: float


@dataclass(frozen=True)
class FlatPattern:
    total_length_mm: float
    width_mm: float
    cap_radius_mm: float
    bend_line_positions_mm: tuple[float, float]
    round_hole_profiles: tuple[CircleProfileSpec, ...]


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


def _face_from_wire(
    exterior: build123d.Wire,
    interior_wires: list[build123d.Wire] | None = None,
) -> build123d.Face:
    holes = list(interior_wires or [])
    return build123d.Face(exterior, holes) if holes else build123d.Face(exterior)


def _assemble_wire(edges: list[build123d.Edge]) -> build123d.Wire:
    wires = list(build123d.Wire.combine(edges))
    if len(wires) != 1:
        raise RuntimeError(f"Expected a single assembled wire, found {len(wires)}")
    return wires[0]


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


def _validate_bend_rule(rule: BendRule) -> None:
    bend_deduction_error = abs(rule.bend_deduction_90_mm - ((2.0 * rule.outside_setback_90_mm) - rule.bend_allowance_90_mm))
    if bend_deduction_error > 0.05:
        raise RuntimeError(
            "SendCutSend bend-rule constants are inconsistent: "
            f"bend deduction error {bend_deduction_error:.3f} mm is too large"
        )


def _extract_selector_shape(step_path: Path, selector: str) -> tuple[str, build123d.Shape]:
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

    def _label_name(label: object) -> str:
        name = TDataStd_Name()
        if not label.FindAttribute(TDataStd_Name.GetID_s(), name):
            return "<unnamed>"
        return name.Get().ToExtString()

    def _resolve_label(shape_tool: object, label: object) -> object:
        if not shape_tool.IsReference_s(label):
            return label
        referred = TDF_Label()
        if shape_tool.GetReferredShape_s(label, referred):
            return referred
        return label

    def _child_components(shape_tool: object, label: object) -> list[object]:
        children = TDF_LabelSequence()
        ok = XCAFDoc_ShapeTool.GetComponents_s(label, children, False)
        resolved = _resolve_label(shape_tool, label)
        if (not ok or children.Length() <= 0) and resolved != label:
            children = TDF_LabelSequence()
            ok = XCAFDoc_ShapeTool.GetComponents_s(resolved, children, False)
        if not ok or children.Length() <= 0:
            return []
        return [children.Value(index) for index in range(1, children.Length() + 1)]

    def _shape_location(shape: object) -> build123d.Location:
        try:
            return build123d.Location(shape.Location().Transformation())
        except Exception:
            return build123d.Location()

    def _located_shape(shape: object, location: build123d.Location | None) -> object:
        if location is None:
            return shape
        return shape.Located(location.wrapped)

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("servo_horn_yoke_doc"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    read_status = reader.ReadFile(str(step_path))
    if int(read_status) != 1:
        raise RuntimeError(f"Could not read {step_path} (status={int(read_status)})")
    if not reader.Transfer(doc):
        raise RuntimeError(f"Could not transfer STEP document from {step_path}")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free_labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_labels)
    if free_labels.Length() <= 0:
        raise RuntimeError(f"No free shapes found in {step_path}")

    if not selector.startswith("xcaf:"):
        raise ValueError(f"Unsupported selector format: {selector}")
    segments = [int(value) for value in selector.split(":", 1)[1].split(".") if value]
    if not segments:
        raise ValueError(f"Selector does not contain XCAF path segments: {selector}")
    if segments[0] < 1 or segments[0] > free_labels.Length():
        raise RuntimeError(f"Selector root index is out of range for {step_path}: {selector}")

    label = free_labels.Value(segments[0])
    current_location: build123d.Location | None = None
    for index in segments[1:]:
        parent_location = _shape_location(shape_tool.GetShape_s(label))
        current_location = parent_location if current_location is None else current_location * parent_location
        children = _child_components(shape_tool, label)
        if index < 1 or index > len(children):
            raise RuntimeError(f"Selector segment {index} is out of range for {selector}")
        label = children[index - 1]

    resolved = _resolve_label(shape_tool, label)
    instance_shape = shape_tool.GetShape_s(label)
    resolved_shape = shape_tool.GetShape_s(resolved)
    base_shape = instance_shape if not instance_shape.IsNull() else resolved_shape
    if base_shape.IsNull():
        raise RuntimeError(f"Selector {selector} did not resolve to a shape")

    base_location = _shape_location(base_shape)
    current_location = base_location if current_location is None else current_location * base_location
    extracted = _cast_shape(_located_shape(base_shape, current_location))
    return _label_name(label), extracted


def _wire_area(wire: build123d.Wire) -> float:
    return abs(_face_from_wire(wire).area)


def _outer_wire_and_inner_wires(face: build123d.Face) -> tuple[build123d.Wire, list[build123d.Wire]]:
    wires = list(face.wires())
    if not wires:
        raise RuntimeError("Face has no wires")

    outer_wire = max(wires, key=_wire_area)
    inner_wires = [wire for wire in wires if not wire.is_same(outer_wire)]
    return outer_wire, inner_wires


def _select_mount_face(shape: build123d.Shape, *, outward_sign: float) -> build123d.Face:
    shape_bb = shape.bounding_box()
    target_y = shape_bb.max.Y if outward_sign > 0.0 else shape_bb.min.Y
    candidates: list[tuple[float, build123d.Face]] = []
    for face in shape.faces():
        if face.geom_type.name != "PLANE":
            continue
        normal = face.normal_at()
        if outward_sign > 0.0 and normal.Y < 0.99:
            continue
        if outward_sign < 0.0 and normal.Y > -0.99:
            continue
        if abs(face.center().Y - target_y) > 0.25:
            continue
        candidates.append((face.area, face))

    if not candidates:
        raise RuntimeError(f"Could not find a horn mount face on y sign {outward_sign}")
    return max(candidates, key=lambda item: item[0])[1]


def _extract_circular_wire_profiles(face: build123d.Face) -> list[tuple[build123d.Vector, float]]:
    _outer_wire, inner_wires = _outer_wire_and_inner_wires(face)
    profiles: list[tuple[build123d.Vector, float]] = []
    for wire in inner_wires:
        circle_edges = [edge for edge in wire.edges() if edge.geom_type.name == "CIRCLE"]
        if not circle_edges:
            continue
        center = _face_from_wire(wire).center()
        radius = max(edge.radius for edge in circle_edges)
        profiles.append((center, radius))
    return profiles


def _extract_horn_mount_spec(shape: build123d.Shape, *, name: str, outward_sign: float) -> HornMountSpec:
    mount_face = _select_mount_face(shape, outward_sign=outward_sign)
    face_center = mount_face.center()
    shape_bbox = shape.bounding_box()
    profiles = _extract_circular_wire_profiles(mount_face)
    if not profiles:
        raise RuntimeError(f"No circular profiles found on horn mount face for {name}")

    center_profiles = [
        (center, radius)
        for center, radius in profiles
        if center.sub(face_center).length <= MOUNT_HOLE_MIN_OFFSET_MM
    ]
    if not center_profiles:
        raise RuntimeError(f"No center clearance profile found on horn mount face for {name}")
    center_clearance_radius = max(radius for _center, radius in center_profiles)

    mount_holes = [
        (center, radius)
        for center, radius in profiles
        if center.sub(face_center).length > MOUNT_HOLE_MIN_OFFSET_MM
    ]
    if len(mount_holes) != 4:
        raise RuntimeError(f"Expected 4 horn mount holes on {name}, found {len(mount_holes)}")

    mount_holes.sort(key=lambda item: (item[0].X, item[0].Z))
    return HornMountSpec(
        name=name,
        mount_face_y=face_center.Y,
        mount_face_center=face_center,
        mount_face_bbox=mount_face.bounding_box(),
        half_width_z=max(face_center.Z - shape_bbox.min.Z, shape_bbox.max.Z - face_center.Z),
        center_clearance_radius=center_clearance_radius,
        mount_holes=tuple(mount_holes),
    )


def _find_reference_face_x(shape: build123d.Shape) -> float:
    target = build123d.Vector(*SERVO_REFERENCE_FACE_TARGET_CENTER_MM)
    candidates: list[tuple[float, float, build123d.Face]] = []
    for face in shape.faces():
        if face.geom_type.name != "PLANE":
            continue
        normal = face.normal_at()
        if normal.X > -0.99:
            continue
        center = face.center()
        dist = center.sub(target).length
        candidates.append((dist, -face.area, face))

    if not candidates:
        raise RuntimeError("Could not find the requested negative-X reference face on the servo")

    dist, _neg_area, face = min(candidates, key=lambda item: (item[0], item[1]))
    if dist > SERVO_REFERENCE_FACE_MAX_ERROR_MM:
        raise RuntimeError(
            "Resolved servo reference face is too far from the requested target center: "
            f"{dist:.3f} mm > {SERVO_REFERENCE_FACE_MAX_ERROR_MM:.3f} mm"
        )
    return face.bounding_box().min.X


def _shape_half_width_z(shape: build123d.Shape) -> float:
    bbox = shape.bounding_box()
    return max(abs(bbox.min.Z), abs(bbox.max.Z))


def _load_generator_inputs() -> GeneratorInputs:
    _validate_bend_rule(SHEET_RULE)
    if not SERVO_STEP.exists():
        raise FileNotFoundError(f"Missing servo STEP: {SERVO_STEP}")

    servo_shape = import_as_shape(SERVO_STEP)
    reference_face_x = _find_reference_face_x(servo_shape)

    upper_label, upper_horn_shape = _extract_selector_shape(SERVO_STEP, UPPER_HORN_SELECTOR)
    lower_label, lower_horn_shape = _extract_selector_shape(SERVO_STEP, LOWER_HORN_SELECTOR)
    if upper_label != UPPER_HORN_NAME:
        raise RuntimeError(
            f"Expected upper horn selector {UPPER_HORN_SELECTOR} to resolve to {UPPER_HORN_NAME}, got {upper_label}"
        )
    if lower_label != LOWER_HORN_NAME:
        raise RuntimeError(
            f"Expected lower horn selector {LOWER_HORN_SELECTOR} to resolve to {LOWER_HORN_NAME}, got {lower_label}"
        )

    upper_horn = _extract_horn_mount_spec(upper_horn_shape, name=upper_label, outward_sign=1.0)
    lower_horn = _extract_horn_mount_spec(lower_horn_shape, name=lower_label, outward_sign=-1.0)
    return GeneratorInputs(
        servo_shape=servo_shape,
        reference_face_x=reference_face_x,
        sheet_half_width_z=_shape_half_width_z(servo_shape),
        upper_horn=upper_horn,
        lower_horn=lower_horn,
    )


def _make_box(*, x_min: float, x_max: float, y_min: float, y_max: float, z_min: float, z_max: float) -> build123d.Solid:
    return build123d.Solid.make_box(
        x_max - x_min,
        y_max - y_min,
        z_max - z_min,
        plane=build123d.Plane(origin=build123d.Vector(x_min, y_min, z_min)),
    )


def _make_capped_flange(
    *,
    face_center: build123d.Vector,
    web_inner_x: float,
    y_min: float,
    y_max: float,
    cap_radius: float,
) -> build123d.Shape:
    strip = _make_box(
        x_min=web_inner_x,
        x_max=face_center.X,
        y_min=y_min,
        y_max=y_max,
        z_min=face_center.Z - cap_radius,
        z_max=face_center.Z + cap_radius,
    )
    cap = _make_cylinder_along(
        radius=cap_radius,
        height=y_max - y_min,
        origin=build123d.Vector(face_center.X, y_min, face_center.Z),
        direction=build123d.Vector(0.0, 1.0, 0.0),
    )
    return strip.fuse(cap)


def _web_pattern_center_from_values(
    *,
    web_outer_x: float,
    top_y_max: float,
    bottom_y_min: float,
    z_center: float,
) -> build123d.Vector:
    return build123d.Vector(
        web_outer_x,
        0.5 * (bottom_y_min + top_y_max),
        z_center,
    )


def _web_pattern_center(layout: BracketLayout) -> build123d.Vector:
    return _web_pattern_center_from_values(
        web_outer_x=layout.web_outer_x,
        top_y_max=layout.top_y_max,
        bottom_y_min=layout.bottom_y_min,
        z_center=0.5 * (layout.global_z_min + layout.global_z_max),
    )


def _center_hole_radius(horn: HornMountSpec) -> float:
    return max(HORN_CENTER_CLEARANCE_RADIUS_MM, horn.center_clearance_radius)


def _horn_face_hole_specs(horn: HornMountSpec) -> tuple[tuple[build123d.Vector, float], ...]:
    return (
        (horn.mount_face_center, _center_hole_radius(horn)),
        *(
            (center, M3_CLEARANCE_RADIUS_MM)
            for center, _radius in horn.mount_holes
        ),
    )


def _web_round_hole_specs(
    layout: BracketLayout,
    *,
    horn: HornMountSpec,
) -> tuple[WebHoleSpec, ...]:
    center = _web_pattern_center(layout)
    specs = [
        WebHoleSpec(
            center_y=center.Y,
            center_z=center.Z,
            radius=_center_hole_radius(horn),
        )
    ]
    for mount_center, _radius in horn.mount_holes:
        specs.append(
            WebHoleSpec(
                center_y=center.Y + (mount_center.X - horn.mount_face_center.X),
                center_z=center.Z + (mount_center.Z - horn.mount_face_center.Z),
                radius=M3_CLEARANCE_RADIUS_MM,
            )
        )
    return tuple(specs)


def _cut_round_horn_face_holes(
    shape: build123d.Shape,
    *,
    horn: HornMountSpec,
    start_y: float,
    direction_y: float,
    rule: BendRule,
) -> build123d.Shape:
    for center, radius in _horn_face_hole_specs(horn):
        cutter = _make_cylinder_along(
            radius=radius,
            height=rule.thickness_mm + (2.0 * CUT_EXTENSION_MM),
            origin=build123d.Vector(center.X, start_y, center.Z),
            direction=build123d.Vector(0.0, direction_y, 0.0),
        )
        shape = shape.cut(cutter)
    return shape


def _cut_round_web_holes(
    shape: build123d.Shape,
    *,
    layout: BracketLayout,
    horn: HornMountSpec,
    rule: BendRule,
) -> build123d.Shape:
    start_x = layout.web_outer_x - CUT_EXTENSION_MM
    for hole in _web_round_hole_specs(layout, horn=horn):
        cutter = _make_cylinder_along(
            radius=hole.radius,
            height=rule.thickness_mm + (2.0 * CUT_EXTENSION_MM),
            origin=build123d.Vector(start_x, hole.center_y, hole.center_z),
            direction=build123d.Vector(1.0, 0.0, 0.0),
        )
        shape = shape.cut(cutter)
    return shape


def _make_bent_channel_body(
    *,
    top_end_x: float,
    bottom_end_x: float,
    layout: BracketLayout,
) -> build123d.Shape:
    # Build the channel body with sharp corners instead of formed bend radii.
    x_inner = layout.web_inner_x
    x_outer = layout.web_outer_x

    profile_points = [
        build123d.Vector(top_end_x, layout.top_y_max, 0.0),
        build123d.Vector(x_outer, layout.top_y_max, 0.0),
        build123d.Vector(x_outer, layout.bottom_y_min, 0.0),
        build123d.Vector(bottom_end_x, layout.bottom_y_min, 0.0),
        build123d.Vector(bottom_end_x, layout.bottom_y_max, 0.0),
        build123d.Vector(x_inner, layout.bottom_y_max, 0.0),
        build123d.Vector(x_inner, layout.top_y_min, 0.0),
        build123d.Vector(top_end_x, layout.top_y_min, 0.0),
    ]
    profile_edges = [
        build123d.Edge.make_line(profile_points[0], profile_points[1]),
        build123d.Edge.make_line(profile_points[1], profile_points[2]),
        build123d.Edge.make_line(profile_points[2], profile_points[3]),
        build123d.Edge.make_line(profile_points[3], profile_points[4]),
        build123d.Edge.make_line(profile_points[4], profile_points[5]),
        build123d.Edge.make_line(profile_points[5], profile_points[6]),
        build123d.Edge.make_line(profile_points[6], profile_points[7]),
        build123d.Edge.make_line(profile_points[7], profile_points[0]),
    ]
    face = _face_from_wire(_assemble_wire(profile_edges))
    return build123d.Solid.extrude(
        face,
        build123d.Vector(0.0, 0.0, layout.global_z_max - layout.global_z_min),
    ).translate((0.0, 0.0, layout.global_z_min))


def _min_horn_face_feature_edge_to_bend_line(
    horn: HornMountSpec,
    *,
    bend_line_u: float,
) -> float:
    return min(
        (center.X - radius) - bend_line_u
        for center, radius in _horn_face_hole_specs(horn)
    )


def _min_web_feature_edge_to_bend_lines(
    *,
    layout: BracketLayout,
    horn: HornMountSpec,
) -> float:
    distances: list[float] = []
    for hole in _web_round_hole_specs(layout, horn=horn):
        distances.append((hole.center_y - hole.radius) - layout.bottom_y_max)
        distances.append(layout.top_y_min - (hole.center_y + hole.radius))
    return min(distances)


def _build_metrics(
    *,
    upper_horn: HornMountSpec,
    lower_horn: HornMountSpec,
    layout: BracketLayout,
    rule: BendRule,
) -> BracketMetrics:
    upper_outside_length = upper_horn.mount_face_center.X - layout.web_outer_x
    lower_outside_length = lower_horn.mount_face_center.X - layout.web_outer_x
    min_flange_outside_length = min(upper_outside_length, lower_outside_length)
    min_flange_flat_length = rule.flat_length_from_outside_length_90(min_flange_outside_length)
    bend_line_spacing = layout.top_y_min - layout.bottom_y_max

    min_hole_edge_to_bend = min(
        _min_horn_face_feature_edge_to_bend_line(upper_horn, bend_line_u=layout.web_inner_x),
        _min_horn_face_feature_edge_to_bend_line(lower_horn, bend_line_u=layout.web_inner_x),
    )
    min_web_feature_edge_to_bend = _min_web_feature_edge_to_bend_lines(
        layout=layout,
        horn=upper_horn,
    )

    max_flange_outside = max(upper_outside_length, lower_outside_length)
    base_to_flange_ratio = bend_line_spacing / max_flange_outside

    return BracketMetrics(
        min_flange_outside_length_mm=min_flange_outside_length,
        min_flange_flat_length_mm=min_flange_flat_length,
        bend_line_spacing_mm=bend_line_spacing,
        min_hole_edge_to_bend_mm=min_hole_edge_to_bend,
        min_web_feature_edge_to_bend_mm=min_web_feature_edge_to_bend,
        base_to_flange_ratio=base_to_flange_ratio,
        bend_deduction_formula_mm=(2.0 * rule.outside_setback_90_mm) - rule.bend_allowance_90_mm,
    )


def _validate_metrics(metrics: BracketMetrics, *, rule: BendRule) -> None:
    if metrics.min_flange_outside_length_mm < rule.min_flange_formed_mm:
        raise RuntimeError(
            "Flange is shorter than the configured bend-rule formed minimum: "
            f"{metrics.min_flange_outside_length_mm:.3f} mm < {rule.min_flange_formed_mm:.3f} mm"
        )
    if metrics.min_flange_flat_length_mm < rule.min_flange_flat_mm:
        raise RuntimeError(
            "Flat flange length implied by the bend rule is too short: "
            f"{metrics.min_flange_flat_length_mm:.3f} mm < {rule.min_flange_flat_mm:.3f} mm"
        )
    if metrics.bend_line_spacing_mm < rule.channel_base_spacing_min_mm:
        raise RuntimeError(
            "Channel base spacing is below the SendCutSend consecutive-bend minimum: "
            f"{metrics.bend_line_spacing_mm:.3f} mm < {rule.channel_base_spacing_min_mm:.3f} mm"
        )
    if metrics.min_hole_edge_to_bend_mm < rule.half_die_width_mm:
        raise RuntimeError(
            "Horn flange features are too close to the bend die region: "
            f"{metrics.min_hole_edge_to_bend_mm:.3f} mm < {rule.half_die_width_mm:.3f} mm"
        )
    if metrics.min_web_feature_edge_to_bend_mm < rule.half_die_width_mm:
        raise RuntimeError(
            "Closing-face horn pattern is too close to a bend die region: "
            f"{metrics.min_web_feature_edge_to_bend_mm:.3f} mm < {rule.half_die_width_mm:.3f} mm"
        )
    if metrics.min_flange_outside_length_mm <= rule.channel_one_to_one_max_flange_mm and metrics.base_to_flange_ratio < 1.0:
        raise RuntimeError(
            "U-channel base-to-flange ratio is below the configured 1:1 manufacturing check for this stock: "
            f"{metrics.base_to_flange_ratio:.3f} < 1.000"
        )


def _build_layout(
    reference_face_x: float,
    *,
    sheet_half_width_z: float,
    upper_horn: HornMountSpec,
    lower_horn: HornMountSpec,
    rule: BendRule,
) -> tuple[BracketLayout, float]:
    web_outer_x = SERVO_HORN_AXIS_X_MM - YOKE_HORN_AXIS_TO_WEB_OUTER_LENGTH_MM
    web_inner_x = web_outer_x + rule.thickness_mm

    top_y_min = upper_horn.mount_face_y + HORN_FACE_GAP_MM
    top_y_max = top_y_min + rule.thickness_mm
    bottom_y_max = lower_horn.mount_face_y - HORN_FACE_GAP_MM
    bottom_y_min = bottom_y_max - rule.thickness_mm

    cap_radius = max(upper_horn.half_width_z, lower_horn.half_width_z)

    top_z_min = upper_horn.mount_face_center.Z - cap_radius
    top_z_max = upper_horn.mount_face_center.Z + cap_radius
    bottom_z_min = lower_horn.mount_face_center.Z - cap_radius
    bottom_z_max = lower_horn.mount_face_center.Z + cap_radius
    global_z_min = min(top_z_min, bottom_z_min)
    global_z_max = max(top_z_max, bottom_z_max)

    layout = BracketLayout(
        web_inner_x=web_inner_x,
        web_outer_x=web_outer_x,
        top_y_min=top_y_min,
        top_y_max=top_y_max,
        bottom_y_min=bottom_y_min,
        bottom_y_max=bottom_y_max,
        top_z_min=top_z_min,
        top_z_max=top_z_max,
        bottom_z_min=bottom_z_min,
        bottom_z_max=bottom_z_max,
        global_z_min=global_z_min,
        global_z_max=global_z_max,
    )
    return layout, cap_radius


def build_bracket(
    reference_face_x: float,
    *,
    sheet_half_width_z: float,
    upper_horn: HornMountSpec,
    lower_horn: HornMountSpec,
    rule: BendRule,
    validate_metrics: bool = True,
) -> tuple[build123d.Shape, BracketLayout, BracketMetrics]:
    layout, cap_radius = _build_layout(
        reference_face_x,
        sheet_half_width_z=sheet_half_width_z,
        upper_horn=upper_horn,
        lower_horn=lower_horn,
        rule=rule,
    )
    web_inner_x = layout.web_inner_x
    web_outer_x = layout.web_outer_x
    top_y_min = layout.top_y_min
    top_y_max = layout.top_y_max
    bottom_y_min = layout.bottom_y_min
    bottom_y_max = layout.bottom_y_max
    global_z_min = layout.global_z_min
    global_z_max = layout.global_z_max

    bracket = _make_bent_channel_body(
        top_end_x=upper_horn.mount_face_center.X + cap_radius,
        bottom_end_x=lower_horn.mount_face_center.X + cap_radius,
        layout=layout,
    )
    cap_mask = (
        _make_box(
            x_min=web_outer_x,
            x_max=web_inner_x,
            y_min=bottom_y_min,
            y_max=top_y_max,
            z_min=global_z_min,
            z_max=global_z_max,
        )
        .fuse(
            _make_capped_flange(
                face_center=upper_horn.mount_face_center,
                web_inner_x=web_inner_x,
                y_min=top_y_min,
                y_max=top_y_max,
                cap_radius=cap_radius,
            )
        )
        .fuse(
            _make_capped_flange(
                face_center=lower_horn.mount_face_center,
                web_inner_x=web_inner_x,
                y_min=bottom_y_min,
                y_max=bottom_y_max,
                cap_radius=cap_radius,
            )
        )
    )
    bracket = bracket.intersect(cap_mask)

    top_start_y = top_y_max + CUT_EXTENSION_MM
    bracket = _cut_round_horn_face_holes(
        bracket,
        horn=upper_horn,
        start_y=top_start_y,
        direction_y=-1.0,
        rule=rule,
    )

    bottom_start_y = bottom_y_min - CUT_EXTENSION_MM
    bracket = _cut_round_horn_face_holes(
        bracket,
        horn=lower_horn,
        start_y=bottom_start_y,
        direction_y=1.0,
        rule=rule,
    )

    bracket = _cut_round_web_holes(
        bracket,
        layout=layout,
        horn=upper_horn,
        rule=rule,
    )
    metrics = _build_metrics(
        upper_horn=upper_horn,
        lower_horn=lower_horn,
        layout=layout,
        rule=rule,
    )
    if validate_metrics:
        _validate_metrics(metrics, rule=rule)
    return bracket, layout, metrics


def build_flat_pattern(
    reference_face_x: float,
    *,
    sheet_half_width_z: float,
    upper_horn: HornMountSpec,
    lower_horn: HornMountSpec,
    rule: BendRule,
) -> tuple[FlatPattern, BracketMetrics]:
    if abs(upper_horn.mount_face_center.Z - lower_horn.mount_face_center.Z) > 1e-6:
        raise RuntimeError("Flat-pattern export currently requires the horn faces to share the same Z centerline")

    layout, cap_radius = _build_layout(
        reference_face_x,
        sheet_half_width_z=sheet_half_width_z,
        upper_horn=upper_horn,
        lower_horn=lower_horn,
        rule=rule,
    )
    metrics = _build_metrics(
        upper_horn=upper_horn,
        lower_horn=lower_horn,
        layout=layout,
        rule=rule,
    )
    _validate_metrics(metrics, rule=rule)

    top_end_x = upper_horn.mount_face_center.X + cap_radius
    bottom_end_x = lower_horn.mount_face_center.X + cap_radius
    top_outside_length = top_end_x - layout.web_outer_x
    bottom_outside_length = bottom_end_x - layout.web_outer_x
    top_flat_length = top_outside_length - rule.outside_setback_90_mm
    bottom_flat_length = bottom_outside_length - rule.outside_setback_90_mm

    bend_allowance = rule.bend_allowance_90_mm
    top_web_tangent_y = layout.top_y_min - rule.effective_inside_radius_mm
    bottom_web_tangent_y = layout.bottom_y_max + rule.effective_inside_radius_mm
    web_flat_length = top_web_tangent_y - bottom_web_tangent_y
    total_flat_length = top_flat_length + bend_allowance + web_flat_length + bend_allowance + bottom_flat_length
    flat_width = layout.global_z_max - layout.global_z_min
    top_bend_line_x = top_flat_length + (0.5 * bend_allowance)
    bottom_bend_line_x = top_flat_length + bend_allowance + web_flat_length + (0.5 * bend_allowance)

    upper_hole_profiles = tuple(
        CircleProfileSpec(
            center_x=top_end_x - center.X,
            center_y=center.Z - layout.global_z_min,
            radius=radius,
        )
        for center, radius in _horn_face_hole_specs(upper_horn)
    )
    web_hole_profiles = tuple(
        CircleProfileSpec(
            center_x=top_flat_length + bend_allowance + (top_web_tangent_y - hole.center_y),
            center_y=hole.center_z - layout.global_z_min,
            radius=hole.radius,
        )
        for hole in _web_round_hole_specs(layout, horn=upper_horn)
    )
    lower_hole_profiles = tuple(
        CircleProfileSpec(
            center_x=total_flat_length - (bottom_end_x - center.X),
            center_y=center.Z - layout.global_z_min,
            radius=radius,
        )
        for center, radius in _horn_face_hole_specs(lower_horn)
    )
    round_hole_profiles = upper_hole_profiles + web_hole_profiles + lower_hole_profiles

    return (
        FlatPattern(
            total_length_mm=total_flat_length,
            width_mm=flat_width,
            cap_radius_mm=cap_radius,
            bend_line_positions_mm=(top_bend_line_x, bottom_bend_line_x),
            round_hole_profiles=round_hole_profiles,
        ),
        metrics,
    )


def _build_flat_pattern_dxf(flat_pattern: FlatPattern) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2010", setup=True)
    doc.units = DXF_UNIT_MM
    if "CUT" not in doc.layers:
        doc.layers.add("CUT")
    if "BEND" not in doc.layers:
        doc.layers.add("BEND", linetype="DASHED")

    msp = doc.modelspace()
    radius = flat_pattern.cap_radius_mm
    width = flat_pattern.width_mm
    total_length = flat_pattern.total_length_mm

    msp.add_line((radius, 0.0), (total_length - radius, 0.0), dxfattribs={"layer": "CUT"})
    msp.add_arc(
        center=(total_length - radius, radius),
        radius=radius,
        start_angle=-90.0,
        end_angle=90.0,
        dxfattribs={"layer": "CUT"},
    )
    msp.add_line((total_length - radius, width), (radius, width), dxfattribs={"layer": "CUT"})
    msp.add_arc(
        center=(radius, radius),
        radius=radius,
        start_angle=90.0,
        end_angle=270.0,
        dxfattribs={"layer": "CUT"},
    )

    for round_hole in flat_pattern.round_hole_profiles:
        msp.add_circle(
            (round_hole.center_x, round_hole.center_y),
            radius=round_hole.radius,
            dxfattribs={"layer": "CUT"},
        )

    for bend_line_x in flat_pattern.bend_line_positions_mm:
        msp.add_line(
            (bend_line_x, 0.0),
            (bend_line_x, width),
            dxfattribs={"layer": "BEND", "linetype": "DASHED"},
        )
    return doc


def build_step() -> build123d.Shape:
    CAD_DIR.mkdir(parents=True, exist_ok=True)
    inputs = _load_generator_inputs()
    bracket, layout, metrics = build_bracket(
        inputs.reference_face_x,
        sheet_half_width_z=inputs.sheet_half_width_z,
        upper_horn=inputs.upper_horn,
        lower_horn=inputs.lower_horn,
        rule=SHEET_RULE,
    )

    solids = bracket.solids()
    if len(solids) != 1:
        raise RuntimeError(f"Expected one connected bracket solid, found {len(solids)}")

    interference_volume = inputs.servo_shape.intersect(bracket).volume
    if interference_volume > 1e-3:
        raise RuntimeError(f"Bracket intersects the servo by {interference_volume:.6f} mm^3")

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
        "Connection features "
        f"face_patterns=3, "
        f"m3_holes_per_pattern=4x{2.0 * M3_CLEARANCE_RADIUS_MM:.3f} mm, "
        f"center_hole_diameter={2.0 * HORN_CENTER_CLEARANCE_RADIUS_MM:.3f} mm"
    )
    print(
        "SendCutSend bend rule "
        f"material={SHEET_RULE.material_name}, "
        f"thickness={SHEET_RULE.thickness_mm:.3f} mm, "
        f"inside_radius={SHEET_RULE.effective_inside_radius_mm:.3f} mm, "
        f"outer_radius={SHEET_RULE.outer_radius_mm:.3f} mm, "
        f"k_factor={SHEET_RULE.k_factor:.2f}, "
        f"bend_deduction_90={SHEET_RULE.bend_deduction_90_mm:.3f} mm"
    )
    print(
        "SendCutSend checks "
        f"flange_outside_min={metrics.min_flange_outside_length_mm:.3f} mm, "
        f"flange_flat_min={metrics.min_flange_flat_length_mm:.3f} mm, "
        f"bend_spacing={metrics.bend_line_spacing_mm:.3f} mm, "
        f"hole_edge_to_bend_min={metrics.min_hole_edge_to_bend_mm:.3f} mm, "
        f"web_feature_edge_to_bend_min={metrics.min_web_feature_edge_to_bend_mm:.3f} mm, "
        f"base_to_flange_ratio={metrics.base_to_flange_ratio:.3f}"
    )
    print(
        "Bracket placement "
        f"inner_web_x={layout.web_inner_x:.3f} mm, "
        f"outer_web_x={layout.web_outer_x:.3f} mm, "
        f"yoke_length={YOKE_HORN_AXIS_TO_WEB_OUTER_LENGTH_MM:.3f} mm, "
        f"reference_face_x={inputs.reference_face_x:.3f} mm, "
        f"reference_clearance={inputs.reference_face_x - layout.web_inner_x:.3f} mm"
    )
    print(
        "Upper horn "
        f"selector={UPPER_HORN_SELECTOR}, face_y={inputs.upper_horn.mount_face_y:.3f} mm, "
        f"m3_holes=4, center_hole_radius={_center_hole_radius(inputs.upper_horn):.3f} mm"
    )
    print(
        "Lower horn "
        f"selector={LOWER_HORN_SELECTOR}, face_y={inputs.lower_horn.mount_face_y:.3f} mm, "
        f"m3_holes=4, center_hole_radius={_center_hole_radius(inputs.lower_horn):.3f} mm"
    )
    print(f"Servo/bracket interference volume (mm^3): {interference_volume:.6f}")
    return bracket


def build_dxf() -> ezdxf.document.Drawing:
    CAD_DIR.mkdir(parents=True, exist_ok=True)
    inputs = _load_generator_inputs()
    flat_pattern, metrics = build_flat_pattern(
        inputs.reference_face_x,
        sheet_half_width_z=inputs.sheet_half_width_z,
        upper_horn=inputs.upper_horn,
        lower_horn=inputs.lower_horn,
        rule=SHEET_RULE,
    )
    doc = _build_flat_pattern_dxf(flat_pattern)
    print(
        "Flat pattern "
        f"length={flat_pattern.total_length_mm:.3f} mm, "
        f"width={flat_pattern.width_mm:.3f} mm, "
        f"bend_lines=({flat_pattern.bend_line_positions_mm[0]:.3f}, {flat_pattern.bend_line_positions_mm[1]:.3f}) mm, "
        f"round_holes={len(flat_pattern.round_hole_profiles)}"
    )
    print(
        "DXF checks "
        f"flange_flat_min={metrics.min_flange_flat_length_mm:.3f} mm, "
        f"bend_spacing={metrics.bend_line_spacing_mm:.3f} mm, "
        f"hole_edge_to_bend_min={metrics.min_hole_edge_to_bend_mm:.3f} mm, "
        f"web_feature_edge_to_bend_min={metrics.min_web_feature_edge_to_bend_mm:.3f} mm"
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
