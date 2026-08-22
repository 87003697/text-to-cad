from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/bundle/materialize-production-layout.sh"


class MaterializeProductionLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.tree = Path(self.temporary.name)
        bundle = self.tree / "scripts/bundle/bundle-skill.sh"
        bundle.parent.mkdir(parents=True)
        bundle.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' skills/cad/scripts/packages/cadgen\n"
        )
        bundle.chmod(0o755)
        target = self.tree / "packages/cadgen"
        target.mkdir(parents=True)
        (target / "pyproject.toml").write_text("[project]\n")
        link = self.tree / "skills/cad/scripts/packages/cadgen"
        link.parent.mkdir(parents=True)
        os.symlink("../../../../packages/cadgen", link)
        subprocess.run(["git", "init", "-q", self.tree], check=True)
        subprocess.run(["git", "-C", self.tree, "add", "."], check=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), "--tree", str(self.tree)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_dereferences_declared_symlink_into_physical_content(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.tree / "skills/cad/scripts/packages/cadgen"
        self.assertTrue(output.is_dir())
        self.assertFalse(output.is_symlink())
        self.assertTrue((output / "pyproject.toml").is_file())

    def test_rejects_tracked_skill_symlink_without_bundle_owner(self) -> None:
        rogue = self.tree / "skills/rogue"
        os.symlink("../packages", rogue)
        subprocess.run(["git", "-C", self.tree, "add", "skills/rogue"], check=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not owned", result.stderr)


if __name__ == "__main__":
    unittest.main()
