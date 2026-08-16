#!/usr/bin/env python3
"""THROWAWAY shared identities and proofs for the SAR-003 seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
from typing import Mapping


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
JOB_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64}$")
MAC_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTITY_KEYS = (
    "jobId", "ownerNonce", "agentImageDigest", "agentConfigDigest",
    "runtimeManifestDigest", "sourceDigest", "inputDigest",
    "brokerAuthorityDigest", "workloadDigest",
)


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Digest:
    value: str

    def __post_init__(self) -> None:
        if not DIGEST_RE.fullmatch(self.value):
            raise ContractError("identity must be one full sha256 digest")


@dataclass(frozen=True)
class ExecutionIdentity:
    job_id: str
    owner_nonce: str
    agent_image_digest: Digest
    agent_config_digest: Digest
    runtime_manifest_digest: Digest
    source_digest: Digest
    input_digest: Digest
    broker_authority_digest: Digest
    workload_digest: Digest

    def __post_init__(self) -> None:
        if not JOB_RE.fullmatch(self.job_id):
            raise ContractError("invalid job identity")
        if not NONCE_RE.fullmatch(self.owner_nonce):
            raise ContractError("invalid owner nonce")

    def as_json(self) -> dict[str, str]:
        return {
            "jobId": self.job_id,
            "ownerNonce": self.owner_nonce,
            "agentImageDigest": self.agent_image_digest.value,
            "agentConfigDigest": self.agent_config_digest.value,
            "runtimeManifestDigest": self.runtime_manifest_digest.value,
            "sourceDigest": self.source_digest.value,
            "inputDigest": self.input_digest.value,
            "brokerAuthorityDigest": self.broker_authority_digest.value,
            "workloadDigest": self.workload_digest.value,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ExecutionIdentity":
        if any(not isinstance(value.get(key), str) for key in IDENTITY_KEYS):
            raise ContractError("identity fields are missing or invalid")
        return cls(
            job_id=str(value["jobId"]),
            owner_nonce=str(value["ownerNonce"]),
            agent_image_digest=Digest(str(value["agentImageDigest"])),
            agent_config_digest=Digest(str(value["agentConfigDigest"])),
            runtime_manifest_digest=Digest(str(value["runtimeManifestDigest"])),
            source_digest=Digest(str(value["sourceDigest"])),
            input_digest=Digest(str(value["inputDigest"])),
            broker_authority_digest=Digest(str(value["brokerAuthorityDigest"])),
            workload_digest=Digest(str(value["workloadDigest"])),
        )


def require_exact_record(
    value: Mapping[str, object], schema: str, identity: ExecutionIdentity,
    extra_keys: tuple[str, ...] = (),
) -> None:
    expected_keys = {"schema", *IDENTITY_KEYS, *extra_keys}
    if set(value) != expected_keys or value.get("schema") != schema:
        raise ContractError("protocol record shape is invalid")
    if ExecutionIdentity.from_mapping(value) != identity:
        raise ContractError("protocol record identity is invalid")


def canonical_tree_digest(root: Path) -> Digest:
    """Hash one closed path/content/mode tree for both outer and entrypoint."""
    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise ContractError("snapshot tree is not closed")
        kind = b"d" if stat.S_ISDIR(metadata.st_mode) else b"f"
        digest.update(
            kind + b"\0" + relative.encode("utf-8") + b"\0"
            + oct(stat.S_IMODE(metadata.st_mode)).encode("ascii") + b"\0"
        )
        if stat.S_ISREG(metadata.st_mode):
            digest.update(str(metadata.st_size).encode("ascii") + b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return Digest("sha256:" + digest.hexdigest())


def broker_mac(secret: bytes, identity: ExecutionIdentity, challenge: str) -> str:
    if len(secret) < 32 or not CHALLENGE_RE.fullmatch(challenge):
        raise ContractError("invalid Broker proof material")
    payload = {"challenge": challenge, **identity.as_json()}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, encoded, hashlib.sha256).hexdigest()


def workload_digest(argv: tuple[str, ...]) -> Digest:
    if (
        not argv or len(argv) > 64
        or not all(isinstance(item, str) and item and "\0" not in item for item in argv)
        or sum(len(item.encode("utf-8")) for item in argv) > 16384
        or not PurePosixPath(argv[0]).is_absolute()
        or ".." in PurePosixPath(argv[0]).parts
    ):
        raise ContractError("workload argv must be one closed nonempty absolute command")
    encoded = json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Digest("sha256:" + hashlib.sha256(encoded).hexdigest())


def verify_broker_mac(
    secret: bytes, identity: ExecutionIdentity, challenge: str, observed: object,
) -> bool:
    return (
        isinstance(observed, str)
        and MAC_RE.fullmatch(observed) is not None
        and hmac.compare_digest(broker_mac(secret, identity, challenge), observed)
    )
