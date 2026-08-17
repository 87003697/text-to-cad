"""Deterministic, network-independent OCI image-layout construction.

This module deliberately owns bytes rather than delegating identity to Docker.
It is usable for Development candidates while upstream admission remains local;
it does not publish, import, tag, or claim a Verified runtime.
"""

from __future__ import annotations

import ast
import binascii
import ctypes
import errno
import hashlib
import io
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import struct
import tempfile
from typing import Any, Mapping, NamedTuple
import zipfile

from scripts.pilot.agent_runtime import (
    canonical_json_bytes,
    canonical_json_digest,
    parse_canonical_json,
)


ENTRYPOINT = "/usr/local/libexec/text-to-cad-agent-entrypoint"
RUNTIME_MANIFEST = "/usr/share/text-to-cad/runtime-manifest.json"
CUP_MANIFEST = "/usr/share/text-to-cad/cup-capability-manifest.json"
LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
FIXED_ENV = [
    "HOME=/home/agent",
    "LANG=C.UTF-8",
    "LC_ALL=C.UTF-8",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE=1",
    "TZ=UTC",
]
FORBIDDEN_EXTERNAL_NAMES = {
    "browser-inventory.json",
    "browser-scan-receipt.json",
    "sbom.spdx.json",
}
FORBIDDEN_RUNTIME_KEYS = {
    "agentImageManifestDigest",
    "browserInventoryDigest",
    "browserScanReceiptDigest",
    "candidateDigest",
    "lockDigest",
    "runtimeManifestDigest",
    "sbomDigest",
    "verifiedRootDigest",
}
FILTERED_ROOTFS_PREFIXES = (
    "etc/apt",
    "etc/hostname",
    "etc/hosts",
    "etc/resolv.conf",
    "opt/sai004",
    "root",
    "tmp",
    "usr/include",
    "usr/local/include",
    "usr/local/lib/node-v24.13.0",
    "usr/lib/gcc",
    "usr/libexec/gcc",
    "var/cache",
    "var/lib/apt/lists",
    "var/log",
)
FILTERED_TOOL_PATHS = {
    "usr/bin/ar", "usr/bin/as", "usr/bin/c++", "usr/bin/cc", "usr/bin/cpp",
    "usr/bin/g++", "usr/bin/gcc", "usr/bin/ld", "usr/bin/make", "usr/bin/nm",
    "usr/bin/objcopy", "usr/bin/objdump", "usr/bin/patchelf", "usr/bin/ranlib",
    "usr/bin/readelf", "usr/bin/strip",
    "usr/lib/git-core/git-web--browse",
    # The sealed Agent runtime has no browser lifecycle authority.  The stdlib
    # helper is therefore not runtime payload, even though Python installs it
    # executable on Noble and it embeds literal browser product names.
    "usr/lib/python3.12/webbrowser.py",
}
_DIGEST_PREFIX = "sha256:"
SPDX_WHEEL_DIGEST = "sha256:4470ca5de095d04e4172d8776e245d629a99abf0d08741261dd014559b746534"
SPDX_WHEEL_SIZE = 18_657
SPDX_CATALOG_DIGEST = "sha256:5865e5d860a9278d30d22eb5522952f85eb620b2a6a3e68e02a5df7449835a31"
SPDX_CATALOG_SIZE = 12_540
SPDX_DOCUMENT_MAX_BYTES = 8 * 1024 * 1024


class BuildInputError(ValueError):
    pass


class RuntimeManifestError(ValueError):
    pass


class OciAuditError(ValueError):
    pass


class BuildRequest(NamedTuple):
    rootfs: Path
    runtime_manifest: Mapping[str, Any]


def spdx_json_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(value, max_document_bytes=SPDX_DOCUMENT_MAX_BYTES)


def _sha256(payload: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def _check_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith(_DIGEST_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise BuildInputError("invalid sha256 digest")
    return value


def _read_stable_regular(path: Path, before: os.stat_result) -> bytes:
    """Read the exact inode described by ``before`` and prove it stayed stable."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError) as error:
        raise BuildInputError("exact input cannot be opened without following links") from error
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(opened_before.st_mode):
            raise BuildInputError("exact input is not a regular file")
        if (before.st_dev, before.st_ino) != (opened_before.st_dev, opened_before.st_ino):
            raise BuildInputError("exact input changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > before.st_size:
                raise BuildInputError("exact input exceeds initial size")
        payload = b"".join(chunks)
        opened_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as error:
        raise BuildInputError("exact input disappeared") from error
    identities = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if identities != (
        opened_after.st_dev,
        opened_after.st_ino,
        opened_after.st_mode,
        opened_after.st_size,
        opened_after.st_mtime_ns,
        opened_after.st_ctime_ns,
    ) or identities != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise BuildInputError("exact input changed during read")
    return payload


def read_exact_regular(path: Path, *, digest: str, size: int) -> bytes:
    """Read one approved stable regular file without following its final link."""

    _check_digest(digest)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise BuildInputError("invalid exact size")
    try:
        before = path.lstat()
    except OSError as error:
        raise BuildInputError("exact input cannot be inspected") from error
    payload = _read_stable_regular(path, before)
    if len(payload) != size or _sha256(payload) != digest:
        raise BuildInputError("exact input identity mismatch")
    return payload


def _container_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or not path.isascii():
        raise RuntimeManifestError("runtime path must be absolute ASCII")
    pure = PurePosixPath(path)
    if str(pure) != path or path == "/" or "\\" in path or any(
        part in ("", ".", "..") for part in path.split("/")[1:]
    ):
        raise RuntimeManifestError("runtime path is not normalized")
    return path


def _regular_identity(rootfs: Path, container_path: str) -> dict[str, Any]:
    path = rootfs / container_path.removeprefix("/")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeManifestError("runtime manifest path is not a regular file")
    payload = _read_stable_regular(path, before)
    return {
        "path": container_path,
        "mode": stat.S_IMODE(before.st_mode),
        "bytes": len(payload),
        "digest": _sha256(payload),
    }


def _runtime_uint(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**63:
        raise RuntimeManifestError(f"{field} is not an unsigned int63")
    return value


def _runtime_mode(value: object, field: str) -> int:
    checked = _runtime_uint(value, field)
    if checked & ~0o777:
        raise RuntimeManifestError(f"{field} is not a permission mode")
    return checked


def _runtime_digest(value: object, field: str) -> str:
    try:
        return _check_digest(value)
    except BuildInputError as error:
        raise RuntimeManifestError(f"{field} is not a full digest") from error


def _runtime_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise RuntimeManifestError(f"{field} is not nonempty ASCII")
    return value


def validate_runtime_manifest(value: Mapping[str, Any], rootfs: Path) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "platform",
        "entrypoint",
        "cupCapabilityManifest",
        "programs",
        "nativeLibraries",
        "runtimeFiles",
    }:
        raise RuntimeManifestError("runtime manifest top-level schema is not closed")
    if value["schema"] != "text-to-cad.agent-runtime-manifest/1":
        raise RuntimeManifestError("runtime manifest schema literal is wrong")
    if value["platform"] != {"architecture": "amd64", "os": "linux"}:
        raise RuntimeManifestError("runtime platform is wrong")
    try:
        encoded = canonical_json_bytes(value)
    except (TypeError, ValueError) as error:
        raise RuntimeManifestError("runtime manifest is not canonical JSON data") from error
    for key in FORBIDDEN_RUNTIME_KEYS:
        if key.encode("ascii") in encoded:
            raise RuntimeManifestError("runtime manifest contains a downstream identity")

    entrypoint = value["entrypoint"]
    if not isinstance(entrypoint, Mapping) or set(entrypoint) != {
        "path", "mode", "bytes", "digest", "argv"
    }:
        raise RuntimeManifestError("entrypoint identity is not closed")
    _runtime_mode(entrypoint["mode"], "entrypoint.mode")
    _runtime_uint(entrypoint["bytes"], "entrypoint.bytes")
    _runtime_digest(entrypoint["digest"], "entrypoint.digest")
    if entrypoint["path"] != ENTRYPOINT or entrypoint["mode"] != 0o555:
        raise RuntimeManifestError("entrypoint path or mode is wrong")
    if entrypoint["argv"] != [ENTRYPOINT]:
        raise RuntimeManifestError("entrypoint argv is wrong")
    cup = value["cupCapabilityManifest"]
    if not isinstance(cup, Mapping) or set(cup) != {"path", "digest"} or cup["path"] != CUP_MANIFEST:
        raise RuntimeManifestError("Cup manifest identity is not closed")
    _runtime_digest(cup["digest"], "cupCapabilityManifest.digest")

    runtime_files = value["runtimeFiles"]
    programs = value["programs"]
    libraries = value["nativeLibraries"]
    if not all(isinstance(items, list) for items in (runtime_files, programs, libraries)):
        raise RuntimeManifestError("runtime inventory members must be arrays")
    paths: list[str] = []
    observed: dict[str, Mapping[str, Any]] = {}
    for record in runtime_files:
        if not isinstance(record, Mapping) or set(record) != {"path", "mode", "bytes", "digest"}:
            raise RuntimeManifestError("runtime file record is not closed")
        path = _container_path(record["path"])
        _runtime_mode(record["mode"], "runtimeFiles.mode")
        _runtime_uint(record["bytes"], "runtimeFiles.bytes")
        _runtime_digest(record["digest"], "runtimeFiles.digest")
        if path == RUNTIME_MANIFEST:
            raise RuntimeManifestError("runtime manifest cannot inventory itself")
        actual = _regular_identity(rootfs, path)
        if dict(record) != actual:
            raise RuntimeManifestError("runtime file identity does not match rootfs")
        paths.append(path)
        observed[path] = record
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RuntimeManifestError("runtime files are not path-sorted and unique")

    program_paths: list[str] = []
    for record in programs:
        if not isinstance(record, Mapping) or set(record) != {"name", "path", "version", "digest"}:
            raise RuntimeManifestError("program record is not closed")
        path = _container_path(record["path"])
        _runtime_text(record["name"], "program.name")
        _runtime_text(record["version"], "program.version")
        _runtime_digest(record["digest"], "program.digest")
        if path not in observed or record["digest"] != observed[path]["digest"]:
            raise RuntimeManifestError("program is not present in runtime files")
        program_paths.append(path)
    if program_paths != sorted(program_paths) or len(program_paths) != len(set(program_paths)):
        raise RuntimeManifestError("programs are not path-sorted and unique")

    library_paths: list[str] = []
    for record in libraries:
        if not isinstance(record, Mapping) or set(record) != {"path", "soname", "digest"}:
            raise RuntimeManifestError("native library record is not closed")
        path = _container_path(record["path"])
        _runtime_text(record["soname"], "nativeLibrary.soname")
        _runtime_digest(record["digest"], "nativeLibrary.digest")
        if path not in observed or record["digest"] != observed[path]["digest"]:
            raise RuntimeManifestError("native library is not present in runtime files")
        library_paths.append(path)
    if library_paths != sorted(library_paths) or len(library_paths) != len(set(library_paths)):
        raise RuntimeManifestError("native libraries are not path-sorted and unique")

    entry_actual = observed.get(ENTRYPOINT)
    cup_actual = observed.get(CUP_MANIFEST)
    if entry_actual is None or cup_actual is None:
        raise RuntimeManifestError("entrypoint or Cup manifest is outside runtime files")
    if {key: entry_actual[key] for key in ("path", "mode", "bytes", "digest")} != {
        key: entrypoint[key] for key in ("path", "mode", "bytes", "digest")
    }:
        raise RuntimeManifestError("entrypoint identity disagrees with runtime files")
    if cup["digest"] != cup_actual["digest"]:
        raise RuntimeManifestError("Cup manifest digest disagrees with runtime files")


def synthetic_test_request(rootfs: Path) -> BuildRequest:
    entrypoint = _regular_identity(rootfs, ENTRYPOINT)
    cup = _regular_identity(rootfs, CUP_MANIFEST)
    payload = _regular_identity(rootfs, "/opt/text-to-cad/payload.txt")
    runtime_files = sorted((entrypoint, payload, cup), key=lambda item: item["path"])
    runtime_manifest = {
        "schema": "text-to-cad.agent-runtime-manifest/1",
        "platform": {"architecture": "amd64", "os": "linux"},
        "entrypoint": {**entrypoint, "argv": [ENTRYPOINT]},
        "cupCapabilityManifest": {"path": CUP_MANIFEST, "digest": cup["digest"]},
        "programs": [
            {
                "name": "text-to-cad-agent-entrypoint",
                "path": ENTRYPOINT,
                "version": "1",
                "digest": entrypoint["digest"],
            }
        ],
        "nativeLibraries": [],
        "runtimeFiles": runtime_files,
    }
    return BuildRequest(rootfs=rootfs, runtime_manifest=runtime_manifest)


def make_runtime_manifest(
    rootfs: Path,
    *,
    programs: list[tuple[str, str, str]],
    native_libraries: list[tuple[str, str]],
    project_prefixes: tuple[str, ...],
) -> Mapping[str, Any]:
    """Construct the closed manifest from observed, already-staged bytes."""

    entrypoint = _regular_identity(rootfs, ENTRYPOINT)
    cup = _regular_identity(rootfs, CUP_MANIFEST)
    runtime_records: dict[str, dict[str, Any]] = {
        entrypoint["path"]: entrypoint,
        cup["path"]: cup,
    }
    program_records: list[dict[str, Any]] = []
    for name, path, version in programs:
        identity = _regular_identity(rootfs, _container_path(path))
        runtime_records[path] = identity
        program_records.append(
            {"name": name, "path": path, "version": version, "digest": identity["digest"]}
        )
    library_records: list[dict[str, Any]] = []
    for path, soname in native_libraries:
        identity = _regular_identity(rootfs, _container_path(path))
        runtime_records[path] = identity
        library_records.append({"path": path, "soname": soname, "digest": identity["digest"]})
    for prefix in project_prefixes:
        prefix = _container_path(prefix)
        source = rootfs / prefix.removeprefix("/")
        if source.is_symlink() or not source.is_dir():
            raise RuntimeManifestError("project runtime prefix is not a regular directory")
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise RuntimeManifestError("project runtime artifacts cannot be symlinks")
            if path.is_file():
                container_path = "/" + path.relative_to(rootfs).as_posix()
                if not container_path.isascii() or _filtered_rootfs_path(
                    container_path.removeprefix("/"), is_directory=False
                ):
                    continue
                runtime_records[container_path] = _regular_identity(rootfs, container_path)
    manifest = {
        "schema": "text-to-cad.agent-runtime-manifest/1",
        "platform": {"architecture": "amd64", "os": "linux"},
        "entrypoint": {**entrypoint, "argv": [ENTRYPOINT]},
        "cupCapabilityManifest": {"path": CUP_MANIFEST, "digest": cup["digest"]},
        "programs": sorted(program_records, key=lambda item: item["path"]),
        "nativeLibraries": sorted(library_records, key=lambda item: item["path"]),
        "runtimeFiles": [runtime_records[path] for path in sorted(runtime_records)],
    }
    validate_runtime_manifest(manifest, rootfs)
    return manifest


def resolve_elf_closure(rootfs: Path, paths: list[str]) -> list[tuple[str, str]]:
    """Resolve a complete rootfs-local DT_NEEDED closure using pyelftools.

    pyelftools is an admitted build-time parser, not copied into the runtime.
    The caller supplies it through the exact SAI-004 builder environment.
    """

    try:
        from elftools.elf.elffile import ELFFile
    except ImportError as error:
        raise BuildInputError("admitted pyelftools parser is unavailable") from error
    search_directories = [
        "/lib64",
        "/lib/x86_64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/local/lib/python3.12/dist-packages/numpy.libs",
        "/usr/local/lib/python3.12/dist-packages/pillow.libs",
        "/usr/local/lib/python3.12/dist-packages/PIL.libs",
    ]
    lookup: dict[str, str] = {}
    for directory in search_directories:
        host = rootfs / directory.removeprefix("/")
        if not host.is_dir():
            continue
        for candidate in sorted(host.iterdir()):
            if candidate.is_file() and not candidate.is_symlink():
                resolved = candidate.resolve(strict=True)
                if rootfs == resolved or rootfs in resolved.parents:
                    lookup.setdefault(candidate.name, "/" + resolved.relative_to(rootfs).as_posix())
            elif candidate.is_symlink():
                try:
                    resolved = candidate.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if rootfs == resolved or rootfs in resolved.parents:
                    lookup.setdefault(candidate.name, "/" + resolved.relative_to(rootfs).as_posix())

    def elf_metadata(path: str) -> tuple[str | None, list[str]]:
        host = rootfs / path.removeprefix("/")
        with host.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                return None, []
            stream.seek(0)
            elf = ELFFile(stream)
            dynamic = elf.get_section_by_name(".dynamic")
            soname = None
            needed: list[str] = []
            if dynamic is not None:
                for tag in dynamic.iter_tags():
                    if tag.entry.d_tag == "DT_NEEDED":
                        needed.append(tag.needed)
                    elif tag.entry.d_tag == "DT_SONAME":
                        soname = tag.soname
            return soname, sorted(set(needed))

    queue = list(dict.fromkeys(paths))
    seen_files: set[str] = set()
    libraries: dict[str, str] = {}
    while queue:
        path = queue.pop(0)
        if path in seen_files:
            continue
        seen_files.add(path)
        _soname, needed = elf_metadata(path)
        for needed_name in needed:
            resolved = lookup.get(needed_name)
            if resolved is None:
                raise BuildInputError(f"unresolved ELF dependency: {needed_name}")
            existing = libraries.get(resolved)
            if existing is not None and existing != needed_name:
                raise BuildInputError("one native library resolved under two sonames")
            libraries[resolved] = needed_name
            queue.append(resolved)
    loader = lookup.get("ld-linux-x86-64.so.2")
    if loader is not None:
        libraries.setdefault(loader, "ld-linux-x86-64.so.2")
    return sorted(libraries.items())


def _split_ustar_path(name: str) -> tuple[bytes, bytes]:
    raw = name.encode("ascii")
    if len(raw) <= 100:
        return raw, b""
    pieces = name.split("/")
    for index in range(1, len(pieces)):
        prefix = "/".join(pieces[:index]).encode("ascii")
        suffix = "/".join(pieces[index:]).encode("ascii")
        if len(prefix) <= 155 and len(suffix) <= 100:
            return suffix, prefix
    raise BuildInputError("path is not representable by deterministic USTAR")


def _octal(value: int, width: int) -> bytes:
    rendered = format(value, "o").encode("ascii")
    if len(rendered) > width - 1:
        raise BuildInputError("USTAR numeric value is too large")
    return b"0" * (width - 1 - len(rendered)) + rendered + b"\0"


def _ustar_header(name: str, *, mode: int, size: int, typeflag: bytes, link: str = "") -> bytes:
    filename, prefix = _split_ustar_path(name)
    if len(link.encode("ascii")) > 100:
        raise BuildInputError("symlink target is too long for deterministic USTAR")
    header = bytearray(512)
    header[0:len(filename)] = filename
    header[100:108] = _octal(mode, 8)
    header[108:116] = _octal(0, 8)
    header[116:124] = _octal(0, 8)
    header[124:136] = _octal(size, 12)
    header[136:148] = _octal(0, 12)
    header[148:156] = b"        "
    header[156:157] = typeflag
    target = link.encode("ascii")
    header[157:157 + len(target)] = target
    header[257:265] = b"ustar\x0000"
    header[345:345 + len(prefix)] = prefix
    checksum = sum(header)
    header[148:156] = format(checksum, "06o").encode("ascii") + b"\0 "
    return bytes(header)


def _iter_rootfs(rootfs: Path, virtual_files: Mapping[str, tuple[bytes, int]]) -> list[tuple[str, str, int, bytes | str]]:
    try:
        root_stat = rootfs.lstat()
        root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        root_descriptor = os.open(rootfs, root_flags)
    except OSError as error:
        raise BuildInputError("rootfs cannot be opened without following links") from error
    try:
        opened_root = os.fstat(root_descriptor)
    finally:
        os.close(root_descriptor)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or (root_stat.st_dev, root_stat.st_ino) != (opened_root.st_dev, opened_root.st_ino)
    ):
        raise BuildInputError("rootfs is not one stable no-follow directory")
    entries: dict[str, tuple[str, int, bytes | str]] = {}
    for current, directory_names, filenames in os.walk(rootfs, topdown=True, followlinks=False):
        directory_names.sort()
        filenames.sort()
        current_path = Path(current)
        relative_current = current_path.relative_to(rootfs)
        directory_names[:] = [
            name
            for name in directory_names
            if not _filtered_rootfs_path((relative_current / name).as_posix(), is_directory=True)
        ]
        for name in tuple(directory_names) + tuple(filenames):
            source = current_path / name
            relative = (relative_current / name).as_posix()
            if _filtered_rootfs_path(relative, is_directory=False):
                continue
            # The fixed first-release USTAR profile is ASCII-only. The Noble
            # CA bundle contains two Unicode aliases for one legacy NetLock
            # certificate; aliases are not execution authority and are omitted.
            if not relative.isascii():
                continue
            if relative.startswith("/") or ".." in PurePosixPath(relative).parts:
                raise BuildInputError("rootfs path is unsafe")
            info = source.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if mode & 0o7000:
                raise BuildInputError("setuid, setgid, and sticky bits are forbidden")
            if stat.S_ISDIR(info.st_mode):
                entries[relative] = ("directory", 0o755, b"")
            elif stat.S_ISREG(info.st_mode):
                if source.name.endswith((".pyc", ".pyo")) or "__pycache__" in source.parts:
                    raise BuildInputError("Python bytecode cache is forbidden")
                if source.name in FORBIDDEN_EXTERNAL_NAMES:
                    raise BuildInputError("external evidence artifact was placed in the rootfs")
                payload = _read_stable_regular(source, info)
                entries[relative] = ("regular", mode, payload)
            elif stat.S_ISLNK(info.st_mode):
                target = os.readlink(source)
                if not target.isascii():
                    continue
                if "\0" in target:
                    raise BuildInputError("symlink target is invalid")
                normalized = PurePosixPath(target)
                if str(normalized) != target:
                    raise BuildInputError("symlink target is not normalized")
                resolved_target = (
                    posixpath.normpath(target).removeprefix("/")
                    if target.startswith("/")
                    else posixpath.normpath(str(PurePosixPath(relative).parent / target))
                )
                if _forbidden_runtime_tool_path(resolved_target):
                    directory_names[:] = [item for item in directory_names if item != name]
                    continue
                entries[relative] = ("symlink", 0o777, target)
                directory_names[:] = [item for item in directory_names if item != name]
            else:
                raise BuildInputError("special rootfs entry is forbidden")
    for absolute, (payload, mode) in virtual_files.items():
        _container_path(absolute)
        relative = absolute.removeprefix("/")
        if relative in entries:
            raise BuildInputError("virtual rootfs file collides with an existing entry")
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            entries.setdefault(str(parent), ("directory", 0o755, b""))
            parent = parent.parent
        entries[relative] = ("regular", mode, payload)
    return [(path, *entries[path]) for path in sorted(entries)]


def _filtered_rootfs_path(path: str, *, is_directory: bool) -> bool:
    normalized = path.removesuffix("/")
    if normalized in FILTERED_TOOL_PATHS:
        return True
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in FILTERED_ROOTFS_PREFIXES):
        return True
    name = PurePosixPath(normalized).name
    if name.endswith((".pyc", ".pyo")) or "__pycache__" in PurePosixPath(normalized).parts:
        return True
    if _forbidden_runtime_tool_path(normalized):
        return True
    return False


def _forbidden_runtime_tool_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    executable_surface = any(part in ("bin", "sbin", "libexec") for part in pure.parts)
    exact = {
        "apt", "apt-cache", "apt-get", "apt-key", "apt-mark", "apt-config",
        "dpkg", "dpkg-deb", "dpkg-query", "dpkg-reconfigure", "dpkg-split", "dpkg-trigger",
        "pip", "pip3", "npm", "npx", "corepack", "snap", "snapctl",
        "ssh", "ssh-add", "ssh-agent", "ssh-copy-id", "ssh-keygen", "ssh-keyscan",
        "scp", "sftp", "sshd", "make", "gmake", "cmake", "ninja", "meson",
        "pkg-config", "patchelf", "clang", "clang++", "gcc", "g++", "cc", "c++",
        "cpp", "ld", "ar", "as", "nm", "objcopy", "objdump", "ranlib", "readelf", "strip",
    }
    tool_suffix = re.compile(
        r"(?:^|[-_.])(?:gcc|g\+\+|cc|c\+\+|cpp|clang(?:\+\+)?|ld|ar|as|nm|objcopy|objdump|ranlib|readelf|strip)(?:[-_.].*)?$"
    )
    if executable_surface and (name in exact or tool_suffix.search(name) is not None):
        return True
    return len(pure.parts) >= 2 and pure.parts[0] in ("usr", "opt") and "include" in pure.parts


def _encode_rootfs_tar(rootfs: Path, virtual_files: Mapping[str, tuple[bytes, int]]) -> bytes:
    output = bytearray()
    for name, kind, mode, value in _iter_rootfs(rootfs, virtual_files):
        if kind == "directory":
            output += _ustar_header(name + "/", mode=mode, size=0, typeflag=b"5")
        elif kind == "symlink":
            output += _ustar_header(name, mode=mode, size=0, typeflag=b"2", link=str(value))
        else:
            payload = bytes(value)
            output += _ustar_header(name, mode=mode, size=len(payload), typeflag=b"0")
            output += payload
            output += b"\0" * ((-len(payload)) % 512)
    output += b"\0" * 1024
    return bytes(output)


def _stored_deflate(payload: bytes) -> bytes:
    output = bytearray()
    offset = 0
    if not payload:
        return b"\x01\x00\x00\xff\xff"
    while offset < len(payload):
        block = payload[offset:offset + 65535]
        offset += len(block)
        output.append(1 if offset == len(payload) else 0)
        output += struct.pack("<H", len(block))
        output += struct.pack("<H", 0xFFFF ^ len(block))
        output += block
    return bytes(output)


def _gzip(payload: bytes) -> bytes:
    return (
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff"
        + _stored_deflate(payload)
        + struct.pack("<II", binascii.crc32(payload) & 0xFFFFFFFF, len(payload) & 0xFFFFFFFF)
    )


def _write_blob(root: Path, payload: bytes) -> str:
    digest = _sha256(payload)
    destination = root / "blobs/sha256" / digest.removeprefix(_DIGEST_PREFIX)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    destination.chmod(0o444)
    return digest


def _build_oci_layout_staged(request: BuildRequest, output: Path) -> dict[str, Any]:
    validate_runtime_manifest(request.runtime_manifest, request.rootfs)
    if output.exists() and any(output.iterdir()):
        raise BuildInputError("OCI output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    runtime_bytes = canonical_json_bytes(request.runtime_manifest)
    runtime_digest = _sha256(runtime_bytes)
    layer_tar = _encode_rootfs_tar(request.rootfs, {RUNTIME_MANIFEST: (runtime_bytes, 0o444)})
    diff_id = _sha256(layer_tar)
    layer = _gzip(layer_tar)
    layer_digest = _write_blob(output, layer)

    config = {
        "architecture": "amd64",
        "config": {
            "Cmd": [],
            "Entrypoint": [ENTRYPOINT],
            "Env": FIXED_ENV,
            "Labels": {"org.text-to-cad.agent-runtime-manifest.digest": runtime_digest},
            "User": "65532:65532",
            "WorkingDir": "/work",
        },
        "os": "linux",
        "rootfs": {"diff_ids": [diff_id], "type": "layers"},
    }
    config_bytes = canonical_json_bytes(config)
    config_digest = _write_blob(output, config_bytes)
    manifest = {
        "config": {"digest": config_digest, "mediaType": CONFIG_MEDIA_TYPE, "size": len(config_bytes)},
        "layers": [{"digest": layer_digest, "mediaType": LAYER_MEDIA_TYPE, "size": len(layer)}],
        "mediaType": MANIFEST_MEDIA_TYPE,
        "schemaVersion": 2,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_digest = _write_blob(output, manifest_bytes)
    index = {
        "manifests": [
            {
                "digest": manifest_digest,
                "mediaType": MANIFEST_MEDIA_TYPE,
                "platform": {"architecture": "amd64", "os": "linux"},
                "size": len(manifest_bytes),
            }
        ],
        "schemaVersion": 2,
    }
    index_bytes = canonical_json_bytes(index)
    (output / "index.json").write_bytes(index_bytes)
    (output / "oci-layout").write_bytes(canonical_json_bytes({"imageLayoutVersion": "1.0.0"}))
    record = {
        "configDigest": config_digest,
        "diffId": diff_id,
        "indexDigest": _sha256(index_bytes),
        "layerDigest": layer_digest,
        "layerMediaType": LAYER_MEDIA_TYPE,
        "manifestDigest": manifest_digest,
        "runtimeManifestDigest": runtime_digest,
    }
    if audit_oci_layout(output) != record:
        raise OciAuditError("fresh OCI closure did not pass independent audit")
    return record


def _open_publication_parent(path: Path) -> tuple[int, str]:
    absolute = path.absolute()
    if path.name in ("", ".", "..") or ".." in absolute.parts:
        raise BuildInputError("publication path is invalid")
    descriptor = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for component in absolute.parent.parts[1:]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except OSError as error:
        os.close(descriptor)
        raise BuildInputError("publication ancestor is not a no-follow directory") from error
    return descriptor, absolute.name


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise BuildInputError("publication write made no progress")
        view = view[written:]


def _rename_noreplace(source: str, destination: str, *, source_dir: int, destination_dir: int) -> None:
    """Atomically publish a directory without replacing any terminal entry."""

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if hasattr(library, "renameatx_np"):
        operation = library.renameatx_np
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(source_dir, source_bytes, destination_dir, destination_bytes, 0x00000004)
    elif hasattr(library, "renameat2"):
        operation = library.renameat2
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(source_dir, source_bytes, destination_dir, destination_bytes, 0x00000001)
    else:
        raise BuildInputError("atomic no-replace directory publication is unavailable")
    if result == 0:
        return
    failure = ctypes.get_errno()
    if failure in (errno.EEXIST, errno.ENOTEMPTY):
        raise BuildInputError("publication target already exists")
    raise BuildInputError("atomic directory publication failed") from OSError(failure, os.strerror(failure))


def publish_exclusive_file(path: Path, payload: bytes, mode: int) -> None:
    parent, final_name = _open_publication_parent(path)
    temp_name = f".{final_name}.stage-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        descriptor = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent,
        )
        try:
            _write_all(descriptor, payload)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temp_name, final_name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        except FileExistsError as error:
            raise BuildInputError("publication target already exists") from error
        os.unlink(temp_name, dir_fd=parent)
    except Exception:
        try:
            os.unlink(temp_name, dir_fd=parent)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent)


def publish_exclusive_directory(source: Path, destination: Path) -> None:
    source_parent, source_name = _open_publication_parent(source)
    destination_parent, destination_name = _open_publication_parent(destination)
    try:
        if os.fstat(source_parent).st_dev != os.fstat(destination_parent).st_dev or os.fstat(source_parent).st_ino != os.fstat(destination_parent).st_ino:
            raise BuildInputError("directory publication must remain in one parent")
        source_info = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
        if not stat.S_ISDIR(source_info.st_mode):
            raise BuildInputError("directory publication source is not a directory")
        _rename_noreplace(
            source_name,
            destination_name,
            source_dir=source_parent,
            destination_dir=destination_parent,
        )
    finally:
        os.close(source_parent)
        os.close(destination_parent)


def build_oci_layout(request: BuildRequest, output: Path) -> dict[str, Any]:
    """Build, audit, and exclusively publish one complete OCI layout."""

    parent, final_name = _open_publication_parent(output)
    stage_name = f".{final_name}.stage-{os.getpid()}-{secrets.token_hex(8)}"
    stage = output.absolute().parent / stage_name
    try:
        try:
            os.stat(final_name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BuildInputError("OCI output already exists")
        os.mkdir(stage_name, 0o700, dir_fd=parent)
        record = _build_oci_layout_staged(request, stage)
        _rename_noreplace(stage_name, final_name, source_dir=parent, destination_dir=parent)
        return record
    except Exception:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise
    finally:
        os.close(parent)


def directory_bytes(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise OciAuditError("OCI layout contains a symlink")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def encode_oci_archive(root: Path) -> bytes:
    """Encode the audited OCI image closure as one normalized USTAR archive."""

    audit_oci_layout(root)
    files = {
        path: payload
        for path, payload in directory_bytes(root).items()
        if not path.startswith("artifacts/")
    }
    directories: set[str] = set()
    for path in files:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            directories.add(str(parent))
            parent = parent.parent
    output = bytearray()
    for path in sorted(directories | set(files)):
        if path in directories:
            output += _ustar_header(path + "/", mode=0o755, size=0, typeflag=b"5")
        else:
            payload = files[path]
            output += _ustar_header(path, mode=0o444, size=len(payload), typeflag=b"0")
            output += payload
            output += b"\0" * ((-len(payload)) % 512)
    output += b"\0" * 1024
    return bytes(output)


def audit_oci_archive(payload: bytes) -> dict[str, Any]:
    """Independently parse an OCI transport archive and audit its full closure."""

    entries = _audit_layer_tar(payload)
    files: dict[str, bytes] = {}
    expected_directories: set[str] = set()
    for path, (kind, mode, value) in entries.items():
        if kind == "directory":
            if mode != 0o755:
                raise OciAuditError("OCI archive directory mode is wrong")
            continue
        if kind != "regular" or mode != 0o444:
            raise OciAuditError("OCI archive entry type or mode is wrong")
        files[path] = bytes(value)
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            expected_directories.add(str(parent))
            parent = parent.parent
    actual_directories = {path for path, record in entries.items() if record[0] == "directory"}
    if actual_directories != expected_directories:
        raise OciAuditError("OCI archive directory closure is not exact")
    with tempfile.TemporaryDirectory(prefix="agent-runtime-oci-audit-") as directory:
        root = Path(directory)
        for path, value in files.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        return audit_oci_layout(root)


def _parse_json_exact(payload: bytes) -> Mapping[str, Any]:
    try:
        value = parse_canonical_json(payload)
    except (TypeError, ValueError) as error:
        raise OciAuditError("OCI JSON is not canonical") from error
    if not isinstance(value, Mapping):
        raise OciAuditError("OCI JSON root is not an object")
    return value


def _inflate_stored_gzip(payload: bytes) -> bytes:
    if len(payload) < 23 or payload[:10] != b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff":
        raise OciAuditError("gzip header is not the fixed profile")
    cursor = 10
    output = bytearray()
    while True:
        if cursor + 5 > len(payload) - 8:
            raise OciAuditError("stored DEFLATE block is truncated")
        header = payload[cursor]
        cursor += 1
        if header not in (0, 1):
            raise OciAuditError("gzip is not canonical stored DEFLATE")
        length, inverse = struct.unpack("<HH", payload[cursor:cursor + 4])
        cursor += 4
        if inverse != (0xFFFF ^ length) or cursor + length > len(payload) - 8:
            raise OciAuditError("stored DEFLATE length is invalid")
        if header == 0 and length != 65535:
            raise OciAuditError("non-final DEFLATE block is not maximal")
        output += payload[cursor:cursor + length]
        cursor += length
        if header == 1:
            break
    if cursor != len(payload) - 8:
        raise OciAuditError("gzip has trailing or concatenated data")
    crc, size = struct.unpack("<II", payload[-8:])
    if crc != (binascii.crc32(output) & 0xFFFFFFFF) or size != (len(output) & 0xFFFFFFFF):
        raise OciAuditError("gzip trailer is invalid")
    return bytes(output)


def _parse_octal(field: bytes) -> int:
    if not field.endswith(b"\0") or any(byte not in b"01234567\0" for byte in field):
        raise OciAuditError("USTAR numeric field is not canonical octal")
    stripped = field[:-1].lstrip(b"0") or b"0"
    return int(stripped, 8)


def _audit_layer_tar(payload: bytes) -> dict[str, tuple[str, int, bytes | str]]:
    cursor = 0
    entries: dict[str, tuple[str, int, bytes | str]] = {}
    previous = ""
    while True:
        if cursor + 512 > len(payload):
            raise OciAuditError("USTAR is truncated")
        header = payload[cursor:cursor + 512]
        cursor += 512
        if header == b"\0" * 512:
            if payload[cursor:cursor + 512] != b"\0" * 512 or cursor + 512 != len(payload):
                raise OciAuditError("USTAR terminator is not exact")
            break
        checksum_field = header[148:156]
        mutable = bytearray(header)
        mutable[148:156] = b"        "
        if checksum_field != format(sum(mutable), "06o").encode("ascii") + b"\0 ":
            raise OciAuditError("USTAR checksum is wrong")
        if header[257:265] != b"ustar\x0000":
            raise OciAuditError("layer is not the fixed USTAR profile")
        if _parse_octal(header[108:116]) != 0 or _parse_octal(header[116:124]) != 0 or _parse_octal(header[136:148]) != 0:
            raise OciAuditError("USTAR ownership or mtime is not normalized")
        name = header[:100].split(b"\0", 1)[0].decode("ascii")
        prefix = header[345:500].split(b"\0", 1)[0].decode("ascii")
        path = f"{prefix}/{name}" if prefix else name
        compare_path = path.removesuffix("/")
        if compare_path <= previous or compare_path.startswith("/") or ".." in PurePosixPath(compare_path).parts:
            raise OciAuditError("USTAR path order or safety is invalid")
        previous = compare_path
        mode = _parse_octal(header[100:108])
        size = _parse_octal(header[124:136])
        typeflag = header[156:157]
        if typeflag == b"5":
            if size != 0 or not path.endswith("/") or mode != 0o755:
                raise OciAuditError("USTAR directory is not normalized")
            value: tuple[str, int, bytes | str] = ("directory", mode, b"")
        elif typeflag == b"2":
            if size != 0 or mode != 0o777:
                raise OciAuditError("USTAR symlink is not normalized")
            link = header[157:257].split(b"\0", 1)[0].decode("ascii")
            if str(PurePosixPath(link)) != link:
                raise OciAuditError("USTAR symlink target is not normalized")
            value = ("symlink", mode, link)
        elif typeflag == b"0":
            if mode & 0o7000:
                raise OciAuditError("USTAR regular mode is unsafe")
            data = payload[cursor:cursor + size]
            if len(data) != size:
                raise OciAuditError("USTAR file data is truncated")
            value = ("regular", mode, data)
        else:
            raise OciAuditError("USTAR entry type is forbidden")
        entries[compare_path] = value
        cursor += size
        padding = (-size) % 512
        if payload[cursor:cursor + padding] != b"\0" * padding:
            raise OciAuditError("USTAR padding is not zero")
        cursor += padding
    return entries


def audit_oci_layout(root: Path) -> dict[str, Any]:
    files = directory_bytes(root)
    if "index.json" not in files or "oci-layout" not in files:
        raise OciAuditError("OCI layout roots are incomplete")
    layout = _parse_json_exact(files["oci-layout"])
    if dict(layout) != {"imageLayoutVersion": "1.0.0"}:
        raise OciAuditError("OCI layout version is wrong")
    index = _parse_json_exact(files["index.json"])
    if set(index) != {"manifests", "schemaVersion"} or index["schemaVersion"] != 2:
        raise OciAuditError("OCI index schema is not closed")
    manifests = index["manifests"]
    if not isinstance(manifests, tuple) or len(manifests) != 1:
        raise OciAuditError("OCI index must contain exactly one manifest")
    descriptor = manifests[0]
    if not isinstance(descriptor, Mapping) or set(descriptor) != {"digest", "mediaType", "platform", "size"}:
        raise OciAuditError("OCI index descriptor is not closed")
    if descriptor["mediaType"] != MANIFEST_MEDIA_TYPE or descriptor["platform"] != {"architecture": "amd64", "os": "linux"}:
        raise OciAuditError("OCI index platform or media type is wrong")

    def blob(digest: object, size: object) -> bytes:
        try:
            checked = _check_digest(digest)
        except BuildInputError as error:
            raise OciAuditError("OCI descriptor digest is invalid") from error
        path = "blobs/sha256/" + checked.removeprefix(_DIGEST_PREFIX)
        if path not in files:
            raise OciAuditError("OCI descriptor blob is missing")
        payload = files[path]
        if isinstance(size, bool) or not isinstance(size, int) or size != len(payload) or _sha256(payload) != checked:
            raise OciAuditError("OCI descriptor does not match blob bytes")
        return payload

    manifest_bytes = blob(descriptor["digest"], descriptor["size"])
    manifest = _parse_json_exact(manifest_bytes)
    if set(manifest) != {"config", "layers", "mediaType", "schemaVersion"} or manifest["mediaType"] != MANIFEST_MEDIA_TYPE or manifest["schemaVersion"] != 2:
        raise OciAuditError("OCI manifest schema is not closed")
    if not isinstance(manifest["layers"], tuple) or len(manifest["layers"]) != 1:
        raise OciAuditError("OCI manifest must contain exactly one layer")
    config_descriptor = manifest["config"]
    layer_descriptor = manifest["layers"][0]
    if not isinstance(config_descriptor, Mapping) or set(config_descriptor) != {"digest", "mediaType", "size"} or config_descriptor["mediaType"] != CONFIG_MEDIA_TYPE:
        raise OciAuditError("OCI config descriptor is not closed")
    if not isinstance(layer_descriptor, Mapping) or set(layer_descriptor) != {"digest", "mediaType", "size"} or layer_descriptor["mediaType"] != LAYER_MEDIA_TYPE:
        raise OciAuditError("OCI layer descriptor is not closed")
    config_bytes = blob(config_descriptor["digest"], config_descriptor["size"])
    layer_bytes = blob(layer_descriptor["digest"], layer_descriptor["size"])
    expected_file_set = {
        "index.json",
        "oci-layout",
        "blobs/sha256/" + descriptor["digest"].removeprefix(_DIGEST_PREFIX),
        "blobs/sha256/" + config_descriptor["digest"].removeprefix(_DIGEST_PREFIX),
        "blobs/sha256/" + layer_descriptor["digest"].removeprefix(_DIGEST_PREFIX),
    }
    oci_files = {path for path in files if not path.startswith("artifacts/")}
    if oci_files != expected_file_set:
        raise OciAuditError("OCI blob set is not closed")
    config = _parse_json_exact(config_bytes)
    if set(config) != {"architecture", "config", "os", "rootfs"} or config["architecture"] != "amd64" or config["os"] != "linux":
        raise OciAuditError("OCI config root is wrong")
    runtime_config = config["config"]
    if not isinstance(runtime_config, Mapping) or set(runtime_config) != {"Cmd", "Entrypoint", "Env", "Labels", "User", "WorkingDir"}:
        raise OciAuditError("OCI execution config is not closed")
    if runtime_config["Entrypoint"] != (ENTRYPOINT,) or runtime_config["Cmd"] != () or runtime_config["User"] != "65532:65532" or runtime_config["WorkingDir"] != "/work" or runtime_config["Env"] != tuple(FIXED_ENV):
        raise OciAuditError("OCI execution config identity is wrong")
    layer_tar = _inflate_stored_gzip(layer_bytes)
    diff_id = _sha256(layer_tar)
    if config["rootfs"] != {"diff_ids": (diff_id,), "type": "layers"}:
        raise OciAuditError("OCI DiffID does not match uncompressed layer")
    entries = _audit_layer_tar(layer_tar)
    forbidden_tools = [path for path in entries if _forbidden_runtime_tool_path(path)]
    if forbidden_tools:
        raise OciAuditError("sealed rootfs contains a compiler, package manager, or SSH surface")
    manifest_entry = entries.get(RUNTIME_MANIFEST.removeprefix("/"))
    if manifest_entry is None or manifest_entry[0] != "regular" or manifest_entry[1] != 0o444:
        raise OciAuditError("runtime manifest is absent or has wrong mode")
    runtime_bytes = bytes(manifest_entry[2])
    runtime_digest = _sha256(runtime_bytes)
    labels = runtime_config["Labels"]
    if labels != {"org.text-to-cad.agent-runtime-manifest.digest": runtime_digest}:
        raise OciAuditError("runtime manifest config label is wrong")
    runtime_manifest = _parse_json_exact(runtime_bytes)
    if runtime_manifest.get("entrypoint", {}).get("argv") != (ENTRYPOINT,):
        raise OciAuditError("runtime manifest entrypoint is wrong")
    for record in runtime_manifest.get("runtimeFiles", ()):
        path = record["path"].removeprefix("/")
        entry = entries.get(path)
        if entry is None or entry[0] != "regular" or entry[1] != record["mode"]:
            raise OciAuditError("runtime file is missing from layer")
        payload = bytes(entry[2])
        if len(payload) != record["bytes"] or _sha256(payload) != record["digest"]:
            raise OciAuditError("runtime file identity differs from layer")
    return {
        "configDigest": config_descriptor["digest"],
        "diffId": diff_id,
        "indexDigest": _sha256(files["index.json"]),
        "layerDigest": layer_descriptor["digest"],
        "layerMediaType": layer_descriptor["mediaType"],
        "manifestDigest": descriptor["digest"],
        "runtimeManifestDigest": runtime_digest,
    }


def derive_spdx_license_catalog(wheel: Path) -> Mapping[str, Any]:
    """Derive the fixed 3.28.0 catalog without importing wheel code."""

    payload = read_exact_regular(wheel, digest=SPDX_WHEEL_DIGEST, size=SPDX_WHEEL_SIZE)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(
                name.startswith("/") or ".." in PurePosixPath(name).parts or "\\" in name
                for name in names
            ):
                raise BuildInputError("SPDX source wheel paths are unsafe")
            source = archive.read("spdx_license_list/__init__.py")
    except (KeyError, OSError, zipfile.BadZipFile) as error:
        raise BuildInputError("SPDX source wheel is malformed") from error
    if _sha256(payload) != SPDX_WHEEL_DIGEST:
        raise BuildInputError("SPDX source wheel changed during derivation")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as error:
        raise BuildInputError("SPDX source module syntax is invalid") from error
    values: dict[str, list[str]] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in ("LICENSES", "EXCEPTIONS")
            and isinstance(node.value, ast.Dict)
        ):
            try:
                keys = [ast.literal_eval(key) for key in node.value.keys]
            except (TypeError, ValueError) as error:
                raise BuildInputError("SPDX catalog key is not a string literal") from error
            if any(not isinstance(key, str) or not key or not key.isascii() for key in keys):
                raise BuildInputError("SPDX catalog key is invalid")
            values[node.target.id] = keys
    if set(values) != {"LICENSES", "EXCEPTIONS"}:
        raise BuildInputError("SPDX source dictionaries are absent or duplicated")
    catalog = {
        "schema": "text-to-cad.spdx-license-catalog/1",
        "licenseListVersion": "3.28.0",
        "licenses": sorted(values["LICENSES"]),
        "exceptions": sorted(values["EXCEPTIONS"]),
    }
    encoded = canonical_json_bytes(catalog)
    if (
        len(catalog["licenses"]) != 727
        or len(catalog["exceptions"]) != 84
        or len(set(catalog["licenses"])) != 727
        or len(set(catalog["exceptions"])) != 84
        or len(encoded) != SPDX_CATALOG_SIZE
        or _sha256(encoded) != SPDX_CATALOG_DIGEST
    ):
        raise BuildInputError("derived SPDX catalog identity is wrong")
    return catalog


def produce_external_artifacts(
    layout: Path,
    *,
    agent_manifest_digest: str,
    license_catalog: Mapping[str, Any],
    package_inventory: list[Mapping[str, Any]] | None = None,
    development_test_only: bool = False,
) -> dict[str, Mapping[str, Any]]:
    """Create raw post-manifest Development artifacts outside the image.

    The production caller must first bind the reviewed 3.28.0 catalog identity;
    this low-level producer accepts an explicit catalog so tests never consult an
    ambient registry or the network.
    """

    audit = audit_oci_layout(layout)
    if audit["manifestDigest"] != agent_manifest_digest:
        raise OciAuditError("external artifact subject does not match final manifest")
    if set(license_catalog) != {"schema", "licenseListVersion", "licenses", "exceptions"}:
        raise BuildInputError("SPDX license catalog schema is not closed")
    if license_catalog["schema"] != "text-to-cad.spdx-license-catalog/1" or license_catalog["licenseListVersion"] != "3.28.0":
        raise BuildInputError("SPDX license catalog identity is wrong")
    catalog_bytes = canonical_json_bytes(license_catalog)
    if not development_test_only and (
        len(catalog_bytes) != SPDX_CATALOG_SIZE
        or _sha256(catalog_bytes) != SPDX_CATALOG_DIGEST
        or len(license_catalog["licenses"]) != 727
        or len(license_catalog["exceptions"]) != 84
    ):
        raise BuildInputError("SPDX license catalog is not the admitted 3.28.0 object")
    if "MIT" not in license_catalog["licenses"]:
        raise BuildInputError("test payload license is absent from catalog")
    manifest_hex = agent_manifest_digest.removeprefix(_DIGEST_PREFIX)
    catalog_digest = canonical_json_digest(license_catalog)
    entries = _layout_rootfs_entries(layout)
    runtime_entry = entries[RUNTIME_MANIFEST.removeprefix("/")]
    runtime_value = _parse_json_exact(bytes(runtime_entry[2]))
    for record in runtime_value["runtimeFiles"]:
        entry = entries.get(record["path"].removeprefix("/"))
        if entry is None or entry[0] != "regular" or len(bytes(entry[2])) != record["bytes"] or _sha256(bytes(entry[2])) != record["digest"]:
            raise BuildInputError("runtime manifest file is absent from SBOM rootfs closure")
    spdx_files = []
    for relative, entry in sorted(entries.items()):
        if entry[0] != "regular":
            continue
        path = "/" + relative
        payload = bytes(entry[2])
        path_id = hashlib.sha256(path.encode("ascii")).hexdigest()
        spdx_files.append(
            {
                "SPDXID": f"SPDXRef-File-{path_id}",
                "checksums": [{"algorithm": "SHA256", "checksumValue": hashlib.sha256(payload).hexdigest()}],
                "copyrightText": "NOASSERTION",
                "fileName": path,
                "fileTypes": ["BINARY"] if payload.startswith(b"\x7fELF") else ["SOURCE"],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
            }
        )
    spdx_packages = []
    seen_package_ids: set[str] = set()
    for item in package_inventory or []:
        if not isinstance(item, Mapping) or set(item) != {"digest", "fileName", "name", "version"}:
            raise BuildInputError("SBOM package inventory record is not closed")
        name = _runtime_text(item["name"], "package.name")
        version = _runtime_text(item["version"], "package.version")
        digest = _runtime_digest(item["digest"], "package.digest")
        file_name = _runtime_text(item["fileName"], "package.fileName")
        package_id = "SPDXRef-Package-" + hashlib.sha256(
            f"{name}\0{version}\0{digest}".encode("ascii")
        ).hexdigest()
        if package_id in seen_package_ids:
            raise BuildInputError("SBOM package inventory contains a duplicate")
        seen_package_ids.add(package_id)
        spdx_packages.append({
            "SPDXID": package_id,
            "checksums": [{"algorithm": "SHA256", "checksumValue": digest[7:]}],
            "copyrightText": "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "name": name,
            "packageFileName": file_name,
            "supplier": "NOASSERTION",
            "versionInfo": version,
        })
    spdx_packages.sort(key=lambda item: (item["name"], item["versionInfo"], item["SPDXID"]))
    if not development_test_only and not spdx_packages:
        raise BuildInputError("production SBOM package inventory is empty")
    sbom = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "comment": f"agentImageManifestDigest={agent_manifest_digest};catalogDigest={catalog_digest}",
            "created": "1970-01-01T00:00:00Z",
            "creators": ["Tool: text-to-cad-agent-runtime-builder/1"],
            "licenseListVersion": "3.28.0",
        },
        "dataLicense": "CC0-1.0",
        "documentNamespace": f"https://text-to-cad.invalid/spdx/agent-runtime/sha256-{manifest_hex}",
        "files": spdx_files,
        "hasExtractedLicensingInfos": [],
        "name": f"text-to-cad-agent-runtime-sha256-{manifest_hex}",
        "packages": spdx_packages,
        "relationships": [
            {
                "relatedSpdxElement": record["SPDXID"],
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
            }
            for record in (*spdx_files, *spdx_packages)
        ],
        "spdxVersion": "SPDX-2.3",
    }
    sbom_bytes = spdx_json_bytes(sbom)
    browser_inventory = _scan_browser_entries(entries, agent_manifest_digest)
    browser_receipt = {
        "agentImageManifestDigest": agent_manifest_digest,
        "browserFindingCount": len(browser_inventory["findings"]),
        "categoryCounts": browser_inventory["categoryCounts"],
        "inventoryDigest": canonical_json_digest(browser_inventory),
        "policyDigest": browser_inventory["policyDigest"],
        "result": "accepted" if not browser_inventory["findings"] else "rejected",
        "scanClosure": browser_inventory["scanClosure"],
        "scannerDigest": browser_inventory["scannerDigest"],
        "schema": "text-to-cad.agent-runtime-browser-scan-receipt/1",
    }
    artifacts = {
        "sbom": sbom,
        "browserInventory": browser_inventory,
        "browserScanReceipt": browser_receipt,
    }
    layout_descriptor = os.open(
        layout,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    stage_name = f".artifacts.stage-{os.getpid()}-{secrets.token_hex(8)}"
    stage = layout / stage_name
    try:
        try:
            os.stat("artifacts", dir_fd=layout_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BuildInputError("artifact output already exists")
        os.mkdir(stage_name, 0o700, dir_fd=layout_descriptor)
        stage_descriptor = os.open(
            stage_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=layout_descriptor,
        )
        try:
            for name, payload in (
                ("sbom.spdx.json", sbom_bytes),
                ("browser-inventory.json", canonical_json_bytes(browser_inventory)),
                ("browser-scan-receipt.json", canonical_json_bytes(browser_receipt)),
            ):
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o400,
                    dir_fd=stage_descriptor,
                )
                try:
                    _write_all(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            os.close(stage_descriptor)
        _rename_noreplace(
            stage_name,
            "artifacts",
            source_dir=layout_descriptor,
            destination_dir=layout_descriptor,
        )
    except Exception:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise
    finally:
        os.close(layout_descriptor)
    return artifacts


def _layout_rootfs_entries(layout: Path) -> dict[str, tuple[str, int, bytes | str]]:
    files = directory_bytes(layout)
    index = _parse_json_exact(files["index.json"])
    descriptor = index["manifests"][0]
    manifest = _parse_json_exact(
        files["blobs/sha256/" + descriptor["digest"].removeprefix(_DIGEST_PREFIX)]
    )
    layer = manifest["layers"][0]
    payload = files["blobs/sha256/" + layer["digest"].removeprefix(_DIGEST_PREFIX)]
    return _audit_layer_tar(_inflate_stored_gzip(payload))


def _scan_browser_entries(
    entries: Mapping[str, tuple[str, int, bytes | str]],
    agent_manifest_digest: str,
) -> Mapping[str, Any]:
    policy_path = Path(__file__).with_name("browser-deny-policy.json")
    policy_bytes = policy_path.read_bytes()
    try:
        policy = parse_canonical_json(policy_bytes)
    except (TypeError, ValueError) as error:
        raise BuildInputError("browser-deny policy is not canonical") from error
    expected_policy_keys = {
        "schema", "cacheMarkers", "candidateRoots", "namePattern",
        "nonBrowserExecutableDigests", "packageMarkers", "productMarkers"
    }
    if not isinstance(policy, Mapping) or set(policy) != expected_policy_keys or policy["schema"] != "text-to-cad.agent-runtime-browser-deny-policy/1":
        raise BuildInputError("browser-deny policy schema is not closed")
    for key in ("cacheMarkers", "candidateRoots", "nonBrowserExecutableDigests", "packageMarkers", "productMarkers"):
        values = policy[key]
        if (
            not isinstance(values, tuple)
            or any(not isinstance(item, str) or not item or not item.isascii() for item in values)
            or len(values) != len(set(values))
        ):
            raise BuildInputError("browser-deny policy array is invalid")
    candidate_roots = tuple(_container_path(item) for item in policy["candidateRoots"])
    name_pattern = re.compile(policy["namePattern"], re.IGNORECASE)
    counts = {key: 0 for key in ("cache", "elfMarker", "executable", "package", "playwright", "productMarker")}
    findings: list[dict[str, Any]] = []
    scanned_paths: list[str] = []
    regular_count = 0
    symlink_count = 0

    def add(category: str, match_kind: str, path: str, rule_id: str, digest: str) -> None:
        key = (category, path, rule_id, digest)
        if any((item["category"], item["path"], item["ruleId"], item["targetDigest"]) == key for item in findings):
            return
        counts[category] += 1
        findings.append(
            {"category": category, "matchKind": match_kind, "path": path, "ruleId": rule_id, "targetDigest": digest}
        )

    for relative in sorted(entries):
        kind, _mode, value = entries[relative]
        path = "/" + relative
        scanned_paths.append(path)
        for index, root in enumerate(candidate_roots):
            if path == root or path.startswith(root + "/"):
                target = bytes(value) if kind == "regular" else canonical_json_bytes(
                    {"kind": kind, "path": path, "target": str(value)}
                )
                add("cache", "candidate-root", path, f"candidate-root-{index}", _sha256(target))
        if kind == "symlink":
            symlink_count += 1
            name = PurePosixPath(relative).name
            if name_pattern.fullmatch(name):
                add("playwright", "symlink-name", path, "browser-name-pattern", _sha256(str(value).encode("ascii")))
            continue
        if kind != "regular":
            continue
        regular_count += 1
        payload = bytes(value)
        digest = _sha256(payload)
        name = PurePosixPath(relative).name
        name_match = name_pattern.fullmatch(name) is not None
        if name_match:
            add("executable", "file-name", path, "browser-name-pattern", digest)
            if payload.startswith(b"\x7fELF"):
                add("elfMarker", "elf-and-browser-name", path, "browser-elf-name", digest)
        lowered_parts = tuple(part.lower() for part in PurePosixPath(relative).parts)
        if "ms-playwright" in lowered_parts or ("cache" in lowered_parts and name in ("metadata", "package.json")):
            add("cache", "cache-path", path, "browser-cache-path", digest)
        package_found = False
        if name in ("METADATA", "package.json"):
            for index, marker in enumerate(policy["packageMarkers"]):
                if marker.encode("ascii").lower() in payload.lower():
                    add("package", "package-marker", path, f"package-marker-{index}", digest)
                    package_found = True
        if package_found or (name_match and "playwright" in name.lower()):
            add("playwright", "package-or-name", path, "playwright-authority", digest)
        executable_or_elf = bool(_mode & 0o111) or payload.startswith(b"\x7fELF")
        if executable_or_elf and digest not in policy["nonBrowserExecutableDigests"]:
            product_found = False
            for index, marker in enumerate(policy["productMarkers"]):
                if marker.encode("ascii").lower() in payload.lower():
                    add("productMarker", "product-marker", path, f"product-marker-{index}", digest)
                    product_found = True
            if product_found:
                add("executable", "product-marker", path, "browser-product-executable", digest)
                if payload.startswith(b"\x7fELF"):
                    add("elfMarker", "elf-product-marker", path, "browser-product-elf", digest)
        if "cache" in lowered_parts:
            for index, marker in enumerate(policy["cacheMarkers"]):
                if marker.encode("ascii").lower() in payload.lower():
                    add("cache", "cache-marker", path, f"cache-marker-{index}", digest)
    findings.sort(key=lambda item: (item["category"], item["path"], item["ruleId"], item["targetDigest"]))
    scanner_bytes = Path(__file__).read_bytes()
    return {
        "agentImageManifestDigest": agent_manifest_digest,
        "categoryCounts": counts,
        "findings": findings,
        "policyDigest": canonical_json_digest(policy),
        "scanClosure": {
            "rootfsRegularFileCount": regular_count,
            "rootfsSymlinkCount": symlink_count,
            "scannedPathSetDigest": canonical_json_digest(scanned_paths),
            "uninspectableCount": 0,
        },
        "scannerDigest": _sha256(scanner_bytes),
        "schema": "text-to-cad.agent-runtime-browser-inventory/1",
    }
