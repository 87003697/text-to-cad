from __future__ import annotations

from dataclasses import dataclass
import math

import build123d


# Extracted from the legacy keyed plate profile.
# Coordinates are relative to the cut center in an arbitrary in-plane basis `(u, v)`.


@dataclass(frozen=True)
class KeyedConnectionArcSpec:
    start_u: float
    start_v: float
    mid_u: float
    mid_v: float
    end_u: float
    end_v: float


KEYED_CONNECTION_REF = "legacy keyed plate profile"
KEYED_CONNECTION_ROTATION_DEG = 45.0

KEYED_CONNECTION_ARC_SPECS_MM: tuple[KeyedConnectionArcSpec, ...] = (
    KeyedConnectionArcSpec(-2.634358, -4.828056, 0.0, -5.499997, 2.634358, -4.828056),
    KeyedConnectionArcSpec(2.634358, -4.828056, 3.095745, -4.818908, 3.368107, -5.19144),
    KeyedConnectionArcSpec(3.368107, -5.19144, 6.081117, -6.081114, 5.191444, -3.368104),
    KeyedConnectionArcSpec(5.191444, -3.368104, 4.818911, -3.095741, 4.82806, -2.634354),
    KeyedConnectionArcSpec(4.82806, -2.634354, 5.5, 0.000004, 4.82806, 2.634361),
    KeyedConnectionArcSpec(4.82806, 2.634361, 4.818911, 3.095748, 5.191444, 3.36811),
    KeyedConnectionArcSpec(5.191444, 3.36811, 6.081117, 6.08112, 3.368107, 5.191447),
    KeyedConnectionArcSpec(3.368107, 5.191447, 3.095745, 4.818915, 2.634358, 4.828063),
    KeyedConnectionArcSpec(2.634358, 4.828063, 0.0, 5.500003, -2.634358, 4.828063),
    KeyedConnectionArcSpec(-2.634358, 4.828063, -3.095745, 4.818915, -3.368107, 5.191447),
    KeyedConnectionArcSpec(-3.368107, 5.191447, -6.081117, 6.08112, -5.191444, 3.36811),
    KeyedConnectionArcSpec(-5.191444, 3.36811, -4.818911, 3.095748, -4.82806, 2.634361),
    KeyedConnectionArcSpec(-4.82806, 2.634361, -5.5, 0.000004, -4.82806, -2.634354),
    KeyedConnectionArcSpec(-4.82806, -2.634354, -4.818911, -3.095741, -5.191444, -3.368104),
    KeyedConnectionArcSpec(-5.191444, -3.368104, -6.081117, -6.081114, -3.368107, -5.19144),
    KeyedConnectionArcSpec(-3.368107, -5.19144, -3.095745, -4.818908, -2.634358, -4.828056),
)

# Conservative exact-profile half-span for bend-clearance checks. The exported
# helper rotates this profile 45 degrees by default to match the plate cut
# orientation used across the current parts.
KEYED_CONNECTION_HALF_SPAN_MM = 6.549746


def _to_global_point(
    *,
    origin: build123d.Vector,
    u_dir: build123d.Vector,
    v_dir: build123d.Vector,
    u: float,
    v: float,
) -> build123d.Vector:
    return build123d.Vector(
        origin.X + (u * u_dir.X) + (v * v_dir.X),
        origin.Y + (u * u_dir.Y) + (v * v_dir.Y),
        origin.Z + (u * u_dir.Z) + (v * v_dir.Z),
    )


def _rotated_in_plane_dirs(
    *,
    u_dir: build123d.Vector,
    v_dir: build123d.Vector,
    rotation_degrees: float,
) -> tuple[build123d.Vector, build123d.Vector]:
    if abs(rotation_degrees) <= 1e-9:
        return u_dir, v_dir

    rotation_rad = math.radians(rotation_degrees)
    cos_theta = math.cos(rotation_rad)
    sin_theta = math.sin(rotation_rad)
    rotated_u = (u_dir * cos_theta) + (v_dir * sin_theta)
    rotated_v = (v_dir * cos_theta) - (u_dir * sin_theta)
    return rotated_u, rotated_v


def make_keyed_connection_wire(
    *,
    origin: build123d.Vector,
    u_dir: build123d.Vector,
    v_dir: build123d.Vector,
    rotation_degrees: float = KEYED_CONNECTION_ROTATION_DEG,
) -> build123d.Wire:
    rotated_u_dir, rotated_v_dir = _rotated_in_plane_dirs(
        u_dir=u_dir,
        v_dir=v_dir,
        rotation_degrees=rotation_degrees,
    )
    edges = [
        build123d.Edge.make_three_point_arc(
            _to_global_point(origin=origin, u_dir=rotated_u_dir, v_dir=rotated_v_dir, u=spec.start_u, v=spec.start_v),
            _to_global_point(origin=origin, u_dir=rotated_u_dir, v_dir=rotated_v_dir, u=spec.mid_u, v=spec.mid_v),
            _to_global_point(origin=origin, u_dir=rotated_u_dir, v_dir=rotated_v_dir, u=spec.end_u, v=spec.end_v),
        )
        for spec in KEYED_CONNECTION_ARC_SPECS_MM
    ]
    wires = list(build123d.Wire.combine(edges))
    if len(wires) != 1:
        raise RuntimeError(f"Expected a single keyed-connection wire, found {len(wires)}")
    return wires[0]


def make_keyed_connection_face(
    *,
    origin: build123d.Vector,
    u_dir: build123d.Vector,
    v_dir: build123d.Vector,
    rotation_degrees: float = KEYED_CONNECTION_ROTATION_DEG,
) -> build123d.Face:
    return build123d.Face(
        make_keyed_connection_wire(
            origin=origin,
            u_dir=u_dir,
            v_dir=v_dir,
            rotation_degrees=rotation_degrees,
        )
    )


def make_keyed_connection_y_aligned_cutter(
    *,
    center: build123d.Vector,
    start_y: float,
    direction_y: float,
    thickness_mm: float,
    cut_extension_mm: float,
    rotation_degrees: float = KEYED_CONNECTION_ROTATION_DEG,
) -> build123d.Solid:
    length = thickness_mm + (2.0 * cut_extension_mm)
    face = make_keyed_connection_face(
        origin=build123d.Vector(center.X, start_y, center.Z),
        u_dir=build123d.Vector(1.0, 0.0, 0.0),
        v_dir=build123d.Vector(0.0, 0.0, 1.0),
        rotation_degrees=rotation_degrees,
    )
    return build123d.Solid.extrude(face, build123d.Vector(0.0, direction_y * length, 0.0))


def make_keyed_connection_x_aligned_cutter(
    *,
    center: build123d.Vector,
    start_x: float,
    direction_x: float,
    thickness_mm: float,
    cut_extension_mm: float,
    rotation_degrees: float = KEYED_CONNECTION_ROTATION_DEG,
) -> build123d.Solid:
    length = thickness_mm + (2.0 * cut_extension_mm)
    face = make_keyed_connection_face(
        origin=build123d.Vector(start_x, center.Y, center.Z),
        u_dir=build123d.Vector(0.0, 1.0, 0.0),
        v_dir=build123d.Vector(0.0, 0.0, 1.0),
        rotation_degrees=rotation_degrees,
    )
    return build123d.Solid.extrude(face, build123d.Vector(direction_x * length, 0.0, 0.0))


def cut_keyed_connection_y_aligned(
    shape: build123d.Shape,
    *,
    center: build123d.Vector,
    start_y: float,
    direction_y: float,
    thickness_mm: float,
    cut_extension_mm: float,
    rotation_degrees: float = KEYED_CONNECTION_ROTATION_DEG,
) -> build123d.Shape:
    return shape.cut(
        make_keyed_connection_y_aligned_cutter(
            center=center,
            start_y=start_y,
            direction_y=direction_y,
            thickness_mm=thickness_mm,
            cut_extension_mm=cut_extension_mm,
            rotation_degrees=rotation_degrees,
        )
    )


def cut_keyed_connection_x_aligned(
    shape: build123d.Shape,
    *,
    center: build123d.Vector,
    start_x: float,
    direction_x: float,
    thickness_mm: float,
    cut_extension_mm: float,
    rotation_degrees: float = KEYED_CONNECTION_ROTATION_DEG,
) -> build123d.Shape:
    return shape.cut(
        make_keyed_connection_x_aligned_cutter(
            center=center,
            start_x=start_x,
            direction_x=direction_x,
            thickness_mm=thickness_mm,
            cut_extension_mm=cut_extension_mm,
            rotation_degrees=rotation_degrees,
        )
    )


def sample_keyed_connection_outline(
    *,
    center_u: float,
    center_v: float,
    points_per_arc: int = 16,
    rotation_degrees: float = KEYED_CONNECTION_ROTATION_DEG,
) -> list[tuple[float, float]]:
    if points_per_arc < 2:
        raise ValueError("points_per_arc must be at least 2")

    wire = make_keyed_connection_wire(
        origin=build123d.Vector(center_u, center_v, 0.0),
        u_dir=build123d.Vector(1.0, 0.0, 0.0),
        v_dir=build123d.Vector(0.0, 1.0, 0.0),
        rotation_degrees=rotation_degrees,
    )
    points: list[tuple[float, float]] = []
    for edge_index, edge in enumerate(wire.edges()):
        start_step = 0 if edge_index == 0 else 1
        for step in range(start_step, points_per_arc):
            point = edge.position_at(step / (points_per_arc - 1))
            points.append((point.X, point.Y))
    if len(points) > 1 and math.dist(points[0], points[-1]) <= 1e-9:
        points.pop()
    return points
