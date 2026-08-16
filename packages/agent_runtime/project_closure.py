"""Produce and audit the project-owned sealed Agent runtime closure."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
from typing import Any, Mapping
import zipfile


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


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the integer-only, ASCII project-artifact vocabulary."""

    def reject_constant(token: str) -> None:
        raise ProjectClosureError(f"non-finite JSON number is forbidden: {token}")

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    json.loads(encoded, parse_constant=reject_constant)
    return encoded


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _tree_records(root: Path) -> list[dict[str, Any]]:
    return [
        _file_record(root, path)
        for path in sorted(root.rglob("*"))
        if (
            path.is_file()
            and not path.is_symlink()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
            and not any(part.endswith((".egg-info", ".dist-info")) for part in path.parts)
            and "build" not in path.parts
        )
    ]


def _record_digest(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json_bytes(records)).hexdigest()


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
    records = _tree_records(target)
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


def audit_meshshot_wheel(wheel: Path) -> dict[str, Any]:
    """Prove the built wheel contains only the Broker client/profile surface."""

    if not re.fullmatch(r"meshshot_agent_runtime-0\.1\.0-py3-none-any\.whl", wheel.name):
        raise ProjectClosureError("meshshot wheel filename is not the exact pure wheel")
    payload = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(archive.namelist())
        _validate_archive_names(names, "meshshot wheel")
        lowered_names = "\n".join(names).lower()
        lowered_bytes = b"\n".join(archive.read(name).lower() for name in names)
        executable_entries = sorted(
            info.filename
            for info in archive.infolist()
            if not info.is_dir() and ((info.external_attr >> 16) & 0o111)
        )
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ProjectClosureError("meshshot wheel metadata is not unique")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    for marker in _FORBIDDEN_ARCHIVE_MARKERS:
        if marker in lowered_names or marker.encode("ascii") in lowered_bytes:
            raise ProjectClosureError(f"meshshot wheel contains forbidden marker: {marker}")
    required = {
        "meshshot/__init__.py",
        "meshshot/broker_client.py",
        "meshshot/profile.py",
        "meshshot/browser_contract.json",
        "meshshot/profiles/cadena_residual_eight_view_v1.json",
    }
    if not required.issubset(names) or "meshshot/renderer.py" in names:
        raise ProjectClosureError("meshshot wheel package surface is not closed")
    requirements = sorted(
        line.removeprefix("Requires-Dist: ").strip()
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist: ")
    )
    if requirements != ["Pillow==12.2.0"]:
        raise ProjectClosureError("meshshot wheel dependency metadata is not exact")
    if executable_entries:
        raise ProjectClosureError("meshshot wheel contains executable files")
    return {
        "distribution": "meshshot-agent-runtime",
        "version": "0.1.0",
        "wheelPath": wheel.name,
        "wheelSha256": hashlib.sha256(payload).hexdigest(),
        "wheelBytes": len(payload),
        "browserInventoryEmpty": True,
        "browserDenial": {
            "playwrightPackageOrImportAbsent": True,
            "browserExecutableAbsent": True,
            "browserCachePathAbsent": True,
            "localBrowserRuntimeAbsent": True,
        },
        "files": names,
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
    records = _tree_records(target)
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


def audit_meshscope_wheel(wheel: Path, *, readelf: str = "readelf") -> dict[str, Any]:
    """Audit one CPython 3.12 linux/amd64 native meshscope wheel and ELF."""

    if _MESHSCOPE_WHEEL.fullmatch(wheel.name) is None:
        raise ProjectClosureError("meshscope wheel must be cp312-cp312 linux_x86_64")
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
    report_payload = canonical_json_bytes({"needed": needed, "reports": reports})
    return {
        "distribution": "meshscope",
        "version": "0.1.0",
        "wheelPath": wheel.name,
        "wheelSha256": hashlib.sha256(payload).hexdigest(),
        "wheelBytes": len(payload),
        "nativePath": native[0],
        "needed": needed,
        "rpathAbsent": True,
        "runpathAbsent": True,
        "nativeAuditDigest": hashlib.sha256(report_payload).hexdigest(),
        "files": names,
    }


def meshscope_source_record(repo_root: Path) -> dict[str, Any]:
    """Bind the exact project source used to build the meshscope wheel."""

    root = repo_root / "packages/meshscope"
    records = _tree_records(root)
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


def assemble_python_artifact(
    source: Mapping[str, Any], wheel: Mapping[str, Any]
) -> dict[str, Any]:
    """Join source and wheel identities without accepting field substitution."""

    if (
        source.get("distribution") != wheel.get("distribution")
        or source.get("version") != wheel.get("version")
    ):
        raise ProjectClosureError("project wheel does not match its source distribution")
    required_source = {"sourceTreeDigest", "fileManifestDigest"}
    required_wheel = {"wheelPath", "wheelSha256", "wheelBytes"}
    if not required_source.issubset(source) or not required_wheel.issubset(wheel):
        raise ProjectClosureError("project source or wheel identity is incomplete")
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
    else:
        raise ProjectClosureError("unsupported project distribution")
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

    if meshshot.get("distribution") != "meshshot-agent-runtime" or set(meshshot) != {
        "distribution", "version", "wheelPath", "wheelSha256", "wheelBytes",
        "sourceTreeDigest", "fileManifestDigest", "browserInventoryEmpty",
        "browserDenial", "importName", "publicCallable",
    }:
        raise ProjectClosureError("meshshot project artifact identity is invalid")
    if meshscope.get("distribution") != "meshscope" or set(meshscope) != {
        "distribution", "version", "wheelPath", "wheelSha256", "wheelBytes",
        "sourceTreeDigest", "fileManifestDigest", "nativeAuditDigest",
        "nativeConformanceDigest", "needed",
    }:
        raise ProjectClosureError("meshscope project artifact identity is invalid")
    if implicit.get("schema") != "text-to-cad.implicit-runtime-files/1":
        raise ProjectClosureError("implicit project artifact identity is invalid")
    return {
        "schema": "text-to-cad.agent-runtime-project-closure/1",
        "platform": {"architecture": "amd64", "os": "linux", "pythonAbi": "cp312"},
        "pythonArtifacts": [dict(meshscope), dict(meshshot)],
        "implicitRuntime": dict(implicit),
    }


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
    meshshot_audit.add_argument("--record", type=Path, required=True)
    meshscope_build = subparsers.add_parser("build-meshscope")
    meshscope_build.add_argument("--repo-root", type=Path, required=True)
    meshscope_build.add_argument("--output", type=Path, required=True)
    meshscope_build.add_argument("--python", default="python3.12")
    meshscope_audit = subparsers.add_parser("audit-meshscope")
    meshscope_audit.add_argument("--wheel", type=Path, required=True)
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
        _write_record(args.record, audit_meshshot_wheel(args.wheel))
    elif args.command == "build-meshscope":
        wheel = build_meshscope_wheel(
            args.repo_root.resolve(), args.output, python=args.python
        )
        print(wheel)
    elif args.command == "audit-meshscope":
        _write_record(
            args.record, audit_meshscope_wheel(args.wheel, readelf=args.readelf)
        )
    elif args.command == "verify-meshscope":
        _write_record(
            args.record,
            verify_meshscope_native_install(args.wheel, python=args.python),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
