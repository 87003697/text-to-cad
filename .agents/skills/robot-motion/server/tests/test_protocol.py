from __future__ import annotations

import unittest

from motion_server.dispatcher import dispatch
from motion_server.protocol import (
    MotionProtocolError,
    normalize_joint_values,
    normalize_request,
)


def fake_context() -> dict[str, object]:
    return {
        "provider": "fake",
        "command": {
            "planningGroup": "arm",
            "jointNames": ["shoulder", "elbow"],
            "endEffectors": [
                {
                    "name": "tool",
                    "link": "tool_link",
                    "frame": "base_link",
                }
            ],
        },
        "motionServer": {
            "version": 1,
            "commands": {
                "urdf.solvePose": {
                    "endEffectors": [
                        {
                            "name": "tool",
                            "link": "tool_link",
                            "frame": "base_link",
                        }
                    ],
                },
                "urdf.planToPose": {},
            },
        },
        "motionConfig": {
            "schemaVersion": 1,
            "kind": "texttocad-robot-motion-server",
            "provider": "fake",
            "commands": {
                "urdf.solvePose": {
                    "planningGroup": "arm",
                    "jointNames": ["shoulder", "elbow"],
                    "endEffectors": [
                        {
                            "name": "tool",
                            "link": "tool_link",
                            "frame": "base_link",
                        }
                    ],
                },
                "urdf.planToPose": {
                    "planningGroup": "arm",
                    "planner": {},
                },
            },
        },
    }


class MotionProtocolTests(unittest.TestCase):
    def test_normalizes_joint_values(self) -> None:
        self.assertEqual(normalize_joint_values({"shoulder": "12.5"}), {"shoulder": 12.5})
        with self.assertRaisesRegex(MotionProtocolError, "empty joint names"):
            normalize_joint_values({"": 1})

    def test_normalizes_request_with_server_context(self) -> None:
        request = normalize_request(
            {
                "id": "abc",
                "type": "urdf.solvePose",
                "payload": {
                    "file": "robot.urdf",
                    "target": {
                        "endEffector": "tool",
                        "frame": "base_link",
                        "xyz": [0.1, 0.0, 0.2],
                    },
                },
            },
            context=fake_context(),
        )

        self.assertEqual(request.id, "abc")
        self.assertEqual(request.command["planningGroup"], "arm")

    def test_fake_provider_solves_and_plans(self) -> None:
        request = normalize_request(
            {
                "id": "abc",
                "type": "urdf.planToPose",
                "payload": {
                    "startJointValuesByNameDeg": {"shoulder": 10},
                    "target": {
                        "endEffector": "tool",
                        "frame": "base_link",
                        "xyz": [0.1, 0.0, 0.2],
                    },
                },
            },
            context={
                **fake_context(),
                "command": fake_context()["motionConfig"]["commands"]["urdf.planToPose"],
            },
        )

        result = dispatch(request)
        self.assertEqual(result["jointValuesByNameDeg"]["shoulder"], 11)
        self.assertEqual(result["trajectory"]["jointNames"], ["shoulder", "elbow"])

    def test_fake_provider_uses_selected_end_effector_joint_group(self) -> None:
        context = fake_context()
        solve_command = context["motionConfig"]["commands"]["urdf.solvePose"]
        solve_command["endEffectors"].append(
            {
                "name": "other_tool",
                "link": "other_tool_link",
                "frame": "base_link",
                "planningGroup": "other_arm",
                "jointNames": ["other_shoulder", "other_elbow"],
            }
        )

        request = normalize_request(
            {
                "id": "abc",
                "type": "urdf.planToPose",
                "payload": {
                    "startJointValuesByNameDeg": {"other_shoulder": 10},
                    "target": {
                        "endEffector": "other_tool",
                        "frame": "base_link",
                        "xyz": [0.1, 0.0, 0.2],
                    },
                },
            },
            context={
                **context,
                "command": context["motionConfig"]["commands"]["urdf.planToPose"],
            },
        )

        result = dispatch(request)
        self.assertEqual(result["jointValuesByNameDeg"], {"other_shoulder": 11.0, "other_elbow": 1.0})
        self.assertEqual(result["trajectory"]["jointNames"], ["other_shoulder", "other_elbow"])


if __name__ == "__main__":
    unittest.main()
