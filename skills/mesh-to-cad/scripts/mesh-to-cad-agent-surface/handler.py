"""Closed Agent Surface seam with supervisor-injected ports.

This module intentionally knows neither the Workspace implementation nor the
reference implementation.  The outer supervisor supplies ``SupervisorPorts``;
the Agent supplies only opaque handles and closed structured intent arguments.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol


REQUEST_SCHEMA = "mesh-to-cad.agent-intent/1"
RESPONSE_SCHEMA = "mesh-to-cad.agent-response/7"
ERROR_SCHEMA = "mesh-to-cad.agent-error/1"

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_REPAIR_STEP = 10
MAX_PARENT_STEP = MAX_REPAIR_STEP - 1

_HANDLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_HANDLE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_UNSUPPORTED_OBSERVATIONS = frozenset(
    {"components", "vertices", "faces", "triangles", "raw_bytes", "export", "raycast", "nearest_point"}
)


class SupervisorPorts(Protocol):
    """The only concrete dependency supplied by W4's trusted supervisor."""

    def workspace_status(self, workspace_handle: str) -> Mapping[str, Any]: ...

    def start_attempt(
        self,
        workspace_handle: str,
        plan_handle: str,
        parent_step_handle: str | None,
    ) -> Mapping[str, Any]: ...

    def run_candidate_tool(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
        operation_handle: str,
    ) -> Mapping[str, Any]: ...

    def submit_step_zero(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
    ) -> Mapping[str, Any]: ...

    def submit_repair(
        self,
        workspace_handle: str,
        attempt_handle: str,
        draft_handle: str,
    ) -> Mapping[str, Any]: ...

    def evaluate_repair_draft(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
        evaluation_ticket: str,
    ) -> Mapping[str, Any]: ...

    def abandon_repair_attempt(
        self, workspace_handle: str, attempt_handle: str
    ) -> Mapping[str, Any]: ...

    def inspect_formal_preview(self, preview_handle: str) -> Mapping[str, Any]: ...

    def inspect_formal_preview_with_preview(
        self, preview_handle: str
    ) -> tuple[Mapping[str, Any], bytes | None]: ...

    def inspect_repair_targets(
        self, step_handle: str, offset: int
    ) -> Mapping[str, Any]: ...

    def observe_target_section(
        self, step_handle: str, rank: int
    ) -> Mapping[str, Any]: ...

    def target_section_requires_local_occupancy(
        self, step_handle: str, rank: int
    ) -> bool: ...

    def acknowledge_target_section_observation(
        self, step_handle: str, rank: int
    ) -> None: ...

    def select_and_finalize(
        self,
        workspace_handle: str,
        step_handle: str,
        selection_handle: str,
        notes_handle: str,
    ) -> Mapping[str, Any]: ...

    def observe_reference(
        self,
        reference_handle: str,
        observation: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def cancel(self) -> None: ...


class AgentSurfaceError(ValueError):
    """Stable error whose fields contain no host exception or path details."""

    def __init__(self, classification: str, path: str = "$", detail: str | None = None):
        self.classification = classification
        self.path = path
        self.detail = detail or classification
        super().__init__(classification)


def error_document(error: AgentSurfaceError) -> dict[str, Any]:
    return {
        "schema": ERROR_SCHEMA,
        "error": {
            "classification": error.classification,
            "path": error.path,
            "detail": error.detail,
        },
    }


def _fail(classification: str, path: str = "$.args", detail: str | None = None) -> None:
    raise AgentSurfaceError(classification, path, detail)


def _canonical_json(value: Any, *, classification: str, path: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError):
        _fail(classification, path)
    return encoded


def _closed(value: Any, fields: tuple[str, ...], path: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        _fail("invalid_request", path)
    return value


def _handle(value: Any, path: str) -> str:
    if type(value) is not str or _HANDLE.fullmatch(value) is None:
        _fail("invalid_handle", path)
    return value


def _step(value: Any, path: str, *, allow_none: bool = False, maximum: int | None = None) -> int | None:
    if allow_none and value is None:
        return None
    if type(value) is not int or isinstance(value, bool) or value < 0:
        _fail("invalid_request", path)
    if maximum is not None and value > maximum:
        _fail("budget_violation", path)
    return value


@dataclass(frozen=True)
class _FieldSpec:
    name: str
    kind: str


@dataclass(frozen=True)
class _OperationSpec:
    name: str
    variants: tuple[tuple[_FieldSpec, ...], ...]
    result: Callable[[Any, str], dict[str, Any]]
    description: str


_STATE_VALUES = {
    "workspace_status": ("ready", "preterminal", "terminal", "blocked"),
    "start_attempt": ("started", "active"),
    "run_candidate_tool": ("completed", "failed"),
    "submit_step_zero": ("published", "failed"),
    "submit_repair": ("published", "failed"),
    "evaluate_repair_draft": ("evaluated", "failed"),
    "abandon_repair_attempt": ("abandoned",),
    "inspect_formal_preview": ("available",),
    "select_and_finalize": ("finalized", "blocked"),
}


def _state(value: Any, operation: str, path: str) -> str:
    if type(value) is not str or value not in _STATE_VALUES[operation]:
        _fail("supervisor_contract_violation", path)
    return value


def _integer(value: Any, path: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        _fail("supervisor_contract_violation", path)
    if maximum is not None and value > maximum:
        _fail("supervisor_contract_violation", path)
    return value


def _signed_integer(value: Any, path: str) -> int:
    if type(value) is not int or isinstance(value, bool):
        _fail("supervisor_contract_violation", path)
    return value


def _number(value: Any, path: str) -> int | float:
    if type(value) is bool or type(value) not in {int, float}:
        _fail("supervisor_contract_violation", path)
    if type(value) is float and (value != value or value in {float("inf"), float("-inf")}):
        _fail("supervisor_contract_violation", path)
    return value


def _vector(value: Any, path: str) -> list[int | float]:
    if type(value) is not list or len(value) != 3:
        _fail("supervisor_contract_violation", path)
    return [_number(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _pair(value: Any, path: str) -> list[int | float]:
    if type(value) is not list or len(value) != 2:
        _fail("supervisor_contract_violation", path)
    return [_number(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _axis_values(value: Any, path: str) -> dict[str, int | float]:
    value = _closed(value, ("x", "y", "z"), path)
    return {axis: _number(value[axis], f"{path}.{axis}") for axis in ("x", "y", "z")}


def _handle_result(value: Any, path: str) -> str:
    if type(value) is not str or _HANDLE.fullmatch(value) is None:
        _fail("supervisor_contract_violation", path)
    return value


def _optional_handle_result(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _handle_result(value, path)


def _bounds_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(value, ("min", "max", "size"), path)
    return {
        key: _vector(value[key], f"{path}.{key}")
        for key in ("min", "max", "size")
    }


def _budgets_result(value: Any, path: str) -> dict[str, int]:
    value = _closed(
        value,
        (
            "remaining_cycles",
            "attempts_per_intended_step",
            "tool_failures_per_intended_step",
        ),
        path,
    )
    return {
        key: _integer(value[key], f"{path}.{key}")
        for key in (
            "remaining_cycles",
            "attempts_per_intended_step",
            "tool_failures_per_intended_step",
        )
    }


def _next_result(value: Any, path: str) -> list[str]:
    if type(value) is not list or len(value) > len(INTENTS):
        _fail("supervisor_contract_violation", path)
    if any(type(item) is not str or item not in INTENTS for item in value):
        _fail("supervisor_contract_violation", path)
    return list(value)


DECISION_FACTS_SCHEMA = "mesh-to-cad.decision-facts/2"
REPAIR_TARGET_PAGE_SCHEMA = "mesh-to-cad.repair-target-page/1"
TARGET_SECTION_OBSERVATION_SCHEMA = "mesh-to-cad.target-section-observation/3"
_DECISION_FACT_MAX_TARGETS = 8
_ACCEPTANCE_STATE_VALUES = ("acceptance_satisfied", "unaccepted")


def _rate(value: Any, path: str) -> float:
    number = _number(value, path)
    if number < 0 or number > 1:
        _fail("supervisor_contract_violation", path)
    return float(number)


def _residual_summary_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(
        value,
        (
            "repair_frontier",
        ),
        path,
    )
    frontier = _closed(
        value["repair_frontier"],
        (
            "active_depth",
            "missing_surface_count",
            "excess_surface_count",
            "surface_error_count",
            "surface_error_rate",
        ),
        f"{path}.repair_frontier",
    )
    active_depth = frontier["active_depth"]
    if active_depth is not None:
        active_depth = _integer(
            active_depth, f"{path}.repair_frontier.active_depth", maximum=8
        )
        if active_depth == 0:
            _fail("supervisor_contract_violation", f"{path}.repair_frontier.active_depth")
    return {
        "repair_frontier": {
            "active_depth": active_depth,
            "missing_surface_count": _integer(
                frontier["missing_surface_count"],
                f"{path}.repair_frontier.missing_surface_count",
            ),
            "excess_surface_count": _integer(
                frontier["excess_surface_count"],
                f"{path}.repair_frontier.excess_surface_count",
            ),
            "surface_error_count": _integer(
                frontier["surface_error_count"],
                f"{path}.repair_frontier.surface_error_count",
            ),
            "surface_error_rate": _rate(
                frontier["surface_error_rate"],
                f"{path}.repair_frontier.surface_error_rate",
            ),
        },
    }


def _repair_target_items(
    value: Any, path: str, expected: int, *, step: int
) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) != expected:
        _fail("supervisor_contract_violation", path)
    if expected > _DECISION_FACT_MAX_TARGETS:
        _fail("supervisor_contract_violation", path)
    items = []
    for index, item in enumerate(value):
        item = _closed(
            item,
            (
                "rank",
                "kind",
                "bounds_canonical",
            ),
            f"{path}[{index}]",
        )
        items.append(
            {
                "rank": _integer(item["rank"], f"{path}[{index}].rank"),
                "kind": _enum(
                    item["kind"],
                    ("missing", "excess", "exterior"),
                    f"{path}[{index}].kind",
                ),
                "bounds_canonical": _bounds_canonical_result(
                    item["bounds_canonical"], f"{path}[{index}].bounds_canonical"
                ),
            }
        )
    return items


def _bounds_canonical_result(value: Any, path: str) -> dict[str, list[int | float]]:
    bounds = _closed(value, ("min", "max"), path)
    minimum = _vector(bounds["min"], f"{path}.min")
    maximum = _vector(bounds["max"], f"{path}.max")
    if any(low > high for low, high in zip(minimum, maximum)):
        _fail("supervisor_contract_violation", path)
    return {"min": minimum, "max": maximum}


def _repair_targets_result(
    value: Any, path: str, *, step: int
) -> dict[str, Any] | None:
    if value is None:
        return None
    value = _closed(value, ("total", "returned", "remaining", "items"), path)
    returned = _integer(
        value["returned"], f"{path}.returned", maximum=_DECISION_FACT_MAX_TARGETS
    )
    return {
        "total": _integer(value["total"], f"{path}.total"),
        "returned": returned,
        "remaining": _integer(value["remaining"], f"{path}.remaining"),
        "items": _repair_target_items(
            value["items"], f"{path}.items", returned, step=step
        ),
    }


def _change_from_parent_result(value: Any, path: str) -> dict[str, Any] | None:
    if value is None:
        return None
    value = _closed(value, ("no_observable_geometry_change", "parent_accepted"), path)
    return {
        "no_observable_geometry_change": _bool(
            value["no_observable_geometry_change"],
            f"{path}.no_observable_geometry_change",
        ),
        "parent_accepted": _bool(value["parent_accepted"], f"{path}.parent_accepted"),
    }


def _decision_facts_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(
        value,
        (
            "schema",
            "step_ordinal",
            "parent_step_ordinal",
            "accepted",
            "acceptance_state",
            "residual_summary",
            "repair_targets",
            "change_from_parent",
        ),
        path,
    )
    if value["schema"] != DECISION_FACTS_SCHEMA:
        _fail("supervisor_contract_violation", f"{path}.schema")
    step_ordinal = _integer(
        value["step_ordinal"], f"{path}.step_ordinal", maximum=MAX_REPAIR_STEP
    )
    parent_value = value["parent_step_ordinal"]
    parent_ordinal = (
        None
        if parent_value is None
        else _integer(parent_value, f"{path}.parent_step_ordinal", maximum=MAX_PARENT_STEP)
    )
    accepted = _bool(value["accepted"], f"{path}.accepted")
    acceptance_state = _enum(
        value["acceptance_state"], _ACCEPTANCE_STATE_VALUES, f"{path}.acceptance_state"
    )
    if (accepted and acceptance_state != "acceptance_satisfied") or (
        not accepted and acceptance_state != "unaccepted"
    ):
        _fail("supervisor_contract_violation", f"{path}.acceptance_state")
    change = _change_from_parent_result(value["change_from_parent"], f"{path}.change_from_parent")
    if (parent_ordinal is None) is not (change is None):
        _fail("supervisor_contract_violation", f"{path}.change_from_parent")
    return {
        "schema": DECISION_FACTS_SCHEMA,
        "step_ordinal": step_ordinal,
        "parent_step_ordinal": parent_ordinal,
        "accepted": accepted,
        "acceptance_state": acceptance_state,
        "residual_summary": _residual_summary_result(
            value["residual_summary"], f"{path}.residual_summary"
        ),
        "repair_targets": _repair_targets_result(
            value["repair_targets"], f"{path}.repair_targets", step=step_ordinal
        ),
        "change_from_parent": change,
    }


def _pca_axes(value: Any, path: str) -> list[list[int | float]] | None:
    if value is None:
        return None
    if type(value) is not list or len(value) != 3:
        _fail("supervisor_contract_violation", path)
    return [_vector(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _summary_projection(value: Any, path: str) -> dict[str, Any]:
    value = _closed(
        value,
        ("schema", "coordinate_contract", "stats", "quality", "canonical_frame"),
        path,
    )
    if value["schema"] != "meshscope.reference-summary/1":
        _fail("supervisor_contract_violation", f"{path}.schema")
    if value["coordinate_contract"] != "trellis2_canonical/1":
        _fail("supervisor_contract_violation", f"{path}.coordinate_contract")
    stats = _closed(
        value["stats"],
        ("vertices", "faces", "edges", "bounds", "surface_area", "volume"),
        f"{path}.stats",
    )
    quality = _closed(
        value["quality"],
        ("watertight", "volume_valid", "degenerate_faces", "euler_number"),
        f"{path}.quality",
    )
    frame = _closed(
        value["canonical_frame"],
        ("center", "status", "pca_axes", "eigenvalues"),
        f"{path}.canonical_frame",
    )
    _pca_axes(frame["pca_axes"], f"{path}.canonical_frame.pca_axes")
    return {
        "schema": value["schema"],
        "coordinate_contract": value["coordinate_contract"],
        "stats": {
            "vertices": _integer(stats["vertices"], f"{path}.stats.vertices"),
            "faces": _integer(stats["faces"], f"{path}.stats.faces"),
            "edges": _integer(stats["edges"], f"{path}.stats.edges"),
            "bounds": _bounds_result(stats["bounds"], f"{path}.stats.bounds"),
            "surface_area": _number(stats["surface_area"], f"{path}.stats.surface_area"),
            "volume": (
                None
                if stats["volume"] is None
                else _number(stats["volume"], f"{path}.stats.volume")
            ),
        },
        "quality": {
            "watertight": _bool(quality["watertight"], f"{path}.quality.watertight"),
            "volume_valid": _bool(quality["volume_valid"], f"{path}.quality.volume_valid"),
            "degenerate_faces": _integer(quality["degenerate_faces"], f"{path}.quality.degenerate_faces"),
            "euler_number": _signed_integer(quality["euler_number"], f"{path}.quality.euler_number"),
        },
        "canonical_frame": {
            "center": _vector(frame["center"], f"{path}.canonical_frame.center"),
            "status": _enum(frame["status"], ("stable", "ambiguous"), f"{path}.canonical_frame.status"),
            "eigenvalues": _vector(frame["eigenvalues"], f"{path}.canonical_frame.eigenvalues"),
        },
    }


def _section_profile_projection(value: Any, path: str) -> dict[str, Any]:
    value = _closed(value, ("schema", "coordinate_contract", "bin_count", "profiles"), path)
    if value["schema"] != "meshscope.reference-section-profile/1":
        _fail("supervisor_contract_violation", f"{path}.schema")
    if value["coordinate_contract"] != "trellis2_canonical/1":
        _fail("supervisor_contract_violation", f"{path}.coordinate_contract")
    if value["bin_count"] != 8 or type(value["bin_count"]) is not int:
        _fail("supervisor_contract_violation", f"{path}.bin_count")
    if type(value["profiles"]) is not list or len(value["profiles"]) != 3:
        _fail("supervisor_contract_violation", f"{path}.profiles")
    profiles = []
    for axis_index, axis in enumerate(("x", "y", "z")):
        profile_path = f"{path}.profiles[{axis_index}]"
        profile = _closed(value["profiles"][axis_index], ("axis", "occupied_axes", "slabs"), profile_path)
        occupied_axes = [item for item in ("x", "y", "z") if item != axis]
        if profile["axis"] != axis or profile["occupied_axes"] != occupied_axes or type(profile["slabs"]) is not list or len(profile["slabs"]) != 8:
            _fail("supervisor_contract_violation", profile_path)
        slabs = []
        for index, slab_value in enumerate(profile["slabs"]):
            slab_path = f"{profile_path}.slabs[{index}]"
            slab = _closed(
                slab_value,
                ("canonical_interval", "occupied_extents", "surface_area_fraction", "mean_abs_normal"),
                slab_path,
            )
            interval = _pair(slab["canonical_interval"], f"{slab_path}.canonical_interval")
            expected = [-0.5 + index / 8, -0.5 + (index + 1) / 8]
            if interval != expected:
                _fail("supervisor_contract_violation", f"{slab_path}.canonical_interval")
            occupied = slab["occupied_extents"]
            if occupied is not None:
                occupied = _closed(occupied, ("min", "max"), f"{slab_path}.occupied_extents")
                minimum = _pair(occupied["min"], f"{slab_path}.occupied_extents.min")
                maximum = _pair(occupied["max"], f"{slab_path}.occupied_extents.max")
                if any(low > high for low, high in zip(minimum, maximum)):
                    _fail("supervisor_contract_violation", f"{slab_path}.occupied_extents")
                occupied = {"min": minimum, "max": maximum}
            area_fraction = _number(slab["surface_area_fraction"], f"{slab_path}.surface_area_fraction")
            mean_abs_normal = _axis_values(slab["mean_abs_normal"], f"{slab_path}.mean_abs_normal")
            if area_fraction < 0 or area_fraction > 1 or any(item < 0 or item > 1 for item in mean_abs_normal.values()):
                _fail("supervisor_contract_violation", slab_path)
            slabs.append({
                "canonical_interval": interval,
                "occupied_extents": occupied,
                "surface_area_fraction": area_fraction,
                "mean_abs_normal": mean_abs_normal,
            })
        profiles.append({"axis": axis, "occupied_axes": occupied_axes, "slabs": slabs})
    return {
        "schema": value["schema"],
        "coordinate_contract": value["coordinate_contract"],
        "bin_count": value["bin_count"],
        "profiles": profiles,
    }


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        _fail("supervisor_contract_violation", path)
    return value


def _enum(value: Any, values: tuple[str, ...], path: str) -> str:
    if type(value) is not str or value not in values:
        _fail("supervisor_contract_violation", path)
    return value


def _observation_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(value, ("schema", "reference_id", "method", "observation"), path)
    if value["schema"] != "meshscope.reference-response/1":
        _fail("supervisor_contract_violation", f"{path}.schema")
    _handle_result(value["reference_id"], f"{path}.reference_id")
    method = _enum(value["method"], ("summary", "section_profile"), f"{path}.method")
    return {
        "method": method,
        "value": (
            _summary_projection(value["observation"], f"{path}.observation")
            if method == "summary"
            else _section_profile_projection(value["observation"], f"{path}.observation")
        ),
    }


def _result_fields(
    value: Any,
    fields: tuple[tuple[str, str], ...],
    path: str,
    operation: str,
) -> dict[str, Any]:
    value = _closed(value, tuple(name for name, _kind in fields), path)
    result = {}
    for name, kind in fields:
        item_path = f"{path}.{name}"
        if kind == "state":
            result[name] = _state(value[name], operation, item_path)
        elif kind == "handle":
            result[name] = _handle_result(value[name], item_path)
        elif kind == "optional_handle":
            result[name] = _optional_handle_result(value[name], item_path)
        elif kind == "identity":
            result[name] = _handle_result(value[name], item_path)
        elif kind == "budgets":
            result[name] = _budgets_result(value[name], item_path)
        elif kind == "next":
            result[name] = _next_result(value[name], item_path)
        elif kind == "observation":
            result[name] = _observation_result(value[name], item_path)
        elif kind == "decision_facts":
            result[name] = _decision_facts_result(value[name], item_path)
        elif kind == "feedback":
            result[name] = _draft_feedback_result(value[name], item_path)
        elif kind == "classification":
            result[name] = _enum(value[name], ("invalid_ticket", "stale_ticket", "admitted_failure", "repair_evidence_failed"), item_path)
        else:
            _fail("supervisor_contract_violation", item_path)
    return result


def _validate_workspace_status_result(value: Any, path: str) -> dict[str, Any]:
    fields = (
        ("state", "state"),
        ("workspace_identity", "identity"),
        ("budgets", "budgets"),
        ("permitted_next_intents", "next"),
    )
    if type(value) is not dict:
        _fail("supervisor_contract_violation", path)
    expected = {name for name, _kind in fields}
    recovery = value.get("publication_recovery")
    if recovery is not None:
        expected.add("publication_recovery")
    if set(value) != expected:
        _fail("supervisor_contract_violation", path)
    result = _result_fields(
        {name: value[name] for name, _kind in fields},
        fields,
        path,
        "workspace_status",
    )
    if recovery is not None:
        recovery_path = f"{path}.publication_recovery"
        if type(recovery) is not dict or recovery.get("state") != "published":
            _fail("supervisor_contract_violation", recovery_path)
        validator = (
            _validate_repair_result
            if "cycle_handle" in recovery
            else _validate_step_zero_result
        )
        result["publication_recovery"] = validator(recovery, recovery_path)
    return result


def _validate_start_attempt_result(value: Any, path: str) -> dict[str, Any]:
    base = (("state", "state"), ("attempt_handle", "handle"), ("candidate_handle", "handle"), ("capability_bundle_handle", "handle"), ("permitted_next_intents", "next"))
    if "evaluation_ticket" not in value:
        return _result_fields(value, base, path, "start_attempt")
    value = _closed(value, tuple(name for name, _kind in base) + ("evaluation_ticket", "draft_budget"), path)
    result = _result_fields({name: value[name] for name, _kind in base}, base, path, "start_attempt")
    result["evaluation_ticket"] = _optional_handle_result(value["evaluation_ticket"], f"{path}.evaluation_ticket")
    budget = _closed(value["draft_budget"], ("used", "remaining", "maximum"), f"{path}.draft_budget")
    result["draft_budget"] = {key: _integer(budget[key], f"{path}.draft_budget.{key}", maximum=8) for key in ("used", "remaining", "maximum")}
    if result["draft_budget"]["maximum"] != 8 or result["draft_budget"]["used"] + result["draft_budget"]["remaining"] != 8:
        _fail("supervisor_contract_violation", f"{path}.draft_budget")
    return result


def _draft_feedback_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(value, ("schema", "before", "after", "delta", "target_change_preview"), path)
    if value["schema"] != "mesh-to-cad.repair-draft-feedback/1":
        _fail("supervisor_contract_violation", f"{path}.schema")
    def counts(item: Any, item_path: str) -> dict[str, int]:
        item = _closed(item, ("missing_surface_count", "excess_surface_count"), item_path)
        return {key: _integer(item[key], f"{item_path}.{key}") for key in item}
    before = counts(value["before"], f"{path}.before")
    after = counts(value["after"], f"{path}.after")
    delta = _closed(value["delta"], ("missing_surface_count", "excess_surface_count"), f"{path}.delta")
    delta = {key: _signed_integer(delta[key], f"{path}.delta.{key}") for key in delta}
    if any(delta[key] != after[key] - before[key] for key in delta):
        _fail("supervisor_contract_violation", f"{path}.delta")
    preview = _closed(value["target_change_preview"], ("resolved", "persisted", "new"), f"{path}.target_change_preview")
    out = {}
    for name in ("resolved", "persisted", "new"):
        section = _closed(preview[name], ("total", "returned", "remaining", "items"), f"{path}.target_change_preview.{name}")
        returned = _integer(section["returned"], f"{path}.target_change_preview.{name}.returned", maximum=8)
        if type(section["items"]) is not list or len(section["items"]) != returned:
            _fail("supervisor_contract_violation", f"{path}.target_change_preview.{name}.items")
        items = []
        for index, raw in enumerate(section["items"]):
            item_path = f"{path}.target_change_preview.{name}.items[{index}]"
            raw = _closed(raw, ("kind", "bounds_canonical"), item_path)
            items.append({"kind": _enum(raw["kind"], ("missing", "excess"), f"{item_path}.kind"), "bounds_canonical": _bounds_canonical_result(raw["bounds_canonical"], f"{item_path}.bounds_canonical")})
        total = _integer(section["total"], f"{path}.target_change_preview.{name}.total")
        remaining = _integer(section["remaining"], f"{path}.target_change_preview.{name}.remaining")
        if total != returned + remaining:
            _fail("supervisor_contract_violation", f"{path}.target_change_preview.{name}")
        out[name] = {"total": total, "returned": returned, "remaining": remaining, "items": items}
    return {"schema": value["schema"], "before": before, "after": after, "delta": delta, "target_change_preview": out}


def _validate_evaluate_repair_result(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict or "state" not in value:
        _fail("supervisor_contract_violation", path)
    if value["state"] == "failed":
        if value.get("classification") in {"invalid_ticket", "stale_ticket"}:
            return _result_fields(value, (("state", "state"), ("classification", "classification"), ("permitted_next_intents", "next")), path, "evaluate_repair_draft")
        value = _closed(value, ("state", "classification", "subtype", "next_evaluation_ticket", "permitted_next_intents"), path)
        result = _result_fields({key: value[key] for key in ("state", "classification", "permitted_next_intents")}, (("state", "state"), ("classification", "classification"), ("permitted_next_intents", "next")), path, "evaluate_repair_draft")
        result["subtype"] = _enum(value["subtype"], ("provider_execution_failed", "voxblame_output_invalid", "preview_output_invalid", "region_diff_invalid", "source_changes_invalid"), f"{path}.subtype")
        result["next_evaluation_ticket"] = _optional_handle_result(
            value["next_evaluation_ticket"], f"{path}.next_evaluation_ticket"
        )
        return result
    return _result_fields(value, (("state", "state"), ("draft_handle", "handle"), ("feedback", "feedback"), ("next_evaluation_ticket", "optional_handle"), ("permitted_next_intents", "next")), path, "evaluate_repair_draft")


def _validate_abandon_repair_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("permitted_next_intents", "next")), path, "abandon_repair_attempt")


def _validate_run_candidate_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("candidate_handle", "handle"), ("result_handle", "handle"), ("permitted_next_intents", "next")), path, "run_candidate_tool")


def _validate_step_zero_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("step_handle", "handle"), ("preview_handle", "handle"), ("decision_facts", "decision_facts"), ("permitted_next_intents", "next")), path, "submit_step_zero")


def _validate_repair_result(value: Any, path: str) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("state") == "failed":
        value = _closed(
            value,
            ("state", "classification", "subtype", "permitted_next_intents"),
            path,
        )
        classification = _enum(
            value["classification"],
            ("repair_evidence_failed",),
            f"{path}.classification",
        )
        return {
            "state": _state(value["state"], "submit_repair", f"{path}.state"),
            "classification": classification,
            "subtype": _enum(value["subtype"], ("provider_execution_failed", "voxblame_output_invalid", "preview_output_invalid", "region_diff_invalid", "source_changes_invalid"), f"{path}.subtype"),
            "permitted_next_intents": _next_result(
                value["permitted_next_intents"],
                f"{path}.permitted_next_intents",
            ),
        }
    return _result_fields(value, (("state", "state"), ("step_handle", "handle"), ("preview_handle", "handle"), ("cycle_handle", "handle"), ("decision_facts", "decision_facts"), ("permitted_next_intents", "next")), path, "submit_repair")


def _validate_preview_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("preview_handle", "handle"), ("permitted_next_intents", "next")), path, "inspect_formal_preview")


def _validate_repair_target_page_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(
        value,
        (
            "schema",
            "step_ordinal",
            "total",
            "returned",
            "remaining",
            "offset",
            "next_offset",
            "items",
        ),
        path,
    )
    if value["schema"] != REPAIR_TARGET_PAGE_SCHEMA:
        _fail("supervisor_contract_violation", f"{path}.schema")
    step = _integer(value["step_ordinal"], f"{path}.step_ordinal", maximum=MAX_REPAIR_STEP)
    total = _integer(value["total"], f"{path}.total")
    returned = _integer(
        value["returned"], f"{path}.returned", maximum=_DECISION_FACT_MAX_TARGETS
    )
    remaining = _integer(value["remaining"], f"{path}.remaining")
    offset = _integer(value["offset"], f"{path}.offset")
    if (
        offset % _DECISION_FACT_MAX_TARGETS
        or (total == 0 and (offset != 0 or returned != 0))
        or (total > 0 and (offset >= total or returned == 0))
    ):
        _fail("supervisor_contract_violation", path)
    if offset + returned + remaining != total or (remaining and returned != _DECISION_FACT_MAX_TARGETS):
        _fail("supervisor_contract_violation", path)
    expected_next = offset + returned if remaining else None
    if value["next_offset"] != expected_next:
        _fail("supervisor_contract_violation", f"{path}.next_offset")
    items = _repair_target_items(value["items"], f"{path}.items", returned, step=step)
    if [item["rank"] for item in items] != list(range(offset, offset + returned)):
        _fail("supervisor_contract_violation", f"{path}.items")
    return {
        "schema": REPAIR_TARGET_PAGE_SCHEMA,
        "step_ordinal": step,
        "total": total,
        "returned": returned,
        "remaining": remaining,
        "offset": offset,
        "next_offset": expected_next,
        "items": items,
    }


def _validate_finalize_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("final_delivery_handle", "handle"), ("permitted_next_intents", "next")), path, "select_and_finalize")


def _target_section_side(value: Any, path: str) -> dict[str, Any]:
    value = _closed(value, ("triangle_count", "profiles"), path)
    projected = _section_profile_projection(
        {
            "schema": "meshscope.reference-section-profile/1",
            "coordinate_contract": "trellis2_canonical/1",
            "bin_count": 8,
            "profiles": value["profiles"],
        },
        path,
    )
    return {
        "triangle_count": _integer(value["triangle_count"], f"{path}.triangle_count"),
        "profiles": projected["profiles"],
    }


def _validate_target_section_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(
        value,
        ("schema", "rank", "reference", "candidate", "local_occupancy"),
        path,
    )
    if value["schema"] != TARGET_SECTION_OBSERVATION_SCHEMA:
        _fail("supervisor_contract_violation", f"{path}.schema")
    sides: dict[str, dict[str, Any]] = {}
    for name in ("reference", "candidate"):
        side = _closed(value[name], ("core",), f"{path}.{name}")
        sides[name] = {
            "core": _target_section_side(side["core"], f"{path}.{name}.core"),
        }
    local_occupancy = value["local_occupancy"]
    if local_occupancy is not None:
        local_occupancy = _closed(
            local_occupancy,
            ("target", "reference", "candidate"),
            f"{path}.local_occupancy",
        )
        if (
            local_occupancy["target"] != [1, 1, 1]
            or any(type(item) is not int for item in local_occupancy["target"])
        ):
            _fail(
                "supervisor_contract_violation",
                f"{path}.local_occupancy.target",
            )
        cubes: dict[str, list[list[list[bool | None]]]] = {}
        null_masks: dict[str, list[bool]] = {}
        for name in ("reference", "candidate"):
            cube = local_occupancy[name]
            if (
                type(cube) is not list
                or len(cube) != 3
                or any(type(plane) is not list or len(plane) != 3 for plane in cube)
                or any(
                    type(row) is not list or len(row) != 3
                    for plane in cube
                    for row in plane
                )
                or any(
                    cell is not None and type(cell) is not bool
                    for plane in cube
                    for row in plane
                    for cell in row
                )
            ):
                _fail(
                    "supervisor_contract_violation",
                    f"{path}.local_occupancy.{name}",
                )
            cubes[name] = cube
            null_masks[name] = [
                cell is None for plane in cube for row in plane for cell in row
            ]
        if null_masks["reference"] != null_masks["candidate"]:
            _fail("supervisor_contract_violation", f"{path}.local_occupancy")
        null_mask = null_masks["reference"]
        if null_mask[13]:
            _fail("supervisor_contract_violation", f"{path}.local_occupancy")
        clipped_planes = {
            (axis, index)
            for axis in range(3)
            for index in (0, 2)
            if all(
                null_mask[x * 9 + y * 3 + z]
                for x in range(3)
                for y in range(3)
                for z in range(3)
                if (x, y, z)[axis] == index
            )
        }
        if any(
            is_null
            != any((axis, (x, y, z)[axis]) in clipped_planes for axis in range(3))
            for x in range(3)
            for y in range(3)
            for z in range(3)
            for is_null in (null_mask[x * 9 + y * 3 + z],)
        ):
            _fail("supervisor_contract_violation", f"{path}.local_occupancy")
        local_occupancy = {
            "target": [1, 1, 1],
            "reference": cubes["reference"],
            "candidate": cubes["candidate"],
        }
    return {
        "schema": TARGET_SECTION_OBSERVATION_SCHEMA,
        "rank": _integer(value["rank"], f"{path}.rank"),
        "reference": sides["reference"],
        "candidate": sides["candidate"],
        "local_occupancy": local_occupancy,
    }


def _validate_observe_result(value: Any, path: str) -> dict[str, Any]:
    return {"observation": _observation_result(value, path)}


_OPERATION_SPECS = (
    _OperationSpec("workspace_status", ((_FieldSpec("workspace_handle", "handle"),),), _validate_workspace_status_result, "Read purpose-bound workflow state."),
    _OperationSpec("start_attempt", (
        (_FieldSpec("workspace_handle", "handle"), _FieldSpec("plan_handle", "handle")),
        (_FieldSpec("workspace_handle", "handle"), _FieldSpec("plan_handle", "handle"), _FieldSpec("parent_step_handle", "handle")),
    ), _validate_start_attempt_result, "Start one supervisor-owned bounded Attempt."),
    _OperationSpec("run_candidate_tool", ((
        _FieldSpec("workspace_handle", "handle"), _FieldSpec("attempt_handle", "handle"), _FieldSpec("candidate_handle", "handle"), _FieldSpec("operation_handle", "handle"),
    ),), _validate_run_candidate_result, "Run one supervisor-registered candidate operation."),
    _OperationSpec("submit_step_zero", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "attempt_handle", "candidate_handle")),), _validate_step_zero_result, "Submit one measured Step 0 through the supervisor."),
    _OperationSpec("submit_repair", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "attempt_handle", "draft_handle")),), _validate_repair_result, "Publish one previously evaluated immutable Repair draft."),
    _OperationSpec("evaluate_repair_draft", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "attempt_handle", "candidate_handle", "evaluation_ticket")),), _validate_evaluate_repair_result, "Evaluate one immutable Repair draft against the frozen Attempt binding."),
    _OperationSpec("abandon_repair_attempt", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "attempt_handle")),), _validate_abandon_repair_result, "Retire the active Repair Attempt while preserving the intended step draft budget."),
    _OperationSpec("inspect_formal_preview", ((_FieldSpec("preview_handle", "handle"),),), _validate_preview_result, "Inspect one committed formal preview."),
    _OperationSpec("inspect_repair_targets", ((_FieldSpec("step_handle", "handle"), _FieldSpec("offset", "offset")),), _validate_repair_target_page_result, "Read one committed Repair Target page."),
    _OperationSpec("observe_target_section", ((_FieldSpec("step_handle", "handle"), _FieldSpec("rank", "rank")),), _validate_target_section_result, "Observe one committed Repair Target section."),
    _OperationSpec("select_and_finalize", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "step_handle", "selection_handle", "notes_handle")),), _validate_finalize_result, "Select and request supervisor-owned Final Delivery."),
    _OperationSpec("observe_reference", ((
        _FieldSpec("reference_handle", "handle"), _FieldSpec("observation", "observation_request"),
    ),), _validate_observe_result, "Request one fixed Reference Capability observation."),
)
_SPEC_BY_INTENT = {spec.name: spec for spec in _OPERATION_SPECS}
INTENTS = tuple(spec.name for spec in _OPERATION_SPECS)


def _validate_observation_request(value: Any, path: str) -> dict[str, Any]:
    observation = _closed(value, ("method", "args"), path)
    method = observation["method"]
    if type(method) is not str:
        _fail("invalid_request", f"{path}.method")
    if method in _UNSUPPORTED_OBSERVATIONS:
        _fail("unsupported_operation", f"{path}.method")
    if method not in {"summary", "section_profile"}:
        _fail("unknown_method", f"{path}.method")
    args = observation["args"]
    if type(args) is not dict or args:
        _fail("invalid_request", f"{path}.args")
    return observation


def _bind_observation_response(
    response: Any,
    reference_handle: str,
    request_observation: dict[str, Any],
) -> None:
    """Bind a W2 response to the exact request before any projection."""

    if type(response) is not dict or set(response) != {"schema", "reference_id", "method", "observation"}:
        _fail("supervisor_contract_violation", "$.result")
    if response["reference_id"] != reference_handle:
        _fail("supervisor_contract_violation", "$.result.reference_id")
    if response["method"] != request_observation["method"]:
        _fail("supervisor_contract_violation", "$.result.method")


def _validate_args(spec: _OperationSpec, args: dict[str, Any]) -> None:
    fields = next(
        (variant for variant in spec.variants if set(args) == {field.name for field in variant}),
        None,
    )
    if fields is None:
        _fail("invalid_request", "$.args")
    for field in fields:
        path = f"$.args.{field.name}"
        if field.kind == "handle":
            _handle(args[field.name], path)
        elif field.kind == "offset":
            offset = _step(args[field.name], path)
            if offset % _DECISION_FACT_MAX_TARGETS:
                _fail("invalid_request", path)
        elif field.kind == "rank":
            _step(args[field.name], path)
        elif field.kind == "observation_request":
            _validate_observation_request(args[field.name], path)


def _field_schema(kind: str) -> dict[str, Any]:
    if kind == "handle":
        return {"type": "string", "pattern": _HANDLE_PATTERN}
    if kind in {"offset", "rank"}:
        return {"type": "integer", "minimum": 0}
    if kind == "observation_request":
        empty = {"type": "object", "additionalProperties": False, "properties": {}}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {"method": {"enum": ["summary", "section_profile"]}, "args": empty},
            "required": ["method", "args"],
        }
    raise AssertionError(kind)


def tool_descriptors() -> list[dict[str, Any]]:
    """Return MCP schemas from the same fixed operation specifications."""

    def variant_schema(variant: tuple[_FieldSpec, ...]) -> dict[str, Any]:
        return {
                "type": "object",
                "additionalProperties": False,
                "properties": {field.name: _field_schema(field.kind) for field in variant},
                "required": [field.name for field in variant],
        }

    descriptors = []
    for spec in _OPERATION_SPECS:
        variants = [variant_schema(variant) for variant in spec.variants]
        descriptor = {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": variants[0] if len(variants) == 1 else {"type": "object", "oneOf": variants},
        }
        if spec.name == "inspect_formal_preview":
            descriptor["annotations"] = {
                "readOnlyHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            }
        descriptors.append(descriptor)
    return descriptors


class AgentSurface:
    """One direct dispatcher shared by CLI and MCP adapters."""

    def __init__(self, ports: SupervisorPorts | None):
        self._ports = ports

    def cancel(self) -> None:
        """Cancel trusted work before the transport is torn down."""

        if self._ports is None:
            return
        callback = getattr(self._ports, "cancel", None)
        if callback is not None:
            callback()

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        response, _preview_png = self._handle(request, include_preview=False)
        return response

    def handle_mcp(self, request: Mapping[str, Any]) -> tuple[dict[str, Any], bytes | None]:
        """Return one closed response and its private MCP-only PNG attachment."""

        return self._handle(request, include_preview=True)

    def acknowledge_written_response(
        self,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
    ) -> None:
        """Record transport-closed observations after their response is written."""

        if response.get("intent") != "observe_target_section":
            return
        args = request["args"]
        self._ports.acknowledge_target_section_observation(
            args["step_handle"], args["rank"]
        )

    def _handle(
        self, request: Mapping[str, Any], *, include_preview: bool
    ) -> tuple[dict[str, Any], bytes | None]:
        encoded_request = _canonical_json(
            request,
            classification="invalid_request",
            path="$.request",
        )
        if len(encoded_request) > MAX_REQUEST_BYTES:
            _fail("request_too_large", "$.request")
        envelope = _closed(request, ("schema", "intent", "args"), "$.request")
        if envelope["schema"] != REQUEST_SCHEMA:
            _fail("invalid_request", "$.request.schema")
        intent = envelope["intent"]
        if type(intent) is not str or intent not in _SPEC_BY_INTENT:
            _fail("unknown_intent", "$.request.intent")
        args = envelope["args"]
        if type(args) is not dict:
            _fail("invalid_request", "$.request.args")
        spec = _SPEC_BY_INTENT[intent]
        _validate_args(spec, args)
        if self._ports is None:
            _fail("supervisor_unavailable", "$.supervisor")
        preview_png: bytes | None = None
        try:
            if intent == "workspace_status":
                result = self._ports.workspace_status(args["workspace_handle"])
            elif intent == "start_attempt":
                result = self._ports.start_attempt(
                    args["workspace_handle"],
                    args["plan_handle"],
                    args.get("parent_step_handle"),
                )
            elif intent == "run_candidate_tool":
                result = self._ports.run_candidate_tool(
                    args["workspace_handle"],
                    args["attempt_handle"],
                    args["candidate_handle"],
                    args["operation_handle"],
                )
            elif intent == "submit_step_zero":
                result = self._ports.submit_step_zero(**args)
            elif intent == "submit_repair":
                result = self._ports.submit_repair(**args)
            elif intent == "evaluate_repair_draft":
                result = self._ports.evaluate_repair_draft(**args)
            elif intent == "abandon_repair_attempt":
                result = self._ports.abandon_repair_attempt(**args)
            elif intent == "inspect_formal_preview":
                if include_preview:
                    result, preview_png = self._ports.inspect_formal_preview_with_preview(**args)
                else:
                    result = self._ports.inspect_formal_preview(**args)
            elif intent == "inspect_repair_targets":
                result = self._ports.inspect_repair_targets(**args)
            elif intent == "observe_target_section":
                result = self._ports.observe_target_section(**args)
                target_section_requires_local_occupancy = (
                    self._ports.target_section_requires_local_occupancy(**args)
                )
            elif intent == "select_and_finalize":
                result = self._ports.select_and_finalize(**args)
            else:
                result = self._ports.observe_reference(
                    args["reference_handle"], args["observation"]
                )
        except Exception as error:
            if (
                intent == "select_and_finalize"
                and getattr(error, "classification", None) == "state_conflict"
            ):
                _fail("state_conflict", "$.supervisor")
            _fail("supervisor_failure", "$.supervisor")
        try:
            if intent == "observe_reference":
                _bind_observation_response(
                    result,
                    args["reference_handle"],
                    args["observation"],
                )
            elif intent == "observe_target_section" and (
                type(result) is not dict or result.get("rank") != args["rank"]
            ):
                _fail("supervisor_contract_violation", "$.result.rank")
            elif intent == "observe_target_section" and (
                (result.get("local_occupancy") is not None)
                is not target_section_requires_local_occupancy
            ):
                _fail("supervisor_contract_violation", "$.result.local_occupancy")
            result = spec.result(result, "$.result")
        except AgentSurfaceError:
            _fail("supervisor_contract_violation", "$.result")
        response = {
            "schema": RESPONSE_SCHEMA,
            "intent": intent,
            "result": result,
        }
        if len(
            _canonical_json(
                response,
                classification="supervisor_contract_violation",
                path="$.response",
            )
        ) > MAX_RESPONSE_BYTES:
            _fail("response_too_large", "$.response")
        return response, preview_png


__all__ = [
    "AgentSurface",
    "AgentSurfaceError",
    "DECISION_FACTS_SCHEMA",
    "REPAIR_TARGET_PAGE_SCHEMA",
    "TARGET_SECTION_OBSERVATION_SCHEMA",
    "ERROR_SCHEMA",
    "INTENTS",
    "REQUEST_SCHEMA",
    "RESPONSE_SCHEMA",
    "SupervisorPorts",
    "error_document",
    "tool_descriptors",
]
