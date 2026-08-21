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


class BrowserRuntimeWorkflowTests(unittest.TestCase):
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

    def test_transfer_destination_is_home_relative_and_shell_free(self) -> None:
        handle = "cvmbr-" + "1" * 24
        self.assertEqual(
            runtime._remote_transfer_destination(handle),
            f"cvm:text-to-cad/.cvm-browser-runtime/{handle}/incoming/",
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

    def test_remote_probe_uses_production_browser_runtime_job_and_cleans_up(self) -> None:
        handle = "cvmbr-" + "4" * 24
        owner = "5" * 32
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp) / ".cvm-browser-runtime"
            state = state_root / handle
            state.mkdir(parents=True)
            provision = {
                "schema": runtime.PROVISION_SCHEMA,
                "status": "provisioned",
                "handle": handle,
                "ownerNonce": owner,
                "image": {"role": "runtime"},
                "retainedImageId": LOADED_IMAGE,
                "archiveSha256": "6" * 64,
                "workflowFiles": WORKFLOW,
                "freeBytes": runtime.MIN_FREE_BYTES,
                "transferAbsent": True,
                "retryAllowed": False,
            }
            (state / "provision.json").write_text(json.dumps(provision), encoding="ascii")

            class FakeJob:
                container_name = "fixed-container"
                network_name = "fixed-network"

                def __init__(self, owner_nonce, capability_dir, image_ref):
                    self.capability_dir = Path(capability_dir)
                    self.owner_nonce = owner_nonce
                    self.image_ref = image_ref

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
                                "programDigest": "sha256:" + "7" * 64,
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

            provision = {
                "schema": runtime.PROVISION_SCHEMA,
                "status": "provisioned",
                "handle": handle,
                "ownerNonce": owner,
                "image": {"role": "runtime"},
                "retainedImageId": LOADED_IMAGE,
                "archiveSha256": "7" * 64,
                "workflowFiles": WORKFLOW,
                "freeBytes": runtime.MIN_FREE_BYTES,
                "transferAbsent": True,
                "retryAllowed": False,
            }
            (state / "provision.json").unlink()
            (state / "provision.json").write_text(json.dumps(provision), encoding="ascii")

            class FailingJob:
                container_name = "failed-container"
                network_name = "failed-network"

                def __init__(self, owner_nonce, capability_dir, image_ref):
                    self.capability_dir = Path(capability_dir)

                def start(self):
                    raise RuntimeError("boot failed")

                def stop(self):
                    return None

            absent = subprocess.CompletedProcess([], 1, "", "")
            enough = SimpleNamespace(free=runtime.MIN_FREE_BYTES + 1)
            (state / "probe-attempt.json").unlink(missing_ok=True)
            with (
                mock.patch.object(runtime, "STATE_ROOT", state_root),
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
