#!/usr/bin/env python3
from __future__ import annotations

from gripper_component_extract import extract_all_except
from gripper_left_claw import LEFT_CLAW_SELECTORS
from gripper_right_claw import RIGHT_CLAW_SELECTORS
from gripper_servo_gear import SERVO_GEAR_SELECTORS


STATIC_EXCLUDED_SELECTORS = RIGHT_CLAW_SELECTORS + LEFT_CLAW_SELECTORS + SERVO_GEAR_SELECTORS


def build_static_gripper():
    return extract_all_except(STATIC_EXCLUDED_SELECTORS, label="gripper_static")


def gen_step() -> dict[str, object]:
    return {
        "shape": build_static_gripper(),
    }


if __name__ == "__main__":
    gen_step()
