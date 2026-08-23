"""Shared plugin-deployment authority for CVM Codex pilots.

The publisher (``scripts/pilot/cvm_install_plugin.py``, invoked over SSH from
``cvm_push``) constructs a symlink-free installed plugin cache under a
content-addressed directory in the deployment root, verifies it against the
prepared publish tree, and atomically swaps the ``current.json`` pointer.
The pointer references a self-contained isolated ``CODEX_HOME`` that already
holds ``config.toml`` marketplace/plugin registration and the installed
plugin cache; consumers materialize a job-private copy of that home.

Consumers (``scripts/pilot/runner.py`` and ``scripts/pilot/cvm_agent.py``) read
``current.json`` through :func:`resolve_current_authority`, which validates the
pointer schema, the identity binding between the pointer digest and the
deployment content, the real paths on disk (lexical containment plus a strict
symlink-free ancestor/leaf chain), and recomputes both prepared-tree and
installed-tree manifests + critical-runtime probe hashes on every consumption.
Any missing or divergent state raises :class:`PluginAuthorityError`; consumers
must fail closed rather than fall back to legacy ``~/.codex/skills`` symlinks.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

try:
    import fcntl
except ModuleNotFoundError:  # Windows can audit authority but cannot publish it.
    fcntl = None  # type: ignore[assignment]


AUTHORITY_ROOT_NAME = ".text-to-cad-codex"
DEPLOYMENTS_DIRNAME = "deployments"
POINTER_NAME = "current.json"
LOCK_NAME = ".publish.lock"
RECEIPT_FILE = "deployment.receipt.json"
PUBLISH_TREE_DIRNAME = "publish-tree"
CODEX_HOME_DIRNAME = "codex-home"
CONFIG_TOML_NAME = "config.toml"
RECEIPT_SCHEMA = "text-to-cad.plugin-authority/2"
PROVENANCE_SCHEMA = "text-to-cad.push-provenance/2"
MARKETPLACE_NAME = "text-to-cad"
PLUGIN_SELECTOR = "cad@text-to-cad"
SANDBOX_MARKETPLACE_SOURCE = "/opt/text-to-cad-publish-tree"

# Stage manifest — the canonical list of regular files the Mac stage carried
# at push time. Its digest is bound into the push provenance so the CVM
# publisher can materialize publish-tree-src from exactly the transferred
# stage, not from whatever files happen to live under the persistent
# non-deleting ``~/text-to-cad`` overlay at install time.
STAGE_MANIFEST_FILENAME = ".text-to-cad-stage-manifest.json"
STAGE_MANIFEST_SCHEMA = "text-to-cad.stage-manifest/2"
REQUIRED_RUNTIME_ATTESTATION_PATHS = (
    "skills/cad-viewer/scripts/viewer/backend/server.mjs",
    "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
)

# Local Mac-side staging hygiene only. This list keeps private/local state
# out of the transferred stage. It is NOT — and never was — sufficient to
# guarantee the transferred snapshot equals what lands on CVM: the CVM
# ``~/text-to-cad`` overlay is persistent and non-deleting, so any file a
# prior push wrote there that later disappeared from the Mac stage would
# linger on CVM regardless of what we exclude here. Exact snapshot identity
# is enforced by the stage manifest read at install time (see
# :func:`materialize_from_stage_manifest`), not by this tuple.
DEPLOYMENT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "/.git",
    "/.git/",
    "/.venv",
    "/.venv/",
    "/.agents/",
    "/.claude/",
    "/.codex/",
    "/.DS_Store",
    "/.cvm-jobs/",
    "/.cvm-agent-jobs/",
    "/.cvm-browser-runtime/",
    "/.text-to-cad-codex/",
    "/outputs/",
    "/models/",
    "/docs/",
    "/tmp/",
    "/worktrees/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    "*.swp",
    "*.tmp",
    "/viewer/dist/",
)


class PluginAuthorityError(RuntimeError):
    """A fail-closed authority state was rejected by the consumer."""


@dataclass(frozen=True)
class DeploymentReceipt:
    """One published plugin-authority pointer/receipt document."""

    schema: str
    deployment_id: str
    version: str
    plugin_selector: str
    prepared_manifest_digest: str
    installed_manifest_digest: str
    codex_home_manifest_digest: str
    codex_version: str
    published_at: str
    source_git_sha: str
    deployment_dir: Path
    publish_tree: Path
    codex_home: Path
    installed_path: Path
    critical_runtimes: tuple[dict[str, str], ...]
    transfer_provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "deployment_id": self.deployment_id,
            "version": self.version,
            "plugin_selector": self.plugin_selector,
            "prepared_manifest_digest": self.prepared_manifest_digest,
            "installed_manifest_digest": self.installed_manifest_digest,
            "codex_home_manifest_digest": self.codex_home_manifest_digest,
            "codex_version": self.codex_version,
            "published_at": self.published_at,
            "source_git_sha": self.source_git_sha,
            "deployment_dir": str(self.deployment_dir),
            "publish_tree": str(self.publish_tree),
            "codex_home": str(self.codex_home),
            "installed_path": str(self.installed_path),
            "critical_runtimes": [dict(item) for item in self.critical_runtimes],
            "transfer_provenance": dict(self.transfer_provenance),
        }


def compute_deployment_id(
    prepared_manifest_digest: str,
    version: str,
    transfer_provenance: Mapping[str, Any],
) -> str:
    """Return the content-bound deployment identity.

    The identity binds the prepared publish-tree manifest digest to the
    canonical repository ``VERSION`` and the identity-bearing transfer
    provenance. This
    keeps idempotence for repeated publication of one snapshot without
    reusing provenance from a different snapshot that finalizes to the same
    plugin bytes.
    """

    if not isinstance(prepared_manifest_digest, str) or len(
        prepared_manifest_digest
    ) != 64:
        raise PluginAuthorityError("prepared manifest digest is invalid")
    try:
        int(prepared_manifest_digest, 16)
    except ValueError as exc:
        raise PluginAuthorityError("prepared manifest digest is invalid") from exc
    if not isinstance(version, str) or not version.strip():
        raise PluginAuthorityError("deployment version is invalid")
    provenance = _validate_transfer_provenance(dict(transfer_provenance))
    # Transfer statistics vary between retries and are operational evidence,
    # not source identity. Every other validated field is identity-bearing,
    # including git branch/head/state, stage digest, and runtime attestation.
    provenance.pop("transfer_summary", None)
    provenance_bytes = json.dumps(
        provenance, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload = b"\0".join(
        (
            prepared_manifest_digest.encode("ascii"),
            version.encode("utf-8"),
            provenance_bytes,
        )
    )
    return hashlib.sha256(payload).hexdigest()


def _lexical_child(parent: Path, name: str) -> Path:
    """Return parent/name without any resolve()/expanduser magic."""

    if "/" in name or "\0" in name or name in {"", ".", ".."}:
        raise PluginAuthorityError(f"invalid path component: {name!r}")
    return Path(os.fspath(parent)) / name


def authority_root(codex_home_root: Path) -> Path:
    """Return the top-level authority directory purely lexically.

    Symlinks under the trusted host home are not followed here: the caller is
    responsible for supplying a trusted host home, and every level from
    ``.text-to-cad-codex`` downward is later checked as symlink-free through
    ``_lexical_stat``. Using ``.resolve()`` here would have silently accepted a
    ``~/.text-to-cad-codex`` that was itself a symlink to attacker-controlled
    state.
    """

    root = Path(codex_home_root).expanduser()
    return _lexical_child(root, AUTHORITY_ROOT_NAME)


def deployment_root(codex_home_root: Path) -> Path:
    return _lexical_child(authority_root(codex_home_root), DEPLOYMENTS_DIRNAME)


def pointer_path(codex_home_root: Path) -> Path:
    return _lexical_child(deployment_root(codex_home_root), POINTER_NAME)


def lock_path(codex_home_root: Path) -> Path:
    return _lexical_child(deployment_root(codex_home_root), LOCK_NAME)


def deployment_directory(codex_home_root: Path, deployment_id: str) -> Path:
    if not isinstance(deployment_id, str) or len(deployment_id) != 64:
        raise PluginAuthorityError("deployment id is invalid")
    try:
        int(deployment_id, 16)
    except ValueError as exc:
        raise PluginAuthorityError("deployment id is invalid") from exc
    return _lexical_child(deployment_root(codex_home_root), deployment_id)


def _reject_preexisting_symlink(path: Path, *, label: str) -> None:
    """Refuse to operate through a symlinked leaf.

    ``mkdir(exist_ok=True)`` and ``open(..., "a")`` both follow symlinks. If
    an attacker (or a stale prior deployment tree) planted a symlink at
    ``~/.text-to-cad-codex``, ``deployments/``, the publish lock, or the
    ``current.json`` pointer, subsequent writes would land outside the
    intended authority root. Reject the symlink lexically before any
    mutation. Missing paths are fine — the caller will create them.
    """

    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PluginAuthorityError(
            f"{label} is inaccessible: {path}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise PluginAuthorityError(f"{label} is a symlink: {path}")


@contextmanager
def publication_lock(path: Path):
    """Lock one physical regular file without following a replaced symlink."""

    if fcntl is None:
        raise PluginAuthorityError("plugin authority publication requires POSIX fcntl")

    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PluginAuthorityError(
            f"publish lock cannot be opened without following links: {path}: {exc}"
        ) from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise PluginAuthorityError(f"publish lock is not a regular file: {path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        def verify() -> None:
            try:
                current = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise PluginAuthorityError(
                    f"publish lock disappeared while held: {path}: {exc}"
                ) from exc
            if (current.st_dev, current.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise PluginAuthorityError(
                    f"publish lock changed while held: {path}"
                )

        verify()
        yield verify
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def ensure_authority_root(codex_home_root: Path) -> Path:
    """Create the authority tree with restrictive permissions if absent.

    Both the authority root (``~/.text-to-cad-codex``) and the deployments
    subdirectory are lexically checked for pre-existing symlinks before any
    ``mkdir`` call: ``mkdir(parents=True, exist_ok=True)`` would happily
    succeed on a symlink whose target is a directory, and every subsequent
    write in that subtree would then land outside the trusted host home.
    """

    root = authority_root(codex_home_root)
    _reject_preexisting_symlink(root, label="authority root")
    root.mkdir(parents=True, exist_ok=True)
    deployments = _lexical_child(root, DEPLOYMENTS_DIRNAME)
    _reject_preexisting_symlink(deployments, label="deployments directory")
    deployments.mkdir(exist_ok=True)
    return deployments


def _lexical_stat(path: Path, *, label: str, expect: str) -> os.stat_result:
    """os.lstat plus a hard reject of symlinks and unexpected file kinds."""

    try:
        metadata = os.lstat(path)
    except FileNotFoundError as exc:
        raise PluginAuthorityError(f"{label} is missing: {path}") from exc
    except OSError as exc:
        raise PluginAuthorityError(f"{label} is inaccessible: {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise PluginAuthorityError(f"{label} is a symlink: {path}")
    mode = metadata.st_mode
    if expect == "dir" and not stat.S_ISDIR(mode):
        raise PluginAuthorityError(f"{label} is not a directory: {path}")
    if expect == "file" and not stat.S_ISREG(mode):
        raise PluginAuthorityError(f"{label} is not a regular file: {path}")
    return metadata


def _reject_symlinks_below(root: Path, *, label: str) -> None:
    """Fail closed on any symlink inside the given subtree."""

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames):
            path = Path(dirpath) / name
            if path.is_symlink():
                raise PluginAuthorityError(
                    f"{label} contains a symlink: {path}"
                )
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                raise PluginAuthorityError(
                    f"{label} contains a symlink: {path}"
                )


def _validate_transfer_provenance(value: object) -> dict[str, Any]:
    """Reject any provenance document without the pinned schema and fields."""

    if not isinstance(value, dict):
        raise PluginAuthorityError("transfer provenance is invalid")
    schema = value.get("schema")
    if schema != PROVENANCE_SCHEMA:
        raise PluginAuthorityError(
            f"transfer provenance has unexpected schema: {schema!r}"
        )
    required = {"schema", "mac_branch", "mac_head", "mac_state", "stage_manifest_digest"}
    missing = required - set(value)
    if missing:
        raise PluginAuthorityError(
            f"transfer provenance missing keys: {sorted(missing)}"
        )
    branch = value["mac_branch"]
    head = value["mac_head"]
    state = value["mac_state"]
    stage_digest = value["stage_manifest_digest"]
    if (
        not isinstance(branch, str)
        or not branch
        or len(branch) > 200
        or "\0" in branch
        or not re.fullmatch(r"[\x20-\x7e]+", branch)
    ):
        raise PluginAuthorityError("transfer provenance mac_branch is invalid")
    if not isinstance(head, str) or (
        head != "no-git" and not re.fullmatch(r"[0-9a-f]{40}", head)
    ):
        raise PluginAuthorityError("transfer provenance mac_head is invalid")
    if state not in {"clean", "dirty"}:
        raise PluginAuthorityError("transfer provenance mac_state is invalid")
    if not isinstance(stage_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", stage_digest
    ):
        raise PluginAuthorityError(
            "transfer provenance stage_manifest_digest is invalid"
        )
    transfer = value.get("transfer_summary")
    if transfer is not None and not isinstance(transfer, dict):
        raise PluginAuthorityError(
            "transfer provenance transfer_summary is invalid"
        )
    runtime = value.get("runtime_attestation")
    if not isinstance(runtime, dict) or not runtime or not all(
        isinstance(k, str)
        and isinstance(v, str)
        and re.fullmatch(r"[0-9a-f]{64}", v)
        for k, v in runtime.items()
    ):
        raise PluginAuthorityError(
            "transfer provenance runtime_attestation is invalid"
        )
    missing_runtime = set(REQUIRED_RUNTIME_ATTESTATION_PATHS) - set(runtime)
    if missing_runtime:
        raise PluginAuthorityError(
            "transfer provenance runtime_attestation missing keys: "
            f"{sorted(missing_runtime)}"
        )
    return dict(value)


def _stage_manifest_digest(entries: list[dict[str, str]]) -> str:
    """Digest bytes are ``path\\0sha256\\0mode\\n`` in path order.

    Same shape as :attr:`scripts.release.smoke_installed_plugin.Manifest.digest`
    so the same helper can be reused for reasoning about identity; deliberately
    NOT importing smoke here — the digest computation is trivial and coupling
    the transport-control artifact to the smoke test would just add churn.
    """

    h = hashlib.sha256()
    for entry in entries:
        h.update(entry["path"].encode("utf-8"))
        h.update(b"\0")
        h.update(entry["sha256"].encode("ascii"))
        h.update(b"\0")
        h.update(entry["mode"].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _validate_manifest_relative_path(rel: str) -> None:
    """Fail closed on absolute, traversal, empty, or otherwise unsafe paths."""

    if not isinstance(rel, str) or not rel:
        raise PluginAuthorityError("stage manifest entry has empty path")
    if "\0" in rel or "\\" in rel:
        raise PluginAuthorityError(
            f"stage manifest entry has invalid path: {rel!r}"
        )
    if rel.startswith("/"):
        raise PluginAuthorityError(
            f"stage manifest entry has absolute path: {rel!r}"
        )
    parts = PurePosixPath(rel).parts
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise PluginAuthorityError(
            f"stage manifest entry has traversal/empty component: {rel!r}"
        )


def write_stage_manifest(stage: Path) -> str:
    """Write the canonical stage manifest and return its digest.

    The manifest lists every regular file under ``stage`` EXCEPT the manifest
    file itself, so it never self-hashes and never becomes plugin content when
    a downstream materializer copies only its listed entries. The returned
    digest is bound into the push provenance so the CVM publisher can prove
    the transferred snapshot equals what the Mac stage carried at push time,
    even though the CVM's ``~/text-to-cad`` overlay is persistent and
    non-deleting.
    """

    stage_path = Path(stage)
    manifest_path = stage_path / STAGE_MANIFEST_FILENAME
    try:
        root_metadata = os.lstat(stage_path)
    except OSError as exc:
        raise PluginAuthorityError(f"stage root is inaccessible: {stage_path}: {exc}") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise PluginAuthorityError(f"stage root is not a physical directory: {stage_path}")
    try:
        manifest_metadata = os.lstat(manifest_path)
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(manifest_metadata.st_mode) or not stat.S_ISREG(
            manifest_metadata.st_mode
        ):
            raise PluginAuthorityError(
                f"stage manifest path is not a regular file: {manifest_path}"
            )
        manifest_path.unlink()

    # Build inputs intentionally contain symlinks (notably node_modules/.bin)
    # that are useful only while bundling and live under roots removed by the
    # publish finalizer. The transfer identity is therefore a regular-file
    # allowlist: symlinks are never listed or copied, while every listed byte
    # is hash-bound and rechecked on the CVM.
    entries: list[dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(stage_path, followlinks=False):
        dirnames.sort()
        for name in dirnames:
            path = Path(dirpath) / name
            if path.is_symlink():
                raise PluginAuthorityError(
                    f"stage contains an unmanifested symlink: {path}"
                )
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode != 0o755:
                raise PluginAuthorityError(
                    f"stage directory has unsafe permission mode {mode:04o}: {path}"
                )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path == manifest_path:
                continue
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                raise PluginAuthorityError(
                    f"stage contains an unmanifested symlink: {path}"
                )
            if not stat.S_ISREG(metadata.st_mode):
                raise PluginAuthorityError(
                    f"stage contains an unsupported filesystem object: {path}"
                )
            relative = path.relative_to(stage_path).as_posix()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
            if mode not in {"0644", "0755"}:
                raise PluginAuthorityError(
                    f"stage file has unsafe permission mode {mode}: {path}"
                )
            entries.append(
                {"path": relative, "sha256": digest.hexdigest(), "mode": mode}
            )
    entries.sort(key=lambda entry: entry["path"])
    digest = _stage_manifest_digest(entries)
    payload = {
        "schema": STAGE_MANIFEST_SCHEMA,
        "entries": entries,
    }
    manifest_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return digest


def normalize_stage_permissions(stage: Path) -> None:
    """Normalize a physical transfer tree to portable safe publish modes."""

    stage_path = Path(stage)
    root_metadata = os.lstat(stage_path)
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise PluginAuthorityError(
            f"stage root is not a physical directory: {stage_path}"
        )
    os.chmod(stage_path, 0o755)
    for dirpath, dirnames, filenames in os.walk(stage_path, followlinks=False):
        for name in dirnames:
            path = Path(dirpath) / name
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise PluginAuthorityError(
                    f"stage contains an unsupported filesystem object: {path}"
                )
            os.chmod(path, 0o755)
        for name in filenames:
            path = Path(dirpath) / name
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise PluginAuthorityError(
                    f"stage contains an unsupported filesystem object: {path}"
                )
            os.chmod(path, 0o755 if metadata.st_mode & 0o111 else 0o644)


def _open_directory_no_follow(path: Path, *, label: str) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PluginAuthorityError(f"{label} is inaccessible: {path}: {exc}") from exc
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise PluginAuthorityError(f"{label} is not a physical directory: {path}")
    return fd


def _open_regular_beneath(root_fd: int, parts: tuple[str, ...], *, label: str) -> int:
    directory_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        relative = "/".join(parts)
        if exc.errno == errno.ENOENT:
            detail = "is missing"
        elif exc.errno == errno.ELOOP:
            detail = "is a symlink"
        elif exc.errno == errno.ENOTDIR:
            detail = "ancestor is a symlink or not a directory"
        else:
            detail = f"is inaccessible: {exc}"
        raise PluginAuthorityError(f"{label} {detail}: {relative}") from exc
    finally:
        os.close(directory_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PluginAuthorityError(f"{label} is not a regular file: {'/'.join(parts)}")
    return fd


def _read_stage_manifest_from_fd(
    source_fd: int, source: Path, expected_digest: str
) -> list[dict[str, str]]:
    try:
        manifest_fd = _open_regular_beneath(
            source_fd,
            (STAGE_MANIFEST_FILENAME,),
            label="stage manifest",
        )
        with os.fdopen(manifest_fd, "r", encoding="utf-8") as stream:
            raw = stream.read()
    except PluginAuthorityError:
        raise
    except OSError as exc:
        raise PluginAuthorityError(
            f"stage manifest is unreadable at {source / STAGE_MANIFEST_FILENAME}: {exc}"
        ) from exc
    return _decode_stage_manifest(raw, source, expected_digest)


def read_stage_manifest(source: Path, expected_digest: str) -> list[dict[str, str]]:
    """Read, schema-check, digest-verify, and shape-check a stage manifest.

    Returns the validated entry list. Rejects: missing manifest, malformed
    JSON, wrong schema, non-list entries, entries missing path/sha256, invalid
    sha256, absolute paths, ``..``, empty components, duplicates, and digest
    mismatch against the caller-supplied ``expected_digest``.
    """

    if not isinstance(expected_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_digest
    ):
        raise PluginAuthorityError(
            "expected stage manifest digest is invalid"
        )
    source_path = Path(source)
    source_fd = _open_directory_no_follow(source_path, label="stage source root")
    try:
        return _read_stage_manifest_from_fd(source_fd, source_path, expected_digest)
    finally:
        os.close(source_fd)


def _decode_stage_manifest(
    raw: str, source: Path, expected_digest: str
) -> list[dict[str, str]]:
    manifest_path = source / STAGE_MANIFEST_FILENAME
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PluginAuthorityError(
            f"stage manifest is not valid JSON: {manifest_path}"
        ) from exc
    if not isinstance(document, dict) or document.get("schema") != STAGE_MANIFEST_SCHEMA:
        raise PluginAuthorityError(
            f"stage manifest has unexpected schema: {manifest_path}"
        )
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise PluginAuthorityError(
            f"stage manifest entries are not a list: {manifest_path}"
        )
    seen: set[str] = set()
    entries: list[dict[str, str]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            raise PluginAuthorityError("stage manifest entry is not an object")
        path = item.get("path")
        sha = item.get("sha256")
        mode = item.get("mode")
        if (
            not isinstance(path, str)
            or not isinstance(sha, str)
            or mode not in {"0644", "0755"}
        ):
            raise PluginAuthorityError("stage manifest entry is malformed")
        _validate_manifest_relative_path(path)
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise PluginAuthorityError(
                f"stage manifest entry has invalid sha256: {path}"
            )
        if path == STAGE_MANIFEST_FILENAME:
            raise PluginAuthorityError(
                "stage manifest may not list itself"
            )
        if path in seen:
            raise PluginAuthorityError(
                f"stage manifest has duplicate entry: {path}"
            )
        seen.add(path)
        entries.append({"path": path, "sha256": sha, "mode": mode})
    computed_digest = _stage_manifest_digest(entries)
    if computed_digest != expected_digest:
        raise PluginAuthorityError(
            "stage manifest digest does not match provenance binding: "
            f"{computed_digest} vs {expected_digest}"
        )
    return entries


def materialize_from_stage_manifest(
    source: Path,
    dst: Path,
    *,
    expected_manifest_digest: str,
) -> None:
    """Copy exactly the manifest-listed files from ``source`` into ``dst``.

    Any unlisted file in the persistent CVM overlay is silently skipped —
    that is the whole point of this materializer, since ``cvm-push`` runs a
    non-deleting rsync into ``~/text-to-cad`` and files removed from the Mac
    stage will linger there indefinitely. For each listed entry the source
    file must exist, must be a regular file (not a symlink, not a
    directory/socket/device), and its sha256 must match the manifest. Any
    mismatch fails closed and no partial ``dst`` is left published.
    """

    source_path = Path(source)
    dst_path = Path(dst)
    if not isinstance(expected_manifest_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_manifest_digest
    ):
        raise PluginAuthorityError("expected stage manifest digest is invalid")
    root_fd = _open_directory_no_follow(source_path, label="stage source root")
    try:
        entries = _read_stage_manifest_from_fd(
            root_fd, source_path, expected_manifest_digest
        )
        dst_path.mkdir(parents=True, exist_ok=True)
        os.chmod(dst_path, 0o755)
        for entry in entries:
            rel = entry["path"]
            expected_sha = entry["sha256"]
            expected_mode = entry["mode"]
            parts = PurePosixPath(rel).parts
            source_fd = _open_regular_beneath(
                root_fd, parts, label="stage manifest source"
            )
            dst_file = dst_path.joinpath(*parts)
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            parent = dst_path
            for component in parts[:-1]:
                parent /= component
                os.chmod(parent, 0o755)
            try:
                metadata = os.fstat(source_fd)
                observed_mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
                if observed_mode != expected_mode:
                    raise PluginAuthorityError(
                        f"stage manifest mode mismatch for {rel}: "
                        f"{observed_mode} vs {expected_mode}"
                    )
                digest = hashlib.sha256()
                with os.fdopen(source_fd, "rb") as source_stream:
                    source_fd = -1
                    with dst_file.open("xb") as destination_stream:
                        for chunk in iter(
                            lambda: source_stream.read(1024 * 1024), b""
                        ):
                            digest.update(chunk)
                            destination_stream.write(chunk)
                observed_sha = digest.hexdigest()
                if observed_sha != expected_sha:
                    dst_file.unlink(missing_ok=True)
                    raise PluginAuthorityError(
                        f"stage manifest content mismatch for {rel}: "
                        f"{observed_sha} vs {expected_sha}"
                    )
                os.chmod(dst_file, int(expected_mode, 8))
            finally:
                if source_fd >= 0:
                    os.close(source_fd)
    finally:
        os.close(root_fd)


def _validate_receipt_shape(value: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "deployment_id",
        "version",
        "plugin_selector",
        "prepared_manifest_digest",
        "installed_manifest_digest",
        "codex_home_manifest_digest",
        "codex_version",
        "published_at",
        "source_git_sha",
        "deployment_dir",
        "publish_tree",
        "codex_home",
        "installed_path",
        "critical_runtimes",
        "transfer_provenance",
    }
    missing = required - set(value)
    if missing:
        raise PluginAuthorityError(
            f"authority receipt missing keys: {sorted(missing)}"
        )
    unknown = set(value) - required
    if unknown:
        raise PluginAuthorityError(
            f"authority receipt has unknown keys: {sorted(unknown)}"
        )
    if value["schema"] != RECEIPT_SCHEMA:
        raise PluginAuthorityError(
            f"authority receipt has unexpected schema: {value['schema']!r}"
        )
    for name in (
        "deployment_id",
        "prepared_manifest_digest",
        "installed_manifest_digest",
        "codex_home_manifest_digest",
    ):
        digest = value[name]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PluginAuthorityError(f"authority receipt has invalid {name}")
    if not isinstance(value["version"], str) or not value["version"].strip():
        raise PluginAuthorityError("authority receipt has invalid version")
    if value["plugin_selector"] != PLUGIN_SELECTOR:
        raise PluginAuthorityError("authority receipt has invalid plugin_selector")
    if not isinstance(value["codex_version"], str) or not value["codex_version"].strip():
        raise PluginAuthorityError("authority receipt has invalid codex_version")
    if not isinstance(value["published_at"], str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value["published_at"]
    ):
        raise PluginAuthorityError("authority receipt has invalid published_at")
    if not isinstance(value["source_git_sha"], str):
        raise PluginAuthorityError("authority receipt has invalid source_git_sha")
    for name in ("deployment_dir", "publish_tree", "codex_home", "installed_path"):
        path_value = value[name]
        if not isinstance(path_value, str) or not Path(path_value).is_absolute():
            raise PluginAuthorityError(f"authority receipt has invalid {name}")
    critical = value["critical_runtimes"]
    if not isinstance(critical, list) or not all(
        isinstance(item, dict)
        and set(item) == {"runtime", "probe", "probe_sha256"}
        and isinstance(item["runtime"], str)
        and isinstance(item["probe"], str)
        and isinstance(item["probe_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", item["probe_sha256"])
        for item in critical
    ):
        raise PluginAuthorityError("authority receipt has invalid critical_runtimes")
    provenance = _validate_transfer_provenance(value["transfer_provenance"])
    if value["source_git_sha"] != provenance["mac_head"]:
        raise PluginAuthorityError(
            "authority receipt source_git_sha disagrees with transfer provenance"
        )


def _receipt_from_document(value: Mapping[str, Any]) -> DeploymentReceipt:
    _validate_receipt_shape(value)
    return DeploymentReceipt(
        schema=str(value["schema"]),
        deployment_id=str(value["deployment_id"]),
        version=str(value["version"]),
        plugin_selector=str(value["plugin_selector"]),
        prepared_manifest_digest=str(value["prepared_manifest_digest"]),
        installed_manifest_digest=str(value["installed_manifest_digest"]),
        codex_home_manifest_digest=str(value["codex_home_manifest_digest"]),
        codex_version=str(value["codex_version"]),
        published_at=str(value["published_at"]),
        source_git_sha=str(value["source_git_sha"]),
        deployment_dir=Path(str(value["deployment_dir"])),
        publish_tree=Path(str(value["publish_tree"])),
        codex_home=Path(str(value["codex_home"])),
        installed_path=Path(str(value["installed_path"])),
        critical_runtimes=tuple(
            {str(k): str(v) for k, v in item.items()}
            for item in value["critical_runtimes"]
        ),
        transfer_provenance=dict(value["transfer_provenance"]),
    )


def read_receipt(path: Path) -> DeploymentReceipt:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PluginAuthorityError(f"cannot read receipt {path}: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise PluginAuthorityError(f"receipt is not a regular file: {path}")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            raw = stream.read()
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PluginAuthorityError(
            f"authority receipt is not valid JSON: {path}"
        ) from exc
    if not isinstance(document, dict):
        raise PluginAuthorityError(f"authority receipt is not a JSON object: {path}")
    return _receipt_from_document(document)


def _load_smoke_installed_plugin() -> Any:
    """Load release manifest helpers in package and direct-script modes."""

    try:
        from scripts.release import smoke_installed_plugin as smoke
    except ModuleNotFoundError as exc:
        if exc.name != "scripts":
            raise
        repo_root = os.fspath(Path(__file__).resolve().parents[2])
        inserted = repo_root not in sys.path
        if inserted:
            sys.path.insert(0, repo_root)
        try:
            from scripts.release import smoke_installed_plugin as smoke
        finally:
            if inserted:
                sys.path.remove(repo_root)
    return smoke


def _compute_manifest_digest(
    root: Path,
    *,
    private_paths: tuple[str, ...] = (),
) -> tuple[str, int]:
    """Return (digest, file_count) of ``root`` reusing the smoke manifest rules.

    Centralizing this call ensures the authority tree and the smoke share one
    canonical manifest definition (regular files only, symlink-free, path +
    sha256 concatenated). A recompute mismatch is exactly the "unrecorded
    byte" attack the P1-2 review flagged.
    """

    smoke = _load_smoke_installed_plugin()

    try:
        manifest = smoke.compute_manifest(Path(root), private_paths=private_paths)
    except smoke.SmokeError as exc:
        raise PluginAuthorityError(str(exc)) from exc
    return manifest.digest, len(manifest.entries)


def _recompute_critical_runtimes(installed_path: Path) -> list[dict[str, str]]:
    smoke = _load_smoke_installed_plugin()

    try:
        return smoke.assert_critical_runtimes(Path(installed_path))
    except smoke.SmokeError as exc:
        raise PluginAuthorityError(str(exc)) from exc


def _assert_registration_intact(
    codex_home: Path, *, expected_marketplace_source: Path
) -> None:
    """Parse ``config.toml`` and refuse a disabled plugin or foreign source.

    The digest binding above catches any byte-level tampering; this parse
    additionally asserts the semantic invariants that make the deployment
    usable at all — the local marketplace must still be registered, its
    ``source_type`` must still be ``local``, and the plugin selector must
    still be ``enabled = true``. If any of those fail, we fail the resolve
    rather than deliver a materialized-but-inert codex home to the pilot.
    """

    import tomllib

    config_path = Path(codex_home) / CONFIG_TOML_NAME
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError as exc:
        raise PluginAuthorityError(
            f"codex home config.toml is missing: {config_path}"
        ) from exc
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginAuthorityError(
            f"codex home config.toml is not valid TOML: {config_path}: {exc}"
        ) from exc
    try:
        marketplace = parsed["marketplaces"][MARKETPLACE_NAME]
    except (KeyError, TypeError) as exc:
        raise PluginAuthorityError(
            "codex home config.toml is missing "
            f"[marketplaces.{MARKETPLACE_NAME}] registration"
        ) from exc
    if marketplace.get("source_type") != "local":
        raise PluginAuthorityError(
            "codex home marketplace source_type is not 'local': "
            f"{marketplace.get('source_type')!r}"
        )
    source = marketplace.get("source")
    if source != str(expected_marketplace_source):
        raise PluginAuthorityError(
            "codex home marketplace source does not match publish tree: "
            f"{source!r} vs {str(expected_marketplace_source)!r}"
        )
    try:
        plugin_entry = parsed["plugins"][PLUGIN_SELECTOR]
    except (KeyError, TypeError) as exc:
        raise PluginAuthorityError(
            "codex home config.toml is missing "
            f'[plugins."{PLUGIN_SELECTOR}"] registration'
        ) from exc
    if plugin_entry.get("enabled") is not True:
        raise PluginAuthorityError(
            f"codex home plugin '{PLUGIN_SELECTOR}' is not enabled"
        )
    allowed_marketplace_keys = {"source", "source_type", "last_updated"}
    last_updated = marketplace.get("last_updated")
    if last_updated is not None and (
        not isinstance(last_updated, str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", last_updated)
    ):
        raise PluginAuthorityError(
            "codex home marketplace last_updated is invalid"
        )
    if (
        set(parsed) != {"marketplaces", "plugins"}
        or set(parsed["marketplaces"]) != {MARKETPLACE_NAME}
        or set(marketplace) - allowed_marketplace_keys
        or set(parsed["plugins"]) != {PLUGIN_SELECTOR}
        or plugin_entry != {"enabled": True}
    ):
        raise PluginAuthorityError(
            "codex home config.toml contains unbound settings or registrations"
        )


def _assert_codex_home_scope(codex_home: Path, installed_path: Path) -> None:
    """Allow only the strict config and identity-bound installed cache."""

    root = Path(codex_home).resolve()
    try:
        installed_rel = Path(installed_path).resolve().relative_to(root)
    except ValueError as exc:
        raise PluginAuthorityError("installed path escapes codex home") from exc
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            relative = (Path(dirpath) / name).relative_to(root)
            if relative == Path(CONFIG_TOML_NAME):
                continue
            try:
                relative.relative_to(installed_rel)
            except ValueError as exc:
                raise PluginAuthorityError(
                    f"codex home contains unbound file: {relative.as_posix()}"
                ) from exc


def validate_deployment_slot(
    receipt: DeploymentReceipt, *, codex_home_root: Path
) -> None:
    """Verify every real path and every recorded digest for a single slot.

    Shared by :func:`resolve_current_authority` (after pointer + identity
    checks) and the same-content idempotent republish branch in
    ``cvm_install_plugin._publish_under_lock``. Both callers must fail
    closed if any recorded digest, critical runtime probe, config.toml
    binding, or lexical path shape drifts from the receipt.
    """

    codex_home_root = Path(codex_home_root).expanduser()
    root = authority_root(codex_home_root)
    _lexical_stat(root, label="authority root", expect="dir")
    dep_root = _lexical_child(root, DEPLOYMENTS_DIRNAME)
    _lexical_stat(dep_root, label="deployments root", expect="dir")

    expected_deployment_dir = _lexical_child(dep_root, receipt.deployment_id)
    if str(receipt.deployment_dir) != str(expected_deployment_dir):
        raise PluginAuthorityError(
            "receipt deployment_dir does not match deployment id: "
            f"{receipt.deployment_dir} vs {expected_deployment_dir}"
        )
    _lexical_stat(expected_deployment_dir, label="deployment directory", expect="dir")

    expected_publish_tree = _lexical_child(
        expected_deployment_dir, PUBLISH_TREE_DIRNAME
    )
    expected_codex_home = _lexical_child(
        expected_deployment_dir, CODEX_HOME_DIRNAME
    )
    if str(receipt.publish_tree) != str(expected_publish_tree):
        raise PluginAuthorityError(
            "receipt publish_tree escapes the deployment directory: "
            f"{receipt.publish_tree}"
        )
    if str(receipt.codex_home) != str(expected_codex_home):
        raise PluginAuthorityError(
            "receipt codex_home escapes the deployment directory: "
            f"{receipt.codex_home}"
        )
    _lexical_stat(expected_publish_tree, label="publish tree", expect="dir")
    _lexical_stat(expected_codex_home, label="codex home", expect="dir")

    installed_path = receipt.installed_path
    installed_str = str(installed_path)
    codex_home_str = str(expected_codex_home)
    if not (
        installed_str == codex_home_str
        or installed_str.startswith(codex_home_str + os.sep)
    ):
        raise PluginAuthorityError(
            "receipt installed_path escapes the codex home: "
            f"{installed_path}"
        )
    _lexical_stat(installed_path, label="installed path", expect="dir")

    receipt_path = _lexical_child(expected_deployment_dir, RECEIPT_FILE)
    _lexical_stat(receipt_path, label="deployment receipt", expect="file")
    on_disk_receipt = read_receipt(receipt_path)
    if on_disk_receipt.as_dict() != receipt.as_dict():
        raise PluginAuthorityError(
            "in-memory receipt and on-disk deployment receipt disagree"
        )

    expected_id = compute_deployment_id(
        receipt.prepared_manifest_digest,
        receipt.version,
        receipt.transfer_provenance,
    )
    if expected_id != receipt.deployment_id:
        raise PluginAuthorityError(
            "deployment id does not match content-bound recomputation"
        )

    _reject_symlinks_below(expected_publish_tree, label="publish tree")
    _reject_symlinks_below(expected_codex_home, label="codex home")

    prepared_digest, _ = _compute_manifest_digest(expected_publish_tree)
    if prepared_digest != receipt.prepared_manifest_digest:
        raise PluginAuthorityError(
            "publish-tree manifest digest recompute differs from receipt: "
            f"{prepared_digest} vs {receipt.prepared_manifest_digest}"
        )
    installed_digest, _ = _compute_manifest_digest(installed_path)
    if installed_digest != receipt.installed_manifest_digest:
        raise PluginAuthorityError(
            "installed cache manifest digest recompute differs from receipt: "
            f"{installed_digest} vs {receipt.installed_manifest_digest}"
        )
    if installed_digest != prepared_digest:
        raise PluginAuthorityError(
            "installed cache manifest differs from the identity-bound publish tree"
        )
    codex_home_digest, _ = _compute_manifest_digest(
        expected_codex_home, private_paths=(CONFIG_TOML_NAME,)
    )
    if codex_home_digest != receipt.codex_home_manifest_digest:
        raise PluginAuthorityError(
            "codex home manifest digest recompute differs from receipt: "
            f"{codex_home_digest} vs {receipt.codex_home_manifest_digest}"
        )
    _assert_registration_intact(
        expected_codex_home,
        expected_marketplace_source=expected_publish_tree,
    )
    _assert_codex_home_scope(expected_codex_home, installed_path)

    observed_critical = _recompute_critical_runtimes(installed_path)
    observed_map = {
        (item["runtime"], item["probe"]): item["probe_sha256"]
        for item in observed_critical
    }
    recorded_map = {
        (item["runtime"], item["probe"]): item["probe_sha256"]
        for item in receipt.critical_runtimes
    }
    if observed_map != recorded_map:
        raise PluginAuthorityError(
            "installed critical-runtime probes disagree with recorded receipt"
        )


def resolve_current_authority(codex_home_root: Path) -> DeploymentReceipt:
    """Read the atomic ``current.json`` pointer and validate every real path.

    Fails closed on:

    * missing / non-regular / symlinked pointer;
    * schema, digest, and identity-binding mismatch;
    * symlinked ancestor chain from ``codex_home_root`` down to authority
      root, deployments root, deployment dir, publish tree, codex home,
      installed cache;
    * any prepared-tree manifest digest that no longer matches the actual
      bytes on disk (recomputed) or any installed-tree manifest digest that no
      longer matches;
    * any codex home / config.toml tampering (whole-tree recompute plus
      parsed marketplace + plugin registration invariants);
    * any critical runtime file that no longer materializes.
    """

    codex_home_root = Path(codex_home_root).expanduser()
    root = authority_root(codex_home_root)
    _lexical_stat(root, label="authority root", expect="dir")
    dep_root = _lexical_child(root, DEPLOYMENTS_DIRNAME)
    _lexical_stat(dep_root, label="deployments root", expect="dir")
    pointer = _lexical_child(dep_root, POINTER_NAME)
    _lexical_stat(pointer, label="authority pointer", expect="file")

    pointer_receipt = read_receipt(pointer)
    guarded_paths = (
        root,
        dep_root,
        Path(pointer_receipt.deployment_dir),
        Path(pointer_receipt.publish_tree),
        Path(pointer_receipt.codex_home),
        Path(pointer_receipt.installed_path),
    )
    guarded_inodes: list[tuple[int, int]] = []
    for path in guarded_paths:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise PluginAuthorityError(
                f"authority path is inaccessible: {path}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise PluginAuthorityError(f"authority path is a symlink: {path}")
        guarded_inodes.append((metadata.st_dev, metadata.st_ino))
    validate_deployment_slot(pointer_receipt, codex_home_root=codex_home_root)
    for path, expected_inode in zip(guarded_paths, guarded_inodes, strict=True):
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise PluginAuthorityError(
                f"authority path changed during validation: {path}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or (
            metadata.st_dev,
            metadata.st_ino,
        ) != expected_inode:
            raise PluginAuthorityError(
                f"authority path changed during validation: {path}"
            )
    return pointer_receipt


def installed_skills_root(receipt: DeploymentReceipt) -> Path:
    """Return the installed plugin cache's skills directory."""

    root = Path(receipt.installed_path) / "skills"
    if not root.is_dir():
        raise PluginAuthorityError(
            f"installed plugin cache has no skills directory: {root}"
        )
    return root


def installed_codex_home(receipt: DeploymentReceipt) -> Path:
    """Return the isolated CODEX_HOME that holds the installed plugin."""

    return Path(receipt.codex_home)


def resolved_skill_directories(receipt: DeploymentReceipt) -> list[Path]:
    """Return the immediate ``SKILL.md``-bearing children of the cache.

    The pilot still binds these under ``/workspace/repo/skills/<name>`` so
    ad-hoc script entrypoints (canonical-build, mesh-compare) resolve; Codex
    itself does not read from this path but from the installed CODEX_HOME.
    """

    return skill_directories_under_installed(Path(receipt.installed_path))


def skill_directories_under_installed(installed_path: Path) -> list[Path]:
    root = Path(installed_path) / "skills"
    if not root.is_dir() or root.is_symlink():
        raise PluginAuthorityError(
            f"installed plugin cache has no skills directory: {root}"
        )
    skills: list[Path] = []
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        if not entry.is_dir() or entry.is_symlink():
            continue
        if (entry / "SKILL.md").is_file():
            skills.append(entry)
    if not skills:
        raise PluginAuthorityError(
            f"installed plugin cache has no runnable skills under {root}"
        )
    return skills


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
    created = False
    try:
        descriptor = os.open(
            tmp,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o644,
        )
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if created:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def _publish_pointer_locked(
    receipt: DeploymentReceipt,
    *,
    codex_home_root: Path,
    verify_lock: Callable[[], None] | None = None,
) -> Path:
    """Publish ``current.json`` assuming the caller already holds the lock.

    The caller MUST hold an exclusive ``flock`` on
    ``deployments/.publish.lock``. Split out from :func:`publish_authority` so
    that ``cvm_install_plugin`` — which acquires the lock once at the top of
    its publish transaction — can chain multiple state mutations (deployment
    slot rename, pointer swap) under the same lock without reacquiring it and
    self-deadlocking on ``flock`` (two OFDs on the same file both requesting
    ``LOCK_EX`` block indefinitely).

    Same-content republication is idempotent: if the existing pointer already
    holds the identical receipt document, no rewrite happens.
    """

    root = ensure_authority_root(codex_home_root)
    pointer = _lexical_child(root, POINTER_NAME)
    _reject_preexisting_symlink(pointer, label="authority pointer")
    on_disk_path = Path(receipt.deployment_dir) / RECEIPT_FILE
    _lexical_stat(on_disk_path, label="deployment receipt", expect="file")
    on_disk = read_receipt(on_disk_path)
    if on_disk.as_dict() != receipt.as_dict():
        raise PluginAuthorityError(
            "cannot publish: in-memory receipt differs from deployment.receipt.json"
        )
    if pointer.exists() and not pointer.is_symlink() and pointer.is_file():
        existing = read_receipt(pointer)
        if existing.as_dict() == receipt.as_dict():
            return pointer
    if verify_lock is not None:
        verify_lock()
    _atomic_write_json(pointer, receipt.as_dict())
    return pointer


def publish_authority(
    receipt: DeploymentReceipt,
    *,
    codex_home_root: Path,
) -> Path:
    """Atomically publish ``current.json`` under a publication lock.

    The caller must have already assembled the deployment directory referenced
    by ``receipt.deployment_dir`` including the on-disk ``deployment.receipt.json``.
    ``publish_authority`` never mutates that content; it only writes the top-level
    pointer atomically. Same-content republications are idempotent when the
    pointer already resolves to the identical receipt document.

    This helper acquires the publication lock for standalone callers. Publishers
    that already hold the lock as part of a larger transaction must invoke
    :func:`_publish_pointer_locked` directly to avoid re-entering the flock.
    """

    root = ensure_authority_root(codex_home_root)
    lock = _lexical_child(root, LOCK_NAME)
    with publication_lock(lock) as verify_lock:
        return _publish_pointer_locked(
            receipt,
            codex_home_root=codex_home_root,
            verify_lock=verify_lock,
        )


def prepare_deployment_slot(
    codex_home_root: Path,
    deployment_id: str,
) -> tuple[Path, Path, Path]:
    """Return the target deployment dir plus its publish-tree and codex-home paths.

    The directory is not created here; the caller assembles it in a staging
    location and atomically renames into ``deployment_directory``. This helper
    only computes the canonical paths so publisher and consumer share one shape.
    """

    deployment_dir = deployment_directory(codex_home_root, deployment_id)
    publish_tree = _lexical_child(deployment_dir, PUBLISH_TREE_DIRNAME)
    codex_home = _lexical_child(deployment_dir, CODEX_HOME_DIRNAME)
    return deployment_dir, publish_tree, codex_home


def move_into_place(staging_dir: Path, target_dir: Path) -> None:
    """Atomically rename an assembled staging directory into its final slot.

    ``target_dir`` must be inside the deployments root and must not already
    exist. The rename is atomic on POSIX when both sides share a filesystem;
    the caller is responsible for placing ``staging_dir`` on the same one.
    """

    staging = Path(staging_dir)
    target = Path(target_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(staging, target)
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
            raise PluginAuthorityError(
                f"deployment slot already exists: {target}"
            ) from exc
        raise


_TOML_MARKETPLACE_HEADER = f"[marketplaces.{MARKETPLACE_NAME}]"
_TOML_PLUGIN_HEADER = f'[plugins."{PLUGIN_SELECTOR}"]'
_TOML_SOURCE_KEY = re.compile(r"\s*source\s*=")


def _rewrite_marketplace_source(config_text: str, new_source: str) -> str:
    """Point the local marketplace at ``new_source`` while preserving structure.

    We rewrite the single ``source = "..."`` line inside the
    ``[marketplaces.text-to-cad]`` section rather than reserializing the file:
    Codex 0.147.0's config.toml has a stable narrow layout, and a targeted
    line rewrite avoids reordering other sections or touching the
    ``[plugins."cad@text-to-cad"] enabled = true`` registration.

    Only the exact TOML key ``source`` (optionally surrounded by whitespace)
    is matched. Sibling keys such as ``source_type = "local"`` share the
    ``source`` prefix but are distinct assignments and must be preserved
    byte-for-byte — otherwise a naive ``startswith("source")`` collapses them
    into a second ``source = "..."`` line and Codex rejects the whole
    ``CODEX_HOME`` with ``config.toml: duplicate key``. Exactly one ``source``
    assignment must be present; zero or multiple fail closed rather than
    silently produce an unusable config.
    """

    lines = config_text.splitlines(keepends=True)
    inside = False
    header_seen = False
    encoded_source = json.dumps(new_source, ensure_ascii=False)
    rewritten: list[str] = []
    updated = 0
    for line in lines:
        stripped_header = line.strip()
        if stripped_header.startswith("[") and stripped_header.endswith("]"):
            inside = stripped_header == _TOML_MARKETPLACE_HEADER
            if inside:
                header_seen = True
            rewritten.append(line)
            continue
        # Strip only trailing newline so leading indentation and any inline
        # comment on the ``source`` line are considered part of the key match.
        line_no_newline = line.rstrip("\r\n")
        if inside and _TOML_SOURCE_KEY.match(line_no_newline):
            newline = line[len(line_no_newline):] or "\n"
            rewritten.append(f"source = {encoded_source}{newline}")
            updated += 1
            continue
        rewritten.append(line)
    if not header_seen:
        raise PluginAuthorityError(
            "materialized codex home lacks the local marketplace section"
        )
    if updated == 0:
        raise PluginAuthorityError(
            "materialized codex home lacks a marketplace source line"
        )
    if updated > 1:
        raise PluginAuthorityError(
            "materialized codex home has multiple marketplace source lines "
            f"({updated} found); refusing to rewrite"
        )
    return "".join(rewritten)


def _merge_extra_toml(config_text: str, extra_toml: str) -> str:
    """Append caller-supplied provider TOML, refusing to touch registration."""

    if extra_toml is None or not extra_toml.strip():
        return config_text
    if _TOML_MARKETPLACE_HEADER in extra_toml or _TOML_PLUGIN_HEADER in extra_toml:
        raise PluginAuthorityError(
            "extra config.toml fragment may not touch marketplace or plugin registration"
        )
    prefix = config_text if config_text.endswith("\n") or not config_text else config_text + "\n"
    body = extra_toml if extra_toml.endswith("\n") else extra_toml + "\n"
    return prefix + body


def _copy_tree(source: Path, target: Path) -> None:
    """Deep-copy source into target, rejecting any symlink encountered."""

    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        rel = Path(dirpath).relative_to(source)
        dest_dir = target / rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in dirnames:
            src = Path(dirpath) / name
            if src.is_symlink():
                raise PluginAuthorityError(
                    f"authority tree contains a symlink: {src}"
                )
        for name in filenames:
            src = Path(dirpath) / name
            if src.is_symlink():
                raise PluginAuthorityError(
                    f"authority tree contains a symlink: {src}"
                )
            shutil.copy2(src, dest_dir / name)


def materialize_job_codex_home(
    receipt: DeploymentReceipt,
    target: Path,
    *,
    extra_toml: str | None = None,
    sandbox_marketplace_source: str | None = SANDBOX_MARKETPLACE_SOURCE,
) -> Path:
    """Copy the authority CODEX_HOME into a job-private writable directory.

    The complete copy is manifest-verified before its job-specific config is
    rewritten, so a corrupted copy or torn write cannot silently regress the
    pilot below the authority it claims to materialize. The marketplace
    ``source`` in the copy's ``config.toml`` is rewritten to
    ``sandbox_marketplace_source`` (or left
    alone when the caller passes ``None``) so the sandbox does not depend on
    the host authority absolute path. Extra provider TOML is appended without
    touching ``[marketplaces.*]`` or ``[plugins.*]`` registration.
    """

    target = Path(target)
    if target.exists():
        raise PluginAuthorityError(f"job codex home target already exists: {target}")
    source_home = Path(receipt.codex_home)
    if not source_home.is_dir():
        raise PluginAuthorityError(f"authority codex home is missing: {source_home}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    try:
        _copy_tree(source_home, target)
        recopy_digest, _ = _compute_manifest_digest(
            target, private_paths=(CONFIG_TOML_NAME,)
        )
        if recopy_digest != receipt.codex_home_manifest_digest:
            raise PluginAuthorityError(
                "materialized codex home manifest differs from authority receipt"
            )
        config_path = target / "config.toml"
        if not config_path.is_file():
            raise PluginAuthorityError(
                "materialized codex home is missing config.toml"
            )
        config = config_path.read_text(encoding="utf-8")
        if sandbox_marketplace_source is not None:
            config = _rewrite_marketplace_source(config, sandbox_marketplace_source)
        config = _merge_extra_toml(config, extra_toml or "")
        config_path.write_text(config, encoding="utf-8")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def materialize_job_publish_tree(
    receipt: DeploymentReceipt,
    target: Path,
) -> Path:
    """Copy the verified publish tree into an immutable job-private snapshot."""

    target = Path(target)
    if target.exists() or target.is_symlink():
        raise PluginAuthorityError(
            f"job publish-tree target already exists: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    try:
        _copy_tree(Path(receipt.publish_tree), target)
        digest, _ = _compute_manifest_digest(target)
        if digest != receipt.prepared_manifest_digest:
            raise PluginAuthorityError(
                "materialized publish tree differs from authority receipt"
            )
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def render_venus_provider_toml(base_url: str, bearer_token: str) -> str:
    """Return provider TOML injected into a job codex home for cvm_agent.

    The value is safely encoded via ``json.dumps`` so a hostile bearer or
    proxy URL cannot escape TOML string quoting.
    """

    return (
        'model_provider = "venus"\n'
        "[model_providers.venus]\n"
        'name = "Venus GPT-5.6-sol"\n'
        f"base_url = {json.dumps(base_url)}\n"
        'wire_api = "responses"\n'
        f"experimental_bearer_token = {json.dumps(bearer_token)}\n"
    )
