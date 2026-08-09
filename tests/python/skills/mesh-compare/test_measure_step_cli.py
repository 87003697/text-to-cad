"""Public ``mesh-compare voxblame-measure`` integration tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")
add_repo_path("skills/mesh-compare/scripts/mesh-compare")

import cli  # noqa: E402
from meshscope.voxblame import (  # noqa: E402
    MEASUREMENT_SUMMARY_SCHEMA,
    UNSUPPORTED_OR_INVALID_STATE,
    prepare_reference,
)


_FORBIDDEN_SUMMARY_FIELDS = {
    "accepted",
    "cad_command",
    "keep",
    "next_action",
    "priority",
    "revert",
    "stop_reason",
    "verdict",
}


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


class MeasureStepCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        mesh = trimesh.Trimesh(
            vertices=np.array(
                [[-0.5, -0.001, 0.0], [0.5, -0.001, 0.0], [-0.5, 0.001, 0.0]],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        )
        raw = self.root / "raw.ply"
        mesh.export(raw)
        self.reference = self.root / "input"
        prepare_reference(raw, self.reference)
        self.candidate = self.reference / "reference.ply"
        self.output = self.root / "voxblame"

    def invoke(self, *arguments: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(list(arguments))
        return status, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_measure_command_publishes_compact_facts_only_summary(self) -> None:
        status, payload, stderr = self.invoke(
            "voxblame-measure",
            str(self.candidate),
            "--reference",
            str(self.reference),
            "--output",
            str(self.output),
            "--step",
            "0",
        )

        self.assertEqual(0, status, stderr)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["idempotent"])
        self.assertEqual(str(self.output), payload["output"])
        summary = payload["measurement"]
        self.assertEqual(MEASUREMENT_SUMMARY_SCHEMA, summary["schema"])
        self.assertEqual(
            list(range(1, 9)),
            [item["depth"] for item in summary["errors_by_depth"]],
        )
        self.assertFalse(_FORBIDDEN_SUMMARY_FIELDS & _all_keys(summary))
        self.assertEqual(
            summary,
            json.loads(
                (self.output / "steps/000000/summary.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def test_measure_command_reports_failure_as_one_json_object(self) -> None:
        self.assertEqual(
            0,
            self.invoke(
                "voxblame-measure",
                str(self.candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.output),
                "--step",
                "0",
            )[0],
        )

        status, payload, stderr = self.invoke(
            "voxblame-measure",
            str(self.candidate),
            "--reference",
            str(self.reference),
            "--output",
            str(self.output),
            "--step",
            "1",
        )

        self.assertEqual(2, status)
        self.assertFalse(payload["ok"])
        self.assertEqual("measurement_failed", payload["error"]["classification"])
        self.assertIn("explicit earlier compare_to", payload["error"]["detail"])
        self.assertIn("measurement_failed", stderr)
        self.assertFalse((self.output / "steps/000001").exists())

    def test_measure_command_accepts_and_publishes_exterior_candidate_facts(
        self,
    ) -> None:
        exterior = trimesh.Trimesh(
            vertices=np.array(
                [[0.6, -0.05, 0.0], [0.7, -0.05, 0.0], [0.6, 0.05, 0.0]],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        )
        candidate = self.root / "exterior.obj"
        exterior.export(candidate)

        status, payload, stderr = self.invoke(
            "voxblame-measure",
            str(candidate),
            "--reference",
            str(self.reference),
            "--output",
            str(self.output),
            "--step",
            "0",
        )

        self.assertEqual(0, status, stderr)
        self.assertTrue(payload["ok"])
        evidence = payload["measurement"]["exterior_surface"]
        self.assertTrue(evidence["surface_present"])
        self.assertEqual(["+x"], evidence["outside_directions"])
        self.assertFalse(
            payload["measurement"]["objective_facts"]["out_of_frame_clear"]
        )
        snapshot = json.loads(
            (self.output / "steps/000000/exterior.json").read_text(encoding="utf-8")
        )
        self.assertEqual("voxblame.exterior-snapshot/1", snapshot["schema"])

    def test_targets_command_rejects_offset_beyond_frozen_target_count(self) -> None:
        self.assertEqual(
            0,
            self.invoke(
                "voxblame-measure",
                str(self.candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.output),
                "--step",
                "0",
            )[0],
        )

        status, payload, stderr = self.invoke(
            "voxblame-targets",
            "--output",
            str(self.output),
            "--step",
            "0",
            "--offset",
            "1",
        )

        self.assertEqual(2, status)
        self.assertEqual(
            UNSUPPORTED_OR_INVALID_STATE,
            payload["error"]["classification"],
        )
        self.assertIn("offset", payload["error"]["detail"])
        self.assertIn(UNSUPPORTED_OR_INVALID_STATE, stderr)

    def test_targets_command_pages_a_published_step_without_remeasurement(
        self,
    ) -> None:
        translated = trimesh.load(self.candidate, force="mesh", process=False)
        translated.apply_translation([0.0, 0.05, 0.0])
        candidate = self.root / "translated.ply"
        translated.export(candidate)
        status, measured, stderr = self.invoke(
            "voxblame-measure",
            str(candidate),
            "--reference",
            str(self.reference),
            "--output",
            str(self.output),
            "--step",
            "0",
        )
        self.assertEqual(0, status, stderr)

        status, payload, stderr = self.invoke(
            "voxblame-targets",
            "--output",
            str(self.output),
            "--step",
            "0",
            "--offset",
            "0",
        )

        self.assertEqual(0, status, stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(str(self.output), payload["output"])
        self.assertEqual(0, payload["step"])
        self.assertEqual(
            measured["measurement"]["repair_targets"],
            payload["repair_targets"],
        )
        self.assertFalse(_FORBIDDEN_SUMMARY_FIELDS & _all_keys(payload))

    def test_targets_command_rejects_forbidden_target_field_with_contract_error(
        self,
    ) -> None:
        translated = trimesh.load(self.candidate, force="mesh", process=False)
        translated.apply_translation([0.0, 0.05, 0.0])
        candidate = self.root / "translated-with-priority.ply"
        translated.export(candidate)
        self.assertEqual(
            0,
            self.invoke(
                "voxblame-measure",
                str(candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.output),
                "--step",
                "0",
            )[0],
        )
        report_path = self.output / "steps/000000/measurement.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["repair_targets"]["ordered_targets"][0]["priority"] = 1
        report_path.write_text(json.dumps(report), encoding="utf-8")

        status, payload, stderr = self.invoke(
            "voxblame-targets",
            "--output",
            str(self.output),
            "--step",
            "0",
        )

        self.assertEqual(2, status)
        self.assertEqual(
            UNSUPPORTED_OR_INVALID_STATE,
            payload["error"]["classification"],
        )
        self.assertIn("priority", payload["error"]["detail"])
        self.assertIn(UNSUPPORTED_OR_INVALID_STATE, stderr)

    def test_targets_command_rejects_bounds_not_derived_from_exact_mask(self) -> None:
        translated = trimesh.load(self.candidate, force="mesh", process=False)
        translated.apply_translation([0.0, 0.05, 0.0])
        candidate = self.root / "translated-with-stale-bounds.ply"
        translated.export(candidate)
        self.assertEqual(
            0,
            self.invoke(
                "voxblame-measure",
                str(candidate),
                "--reference",
                str(self.reference),
                "--output",
                str(self.output),
                "--step",
                "0",
            )[0],
        )
        report_path = self.output / "steps/000000/measurement.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        target = next(
            item
            for item in report["repair_targets"]["ordered_targets"]
            if item["kind"] == "interior"
        )
        target["bounds_canonical"] = {
            "min": [-0.5, -0.5, -0.5],
            "max": [0.5, 0.5, 0.5],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")

        status, payload, stderr = self.invoke(
            "voxblame-targets",
            "--output",
            str(self.output),
            "--step",
            "0",
        )

        self.assertEqual(2, status)
        self.assertEqual(
            UNSUPPORTED_OR_INVALID_STATE,
            payload["error"]["classification"],
        )
        self.assertIn("bounds", payload["error"]["detail"])
        self.assertIn(UNSUPPORTED_OR_INVALID_STATE, stderr)
if __name__ == "__main__":
    unittest.main()
