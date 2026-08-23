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
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


AUTHORITY_ROOT_NAME = ".text-to-cad-codex"
DEPLOYMENTS_DIRNAME = "deployments"
POINTER_NAME = "current.json"
LOCK_NAME = ".publish.lock"
RECEIPT_FILE = "deployment.receipt.json"
PUBLISH_TREE_DIRNAME = "publish-tree"
CODEX_HOME_DIRNAME = "codex-home"
RECEIPT_SCHEMA = "text-to-cad.plugin-authority/1"
MARKETPLACE_NAME = "text-to-cad"
PLUGIN_SELECTOR = "cad@text-to-cad"
SANDBOX_MARKETPLACE_SOURCE = "/opt/text-to-cad-publish-tree"


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


def compute_deployment_id(prepared_manifest_digest: str, version: str) -> str:
    """Return the content-bound deployment identity.

    The identity binds the prepared publish-tree manifest digest to the
    canonical repository ``VERSION`` so two publications with identical bytes
    but different declared versions never collide and never masquerade.
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
    payload = prepared_manifest_digest.encode("ascii") + b"\0" + version.encode(
        "utf-8"
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


def ensure_authority_root(codex_home_root: Path) -> Path:
    """Create the authority tree with restrictive permissions if absent."""

    root = authority_root(codex_home_root)
    root.mkdir(parents=True, exist_ok=True)
    deployments = _lexical_child(root, DEPLOYMENTS_DIRNAME)
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
    if schema != "text-to-cad.push-provenance/1":
        raise PluginAuthorityError(
            f"transfer provenance has unexpected schema: {schema!r}"
        )
    required = {"schema", "mac_branch", "mac_head", "mac_state"}
    missing = required - set(value)
    if missing:
        raise PluginAuthorityError(
            f"transfer provenance missing keys: {sorted(missing)}"
        )
    branch = value["mac_branch"]
    head = value["mac_head"]
    state = value["mac_state"]
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
    transfer = value.get("transfer_summary")
    if transfer is not None and not isinstance(transfer, dict):
        raise PluginAuthorityError(
            "transfer provenance transfer_summary is invalid"
        )
    runtime = value.get("runtime_attestation")
    if runtime is not None:
        if not isinstance(runtime, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
            for k, v in runtime.items()
        ):
            raise PluginAuthorityError(
                "transfer provenance runtime_attestation is invalid"
            )
    return dict(value)


def _validate_receipt_shape(value: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "deployment_id",
        "version",
        "plugin_selector",
        "prepared_manifest_digest",
        "installed_manifest_digest",
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
    if value["schema"] != RECEIPT_SCHEMA:
        raise PluginAuthorityError(
            f"authority receipt has unexpected schema: {value['schema']!r}"
        )
    critical = value["critical_runtimes"]
    if not isinstance(critical, list) or not all(
        isinstance(item, dict) for item in critical
    ):
        raise PluginAuthorityError("authority receipt has invalid critical_runtimes")
    _validate_transfer_provenance(value["transfer_provenance"])


def _receipt_from_document(value: Mapping[str, Any]) -> DeploymentReceipt:
    _validate_receipt_shape(value)
    return DeploymentReceipt(
        schema=str(value["schema"]),
        deployment_id=str(value["deployment_id"]),
        version=str(value["version"]),
        plugin_selector=str(value["plugin_selector"]),
        prepared_manifest_digest=str(value["prepared_manifest_digest"]),
        installed_manifest_digest=str(value["installed_manifest_digest"]),
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
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginAuthorityError(f"cannot read receipt {path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PluginAuthorityError(
            f"authority receipt is not valid JSON: {path}"
        ) from exc
    if not isinstance(document, dict):
        raise PluginAuthorityError(f"authority receipt is not a JSON object: {path}")
    return _receipt_from_document(document)


def _compute_manifest_digest(root: Path) -> tuple[str, int]:
    """Return (digest, file_count) of ``root`` reusing the smoke manifest rules.

    Centralizing this call ensures the authority tree and the smoke share one
    canonical manifest definition (regular files only, symlink-free, path +
    sha256 concatenated). A recompute mismatch is exactly the "unrecorded
    byte" attack the P1-2 review flagged.
    """

    from scripts.release import smoke_installed_plugin as smoke

    try:
        manifest = smoke.compute_manifest(Path(root))
    except smoke.SmokeError as exc:
        raise PluginAuthorityError(str(exc)) from exc
    return manifest.digest, len(manifest.entries)


def _recompute_critical_runtimes(installed_path: Path) -> list[dict[str, str]]:
    from scripts.release import smoke_installed_plugin as smoke

    try:
        return smoke.assert_critical_runtimes(Path(installed_path))
    except smoke.SmokeError as exc:
        raise PluginAuthorityError(str(exc)) from exc


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

    expected_deployment_dir = _lexical_child(
        dep_root, pointer_receipt.deployment_id
    )
    if str(pointer_receipt.deployment_dir) != str(expected_deployment_dir):
        raise PluginAuthorityError(
            "pointer deployment_dir does not match deployment id: "
            f"{pointer_receipt.deployment_dir} vs {expected_deployment_dir}"
        )
    _lexical_stat(
        expected_deployment_dir, label="deployment directory", expect="dir"
    )

    expected_publish_tree = _lexical_child(
        expected_deployment_dir, PUBLISH_TREE_DIRNAME
    )
    expected_codex_home = _lexical_child(
        expected_deployment_dir, CODEX_HOME_DIRNAME
    )
    if str(pointer_receipt.publish_tree) != str(expected_publish_tree):
        raise PluginAuthorityError(
            "pointer publish_tree escapes the deployment directory: "
            f"{pointer_receipt.publish_tree}"
        )
    if str(pointer_receipt.codex_home) != str(expected_codex_home):
        raise PluginAuthorityError(
            "pointer codex_home escapes the deployment directory: "
            f"{pointer_receipt.codex_home}"
        )
    _lexical_stat(expected_publish_tree, label="publish tree", expect="dir")
    _lexical_stat(expected_codex_home, label="codex home", expect="dir")

    installed_path = pointer_receipt.installed_path
    installed_str = str(installed_path)
    codex_home_str = str(expected_codex_home)
    if not (
        installed_str == codex_home_str
        or installed_str.startswith(codex_home_str + os.sep)
    ):
        raise PluginAuthorityError(
            "pointer installed_path escapes the codex home: "
            f"{installed_path}"
        )
    _lexical_stat(installed_path, label="installed path", expect="dir")

    receipt_path = _lexical_child(expected_deployment_dir, RECEIPT_FILE)
    _lexical_stat(receipt_path, label="deployment receipt", expect="file")
    on_disk_receipt = read_receipt(receipt_path)
    if on_disk_receipt.as_dict() != pointer_receipt.as_dict():
        raise PluginAuthorityError(
            "pointer receipt and on-disk deployment receipt disagree"
        )

    expected_id = compute_deployment_id(
        pointer_receipt.prepared_manifest_digest, pointer_receipt.version
    )
    if expected_id != pointer_receipt.deployment_id:
        raise PluginAuthorityError(
            "deployment id does not match content-bound recomputation"
        )

    _reject_symlinks_below(expected_publish_tree, label="publish tree")
    _reject_symlinks_below(expected_codex_home, label="codex home")

    prepared_digest, _ = _compute_manifest_digest(expected_publish_tree)
    if prepared_digest != pointer_receipt.prepared_manifest_digest:
        raise PluginAuthorityError(
            "publish-tree manifest digest recompute differs from receipt: "
            f"{prepared_digest} vs {pointer_receipt.prepared_manifest_digest}"
        )
    installed_digest, _ = _compute_manifest_digest(installed_path)
    if installed_digest != pointer_receipt.installed_manifest_digest:
        raise PluginAuthorityError(
            "installed cache manifest digest recompute differs from receipt: "
            f"{installed_digest} vs {pointer_receipt.installed_manifest_digest}"
        )
    observed_critical = _recompute_critical_runtimes(installed_path)
    observed_map = {
        (item["runtime"], item["probe"]): item["probe_sha256"]
        for item in observed_critical
    }
    recorded_map = {
        (item["runtime"], item["probe"]): item["probe_sha256"]
        for item in pointer_receipt.critical_runtimes
    }
    if observed_map != recorded_map:
        raise PluginAuthorityError(
            "installed critical-runtime probes disagree with recorded receipt"
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

    root = installed_skills_root(receipt)
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
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with os.fdopen(
            os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644),
            "w",
            encoding="utf-8",
        ) as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _publish_pointer_locked(
    receipt: DeploymentReceipt,
    *,
    codex_home_root: Path,
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
    on_disk_path = Path(receipt.deployment_dir) / RECEIPT_FILE
    if not on_disk_path.is_file():
        raise PluginAuthorityError(
            f"cannot publish: deployment receipt missing at {on_disk_path}"
        )
    on_disk = read_receipt(on_disk_path)
    if on_disk.as_dict() != receipt.as_dict():
        raise PluginAuthorityError(
            "cannot publish: in-memory receipt differs from deployment.receipt.json"
        )
    if pointer.exists() and not pointer.is_symlink() and pointer.is_file():
        existing = read_receipt(pointer)
        if existing.as_dict() == receipt.as_dict():
            return pointer
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
    with open(lock, "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            return _publish_pointer_locked(
                receipt, codex_home_root=codex_home_root
            )
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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

    The copy is independently manifest-verified against
    ``receipt.installed_manifest_digest`` after materialization so a corrupted
    copy or a torn-write cannot silently regress the pilot below the authority
    it claims to be materializing. The marketplace ``source`` in the copy's
    ``config.toml`` is rewritten to ``sandbox_marketplace_source`` (or left
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
        installed_rel = Path(receipt.installed_path).relative_to(source_home)
        copy_installed = target / installed_rel
        recopy_digest, _ = _compute_manifest_digest(copy_installed)
        if recopy_digest != receipt.installed_manifest_digest:
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
