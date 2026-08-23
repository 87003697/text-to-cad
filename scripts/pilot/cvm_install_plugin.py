#!/usr/bin/env python3
"""CVM-side plugin publisher invoked over SSH from ``cvm-push``.

Copies the just-transferred ``~/text-to-cad/`` bytes into a staging area under
``~/.text-to-cad-codex/deployments/``, runs the repository's canonical
``scripts/release/finalize-publish-tree.sh`` there to produce a symlink-free
production publish tree, installs it through the real Codex plugin CLI into an
isolated ``CODEX_HOME``, verifies the installed cache against the prepared
tree, atomically publishes the ``current.json`` authority pointer, and prints
the JSON authority receipt fragment on stdout for ``cvm_push`` to embed.

The Codex CLI version is gated to ``>= 0.142.0`` before any marketplace
mutation touches the authority: an out-of-range CLI could silently omit skill
symlinks or emit a subtly different install layout, so we refuse to install
against it in the first place.

Push provenance (the Mac source branch, Git head, and dirty flag observed by
``cvm_push`` *before* the rsync of transferred bytes) is supplied as a strict
URL-safe base64 canonical-JSON blob via ``--provenance-b64`` and bound into
the deployment receipt so a consumer can trace an authority pointer back to
the exact Mac working tree that produced it. The publisher never inspects the
CVM checkout's git state — that would just tell us the CVM already got the
bytes, not who sent them.

Failures print a JSON error fragment with a ``stage`` field so the caller can
map them to exit codes 7 (install failed) and 8 (verify failed). The prior
``current.json`` pointer is never touched on failure.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import errno
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pilot import plugin_deployment  # noqa: E402
from scripts.release import smoke_installed_plugin as smoke  # noqa: E402


PLUGIN_SELECTOR = "cad@text-to-cad"
FINALIZE_SCRIPT_REL = Path("scripts/release/finalize-publish-tree.sh")
ERROR_SCHEMA = "text-to-cad.plugin-authority-error/1"

MIN_CODEX_VERSION = (0, 142, 0)
MAX_PROVENANCE_BYTES = 4096


class InstallError(RuntimeError):
    """A failure attributable to install or verify."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        if stage not in {"install", "verify"}:
            raise ValueError(f"invalid stage: {stage}")
        self.stage = stage


def _read_version(source: Path) -> str:
    version_path = source / "VERSION"
    try:
        raw = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise InstallError(
            f"cannot read VERSION at {version_path}: {exc}", stage="install"
        ) from exc
    if not raw:
        raise InstallError(f"VERSION is empty: {version_path}", stage="install")
    return raw


def _parse_version_triple(raw: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
    if match is None:
        raise InstallError(
            f"cannot parse codex CLI version from {raw!r}", stage="install"
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _ensure_codex_version_gate(codex_executable: str) -> str:
    """Assert the CLI meets the minimum before it touches the authority.

    Running this gate *before* ``codex plugin marketplace add`` is what makes
    the guarantee load-bearing: even a same-shell CLI that would otherwise
    silently omit critical runtimes never gets to write into an isolated
    codex home, so a downstream ``resolve_current_authority`` recompute would
    catch the divergence but so does this cheaper check.
    """

    try:
        version_string = smoke.codex_version(codex_executable)
    except smoke.SmokeError as exc:
        raise InstallError(str(exc), stage="install") from exc
    triple = _parse_version_triple(version_string)
    if triple < MIN_CODEX_VERSION:
        raise InstallError(
            f"codex CLI {version_string} is below required "
            f"{'.'.join(str(v) for v in MIN_CODEX_VERSION)}",
            stage="install",
        )
    return version_string


def _materialize_publish_source(
    src: Path, dst: Path, *, expected_manifest_digest: str
) -> None:
    """Copy exactly the manifest-listed files from ``src`` into ``dst``.

    ``src`` is the persistent ``~/text-to-cad`` overlay on the CVM. Because
    ``cvm-push`` never uses ``rsync --delete``, that overlay accumulates every
    file any prior push, pilot, or ad-hoc SSH session ever left there. An
    exclusion list can never guarantee identity against that overlay — a
    tracked file that existed in an earlier push and later disappeared from
    the Mac stage would simply linger. Instead, we materialize publish-tree-src
    from exactly the paths listed in the stage manifest whose digest was
    bound into the push provenance, hashing each file at copy time and failing
    closed on any absent/symlinked/mismatched entry.
    """

    try:
        plugin_deployment.materialize_from_stage_manifest(
            src,
            dst,
            expected_manifest_digest=expected_manifest_digest,
        )
    except plugin_deployment.PluginAuthorityError as exc:
        raise InstallError(str(exc), stage="install") from exc


def _run_finalize(publish_tree: Path) -> None:
    script = publish_tree / FINALIZE_SCRIPT_REL
    if not script.is_file():
        raise InstallError(
            f"finalize-publish-tree.sh missing in publish staging: {script}",
            stage="install",
        )
    result = subprocess.run(
        ["bash", str(script), "--tree", str(publish_tree)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise InstallError(
            "finalize-publish-tree.sh failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            stage="install",
        )


def _atomic_receipt(path: Path, value: dict[str, Any]) -> None:
    plugin_deployment._atomic_write_json(path, value)


def _rmtree_force(path: Path) -> None:
    def onerror(func, target, exc_info):
        exc = exc_info[1]
        if isinstance(exc, FileNotFoundError):
            return
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
        try:
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


def _remove_codex_cli_tmp(codex_home: Path) -> None:
    """Drop task-local CLI scratch state before sealing CODEX_HOME."""

    cli_tmp = codex_home / "tmp"
    try:
        metadata = os.lstat(cli_tmp)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise InstallError(f"cannot inspect Codex CLI tmp: {exc}", stage="verify") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise InstallError("Codex CLI tmp is not a physical directory", stage="verify")
    try:
        shutil.rmtree(cli_tmp)
    except OSError as exc:
        raise InstallError(f"cannot remove Codex CLI tmp: {exc}", stage="verify") from exc


def _decode_provenance(encoded: str | None) -> dict[str, Any]:
    """Decode the caller-provided base64url canonical JSON provenance.

    The transport is one small argv value on the SSH command line so we do not
    need a second scp/rsync exchange, but this means the encoded string must be
    strictly bounded and strictly typed. Any decoding, JSON, or schema failure
    fails the install stage — a missing provenance is treated the same as an
    invalid one because a pilot must never end up with an unauditable receipt.
    """

    if encoded is None or not encoded.strip():
        raise InstallError(
            "provenance blob is required (missing --provenance-b64)",
            stage="install",
        )
    if len(encoded) > MAX_PROVENANCE_BYTES:
        raise InstallError(
            f"provenance blob exceeds {MAX_PROVENANCE_BYTES}-byte limit",
            stage="install",
        )
    if not re.fullmatch(r"[A-Za-z0-9_\-=]+", encoded):
        raise InstallError(
            "provenance blob is not URL-safe base64", stage="install"
        )
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise InstallError(
            f"provenance blob failed base64 decode: {exc}", stage="install"
        ) from exc
    try:
        document = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError(
            f"provenance blob is not canonical JSON: {exc}", stage="install"
        ) from exc
    try:
        return plugin_deployment._validate_transfer_provenance(document)
    except plugin_deployment.PluginAuthorityError as exc:
        raise InstallError(str(exc), stage="install") from exc


def publish(
    transferred_source: Path,
    codex_home_root: Path,
    *,
    provenance: dict[str, Any],
    codex_executable: str = "codex",
) -> dict[str, Any]:
    """Assemble one deployment slot and atomically publish its pointer."""

    transferred_source = Path(transferred_source).expanduser()
    if not transferred_source.is_absolute():
        transferred_source = Path.cwd() / transferred_source
    # The parent is host-owned/trusted; canonicalize it, but preserve and
    # lstat the transfer-root leaf so a redirected ~/text-to-cad is rejected.
    transferred_source = transferred_source.parent.resolve() / transferred_source.name
    codex_home_root = Path(codex_home_root).expanduser()
    try:
        transferred_metadata = os.lstat(transferred_source)
    except OSError as exc:
        raise InstallError(
            f"transferred source is inaccessible: {transferred_source}: {exc}",
            stage="install",
        ) from exc
    if stat.S_ISLNK(transferred_metadata.st_mode) or not stat.S_ISDIR(
        transferred_metadata.st_mode
    ):
        raise InstallError(
            f"transferred source is not a physical directory: {transferred_source}",
            stage="install",
        )
    codex_version_string = _ensure_codex_version_gate(codex_executable)

    deployments_root = plugin_deployment.ensure_authority_root(codex_home_root)
    lock_file = deployments_root / plugin_deployment.LOCK_NAME
    source_sha = provenance["mac_head"]

    try:
        with plugin_deployment.publication_lock(lock_file) as verify_lock:
            return _publish_under_lock(
                transferred_source=transferred_source,
                codex_home_root=codex_home_root,
                deployments_root=deployments_root,
                provenance=provenance,
                source_sha=source_sha,
                codex_version_string=codex_version_string,
                codex_executable=codex_executable,
                verify_lock=verify_lock,
            )
    except plugin_deployment.PluginAuthorityError as exc:
        raise InstallError(str(exc), stage="install") from exc


def _publish_under_lock(
    *,
    transferred_source: Path,
    codex_home_root: Path,
    deployments_root: Path,
    provenance: dict[str, Any],
    source_sha: str,
    codex_version_string: str,
    codex_executable: str,
    verify_lock,
) -> dict[str, Any]:
    staging_dir = deployments_root / f".staging-{secrets.token_hex(8)}"
    staging_dir.mkdir(mode=0o755)
    keep_staging = False
    try:
        publish_src = staging_dir / "publish-tree-src"
        _materialize_publish_source(
            transferred_source,
            publish_src,
            expected_manifest_digest=provenance["stage_manifest_digest"],
        )
        _run_finalize(publish_src)
        version = _read_version(publish_src)
        prepared_manifest = smoke.compute_manifest(publish_src)
        prepared_digest = prepared_manifest.digest
        deployment_id = plugin_deployment.compute_deployment_id(
            prepared_digest,
            version,
            provenance,
        )
        target_dir = plugin_deployment.deployment_directory(
            codex_home_root, deployment_id
        )

        if target_dir.is_dir():
            existing_receipt_path = target_dir / plugin_deployment.RECEIPT_FILE
            if existing_receipt_path.is_file():
                existing = plugin_deployment.read_receipt(existing_receipt_path)
                if (
                    existing.prepared_manifest_digest == prepared_digest
                    and existing.version == version
                ):
                    # Same content-bound identity is not enough: the slot
                    # on disk may have been tampered with (config.toml
                    # ``enabled = false``, critical runtime deletion, etc.)
                    # between the previous publication and now. Fully
                    # revalidate the existing slot before republishing
                    # its pointer; anything divergent maps to verify-stage
                    # (exit 8) rather than silently reusing a broken slot.
                    try:
                        plugin_deployment.validate_deployment_slot(
                            existing, codex_home_root=codex_home_root
                        )
                    except plugin_deployment.PluginAuthorityError as exc:
                        raise InstallError(
                            f"existing deployment slot failed revalidation: {exc}",
                            stage="verify",
                        ) from exc
                    plugin_deployment._publish_pointer_locked(
                        existing,
                        codex_home_root=codex_home_root,
                        verify_lock=verify_lock,
                    )
                    return existing.as_dict()
            raise InstallError(
                "existing deployment slot has divergent receipt: "
                f"{target_dir}",
                stage="verify",
            )

        publish_tree_final = staging_dir / plugin_deployment.PUBLISH_TREE_DIRNAME
        codex_home_final = staging_dir / plugin_deployment.CODEX_HOME_DIRNAME
        os.rename(publish_src, publish_tree_final)
        codex_home_final.mkdir(mode=0o700)

        try:
            install_result = smoke.install_plugin_isolated(
                publish_tree_final,
                codex_home_final,
                codex_executable=codex_executable,
                plugin_selector=PLUGIN_SELECTOR,
            )
        except smoke.SmokeError as exc:
            raise InstallError(str(exc), stage="install") from exc

        _remove_codex_cli_tmp(codex_home_final)

        staged_installed_path = Path(install_result["installed_path"]).resolve()
        try:
            installed_rel = staged_installed_path.relative_to(
                codex_home_final.resolve()
            )
        except ValueError as exc:
            raise InstallError(
                f"installed path escapes staging codex home: {staged_installed_path}",
                stage="install",
            ) from exc

        try:
            installed_manifest = smoke.compute_manifest(staged_installed_path)
            smoke.assert_manifests_equal(prepared_manifest, installed_manifest)
            critical_runtimes = smoke.assert_critical_runtimes(staged_installed_path)
        except smoke.SmokeError as exc:
            raise InstallError(str(exc), stage="verify") from exc

        final_publish_tree = target_dir / plugin_deployment.PUBLISH_TREE_DIRNAME
        final_codex_home = target_dir / plugin_deployment.CODEX_HOME_DIRNAME
        final_installed_path = final_codex_home / installed_rel

        config_path = codex_home_final / plugin_deployment.CONFIG_TOML_NAME
        try:
            config = config_path.read_text(encoding="utf-8")
            config = plugin_deployment._rewrite_marketplace_source(
                config, str(final_publish_tree)
            )
            config_path.write_text(config, encoding="utf-8")
        except (OSError, plugin_deployment.PluginAuthorityError) as exc:
            raise InstallError(
                f"cannot bind marketplace registration to final publish tree: {exc}",
                stage="verify",
            ) from exc

        # Bind the whole isolated CODEX_HOME, including config.toml and the
        # installed cache. Semantic registration checks below additionally
        # reject a disabled plugin or redirected marketplace.
        try:
            codex_home_manifest = smoke.compute_manifest(
                codex_home_final,
                private_paths=(plugin_deployment.CONFIG_TOML_NAME,),
            )
        except smoke.SmokeError as exc:
            raise InstallError(str(exc), stage="verify") from exc
        try:
            plugin_deployment._assert_registration_intact(
                codex_home_final,
                expected_marketplace_source=final_publish_tree,
            )
            plugin_deployment._assert_codex_home_scope(
                codex_home_final, staged_installed_path
            )
        except plugin_deployment.PluginAuthorityError as exc:
            raise InstallError(str(exc), stage="verify") from exc

        receipt = plugin_deployment.DeploymentReceipt(
            schema=plugin_deployment.RECEIPT_SCHEMA,
            deployment_id=deployment_id,
            version=version,
            plugin_selector=PLUGIN_SELECTOR,
            prepared_manifest_digest=prepared_digest,
            installed_manifest_digest=installed_manifest.digest,
            codex_home_manifest_digest=codex_home_manifest.digest,
            codex_version=codex_version_string,
            published_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            source_git_sha=source_sha,
            deployment_dir=target_dir,
            publish_tree=final_publish_tree,
            codex_home=final_codex_home,
            installed_path=final_installed_path,
            critical_runtimes=tuple(
                {str(k): str(v) for k, v in item.items()}
                for item in critical_runtimes
            ),
            transfer_provenance=provenance,
        )
        _atomic_receipt(
            staging_dir / plugin_deployment.RECEIPT_FILE,
            receipt.as_dict(),
        )
        try:
            verify_lock()
            plugin_deployment.move_into_place(staging_dir, target_dir)
        except plugin_deployment.PluginAuthorityError:
            # Race: another publisher (or a stale slot) already occupies the
            # target. Because we hold the deployments-root flock this cannot be
            # a concurrent publisher on the same host; treat as an unrecoverable
            # inconsistency and let the retained staging surface for debugging.
            keep_staging = True
            raise InstallError(
                "target deployment slot already exists after finalization: "
                f"{target_dir}",
                stage="verify",
            )
        keep_staging = True
        plugin_deployment._publish_pointer_locked(
            receipt,
            codex_home_root=codex_home_root,
            verify_lock=verify_lock,
        )
        return receipt.as_dict()
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
            keep_staging = True
            raise InstallError(
                f"deployment rename raced with another process: {exc}",
                stage="verify",
            ) from exc
        raise
    finally:
        if not keep_staging and staging_dir.exists():
            _rmtree_force(staging_dir)


def _emit(payload: dict[str, Any], output_json: Path | None) -> None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(body + "\n", encoding="utf-8")
    else:
        print(body)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize the transferred CVM source into a symlink-free publish "
            "tree, install it through the real Codex plugin CLI in an isolated "
            "CODEX_HOME, and atomically publish the shared authority pointer."
        )
    )
    parser.add_argument(
        "--transferred-source",
        type=Path,
        required=True,
        help="Path to the just-transferred CVM checkout (typically ~/text-to-cad).",
    )
    parser.add_argument(
        "--codex-home-root",
        type=Path,
        required=True,
        help="Root that contains .text-to-cad-codex/ (typically the user home).",
    )
    parser.add_argument(
        "--codex",
        default="codex",
        help="Codex CLI executable (default: codex on PATH).",
    )
    parser.add_argument(
        "--provenance-b64",
        required=True,
        help=(
            "URL-safe base64 canonical JSON encoding of the Mac-side push "
            f"provenance document (schema {plugin_deployment.PROVENANCE_SCHEMA})."
        ),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write the JSON authority receipt/error fragment.",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        provenance = _decode_provenance(args.provenance_b64)
        receipt = publish(
            args.transferred_source,
            args.codex_home_root,
            provenance=provenance,
            codex_executable=args.codex,
        )
    except InstallError as exc:
        _emit(
            {"schema": ERROR_SCHEMA, "stage": exc.stage, "error": str(exc)},
            args.output_json,
        )
        return 1
    except (plugin_deployment.PluginAuthorityError, smoke.SmokeError) as exc:
        _emit(
            {"schema": ERROR_SCHEMA, "stage": "verify", "error": str(exc)},
            args.output_json,
        )
        return 1
    _emit(receipt, args.output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
