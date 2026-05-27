from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import snapshot.__main__ as snapshot_main
from snapshot.__main__ import (
    RENDER_HTML_PATH,
    RUNTIME_DIR,
    SnapshotError,
    load_job_from_options,
    parse_snapshot_args,
    resolve_render_job_packet,
    resolve_snapshot_route_file,
    timestamp_output_path,
)


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class SnapshotCliTests(unittest.TestCase):
    def test_shortcut_job_shape_stays_owned_by_python_cli(self) -> None:
        options = parse_snapshot_args(
            [
                "--input",
                "models/simple/cylindrical_cap.step",
                "--output",
                "tmp/cap.png",
                "--root-dir",
                "models",
                "--display",
                "wireframe",
                "--size-profile",
                "simple",
            ]
        )

        job = load_job_from_options(options, stdin=_TtyStringIO(), cwd=Path.cwd())

        self.assertEqual(job["input"], "models/simple/cylindrical_cap.step")
        self.assertEqual(job["rootDir"], "models")
        self.assertEqual(job["outputs"][0]["path"], "tmp/cap.png")
        self.assertEqual(job["display"], {"mode": "wireframe"})
        self.assertEqual(job["render"]["sizeProfile"], "simple")

    def test_output_paths_are_timestamped_when_jobs_are_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            models = root / "models"
            models.mkdir()
            (models / "part.step").write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
            (models / ".part.step.glb").write_bytes(b"glb")

            original_timestamp = snapshot_main.snapshot_timestamp
            original_ensure = snapshot_main.ensure_step_topology_artifact
            try:
                snapshot_main.snapshot_timestamp = lambda: "20260527T163012Z"
                snapshot_main.ensure_step_topology_artifact = lambda *args, **kwargs: None

                packet = resolve_render_job_packet(
                    {
                        "jobs": [
                            {
                                "input": "part.step",
                                "workspaceRoot": str(root),
                                "rootDir": "models",
                                "outputs": [
                                    {"path": "tmp/iso.png", "camera": "iso"},
                                    {"path": "tmp/front.png", "camera": "front"},
                                ],
                            },
                            {
                                "input": "part.step",
                                "workspaceRoot": str(root),
                                "rootDir": "models",
                                "mode": "orbit",
                                "outputs": [{"path": "tmp/orbit.gif"}],
                            },
                        ]
                    },
                    cwd=root,
                )
            finally:
                snapshot_main.snapshot_timestamp = original_timestamp
                snapshot_main.ensure_step_topology_artifact = original_ensure

            output_paths = [
                Path(output["path"]).relative_to(root).as_posix()
                for job in packet["jobs"]
                for output in job["outputs"]
            ]

        self.assertEqual(
            output_paths,
            [
                "tmp/iso_20260527T163012Z.png",
                "tmp/front_20260527T163012Z.png",
                "tmp/orbit_20260527T163012Z.gif",
            ],
        )

    def test_timestamp_output_path_preserves_extension(self) -> None:
        self.assertEqual(
            timestamp_output_path("snapshots/review.png", "20260527T163012Z"),
            "snapshots/review_20260527T163012Z.png",
        )

    def test_removed_daemon_flags_stay_removed(self) -> None:
        with self.assertRaisesRegex(SnapshotError, "daemon commands have been removed"):
            parse_snapshot_args(["daemon"])
        with self.assertRaisesRegex(SnapshotError, "--socket has been removed"):
            parse_snapshot_args(["--socket", "snapshot.sock"])

    def test_runtime_routes_are_self_contained(self) -> None:
        self.assertEqual(
            resolve_snapshot_route_file("http://snapshot.local/render.html"),
            RENDER_HTML_PATH,
        )
        self.assertEqual(
            resolve_snapshot_route_file("http://snapshot.local/snapshot-render.js"),
            RUNTIME_DIR / "snapshot-render.js",
        )

    def test_snapshot_tool_has_no_sideways_runtime_dependencies(self) -> None:
        snapshot_root = Path(__file__).resolve().parents[1]
        checked_files = [
            snapshot_root / "__main__.py",
            snapshot_root / "runtime" / "render.html",
            snapshot_root / "runtime" / "snapshot-render.js",
        ]
        forbidden = (
            "packages/cadjs",
            "skills/cad-viewer",
            "/node_modules/",
            "\\node_modules\\",
            "CADJS_NODE_MODULES_ROOT",
        )
        for checked_file in checked_files:
            text = checked_file.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{checked_file} should not reference {token}")


if __name__ == "__main__":
    unittest.main()
