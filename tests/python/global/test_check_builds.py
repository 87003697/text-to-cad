from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
CHECK_BUILDS = REPO_ROOT / "scripts/github-workflows/check-builds.sh"


class CheckBuildsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        workflow_dir = self.repo / "scripts/github-workflows"
        bundle_dir = self.repo / "scripts/bundle"
        workflow_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)
        (self.repo / "skills/example/node_modules/package").mkdir(parents=True)
        (self.repo / "plugins/cad/skills/example/references").mkdir(parents=True)
        (self.repo / "generated").mkdir()

        shutil.copy2(CHECK_BUILDS, workflow_dir / "check-builds.sh")
        self._write_executable(
            bundle_dir / "bundle.sh",
            "#!/usr/bin/env bash\n"
            "if [ \"${1:-}\" = --print-outputs ]; then\n"
            "  printf '%s\\n' generated\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
        )

        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo,
            check=True,
        )
        bundled_manifest = self.repo / "plugins/cad/skills/example/SKILL.md"
        bundled_manifest.write_text("# Example\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "plugins/cad/skills/example/SKILL.md"],
            cwd=self.repo,
            check=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _write_executable(path: Path, contents: str) -> None:
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)

    def _run_check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "scripts/github-workflows/check-builds.sh",
                "--skip-bundle-check",
            ],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_discovers_generated_outputs_through_master_bundle_entrypoint(
        self,
    ) -> None:
        result = self._run_check()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Production bundle layout is valid.", result.stdout)

    def test_rejects_symlink_inside_generated_node_modules(self) -> None:
        node_modules = self.repo / "generated/node_modules"
        node_modules.mkdir()
        os.symlink("../target", node_modules / "dependency")

        result = self._run_check()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Production bundle paths must not contain symlinks.",
            result.stderr,
        )
        self.assertIn("generated/node_modules/dependency", result.stderr)

    def test_rejects_tracked_symlink_in_published_plugin_skill_tree(self) -> None:
        link = self.repo / "plugins/cad/skills/example/references/shared"
        os.symlink("../SKILL.md", link)
        subprocess.run(
            ["git", "add", "plugins/cad/skills/example/references/shared"],
            cwd=self.repo,
            check=True,
        )

        result = self._run_check()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Published plugin skills must not contain symlinks.", result.stderr)
        self.assertIn(
            "plugins/cad/skills/example/references/shared",
            result.stderr,
        )

    def test_ignores_untracked_source_skill_dependency_symlink(self) -> None:
        node_modules = self.repo / "skills/example/node_modules/package"
        os.symlink("../package", node_modules / "self")

        result = self._run_check()

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
