"""Focused tests for the simple CVM Browser Runtime installer."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.pilot import cvm_browser_runtime as runtime


SOURCE_ID = "sha256:" + "a" * 64
HOST_ID = "sha256:" + "b" * 64
REVISION = "c" * 40
TRANSPORT = "text-to-cad-browser-runtime-transfer:" + "d" * 24


def source_lock(image_id: str = SOURCE_ID):
    return {
        "schema_version": 1,
        "image": {
            "name": "text-to-cad-browser-runtime",
            "id": image_id,
            "base_image": "fixed",
            "base_id": "sha256:" + "e" * 64,
            "playwright_mcp_version": "fixed",
            "content_size_bytes": 100,
            "architecture": "amd64",
        },
        "built_from_ref": REVISION,
        "notes": "exact ID only",
    }


class BrowserRuntimeInstallerTests(unittest.TestCase):
    def test_public_receipt_validators_reject_extra_or_mismatched_fields(self) -> None:
        install = {
            "schema": "cvm-browser-runtime.install/1",
            "status": "installed",
            "sourceImageId": SOURCE_ID,
            "imageId": HOST_ID,
            "sourceRevision": REVISION,
            "platform": "linux/amd64",
            "retentionReference": runtime._retention_reference(HOST_ID),
            "archiveSha256": "f" * 64,
            "hostLockSha256": "1" * 64,
            "transportAbsent": True,
        }
        expected_lock = source_lock()
        expected_lock["image"] = dict(expected_lock["image"], id=HOST_ID)
        expected_lock["host"] = {
            "sourceImageId": SOURCE_ID,
            "retentionReference": runtime._retention_reference(HOST_ID),
            "archiveSha256": "f" * 64,
        }
        install["hostLockSha256"] = runtime._sha256_bytes(
            runtime._canonical(expected_lock)
        )
        with mock.patch.object(runtime, "_source_lock", return_value=source_lock()):
            runtime._validate_install_receipt(
                install, REVISION, SOURCE_ID, "f" * 64
            )
        install["extra"] = True
        with (
            mock.patch.object(runtime, "_source_lock", return_value=source_lock()),
            self.assertRaisesRegex(runtime.RuntimeWorkflowError, "invalid"),
        ):
            runtime._validate_install_receipt(install, REVISION, SOURCE_ID, "f" * 64)
        install.pop("extra")
        install["hostLockSha256"] = "3" * 64
        with (
            mock.patch.object(runtime, "_source_lock", return_value=source_lock()),
            self.assertRaisesRegex(runtime.RuntimeWorkflowError, "invalid"),
        ):
            runtime._validate_install_receipt(install, REVISION, SOURCE_ID, "f" * 64)

        probe = {
            "schema": "cvm-browser-runtime.probe/2",
            "status": "succeeded",
            "imageId": HOST_ID,
            "programDigest": runtime.CAD_RENDER_PROGRAMS["residual"],
            "pngSha256": "sha256:" + "2" * 64,
            "capabilitySchema": "text-to-cad.browser-runtime-capability/1",
            "cleanupAbsent": True,
        }
        runtime._validate_probe_receipt(probe, HOST_ID)
        probe["imageId"] = SOURCE_ID
        with self.assertRaisesRegex(runtime.RuntimeWorkflowError, "invalid"):
            runtime._validate_probe_receipt(probe, HOST_ID)

    def test_atomic_replace_does_not_clean_temp_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "lock.json"
            with mock.patch.object(
                Path, "unlink", side_effect=OSError("must not run after commit")
            ):
                runtime._replace_json(target, {"fixed": True})
            self.assertEqual(
                json.loads(target.read_text(encoding="ascii")), {"fixed": True}
            )

    def test_public_interface_is_install_probe_status_only(self) -> None:
        self.assertEqual(
            runtime.parse_args(
                [
                    "install",
                    "--source-revision",
                    REVISION,
                    "--runtime-image",
                    SOURCE_ID,
                ]
            ).operation,
            "install",
        )
        for retired in ("prepare", "provision", "abort"):
            with self.assertRaises(SystemExit):
                runtime.parse_args([retired])

    def test_retention_reference_is_derived_from_exact_host_id(self) -> None:
        self.assertEqual(
            runtime._retention_reference(HOST_ID),
            "text-to-cad-browser-runtime-retained:" + "b" * 64,
        )

    def test_remote_install_writes_host_lock_and_keeps_retention_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            host_lock = state / "image-lock.json"
            source_path = root / "source-lock.json"
            source_path.write_text(json.dumps(source_lock()), encoding="ascii")
            archive = root / "archive.tar"
            archive.write_bytes(b"fixed")
            refs: dict[str, tuple[str, ...]] = {}

            def docker(*args, **kwargs):
                if args[:2] == ("image", "load"):
                    refs[TRANSPORT] = (HOST_ID,)
                elif args[:2] == ("image", "tag"):
                    refs[args[3]] = (args[2],)
                elif args[:2] == ("image", "rm"):
                    refs.pop(args[2], None)
                return subprocess.CompletedProcess(args, 0, "", "")

            def inspect(image_id):
                return {
                    "id": image_id,
                    "platform": "linux/amd64",
                    "sourceRevision": REVISION,
                }

            with (
                mock.patch.object(runtime, "STATE_ROOT", state),
                mock.patch.object(runtime, "HOST_IMAGE_LOCK_PATH", host_lock),
                mock.patch.object(runtime, "SOURCE_IMAGE_LOCK", source_path),
                mock.patch.object(runtime, "_read_archive", return_value=archive),
                mock.patch.object(runtime, "_docker", side_effect=docker),
                mock.patch.object(
                    runtime, "_image_ids", side_effect=lambda ref: refs.get(ref, ())
                ),
                mock.patch.object(runtime, "_inspect_image", side_effect=inspect),
            ):
                receipt = runtime.remote_install(
                    REVISION, SOURCE_ID, TRANSPORT, 5, "f" * 64
                )

            retention = runtime._retention_reference(HOST_ID)
            self.assertEqual(receipt["status"], "installed")
            self.assertEqual(receipt["imageId"], HOST_ID)
            self.assertEqual(refs, {retention: (HOST_ID,)})
            self.assertEqual(
                json.loads(host_lock.read_text(encoding="ascii"))["image"]["id"],
                HOST_ID,
            )
            self.assertFalse(archive.exists())

    def test_remote_install_final_disk_gate_fails_before_host_lock_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            host_lock = state / "image-lock.json"
            source_path = root / "source-lock.json"
            source_path.write_text(json.dumps(source_lock()), encoding="ascii")
            archive = root / "archive.tar"
            archive.write_bytes(b"fixed")
            refs = {}

            def docker(*args, **kwargs):
                if args[:2] == ("image", "load"):
                    refs[TRANSPORT] = (HOST_ID,)
                elif args[:2] == ("image", "tag"):
                    refs[args[3]] = (args[2],)
                elif args[:2] == ("image", "rm"):
                    refs.pop(args[2], None)
                return subprocess.CompletedProcess(args, 0, "", "")

            inspected = {
                "id": HOST_ID,
                "platform": "linux/amd64",
                "sourceRevision": REVISION,
            }
            with (
                mock.patch.object(runtime, "STATE_ROOT", state),
                mock.patch.object(runtime, "HOST_IMAGE_LOCK_PATH", host_lock),
                mock.patch.object(runtime, "PROBE_RECEIPT", state / "probe.json"),
                mock.patch.object(runtime, "SOURCE_IMAGE_LOCK", source_path),
                mock.patch.object(runtime, "_read_archive", return_value=archive),
                mock.patch.object(runtime, "_docker", side_effect=docker),
                mock.patch.object(
                    runtime, "_image_ids", side_effect=lambda ref: refs.get(ref, ())
                ),
                mock.patch.object(runtime, "_inspect_image", return_value=inspected),
                mock.patch.object(
                    runtime.shutil,
                    "disk_usage",
                    return_value=type("Disk", (), {"free": runtime.MIN_FREE_BYTES - 1})(),
                ),
                self.assertRaisesRegex(runtime.RuntimeWorkflowError, "final disk gate"),
            ):
                runtime.remote_install(
                    REVISION, SOURCE_ID, TRANSPORT, 5, "f" * 64
                )
            self.assertFalse(host_lock.exists())
            self.assertEqual(refs, {})

    def test_source_lock_is_not_a_runtime_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "source-lock.json"
            source_path.write_text(json.dumps(source_lock()), encoding="ascii")
            with mock.patch.object(runtime, "SOURCE_IMAGE_LOCK", source_path):
                with self.assertRaisesRegex(runtime.RuntimeWorkflowError, "does not match"):
                    runtime._source_lock(REVISION, HOST_ID)

    def test_status_reads_only_host_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            host_lock = state / "image-lock.json"
            probe = state / "probe.json"
            host_lock.write_text(json.dumps(source_lock(HOST_ID)), encoding="ascii")
            with (
                mock.patch.object(runtime, "HOST_IMAGE_LOCK_PATH", host_lock),
                mock.patch.object(runtime, "PROBE_RECEIPT", probe),
            ):
                observed = runtime.status()
            self.assertEqual(observed["status"], "observed")
            self.assertEqual(observed["hostLock"]["image"]["id"], HOST_ID)
            self.assertNotIn("probe", observed)

    def test_runner_uses_host_lock_not_repository_lock(self) -> None:
        root = Path(__file__).resolve().parents[3]
        runner = (root / "scripts/pilot/runner.py").read_text(encoding="utf-8")
        self.assertIn("image_lock_path=HOST_IMAGE_LOCK_PATH", runner)
        self.assertNotIn("cvmbr-", runner)

    def test_repository_has_no_retired_workflow_commands(self) -> None:
        root = Path(__file__).resolve().parents[3]
        module = (root / "scripts/pilot/cvm_browser_runtime.py").read_text(
            encoding="utf-8"
        )
        for retired in (
            "PREPARE_SCHEMA",
            "PROVISION_SCHEMA",
            "remote_begin",
            "remote_provision",
            "remote_abort",
            "probe-attempt",
        ):
            self.assertNotIn(retired, module)


if __name__ == "__main__":
    unittest.main()
