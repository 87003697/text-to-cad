"""Closed external-byte admission contracts for the sealed Agent runtime."""

from __future__ import annotations

import ast
import hashlib
import base64
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import socket
import sys
import tempfile
import lzma
import zipfile
from urllib.parse import urlsplit
from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Protocol, cast

from .canonical_json import (
    CanonicalJSONInput,
    CanonicalJSONValue,
    canonical_json_bytes,
    canonical_json_digest,
    parse_canonical_json,
)


class ExternalAdmissionError(ValueError):
    """An external admission document violates its closed contract."""


class ExternalMirrorPublishError(ExternalAdmissionError):
    """A normalized mirror error with an explicit possible-write boundary."""

    def __init__(self, message: str, *, may_have_written: bool) -> None:
        super().__init__(message)
        self.may_have_written = may_have_written


@dataclass(frozen=True)
class ExternalAdmissionDocument:
    """One typed, recursively immutable external admission document."""

    kind: str
    value: Mapping[str, CanonicalJSONValue]

    def __post_init__(self) -> None:
        if type(self.kind) is not str or not self.kind:
            raise ExternalAdmissionError("external document kind must be a nonempty string")
        if not isinstance(self.value, Mapping):
            raise ExternalAdmissionError("external document value must be an object")
        object.__setattr__(self, "value", _freeze_mapping(self.value))


@dataclass(frozen=True)
class CodexOfflineVerificationPlan:
    """Exact offline Cosign replay plus its three adversarial controls."""

    positive_args: tuple[str, ...]
    positive_environment: Mapping[str, str]
    wrong_artifact_args: tuple[str, ...]
    wrong_identity_args: tuple[str, ...]
    wrong_rekor_environment: Mapping[str, str]


@dataclass(frozen=True)
class CodexTrustMaterial:
    """PEM files deterministically derived from the approved trusted root."""

    ca_root: Path
    ca_intermediate: Path
    rekor_key: Path
    ct_key: Path


@dataclass(frozen=True)
class StableBlobSnapshot:
    """One verified private snapshot produced from a single no-follow open."""

    path: Path
    digest: str
    bytes: int


@dataclass(frozen=True)
class LocalCASArtifactLocator:
    """One exact, read-only local CAS record for downstream consumers."""

    artifact_id: str
    path: Path
    bytes: int
    digest: str
    mode: str


class ExternalMirrorStore(Protocol):
    """Minimal create-only, exact-version external mirror adapter."""

    def versioning_status(self, bucket: str) -> str | None: ...

    def current_version(self, bucket: str, key: str) -> tuple[str, str] | None: ...

    def put_create_only(self, bucket: str, key: str, payload: bytes) -> tuple[str, str]: ...

    def get_exact_version(self, bucket: str, key: str, version_id: str) -> bytes: ...


CODEX_APPROVAL_DIGEST = (
    "sha256:85bf8165e3ded898ec4892c8ae3ab48172566d871c79add02c96f389f663d5c4"
)
CODEX_POLICY_DIGEST = (
    "sha256:8bfd47abc5c13845f82a218fe79ac2378adb29c4cb302a8b9a41eb631f3451d2"
)
CODEX_PROOF_RECEIPT_DIGEST = (
    "sha256:ee8632e8d7e9610014e0d59e0b074414540e6e6b5e8feca04d2ef519db488e84"
)
CODEX_FORMAL_RECEIPT_DIGEST = (
    "sha256:9a310c7a26bb8037af9647e93cdfa279855ad3644f31ae307a38f575fa20f0f2"
)
CODEX_RETRIEVAL_RECEIPT_DIGEST = (
    "sha256:8441d9ab8c6b0b75703fba5da1b6ccb16aea28f431ef1a8c963583d7a4c6e331"
)
NOBLE_DEB_CLOSURE_DIGEST = (
    "sha256:1d389e9bccb3f8cfac0120d1c2596d92794660fbffb75f3ff57a072d6c32de8c"
)
_NOBLE_DEB_CLOSURE_FILE_DIGEST = (
    "sha256:d8991abf6429b21426a7109f22e5adb3b32a158c9fe258cedc4240d4dd173552"
)
BUILDER_REPRODUCIBILITY_DIAGNOSTIC_DIGEST = (
    "sha256:a8c99137e96c6e520be9ce2d3a50c8d6976891f37111d28e13e0742d1068b024"
)
BUILDER_INPUT_CANDIDATE_DIGEST = (
    "sha256:d1bc0a6b4865dc220973afe503dbe75ada247b2e5ce48e4717cb3890f24a1b57"
)
BUILDER_NETWORK_DENIAL_RECEIPT_DIGEST = (
    "sha256:beadfa37c1dfe9dd78c2bc0856d75e191f9901af0d1f4eabe4a10594d9285ef1"
)
NOBLE_DEB_REPLAY_RECEIPT_DIGEST = (
    "sha256:b9f5c776ce1888ec43edafc7b2934f16d1b7351d18ee5c51b27b43a7373a8de4"
)
NOBLE_RUNTIME_DEB_CLOSURE_DIGEST = (
    "sha256:0e5690a15585657ae71452605f69ad3b5f81874630c0a1989838401595f06b89"
)
_NOBLE_RUNTIME_DEB_CLOSURE_FILE_DIGEST = (
    "sha256:b2a05d9ffea54f8bfa4fcd0ec254c0cbde272f3a8b0fb31d81d0632802356820"
)
NOBLE_RUNTIME_DEB_REPLAY_RECEIPT_DIGEST = (
    "sha256:deb265b9f96e7f1610c20174ef0413f4e30756187feabe1e45bfffbd6fa4a6ee"
)
CODEX_OS_NETWORK_DENIED_LAUNCH_RECEIPT_DIGEST = (
    "sha256:e17e637c59c8121b3bdd254cd8f081e000068281cd345a5943705bcd2e8270e6"
)
LOCAL_CAS_BYTE_LOCATORS_DIGEST = (
    "sha256:9f068c5b6c3d03eae562a5da5a872abd3370ea1fc1cee46b26c330ce49e60e66"
)
_LOCAL_CAS_BYTE_LOCATORS_FILE_DIGEST = (
    "sha256:9f068c5b6c3d03eae562a5da5a872abd3370ea1fc1cee46b26c330ce49e60e66"
)
SPDX_LICENSE_CATALOG_DIGEST = (
    "sha256:5865e5d860a9278d30d22eb5522952f85eb620b2a6a3e68e02a5df7449835a31"
)
NOBLE_RUNTIME_DEB_LOCAL_LOCATORS_DIGEST = (
    "sha256:18e98c27e115ecf4c336c4847c87540b0a6fc103b5791d20a88d846f01e4aac6"
)
_NOBLE_RUNTIME_DEB_LOCAL_LOCATORS_FILE_DIGEST = (
    "sha256:37c7b305ffbd7ca672d021fab986e40ed8cbe45e01dc99566a29640da21275e7"
)
RUNTIME_OS_NETWORK_DENIAL_RECEIPT_DIGEST = (
    "sha256:37e2652151c9791ec2d51804997482af2e6f8f836a86f7cac3743594d011c967"
)

_ARCHIVE_DIGEST = "sha256:0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36"
_EXECUTABLE_DIGEST = "sha256:cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40"
_BUNDLE_DIGEST = "sha256:8ea31ab792fe0cfc7ba55c9dfc1836edf166dabf2d564ed7391eed6c7d422b3d"
_VERIFIER_DIGEST = "sha256:13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62"
_CHECKSUMS_DIGEST = "sha256:5020625e52f7041b9e4a21ee7ef4e2d085d767e72f86e2458443b012b0200362"
_ROOT_DIGEST = "sha256:73747011d0857ada15479a16c4cae0f3ed03aac698b523b97e1de314ac9d9ca8"
_TIMESTAMP_DIGEST = "sha256:367992e4f09fbdb98f05cbf4433a3e6d3830d34c230eebd955fb20ccb5c0a956"
_SNAPSHOT_DIGEST = "sha256:8f784ab614ec62bfdd5f568eb2a2e3011668449ba235ed4eb7befa99f8469933"
_TARGETS_DIGEST = "sha256:6a697f7f8908c8ab26c11786ecb490b54acec97fa8c802e399f065f8a0cc1acd"
_TRUSTED_ROOT_DIGEST = "sha256:6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66"

_LEGACY_TRUST_MATERIAL: dict[str, CanonicalJSONInput] = {
    "ctfeKeyDigest": "sha256:270488a309d22e804eeb245493e87c667658d749006b9fee9cc614572d4fbbdc",
    "fulcioIntermediateDigest": "sha256:f8cbecf186db7714624a5f4e99da31a917cbef70a94dd6921f5c3ca969dfe30a",
    "fulcioRootDigest": "sha256:f989aa23def87c549404eadba767768d2a3c8d6d30a8b793f9f518a8eafd2cf5",
    "rekorKeyDigest": "sha256:dce5ef715502ec9f3cdfd11f8cc384b31a6141023d3e7595e9908a81cb6241bd",
}

_TRUSTED_ROOT: dict[str, CanonicalJSONInput] = {
    "legacyTrustMaterial": _LEGACY_TRUST_MATERIAL,
    "rootBytes": 5630,
    "rootDigest": _ROOT_DIGEST,
    "rootExpires": "2026-11-20T13:58:18Z",
    "rootVersion": 15,
    "snapshotBytes": 1760,
    "snapshotDigest": _SNAPSHOT_DIGEST,
    "snapshotExpires": "2036-05-15T08:09:16Z",
    "snapshotVersion": 165,
    "targetsBytes": 4942,
    "targetsDigest": _TARGETS_DIGEST,
    "targetsExpires": "2036-05-09T09:00:52Z",
    "targetsVersion": 14,
    "timestampBytes": 449,
    "timestampDigest": _TIMESTAMP_DIGEST,
    "timestampExpires": "2026-08-23T01:53:11Z",
    "timestampVersion": 757,
    "trustedRootBytes": 6787,
    "trustedRootDigest": _TRUSTED_ROOT_DIGEST,
}

_APPROVAL: dict[str, CanonicalJSONInput] = {
    "approvalAuthority": "text-to-cad/SAR-004-reviewed-spec",
    "approvalVersion": 2,
    "approvedBytes": {
        "archive": {"bytes": 98970270, "digest": _ARCHIVE_DIGEST},
        "executable": {"bytes": 258278208, "digest": _EXECUTABLE_DIGEST},
        "legacyTrustMaterial": _LEGACY_TRUST_MATERIAL,
        "signatureBundle": {"bytes": 8585, "digest": _BUNDLE_DIGEST},
        "trustedRoot": {
            key: value
            for key, value in _TRUSTED_ROOT.items()
            if key != "legacyTrustMaterial"
            and not key.endswith("Expires")
            and not key.endswith("Version")
        },
        "verifier": {
            "binaryBytes": 108805570,
            "binaryDigest": _VERIFIER_DIGEST,
            "checksumsBytes": 3906,
            "checksumsDigest": _CHECKSUMS_DIGEST,
        },
    },
    "deliveryChannel": "text-to-cad-reviewed-release-input",
    "sameOriginHashAuthenticationAllowed": False,
    "schema": "text-to-cad.sigstore-trust-anchor-approval/2",
    "scope": "codex-0.147.0-x86_64-unknown-linux-musl",
    "signaturePolicyDigest": CODEX_POLICY_DIGEST,
}

_POLICY: dict[str, CanonicalJSONInput] = {
    "archive": {
        "assetId": 504450426,
        "bytes": 98970270,
        "digest": _ARCHIVE_DIGEST,
        "linksAllowed": False,
        "memberBytes": 258278208,
        "memberCount": 1,
        "memberDigest": _EXECUTABLE_DIGEST,
        "memberName": "codex-x86_64-unknown-linux-musl",
        "memberType": "regular-file",
        "name": "codex-x86_64-unknown-linux-musl.tar.gz",
        "pathTraversalAllowed": False,
        "signedDirectly": False,
    },
    "certificate": {
        "chainIssuer": "O=sigstore.dev,CN=sigstore-intermediate",
        "fingerprintDigest": "sha256:0cd70c48dbbb777f1910538d62604b16be271028b8195325bb8eae58fcf255c8",
        "notAfter": "2026-08-07T01:12:23Z",
        "notBefore": "2026-08-07T01:02:23Z",
        "oidcIssuer": "https://token.actions.githubusercontent.com",
        "sanUri": "https://github.com/openai/codex/.github/workflows/rust-release.yml@refs/tags/rust-v0.147.0",
    },
    "codexVersion": "0.147.0",
    "executable": {
        "bytes": 258278208,
        "digest": _EXECUTABLE_DIGEST,
        "name": "codex-x86_64-unknown-linux-musl",
        "platform": "x86_64-unknown-linux-musl",
    },
    "githubWorkflow": {
        "linuxSigningActionDigest": "sha256:4e5fa040cf838f087ce4a0c585f651e90111b4a02973458b926d6938a24108e5",
        "name": "rust-release",
        "ref": "refs/tags/rust-v0.147.0",
        "repository": "openai/codex",
        "sha": "be6e8eac029b183056b7e4402879f15d2c85f61b",
        "trigger": "push",
        "wildcardsAllowed": False,
        "workflowDigest": "sha256:62367daacaabcc8972b6f0a60d2f964bd957e7ec68cab5d62756fd494041d183",
    },
    "schema": "text-to-cad.agent-runtime-codex-signature-policy/1",
    "signatureBundle": {
        "assetId": 504450400,
        "bytes": 8585,
        "digest": _BUNDLE_DIGEST,
        "name": "codex-x86_64-unknown-linux-musl.sigstore",
        "payloadDigest": _EXECUTABLE_DIGEST,
    },
    "transparencyLog": {
        "integratedTime": "2026-08-07T01:02:25Z",
        "logId": "c0d23d6ad406973f9559f3ba2d1ca01f84147d8ffc5b8445c224f98b9591801d",
        "logIndex": 2363083279,
    },
    "trustedRoot": _TRUSTED_ROOT,
    "verifier": {
        "assetId": 196693093,
        "binaryBytes": 108805570,
        "binaryDigest": _VERIFIER_DIGEST,
        "checksumsBytes": 3906,
        "checksumsDigest": _CHECKSUMS_DIGEST,
        "commitSha": "9a4cfe1aae777984c07ce373d97a65428bbff734",
        "name": "cosign",
        "platform": "darwin/arm64",
        "releaseId": 178267850,
        "sourcePackaging": "raw-executable-no-archive",
        "tagObjectSha": "531befdf6581582e22eda7cda084565bb106efa6",
        "version": "2.4.1",
    },
}

_NORMATIVE: dict[str, tuple[dict[str, CanonicalJSONInput], str]] = {
    "sigstore-trust-anchor-approval": (_APPROVAL, CODEX_APPROVAL_DIGEST),
    "codex-signature-policy": (_POLICY, CODEX_POLICY_DIGEST),
}

_PYTHON_WHEEL_LOCK: dict[str, CanonicalJSONInput] = {
    "builderArtifacts": [
        {"bytes": 1816632, "digest": "sha256:71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e", "distribution": "pip", "filename": "pip-26.2.1-py3-none-any.whl", "version": "26.2.1"},
        {"bytes": 1006223, "digest": "sha256:a59e362652f08dcd477c78bb6e7bd9d80a7995bc73ce773050228a348ce2e5bb", "distribution": "setuptools", "filename": "setuptools-82.0.1-py3-none-any.whl", "version": "82.0.1"},
        {"bytes": 33320, "digest": "sha256:3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab", "distribution": "wheel", "filename": "wheel-0.48.0-py3-none-any.whl", "version": "0.48.0"},
        {"bytes": 65293, "digest": "sha256:9cc3a9038d970c843ede84c6ebd3d837350162f6bb1843c11bda8a5d6b5fe39a", "distribution": "auditwheel", "filename": "auditwheel-6.8.1-py3-none-any.whl", "version": "6.8.1"},
        {"bytes": 129956, "digest": "sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c", "distribution": "packaging", "filename": "packaging-26.3-py3-none-any.whl", "version": "26.3"},
        {"bytes": 201178, "digest": "sha256:f215ad5f47d3f1373a21496a6c9e0707c622840d0622f23ff7ce08678b020036", "distribution": "pyelftools", "filename": "pyelftools-0.33-py3-none-any.whl", "version": "0.33"},
    ],
    "platform": {"architecture": "x86_64", "pythonAbi": "cp312", "system": "linux"},
    "runtimeArtifacts": [
        {"bytes": 16645538, "digest": "sha256:90f9849678c75fe7afa2d348ac842c168b0a4d3d61919687216dfc547976d853", "distribution": "numpy", "filename": "numpy-2.4.6-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl", "version": "2.4.6"},
        {"bytes": 741043, "digest": "sha256:b5b5afa63c5272345f2858f7676bc8c217dc8a89f4fadf6193fe10a81b5ff2aa", "distribution": "trimesh", "filename": "trimesh-4.12.2-py3-none-any.whl", "version": "4.12.2"},
        {"bytes": 8094744, "digest": "sha256:b86024e52a1b269467a802258c25521e6d742349d760728092e1bc2d135b4d76", "distribution": "Pillow", "filename": "pillow-12.2.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", "version": "12.2.0"},
    ],
    "schema": "text-to-cad.agent-runtime-python-wheel-lock/1",
}

_PROOF_RECEIPT: dict[str, CanonicalJSONInput] = {
    "archive": _POLICY["archive"],
    "archiveNegativeControl": {
        "bundlePayloadDigest": _EXECUTABLE_DIGEST,
        "exitCode": 1,
        "result": "rejected-payload-mismatch",
        "testedPayloadDigest": _ARCHIVE_DIGEST,
    },
    "certificate": _POLICY["certificate"],
    "cryptographicResult": "verified",
    "githubWorkflow": _POLICY["githubWorkflow"],
    "result": "proof-only",
    "schema": "text-to-cad.agent-runtime-codex-signature-verification/1",
    "signatureBundleDigest": _BUNDLE_DIGEST,
    "signaturePolicyDigest": CODEX_POLICY_DIGEST,
    "signedPayloadDigest": _EXECUTABLE_DIGEST,
    "transparencyLog": _POLICY["transparencyLog"],
    "trustBootstrap": {
        "approvalDigest": CODEX_APPROVAL_DIGEST,
        "status": "not-formal-admission",
    },
    "trustedRoot": {
        "legacyTrustMaterial": _LEGACY_TRUST_MATERIAL,
        "rootDigest": _ROOT_DIGEST,
        "snapshotDigest": _SNAPSHOT_DIGEST,
        "targetsDigest": _TARGETS_DIGEST,
        "timestampDigest": _TIMESTAMP_DIGEST,
        "trustedRootDigest": _TRUSTED_ROOT_DIGEST,
    },
    "verifier": {
        "binaryDigest": _VERIFIER_DIGEST,
        "checksumsDigest": _CHECKSUMS_DIGEST,
        "commitSha": "9a4cfe1aae777984c07ce373d97a65428bbff734",
        "name": "cosign",
        "platform": "darwin/arm64",
        "tagObjectSha": "531befdf6581582e22eda7cda084565bb106efa6",
        "version": "2.4.1",
    },
}

_RETRIEVAL_KINDS = (
    "archive", "signatureBundle", "verifierBinary", "verifierChecksums",
    "root", "timestamp", "snapshot", "targets", "trustedRoot",
)
_RETRIEVAL_IDENTITIES = (
    (98970270, _ARCHIVE_DIGEST),
    (8585, _BUNDLE_DIGEST),
    (108805570, _VERIFIER_DIGEST),
    (3906, _CHECKSUMS_DIGEST),
    (5630, _ROOT_DIGEST),
    (449, _TIMESTAMP_DIGEST),
    (1760, _SNAPSHOT_DIGEST),
    (4942, _TARGETS_DIGEST),
    (6787, _TRUSTED_ROOT_DIGEST),
)
_NODE_CANDIDATE: dict[str, CanonicalJSONInput] = {
    "archive": {
        "bytes": 30768936,
        "digest": "sha256:e798599612f4bb71333a3397ab0d095fd62214e115aea45aa858a145fc72d67e",
        "filename": "node-v24.13.0-linux-x64.tar.xz",
    },
    "checksums": {
        "bytes": 2967,
        "digest": "sha256:10002931019dcfc77706da05664dc133bdffba06c3bf3571102a7b37cb58f15c",
        "filename": "SHASUMS256.txt",
        "lineExact": True,
        "signatureBytes": 566,
        "signatureDigest": "sha256:03660ed4781e082da6c4f0696e2ba360754664094421f54705056a700eadea6c",
    },
    "claims": {"formalAdmission": False, "immutableMirrorVisible": False},
    "elf": {
        "executableBytes": 121158216,
        "executableDigest": "sha256:53fb205ae78805130177e24bcb459a69a1518c8d98f8965f31d85aae7ea840fc",
        "interpreter": "/lib64/ld-linux-x86-64.so.2",
        "needed": [
            "ld-linux-x86-64.so.2", "libc.so.6", "libdl.so.2", "libgcc_s.so.1",
            "libm.so.6", "libpthread.so.0", "libstdc++.so.6",
        ],
        "unresolved": [],
    },
    "platform": "linux/x86_64",
    "runtimeProbe": {
        "network": "none",
        "permissionModel": "passed",
        "versionOutput": "v24.13.0",
    },
    "schema": "text-to-cad.agent-runtime-node-admission-candidate/1",
    "signature": {
        "fingerprint": "CC68F5A3106FF448322E48ED27F5E38D5B0A215F",
        "keyBytes": 3275,
        "keyDigest": "sha256:9c51e903b0da945fc21947fd6fce8fb4d72bc20ddf82e8f0aada694e9688447d",
        "result": "verified",
        "tagCommit": "def0bdf8abee441cfcbf793a8dc24a6f3b899573",
        "tagObject": "d3a73a945e6fff8263153d74e6c3d203fb060b6e",
        "taggedReadmeBytes": 41704,
        "taggedReadmeDigest": "sha256:92e14eb9ac89d5a9b849b8f938267b845a651a39457220181fae487b9a64a574",
    },
    "status": "local-candidate",
    "version": "24.13.0",
}
_CODEX_CANDIDATE: dict[str, CanonicalJSONInput] = {
    "archive": {
        "bytes": 98970270,
        "digest": _ARCHIVE_DIGEST,
        "memberCount": 1,
        "memberDigest": _EXECUTABLE_DIGEST,
        "memberName": "codex-x86_64-unknown-linux-musl",
        "memberType": "regular-file",
    },
    "claims": {
        "formalAdmission": False,
        "formalSignatureReceipt": True,
        "immutableMirrorVisible": False,
    },
    "elf": {
        "architecture": "x86_64",
        "interpreter": None,
        "needed": [],
        "type": "static-pie",
        "unresolved": [],
    },
    "platform": "linux/amd64",
    "probes": {
        "localCasObjectsExact": True,
        "network": "none",
        "nodeAbsent": True,
        "noninteractiveParserSmoke": True,
    },
    "proofReceiptDigest": CODEX_PROOF_RECEIPT_DIGEST,
    "schema": "text-to-cad.agent-runtime-codex-admission-candidate/1",
    "signatureBundleDigest": _BUNDLE_DIGEST,
    "signaturePolicyDigest": CODEX_POLICY_DIGEST,
    "status": "local-candidate",
    "version": "0.147.0",
    "versionOutput": "codex-cli 0.147.0",
}
_PYTHON_WHEEL_CANDIDATE: dict[str, CanonicalJSONInput] = {
    "auditwheel": {
        "Pillow": {
            "kind": "native",
            "platformTag": "manylinux_2_17_x86_64",
            "versionedLibraries": [
                "libc.so.6", "libdl.so.2", "libjpeg-02b4d8cf.so.62.4.0",
                "liblzma-d41bb66c.so.5.8.3", "libm.so.6",
                "libpng16-1ff02007.so.16.56.0", "libpthread.so.0",
                "libtiff-56c1bbc6.so.6.2.0", "libz.so.1",
            ],
        },
        "numpy": {
            "kind": "native",
            "platformTag": "manylinux_2_27_x86_64",
            "versionedLibraries": [
                "libc.so.6", "libgcc_s.so.1", "libgfortran-040039e1-0352e75f.so.5.0.0",
                "libm.so.6", "libpthread.so.0", "libquadmath-96973f99-934c22de.so.0.0.0",
                "libstdc++.so.6",
            ],
        },
        "trimesh": {"kind": "pure-python", "platformTag": "any", "versionedLibraries": []},
    },
    "claims": {"formalAdmission": False, "immutableMirrorVisible": False},
    "offlineImport": {
        "Pillow": "12.2.0",
        "network": "none",
        "numpy": "2.4.6",
        "sourceFallback": False,
        "trimesh": "4.12.2",
    },
    "schema": "text-to-cad.agent-runtime-python-wheel-admission-candidate/1",
    "status": "local-candidate",
    "wheelLockDigest": "sha256:375f63e4cd6b89325bfc6cceac7806208902fa41599b969e4fe949d539870f8f",
}
_RFC3339_SECONDS_RE = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z\Z"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MIRROR_VERSION_ID_RE = re.compile(r"[A-Za-z0-9._~+/=-]{1,1024}\Z")
_MIRROR_ETAG_RE = re.compile(r'"[ -!#-~]{1,1022}"\Z')


def _require_keys(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ExternalAdmissionError(f"{label} has unexpected keys")
    return value


def _require_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ExternalAdmissionError(f"{label} is not a canonical digest")


def _validate_builder_candidate(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "apt", "baseImage", "claims", "localImage", "recipe", "schema",
            "snapshot", "status", "toolchain", "wheels",
        },
        "builder input candidate",
    )
    if value["schema"] != "text-to-cad.agent-runtime-builder-input-candidate/1":
        raise ExternalAdmissionError("builder input candidate schema is invalid")
    if canonical_json_digest(value) != BUILDER_INPUT_CANDIDATE_DIGEST:
        raise ExternalAdmissionError("builder input candidate does not equal exact candidate digest")
    if value["status"] != "local-candidate":
        raise ExternalAdmissionError("builder input candidate cannot claim admission")
    claims = _require_keys(
        value["claims"],
        {"debBytesMirrored", "formalAdmission", "immutableMirrorVisible", "networklessRebuild"},
        "builder claims",
    )
    if dict(claims) != {
        "debBytesMirrored": True,
        "formalAdmission": False,
        "immutableMirrorVisible": False,
        "networklessRebuild": True,
    }:
        raise ExternalAdmissionError("builder candidate claims exceed observed evidence")
    base = _require_keys(value["baseImage"], {"digest", "reference"}, "builder base image")
    _require_digest(base["digest"], "builder base image digest")
    if base["digest"] != "sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea":
        raise ExternalAdmissionError("builder base image is not the selected Noble input")
    image = _require_keys(
        value["localImage"],
        {"architecture", "id", "os", "pullDisabledProbe", "runtimeNetworkDisabledProbe", "tag"},
        "builder local image",
    )
    _require_digest(image["id"], "builder local image id")
    if (
        image["architecture"] != "amd64"
        or image["os"] != "linux"
        or image["pullDisabledProbe"] is not True
        or image["runtimeNetworkDisabledProbe"] is not True
    ):
        raise ExternalAdmissionError("builder local image probe is not exact")
    snapshot = _require_keys(
        value["snapshot"], {"components", "inRelease", "timestamp"}, "builder snapshot"
    )
    if snapshot["timestamp"] != "20260815T000000Z" or tuple(snapshot["components"]) != (
        "main", "universe"
    ):
        raise ExternalAdmissionError("builder snapshot selection is not exact")
    expected_suites = ("noble", "noble-updates", "noble-backports", "noble-security")
    if not isinstance(snapshot["inRelease"], (list, tuple)) or len(snapshot["inRelease"]) != 4:
        raise ExternalAdmissionError("builder snapshot InRelease set is incomplete")
    for expected_suite, item in zip(expected_suites, snapshot["inRelease"], strict=True):
        record = _require_keys(item, {"digest", "suite"}, "builder InRelease")
        if record["suite"] != expected_suite:
            raise ExternalAdmissionError("builder InRelease order is not exact")
        _require_digest(record["digest"], "builder InRelease digest")
    apt = _require_keys(
        value["apt"],
        {"inReleaseManifestDigest", "installedPackageCount", "installedPackageManifestDigest", "roots"},
        "builder apt",
    )
    _require_digest(apt["inReleaseManifestDigest"], "builder InRelease manifest digest")
    _require_digest(apt["installedPackageManifestDigest"], "builder package manifest digest")
    if apt["installedPackageCount"] != 169 or tuple(apt["roots"]) != (
        "binutils", "build-essential", "ca-certificates", "g++", "patchelf",
        "python3.12", "python3.12-dev", "python3.12-venv",
    ):
        raise ExternalAdmissionError("builder apt closure summary is not exact")
    recipe = _require_keys(
        value["recipe"], {"dockerfileDigest", "snapshotSourcesDigest", "wheelHashListDigest"},
        "builder recipe",
    )
    for field in recipe:
        _require_digest(recipe[field], f"builder recipe {field}")
    toolchain = _require_keys(
        value["toolchain"],
        {"auditwheelVersion", "binutilsVersion", "cxxVersion", "patchelfVersion", "pythonVersion", "setuptoolsVersion", "wheelVersion"},
        "builder toolchain",
    )
    if dict(toolchain) != {
        "auditwheelVersion": "6.8.1",
        "binutilsVersion": "2.42-4ubuntu2.10",
        "cxxVersion": "13.3.0-6ubuntu2~24.04.1",
        "patchelfVersion": "0.18.0-1.1build1",
        "pythonVersion": "3.12.3",
        "setuptoolsVersion": "82.0.1",
        "wheelVersion": "0.48.0",
    }:
        raise ExternalAdmissionError("builder toolchain versions are not exact")
    if not isinstance(value["wheels"], (list, tuple)) or len(value["wheels"]) != 6:
        raise ExternalAdmissionError("builder wheel closure is incomplete")
    for item in value["wheels"]:
        wheel = _require_keys(item, {"bytes", "digest", "filename", "version"}, "builder wheel")
        if isinstance(wheel["bytes"], bool) or not isinstance(wheel["bytes"], int) or wheel["bytes"] <= 0:
            raise ExternalAdmissionError("builder wheel byte length is invalid")
        _require_digest(wheel["digest"], "builder wheel digest")


def _validate_https_url(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ExternalAdmissionError("retrieval locator is not an HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ExternalAdmissionError("retrieval locator is not an HTTPS URL")


def _validate_retrieval_metadata(value: Mapping[str, Any]) -> None:
    _require_keys(value, {"schema", "observedAt", "objects"}, "retrieval metadata")
    if value["schema"] != "text-to-cad.codex-retrieval-metadata/1":
        raise ExternalAdmissionError("retrieval metadata schema is invalid")
    if not isinstance(value["observedAt"], str) or _RFC3339_SECONDS_RE.fullmatch(value["observedAt"]) is None:
        raise ExternalAdmissionError("retrieval metadata timestamp is invalid")
    objects = value["objects"]
    if not isinstance(objects, (list, tuple)) or len(objects) != len(_RETRIEVAL_KINDS):
        raise ExternalAdmissionError("retrieval object set is incomplete")
    for expected_kind, expected_identity, item in zip(
        _RETRIEVAL_KINDS, _RETRIEVAL_IDENTITIES, objects, strict=True
    ):
        record = _require_keys(
            item,
            {"bytes", "digest", "finalUrl", "kind", "redirects", "requestedUrl", "responseMetadataDigest"},
            "retrieval object",
        )
        if record["kind"] != expected_kind:
            raise ExternalAdmissionError("retrieval object order is not exact")
        if (record["bytes"], record["digest"]) != expected_identity:
            raise ExternalAdmissionError("retrieval object identity is not approved")
        _require_digest(record["responseMetadataDigest"], "retrieval response metadata digest")
        _validate_https_url(record["requestedUrl"])
        _validate_https_url(record["finalUrl"])
        if not isinstance(record["redirects"], (list, tuple)):
            raise ExternalAdmissionError("retrieval redirects must be an array")
        for redirect in record["redirects"]:
            _validate_https_url(redirect)


def _validate_noble_deb_closure(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "claims", "inRelease", "packageIndices", "packages", "schema", "snapshot",
            "status", "ubuntuArchiveKeyring",
        },
        "Noble deb closure",
    )
    if value["schema"] != "text-to-cad.agent-runtime-noble-deb-closure-candidate/1":
        raise ExternalAdmissionError("Noble deb closure schema is invalid")
    if canonical_json_digest(value) != NOBLE_DEB_CLOSURE_DIGEST:
        raise ExternalAdmissionError("Noble deb closure does not equal reviewed closure digest")
    if value["snapshot"] != "20260815T000000Z" or value["status"] != "local-candidate":
        raise ExternalAdmissionError("Noble deb closure identity is invalid")
    keyring = _require_keys(
        value["ubuntuArchiveKeyring"],
        {"bytes", "digest", "signingKeyFingerprint"},
        "Ubuntu archive keyring",
    )
    if dict(keyring) != {
        "bytes": 3607,
        "digest": "sha256:80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31",
        "signingKeyFingerprint": "F6ECB3762474EDA9D21B7022871920D1991BC93C",
    }:
        raise ExternalAdmissionError("Ubuntu archive keyring identity is invalid")
    if value["claims"] != {
        "debBytesMirroredLocal": True,
        "formalAdmission": False,
        "immutableMirrorVisible": False,
        "inReleaseAuthenticated": True,
        "networklessRebuild": True,
        "packageIndexHashesMatched": True,
    }:
        raise ExternalAdmissionError("Noble deb closure claims are invalid")
    in_release = value["inRelease"]
    indices = value["packageIndices"]
    packages = value["packages"]
    if not isinstance(in_release, (list, tuple)) or len(in_release) != 4:
        raise ExternalAdmissionError("Noble InRelease closure is incomplete")
    if not isinstance(indices, (list, tuple)) or len(indices) != 8:
        raise ExternalAdmissionError("Noble Packages index closure is incomplete")
    if not isinstance(packages, (list, tuple)) or len(packages) != 78:
        raise ExternalAdmissionError("Noble package closure is incomplete")
    for record in in_release:
        item = _require_keys(
            record, {"bytes", "digest", "signatureVerified", "suite"}, "Noble InRelease"
        )
        if item["signatureVerified"] is not True:
            raise ExternalAdmissionError("Noble InRelease signature is not verified")
        _require_digest(item["digest"], "Noble InRelease digest")
    for record in indices:
        item = _require_keys(
            record,
            {"bytes", "component", "digest", "path", "suite", "uncompressedDigest"},
            "Noble Packages index",
        )
        _require_digest(item["digest"], "Noble Packages index digest")
        _require_digest(item["uncompressedDigest"], "Noble Packages uncompressed digest")
    package_names: list[str] = []
    for record in packages:
        item = _require_keys(
            record,
            {"architecture", "bytes", "digest", "indexAuthorities", "localFilename", "package", "poolPath", "version"},
            "Noble package",
        )
        _require_digest(item["digest"], "Noble package digest")
        if not isinstance(item["indexAuthorities"], (list, tuple)) or not item["indexAuthorities"]:
            raise ExternalAdmissionError("Noble package has no signed index authority")
        if not isinstance(item["package"], str):
            raise ExternalAdmissionError("Noble package name is invalid")
        package_names.append(item["package"])
    if package_names != sorted(package_names):
        raise ExternalAdmissionError("Noble package closure order is not canonical")


def _validate_builder_reproducibility_diagnostic(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "baseLayerEqual", "fileInventory", "firstImage", "postBaseLayersEqual",
            "reproducible", "schema", "secondImage", "semanticConfigEqual", "status",
        },
        "builder reproducibility diagnostic",
    )
    if value["schema"] != "text-to-cad.agent-runtime-builder-reproducibility-diagnostic/1":
        raise ExternalAdmissionError("builder reproducibility diagnostic schema is invalid")
    if canonical_json_digest(value) != BUILDER_REPRODUCIBILITY_DIAGNOSTIC_DIGEST:
        raise ExternalAdmissionError("builder result does not equal reviewed diagnostic digest")
    if (
        value["status"] != "diagnostic-only"
        or value["reproducible"] is not False
        or value["semanticConfigEqual"] is not True
        or value["baseLayerEqual"] is not True
        or value["postBaseLayersEqual"] is not False
    ):
        raise ExternalAdmissionError("builder reproducibility result is overstated")
    inventory = _require_keys(
        value["fileInventory"],
        {"changed", "files", "pycChanged", "runtimeMountedChanged", "transientStateChanged"},
        "builder file inventory",
    )
    if dict(inventory) != {
        "changed": 846,
        "files": 11441,
        "pycChanged": 842,
        "runtimeMountedChanged": 1,
        "transientStateChanged": 3,
    }:
        raise ExternalAdmissionError("builder file inventory is not exact")
    for label in ("firstImage", "secondImage"):
        image = _require_keys(
            value[label], {"created", "id", "layers", "size"}, f"builder {label}"
        )
        _require_digest(image["id"], f"builder {label} id")
        if not isinstance(image["layers"], (list, tuple)) or len(image["layers"]) != 8:
            raise ExternalAdmissionError(f"builder {label} layer closure is incomplete")
        for layer in image["layers"]:
            _require_digest(layer, f"builder {label} layer")


def _validate(kind: str, value: Mapping[str, Any]) -> None:
    if kind in _NORMATIVE:
        expected, expected_digest = _NORMATIVE[kind]
        expected_frozen = _freeze_mapping(expected)
        if set(value) != set(expected_frozen):
            raise ExternalAdmissionError(f"{kind} has unexpected keys")
        if value != expected_frozen:
            label = "normative policy" if kind == "codex-signature-policy" else "normative approval"
            raise ExternalAdmissionError(f"{label} does not equal reviewed bytes")
        if canonical_json_digest(value) != expected_digest:
            raise ExternalAdmissionError(f"{kind} digest does not equal reviewed digest")
        return
    if kind == "builder-input-candidate":
        _validate_builder_candidate(value)
        return
    if kind == "noble-deb-closure-candidate":
        _validate_noble_deb_closure(value)
        return
    if kind == "noble-runtime-deb-closure-candidate":
        if canonical_json_digest(value) != NOBLE_RUNTIME_DEB_CLOSURE_DIGEST:
            raise ExternalAdmissionError("Noble runtime closure does not equal exact digest")
        _require_keys(
            value,
            {
                "builderInput", "claims", "inRelease", "packageIndices", "packages",
                "runtimeRoots", "schema", "snapshot", "status", "ubuntuArchiveKeyring",
            },
            "Noble runtime deb closure",
        )
        if value["schema"] != "text-to-cad.agent-runtime-noble-runtime-deb-closure-candidate/1":
            raise ExternalAdmissionError("Noble runtime closure schema is invalid")
        if tuple(value["runtimeRoots"]) != (
            "bash", "coreutils", "file", "findutils", "git", "git-lfs", "locales",
            "procps", "ripgrep", "sed",
        ) or len(value["packages"]) != 47:
            raise ExternalAdmissionError("Noble runtime closure roots or package count is invalid")
        return
    if kind == "builder-reproducibility-diagnostic":
        _validate_builder_reproducibility_diagnostic(value)
        return
    if kind == "builder-network-denial-launch":
        if canonical_json_digest(value) != BUILDER_NETWORK_DENIAL_RECEIPT_DIGEST:
            raise ExternalAdmissionError("builder network denial receipt is not exact")
        if value.get("schema") != "text-to-cad.agent-runtime-builder-network-denial-launch/1":
            raise ExternalAdmissionError("builder network denial receipt schema is invalid")
        return
    if kind == "noble-deb-closure-replay":
        if canonical_json_digest(value) != NOBLE_DEB_REPLAY_RECEIPT_DIGEST:
            raise ExternalAdmissionError("Noble replay receipt is not exact")
        if value.get("schema") != "text-to-cad.agent-runtime-noble-deb-closure-replay/1":
            raise ExternalAdmissionError("Noble replay receipt schema is invalid")
        return
    if kind == "codex-os-network-denied-verification-launch":
        if canonical_json_digest(value) != CODEX_OS_NETWORK_DENIED_LAUNCH_RECEIPT_DIGEST:
            raise ExternalAdmissionError("Codex OS network denial launch receipt is not exact")
        if value.get("schema") != "text-to-cad.agent-runtime-codex-os-network-denied-verification-launch/1":
            raise ExternalAdmissionError("Codex OS network denial launch schema is invalid")
        return
    if kind == "noble-runtime-deb-closure-replay":
        if canonical_json_digest(value) != NOBLE_RUNTIME_DEB_REPLAY_RECEIPT_DIGEST:
            raise ExternalAdmissionError("Noble runtime replay receipt is not exact")
        if value.get("schema") != "text-to-cad.agent-runtime-noble-runtime-deb-closure-replay/1":
            raise ExternalAdmissionError("Noble runtime replay receipt schema is invalid")
        return
    if kind == "local-cas-byte-locators":
        if canonical_json_digest(value) != LOCAL_CAS_BYTE_LOCATORS_DIGEST:
            raise ExternalAdmissionError("local CAS locator manifest is not exact")
        _require_keys(
            value, {"artifacts", "formalAdmission", "immutableMirrorVisible", "schema"},
            "local CAS locator manifest",
        )
        if value["schema"] != "text-to-cad.agent-runtime-local-cas-byte-locators/1":
            raise ExternalAdmissionError("local CAS locator schema is invalid")
        artifacts = cast(tuple[Any, ...], value["artifacts"])
        expected_ids = (
            "builder.docker-archive", "python.numpy", "python.trimesh", "python.pillow",
            "node.archive", "node.checksums", "node.checksums-signature",
            "node.release-key", "codex.archive", "codex.executable",
            "codex.signature-bundle", "codex.verifier", "codex.verifier-checksums",
            "codex.tuf-root", "codex.tuf-timestamp", "codex.tuf-snapshot",
            "codex.tuf-targets", "codex.trusted-root", "spdx.license-list-wheel",
            "spdx.license-catalog",
        )
        if (
            len(artifacts) != 20
            or value["formalAdmission"] is not False
            or value["immutableMirrorVisible"] is not False
            or tuple(cast(Mapping[str, Any], item).get("artifactId") for item in artifacts)
            != expected_ids
        ):
            raise ExternalAdmissionError("local CAS locator claims are invalid")
        for artifact_value in artifacts:
            artifact = cast(Mapping[str, Any], artifact_value)
            required = {"artifactId", "bytes", "digest", "kind", "locator", "mode"}
            if not set(artifact).issuperset(required):
                raise ExternalAdmissionError("local CAS artifact record is incomplete")
            artifact_id = artifact["artifactId"]
            if (
                type(artifact_id) is not str
                or re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,63}", artifact_id) is None
                or type(artifact["bytes"]) is not int
                or artifact["bytes"] < 0
                or type(artifact["kind"]) is not str
                or not artifact["kind"].isascii()
                or artifact["mode"] != "0444"
            ):
                raise ExternalAdmissionError("local CAS artifact scalar is invalid")
            _require_digest(artifact["digest"], "local CAS artifact digest")
            if artifact["locator"] != (
                "/private/tmp/sai004-external-mirror/sha256/"
                + cast(str, artifact["digest"]).removeprefix("sha256:")
            ):
                raise ExternalAdmissionError("local CAS artifact locator is not exact")
        return
    if kind == "spdx-license-catalog":
        if canonical_json_digest(value) != SPDX_LICENSE_CATALOG_DIGEST:
            raise ExternalAdmissionError("SPDX license catalog is not exact")
        _require_keys(
            value,
            {"exceptions", "licenseListVersion", "licenses", "schema"},
            "SPDX license catalog",
        )
        licenses = value["licenses"]
        exceptions = value["exceptions"]
        if (
            value["schema"] != "text-to-cad.spdx-license-catalog/1"
            or value["licenseListVersion"] != "3.28.0"
            or not isinstance(licenses, (list, tuple))
            or not isinstance(exceptions, (list, tuple))
            or len(licenses) != 727
            or len(exceptions) != 84
        ):
            raise ExternalAdmissionError("SPDX license catalog shape is invalid")
        for label, identifiers in (("license", licenses), ("exception", exceptions)):
            if tuple(sorted(identifiers)) != tuple(identifiers) or len(set(identifiers)) != len(identifiers):
                raise ExternalAdmissionError(f"SPDX {label} identifiers are not ordered unique")
            if any(type(item) is not str or not item or not item.isascii() for item in identifiers):
                raise ExternalAdmissionError(f"SPDX {label} identifier is invalid")
        return
    if kind == "noble-runtime-deb-local-locators":
        if canonical_json_digest(value) != NOBLE_RUNTIME_DEB_LOCAL_LOCATORS_DIGEST:
            raise ExternalAdmissionError("Noble runtime local locators are not exact")
        _require_keys(
            value,
            {"casRoot", "closureDigest", "formalAdmission", "immutableMirrorVisible", "objects", "schema"},
            "Noble runtime local locators",
        )
        if (
            value["schema"] != "text-to-cad.agent-runtime-noble-runtime-deb-local-locators/1"
            or len(value["objects"]) != 47
            or value["formalAdmission"] is not False
        ):
            raise ExternalAdmissionError("Noble runtime local locator claims are invalid")
        return
    if kind == "runtime-os-network-denial-launch":
        if canonical_json_digest(value) != RUNTIME_OS_NETWORK_DENIAL_RECEIPT_DIGEST:
            raise ExternalAdmissionError("runtime OS network denial receipt is not exact")
        _require_keys(
            value,
            {
                "buildInvocation", "buildKit", "builderImageId", "debCount",
                "dockerfileDigest", "exitCode", "formalAdmission",
                "immutableMirrorVisible", "networkMode", "observedAt", "platform",
                "pull", "result", "rootFsDiffIds", "runtimeClosureDigest",
                "runtimeImageBytes", "runtimeImageId", "schema", "toolVersions",
            },
            "runtime OS network denial receipt",
        )
        if (
            value["schema"]
            != "text-to-cad.agent-runtime-runtime-os-network-denial-launch/1"
            or value["networkMode"] != "none"
            or value["result"] != "network-disabled-build-succeeded"
            or value["platform"] != "linux/amd64"
            or value["debCount"] != 47
            or value["exitCode"] != 0
            or value["buildKit"] is not False
            or value["pull"] is not False
            or value["formalAdmission"] is not False
            or value["immutableMirrorVisible"] is not False
        ):
            raise ExternalAdmissionError("runtime OS network denial claims are invalid")
        for field in ("builderImageId", "dockerfileDigest", "runtimeClosureDigest", "runtimeImageId"):
            _require_digest(value[field], f"runtime OS {field}")
        if len(value["rootFsDiffIds"]) != 10:
            raise ExternalAdmissionError("runtime OS root filesystem closure is incomplete")
        for layer in value["rootFsDiffIds"]:
            _require_digest(layer, "runtime OS root filesystem layer")
        _require_keys(
            value["toolVersions"],
            {"bash", "coreutils", "file", "findutils", "git", "git-lfs",
             "locales", "procps", "ripgrep", "sed"},
            "runtime OS tool versions",
        )
        return
    if kind == "python-wheel-lock":
        expected = _freeze_mapping(_PYTHON_WHEEL_LOCK)
        if set(value) != set(expected):
            raise ExternalAdmissionError("python wheel lock has unexpected keys")
        for group in ("runtimeArtifacts", "builderArtifacts"):
            for artifact in value[group]:
                _require_keys(
                    artifact,
                    {"bytes", "digest", "distribution", "filename", "version"},
                    "python wheel artifact",
                )
        if value != expected:
            raise ExternalAdmissionError("python wheel lock does not equal selected bytes")
        return
    if kind == "codex-signature-verification":
        proof = _freeze_mapping(_PROOF_RECEIPT)
        formal = dict(_PROOF_RECEIPT)
        formal["result"] = "verified"
        formal["trustBootstrap"] = {
            "approvalDigest": CODEX_APPROVAL_DIGEST,
            "status": "verified",
        }
        formal_frozen = _freeze_mapping(formal)
        if set(value) != set(proof):
            raise ExternalAdmissionError("Codex signature receipt has unexpected keys")
        if value not in (proof, formal_frozen):
            raise ExternalAdmissionError("Codex signature receipt is not an exact reviewed variant")
        if value == proof and canonical_json_digest(value) != CODEX_PROOF_RECEIPT_DIGEST:
            raise ExternalAdmissionError("Codex proof receipt digest is not reviewed")
        return
    if kind == "codex-retrieval-metadata":
        _validate_retrieval_metadata(value)
        return
    if kind == "node-admission-candidate":
        expected = _freeze_mapping(_NODE_CANDIDATE)
        if set(value) != set(expected):
            raise ExternalAdmissionError("Node admission candidate has unexpected keys")
        if value != expected:
            raise ExternalAdmissionError("Node admission candidate does not equal observed bytes")
        return
    if kind == "codex-admission-candidate":
        expected = _freeze_mapping(_CODEX_CANDIDATE)
        if set(value) != set(expected):
            raise ExternalAdmissionError("Codex admission candidate has unexpected keys")
        if value != expected:
            raise ExternalAdmissionError("Codex admission candidate does not equal observed bytes")
        return
    if kind == "python-wheel-admission-candidate":
        expected = _freeze_mapping(_PYTHON_WHEEL_CANDIDATE)
        if set(value) != set(expected):
            raise ExternalAdmissionError("Python wheel candidate has unexpected keys")
        if value != expected:
            raise ExternalAdmissionError("Python wheel candidate does not equal observed evidence")
        return
    raise ExternalAdmissionError("unknown external admission kind")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, CanonicalJSONValue]:
    frozen = parse_canonical_json(canonical_json_bytes(dict(value)))
    if not isinstance(frozen, Mapping):
        raise ExternalAdmissionError("external document must be an object")
    return cast(Mapping[str, CanonicalJSONValue], frozen)


def parse_external_strict(kind: str, payload: bytes) -> ExternalAdmissionDocument:
    """Parse a selected closed SAI-004 document through the shared JSON seam."""

    try:
        value = parse_canonical_json(payload)
    except ValueError as exc:
        raise ExternalAdmissionError(str(exc)) from exc
    if not isinstance(value, Mapping):
        raise ExternalAdmissionError("external document must be an object")
    _validate(kind, value)
    return ExternalAdmissionDocument(kind=kind, value=cast(Mapping[str, CanonicalJSONValue], value))


def canonical_external_bytes(document: ExternalAdmissionDocument) -> bytes:
    """Validate then canonicalize one typed external admission document."""

    if not isinstance(document, ExternalAdmissionDocument):
        raise ExternalAdmissionError("external document must be typed")
    _validate(document.kind, document.value)
    encoded = canonical_json_bytes(document.value)
    return encoded


def external_digest(document: ExternalAdmissionDocument) -> str:
    """Digest a validated typed external admission document."""

    canonical_external_bytes(document)
    return canonical_json_digest(document.value)


def load_codex_normative_inputs() -> tuple[ExternalAdmissionDocument, ExternalAdmissionDocument]:
    """Return the exact reviewed Codex approval and signature policy."""

    approval = parse_external_strict(
        "sigstore-trust-anchor-approval", canonical_json_bytes(_APPROVAL)
    )
    policy = parse_external_strict(
        "codex-signature-policy", canonical_json_bytes(_POLICY)
    )
    return approval, policy


def load_codex_signature_proof() -> ExternalAdmissionDocument:
    """Return the exact reviewed proof-only receipt, never a formal receipt."""

    return parse_external_strict(
        "codex-signature-verification", canonical_json_bytes(_PROOF_RECEIPT)
    )


def _formal_codex_signature_receipt() -> ExternalAdmissionDocument:
    """Return the exact reviewed two-leaf promotion of the proof receipt."""

    formal = dict(_PROOF_RECEIPT)
    formal["result"] = "verified"
    formal["trustBootstrap"] = {
        "approvalDigest": CODEX_APPROVAL_DIGEST,
        "status": "verified",
    }
    return parse_external_strict(
        "codex-signature-verification", canonical_json_bytes(formal)
    )


def build_codex_offline_plan(
    *,
    verifier: Path,
    bundle: Path,
    executable: Path,
    archive: Path,
    ca_root: Path,
    ca_intermediate: Path,
    rekor_key: Path,
    ct_key: Path,
) -> CodexOfflineVerificationPlan:
    """Build the fixed legacy-bundle replay without an ambient TUF cache."""

    identity = (
        "https://github.com/openai/codex/.github/workflows/"
        "rust-release.yml@refs/tags/rust-v0.147.0"
    )
    args = (
        str(verifier), "verify-blob", "--offline", "--bundle", str(bundle),
        "--ca-roots", str(ca_root), "--ca-intermediates", str(ca_intermediate),
        "--certificate-identity", identity,
        "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com",
        "--certificate-github-workflow-name", "rust-release",
        "--certificate-github-workflow-ref", "refs/tags/rust-v0.147.0",
        "--certificate-github-workflow-repository", "openai/codex",
        "--certificate-github-workflow-sha", "be6e8eac029b183056b7e4402879f15d2c85f61b",
        "--certificate-github-workflow-trigger", "push", str(executable),
    )
    environment = {
        "ALL_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
        "SIGSTORE_CT_LOG_PUBLIC_KEY_FILE": str(ct_key),
        "SIGSTORE_NO_CACHE": "1",
        "SIGSTORE_REKOR_PUBLIC_KEY": str(rekor_key),
    }
    wrong_identity = identity.replace("rust-v0.147.0", "rust-v0.147.1")
    wrong_identity_args = tuple(wrong_identity if item == identity else item for item in args)
    wrong_rekor_environment = dict(environment)
    wrong_rekor_environment["SIGSTORE_REKOR_PUBLIC_KEY"] = str(ct_key)
    return CodexOfflineVerificationPlan(
        positive_args=args,
        positive_environment=_freeze_string_mapping(environment),
        wrong_artifact_args=args[:-1] + (str(archive),),
        wrong_identity_args=wrong_identity_args,
        wrong_rekor_environment=_freeze_string_mapping(wrong_rekor_environment),
    )


def _pem_bytes(label: str, payload: bytes) -> bytes:
    encoded = base64.b64encode(payload)
    lines = [encoded[index:index + 64] for index in range(0, len(encoded), 64)]
    pem = (
        f"-----BEGIN {label}-----\n".encode("ascii")
        + b"\n".join(lines)
        + f"\n-----END {label}-----".encode("ascii")
    )
    return pem if label == "CERTIFICATE" else pem + b"\n"


def _decode_approved(raw: Any, expected_digest: str, label: str) -> bytes:
    if not isinstance(raw, str) or not raw.isascii():
        raise ExternalAdmissionError(f"approved {label} is not base64 text")
    try:
        payload = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise ExternalAdmissionError(f"approved {label} is not strict base64") from exc
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if digest != expected_digest:
        raise ExternalAdmissionError(f"approved {label} digest mismatch")
    return payload


def _write_read_only(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o444)
    except OSError as exc:
        raise ExternalAdmissionError("trust output must be new and private") from exc


def extract_codex_trust_material(
    trusted_root: Path, output_directory: Path
) -> CodexTrustMaterial:
    """Extract only fixed Fulcio, Rekor, and CT keys from approved root bytes."""

    digest, size = _file_identity(trusted_root)
    if size != 6787:
        raise ExternalAdmissionError("approved trusted root byte length mismatch")
    if digest != _TRUSTED_ROOT_DIGEST:
        raise ExternalAdmissionError("approved trusted root digest mismatch")
    try:
        root = json.loads(_read_regular_bytes(trusted_root, "approved trusted root"))
        intermediate_raw = root["certificateAuthorities"][1]["certChain"]["certificates"][0]["rawBytes"]
        ca_root_raw = root["certificateAuthorities"][1]["certChain"]["certificates"][1]["rawBytes"]
        rekor_raw = root["tlogs"][0]["publicKey"]["rawBytes"]
        ct_raw = root["ctlogs"][1]["publicKey"]["rawBytes"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ExternalAdmissionError("approved trusted root structure is invalid") from exc
    intermediate = _decode_approved(
        intermediate_raw,
        "sha256:15d795348226b4649f750f5802592c393bee7cc53c3b86982175b7ad087efe47",
        "Fulcio intermediate",
    )
    ca_root_der = _decode_approved(
        ca_root_raw,
        "sha256:3ba7b6cc4e95469d4d334b49cb257ad8537076fa84b0ca87ff4ecfe6a54680c1",
        "Fulcio root",
    )
    rekor = _decode_approved(
        rekor_raw,
        "sha256:" + cast(Mapping[str, Any], _POLICY["transparencyLog"])["logId"],
        "Rekor key",
    )
    ct_key = _decode_approved(
        ct_raw,
        "sha256:dd3d306ac6c7113263191e1c99673702a24a5eb8de3cadff878a72802f29ee8e",
        "CT key",
    )
    try:
        output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError as exc:
        raise ExternalAdmissionError("trust output directory must be new") from exc
    material = CodexTrustMaterial(
        ca_root=output_directory / "fulcio-root.pem",
        ca_intermediate=output_directory / "fulcio-intermediate.pem",
        rekor_key=output_directory / "rekor.pem",
        ct_key=output_directory / "ctfe.pem",
    )
    _write_read_only(material.ca_root, _pem_bytes("CERTIFICATE", ca_root_der))
    _write_read_only(material.ca_intermediate, _pem_bytes("CERTIFICATE", intermediate))
    _write_read_only(material.rekor_key, _pem_bytes("PUBLIC KEY", rekor))
    _write_read_only(material.ct_key, _pem_bytes("PUBLIC KEY", ct_key))
    expected_pem_digests = {
        material.ca_root: cast(str, _LEGACY_TRUST_MATERIAL["fulcioRootDigest"]),
        material.ca_intermediate: cast(
            str, _LEGACY_TRUST_MATERIAL["fulcioIntermediateDigest"]
        ),
        material.rekor_key: "sha256:dce5ef715502ec9f3cdfd11f8cc384b31a6141023d3e7595e9908a81cb6241bd",
        material.ct_key: "sha256:270488a309d22e804eeb245493e87c667658d749006b9fee9cc614572d4fbbdc",
    }
    for path, expected_digest in expected_pem_digests.items():
        actual_digest, _ = _file_identity(path)
        if actual_digest != expected_digest:
            raise ExternalAdmissionError("derived trust PEM digest mismatch")
    return material


def _freeze_string_mapping(value: Mapping[str, str]) -> Mapping[str, str]:
    from types import MappingProxyType

    return MappingProxyType(dict(value))


def replay_codex_offline_plan(
    plan: CodexOfflineVerificationPlan,
    runner: Any,
) -> ExternalAdmissionDocument:
    """Replay exact offline controls and return only the reviewed proof receipt."""

    positive_code, positive_output = runner(plan.positive_args, plan.positive_environment)
    if positive_code != 0 or "Verified OK" not in positive_output:
        raise ExternalAdmissionError("Codex deny-proxy offline verification failed")
    artifact_code, artifact_output = runner(
        plan.wrong_artifact_args, plan.positive_environment
    )
    if artifact_code != 1 or "payload" not in artifact_output.lower():
        raise ExternalAdmissionError("Codex wrong artifact control did not reject")
    identity_code, identity_output = runner(
        plan.wrong_identity_args, plan.positive_environment
    )
    if identity_code != 1 or "identit" not in identity_output.lower():
        raise ExternalAdmissionError("Codex wrong identity control did not reject")
    rekor_code, rekor_output = runner(
        plan.positive_args, plan.wrong_rekor_environment
    )
    if rekor_code != 1 or "rekor" not in rekor_output.lower():
        raise ExternalAdmissionError("Codex wrong Rekor key control did not reject")
    return load_codex_signature_proof()


_NETWORK_DENY_PROFILE = "(version 1)(allow default)(deny network*)"


def _run_in_os_network_sandbox(
    args: tuple[str, ...], environment: Mapping[str, str]
) -> tuple[int, str]:
    executor = Path("/usr/bin/sandbox-exec")
    if not executor.is_file():
        raise ExternalAdmissionError("OS network-disabled executor is unavailable")
    try:
        completed = subprocess.run(
            (str(executor), "-p", _NETWORK_DENY_PROFILE, *args),
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ExternalAdmissionError(
            "approved Codex verifier could not execute in OS network sandbox"
        ) from exc
    return completed.returncode, completed.stdout + completed.stderr


def _run_os_network_disabled_command(
    args: tuple[str, ...], environment: Mapping[str, str]
) -> tuple[int, str]:
    return _run_in_os_network_sandbox(args, environment)


def _assert_os_network_denied() -> None:
    """Prove the exact sandbox denies both loopback and outbound connects."""

    probe = (
        "import errno,socket,sys; host=sys.argv[1]; port=int(sys.argv[2]); "
        "s=socket.socket(); s.settimeout(1); "
        "\ntry: s.connect((host,port))\n"
        "except OSError as e: sys.exit(73 if e.errno in (errno.EPERM,errno.EACCES) else 74)\n"
        "else: sys.exit(0)"
    )
    try:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        loopback_port = listener.getsockname()[1]
        loopback_code, _ = _run_in_os_network_sandbox(
            (sys.executable, "-c", probe, "127.0.0.1", str(loopback_port)), {}
        )
    except OSError as exc:
        raise ExternalAdmissionError("loopback denial probe could not launch") from exc
    finally:
        try:
            listener.close()
        except (OSError, UnboundLocalError):
            pass
    outbound_code, _ = _run_in_os_network_sandbox(
        (sys.executable, "-c", probe, "1.1.1.1", "443"), {}
    )
    if loopback_code != 73 or outbound_code != 73:
        raise ExternalAdmissionError("OS sandbox did not separately deny network probes")


def _snapshot_exact_blob(
    source: Path,
    destination: Path,
    digest: str,
    size: int,
    label: str,
    *,
    executable: bool = False,
    expected_source_mode: int | None = None,
) -> StableBlobSnapshot:
    """Open once without following links, hash-copy, and rehash a stable snapshot."""

    if type(size) is not int or size < 0:
        raise ExternalAdmissionError(f"approved {label} byte length is invalid")
    _require_digest(digest, f"approved {label} digest")
    source_fd = _open_regular_no_follow(source, label)
    try:
        before = os.fstat(source_fd)
        if expected_source_mode is not None and stat.S_IMODE(before.st_mode) != expected_source_mode:
            raise ExternalAdmissionError(f"approved {label} source mode mismatch")
        if before.st_size != size:
            raise ExternalAdmissionError(f"approved {label} byte length mismatch")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        destination_fd = os.open(destination, flags, 0o500 if executable else 0o400)
        source_hash = hashlib.sha256()
        copied = 0
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                source_hash.update(chunk)
                copied += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise OSError("short snapshot write")
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise ExternalAdmissionError(f"approved {label} changed during snapshot")
    except ExternalAdmissionError:
        raise
    except OSError as exc:
        raise ExternalAdmissionError(f"approved {label} snapshot failed") from exc
    finally:
        os.close(source_fd)
    observed = "sha256:" + source_hash.hexdigest()
    if copied != size:
        raise ExternalAdmissionError(f"approved {label} byte length mismatch")
    if observed != digest:
        raise ExternalAdmissionError(f"approved {label} digest mismatch")
    try:
        os.chmod(destination, 0o555 if executable else 0o444)
    except OSError as exc:
        raise ExternalAdmissionError(
            f"approved {label} snapshot mode cannot be fixed"
        ) from exc
    snapshot_digest, snapshot_size = _file_identity(destination)
    if (snapshot_digest, snapshot_size) != (digest, size):
        raise ExternalAdmissionError(f"approved {label} snapshot rehash mismatch")
    return StableBlobSnapshot(destination, snapshot_digest, snapshot_size)


def get_local_cas_artifact_locator(
    artifact_id: str, *, manifest: Path | None = None
) -> LocalCASArtifactLocator:
    """Return one exact locator record from the closed local CAS document."""

    if type(artifact_id) is not str or not artifact_id:
        raise ExternalAdmissionError("local CAS artifact id is invalid")
    selected_manifest = manifest or (
        Path(__file__).resolve().parents[3]
        / "packages/agent_runtime/external/local-cas-byte-locators.json"
    )
    document = parse_external_strict(
        "local-cas-byte-locators",
        _read_regular_bytes(selected_manifest, "local CAS locator manifest"),
    )
    matches = tuple(
        cast(Mapping[str, Any], item)
        for item in cast(tuple[Any, ...], document.value["artifacts"])
        if cast(Mapping[str, Any], item)["artifactId"] == artifact_id
    )
    if len(matches) != 1:
        raise ExternalAdmissionError("local CAS artifact id is not selected exactly once")
    record = matches[0]
    return LocalCASArtifactLocator(
        artifact_id=artifact_id,
        path=Path(cast(str, record["locator"])),
        bytes=cast(int, record["bytes"]),
        digest=cast(str, record["digest"]),
        mode=cast(str, record["mode"]),
    )


def _derive_spdx_catalog_bytes(wheel_bytes: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            source_bytes = archive.read("spdx_license_list/__init__.py")
        source = source_bytes.decode("utf-8")
        tree = ast.parse(source)
    except (KeyError, UnicodeDecodeError, SyntaxError, zipfile.BadZipFile, OSError) as exc:
        raise ExternalAdmissionError("SPDX source wheel cannot be parsed offline") from exc
    collections: dict[str, list[str]] = {}
    for node in tree.body:
        if (
            not isinstance(node, ast.AnnAssign)
            or not isinstance(node.target, ast.Name)
            or node.target.id not in {"LICENSES", "EXCEPTIONS"}
            or not isinstance(node.value, ast.Dict)
        ):
            continue
        identifiers: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or type(key.value) is not str:
                raise ExternalAdmissionError("SPDX source identifier is not a literal string")
            identifiers.append(key.value)
        collections[node.target.id] = identifiers
    if set(collections) != {"LICENSES", "EXCEPTIONS"}:
        raise ExternalAdmissionError("SPDX source dictionaries are not exact")
    licenses = sorted(collections["LICENSES"])
    exceptions = sorted(collections["EXCEPTIONS"])
    if (
        len(licenses) != 727
        or len(exceptions) != 84
        or len(set(licenses)) != len(licenses)
        or len(set(exceptions)) != len(exceptions)
        or any(not item or not item.isascii() for item in licenses + exceptions)
    ):
        raise ExternalAdmissionError("SPDX source identifier closure is invalid")
    return canonical_json_bytes(
        {
            "exceptions": exceptions,
            "licenseListVersion": "3.28.0",
            "licenses": licenses,
            "schema": "text-to-cad.spdx-license-catalog/1",
        }
    )


def validate_spdx_license_catalog(
    *, source_wheel: Path, catalog: Path, output_directory: Path
) -> ExternalAdmissionDocument:
    """Derive and validate the exact SPDX 3.28.0 catalog without importing code."""

    try:
        output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError as exc:
        raise ExternalAdmissionError("SPDX replay output must be new") from exc
    wheel_snapshot = _snapshot_exact_blob(
        source_wheel,
        output_directory / "spdx_license_list-3.28.0-py3-none-any.whl",
        "sha256:4470ca5de095d04e4172d8776e245d629a99abf0d08741261dd014559b746534",
        18657,
        "SPDX License List source wheel",
        expected_source_mode=0o444,
    )
    catalog_snapshot = _snapshot_exact_blob(
        catalog,
        output_directory / "spdx-license-catalog-3.28.0.json",
        SPDX_LICENSE_CATALOG_DIGEST,
        12540,
        "SPDX license catalog",
        expected_source_mode=0o444,
    )
    derived = _derive_spdx_catalog_bytes(
        _read_regular_bytes(wheel_snapshot.path, "SPDX source wheel snapshot")
    )
    observed = _read_regular_bytes(catalog_snapshot.path, "SPDX catalog snapshot")
    if derived != observed:
        raise ExternalAdmissionError("SPDX catalog differs from admitted source wheel")
    return parse_external_strict("spdx-license-catalog", observed)


def validate_local_cas_locators(
    manifest: Path, output_directory: Path
) -> ExternalAdmissionDocument:
    """Snapshot and revalidate every exact local CAS byte locator for consumption."""

    try:
        output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError as exc:
        raise ExternalAdmissionError("local CAS validation output must be new") from exc
    manifest_snapshot = _snapshot_exact_blob(
        manifest,
        output_directory / "manifest.json",
        _LOCAL_CAS_BYTE_LOCATORS_FILE_DIGEST,
        6817,
        "local CAS locator manifest",
    )
    document = parse_external_strict(
        "local-cas-byte-locators",
        _read_regular_bytes(manifest_snapshot.path, "local CAS locator snapshot"),
    )
    for index, artifact_value in enumerate(cast(tuple[Any, ...], document.value["artifacts"])):
        artifact = cast(Mapping[str, Any], artifact_value)
        locator = Path(cast(str, artifact["locator"]))
        if locator.name != cast(str, artifact["digest"]).removeprefix("sha256:"):
            raise ExternalAdmissionError("local CAS locator is not content-addressed")
        _snapshot_exact_blob(
            locator,
            output_directory / "objects" / f"{index:02d}-{locator.name}",
            cast(str, artifact["digest"]),
            cast(int, artifact["bytes"]),
            f"local CAS artifact {index}",
            expected_source_mode=0o444,
        )
    return document


def validate_noble_runtime_deb_local_locators(
    manifest: Path, output_directory: Path
) -> ExternalAdmissionDocument:
    """Snapshot and revalidate all 47 content-addressed runtime deb locators."""

    try:
        output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError as exc:
        raise ExternalAdmissionError("runtime deb validation output must be new") from exc
    manifest_snapshot = _snapshot_exact_blob(
        manifest,
        output_directory / "manifest.json",
        _NOBLE_RUNTIME_DEB_LOCAL_LOCATORS_FILE_DIGEST,
        16393,
        "runtime deb locator manifest",
    )
    document = parse_external_strict(
        "noble-runtime-deb-local-locators",
        _read_regular_bytes(manifest_snapshot.path, "runtime deb locator snapshot"),
    )
    for index, artifact_value in enumerate(cast(tuple[Any, ...], document.value["objects"])):
        artifact = cast(Mapping[str, Any], artifact_value)
        locator = Path(cast(str, artifact["locator"]))
        if locator.name != cast(str, artifact["digest"]).removeprefix("sha256:"):
            raise ExternalAdmissionError("runtime deb locator is not content-addressed")
        _snapshot_exact_blob(
            locator,
            output_directory / "objects" / f"{index:02d}-{locator.name}",
            cast(str, artifact["digest"]),
            cast(int, artifact["bytes"]),
            f"runtime deb CAS object {index}",
            expected_source_mode=0o444,
        )
    return document


def produce_codex_formal_signature_receipt(
    *,
    verifier: Path,
    bundle: Path,
    executable: Path,
    archive: Path,
    trusted_root: Path,
    verifier_checksums: Path,
    root: Path,
    timestamp: Path,
    snapshot: Path,
    targets: Path,
    output_directory: Path,
) -> ExternalAdmissionDocument:
    """Verify approved snapshots with the fixed OS network-disabled executor."""

    _assert_os_network_denied()
    return _produce_codex_formal_signature_receipt_for_test(
        verifier=verifier,
        bundle=bundle,
        executable=executable,
        archive=archive,
        trusted_root=trusted_root,
        verifier_checksums=verifier_checksums,
        root=root,
        timestamp=timestamp,
        snapshot=snapshot,
        targets=targets,
        output_directory=output_directory,
        runner=_run_os_network_disabled_command,
    )


def _produce_codex_formal_signature_receipt_for_test(
    *,
    verifier: Path,
    bundle: Path,
    executable: Path,
    archive: Path,
    trusted_root: Path,
    verifier_checksums: Path,
    root: Path,
    timestamp: Path,
    snapshot: Path,
    targets: Path,
    output_directory: Path,
    runner: Any,
) -> ExternalAdmissionDocument:
    """Private injected seam; production never accepts an alternate executor."""

    try:
        output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError as exc:
        raise ExternalAdmissionError("formal verification output must be new") from exc
    inputs = output_directory / "inputs"
    verifier_snapshot = _snapshot_exact_blob(
        verifier, inputs / "cosign", _VERIFIER_DIGEST, 108805570, "verifier", executable=True
    )
    bundle_snapshot = _snapshot_exact_blob(
        bundle, inputs / "bundle.sigstore", _BUNDLE_DIGEST, 8585, "signature bundle"
    )
    executable_snapshot = _snapshot_exact_blob(
        executable, inputs / "codex", _EXECUTABLE_DIGEST, 258278208, "executable"
    )
    archive_snapshot = _snapshot_exact_blob(
        archive, inputs / "codex.tar.gz", _ARCHIVE_DIGEST, 98970270, "archive"
    )
    trusted_root_snapshot = _snapshot_exact_blob(
        trusted_root, inputs / "trusted_root.json", _TRUSTED_ROOT_DIGEST, 6787, "trusted root"
    )
    _snapshot_exact_blob(
        verifier_checksums, inputs / "cosign_checksums.txt",
        _CHECKSUMS_DIGEST, 3906, "verifier checksums"
    )
    _snapshot_exact_blob(root, inputs / "15.root.json", _ROOT_DIGEST, 5630, "TUF root")
    _snapshot_exact_blob(
        timestamp, inputs / "timestamp.json", _TIMESTAMP_DIGEST, 449, "TUF timestamp"
    )
    _snapshot_exact_blob(
        snapshot, inputs / "165.snapshot.json", _SNAPSHOT_DIGEST, 1760, "TUF snapshot"
    )
    _snapshot_exact_blob(
        targets, inputs / "14.targets.json", _TARGETS_DIGEST, 4942, "TUF targets"
    )
    material = extract_codex_trust_material(
        trusted_root_snapshot.path, output_directory / "trust"
    )
    plan = build_codex_offline_plan(
        verifier=verifier_snapshot.path,
        bundle=bundle_snapshot.path,
        executable=executable_snapshot.path,
        archive=archive_snapshot.path,
        ca_root=material.ca_root,
        ca_intermediate=material.ca_intermediate,
        rekor_key=material.rekor_key,
        ct_key=material.ct_key,
    )
    replay_codex_offline_plan(plan, runner)
    return _formal_codex_signature_receipt()


def _file_identity(path: Path) -> tuple[str, int]:
    descriptor = _open_regular_no_follow(path, "external blob")
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise ExternalAdmissionError("external blob changed during identity read")
    except ExternalAdmissionError:
        raise
    except OSError as exc:
        raise ExternalAdmissionError("external blob cannot be read") from exc
    finally:
        os.close(descriptor)
    return "sha256:" + digest.hexdigest(), size


def _open_regular_no_follow(path: Path, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise ExternalAdmissionError(f"{label} cannot be opened without following links") from exc
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ExternalAdmissionError(f"{label} must be a regular file")
    return descriptor


def _read_regular_bytes(path: Path, label: str) -> bytes:
    descriptor = _open_regular_no_follow(path, label)
    chunks: list[bytes] = []
    try:
        before = os.fstat(descriptor)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise ExternalAdmissionError(f"{label} changed during read")
    except ExternalAdmissionError:
        raise
    except OSError as exc:
        raise ExternalAdmissionError(f"{label} cannot be read") from exc
    finally:
        os.close(descriptor)
    try:
        return b"".join(chunks)
    except MemoryError:
        raise
    except Exception as exc:
        raise ExternalAdmissionError(f"{label} cannot be materialized") from exc


def _fixed_gpgv_runner(args: tuple[str, ...]) -> tuple[int, str]:
    candidates = (Path("/opt/homebrew/bin/gpgv"), Path("/usr/bin/gpgv"))
    executable = next((candidate for candidate in candidates if candidate.is_file()), None)
    if executable is None:
        raise ExternalAdmissionError("fixed gpgv verifier is unavailable")
    try:
        completed = subprocess.run(
            (str(executable), *args), check=False, capture_output=True, text=True
        )
    except OSError as exc:
        raise ExternalAdmissionError("Ubuntu InRelease verifier could not execute") from exc
    return completed.returncode, completed.stdout + completed.stderr


def _release_sha256_entries(payload: bytes) -> Mapping[str, tuple[str, int]]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ExternalAdmissionError("Ubuntu InRelease is not UTF-8 text") from exc
    entries: dict[str, tuple[str, int]] = {}
    in_sha256 = False
    for line in lines:
        if line == "SHA256:":
            in_sha256 = True
            continue
        if not in_sha256:
            continue
        if not line.startswith(" "):
            break
        fields = line.split()
        if len(fields) != 3 or re.fullmatch(r"[0-9a-f]{64}", fields[0]) is None:
            raise ExternalAdmissionError("Ubuntu InRelease SHA256 section is malformed")
        try:
            size = int(fields[1])
        except ValueError as exc:
            raise ExternalAdmissionError("Ubuntu InRelease size is malformed") from exc
        if fields[2] in entries or size < 0:
            raise ExternalAdmissionError("Ubuntu InRelease SHA256 entry is invalid")
        entries[fields[2]] = ("sha256:" + fields[0], size)
    if not entries:
        raise ExternalAdmissionError("Ubuntu InRelease has no SHA256 authority")
    return _freeze_mapping(entries)  # type: ignore[arg-type]


def _package_stanzas(payload: bytes) -> Mapping[str, Mapping[str, str]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExternalAdmissionError("Ubuntu Packages index is not UTF-8 text") from exc
    records: dict[str, Mapping[str, str]] = {}
    for raw_stanza in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in raw_stanza.splitlines():
            if not line or line.startswith((" ", "\t")) or ": " not in line:
                continue
            key, value = line.split(": ", 1)
            fields[key] = value
        filename = fields.get("Filename")
        if filename is None:
            continue
        required = {"Package", "Version", "Architecture", "Filename", "Size", "SHA256"}
        if set(fields).issuperset(required):
            if filename in records:
                raise ExternalAdmissionError("Ubuntu Packages index has duplicate Filename")
            records[filename] = fields
    return records


def replay_noble_deb_closure(
    *,
    closure: Path,
    keyring: Path,
    inrelease_directory: Path,
    package_index_directory: Path,
    deb_directory: Path,
    output_directory: Path,
) -> ExternalAdmissionDocument:
    """Replay the complete signed Noble -> Packages -> 78-deb chain."""

    return _replay_noble_deb_closure_for_test(
        closure=closure,
        keyring=keyring,
        inrelease_directory=inrelease_directory,
        package_index_directory=package_index_directory,
        deb_directory=deb_directory,
        output_directory=output_directory,
        gpgv_runner=_fixed_gpgv_runner,
    )


def replay_noble_runtime_deb_closure(
    *,
    closure: Path,
    keyring: Path,
    inrelease_directory: Path,
    package_index_directory: Path,
    deb_directory: Path,
    output_directory: Path,
) -> ExternalAdmissionDocument:
    """Replay the independently locked 47-deb Agent runtime closure."""

    return _replay_noble_deb_closure_for_test(
        closure=closure,
        keyring=keyring,
        inrelease_directory=inrelease_directory,
        package_index_directory=package_index_directory,
        deb_directory=deb_directory,
        output_directory=output_directory,
        gpgv_runner=_fixed_gpgv_runner,
        closure_kind="noble-runtime-deb-closure-candidate",
        closure_file_digest=_NOBLE_RUNTIME_DEB_CLOSURE_FILE_DIGEST,
        closure_file_bytes=21263,
    )


def _replay_noble_deb_closure_for_test(
    *,
    closure: Path,
    keyring: Path,
    inrelease_directory: Path,
    package_index_directory: Path,
    deb_directory: Path,
    output_directory: Path,
    gpgv_runner: Any,
    closure_kind: str = "noble-deb-closure-candidate",
    closure_file_digest: str = _NOBLE_DEB_CLOSURE_FILE_DIGEST,
    closure_file_bytes: int = 35151,
) -> ExternalAdmissionDocument:
    """Private injected seam for deterministic GPG failure tests."""

    try:
        output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError as exc:
        raise ExternalAdmissionError("Noble replay output must be new") from exc
    closure_snapshot = _snapshot_exact_blob(
        closure,
        output_directory / "closure.json",
        closure_file_digest,
        closure_file_bytes,
        "Noble closure",
    )
    document = parse_external_strict(
        closure_kind,
        _read_regular_bytes(closure_snapshot.path, "Noble closure snapshot"),
    )
    value = document.value
    keyring_record = cast(Mapping[str, Any], value["ubuntuArchiveKeyring"])
    keyring_snapshot = _snapshot_exact_blob(
        keyring,
        output_directory / "ubuntu-archive-keyring.gpg",
        cast(str, keyring_record["digest"]),
        cast(int, keyring_record["bytes"]),
        "Ubuntu archive keyring",
    )
    release_authorities: dict[str, Mapping[str, tuple[str, int]]] = {}
    for record_value in cast(tuple[Any, ...], value["inRelease"]):
        record = cast(Mapping[str, Any], record_value)
        suite = cast(str, record["suite"])
        try:
            matches = tuple(inrelease_directory.glob(f"*_dists_{suite}_InRelease"))
        except OSError as exc:
            raise ExternalAdmissionError("Noble InRelease paths cannot be enumerated") from exc
        if len(matches) != 1:
            raise ExternalAdmissionError("Noble InRelease path set is not exact")
        snapshot = _snapshot_exact_blob(
            matches[0],
            output_directory / "inrelease" / f"{suite}.InRelease",
            cast(str, record["digest"]),
            cast(int, record["bytes"]),
            f"{suite} InRelease",
        )
        code, output = gpgv_runner(
            ("--status-fd=1", "--keyring", str(keyring_snapshot.path), str(snapshot.path))
        )
        fingerprint = cast(str, keyring_record["signingKeyFingerprint"])
        if code != 0 or "[GNUPG:] VALIDSIG " + fingerprint not in output:
            raise ExternalAdmissionError("Ubuntu InRelease signature verification failed")
        release_authorities[suite] = _release_sha256_entries(
            _read_regular_bytes(snapshot.path, f"{suite} InRelease snapshot")
        )

    index_records: dict[str, Mapping[str, Mapping[str, str]]] = {}
    for record_value in cast(tuple[Any, ...], value["packageIndices"]):
        record = cast(Mapping[str, Any], record_value)
        suite = cast(str, record["suite"])
        component = cast(str, record["component"])
        authority = f"{suite}/{record['path']}"
        expected = release_authorities[suite].get(cast(str, record["path"]))
        if expected != (record["digest"], record["bytes"]):
            raise ExternalAdmissionError("Packages index is not bound by verified InRelease")
        source = package_index_directory / f"{suite}_{component}_Packages.xz"
        snapshot = _snapshot_exact_blob(
            source,
            output_directory / "indices" / f"{suite}_{component}_Packages.xz",
            cast(str, record["digest"]),
            cast(int, record["bytes"]),
            f"{authority} index",
        )
        try:
            decompressed = lzma.decompress(
                _read_regular_bytes(snapshot.path, f"{authority} index snapshot")
            )
        except (OSError, lzma.LZMAError) as exc:
            raise ExternalAdmissionError("Packages index decompression failed") from exc
        observed_uncompressed = "sha256:" + hashlib.sha256(decompressed).hexdigest()
        if observed_uncompressed != record["uncompressedDigest"]:
            raise ExternalAdmissionError("Packages uncompressed digest mismatch")
        index_records[authority] = _package_stanzas(decompressed)

    packages = cast(tuple[Any, ...], value["packages"])
    for record_value in packages:
        record = cast(Mapping[str, Any], record_value)
        local_name = cast(str, record["localFilename"])
        _snapshot_exact_blob(
            deb_directory / local_name,
            output_directory / "debs" / local_name,
            cast(str, record["digest"]),
            cast(int, record["bytes"]),
            f"deb {local_name}",
        )
        for authority_value in cast(tuple[Any, ...], record["indexAuthorities"]):
            authority = cast(str, authority_value)
            indexed = index_records.get(authority, {}).get(cast(str, record["poolPath"]))
            if indexed is None:
                raise ExternalAdmissionError("deb is absent from declared Packages authority")
            expected_package = cast(str, record["package"]).split(":", 1)[0]
            if (
                indexed["Package"] != expected_package
                or indexed["Version"] != record["version"]
                or indexed["Architecture"] != record["architecture"]
                or indexed["Filename"] != record["poolPath"]
                or indexed["SHA256"] != cast(str, record["digest"]).removeprefix("sha256:")
                or indexed["Size"] != str(record["bytes"])
            ):
                raise ExternalAdmissionError("deb metadata differs from Packages authority")
    return document


def admit_local_blob(
    source: Path, mirror_root: Path, expected_digest: str, expected_bytes: int
) -> Path:
    """Copy exact regular bytes into a local content-addressed mirror."""

    if type(expected_bytes) is not int or expected_bytes < 0:
        raise ExternalAdmissionError("external blob byte length is invalid")
    _require_digest(expected_digest, "external blob digest")
    destination_dir = mirror_root / "sha256"
    destination = destination_dir / expected_digest.removeprefix("sha256:")
    try:
        destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ExternalAdmissionError("local mirror directory cannot be created") from exc
    if destination.exists() or destination.is_symlink():
        existing_digest, existing_bytes = _file_identity(destination)
        if (existing_digest, existing_bytes) != (expected_digest, expected_bytes):
            raise ExternalAdmissionError("existing mirror object is a substitution")
        return destination

    try:
        with tempfile.TemporaryDirectory(prefix=".admit-", dir=destination_dir) as staging:
            temporary = Path(staging) / "object"
            snapshot = _snapshot_exact_blob(
                source,
                temporary,
                expected_digest,
                expected_bytes,
                "external blob",
            )
            try:
                os.link(snapshot.path, destination)
            except FileExistsError:
                existing_digest, existing_bytes = _file_identity(destination)
                if (existing_digest, existing_bytes) != (expected_digest, expected_bytes):
                    raise ExternalAdmissionError("existing mirror object is a substitution")
    except ExternalAdmissionError:
        raise
    except OSError as exc:
        raise ExternalAdmissionError("local mirror snapshot cannot be committed") from exc
    return destination


def _mirror_version_response(
    value: Any, *, may_have_written: bool
) -> tuple[str, str]:
    if type(value) is not tuple or len(value) != 2:
        raise ExternalMirrorPublishError(
            "external mirror adapter response is malformed",
            may_have_written=may_have_written,
        )
    version_id, etag = value
    if (
        type(version_id) is not str
        or _MIRROR_VERSION_ID_RE.fullmatch(version_id) is None
        or type(etag) is not str
        or _MIRROR_ETAG_RE.fullmatch(etag) is None
    ):
        raise ExternalMirrorPublishError(
            "external mirror adapter response is malformed",
            may_have_written=may_have_written,
        )
    return version_id, etag


def publish_external_blob(
    *,
    store: ExternalMirrorStore,
    bucket: str,
    prefix: str,
    payload: bytes,
    digest: str,
) -> Mapping[str, CanonicalJSONValue]:
    """Publish one exact blob through a versioned create-only adapter."""

    if type(bucket) is not str or not bucket or not bucket.isascii():
        raise ExternalMirrorPublishError("external mirror bucket is invalid", may_have_written=False)
    if (
        type(prefix) is not str
        or not prefix
        or not prefix.isascii()
        or prefix.startswith("/")
        or any(part in ("", ".", "..") for part in prefix.split("/"))
    ):
        raise ExternalMirrorPublishError("external mirror prefix is invalid", may_have_written=False)
    if type(payload) is not bytes:
        raise ExternalMirrorPublishError("external mirror payload must be bytes", may_have_written=False)
    if type(digest) is not str or _DIGEST_RE.fullmatch(digest) is None:
        raise ExternalMirrorPublishError("external mirror digest is invalid", may_have_written=False)
    actual_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if actual_digest != digest:
        raise ExternalMirrorPublishError(
            "external mirror payload digest mismatch", may_have_written=False
        )
    key = prefix.rstrip("/") + "/sha256/" + digest.removeprefix("sha256:")
    try:
        versioning = store.versioning_status(bucket)
    except Exception as exc:
        raise ExternalMirrorPublishError(
            "external mirror versioning preflight failed", may_have_written=False
        ) from exc
    if versioning != "Enabled":
        raise ExternalMirrorPublishError(
            "external mirror bucket versioning must be Enabled", may_have_written=False
        )
    try:
        current = store.current_version(bucket, key)
    except Exception as exc:
        raise ExternalMirrorPublishError(
            "external mirror current-version preflight failed", may_have_written=False
        ) from exc
    if current is not None:
        version_id, etag = _mirror_version_response(current, may_have_written=False)
        try:
            reread = store.get_exact_version(bucket, key, version_id)
        except Exception as exc:
            raise ExternalMirrorPublishError(
                "external mirror exact reuse reread failed", may_have_written=False
            ) from exc
        if type(reread) is not bytes or reread != payload:
            raise ExternalMirrorPublishError(
                "external mirror existing object is not exact", may_have_written=False
            )
        disposition = "reused-exact-version"
    else:
        try:
            response = store.put_create_only(bucket, key, payload)
        except Exception as exc:
            raise ExternalMirrorPublishError(
                "external mirror create-only write failed", may_have_written=True
            ) from exc
        version_id, etag = _mirror_version_response(response, may_have_written=True)
        try:
            reread = store.get_exact_version(bucket, key, version_id)
        except Exception as exc:
            raise ExternalMirrorPublishError(
                "external mirror exact-version reread failed", may_have_written=True
            ) from exc
        disposition = "created"
    if type(reread) is not bytes or reread != payload:
        raise ExternalMirrorPublishError(
            "external mirror exact-version reread mismatch",
            may_have_written=disposition == "created",
        )
    receipt = {
        "bucket": bucket,
        "bytes": len(payload),
        "digest": digest,
        "disposition": disposition,
        "etag": etag,
        "exactVersionReread": True,
        "key": key,
        "schema": "text-to-cad.agent-runtime-external-mirror-publication/1",
        "versionId": version_id,
    }
    try:
        return _freeze_mapping(receipt)
    except Exception as exc:
        raise ExternalMirrorPublishError(
            "external mirror publication receipt is not canonical",
            may_have_written=disposition == "created",
        ) from exc
