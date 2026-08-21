#!/usr/bin/env python3
"""Install and probe the one exact Browser Runtime image on CVM."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.fspath(REPO_ROOT / "packages/browser_runtime/src"))
from browser_runtime import BrowserRuntimeJob, HOST_IMAGE_LOCK_PATH
from browser_runtime.config import CAD_RENDER_PROGRAMS


STATE_ROOT = HOST_IMAGE_LOCK_PATH.parent
SOURCE_IMAGE_LOCK = REPO_ROOT / "packages/browser_runtime/image/image-lock.json"
PROBE_RECEIPT = STATE_ROOT / "probe.json"
REMOTE_ROOT = "~/text-to-cad"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MIN_FREE_BYTES = 3 * 1024 * 1024 * 1024


class RuntimeWorkflowError(RuntimeError):
    """Closed Browser Runtime install/probe failure."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _strict_json(text: str, label: str) -> Mapping[str, Any]:
    def unique(pairs):
        value = {}
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _replace_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeWorkflowError("temporary state file already exists")
    committed = False
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            payload = memoryview(_canonical(value))
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:
                    raise OSError("short state write")
                payload = payload[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        committed = True
    finally:
        if not committed:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _run(
    argv: Sequence[str], *, check: bool = True, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(argv), check=False, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeWorkflowError("fixed command failed") from exc
    if check and completed.returncode != 0:
        raise RuntimeWorkflowError("fixed command failed")
    return completed


def _docker(*argv: str, check: bool = True, timeout: int = 300):
    return _run(["docker", *argv], check=check, timeout=timeout)


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
        key: _docker("image", "inspect", image_id, "--format", fmt).stdout.strip()
        for key, fmt in fields.items()
    }
    if (
        observed["id"] != image_id
        or observed["os"] != "linux"
        or observed["architecture"] != "amd64"
        or REVISION.fullmatch(observed["revision"]) is None
    ):
        raise RuntimeWorkflowError("runtime image attestation is invalid")
    return {
        "id": image_id,
        "platform": "linux/amd64",
        "sourceRevision": observed["revision"],
    }


def _image_ids(reference: str) -> tuple[str, ...]:
    result = _docker(
        "image", "ls", "--all", "--no-trunc", "--quiet", reference, check=False
    )
    if result.returncode != 0:
        raise RuntimeWorkflowError("runtime image inventory failed")
    values = tuple(line for line in result.stdout.splitlines() if line)
    if any(IMAGE_ID.fullmatch(value) is None for value in values):
        raise RuntimeWorkflowError("runtime image inventory is invalid")
    return values


def _source_lock(source_revision: str, source_image_id: str) -> dict[str, Any]:
    value = dict(
        _strict_json(SOURCE_IMAGE_LOCK.read_text(encoding="utf-8"), "source image lock")
    )
    image = value.get("image")
    if (
        set(value) != {"schema_version", "image", "built_from_ref", "notes"}
        or value.get("schema_version") != 1
        or value.get("built_from_ref") != source_revision
        or not isinstance(image, dict)
        or image.get("id") != source_image_id
        or image.get("architecture") != "amd64"
    ):
        raise RuntimeWorkflowError("source image lock does not match install request")
    return value


def _retention_reference(image_id: str) -> str:
    if IMAGE_ID.fullmatch(image_id) is None:
        raise RuntimeWorkflowError("retained image ID is invalid")
    return "text-to-cad-browser-runtime-retained:" + image_id.removeprefix("sha256:")


def _remove_reference(reference: str) -> bool:
    _docker("image", "rm", reference, check=False)
    return not _image_ids(reference)


def _remote(operation: str, *args: object, stdin=None, timeout: int = 300):
    command = " ".join(
        ["python3", "-m", "scripts.pilot.cvm_browser_runtime", operation]
        + [str(value) for value in args]
    )
    try:
        completed = subprocess.run(
            ["ssh", "cvm", f"cd {REMOTE_ROOT} && {command}"],
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeWorkflowError("remote Browser Runtime operation failed") from exc
    stdout = completed.stdout.decode("utf-8", "replace")
    stderr = completed.stderr.decode("utf-8", "replace")
    if completed.returncode != 0:
        details = [
            line.removeprefix("cvm-browser-runtime: ")
            for line in stderr.splitlines()
            if line.startswith("cvm-browser-runtime: ")
        ]
        raise RuntimeWorkflowError(
            details[-1] if len(details) == 1 else "remote Browser Runtime operation failed"
        )
    lines = [line for line in stdout.splitlines() if line]
    if not lines:
        raise RuntimeWorkflowError("remote Browser Runtime returned no receipt")
    return _strict_json(lines[-1], "remote receipt")


def _validate_install_receipt(
    receipt: Mapping[str, Any],
    source_revision: str,
    runtime_image: str,
    archive_sha256: str,
) -> None:
    expected = {
        "schema", "status", "sourceImageId", "imageId", "sourceRevision",
        "platform", "retentionReference", "archiveSha256", "hostLockSha256",
        "transportAbsent",
    }
    expected_lock = _source_lock(source_revision, runtime_image)
    expected_image = dict(expected_lock["image"])
    if isinstance(receipt.get("imageId"), str):
        expected_image["id"] = receipt["imageId"]
    expected_lock["image"] = expected_image
    expected_lock["host"] = {
        "sourceImageId": runtime_image,
        "retentionReference": receipt.get("retentionReference"),
        "archiveSha256": archive_sha256,
    }
    expected_lock_sha256 = _sha256_bytes(_canonical(expected_lock))
    if (
        set(receipt) != expected
        or receipt.get("schema") != "cvm-browser-runtime.install/1"
        or receipt.get("status") != "installed"
        or receipt.get("sourceImageId") != runtime_image
        or receipt.get("sourceRevision") != source_revision
        or receipt.get("platform") != "linux/amd64"
        or not isinstance(receipt.get("imageId"), str)
        or IMAGE_ID.fullmatch(receipt["imageId"]) is None
        or receipt.get("retentionReference")
        != _retention_reference(receipt["imageId"])
        or receipt.get("archiveSha256") != archive_sha256
        or receipt.get("hostLockSha256") != expected_lock_sha256
        or receipt.get("transportAbsent") is not True
    ):
        raise RuntimeWorkflowError("remote install receipt is invalid")


def install(source_revision: str, runtime_image: str) -> Mapping[str, object]:
    if REVISION.fullmatch(source_revision) is None:
        raise RuntimeWorkflowError("source revision must be an exact Git SHA")
    image = _inspect_image(runtime_image)
    if image["sourceRevision"] != source_revision:
        raise RuntimeWorkflowError("runtime image source revision does not match")
    _source_lock(source_revision, runtime_image)
    transport = f"text-to-cad-browser-runtime-transfer:{secrets.token_hex(12)}"
    with tempfile.TemporaryDirectory(prefix="cvm-browser-runtime-") as temp:
        archive = Path(temp) / "runtime-image.tar"
        tagged = False
        try:
            if _image_ids(transport):
                raise RuntimeWorkflowError("temporary transport reference exists")
            tagged = True
            _docker("image", "tag", runtime_image, transport)
            _docker("image", "save", "--output", os.fspath(archive), transport, timeout=1800)
            if not archive.is_file() or archive.stat().st_size <= 0:
                raise RuntimeWorkflowError("runtime archive is empty")
            archive_sha256 = _sha256_file(archive)
            with archive.open("rb") as stream:
                receipt = _remote(
                    "remote-install",
                    source_revision,
                    runtime_image,
                    transport,
                    archive.stat().st_size,
                    archive_sha256,
                    stdin=stream,
                    timeout=1800,
                )
        finally:
            if tagged and not _remove_reference(transport):
                raise RuntimeWorkflowError("local transport reference cleanup failed")
    _validate_install_receipt(
        receipt, source_revision, runtime_image, archive_sha256
    )
    return receipt


def _read_archive(expected_bytes: int, expected_sha256: str) -> Path:
    if expected_bytes <= 0 or HEX64.fullmatch(expected_sha256) is None:
        raise RuntimeWorkflowError("archive attestation is invalid")
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(STATE_ROOT).free < MIN_FREE_BYTES + expected_bytes:
        raise RuntimeWorkflowError("CVM disk gate failed")
    incoming = STATE_ROOT / "incoming.tar"
    if incoming.exists() or incoming.is_symlink():
        raise RuntimeWorkflowError("incoming runtime archive already exists")
    digest = hashlib.sha256()
    remaining = expected_bytes
    try:
        with incoming.open("xb") as output:
            while remaining:
                block = sys.stdin.buffer.read(min(1024 * 1024, remaining))
                if not block:
                    raise RuntimeWorkflowError("runtime archive ended early")
                output.write(block)
                digest.update(block)
                remaining -= len(block)
            if sys.stdin.buffer.read(1):
                raise RuntimeWorkflowError("runtime archive has trailing bytes")
        if digest.hexdigest() != expected_sha256:
            raise RuntimeWorkflowError("runtime archive digest mismatch")
        return incoming
    except BaseException:
        incoming.unlink(missing_ok=True)
        raise


def remote_install(
    source_revision: str,
    source_image_id: str,
    transport: str,
    archive_bytes: int,
    archive_sha256: str,
) -> Mapping[str, object]:
    if (
        REVISION.fullmatch(source_revision) is None
        or IMAGE_ID.fullmatch(source_image_id) is None
        or re.fullmatch(r"text-to-cad-browser-runtime-transfer:[0-9a-f]{24}", transport)
        is None
    ):
        raise RuntimeWorkflowError("install identity is invalid")
    source_lock = _source_lock(source_revision, source_image_id)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        STATE_ROOT / "install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    incoming: Path | None = None
    retention: str | None = None
    retention_created = False
    installed = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        incoming = _read_archive(archive_bytes, archive_sha256)
        if _image_ids(transport):
            raise RuntimeWorkflowError("transport reference is not fresh")
        _docker("image", "load", "--input", os.fspath(incoming), timeout=1800)
        loaded = _image_ids(transport)
        if len(loaded) != 1:
            raise RuntimeWorkflowError("loaded image inventory is invalid")
        retained = _inspect_image(loaded[0])
        if retained["sourceRevision"] != source_revision:
            raise RuntimeWorkflowError("loaded image revision changed")
        retention = _retention_reference(loaded[0])
        existing = _image_ids(retention)
        if existing not in {(), (loaded[0],)}:
            raise RuntimeWorkflowError("retention reference is occupied")
        if not existing:
            retention_created = True
            _docker("image", "tag", loaded[0], retention)
        if _image_ids(retention) != (loaded[0],):
            raise RuntimeWorkflowError("retention reference is invalid")
        if not _remove_reference(transport):
            raise RuntimeWorkflowError("transport reference cleanup failed")
        if _inspect_image(loaded[0])["id"] != loaded[0]:
            raise RuntimeWorkflowError("retained image is unavailable")
        host_lock = dict(source_lock)
        host_image = dict(source_lock["image"])
        host_image["id"] = loaded[0]
        host_lock["image"] = host_image
        host_lock["host"] = {
            "sourceImageId": source_image_id,
            "retentionReference": retention,
            "archiveSha256": archive_sha256,
        }
        host_lock_sha256 = _sha256_bytes(_canonical(host_lock))
        incoming.unlink()
        incoming = None
        if shutil.disk_usage(STATE_ROOT).free < MIN_FREE_BYTES:
            raise RuntimeWorkflowError("CVM final disk gate failed")
        PROBE_RECEIPT.unlink(missing_ok=True)
        _replace_json(HOST_IMAGE_LOCK_PATH, host_lock)
        installed = True
        receipt: Mapping[str, object] = {
            "schema": "cvm-browser-runtime.install/1",
            "status": "installed",
            "sourceImageId": source_image_id,
            "imageId": loaded[0],
            "sourceRevision": source_revision,
            "platform": "linux/amd64",
            "retentionReference": retention,
            "archiveSha256": archive_sha256,
            "hostLockSha256": host_lock_sha256,
            "transportAbsent": True,
        }
        return receipt
    finally:
        cleanup_errors: list[BaseException] = []
        try:
            if not installed and incoming is not None:
                try:
                    incoming.unlink(missing_ok=True)
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if not installed:
                try:
                    if _image_ids(transport) and not _remove_reference(transport):
                        cleanup_errors.append(
                            RuntimeWorkflowError("transport reference cleanup failed")
                        )
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if (
                retention_created
                and not installed
                and retention is not None
            ):
                try:
                    if not _remove_reference(retention):
                        cleanup_errors.append(
                            RuntimeWorkflowError("retention reference cleanup failed")
                        )
                except BaseException as exc:
                    cleanup_errors.append(exc)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        if cleanup_errors:
            raise RuntimeWorkflowError("Browser Runtime install cleanup failed")


def probe() -> Mapping[str, object]:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        STATE_ROOT / "install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _probe_locked()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _probe_locked() -> Mapping[str, object]:
    if not HOST_IMAGE_LOCK_PATH.is_file() or HOST_IMAGE_LOCK_PATH.is_symlink():
        raise RuntimeWorkflowError("Browser Runtime host lock is unavailable")
    lock = _strict_json(HOST_IMAGE_LOCK_PATH.read_text(encoding="utf-8"), "host lock")
    image = lock.get("image")
    image_id = image.get("id") if isinstance(image, dict) else None
    if not isinstance(image_id, str) or IMAGE_ID.fullmatch(image_id) is None:
        raise RuntimeWorkflowError("Browser Runtime host lock is invalid")
    owner = secrets.token_hex(16)
    capability_dir = STATE_ROOT / f"probe-{owner}"
    job = BrowserRuntimeJob(
        owner_nonce=owner,
        capability_dir=capability_dir,
        image_lock_path=HOST_IMAGE_LOCK_PATH,
    )
    try:
        job.start()
        job.preflight()
        preflight = _strict_json(
            (capability_dir / "preflight.json").read_text(encoding="ascii"),
            "preflight receipt",
        )
        capability = _strict_json(
            (capability_dir / "runtime.json").read_text(encoding="ascii"),
            "runtime capability",
        )
    except Exception as exc:
        raise RuntimeWorkflowError("Browser Runtime probe failed") from exc
    finally:
        job.stop()
    cleanup = (
        _docker("container", "inspect", job.container_name, check=False).returncode != 0
        and _docker("network", "inspect", job.network_name, check=False).returncode != 0
    )
    if (
        preflight.get("passed") is not True
        or preflight.get("programDigest") != CAD_RENDER_PROGRAMS["residual"]
        or capability.get("imageRef") != image_id
        or not cleanup
    ):
        raise RuntimeWorkflowError("Browser Runtime probe failed")
    receipt: Mapping[str, object] = {
        "schema": "cvm-browser-runtime.probe/2",
        "status": "succeeded",
        "imageId": image_id,
        "programDigest": preflight["programDigest"],
        "pngSha256": preflight["pngSha256"],
        "capabilitySchema": capability["schema"],
        "cleanupAbsent": True,
    }
    _replace_json(PROBE_RECEIPT, receipt)
    shutil.rmtree(capability_dir)
    return receipt


def status() -> Mapping[str, object]:
    values: dict[str, object] = {}
    for name, path in (
        ("hostLock", HOST_IMAGE_LOCK_PATH),
        ("probe", PROBE_RECEIPT),
    ):
        if path.is_file() and not path.is_symlink():
            values[name] = _strict_json(path.read_text(encoding="utf-8"), name)
    return {"schema": "cvm-browser-runtime.status/2", "status": "observed", **values}


def _validate_probe_receipt(value: Mapping[str, Any], expected_image_id: str) -> None:
    if (
        set(value)
        != {
            "schema", "status", "imageId", "programDigest", "pngSha256",
            "capabilitySchema", "cleanupAbsent",
        }
        or value.get("schema") != "cvm-browser-runtime.probe/2"
        or value.get("status") != "succeeded"
        or value.get("imageId") != expected_image_id
        or value.get("programDigest") != CAD_RENDER_PROGRAMS["residual"]
        or not isinstance(value.get("pngSha256"), str)
        or IMAGE_ID.fullmatch(value["pngSha256"]) is None
        or value.get("capabilitySchema")
        != "text-to-cad.browser-runtime-capability/1"
        or value.get("cleanupAbsent") is not True
    ):
        raise RuntimeWorkflowError("remote probe receipt is invalid")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    operations = parser.add_subparsers(dest="operation", required=True)
    install_parser = operations.add_parser("install")
    install_parser.add_argument("--source-revision", required=True)
    install_parser.add_argument("--runtime-image", required=True)
    operations.add_parser("probe")
    operations.add_parser("status")
    remote_install_parser = operations.add_parser("remote-install")
    remote_install_parser.add_argument("source_revision")
    remote_install_parser.add_argument("source_image_id")
    remote_install_parser.add_argument("transport")
    remote_install_parser.add_argument("archive_bytes", type=int)
    remote_install_parser.add_argument("archive_sha256")
    operations.add_parser("remote-probe")
    operations.add_parser("remote-status")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.operation == "install":
            value = install(args.source_revision, args.runtime_image)
        elif args.operation == "remote-install":
            value = remote_install(
                args.source_revision,
                args.source_image_id,
                args.transport,
                args.archive_bytes,
                args.archive_sha256,
            )
        elif args.operation == "probe":
            before = _remote("remote-status")
            before_lock = before.get("hostLock")
            before_image = (
                before_lock.get("image")
                if isinstance(before_lock, dict)
                else None
            )
            expected_image_id = (
                before_image.get("id") if isinstance(before_image, dict) else None
            )
            if (
                not isinstance(expected_image_id, str)
                or IMAGE_ID.fullmatch(expected_image_id) is None
            ):
                raise RuntimeWorkflowError("remote Browser Runtime host lock is invalid")
            value = _remote("remote-probe", timeout=600)
            _validate_probe_receipt(value, expected_image_id)
        elif args.operation == "remote-probe":
            value = probe()
        elif args.operation == "status":
            value = _remote("remote-status")
        else:
            value = status()
    except (RuntimeWorkflowError, OSError, KeyError, ValueError) as exc:
        print(f"cvm-browser-runtime: {exc}", file=sys.stderr)
        return 1
    print(_canonical(value).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
