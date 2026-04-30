from __future__ import annotations

import json
import posixpath
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from motion_server.protocol import MotionProtocolError, SUPPORTED_REQUEST_TYPES


DEFAULT_CAD_DIRECTORY = ""
MOTION_SERVER_VERSION = 1
MOTION_EXPLORER_METADATA_KIND = "texttocad-robot-motion-explorer"
MOTION_EXPLORER_METADATA_SCHEMA_VERSION = 1
MOTION_CONFIG_KIND = "texttocad-robot-motion-server"
MOTION_CONFIG_SCHEMA_VERSION = 1
MOTION_SERVER_PROVIDERS = {"moveit_py", "fake"}


def _plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _path_is_inside(child_path: Path, parent_path: Path) -> bool:
    try:
        child_path.resolve().relative_to(parent_path.resolve())
        return True
    except ValueError:
        return False


def _file_version(file_path: Path) -> str:
    try:
        stats = file_path.stat()
    except FileNotFoundError:
        return ""
    if not stats.st_mode:
        return ""
    return f"{stats.st_size:x}-{stats.st_mtime_ns:x}"


def _combined_version(paths: list[Path]) -> str:
    return "|".join(f"{path.name}:{_file_version(path)}" for path in paths)


def _read_json_object(file_path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MotionProtocolError(f"{label} is missing: {file_path}") from exc
    except json.JSONDecodeError as exc:
        raise MotionProtocolError(f"{label} is invalid JSON: {file_path}") from exc
    if not _plain_object(payload):
        raise MotionProtocolError(f"{label} must be an object")
    return payload


def _normalize_relative_path(value: Any, *, label: str, suffix: str | None = None) -> str:
    raw_value = str(value or "").strip().replace("\\", "/").lstrip("/")
    normalized = posixpath.normpath(raw_value)
    if not raw_value or normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise MotionProtocolError(f"{label} must stay inside the repository: {raw_value or '(missing)'}")
    if suffix and not normalized.lower().endswith(suffix):
        raise MotionProtocolError(f"{label} must end in {suffix}: {normalized}")
    return normalized


def normalize_cad_directory(value: Any = DEFAULT_CAD_DIRECTORY) -> str:
    if value is None:
        value = DEFAULT_CAD_DIRECTORY
    raw_value = str(value).strip()
    if not raw_value:
        return ""
    return _normalize_relative_path(raw_value, label="dir")


def normalize_file_ref(value: Any) -> str:
    return _normalize_relative_path(value, label="file", suffix=".urdf")


def _file_ref_relative_to_cad_dir(file_ref: str, *, cad_dir: str, cad_root: Path) -> str:
    if not cad_dir:
        return file_ref
    prefix = f"{cad_dir.rstrip('/')}/"
    if not file_ref.startswith(prefix):
        return file_ref
    scan_relative_ref = file_ref[len(prefix):]
    if scan_relative_ref and not (cad_root / file_ref).is_file() and (cad_root / scan_relative_ref).is_file():
        return scan_relative_ref
    return file_ref


def _urdf_link_names(urdf_path: Path) -> set[str]:
    try:
        root = ET.parse(urdf_path).getroot()
    except FileNotFoundError as exc:
        raise MotionProtocolError(f"URDF file does not exist: {urdf_path}") from exc
    except ET.ParseError as exc:
        raise MotionProtocolError(f"URDF file is invalid XML: {urdf_path}") from exc
    return {
        str(link.get("name") or "").strip()
        for link in root.findall("link")
        if str(link.get("name") or "").strip()
    }


def _validate_provider(value: Any) -> str:
    provider = str(value or "").strip()
    if not provider:
        raise MotionProtocolError("motion server config provider is required")
    if provider not in MOTION_SERVER_PROVIDERS:
        raise MotionProtocolError(f"motion server config provider {provider} is unsupported")
    return provider


def _validate_planning_group(value: Any, command_name: str) -> str:
    planning_group = str(value or "").strip()
    if not planning_group:
        raise MotionProtocolError(f"motion server command {command_name} planningGroup is required")
    return planning_group


def _validate_joint_names(value: Any, command_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MotionProtocolError(f"motion server command {command_name} jointNames must be a non-empty array")
    names: list[str] = []
    seen = set()
    for raw_name in value:
        name = str(raw_name or "").strip()
        if not name:
            raise MotionProtocolError(f"motion server command {command_name} jointNames cannot include empty names")
        if name in seen:
            raise MotionProtocolError(f"motion server command {command_name} jointNames includes duplicate {name}")
        seen.add(name)
        names.append(name)
    return names


def _validate_end_effectors(
    value: Any,
    link_names: set[str],
    command_name: str,
    *,
    allow_solver_fields: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MotionProtocolError(f"motion server command {command_name} endEffectors must be a non-empty array")
    end_effectors: list[dict[str, Any]] = []
    seen = set()
    for raw_end_effector in value:
        if not _plain_object(raw_end_effector):
            raise MotionProtocolError(f"motion server command {command_name} end effector must be an object")
        name = str(raw_end_effector.get("name") or "").strip()
        if not name:
            raise MotionProtocolError(f"motion server command {command_name} end effector name is required")
        if name in seen:
            raise MotionProtocolError(f"Duplicate motion server end effector name: {name}")
        seen.add(name)
        link = str(raw_end_effector.get("link") or "").strip()
        if not link or link not in link_names:
            raise MotionProtocolError(f"motion server end effector {name} references missing link {link or '(missing)'}")
        frame = str(raw_end_effector.get("frame") or "").strip()
        if not frame or frame not in link_names:
            raise MotionProtocolError(f"motion server end effector {name} references missing frame {frame or '(missing)'}")
        position_tolerance = raw_end_effector.get("positionTolerance", 0.002)
        try:
            position_tolerance = float(position_tolerance)
        except (TypeError, ValueError) as exc:
            raise MotionProtocolError(f"motion server end effector {name} positionTolerance must be positive") from exc
        if position_tolerance <= 0:
            raise MotionProtocolError(f"motion server end effector {name} positionTolerance must be positive")
        parsed_end_effector = {
            "name": name,
            "link": link,
            "frame": frame,
            "positionTolerance": position_tolerance,
        }
        if allow_solver_fields:
            raw_planning_group = raw_end_effector.get("planningGroup")
            raw_joint_names = raw_end_effector.get("jointNames")
            has_planning_group = raw_planning_group is not None
            has_joint_names = raw_joint_names is not None
            if has_planning_group != has_joint_names:
                raise MotionProtocolError(
                    f"motion server end effector {name} must include both planningGroup and jointNames"
                )
            if has_planning_group:
                planning_group = str(raw_planning_group or "").strip()
                if not planning_group:
                    raise MotionProtocolError(f"motion server end effector {name} planningGroup is required")
                parsed_end_effector["planningGroup"] = planning_group
                parsed_end_effector["jointNames"] = _validate_joint_names(
                    raw_joint_names,
                    f"{command_name} end effector {name}",
                )
        end_effectors.append(parsed_end_effector)
    return end_effectors


def _validate_planner(value: Any, command_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not _plain_object(value):
        raise MotionProtocolError(f"motion server command {command_name} planner must be an object")
    planner: dict[str, Any] = {}
    for key in ("pipeline", "plannerId"):
        if value.get(key) is None:
            continue
        normalized = str(value.get(key) or "").strip()
        if not normalized:
            raise MotionProtocolError(f"motion server command {command_name} planner.{key} cannot be empty")
        planner[key] = normalized
    if value.get("planningTime") is not None:
        try:
            planning_time = float(value.get("planningTime"))
        except (TypeError, ValueError) as exc:
            raise MotionProtocolError(f"motion server command {command_name} planner.planningTime must be positive") from exc
        if planning_time <= 0:
            raise MotionProtocolError(f"motion server command {command_name} planner.planningTime must be positive")
        planner["planningTime"] = planning_time
    return planner


def _validate_explorer_command(
    command_name: str,
    command: Any,
    *,
    link_names: set[str],
) -> dict[str, Any]:
    if command_name not in SUPPORTED_REQUEST_TYPES:
        raise MotionProtocolError(f"robot motion explorer metadata motionServer command {command_name} is unsupported")
    if not _plain_object(command):
        raise MotionProtocolError(f"robot motion explorer metadata motionServer command {command_name} must be an object")
    if command_name == "urdf.solvePose":
        return {
            "endEffectors": _validate_end_effectors(command.get("endEffectors"), link_names, command_name),
        }
    if command:
        key = sorted(command)[0]
        raise MotionProtocolError(f"robot motion explorer metadata motionServer command {command_name} cannot include {key}")
    return {}


def _validate_server_command(
    command_name: str,
    command: Any,
    *,
    link_names: set[str],
) -> dict[str, Any]:
    if command_name not in SUPPORTED_REQUEST_TYPES:
        raise MotionProtocolError(f"motion server command {command_name} is unsupported")
    if not _plain_object(command):
        raise MotionProtocolError(f"motion server command {command_name} must be an object")
    parsed = {
        "planningGroup": _validate_planning_group(command.get("planningGroup"), command_name),
    }
    if command_name == "urdf.solvePose":
        parsed["jointNames"] = _validate_joint_names(command.get("jointNames"), command_name)
        parsed["endEffectors"] = _validate_end_effectors(
            command.get("endEffectors"),
            link_names,
            command_name,
            allow_solver_fields=True,
        )
    if command_name == "urdf.planToPose":
        parsed["planner"] = _validate_planner(command.get("planner"), command_name)
    return parsed


def _validate_motion_explorer_metadata(metadata: dict[str, Any], command_name: str, *, link_names: set[str]) -> dict[str, Any]:
    if int(metadata.get("schemaVersion") or 0) != MOTION_EXPLORER_METADATA_SCHEMA_VERSION:
        raise MotionProtocolError(f"robot motion explorer metadata schemaVersion must be {MOTION_EXPLORER_METADATA_SCHEMA_VERSION}")
    if str(metadata.get("kind") or "") != MOTION_EXPLORER_METADATA_KIND:
        raise MotionProtocolError(f"robot motion explorer metadata kind must be {MOTION_EXPLORER_METADATA_KIND}")
    motion_server = metadata.get("motionServer")
    if not _plain_object(motion_server):
        raise MotionProtocolError("robot motion explorer metadata does not advertise motionServer commands")
    if int(motion_server.get("version") or 0) != MOTION_SERVER_VERSION:
        raise MotionProtocolError(f"robot motion explorer metadata motionServer.version must be {MOTION_SERVER_VERSION}")
    commands = motion_server.get("commands")
    if not _plain_object(commands) or not commands:
        raise MotionProtocolError("robot motion explorer metadata motionServer.commands must be a non-empty object")
    parsed_commands = {
        name: _validate_explorer_command(name, command, link_names=link_names)
        for name, command in commands.items()
    }
    if "urdf.planToPose" in parsed_commands and "urdf.solvePose" not in parsed_commands:
        raise MotionProtocolError("robot motion explorer metadata motionServer command urdf.planToPose requires urdf.solvePose")
    if command_name not in parsed_commands:
        raise MotionProtocolError(f"robot motion explorer metadata does not implement motionServer command {command_name}")
    return {
        "version": MOTION_SERVER_VERSION,
        "commands": parsed_commands,
    }


def _validate_motion_config(config: dict[str, Any], command_name: str, *, link_names: set[str]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if int(config.get("schemaVersion") or 0) != MOTION_CONFIG_SCHEMA_VERSION:
        raise MotionProtocolError(f"motion server config schemaVersion must be {MOTION_CONFIG_SCHEMA_VERSION}")
    if str(config.get("kind") or "") != MOTION_CONFIG_KIND:
        raise MotionProtocolError(f"motion server config kind must be {MOTION_CONFIG_KIND}")
    provider = _validate_provider(config.get("provider"))
    commands = config.get("commands")
    if not _plain_object(commands) or not commands:
        raise MotionProtocolError("motion server config commands must be a non-empty object")
    parsed_commands = {
        name: _validate_server_command(name, command, link_names=link_names)
        for name, command in commands.items()
    }
    if "urdf.planToPose" in parsed_commands and "urdf.solvePose" not in parsed_commands:
        raise MotionProtocolError("motion server config command urdf.planToPose requires urdf.solvePose")
    command = parsed_commands.get(command_name)
    if not command:
        raise MotionProtocolError(f"motion server config does not implement command {command_name}")
    return provider, {
        "schemaVersion": MOTION_CONFIG_SCHEMA_VERSION,
        "kind": MOTION_CONFIG_KIND,
        "provider": provider,
        "commands": parsed_commands,
    }, command


def build_motion_context(*, repo_root: str | Path, dir: Any = DEFAULT_CAD_DIRECTORY, file: Any, type: Any) -> dict[str, Any]:
    command_name = str(type or "").strip()
    if command_name not in SUPPORTED_REQUEST_TYPES:
        raise MotionProtocolError(f"Unsupported request type {command_name or '(missing)'}")
    resolved_repo_root = Path(repo_root).resolve()
    cad_dir = normalize_cad_directory(dir)
    file_ref = normalize_file_ref(file)
    cad_root = (resolved_repo_root / cad_dir).resolve()
    if not _path_is_inside(cad_root, resolved_repo_root):
        raise MotionProtocolError(f"dir must stay inside the repository: {cad_dir}")
    file_ref = _file_ref_relative_to_cad_dir(file_ref, cad_dir=cad_dir, cad_root=cad_root)
    urdf_path = (cad_root / file_ref).resolve()
    if not _path_is_inside(urdf_path, cad_root):
        root_label = cad_dir or "the repository"
        raise MotionProtocolError(f"file must stay inside {root_label}: {file_ref}")
    if not urdf_path.is_file():
        raise MotionProtocolError(f"URDF file does not exist: {file_ref}")
    explorer_dir = urdf_path.parent / f".{urdf_path.name}"
    motion_dir = explorer_dir / "robot-motion"
    motion_explorer_metadata_path = motion_dir / "explorer.json"
    motion_config_path = motion_dir / "motion_server.json"
    metadata = _read_json_object(motion_explorer_metadata_path, label="robot motion explorer metadata")
    link_names = _urdf_link_names(urdf_path)
    motion_server = _validate_motion_explorer_metadata(
        metadata,
        command_name,
        link_names=link_names,
    )
    motion_config_payload = _read_json_object(motion_config_path, label="motion server config")
    provider, motion_config, command = _validate_motion_config(
        motion_config_payload,
        command_name,
        link_names=link_names,
    )
    sidecar_dir = motion_dir
    version_paths = [urdf_path, motion_explorer_metadata_path, motion_config_path]
    version_paths.extend(sorted(path for path in sidecar_dir.glob("moveit2_*") if path.is_file()))
    return {
        "repoRoot": str(resolved_repo_root),
        "dir": cad_dir,
        "file": file_ref,
        "urdfPath": str(urdf_path),
        "explorerMetadataPath": str(motion_explorer_metadata_path),
        "motionExplorerMetadataPath": str(motion_explorer_metadata_path),
        "motionConfigPath": str(motion_config_path),
        "explorerMetadataHash": _combined_version(version_paths),
        "commandName": command_name,
        "provider": provider,
        "command": command,
        "motionServer": motion_server,
        "motionConfig": motion_config,
        "sidecarDir": str(sidecar_dir),
    }
