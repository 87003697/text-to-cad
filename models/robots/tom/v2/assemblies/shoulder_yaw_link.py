from __future__ import annotations

import sys
from pathlib import Path

TOM_DIR = Path(__file__).resolve().parents[2]
if str(TOM_DIR) not in sys.path:
    sys.path.insert(0, str(TOM_DIR))

from v1.assemblies.robot_arm_link_common import rebased_robot_arm_instances


SOURCE_REF = "@cad[STEP/robot_arm#o1.2]"
ANCHOR_INSTANCE_NAME = "sts3250_1"
EXTRACTED_INSTANCE_NAMES = (
    "sts3250_1",
    "servo_end_mount",
)


def gen_step() -> dict[str, object]:
    instances = rebased_robot_arm_instances(
        source_ref=SOURCE_REF,
        anchor_instance_name=ANCHOR_INSTANCE_NAME,
        extracted_instance_names=EXTRACTED_INSTANCE_NAMES,
    )
    for instance in instances:
        if instance.get("name") == "servo_end_mount":
            instance["path"] = "../parts/servo_end_mount.step"
            instance["use_source_colors"] = True
    return {
        "instances": instances,
    }
