#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])

plugin_name = "cad"
version = "0.1.0"
skills = [
    "bambu-labs",
    "cad",
    "cad-viewer",
    "gcode",
    "sdf",
    "sendcutsend",
    "srdf",
    "step-parts",
    "urdf",
]

plugin_root = repo_root / "plugins" / plugin_name
codex_manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
claude_manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
claude_marketplace_path = repo_root / "plugins" / ".claude-plugin" / "marketplace.json"
gemini_manifest_path = plugin_root / "gemini-extension.json"
gemini_context_path = plugin_root / "GEMINI.md"
codex_marketplace_path = repo_root / ".agents" / "plugins" / "marketplace.json"
version_path = plugin_root / "VERSION"
errors: list[str] = []


def require(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


require(plugin_root.is_dir(), f"missing plugin directory: {plugin_root}")
require(codex_manifest_path.is_file(), f"missing Codex plugin manifest: {codex_manifest_path}")
require(claude_manifest_path.is_file(), f"missing Claude plugin manifest: {claude_manifest_path}")
require(claude_marketplace_path.is_file(), f"missing Claude marketplace: {claude_marketplace_path}")
require(gemini_manifest_path.is_file(), f"missing Gemini extension manifest: {gemini_manifest_path}")
require(gemini_context_path.is_file(), f"missing Gemini extension context: {gemini_context_path}")
require(codex_marketplace_path.is_file(), f"missing Codex marketplace: {codex_marketplace_path}")
require(version_path.is_file(), f"missing plugin version file: {version_path}")


def load_json_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON in {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{path} must contain a JSON object")
        return {}
    return payload


def validate_plugin_manifest(path: Path, provider: str) -> None:
    manifest = load_json_object(path)
    if not manifest:
        return
    require(manifest.get("name") == plugin_name, f"{provider} plugin manifest name is stale")
    require(manifest.get("version") == version, f"{provider} plugin manifest version is stale")
    require(manifest.get("skills") in {"./skills/", "./skills", "skills"}, f"{provider} plugin manifest must point at ./skills/")


validate_plugin_manifest(codex_manifest_path, "Codex")
validate_plugin_manifest(claude_manifest_path, "Claude")

gemini_manifest = load_json_object(gemini_manifest_path)
if gemini_manifest:
    require(gemini_manifest.get("name") == plugin_name, "Gemini extension manifest name is stale")
    require(gemini_manifest.get("version") == version, "Gemini extension manifest version is stale")
    require(gemini_manifest.get("contextFileName") == "GEMINI.md", "Gemini extension contextFileName is stale")

codex_marketplace = load_json_object(codex_marketplace_path)
if codex_marketplace:
    require(codex_marketplace.get("name") == "text-to-cad", "Codex marketplace name is stale")
    plugins = codex_marketplace.get("plugins")
    if not isinstance(plugins, list):
        errors.append("Codex marketplace plugins must be an array")
    else:
        cad_entries = [
            entry for entry in plugins
            if isinstance(entry, dict) and entry.get("name") == plugin_name
        ]
        require(len(cad_entries) == 1, "Codex marketplace must contain exactly one cad plugin entry")
        if cad_entries:
            source = cad_entries[0].get("source")
            if not isinstance(source, dict):
                errors.append("Codex marketplace cad source must be an object")
            else:
                require(source.get("path") == "./plugins/cad", "Codex marketplace cad source path is stale")

claude_marketplace = load_json_object(claude_marketplace_path)
if claude_marketplace:
    require(claude_marketplace.get("name") == "text-to-cad", "Claude marketplace name is stale")
    require(claude_marketplace.get("version") == version, "Claude marketplace version is stale")
    plugins = claude_marketplace.get("plugins")
    if not isinstance(plugins, list):
        errors.append("Claude marketplace plugins must be an array")
    else:
        cad_entries = [
            entry for entry in plugins
            if isinstance(entry, dict) and entry.get("name") == plugin_name
        ]
        require(len(cad_entries) == 1, "Claude marketplace must contain exactly one cad plugin entry")
        if cad_entries:
            entry = cad_entries[0]
            require(entry.get("source") == "./plugins/cad", "Claude marketplace cad source is stale")
            require(entry.get("version") == version, "Claude marketplace cad version is stale")

if version_path.is_file():
    require(version_path.read_text(encoding="utf-8").strip() == version, "cad plugin VERSION is stale")

skills_root = plugin_root / "skills"
for skill in skills:
    root_skill = repo_root / "skills" / skill
    bundled_skill = skills_root / skill
    require(root_skill.is_dir(), f"missing source skill: {root_skill}")
    require((root_skill / "SKILL.md").is_file(), f"missing source skill manifest: {root_skill / 'SKILL.md'}")
    require(bundled_skill.is_symlink(), f"plugin skill must be a symlink: {bundled_skill}")
    if bundled_skill.exists():
        try:
            resolved = bundled_skill.resolve(strict=True)
        except FileNotFoundError:
            errors.append(f"plugin skill symlink is broken: {bundled_skill}")
        else:
            require(resolved == root_skill.resolve(strict=True), f"plugin skill points at wrong target: {bundled_skill} -> {resolved}")

if skills_root.is_dir():
    bundled_names = sorted(path.name for path in skills_root.iterdir() if not path.name.startswith("."))
    require(bundled_names == skills, f"cad plugin bundled skill list is stale: {bundled_names}")

if errors:
    print("Plugin validation failed:")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print(f"Plugin validation passed: {plugin_root}")
PY
