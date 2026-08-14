"""Contract tests for the throwaway one-command harness seam."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROTOTYPE_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("browser_sidecar_harness", PROTOTYPE_DIR / "harness.py")
assert SPEC and SPEC.loader
harness_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = harness_module
SPEC.loader.exec_module(harness_module)


def completed(*, stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class ExactResourceLedgerTests(unittest.TestCase):
    def make_harness(self) -> harness_module.Harness:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return harness_module.Harness(
            docker_host="unix:///fake.sock",
            repo=PROTOTYPE_DIR,
            evidence_dir=Path(temporary.name),
        )

    def test_start_failure_after_create_is_terminally_cleaned_by_exact_id(self) -> None:
        harness = self.make_harness()
        calls: list[tuple[str, ...]] = []
        network_id = "e" * 64
        container_id = "f" * 64
        container_inspects = 0

        def fake_run(*args: str, check: bool = True, timeout: int = 600):
            nonlocal container_inspects
            del timeout
            calls.append(args)
            if args[:2] == ("network", "create"):
                return completed(stdout=f"{network_id}\n")
            if args[:3] == ("network", "inspect", f"{harness_module.PREFIX}-fails"):
                return completed(stdout=json.dumps([{
                    "Id": network_id,
                    "Name": f"{harness_module.PREFIX}-fails",
                    "Labels": {harness_module.OWNERSHIP_LABEL: harness.ownership_token},
                }]))
            if args[0] == "create":
                return completed(stdout=f"{container_id}\n")
            if args[0] == "run":
                raise harness_module.HarnessError("run failed after network creation")
            if args[:3] == ("container", "inspect", f"{harness_module.PREFIX}-fails-sidecar"):
                return completed(stdout=json.dumps([{
                    "Id": container_id,
                    "Name": f"/{harness_module.PREFIX}-fails-sidecar",
                    "Config": {"Labels": {harness_module.OWNERSHIP_LABEL: harness.ownership_token}},
                }]))
            if args[:2] == ("start", container_id):
                result = completed(returncode=1, stderr="start failed")
                if check:
                    raise harness_module.HarnessError("start failed")
                return result
            if args[:2] in (("stop", container_id), ("rm", container_id)):
                return completed()
            if args[:3] == ("container", "inspect", container_id):
                container_inspects += 1
                if container_inspects == 1 and "--format" in args:
                    return completed(stdout="false\n")
                return completed(returncode=1, stderr="not found")
            if args[:3] == ("network", "rm", network_id):
                return completed()
            if args[:3] == ("network", "inspect", network_id):
                return completed(returncode=1, stderr="not found")
            return completed()

        harness.run = fake_run  # type: ignore[method-assign]
        with self.assertRaises(harness_module.HarnessError):
            harness.start_job("fails")
        cleanup = harness.cleanup_all()

        self.assertEqual([], cleanup["failures"])
        self.assertTrue(all(item["absent"] for item in cleanup["absenceProofs"]))
        self.assertIn(("rm", container_id), calls)
        self.assertIn(("network", "rm", network_id), calls)

    def test_cleanup_preserves_first_failure_but_attempts_every_resource(self) -> None:
        harness = self.make_harness()
        calls: list[tuple[str, ...]] = []
        harness.ledger.register_network(name="network-a", resource_id="network-id")
        harness.ledger.register_container(name="container-a", resource_id="container-id")

        def fake_run(*args: str, check: bool = True, timeout: int = 600):
            del check, timeout
            calls.append(args)
            if args[:2] == ("rm", "container-id"):
                return completed(returncode=1, stderr="container locked")
            if args[:3] == ("container", "inspect", "container-id"):
                return completed(stdout="still-present\n")
            if args[:3] == ("network", "inspect", "network-id"):
                return completed(returncode=1, stderr="not found")
            return completed()

        harness.run = fake_run  # type: ignore[method-assign]
        cleanup = harness.cleanup_all()

        self.assertEqual("container locked", cleanup["firstFailure"]["stderr"])
        self.assertIn(("network", "rm", "network-id"), calls)
        proof = {item["id"]: item for item in cleanup["absenceProofs"]}
        self.assertFalse(proof["container-id"]["absent"])
        self.assertTrue(proof["network-id"]["absent"])

    def test_cleanup_inspect_exceptions_do_not_skip_later_resources_or_absence_evidence(self) -> None:
        harness = self.make_harness()
        calls: list[tuple[str, ...]] = []
        harness.ledger.register_container(name="container-a", resource_id="container-a-id")
        harness.ledger.register_container(name="container-b", resource_id="container-b-id")
        harness.ledger.register_network(name="network-a", resource_id="network-a-id")
        inspect_counts = {"container-a-id": 0, "container-b-id": 0}

        def fake_run(*args: str, check: bool = True, timeout: int = 600):
            del check, timeout
            calls.append(args)
            if args[:2] == ("container", "inspect"):
                resource_id = args[2]
                inspect_counts[resource_id] += 1
                if resource_id == "container-a-id" and inspect_counts[resource_id] == 1:
                    raise subprocess.TimeoutExpired(args, 30)
                if resource_id == "container-b-id" and inspect_counts[resource_id] == 2:
                    raise RuntimeError("absence inspect transport failed")
                if inspect_counts[resource_id] == 1:
                    return completed(stdout="true\n")
                return completed(returncode=1, stderr="not found")
            if args[:2] == ("network", "inspect"):
                return completed(returncode=1, stderr="not found")
            return completed()

        harness.run = fake_run  # type: ignore[method-assign]
        cleanup = harness.cleanup_all()

        self.assertIn(("rm", "container-a-id"), calls)
        self.assertIn(("rm", "container-b-id"), calls)
        self.assertIn(("network", "rm", "network-a-id"), calls)
        self.assertEqual("container", cleanup["firstFailure"]["kind"])
        proofs = {item["id"]: item for item in cleanup["absenceProofs"]}
        self.assertTrue(proofs["container-a-id"]["absent"])
        self.assertFalse(proofs["container-b-id"]["absent"])
        self.assertTrue(proofs["container-b-id"]["inspectionError"])
        self.assertTrue(proofs["network-a-id"]["absent"])

    def test_create_output_loss_recovers_only_exact_owned_container(self) -> None:
        harness = self.make_harness()
        calls: list[tuple[str, ...]] = []
        recovered_id = "a" * 64

        def fake_run(*args: str, check: bool = True, timeout: int = 600):
            del check, timeout
            calls.append(args)
            if args[0] == "create":
                return completed(stdout="")
            if args[:3] == ("container", "inspect", "owned-name"):
                return completed(stdout=json.dumps([{
                    "Id": recovered_id,
                    "Name": "/owned-name",
                    "Config": {"Labels": {harness_module.OWNERSHIP_LABEL: harness.ownership_token}},
                }]))
            return completed(returncode=1, stderr="not found")

        harness.run = fake_run  # type: ignore[method-assign]
        container_id = harness.create_container("owned-name", "image:fixed")

        self.assertEqual(recovered_id, container_id)
        self.assertEqual([recovered_id], [item.resource_id for item in harness.ledger.containers])
        create_call = next(call for call in calls if call[0] == "create")
        self.assertIn("--label", create_call)
        self.assertIn(f"{harness_module.OWNERSHIP_LABEL}={harness.ownership_token}", create_call)

        harness.run = lambda *args, **kwargs: completed(stdout=json.dumps([{
            "Id": "b" * 64,
            "Name": "/foreign-name",
            "Config": {"Labels": {harness_module.OWNERSHIP_LABEL: "some-other-owner"}},
        }])) if args[:3] == ("container", "inspect", "foreign-name") else completed(stdout="")  # type: ignore[method-assign]
        with self.assertRaises(harness_module.HarnessError):
            harness.create_container("foreign-name", "image:fixed")
        self.assertNotIn("b" * 64, [item.resource_id for item in harness.ledger.containers])

    def test_network_create_output_loss_recovers_only_exact_owned_network(self) -> None:
        harness = self.make_harness()
        recovered_id = "c" * 64

        def fake_run(*args: str, check: bool = True, timeout: int = 600):
            del check, timeout
            if args[:2] == ("network", "create"):
                return completed(stdout="")
            if args[:3] == ("network", "inspect", "owned-network"):
                return completed(stdout=json.dumps([{
                    "Id": recovered_id,
                    "Name": "owned-network",
                    "Labels": {harness_module.OWNERSHIP_LABEL: harness.ownership_token},
                }]))
            return completed(returncode=1, stderr="not found")

        harness.run = fake_run  # type: ignore[method-assign]
        network_id = harness.create_network("owned-network", "--internal")

        self.assertEqual(recovered_id, network_id)
        self.assertEqual([recovered_id], [item.resource_id for item in harness.ledger.networks])

    def test_detached_process_is_registered_before_stdin_validation_or_write(self) -> None:
        class BrokenStdin:
            def write(self, value: str) -> int:
                del value
                raise BrokenPipeError("client exited before request write")

            def close(self) -> None:
                pass

        class FakeProcess:
            def __init__(self, stdin):
                self.stdin = stdin
                self.stdout = io.StringIO()
                self.stderr = io.StringIO()
                self.returncode = 1

            def communicate(self, timeout: int):
                del timeout
                return "", "client exited"

        for stdin, expected in ((BrokenStdin(), BrokenPipeError), (None, harness_module.HarnessError)):
            with self.subTest(expected=expected.__name__):
                harness = self.make_harness()
                process = FakeProcess(stdin)
                with mock.patch.object(harness_module.subprocess, "Popen", return_value=process):
                    with self.assertRaises(expected):
                        harness.start_detached("detached", "d" * 64, {"request": True})
                self.assertEqual(1, len(harness.detached_runs))
                cleanup = harness.cleanup_all()
                self.assertTrue(harness.detached_runs[0].finished)
                self.assertEqual([], cleanup["failures"])

    def test_controlled_interrupt_raises_before_cleanup_but_not_during_cleanup(self) -> None:
        state = harness_module.InterruptState()
        with self.assertRaises(harness_module.HarnessInterrupted):
            state.handle(signal.SIGTERM, None)
        self.assertEqual("SIGTERM", state.signal_name)

        state.cleanup_started = True
        state.handle(signal.SIGINT, None)
        self.assertEqual("SIGINT", state.signal_name)

    def test_default_build_rejects_dirty_tracked_or_untracked_source_before_docker(self) -> None:
        harness = self.make_harness()
        docker_calls: list[tuple[str, ...]] = []
        harness.run = lambda *args, **kwargs: docker_calls.append(args) or completed()  # type: ignore[method-assign]
        with mock.patch.object(
            harness_module.subprocess,
            "run",
            return_value=completed(stdout=" M dirty.py\n?? untracked.step\n"),
        ):
            with self.assertRaises(harness_module.HarnessError):
                harness.build()
        self.assertEqual([], docker_calls)

    def test_image_revision_validation_supports_default_and_exact_skip_build_sets(self) -> None:
        current = "1" * 40
        legacy = "2" * 40
        images = {
            "sidecar": {"labels": {"org.opencontainers.image.revision": current}},
            "agent": {"labels": {"org.opencontainers.image.revision": current}},
            "legacy": {"labels": {"org.opencontainers.image.revision": legacy}},
        }
        harness_module.validate_image_revisions(images, {
            "sidecar": current,
            "agent": current,
            "legacy": legacy,
        })
        with self.assertRaises(harness_module.HarnessError):
            harness_module.validate_image_revisions(images, {
                "sidecar": current,
                "agent": current,
                "legacy": current,
            })

    def test_incomplete_evidence_is_fail_closed(self) -> None:
        predicates = harness_module.predicate_matrix({})
        self.assertGreaterEqual(len(predicates), 25)
        self.assertFalse(all(predicates.values()))
        self.assertFalse(predicates["p1.readonly_root"])
        self.assertFalse(predicates["p2.final_public_png_profile_views_evidence_parity"])
        self.assertFalse(predicates["p3.all_negative_cross_checks"])


if __name__ == "__main__":
    unittest.main()
