"""Regression tests for incremental-regen building blocks:

- source import-closure capture/check (cadpy.source_hash)
- the binary (BinTools) scene cache round-trip
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import build123d
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer

from cadpy import catalog as cad_catalog
from cadpy import source_hash as cad_source_hash
from cadpy import step_scene
from cadpy.step_scene import LoadedStepScene, OccurrenceNode
from tests.python.support.tmp_root import temporary_directory


def _face_count(shape: object) -> int:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


class SourceClosureTests(unittest.TestCase):
    def test_closure_round_trip_detects_dependency_changes(self) -> None:
        with temporary_directory(prefix="closure-") as raw_dir:
            base = Path(raw_dir)
            script = base / "part.py"
            dep = base / "helper.py"
            script.write_text("import helper\n", encoding="utf-8")
            dep.write_text("VALUE = 1\n", encoding="utf-8")

            closure = cad_source_hash.closure_for_files(script, [dep])
            # The script and its dependency are both recorded.
            self.assertEqual(2, len(closure.files))

            # Re-hashing the recorded file list reproduces the same hash.
            self.assertEqual(
                closure.closure_hash,
                cad_source_hash.closure_hash_from_files(closure.files),
            )

            # Editing a dependency changes the recomputed hash (stale).
            dep.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(
                closure.closure_hash,
                cad_source_hash.closure_hash_from_files(closure.files),
            )

            # A missing recorded file yields None (callers treat as stale).
            dep.unlink()
            self.assertIsNone(cad_source_hash.closure_hash_from_files(closure.files))

    def test_capture_runtime_closure_includes_imported_repo_local_modules(self) -> None:
        with temporary_directory(prefix="closure-capture-") as raw_dir:
            base = Path(raw_dir)
            script = base / "widget.py"
            dep = base / "shared_helper.py"
            script.write_text("import shared_helper\n", encoding="utf-8")
            dep.write_text("X = 1\n", encoding="utf-8")

            # Ensure a clean import so the sys.modules delta actually captures it.
            sys.modules.pop("shared_helper", None)
            with mock.patch.object(cad_catalog, "REPO_ROOT", base):
                before = set(sys.modules)
                sys.path.insert(0, str(base))
                try:
                    import shared_helper  # noqa: F401  (exercise a real import)
                    closure = cad_source_hash.capture_runtime_closure(before, script)
                finally:
                    sys.path.remove(str(base))
                    sys.modules.pop("shared_helper", None)

            self.assertIn("shared_helper.py", " ".join(closure.files))
            self.assertTrue(any(f.endswith("widget.py") for f in closure.files))


class BinarySceneCacheTests(unittest.TestCase):
    _IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)

    def _scene(self, step_path: Path) -> LoadedStepScene:
        shape = build123d.Box(3, 2, 1).wrapped
        node = OccurrenceNode(
            path=(1,),
            name="box",
            source_name="box",
            transform=self._IDENTITY,
            local_transform=self._IDENTITY,
            prototype_key=7,
        )
        return LoadedStepScene(
            step_path=step_path,
            roots=[node],
            prototype_shapes={7: shape},
            prototype_names={7: "box"},
        )

    def test_cache_round_trip_is_binary_and_preserves_geometry(self) -> None:
        with temporary_directory(prefix="scene-cache-") as raw_dir:
            step_path = Path(raw_dir) / "box.step"
            step_path.write_text("ISO-10303-21;\n", encoding="utf-8")
            scene = self._scene(step_path)
            expected_faces = _face_count(scene.prototype_shapes[7])

            step_scene._write_step_scene_cache(scene, step_hash="hash-abc")

            # The cache is written inline beside the STEP in __cadcache__, as binary
            # BREP (.bin), not ASCII (.brep).
            cadcache = step_path.parent / "__cadcache__"
            self.assertTrue(cadcache.is_dir())
            self.assertEqual(1, len(list(cadcache.rglob("*.bin"))))
            self.assertEqual([], list(cadcache.rglob("*.brep")))

            cached = step_scene._read_step_scene_cache(step_path, step_hash="hash-abc")
            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertEqual(1, len(cached.prototype_shapes))
            (restored_shape,) = cached.prototype_shapes.values()
            self.assertEqual(expected_faces, _face_count(restored_shape))

    def test_cache_misses_on_schema_version_bump(self) -> None:
        with temporary_directory(prefix="scene-cache-schema-") as raw_dir:
            step_path = Path(raw_dir) / "box.step"
            step_path.write_text("ISO-10303-21;\n", encoding="utf-8")
            step_scene._write_step_scene_cache(self._scene(step_path), step_hash="hash-xyz")

            with mock.patch.object(
                step_scene, "STEP_SCENE_CACHE_SCHEMA_VERSION", step_scene.STEP_SCENE_CACHE_SCHEMA_VERSION + 1
            ):
                self.assertIsNone(
                    step_scene._read_step_scene_cache(step_path, step_hash="hash-xyz")
                )


if __name__ == "__main__":
    unittest.main()
