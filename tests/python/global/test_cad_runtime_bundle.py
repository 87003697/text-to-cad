from __future__ import annotations

import unittest
from pathlib import Path

from tests.python.support.paths import REPO_ROOT
VIEWER_BUNDLE = (
    REPO_ROOT / "scripts" / "bundle" / "skills" / "bundle-cad-viewer.sh"
)


class CadRuntimeBundleTests(unittest.TestCase):
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
