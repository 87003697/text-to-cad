from __future__ import annotations

from typing import Any

from motion_server.protocol import MotionProtocolError, normalize_joint_values, normalize_motion_target


class FakeProvider:
    """Test provider for protocol and UI wiring."""

    def _selected_end_effector(self, request: Any) -> dict[str, Any]:
        solve_command = request.command if request.type == "urdf.solvePose" else (
            request.context.get("motionConfig", {})
            .get("commands", {})
            .get("urdf.solvePose", {})
        )
        target = request.payload.get("target") if isinstance(request.payload, dict) else None
        target_name = str(target.get("endEffector") or "").strip() if isinstance(target, dict) else ""
        for end_effector in solve_command.get("endEffectors", []):
            if not target_name or str(end_effector.get("name", "")).strip() == target_name:
                return end_effector
        if target_name:
            raise MotionProtocolError(f"Unknown end effector {target_name}")
        return {}

    def _joint_names(self, request: Any) -> list[str]:
        solve_command = request.command if request.type == "urdf.solvePose" else (
            request.context.get("motionConfig", {})
            .get("commands", {})
            .get("urdf.solvePose", {})
        )
        end_effector = self._selected_end_effector(request)
        return [
            str(name).strip()
            for name in end_effector.get("jointNames", solve_command.get("jointNames", []))
            if str(name).strip()
        ]

    def solve_pose(self, request: Any) -> dict[str, Any]:
        start = normalize_joint_values(request.payload.get("startJointValuesByNameDeg"))
        normalize_motion_target(request.payload)
        joint_names = self._joint_names(request)
        return {
            "jointValuesByNameDeg": {
                name: start.get(name, 0.0) + 1.0
                for name in joint_names
            },
            "residual": {"position": 0.0},
        }

    def plan_to_pose(self, request: Any) -> dict[str, Any]:
        result = self.solve_pose(request)
        joint_names = self._joint_names(request)
        result["trajectory"] = {
            "jointNames": joint_names,
            "points": [
                {
                    "timeFromStartSec": 0.0,
                    "positionsDeg": [0.0 for _ in joint_names],
                },
                {
                    "timeFromStartSec": 0.25,
                    "positionsDeg": [result["jointValuesByNameDeg"].get(name, 0.0) for name in joint_names],
                },
            ],
        }
        return result
