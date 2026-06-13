from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parent
TOM_DIR = V2_DIR.parent
ASSEMBLIES_DIR = V2_DIR / "assemblies"
for path in (TOM_DIR, V2_DIR, ASSEMBLIES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from robot_common import robot_arm
from robot_common.materials import GRAY_ALUMINUM_RGBA
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

NEXT_PITCH_LINK_BY_ROLL_LINK = {
    "shoulder": "elbow_pitch_link",
    "elbow": "wrist_pitch_link",
}

YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM = -9.1
YOKE_180_ABOUT_WEB_AXIS_TRANSFORM = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -1.0,
    0.0,
    2.0 * YOKE_HORN_SPAN_CENTER_LOCAL_Y_MM,
    0.0,
    0.0,
    -1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)

URDF_MATERIALS = {
    "aluminum_5052": GRAY_ALUMINUM_RGBA,
}

URDF_STEP_MATERIALS = {
    "base_plate.step": "aluminum_5052",
    "servo_end_mount.step": "aluminum_5052",
    "servo_horn_yoke.step": "aluminum_5052",
    "link_bracket_right.step": "aluminum_5052",
    "link_bracket_left.step": "aluminum_5052",
}


def _mate(
    source_label: str,
    *,
    fixed: str,
    moving: str,
    relation: str = "rigid",
) -> dict[str, object]:
    fixed_part, fixed_frame = fixed.split(":", 1)
    moving_part, moving_frame = moving.split(":", 1)
    return {
        "sourceLabel": source_label,
        "type": relation,
        "relation": relation,
        "fixed": fixed,
        "moving": moving,
        "parameters": {},
        "fixedEndpoint": {
            "part": fixed_part,
            "frame": fixed_frame,
        },
        "movingEndpoint": {
            "part": moving_part,
            "frame": moving_frame,
        },
    }


def _assembly_mates() -> list[dict[str, object]]:
    return [
        _mate(
            "base_servo_to_base_plate",
            fixed="base_link__base_plate:top_plate",
            moving="base_link__sts3250_3:case_mount",
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


def gen_step() -> dict[str, object]:
    instances: list[dict[str, object]] = []
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

        if child_name == GRIPPER_CHILD_NAME:
            instances.append(
                {
                    "path": str(source_child["path"]),
                    "name": child_name,
                    "transform": child_transform,
                    "use_source_colors": bool(source_child.get("use_source_colors", True)),
                }
            )
            continue

        instances.extend(
            _flat_child_instances(
                child_name=child_name,
                child_transform=child_transform,
            )
        )

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
        "assembly_mates": _assembly_mates(),
    }
