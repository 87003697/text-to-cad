"""Flat three-link bracelet builder (moonwatch archetype).

Frame: WATCH assembly frame from `_spec` — +Z through the crystal, 12
o'clock at +Y, z = 0 at the case-middle/caseback joint. The bracelet
attaches at the spring-bar axes (y = +/-SPRING_BAR_Y, z = SPRING_BAR_Z)
and drapes outward along +/-Y with a progressive downward pitch, like a
bracelet resting over an invisible wrist form.

Construction vocabulary (all rows share it):

- A row is three separate bodies: two wider brushed outer links
  (BRACELET_OUTER) and a narrower polished center link (BRACELET_CENTER),
  so the center row reads brighter than the brushed outers.
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
- Every link carries a crowned cross-section baked into its planned
  SECTION (never post-chamfered): the top is a shallow arc across the
  link's own width (sagitta CROWN_SAG) flanked by crisp 45-degree bevel
  facets of leg BEVEL; the bottom is a flatter dome (CROWN_SAG_BOT) with
  the same bevels. The clasp outer plate, inner cover, and flip-lock bow
  sweep the same crowned+beveled section along the clasp curvature arc.
- Joint shutlines: at every pin axis the link whose end carries the
  knuckle EYE has its crowned top milled back to a crisp straight edge
  SHUT_EDGE before the axis, a shallow crown-parallel floor SHUT_DEPTH
  below the crown, and a vertical wall SHUT_EDGE past the axis — ONE
  straight, constant-width (2*SHUT_EDGE = 0.28) shutline per joint,
  parallel to that joint's pin axis, crossing all three links. The
  mating recess end keeps its natural wrap lip: over the knuckle it
  reads as a hairline, not a second gap (pulling it back instead opens
  a wedge, because the wrap lip's plan curve runs 0.49 -> 1.02 from the
  axis across the crown sag). Laterally the center<->outer walls sit
  LINK_GAP apart under small BEVEL_INNER edge breaks — a constant
  LINK_GAP + 2*BEVEL_INNER = 0.22 opening, the two grooves parallel
  along the whole strap. All shutline cuts only REMOVE material, so the
  0.06 articulation clearance is untouched.
"""

from __future__ import annotations

import math

from build123d import (
    Box,
    CenterArc,
    Circle,
    Color,
    Compound,
    Cylinder,
    Part,
    Plane,
    Polygon,
    Polyline,
    Pos,
    RectangleRounded,
    Rot,
    ThreePointArc,
    extrude,
    loft,
    make_face,
    sweep,
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
LINK_GAP = 0.12                     # lateral WALL gap center <-> outer links
BEVEL = 0.15                        # built-in 45-degree edge-break bevel leg
BEVEL_INNER = 0.05                  # small break on center<->outer facing edges
#                                     (lateral opening = LINK_GAP + 0.10 = 0.22)
# top-face joint shutline, cut into the knuckle-EYE end only (see module
# docstring): crown-edge setback from the pin axis, groove floor depth
# below the crown, and floor run past the 45-degree facet foot
SHUT_EDGE = 0.14                    # 2 * 0.14 = 0.28 surface opening
SHUT_DEPTH = 0.18                   # shallow floor — a line, not a canyon
SHUT_RUN = 0.10                     # facet foot (+0.04) -> wall at +SHUT_EDGE
SHUT_RUN_TUCK = 0.86                # row-1 center near end: run the floor to
#                                     -0.90 so the knuckle ducks 0.07 BELOW the
#                                     end-link slot mouth instead of standing
#                                     0.11 proud of it
CROWN_SAG = 0.30                    # top crown sagitta across each link width
CROWN_SAG_BOT = 0.10                # gentler dome on the wrist side
CROWN_APEX = T2 - 0.02              # crown apex 0.02 inside the eye envelope
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


def _crowned_face(xa, xb, z_top, z_bot, s_top, bev, s_bot=0.0):
    """Closed crowned cross-section face in local (x, z) coordinates.

    Top: shallow arc (sagitta `s_top`, apex at `z_top`) flanked by crisp
    45-degree bevel facets of leg `bev` meeting vertical side walls at
    x = xa / xb; `bev` may be a (left, right) pair so the facing edges of
    adjacent links carry a wider polished bevel than the outer flanks.
    Bottom: flat at `z_bot` when `s_bot` == 0, else a gentler downward
    arc with the same bevels. Baking the crown and bevels into the
    SECTION replaces 3D edge chamfers entirely — OCC's chamfer on link
    perimeters that touch dome/eye-cap tangent chains silently fails,
    churns for minutes, or segfaults (see /BUGS.md).
    """
    ba, bb = bev if isinstance(bev, tuple) else (bev, bev)
    xc = (xa + xb) / 2.0
    top = ThreePointArc(
        (xb - bb, z_top - s_top), (xc, z_top), (xa + ba, z_top - s_top)
    )
    if s_bot > 0.0:
        left = Polyline(
            (xa + ba, z_top - s_top),
            (xa, z_top - s_top - ba),
            (xa, z_bot + s_bot + ba),
            (xa + ba, z_bot + s_bot),
        )
        bottom = ThreePointArc(
            (xa + ba, z_bot + s_bot), (xc, z_bot), (xb - bb, z_bot + s_bot)
        )
        right = Polyline(
            (xb - bb, z_bot + s_bot),
            (xb, z_bot + s_bot + bb),
            (xb, z_top - s_top - bb),
            (xb - bb, z_top - s_top),
        )
    else:
        left = Polyline(
            (xa + ba, z_top - s_top), (xa, z_top - s_top - ba), (xa, z_bot)
        )
        bottom = Polyline((xa, z_bot), (xb, z_bot))
        right = Polyline(
            (xb, z_bot), (xb, z_top - s_top - bb), (xb - bb, z_top - s_top)
        )
    return make_face(top + left + bottom + right)


def _plan_prism(xa0, xb0, xa1, xb1, y0, y1, bev=BEVEL):
    """Tapered side-wall prism with a crowned, edge-broken section.

    Lofted between two crowned XZ sections (x in [xa, xb] at y0 linearly
    to y1): the top of each section is a shallow arc across the link's
    own width (sagitta CROWN_SAG) flanked by 45-degree bevel facets, the
    bottom a flatter dome (CROWN_SAG_BOT) with the same bevels. The apex
    sits at CROWN_APEX, 0.02 below the stadium/eye-cap envelope, so the
    knuckle-eye cylinders cross the crown transversally (no tangent
    contact) and only a short realistic knuckle crest pokes through at
    each pin axis. See `_crowned_face` for why no 3D chamfer is used.
    """

    def section(xa, xb, y):
        plane = Plane(origin=(0, y, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        return plane * _crowned_face(
            xa, xb, CROWN_APEX, -CROWN_APEX, CROWN_SAG, bev, s_bot=CROWN_SAG_BOT
        )

    return loft([section(xa0, xb0, y0), section(xa1, xb1, y1)], ruled=True)


def _xcyl(r, length, y, z, x_mid=0.0):
    """Cylinder along the X axis at (y, z)."""
    return Pos(x_mid, y, z) * Rot(0, 90, 0) * Cylinder(r, length)


def _crown_cap(half_w, length, s_top=CROWN_SAG, bev=BEVEL):
    """Crowning cap prism (intersect with it): crowned top arc with apex
    at local z = 0 and 45-degree bevel facets at x = +/-half_w, side
    walls dropping far below — bakes the crown + top edge breaks into a
    body whose plan is not a `_plan_prism` trapezoid (end link)."""
    plane = Plane(origin=(0, 0, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    face = plane * _crowned_face(-half_w, half_w, 0.0, -12.0, s_top, bev)
    return extrude(face, amount=length, both=True)


def _reveal_face(xa, xb, y, drop, bev):
    """Loft section for `_reveal_cutter`: the region ABOVE the link's own
    crowned top arc lowered by `drop`, up to z = +3, extended 0.3 past
    the plan width so the side walls sit in air. The floor runs FLAT at
    the arc-end height across each side-bevel corner (it does not follow
    the bevel down): at the flanks the lowered bevel corner would dip
    below the knuckle bore's top (0.95) and open pinholes into the bore
    at every groove corner."""
    ba, bb = bev if isinstance(bev, tuple) else (bev, bev)
    z_apex = CROWN_APEX - drop
    z_sh = z_apex - CROWN_SAG
    xc = (xa + xb) / 2.0
    arc = ThreePointArc((xb - bb, z_sh), (xc, z_apex), (xa + ba, z_sh))
    lid = Polyline(
        (xa + ba, z_sh),
        (xa - 0.3, z_sh),
        (xa - 0.3, 3.0),
        (xb + 0.3, 3.0),
        (xb + 0.3, z_sh),
        (xb - bb, z_sh),
    )
    plane = Plane(origin=(0, y, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    return plane * make_face(arc + lid)


def _reveal_cutter(xa, xb, axis_y, direction, edge, depth, run, bev=BEVEL):
    """Top-face joint reveal cutter about a pin axis.

    Subtracting it pulls the link's crowned top back to a CRISP edge at
    y = axis_y - direction*edge, drops a 45-degree reveal facet to `depth`
    below the crown (following the crown arc across x, so the pullback is
    uniform over the whole width), and with `run` > 0 keeps a flat deck at
    that depth across the knuckle so the joint reads as a real shadowed
    gap. direction = +1 for the far end (link body at y < axis), -1 for
    the near end. Removal only: articulation clearances are untouched.
    """
    lead = 0.06  # start the loft slightly above the crown: transversal cut
    y0 = axis_y - direction * (edge + lead)
    stations = [(y0, -lead), (y0 + direction * (depth + lead), depth)]
    if run > 0.0:
        stations.append((y0 + direction * (depth + lead + run), depth))
    return loft(
        [_reveal_face(xa, xb, y, d, bev) for y, d in stations], ruled=True
    )


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

def _make_link(
    xn0, xn1, xf0, xf1, pitch, near, far, color, bev=BEVEL, near_run=SHUT_RUN
):
    """One link body in row-local frame (origin = near pin axis, +Y along
    the strap, z = 0 mid-thickness). `near`/`far` are 'eye' or 'recess'.

    The joint shutline is cut into EYE ends only: a recess end's crown
    already terminates at its wrap lip, and any straight pullback wide
    enough to swallow that curved lip (>= 1.02 at the flanks) gapes
    (see module docstring). `near_run` extends the near shutline's floor
    past its wall (SHUT_RUN_TUCK for the row-1 center link).
    """
    ya, yb = -EYE_R - 0.3, pitch + EYE_R + 0.3
    body = (
        _prism_x(_stadium_side(pitch), 12.0)
        & _plan_prism(xn0, xn1, xf0, xf1, ya, yb, bev=bev)
    )
    tools = []
    tools.append(_xcyl(RECESS_R if near == "recess" else BORE_R, 30.0, 0.0, 0.0))
    tools.append(_xcyl(RECESS_R if far == "recess" else BORE_R, 30.0, pitch, 0.0))
    for axis_y, direction, kind, x0, x1, run in (
        (0.0, -1.0, near, xn0, xn1, near_run),
        (pitch, 1.0, far, xf0, xf1, SHUT_RUN),
    ):
        if kind == "eye":
            tools.append(
                _reveal_cutter(
                    x0, x1, axis_y, direction, SHUT_EDGE, SHUT_DEPTH, run, bev=bev
                )
            )
    body = body - tools
    body = _chamfer_link(body)
    body.color = Color(*color)
    return body


def make_row(w0, w1, pitch=P, terminal=False, first=False):
    """One bracelet row: (left, center, right) bodies in row-local frame.

    w0/w1: row width at the near/far pin axis (taper is smooth link to
    link). Outer links: recess near / eye far. Center link: eye near /
    recess far (eye both ends when `terminal`). `first` marks the row at
    the end link, whose center knuckle must duck under the slot mouth.
    """
    ya, yb = -EYE_R - 0.3, pitch + EYE_R + 0.3

    def hw(y):
        return (w0 + (w1 - w0) * y / pitch) / 2.0

    def ch(y):
        return CENTER_FRAC * (w0 + (w1 - w0) * y / pitch) / 2.0

    center = _make_link(
        -ch(ya), ch(ya), -ch(yb), ch(yb), pitch,
        near="eye", far=("eye" if terminal else "recess"),
        color=S.BRACELET_CENTER, bev=(BEVEL_INNER, BEVEL_INNER),
        near_run=(SHUT_RUN_TUCK if first else SHUT_RUN),
    )
    left = _make_link(
        -hw(ya), -(ch(ya) + LINK_GAP), -hw(yb), -(ch(yb) + LINK_GAP), pitch,
        near="recess", far="eye", color=S.BRACELET_OUTER,
        bev=(BEVEL, BEVEL_INNER),
    )
    right = _make_link(
        ch(ya) + LINK_GAP, hw(ya), ch(yb) + LINK_GAP, hw(yb), pitch,
        near="recess", far="eye", color=S.BRACELET_OUTER,
        bev=(BEVEL_INNER, BEVEL),
    )
    return left, center, right


def make_pin(length):
    """Link pin body along X, visible ends on the link flanks.

    Ends are flat discs with only a 0.05 edge break, sitting near-flush
    in the bore — a 0.18 end chamfer on the 0.9 radius read as a pointed
    cone tip recessed in the bore at macro scale."""
    pin = Rot(0, 90, 0) * Cylinder(PIN_R, length)
    ends = [
        e
        for e in pin.edges()
        if (e.bounding_box().max.X - e.bounding_box().min.X) < 0.01
    ]
    pin, _ = F.safe_chamfer(pin, ends, 0.05)
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
    # crown + 45-degree top edge bevels baked in, pitched to follow the
    # nose->tail top slope so the crown runs the full link length
    cap = (
        Pos(0, nose_y, top_nose)
        * Rot(-slope_deg, 0, 0)
        * _crown_cap(half_w, 14.0)
    )
    body = _prism_x(prof, half_w) & cap

    cw1 = CENTER_FRAC * S.BRACELET_WIDTH_AT_LUG      # center width at joint 1
    slot_half = cw1 / 2.0 + LINK_GAP

    # groove pair continuing the three-link separation lines over the top,
    # matching the 0.22 lateral shutline opening between center and outers
    # (centered boxes: align=(None,None,None) is corner-origin, which left
    # the old grooves floating above the surface and cutting nothing)
    def groove(x):
        y_mid = 22.9
        z_top = top_nose - slope * (y_mid - nose_y)
        return (
            Pos(x, y_mid, z_top)
            * Rot(-slope_deg, 0, 0)
            * Box(LINK_GAP + 2 * BEVEL_INNER, 4.6, 2 * SHUT_DEPTH)
        )

    # joint-1 shutline: straight vertical-walled band across the tail at
    # the pin axis, floor following the crown cap lowered SHUT_DEPTH. The
    # band bottom is clamped at 3.60 (bore roof at the shutline is 3.54)
    # so the floor cannot pinhole into the pin bore under the side bevels.
    shut_band = Pos(0, jy, 4.6) * Box(END_LINK_WIDTH + 1.0, 2 * SHUT_EDGE, 2.0)
    shut_floor = (
        Pos(0, nose_y, top_nose - SHUT_DEPTH)
        * Rot(-slope_deg, 0, 0)
        * _crown_cap(half_w, 14.0)
    )

    tools = [
        Cylinder(NOSE_HUG_R, 20.0, align=(None, None, None)),  # case-hugging nose arc
        _xcyl(S.SPRING_BAR_DIAMETER / 2.0 + 0.1, 30.0, S.SPRING_BAR_Y, S.SPRING_BAR_Z),
        Pos(0, 23.05, 1.7) * Box(16.6, 3.7, 2.4),    # hollow back (centered)
        _xcyl(RECESS_R, 2 * slot_half, jy, jz),      # center-link recess slot
        _xcyl(BORE_R, 30.0, jy, jz),                 # pin bore
        groove(cw1 / 2.0 + LINK_GAP / 2.0),
        groove(-(cw1 / 2.0 + LINK_GAP / 2.0)),
        shut_band - shut_floor,
    ]
    body = body - tools

    # break remaining edges, but keep the profile-baked crown/bevel facet
    # lines (along Y at the outer widths, near the top) AND the joint-1
    # shutline walls crisp (a 0.12 chamfer on a 0.28 groove is a glint)
    def _keep(e):
        bb = e.bounding_box()
        on_bevel_band = (
            bb.min.Z > 3.0 and min(abs(bb.min.X), abs(bb.max.X)) > 9.3
        )
        on_shutline = bb.max.Y > jy - 0.30 and bb.min.Z > 3.2
        return not (on_bevel_band or on_shutline)

    body, _ = F.safe_chamfer(body, [e for e in body.edges() if _keep(e)], 0.12)
    body.color = Color(*S.BRACELET_OUTER)
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


def _crowned_band(r_out, r_in, phi0_deg, phi1_deg, half_w, s_top, bev):
    """Curved crowned plate: the crowned + 45-degree-beveled cross-section
    (see `_crowned_face`) swept along the clasp curvature arc, so the edge
    breaks are baked into the profile instead of post-chamfered."""
    zc = T2 - CLASP_R
    t = r_out - r_in
    r_mid = (r_out + r_in) / 2.0
    path = Plane.YZ * CenterArc(
        (0, zc), r_mid,
        start_angle=90.0 - phi1_deg,
        arc_size=phi1_deg - phi0_deg,
    )
    plane = Plane(origin=path @ 0, x_dir=(1, 0, 0), z_dir=path % 0)
    section = plane * _crowned_face(-half_w, half_w, t / 2.0, -t / 2.0, s_top, bev)
    return sweep(section, path=path)


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
    # pusher axis sits 0.55 below the outer face so the through-hole stays
    # clear of the crown's edge bevels (crown drop at the flank = 0.45)
    y_push, z_push = _arc_point(26.0, CLASP_R - 2.15)

    # --- outer plate (brushed, curved, crowned) + center hinge tab ----------
    plate = _crowned_band(
        CLASP_R, CLASP_R - CLASP_PLATE_T, 1.0, 31.0, CLASP_HALF_W,
        CROWN_SAG, BEVEL,
    )
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
    # break only the short cut/end edges; the long swept facets already
    # carry their profile-baked bevels and must stay crisp
    short_edges = [
        e
        for e in body.edges()
        if (e.bounding_box().max.Y - e.bounding_box().min.Y) < 12.0
    ]
    body, _ = F.safe_chamfer(body, short_edges, 0.12)
    body.color = Color(*S.BRACELET_OUTER)
    parts.append((body, "clasp_body"))

    # --- inner cover plate (folded closed under the outer plate) ------------
    # crowned + beveled section baked in; no post chamfer
    cover = _crowned_band(CLASP_R - 2.3, CLASP_R - 3.5, 4.0, 29.5, 7.0, 0.18, 0.10)
    cover.color = Color(*S.BRACELET_OUTER)
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
        push.color = Color(*S.BRACELET_CENTER)
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
    # crowned + beveled section baked in; only the window-cut edges (all
    # strictly inside the window plan) still need a break
    bow = _crowned_band(CLASP_R + 0.66, CLASP_R + 0.06, 20.6, 30.4, 4.5, 0.15, 0.08)
    y_w, _zw = _arc_point(25.5, CLASP_R + 0.45)
    window = extrude(
        Pos(0, y_w) * RectangleRounded(7.0, 9.8, 2.2), amount=60.0, both=True
    )
    bow = bow - window

    def _in_window(e):
        bb = e.bounding_box()
        return (
            max(abs(bb.min.X), abs(bb.max.X)) < 3.8
            and bb.min.Y > y_w - 5.3
            and bb.max.Y < y_w + 5.3
        )

    bow, _ = F.safe_chamfer(bow, [e for e in bow.edges() if _in_window(e)], 0.08)
    bow.color = Color(*S.BRACELET_CENTER)
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
        left, center, right = make_row(w0, w1, terminal=terminal, first=(i == 0))
        parts.append((world(place * left), f"link_{side}_r{i + 1}_left"))
        parts.append((world(place * center), f"link_{side}_r{i + 1}_center"))
        parts.append((world(place * right), f"link_{side}_r{i + 1}_right"))

    for k, (jy, jz) in enumerate(joints, start=1):
        pin = make_pin(_joint_width(k, n) - 0.10)
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
            if not isinstance(part, Part):
                # some boolean/chamfer chains return a bare Compound; the
                # per-component STEP/GLB export only colors Part/Sketch/
                # Curve leaves ("Unknown Compound type, color not set"),
                # which silently drops the finish contrast
                solid = Part(part.wrapped)
                solid.color = part.color
                part = solid
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
