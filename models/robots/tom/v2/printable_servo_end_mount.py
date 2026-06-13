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
        "servo_end_mount",
        sheet_thickness_mm=PRINTABLE_EQUIVALENT_THICKNESS_MM,
    ) as module:
        if not module.SERVO_STEP.exists():
            raise FileNotFoundError(f"Missing STS3250 servo STEP: {module.SERVO_STEP}")
        servo_shape = module.import_as_shape(module.SERVO_STEP)
        bracket, _layout = module.build_bracket(
            servo_shape,
            include_top_center_bridge=True,
            validate_bend_rule=False,
        )
        solids = bracket.solids()
        if len(solids) != 1:
            raise RuntimeError(f"Expected one connected printable bracket solid, found {len(solids)}")
        intersection_volume = servo_shape.intersect(bracket).volume
        if intersection_volume > 1e-3:
            raise RuntimeError(f"Printable bracket intersects the servo by {intersection_volume:.6f} mm^3")
        print(
            "Printable servo end mount "
            f"thickness={PRINTABLE_EQUIVALENT_THICKNESS_MM:.3f} mm"
        )
        return finish_part(bracket, PART_NAME, printable=True)


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }
