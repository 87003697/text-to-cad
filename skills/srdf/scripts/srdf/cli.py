from __future__ import annotations

import argparse
import sys
import warnings
from collections.abc import Sequence
from math import isfinite
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(SCRIPTS_DIR))

from srdf.source import SrdfSource, SrdfSourceError, read_srdf_source

SRDF_SUFFIX = ".srdf"
URDF_SUFFIX = ".urdf"


def validate_srdf_targets(targets: Sequence[str]) -> int:
    target_paths = [_resolve_target_path(target) for target in targets]
    failed = False
    for target_path in target_paths:
        if not _validate_target(target_path):
            failed = True
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate",
        description="Validate explicit MoveIt2 SRDF targets against their linked URDF.",
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Explicit .srdf file to validate.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    return validate_srdf_targets(args.targets)


def _resolve_target_path(raw_target: object) -> Path:
    value = str(raw_target or "").strip()
    if not value:
        raise ValueError("srdf target must be a non-empty path")
    target_path = Path(value).expanduser()
    return target_path.resolve() if target_path.is_absolute() else (Path.cwd() / target_path).resolve()


def _validate_target(target_path: Path) -> bool:
    display = _display_path(target_path)
    if target_path.suffix.lower() != SRDF_SUFFIX:
        print(f"FAIL {display}: target must be a .srdf file", file=sys.stderr)
        return False
    if not target_path.is_file():
        print(f"FAIL {display}: file not found", file=sys.stderr)
        return False
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        try:
            srdf_source = read_srdf_source(target_path)
            urdf_path = _resolve_linked_urdf_path(srdf_source, srdf_path=target_path)
            urdf_robot = _read_urdf_robot(urdf_path)
            _validate_srdf_against_urdf(srdf_source, urdf_robot=urdf_robot)
        except (SrdfSourceError, ValueError, FileNotFoundError) as exc:
            print(f"FAIL {display}: {exc}", file=sys.stderr)
            return False
    for caught in caught_warnings:
        print(f"WARN {display}: {caught.message}", file=sys.stderr)
    print(_summary_line(display, srdf_source, urdf_path))
    return True


def _summary_line(display: str, srdf_source: SrdfSource, urdf_path: Path) -> str:
    return (
        f"OK {display}: robot {srdf_source.robot_name!r}, urdf {_display_path(urdf_path)}, "
        f"{len(srdf_source.planning_groups)} groups, {len(srdf_source.end_effectors)} end effectors, "
        f"{len(srdf_source.group_states)} group states, "
        f"{len(srdf_source.disabled_collision_pairs)} disabled collision pairs"
    )


def _resolve_linked_urdf_path(srdf_source: SrdfSource, *, srdf_path: Path) -> Path:
    raw_value = str(srdf_source.urdf_ref or "").strip()
    if not raw_value:
        raise SrdfSourceError('SRDF must include <tcad:urdf path="..."/> metadata')
    if "\\" in raw_value:
        raise SrdfSourceError("SRDF tcad:urdf path must use POSIX '/' separators")
    pure = PurePosixPath(raw_value)
    if pure.is_absolute() or any(part in {"", "."} for part in pure.parts):
        raise SrdfSourceError(f"SRDF tcad:urdf path must be a relative path: {raw_value!r}")
    urdf_path = (srdf_path.parent / Path(*pure.parts)).resolve()
    if urdf_path.suffix.lower() != URDF_SUFFIX:
        raise SrdfSourceError(f"SRDF tcad:urdf path must end in .urdf: {raw_value!r}")
    if not urdf_path.is_file():
        raise SrdfSourceError(f"SRDF tcad:urdf file does not exist: {raw_value!r}")
    return urdf_path


def _read_urdf_robot(urdf_path: Path) -> dict[str, object]:
    try:
        root = ET.parse(urdf_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"URDF is invalid XML: {_display_path(urdf_path)}") from exc
    if root.tag != "robot":
        raise ValueError("URDF root must be <robot>")
    robot_name = str(root.get("name") or "").strip()
    links = {
        str(link.get("name") or "").strip()
        for link in root.findall("link")
        if str(link.get("name") or "").strip()
    }
    joints: dict[str, dict[str, object]] = {}
    for joint in root.findall("joint"):
        name = str(joint.get("name") or "").strip()
        if not name:
            continue
        parent_element = joint.find("parent")
        child_element = joint.find("child")
        joint_type = str(joint.get("type") or "").strip()
        lower: float | None = None
        upper: float | None = None
        limit_element = joint.find("limit")
        if limit_element is not None and joint_type in {"revolute", "prismatic"}:
            lower = _optional_finite_float(limit_element.get("lower"))
            upper = _optional_finite_float(limit_element.get("upper"))
        joints[name] = {
            "type": joint_type,
            "parent": str(parent_element.get("link") if parent_element is not None else "").strip(),
            "child": str(child_element.get("link") if child_element is not None else "").strip(),
            "lower": lower,
            "upper": upper,
            "mimic": joint.find("mimic") is not None,
        }
    if not robot_name:
        raise ValueError("URDF robot name is required")
    return {"name": robot_name, "links": links, "joints": joints}


def _validate_srdf_against_urdf(srdf_source: SrdfSource, *, urdf_robot: dict[str, object]) -> None:
    urdf_name = str(urdf_robot["name"])
    if srdf_source.robot_name != urdf_name:
        raise SrdfSourceError(f"SRDF robot name {srdf_source.robot_name!r} must match URDF robot name {urdf_name!r}")
    links = urdf_robot["links"]
    joints = urdf_robot["joints"]
    assert isinstance(links, set)
    assert isinstance(joints, dict)
    group_names = {group.name for group in srdf_source.planning_groups}
    groups_by_name = {group.name: group for group in srdf_source.planning_groups}
    if not group_names:
        raise SrdfSourceError("SRDF must define at least one planning group")

    for group in srdf_source.planning_groups:
        if not group.joint_names and not group.link_names and not group.chains and not group.subgroups:
            raise SrdfSourceError(f"SRDF planning group {group.name!r} must define joints, links, chains, or subgroups")
        _validate_names_exist(group.joint_names, set(joints), label=f"planning group {group.name!r} joint")
        _validate_names_exist(group.link_names, links, label=f"planning group {group.name!r} link")
        _validate_names_exist(group.subgroups, group_names, label=f"planning group {group.name!r} subgroup")
        for chain in group.chains:
            if chain.base_link not in links:
                raise SrdfSourceError(f"planning group {group.name!r} chain references missing base_link {chain.base_link!r}")
            if chain.tip_link not in links:
                raise SrdfSourceError(f"planning group {group.name!r} chain references missing tip_link {chain.tip_link!r}")
            if not _joint_path_for_chain(urdf_robot, base_link=chain.base_link, tip_link=chain.tip_link):
                raise SrdfSourceError(
                    f"planning group {group.name!r} chain {chain.base_link!r} -> {chain.tip_link!r} "
                    "is not a parent-to-child path in the URDF tree"
                )

    for end_effector in srdf_source.end_effectors:
        if end_effector.parent_link not in links:
            raise SrdfSourceError(f"end_effector {end_effector.name!r} references missing parent_link {end_effector.parent_link!r}")
        if end_effector.group not in group_names:
            raise SrdfSourceError(f"end_effector {end_effector.name!r} references missing group {end_effector.group!r}")
        if end_effector.parent_group and end_effector.parent_group not in group_names:
            raise SrdfSourceError(
                f"end_effector {end_effector.name!r} references missing parent_group {end_effector.parent_group!r}"
            )
        _validate_end_effector_topology(
            end_effector,
            groups_by_name=groups_by_name,
            urdf_robot=urdf_robot,
        )

    for group_state in srdf_source.group_states:
        if group_state.group not in group_names:
            raise SrdfSourceError(f"group_state {group_state.name!r} references missing group {group_state.group!r}")
        group_joint_names = set(_joint_names_for_group(groups_by_name[group_state.group], urdf_robot=urdf_robot, groups_by_name=groups_by_name))
        for joint_name, value in group_state.joint_values_by_name.items():
            if joint_name not in joints:
                raise SrdfSourceError(f"group_state {group_state.name!r} joint references missing name {joint_name!r}")
            if joint_name not in group_joint_names:
                raise SrdfSourceError(
                    f"group_state {group_state.name!r} joint {joint_name!r} is not in group {group_state.group!r}"
                )
            _validate_group_state_joint_value(group_state.name, joint_name, value, joints[joint_name])

    for pair in srdf_source.disabled_collision_pairs:
        _validate_names_exist((pair.link1, pair.link2), links, label="disable_collisions link")
    _warn_on_many_manual_disabled_pairs(srdf_source.disabled_collision_pairs)


def _optional_finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _joint_path_for_chain(urdf_robot: dict[str, object], *, base_link: str, tip_link: str) -> list[str]:
    if not base_link or not tip_link or base_link == tip_link:
        return []
    joints = urdf_robot.get("joints")
    if not isinstance(joints, dict):
        return []
    by_parent: dict[str, list[tuple[str, dict[str, object]]]] = {}
    for joint_name, joint in joints.items():
        if not isinstance(joint, dict):
            continue
        parent = str(joint.get("parent") or "").strip()
        child = str(joint.get("child") or "").strip()
        if parent and child:
            by_parent.setdefault(parent, []).append((str(joint_name), joint))

    stack: list[tuple[str, list[str]]] = [(base_link, [])]
    visited: set[str] = set()
    while stack:
        link_name, path = stack.pop()
        if link_name == tip_link:
            return path
        if link_name in visited:
            continue
        visited.add(link_name)
        for joint_name, joint in reversed(by_parent.get(link_name, [])):
            child = str(joint.get("child") or "").strip()
            if child:
                stack.append((child, [*path, joint_name]))
    return []


def _joint_names_for_group(
    group: object,
    *,
    urdf_robot: dict[str, object],
    groups_by_name: dict[str, object],
    visited: set[str] | None = None,
) -> list[str]:
    names: list[str] = []
    joints = urdf_robot.get("joints")
    if not isinstance(joints, dict):
        return names

    for joint_name in getattr(group, "joint_names", ()):
        joint = joints.get(joint_name)
        if isinstance(joint, dict) and str(joint.get("type") or "") != "fixed" and not bool(joint.get("mimic")):
            _append_unique(names, [joint_name])

    for chain in getattr(group, "chains", ()):
        chain_joint_names = []
        for joint_name in _joint_path_for_chain(
            urdf_robot,
            base_link=str(getattr(chain, "base_link", "") or ""),
            tip_link=str(getattr(chain, "tip_link", "") or ""),
        ):
            joint = joints.get(joint_name)
            if isinstance(joint, dict) and str(joint.get("type") or "") != "fixed" and not bool(joint.get("mimic")):
                chain_joint_names.append(joint_name)
        _append_unique(names, chain_joint_names)

    if visited is None:
        visited = set()
    group_name = str(getattr(group, "name", "") or "")
    if group_name:
        visited.add(group_name)
    for subgroup_name in getattr(group, "subgroups", ()):
        subgroup_key = str(subgroup_name or "").strip()
        if not subgroup_key or subgroup_key in visited:
            continue
        subgroup = groups_by_name.get(subgroup_key)
        if subgroup is not None:
            _append_unique(
                names,
                _joint_names_for_group(
                    subgroup,
                    urdf_robot=urdf_robot,
                    groups_by_name=groups_by_name,
                    visited=visited,
                ),
            )
    return names


def _link_names_for_group(
    group: object,
    *,
    urdf_robot: dict[str, object],
    groups_by_name: dict[str, object],
    visited: set[str] | None = None,
) -> set[str]:
    links = set(str(link_name) for link_name in getattr(group, "link_names", ()))
    joints = urdf_robot.get("joints")
    if not isinstance(joints, dict):
        return links

    def add_joint_links(joint_name: str) -> None:
        joint = joints.get(joint_name)
        if not isinstance(joint, dict):
            return
        child = str(joint.get("child") or "").strip()
        if child:
            links.add(child)

    for joint_name in getattr(group, "joint_names", ()):
        add_joint_links(str(joint_name))
    for chain in getattr(group, "chains", ()):
        links.add(str(getattr(chain, "tip_link", "") or ""))
        for joint_name in _joint_path_for_chain(
            urdf_robot,
            base_link=str(getattr(chain, "base_link", "") or ""),
            tip_link=str(getattr(chain, "tip_link", "") or ""),
        ):
            add_joint_links(joint_name)

    if visited is None:
        visited = set()
    group_name = str(getattr(group, "name", "") or "")
    if group_name:
        visited.add(group_name)
    for subgroup_name in getattr(group, "subgroups", ()):
        subgroup_key = str(subgroup_name or "").strip()
        if not subgroup_key or subgroup_key in visited:
            continue
        subgroup = groups_by_name.get(subgroup_key)
        if subgroup is not None:
            links.update(
                _link_names_for_group(
                    subgroup,
                    urdf_robot=urdf_robot,
                    groups_by_name=groups_by_name,
                    visited=visited,
                )
            )
    links.discard("")
    return links


def _joint_adjacent_to_any_link(urdf_robot: dict[str, object], parent_link: str, child_links: set[str]) -> bool:
    joints = urdf_robot.get("joints")
    if not isinstance(joints, dict):
        return False
    for joint in joints.values():
        if not isinstance(joint, dict):
            continue
        parent = str(joint.get("parent") or "").strip()
        child = str(joint.get("child") or "").strip()
        if (parent == parent_link and child in child_links) or (child == parent_link and parent in child_links):
            return True
    return False


def _validate_end_effector_topology(
    end_effector: object,
    *,
    groups_by_name: dict[str, object],
    urdf_robot: dict[str, object],
) -> None:
    group_name = str(getattr(end_effector, "group", "") or "")
    parent_group_name = str(getattr(end_effector, "parent_group", "") or "")
    parent_link = str(getattr(end_effector, "parent_link", "") or "")
    if not group_name or group_name not in groups_by_name:
        return

    end_effector_links = _link_names_for_group(
        groups_by_name[group_name],
        urdf_robot=urdf_robot,
        groups_by_name=groups_by_name,
    )
    if parent_group_name and parent_group_name in groups_by_name:
        parent_group_links = _link_names_for_group(
            groups_by_name[parent_group_name],
            urdf_robot=urdf_robot,
            groups_by_name=groups_by_name,
        )
        overlap = sorted(end_effector_links & parent_group_links)
        if overlap:
            raise SrdfSourceError(
                f"end_effector {getattr(end_effector, 'name', '')!r} group shares link(s) with parent_group: {overlap!r}"
            )
        if parent_link and parent_link not in parent_group_links:
            raise SrdfSourceError(
                f"end_effector {getattr(end_effector, 'name', '')!r} parent_link {parent_link!r} is not in parent_group {parent_group_name!r}"
            )
    if end_effector_links and parent_link not in end_effector_links and not _joint_adjacent_to_any_link(
        urdf_robot,
        parent_link,
        end_effector_links,
    ):
        raise SrdfSourceError(
            f"end_effector {getattr(end_effector, 'name', '')!r} parent_link {parent_link!r} is not adjacent to its group"
        )


def _validate_group_state_joint_value(
    state_name: str,
    joint_name: str,
    value: float,
    joint: object,
) -> None:
    if not isinstance(joint, dict):
        return
    joint_type = str(joint.get("type") or "").strip()
    if joint_type == "fixed":
        raise SrdfSourceError(f"group_state {state_name!r} cannot set fixed joint {joint_name!r}")
    if bool(joint.get("mimic")):
        raise SrdfSourceError(f"group_state {state_name!r} cannot set mimic joint {joint_name!r}")
    if joint_type == "continuous":
        return
    lower = joint.get("lower")
    upper = joint.get("upper")
    if isinstance(lower, float) and value < lower:
        raise SrdfSourceError(f"group_state {state_name!r} joint {joint_name!r} is below its URDF lower limit")
    if isinstance(upper, float) and value > upper:
        raise SrdfSourceError(f"group_state {state_name!r} joint {joint_name!r} is above its URDF upper limit")


def _append_unique(target: list[str], values: list[str]) -> None:
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)


def _warn_on_many_manual_disabled_pairs(pairs: object) -> None:
    manual_count = sum(1 for pair in pairs if getattr(pair, "source", "") == "manual")
    if manual_count >= 25:
        warnings.warn(
            f"SRDF contains {manual_count} manually reasoned disabled collision pairs; prefer sampled/setup-assistant provenance.",
            stacklevel=3,
        )


def _validate_names_exist(names: object, allowed: set[str], *, label: str) -> None:
    for name in names:
        if name not in allowed:
            raise SrdfSourceError(f"{label} references missing name {name!r}")


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
