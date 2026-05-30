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
    SNAPSHOT_ORIGIN,
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


class ImplicitCadSnapshotCliTests(unittest.TestCase):
    def test_shortcut_job_shape_stays_owned_by_python_cli(self) -> None:
        options = parse_snapshot_args(
            [
                "--input",
                "models/implicit-cad/orb.implicit.js",
                "--output",
                "tmp/orb.png",
                "--camera",
                "front",
                "--width",
                "640",
                "--height",
                "480",
                "--params",
                '{"radius": 24}',
                "--appearance",
                "workbench",
                "--graphics",
                '{"detail": 2, "resolutionScale": 3}',
                "--size-profile",
                "simple",
            ]
        )

        job = load_job_from_options(options, stdin=_TtyStringIO(), cwd=Path.cwd())

        self.assertEqual(job["input"], "models/implicit-cad/orb.implicit.js")
        self.assertEqual(job["mode"], "view")
        self.assertEqual(job["outputs"][0]["path"], "tmp/orb.png")
        self.assertEqual(job["outputs"][0]["camera"], "front")
        self.assertEqual(job["outputs"][0]["width"], 640)
        self.assertEqual(job["outputs"][0]["height"], 480)
        self.assertEqual(job["appearance"], "workbench")
        self.assertEqual(job["graphics"], {"detail": 2, "resolutionScale": 3})
        self.assertEqual(job["render"], {"sizeProfile": "simple"})
        self.assertEqual(job["implicitParameters"], {"radius": 24})

    def test_shortcut_accepts_json_camera(self) -> None:
        options = parse_snapshot_args(
            [
                "--input",
                "models/implicit-cad/orb.implicit.js",
                "--output",
                "tmp/orb.png",
                "--camera",
                '{"position":[4,-5,3],"target":[0,0,0],"up":[0,0,1],"zoom":1.4}',
            ]
        )

        job = load_job_from_options(options, stdin=_TtyStringIO(), cwd=Path.cwd())

        self.assertEqual(
            job["outputs"][0]["camera"],
            {"position": [4, -5, 3], "target": [0, 0, 0], "up": [0, 0, 1], "zoom": 1.4},
        )

    def test_shortcut_gif_defaults_to_orbit_mode(self) -> None:
        options = parse_snapshot_args(
            [
                "--input",
                "models/implicit-cad/orb.implicit.js",
                "--output",
                "tmp/orb.gif",
            ]
        )

        job = load_job_from_options(options, stdin=_TtyStringIO(), cwd=Path.cwd())

        self.assertEqual(job["mode"], "orbit")
        self.assertEqual(job["outputs"][0]["path"], "tmp/orb.gif")

    def test_render_job_derives_asset_root_from_input_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            models = root / "models" / "implicit-cad"
            models.mkdir(parents=True)
            (models / "orb.implicit.js").write_text(
                "export default {distance: 'length(p) - 1.0'};\n",
                encoding="utf-8",
            )

            original_timestamp = snapshot_main.snapshot_timestamp
            try:
                snapshot_main.snapshot_timestamp = lambda: "20260527T163012Z"
                packet = resolve_render_job_packet(
                    {
                        "input": "models/implicit-cad/orb.implicit.js",
                        "outputs": [{"path": "tmp/orb.png", "camera": "iso"}],
                    },
                    cwd=root,
                )
            finally:
                snapshot_main.snapshot_timestamp = original_timestamp

        job = packet["jobs"][0]
        output_path = Path(job["outputs"][0]["path"])
        self.assertEqual(packet["single"], True)
        self.assertEqual(job["resolved"]["kind"], "implicit")
        self.assertEqual(job["resolved"]["rootPath"], str(models))
        self.assertEqual(job["resolved"]["inputUrl"], "/__render_asset/orb.implicit.js")
        self.assertEqual(job["appearance"], "workbench")
        self.assertEqual(job["render"], {})
        self.assertEqual(job["graphics"], {})
        self.assertEqual(job["outputs"][0]["width"], 1600)
        self.assertEqual(job["outputs"][0]["height"], 1200)
        self.assertEqual(output_path.name, "orb_20260527T163012Z.png")

    def test_render_job_accepts_animation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            models = root / "models" / "implicit-cad"
            models.mkdir(parents=True)
            (models / "orb.implicit.js").write_text(
                "export default {glsl: 'float sdf(vec3 p) { return length(p) - 1.0; }'};\n",
                encoding="utf-8",
            )

            packet = resolve_render_job_packet(
                {
                    "input": "models/implicit-cad/orb.implicit.js",
                    "mode": "animate",
                    "outputs": [{"path": "tmp/orb.gif"}],
                },
                cwd=root,
            )

        self.assertEqual(packet["jobs"][0]["mode"], "animate")

    def test_rejects_non_implicit_input_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            model = root / "models" / "part.js"
            model.parent.mkdir()
            model.write_text("export default {};\n", encoding="utf-8")

            with self.assertRaisesRegex(SnapshotError, "only .implicit.js and .implicit.mjs"):
                resolve_render_job_packet(
                    {"input": "models/part.js", "outputs": [{"path": "tmp/part.png"}]},
                    cwd=root,
                )

    def test_rejects_legacy_top_level_theme_and_params_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            models = root / "models" / "implicit-cad"
            models.mkdir(parents=True)
            (models / "orb.implicit.js").write_text("export default {};\n", encoding="utf-8")

            with self.assertRaisesRegex(SnapshotError, "use appearance"):
                resolve_render_job_packet(
                    {
                        "input": "models/implicit-cad/orb.implicit.js",
                        "theme": "dark",
                        "outputs": [{"path": "tmp/orb.png"}],
                    },
                    cwd=root,
                )
            with self.assertRaisesRegex(SnapshotError, "use implicitParameters"):
                resolve_render_job_packet(
                    {
                        "input": "models/implicit-cad/orb.implicit.js",
                        "params": {},
                        "outputs": [{"path": "tmp/orb.png"}],
                    },
                    cwd=root,
                )

    def test_timestamp_output_path_preserves_extension(self) -> None:
        self.assertEqual(
            timestamp_output_path("snapshots/review.png", "20260527T163012Z"),
            "snapshots/review_20260527T163012Z.png",
        )

    def test_runtime_routes_are_self_contained(self) -> None:
        self.assertEqual(
            resolve_snapshot_route_file(f"{SNAPSHOT_ORIGIN}/render.html"),
            RENDER_HTML_PATH,
        )
        self.assertEqual(
            resolve_snapshot_route_file(f"{SNAPSHOT_ORIGIN}/snapshot-render.js"),
            RUNTIME_DIR / "snapshot-render.js",
        )


if __name__ == "__main__":
    unittest.main()
