from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


V2_DIR = Path(__file__).resolve().parent
TOM_SOURCE = V2_DIR / "tom.py"
DOUBLE_SOURCE = V2_DIR / "assemblies" / "servo_horn_yoke_double.py"
ASSEMBLIES_DIR = V2_DIR / "assemblies"
PARTS_DIR = V2_DIR / "parts"

for path in (V2_DIR.parent, V2_DIR, PARTS_DIR, ASSEMBLIES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SHOULDER_DOUBLE_CHILD_NAME = "shoulder_yaw_link"
SHOULDER_DOUBLE_PRIMARY_SERVO = "sts3250_rear"
SHOULDER_DOUBLE_ORIGINAL_SERVO_INSTANCE = "shoulder_yaw_link__sts3250_1"
SHOULDER_DOUBLE_REPLACED_MATES = frozenset(
    {
        "base_servo_horn_to_shoulder_yaw_mount",
        "shoulder_yaw_mount_to_servo",
        "shoulder_pitch_yoke_to_servo",
    }
)
SHOULDER_DOUBLE_REMOVED_INSTANCES = frozenset(
    {
        "shoulder_pitch_link__servo_horn_yoke",
    }
)
SHOULDER_DOUBLE_URDF_MESH_BY_STEP_BASENAME = {
    "servo_horn_yoke_double_horn.step": "3MF/servo_horn_yoke_double_horn.3mf",
    "servo_end_mount_double.step": "3MF/servo_end_mount_double.3mf",
}
SHOULDER_DOUBLE_URDF_MATERIAL_BY_STEP_BASENAME = {
    "servo_horn_yoke_double_horn.step": "aluminum_5052",
    "servo_end_mount_double.step": "aluminum_5052",
}


def _load_module(path: Path, module_label: str):
    module_name = f"_tom_v2_{module_label}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _prefix_mate(parent_name: str, mate: dict[str, object]) -> dict[str, object]:
    fixed_part, fixed_frame = str(mate["fixed"]).split(":", 1)
    moving_part, moving_frame = str(mate["moving"]).split(":", 1)
    fixed = f"{parent_name}__{fixed_part}:{fixed_frame}"
    moving = f"{parent_name}__{moving_part}:{moving_frame}"
    return {
        **mate,
        "sourceLabel": f"{parent_name}_{mate['sourceLabel']}",
        "fixed": fixed,
        "moving": moving,
        "fixedEndpoint": {
            "part": fixed.split(":", 1)[0],
            "frame": fixed_frame,
        },
        "movingEndpoint": {
            "part": moving.split(":", 1)[0],
            "frame": moving_frame,
        },
    }


def _replace_shoulder_double_mates(
    *,
    tom_module,
    double_module_envelope: dict[str, object],
    include_gripper: bool,
) -> list[dict[str, object]]:
    mates = [
        mate
        for mate in tom_module._assembly_mates(include_gripper=include_gripper)
        if str(mate["sourceLabel"]) not in SHOULDER_DOUBLE_REPLACED_MATES
    ]
    mates.extend(
        _prefix_mate(SHOULDER_DOUBLE_CHILD_NAME, mate)
        for mate in double_module_envelope.get("assembly_mates", [])
    )
    mates.append(
        tom_module._mate(
            "base_servo_horn_to_shoulder_yaw_double_mount",
            fixed="base_link__sts3250_3:output_horn_face",
            moving=f"{SHOULDER_DOUBLE_CHILD_NAME}__servo_end_mount_double:front_horn_mount_face",
            parameters={"clearance_mm": tom_module.BASE_TO_SHOULDER_YAW_HORN_CLEARANCE_MM},
        )
    )
    return mates


def _local_transform_by_name(
    instances: list[dict[str, object]],
    name: str,
) -> list[float]:
    for instance in instances:
        if str(instance["name"]) == name:
            return [float(value) for value in instance["transform"]]
    raise RuntimeError(f"Unable to find local transform for {name}")


def _shoulder_double_parent_transform(
    *,
    tom_module,
    child_name: str,
    design_child_transform: list[float],
    double_module_envelope: dict[str, object],
    base_servo_transform: list[float],
) -> list[float]:
    original_module_transform = tom_module._module_transform_for_child(
        child_name=child_name,
        design_child_transform=design_child_transform,
    )
    original_instances = tom_module._flat_child_instances(
        child_name=child_name,
        child_transform=original_module_transform,
    )
    original_servo_transform = _local_transform_by_name(
        original_instances,
        SHOULDER_DOUBLE_ORIGINAL_SERVO_INSTANCE,
    )
    primary_servo_local = _local_transform_by_name(
        double_module_envelope["instances"],
        f"sts3250_{SHOULDER_DOUBLE_PRIMARY_SERVO.removeprefix('sts3250_')}",
    )
    parent_transform = tom_module.multiply_transforms(
        original_servo_transform,
        tom_module.invert_rigid_transform(primary_servo_local),
    )
    base_horn_face_center = tom_module._transform_point(
        base_servo_transform,
        (
            tom_module.STS3215_HORN_AXIS_LOCAL_X_MM,
            tom_module.STS3250_OUTPUT_HORN_FACE_LOCAL_Y_MM,
            0.0,
        ),
    )
    double_mount_face_center = tom_module._transform_point(
        parent_transform,
        (
            tom_module.SERVO_END_MOUNT_FRONT_HORN_FACE_CENTER_LOCAL_MM[0],
            0.0,
            0.0,
        ),
    )
    return tom_module._translate_transform(
        parent_transform,
        (
            base_horn_face_center[0] - double_mount_face_center[0],
            base_horn_face_center[1] - double_mount_face_center[1],
            base_horn_face_center[2] - double_mount_face_center[2],
        ),
    )


def gen_step_with_options(*, include_gripper: bool = False) -> dict[str, object]:
    tom_module = _load_module(TOM_SOURCE, "tom_double_base")
    double_module = _load_module(DOUBLE_SOURCE, "shoulder_double")
    double_envelope = double_module.gen_step()

    instances: list[dict[str, object]] = []
    instance_transforms_by_name: dict[str, list[float]] = {}
    downstream_correction = tom_module._identity_transform()
    pending_child_transform_overrides: dict[str, list[float]] = {}

    for source_child in tom_module.robot_arm.robot_arm_assembly_children():
        child_name = str(source_child["name"])
        source_child_transform = [float(value) for value in source_child["transform"]]
        child_transform = pending_child_transform_overrides.pop(child_name, None)
        if child_transform is not None:
            downstream_correction = tom_module.multiply_transforms(
                child_transform,
                tom_module.invert_rigid_transform(source_child_transform),
            )
        else:
            child_transform = tom_module.multiply_transforms(
                downstream_correction,
                source_child_transform,
            )

        if child_name == "shoulder_yaw_link":
            base_servo_transform = instance_transforms_by_name.get("base_link__sts3250_3")
            if base_servo_transform is not None:
                child_transform = tom_module._mate_shoulder_yaw_mount_to_base_servo_horn(
                    shoulder_yaw_transform=child_transform,
                    base_servo_transform=base_servo_transform,
                )
                downstream_correction = tom_module.multiply_transforms(
                    child_transform,
                    tom_module.invert_rigid_transform(source_child_transform),
                )

        if child_name == tom_module.GRIPPER_CHILD_NAME:
            if not include_gripper:
                continue
            terminal_servo_transform = instance_transforms_by_name.get(
                tom_module.V2_GRIPPER_SERVO_INSTANCE_NAME
            )
            if terminal_servo_transform is not None:
                child_transform = tom_module._mate_gripper_to_terminal_servo_horn(
                    gripper_transform=child_transform,
                    terminal_servo_transform=terminal_servo_transform,
                )
            instances.append(
                {
                    "path": str(source_child["path"]),
                    "name": child_name,
                    "transform": child_transform,
                    "use_source_colors": bool(source_child.get("use_source_colors", True)),
                }
            )
            instance_transforms_by_name[child_name] = child_transform
            continue

        if child_name == SHOULDER_DOUBLE_CHILD_NAME:
            base_servo_transform = instance_transforms_by_name.get("base_link__sts3250_3")
            if base_servo_transform is None:
                raise RuntimeError("Cannot place shoulder double before base servo is available")
            double_parent_transform = _shoulder_double_parent_transform(
                tom_module=tom_module,
                child_name=child_name,
                design_child_transform=child_transform,
                double_module_envelope=double_envelope,
                base_servo_transform=base_servo_transform,
            )
            child_instances = tom_module._flatten_instances(
                parent_name=child_name,
                parent_transform=double_parent_transform,
                local_instances=double_envelope["instances"],
                local_source_dir=ASSEMBLIES_DIR,
            )
            instances.extend(child_instances)
            for instance in child_instances:
                instance_transforms_by_name[str(instance["name"])] = [
                    float(value) for value in instance["transform"]
                ]
            continue

        module_child_transform = tom_module._module_transform_for_child(
            child_name=child_name,
            design_child_transform=child_transform,
        )
        child_instances = tom_module._flat_child_instances(
            child_name=child_name,
            child_transform=module_child_transform,
        )
        child_instances = [
            instance
            for instance in child_instances
            if str(instance["name"]) not in SHOULDER_DOUBLE_REMOVED_INSTANCES
        ]
        instances.extend(child_instances)
        for instance in child_instances:
            instance_transforms_by_name[str(instance["name"])] = [
                float(value) for value in instance["transform"]
            ]

        replacement_kind = tom_module.ROLL_LINK_REPLACEMENTS.get(child_name)
        if replacement_kind is not None:
            mates = tom_module.roll_link_mates(replacement_kind)
            next_pitch_child_name = tom_module.NEXT_PITCH_LINK_BY_ROLL_LINK.get(replacement_kind)
            if next_pitch_child_name is not None:
                upstream_servo_transform = tom_module.multiply_transforms(
                    child_transform,
                    mates.downstream_servo_local,
                )
                pending_child_transform_overrides[next_pitch_child_name] = (
                    tom_module._yoke_transform_for_servo_horn(
                        upstream_servo_transform=upstream_servo_transform,
                    )
                )

    return {
        "instances": instances,
        "assembly_mates": _replace_shoulder_double_mates(
            tom_module=tom_module,
            double_module_envelope=double_envelope,
            include_gripper=include_gripper,
        ),
    }


def gen_step() -> dict[str, object]:
    envelope = gen_step_with_options(include_gripper=False)
    return {
        "instances": envelope["instances"],
        "assembly_mates": envelope.get("assembly_mates", []),
    }


def _zero_pose_source_frames_instances_and_double_visuals(
    *,
    tom_module,
    double_module_envelope: dict[str, object],
) -> tuple[
    dict[str, list[float]],
    dict[str, list[float]],
    dict[str, list[dict[str, object]]],
]:
    frames: dict[str, list[float]] = {"base_footprint": tom_module._identity_transform()}
    instance_transforms_by_name: dict[str, list[float]] = {}
    visual_instances_by_link: dict[str, list[dict[str, object]]] = {}
    downstream_correction = tom_module._identity_transform()
    pending_child_transform_overrides: dict[str, list[float]] = {}

    for source_child in tom_module.robot_arm.robot_arm_assembly_children():
        child_name = str(source_child["name"])
        source_child_transform = [float(value) for value in source_child["transform"]]
        child_transform = pending_child_transform_overrides.pop(child_name, None)
        if child_transform is not None:
            downstream_correction = tom_module.multiply_transforms(
                child_transform,
                tom_module.invert_rigid_transform(source_child_transform),
            )
        else:
            child_transform = tom_module.multiply_transforms(
                downstream_correction,
                source_child_transform,
            )

        if child_name == "shoulder_yaw_link":
            base_servo_transform = instance_transforms_by_name.get("base_link__sts3250_3")
            if base_servo_transform is not None:
                child_transform = tom_module._mate_shoulder_yaw_mount_to_base_servo_horn(
                    shoulder_yaw_transform=child_transform,
                    base_servo_transform=base_servo_transform,
                )
                downstream_correction = tom_module.multiply_transforms(
                    child_transform,
                    tom_module.invert_rigid_transform(source_child_transform),
                )

        if child_name == tom_module.GRIPPER_CHILD_NAME:
            continue

        if child_name == SHOULDER_DOUBLE_CHILD_NAME:
            base_servo_transform = instance_transforms_by_name.get("base_link__sts3250_3")
            if base_servo_transform is None:
                raise RuntimeError("Cannot place shoulder double before base servo is available")
            frames[child_name] = child_transform
            double_parent_transform = _shoulder_double_parent_transform(
                tom_module=tom_module,
                child_name=child_name,
                design_child_transform=child_transform,
                double_module_envelope=double_module_envelope,
                base_servo_transform=base_servo_transform,
            )
            child_instances = tom_module._flatten_instances(
                parent_name=child_name,
                parent_transform=double_parent_transform,
                local_instances=double_module_envelope["instances"],
                local_source_dir=ASSEMBLIES_DIR,
            )
            visual_instances_by_link[child_name] = []
            inverse_source_frame = tom_module.invert_rigid_transform(child_transform)
            for instance in child_instances:
                world_transform = [float(value) for value in instance["transform"]]
                instance_transforms_by_name[str(instance["name"])] = world_transform
                visual_instance = dict(instance)
                visual_instance["transform"] = tom_module.multiply_transforms(
                    inverse_source_frame,
                    world_transform,
                )
                visual_instances_by_link[child_name].append(visual_instance)
            continue

        module_child_transform = tom_module._module_transform_for_child(
            child_name=child_name,
            design_child_transform=child_transform,
        )
        frames[child_name] = module_child_transform
        child_instances = tom_module._flat_child_instances(
            child_name=child_name,
            child_transform=module_child_transform,
        )
        child_instances = [
            instance
            for instance in child_instances
            if str(instance["name"]) not in SHOULDER_DOUBLE_REMOVED_INSTANCES
        ]
        for instance in child_instances:
            instance_transforms_by_name[str(instance["name"])] = [
                float(value) for value in instance["transform"]
            ]

        replacement_kind = tom_module.ROLL_LINK_REPLACEMENTS.get(child_name)
        if replacement_kind is not None:
            mates = tom_module.roll_link_mates(replacement_kind)
            next_pitch_child_name = tom_module.NEXT_PITCH_LINK_BY_ROLL_LINK.get(replacement_kind)
            if next_pitch_child_name is not None:
                upstream_servo_transform = tom_module.multiply_transforms(
                    child_transform,
                    mates.downstream_servo_local,
                )
                pending_child_transform_overrides[next_pitch_child_name] = (
                    tom_module._yoke_transform_for_servo_horn(
                        upstream_servo_transform=upstream_servo_transform,
                    )
                )

    wrist_pitch = frames.get("wrist_pitch_link")
    if wrist_pitch is not None and "wrist_roll_link" not in frames:
        wrist_roll_joint = next(
            joint
            for joint in tom_module.robot_arm.URDF_JOINTS
            if str(joint["name"]) == "wrist_roll"
        )
        frames["wrist_roll_link"] = tom_module.multiply_transforms(
            wrist_pitch,
            tom_module._transform_from_urdf_origin_attrs(
                str(wrist_roll_joint["origin_xyz"]),
                str(wrist_roll_joint["origin_rpy"]),
            ),
        )

    return frames, instance_transforms_by_name, visual_instances_by_link


def _double_horn_axis(
    tom_module,
    instance_transforms_by_name: dict[str, list[float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    horn_transform = instance_transforms_by_name.get(
        f"{SHOULDER_DOUBLE_CHILD_NAME}__servo_horn_yoke_double_horn"
    )
    if horn_transform is None:
        raise RuntimeError("Missing double horn transform for shoulder_pitch URDF axis")
    center = tom_module._transform_point(
        horn_transform,
        (
            tom_module.STS3215_HORN_AXIS_LOCAL_X_MM,
            0.0,
            0.0,
        ),
    )
    direction = tom_module._normalized_vector(
        tom_module._transform_direction(horn_transform, (0.0, 1.0, 0.0))
    )
    return center, direction


def _urdf_link_frames_axes_and_double_visuals(
    *,
    tom_module,
    double_module_envelope: dict[str, object],
) -> tuple[
    dict[str, list[float]],
    dict[str, list[float]],
    dict[str, str],
    dict[str, list[dict[str, object]]],
]:
    source_frames, instance_transforms, double_visuals = (
        _zero_pose_source_frames_instances_and_double_visuals(
            tom_module=tom_module,
            double_module_envelope=double_module_envelope,
        )
    )
    urdf_frames = {name: list(transform) for name, transform in source_frames.items()}
    axes_by_joint: dict[str, str] = {}
    servo_axis_instance_by_joint = dict(tom_module.URDF_SERVO_AXIS_INSTANCE_BY_JOINT)

    for joint in tom_module._no_gripper_joints():
        joint_name = str(joint["name"])
        if joint_name == "shoulder_pitch":
            axis_center_world, axis_direction_world = _double_horn_axis(
                tom_module,
                instance_transforms,
            )
        else:
            servo_instance_name = servo_axis_instance_by_joint.get(joint_name)
            if not servo_instance_name:
                continue
            servo_transform = instance_transforms.get(servo_instance_name)
            if servo_transform is None:
                raise RuntimeError(
                    f"Missing servo instance {servo_instance_name!r} "
                    f"for URDF joint {joint_name!r}"
                )
            axis_center_world, axis_direction_world = tom_module._servo_horn_axis_from_transform(
                servo_transform,
            )

        child_link = str(joint["child"])
        if child_link not in urdf_frames:
            parent_frame = urdf_frames.get(str(joint["parent"]))
            if parent_frame is None:
                raise RuntimeError(f"Missing parent frame for URDF joint {joint_name!r}")
            urdf_frames[child_link] = list(parent_frame)
        urdf_frames[child_link] = tom_module._with_translation(
            urdf_frames[child_link],
            axis_center_world,
        )
        axis_in_joint_frame = tom_module._world_vector_to_local(
            urdf_frames[child_link],
            axis_direction_world,
        )
        axes_by_joint[joint_name] = tom_module._axis_attrs(axis_in_joint_frame)

    return source_frames, urdf_frames, axes_by_joint, double_visuals


def _instances_for_urdf_link_double(
    *,
    tom_module,
    link_name: str,
    double_visuals: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    if link_name == SHOULDER_DOUBLE_CHILD_NAME:
        return [dict(instance) for instance in double_visuals.get(link_name, [])]
    if link_name == "shoulder_pitch_link":
        return []
    return tom_module._instances_for_urdf_link(link_name)


def _link_element_double(
    *,
    tom_module,
    link_name: str,
    source_frames: dict[str, list[float]],
    urdf_frames: dict[str, list[float]],
    double_visuals: dict[str, list[dict[str, object]]],
):
    owner_source_link_frame = source_frames.get(
        link_name,
        urdf_frames.get(link_name, tom_module._identity_transform()),
    )
    urdf_link_frame = urdf_frames.get(link_name, owner_source_link_frame)
    instances = _instances_for_urdf_link_double(
        tom_module=tom_module,
        link_name=link_name,
        double_visuals=double_visuals,
    )
    children = []
    inertial = tom_module._inertial_element_for_instances(
        instances,
        link_name=link_name,
        source_frames=source_frames,
        urdf_link_frame=urdf_link_frame,
    )
    if inertial is not None:
        children.append(inertial)
    children.extend(
        tom_module._visual_element_for_instance(
            instance,
            source_link_frame=source_frames.get(
                str(instance.get("source_link_name", link_name)),
                urdf_frames.get(
                    str(instance.get("source_link_name", link_name)),
                    tom_module._identity_transform(),
                ),
            ),
            urdf_link_frame=urdf_link_frame,
        )
        for instance in instances
    )
    return tom_module._xml(
        "link",
        {"name": link_name},
        tuple(children),
    )


def gen_urdf() -> dict[str, object]:
    """Jointed no-gripper URDF for the tom_double shoulder-yaw variant.

    Design ledger:
    - Based on tom_v2's no-gripper URDF and 3MF visual mesh convention.
    - The shoulder-yaw single STS3250/end-mount visual module is replaced by
      the side-by-side double servo horn/mount assembly.
    - The shoulder_pitch joint frame/axis is derived from the double horn plate
      centerline, not from either offset servo body.
    - Inertials use the same vendor servo masses and 5052 STEP-volume
      plate/standoff mass model as tom.urdf. Gripper links and collision
      elements are intentionally omitted to match tom.urdf's current scope.
    """
    tom_module = _load_module(TOM_SOURCE, "tom_double_urdf_base")
    double_module = _load_module(DOUBLE_SOURCE, "tom_double_urdf_shoulder_double")
    double_envelope = double_module.gen_step()
    tom_module.URDF_JOINT_EFFORT_NM_BY_NAME = dict(
        tom_module.URDF_JOINT_EFFORT_NM_BY_NAME
    )
    tom_module.URDF_JOINT_EFFORT_NM_BY_NAME["shoulder_pitch"] = (
        2.0 * tom_module.STS3250_STALL_TORQUE_NM
    )
    tom_module.URDF_MESH_BY_STEP_BASENAME.update(SHOULDER_DOUBLE_URDF_MESH_BY_STEP_BASENAME)
    tom_module.URDF_MATERIAL_BY_STEP_BASENAME.update(
        SHOULDER_DOUBLE_URDF_MATERIAL_BY_STEP_BASENAME
    )

    source_frames, urdf_frames, axes_by_joint, double_visuals = (
        _urdf_link_frames_axes_and_double_visuals(
            tom_module=tom_module,
            double_module_envelope=double_envelope,
        )
    )
    robot = tom_module._xml("robot", {"name": "tom_v2_double"})
    robot.append(tom_module.ET.Comment(" Generated by models/robots/tom/v2/tom_double.py. "))
    robot.append(
        tom_module.ET.Comment(
            " Jointed no-gripper URDF for the double shoulder-yaw variant; "
            "all visual meshes are 3MF in millimeters with scale 0.001 0.001 0.001. "
            "Link inertials use Feetech STS3250 74.5 g / STS3215 55 g masses, "
            "5052 aluminum density for plates/standoffs, 2x STS3250 stall torque "
            "on shoulder_pitch, and single-servo stall torque plus no-load speeds "
            "elsewhere converted to SI units. "
        )
    )
    for material_name, rgba in tom_module.URDF_MATERIALS.items():
        robot.append(
            tom_module._xml(
                "material",
                {"name": material_name},
                (tom_module._xml("color", {"rgba": tom_module._rgba_text(rgba)}),),
            )
        )
    for link_name in tom_module.NO_GRIPPER_LINK_NAMES:
        robot.append(
            _link_element_double(
                tom_module=tom_module,
                link_name=link_name,
                source_frames=source_frames,
                urdf_frames=urdf_frames,
                double_visuals=double_visuals,
            )
        )
    for joint in tom_module._no_gripper_joints():
        robot.append(
            tom_module._joint_element(
                joint,
                link_frames=urdf_frames,
                axes_by_joint=axes_by_joint,
            )
        )
    return {"xml": tom_module._serialize_urdf(robot)}


def gen_srdf() -> dict[str, object]:
    """MoveIt semantic groups and named states for the double shoulder v2 arm."""
    return _load_module(TOM_SOURCE, "tom_double_srdf_base").gen_srdf_with_options(
        include_gripper=False,
        robot_name="tom_v2_double",
        urdf="tom_double.urdf",
    )
