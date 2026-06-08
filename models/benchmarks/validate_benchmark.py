from __future__ import annotations

from math import atan2, degrees, hypot
from pathlib import Path

from build123d import import_step


ROOT = Path(__file__).resolve().parent
BASIC_TOL = 0.2
COMPLEX_TOL = 0.5
ANGLE_TOL = 2.0


class BenchmarkFailure(AssertionError):
    pass


def close(actual: float, expected: float, tol: float = BASIC_TOL) -> bool:
    return abs(actual - expected) <= tol


def check(condition: bool, message: str) -> None:
    if not condition:
        raise BenchmarkFailure(message)


def bbox(shape):
    box = shape.bounding_box()
    return box.min, box.max, (
        box.max.X - box.min.X,
        box.max.Y - box.min.Y,
        box.max.Z - box.min.Z,
    )


def assert_bbox(shape, expected_size, expected_min=None, tol: float = BASIC_TOL):
    min_v, max_v, size = bbox(shape)
    for actual, expected, axis in zip(size, expected_size, "xyz"):
        check(close(actual, expected, tol), f"bbox {axis} size {actual:.3f} != {expected:.3f}")
    if expected_min is not None:
        for actual, expected, axis in zip((min_v.X, min_v.Y, min_v.Z), expected_min, "xyz"):
            check(close(actual, expected, tol), f"bbox min {axis} {actual:.3f} != {expected:.3f}")


def axis_name(direction) -> str:
    components = {"x": abs(direction.X), "y": abs(direction.Y), "z": abs(direction.Z)}
    return max(components, key=components.get)


def cyl_faces(shape, *, radius: float | None = None, axis: str | None = None, tol: float = BASIC_TOL):
    faces = []
    for face in shape.faces():
        if "CYLINDER" not in str(face.geom_type):
            continue
        if radius is not None and not close(face.radius, radius, tol):
            continue
        if axis is not None and axis_name(face.axis_of_rotation.direction) != axis:
            continue
        faces.append(face)
    return faces


def torus_count(shape) -> int:
    return sum(1 for face in shape.faces() if "TORUS" in str(face.geom_type))


def cone_count(shape) -> int:
    return sum(1 for face in shape.faces() if "CONE" in str(face.geom_type))


def cylinder_centers(shape, *, radius: float, axis: str):
    centers = []
    for face in cyl_faces(shape, radius=radius, axis=axis):
        pos = face.axis_of_rotation.position
        centers.append((pos.X, pos.Y, pos.Z))
    return centers


def expect_cylinder_at(shape, *, radius: float, axis: str, x=None, y=None, z=None, tol: float = BASIC_TOL):
    for center in cylinder_centers(shape, radius=radius, axis=axis):
        if x is not None and not close(center[0], x, tol):
            continue
        if y is not None and not close(center[1], y, tol):
            continue
        if z is not None and not close(center[2], z, tol):
            continue
        return
    raise BenchmarkFailure(f"missing cylinder radius {radius} axis {axis} at x={x} y={y} z={z}")


def plane_faces_at(shape, *, axis: str, coordinate: float, tol: float = BASIC_TOL):
    index = "xyz".index(axis)
    faces = []
    for face in shape.faces():
        if "PLANE" not in str(face.geom_type):
            continue
        box = face.bounding_box()
        mins = (box.min.X, box.min.Y, box.min.Z)
        maxs = (box.max.X, box.max.Y, box.max.Z)
        if close(mins[index], coordinate, tol) and close(maxs[index], coordinate, tol):
            faces.append(face)
    return faces


def assert_common(shape, *, solids: int | None = None, min_solids: int | None = None):
    check(shape.is_valid, "STEP imports but imported B-rep is invalid")
    check(shape.is_manifold, "STEP imports but B-rep is non-manifold")
    if solids is not None:
        check(len(shape.solids()) == solids, f"solid count {len(shape.solids())} != {solids}")
    if min_solids is not None:
        check(len(shape.solids()) >= min_solids, f"solid count {len(shape.solids())} < {min_solids}")


def validate_01(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (100.0, 60.0, 20.0), (-50.0, -30.0, 0.0))
    for x in (-35.0, 35.0):
        for y in (-20.0, 20.0):
            expect_cylinder_at(shape, radius=4.0, axis="z", x=x, y=y)
    check(len(cyl_faces(shape, radius=4.0, axis="z")) == 4, "expected four 8 mm through holes")
    top_faces = plane_faces_at(shape, axis="z", coordinate=20.0)
    check(any(close(face.bounding_box().size.X, 96.0) and close(face.bounding_box().size.Y, 56.0) for face in top_faces), "top chamfered face is not inset by 2 mm")
    check(cone_count(shape) == 0 and torus_count(shape) == 0, "unexpected hole chamfers/roundovers")


def validate_02(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (80.0, 80.0, 10.0), (-40.0, -40.0, 0.0))
    expect_cylinder_at(shape, radius=15.0, axis="z", x=0.0, y=0.0)
    bolt_centers = cylinder_centers(shape, radius=3.0, axis="z")
    check(len(bolt_centers) == 6, "expected six bolt holes")
    angles = sorted(degrees(atan2(y, x)) % 360.0 for x, y, _ in bolt_centers)
    for x, y, _ in bolt_centers:
        check(close(hypot(x, y), 30.0, COMPLEX_TOL), "bolt hole is off 60 mm bolt circle")
    gaps = [(angles[(i + 1) % 6] - angles[i]) % 360.0 for i in range(6)]
    check(all(close(gap, 60.0, ANGLE_TOL) for gap in gaps), "bolt holes are not spaced 60 degrees apart")
    check(torus_count(shape) >= 2, "missing top/bottom outer edge fillets")


def validate_03(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (80.0, 50.0, 58.0), (-40.0, -25.0, 0.0))
    for x in (-25.0, 25.0):
        expect_cylinder_at(shape, radius=3.0, axis="z", x=x, y=-10.0)
        expect_cylinder_at(shape, radius=3.0, axis="y", x=x, z=30.0)
    check(len(cyl_faces(shape, radius=3.0, axis="z")) == 2, "base hole count is not two")
    check(len(cyl_faces(shape, radius=3.0, axis="y")) == 2, "back plate hole count is not two")
    check(len(shape.faces()) >= 20, "missing gusset/fillet detail")


def validate_04(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (120.0, 30.0, 30.0), (0.0, -15.0, -15.0))
    check(len(cyl_faces(shape, radius=10.0, axis="x")) == 2, "expected two 20 mm shaft sections")
    check(len(cyl_faces(shape, radius=15.0, axis="x")) == 1, "expected one 30 mm shaft section")
    keyway_planes = plane_faces_at(shape, axis="z", coordinate=12.0)
    check(any(close(face.bounding_box().size.X, 40.0) and close(face.bounding_box().size.Y, 6.0) for face in keyway_planes), "keyway floor is not 40 x 6 mm at 3 mm depth")
    check(cone_count(shape) >= 2, "missing 1 mm end chamfers")


def validate_05(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (100.0, 70.0, 30.0), (-50.0, -35.0, 0.0))
    check(any(close(face.bounding_box().size.X, 94.0) and close(face.bounding_box().size.Y, 64.0) for face in plane_faces_at(shape, axis="z", coordinate=3.0)), "inside floor/wall thickness check failed")
    for x in (-35.0, 35.0):
        for y in (-25.0, 25.0):
            expect_cylinder_at(shape, radius=5.0, axis="z", x=x, y=y)
            expect_cylinder_at(shape, radius=1.5, axis="z", x=x, y=y)
    check(len(cyl_faces(shape, radius=5.0, axis="z")) == 4, "standoff count is not four")
    check(len(cyl_faces(shape, radius=1.5, axis="z")) == 4, "blind hole count is not four")
    check(len(cyl_faces(shape, radius=2.0, axis="z")) >= 4, "missing 2 mm outside vertical corner fillets")


def validate_06(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (120.0, 60.0, 52.0), (-60.0, -30.0, 0.0))
    expect_cylinder_at(shape, radius=7.0, axis="y", x=0.0, z=34.0)
    for x in (-45.0, 45.0):
        for y in (-20.0, 20.0):
            expect_cylinder_at(shape, radius=3.5, axis="z", x=x, y=y)
    check(len(cyl_faces(shape, radius=7.0, axis="y")) == 2, "clevis through-hole should pass through two separate lugs")
    check(len(cyl_faces(shape, radius=3.5, axis="z")) == 4, "base mounting hole count is not four")
    check(len(cyl_faces(shape, radius=2.0, axis="x")) >= 2, "missing lug-to-base radius detail")
    check(len(shape.faces()) >= 90, "missing cutout/rib/lug detail")


def validate_07(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (79.1, 70.0, 92.7), (-35.0, -35.0, 0.0), tol=COMPLEX_TOL)
    check(len(cyl_faces(shape, radius=31.0, axis="z")) == 12, "cooling fin count is not 12")
    check(len(cyl_faces(shape, radius=2.5, axis="z")) == 6, "mounting hole count is not six")
    check(len(cyl_faces(shape, radius=18.0, axis="z")) >= 1, "missing 36 mm barrel")
    check(len(cyl_faces(shape, radius=22.0, axis="z")) == 1, "missing 44 mm top cap")
    boss = cyl_faces(shape, radius=6.0)
    bore = [face for face in cyl_faces(shape, radius=2.5) if axis_name(face.axis_of_rotation.direction) == "x"]
    check(len(boss) == 1 and len(bore) == 1, "missing angled boss or boss bore")
    for face in (boss[0], bore[0]):
        direction = face.axis_of_rotation.direction
        angle = degrees(atan2(abs(direction.Z), abs(direction.X)))
        check(close(angle, 35.0, ANGLE_TOL), "boss axis is not 35 degrees upward")
    check(torus_count(shape) >= 24, "missing fin/flange edge fillets")


def validate_08(shape):
    assert_common(shape, solids=1)
    assert_bbox(shape, (90.0, 90.0, 28.0), (-45.0, -45.0, 0.0), tol=COMPLEX_TOL)
    expect_cylinder_at(shape, radius=4.0, axis="z", x=0.0, y=0.0)
    check(len(cyl_faces(shape, radius=13.0, axis="z")) == 1, "missing 26 mm hub")
    check(len(shape.faces()) >= 220, "missing repeated curved blade detail")
    check(torus_count(shape) >= 2, "missing backplate edge fillets")


def validate_09(shape):
    assert_common(shape, min_solids=43)
    assert_bbox(shape, (137.0, 137.0, 140.0), (-68.5, -68.5, 0.0), tol=COMPLEX_TOL)
    check(len(cyl_faces(shape, radius=7.0, axis="z")) == 1, "missing central column")
    check(len(cyl_faces(shape, radius=45.0, axis="z")) == 1, "missing base disk")
    check(len(cyl_faces(shape, radius=62.0, axis="z")) == 20, "tread outer radius/count failed")
    check(len(cyl_faces(shape, radius=10.0, axis="z")) == 20, "tread inner radius/count failed")
    check(len(cyl_faces(shape, radius=1.5, axis="z")) == 20, "baluster count failed")
    check(len(shape.solids()) == 43, "staircase assembly should have 43 solids")


def validate_10(shape):
    assert_common(shape, solids=9)
    assert_bbox(shape, (140.0, 140.0, 14.0), (-70.0, -70.0, -5.0), tol=COMPLEX_TOL)
    check(len(cyl_faces(shape, radius=3.0, axis="z")) == 3, "planet pin count failed")
    check(len(cyl_faces(shape, radius=5.0, axis="z")) == 1, "sun bore failed")
    check(len(shape.faces()) >= 550 and len(shape.edges()) >= 1600, "gear tooth detail is missing")


VALIDATORS = [
    ("1. Rectangular calibration block with four holes", "benchmark_01_rectangular_calibration_block.step", validate_01),
    ("2. Circular flange with bolt-hole pattern", "benchmark_02_circular_flange.step", validate_02),
    ("3. L-bracket with gussets and two hole directions", "benchmark_03_l_bracket.step", validate_03),
    ("4. Stepped shaft with keyway", "benchmark_04_stepped_shaft_keyway.step", validate_04),
    ("5. Open-top electronics enclosure with bosses", "benchmark_05_open_top_electronics_enclosure.step", validate_05),
    ("6. Aerospace-style clevis bracket with lightening cutouts", "benchmark_06_clevis_bracket_lightening_cutouts.step", validate_06),
    ("7. Radial-engine-style cylinder with cooling fins", "benchmark_07_radial_engine_cylinder.step", validate_07),
    ("8. Centrifugal impeller with backward-curved blades", "benchmark_08_centrifugal_impeller.step", validate_08),
    ("9. Spiral staircase with helical handrail", "benchmark_09_spiral_staircase.step", validate_09),
    ("10. Simplified planetary gear stage", "benchmark_10_planetary_gear_stage.step", validate_10),
]


def main() -> int:
    failures = []
    for title, filename, validator in VALIDATORS:
        step_path = ROOT / filename
        try:
            shape = import_step(step_path)
            validator(shape)
        except Exception as exc:
            failures.append((title, exc))
            print(f"FAIL {title}: {exc}")
        else:
            print(f"PASS {title}")

    if failures:
        print(f"\n{len(failures)} benchmark(s) failed.")
        return 1

    print("\nAll benchmark checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
