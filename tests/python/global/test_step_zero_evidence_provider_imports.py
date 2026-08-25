"""Direct import-path coverage for the real Step 0 evidence provider.

The production provider vendors ``meshscope.voxblame`` and ``meshshot``
via ``_ensure_shipped_package``. Two invariants matter here:

* ``_import_meshscope`` / ``_import_meshshot`` resolve to the fixed vendored
  mesh-compare runtimes that survive publish-tree finalization.
* Resolution is fail-closed when a package is missing or an ambient import
  resolves outside those roots.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from scripts.pilot import step_zero_evidence


REPO_ROOT = Path(__file__).resolve().parents[3]


class StepZeroEvidenceImportTests(unittest.TestCase):
    def test_repo_root_resolves_to_repository_top(self) -> None:
        # The provider file lives at scripts/pilot/step_zero_evidence.py;
        # parents[2] must land on the repo root so that
        # the fixed vendored skill runtime is the correct sibling.
        self.assertEqual(REPO_ROOT, step_zero_evidence._REPO_ROOT)
        self.assertTrue(
            (step_zero_evidence._MESHSCOPE_SRC / "meshscope/__init__.py").is_file()
        )
        self.assertTrue(
            (step_zero_evidence._MESHSHOT_SRC / "meshshot/__init__.py").is_file()
        )

    def test_import_meshscope_returns_shipped_module_root(self) -> None:
        measure_step, _prepare, _publish, _validate = step_zero_evidence._import_meshscope()
        import meshscope  # noqa: WPS433 — imported after helper.

        module_root = Path(meshscope.__file__).resolve().parent
        self.assertEqual(
            step_zero_evidence._MESHSCOPE_SRC.resolve() / "meshscope",
            module_root,
        )
        self.assertTrue((module_root / "voxblame/__init__.py").is_file())
        self.assertTrue(callable(measure_step))

    def test_import_meshshot_returns_shipped_module_root(self) -> None:
        MeshGeometry, load_profile, render_residual_preview = (
            step_zero_evidence._import_meshshot()
        )
        import meshshot  # noqa: WPS433 — imported after helper.

        module_root = Path(meshshot.__file__).resolve().parent
        self.assertEqual(
            step_zero_evidence._MESHSHOT_SRC.resolve() / "meshshot",
            module_root,
        )
        self.assertTrue((module_root / "profile.py").is_file())
        self.assertTrue(callable(load_profile))
        self.assertTrue(callable(render_residual_preview))
        self.assertTrue(callable(MeshGeometry))

    def test_ensure_shipped_package_fails_closed_on_missing_root(self) -> None:
        # Choose a package name that is definitely not importable and
        # point at an empty scratch directory. The provider must raise
        # ``provider_dependency_missing`` rather than silently searching
        # elsewhere.
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            with self.assertRaises(step_zero_evidence.StepZeroEvidenceError) as raised:
                step_zero_evidence._ensure_shipped_package(
                    root,
                    "voxblame_absent_provider_probe",
                )
            self.assertEqual(
                "provider_dependency_missing", raised.exception.classification
            )
            # A rejected root must never leak into ``sys.path``.
            self.assertNotIn(str(root), sys.path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
