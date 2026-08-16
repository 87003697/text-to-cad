"""Provider-free contract tests for the THROWAWAY SAR-003 seam."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_boundary", ROOT / "boundary.py")
assert SPEC and SPEC.loader
boundary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = boundary
SPEC.loader.exec_module(boundary)


class BoundaryTests(unittest.TestCase):
    def test_required_adversarial_matrix_is_green(self) -> None:
        result = boundary.matrix()
        self.assertEqual(result["caseCount"], 17)
        self.assertEqual(result["passCount"], 17)
        self.assertEqual(result["verdict"], "ADOPT_WITH_FORMAL_VERIFICATION_GATES")
        self.assertFalse(result["agentRuntimeVerified"])
        self.assertEqual(result["realOciRun"], "NOT_RUN")

    def test_before_release_faults_never_start_workload(self) -> None:
        for case in boundary.PRECREATE_REJECTIONS | boundary.INERT_REJECTIONS | boundary.PREFLIGHT_REJECTIONS:
            with self.subTest(case=case):
                self.assertFalse(boundary.proposed_green(case)["workloadStarted"])

    def test_terminal_failure_and_cleanup_residue_dominate_success(self) -> None:
        terminal = boundary.proposed_green("terminal_publication_failure")
        retained = boundary.proposed_green("cleanup_residue")
        self.assertEqual(terminal["status"], "failed")
        self.assertTrue(terminal["absenceProved"])
        self.assertEqual(retained["status"], "failed")
        self.assertTrue(retained["retainedResource"])
        self.assertFalse(retained["absenceProved"])

    def test_create_argv_has_no_docker_or_network_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ("meshshot-agent-boundary-prototype-job-a-" + "b" * 12)
            root.mkdir(mode=0o700)
            names = ("source", "input", "control", "home", "cache", "tmp", "work", "output")
            paths = {name: root / name for name in names}
            for path in paths.values():
                path.mkdir()
            argv = boundary.build_create_argv(
                docker_host="unix:///private/outer/docker.sock",
                image_digest="sha256:" + "a" * 64,
                job_id="job-a", owner_nonce="b" * 32,
                name="meshshot-agent-boundary-prototype-job-a-" + "b" * 12,
                broker_volume="meshshot-agent-boundary-prototype-job-a-" + "b" * 12 + "-broker",
                paths=boundary.JobPaths(**paths),
            )
        joined = " ".join(argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--network none", joined)
        self.assertIn("--cap-drop ALL", joined)
        self.assertNotIn("/var/run/docker.sock", joined)
        self.assertNotIn("/private/outer/docker.sock,dst=", joined)
        self.assertIn("type=volume,src=meshshot-agent-boundary-prototype-job-a-" + "b" * 12 + "-broker", joined)
        self.assertEqual(argv[-1], "sha256:" + "a" * 64)
        self.assertEqual(sum("readonly" in item for item in argv), 4)

    def test_snapshot_digest_is_content_and_path_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a").write_text("one", encoding="utf-8")
            first = boundary.canonical_tree_digest(root)
            (root / "a").write_text("two", encoding="utf-8")
            second = boundary.canonical_tree_digest(root)
            (root / "a").rename(root / "b")
            third = boundary.canonical_tree_digest(root)
            (root / "b").chmod(0o744)
            fourth = boundary.canonical_tree_digest(root)
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertNotEqual(third, fourth)

    def test_digest_and_returned_id_are_exact(self) -> None:
        with self.assertRaises(boundary.BoundaryError):
            boundary.require_digest("agent:latest", "image_digest")
        with self.assertRaises(boundary.BoundaryError):
            boundary.require_resource_id("short")


if __name__ == "__main__":
    unittest.main()
