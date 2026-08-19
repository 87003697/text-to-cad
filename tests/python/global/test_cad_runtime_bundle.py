from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.python.support.paths import REPO_ROOT
VIEWER_BUNDLE = (
    REPO_ROOT / "scripts" / "bundle" / "skills" / "bundle-cad-viewer.sh"
)
VIEWER_SKILL = REPO_ROOT / "skills" / "cad-viewer" / "SKILL.md"


class CadRuntimeBundleTests(unittest.TestCase):
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
