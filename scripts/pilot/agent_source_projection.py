"""Agent Source Projection — Agent-only subset of installed skill source.

The Agent Source Projection is the explicit, purpose-bound subset of installed
skill source that the runner mounts into a candidate-only Agent Execution.
It contains the ``SKILL.md`` files and progressive ``references/`` for the
Modeling Agent's peer skills (mesh-to-cad, cad, mesh-compare, mesh-inspect,
cad-viewer) and nothing else. Trusted scripts (workspace publication,
Git/LFS, review compilation, Agent Surface handler, canonical build/mesh
tools, runner/supervisor) are never projected — the trusted supervisor
invokes them out-of-band.

This module is used at three points:

* ``bundle`` — materializes the projection from repo source into
  ``.claude/agent-source-projection/`` and writes a canonical manifest.
* ``bundle --check`` — fails closed when the checked-in projection is stale
  relative to source or manifest.
* ``pilot runner`` — verifies the projection before binding it into an
  isolated Agent sandbox and fails closed on any drift (missing/extra path,
  symlink, forbidden name, digest mismatch, wrong schema).

The projection root is a single canonical location, has a stable schema and
version, and contains no symlinks. All Agent-visible sandbox paths are stable
strings; source projection paths never appear in Agent prompts, bootstrap
contracts, results, or error strings.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECTION_SCHEMA = "text-to-cad.agent-source-projection/2"
PROJECTION_VERSION = "2"
PROJECTION_ROOT_REL = ".claude/agent-source-projection"
MANIFEST_NAME = "manifest.json"
SKILLS_SUBDIR = "skills"
CLIENT_SUBDIR = "agent-surface"
CLIENT_PROJECTED_REL = "agent-surface/client.py"

# Purpose-bound allowlist. Every entry is (source path relative to repo root,
# projected path relative to the projection root). The source is a dedicated,
# curated Agent-facing document or the fixed Agent Surface client script —
# never a canonical trusted skill document — and any file outside this list
# is a projection violation. A file in this list that is missing at
# verify-time is a projection violation.
ALLOWED_SOURCES: tuple[tuple[str, str], ...] = (
    (
        "skills/mesh-to-cad/agent-source/SKILL.md",
        "skills/mesh-to-cad/SKILL.md",
    ),
    (
        "skills/mesh-to-cad/agent-source/references/candidate-authoring.md",
        "skills/mesh-to-cad/references/candidate-authoring.md",
    ),
    (
        "skills/mesh-to-cad/agent-source/references/assessment.md",
        "skills/mesh-to-cad/references/assessment.md",
    ),
    (
        "scripts/pilot/agent_surface_client.py",
        CLIENT_PROJECTED_REL,
    ),
)

# Source paths that must never appear as the ``source`` half of an
# ``ALLOWED_SOURCES`` entry. Everything in this set is either canonical
# trusted skill documentation (which teaches Workspace CLI, authority
# layout, storage, Git/LFS, terminal handoff, runner/supervisor internals,
# raw reference paths, or arbitrary trusted commands) or a trusted runtime
# script. If a future maintainer adds one of these to the allowlist by
# mistake, ``compute_expected_entries`` refuses to build a manifest.
FORBIDDEN_ORIGINAL_SOURCES: frozenset[str] = frozenset(
    {
        "skills/mesh-to-cad/SKILL.md",
        "skills/mesh-to-cad/references/output-schemas.md",
        "skills/mesh-to-cad/references/reconstruction-spec.md",
        "skills/mesh-to-cad/references/workspace-contract.md",
        "skills/mesh-compare/SKILL.md",
        "skills/mesh-inspect/SKILL.md",
        "skills/cad/SKILL.md",
        "skills/cad-viewer/SKILL.md",
        "scripts/pilot/runner.py",
        "scripts/pilot/workspace_supervisor.py",
        "scripts/pilot/workspace.py",
        "scripts/pilot/workspace_core.py",
    }
)

# Names that must never appear anywhere inside the projection tree. Guards
# against a future manifest edit or partial materialization that mistakenly
# ships trusted supervisor scripts, Workspace publication code, review
# compilation, VCS state, or raw reference bytes.
FORBIDDEN_BASENAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".env",
        ".env.local",
        ".credentials",
        "credentials.json",
        "reference.vbsvo",
        "runner.py",
        "workspace.py",
        "workspace_core.py",
        "workspace_supervisor.py",
        "mesh-to-cad-workspace",
        "mesh-to-cad-review",
        "mesh-to-cad-agent-surface",
    }
)

# Path fragments that must never appear as any component in a projected
# relative path. Broader than FORBIDDEN_BASENAMES: catches directories under
# a forbidden name that were flattened into the projection by accident.
FORBIDDEN_PATH_COMPONENTS: frozenset[str] = frozenset(
    {
        ".git",
        "scripts",
        "agents",
        "work",
        "steps",
        "cycles",
        "final",
    }
)

# Case-insensitive substrings that must not appear in any projected file's
# bytes. This catches leaks of Workspace CLI usage, authority path layout,
# terminal/handoff choreography, raw reference bytes, Git/LFS wording,
# trusted runtime module names, review compilation, and known absolute
# host authority paths — including a mistakenly re-added canonical skill
# document that would evade the source-path allowlist because someone
# projected it under a sanitized filename. Kept small on purpose: the
# projection sources are dedicated Agent-facing documents plus the fixed
# client script, none of which need any of these tokens. Agent-visible
# sandbox mounts (``/candidate``, ``/agent-surface``, ``/workspace/repo``,
# ``/run/mesh-to-cad-agent-surface.sock``) are not on this list.
FORBIDDEN_CONTENT_TOKENS: tuple[str, ...] = (
    "mesh-to-cad-workspace",
    "workspace_core",
    "--workspace",
    "<EXP_DIR>",
    "$EXP_DIR",
    "input/reference.ply",
    "input/original",
    "reference.vbsvo",
    "terminal-validation",
    ".internal-terminal-validation",
    "git lfs",
    "runner.py",
    "workspace_supervisor.py",
    "workspace_supervisor",
    "mesh-to-cad-review",
    "voxblame",
    "publish-step-zero",
    "publish-cycle",
    "record-attempt",
    "begin-attempt",
    "final/manifest.json",
    "final/rebuild.json",
    ".text-to-cad-codex",
    "/opt/text-to-cad",
    "/home/pilot/.codex",
    "/home/pilot/.text-to-cad",
    "/private/tmp/",
    "final delivery",
    "trusted-tool-registry",
    "canonical-build",
    "plugin-publish-tree",
)


class ProjectionError(Exception):
    """The projection is missing, extra, tampered, or otherwise unusable."""


@dataclass(frozen=True)
class ProjectionEntry:
    """One file materialized into the projection."""

    path: str
    sha256: str
    size: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class ProjectionInventory:
    """Deterministic identity of one projection root."""

    schema: str
    version: str
    entries: tuple[ProjectionEntry, ...]
    digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "version": self.version,
            "digest": self.digest,
            "entries": [entry.as_dict() for entry in self.entries],
        }


def canonical_manifest_bytes(
    schema: str, version: str, entries: Iterable[ProjectionEntry]
) -> bytes:
    """Return the canonical bytes of a projection manifest."""

    value = {
        "schema": schema,
        "version": version,
        "entries": [entry.as_dict() for entry in entries],
    }
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _open_regular_readonly(path: Path) -> bytes:
    """Read ``path`` as a regular file, refusing symlinks and specials."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ProjectionError(
                f"projection source must be a regular file, not a symlink: {path.name}"
            ) from exc
        raise ProjectionError(f"cannot read projection source: {exc.strerror}") from exc
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise ProjectionError(
                f"projection source must be a regular file: {path.name}"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _validate_projected_relative(relative: str) -> None:
    """Reject any projected path that traverses or names a forbidden token."""

    if not relative or relative.startswith("/"):
        raise ProjectionError("projected path must be a non-empty relative path")
    if relative == MANIFEST_NAME:
        raise ProjectionError(
            "projected path cannot shadow the manifest filename"
        )
    parts = Path(relative).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ProjectionError(f"projected path traverses: {relative}")
    for part in parts:
        if part in FORBIDDEN_PATH_COMPONENTS or part in FORBIDDEN_BASENAMES:
            raise ProjectionError(f"projected path names forbidden token: {relative}")
    if parts[0] not in (SKILLS_SUBDIR, CLIENT_SUBDIR):
        raise ProjectionError(
            f"projected path must live under {SKILLS_SUBDIR}/ or {CLIENT_SUBDIR}/: {relative}"
        )


def _scan_content_for_forbidden(relative: str, body: bytes) -> None:
    """Reject bytes that contain any FORBIDDEN_CONTENT_TOKENS substring.

    The scan is case-insensitive on bytes, decoded via latin-1 so non-UTF-8
    payloads still hit the guard.
    """

    haystack = body.decode("latin-1").lower()
    for token in FORBIDDEN_CONTENT_TOKENS:
        needle = token.lower()
        if needle and needle in haystack:
            raise ProjectionError(
                f"projected file exposes forbidden token '{token}': {relative}"
            )


def compute_expected_entries(repo_root: Path) -> tuple[ProjectionEntry, ...]:
    """Return the manifest entries computed from live repo source bytes."""

    entries: list[ProjectionEntry] = []
    seen: set[str] = set()
    for source_rel, projected_rel in ALLOWED_SOURCES:
        if source_rel in FORBIDDEN_ORIGINAL_SOURCES:
            raise ProjectionError(
                f"projection source is a canonical trusted document: {source_rel}"
            )
        _validate_projected_relative(projected_rel)
        if projected_rel in seen:
            raise ProjectionError(
                f"duplicate projection entry: {projected_rel}"
            )
        seen.add(projected_rel)
        source_path = Path(repo_root) / source_rel
        if source_path.is_symlink():
            raise ProjectionError(
                "projection source cannot be a symlink"
            )
        try:
            body = _open_regular_readonly(source_path)
        except ProjectionError:
            raise
        _scan_content_for_forbidden(projected_rel, body)
        entries.append(
            ProjectionEntry(
                path=projected_rel,
                sha256=_sha256_bytes(body),
                size=len(body),
            )
        )
    entries.sort(key=lambda entry: entry.path)
    return tuple(entries)


def _projection_digest(schema: str, version: str, entries: Iterable[ProjectionEntry]) -> str:
    return _sha256_bytes(canonical_manifest_bytes(schema, version, entries))


def _write_regular_file(target: Path, body: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_TRUNC
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(target, flags, 0o644)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)


def _walk_projection_files(root: Path) -> list[Path]:
    """Return every regular file below ``root`` (excluding the manifest)."""

    collected: list[Path] = []
    for parent, dirnames, filenames in os.walk(root, followlinks=False):
        parent_path = Path(parent)
        for name in list(dirnames):
            entry = parent_path / name
            if entry.is_symlink():
                raise ProjectionError(
                    f"projection contains symlink directory: {entry.relative_to(root)}"
                )
        for name in filenames:
            entry = parent_path / name
            if entry.is_symlink():
                raise ProjectionError(
                    f"projection contains symlink: {entry.relative_to(root)}"
                )
            collected.append(entry)
    collected.sort()
    return collected


def materialize(
    repo_root: Path, target: Path, *, remove_existing: bool = True
) -> ProjectionInventory:
    """Materialize the Agent Source Projection into ``target``.

    ``target`` is the projection root (contains ``manifest.json`` and
    ``skills/...``). Existing physical files below ``target`` are removed
    when ``remove_existing`` is set so materialization is deterministic.
    Symlinks in the source tree cause a fail-closed error rather than being
    dereferenced.
    """

    target = Path(target)
    if target.is_symlink():
        raise ProjectionError("projection target cannot be a symlink")
    if remove_existing and target.exists():
        _remove_projection_tree(target)
    target.mkdir(parents=True, exist_ok=True)

    entries = compute_expected_entries(repo_root)
    manifest_body = canonical_manifest_bytes(
        PROJECTION_SCHEMA, PROJECTION_VERSION, entries
    )

    for source_rel, projected_rel in ALLOWED_SOURCES:
        source_path = Path(repo_root) / source_rel
        body = _open_regular_readonly(source_path)
        _write_regular_file(target / projected_rel, body)

    _write_regular_file(target / MANIFEST_NAME, manifest_body)
    return ProjectionInventory(
        schema=PROJECTION_SCHEMA,
        version=PROJECTION_VERSION,
        entries=entries,
        digest=_sha256_bytes(manifest_body),
    )


def _remove_projection_tree(target: Path) -> None:
    """Delete a physical projection tree without following symlinks."""

    for parent, dirnames, filenames in os.walk(target, topdown=False, followlinks=False):
        parent_path = Path(parent)
        for name in filenames:
            entry = parent_path / name
            try:
                entry.unlink()
            except FileNotFoundError:
                pass
        for name in dirnames:
            entry = parent_path / name
            if entry.is_symlink():
                entry.unlink()
            else:
                try:
                    entry.rmdir()
                except FileNotFoundError:
                    pass
    try:
        target.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        # A leftover file we do not own would fail here; verify() will catch it.
        raise ProjectionError("cannot clear projection target")


def verify(target: Path) -> ProjectionInventory:
    """Verify a materialized projection root and return its inventory.

    Fail-closed on: missing/extra files, forbidden names, symlinks,
    traversal, mode drift, missing manifest, malformed manifest, schema or
    version drift, and any content digest that does not match the manifest.
    """

    target = Path(target)
    if not target.is_dir() or target.is_symlink():
        raise ProjectionError("projection root is missing")
    manifest_path = target / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ProjectionError("projection manifest is missing")

    manifest_body = _open_regular_readonly(manifest_path)
    try:
        parsed = json.loads(manifest_body)
    except json.JSONDecodeError as exc:
        raise ProjectionError("projection manifest is malformed") from exc
    if not isinstance(parsed, dict):
        raise ProjectionError("projection manifest is malformed")
    if parsed.get("schema") != PROJECTION_SCHEMA:
        raise ProjectionError("projection manifest schema is unknown")
    if parsed.get("version") != PROJECTION_VERSION:
        raise ProjectionError("projection manifest version is unknown")
    raw_entries = parsed.get("entries")
    if not isinstance(raw_entries, list):
        raise ProjectionError("projection manifest is malformed")
    seen_paths: set[str] = set()
    manifest_entries: list[ProjectionEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            raise ProjectionError("projection manifest is malformed")
        entry_path = item.get("path")
        entry_sha = item.get("sha256")
        entry_size = item.get("size")
        if not (
            isinstance(entry_path, str)
            and isinstance(entry_sha, str)
            and isinstance(entry_size, int)
        ):
            raise ProjectionError("projection manifest is malformed")
        _validate_projected_relative(entry_path)
        if entry_path in seen_paths:
            raise ProjectionError("projection manifest lists duplicate paths")
        seen_paths.add(entry_path)
        manifest_entries.append(
            ProjectionEntry(path=entry_path, sha256=entry_sha, size=entry_size)
        )
    manifest_entries.sort(key=lambda entry: entry.path)
    expected_paths = {entry.path for entry in manifest_entries}

    physical_files = _walk_projection_files(target)
    physical_relatives: set[str] = set()
    for absolute in physical_files:
        relative = absolute.relative_to(target).as_posix()
        if relative == MANIFEST_NAME:
            continue
        _validate_projected_relative(relative)
        physical_relatives.add(relative)

    missing = expected_paths - physical_relatives
    if missing:
        raise ProjectionError(
            "projection is missing required files: " + ", ".join(sorted(missing))
        )
    extra = physical_relatives - expected_paths
    if extra:
        raise ProjectionError(
            "projection contains unexpected files: " + ", ".join(sorted(extra))
        )

    for entry in manifest_entries:
        absolute = target / entry.path
        body = _open_regular_readonly(absolute)
        if len(body) != entry.size or _sha256_bytes(body) != entry.sha256:
            raise ProjectionError(
                "projection file digest does not match manifest"
            )
        _scan_content_for_forbidden(entry.path, body)
    canonical_expected = canonical_manifest_bytes(
        PROJECTION_SCHEMA, PROJECTION_VERSION, manifest_entries
    )
    if manifest_body != canonical_expected:
        raise ProjectionError("projection manifest bytes are not canonical")
    return ProjectionInventory(
        schema=PROJECTION_SCHEMA,
        version=PROJECTION_VERSION,
        entries=tuple(manifest_entries),
        digest=_sha256_bytes(canonical_expected),
    )


def verify_matches_source(repo_root: Path, target: Path) -> ProjectionInventory:
    """Verify a projection root and require it to match live repo bytes."""

    inventory = verify(target)
    expected_entries = compute_expected_entries(repo_root)
    if inventory.entries != expected_entries:
        raise ProjectionError(
            "projection is stale relative to skill source; re-run bundle"
        )
    return inventory


def projected_skills_root(target: Path) -> Path:
    """Return the ``skills/`` subdirectory of a materialized projection."""

    return Path(target) / SKILLS_SUBDIR


def projected_agent_surface_client(target: Path) -> Path:
    """Return the Agent Surface client script inside a materialized projection."""

    return Path(target) / CLIENT_PROJECTED_REL


def _cli(argv: list[str]) -> int:
    """CLI entry: ``python agent_source_projection.py <materialize|verify|check> [args]``."""

    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    materialize_parser = sub.add_parser(
        "materialize",
        help="Materialize the projection into <target>.",
    )
    materialize_parser.add_argument("--repo-root", type=Path, required=True)
    materialize_parser.add_argument("--target", type=Path, required=True)

    check_parser = sub.add_parser(
        "check",
        help="Verify a materialized projection matches live skill source.",
    )
    check_parser.add_argument("--repo-root", type=Path, required=True)
    check_parser.add_argument("--target", type=Path, required=True)

    verify_parser = sub.add_parser(
        "verify",
        help="Verify a materialized projection against its embedded manifest.",
    )
    verify_parser.add_argument("--target", type=Path, required=True)

    print_parser = sub.add_parser(
        "print-outputs",
        help="Print projection output paths relative to the repository root.",
    )
    print_parser.add_argument("--repo-root", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "materialize":
            inventory = materialize(args.repo_root, args.target)
            print(f"agent-source-projection digest {inventory.digest}")
            return 0
        if args.command == "check":
            verify_matches_source(args.repo_root, args.target)
            print("agent-source-projection is up to date.")
            return 0
        if args.command == "verify":
            inventory = verify(args.target)
            print(f"agent-source-projection digest {inventory.digest}")
            return 0
        if args.command == "print-outputs":
            root_rel = Path(PROJECTION_ROOT_REL)
            print((root_rel / MANIFEST_NAME).as_posix())
            for _, projected_rel in ALLOWED_SOURCES:
                print((root_rel / projected_rel).as_posix())
            return 0
    except ProjectionError as exc:
        print(f"agent-source-projection: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
