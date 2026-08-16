"""Focused tests for the THROWAWAY SAR-007 concurrency decision harness."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "packages/meshshot/prototypes/agent_runtime_boundary"))
import agent_runtime_concurrency_matrix as evidence  # noqa: E402
import concurrency  # noqa: E402


class AgentRuntimeConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = evidence.matrix()

    def test_fifth_job_queues_and_peak_is_exactly_four(self) -> None:
        admission = self.result["admission"]
        self.assertEqual(admission["activeCap"], 4)
        self.assertTrue(admission["fifthQueuedAtCap"])
        self.assertEqual(admission["observedPeak"], 4)
        self.assertEqual(
            self.result["verdict"], "ADOPT_FIRST_RELEASE_CAP_FOUR",
        )

    def test_only_immutable_identities_are_shared(self) -> None:
        admission = self.result["admission"]
        self.assertTrue(admission["sharedImmutableIdentitiesOnly"])
        self.assertTrue(admission["privateExecutionAuthority"])
        self.assertTrue(admission["allReceiptsExactAndAbsent"])
        self.assertEqual(
            sorted(admission["sharedSubject"]),
            sorted(evidence.SHARED_IDENTITY_KEYS),
        )

    def test_preview_broker_and_sidecar_are_lazy_and_job_private(self) -> None:
        rows = self.result["admission"]["executions"]
        self.assertEqual(
            [row["previewBrokerStarted"] for row in rows],
            [False, True, False, False, True],
        )
        self.assertEqual(
            [row["sidecarStarted"] for row in rows],
            [False, True, False, False, True],
        )
        self.assertEqual(len({row["brokerVolume"] for row in rows}), 5)
        self.assertEqual(len({row["ownerNonce"] for row in rows}), 5)
        preview_authorities = [
            row["previewAuthorityDigest"] for row in rows
            if row["previewRequested"]
        ]
        self.assertEqual(len(set(preview_authorities)), 2)
        self.assertEqual(len({
            row["sidecarOwnerNonce"] for row in rows
            if row["previewRequested"]
        }), 2)
        self.assertTrue(all(
            row["previewAuthorityDigest"] is None
            and row["sidecarOwnerNonce"] is None
            for row in rows if not row["previewRequested"]
        ))

    def test_output_and_terminal_receipts_map_to_exact_execution_subject(self) -> None:
        rows = self.result["admission"]["executions"]
        self.assertTrue(all(
            row["executionSubjectDigest"] == row["terminalReceiptSubjectDigest"]
            and row["outputSubjectDigest"] is not None
            and row["cleanupAbsence"] is True
            for row in rows
        ))

    def test_cross_job_substitution_matrix_is_closed(self) -> None:
        rows = self.result["substitutions"]["rows"]
        self.assertEqual(
            [row["substitution"] for row in rows],
            [
                "owner", "secret", "challenge", "broker-volume", "source",
                "input", "output", "receipt",
            ],
        )
        self.assertTrue(all(row["verdict"] == "PASS" for row in rows))
        self.assertTrue(all(
            row["foreignResourcesPreserved"] is True for row in rows
        ))

    def test_one_residue_does_not_delete_or_falsify_peers(self) -> None:
        result = self.result["failureIsolation"]
        self.assertEqual(result["failedJobFailureCheck"], "retained-resource")
        self.assertTrue(result["failedJobRetained"])
        self.assertEqual(result["peerStatuses"], ["succeeded"] * 3)
        self.assertTrue(result["peerRootsAbsent"])

    def test_harness_does_not_claim_real_container_evidence(self) -> None:
        self.assertFalse(self.result["dockerAuthorityInAgent"])
        self.assertEqual(self.result["realOciContainers"], "NOT_RUN")
        self.assertEqual(self.result["colimaConformance"], "NOT_RUN")
        self.assertEqual(self.result["cvmConformance"], "NOT_RUN")
        self.assertFalse(self.result["agentRuntimeVerified"])
        with self.assertRaisesRegex(ValueError, "exactly four"):
            concurrency.AdmissionController(5)


if __name__ == "__main__":
    unittest.main()
