"""Regression tests for scripts/release/finalize-publish-tree.sh.

The Release workflow and the installed-plugin smoke both delegate the trim +
pin + no-symlink rules to this one script. These tests pin the behaviour so
neither caller can silently regress.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/release/finalize-publish-tree.sh"
PIN_SCRIPT = REPO_ROOT / "scripts/release/pin-cadgen-requirements.sh"
PROJECTION_SCRIPT = REPO_ROOT / "scripts/pilot/agent_source_projection.py"
TRUSTED_TOOLS_SCRIPT = REPO_ROOT / "scripts/pilot/trusted_tools.py"


class FinalizePublishTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self._tmp.name) / "tree"
        self.tree.mkdir()
        # Mirror both release scripts so the finalize script can source its
        # sibling pin-cadgen-requirements.sh via SCRIPT_DIR resolution.
        (self.tree / "scripts/release").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.tree / "scripts/release/finalize-publish-tree.sh")
        shutil.copy2(PIN_SCRIPT, self.tree / "scripts/release/pin-cadgen-requirements.sh")
        (self.tree / "scripts/pilot").mkdir()
        shutil.copy2(PROJECTION_SCRIPT, self.tree / "scripts/pilot/agent_source_projection.py")
        shutil.copy2(TRUSTED_TOOLS_SCRIPT, self.tree / "scripts/pilot/trusted_tools.py")
        shutil.copytree(
            REPO_ROOT / ".claude/agent-source-projection",
            self.tree / ".claude/agent-source-projection",
        )
        os.chmod(self.tree / "scripts/release/finalize-publish-tree.sh", 0o755)
        os.chmod(self.tree / "scripts/release/pin-cadgen-requirements.sh", 0o755)
        (self.tree / "VERSION").write_text("9.9.9\n")
        (self.tree / "skills/cad-viewer/scripts/viewer").mkdir(parents=True)
        (self.tree / "skills/cad-viewer/scripts/viewer/package.json").write_text("{}\n")
        (self.tree / "skills/cad/scripts").mkdir(parents=True)
        (self.tree / "skills/cad/requirements.txt").write_text(
            "--editable ./scripts/packages/cadgen\n"
        )
        (self.tree / "skills/cad/scripts/packages/cadgen").mkdir(parents=True)
        (self.tree / "skills/cad/scripts/packages/cadgen/pyproject.toml").write_text(
            "[project]\nname='cadgen'\nversion='9.9.9'\n"
        )
        for trusted_root in (
            "skills/cad/scripts/canonical-build",
            "skills/cad/scripts/packages/cadgen/src/cadgen",
            "skills/mesh-compare/scripts/packages/meshscope/src/meshscope",
            "skills/mesh-compare/scripts/packages/meshshot/src/meshshot",
        ):
            root = self.tree / trusted_root
            root.mkdir(parents=True, exist_ok=True)
            (root / "runtime.py").write_text("# fixture\n")
        subprocess.run(
            [
                "python3",
                str(self.tree / "scripts/pilot/trusted_tools.py"),
                "--repo-root",
                str(self.tree),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        (self.tree / "docs").mkdir()
        (self.tree / "packages").mkdir()
        (self.tree / "models").mkdir()
        (self.tree / "tests").mkdir()
        (self.tree / "viewer").mkdir()
        (self.tree / "requirements-dev.txt").write_text("black\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(self.tree / "scripts/release/finalize-publish-tree.sh"),
                *extra,
            ],
            cwd=self.tree,
            capture_output=True,
            text=True,
        )

    def test_happy_path_trims_and_pins(self) -> None:
        result = self._run("--print-removed-roots")
        self.assertEqual(result.returncode, 0, result.stderr)
        # All removed roots gone.
        for removed in ("models", "viewer", "tests", "requirements-dev.txt", "docs", "packages"):
            self.assertFalse((self.tree / removed).exists(), f"{removed} should be trimmed")
        # cadgen requirement pinned to VERSION.
        self.assertIn(
            "cadgen==9.9.9",
            (self.tree / "skills/cad/requirements.txt").read_text(),
        )
        # Removed-roots list is printed on the last line.
        self.assertIn("models viewer tests requirements-dev.txt docs packages", result.stdout)

    def test_missing_viewer_runtime_fails_closed(self) -> None:
        (self.tree / "skills/cad-viewer/scripts/viewer/package.json").unlink()
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bundled CAD Viewer runtime", result.stderr)

    def test_symlink_in_publish_tree_fails_closed(self) -> None:
        # A symlink smuggled into skills/ must fail loud — this is the
        # regression that motivated the smoke in the first place.
        link_source = self.tree / "skills/cad/scripts/rogue"
        target = self.tree / "packages"
        target.mkdir(exist_ok=True)
        os.symlink(str(target), link_source)
        result = self._run()
        # The trim removes packages/ before the symlink check, leaving a dangling
        # symlink. Either the trim's own re-check or the final symlink scan must
        # catch it.
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_skill_referencing_repo_root_packages_fails_closed(self) -> None:
        (self.tree / "skills/cad/scripts/rogue.py").write_text(
            "import sys\nsys.path.insert(0, '../../../packages/cadgen/src')\n"
        )
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("packages/", result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
