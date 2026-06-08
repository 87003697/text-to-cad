#!/usr/bin/env python3
from __future__ import annotations

from gripper_component_extract import extract_occurrences


RIGHT_CLAW_SELECTORS = (
    "o1.2",  # RB9.01.061.020 Clamp
    "o1.3",  # Gear rack
    "o1.7",  # Upper linear bearing
    "o1.12",  # Lower linear bearing
)


def build_right_claw():
    return extract_occurrences(RIGHT_CLAW_SELECTORS, label="gripper_right_claw")


def gen_step() -> dict[str, object]:
    return {
        "shape": build_right_claw(),
    }


if __name__ == "__main__":
    gen_step()
