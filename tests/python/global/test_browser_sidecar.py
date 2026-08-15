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
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "event": "ready",
                            "jobId": "formal-job-1",
                            "endpointPath": "/browser/session-token",
                            "programs": PROGRAMS,
                        }
                    )
                    + "\n",
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
            "/workspace/repo/outputs/group/exp/run/browser-sidecar.sock",
        )
        self.assertEqual(receipt["status"], "succeeded")
        self.assertTrue(receipt["absenceProof"]["proved"])
        self.assertEqual(receipt["cleanupErrors"], [])
        run = next(command for command in calls if command[1] == "run")
        self.assertIn("--pull=never", run)
        self.assertIn("--read-only", run)
        self.assertNotIn("-v", run)
        self.assertNotIn("--mount", run)
        self.assertEqual(run[-1], IMAGE_ID)
        broker_argv = popen.call_args.args[0]
        self.assertEqual(broker_argv[1:3], [str(browser_sidecar.Path(browser_sidecar.__file__)), "broker"])


if __name__ == "__main__":
    unittest.main()
