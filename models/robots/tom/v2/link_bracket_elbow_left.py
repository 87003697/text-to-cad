from __future__ import annotations

from pathlib import Path

import link_common as lc
from v2_variant_common import finish_part, loaded_v2_module, mirror_y


PART_NAME = Path(__file__).stem


def build_step():
    with loaded_v2_module(
        "link_bracket",
        case_span_centering_offset_mm=lc.ELBOW_ROLL_CASE_SPAN_CENTERING_OFFSET_MM,
    ) as module:
        return finish_part(mirror_y(module.build_step()), PART_NAME)


def gen_step() -> dict[str, object]:
    return {
        "shape": build_step(),
    }
