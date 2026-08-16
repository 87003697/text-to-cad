"""Closed external-byte admission contracts for the sealed Agent runtime."""

from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
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


@dataclass(frozen=True)
class ExternalAdmissionDocument:
    """One typed, recursively immutable external admission document."""

    kind: str
    value: Mapping[str, CanonicalJSONValue]


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
    "sha256:53729fdf0db8bfac5a722db686a19bc073240de175eadb7509326f4fcf2c91b2"
)
BUILDER_REPRODUCIBILITY_DIAGNOSTIC_DIGEST = (
    "sha256:a8c99137e96c6e520be9ce2d3a50c8d6976891f37111d28e13e0742d1068b024"
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
        {"claims", "inRelease", "packageIndices", "packages", "schema", "snapshot", "status"},
        "Noble deb closure",
    )
    if value["schema"] != "text-to-cad.agent-runtime-noble-deb-closure-candidate/1":
        raise ExternalAdmissionError("Noble deb closure schema is invalid")
    if canonical_json_digest(value) != NOBLE_DEB_CLOSURE_DIGEST:
        raise ExternalAdmissionError("Noble deb closure does not equal reviewed closure digest")
    if value["snapshot"] != "20260815T000000Z" or value["status"] != "local-candidate":
        raise ExternalAdmissionError("Noble deb closure identity is invalid")
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
    if kind == "builder-reproducibility-diagnostic":
        _validate_builder_reproducibility_diagnostic(value)
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
        root = json.loads(trusted_root.read_bytes())
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


def _run_offline_command(
    args: tuple[str, ...], environment: Mapping[str, str]
) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ExternalAdmissionError("approved Codex verifier could not execute") from exc
    return completed.returncode, completed.stdout + completed.stderr


def _require_exact_blob(path: Path, digest: str, size: int, label: str) -> None:
    actual_digest, actual_size = _file_identity(path)
    if actual_size != size:
        raise ExternalAdmissionError(f"approved {label} byte length mismatch")
    if actual_digest != digest:
        raise ExternalAdmissionError(f"approved {label} digest mismatch")


def produce_codex_formal_signature_receipt(
    *,
    verifier: Path,
    bundle: Path,
    executable: Path,
    archive: Path,
    trusted_root: Path,
    output_directory: Path,
    runner: Any = _run_offline_command,
) -> ExternalAdmissionDocument:
    """Verify every approved byte/control, then promote only the two formal leaves."""

    _require_exact_blob(verifier, _VERIFIER_DIGEST, 108805570, "verifier")
    _require_exact_blob(bundle, _BUNDLE_DIGEST, 8585, "signature bundle")
    _require_exact_blob(executable, _EXECUTABLE_DIGEST, 258278208, "executable")
    _require_exact_blob(archive, _ARCHIVE_DIGEST, 98970270, "archive")
    _require_exact_blob(trusted_root, _TRUSTED_ROOT_DIGEST, 6787, "trusted root")
    material = extract_codex_trust_material(trusted_root, output_directory)
    plan = build_codex_offline_plan(
        verifier=verifier,
        bundle=bundle,
        executable=executable,
        archive=archive,
        ca_root=material.ca_root,
        ca_intermediate=material.ca_intermediate,
        rekor_key=material.rekor_key,
        ct_key=material.ct_key,
    )
    replay_codex_offline_plan(plan, runner)
    return _formal_codex_signature_receipt()


def _file_identity(path: Path) -> tuple[str, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ExternalAdmissionError("external blob cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ExternalAdmissionError("external blob must be a regular file")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ExternalAdmissionError("external blob cannot be read") from exc
    return "sha256:" + digest.hexdigest(), size


def admit_local_blob(
    source: Path, mirror_root: Path, expected_digest: str, expected_bytes: int
) -> Path:
    """Copy exact regular bytes into a local content-addressed mirror."""

    _require_digest(expected_digest, "external blob digest")
    actual_digest, actual_bytes = _file_identity(source)
    if actual_digest != expected_digest:
        raise ExternalAdmissionError("external blob digest mismatch")
    if actual_bytes != expected_bytes:
        raise ExternalAdmissionError("external blob byte length mismatch")
    destination_dir = mirror_root / "sha256"
    destination = destination_dir / expected_digest.removeprefix("sha256:")
    destination_dir.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        existing_digest, existing_bytes = _file_identity(destination)
        if (existing_digest, existing_bytes) != (expected_digest, expected_bytes):
            raise ExternalAdmissionError("existing mirror object is a substitution")
        return destination

    descriptor, temporary_name = tempfile.mkstemp(prefix=".admit-", dir=destination_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            while chunk := input_stream.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            existing_digest, existing_bytes = _file_identity(destination)
            if (existing_digest, existing_bytes) != (expected_digest, expected_bytes):
                raise ExternalAdmissionError("existing mirror object is a substitution")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def publish_external_blob(
    *,
    store: ExternalMirrorStore,
    bucket: str,
    prefix: str,
    payload: bytes,
    digest: str,
) -> Mapping[str, CanonicalJSONValue]:
    """Publish one exact blob through a versioned create-only adapter."""

    _require_digest(digest, "external mirror digest")
    actual_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if actual_digest != digest:
        raise ExternalAdmissionError("external mirror payload digest mismatch")
    if store.versioning_status(bucket) != "Enabled":
        raise ExternalAdmissionError("external mirror bucket versioning must be Enabled")
    key = prefix.rstrip("/") + "/sha256/" + digest.removeprefix("sha256:")
    if store.current_version(bucket, key) is not None:
        raise ExternalAdmissionError("external mirror key already exists")
    version_id, etag = store.put_create_only(bucket, key, payload)
    reread = store.get_exact_version(bucket, key, version_id)
    if reread != payload:
        raise ExternalAdmissionError("external mirror exact-version reread mismatch")
    receipt = {
        "bucket": bucket,
        "bytes": len(payload),
        "digest": digest,
        "etag": etag,
        "key": key,
        "versionId": version_id,
    }
    return _freeze_mapping(receipt)
