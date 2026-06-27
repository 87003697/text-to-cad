"""Diff the Python scanner against the Node scanner on real fixtures.

Regenerates the golden catalog from Node at test time (so mtime-based ``?v=``
tokens match), then asserts the Python scan is byte-identical per entry. Skips
if Node or the cadjs workspace link is unavailable (lightweight worktree).
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from server_py import scanner  # noqa: E402

_WORKTREE = pathlib.Path(__file__).resolve().parents[3]
_MODELS = _WORKTREE / "models"
_CADPY_SRC = _WORKTREE / "packages" / "cadpy" / "src"
_GEN = pathlib.Path(__file__).resolve().parent / "gen_catalog_golden.mjs"
_GOLDEN = pathlib.Path(__file__).resolve().parent / "catalog_golden.json"
# implicits/gcode/simple: single-asset + implicit. robots/lyra: python-generated
# urdf/srdf (embedded cadpy metadata, missing-generator status) + srdf->urdf
# relations. robots/tom: broad mix (3mf/dxf/stl/urdf/srdf).
_SUBDIRS = ["implicits", "simple", "gcode", "robots/lyra", "robots/tom"]


def _node_golden(repo_root, *subdirs):
    """Run the Node scanner (catalog path) -> {subdir: catalog}. Skip if unavailable."""
    try:
        subprocess.run(
            ["node", str(_GEN), str(repo_root), *subdirs],
            cwd=str(_WORKTREE), check=True, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise unittest.SkipTest(f"cannot run Node scanner (node/cadjs unavailable): {exc}")
    return json.loads(_GOLDEN.read_text())


class ScannerParity(unittest.TestCase):
    def test_single_asset_entries_match_node(self):
        if not _MODELS.is_dir():
            self.skipTest("models/ fixtures not hydrated")
        golden = _node_golden(_MODELS, *_SUBDIRS)
        for subdir in _SUBDIRS:
            expected = golden[subdir]["entries"]
            result = scanner.scan_cad_directory(str(_MODELS), subdir, include_artifact_status=False)
            self.assertEqual(result["schemaVersion"], golden[subdir]["schemaVersion"])
            self.assertEqual(result["entries"], expected, f"catalog mismatch for {subdir}")

    def test_built_step_package_entry_matches_node(self):
        fixture = _MODELS / "simple" / "basic_shape_mating_test_fixture.step.py"
        if not fixture.is_file():
            self.skipTest("step.py fixture missing")
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(fixture, os.path.join(tmp, fixture.name))
            env = dict(os.environ, PYTHONPATH=str(_CADPY_SRC))
            try:
                subprocess.run(
                    [sys.executable, "-m", "cadpy.step_artifact", "--repo-root", ".",
                     "--step", os.path.join(tmp, "basic_shape_mating_test_fixture.step"),
                     "--source-path", os.path.join(tmp, fixture.name), "--skip-step-write"],
                    cwd=tmp, env=env, check=True, capture_output=True, text=True, timeout=180,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise unittest.SkipTest(f"cannot build cadpy package (OCP unavailable): {exc}")
            golden = _node_golden(tmp)["."]
            result = scanner.scan_cad_directory(tmp, "", include_artifact_status=False)
            self.assertTrue(any(e["kind"] in ("part", "assembly") for e in result["entries"]),
                            "expected a STEP entry from the built package")
            self.assertEqual(result["entries"], golden["entries"])


if __name__ == "__main__":
    unittest.main()
