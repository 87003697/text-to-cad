"""Deterministic numeric-protocol tests for mesh-compare."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")
add_repo_path("skills/mesh-compare/scripts/mesh-compare")

import cli  # noqa: E402


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
        self.assertEqual(str(cli._BUNDLED_MESHSCOPE), sys.path[0])


class TestLegacyCliCompatibility(_CliCase):
    def test_numeric_output_has_no_voxblame(self):
        code, payload = self.invoke([str(self.reference), str(self.candidate), "--samples", "100", "--quiet"])
        self.assertEqual(0, code)
        self.assertTrue(payload["ok"])
        self.assertIn("chamfer", payload)
        self.assertIn("hausdorff", payload)
        self.assertIn("stats", payload)
        self.assertIn("meta", payload)
        self.assertNotIn("voxblame", payload)

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


if __name__ == "__main__":
    unittest.main()
