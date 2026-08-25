"""Trusted canonical-build tool bundle materialized for candidate execution.

The candidate-tool sandbox must be able to invoke the shipped
``cad.canonical-build/1`` implementation without either (a) mounting the
repository or the installed skill tree broadly into the candidate, or (b)
falling back to whatever checkout happens to sit under an ambient
``$CODEX_HOME``. This module packages the required subset — the
``skills/cad/scripts/canonical-build/`` entrypoint and the vendored
``skills/cad/scripts/packages/cadgen/`` runtime — into a
content-addressed bundle that the trusted supervisor mounts read-only at
one fixed internal path (``/builder``).

The bundle contains only regular files under two allowlisted roots. The
identity of the bundle is the SHA-256 of its canonical manifest and it
becomes part of the trusted runner's runtime identity: a new bundle
identity implies a new mount, and the finalized publish tree records the
same identity the supervisor served at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import time
from typing import Iterable


class CanonicalBuildBundleError(RuntimeError):
    """The trusted canonical-build tool bundle could not be made safe/complete."""


_BUNDLE_SCHEMA = "mesh-to-cad.canonical-build-bundle/1"
_MANIFEST_NAME = ".bundle-manifest.json"
_MARKER_NAME = ".bundle-complete"
_MAX_BUNDLE_FILE_BYTES = 8 * 1024 * 1024
_MAX_BUNDLE_TOTAL_BYTES = 64 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024
_MAX_CACHE_ENTRIES = 2
_SOURCE_TREES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "skills/cad/scripts/canonical-build",
        "canonical-build",
        (".py",),
    ),
    (
        "skills/cad/scripts/packages/cadgen/src",
        "packages/cadgen/src",
        (".py",),
    ),
)
_SKIP_DIR_NAMES = frozenset({"__pycache__"})
_SKIP_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})
_IDENTITY_TAG = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CanonicalBuildBundleLease:
    """One live-use lease over a materialized canonical-build bundle."""

    bundle: Path
    identity: str

    @property
    def path(self) -> Path:
        return self.bundle

    def __fspath__(self) -> str:
        return os.fspath(self.bundle)

    def __str__(self) -> str:
        return str(self.bundle)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _iter_source_files(root: Path, allowed_suffixes: tuple[str, ...]) -> Iterable[Path]:
    """Yield the regular files below ``root`` that must be projected."""

    for parent, dirnames, filenames in os.walk(root, followlinks=False):
        parent_path = Path(parent)
        dirnames.sort()
        dirnames[:] = [
            name for name in dirnames
            if name not in _SKIP_DIR_NAMES and not (parent_path / name).is_symlink()
        ]
        for name in sorted(filenames):
            path = parent_path / name
            if path.is_symlink():
                raise CanonicalBuildBundleError("canonical_build_bundle_symlink")
            suffix = path.suffix.lower()
            if suffix in _SKIP_FILE_SUFFIXES:
                continue
            if suffix and suffix not in allowed_suffixes:
                continue
            yield path


def _read_regular(source: Path) -> bytes:
    """Read a regular file's bytes with no-follow semantics and size cap."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise CanonicalBuildBundleError("canonical_build_bundle_source_unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CanonicalBuildBundleError("canonical_build_bundle_source_invalid")
        if info.st_size > _MAX_BUNDLE_FILE_BYTES:
            raise CanonicalBuildBundleError("canonical_build_bundle_source_too_large")
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining > 0:
            chunk = os.read(descriptor, min(_COPY_CHUNK, remaining))
            if not chunk:
                raise CanonicalBuildBundleError("canonical_build_bundle_source_truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_regular(target: Path, body: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(target, flags, 0o600)
    try:
        view = memoryview(body)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short canonical build bundle write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _compute_entries(repo_root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen_relatives: set[str] = set()
    total_bytes = 0
    for source_rel, projected_rel, allowed_suffixes in _SOURCE_TREES:
        source_root = (repo_root / source_rel).resolve()
        if source_root.is_symlink():
            raise CanonicalBuildBundleError("canonical_build_bundle_source_symlink")
        if not source_root.is_dir():
            raise CanonicalBuildBundleError("canonical_build_bundle_source_missing")
        for source_path in _iter_source_files(source_root, allowed_suffixes):
            body = _read_regular(source_path)
            total_bytes += len(body)
            if total_bytes > _MAX_BUNDLE_TOTAL_BYTES:
                raise CanonicalBuildBundleError("canonical_build_bundle_too_large")
            projected = PurePosixPath(projected_rel) / PurePosixPath(
                source_path.relative_to(source_root).as_posix()
            )
            key = projected.as_posix()
            if key in seen_relatives:
                raise CanonicalBuildBundleError("canonical_build_bundle_duplicate")
            seen_relatives.add(key)
            entries.append(
                {
                    "path": key,
                    "size": len(body),
                    "sha256": _sha256_bytes(body),
                }
            )
    entries.sort(key=lambda item: item["path"])
    return entries


def _manifest_bytes(identity: str, entries: list[dict[str, object]]) -> bytes:
    payload = {
        "schema": _BUNDLE_SCHEMA,
        "identity": identity,
        "total_bytes": sum(int(item["size"]) for item in entries),
        "entries": entries,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _identity_from_entries(entries: list[dict[str, object]]) -> str:
    body = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("ascii")
    return _sha256_bytes(_BUNDLE_SCHEMA.encode("ascii") + b"\0" + body)


def _reject_symlink_components(path: Path) -> None:
    current = Path(path)
    while not current.exists() and not current.is_symlink() and current.parent != current:
        current = current.parent
    anchor = current
    current = Path(path)
    while True:
        try:
            if current.is_symlink():
                raise CanonicalBuildBundleError("canonical_build_bundle_cache_symlink")
        except OSError as exc:
            raise CanonicalBuildBundleError("canonical_build_bundle_cache_unavailable") from exc
        if current == anchor or current.parent == current:
            return
        current = current.parent


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise CanonicalBuildBundleError("canonical_build_bundle_symlink")
        try:
            if path.is_dir():
                path.chmod(0o555)
            else:
                path.chmod(0o444)
        except OSError as exc:
            raise CanonicalBuildBundleError("canonical_build_bundle_publish_failed") from exc
    root.chmod(0o555)


def _remove_tree(root: Path) -> None:
    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink():
        root.unlink()
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            path.unlink(missing_ok=True)
            continue
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            pass
    try:
        root.chmod(0o755)
    except OSError:
        pass
    shutil.rmtree(root, ignore_errors=False)


def _validate_materialized(bundle: Path, identity: str, entries: list[dict[str, object]]) -> None:
    """Assert the on-disk bundle exactly reproduces the manifest identity."""

    expected: dict[str, tuple[int, str]] = {
        str(item["path"]): (int(item["size"]), str(item["sha256"]))
        for item in entries
    }
    actual: dict[str, tuple[int, str]] = {}
    total = 0
    for path in sorted(bundle.rglob("*"), key=lambda item: item.relative_to(bundle).as_posix()):
        relative = path.relative_to(bundle).as_posix()
        if relative in {_MANIFEST_NAME, _MARKER_NAME}:
            if path.is_symlink() or path.parent != bundle:
                raise CanonicalBuildBundleError("canonical_build_bundle_publish_failed")
            continue
        if path.is_symlink():
            raise CanonicalBuildBundleError("canonical_build_bundle_symlink")
        if path.is_dir():
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CanonicalBuildBundleError("canonical_build_bundle_publish_failed")
        body = path.read_bytes()
        total += len(body)
        if total > _MAX_BUNDLE_TOTAL_BYTES:
            raise CanonicalBuildBundleError("canonical_build_bundle_too_large")
        actual[relative] = (len(body), _sha256_bytes(body))
    if actual != expected:
        raise CanonicalBuildBundleError("canonical_build_bundle_drift")


def _validate_marker(bundle: Path, identity: str) -> None:
    marker = bundle / _MARKER_NAME
    manifest = bundle / _MANIFEST_NAME
    if marker.is_symlink() or manifest.is_symlink():
        raise CanonicalBuildBundleError("canonical_build_bundle_symlink")
    if not marker.is_file() or not manifest.is_file():
        raise CanonicalBuildBundleError("canonical_build_bundle_incomplete")
    try:
        value = json.loads(marker.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalBuildBundleError("canonical_build_bundle_incomplete") from exc
    if not isinstance(value, dict) or value.get("schema") != _BUNDLE_SCHEMA:
        raise CanonicalBuildBundleError("canonical_build_bundle_incomplete")
    if value.get("identity") != identity:
        raise CanonicalBuildBundleError("canonical_build_bundle_identity_mismatch")
    if bundle.stat().st_mode & 0o222:
        raise CanonicalBuildBundleError("canonical_build_bundle_not_read_only")


def materialize_canonical_build_bundle(
    repo_root: Path,
    cache_root: Path,
) -> CanonicalBuildBundleLease:
    """Return one immutable, content-addressed canonical-build tool bundle.

    ``repo_root`` is the trusted repository root the pilot ships from;
    the bundle sources are the fixed ``_SOURCE_TREES`` beneath it. The
    cache is a supervisor-owned tree external to any Workspace; entries
    are keyed by the SHA-256 identity of their canonical manifest.
    """

    raw_repo = Path(repo_root)
    if raw_repo.is_symlink() or not raw_repo.is_dir():
        raise CanonicalBuildBundleError("canonical_build_bundle_repo_unavailable")
    repo_root = raw_repo.resolve()

    raw_cache = Path(cache_root)
    _reject_symlink_components(raw_cache)
    try:
        raw_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw_cache.chmod(0o700)
    except OSError as exc:
        raise CanonicalBuildBundleError("canonical_build_bundle_cache_unavailable") from exc

    entries = _compute_entries(repo_root)
    identity = _identity_from_entries(entries)
    final = raw_cache / identity
    if final.is_symlink():
        raise CanonicalBuildBundleError("canonical_build_bundle_cache_corrupt")

    if final.is_dir():
        try:
            _validate_marker(final, identity)
            _validate_materialized(final, identity, entries)
            _prune_cache(raw_cache, identity)
            return CanonicalBuildBundleLease(bundle=final, identity=identity)
        except CanonicalBuildBundleError:
            _remove_tree(final)

    temporary = raw_cache / f".{identity}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    if temporary.exists() or temporary.is_symlink():
        _remove_tree(temporary)
    temporary.mkdir(mode=0o700)
    try:
        for source_rel, projected_rel, allowed_suffixes in _SOURCE_TREES:
            source_root = (repo_root / source_rel).resolve()
            for source_path in _iter_source_files(source_root, allowed_suffixes):
                relative = source_path.relative_to(source_root).as_posix()
                projected = PurePosixPath(projected_rel) / PurePosixPath(relative)
                target = temporary / projected.as_posix()
                _write_regular(target, _read_regular(source_path))
        _validate_materialized(temporary, identity, entries)
        manifest_body = _manifest_bytes(identity, entries)
        _write_regular(temporary / _MANIFEST_NAME, manifest_body)
        marker_body = (
            json.dumps(
                {
                    "schema": _BUNDLE_SCHEMA,
                    "identity": identity,
                    "manifest_sha256": _sha256_bytes(manifest_body),
                    "published_ns": time.time_ns(),
                },
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
        _write_regular(temporary / _MARKER_NAME, marker_body)
        try:
            os.replace(temporary, final)
        except FileExistsError:
            _remove_tree(temporary)
            _validate_marker(final, identity)
            _validate_materialized(final, identity, entries)
        else:
            _make_read_only(final)
            _validate_marker(final, identity)
            _validate_materialized(final, identity, entries)
        _prune_cache(raw_cache, identity)
        return CanonicalBuildBundleLease(bundle=final, identity=identity)
    except Exception:
        _remove_tree(temporary)
        raise


def _prune_cache(cache_root: Path, protected: str) -> None:
    entries = [
        path
        for path in cache_root.iterdir()
        if path.is_dir() and not path.is_symlink() and _IDENTITY_TAG.fullmatch(path.name)
    ]
    if len(entries) <= _MAX_CACHE_ENTRIES:
        return
    entries.sort(key=lambda item: item.stat().st_mtime_ns)
    keep = {protected}
    for path in reversed(entries):
        if len(keep) >= _MAX_CACHE_ENTRIES:
            break
        keep.add(path.name)
    for path in entries:
        if path.name not in keep:
            _remove_tree(path)


def validate_canonical_build_bundle(bundle: Path, identity: str | None = None) -> str:
    """Validate a previously materialized bundle and return its identity."""

    raw = Path(bundle)
    if raw.is_symlink() or not raw.is_dir():
        raise CanonicalBuildBundleError("canonical_build_bundle_unavailable")
    marker = raw / _MARKER_NAME
    manifest = raw / _MANIFEST_NAME
    if marker.is_symlink() or manifest.is_symlink():
        raise CanonicalBuildBundleError("canonical_build_bundle_symlink")
    if not marker.is_file() or not manifest.is_file():
        raise CanonicalBuildBundleError("canonical_build_bundle_incomplete")
    try:
        marker_value = json.loads(marker.read_text(encoding="ascii"))
        manifest_value = json.loads(manifest.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalBuildBundleError("canonical_build_bundle_incomplete") from exc
    if (
        not isinstance(marker_value, dict)
        or not isinstance(manifest_value, dict)
        or marker_value.get("schema") != _BUNDLE_SCHEMA
        or manifest_value.get("schema") != _BUNDLE_SCHEMA
    ):
        raise CanonicalBuildBundleError("canonical_build_bundle_incomplete")
    manifest_identity = manifest_value.get("identity")
    if not isinstance(manifest_identity, str) or not _IDENTITY_TAG.fullmatch(manifest_identity):
        raise CanonicalBuildBundleError("canonical_build_bundle_identity_invalid")
    if marker_value.get("identity") != manifest_identity:
        raise CanonicalBuildBundleError("canonical_build_bundle_identity_mismatch")
    if identity is not None and manifest_identity != identity:
        raise CanonicalBuildBundleError("canonical_build_bundle_identity_mismatch")
    raw_entries = manifest_value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CanonicalBuildBundleError("canonical_build_bundle_incomplete")
    typed_entries: list[dict[str, object]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            raise CanonicalBuildBundleError("canonical_build_bundle_incomplete")
        typed_entries.append(item)
    _validate_materialized(raw, manifest_identity, typed_entries)
    if raw.stat().st_mode & 0o222:
        raise CanonicalBuildBundleError("canonical_build_bundle_not_read_only")
    return manifest_identity


BUILDER_TOOL_ENTRYPOINT: PurePosixPath = PurePosixPath("canonical-build")


__all__ = [
    "BUILDER_TOOL_ENTRYPOINT",
    "CanonicalBuildBundleError",
    "CanonicalBuildBundleLease",
    "materialize_canonical_build_bundle",
    "validate_canonical_build_bundle",
]
