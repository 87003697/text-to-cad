from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
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


class FakeMirrorStore:
    def __init__(self, *, versioning: str | None = "Enabled") -> None:
        self.versioning = versioning
        self.objects: dict[tuple[str, str, str], bytes] = {}
        self.current: dict[tuple[str, str], tuple[str, str]] = {}
        self.put_calls = 0

    def versioning_status(self, bucket: str) -> str | None:
        return self.versioning

    def current_version(self, bucket: str, key: str) -> tuple[str, str] | None:
        return self.current.get((bucket, key))

    def put_create_only(self, bucket: str, key: str, payload: bytes) -> tuple[str, str]:
        self.put_calls += 1
        version_id = "version-1"
        etag = '"etag-1"'
        self.objects[(bucket, key, version_id)] = payload
        self.current[(bucket, key)] = (version_id, etag)
        return version_id, etag

    def get_exact_version(self, bucket: str, key: str, version_id: str) -> bytes:
        return self.objects[(bucket, key, version_id)]


class ExternalAdmissionContractTests(unittest.TestCase):
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
        self.assertEqual(external_digest(approval), CODEX_APPROVAL_DIGEST)
        self.assertEqual(external_digest(policy), CODEX_POLICY_DIGEST)
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
        self.assertFalse(document.value["claims"]["debBytesMirrored"])
        self.assertFalse(document.value["claims"]["immutableMirrorVisible"])
        self.assertFalse(document.value["claims"]["formalAdmission"])
        self.assertEqual(
            document.value["localImage"]["id"],
            "sha256:49de767070e9a205a5424860162e409c8ff4268e0567effb8d9265fc553a1ee2",
        )
        self.assertEqual(
            canonical_external_bytes(document), BUILDER_CANDIDATE.read_bytes().removesuffix(b"\n")
        )

        extended = copy.deepcopy(document.value)
        extended["localImage"]["tagIsAuthority"] = True
        from scripts.pilot.agent_runtime import canonical_json_bytes
        from scripts.pilot.agent_runtime.external_admission import ExternalAdmissionError

        with self.assertRaisesRegex(ExternalAdmissionError, "unexpected keys"):
            parse_external_strict("builder-input-candidate", canonical_json_bytes(extended))

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
            with self.assertRaisesRegex(ExternalAdmissionError, "regular file"):
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
        self.assertTrue(receipt["key"].endswith(digest.removeprefix("sha256:")))
        self.assertEqual(store.put_calls, 1)

    def test_codex_proof_receipt_is_exact_and_not_formal(self) -> None:
        from scripts.pilot.agent_runtime.external_admission import (
            CODEX_PROOF_RECEIPT_DIGEST,
            external_digest,
            load_codex_signature_proof,
            parse_external_strict,
            canonical_external_bytes,
        )

        proof = load_codex_signature_proof()
        self.assertEqual(external_digest(proof), CODEX_PROOF_RECEIPT_DIGEST)
        self.assertEqual(proof.value["result"], "proof-only")
        self.assertEqual(proof.value["trustBootstrap"]["status"], "not-formal-admission")
        self.assertEqual(
            parse_external_strict(
                "codex-signature-verification", canonical_external_bytes(proof)
            ),
            proof,
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
        self.assertFalse(document.value["claims"]["formalSignatureReceipt"])
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


if __name__ == "__main__":
    unittest.main()
