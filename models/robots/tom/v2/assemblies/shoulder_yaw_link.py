from __future__ import annotations

from STEP.assemblies.robot_arm_link_common import rebased_robot_arm_instances


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
            instance["use_source_colors"] = True
    return {
        "instances": instances,
    }
