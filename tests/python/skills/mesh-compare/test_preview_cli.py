"""Public ``mesh-compare voxblame-preview`` integration tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import BytesIO
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")
add_repo_path("skills/mesh-compare/scripts/mesh-compare")

import cli  # noqa: E402
from meshscope.voxblame import prepare_reference  # noqa: E402
from meshscope.voxblame.exterior import measure_exterior_surface  # noqa: E402
from meshshot import RenderedPreview, load_profile  # noqa: E402


class PreviewCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        raw = self.root / "raw.ply"
        trimesh.Trimesh(
            vertices=np.array(
                [
                    [-1.0, -0.5, -0.25],
                    [1.0, -0.5, -0.25],
                    [0.0, 0.75, 0.4],
                ],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        ).export(raw)
        self.reference = self.root / "input"
        prepare_reference(raw, self.reference)
        self.candidate = self.reference / "reference.ply"
        loaded_profile = load_profile()
        self.experiment = self.root / "experiment.json"
        self.experiment.write_text(
            json.dumps(
                {
                    "preview_profile": {
                        "name": loaded_profile.profile["name"],
                        "sha256": loaded_profile.sha256,
                    }
                }
            ),
            encoding="utf-8",
        )

    def preview_arguments(self, *arguments: str) -> tuple[str, ...]:
        return (
            "voxblame-preview",
            *arguments,
            "--experiment",
            str(self.experiment),
        )

    def write_selected_summary(
        self,
        *,
        candidate: Path | None = None,
        step: int = 1,
    ) -> Path:
        summary = json.loads(
            (
                Path(__file__).parents[2]
                / "packages/meshscope/fixtures/voxblame_contract/summary.json"
            ).read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (self.reference / "input.json").read_text(encoding="utf-8")
        )
        summary["step"] = step
        summary["canonical_reference"]["canonical_reference_sha256"] = (
            manifest["canonical_reference_sha256"]
        )
        selected_candidate = candidate or self.candidate
        summary["measurement"]["candidate_mesh_sha256"] = hashlib.sha256(
            selected_candidate.read_bytes()
        ).hexdigest()
        path = self.root / "voxblame" / "steps" / f"{step:06d}" / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary), encoding="utf-8")
        return path

    def invoke(self, *arguments: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(list(arguments))
        return status, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_preview_command_atomically_publishes_bound_step_png_and_metadata(self) -> None:
        output = self.root / "preview"
        arguments = self.preview_arguments(
            str(self.candidate),
            "--reference",
            str(self.reference),
            "--output",
            str(output),
            "--variant",
            "step",
        )

        status, payload, stderr = self.invoke(*arguments)

        self.assertEqual(0, status, stderr)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["idempotent"])
        self.assertEqual({"preview.json", "preview.png"}, {path.name for path in output.iterdir()})
        metadata = json.loads((output / "preview.json").read_text(encoding="utf-8"))
        self.assertEqual("voxblame.preview/1", metadata["schema"])
        self.assertEqual("step", metadata["render_variant"])
        self.assertEqual("trellis2_canonical/1", metadata["canonical_frame"]["coordinate_contract"])
        self.assertEqual("cadena_residual_eight_view/1", metadata["profile"]["name"])
        self.assertEqual("meshshot-three-webgl", metadata["profile"]["renderer"]["name"])
        self.assertIn("camera", metadata["profile"])
        self.assertIn("lighting", metadata["profile"])
        self.assertIn("padding", metadata["profile"])
        self.assertIn("downsampling", metadata["profile"])
        self.assertEqual(
            ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
            [view["name"] for view in metadata["ordered_views"]],
        )
        self.assertEqual(
            metadata["reference"]["canonical_reference_sha256"],
            json.loads((self.reference / "input.json").read_text())["canonical_reference_sha256"],
        )
        self.assertEqual(metadata, payload["preview"])
        self.assertRegex(metadata["preview_identity_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            metadata["image"]["sha256"],
            hashlib.sha256((output / "preview.png").read_bytes()).hexdigest(),
        )
        with Image.open(BytesIO((output / "preview.png").read_bytes())) as image:
            self.assertEqual("RGB", image.mode)
            self.assertEqual((504, 1008), image.size)
        first_png = (output / "preview.png").read_bytes()
        status, rerun, stderr = self.invoke(*arguments)
        self.assertEqual(0, status, stderr)
        self.assertTrue(rerun["idempotent"])
        self.assertEqual(first_png, (output / "preview.png").read_bytes())
        self.assertFalse(any(path.name.startswith(".tmp-") for path in self.root.iterdir()))

    def test_preview_preserves_exterior_measurement_and_draws_direction_markers(self) -> None:
        candidate = self.root / "exterior.ply"
        exterior_mesh = trimesh.Trimesh(
            vertices=np.array(
                [[0.62, -0.08, 0.0], [0.72, -0.08, 0.0], [0.62, 0.08, 0.0]],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        )
        exterior_mesh.export(candidate)
        reloaded = trimesh.load(candidate, force="mesh", process=False)
        objective = measure_exterior_surface(np.asarray(reloaded.triangles, dtype=np.float64))
        output = self.root / "exterior-preview"

        status, payload, stderr = self.invoke(*self.preview_arguments(
            str(candidate),
            "--reference",
            str(self.reference),
            "--output",
            str(output),
            "--variant",
            "step",
        ))

        self.assertEqual(0, status, stderr)
        evidence = payload["preview"]["exterior_surface"]
        self.assertTrue(evidence["out_of_frame"])
        self.assertEqual(["+x"], evidence["outside_directions"])
        self.assertEqual(1, evidence["component_count"])
        self.assertEqual(objective.logical_sha256, evidence["measurement_snapshot_sha256"])
        self.assertEqual(
            ["+x"] * 8,
            [
                markers["markers"][0]["direction"]
                for markers in evidence["edge_direction_markers"]
            ],
        )

    def test_final_preview_rejects_missing_selected_step_before_rendering(self) -> None:
        with mock.patch.object(cli, "render_residual_preview") as render:
            status, payload, stderr = self.invoke(*self.preview_arguments(
                str(self.candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.root / "invalid-final"),
                "--variant",
                "final",
            ))

        self.assertEqual(2, status)
        self.assertEqual("preview_failed", payload["error"]["classification"])
        self.assertIn("selected step", payload["error"]["detail"])
        self.assertIn("preview_failed", stderr)
        render.assert_not_called()

    def test_final_preview_binds_selected_step_and_high_resolution_dimensions(self) -> None:
        output = self.root / "final-preview"
        selected_summary = self.write_selected_summary(step=1)

        status, payload, stderr = self.invoke(*self.preview_arguments(
            str(self.candidate),
            "--reference",
            str(self.reference),
            "--output",
            str(output),
            "--variant",
            "final",
            "--selected-step",
            "1",
            "--selected-summary",
            str(selected_summary),
        ))

        self.assertEqual(0, status, stderr)
        metadata = payload["preview"]
        self.assertEqual("final", metadata["render_variant"])
        self.assertEqual(1, metadata["selected_step"])
        self.assertEqual(
            hashlib.sha256(selected_summary.read_bytes()).hexdigest(),
            metadata["selected_summary_sha256"],
        )
        self.assertEqual(1008, metadata["image"]["width"])
        self.assertEqual(2016, metadata["image"]["height"])
        with Image.open(output / "preview.png") as image:
            self.assertEqual((1008, 2016), image.size)

    def test_preview_rejects_renderer_profile_identity_conflict(self) -> None:
        loaded = load_profile()
        image = Image.new("RGB", (504, 1008), (0, 0, 0))
        encoded = BytesIO()
        image.save(encoded, format="PNG")
        views = tuple(
            {
                **view,
                "markers": [],
                "framing": {
                    "projection": (
                        "orthographic"
                        if view["kind"] == "axial_depth"
                        else "perspective"
                    )
                },
            }
            for view in loaded.profile["views"]
        )
        rendered = RenderedPreview(
            png_bytes=encoded.getvalue(),
            variant="step",
            profile_sha256="0" * 64,
            views=views,
        )
        output = self.root / "profile-conflict"

        with mock.patch.object(cli, "render_residual_preview", return_value=rendered):
            status, payload, stderr = self.invoke(*self.preview_arguments(
                str(self.candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(output),
                "--variant",
                "step",
            ))

        self.assertEqual(2, status)
        self.assertEqual("preview_failed", payload["error"]["classification"])
        self.assertIn("profile identity conflict", payload["error"]["detail"])
        self.assertIn("preview_failed", stderr)
        self.assertFalse(output.exists())

    def test_renderer_failure_uses_one_compact_json_error(self) -> None:
        with mock.patch.object(
            cli,
            "render_residual_preview",
            side_effect=RuntimeError("renderer exploded " + "x" * 5000),
        ):
            status, payload, stderr = self.invoke(*self.preview_arguments(
                str(self.candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.root / "failed-preview"),
                "--variant",
                "step",
            ))

        self.assertEqual(2, status)
        self.assertEqual("preview_failed", payload["error"]["classification"])
        self.assertLessEqual(len(payload["error"]["detail"]), 1000)
        self.assertIn("renderer exploded", payload["error"]["detail"])
        self.assertEqual(1, stderr.count("preview_failed"))

    def test_renderer_failure_publishes_only_closed_phases(self) -> None:
        cases = {
            "runtime": "preview_runtime_failed",
            "dependency": "preview_dependency_failed",
            "browser_launch": "preview_browser_launch_failed",
            "browser_render": "preview_browser_render_failed",
            "browser_result": "preview_browser_result_failed",
            "shell": "preview_failed",
        }
        for phase, expected in cases.items():
            with self.subTest(phase=phase):
                with mock.patch.object(
                    cli,
                    "render_residual_preview",
                    side_effect=cli.MeshshotError(
                        "sensitive renderer detail",
                        phase=phase,
                    ),
                ):
                    status, payload, stderr = self.invoke(*self.preview_arguments(
                        str(self.candidate),
                        "--reference",
                        str(self.reference),
                        "--output",
                        str(self.root / f"failed-{phase}"),
                        "--variant",
                        "step",
                    ))

                self.assertEqual(2, status)
                self.assertEqual(expected, payload["error"]["classification"])
                self.assertIn(expected, stderr)

    def test_preview_rejects_experiment_profile_conflict_before_rendering(self) -> None:
        experiment = json.loads(self.experiment.read_text(encoding="utf-8"))
        experiment["preview_profile"]["sha256"] = "0" * 64
        self.experiment.write_text(json.dumps(experiment), encoding="utf-8")

        with mock.patch.object(cli, "render_residual_preview") as render:
            status, payload, _stderr = self.invoke(*self.preview_arguments(
                str(self.candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.root / "profile-conflict"),
            ))

        self.assertEqual(2, status)
        self.assertIn("experiment preview profile", payload["error"]["detail"])
        render.assert_not_called()

    def test_final_preview_rejects_candidate_not_bound_to_selected_step(self) -> None:
        other = self.root / "other.ply"
        trimesh.Trimesh(
            vertices=np.array(
                [[-0.2, -0.2, 0.0], [0.2, -0.2, 0.0], [0.0, 0.3, 0.0]],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        ).export(other)
        selected_summary = self.write_selected_summary(candidate=other, step=1)

        with mock.patch.object(cli, "render_residual_preview") as render:
            status, payload, _stderr = self.invoke(*self.preview_arguments(
                str(self.candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.root / "candidate-conflict"),
                "--variant",
                "final",
                "--selected-step",
                "1",
                "--selected-summary",
                str(selected_summary),
            ))

        self.assertEqual(2, status)
        self.assertIn("selected summary candidate", payload["error"]["detail"])
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
