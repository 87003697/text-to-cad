"""Closed Execution Source Snapshot construction and publication contracts."""

from __future__ import annotations

import errno
import hashlib
import io
import os
import re
import stat
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .canonical_json import (
    EvidenceError,
    canonical_json_bytes,
    canonical_json_digest,
    parse_canonical_json,
)


class SourceSnapshotError(EvidenceError):
    """A Source Snapshot violates its closed identity or operation contract."""


class SourceSnapshotPublicationError(SourceSnapshotError):
    """A publication adapter failed with an explicit possible-write state."""

    def __init__(self, message: str, *, stage: str, may_have_written: bool) -> None:
        super().__init__(message)
        self.stage = stage
        self.may_have_written = may_have_written
        self.retry_allowed = False


class SourceSnapshotStore(Protocol):
    """Minimal exact-version object-store adapter used by the publisher."""

    def versioning_status(self, bucket: str) -> str: ...

    def current_version(self, bucket: str, key: str) -> tuple[str, str] | None: ...

    def put_create_only(self, bucket: str, key: str, payload: bytes) -> tuple[str, str]: ...

    def get_exact_version(self, bucket: str, key: str, version_id: str) -> bytes: ...


@dataclass(frozen=True)
class SourceSnapshotBuild:
    """Locally closed manifest and deterministic payload, before publication."""

    manifest: Mapping[str, Any]
    manifest_bytes: bytes
    manifest_digest: str
    payload: bytes
    payload_sha256: str
    payload_bytes: int

    def __post_init__(self) -> None:
        try:
            frozen = parse_canonical_json(canonical_json_bytes(self.manifest))
        except EvidenceError as exc:
            raise SourceSnapshotError("snapshot build manifest is not canonical JSON") from exc
        if not isinstance(frozen, Mapping):
            raise SourceSnapshotError("snapshot build manifest must be an object")
        try:
            manifest_bytes = bytes(self.manifest_bytes)
            payload = bytes(self.payload)
        except (TypeError, ValueError) as exc:
            raise SourceSnapshotError("snapshot build byte fields are invalid") from exc
        object.__setattr__(self, "manifest", frozen)
        object.__setattr__(self, "manifest_bytes", manifest_bytes)
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True)
class _GitEntryProof:
    path: str
    mode: str
    size: int
    sha256: str
    storage: str


@dataclass(frozen=True)
class _GitSourceProof:
    commit: str
    entries: tuple[_GitEntryProof, ...]


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9._~+/=-]+\Z")
_ETAG_RE = re.compile(r'"[ -!#-~]+"\Z')
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_REGION_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+\Z")
_SCHEMAS = {
    "manifest": "text-to-cad.source-manifest/1",
    "publication": "text-to-cad.source-snapshot-publication/1",
    "visibility": "text-to-cad.source-snapshot-visibility/1",
    "lock": "text-to-cad.source-snapshot-lock/1",
}
_PAYLOAD_FORMAT = "tar-pax-v1"
_NORMALIZATION = "text-to-cad.source-normalization/1"
_CLEAN_POLICY = "clean-exact-commit"
_SYMLINK_POLICY = "reject"


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _require_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise SourceSnapshotError(f"{label} has unexpected keys")


def _require_ascii(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise SourceSnapshotError(f"{label} must be nonempty ASCII")
    return value


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise SourceSnapshotError(f"{label} must be a canonical SHA-256 digest")
    return value


def _require_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**63:
        raise SourceSnapshotError(f"{label} must be a nonnegative signed 64-bit integer")
    return value


def _canonical_path(raw: Any, label: str) -> str:
    path = _require_ascii(raw, label)
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or "\\" in path
        or path.endswith("/")
        or "//" in path
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in path)
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != path
    ):
        raise SourceSnapshotError(f"{label} is not a canonical relative path")
    return path


def _canonical_path_array(value: Any, label: str, *, nonempty: bool) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SourceSnapshotError(f"{label} must be an array")
    paths = tuple(_canonical_path(item, f"{label} item") for item in value)
    if nonempty and not paths:
        raise SourceSnapshotError(f"{label} must not be empty")
    if tuple(sorted(paths)) != paths or len(set(paths)) != len(paths):
        raise SourceSnapshotError(f"{label} must be sorted and unique")
    return paths


def _paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _validate_files(value: Any, *, path_count: int, total_bytes: int) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SourceSnapshotError("files must be an array")
    files: list[Mapping[str, Any]] = []
    paths: list[str] = []
    observed_bytes = 0
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise SourceSnapshotError("file entry must be an object")
        _require_keys(item, {"mode", "path", "sha256", "size", "type"}, "file entry")
        if item["type"] != "regular":
            raise SourceSnapshotError("file type must be regular")
        path = _canonical_path(item["path"], f"files[{index}].path")
        if item["mode"] not in {"0644", "0755"}:
            raise SourceSnapshotError("file mode must be 0644 or 0755")
        size = _require_count(item["size"], f"files[{index}].size")
        _require_digest(item["sha256"], f"files[{index}].sha256")
        paths.append(path)
        observed_bytes += size
        files.append(item)
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        raise SourceSnapshotError("file paths must be sorted and unique")
    if len(files) != path_count or observed_bytes != total_bytes:
        raise SourceSnapshotError("file entries disagree with aggregate counts")
    return tuple(files)


def _validate_manifest(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "cleanPolicy",
            "exclusions",
            "files",
            "gitCommit",
            "includePaths",
            "normalizationVersion",
            "pathCount",
            "schema",
            "symlinkPolicy",
            "totalBytes",
        },
        "source manifest",
    )
    if value["schema"] != _SCHEMAS["manifest"]:
        raise SourceSnapshotError("source manifest schema is invalid")
    if value["normalizationVersion"] != _NORMALIZATION:
        raise SourceSnapshotError("normalization version is invalid")
    if value["cleanPolicy"] != _CLEAN_POLICY or value["symlinkPolicy"] != _SYMLINK_POLICY:
        raise SourceSnapshotError("source policy is not closed")
    if not isinstance(value["gitCommit"], str) or _GIT_COMMIT_RE.fullmatch(value["gitCommit"]) is None:
        raise SourceSnapshotError("Git commit must be 40 lowercase hexadecimal characters")
    include_paths = _canonical_path_array(value["includePaths"], "includePaths", nonempty=True)
    exclusions = _canonical_path_array(value["exclusions"], "exclusions", nonempty=False)
    if any(_paths_overlap(included, excluded) for included in include_paths for excluded in exclusions):
        raise SourceSnapshotError("included and excluded paths overlap")
    count = _require_count(value["pathCount"], "pathCount")
    total = _require_count(value["totalBytes"], "totalBytes")
    files = _validate_files(value["files"], path_count=count, total_bytes=total)
    if tuple(item["path"] for item in files) != include_paths:
        raise SourceSnapshotError("includePaths must exactly equal the closed file path set")


def _validate_publication(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "bucket",
            "disposition",
            "etag",
            "exactVersionReread",
            "key",
            "payloadBytes",
            "payloadFormat",
            "payloadSha256",
            "region",
            "schema",
            "sourceManifestDigest",
            "versionId",
        },
        "source publication receipt",
    )
    if value["schema"] != _SCHEMAS["publication"] or value["payloadFormat"] != _PAYLOAD_FORMAT:
        raise SourceSnapshotError("source publication literals are invalid")
    _require_digest(value["sourceManifestDigest"], "sourceManifestDigest")
    payload_digest = _require_digest(value["payloadSha256"], "payloadSha256")
    _require_count(value["payloadBytes"], "payloadBytes")
    bucket = _require_ascii(value["bucket"], "bucket")
    region = _require_ascii(value["region"], "region")
    key = _require_ascii(value["key"], "key")
    if (
        _BUCKET_RE.fullmatch(bucket) is None
        or _REGION_RE.fullmatch(region) is None
        or not _content_key_matches(key, payload_digest)
    ):
        raise SourceSnapshotError("publication locator is not content-addressed")
    if not isinstance(value["versionId"], str) or _VERSION_RE.fullmatch(value["versionId"]) is None:
        raise SourceSnapshotError("versionId is invalid")
    if not isinstance(value["etag"], str) or _ETAG_RE.fullmatch(value["etag"]) is None:
        raise SourceSnapshotError("etag is invalid")
    if value["disposition"] not in {"created", "exact-reuse"} or value["exactVersionReread"] is not True:
        raise SourceSnapshotError("publication result is not terminal exact-version evidence")


def _validate_visibility(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "bucket",
            "key",
            "macMountVisible",
            "payloadBytes",
            "payloadSha256",
            "publicationReceiptDigest",
            "region",
            "s3ExactVersionVisible",
            "schema",
            "sourceManifestDigest",
            "versionId",
        },
        "source visibility receipt",
    )
    if value["schema"] != _SCHEMAS["visibility"]:
        raise SourceSnapshotError("source visibility schema is invalid")
    for field in ("sourceManifestDigest", "payloadSha256", "publicationReceiptDigest"):
        _require_digest(value[field], field)
    _require_count(value["payloadBytes"], "payloadBytes")
    bucket = _require_ascii(value["bucket"], "bucket")
    region = _require_ascii(value["region"], "region")
    if _BUCKET_RE.fullmatch(bucket) is None or _REGION_RE.fullmatch(region) is None:
        raise SourceSnapshotError("visibility locator is invalid")
    key = _require_ascii(value["key"], "key")
    if not _content_key_matches(key, value["payloadSha256"]):
        raise SourceSnapshotError("visibility key is not content-addressed")
    if not isinstance(value["versionId"], str) or _VERSION_RE.fullmatch(value["versionId"]) is None:
        raise SourceSnapshotError("versionId is invalid")
    if value["s3ExactVersionVisible"] is not True or value["macMountVisible"] is not True:
        raise SourceSnapshotError("visibility receipt cannot represent an incomplete check")


def _validate_lock(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "bucket",
            "cleanPolicy",
            "exclusions",
            "files",
            "gitCommit",
            "includePaths",
            "key",
            "normalizationVersion",
            "pathCount",
            "payloadBytes",
            "payloadFormat",
            "payloadSha256",
            "publicationReceiptDigest",
            "region",
            "schema",
            "sourceManifestDigest",
            "symlinkPolicy",
            "totalBytes",
            "versionId",
            "visibilityReceiptDigest",
        },
        "source snapshot lock",
    )
    if value["schema"] != _SCHEMAS["lock"] or value["payloadFormat"] != _PAYLOAD_FORMAT:
        raise SourceSnapshotError("source snapshot lock literals are invalid")
    for field in ("sourceManifestDigest", "payloadSha256", "publicationReceiptDigest", "visibilityReceiptDigest"):
        _require_digest(value[field], field)
    publication_projection = {
        "bucket": value["bucket"],
        "key": value["key"],
        "payloadBytes": value["payloadBytes"],
        "payloadSha256": value["payloadSha256"],
        "region": value["region"],
        "versionId": value["versionId"],
    }
    for field, item in publication_projection.items():
        _require_ascii(item, field) if field not in {"payloadBytes", "payloadSha256"} else None
    _require_count(value["payloadBytes"], "payloadBytes")
    _require_digest(value["payloadSha256"], "payloadSha256")
    if not _content_key_matches(value["key"], value["payloadSha256"]):
        raise SourceSnapshotError("lock key is not content-addressed")
    if _BUCKET_RE.fullmatch(value["bucket"]) is None or _REGION_RE.fullmatch(value["region"]) is None:
        raise SourceSnapshotError("lock locator is invalid")
    if _VERSION_RE.fullmatch(value["versionId"]) is None:
        raise SourceSnapshotError("lock versionId is invalid")
    manifest_projection = {
        "cleanPolicy": value["cleanPolicy"],
        "exclusions": value["exclusions"],
        "files": value["files"],
        "gitCommit": value["gitCommit"],
        "includePaths": value["includePaths"],
        "normalizationVersion": value["normalizationVersion"],
        "pathCount": value["pathCount"],
        "schema": _SCHEMAS["manifest"],
        "symlinkPolicy": value["symlinkPolicy"],
        "totalBytes": value["totalBytes"],
    }
    _validate_manifest(manifest_projection)
    if canonical_json_digest(manifest_projection) != value["sourceManifestDigest"]:
        raise SourceSnapshotError("lock source manifest digest does not match embedded manifest")


_VALIDATORS = {
    "manifest": _validate_manifest,
    "publication": _validate_publication,
    "visibility": _validate_visibility,
    "lock": _validate_lock,
}


def parse_source_snapshot_document(kind: str, payload: bytes) -> Mapping[str, Any]:
    """Parse canonical bytes, then validate one producer-owned closed schema."""

    if kind not in _VALIDATORS:
        raise SourceSnapshotError("unknown Source Snapshot document kind")
    try:
        value = parse_canonical_json(payload)
    except EvidenceError as exc:
        raise SourceSnapshotError(str(exc)) from exc
    if not isinstance(value, Mapping):
        raise SourceSnapshotError("Source Snapshot document must be an object")
    _VALIDATORS[kind](value)
    return value


def _typed(kind: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
    _VALIDATORS[kind](value)
    return parse_source_snapshot_document(kind, canonical_json_bytes(value))


def _default_git_verifier(root: Path, include_paths: tuple[str, ...]) -> _GitSourceProof:
    def run(arguments: list[str]) -> bytes:
        try:
            result = subprocess.run(
                ["git", "-C", os.fspath(root), *arguments],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SourceSnapshotError("Git clean/exact-commit verification failed") from exc
        return result.stdout

    commit = run(["rev-parse", "--verify", "HEAD^{commit}"]).decode("ascii").strip()
    if _GIT_COMMIT_RE.fullmatch(commit) is None:
        raise SourceSnapshotError("Git HEAD is not an exact SHA-1 commit identity")
    status = run(["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if status:
        raise SourceSnapshotError("source worktree is not clean at the exact Git commit")
    raw_entries = run(
        ["ls-tree", "-r", "-z", "--full-tree", commit, "--", *include_paths]
    ).split(b"\0")
    tree_entries: dict[str, tuple[str, str]] = {}
    for raw in raw_entries:
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            path = raw_path.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise SourceSnapshotError("Git tree entry is malformed or non-ASCII") from exc
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise SourceSnapshotError("included Git entry is not a regular 100644/100755 blob")
        if path in tree_entries:
            raise SourceSnapshotError("Git tree contains a duplicate included path")
        tree_entries[path] = (mode, object_id)
    if tuple(sorted(tree_entries)) != include_paths:
        raise SourceSnapshotError("includePaths are not exactly regular files in the Git commit")

    proofs: list[_GitEntryProof] = []
    for path in include_paths:
        mode, object_id = tree_entries[path]
        blob = run(["cat-file", "blob", object_id])
        attributes = run(["check-attr", "--cached", "-z", "filter", "--", path]).split(b"\0")
        if len(attributes) != 4 or attributes[0].decode("ascii") != path or attributes[1] != b"filter":
            raise SourceSnapshotError("Git filter attribute response is malformed")
        filter_value = attributes[2]
        if filter_value == b"lfs":
            match = re.fullmatch(
                rb"version https://git-lfs.github.com/spec/v1\n"
                rb"oid sha256:([0-9a-f]{64})\n"
                rb"size ([0-9]+)\n",
                blob,
            )
            if match is None:
                raise SourceSnapshotError("Git LFS blob is not an exact canonical pointer")
            size = int(match.group(2))
            if not 0 <= size < 2**63:
                raise SourceSnapshotError("Git LFS pointer size is outside the closed range")
            digest_value = "sha256:" + match.group(1).decode("ascii")
            storage = "git-lfs"
        else:
            size = len(blob)
            digest_value = _sha256(blob)
            storage = "git-blob"
        proofs.append(
            _GitEntryProof(
                path=path,
                mode="0755" if mode == "100755" else "0644",
                size=size,
                sha256=digest_value,
                storage=storage,
            )
        )
    return _GitSourceProof(commit=commit, entries=tuple(proofs))


def _read_regular_file(
    root_fd: int,
    relative_path: str,
    after_read: Callable[[str, int], None] | None,
) -> tuple[bytes, os.stat_result]:
    parts = relative_path.split("/")
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            try:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current_fd)
            except OSError as exc:
                raise SourceSnapshotError("source path escapes, races, or crosses a symlink") from exc
            os.close(current_fd)
            current_fd = next_fd
        try:
            observed = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        except OSError as exc:
            raise SourceSnapshotError("selected source path cannot be observed") from exc
        if stat.S_ISLNK(observed.st_mode):
            raise SourceSnapshotError("selected source path is a symlink")
        if not stat.S_ISREG(observed.st_mode):
            raise SourceSnapshotError("selected source path is not a regular file")
        try:
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current_fd)
        except OSError as exc:
            raise SourceSnapshotError("selected source changed before no-follow open") from exc
        try:
            before = os.fstat(file_fd)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            if after_read is not None:
                after_read(relative_path, file_fd)
            after = os.fstat(file_fd)
            try:
                path_after = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
            except OSError as exc:
                raise SourceSnapshotError("selected source changed while reading") from exc
        finally:
            os.close(file_fd)
        identity = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if (
            identity(before) != identity(after)
            or identity(observed) != identity(before)
            or identity(path_after) != identity(before)
        ):
            raise SourceSnapshotError("selected source changed while reading")
        return b"".join(chunks), before
    finally:
        os.close(current_fd)


def _enumerate_regular_paths(directory_fd: int, prefix: str = "") -> tuple[str, ...]:
    paths: list[str] = []
    try:
        entries = sorted(os.scandir(directory_fd), key=lambda entry: entry.name)
    except OSError as exc:
        raise SourceSnapshotError("mounted source tree cannot be enumerated") from exc
    for entry in entries:
        path = f"{prefix}/{entry.name}" if prefix else entry.name
        _canonical_path(path, "mounted source path")
        try:
            observed = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise SourceSnapshotError("mounted source path changed during enumeration") from exc
        if stat.S_ISLNK(observed.st_mode):
            raise SourceSnapshotError("mounted source tree contains a symlink")
        if stat.S_ISREG(observed.st_mode):
            paths.append(path)
            continue
        if not stat.S_ISDIR(observed.st_mode):
            raise SourceSnapshotError("mounted source tree contains a special file")
        try:
            child_fd = os.open(
                entry.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise SourceSnapshotError("mounted source directory changed or became a symlink") from exc
        try:
            paths.extend(_enumerate_regular_paths(child_fd, path))
        finally:
            os.close(child_fd)
    return tuple(paths)


def _make_payload(files: Sequence[tuple[str, bytes, str]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path, payload, mode in files:
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            info.mode = int(mode, 8)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def _tar_octal(field: bytes, label: str) -> int:
    raw = field.rstrip(b"\0 ").lstrip(b"0") or b"0"
    if any(byte not in b"01234567" for byte in raw):
        raise SourceSnapshotError(f"tar {label} is not canonical octal")
    return int(raw, 8)


def _tar_text(field: bytes, label: str) -> str:
    raw = field.split(b"\0", 1)[0]
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SourceSnapshotError(f"tar {label} is not ASCII") from exc
    if field != raw + bytes(len(field) - len(raw)):
        raise SourceSnapshotError(f"tar {label} has bytes after its first terminator")
    return value


def _tar_octal_bytes(value: int, width: int) -> bytes:
    rendered = f"{value:0{width - 1}o}".encode("ascii") + b"\0"
    if len(rendered) != width:
        raise SourceSnapshotError("tar numeric field exceeds its closed width")
    return rendered


def _parse_pax_path(payload: bytes) -> str:
    cursor = 0
    path: str | None = None
    while cursor < len(payload):
        space = payload.find(b" ", cursor)
        if space <= cursor:
            raise SourceSnapshotError("PAX record length is malformed")
        raw_length = payload[cursor:space]
        if not raw_length.isdigit() or raw_length.startswith(b"0"):
            raise SourceSnapshotError("PAX record length is not canonical decimal")
        length = int(raw_length)
        end = cursor + length
        if end > len(payload) or payload[end - 1 : end] != b"\n":
            raise SourceSnapshotError("PAX record boundary is malformed")
        record = payload[space + 1 : end - 1]
        if not record.startswith(b"path=") or path is not None:
            raise SourceSnapshotError("PAX headers may contain exactly one path record")
        try:
            path = record[5:].decode("ascii")
        except UnicodeDecodeError as exc:
            raise SourceSnapshotError("PAX path is not ASCII") from exc
        cursor = end
    if path is None:
        raise SourceSnapshotError("PAX header does not establish a path")
    return _canonical_path(path, "PAX path")


def _inspect_tar_payload(payload: bytes) -> tuple[tuple[str, bytes, str], ...]:
    """Independently parse the exact tar/PAX bytes emitted by this contract."""

    if not isinstance(payload, bytes) or len(payload) < 10240 or len(payload) % 10240:
        raise SourceSnapshotError("tar-pax-v1 payload has invalid block padding")
    cursor = 0
    pending_path: str | None = None
    entries: list[tuple[str, bytes, str]] = []
    while cursor + 512 <= len(payload):
        header = payload[cursor : cursor + 512]
        if header == bytes(512):
            if pending_path is not None or any(payload[cursor:]):
                raise SourceSnapshotError("tar-pax-v1 terminal padding is malformed")
            if len(payload) - cursor < 1024:
                raise SourceSnapshotError("tar-pax-v1 lacks two terminal zero blocks")
            return tuple(entries)
        stored_checksum = _tar_octal(header[148:156], "checksum")
        checksum_header = header[:148] + b"        " + header[156:]
        if (
            sum(checksum_header) != stored_checksum
            or header[148:156] != f"{stored_checksum:06o}".encode("ascii") + b"\0 "
        ):
            raise SourceSnapshotError("tar header checksum mismatch")
        mode = _tar_octal(header[100:108], "mode")
        uid = _tar_octal(header[108:116], "uid")
        gid = _tar_octal(header[116:124], "gid")
        size = _tar_octal(header[124:136], "size")
        mtime = _tar_octal(header[136:148], "mtime")
        type_flag = header[156:157]
        name = _tar_text(header[:100], "name")
        uname = _tar_text(header[265:297], "uname")
        gname = _tar_text(header[297:329], "gname")
        prefix = _tar_text(header[345:500], "prefix")
        if (
            header[257:265] != b"ustar\x0000"
            or header[329:345] != bytes(16)
            or header[100:108] != _tar_octal_bytes(mode, 8)
            or header[108:116] != _tar_octal_bytes(uid, 8)
            or header[116:124] != _tar_octal_bytes(gid, 8)
            or header[124:136] != _tar_octal_bytes(size, 12)
            or header[136:148] != _tar_octal_bytes(mtime, 12)
        ):
            raise SourceSnapshotError("tar header encoding is not canonical ustar")
        if uid != 0 or gid != 0 or mtime != 0 or uname or gname:
            raise SourceSnapshotError("tar ownership or timestamp is not normalized")
        data_start = cursor + 512
        data_end = data_start + size
        padded_end = data_start + ((size + 511) // 512) * 512
        if padded_end > len(payload) or any(payload[data_end:padded_end]):
            raise SourceSnapshotError("tar entry payload or padding is truncated or nonzero")
        data = payload[data_start:data_end]
        if type_flag == b"x":
            if pending_path is not None or name != "././@PaxHeader" or mode != 0 or prefix:
                raise SourceSnapshotError("PAX extended header metadata is not normalized")
            pending_path = _parse_pax_path(data)
        elif type_flag in {b"0", b"\0"}:
            if mode not in {0o644, 0o755}:
                raise SourceSnapshotError("tar regular file mode is not normalized")
            header_path = f"{prefix}/{name}" if prefix else name
            path = pending_path or _canonical_path(header_path, "tar path")
            if pending_path is not None and (prefix or name != path[:100]):
                raise SourceSnapshotError("PAX backing header path is not deterministic")
            entries.append((path, data, f"{mode:04o}"))
            pending_path = None
        else:
            raise SourceSnapshotError("tar-pax-v1 contains a non-regular entry")
        cursor = padded_end
    raise SourceSnapshotError("tar-pax-v1 has no terminal zero blocks")


def _validate_build(built: SourceSnapshotBuild) -> None:
    if not isinstance(built, SourceSnapshotBuild):
        raise SourceSnapshotError("snapshot build must be a typed local result")
    manifest = _typed("manifest", built.manifest)
    if (
        built.manifest_bytes != canonical_json_bytes(manifest)
        or built.manifest_digest != canonical_json_digest(manifest)
        or built.payload_bytes != len(built.payload)
        or built.payload_sha256 != _sha256(built.payload)
    ):
        raise SourceSnapshotError("snapshot build identity fields disagree")
    observed_files = _inspect_tar_payload(built.payload)
    if len(observed_files) != manifest["pathCount"]:
        raise SourceSnapshotError("snapshot payload path count differs from manifest")
    for (path, payload, mode), entry in zip(observed_files, manifest["files"], strict=True):
        if (
            path != entry["path"]
            or mode != entry["mode"]
            or len(payload) != entry["size"]
            or _sha256(payload) != entry["sha256"]
        ):
            raise SourceSnapshotError("snapshot payload bytes or metadata differ from manifest")
    if _make_payload(observed_files) != built.payload:
        raise SourceSnapshotError("snapshot payload encoding is not deterministic tar-pax-v1")


def _build_source_snapshot(
    root: str | os.PathLike[str],
    *,
    include_paths: Sequence[str],
    git_commit: str,
    exclusions: Sequence[str] = (),
    git_verifier: Callable[[Path, tuple[str, ...]], _GitSourceProof] = _default_git_verifier,
    after_git_proof: Callable[[Path], None] | None = None,
    after_read: Callable[[str, int], None] | None = None,
) -> SourceSnapshotBuild:
    """Internal construction seam with injected observers for adversarial tests."""

    normalized = tuple(sorted(_canonical_path(path, "include path") for path in include_paths))
    if not normalized or len(set(normalized)) != len(normalized):
        raise SourceSnapshotError("include_paths must be nonempty and unique")
    normalized_exclusions = tuple(sorted(_canonical_path(path, "exclusion") for path in exclusions))
    if len(set(normalized_exclusions)) != len(normalized_exclusions):
        raise SourceSnapshotError("exclusions must be unique")
    if any(
        _paths_overlap(included, excluded)
        for included in normalized
        for excluded in normalized_exclusions
    ):
        raise SourceSnapshotError("included and excluded paths overlap")
    if not isinstance(git_commit, str) or _GIT_COMMIT_RE.fullmatch(git_commit) is None:
        raise SourceSnapshotError("Git commit must be 40 lowercase hexadecimal characters")
    root_path = Path(root)
    initial_proof = git_verifier(root_path, normalized)
    if not isinstance(initial_proof, _GitSourceProof) or initial_proof.commit != git_commit:
        raise SourceSnapshotError("observed Git commit does not equal requested commit")
    if tuple(entry.path for entry in initial_proof.entries) != normalized:
        raise SourceSnapshotError("Git proof does not cover the exact included path set")
    if after_git_proof is not None:
        after_git_proof(root_path)
    try:
        root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise SourceSnapshotError("source root is not a no-follow directory") from exc
    entries: list[dict[str, Any]] = []
    payload_files: list[tuple[str, bytes, str]] = []
    try:
        for path, git_entry in zip(normalized, initial_proof.entries, strict=True):
            payload, observed = _read_regular_file(root_fd, path, after_read)
            actual_mode = stat.S_IMODE(observed.st_mode)
            if actual_mode not in {0o644, 0o755}:
                raise SourceSnapshotError("selected source mode is not normalized to 0644 or 0755")
            mode = f"{actual_mode:04o}"
            if (
                mode != git_entry.mode
                or len(payload) != git_entry.size
                or _sha256(payload) != git_entry.sha256
            ):
                raise SourceSnapshotError("captured source bytes or mode do not match the Git commit")
            entries.append(
                {
                    "mode": mode,
                    "path": path,
                    "sha256": _sha256(payload),
                    "size": len(payload),
                    "type": "regular",
                }
            )
            payload_files.append((path, payload, mode))
    finally:
        os.close(root_fd)
    final_proof = git_verifier(root_path, normalized)
    if final_proof != initial_proof:
        raise SourceSnapshotError("Git HEAD, clean state, or included tree changed during capture")
    total_bytes = sum(item["size"] for item in entries)
    manifest_value = {
        "cleanPolicy": _CLEAN_POLICY,
        "exclusions": list(normalized_exclusions),
        "files": entries,
        "gitCommit": git_commit,
        "includePaths": list(normalized),
        "normalizationVersion": _NORMALIZATION,
        "pathCount": len(entries),
        "schema": _SCHEMAS["manifest"],
        "symlinkPolicy": _SYMLINK_POLICY,
        "totalBytes": total_bytes,
    }
    manifest = _typed("manifest", manifest_value)
    manifest_bytes = canonical_json_bytes(manifest)
    payload = _make_payload(payload_files)
    result = SourceSnapshotBuild(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_digest=canonical_json_digest(manifest),
        payload=payload,
        payload_sha256=_sha256(payload),
        payload_bytes=len(payload),
    )
    _validate_build(result)
    return result


def build_source_snapshot(
    root: str | os.PathLike[str],
    *,
    include_paths: Sequence[str],
    git_commit: str,
    exclusions: Sequence[str] = (),
) -> SourceSnapshotBuild:
    """Build a clean, exact-commit, regular-file-only deterministic snapshot."""

    return _build_source_snapshot(
        root,
        include_paths=include_paths,
        git_commit=git_commit,
        exclusions=exclusions,
    )


def _content_key_matches(key: str, payload_digest: str) -> bool:
    return (
        key == f"source-snapshots/payloads/sha256/{payload_digest[7:]}.tar"
        and "//" not in key
        and ".." not in PurePosixPath(key).parts
    )


def _validate_publication_request(
    built: SourceSnapshotBuild,
    bucket: str,
    region: str,
    key: str,
) -> None:
    _validate_build(built)
    if (
        not isinstance(bucket, str)
        or _BUCKET_RE.fullmatch(bucket) is None
        or not isinstance(region, str)
        or _REGION_RE.fullmatch(region) is None
        or not isinstance(key, str)
        or not _content_key_matches(key, built.payload_sha256)
    ):
        raise SourceSnapshotError("publication request locator is invalid or not content-addressed")


def _version_response(value: Any, *, stage: str, may_have_written: bool) -> tuple[str, str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise SourceSnapshotPublicationError(
            "object-store version response is malformed",
            stage=stage,
            may_have_written=may_have_written,
        )
    version_id, etag = value
    if (
        not isinstance(version_id, str)
        or _VERSION_RE.fullmatch(version_id) is None
        or not isinstance(etag, str)
        or _ETAG_RE.fullmatch(etag) is None
    ):
        raise SourceSnapshotPublicationError(
            "object-store version identity is malformed",
            stage=stage,
            may_have_written=may_have_written,
        )
    return version_id, etag


def _exact_payload(built: SourceSnapshotBuild, observed: bytes, label: str) -> None:
    if (
        not isinstance(observed, bytes)
        or len(observed) != built.payload_bytes
        or _sha256(observed) != built.payload_sha256
        or observed != built.payload
    ):
        raise SourceSnapshotError(f"{label} exact reread does not match payload")


def publish_source_snapshot(
    built: SourceSnapshotBuild,
    *,
    store: SourceSnapshotStore,
    bucket: str,
    region: str,
    key: str,
) -> Mapping[str, Any]:
    """Create once or exactly reuse, then reread the selected immutable version."""

    _validate_publication_request(built, bucket, region, key)
    try:
        versioning = store.versioning_status(bucket)
    except Exception as exc:
        raise SourceSnapshotPublicationError(
            "bucket versioning preflight failed",
            stage="versioning-preflight",
            may_have_written=False,
        ) from exc
    if versioning != "Enabled":
        raise SourceSnapshotError("bucket versioning must be Enabled before publication")
    try:
        existing = store.current_version(bucket, key)
    except Exception as exc:
        raise SourceSnapshotPublicationError(
            "content-addressed key preflight failed",
            stage="key-preflight",
            may_have_written=False,
        ) from exc
    if existing is None:
        try:
            response = store.put_create_only(bucket, key, built.payload)
        except Exception as exc:
            raise SourceSnapshotPublicationError(
                "create-only publication failed or has an unresolved response",
                stage="create-only-put",
                may_have_written=True,
            ) from exc
        version_id, etag = _version_response(
            response,
            stage="create-only-put-response",
            may_have_written=True,
        )
        disposition = "created"
        may_have_written = True
    else:
        version_id, etag = _version_response(
            existing,
            stage="existing-version-response",
            may_have_written=False,
        )
        disposition = "exact-reuse"
        may_have_written = False
    try:
        observed = store.get_exact_version(bucket, key, version_id)
    except Exception as exc:
        raise SourceSnapshotPublicationError(
            "S3 exact-version reread failed",
            stage="exact-version-reread",
            may_have_written=may_have_written,
        ) from exc
    try:
        _exact_payload(built, observed, "S3 exact-version")
        return _typed(
            "publication",
            {
                "bucket": bucket,
                "disposition": disposition,
                "etag": etag,
                "exactVersionReread": True,
                "key": key,
                "payloadBytes": built.payload_bytes,
                "payloadFormat": _PAYLOAD_FORMAT,
                "payloadSha256": built.payload_sha256,
                "region": region,
                "schema": _SCHEMAS["publication"],
                "sourceManifestDigest": built.manifest_digest,
                "versionId": version_id,
            },
        )
    except SourceSnapshotError as exc:
        raise SourceSnapshotPublicationError(
            "published version failed exact terminal validation",
            stage="exact-version-validation",
            may_have_written=may_have_written,
        ) from exc


def verify_source_snapshot_visibility(
    built: SourceSnapshotBuild,
    publication: Mapping[str, Any],
    *,
    store: SourceSnapshotStore,
    mac_reader: Callable[[str], bytes],
) -> Mapping[str, Any]:
    """Prove the exact version and the Mac-mounted object expose identical bytes."""

    _validate_build(built)
    publication = _typed("publication", publication)
    if (
        publication["sourceManifestDigest"] != built.manifest_digest
        or publication["payloadSha256"] != built.payload_sha256
        or publication["payloadBytes"] != built.payload_bytes
    ):
        raise SourceSnapshotError("publication receipt is for another Source Snapshot")
    try:
        s3_payload = store.get_exact_version(
            publication["bucket"], publication["key"], publication["versionId"]
        )
    except Exception as exc:
        raise SourceSnapshotError("S3 exact-version visibility read failed") from exc
    _exact_payload(built, s3_payload, "S3 visibility")
    try:
        mac_payload = mac_reader(publication["key"])
    except Exception as exc:
        raise SourceSnapshotError("Mac mount visibility read failed") from exc
    try:
        _exact_payload(built, mac_payload, "Mac mount")
    except SourceSnapshotError as exc:
        raise SourceSnapshotError("Mac mount visibility payload mismatch") from exc
    return _typed(
        "visibility",
        {
            "bucket": publication["bucket"],
            "key": publication["key"],
            "macMountVisible": True,
            "payloadBytes": built.payload_bytes,
            "payloadSha256": built.payload_sha256,
            "publicationReceiptDigest": canonical_json_digest(publication),
            "region": publication["region"],
            "s3ExactVersionVisible": True,
            "schema": _SCHEMAS["visibility"],
            "sourceManifestDigest": built.manifest_digest,
            "versionId": publication["versionId"],
        },
    )


def build_source_snapshot_lock(
    built: SourceSnapshotBuild,
    publication: Mapping[str, Any],
    visibility: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Close manifest, payload, exact publication, and visibility identities."""

    _validate_build(built)
    publication = _typed("publication", publication)
    visibility = _typed("visibility", visibility)
    publication_digest = canonical_json_digest(publication)
    if visibility["publicationReceiptDigest"] != publication_digest:
        raise SourceSnapshotError("visibility does not bind the exact publication receipt")
    shared_fields = (
        "bucket",
        "key",
        "payloadBytes",
        "payloadSha256",
        "region",
        "sourceManifestDigest",
        "versionId",
    )
    if any(visibility[field] != publication[field] for field in shared_fields):
        raise SourceSnapshotError("publication and visibility projections differ")
    if (
        publication["sourceManifestDigest"] != built.manifest_digest
        or publication["payloadSha256"] != built.payload_sha256
        or publication["payloadBytes"] != built.payload_bytes
    ):
        raise SourceSnapshotError("publication chain does not bind the local build")
    manifest = built.manifest
    value = {
        "bucket": publication["bucket"],
        "cleanPolicy": manifest["cleanPolicy"],
        "exclusions": list(manifest["exclusions"]),
        "files": [dict(item) for item in manifest["files"]],
        "gitCommit": manifest["gitCommit"],
        "includePaths": list(manifest["includePaths"]),
        "key": publication["key"],
        "normalizationVersion": manifest["normalizationVersion"],
        "pathCount": manifest["pathCount"],
        "payloadBytes": built.payload_bytes,
        "payloadFormat": _PAYLOAD_FORMAT,
        "payloadSha256": built.payload_sha256,
        "publicationReceiptDigest": publication_digest,
        "region": publication["region"],
        "schema": _SCHEMAS["lock"],
        "sourceManifestDigest": built.manifest_digest,
        "symlinkPolicy": manifest["symlinkPolicy"],
        "totalBytes": manifest["totalBytes"],
        "versionId": publication["versionId"],
        "visibilityReceiptDigest": canonical_json_digest(visibility),
    }
    return _typed("lock", value)


def _probe_read_only_root(root_fd: int) -> bool:
    probe_name = ".agent-runtime-read-only-probe"
    try:
        descriptor = os.open(
            probe_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
    except OSError as exc:
        if exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}:
            return True
        raise SourceSnapshotError("read-only mount probe failed ambiguously") from exc
    os.close(descriptor)
    try:
        os.unlink(probe_name, dir_fd=root_fd)
    except OSError as exc:
        raise SourceSnapshotError("writable mount probe cleanup failed") from exc
    raise SourceSnapshotError("Source Snapshot mount accepted a write")


def _verify_read_only_mount(
    mount_root: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    *,
    write_probe: Callable[[int], None] | None = None,
) -> bool:
    """Internal mount verifier with an injected same-FD probe for adversarial tests."""

    _validate_manifest(manifest)
    root = Path(mount_root)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise SourceSnapshotError("read-only mount root is not a no-follow directory") from exc
    try:
        observed_paths = _enumerate_regular_paths(root_fd)
        expected_paths = tuple(entry["path"] for entry in manifest["files"])
        if observed_paths != expected_paths:
            raise SourceSnapshotError("read-only mount path set differs from manifest")
        for entry in manifest["files"]:
            payload, observed = _read_regular_file(root_fd, entry["path"], None)
            actual_mode = stat.S_IMODE(observed.st_mode)
            if actual_mode not in {0o644, 0o755}:
                raise SourceSnapshotError("read-only mount file mode is not normalized")
            mode = f"{actual_mode:04o}"
            if (
                len(payload) != entry["size"]
                or _sha256(payload) != entry["sha256"]
                or mode != entry["mode"]
            ):
                raise SourceSnapshotError("read-only mount content differs from manifest")
        if write_probe is None:
            return _probe_read_only_root(root_fd)
        try:
            write_probe(root_fd)
        except OSError as exc:
            if exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}:
                return True
            raise SourceSnapshotError("read-only mount probe failed ambiguously") from exc
        raise SourceSnapshotError("Source Snapshot mount accepted a write")
    finally:
        os.close(root_fd)


def verify_read_only_mount(
    mount_root: str | os.PathLike[str],
    manifest: Mapping[str, Any],
) -> bool:
    """Reobserve the complete manifest and prove the same opened root rejects a write."""

    try:
        return _verify_read_only_mount(mount_root, manifest)
    except SourceSnapshotError:
        raise
    except OSError as exc:
        raise SourceSnapshotError("read-only mount verification failed") from exc


__all__ = [
    "SourceSnapshotBuild",
    "SourceSnapshotError",
    "SourceSnapshotPublicationError",
    "SourceSnapshotStore",
    "build_source_snapshot",
    "build_source_snapshot_lock",
    "parse_source_snapshot_document",
    "publish_source_snapshot",
    "verify_read_only_mount",
    "verify_source_snapshot_visibility",
]
