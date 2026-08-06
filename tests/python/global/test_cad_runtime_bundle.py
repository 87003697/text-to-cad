from __future__ import annotations

import os
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
        self.assertIn('"agent:start": "node scripts/start-agent-viewer.mjs"', script)
        self.assertIn('"$target_dir/scripts/start-agent-viewer.mjs"', script)
        self.assertEqual(script.count("--target=node20"), 2)
        self.assertNotIn("--target=node22", script)
        self.assertLess(
            script.index('if [ -f "$VIEWER_DIR/package-lock.json" ]'),
            script.index("command -v pnpm"),
        )


if __name__ == "__main__":
    unittest.main()
