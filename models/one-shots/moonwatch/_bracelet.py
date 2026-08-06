"""Flat three-link bracelet builder (moonwatch archetype).

Frame: WATCH assembly frame from `_spec` — +Z through the crystal, 12
o'clock at +Y, z = 0 at the case-middle/caseback joint. The bracelet
attaches at the spring-bar axes (y = +/-SPRING_BAR_Y, z = SPRING_BAR_Z)
and drapes outward along +/-Y with a progressive downward pitch, like a
bracelet resting over an invisible wrist form.

Construction vocabulary (all rows share it):

- A row is three separate bodies: two wider brushed outer links (STEEL)
  and a narrower polished center link (STEEL_BRIGHT).
- Row ends articulate on shared pin axes. At every joint the earlier
  row's OUTER links carry convex knuckle eyes (radius EYE_R, centered on
  the axis) and the later row's CENTER link carries the mating eye; the
  complementary ends are cut back with a concave recess of radius
  EYE_R + JOINT_CLEARANCE about the same axis, so any relative pitch
  articulates with a constant 0.06 radial clearance and zero
  interpenetration.
- Every joint gets a steel pin body (visible ends on the link flanks)
  passing through bores in the knuckle eyes.
- Width tapers linearly BRACELET_WIDTH_AT_LUG -> BRACELET_WIDTH_AT_CLASP
  over each strap; every link is planned as a trapezoid so the taper is
  smooth link to link.
- The outer surface of each row is trimmed by one large shared dome
  cylinder (gently domed flat-link profile) and edges get safe_chamfer
  ladders.
"""

from __future__ import annotations

import math

from build123d import (
    Box,
    Circle,
    Color,
    Compound,
    Cylinder,
    Plane,
    Polygon,
    Pos,
    RectangleRounded,
    Rot,
    extrude,
    loft,
)

import _spec as S
import _finishing as F

# ---------------------------------------------------------------------------
# Derived constants (shared dims come from _spec; only construction-local
# values are defined here)
# ---------------------------------------------------------------------------

T2 = S.LINK_THICKNESS / 2.0        # link half thickness (stadium radius)
EYE_R = T2                          # knuckle eye radius = half thickness
JOINT_CLEARANCE = 0.06              # radial clearance at every articulation
RECESS_R = EYE_R + JOINT_CLEARANCE  # concave mating cut radius
PIN_R = 0.9                         # link pin body radius
BORE_R = 0.95                       # knuckle bore radius (0.05 pin clearance)
CENTER_FRAC = 0.34                  # center link share of the row width
LINK_GAP = 0.12                     # lateral gap center <-> outer links
DOME_R = 400.0                      # outer-surface dome cylinder radius
BEVEL = 0.15                        # built-in 45-degree side-edge bevel
P = S.LINK_PITCH

# first pin axis (end link -> row 1), watch frame, +Y strap
JOINT1_Y = 26.0
JOINT1_Z = 2.6

# drape: rows 1-2 gentle, then ~9 deg more per row (invisible wrist form)
DRAPE_START = 3.0
DRAPE_SECOND = 6.0
DRAPE_STEP = 9.0
CLASP_PITCH_DEG = 38.0              # clasp chord pitch below horizontal

# end link
END_LINK_WIDTH = S.LUG_WIDTH - 0.2  # 0.1 clearance to each lug inner face
NOSE_HUG_R = S.END_LINK_SEAT_Y      # concave nose arc hugging the case

# clasp construction
CLASP_R = 70.0                      # outer-surface curvature radius
CLASP_PLATE_T = 2.0
CLASP_HALF_W = S.CLASP_WIDTH / 2.0


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------

def _prism_x(profile2d, half_width):
    """Extrude a YZ-plane sketch symmetrically along X."""
    return extrude(Plane.YZ * profile2d, amount=half_width, both=True)


def _plan_prism(xa0, xb0, xa1, xb1, y0, y1, bev=BEVEL):
    """Tapered side-wall prism with built-in 45-degree bevels.

    Lofted between two octagonal XZ sections (x in [xa, xb] at y0
    linearly to y1): vertical side walls whose top/bottom edges carry a
    `bev` chamfer as part of the section, replacing a 3D edge chamfer.
    OCC's chamfer on the link perimeter is unusable here — the dome face
    is tangent to the knuckle-eye cap cylinders, and depending on the
    exact link width the chamfer silently fails, churns for minutes, or
    segfaults (all three observed); the lofted section is deterministic.
    """

    def section(xa, xb, y):
        pts = [
            (xa + bev, -T2 - 0.12),
            (xb - bev, -T2 - 0.12),
            (xb, -(T2 - bev)),
            (xb, T2 - bev),
            (xb - bev, T2 + 0.12),
            (xa + bev, T2 + 0.12),
            (xa, T2 - bev),
            (xa, -(T2 - bev)),
        ]
        plane = Plane(origin=(0, y, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        return plane * Polygon(*pts, align=None)

    return loft([section(xa0, xb0, y0), section(xa1, xb1, y1)], ruled=True)


def _xcyl(r, length, y, z, x_mid=0.0):
    """Cylinder along the X axis at (y, z)."""
    return Pos(x_mid, y, z) * Rot(0, 90, 0) * Cylinder(r, length)


def _dome(y_mid, top_z=T2, length=30.0):
    """Large shared cylinder trimming the outer surface into a gentle dome."""
    return Pos(0, y_mid, top_z - DOME_R) * Rot(90, 0, 0) * Cylinder(DOME_R, length)


def _stadium_side(pitch):
    """Side (YZ) profile: rect between the two pin axes + both end discs."""
    rect = Polygon((0, -T2), (pitch, -T2), (pitch, T2), (0, T2), align=None)
    return rect + Circle(EYE_R) + Pos(pitch, 0) * Circle(EYE_R)


def _chamfer_link(part):
    """safe_chamfer ladder on the knuckle-arc flank edges only.

    The top/bottom perimeter bevel is carried by `_plan_prism`'s lofted
    section instead of a 3D chamfer (see its docstring for why).
    """
    flank = [
        e
        for e in part.edges()
        if (e.bounding_box().max.Z - e.bounding_box().min.Z) > 2.0
    ]
    part, _ = F.safe_chamfer(part, flank, 0.10)
    return part


# ---------------------------------------------------------------------------
# Links, rows, pins
# ---------------------------------------------------------------------------

def _make_link(xn0, xn1, xf0, xf1, pitch, near, far, color):
    """One link body in row-local frame (origin = near pin axis, +Y along
    the strap, z = 0 mid-thickness). `near`/`far` are 'eye' or 'recess'."""
    ya, yb = -EYE_R - 0.3, pitch + EYE_R + 0.3
    body = (
        _prism_x(_stadium_side(pitch), 12.0)
        & _plan_prism(xn0, xn1, xf0, xf1, ya, yb)
        & _dome(pitch / 2.0)
    )
    tools = []
    tools.append(_xcyl(RECESS_R if near == "recess" else BORE_R, 30.0, 0.0, 0.0))
    tools.append(_xcyl(RECESS_R if far == "recess" else BORE_R, 30.0, pitch, 0.0))
    body = body - tools
    body = _chamfer_link(body)
    body.color = Color(*color)
    return body


def make_row(w0, w1, pitch=P, terminal=False):
    """One bracelet row: (left, center, right) bodies in row-local frame.

    w0/w1: row width at the near/far pin axis (taper is smooth link to
    link). Outer links: recess near / eye far. Center link: eye near /
    recess far (eye both ends when `terminal`).
    """
    ya, yb = -EYE_R - 0.3, pitch + EYE_R + 0.3

    def hw(y):
        return (w0 + (w1 - w0) * y / pitch) / 2.0

    def ch(y):
        return CENTER_FRAC * (w0 + (w1 - w0) * y / pitch) / 2.0

    center = _make_link(
        -ch(ya), ch(ya), -ch(yb), ch(yb), pitch,
        near="eye", far=("eye" if terminal else "recess"),
        color=S.STEEL_BRIGHT,
    )
    left = _make_link(
        -hw(ya), -(ch(ya) + LINK_GAP), -hw(yb), -(ch(yb) + LINK_GAP), pitch,
        near="recess", far="eye", color=S.STEEL,
    )
    right = _make_link(
        ch(ya) + LINK_GAP, hw(ya), ch(yb) + LINK_GAP, hw(yb), pitch,
        near="recess", far="eye", color=S.STEEL,
    )
    return left, center, right


def make_pin(length):
    """Link pin body along X, visible ends on the link flanks."""
    pin = Rot(0, 90, 0) * Cylinder(PIN_R, length)
    ends = [
        e
        for e in pin.edges()
        if (e.bounding_box().max.X - e.bounding_box().min.X) < 0.01
    ]
    pin, _ = F.safe_chamfer(pin, ends, 0.18)
    pin.color = Color(*S.STEEL_DARK)
    return pin


def make_spring_bar():
    """Spring bar on the lug axis (watch frame, +Y side)."""
    bar = _xcyl(S.SPRING_BAR_DIAMETER / 2.0, 22.0, S.SPRING_BAR_Y, S.SPRING_BAR_Z)
    ends = [
        e
        for e in bar.edges()
        if (e.bounding_box().max.X - e.bounding_box().min.X) < 0.01
    ]
    bar, _ = F.safe_chamfer(bar, ends, 0.2)
    bar.color = Color(*S.STEEL_DARK)
    return bar


# ---------------------------------------------------------------------------
# End link
# ---------------------------------------------------------------------------

def make_end_link():
    """End link in the watch frame (+Y side): hugs the lug opening, nose
    tucked between the lugs against the case flank, hollow-look back,
    knuckle eyes at the outer widths of the first joint."""
    jy, jz = JOINT1_Y, JOINT1_Z
    nose_y = 19.2                     # flat flank front; arc trims the center
    top_nose, top_tail = 4.45, 4.05
    bot_nose, bot_tail = 1.9, 1.35
    slope = (top_nose - top_tail) / (24.8 - nose_y)
    slope_deg = math.degrees(math.atan(slope))

    prof = Polygon(
        (nose_y, bot_nose),
        (nose_y, top_nose),
        (24.8, top_tail),
        (jy, 3.6),
        (jy, 1.6),
        (24.8, bot_tail),
        align=None,
    ) + Pos(jy, jz) * Circle(EYE_R)

    half_w = END_LINK_WIDTH / 2.0
    body = _prism_x(prof, half_w) & _dome(22.8, top_z=top_nose, length=12.0)

    cw1 = CENTER_FRAC * S.BRACELET_WIDTH_AT_LUG      # center width at joint 1
    slot_half = cw1 / 2.0 + LINK_GAP

    # groove pair continuing the three-link separation lines over the top
    def groove(x):
        y_mid = 22.9
        z_top = top_nose - slope * (y_mid - nose_y)
        return (
            Pos(x, y_mid, z_top - 0.25 + 0.3)
            * Rot(-slope_deg, 0, 0)
            * Box(0.34, 4.6, 0.6, align=(None, None, None))
        )

    tools = [
        Cylinder(NOSE_HUG_R, 20.0, align=(None, None, None)),  # case-hugging nose arc
        _xcyl(S.SPRING_BAR_DIAMETER / 2.0 + 0.1, 30.0, S.SPRING_BAR_Y, S.SPRING_BAR_Z),
        Pos(0, 23.05, 1.7) * Box(16.6, 3.7, 2.4, align=(None, None, None)),  # hollow back
        _xcyl(RECESS_R, 2 * slot_half, jy, jz),      # center-link recess slot
        _xcyl(BORE_R, 30.0, jy, jz),                 # pin bore
        groove(cw1 / 2.0 + LINK_GAP / 2.0),
        groove(-(cw1 / 2.0 + LINK_GAP / 2.0)),
    ]
    body = body - tools
    body, _ = F.safe_chamfer(body, body.edges(), 0.12)
    body.color = Color(*S.STEEL)
    return body


# ---------------------------------------------------------------------------
# Clasp (closed, unbranded)
# ---------------------------------------------------------------------------

def _band(r_out, r_in, phi0_deg, phi1_deg, half_w):
    """Curved plate segment: annular sector about the clasp curvature
    center (clasp-local YZ), extruded across X."""
    zc = T2 - CLASP_R
    p0, p1 = math.radians(phi0_deg), math.radians(phi1_deg)
    ann = Pos(0, zc) * (Circle(r_out) - Circle(r_in))
    wedge = Polygon(
        (0, zc),
        (200.0 * math.sin(p0), zc + 200.0 * math.cos(p0)),
        (200.0 * math.sin(p1), zc + 200.0 * math.cos(p1)),
        align=None,
    )
    return _prism_x(ann & wedge, half_w)


def _arc_point(phi_deg, radius):
    """(y, z) of a point on the clasp curvature arc (clasp-local)."""
    zc = T2 - CLASP_R
    p = math.radians(phi_deg)
    return radius * math.sin(p), zc + radius * math.cos(p)


def make_clasp():
    """Closed fold-over clasp in clasp-local frame (origin = hinge pin
    axis to the last 6-o'clock row, +Y along the strap, +Z outward).

    Returns a list of (part, label) with colors set. Unbranded: plain
    brushed outer plate, chamfered edges only.
    """
    parts = []
    # pusher axis sits 0.2 below the outer face: hole top = R-1.8+1.6 = R-0.2
    y_push, z_push = _arc_point(26.0, CLASP_R - 1.8)

    # --- outer plate (brushed, curved) + center hinge tab -------------------
    plate = _band(CLASP_R, CLASP_R - CLASP_PLATE_T, 1.0, 31.0, CLASP_HALF_W)
    plan = extrude(
        Pos(0, 18.3) * RectangleRounded(S.CLASP_WIDTH, 35.0, 3.0),
        amount=40.0, both=True,
    )
    plate = plate & plan
    plate = plate - [
        _xcyl(RECESS_R, 40.0, 0.0, 0.0),          # hinge relief at the strap joint
        _xcyl(1.6, 40.0, y_push, z_push),         # pusher through-holes
    ]
    tab_prof = Circle(EYE_R) + Polygon(
        (0, -T2), (3.5, -T2), (3.5, T2), (0, T2), align=None
    )
    tab = _prism_x(tab_prof, 2.65) - _xcyl(BORE_R, 30.0, 0.0, 0.0)
    body = plate + tab
    body, _ = F.safe_chamfer(body, body.edges(), 0.12)
    body.color = Color(*S.STEEL)
    parts.append((body, "clasp_body"))

    # --- inner cover plate (folded closed under the outer plate) ------------
    cover = _band(CLASP_R - 2.3, CLASP_R - 3.5, 4.0, 29.5, 7.0)
    cover, _ = F.safe_chamfer(cover, cover.edges(), 0.1)
    cover.color = Color(*S.STEEL)
    parts.append((cover, "clasp_cover"))

    # --- internal spring blade ----------------------------------------------
    blade = _band(CLASP_R - 2.06, CLASP_R - 2.24, 8.0, 20.0, 2.0)
    blade.color = Color(*S.STEEL_DARK)
    parts.append((blade, "clasp_spring_blade"))

    # --- flank pushers -------------------------------------------------------
    for sgn, side in ((1.0, "right"), (-1.0, "left")):
        push = _xcyl(1.5, 2.7, y_push, z_push, x_mid=sgn * 8.25)
        ends = [
            e
            for e in push.edges()
            if (e.bounding_box().max.X - e.bounding_box().min.X) < 0.01
        ]
        push, _ = F.safe_chamfer(push, ends, 0.3)
        push.color = Color(*S.STEEL)
        parts.append((push, f"clasp_pusher_{side}"))

    # --- fold hinge knuckle --------------------------------------------------
    yk, zk = _arc_point(30.6, CLASP_R - 3.1)
    knuckle = _xcyl(1.0, 13.0, yk, zk)
    ends = [
        e
        for e in knuckle.edges()
        if (e.bounding_box().max.X - e.bounding_box().min.X) < 0.01
    ]
    knuckle, _ = F.safe_chamfer(knuckle, ends, 0.15)
    knuckle.color = Color(*S.STEEL_DARK)
    parts.append((knuckle, "clasp_hinge_knuckle"))

    # --- flip-lock bow (polished stirrup hugging the outer plate) -----------
    bow = _band(CLASP_R + 0.66, CLASP_R + 0.06, 20.6, 30.4, 4.5)
    y_w, _zw = _arc_point(25.5, CLASP_R + 0.45)
    window = extrude(
        Pos(0, y_w) * RectangleRounded(7.0, 9.8, 2.2), amount=60.0, both=True
    )
    bow = bow - window
    bow, _ = F.safe_chamfer(bow, bow.edges(), 0.08)
    bow.color = Color(*S.STEEL_BRIGHT)
    parts.append((bow, "clasp_flip_lock"))

    return parts


# ---------------------------------------------------------------------------
# Strap assembly (drape chain)
# ---------------------------------------------------------------------------

def _row_angles(n):
    """Downward pitch per row: gentle for rows 1-2, +DRAPE_STEP after."""
    out = []
    for k in range(1, n + 1):
        if k == 1:
            out.append(DRAPE_START)
        elif k == 2:
            out.append(DRAPE_SECOND)
        else:
            out.append(DRAPE_SECOND + DRAPE_STEP * (k - 2))
    return out


def _joint_chain(angles):
    """Pin-axis (y, z) positions from JOINT1 through the last joint."""
    joints = [(JOINT1_Y, JOINT1_Z)]
    for th in angles:
        y, z = joints[-1]
        t = math.radians(th)
        joints.append((y + P * math.cos(t), z - P * math.sin(t)))
    return joints


def _joint_width(k, n_rows):
    """Row width at 1-based joint index k (1 = lug end)."""
    taper = S.BRACELET_WIDTH_AT_LUG - S.BRACELET_WIDTH_AT_CLASP
    return S.BRACELET_WIDTH_AT_LUG - taper * (k - 1) / n_rows


def _build_strap(side):
    """All parts of one strap as (part, label) in the watch frame.

    `side` is "12" (+Y, 6 rows) or "6" (-Y, 5 rows + clasp).
    """
    n = S.LINKS_PER_SIDE_12 if side == "12" else S.LINKS_PER_SIDE_6
    angles = _row_angles(n)
    joints = _joint_chain(angles)
    flip = Rot(0, 0, 180) if side == "6" else None

    def world(p):
        return (flip * p) if flip is not None else p

    parts = []
    parts.append((world(make_end_link()), f"end_link_{side}"))
    parts.append((world(make_spring_bar()), f"spring_bar_{side}"))

    for i in range(n):
        w0 = _joint_width(i + 1, n)
        w1 = _joint_width(i + 2, n)
        terminal = side == "12" and i == n - 1
        th = angles[i]
        jy, jz = joints[i]
        place = Pos(0, jy, jz) * Rot(-th, 0, 0)
        left, center, right = make_row(w0, w1, terminal=terminal)
        parts.append((world(place * left), f"link_{side}_r{i + 1}_left"))
        parts.append((world(place * center), f"link_{side}_r{i + 1}_center"))
        parts.append((world(place * right), f"link_{side}_r{i + 1}_right"))

    for k, (jy, jz) in enumerate(joints, start=1):
        pin = make_pin(_joint_width(k, n) - 0.12)
        parts.append((world(Pos(0, jy, jz) * pin), f"pin_{side}_j{k}"))

    clasp_parts = []
    if side == "6":
        jy, jz = joints[-1]
        place = Pos(0, jy, jz) * Rot(-CLASP_PITCH_DEG, 0, 0)
        for part, label in make_clasp():
            clasp_parts.append((world(place * part), label))
    return parts, clasp_parts


def build_bracelet():
    """Full bracelet as a labeled assembly Compound in the watch frame."""
    strap12, _ = _build_strap("12")
    strap6, clasp = _build_strap("6")

    def compound(pairs, label):
        kids = []
        for part, name in pairs:
            part.label = name
            kids.append(part)
        return Compound(children=kids, label=label)

    return Compound(
        children=[
            compound(strap12, "strap_12"),
            compound(strap6, "strap_6"),
            compound(clasp, "clasp"),
        ],
        label="bracelet",
    )
