from __future__ import annotations

import json
import pprint
import tempfile
from pathlib import Path
import unittest

from gen_motion_artifacts import cli


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_motion_source(path: Path, payload: object) -> None:
    path.write_text(
        "from __future__ import annotations\n\n"
        "def gen_motion():\n"
        f"    return {pprint.pformat(payload, width=100)}\n",
        encoding="utf-8",
    )


def sample_urdf() -> str:
    return """
    <robot name="sample">
      <link name="base_link" />
      <link name="shoulder_link" />
      <link name="tool_link" />
      <joint name="shoulder" type="revolute">
        <parent link="base_link" />
        <child link="shoulder_link" />
      </joint>
      <joint name="wrist" type="revolute">
        <parent link="shoulder_link" />
        <child link="tool_link" />
      </joint>
    </robot>
    """


def sample_motion() -> dict[str, object]:
    return {
        "urdf": "sample.urdf",
        "provider": "moveit_py",
        "commands": ["urdf.solvePose", "urdf.planToPose"],
        "planningGroup": "arm",
        "jointNames": ["shoulder", "wrist"],
        "endEffectors": [
            {
                "name": "tool",
                "link": "tool_link",
                "frame": "base_link",
                "parentLink": "shoulder_link",
                "positionTolerance": 0.003,
            }
        ],
        "planner": {
            "pipeline": "ompl",
            "plannerId": "RRTConnectkConfigDefault",
            "planningTime": 1.25,
        },
        "disabledCollisionPairs": [["base_link", "tool_link"]],
    }


def dual_arm_urdf() -> str:
    return """
    <robot name="dual_sample">
      <link name="base_link" />
      <link name="arm_1_shoulder_link" />
      <link name="arm_1_tool_link" />
      <link name="arm_2_shoulder_link" />
      <link name="arm_2_tool_link" />
      <joint name="arm_1_shoulder" type="revolute">
        <parent link="base_link" />
        <child link="arm_1_shoulder_link" />
      </joint>
      <joint name="arm_1_wrist" type="revolute">
        <parent link="arm_1_shoulder_link" />
        <child link="arm_1_tool_link" />
      </joint>
      <joint name="arm_2_shoulder" type="revolute">
        <parent link="base_link" />
        <child link="arm_2_shoulder_link" />
      </joint>
      <joint name="arm_2_wrist" type="revolute">
        <parent link="arm_2_shoulder_link" />
        <child link="arm_2_tool_link" />
      </joint>
    </robot>
    """


def dual_arm_motion() -> dict[str, object]:
    return {
        "urdf": "dual_sample.urdf",
        "provider": "moveit_py",
        "commands": ["urdf.solvePose", "urdf.planToPose"],
        "planningGroups": [
            {"name": "arm_1", "jointNames": ["arm_1_shoulder", "arm_1_wrist"]},
            {"name": "arm_2", "jointNames": ["arm_2_shoulder", "arm_2_wrist"]},
        ],
        "endEffectors": [
            {
                "name": "arm_1_tool",
                "link": "arm_1_tool_link",
                "frame": "base_link",
                "parentLink": "arm_1_shoulder_link",
                "planningGroup": "arm_1",
            },
            {
                "name": "arm_2_tool",
                "link": "arm_2_tool_link",
                "frame": "base_link",
                "parentLink": "arm_2_shoulder_link",
                "planningGroup": "arm_2",
            },
        ],
        "planner": {"planningTime": 1.0},
        "groupStates": [
            {"name": "home", "planningGroup": "arm_1", "jointValuesByNameRad": {"arm_1_shoulder": 0.0}},
            {"name": "home", "planningGroup": "arm_2", "jointValuesByNameRad": {"arm_2_shoulder": 0.0}},
        ],
    }


class GenMotionArtifactsCliTests(unittest.TestCase):
    def test_requires_explicit_target(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            cli.main([])
        self.assertEqual(2, cm.exception.code)

    def test_generates_motion_metadata_and_moveit_sidecars_under_robot_motion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            root = Path(tempdir)
            (root / "sample.urdf").write_text(sample_urdf(), encoding="utf-8")
            explorer_dir = root / ".sample.urdf"
            write_json(
                explorer_dir / "explorer.json",
                {
                    "schemaVersion": 3,
                    "kind": "texttocad-urdf-explorer",
                    "poses": [{"name": "home", "jointValuesByName": {"shoulder": 0}}],
                },
            )
            source_path = root / "sample_motion.py"
            write_motion_source(source_path, sample_motion())

            self.assertEqual(0, cli.generate_motion_artifact_targets([str(source_path)], summary=True))

            urdf_explorer_metadata = json.loads((explorer_dir / "explorer.json").read_text(encoding="utf-8"))
            self.assertEqual(urdf_explorer_metadata["poses"][0]["name"], "home")
            self.assertNotIn("motionServer", urdf_explorer_metadata)

            motion_dir = explorer_dir / "robot-motion"
            motion_explorer_metadata = json.loads((motion_dir / "explorer.json").read_text(encoding="utf-8"))
            self.assertEqual(motion_explorer_metadata["schemaVersion"], 1)
            self.assertEqual(motion_explorer_metadata["kind"], "texttocad-robot-motion-explorer")
            self.assertEqual(
                motion_explorer_metadata["motionServer"]["commands"]["urdf.solvePose"]["endEffectors"][0]["name"],
                "tool",
            )
            self.assertEqual(motion_explorer_metadata["motionServer"]["commands"]["urdf.planToPose"], {})
            motion_config = json.loads((motion_dir / "motion_server.json").read_text(encoding="utf-8"))
            self.assertEqual(motion_config["provider"], "moveit_py")
            self.assertEqual(motion_config["commands"]["urdf.planToPose"]["planner"]["planningTime"], 1.25)
            srdf = (motion_dir / "moveit2_robot.srdf").read_text(encoding="utf-8")
            self.assertIn('<group name="arm">', srdf)
            self.assertIn('<end_effector name="tool"', srdf)
            self.assertIn('link1="base_link" link2="tool_link"', srdf)
            self.assertEqual(
                json.loads((motion_dir / "moveit2_kinematics.yaml").read_text(encoding="utf-8"))["arm"][
                    "kinematics_solver"
                ],
                "kdl_kinematics_plugin/KDLKinematicsPlugin",
            )
            self.assertEqual(
                json.loads((motion_dir / "moveit2_planning_pipelines.yaml").read_text(encoding="utf-8"))[
                    "plan_request_params"
                ]["planning_time"],
                1.25,
            )

    def test_generates_multi_group_motion_sidecars_for_multiple_end_effectors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            root = Path(tempdir)
            (root / "dual_sample.urdf").write_text(dual_arm_urdf(), encoding="utf-8")
            source_path = root / "dual_motion.py"
            write_motion_source(source_path, dual_arm_motion())

            self.assertEqual(0, cli.generate_motion_artifact_targets([str(source_path)], summary=True))

            motion_dir = root / ".dual_sample.urdf/robot-motion"
            motion_config = json.loads((motion_dir / "motion_server.json").read_text(encoding="utf-8"))
            solve_pose = motion_config["commands"]["urdf.solvePose"]
            end_effectors = {end_effector["name"]: end_effector for end_effector in solve_pose["endEffectors"]}
            self.assertEqual("arm_2", end_effectors["arm_2_tool"]["planningGroup"])
            self.assertEqual(["arm_2_shoulder", "arm_2_wrist"], end_effectors["arm_2_tool"]["jointNames"])
            self.assertIn("urdf.planToPose", motion_config["commands"])

            srdf = (motion_dir / "moveit2_robot.srdf").read_text(encoding="utf-8")
            self.assertIn('<group name="arm_1">', srdf)
            self.assertIn('<group name="arm_2">', srdf)
            self.assertIn('end_effector name="arm_2_tool"', srdf)
            self.assertIn('parent_group="arm_2"', srdf)
            self.assertEqual(
                sorted(json.loads((motion_dir / "moveit2_kinematics.yaml").read_text(encoding="utf-8"))),
                ["arm_1", "arm_2"],
            )

    def test_ik_only_sources_omit_planning_pipeline_sidecar(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            root = Path(tempdir)
            (root / "sample.urdf").write_text(sample_urdf(), encoding="utf-8")
            payload = sample_motion()
            payload["commands"] = ["urdf.solvePose"]
            payload.pop("planner")
            source_path = root / "sample_motion.py"
            write_motion_source(source_path, payload)

            self.assertEqual(0, cli.generate_motion_artifact_targets([str(source_path)]))

            motion_dir = root / ".sample.urdf/robot-motion"
            metadata = json.loads((motion_dir / "explorer.json").read_text(encoding="utf-8"))
            self.assertIn("urdf.solvePose", metadata["motionServer"]["commands"])
            self.assertNotIn("urdf.planToPose", metadata["motionServer"]["commands"])
            self.assertFalse((motion_dir / "moveit2_planning_pipelines.yaml").exists())

    def test_rejects_json_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            root = Path(tempdir)
            spec_path = root / "sample.motion.json"
            write_json(spec_path, sample_motion())

            with self.assertRaisesRegex(cli.MotionArtifactError, "Python motion source"):
                cli.generate_motion_artifact_targets([str(spec_path)])

    def test_rejects_missing_gen_motion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            source_path = Path(tempdir) / "sample_motion.py"
            source_path.write_text("VALUE = 1\n", encoding="utf-8")

            with self.assertRaisesRegex(cli.MotionArtifactError, "must define gen_motion"):
                cli.generate_motion_artifact_targets([str(source_path)])

    def test_rejects_gen_motion_that_requires_arguments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            source_path = Path(tempdir) / "sample_motion.py"
            source_path.write_text("def gen_motion(name):\n    return {}\n", encoding="utf-8")

            with self.assertRaisesRegex(cli.MotionArtifactError, "must not require arguments"):
                cli.generate_motion_artifact_targets([str(source_path)])

    def test_rejects_non_object_gen_motion_return(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            source_path = Path(tempdir) / "sample_motion.py"
            source_path.write_text("def gen_motion():\n    return []\n", encoding="utf-8")

            with self.assertRaisesRegex(cli.MotionArtifactError, "must return an object"):
                cli.generate_motion_artifact_targets([str(source_path)])

    def test_rejects_missing_joint_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tmp-gen-motion-") as tempdir:
            root = Path(tempdir)
            (root / "sample.urdf").write_text(sample_urdf(), encoding="utf-8")
            payload = sample_motion()
            payload["jointNames"] = ["missing"]
            source_path = root / "sample_motion.py"
            write_motion_source(source_path, payload)

            with self.assertRaisesRegex(cli.MotionArtifactError, "missing joint"):
                cli.generate_motion_artifact_targets([str(source_path)])


if __name__ == "__main__":
    unittest.main()
