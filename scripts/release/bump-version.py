#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_VERSION_PATH = Path("plugins/cad/VERSION")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True)
class JsonTarget:
    path: Path
    fields: tuple[tuple[str, ...], ...]
    plugin_entries: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class TomlTarget:
    path: Path
    required: bool = True


@dataclass(frozen=True)
class TextTarget:
    path: Path
    pattern: str
    replacement: str
    label: str
    expected_count: int = 1
    required: bool = True


@dataclass(frozen=True)
class PlannedChange:
    path: Path
    labels: tuple[str, ...]


JSON_TARGETS = (
    JsonTarget(Path("docs/package.json"), (("version",),)),
    JsonTarget(Path("docs/package-lock.json"), (("version",), ("packages", "", "version"))),
    JsonTarget(Path("packages/cadjs/package.json"), (("version",),)),
    JsonTarget(Path("packages/cadjs/package-lock.json"), (("version",), ("packages", "", "version"))),
    JsonTarget(Path("viewer/package.json"), (("version",),)),
    JsonTarget(
        Path("viewer/package-lock.json"),
        (("version",), ("packages", "", "version"), ("packages", "packages/cadjs", "version")),
    ),
    JsonTarget(Path("skills/cad-viewer/scripts/viewer/package.json"), (("version",),)),
    JsonTarget(Path("plugins/cad/.claude-plugin/plugin.json"), (("version",),)),
    JsonTarget(Path("plugins/cad/.codex-plugin/plugin.json"), (("version",),)),
    JsonTarget(Path("plugins/cad/gemini-extension.json"), (("version",),)),
    JsonTarget(Path("plugins/.claude-plugin/marketplace.json"), (("version",),), plugin_entries=("cad",)),
    JsonTarget(Path("viewer/packages/cadjs/package.json"), (("version",),), required=False),
    JsonTarget(
        Path("viewer/packages/cadjs/package-lock.json"),
        (("version",), ("packages", "", "version")),
        required=False,
    ),
    JsonTarget(Path("skills/cad-viewer/scripts/viewer/packages/cadjs/package.json"), (("version",),), required=False),
    JsonTarget(
        Path("skills/cad-viewer/scripts/viewer/packages/cadjs/package-lock.json"),
        (("version",), ("packages", "", "version")),
        required=False,
    ),
)


TOML_TARGETS = (
    TomlTarget(Path("packages/cadpy/pyproject.toml")),
    TomlTarget(Path("packages/cadpy_metadata/pyproject.toml")),
    TomlTarget(Path("viewer/moveit2_server/pyproject.toml")),
    TomlTarget(Path("viewer/packages/cadpy/pyproject.toml")),
    TomlTarget(Path("skills/cad-viewer/scripts/viewer/moveit2_server/pyproject.toml")),
    TomlTarget(Path("skills/cad-viewer/scripts/viewer/packages/cadpy/pyproject.toml")),
    TomlTarget(Path("skills/cad/scripts/packages/cadpy/pyproject.toml")),
    TomlTarget(Path("skills/sdf/scripts/packages/cadpy_metadata/pyproject.toml")),
    TomlTarget(Path("skills/srdf/scripts/packages/cadpy_metadata/pyproject.toml")),
    TomlTarget(Path("skills/urdf/scripts/packages/cadpy_metadata/pyproject.toml")),
)


TEXT_TARGETS = (
    TextTarget(
        Path("AGENTS.md"),
        r"current release version is `{old}`\.",
        "current release version is `{new}`.",
        "repo release guidance",
    ),
    TextTarget(
        Path("plugins/README.md"),
        r"versioned as `{old}`",
        "versioned as `{new}`",
        "plugin README version",
    ),
    TextTarget(
        Path("plugins/cad/README.md"),
        r"Version: `{old}`",
        "Version: `{new}`",
        "cad plugin README version",
    ),
    TextTarget(
        Path("scripts/check/validate-plugins.sh"),
        r'version = "{old}"',
        'version = "{new}"',
        "plugin validator expected version",
    ),
    TextTarget(
        Path("scripts/build/build-cad-viewer-skill.sh"),
        r'"version": "{old}",',
        '"version": "{new}",',
        "generated CAD Viewer runtime package template",
    ),
)


def parse_semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError(f"expected a plain semver version like 1.2.3, got {value!r}")
    return tuple(int(part) for part in match.groups())


def bump_version(current: str, part: str) -> str:
    major, minor, patch = parse_semver(current)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump part: {part}")


def read_text(path: Path, required: bool = True) -> str | None:
    absolute_path = REPO_ROOT / path
    if not absolute_path.is_file():
        if required:
            raise FileNotFoundError(f"missing required version target: {path}")
        return None
    return absolute_path.read_text(encoding="utf-8")


def require_current(path: Path, label: str, value: Any, current_version: str) -> None:
    if value != current_version:
        raise ValueError(f"{path} {label} is {value!r}, expected {current_version!r}")


def set_json_field(data: dict[str, Any], path: tuple[str, ...], current_version: str, next_version: str) -> str:
    cursor: Any = data
    for key in path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            raise KeyError(".".join(path))
        cursor = cursor[key]
    key = path[-1]
    if not isinstance(cursor, dict) or key not in cursor:
        raise KeyError(".".join(path))
    require_current(Path(".".join(path)), "value", cursor[key], current_version)
    cursor[key] = next_version
    return format_json_path(path)


def format_json_path(path: tuple[str, ...]) -> str:
    labels: list[str] = []
    for part in path:
        if part:
            labels.append(part)
        else:
            labels[-1] = f'{labels[-1]}[""]'
    return ".".join(labels)


def set_plugin_entry_version(data: dict[str, Any], plugin_name: str, current_version: str, next_version: str) -> str:
    entries = data.get("plugins")
    if not isinstance(entries, list):
        raise ValueError("plugins must be an array")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("name") == plugin_name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one plugin entry named {plugin_name!r}")
    entry = matches[0]
    require_current(Path(f"plugins[{plugin_name}].version"), "value", entry.get("version"), current_version)
    entry["version"] = next_version
    return f"plugins[{plugin_name}].version"


def plan_json_target(target: JsonTarget, current_version: str, next_version: str) -> tuple[str, tuple[str, ...]] | None:
    text = read_text(target.path, target.required)
    if text is None:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{target.path} must contain a JSON object")

    labels = [
        set_json_field(data, field, current_version, next_version)
        for field in target.fields
    ]
    labels.extend(
        set_plugin_entry_version(data, plugin_name, current_version, next_version)
        for plugin_name in target.plugin_entries
    )
    return json.dumps(data, indent=2) + "\n", tuple(labels)


def plan_toml_target(target: TomlTarget, current_version: str, next_version: str) -> tuple[str, tuple[str, ...]] | None:
    text = read_text(target.path, target.required)
    if text is None:
        return None

    matches = list(re.finditer(r'(?m)^(version\s*=\s*)"([^"]+)"', text))
    if len(matches) != 1:
        raise ValueError(f"{target.path} must contain exactly one double-quoted version field")
    match = matches[0]
    require_current(target.path, "version", match.group(2), current_version)
    updated = text[: match.start(2)] + next_version + text[match.end(2) :]
    return updated, ("version",)


def plan_text_target(target: TextTarget, current_version: str, next_version: str) -> tuple[str, tuple[str, ...]] | None:
    text = read_text(target.path, target.required)
    if text is None:
        return None

    pattern = target.pattern.format(old=re.escape(current_version))
    replacement = target.replacement.format(new=next_version)
    updated, count = re.subn(pattern, replacement, text)
    if count != target.expected_count:
        raise ValueError(
            f"{target.path} {target.label} matched {count} time(s), expected {target.expected_count}"
        )
    return updated, (target.label,)


def plan_version_file(current_version: str, next_version: str) -> tuple[str, tuple[str, ...]]:
    text = read_text(CANONICAL_VERSION_PATH)
    assert text is not None
    value = text.strip()
    require_current(CANONICAL_VERSION_PATH, "version", value, current_version)
    return f"{next_version}\n", ("canonical plugin version",)


def collect_updates(current_version: str, next_version: str) -> tuple[dict[Path, str], list[PlannedChange]]:
    updates: dict[Path, str] = {}
    changes: list[PlannedChange] = []

    def add(path: Path, planned: tuple[str, tuple[str, ...]] | None) -> None:
        if planned is None:
            return
        updated_text, labels = planned
        absolute_path = REPO_ROOT / path
        if updates.get(absolute_path) == updated_text:
            return
        updates[absolute_path] = updated_text
        changes.append(PlannedChange(path, labels))

    add(CANONICAL_VERSION_PATH, plan_version_file(current_version, next_version))
    for target in JSON_TARGETS:
        add(target.path, plan_json_target(target, current_version, next_version))
    for target in TOML_TARGETS:
        add(target.path, plan_toml_target(target, current_version, next_version))
    for target in TEXT_TARGETS:
        add(target.path, plan_text_target(target, current_version, next_version))

    return updates, changes


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bump the repo-owned release version across plugin, package, and generated-runtime metadata."
    )
    parser.add_argument("part", nargs="?", choices=("major", "minor", "patch"), help="semver part to bump")
    parser.add_argument("--set-version", help="set an exact X.Y.Z version instead of calculating a bump")
    parser.add_argument("--from-version", help="override the version expected in existing files")
    parser.add_argument("--dry-run", action="store_true", help="show planned edits without writing files")
    args = parser.parse_args(argv)

    if bool(args.part) == bool(args.set_version):
        parser.error("provide exactly one of major/minor/patch or --set-version")
    if args.set_version:
        parse_semver(args.set_version)
    if args.from_version:
        parse_semver(args.from_version)
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.from_version:
        current_version = args.from_version
    else:
        current_text = read_text(CANONICAL_VERSION_PATH)
        assert current_text is not None
        current_version = current_text.strip()
        parse_semver(current_version)

    next_version = args.set_version or bump_version(current_version, args.part)
    if next_version == current_version:
        raise ValueError(f"next version matches current version: {current_version}")

    updates, changes = collect_updates(current_version, next_version)

    print(f"Version bump: {current_version} -> {next_version}")
    for change in changes:
        labels = ", ".join(change.labels)
        print(f"- {change.path} ({labels})")

    if args.dry_run:
        print("Dry run only; no files changed.")
        print(f"Release tag to create separately: {next_version}")
        return 0

    for path, text in updates.items():
        path.write_text(text, encoding="utf-8")
    print(f"Updated {len(updates)} file(s).")
    print(f"Release tag to create separately: {next_version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
