"""Fake-boundary tests for the single-image CVM Browser Runtime workflow."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.pilot import cvm_browser_runtime as runtime


IMAGE = "sha256:" + "a" * 64
LOADED_IMAGE = "sha256:" + "b" * 64
REVISION = "c" * 40
WORKFLOW = {"module": "d" * 64, "wrapper": "e" * 64}


def image_lock_value(image_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "image": {
            "name": "text-to-cad-browser-runtime",
            "id": image_id,
            "base_image": "fixed-base",
            "base_id": "sha256:" + "1" * 64,
            "playwright_mcp_version": "fixed",
            "content_size_bytes": 100,
            "architecture": "amd64",
        },
        "built_from_ref": REVISION,
        "notes": "fixed",
    }


def provision_receipt(handle: str, owner: str) -> dict[str, object]:
    source = {
        "role": "runtime",
        "id": IMAGE,
        "platform": "linux/amd64",
        "sourceRevision": REVISION,
        "archiveReference": "text-to-cad-browser-runtime-transfer:test",
    }
    retained = {
        "role": "runtime",
        "id": LOADED_IMAGE,
        "platform": "linux/amd64",
        "sourceRevision": REVISION,
    }
    return {
        "schema": runtime.PROVISION_SCHEMA,
        "status": "provisioned",
        "handle": handle,
        "ownerNonce": owner,
        "image": source,
        "retainedImageId": LOADED_IMAGE,
        "retainedImage": retained,
        "remoteLockSha256": "f" * 64,
        "archiveSha256": "6" * 64,
        "workflowFiles": WORKFLOW,
        "freeBytes": runtime.MIN_FREE_BYTES,
        "transferAbsent": True,
        "retryAllowed": False,
    }


class BrowserRuntimeWorkflowTests(unittest.TestCase):
    def test_replace_bytes_cleans_temporary_after_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "lock.json"
            target.write_bytes(b"old")
            with (
                mock.patch.object(runtime.os, "replace", side_effect=OSError("fixed")),
                self.assertRaises(OSError),
            ):
                runtime._replace_bytes(target, b"new")
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(target.with_name("lock.json.tmp").exists())

    def test_lock_activation_rolls_back_when_receipt_publish_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "image-lock.json"
            receipt = Path(temp) / "provision.json"
            original = json.dumps(image_lock_value(IMAGE)).encode("ascii")
            lock.write_bytes(original)
            with (
                mock.patch.object(runtime, "REMOTE_IMAGE_LOCK", lock),
                mock.patch.object(
                    runtime, "_write_json", side_effect=OSError("fixed")
                ),
                self.assertRaises(OSError),
            ):
                runtime._activate_lock_with_receipt(
                    original, image_lock_value(LOADED_IMAGE), receipt, {"fixed": True}
                )
            self.assertEqual(lock.read_bytes(), original)
            self.assertFalse(receipt.exists())

    def test_loaded_image_failure_always_attempts_transport_tag_cleanup(self) -> None:
        reference = "text-to-cad-browser-runtime-probe:fixed"
        source = provision_receipt("cvmbr-" + "1" * 24, "2" * 32)["image"]
        references = {reference: (LOADED_IMAGE,)}

        def docker(*args, **kwargs):
            if args[:2] == ("image", "rm"):
                references.pop(args[2], None)
            return subprocess.CompletedProcess(args, 0, "", "")

        retained = {
            "role": "runtime",
            "id": LOADED_IMAGE,
            "platform": "linux/amd64",
            "sourceRevision": "0" * 40,
        }
        with (
            mock.patch.object(runtime, "_docker", side_effect=docker),
            mock.patch.object(
                runtime,
                "_image_ids",
                side_effect=lambda value: references.get(value, ()),
            ),
            mock.patch.object(runtime, "_inspect_image", return_value=retained),
            self.assertRaisesRegex(runtime.RuntimeWorkflowError, "attestation"),
        ):
            runtime._load_retained_image(Path("fixed.tar"), reference, source)
        self.assertEqual(references, {})

    def test_loaded_image_reports_transport_tag_cleanup_failure(self) -> None:
        reference = "text-to-cad-browser-runtime-probe:fixed"
        source = provision_receipt("cvmbr-" + "1" * 24, "2" * 32)["image"]
        retained = {
            "role": "runtime",
            "id": LOADED_IMAGE,
            "platform": "linux/amd64",
            "sourceRevision": REVISION,
        }
        completed = subprocess.CompletedProcess([], 1, "", "")
        with (
            mock.patch.object(runtime, "_docker", return_value=completed),
            mock.patch.object(runtime, "_image_ids", return_value=(LOADED_IMAGE,)),
            mock.patch.object(runtime, "_inspect_image", return_value=retained),
            self.assertRaisesRegex(runtime.RuntimeWorkflowError, "cleanup failed"),
        ):
            runtime._load_retained_image(Path("fixed.tar"), reference, source)

    def test_public_prepare_accepts_only_one_runtime_image(self) -> None:
        args = runtime.parse_args(
            ["prepare", "--source-revision", REVISION, "--runtime-image", IMAGE]
        )
        self.assertEqual(args.runtime_image, IMAGE)

    def test_prepare_binds_one_image_and_removes_temporary_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_root = root / ".cvm-browser-runtime"
            references: dict[str, tuple[str, ...]] = {}

            def image_ids(reference: str):
                return references.get(reference, ())

            def docker(*args, **kwargs):
                if args[:2] == ("image", "tag"):
                    references[args[3]] = (args[2],)
                elif args[:2] == ("image", "save"):
                    output = Path(args[args.index("--output") + 1])
                    output.write_bytes(b"fixed-runtime-archive")
                elif args[:2] == ("image", "rm"):
                    references.pop(args[2], None)
                return subprocess.CompletedProcess(args, 0, "", "")

            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "_workflow_revision", return_value=REVISION),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
                mock.patch.object(
                    runtime,
                    "_inspect_image",
                    return_value={
                        "role": "runtime",
                        "id": IMAGE,
                        "platform": "linux/amd64",
                        "sourceRevision": REVISION,
                    },
                ),
                mock.patch.object(runtime, "_image_ids", side_effect=image_ids),
                mock.patch.object(runtime, "_docker", side_effect=docker),
            ):
                receipt = runtime.prepare(REVISION, IMAGE)

            self.assertEqual(receipt["status"], "prepared")
            self.assertRegex(receipt["prepareNonce"], r"^[0-9a-f]{32}$")
            self.assertEqual(receipt["image"]["role"], "runtime")
            self.assertEqual(references, {})
            self.assertTrue(
                (state_root / receipt["handle"] / "runtime-image.tar").is_file()
            )

    def test_prepare_rejects_image_from_another_revision(self) -> None:
        with (
            mock.patch.object(runtime, "_workflow_revision", return_value=REVISION),
            mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
            mock.patch.object(
                runtime,
                "_inspect_image",
                return_value={
                    "role": "runtime",
                    "id": IMAGE,
                    "platform": "linux/amd64",
                    "sourceRevision": "f" * 40,
                },
            ),
        ):
            with self.assertRaisesRegex(runtime.RuntimeWorkflowError, "revision"):
                runtime.prepare(REVISION, IMAGE)

    def test_prepare_same_image_creates_fresh_handles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"

            def docker(*args, **kwargs):
                if args[:2] == ("image", "save"):
                    Path(args[args.index("--output") + 1]).write_bytes(b"archive")
                return subprocess.CompletedProcess(args, 0, "", "")

            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "_workflow_revision", return_value=REVISION),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
                mock.patch.object(
                    runtime,
                    "_inspect_image",
                    return_value={
                        "role": "runtime",
                        "id": IMAGE,
                        "platform": "linux/amd64",
                        "sourceRevision": REVISION,
                    },
                ),
                mock.patch.object(
                    runtime,
                    "_image_ids",
                    side_effect=[(), (IMAGE,), (), (), (IMAGE,), ()],
                ),
                mock.patch.object(runtime, "_docker", side_effect=docker),
            ):
                first = runtime.prepare(REVISION, IMAGE)
                second = runtime.prepare(REVISION, IMAGE)
            self.assertNotEqual(first["handle"], second["handle"])

    def test_remote_begin_failure_preserves_closed_reason(self) -> None:
        completed = subprocess.CompletedProcess(
            [], 1, "", "cvm-browser-runtime: CVM disk gate failed\n"
        )
        with self.assertRaisesRegex(runtime.RuntimeWorkflowError, "CVM disk gate"):
            runtime._remote_failure(completed, "begin")

    def test_remote_status_reads_receipts_without_mutation(self) -> None:
        handle = "cvmbr-" + "2" * 24
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            state.mkdir(parents=True)
            receipt = {
                "schema": runtime.PROVISION_SCHEMA,
                "status": "failed",
                "handle": handle,
                "ownerNonce": "3" * 32,
                "transferAbsent": True,
                "retryAllowed": False,
            }
            (state / "provision.json").write_text(
                json.dumps(receipt), encoding="ascii"
            )
            with mock.patch.object(runtime, "STATE_ROOT", state_root):
                observed = runtime.remote_status(handle)
        self.assertEqual(observed["status"], "observed")
        self.assertEqual(observed["receipts"], {"provision": receipt})

    def test_remote_status_rejects_extra_or_incomplete_receipts(self) -> None:
        handle = "cvmbr-" + "4" * 24
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            state.mkdir(parents=True)
            (state / "provision.json").write_text(
                json.dumps(
                    {
                        "schema": runtime.PROVISION_SCHEMA,
                        "status": "failed",
                        "handle": handle,
                        "extra": True,
                    }
                ),
                encoding="ascii",
            )
            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                self.assertRaisesRegex(
                    runtime.RuntimeWorkflowError, "status receipt is invalid"
                ),
            ):
                runtime.remote_status(handle)

    def test_remote_status_accepts_failed_provision_with_cleanup_residue(self) -> None:
        handle = "cvmbr-" + "6" * 24
        receipt = {
            "schema": runtime.PROVISION_SCHEMA,
            "status": "failed",
            "handle": handle,
            "ownerNonce": "7" * 32,
            "transferAbsent": False,
            "retryAllowed": False,
        }
        runtime._validate_status_receipt(
            "provision", receipt, handle, runtime.PROVISION_SCHEMA
        )

    def test_remote_status_rejects_impossible_abort_and_probe_receipts(self) -> None:
        handle = "cvmbr-" + "7" * 24
        abort = {
            "schema": "cvm-browser-runtime.abort/1",
            "status": "aborted",
            "handle": handle,
            "ownerNonce": "8" * 32,
            "transferAbsent": False,
            "retryAllowed": False,
        }
        probe = {
            "schema": runtime.PROBE_SCHEMA,
            "status": "succeeded",
            "handle": handle,
            "ownerNonce": "8" * 32,
            "retainedImageId": LOADED_IMAGE,
            "programDigest": None,
            "pngSha256": "sha256:" + "9" * 64,
            "capabilitySchema": "text-to-cad.browser-runtime-capability/1",
            "cleanupAbsent": True,
            "freeBytes": runtime.MIN_FREE_BYTES,
            "retryAllowed": False,
        }
        with self.assertRaisesRegex(runtime.RuntimeWorkflowError, "invalid"):
            runtime._validate_status_receipt(
                "abort", abort, handle, "cvm-browser-runtime.abort/1"
            )
        with self.assertRaisesRegex(runtime.RuntimeWorkflowError, "invalid"):
            runtime._validate_status_receipt(
                "probe", probe, handle, runtime.PROBE_SCHEMA
            )

    def test_provision_receipt_binds_cross_engine_artifact_identity(self) -> None:
        handle = "cvmbr-" + "8" * 24
        receipt = provision_receipt(handle, "9" * 32)
        runtime._validate_status_receipt(
            "provision", receipt, handle, runtime.PROVISION_SCHEMA
        )
        changed = json.loads(json.dumps(receipt))
        changed["retainedImage"]["sourceRevision"] = "0" * 40
        with self.assertRaisesRegex(runtime.RuntimeWorkflowError, "invalid"):
            runtime._validate_status_receipt(
                "provision", changed, handle, runtime.PROVISION_SCHEMA
            )

    def test_remote_status_includes_terminal_attempt_receipts(self) -> None:
        handle = "cvmbr-" + "5" * 24
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            state.mkdir(parents=True)
            attempt = {
                "schema": "cvm-browser-runtime.probe-attempt/1",
                "handle": handle,
            }
            (state / "probe-attempt.json").write_text(
                json.dumps(attempt), encoding="ascii"
            )
            with mock.patch.object(runtime, "STATE_ROOT", state_root):
                observed = runtime.remote_status(handle)
        self.assertEqual(observed["receipts"], {"probe-attempt": attempt})

    def test_transfer_destination_matches_proven_cvm_push_root(self) -> None:
        handle = "cvmbr-" + "1" * 24
        self.assertEqual(
            runtime._remote_transfer_destination(handle),
            f"cvm:~/text-to-cad/.cvm-browser-runtime/{handle}/incoming/",
        )

    def test_remote_begin_requires_deployed_workflow_and_archive_capacity(self) -> None:
        handle = "cvmbr-" + "1" * 24
        owner = "2" * 32
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            enough = SimpleNamespace(free=runtime.MIN_FREE_BYTES + 10_000)
            docker_server = subprocess.CompletedProcess(
                [], 0, json.dumps({"Os": "linux", "Arch": "amd64"}), ""
            )
            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
                mock.patch.object(runtime.shutil, "disk_usage", return_value=enough),
                mock.patch.object(runtime, "_docker", return_value=docker_server),
            ):
                receipt = runtime.remote_begin(
                    handle, owner, 1000, "3" * 64, WORKFLOW["module"], WORKFLOW["wrapper"]
                )
            self.assertEqual(receipt["status"], "ready")
            self.assertTrue((state_root / handle / "incoming").is_dir())

    def test_remote_provision_rebinds_exact_id_after_cross_engine_load(self) -> None:
        handle = "cvmbr-" + "3" * 24
        owner = "4" * 32
        reference = "text-to-cad-browser-runtime-probe:fixed"
        archive_bytes = b"runtime archive"
        archive_digest = runtime._sha256_bytes(archive_bytes)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_root = root / ".cvm-browser-runtime"
            incoming = state_root / handle / "incoming"
            incoming.mkdir(parents=True)
            begin = {
                "schema": "cvm-browser-runtime.begin/1",
                "status": "ready",
                "handle": handle,
                "ownerNonce": owner,
                "archive": {"bytes": len(archive_bytes), "sha256": archive_digest},
                "workflowFiles": WORKFLOW,
                "freeBytes": runtime.MIN_FREE_BYTES,
            }
            source_image = {
                "role": "runtime",
                "id": IMAGE,
                "platform": "linux/amd64",
                "sourceRevision": REVISION,
                "archiveReference": reference,
            }
            prepare = {
                "schema": runtime.PREPARE_SCHEMA,
                "handle": handle,
                "archive": begin["archive"],
                "image": source_image,
            }
            (state_root / handle / "begin.json").write_text(
                json.dumps(begin), encoding="ascii"
            )
            (incoming / "prepare.json").write_text(
                json.dumps(prepare), encoding="ascii"
            )
            (incoming / "runtime-image.tar").write_bytes(archive_bytes)
            image_lock = root / "image-lock.json"
            image_lock.write_text(
                json.dumps(image_lock_value(IMAGE)),
                encoding="ascii",
            )
            references: dict[str, tuple[str, ...]] = {}

            def docker(*args, **kwargs):
                if args[:2] == ("image", "load"):
                    references[reference] = (LOADED_IMAGE,)
                elif args[:2] == ("image", "rm"):
                    references.pop(args[2], None)
                return subprocess.CompletedProcess(args, 0, "", "")

            def inspect(image_id: str):
                return {
                    "role": "runtime",
                    "id": image_id,
                    "platform": "linux/amd64",
                    "sourceRevision": REVISION,
                }

            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "REMOTE_IMAGE_LOCK", image_lock),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
                mock.patch.object(runtime, "_disk_gate", return_value=runtime.MIN_FREE_BYTES),
                mock.patch.object(runtime, "_docker", side_effect=docker),
                mock.patch.object(
                    runtime,
                    "_image_ids",
                    side_effect=lambda value: references.get(value, ()),
                ),
                mock.patch.object(runtime, "_inspect_image", side_effect=inspect),
            ):
                receipt = runtime._remote_provision_operation(handle, owner)

            self.assertEqual(receipt["image"]["id"], IMAGE)
            self.assertEqual(receipt["retainedImageId"], LOADED_IMAGE)
            self.assertEqual(receipt["retainedImage"]["id"], LOADED_IMAGE)
            self.assertEqual(references, {})
            self.assertEqual(
                json.loads(image_lock.read_text(encoding="ascii"))["image"]["id"],
                LOADED_IMAGE,
            )
            self.assertFalse(incoming.exists())

    def test_remote_probe_uses_production_browser_runtime_job_and_cleans_up(self) -> None:
        handle = "cvmbr-" + "4" * 24
        owner = "5" * 32
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            state.mkdir(parents=True)
            remote_lock = Path(temp) / "image-lock.json"
            remote_lock.write_text(
                json.dumps(image_lock_value(LOADED_IMAGE)), encoding="ascii"
            )
            provision = provision_receipt(handle, owner)
            provision["remoteLockSha256"] = runtime._sha256_file(remote_lock)
            (state / "provision.json").write_text(json.dumps(provision), encoding="ascii")

            class FakeJob:
                container_name = "fixed-container"
                network_name = "fixed-network"

                def __init__(self, owner_nonce, capability_dir, image_lock_path):
                    self.capability_dir = Path(capability_dir)
                    self.owner_nonce = owner_nonce
                    self.image_ref = json.loads(
                        Path(image_lock_path).read_text(encoding="ascii")
                    )["image"]["id"]

                def start(self):
                    self.capability_dir.mkdir(parents=True)
                    (self.capability_dir / "runtime.json").write_text(
                        json.dumps(
                            {
                                "schema": "text-to-cad.browser-runtime-capability/1",
                                "jobId": owner,
                                "imageRef": LOADED_IMAGE,
                            }
                        ),
                        encoding="ascii",
                    )

                def preflight(self):
                    (self.capability_dir / "preflight.json").write_text(
                        json.dumps(
                            {
                                "passed": True,
                                "programDigest": runtime.CAD_RENDER_PROGRAMS["residual"],
                                "pngSha256": "sha256:" + "8" * 64,
                            }
                        ),
                        encoding="ascii",
                    )

                def stop(self):
                    return None

            absent = subprocess.CompletedProcess([], 1, "", "")
            enough = SimpleNamespace(free=runtime.MIN_FREE_BYTES + 1)
            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "REMOTE_IMAGE_LOCK", remote_lock),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
                mock.patch.object(runtime.shutil, "disk_usage", return_value=enough),
                mock.patch("browser_runtime.BrowserRuntimeJob", FakeJob),
                mock.patch.object(runtime, "_docker", return_value=absent),
            ):
                receipt = runtime.remote_probe(handle)

                with self.assertRaisesRegex(
                    runtime.RuntimeWorkflowError, "receipt already exists"
                ):
                    runtime.remote_probe(handle)

            self.assertEqual(receipt["status"], "succeeded")
            self.assertTrue(receipt["cleanupAbsent"])

    def test_remote_probe_rejects_redeployed_source_lock(self) -> None:
        handle = "cvmbr-" + "6" * 24
        owner = "7" * 32
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            state.mkdir(parents=True)
            remote_lock = Path(temp) / "image-lock.json"
            retained_bytes = json.dumps(image_lock_value(LOADED_IMAGE)).encode("ascii")
            remote_lock.write_bytes(retained_bytes)
            provision = provision_receipt(handle, owner)
            provision["remoteLockSha256"] = runtime._sha256_bytes(retained_bytes)
            (state / "provision.json").write_text(
                json.dumps(provision), encoding="ascii"
            )
            remote_lock.write_text(
                json.dumps(image_lock_value(IMAGE)), encoding="ascii"
            )
            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "REMOTE_IMAGE_LOCK", remote_lock),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
                self.assertRaisesRegex(runtime.RuntimeWorkflowError, "lock changed"),
            ):
                runtime.remote_probe(handle)

    def test_failed_remote_provision_and_probe_are_terminal(self) -> None:
        handle = "cvmbr-" + "9" * 24
        owner = "8" * 32
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            incoming = state / "incoming"
            incoming.mkdir(parents=True)
            begin = {
                "schema": "cvm-browser-runtime.begin/1",
                "status": "ready",
                "handle": handle,
                "ownerNonce": owner,
                "archive": {"bytes": 1, "sha256": "7" * 64},
                "workflowFiles": WORKFLOW,
                "freeBytes": runtime.MIN_FREE_BYTES,
            }
            (state / "begin.json").write_text(json.dumps(begin), encoding="ascii")
            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
            ):
                receipt = runtime.remote_provision(handle, owner)
                self.assertEqual(receipt["status"], "failed")
                with self.assertRaisesRegex(
                    runtime.RuntimeWorkflowError, "receipt already exists"
                ):
                    runtime.remote_provision(handle, owner)

            provision = provision_receipt(handle, owner)
            remote_lock = Path(temp) / "image-lock.json"
            remote_lock.write_text(
                json.dumps(image_lock_value(LOADED_IMAGE)), encoding="ascii"
            )
            provision["remoteLockSha256"] = runtime._sha256_file(remote_lock)
            (state / "provision.json").unlink()
            (state / "provision.json").write_text(json.dumps(provision), encoding="ascii")

            class FailingJob:
                container_name = "failed-container"
                network_name = "failed-network"

                def __init__(self, owner_nonce, capability_dir, image_lock_path):
                    self.capability_dir = Path(capability_dir)
                    self.image_ref = LOADED_IMAGE

                def start(self):
                    raise RuntimeError("boot failed")

                def stop(self):
                    return None

            absent = subprocess.CompletedProcess([], 1, "", "")
            enough = SimpleNamespace(free=runtime.MIN_FREE_BYTES + 1)
            (state / "probe-attempt.json").unlink(missing_ok=True)
            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
                mock.patch.object(runtime, "REMOTE_IMAGE_LOCK", remote_lock),
                mock.patch.object(runtime, "_workflow_hashes", return_value=WORKFLOW),
                mock.patch.object(runtime.shutil, "disk_usage", return_value=enough),
                mock.patch("browser_runtime.BrowserRuntimeJob", FailingJob),
                mock.patch.object(runtime, "_docker", return_value=absent),
            ):
                receipt = runtime.remote_probe(handle)
                self.assertEqual(receipt["status"], "failed")
                with self.assertRaisesRegex(
                    runtime.RuntimeWorkflowError, "receipt already exists"
                ):
                    runtime.remote_probe(handle)

    def test_public_provision_and_probe_reject_consumed_handles_locally(self) -> None:
        handle = "cvmbr-" + "a" * 24
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            state.mkdir(parents=True)
            prepare = {
                "schema": runtime.PREPARE_SCHEMA,
                "handle": handle,
            }
            provision = {
                "schema": runtime.PROVISION_SCHEMA,
                "handle": handle,
            }
            (state / "prepare.json").write_text(json.dumps(prepare), encoding="ascii")
            (state / "provision.json").write_text(json.dumps(provision), encoding="ascii")
            runtime._write_json(
                state / "provision-attempt.json",
                {"schema": "test", "handle": handle},
            )
            runtime._write_json(
                state / "probe-attempt.json",
                {"schema": "test", "handle": handle},
            )
            with mock.patch.object(runtime, "STATE_ROOT", state_root):
                with self.assertRaisesRegex(
                    runtime.RuntimeWorkflowError, "receipt already exists"
                ):
                    runtime.provision(handle)
                with self.assertRaisesRegex(
                    runtime.RuntimeWorkflowError, "receipt already exists"
                ):
                    runtime.probe(handle)

    def test_repository_has_no_legacy_sidecar_workflow(self) -> None:
        root = Path(__file__).resolve().parents[3]
        self.assertFalse((root / "scripts/pilot/cvm_sidecar_probe.py").exists())
        self.assertFalse((root / "scripts/pilot/cvm-sidecar-probe.sh").exists())
        self.assertFalse((root / ".claude/skills/cvm-sidecar-probe").exists())
        self.assertFalse((root / "scripts/pilot/browser_gate_contract.py").exists())
        entrypoint = (
            root / "packages/agent_runtime/text-to-cad-agent-entrypoint"
        ).read_text(encoding="utf-8")
        self.assertNotIn("browser.sock", entrypoint)
        self.assertNotIn("agent-broker", entrypoint)
        self.assertNotIn("brokerAuthorityDigest", entrypoint)


if __name__ == "__main__":
    unittest.main()
