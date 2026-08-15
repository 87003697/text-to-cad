"""Public formal-pilot Browser Sidecar lifecycle tests."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
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


class FakeBrokerProcess:
    """External broker-process stand-in at the process boundary."""

    def __init__(self, job_id: str) -> None:
        self.pid = 9876
        self.returncode: int | None = None
        self.stdout = BytesIO(
            (
                json.dumps(
                    {
                        "event": "ready",
                        "schema": "meshshot.browser-sidecar.broker/1",
                        "jobId": job_id,
                        "imageId": IMAGE_ID,
                        "programs": PROGRAMS,
                        "isolation": {
                            "sourceAliasesVisible": [],
                            "externalEgressBlocked": True,
                            "browserPid": 321,
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "terminal",
                        "schema": "meshshot.browser-sidecar.broker/1",
                        "jobId": job_id,
                        "imageId": IMAGE_ID,
                        "acceptedRequests": 2,
                        "freshContexts": 3,
                        "programCounts": {"residual": 1, "viewer": 1},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("ascii")
        )

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout=None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class BrowserSidecarJobTests(unittest.TestCase):
    """Observe one complete exact-image lifecycle through its public adapter."""

    def test_success_owns_one_sidecar_and_publishes_terminal_absence(self) -> None:
        calls: list[list[str]] = []

        def docker(argv, **kwargs):
            command = list(argv)
            calls.append(command)
            if command[1:4] == ["inspect", "--type=image", "--format"]:
                projection = command[4]
                values = {
                    "{{.Id}}": IMAGE_ID,
                    "{{.Os}}": "linux",
                    "{{.Architecture}}": "amd64",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': SOURCE_REVISION,
                }
                return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
            if command[1:3] in (["container", "inspect"], ["network", "inspect"]):
                if CONTAINER_ID in command and "--format" in command:
                    running = not any(call[1] == "stop" for call in calls)
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
                return subprocess.CompletedProcess(command, 0, CONTAINER_ID + "\n", "")
            if command[1] == "logs":
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
            if command[1] == "port":
                ports = {"3000/tcp": 43000, "4173/tcp": 43173, "4174/tcp": 43174}
                return subprocess.CompletedProcess(
                    command, 0, f"127.0.0.1:{ports[command[-1]]}\n", ""
                )
            if command[1:3] in (
                ["container", "ls"],
                ["network", "ls"],
            ):
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp)
            broker = FakeBrokerProcess("formal-job-1")
            with (
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.subprocess, "run", side_effect=docker),
                mock.patch.object(browser_sidecar.subprocess, "Popen", return_value=broker) as popen,
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 16),
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
        run = next(command for command in calls if command[1] == "run")
        self.assertIn("--pull=never", run)
        self.assertIn("--read-only", run)
        self.assertNotIn("-v", run)
        self.assertNotIn("--mount", run)
        self.assertEqual(run[-1], IMAGE_ID)
        broker_argv = popen.call_args.args[0]
        self.assertEqual(broker_argv[1:3], [str(browser_sidecar.Path(browser_sidecar.__file__)), "broker"])

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
            job.broker = FakeBrokerProcess("formal-job-1")
            job.broker.stdout.readline()
            with mock.patch.object(browser_sidecar.subprocess, "run", side_effect=docker):
                receipt = job.close(workload_status=0)

        self.assertEqual(receipt["status"], "failed")
        self.assertIn("sidecar-stop", receipt["cleanupErrors"])
        self.assertTrue(receipt["absenceProof"]["proved"])
        self.assertTrue(any(command[1:3] == ["container", "ls"] for command in calls))
        self.assertTrue(any(command[1:3] == ["network", "ls"] for command in calls))


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
