from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from cadpy_metadata.generator import GenerationOutput, track_generation_run


class MetadataGenerationStatusTests(unittest.TestCase):
    def test_generation_status_uses_hidden_lock_next_to_output_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadpy-metadata-generation-status-") as tempdir:
            workspace = Path(tempdir) / "workspace"
            model_dir = workspace / "models"
            skill_dir = Path(tempdir) / "installed-skill"
            source_path = model_dir / "robot_urdf.py"
            urdf_path = model_dir / "robot.urdf"
            model_dir.mkdir(parents=True)
            source_path.write_text("def gen_urdf():\n    return '<robot />'\n", encoding="utf-8")
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")

            with track_generation_run(
                source_path=source_path,
                generator="gen_urdf",
                outputs=[GenerationOutput(urdf_path, "urdf")],
                repo_root=skill_dir,
            ):
                lock_paths = tuple(model_dir.glob(".robot.urdf.*.generation.lock.json"))
                self.assertEqual(1, len(lock_paths))
                payload = json.loads(lock_paths[0].read_text(encoding="utf-8"))
                self.assertEqual("running", payload["status"])
                self.assertEqual("gen_urdf", payload["generator"])
                self.assertEqual(str(urdf_path.resolve()), payload["outputs"][0]["path"])
                self.assertFalse(tuple(skill_dir.rglob("*.generation.lock.json")))

            self.assertFalse(lock_paths[0].exists())

    def test_generation_status_writes_one_lock_for_each_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadpy-metadata-generation-status-") as tempdir:
            model_dir = Path(tempdir) / "models"
            urdf_path = model_dir / "robot.urdf"
            srdf_path = model_dir / "robot.srdf"
            model_dir.mkdir(parents=True)

            with track_generation_run(
                source_path=None,
                generator="gen_srdf",
                outputs=[
                    GenerationOutput(urdf_path, "urdf"),
                    GenerationOutput(srdf_path, "srdf"),
                ],
                repo_root=Path(tempdir),
            ):
                urdf_locks = tuple(model_dir.glob(".robot.urdf.*.generation.lock.json"))
                srdf_locks = tuple(model_dir.glob(".robot.srdf.*.generation.lock.json"))
                self.assertEqual(1, len(urdf_locks))
                self.assertEqual(1, len(srdf_locks))
                self.assertNotEqual(urdf_locks[0], srdf_locks[0])

            self.assertFalse(tuple(model_dir.glob("*.generation.lock.json")))


if __name__ == "__main__":
    unittest.main()
