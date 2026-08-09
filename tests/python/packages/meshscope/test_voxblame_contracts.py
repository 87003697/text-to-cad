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


if __name__ == "__main__":
    unittest.main()
