"""Opt-in production-path checks for the exact Browser Sidecar Broker image."""

from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")

from scripts.pilot import browser_sidecar
from scripts.pilot import browser_sidecar_conformance as conformance
from scripts.pilot.runner import _prepare_nested_browser_gate_from_manifest


CLIENT_PATH = "/opt/text-to-cad/scripts/pilot/browser_sidecar_conformance.py"
LEGACY_IMAGE_ID = "sha256:7a15df89f7e8f194446ba251cfdb280416e85c46b9a514528d4ab221201ca3af"
LEGACY_SOURCE_REVISION = "7e9fbbd15a365d5df691a79b0d2352492888d361"
RESOURCE_ID = re.compile(r"[0-9a-f]{64}\Z")


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

    def _linux_public_fixture(self) -> dict[str, object]:
        """Render one canonical fixture in the exact reviewed Linux baseline."""

        for projection, expected in (
            ("{{.Id}}", LEGACY_IMAGE_ID),
            ("{{.Os}}", "linux"),
            ("{{.Architecture}}", "amd64"),
            (
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                LEGACY_SOURCE_REVISION,
            ),
        ):
            inspected = subprocess.run(
                [
                    self.docker,
                    "inspect",
                    "--type=image",
                    "--format",
                    projection,
                    LEGACY_IMAGE_ID.removeprefix("sha256:"),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(inspected.stdout.splitlines(), [expected])
        name = f"ttc-bs-image-baseline-{os.getpid()}"
        absent = subprocess.run(
            [self.docker, "container", "inspect", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(absent.returncode, 1, "foreign baseline-test name exists")
        created = subprocess.run(
            [
                self.docker,
                "create",
                "--name",
                name,
                "--label",
                "io.text-to-cad.browser-sidecar-image-test=linux-baseline",
                "--pull=never",
                "--platform",
                "linux/amd64",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "128",
                "--memory",
                "768m",
                "--memory-swap",
                "768m",
                "--cpus",
                "1",
                "--shm-size",
                "256m",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                "--tmpfs",
                "/home/pwuser:rw,nosuid,nodev,size=32m,uid=1001,gid=1001,mode=700",
                "--entrypoint",
                "python3",
                LEGACY_IMAGE_ID,
                "/opt/browser-sidecar/image-baseline.py",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_id = created.stdout.strip()
        self.assertRegex(container_id, RESOURCE_ID)
        helper = (
            browser_sidecar.REPO_ROOT
            / "tests/python/fixtures/browser_sidecar_linux_baseline.py"
        )
        try:
            copied = subprocess.run(
                [
                    self.docker,
                    "cp",
                    "-a",
                    os.fspath(helper),
                    f"{container_id}:/opt/browser-sidecar/image-baseline.py",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(copied.returncode, 0, copied.stderr)
            completed = subprocess.run(
                [self.docker, "start", "-a", container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=conformance.CONFORMANCE_TIMEOUT_SECONDS,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            lines = completed.stdout.splitlines()
            self.assertEqual(len(lines), 1, completed.stdout)
            fixture = json.loads(lines[0])
        finally:
            subprocess.run(
                [self.docker, "rm", "-f", container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(
            fixture["pngSha256"], browser_sidecar.NESTED_GATE["publicPngSha256"]
        )
        self.assertEqual(
            fixture["profileSha256"], browser_sidecar.NESTED_GATE["profileSha256"]
        )
        return fixture

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
            copied_sources: dict[str, bytes] = {}
            for remote in (
                "/opt/text-to-cad/scripts/pilot/browser_sidecar.py",
                CLIENT_PATH,
                "/opt/text-to-cad/packages/meshshot/src/meshshot",
            ):
                copied = subprocess.run(
                    [self.docker, "cp", f"{container_id}:{remote}", "-"],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
                self.assertEqual(
                    copied.returncode,
                    0,
                    copied.stderr.decode(errors="replace"),
                )
                with tarfile.open(fileobj=BytesIO(copied.stdout), mode="r|") as archive:
                    while member := archive.next():
                        if not member.isfile():
                            continue
                        extracted = archive.extractfile(member)
                        self.assertIsNotNone(extracted)
                        assert extracted is not None
                        copied_sources[member.name] = extracted.read()
            expected_sources = {
                "browser_sidecar.py": (
                    browser_sidecar.REPO_ROOT / "scripts/pilot/browser_sidecar.py"
                ).read_bytes(),
                "browser_sidecar_conformance.py": (
                    browser_sidecar.REPO_ROOT
                    / "scripts/pilot/browser_sidecar_conformance.py"
                ).read_bytes(),
            }
            meshshot_root = (
                browser_sidecar.REPO_ROOT / "packages/meshshot/src/meshshot"
            )
            for path in sorted(meshshot_root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    expected_sources["meshshot/" + path.relative_to(meshshot_root).as_posix()] = (
                        path.read_bytes()
                    )
            self.assertEqual(copied_sources, expected_sources)
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

        linux_fixture = self._linux_public_fixture()
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
            manifest = {
                "schema": browser_sidecar.NESTED_GATE["surfaceSchema"],
                "scanRoots": [],
                "browserExclusions": [],
            }
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
            fixture = root / "fixture.json"
            fixture.write_text(
                json.dumps(
                    {
                        "pngDataUrl": linux_fixture["pngDataUrl"],
                        "views": linux_fixture["views"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="ascii",
            )
            harness = (
                browser_sidecar.REPO_ROOT
                / "tests/python/fixtures/browser_sidecar_image_harness.py"
            )
            name = f"ttc-bs-image-gate-{os.getpid()}"
            absent = subprocess.run(
                [self.docker, "container", "inspect", name],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(absent.returncode, 1, "foreign image-gate name exists")
            created = subprocess.run(
                [
                    self.docker,
                    "create",
                    "--name",
                    name,
                    "--label",
                    "io.text-to-cad.browser-sidecar-image-test=gate-client",
                    "--pull=never",
                    "--platform",
                    "linux/amd64",
                    "--network",
                    "none",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--entrypoint",
                    "python3",
                    browser_sidecar.BROKER_IMAGE_ID,
                    "/tmp/browser-sidecar-image-harness.py",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            container_id = created.stdout.strip()
            self.assertRegex(container_id, RESOURCE_ID)
            try:
                for source, destination in (
                    (capability, "/run/meshshot-browser"),
                    (harness, "/tmp/browser-sidecar-image-harness.py"),
                    (fixture, "/tmp/browser-sidecar-image-fixture.json"),
                ):
                    copied = subprocess.run(
                        [
                            self.docker,
                            "cp",
                            "-a",
                            os.fspath(source),
                            f"{container_id}:{destination}",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(copied.returncode, 0, copied.stderr)
                completed = subprocess.run(
                    [self.docker, "start", "-a", container_id],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=conformance.CONFORMANCE_TIMEOUT_SECONDS,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                lines = completed.stdout.splitlines()
                self.assertEqual(len(lines), 1, completed.stdout)
                result = json.loads(lines[0])
            finally:
                subprocess.run(
                    [self.docker, "rm", "-f", container_id],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

        self.assertEqual(
            result["schema"], "meshshot.browser-sidecar.local-conformance-client/1"
        )
        self.assertEqual(
            result["publicResidual"]["pngSha256"],
            browser_sidecar.NESTED_GATE["publicPngSha256"],
        )
        self.assertTrue(result["viewer"]["inspection"]["changed"])
        self.assertEqual(result["clientBrowserInventory"]["browserProcesses"], [])


if __name__ == "__main__":
    unittest.main()
