from __future__ import annotations

from functools import lru_cache
from typing import Any

from motion_server.protocol import MotionProtocolError, MotionRequest
from motion_server.provider_loader import load_provider


@lru_cache(maxsize=16)
def _provider(provider_name: str) -> Any:
    return load_provider(provider_name)


def _provider_name(request: MotionRequest) -> str:
    provider = str(request.context.get("provider", "")).strip()
    if not provider:
        raise MotionProtocolError("request context provider is required")
    return provider


def dispatch(request: MotionRequest) -> dict[str, Any]:
    provider = _provider(_provider_name(request))
    if request.type == "urdf.solvePose":
        result = provider.solve_pose(request)
    elif request.type == "urdf.planToPose":
        result = provider.plan_to_pose(request)
    else:
        raise MotionProtocolError(f"Unsupported request type {request.type}")

    if not isinstance(result, dict):
        raise MotionProtocolError("Provider result must be an object")
    if result.get("ok") is False:
        raise RuntimeError(str(result.get("message") or "Provider request failed"))
    result.pop("ok", None)
    return result
