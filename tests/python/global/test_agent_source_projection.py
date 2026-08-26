from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.pilot import agent_source_projection as projection
from tests.python.support.paths import REPO_ROOT


def _stage_source(root: Path) -> None:
    for source, _ in projection.SOURCE_MAPPINGS:
        destination = root / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / source).read_bytes())


class AgentSourceProjectionTests(unittest.TestCase):
    def _bundle(self, temporary: str) -> tuple[Path, Path]:
        root = Path(temporary) / "repo"
        _stage_source(root)
        target = root / projection.PROJECTION_ROOT_REL
        projection.bundle(root, target)
        return root, target

    def test_bundle_and_checked_in_projection_are_fresh_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, target = self._bundle(temporary)
            inventory = projection.check_bundle(root, target)
            self.assertEqual(projection.PROJECTED_PATHS, tuple(e.path for e in inventory.entries))
        checked_in = REPO_ROOT / projection.PROJECTION_ROOT_REL
        projection.check_bundle(REPO_ROOT, checked_in)
        files: set[str] = set()
        for parent, _, names in os.walk(checked_in):
            for name in names:
                path = Path(parent) / name
                self.assertFalse(path.is_symlink())
                files.add(path.relative_to(checked_in).as_posix())
        self.assertEqual({projection.MANIFEST_NAME, *projection.PROJECTED_PATHS}, files)

    def test_verify_rejects_missing_extra_and_digest_mismatch(self) -> None:
        mutations = (
            lambda target: (target / projection.PROJECTED_PATHS[0]).unlink(),
            lambda target: (target / "skills/mesh-to-cad/extra.md").write_text("extra"),
            lambda target: (target / projection.PROJECTED_PATHS[0]).write_text("tampered"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                _, target = self._bundle(temporary)
                mutate(target)
                with self.assertRaises(projection.ProjectionError):
                    projection.verify(target)

    def test_verify_rejects_symlinks_and_manifest_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, target = self._bundle(temporary)
            path = target / projection.PROJECTED_PATHS[0]
            original = path.with_suffix(".original")
            path.rename(original)
            os.symlink(original.name, path)
            with self.assertRaises(projection.ProjectionError):
                projection.verify(target)

    def test_verify_rejects_noninteger_manifest_size(self) -> None:
        for invalid in (3957.0, True):
            with self.subTest(size=invalid), tempfile.TemporaryDirectory() as temporary:
                _, target = self._bundle(temporary)
                manifest_path = target / projection.MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text())
                manifest["entries"][0]["size"] = invalid
                manifest_path.write_text(
                    json.dumps(
                        manifest,
                        indent=2,
                        sort_keys=True,
                        separators=(",", ": "),
                    )
                    + "\n"
                )
                with self.assertRaises(projection.ProjectionError):
                    projection.verify(target)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_verify_rejects_fifo_manifest_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, target = self._bundle(temporary)
            manifest = target / projection.MANIFEST_NAME
            manifest.unlink()
            os.mkfifo(manifest)
            with self.assertRaises(projection.ProjectionError):
                projection.verify(target)
        with tempfile.TemporaryDirectory() as temporary:
            _, target = self._bundle(temporary)
            manifest_path = target / projection.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text())
            manifest["entries"][0]["path"] = "skills/mesh-to-cad/extra.md"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(projection.ProjectionError):
                projection.verify(target)

    def test_check_detects_source_drift_and_bundle_lints_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, target = self._bundle(temporary)
            source = root / projection.SOURCE_MAPPINGS[0][0]
            source.write_bytes(source.read_bytes() + b"\nordinary drift\n")
            with self.assertRaises(projection.ProjectionError):
                projection.check_bundle(root, target)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            _stage_source(root)
            source = root / projection.SOURCE_MAPPINGS[0][0]
            source.write_bytes(source.read_bytes() + b"\nmesh-to-cad-workspace\n")
            with self.assertRaises(projection.ProjectionError):
                projection.bundle(root, root / projection.PROJECTION_ROOT_REL)

    def test_bundle_refuses_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            _stage_source(root)
            source = root / projection.SOURCE_MAPPINGS[0][0]
            original = source.with_suffix(".original")
            source.rename(original)
            os.symlink(original.name, source)
            with self.assertRaises(projection.ProjectionError):
                projection.bundle(root, root / projection.PROJECTION_ROOT_REL)

    def test_runtime_paths_are_fixed(self) -> None:
        root = REPO_ROOT / projection.PROJECTION_ROOT_REL
        self.assertEqual(root / "skills", projection.projected_skills_root(root))
        self.assertEqual(
            root / "agent-surface/client.py",
            projection.projected_agent_surface_client(root),
        )


if __name__ == "__main__":
    unittest.main()
