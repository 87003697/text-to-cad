"""Produce and audit the project-owned sealed Agent runtime closure."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
from typing import Any, Mapping
import zipfile

from scripts.pilot.agent_runtime import (
    canonical_json_bytes,
    canonical_json_digest,
    parse_canonical_json,
)


class ProjectClosureError(ValueError):
    """A project artifact is incomplete, mutable, or outside the Cup surface."""


_MESHSCOPE_WHEEL = re.compile(
    r"meshscope-0\.1\.0-cp312-cp312-(?:manylinux[^-]*_x86_64|linux_x86_64)\.whl\Z"
)
_FORBIDDEN_ARCHIVE_MARKERS = (
    "playwright",
    "chromium",
    "chrome-linux",
    "browser_cache",
    "node_modules",
    "runtime/render.html",
    "runtime/residual-render.js",
)
_IMPLICIT_FILES = (
    "scripts/canonical-build.mjs",
    "src/common/parameters.js",
    "src/lib/implicitCad/animation.js",
    "src/lib/implicitCad/canonicalBuild.js",
    "src/lib/implicitCad/canonicalBuildWorker.mjs",
    "src/lib/implicitCad/exportModel.js",
    "src/lib/implicitCad/exporters.js",
    "src/lib/implicitCad/mesh.js",
    "src/lib/implicitCad/model.js",
    "src/lib/implicitCad/schema.js",
    "src/lib/implicitCad/sdfEvaluator.js",
)
_MESHSHOT_SOURCE_FILES = (
    "pyproject.toml",
    "src/meshshot/__init__.py",
    "src/meshshot/broker_client.py",
    "src/meshshot/browser_contract.json",
    "src/meshshot/profile.py",
    "src/meshshot/profiles/cadena_residual_eight_view_v1.json",
)
_MESHSHOT_PACKAGE_FILES = tuple(
    path.removeprefix("src/") for path in _MESHSHOT_SOURCE_FILES if path != "pyproject.toml"
)
_MESHSHOT_DIST_INFO = "meshshot_agent_runtime-0.1.0.dist-info"
_MESHSHOT_WHEEL_FILES = _MESHSHOT_PACKAGE_FILES + (
    f"{_MESHSHOT_DIST_INFO}/METADATA",
    f"{_MESHSHOT_DIST_INFO}/RECORD",
    f"{_MESHSHOT_DIST_INFO}/WHEEL",
    f"{_MESHSHOT_DIST_INFO}/top_level.txt",
)
_MESHSCOPE_SOURCE_FILES = (
    "MANIFEST.in",
    "pyproject.toml",
    "setup.py",
    "src/meshscope/__init__.py",
    "src/meshscope/inspect.py",
    "src/meshscope/io.py",
    "src/meshscope/viewer_glb.py",
    "src/meshscope/voxblame/CONTRACT.md",
    "src/meshscope/voxblame/README.md",
    "src/meshscope/voxblame/__init__.py",
    "src/meshscope/voxblame/_native.cpp",
    "src/meshscope/voxblame/canonical_artifacts.py",
    "src/meshscope/voxblame/codec.py",
    "src/meshscope/voxblame/contracts.py",
    "src/meshscope/voxblame/errors.py",
    "src/meshscope/voxblame/exterior.py",
    "src/meshscope/voxblame/frame.py",
    "src/meshscope/voxblame/measurement.py",
    "src/meshscope/voxblame/prepare_reference.py",
    "src/meshscope/voxblame/preview.py",
    "src/meshscope/voxblame/region_diff.py",
    "src/meshscope/voxblame/targets.py",
    "src/meshscope/voxblame/tree.py",
    "src/meshscope/voxblame/verification.py",
    "src/meshscope/voxblame/voxelize.py",
)


def _validate_archive_names(names: list[str], label: str) -> None:
    for name in names:
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or ".." in path.parts
        ):
            raise ProjectClosureError(f"{label} contains an unsafe archive path")


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _tree_records(
    root: Path,
    expected_files: tuple[str, ...],
    *,
    allow_python_cache: bool = False,
) -> list[dict[str, Any]]:
    expected = set(expected_files)
    expected_directories = {
        parent.as_posix()
        for relative in expected_files
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    observed: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ProjectClosureError(f"source tree contains a symlink: {relative}")
        # Interpreter bytecode is the sole explicit exclusion. Build and package
        # metadata directories are never silently omitted from source authority.
        cache_entry = "__pycache__" in path.parts and (
            path.is_dir() or path.suffix in {".pyc", ".pyo"}
        )
        if allow_python_cache and cache_entry:
            continue
        if path.is_dir():
            if relative not in expected_directories:
                raise ProjectClosureError(
                    f"source tree contains an unexpected directory: {relative}"
                )
            continue
        if not path.is_file() or relative not in expected:
            raise ProjectClosureError(
                f"source tree contains an unexpected entry: {relative}"
            )
        observed.add(relative)
    if observed != expected:
        missing = ", ".join(sorted(expected - observed))
        raise ProjectClosureError(f"source tree is missing expected files: {missing}")
    return [_file_record(root, root / relative) for relative in sorted(expected)]


def _record_digest(records: list[dict[str, Any]]) -> str:
    return canonical_json_digest(records)


def _is_digest(value: Any, *, prefixed: bool) -> bool:
    if not isinstance(value, str):
        return False
    raw = value.removeprefix("sha256:") if prefixed else value
    if prefixed and not value.startswith("sha256:"):
        return False
    repeated_placeholder = any(
        raw == raw[:width] * (64 // width) for width in (1, 2, 4, 8)
    )
    return bool(re.fullmatch(r"[0-9a-f]{64}", raw)) and not repeated_placeholder


def _validate_file_records(
    records: Any, expected_files: tuple[str, ...]
) -> list[dict[str, Any]]:
    if not isinstance(records, (list, tuple)) or len(records) != len(expected_files):
        raise ProjectClosureError("file manifest member set is not exact")
    normalized: list[dict[str, Any]] = []
    for record, expected_path in zip(records, sorted(expected_files), strict=True):
        if not isinstance(record, Mapping) or set(record) != {"path", "sha256", "bytes"}:
            raise ProjectClosureError("file manifest record shape is not exact")
        if record.get("path") != expected_path or not _is_digest(
            record.get("sha256"), prefixed=False
        ):
            raise ProjectClosureError("file manifest path or digest is invalid")
        size = record.get("bytes")
        if type(size) is not int or size < 0:
            raise ProjectClosureError("file manifest size is invalid")
        normalized.append(dict(record))
    return normalized


def _validate_meshshot_source_record(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected_keys = {
        "distribution",
        "version",
        "importName",
        "publicCallable",
        "runtimeDependencies",
        "sourceTreeDigest",
        "fileManifestDigest",
        "files",
    }
    if set(source) != expected_keys or (
        source.get("distribution"),
        source.get("version"),
        source.get("importName"),
        source.get("publicCallable"),
    ) != (
        "meshshot-agent-runtime",
        "0.1.0",
        "meshshot",
        "meshshot.render_residual_preview",
    ):
        raise ProjectClosureError("meshshot source identity is invalid")
    dependencies = source.get("runtimeDependencies")
    if not isinstance(dependencies, (list, tuple)) or list(dependencies) != [
        "Pillow==12.2.0"
    ]:
        raise ProjectClosureError("meshshot source dependencies are invalid")
    records = _validate_file_records(source.get("files"), _MESHSHOT_SOURCE_FILES)
    digest = _record_digest(records)
    if source.get("sourceTreeDigest") != digest or source.get("fileManifestDigest") != digest:
        raise ProjectClosureError("meshshot source digest is invalid")
    return records


def generate_meshshot_distribution(repo_root: Path, output_root: Path) -> dict[str, Any]:
    """Generate the formal distribution from the authoritative Broker client."""

    source = repo_root / "packages/meshshot/src/meshshot"
    target = output_root / "meshshot-agent-runtime"
    if target.exists():
        raise ProjectClosureError("meshshot output must not already exist")
    package = target / "src/meshshot"
    (package / "profiles").mkdir(parents=True)
    pyproject = """[build-system]
requires = ["setuptools==82.0.1"]
build-backend = "setuptools.build_meta"

[project]
name = "meshshot-agent-runtime"
version = "0.1.0"
description = "Broker-only residual preview client for the sealed Agent runtime."
requires-python = ">=3.12,<3.13"
dependencies = ["Pillow==12.2.0"]

[tool.setuptools.packages.find]
where = ["src"]
include = ["meshshot"]

[tool.setuptools.package-data]
meshshot = ["browser_contract.json", "profiles/*.json"]
"""
    (target / "pyproject.toml").write_text(pyproject, encoding="utf-8", newline="\n")
    public_init = '''"""Public API for the sealed Broker-only residual client."""

from meshshot.broker_client import (
    MeshGeometry,
    MeshshotError,
    RenderedPreview,
    render_residual_preview,
)
from meshshot.profile import LoadedProfile, load_profile

__all__ = [
    "LoadedProfile",
    "MeshGeometry",
    "MeshshotError",
    "RenderedPreview",
    "load_profile",
    "render_residual_preview",
]
'''
    (package / "__init__.py").write_text(public_init, encoding="utf-8", newline="\n")
    for relative in (
        "broker_client.py",
        "profile.py",
        "browser_contract.json",
        "profiles/cadena_residual_eight_view_v1.json",
    ):
        source_path = source / relative
        if not source_path.is_file() or source_path.is_symlink():
            raise ProjectClosureError(f"meshshot source is not a regular file: {relative}")
        destination = package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
    records = _tree_records(target, _MESHSHOT_SOURCE_FILES)
    return {
        "distribution": "meshshot-agent-runtime",
        "version": "0.1.0",
        "importName": "meshshot",
        "publicCallable": "meshshot.render_residual_preview",
        "runtimeDependencies": ["Pillow==12.2.0"],
        "sourceTreeDigest": _record_digest(records),
        "fileManifestDigest": _record_digest(records),
        "files": records,
    }


def audit_meshshot_wheel(wheel: Path, source: Mapping[str, Any]) -> dict[str, Any]:
    """Prove the built wheel contains only the Broker client/profile surface."""

    if not re.fullmatch(r"meshshot_agent_runtime-0\.1\.0-py3-none-any\.whl", wheel.name):
        raise ProjectClosureError("meshshot wheel filename is not the exact pure wheel")
    source_records = {
        record["path"]: record for record in _validate_meshshot_source_record(source)
    }
    payload = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ProjectClosureError("meshshot wheel contains duplicate members")
        _validate_archive_names(names, "meshshot wheel")
        if set(names) != set(_MESHSHOT_WHEEL_FILES):
            raise ProjectClosureError("meshshot wheel member set is not exact")
        wheel_payloads: dict[str, bytes] = {}
        for info in infos:
            mode = info.external_attr >> 16
            if info.is_dir() or not stat.S_ISREG(mode) or mode & 0o111:
                raise ProjectClosureError("meshshot wheel member mode is invalid")
            if stat.S_IMODE(mode) not in {0o644, 0o664}:
                raise ProjectClosureError("meshshot wheel member permissions are invalid")
            wheel_payloads[info.filename] = archive.read(info)
        try:
            metadata = wheel_payloads[f"{_MESHSHOT_DIST_INFO}/METADATA"].decode(
                "utf-8"
            )
        except UnicodeDecodeError as exc:
            raise ProjectClosureError("meshshot wheel metadata is not UTF-8") from exc
    lowered_names = "\n".join(names).lower()
    lowered_bytes = b"\n".join(wheel_payloads[name].lower() for name in names)
    for marker in _FORBIDDEN_ARCHIVE_MARKERS:
        if marker in lowered_names or marker.encode("ascii") in lowered_bytes:
            raise ProjectClosureError(f"meshshot wheel contains forbidden marker: {marker}")
    requirements = sorted(
        line.removeprefix("Requires-Dist: ").strip()
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist: ")
    )
    if requirements != ["Pillow==12.2.0"]:
        raise ProjectClosureError("meshshot wheel dependency metadata is not exact")
    metadata_fields = {
        key: value
        for line in metadata.splitlines()
        if ": " in line
        for key, value in (line.split(": ", 1),)
    }
    if (
        metadata_fields.get("Name") != "meshshot-agent-runtime"
        or metadata_fields.get("Version") != "0.1.0"
    ):
        raise ProjectClosureError("meshshot wheel metadata identity is invalid")
    record_name = f"{_MESHSHOT_DIST_INFO}/RECORD"
    try:
        rows = list(csv.reader(StringIO(wheel_payloads[record_name].decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ProjectClosureError("meshshot wheel RECORD is invalid") from exc
    if any(len(row) != 3 for row in rows) or len(rows) != len(names):
        raise ProjectClosureError("meshshot wheel RECORD shape is invalid")
    record_paths = [row[0] for row in rows]
    if len(record_paths) != len(set(record_paths)) or set(record_paths) != set(names):
        raise ProjectClosureError("meshshot wheel RECORD member set is not exact")
    record_facts = []
    for name, digest_field, size_field in rows:
        if name == record_name:
            if digest_field or size_field:
                raise ProjectClosureError("meshshot wheel RECORD self-entry is invalid")
            continue
        try:
            if not digest_field.startswith("sha256=") or not size_field.isascii():
                raise ValueError
            encoded = digest_field.removeprefix("sha256=")
            if re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded) is None:
                raise ValueError
            decoded = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
            size = int(size_field)
        except (ValueError, TypeError) as exc:
            raise ProjectClosureError("meshshot wheel RECORD value is invalid") from exc
        member = wheel_payloads[name]
        if decoded != hashlib.sha256(member).digest() or size_field != str(len(member)):
            raise ProjectClosureError("meshshot wheel RECORD integrity check failed")
        record_facts.append(
            {
                "path": name,
                "sha256": hashlib.sha256(member).hexdigest(),
                "bytes": size,
            }
        )
    for name in _MESHSHOT_PACKAGE_FILES:
        source_record = source_records[f"src/{name}"]
        member = wheel_payloads[name]
        if (
            source_record["sha256"] != hashlib.sha256(member).hexdigest()
            or source_record["bytes"] != len(member)
        ):
            raise ProjectClosureError("meshshot wheel does not match its source files")
    return {
        "distribution": "meshshot-agent-runtime",
        "version": "0.1.0",
        "wheelPath": wheel.name,
        "wheelSha256": hashlib.sha256(payload).hexdigest(),
        "wheelBytes": len(payload),
        "sourceTreeDigest": source["sourceTreeDigest"],
        "fileManifestDigest": source["fileManifestDigest"],
        "wheelRecordDigest": canonical_json_digest(record_facts),
        "browserInventoryEmpty": True,
        "browserDenial": {
            "playwrightPackageOrImportAbsent": True,
            "browserExecutableAbsent": True,
            "browserCachePathAbsent": True,
            "localBrowserRuntimeAbsent": True,
        },
        "files": sorted(names),
    }


def generate_implicit_runtime(repo_root: Path, output_root: Path) -> dict[str, Any]:
    """Vendor the exact dependency-free canonical-build module graph."""

    source = repo_root / "packages/implicitjs"
    target = output_root / "implicit-runtime"
    if target.exists():
        raise ProjectClosureError("implicit runtime output must not already exist")
    for relative in _IMPLICIT_FILES:
        source_path = source / relative
        if not source_path.is_file() or source_path.is_symlink():
            raise ProjectClosureError(f"implicit source is not a regular file: {relative}")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
    records = _tree_records(target, _IMPLICIT_FILES)
    if tuple(record["path"] for record in records) != tuple(sorted(_IMPLICIT_FILES)):
        raise ProjectClosureError("implicit canonical source graph is not exact")
    admitted = set(_IMPLICIT_FILES)
    for relative in _IMPLICIT_FILES:
        source_text = (target / relative).read_text(encoding="utf-8")
        for specifier in re.findall(
            r"(?:\bfrom\s+|\bimport\s*\()\s*['\"]([^'\"]+)", source_text
        ):
            if specifier.startswith("node:"):
                continue
            if not specifier.startswith("."):
                raise ProjectClosureError(
                    f"implicit canonical source has an npm import: {specifier}"
                )
            resolved = (PurePosixPath(relative).parent / specifier).as_posix()
            normalized_parts: list[str] = []
            for part in PurePosixPath(resolved).parts:
                if part == "..":
                    if not normalized_parts:
                        raise ProjectClosureError("implicit import escapes the runtime root")
                    normalized_parts.pop()
                elif part != ".":
                    normalized_parts.append(part)
            normalized = "/".join(normalized_parts)
            if normalized not in admitted:
                raise ProjectClosureError(
                    f"implicit canonical import is outside the closed graph: {normalized}"
                )
    record = {
        "schema": "text-to-cad.implicit-runtime-files/1",
        "entrypoint": "scripts/canonical-build.mjs",
        "bundlePath": "implicit-runtime",
        "runtimeDependencies": [],
        "fileCount": len(records),
        "filesDigest": _record_digest(records),
        "bundleDigest": _record_digest(records),
        "fileManifestDigest": _record_digest(records),
        "files": records,
    }
    (target / "implicit-runtime-manifest.json").write_bytes(
        canonical_json_bytes(record) + b"\n"
    )
    return record


def audit_meshscope_wheel(
    wheel: Path,
    source: Mapping[str, Any] | None = None,
    *,
    readelf: str = "readelf",
) -> dict[str, Any]:
    """Audit one CPython 3.12 linux/amd64 native meshscope wheel and ELF."""

    if _MESHSCOPE_WHEEL.fullmatch(wheel.name) is None:
        raise ProjectClosureError("meshscope wheel must be cp312-cp312 linux_x86_64")
    if source is None:
        raise ProjectClosureError("meshscope wheel audit requires its source record")
    _validate_meshscope_source_record(source)
    payload = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(archive.namelist())
        _validate_archive_names(names, "meshscope wheel")
        native = [
            name
            for name in names
            if re.fullmatch(
                r"meshscope/voxblame/_native\.cpython-312-x86_64-linux-gnu\.so",
                name,
            )
        ]
        if len(native) != 1:
            raise ProjectClosureError(
                "meshscope wheel must contain exactly one CPython 3.12 native backend"
            )
        native_bytes = archive.read(native[0])
    if len(native_bytes) < 20 or native_bytes[:6] != b"\x7fELF\x02\x01":
        raise ProjectClosureError("meshscope native backend is not ELF64 little-endian")
    machine = struct.unpack_from("<H", native_bytes, 18)[0]
    if machine != 62:
        raise ProjectClosureError("meshscope native backend is not x86_64")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="meshscope-elf-") as directory:
        elf = Path(directory) / "_native.so"
        elf.write_bytes(native_bytes)
        reports: dict[str, str] = {}
        for name, args in (
            ("header", ("-h",)),
            ("programHeaders", ("-lW",)),
            ("dynamic", ("-dW",)),
            ("versionInfo", ("-VW",)),
            ("symbols", ("-sW",)),
        ):
            completed = subprocess.run(
                [readelf, *args, str(elf)], check=True, capture_output=True, text=True
            )
            reports[name] = completed.stdout
    dynamic = reports["dynamic"]
    needed = sorted(set(re.findall(r"Shared library: \[([^\]]+)\]", dynamic)))
    allowed_needed = {"libc.so.6", "libgcc_s.so.1", "libstdc++.so.6"}
    if not set(needed).issubset(allowed_needed):
        raise ProjectClosureError(
            "meshscope native backend has an unexpected DT_NEEDED closure: "
            + ", ".join(sorted(set(needed) - allowed_needed))
        )
    if any(token in dynamic for token in ("(RPATH)", "(RUNPATH)")):
        raise ProjectClosureError("meshscope native backend must not contain RPATH/RUNPATH")
    if "PyInit__native" not in reports["symbols"]:
        raise ProjectClosureError("meshscope native backend lacks PyInit__native")
    return {
        "distribution": "meshscope",
        "version": "0.1.0",
        "wheelPath": wheel.name,
        "wheelSha256": hashlib.sha256(payload).hexdigest(),
        "wheelBytes": len(payload),
        "sourceTreeDigest": source["sourceTreeDigest"],
        "fileManifestDigest": source["fileManifestDigest"],
        "nativePath": native[0],
        "needed": needed,
        "rpathAbsent": True,
        "runpathAbsent": True,
        "nativeAuditDigest": canonical_json_digest(
            {"needed": needed, "reports": reports}
        ),
        "files": names,
    }


def meshscope_source_record(repo_root: Path) -> dict[str, Any]:
    """Bind the exact project source used to build the meshscope wheel."""

    root = repo_root / "packages/meshscope"
    records = _tree_records(
        root, _MESHSCOPE_SOURCE_FILES, allow_python_cache=True
    )
    if not records or not any(
        record["path"] == "src/meshscope/voxblame/_native.cpp" for record in records
    ):
        raise ProjectClosureError("meshscope source closure lacks the native backend")
    digest = _record_digest(records)
    return {
        "distribution": "meshscope",
        "version": "0.1.0",
        "sourceTreeDigest": digest,
        "fileManifestDigest": digest,
        "files": records,
    }


def _validate_meshscope_source_record(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    if set(source) != {
        "distribution",
        "version",
        "sourceTreeDigest",
        "fileManifestDigest",
        "files",
    } or (source.get("distribution"), source.get("version")) != ("meshscope", "0.1.0"):
        raise ProjectClosureError("meshscope source identity is invalid")
    records = _validate_file_records(source.get("files"), _MESHSCOPE_SOURCE_FILES)
    digest = _record_digest(records)
    if source.get("sourceTreeDigest") != digest or source.get("fileManifestDigest") != digest:
        raise ProjectClosureError("meshscope source digest is invalid")
    return records


def assemble_python_artifact(
    source: Mapping[str, Any], wheel: Mapping[str, Any]
) -> dict[str, Any]:
    """Join source and wheel identities without accepting field substitution."""

    if (
        source.get("distribution") != wheel.get("distribution")
        or source.get("version") != wheel.get("version")
    ):
        raise ProjectClosureError("project wheel does not match its source distribution")
    if source["distribution"] == "meshshot-agent-runtime":
        _validate_meshshot_source_record(source)
    elif source["distribution"] == "meshscope":
        _validate_meshscope_source_record(source)
    else:
        raise ProjectClosureError("unsupported project distribution")
    if not {
        "wheelPath",
        "wheelSha256",
        "wheelBytes",
        "sourceTreeDigest",
        "fileManifestDigest",
    }.issubset(wheel):
        raise ProjectClosureError("project source or wheel identity is incomplete")
    if (
        wheel["sourceTreeDigest"] != source["sourceTreeDigest"]
        or wheel["fileManifestDigest"] != source["fileManifestDigest"]
    ):
        raise ProjectClosureError("project wheel source binding does not match")
    result = {
        "distribution": source["distribution"],
        "version": source["version"],
        "wheelPath": wheel["wheelPath"],
        "wheelSha256": wheel["wheelSha256"],
        "wheelBytes": wheel["wheelBytes"],
        "sourceTreeDigest": source["sourceTreeDigest"],
        "fileManifestDigest": source["fileManifestDigest"],
    }
    if source["distribution"] == "meshshot-agent-runtime":
        denial = wheel.get("browserDenial")
        if wheel.get("browserInventoryEmpty") is not True or denial != {
            "playwrightPackageOrImportAbsent": True,
            "browserExecutableAbsent": True,
            "browserCachePathAbsent": True,
            "localBrowserRuntimeAbsent": True,
        }:
            raise ProjectClosureError("meshshot browser-deny evidence is absent")
        result["browserInventoryEmpty"] = True
        result["browserDenial"] = denial
        result["importName"] = source["importName"]
        result["publicCallable"] = source["publicCallable"]
    elif source["distribution"] == "meshscope":
        if not {"nativeAuditDigest", "nativeConformanceDigest"}.issubset(wheel):
            raise ProjectClosureError("meshscope native audit/conformance identity is absent")
        result["nativeAuditDigest"] = wheel["nativeAuditDigest"]
        result["nativeConformanceDigest"] = wheel["nativeConformanceDigest"]
        result["needed"] = wheel["needed"]
    return result


def build_meshscope_wheel(
    repo_root: Path,
    output_root: Path,
    *,
    python: str = "python3.12",
    source_date_epoch: int = 1_755_302_400,
) -> Path:
    """Build the native wheel inside an already-admitted linux/amd64 builder.

    The function never resolves or installs dependencies.  Its caller owns the
    builder/toolchain admission and must provide Python 3.12, setuptools, wheel,
    a C++17 compiler, and binutils before invoking this seam.
    """

    probe = subprocess.run(
        [
            python,
            "-c",
            "import platform,sys; "
            "print(platform.system(),platform.machine(),sys.version_info[:2])",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if probe != "Linux x86_64 (3, 12)":
        raise ProjectClosureError(
            f"meshscope builder must be Linux x86_64 CPython 3.12, observed {probe!r}"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": str(source_date_epoch),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    subprocess.run(
        [
            python,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(output_root),
            str(repo_root / "packages/meshscope"),
        ],
        check=True,
        env=environment,
    )
    wheels = sorted(output_root.glob("meshscope-*.whl"))
    if len(wheels) != 1:
        raise ProjectClosureError("meshscope build must produce exactly one wheel")
    return wheels[0]


def verify_meshscope_native_install(
    wheel: Path, *, python: str = "python3.12"
) -> dict[str, Any]:
    """Install without resolution and exercise the native backend through its public seam."""

    import os
    import tempfile

    with tempfile.TemporaryDirectory(prefix="meshscope-native-install-") as directory:
        target = Path(directory) / "site-packages"
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--target",
                str(target),
                str(wheel),
            ],
            check=True,
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        probe = '''import json
import numpy as np
import PIL
import trimesh
import meshscope
from meshscope.voxblame import _native
from meshscope.voxblame.voxelize import build_lattice_tree
triangles = np.asarray([[[-.25,-.25,0.0],[.25,-.25,0.0],[0.0,.25,0.0]]], dtype=np.float64)
tree = build_lattice_tree(triangles, 4, backend="native")
print(json.dumps({
    "imports": ["PIL","meshscope","meshscope.voxblame._native","numpy","trimesh"],
    "nativeModule": _native.__file__,
    "leafCount": tree.leaf_count,
    "nonEmpty": tree.leaf_count > 0,
}, sort_keys=True, separators=(",", ":")))
'''
        completed = subprocess.run(
            [python, "-c", probe],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(target),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    value = json.loads(completed.stdout)
    if (
        value.get("imports")
        != ["PIL", "meshscope", "meshscope.voxblame._native", "numpy", "trimesh"]
        or value.get("nonEmpty") is not True
        or not isinstance(value.get("leafCount"), int)
        or value["leafCount"] <= 0
        or not str(value.get("nativeModule", "")).endswith(
            "_native.cpython-312-x86_64-linux-gnu.so"
        )
    ):
        raise ProjectClosureError("meshscope native import/backend conformance failed")
    return {
        "imports": value["imports"],
        "nativeModuleBasename": Path(value["nativeModule"]).name,
        "nativeBackendCallable": True,
        "leafCount": value["leafCount"],
    }


def build_project_manifest(
    *, meshshot: Mapping[str, Any], meshscope: Mapping[str, Any], implicit: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind the three project-owned artifacts without external dependency bytes."""

    manifest = {
        "schema": "text-to-cad.agent-runtime-project-closure/1",
        "platform": {"architecture": "amd64", "os": "linux", "pythonAbi": "cp312"},
        "pythonArtifacts": [dict(meshscope), dict(meshshot)],
        "implicitRuntime": dict(implicit),
    }
    validate_project_manifest(manifest)
    return manifest


def _validate_wheel_identity(
    artifact: Mapping[str, Any], *, distribution: str, expected_keys: set[str]
) -> None:
    if set(artifact) != expected_keys or (
        artifact.get("distribution"), artifact.get("version")
    ) != (distribution, "0.1.0"):
        raise ProjectClosureError(f"{distribution} project artifact shape is invalid")
    if not _is_digest(artifact.get("wheelSha256"), prefixed=False):
        raise ProjectClosureError(f"{distribution} wheel digest is invalid")
    if type(artifact.get("wheelBytes")) is not int or artifact["wheelBytes"] <= 0:
        raise ProjectClosureError(f"{distribution} wheel size is invalid")
    for field in (
        "sourceTreeDigest",
        "fileManifestDigest",
        *(('nativeAuditDigest', 'nativeConformanceDigest') if distribution == "meshscope" else ()),
    ):
        if not _is_digest(artifact.get(field), prefixed=True):
            raise ProjectClosureError(f"{distribution} {field} is invalid")
    if artifact["sourceTreeDigest"] != artifact["fileManifestDigest"]:
        raise ProjectClosureError(f"{distribution} source manifest binding is invalid")


def _validate_implicit_record(implicit: Mapping[str, Any]) -> None:
    expected_keys = {
        "schema",
        "entrypoint",
        "bundlePath",
        "runtimeDependencies",
        "fileCount",
        "filesDigest",
        "bundleDigest",
        "fileManifestDigest",
        "files",
    }
    if set(implicit) != expected_keys or (
        implicit.get("schema"),
        implicit.get("entrypoint"),
        implicit.get("bundlePath"),
        implicit.get("fileCount"),
    ) != (
        "text-to-cad.implicit-runtime-files/1",
        "scripts/canonical-build.mjs",
        "implicit-runtime",
        len(_IMPLICIT_FILES),
    ):
        raise ProjectClosureError("implicit project artifact shape is invalid")
    dependencies = implicit.get("runtimeDependencies")
    if not isinstance(dependencies, (list, tuple)) or dependencies:
        raise ProjectClosureError("implicit project dependencies are invalid")
    records = _validate_file_records(implicit.get("files"), _IMPLICIT_FILES)
    digest = _record_digest(records)
    if any(
        implicit.get(field) != digest
        for field in ("filesDigest", "bundleDigest", "fileManifestDigest")
    ):
        raise ProjectClosureError("implicit project artifact digest is invalid")


def validate_project_manifest(value: Mapping[str, Any]) -> None:
    """Reject any project closure that is not the exact Cup runtime surface."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema", "platform", "pythonArtifacts", "implicitRuntime"
    }:
        raise ProjectClosureError("project closure manifest shape is invalid")
    if (
        value.get("schema") != "text-to-cad.agent-runtime-project-closure/1"
        or value.get("platform")
        != {"architecture": "amd64", "os": "linux", "pythonAbi": "cp312"}
    ):
        raise ProjectClosureError("project closure platform identity is invalid")
    artifacts = value.get("pythonArtifacts")
    if not isinstance(artifacts, (list, tuple)) or len(artifacts) != 2:
        raise ProjectClosureError("project Python artifact set is not exact")
    meshscope, meshshot = artifacts
    if not isinstance(meshscope, Mapping) or not isinstance(meshshot, Mapping):
        raise ProjectClosureError("project Python artifact is not an object")
    _validate_wheel_identity(
        meshscope,
        distribution="meshscope",
        expected_keys={
            "distribution", "version", "wheelPath", "wheelSha256", "wheelBytes",
            "sourceTreeDigest", "fileManifestDigest", "nativeAuditDigest",
            "nativeConformanceDigest", "needed",
        },
    )
    meshscope_filename = PurePosixPath(str(meshscope.get("wheelPath"))).name
    if (
        _MESHSCOPE_WHEEL.fullmatch(meshscope_filename) is None
        or meshscope.get("wheelPath") != f"wheels/{meshscope_filename}"
    ):
        raise ProjectClosureError("meshscope wheel path is invalid")
    needed = meshscope.get("needed")
    if (
        not isinstance(needed, (list, tuple))
        or any(type(item) is not str for item in needed)
        or list(needed) != sorted(set(needed))
        or not set(needed).issubset(
            {"libc.so.6", "libgcc_s.so.1", "libstdc++.so.6"}
        )
    ):
        raise ProjectClosureError("meshscope dependency closure is invalid")
    _validate_wheel_identity(
        meshshot,
        distribution="meshshot-agent-runtime",
        expected_keys={
            "distribution", "version", "wheelPath", "wheelSha256", "wheelBytes",
            "sourceTreeDigest", "fileManifestDigest", "browserInventoryEmpty",
            "browserDenial", "importName", "publicCallable",
        },
    )
    if meshshot.get("wheelPath") != "wheels/meshshot_agent_runtime-0.1.0-py3-none-any.whl" or (
        meshshot.get("importName"), meshshot.get("publicCallable")
    ) != ("meshshot", "meshshot.render_residual_preview"):
        raise ProjectClosureError("meshshot public wheel identity is invalid")
    if meshshot.get("browserInventoryEmpty") is not True or meshshot.get("browserDenial") != {
        "playwrightPackageOrImportAbsent": True,
        "browserExecutableAbsent": True,
        "browserCachePathAbsent": True,
        "localBrowserRuntimeAbsent": True,
    }:
        raise ProjectClosureError("meshshot browser denial is invalid")
    implicit = value.get("implicitRuntime")
    if not isinstance(implicit, Mapping):
        raise ProjectClosureError("implicit project artifact is not an object")
    _validate_implicit_record(implicit)


def _write_record(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(dict(value)) + b"\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Produce browser-free project artifacts for the sealed Agent runtime."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--repo-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    meshshot_audit = subparsers.add_parser("audit-meshshot")
    meshshot_audit.add_argument("--wheel", type=Path, required=True)
    meshshot_audit.add_argument("--source-record", type=Path, required=True)
    meshshot_audit.add_argument("--record", type=Path, required=True)
    meshscope_build = subparsers.add_parser("build-meshscope")
    meshscope_build.add_argument("--repo-root", type=Path, required=True)
    meshscope_build.add_argument("--output", type=Path, required=True)
    meshscope_build.add_argument("--python", default="python3.12")
    meshscope_audit = subparsers.add_parser("audit-meshscope")
    meshscope_audit.add_argument("--wheel", type=Path, required=True)
    meshscope_audit.add_argument("--source-record", type=Path, required=True)
    meshscope_audit.add_argument("--record", type=Path, required=True)
    meshscope_audit.add_argument("--readelf", default="readelf")
    meshscope_verify = subparsers.add_parser("verify-meshscope")
    meshscope_verify.add_argument("--wheel", type=Path, required=True)
    meshscope_verify.add_argument("--record", type=Path, required=True)
    meshscope_verify.add_argument("--python", default="python3.12")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        meshshot = generate_meshshot_distribution(args.repo_root.resolve(), args.output)
        meshscope = meshscope_source_record(args.repo_root.resolve())
        implicit = generate_implicit_runtime(args.repo_root.resolve(), args.output)
        _write_record(args.output / "meshshot-source-manifest.json", meshshot)
        _write_record(args.output / "meshscope-source-manifest.json", meshscope)
        _write_record(args.output / "implicit-runtime-record.json", implicit)
    elif args.command == "audit-meshshot":
        source = parse_canonical_json(args.source_record.read_bytes())
        if not isinstance(source, Mapping):
            raise ProjectClosureError("meshshot source record must be an object")
        _write_record(args.record, audit_meshshot_wheel(args.wheel, source))
    elif args.command == "build-meshscope":
        wheel = build_meshscope_wheel(
            args.repo_root.resolve(), args.output, python=args.python
        )
        print(wheel)
    elif args.command == "audit-meshscope":
        source = parse_canonical_json(args.source_record.read_bytes())
        if not isinstance(source, Mapping):
            raise ProjectClosureError("meshscope source record must be an object")
        _write_record(
            args.record,
            audit_meshscope_wheel(args.wheel, source, readelf=args.readelf),
        )
    elif args.command == "verify-meshscope":
        _write_record(
            args.record,
            verify_meshscope_native_install(args.wheel, python=args.python),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
