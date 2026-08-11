#!/usr/bin/env python3
"""Create and verify one closed, complete provider-free execution authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any, Sequence


SCHEMA = "cvm.deployed-source-authority/1"
RECEIPT_PATH = ".cvm-deployment.json"
EXCLUDED_DIRECTORY_NAMES = (".git", "__pycache__", "node_modules")
EXCLUDED_FILE_SUFFIXES = (".dylib", ".pyc", ".pyd")
EXECUTION_AUTHORITY_PATHS = (
    "scripts/pilot",
    "skills/mesh-to-cad/scripts/mesh-to-cad-workspace",
    "skills/mesh-to-cad/scripts/mesh-to-cad-authority",
    "skills/mesh-compare/scripts/mesh-compare",
    "skills/mesh-compare/scripts/packages/meshscope",
    "skills/mesh-compare/scripts/packages/meshshot",
    "skills/cad/scripts/canonical-build",
    "skills/cad/scripts/packages",
    "skills/implicit-cad/scripts/packages/implicitjs",
    "skills/cad-viewer/scripts/viewer",
    "models/simple/rectangular_clamp_block.py",
    "models/simple/simple_model_library.py",
)


class DeploymentAuthorityError(RuntimeError):
    """The deployed execution tree cannot be represented or verified safely."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise DeploymentAuthorityError(f"unsafe execution-authority path: {value!r}")
    return Path(*pure.parts)


def _regular_files(root: Path, contract_paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for declared in contract_paths:
        relative = _safe_relative(declared)
        path = root / relative
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise DeploymentAuthorityError(f"missing execution-authority path: {declared}") from exc
        candidates = [path]
        if stat.S_ISDIR(mode):
            candidates = sorted(path.rglob("*"))
        for candidate in candidates:
            relative_text = candidate.relative_to(root).as_posix()
            try:
                candidate_mode = candidate.lstat().st_mode
            except OSError as exc:
                raise DeploymentAuthorityError(
                    f"cannot inspect execution-authority path: {relative_text}"
                ) from exc
            if stat.S_ISLNK(candidate_mode):
                raise DeploymentAuthorityError(
                    f"execution-authority tree contains a symlink: {relative_text}"
                )
            relative_parts = candidate.relative_to(root).parts
            if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative_parts):
                continue
            if stat.S_ISDIR(candidate_mode):
                continue
            if candidate.suffix in EXCLUDED_FILE_SUFFIXES:
                continue
            if not stat.S_ISREG(candidate_mode):
                raise DeploymentAuthorityError(
                    f"execution-authority tree contains a special file: {relative_text}"
                )
            if relative_text not in seen:
                seen.add(relative_text)
                files.append(candidate)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def build_receipt(
    root: str | Path,
    *,
    source_head: str,
    contract_paths: Sequence[str] = EXECUTION_AUTHORITY_PATHS,
) -> dict[str, Any]:
    """Hash every regular file under the closed execution-authority paths."""

    source_root = Path(root).resolve()
    if not source_root.is_dir():
        raise DeploymentAuthorityError("execution-authority root is not a directory")
    if len(source_head) not in {40, 64}:
        raise DeploymentAuthorityError("source identity is not a Git object identity")
    try:
        int(source_head, 16)
    except ValueError as exc:
        raise DeploymentAuthorityError("source identity is not hexadecimal") from exc
    normalized = tuple(_safe_relative(value).as_posix() for value in contract_paths)
    paths = _regular_files(source_root, normalized)
    files = []
    for path in paths:
        data = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(source_root).as_posix(),
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    if not files:
        raise DeploymentAuthorityError("execution-authority tree is empty")
    return {
        "schema": SCHEMA,
        "source_head": source_head.lower(),
        "contract_paths": list(normalized),
        "exclusions": {
            "directory_names": list(EXCLUDED_DIRECTORY_NAMES),
            "file_suffixes": list(EXCLUDED_FILE_SUFFIXES),
            "native_shared_objects_included": True,
        },
        "file_count": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
        "tree_sha256": hashlib.sha256(_canonical_bytes(files)).hexdigest(),
        "files": files,
    }


def verify_receipt(root: str | Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Recompute a deployed receipt from actual files and require exact equality."""

    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        raise DeploymentAuthorityError("deployed source receipt schema is invalid")
    expected = build_receipt(
        root,
        source_head=str(receipt.get("source_head", "")),
        contract_paths=receipt.get("contract_paths", ()),
    )
    if receipt != expected:
        raise DeploymentAuthorityError("deployed source tree does not match receipt")
    return receipt


def materialize_receipt(
    source_root: str | Path,
    receipt: dict[str, Any],
    destination: str | Path,
) -> dict[str, Any]:
    """Copy exactly the verified authority files into retained evidence."""

    source = Path(source_root).resolve()
    verify_receipt(source, receipt)
    target = Path(destination)
    if target.exists() or target.is_symlink():
        raise DeploymentAuthorityError("retained deployment target already exists")
    target.mkdir(parents=True)
    for item in receipt["files"]:
        relative = _safe_relative(item["path"])
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, output, follow_symlinks=False)
    return verify_materialized(target, receipt)


def verify_materialized(
    retained_root: str | Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Verify retained files without trusting their supplied digest list."""

    return verify_receipt(retained_root, receipt)


def write_receipt(
    root: str | Path,
    *,
    source_head: str,
) -> dict[str, Any]:
    source_root = Path(root).resolve()
    receipt = build_receipt(source_root, source_head=source_head)
    output = source_root / RECEIPT_PATH
    temporary = source_root / f".{Path(RECEIPT_PATH).name}.tmp"
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deployment-authority")
    parser.add_argument("command", choices=("write", "check"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-head")
    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            if not args.source_head:
                raise DeploymentAuthorityError("write requires --source-head")
            receipt = write_receipt(args.root, source_head=args.source_head)
        else:
            receipt = json.loads((args.root / RECEIPT_PATH).read_text(encoding="utf-8"))
            verify_receipt(args.root, receipt)
    except (OSError, json.JSONDecodeError, DeploymentAuthorityError) as exc:
        parser.exit(1, f"deployment authority failed: {exc}\n")
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
