from __future__ import annotations

from typing import Any, Protocol


class MotionProvider(Protocol):
    def solve_pose(self, request: Any) -> dict[str, Any]:
        ...

    def plan_to_pose(self, request: Any) -> dict[str, Any]:
        ...


def load_provider(name: str) -> MotionProvider:
    provider = str(name or "").strip()
    if provider == "fake":
        from motion_server.providers.fake import FakeProvider

        return FakeProvider()
    if provider == "moveit_py":
        from motion_server.providers.moveit_py import MoveItPyProvider

        return MoveItPyProvider()
    raise ValueError(f"Unsupported motion server provider {provider or '(missing)'}")
