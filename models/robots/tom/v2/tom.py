from __future__ import annotations

from functools import lru_cache
import importlib.util
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

V2_DIR = Path(__file__).resolve().parent
TOM_DIR = V2_DIR.parent
ASSEMBLIES_DIR = V2_DIR / "assemblies"
PARTS_DIR = V2_DIR / "parts"
for path in (TOM_DIR, V2_DIR, PARTS_DIR, ASSEMBLIES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from robot_common import robot_arm
from robot_common.materials import BLACK_ALUMINUM_RGBA, GRAY_ALUMINUM_RGBA
import link_bracket
import servo_end_mount
import servo_horn_yoke
from v2_roll_link_common import (
    invert_rigid_transform,
    multiply_transforms,
    roll_link_mates,
    roll_link_instances,
)


LINK_ASSEMBLY_MODULES = {
    "base_link": ASSEMBLIES_DIR / "base_link.py",
    "shoulder_yaw_link": ASSEMBLIES_DIR / "shoulder_yaw_link.py",
    "shoulder_pitch_link": ASSEMBLIES_DIR / "pitch_link_sts3250.py",
    "elbow_pitch_link": ASSEMBLIES_DIR / "pitch_link_sts3215.py",
    "wrist_pitch_link": ASSEMBLIES_DIR / "pitch_link_sts3215.py",
}

ROLL_LINK_REPLACEMENTS = {
    "shoulder_roll_link": "shoulder",
    "elbow_roll_link": "elbow",
}

GRIPPER_CHILD_NAME = "parallel_gripper"
V2_GRIPPER_SERVO_INSTANCE_NAME = "wrist_pitch_link__sts3215"

NEXT_PITCH_LINK_BY_ROLL_LINK = {
    "shoulder": "elbow_pitch_link",
    "elbow": "wrist_pitch_link",
}

YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM = -9.1
# Source STEP face datums for the terminal gripper mate:
# - sts3215 #o1.16.f14 is the rear horn face at local y=-27.4.
# - gripper #o1.5.f82 is the mounting face at local z=-52.
STS3215_HORN_AXIS_LOCAL_X_MM = -25.5
STS3250_OUTPUT_HORN_FACE_LOCAL_Y_MM = 9.2
STS3215_REAR_HORN_FACE_LOCAL_Y_MM = -27.4
GRIPPER_MOUNT_FACE_CENTER_LOCAL_MM = (0.032382, 0.002077, -52.0)
BASE_TO_SHOULDER_YAW_HORN_CLEARANCE_MM = 0.0
SERVO_END_MOUNT_FRONT_HORN_FACE_CENTER_LOCAL_MM = (
    servo_end_mount.front_horn_mount_face_center_local_mm()
)
YOKE_180_ABOUT_WEB_AXIS_TRANSFORM = tuple(
    servo_horn_yoke.YOKE_180_ABOUT_WEB_AXIS_DESIGN_TRANSFORM
)
PITCH_MODULE_STANDALONE_TO_DESIGN_TRANSFORM = tuple(
    servo_horn_yoke.STANDALONE_TO_DESIGN_TRANSFORM
)

URDF_MATERIALS = {
    "aluminum_5052": GRAY_ALUMINUM_RGBA,
    "dark_servo": BLACK_ALUMINUM_RGBA,
    "silver_aluminum_alloy": (0.78, 0.80, 0.82, 1.0),
}

URDF_MESH_BY_STEP_BASENAME = {
    "base_plate.step": "3MF/base_plate.3mf",
    "servo_end_mount.step": "3MF/servo_end_mount.3mf",
    "servo_horn_yoke.step": "3MF/servo_horn_yoke.3mf",
    "link_bracket_right.step": "3MF/link_bracket_right.3mf",
    "link_bracket_left.step": "3MF/link_bracket_left.3mf",
    "link_standoff_m3_35.step": "3MF/link_standoff_m3_35.3mf",
    "sts3250.step": "3MF/sts3250.3mf",
    "sts3215.step": "3MF/sts3215.3mf",
}

URDF_MATERIAL_BY_STEP_BASENAME = {
    "base_plate.step": "aluminum_5052",
    "servo_end_mount.step": "aluminum_5052",
    "servo_horn_yoke.step": "aluminum_5052",
    "link_bracket_right.step": "aluminum_5052",
    "link_bracket_left.step": "aluminum_5052",
    "link_standoff_m3_35.step": "silver_aluminum_alloy",
    "sts3250.step": "dark_servo",
    "sts3215.step": "dark_servo",
}

KGF_CM_TO_NM = robot_arm.KGF_CM_TO_NM
STS3250_MASS_KG = robot_arm.STS3250_MASS_KG
STS3215_MASS_KG = robot_arm.STS3215_MASS_KG
STS3250_STALL_TORQUE_NM = 50.0 * KGF_CM_TO_NM
STS3215_STALL_TORQUE_NM = 30.0 * KGF_CM_TO_NM
STS3250_NO_LOAD_SPEED_RAD_S = math.radians(60.0) / 0.133
STS3215_NO_LOAD_SPEED_RAD_S = math.radians(60.0) / 0.222
ALUMINUM_5052_DENSITY_KG_PER_MM3 = robot_arm.ALUMINUM_5052_H32_DENSITY_KG_PER_MM3
SERVO_MASS_KG_BY_STEP_BASENAME = {
    "sts3250.step": STS3250_MASS_KG,
    "sts3215.step": STS3215_MASS_KG,
}
MATERIAL_DENSITY_KG_PER_MM3 = {
    "aluminum_5052": ALUMINUM_5052_DENSITY_KG_PER_MM3,
    "silver_aluminum_alloy": ALUMINUM_5052_DENSITY_KG_PER_MM3,
}
URDF_JOINT_EFFORT_NM_BY_NAME = {
    "base_yaw": STS3250_STALL_TORQUE_NM,
    "shoulder_pitch": STS3250_STALL_TORQUE_NM,
    "shoulder_roll": STS3250_STALL_TORQUE_NM,
    "elbow_pitch": STS3250_STALL_TORQUE_NM,
    "elbow_roll": STS3215_STALL_TORQUE_NM,
    "wrist_pitch": STS3215_STALL_TORQUE_NM,
    "wrist_roll": STS3215_STALL_TORQUE_NM,
}
URDF_JOINT_VELOCITY_RAD_S_BY_NAME = {
    "base_yaw": STS3250_NO_LOAD_SPEED_RAD_S,
    "shoulder_pitch": STS3250_NO_LOAD_SPEED_RAD_S,
    "shoulder_roll": STS3250_NO_LOAD_SPEED_RAD_S,
    "elbow_pitch": STS3250_NO_LOAD_SPEED_RAD_S,
    "elbow_roll": STS3215_NO_LOAD_SPEED_RAD_S,
    "wrist_pitch": STS3215_NO_LOAD_SPEED_RAD_S,
    "wrist_roll": STS3215_NO_LOAD_SPEED_RAD_S,
}
V2_HOME_ELBOW_PITCH_DEG = -90.0
V2_SRDF_REACH_ARM_GROUP_STATE_DEG = {
    "base_yaw": 0.0,
    "shoulder_pitch": 90.0,
    "shoulder_roll": 0.0,
    "elbow_pitch": 0.0,
    "elbow_roll": 0.0,
    "wrist_pitch": 0.0,
    "wrist_roll": 0.0,
}
V2_SRDF_INSPECTION_ARM_GROUP_STATE_DEG = {
    "base_yaw": 45.0,
    "shoulder_pitch": -27.0,
    "shoulder_roll": 66.0,
    "elbow_pitch": -87.0,
    "elbow_roll": 121.995,
    "wrist_pitch": 83.0,
    "wrist_roll": -99.0,
}
V2_SRDF_INSPECTION_MIRRORED_ARM_GROUP_STATE_DEG = {
    **V2_SRDF_INSPECTION_ARM_GROUP_STATE_DEG,
    "shoulder_roll": -V2_SRDF_INSPECTION_ARM_GROUP_STATE_DEG["shoulder_roll"],
    "elbow_roll": -V2_SRDF_INSPECTION_ARM_GROUP_STATE_DEG["elbow_roll"],
    "wrist_roll": -V2_SRDF_INSPECTION_ARM_GROUP_STATE_DEG["wrist_roll"],
}

NO_GRIPPER_LINK_NAMES = (
    "base_footprint",
    "base_link",
    "shoulder_yaw_link",
    "shoulder_pitch_link",
    "shoulder_roll_link",
    "elbow_pitch_link",
    "elbow_roll_link",
    "wrist_pitch_link",
    "wrist_roll_link",
)
GRIPPER_LINK_NAMES = (
    "gripper_base_link",
    "gripper_servo_link",
    "gripper_right_claw_link",
    "gripper_left_claw_link",
)
WITH_GRIPPER_LINK_NAMES = (*NO_GRIPPER_LINK_NAMES, *GRIPPER_LINK_NAMES)

URDF_SERVO_AXIS_INSTANCE_BY_JOINT = {
    "base_yaw": "base_link__sts3250_3",
    "shoulder_pitch": "shoulder_yaw_link__sts3250_1",
    "shoulder_roll": "shoulder_pitch_link__sts3250",
    "elbow_pitch": "shoulder_roll_link__sts3250_4",
    "elbow_roll": "elbow_pitch_link__sts3215",
    "wrist_pitch": "elbow_roll_link__sts3215_6",
    "wrist_roll": "wrist_pitch_link__sts3215",
}

URDF_VISUAL_MODULE_BY_LINK = {
    "base_link": LINK_ASSEMBLY_MODULES["base_link"],
    "shoulder_yaw_link": LINK_ASSEMBLY_MODULES["shoulder_yaw_link"],
    "shoulder_pitch_link": LINK_ASSEMBLY_MODULES["shoulder_pitch_link"],
    "elbow_pitch_link": LINK_ASSEMBLY_MODULES["elbow_pitch_link"],
    "wrist_pitch_link": LINK_ASSEMBLY_MODULES["wrist_pitch_link"],
}

URDF_PITCH_MODULE_SERVO_BY_LINK = {
    "shoulder_pitch_link": "sts3250",
    "elbow_pitch_link": "sts3215",
    "wrist_pitch_link": "sts3215",
}

URDF_ROLL_BOTTOM_SERVO_SOURCE_BY_LINK = {
    "shoulder_roll_link": ("shoulder_pitch_link", "sts3250"),
    "elbow_roll_link": ("elbow_pitch_link", "sts3215"),
    "wrist_roll_link": ("wrist_pitch_link", "sts3215"),
}


def _mate(
    source_label: str,
    *,
    fixed: str,
    moving: str,
    relation: str = "rigid",
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    fixed_part, fixed_frame = fixed.split(":", 1)
    moving_part, moving_frame = moving.split(":", 1)
    return {
        "sourceLabel": source_label,
        "type": relation,
        "relation": relation,
        "fixed": fixed,
        "moving": moving,
        "parameters": dict(parameters or {}),
        "fixedEndpoint": {
            "part": fixed_part,
            "frame": fixed_frame,
        },
        "movingEndpoint": {
            "part": moving_part,
            "frame": moving_frame,
        },
    }


def _roll_link_standoff_mates(parent_name: str, kind: str) -> list[dict[str, object]]:
    mates: list[dict[str, object]] = []
    for label, _x, _z in link_bracket.STANDOFF_CENTER_XZ_MM:
        standoff = f"{parent_name}__{kind}_link_standoff_{label}"
        mates.append(
            _mate(
                f"{parent_name}_{label}_standoff_to_right_bracket",
                fixed=f"{parent_name}__{kind}_link_bracket_right:{label}_standoff_hole",
                moving=f"{standoff}:positive_y_thread",
            )
        )
        mates.append(
            _mate(
                f"{parent_name}_{label}_standoff_to_left_bracket",
                fixed=f"{parent_name}__{kind}_link_bracket_left:{label}_standoff_hole",
                moving=f"{standoff}:negative_y_thread",
            )
        )
    return mates


def _assembly_mates(*, include_gripper: bool) -> list[dict[str, object]]:
    mates = [
        _mate(
            "base_servo_to_base_plate",
            fixed="base_link__base_plate:top_plate",
            moving="base_link__sts3250_3:case_mount",
        ),
        _mate(
            "base_servo_horn_to_shoulder_yaw_mount",
            fixed="base_link__sts3250_3:output_horn_face",
            moving="shoulder_yaw_link__servo_end_mount:front_horn_mount_face",
            parameters={"clearance_mm": BASE_TO_SHOULDER_YAW_HORN_CLEARANCE_MM},
        ),
        _mate(
            "shoulder_yaw_mount_to_servo",
            fixed="shoulder_yaw_link__sts3250_1:rear_case",
            moving="shoulder_yaw_link__servo_end_mount:servo_face",
        ),
        _mate(
            "shoulder_pitch_yoke_to_servo",
            fixed="shoulder_pitch_link__servo_horn_yoke:horn_axis",
            moving="shoulder_pitch_link__sts3250:horn_axis",
        ),
        _mate(
            "shoulder_pitch_servo_to_roll_bracket_right",
            fixed="shoulder_pitch_link__sts3250:upstream_case",
            moving="shoulder_roll_link__shoulder_link_bracket_right:bottom_servo_mount",
        ),
        _mate(
            "shoulder_pitch_servo_to_roll_bracket_left",
            fixed="shoulder_pitch_link__sts3250:upstream_case",
            moving="shoulder_roll_link__shoulder_link_bracket_left:bottom_servo_mount",
        ),
        _mate(
            "shoulder_roll_bracket_right_to_servo",
            fixed="shoulder_roll_link__shoulder_link_bracket_right:top_servo_mount",
            moving="shoulder_roll_link__sts3250_4:case_mount",
        ),
        _mate(
            "shoulder_roll_bracket_left_to_servo",
            fixed="shoulder_roll_link__shoulder_link_bracket_left:top_servo_mount",
            moving="shoulder_roll_link__sts3250_4:case_mount",
        ),
        _mate(
            "elbow_pitch_yoke_to_shoulder_roll_servo",
            fixed="shoulder_roll_link__sts3250_4:horn_axis",
            moving="elbow_pitch_link__servo_horn_yoke:horn_axis",
        ),
        _mate(
            "elbow_pitch_servo_to_roll_bracket_right",
            fixed="elbow_pitch_link__sts3215:upstream_case",
            moving="elbow_roll_link__elbow_link_bracket_right:bottom_servo_mount",
        ),
        _mate(
            "elbow_pitch_servo_to_roll_bracket_left",
            fixed="elbow_pitch_link__sts3215:upstream_case",
            moving="elbow_roll_link__elbow_link_bracket_left:bottom_servo_mount",
        ),
        _mate(
            "elbow_roll_bracket_right_to_servo",
            fixed="elbow_roll_link__elbow_link_bracket_right:top_servo_mount",
            moving="elbow_roll_link__sts3215_6:case_mount",
        ),
        _mate(
            "elbow_roll_bracket_left_to_servo",
            fixed="elbow_roll_link__elbow_link_bracket_left:top_servo_mount",
            moving="elbow_roll_link__sts3215_6:case_mount",
        ),
        _mate(
            "wrist_pitch_yoke_to_elbow_roll_servo",
            fixed="elbow_roll_link__sts3215_6:horn_axis",
            moving="wrist_pitch_link__servo_horn_yoke:horn_axis",
        ),
    ]
    if include_gripper:
        mates.append(
            _mate(
                "gripper_mount_face_to_wrist_servo_horn",
                fixed="wrist_pitch_link__sts3215:rear_horn_face",
                moving="parallel_gripper:mount_face",
            )
        )
    mates.extend(_roll_link_standoff_mates("shoulder_roll_link", "shoulder"))
    mates.extend(_roll_link_standoff_mates("elbow_roll_link", "elbow"))
    return mates


def _module_gen_step(module_path: Path) -> dict[str, object]:
    module_name = f"_tom_v2_{module_path.stem}_{abs(hash(module_path))}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    gen_step = getattr(module, "gen_step", None)
    if not callable(gen_step):
        raise RuntimeError(f"{module_path} does not define gen_step()")
    envelope = gen_step()
    if not isinstance(envelope, dict) or "instances" not in envelope:
        raise RuntimeError(f"{module_path} must return an instances assembly")
    instances = envelope["instances"]
    if not isinstance(instances, list) or not instances:
        raise RuntimeError(f"{module_path} returned no instances")
    return envelope


def _instances_from_module(module_path: Path) -> list[dict[str, object]]:
    return [
        dict(instance)
        for instance in _module_gen_step(module_path)["instances"]  # type: ignore[index]
    ]


def _rebase_step_path(path: str, *, source_dir: Path) -> str:
    resolved = (source_dir / path).resolve()
    try:
        return resolved.relative_to(V2_DIR.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"{path} resolves outside the v2 assembly folder") from exc


def _v2_source_child_path(path: str) -> str:
    source_path = Path(path)
    if source_path.parts and source_path.parts[0] in {"imports", "gripper"}:
        return (Path("parts") / source_path).as_posix()
    return source_path.as_posix()


def _flatten_instances(
    *,
    parent_name: str,
    parent_transform: list[float],
    local_instances: list[dict[str, object]],
    local_source_dir: Path,
    instance_transform_overrides: dict[str, list[float]] | None = None,
) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    transform_overrides = instance_transform_overrides or {}
    for local in local_instances:
        local_name = str(local["name"])
        local_transform = [float(value) for value in local["transform"]]
        world_transform = transform_overrides.get(local_name)
        if world_transform is None:
            world_transform = multiply_transforms(parent_transform, local_transform)
        flattened.append(
            {
                "path": _rebase_step_path(str(local["path"]), source_dir=local_source_dir),
                "name": f"{parent_name}__{local_name}",
                "transform": world_transform,
                "use_source_colors": bool(local.get("use_source_colors", True)),
            }
        )
    return flattened


def _flat_child_instances(
    *,
    child_name: str,
    child_transform: list[float],
    instance_transform_overrides: dict[str, list[float]] | None = None,
) -> list[dict[str, object]]:
    replacement_kind = ROLL_LINK_REPLACEMENTS.get(child_name)
    if replacement_kind is not None:
        return _flatten_instances(
            parent_name=child_name,
            parent_transform=child_transform,
            local_instances=roll_link_instances(replacement_kind),
            local_source_dir=ASSEMBLIES_DIR,
            instance_transform_overrides=instance_transform_overrides,
        )

    module_path = LINK_ASSEMBLY_MODULES[child_name]
    return _flatten_instances(
        parent_name=child_name,
        parent_transform=child_transform,
        local_instances=_instances_from_module(module_path),
        local_source_dir=module_path.parent,
        instance_transform_overrides=instance_transform_overrides,
    )


def _module_transform_for_child(
    *,
    child_name: str,
    design_child_transform: list[float],
) -> list[float]:
    if child_name in URDF_PITCH_MODULE_SERVO_BY_LINK:
        return list(
            multiply_transforms(
                design_child_transform,
                PITCH_MODULE_STANDALONE_TO_DESIGN_TRANSFORM,
            )
        )
    return design_child_transform


def _yoke_transform_for_servo_horn(
    *,
    upstream_servo_transform: list[float],
) -> list[float]:
    return multiply_transforms(
        upstream_servo_transform,
        YOKE_180_ABOUT_WEB_AXIS_TRANSFORM,
    )


def _identity_transform() -> list[float]:
    return [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    ]


def _transform_point(
    transform: list[float],
    point: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = point
    return (
        transform[0] * x + transform[1] * y + transform[2] * z + transform[3],
        transform[4] * x + transform[5] * y + transform[6] * z + transform[7],
        transform[8] * x + transform[9] * y + transform[10] * z + transform[11],
    )


def _transform_direction(
    transform: list[float],
    direction: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = direction
    return (
        transform[0] * x + transform[1] * y + transform[2] * z,
        transform[4] * x + transform[5] * y + transform[6] * z,
        transform[8] * x + transform[9] * y + transform[10] * z,
    )


def _translate_transform(
    transform: list[float],
    delta: tuple[float, float, float],
) -> list[float]:
    adjusted = list(transform)
    adjusted[3] += delta[0]
    adjusted[7] += delta[1]
    adjusted[11] += delta[2]
    return adjusted


def _mate_shoulder_yaw_mount_to_base_servo_horn(
    *,
    shoulder_yaw_transform: list[float],
    base_servo_transform: list[float],
) -> list[float]:
    base_horn_face_center = _transform_point(
        base_servo_transform,
        (
            STS3215_HORN_AXIS_LOCAL_X_MM,
            STS3250_OUTPUT_HORN_FACE_LOCAL_Y_MM,
            0.0,
        ),
    )
    base_horn_face_normal = _transform_direction(base_servo_transform, (0.0, 1.0, 0.0))
    target = tuple(
        base_horn_face_center[index]
        + (BASE_TO_SHOULDER_YAW_HORN_CLEARANCE_MM * base_horn_face_normal[index])
        for index in range(3)
    )
    moving = _transform_point(
        shoulder_yaw_transform,
        SERVO_END_MOUNT_FRONT_HORN_FACE_CENTER_LOCAL_MM,
    )
    return _translate_transform(
        shoulder_yaw_transform,
        (
            target[0] - moving[0],
            target[1] - moving[1],
            target[2] - moving[2],
        ),
    )


def _mate_gripper_to_terminal_servo_horn(
    *,
    gripper_transform: list[float],
    terminal_servo_transform: list[float],
) -> list[float]:
    target = _transform_point(
        terminal_servo_transform,
        (
            STS3215_HORN_AXIS_LOCAL_X_MM,
            STS3215_REAR_HORN_FACE_LOCAL_Y_MM,
            0.0,
        ),
    )
    moving = _transform_point(gripper_transform, GRIPPER_MOUNT_FACE_CENTER_LOCAL_MM)
    return _translate_transform(
        gripper_transform,
        (
            target[0] - moving[0],
            target[1] - moving[1],
            target[2] - moving[2],
        ),
    )


def gen_step_with_options(*, include_gripper: bool = False) -> dict[str, object]:
    instances: list[dict[str, object]] = []
    instance_transforms_by_name: dict[str, list[float]] = {}
    downstream_correction = _identity_transform()
    pending_child_transform_overrides: dict[str, list[float]] = {}

    for source_child in robot_arm.robot_arm_assembly_children():
        child_name = str(source_child["name"])
        source_child_transform = [float(value) for value in source_child["transform"]]
        child_transform = pending_child_transform_overrides.pop(child_name, None)
        if child_transform is not None:
            downstream_correction = multiply_transforms(
                child_transform,
                invert_rigid_transform(source_child_transform),
            )
        else:
            child_transform = multiply_transforms(
                downstream_correction,
                source_child_transform,
            )

        if child_name == "shoulder_yaw_link":
            base_servo_transform = instance_transforms_by_name.get("base_link__sts3250_3")
            if base_servo_transform is not None:
                child_transform = _mate_shoulder_yaw_mount_to_base_servo_horn(
                    shoulder_yaw_transform=child_transform,
                    base_servo_transform=base_servo_transform,
                )
                downstream_correction = multiply_transforms(
                    child_transform,
                    invert_rigid_transform(source_child_transform),
            )

        if child_name == GRIPPER_CHILD_NAME:
            if not include_gripper:
                continue
            terminal_servo_transform = instance_transforms_by_name.get(
                V2_GRIPPER_SERVO_INSTANCE_NAME
            )
            if terminal_servo_transform is not None:
                child_transform = _mate_gripper_to_terminal_servo_horn(
                    gripper_transform=child_transform,
                    terminal_servo_transform=terminal_servo_transform,
                )
            instances.append(
                {
                    "path": _v2_source_child_path(str(source_child["path"])),
                    "name": child_name,
                    "transform": child_transform,
                    "use_source_colors": bool(source_child.get("use_source_colors", True)),
                }
            )
            instance_transforms_by_name[child_name] = child_transform
            continue

        module_child_transform = _module_transform_for_child(
            child_name=child_name,
            design_child_transform=child_transform,
        )
        child_instances = _flat_child_instances(
            child_name=child_name,
            child_transform=module_child_transform,
        )
        instances.extend(child_instances)
        for instance in child_instances:
            instance_transforms_by_name[str(instance["name"])] = [
                float(value) for value in instance["transform"]
            ]

        replacement_kind = ROLL_LINK_REPLACEMENTS.get(child_name)
        if replacement_kind is not None:
            mates = roll_link_mates(replacement_kind)
            next_pitch_child_name = NEXT_PITCH_LINK_BY_ROLL_LINK.get(replacement_kind)
            if next_pitch_child_name is not None:
                upstream_servo_transform = multiply_transforms(
                    child_transform,
                    mates.downstream_servo_local,
                )
                pending_child_transform_overrides[next_pitch_child_name] = (
                    _yoke_transform_for_servo_horn(
                        upstream_servo_transform=upstream_servo_transform,
                    )
                )

    return {
        "instances": instances,
        "assembly_mates": _assembly_mates(include_gripper=include_gripper),
    }


def gen_step() -> dict[str, object]:
    envelope = gen_step_with_options(include_gripper=False)
    return {
        "instances": envelope["instances"],
        "assembly_mates": envelope.get("assembly_mates", []),
    }


def _xml(
    tag: str,
    attrs: dict[str, str] | None = None,
    children: tuple[ET.Element, ...] = (),
) -> ET.Element:
    element = ET.Element(tag, attrs or {})
    for child in children:
        element.append(child)
    return element


def _serialize_urdf(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode")


def _urdf_number(value: float) -> str:
    numeric = 0.0 if abs(value) < 5e-10 else float(value)
    text = f"{numeric:.9f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def _rgba_text(rgba: tuple[float, float, float, float]) -> str:
    return " ".join(_urdf_number(float(value)) for value in rgba)


def _rpy_from_transform(transform: list[float] | tuple[float, ...]) -> tuple[float, float, float]:
    r00 = float(transform[0])
    r10 = float(transform[4])
    r20 = float(transform[8])
    r21 = float(transform[9])
    r22 = float(transform[10])
    r01 = float(transform[1])
    r11 = float(transform[5])

    pitch = math.asin(max(-1.0, min(1.0, -r20)))
    cp = math.cos(pitch)
    if abs(cp) > 1e-9:
        roll = math.atan2(r21, r22)
        yaw = math.atan2(r10, r00)
    else:
        roll = 0.0
        yaw = math.atan2(-r01, r11)
    return roll, pitch, yaw


def _urdf_origin_attrs_from_transform(
    transform: list[float] | tuple[float, ...],
) -> dict[str, str]:
    xyz = (
        float(transform[3]) * 0.001,
        float(transform[7]) * 0.001,
        float(transform[11]) * 0.001,
    )
    rpy = _rpy_from_transform(transform)
    return {
        "xyz": " ".join(_urdf_number(value) for value in xyz),
        "rpy": " ".join(_urdf_number(value) for value in rpy),
    }


def _transform_from_urdf_origin_attrs(xyz: str, rpy: str) -> list[float]:
    x, y, z = (float(value) * 1000.0 for value in xyz.split())
    roll, pitch, yaw = (float(value) for value in rpy.split())
    sr = math.sin(roll)
    cr = math.cos(roll)
    sp = math.sin(pitch)
    cp = math.cos(pitch)
    sy = math.sin(yaw)
    cy = math.cos(yaw)
    return [
        cy * cp,
        cy * sp * sr - sy * cr,
        cy * sp * cr + sy * sr,
        x,
        sy * cp,
        sy * sp * sr + cy * cr,
        sy * sp * cr - cy * sr,
        y,
        -sp,
        cp * sr,
        cp * cr,
        z,
        0.0,
        0.0,
        0.0,
        1.0,
    ]


def _world_vector_to_local(
    frame_transform: list[float] | tuple[float, ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = vector
    return (
        frame_transform[0] * x + frame_transform[4] * y + frame_transform[8] * z,
        frame_transform[1] * x + frame_transform[5] * y + frame_transform[9] * z,
        frame_transform[2] * x + frame_transform[6] * y + frame_transform[10] * z,
    )


def _normalized_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(component * component for component in vector))
    if length <= 1e-9:
        raise RuntimeError("Cannot normalize a zero-length vector")
    return tuple(component / length for component in vector)  # type: ignore[return-value]


def _axis_attrs(vector: tuple[float, float, float]) -> str:
    axis = _normalized_vector(vector)
    return " ".join(_urdf_number(value) for value in axis)


def _with_translation(
    transform: list[float] | tuple[float, ...],
    point: tuple[float, float, float],
) -> list[float]:
    adjusted = [float(value) for value in transform]
    adjusted[3], adjusted[7], adjusted[11] = point
    return adjusted


def _servo_horn_axis_from_transform(
    transform: list[float] | tuple[float, ...],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    rear = _transform_point(
        list(transform),
        (
            STS3215_HORN_AXIS_LOCAL_X_MM,
            STS3215_REAR_HORN_FACE_LOCAL_Y_MM,
            0.0,
        ),
    )
    front = _transform_point(
        list(transform),
        (
            STS3215_HORN_AXIS_LOCAL_X_MM,
            STS3250_OUTPUT_HORN_FACE_LOCAL_Y_MM,
            0.0,
        ),
    )
    center = tuple(0.5 * (rear[index] + front[index]) for index in range(3))
    direction = _normalized_vector(
        tuple(front[index] - rear[index] for index in range(3))
    )
    return center, direction


def _zero_pose_source_frames_and_instances(
    *,
    include_gripper: bool = False,
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    frames: dict[str, list[float]] = {"base_footprint": _identity_transform()}
    instance_transforms_by_name: dict[str, list[float]] = {}
    downstream_correction = _identity_transform()
    pending_child_transform_overrides: dict[str, list[float]] = {}

    for source_child in robot_arm.robot_arm_assembly_children():
        child_name = str(source_child["name"])
        source_child_transform = [float(value) for value in source_child["transform"]]
        child_transform = pending_child_transform_overrides.pop(child_name, None)
        if child_transform is not None:
            downstream_correction = multiply_transforms(
                child_transform,
                invert_rigid_transform(source_child_transform),
            )
        else:
            child_transform = multiply_transforms(
                downstream_correction,
                source_child_transform,
            )

        if child_name == "shoulder_yaw_link":
            base_servo_transform = instance_transforms_by_name.get("base_link__sts3250_3")
            if base_servo_transform is not None:
                child_transform = _mate_shoulder_yaw_mount_to_base_servo_horn(
                    shoulder_yaw_transform=child_transform,
                    base_servo_transform=base_servo_transform,
                )
                downstream_correction = multiply_transforms(
                    child_transform,
                    invert_rigid_transform(source_child_transform),
                )

        if child_name == GRIPPER_CHILD_NAME:
            if include_gripper:
                frames[child_name] = child_transform
            continue

        module_child_transform = _module_transform_for_child(
            child_name=child_name,
            design_child_transform=child_transform,
        )
        frames[child_name] = module_child_transform
        child_instances = _flat_child_instances(
            child_name=child_name,
            child_transform=module_child_transform,
        )
        for instance in child_instances:
            instance_transforms_by_name[str(instance["name"])] = [
                float(value) for value in instance["transform"]
            ]

        replacement_kind = ROLL_LINK_REPLACEMENTS.get(child_name)
        if replacement_kind is not None:
            mates = roll_link_mates(replacement_kind)
            next_pitch_child_name = NEXT_PITCH_LINK_BY_ROLL_LINK.get(replacement_kind)
            if next_pitch_child_name is not None:
                upstream_servo_transform = multiply_transforms(
                    child_transform,
                    mates.downstream_servo_local,
                )
                pending_child_transform_overrides[next_pitch_child_name] = (
                    _yoke_transform_for_servo_horn(
                        upstream_servo_transform=upstream_servo_transform,
                    )
                )

    wrist_pitch = frames.get("wrist_pitch_link")
    if wrist_pitch is not None and "wrist_roll_link" not in frames:
        wrist_roll_joint = next(
            joint
            for joint in robot_arm.URDF_JOINTS
            if str(joint["name"]) == "wrist_roll"
        )
        frames["wrist_roll_link"] = multiply_transforms(
            wrist_pitch,
            _transform_from_urdf_origin_attrs(
                str(wrist_roll_joint["origin_xyz"]),
                str(wrist_roll_joint["origin_rpy"]),
            ),
        )
    return frames, instance_transforms_by_name


def _urdf_link_frames_and_axes(
    *,
    include_gripper: bool = False,
) -> tuple[
    dict[str, list[float]],
    dict[str, list[float]],
    dict[str, str],
]:
    source_frames, instance_transforms = _zero_pose_source_frames_and_instances(
        include_gripper=include_gripper,
    )
    if include_gripper and GRIPPER_CHILD_NAME in source_frames:
        source_frames["gripper_base_link"] = list(source_frames[GRIPPER_CHILD_NAME])
    urdf_frames = {name: list(transform) for name, transform in source_frames.items()}
    axes_by_joint: dict[str, str] = {}

    for joint in _no_gripper_joints():
        joint_name = str(joint["name"])
        servo_instance_name = URDF_SERVO_AXIS_INSTANCE_BY_JOINT.get(joint_name)
        if not servo_instance_name:
            continue
        servo_transform = instance_transforms.get(servo_instance_name)
        if servo_transform is None:
            raise RuntimeError(
                f"Missing servo instance {servo_instance_name!r} for URDF joint {joint_name!r}"
            )

        axis_center_world, axis_direction_world = _servo_horn_axis_from_transform(
            servo_transform,
        )
        child_link = str(joint["child"])
        if child_link not in urdf_frames:
            parent_frame = urdf_frames.get(str(joint["parent"]))
            if parent_frame is None:
                raise RuntimeError(f"Missing parent frame for URDF joint {joint_name!r}")
            urdf_frames[child_link] = list(parent_frame)
        urdf_frames[child_link] = _with_translation(
            urdf_frames[child_link],
            axis_center_world,
        )
        axis_in_joint_frame = _world_vector_to_local(
            urdf_frames[child_link],
            axis_direction_world,
        )
        axes_by_joint[joint_name] = _axis_attrs(axis_in_joint_frame)

    return source_frames, urdf_frames, axes_by_joint


def _instances_for_urdf_link(link_name: str) -> list[dict[str, object]]:
    instances: list[dict[str, object]] = []
    bottom_servo_source = URDF_ROLL_BOTTOM_SERVO_SOURCE_BY_LINK.get(link_name)
    if bottom_servo_source is not None:
        source_link_name, source_instance_name = bottom_servo_source
        source_module_path = URDF_VISUAL_MODULE_BY_LINK[source_link_name]
        source_instances = _module_gen_step(source_module_path)["instances"]  # type: ignore[index]
        for instance in source_instances:
            if str(instance["name"]) == source_instance_name:
                moved_instance = dict(instance)
                moved_instance["source_link_name"] = source_link_name
                instances.append(moved_instance)
                break
        else:
            raise RuntimeError(
                f"Missing bottom roll servo {source_instance_name!r} "
                f"in source link {source_link_name!r}"
            )

    if link_name == "shoulder_roll_link":
        instances.extend(dict(instance) for instance in roll_link_instances("shoulder"))
        return instances
    if link_name == "elbow_roll_link":
        instances.extend(dict(instance) for instance in roll_link_instances("elbow"))
        return instances
    if link_name == "wrist_roll_link":
        return instances

    module_path = URDF_VISUAL_MODULE_BY_LINK.get(link_name)
    if module_path is None:
        return instances
    skipped_servo_name = URDF_PITCH_MODULE_SERVO_BY_LINK.get(link_name)
    return [
        dict(instance)
        for instance in _module_gen_step(module_path)["instances"]  # type: ignore[index]
        if str(instance["name"]) != skipped_servo_name
    ]


def _mesh_filename_for_step_path(step_path: str) -> str:
    basename = Path(step_path).name
    mesh = URDF_MESH_BY_STEP_BASENAME.get(basename)
    if mesh is None:
        raise RuntimeError(f"No v2 URDF 3MF mesh mapping for {step_path!r}")
    return mesh


def _material_name_for_step_path(step_path: str) -> str:
    basename = Path(step_path).name
    material = URDF_MATERIAL_BY_STEP_BASENAME.get(basename)
    if material is None:
        raise RuntimeError(f"No v2 URDF material mapping for {step_path!r}")
    return material


def _source_dir_for_urdf_instance(
    instance: dict[str, object],
    *,
    link_name: str,
) -> Path:
    source_link_name = str(instance.get("source_link_name", link_name))
    module_path = URDF_VISUAL_MODULE_BY_LINK.get(source_link_name)
    if module_path is not None:
        return module_path.parent
    if source_link_name in ROLL_LINK_REPLACEMENTS:
        return ASSEMBLIES_DIR
    return V2_DIR


def _resolved_step_path(
    step_path: str,
    *,
    source_dir: Path,
) -> Path:
    path = Path(step_path)
    candidates = [
        (source_dir / path).resolve(),
        (V2_DIR / path).resolve(),
        (PARTS_DIR / path.name).resolve(),
        (PARTS_DIR / "imports" / path.name).resolve(),
        (PARTS_DIR / "gripper" / path.name).resolve(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Unable to resolve STEP path {step_path!r}")


def _build123d_vector_xyz(vector: object) -> tuple[float, float, float]:
    return (
        float(getattr(vector, "X")),
        float(getattr(vector, "Y")),
        float(getattr(vector, "Z")),
    )


@lru_cache(maxsize=None)
def _step_geometry_properties(step_file: str) -> dict[str, object]:
    from build123d import import_step

    shape = import_step(step_file)
    bbox = shape.bounding_box()
    return {
        "volume_mm3": float(shape.volume),
        "center_mm": _build123d_vector_xyz(shape.center()),
        "size_mm": _build123d_vector_xyz(bbox.size),
    }


def _part_mass_properties(
    step_path: str,
    *,
    source_dir: Path,
) -> dict[str, object]:
    step_file = _resolved_step_path(step_path, source_dir=source_dir)
    basename = step_file.name
    geometry = _step_geometry_properties(step_file.as_posix())
    mass_kg = SERVO_MASS_KG_BY_STEP_BASENAME.get(basename)
    if mass_kg is None:
        material = _material_name_for_step_path(basename)
        density = MATERIAL_DENSITY_KG_PER_MM3.get(material)
        if density is None:
            raise RuntimeError(f"No density for v2 URDF material {material!r}")
        mass_kg = float(geometry["volume_mm3"]) * density
    return {
        "mass_kg": mass_kg,
        "center_mm": geometry["center_mm"],
        "size_mm": geometry["size_mm"],
    }


def _mat3_mul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [
            sum(left[row][inner] * right[inner][col] for inner in range(3))
            for col in range(3)
        ]
        for row in range(3)
    ]


def _mat3_transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [[matrix[col][row] for col in range(3)] for row in range(3)]


def _rotation_matrix(transform: list[float] | tuple[float, ...]) -> list[list[float]]:
    return [
        [float(transform[0]), float(transform[1]), float(transform[2])],
        [float(transform[4]), float(transform[5]), float(transform[6])],
        [float(transform[8]), float(transform[9]), float(transform[10])],
    ]


def _rotated_box_inertia_kg_m2(
    *,
    mass_kg: float,
    size_mm: tuple[float, float, float],
    transform: list[float],
) -> list[list[float]]:
    sx, sy, sz = (dimension * 0.001 for dimension in size_mm)
    local = [
        [mass_kg * (sy * sy + sz * sz) / 12.0, 0.0, 0.0],
        [0.0, mass_kg * (sx * sx + sz * sz) / 12.0, 0.0],
        [0.0, 0.0, mass_kg * (sx * sx + sy * sy) / 12.0],
    ]
    rotation = _rotation_matrix(transform)
    return _mat3_mul(_mat3_mul(rotation, local), _mat3_transpose(rotation))


def _add_parallel_axis(
    inertia: list[list[float]],
    *,
    mass_kg: float,
    offset_m: tuple[float, float, float],
) -> None:
    x, y, z = offset_m
    distance_squared = x * x + y * y + z * z
    offsets = (x, y, z)
    for row in range(3):
        for col in range(3):
            inertia[row][col] += mass_kg * (
                (distance_squared if row == col else 0.0)
                - offsets[row] * offsets[col]
            )


def _inertial_element_for_instances(
    instances: list[dict[str, object]],
    *,
    link_name: str,
    source_frames: dict[str, list[float]],
    urdf_link_frame: list[float],
) -> ET.Element | None:
    placed_parts: list[dict[str, object]] = []
    total_mass_kg = 0.0
    weighted_center_mm = [0.0, 0.0, 0.0]

    for instance in instances:
        source_link_name = str(instance.get("source_link_name", link_name))
        source_link_frame = source_frames.get(
            source_link_name,
            _identity_transform(),
        )
        source_local_transform = [float(value) for value in instance["transform"]]
        world_transform = multiply_transforms(source_link_frame, source_local_transform)
        link_transform = multiply_transforms(
            invert_rigid_transform(urdf_link_frame),
            world_transform,
        )
        properties = _part_mass_properties(
            str(instance["path"]),
            source_dir=_source_dir_for_urdf_instance(instance, link_name=link_name),
        )
        mass_kg = float(properties["mass_kg"])
        center_link_mm = _transform_point(
            link_transform,
            properties["center_mm"],  # type: ignore[arg-type]
        )
        total_mass_kg += mass_kg
        for axis in range(3):
            weighted_center_mm[axis] += mass_kg * center_link_mm[axis]
        placed_parts.append(
            {
                "mass_kg": mass_kg,
                "center_link_mm": center_link_mm,
                "size_mm": properties["size_mm"],
                "link_transform": link_transform,
            }
        )

    if total_mass_kg <= 0.0:
        return None

    center_m = tuple(
        (weighted_center_mm[axis] / total_mass_kg) * 0.001
        for axis in range(3)
    )
    inertia = [[0.0, 0.0, 0.0] for _ in range(3)]
    for part in placed_parts:
        part_inertia = _rotated_box_inertia_kg_m2(
            mass_kg=float(part["mass_kg"]),
            size_mm=part["size_mm"],  # type: ignore[arg-type]
            transform=part["link_transform"],  # type: ignore[arg-type]
        )
        center_link_m = tuple(
            component * 0.001
            for component in part["center_link_mm"]  # type: ignore[union-attr]
        )
        offset_m = tuple(center_link_m[index] - center_m[index] for index in range(3))
        _add_parallel_axis(
            part_inertia,
            mass_kg=float(part["mass_kg"]),
            offset_m=offset_m,  # type: ignore[arg-type]
        )
        for row in range(3):
            for col in range(3):
                inertia[row][col] += part_inertia[row][col]

    return _xml(
        "inertial",
        children=(
            _xml(
                "origin",
                {
                    "xyz": " ".join(_urdf_number(value) for value in center_m),
                    "rpy": "0 0 0",
                },
            ),
            _xml("mass", {"value": _urdf_number(total_mass_kg)}),
            _xml(
                "inertia",
                {
                    "ixx": _urdf_number(inertia[0][0]),
                    "ixy": _urdf_number(inertia[0][1]),
                    "ixz": _urdf_number(inertia[0][2]),
                    "iyy": _urdf_number(inertia[1][1]),
                    "iyz": _urdf_number(inertia[1][2]),
                    "izz": _urdf_number(inertia[2][2]),
                },
            ),
        ),
    )


def _visual_element_for_instance(
    instance: dict[str, object],
    *,
    source_link_frame: list[float],
    urdf_link_frame: list[float],
) -> ET.Element:
    step_path = str(instance["path"])
    source_local_transform = [float(value) for value in instance["transform"]]
    world_transform = multiply_transforms(source_link_frame, source_local_transform)
    transform = multiply_transforms(
        invert_rigid_transform(urdf_link_frame),
        world_transform,
    )
    return _xml(
        "visual",
        children=(
            _xml("origin", _urdf_origin_attrs_from_transform(transform)),
            _xml(
                "geometry",
                children=(
                    _xml(
                        "mesh",
                        {
                            "filename": _mesh_filename_for_step_path(step_path),
                            "scale": "0.001 0.001 0.001",
                        },
                    ),
                ),
            ),
            _xml("material", {"name": _material_name_for_step_path(step_path)}),
        ),
    )


def _link_element(
    link_name: str,
    *,
    source_frames: dict[str, list[float]],
    urdf_frames: dict[str, list[float]],
) -> ET.Element:
    owner_source_link_frame = source_frames.get(
        link_name,
        urdf_frames.get(link_name, _identity_transform()),
    )
    urdf_link_frame = urdf_frames.get(link_name, owner_source_link_frame)
    instances = _instances_for_urdf_link(link_name)
    children: list[ET.Element] = []
    inertial = _inertial_element_for_instances(
        instances,
        link_name=link_name,
        source_frames=source_frames,
        urdf_link_frame=urdf_link_frame,
    )
    if inertial is not None:
        children.append(inertial)
    children.extend(
        _visual_element_for_instance(
            instance,
            source_link_frame=source_frames.get(
                str(instance.get("source_link_name", link_name)),
                urdf_frames.get(
                    str(instance.get("source_link_name", link_name)),
                    _identity_transform(),
                ),
            ),
            urdf_link_frame=urdf_link_frame,
        )
        for instance in instances
    )
    return _xml(
        "link",
        {"name": link_name},
        tuple(children),
    )


def _joint_element(
    joint: dict[str, object],
    *,
    link_frames: dict[str, list[float]],
    axes_by_joint: dict[str, str],
) -> ET.Element:
    parent = str(joint["parent"])
    child = str(joint["child"])
    attrs = {"name": str(joint["name"]), "type": str(joint["joint_type"])}
    attrs.update({str(key): str(value) for key, value in dict(joint.get("urdf_attrs", {})).items()})

    if parent in link_frames and child in link_frames:
        relative = multiply_transforms(
            invert_rigid_transform(link_frames[parent]),
            link_frames[child],
        )
        origin_attrs = _urdf_origin_attrs_from_transform(relative)
    else:
        origin_attrs = {
            "xyz": str(joint["origin_xyz"]),
            "rpy": str(joint["origin_rpy"]),
        }

    children = [
        _xml("parent", {"link": parent}),
        _xml("child", {"link": child}),
        _xml("origin", origin_attrs),
    ]
    axis_xyz = axes_by_joint.get(str(joint["name"]), joint.get("axis_xyz"))
    if axis_xyz is not None:
        children.append(_xml("axis", {"xyz": str(axis_xyz)}))
    limit = joint.get("limit")
    if limit is not None:
        limit_attrs = {str(key): str(value) for key, value in dict(limit).items()}
        effort_nm = URDF_JOINT_EFFORT_NM_BY_NAME.get(str(joint["name"]))
        if effort_nm is not None:
            limit_attrs["effort"] = _urdf_number(effort_nm)
        velocity_rad_s = URDF_JOINT_VELOCITY_RAD_S_BY_NAME.get(str(joint["name"]))
        if velocity_rad_s is not None:
            limit_attrs["velocity"] = _urdf_number(velocity_rad_s)
        children.append(_xml("limit", limit_attrs))
    mimic = joint.get("mimic")
    if mimic is not None:
        children.append(_xml("mimic", {str(key): str(value) for key, value in dict(mimic).items()}))
    return _xml("joint", attrs, tuple(children))


def _joints_for_link_names(link_names: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    link_name_set = set(link_names)
    return tuple(
        dict(joint)
        for joint in robot_arm.URDF_JOINTS
        if str(joint["parent"]) in link_name_set and str(joint["child"]) in link_name_set
    )


def _no_gripper_joints() -> tuple[dict[str, object], ...]:
    return _joints_for_link_names(NO_GRIPPER_LINK_NAMES)


def _robot_common_link(name: str) -> dict[str, object]:
    for link in robot_arm.URDF_LINKS:
        if str(link["name"]) == name:
            return dict(link)
    raise RuntimeError(f"Missing shared URDF link {name!r}")


def gen_urdf_with_options(
    *,
    include_gripper: bool = False,
    robot_name: str = "tom_v2",
    source_name: str = "models/robots/tom/v2/tom.py",
) -> dict[str, object]:
    """Jointed v2 URDF using 3MF visual meshes.

    Design ledger:
    - Robot name: tom_v2 or tom_v2_with_gripper.
    - Target consumers: CAD Viewer, RViz/robot_state_publisher-style display.
    - Units: URDF meters/radians; v2 STEP/3MF meshes are authored in millimeters.
    - Frame convention: each moving child link frame is placed on its incoming
      servo axis, measured as the line between the rear and output horn face
      centers. Link visuals are rebased from the v2 source link frames into
      those horn-axis URDF frames.
    - Roll joints: the upstream yoke remains on the pitch link, while the
      bottom servo body is owned by the child roll link so the servo case
      rotates with the bracket/link body.
    - Mesh source: 3MF visual meshes only. V2 brackets, mounts, standoffs,
      stock servos, and optional gripper component meshes live in v2/3MF.
    - Kinematics: the default tom.urdf stops at wrist_roll_link; the
      tom_with_gripper.urdf variant adds the Robonine gripper base, servo gear,
      claw slide links, and mimic joints.
    - Inertials: link masses are assembled from the same STEP instances as the
      visuals. Servos use Feetech published masses; aluminum plates and
      standoffs use 5052 density and current STEP volumes. Gripper inertials
      reuse the shared Robonine mesh-derived estimates. Arm inertia tensors use
      transformed bounding-box approximations around the computed center of
      mass. Collision elements are intentionally omitted for the arm pending
      simplified collision geometry.
    """
    source_frames, urdf_frames, axes_by_joint = _urdf_link_frames_and_axes(
        include_gripper=include_gripper,
    )
    link_names = WITH_GRIPPER_LINK_NAMES if include_gripper else NO_GRIPPER_LINK_NAMES
    joints = _joints_for_link_names(link_names)

    robot = _xml("robot", {"name": robot_name})
    robot.append(ET.Comment(f" Generated by {source_name}. "))
    robot.append(
        ET.Comment(
            " Jointed v2 URDF; all visual meshes are 3MF in "
            "millimeters with scale 0.001 0.001 0.001. Link inertials use "
            "Feetech STS3250 74.5 g / STS3215 55 g masses and 5052 aluminum "
            "density for plates/standoffs; gripper inertials use shared "
            "Robonine mesh-derived estimates when included. Joint efforts use "
            "50/30 kg-cm stall torque converted to N-m for arm joints; joint "
            "velocities use Feetech no-load speed at 12 V converted to rad/s. "
        )
    )
    for material_name, rgba in URDF_MATERIALS.items():
        robot.append(
            _xml(
                "material",
                {"name": material_name},
                (_xml("color", {"rgba": _rgba_text(rgba)}),),
            )
        )
    for link_name in link_names:
        if link_name in GRIPPER_LINK_NAMES:
            robot.append(robot_arm._urdf_link_element(_robot_common_link(link_name)))
        else:
            robot.append(
                _link_element(
                    link_name,
                    source_frames=source_frames,
                    urdf_frames=urdf_frames,
                )
            )
    for joint in joints:
        robot.append(
            _joint_element(
                joint,
                link_frames=urdf_frames,
                axes_by_joint=axes_by_joint,
            )
        )
    return {"xml": _serialize_urdf(robot)}


def gen_urdf() -> dict[str, object]:
    """Jointed no-gripper URDF using 3MF visual meshes."""
    return gen_urdf_with_options(include_gripper=False)


def _srdf_joint_degrees_to_radians(joint_values_deg: dict[str, float]) -> dict[str, float]:
    return {
        joint_name: math.radians(float(value_deg))
        for joint_name, value_deg in joint_values_deg.items()
    }


def _srdf_group_state_element(
    *,
    name: str,
    group: str,
    joint_values_rad: dict[str, float],
) -> ET.Element:
    return _xml(
        "group_state",
        {"name": name, "group": group},
        tuple(
            _xml("joint", {"name": joint_name, "value": _urdf_number(value_rad)})
            for joint_name, value_rad in joint_values_rad.items()
        ),
    )


def _srdf_arm_group_states_deg() -> tuple[tuple[str, dict[str, float]], ...]:
    old_home_elbow_pitch_deg = 0.0
    for state_name, joint_values_deg in robot_arm.ROBOT_ARM_SRDF_ARM_GROUP_STATES_DEG:
        if state_name == "home":
            old_home_elbow_pitch_deg = float(joint_values_deg.get("elbow_pitch", 0.0))
            break
    elbow_pitch_offset_deg = V2_HOME_ELBOW_PITCH_DEG - old_home_elbow_pitch_deg
    states: list[tuple[str, dict[str, float]]] = []
    for state_name, joint_values_deg in robot_arm.ROBOT_ARM_SRDF_ARM_GROUP_STATES_DEG:
        next_values = dict(joint_values_deg)
        next_values["elbow_pitch"] = float(next_values.get("elbow_pitch", 0.0)) + elbow_pitch_offset_deg
        if state_name == "reach_forward":
            next_values = dict(V2_SRDF_REACH_ARM_GROUP_STATE_DEG)
        elif state_name == "inspection":
            next_values = dict(V2_SRDF_INSPECTION_ARM_GROUP_STATE_DEG)
        elif state_name == "inspection_mirrored":
            next_values = dict(V2_SRDF_INSPECTION_MIRRORED_ARM_GROUP_STATE_DEG)
        states.append((state_name, next_values))
    return tuple(states)


def _srdf_adjacent_collision_pairs_from_urdf(urdf: str) -> tuple[tuple[str, str], ...]:
    urdf_root = ET.parse(V2_DIR / urdf).getroot()
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for joint in urdf_root.findall("joint"):
        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            continue
        parent_link = str(parent.attrib.get("link", ""))
        child_link = str(child.attrib.get("link", ""))
        if not parent_link or not child_link or "base_footprint" in {parent_link, child_link}:
            continue
        key = tuple(sorted((parent_link, child_link)))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((parent_link, child_link))
    return tuple(pairs)


def _srdf_root_element(
    *,
    robot_name: str,
    urdf: str,
    include_gripper: bool,
) -> ET.Element:
    root = _xml("robot", {"name": robot_name})
    root.append(ET.Comment(f" Generated by models/robots/tom/v2/{Path(urdf).stem}.py for MoveIt2 and CAD Viewer test poses. "))
    root.append(
        _xml(
            "virtual_joint",
            {
                "name": "fixed_base",
                "type": "fixed",
                "parent_frame": "world",
                "child_link": "base_footprint",
            },
        )
    )

    arm_group = _xml("group", {"name": "arm"})
    arm_group.append(_xml("chain", {"base_link": "base_link", "tip_link": "wrist_roll_link"}))
    root.append(arm_group)

    if include_gripper:
        tcp_group = _xml("group", {"name": "tcp"})
        tcp_group.append(_xml("link", {"name": "gripper_base_link"}))
        root.append(tcp_group)

        gripper_group = _xml("group", {"name": "gripper"})
        gripper_group.append(_xml("joint", {"name": "gripper_servo"}))
        root.append(gripper_group)

        arm_with_gripper_group = _xml("group", {"name": "arm_with_gripper"})
        arm_with_gripper_group.append(_xml("group", {"name": "arm"}))
        arm_with_gripper_group.append(_xml("group", {"name": "gripper"}))
        root.append(arm_with_gripper_group)

        root.append(
            _xml(
                "end_effector",
                {
                    "name": "gripper_tcp",
                    "parent_link": "wrist_roll_link",
                    "group": "tcp",
                    "parent_group": "arm",
                },
            )
        )

    for name, joint_values_deg in _srdf_arm_group_states_deg():
        root.append(
            _srdf_group_state_element(
                name=name,
                group="arm",
                joint_values_rad=_srdf_joint_degrees_to_radians(joint_values_deg),
            )
        )

    if include_gripper:
        for name, joint_values_rad in robot_arm.ROBOT_ARM_SRDF_GRIPPER_GROUP_STATES_RAD:
            root.append(
                _srdf_group_state_element(
                    name=name,
                    group="gripper",
                    joint_values_rad=joint_values_rad,
                )
            )

    for link1, link2 in _srdf_adjacent_collision_pairs_from_urdf(urdf):
        root.append(_xml("disable_collisions", {"link1": link1, "link2": link2, "reason": "Adjacent"}))
    return root


def gen_srdf_with_options(
    *,
    include_gripper: bool = False,
    robot_name: str = "tom_v2",
    urdf: str = "tom.urdf",
) -> dict[str, object]:
    """MoveIt semantic groups and named states for the v2 URDF variants."""
    return {
        "xml": _serialize_urdf(
            _srdf_root_element(
                robot_name=robot_name,
                urdf=urdf,
                include_gripper=include_gripper,
            )
        ),
        "urdf": urdf,
    }


def gen_srdf() -> dict[str, object]:
    """MoveIt semantic groups and named states for the no-gripper v2 arm."""
    return gen_srdf_with_options(include_gripper=False)
