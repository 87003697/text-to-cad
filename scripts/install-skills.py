#!/usr/bin/env python3
"""Install bundled agent skills into common provider skill directories."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


AGENT_CHOICES = (
    "codex",
    "claude",
    "gemini",
    "openclaw",
    "cursor",
    "vscode",
    "goose",
    "project",
    "all",
)

ALL_AGENT_TARGETS = (
    "codex",
    "claude",
    "gemini",
    "openclaw",
    "cursor",
    "vscode",
    "goose",
    "project",
)

EXCLUDED_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "coverage",
    "dist",
    "dist-verify",
    "htmlcov",
    "node_modules",
}

EXCLUDED_SUFFIXES = (".pyc", ".pyo")


@dataclass(frozen=True)
class Skill:
    name: str
    path: Path


@dataclass(frozen=True)
class AgentTarget:
    name: str
    label: str
    destination: Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_user_path(raw_path: str | Path) -> Path:
    return Path(raw_path).expanduser().resolve()


def path_from_env(env_name: str, default: str) -> Path:
    value = os.environ.get(env_name) or default
    return resolve_user_path(value)


def codex_destination() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return resolve_user_path(Path(codex_home) / "skills")
    return resolve_user_path(Path.home() / ".codex" / "skills")


def agent_target(agent: str) -> AgentTarget:
    destinations = {
        "codex": AgentTarget("codex", "OpenAI Codex", codex_destination()),
        "claude": AgentTarget(
            "claude",
            "Claude Code",
            path_from_env("CLAUDE_SKILLS_DIR", "~/.claude/skills"),
        ),
        "gemini": AgentTarget(
            "gemini",
            "Gemini CLI",
            path_from_env("GEMINI_SKILLS_DIR", ".gemini/skills"),
        ),
        "openclaw": AgentTarget(
            "openclaw",
            "OpenClaw",
            path_from_env("OPENCLAW_SKILLS_DIR", "~/.openclaw/skills"),
        ),
        "cursor": AgentTarget(
            "cursor",
            "Cursor",
            path_from_env("CURSOR_SKILLS_DIR", ".cursor/skills"),
        ),
        "vscode": AgentTarget(
            "vscode",
            "VS Code / Copilot",
            path_from_env("VSCODE_SKILLS_DIR", ".github/skills"),
        ),
        "goose": AgentTarget(
            "goose",
            "Goose",
            path_from_env("GOOSE_SKILLS_DIR", "~/.config/goose/skills"),
        ),
        "project": AgentTarget(
            "project",
            "Generic project",
            path_from_env("PROJECT_SKILLS_DIR", ".skills"),
        ),
    }
    return destinations[agent]


def frontmatter_name(skill_file: Path) -> str | None:
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    if not lines or lines[0].strip() != "---":
        return None

    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def discover_skills(root: Path) -> dict[str, Skill]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        raise SystemExit(f"Skills directory not found: {skills_root}")

    skills: dict[str, Skill] = {}
    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        name = frontmatter_name(skill_file) or skill_dir.name
        if name in skills:
            raise SystemExit(f"Duplicate skill name discovered: {name}")
        skills[name] = Skill(name=name, path=skill_dir)
    return skills


def ignored_copy_names(_directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
            ignored.add(name)
    return ignored


def remove_existing(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)


def install_skill(
    skill: Skill,
    target: AgentTarget,
    *,
    dry_run: bool,
    force: bool,
    link: bool,
) -> str:
    destination = target.destination / skill.name
    action = "link" if link else "copy"

    if destination.exists() or destination.is_symlink():
        try:
            same_path = destination.resolve() == skill.path.resolve()
        except OSError:
            same_path = False
        if same_path:
            return f"skip {skill.name}: already points at {skill.path}"
        if not force:
            return f"skip {skill.name}: {destination} already exists"
        if dry_run:
            return f"would replace {destination} and {action} {skill.path}"
        remove_existing(destination)

    if dry_run:
        return f"would {action} {skill.path} -> {destination}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    if link:
        destination.symlink_to(skill.path.resolve(), target_is_directory=True)
    else:
        shutil.copytree(
            skill.path,
            destination,
            ignore=ignored_copy_names,
            symlinks=True,
        )
    completed_action = "linked" if link else "copied"
    return f"{completed_action} {skill.name} -> {destination}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install this repository's bundled agent skills.",
    )
    parser.add_argument(
        "--agent",
        choices=AGENT_CHOICES,
        help="Target agent or 'all' for every supported target.",
    )
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="Install one skill by name. May be repeated. Defaults to all skills.",
    )
    parser.add_argument("--list", action="store_true", help="List discovered skills and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing files.")
    parser.add_argument("--force", action="store_true", help="Replace existing installed skill folders.")
    parser.add_argument("--link", action="store_true", help="Symlink skills instead of copying them.")

    args = parser.parse_args(argv)
    if not args.list and not args.agent:
        parser.error("--agent is required unless --list is used")
    return args


def selected_skills(all_skills: dict[str, Skill], names: list[str]) -> list[Skill]:
    if not names:
        return [all_skills[name] for name in sorted(all_skills)]

    selected = []
    for name in names:
        if name not in all_skills:
            available = ", ".join(sorted(all_skills))
            raise SystemExit(f"Unknown skill '{name}'. Available skills: {available}")
        selected.append(all_skills[name])
    return selected


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    skills = discover_skills(repo_root())

    if args.list:
        for name in sorted(skills):
            print(f"{name}\t{skills[name].path}")
        return 0

    agent_names = ALL_AGENT_TARGETS if args.agent == "all" else (args.agent,)
    targets = [agent_target(agent) for agent in agent_names]
    skills_to_install = selected_skills(skills, args.skill)

    for target in targets:
        print(f"{target.label}: {target.destination}")
        for skill in skills_to_install:
            print(f"  {install_skill(skill, target, dry_run=args.dry_run, force=args.force, link=args.link)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
