from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
import unittest

from motion_server.context import build_motion_context
from motion_server.protocol import MotionProtocolError
from motion_server.server import handle_message


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sample_metadata(provider: str = "fake") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "texttocad-robot-motion-explorer",
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
    }


def sample_motion_config(provider: str = "fake") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "texttocad-robot-motion-server",
        "provider": provider,
        "commands": {
            "urdf.solvePose": {
                "planningGroup": "arm",
                "jointNames": ["shoulder"],
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
                "planner": {
                    "pipeline": "ompl",
                },
            },
        },
    }


def write_sample_robot(repo_root: Path, urdf_ref: str = "robot.urdf") -> None:
    urdf_path = repo_root / urdf_ref
    write_file(
        urdf_path,
        "<robot name='robot'><link name='base_link'/><link name='tool_link'/></robot>",
    )
    motion_dir = urdf_path.parent / f".{urdf_path.name}/robot-motion"
    write_file(motion_dir / "explorer.json", json.dumps(sample_metadata()))
    write_file(motion_dir / "motion_server.json", json.dumps(sample_motion_config()))
    write_file(motion_dir / "moveit2_kinematics.yaml", "{}")


class MotionContextTests(unittest.TestCase):
    def test_builds_context_for_cataloged_motion_server_urdf(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            write_sample_robot(repo_root)

            context = build_motion_context(
                repo_root=repo_root,
                dir="",
                file="robot.urdf",
                type="urdf.planToPose",
            )

            self.assertEqual(context["file"], "robot.urdf")
            self.assertEqual(context["provider"], "fake")
            self.assertEqual(context["command"]["planningGroup"], "arm")
            self.assertIn("robot.urdf", context["explorerMetadataHash"])
            self.assertIn("motion_server.json", context["explorerMetadataHash"])
            self.assertIn("moveit2_kinematics.yaml", context["explorerMetadataHash"])
            self.assertEqual(Path(str(context["sidecarDir"])).resolve(), (repo_root / ".robot.urdf/robot-motion").resolve())

    def test_builds_context_for_repo_root_catalog_file_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            write_sample_robot(repo_root)

            context = build_motion_context(
                repo_root=repo_root,
                dir="",
                file="robot.urdf",
                type="urdf.planToPose",
            )

            self.assertEqual(context["dir"], "")
            self.assertEqual(context["file"], "robot.urdf")
            self.assertEqual(Path(str(context["urdfPath"])).resolve(), (repo_root / "robot.urdf").resolve())

    def test_preserves_end_effector_solver_overrides_from_motion_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            write_file(
                repo_root / "robot.urdf",
                "<robot name='robot'><link name='base_link'/><link name='tool_link'/></robot>",
            )
            write_file(
                repo_root / ".robot.urdf/robot-motion/explorer.json",
                json.dumps(sample_metadata()),
            )
            config = sample_motion_config()
            config["commands"]["urdf.solvePose"]["endEffectors"][0]["planningGroup"] = "arm_2"
            config["commands"]["urdf.solvePose"]["endEffectors"][0]["jointNames"] = ["arm_2_shoulder", "arm_2_wrist"]
            write_file(
                repo_root / ".robot.urdf/robot-motion/motion_server.json",
                json.dumps(config),
            )

            context = build_motion_context(
                repo_root=repo_root,
                dir="",
                file="robot.urdf",
                type="urdf.solvePose",
            )

            end_effector = context["command"]["endEffectors"][0]
            self.assertEqual(end_effector["planningGroup"], "arm_2")
            self.assertEqual(end_effector["jointNames"], ["arm_2_shoulder", "arm_2_wrist"])

    def test_accepts_repo_relative_file_ref_with_catalog_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            write_sample_robot(repo_root, "workspace/robot.urdf")

            context = build_motion_context(
                repo_root=repo_root,
                dir="workspace",
                file="workspace/robot.urdf",
                type="urdf.planToPose",
            )

            self.assertEqual(context["dir"], "workspace")
            self.assertEqual(context["file"], "robot.urdf")
            self.assertEqual(Path(str(context["urdfPath"])).resolve(), (repo_root / "workspace/robot.urdf").resolve())

    def test_handle_message_preserves_empty_catalog_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            write_sample_robot(repo_root)

            response = asyncio.run(handle_message(json.dumps({
                "id": "req-1",
                "type": "urdf.solvePose",
                "payload": {
                    "dir": "",
                    "file": "robot.urdf",
                    "target": {
                        "endEffector": "tool",
                        "frame": "base_link",
                        "xyz": [0.1, 0.0, 0.2],
                    },
                },
            }), repo_root=repo_root))

            payload = json.loads(response)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["jointValuesByNameDeg"]["shoulder"], 1)

    def test_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertRaisesRegex(MotionProtocolError, "file must stay inside"):
                build_motion_context(
                    repo_root=Path(tempdir),
                    dir="",
                    file="../robot.urdf",
                    type="urdf.solvePose",
                )

    def test_rejects_missing_requested_command(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            metadata = sample_metadata()
            del metadata["motionServer"]["commands"]["urdf.planToPose"]
            write_file(
                repo_root / "robot.urdf",
                "<robot name='robot'><link name='base_link'/><link name='tool_link'/></robot>",
            )
            write_file(
                repo_root / ".robot.urdf/robot-motion/explorer.json",
                json.dumps(metadata),
            )
            write_file(
                repo_root / ".robot.urdf/robot-motion/motion_server.json",
                json.dumps(sample_motion_config()),
            )

            with self.assertRaisesRegex(MotionProtocolError, "does not implement motionServer command urdf.planToPose"):
                build_motion_context(
                    repo_root=repo_root,
                    dir="",
                    file="robot.urdf",
                    type="urdf.planToPose",
                )


if __name__ == "__main__":
    unittest.main()
