from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_REQUEST_TYPES = {"urdf.solvePose", "urdf.planToPose"}


class MotionProtocolError(ValueError):
    """Raised when a motion server request is malformed."""


def _plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _string(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MotionProtocolError(f"{label} is required")
    return normalized


def _number(value: Any, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise MotionProtocolError(f"{label} must be a finite number") from exc
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise MotionProtocolError(f"{label} must be a finite number")
    return numeric


def normalize_xyz(value: Any, label: str = "target.xyz") -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise MotionProtocolError(f"{label} must be a 3-number array")
    return (
        _number(value[0], f"{label}[0]"),
        _number(value[1], f"{label}[1]"),
        _number(value[2], f"{label}[2]"),
    )


def normalize_joint_values(value: Any, label: str = "startJointValuesByNameDeg") -> dict[str, float]:
    if value is None:
        return {}
    if not _plain_object(value):
        raise MotionProtocolError(f"{label} must be an object")
    normalized: dict[str, float] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name or "").strip()
        if not name:
            raise MotionProtocolError(f"{label} cannot include empty joint names")
        normalized[name] = _number(raw_value, f"{label}.{name}")
    return normalized


@dataclass(frozen=True)
class WireMessage:
    id: str
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class MotionRequest:
    id: str
    type: str
    payload: dict[str, Any]
    context: dict[str, Any]
    command: dict[str, Any]


def normalize_wire_message(message: Any) -> WireMessage:
    if not _plain_object(message):
        raise MotionProtocolError("Motion request must be an object")
    request_id = _string(message.get("id"), "id")
    request_type = _string(message.get("type"), "type")
    if request_type not in SUPPORTED_REQUEST_TYPES:
        raise MotionProtocolError(f"Unsupported request type {request_type}")
    payload = message.get("payload")
    if not _plain_object(payload):
        raise MotionProtocolError("payload must be an object")
    return WireMessage(
        id=request_id,
        type=request_type,
        payload=payload,
    )


def normalize_request(message: Any, *, context: dict[str, Any]) -> MotionRequest:
    wire = normalize_wire_message(message)
    if not _plain_object(context):
        raise MotionProtocolError("request context must be an object")
    command = context.get("command")
    if not _plain_object(command):
        raise MotionProtocolError("request context command must be an object")
    return MotionRequest(
        id=wire.id,
        type=wire.type,
        payload=wire.payload,
        context=context,
        command=command,
    )


def normalize_motion_target(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload.get("target")
    if not _plain_object(target):
        raise MotionProtocolError("target must be an object")
    return {
        "endEffector": _string(target.get("endEffector"), "target.endEffector"),
        "frame": _string(target.get("frame"), "target.frame"),
        "xyz": normalize_xyz(target.get("xyz")),
    }


def success_response(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": True,
        "result": result,
    }


def error_response(request_id: str, error: BaseException) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": False,
        "error": {
            "code": error.__class__.__name__,
            "message": str(error),
        },
    }
