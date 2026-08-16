#!/usr/bin/env python3
"""Build one offline deterministic SAI-005 Development OCI candidate."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import secrets
import shutil
import sys

from packages.agent_runtime.oci_builder import (
    BuildInputError,
    BuildRequest,
    audit_oci_archive,
    build_oci_layout,
    canonical_json_bytes,
    canonical_json_digest,
    encode_oci_archive,
    make_runtime_manifest,
    produce_external_artifacts,
    publish_exclusive_directory,
    publish_exclusive_file,
    read_exact_regular,
    resolve_elf_closure,
    spdx_json_bytes,
)
from scripts.pilot.agent_runtime import parse_canonical_json
from scripts.pilot.agent_runtime.external_admission import get_local_cas_artifact_locator


PROGRAMS = [
    ("bash", "/usr/bin/bash", "5.2.21(1)-release"),
    ("cat", "/usr/bin/cat", "coreutils-9.4"),
    ("chmod", "/usr/bin/chmod", "coreutils-9.4"),
    ("codex", "/usr/local/bin/codex", "0.147.0"),
    ("cp", "/usr/bin/cp", "coreutils-9.4"),
    ("env", "/usr/bin/env", "coreutils-9.4"),
    ("file", "/usr/bin/file", "5.45"),
    ("find", "/usr/bin/find", "4.9.0"),
    ("git", "/usr/bin/git", "2.43.0"),
    ("git-lfs", "/usr/bin/git-lfs", "3.4.1"),
    ("ls", "/usr/bin/ls", "coreutils-9.4"),
    ("mkdir", "/usr/bin/mkdir", "coreutils-9.4"),
    ("mv", "/usr/bin/mv", "coreutils-9.4"),
    ("node", "/usr/local/bin/node", "24.13.0"),
    ("ps", "/usr/bin/ps", "4.0.4"),
    ("python3", "/usr/bin/python3.12", "3.12.3"),
    ("rg", "/usr/bin/rg", "14.1.0"),
    ("rm", "/usr/bin/rm", "coreutils-9.4"),
    ("sed", "/usr/bin/sed", "4.9"),
    ("sha256sum", "/usr/bin/sha256sum", "coreutils-9.4"),
    ("stat", "/usr/bin/stat", "coreutils-9.4"),
]
PROJECT_PREFIXES = (
    "/usr/local/lib/python3.12/dist-packages",
    "/usr/local/lib/text-to-cad/implicitjs",
    "/usr/local/libexec",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXACT_INPUTS = {
    "qualifiedLocalRecord": (
        REPO_ROOT / "models/agent-runtime/cup_cup_033/meshscope-development/candidate.json",
        2_095,
        "sha256:0ac123449e0042cfa0bcc231d27f3c624aa8c092d1351131768755fc3bb2f766",
    ),
    "localCasLocatorManifest": (
        REPO_ROOT / "packages/agent_runtime/external/local-cas-byte-locators.json",
        6_817,
        "sha256:9f068c5b6c3d03eae562a5da5a872abd3370ea1fc1cee46b26c330ce49e60e66",
    ),
    "runtimeOsBuildReceipt": (
        REPO_ROOT / "packages/agent_runtime/external/builder/runtime-os-network-denial-launch-receipt.json",
        1_896,
        "sha256:e7a90014dd9c8dcceb3860c197214bac4a0b424cd9ef8e76e7886a955213ae6d",
    ),
    "runtimeDebClosure": (
        REPO_ROOT / "packages/agent_runtime/external/builder/noble-runtime-deb-closure-candidate.json",
        21_263,
        "sha256:b2a05d9ffea54f8bfa4fcd0ec254c0cbde272f3a8b0fb31d81d0632802356820",
    ),
    "pythonWheelLock": (
        REPO_ROOT / "packages/agent_runtime/external/python/python-wheel-lock.json",
        1_956,
        "sha256:cb41d9710da2617ffd43485cfa69cec7fabb1742621fd30d13d6c99a18598091",
    ),
    "cupCapabilityManifest": (
        REPO_ROOT / "models/agent-runtime/cup_cup_033/cup-capability-manifest.json",
        2_954,
        "sha256:903d589bc6f3808849e8521349f4c625b20ecbdf103f0c1fbfe7f672a136d6a8",
    ),
}
CANONICAL_SEAM = (
    REPO_ROOT / "scripts/pilot/agent_runtime/canonical_json.py",
    9_869,
    "sha256:aea1933df81469dcb88073e12d67bb5f165a3b4f6b7787a6a45650bb9422bbc3",
)


def _validate_installed_canonical_seam(rootfs: Path) -> None:
    source, size, digest = CANONICAL_SEAM
    source_bytes = read_exact_regular(source, digest=digest, size=size)
    installed = rootfs / "usr/local/libexec/agent_runtime_canonical_json.py"
    installed_bytes = read_exact_regular(installed, digest=digest, size=size)
    if source_bytes != installed_bytes or installed.lstat().st_mode & 0o777 != 0o444:
        raise BuildInputError("image canonical JSON seam is not the exact vendored module")


def _read_exact_inputs() -> dict:
    records = {}
    for name, (path, size, digest) in EXACT_INPUTS.items():
        payload = read_exact_regular(path, digest=digest, size=size)
        records[name] = parse_canonical_json(payload)
    return records


def _exact_build_inputs(runtime_manifest: dict) -> dict:
    records = _read_exact_inputs()
    qualified = records["qualifiedLocalRecord"]
    local_cas = records["localCasLocatorManifest"]
    runtime_os = records["runtimeOsBuildReceipt"]
    if (
        qualified["status"] != "development-candidate"
        or qualified["localDevelopmentAdmission"]["status"] != "qualified-local-candidate"
        or qualified["localDevelopmentAdmission"]["formalAdmission"] is not False
        or local_cas["formalAdmission"] is not False
        or local_cas["immutableMirrorVisible"] is not False
        or runtime_os["result"] != "network-disabled-build-succeeded"
        or runtime_os["networkMode"] != "none"
        or runtime_os["pull"] is not False
    ):
        raise BuildInputError("SAI-003/004 Development input authority is invalid")
    project_records = [
        record
        for record in runtime_manifest["runtimeFiles"]
        if any(record["path"] == prefix or record["path"].startswith(prefix + "/") for prefix in PROJECT_PREFIXES)
    ]
    project_digest = canonical_json_digest(project_records)
    dependency_digest = canonical_json_digest(
        {
            "localCasLocatorManifestDigest": EXACT_INPUTS["localCasLocatorManifest"][2],
            "pythonWheelLockDigest": EXACT_INPUTS["pythonWheelLock"][2],
            "runtimeDebClosureDigest": EXACT_INPUTS["runtimeDebClosure"][2],
        }
    )
    recipe = {
        "archiveFormat": "ustar-v1",
        "compression": "text-to-cad.stored-deflate-v1",
        "compressionLevel": 0,
        "config": {
            "cmd": [],
            "entrypoint": ["/usr/local/libexec/text-to-cad-agent-entrypoint"],
            "user": "65532:65532",
            "workingDir": "/work",
        },
        "gzipMtime": 0,
        "gzipOs": 255,
        "layerCount": 1,
        "mtime": 0,
        "platform": {"architecture": "amd64", "os": "linux"},
        "uid": 0,
        "gid": 0,
    }
    recipe_digest = canonical_json_digest(recipe)
    identity = {
        "baseImageManifestDigest": "sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea",
        "buildRecipeDigest": recipe_digest,
        "dependencyLockDigest": dependency_digest,
        "projectRuntimeArtifactSetDigest": project_digest,
        "ubuntuSnapshotManifestDigest": "sha256:d6f8f3ca10c0f22b6038b215b24a79c48515a4ccd51f67eec93b79e41b805e3f",
    }
    return {
        **identity,
        "buildInputSetDigest": canonical_json_digest(identity),
        "builderImageId": runtime_os["builderImageId"],
        "cupCapabilityManifestDigest": EXACT_INPUTS["cupCapabilityManifest"][2],
        "localCasLocatorManifestDigest": EXACT_INPUTS["localCasLocatorManifest"][2],
        "qualifiedLocalRecordDigest": EXACT_INPUTS["qualifiedLocalRecord"][2],
        "recipe": recipe,
        "runtimeOsImageId": runtime_os["runtimeImageId"],
    }


def _exact_spdx_packages(runtime_manifest: dict) -> list[dict]:
    records = _read_exact_inputs()
    result = [
        {
            "digest": item["digest"],
            "fileName": item["localFilename"],
            "name": item["package"],
            "version": item["version"],
        }
        for item in records["runtimeDebClosure"]["packages"]
    ]
    result.extend(
        {
            "digest": item["digest"],
            "fileName": item["filename"],
            "name": item["distribution"],
            "version": item["version"],
        }
        for item in records["pythonWheelLock"]["runtimeArtifacts"]
    )
    artifacts = {item["artifactId"]: item for item in records["localCasLocatorManifest"]["artifacts"]}
    for artifact_id, name in (("node.archive", "node"), ("codex.executable", "codex")):
        item = artifacts[artifact_id]
        result.append({
            "digest": item["digest"],
            "fileName": artifact_id,
            "name": name,
            "version": item["version"],
        })
    qualified = records["qualifiedLocalRecord"]
    result.append({
        "digest": qualified["reproducibility"]["wheel"]["sha256"],
        "fileName": qualified["reproducibility"]["wheel"]["path"],
        "name": "meshscope",
        "version": "0.1.0",
    })
    for name, prefix in (("meshshot", "/usr/local/lib/python3.12/dist-packages/meshshot"), ("text-to-cad-implicit-runtime", "/usr/local/lib/text-to-cad/implicitjs")):
        members = [item for item in runtime_manifest["runtimeFiles"] if item["path"] == prefix or item["path"].startswith(prefix + "/")]
        if not members:
            raise BuildInputError(f"project package is absent: {name}")
        result.append({
            "digest": canonical_json_digest(members),
            "fileName": prefix,
            "name": name,
            "version": "0.1.0",
        })
    return sorted(result, key=lambda item: (item["name"], item["version"], item["digest"]))


def _project_elf_paths(rootfs: Path) -> list[str]:
    result = [path for _name, path, _version in PROGRAMS]
    for prefix in PROJECT_PREFIXES:
        host = rootfs / prefix.removeprefix("/")
        for path in sorted(host.rglob("*")):
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        result.append("/" + path.relative_to(rootfs).as_posix())
    return result


def _exact_catalog() -> object:
    wheel = get_local_cas_artifact_locator("spdx.license-list-wheel")
    catalog = get_local_cas_artifact_locator("spdx.license-catalog")
    # The integrated SAI-004 helper already fixes path/digest/size/mode. Rehash
    # here at the consumer boundary before allowing the bytes into SAI-005.
    payloads = {}
    for record in (wheel, catalog):
        payload = read_exact_regular(record.path, digest=record.digest, size=record.bytes)
        payloads[record.artifact_id] = payload
        if record.path.lstat().st_mode & 0o777 != 0o444:
            raise RuntimeError(f"local CAS artifact changed: {record.artifact_id}")
    return parse_canonical_json(payloads[catalog.artifact_id])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    transaction_layout = args.output.with_name(
        f".{args.output.name}.transaction-{secrets.token_hex(8)}"
    )
    archive_path = args.output.with_name(args.output.name + ".oci.tar")
    published_layout = False
    published_archive = False
    published_receipt = False
    try:
        _validate_installed_canonical_seam(args.rootfs)
        libraries = resolve_elf_closure(args.rootfs, _project_elf_paths(args.rootfs))
        runtime_manifest = make_runtime_manifest(
            args.rootfs,
            programs=PROGRAMS,
            native_libraries=libraries,
            project_prefixes=PROJECT_PREFIXES,
        )
        build_inputs = _exact_build_inputs(runtime_manifest)
        build = build_oci_layout(BuildRequest(args.rootfs, runtime_manifest), transaction_layout)
        archive_bytes = encode_oci_archive(transaction_layout)
        if audit_oci_archive(archive_bytes) != build:
            raise BuildInputError("OCI archive does not reproduce the audited layout")
        build = {
            **build,
            "archiveBytes": len(archive_bytes),
            "archiveDigest": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
        }
        artifacts = produce_external_artifacts(
            transaction_layout,
            agent_manifest_digest=build["manifestDigest"],
            license_catalog=_exact_catalog(),
            package_inventory=_exact_spdx_packages(runtime_manifest),
        )
        if artifacts["browserScanReceipt"]["result"] != "accepted":
            raise BuildInputError("browser-deny scan rejected the Agent rootfs")
        receipt = {
            "agentRuntimeVerified": False,
            "artifacts": {
                "browserInventoryDigest": "sha256:" + hashlib.sha256(canonical_json_bytes(artifacts["browserInventory"])).hexdigest(),
                "browserScanReceiptDigest": "sha256:" + hashlib.sha256(canonical_json_bytes(artifacts["browserScanReceipt"])).hexdigest(),
                "sbomDigest": "sha256:" + hashlib.sha256(spdx_json_bytes(artifacts["sbom"])).hexdigest(),
            },
            "blockers": ["external-formal-admission", "immutable-mirror-visibility", "sbom-license-conclusions"],
            "build": build,
            "buildInputs": build_inputs,
            "formalAdmission": False,
            "immutableMirrorVisible": False,
            "schema": "text-to-cad.agent-runtime-development-oci-build-receipt/1",
            "status": "development-build",
        }
        receipt_bytes = canonical_json_bytes(receipt)
    except Exception:
        if transaction_layout.exists() and not transaction_layout.is_symlink():
            shutil.rmtree(transaction_layout)
        raise
    try:
        publish_exclusive_directory(transaction_layout, args.output)
        published_layout = True
        publish_exclusive_file(archive_path, archive_bytes, 0o444)
        published_archive = True
        publish_exclusive_file(args.receipt, receipt_bytes, 0o444)
        published_receipt = True
    except Exception:
        if published_receipt:
            args.receipt.unlink()
        if published_archive:
            archive_path.unlink()
        if published_layout:
            shutil.rmtree(args.output)
        if transaction_layout.exists() and not transaction_layout.is_symlink():
            shutil.rmtree(transaction_layout)
        raise
    sys.stdout.buffer.write(receipt_bytes + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
