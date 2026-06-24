"""Movie-inspired Optimus Prime CAD figurine.

Intent:
- A recognizable, blocky movie-era Optimus Prime interpretation.
- Emphasize the truck-cab chest, broad shoulders, blue/silver helmet,
  heavier thighs/shins, and heroic stance.

Frame:
- Origin midway between the feet on the floor plane.
- +X forward, +Y robot-left, +Z up.
- Units: millimeters.
"""

from __future__ import annotations

from build123d import Box, Color, Compound, Cylinder, Pos, Rot

RED = Color(0.78, 0.12, 0.12, 1.0)
BLUE = Color(0.12, 0.33, 0.80, 1.0)
SILVER = Color(0.82, 0.83, 0.85, 1.0)
DARK = Color(0.17, 0.18, 0.20, 1.0)
GRAY = Color(0.58, 0.60, 0.64, 1.0)
WINDOW = Color(0.12, 0.56, 0.90, 1.0)
BLACK = Color(0.10, 0.11, 0.12, 1.0)


def _label(shape, label: str, color: Color):
    shape.label = label
    shape.color = color
    return shape


def _move(shape, x: float, y: float, z: float, label: str, color: Color):
    return _label(shape.moved(Pos(x, y, z)), label, color)


def _foot(side: int):
    tag = "left" if side > 0 else "right"
    y = side * 27.0
    parts = []
    parts.append(_move(Box(58.0, 24.0, 10.0), 0.0, y, 5.0, f"foot_sole_{tag}", BLUE))
    parts.append(_move(Box(26.0, 18.0, 8.0), 20.0, y, 8.0, f"toe_{tag}", RED))
    parts.append(_move(Box(18.0, 16.0, 7.0), -18.0, y, 7.0, f"heel_{tag}", GRAY))
    parts.append(_move(Box(16.0, 12.0, 6.0), -2.0, y, 16.0, f"ankle_block_{tag}", DARK))
    return parts


def _shin(side: int):
    tag = "left" if side > 0 else "right"
    y = side * 21.0
    parts = []
    parts.append(_move(Box(22.0, 16.0, 50.0), 0.0, y, 41.0, f"shin_primary_{tag}", BLUE))
    parts.append(_move(Box(18.0, 12.0, 14.0), 0.0, y, 66.0, f"knee_cap_{tag}", GRAY))
    parts.append(_move(Box(10.0, 6.0, 22.0), -7.0, y, 51.0, f"shin_side_fin_{tag}", RED))
    parts.append(_move(Cylinder(radius=3.8, height=8.0), 8.0, y, 52.0, f"shin_wheel_{tag}", BLACK))
    return parts


def _thigh(side: int):
    tag = "left" if side > 0 else "right"
    y = side * 19.0
    parts = []
    parts.append(_move(Box(25.0, 18.0, 40.0), 0.0, y, 90.0, f"thigh_primary_{tag}", BLUE))
    parts.append(_move(Box(18.0, 14.0, 12.0), 2.0, y, 109.0, f"hip_plate_{tag}", RED))
    parts.append(_move(Cylinder(radius=4.4, height=10.0), -12.0, y, 94.0, f"thigh_wheel_{tag}", DARK))
    return parts


def _pelvis():
    parts = []
    parts.append(_move(Box(44.0, 34.0, 18.0), 0.0, 0.0, 123.0, "pelvis_core", RED))
    parts.append(_move(Box(30.0, 20.0, 10.0), 0.0, 0.0, 119.0, "waist_panel", DARK))
    parts.append(_move(Box(16.0, 10.0, 12.0), 0.0, 0.0, 111.0, "crotch_guard", SILVER))
    return parts


def _torso():
    parts = []
    # Movie-style truck cab chest: larger, wider, with a hood/radiator feel.
    parts.append(_move(Box(58.0, 42.0, 54.0), 0.0, 0.0, 166.0, "torso_cab", RED))
    parts.append(_move(Box(42.0, 30.0, 16.0), 9.0, 0.0, 191.0, "cab_roof", RED))
    # Window panels: larger and brighter.
    parts.append(_move(Box(18.0, 14.0, 18.0), 24.0, -11.0, 180.0, "window_left", WINDOW))
    parts.append(_move(Box(18.0, 14.0, 18.0), 24.0, 11.0, 180.0, "window_right", WINDOW))
    # Front grille and bumper give the chest its unmistakable vehicle face.
    parts.append(_move(Box(16.0, 20.0, 12.0), 27.0, 0.0, 161.0, "grille", GRAY))
    parts.append(_move(Box(22.0, 9.0, 7.0), 24.5, 0.0, 150.0, "bumper_bar", SILVER))
    # Shoulder armor and backpack.
    parts.append(_move(Box(18.0, 18.0, 14.0), -26.0, 30.0, 188.0, "shoulder_left_cap", RED))
    parts.append(_move(Box(18.0, 18.0, 14.0), -26.0, -30.0, 188.0, "shoulder_right_cap", RED))
    parts.append(_move(Box(16.0, 24.0, 38.0), -28.0, 0.0, 170.0, "backpack", DARK))
    # Smokestacks on the shoulders.
    parts.append(_move(Cylinder(radius=4.0, height=26.0), -6.0, 24.0, 189.0, "smokestack_left", SILVER))
    parts.append(_move(Cylinder(radius=4.0, height=26.0), -6.0, -24.0, 189.0, "smokestack_right", SILVER))
    return parts


def _arm(side: int):
    tag = "left" if side > 0 else "right"
    y = side * 37.0
    parts = []
    parts.append(_move(Box(20.0, 18.0, 42.0), 6.0, y, 150.0, f"upper_arm_{tag}", RED))
    parts.append(_move(Cylinder(radius=5.6, height=12.0), 16.0, y, 138.0, f"elbow_{tag}", SILVER))
    parts.append(_move(Box(18.0, 16.0, 38.0), 7.0, y, 115.0, f"forearm_{tag}", BLUE))
    parts.append(_move(Box(17.0, 14.0, 16.0), 9.0, y, 92.0, f"fist_{tag}", GRAY))
    parts.append(_move(Cylinder(radius=5.0, height=8.0), 17.0, y, 123.0, f"forearm_wheel_{tag}", BLACK))
    return parts


def _head():
    parts = []
    # Movie Optimus: blue helmet, silver faceplate, stronger brow and cheek area.
    parts.append(_move(Box(30.0, 24.0, 26.0), 0.0, 0.0, 206.0, "helmet_main", BLUE))
    parts.append(_move(Box(20.0, 10.0, 10.0), 0.0, 0.0, 218.0, "helmet_crown", RED))
    parts.append(_move(Box(16.0, 16.0, 14.0), 12.0, 0.0, 201.5, "faceplate", SILVER))
    parts.append(_move(Box(10.0, 3.5, 3.5), 14.0, 0.0, 210.0, "eye_band", WINDOW))
    parts.append(_move(Box(8.0, 6.0, 4.0), 3.0, 0.0, 214.0, "brow", BLACK))
    parts.append(_move(Box(5.0, 5.0, 11.0), -3.0, 10.0, 212.0, "antenna_left", SILVER))
    parts.append(_move(Box(5.0, 5.0, 11.0), -3.0, -10.0, 212.0, "antenna_right", SILVER))
    parts.append(_move(Box(6.0, 5.0, 8.0), 2.0, 0.0, 197.0, "chin", DARK))
    parts.append(_move(Box(6.0, 12.0, 5.0), -11.0, 0.0, 207.0, "helmet_fin_left", RED))
    parts.append(_move(Box(6.0, 12.0, 5.0), -11.0, 0.0, 201.0, "helmet_fin_right", RED))
    return parts


def gen_step():
    """Return a movie-inspired Optimus Prime assembly in millimeters."""
    parts = []
    parts.extend(_foot(+1))
    parts.extend(_foot(-1))
    parts.extend(_shin(+1))
    parts.extend(_shin(-1))
    parts.extend(_thigh(+1))
    parts.extend(_thigh(-1))
    parts.extend(_pelvis())
    parts.extend(_torso())
    parts.extend(_arm(+1))
    parts.extend(_arm(-1))
    parts.extend(_head())
    return Compound(obj=parts, children=parts, label="optimus_prime_stylized")
