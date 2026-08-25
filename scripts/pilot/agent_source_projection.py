"""Build and verify the five-file Agent Source Projection.

Bundling copies five explicit Agent-facing sources and checks them for known
authority-language leaks. Pilot runtime only verifies the shipped manifest
and physical tree; it never rebuilds the projection from a checkout.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECTION_SCHEMA = "text-to-cad.agent-source-projection/2"
PROJECTION_VERSION = "2"
PROJECTION_ROOT_REL = ".claude/agent-source-projection"
MANIFEST_NAME = "manifest.json"
SKILLS_SUBDIR = "skills"
CLIENT_PROJECTED_REL = "agent-surface/client.py"

# This is intentionally data, not a configurable projection framework.
SOURCE_MAPPINGS: tuple[tuple[str, str], ...] = (
    ("skills/mesh-to-cad/agent-source/SKILL.md", "skills/mesh-to-cad/SKILL.md"),
    (
        "skills/mesh-to-cad/agent-source/references/candidate-authoring.md",
        "skills/mesh-to-cad/references/candidate-authoring.md",
    ),
    (
        "skills/mesh-to-cad/agent-source/references/assessment.md",
        "skills/mesh-to-cad/references/assessment.md",
    ),
    (
        "skills/mesh-to-cad/agent-source/references/agent-selection-claim.md",
        "skills/mesh-to-cad/references/agent-selection-claim.md",
    ),
    ("scripts/pilot/agent_surface_client.py", CLIENT_PROJECTED_REL),
)
PROJECTED_PATHS = tuple(sorted(projected for _, projected in SOURCE_MAPPINGS))

# Bundle-time lint only. Exact source mappings are the primary boundary;
# these tokens catch accidental authority instructions in the curated files.
FORBIDDEN_CONTENT_TOKENS: tuple[str, ...] = (
    "mesh-to-cad-workspace",
    "workspace_supervisor",
    "input/reference.ply",
    "reference.vbsvo",
    ".internal-terminal-validation",
    "mesh-to-cad-review",
    "publish-step-zero",
    "publish-cycle",
    "record-attempt",
    "git lfs",
    "/private/tmp/",
)


class ProjectionError(RuntimeError):
    """The projection cannot be built or verified safely."""


@dataclass(frozen=True)
class ProjectionEntry:
    path: str
    sha256: str
    size: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class ProjectionInventory:
    schema: str
    version: str
    entries: tuple[ProjectionEntry, ...]
    digest: str


def _manifest_bytes(entries: tuple[ProjectionEntry, ...]) -> bytes:
    value = {
        "schema": PROJECTION_SCHEMA,
        "version": PROJECTION_VERSION,
        "entries": [entry.as_dict() for entry in entries],
    }
    return (json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n").encode()


def _read_regular(path: Path, *, label: str) -> bytes:
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ProjectionError(f"{label} must not be a symlink") from exc
        raise ProjectionError(f"cannot read {label}: {exc.strerror}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ProjectionError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _source_entries(repo_root: Path) -> tuple[tuple[ProjectionEntry, bytes], ...]:
    result: list[tuple[ProjectionEntry, bytes]] = []
    for source, projected in SOURCE_MAPPINGS:
        body = _read_regular(repo_root / source, label=f"projection source {source}")
        lowered = body.lower()
        for token in FORBIDDEN_CONTENT_TOKENS:
            if token.lower().encode() in lowered:
                raise ProjectionError(
                    f"projection source exposes forbidden token {token!r}: {source}"
                )
        result.append(
            (
                ProjectionEntry(projected, hashlib.sha256(body).hexdigest(), len(body)),
                body,
            )
        )
    return tuple(sorted(result, key=lambda pair: pair[0].path))


def bundle(repo_root: Path, target: Path) -> ProjectionInventory:
    """Write the projection into a new, empty target during bundling."""

    if target.exists() or target.is_symlink():
        raise ProjectionError("bundle target must not already exist")
    sources = _source_entries(repo_root)
    target.mkdir(parents=True)
    for entry, body in sources:
        destination = target / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
    entries = tuple(entry for entry, _ in sources)
    manifest = _manifest_bytes(entries)
    (target / MANIFEST_NAME).write_bytes(manifest)
    return ProjectionInventory(
        PROJECTION_SCHEMA,
        PROJECTION_VERSION,
        entries,
        hashlib.sha256(manifest).hexdigest(),
    )


def _physical_inventory(target: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for parent, dirnames, filenames in os.walk(target, followlinks=False):
        parent_path = Path(parent)
        for name in dirnames:
            entry = parent_path / name
            relative = entry.relative_to(target).as_posix()
            if entry.is_symlink():
                raise ProjectionError(f"projection contains symlink: {relative}")
            directories.add(relative)
        for name in filenames:
            entry = parent_path / name
            relative = entry.relative_to(target).as_posix()
            if entry.is_symlink():
                raise ProjectionError(f"projection contains symlink: {relative}")
            files.add(relative)
    return files, directories


def verify(target: Path) -> ProjectionInventory:
    """Verify the exact shipped projection without consulting repo source."""

    if not target.is_dir() or target.is_symlink():
        raise ProjectionError("projection root is missing")
    manifest_body = _read_regular(target / MANIFEST_NAME, label="projection manifest")
    try:
        value = json.loads(manifest_body)
        entries = tuple(ProjectionEntry(**item) for item in value["entries"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProjectionError("projection manifest is malformed") from exc
    if value.get("schema") != PROJECTION_SCHEMA or value.get("version") != PROJECTION_VERSION:
        raise ProjectionError("projection manifest schema or version is unknown")
    if tuple(entry.path for entry in entries) != PROJECTED_PATHS:
        raise ProjectionError("projection manifest does not name the five fixed files")
    if manifest_body != _manifest_bytes(entries):
        raise ProjectionError("projection manifest bytes are not canonical")

    expected_files = {MANIFEST_NAME, *PROJECTED_PATHS}
    expected_dirs = {
        str(parent)
        for path in PROJECTED_PATHS
        for parent in Path(path).parents
        if str(parent) != "."
    }
    files, directories = _physical_inventory(target)
    if files != expected_files or directories != expected_dirs:
        raise ProjectionError("projection physical inventory is not exact")
    for entry in entries:
        body = _read_regular(target / entry.path, label=f"projected file {entry.path}")
        if entry.size != len(body) or entry.sha256 != hashlib.sha256(body).hexdigest():
            raise ProjectionError("projection file digest does not match manifest")
    return ProjectionInventory(
        PROJECTION_SCHEMA,
        PROJECTION_VERSION,
        entries,
        hashlib.sha256(manifest_body).hexdigest(),
    )


def check_bundle(repo_root: Path, target: Path) -> ProjectionInventory:
    inventory = verify(target)
    expected = tuple(entry for entry, _ in _source_entries(repo_root))
    if inventory.entries != expected:
        raise ProjectionError("projection is stale; re-run bundle")
    return inventory


def projected_skills_root(target: Path) -> Path:
    return target / SKILLS_SUBDIR


def projected_agent_surface_client(target: Path) -> Path:
    return target / CLIENT_PROJECTED_REL


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("bundle", "check", "verify"))
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            inventory = verify(args.target)
        else:
            if args.repo_root is None:
                parser.error("--repo-root is required for bundle and check")
            inventory = (
                bundle(args.repo_root, args.target)
                if args.command == "bundle"
                else check_bundle(args.repo_root, args.target)
            )
        print(f"agent-source-projection digest {inventory.digest}")
        return 0
    except ProjectionError as exc:
        print(f"agent-source-projection: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
