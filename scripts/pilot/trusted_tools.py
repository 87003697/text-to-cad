#!/usr/bin/env python3
"""Exact manifest for the fixed files used by trusted candidate execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCHEMA = "text-to-cad.trusted-tools/1"
MANIFEST_RELATIVE = Path(".claude/trusted-tools-manifest.json")
CANONICAL_BUILD_RELATIVE = Path("skills/cad/scripts/canonical-build")
CADGEN_RUNTIME_RELATIVE = Path("skills/cad/scripts/packages/cadgen")
MESHSCOPE_RUNTIME_RELATIVE = Path(
    "skills/mesh-compare/scripts/packages/meshscope"
)
MESHSHOT_RUNTIME_RELATIVE = Path("skills/mesh-compare/scripts/packages/meshshot")

_MAPPINGS = (
    (CANONICAL_BUILD_RELATIVE, Path("canonical-build")),
    (CADGEN_RUNTIME_RELATIVE / "src/cadgen", Path("packages/cadgen/src/cadgen")),
    (
        MESHSCOPE_RUNTIME_RELATIVE / "src/meshscope",
        Path("packages/meshscope/src/meshscope"),
    ),
    (
        MESHSHOT_RUNTIME_RELATIVE / "src/meshshot",
        Path("packages/meshshot/src/meshshot"),
    ),
)
_SKIP_DIRS = {"__pycache__", "tests", "__tests__"}
_SKIP_SUFFIXES = {".md", ".pyc", ".pyo"}
_BUILD_SUFFIXES = {".c", ".cpp", ".dylib", ".h", ".o", ".pyd", ".so"}
_IGNORED_NAMES = {".DS_Store"}


class TrustedToolsError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entries(repo_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source_relative, mounted_root in _MAPPINGS:
        source = repo_root / source_relative
        if not source.is_dir():
            raise TrustedToolsError(f"missing trusted tool source: {source_relative}")
        try:
            source.resolve().relative_to(repo_root)
        except ValueError as exc:
            raise TrustedToolsError(
                f"trusted tool source escapes the shipped tree: {source_relative}"
            ) from exc
        for parent_name, dirnames, filenames in os.walk(source, followlinks=False):
            parent = Path(parent_name)
            for dirname in dirnames:
                if (parent / dirname).is_symlink():
                    raise TrustedToolsError(
                        f"symlink in trusted tool source: {parent / dirname}"
                    )
            dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
            for filename in sorted(filenames):
                path = parent / filename
                if path.is_symlink():
                    raise TrustedToolsError(f"symlink in trusted tool source: {path}")
                if (
                    path.suffix in _SKIP_SUFFIXES
                    or path.suffix in _BUILD_SUFFIXES
                    or filename.startswith("_native.")
                    or filename in _IGNORED_NAMES
                    or filename.startswith("test_")
                    or filename.endswith("_test.py")
                    or filename.endswith("~")
                ):
                    continue
                mounted = mounted_root / path.relative_to(source)
                entries.append(
                    {
                        "path": mounted.as_posix(),
                        "size": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
    entries.sort(key=lambda item: item["path"])
    return entries


def manifest_bytes(repo_root: Path) -> bytes:
    value = {"schema": SCHEMA, "files": _entries(repo_root.resolve())}
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def validate_trusted_tools(repo_root: Path) -> None:
    root = Path(repo_root).resolve()
    manifest = root / MANIFEST_RELATIVE
    try:
        current = manifest.read_bytes()
    except OSError as exc:
        raise TrustedToolsError("trusted_tools_manifest_missing") from exc
    if current != manifest_bytes(root):
        raise TrustedToolsError("trusted_tools_manifest_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.repo_root / MANIFEST_RELATIVE
    if args.check:
        validate_trusted_tools(args.repo_root)
        print("Trusted tool manifest is up to date.")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(manifest_bytes(args.repo_root))
        print(f"Bundled {MANIFEST_RELATIVE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
