from __future__ import annotations

import sys
from pathlib import PurePosixPath, Path

TOM_DIR = Path(__file__).resolve().parent.parent
if str(TOM_DIR) not in sys.path:
    sys.path.insert(0, str(TOM_DIR))

from robot_common.robot_arm import (
    robot_arm_assembly_children,
    robot_arm_motion_envelope,
    robot_arm_srdf_envelope,
    robot_arm_urdf_envelope,
)


def _v1_step_path(path: str) -> str:
    step_path = PurePosixPath(path)
    if step_path.parts and step_path.parts[0] == "assemblies":
        return step_path.as_posix()
    if step_path.parts and step_path.parts[0] in {"imports", "gripper"}:
        return (PurePosixPath("parts") / step_path).as_posix()
    return (PurePosixPath("parts") / step_path.name).as_posix()


def gen_step() -> dict[str, object]:
    children = [
        {
            **child,
            "path": _v1_step_path(str(child["path"])),
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
