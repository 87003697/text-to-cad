"""Drift guard for the viewer server's deliberate cadgen mirrors.

viewer/server_py must stay importable WITHOUT cadgen/OCP installed, so
scanner.py re-implements the ``__cadgen__`` render-package path helpers and
descriptor constants instead of importing them. This test pins the two sides
together: if either renames the directory, the models namespace, a descriptor
filename, or changes the package-path shape, it fails here instead of
silently splitting the viewer from the build pipeline.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

for entry in (str(REPO_ROOT / "viewer"), str(REPO_ROOT / "packages" / "cadgen" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from cadgen import catalog as cadgen_catalog  # noqa: E402
from server_py import scanner  # noqa: E402


def _module_constants(path: Path, names: set[str]) -> dict[str, object]:
    """Read simple ``NAME = <literal>`` assignments without importing the module
    (component_package/drawing_package pull in OCP/ezdxf at import time)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)
    return found


class ViewerCadgenMirrorTest(unittest.TestCase):
    def test_cadgen_dirname_constants_match(self) -> None:
        self.assertEqual(scanner.CADGEN_DIRNAME, cadgen_catalog.CADGEN_DIRNAME)
        self.assertEqual(scanner.CADGEN_MODELS_DIRNAME, cadgen_catalog.CADGEN_MODELS_DIRNAME)

    def test_render_package_dir_shapes_match(self) -> None:
        entry = REPO_ROOT / "models" / "sample" / "part.step"
        expected = cadgen_catalog.render_package_dir(entry)
        actual = Path(scanner.render_package_dir(str(entry))).resolve()
        self.assertEqual(expected, actual)
        # Round trip: the scanner must map the package dir back to the entry file.
        self.assertEqual(
            str(entry),
            scanner.entry_path_for_render_package(scanner.render_package_dir(str(entry))),
        )

    def test_step_descriptor_constants_match(self) -> None:
        constants = _module_constants(
            REPO_ROOT / "packages" / "cadgen" / "src" / "cadgen" / "_internal" / "component_package.py",
            {"DESCRIPTOR_NAME", "PACKAGE_KIND"},
        )
        self.assertEqual(constants.get("DESCRIPTOR_NAME"), "assembly.json")
        # scanner.py reads assembly.json literally in read_step_catalog_metadata /
        # _package_descriptor_stats; pin the emit side to the same name.
        self.assertEqual(constants.get("PACKAGE_KIND"), "assembly-package")

    def test_drawing_descriptor_constants_match(self) -> None:
        constants = _module_constants(
            REPO_ROOT / "packages" / "cadgen" / "src" / "cadgen" / "_internal" / "drawing_package.py",
            {"DRAWING_DESCRIPTOR_NAME", "DRAWING_PACKAGE_KIND"},
        )
        self.assertEqual(constants.get("DRAWING_DESCRIPTOR_NAME"), scanner.DRAWING_DESCRIPTOR_NAME)
        self.assertEqual(constants.get("DRAWING_PACKAGE_KIND"), scanner.DRAWING_PACKAGE_KIND)

    def test_generation_lock_paths_match(self) -> None:
        # cadgen writes the lock (generation_status.generation_lock_path); the viewer
        # reads it (server_py.artifact.generation_lock_path). Same package dir must
        # yield the same lock file.
        from cadgen._internal.generation_status import generation_lock_path as write_side
        from server_py.artifact import generation_lock_path as read_side

        package_dir = REPO_ROOT / "models" / "sample" / "__cadgen__" / "models" / "part.step"
        self.assertEqual(str(write_side(package_dir)), read_side(str(package_dir)))


if __name__ == "__main__":
    unittest.main()
