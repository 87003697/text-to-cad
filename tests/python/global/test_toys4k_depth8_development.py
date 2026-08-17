from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "scripts/pilot/toys4k-depth8-development.py"
CLASSIFICATION = "Development/MVP — Not Sealed, Not Formal, Not Verified, Not Production"
ORDER = ("bottle_bottle_089", "toaster_toaster_005", "mushroom_mushroom_018", "airplane_airplane_016")
ROUTES = {ORDER[0]: "cad", ORDER[1]: "cad", ORDER[2]: "implicit-cad", ORDER[3]: "implicit-cad"}
EVIDENCE = {
    ORDER[0]: {"rubricRule": "agent-judgment-machinable", "source": "skills/mesh-to-cad/references/routing-rubric.md"},
    ORDER[1]: {"rubricRule": "agent-judgment-machinable", "source": "skills/mesh-to-cad/references/routing-rubric.md"},
    ORDER[2]: {"rubricRule": "organic-plant", "source": "skills/mesh-to-cad/references/routing-rubric.md"},
    ORDER[3]: {"rubricRule": "face-count-over-100k", "observedFaces": 305796, "plyHeader": "element face 305796", "source": "skills/mesh-to-cad/references/routing-rubric.md"},
}


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Case:
    def __init__(self, root: Path, key: str = ORDER[0]) -> None:
        self.root, self.key = root, key
        self.fixture_root = root / "fixtures"; self.fixture_root.mkdir()
        self.fixture_bytes = b"ply\nformat ascii 1.0\nend_header\n"
        (self.fixture_root / f"{key}.ply").write_bytes(self.fixture_bytes)
        self.contract = root / "contract.json"
        self.contract.write_text(json.dumps({"schema": "text-to-cad.toys4k-provider-free-conformance/1", "fixtureKey": key, "paidDispatchCount": 0, "route": ROUTES[key], "bytes": len(self.fixture_bytes), "sha256": digest(self.fixture_bytes)}))
        self.sequence = root / "sequence.json"
        self.sequence.write_text(json.dumps({"schema": "text-to-cad.toys4k-depth8-sequence/1", "completed": list(ORDER[:ORDER.index(key)]), "blockingFailures": []}))
        self.run_id = f"run-{key.replace('_', '-')}-01"
        self.ledger = root / "ledger.jsonl"; self.write_ledger(self.valid_ledger())
        self.evidence_path = root / "evidence.json"
        self.evidence = self.valid_evidence(); self.write_evidence()

    def valid_ledger(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        job_ids = [f"prior-{key}" for key in ORDER[:ORDER.index(self.key)]] + [self.run_id]
        for job_id in job_ids:
            common = {"schema": "text-to-cad.development-venus-ledger/1", "jobId": job_id, "attempt": 1}
            rows.extend([
                {**common, "event": "reserve", "reservedUsd": "2.450000", "pricingAuthority": "iWiki-4020336897-v54-2026-08-14", "inputTokenUpperBound": 200000, "outputTokenUpperBound": 40000, "requestBytes": 1000},
                {**common, "event": "settle", "releasedReservedUsd": "2.450000", "settledCostUpperBoundUsd": "0.100000", "usage": {"inputTokens": 1000, "outputTokens": 100}},
            ])
        return rows

    def write_ledger(self, rows: list[dict[str, object]]) -> None:
        self.ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def valid_evidence(self) -> dict[str, object]:
        route_suffix = ".step" if ROUTES[self.key] == "cad" else ".js"
        artifacts = []
        for kind, rel, data in (
            ("source", "source/model.txt", b"source"), ("route-artifact", f"route/model{route_suffix}", b"route"),
            ("glb", "geometry/final.glb", b"glb"), ("events", "logs/events.jsonl", b"{}\n"),
            ("stdout", "logs/stdout.txt", b"ok\n"), ("stderr", "logs/stderr.txt", b""),
            ("terminal-receipt", "terminal/receipt.json", b"{}\n"),
        ):
            path = self.root / rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            artifacts.append({"kind": kind, "path": rel, "sha256": digest(data), "runId": self.run_id})
        nodes = []; parent = None
        for number, kind in enumerate(("input", "attempt", "measured-step", "selection", "final-delivery")):
            payload = {"ordinal": number, "fixture": self.key}; node_digest = digest(canonical(payload))
            nodes.append({"type": kind, "payload": payload, "digest": node_digest, "parentDigest": parent, "runId": self.run_id}); parent = node_digest
        depths = [{"depth": depth, "authority": "mesh-compare", "referenceSha256": digest(self.fixture_bytes), "missing": 9 - depth, "excess": depth, "exteriorClear": True} for depth in range(1, 9)]
        resources = sorted(f"t2c-{self.run_id}-{kind}" for kind in ("agent", "proxy", "internal", "egress", "evidence", "secrets", "process-group"))
        mapped = {item["kind"]: item for item in artifacts}
        return {
            "schema": "text-to-cad.toys4k-depth8-evidence/1", "classification": CLASSIFICATION, "runId": self.run_id,
            "fixture": {"key": self.key, "bytes": len(self.fixture_bytes), "sha256": digest(self.fixture_bytes)},
            "route": {"chosen": ROUTES[self.key], "consideredAlternative": "implicit-cad" if ROUTES[self.key] == "cad" else "cad", "evidence": EVIDENCE[self.key]},
            "artifacts": artifacts,
            "workspace": {"schema": "mesh-to-cad.workspace/1", "runId": self.run_id, "nodes": nodes, "depthMeasurements": depths, "selectedStep": 0, "accepted": False, "stopReason": "no_feasible_strategy"},
            "finalDelivery": {"schema": "mesh-to-cad.final-delivery/1", "runId": self.run_id, "selectedStep": 0, "accepted": False, "sourceSha256": mapped["source"]["sha256"], "glbSha256": mapped["glb"]["sha256"], "verification": {"authority": "mesh-compare", "observableGeometry": True}},
            "terminal": {"status": "development-unaccepted"},
            "cleanup": {"ownerRunId": self.run_id, "exactResources": resources, "absence": {name: True for name in resources}},
        }

    def write_evidence(self) -> None:
        self.evidence_path.write_text(json.dumps(self.evidence))

    def args(self, *, evidence: bool = True) -> list[str]:
        args = [self.key, "--fixture-root", str(self.fixture_root), "--provider-free-conformance-contract", str(self.contract), "--prior-total-ledger", str(self.ledger)]
        if self.key != ORDER[0]: args.extend(("--sequence-state", str(self.sequence)))
        if evidence: args.extend(("--evidence", str(self.evidence_path)))
        return args

    def adapter(self, *, sleep_seconds: int = 0) -> Path:
        adapter = self.root / "provider-free-adapter.py"
        adapter_ledger = self.root / "adapter-ledger.jsonl"
        adapter_ledger.write_bytes(self.ledger.read_bytes())
        script = f'''#!{sys.executable}
import json,pathlib,shutil,sys,time
request=json.loads(sys.stdin.buffer.readline())
time.sleep({sleep_seconds})
output=pathlib.Path(request["outputPath"])
source=pathlib.Path({str(self.root)!r})
for name in ("source","route","geometry","logs","terminal"):
    shutil.copytree(source/name,output/name)
shutil.copyfile(source/"evidence.json",output/"evidence.json")
shutil.copyfile(source/"adapter-ledger.jsonl",output/"ledger.jsonl")
receipt={{"schema":"text-to-cad.toys4k-depth8-adapter-terminal/1","status":"development-terminal","fixture":request["fixture"],"route":request["route"]["chosen"],"paidDispatchCount":0,"wholeJobRetryCount":0,"processGroupAbsent":True,"cleanupAbsence":True,"evidencePath":"evidence.json","ledgerPath":"ledger.jsonl"}}
print(json.dumps(receipt,sort_keys=True,separators=(",",":")))
'''
        adapter.write_text(script); adapter.chmod(0o700); return adapter


class Toys4KDepth8DevelopmentCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_cli(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run([sys.executable, str(CLI), *args], cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return result, json.loads(result.stdout)

    def assert_failure(self, case: Case, check: str) -> None:
        case.write_evidence(); result, receipt = self.run_cli(*case.args()); self.assertEqual(2, result.returncode, result.stderr); self.assertEqual(check, receipt["failureCheck"]); self.assertEqual(0, receipt["paidDispatchCount"])

    # 1
    def test_unknown_key_and_path_inputs_fail_before_dispatch(self) -> None:
        for candidate in ("cup_cup_033", "../bottle_bottle_089", "/tmp/input.ply"):
            result, receipt = self.run_cli(candidate); self.assertEqual(2, result.returncode); self.assertEqual("fixture-allowlist", receipt["failureCheck"]); self.assertEqual((0, 0), (receipt["attemptCount"], receipt["paidDispatchCount"]))

    # 2 and 13
    def test_missing_pointer_and_mismatch_are_attempt_zero_preparation_failures(self) -> None:
        for mutation in (None, b"version https://git-lfs.github.com/spec/v1\n", b"drift"):
            with self.subTest(mutation=mutation):
                child = self.root / str(len(list(self.root.iterdir()))); child.mkdir(); case = Case(child); path = case.fixture_root / f"{case.key}.ply"
                if mutation is None: path.unlink()
                else: path.write_bytes(mutation)
                result, receipt = self.run_cli(*case.args(evidence=False)); self.assertEqual(2, result.returncode); self.assertEqual("fixture-identity", receipt["failureCheck"]); self.assertEqual((0, 0), (receipt["attemptCount"], receipt["paidDispatchCount"]))

    # 3
    def test_closed_routes_cannot_be_substituted(self) -> None:
        for key in ORDER[:3]:
            child = self.root / key; child.mkdir(); case = Case(child, key); case.evidence["route"]["chosen"] = "cad" if ROUTES[key] == "implicit-cad" else "implicit-cad"; self.assert_failure(case, "route-policy")

    # 4
    def test_airplane_records_current_face_count_rubric_evidence(self) -> None:
        case = Case(self.root, ORDER[3]); result, receipt = self.run_cli(*case.args()); self.assertEqual(0, result.returncode); self.assertEqual(305796, receipt["routeEvidence"]["observedFaces"])

    # 5
    def test_missing_cross_run_or_wrong_route_artifacts_are_rejected(self) -> None:
        case = Case(self.root); case.evidence["artifacts"][0]["runId"] = "other-run-01"; self.assert_failure(case, "artifact-identity")

    # 6
    def test_workspace_missing_reordered_tampered_and_cross_run_nodes_are_rejected(self) -> None:
        for mutation in ("missing", "reordered", "tampered", "cross-run"):
            child = self.root / mutation; child.mkdir(); case = Case(child); nodes = case.evidence["workspace"]["nodes"]
            if mutation == "missing": nodes.pop()
            elif mutation == "reordered": nodes[1], nodes[2] = nodes[2], nodes[1]
            elif mutation == "tampered": nodes[2]["payload"]["ordinal"] = 99
            else: nodes[2]["runId"] = "other-run-01"
            self.assert_failure(case, "workspace")

    # 7
    def test_depths_one_through_eight_and_independent_authority_are_required(self) -> None:
        for mutation in ("missing", "self-auth", "wrong-input"):
            child = self.root / mutation; child.mkdir(); case = Case(child); depths = case.evidence["workspace"]["depthMeasurements"]
            if mutation == "missing": depths.pop()
            elif mutation == "self-auth": depths[-1]["authority"] = "toys4k-runner"
            else: depths[-1]["referenceSha256"] = "0" * 64
            self.assert_failure(case, "depth-evidence")

    # 8
    def test_final_delivery_and_observable_geometry_verification_are_required(self) -> None:
        case = Case(self.root); case.evidence["finalDelivery"]["verification"]["observableGeometry"] = False; self.assert_failure(case, "final-delivery")

    # 9
    def test_attempt_request_token_job_and_total_caps_fail_closed(self) -> None:
        mutations = []
        base = Case(self.root); common = base.valid_ledger()[0]
        mutations.append([{**common, "attempt": attempt} for attempt in range(1, 18)])
        rows = base.valid_ledger(); rows[0]["requestBytes"] = 200001; mutations.append(rows)
        rows = base.valid_ledger(); rows[1]["usage"]["outputTokens"] = 40001; mutations.append(rows)
        mutations.append([{**common, "jobId": f"job-{job}", "attempt": 1} for job in range(5)])
        for index, rows in enumerate(mutations):
            child = self.root / f"cap-{index}"; child.mkdir(); case = Case(child); case.write_ledger(rows); self.assert_failure(case, "accounting")

    # 10
    def test_ledger_order_duplicate_release_pricing_and_replay_are_rejected(self) -> None:
        base = Case(self.root); variants = []
        rows = base.valid_ledger(); variants.append(rows[::-1])
        rows = base.valid_ledger(); variants.append([rows[0], copy.deepcopy(rows[0]), rows[1]])
        rows = base.valid_ledger(); rows[1]["releasedReservedUsd"] = "1.0"; variants.append(rows)
        rows = base.valid_ledger(); rows[0]["reservedUsd"] = "0.01"; variants.append(rows)
        rows = base.valid_ledger(); rows[0]["pricingAuthority"] = "wrong"; variants.append(rows)
        for index, rows in enumerate(variants):
            child = self.root / f"ledger-{index}"; child.mkdir(); case = Case(child); case.write_ledger(rows); self.assert_failure(case, "accounting")

    # 11
    def test_ambiguous_timeout_retains_reservation_with_no_whole_job_retry_and_cleanup_absence(self) -> None:
        case = Case(self.root); rows = case.valid_ledger()[:1] + [{"schema": "text-to-cad.development-venus-ledger/1", "jobId": case.run_id, "attempt": 1, "event": "transport-error"}]; case.write_ledger(rows)
        result, receipt = self.run_cli(*case.args()); self.assertEqual(0, result.returncode); self.assertEqual("2.450000", receipt["accounting"]["conservativeUsd"]); self.assertFalse(receipt["policy"]["wholeJobRetry"])

    def test_execute_supervises_one_fresh_provider_free_adapter_and_validates_its_terminal_evidence(self) -> None:
        case = Case(self.root); output = self.root / "executed"; adapter = case.adapter()
        case.ledger.write_text("")
        args = case.args(evidence=False) + ["--execute", "--output-root", str(output), "--provider-free-adapter", str(adapter)]
        result, receipt = self.run_cli(*args)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("development-evidence-complete", receipt["status"])
        self.assertEqual(0, receipt["paidDispatchCount"])
        self.assertEqual(0, receipt["execution"]["wholeJobRetryCount"])
        self.assertTrue(receipt["execution"]["processGroupAbsent"])

    def test_execute_timeout_kills_the_adapter_process_group_without_retry(self) -> None:
        case = Case(self.root); output = self.root / "timed-out"; adapter = case.adapter(sleep_seconds=10)
        case.ledger.write_text("")
        args = case.args(evidence=False) + ["--execute", "--output-root", str(output), "--provider-free-adapter", str(adapter), "--timeout-seconds", "1"]
        result, receipt = self.run_cli(*args)
        self.assertEqual(2, result.returncode)
        self.assertEqual("execution-timeout", receipt["failureCheck"])
        self.assertEqual((0, 0), (receipt["attemptCount"], receipt["paidDispatchCount"]))

    # 12
    def test_secret_or_capability_material_is_rejected_from_evidence_and_logs(self) -> None:
        case = Case(self.root); case.evidence["debug"] = "Authorization: Bearer abcdefghijklmnop"; self.assert_failure(case, "credential-safety")

    # 14
    def test_cleanup_requires_exact_owned_resources_and_absence(self) -> None:
        case = Case(self.root); case.evidence["cleanup"]["exactResources"].append("unrelated-container"); self.assert_failure(case, "cleanup-absence")

    # 15
    def test_valid_unaccepted_selection_stays_unaccepted_and_is_evidence_complete(self) -> None:
        case = Case(self.root); result, receipt = self.run_cli(*case.args()); self.assertEqual(0, result.returncode); self.assertEqual("development-evidence-complete", receipt["status"]); self.assertFalse(receipt["accepted"]); self.assertEqual("no_feasible_strategy", receipt["stopReason"])

    # 16
    def test_next_sample_blocks_only_on_serialized_safety_barrier(self) -> None:
        case = Case(self.root, ORDER[1]); result, _ = self.run_cli(*case.args()); self.assertEqual(0, result.returncode)
        state = json.loads(case.sequence.read_text()); state["blockingFailures"] = ["cleanup"]; case.sequence.write_text(json.dumps(state)); result, receipt = self.run_cli(*case.args()); self.assertEqual(2, result.returncode); self.assertEqual("sequence-gate", receipt["failureCheck"])


if __name__ == "__main__": unittest.main()
