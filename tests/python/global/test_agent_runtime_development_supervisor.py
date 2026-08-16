from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.pilot.agent_runtime import development_supervisor as supervisor


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class FakeEngine:
    def __init__(self, *, timeout: bool = False, residue: bool = False) -> None:
        self.timeout = timeout
        self.residue = residue
        self.calls: list[object] = []
        self.spec: supervisor.ContainerSpec | None = None
        self.release: bytes | None = None
        self.control: dict[str, object] | None = None
        self.broker_proof: dict[str, object] | None = None
        self.mount_modes: dict[str, int] = {}
        self.control_mode: int | None = None

    def create(self, spec: supervisor.ContainerSpec) -> str:
        self.calls.append("create")
        self.spec = spec
        control_path = next(
            mount.source for mount in spec.mounts
            if mount.target == "/run/text-to-cad-agent/control"
        ) / "manifest.json"
        self.mount_modes = {
            mount.target: mount.source.stat().st_mode & 0o777 for mount in spec.mounts
        }
        self.control_mode = control_path.stat().st_mode & 0o777
        self.control = json.loads(control_path.read_bytes())
        return "container-exact"

    def inspect(self, container_id: str) -> supervisor.ContainerObservation:
        self.calls.append(("inspect", container_id))
        assert self.spec is not None
        return supervisor.ContainerObservation.from_spec(container_id, self.spec)

    def exchange(self, container_id, release_for_preflight, timeout_seconds):
        self.calls.append(("exchange", container_id, timeout_seconds))
        if self.timeout:
            raise TimeoutError("synthetic timeout")
        assert self.control is not None
        identity = {key: self.control[key] for key in supervisor.IDENTITY_KEYS}
        assert self.broker_proof is not None
        proof = self.broker_proof
        preflight = {
            "schema": "text-to-cad.agent-entrypoint-preflight/1",
            "brokerProof": proof,
            "brokerProofDigest": _digest(supervisor.canonical_json_bytes(proof)),
        }
        self.release = release_for_preflight(supervisor.canonical_json_bytes(preflight))
        terminal = {
            "schema": "text-to-cad.agent-entrypoint-terminal/1",
            "workloadStatus": 0,
            "outputDigest": "sha256:" + "8" * 64,
            "processGroupAbsent": True,
            "descendantResidue": False,
            "interruptedSignal": None,
            **identity,
        }
        return supervisor.AttachedResult(
            status=0,
            stdout=supervisor.canonical_json_bytes(preflight) + b"\n" + supervisor.canonical_json_bytes(terminal) + b"\n",
            stderr=b"",
        )

    def terminate(self, container_id: str) -> None:
        self.calls.append(("terminate", container_id))

    def remove(self, container_id: str) -> None:
        self.calls.append(("remove", container_id))

    def container_absent(self, container_id: str) -> bool:
        self.calls.append(("container_absent", container_id))
        return not self.residue

    def owner_absent(self, owner_nonce: str) -> bool:
        self.calls.append(("owner_absent", owner_nonce))
        return not self.residue


class FakeBroker:
    def __init__(self, engine, root, control, secret):
        root.mkdir(mode=0o777)
        root.chmod(0o777)
        challenge = {
            "schema": "text-to-cad.agent-broker-challenge/1",
            "challenge": control["challenge"],
            **{key: control[key] for key in supervisor.IDENTITY_KEYS},
        }
        engine.broker_proof = {
            "schema": "text-to-cad.agent-broker-proof/1",
            "challenge": control["challenge"],
            "brokerMac": hmac.new(
                secret, supervisor.canonical_json_bytes(challenge), hashlib.sha256
            ).hexdigest(),
            **{key: control[key] for key in supervisor.IDENTITY_KEYS},
        }
        self.error = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class DevelopmentSupervisorTests(unittest.TestCase):
    def test_docker_adapter_creates_exact_inert_container_without_network_creation(self) -> None:
        spec = supervisor.ContainerSpec(
            image_id="sha256:" + "1" * 64,
            name="fixed-job",
            entrypoint=supervisor.FIXED_ENTRYPOINT,
            command=(),
            user="65532:65532",
            read_only_root=True,
            network_mode="job-internal",
            mounts=(supervisor.Mount(Path("/host/input"), "/guest/input", True),),
            labels={"owner": "nonce"},
        )
        engine = supervisor.DockerEngine("docker-test")
        completed = subprocess.CompletedProcess([], 0, b"container-exact\n", b"")
        with mock.patch.object(engine, "_run", return_value=completed) as run:
            self.assertEqual(engine.create(spec), "container-exact")
        args = run.call_args.args
        self.assertEqual(args[0], "create")
        self.assertIn("never", args)
        self.assertIn("job-internal", args)
        self.assertIn("no-new-privileges", args)
        self.assertIn("type=bind,src=/host/input,dst=/guest/input,readonly", args)
        self.assertNotIn("network create", " ".join(args))

    def test_fixed_candidate_request_rejects_mutable_or_noncanonical_workload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = root / "workload.json"
            workload.write_bytes(b'["/usr/bin/true"]')
            output = root / "run"
            output.mkdir()
            request = supervisor.fixed_candidate_request(
                repo_root=supervisor.REPO_ROOT,
                image_id="sha256:" + "9" * 64,
                output_dir=output,
                workload_path=workload,
            )
            self.assertEqual(
                request.image_manifest_digest,
                "sha256:a64ae96f4703bb8dfdbce1159106f606f1f00e1bf05991fa4bcabe27a0bfedc2",
            )
            self.assertEqual(request.workload, ("/usr/bin/true",))
            workload.write_bytes(b'[ "/usr/bin/true" ]\n')
            with self.assertRaisesRegex(supervisor.SupervisorError, "canonical"):
                supervisor.fixed_candidate_request(
                    repo_root=supervisor.REPO_ROOT,
                    image_id="sha256:" + "9" * 64,
                    output_dir=output,
                    workload_path=workload,
                )

    def _request(self, root: Path, *, timeout_seconds: int = 2700):
        source = root / "source"
        input_root = root / "input"
        output = root / "output"
        source.mkdir()
        input_root.mkdir()
        output.mkdir()
        shutil.copyfile(
            supervisor.REPO_ROOT / "models/agent-runtime/cup_cup_033/source/cup_cup_033.implicit.js",
            source / "cup_cup_033.implicit.js",
        )
        shutil.copyfile(
            supervisor.REPO_ROOT / "models/agent-runtime/cup_cup_033/input/cup_cup_033.ply",
            input_root / "cup_cup_033.ply",
        )
        return supervisor.DevelopmentRequest(
            image_id="sha256:" + "1" * 64,
            image_manifest_digest="sha256:" + "2" * 64,
            image_config_digest="sha256:" + "3" * 64,
            runtime_manifest_digest="sha256:" + "4" * 64,
            entrypoint_digest="sha256:" + "5" * 64,
            source_dir=source,
            input_dir=input_root,
            output_dir=output,
            workload=("/usr/bin/true",),
            timeout_seconds=timeout_seconds,
        )

    def test_single_job_handshake_uses_closed_mounts_and_publishes_after_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = FakeEngine()
            receipt = supervisor.execute(
                self._request(root), engine=engine,
                broker_factory=lambda path, control, secret: FakeBroker(engine, path, control, secret),
            )

            assert engine.spec is not None
            self.assertEqual(engine.spec.entrypoint, supervisor.FIXED_ENTRYPOINT)
            self.assertEqual(engine.spec.command, ())
            self.assertTrue(engine.spec.read_only_root)
            self.assertEqual(engine.spec.network_mode, "none")
            self.assertEqual(engine.spec.user, "65532:65532")
            self.assertEqual(
                {(mount.target, mount.read_only) for mount in engine.spec.mounts},
                {
                    ("/run/text-to-cad-agent/control", True),
                    ("/run/text-to-cad-agent/source", True),
                    ("/run/text-to-cad-agent/input", True),
                    ("/run/text-to-cad-agent/home", False),
                    ("/run/text-to-cad-agent/cache", False),
                    ("/run/text-to-cad-agent/tmp", False),
                    ("/run/text-to-cad-agent/work", False),
                    ("/run/text-to-cad-agent/output", False),
                    ("/run/meshshot-browser", False),
                },
            )
            self.assertEqual(engine.calls.count("create"), 1)
            self.assertEqual(engine.mount_modes["/run/text-to-cad-agent/control"], 0o555)
            self.assertEqual(engine.control_mode, 0o444)
            for mount in engine.spec.mounts:
                if not mount.read_only:
                    self.assertEqual(engine.mount_modes[mount.target], 0o777)
            self.assertNotIn(("terminate", "container-exact"), engine.calls)
            self.assertLess(
                engine.calls.index(("container_absent", "container-exact")),
                engine.calls.index(("owner_absent", receipt["ownerNonce"])),
            )
            self.assertEqual(json.loads(engine.release), {
                "brokerProofDigest": receipt["brokerProofDigest"],
                "release": True,
                "schema": "text-to-cad.agent-entrypoint-release/1",
            })
            self.assertEqual(receipt["status"], "development-succeeded")
            self.assertEqual(receipt["attemptCount"], 1)
            self.assertEqual(receipt["providerDispatchCount"], 0)
            output = self._request_output(root)
            self.assertTrue((output / "supervisor" / "entrypoint.stdout.jsonl").is_file())
            self.assertTrue((output / "supervisor" / "entrypoint.stderr").is_file())
            self.assertEqual(
                json.loads((output / "supervisor" / "terminal.json").read_bytes()),
                receipt,
            )

    def _request_output(self, root: Path) -> Path:
        return root / "output"

    def test_timeout_is_bounded_and_terminates_once_without_job_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = FakeEngine(timeout=True)
            with self.assertRaisesRegex(supervisor.SupervisorError, "timeout"):
                supervisor.execute(
                    self._request(root, timeout_seconds=7), engine=engine,
                    broker_factory=lambda path, control, secret: FakeBroker(engine, path, control, secret),
                )
            self.assertEqual(engine.calls.count("create"), 1)
            self.assertEqual(engine.calls.count(("terminate", "container-exact")), 1)
            self.assertEqual(engine.calls.count(("remove", "container-exact")), 1)
            failure = json.loads((root / "output/supervisor/terminal.json").read_bytes())
            self.assertTrue((root / "output/supervisor/entrypoint.stdout.jsonl").is_file())
            self.assertTrue((root / "output/supervisor/entrypoint.stderr").is_file())
            self.assertEqual(failure["status"], "development-failed")
            self.assertEqual(failure["failureCheck"], "timeout")
            self.assertEqual(failure["attemptCount"], 1)
            self.assertEqual(failure["providerDispatchCount"], 0)

    def test_precreated_internal_network_is_consumed_but_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = FakeEngine()
            request = replace(
                self._request(root), internal_network="t2c-job-private-123"
            )
            supervisor.execute(
                request, engine=engine,
                broker_factory=lambda path, control, secret: FakeBroker(engine, path, control, secret),
            )
            assert engine.spec is not None
            self.assertEqual(engine.spec.network_mode, "t2c-job-private-123")
            self.assertFalse(any(call == "create-network" for call in engine.calls))

    def test_residue_fails_closed_and_retains_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = FakeEngine(residue=True)
            with self.assertRaisesRegex(supervisor.SupervisorError, "absence"):
                supervisor.execute(
                    self._request(root), engine=engine,
                    broker_factory=lambda path, control, secret: FakeBroker(engine, path, control, secret),
                )
            failure = json.loads((root / "output/supervisor/terminal.json").read_bytes())
            self.assertEqual(failure["failureCheck"], "cleanup-absence")
            self.assertFalse(failure["containerAbsent"])
            self.assertFalse(failure["ownerLabelsAbsent"])


if __name__ == "__main__":
    unittest.main()
