"""Direct import-path coverage for the real Step 0 evidence provider.

The production provider vendors ``meshscope.voxblame`` and ``meshshot``
via ``_ensure_shipped_package``. Two invariants matter here:

* ``_import_meshscope`` / ``_import_meshshot`` resolve to the fixed vendored
  mesh-compare runtimes that survive publish-tree finalization.
* Resolution is fail-closed when a package is missing or an ambient import
  resolves outside those roots.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path

from scripts.pilot import step_zero_evidence


REPO_ROOT = Path(__file__).resolve().parents[3]


def _snapshot_tree(root: Path) -> dict[str, tuple[str, int]]:
    files: dict[str, tuple[str, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        files[path.relative_to(root).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            stat.S_IMODE(path.stat().st_mode),
        )
    return files


class StepZeroEvidenceImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._provider_modules = {
            name: module
            for name, module in sys.modules.items()
            if name.split(".", 1)[0] in {"meshscope", "meshshot"}
        }
        self._sys_path = sys.path[:]
        self._shipped_package_roots = (
            step_zero_evidence._SHIPPED_PACKAGE_ROOTS.copy()
        )
        step_zero_evidence._SHIPPED_PACKAGE_ROOTS.clear()
        for name in self._provider_modules:
            sys.modules.pop(name, None)
        self.addCleanup(self._restore_provider_imports)

    def _restore_provider_imports(self) -> None:
        for name in tuple(sys.modules):
            if name.split(".", 1)[0] in {"meshscope", "meshshot"}:
                sys.modules.pop(name, None)
        sys.modules.update(self._provider_modules)
        sys.path[:] = self._sys_path
        step_zero_evidence._SHIPPED_PACKAGE_ROOTS.clear()
        step_zero_evidence._SHIPPED_PACKAGE_ROOTS.update(
            self._shipped_package_roots
        )

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
            previous_dont_write_bytecode = sys.dont_write_bytecode
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
            self.assertEqual(
                previous_dont_write_bytecode,
                sys.dont_write_bytecode,
            )

    def test_imports_do_not_mutate_a_published_package_tree(self) -> None:
        """Shipped imports must leave their digest-bound source tree untouched."""

        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            meshscope_src = root / "meshscope-src"
            meshshot_src = root / "meshshot-src"
            shutil.copytree(
                step_zero_evidence._MESHSCOPE_SRC / "meshscope",
                meshscope_src / "meshscope",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so"),
            )
            shutil.copytree(
                step_zero_evidence._MESHSHOT_SRC / "meshshot",
                meshshot_src / "meshshot",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so"),
            )
            before_meshscope = _snapshot_tree(meshscope_src)
            before_meshshot = _snapshot_tree(meshshot_src)
            previous_dont_write_bytecode = sys.dont_write_bytecode

            step_zero_evidence._import_meshscope(meshscope_src)
            step_zero_evidence._import_meshshot(meshshot_src)

            after_meshscope = _snapshot_tree(meshscope_src)
            after_meshshot = _snapshot_tree(meshshot_src)
            self.assertEqual(set(before_meshscope), set(after_meshscope))
            self.assertEqual(set(before_meshshot), set(after_meshshot))
            self.assertEqual(before_meshscope, after_meshscope)
            self.assertEqual(before_meshshot, after_meshshot)
            self.assertEqual(
                previous_dont_write_bytecode,
                sys.dont_write_bytecode,
            )

    def test_production_import_order_reloads_verified_package_root(self) -> None:
        """Reference binding followed by provider import cannot leave stale modules."""

        from scripts.pilot.workspace_supervisor import _load_reference_type

        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            meshscope_src_a = root / "meshscope-src-a"
            meshscope_src_b = root / "meshscope-src-b"
            for destination in (meshscope_src_a, meshscope_src_b):
                shutil.copytree(
                    step_zero_evidence._MESHSCOPE_SRC / "meshscope",
                    destination / "meshscope",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so"),
                )
            before_a = _snapshot_tree(meshscope_src_a)
            before_b = _snapshot_tree(meshscope_src_b)
            previous_dont_write_bytecode = sys.dont_write_bytecode

            _load_reference_type(meshscope_src_a)
            step_zero_evidence._import_meshscope(meshscope_src_b)
            import meshscope  # noqa: WPS433 — imported after helper.

            self.assertEqual(
                meshscope_src_b.resolve() / "meshscope",
                Path(meshscope.__file__).resolve().parent,
            )
            self.assertEqual(before_a, _snapshot_tree(meshscope_src_a))
            self.assertEqual(before_b, _snapshot_tree(meshscope_src_b))
            self.assertEqual(
                previous_dont_write_bytecode,
                sys.dont_write_bytecode,
            )

    def test_ambient_package_module_is_rejected_before_eviction(self) -> None:
        """An unverified ambient module must not be replaced by a shipped root."""

        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "meshscope").mkdir()
            (root / "meshscope/__init__.py").write_text("", encoding="utf-8")
            ambient = types.ModuleType("meshscope")
            ambient.__file__ = "/ambient/provider/meshscope/__init__.py"
            sys.modules["meshscope"] = ambient
            with self.assertRaises(step_zero_evidence.StepZeroEvidenceError) as raised:
                step_zero_evidence._ensure_shipped_package(root, "meshscope")
            self.assertEqual(
                "provider_dependency_missing", raised.exception.classification
            )
            self.assertIs(ambient, sys.modules["meshscope"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
