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
_MESHSCOPE_PACKAGE_FILES = tuple(
    path.removeprefix("src/")
    for path in _MESHSCOPE_SOURCE_FILES
    if path.startswith("src/meshscope/") and path.endswith(".py")
)
_MESHSCOPE_NATIVE_FILE = (
    "meshscope/voxblame/_native.cpython-312-x86_64-linux-gnu.so"
)
_MESHSCOPE_DIST_INFO = "meshscope-0.1.0.dist-info"
_MESHSCOPE_WHEEL_FILES = _MESHSCOPE_PACKAGE_FILES + (
    _MESHSCOPE_NATIVE_FILE,
    f"{_MESHSCOPE_DIST_INFO}/METADATA",
    f"{_MESHSCOPE_DIST_INFO}/RECORD",
    f"{_MESHSCOPE_DIST_INFO}/WHEEL",
    f"{_MESHSCOPE_DIST_INFO}/top_level.txt",
)
_MESHSCOPE_DIRECT_NEEDED = ("libc.so.6", "libgcc_s.so.1", "libstdc++.so.6")
_MESHSCOPE_RESOLVED_SONAMES = (
    "ld-linux-x86-64.so.2",
    "libc.so.6",
    "libgcc_s.so.1",
    "libm.so.6",
    "libstdc++.so.6",
)
_MESHSCOPE_VERSION_REQUIREMENTS = (
    "CXXABI_1.3",
    "CXXABI_1.3.9",
    "GCC_3.0",
    "GLIBCXX_3.4",
    "GLIBCXX_3.4.21",
    "GLIBC_2.14",
    "GLIBC_2.2.5",
    "GLIBC_2.4",
)
_CUP_INPUT_SHA256 = "3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67"
_CUP_INPUT_BYTES = 190_047


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


def _closed_wheel_payloads(
    archive: zipfile.ZipFile,
    expected_files: tuple[str, ...],
    label: str,
    *,
    executable_files: tuple[str, ...] = (),
) -> dict[str, bytes]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ProjectClosureError(f"{label} contains duplicate members")
    _validate_archive_names(names, label)
    if set(names) != set(expected_files):
        raise ProjectClosureError(f"{label} member set is not exact")
    payloads: dict[str, bytes] = {}
    for info in infos:
        mode = info.external_attr >> 16
        if info.is_dir() or not stat.S_ISREG(mode):
            raise ProjectClosureError(f"{label} member mode is invalid")
        permissions = stat.S_IMODE(mode)
        expected_permissions = {0o755} if info.filename in executable_files else {0o644, 0o664}
        if permissions not in expected_permissions:
            raise ProjectClosureError(f"{label} member permissions are invalid")
        payloads[info.filename] = archive.read(info)
    return payloads


def _validate_wheel_record(
    payloads: Mapping[str, bytes], record_name: str, label: str
) -> list[dict[str, Any]]:
    try:
        rows = list(
            csv.reader(StringIO(payloads[record_name].decode("utf-8"), newline=""))
        )
    except (KeyError, UnicodeDecodeError, csv.Error) as exc:
        raise ProjectClosureError(f"{label} RECORD is invalid") from exc
    if any(len(row) != 3 for row in rows) or len(rows) != len(payloads):
        raise ProjectClosureError(f"{label} RECORD shape is invalid")
    record_paths = [row[0] for row in rows]
    if len(record_paths) != len(set(record_paths)) or set(record_paths) != set(payloads):
        raise ProjectClosureError(f"{label} RECORD member set is not exact")
    facts: list[dict[str, Any]] = []
    for name, digest_field, size_field in rows:
        if name == record_name:
            if digest_field or size_field:
                raise ProjectClosureError(f"{label} RECORD self-entry is invalid")
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
            raise ProjectClosureError(f"{label} RECORD value is invalid") from exc
        member = payloads[name]
        digest = hashlib.sha256(member).digest()
        if decoded != digest or size_field != str(len(member)):
            raise ProjectClosureError(f"{label} RECORD integrity check failed")
        facts.append(
            {"path": name, "sha256": digest.hex(), "bytes": size}
        )
    return facts


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
        wheel_payloads = _closed_wheel_payloads(
            archive, _MESHSHOT_WHEEL_FILES, "meshshot wheel"
        )
        try:
            metadata = wheel_payloads[f"{_MESHSHOT_DIST_INFO}/METADATA"].decode(
                "utf-8"
            )
        except UnicodeDecodeError as exc:
            raise ProjectClosureError("meshshot wheel metadata is not UTF-8") from exc
    lowered_names = "\n".join(wheel_payloads).lower()
    lowered_bytes = b"\n".join(
        wheel_payloads[name].lower() for name in wheel_payloads
    )
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
    record_facts = _validate_wheel_record(
        wheel_payloads, record_name, "meshshot wheel"
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
        "files": sorted(wheel_payloads),
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
    ldd: str = "ldd",
    auditwheel: str = "auditwheel",
) -> dict[str, Any]:
    """Audit one CPython 3.12 linux/amd64 native meshscope wheel and ELF."""

    if _MESHSCOPE_WHEEL.fullmatch(wheel.name) is None:
        raise ProjectClosureError("meshscope wheel must be cp312-cp312 linux_x86_64")
    if source is None:
        raise ProjectClosureError("meshscope wheel audit requires its source record")
    source_records = {
        record["path"]: record for record in _validate_meshscope_source_record(source)
    }
    payload = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        wheel_payloads = _closed_wheel_payloads(
            archive,
            _MESHSCOPE_WHEEL_FILES,
            "meshscope wheel",
            executable_files=(_MESHSCOPE_NATIVE_FILE,),
        )
    native_bytes = wheel_payloads[_MESHSCOPE_NATIVE_FILE]
    if len(native_bytes) < 20 or native_bytes[:6] != b"\x7fELF\x02\x01":
        raise ProjectClosureError("meshscope native backend is not ELF64 little-endian")
    machine = struct.unpack_from("<H", native_bytes, 18)[0]
    if machine != 62:
        raise ProjectClosureError("meshscope native backend is not x86_64")
    metadata_name = f"{_MESHSCOPE_DIST_INFO}/METADATA"
    wheel_metadata_name = f"{_MESHSCOPE_DIST_INFO}/WHEEL"
    try:
        metadata = wheel_payloads[metadata_name].decode("utf-8")
        wheel_metadata = wheel_payloads[wheel_metadata_name].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectClosureError("meshscope wheel metadata is not UTF-8") from exc
    metadata_lines = metadata.splitlines()
    if [line for line in metadata_lines if line.startswith("Name: ")] != [
        "Name: meshscope"
    ] or [line for line in metadata_lines if line.startswith("Version: ")] != [
        "Version: 0.1.0"
    ]:
        raise ProjectClosureError("meshscope wheel metadata identity is invalid")
    if [line for line in metadata_lines if line.startswith("Requires-Python: ")] != [
        "Requires-Python: <3.13,>=3.12"
    ]:
        raise ProjectClosureError("meshscope Python ABI metadata is invalid")
    requirements = sorted(
        line.removeprefix("Requires-Dist: ")
        for line in metadata_lines
        if line.startswith("Requires-Dist: ")
    )
    if requirements != [
        "Pillow==12.2.0",
        "numpy==2.4.6",
        "trimesh==4.12.2",
    ]:
        raise ProjectClosureError("meshscope runtime dependency metadata is invalid")
    if (
        "Root-Is-Purelib: false" not in wheel_metadata.splitlines()
        or "Tag: cp312-cp312-linux_x86_64" not in wheel_metadata.splitlines()
    ):
        raise ProjectClosureError("meshscope native wheel tag metadata is invalid")
    for name in _MESHSCOPE_PACKAGE_FILES:
        member = wheel_payloads[name]
        source_record = source_records[f"src/{name}"]
        if (
            hashlib.sha256(member).hexdigest() != source_record["sha256"]
            or len(member) != source_record["bytes"]
        ):
            raise ProjectClosureError("meshscope wheel does not match its source files")
    record_facts = _validate_wheel_record(
        wheel_payloads, f"{_MESHSCOPE_DIST_INFO}/RECORD", "meshscope wheel"
    )

    try:
        auditwheel_completed = subprocess.run(
            [auditwheel, "show", str(wheel)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProjectClosureError("meshscope auditwheel inspection failed") from exc
    auditwheel_report = auditwheel_completed.stdout
    platform_tags = re.findall(r'platform tag: "([^"]+)"', auditwheel_report)
    if platform_tags != ["manylinux_2_24_x86_64"]:
        raise ProjectClosureError("meshscope auditwheel platform result is invalid")

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
            try:
                completed = subprocess.run(
                    [readelf, *args, str(elf)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ProjectClosureError(
                    f"meshscope readelf {name} inspection failed"
                ) from exc
            reports[name] = completed.stdout
        try:
            ldd_completed = subprocess.run(
                [ldd, str(elf)], check=True, capture_output=True, text=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ProjectClosureError("meshscope ELF resolution failed") from exc
        resolved_libraries = _parse_resolved_libraries(ldd_completed.stdout)
    dynamic = reports["dynamic"]
    needed = sorted(set(re.findall(r"Shared library: \[([^\]]+)\]", dynamic)))
    if tuple(needed) != _MESHSCOPE_DIRECT_NEEDED:
        raise ProjectClosureError("meshscope native DT_NEEDED closure is not exact")
    if any(token in dynamic for token in ("(RPATH)", "(RUNPATH)")):
        raise ProjectClosureError("meshscope native backend must not contain RPATH/RUNPATH")
    if "PyInit__native" not in reports["symbols"]:
        raise ProjectClosureError("meshscope native backend lacks PyInit__native")
    versions = sorted(
        set(
            re.findall(
                r"(?:CXXABI|GCC|GLIBCXX|GLIBC)_[0-9.]+", reports["versionInfo"]
            )
        )
    )
    if tuple(versions) != _MESHSCOPE_VERSION_REQUIREMENTS:
        raise ProjectClosureError("meshscope native symbol version closure is not exact")
    audit = {
        "distribution": "meshscope",
        "version": "0.1.0",
        "wheelPath": wheel.name,
        "wheelSha256": hashlib.sha256(payload).hexdigest(),
        "wheelBytes": len(payload),
        "sourceTreeDigest": source["sourceTreeDigest"],
        "fileManifestDigest": source["fileManifestDigest"],
        "wheelRecordDigest": canonical_json_digest(record_facts),
        "nativePath": _MESHSCOPE_NATIVE_FILE,
        "nativeSha256": f"sha256:{hashlib.sha256(native_bytes).hexdigest()}",
        "needed": needed,
        "versionRequirements": versions,
        "resolvedLibraries": resolved_libraries,
        "rpathAbsent": True,
        "runpathAbsent": True,
        "auditwheelPlatformTag": platform_tags[0],
        "auditwheelReportDigest": canonical_json_digest(auditwheel_report),
        "elfReportsDigest": canonical_json_digest(reports),
        "files": sorted(wheel_payloads),
    }
    audit["nativeAuditDigest"] = canonical_json_digest(audit)
    return audit


def _parse_resolved_libraries(report: str) -> list[dict[str, Any]]:
    if "not found" in report:
        raise ProjectClosureError("meshscope ELF dependency is unresolved")
    resolved: dict[str, Path] = {}
    for raw_line in report.splitlines():
        parts = raw_line.strip().split()
        if not parts or parts[0] == "linux-vdso.so.1":
            continue
        if "=>" in parts:
            arrow = parts.index("=>")
            if arrow + 1 >= len(parts) or not parts[arrow + 1].startswith("/"):
                raise ProjectClosureError("meshscope ELF resolution row is invalid")
            soname = Path(parts[0]).name
            path = Path(parts[arrow + 1])
        elif parts[0].startswith("/"):
            path = Path(parts[0])
            soname = path.name
        else:
            raise ProjectClosureError("meshscope ELF resolution row is invalid")
        if soname in resolved:
            raise ProjectClosureError("meshscope ELF resolution contains duplicates")
        resolved[soname] = path
    if tuple(sorted(resolved)) != _MESHSCOPE_RESOLVED_SONAMES:
        raise ProjectClosureError("meshscope resolved SONAME closure is not exact")
    result = []
    for soname in sorted(resolved):
        path = resolved[soname]
        if not path.is_file():
            raise ProjectClosureError("meshscope resolved SONAME path is not a file")
        payload = path.read_bytes()
        result.append(
            {
                "soname": soname,
                "path": path.as_posix(),
                "sha256": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                "bytes": len(payload),
            }
        )
    return result


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
    builder_image_id: str | None = None,
    record_path: Path | None = None,
) -> Path:
    """Build the native wheel inside an already-admitted linux/amd64 builder.

    The function never resolves or installs dependencies.  Its caller owns the
    builder/toolchain admission and must provide Python 3.12, setuptools, wheel,
    a C++17 compiler, and binutils before invoking this seam.
    """

    if record_path is not None and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", builder_image_id or ""
    ):
        raise ProjectClosureError("meshscope build record requires an exact image ID")
    source = meshscope_source_record(repo_root)
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
    completed = subprocess.run(
        [
            python,
            "-m",
            "pip",
            "wheel",
            "-v",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(output_root),
            str(repo_root / "packages/meshscope"),
        ],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    wheels = sorted(output_root.glob("meshscope-*.whl"))
    if len(wheels) != 1:
        raise ProjectClosureError("meshscope build must produce exactly one wheel")
    if record_path is not None:
        log = completed.stdout + completed.stderr
        commands = [
            line.strip()
            for line in log.splitlines()
            if re.search(
                r"(?:^|\s)(?:[A-Za-z0-9_]+-)*g\+\+(?:\s|$)", line.strip()
            )
        ]
        if (
            len(commands) != 2
            or not any(" -c " in f" {command} " for command in commands)
            or not any(" -shared " in f" {command} " for command in commands)
            or not any("-std=c++17" in command for command in commands)
            or not any("-g0" in command for command in commands)
        ):
            raise ProjectClosureError("meshscope compiler/linker command capture is invalid")
        toolchain = _meshscope_toolchain_record(python)
        wheel_payload = wheels[0].read_bytes()
        build_record = {
            "schema": "text-to-cad.meshscope-build-candidate/1",
            "status": "development-candidate",
            "builderImageId": builder_image_id,
            "platform": {"architecture": "amd64", "os": "linux", "pythonAbi": "cp312"},
            "source": source,
            "sourceDateEpoch": source_date_epoch,
            "environment": environment,
            "toolchain": toolchain,
            "commands": commands,
            "buildLogSha256": f"sha256:{hashlib.sha256(log.encode('utf-8')).hexdigest()}",
            "wheel": {
                "path": wheels[0].name,
                "sha256": f"sha256:{hashlib.sha256(wheel_payload).hexdigest()}",
                "bytes": len(wheel_payload),
            },
        }
        _write_record(record_path, build_record)
    return wheels[0]


def _meshscope_toolchain_record(python: str) -> dict[str, Any]:
    identity_script = """import importlib.metadata as m
import sys
import sysconfig
print(sys.version.split()[0])
print(sys.implementation.cache_tag)
print(sysconfig.get_config_var('EXT_SUFFIX'))
print(sysconfig.get_config_var('CC'))
print(sysconfig.get_config_var('CXX'))
print(sysconfig.get_config_var('LDSHARED'))
print(m.version('pip'))
print(m.version('setuptools'))
print(m.version('wheel'))
"""
    python_lines = subprocess.run(
        [python, "-c", identity_script],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if len(python_lines) != 9:
        raise ProjectClosureError("meshscope Python toolchain identity is invalid")

    def first_line(command: list[str]) -> str:
        output = subprocess.run(
            command, check=True, capture_output=True, text=True
        ).stdout.splitlines()
        if not output:
            raise ProjectClosureError("meshscope toolchain command returned no identity")
        return output[0]

    record = {
        "python": python_lines[0],
        "pythonCacheTag": python_lines[1],
        "extensionSuffix": python_lines[2],
        "configuredCc": python_lines[3],
        "configuredCxx": python_lines[4],
        "configuredLdshared": python_lines[5],
        "pip": python_lines[6],
        "setuptools": python_lines[7],
        "wheel": python_lines[8],
        "compiler": first_line(["g++", "--version"]),
        "compilerVersion": first_line(["g++", "-dumpfullversion", "-dumpversion"]),
        "linker": first_line(["ld", "--version"]),
        "readelf": first_line(["readelf", "--version"]),
    }
    if (
        record["python"] != "3.12.3"
        or record["pythonCacheTag"] != "cpython-312"
        or record["extensionSuffix"]
        != ".cpython-312-x86_64-linux-gnu.so"
        or record["pip"] != "26.2.1"
        or record["setuptools"] != "82.0.1"
        or record["wheel"] != "0.48.0"
        or record["compilerVersion"] != "13.3.0"
        or not record["compiler"].startswith("g++ (Ubuntu 13.3.0")
        or not record["linker"].endswith(" 2.42")
        or not record["readelf"].endswith(" 2.42")
    ):
        raise ProjectClosureError("meshscope builder toolchain is not the fixed candidate")
    return record


def verify_meshscope_native_install(
    wheel: Path,
    fixture_input: Path,
    dependency_wheels: tuple[Path, ...],
    *,
    python: str = "python3.12",
) -> dict[str, Any]:
    """Offline-install candidate bytes and run real Cup native measurement."""

    import os
    import tempfile

    fixture_payload = fixture_input.read_bytes()
    if (
        len(fixture_payload) != _CUP_INPUT_BYTES
        or hashlib.sha256(fixture_payload).hexdigest() != _CUP_INPUT_SHA256
    ):
        raise ProjectClosureError("meshscope conformance requires the exact Cup input")

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
                *(str(path) for path in dependency_wheels),
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
        probe = '''from pathlib import Path
import sys
import numpy as np
import PIL
import trimesh
import meshscope
from meshscope.voxblame import _native
from meshscope.voxblame import measure_step, prepare_reference
fixture = Path(sys.argv[1])
root = Path(sys.argv[2])
prepared = prepare_reference(fixture, root / "input")
measured = measure_step(
    root / "input",
    root / "input/reference.ply",
    root / "voxblame",
    step=0,
    backend="native",
)
assert prepared.manifest["input_triangle_count"] == 3764
assert measured.summary["objective_facts"] == {
    "global_depth_8_zero": True,
    "out_of_frame_clear": True,
    "no_evidence_conflict": True,
}
print("meshscope-cup-native-ok")
'''
        cup_root = Path(directory) / "cup"
        completed = subprocess.run(
            [python, "-c", probe, str(fixture_input), str(cup_root)],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(target),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        if completed.stdout != "meshscope-cup-native-ok\n" or completed.stderr:
            raise ProjectClosureError("meshscope native import/backend conformance failed")
        input_manifest = json.loads((cup_root / "input/input.json").read_bytes())
        summary_path = cup_root / "voxblame/steps/000000/summary.json"
        summary_bytes = summary_path.read_bytes()
        summary = json.loads(summary_bytes)
        native_paths = sorted(
            target.glob(
                "meshscope/voxblame/_native.cpython-312-x86_64-linux-gnu.so"
            )
        )
        depth_eight = summary.get("errors_by_depth", [{}])[-1]
        if (
            len(native_paths) != 1
            or input_manifest.get("input_triangle_count") != 3764
            or input_manifest.get("canonical_triangle_count") != 3764
            or summary.get("max_depth") != 8
            or summary.get("step") != 0
            or depth_eight.get("reference_surface_count", 0) <= 0
            or depth_eight.get("reference_surface_count")
            != depth_eight.get("candidate_surface_count")
            or depth_eight.get("missing_surface_count") != 0
            or depth_eight.get("excess_surface_count") != 0
            or summary.get("objective_facts")
            != {
                "global_depth_8_zero": True,
                "out_of_frame_clear": True,
                "no_evidence_conflict": True,
            }
        ):
            raise ProjectClosureError(
                "meshscope native import/backend conformance failed"
            )
        native_payload = native_paths[0].read_bytes()
        native_basename = native_paths[0].name
        depth_eight_evidence = {
            key: depth_eight[key]
            for key in (
                "depth",
                "reference_surface_count",
                "candidate_surface_count",
                "missing_surface_count",
                "excess_surface_count",
                "union_surface_count",
                "surface_error_count",
            )
        }
    dependencies = []
    for dependency in sorted(dependency_wheels, key=lambda path: path.name.lower()):
        dependency_payload = dependency.read_bytes()
        dependencies.append(
            {
                "path": dependency.name,
                "sha256": f"sha256:{hashlib.sha256(dependency_payload).hexdigest()}",
                "bytes": len(dependency_payload),
            }
        )
    result = {
        "schema": "text-to-cad.meshscope-native-conformance-candidate/1",
        "status": "development-candidate",
        "imports": ["PIL", "meshscope", "meshscope.voxblame._native", "numpy", "trimesh"],
        "dependencyWheels": dependencies,
        "meshscopeWheel": {
            "path": wheel.name,
            "sha256": f"sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}",
            "bytes": wheel.stat().st_size,
        },
        "fixture": {
            "id": "cup_cup_033",
            "path": "models/agent-runtime/cup_cup_033/input/cup_cup_033.ply",
            "sha256": f"sha256:{hashlib.sha256(fixture_payload).hexdigest()}",
            "bytes": len(fixture_payload),
            "inputTriangleCount": input_manifest["input_triangle_count"],
            "canonicalTriangleCount": input_manifest["canonical_triangle_count"],
        },
        "nativeModuleBasename": native_basename,
        "nativeModuleSha256": f"sha256:{hashlib.sha256(native_payload).hexdigest()}",
        "nativeBackendCallable": True,
        "measurement": {
            "summarySha256": f"sha256:{hashlib.sha256(summary_bytes).hexdigest()}",
            "maxDepth": summary["max_depth"],
            "step": summary["step"],
            "depthEight": depth_eight_evidence,
            "objectiveFacts": summary["objective_facts"],
        },
        "providerDispatchCount": 0,
    }
    result["nativeConformanceDigest"] = canonical_json_digest(result)
    return result


def assemble_meshscope_development_candidate(
    builds: tuple[Mapping[str, Any], ...],
    audit: Mapping[str, Any],
    conformance: Mapping[str, Any],
) -> dict[str, Any]:
    """Cross-bind Development evidence without claiming dependency admission."""

    if len(builds) != 3:
        raise ProjectClosureError("meshscope candidate requires three build records")
    expected_build_keys = {
        "schema",
        "status",
        "builderImageId",
        "platform",
        "source",
        "sourceDateEpoch",
        "environment",
        "toolchain",
        "commands",
        "buildLogSha256",
        "wheel",
    }
    wheel_identities = []
    source_digests = []
    builder_ids = []
    for build in builds:
        if set(build) != expected_build_keys or (
            build.get("schema"), build.get("status"), build.get("sourceDateEpoch")
        ) != (
            "text-to-cad.meshscope-build-candidate/1",
            "development-candidate",
            1_755_302_400,
        ):
            raise ProjectClosureError("meshscope build record shape is invalid")
        source = build.get("source")
        if not isinstance(source, Mapping):
            raise ProjectClosureError("meshscope build source record is invalid")
        _validate_meshscope_source_record(source)
        wheel_identity = build.get("wheel")
        if not isinstance(wheel_identity, Mapping) or set(wheel_identity) != {
            "path", "sha256", "bytes"
        }:
            raise ProjectClosureError("meshscope build wheel identity is invalid")
        if (
            wheel_identity.get("path")
            != "meshscope-0.1.0-cp312-cp312-linux_x86_64.whl"
            or not _is_digest(wheel_identity.get("sha256"), prefixed=True)
            or type(wheel_identity.get("bytes")) is not int
            or wheel_identity["bytes"] <= 0
        ):
            raise ProjectClosureError("meshscope build wheel identity is invalid")
        if not _is_digest(build.get("buildLogSha256"), prefixed=True):
            raise ProjectClosureError("meshscope build log identity is invalid")
        wheel_identities.append(dict(wheel_identity))
        source_digests.append(source["sourceTreeDigest"])
        builder_ids.append(build.get("builderImageId"))
    if (
        len({canonical_json_digest(item) for item in wheel_identities}) != 1
        or len(set(source_digests)) != 1
        or len(set(builder_ids)) != 1
        or not _is_digest(builder_ids[0], prefixed=True)
    ):
        raise ProjectClosureError("meshscope builds are not reproducibly cross-bound")

    expected_audit_keys = {
        "distribution", "version", "wheelPath", "wheelSha256", "wheelBytes",
        "sourceTreeDigest", "fileManifestDigest", "wheelRecordDigest",
        "nativePath", "nativeSha256", "needed", "versionRequirements",
        "resolvedLibraries", "rpathAbsent", "runpathAbsent",
        "auditwheelPlatformTag", "auditwheelReportDigest", "elfReportsDigest",
        "files", "nativeAuditDigest",
    }
    if set(audit) != expected_audit_keys:
        raise ProjectClosureError("meshscope audit record shape is invalid")
    audit_without_digest = dict(audit)
    audit_digest = audit_without_digest.pop("nativeAuditDigest", None)
    if audit_digest != canonical_json_digest(audit_without_digest):
        raise ProjectClosureError("meshscope native audit digest is invalid")
    wheel_identity = wheel_identities[0]
    if (
        audit.get("wheelPath") != wheel_identity["path"]
        or f"sha256:{audit.get('wheelSha256')}" != wheel_identity["sha256"]
        or audit.get("wheelBytes") != wheel_identity["bytes"]
        or audit.get("sourceTreeDigest") != source_digests[0]
        or list(audit.get("needed", ())) != list(_MESHSCOPE_DIRECT_NEEDED)
        or list(audit.get("versionRequirements", ()))
        != list(_MESHSCOPE_VERSION_REQUIREMENTS)
        or audit.get("rpathAbsent") is not True
        or audit.get("runpathAbsent") is not True
        or audit.get("auditwheelPlatformTag") != "manylinux_2_24_x86_64"
    ):
        raise ProjectClosureError("meshscope audit is not bound to the build")

    expected_conformance_keys = {
        "schema", "status", "imports", "dependencyWheels", "meshscopeWheel",
        "fixture", "nativeModuleBasename", "nativeModuleSha256",
        "nativeBackendCallable", "measurement", "providerDispatchCount",
        "nativeConformanceDigest",
    }
    if set(conformance) != expected_conformance_keys or (
        conformance.get("schema"),
        conformance.get("status"),
        conformance.get("providerDispatchCount"),
    ) != (
        "text-to-cad.meshscope-native-conformance-candidate/1",
        "development-candidate",
        0,
    ):
        raise ProjectClosureError("meshscope conformance record shape is invalid")
    conformance_without_digest = dict(conformance)
    conformance_digest = conformance_without_digest.pop(
        "nativeConformanceDigest", None
    )
    if conformance_digest != canonical_json_digest(conformance_without_digest):
        raise ProjectClosureError("meshscope native conformance digest is invalid")
    dependencies = conformance.get("dependencyWheels")
    if not isinstance(dependencies, (list, tuple)) or len(dependencies) != 3:
        raise ProjectClosureError("meshscope dependency candidate set is invalid")
    dependency_names = []
    for dependency in dependencies:
        if not isinstance(dependency, Mapping) or set(dependency) != {
            "path", "sha256", "bytes"
        }:
            raise ProjectClosureError("meshscope dependency candidate is invalid")
        if (
            not _is_digest(dependency.get("sha256"), prefixed=True)
            or type(dependency.get("bytes")) is not int
            or dependency["bytes"] <= 0
            or PurePosixPath(str(dependency.get("path"))).name
            != dependency.get("path")
        ):
            raise ProjectClosureError("meshscope dependency candidate is invalid")
        dependency_names.append(dependency["path"])
    if dependency_names != sorted(dependency_names, key=str.lower):
        raise ProjectClosureError("meshscope dependency candidates are not ordered")
    measurement = conformance.get("measurement")
    fixture = conformance.get("fixture")
    if (
        list(conformance.get("imports", ()))
        != ["PIL", "meshscope", "meshscope.voxblame._native", "numpy", "trimesh"]
        or not isinstance(fixture, Mapping)
        or fixture.get("sha256") != f"sha256:{_CUP_INPUT_SHA256}"
        or fixture.get("bytes") != _CUP_INPUT_BYTES
        or fixture.get("inputTriangleCount") != 3764
        or fixture.get("canonicalTriangleCount") != 3764
        or not isinstance(measurement, Mapping)
        or measurement.get("maxDepth") != 8
        or measurement.get("step") != 0
        or measurement.get("objectiveFacts")
        != {
            "global_depth_8_zero": True,
            "out_of_frame_clear": True,
            "no_evidence_conflict": True,
        }
    ):
        raise ProjectClosureError("meshscope Cup conformance facts are invalid")
    if (
        conformance.get("meshscopeWheel") != wheel_identity
        or conformance.get("nativeModuleSha256") != audit.get("nativeSha256")
        or conformance.get("nativeBackendCallable") is not True
        or conformance.get("fixture", {}).get("sha256")
        != f"sha256:{_CUP_INPUT_SHA256}"
    ):
        raise ProjectClosureError("meshscope conformance is not bound to the audit")

    record_names = (
        "build-1.json",
        "build-2.json",
        "build-alternate-root.json",
        "wheel-audit.json",
        "cup-native-conformance.json",
    )
    record_values = (*builds, audit, conformance)
    result = {
        "schema": "text-to-cad.meshscope-development-candidate/1",
        "status": "development-candidate",
        "builderImageId": builder_ids[0],
        "sourceTreeDigest": source_digests[0],
        "reproducibility": {
            "buildCount": 3,
            "absoluteBuildRootVariation": True,
            "byteIdentical": True,
            "wheel": wheel_identity,
        },
        "records": [
            {
                "path": name,
                "digest": canonical_json_digest(value),
            }
            for name, value in zip(record_names, record_values, strict=True)
        ],
        "nativeAuditDigest": audit_digest,
        "nativeConformanceDigest": conformance_digest,
        "admission": {
            "admitted": False,
            "blockers": [
                "sai004-builder-formal-admission-binding",
                "sai004-runtime-wheels-formal-admission-binding",
            ],
        },
    }
    return result


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


def _read_record(path: Path, label: str) -> Mapping[str, Any]:
    value = parse_canonical_json(path.read_bytes())
    if not isinstance(value, Mapping):
        raise ProjectClosureError(f"{label} must be a canonical JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Produce browser-free project artifacts for the sealed Agent runtime."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--repo-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    meshscope_source = subparsers.add_parser("record-meshscope-source")
    meshscope_source.add_argument("--repo-root", type=Path, required=True)
    meshscope_source.add_argument("--record", type=Path, required=True)
    meshshot_audit = subparsers.add_parser("audit-meshshot")
    meshshot_audit.add_argument("--wheel", type=Path, required=True)
    meshshot_audit.add_argument("--source-record", type=Path, required=True)
    meshshot_audit.add_argument("--record", type=Path, required=True)
    meshscope_build = subparsers.add_parser("build-meshscope")
    meshscope_build.add_argument("--repo-root", type=Path, required=True)
    meshscope_build.add_argument("--output", type=Path, required=True)
    meshscope_build.add_argument("--python", default="python3.12")
    meshscope_build.add_argument("--builder-image-id")
    meshscope_build.add_argument("--record", type=Path)
    meshscope_audit = subparsers.add_parser("audit-meshscope")
    meshscope_audit.add_argument("--wheel", type=Path, required=True)
    meshscope_audit.add_argument("--source-record", type=Path, required=True)
    meshscope_audit.add_argument("--record", type=Path, required=True)
    meshscope_audit.add_argument("--readelf", default="readelf")
    meshscope_audit.add_argument("--ldd", default="ldd")
    meshscope_audit.add_argument("--auditwheel", default="auditwheel")
    meshscope_verify = subparsers.add_parser("verify-meshscope")
    meshscope_verify.add_argument("--wheel", type=Path, required=True)
    meshscope_verify.add_argument("--fixture", type=Path, required=True)
    meshscope_verify.add_argument(
        "--dependency-wheel", type=Path, action="append", required=True
    )
    meshscope_verify.add_argument("--record", type=Path, required=True)
    meshscope_verify.add_argument("--python", default="python3.12")
    meshscope_candidate = subparsers.add_parser("assemble-meshscope-candidate")
    meshscope_candidate.add_argument(
        "--build-record", type=Path, action="append", required=True
    )
    meshscope_candidate.add_argument("--audit-record", type=Path, required=True)
    meshscope_candidate.add_argument(
        "--conformance-record", type=Path, required=True
    )
    meshscope_candidate.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        meshshot = generate_meshshot_distribution(args.repo_root.resolve(), args.output)
        meshscope = meshscope_source_record(args.repo_root.resolve())
        implicit = generate_implicit_runtime(args.repo_root.resolve(), args.output)
        _write_record(args.output / "meshshot-source-manifest.json", meshshot)
        _write_record(args.output / "meshscope-source-manifest.json", meshscope)
        _write_record(args.output / "implicit-runtime-record.json", implicit)
    elif args.command == "record-meshscope-source":
        _write_record(
            args.record, meshscope_source_record(args.repo_root.resolve())
        )
    elif args.command == "audit-meshshot":
        source = _read_record(args.source_record, "meshshot source record")
        _write_record(args.record, audit_meshshot_wheel(args.wheel, source))
    elif args.command == "build-meshscope":
        wheel = build_meshscope_wheel(
            args.repo_root.resolve(),
            args.output,
            python=args.python,
            builder_image_id=args.builder_image_id,
            record_path=args.record,
        )
        print(wheel)
    elif args.command == "audit-meshscope":
        source = _read_record(args.source_record, "meshscope source record")
        _write_record(
            args.record,
            audit_meshscope_wheel(
                args.wheel,
                source,
                readelf=args.readelf,
                ldd=args.ldd,
                auditwheel=args.auditwheel,
            ),
        )
    elif args.command == "verify-meshscope":
        _write_record(
            args.record,
            verify_meshscope_native_install(
                args.wheel,
                args.fixture,
                tuple(args.dependency_wheel),
                python=args.python,
            ),
        )
    elif args.command == "assemble-meshscope-candidate":
        builds = tuple(
            _read_record(path, "meshscope build record")
            for path in args.build_record
        )
        audit = _read_record(args.audit_record, "meshscope audit record")
        conformance = _read_record(
            args.conformance_record, "meshscope conformance record"
        )
        _write_record(
            args.record,
            assemble_meshscope_development_candidate(builds, audit, conformance),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
