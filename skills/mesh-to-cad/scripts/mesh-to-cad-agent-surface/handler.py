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
RESPONSE_SCHEMA = "mesh-to-cad.agent-response/1"
ERROR_SCHEMA = "mesh-to-cad.agent-error/1"

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_COMPONENT_LIMIT = 32
MAX_REPAIR_STEP = 5
MAX_PARENT_STEP = MAX_REPAIR_STEP - 1

_HANDLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_HANDLE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_W2_SUMMARY_SCHEMA = "meshscope.reference-summary/1"
_W2_COMPONENTS_SCHEMA = "meshscope.reference-components/1"
_UNSUPPORTED_OBSERVATIONS = frozenset(
    {"vertices", "faces", "triangles", "raw_bytes", "export", "raycast", "nearest_point"}
)


class SupervisorPorts(Protocol):
    """The only concrete dependency supplied by W4's trusted supervisor."""

    def workspace_status(self, workspace_handle: str) -> Mapping[str, Any]: ...

    def start_attempt(
        self,
        workspace_handle: str,
        plan_handle: str,
        from_step: int | None,
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
        candidate_handle: str,
    ) -> Mapping[str, Any]: ...

    def select_and_finalize(
        self,
        workspace_handle: str,
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


def _handle_result(value: Any, path: str) -> str:
    if type(value) is not str or _HANDLE.fullmatch(value) is None:
        _fail("supervisor_contract_violation", path)
    return value


def _bounds_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(value, ("min", "max", "size"), path)
    return {
        key: _vector(value[key], f"{path}.{key}")
        for key in ("min", "max", "size")
    }


def _budgets_result(value: Any, path: str) -> dict[str, int]:
    value = _closed(
        value,
        ("remaining_cycles", "remaining_attempts", "remaining_tool_failures"),
        path,
    )
    return {
        key: _integer(value[key], f"{path}.{key}")
        for key in ("remaining_cycles", "remaining_attempts", "remaining_tool_failures")
    }


def _next_result(value: Any, path: str) -> list[str]:
    if type(value) is not list or len(value) > len(INTENTS):
        _fail("supervisor_contract_violation", path)
    if any(type(item) is not str or item not in INTENTS for item in value):
        _fail("supervisor_contract_violation", path)
    return list(value)


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


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        _fail("supervisor_contract_violation", path)
    return value


def _enum(value: Any, values: tuple[str, ...], path: str) -> str:
    if type(value) is not str or value not in values:
        _fail("supervisor_contract_violation", path)
    return value


def _component_projection(value: Any, path: str) -> dict[str, Any]:
    value = _closed(
        value,
        ("schema", "limit", "total", "returned", "omitted", "components"),
        path,
    )
    if value["schema"] != "meshscope.reference-components/1":
        _fail("supervisor_contract_violation", f"{path}.schema")
    limit = _integer(value["limit"], f"{path}.limit", maximum=MAX_COMPONENT_LIMIT)
    total = _integer(value["total"], f"{path}.total")
    returned = _integer(value["returned"], f"{path}.returned")
    omitted = _integer(value["omitted"], f"{path}.omitted")
    components = value["components"]
    if type(components) is not list or len(components) > MAX_COMPONENT_LIMIT:
        _fail("supervisor_contract_violation", f"{path}.components")
    rows = []
    for index, item in enumerate(components):
        item = _closed(
            item,
            ("rank", "vertices", "faces", "bounds", "centroid"),
            f"{path}.components[{index}]",
        )
        rows.append(
            {
                "rank": _integer(item["rank"], f"{path}.components[{index}].rank"),
                "vertices": _integer(item["vertices"], f"{path}.components[{index}].vertices"),
                "faces": _integer(item["faces"], f"{path}.components[{index}].faces"),
                "bounds": _bounds_result(item["bounds"], f"{path}.components[{index}].bounds"),
                "centroid": _vector(item["centroid"], f"{path}.components[{index}].centroid"),
            }
        )
    if returned != len(rows) or omitted != total - returned or returned > limit:
        _fail("supervisor_contract_violation", path)
    return {
        "schema": value["schema"],
        "limit": limit,
        "total": total,
        "returned": returned,
        "omitted": omitted,
        "components": rows,
    }


def _observation_result(value: Any, path: str) -> dict[str, Any]:
    value = _closed(value, ("schema", "reference_id", "method", "observation"), path)
    if value["schema"] != "meshscope.reference-response/1":
        _fail("supervisor_contract_violation", f"{path}.schema")
    _handle_result(value["reference_id"], f"{path}.reference_id")
    method = _enum(value["method"], ("summary", "components"), f"{path}.method")
    projection = (
        _summary_projection(value["observation"], f"{path}.observation")
        if method == "summary"
        else _component_projection(value["observation"], f"{path}.observation")
    )
    return {"method": method, "value": projection}


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
        elif kind == "identity":
            result[name] = _handle_result(value[name], item_path)
        elif kind == "budgets":
            result[name] = _budgets_result(value[name], item_path)
        elif kind == "next":
            result[name] = _next_result(value[name], item_path)
        elif kind == "observation":
            result[name] = _observation_result(value[name], item_path)
        else:
            _fail("supervisor_contract_violation", item_path)
    return result


def _validate_workspace_status_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(
        value,
        (("state", "state"), ("workspace_identity", "identity"), ("budgets", "budgets"), ("permitted_next_intents", "next")),
        path,
        "workspace_status",
    )


def _validate_start_attempt_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("attempt_handle", "handle"), ("candidate_handle", "handle"), ("capability_bundle_handle", "handle"), ("permitted_next_intents", "next")), path, "start_attempt")


def _validate_run_candidate_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("candidate_handle", "handle"), ("result_handle", "handle"), ("permitted_next_intents", "next")), path, "run_candidate_tool")


def _validate_step_zero_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("step_handle", "handle"), ("permitted_next_intents", "next")), path, "submit_step_zero")


def _validate_repair_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("step_handle", "handle"), ("cycle_handle", "handle"), ("permitted_next_intents", "next")), path, "submit_repair")


def _validate_finalize_result(value: Any, path: str) -> dict[str, Any]:
    return _result_fields(value, (("state", "state"), ("final_delivery_handle", "handle"), ("permitted_next_intents", "next")), path, "select_and_finalize")


def _validate_observe_result(value: Any, path: str) -> dict[str, Any]:
    return {"observation": _observation_result(value, path)}


_OPERATION_SPECS = (
    _OperationSpec("workspace_status", ((_FieldSpec("workspace_handle", "handle"),),), _validate_workspace_status_result, "Read purpose-bound workflow state."),
    _OperationSpec("start_attempt", (
        (_FieldSpec("workspace_handle", "handle"), _FieldSpec("plan_handle", "handle")),
        (_FieldSpec("workspace_handle", "handle"), _FieldSpec("plan_handle", "handle"), _FieldSpec("from_step", "parent_step")),
    ), _validate_start_attempt_result, "Start one supervisor-owned bounded Attempt."),
    _OperationSpec("run_candidate_tool", ((
        _FieldSpec("workspace_handle", "handle"), _FieldSpec("attempt_handle", "handle"), _FieldSpec("candidate_handle", "handle"), _FieldSpec("operation_handle", "handle"),
    ),), _validate_run_candidate_result, "Run one supervisor-registered candidate operation."),
    _OperationSpec("submit_step_zero", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "attempt_handle", "candidate_handle")),), _validate_step_zero_result, "Submit one measured Step 0 through the supervisor."),
    _OperationSpec("submit_repair", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "attempt_handle", "candidate_handle")),), _validate_repair_result, "Submit one measured Repair Cycle through the supervisor."),
    _OperationSpec("select_and_finalize", (tuple(_FieldSpec(name, "handle") for name in ("workspace_handle", "selection_handle", "notes_handle")),), _validate_finalize_result, "Select and request supervisor-owned Final Delivery."),
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
    if method not in {"summary", "components"}:
        _fail("unknown_method", f"{path}.method")
    args = observation["args"]
    if type(args) is not dict or set(args) - {"limit"}:
        _fail("invalid_request", f"{path}.args")
    if method == "summary" and args:
        _fail("invalid_request", f"{path}.args")
    if "limit" in args and (
        type(args["limit"]) is not int
        or isinstance(args["limit"], bool)
        or not 1 <= args["limit"] <= MAX_COMPONENT_LIMIT
    ):
        _fail("invalid_request", f"{path}.args.limit")
    if method == "summary" and "limit" in args:
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
    if request_observation["method"] == "components":
        requested_limit = request_observation["args"].get("limit", MAX_COMPONENT_LIMIT)
        returned = response["observation"]
        if type(returned) is not dict or returned.get("limit") != requested_limit:
            _fail("supervisor_contract_violation", "$.result.observation.limit")


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
        elif field.kind == "parent_step":
            _step(args[field.name], path, maximum=MAX_PARENT_STEP)
        elif field.kind == "observation_request":
            _validate_observation_request(args[field.name], path)


def _field_schema(kind: str) -> dict[str, Any]:
    if kind == "handle":
        return {"type": "string", "pattern": _HANDLE_PATTERN}
    if kind == "parent_step":
        return {"type": "integer", "minimum": 0, "maximum": MAX_PARENT_STEP}
    if kind == "observation_request":
        empty = {"type": "object", "additionalProperties": False, "properties": {}}
        components = {"type": "object", "additionalProperties": False, "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": MAX_COMPONENT_LIMIT}}, "required": []}
        return {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"method": {"const": "summary"}, "args": empty},
                    "required": ["method", "args"],
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"method": {"const": "components"}, "args": {"anyOf": [empty, components]}},
                    "required": ["method", "args"],
                },
            ]
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
        descriptors.append({
            "name": spec.name,
            "description": spec.description,
            "inputSchema": variants[0] if len(variants) == 1 else {"type": "object", "oneOf": variants},
        })
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
        try:
            if intent == "workspace_status":
                result = self._ports.workspace_status(args["workspace_handle"])
            elif intent == "start_attempt":
                result = self._ports.start_attempt(
                    args["workspace_handle"],
                    args["plan_handle"],
                    args.get("from_step"),
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
            elif intent == "select_and_finalize":
                result = self._ports.select_and_finalize(**args)
            else:
                result = self._ports.observe_reference(
                    args["reference_handle"], args["observation"]
                )
        except Exception:
            _fail("supervisor_failure", "$.supervisor")
        try:
            if intent == "observe_reference":
                _bind_observation_response(
                    result,
                    args["reference_handle"],
                    args["observation"],
                )
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
        return response


__all__ = [
    "AgentSurface",
    "AgentSurfaceError",
    "ERROR_SCHEMA",
    "INTENTS",
    "REQUEST_SCHEMA",
    "RESPONSE_SCHEMA",
    "SupervisorPorts",
    "error_document",
    "tool_descriptors",
]
