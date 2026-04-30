from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from motion_server.providers.moveit_py import (
    MoveItPyProvider,
    _joint_state_seed,
    _robot_description_for_moveit,
)


class MoveItPyProviderHelperTests(unittest.TestCase):
    def test_robot_description_rewrites_relative_meshes_to_file_uris(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            urdf_path = root / "robot.urdf"
            urdf_path.write_text(
                """
                <robot name="robot">
                  <link name="base">
                    <visual>
                      <geometry>
                        <mesh filename="STL/base.stl" />
                      </geometry>
                    </visual>
                  </link>
                </robot>
                """,
                encoding="utf-8",
            )

            description = _robot_description_for_moveit(urdf_path)

            self.assertIn((root / "STL/base.stl").resolve().as_uri(), description)
            self.assertNotIn('filename="STL/base.stl"', description)

    def test_config_dict_reads_flat_moveit2_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            urdf_path = root / "robot.urdf"
            urdf_path.write_text('<robot name="robot"><link name="base" /></robot>', encoding="utf-8")
            (root / "moveit2_robot.srdf").write_text('<robot name="robot" />', encoding="utf-8")
            (root / "moveit2_kinematics.yaml").write_text(
                "arm:\n  kinematics_solver_timeout: 0.05\n",
                encoding="utf-8",
            )
            (root / "moveit2_planning_pipelines.yaml").write_text(
                "planning_pipelines:\n  pipeline_names: [ompl]\n",
                encoding="utf-8",
            )
            (root / "moveit2_py.yaml").write_text(
                "planning_scene_monitor_options:\n  wait_for_initial_state_timeout: 1.0\n",
                encoding="utf-8",
            )
            request = SimpleNamespace(context={"urdfPath": str(urdf_path), "sidecarDir": str(root)})

            config = MoveItPyProvider()._config_dict(request)

            self.assertEqual(config["robot_description_semantic"], '<robot name="robot" />')
            self.assertEqual(config["robot_description_kinematics"]["arm"]["kinematics_solver_timeout"], 0.05)
            self.assertEqual(config["planning_pipelines"]["pipeline_names"], ["ompl"])
            self.assertEqual(config["planning_scene_monitor_options"]["wait_for_initial_state_timeout"], 5.0)

    def test_joint_state_seed_includes_all_active_urdf_joints(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            urdf_path = Path(tempdir) / "robot.urdf"
            urdf_path.write_text(
                """
                <robot name="robot">
                  <joint name="fixed_base" type="fixed" />
                  <joint name="shoulder" type="revolute" />
                  <joint name="slide" type="prismatic" />
                </robot>
                """,
                encoding="utf-8",
            )
            request = SimpleNamespace(
                context={"urdfPath": str(urdf_path)},
                payload={"startJointValuesByNameDeg": {"shoulder": 90, "slide": 0.25}},
                command={
                    "planningGroup": "arm",
                    "jointNames": ["shoulder"],
                },
            )

            names, positions = _joint_state_seed(request)

            self.assertEqual(names, ["shoulder", "slide"])
            self.assertAlmostEqual(positions[0], 1.5707963267948966)
            self.assertEqual(positions[1], 0.25)

    def test_serializes_robot_trajectory_messages_with_fallback_timing(self) -> None:
        class Duration:
            sec = 0
            nanosec = 0

        class Point:
            def __init__(self, positions: list[float]) -> None:
                self.time_from_start = Duration()
                self.positions = positions

        class JointTrajectory:
            joint_names = ["shoulder", "elbow"]
            points = [Point([0.0, 0.0]), Point([1.5707963267948966, 0.0])]

        class RobotTrajectoryMsg:
            joint_trajectory = JointTrajectory()

        class RobotTrajectory:
            def get_robot_trajectory_msg(self) -> RobotTrajectoryMsg:
                return RobotTrajectoryMsg()

        serialized = MoveItPyProvider()._serialize_trajectory(RobotTrajectory(), ["shoulder", "elbow"])

        self.assertEqual(serialized["jointNames"], ["shoulder", "elbow"])
        self.assertEqual(serialized["points"][0]["timeFromStartSec"], 0.0)
        self.assertGreater(serialized["points"][1]["timeFromStartSec"], 0.0)
        self.assertAlmostEqual(serialized["points"][1]["positionsDeg"][0], 90.0)


if __name__ == "__main__":
    unittest.main()
