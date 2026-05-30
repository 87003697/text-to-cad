from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadpy/src")

from cadpy.interference import InterferenceOptions, analyze_interference_scene  # noqa: E402
from cadpy.step_scene import LoadedStepScene, OccurrenceNode, _location_from_transform_matrix  # noqa: E402


IDENTITY_TRANSFORM = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def _translation_transform(x: float, y: float = 0.0, z: float = 0.0) -> tuple[float, ...]:
    return (1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1)


def _box_scene(step_path: Path, *, offset: float) -> LoadedStepScene:
    import build123d as b

    box = b.Box(1, 1, 1).wrapped
    right_transform = _translation_transform(offset)
    return LoadedStepScene(
        step_path=step_path.resolve(),
        roots=[
            OccurrenceNode(
                path=(1,),
                name="left",
                source_name="left",
                transform=IDENTITY_TRANSFORM,
                local_transform=IDENTITY_TRANSFORM,
                location=_location_from_transform_matrix(IDENTITY_TRANSFORM),
                prototype_key=1,
            ),
            OccurrenceNode(
                path=(2,),
                name="right",
                source_name="right",
                transform=right_transform,
                local_transform=right_transform,
                location=_location_from_transform_matrix(right_transform),
                prototype_key=2,
            ),
        ],
        prototype_shapes={1: box, 2: box},
        source_kind="step",
        step_hash="step-hash-123",
    )


def _nested_scene(step_path: Path) -> LoadedStepScene:
    import build123d as b

    box = b.Box(1, 1, 1).wrapped
    leaf_a_transform = _translation_transform(0.0)
    leaf_b_transform = _translation_transform(0.8)
    leaf_c_transform = _translation_transform(3.0)
    return LoadedStepScene(
        step_path=step_path.resolve(),
        roots=[
            OccurrenceNode(
                path=(1,),
                name="fixture",
                source_name="fixture",
                transform=IDENTITY_TRANSFORM,
                local_transform=IDENTITY_TRANSFORM,
                location=_location_from_transform_matrix(IDENTITY_TRANSFORM),
                prototype_key=None,
                children=[
                    OccurrenceNode(
                        path=(1, 1),
                        name="trusted_servo",
                        source_name="trusted_servo",
                        transform=IDENTITY_TRANSFORM,
                        local_transform=IDENTITY_TRANSFORM,
                        location=_location_from_transform_matrix(IDENTITY_TRANSFORM),
                        prototype_key=None,
                        children=[
                            OccurrenceNode(
                                path=(1, 1, 1),
                                name="servo_case",
                                source_name="servo_case",
                                transform=leaf_a_transform,
                                local_transform=leaf_a_transform,
                                location=_location_from_transform_matrix(leaf_a_transform),
                                prototype_key=1,
                            ),
                            OccurrenceNode(
                                path=(1, 1, 2),
                                name="servo_gear",
                                source_name="servo_gear",
                                transform=leaf_b_transform,
                                local_transform=leaf_b_transform,
                                location=_location_from_transform_matrix(leaf_b_transform),
                                prototype_key=2,
                            ),
                        ],
                    ),
                    OccurrenceNode(
                        path=(1, 2),
                        name="mount",
                        source_name="mount",
                        transform=leaf_c_transform,
                        local_transform=leaf_c_transform,
                        location=_location_from_transform_matrix(leaf_c_transform),
                        prototype_key=3,
                    ),
                ],
            )
        ],
        prototype_shapes={1: box, 2: box, 3: box},
        source_kind="step",
        step_hash="step-hash-123",
    )


class InterferenceTests(unittest.TestCase):
    def test_collision_pair_reports_unexpected_interference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadpy-interference-") as tempdir:
            scene = _box_scene(Path(tempdir) / "collision.step", offset=0.8)

            payload = analyze_interference_scene(scene)

        self.assertEqual("cadpy-interference-report", payload["kind"])
        self.assertEqual(2, payload["summary"]["bodyCount"])
        self.assertEqual(1, payload["summary"]["unexpectedInterferenceCount"])
        pair = payload["pairs"][0]
        self.assertEqual("collision", pair["status"])
        self.assertTrue(pair["violation"])
        self.assertEqual("forbid_interference", pair["policy"])
        self.assertGreater(pair["intersectionVolumeMm3"], 0.19)
        self.assertIn("suggestedCorrection", pair)

    def test_allowed_pair_is_suppressed_by_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadpy-interference-") as tempdir:
            scene = _box_scene(Path(tempdir) / "allowed.step", offset=0.8)

            payload = analyze_interference_scene(
                scene,
                options=InterferenceOptions(allow_pairs=("left:right",)),
            )

        self.assertEqual(0, payload["summary"]["unexpectedInterferenceCount"])
        self.assertEqual(0, payload["summary"]["reportedPairCount"])

    def test_allowed_pair_can_be_included_for_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadpy-interference-") as tempdir:
            scene = _box_scene(Path(tempdir) / "allowed.step", offset=0.8)

            payload = analyze_interference_scene(
                scene,
                options=InterferenceOptions(allow_pairs=("left:right",), include_allowed=True),
            )

        self.assertEqual(0, payload["summary"]["unexpectedInterferenceCount"])
        self.assertEqual(1, payload["summary"]["allowedInterferenceCount"])
        self.assertFalse(payload["pairs"][0]["violation"])
        self.assertEqual("allow_interference", payload["pairs"][0]["policy"])

    def test_body_depth_collapses_internal_subassembly_pairs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadpy-interference-") as tempdir:
            scene = _nested_scene(Path(tempdir) / "nested.step")

            leaf_payload = analyze_interference_scene(scene)
            collapsed_payload = analyze_interference_scene(scene, options=InterferenceOptions(body_depth=2))

        self.assertEqual(3, leaf_payload["summary"]["bodyCount"])
        self.assertEqual(1, leaf_payload["summary"]["unexpectedInterferenceCount"])
        self.assertEqual(2, collapsed_payload["summary"]["bodyCount"])
        self.assertEqual(0, collapsed_payload["summary"]["unexpectedInterferenceCount"])
        self.assertEqual(["o1.1.1", "o1.1.2"], collapsed_payload["bodies"][0]["sourceOccurrences"])

    def test_explicit_pair_reports_separated_result_even_outside_clearance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadpy-interference-") as tempdir:
            scene = _box_scene(Path(tempdir) / "separated.step", offset=5.0)

            payload = analyze_interference_scene(
                scene,
                options=InterferenceOptions(pairs=("left:right",)),
            )

        self.assertEqual(1, payload["summary"]["reportedPairCount"])
        self.assertEqual("separated", payload["pairs"][0]["status"])
        self.assertGreater(payload["pairs"][0]["minDistanceMm"], 3.0)


if __name__ == "__main__":
    unittest.main()
