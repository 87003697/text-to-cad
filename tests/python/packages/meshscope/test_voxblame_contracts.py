from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from meshscope.voxblame import (
    FORBIDDEN_FIELDS,
    REPORT_REQUIRED_FIELDS,
    SESSION_REQUIRED_FIELDS,
    SUMMARY_REQUIRED_FIELDS,
    UNSUPPORTED_OR_INVALID_STATE,
    UnsupportedOrInvalidVoxBlameState,
    validate_contract_bundle,
    validate_report_contract,
    validate_session_contract,
    validate_summary_contract,
)


FIXTURES = Path(__file__).parent / "fixtures" / "voxblame_contract"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class CanonicalVoxBlameContractTests(unittest.TestCase):
    def setUp(self):
        self.session = _fixture("session.json")
        self.report = _fixture("report.json")
        self.summary = _fixture("summary.json")

    def assert_invalid(self, callback, *, path: str | None = None):
        with self.assertRaises(UnsupportedOrInvalidVoxBlameState) as raised:
            callback()
        error = raised.exception
        self.assertEqual(UNSUPPORTED_OR_INVALID_STATE, str(error))
        self.assertEqual(UNSUPPORTED_OR_INVALID_STATE, error.classification)
        self.assertTrue(error.detail)
        if path is not None:
            self.assertEqual(path, error.path)
        return error

    def test_representative_new_shape_bundle_is_valid(self):
        self.assertIs(self.session, validate_session_contract(self.session))
        self.assertIs(self.report, validate_report_contract(self.report))
        self.assertIs(self.summary, validate_summary_contract(self.summary))
        validate_contract_bundle(self.session, self.report, self.summary)

    def test_top_level_contract_ledgers_are_exhaustive(self):
        self.assertEqual(set(self.session), SESSION_REQUIRED_FIELDS)
        self.assertEqual(set(self.report), REPORT_REQUIRED_FIELDS)
        self.assertEqual(set(self.summary), SUMMARY_REQUIRED_FIELDS)
        self.assertTrue(
            {
                "next_action",
                "remaining_error_count",
                "coarsest_first_error_depth",
                "change_counts",
                "measurement_contract",
                "bounds_world",
                "accepted",
            }.issubset(FORBIDDEN_FIELDS)
        )

    def test_depth_evidence_is_exactly_ordered_one_through_eight(self):
        reversed_report = deepcopy(self.report)
        reversed_report["errors_by_depth"].reverse()
        self.assert_invalid(
            lambda: validate_report_contract(reversed_report),
            path="$.errors_by_depth[0].depth",
        )

        short_summary = deepcopy(self.summary)
        short_summary["errors_by_depth"].pop()
        self.assert_invalid(
            lambda: validate_summary_contract(short_summary),
            path="$.errors_by_depth",
        )

    def test_nonzero_step_requires_explicit_earlier_ancestry(self):
        missing_parent = deepcopy(self.report)
        missing_parent["compare_to"] = None
        self.assert_invalid(
            lambda: validate_report_contract(missing_parent),
            path="$.compare_to",
        )

        future_parent = deepcopy(self.summary)
        future_parent["compare_to"] = 1
        self.assert_invalid(
            lambda: validate_summary_contract(future_parent),
            path="$.compare_to",
        )

        step_zero = deepcopy(self.report)
        step_zero["step"] = 0
        step_zero["compare_to"] = None
        target = step_zero["repair_targets"]["ordered_targets"][0]
        target["source_step"] = 0
        target["target_key"] = "step-000000:target-0000"
        validate_report_contract(step_zero)

    def test_legacy_shape_uses_the_single_unsupported_state_classification(self):
        legacy = {
            "schema": "voxblame.session/2",
            "max_depth": 8,
            "frame": {"scale": 1.0},
            "reference": {"source_sha256": "0" * 64},
        }
        self.assert_invalid(
            lambda: validate_session_contract(legacy),
            path="$.frame",
        )

    def test_mixed_shape_forbidden_field_uses_the_same_classification(self):
        mixed = deepcopy(self.summary)
        mixed["next_action"] = None
        self.assert_invalid(
            lambda: validate_summary_contract(mixed),
            path="$.next_action",
        )

    def test_corrupt_evidence_uses_the_same_classification(self):
        corrupt = deepcopy(self.report)
        corrupt["errors_by_depth"][7]["surface_error_count"] = 2
        self.assert_invalid(
            lambda: validate_report_contract(corrupt),
            path="$.errors_by_depth[7].surface_error_count",
        )

    def test_unknown_nested_field_uses_the_same_classification(self):
        unknown = deepcopy(self.report)
        unknown["measurement"]["mystery_sha256"] = "5" * 64
        self.assert_invalid(
            lambda: validate_report_contract(unknown),
            path="$.measurement.mystery_sha256",
        )

    def test_bundle_rejects_cross_artifact_identity_drift(self):
        drifted = deepcopy(self.summary)
        drifted["measurement"]["observable_sha256"] = "6" * 64
        self.assert_invalid(
            lambda: validate_contract_bundle(self.session, self.report, drifted),
            path="$.summary.measurement",
        )

    def test_bundle_rejects_a_summary_target_not_projected_from_report(self):
        drifted = deepcopy(self.summary)
        drifted["repair_targets"]["items"][0]["mask"]["logical_sha256"] = "6" * 64
        self.assert_invalid(
            lambda: validate_contract_bundle(self.session, self.report, drifted),
            path="$.summary.repair_targets.items[0]",
        )

    def test_target_kind_requires_its_exact_mask_storage_schema(self):
        wrong = deepcopy(self.report)
        wrong["repair_targets"]["ordered_targets"][0]["mask"][
            "storage_schema"
        ] = "exterior_grid_region_set/1"

        self.assert_invalid(
            lambda: validate_report_contract(wrong),
            path=(
                "$.repair_targets.ordered_targets[0].mask.storage_schema"
            ),
        )

    def test_exterior_resolution_accepts_signed_coarsening_levels(self):
        for document in (self.report, self.summary):
            document["exterior_surface"]["diagnostic_grid_depth"] = 0
            document["exterior_surface"]["coarsened"] = True

        validate_contract_bundle(self.session, self.report, self.summary)

        inconsistent = deepcopy(self.report)
        inconsistent["exterior_surface"]["coarsened"] = False
        self.assert_invalid(
            lambda: validate_report_contract(inconsistent),
            path="$.exterior_surface.coarsened",
        )

    def test_exterior_target_keeps_its_signed_diagnostic_resolution(self):
        report = deepcopy(self.report)
        report["exterior_surface"].update(
            {
                "surface_present": True,
                "surface_cell_count": 1,
                "bounds_canonical": {
                    "min": [0.6, 0.0, 0.0],
                    "max": [0.7, 0.1, 0.1],
                },
                "centroid_canonical": [0.6333333333333333, 0.0333333333333333, 0.0333333333333333],
                "nearest_overrun": 0.1,
                "farthest_overrun": 0.2,
                "outside_directions": ["+x"],
                "diagnostic_grid_depth": 0,
                "coarsened": True,
            }
        )
        exterior_target = deepcopy(
            report["repair_targets"]["ordered_targets"][0]
        )
        exterior_target.update(
            {
                "target_key": "step-000001:target-exterior",
                "kind": "exterior",
                "display_rank": 1,
                "bounds_canonical": report["exterior_surface"]["bounds_canonical"],
                "error_profile": {
                    "missing_surface_count": 0,
                    "excess_surface_count": 1,
                    "surface_error_count": 1,
                },
                "exterior": {
                    key: report["exterior_surface"][key]
                    for key in (
                        "centroid_canonical",
                        "surface_cell_count",
                        "nearest_overrun",
                        "farthest_overrun",
                        "outside_directions",
                        "diagnostic_grid_depth",
                        "coarsened",
                    )
                },
            }
        )
        exterior_target["mask"] = {
            "storage_schema": "exterior_grid_region_set/1",
            "path": "voxblame/steps/000001/targets/exterior.vbregions",
            "logical_sha256": "6" * 64,
            "region_count": 1,
        }
        exterior_target["component"] = {
            "component_key": "exterior-component",
            "split_index": 0,
            "split_count": 1,
            "split_reason": "not_split",
        }
        report["repair_targets"]["ordered_targets"].append(exterior_target)
        report["repair_targets"]["total"] = 2
        report["objective_facts"]["out_of_frame_clear"] = False

        validate_report_contract(report)


if __name__ == "__main__":
    unittest.main()
