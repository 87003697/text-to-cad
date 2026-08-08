import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path, repo_path

add_repo_path("skills/dxf/scripts")

from snapshot import cli as snapshot


class DxfSnapshotCliTests(unittest.TestCase):
    """A drawing snapshot is a package build followed by a mesh render.

    The render half is cadgen.snapshot_core, shared with the CAD skill, so these tests
    cover only what is specific here: that the input is resolved to the package's
    preview.glb, and that drawing-shaped inputs are the ones accepted.
    """

    def test_renders_the_packages_preview_glb(self) -> None:
        payload = {"previewPath": "models/dxf/__cadgen__/models/x.dxf/preview.glb"}
        with mock.patch.object(snapshot, "build_dxf_artifact", return_value=payload) as build:
            with mock.patch.object(Path, "is_file", return_value=True):
                resolved = snapshot.preview_path_for_input(Path("/models/x.dxf"), force=False)

        self.assertTrue(str(resolved).endswith("preview.glb"))
        self.assertFalse(build.call_args.kwargs["force"])

    def test_force_reaches_the_package_build(self) -> None:
        payload = {"previewPath": "p/preview.glb"}
        with mock.patch.object(snapshot, "build_dxf_artifact", return_value=payload) as build:
            with mock.patch.object(Path, "is_file", return_value=True):
                snapshot.preview_path_for_input(Path("/models/x.dxf"), force=True)

        self.assertTrue(build.call_args.kwargs["force"])

    def test_a_package_without_a_preview_is_an_error(self) -> None:
        # Rendering a package that predates preview.glb would silently show nothing;
        # saying so is better than a blank image.
        with mock.patch.object(snapshot, "build_dxf_artifact", return_value={"ok": True}):
            with mock.patch.object(Path, "is_file", return_value=True):
                with self.assertRaises(snapshot.SnapshotError):
                    snapshot.preview_path_for_input(Path("/models/x.dxf"), force=False)

    def test_rejects_a_non_drawing_input(self) -> None:
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.preview_path_for_input(Path("/models/part.step"), force=False)

    def test_reports_a_missing_input(self) -> None:
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.preview_path_for_input(Path("/models/definitely-absent.dxf"), force=False)

    def test_modes_are_limited_to_view_and_orbit(self) -> None:
        # Drawings have no CAD topology, so section and list have nothing to work with.
        parser = snapshot.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--input", "a.dxf", "--output", "a.png", "--mode", "section"])

    def test_scripts_snapshot_directory_invokes_cli(self) -> None:
        skill_root = repo_path("skills/dxf")
        result = subprocess.run(
            [sys.executable, "scripts/snapshot", "--help"],
            cwd=skill_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.returncode)
        self.assertIn("usage: scripts/snapshot", result.stdout)

    def test_runtime_is_bundled_beside_the_cli(self) -> None:
        # The skill must carry its own render runtime: it may not reach into the CAD
        # skill's copy, and a published skill ships no node_modules.
        runtime = repo_path("skills/dxf/scripts/snapshot/runtime")
        self.assertTrue((Path(runtime) / "render.html").is_file())
        self.assertTrue((Path(runtime) / "snapshot-render.js").is_file())


if __name__ == "__main__":
    unittest.main()
