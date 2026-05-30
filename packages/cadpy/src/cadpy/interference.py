from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import json
import math
from pathlib import Path
import time
from typing import Any

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS_Compound

from cadpy.render import relative_to_repo
from cadpy.step_scene import (
    LoadedStepScene,
    OccurrenceNode,
    _bbox_from_shape,
    load_step_scene,
    load_step_scene_cached,
    occurrence_selector_id,
    scene_occurrence_shape,
    step_file_hash,
)


INTERFERENCE_SCHEMA_VERSION = 1
DEFAULT_CLEARANCE_MM = 0.0
DEFAULT_CONTACT_TOLERANCE_MM = 1.0e-4
DEFAULT_COLLISION_VOLUME_TOLERANCE_MM3 = 1.0e-9
DEFAULT_MAX_PAIRS = 1000


@dataclass(frozen=True)
class InterferenceOptions:
    clearance_mm: float = DEFAULT_CLEARANCE_MM
    contact_tolerance_mm: float = DEFAULT_CONTACT_TOLERANCE_MM
    collision_volume_tolerance_mm3: float = DEFAULT_COLLISION_VOLUME_TOLERANCE_MM3
    max_pairs: int = DEFAULT_MAX_PAIRS
    time_budget_ms: float | None = None
    body_depth: int | None = None
    collapse: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    set_a: tuple[str, ...] = ()
    set_b: tuple[str, ...] = ()
    pairs: tuple[str, ...] = ()
    allow_pairs: tuple[str, ...] = ()
    include_contact: bool = False
    include_clearance: bool = False
    include_separated: bool = False
    include_allowed: bool = False
    list_bodies: bool = False


@dataclass(frozen=True)
class _InterferenceBody:
    id: str
    name: str
    shape: Any
    bbox: dict[str, Any]
    source_occurrence_ids: tuple[str, ...]
    source_occurrence_names: tuple[str, ...]
    collapsed: bool = False
    collapse_reason: str | None = None


@dataclass(frozen=True)
class _DistanceResult:
    value: float
    witness: dict[str, Any] | None = None


@dataclass(frozen=True)
class _IntersectionResult:
    volume_mm3: float = 0.0
    witness: dict[str, Any] | None = None


def normalize_interference_numeric(value: object, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    if normalized < 0.0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return normalized


def normalize_interference_max_pairs(value: object, *, field_name: str = "max_pairs") -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return int(value)


def normalize_interference_body_depth(value: object, *, field_name: str = "body_depth") -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return int(value)


def analyze_interference_scene(
    scene: LoadedStepScene,
    *,
    options: InterferenceOptions | None = None,
) -> dict[str, Any]:
    resolved_options = options or InterferenceOptions()
    started = time.perf_counter()
    bodies = _analysis_bodies(scene, resolved_options)
    if resolved_options.exclude:
        bodies = [body for body in bodies if not _matches_any_body_selector(body, resolved_options.exclude)]

    if resolved_options.list_bodies:
        return _report_payload(
            scene,
            options=resolved_options,
            bodies=bodies,
            pairs=[],
            total_pair_count=0,
            candidate_pair_count=0,
            analyzed_pair_count=0,
            skipped_by_bbox_count=0,
            skipped_by_pair_limit_count=0,
            truncated=False,
            timed_out=False,
            elapsed_ms=_elapsed_ms(started),
        )

    scoped_pairs = _scoped_pairs(bodies, resolved_options)
    total_pair_count = len(scoped_pairs)
    threshold = math.inf if resolved_options.pairs else max(
        resolved_options.clearance_mm,
        resolved_options.contact_tolerance_mm,
    )
    candidate_pairs_all = [
        (_bbox_distance(left.bbox, right.bbox), left, right)
        for left, right in scoped_pairs
        if _bbox_distance(left.bbox, right.bbox) <= threshold
    ]
    candidate_pairs_all.sort(key=lambda item: (item[0], item[1].id, item[2].id))
    candidate_pair_count = len(candidate_pairs_all)
    truncated = candidate_pair_count > resolved_options.max_pairs
    candidate_pairs = candidate_pairs_all[: resolved_options.max_pairs]

    pair_payloads: list[dict[str, Any]] = []
    timed_out = False
    analyzed_pair_count = 0
    explicit_pair_mode = bool(resolved_options.pairs)
    for bbox_distance_mm, left, right in candidate_pairs:
        if resolved_options.time_budget_ms is not None and _elapsed_ms(started) >= resolved_options.time_budget_ms:
            timed_out = True
            break
        pair = _analyze_pair(
            left,
            right,
            bbox_distance_mm=bbox_distance_mm,
            options=resolved_options,
        )
        analyzed_pair_count += 1
        if explicit_pair_mode or _should_report_pair(pair, options=resolved_options):
            pair_payloads.append(pair)

    skipped_by_bbox = total_pair_count - candidate_pair_count
    skipped_by_pair_limit = max(0, candidate_pair_count - len(candidate_pairs))
    return _report_payload(
        scene,
        options=resolved_options,
        bodies=bodies,
        pairs=pair_payloads,
        total_pair_count=total_pair_count,
        candidate_pair_count=candidate_pair_count,
        analyzed_pair_count=analyzed_pair_count,
        skipped_by_bbox_count=max(0, skipped_by_bbox),
        skipped_by_pair_limit_count=skipped_by_pair_limit,
        truncated=truncated,
        timed_out=timed_out,
        elapsed_ms=_elapsed_ms(started),
    )


def analyze_interference_file(
    step_path: Path,
    *,
    options: InterferenceOptions | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    scene = load_step_scene_cached(step_path) if use_cache else load_step_scene(step_path)
    if not scene.step_hash and scene.step_path.is_file():
        scene.step_hash = step_file_hash(scene.step_path)
    return analyze_interference_scene(scene, options=options)


def _analysis_bodies(scene: LoadedStepScene, options: InterferenceOptions) -> list[_InterferenceBody]:
    bodies: list[_InterferenceBody] = []
    for root in scene.roots:
        _collect_analysis_bodies(scene, root, options=options, bodies=bodies)
    return bodies


def _collect_analysis_bodies(
    scene: LoadedStepScene,
    node: OccurrenceNode,
    *,
    options: InterferenceOptions,
    bodies: list[_InterferenceBody],
) -> None:
    if not _node_has_geometry(scene, node):
        return
    selector_id = occurrence_selector_id(node)
    collapse_match = _matches_node_selector(node, options.collapse)
    depth_match = options.body_depth is not None and len(node.path) >= options.body_depth
    leaf_match = node.prototype_key is not None and not node.children
    if collapse_match or depth_match or leaf_match:
        reason = "selector" if collapse_match else "body-depth" if depth_match and not leaf_match else None
        bodies.append(_body_from_node(scene, node, collapsed=not leaf_match, collapse_reason=reason))
        return
    for child in node.children:
        _collect_analysis_bodies(scene, child, options=options, bodies=bodies)


def _node_has_geometry(scene: LoadedStepScene, node: OccurrenceNode) -> bool:
    if node.prototype_key is not None and node.prototype_key in scene.prototype_shapes:
        return True
    return any(_node_has_geometry(scene, child) for child in node.children)


def _body_from_node(
    scene: LoadedStepScene,
    node: OccurrenceNode,
    *,
    collapsed: bool,
    collapse_reason: str | None,
) -> _InterferenceBody:
    leaves = _subtree_leaf_occurrences(node)
    if node.prototype_key is not None and node.prototype_key in scene.prototype_shapes:
        shape = scene_occurrence_shape(scene, node)
    else:
        shape = _compound_from_shapes(scene_occurrence_shape(scene, leaf) for leaf in leaves)
    selector_id = occurrence_selector_id(node)
    name = str(node.name or node.source_name or selector_id)
    source_names = tuple(
        str(leaf.name or leaf.source_name or occurrence_selector_id(leaf))
        for leaf in leaves
    )
    return _InterferenceBody(
        id=selector_id,
        name=name,
        shape=shape,
        bbox=_bbox_from_shape(shape),
        source_occurrence_ids=tuple(occurrence_selector_id(leaf) for leaf in leaves),
        source_occurrence_names=source_names,
        collapsed=collapsed or len(leaves) > 1,
        collapse_reason=collapse_reason,
    )


def _subtree_leaf_occurrences(node: OccurrenceNode) -> list[OccurrenceNode]:
    leaves: list[OccurrenceNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.prototype_key is not None:
            leaves.append(current)
        stack.extend(reversed(current.children))
    return leaves


def _compound_from_shapes(shapes: Iterable[Any]) -> Any:
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def _scoped_pairs(bodies: list[_InterferenceBody], options: InterferenceOptions) -> list[tuple[_InterferenceBody, _InterferenceBody]]:
    if options.pairs:
        pairs: list[tuple[_InterferenceBody, _InterferenceBody]] = []
        for pair_selector in options.pairs:
            left_selector, right_selector = _split_pair_selector(pair_selector)
            for left in bodies:
                if not _matches_body_selector(left, left_selector):
                    continue
                for right in bodies:
                    if left.id == right.id or not _matches_body_selector(right, right_selector):
                        continue
                    pairs.append(_ordered_body_pair(left, right))
        return _dedupe_body_pairs(pairs)

    if options.set_a or options.set_b:
        left_bodies = _select_bodies(bodies, options.set_a or options.set_b)
        right_bodies = _select_bodies(bodies, options.set_b or options.set_a)
        return _dedupe_body_pairs(
            _ordered_body_pair(left, right)
            for left in left_bodies
            for right in right_bodies
            if left.id != right.id
        )

    return [
        (left, right)
        for left_index, left in enumerate(bodies)
        for right in bodies[left_index + 1 :]
    ]


def _select_bodies(bodies: list[_InterferenceBody], selectors: Sequence[str]) -> list[_InterferenceBody]:
    if not selectors:
        return list(bodies)
    return [body for body in bodies if _matches_any_body_selector(body, selectors)]


def _ordered_body_pair(left: _InterferenceBody, right: _InterferenceBody) -> tuple[_InterferenceBody, _InterferenceBody]:
    return (left, right) if left.id <= right.id else (right, left)


def _dedupe_body_pairs(pairs: Iterable[tuple[_InterferenceBody, _InterferenceBody]]) -> list[tuple[_InterferenceBody, _InterferenceBody]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[_InterferenceBody, _InterferenceBody]] = []
    for left, right in pairs:
        key = (left.id, right.id)
        if key in seen:
            continue
        seen.add(key)
        result.append((left, right))
    return result


def _analyze_pair(
    left: _InterferenceBody,
    right: _InterferenceBody,
    *,
    bbox_distance_mm: float,
    options: InterferenceOptions,
) -> dict[str, Any]:
    intersection = _IntersectionResult()
    if bbox_distance_mm <= options.contact_tolerance_mm:
        intersection = _intersection_shape_properties(left.shape, right.shape)

    if intersection.volume_mm3 > options.collision_volume_tolerance_mm3:
        status = "collision"
        min_distance_mm = 0.0
        witness = intersection.witness
    else:
        distance = _shape_distance(left.shape, right.shape)
        min_distance_mm = distance.value
        witness = distance.witness
        if min_distance_mm <= options.contact_tolerance_mm:
            status = "contact"
        elif min_distance_mm <= options.clearance_mm:
            status = "clearance"
        else:
            status = "separated"

    allowed = _pair_allowed(left, right, options.allow_pairs)
    violation = status == "collision" and not allowed
    payload: dict[str, Any] = {
        "id": f"{left.id}:{right.id}",
        "a": left.id,
        "b": right.id,
        "aName": left.name,
        "bName": right.name,
        "status": status,
        "policy": "allow_interference" if allowed else "forbid_interference",
        "violation": violation,
        "bboxDistanceMm": _round_float(bbox_distance_mm),
        "minDistanceMm": _round_float(min_distance_mm),
        "intersectionVolumeMm3": _round_float(intersection.volume_mm3),
    }
    if witness:
        payload["witness"] = witness
    if violation:
        payload["suggestedCorrection"] = _suggested_bbox_correction(left, right, options=options)
    return payload


def _should_report_pair(pair: dict[str, Any], *, options: InterferenceOptions) -> bool:
    status = str(pair.get("status") or "")
    allowed = pair.get("policy") == "allow_interference"
    if allowed and not options.include_allowed:
        return False
    if status == "collision":
        return True
    if status == "contact":
        return bool(options.include_contact)
    if status == "clearance":
        return bool(options.include_clearance)
    if status == "separated":
        return bool(options.include_separated)
    return False


def _shape_distance(left_shape: Any, right_shape: Any) -> _DistanceResult:
    try:
        distance = BRepExtrema_DistShapeShape(left_shape, right_shape)
        distance.SetMultiThread(True)
        distance.Perform()
        if not distance.IsDone():
            return _DistanceResult(math.inf)
        value = float(distance.Value())
        witness = None
        if distance.NbSolution() > 0:
            left_point = distance.PointOnShape1(1)
            right_point = distance.PointOnShape2(1)
            witness = {
                "aPoint": _point_payload(left_point),
                "bPoint": _point_payload(right_point),
                "vectorAToB": _vector_between(left_point, right_point),
            }
        return _DistanceResult(value=value, witness=witness)
    except Exception:
        return _DistanceResult(math.inf)


def _intersection_shape_properties(left_shape: Any, right_shape: Any) -> _IntersectionResult:
    try:
        common = BRepAlgoAPI_Common(left_shape, right_shape)
        common.SetRunParallel(True)
        common.Build()
        if not common.IsDone():
            return _IntersectionResult()
        shape = common.Shape()
        if shape.IsNull():
            return _IntersectionResult()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props, False, False, True)
        volume = max(0.0, float(props.Mass()))
        center = props.CentreOfMass()
        witness = {
            "intersectionCenter": _point_payload(center),
        }
        return _IntersectionResult(volume_mm3=volume, witness=witness)
    except Exception:
        return _IntersectionResult()


def _suggested_bbox_correction(
    left: _InterferenceBody,
    right: _InterferenceBody,
    *,
    options: InterferenceOptions,
) -> dict[str, Any] | None:
    left_min = _bbox_point(left.bbox, "min")
    left_max = _bbox_point(left.bbox, "max")
    right_min = _bbox_point(right.bbox, "min")
    right_max = _bbox_point(right.bbox, "max")
    if left_min is None or left_max is None or right_min is None or right_max is None:
        return None
    overlaps = []
    for index, axis in enumerate(("x", "y", "z")):
        overlap = min(left_max[index], right_max[index]) - max(left_min[index], right_min[index])
        if overlap <= 0.0:
            continue
        left_center = (left_min[index] + left_max[index]) * 0.5
        right_center = (right_min[index] + right_max[index]) * 0.5
        sign = 1.0 if right_center >= left_center else -1.0
        overlaps.append((overlap, index, axis, sign))
    if not overlaps:
        return None
    overlap, index, axis, sign = min(overlaps, key=lambda item: item[0])
    delta = [0.0, 0.0, 0.0]
    delta[index] = sign * (overlap + max(options.contact_tolerance_mm, 1.0e-6))
    return {
        "move": right.id,
        "translationMm": [_round_float(value) for value in delta],
        "axis": axis,
        "basis": "bbox-minimum-separation",
        "confidence": "coarse",
        "reason": "smallest axis-aligned bounding-box separation for the reported interfering pair",
    }


def _report_payload(
    scene: LoadedStepScene,
    *,
    options: InterferenceOptions,
    bodies: list[_InterferenceBody],
    pairs: list[dict[str, Any]],
    total_pair_count: int,
    candidate_pair_count: int,
    analyzed_pair_count: int,
    skipped_by_bbox_count: int,
    skipped_by_pair_limit_count: int,
    truncated: bool,
    timed_out: bool,
    elapsed_ms: float,
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    unexpected_count = 0
    allowed_count = 0
    for pair in pairs:
        status = str(pair.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "collision" and pair.get("violation") is True:
            unexpected_count += 1
        elif status == "collision":
            allowed_count += 1
    step_hash = scene.step_hash or ""
    if not step_hash and scene.step_path.is_file():
        step_hash = step_file_hash(scene.step_path)
    return {
        "schemaVersion": INTERFERENCE_SCHEMA_VERSION,
        "kind": "cadpy-interference-report",
        "profile": "agent-interference",
        "units": "mm",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "stepPath": relative_to_repo(scene.step_path),
            **({"stepHash": step_hash} if step_hash else {}),
        },
        "options": _options_payload(options),
        "summary": {
            "bodyCount": len(bodies),
            "totalPairCount": total_pair_count,
            "candidatePairCount": candidate_pair_count,
            "analyzedPairCount": analyzed_pair_count,
            "reportedPairCount": len(pairs),
            "skippedByBoundingBoxCount": skipped_by_bbox_count,
            "skippedByPairLimitCount": skipped_by_pair_limit_count,
            "truncated": truncated,
            "timedOut": timed_out,
            "elapsedMs": round(elapsed_ms, 1),
            "statusCounts": status_counts,
            "unexpectedInterferenceCount": unexpected_count,
            "allowedInterferenceCount": allowed_count,
        },
        "bodies": [_body_payload(body) for body in bodies],
        "pairs": pairs,
    }


def _options_payload(options: InterferenceOptions) -> dict[str, Any]:
    return {
        "clearanceMm": options.clearance_mm,
        "contactToleranceMm": options.contact_tolerance_mm,
        "collisionVolumeToleranceMm3": options.collision_volume_tolerance_mm3,
        "maxPairs": options.max_pairs,
        **({"timeBudgetMs": options.time_budget_ms} if options.time_budget_ms is not None else {}),
        **({"bodyDepth": options.body_depth} if options.body_depth is not None else {}),
        **({"collapse": list(options.collapse)} if options.collapse else {}),
        **({"exclude": list(options.exclude)} if options.exclude else {}),
        **({"setA": list(options.set_a)} if options.set_a else {}),
        **({"setB": list(options.set_b)} if options.set_b else {}),
        **({"pairs": list(options.pairs)} if options.pairs else {}),
        **({"allowPairs": list(options.allow_pairs)} if options.allow_pairs else {}),
        "includeContact": options.include_contact,
        "includeClearance": options.include_clearance,
        "includeSeparated": options.include_separated,
        "includeAllowed": options.include_allowed,
        "listBodies": options.list_bodies,
    }


def _body_payload(body: _InterferenceBody) -> dict[str, Any]:
    return {
        "id": body.id,
        "name": body.name,
        "bbox": _compact_bbox(body.bbox),
        "sourceOccurrences": list(body.source_occurrence_ids),
        "sourceOccurrenceNames": list(body.source_occurrence_names),
        "collapsed": body.collapsed,
        **({"collapseReason": body.collapse_reason} if body.collapse_reason else {}),
    }


def _pair_allowed(left: _InterferenceBody, right: _InterferenceBody, allow_pairs: Sequence[str]) -> bool:
    for pair_selector in allow_pairs:
        left_selector, right_selector = _split_pair_selector(pair_selector)
        if _matches_body_selector(left, left_selector) and _matches_body_selector(right, right_selector):
            return True
        if _matches_body_selector(left, right_selector) and _matches_body_selector(right, left_selector):
            return True
    return False


def _split_pair_selector(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ValueError(f"Pair selector must use LEFT:RIGHT syntax, got {value!r}")
    left, right = value.split(":", 1)
    left = left.strip()
    right = right.strip()
    if not left or not right:
        raise ValueError(f"Pair selector must include both sides, got {value!r}")
    return left, right


def _matches_node_selector(node: OccurrenceNode, selectors: Sequence[str]) -> bool:
    if not selectors:
        return False
    selector_id = occurrence_selector_id(node)
    candidates = (
        selector_id,
        str(node.name or ""),
        str(node.source_name or ""),
    )
    return any(_selector_matches(candidate, selector) for selector in selectors for candidate in candidates if candidate)


def _matches_any_body_selector(body: _InterferenceBody, selectors: Sequence[str]) -> bool:
    return any(_matches_body_selector(body, selector) for selector in selectors)


def _matches_body_selector(body: _InterferenceBody, selector: str) -> bool:
    candidates = (
        body.id,
        body.name,
        *body.source_occurrence_ids,
        *body.source_occurrence_names,
    )
    return any(_selector_matches(candidate, selector) for candidate in candidates if candidate)


def _selector_matches(candidate: str, selector: str) -> bool:
    normalized = str(selector or "").strip()
    if not normalized:
        return False
    return candidate == normalized or fnmatch.fnmatchcase(candidate, normalized)


def _bbox_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_min = left.get("min") or [0.0, 0.0, 0.0]
    left_max = left.get("max") or [0.0, 0.0, 0.0]
    right_min = right.get("min") or [0.0, 0.0, 0.0]
    right_max = right.get("max") or [0.0, 0.0, 0.0]
    squared = 0.0
    for index in range(3):
        if float(left_max[index]) < float(right_min[index]):
            delta = float(right_min[index]) - float(left_max[index])
        elif float(right_max[index]) < float(left_min[index]):
            delta = float(left_min[index]) - float(right_max[index])
        else:
            delta = 0.0
        squared += delta * delta
    return math.sqrt(squared)


def _bbox_point(bbox: dict[str, Any], key: str) -> tuple[float, float, float] | None:
    value = bbox.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


def _compact_bbox(bbox: dict[str, Any]) -> dict[str, list[float | None]]:
    return {
        "min": [_round_float(value) for value in bbox.get("min", [])],
        "max": [_round_float(value) for value in bbox.get("max", [])],
    }


def _point_payload(point: Any) -> list[float | None]:
    try:
        return [_round_float(point.X()), _round_float(point.Y()), _round_float(point.Z())]
    except Exception:
        return [None, None, None]


def _vector_between(left_point: Any, right_point: Any) -> list[float | None]:
    left = _point_payload(left_point)
    right = _point_payload(right_point)
    if any(value is None for value in left + right):
        return [None, None, None]
    return [_round_float(float(right[index]) - float(left[index])) for index in range(3)]


def _round_float(value: object, *, digits: int = 6) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    rounded = round(numeric, digits)
    return 0.0 if rounded == -0.0 else rounded


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _normalize_cli_numeric(value: object, *, field_name: str, parser: argparse.ArgumentParser) -> float | None:
    try:
        return normalize_interference_numeric(value, field_name=field_name)
    except ValueError as exc:
        parser.error(str(exc))
    return None


def _normalize_cli_max_pairs(value: object, *, parser: argparse.ArgumentParser) -> int | None:
    try:
        return normalize_interference_max_pairs(value, field_name="max_pairs")
    except ValueError as exc:
        parser.error(str(exc))
    return None


def _normalize_cli_body_depth(value: object, *, parser: argparse.ArgumentParser) -> int | None:
    try:
        return normalize_interference_body_depth(value, field_name="body_depth")
    except ValueError as exc:
        parser.error(str(exc))
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cadpy-interference",
        description="Run on-demand STEP assembly interference detection for agentic CAD validation.",
    )
    parser.add_argument("step_path", help="STEP/STP file to inspect.")
    parser.add_argument("--clearance", type=float, default=DEFAULT_CLEARANCE_MM, help="Near-clearance threshold in millimeters.")
    parser.add_argument("--contact-tolerance", type=float, default=DEFAULT_CONTACT_TOLERANCE_MM, help="Contact tolerance in millimeters.")
    parser.add_argument(
        "--collision-volume-tolerance",
        type=float,
        default=DEFAULT_COLLISION_VOLUME_TOLERANCE_MM3,
        help="Minimum common solid volume in cubic millimeters classified as interference.",
    )
    parser.add_argument("--max-pairs", type=int, default=DEFAULT_MAX_PAIRS, help="Maximum candidate pairs to solve exactly.")
    parser.add_argument("--time-budget-ms", type=float, help="Stop before starting a new pair once this elapsed time budget is reached.")
    parser.add_argument("--body-depth", type=int, help="Collapse the occurrence tree into analysis bodies at this 1-based occurrence depth.")
    parser.add_argument("--collapse", action="append", default=[], help="Shell-style occurrence selector/name pattern to collapse into one analysis body.")
    parser.add_argument("--exclude", action="append", default=[], help="Shell-style body selector/name pattern to omit.")
    parser.add_argument("--set-a", action="append", default=[], help="Body selector/name pattern for the left analysis set.")
    parser.add_argument("--set-b", action="append", default=[], help="Body selector/name pattern for the right analysis set.")
    parser.add_argument("--pair", action="append", default=[], help="Explicit LEFT:RIGHT body selector pair. May be repeated.")
    parser.add_argument("--allow-pair", action="append", default=[], help="LEFT:RIGHT pair whose interference is intentional and not a violation.")
    parser.add_argument("--include-contact", action="store_true", help="Report contact pairs in addition to interferences.")
    parser.add_argument("--include-clearance", action="store_true", help="Report near-clearance pairs in addition to interferences.")
    parser.add_argument("--include-separated", action="store_true", help="Report separated analyzed pairs.")
    parser.add_argument("--include-allowed", action="store_true", help="Include allowed interference pairs in the reported pairs list.")
    parser.add_argument("--list-bodies", action="store_true", help="Only emit the resolved analysis bodies.")
    parser.add_argument("--no-cache", action="store_true", help="Load the STEP file without the temporary scene cache.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format. JSON is the agent-oriented default.")
    parser.add_argument("--fail-on-interference", action="store_true", help="Exit with status 2 when unexpected interferences are found.")
    return parser


def _options_from_args(args: argparse.Namespace, *, parser: argparse.ArgumentParser) -> InterferenceOptions:
    try:
        for pair_selector in [*(args.pair or []), *(args.allow_pair or [])]:
            _split_pair_selector(pair_selector)
    except ValueError as exc:
        parser.error(str(exc))
    clearance = _normalize_cli_numeric(args.clearance, field_name="clearance", parser=parser)
    contact_tolerance = _normalize_cli_numeric(args.contact_tolerance, field_name="contact_tolerance", parser=parser)
    collision_volume_tolerance = _normalize_cli_numeric(
        args.collision_volume_tolerance,
        field_name="collision_volume_tolerance",
        parser=parser,
    )
    max_pairs = _normalize_cli_max_pairs(args.max_pairs, parser=parser)
    return InterferenceOptions(
        clearance_mm=DEFAULT_CLEARANCE_MM if clearance is None else clearance,
        contact_tolerance_mm=DEFAULT_CONTACT_TOLERANCE_MM if contact_tolerance is None else contact_tolerance,
        collision_volume_tolerance_mm3=(
            DEFAULT_COLLISION_VOLUME_TOLERANCE_MM3
            if collision_volume_tolerance is None
            else collision_volume_tolerance
        ),
        max_pairs=DEFAULT_MAX_PAIRS if max_pairs is None else max_pairs,
        time_budget_ms=_normalize_cli_numeric(args.time_budget_ms, field_name="time_budget_ms", parser=parser),
        body_depth=_normalize_cli_body_depth(args.body_depth, parser=parser),
        collapse=tuple(args.collapse or ()),
        exclude=tuple(args.exclude or ()),
        set_a=tuple(args.set_a or ()),
        set_b=tuple(args.set_b or ()),
        pairs=tuple(args.pair or ()),
        allow_pairs=tuple(args.allow_pair or ()),
        include_contact=bool(args.include_contact),
        include_clearance=bool(args.include_clearance),
        include_separated=bool(args.include_separated),
        include_allowed=bool(args.include_allowed),
        list_bodies=bool(args.list_bodies),
    )


def _format_text_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "Interference report",
        f"  bodies: {summary.get('bodyCount', 0)}",
        f"  pairs: {summary.get('reportedPairCount', 0)} reported / {summary.get('candidatePairCount', 0)} candidates / {summary.get('totalPairCount', 0)} scoped",
        f"  unexpected interferences: {summary.get('unexpectedInterferenceCount', 0)}",
    ]
    for pair in payload.get("pairs", []) if isinstance(payload.get("pairs"), list) else []:
        if not isinstance(pair, dict):
            continue
        marker = "VIOLATION" if pair.get("violation") else "allowed"
        lines.append(
            f"  - {marker} {pair.get('status')}: {pair.get('aName') or pair.get('a')} <-> {pair.get('bName') or pair.get('b')} "
            f"volume={pair.get('intersectionVolumeMm3', 0)}mm^3"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    payload = analyze_interference_file(
        Path(args.step_path),
        options=_options_from_args(args, parser=parser),
        use_cache=not bool(args.no_cache),
    )
    if args.format == "text":
        print(_format_text_report(payload))
    else:
        print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=bool(args.pretty)))
    unexpected = int((payload.get("summary") or {}).get("unexpectedInterferenceCount") or 0)
    return 2 if bool(args.fail_on_interference) and unexpected > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
