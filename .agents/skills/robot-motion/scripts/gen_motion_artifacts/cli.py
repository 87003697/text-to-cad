from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET


MOTION_EXPLORER_METADATA_KIND = "texttocad-robot-motion-explorer"
MOTION_EXPLORER_METADATA_SCHEMA_VERSION = 1
MOTION_CONFIG_KIND = "texttocad-robot-motion-server"
MOTION_CONFIG_SCHEMA_VERSION = 1
MOTION_SERVER_VERSION = 1
SUPPORTED_PROVIDERS = {"moveit_py"}
SUPPORTED_COMMANDS = {"urdf.solvePose", "urdf.planToPose"}
DEFAULT_PLANNER = {
    "pipeline": "ompl",
    "plannerId": "RRTConnectkConfigDefault",
    "planningTime": 1.0,
}


class MotionArtifactError(ValueError):
    pass


def generate_motion_artifact_targets(targets: Sequence[str], *, summary: bool = False) -> int:
    generated = [_generate_target(target) for target in targets]
    if summary:
        for artifact_set in generated:
            print(
                f"{_display_path(artifact_set['urdfPath'])}: "
                f"group={artifact_set['planningGroup']} "
                f"commands={','.join(artifact_set['commands'])} "
                f"sidecars={len(artifact_set['sidecars'])}"
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_motion_artifacts",
        description="Generate motion server and CAD Explorer motion artifacts for explicit URDF targets.",
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Explicit Python motion source with zero-arg gen_motion().",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a compact summary for generated artifacts.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    return generate_motion_artifact_targets(args.targets, summary=args.summary)


def _generate_target(target: str) -> dict[str, object]:
    source_path = Path(target).resolve()
    if source_path.suffix.lower() != ".py":
        raise MotionArtifactError(f"{_display_path(source_path)} must be a Python motion source")
    envelope = _load_motion_envelope(source_path)

    urdf_path = _resolve_relative_file(envelope.get("urdf"), source_path=source_path, suffix=".urdf", label="urdf")
    robot = _read_urdf_robot(urdf_path)
    provider = _normalize_provider(envelope.get("provider"))
    planning_groups = _normalize_planning_groups(envelope, robot)
    default_planning_group = planning_groups[0]
    commands = _normalize_commands(envelope.get("commands"), has_planner=envelope.get("planner") is not None)
    end_effectors = _normalize_end_effectors(envelope.get("endEffectors"), robot, planning_groups)
    planner = _normalize_planner(envelope.get("planner")) if "urdf.planToPose" in commands else {}
    group_states = _normalize_group_states(envelope.get("groupStates"), planning_groups)
    disabled_pairs = _collision_pairs(robot, envelope.get("disabledCollisionPairs"))

    explorer_dir = urdf_path.parent / f".{urdf_path.name}"
    motion_dir = explorer_dir / "robot-motion"
    motion_dir.mkdir(parents=True, exist_ok=True)
    motion_explorer_metadata_path = motion_dir / "explorer.json"
    _write_json(motion_explorer_metadata_path, _motion_explorer_metadata(
        commands=commands,
        end_effectors=end_effectors,
    ))
    print(f"Wrote robot motion explorer metadata: {motion_explorer_metadata_path}")

    sidecars = {
        "motion_server.json": _motion_server_config(
            provider=provider,
            commands=commands,
            planning_groups=planning_groups,
            end_effectors=end_effectors,
            planner=planner,
        ),
        "moveit2_robot.srdf": _moveit_srdf(
            robot_name=robot["name"],
            planning_groups=planning_groups,
            end_effectors=end_effectors,
            group_states=group_states,
            disabled_pairs=disabled_pairs,
        ),
        "moveit2_kinematics.yaml": {
            str(planning_group["name"]): {
                "kinematics_solver": "kdl_kinematics_plugin/KDLKinematicsPlugin",
                "kinematics_solver_search_resolution": 0.005,
                "kinematics_solver_timeout": 0.05,
            }
            for planning_group in planning_groups
        },
        "moveit2_py.yaml": {
            "planning_scene_monitor_options": {
                "name": "planning_scene_monitor",
                "robot_description": "robot_description",
                "joint_state_topic": "/joint_states",
                "attached_collision_object_topic": "/moveit_cpp/planning_scene_monitor",
                "publish_planning_scene_topic": "/moveit_cpp/publish_planning_scene",
                "monitored_planning_scene_topic": "/moveit_cpp/monitored_planning_scene",
                "wait_for_initial_state_timeout": 5.0,
            }
        },
    }
    if "urdf.planToPose" in commands:
        sidecars["moveit2_planning_pipelines.yaml"] = _planning_pipelines(planner)

    for sidecar_name, payload in sidecars.items():
        sidecar_path = motion_dir / sidecar_name
        if isinstance(payload, str):
            text = payload if payload.endswith("\n") else payload + "\n"
            sidecar_path.write_text(text, encoding="utf-8")
        else:
            _write_json(sidecar_path, payload)
        print(f"Wrote robot motion sidecar: {sidecar_path}")

    return {
        "urdfPath": urdf_path,
        "planningGroup": default_planning_group["name"],
        "planningGroups": [planning_group["name"] for planning_group in planning_groups],
        "commands": commands,
        "sidecars": sorted(["explorer.json", *sidecars]),
    }


def _load_motion_envelope(source_path: Path) -> dict[str, object]:
    try:
        spec = importlib.util.spec_from_file_location(f"_robot_motion_{source_path.stem}", source_path)
    except (ImportError, OSError) as exc:
        raise MotionArtifactError(f"Could not load motion source: {_display_path(source_path)}") from exc
    if spec is None or spec.loader is None:
        raise MotionArtifactError(f"Could not load motion source: {_display_path(source_path)}")
    module = importlib.util.module_from_spec(spec)
    inserted_paths: list[str] = []
    for candidate in (str(source_path.parent), str(Path.cwd().resolve())):
        if candidate and candidate not in sys.path:
            sys.path.insert(0, candidate)
            inserted_paths.append(candidate)
    try:
        try:
            spec.loader.exec_module(module)
        except FileNotFoundError as exc:
            raise MotionArtifactError(f"Motion source not found: {_display_path(source_path)}") from exc
        except Exception as exc:
            raise MotionArtifactError(f"Motion source failed to import: {_display_path(source_path)}") from exc
    finally:
        for inserted_path in reversed(inserted_paths):
            try:
                sys.path.remove(inserted_path)
            except ValueError:
                pass
    generator = getattr(module, "gen_motion", None)
    if not callable(generator):
        raise MotionArtifactError(f"{_display_path(source_path)} must define gen_motion()")
    try:
        signature = inspect.signature(generator)
    except (TypeError, ValueError) as exc:
        raise MotionArtifactError("gen_motion() must be introspectable") from exc
    required_params = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    if required_params:
        raise MotionArtifactError("gen_motion() must not require arguments")
    envelope = generator()
    if not isinstance(envelope, dict):
        raise MotionArtifactError("gen_motion() must return an object")
    return envelope


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_relative_file(raw_value: object, *, source_path: Path, suffix: str, label: str) -> Path:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise MotionArtifactError(f"{label} must be a non-empty relative path")
    value = raw_value.strip()
    if "\\" in value:
        raise MotionArtifactError(f"{label} must use POSIX '/' separators")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", "."} for part in pure.parts):
        raise MotionArtifactError(f"{label} must be a relative path")
    path = (source_path.parent / Path(*pure.parts)).resolve()
    if path.suffix.lower() != suffix:
        raise MotionArtifactError(f"{label} must end in {suffix}")
    if not path.is_file():
        raise MotionArtifactError(f"{label} file does not exist: {_display_path(path)}")
    return path


def _read_urdf_robot(urdf_path: Path) -> dict[str, object]:
    try:
        root = ET.parse(urdf_path).getroot()
    except ET.ParseError as exc:
        raise MotionArtifactError(f"URDF is invalid XML: {_display_path(urdf_path)}") from exc
    if root.tag != "robot":
        raise MotionArtifactError("URDF root must be <robot>")
    links = {
        str(link.get("name") or "").strip()
        for link in root.findall("link")
        if str(link.get("name") or "").strip()
    }
    joints: dict[str, dict[str, str]] = {}
    for joint in root.findall("joint"):
        name = str(joint.get("name") or "").strip()
        joint_type = str(joint.get("type") or "").strip()
        parent_element = joint.find("parent")
        child_element = joint.find("child")
        parent = str(parent_element.get("link") if parent_element is not None else "").strip()
        child = str(child_element.get("link") if child_element is not None else "").strip()
        if name:
            joints[name] = {
                "type": joint_type,
                "parent": parent,
                "child": child,
            }
    return {
        "name": str(root.get("name") or "robot").strip() or "robot",
        "links": links,
        "joints": joints,
    }


def _normalize_provider(value: object) -> str:
    provider = _required_string(value, "provider")
    if provider not in SUPPORTED_PROVIDERS:
        raise MotionArtifactError(f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
    return provider


def _required_string(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MotionArtifactError(f"{label} is required")
    return normalized


def _normalize_commands(value: object, *, has_planner: bool) -> list[str]:
    raw_commands = value if value is not None else ["urdf.solvePose", "urdf.planToPose" if has_planner else ""]
    if not isinstance(raw_commands, list):
        raise MotionArtifactError("commands must be an array")
    commands: list[str] = []
    seen = set()
    for raw_command in raw_commands:
        command = str(raw_command or "").strip()
        if not command:
            continue
        if command not in SUPPORTED_COMMANDS:
            raise MotionArtifactError(f"Unsupported command: {command}")
        if command in seen:
            raise MotionArtifactError(f"Duplicate command: {command}")
        seen.add(command)
        commands.append(command)
    if not commands:
        raise MotionArtifactError("commands must include urdf.solvePose")
    if "urdf.planToPose" in commands and "urdf.solvePose" not in commands:
        raise MotionArtifactError("urdf.planToPose requires urdf.solvePose")
    return commands


def _normalize_planning_groups(envelope: dict[str, object], robot: dict[str, object]) -> list[dict[str, object]]:
    raw_groups = envelope.get("planningGroups")
    if raw_groups is None:
        return [
            {
                "name": _required_string(envelope.get("planningGroup"), "planningGroup"),
                "jointNames": _normalize_joint_names(envelope.get("jointNames"), robot),
            }
        ]
    if not isinstance(raw_groups, list) or not raw_groups:
        raise MotionArtifactError("planningGroups must be a non-empty array")
    groups: list[dict[str, object]] = []
    seen = set()
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            raise MotionArtifactError("Each planning group must be an object")
        name = _required_string(raw_group.get("name"), "planningGroup.name")
        if name in seen:
            raise MotionArtifactError(f"Duplicate planning group: {name}")
        seen.add(name)
        groups.append({
            "name": name,
            "jointNames": _normalize_joint_names(
                raw_group.get("jointNames"),
                robot,
                label=f"planningGroup {name} jointNames",
            ),
        })
    return groups


def _normalize_joint_names(value: object, robot: dict[str, object], *, label: str = "jointNames") -> list[str]:
    if not isinstance(value, list) or not value:
        raise MotionArtifactError(f"{label} must be a non-empty array")
    joints = robot["joints"]
    assert isinstance(joints, dict)
    names: list[str] = []
    seen = set()
    for raw_name in value:
        name = str(raw_name or "").strip()
        if not name:
            raise MotionArtifactError(f"{label} cannot include empty names")
        if name in seen:
            raise MotionArtifactError(f"Duplicate joint name in {label}: {name}")
        if name not in joints:
            raise MotionArtifactError(f"{label} references missing joint: {name}")
        if joints[name].get("type") == "fixed":
            raise MotionArtifactError(f"{label} cannot include fixed joint: {name}")
        seen.add(name)
        names.append(name)
    return names


def _normalize_end_effectors(
    value: object,
    robot: dict[str, object],
    planning_groups: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise MotionArtifactError("endEffectors must be a non-empty array")
    links = robot["links"]
    assert isinstance(links, set)
    joint_names_by_group = {
        str(planning_group["name"]): list(planning_group["jointNames"])
        for planning_group in planning_groups
    }
    default_planning_group = str(planning_groups[0]["name"])
    end_effectors: list[dict[str, object]] = []
    seen = set()
    for raw_end_effector in value:
        if not isinstance(raw_end_effector, dict):
            raise MotionArtifactError("Each end effector must be an object")
        name = _required_string(raw_end_effector.get("name"), "endEffector.name")
        if name in seen:
            raise MotionArtifactError(f"Duplicate end effector: {name}")
        link = _required_link(raw_end_effector.get("link"), links, "endEffector.link")
        frame = _required_link(raw_end_effector.get("frame"), links, "endEffector.frame")
        parent_link = str(raw_end_effector.get("parentLink") or "").strip() or _infer_parent_link(robot, link)
        if parent_link not in links:
            raise MotionArtifactError(f"endEffector.parentLink references missing link: {parent_link or '(missing)'}")
        group = str(raw_end_effector.get("group") or "").strip() or name
        planning_group = str(raw_end_effector.get("planningGroup") or default_planning_group).strip()
        if planning_group not in joint_names_by_group:
            raise MotionArtifactError(f"endEffector {name} references missing planningGroup: {planning_group or '(missing)'}")
        if raw_end_effector.get("jointNames") is None:
            joint_names = joint_names_by_group[planning_group]
        else:
            joint_names = _normalize_joint_names(
                raw_end_effector.get("jointNames"),
                robot,
                label=f"endEffector {name} jointNames",
            )
        tolerance = _positive_float(raw_end_effector.get("positionTolerance", 0.002), "endEffector.positionTolerance")
        seen.add(name)
        end_effectors.append({
            "name": name,
            "link": link,
            "frame": frame,
            "parentLink": parent_link,
            "group": group,
            "planningGroup": planning_group,
            "jointNames": joint_names,
            "positionTolerance": tolerance,
        })
    return end_effectors


def _required_link(value: object, links: set[str], label: str) -> str:
    link = _required_string(value, label)
    if link not in links:
        raise MotionArtifactError(f"{label} references missing link: {link}")
    return link


def _infer_parent_link(robot: dict[str, object], child_link: str) -> str:
    joints = robot["joints"]
    assert isinstance(joints, dict)
    for joint in joints.values():
        if joint.get("child") == child_link:
            return str(joint.get("parent") or "")
    return ""


def _normalize_planner(value: object) -> dict[str, object]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise MotionArtifactError("planner must be an object")
    planner = dict(DEFAULT_PLANNER)
    for key in ("pipeline", "plannerId"):
        if value.get(key) is not None:
            planner[key] = _required_string(value.get(key), f"planner.{key}")
    if value.get("planningTime") is not None:
        planner["planningTime"] = _positive_float(value.get("planningTime"), "planner.planningTime")
    return planner


def _positive_float(value: object, label: str) -> float:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise MotionArtifactError(f"{label} must be positive") from exc
    if numeric_value <= 0:
        raise MotionArtifactError(f"{label} must be positive")
    return numeric_value


def _normalize_group_states(value: object, planning_groups: list[dict[str, object]]) -> list[dict[str, object]]:
    joint_names_by_group = {
        str(planning_group["name"]): list(planning_group["jointNames"])
        for planning_group in planning_groups
    }
    if value is None:
        return [
            {
                "name": "home",
                "planningGroup": planning_group_name,
                "jointValuesByNameRad": {name: 0.0 for name in joint_names},
            }
            for planning_group_name, joint_names in joint_names_by_group.items()
        ]
    if not isinstance(value, list) or not value:
        raise MotionArtifactError("groupStates must be a non-empty array")
    states: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for raw_state in value:
        if not isinstance(raw_state, dict):
            raise MotionArtifactError("Each group state must be an object")
        name = _required_string(raw_state.get("name"), "groupState.name")
        planning_group = str(raw_state.get("planningGroup") or "").strip()
        if not planning_group:
            if len(joint_names_by_group) != 1:
                raise MotionArtifactError(f"group state {name} planningGroup is required when multiple planning groups are defined")
            planning_group = next(iter(joint_names_by_group))
        if planning_group not in joint_names_by_group:
            raise MotionArtifactError(f"group state {name} references missing planningGroup: {planning_group}")
        key = (planning_group, name)
        if key in seen:
            raise MotionArtifactError(f"Duplicate group state {name} for planning group {planning_group}")
        joint_values = raw_state.get("jointValuesByNameRad")
        if not isinstance(joint_values, dict):
            raise MotionArtifactError(f"group state {name} jointValuesByNameRad must be an object")
        allowed_joints = set(joint_names_by_group[planning_group])
        normalized_values: dict[str, float] = {}
        for raw_joint_name, raw_value in joint_values.items():
            joint_name = str(raw_joint_name or "").strip()
            if joint_name not in allowed_joints:
                raise MotionArtifactError(f"group state {name} references joint outside planning group: {joint_name}")
            try:
                normalized_values[joint_name] = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise MotionArtifactError(f"group state {name} joint {joint_name} must be numeric radians") from exc
        seen.add(key)
        states.append({
            "name": name,
            "planningGroup": planning_group,
            "jointValuesByNameRad": normalized_values,
        })
    return states


def _collision_pairs(robot: dict[str, object], value: object) -> list[tuple[str, str]]:
    links = robot["links"]
    joints = robot["joints"]
    assert isinstance(links, set)
    assert isinstance(joints, dict)
    pairs = {
        tuple(sorted((str(joint.get("parent") or ""), str(joint.get("child") or ""))))
        for joint in joints.values()
        if str(joint.get("parent") or "").strip() and str(joint.get("child") or "").strip()
    }
    if value is not None:
        if not isinstance(value, list):
            raise MotionArtifactError("disabledCollisionPairs must be an array")
        for raw_pair in value:
            if not isinstance(raw_pair, list) or len(raw_pair) != 2:
                raise MotionArtifactError("disabledCollisionPairs entries must be [link1, link2]")
            left = _required_link(raw_pair[0], links, "disabledCollisionPairs link")
            right = _required_link(raw_pair[1], links, "disabledCollisionPairs link")
            if left == right:
                raise MotionArtifactError("disabledCollisionPairs cannot reference the same link twice")
            pairs.add(tuple(sorted((left, right))))
    return sorted(pairs)


def _motion_explorer_metadata(
    *,
    commands: list[str],
    end_effectors: list[dict[str, object]],
) -> dict[str, object]:
    command_payloads: dict[str, object] = {}
    if "urdf.solvePose" in commands:
        command_payloads["urdf.solvePose"] = {
            "endEffectors": [
                {
                    "name": end_effector["name"],
                    "link": end_effector["link"],
                    "frame": end_effector["frame"],
                    "positionTolerance": end_effector["positionTolerance"],
                }
                for end_effector in end_effectors
            ],
        }
    if "urdf.planToPose" in commands:
        command_payloads["urdf.planToPose"] = {}
    return {
        "schemaVersion": MOTION_EXPLORER_METADATA_SCHEMA_VERSION,
        "kind": MOTION_EXPLORER_METADATA_KIND,
        "motionServer": {
            "version": MOTION_SERVER_VERSION,
            "commands": command_payloads,
        },
    }


def _motion_server_config(
    *,
    provider: str,
    commands: list[str],
    planning_groups: list[dict[str, object]],
    end_effectors: list[dict[str, object]],
    planner: dict[str, object],
) -> dict[str, object]:
    default_planning_group = planning_groups[0]
    command_payloads: dict[str, object] = {}
    if "urdf.solvePose" in commands:
        command_payloads["urdf.solvePose"] = {
            "planningGroup": default_planning_group["name"],
            "jointNames": default_planning_group["jointNames"],
            "endEffectors": [
                {
                    "name": end_effector["name"],
                    "link": end_effector["link"],
                    "frame": end_effector["frame"],
                    "planningGroup": end_effector["planningGroup"],
                    "jointNames": end_effector["jointNames"],
                    "positionTolerance": end_effector["positionTolerance"],
                }
                for end_effector in end_effectors
            ],
        }
    if "urdf.planToPose" in commands:
        command_payloads["urdf.planToPose"] = {
            "planningGroup": default_planning_group["name"],
            "planner": planner,
        }
    return {
        "schemaVersion": MOTION_CONFIG_SCHEMA_VERSION,
        "kind": MOTION_CONFIG_KIND,
        "provider": provider,
        "commands": command_payloads,
    }


def _moveit_srdf(
    *,
    robot_name: str,
    planning_groups: list[dict[str, object]],
    end_effectors: list[dict[str, object]],
    group_states: list[dict[str, object]],
    disabled_pairs: list[tuple[str, str]],
) -> str:
    root = ET.Element("robot", {"name": robot_name})
    for planning_group in planning_groups:
        planning_group_element = ET.SubElement(root, "group", {"name": str(planning_group["name"])})
        joint_names = planning_group["jointNames"]
        assert isinstance(joint_names, list)
        for joint_name in joint_names:
            ET.SubElement(planning_group_element, "joint", {"name": str(joint_name)})
    for state in group_states:
        planning_group_name = str(state["planningGroup"])
        state_element = ET.SubElement(root, "group_state", {"name": str(state["name"]), "group": planning_group_name})
        joint_values = state["jointValuesByNameRad"]
        assert isinstance(joint_values, dict)
        planning_group = next(
            (group for group in planning_groups if str(group["name"]) == planning_group_name),
            None,
        )
        if planning_group is None:
            raise MotionArtifactError(f"group state {state['name']} references missing planning group {planning_group_name}")
        joint_names = planning_group["jointNames"]
        assert isinstance(joint_names, list)
        for joint_name in joint_names:
            ET.SubElement(state_element, "joint", {
                "name": str(joint_name),
                "value": _format_float(float(joint_values.get(str(joint_name), 0.0))),
            })
    for end_effector in end_effectors:
        group_name = str(end_effector["group"])
        group_element = ET.SubElement(root, "group", {"name": group_name})
        ET.SubElement(group_element, "link", {"name": str(end_effector["link"])})
        ET.SubElement(root, "end_effector", {
            "name": str(end_effector["name"]),
            "parent_link": str(end_effector["parentLink"]),
            "group": group_name,
            "parent_group": str(end_effector["planningGroup"]),
        })
    for link1, link2 in disabled_pairs:
        ET.SubElement(root, "disable_collisions", {
            "link1": link1,
            "link2": link2,
            "reason": "Adjacent",
        })
    ET.indent(root, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode")


def _planning_pipelines(planner: dict[str, object]) -> dict[str, object]:
    pipeline = str(planner.get("pipeline") or DEFAULT_PLANNER["pipeline"])
    planner_id = str(planner.get("plannerId") or DEFAULT_PLANNER["plannerId"])
    planning_time = float(planner.get("planningTime") or DEFAULT_PLANNER["planningTime"])
    request_params = {
        "planning_attempts": 1,
        "planning_pipeline": pipeline,
        "planner_id": planner_id,
        "planning_time": planning_time,
        "max_velocity_scaling_factor": 1.0,
        "max_acceleration_scaling_factor": 1.0,
    }
    return {
        "planning_pipelines": {
            "pipeline_names": [pipeline],
        },
        pipeline: {
            "planning_plugin": "ompl_interface/OMPLPlanner",
            "planning_plugins": ["ompl_interface/OMPLPlanner"],
            "request_adapters": [
                "default_planning_request_adapters/ResolveConstraintFrames",
                "default_planning_request_adapters/ValidateWorkspaceBounds",
                "default_planning_request_adapters/CheckStartStateBounds",
                "default_planning_request_adapters/CheckStartStateCollision",
            ],
            "start_state_max_bounds_error": 0.1,
        },
        "plan_request_params": request_params,
        "ompl_rrtc": {
            "plan_request_params": request_params,
        },
    }


def _format_float(value: float) -> str:
    text = f"{float(value):.12g}"
    return "0" if text == "-0" else text


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
