"""Regression tests for the plugin-owned canonical release version."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
VERSION_PATH = REPO_ROOT / "plugins" / "cad" / "VERSION"
ROOT_VERSION_PATH = REPO_ROOT / "VERSION"


class ReleaseVersionPathTest(unittest.TestCase):
    def test_plugins_cad_version_is_the_only_canonical_version_file(self) -> None:
        self.assertTrue(VERSION_PATH.is_file())
        self.assertRegex(
            VERSION_PATH.read_text(encoding="utf-8").strip(),
            re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"),
        )
        self.assertFalse(
            ROOT_VERSION_PATH.exists(),
            "do not migrate canonical version ownership to a root VERSION file",
        )

    def test_release_version_check_uses_the_plugin_owned_path(self) -> None:
        completed = subprocess.run(
            [str(REPO_ROOT / "scripts" / "release" / "check-version.sh")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        self.assertIn("plugins/cad/VERSION", completed.stdout)

    def test_derived_metadata_is_synced_from_the_plugin_owned_path(self) -> None:
        completed = subprocess.run(
            ["node", "scripts/release/sync-version.mjs", "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        self.assertIn("plugins/cad/VERSION", completed.stdout)


if __name__ == "__main__":
    unittest.main()
