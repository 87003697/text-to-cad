#!/usr/bin/env python3
from __future__ import annotations

from gripper_component_extract import extract_occurrences


LEFT_CLAW_SELECTORS = (
    "o1.13",  # RB9.01.061.020 Clamp
    "o1.29",  # Gear rack
    "o1.14",  # Upper linear bearing
    "o1.15",  # Lower linear bearing
)


def build_left_claw():
    return extract_occurrences(LEFT_CLAW_SELECTORS, label="gripper_left_claw")


def gen_step() -> dict[str, object]:
    return {
        "shape": build_left_claw(),
    }


if __name__ == "__main__":
    gen_step()
