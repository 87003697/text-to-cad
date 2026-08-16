"""Executable contract tests for the THROWAWAY SAR-003 Agent seam."""

from __future__ import annotations

from dataclasses import replace
import io
from pathlib import Path
import signal
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[4]
PROTOTYPE = REPO / "packages/meshshot/prototypes/agent_runtime_boundary"
sys.path.insert(0, str(PROTOTYPE))
import boundary  # noqa: E402
import contract  # noqa: E402
import matrix  # noqa: E402
import process_group  # noqa: E402


class FakeGroupAdapter:
    def __init__(self, group_states: list[bool]) -> None:
        self.group_states = group_states
        self.calls: list[object] = []
        self.now = 0.0

    def spawn(self, argv, cwd, env, stdout, stderr):
        self.calls.append("spawn-session")
        return object(), 42

    def wait(self, process):
        self.calls.append("wait-leader")
        return 0

    def group_exists(self, pgid):
        self.calls.append("inspect-group")
        return self.group_states.pop(0) if self.group_states else False

    def signal_group(self, pgid, signum):
        self.calls.append(("signal-group", signum))

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class AgentRuntimeBoundaryTests(unittest.TestCase):
    def make_spec(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return matrix.fixture_spec(Path(temporary.name))

    def test_executable_matrix_closes_all_cases(self) -> None:
        result = matrix.matrix()
        self.assertEqual(result["caseCount"], 27)
        self.assertEqual(result["passCount"], 27)
        self.assertEqual(result["verdict"], "ADOPT_WITH_FORMAL_VERIFICATION_GATES")
        self.assertEqual(result["realOciRun"], "NOT_RUN")
        self.assertFalse(result["agentRuntimeVerified"])

    def test_success_executes_public_lifecycle_in_order(self) -> None:
        spec = self.make_spec()
        receipt = boundary.run_job(spec, matrix.ScriptedAdapter(spec))
        self.assertEqual(receipt.status, "succeeded")
        self.assertTrue(receipt.absence_proved)
        self.assertEqual(receipt.calls, (
            "inspect-image", "provision-broker-secret", "create-inert",
            "inspect-container", "start-entrypoint", "read-ready",
            "write-challenge", "read-preflight", "write-release",
            "read-terminal", "write-ack", "remove-exact",
            "cleanup-owner-labeled", "cleanup-broker-volume",
            "cleanup-private-tree", "prove-container-absence",
            "prove-owner-label-absence", "prove-broker-volume-absence",
            "prove-private-tree-absence",
        ))

    def test_foreign_returned_id_never_becomes_delete_authority(self) -> None:
        spec = self.make_spec()
        adapter = matrix.ScriptedAdapter(spec)
        adapter.container = replace(adapter.container, resource_id="d" * 64)
        receipt = boundary.run_job(spec, adapter)
        self.assertEqual(receipt.failure_check, "container-ownership")
        self.assertNotIn("start-entrypoint", receipt.calls)
        self.assertNotIn("remove-exact", receipt.calls)
        self.assertIn("cleanup-owner-labeled", receipt.calls)
        self.assertNotIn("prove-container-absence", receipt.calls)
        self.assertTrue(receipt.owner_labels_absent)

    def test_owned_but_misconfigured_container_is_removed_by_exact_id(self) -> None:
        spec = self.make_spec()
        adapter = matrix.ScriptedAdapter(spec)
        adapter.container = replace(adapter.container, read_only_root=False)
        receipt = boundary.run_job(spec, adapter)
        self.assertEqual(receipt.failure_check, "inert-container")
        self.assertIn("remove-exact", receipt.calls)
        self.assertNotIn("start-entrypoint", receipt.calls)

    def test_workload_is_closed_and_bound_before_any_start_or_release(self) -> None:
        spec = self.make_spec()
        cases = (
            replace(spec, workload=("relative",)),
            replace(spec, workload=("/opt/text-to-cad/bin/substitute",)),
        )
        for candidate in cases:
            with self.subTest(workload=candidate.workload):
                adapter = matrix.ScriptedAdapter(candidate)
                receipt = boundary.run_job(candidate, adapter)
                self.assertEqual(receipt.failure_check, "workload-identity")
                self.assertNotIn("create-inert", receipt.calls)
                self.assertNotIn("start-entrypoint", receipt.calls)
                self.assertNotIn("write-release", receipt.calls)

    def test_image_identity_is_outer_attested_before_create(self) -> None:
        spec = self.make_spec()
        adapter = matrix.ScriptedAdapter(spec)
        adapter.image = None
        receipt = boundary.run_job(spec, adapter)
        self.assertEqual(receipt.failure_check, "image-identity")
        self.assertNotIn("create-inert", receipt.calls)

    def test_broker_mac_requires_outer_secret_and_exact_identity(self) -> None:
        spec = self.make_spec()
        wrong = matrix.ScriptedAdapter(spec)
        wrong.broker_key_override = b"x" * 32
        receipt = boundary.run_job(spec, wrong)
        self.assertEqual(receipt.failure_check, "broker-proof")
        self.assertNotIn("write-release", receipt.calls)
        other = replace(spec.identity, job_id="job-b")
        observed = contract.broker_mac(spec.broker_secret, other, spec.challenge)
        self.assertFalse(contract.verify_broker_mac(
            spec.broker_secret, spec.identity, spec.challenge, observed,
        ))

    def test_each_private_resource_residue_dominates_success(self) -> None:
        fields = (
            ("container_absent", "container_absent"),
            ("owner_absent", "owner_labels_absent"),
            ("broker_absent", "broker_volume_absent"),
            ("tree_absent", "private_tree_absent"),
        )
        for adapter_field, receipt_field in fields:
            with self.subTest(resource=adapter_field):
                spec = self.make_spec()
                adapter = matrix.ScriptedAdapter(spec)
                setattr(adapter, adapter_field, False)
                receipt = boundary.run_job(spec, adapter)
                self.assertEqual(receipt.failure_check, "retained-resource")
                self.assertFalse(getattr(receipt, receipt_field))
                self.assertTrue(receipt.retained_resource)

    def test_process_group_descendant_is_killed_and_cannot_succeed(self) -> None:
        adapter = FakeGroupAdapter([True, True, False])
        result = process_group.run_workload_group(
            ("/fixed/workload",), cwd="/fixed", env={},
            stdout=io.BytesIO(), stderr=io.BytesIO(), adapter=adapter,
        )
        self.assertEqual(result.returncode, 125)
        self.assertTrue(result.descendant_residue)
        self.assertTrue(result.group_absent)
        self.assertIn(("signal-group", signal.SIGTERM), adapter.calls)

        spec = self.make_spec()
        lifecycle = matrix.ScriptedAdapter(spec)
        lifecycle.descendant_residue = True
        receipt = boundary.run_job(spec, lifecycle)
        self.assertEqual(receipt.failure_check, "workload-process-group")
        self.assertNotIn("write-ack", receipt.calls)

    def test_formal_scanner_detects_renamed_browser_elf_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "surface"
            root.mkdir()
            renamed = root / "innocent-tool"
            renamed.write_bytes(b"\x7fELF" + b"HeadlessChrome")
            renamed.chmod(0o755)
            findings = matrix.discover_browser_artifacts((root,))
        self.assertEqual(findings[0]["kind"], "executable")

    def test_create_configuration_excludes_agent_secret_and_docker(self) -> None:
        spec = self.make_spec()
        joined = " ".join(boundary.build_create_argv(spec))
        self.assertNotIn("docker.sock,dst=", joined)
        self.assertNotIn(spec.broker_secret.hex(), joined)

    def test_tree_digest_is_shared_and_workload_digest_is_ordered(self) -> None:
        self.assertIs(boundary.canonical_tree_digest, contract.canonical_tree_digest)
        self.assertNotEqual(
            contract.workload_digest(("/bin/tool", "a")),
            contract.workload_digest(("/bin/tool", "b")),
        )

    def test_unsafe_red_releases_before_inspection(self) -> None:
        spec = self.make_spec()
        calls = matrix.unsafe_red(matrix.ScriptedAdapter(spec))
        self.assertLess(calls.index("write-release"), calls.index("inspect-image"))


if __name__ == "__main__":
    unittest.main()
