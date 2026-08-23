"""Policy checks for the repo-root agent plugin package.

The repository root is the plugin package. Provider manifests live beside the
canonical ``skills/`` directory, so the plugin publishes every product skill
without a generated ``plugins/cad/skills`` mirror.

Version consistency is intentionally outside this test's scope. Release
tooling owns stamping derived version fields from the canonical root
``VERSION`` file.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_NAME = "cad"
MARKETPLACE_NAME = "text-to-cad"

CLAUDE_PLUGIN_PATH = REPO_ROOT / ".claude-plugin" / "plugin.json"
CODEX_PLUGIN_PATH = REPO_ROOT / ".codex-plugin" / "plugin.json"
CLAUDE_MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MARKETPLACE_PATH = REPO_ROOT / ".codex-plugin" / "marketplace.json"
VERSION_PATH = REPO_ROOT / "VERSION"
SKILLS_ROOT = REPO_ROOT / "skills"

EXPECTED_SKILLS = {
    "bambu-labs",
    "cad",
    "cad-viewer",
    "dfam-check",
    "dxf",
    "gcode",
    "mesh-compare",
    "mesh-inspect",
    "mesh-to-cad",
    "sdf",
    "sendcutsend",
    "srdf",
    "step-parts",
    "urdf",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PluginManifestPolicyTest(unittest.TestCase):
    def test_repo_root_package_files_exist(self) -> None:
        for path in (
            CLAUDE_PLUGIN_PATH,
            CODEX_PLUGIN_PATH,
            CLAUDE_MARKETPLACE_PATH,
            VERSION_PATH,
        ):
            self.assertTrue(
                path.is_file(),
                f"missing repo-root plugin file: {path.relative_to(REPO_ROOT)}",
            )

        self.assertFalse(
            CODEX_MARKETPLACE_PATH.exists(),
            ".codex-plugin/marketplace.json is not a consumed marketplace",
        )
        self.assertFalse(
            (REPO_ROOT / "plugins").exists(),
            "plugins/ was replaced by the repo-root plugin package",
        )

    def test_provider_manifests_point_at_canonical_skills(self) -> None:
        for path in (CLAUDE_PLUGIN_PATH, CODEX_PLUGIN_PATH):
            manifest = load_json(path)
            self.assertEqual(
                manifest.get("name"),
                PLUGIN_NAME,
                f"{path.relative_to(REPO_ROOT)} must declare {PLUGIN_NAME!r}",
            )
            self.assertEqual(
                manifest.get("skills"),
                "./skills/",
                f"{path.relative_to(REPO_ROOT)} must point at ./skills/",
            )

    def test_marketplace_sources_plugin_from_repository_root(self) -> None:
        marketplace = load_json(CLAUDE_MARKETPLACE_PATH)
        self.assertEqual(marketplace.get("name"), MARKETPLACE_NAME)
        self.assertEqual(
            marketplace.get("interface", {}).get("displayName"),
            "text-to-cad",
        )

        plugins = marketplace.get("plugins")
        self.assertIsInstance(plugins, list, "marketplace plugins must be an array")
        entries = [
            entry
            for entry in plugins
            if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME
        ]
        self.assertEqual(
            len(entries),
            1,
            f"marketplace must contain exactly one {PLUGIN_NAME!r} entry",
        )
        self.assertEqual(
            entries[0].get("source"),
            "./",
            "marketplace entry must source the plugin from the repository root",
        )

    def test_repo_root_plugin_publishes_all_current_skills(self) -> None:
        actual_skills = {
            path.name
            for path in SKILLS_ROOT.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        }
        self.assertEqual(
            actual_skills,
            EXPECTED_SKILLS,
            "update the repo-root plugin policy when the product skill set changes",
        )
        for skill_name in sorted(EXPECTED_SKILLS):
            self.assertTrue(
                (SKILLS_ROOT / skill_name / "SKILL.md").is_file(),
                f"missing skill manifest: skills/{skill_name}/SKILL.md",
            )

        self.assertTrue(
            {"mesh-compare", "mesh-inspect", "mesh-to-cad"}
            <= actual_skills,
            "repo-root plugin must publish the mesh workflow skills",
        )


if __name__ == "__main__":
    unittest.main()
