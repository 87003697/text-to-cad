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
        "link_bracket",
        sheet_thickness_mm=PRINTABLE_EQUIVALENT_THICKNESS_MM,
    ) as module:
        bracket = module.build_bracket()
        top_servo, bottom_servo = module.lc.placed_link_servos_case()
        module.lc.verify_no_interference(bracket, top_servo, label="the top servo")
        module.lc.verify_no_interference(bracket, bottom_servo, label="the bottom servo")
        print(
            "Printable link bracket "
            f"thickness={PRINTABLE_EQUIVALENT_THICKNESS_MM:.3f} mm"
        )
        return finish_part(bracket, PART_NAME, printable=True)


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }
