"""Compatibility and opt-in persistence tests for mesh-compare."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")
add_repo_path("skills/mesh-compare/scripts/mesh-compare")

import cli  # noqa: E402
from meshscope.voxblame import read_surface_tree, run_step  # noqa: E402


def _triangle(center: tuple[float, float, float], size: float = 0.08) -> trimesh.Trimesh:
    x, y, z = center
    return trimesh.Trimesh(
        vertices=np.array(
            [[x - size, y - size, z], [x + size, y - size, z], [x, y + size, z]],
            dtype=np.float64,
        ),
        faces=[[0, 1, 2]],
        process=False,
    )


def _combine(*meshes: trimesh.Trimesh) -> trimesh.Trimesh:
    return trimesh.util.concatenate(meshes)


class _CliCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = self.root / "reference.ply"
        self.candidate = self.root / "candidate.ply"
        trimesh.creation.box(extents=(1, 1, 1)).export(self.reference)
        trimesh.creation.box(extents=(0.8, 0.8, 0.8)).export(self.candidate)

    def invoke(self, arguments: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(arguments)
        return code, json.loads(output.getvalue())


class TestBundledRuntime(unittest.TestCase):
    def test_cli_bootstraps_bundled_meshscope_first(self):
        self.assertTrue(cli._BUNDLED_MESHSCOPE.is_dir())
        self.assertTrue(cli._BUNDLED_MESHSHOT.is_dir())
        self.assertEqual(str(cli._BUNDLED_MESHSCOPE), sys.path[0])
        self.assertEqual(str(cli._BUNDLED_MESHSHOT), sys.path[1])


class TestLegacyCliCompatibility(_CliCase):
    def test_legacy_output_has_no_voxblame(self):
        state = self.root / "unused"
        code, payload = self.invoke([str(self.reference), str(self.candidate), "--samples", "100", "--quiet"])
        self.assertEqual(0, code)
        self.assertTrue(payload["ok"])
        self.assertIn("chamfer", payload)
        self.assertIn("hausdorff", payload)
        self.assertIn("stats", payload)
        self.assertIn("meta", payload)
        self.assertNotIn("voxblame", payload)
        self.assertFalse(state.exists())

    def test_default_sampling_protocol_matches_acceptance_contract(self):
        code, payload = self.invoke(
            [str(self.reference), str(self.candidate), "--quiet"]
        )
        self.assertEqual(0, code)
        self.assertEqual(50000, payload["meta"]["n_samples"])
        self.assertEqual(0, payload["meta"]["sample_seed"])
        self.assertEqual("trimesh_surface_seeded", payload["meta"]["sampling"])

    def test_sampling_seed_is_reproducible_and_reported(self):
        arguments = [
            str(self.reference),
            str(self.candidate),
            "--samples", "100",
            "--seed", "23",
            "--quiet",
        ]
        first_code, first = self.invoke(arguments)
        second_code, second = self.invoke(arguments)
        self.assertEqual(0, first_code)
        self.assertEqual(0, second_code)
        self.assertEqual(first, second)
        self.assertEqual(23, first["meta"]["sample_seed"])


class TestVoxBlameCli(_CliCase):
    def test_opt_in_returns_summary_only(self):
        state = self.root / "voxblame"
        code, payload = self.invoke(
            [
                str(self.reference),
                str(self.candidate),
                "--samples", "100",
                "--voxblame-dir", str(state),
                "--step", "0",
                "--max-depth", "3",
                "--quiet",
            ]
        )
        self.assertEqual(0, code)
        summary = payload["voxblame"]
        self.assertEqual("voxblame.summary/1", summary["schema"])
        self.assertNotIn("current", summary)
        self.assertNotIn("changes", summary)
        self.assertTrue((state / "steps/000000/report.json").is_file())

    def test_compare_to_non_adjacent_step(self):
        state = self.root / "voxblame"
        base = [str(self.reference), str(self.reference), "--samples", "20", "--voxblame-dir", str(state), "--max-depth", "3", "--quiet"]
        self.assertEqual(0, self.invoke([*base, "--step", "0"])[0])
        changed = [str(self.reference), str(self.candidate), "--samples", "20", "--voxblame-dir", str(state), "--max-depth", "3", "--quiet", "--step", "1"]
        self.assertEqual(0, self.invoke(changed)[0])
        code, payload = self.invoke([*base, "--step", "2", "--compare-to", "0"])
        self.assertEqual(0, code)
        self.assertEqual(0, payload["voxblame"]["compare_to"])
        report = json.loads((state / "steps/000002/report.json").read_text())
        self.assertEqual(0, report["compare_to"])

    def test_flag_pairs_are_required(self):
        cases = (
            [str(self.reference), str(self.candidate), "--voxblame-dir", str(self.root / "state")],
            [str(self.reference), str(self.candidate), "--step", "0"],
            [str(self.reference), str(self.candidate), "--compare-to", "0"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main(arguments)
                self.assertEqual(2, caught.exception.code)
        self.assertFalse((self.root / "state").exists())

    def test_candidate_outside_frame_is_graded(self):
        reference = trimesh.creation.box(extents=(1, 1, 1))
        candidate = reference.copy()
        candidate.apply_translation([2, 0, 0])
        reference.export(self.reference)
        candidate.export(self.candidate)
        state = self.root / "voxblame"
        code, payload = self.invoke(
            [str(self.reference), str(self.candidate), "--samples", "20", "--voxblame-dir", str(state), "--step", "0", "--max-depth", "3", "--quiet"]
        )
        self.assertEqual(0, code)
        self.assertTrue(payload["ok"])
        self.assertEqual(1, payload["voxblame"]["remaining_error_count"])
        self.assertTrue((state / "steps/000000").exists())

    def test_controlled_progression_matches_core(self):
        anchor_left = _triangle((-0.48, -0.48, 0), 0.02)
        anchor_right = _triangle((0.48, 0.48, 0), 0.02)
        a = _triangle((-0.2, 0, 0), 0.07)
        a_coarse = _triangle((-0.2, 0, 0), 0.025)
        b = _triangle((0.2, 0, 0), 0.07)
        c = _triangle((0, 0.3, 0), 0.04)
        meshes = (
            _combine(anchor_left, anchor_right),
            _combine(anchor_left, anchor_right, a_coarse),
            _combine(anchor_left, anchor_right, a, b),
            _combine(anchor_right, a_coarse, b, c),
        )
        paths: list[Path] = []
        for index, mesh in enumerate(meshes):
            path = self.root / f"step_{index}.ply"
            mesh.export(path)
            paths.append(path)
        reference = paths[2]
        core_state = self.root / "core" / "voxblame"
        cli_state = self.root / "cli" / "voxblame"

        core_summaries = [
            run_step(reference, path, core_state, index, max_depth=4)
            for index, path in enumerate(paths)
        ]
        cli_summaries = []
        for index, path in enumerate(paths):
            code, payload = self.invoke(
                [str(reference), str(path), "--samples", "20", "--voxblame-dir", str(cli_state), "--step", str(index), "--max-depth", "4", "--quiet"]
            )
            self.assertEqual(0, code)
            cli_summaries.append(payload["voxblame"])

        self.assertGreater(core_summaries[0]["remaining_error_count"], 0)
        self.assertEqual("remaining", core_summaries[0]["next_action"]["reason"])
        self.assertEqual(1, core_summaries[0]["next_action"]["first_error_depth"])
        self.assertGreater(core_summaries[1]["change_counts"]["improved"], 0)
        self.assertEqual("remaining", core_summaries[1]["next_action"]["reason"])
        self.assertEqual("missing", core_summaries[1]["next_action"]["direction"])
        self.assertEqual(0, core_summaries[2]["remaining_error_count"])
        self.assertGreater(core_summaries[2]["change_counts"]["resolved"], 0)
        self.assertGreater(core_summaries[3]["change_counts"]["introduced"], 0)
        self.assertEqual("introduced", core_summaries[3]["next_action"]["reason"])
        self.assertEqual("missing", core_summaries[3]["next_action"]["direction"])
        self.assertEqual(2, core_summaries[3]["next_action"]["first_error_depth"])

        step3_report = json.loads(
            (core_state / "steps/000003/report.json").read_text()
        )
        step3_errors = step3_report["current"]["errors"]
        self.assertEqual({"missing", "excess"}, {item["direction"] for item in step3_errors})
        self.assertTrue(
            any(
                item["direction"] == "missing"
                and item["first_error_depth"] == 2
                and item["bounds_world"]["min"][0] <= -0.5
                for item in step3_errors
            ),
            "removed coarse anchor must remain a localized missing error",
        )
        self.assertTrue(
            any(
                item["direction"] == "missing"
                and item["first_error_depth"] == 4
                and item["bounds_world"]["max"][0] < 0
                for item in step3_errors
            ),
            "fine A residual must coexist with the coarse body regression",
        )
        self.assertTrue(
            any(
                item["direction"] == "excess"
                and item["bounds_world"]["min"][1] >= 0.25
                for item in step3_errors
            ),
            "floating C must remain a localized excess error",
        )

        for index in range(4):
            core_tree = read_surface_tree(
                core_state / f"steps/{index:06d}/candidate.vbsvo"
            )
            cli_tree = read_surface_tree(
                cli_state / f"steps/{index:06d}/candidate.vbsvo"
            )
            self.assertEqual(core_tree.logical_sha256, cli_tree.logical_sha256)
            self.assertEqual(core_tree.masks, cli_tree.masks)
            self.assertEqual(
                json.loads((core_state / f"steps/{index:06d}/report.json").read_text()),
                json.loads((cli_state / f"steps/{index:06d}/report.json").read_text()),
            )
            expected = dict(core_summaries[index])
            expected["report"] = cli_summaries[index]["report"]
            self.assertEqual(expected, cli_summaries[index])


if __name__ == "__main__":
    unittest.main()
