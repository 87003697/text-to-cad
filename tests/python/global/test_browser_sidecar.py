"""Public formal-pilot Browser Sidecar lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.pilot import browser_sidecar


IMAGE_ID = "sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1"
SOURCE_REVISION = "1abe4c97929906b5c0b28b0f3f38857bd923952f"
PROGRAMS = {
    "residual": "d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b",
    "viewer": "e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b",
}
NETWORK_ID = "a" * 64
CONTAINER_ID = "b" * 64
BROKER_CONTAINER_ID = "c" * 64


class BrowserSidecarJobTests(unittest.TestCase):
    """Observe one complete exact-image lifecycle through its public adapter."""

    def test_success_owns_one_sidecar_and_publishes_terminal_absence(self) -> None:
        calls: list[list[str]] = []

        def docker(argv, **kwargs):
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
            if command[1] == "run":
                if browser_sidecar.BROKER_IMAGE_ID in command:
                    bind = next(value for value in command if value.startswith("type=bind,"))
                    source = Path(bind.split("src=", 1)[1].split(",dst=", 1)[0])
                    created_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    created_socket.bind(str(source / "browser-sidecar.sock"))
                    created_socket.close()
                    (source / "browser-sidecar.sock").chmod(0o600)
                    return subprocess.CompletedProcess(command, 0, BROKER_CONTAINER_ID + "\n", "")
                return subprocess.CompletedProcess(command, 0, CONTAINER_ID + "\n", "")
            if command[1] == "logs":
                if BROKER_CONTAINER_ID in command:
                    records = [
                        {
                            "event": "ready",
                            "schema": "meshshot.browser-sidecar.broker/1",
                            "jobId": "formal-job-1",
                            "imageId": IMAGE_ID,
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
                                "imageId": IMAGE_ID,
                                "acceptedRequests": 2,
                                "freshContexts": 3,
                                "programCounts": {"residual": 1, "viewer": 1},
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
                job = browser_sidecar.BrowserSidecarJob(
                    exp_dir,
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )
                authority_path = job.start()
                authority = json.loads(authority_path.read_text(encoding="utf-8"))
                receipt = job.close(workload_status=0)

        self.assertEqual(authority["schema"], "meshshot.browser-authority/1")
        self.assertEqual(authority["imageId"], IMAGE_ID)
        self.assertEqual(authority["programs"], PROGRAMS)
        self.assertEqual(
            authority["socketPath"],
            "/run/meshshot-browser/browser-sidecar.sock",
        )
        self.assertEqual(job.sandbox_authority_path, Path("/run/meshshot-browser/authority.json"))
        self.assertFalse(authority_path.parent.exists())
        self.assertEqual(receipt["status"], "succeeded")
        self.assertTrue(receipt["absenceProof"]["proved"])
        self.assertEqual(receipt["cleanupErrors"], [])
        self.assertEqual(receipt["brokerImageId"], browser_sidecar.BROKER_IMAGE_ID)
        self.assertEqual(receipt["ownedResources"]["broker"]["id"], BROKER_CONTAINER_ID)
        self.assertEqual(receipt["terminal"]["closingObserved"], True)
        self.assertEqual(
            receipt["brokerTerminal"],
            {
                "event": "terminal",
                "schema": "meshshot.browser-sidecar.broker/1",
                "jobId": "formal-job-1",
                "imageId": IMAGE_ID,
                "acceptedRequests": 2,
                "freshContexts": 3,
                "programCounts": {"residual": 1, "viewer": 1},
            },
        )
        runs = [command for command in calls if command[1] == "run"]
        self.assertEqual(len(runs), 2)
        sidecar_run, broker_run = runs
        self.assertIn("--pull=never", sidecar_run)
        self.assertIn("--read-only", sidecar_run)
        self.assertNotIn("--mount", sidecar_run)
        self.assertIn(IMAGE_ID, sidecar_run)
        self.assertIn("--pull=never", broker_run)
        self.assertIn("--read-only", broker_run)
        self.assertIn("--mount", broker_run)
        self.assertIn(browser_sidecar.BROKER_IMAGE_ID, broker_run)
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
            job = browser_sidecar.BrowserSidecarJob(
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
        self.assertIn("sidecar-stop", receipt["cleanupErrors"])
        self.assertTrue(receipt["absenceProof"]["proved"])
        self.assertTrue(any(command[1:3] == ["container", "ls"] for command in calls))
        self.assertTrue(any(command[1:3] == ["network", "ls"] for command in calls))

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
                job = browser_sidecar.BrowserSidecarJob(
                    Path(temp),
                    Path("/workspace/repo/outputs/group/exp"),
                    job_id="formal-job-1",
                )
                with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                    job.start()

        self.assertEqual(caught.exception.check, "broker-image-access")
        self.assertFalse(any(command[1:3] == ["network", "create"] for command in calls))
        self.assertFalse(any(command[1] == "run" for command in calls))

    def test_public_job_uses_internal_broker_container_and_exact_ledger(self) -> None:
        """No host port/process is present in the successful public lifecycle."""

        self.assertRegex(browser_sidecar.BROKER_IMAGE_ID, r"sha256:[0-9a-f]{64}\Z")
        self.assertNotEqual(browser_sidecar.BROKER_IMAGE_ID, "sha256:" + "0" * 64)
        self.assertRegex(browser_sidecar.BROKER_IMAGE_SOURCE_REVISION, r"[0-9a-f]{40}\Z")
        source = Path(browser_sidecar.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"-p",', source)
        self.assertNotIn("subprocess.Popen(", source)

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )

        self.assertEqual(job.broker_container_name, f"{job.prefix}-broker")
        self.assertNotEqual(job.broker_container_name, job.container_name)

    def test_broker_artifact_seals_current_public_conformance_client(self) -> None:
        """The exact browser-less artifact can prove the public API without source mounts."""

        dockerfile = (
            browser_sidecar.REPO_ROOT
            / "packages/meshshot/browser_sidecar_broker/Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "COPY packages/meshshot/src/meshshot ./packages/meshshot/src/meshshot",
            dockerfile,
        )
        self.assertIn(
            "COPY scripts/pilot/browser_sidecar_conformance.py",
            dockerfile,
        )

    def test_public_job_rejects_preexisting_capability_socket(self) -> None:
        """A pre-existing path is foreign state and is never unlinked or adopted."""

        with tempfile.TemporaryDirectory() as temp:
            job = browser_sidecar.BrowserSidecarJob(
                Path(temp),
                Path("/workspace/repo/outputs/group/exp"),
                job_id="formal-job-1",
            )
            job.socket_path.write_text("foreign", encoding="utf-8")
            with self.assertRaises(browser_sidecar.BrowserSidecarError) as caught:
                job.start()
            self.assertEqual(job.socket_path.read_text(encoding="utf-8"), "foreign")

        self.assertEqual(caught.exception.check, "broker-socket-preexisting")


class RegisteredProgramBrokerTests(unittest.TestCase):
    """Observe exact request validation and fresh contexts at the broker seam."""

    def test_residual_program_uses_one_fresh_context_and_rejects_extra_keys(self) -> None:
        view_names = ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"]

        class FakePage:
            def __init__(self) -> None:
                self.goto_calls: list[tuple[str, dict[str, object]]] = []

            def goto(self, url, **kwargs):
                self.goto_calls.append((url, kwargs))

            def wait_for_function(self, expression, **kwargs):
                self.wait_expression = expression

            def evaluate(self, expression, payload):
                self.expression = expression
                self.payload = payload
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

        geometry = {
            "vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "faces": [[0, 1, 2]],
        }
        request = {
            "schema": "meshshot.browser-sidecar.render-request/2",
            "jobId": "formal-job-1",
            "imageId": IMAGE_ID,
            "program": "residual",
            "payload": {
                "reference": geometry,
                "candidate": geometry,
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
        self.assertNotIn("options", browser.contexts[0].page.payload)

        malformed = dict(request)
        malformed["url"] = "https://attacker.invalid/"
        with self.assertRaisesRegex(browser_sidecar.BrowserSidecarError, "schema"):
            broker.execute(malformed)
        self.assertEqual(len(browser.contexts), 1)

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
                "schema": "meshshot.browser-sidecar.render-request/2",
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
        preflight_result = [
            {
                "authority": {
                    "schema": "meshshot.browser-sidecar.prototype/1",
                    "jobId": "formal-job-1",
                    "endpointPath": "/browser/token",
                    "browserPid": 321,
                    "chromiumRevision": "1223",
                    "chromiumVersion": "148.0.7778.96",
                    "playwrightVersion": "1.60.0",
                    "programs": PROGRAMS,
                    "sourceAliasesVisible": [],
                },
                "externalEgressBlocked": True,
            }
        ]

        class FakePage:
            def goto(self, url, **kwargs):
                self.url = url

            def evaluate(self, expression, argument=None):
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

        receipt = broker.preflight()

        self.assertEqual(
            receipt,
            {
                "sourceAliasesVisible": [],
                "externalEgressBlocked": True,
                "browserPid": 321,
            },
        )
        self.assertTrue(browser.contexts[0].closed)

        preflight_result[0] = {
            "authority": {
                "schema": "meshshot.browser-sidecar.prototype/1",
                "jobId": "formal-job-1",
                "programs": PROGRAMS,
                "sourceAliasesVisible": ["/workspace"],
                "browserPid": 321,
            },
            "externalEgressBlocked": True,
        }
        with self.assertRaisesRegex(browser_sidecar.BrowserSidecarError, "isolation"):
            broker.preflight()


if __name__ == "__main__":
    unittest.main()
