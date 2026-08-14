"""Contract tests for the throwaway one-command harness seam."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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

        def fake_run(*args: str, check: bool = True, timeout: int = 600):
            del timeout
            calls.append(args)
            if args[:2] == ("network", "create"):
                return completed(stdout="network-id\n")
            if args[0] == "create":
                return completed(stdout="container-id\n")
            if args[0] == "run":
                raise harness_module.HarnessError("run failed after network creation")
            if args[:2] == ("start", "container-id"):
                result = completed(returncode=1, stderr="start failed")
                if check:
                    raise harness_module.HarnessError("start failed")
                return result
            if args[:2] in (("stop", "container-id"), ("rm", "container-id")):
                return completed()
            if args[:3] == ("container", "inspect", "container-id"):
                return completed(returncode=1, stderr="not found")
            if args[:3] == ("network", "rm", "network-id"):
                return completed()
            if args[:3] == ("network", "inspect", "network-id"):
                return completed(returncode=1, stderr="not found")
            return completed()

        harness.run = fake_run  # type: ignore[method-assign]
        with self.assertRaises(harness_module.HarnessError):
            harness.start_job("fails")
        cleanup = harness.cleanup_all()

        self.assertEqual([], cleanup["failures"])
        self.assertTrue(all(item["absent"] for item in cleanup["absenceProofs"]))
        self.assertIn(("rm", "container-id"), calls)
        self.assertIn(("network", "rm", "network-id"), calls)

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

    def test_incomplete_evidence_is_fail_closed(self) -> None:
        predicates = harness_module.predicate_matrix({})
        self.assertGreaterEqual(len(predicates), 25)
        self.assertFalse(all(predicates.values()))
        self.assertFalse(predicates["p1.readonly_root"])
        self.assertFalse(predicates["p2.final_public_png_profile_views_evidence_parity"])
        self.assertFalse(predicates["p3.all_negative_cross_checks"])


if __name__ == "__main__":
    unittest.main()
