from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]


class VoxBlameWorkflowContractTests(unittest.TestCase):
    def test_measurement_and_acceptance_contract(self):
        workflow = (REPO_ROOT / "skills/mesh-to-cad/SKILL.md").read_text(
            encoding="utf-8"
        )
        schema = (
            REPO_ROOT / "skills/mesh-to-cad/references/output-schemas.md"
        ).read_text(encoding="utf-8")

        self.assertIn("--samples 50000", workflow)
        self.assertIn("--seed 0", workflow)
        self.assertIn("--voxblame-dir", workflow)
        self.assertIn("`chamfer ≤ 0.01`", schema)
        self.assertIn("`stats.p95_a2b ≤ 0.03`", schema)
        self.assertIn("`stats.p95_b2a ≤ 0.03`", schema)
        self.assertIn("coarsest_first_error_depth", schema)
        self.assertIn("Do not compute, append,", schema)
        self.assertIn("or use IoU for this workflow", schema)

    def test_divergence_has_an_explicit_terminal_audit_commit(self):
        workflow = (REPO_ROOT / "skills/mesh-to-cad/SKILL.md").read_text(
            encoding="utf-8"
        )
        schema = (
            REPO_ROOT / "skills/mesh-to-cad/references/output-schemas.md"
        ).read_text(encoding="utf-8")

        for document in (workflow, schema):
            self.assertIn("plateau_via_divergence", document)
        self.assertIn("git commit --allow-empty", workflow)
        self.assertIn("git commit --allow-empty", schema)
        self.assertIn("kept=iter <N-1>", workflow)
        self.assertIn("kept=iter <N-1>", schema)


if __name__ == "__main__":
    unittest.main()
