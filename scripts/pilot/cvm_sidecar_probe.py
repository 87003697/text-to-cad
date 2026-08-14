#!/usr/bin/env python3
"""Provision exact Browser Sidecar images and run one bounded CVM probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_STATE_ROOT = REPO_ROOT / ".cvm-sidecar-probes"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
SOURCE_REVISION = re.compile(r"[0-9a-f]{40}\Z")
HANDLE = re.compile(r"cvmsp-[0-9a-f]{24}\Z")
RESOURCE_ID = re.compile(r"[0-9a-f]{64}\Z")
OWNER_NONCE = re.compile(r"[0-9a-f]{32}\Z")
REMOTE_ROOT = "~/text-to-cad"
MIN_REMOTE_FREE_BYTES = 3 * 1024 * 1024 * 1024
REQUEST = {
    "schema": "meshshot.browser-sidecar.render-request/2",
    "program": "probe",
    "payload": {},
}


class ProbeError(RuntimeError):
    """Closed public failure from the fixed provisioning workflow."""

    def __init__(self, message: str, *, check: str = "workflow") -> None:
        super().__init__(message)
        self.check = check


def _strict_json_loads(text: str, label: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ProbeError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{label} was not JSON") from exc


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _workflow_file_hashes() -> Mapping[str, str]:
    return {
        "module": _sha256_file(Path(__file__).resolve()),
        "wrapper": _sha256_file(Path(__file__).resolve().with_name("cvm-sidecar-probe.sh")),
    }


def _validate_workflow_files(payload: object) -> Mapping[str, str]:
    if not isinstance(payload, dict) or set(payload) != {"module", "wrapper"}:
        raise ProbeError("workflow file attestation is incomplete")
    if not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in payload.values()
    ):
        raise ProbeError("workflow file attestation is not exact SHA-256")
    return payload


def _verify_deployed_workflow(expected: object) -> Mapping[str, str]:
    validated = _validate_workflow_files(expected)
    actual = _workflow_file_hashes()
    if actual != validated:
        raise ProbeError(
            "deployed workflow file hash mismatch",
            check="deployed-workflow-hash",
        )
    return actual


def _remote_disk_gate() -> int:
    free_bytes = shutil.disk_usage(REPO_ROOT).free
    if free_bytes < MIN_REMOTE_FREE_BYTES:
        raise ProbeError(
            "CVM disk below mandatory 3 GiB gate",
            check="remote-disk-gate",
        )
    return free_bytes


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_text,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(
            f"fixed command timed out: {argv[0]}",
            check=f"{Path(argv[0]).name}-timeout",
        ) from exc
    if check and completed.returncode:
        raise ProbeError(f"fixed command failed: {argv[0]} {argv[1]}")
    return completed


def _inspect_image(role: str, image_id: str) -> Mapping[str, object]:
    if IMAGE_ID.fullmatch(image_id) is None:
        raise ProbeError(f"{role} image must be an exact sha256 image ID")
    completed = _run(
        ["docker", "image", "inspect", image_id],
        cwd=REPO_ROOT,
        timeout=60,
    )
    try:
        payload = _strict_json_loads(completed.stdout, f"{role} image inspection")
    except ProbeError as exc:
        raise ProbeError(f"{role} image inspection was not JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ProbeError(f"{role} image inspection was not singular")
    image = payload[0]
    if image.get("Id") != image_id:
        raise ProbeError(f"{role} image ID changed during inspection")
    if image.get("Os") != "linux" or image.get("Architecture") != "amd64":
        raise ProbeError(f"{role} image is not linux/amd64")
    config = image.get("Config")
    if not isinstance(config, dict):
        raise ProbeError(f"{role} image has no immutable config")
    labels = config.get("Labels")
    revision = (
        labels.get("org.opencontainers.image.revision")
        if isinstance(labels, dict)
        else None
    )
    if not isinstance(revision, str):
        raise ProbeError(
            f"{role} image has no immutable source revision label",
            check="image-source-revision",
        )
    return {
        "role": role,
        "id": image_id,
        "platform": "linux/amd64",
        "configSha256": _sha256_bytes(_canonical_json(config)),
        "sourceRevision": revision,
    }


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical_json(payload) + b"\n")
    os.replace(temporary, path)


def _inspect_workflow_source() -> str:
    top = _run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=REPO_ROOT,
        timeout=30,
    ).stdout.strip()
    try:
        if Path(top).resolve() != REPO_ROOT.resolve():
            raise ProbeError("workflow source is not this repository checkout")
    except OSError as exc:
        raise ProbeError("workflow repository identity cannot be resolved") from exc
    head = _run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, timeout=30
    ).stdout.strip()
    if SOURCE_REVISION.fullmatch(head) is None:
        raise ProbeError("workflow source has no exact Git HEAD")
    status = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        timeout=60,
    ).stdout
    if status:
        raise ProbeError(
            "workflow source must be clean before archive preparation",
            check="workflow-source-clean",
        )
    return head


def _claim_once(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ProbeError(f"operation already claimed: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_json(payload) + b"\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _load_receipt(handle: str, name: str, schema: str) -> Mapping[str, Any]:
    _validate_handle(handle)
    path = LOCAL_STATE_ROOT / handle / name
    try:
        payload = _strict_json_loads(
            path.read_text(encoding="utf-8"), f"fixed {name} receipt"
        )
    except (OSError, ProbeError) as exc:
        raise ProbeError(f"cannot read fixed {name} receipt") from exc
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise ProbeError(f"invalid fixed {name} receipt")
    if payload.get("handle") != handle:
        raise ProbeError(f"{name} receipt handle mismatch")
    return payload


def _validate_handle(handle: str) -> None:
    if HANDLE.fullmatch(handle) is None:
        raise ProbeError("handle is not a fixed cvmsp identity")


def _remote(
    operation: str,
    handle: str,
    *,
    check: bool = True,
    owner_nonce: str | None = None,
    workflow_files: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    _validate_handle(handle)
    if operation not in {
        "remote-begin",
        "remote-provision",
        "remote-abort",
        "remote-probe",
    }:
        raise ProbeError("remote operation is not registered")
    arguments = [operation, handle]
    if operation == "remote-begin":
        validated_files = _validate_workflow_files(workflow_files)
        arguments.extend([validated_files["module"], validated_files["wrapper"]])
    elif operation in {"remote-provision", "remote-abort"}:
        if owner_nonce is None or OWNER_NONCE.fullmatch(owner_nonce) is None:
            raise ProbeError("remote ownership nonce is invalid")
        arguments.append(owner_nonce)
    command = f"cd {REMOTE_ROOT} && python3 -m scripts.pilot.cvm_sidecar_probe " + " ".join(
        shlex.quote(argument) for argument in arguments
    )
    timeouts = {
        "remote-begin": 60,
        "remote-provision": 1800,
        "remote-abort": 60,
        "remote-probe": 600,
    }
    return _run(
        ["ssh", "-n", "cvm", command],
        cwd=REPO_ROOT,
        check=check,
        timeout=timeouts[operation],
    )


def _parse_stdout_receipt(
    completed: subprocess.CompletedProcess[str], schema: str
) -> Mapping[str, Any]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        payload = _strict_json_loads(lines[-1], "remote receipt")
    except (IndexError, ProbeError) as exc:
        raise ProbeError(
            "remote operation returned no structured receipt",
            check="remote-receipt-missing",
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise ProbeError(
            "remote operation returned the wrong receipt schema",
            check="remote-receipt-schema",
        )
    return payload


def _validate_provision_success(
    remote: Mapping[str, Any],
    prepare_receipt: Mapping[str, Any],
    *,
    handle: str,
    owner_nonce: str,
    workflow_files: Mapping[str, str],
) -> Mapping[str, Any]:
    expected_keys = {
        "schema",
        "status",
        "handle",
        "ownerNonce",
        "sourceRevision",
        "imageSourceRevision",
        "workflowSourceRevision",
        "workflowFilesVerified",
        "freeBytesAtLoad",
        "archive",
        "images",
        "retainedImageIds",
        "transferCleanup",
        "retryAllowed",
        "terminalOperation",
    }
    images = prepare_receipt.get("images")
    archive = prepare_receipt.get("archive")
    if not isinstance(images, list) or not isinstance(archive, dict):
        raise ProbeError("prepare receipt is incomplete")
    expected_archive = {
        "sha256": archive.get("sha256"),
        "bytes": archive.get("bytes"),
        "remoteVerified": True,
    }
    expected_cleanup = {
        "archiveAbsent": True,
        "prepareReceiptAbsent": True,
        "incomingDirectoryAbsent": True,
        "errors": [],
    }
    expected_terminal = {
        "operation": "provision",
        "handle": handle,
        "retryAllowed": False,
    }
    expected_retained = [image.get("id") for image in images if isinstance(image, dict)]
    if (
        set(remote) != expected_keys
        or remote.get("schema") != "cvm-sidecar.provision-receipt/1"
        or remote.get("status") != "provisioned"
        or remote.get("handle") != handle
        or remote.get("ownerNonce") != owner_nonce
        or remote.get("sourceRevision") != prepare_receipt.get("sourceRevision")
        or remote.get("imageSourceRevision")
        != prepare_receipt.get("imageSourceRevision")
        or remote.get("workflowSourceRevision")
        != prepare_receipt.get("workflowSourceRevision")
        or remote.get("workflowFilesVerified") != workflow_files
        or not isinstance(remote.get("freeBytesAtLoad"), int)
        or int(remote["freeBytesAtLoad"]) < MIN_REMOTE_FREE_BYTES
        or remote.get("archive") != expected_archive
        or remote.get("images") != images
        or remote.get("retainedImageIds") != expected_retained
        or remote.get("transferCleanup") != expected_cleanup
        or remote.get("retryAllowed") is not False
        or remote.get("terminalOperation") != expected_terminal
    ):
        raise ProbeError(
            "remote provision receipt did not bind the prepared artifact",
            check="remote-provision-receipt-binding",
        )
    return remote


def _validate_abort_receipt(
    remote: Mapping[str, Any], *, handle: str, owner_nonce: str
) -> Mapping[str, Any]:
    if (
        set(remote)
        != {
            "schema",
            "status",
            "handle",
            "ownerNonce",
            "transferAbsenceProved",
            "errors",
            "retryAllowed",
        }
        or remote.get("schema") != "cvm-sidecar.abort-receipt/1"
        or remote.get("status") not in {"aborted", "absent"}
        or remote.get("handle") != handle
        or remote.get("ownerNonce") != owner_nonce
        or remote.get("transferAbsenceProved") is not True
        or remote.get("errors") != []
        or remote.get("retryAllowed") is not False
    ):
        raise ProbeError(
            "remote abort receipt did not prove owned transfer absence",
            check="remote-abort-receipt-binding",
        )
    return remote


def prepare(args: argparse.Namespace) -> Mapping[str, object]:
    if SOURCE_REVISION.fullmatch(args.source_revision) is None:
        raise ProbeError("source revision must be an exact 40-hex Git SHA")
    workflow_source_revision = _inspect_workflow_source()
    workflow_files = _workflow_file_hashes()
    images = [
        _inspect_image("sidecar", args.sidecar_image),
        _inspect_image("client", args.client_image),
    ]
    if any(image.get("sourceRevision") != args.source_revision for image in images):
        raise ProbeError(
            "image source revision label does not match the reviewed revision",
            check="image-source-revision",
        )
    identity = {
        "imageSourceRevision": args.source_revision,
        "workflowSourceRevision": workflow_source_revision,
        "workflowFiles": workflow_files,
        "images": [image["id"] for image in images],
    }
    handle = f"cvmsp-{_sha256_bytes(_canonical_json(identity))[:24]}"
    state = LOCAL_STATE_ROOT / handle
    try:
        state.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ProbeError(f"local handle already exists: {handle}") from exc

    archive = state / "images.tar"
    temporary_archive = state / "images.tar.tmp"
    try:
        _run(
            [
                "docker",
                "image",
                "save",
                "--output",
                os.fspath(temporary_archive),
                args.sidecar_image,
                args.client_image,
            ],
            cwd=REPO_ROOT,
            timeout=1800,
        )
        if not temporary_archive.is_file() or temporary_archive.stat().st_size == 0:
            raise ProbeError("docker save did not produce a non-empty archive")
        os.replace(temporary_archive, archive)
        receipt: Mapping[str, object] = {
            "schema": "cvm-sidecar.prepare-receipt/1",
            "status": "prepared",
            "handle": handle,
            "sourceRevision": args.source_revision,
            "imageSourceRevision": args.source_revision,
            "workflowSourceRevision": workflow_source_revision,
            "workflowFiles": workflow_files,
            "images": images,
            "archive": {
                "relativePath": archive.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256_file(archive),
                "bytes": archive.stat().st_size,
            },
        }
        _write_json_atomic(state / "prepare.json", receipt)
        return receipt
    except BaseException:
        temporary_archive.unlink(missing_ok=True)
        raise


def provision(handle: str) -> Mapping[str, object]:
    receipt = _load_receipt(
        handle, "prepare.json", "cvm-sidecar.prepare-receipt/1"
    )
    archive_payload = receipt.get("archive")
    if not isinstance(archive_payload, dict):
        raise ProbeError("prepare receipt has no archive attestation")
    workflow_files = _validate_workflow_files(receipt.get("workflowFiles"))
    archive = LOCAL_STATE_ROOT / handle / "images.tar"
    if (
        not archive.is_file()
        or archive.stat().st_size != archive_payload.get("bytes")
        or _sha256_file(archive) != archive_payload.get("sha256")
    ):
        raise ProbeError("local image archive does not match its attestation")

    attempt = LOCAL_STATE_ROOT / handle / "provision-attempt.json"
    _claim_once(
        attempt,
        {"schema": "cvm-sidecar.provision-attempt/1", "handle": handle},
    )
    begin_owned = False
    owner_nonce: str | None = None
    try:
        begin = _parse_stdout_receipt(
            _remote(
                "remote-begin",
                handle,
                workflow_files=workflow_files,
            ),
            "cvm-sidecar.remote-begin-receipt/1",
        )
        expected_begin_keys = {
            "schema",
            "status",
            "handle",
            "ownerNonce",
            "workflowFilesVerified",
            "freeBytes",
        }
        if (
            set(begin) != expected_begin_keys
            or begin.get("status") != "ready-for-fixed-archive"
            or begin.get("handle") != handle
            or not isinstance(begin.get("ownerNonce"), str)
            or OWNER_NONCE.fullmatch(str(begin["ownerNonce"])) is None
            or begin.get("workflowFilesVerified") != workflow_files
            or not isinstance(begin.get("freeBytes"), int)
            or int(begin["freeBytes"]) < MIN_REMOTE_FREE_BYTES
        ):
            raise ProbeError("remote begin receipt did not prove fresh ownership")
        owner_nonce = str(begin["ownerNonce"])
        begin_owned = True
        destination = (
            f"cvm:{REMOTE_ROOT}/.cvm-sidecar-probes/{handle}/incoming/"
        )
        _run(
            [
                "rsync",
                "-a",
                "--",
                os.fspath(archive),
                os.fspath(LOCAL_STATE_ROOT / handle / "prepare.json"),
                destination,
            ],
            cwd=REPO_ROOT,
            timeout=1800,
        )
        remote = _parse_stdout_receipt(
            _remote("remote-provision", handle, owner_nonce=owner_nonce),
            "cvm-sidecar.provision-receipt/1",
        )
        remote = _validate_provision_success(
            remote,
            receipt,
            handle=handle,
            owner_nonce=owner_nonce,
            workflow_files=workflow_files,
        )
        _write_json_atomic(LOCAL_STATE_ROOT / handle / "provision.json", remote)
        return remote
    except BaseException as exc:
        abort: Mapping[str, Any] | None = None
        if begin_owned and owner_nonce is not None:
            try:
                abort = _validate_abort_receipt(
                    _parse_stdout_receipt(
                        _remote(
                            "remote-abort",
                            handle,
                            check=False,
                            owner_nonce=owner_nonce,
                        ),
                        "cvm-sidecar.abort-receipt/1",
                    ),
                    handle=handle,
                    owner_nonce=owner_nonce,
                )
            except BaseException:
                abort = None
        failure = {
            "schema": "cvm-sidecar.provision-receipt/1",
            "status": "failed",
            "handle": handle,
            "retryAllowed": False,
            "abort": abort,
            "errorOperation": "provision",
            "errorCheck": exc.check if isinstance(exc, ProbeError) else "unexpected",
        }
        _write_json_atomic(LOCAL_STATE_ROOT / handle / "provision.json", failure)
        raise


def remote_begin(
    handle: str, module_sha256: str, wrapper_sha256: str
) -> Mapping[str, object]:
    _validate_handle(handle)
    expected_files = _validate_workflow_files(
        {"module": module_sha256, "wrapper": wrapper_sha256}
    )
    verified_files = _verify_deployed_workflow(expected_files)
    free_bytes = _remote_disk_gate()
    LOCAL_STATE_ROOT.mkdir(parents=True, exist_ok=True)
    state = LOCAL_STATE_ROOT / handle
    try:
        state.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ProbeError("remote handle already exists; adoption is forbidden") from exc
    owner_nonce = secrets.token_hex(16)
    try:
        _claim_once(
            state / "provision-attempt.json",
            {
                "schema": "cvm-sidecar.remote-provision-attempt/1",
                "handle": handle,
                "ownerNonce": owner_nonce,
                "workflowFilesVerified": verified_files,
                "freeBytes": free_bytes,
            },
        )
        (state / "incoming").mkdir(mode=0o700)
        return {
            "schema": "cvm-sidecar.remote-begin-receipt/1",
            "status": "ready-for-fixed-archive",
            "handle": handle,
            "ownerNonce": owner_nonce,
            "workflowFilesVerified": verified_files,
            "freeBytes": free_bytes,
        }
    except BaseException:
        try:
            (state / "provision-attempt.json").unlink(missing_ok=True)
            state.rmdir()
        except OSError:
            pass
        raise


def _load_remote_provision_owner(handle: str, owner_nonce: str) -> Mapping[str, Any]:
    _validate_handle(handle)
    if OWNER_NONCE.fullmatch(owner_nonce) is None:
        raise ProbeError("remote ownership nonce is invalid")
    attempt_path = LOCAL_STATE_ROOT / handle / "provision-attempt.json"
    try:
        attempt = _strict_json_loads(
            attempt_path.read_text(encoding="utf-8"),
            "remote provision ownership receipt",
        )
    except (OSError, ProbeError) as exc:
        raise ProbeError("remote provision ownership is unavailable") from exc
    if (
        not isinstance(attempt, dict)
        or attempt.get("schema") != "cvm-sidecar.remote-provision-attempt/1"
        or attempt.get("handle") != handle
        or attempt.get("ownerNonce") != owner_nonce
    ):
        raise ProbeError("remote provision ownership mismatch")
    return attempt


def remote_abort(handle: str, owner_nonce: str) -> Mapping[str, object]:
    _validate_handle(handle)
    state = LOCAL_STATE_ROOT / handle
    incoming = state / "incoming"
    if not state.is_dir():
        return {
            "schema": "cvm-sidecar.abort-receipt/1",
            "status": "absent",
            "handle": handle,
            "transferAbsenceProved": True,
            "ownerNonce": owner_nonce,
            "errors": [],
            "retryAllowed": False,
        }
    _load_remote_provision_owner(handle, owner_nonce)
    _claim_once(
        state / "abort-attempt.json",
        {
            "schema": "cvm-sidecar.abort-attempt/1",
            "handle": handle,
            "ownerNonce": owner_nonce,
        },
    )
    errors: list[str] = []
    for path in (incoming / "images.tar", incoming / "prepare.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            errors.append(f"cannot remove {path.name}")
    try:
        incoming.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        errors.append("cannot remove incoming directory")
    absence = (
        not (incoming / "images.tar").exists()
        and not (incoming / "prepare.json").exists()
        and not incoming.exists()
    )
    receipt = {
        "schema": "cvm-sidecar.abort-receipt/1",
        "status": "aborted" if absence and not errors else "cleanup-failed",
        "handle": handle,
        "ownerNonce": owner_nonce,
        "transferAbsenceProved": absence,
        "errors": errors,
        "retryAllowed": False,
    }
    _write_json_atomic(state / "abort.json", receipt)
    return receipt


def _verify_image_receipt(image: Mapping[str, Any]) -> Mapping[str, object]:
    role = image.get("role")
    image_id = image.get("id")
    if role not in {"sidecar", "client"} or not isinstance(image_id, str):
        raise ProbeError("archive receipt contains an invalid image role")
    inspected = _inspect_image(role, image_id)
    if inspected != image:
        raise ProbeError(f"loaded {role} image attestation mismatch")
    return inspected


def remote_provision(handle: str, owner_nonce: str) -> Mapping[str, object]:
    _validate_handle(handle)
    ownership = _load_remote_provision_owner(handle, owner_nonce)
    verified_files = _verify_deployed_workflow(
        ownership.get("workflowFilesVerified")
    )
    free_bytes = _remote_disk_gate()
    state = LOCAL_STATE_ROOT / handle
    incoming = state / "incoming"
    prepare_path = incoming / "prepare.json"
    archive = incoming / "images.tar"
    operation_error: BaseException | None = None
    receipt_data: dict[str, Any] | None = None
    try:
        try:
            prepare_receipt = _strict_json_loads(
                prepare_path.read_text(encoding="utf-8"),
                "transferred prepare receipt",
            )
        except (OSError, ProbeError) as exc:
            raise ProbeError("transferred prepare receipt is unavailable") from exc
        if (
            not isinstance(prepare_receipt, dict)
            or prepare_receipt.get("schema") != "cvm-sidecar.prepare-receipt/1"
            or prepare_receipt.get("handle") != handle
        ):
            raise ProbeError("transferred prepare receipt identity mismatch")
        archive_payload = prepare_receipt.get("archive")
        if not isinstance(archive_payload, dict):
            raise ProbeError("transferred receipt has no archive attestation")
        if (
            not archive.is_file()
            or archive.stat().st_size != archive_payload.get("bytes")
            or _sha256_file(archive) != archive_payload.get("sha256")
        ):
            raise ProbeError("remote archive hash or size mismatch")
        _run(
            ["docker", "image", "load", "--input", os.fspath(archive)],
            cwd=REPO_ROOT,
            timeout=1800,
        )
        images_payload = prepare_receipt.get("images")
        if not isinstance(images_payload, list) or len(images_payload) != 2:
            raise ProbeError("prepare receipt does not name exactly two images")
        images = [
            _verify_image_receipt(image)
            for image in images_payload
            if isinstance(image, dict)
        ]
        if [image["role"] for image in images] != ["sidecar", "client"]:
            raise ProbeError("prepare receipt image order is not fixed")
        receipt_data = {
            "schema": "cvm-sidecar.provision-receipt/1",
            "status": "provisioned",
            "handle": handle,
            "ownerNonce": owner_nonce,
            "sourceRevision": prepare_receipt.get("sourceRevision"),
            "imageSourceRevision": prepare_receipt.get("imageSourceRevision"),
            "workflowSourceRevision": prepare_receipt.get("workflowSourceRevision"),
            "workflowFilesVerified": verified_files,
            "freeBytesAtLoad": free_bytes,
            "archive": {
                "sha256": archive_payload.get("sha256"),
                "bytes": archive_payload.get("bytes"),
                "remoteVerified": True,
            },
            "images": images,
            "retainedImageIds": [image["id"] for image in images],
            "retryAllowed": False,
            "terminalOperation": {
                "operation": "provision",
                "handle": handle,
                "retryAllowed": False,
            },
        }
    except BaseException as exc:
        operation_error = exc

    cleanup_errors: list[str] = []
    for path in (archive, prepare_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            cleanup_errors.append(f"cannot remove {path.name}")
    try:
        incoming.rmdir()
    except OSError:
        cleanup_errors.append("cannot remove incoming directory")
    transfer_cleanup = {
        "archiveAbsent": not archive.exists(),
        "prepareReceiptAbsent": not prepare_path.exists(),
        "incomingDirectoryAbsent": not incoming.exists(),
        "errors": cleanup_errors,
    }
    if cleanup_errors or not all(
        transfer_cleanup[key]
        for key in (
            "archiveAbsent",
            "prepareReceiptAbsent",
            "incomingDirectoryAbsent",
        )
    ):
        raise ProbeError(
            "remote provision transfer cleanup failed",
            check="transfer-cleanup-absence",
        )
    if operation_error is not None:
        raise operation_error
    assert receipt_data is not None
    receipt_data["transferCleanup"] = transfer_cleanup
    _write_json_atomic(state / "provision.json", receipt_data)
    return receipt_data


def _docker(
    *args: str,
    input_text: str | None = None,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["docker", *args],
        cwd=REPO_ROOT,
        input_text=input_text,
        check=check,
        timeout=timeout,
    )


def _last_json_line(output: str, label: str) -> Mapping[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            payload = _strict_json_loads(line, label)
        except ProbeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ProbeError(f"{label} returned no JSON result")


def _created_resource_id(output: str, kind: str) -> str:
    resource_id = output.strip()
    if RESOURCE_ID.fullmatch(resource_id) is None:
        raise ProbeError(
            f"{kind} create returned no exact resource ID",
            check=f"{kind}-create-identity",
        )
    return resource_id


def _owned_resource_inspection(
    resource: Mapping[str, Any],
    *,
    handle: str,
    owner_nonce: str,
) -> Mapping[str, Any] | None:
    kind = resource.get("kind")
    target = resource.get("id") or resource.get("name")
    if kind not in {"container", "network"} or not isinstance(target, str):
        return None
    command = (
        ("container", "inspect", target)
        if kind == "container"
        else ("network", "inspect", target)
    )
    try:
        completed = _docker(*command, check=False, timeout=30)
    except BaseException:
        return None
    if completed.returncode:
        return None
    try:
        inspected = _strict_json_loads(
            completed.stdout, f"owned {kind} inspection"
        )
    except ProbeError:
        return None
    if not isinstance(inspected, list) or len(inspected) != 1:
        return None
    payload = inspected[0]
    if not isinstance(payload, dict):
        return None
    resource_id = payload.get("Id")
    if not isinstance(resource_id, str) or RESOURCE_ID.fullmatch(resource_id) is None:
        return None
    labels = (
        payload.get("Config", {}).get("Labels")
        if kind == "container" and isinstance(payload.get("Config"), dict)
        else payload.get("Labels")
    )
    if not isinstance(labels, dict):
        return None
    if (
        labels.get("io.text-to-cad.cvm-sidecar-handle") != handle
        or labels.get("io.text-to-cad.cvm-sidecar-owner") != owner_nonce
    ):
        return None
    return payload


def _wait_sidecar_ready(name: str, job_id: str) -> Mapping[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        logs = _docker("logs", "--tail", "50", name, check=False, timeout=30)
        for line in logs.stdout.splitlines():
            try:
                record = _strict_json_loads(line, "sidecar log")
            except ProbeError:
                continue
            if record.get("event") == "ready" and record.get("jobId") == job_id:
                return record
        state = _docker(
            "container",
            "inspect",
            name,
            "--format",
            "{{json .State}}",
            check=False,
            timeout=30,
        )
        if state.returncode:
            raise ProbeError("sidecar disappeared before readiness")
        try:
            state_payload = _strict_json_loads(state.stdout, "sidecar state")
            running = state_payload.get("Running") if isinstance(state_payload, dict) else None
        except (ProbeError, AttributeError) as exc:
            raise ProbeError("sidecar state was not structured") from exc
        if not running:
            raise ProbeError("sidecar stopped before readiness")
        time.sleep(1)
    raise ProbeError("sidecar readiness deadline exceeded")


def _resource_absence(
    label: str, handle: str, owner_nonce: str
) -> Mapping[str, object]:
    errors: list[str] = []
    try:
        containers = _docker(
            "container",
            "ls",
            "-a",
            "--filter",
            f"label=io.text-to-cad.cvm-sidecar-handle={handle}",
            "--filter",
            f"label=io.text-to-cad.cvm-sidecar-owner={owner_nonce}",
            "--format",
            "{{.ID}}",
            check=False,
            timeout=30,
        )
    except BaseException:
        containers = subprocess.CompletedProcess([], 1, "", "")
        errors.append("container absence proof failed")
    try:
        networks = _docker(
            "network",
            "ls",
            "--filter",
            f"label=io.text-to-cad.cvm-sidecar-handle={handle}",
            "--filter",
            f"label=io.text-to-cad.cvm-sidecar-owner={owner_nonce}",
            "--format",
            "{{.ID}}",
            check=False,
            timeout=30,
        )
    except BaseException:
        networks = subprocess.CompletedProcess([], 1, "", "")
        errors.append("network absence proof failed")
    return {
        "label": label,
        "ownerNonce": owner_nonce,
        "containers": containers.stdout.split(),
        "networks": networks.stdout.split(),
        "errors": errors,
        "proved": (
            containers.returncode == 0
            and networks.returncode == 0
            and not containers.stdout.split()
            and not networks.stdout.split()
        ),
    }


def _run_remote_probe(
    handle: str,
    provision_receipt: Mapping[str, Any],
    owner_nonce: str,
) -> Mapping[str, object]:
    images = provision_receipt.get("images")
    if not isinstance(images, list) or len(images) != 2:
        raise ProbeError("provision receipt does not name two fixed images")
    by_role = {
        image.get("role"): image.get("id")
        for image in images
        if isinstance(image, dict)
    }
    sidecar_id = by_role.get("sidecar")
    client_id = by_role.get("client")
    if not isinstance(sidecar_id, str) or not isinstance(client_id, str):
        raise ProbeError("provisioned image roles are incomplete")

    state = LOCAL_STATE_ROOT / handle
    suffix = handle.removeprefix("cvmsp-")
    job_id = f"cvm-probe-{suffix[:12]}"
    prefix = f"ttc-cvmsp-{suffix[:16]}"
    network = f"{prefix}-net"
    sidecar = f"{prefix}-sidecar"
    client = f"{prefix}-client"
    label = f"io.text-to-cad.cvm-sidecar-handle={handle}"
    owner_label = f"io.text-to-cad.cvm-sidecar-owner={owner_nonce}"
    ledger = [
        {"kind": "network", "name": network, "state": "planned", "ownerNonce": owner_nonce},
        {"kind": "container", "name": sidecar, "state": "planned", "ownerNonce": owner_nonce},
        {"kind": "container", "name": client, "state": "planned", "ownerNonce": owner_nonce},
    ]
    result: Mapping[str, Any] | None = None
    config: Mapping[str, Any] | None = None
    readiness: Mapping[str, Any] | None = None
    sidecar_terminal: Mapping[str, Any] | None = None
    first_error: str | None = None
    cleanup_errors: list[str] = []
    try:
        network_created = _docker(
            "network", "create", "--internal", "--label", label,
            "--label", owner_label, network
        )
        ledger[0].update(
            {
                "state": "created",
                "id": _created_resource_id(network_created.stdout, "network"),
            }
        )
        sidecar_created = _docker(
            "run", "-d", "--name", sidecar,
            "--label", label, "--label", owner_label,
            "--network", network, "--network-alias", "sidecar",
            "--pull=never", "--platform", "linux/amd64",
            "--read-only", "--init", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "256", "--memory", "1536m",
            "--memory-swap", "1536m", "--cpus", "1.5",
            "--shm-size", "256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=64m,uid=1001,gid=1001,mode=700",
            "-e", f"BROWSER_SIDECAR_JOB_ID={job_id}", sidecar_id,
        )
        ledger[1].update(
            {
                "state": "created",
                "id": _created_resource_id(sidecar_created.stdout, "sidecar"),
            }
        )
        readiness = _wait_sidecar_ready(str(ledger[1]["id"]), job_id)
        inspected = _docker("container", "inspect", str(ledger[1]["id"]))
        try:
            inspected_payload = _strict_json_loads(
                inspected.stdout, "sidecar runtime inspection"
            )
            config_payload = inspected_payload[0]
        except (ProbeError, IndexError, TypeError) as exc:
            raise ProbeError("sidecar runtime inspection was not singular") from exc
        host_config = config_payload.get("HostConfig", {})
        mounts = config_payload.get("Mounts")
        config = {
            "readonlyRootfs": host_config.get("ReadonlyRootfs"),
            "mounts": mounts,
            "memory": host_config.get("Memory"),
            "memorySwap": host_config.get("MemorySwap"),
            "nanoCpus": host_config.get("NanoCpus"),
            "pidsLimit": host_config.get("PidsLimit"),
            "shmSize": host_config.get("ShmSize"),
        }
        request_text = _canonical_json(REQUEST).decode("ascii") + "\n"
        client_created = _docker(
            "container", "create", "--name", client,
            "--label", label, "--label", owner_label,
            "--network", network,
            "--pull=never", "--platform", "linux/amd64",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "64", "--memory", "768m",
            "--memory-swap", "768m", "--cpus", "1",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=8m,uid=1001,gid=1001,mode=700",
            "-e", f"BROWSER_SIDECAR_JOB_ID={job_id}",
            "-e", "BROWSER_SIDECAR_HOST=sidecar",
            "-i", client_id,
        )
        ledger[2].update(
            {
                "state": "created",
                "id": _created_resource_id(client_created.stdout, "client"),
            }
        )
        client_completed = _docker(
            "container", "start", "--attach", "--interactive", str(ledger[2]["id"]),
            input_text=request_text,
        )
        result = _last_json_line(client_completed.stdout, "sealed client")
        nested = result.get("result") if isinstance(result, dict) else None
        if not (
            result.get("ok") is True
            and result.get("schema") == REQUEST["schema"]
            and result.get("program") == "probe"
            and result.get("jobId") == job_id
            and isinstance(nested, dict)
            and nested.get("connected") is True
            and nested.get("browserExecutablesVisible") == []
            and nested.get("contextCount") == 1
            and nested.get("pageCount") == 1
            and nested.get("sourceAliasesVisible") == []
            and nested.get("externalEgressBlocked") is True
        ):
            raise ProbeError("sealed client probe predicates failed")
        if config != {
            "readonlyRootfs": True,
            "mounts": [],
            "memory": 1610612736,
            "memorySwap": 1610612736,
            "nanoCpus": 1500000000,
            "pidsLimit": 256,
            "shmSize": 268435456,
        }:
            raise ProbeError("sidecar outer resource predicates failed")
    except BaseException as exc:
        first_error = (
            exc.check if isinstance(exc, ProbeError) else "unexpected"
        )
    finally:
        for resource in reversed(ledger[1:]):
            if resource["kind"] != "container":
                continue
            inspection = _owned_resource_inspection(
                resource, handle=handle, owner_nonce=owner_nonce
            )
            if inspection is None:
                if resource["state"] == "created":
                    cleanup_errors.append("container ownership proof failed")
                continue
            resource_id = str(inspection["Id"])
            resource.update({"state": "created", "id": resource_id})
            if resource["name"] == sidecar:
                try:
                    stopped = _docker(
                        "stop", "--time", "15", resource_id,
                        check=False, timeout=30,
                    )
                    if stopped.returncode:
                        cleanup_errors.append("sidecar stop failed")
                except BaseException:
                    cleanup_errors.append("sidecar stop failed")
                try:
                    logs = _docker(
                        "logs", "--tail", "50", resource_id,
                        check=False, timeout=30,
                    )
                except BaseException:
                    logs = subprocess.CompletedProcess([], 1, "", "")
                    cleanup_errors.append("sidecar logs failed")
                try:
                    terminal = _docker(
                        "container", "inspect", resource_id,
                        "--format", "{{json .State}}", check=False, timeout=30,
                    )
                except BaseException:
                    terminal = subprocess.CompletedProcess([], 1, "", "")
                    cleanup_errors.append("sidecar terminal inspect failed")
                try:
                    sidecar_terminal = {
                        "state": _strict_json_loads(
                            terminal.stdout, "sidecar terminal state"
                        ),
                        "closingObserved": any(
                            isinstance(record, dict)
                            and record.get("event") == "closing"
                            and record.get("jobId") == job_id
                            and record.get("reason") == "SIGTERM"
                            for record in (
                                _strict_json_loads(line, "sidecar terminal log")
                                for line in logs.stdout.splitlines()
                                if line.startswith("{")
                            )
                        ),
                    }
                except (ProbeError, TypeError):
                    sidecar_terminal = {"state": None, "closingObserved": False}
            try:
                removed = _docker(
                    "rm", "-f", resource_id, check=False, timeout=30
                )
            except BaseException:
                removed = subprocess.CompletedProcess([], 1, "", "")
            if removed.returncode:
                cleanup_errors.append(f"{resource['kind']} cleanup failed")
            else:
                resource["state"] = "removed"
        network_inspection = _owned_resource_inspection(
            ledger[0], handle=handle, owner_nonce=owner_nonce
        )
        if network_inspection is not None:
            network_id = str(network_inspection["Id"])
            ledger[0].update({"state": "created", "id": network_id})
            try:
                removed_network = _docker(
                    "network", "rm", network_id, check=False, timeout=30
                )
            except BaseException:
                removed_network = subprocess.CompletedProcess([], 1, "", "")
            if removed_network.returncode:
                cleanup_errors.append("network cleanup failed")
            else:
                ledger[0]["state"] = "removed"
        elif ledger[0]["state"] == "created":
            cleanup_errors.append("network ownership proof failed")

    absence = _resource_absence(label, handle, owner_nonce)
    terminal_ok = bool(
        sidecar_terminal
        and sidecar_terminal.get("closingObserved") is True
        and isinstance(sidecar_terminal.get("state"), dict)
        and sidecar_terminal["state"].get("ExitCode") == 0
    )
    succeeded = (
        first_error is None
        and not cleanup_errors
        and absence.get("proved") is True
        and terminal_ok
    )
    receipt = {
        "schema": "cvm-sidecar.probe-receipt/1",
        "status": "succeeded" if succeeded else "failed",
        "handle": handle,
        "sourceRevision": provision_receipt.get("sourceRevision"),
        "imageSourceRevision": provision_receipt.get("imageSourceRevision"),
        "workflowSourceRevision": provision_receipt.get("workflowSourceRevision"),
        "images": images,
        "requestSha256": _sha256_bytes(_canonical_json(REQUEST)),
        "readiness": readiness,
        "result": result,
        "outerConfig": config,
        "resourceLedger": ledger,
        "ownerNonce": owner_nonce,
        "terminal": sidecar_terminal,
        "absenceProof": absence,
        "retainedImageIds": [sidecar_id, client_id],
        "terminalOperation": {
            "operation": "probe",
            "handle": handle,
            "retryAllowed": False,
        },
        "errorOperation": "probe" if first_error is not None else None,
        "errorCheck": first_error,
        "cleanupErrors": cleanup_errors,
    }
    _write_json_atomic(state / "probe.json", receipt)
    return receipt


def remote_probe(handle: str) -> Mapping[str, object]:
    _validate_handle(handle)
    state = LOCAL_STATE_ROOT / handle
    owner_nonce = secrets.token_hex(16)
    _claim_once(
        state / "probe-attempt.json",
        {
            "schema": "cvm-sidecar.remote-probe-attempt/1",
            "handle": handle,
            "ownerNonce": owner_nonce,
        },
    )
    try:
        provision_receipt = _load_receipt(
            handle, "provision.json", "cvm-sidecar.provision-receipt/1"
        )
        return _run_remote_probe(handle, provision_receipt, owner_nonce)
    except BaseException as exc:
        check = exc.check if isinstance(exc, ProbeError) else "unexpected"
        failure = {
            "schema": "cvm-sidecar.probe-receipt/1",
            "status": "failed",
            "handle": handle,
            "ownerNonce": owner_nonce,
            "resourceLedger": [],
            "terminal": None,
            "absenceProof": {
                "proved": False,
                "containers": [],
                "networks": [],
                "errors": ["probe operation escaped before full cleanup proof"],
            },
            "terminalOperation": {
                "operation": "probe",
                "handle": handle,
                "retryAllowed": False,
            },
            "errorOperation": "probe",
            "errorCheck": check,
            "cleanupErrors": ["terminal cleanup proof incomplete"],
        }
        _write_json_atomic(state / "probe.json", failure)
        return failure


def probe(handle: str) -> Mapping[str, object]:
    provision_receipt = _load_receipt(
        handle, "provision.json", "cvm-sidecar.provision-receipt/1"
    )
    if provision_receipt.get("status") != "provisioned":
        raise ProbeError("fixed handle was not successfully provisioned")
    _claim_once(
        LOCAL_STATE_ROOT / handle / "probe-attempt.json",
        {"schema": "cvm-sidecar.probe-attempt/1", "handle": handle},
    )
    try:
        remote = _parse_stdout_receipt(
            _remote("remote-probe", handle, check=False),
            "cvm-sidecar.probe-receipt/1",
        )
    except BaseException as exc:
        check = exc.check if isinstance(exc, ProbeError) else "unexpected"
        failure = {
            "schema": "cvm-sidecar.probe-receipt/1",
            "status": "failed",
            "handle": handle,
            "terminalOperation": {
                "operation": "probe",
                "handle": handle,
                "retryAllowed": False,
            },
            "errorOperation": "probe",
            "errorCheck": check,
            "remoteReceiptVerified": False,
        }
        _write_json_atomic(LOCAL_STATE_ROOT / handle / "probe.json", failure)
        raise ProbeError(
            "the one-shot remote probe produced no verified receipt",
            check=check,
        ) from exc
    _write_json_atomic(LOCAL_STATE_ROOT / handle / "probe.json", remote)
    if remote.get("status") != "succeeded":
        raise ProbeError("the one-shot remote probe failed; retry is forbidden")
    return remote


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source-revision", required=True)
    prepare_parser.add_argument("--sidecar-image", required=True)
    prepare_parser.add_argument("--client-image", required=True)
    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("handle")
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("handle")
    remote_begin_parser = subparsers.add_parser("remote-begin")
    remote_begin_parser.add_argument("handle")
    remote_begin_parser.add_argument("module_sha256")
    remote_begin_parser.add_argument("wrapper_sha256")
    remote_provision_parser = subparsers.add_parser("remote-provision")
    remote_provision_parser.add_argument("handle")
    remote_provision_parser.add_argument("owner_nonce")
    remote_abort_parser = subparsers.add_parser("remote-abort")
    remote_abort_parser.add_argument("handle")
    remote_abort_parser.add_argument("owner_nonce")
    remote_probe_parser = subparsers.add_parser("remote-probe")
    remote_probe_parser.add_argument("handle")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.operation == "prepare":
            receipt = prepare(args)
        elif args.operation == "provision":
            receipt = provision(args.handle)
        elif args.operation == "probe":
            receipt = probe(args.handle)
        elif args.operation == "remote-begin":
            receipt = remote_begin(
                args.handle, args.module_sha256, args.wrapper_sha256
            )
        elif args.operation == "remote-provision":
            receipt = remote_provision(args.handle, args.owner_nonce)
        elif args.operation == "remote-abort":
            receipt = remote_abort(args.handle, args.owner_nonce)
        elif args.operation == "remote-probe":
            receipt = remote_probe(args.handle)
            print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
            return 0 if receipt.get("status") == "succeeded" else 1
        else:  # pragma: no cover - argparse owns this closed branch.
            raise ProbeError("unknown operation")
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except ProbeError as exc:
        print(f"cvm-sidecar-probe: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
