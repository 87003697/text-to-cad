"""Movement base cluster: main plate, barrel + ratchet/crown/click, going
train, bridges, escapement, and balance assembly for the caliber-321-lineage
movement. Frame: MOVEMENT local (see `_spec.py`) — bridge side up, z = 0 at
the plate's bridge-side face.

Exposes `build_base()` -> list of labeled/colored parts, plus module-level
plan-outline constants (documented below) that the chronograph-works builder
imports READ-ONLY to route its levers around these bridges.

Bridge outlines are unions of circles `(x, y, r)` in movement XY, clipped to
`*_CLIP_R` about the movement center. Anything inside an outline between
`BRIDGE_SEAT_Z` and `BRIDGE_TOP_Z` (or the pallet/cock z-ranges) is occupied.
Raised jewel bosses (r 1.5 at every `JEWEL_POSITIONS_UPPER` entry) rise a
further 0.14 above `BRIDGE_TOP_Z`; the winding square + ratchet screw reach
`RATCHET_SCREW_TOP_Z` over `BARREL_POS`.

NOTE: `_finishing.py` helpers assume `align=(None,None,None)` centers
primitives; it actually leaves the raw OCC datum (see /BUGS.md). This module
compensates with corrective cuts (proper screw slots, countersink cones,
full-depth wheel windows, perlage z-shift) while still building on the
shared helpers.
"""

from __future__ import annotations

import math

from build123d import (
    Box,
    Circle,
    Color,
    Cone,
    Cylinder,
    Polygon,
    Pos,
    Rot,
    extrude,
)

import _spec as S
import _finishing as F

# ---------------------------------------------------------------------------
# Exported layout constants (READ-ONLY for other builders)
# ---------------------------------------------------------------------------

#: Bridges (barrel + train) span this z band; their striped top face is
#: BRIDGE_TOP_Z. The chronograph layer must stay above BRIDGE_TOP_Z except
#: where a plan position is outside every outline below.
BRIDGE_SEAT_Z = S.BRIDGE_SEAT_Z          # 1.8
BRIDGE_TOP_Z = S.BRIDGE_SEAT_Z + S.BRIDGE_THICKNESS  # 2.85

#: Barrel bridge plan outline: union of (x, y, r) circles clipped to
#: BRIDGE_CLIP_R about (0, 0). Covers barrel, crown wheel and click zones.
BARREL_BRIDGE_OUTLINE = (
    (-1.6, 8.0, 6.0),
    (6.9, 8.3, 3.9),
    (4.3, 4.1, 2.6),    # click lobe
    (10.6, 4.2, 3.4),
    (9.2, 6.6, 3.0),
)
#: Train bridge plan outline (same convention). Three-finger cock over
#: center / third / fourth / escape pivots.
TRAIN_BRIDGE_OUTLINE = (
    (0.0, 0.0, 1.7),
    (-2.5, -1.3, 1.4),
    (-5.0, -2.6, 1.9),
    (-6.8, -4.3, 1.6),
    (-7.6, -5.2, 1.6),   # slim terminal finger: the escape wheel stays visible
    (-10.9, -3.2, 2.5),
    (-11.2, -1.8, 1.9),
    (-10.2, 0.0, 2.0),
    (-11.2, 1.6, 1.3),
    (-11.6, -4.5, 1.8),
)
#: Circles SUBTRACTED from the train-bridge outline (wheel-reveal cutout).
TRAIN_BRIDGE_CUTOUTS = ((-8.9, -2.4, 1.0),)
#: Pallet bridge (z PALLET_BRIDGE_Z) and balance cock (z COCK_Z) outlines.
PALLET_BRIDGE_OUTLINE = ((-4.9, -7.2, 1.15), (-5.4, -8.05, 0.9), (-5.9, -8.9, 1.05))
#: Balance cock: tapered finger — wide foot at the rim, waisted neck, round
#: head over the shock setting, small stud-holder lobe on the +X side.
BALANCE_COCK_OUTLINE = (
    (-0.40, -7.60, 1.95),    # head over the shock setting
    (-0.45, -8.15, 1.70),
    (-0.50, -8.70, 1.48),
    (-0.55, -9.20, 1.32),
    (-0.60, -9.70, 1.22),    # waist
    (-0.65, -10.20, 1.26),
    (-0.70, -10.70, 1.38),
    (-0.75, -11.15, 1.55),
    (-0.80, -11.60, 1.78),
    (-0.90, -12.30, 2.30),   # foot (clipped at COCK_CLIP_R)
    (1.35, -9.15, 0.80),     # stud-holder lobe
)
BRIDGE_CLIP_R = 12.85
COCK_CLIP_R = 13.2

PALLET_BRIDGE_Z = (1.6, 1.96)            # pallet cock z band
COCK_Z = (2.7, 3.25)                     # balance cock plate z band
COCK_SHOCK_TOP_Z = 3.4                   # lyre-spring shock setting apex

#: Blued bridge screws: label -> (x, y). Screw heads sit at the owning
#: bridge's top surface; keep chronograph levers clear of a 1.1 mm radius.
BRIDGE_SCREW_POSITIONS = {
    "barrel_bridge:left": (-7.9, 9.9),
    "barrel_bridge:right": (11.2, 3.4),
    "barrel_bridge:top": (1.6, 11.3),
    "train_bridge:foot": (-11.4, -4.4),
    "train_bridge:fourth": (-11.5, 2.2),
    "train_bridge:center": (-2.6, -0.6),
    "pallet_bridge": (-5.9, -8.9),
    "balance_cock": (-0.83, -12.87),
}
#: Upper (bridge-top) jewel positions: label -> (x, y).
JEWEL_POSITIONS_UPPER = {
    "center": S.CENTER_WHEEL_POS,
    "third": S.THIRD_WHEEL_POS,
    "fourth": S.FOURTH_WHEEL_POS,
    "escape": S.ESCAPE_WHEEL_POS,
    "barrel": S.BARREL_POS,
    "pallet": S.PALLET_FORK_POS,
}
#: Ratchet / crown wheel occupy z [RATCHET_Z0, RATCHET_TOP_Z] above the
#: barrel bridge; the raised winding square + blued screw reach
#: RATCHET_SCREW_TOP_Z (chronograph levers must clear a 1.3 mm radius there).
RATCHET_Z0 = 2.88
RATCHET_TOP_Z = 3.14
RATCHET_SQUARE_TOP_Z = 3.46
RATCHET_SCREW_TOP_Z = 3.58
BALANCE_RIM_Z = (2.0, 2.45)              # balance rim ring z band (r 4.1..4.8)

# --- internal z plan -------------------------------------------------------
_DRUM_Z = (0.4, 1.45)                    # barrel drum wall
_FLANGE_Z = (1.435, 1.735)               # barrel tooth flange (top of drum)
_CW_Z = 0.2                              # center wheel web mid z
_CP_Z = (1.25, 1.75)                     # center pinion
_TP_Z = (0.05, 0.60)                     # third pinion
_TW_Z = 0.88                             # third wheel web mid z
_FP_Z = (0.70, 1.30)                     # fourth pinion
_FW_Z = 1.40                             # fourth wheel web mid z
_EP_Z = (1.25, 1.72)                     # escape pinion
_EW_Z = 1.12                             # escape wheel web mid z
_FORK_Z = (1.28, 1.43)                   # pallet fork plate

# --- mesh-driven diameters (tip circles reach partner pinion roots) --------
_BARREL_GEAR_D = 13.65                   # 80 t, meshes center pinion
_CENTER_PINION_D = 4.2                   # 12 leaves
_CENTER_WHEEL_D = 9.6                    # 64 t, meshes third pinion
_THIRD_PINION_D = 2.6
_THIRD_WHEEL_D = 10.0                    # 60 t, meshes fourth pinion
_FOURTH_PINION_D = 2.53
_FOURTH_WHEEL_D = 9.71                   # 70 t, meshes escape pinion
_ESCAPE_PINION_D = 3.0
_ESCAPE_WHEEL_D = 5.4                    # 15 club teeth

_ARBOR_R = 0.30


def _cyl(r, h, z0):
    """Cylinder spanning z [z0, z0 + h] (default centered primitive)."""
    return Pos(0, 0, z0 + h / 2.0) * Cylinder(r, h)


def _ring(ri, ro, h, z0):
    return Pos(0, 0, z0) * extrude(Circle(ro) - Circle(ri), amount=h)


def _blob(circles, clip_r, cutouts=()):
    """2D union of (x, y, r) circles clipped to a disk about the origin."""
    prof = None
    for x, y, r in circles:
        c = Pos(x, y) * Circle(r)
        prof = c if prof is None else prof + c
    prof = prof & Circle(clip_r)
    for x, y, r in cutouts:
        prof = prof - Pos(x, y) * Circle(r)
    return prof


def _ang(frm, to):
    return math.degrees(math.atan2(to[1] - frm[1], to[0] - frm[0]))


def _finish(part, label, color):
    part.label = label
    part.color = Color(*color)
    return part


def _screw(head_d=S.SCREW_HEAD_DIAMETER, hh=0.34, shank=0.8,
           slot_w=S.SCREW_SLOT_WIDTH, color=S.BLUED):
    """F.slotted_screw + corrective cuts: flatten the shank stub that pokes
    through the dome, then cut a proper centered full-length slot. Head top
    lands at the returned part's local z = its `top` (~0.06..0.09)."""
    s = F.slotted_screw(head_diameter=head_d, head_height=hh,
                        shank_length=shank, slot_width=slot_w, color=color)
    r = head_d / 2.0
    apex = min(hh / 2.0, 0.2 * r)
    top = apex - 0.038
    s = s - Pos(0, 0, top + 0.5) * Box(head_d * 3, head_d * 3, 1.0)
    depth = min(0.16, hh * 0.5)
    s = s - Pos(0, 0, top + 0.3 - depth) * Box(head_d * 2.2, slot_w, 0.6)
    s.color = Color(*color)
    return s, top


def _place_screw(x, y, surface_z, proud=0.06, **kw):
    s, top = _screw(**kw)
    return Pos(x, y, surface_z + proud - top) * s


def _sink_tool():
    """Jewel countersink tool: shared helper + corrective polished cone
    (the helper's cone is half above the surface with an inverted flare)."""
    return (F.jewel_countersink_cut()
            + Pos(0, 0, -0.11) * Cone(0.79, 1.35, 0.22))


def _window_cutter(od, frac):
    """Full-depth replica of F.train_wheel's crossing-out cutter (the helper
    only cuts the top half of every spoke window; see /BUGS.md)."""
    r_o = od / 2.0
    r_root = r_o - r_o * frac
    rim_inner = r_root - od * 0.10
    hub = max(1.4, od * 0.14)
    spoke_w = max(0.5, od * 0.045)
    ring = _cyl(rim_inner, 1.2, -0.6) - _cyl(hub / 2.0 + 0.4, 1.4, -0.7)
    spokes = []
    for k in range(5):
        sp = Rot(0, 0, 72.0 * k) * (Pos(rim_inner * 1.025, spoke_w / 2.0, 0)
                                    * Box(rim_inner * 2.05, spoke_w, 1.6))
        spokes.append(sp)
    return ring - spokes


# ---------------------------------------------------------------------------
# Bridge factory: extrude blob, anglage, circular stripes about (0,0),
# raised polished jewel bosses, jewel countersinks + polished screw sinks,
# all in batched booleans.
# ---------------------------------------------------------------------------

_BOSS_R = 1.5                            # raised jewel boss radius
_BOSS_H = 0.14                           # boss rise above the striped top
# Circular-striping V grooves about (0,0): depth sets the facet slope that
# makes the stripes READ under the presentation light (0.07 -> 4.3 deg was
# invisible, 0.13 -> 8 deg still washed out; 0.22 -> ~13 deg reads like the
# ratchet snailing at macro).
_STRIPE_DEPTH = 0.22


def _bridge(circles, clip_r, z0, z1, jewel_xy, screw_xy, cutouts=(),
            extra_cuts=(), stripe=True, boss_xy=()):
    prof = _blob(circles, clip_r, cutouts)
    body = Pos(0, 0, z0) * extrude(prof, amount=z1 - z0)
    body, _ = F.anglage_top(body, S.ANGLAGE_WIDTH)

    if boss_xy:
        adds = []
        for x, y in boss_xy:
            adds.append(Pos(x, y, 0) * (
                _cyl(_BOSS_R, z1 - z0, z0)
                + Pos(0, 0, z1 + _BOSS_H / 2) * Cone(_BOSS_R, _BOSS_R - 0.14,
                                                     _BOSS_H)))
        body = body + adds

    cuts = list(extra_cuts)
    if stripe:
        rings = F.snailing_cutter(2 * clip_r + 2.0, 2.4, pitch=S.GENEVA_STRIPE_PITCH,
                                  groove_depth=_STRIPE_DEPTH, groove_width=1.85)
        inset = _blob([(x, y, max(r - 0.4, 0.2)) for x, y, r in circles],
                      clip_r - 0.4)
        for x, y in boss_xy:
            inset = inset - Pos(x, y) * Circle(_BOSS_R + 0.05)
        band = Pos(0, 0, z1 - 0.3) * extrude(inset, amount=0.6)
        cuts.append((Pos(0, 0, z1) * rings) & band)
    for x, y in jewel_xy:
        cuts.append(Pos(x, y, z1) * _sink_tool())
    for x, y in boss_xy:
        cuts.append(Pos(x, y, z1 + _BOSS_H) * _sink_tool())
    for x, y in screw_xy:
        sink = _cyl(0.875, 0.30, -0.30) + Pos(0, 0, -0.12) * Cone(0.875, 1.05, 0.12)
        cuts.append(Pos(x, y, z1) * sink
                    + Pos(x, y, 0) * _cyl(0.45, z1 - z0 + 1.0, z0 - 0.5))
    return body - cuts


# ---------------------------------------------------------------------------
# 1. Main plate (perlage, recesses, bosses, lower jewels)
# ---------------------------------------------------------------------------

def _plate_parts():
    parts = []
    plate = _cyl(S.MOVEMENT_DIAMETER / 2.0, S.PLATE_THICKNESS, -S.PLATE_THICKNESS)
    rim_edges = [e for e in plate.edges()
                 if abs(e.bounding_box().max.Z) < 1e-6
                 or abs(e.bounding_box().min.Z + S.PLATE_THICKNESS) < 1e-6]
    plate, _ = F.safe_chamfer(plate, rim_edges, 0.15)

    bx, by = S.BARREL_POS
    lx, ly = S.BALANCE_POS

    # bosses/pillars where bridges land (heights limited by wheel clearances)
    # + a crescent platform under the balance-cock foot (relieved around the
    # balance rim sweep so the wheel spins free below the cock)
    foot_prof = ((Pos(-0.9, -12.3) * Circle(1.9)) & Circle(13.4)
                 - Pos(lx, ly) * Circle(4.95))
    bosses = [
        Pos(-7.9, 9.9, 0) * _cyl(1.1, 1.38, 0),
        Pos(11.2, 3.4, 0) * _cyl(1.1, 1.80, 0),
        Pos(-11.4, -4.4, 0) * _cyl(1.0, 1.25, 0),
        Pos(-11.5, 2.2, 0) * _cyl(1.0, 1.26, 0),
        Pos(-5.9, -8.9, 0) * _cyl(0.8, 1.55, 0),
        extrude(foot_prof, amount=2.70),
    ]
    plate = plate + bosses
    cuts = [
        Pos(bx, by, 0) * _cyl(6.4, 0.55, -0.35),          # barrel recess
        Pos(lx, ly, 0) * _cyl(5.3, 0.50, -0.30),          # balance recess
        _cyl(0.9, 3.0, -1.6),                              # center bore
        Pos(bx, by, 0) * _cyl(0.75, 3.0, -1.6),            # barrel arbor bore
        # radial stem bore at 3 o'clock (keyless builder finishes it)
        Pos(12.2, 0, S.STEM_AXIS_Z_LOCAL) * Rot(0, 90, 0) * Cylinder(0.5, 3.2),
    ]
    # lower train/balance jewels: countersinks open to the dial side
    lower = [S.CENTER_WHEEL_POS, S.THIRD_WHEEL_POS, S.FOURTH_WHEEL_POS,
             S.ESCAPE_WHEEL_POS, S.PALLET_FORK_POS, S.BALANCE_POS]
    for x, y in lower:
        cuts.append(Pos(x, y, -S.PLATE_THICKNESS) * Rot(180, 0, 0) * _sink_tool())

    # perlage over every open bridge-side field (hex grid, keep-out filtered;
    # stamps dropped 0.033 so the helper's z>=0-clipped lens actually bites)
    proto = F.perlage_cutter(0.001, 0.001)[0]
    keep_out = [(x, y, r - 0.45) for x, y, r in
                (BARREL_BRIDGE_OUTLINE + TRAIN_BRIDGE_OUTLINE
                 + PALLET_BRIDGE_OUTLINE + BALANCE_COCK_OUTLINE)]
    keep_out += [(bx, by, 6.5), (lx, ly, 5.4), (-0.9, -12.3, 2.4)]
    step, row = S.PERLAGE_DIAMETER + 0.05, (S.PERLAGE_DIAMETER + 0.05) * 0.8660
    n = int(12.6 / step) + 1
    for j in range(-n - 2, n + 3):
        for i in range(-n - 2, n + 3):
            x = i * step + (step / 2 if j % 2 else 0.0)
            y = j * row
            if math.hypot(x, y) > 12.5:
                continue
            if any(math.hypot(x - kx, y - ky) < kr for kx, ky, kr in keep_out):
                continue
            cuts.append(Pos(x, y, -0.033) * proto)
    plate = plate - cuts
    parts.append(_finish(plate, "main_plate", S.COPPER_GOLD))

    for (x, y), name in zip(lower, ("center", "third", "fourth", "escape",
                                    "pallet", "balance")):
        ruby = Pos(x, y, -S.PLATE_THICKNESS) * Rot(180, 0, 0) * F.jeweled_bearing()
        parts.append(_finish(ruby, f"plate_jewel:{name}", S.RUBY))
    # Incabloc-style polished setting hint at the balance, dial side
    ring = Pos(lx, ly, 0) * _ring(0.65, 1.2, 0.18, -1.68)
    parts.append(_finish(ring, "balance_lower_shock_ring", S.STEEL_BRIGHT))
    cap = Pos(lx, ly, 0) * _cyl(0.5, 0.2, -1.58)
    parts.append(_finish(cap, "balance_lower_cap_jewel", S.RUBY_BRIGHT))
    return parts


# ---------------------------------------------------------------------------
# 2. Barrel group (drum + tooth flange, lid, mainspring, arbor)
# ---------------------------------------------------------------------------

def _barrel_parts():
    parts = []
    bx, by = S.BARREL_POS
    rot_flange = _ang(S.BARREL_POS, S.CENTER_WHEEL_POS)

    flange = Rot(0, 0, rot_flange) * F.train_wheel(
        S.BARREL_TEETH, _BARREL_GEAR_D, web_thickness=0.30, spoke_count=0,
        tooth_depth_frac=0.12, color=S.BRASS_MOVEMENT)
    drum = (
        _cyl(6.0, _DRUM_Z[1] - _DRUM_Z[0], _DRUM_Z[0])
        - _cyl(5.5, 1.2, _DRUM_Z[0] + 0.15)
        + Pos(0, 0, (_FLANGE_Z[0] + _FLANGE_Z[1]) / 2.0) * flange
    )
    drum = drum - [_cyl(0.85, 3.0, 0.2), _cyl(5.44, 1.2, 0.9)]
    drum = Pos(bx, by, 0) * drum
    parts.append(_finish(drum, "barrel_drum", S.BRASS_MOVEMENT))

    lid = _cyl(5.42, 0.15, 1.45) - _cyl(0.85, 1.0, 1.2)
    parts.append(_finish(Pos(bx, by, 0) * lid, "barrel_lid", S.BRASS_MOVEMENT))

    coils = Pos(2.65, -0.05, 0) * Box(3.9, 0.1, 0.6) * 0 if False else None
    coils = Pos(0, 0, 1.0) * Box(3.9, 0.1, 0.6)
    coils = Pos(2.68, 0, 0) * coils  # radial tie keeping the coils one solid
    for r in (0.8, 1.7, 2.6, 3.5, 4.4):
        coils = coils + _ring(r, r + 0.16, 0.6, 0.7)
    parts.append(_finish(Pos(bx, by, 0) * coils, "mainspring", S.STEEL_DARK))

    # raised polished winding square: proud of the ratchet wheel with a
    # chamfered crown so it reads clearly 3D under the blued ratchet screw
    square = Pos(0, 0, (2.80 + RATCHET_SQUARE_TOP_Z) / 2.0) * Box(
        1.42, 1.42, RATCHET_SQUARE_TOP_Z - 2.80)
    square, _ = F.safe_chamfer(
        square,
        [e for e in square.edges()
         if abs(e.bounding_box().max.Z - RATCHET_SQUARE_TOP_Z) < 1e-6
         and abs(e.bounding_box().min.Z - RATCHET_SQUARE_TOP_Z) < 1e-6],
        0.12)
    arbor = (
        _cyl(0.5, 1.75, -1.30)             # lower pivot into the plate
        + _cyl(0.675, 2.42, 0.45)          # body through drum/lid/bridge jewel
        + square
    )
    parts.append(_finish(Pos(bx, by, 0) * arbor, "barrel_arbor", S.STEEL_DARK))
    return parts


# ---------------------------------------------------------------------------
# 3. Going train (wheels GILT, pinions+arbors STEEL_DARK, mesh-phased)
# ---------------------------------------------------------------------------

def _train_parts():
    parts = []

    def wheel(label, teeth, dia, pos, z_mid, partner, frac, thick=0.18, bore=0.34):
        w = F.train_wheel(teeth, dia, web_thickness=thick, tooth_depth_frac=frac)
        w = w - [_window_cutter(dia, frac), _cyl(bore, 2.0, -1.0)]
        w = Pos(pos[0], pos[1], z_mid) * Rot(0, 0, _ang(pos, partner)) * w
        parts.append(_finish(w, label, S.GILT))

    def pinion(label, leaves, dia, pos, z_band, partner, arbor_top=2.18):
        p = F.pinion(leaves, dia, z_band[1] - z_band[0])  # spans z [0, L]
        rot = _ang(pos, partner) + 180.0 / leaves
        body = (Pos(0, 0, z_band[0]) * Rot(0, 0, rot) * p
                + _cyl(_ARBOR_R, arbor_top + 1.28, -1.28))
        parts.append(_finish(Pos(pos[0], pos[1], 0) * body, label, S.STEEL_DARK))

    cw, tw, fw, ew = (S.CENTER_WHEEL_POS, S.THIRD_WHEEL_POS,
                      S.FOURTH_WHEEL_POS, S.ESCAPE_WHEEL_POS)
    wheel("center_wheel", S.CENTER_WHEEL_TEETH, _CENTER_WHEEL_D, cw, _CW_Z, tw, 0.11)
    pinion("center_pinion", S.CENTER_PINION_LEAVES, _CENTER_PINION_D, cw, _CP_Z,
           S.BARREL_POS)
    wheel("third_wheel", S.THIRD_WHEEL_TEETH, _THIRD_WHEEL_D, tw, _TW_Z, fw, 0.10)
    pinion("third_pinion", S.THIRD_PINION_LEAVES, _THIRD_PINION_D, tw, _TP_Z, cw)
    wheel("fourth_wheel", S.FOURTH_WHEEL_TEETH, _FOURTH_WHEEL_D, fw, _FW_Z, ew, 0.12)
    pinion("fourth_pinion", S.FOURTH_PINION_LEAVES, _FOURTH_PINION_D, fw, _FP_Z, tw)
    pinion("escape_pinion", S.ESCAPE_PINION_LEAVES, _ESCAPE_PINION_D, ew, _EP_Z, fw)
    return parts


# ---------------------------------------------------------------------------
# 4. Bridges, screws, upper jewels, ratchet/crown/click layer
# ---------------------------------------------------------------------------

def _bridge_parts():
    parts = []
    z0, z1 = BRIDGE_SEAT_Z, BRIDGE_TOP_Z
    bx, by = S.BARREL_POS
    cwx, cwy = S.CROWN_WHEEL_POS

    # barrel bridge -------------------------------------------------------
    extra = [
        Pos(cwx, cwy, 0) * _cyl(0.6, 1.4, z1 - 0.9),      # crown-wheel screw bore
        Pos(3.74, 3.87, 0) * _cyl(0.3, 1.2, z1 - 0.7),    # click screw bore
        Pos(5.3, 4.6, 0) * _cyl(0.26, 1.2, z1 - 0.7),     # click-spring screw bore
    ]
    zb = z1 + _BOSS_H                     # striped-top + raised jewel boss
    bb = _bridge(BARREL_BRIDGE_OUTLINE, BRIDGE_CLIP_R, z0, z1, [],
                 [BRIDGE_SCREW_POSITIONS["barrel_bridge:left"],
                  BRIDGE_SCREW_POSITIONS["barrel_bridge:right"],
                  BRIDGE_SCREW_POSITIONS["barrel_bridge:top"]],
                 extra_cuts=extra, boss_xy=[S.BARREL_POS])
    parts.append(_finish(bb, "barrel_bridge", S.COPPER_GOLD))
    ruby = F.jeweled_bearing(surface_z=0.0) - _cyl(0.725, 1.4, -1.2)
    parts.append(_finish(Pos(bx, by, zb) * ruby, "bridge_jewel:barrel", S.RUBY))

    # train bridge --------------------------------------------------------
    train_jewels = [S.CENTER_WHEEL_POS, S.THIRD_WHEEL_POS,
                    S.FOURTH_WHEEL_POS, S.ESCAPE_WHEEL_POS]
    tb = _bridge(TRAIN_BRIDGE_OUTLINE, BRIDGE_CLIP_R, z0, z1, [],
                 [BRIDGE_SCREW_POSITIONS["train_bridge:foot"],
                  BRIDGE_SCREW_POSITIONS["train_bridge:fourth"],
                  BRIDGE_SCREW_POSITIONS["train_bridge:center"]],
                 cutouts=TRAIN_BRIDGE_CUTOUTS, boss_xy=train_jewels)
    parts.append(_finish(tb, "train_bridge", S.COPPER_GOLD))
    for (x, y), name in zip(train_jewels, ("center", "third", "fourth", "escape")):
        parts.append(_finish(Pos(x, y, zb) * F.jeweled_bearing(),
                             f"bridge_jewel:{name}", S.RUBY))
        chaton = Pos(x, y, 0) * _ring(0.78, 1.05, 0.07, zb - S.JEWEL_COUNTERSINK_DEPTH - 0.02)
        parts.append(_finish(chaton, f"chaton:{name}", S.BRASS_MOVEMENT))

    # bridge screws (shanks stay inside the bridge bore: longer ones hung
    # visibly in the open gap between the low plate bosses and the seat)
    for name, shank in (("barrel_bridge:left", 0.9), ("barrel_bridge:right", 0.9),
                        ("barrel_bridge:top", 0.75), ("train_bridge:foot", 0.9),
                        ("train_bridge:fourth", 0.9), ("train_bridge:center", 0.75)):
        x, y = BRIDGE_SCREW_POSITIONS[name]
        parts.append(_finish(_place_screw(x, y, z1, shank=shank),
                             f"screw:{name}", S.BLUED))

    # ratchet wheel + raised winding square + blued screw ------------------
    ratchet = F.train_wheel(48, S.RATCHET_WHEEL_DIAMETER, web_thickness=0.26,
                            spoke_count=0, tooth_depth_frac=0.06)
    ratchet = ratchet - Pos(0, 0, 0.13) * F.snailing_cutter(9.2, 2.2)
    beak_gap = _ang(S.BARREL_POS, (2.52, 4.9)) + 3.75
    ratchet = Pos(bx, by, (RATCHET_Z0 + RATCHET_TOP_Z) / 2.0) * Rot(0, 0, beak_gap) * ratchet
    # square hole cut AFTER the mesh-phasing rotation so it stays aligned
    # with the arbor's raised winding square (they clashed when the hole
    # rotated with the wheel)
    ratchet = ratchet - Pos(bx, by, 3.0) * Box(1.52, 1.52, 1.0)
    parts.append(_finish(ratchet, "ratchet_wheel", S.GILT))
    parts.append(_finish(
        _place_screw(bx, by, RATCHET_SQUARE_TOP_Z, proud=0.04, head_d=1.3,
                     hh=0.34, shank=1.4, slot_w=0.30),
        "screw:ratchet", S.BLUED))

    # crown wheel + recessed polished steel core --------------------------
    crown = F.train_wheel(30, S.CROWN_WHEEL_DIAMETER, web_thickness=0.24,
                          spoke_count=0, tooth_depth_frac=0.08)
    crown = crown - [Pos(0, 0, 0.12) * F.snailing_cutter(5.6, 1.8),
                     _cyl(0.62, 1.0, -0.5),
                     _cyl(1.35, 0.4, 0.02)]     # core recess in the snailed top
    parts.append(_finish(Pos(cwx, cwy, 2.99) * crown, "crown_wheel", S.GILT))
    parts.append(_finish(
        _place_screw(cwx, cwy, 3.11, proud=0.08, head_d=2.2, hh=0.18, shank=0.9,
                     slot_w=0.34, color=S.STEEL_BRIGHT),
        "crown_wheel_core", S.STEEL_BRIGHT))

    # click + click spring (polished steel, engaging the ratchet) ---------
    # smooth polished lever: dense disk chain beak -> pivot -> tail so the
    # envelope reads as one continuous curve (sparse chains scallop)
    click_pts = []
    for (xa, ya, ra), (xb, yb, rb), n in (((2.52, 4.90, 0.22), (3.74, 3.87, 0.55), 12),
                                          ((3.74, 3.87, 0.55), (4.75, 3.12, 0.32), 10)):
        for i in range(n + 1):
            t = i / n
            tt = t * t * (3 - 2 * t)      # smoothstep radius blend
            click_pts.append((xa + (xb - xa) * t, ya + (yb - ya) * t,
                              ra + (rb - ra) * tt))
    click_prof = _blob(click_pts, 14.0)
    click = Pos(0, 0, 2.9) * extrude(click_prof, amount=0.16)
    click, _ = F.anglage_top(click, 0.06)
    click = click - Pos(3.74, 3.87, 0) * _cyl(0.3, 1.0, 2.6)
    parts.append(_finish(click, "click", S.STEEL_LEVER))
    parts.append(_finish(
        _place_screw(3.74, 3.87, 3.06, proud=0.0, head_d=1.0, hh=0.24, shank=0.7),
        "screw:click", S.BLUED))

    # blade drawn as a dense chain of overlapping disks along the Bezier
    # (an offset-strip Polygon self-crossed at the tight bend; validate
    # flagged it selfIntersecting)
    p0, p1, p2 = (5.2, 4.3), (5.5, 3.7), (4.98, 3.28)
    blade = []
    for i in range(25):
        t = i / 24.0
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        blade.append((x, y, 0.09))
    spring_prof = _blob(blade, 14.0) + Pos(5.3, 4.6) * Circle(0.4)
    spring = Pos(0, 0, 2.9) * extrude(spring_prof, amount=0.14)
    spring = spring - Pos(5.3, 4.6, 0) * _cyl(0.26, 1.0, 2.6)
    parts.append(_finish(spring, "click_spring", S.STEEL_LEVER))
    parts.append(_finish(
        _place_screw(5.3, 4.6, 3.04, proud=0.0, head_d=0.9, hh=0.2, shank=0.6),
        "screw:click_spring", S.BLUED))
    return parts


# ---------------------------------------------------------------------------
# 5. Escapement: club-tooth escape wheel, pallet fork + stones, pallet bridge
# ---------------------------------------------------------------------------

def _escape_wheel():
    # inner chord sunk to r1.55 so every tooth overlaps the rim band (a
    # chord at exactly r1.70 touched the rim at ONE point -> 15 disjoint
    # tooth solids, caught by inspect validate)
    tooth = Polygon((1.55, 0.30), (1.55, -0.05), (2.30, -0.45), (2.62, -0.55),
                    (2.70, -0.30), (2.48, -0.12), (2.02, 0.24), align=None)
    prof = Circle(1.70) + [Rot(0, 0, 360.0 * k / S.ESCAPE_WHEEL_TEETH) * tooth
                           for k in range(S.ESCAPE_WHEEL_TEETH)]
    prof = prof - (Circle(1.42) - Circle(0.5))
    spokes = None
    for k in range(4):
        sp = Rot(0, 0, 45 + 90 * k) * Polygon(
            (-1.5, -0.14), (1.5, -0.14), (1.5, 0.14), (-1.5, 0.14), align=None)
        spokes = sp if spokes is None else spokes + sp
    prof = prof + (spokes & Circle(1.6))
    wheel = extrude(prof, amount=0.18)          # spans z [0, 0.18]
    wheel = wheel - _cyl(0.33, 1.0, -0.4)
    ex, ey = S.ESCAPE_WHEEL_POS
    wheel = Pos(ex, ey, _EW_Z - 0.09) * Rot(0, 0, 7.0) * wheel
    return _finish(wheel, "escape_wheel", S.GILT)


def _pallet_parts():
    parts = []
    pp = S.PALLET_FORK_POS
    ec = S.ESCAPE_WHEEL_POS
    bal = S.BALANCE_POS
    th_b = math.radians(_ang(pp, bal))
    dir_b = (math.cos(th_b), math.sin(th_b))
    perp = (-dir_b[1], dir_b[0])
    th_pe = math.radians(_ang(ec, pp))
    stones = []
    for dth, name, ax in ((-30.0, "entry", 38.5), (30.0, "exit", 98.5)):
        a = th_pe + math.radians(dth)
        sx, sy = ec[0] + 2.85 * math.cos(a), ec[1] + 2.85 * math.sin(a)
        stones.append((sx, sy, name, ax))

    def along(t, off=0.0):
        return (pp[0] + t * dir_b[0] + off * perp[0],
                pp[1] + t * dir_b[1] + off * perp[1])

    circles = [(pp[0], pp[1], 0.5)]
    for sx, sy, _n, _a in stones:
        circles.append((sx, sy, 0.42))
        circles.append(((pp[0] + sx) / 2, (pp[1] + sy) / 2, 0.5))
    for t, r in ((1.2, 0.38), (2.4, 0.34), (3.4, 0.30)):
        x, y = along(t)
        circles.append((x, y, r))
    for sgn in (1.0, -1.0):
        x, y = along(3.85, sgn * 0.30)
        circles.append((x, y, 0.24))
    prof = _blob(circles, 14.0)
    nx, ny = along(4.15)
    prof = prof - Pos(nx, ny) * Circle(0.18)
    fork = Pos(0, 0, _FORK_Z[0]) * extrude(prof, amount=_FORK_Z[1] - _FORK_Z[0])
    fork, _ = F.anglage_top(fork, 0.05)

    stone_cuts, stone_parts = [], []
    for sx, sy, name, ax in stones:
        slab = Pos(sx, sy, 1.17) * Rot(0, 0, ax) * Box(0.6, 0.24, 0.34)
        pad = Pos(sx, sy, 1.30) * Rot(0, 0, ax) * Box(0.66, 0.30, 0.44)
        stone_cuts.append(pad)
        stone_parts.append(_finish(slab, f"pallet_stone:{name}", S.RUBY_BRIGHT))
    fork = fork - (stone_cuts + [Pos(pp[0], pp[1], 0) * _cyl(0.21, 1.0, 0.9)])
    gx, gy = along(3.6)
    fork = fork + Pos(gx, gy, 0) * _cyl(0.07, 0.18, 1.13)   # guard pin
    parts.append(_finish(fork, "pallet_fork", S.STEEL_BRIGHT))
    parts.extend(stone_parts)

    arbor = Pos(pp[0], pp[1], 0) * _cyl(0.2, 2.72, -1.28)
    parts.append(_finish(arbor, "pallet_arbor", S.STEEL_DARK))

    # pallet bridge: small striped copper cock, 1 jewel, 1 blued screw
    pbz0, pbz1 = PALLET_BRIDGE_Z
    pb = _bridge(PALLET_BRIDGE_OUTLINE, COCK_CLIP_R, pbz0, pbz1,
                 [pp], [BRIDGE_SCREW_POSITIONS["pallet_bridge"]])
    parts.append(_finish(pb, "pallet_bridge", S.COPPER_GOLD))
    ruby = Pos(pp[0], pp[1], pbz1 - S.JEWEL_COUNTERSINK_DEPTH - 0.15) * F.jewel(thickness=0.26)
    parts.append(_finish(ruby, "bridge_jewel:pallet", S.RUBY))
    x, y = BRIDGE_SCREW_POSITIONS["pallet_bridge"]
    parts.append(_finish(_place_screw(x, y, pbz1, shank=0.6),
                         "screw:pallet_bridge", S.BLUED))
    return parts


# ---------------------------------------------------------------------------
# 6. Balance assembly: wheel + timing screws, hairspring, staff/rollers,
#    cock with stripes/anglage, lyre-spring shock setting, regulator, stud
# ---------------------------------------------------------------------------

def _balance_parts():
    parts = []
    lx, ly = S.BALANCE_POS
    pp = S.PALLET_FORK_POS
    z_rim0, z_rim1 = BALANCE_RIM_Z

    rim = _ring(4.1, 4.8, z_rim1 - z_rim0, z_rim0)
    arms = Pos(0, 0, 2.14) * Rot(0, 0, 30) * Box(8.5, 0.85, 0.2)
    hub = _cyl(0.65, z_rim1 - z_rim0, z_rim0)
    slits = [Rot(0, 0, 120 + 180 * k) * Pos(4.45, 0, 2.225) * Box(0.9, 0.12, 0.6)
             for k in range(2)]
    wheel = (rim + arms + hub) - (slits + [_cyl(0.28, 1.0, 1.8)])
    parts.append(_finish(Pos(lx, ly, 0) * wheel, "balance_wheel", S.BRASS_MOVEMENT))

    for k in range(16):
        a = 11.25 + k * 22.5
        s = (Pos(4.85, 0, 2.225) * Rot(0, 90, 0) * Cylinder(0.16, 0.30)
             + Pos(5.03, 0, 2.225) * Rot(0, 90, 0) * Cylinder(0.22, 0.14))
        s = Pos(lx, ly, 0) * Rot(0, 0, a) * s
        parts.append(_finish(s, f"timing_screw:{k}", S.BRASS_MOVEMENT))

    # staff + safety/impulse rollers + collet
    th_p = math.radians(_ang(S.BALANCE_POS, pp))
    pinx = lx + 0.55 * math.cos(th_p)
    piny = ly + 0.55 * math.sin(th_p)
    staff = (_cyl(0.27, 4.26, -1.28)
             + _cyl(0.55, 0.13, 1.05)          # impulse roller
             + _cyl(0.35, 0.10, 1.18)          # safety roller
             + _cyl(0.375, 0.22, 2.46))        # collet
    staff = Pos(lx, ly, 0) * staff
    staff = staff - Pos(pinx, piny, 0) * _cyl(0.10, 0.8, 1.0)
    parts.append(_finish(staff, "balance_staff", S.STEEL_DARK))
    pin = Pos(pinx, piny, 0) * _cyl(0.09, 0.42, 1.05)
    parts.append(_finish(pin, "impulse_jewel", S.RUBY_BRIGHT))

    # hairspring: 12-coil Archimedean band ending at the stud angle (-40 deg)
    r0, pitch, turns, width = 0.42, 0.19, 12, 0.045
    phi0 = math.radians(-40.0)
    outer, inner = [], []
    steps = turns * 36
    for i in range(steps + 1):
        th = 2 * math.pi * turns * i / steps
        r = r0 + pitch * th / (2 * math.pi)
        a = phi0 + th
        outer.append(((r + width) * math.cos(a), (r + width) * math.sin(a)))
        inner.append((r * math.cos(a), r * math.sin(a)))
    poly = Polygon(*(outer + list(reversed(inner))), align=None)
    spring = Pos(lx, ly, 2.52) * extrude(poly, amount=0.12)
    parts.append(_finish(spring, "hairspring", S.STEEL_DARK))

    # stud: steel pin dropping from the stud holder to the spring's outer end
    cz0, cz1 = COCK_Z
    sx = lx + 2.85 * math.cos(math.radians(-40))
    sy = ly + 2.85 * math.sin(math.radians(-40))
    stud = Pos(sx, sy, 0) * (_cyl(0.15, cz1 - 2.34, 2.34) + _cyl(0.22, 0.20, 2.42))
    parts.append(_finish(stud, "hairspring_stud", S.STEEL_DARK))

    # balance cock ---------------------------------------------------------
    bootx = lx + 2.85 * math.cos(math.radians(-95))
    booty = ly + 2.85 * math.sin(math.radians(-95))
    extra = [
        Pos(lx, ly, 0) * _cyl(1.28, 1.2, cz0 - 0.3),        # shock-setting seat
        Pos(bootx, booty, 0) * _cyl(0.17, 1.2, cz0 - 0.3),  # regulator boot bore
        Pos(sx, sy, 0) * _cyl(0.18, 1.2, cz0 - 0.3),        # stud bore
    ]
    cock = _bridge(BALANCE_COCK_OUTLINE, COCK_CLIP_R, cz0, cz1, [],
                   [BRIDGE_SCREW_POSITIONS["balance_cock"]], extra_cuts=extra)
    parts.append(_finish(cock, "balance_cock", S.COPPER_GOLD))
    x, y = BRIDGE_SCREW_POSITIONS["balance_cock"]
    parts.append(_finish(_place_screw(x, y, cz1, shank=0.8),
                         "screw:balance_cock", S.BLUED))

    # stud holder: small polished plate on the cock's side lobe, clamping
    # the stud with its own tiny blued screw
    holder2d = _blob(((sx, sy, 0.45), (1.45, -9.20, 0.42), (1.12, -8.98, 0.40)), 14.0)
    holder = Pos(0, 0, cz1) * extrude(holder2d, amount=0.12)
    holder, _ = F.anglage_top(holder, 0.05)
    holder = holder - Pos(1.12, -8.98, 0) * _cyl(0.14, 1.0, cz1 - 0.4)
    parts.append(_finish(holder, "stud_holder", S.STEEL_LEVER))
    parts.append(_finish(
        _place_screw(1.12, -8.98, cz1 + 0.12, proud=0.02, head_d=0.8, hh=0.2,
                     shank=0.5),
        "screw:stud_holder", S.BLUED))

    # shock setting: polished bezel, gold chaton, cap jewel, lyre spring
    bezel = Pos(lx, ly, 0) * (_ring(0.85, 1.25, 0.66, 2.72)
                              - Pos(0, 0, 3.38) * Cone(1.3, 0.9, 0.16))
    parts.append(_finish(bezel, "shock_bezel", S.STEEL_BRIGHT))
    gold = Pos(lx, ly, 0) * _ring(0.48, 0.83, 0.5, 2.84)
    parts.append(_finish(gold, "shock_chaton", S.BRASS_MOVEMENT))
    cap = Pos(lx, ly, 3.25) * F.jewel(diameter=0.95, thickness=0.3)
    parts.append(_finish(cap, "shock_cap_jewel", S.RUBY_BRIGHT))
    # lyre spring: 300-degree sprung ring with two feet
    ring2d = (Circle(1.12) - Circle(0.92)) - Rot(0, 0, -40) * Polygon(
        (0.0, 0.0), (2.5, -0.65), (2.5, 0.65), align=None)
    feet = (Rot(0, 0, -40 + 15) * Pos(1.02, 0) * Circle(0.16)
            + Rot(0, 0, -40 - 15) * Pos(1.02, 0) * Circle(0.16))
    lyre = Pos(lx, ly, 3.30) * extrude(ring2d + feet, amount=0.08)
    parts.append(_finish(lyre, "shock_lyre_spring", S.STEEL_BRIGHT))

    # regulator: black-polished ring + tapered index lever + boot through
    # the cock (curb pins gripping the hairspring's outer coil)
    tail_a = -95.0
    reg2d = Pos(lx, ly) * (Circle(1.72) - Circle(1.36))
    tail = Polygon((-0.27, 1.42), (0.27, 1.42), (0.10, 4.05), (-0.10, 4.05),
                   align=None)
    tip = Polygon((-0.10, 4.05), (0.10, 4.05), (0.0, 4.55), align=None)
    reg2d = reg2d + Pos(lx, ly) * Rot(0, 0, tail_a - 90) * (tail + tip)
    reg = Pos(0, 0, cz1 + 0.01) * extrude(reg2d, amount=0.08)
    boot = Pos(bootx, booty, 0) * _cyl(0.14, 0.82, 2.50)
    parts.append(_finish(reg + boot, "regulator", S.STEEL_DARK))
    return parts


def build_base():
    """All movement-base parts, labeled and colored, in the movement frame."""
    parts = []
    parts += _plate_parts()
    parts += _barrel_parts()
    parts += _train_parts()
    parts += _bridge_parts()
    parts.append(_escape_wheel())
    parts += _pallet_parts()
    parts += _balance_parts()
    return parts
