from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.python.support.paths import REPO_ROOT
from tests.python.support.tmp_root import temporary_directory


IMPLICIT_BUNDLE = (
    REPO_ROOT / "scripts" / "bundle" / "skills" / "bundle-implicit-cad.sh"
)
VIEWER_BUNDLE = (
    REPO_ROOT / "scripts" / "bundle" / "skills" / "bundle-cad-viewer.sh"
)
VIEWER_SKILL = REPO_ROOT / "skills" / "cad-viewer" / "SKILL.md"


class CadRuntimeBundleTests(unittest.TestCase):
    def test_implicit_runtime_dependency_copy_is_complete_and_replaces_stale_data(
        self,
    ) -> None:
        with temporary_directory(prefix="implicit-runtime-bundle-") as root_text:
            root = Path(root_text)
            source = root / "source"
            target = root / "runtime"
            for dependency in ("playwright", "playwright-core", "three", "gifenc"):
                directory = source / dependency
                directory.mkdir(parents=True)
                (directory / "package.json").write_text(
                    f'{{"name":"{dependency}"}}\n',
                    encoding="utf-8",
                )
            (target / "node_modules" / "stale-package").mkdir(parents=True)

            env = {
                **os.environ,
                "IMPLICITJS_RUNTIME_DIR": os.fspath(target),
                "IMPLICITJS_RUNTIME_NODE_MODULES_SOURCE": os.fspath(source),
            }
            result = subprocess.run(
                [os.fspath(IMPLICIT_BUNDLE)],
                cwd=REPO_ROOT,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                (target / "node_modules" / "stale-package").exists()
            )
            for dependency in ("playwright", "playwright-core", "three", "gifenc"):
                self.assertTrue(
                    (target / "node_modules" / dependency / "package.json").is_file()
                )
            self.assertEqual(
                list(target.parent.glob(f"{target.name}.node_modules-stage.*")),
                [],
            )

    def test_implicit_runtime_missing_dependency_preserves_existing_node_modules(
        self,
    ) -> None:
        with temporary_directory(prefix="implicit-runtime-fail-closed-") as root_text:
            root = Path(root_text)
            source = root / "source"
            target = root / "runtime"
            for dependency in ("playwright", "playwright-core", "three"):
                (source / dependency).mkdir(parents=True)
            marker = target / "node_modules" / "existing" / "marker.txt"
            marker.parent.mkdir(parents=True)
            marker.write_text("keep\n", encoding="utf-8")

            result = subprocess.run(
                [os.fspath(IMPLICIT_BUNDLE)],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "IMPLICITJS_RUNTIME_DIR": os.fspath(target),
                    "IMPLICITJS_RUNTIME_NODE_MODULES_SOURCE": os.fspath(source),
                },
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.is_file())
            self.assertEqual(
                list(target.parent.glob(f"{target.name}.node_modules-stage.*")),
                [],
            )

    def test_viewer_bundle_contract_targets_node20_and_embeds_launcher(self) -> None:
        script = VIEWER_BUNDLE.read_text(encoding="utf-8")
        self.assertIn('"viewer:open": "node scripts/start-agent-viewer.mjs"', script)
        self.assertIn('"agent:start": "node scripts/start-agent-viewer.mjs"', script)
        self.assertIn('"$target_dir/scripts/start-agent-viewer.mjs"', script)
        self.assertEqual(script.count("--target=node20"), 2)
        self.assertNotIn("--target=node22", script)
        self.assertLess(
            script.index('if [ -f "$VIEWER_DIR/package-lock.json" ]'),
            script.index("command -v pnpm"),
        )

    def test_viewer_skill_commands_exist_in_bundled_runtime_package(self) -> None:
        bundle_script = VIEWER_BUNDLE.read_text(encoding="utf-8")
        package_match = re.search(
            r'cat > "\$target_dir/package\.json" <<EOF\n(.*?)\nEOF',
            bundle_script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(package_match)
        package_text = package_match.group(1).replace(
            '"$RELEASE_VERSION"',
            '"0.0.0-test"',
        )
        runtime_package = json.loads(package_text)

        skill_text = VIEWER_SKILL.read_text(encoding="utf-8")
        documented_commands = set(
            re.findall(
                r"npm --prefix scripts/viewer run ([A-Za-z0-9:_-]+)",
                skill_text,
            )
        )
        self.assertTrue(documented_commands)
        self.assertEqual(
            documented_commands - set(runtime_package["scripts"]),
            set(),
            "Every npm command documented by cad-viewer/SKILL.md must exist "
            "in the bundled runtime package.",
        )


if __name__ == "__main__":
    unittest.main()
