"""Executable contract tests for the THROWAWAY SAR-003 Agent seam."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[4]
PROTOTYPE = REPO / "packages/meshshot/prototypes/agent_runtime_boundary"
sys.path.insert(0, str(PROTOTYPE))
import contract  # noqa: E402

SPEC = importlib.util.spec_from_file_location("agent_boundary", PROTOTYPE / "boundary.py")
assert SPEC and SPEC.loader
boundary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = boundary
SPEC.loader.exec_module(boundary)


class AgentRuntimeBoundaryTests(unittest.TestCase):
    def make_spec(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return boundary._fixture_spec(Path(temporary.name))

    def test_executable_matrix_closes_all_cases(self) -> None:
        result = boundary.matrix()
        self.assertEqual(result["caseCount"], 21)
        self.assertEqual(result["passCount"], 21)
        self.assertEqual(result["verdict"], "ADOPT_WITH_FORMAL_VERIFICATION_GATES")
        self.assertEqual(result["realOciRun"], "NOT_RUN")
        self.assertFalse(result["agentRuntimeVerified"])

    def test_success_executes_public_lifecycle_in_order(self) -> None:
        spec = self.make_spec()
        adapter = boundary.ScriptedAdapter(spec)
        receipt = boundary.run_job(spec, adapter)
        self.assertEqual(receipt.status, "succeeded")
        self.assertEqual(receipt.workload_status, 0)
        self.assertEqual(receipt.calls, (
            "inspect-image", "provision-broker-secret", "create-inert", "inspect-container", "start-entrypoint",
            "read-ready", "write-challenge", "read-preflight", "write-release",
            "read-terminal", "write-ack", "remove-exact", "cleanup-owned", "prove-id-absence",
            "prove-owner-absence",
        ))

    def test_image_identity_is_outer_attested_before_create(self) -> None:
        for observation in (
            None,
            boundary.ImageObservation(
                frozenset({"sha256:" + "9" * 64}), "sha256:" + "2" * 64,
                "sha256:" + "3" * 64,
            ),
            boundary.ImageObservation(
                frozenset({"sha256:" + "1" * 64}), "sha256:" + "9" * 64,
                "sha256:" + "3" * 64,
            ),
        ):
            with self.subTest(observation=observation):
                spec = self.make_spec()
                adapter = boundary.ScriptedAdapter(spec)
                adapter.image = observation
                receipt = boundary.run_job(spec, adapter)
                self.assertEqual(receipt.failure_check, "image-identity")
                self.assertNotIn("create-inert", receipt.calls)
                self.assertNotIn("start-entrypoint", receipt.calls)

    def test_returned_id_substitution_never_starts_and_cleans_returned_id(self) -> None:
        spec = self.make_spec()
        adapter = boundary.ScriptedAdapter(spec)
        adapter.container = replace(adapter.container, resource_id="d" * 64)
        receipt = boundary.run_job(spec, adapter)
        self.assertEqual(receipt.failure_check, "inert-container")
        self.assertNotIn("start-entrypoint", receipt.calls)
        self.assertIn("remove-exact", receipt.calls)
        self.assertTrue(receipt.absence_proved)

    def test_broker_mac_requires_outer_secret_and_exact_job_challenge(self) -> None:
        spec = self.make_spec()
        valid = boundary.ScriptedAdapter(spec)
        self.assertEqual(boundary.run_job(spec, valid).status, "succeeded")

        wrong_key = boundary.ScriptedAdapter(spec)
        wrong_key.broker_key_override = b"x" * 32
        rejected = boundary.run_job(spec, wrong_key)
        self.assertEqual(rejected.failure_check, "broker-proof")
        self.assertNotIn("write-release", rejected.calls)

        other_identity = replace(spec.identity, job_id="job-b")
        echo_mac = contract.broker_mac(spec.broker_secret, other_identity, spec.challenge)
        self.assertFalse(contract.verify_broker_mac(
            spec.broker_secret, spec.identity, spec.challenge, echo_mac,
        ))
        self.assertFalse(contract.verify_broker_mac(
            spec.broker_secret, spec.identity, spec.challenge, spec.challenge,
        ))

    def test_terminal_and_cleanup_failures_dominate_workload_success(self) -> None:
        spec = self.make_spec()
        terminal = boundary.ScriptedAdapter(spec)
        terminal.fail_read = "terminal"
        terminal_receipt = boundary.run_job(spec, terminal)
        self.assertTrue(terminal_receipt.workload_released)
        self.assertEqual(terminal_receipt.failure_check, "terminal-publication")
        self.assertTrue(terminal_receipt.absence_proved)

        retained = boundary.ScriptedAdapter(spec)
        retained.absent = False
        retained_receipt = boundary.run_job(spec, retained)
        self.assertEqual(retained_receipt.failure_check, "retained-resource")
        self.assertTrue(retained_receipt.retained_resource)

    def test_formal_scanner_detects_renamed_browser_elf_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "surface"
            root.mkdir()
            renamed = root / "innocent-tool"
            renamed.write_bytes(b"\x7fELF" + b"\0" * 32 + b"HeadlessChrome")
            renamed.chmod(0o755)
            findings = boundary.discover_browser_artifacts((root,))
        self.assertEqual(findings, [{
            "kind": "executable", "target": "/agent/surface/innocent-tool",
            "mask": "dev-null",
        }])

    def test_create_configuration_has_no_agent_secret_or_docker_authority(self) -> None:
        spec = self.make_spec()
        argv = boundary.build_create_argv(spec)
        joined = " ".join(argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--network none", joined)
        self.assertIn("--cap-drop ALL", joined)
        self.assertNotIn("docker.sock,dst=", joined)
        self.assertNotIn(spec.broker_secret.hex(), joined)
        self.assertEqual(argv[-1], spec.image.reference)

    def test_tree_digest_has_one_shared_authority_and_binds_mode(self) -> None:
        self.assertIs(boundary.canonical_tree_digest, contract.canonical_tree_digest)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "file"
            target.write_text("same", encoding="utf-8")
            before = contract.canonical_tree_digest(root)
            target.chmod(0o744)
            after = contract.canonical_tree_digest(root)
        self.assertNotEqual(before, after)

    def test_unsafe_red_releases_before_inspection(self) -> None:
        spec = self.make_spec()
        calls = boundary.run_unsafe_baseline(boundary.ScriptedAdapter(spec))
        self.assertLess(calls.index("write-release"), calls.index("inspect-image"))


if __name__ == "__main__":
    unittest.main()
