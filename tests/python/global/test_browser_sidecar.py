"""Public formal-pilot Browser Sidecar lifecycle tests."""

from __future__ import annotations

import base64
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import struct
import tempfile
import unittest
from unittest import mock

from scripts.pilot import browser_sidecar


IMAGE_ID = "sha256:071d17155480044647e94aee933200bff4fe8d4e8fcc92603828062a478537e5"
SOURCE_REVISION = "e465dc3659a08c45248fdb06ea0ab21397c6330f"
PROGRAMS = {
    "residual": "06d7fe1efae38aeeb7252a9f81683fc97b4b914d4a9fbd79169b2d58e95fa491",
    "viewer": "e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b",
}
NETWORK_ID = "a" * 64
CONTAINER_ID = "b" * 64
BROKER_CONTAINER_ID = "c" * 64


def packed_triangle() -> dict[str, object]:
    vertices = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    faces = struct.pack("<3I", 0, 1, 2)
    return {
        "encoding": "meshshot.packed-geometry/1",
        "vertexCount": 3,
        "faceCount": 1,
        "verticesData": base64.b64encode(vertices).decode("ascii"),
        "facesData": base64.b64encode(faces).decode("ascii"),
        "sha256": hashlib.sha256(
            b"meshshot.packed-geometry/1\0"
            + struct.pack("<II", 3, 1)
            + vertices
            + faces
        ).hexdigest(),
    }


def nested_gate_proof(
    *,
    job_id: str = "formal-job-1",
    nonce: str = "1" * 16,
    artifact_sha256: str = "2" * 64,
    surface_manifest_sha256: str = "3" * 64,
) -> dict[str, object]:
    """Return one independently fixed successful nested-gate proof."""

    return {
        "schema": "meshshot.browser-sidecar.nested-gate-proof/1",
        "status": "succeeded",
        "jobId": job_id,
        "nonce": nonce,
        "artifactSha256": artifact_sha256,
        "surfaceManifestSha256": surface_manifest_sha256,
        "predicates": {
            "publicResidualParity": True,
            "viewerProjectionChanged": True,
            "viewerArtifactClean": True,
            "browserInventoryEmpty": True,
            "browserProcessZero": True,
        },
        "residual": {
            "pngSha256": "b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b",
            "mode": "RGB",
            "size": [504, 1008],
            "profileSha256": "87da3cc3f625cb9c24f51bed41dcdc70402a4d461b2af29eaa19846b1e8f7241",
            "views": ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
        },
        "viewer": {
            "before": "Display and projection: Solid, Orthographic",
            "after": "Display and projection: Solid, Perspective",
            "bodyMentionsFixture": True,
            "bodyHasArtifactError": False,
        },
        "inventory": {
            "browserExecutables": [],
            "browserPackages": [],
            "browserCaches": [],
            "browserProcesses": [],
        },
    }


def configure_gate(job: browser_sidecar.BrowserSidecarJob) -> None:
    """Bind fixed test identities before the public job starts."""

    job.configure_nested_gate(
        artifact_sha256="2" * 64,
        surface_manifest_sha256="3" * 64,
    )


class BrowserSidecarJobTests(unittest.TestCase):
    def test_runtime_admission_caps_aggregate_container_memory(self) -> None:
        """Host slots reserve 2.25 GiB per job and fail closed when occupied."""

        gib = 1024**3
        self.assertEqual(browser_sidecar._browser_runtime_slot_count(16 * gib), 4)
        self.assertEqual(
            browser_sidecar._browser_runtime_slot_count(16_497_991_680),
            4,
        )
        self.assertEqual(browser_sidecar._browser_runtime_slot_count(10 * gib), 2)
        self.assertEqual(browser_sidecar._browser_runtime_slot_count(6 * gib), 0)
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            browser_sidecar,
            "BROWSER_RUNTIME_ADMISSION_ROOT",
            Path(temp) / "slots",
        ):
            first = browser_sidecar._acquire_browser_runtime_slot(
                lambda: False,
                total_memory_bytes=7 * gib,
            )
            try:
                with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                    browser_sidecar._acquire_browser_runtime_slot(
                        lambda: True,
                        total_memory_bytes=7 * gib,
                    )
                self.assertEqual(caught.exception.check, "runtime-admission")
            finally:
                first.close()
            slot_path = browser_sidecar.BROWSER_RUNTIME_ADMISSION_ROOT / "slot-0.lock"
            slot_path.chmod(0o666)
            with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                browser_sidecar._acquire_browser_runtime_slot(
                    lambda: False,
                    total_memory_bytes=7 * gib,
                )
            self.assertEqual(caught.exception.check, "runtime-admission")

    def test_geometry_enforces_package_owned_production_bounds(self) -> None:
        """The exact package bounds admit their edge and reject edge plus one."""

        vertices = [[0.0, 0.0, 0.0]] * browser_sidecar.MAX_GEOMETRY_VERTICES
        faces = [[0, 0, 0]] * browser_sidecar.MAX_GEOMETRY_FACES
        geometry = browser_sidecar._geometry(
            {"vertices": vertices, "faces": faces},
            "reference",
        )
        self.assertIs(geometry["vertices"], vertices)
        self.assertIs(geometry["faces"], faces)
        for label, payload in (
            (
                "vertices",
                {
                    "vertices": vertices + [[0.0, 0.0, 0.0]],
                    "faces": [[0, 0, 0]],
                },
            ),
            (
                "faces",
                {
                    "vertices": [[0.0, 0.0, 0.0]],
                    "faces": faces + [[0, 0, 0]],
                },
            ),
        ):
            with self.subTest(label=label), self.assertRaises(
                browser_sidecar.BrowserSidecarError
            ) as caught:
                browser_sidecar._geometry(payload, "reference")
            self.assertEqual(caught.exception.check, "reference-geometry")

    """Observe one complete exact-image lifecycle through its public adapter."""

    def test_broker_runtime_user_has_matching_private_home(self) -> None:
        with (
            mock.patch.object(browser_sidecar.os, "getuid", return_value=1234),
            mock.patch.object(browser_sidecar.os, "getgid", return_value=5678),
        ):
            self.assertEqual(
                browser_sidecar._broker_runtime_user_options(),
                (
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
                    "--tmpfs",
                    (
                        "/home/pwuser:rw,nosuid,nodev,size=16m,"
                        "uid=1234,gid=5678,mode=700"
                    ),
                    "--user",
                    "1234:5678",
                ),
            )

    def test_sidecar_endpoint_path_accepts_native_guid_without_url_escape(self) -> None:
        for accepted in (
            "/01234567-89ab-cdef-0123-456789abcdef",
            "/browser/session-token",
        ):
            self.assertTrue(browser_sidecar._valid_sidecar_endpoint_path(accepted))
        for rejected in (
            "/",
            "//peer",
            "/browser/",
            "/x?peer",
            "/x#peer",
            "x",
            "/./peer",
            "/a/../peer",
            "/..",
        ):
            self.assertFalse(browser_sidecar._valid_sidecar_endpoint_path(rejected))

    def test_broker_startup_failure_reports_only_closed_stage(self) -> None:
        output = StringIO()
        with mock.patch("sys.stdout", new=output):
            status = browser_sidecar._publish_broker_startup_failure(
                "formal-job-1", "isolation-preflight"
            )
        self.assertEqual(status, 1)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "event": "startup-failed",
                "schema": browser_sidecar.BROKER_SCHEMA,
                "jobId": "formal-job-1",
                "stage": "isolation-preflight",
            },
        )

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            job.docker = "/usr/bin/docker"
            job.broker_container_id = BROKER_CONTAINER_ID
            failure = {
                "event": "startup-failed",
                "schema": browser_sidecar.BROKER_SCHEMA,
                "jobId": "formal-job-1",
                "stage": "isolation-preflight",
            }

            def docker(*arguments, **_kwargs):
                if arguments[0] == "logs":
                    return subprocess.CompletedProcess(
                        arguments, 0, json.dumps(failure) + "\n", ""
                    )
                raise AssertionError(arguments)

            with mock.patch.object(job, "_docker", side_effect=docker):
                with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                    job._wait_broker_ready()
        self.assertEqual(caught.exception.check, "broker-startup-isolation-preflight")

        failure["stage"] = []
        with mock.patch.object(job, "_docker", side_effect=docker):
            with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                job._wait_broker_ready()
        self.assertEqual(caught.exception.check, "broker-readiness")

    def test_broker_startup_failure_survives_log_exit_race(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            job.docker = "/usr/bin/docker"
            job.broker_container_id = BROKER_CONTAINER_ID
            calls = 0

            def docker(*arguments, **_kwargs):
                nonlocal calls
                if arguments[0] == "logs":
                    calls += 1
                    output = ""
                    if calls == 2:
                        output = json.dumps(
                            {
                                "event": "startup-failed",
                                "schema": browser_sidecar.BROKER_SCHEMA,
                                "jobId": "formal-job-1",
                                "stage": "browser-connect",
                            }
                        ) + "\n"
                    return subprocess.CompletedProcess(arguments, 0, output, "")
                if arguments[:2] == ("container", "inspect"):
                    return subprocess.CompletedProcess(
                        arguments, 0, json.dumps({"Running": False}) + "\n", ""
                    )
                raise AssertionError(arguments)

            with mock.patch.object(job, "_docker", side_effect=docker):
                with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                    job._wait_broker_ready()
        self.assertEqual(calls, 2)
        self.assertEqual(caught.exception.check, "broker-startup-browser-connect")

    def test_broker_import_failure_emits_only_closed_stage(self) -> None:
        output = StringIO()
        with (
            mock.patch.object(
                browser_sidecar,
                "_load_sync_playwright",
                side_effect=OSError("sensitive path"),
            ),
            mock.patch("sys.stdout", new=output),
        ):
            status = browser_sidecar.run_broker(
                browser_sidecar.argparse.Namespace(job_id="formal-job-1")
            )
        self.assertEqual(status, 1)
        self.assertNotIn("sensitive", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["stage"], "playwright-import")

    def test_runtime_binding_accepts_only_current_two_image_provision(self) -> None:
        handle = "cvmsp-" + "1" * 24
        sidecar_runtime_id = "sha256:" + "a" * 64
        broker_runtime_id = "sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            state = repo / ".cvm-sidecar-probes" / handle
            state.mkdir(parents=True)
            receipt = {
                "schema": "cvm-sidecar.provision-receipt/1",
                "status": "provisioned",
                "handle": handle,
                "images": [
                    {
                        "role": "sidecar",
                        "id": browser_sidecar.IMAGE_ID,
                        "configSha256": browser_sidecar.IMAGE_ID.removeprefix("sha256:"),
                        "platform": "linux/amd64",
                        "sourceRevision": browser_sidecar.IMAGE_SOURCE_REVISION,
                        "archiveReference": f"text-to-cad-cvm-sidecar-sidecar:{handle}-{'c' * 32}",
                    },
                    {
                        "role": "broker",
                        "id": browser_sidecar.BROKER_IMAGE_ID,
                        "configSha256": browser_sidecar.BROKER_IMAGE_ID.removeprefix("sha256:"),
                        "platform": "linux/amd64",
                        "sourceRevision": browser_sidecar.BROKER_IMAGE_SOURCE_REVISION,
                        "archiveReference": f"text-to-cad-cvm-sidecar-broker:{handle}-{'c' * 32}",
                    },
                ],
                "retainedImageIds": [sidecar_runtime_id, broker_runtime_id],
                "transferCleanup": {
                    "archiveAbsent": True,
                    "prepareReceiptAbsent": True,
                    "incomingDirectoryAbsent": True,
                    "errors": [],
                },
                "retryAllowed": False,
            }
            (state / "provision.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            with mock.patch.object(browser_sidecar, "REPO_ROOT", repo):
                binding = browser_sidecar.resolve_runtime_image_binding(
                    {"TTC_BROWSER_RUNTIME_PROVISION_HANDLE": handle}
                )

        self.assertEqual(binding.sidecar_address, receipt["images"][0]["archiveReference"])
        self.assertEqual(binding.sidecar_runtime_id, sidecar_runtime_id)
        self.assertEqual(binding.broker_address, receipt["images"][1]["archiveReference"])
        self.assertEqual(binding.broker_runtime_id, broker_runtime_id)

        receipt["images"][1]["role"] = "client"
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            state = repo / ".cvm-sidecar-probes" / handle
            state.mkdir(parents=True)
            (state / "provision.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            with (
                mock.patch.object(browser_sidecar, "REPO_ROOT", repo),
                self.assertRaises(browser_sidecar.BrowserSidecarError) as raised,
            ):
                browser_sidecar.resolve_runtime_image_binding(
                    {"TTC_BROWSER_RUNTIME_PROVISION_HANDLE": handle}
                )
        self.assertEqual(raised.exception.check, "runtime-image-binding")

    def test_capability_layout_failure_is_terminal_and_released(self) -> None:
        """Partial pre-Docker construction still owns cleanup and a receipt."""

        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory(
            dir="/tmp"
        ) as temporary:
            exp_dir = Path(temp)
            capability = Path(temporary) / "capability"
            capability.mkdir(mode=0o700)
            with (
                mock.patch.object(
                    browser_sidecar.tempfile,
                    "mkdtemp",
                    return_value=os.fspath(capability),
                ),
                mock.patch.object(
                    browser_sidecar.Path,
                    "symlink_to",
                    side_effect=OSError("closed fixture"),
                ),
                self.assertRaises(browser_sidecar.BrowserSidecarError) as raised,
            ):
                browser_sidecar.BrowserSidecarJob.create(
                    exp_dir,
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )

            receipt = json.loads(
                (exp_dir / "run/browser-sidecar-receipt.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(raised.exception.check, "capability-layout")
        self.assertEqual(raised.exception.terminal_receipt, receipt)
        self.assertFalse(capability.exists())
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failureCheck"], "capability-layout")
        self.assertTrue(receipt["predicates"]["absenceProved"])

    def test_pre_resource_gate_identity_failure_truthfully_proves_absence(self) -> None:
        """A closed pre-create failure cannot imply an unproved retained resource."""

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            with self.assertRaises(browser_sidecar.BrowserSidecarError) as raised:
                job.start()
            receipt = job.close(workload_status=None)

        self.assertEqual(raised.exception.check, "nested-gate-identity")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failureCheck"], "nested-gate-identity")
        self.assertTrue(receipt["predicates"]["absenceProved"])

    def test_success_owns_one_sidecar_and_publishes_terminal_absence(self) -> None:
        calls: list[list[str]] = []
        broker_source: Path | None = None

        def docker(argv, **kwargs):
            nonlocal broker_source
            command = list(argv)
            calls.append(command)
            if command[1:4] == ["inspect", "--type=image", "--format"]:
                projection = command[4]
                is_broker = command[5] == browser_sidecar.BROKER_IMAGE_ID.removeprefix("sha256:")
                values = {
                    "{{.Id}}": browser_sidecar.BROKER_IMAGE_ID if is_broker else IMAGE_ID,
                    "{{.Os}}": "linux",
                    "{{.Architecture}}": "amd64",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': (
                        browser_sidecar.BROKER_IMAGE_SOURCE_REVISION if is_broker else SOURCE_REVISION
                    ),
                    '{{index .Config.Labels "io.text-to-cad.browser-sidecar-broker-base"}}': (
                        browser_sidecar.BROKER_BASE_IMAGE_ID
                    ),
                }
                return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
            if command[1:3] in (["container", "inspect"], ["network", "inspect"]):
                if "--format" in command and any(
                    resource in command
                    for resource in (NETWORK_ID, CONTAINER_ID, BROKER_CONTAINER_ID)
                ):
                    projection = command[-1]
                    if "browser-sidecar-job" in projection:
                        return subprocess.CompletedProcess(
                            command, 0, "formal-job-1\n", ""
                        )
                    if "browser-sidecar-owner" in projection:
                        return subprocess.CompletedProcess(command, 0, "1" * 16 + "\n", "")
                if (
                    (CONTAINER_ID in command or BROKER_CONTAINER_ID in command)
                    and "--format" in command
                ):
                    target = BROKER_CONTAINER_ID if BROKER_CONTAINER_ID in command else CONTAINER_ID
                    running = not any(call[1] == "stop" and target in call for call in calls)
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps({"Running": running, "ExitCode": 0}) + "\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 1, "", "not found")
            if command[1:3] == ["network", "create"]:
                return subprocess.CompletedProcess(command, 0, NETWORK_ID + "\n", "")
            if command[1] == "create":
                if browser_sidecar.BROKER_IMAGE_ID in command:
                    bind = next(value for value in command if value.startswith("type=bind,"))
                    broker_source = Path(bind.split("src=", 1)[1].split(",dst=", 1)[0])
                    return subprocess.CompletedProcess(command, 0, BROKER_CONTAINER_ID + "\n", "")
                return subprocess.CompletedProcess(command, 0, CONTAINER_ID + "\n", "")
            if command[1:3] == ["start", BROKER_CONTAINER_ID]:
                assert broker_source is not None
                created_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                created_socket.bind(str(broker_source / "browser.sock"))
                created_socket.close()
                (broker_source / "browser.sock").chmod(0o600)
                return subprocess.CompletedProcess(command, 0, BROKER_CONTAINER_ID + "\n", "")
            if command[1] == "logs":
                if BROKER_CONTAINER_ID in command:
                    records = [
                        {
                            "event": "ready",
                            "schema": "meshshot.browser-sidecar.broker/1",
                            "jobId": "formal-job-1",
                            "programs": PROGRAMS,
                            "isolation": {
                                "sourceAliasesVisible": [],
                                "externalEgressBlocked": True,
                                "browserPid": 321,
                            },
                        }
                    ]
                    if any(call[1] == "stop" and BROKER_CONTAINER_ID in call for call in calls):
                        records.append(
                            {
                                "event": "terminal",
                                "schema": "meshshot.browser-sidecar.broker/1",
                                "jobId": "formal-job-1",
                                "acceptedRequests": 2,
                                "freshContexts": 3,
                                "programCounts": {"residual": 1, "viewer": 1},
                                "programPredicates": {
                                    "residualEightView": True,
                                    "viewerProjectionChanged": True,
                                    "viewerArtifactClean": True,
                                },
                            }
                        )
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        "".join(json.dumps(record) + "\n" for record in records),
                        "",
                    )
                records = [
                    {
                        "event": "ready",
                        "jobId": "formal-job-1",
                        "endpointPath": "/browser/session-token",
                        "programs": PROGRAMS,
                    }
                ]
                if any(call[1] == "stop" for call in calls):
                    records.append(
                        {
                            "event": "closing",
                            "jobId": "formal-job-1",
                            "reason": "SIGTERM",
                        }
                    )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "".join(json.dumps(record) + "\n" for record in records),
                    "",
                )
            if command[1:3] in (
                ["container", "ls"],
                ["network", "ls"],
            ):
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory(
            dir="/tmp"
        ) as capability:
            exp_dir = Path(temp)
            with (
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.subprocess, "run", side_effect=docker),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 16),
                mock.patch.object(browser_sidecar.tempfile, "mkdtemp", return_value=capability),
            ):
                job = browser_sidecar.BrowserSidecarJob.create(
                    exp_dir,
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )
                job.gate_artifact_path.write_bytes(b"sealed-gate")
                job.gate_artifact_path.chmod(0o444)
                job.gate_input_path.write_bytes(b"sealed-input")
                job.gate_input_path.chmod(0o444)
                configure_gate(job)
                self.assertEqual(
                    job.public_socket_path.readlink(),
                    Path("broker") / "browser.sock",
                )
                self.assertTrue(job.gate_artifact_path.is_file())
                self.assertFalse((job.broker_capability_dir / "browser-gate.pyz").exists())
                self.assertFalse((job.broker_capability_dir / "gate-input.json").exists())
                authority_path = job.start()
                authority = json.loads(authority_path.read_text(encoding="utf-8"))
                job.record_nested_gate(nested_gate_proof(nonce=job.gate_nonce))
                receipt = job.close(workload_status=0)

        self.assertEqual(authority["schema"], "meshshot.browser-authority/1")
        self.assertEqual(authority["imageId"], IMAGE_ID)
        self.assertEqual(authority["programs"], PROGRAMS)
        self.assertEqual(
            set(authority),
            {"schema", "jobId", "gateNonce", "imageId", "programs"},
        )
        self.assertEqual(job.sandbox_authority_path, Path("/run/meshshot-browser/authority.json"))
        self.assertFalse(authority_path.parent.exists())
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["brokerImageId"], browser_sidecar.BROKER_IMAGE_ID)
        self.assertEqual(
            set(receipt),
            {
                "schema",
                "status",
                "imageId",
                "imageSourceRevision",
                "brokerImageId",
                "brokerImageSourceRevision",
                "brokerBaseImageId",
                "programs",
                "predicates",
                "counts",
                "failureCheck",
                "retryAllowed",
            },
        )
        self.assertEqual(
            receipt["counts"],
            {
                "acceptedRequests": 2,
                "freshContexts": 3,
                "programCounts": {"residual": 1, "viewer": 1},
            },
        )
        self.assertEqual(
            set(receipt["predicates"]),
            set(browser_sidecar.RECEIPT_PREDICATES),
        )
        self.assertTrue(all(receipt["predicates"].values()))
        self.assertNotIn("residualPublicParity", receipt["predicates"])
        self.assertIn("nestedPublicResidualParity", receipt["predicates"])
        self.assertIn("sidecarSourceHidden", receipt["predicates"])
        self.assertIn("sidecarEgressBlocked", receipt["predicates"])
        self.assertNotIn("brokerSourceHidden", receipt["predicates"])
        self.assertNotIn("brokerEgressBlocked", receipt["predicates"])
        self.assertFalse(any(name.startswith("nestedSource") for name in receipt["predicates"]))
        self.assertFalse(any(name.startswith("nestedEgress") for name in receipt["predicates"]))
        self.assertIsNone(receipt["failureCheck"])
        serialized = json.dumps(receipt, sort_keys=True)
        self.assertNotIn(NETWORK_ID, serialized)
        self.assertNotIn(CONTAINER_ID, serialized)
        self.assertNotIn(BROKER_CONTAINER_ID, serialized)
        for forbidden in ("Pid", "StartedAt", "FinishedAt", "stderr", "argv"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("jobId", receipt)
        creates = [command for command in calls if command[1] == "create"]
        self.assertEqual(len(creates), 2)
        sidecar_create, broker_create = creates
        self.assertFalse(any(command[1] == "run" for command in calls))
        self.assertIn("--pull=never", sidecar_create)
        self.assertIn("--read-only", sidecar_create)
        self.assertNotIn("--mount", sidecar_create)
        self.assertIn(IMAGE_ID, sidecar_create)
        self.assertIn("--pull=never", broker_create)
        self.assertIn("--read-only", broker_create)
        self.assertIn("--mount", broker_create)
        self.assertIn(browser_sidecar.BROKER_IMAGE_ID, broker_create)
        memory_index = broker_create.index("--memory")
        swap_index = broker_create.index("--memory-swap")
        self.assertEqual(broker_create[memory_index + 1], "768m")
        self.assertEqual(broker_create[swap_index + 1], "768m")
        broker_tmpfs = [
            broker_create[index + 1]
            for index, argument in enumerate(broker_create)
            if argument == "--tmpfs"
        ]
        self.assertEqual(
            broker_tmpfs,
            [
                "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
                (
                    "/home/pwuser:rw,nosuid,nodev,size=16m,"
                    f"uid={os.getuid()},gid={os.getgid()},mode=700"
                ),
            ],
        )
        self.assertEqual(
            broker_create[broker_create.index("--user") + 1],
            f"{os.getuid()}:{os.getgid()}",
        )
        broker_bind = next(
            value for value in broker_create if value.startswith("type=bind,")
        )
        self.assertIn(f"src={job.broker_capability_dir},", broker_bind)
        self.assertNotIn(f"src={job.capability_dir},", broker_bind)
        self.assertNotEqual(job.broker_capability_dir, job.capability_dir)
        starts = [command for command in calls if command[1] == "start"]
        self.assertEqual(starts, [["/usr/bin/docker", "start", CONTAINER_ID], ["/usr/bin/docker", "start", BROKER_CONTAINER_ID]])
        for container_id in (CONTAINER_ID, BROKER_CONTAINER_ID):
            verified_at = max(
                index
                for index, command in enumerate(calls)
                if command[1:3] == ["container", "inspect"]
                and container_id in command
                and "Labels" in command[-1]
            )
            started_at = next(
                index
                for index, command in enumerate(calls)
                if command[1:3] == ["start", container_id]
            )
            self.assertLess(verified_at, started_at)
        self.assertFalse(any(command[1] == "port" for command in calls))

    def test_cleanup_timeout_publishes_failure_and_continues_absence_proof(self) -> None:
        calls: list[list[str]] = []

        def docker(argv, **kwargs):
            command = list(argv)
            calls.append(command)
            if command[1] == "stop":
                raise subprocess.TimeoutExpired(command, 30)
            if command[1:3] == ["container", "inspect"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"Running": False, "ExitCode": 0}) + "\n",
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            job.docker = "/usr/bin/docker"
            job.container_id = CONTAINER_ID
            job.network_id = NETWORK_ID
            with mock.patch.object(browser_sidecar.subprocess, "run", side_effect=docker):
                receipt = job.close(workload_status=0)

        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failureCheck"], "sidecar-stop")
        self.assertTrue(receipt["predicates"]["absenceProved"])
        self.assertTrue(any(command[1:3] == ["container", "ls"] for command in calls))
        self.assertTrue(any(command[1:3] == ["network", "ls"] for command in calls))

    def test_nonzero_terminal_and_nonexact_closing_are_closed_failures(self) -> None:
        """Raw terminal success cannot hide either a nonzero exit or closing drift."""

        cases = (
            (9, {"event": "closing", "jobId": "formal-job-1", "reason": "SIGTERM"}, "sidecar-terminal"),
            (
                0,
                {
                    "event": "closing",
                    "jobId": "formal-job-1",
                    "reason": "SIGTERM",
                    "extra": True,
                },
                "sidecar-closing",
            ),
        )
        for exit_code, closing, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                def docker(argv, **kwargs):
                    command = list(argv)
                    if command[1] == "logs":
                        if BROKER_CONTAINER_ID in command:
                            terminal = {
                                "event": "terminal",
                                "schema": browser_sidecar.BROKER_SCHEMA,
                                "jobId": "formal-job-1",
                                "acceptedRequests": 2,
                                "freshContexts": 3,
                                "programCounts": {"residual": 1, "viewer": 1},
                                "programPredicates": {
                                    "residualEightView": True,
                                    "viewerProjectionChanged": True,
                                    "viewerArtifactClean": True,
                                },
                            }
                            return subprocess.CompletedProcess(
                                command, 0, json.dumps(terminal) + "\n", ""
                            )
                        return subprocess.CompletedProcess(
                            command, 0, json.dumps(closing) + "\n", ""
                        )
                    if command[1:3] == ["container", "inspect"]:
                        observed_exit = (
                            0 if BROKER_CONTAINER_ID in command else exit_code
                        )
                        return subprocess.CompletedProcess(
                            command,
                            0,
                            json.dumps(
                                {"Running": False, "ExitCode": observed_exit}
                            )
                            + "\n",
                            "",
                        )
                    return subprocess.CompletedProcess(command, 0, "", "")

                job = browser_sidecar.BrowserSidecarJob.create(
                    Path(temp),
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )
                job.docker = "/usr/bin/docker"
                job.container_id = CONTAINER_ID
                job.broker_container_id = BROKER_CONTAINER_ID
                job.readiness = {"ready": True}
                job.broker_readiness = {
                    "isolation": {
                        "sourceAliasesVisible": [],
                        "externalEgressBlocked": True,
                    }
                }
                job.socket_identity = (1, 1)
                job.configure_nested_gate(
                    artifact_sha256="2" * 64,
                    surface_manifest_sha256="3" * 64,
                )
                job.record_nested_gate(nested_gate_proof(nonce=job.gate_nonce))
                with mock.patch.object(
                    browser_sidecar.subprocess,
                    "run",
                    side_effect=docker,
                ):
                    receipt = job.close(workload_status=0)

            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["failureCheck"], expected)

    def test_public_job_requires_exact_broker_artifact_before_create(self) -> None:
        """The public job boundary attests both artifacts before resource creation."""

        calls: list[list[str]] = []

        def docker(argv, **kwargs):
            command = list(argv)
            calls.append(command)
            if command[1:4] == ["inspect", "--type=image", "--format"]:
                projection = command[4]
                address = command[5]
                if address == IMAGE_ID.removeprefix("sha256:"):
                    values = {
                        "{{.Id}}": IMAGE_ID,
                        "{{.Os}}": "linux",
                        "{{.Architecture}}": "amd64",
                        '{{index .Config.Labels "org.opencontainers.image.revision"}}': SOURCE_REVISION,
                    }
                    return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
                return subprocess.CompletedProcess(command, 1, "", "missing")
            if command[1:3] in (["container", "ls"], ["network", "ls"]):
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "not found")

        with tempfile.TemporaryDirectory() as temp:
            with (
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.subprocess, "run", side_effect=docker),
            ):
                job = browser_sidecar.BrowserSidecarJob.create(
                    Path(temp),
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )
                configure_gate(job)
                with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                    job.start()

        self.assertEqual(caught.exception.check, "broker-image-access")
        self.assertFalse(any(command[1:3] == ["network", "create"] for command in calls))
        self.assertFalse(any(command[1] == "run" for command in calls))

    def test_nested_gate_proof_is_bound_to_exact_job_and_fresh_nonce(self) -> None:
        """A proof from Job A or a wrong outer nonce cannot satisfy Job B."""

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-b",
            )
            job.configure_nested_gate(
                artifact_sha256="2" * 64,
                surface_manifest_sha256="3" * 64,
            )
            for proof in (
                nested_gate_proof(job_id="formal-job-a", nonce=job.gate_nonce),
                nested_gate_proof(job_id="formal-job-b", nonce="9" * 16),
            ):
                with self.subTest(proof=proof["jobId"], nonce=proof["nonce"]):
                    with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                        job.record_nested_gate(proof)
                    self.assertEqual(caught.exception.check, "nested-gate-proof")

    def test_nested_gate_validator_rejects_exact_artifact_and_surface_mismatch(self) -> None:
        """Each immutable proof digest is checked by the production job validator."""

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            configure_gate(job)
            mismatches = {
                "artifact": nested_gate_proof(
                    nonce=job.gate_nonce,
                    artifact_sha256="4" * 64,
                ),
                "surface": nested_gate_proof(
                    nonce=job.gate_nonce,
                    surface_manifest_sha256="5" * 64,
                ),
            }
            for label, proof in mismatches.items():
                with self.subTest(label=label), self.assertRaises(
                    browser_sidecar.BrowserSidecarError
                ) as caught:
                    job.record_nested_gate(proof)
                self.assertEqual(caught.exception.check, "nested-gate-proof")

    def test_public_job_uses_internal_broker_container_and_exact_ledger(self) -> None:
        """No host port/process is present in the successful public lifecycle."""

        self.assertRegex(browser_sidecar.BROKER_IMAGE_ID, r"sha256:[0-9a-f]{64}\Z")
        self.assertNotEqual(browser_sidecar.BROKER_IMAGE_ID, "sha256:" + "0" * 64)
        self.assertRegex(browser_sidecar.BROKER_IMAGE_SOURCE_REVISION, r"[0-9a-f]{40}\Z")
        source = Path(browser_sidecar.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"-p",', source)
        self.assertNotIn("subprocess.Popen(", source)

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )

        self.assertEqual(job.broker_container_name, f"{job.prefix}-broker")
        self.assertNotEqual(job.broker_container_name, job.container_name)

    def test_broker_artifact_contains_fixed_client_and_discovery_source(self) -> None:
        """The image seals both the released client and surface discovery role."""

        dockerfile = (
            browser_sidecar.REPO_ROOT
            / "packages/meshshot/browser_sidecar_broker/Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn(f"FROM {browser_sidecar.BROKER_BASE_IMAGE_ID}\n", dockerfile)
        self.assertNotIn("meshshot-sidecar-agent-client-prototype@", dockerfile)
        self.assertIn("-xtype l -delete", dockerfile)
        self.assertIn(
            "COPY packages/meshshot/src/meshshot ./packages/meshshot/src/meshshot",
            dockerfile,
        )
        self.assertIn(
            "COPY scripts/pilot/browser_sidecar_conformance.py "
            "./scripts/pilot/browser_sidecar_conformance.py",
            dockerfile,
        )
        for source in (
            "browser_sidecar_gate.py",
            "browser_gate_contract.py",
            "browser_surface.py",
        ):
            self.assertIn(
                f"COPY scripts/pilot/{source} ./scripts/pilot/{source}",
                dockerfile,
            )
        self.assertNotIn("COPY scripts/pilot/runner.py", dockerfile)
        dockerignore = (
            browser_sidecar.REPO_ROOT
            / "packages/meshshot/browser_sidecar_broker/Dockerfile.dockerignore"
        ).read_text(encoding="utf-8")
        self.assertTrue(dockerignore.startswith("**\n"))
        self.assertIn("!scripts/pilot/browser_sidecar.py\n", dockerignore)
        self.assertIn("!scripts/pilot/browser_sidecar_conformance.py\n", dockerignore)
        self.assertIn("!scripts/pilot/browser_sidecar_gate.py\n", dockerignore)
        self.assertIn("!scripts/pilot/browser_gate_contract.py\n", dockerignore)
        self.assertIn("!scripts/pilot/browser_surface.py\n", dockerignore)
        self.assertIn("!packages/meshshot/src/meshshot/**\n", dockerignore)
        self.assertTrue(dockerignore.endswith("**/*.pyc\n"))

    def test_package_owned_contract_matches_outer_lifecycle(self) -> None:
        """Generated/vendor isolation keeps one package-owned identity source."""

        contract = json.loads(
            (
                browser_sidecar.REPO_ROOT
                / "packages/meshshot/src/meshshot/browser_contract.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(contract["sidecarImageId"], browser_sidecar.IMAGE_ID)
        self.assertEqual(contract["programs"], browser_sidecar.PROGRAMS)
        self.assertEqual(
            contract["programs"]["residual"],
            hashlib.sha256(
                (
                    browser_sidecar.REPO_ROOT
                    / "packages/meshshot/src/meshshot/runtime/residual-render.js"
                ).read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(contract["authoritySchema"], browser_sidecar.AUTHORITY_SCHEMA)
        self.assertEqual(contract["requestSchema"], browser_sidecar.REQUEST_SCHEMA)
        self.assertEqual(contract["responseSchema"], browser_sidecar.RESPONSE_SCHEMA)
        self.assertEqual(contract["authorityPath"], "/run/meshshot-browser/authority.json")
        self.assertEqual(contract["socketPath"], "/run/meshshot-browser/browser.sock")
        self.assertEqual(contract["maxRequestBytes"], 48 * 1024 * 1024)
        self.assertEqual(contract["maxRequestBytes"], browser_sidecar.MAX_REQUEST_BYTES)
        self.assertEqual(contract["maxGeometryVertices"], 1_000_000)
        self.assertEqual(contract["maxGeometryFaces"], 400_000)
        self.assertEqual(
            contract["maxGeometryVertices"],
            browser_sidecar.MAX_GEOMETRY_VERTICES,
        )
        self.assertEqual(
            contract["maxGeometryFaces"],
            browser_sidecar.MAX_GEOMETRY_FACES,
        )
        package_data = (
            browser_sidecar.REPO_ROOT / "packages/meshshot/pyproject.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('"browser_contract.json"', package_data)

    def test_public_job_rejects_preexisting_capability_socket(self) -> None:
        """A pre-existing path is foreign state and is never unlinked or adopted."""

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            configure_gate(job)
            job.socket_path.write_text("foreign", encoding="utf-8")
            with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                job.start()
            self.assertEqual(job.socket_path.read_text(encoding="utf-8"), "foreign")

        self.assertEqual(caught.exception.check, "broker-socket-preexisting")

    def test_public_job_canonicalizes_capability_bind_source(self) -> None:
        """The Docker bind source uses the host-canonical path across adapters."""

        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            canonical = Path(temp) / "formal-capability"
            canonical.mkdir()
            returned = canonical.parent / "missing" / ".." / canonical.name
            with mock.patch.object(
                browser_sidecar.tempfile,
                "mkdtemp",
                return_value=os.fspath(returned),
            ):
                job = browser_sidecar.BrowserSidecarJob.create(
                    Path("/tmp/formal-exp"),
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )

        self.assertEqual(job.capability_dir, returned.resolve())

    def test_public_job_uses_exact_private_capability_parent(self) -> None:
        """A trusted host may place the random capability below one shared root."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / "daemon-shared"
            parent.mkdir(mode=0o700)
            job = browser_sidecar.BrowserSidecarJob.create(
                root / "exp",
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
                capability_parent=parent,
            )
            capability = job.capability_dir
            self.assertIsNotNone(capability)
            assert capability is not None
            self.assertEqual(capability.parent, parent.resolve())
            job.close(workload_status=None)
            self.assertFalse(capability.exists())

    def test_public_job_rejects_nonprivate_capability_parent(self) -> None:
        """A shared root with group or other access cannot hold formal authority."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / "shared"
            parent.mkdir(mode=0o755)
            exp_dir = root / "exp"
            with self.assertRaises(browser_sidecar.BrowserSidecarError) as raised:
                browser_sidecar.BrowserSidecarJob.create(
                    exp_dir,
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                    capability_parent=parent,
                )
            receipt = json.loads(
                (exp_dir / "run/browser-sidecar-receipt.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(raised.exception.check, "capability-layout")
        self.assertEqual(receipt["failureCheck"], "capability-layout")
        self.assertTrue(receipt["predicates"]["absenceProved"])

    def test_created_container_requires_exact_owner_labels_before_readiness(self) -> None:
        """A returned ID is untouched until exact labels prove cleanup authority."""

        calls: list[list[str]] = []

        def docker(argv, **kwargs):
            command = list(argv)
            calls.append(command)
            if command[1:4] == ["inspect", "--type=image", "--format"]:
                projection = command[4]
                broker = command[5] == browser_sidecar.BROKER_IMAGE_ID.removeprefix(
                    "sha256:"
                )
                values = {
                    "{{.Id}}": browser_sidecar.BROKER_IMAGE_ID if broker else IMAGE_ID,
                    "{{.Os}}": "linux",
                    "{{.Architecture}}": "amd64",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': (
                        browser_sidecar.BROKER_IMAGE_SOURCE_REVISION
                        if broker
                        else SOURCE_REVISION
                    ),
                    '{{index .Config.Labels "io.text-to-cad.browser-sidecar-broker-base"}}': (
                        browser_sidecar.BROKER_BASE_IMAGE_ID
                    ),
                }
                return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
            if command[1:3] == ["network", "create"]:
                return subprocess.CompletedProcess(command, 0, NETWORK_ID + "\n", "")
            if command[1:3] == ["network", "inspect"] and NETWORK_ID in command:
                projection = command[-1]
                values = {
                    '{{index .Labels "io.text-to-cad.browser-sidecar-job"}}': "formal-job-1",
                    '{{index .Labels "io.text-to-cad.browser-sidecar-owner"}}': "1" * 16,
                }
                return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
            if command[1:3] == ["container", "inspect"] and CONTAINER_ID in command:
                projection = command[-1]
                values = {
                    '{{index .Config.Labels "io.text-to-cad.browser-sidecar-job"}}': "formal-job-1",
                    '{{index .Config.Labels "io.text-to-cad.browser-sidecar-owner"}}': "foreign",
                    "{{json .State}}": json.dumps({"Running": False, "ExitCode": 1}),
                }
                return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
            if command[1:3] in (["container", "inspect"], ["network", "inspect"]):
                return subprocess.CompletedProcess(command, 1, "", "not found")
            if command[1] == "create":
                return subprocess.CompletedProcess(command, 0, CONTAINER_ID + "\n", "")
            if command[1:3] in (["container", "ls"], ["network", "ls"]):
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            with (
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.subprocess, "run", side_effect=docker),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 16),
            ):
                job = browser_sidecar.BrowserSidecarJob.create(
                    Path(temp),
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )
                configure_gate(job)
                with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                    job.start()
                receipt = job.close(workload_status=None)

        self.assertEqual(caught.exception.check, "container-owner-label")
        self.assertEqual(receipt["failureCheck"], "container-owner-label")
        self.assertFalse(any(call[1:4] == ["rm", "-f", CONTAINER_ID] for call in calls))
        self.assertFalse(any(call[1:3] == ["start", CONTAINER_ID] for call in calls))
        self.assertFalse(any(call[-1] == job.container_name and call[1] == "rm" for call in calls))

    def test_startup_signal_after_network_create_closes_exact_resources(self) -> None:
        """Cancellation at a startup boundary enters the same terminal cleanup."""

        calls: list[list[str]] = []
        cancelled = False

        def docker(argv, **kwargs):
            nonlocal cancelled
            command = list(argv)
            calls.append(command)
            if command[1:4] == ["inspect", "--type=image", "--format"]:
                projection = command[4]
                is_broker = command[5] == browser_sidecar.BROKER_IMAGE_ID.removeprefix("sha256:")
                values = {
                    "{{.Id}}": browser_sidecar.BROKER_IMAGE_ID if is_broker else IMAGE_ID,
                    "{{.Os}}": "linux",
                    "{{.Architecture}}": "amd64",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': (
                        browser_sidecar.BROKER_IMAGE_SOURCE_REVISION if is_broker else SOURCE_REVISION
                    ),
                    '{{index .Config.Labels "io.text-to-cad.browser-sidecar-broker-base"}}': browser_sidecar.BROKER_BASE_IMAGE_ID,
                }
                return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
            if command[1:3] == ["network", "inspect"] and NETWORK_ID in command:
                projection = command[-1]
                values = {
                    '{{index .Labels "io.text-to-cad.browser-sidecar-job"}}': "formal-job-1",
                    '{{index .Labels "io.text-to-cad.browser-sidecar-owner"}}': "1" * 16,
                }
                return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
            if command[1:3] in (["container", "inspect"], ["network", "inspect"]):
                return subprocess.CompletedProcess(command, 1, "", "not found")
            if command[1:3] == ["network", "create"]:
                cancelled = True
                return subprocess.CompletedProcess(command, 0, NETWORK_ID + "\n", "")
            if command[1:3] in (["container", "ls"], ["network", "ls"]):
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            with (
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.subprocess, "run", side_effect=docker),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 16),
            ):
                job = browser_sidecar.BrowserSidecarJob.create(
                    Path(temp),
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                    cancelled=lambda: cancelled,
                )
                configure_gate(job)
                with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                    job.start()
                receipt = job.close(workload_status=None)

        self.assertEqual(caught.exception.check, "startup-signal")
        self.assertEqual(receipt["failureCheck"], "startup-signal")
        self.assertTrue(receipt["predicates"]["absenceProved"])
        self.assertFalse(any(command[1] == "run" for command in calls))
        self.assertTrue(any(command[1:3] == ["network", "rm"] for command in calls))

    def test_cleanup_failure_precedence_and_retained_override(self) -> None:
        """First cleanup failure is stable, except positive retention dominates."""

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            job.docker = "/usr/bin/docker"
            job.cleanup_errors.extend(["sidecar-stop", "network-remove"])
            with mock.patch.object(
                browser_sidecar.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                first = job.close(workload_status=0)
        self.assertEqual(first["failureCheck"], "sidecar-stop")

        with tempfile.TemporaryDirectory() as temp:
            signal_job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            signal_job.first_error = "startup-signal"
            signal_job.cleanup_errors.append("sidecar-stop")
            signal_receipt = signal_job.close(workload_status=None)
        self.assertEqual(signal_receipt["failureCheck"], "startup-signal")

        with tempfile.TemporaryDirectory() as temp:
            retained_job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            retained_job.docker = "/usr/bin/docker"
            retained_job.cleanup_errors.extend(["sidecar-stop", "network-remove"])
            with mock.patch.object(
                retained_job,
                "_prove_absence",
                return_value={
                    "containers": ["retained"],
                    "networks": [],
                    "errors": [],
                    "proved": False,
                },
            ):
                retained = retained_job.close(workload_status=0)
        self.assertEqual(retained["failureCheck"], "retained-resource")

    def test_success_requires_both_registered_programs(self) -> None:
        """A residual-only terminal record cannot produce a successful receipt."""

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob.create(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            job.docker = "/usr/bin/docker"
            job.readiness = {"ready": True}
            job.broker_readiness = {
                "isolation": {
                    "sourceAliasesVisible": [],
                    "externalEgressBlocked": True,
                }
            }
            job.socket_identity = (1, 1)
            job.broker_terminal = {
                "acceptedRequests": 1,
                "freshContexts": 2,
                "programCounts": {"residual": 1, "viewer": 0},
                "programPredicates": {
                    "residualEightView": True,
                    "viewerProjectionChanged": False,
                    "viewerArtifactClean": False,
                },
            }
            job.configure_nested_gate(
                artifact_sha256="2" * 64,
                surface_manifest_sha256="3" * 64,
            )
            job.record_nested_gate(nested_gate_proof(nonce=job.gate_nonce))
            with mock.patch.object(
                browser_sidecar.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                receipt = job.close(workload_status=0)

        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failureCheck"], "viewer-required")
        self.assertFalse(receipt["predicates"]["brokerViewerAccepted"])


class RegisteredProgramBrokerTests(unittest.TestCase):
    """Observe exact request validation and fresh contexts at the broker seam."""

    def test_residual_program_uses_one_fresh_context_and_rejects_extra_keys(self) -> None:
        view_names = ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"]

        class FakePage:
            reject_packed_input = False

            def __init__(self) -> None:
                self.goto_calls: list[tuple[str, dict[str, object]]] = []

            def goto(self, url, **kwargs):
                self.goto_calls.append((url, kwargs))

            def wait_for_function(self, expression, **kwargs):
                self.wait_expression = expression

            def evaluate(self, expression, payload):
                self.expression = expression
                self.payload = payload
                if self.reject_packed_input:
                    raise RuntimeError("browser rejected input")
                return {
                    "ok": True,
                    "pngDataUrl": "data:image/png;base64,aGVsbG8=",
                    "views": [{"name": name} for name in view_names],
                }

        class FakeContext:
            def __init__(self) -> None:
                self.page = FakePage()
                self.closed = False

            def new_page(self):
                return self.page

            def close(self):
                self.closed = True

        class FakeBrowser:
            def __init__(self) -> None:
                self.contexts: list[FakeContext] = []

            def new_context(self, **kwargs):
                self.context_kwargs = kwargs
                context = FakeContext()
                self.contexts.append(context)
                return context

        request = {
            "schema": "meshshot.browser-sidecar.render-request/3",
            "jobId": "formal-job-1",
            "imageId": IMAGE_ID,
            "program": "residual",
            "payload": {
                "reference": packed_triangle(),
                "candidate": packed_triangle(),
                "variant": "step",
                "exteriorDirections": [],
                "options": {
                    "cameraPolicy": "profile-fixed",
                    "canonicalPostprocess": True,
                },
            },
        }
        browser = FakeBrowser()
        broker = browser_sidecar.RegisteredProgramBroker(browser, "formal-job-1")

        response = broker.execute(request)

        self.assertEqual(response["schema"], "meshshot.browser-sidecar.render-response/1")
        self.assertEqual(response["result"]["views"], [{"name": name} for name in view_names])
        self.assertEqual(len(browser.contexts), 1)
        self.assertTrue(browser.contexts[0].closed)
        self.assertEqual(
            browser.contexts[0].page.goto_calls[0][0],
            "http://127.0.0.1:4174/render.html",
        )
        self.assertEqual(
            browser.contexts[0].page.payload["payload"], request["payload"]
        )

        malformed = dict(request)
        malformed["url"] = "https://attacker.invalid/"
        with self.assertRaisesRegex(browser_sidecar.BrowserSidecarError, "schema"):
            broker.execute(malformed)
        self.assertEqual(len(browser.contexts), 1)

        malformed_buffer = json.loads(json.dumps(request))
        malformed_buffer["payload"]["reference"]["verticesData"] = "AAAA"
        with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
            broker.execute(malformed_buffer)
        self.assertEqual(caught.exception.check, "reference-geometry")
        self.assertEqual(len(browser.contexts), 1)

        FakePage.reject_packed_input = True
        with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
            broker.execute(request)
        self.assertEqual(caught.exception.check, "residual-input")
        self.assertTrue(browser.contexts[-1].closed)

    def test_viewer_program_uses_real_registered_projection_control(self) -> None:
        class FakeKeyboard:
            def __init__(self) -> None:
                self.keys: list[str] = []

            def press(self, key):
                self.keys.append(key)

        class FakeLocator:
            def inner_text(self):
                return "browser_sidecar_inspection.step\nLoaded"

        class FakePage:
            def __init__(self) -> None:
                self.keyboard = FakeKeyboard()
                self.evaluations = 0

            def goto(self, url, **kwargs):
                self.goto_url = url

            def wait_for_timeout(self, timeout):
                self.wait_timeout = timeout

            def evaluate(self, expression, argument=None):
                self.evaluations += 1
                return {
                    1: "Display and projection: Solid, Orthographic",
                    2: None,
                    3: "Display and projection: Solid, Perspective",
                }[self.evaluations]

            def screenshot(self, **kwargs):
                return b"registered-viewer-png"

            def title(self):
                return "CAD Viewer | browser_sidecar_inspection.step"

            def locator(self, selector):
                self.selector = selector
                return FakeLocator()

        class FakeContext:
            def __init__(self) -> None:
                self.page = FakePage()
                self.closed = False

            def new_page(self):
                return self.page

            def close(self):
                self.closed = True

        class FakeBrowser:
            def __init__(self) -> None:
                self.contexts: list[FakeContext] = []

            def new_context(self, **kwargs):
                context = FakeContext()
                self.contexts.append(context)
                return context

        browser = FakeBrowser()
        broker = browser_sidecar.RegisteredProgramBroker(browser, "formal-job-1")

        response = broker.execute(
            {
                "schema": "meshshot.browser-sidecar.render-request/3",
                "jobId": "formal-job-1",
                "imageId": IMAGE_ID,
                "program": "viewer",
                "payload": {
                    "modelKey": "inspection-step",
                    "inspectionControl": "toggle-projection",
                },
            }
        )

        self.assertEqual(response["program"], "viewer")
        self.assertIs(response["result"]["bodyMentionsFixture"], True)
        self.assertIs(response["result"]["bodyHasArtifactError"], False)
        self.assertEqual(
            response["result"]["inspection"],
            {
                "control": "toggle-projection",
                "before": "Display and projection: Solid, Orthographic",
                "target": "Perspective",
                "after": "Display and projection: Solid, Perspective",
                "changed": True,
            },
        )
        self.assertTrue(response["result"]["screenshotDataUrl"].startswith("data:image/png;base64,"))
        self.assertEqual(browser.contexts[0].page.keyboard.keys, ["Enter", "Enter"])
        self.assertTrue(browser.contexts[0].closed)

    def test_preflight_requires_no_source_alias_and_blocked_browser_egress(self) -> None:
        authority = {
            "schema": "meshshot.browser-sidecar.prototype/1",
            "jobId": "formal-job-1",
            "endpointPath": "/browser/token",
            "browserPid": 321,
            "chromiumRevision": "1223",
            "chromiumVersion": "148.0.7778.96",
            "playwrightVersion": "1.60.0",
            "programs": PROGRAMS,
            "sourceAliasesVisible": [],
        }
        preflight_result = [{"externalEgressBlocked": True}]

        class FakePage:
            def goto(self, url, **kwargs):
                self.url = url

            def evaluate(self, expression, argument=None):
                self.expression = expression
                return preflight_result[0]

        class FakeContext:
            def __init__(self) -> None:
                self.page = FakePage()
                self.closed = False

            def new_page(self):
                return self.page

            def close(self):
                self.closed = True

        class FakeBrowser:
            def __init__(self) -> None:
                self.contexts: list[FakeContext] = []

            def new_context(self, **kwargs):
                context = FakeContext()
                self.contexts.append(context)
                return context

        browser = FakeBrowser()
        broker = browser_sidecar.RegisteredProgramBroker(browser, "formal-job-1")

        receipt = broker.preflight(authority)

        self.assertEqual(
            receipt,
            {
                "sourceAliasesVisible": [],
                "externalEgressBlocked": True,
                "browserPid": 321,
            },
        )
        self.assertTrue(browser.contexts[0].closed)
        self.assertNotIn("3001/v1/authority", browser.contexts[0].page.expression)

        invalid_authority = dict(authority)
        invalid_authority["sourceAliasesVisible"] = ["/workspace"]
        with self.assertRaisesRegex(browser_sidecar.BrowserSidecarError, "isolation"):
            broker.preflight(invalid_authority)


if __name__ == "__main__":
    unittest.main()
