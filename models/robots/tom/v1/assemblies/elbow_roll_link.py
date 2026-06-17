from __future__ import annotations

import sys
from pathlib import Path

TOM_DIR = Path(__file__).resolve().parents[2]
if str(TOM_DIR) not in sys.path:
    sys.path.insert(0, str(TOM_DIR))

from v1.assemblies.robot_arm_link_common import (
    rebased_robot_arm_instances,
    translated_instances,
)


SOURCE_REF = "@cad[STEP/robot_arm#o1.11,o1.12,o1.13,o1.14,o1.15]"
ANCHOR_INSTANCE_NAME = "quinary_servo_2020_connector"
EXTRACTED_INSTANCE_NAMES = (
    "quinary_servo_2020_connector",
    "quinary_servo_hfs5_2020",
    "quinary_servo_end_bracket",
    "senary_servo_end_mount",
    "sts3215_6",
)
SERVO_END_MOUNT_FLUSH_Z_ADJUST_MM = 2.7308
SERVO_END_MOUNT_INSTANCE_NAMES = frozenset(
    {
        "senary_servo_end_mount",
        "sts3215_6",
    }
)


def gen_step() -> dict[str, object]:
    instances = rebased_robot_arm_instances(
        source_ref=SOURCE_REF,
        anchor_instance_name=ANCHOR_INSTANCE_NAME,
        extracted_instance_names=EXTRACTED_INSTANCE_NAMES,
    )
    return {
        "instances": translated_instances(
            instances,
            names=SERVO_END_MOUNT_INSTANCE_NAMES,
            vector_xyz=(0.0, 0.0, 1.0),
            distance_mm=SERVO_END_MOUNT_FLUSH_Z_ADJUST_MM,
        ),
    }
