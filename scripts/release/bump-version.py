#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
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


SKILL_NAMES = (
    "bambu-labs",
    "cad",
    "cad-viewer",
    "gcode",
    "sdf",
    "sendcutsend",
    "srdf",
    "step-parts",
    "urdf",
)


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
    JsonTarget(Path("plugins/cad/skills/cad-viewer/scripts/viewer/package.json"), (("version",),)),
    JsonTarget(Path("plugins/cad/.claude-plugin/plugin.json"), (("version",),)),
    JsonTarget(Path("plugins/cad/.codex-plugin/plugin.json"), (("version",),)),
    JsonTarget(Path(".claude-plugin/marketplace.json"), (("version",),), plugin_entries=("cad",)),
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
    JsonTarget(
        Path("plugins/cad/skills/cad-viewer/scripts/viewer/packages/cadjs/package.json"),
        (("version",),),
        required=False,
    ),
    JsonTarget(
        Path("plugins/cad/skills/cad-viewer/scripts/viewer/packages/cadjs/package-lock.json"),
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
    TomlTarget(Path("plugins/cad/skills/cad/scripts/packages/cadpy/pyproject.toml")),
    TomlTarget(Path("plugins/cad/skills/cad-viewer/scripts/viewer/moveit2_server/pyproject.toml")),
    TomlTarget(Path("plugins/cad/skills/cad-viewer/scripts/viewer/packages/cadpy/pyproject.toml")),
    TomlTarget(Path("plugins/cad/skills/sdf/scripts/packages/cadpy_metadata/pyproject.toml")),
    TomlTarget(Path("plugins/cad/skills/srdf/scripts/packages/cadpy_metadata/pyproject.toml")),
    TomlTarget(Path("plugins/cad/skills/urdf/scripts/packages/cadpy_metadata/pyproject.toml")),
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
    *(
        TextTarget(
            Path(f"skills/{skill}/SKILL.md"),
            r"Release version: `{old}`\.",
            "Release version: `{new}`.",
            f"{skill} source skill version",
        )
        for skill in SKILL_NAMES
    ),
    *(
        TextTarget(
            Path(f"plugins/cad/skills/{skill}/SKILL.md"),
            r"Release version: `{old}`\.",
            "Release version: `{new}`.",
            f"{skill} bundled skill version",
        )
        for skill in SKILL_NAMES
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


def semver_is_greater(left: str, right: str) -> bool:
    return parse_semver(left) > parse_semver(right)


def run_git(args: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture and result.stdout is not None else ""


def git_text_at_ref(ref: str, path: Path) -> str:
    if not ref or set(ref) == {"0"}:
        raise ValueError("base ref must be a real commit, not an empty all-zero ref")
    return run_git(["show", f"{ref}:{path.as_posix()}"], capture=True)


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


def check_versions_consistent() -> str:
    current_text = read_text(CANONICAL_VERSION_PATH)
    assert current_text is not None
    current_version = current_text.strip()
    parse_semver(current_version)

    # Reuse the normal bump planner as the single source of truth for every
    # release target and its current-version assertions. The planned patch bump
    # is thrown away; this check is read-only.
    collect_updates(current_version, bump_version(current_version, "patch"))
    return current_version


def check_version_incremented_from(base_ref: str) -> tuple[str, str]:
    current_version = check_versions_consistent()
    base_version = git_text_at_ref(base_ref, CANONICAL_VERSION_PATH).strip()
    parse_semver(base_version)
    if not semver_is_greater(current_version, base_version):
        raise ValueError(
            f"current version {current_version} must be greater than "
            f"{base_version} from {base_ref}"
        )
    return current_version, base_version


def stage_paths(paths: list[Path]) -> None:
    if not paths:
        return
    run_git(["add", "--", *[path.relative_to(REPO_ROOT).as_posix() for path in paths]])


def commit_version_bump(
    next_version: str,
    paths: list[Path],
    *,
    amend: bool,
    messages: list[str],
    no_edit: bool,
    no_verify: bool,
    signoff: bool,
) -> None:
    stage_paths(paths)
    command = ["commit"]
    if amend:
        command.append("--amend")
    if no_verify:
        command.append("--no-verify")
    if signoff:
        command.append("--signoff")
    if no_edit:
        command.append("--no-edit")
    elif messages:
        for message in messages:
            command.extend(["-m", message])
    elif amend:
        command.append("--no-edit")
    else:
        command.extend(["-m", f"Bump version to {next_version}"])
    run_git(command)


def tag_exists(tag_name: str) -> bool:
    try:
        run_git(["rev-parse", "--verify", "--quiet", f"refs/tags/{tag_name}"], capture=True)
        return True
    except subprocess.CalledProcessError:
        return False


def create_release_tag(tag_name: str, *, force: bool) -> None:
    if tag_exists(tag_name):
        tag_commit = run_git(["rev-list", "-n", "1", tag_name], capture=True)
        head_commit = run_git(["rev-parse", "HEAD"], capture=True)
        if tag_commit == head_commit:
            print(f"Release tag already exists on HEAD: {tag_name}")
            return
        if not force:
            raise ValueError(f"release tag already exists on a different commit: {tag_name}")
        run_git(["tag", "-f", tag_name, "HEAD"])
        print(f"Moved release tag: {tag_name}")
        return
    run_git(["tag", tag_name, "HEAD"])
    print(f"Created release tag: {tag_name}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bump the repo-owned release version across plugin, package, and generated-runtime metadata."
    )
    parser.add_argument("part", nargs="?", choices=("major", "minor", "patch"), help="semver part to bump")
    parser.add_argument("--set-version", help="set an exact X.Y.Z version instead of calculating a bump")
    parser.add_argument("--from-version", help="override the version expected in existing files")
    parser.add_argument("--dry-run", action="store_true", help="show planned edits without writing files")
    parser.add_argument("--check", action="store_true", help="verify all repo-owned release versions are consistent")
    parser.add_argument(
        "--check-incremented-from",
        metavar="REF",
        help="verify the current canonical version is greater than REF:plugins/cad/VERSION",
    )
    parser.add_argument("--commit", action="store_true", help="commit the version bump after writing files")
    parser.add_argument("--amend", action="store_true", help="amend the current commit with the version bump")
    parser.add_argument("-m", "--message", action="append", default=[], help="commit message; may be passed more than once")
    parser.add_argument("--no-edit", action="store_true", help="reuse the current commit message when amending")
    parser.add_argument("--no-verify", action="store_true", help="pass --no-verify to git commit")
    parser.add_argument("--signoff", action="store_true", help="pass --signoff to git commit")
    parser.add_argument("--tag", action="store_true", help="create a release tag named after the new version")
    parser.add_argument("--force-tag", action="store_true", help="move an existing local release tag to HEAD")
    args = parser.parse_args(argv)

    check_mode = args.check or bool(args.check_incremented_from)
    if check_mode:
        if args.part or args.set_version or args.from_version:
            parser.error("version bump arguments cannot be combined with --check or --check-incremented-from")
        if args.dry_run or args.commit or args.amend or args.message or args.no_edit or args.no_verify or args.signoff:
            parser.error("git commit arguments cannot be combined with version check modes")
        if args.tag or args.force_tag:
            parser.error("tag arguments cannot be combined with version check modes")
        return args

    if bool(args.part) == bool(args.set_version):
        parser.error("provide exactly one of major/minor/patch or --set-version")
    if args.set_version:
        parse_semver(args.set_version)
    if args.from_version:
        parse_semver(args.from_version)
    if args.commit and args.amend:
        parser.error("--commit and --amend are mutually exclusive")
    if args.no_edit and not args.amend:
        parser.error("--no-edit only applies with --amend")
    if args.no_edit and args.message:
        parser.error("--no-edit cannot be combined with --message")
    if args.tag and not (args.commit or args.amend) and not args.dry_run:
        parser.error("--tag requires --commit or --amend")
    if args.force_tag and not args.tag:
        parser.error("--force-tag requires --tag")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.check:
        version = check_versions_consistent()
        print(f"Release versions are consistent: {version}")
        return 0

    if args.check_incremented_from:
        current_version, base_version = check_version_incremented_from(args.check_incremented_from)
        print(
            f"Release version is incremented from {args.check_incremented_from}: "
            f"{base_version} -> {current_version}"
        )
        return 0

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
        if args.commit or args.amend:
            action = "amend the current commit" if args.amend else "create a commit"
            print(f"Would {action}.")
        if args.tag:
            print(f"Would create release tag: {next_version}")
        if not args.commit and not args.amend and not args.tag:
            print(f"Release prep command: scripts/release/create-github-release.sh --set-version {next_version}")
        return 0

    for path, text in updates.items():
        path.write_text(text, encoding="utf-8")
    print(f"Updated {len(updates)} file(s).")

    committed = args.commit or args.amend
    if committed:
        commit_version_bump(
            next_version,
            list(updates),
            amend=args.amend,
            messages=args.message,
            no_edit=args.no_edit,
            no_verify=args.no_verify,
            signoff=args.signoff,
        )
        print("Committed version bump.")
    if args.tag:
        create_release_tag(next_version, force=args.force_tag or args.amend)
    elif committed:
        print(f"Release tag to create separately: {next_version}")
    else:
        print(f"Release prep command: scripts/release/create-github-release.sh --set-version {next_version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
