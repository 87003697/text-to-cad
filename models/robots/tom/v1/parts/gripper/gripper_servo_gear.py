#!/usr/bin/env python3
from __future__ import annotations

from gripper_component_extract import extract_occurrences


SERVO_GEAR_SELECTORS = ("o1.4",)


def build_servo_gear():
    return extract_occurrences(SERVO_GEAR_SELECTORS, label="gripper_servo_gear")


def gen_step() -> dict[str, object]:
    return {
        "shape": build_servo_gear(),
    }


if __name__ == "__main__":
    gen_step()
