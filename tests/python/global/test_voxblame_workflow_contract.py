from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

import meshscope.voxblame as voxblame


REPO_ROOT = Path(__file__).resolve().parents[3]


REMOVED_PATHS = (
    "packages/meshscope/src/meshscope/compare.py",
    "packages/meshscope/src/meshscope/viz.py",
    "packages/meshscope/src/meshscope/octree_error.py",
    "packages/meshscope/src/meshscope/surface_tree.py",
    "packages/meshscope/src/meshscope/voxblame/grading.py",
    "packages/meshscope/src/meshscope/voxblame/reporting.py",
    "packages/meshscope/src/meshscope/voxblame/session.py",
    "packages/meshscope/src/meshscope/voxblame/store.py",
    "skills/mesh-compare/scripts/mesh-render/__main__.py",
    "skills/mesh-compare/scripts/mesh-render/cli.py",
    "skills/mesh-compare/references/compare-metrics.md",
    "skills/mesh-compare/references/render-modes.md",
    "tests/python/packages/meshscope/test_compare.py",
    "tests/python/packages/meshscope/test_octree_error.py",
    "tests/python/packages/meshscope/test_viz.py",
    "tests/python/skills/mesh-compare/test_cli.py",
    "tests/python/skills/mesh-compare/test_render_cli.py",
)

FORBIDDEN_EXECUTION_LANGUAGE = (
    "mesh-render",
    "meshscope.compare",
    "meshscope.viz",
    "meshscope.octree_error",
    "compare_metrics.json",
    "next_action",
    "remaining_error_count",
    "coarsest_first_error_depth",
    "change_counts",
    "plateau_via_divergence",
    "--samples",
    "p95_a2b",
    "p95_b2a",
    "Hausdorff",
    "sampled Chamfer",
)

REMOVED_PUBLIC_SYMBOLS = (
    "ChangeCell",
    "ErrorCell",
    "NextAction",
    "RegionHandle",
    "compare_error_trees",
    "grade_surface_trees",
    "lattice_bounds",
    "run_step",
    "select_next_action",
    "world_bounds",
)

CONTRACT_ROOTS = (
    "packages/meshscope/src/meshscope/voxblame",
    "skills/mesh-compare",
    "skills/mesh-compare/scripts/packages/meshscope",
    "skills/mesh-to-cad",
    "plugins/cad/skills/mesh-compare",
    "plugins/cad/skills/mesh-to-cad",
    ".claude/skills/pilot-review",
    ".claude/skills/cvm-pull",
    "scripts/pilot",
    "tests/python/packages/meshscope",
    "tests/python/skills/mesh-compare",
    "tests/python/skills/mesh-to-cad",
    "tests/python/global",
)

ALLOWED_REJECTION_OCCURRENCES = {
    "packages/meshscope/src/meshscope/voxblame/CONTRACT.md": {
        "change_counts": 1,
        "coarsest_first_error_depth": 1,
        "next_action": 2,
        "remaining_error_count": 1,
    },
    "packages/meshscope/src/meshscope/voxblame/contracts.py": {
        "change_counts": 1,
        "coarsest_first_error_depth": 1,
        "next_action": 1,
        "remaining_error_count": 1,
    },
    "skills/mesh-compare/scripts/packages/meshscope/src/meshscope/voxblame/CONTRACT.md": {
        "change_counts": 1,
        "coarsest_first_error_depth": 1,
        "next_action": 2,
        "remaining_error_count": 1,
    },
    "skills/mesh-compare/scripts/packages/meshscope/src/meshscope/voxblame/contracts.py": {
        "change_counts": 1,
        "coarsest_first_error_depth": 1,
        "next_action": 1,
        "remaining_error_count": 1,
    },
    "plugins/cad/skills/mesh-compare/scripts/packages/meshscope/src/meshscope/voxblame/CONTRACT.md": {
        "change_counts": 1,
        "coarsest_first_error_depth": 1,
        "next_action": 2,
        "remaining_error_count": 1,
    },
    "plugins/cad/skills/mesh-compare/scripts/packages/meshscope/src/meshscope/voxblame/contracts.py": {
        "change_counts": 1,
        "coarsest_first_error_depth": 1,
        "next_action": 1,
        "remaining_error_count": 1,
    },
    "plugins/cad/skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py": {
        "compare_metrics.json": 1,
    },
    "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py": {
        "compare_metrics.json": 1,
    },
    "tests/python/packages/meshscope/test_voxblame_contracts.py": {
        "change_counts": 1,
        "coarsest_first_error_depth": 1,
        "next_action": 3,
        "remaining_error_count": 1,
    },
    "tests/python/skills/mesh-compare/test_measure_step_cli.py": {
        "next_action": 1,
    },
    "tests/python/skills/mesh-to-cad/test_workspace_cli.py": {
        "compare_metrics.json": 1,
    },
}


def _contract_files() -> list[Path]:
    files: set[Path] = set()
    for relative in CONTRACT_ROOTS:
        root = (REPO_ROOT / relative).resolve()
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {
                ".json",
                ".md",
                ".py",
                ".txt",
                ".yaml",
                ".yml",
            }:
                continue
            resolved = path.resolve()
            repo_relative = resolved.relative_to(REPO_ROOT).as_posix()
            if repo_relative == "tests/python/global/test_voxblame_workflow_contract.py":
                continue
            files.add(resolved)
    return sorted(files)


class VoxBlameWorkflowContractTests(unittest.TestCase):
    def test_removed_legacy_surfaces_do_not_exist(self) -> None:
        for relative in REMOVED_PATHS:
            with self.subTest(path=relative):
                self.assertFalse((REPO_ROOT / relative).exists())

        for module in (
            "meshscope.compare",
            "meshscope.viz",
            "meshscope.octree_error",
            "meshscope.surface_tree",
            "meshscope.voxblame.grading",
            "meshscope.voxblame.reporting",
            "meshscope.voxblame.session",
            "meshscope.voxblame.store",
        ):
            with self.subTest(module=module):
                self.assertIsNone(importlib.util.find_spec(module))

        for symbol in REMOVED_PUBLIC_SYMBOLS:
            with self.subTest(symbol=symbol):
                self.assertFalse(hasattr(voxblame, symbol))

    def test_distance_only_dependency_is_removed(self) -> None:
        for relative in (
            "packages/meshscope/pyproject.toml",
            "skills/mesh-compare/requirements.txt",
        ):
            text = (REPO_ROOT / relative).read_text(encoding="utf-8").lower()
            with self.subTest(path=relative):
                self.assertNotIn("scipy", text)
                self.assertNotIn("trimesh[easy]", text)

    def test_skill_runtime_installs_native_packages_instead_of_shadowing_them(self) -> None:
        requirements = (
            REPO_ROOT / "skills/mesh-compare/requirements.txt"
        ).read_text(encoding="utf-8").splitlines()
        self.assertIn("./scripts/packages/meshscope", requirements)
        self.assertIn("./scripts/packages/meshshot", requirements)

        cli = (
            REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare/cli.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("_BUNDLED_MESHSCOPE", cli)
        self.assertNotIn("_BUNDLED_MESHSHOT", cli)

        native_source = (
            REPO_ROOT
            / "skills/mesh-compare/scripts/packages/meshscope"
            / "src/meshscope/voxblame/_native.cpp"
        )
        self.assertTrue(native_source.is_file())

    def test_legacy_positional_comparison_is_rejected(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare"),
                "old-reference.glb",
                "old-candidate.glb",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual("unsupported_command", payload["error"]["classification"])

    def test_execution_contracts_use_only_canonical_workspace_language(self) -> None:
        for path in _contract_files():
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(REPO_ROOT).as_posix()
            for forbidden in FORBIDDEN_EXECUTION_LANGUAGE:
                expected = ALLOWED_REJECTION_OCCURRENCES.get(relative, {}).get(
                    forbidden, 0
                )
                with self.subTest(path=relative, forbidden=forbidden):
                    self.assertEqual(expected, text.count(forbidden))

    def test_execution_contracts_name_the_canonical_workflow(self) -> None:
        mesh_compare = (REPO_ROOT / "skills/mesh-compare/SKILL.md").read_text(
            encoding="utf-8"
        )
        mesh_to_cad = (REPO_ROOT / "skills/mesh-to-cad/SKILL.md").read_text(
            encoding="utf-8"
        )
        pilot_review = (
            REPO_ROOT / ".claude/skills/pilot-review/SKILL.md"
        ).read_text(encoding="utf-8")

        for term in (
            "Canonical Reference",
            "Measured Step",
            "Repair Target",
            "Region Diff",
            "Observable Geometry",
        ):
            with self.subTest(contract="mesh-compare", term=term):
                self.assertIn(term, mesh_compare)
        for term in (
            "Workspace",
            "Repair Batch",
            "Repair Cycle",
            "Attempt",
            "Selected Step",
            "Final Delivery",
        ):
            with self.subTest(contract="mesh-to-cad", term=term):
                self.assertIn(term, mesh_to_cad)
            with self.subTest(contract="pilot-review", term=term):
                self.assertIn(term, pilot_review)

    def test_mesh_to_cad_freezes_registered_build_path_semantics(self) -> None:
        contract = (REPO_ROOT / "skills/mesh-to-cad/SKILL.md").read_text(
            encoding="utf-8"
        )

        for required in (
            "mesh-to-cad-workspace build",
            "trusted registry",
            "do not supply or reconstruct a\n   launcher",
            "experiment-root-relative paths",
            "candidate bundle as cwd",
            'Path("source/width.txt")',
            "experiment-root-relative\n   `--input`",
            "preflight failure is cleaned and spends no command",
            "`build.json`",
            "`measurement.glb`",
        ):
            with self.subTest(required=required):
                self.assertIn(required, contract)


if __name__ == "__main__":
    unittest.main()
