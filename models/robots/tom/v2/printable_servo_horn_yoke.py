from __future__ import annotations

from pathlib import Path

from v2_variant_common import (
    PRINTABLE_EQUIVALENT_THICKNESS_MM,
    finish_part,
    loaded_v2_module,
)


PART_NAME = Path(__file__).stem


def build_step():
    with loaded_v2_module(
        "servo_horn_yoke",
        sheet_thickness_mm=PRINTABLE_EQUIVALENT_THICKNESS_MM,
    ) as module:
        inputs = module._load_generator_inputs()
        bracket, _layout, _metrics = module.build_bracket(
            inputs.reference_face_x,
            sheet_half_width_z=inputs.sheet_half_width_z,
            upper_horn=inputs.upper_horn,
            lower_horn=inputs.lower_horn,
            rule=module.SHEET_RULE,
            validate_metrics=False,
        )
        solids = bracket.solids()
        if len(solids) != 1:
            raise RuntimeError(f"Expected one connected printable yoke solid, found {len(solids)}")
        intersection_volume = inputs.servo_shape.intersect(bracket).volume
        if intersection_volume > 1e-3:
            raise RuntimeError(f"Printable yoke intersects the servo by {intersection_volume:.6f} mm^3")
        print(
            "Printable servo horn yoke "
            f"thickness={PRINTABLE_EQUIVALENT_THICKNESS_MM:.3f} mm"
        )
        return finish_part(bracket, PART_NAME, printable=True)


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }
