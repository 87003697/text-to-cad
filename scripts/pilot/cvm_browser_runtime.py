#!/usr/bin/env python3
"""Provision and probe the one Browser Runtime image on CVM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.fspath(REPO_ROOT / "packages/browser_runtime/src"))
STATE_ROOT = REPO_ROOT / ".cvm-browser-runtime"
REMOTE_ROOT = "~/text-to-cad"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
HANDLE = re.compile(r"cvmbr-[0-9a-f]{24}\Z")
NONCE = re.compile(r"[0-9a-f]{32}\Z")
MIN_FREE_BYTES = 3 * 1024 * 1024 * 1024
PREPARE_SCHEMA = "cvm-browser-runtime.prepare/1"
PROVISION_SCHEMA = "cvm-browser-runtime.provision/1"
PROBE_SCHEMA = "cvm-browser-runtime.probe/1"


class RuntimeWorkflowError(RuntimeError):
    """Closed failure from the Browser Runtime CVM workflow."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(
    argv: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
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
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeWorkflowError("fixed command failed") from exc
    if check and completed.returncode != 0:
        raise RuntimeWorkflowError("fixed command failed")
    return completed


def _docker(*argv: str, check: bool = True, timeout: int = 300):
    return _run(["docker", *argv], check=check, timeout=timeout)


def _strict_json(text: str, label: str) -> Mapping[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RuntimeWorkflowError(f"{label} has duplicate keys")
            value[key] = item
        return value

    try:
        value = json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise RuntimeWorkflowError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeWorkflowError(f"{label} is not an object")
    return value


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeWorkflowError("temporary receipt already exists")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = _canonical(value)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short receipt write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path)
    except FileExistsError as exc:
        raise RuntimeWorkflowError("receipt already exists") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _claim_once(state: Path, operation: str, handle: str) -> None:
    """Atomically consume a one-shot workflow operation."""

    _write_json(
        state / f"{operation}-attempt.json",
        {
            "schema": f"cvm-browser-runtime.{operation}-attempt/1",
            "handle": handle,
        },
    )


def _workflow_hashes() -> Mapping[str, str]:
    module = Path(__file__).resolve()
    wrapper = module.with_name("cvm-browser-runtime.sh")
    return {"module": _sha256_file(module), "wrapper": _sha256_file(wrapper)}


def _workflow_revision() -> str:
    status = _run(["git", "status", "--porcelain"]).stdout
    revision = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if status or REVISION.fullmatch(revision) is None:
        raise RuntimeWorkflowError("workflow checkout must be clean")
    return revision


def _inspect_image(image_id: str) -> Mapping[str, object]:
    if IMAGE_ID.fullmatch(image_id) is None:
        raise RuntimeWorkflowError("runtime image must be an exact sha256 ID")
    fields = {
        "id": "{{.Id}}",
        "os": "{{.Os}}",
        "architecture": "{{.Architecture}}",
        "revision": '{{index .Config.Labels "org.opencontainers.image.revision"}}',
    }
    observed = {
        key: _docker("image", "inspect", image_id, "--format", projection).stdout.strip()
        for key, projection in fields.items()
    }
    if (
        observed["id"] != image_id
        or observed["os"] != "linux"
        or observed["architecture"] != "amd64"
        or REVISION.fullmatch(observed["revision"]) is None
    ):
        raise RuntimeWorkflowError("runtime image attestation is invalid")
    return {
        "role": "runtime",
        "id": image_id,
        "platform": "linux/amd64",
        "sourceRevision": observed["revision"],
    }


def _image_ids(reference: str) -> tuple[str, ...]:
    completed = _docker(
        "image", "ls", "--all", "--no-trunc", "--quiet", reference, check=False
    )
    if completed.returncode != 0:
        raise RuntimeWorkflowError("runtime image inventory failed")
    values = tuple(line for line in completed.stdout.splitlines() if line)
    if any(IMAGE_ID.fullmatch(value) is None for value in values):
        raise RuntimeWorkflowError("runtime image inventory is invalid")
    return values


def _validate_handle(handle: str) -> None:
    if HANDLE.fullmatch(handle) is None:
        raise RuntimeWorkflowError("handle is invalid")


def _load_receipt(handle: str, filename: str, schema: str) -> Mapping[str, Any]:
    _validate_handle(handle)
    path = STATE_ROOT / handle / filename
    try:
        value = _strict_json(path.read_text(encoding="ascii"), filename)
    except OSError as exc:
        raise RuntimeWorkflowError(f"{filename} is unavailable") from exc
    if value.get("schema") != schema or value.get("handle") != handle:
        raise RuntimeWorkflowError(f"{filename} identity is invalid")
    return value


def prepare(source_revision: str, runtime_image: str) -> Mapping[str, object]:
    if REVISION.fullmatch(source_revision) is None:
        raise RuntimeWorkflowError("source revision must be an exact Git SHA")
    workflow_revision = _workflow_revision()
    workflow_files = _workflow_hashes()
    image = dict(_inspect_image(runtime_image))
    if image["sourceRevision"] != source_revision:
        raise RuntimeWorkflowError("runtime image source revision does not match")
    prepare_nonce = secrets.token_hex(16)
    identity = {
        "sourceRevision": source_revision,
        "workflowRevision": workflow_revision,
        "workflowFiles": workflow_files,
        "imageId": runtime_image,
        "prepareNonce": prepare_nonce,
    }
    handle = "cvmbr-" + _sha256_bytes(_canonical(identity))[:24]
    state = STATE_ROOT / handle
    try:
        state.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RuntimeWorkflowError("prepared handle already exists") from exc
    nonce = secrets.token_hex(16)
    reference = f"text-to-cad-browser-runtime-probe:{handle}-{nonce}"
    archive = state / "runtime-image.tar"
    temporary = state / "runtime-image.tar.tmp"
    tagged = False
    try:
        if _image_ids(reference):
            raise RuntimeWorkflowError("temporary image reference already exists")
        _docker("image", "tag", runtime_image, reference)
        tagged = True
        if _image_ids(reference) != (runtime_image,):
            raise RuntimeWorkflowError("temporary image reference changed")
        _docker(
            "image", "save", "--output", os.fspath(temporary), reference,
            timeout=1800,
        )
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeWorkflowError("runtime image archive is empty")
        os.replace(temporary, archive)
        removed = _docker("image", "rm", reference, check=False)
        if removed.returncode != 0 or _image_ids(reference):
            raise RuntimeWorkflowError("temporary image reference cleanup failed")
        tagged = False
        image["archiveReference"] = reference
        receipt: Mapping[str, object] = {
            "schema": PREPARE_SCHEMA,
            "status": "prepared",
            "handle": handle,
            "sourceRevision": source_revision,
            "workflowRevision": workflow_revision,
            "workflowFiles": workflow_files,
            "prepareNonce": prepare_nonce,
            "image": image,
            "archive": {
                "sha256": _sha256_file(archive),
                "bytes": archive.stat().st_size,
            },
        }
        _write_json(state / "prepare.json", receipt)
        return receipt
    except BaseException:
        temporary.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        (state / "prepare.json").unlink(missing_ok=True)
        try:
            state.rmdir()
        except OSError:
            pass
        raise
    finally:
        if tagged:
            _docker("image", "rm", reference, check=False)


def _remote(operation: str, *args: object, check: bool = True):
    quoted = " ".join(
        ["python3", "-m", "scripts.pilot.cvm_browser_runtime", operation]
        + [str(value) for value in args]
    )
    return _run(
        ["ssh", "-n", "cvm", f"cd {REMOTE_ROOT} && {quoted}"],
        check=check,
        timeout={
            "remote-begin": 60,
            "remote-provision": 1800,
            "remote-abort": 60,
            "remote-probe": 600,
        }[operation],
    )


def _remote_receipt(completed: subprocess.CompletedProcess[str], schema: str):
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeWorkflowError("remote operation returned no receipt")
    value = _strict_json(lines[-1], "remote receipt")
    if value.get("schema") != schema:
        raise RuntimeWorkflowError("remote operation returned wrong receipt")
    return value


def _remote_failure(completed: subprocess.CompletedProcess[str], phase: str) -> None:
    prefix = "cvm-browser-runtime: "
    lines = [line for line in completed.stderr.splitlines() if line.startswith(prefix)]
    if len(lines) == 1:
        detail = lines[0][len(prefix) :]
        allowed = {
            "CVM disk gate failed",
            "deployed workflow hash mismatch",
            "Docker server must be linux/amd64",
            "remote handle already exists",
        }
        if detail in allowed:
            raise RuntimeWorkflowError(f"remote {phase} failed: {detail}")
    raise RuntimeWorkflowError(f"remote {phase} failed")


def provision(handle: str) -> Mapping[str, object]:
    prepare_receipt = _load_receipt(handle, "prepare.json", PREPARE_SCHEMA)
    _claim_once(STATE_ROOT / handle, "provision", handle)
    if prepare_receipt.get("workflowFiles") != _workflow_hashes():
        raise RuntimeWorkflowError("workflow changed after prepare")
    archive = STATE_ROOT / handle / "runtime-image.tar"
    attestation = prepare_receipt.get("archive")
    if (
        not isinstance(attestation, dict)
        or not archive.is_file()
        or archive.stat().st_size != attestation.get("bytes")
        or _sha256_file(archive) != attestation.get("sha256")
    ):
        raise RuntimeWorkflowError("runtime archive does not match prepare receipt")
    owner = secrets.token_hex(16)
    begin = _remote(
        "remote-begin",
        handle,
        owner,
        attestation["bytes"],
        attestation["sha256"],
        prepare_receipt["workflowFiles"]["module"],
        prepare_receipt["workflowFiles"]["wrapper"],
        check=False,
    )
    if begin.returncode != 0:
        _remote_failure(begin, "begin")
    _remote_receipt(begin, "cvm-browser-runtime.begin/1")
    destination = f"cvm:{REMOTE_ROOT}/.cvm-browser-runtime/{handle}/incoming/"
    try:
        _run(
            [
                "rsync", "-az", "--protect-args", os.fspath(archive),
                os.fspath(STATE_ROOT / handle / "prepare.json"), destination,
            ],
            timeout=1800,
        )
        completed = _remote("remote-provision", handle, owner, check=False)
        receipt = _remote_receipt(completed, PROVISION_SCHEMA)
    except BaseException:
        _remote("remote-abort", handle, owner, check=False)
        raise
    if (
        completed.returncode != 0
        or receipt.get("status") != "provisioned"
        or receipt.get("handle") != handle
        or receipt.get("ownerNonce") != owner
        or receipt.get("image") != prepare_receipt.get("image")
        or not isinstance(receipt.get("retainedImageId"), str)
        or IMAGE_ID.fullmatch(receipt["retainedImageId"]) is None
        or receipt.get("archiveSha256") != attestation["sha256"]
        or receipt.get("transferAbsent") is not True
    ):
        raise RuntimeWorkflowError("remote provision receipt is invalid")
    _write_json(STATE_ROOT / handle / "provision.json", receipt)
    return receipt


def _disk_gate(extra_bytes: int = 0) -> int:
    free = shutil.disk_usage(REPO_ROOT).free
    if free < MIN_FREE_BYTES + extra_bytes:
        raise RuntimeWorkflowError("CVM disk gate failed")
    return free


def _docker_server_gate() -> None:
    value = _strict_json(
        _docker("version", "--format", "{{json .Server}}", timeout=30).stdout,
        "Docker server",
    )
    if value.get("Os") != "linux" or value.get("Arch") != "amd64":
        raise RuntimeWorkflowError("Docker server must be linux/amd64")


def remote_begin(
    handle: str,
    owner: str,
    archive_bytes: int,
    archive_sha256: str,
    module_sha256: str,
    wrapper_sha256: str,
) -> Mapping[str, object]:
    _validate_handle(handle)
    if NONCE.fullmatch(owner) is None:
        raise RuntimeWorkflowError("owner nonce is invalid")
    if archive_bytes <= 0 or re.fullmatch(r"[0-9a-f]{64}", archive_sha256) is None:
        raise RuntimeWorkflowError("archive attestation is invalid")
    if {"module": module_sha256, "wrapper": wrapper_sha256} != _workflow_hashes():
        raise RuntimeWorkflowError("deployed workflow hash mismatch")
    free = _disk_gate(archive_bytes)
    _docker_server_gate()
    state = STATE_ROOT / handle
    try:
        (state / "incoming").mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RuntimeWorkflowError("remote handle already exists") from exc
    receipt: Mapping[str, object] = {
        "schema": "cvm-browser-runtime.begin/1",
        "status": "ready",
        "handle": handle,
        "ownerNonce": owner,
        "archive": {"bytes": archive_bytes, "sha256": archive_sha256},
        "workflowFiles": _workflow_hashes(),
        "freeBytes": free,
    }
    _write_json(state / "begin.json", receipt)
    return receipt


def _load_begin(handle: str, owner: str) -> Mapping[str, Any]:
    value = _load_receipt(handle, "begin.json", "cvm-browser-runtime.begin/1")
    if value.get("ownerNonce") != owner:
        raise RuntimeWorkflowError("remote owner does not match")
    return value


def _remote_provision_operation(handle: str, owner: str) -> Mapping[str, object]:
    begin = _load_begin(handle, owner)
    state = STATE_ROOT / handle
    incoming = state / "incoming"
    archive = incoming / "runtime-image.tar"
    prepare_path = incoming / "prepare.json"
    prepare_receipt = _strict_json(
        prepare_path.read_text(encoding="ascii"), "prepare receipt"
    )
    if prepare_receipt.get("schema") != PREPARE_SCHEMA or prepare_receipt.get("handle") != handle:
        raise RuntimeWorkflowError("prepare receipt identity is invalid")
    attestation = begin["archive"]
    if (
        prepare_receipt.get("archive") != attestation
        or not archive.is_file()
        or archive.stat().st_size != attestation["bytes"]
        or _sha256_file(archive) != attestation["sha256"]
    ):
        raise RuntimeWorkflowError("transferred archive is invalid")
    image = prepare_receipt.get("image")
    if not isinstance(image, dict) or image.get("role") != "runtime":
        raise RuntimeWorkflowError("runtime image receipt is invalid")
    reference = image.get("archiveReference")
    if not isinstance(reference, str) or _image_ids(reference):
        raise RuntimeWorkflowError("runtime archive reference is not fresh")
    _docker("image", "load", "--input", os.fspath(archive), timeout=1800)
    loaded = _image_ids(reference)
    if loaded != (image.get("id"),):
        raise RuntimeWorkflowError("loaded runtime image ID changed across transport")
    for path in (archive, prepare_path):
        path.unlink()
    incoming.rmdir()
    receipt: Mapping[str, object] = {
        "schema": PROVISION_SCHEMA,
        "status": "provisioned",
        "handle": handle,
        "ownerNonce": owner,
        "image": image,
        "retainedImageId": loaded[0],
        "archiveSha256": attestation["sha256"],
        "workflowFiles": _workflow_hashes(),
        "freeBytes": _disk_gate(),
        "transferAbsent": not incoming.exists(),
        "retryAllowed": False,
    }
    _write_json(state / "provision.json", receipt)
    return receipt


def _cleanup_incoming(handle: str) -> bool:
    incoming = STATE_ROOT / handle / "incoming"
    for filename in ("runtime-image.tar", "prepare.json"):
        try:
            (incoming / filename).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        incoming.rmdir()
    except (FileNotFoundError, OSError):
        pass
    return not incoming.exists()


def remote_provision(handle: str, owner: str) -> Mapping[str, object]:
    _validate_handle(handle)
    _claim_once(STATE_ROOT / handle, "provision", handle)
    try:
        return _remote_provision_operation(handle, owner)
    except BaseException:
        absent = _cleanup_incoming(handle)
        receipt: Mapping[str, object] = {
            "schema": PROVISION_SCHEMA,
            "status": "failed",
            "handle": handle,
            "ownerNonce": owner,
            "transferAbsent": absent,
            "retryAllowed": False,
        }
        _write_json(STATE_ROOT / handle / "provision.json", receipt)
        return receipt


def remote_abort(handle: str, owner: str) -> Mapping[str, object]:
    _load_begin(handle, owner)
    absent = _cleanup_incoming(handle)
    receipt: Mapping[str, object] = {
        "schema": "cvm-browser-runtime.abort/1",
        "status": "aborted" if absent else "cleanup-failed",
        "handle": handle,
        "ownerNonce": owner,
        "transferAbsent": absent,
        "retryAllowed": False,
    }
    _write_json(STATE_ROOT / handle / "abort.json", receipt)
    return receipt


def probe(handle: str) -> Mapping[str, object]:
    provision_receipt = _load_receipt(handle, "provision.json", PROVISION_SCHEMA)
    _claim_once(STATE_ROOT / handle, "probe", handle)
    completed = _remote("remote-probe", handle, check=False)
    receipt = _remote_receipt(completed, PROBE_SCHEMA)
    if (
        completed.returncode != 0
        or receipt.get("status") != "succeeded"
        or receipt.get("handle") != handle
        or receipt.get("retainedImageId") != provision_receipt.get("retainedImageId")
        or receipt.get("cleanupAbsent") is not True
        or receipt.get("retryAllowed") is not False
    ):
        raise RuntimeWorkflowError("remote Browser Runtime probe failed")
    _write_json(STATE_ROOT / handle / "probe.json", receipt)
    return receipt


def remote_probe(handle: str) -> Mapping[str, object]:
    provision_receipt = _load_receipt(handle, "provision.json", PROVISION_SCHEMA)
    _claim_once(STATE_ROOT / handle, "probe", handle)
    retained = provision_receipt.get("retainedImageId")
    owner = provision_receipt.get("ownerNonce")
    if not isinstance(retained, str) or IMAGE_ID.fullmatch(retained) is None:
        raise RuntimeWorkflowError("retained runtime image is invalid")
    if not isinstance(owner, str) or NONCE.fullmatch(owner) is None:
        raise RuntimeWorkflowError("runtime owner is invalid")
    if provision_receipt.get("workflowFiles") != _workflow_hashes():
        raise RuntimeWorkflowError("deployed workflow changed before probe")
    _disk_gate()
    from browser_runtime import BrowserRuntimeJob

    job = BrowserRuntimeJob(
        owner_nonce=owner,
        capability_dir=STATE_ROOT / handle / "probe-runtime",
        image_ref=retained,
    )
    preflight: Mapping[str, Any] | None = None
    capability: Mapping[str, Any] | None = None
    error: BaseException | None = None
    try:
        job.start()
        job.preflight()
        preflight = _strict_json(
            (job.capability_dir / "preflight.json").read_text(encoding="ascii"),
            "preflight receipt",
        )
        capability = _strict_json(
            (job.capability_dir / "runtime.json").read_text(encoding="ascii"),
            "runtime capability",
        )
    except BaseException as exc:
        error = exc
    finally:
        job.stop()
    container_absent = _docker(
        "container", "inspect", job.container_name, check=False, timeout=30
    ).returncode != 0
    network_absent = _docker(
        "network", "inspect", job.network_name, check=False, timeout=30
    ).returncode != 0
    cleanup_absent = container_absent and network_absent
    succeeded = bool(
        error is None
        and isinstance(preflight, dict)
        and preflight.get("passed") is True
        and isinstance(capability, dict)
        and capability.get("imageRef") == retained
        and capability.get("jobId") == owner
        and cleanup_absent
    )
    receipt: Mapping[str, object] = {
        "schema": PROBE_SCHEMA,
        "status": "succeeded" if succeeded else "failed",
        "handle": handle,
        "ownerNonce": owner,
        "retainedImageId": retained,
        "programDigest": preflight.get("programDigest") if isinstance(preflight, dict) else None,
        "pngSha256": preflight.get("pngSha256") if isinstance(preflight, dict) else None,
        "capabilitySchema": capability.get("schema") if isinstance(capability, dict) else None,
        "cleanupAbsent": cleanup_absent,
        "freeBytes": _disk_gate(),
        "retryAllowed": False,
    }
    _write_json(STATE_ROOT / handle / "probe.json", receipt)
    return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    operations = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = operations.add_parser("prepare")
    prepare_parser.add_argument("--source-revision", required=True)
    prepare_parser.add_argument("--runtime-image", required=True)
    for operation in ("provision", "probe", "remote-probe"):
        child = operations.add_parser(operation)
        child.add_argument("handle")
    begin = operations.add_parser("remote-begin")
    begin.add_argument("handle")
    begin.add_argument("owner")
    begin.add_argument("archive_bytes", type=int)
    begin.add_argument("archive_sha256")
    begin.add_argument("module_sha256")
    begin.add_argument("wrapper_sha256")
    remote_provision_parser = operations.add_parser("remote-provision")
    remote_provision_parser.add_argument("handle")
    remote_provision_parser.add_argument("owner")
    remote_abort_parser = operations.add_parser("remote-abort")
    remote_abort_parser.add_argument("handle")
    remote_abort_parser.add_argument("owner")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.operation == "prepare":
            receipt = prepare(args.source_revision, args.runtime_image)
        elif args.operation == "provision":
            receipt = provision(args.handle)
        elif args.operation == "probe":
            receipt = probe(args.handle)
        elif args.operation == "remote-begin":
            receipt = remote_begin(
                args.handle,
                args.owner,
                args.archive_bytes,
                args.archive_sha256,
                args.module_sha256,
                args.wrapper_sha256,
            )
        elif args.operation == "remote-provision":
            receipt = remote_provision(args.handle, args.owner)
        elif args.operation == "remote-abort":
            receipt = remote_abort(args.handle, args.owner)
        elif args.operation == "remote-probe":
            receipt = remote_probe(args.handle)
        else:
            raise RuntimeWorkflowError("unknown operation")
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0 if receipt.get("status") not in {"failed"} else 1
    except RuntimeWorkflowError as exc:
        print(f"cvm-browser-runtime: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
