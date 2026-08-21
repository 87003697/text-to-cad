"""Frozen canonical VoxBlame JSON contracts and strict structural validators.

The canonical contracts deliberately reuse the public schema identifiers of
the pre-canonical workflow. The breaking cutover is complete: these validators
are the only supported readers and reject legacy or mixed-shape state.
"""

from __future__ import annotations

import math
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence

from meshscope.voxblame.errors import UnsupportedOrInvalidVoxBlameState


SESSION_SCHEMA = "voxblame.session/2"
REPORT_SCHEMA = "voxblame.report/2"
MEASUREMENT_SCHEMA = "voxblame.measurement/1"
SUMMARY_SCHEMA = "voxblame.summary/1"
COORDINATE_CONTRACT = "trellis2_canonical/1"
MAX_DEPTH = 8
BOUNDARY_EPSILON = 1e-9
MIN_EXTERIOR_DIAGNOSTIC_GRID_DEPTH = -1022

# These fields belong either to the superseded workflow, sampled-distance
# evaluation, world-coordinate terminology, or Agent-owned judgment.  They are
# forbidden at every nesting level, in addition to each object being closed to
# unknown fields.
FORBIDDEN_FIELDS = frozenset(
    {
        "accepted",
        "best_step",
        "bounds_world",
        "candidate",
        "candidate_digest",
        "chamfer",
        "chamfer_distance",
        "change_counts",
        "changes",
        "coarsest_first_error_depth",
        "current",
        "current_error",
        "direction",
        "distances",
        "errors",
        "first_error_depth",
        "final_step",
        "frame",
        "hausdorff",
        "hausdorff_distance",
        "heatmap",
        "measurement_contract",
        "next_action",
        "overview",
        "morton_prefix",
        "octant_prefix",
        "p90",
        "p95",
        "priority",
        "previous_error",
        "reference",
        "region_handle",
        "remaining_error_count",
        "sample_count",
        "sample_seed",
        "samples",
        "selected_best_step",
        "stop_reason",
        "stats",
        "strategy",
        "verdict",
    }
)

SESSION_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "coordinate_contract",
        "semantic_units",
        "max_depth",
        "boundary_epsilon",
        "canonical_reference",
        "profiles",
    }
)
REPORT_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "coordinate_contract",
        "max_depth",
        "step",
        "compare_to",
        "canonical_reference",
        "measurement",
        "errors_by_depth",
        "depth_8_evidence",
        "exterior_surface",
        "repair_targets",
        "objective_facts",
        "no_observable_geometry_change",
    }
)
SUMMARY_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "coordinate_contract",
        "max_depth",
        "step",
        "compare_to",
        "report",
        "canonical_reference",
        "measurement",
        "errors_by_depth",
        "exterior_surface",
        "repair_targets",
        "objective_facts",
        "no_observable_geometry_change",
    }
)

_CANONICAL_REFERENCE_SESSION_FIELDS = frozenset(
    {
        "canonical_reference_sha256",
        "reference_ply_path",
        "reference_ply_sha256",
        "triangle_set_sha256",
        "normalization_json_path",
        "normalization_json_sha256",
        "interior_tree_path",
        "interior_tree_sha256",
    }
)
_CANONICAL_REFERENCE_MEASUREMENT_FIELDS = frozenset(
    {
        "canonical_reference_sha256",
        "reference_ply_sha256",
        "triangle_set_sha256",
        "interior_tree_sha256",
    }
)
_PROFILE_FIELDS = frozenset(
    {"surface_occupancy", "target_partition", "exterior_surface"}
)
_MEASUREMENT_FIELDS = frozenset(
    {
        "candidate_mesh_sha256",
        "interior_tree_sha256",
        "exterior_snapshot_sha256",
        "observable_sha256",
    }
)
_DEPTH_FIELDS = frozenset(
    {
        "depth",
        "reference_surface_count",
        "candidate_surface_count",
        "missing_surface_count",
        "excess_surface_count",
        "union_surface_count",
        "surface_error_count",
        "surface_error_rate",
    }
)
_SET_EVIDENCE_FIELDS = frozenset(
    {"storage_schema", "path", "logical_sha256", "surface_count"}
)
_DEPTH_8_EVIDENCE_FIELDS = frozenset(
    {"missing_surface", "excess_surface"}
)
_EXTERIOR_FIELDS = frozenset(
    {
        "storage_schema",
        "path",
        "logical_sha256",
        "surface_present",
        "surface_cell_count",
        "bounds_canonical",
        "centroid_canonical",
        "nearest_overrun",
        "farthest_overrun",
        "outside_directions",
        "diagnostic_grid_depth",
        "coarsened",
    }
)
_BOUNDS_FIELDS = frozenset({"min", "max"})
_ERROR_PROFILE_FIELDS = frozenset(
    {"missing_surface_count", "excess_surface_count", "surface_error_count"}
)
_MASK_FIELDS = frozenset(
    {"storage_schema", "path", "logical_sha256", "region_count"}
)
_COMPACT_MASK_FIELDS = frozenset(
    {"storage_schema", "logical_sha256", "region_count"}
)
_COMPONENT_FIELDS = frozenset(
    {"component_key", "split_index", "split_count", "split_reason"}
)
_EXTERIOR_TARGET_FIELDS = frozenset(
    {
        "centroid_canonical",
        "surface_cell_count",
        "nearest_overrun",
        "farthest_overrun",
        "outside_directions",
        "diagnostic_grid_depth",
        "coarsened",
    }
)
_TARGET_FIELDS = frozenset(
    {
        "target_key",
        "source_step",
        "kind",
        "display_rank",
        "bounds_canonical",
        "error_profile",
        "mask",
        "component",
        "exterior",
    }
)
_REPORT_TARGETS_FIELDS = frozenset(
    {"ordering_profile", "total", "ordered_targets"}
)
_SUMMARY_TARGETS_FIELDS = frozenset(
    {
        "ordering_profile",
        "total",
        "returned",
        "remaining",
        "offset",
        "next_offset",
        "items",
    }
)
_OBJECTIVE_FACTS_FIELDS = frozenset(
    {"global_depth_8_zero", "out_of_frame_clear", "no_evidence_conflict"}
)
_DIRECTIONS = frozenset({"-x", "+x", "-y", "+y", "-z", "+z"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def validate_session_contract(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate and return one canonical ``voxblame.session/2`` document."""

    root = _object(value, "$", SESSION_REQUIRED_FIELDS)
    _const(root["schema"], SESSION_SCHEMA, "$.schema")
    _validate_common_header(root)
    _const(root["semantic_units"], None, "$.semantic_units")
    _validate_session_reference(root["canonical_reference"], "$.canonical_reference")
    profiles = _object(root["profiles"], "$.profiles", _PROFILE_FIELDS)
    _const(
        profiles["surface_occupancy"],
        "conservative_surface_occupancy/1",
        "$.profiles.surface_occupancy",
    )
    if profiles["target_partition"] not in {
        "repair_target_partition/1",
        "repair_target_partition/2",
    }:
        _fail(
            "$.profiles.target_partition",
            "must identify a supported Repair Target partition profile",
        )
    _const(
        profiles["exterior_surface"],
        "signed_exterior_surface/1",
        "$.profiles.exterior_surface",
    )
    return value


def validate_report_contract(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate and return one canonical ``voxblame.report/2`` document."""

    root = _object(value, "$", REPORT_REQUIRED_FIELDS)
    _const(root["schema"], REPORT_SCHEMA, "$.schema")
    _validate_measurement_document(root, report=True)
    return value


def validate_measurement_contract(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate one persisted canonical Measured Step document."""

    root = _object(value, "$", REPORT_REQUIRED_FIELDS)
    _const(root["schema"], MEASUREMENT_SCHEMA, "$.schema")
    _validate_measurement_document(root, report=True)
    return value


def validate_summary_contract(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate and return one canonical ``voxblame.summary/1`` document."""

    root = _object(value, "$", SUMMARY_REQUIRED_FIELDS)
    _const(root["schema"], SUMMARY_SCHEMA, "$.schema")
    _relative_path(root["report"], "$.report")
    _validate_measurement_document(root, report=False)
    return value


def validate_contract_bundle(
    session: Mapping[str, Any],
    report: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    """Validate three documents and their public cross-artifact identities."""

    validate_session_contract(session)
    validate_report_contract(report)
    validate_summary_contract(summary)

    session_reference = session["canonical_reference"]
    report_reference = report["canonical_reference"]
    summary_reference = summary["canonical_reference"]
    for key in _CANONICAL_REFERENCE_MEASUREMENT_FIELDS:
        if report_reference[key] != summary_reference[key]:
            _fail(f"$.summary.canonical_reference.{key}", "does not match report")
        if session_reference[key] != report_reference[key]:
            _fail(f"$.report.canonical_reference.{key}", "does not match session")

    for key in (
        "coordinate_contract",
        "max_depth",
        "step",
        "compare_to",
        "measurement",
        "errors_by_depth",
        "exterior_surface",
        "objective_facts",
        "no_observable_geometry_change",
    ):
        if report[key] != summary[key]:
            _fail(f"$.summary.{key}", "does not match report")

    full_targets = report["repair_targets"]
    page = summary["repair_targets"]
    if full_targets["ordering_profile"] != page["ordering_profile"]:
        _fail("$.summary.repair_targets.ordering_profile", "does not match report")
    if full_targets["total"] != page["total"]:
        _fail("$.summary.repair_targets.total", "does not match report")
    start = page["offset"]
    expected = full_targets["ordered_targets"][start : start + page["returned"]]
    for index, (full, compact) in enumerate(zip(expected, page["items"], strict=True)):
        projected = dict(full)
        projected["mask"] = {
            key: full["mask"][key] for key in _COMPACT_MASK_FIELDS
        }
        if projected != compact:
            _fail(
                f"$.summary.repair_targets.items[{index}]",
                "does not match the corresponding report target",
            )


def _validate_measurement_document(root: Mapping[str, Any], *, report: bool) -> None:
    _validate_common_header(root)
    step = _integer(root["step"], "$.step", minimum=0)
    compare_to = root["compare_to"]
    if step == 0:
        _const(compare_to, None, "$.compare_to")
    else:
        parent = _integer(compare_to, "$.compare_to", minimum=0)
        if parent >= step:
            _fail("$.compare_to", "must identify an earlier measured step")

    _validate_measurement_reference(root["canonical_reference"], "$.canonical_reference")
    measurement = _object(root["measurement"], "$.measurement", _MEASUREMENT_FIELDS)
    for key in _MEASUREMENT_FIELDS:
        _sha256(measurement[key], f"$.measurement.{key}")

    depths = _array(root["errors_by_depth"], "$.errors_by_depth", length=MAX_DEPTH)
    for expected_depth, entry in enumerate(depths, start=1):
        _validate_depth(entry, expected_depth, f"$.errors_by_depth[{expected_depth - 1}]")

    exterior = _validate_exterior(root["exterior_surface"], "$.exterior_surface")
    if measurement["exterior_snapshot_sha256"] != exterior["logical_sha256"]:
        _fail(
            "$.measurement.exterior_snapshot_sha256",
            "does not match exterior_surface.logical_sha256",
        )

    if report:
        evidence = _object(
            root["depth_8_evidence"],
            "$.depth_8_evidence",
            _DEPTH_8_EVIDENCE_FIELDS,
        )
        depth_8 = depths[-1]
        for key, count_key in (
            ("missing_surface", "missing_surface_count"),
            ("excess_surface", "excess_surface_count"),
        ):
            item = _object(
                evidence[key], f"$.depth_8_evidence.{key}", _SET_EVIDENCE_FIELDS
            )
            _string(item["storage_schema"], f"$.depth_8_evidence.{key}.storage_schema")
            _relative_path(item["path"], f"$.depth_8_evidence.{key}.path")
            _sha256(item["logical_sha256"], f"$.depth_8_evidence.{key}.logical_sha256")
            count = _integer(
                item["surface_count"],
                f"$.depth_8_evidence.{key}.surface_count",
                minimum=0,
            )
            if count != depth_8[count_key]:
                _fail(
                    f"$.depth_8_evidence.{key}.surface_count",
                    f"does not match errors_by_depth[7].{count_key}",
                )
        _validate_report_targets(
            root["repair_targets"],
            step,
            interior_error_count=depth_8["surface_error_count"],
            exterior_error_count=exterior["surface_cell_count"],
        )
    else:
        _validate_summary_targets(root["repair_targets"], step)

    facts = _object(root["objective_facts"], "$.objective_facts", _OBJECTIVE_FACTS_FIELDS)
    for key in _OBJECTIVE_FACTS_FIELDS:
        _boolean(facts[key], f"$.objective_facts.{key}")
    depth_8 = depths[-1]
    expected_zero = (
        depth_8["missing_surface_count"] == 0
        and depth_8["excess_surface_count"] == 0
    )
    if facts["global_depth_8_zero"] is not expected_zero:
        _fail("$.objective_facts.global_depth_8_zero", "contradicts depth-8 evidence")
    if facts["out_of_frame_clear"] is exterior["surface_present"]:
        _fail("$.objective_facts.out_of_frame_clear", "contradicts exterior evidence")
    no_change = _boolean(
        root["no_observable_geometry_change"],
        "$.no_observable_geometry_change",
    )
    if step == 0 and no_change:
        _fail(
            "$.no_observable_geometry_change",
            "step 0 has no comparison parent",
        )


def _validate_common_header(root: Mapping[str, Any]) -> None:
    _const(root["coordinate_contract"], COORDINATE_CONTRACT, "$.coordinate_contract")
    _const(root["max_depth"], MAX_DEPTH, "$.max_depth")
    if "boundary_epsilon" in root:
        _const(root["boundary_epsilon"], BOUNDARY_EPSILON, "$.boundary_epsilon")


def _validate_session_reference(value: Any, path: str) -> None:
    reference = _object(value, path, _CANONICAL_REFERENCE_SESSION_FIELDS)
    for key in (
        "canonical_reference_sha256",
        "reference_ply_sha256",
        "triangle_set_sha256",
        "normalization_json_sha256",
        "interior_tree_sha256",
    ):
        _sha256(reference[key], f"{path}.{key}")
    for key in (
        "reference_ply_path",
        "normalization_json_path",
        "interior_tree_path",
    ):
        _relative_path(reference[key], f"{path}.{key}")


def _validate_measurement_reference(value: Any, path: str) -> None:
    reference = _object(value, path, _CANONICAL_REFERENCE_MEASUREMENT_FIELDS)
    for key in _CANONICAL_REFERENCE_MEASUREMENT_FIELDS:
        _sha256(reference[key], f"{path}.{key}")


def _validate_depth(value: Any, expected_depth: int, path: str) -> None:
    entry = _object(value, path, _DEPTH_FIELDS)
    _const(entry["depth"], expected_depth, f"{path}.depth")
    for key in _DEPTH_FIELDS - {"depth", "surface_error_rate"}:
        _integer(entry[key], f"{path}.{key}", minimum=0)
    if entry["missing_surface_count"] > entry["reference_surface_count"]:
        _fail(f"{path}.missing_surface_count", "cannot exceed reference surface")
    if entry["excess_surface_count"] > entry["candidate_surface_count"]:
        _fail(f"{path}.excess_surface_count", "cannot exceed candidate surface")
    if entry["union_surface_count"] != (
        entry["reference_surface_count"] + entry["excess_surface_count"]
    ) or entry["union_surface_count"] != (
        entry["candidate_surface_count"] + entry["missing_surface_count"]
    ):
        _fail(f"{path}.union_surface_count", "contradicts set-count identities")
    if entry["surface_error_count"] != (
        entry["missing_surface_count"] + entry["excess_surface_count"]
    ):
        _fail(f"{path}.surface_error_count", "must equal missing plus excess")
    if entry["union_surface_count"] < entry["surface_error_count"]:
        _fail(f"{path}.union_surface_count", "must cover the complete error set")
    expected_rate = (
        entry["surface_error_count"] / entry["union_surface_count"]
        if entry["union_surface_count"]
        else 0.0
    )
    rate = _number(entry["surface_error_rate"], f"{path}.surface_error_rate")
    if not math.isclose(rate, expected_rate, rel_tol=0.0, abs_tol=1e-15):
        _fail(f"{path}.surface_error_rate", "does not match count evidence")


def _validate_exterior(value: Any, path: str) -> Mapping[str, Any]:
    exterior = _object(value, path, _EXTERIOR_FIELDS)
    _string(exterior["storage_schema"], f"{path}.storage_schema")
    _relative_path(exterior["path"], f"{path}.path")
    _sha256(exterior["logical_sha256"], f"{path}.logical_sha256")
    present = _boolean(exterior["surface_present"], f"{path}.surface_present")
    count = _integer(
        exterior["surface_cell_count"],
        f"{path}.surface_cell_count",
        minimum=0,
    )
    _validate_exterior_resolution(exterior, path)
    directions = _directions(exterior["outside_directions"], f"{path}.outside_directions")
    nullable_fields = (
        "bounds_canonical",
        "centroid_canonical",
        "nearest_overrun",
        "farthest_overrun",
    )
    if present:
        if count == 0 or not directions:
            _fail(path, "present exterior surface requires cells and directions")
        _bounds(exterior["bounds_canonical"], f"{path}.bounds_canonical")
        _vector3(exterior["centroid_canonical"], f"{path}.centroid_canonical")
        nearest = _number(
            exterior["nearest_overrun"],
            f"{path}.nearest_overrun",
            minimum=0.0,
        )
        farthest = _number(
            exterior["farthest_overrun"],
            f"{path}.farthest_overrun",
            minimum=0.0,
        )
        if nearest > farthest:
            _fail(f"{path}.nearest_overrun", "must not exceed farthest_overrun")
    else:
        if count != 0 or directions:
            _fail(path, "clear exterior surface must have zero cells and directions")
        for key in nullable_fields:
            _const(exterior[key], None, f"{path}.{key}")
    return exterior


def _validate_report_targets(
    value: Any,
    step: int,
    *,
    interior_error_count: int,
    exterior_error_count: int,
) -> None:
    targets = _object(value, "$.repair_targets", _REPORT_TARGETS_FIELDS)
    _string(targets["ordering_profile"], "$.repair_targets.ordering_profile")
    total = _integer(targets["total"], "$.repair_targets.total", minimum=0)
    ordered = _array(targets["ordered_targets"], "$.repair_targets.ordered_targets")
    if len(ordered) != total:
        _fail("$.repair_targets.total", "does not match ordered_targets")
    seen: set[str] = set()
    seen_masks: set[str] = set()
    observed_interior = 0
    observed_exterior = 0
    for rank, target in enumerate(ordered):
        key = _validate_target(
            target,
            f"$.repair_targets.ordered_targets[{rank}]",
            step=step,
            rank=rank,
            compact=False,
        )
        if key in seen:
            _fail(
                f"$.repair_targets.ordered_targets[{rank}].target_key",
                "must be unique",
            )
        seen.add(key)
        mask_sha256 = target["mask"]["logical_sha256"]
        if mask_sha256 in seen_masks:
            _fail(
                f"$.repair_targets.ordered_targets[{rank}].mask.logical_sha256",
                "must be unique",
            )
        seen_masks.add(mask_sha256)
        if target["kind"] == "interior":
            observed_interior += target["error_profile"]["surface_error_count"]
        else:
            observed_exterior += target["error_profile"]["surface_error_count"]
    if observed_interior != interior_error_count:
        _fail(
            "$.repair_targets.ordered_targets",
            "interior targets do not cover the depth-8 error count",
        )
    if observed_exterior != exterior_error_count:
        _fail(
            "$.repair_targets.ordered_targets",
            "exterior targets do not cover the exterior error count",
        )


def _validate_summary_targets(value: Any, step: int) -> None:
    targets = _object(value, "$.repair_targets", _SUMMARY_TARGETS_FIELDS)
    _string(targets["ordering_profile"], "$.repair_targets.ordering_profile")
    total = _integer(targets["total"], "$.repair_targets.total", minimum=0)
    returned = _integer(
        targets["returned"],
        "$.repair_targets.returned",
        minimum=0,
        maximum=8,
    )
    remaining = _integer(targets["remaining"], "$.repair_targets.remaining", minimum=0)
    offset = _integer(targets["offset"], "$.repair_targets.offset", minimum=0)
    if offset > total:
        _fail("$.repair_targets.offset", "must not exceed total")
    items = _array(targets["items"], "$.repair_targets.items")
    if len(items) != returned or returned + remaining != max(total - offset, 0):
        _fail("$.repair_targets", "pagination counts are inconsistent")
    expected_next = offset + returned if remaining else None
    _const(targets["next_offset"], expected_next, "$.repair_targets.next_offset")
    for index, target in enumerate(items):
        _validate_target(
            target,
            f"$.repair_targets.items[{index}]",
            step=step,
            rank=offset + index,
            compact=True,
        )


def _validate_target(
    value: Any,
    path: str,
    *,
    step: int,
    rank: int,
    compact: bool,
) -> str:
    target = _object(value, path, _TARGET_FIELDS)
    key = _key(target["target_key"], f"{path}.target_key")
    _const(target["source_step"], step, f"{path}.source_step")
    if not key.startswith(f"step-{step:06d}:"):
        _fail(f"{path}.target_key", "does not belong to source_step")
    kind = target["kind"]
    if kind not in ("interior", "exterior"):
        _fail(f"{path}.kind", "must be interior or exterior")
    _const(target["display_rank"], rank, f"{path}.display_rank")
    _bounds(target["bounds_canonical"], f"{path}.bounds_canonical")
    profile = _object(
        target["error_profile"],
        f"{path}.error_profile",
        _ERROR_PROFILE_FIELDS,
    )
    for field in _ERROR_PROFILE_FIELDS:
        _integer(profile[field], f"{path}.error_profile.{field}", minimum=0)
    if profile["surface_error_count"] != (
        profile["missing_surface_count"] + profile["excess_surface_count"]
    ):
        _fail(
            f"{path}.error_profile.surface_error_count",
            "must equal missing plus excess",
        )
    mask_fields = _COMPACT_MASK_FIELDS if compact else _MASK_FIELDS
    mask = _object(target["mask"], f"{path}.mask", mask_fields)
    _const(
        mask["storage_schema"],
        (
            "octree_region_set/1"
            if kind == "interior"
            else "exterior_grid_region_set/1"
        ),
        f"{path}.mask.storage_schema",
    )
    if not compact:
        _relative_path(mask["path"], f"{path}.mask.path")
    _sha256(mask["logical_sha256"], f"{path}.mask.logical_sha256")
    _integer(mask["region_count"], f"{path}.mask.region_count", minimum=1)
    component = _object(
        target["component"],
        f"{path}.component",
        _COMPONENT_FIELDS,
    )
    _key(component["component_key"], f"{path}.component.component_key")
    split_index = _integer(
        component["split_index"],
        f"{path}.component.split_index",
        minimum=0,
    )
    split_count = _integer(
        component["split_count"],
        f"{path}.component.split_count",
        minimum=1,
    )
    if split_index >= split_count:
        _fail(f"{path}.component.split_index", "must be less than split_count")
    _string(component["split_reason"], f"{path}.component.split_reason")
    if kind == "interior":
        _const(target["exterior"], None, f"{path}.exterior")
    else:
        exterior = _object(
            target["exterior"],
            f"{path}.exterior",
            _EXTERIOR_TARGET_FIELDS,
        )
        _vector3(
            exterior["centroid_canonical"],
            f"{path}.exterior.centroid_canonical",
        )
        _integer(
            exterior["surface_cell_count"],
            f"{path}.exterior.surface_cell_count",
            minimum=1,
        )
        nearest = _number(
            exterior["nearest_overrun"],
            f"{path}.exterior.nearest_overrun",
            minimum=0.0,
        )
        farthest = _number(
            exterior["farthest_overrun"],
            f"{path}.exterior.farthest_overrun",
            minimum=0.0,
        )
        if nearest > farthest:
            _fail(f"{path}.exterior.nearest_overrun", "must not exceed farthest_overrun")
        if not _directions(
            exterior["outside_directions"],
            f"{path}.exterior.outside_directions",
        ):
            _fail(f"{path}.exterior.outside_directions", "must not be empty")
        _validate_exterior_resolution(exterior, f"{path}.exterior")
    return key


def _validate_exterior_resolution(
    exterior: Mapping[str, Any],
    path: str,
) -> None:
    diagnostic_depth = _integer(
        exterior["diagnostic_grid_depth"],
        f"{path}.diagnostic_grid_depth",
        minimum=MIN_EXTERIOR_DIAGNOSTIC_GRID_DEPTH,
        maximum=MAX_DEPTH,
    )
    coarsened = _boolean(exterior["coarsened"], f"{path}.coarsened")
    if coarsened is not (diagnostic_depth < MAX_DEPTH):
        _fail(f"{path}.coarsened", "contradicts diagnostic grid depth")


def _object(value: Any, path: str, required: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    keys = set(value)
    forbidden = sorted(keys & FORBIDDEN_FIELDS)
    if forbidden:
        _fail(f"{path}.{forbidden[0]}", "field is forbidden by the canonical contract")
    missing = sorted(required - keys)
    if missing:
        _fail(path, f"missing required field {missing[0]}")
    unknown = sorted(keys - required)
    if unknown:
        _fail(f"{path}.{unknown[0]}", "unknown field")
    return value


def _array(value: Any, path: str, *, length: int | None = None) -> Sequence[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    if length is not None and len(value) != length:
        _fail(path, f"must contain exactly {length} items")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(path, "must be a non-empty string")
    return value


def _key(value: Any, path: str) -> str:
    text = _string(value, path)
    if _KEY.fullmatch(text) is None:
        _fail(path, "must be a stable lowercase key")
    return text


def _sha256(value: Any, path: str) -> str:
    text = _string(value, path)
    if _SHA256.fullmatch(text) is None:
        _fail(path, "must be a lowercase SHA-256 digest")
    return text


def _relative_path(value: Any, path: str) -> str:
    text = _string(value, path)
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in text:
        _fail(path, "must be a normalized relative POSIX path")
    return text


def _integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be an integer")
    if minimum is not None and value < minimum:
        _fail(path, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        _fail(path, f"must be at most {maximum}")
    return value


def _number(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "must be a number")
    result = float(value)
    if not math.isfinite(result):
        _fail(path, "must be finite")
    if minimum is not None and result < minimum:
        _fail(path, f"must be at least {minimum}")
    return result


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, "must be a boolean")
    return value


def _vector3(value: Any, path: str) -> tuple[float, float, float]:
    items = _array(value, path, length=3)
    return (
        _number(items[0], f"{path}[0]"),
        _number(items[1], f"{path}[1]"),
        _number(items[2], f"{path}[2]"),
    )


def _bounds(value: Any, path: str) -> None:
    bounds = _object(value, path, _BOUNDS_FIELDS)
    lower = _vector3(bounds["min"], f"{path}.min")
    upper = _vector3(bounds["max"], f"{path}.max")
    if any(low > high for low, high in zip(lower, upper, strict=True)):
        _fail(path, "min must not exceed max")


def _directions(value: Any, path: str) -> list[str]:
    items = _array(value, path)
    directions = []
    for index, item in enumerate(items):
        direction = _string(item, f"{path}[{index}]")
        if direction not in _DIRECTIONS:
            _fail(f"{path}[{index}]", "is an unknown outside direction")
        directions.append(direction)
    if len(directions) != len(set(directions)):
        _fail(path, "must not contain duplicate directions")
    expected_order = [
        item
        for item in ("-x", "+x", "-y", "+y", "-z", "+z")
        if item in directions
    ]
    if directions != expected_order:
        _fail(path, "must use canonical direction order")
    return directions


def _const(value: Any, expected: Any, path: str) -> None:
    if type(value) is not type(expected) or value != expected:
        _fail(path, f"must equal {expected!r}")


def _fail(path: str, detail: str) -> None:
    raise UnsupportedOrInvalidVoxBlameState(path=path, detail=detail)
