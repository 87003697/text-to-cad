"""Policy checks for the versioned ``plugins/cad`` package."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "cad"
PLUGIN_NAME = "cad"

CLAUDE_MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MARKETPLACE_PATH = REPO_ROOT / ".codex-plugin" / "marketplace.json"
CLAUDE_PLUGIN_PATH = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
CODEX_PLUGIN_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
VERSION_PATH = PLUGIN_ROOT / "VERSION"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def product_skills(root: Path) -> set[str]:
    return {
        path.parent.name
        for path in root.glob("*/SKILL.md")
        if not path.parent.name.startswith(".")
    }


class PluginManifestPolicyTest(unittest.TestCase):
    def test_provider_manifests_remain_in_the_versioned_plugin(self) -> None:
        version = VERSION_PATH.read_text(encoding="utf-8").strip()

        for path in (CLAUDE_PLUGIN_PATH, CODEX_PLUGIN_PATH):
            manifest = load_json(path)
            self.assertEqual(manifest.get("name"), PLUGIN_NAME)
            self.assertEqual(manifest.get("version"), version)
            self.assertEqual(manifest.get("skills"), "./skills/")

        self.assertFalse(
            (REPO_ROOT / ".claude-plugin" / "plugin.json").exists(),
            "the repository root is a marketplace, not the Claude plugin package",
        )
        self.assertFalse(
            (REPO_ROOT / ".codex-plugin" / "plugin.json").exists(),
            "the repository root is a marketplace, not the Codex plugin package",
        )

    def test_both_marketplaces_source_plugins_cad(self) -> None:
        claude = load_json(CLAUDE_MARKETPLACE_PATH)
        codex = load_json(CODEX_MARKETPLACE_PATH)

        claude_entries = [
            entry
            for entry in claude.get("plugins", [])
            if entry.get("name") == PLUGIN_NAME
        ]
        codex_entries = [
            entry
            for entry in codex.get("plugins", [])
            if entry.get("name") == PLUGIN_NAME
        ]

        self.assertEqual(len(claude_entries), 1)
        self.assertEqual(claude_entries[0].get("source"), "./plugins/cad")
        self.assertEqual(len(codex_entries), 1)
        self.assertEqual(
            codex_entries[0].get("source"),
            {"source": "local", "path": "./plugins/cad"},
        )

    def test_plugin_package_publishes_every_product_skill(self) -> None:
        source_skills = product_skills(REPO_ROOT / "skills")
        packaged_skills = product_skills(PLUGIN_ROOT / "skills")

        self.assertTrue(source_skills, "expected product skills under skills/")
        self.assertEqual(
            packaged_skills,
            source_skills,
            "plugins/cad/skills must contain every product skill and no stale skill",
        )

    def test_plugin_skills_use_a_supported_develop_or_production_layout(self) -> None:
        for skill in product_skills(REPO_ROOT / "skills"):
            packaged_skill = PLUGIN_ROOT / "skills" / skill
            if packaged_skill.is_symlink():
                self.assertEqual(
                    packaged_skill.resolve(),
                    (REPO_ROOT / "skills" / skill).resolve(),
                )
                continue

            self.assertTrue(packaged_skill.is_dir())
            first_link = next(
                (path for path in packaged_skill.rglob("*") if path.is_symlink()),
                None,
            )
            self.assertIsNone(
                first_link,
                f"production plugin skill must be self-contained: {first_link}",
            )


if __name__ == "__main__":
    unittest.main()
