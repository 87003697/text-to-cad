"""Public production-shaped Browser Sidecar conformance host tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.pilot import browser_sidecar
from scripts.pilot import browser_sidecar_conformance as conformance


NETWORK_ID = "a" * 64
SIDECAR_ID = "b" * 64
BROKER_ID = "c" * 64
SURFACE_ID = "d" * 64
CLIENT_ID = "e" * 64


def _proof_from_gate_input(value: dict[str, object]) -> dict[str, object]:
    """Return the independent fixed proof required by the public validator."""

    manifest = value["surfaceManifest"]
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    nested = browser_sidecar.NESTED_GATE
    return {
        "schema": nested["schema"],
        "status": "succeeded",
        "jobId": value["jobId"],
        "nonce": value["nonce"],
        "artifactSha256": value["artifactSha256"],
        "surfaceManifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "predicates": {
            "publicResidualParity": True,
            "viewerProjectionChanged": True,
            "viewerArtifactClean": True,
            "browserInventoryEmpty": True,
            "browserProcessZero": True,
        },
        "residual": {
            "pngSha256": nested["publicPngSha256"],
            "mode": "RGB",
            "size": [504, 1008],
            "profileSha256": nested["profileSha256"],
            "views": nested["views"],
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


class DockerBoundary:
    """Fake only the exact Docker/process boundary of the public host action."""

    def __init__(
        self,
        *,
        foreign_surface: bool = False,
        foreign_client: bool = False,
        surface_output_loss: bool = False,
        client_replaced: bool = False,
    ) -> None:
        self.calls: list[list[str]] = []
        self.events: list[str] = []
        self.gate_input: dict[str, object] | None = None
        self.foreign_surface = foreign_surface
        self.foreign_client = foreign_client
        self.surface_output_loss = surface_output_loss
        self.client_replaced = client_replaced
        self.client_capability: Path | None = None

    def run(self, argv, **kwargs):
        command = list(argv)
        self.calls.append(command)
        if command[1:4] == ["inspect", "--type=image", "--format"]:
            projection = command[4]
            broker = command[5] == browser_sidecar.BROKER_IMAGE_ID.removeprefix(
                "sha256:"
            )
            values = {
                "{{.Id}}": (
                    browser_sidecar.BROKER_IMAGE_ID
                    if broker
                    else browser_sidecar.IMAGE_ID
                ),
                "{{.Os}}": "linux",
                "{{.Architecture}}": "amd64",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}': (
                    browser_sidecar.BROKER_IMAGE_SOURCE_REVISION
                    if broker
                    else browser_sidecar.IMAGE_SOURCE_REVISION
                ),
                '{{index .Config.Labels "io.text-to-cad.browser-sidecar-broker-base"}}': (
                    browser_sidecar.BROKER_BASE_IMAGE_ID
                ),
            }
            return subprocess.CompletedProcess(command, 0, values[projection] + "\n", "")
        if command[1:3] in (["container", "inspect"], ["network", "inspect"]):
            if "--format" in command and any(
                resource in command
                for resource in (NETWORK_ID, SIDECAR_ID, BROKER_ID, SURFACE_ID, CLIENT_ID)
            ):
                projection = command[-1]
                if "browser-sidecar-job" in projection:
                    return subprocess.CompletedProcess(
                        command, 0, "formal-local-conformance\n", ""
                    )
                if "browser-sidecar-owner" in projection:
                    return subprocess.CompletedProcess(command, 0, "1" * 32 + "\n", "")
            if "--format" in command and (
                SIDECAR_ID in command or BROKER_ID in command
            ):
                target = BROKER_ID if BROKER_ID in command else SIDECAR_ID
                running = not any(
                    call[1] == "stop" and target in call for call in self.calls
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"Running": running, "ExitCode": 0}) + "\n",
                    "",
                )
            if self.foreign_surface and command[-1].endswith("-surface"):
                return subprocess.CompletedProcess(command, 0, "foreign\n", "")
            if self.foreign_client and command[-1].endswith("-client"):
                return subprocess.CompletedProcess(command, 0, "foreign\n", "")
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[1:3] == ["network", "create"]:
            return subprocess.CompletedProcess(command, 0, NETWORK_ID + "\n", "")
        if command[1] == "create":
            name = command[command.index("--name") + 1]
            if name.endswith("-surface"):
                self.events.append("surface-create")
                output = "" if self.surface_output_loss else SURFACE_ID + "\n"
                return subprocess.CompletedProcess(command, 0, output, "")
            if name.endswith("-client"):
                self.events.append("client-create")
                mount = next(
                    value
                    for value in command
                    if value.startswith("type=bind,")
                    and ",dst=/run/meshshot-browser" in value
                )
                self.client_capability = Path(
                    mount.split("src=", 1)[1].split(",dst=", 1)[0]
                )
                return subprocess.CompletedProcess(command, 0, CLIENT_ID + "\n", "")
            if browser_sidecar.BROKER_IMAGE_ID in command:
                self.events.append("broker-create")
                bind = next(value for value in command if value.startswith("type=bind,"))
                self.broker_source = Path(
                    bind.split("src=", 1)[1].split(",dst=", 1)[0]
                )
                return subprocess.CompletedProcess(command, 0, BROKER_ID + "\n", "")
            self.events.append("sidecar-create")
            return subprocess.CompletedProcess(command, 0, SIDECAR_ID + "\n", "")
        if command[1:3] == ["start", BROKER_ID]:
            self.events.append("broker-run")
            created = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            created.bind(os.fspath(self.broker_source / "browser.sock"))
            created.close()
            (self.broker_source / "browser.sock").chmod(0o600)
            return subprocess.CompletedProcess(command, 0, BROKER_ID + "\n", "")
        if command[1:3] == ["start", SIDECAR_ID]:
            self.events.append("sidecar-run")
            return subprocess.CompletedProcess(command, 0, SIDECAR_ID + "\n", "")
        if command[1:3] == ["start", "-a"] and SURFACE_ID in command:
            self.events.append("surface-discovery")
            discovery = {
                "schema": "meshshot.browser-sidecar.conformance-surface/1",
                "scanRoots": ["/opt", "/usr"],
                "browserExclusions": [
                    {
                        "kind": "package",
                        "target": "/usr/local/lib/python3/site-packages/playwright",
                        "mask": "tmpfs",
                    }
                ],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(discovery, sort_keys=True, separators=(",", ":")) + "\n",
                "",
            )
        if command[1] == "run":
            if "--discover-conformance-surface" in command:
                self.events.append("surface-discovery")
                discovery = {
                    "schema": "meshshot.browser-sidecar.conformance-surface/1",
                    "scanRoots": ["/opt", "/usr"],
                    "browserExclusions": [
                        {
                            "kind": "package",
                            "target": "/usr/local/lib/python3/site-packages/playwright",
                            "mask": "tmpfs",
                        }
                    ],
                }
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(discovery, sort_keys=True, separators=(",", ":"))
                    + "\n",
                    "",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"schema": "fixed-client-result/1"}) + "\n",
                "",
            )
        if command[1] == "logs":
            if BROKER_ID in command:
                records = [
                    {
                        "event": "ready",
                        "schema": browser_sidecar.BROKER_SCHEMA,
                        "jobId": "formal-local-conformance",
                        "imageId": browser_sidecar.IMAGE_ID,
                        "programs": browser_sidecar.PROGRAMS,
                        "isolation": {
                            "sourceAliasesVisible": [],
                            "externalEgressBlocked": True,
                            "browserPid": 321,
                        },
                    }
                ]
                if any(call[1] == "stop" and BROKER_ID in call for call in self.calls):
                    records.append(
                        {
                            "event": "terminal",
                            "schema": browser_sidecar.BROKER_SCHEMA,
                            "jobId": "formal-local-conformance",
                            "imageId": browser_sidecar.IMAGE_ID,
                            "acceptedRequests": 4,
                            "freshContexts": 5,
                            "programCounts": {"residual": 2, "viewer": 2},
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
                    "jobId": "formal-local-conformance",
                    "endpointPath": "/browser/session-token",
                    "programs": browser_sidecar.PROGRAMS,
                }
            ]
            if any(call[1] == "stop" and SIDECAR_ID in call for call in self.calls):
                records.append(
                    {
                        "event": "closing",
                        "jobId": "formal-local-conformance",
                        "reason": "SIGTERM",
                    }
                )
            return subprocess.CompletedProcess(
                command,
                0,
                "".join(json.dumps(record) + "\n" for record in records),
                "",
            )
        if command[1:3] in (["container", "ls"], ["network", "ls"]):
            if self.surface_output_loss and command[1] == "container":
                return subprocess.CompletedProcess(command, 0, SURFACE_ID + "\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["rm", "-f"] and CLIENT_ID in command and self.client_replaced:
            return subprocess.CompletedProcess(command, 1, "", "no such container")
        return subprocess.CompletedProcess(command, 0, "", "")

    def popen(self, argv, **kwargs):
        command = list(argv)
        self.calls.append(command)
        self.events.append("gate-client-popen")
        if self.client_capability is None:
            mount = next(
                value
                for value in command
                if value.startswith("type=bind,")
                and ",dst=/run/meshshot-browser" in value
            )
            capability = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
        else:
            capability = self.client_capability
        gate_input = json.loads((capability / "gate-input.json").read_text())
        self.gate_input = gate_input
        proof = _proof_from_gate_input(gate_input)
        boundary = self

        class Process:
            returncode: int | None = None

            def __init__(self) -> None:
                self.done = threading.Event()
                self.thread = threading.Thread(target=self._serve, daemon=True)
                self.thread.start()

            def _serve(self) -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(os.fspath(capability / "gate.sock"))
                    client.sendall(
                        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode(
                            "ascii"
                        )
                        + b"\n"
                    )
                    client.shutdown(socket.SHUT_WR)
                    if client.recv(2) == b"\x01" and client.recv(1) == b"":
                        boundary.events.append("gate-proof-released")
                        self.returncode = 0
                    else:
                        self.returncode = 1
                self.done.set()

            def communicate(self, timeout=None):
                if not self.done.wait(timeout):
                    raise subprocess.TimeoutExpired(command, timeout)
                boundary.events.append("client-exec-complete")
                return json.dumps({"schema": "fixed-client-result/1"}) + "\n", ""

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.communicate(timeout=timeout)
                return self.returncode

            def terminate(self):
                self.returncode = 143
                self.done.set()

            def kill(self):
                self.returncode = 137
                self.done.set()

        return Process()


class BrowserSidecarConformanceHostTests(unittest.TestCase):
    """Observe the production host action through its public evidence file."""

    def test_surface_discovery_home_matches_fixed_root_role(self) -> None:
        """Capability-dropped root can inspect its fresh private home."""

        arguments = conformance._fixed_container_isolation(
            user="0:0", read_only_discovery=True
        )
        self.assertIn(
            "/home/pwuser:rw,nosuid,nodev,size=16m,uid=0,gid=0,mode=700",
            arguments,
        )
        self.assertIn("DAC_READ_SEARCH", arguments)
        self.assertNotIn("DAC_READ_SEARCH", conformance._fixed_container_isolation())
        self.assertNotIn(
            "DAC_READ_SEARCH", conformance._fixed_container_isolation(user="0:0")
        )

    def test_host_rejects_nonprivate_evidence_parent_before_docker(self) -> None:
        """The CLI evidence parent is also the exact private daemon-share root."""

        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            parent = Path(temp).resolve() / "public"
            parent.mkdir(mode=0o755)
            evidence = parent / "conformance.json"
            with (
                mock.patch.object(
                    conformance.shutil, "which", return_value="/usr/bin/docker"
                ),
                mock.patch.object(subprocess, "run") as run,
            ):
                status = conformance.run_host(evidence)
            published = json.loads(evidence.read_text(encoding="utf-8"))

        self.assertEqual(status, 2)
        self.assertEqual(published["status"], "failed")
        self.assertEqual(
            published["receipt"]["failureCheck"], "capability-parent-private"
        )
        self.assertTrue(published["receipt"]["predicates"]["absenceProved"])
        self.assertFalse(published["receipt"]["retryAllowed"])
        run.assert_not_called()

    def test_host_rejects_symlink_evidence_parent_and_publishes_canonically(
        self,
    ) -> None:
        """A caller alias cannot split the capability root from terminal evidence."""

        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp).resolve()
            parent = root / "private"
            parent.mkdir(mode=0o700)
            alias = root / "alias"
            alias.symlink_to(parent, target_is_directory=True)
            evidence = alias / "conformance.json"
            canonical_evidence = parent / evidence.name
            with (
                mock.patch.object(
                    conformance.shutil, "which", return_value="/usr/bin/docker"
                ),
                mock.patch.object(subprocess, "run") as run,
                mock.patch.object(
                    conformance, "_write_atomic", wraps=conformance._write_atomic
                ) as write,
            ):
                status = conformance.run_host(evidence)

            published = json.loads(canonical_evidence.read_text(encoding="utf-8"))

        self.assertEqual(status, 2)
        self.assertEqual(published["status"], "failed")
        self.assertEqual(
            published["receipt"]["failureCheck"], "capability-parent-canonical"
        )
        self.assertTrue(published["receipt"]["predicates"]["absenceProved"])
        self.assertFalse(published["receipt"]["retryAllowed"])
        self.assertEqual(write.call_args.args[0], canonical_evidence)
        run.assert_not_called()

    def test_surface_discovery_has_one_bounded_cpu(self) -> None:
        """The exhaustive immutable scan finishes inside the fixed host limit."""

        discovery = conformance._fixed_container_isolation(
            user="0:0", read_only_discovery=True
        )
        workload = conformance._fixed_container_isolation()
        self.assertEqual(discovery[discovery.index("--cpus") + 1], "1")
        self.assertEqual(workload[workload.index("--cpus") + 1], "0.5")

    def test_cleanup_failure_dominates_discovery_failure(self) -> None:
        """A failed removal remains the terminal discovery classification."""

        with (
            mock.patch.object(
                conformance, "_create_owned_container", return_value=SURFACE_ID
            ),
            mock.patch.object(
                conformance,
                "_run_docker",
                return_value=subprocess.CompletedProcess([], 1, "", "failed"),
            ),
            mock.patch.object(
                conformance,
                "_remove_owned_container",
                side_effect=RuntimeError("surface cleanup failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "surface cleanup failed"):
                conformance._discover_client_surface(
                    "/usr/bin/docker", mock.Mock(), "fixed-surface"
                )

    def test_cleanup_failure_dominates_client_failure(self) -> None:
        """A failed removal remains the terminal client classification."""

        with tempfile.TemporaryDirectory() as temporary:
            job = SimpleNamespace(capability_dir=Path(temporary))
            channel = mock.Mock()
            with (
                mock.patch(
                    "scripts.pilot.runner.NestedGateChannel", return_value=channel
                ),
                mock.patch.object(
                    conformance, "_create_owned_container", return_value=CLIENT_ID
                ),
                mock.patch.object(
                    conformance.subprocess,
                    "Popen",
                    side_effect=RuntimeError("client start failed"),
                ),
                mock.patch.object(
                    conformance,
                    "_remove_owned_container",
                    side_effect=RuntimeError("client cleanup failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "client cleanup failed"):
                    conformance._run_gate_then_client(
                        "/usr/bin/docker",
                        job,
                        "fixed-client",
                        {"browserExclusions": []},
                    )

    def test_host_seals_and_validates_gate_before_fixed_client_exec(self) -> None:
        boundary = DockerBoundary()
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            evidence_path = Path(temp).resolve() / "conformance.json"
            with (
                mock.patch.object(conformance.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 32),
                mock.patch.object(subprocess, "run", side_effect=boundary.run),
                mock.patch.object(subprocess, "Popen", side_effect=boundary.popen),
            ):
                status = conformance.run_host(evidence_path)

            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertEqual(evidence["status"], "succeeded")
        self.assertEqual(evidence["receipt"]["status"], "succeeded")
        self.assertEqual(
            evidence["receipt"]["counts"],
            {
                "acceptedRequests": 4,
                "freshContexts": 5,
                "programCounts": {"residual": 2, "viewer": 2},
            },
        )
        self.assertTrue(all(evidence["receipt"]["predicates"].values()))
        self.assertIsNotNone(boundary.gate_input)
        assert boundary.gate_input is not None
        self.assertEqual(boundary.gate_input["jobId"], "formal-local-conformance")
        self.assertEqual(boundary.gate_input["nonce"], "1" * 32)
        self.assertRegex(boundary.gate_input["artifactSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            boundary.gate_input["surfaceManifest"],
            {
                "schema": browser_sidecar.NESTED_GATE["surfaceSchema"],
                "scanRoots": ["/opt", "/usr"],
                "browserExclusions": [
                    {
                        "kind": "package",
                        "target": "/usr/local/lib/python3/site-packages/playwright",
                        "mask": "tmpfs",
                    }
                ],
            },
        )
        self.assertLess(
            boundary.events.index("surface-discovery"),
            boundary.events.index("sidecar-run"),
        )
        self.assertLess(
            boundary.events.index("broker-run"),
            boundary.events.index("gate-client-popen"),
        )
        self.assertLess(
            boundary.events.index("gate-proof-released"),
            boundary.events.index("client-exec-complete"),
        )
        surface_create = next(
            call
            for call in boundary.calls
            if call[1] == "create" and "ttc-bs-111111111111-surface" in call
        )
        self.assertFalse(
            any(
                value.startswith("type=bind,")
                and f",dst={browser_sidecar.NESTED_GATE['artifactPath']}" in value
                for value in surface_create
            ),
            surface_create,
        )
        self.assertIn(conformance.DISCOVERY_ARTIFACT_PATH, surface_create)
        self.assertEqual(
            surface_create[surface_create.index("--user") + 1],
            "0:0",
        )
        self.assertFalse(
            any(call[1:3] == ["cp", "-a"] for call in boundary.calls),
            boundary.calls,
        )
        bind_sources = [
            value.split("src=", 1)[1].split(",dst=", 1)[0]
            for call in boundary.calls
            for value in call
            if value.startswith("type=bind,") and "src=" in value
        ]
        self.assertTrue(bind_sources)
        self.assertTrue(
            all(
                Path(temp).resolve() in Path(source).parents
                for source in bind_sources
            ),
            bind_sources,
        )
        self.assertTrue(
            all(Path(source) == Path(source).resolve() for source in bind_sources),
            bind_sources,
        )
        self.assertFalse(
            any(
                call[1:3] == ["rm", "-f"]
                and call[-1].startswith("ttc-bs-")
                for call in boundary.calls
            ),
            boundary.calls,
        )
        self.assertTrue(any(call[1] == "rm" and BROKER_ID in call for call in boundary.calls))
        self.assertTrue(any(call[1] == "rm" and SIDECAR_ID in call for call in boundary.calls))
        self.assertTrue(
            any(call[1:3] == ["network", "rm"] and NETWORK_ID in call for call in boundary.calls)
        )

    def test_surface_discovery_uses_only_the_revision_bound_image_source(self) -> None:
        """Discovery needs neither a daemon-visible host path nor a rootfs write."""

        boundary = DockerBoundary()
        with tempfile.TemporaryDirectory() as temp:
            with (
                mock.patch.object(
                    browser_sidecar.secrets, "token_hex", return_value="1" * 32
                ),
                mock.patch.object(subprocess, "run", side_effect=boundary.run),
            ):
                job = browser_sidecar.BrowserSidecarJob.create(
                    Path(temp) / "exp",
                    Path("/workspace/repo/outputs/conformance/formal"),
                    job_id="formal-local-conformance",
                )
                try:
                    conformance._discover_client_surface(
                        "/usr/bin/docker", job, f"{job.prefix}-surface"
                    )
                finally:
                    job.close(workload_status=None)

        create = next(call for call in boundary.calls if call[1] == "create")
        self.assertFalse(
            any(
                value.startswith("type=bind,")
                and f",dst={browser_sidecar.NESTED_GATE['artifactPath']}" in value
                for value in create
            ),
            create,
        )
        self.assertIn(conformance.DISCOVERY_ARTIFACT_PATH, create)
        self.assertFalse(any(call[1:3] == ["cp", "-a"] for call in boundary.calls))

    def test_foreign_surface_collision_is_preserved(self) -> None:
        """A pre-existing predictable name is never adopted or removed."""

        boundary = DockerBoundary(foreign_surface=True)
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = Path(temp).resolve() / "conformance.json"
            with (
                mock.patch.object(conformance.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 32),
                mock.patch.object(subprocess, "run", side_effect=boundary.run),
                mock.patch.object(subprocess, "Popen", side_effect=boundary.popen),
            ):
                status = conformance.run_host(evidence_path)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        surface_name = "ttc-bs-111111111111-surface"
        self.assertEqual(status, 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("foreign conformance-surface name", evidence["error"])
        self.assertFalse(
            any(call[1:4] == ["rm", "-f", surface_name] for call in boundary.calls),
            boundary.calls,
        )

    def test_surface_create_output_loss_is_retained_without_name_cleanup(self) -> None:
        """Lost create output cannot authorize deleting whatever later owns the name."""

        boundary = DockerBoundary(surface_output_loss=True)
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = Path(temp).resolve() / "conformance.json"
            with (
                mock.patch.object(conformance.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 32),
                mock.patch.object(subprocess, "run", side_effect=boundary.run),
                mock.patch.object(subprocess, "Popen", side_effect=boundary.popen),
            ):
                status = conformance.run_host(evidence_path)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        surface_name = "ttc-bs-111111111111-surface"
        self.assertEqual(status, 1)
        self.assertEqual(evidence["receipt"]["failureCheck"], "retained-resource")
        self.assertFalse(
            any(call[1:4] == ["rm", "-f", surface_name] for call in boundary.calls),
            boundary.calls,
        )

    def test_unproved_surface_id_is_never_removed(self) -> None:
        """A returned ID is cleanup authority only after both labels verify."""

        with tempfile.TemporaryDirectory(dir="/tmp") as capability:
            with mock.patch.object(
                browser_sidecar.tempfile, "mkdtemp", return_value=capability
            ):
                job = browser_sidecar.BrowserSidecarJob.create(
                    Path(capability) / "exp",
                    Path("/workspace/repo/outputs/conformance/formal"),
                    job_id="formal-local-conformance",
                )
            def docker(argv):
                command = list(argv)
                if command[1] == "create":
                    return subprocess.CompletedProcess(command, 0, SURFACE_ID + "\n", "")
                if command[1:3] == ["container", "inspect"]:
                    if command[-1] == "fixed-surface":
                        return subprocess.CompletedProcess(command, 1, "", "not found")
                    return subprocess.CompletedProcess(command, 0, "wrong\n", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                mock.patch.object(conformance, "_run_docker", side_effect=docker),
                mock.patch.object(conformance, "_remove_owned_container") as remove,
            ):
                with self.assertRaisesRegex(RuntimeError, "job label mismatch"):
                    conformance._discover_client_surface(
                        "/usr/bin/docker", job, "fixed-surface"
                    )
            remove.assert_not_called()

    def test_foreign_client_collision_is_preserved(self) -> None:
        """A client-name collision after Sidecar startup remains foreign state."""

        boundary = DockerBoundary(foreign_client=True)
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            evidence_path = Path(temp).resolve() / "conformance.json"
            with (
                mock.patch.object(conformance.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 32),
                mock.patch.object(subprocess, "run", side_effect=boundary.run),
                mock.patch.object(subprocess, "Popen", side_effect=boundary.popen),
            ):
                status = conformance.run_host(evidence_path)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        client_name = "ttc-bs-111111111111-client"
        self.assertEqual(status, 1)
        self.assertIn("foreign conformance-client name", evidence["error"])
        self.assertFalse(
            any(call[1:4] == ["rm", "-f", client_name] for call in boundary.calls),
            boundary.calls,
        )

    def test_client_name_replacement_is_preserved_and_fails_closed(self) -> None:
        """Cleanup targets the returned ID and never a replacement at its old name."""

        boundary = DockerBoundary(client_replaced=True)
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            evidence_path = Path(temp).resolve() / "conformance.json"
            with (
                mock.patch.object(conformance.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(browser_sidecar.secrets, "token_hex", return_value="1" * 32),
                mock.patch.object(subprocess, "run", side_effect=boundary.run),
                mock.patch.object(subprocess, "Popen", side_effect=boundary.popen),
            ):
                status = conformance.run_host(evidence_path)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        client_name = "ttc-bs-111111111111-client"
        self.assertEqual(status, 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertTrue(any(call[1:4] == ["rm", "-f", CLIENT_ID] for call in boundary.calls))
        self.assertFalse(
            any(call[1:4] == ["rm", "-f", client_name] for call in boundary.calls),
            boundary.calls,
        )


if __name__ == "__main__":
    unittest.main()
