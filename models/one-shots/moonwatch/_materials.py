"""Per-part material assignment for the whole watch.

The render pipeline supports per-occurrence PBR overrides: any leaf shape
carrying a plain ``cad_material`` dict ({roughness, metalness, clearcoat,
clearcoatRoughness, opacity} in [0,1]) has those channels override the
presentation theme's global material (see cadgen
``_occurrence_material`` and cadjs ``applyMaterialSettingsToRecord``).
Albedo still comes from ``shape.color``.

``apply(compound)`` walks a built Compound's leaves and assigns materials
by label using ordered fnmatch rules — first match wins. Every entry calls
it on the Compound it returns, so material assignment lives in ONE place
for the whole project.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

# --- material classes -------------------------------------------------------

POLISHED_STEEL = {"roughness": 0.08, "metalness": 1.0, "clearcoat": 0.25, "clearcoatRoughness": 0.08}
BRUSHED_STEEL = {"roughness": 0.42, "metalness": 0.92, "clearcoat": 0.05, "clearcoatRoughness": 0.4}
SATIN_STEEL = {"roughness": 0.30, "metalness": 0.95, "clearcoat": 0.1, "clearcoatRoughness": 0.2}
GRAINED_LEVER = {"roughness": 0.34, "metalness": 0.95, "clearcoat": 0.08, "clearcoatRoughness": 0.2}
BLUED_SCREW = {"roughness": 0.10, "metalness": 0.92, "clearcoat": 0.55, "clearcoatRoughness": 0.06}
COPPER_PLATE = {"roughness": 0.34, "metalness": 0.90, "clearcoat": 0.08, "clearcoatRoughness": 0.3}
ANGLAGE_MIRROR = {"roughness": 0.05, "metalness": 1.0, "clearcoat": 0.3, "clearcoatRoughness": 0.05}
GILT_WHEEL = {"roughness": 0.24, "metalness": 0.95, "clearcoat": 0.1, "clearcoatRoughness": 0.15}
BRASS_PART = {"roughness": 0.38, "metalness": 0.9}
BLACK_LACQUER = {"roughness": 0.38, "metalness": 0.0, "clearcoat": 0.9, "clearcoatRoughness": 0.08}
MATTE_PRINT = {"roughness": 0.85, "metalness": 0.0, "clearcoat": 0.0}
LUME_PLOT = {"roughness": 0.65, "metalness": 0.0, "clearcoat": 0.15, "clearcoatRoughness": 0.3}
PAINTED_HAND = {"roughness": 0.28, "metalness": 0.25, "clearcoat": 0.6, "clearcoatRoughness": 0.1}
BEZEL_ANODIZED = {"roughness": 0.5, "metalness": 0.35, "clearcoat": 0.2, "clearcoatRoughness": 0.3}
GLASS = {"roughness": 0.04, "metalness": 0.0, "clearcoat": 1.0, "clearcoatRoughness": 0.02}
RUBBER = {"roughness": 0.9, "metalness": 0.0, "clearcoat": 0.0}
RUBY_JEWEL = {"roughness": 0.05, "metalness": 0.0, "clearcoat": 1.0, "clearcoatRoughness": 0.03}

# --- ordered label rules (first match wins) ---------------------------------

RULES: list[tuple[str, dict]] = [
    # glass
    ("crystal", GLASS),
    ("caseback_sapphire", GLASS),
    # gaskets / o-rings / rubber
    ("*o_ring*", RUBBER),
    ("*gasket*", RUBBER),
    # dial furniture
    ("dial_plate", BLACK_LACQUER),
    ("subdial_print*", MATTE_PRINT),
    ("minute_track", MATTE_PRINT),
    ("index_lume", LUME_PLOT),
    ("index_frames", MATTE_PRINT),
    ("*_lume", LUME_PLOT),
    ("hand:*cap*", POLISHED_STEEL),
    ("hub:*", POLISHED_STEEL),
    ("hand:*", PAINTED_HAND),
    ("dial_foot*", BRASS_PART),
    # bezel
    ("bezel_insert", BEZEL_ANODIZED),
    ("tachymeter_scale", MATTE_PRINT),
    ("bezel_ring", POLISHED_STEEL),
    # case
    ("case_middle", BRUSHED_STEEL),
    ("caseback_retaining_ring", SATIN_STEEL),
    ("caseback", BRUSHED_STEEL),
    ("crown*", POLISHED_STEEL),
    ("pusher_cap*", POLISHED_STEEL),
    ("pusher_tube*", POLISHED_STEEL),
    ("pusher_spring*", SATIN_STEEL),
    ("spring_bar*", SATIN_STEEL),
    # bracelet
    ("link_*_center", POLISHED_STEEL),
    ("link_*", BRUSHED_STEEL),
    ("end_link*", BRUSHED_STEEL),
    ("pin_*", SATIN_STEEL),
    ("clasp_flip_lock", POLISHED_STEEL),
    ("clasp_pusher*", POLISHED_STEEL),
    ("clasp_*", BRUSHED_STEEL),
    # movement: jewels, screws, finishing overlays
    ("*jewel*", RUBY_JEWEL),
    ("shock_cap*", RUBY_JEWEL),
    ("screw:*", BLUED_SCREW),
    ("timing_screw*", GILT_WHEEL),
    ("*:anglage*", ANGLAGE_MIRROR),
    ("*:stripe_shadow*", COPPER_PLATE),
    ("chaton:*", BRASS_PART),
    ("shock_chaton", BRASS_PART),
    ("shock_bezel", POLISHED_STEEL),
    ("shock_lyre_spring", POLISHED_STEEL),
    # movement: plates and bridges
    ("main_plate", COPPER_PLATE),
    ("*bridge*", COPPER_PLATE),
    ("balance_cock", COPPER_PLATE),
    # movement: wheels and steel
    ("ratchet_wheel", GILT_WHEEL),
    ("crown_wheel_core", POLISHED_STEEL),
    ("crown_wheel", GILT_WHEEL),
    ("*_wheel", GILT_WHEEL),
    ("escape_wheel", POLISHED_STEEL),
    ("*pinion*", POLISHED_STEEL),
    ("barrel_arbor", POLISHED_STEEL),
    ("barrel*", BRASS_PART),
    ("mainspring", SATIN_STEEL),
    ("click*", GRAINED_LEVER),
    ("pallet_fork", GRAINED_LEVER),
    ("pallet_stone*", RUBY_JEWEL),
    ("pallet_arbor", POLISHED_STEEL),
    ("balance_wheel", GILT_WHEEL),
    ("balance_staff", POLISHED_STEEL),
    ("impulse_jewel", RUBY_JEWEL),
    ("hairspring*", SATIN_STEEL),
    ("stud_holder", POLISHED_STEEL),
    ("regulator", POLISHED_STEEL),
    # keyless
    ("stem", POLISHED_STEEL),
    ("winding_pinion", POLISHED_STEEL),
    ("sliding_pinion", POLISHED_STEEL),
    ("setting_lever*", GRAINED_LEVER),
    ("yoke", GRAINED_LEVER),
    # chronograph works (labels prefixed chrono/ column/ hammer/ etc.)
    ("column_wheel*", POLISHED_STEEL),
    ("chrono_*", GILT_WHEEL),
    ("coupling_*", GRAINED_LEVER),
    ("operating_*", GRAINED_LEVER),
    ("reset_*", GRAINED_LEVER),
    ("brake_*", GRAINED_LEVER),
    ("hammer*", POLISHED_STEEL),
    ("*recorder*", GILT_WHEEL),
    ("*jumper*", GRAINED_LEVER),
    ("*spring*", SATIN_STEEL),
    ("heart_cam*", POLISHED_STEEL),
    # assembly
    ("movement_ring", BRUSHED_STEEL),
]


def material_for_label(label: str) -> dict | None:
    name = str(label or "")
    for pattern, material in RULES:
        if fnmatchcase(name, pattern):
            return material
    return None


def apply(node) -> None:
    """Walk a built Compound tree and assign ``cad_material`` to every leaf
    whose label matches a rule. Mutates shapes in place (safe: entries call
    this on the final tree right before returning it)."""
    children = list(getattr(node, "children", []) or [])
    if not children:
        material = material_for_label(getattr(node, "label", ""))
        if material is not None:
            node.cad_material = material
        return
    for child in children:
        apply(child)
