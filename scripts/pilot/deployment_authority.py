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
import subprocess
from typing import Any, Sequence


SCHEMA = "cvm.deployed-source-authority/1"
RECEIPT_PATH = ".cvm-deployment.json"
EXCLUDED_DIRECTORY_NAMES = (".git", "__pycache__", "node_modules")
EXCLUDED_FILE_SUFFIXES = (".dylib", ".pyc", ".pyd")
TRUSTED_BWRAP_PATH = Path("/usr/bin/bwrap")
SANDBOX_BROWSER_CACHE = "/home/provider-free/.cache/ms-playwright"
CADPY_RUNTIME_PATH = "skills/cad/scripts/packages/cadpy/src/cadpy/__init__.py"
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


def _physical_path(root: Path, relative: Path) -> Path:
    """Resolve one authority path without crossing a symlink or root boundary."""

    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise DeploymentAuthorityError(
                f"missing execution-authority path: {relative.as_posix()}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise DeploymentAuthorityError(
                f"execution-authority path has a symlink ancestor: {current}"
            )
        try:
            current.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise DeploymentAuthorityError(
                f"execution-authority path escapes root: {relative.as_posix()}"
            ) from exc
    return current


def _physical_absolute(path: Path) -> Path:
    if not path.is_absolute():
        raise DeploymentAuthorityError("runtime identity path must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise DeploymentAuthorityError(f"runtime identity path is missing: {path}") from exc
        if stat.S_ISLNK(mode):
            raise DeploymentAuthorityError(f"runtime identity path contains a symlink: {path}")
    if not current.is_file():
        raise DeploymentAuthorityError(f"runtime identity path is not a file: {path}")
    return current


def probe_runtime_identity(
    root: str | Path,
    *,
    chromium_revision: str,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Measure the fixed external and bundled provider-free runtime."""

    if not chromium_revision.isdigit():
        raise DeploymentAuthorityError("Chromium revision must be numeric")
    source_root = Path(root).resolve(strict=True)
    bwrap = _physical_absolute(TRUSTED_BWRAP_PATH)
    try:
        version_result = subprocess.run(
            [os.fspath(bwrap), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentAuthorityError("trusted bwrap version probe failed") from exc
    version = " ".join(version_result.stdout.split())
    if not version.startswith("bubblewrap "):
        raise DeploymentAuthorityError("trusted bwrap version is invalid")
    host_cache = (
        Path(home).expanduser().resolve(strict=True)
        if home is not None
        else Path.home().resolve(strict=True)
    ) / ".cache/ms-playwright"
    browser = _physical_absolute(
        host_cache
        / f"chromium_headless_shell-{chromium_revision}"
        / "chrome-headless-shell-linux64/chrome-headless-shell"
    )
    cadpy = _physical_path(source_root, _safe_relative(CADPY_RUNTIME_PATH))
    return {
        "schema": "cvm.provider-free-runtime-identity/1",
        "bwrap": {
            "path": os.fspath(bwrap),
            "sha256": hashlib.sha256(bwrap.read_bytes()).hexdigest(),
            "version": version,
        },
        "chromium": {
            "revision": chromium_revision,
            "host_cache_path": os.fspath(host_cache),
            "sandbox_cache_path": SANDBOX_BROWSER_CACHE,
            "executable_path": os.fspath(browser),
            "sha256": hashlib.sha256(browser.read_bytes()).hexdigest(),
        },
        "cadpy": {
            "path": CADPY_RUNTIME_PATH,
            "sha256": hashlib.sha256(cadpy.read_bytes()).hexdigest(),
        },
    }


def validate_runtime_identity(
    root: str | Path,
    identity: object,
    *,
    verify_external: bool,
) -> dict[str, Any]:
    """Validate the closed runtime identity and optionally remeasure host files."""

    if not isinstance(identity, dict) or set(identity) != {
        "schema",
        "bwrap",
        "chromium",
        "cadpy",
    }:
        raise DeploymentAuthorityError("provider-free runtime identity is incomplete")
    if identity.get("schema") != "cvm.provider-free-runtime-identity/1":
        raise DeploymentAuthorityError("provider-free runtime identity schema is invalid")
    bwrap = identity.get("bwrap")
    chromium = identity.get("chromium")
    cadpy = identity.get("cadpy")
    chromium_cache = PurePosixPath(
        str(chromium.get("host_cache_path", ""))
        if isinstance(chromium, dict)
        else ""
    )
    def valid_sha256(value: object) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(
            character in "0123456789abcdef" for character in value
        )

    if (
        not isinstance(bwrap, dict)
        or set(bwrap) != {"path", "sha256", "version"}
        or bwrap.get("path") != os.fspath(TRUSTED_BWRAP_PATH)
        or not str(bwrap.get("version", "")).startswith("bubblewrap ")
        or not valid_sha256(bwrap.get("sha256"))
        or not isinstance(chromium, dict)
        or set(chromium)
        != {
            "revision",
            "host_cache_path",
            "sandbox_cache_path",
            "executable_path",
            "sha256",
        }
        or not str(chromium.get("revision", "")).isdigit()
        or chromium.get("sandbox_cache_path") != SANDBOX_BROWSER_CACHE
        or not chromium_cache.is_absolute()
        or any(part in {".", ".."} for part in chromium_cache.parts)
        or chromium_cache.as_posix() != chromium.get("host_cache_path")
        or chromium.get("executable_path")
        != os.fspath(
            Path(str(chromium.get("host_cache_path", "")))
            / f"chromium_headless_shell-{chromium.get('revision')}"
            / "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        or not valid_sha256(chromium.get("sha256"))
        or not isinstance(cadpy, dict)
        or set(cadpy) != {"path", "sha256"}
        or cadpy.get("path") != CADPY_RUNTIME_PATH
        or not valid_sha256(cadpy.get("sha256"))
    ):
        raise DeploymentAuthorityError("provider-free runtime identity fields are invalid")
    cadpy_path = _physical_path(Path(root).resolve(strict=True), _safe_relative(CADPY_RUNTIME_PATH))
    if hashlib.sha256(cadpy_path.read_bytes()).hexdigest() != cadpy.get("sha256"):
        raise DeploymentAuthorityError("audited cadpy runtime identity conflicts")
    if verify_external:
        bwrap_path = _physical_absolute(Path(str(bwrap["path"])))
        browser_path = _physical_absolute(Path(str(chromium["executable_path"])))
        try:
            browser_path.relative_to(
                Path(str(chromium["host_cache_path"])).resolve(strict=True)
            )
        except (OSError, ValueError) as exc:
            raise DeploymentAuthorityError("Chromium path escapes its cache") from exc
        if hashlib.sha256(bwrap_path.read_bytes()).hexdigest() != bwrap.get("sha256"):
            raise DeploymentAuthorityError("trusted bwrap digest conflicts")
        try:
            measured_version = subprocess.run(
                [os.fspath(bwrap_path), "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentAuthorityError("trusted bwrap version probe failed") from exc
        if " ".join(measured_version.stdout.split()) != bwrap.get("version"):
            raise DeploymentAuthorityError("trusted bwrap version conflicts")
        if hashlib.sha256(browser_path.read_bytes()).hexdigest() != chromium.get("sha256"):
            raise DeploymentAuthorityError("Chromium digest conflicts")
    return identity


def _regular_files(root: Path, contract_paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for declared in contract_paths:
        relative = _safe_relative(declared)
        path = _physical_path(root, relative)
        mode = path.lstat().st_mode
        candidates = [path]
        if stat.S_ISDIR(mode):
            candidates = sorted(path.rglob("*"))
        for candidate in candidates:
            relative_text = candidate.relative_to(root).as_posix()
            candidate = _physical_path(root, candidate.relative_to(root))
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
    runtime_identity: dict[str, Any] | None = None,
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
    receipt = {
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
    if runtime_identity is not None:
        validate_runtime_identity(source_root, runtime_identity, verify_external=False)
        receipt["runtime_identity"] = runtime_identity
    return receipt


def verify_receipt(root: str | Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Recompute a deployed receipt from actual files and require exact equality."""

    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        raise DeploymentAuthorityError("deployed source receipt schema is invalid")
    expected = build_receipt(
        root,
        source_head=str(receipt.get("source_head", "")),
        contract_paths=receipt.get("contract_paths", ()),
        runtime_identity=receipt.get("runtime_identity"),
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
    runtime_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_root = Path(root).resolve()
    receipt = build_receipt(
        source_root,
        source_head=source_head,
        runtime_identity=runtime_identity,
    )
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
    parser.add_argument("--chromium-revision")
    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            if not args.source_head:
                raise DeploymentAuthorityError("write requires --source-head")
            if not args.chromium_revision:
                raise DeploymentAuthorityError("write requires --chromium-revision")
            runtime_identity = probe_runtime_identity(
                args.root,
                chromium_revision=args.chromium_revision,
            )
            receipt = write_receipt(
                args.root,
                source_head=args.source_head,
                runtime_identity=runtime_identity,
            )
        else:
            receipt = json.loads((args.root / RECEIPT_PATH).read_text(encoding="utf-8"))
            verify_receipt(args.root, receipt)
            validate_runtime_identity(
                args.root,
                receipt.get("runtime_identity"),
                verify_external=True,
            )
    except (OSError, json.JSONDecodeError, DeploymentAuthorityError) as exc:
        parser.exit(1, f"deployment authority failed: {exc}\n")
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
