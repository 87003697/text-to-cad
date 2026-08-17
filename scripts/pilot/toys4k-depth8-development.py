#!/usr/bin/env python3
"""Fail-closed Development/MVP gate for four Toys4K depth-8 runs.

This command does not read credentials or transport requests. It binds the
input, route, serialized admission, accounting, Workspace, Final Delivery and
cleanup evidence around the existing Development proxy/supervisor boundary.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any

CLASSIFICATION = "Development/MVP — Not Sealed, Not Formal, Not Verified, Not Production"
ORDER = ("bottle_bottle_089", "toaster_toaster_005", "mushroom_mushroom_018", "airplane_airplane_016")
RUBRIC = "skills/mesh-to-cad/references/routing-rubric.md"
FIXTURES: dict[str, dict[str, Any]] = {
    "bottle_bottle_089": {"bytes": 110741, "sha256": "80353ef44563ac1eaeec84d1188059ad5ab373aa1e258d710588e6650789e214", "route": "cad", "routeEvidence": {"rubricRule": "agent-judgment-machinable", "source": RUBRIC}},
    "toaster_toaster_005": {"bytes": 53914, "sha256": "ee28c82344d82425d4c10840aff55365679f6d7154f09bdb753d112e776cd605", "route": "cad", "routeEvidence": {"rubricRule": "agent-judgment-machinable", "source": RUBRIC}},
    "mushroom_mushroom_018": {"bytes": 68280, "sha256": "49d27f6e853a80fc9450e5e650deaa34b0d731c37cba39838a88901385362990", "route": "implicit-cad", "routeEvidence": {"rubricRule": "organic-plant", "source": RUBRIC}},
    "airplane_airplane_016": {"bytes": 10475507, "sha256": "72abf42e0efc7cb7023d10b7677a20c16a28b03adf05ed6414eae6e102f562d9", "route": "implicit-cad", "routeEvidence": {"rubricRule": "face-count-over-100k", "observedFaces": 305796, "plyHeader": "element face 305796", "source": RUBRIC}},
}
LEDGER_SCHEMA = "text-to-cad.development-venus-ledger/1"
PRICING = "iWiki-4020336897-v54-2026-08-14"
SECRET = re.compile(r"(?i)(venus[_-]?token|authorization\s*:|bearer\s+[A-Za-z0-9._~+/=-]{8,}|api[_-]?key|secret[_-]?key)")


class GateError(RuntimeError):
    def __init__(self, check: str, detail: str) -> None:
        super().__init__(detail); self.check = check; self.detail = detail


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path, check: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise GateError(check, "authority must be one regular file")
    try: return json.loads(path.read_bytes())
    except (OSError, ValueError) as exc: raise GateError(check, "authority is not valid JSON") from exc


def contract(args: argparse.Namespace, key: str) -> dict[str, Any]:
    fixed = FIXTURES[key]
    path = args.provider_free_conformance_contract
    if path is None: return fixed
    value = read_json(path, "conformance-contract")
    if not isinstance(value, dict) or value.get("schema") != "text-to-cad.toys4k-provider-free-conformance/1" or value.get("fixtureKey") != key or value.get("paidDispatchCount") != 0:
        raise GateError("conformance-contract", "provider-free contract is invalid")
    if value.get("route") != fixed["route"]:
        raise GateError("route-policy", "conformance contract cannot override the closed route")
    return {**fixed, "bytes": value.get("bytes"), "sha256": value.get("sha256")}


def input_identity(root: Path, key: str, expected: dict[str, Any]) -> dict[str, Any]:
    path = root / f"{key}.ply"
    try: info = path.lstat()
    except OSError as exc: raise GateError("fixture-identity", "allowlisted fixture is absent") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GateError("fixture-identity", "allowlisted fixture is not one regular file")
    data = path.read_bytes()
    if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise GateError("fixture-identity", "allowlisted fixture is an LFS pointer")
    observed = {"key": key, "bytes": len(data), "sha256": digest(data)}
    if (observed["bytes"], observed["sha256"]) != (expected["bytes"], expected["sha256"]):
        raise GateError("fixture-identity", "allowlisted fixture bytes or digest drifted")
    return observed


def sequence(path: Path | None, key: str) -> None:
    prefix = list(ORDER[:ORDER.index(key)])
    if path is None:
        if prefix: raise GateError("sequence-gate", "prior serialized state is required")
        return
    value = read_json(path, "sequence-gate")
    if not isinstance(value, dict) or value.get("schema") != "text-to-cad.toys4k-depth8-sequence/1" or value.get("completed") != prefix:
        raise GateError("sequence-gate", "serialized completion prefix is invalid")
    if value.get("blockingFailures"):
        raise GateError("sequence-gate", "prior identity, ledger, credential-safety, or cleanup failure blocks dispatch")


def ledger_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None: return []
    if path.is_symlink() or not path.is_file(): raise GateError("accounting", "ledger must be one regular file")
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError) as exc: raise GateError("accounting", "ledger is not valid JSONL") from exc
    if not all(isinstance(row, dict) for row in rows): raise GateError("accounting", "ledger row is not an object")
    return rows


def money(row: dict[str, Any], key: str) -> Decimal:
    try: value = Decimal(str(row[key]))
    except (KeyError, InvalidOperation, ValueError) as exc: raise GateError("accounting", f"invalid {key}") from exc
    if not value.is_finite() or value < 0: raise GateError("accounting", f"invalid {key}")
    return value


def validate_ledger(rows: list[dict[str, Any]], job_id: str | None, terminal: bool) -> dict[str, Any]:
    reserves: dict[tuple[str, int], Decimal] = {}; settled: set[tuple[str, int]] = set(); jobs: list[str] = []
    exposure = Decimal(0); attempts_by_job: dict[str, int] = {}
    for row in rows:
        event, job, attempt = row.get("event"), row.get("jobId"), row.get("attempt")
        if row.get("schema") != LEDGER_SCHEMA or not isinstance(job, str) or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt <= 0:
            raise GateError("accounting", "ledger schema or attempt identity is invalid")
        pair = (job, attempt)
        if event == "reserve":
            if pair in reserves: raise GateError("accounting", "duplicate reserve")
            amount = money(row, "reservedUsd")
            if amount != Decimal("2.450000") or row.get("pricingAuthority") != PRICING:
                raise GateError("accounting", "tiny reserve or wrong pricing authority")
            if row.get("inputTokenUpperBound") != 200000 or row.get("outputTokenUpperBound") != 40000 or row.get("requestBytes", 0) > 200000:
                raise GateError("accounting", "request or token policy drifted")
            reserves[pair] = amount; exposure += amount
            if job not in jobs: jobs.append(job)
            attempts_by_job[job] = attempts_by_job.get(job, 0) + 1
            if attempts_by_job[job] > 16: raise GateError("accounting", "attempt 17 denied")
        elif event in {"settle", "transport-error", "missing-usage"}:
            if pair not in reserves or pair in settled: raise GateError("accounting", "missing, reordered, duplicate, or replayed reserve/settle")
            if event == "settle":
                released, cost, usage = money(row, "releasedReservedUsd"), money(row, "settledCostUpperBoundUsd"), row.get("usage")
                if released != reserves[pair] or cost > released or not isinstance(usage, dict): raise GateError("accounting", "wrong release or invalid usage")
                if not all(isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool) and usage[k] >= 0 for k in ("inputTokens", "outputTokens")):
                    raise GateError("accounting", "invalid usage")
                if usage["inputTokens"] > 200000 or usage["outputTokens"] > 40000: raise GateError("accounting", "usage exceeds fixed policy")
                settled.add(pair); exposure += cost - released
            # transport-error/missing-usage intentionally retain reservation.
        else: raise GateError("accounting", "unknown or send-before-reserve ledger event")
        if len(jobs) > 4 or exposure > Decimal("156.800000"): raise GateError("accounting", "job count or cumulative budget exceeded")
    if terminal and not rows: raise GateError("accounting", "terminal evidence requires a ledger")
    return {"attemptCount": attempts_by_job.get(job_id, 0) if job_id else 0, "jobCount": len(jobs), "conservativeUsd": f"{exposure:.6f}"}


def artifact_map(evidence: dict[str, Any], run_id: str, route: str, root: Path) -> dict[str, dict[str, Any]]:
    items = evidence.get("artifacts"); mapped: dict[str, dict[str, Any]] = {}
    if not isinstance(items, list): raise GateError("artifact-identity", "artifacts are missing")
    for item in items:
        if not isinstance(item, dict) or item.get("runId") != run_id or item.get("kind") in mapped: raise GateError("artifact-identity", "artifact is duplicate or cross-run")
        kind, rel = item.get("kind"), item.get("path")
        if not isinstance(kind, str) or not isinstance(rel, str) or Path(rel).is_absolute() or ".." in Path(rel).parts: raise GateError("artifact-identity", "artifact path is unsafe")
        path = root / rel
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != item.get("sha256"): raise GateError("artifact-identity", "artifact is absent or digest-mismatched")
        mapped[kind] = item
    required = {"source", "route-artifact", "glb", "events", "stdout", "stderr", "terminal-receipt"}
    if not required <= mapped.keys(): raise GateError("artifact-identity", "required source, route, GLB, event, log, or receipt artifact is missing")
    suffix = Path(mapped["route-artifact"]["path"]).suffix.lower()
    if route == "cad" and suffix not in {".step", ".stp"}: raise GateError("route-policy", "CAD route requires STEP authority")
    if route == "implicit-cad" and suffix not in {".js", ".py", ".json"}: raise GateError("route-policy", "implicit route artifact was substituted")
    return mapped


def workspace(evidence: dict[str, Any], run_id: str, fixture_sha: str) -> dict[str, Any]:
    value = evidence.get("workspace")
    if not isinstance(value, dict) or value.get("schema") != "mesh-to-cad.workspace/1" or value.get("runId") != run_id: raise GateError("workspace", "canonical Workspace identity is invalid")
    nodes = value.get("nodes"); types = ["input", "attempt", "measured-step", "selection", "final-delivery"]
    if not isinstance(nodes, list) or [node.get("type") for node in nodes if isinstance(node, dict)] != types: raise GateError("workspace", "Workspace nodes are missing or reordered")
    parent = None
    for node in nodes:
        payload = node.get("payload")
        if node.get("runId") != run_id or node.get("parentDigest") != parent or not isinstance(payload, dict): raise GateError("workspace", "Workspace ancestry is cross-run or tampered")
        parent = digest(canonical(payload))
        if node.get("digest") != parent: raise GateError("workspace", "Workspace node digest is tampered")
    depths = value.get("depthMeasurements")
    if not isinstance(depths, list) or [row.get("depth") for row in depths if isinstance(row, dict)] != list(range(1, 9)): raise GateError("depth-evidence", "depth 1-8 evidence is incomplete")
    for row in depths:
        if row.get("authority") != "mesh-compare" or row.get("referenceSha256") != fixture_sha or not isinstance(row.get("missing"), int) or not isinstance(row.get("excess"), int): raise GateError("depth-evidence", "measurement is self-authenticated or identity-mismatched")
    accepted, stop = value.get("accepted"), value.get("stopReason"); d8 = depths[-1]
    objective = d8["missing"] == 0 and d8["excess"] == 0 and d8.get("exteriorClear") is True
    if accepted is not objective or (accepted and stop != "acceptance_satisfied") or (not accepted and stop == "acceptance_satisfied"): raise GateError("honest-selection", "Selected Step acceptance or stop reason was relabeled")
    return {"selectedStep": value.get("selectedStep"), "accepted": accepted, "stopReason": stop, "depth8": d8}


def terminal(path: Path, identity: dict[str, Any], expected: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = read_json(path, "terminal-evidence"); run_id = evidence.get("runId")
    if not isinstance(evidence, dict) or evidence.get("schema") != "text-to-cad.toys4k-depth8-evidence/1" or evidence.get("classification") != CLASSIFICATION or not isinstance(run_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", run_id): raise GateError("terminal-evidence", "terminal schema, classification, or run identity is invalid")
    if evidence.get("fixture") != identity: raise GateError("fixture-identity", "terminal fixture identity is cross-run or mismatched")
    route = evidence.get("route")
    if not isinstance(route, dict) or route.get("chosen") != expected["route"] or route.get("evidence") != expected["routeEvidence"] or route.get("consideredAlternative") == expected["route"]: raise GateError("route-policy", "route substitution or missing rubric evidence")
    artifacts = artifact_map(evidence, run_id, expected["route"], path.parent); ws = workspace(evidence, run_id, identity["sha256"])
    final = evidence.get("finalDelivery"); verify = final.get("verification") if isinstance(final, dict) else None
    if not isinstance(final, dict) or final.get("schema") != "mesh-to-cad.final-delivery/1" or final.get("runId") != run_id or final.get("selectedStep") != ws["selectedStep"] or final.get("accepted") is not ws["accepted"] or final.get("sourceSha256") != artifacts["source"]["sha256"] or final.get("glbSha256") != artifacts["glb"]["sha256"] or not isinstance(verify, dict) or verify.get("authority") != "mesh-compare" or verify.get("observableGeometry") is not True: raise GateError("final-delivery", "Final Delivery or Observable Geometry verification is invalid")
    cleanup = evidence.get("cleanup"); resources = {f"t2c-{run_id}-{kind}" for kind in ("agent", "proxy", "internal", "egress", "evidence", "secrets", "process-group")}
    absence = cleanup.get("absence") if isinstance(cleanup, dict) else None
    if not isinstance(cleanup, dict) or cleanup.get("ownerRunId") != run_id or set(cleanup.get("exactResources", [])) != resources or not isinstance(absence, dict) or set(absence) != resources or not all(value is True for value in absence.values()): raise GateError("cleanup-absence", "exact run-owned cleanup absence is unproved")
    if SECRET.search(json.dumps(evidence, sort_keys=True)): raise GateError("credential-safety", "secret or capability material appears in evidence")
    for item in artifacts.values():
        if SECRET.search((path.parent / item["path"]).read_text(encoding="utf-8", errors="ignore")[:1_000_000]): raise GateError("credential-safety", "secret or capability material appears in an artifact")
    accounting = validate_ledger(rows, run_id, terminal=True)
    if accounting["attemptCount"] < 1: raise GateError("accounting", "terminal ledger is not bound to the run identity")
    terminal_value = evidence.get("terminal")
    if not isinstance(terminal_value, dict) or terminal_value.get("status") not in {"development-succeeded", "development-unaccepted"}: raise GateError("terminal-evidence", "run did not reach a legal Development terminal state")
    return {"runId": run_id, **ws, "accounting": accounting}


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("fixture_key")
    parser.add_argument("--fixture-root", type=Path); parser.add_argument("--provider-free-conformance-contract", type=Path)
    parser.add_argument("--prior-total-ledger", type=Path); parser.add_argument("--sequence-state", type=Path); parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    receipt: dict[str, Any] = {"schema": "text-to-cad.toys4k-depth8-development-receipt/1", "classification": CLASSIFICATION, "status": "development-failed", "attemptCount": 0, "paidDispatchCount": 0}
    try:
        if args.fixture_key not in FIXTURES: raise GateError("fixture-allowlist", "fixture key is not allowlisted")
        expected = contract(args, args.fixture_key); root = args.fixture_root or Path(__file__).resolve().parents[2] / "models/toys4k"
        identity = input_identity(root, args.fixture_key, expected); sequence(args.sequence_state, args.fixture_key)
        rows = ledger_rows(args.prior_total_ledger); accounting = validate_ledger(rows, None, terminal=False)
        receipt.update({"fixture": identity, "route": expected["route"], "routeEvidence": expected["routeEvidence"], "policy": {"maxAttempts": 16, "maxRequestBytes": 200000, "maxOutputTokens": 40000, "maxJobUsd": "39.200000", "maxJobs": 4, "maxTotalUsd": "156.800000", "timeoutSeconds": 2700, "wholeJobRetry": False}, "accounting": accounting})
        if args.evidence is None:
            if accounting["jobCount"] != ORDER.index(args.fixture_key) or Decimal(accounting["conservativeUsd"]) + Decimal("2.450000") > Decimal("156.800000"):
                raise GateError("accounting", "next serialized job cannot reserve its worst case")
            receipt["status"] = "development-prepared"
        else:
            result = terminal(args.evidence, identity, expected, rows); receipt.update(result); receipt["attemptCount"] = result["accounting"]["attemptCount"]
            if result["accounting"]["jobCount"] != ORDER.index(args.fixture_key) + 1: raise GateError("accounting", "terminal ledger job prefix is not serialized")
            receipt["paidDispatchCount"] = 0 if args.provider_free_conformance_contract else 1; receipt["status"] = "development-evidence-complete"
            receipt["nextSequenceState"] = {"schema": "text-to-cad.toys4k-depth8-sequence/1", "completed": list(ORDER[:ORDER.index(args.fixture_key) + 1]), "blockingFailures": []}
    except GateError as exc:
        receipt.update({"failureCheck": exc.check, "detail": exc.detail}); emit(receipt); return 2
    emit(receipt); return 0


if __name__ == "__main__": raise SystemExit(main())
