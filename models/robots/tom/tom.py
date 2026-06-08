from __future__ import annotations

from robot_common.robot_arm import (
    robot_arm_assembly_children,
    robot_arm_motion_envelope,
    robot_arm_srdf_envelope,
    robot_arm_urdf_envelope,
)


def gen_step() -> dict[str, object]:
    children = [
        {
            **child,
            "path": f"STEP/{child['path']}",
        }
        for child in robot_arm_assembly_children()
    ]
    return {
        "children": children,
    }


def gen_urdf() -> dict[str, object]:
    payload = robot_arm_urdf_envelope(urdf_output="tom.urdf")
    return {"xml": payload["xml"]}


def gen_srdf() -> dict[str, object]:
    return robot_arm_srdf_envelope(urdf="tom.urdf")


def gen_motion() -> dict[str, object]:
    return robot_arm_motion_envelope(urdf="tom.urdf")
