"""Objective fixed-mask comparison for one Agent-authored Repair Batch."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Hashable, Iterable, Mapping, TypeVar
import uuid

from meshscope.voxblame.codec import read_surface_tree
from meshscope.voxblame.contracts import (
    COORDINATE_CONTRACT,
    MAX_DEPTH,
    validate_measurement_contract,
)
from meshscope.voxblame.errors import (
    OctreeError,
    UnsupportedOrInvalidVoxBlameState,
)
from meshscope.voxblame.grading import decode_octant_prefix
from meshscope.voxblame.targets import _expand_region_set, page_repair_targets
from meshscope.voxblame.tree import SurfaceTree


REPAIR_BATCH_SCHEMA = "voxblame.repair-batch/1"
REGION_DIFF_SCHEMA = "voxblame.region-diff/1"
_PLAN_DIGEST_DOMAIN = b"voxblame.repair-batch/1\0"
_DIFF_DIGEST_DOMAIN = b"voxblame.region-diff/1\0"
_EDIT_KEY = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_FORBIDDEN_EVIDENCE_FIELDS = frozenset(
    {"improved", "regressed", "resolved", "introduced", "keep", "revert", "verdict"}
)
_Cell = TypeVar("_Cell", bound=Hashable)


@dataclass(frozen=True)
class RegionDiffResult:
    """Published Region Diff plus idempotent-retry state."""

    region_diff: dict[str, Any]
    idempotent: bool


def publish_region_diff(
    workspace: str | Path,
    *,
    from_step: int,
    to_step: int,
    repair_plan: str | Path | Mapping[str, Any],
    output: str | Path,
) -> RegionDiffResult:
    """Compare one explicit Repair Cycle and atomically publish its evidence."""

    root = Path(workspace)
    before = _load_measurement(root, from_step)
    after = _load_measurement(root, to_step)
    if to_step <= from_step:
        raise OctreeError("Region Diff to_step must be later than from_step")
    if after["compare_to"] != from_step:
        raise OctreeError("Region Diff steps do not form the published comparison edge")
    if before["canonical_reference"] != after["canonical_reference"]:
        raise OctreeError("Region Diff steps use different Canonical References")

    plan = _load_repair_plan(repair_plan)
    targets = _validate_repair_batch(plan, from_step=from_step, measurement=before)
    plan_bytes = _json_bytes(plan)
    plan_sha256 = hashlib.sha256(_PLAN_DIGEST_DOMAIN + plan_bytes).hexdigest()

    reference_sets = _occupancy_by_depth(read_surface_tree(root / "reference.vbsvo"))
    before_sets = _step_sets(root, from_step)
    after_sets = _step_sets(root, to_step)
    before_exterior = _load_exterior_snapshot(root, from_step)
    after_exterior = _load_exterior_snapshot(root, to_step)

    selected_regions = []
    selected_interior: set[int] = set()
    selected_exterior: set[tuple[int, int, int]] = set()
    selected_exterior_depth: int | None = None
    for target in targets:
        exact_mask = {
            key: target["mask"][key]
            for key in ("storage_schema", "logical_sha256", "region_count")
        }
        if target["kind"] == "interior":
            cells = _read_interior_mask(root, target)
            selected_interior.update(cells)
            selected_regions.append(
                {
                    "target_key": target["target_key"],
                    "kind": "interior",
                    "exact_mask": exact_mask,
                    "interior": _interior_region_evidence(
                        cells, reference_sets, before_sets, after_sets
                    ),
                    "exterior": None,
                }
            )
        else:
            depth, exterior_cells = _read_exterior_mask(root, target)
            if selected_exterior_depth not in (None, depth):
                raise OctreeError(
                    "selected exterior Repair Targets use different frozen grids"
                )
            selected_exterior_depth = depth
            selected_exterior.update(exterior_cells)
            selected_regions.append(
                {
                    "target_key": target["target_key"],
                    "kind": "exterior",
                    "exact_mask": exact_mask,
                    "interior": None,
                    "exterior": _exterior_region_evidence(
                        exterior_cells,
                        depth,
                        before_exterior,
                        after_exterior,
                    ),
                }
            )

    selected_keys = [item["target_key"] for item in plan["selected_targets"]]
    document: dict[str, Any] = {
        "schema": REGION_DIFF_SCHEMA,
        "coordinate_contract": COORDINATE_CONTRACT,
        "max_depth": MAX_DEPTH,
        "from_step": from_step,
        "to_step": to_step,
        "repair_batch": {
            "schema": REPAIR_BATCH_SCHEMA,
            "plan_sha256": plan_sha256,
            "from_step": from_step,
            "selected_targets": [
                {
                    "target_key": target["target_key"],
                    "kind": target["kind"],
                    "mask_sha256": target["mask"]["logical_sha256"],
                }
                for target in targets
            ],
            "planned_edits": [
                {
                    "edit_key": edit["edit_key"],
                    "target_keys": list(edit["target_keys"]),
                }
                for edit in plan["planned_edits"]
            ],
        },
        "measurement_trajectory": _measurement_trajectory(before, after),
        "selected_regions": selected_regions,
        "batch_union": {
            "selected_target_keys": selected_keys,
            "interior": (
                _interior_region_evidence(
                    selected_interior, reference_sets, before_sets, after_sets
                )
                if selected_interior
                else None
            ),
            "exterior": (
                _exterior_region_evidence(
                    selected_exterior,
                    selected_exterior_depth,
                    before_exterior,
                    after_exterior,
                )
                if selected_exterior_depth is not None
                else None
            ),
        },
        "outside_selected_regions": {
            "interior": _outside_interior_evidence(
                selected_interior,
                before_sets[-1],
                after_sets[-1],
                reference_sets[-1],
            ),
            "exterior": _outside_exterior_evidence(
                before_exterior,
                after_exterior,
                selected_exterior,
                selected_exterior_depth,
            ),
        },
    }
    document["identity"] = {
        "region_diff_sha256": hashlib.sha256(
            _DIFF_DIGEST_DOMAIN + _json_bytes(document)
        ).hexdigest()
    }
    validate_region_diff_contract(document)
    data = _json_bytes(document)
    destination = Path(output)
    return RegionDiffResult(document, _publish_no_clobber(destination, data))


def _publish_no_clobber(destination: Path, data: bytes) -> bool:
    """Atomically publish without replacing a concurrent winner."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".tmp-region-diff-{uuid.uuid4().hex}"
    try:
        with stage.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(stage, destination)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file():
                raise OctreeError("Region Diff output conflicts with another artifact")
            try:
                if destination.read_bytes() == data:
                    return True
            except OSError as exc:
                raise OctreeError("cannot read existing Region Diff output") from exc
            raise OctreeError(
                "Region Diff output already exists with a different identity"
            )
        except OSError as exc:
            raise OctreeError("cannot atomically publish Region Diff output") from exc
        return False
    finally:
        stage.unlink(missing_ok=True)


def validate_region_diff_contract(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate one closed Region Diff with the common invalid-state class."""

    try:
        _reject_forbidden_evidence_fields(value)
        _validate_nested_region_diff_contract(value)
        return _validate_region_diff_identity(value)
    except UnsupportedOrInvalidVoxBlameState:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        _contract_fail("$", str(exc))


def _validate_region_diff_identity(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the public Region Diff shape and its frozen-plan identity."""

    root_fields = {
        "schema",
        "coordinate_contract",
        "max_depth",
        "from_step",
        "to_step",
        "repair_batch",
        "measurement_trajectory",
        "selected_regions",
        "batch_union",
        "outside_selected_regions",
        "identity",
    }
    if not isinstance(value, Mapping) or set(value) != root_fields:
        raise OctreeError("Region Diff has an unsupported shape")
    if (
        value["schema"] != REGION_DIFF_SCHEMA
        or value["coordinate_contract"] != COORDINATE_CONTRACT
        or value["max_depth"] != MAX_DEPTH
    ):
        raise OctreeError("Region Diff contract identity is unsupported")
    from_step = value["from_step"]
    to_step = value["to_step"]
    if (
        not isinstance(from_step, int)
        or isinstance(from_step, bool)
        or not isinstance(to_step, int)
        or isinstance(to_step, bool)
        or from_step < 0
        or to_step <= from_step
    ):
        raise OctreeError("Region Diff step identities are invalid")

    batch = value["repair_batch"]
    if not isinstance(batch, Mapping) or set(batch) != {
        "schema",
        "plan_sha256",
        "from_step",
        "selected_targets",
        "planned_edits",
    }:
        raise OctreeError("Region Diff Repair Batch identity is invalid")
    if (
        batch["schema"] != REPAIR_BATCH_SCHEMA
        or batch["from_step"] != from_step
        or not _is_sha256(batch["plan_sha256"])
    ):
        raise OctreeError("Region Diff Repair Batch identity is invalid")
    selected = batch["selected_targets"]
    edits = batch["planned_edits"]
    if not isinstance(selected, list) or not selected:
        raise OctreeError("Region Diff must bind selected Repair Targets")
    selected_keys: list[str] = []
    selected_digests: dict[str, str] = {}
    for target in selected:
        if not isinstance(target, Mapping) or set(target) != {
            "target_key",
            "kind",
            "mask_sha256",
        }:
            raise OctreeError("Region Diff selected target identity is invalid")
        key = target["target_key"]
        if (
            not isinstance(key, str)
            or not key.startswith(f"step-{from_step:06d}:")
            or key in selected_digests
            or target["kind"] not in ("interior", "exterior")
            or not _is_sha256(target["mask_sha256"])
        ):
            raise OctreeError("Region Diff selected target identity is invalid")
        selected_keys.append(key)
        selected_digests[key] = target["mask_sha256"]
    if not isinstance(edits, list) or not edits:
        raise OctreeError("Region Diff must bind Planned Edits")
    edit_keys: set[str] = set()
    mapped: set[str] = set()
    for edit in edits:
        if not isinstance(edit, Mapping) or set(edit) != {"edit_key", "target_keys"}:
            raise OctreeError("Region Diff Planned Edit identity is invalid")
        key = edit["edit_key"]
        target_keys = edit["target_keys"]
        if (
            not isinstance(key, str)
            or not _EDIT_KEY.fullmatch(key)
            or key in edit_keys
            or not isinstance(target_keys, list)
            or not target_keys
            or len(set(target_keys)) != len(target_keys)
            or any(target_key not in selected_digests for target_key in target_keys)
        ):
            raise OctreeError("Region Diff Planned Edit mapping is invalid")
        edit_keys.add(key)
        mapped.update(target_keys)
    if mapped != set(selected_keys):
        raise OctreeError("Region Diff Planned Edits do not cover selected targets")

    regions = value["selected_regions"]
    if (
        not isinstance(regions, list)
        or any(not isinstance(item, Mapping) for item in regions)
        or [item.get("target_key") for item in regions] != selected_keys
    ):
        raise OctreeError("Region Diff selected-region order is invalid")
    for region in regions:
        key = region["target_key"]
        if (
            not isinstance(region, Mapping)
            or set(region) != {
                "target_key",
                "kind",
                "exact_mask",
                "interior",
                "exterior",
            }
            or region["exact_mask"].get("logical_sha256") != selected_digests[key]
        ):
            raise OctreeError("Region Diff selected-region identity is invalid")
    trajectory = value["measurement_trajectory"]
    if (
        not isinstance(trajectory, Mapping)
        or trajectory.get("steps") != [from_step, to_step]
    ):
        raise OctreeError("Region Diff trajectory does not match its explicit edge")
    batch_union = value["batch_union"]
    if (
        not isinstance(batch_union, Mapping)
        or batch_union.get("selected_target_keys") != selected_keys
    ):
        raise OctreeError("Region Diff batch union does not match selected targets")

    identity = value["identity"]
    if not isinstance(identity, Mapping) or set(identity) != {"region_diff_sha256"}:
        raise OctreeError("Region Diff artifact identity is invalid")
    expected_document = dict(value)
    expected_document.pop("identity")
    expected = hashlib.sha256(
        _DIFF_DIGEST_DOMAIN + _json_bytes(expected_document)
    ).hexdigest()
    if identity["region_diff_sha256"] != expected:
        raise OctreeError("Region Diff artifact identity mismatch")
    return value


def _validate_nested_region_diff_contract(value: Mapping[str, Any]) -> None:
    _closed(
        value,
        {
            "schema",
            "coordinate_contract",
            "max_depth",
            "from_step",
            "to_step",
            "repair_batch",
            "measurement_trajectory",
            "selected_regions",
            "batch_union",
            "outside_selected_regions",
            "identity",
        },
        "$",
    )
    _validate_trajectory(value["measurement_trajectory"], "$.measurement_trajectory")
    regions = _array(value["selected_regions"], "$.selected_regions")
    for index, region in enumerate(regions):
        _validate_selected_region(region, f"$.selected_regions[{index}]")
    _validate_batch_union(value["batch_union"], "$.batch_union")
    _validate_outside_regions(
        value["outside_selected_regions"], "$.outside_selected_regions"
    )
    _closed(value["identity"], {"region_diff_sha256"}, "$.identity")


def _validate_trajectory(value: Any, path: str) -> None:
    trajectory = _closed(
        value,
        {"steps", "errors_by_depth", "observable_geometry", "exterior_surface"},
        path,
    )
    _array(trajectory["steps"], f"{path}.steps")
    depths = _array(trajectory["errors_by_depth"], f"{path}.errors_by_depth")
    for index, depth in enumerate(depths):
        item_path = f"{path}.errors_by_depth[{index}]"
        item = _closed(depth, {"depth", "before", "after", "delta"}, item_path)
        _metric_triplet(item, _NATIVE_METRIC_FIELDS, item_path, {"depth"})
    _closed(
        trajectory["observable_geometry"],
        {"before_sha256", "after_sha256", "changed"},
        f"{path}.observable_geometry",
    )
    exterior = _closed(
        trajectory["exterior_surface"],
        {"before", "after"},
        f"{path}.exterior_surface",
    )
    _validate_exterior_summary(exterior["before"], f"{path}.exterior_surface.before")
    _validate_exterior_summary(exterior["after"], f"{path}.exterior_surface.after")


def _validate_selected_region(value: Any, path: str) -> None:
    region = _closed(
        value,
        {"target_key", "kind", "exact_mask", "interior", "exterior"},
        path,
    )
    _closed(region["exact_mask"], _MASK_IDENTITY_FIELDS, f"{path}.exact_mask")
    if region["kind"] == "interior":
        _validate_interior_evidence(region["interior"], f"{path}.interior")
        if region["exterior"] is not None:
            _contract_fail(f"{path}.exterior", "must be null for an interior target")
    elif region["kind"] == "exterior":
        if region["interior"] is not None:
            _contract_fail(f"{path}.interior", "must be null for an exterior target")
        _validate_exterior_evidence(region["exterior"], f"{path}.exterior")
    else:
        _contract_fail(f"{path}.kind", "must be interior or exterior")


def _validate_batch_union(value: Any, path: str) -> None:
    union = _closed(
        value, {"selected_target_keys", "interior", "exterior"}, path
    )
    _array(union["selected_target_keys"], f"{path}.selected_target_keys")
    if union["interior"] is not None:
        _validate_interior_evidence(union["interior"], f"{path}.interior")
    if union["exterior"] is not None:
        _validate_exterior_evidence(union["exterior"], f"{path}.exterior")


def _validate_interior_evidence(value: Any, path: str) -> None:
    evidence = _closed(
        value,
        {"mask_depth8_cell_count", "errors_by_depth", "halo", "direction_transitions"},
        path,
    )
    depths = _array(evidence["errors_by_depth"], f"{path}.errors_by_depth")
    for index, depth in enumerate(depths):
        item_path = f"{path}.errors_by_depth[{index}]"
        item = _closed(depth, {"depth", "before", "after", "delta"}, item_path)
        _metric_triplet(item, _PROJECTED_METRIC_FIELDS, item_path, {"depth"})
    halo = _closed(
        evidence["halo"],
        {"grid_depth", "cell_count", "before", "after", "delta"},
        f"{path}.halo",
    )
    _metric_triplet(
        halo,
        _NATIVE_METRIC_FIELDS,
        f"{path}.halo",
        {"grid_depth", "cell_count"},
    )
    _validate_direction_transitions(
        evidence["direction_transitions"], f"{path}.direction_transitions"
    )


def _validate_exterior_evidence(value: Any, path: str) -> None:
    evidence = _closed(
        value, {"grid_depth", "exact_region", "halo", "containment"}, path
    )
    exact = _closed(
        evidence["exact_region"],
        {"cell_count", "before", "after", "delta"},
        f"{path}.exact_region",
    )
    _metric_triplet(
        exact, _EXTERIOR_METRIC_FIELDS, f"{path}.exact_region", {"cell_count"}
    )
    halo = _closed(
        evidence["halo"],
        {"grid_depth", "cell_count", "before", "after", "delta"},
        f"{path}.halo",
    )
    _metric_triplet(
        halo,
        _EXTERIOR_METRIC_FIELDS,
        f"{path}.halo",
        {"grid_depth", "cell_count"},
    )
    containment = _closed(
        evidence["containment"], {"before", "after"}, f"{path}.containment"
    )
    _validate_exterior_exact(containment["before"], f"{path}.containment.before")
    _validate_exterior_exact(containment["after"], f"{path}.containment.after")


def _validate_outside_regions(value: Any, path: str) -> None:
    outside = _closed(value, {"interior", "exterior"}, path)
    interior_path = f"{path}.interior"
    interior = _closed(
        outside["interior"],
        {
            "before",
            "after",
            "delta",
            "direction_transitions",
            "new_missing_surface_count",
            "new_excess_surface_count",
            "new_surface_error_count",
            "largest_new_components",
        },
        interior_path,
    )
    _metric_triplet(interior, _NATIVE_METRIC_FIELDS, interior_path, {
        "direction_transitions",
        "new_missing_surface_count",
        "new_excess_surface_count",
        "new_surface_error_count",
        "largest_new_components",
    })
    _validate_direction_transitions(
        interior["direction_transitions"], f"{interior_path}.direction_transitions"
    )
    _validate_components(
        interior["largest_new_components"],
        f"{interior_path}.largest_new_components",
    )

    exterior_path = f"{path}.exterior"
    exterior = _closed(
        outside["exterior"],
        {
            "grid_depth",
            "before",
            "after",
            "delta",
            "new_excess_surface_count",
            "largest_new_components",
        },
        exterior_path,
    )
    _metric_triplet(exterior, _EXTERIOR_METRIC_FIELDS, exterior_path, {
        "grid_depth",
        "new_excess_surface_count",
        "largest_new_components",
    })
    _validate_components(
        exterior["largest_new_components"],
        f"{exterior_path}.largest_new_components",
    )


def _validate_components(value: Any, path: str) -> None:
    components = _array(value, path)
    if len(components) > 3:
        _contract_fail(path, "must contain at most three components")
    for index, component in enumerate(components):
        component_path = f"{path}[{index}]"
        item = _closed(
            component,
            {
                "missing_surface_count",
                "excess_surface_count",
                "surface_error_count",
                "bounds_canonical",
            },
            component_path,
        )
        _validate_bounds(item["bounds_canonical"], f"{component_path}.bounds_canonical")


def _validate_direction_transitions(value: Any, path: str) -> None:
    transitions = _closed(
        value, {"missing_to_excess", "excess_to_missing"}, path
    )
    _closed(
        transitions["missing_to_excess"],
        {"before_missing_not_after_count", "after_excess_not_before_count"},
        f"{path}.missing_to_excess",
    )
    _closed(
        transitions["excess_to_missing"],
        {"before_excess_not_after_count", "after_missing_not_before_count"},
        f"{path}.excess_to_missing",
    )


def _validate_exterior_summary(value: Any, path: str) -> None:
    exterior = _closed(value, _EXTERIOR_SUMMARY_FIELDS, path)
    bounds = exterior["bounds_canonical"]
    if bounds is not None:
        _validate_bounds(bounds, f"{path}.bounds_canonical")


def _validate_exterior_exact(value: Any, path: str) -> None:
    exact = _closed(value, _EXTERIOR_EXACT_FIELDS, path)
    bounds = exact["bounds_canonical"]
    if bounds is not None:
        _validate_bounds(bounds, f"{path}.bounds_canonical")


def _validate_bounds(value: Any, path: str) -> None:
    bounds = _closed(value, {"min", "max"}, path)
    _array(bounds["min"], f"{path}.min")
    _array(bounds["max"], f"{path}.max")


def _metric_triplet(
    value: Mapping[str, Any],
    metric_fields: frozenset[str],
    path: str,
    extra_fields: set[str],
) -> None:
    _closed(value, {"before", "after", "delta"} | extra_fields, path)
    for label in ("before", "after", "delta"):
        _closed(value[label], metric_fields, f"{path}.{label}")


def _reject_forbidden_evidence_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if key in _FORBIDDEN_EVIDENCE_FIELDS:
                _contract_fail(item_path, "repair verdict labels are forbidden")
            _reject_forbidden_evidence_fields(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_evidence_fields(item, f"{path}[{index}]")


def _closed(
    value: Any, fields: set[str] | frozenset[str], path: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _contract_fail(path, "must be an object")
    if set(value) != set(fields):
        _contract_fail(path, "has unknown or missing fields")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _contract_fail(path, "must be an array")
    return value


def _contract_fail(path: str, detail: str) -> None:
    raise UnsupportedOrInvalidVoxBlameState(path=path, detail=detail)


_MASK_IDENTITY_FIELDS = frozenset(
    {"storage_schema", "logical_sha256", "region_count"}
)
_NATIVE_METRIC_FIELDS = frozenset(
    {
        "missing_surface_count",
        "excess_surface_count",
        "union_surface_count",
        "surface_error_count",
        "surface_error_rate",
    }
)
_PROJECTED_METRIC_FIELDS = frozenset(
    {
        "missing_depth8_equivalent_count",
        "excess_depth8_equivalent_count",
        "union_depth8_equivalent_count",
        "surface_error_depth8_equivalent_count",
        "projected_surface_error_rate",
    }
)
_EXTERIOR_METRIC_FIELDS = frozenset({"excess_surface_count"})
_EXTERIOR_EXACT_FIELDS = frozenset(
    {
        "surface_present",
        "bounds_canonical",
        "centroid_canonical",
        "nearest_overrun",
        "farthest_overrun",
        "outside_directions",
    }
)
_EXTERIOR_SUMMARY_FIELDS = frozenset(
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


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _load_measurement(workspace: Path, step: int) -> dict[str, Any]:
    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        raise OctreeError("Region Diff step must be a non-negative integer")
    path = workspace / "steps" / f"{step:06d}" / "measurement.json"
    try:
        measurement = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError(f"Measured Step {step} is unavailable") from exc
    validate_measurement_contract(measurement)
    page_repair_targets(workspace, step=step)
    if measurement["step"] != step:
        raise OctreeError("Measured Step identity does not match its path")
    return measurement


def _load_repair_plan(
    repair_plan: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(repair_plan, Mapping):
        return dict(repair_plan)
    try:
        value = json.loads(Path(repair_plan).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("Repair Batch plan is unavailable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise OctreeError("Repair Batch plan must be a JSON object")
    return value


def _validate_repair_batch(
    plan: dict[str, Any], *, from_step: int, measurement: dict[str, Any]
) -> list[dict[str, Any]]:
    expected = {
        "schema",
        "from_step",
        "selected_targets",
        "planned_edits",
        "rationale",
        "preview_observation",
    }
    if set(plan) != expected or plan.get("schema") != REPAIR_BATCH_SCHEMA:
        raise OctreeError("Repair Batch plan has an unsupported shape")
    if plan.get("from_step") != from_step:
        raise OctreeError("Repair Batch from_step does not match Region Diff")
    _short_text(plan["rationale"], "rationale")
    _short_text(plan["preview_observation"], "preview_observation")

    selected = plan["selected_targets"]
    edits = plan["planned_edits"]
    if not isinstance(selected, list) or not selected:
        raise OctreeError("Repair Batch must select one or more Repair Targets")
    if not isinstance(edits, list) or not edits:
        raise OctreeError("Repair Batch must contain one or more Planned Edits")
    current = {
        target["target_key"]: target
        for target in measurement["repair_targets"]["ordered_targets"]
    }
    resolved = []
    selected_keys: set[str] = set()
    for item in selected:
        if not isinstance(item, dict) or set(item) != {"target_key", "mask_sha256"}:
            raise OctreeError("Repair Batch selected target has an unsupported shape")
        key = item["target_key"]
        if not isinstance(key, str) or key in selected_keys or key not in current:
            raise OctreeError("Repair Batch selected target is not current and unique")
        target = current[key]
        if item["mask_sha256"] != target["mask"]["logical_sha256"]:
            raise OctreeError("Repair Batch selected target mask identity is stale")
        selected_keys.add(key)
        resolved.append(target)

    edit_keys: set[str] = set()
    mapped_targets: set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {
            "edit_key",
            "target_keys",
            "description",
        }:
            raise OctreeError("Planned Edit has an unsupported shape")
        edit_key = edit["edit_key"]
        if (
            not isinstance(edit_key, str)
            or not _EDIT_KEY.fullmatch(edit_key)
            or edit_key in edit_keys
        ):
            raise OctreeError("Planned Edit keys must be stable and unique")
        edit_keys.add(edit_key)
        _short_text(edit["description"], "Planned Edit description")
        target_keys = edit["target_keys"]
        if (
            not isinstance(target_keys, list)
            or not target_keys
            or len(set(target_keys)) != len(target_keys)
            or any(key not in selected_keys for key in target_keys)
        ):
            raise OctreeError("Planned Edit mappings must name selected targets")
        mapped_targets.update(target_keys)
    if mapped_targets != selected_keys:
        raise OctreeError("Every selected Repair Target must map to a Planned Edit")
    return resolved


def _short_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 2_000:
        raise OctreeError(f"Repair Batch {label} must be short non-empty text")


def _step_sets(workspace: Path, step: int) -> list[set[int]]:
    tree = read_surface_tree(
        workspace / "steps" / f"{step:06d}" / "candidate.vbsvo"
    )
    return _occupancy_by_depth(tree)


def _occupancy_by_depth(tree: SurfaceTree) -> list[set[int]]:
    leaves = {int(code) for code in tree.iter_leaf_codes()}
    return [
        {code >> (3 * (MAX_DEPTH - depth)) for code in leaves}
        for depth in range(1, MAX_DEPTH + 1)
    ]


def _read_interior_mask(workspace: Path, target: dict[str, Any]) -> set[int]:
    path = workspace.parent / Path(target["mask"]["path"])
    try:
        return _expand_region_set(path.read_bytes())
    except OSError as exc:
        raise OctreeError("Repair Target mask artifact is unavailable") from exc


def _read_exterior_mask(
    workspace: Path, target: dict[str, Any]
) -> tuple[int, set[tuple[int, int, int]]]:
    path = workspace.parent / Path(target["mask"]["path"])
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if set(value) != {"schema", "diagnostic_grid_depth", "cells"}:
            raise ValueError("unsupported shape")
        depth = value["diagnostic_grid_depth"]
        cells = {tuple(cell) for cell in value["cells"]}
        if (
            value["schema"] != "exterior_grid_region_set/1"
            or not isinstance(depth, int)
            or isinstance(depth, bool)
            or len(cells) != len(value["cells"])
            or any(
                len(cell) != 3
                or any(
                    not isinstance(item, int) or isinstance(item, bool)
                    for item in cell
                )
                for cell in cells
            )
        ):
            raise ValueError("invalid values")
    except (
        OSError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise OctreeError(
            "exterior Repair Target mask is unavailable or invalid"
        ) from exc
    return depth, cells


def _interior_region_evidence(
    cells: set[int],
    reference_sets: list[set[int]],
    before_sets: list[set[int]],
    after_sets: list[set[int]],
) -> dict[str, Any]:
    halo = _interior_halo(cells)
    comparison_region = cells | halo
    return {
        "mask_depth8_cell_count": len(cells),
        "errors_by_depth": [
            _projected_depth_evidence(
                cells,
                depth,
                reference_sets[depth - 1],
                before_sets[depth - 1],
                after_sets[depth - 1],
            )
            for depth in range(1, MAX_DEPTH + 1)
        ],
        "halo": {
            "grid_depth": MAX_DEPTH,
            "cell_count": len(halo),
            **_native_region_evidence(
                halo, reference_sets[-1], before_sets[-1], after_sets[-1]
            ),
        },
        "direction_transitions": _direction_transitions(
            comparison_region,
            reference_sets[-1],
            before_sets[-1],
            after_sets[-1],
        ),
    }


def _projected_depth_evidence(
    cells: set[int],
    depth: int,
    reference: set[int],
    before: set[int],
    after: set[int],
) -> dict[str, Any]:
    shift = 3 * (MAX_DEPTH - depth)
    ancestors = {cell: cell >> shift for cell in cells}
    before_metrics = _projected_metrics(ancestors, reference, before)
    after_metrics = _projected_metrics(ancestors, reference, after)
    return {
        "depth": depth,
        "before": before_metrics,
        "after": after_metrics,
        "delta": _metric_delta(before_metrics, after_metrics),
    }


def _projected_metrics(
    ancestors: dict[int, int], reference: set[int], candidate: set[int]
) -> dict[str, Any]:
    missing = reference - candidate
    excess = candidate - reference
    union = reference | candidate
    directions = {
        cell: (
            "missing"
            if ancestor in missing
            else "excess"
            if ancestor in excess
            else "none"
        )
        for cell, ancestor in ancestors.items()
    }
    missing_count = sum(value == "missing" for value in directions.values())
    excess_count = sum(value == "excess" for value in directions.values())
    union_count = sum(ancestor in union for ancestor in ancestors.values())
    error_count = missing_count + excess_count
    return {
        "missing_depth8_equivalent_count": missing_count,
        "excess_depth8_equivalent_count": excess_count,
        "union_depth8_equivalent_count": union_count,
        "surface_error_depth8_equivalent_count": error_count,
        "projected_surface_error_rate": (
            error_count / union_count if union_count else 0.0
        ),
    }


def _native_region_evidence(
    cells: set[int], reference: set[int], before: set[int], after: set[int]
) -> dict[str, Any]:
    before_metrics = _native_metrics(cells, reference, before)
    after_metrics = _native_metrics(cells, reference, after)
    return {
        "before": before_metrics,
        "after": after_metrics,
        "delta": _metric_delta(before_metrics, after_metrics),
    }


def _native_metrics(
    cells: set[int], reference: set[int], candidate: set[int]
) -> dict[str, Any]:
    projected = _projected_metrics(
        {cell: cell for cell in cells}, reference, candidate
    )
    return {
        "missing_surface_count": projected["missing_depth8_equivalent_count"],
        "excess_surface_count": projected["excess_depth8_equivalent_count"],
        "union_surface_count": projected["union_depth8_equivalent_count"],
        "surface_error_count": projected["surface_error_depth8_equivalent_count"],
        "surface_error_rate": projected["projected_surface_error_rate"],
    }


def _direction_transitions(
    cells: set[int], reference: set[int], before: set[int], after: set[int]
) -> dict[str, Any]:
    before_missing = (reference - before) & cells
    before_excess = (before - reference) & cells
    after_missing = (reference - after) & cells
    after_excess = (after - reference) & cells
    before_errors = before_missing | before_excess
    return {
        "missing_to_excess": {
            "before_missing_not_after_count": len(before_missing - after_missing),
            "after_excess_not_before_count": len(after_excess - before_errors),
        },
        "excess_to_missing": {
            "before_excess_not_after_count": len(before_excess - after_excess),
            "after_missing_not_before_count": len(after_missing - before_errors),
        },
    }


def _metric_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {key: after[key] - before[key] for key in before}


def _interior_halo(cells: set[int]) -> set[int]:
    size = 1 << MAX_DEPTH
    halo: set[int] = set()
    for cell in cells:
        x, y, z = decode_octant_prefix(cell, MAX_DEPTH)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    point = (x + dx, y + dy, z + dz)
                    if (
                        (dx or dy or dz)
                        and all(0 <= coordinate < size for coordinate in point)
                    ):
                        halo.add(_encode_octant_prefix(*point, MAX_DEPTH))
    return halo - cells


def _encode_octant_prefix(x: int, y: int, z: int, depth: int) -> int:
    code = 0
    for shift in range(depth - 1, -1, -1):
        code = (code << 3) | (
            (((x >> shift) & 1) << 2)
            | (((y >> shift) & 1) << 1)
            | ((z >> shift) & 1)
        )
    return code


def _measurement_trajectory(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    depths = []
    for before_depth, after_depth in zip(
        before["errors_by_depth"], after["errors_by_depth"], strict=True
    ):
        before_counts = _global_counts(before_depth)
        after_counts = _global_counts(after_depth)
        depths.append(
            {
                "depth": before_depth["depth"],
                "before": before_counts,
                "after": after_counts,
                "delta": _metric_delta(before_counts, after_counts),
            }
        )
    before_observable = before["measurement"]["observable_sha256"]
    after_observable = after["measurement"]["observable_sha256"]
    return {
        "steps": [before["step"], after["step"]],
        "errors_by_depth": depths,
        "observable_geometry": {
            "before_sha256": before_observable,
            "after_sha256": after_observable,
            "changed": before_observable != after_observable,
        },
        "exterior_surface": {
            "before": before["exterior_surface"],
            "after": after["exterior_surface"],
        },
    }


def _global_counts(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "missing_surface_count",
            "excess_surface_count",
            "union_surface_count",
            "surface_error_count",
            "surface_error_rate",
        )
    }


def _outside_interior_evidence(
    selected: set[int], before: set[int], after: set[int], reference: set[int]
) -> dict[str, Any]:
    all_cells = (reference | before | after) - selected
    evidence = _native_region_evidence(all_cells, reference, before, after)
    evidence["direction_transitions"] = _direction_transitions(
        all_cells, reference, before, after
    )
    before_errors = (reference - before) | (before - reference)
    after_missing = (reference - after) - selected
    after_excess = (after - reference) - selected
    new_missing = after_missing - before_errors
    new_excess = after_excess - before_errors
    evidence["new_missing_surface_count"] = len(new_missing)
    evidence["new_excess_surface_count"] = len(new_excess)
    evidence["new_surface_error_count"] = len(new_missing) + len(new_excess)
    components = []
    for component in _interior_components(new_missing | new_excess):
        components.append(
            {
                "missing_surface_count": len(component & new_missing),
                "excess_surface_count": len(component & new_excess),
                "surface_error_count": len(component),
                "bounds_canonical": _interior_bounds(component),
            }
        )
    components.sort(
        key=lambda item: (
            -item["surface_error_count"],
            tuple(item["bounds_canonical"]["min"]),
            tuple(item["bounds_canonical"]["max"]),
        )
    )
    evidence["largest_new_components"] = components[:3]
    return evidence


def _interior_components(cells: set[int]) -> list[set[int]]:
    coordinates = {
        decode_octant_prefix(cell, MAX_DEPTH): cell for cell in cells
    }
    offsets = tuple(
        (dx, dy, dz)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        for dz in (-1, 0, 1)
        if 0 < abs(dx) + abs(dy) + abs(dz) <= 2
    )

    def neighbors(current: int) -> Iterable[int]:
        x, y, z = decode_octant_prefix(current, MAX_DEPTH)
        return (
            neighbor
            for dx, dy, dz in offsets
            if (neighbor := coordinates.get((x + dx, y + dy, z + dz)))
            is not None
        )

    return _connected_components(cells, neighbors)


def _interior_bounds(cells: set[int]) -> dict[str, list[float]]:
    coordinates = [decode_octant_prefix(cell, MAX_DEPTH) for cell in cells]
    scale = 1 << MAX_DEPTH
    return {
        "min": [
            -0.5 + min(point[axis] for point in coordinates) / scale
            for axis in range(3)
        ],
        "max": [
            -0.5 + (max(point[axis] for point in coordinates) + 1) / scale
            for axis in range(3)
        ],
    }


def _exterior_region_evidence(
    cells: set[tuple[int, int, int]],
    depth: int,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    halo = _exterior_halo(cells)
    return {
        "grid_depth": depth,
        "exact_region": {
            "cell_count": len(cells),
            **_exterior_fixed_region_counts(cells, depth, before, after),
        },
        "halo": {
            "grid_depth": depth,
            "cell_count": len(halo),
            **_exterior_fixed_region_counts(halo, depth, before, after),
        },
        "containment": {
            "before": before["exact"],
            "after": after["exact"],
        },
    }


def _exterior_fixed_region_counts(
    cells: set[tuple[int, int, int]],
    depth: int,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_count = len(_exterior_occupied_cells(cells, depth, before))
    after_count = len(_exterior_occupied_cells(cells, depth, after))
    return {
        "before": {"excess_surface_count": before_count},
        "after": {"excess_surface_count": after_count},
        "delta": {"excess_surface_count": after_count - before_count},
    }


def _outside_exterior_evidence(
    before: dict[str, Any],
    after: dict[str, Any],
    selected: set[tuple[int, int, int]],
    selected_depth: int | None,
) -> dict[str, Any]:
    depth = (
        min(before["depth"], after["depth"])
        if selected_depth is None
        else selected_depth
    )
    before_total = _exterior_equivalent_count(before, depth)
    after_total = _exterior_equivalent_count(after, depth)
    before_selected = len(_exterior_occupied_cells(selected, depth, before))
    after_selected = len(_exterior_occupied_cells(selected, depth, after))
    before_outside = before_total - before_selected
    after_outside = after_total - after_selected
    before_outside_cells = _project_exterior_cells(before, depth) - selected
    overlap = len(
        _exterior_occupied_cells(before_outside_cells, depth, after)
    )
    after_outside_cells = _project_exterior_cells(after, depth) - selected
    new_cells = after_outside_cells - before_outside_cells
    components = _exterior_components(new_cells)
    components.sort(
        key=lambda component: (
            -len(component),
            min(component),
        )
    )
    largest_new_components = [
        {
            "missing_surface_count": 0,
            "excess_surface_count": len(component),
            "surface_error_count": len(component),
            "bounds_canonical": _exterior_bounds(component, depth),
        }
        for component in components[:3]
    ]
    return {
        "grid_depth": depth,
        "before": {"excess_surface_count": before_outside},
        "after": {"excess_surface_count": after_outside},
        "delta": {"excess_surface_count": after_outside - before_outside},
        "new_excess_surface_count": after_outside - overlap,
        "largest_new_components": largest_new_components,
    }


def _load_exterior_snapshot(workspace: Path, step: int) -> dict[str, Any]:
    path = workspace / "steps" / f"{step:06d}" / "exterior.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {
            "depth": value["resolution"]["diagnostic_grid_depth"],
            "cells": {tuple(cell) for cell in value["cells"]},
            "exact": value["exact"],
        }
    except (
        OSError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise OctreeError("exterior Region Diff evidence is unavailable") from exc


def _exterior_halo(
    cells: set[tuple[int, int, int]],
) -> set[tuple[int, int, int]]:
    return {
        (x + dx, y + dy, z + dz)
        for x, y, z in cells
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        for dz in (-1, 0, 1)
        if dx or dy or dz
    } - cells


def _exterior_components(
    cells: set[tuple[int, int, int]],
) -> list[set[tuple[int, int, int]]]:
    offsets = tuple(
        (dx, dy, dz)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        for dz in (-1, 0, 1)
        if dx or dy or dz
    )
    return _connected_components(
        cells,
        lambda cell: (
            (cell[0] + dx, cell[1] + dy, cell[2] + dz)
            for dx, dy, dz in offsets
        ),
    )


def _connected_components(
    cells: set[_Cell], neighbors: Callable[[_Cell], Iterable[_Cell]]
) -> list[set[_Cell]]:
    remaining = set(cells)
    components: list[set[_Cell]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        queue = [seed]
        while queue:
            for neighbor in neighbors(queue.pop()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return components


def _exterior_bounds(
    cells: set[tuple[int, int, int]], depth: int
) -> dict[str, list[float]]:
    cell_size = 2.0**-depth
    return {
        "min": [
            -0.5 + min(cell[axis] for cell in cells) * cell_size
            for axis in range(3)
        ],
        "max": [
            -0.5 + (max(cell[axis] for cell in cells) + 1) * cell_size
            for axis in range(3)
        ],
    }


def _exterior_occupied_cells(
    cells: set[tuple[int, int, int]],
    depth: int,
    snapshot: dict[str, Any],
) -> set[tuple[int, int, int]]:
    snapshot_depth = snapshot["depth"]
    if snapshot_depth < depth:
        raise OctreeError(
            "coarser exterior evidence cannot be compared at a frozen grid depth"
        )
    return cells & _project_exterior_cells(snapshot, depth)


def _exterior_equivalent_count(snapshot: dict[str, Any], depth: int) -> int:
    snapshot_depth = snapshot["depth"]
    if snapshot_depth < depth:
        raise OctreeError(
            "coarser exterior evidence cannot be compared at a frozen grid depth"
        )
    return len(_project_exterior_cells(snapshot, depth))


def _project_exterior_cells(
    snapshot: dict[str, Any], depth: int
) -> set[tuple[int, int, int]]:
    snapshot_depth = snapshot["depth"]
    if snapshot_depth == depth:
        return set(snapshot["cells"])
    if snapshot_depth < depth:
        raise OctreeError(
            "coarser exterior evidence cannot be compared at a frozen grid depth"
        )
    factor = 1 << (snapshot_depth - depth)
    return {
        tuple(coordinate // factor for coordinate in cell)
        for cell in snapshot["cells"]
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


__all__ = [
    "REGION_DIFF_SCHEMA",
    "REPAIR_BATCH_SCHEMA",
    "RegionDiffResult",
    "publish_region_diff",
    "validate_region_diff_contract",
]
