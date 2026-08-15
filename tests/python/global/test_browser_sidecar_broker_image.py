"""Opt-in production-path checks for the exact Browser Sidecar Broker image."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")

from meshshot import MeshGeometry, render_residual_preview
from scripts.pilot import browser_sidecar
from scripts.pilot import browser_sidecar_conformance as conformance
from scripts.pilot.runner import _build_gate_artifact, _prepare_nested_browser_gate_from_manifest


CLIENT_PATH = "/opt/text-to-cad/scripts/pilot/browser_sidecar_conformance.py"
RESOURCE_ID = re.compile(r"[0-9a-f]{64}\Z")


class FixedBrokerFixture:
    """Serve only the two registered programs from one canonical public render."""

    def __init__(self, capability: Path, rendered) -> None:
        self.path = capability / "browser.sock"
        self.rendered = rendered
        self.requests: list[str] = []
        self.error: BaseException | None = None
        self.stop = threading.Event()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(os.fspath(self.path))
        self.path.chmod(0o600)
        self.server.listen(4)
        self.server.settimeout(0.2)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            while len(self.requests) < 4 and not self.stop.is_set():
                try:
                    connection, _ = self.server.accept()
                except TimeoutError:
                    continue
                with connection:
                    wire = bytearray()
                    while chunk := connection.recv(65536):
                        wire.extend(chunk)
                    request = json.loads(bytes(wire).decode("ascii"))
                    program = request["program"]
                    self.requests.append(program)
                    result = self._result(program)
                    response = {
                        "schema": browser_sidecar.RESPONSE_SCHEMA,
                        "jobId": request["jobId"],
                        "imageId": browser_sidecar.IMAGE_ID,
                        "program": program,
                        "result": result,
                    }
                    connection.sendall(
                        json.dumps(response, sort_keys=True, separators=(",", ":")).encode(
                            "ascii"
                        )
                        + b"\n"
                    )
        except BaseException as exc:  # surfaced by the owning test thread
            self.error = exc

    def _result(self, program: str) -> dict[str, object]:
        if program == "residual":
            return {
                "ok": True,
                "pngDataUrl": "data:image/png;base64,"
                + base64.b64encode(self.rendered.png_bytes).decode("ascii"),
                "views": list(self.rendered.views),
            }
        if program != "viewer":
            raise ValueError("unexpected registered program")
        return {
            "title": "CAD Viewer | browser_sidecar_inspection.step",
            "modelKey": "inspection-step",
            "programDigest": browser_sidecar.PROGRAMS["viewer"],
            "screenshotDataUrl": "data:image/png;base64,cG5n",
            "screenshotSha256": "0" * 64,
            "screenshotBytes": 3,
            "bodyMentionsFixture": True,
            "bodyHasArtifactError": False,
            "inspection": {
                "control": "toggle-projection",
                "before": "Display and projection: Solid, Orthographic",
                "target": "Perspective",
                "after": "Display and projection: Solid, Perspective",
                "changed": True,
            },
        }

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=1)
        self.server.close()
        if self.error is not None:
            raise self.error


@unittest.skipUnless(
    os.environ.get("BROWSER_SIDECAR_BROKER_IMAGE_TEST") == "1",
    "requires the exact pre-provisioned Broker image",
)
class BrowserSidecarBrokerImageTests(unittest.TestCase):
    """Execute the reviewed image without pulling, building, or starting a Sidecar."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.docker = shutil.which("docker")
        if cls.docker is None:
            raise unittest.SkipTest("docker is unavailable")

    def test_locked_image_contains_exact_executable_client_source(self) -> None:
        """Extraction and execution both use the exact locked image and file path."""

        for projection, expected in (
            ("{{.Id}}", browser_sidecar.BROKER_IMAGE_ID),
            ("{{.Os}}", "linux"),
            ("{{.Architecture}}", "amd64"),
            (
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                browser_sidecar.BROKER_IMAGE_SOURCE_REVISION,
            ),
        ):
            inspected = subprocess.run(
                [
                    self.docker,
                    "inspect",
                    "--type=image",
                    "--format",
                    projection,
                    browser_sidecar.BROKER_IMAGE_ID.removeprefix("sha256:"),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(inspected.stdout.splitlines(), [expected])
        name = f"ttc-bs-image-file-{os.getpid()}"
        absent = subprocess.run(
            [self.docker, "container", "inspect", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(absent.returncode, 1, "foreign image-test name exists")
        created = subprocess.run(
            [
                self.docker,
                "create",
                "--name",
                name,
                "--label",
                "io.text-to-cad.browser-sidecar-image-test=client-file",
                "--pull=never",
                "--platform",
                "linux/amd64",
                "--network",
                "none",
                "--entrypoint",
                "python3",
                browser_sidecar.BROKER_IMAGE_ID,
                CLIENT_PATH,
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_id = created.stdout.strip()
        self.assertRegex(container_id, RESOURCE_ID)
        try:
            copied = subprocess.run(
                [self.docker, "cp", f"{container_id}:{CLIENT_PATH}", "-"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.assertEqual(copied.returncode, 0, copied.stderr.decode(errors="replace"))
            with tarfile.open(fileobj=BytesIO(copied.stdout), mode="r|") as archive:
                member = archive.next()
                self.assertIsNotNone(member)
                assert member is not None
                extracted = archive.extractfile(member)
                self.assertIsNotNone(extracted)
                assert extracted is not None
                image_bytes = extracted.read()
            expected = (
                browser_sidecar.REPO_ROOT / "scripts/pilot/browser_sidecar_conformance.py"
            ).read_bytes()
            self.assertEqual(image_bytes, expected)
            executed = subprocess.run(
                [self.docker, "start", "-a", container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("{client,host}", executed.stdout)
        finally:
            subprocess.run(
                [self.docker, "rm", "-f", container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_gate_release_executes_image_client_and_completes_predicates(self) -> None:
        """The real sealed gate replaces its PID with the packaged fixed client."""

        reference = MeshGeometry(
            vertices=[[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
            faces=[[0, 1, 2]],
        )
        candidate = MeshGeometry(
            vertices=[[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
            faces=[[0, 1, 2]],
        )
        rendered = render_residual_preview(
            reference, candidate, variant="step", exterior_directions=[]
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary).resolve()
            capability = root / "capability"
            capability.mkdir()
            with (
                mock.patch.object(
                    browser_sidecar.tempfile,
                    "mkdtemp",
                    return_value=os.fspath(capability),
                ),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="7" * 32),
            ):
                job = browser_sidecar.BrowserSidecarJob(
                    root / "exp",
                    Path("/workspace/repo/outputs/conformance/formal"),
                    job_id="formal-image-client",
                )
            discovery = capability / "browser-gate-discovery.pyz"
            _build_gate_artifact(browser_sidecar.REPO_ROOT, discovery)
            manifest = conformance._discover_client_surface(
                self.docker, job, discovery, f"{job.prefix}-surface"
            )
            _prepare_nested_browser_gate_from_manifest(
                browser_sidecar.REPO_ROOT, job, manifest
            )
            authority = {
                "schema": browser_sidecar.AUTHORITY_SCHEMA,
                "jobId": job.job_id,
                "gateNonce": job.gate_nonce,
                "imageId": browser_sidecar.IMAGE_ID,
                "programs": browser_sidecar.PROGRAMS,
            }
            job.authority_path.write_text(
                json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="ascii",
            )
            job.authority_path.chmod(0o444)
            broker = FixedBrokerFixture(capability, rendered)
            try:
                status, result = conformance._run_gate_then_client(
                    self.docker, job, f"{job.prefix}-client", manifest
                )
            finally:
                broker.close()

        self.assertEqual(status, 0)
        self.assertEqual(
            result["schema"], "meshshot.browser-sidecar.local-conformance-client/1"
        )
        self.assertEqual(
            result["publicResidual"]["pngSha256"],
            browser_sidecar.NESTED_GATE["publicPngSha256"],
        )
        self.assertTrue(result["viewer"]["inspection"]["changed"])
        self.assertEqual(result["clientBrowserInventory"]["browserProcesses"], [])
        self.assertEqual(broker.requests, ["residual", "viewer", "residual", "viewer"])


if __name__ == "__main__":
    unittest.main()
