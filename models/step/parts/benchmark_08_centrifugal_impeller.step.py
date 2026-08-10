from math import asin, cos, hypot, radians, sin, tau

from build123d import (
    Align,
    BuildPart,
    BuildSketch,
    Cylinder,
    Location,
    Locations,
    Mode,
    Plane,
    Polygon,
    add,
    extrude,
)

from benchmark_common import circular_edges, polar_point, safe_fillet


BACKPLATE_DIAMETER = 90.0
BACKPLATE_THICKNESS = 6.0
BACKPLATE_FILLET = 1.5
HUB_DIAMETER = 26.0
HUB_HEIGHT = 22.0
BORE_DIAMETER = 8.0

BLADE_COUNT = 12
BLADE_ROOT_RADIUS = 18.0
BLADE_TIP_RADIUS = 43.0
BLADE_THICKNESS = 3.0
BLADE_HEIGHT = 16.0
BLADE_BACKWARD_SWEEP = radians(45.0)
BLADE_SEGMENTS = 5
BLADE_ROOT_FILLET = 1.0


def _clamp_radius(point: tuple[float, float]) -> tuple[float, float]:
    radius = hypot(point[0], point[1])
    if radius < BLADE_ROOT_RADIUS:
        scale = BLADE_ROOT_RADIUS / radius
        return (point[0] * scale, point[1] * scale)
    if radius > BLADE_TIP_RADIUS:
        scale = BLADE_TIP_RADIUS / radius
        return (point[0] * scale, point[1] * scale)
    return point


def _blade_center(base_angle: float, t: float) -> tuple[float, float]:
    radius = BLADE_ROOT_RADIUS + (BLADE_TIP_RADIUS - BLADE_ROOT_RADIUS) * t
    angle = base_angle - BLADE_BACKWARD_SWEEP * t
    return polar_point(radius, angle)


def _blade_normal(base_angle: float, t: float) -> tuple[float, float]:
    radius = BLADE_ROOT_RADIUS + (BLADE_TIP_RADIUS - BLADE_ROOT_RADIUS) * t
    dr_dt = BLADE_TIP_RADIUS - BLADE_ROOT_RADIUS
    dtheta_dt = -BLADE_BACKWARD_SWEEP
    angle = base_angle + dtheta_dt * t
    dx_dt = dr_dt * cos(angle) - radius * sin(angle) * dtheta_dt
    dy_dt = dr_dt * sin(angle) + radius * cos(angle) * dtheta_dt
    length = hypot(dx_dt, dy_dt)
    return (-dy_dt / length, dx_dt / length)


def _blade_end_points(base_angle: float, t: float) -> tuple[tuple[float, float], tuple[float, float]]:
    radius = BLADE_ROOT_RADIUS if t == 0.0 else BLADE_TIP_RADIUS
    angle = base_angle - BLADE_BACKWARD_SWEEP * t
    half_angle = asin((BLADE_THICKNESS / 2.0) / radius)
    return (polar_point(radius, angle + half_angle), polar_point(radius, angle - half_angle))


def _blade_outline(base_angle: float) -> list[tuple[float, float]]:
    left_side = []
    right_side = []
    half_thickness = BLADE_THICKNESS / 2.0

    for index in range(BLADE_SEGMENTS + 1):
        t = index / BLADE_SEGMENTS
        if index == 0 or index == BLADE_SEGMENTS:
            left, right = _blade_end_points(base_angle, t)
        else:
            center = _blade_center(base_angle, t)
            normal = _blade_normal(base_angle, t)
            left = _clamp_radius((center[0] + normal[0] * half_thickness, center[1] + normal[1] * half_thickness))
            right = _clamp_radius((center[0] - normal[0] * half_thickness, center[1] - normal[1] * half_thickness))
        left_side.append(left)
        right_side.append(right)

    return left_side + list(reversed(right_side))


def _make_blade(index: int):
    base_angle = tau * index / BLADE_COUNT
    with BuildPart() as blade:
        with BuildSketch(Plane.XY):
            Polygon(_blade_outline(base_angle), align=None)
        extrude(amount=BLADE_HEIGHT)

    part = blade.part.moved(Location((0.0, 0.0, BACKPLATE_THICKNESS)))
    part.label = f"backward_curved_blade_{index + 1:02d}"
    return part


def _blade_root_edges(part):
    edges = []
    for edge in part.edges():
        bbox = edge.bounding_box()
        center = bbox.center()
        if abs(center.Z - BACKPLATE_THICKNESS) > 0.05:
            continue
        radial_distance = hypot(center.X, center.Y)
        if BLADE_ROOT_RADIUS - 3.0 <= radial_distance <= 30.0:
            if str(edge.geom_type).endswith("LINE"):
                edges.append(edge)
    return edges


def gen_step():
    """Return the centrifugal impeller benchmark model in millimeters."""
    total_height = BACKPLATE_THICKNESS + HUB_HEIGHT

    with BuildPart() as impeller:
        Cylinder(
            radius=BACKPLATE_DIAMETER / 2.0,
            height=BACKPLATE_THICKNESS,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        for blade_index in range(BLADE_COUNT):
            add(_make_blade(blade_index), mode=Mode.ADD)
        with Locations(Location((0.0, 0.0, BACKPLATE_THICKNESS))):
            Cylinder(
                radius=HUB_DIAMETER / 2.0,
                height=HUB_HEIGHT,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.ADD,
            )
        with Locations(Location((0.0, 0.0, -1.0))):
            Cylinder(
                radius=BORE_DIAMETER / 2.0,
                height=total_height + 2.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT,
            )

    part = impeller.part
    backplate_edges = []
    backplate_edges.extend(circular_edges(part, radius=BACKPLATE_DIAMETER / 2.0, axis="z", coordinate=0.0))
    backplate_edges.extend(circular_edges(part, radius=BACKPLATE_DIAMETER / 2.0, axis="z", coordinate=BACKPLATE_THICKNESS))
    part = safe_fillet(part, backplate_edges, radius=BACKPLATE_FILLET)
    part = safe_fillet(part, _blade_root_edges(part), radius=BLADE_ROOT_FILLET)
    part.label = "benchmark_08_centrifugal_impeller_backward_curved_blades"
    return part
