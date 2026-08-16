from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
import inspect
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BUILDER_CANDIDATE = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "builder"
    / "builder-input-candidate.json"
)
NOBLE_DEB_CLOSURE = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "builder"
    / "noble-deb-closure-candidate.json"
)
BUILDER_REPRODUCIBILITY_DIAGNOSTIC = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "builder"
    / "builder-reproducibility-diagnostic.json"
)
BUILDER_NETWORK_RECEIPT = (
    REPO_ROOT / "packages" / "agent_runtime" / "external" / "builder"
    / "builder-network-denial-launch-receipt.json"
)
NOBLE_REPLAY_RECEIPT = (
    REPO_ROOT / "packages" / "agent_runtime" / "external" / "builder"
    / "noble-deb-closure-replay-receipt.json"
)
NOBLE_RUNTIME_DEB_CLOSURE = (
    REPO_ROOT / "packages" / "agent_runtime" / "external" / "builder"
    / "noble-runtime-deb-closure-candidate.json"
)
NOBLE_RUNTIME_REPLAY_RECEIPT = (
    REPO_ROOT / "packages" / "agent_runtime" / "external" / "builder"
    / "noble-runtime-deb-closure-replay-receipt.json"
)
NOBLE_RUNTIME_LOCAL_LOCATORS = (
    REPO_ROOT / "packages" / "agent_runtime" / "external" / "builder"
    / "noble-runtime-deb-local-locators.json"
)
RUNTIME_OS_BUILD_RECEIPT = (
    REPO_ROOT / "packages" / "agent_runtime" / "external" / "builder"
    / "runtime-os-network-denial-launch-receipt.json"
)
PYTHON_WHEEL_LOCK = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "python"
    / "python-wheel-lock.json"
)
PYTHON_CANDIDATE = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "python"
    / "python-wheel-admission-candidate.json"
)
NODE_CANDIDATE = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "node"
    / "node-admission-candidate.json"
)
CODEX_CANDIDATE = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "codex"
    / "codex-admission-candidate.json"
)
CODEX_SIGNATURE_RECEIPT = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "codex"
    / "codex-signature-verification-receipt.json"
)
CODEX_RETRIEVAL_RECEIPT = (
    REPO_ROOT
    / "packages"
    / "agent_runtime"
    / "external"
    / "codex"
    / "codex-retrieval-metadata.json"
)
CODEX_OS_NETWORK_RECEIPT = (
    REPO_ROOT / "packages" / "agent_runtime" / "external" / "codex"
    / "codex-os-network-denied-verification-launch-receipt.json"
)
LOCAL_CAS_LOCATORS = (
    REPO_ROOT / "packages" / "agent_runtime" / "external"
    / "local-cas-byte-locators.json"
)


class FakeMirrorStore:
    def __init__(self, *, versioning: str | None = "Enabled") -> None:
        self.versioning = versioning
        self.objects: dict[tuple[str, str, str], bytes] = {}
        self.current: dict[tuple[str, str], tuple[str, str]] = {}
        self.calls = 0
        self.put_calls = 0

    def versioning_status(self, bucket: str) -> str | None:
        self.calls += 1
        return self.versioning

    def current_version(self, bucket: str, key: str) -> tuple[str, str] | None:
        self.calls += 1
        return self.current.get((bucket, key))

    def put_create_only(self, bucket: str, key: str, payload: bytes) -> tuple[str, str]:
        self.calls += 1
        self.put_calls += 1
        version_id = "version-1"
        etag = '"etag-1"'
        self.objects[(bucket, key, version_id)] = payload
        self.current[(bucket, key)] = (version_id, etag)
        return version_id, etag

    def get_exact_version(self, bucket: str, key: str, version_id: str) -> bytes:
        self.calls += 1
        return self.objects[(bucket, key, version_id)]


class ExternalAdmissionContractTests(unittest.TestCase):
    def test_local_cas_locator_manifest_is_closed(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            LOCAL_CAS_BYTE_LOCATORS_DIGEST,
            external_digest,
            parse_external_strict,
        )

        manifest = parse_external_strict(
            "local-cas-byte-locators", LOCAL_CAS_LOCATORS.read_bytes()
        )
        self.assertEqual(external_digest(manifest), LOCAL_CAS_BYTE_LOCATORS_DIGEST)
        self.assertEqual(manifest.value["artifacts"][0]["kind"], "builderDockerArchive")
        self.assertEqual(
            [artifact["distribution"] for artifact in manifest.value["artifacts"][1:]],
            ["numpy", "trimesh", "Pillow"],
        )

    def test_noble_runtime_deb_closure_is_distinct_exact_and_complete(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            NOBLE_RUNTIME_DEB_CLOSURE_DIGEST,
            external_digest,
            parse_external_strict,
        )

        closure = parse_external_strict(
            "noble-runtime-deb-closure-candidate", NOBLE_RUNTIME_DEB_CLOSURE.read_bytes()
        )
        self.assertEqual(external_digest(closure), NOBLE_RUNTIME_DEB_CLOSURE_DIGEST)
        self.assertEqual(len(closure.value["packages"]), 47)
        self.assertEqual(
            closure.value["runtimeRoots"],
            ("bash", "coreutils", "file", "findutils", "git", "git-lfs", "locales",
             "procps", "ripgrep", "sed"),
        )
        replay = parse_external_strict(
            "noble-runtime-deb-closure-replay",
            NOBLE_RUNTIME_REPLAY_RECEIPT.read_bytes(),
        )
        self.assertEqual(replay.value["result"], "verified")
        locators = parse_external_strict(
            "noble-runtime-deb-local-locators", NOBLE_RUNTIME_LOCAL_LOCATORS.read_bytes()
        )
        self.assertEqual(len(locators.value["objects"]), 47)

    def test_runtime_os_build_receipt_binds_exact_offline_image(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            RUNTIME_OS_NETWORK_DENIAL_RECEIPT_DIGEST,
            external_digest,
            parse_external_strict,
        )

        receipt = parse_external_strict(
            "runtime-os-network-denial-launch", RUNTIME_OS_BUILD_RECEIPT.read_bytes()
        )
        self.assertEqual(
            external_digest(receipt), RUNTIME_OS_NETWORK_DENIAL_RECEIPT_DIGEST
        )
        self.assertEqual(receipt.value["networkMode"], "none")
        self.assertEqual(
            receipt.value["runtimeImageId"],
            "sha256:921e5f6de0f7a8b2fdafff4f5f561fc5a797baa0787e89bf3ec7cfe4fe6cf61c",
        )
        self.assertEqual(receipt.value["debCount"], 47)
        self.assertFalse(receipt.value["formalAdmission"])
        self.assertFalse(receipt.value["immutableMirrorVisible"])
        self.assertEqual(
            set(receipt.value["toolVersions"]),
            {"bash", "coreutils", "file", "findutils", "git", "git-lfs",
             "locales", "procps", "ripgrep", "sed"},
        )

    def test_network_denial_and_noble_replay_receipts_are_exact(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            BUILDER_NETWORK_DENIAL_RECEIPT_DIGEST,
            NOBLE_DEB_REPLAY_RECEIPT_DIGEST,
            external_digest,
            parse_external_strict,
        )

        network = parse_external_strict(
            "builder-network-denial-launch", BUILDER_NETWORK_RECEIPT.read_bytes()
        )
        replay = parse_external_strict(
            "noble-deb-closure-replay", NOBLE_REPLAY_RECEIPT.read_bytes()
        )
        self.assertEqual(external_digest(network), BUILDER_NETWORK_DENIAL_RECEIPT_DIGEST)
        self.assertEqual(external_digest(replay), NOBLE_DEB_REPLAY_RECEIPT_DIGEST)
        self.assertEqual(network.value["networkMode"], "none")
        self.assertEqual(network.value["result"], "network-disabled-build-succeeded")
        self.assertEqual(replay.value["debCount"], 78)

    def test_noble_deb_closure_is_exact_signed_and_networkless_rebuilt(self) -> None:
        from scripts.pilot.agent_runtime import canonical_json_bytes
        from scripts.pilot.agent_runtime.external_admission import (
            NOBLE_DEB_CLOSURE_DIGEST,
            ExternalAdmissionError,
            external_digest,
            parse_external_strict,
        )

        closure = parse_external_strict(
            "noble-deb-closure-candidate", NOBLE_DEB_CLOSURE.read_bytes()
        )
        self.assertEqual(external_digest(closure), NOBLE_DEB_CLOSURE_DIGEST)
        self.assertEqual(len(closure.value["packages"]), 78)
        self.assertEqual(len(closure.value["packageIndices"]), 8)
        self.assertTrue(closure.value["claims"]["inReleaseAuthenticated"])
        self.assertTrue(closure.value["claims"]["packageIndexHashesMatched"])
        self.assertTrue(closure.value["claims"]["networklessRebuild"])
        self.assertFalse(closure.value["claims"]["formalAdmission"])
        substituted = copy.deepcopy(closure.value)
        substituted["packages"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ExternalAdmissionError, "reviewed closure digest"):
            parse_external_strict(
                "noble-deb-closure-candidate", canonical_json_bytes(substituted)
            )

    def test_builder_reproducibility_diagnostic_refuses_false_success(self) -> None:
        from scripts.pilot.agent_runtime import canonical_json_bytes
        from scripts.pilot.agent_runtime.external_admission import (
            BUILDER_REPRODUCIBILITY_DIAGNOSTIC_DIGEST,
            ExternalAdmissionError,
            external_digest,
            parse_external_strict,
        )

        diagnostic = parse_external_strict(
            "builder-reproducibility-diagnostic",
            BUILDER_REPRODUCIBILITY_DIAGNOSTIC.read_bytes(),
        )
        self.assertEqual(
            external_digest(diagnostic), BUILDER_REPRODUCIBILITY_DIAGNOSTIC_DIGEST
        )
        self.assertFalse(diagnostic.value["reproducible"])
        self.assertEqual(diagnostic.value["fileInventory"]["changed"], 846)
        substituted = copy.deepcopy(diagnostic.value)
        substituted["reproducible"] = True
        with self.assertRaisesRegex(ExternalAdmissionError, "diagnostic digest"):
            parse_external_strict(
                "builder-reproducibility-diagnostic", canonical_json_bytes(substituted)
            )

    def test_codex_evidence_contract_matches_reviewed_subject_and_predicates(self) -> None:
        from scripts.pilot.agent_runtime.contracts import PREDICATES, SUBJECT_FIELDS

        self.assertEqual(
            SUBJECT_FIELDS["codex-admission"],
            (
                "codexVersion", "platform", "retrievalReceiptDigest", "archiveDigest",
                "executableDigest", "signatureBundleDigest", "signaturePolicyDigest",
                "signatureVerificationReceiptDigest", "elfClosureDigest",
            ),
        )
        self.assertEqual(
            PREDICATES["codex-admission"],
            (
                "versionExact", "platformArtifactExact", "retrievalMetadataRecorded",
                "archiveDigestExact", "executableDigestExact", "archiveSingleExecutableExact",
                "signatureBundleDigestExact", "signaturePolicyExact", "signatureVerified",
                "certificateIdentityExact", "certificateIssuerExact", "transparencyLogVerified",
                "elfClosureClosed", "nodeAbsentSmokePassed", "noninteractiveSmokePassed",
                "immutableMirrorVisible",
            ),
        )

    def test_codex_normative_approval_and_policy_are_exact(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            CODEX_APPROVAL_DIGEST,
            CODEX_POLICY_DIGEST,
            canonical_external_bytes,
            external_digest,
            load_codex_normative_inputs,
            parse_external_strict,
        )

        approval, policy = load_codex_normative_inputs()
        self.assertEqual(
            CODEX_APPROVAL_DIGEST,
            "sha256:85bf8165e3ded898ec4892c8ae3ab48172566d871c79add02c96f389f663d5c4",
        )
        self.assertEqual(
            CODEX_POLICY_DIGEST,
            "sha256:8bfd47abc5c13845f82a218fe79ac2378adb29c4cb302a8b9a41eb631f3451d2",
        )
        self.assertEqual(external_digest(approval), CODEX_APPROVAL_DIGEST)
        self.assertEqual(external_digest(policy), CODEX_POLICY_DIGEST)
        self.assertEqual(
            approval.value["approvedBytes"]["legacyTrustMaterial"]["fulcioRootDigest"],
            "sha256:f989aa23def87c549404eadba767768d2a3c8d6d30a8b793f9f518a8eafd2cf5",
        )
        self.assertEqual(
            parse_external_strict(
                "sigstore-trust-anchor-approval",
                canonical_external_bytes(approval),
            ),
            approval,
        )
        self.assertEqual(
            parse_external_strict(
                "codex-signature-policy",
                canonical_external_bytes(policy),
            ),
            policy,
        )

    def test_codex_normative_inputs_reject_extension_or_substitution(self) -> None:
        from scripts.pilot.agent_runtime import canonical_json_bytes
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            load_codex_normative_inputs,
            parse_external_strict,
        )

        approval, policy = load_codex_normative_inputs()
        extended = copy.deepcopy(approval.value)
        extended["retrievedFrom"] = "https://example.invalid"
        with self.assertRaisesRegex(ExternalAdmissionError, "unexpected keys"):
            parse_external_strict(
                "sigstore-trust-anchor-approval",
                canonical_json_bytes(extended),
            )

        substituted = copy.deepcopy(policy.value)
        substituted["githubWorkflow"]["wildcardsAllowed"] = True
        with self.assertRaisesRegex(ExternalAdmissionError, "normative policy"):
            parse_external_strict(
                "codex-signature-policy",
                canonical_json_bytes(substituted),
            )

    def test_raw_canonical_json_is_not_an_external_document(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            canonical_external_bytes,
        )

        with self.assertRaisesRegex(ExternalAdmissionError, "typed"):
            canonical_external_bytes({"schema": "not-authority"})

    def test_builder_handoff_is_closed_and_explicitly_not_formal_admission(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            canonical_external_bytes,
            parse_external_strict,
        )

        document = parse_external_strict("builder-input-candidate", BUILDER_CANDIDATE.read_bytes())
        self.assertEqual(document.value["status"], "local-candidate")
        self.assertTrue(document.value["claims"]["debBytesMirrored"])
        self.assertTrue(document.value["claims"]["networklessRebuild"])
        self.assertFalse(document.value["claims"]["immutableMirrorVisible"])
        self.assertFalse(document.value["claims"]["formalAdmission"])
        self.assertEqual(
            document.value["localImage"]["id"],
            "sha256:9f53dae6dd44ad326e18c7620b45230607c5e81c8dfc1cf59494656e295faeff",
        )
        self.assertEqual(
            canonical_external_bytes(document), BUILDER_CANDIDATE.read_bytes().removesuffix(b"\n")
        )

        extended = copy.deepcopy(document.value)
        extended["localImage"]["tagIsAuthority"] = True
        from scripts.pilot.agent_runtime import canonical_json_bytes
        from scripts.pilot.agent_runtime.external_admission import ExternalAdmissionError

        with self.assertRaisesRegex(ExternalAdmissionError, "exact candidate digest"):
            parse_external_strict("builder-input-candidate", canonical_json_bytes(extended))

        scalar_substitution = copy.deepcopy(document.value)
        scalar_substitution["apt"]["installedPackageCount"] = True
        with self.assertRaisesRegex(ExternalAdmissionError, "exact candidate digest"):
            parse_external_strict(
                "builder-input-candidate", canonical_json_bytes(scalar_substitution)
            )

    def test_python_wheel_lock_is_exact_closed_and_runtime_minimal(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            canonical_external_bytes,
            parse_external_strict,
        )

        document = parse_external_strict("python-wheel-lock", PYTHON_WHEEL_LOCK.read_bytes())
        self.assertEqual(
            [artifact["distribution"] for artifact in document.value["runtimeArtifacts"]],
            ["numpy", "trimesh", "Pillow"],
        )
        self.assertEqual(len(document.value["builderArtifacts"]), 6)
        self.assertEqual(
            canonical_external_bytes(document), PYTHON_WHEEL_LOCK.read_bytes().removesuffix(b"\n")
        )

        extended = copy.deepcopy(document.value)
        extended["runtimeArtifacts"][0]["indexTrusted"] = True
        from scripts.pilot.agent_runtime import canonical_json_bytes
        from scripts.pilot.agent_runtime.external_admission import ExternalAdmissionError

        with self.assertRaisesRegex(ExternalAdmissionError, "unexpected keys"):
            parse_external_strict("python-wheel-lock", canonical_json_bytes(extended))

    def test_python_wheel_candidate_binds_offline_import_and_auditwheel(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import parse_external_strict

        document = parse_external_strict(
            "python-wheel-admission-candidate", PYTHON_CANDIDATE.read_bytes()
        )
        self.assertEqual(document.value["offlineImport"]["numpy"], "2.4.6")
        self.assertEqual(document.value["offlineImport"]["trimesh"], "4.12.2")
        self.assertEqual(document.value["offlineImport"]["Pillow"], "12.2.0")
        self.assertEqual(document.value["auditwheel"]["numpy"]["platformTag"], "manylinux_2_27_x86_64")
        self.assertEqual(document.value["auditwheel"]["Pillow"]["platformTag"], "manylinux_2_17_x86_64")
        self.assertEqual(document.value["auditwheel"]["trimesh"]["kind"], "pure-python")
        self.assertFalse(document.value["claims"]["formalAdmission"])

    def test_local_cas_rejects_symlinks_mismatch_and_existing_substitution(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            admit_local_blob,
        )

        payload = b"exact external bytes"
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(payload)
            admitted = admit_local_blob(source, root / "mirror", digest, len(payload))
            self.assertEqual(admitted, root / "mirror" / "sha256" / digest.removeprefix("sha256:"))
            self.assertEqual(admitted.read_bytes(), payload)
            self.assertEqual(admitted.stat().st_mode & 0o777, 0o444)
            self.assertEqual(admit_local_blob(source, root / "mirror", digest, len(payload)), admitted)

            link = root / "link.bin"
            os.symlink(source, link)
            with self.assertRaisesRegex(ExternalAdmissionError, "without following links"):
                admit_local_blob(link, root / "other", digest, len(payload))
            with self.assertRaisesRegex(ExternalAdmissionError, "digest"):
                admit_local_blob(source, root / "other", "sha256:" + "0" * 64, len(payload))

            admitted.chmod(0o644)
            admitted.write_bytes(b"substitution")
            with self.assertRaisesRegex(ExternalAdmissionError, "existing mirror object"):
                admit_local_blob(source, root / "mirror", digest, len(payload))

    def test_remote_mirror_refuses_unversioned_store_before_write(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            publish_external_blob,
        )

        payload = b"immutable external object"
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        store = FakeMirrorStore(versioning=None)
        with self.assertRaisesRegex(ExternalAdmissionError, "versioning"):
            publish_external_blob(
                store=store,
                bucket="arcwm-code-us-west-2",
                prefix="ericzyma/text-to-cad/agent-runtime/external",
                payload=payload,
                digest=digest,
            )
        self.assertEqual(store.put_calls, 0)

    def test_remote_mirror_uses_content_key_create_only_and_exact_version_reread(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import publish_external_blob

        payload = b"immutable external object"
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        store = FakeMirrorStore()
        receipt = publish_external_blob(
            store=store,
            bucket="arcwm-code-us-west-2",
            prefix="ericzyma/text-to-cad/agent-runtime/external",
            payload=payload,
            digest=digest,
        )
        self.assertEqual(receipt["versionId"], "version-1")
        self.assertEqual(receipt["disposition"], "created")
        self.assertEqual(
            set(receipt),
            {"bucket", "bytes", "digest", "disposition", "etag",
             "exactVersionReread", "key", "schema", "versionId"},
        )
        self.assertEqual(
            receipt["schema"], "text-to-cad.agent-runtime-external-mirror-publication/1"
        )
        self.assertTrue(receipt["exactVersionReread"])
        self.assertTrue(receipt["key"].endswith(digest.removeprefix("sha256:")))
        self.assertEqual(store.put_calls, 1)

        reused = publish_external_blob(
            store=store,
            bucket="arcwm-code-us-west-2",
            prefix="ericzyma/text-to-cad/agent-runtime/external",
            payload=payload,
            digest=digest,
        )
        self.assertEqual(reused["disposition"], "reused-exact-version")
        self.assertEqual(store.put_calls, 1)

    def test_remote_mirror_preflight_and_write_uncertainty_are_typed(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalMirrorPublishError,
            publish_external_blob,
        )

        store = FakeMirrorStore()
        with self.assertRaises(ExternalMirrorPublishError) as caught:
            publish_external_blob(
                store=store, bucket="bucket", prefix="prefix", payload=b"bytes",
                digest="not-a-digest",
            )
        self.assertFalse(caught.exception.may_have_written)
        self.assertEqual(store.calls, 0)

        class FailingPut(FakeMirrorStore):
            def put_create_only(self, bucket: str, key: str, payload: bytes):
                self.calls += 1
                raise OSError("ambiguous transport failure")

        digest = "sha256:" + hashlib.sha256(b"bytes").hexdigest()
        with self.assertRaises(ExternalMirrorPublishError) as written:
            publish_external_blob(
                store=FailingPut(), bucket="bucket", prefix="prefix",
                payload=b"bytes", digest=digest,
            )
        self.assertTrue(written.exception.may_have_written)

        class MalformedPut(FakeMirrorStore):
            def put_create_only(self, bucket: str, key: str, payload: bytes):
                self.put_calls += 1
                return ("version-only",)

        with self.assertRaises(ExternalMirrorPublishError) as malformed:
            publish_external_blob(
                store=MalformedPut(), bucket="bucket", prefix="prefix",
                payload=b"bytes", digest=digest,
            )
        self.assertTrue(malformed.exception.may_have_written)

    def test_codex_proof_receipt_is_exact_and_not_formal(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            CODEX_PROOF_RECEIPT_DIGEST,
            external_digest,
            load_codex_signature_proof,
            parse_external_strict,
            canonical_external_bytes,
        )

        proof = load_codex_signature_proof()
        self.assertEqual(
            CODEX_PROOF_RECEIPT_DIGEST,
            "sha256:ee8632e8d7e9610014e0d59e0b074414540e6e6b5e8feca04d2ef519db488e84",
        )
        self.assertEqual(external_digest(proof), CODEX_PROOF_RECEIPT_DIGEST)
        self.assertEqual(proof.value["result"], "proof-only")
        self.assertEqual(proof.value["trustBootstrap"]["status"], "not-formal-admission")
        self.assertEqual(
            parse_external_strict(
                "codex-signature-verification", canonical_external_bytes(proof)
            ),
            proof,
        )

    def test_formal_signature_subreceipt_changes_only_two_reviewed_leaves(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            CODEX_FORMAL_RECEIPT_DIGEST,
            _formal_codex_signature_receipt,
            external_digest,
            load_codex_signature_proof,
            parse_external_strict,
        )

        proof = copy.deepcopy(load_codex_signature_proof().value)
        formal = _formal_codex_signature_receipt()
        expected = copy.deepcopy(proof)
        expected["result"] = "verified"
        expected["trustBootstrap"]["status"] = "verified"
        self.assertEqual(formal.value, expected)
        persisted = parse_external_strict(
            "codex-signature-verification", CODEX_SIGNATURE_RECEIPT.read_bytes()
        )
        self.assertEqual(persisted.value, formal.value)
        self.assertEqual(external_digest(persisted), CODEX_FORMAL_RECEIPT_DIGEST)

    def test_codex_formal_launch_binds_os_network_denial_and_snapshots(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            CODEX_OS_NETWORK_DENIED_LAUNCH_RECEIPT_DIGEST,
            external_digest,
            parse_external_strict,
        )

        receipt = parse_external_strict(
            "codex-os-network-denied-verification-launch",
            CODEX_OS_NETWORK_RECEIPT.read_bytes(),
        )
        self.assertEqual(
            external_digest(receipt), CODEX_OS_NETWORK_DENIED_LAUNCH_RECEIPT_DIGEST
        )
        self.assertEqual(receipt.value["executor"], "/usr/bin/sandbox-exec")
        self.assertTrue(receipt.value["stableSnapshotsRehashed"])
        self.assertTrue(receipt.value["controls"]["loopbackNetworkDenied"])
        self.assertTrue(receipt.value["controls"]["outboundNetworkDenied"])

    def test_formal_signature_producer_rejects_substituted_bytes_before_runner(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            _produce_codex_formal_signature_receipt_for_test,
            produce_codex_formal_signature_receipt,
        )

        self.assertNotIn(
            "runner", inspect.signature(produce_codex_formal_signature_receipt).parameters
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            substituted = root / "substituted"
            substituted.write_bytes(b"not approved")
            calls = []
            with self.assertRaisesRegex(ExternalAdmissionError, "byte length"):
                _produce_codex_formal_signature_receipt_for_test(
                    verifier=substituted,
                    bundle=substituted,
                    executable=substituted,
                    archive=substituted,
                    trusted_root=substituted,
                    verifier_checksums=substituted,
                    root=substituted,
                    timestamp=substituted,
                    snapshot=substituted,
                    targets=substituted,
                    output_directory=root / "trust",
                    runner=lambda args, env: calls.append((args, env)),
                )
            self.assertEqual(calls, [])

    def test_external_document_constructor_deep_freezes_mutable_input(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import ExternalAdmissionDocument

        source = {"nested": {"items": [1, 2]}}
        document = ExternalAdmissionDocument(kind="test-only", value=source)
        source["nested"]["items"].append(3)
        self.assertEqual(document.value["nested"]["items"], (1, 2))
        with self.assertRaises(TypeError):
            document.value["nested"]["new"] = True

    def test_stable_snapshot_detects_source_change_during_single_open_copy(self) -> None:
        from scripts.pilot.agent_runtime import external_admission

        payload = b"x" * (1024 * 1024 + 1)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(payload)
            original_read = os.read
            changed = False

            def racing_read(descriptor: int, size: int) -> bytes:
                nonlocal changed
                chunk = original_read(descriptor, size)
                if chunk and not changed:
                    changed = True
                    with source.open("ab") as stream:
                        stream.write(b"substitution")
                return chunk

            with mock.patch.object(external_admission.os, "read", side_effect=racing_read):
                with self.assertRaisesRegex(
                    external_admission.ExternalAdmissionError, "changed during snapshot"
                ):
                    external_admission._snapshot_exact_blob(
                        source, root / "snapshot", digest, len(payload), "racing blob"
                    )

    def test_existing_local_cas_rejects_change_during_single_open_validation(self) -> None:
        from scripts.pilot.agent_runtime import external_admission

        payload = b"y" * (1024 * 1024 + 1)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(payload)
            admitted = external_admission.admit_local_blob(
                source, root / "mirror", digest, len(payload)
            )
            admitted.chmod(0o644)
            original_read = os.read
            changed = False

            def racing_read(descriptor: int, size: int) -> bytes:
                nonlocal changed
                chunk = original_read(descriptor, size)
                if chunk and not changed:
                    changed = True
                    with admitted.open("ab") as stream:
                        stream.write(b"substitution")
                return chunk

            with mock.patch.object(external_admission.os, "read", side_effect=racing_read):
                with self.assertRaisesRegex(
                    external_admission.ExternalAdmissionError,
                    "changed during identity read",
                ):
                    external_admission.admit_local_blob(
                        source, root / "mirror", digest, len(payload)
                    )

    def test_retrieval_metadata_is_closed_non_authoritative_and_ordered(self) -> None:
        from scripts.pilot.agent_runtime import canonical_json_bytes
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            load_codex_normative_inputs,
            parse_external_strict,
        )

        approval, _ = load_codex_normative_inputs()
        approved = approval.value["approvedBytes"]
        identities = [
            ("archive", approved["archive"]),
            ("signatureBundle", approved["signatureBundle"]),
            ("verifierBinary", {"bytes": approved["verifier"]["binaryBytes"], "digest": approved["verifier"]["binaryDigest"]}),
            ("verifierChecksums", {"bytes": approved["verifier"]["checksumsBytes"], "digest": approved["verifier"]["checksumsDigest"]}),
            ("root", {"bytes": approved["trustedRoot"]["rootBytes"], "digest": approved["trustedRoot"]["rootDigest"]}),
            ("timestamp", {"bytes": approved["trustedRoot"]["timestampBytes"], "digest": approved["trustedRoot"]["timestampDigest"]}),
            ("snapshot", {"bytes": approved["trustedRoot"]["snapshotBytes"], "digest": approved["trustedRoot"]["snapshotDigest"]}),
            ("targets", {"bytes": approved["trustedRoot"]["targetsBytes"], "digest": approved["trustedRoot"]["targetsDigest"]}),
            ("trustedRoot", {"bytes": approved["trustedRoot"]["trustedRootBytes"], "digest": approved["trustedRoot"]["trustedRootDigest"]}),
        ]
        document = {
            "schema": "text-to-cad.codex-retrieval-metadata/1",
            "observedAt": "2026-08-16T11:28:49Z",
            "objects": [
                {
                    "kind": kind,
                    "requestedUrl": f"https://example.test/{kind}",
                    "finalUrl": f"https://cdn.example.test/{kind}",
                    "redirects": [f"https://cdn.example.test/{kind}"],
                    "bytes": identity["bytes"],
                    "digest": identity["digest"],
                    "responseMetadataDigest": "sha256:" + "a" * 64,
                }
                for kind, identity in identities
            ],
        }
        parsed = parse_external_strict("codex-retrieval-metadata", canonical_json_bytes(document))
        self.assertEqual(parsed.value["objects"][0]["kind"], "archive")

        reordered = copy.deepcopy(document)
        reordered["objects"][0], reordered["objects"][1] = (
            reordered["objects"][1], reordered["objects"][0]
        )
        with self.assertRaisesRegex(ExternalAdmissionError, "order"):
            parse_external_strict("codex-retrieval-metadata", canonical_json_bytes(reordered))
        document["objects"][0]["requestedUrl"] = "https://user@example.test/archive"
        with self.assertRaisesRegex(ExternalAdmissionError, "HTTPS URL"):
            parse_external_strict("codex-retrieval-metadata", canonical_json_bytes(document))

    def test_concrete_retrieval_receipt_binds_observed_approved_bytes(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            CODEX_RETRIEVAL_RECEIPT_DIGEST,
            external_digest,
            parse_external_strict,
        )

        receipt = parse_external_strict(
            "codex-retrieval-metadata", CODEX_RETRIEVAL_RECEIPT.read_bytes()
        )
        self.assertEqual(external_digest(receipt), CODEX_RETRIEVAL_RECEIPT_DIGEST)
        self.assertEqual(receipt.value["objects"][0]["kind"], "archive")
        self.assertEqual(receipt.value["objects"][-1]["kind"], "trustedRoot")

    def test_node_candidate_binds_signed_checksum_elf_and_offline_smoke(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import parse_external_strict

        document = parse_external_strict("node-admission-candidate", NODE_CANDIDATE.read_bytes())
        self.assertEqual(document.value["version"], "24.13.0")
        self.assertEqual(document.value["signature"]["result"], "verified")
        self.assertEqual(document.value["runtimeProbe"]["versionOutput"], "v24.13.0")
        self.assertEqual(
            document.value["elf"]["needed"],
            (
                "ld-linux-x86-64.so.2", "libc.so.6", "libdl.so.2", "libgcc_s.so.1",
                "libm.so.6", "libpthread.so.0", "libstdc++.so.6",
            ),
        )
        self.assertFalse(document.value["claims"]["immutableMirrorVisible"])
        self.assertFalse(document.value["claims"]["formalAdmission"])

    def test_codex_candidate_records_static_pie_node_absence_and_proof_boundary(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import parse_external_strict

        document = parse_external_strict("codex-admission-candidate", CODEX_CANDIDATE.read_bytes())
        self.assertEqual(document.value["versionOutput"], "codex-cli 0.147.0")
        self.assertEqual(document.value["elf"]["needed"], ())
        self.assertIsNone(document.value["elf"]["interpreter"])
        self.assertTrue(document.value["probes"]["nodeAbsent"])
        self.assertTrue(document.value["probes"]["noninteractiveParserSmoke"])
        self.assertTrue(document.value["claims"]["formalSignatureReceipt"])
        self.assertFalse(document.value["claims"]["formalAdmission"])

    def test_codex_legacy_offline_plan_is_deny_proxy_and_has_three_negatives(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import build_codex_offline_plan

        plan = build_codex_offline_plan(
            verifier=Path("/mirror/cosign"),
            bundle=Path("/mirror/bundle"),
            executable=Path("/mirror/executable"),
            archive=Path("/mirror/archive"),
            ca_root=Path("/trust/root.pem"),
            ca_intermediate=Path("/trust/intermediate.pem"),
            rekor_key=Path("/trust/rekor.pem"),
            ct_key=Path("/trust/ctfe.pem"),
        )
        self.assertIn("--offline", plan.positive_args)
        self.assertNotIn("--trusted-root", plan.positive_args)
        self.assertEqual(plan.positive_environment["HTTPS_PROXY"], "http://127.0.0.1:9")
        self.assertEqual(plan.positive_environment["SIGSTORE_NO_CACHE"], "1")
        self.assertEqual(plan.wrong_artifact_args[-1], "/mirror/archive")
        self.assertTrue(any("rust-v0.147.1" in item for item in plan.wrong_identity_args))
        self.assertEqual(
            plan.wrong_rekor_environment["SIGSTORE_REKOR_PUBLIC_KEY"], "/trust/ctfe.pem"
        )

    def test_codex_offline_replay_returns_only_proof_and_requires_all_negatives(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            build_codex_offline_plan,
            replay_codex_offline_plan,
        )

        plan = build_codex_offline_plan(
            verifier=Path("/mirror/cosign"), bundle=Path("/mirror/bundle"),
            executable=Path("/mirror/executable"), archive=Path("/mirror/archive"),
            ca_root=Path("/trust/root.pem"), ca_intermediate=Path("/trust/intermediate.pem"),
            rekor_key=Path("/trust/rekor.pem"), ct_key=Path("/trust/ctfe.pem"),
        )
        results = iter(
            [(0, "Verified OK"), (1, "payload mismatch"), (1, "identity mismatch"), (1, "rekor key not found")]
        )
        proof = replay_codex_offline_plan(plan, lambda _args, _env: next(results))
        self.assertEqual(proof.value["result"], "proof-only")

        bad_results = iter(
            [(0, "Verified OK"), (1, "payload mismatch"), (1, "identity mismatch"), (0, "Verified OK")]
        )
        with self.assertRaisesRegex(ExternalAdmissionError, "wrong Rekor key"):
            replay_codex_offline_plan(plan, lambda _args, _env: next(bad_results))

    def test_trust_extraction_rejects_any_unapproved_root_before_output(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            ExternalAdmissionError,
            extract_codex_trust_material,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            substituted = root / "trusted-root.json"
            substituted.write_text("{}", encoding="ascii")
            with self.assertRaisesRegex(ExternalAdmissionError, "byte length"):
                extract_codex_trust_material(substituted, root / "out")
            self.assertFalse((root / "out").exists())

    def test_legacy_pem_projection_has_no_trailing_newline(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import _pem_bytes

        pem = _pem_bytes("CERTIFICATE", b"exact-cert-bytes")
        self.assertEqual(
            pem,
            b"-----BEGIN CERTIFICATE-----\nZXhhY3QtY2VydC1ieXRlcw==\n-----END CERTIFICATE-----",
        )
        self.assertFalse(pem.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
