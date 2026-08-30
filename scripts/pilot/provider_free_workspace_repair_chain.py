"""Provider-free, model-free Workspace repair-chain integration gate.

This scenario drives the production W1/W3/W4 assembly with a deterministic
triangle fixture.  It deliberately uses the same candidate build and evidence
providers as the pilot runner; no fake renderer or direct Workspace calls are
used for agent intents.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import sys
import tempfile
from typing import Any, Mapping, Sequence

from scripts.pilot import plugin_deployment, provider_free_installed_plugin as installed, runner
from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge
from scripts.pilot.cvm_job import protocol
from scripts.pilot.workspace_supervisor import SupervisorError, WorkspaceSupervisor

SCENARIO = "workspace-repair-chain"
EVIDENCE_SCHEMA_V1 = "text-to-cad.provider-free-workspace-repair-chain-evidence/1"
EVIDENCE_SCHEMA_V2 = "text-to-cad.provider-free-workspace-repair-chain-evidence/2"
EVIDENCE_SCHEMA_V3 = "text-to-cad.provider-free-workspace-repair-chain-evidence/3"
MANIFEST_SCHEMA = "text-to-cad.provider-free-artifact-manifest/1"
MAX_EVIDENCE_BYTES = 96 * 1024
MAX_MANIFEST_BYTES = 8 * 1024
STEP_ZERO_WIDTH = 2 / 3
REPAIR_A_WIDTH = 9 / 10
REPAIR_B_WIDTH = 1 / 8
SPEC_INITIAL_BYTES = b'{"revision":"initial"}\n'
SPEC_FINAL_BYTES = b'{"revision":"updated","semantic_regions":[]}\n'
MCP_PERMITTED_INTENTS = frozenset({
    "workspace_status", "start_attempt", "run_candidate_tool", "submit_step_zero",
    "submit_repair", "inspect_formal_preview", "select_and_finalize", "observe_reference",
})


class ProviderFreeError(RuntimeError):
    """The deterministic repair-chain contract was not satisfied."""


def _frontier(decision_facts: Mapping[str, Any]) -> dict[str, int]:
    summary = decision_facts.get("residual_summary")
    frontier = summary.get("repair_frontier") if isinstance(summary, dict) else None
    targets = decision_facts.get("repair_targets")
    items = targets.get("items") if isinstance(targets, dict) else None
    if not isinstance(frontier, dict) or set(frontier) != {"active_depth", "missing_surface_count", "excess_surface_count", "surface_error_count", "surface_error_rate"} or not isinstance(items, list):
        raise ProviderFreeError("invalid decision frontier")
    if not isinstance(frontier["active_depth"], int) or frontier["active_depth"] <= 0:
        raise ProviderFreeError("invalid active depth")
    if any(type(frontier[key]) is not int or frontier[key] < 0 for key in ("missing_surface_count", "excess_surface_count", "surface_error_count")) or not items:
        raise ProviderFreeError("invalid frontier counts")
    return {"active_depth": frontier["active_depth"], "missing_surface_error_count": frontier["missing_surface_count"], "excess_surface_error_count": frontier["excess_surface_count"], "surface_error_count": frontier["surface_error_count"], "target_count": len(items)}


def _frontier_order(value: Mapping[str, int]) -> tuple[int, int]:
    return (value["active_depth"], -value["surface_error_count"])


def authority_identity(receipt: plugin_deployment.DeploymentReceipt) -> dict[str, str]:
    return installed.authority_identity(receipt)


def assert_current_authority(
    record: Mapping[str, Any], host_home: Path
) -> plugin_deployment.DeploymentReceipt:
    return installed.assert_current_authority(
        {**record, "scenario": installed.SCENARIO, "object": installed.SCENARIO},
        host_home,
    )


def build_runner_env(environ: Mapping[str, str]) -> dict[str, str]:
    return installed.build_runner_env(environ)


def expected_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    job = record.get("job")
    if not isinstance(job, str) or "/" not in job or record.get("exp_dir") != f"outputs/{job}":
        raise ProviderFreeError("invalid repair-chain job binding")
    base = installed.expected_identity({**record, "scenario": installed.SCENARIO, "object": installed.SCENARIO})
    if record.get("provider_free") is not True or record.get("scenario") != SCENARIO or record.get("object") != SCENARIO or record.get("token_slot") is not None:
        raise ProviderFreeError("invalid repair-chain identity")
    return {**base, "scenario": SCENARIO}


def artifact_paths(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    exp = repo_root / str(record["exp_dir"])
    return exp, exp / "provider-free-evidence.json", exp / "artifact_manifest.json"


def _json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _fixture(path: Path) -> None:
    import trimesh
    mesh = trimesh.creation.box(extents=(12.0, 6.0, 3.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path, file_type="ply")


def _source(path: Path, width: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"from build123d import Box\n\ndef gen_step():\n    return Box({width}, 0.5, 0.25)\n", encoding="utf-8")


def _mcp_call(socket_path: Path, handle: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(os.fspath(socket_path))
        stream = conn.makefile("rwb")
        def send(value: Mapping[str, Any]) -> dict[str, Any]:
            stream.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
            stream.flush()
            while True:
                line = stream.readline()
                if not line:
                    raise ProviderFreeError("MCP closed")
                value = json.loads(line)
                if value.get("__notification__"):
                    continue
                return value
        initialized = send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "repair-chain", "version": "1"}}})
        stream.write(b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n'); stream.flush()
        listed = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        called = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "inspect_formal_preview", "arguments": {"preview_handle": handle}}})
        return initialized, listed, called


def _inspect(socket_path: Path, handle: str, expected: bytes) -> dict[str, Any]:
    initialized, listed, result = _mcp_call(socket_path, handle)
    if set(initialized) != {"jsonrpc", "id", "result"} or initialized.get("jsonrpc") != "2.0" or initialized.get("id") != 1:
        raise ProviderFreeError("MCP initialize failed")
    init_result = initialized.get("result")
    if not isinstance(init_result, dict) or set(init_result) != {"protocolVersion", "capabilities", "serverInfo"} or init_result.get("protocolVersion") != "2025-06-18":
        raise ProviderFreeError("MCP initialize result is invalid")
    if set(listed) != {"jsonrpc", "id", "result"} or listed.get("jsonrpc") != "2.0" or listed.get("id") != 2:
        raise ProviderFreeError("MCP tools/list response is invalid")
    tools = listed.get("result", {}).get("tools", [])
    if len(tools) != 1 or tools[0].get("name") != "inspect_formal_preview" or tools[0].get("annotations") != {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}:
        raise ProviderFreeError("MCP public inspector contract failed")
    if set(result) != {"jsonrpc", "id", "result"} or result.get("jsonrpc") != "2.0" or result.get("id") != 3:
        raise ProviderFreeError("MCP tools/call response is invalid")
    call_result = result.get("result")
    if not isinstance(call_result, dict) or set(call_result) != {"content", "isError", "structuredContent"} or call_result.get("isError") is not False:
        raise ProviderFreeError("MCP tools/call result is invalid")
    content = call_result["content"]
    if not isinstance(content, list) or len(content) != 2:
        raise ProviderFreeError("MCP preview content is invalid")
    text, image = content
    if not isinstance(text, dict) or set(text) != {"type", "text"} or text.get("type") != "text" or not isinstance(text.get("text"), str):
        raise ProviderFreeError("MCP preview text block is invalid")
    if not isinstance(image, dict) or set(image) != {"type", "data", "mimeType"} or image.get("type") != "image" or image.get("mimeType") != "image/png" or not isinstance(image.get("data"), str):
        raise ProviderFreeError("MCP preview image block is invalid")
    try:
        image_bytes = base64.b64decode(image["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProviderFreeError("MCP preview image encoding is invalid") from exc
    if image_bytes != expected:
        raise ProviderFreeError("MCP preview image mismatch")
    structured = call_result["structuredContent"]
    try:
        parsed_text = json.loads(text["text"])
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("MCP preview text is invalid") from exc
    if parsed_text != structured:
        raise ProviderFreeError("MCP preview text does not match structured content")
    if not isinstance(structured, dict) or set(structured) != {"schema", "intent", "result"} or structured.get("schema") != "mesh-to-cad.agent-response/1" or structured.get("intent") != "inspect_formal_preview" or not isinstance(structured.get("result"), dict) or set(structured["result"]) != {"state", "preview_handle", "permitted_next_intents"} or structured["result"].get("state") != "available" or structured["result"].get("preview_handle") != handle or not isinstance(structured["result"].get("permitted_next_intents"), list) or any(type(item) is not str or item not in MCP_PERMITTED_INTENTS for item in structured["result"]["permitted_next_intents"]):
        raise ProviderFreeError("MCP preview envelope is invalid")
    return {"initialize_id": initialized.get("id"), "tools_list_id": listed.get("id"), "call_id": result.get("id"), "tools": 1, "content_types": [item.get("type") for item in content], "image_bytes": len(expected), "text_present": True, "handle_bound": True}


def _public(value: Any) -> str:
    text = json.dumps(value, sort_keys=True)
    forbidden = ("target_key", "mask_sha256", "depth8", "/Users/", "/home/", "Traceback", "Exception")
    if any(item in text for item in forbidden):
        raise ProviderFreeError("public response leaked forbidden detail")
    return text


def _validate_v1_artifacts(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path]:
    _, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    try:
        if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES or artifact_manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProviderFreeError("artifact too large")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("evidence missing or invalid") from exc
    identity = expected_identity(record)
    required = {"schema", "identity", "scenario", "gate_passed", "sequence", "active_depth", "selected_public_target", "step_zero", "repair", "ordinals", "attempt", "region_diff", "cycle", "previews", "original_plan_digest_binding", "mcp", "browser", "workspace_validation", "module_paths"}
    if not isinstance(evidence, dict) or set(evidence) != required or evidence.get("schema") != EVIDENCE_SCHEMA_V1 or evidence.get("identity") != identity or evidence.get("scenario") != SCENARIO or evidence.get("gate_passed") is not True:
        raise ProviderFreeError("invalid evidence shape")
    if evidence["sequence"] != ["plan", "start_attempt", "run_candidate", "submit_step_zero", "mcp_inspect_step_zero", "start_repair", "run_candidate", "submit_repair", "mcp_inspect_repair"]:
        raise ProviderFreeError("invalid sequence")
    if not isinstance(evidence["active_depth"], int) or evidence["active_depth"] <= 0 or not isinstance(evidence["selected_public_target"], dict):
        raise ProviderFreeError("invalid active depth evidence")
    for key in ("step_zero", "repair"):
        if not isinstance(evidence[key], dict) or not evidence[key].get("handle"):
            raise ProviderFreeError("missing publication handle")
    exp_root = (repo_root / str(record["exp_dir"])).resolve()
    ordinals = evidence["ordinals"]
    if not isinstance(ordinals, dict) or set(ordinals) != {"step_zero", "repair", "repair_parent"} or any(type(ordinals[key]) is not int or ordinals[key] < 0 for key in ordinals) or ordinals["repair_parent"] != ordinals["step_zero"]:
        raise ProviderFreeError("invalid publication ordinals")
    expected_paths = {
        "attempt": f"cycles/{ordinals['repair']:06d}/attempt.json",
        "region_diff": f"cycles/{ordinals['repair']:06d}/diff.json",
        "cycle": f"cycles/{ordinals['repair']:06d}/cycle.json",
    }
    for key in ("attempt", "region_diff", "cycle"):
        item = evidence[key]
        target = (exp_root / str(item.get("path"))).resolve() if isinstance(item, dict) and isinstance(item.get("path"), str) else None
        if not isinstance(item, dict) or item.get("path") != expected_paths[key] or not isinstance(item.get("bytes"), int) or item["bytes"] <= 0 or target is None:
            raise ProviderFreeError("missing authority evidence")
        try: target.relative_to(exp_root)
        except ValueError: raise ProviderFreeError("authority path escaped exp")
        if not target.is_file() or target.stat().st_size != item["bytes"]:
            raise ProviderFreeError("missing authority evidence")
    repair_attempt_path = (exp_root / evidence["attempt"]["path"]).resolve()
    diff_path = (exp_root / evidence["region_diff"]["path"]).resolve()
    try:
        attempt = json.loads(repair_attempt_path.read_text(encoding="utf-8"))
        region_diff = json.loads(diff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("authority evidence is not json") from exc
    batch = region_diff.get("repair_batch") if isinstance(region_diff, dict) else None
    required_batch = {"schema", "from_step", "selected_targets", "planned_edits", "plan_sha256"}
    if not isinstance(batch, dict) or set(batch) != required_batch or batch.get("schema") != "voxblame.repair-batch/1" or not isinstance(batch.get("from_step"), int) or not isinstance(batch.get("selected_targets"), list) or not isinstance(batch.get("planned_edits"), list) or not isinstance(batch.get("plan_sha256"), str):
        raise ProviderFreeError("invalid repair batch schema")
    attempt_digest = attempt.get("plan_digest") if isinstance(attempt, dict) else None
    if not isinstance(attempt_digest, str) or attempt.get("intended_step") != 1 or attempt.get("from_step") != 0 or batch["from_step"] != 0 or batch["plan_sha256"] != attempt_digest:
        raise ProviderFreeError("repair batch digest mismatch")
    step_document = json.loads((exp_root / f"steps/{ordinals['step_zero']:06d}/step.json").read_text(encoding="utf-8"))
    cycle_document = json.loads((exp_root / expected_paths["cycle"]).read_text(encoding="utf-8"))
    if not isinstance(step_document, dict) or step_document.get("step") != ordinals["step_zero"] or step_document.get("parent_step") is not None or step_document.get("cycle") is not None or step_document.get("compare_to") is not None:
        raise ProviderFreeError("Step 0 manifest contract mismatch")
    if not isinstance(cycle_document, dict) or cycle_document.get("cycle") != ordinals["repair"] or cycle_document.get("from_step") != ordinals["repair_parent"] or cycle_document.get("to_step") != ordinals["repair"]:
        raise ProviderFreeError("repair cycle manifest identity mismatch")
    if cycle_document.get("from_step") != ordinals["step_zero"]:
        raise ProviderFreeError("repair parent manifest mismatch")
    previews = evidence["previews"]
    if not isinstance(previews, dict) or previews.get("distinct") is not True or not all(isinstance(previews.get(key), dict) and isinstance(previews[key].get("path"), str) and isinstance(previews[key].get("bytes"), int) and previews[key]["bytes"] > 0 for key in ("step_zero", "repair")):
        raise ProviderFreeError("invalid preview evidence")
    expected_previews = {
        "step_zero": f"steps/{ordinals['step_zero']:06d}/preview/preview.png",
        "repair": f"steps/{ordinals['repair']:06d}/preview/preview.png",
    }
    for key, expected_path in expected_previews.items():
        item = previews[key]
        if item["path"] != expected_path:
            raise ProviderFreeError("invalid preview authority path")
        preview_path = (exp_root / item["path"]).resolve()
        try:
            preview_path.relative_to(exp_root)
        except ValueError as exc:
            raise ProviderFreeError("preview path escaped exp") from exc
        if not preview_path.is_file() or preview_path.stat().st_size != item["bytes"]:
            raise ProviderFreeError("preview authority missing or changed")
    binding = evidence["original_plan_digest_binding"]
    if not isinstance(binding, dict) or binding.get("attempts", 0) < 2 or not isinstance(binding.get("repair_attempt_path"), str) or not isinstance(binding.get("attempt_plan_digest"), str) or not isinstance(binding.get("region_diff_plan_sha256"), str) or binding.get("derived_equal") is not True or binding["attempt_plan_digest"] != binding["region_diff_plan_sha256"] or not isinstance(binding.get("plan_files"), list):
        raise ProviderFreeError("invalid plan binding evidence")
    for relative in [binding["repair_attempt_path"], *binding["plan_files"]]:
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts or not (exp_root / relative).resolve().is_file():
            raise ProviderFreeError("invalid plan authority path")
    browser = evidence["browser"]
    if not isinstance(browser, dict) or browser.get("capability") is not True or browser.get("preflight") is not True or evidence["workspace_validation"] is not True:
        raise ProviderFreeError("invalid runtime evidence")
    modules = evidence["module_paths"]
    if not isinstance(modules, dict) or set(modules) != {"product_root", "workspace", "core", "handler", "mcp", "rebuild", "geometry"} or any(not isinstance(modules[key], str) or modules[key].startswith("/") for key in modules) or any(not modules[key].startswith("skills/") for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry")):
        raise ProviderFreeError("invalid published module paths")
    if modules["product_root"] != "skills":
        raise ProviderFreeError("invalid published module root")
    for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry"):
        relative = PurePosixPath(modules[key])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0:1] != ("skills",):
            raise ProviderFreeError("invalid published module path")
    for key in ("step_zero", "repair"):
        mcp = evidence["mcp"].get(key)
        if not isinstance(mcp, dict) or mcp.get("image_bytes") != previews[key]["bytes"]:
            raise ProviderFreeError("MCP preview size mismatch")
    _public(evidence["selected_public_target"])
    if not isinstance(manifest, dict) or manifest != {"schema": MANIFEST_SCHEMA, "final_status": 0, "identity": identity, "evidence": {"path": evidence_path.name}}:
        raise ProviderFreeError("invalid manifest")
    return evidence_path, artifact_manifest_path


def _validate_v2_artifacts(
    repo_root: Path,
    record: Mapping[str, Any],
    *,
    schema: str = EVIDENCE_SCHEMA_V2,
) -> tuple[Path, Path]:
    exp_dir, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("v2 evidence missing or invalid") from exc
    required = {"schema", "identity", "scenario", "gate_passed", "selection", "steps", "graph", "previews", "mcp", "workspace_validation", "module_paths", "runtime", "final", "cycles"}
    if schema == EVIDENCE_SCHEMA_V3:
        required.add("spec_persistence")
    if not isinstance(evidence, dict) or set(evidence) != required or evidence.get("schema") != schema or evidence.get("identity") != expected_identity(record) or evidence.get("scenario") != SCENARIO or evidence.get("gate_passed") is not True:
        raise ProviderFreeError("invalid v2 evidence shape")
    selection = evidence["selection"]
    if not isinstance(selection, dict) or set(selection) != {"considered", "selected", "selected_step", "repair_b_is_head"} or selection.get("selected") not in {"step_zero", "repair_a"} or selection.get("considered") != ["step_zero", "repair_a", "repair_b"]:
        raise ProviderFreeError("invalid historical selection evidence")
    steps = evidence["steps"]
    if not isinstance(steps, dict) or set(steps) != {"step_zero", "repair_a", "repair_b"}:
        raise ProviderFreeError("invalid v2 step evidence")
    cycles = evidence["cycles"]
    if not isinstance(cycles, dict) or set(cycles) != {"repair_a", "repair_b"}:
        raise ProviderFreeError("invalid v2 cycle evidence")
    for name in ("repair_a", "repair_b"):
        cycle = cycles[name]
        if not isinstance(cycle, dict) or set(cycle) != {"ordinal", "from_step", "to_step", "selected_parent_target", "artifacts"}:
            raise ProviderFreeError("invalid cycle record")
        if cycle["ordinal"] != steps[name]["ordinal"] or cycle["from_step"] != steps[name]["parent"] or cycle["to_step"] != steps[name]["ordinal"]:
            raise ProviderFreeError("cycle edge mismatch")
        target = cycle["selected_parent_target"]
        if not isinstance(target, dict) or set(target) != {"rank", "kind", "bounds_canonical"}:
            raise ProviderFreeError("invalid parent target record")
        artifacts = cycle["artifacts"]
        expected = {"plan": f"cycles/{cycle['to_step']:06d}/plan.json", "assessment": f"cycles/{cycle['to_step']:06d}/assessment.json", "diff": f"cycles/{cycle['to_step']:06d}/diff.json", "cycle": f"cycles/{cycle['to_step']:06d}/cycle.json", "attempt": f"cycles/{cycle['to_step']:06d}/attempt.json"}
        if not isinstance(artifacts, dict) or set(artifacts) != set(expected) or any(artifacts[key] != expected[key] for key in expected):
            raise ProviderFreeError("invalid cycle artifact paths")
        for relative in artifacts.values():
            target_path = (exp_dir / relative).resolve()
            try: target_path.relative_to(exp_dir.resolve())
            except ValueError as exc: raise ProviderFreeError("cycle path escaped exp") from exc
            if not target_path.is_file(): raise ProviderFreeError("cycle artifact missing")
        docs = {key: json.loads((exp_dir / relative).read_text(encoding="utf-8")) for key, relative in artifacts.items()}
        plan = docs["plan"]
        assessment = docs["assessment"]
        cycle_doc = docs["cycle"]
        attempt = docs["attempt"]
        diff = docs["diff"]
        if plan.get("from_step") != cycle["from_step"] or cycle["selected_parent_target"] not in plan.get("selected_targets", []) or not any(edit.get("target_ranks") == [cycle["selected_parent_target"]["rank"]] for edit in plan.get("planned_edits", [])):
            raise ProviderFreeError("cycle plan target binding mismatch")
        if assessment.get("from_step") != cycle["from_step"] or assessment.get("to_step") != cycle["to_step"]:
            raise ProviderFreeError("cycle assessment edge mismatch")
        if cycle_doc.get("cycle") != cycle["to_step"] or cycle_doc.get("from_step") != cycle["from_step"] or cycle_doc.get("to_step") != cycle["to_step"]:
            raise ProviderFreeError("cycle manifest edge mismatch")
        if attempt.get("intended_step") != cycle["to_step"] or attempt.get("from_step") != cycle["from_step"] or attempt.get("intended_cycle") != cycle["to_step"]:
            raise ProviderFreeError("cycle attempt edge mismatch")
        batch = diff.get("repair_batch") if isinstance(diff, dict) else None
        if diff.get("from_step") != cycle["from_step"] or not isinstance(batch, dict) or batch.get("plan_sha256") != attempt.get("plan_digest") or cycle_doc.get("plan_digest") != attempt.get("plan_digest"):
            raise ProviderFreeError("cycle digest binding mismatch")
    for name, item in steps.items():
        frontier = item.get("frontier")
        if not isinstance(item, dict) or set(item) != {"step_handle", "ordinal", "parent", "cycle", "accepted", "frontier", "target_count", "manifest"} or not isinstance(item.get("ordinal"), int) or item.get("manifest") != f"steps/{item['ordinal']:06d}/step.json" or item.get("accepted") is not False or type(item.get("target_count")) is not int or item["target_count"] <= 0 or not isinstance(frontier, dict) or set(frontier) != {"active_depth", "missing_surface_error_count", "excess_surface_error_count", "surface_error_count", "target_count"} or frontier.get("target_count") != item["target_count"]:
            raise ProviderFreeError("invalid v2 step manifest record")
        step_manifest_path = (exp_dir / item["manifest"]).resolve()
        if not step_manifest_path.is_file():
            raise ProviderFreeError("step manifest missing")
        document = json.loads(step_manifest_path.read_text(encoding="utf-8"))
        if document.get("step") != item["ordinal"] or document.get("parent_step") != item.get("parent"):
            raise ProviderFreeError("step manifest identity mismatch")
    if steps["repair_a"]["parent"] != steps["step_zero"]["ordinal"] or steps["repair_b"]["parent"] != steps["repair_a"]["ordinal"]:
        raise ProviderFreeError("repair parent chain mismatch")
    expected_best = "repair_a" if _frontier_order(steps["repair_a"]["frontier"]) > _frontier_order(steps["step_zero"]["frontier"]) else "step_zero"
    if selection.get("selected") != expected_best or selection.get("selected_step") != steps[expected_best]["ordinal"] or selection.get("selected_step") == steps["repair_b"]["ordinal"]:
        raise ProviderFreeError("selected historical ordinal mismatch")
    graph = evidence["graph"]
    if not isinstance(graph, dict) or set(graph) != {"source", "heads"} or graph.get("source") != "step_parentage" or graph.get("heads") != [steps["repair_b"]["ordinal"]]:
        raise ProviderFreeError("graph head identity mismatch")
    if not _frontier_order(steps[expected_best]["frontier"]) > _frontier_order(steps["repair_b"]["frontier"]):
        raise ProviderFreeError("frontier ordering mismatch")
    previews = evidence["previews"]
    if not isinstance(previews, dict) or set(previews) != {"step_zero", "repair_a", "repair_b", "selected_reinspect"}:
        raise ProviderFreeError("invalid v2 preview evidence")
    for key, item in previews.items():
        if not isinstance(item, dict) or set(item) != {"path", "bytes"} or not isinstance(item.get("path"), str) or not isinstance(item.get("bytes"), int) or item["bytes"] <= 0:
            raise ProviderFreeError("invalid v2 preview record")
        target = (exp_dir / item["path"]).resolve()
        try:
            target.relative_to(exp_dir.resolve())
        except ValueError as exc:
            raise ProviderFreeError("v2 preview path escaped exp") from exc
        if not target.is_file() or target.stat().st_size != item["bytes"]:
            raise ProviderFreeError("v2 preview missing or changed")
    expected_preview_key = selection["selected"]
    if previews["selected_reinspect"] != previews[expected_preview_key]:
        raise ProviderFreeError("selected reinspect preview is not best step")
    if evidence.get("workspace_validation") is not True or not isinstance(evidence.get("mcp"), dict):
        raise ProviderFreeError("invalid v2 runtime evidence")
    modules = evidence["module_paths"]
    if not isinstance(modules, dict) or set(modules) != {"product_root", "workspace", "core", "handler", "mcp", "rebuild", "geometry"} or modules.get("product_root") != "skills" or any(not isinstance(modules[key], str) or not modules[key].startswith("skills/") for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry")):
        raise ProviderFreeError("invalid v2 module provenance")
    runtime = evidence["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {"interpreter", "registry"} or runtime.get("interpreter") != ".venv/bin/python":
        raise ProviderFreeError("invalid v2 runtime identity")
    registry = runtime["registry"]
    if not isinstance(registry, dict) or set(registry) != {"schema", "rebuild_id", "geometry_id", "authority", "provenance"} or registry != {"schema": "mesh-to-cad.tool-registry/2", "rebuild_id": "cad.canonical-build/1", "geometry_id": "mesh-compare.voxblame/1", "authority": "installed_publish_tree", "provenance": "receipt.publish_tree"}:
        raise ProviderFreeError("invalid v2 registry provenance")
    mcp = evidence["mcp"]
    for key in ("step_zero", "repair_a", "repair_b", "selected_reinspect"):
        if not isinstance(mcp.get(key), dict) or set(mcp[key]) != {"initialize_id", "tools_list_id", "call_id", "tools", "content_types", "image_bytes", "text_present", "handle_bound"} or mcp[key].get("image_bytes") != previews[key]["bytes"]:
            raise ProviderFreeError("v2 MCP preview mismatch")
    final = evidence.get("final")
    expected_final = {"manifest": "final/manifest.json", "source": "final/source/source/model.py", "measurement": "final/measurement.json", "preview": "final/preview.json", "verification": "final/verification.json"}
    if not isinstance(final, dict) or set(final) != {"manifest", "selected_step", "source", "measurement", "preview", "verification", "identity_bound"} or final.get("selected_step") != selection.get("selected_step") or any(final.get(key) != value for key, value in expected_final.items()):
        raise ProviderFreeError("final selection identity mismatch")
    for relative in ("final/manifest.json", "final/source/source/model.py", "final/measurement.json", "final/preview.json", "final/verification.json"):
        target = (exp_dir / relative).resolve()
        try:
            target.relative_to(exp_dir.resolve())
        except ValueError as exc:
            raise ProviderFreeError("final artifact path escaped exp") from exc
        if not target.is_file():
            raise ProviderFreeError("final artifact missing")
    final_manifest = json.loads((exp_dir / "final/manifest.json").read_text(encoding="utf-8"))
    final_preview = json.loads((exp_dir / "final/preview.json").read_text(encoding="utf-8"))
    verification = json.loads((exp_dir / "final/verification.json").read_text(encoding="utf-8"))
    if final_manifest.get("selected_step") != selection.get("selected_step") or final_manifest.get("stop_reason") != "no_feasible_strategy" or final_preview.get("selected_step") != selection.get("selected_step"):
        raise ProviderFreeError("final manifest or preview is not bound to selected step")
    equality = verification.get("equality")
    if verification.get("against_step") != selection.get("selected_step") or verification.get("verified") is not True or not isinstance(equality, dict) or set(equality) != {"interior", "exterior", "observable", "errors_by_depth"} or any(value is not True for value in equality.values()):
        raise ProviderFreeError("final verification is not bound to selected step")
    selected_source = exp_dir / f"steps/{selection['selected_step']:06d}/candidate/source/model.py"
    if not selected_source.is_file() or (exp_dir / "final/source/source/model.py").read_bytes() != selected_source.read_bytes():
        raise ProviderFreeError("final source is not selected-step source")
    if (exp_dir / "final/measurement.json").read_bytes() != (exp_dir / f"steps/{selection['selected_step']:06d}/measurement.json").read_bytes():
        raise ProviderFreeError("final measurement is not selected-step measurement")
    expected_manifest = {"schema": f"text-to-cad.provider-free-artifact-manifest/{3 if schema == EVIDENCE_SCHEMA_V3 else 2}", "final_status": 0, "identity": expected_identity(record), "evidence": {"path": evidence_path.name}}
    if not isinstance(manifest, dict) or manifest != expected_manifest:
        raise ProviderFreeError("invalid v2 manifest")
    if final.get("identity_bound") is not True:
        raise ProviderFreeError("final identity binding was not derived")
    if final_manifest.get("selected_step") != selection["selected_step"] or final_manifest.get("selected_step") == steps["repair_b"]["ordinal"]:
        raise ProviderFreeError("final manifest selected wrong step")
    if schema == EVIDENCE_SCHEMA_V3:
        spec = evidence["spec_persistence"]
        expected_spec = {
            "seam": "runner.persist_agent_reconstruction_spec",
            "path": runner.RECONSTRUCTION_SPEC_RELATIVE.as_posix(),
            "enabled_status": 0,
            "updated_bytes": len(SPEC_FINAL_BYTES),
            "disabled_absent": True,
            "workspace_authority_absent": True,
            "missing_status": 1,
            "prior_failure_status": 17,
        }
        if spec != expected_spec:
            raise ProviderFreeError("invalid Reconstruction Spec persistence evidence")
        if (exp_dir / runner.RECONSTRUCTION_SPEC_RELATIVE).read_bytes() != SPEC_FINAL_BYTES:
            raise ProviderFreeError("persisted Reconstruction Spec bytes changed")
    return evidence_path, artifact_manifest_path


def validate_artifacts(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path]:
    _, evidence_path, _ = artifact_paths(repo_root, record)
    try:
        schema = json.loads(evidence_path.read_text(encoding="utf-8")).get("schema")
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("evidence missing or invalid") from exc
    if schema == EVIDENCE_SCHEMA_V1:
        return _validate_v1_artifacts(repo_root, record)
    if schema == EVIDENCE_SCHEMA_V2:
        return _validate_v2_artifacts(repo_root, record)
    if schema == EVIDENCE_SCHEMA_V3:
        return _validate_v2_artifacts(repo_root, record, schema=EVIDENCE_SCHEMA_V3)
    raise ProviderFreeError("unknown evidence schema")


def run_job(record: Mapping[str, Any], *, repo_root: Path, host_home: Path, environ: Mapping[str, str]) -> int:
    identity = expected_identity(record)
    try:
        receipt = installed.assert_current_authority(
            {**record, "scenario": installed.SCENARIO, "object": installed.SCENARIO}, host_home
        )
    except installed.ProviderFreeError as exc:
        raise ProviderFreeError("plugin authority changed") from exc
    exp_dir, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    exp_dir.mkdir(parents=True, exist_ok=False)
    (repo_root / "tmp").mkdir(exist_ok=True)
    fixture_root = Path(tempfile.mkdtemp(prefix="workspace-repair-chain-", dir=repo_root / "tmp"))
    fixture = fixture_root / "repair-chain.ply"
    cleanup_errors: list[str] = []
    sidecar = candidate_lease = supervisor = bridge = None
    socket_dir: Path | None = None
    try:
        runner.prepare_exp(exp_dir)
        _fixture(fixture)
        trusted = receipt.publish_tree
        published_rebuild = trusted / "skills/cad/scripts/canonical-build/__main__.py"
        published_geometry = trusted / "skills/mesh-compare/scripts/mesh-compare/__main__.py"
        if not published_rebuild.is_file() or not published_geometry.is_file():
            raise ProviderFreeError("published production entrypoints unavailable")
        runner.prepare_and_initialize_workspace(exp_dir, fixture, trusted_tools_root=trusted)
        sidecar = runner.BrowserRuntimeJob.create(exp_dir, image_lock_path=runner.HOST_IMAGE_LOCK_PATH, viewer_runtime_dir=runner.VIEWER_RUNTIME_DIR)
        candidate_lease = runner.materialize_candidate_runtime(repo_root / ".venv", repo_root / ".cache" / "mesh-to-cad-agent-runtime", repo_root=repo_root)
        candidate_root = exp_dir.parent / f".agent-candidate-repair-chain-{os.getpid()}"
        registry = runner.publish_tool_registry(sidecar.capability_dir, rebuild_entrypoint=published_rebuild, geometry_entrypoint=published_geometry)
        registry_document = json.loads(registry.read_text(encoding="utf-8"))
        supervisor = WorkspaceSupervisor(exp_dir, bind_reference=True, candidate_root=candidate_root, rebuild_entrypoint=published_rebuild, geometry_entrypoint=published_geometry, tool_registry=registry, browser_runtime_capability=sidecar.capability_dir / "runtime.json", candidate_runtime=candidate_lease.runtime, trusted_tools_root=trusted, trusted_product_root=trusted, step_zero_evidence_provider=lambda req: runner.real_step_zero_evidence_provider(req, capability_path=sidecar.capability_dir / "runtime.json", meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src", meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src"), repair_evidence_provider=lambda req: runner.real_repair_evidence_provider(req, capability_path=sidecar.capability_dir / "runtime.json", meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src", meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src"))
        socket_dir = Path(tempfile.mkdtemp(prefix="ttc-a-", dir="/tmp"))
        bridge = AgentSurfaceBridge(supervisor.agent_surface(), socket_dir / "surface.sock", trusted_product_root=trusted)
        bridge.start(); sidecar.start(); sidecar.preflight(); sidecar.preflight_mcp()
        bootstrap = supervisor.agent_bootstrap_contract(); surface = supervisor.agent_surface(); wh = bootstrap["workspace_handle"]; ph = bootstrap["plan_handle"]
        plan = candidate_root / "plan.json"; _json(plan, {"schema": "mesh-to-cad.initial-plan/1", "summary": "deterministic box reconstruction"})
        a0_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph}}); _public(a0_response); a0 = a0_response["result"]
        work = candidate_root / "work"; _source(work / "source/model.py", STEP_ZERO_WIDTH)
        run0 = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "run_candidate_tool", "args": {"workspace_handle": wh, "attempt_handle": a0["attempt_handle"], "candidate_handle": a0["candidate_handle"], "operation_handle": a0["capability_bundle_handle"]}}); _public(run0)
        s0_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_step_zero", "args": {"workspace_handle": wh, "attempt_handle": a0["attempt_handle"], "candidate_handle": a0["candidate_handle"]}}); _public(s0_response); s0 = s0_response["result"]
        step_ordinal = s0["decision_facts"]["step_ordinal"]
        step_preview_path = exp_dir / f"steps/{step_ordinal:06d}/preview/preview.png"
        step_preview_bytes = step_preview_path.read_bytes()
        png0 = step_preview_bytes; mcp0 = _inspect(bridge.socket_path, s0["preview_handle"], step_preview_bytes)
        facts = s0.get("decision_facts")
        if not isinstance(facts, dict) or facts.get("accepted") is not False:
            raise ProviderFreeError("Step 0 must remain unaccepted")
        targets = facts.get("repair_targets")
        items = targets.get("items") if isinstance(targets, dict) else None
        step_frontier = _frontier(facts)
        active_depth = step_frontier["active_depth"]
        if not isinstance(items, list) or not items:
            raise ProviderFreeError("Step 0 repair frontier is invalid")
        active = items[0]
        _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": 0, "selected_targets": [active], "planned_edits": [{"edit_key": "expand-primary", "target_ranks": [active["rank"]], "description": "expand the primary box"}], "rationale": "expand the measured candidate", "preview_observation": "the candidate is narrower than the reference"})
        def submit_child(parent: Mapping[str, Any], parent_ordinal: int, next_ordinal: int, width: float, edit_key: str, edit_description: str, assessment_observation: str, assessment_summary: str) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
            parent_facts = parent["decision_facts"]
            parent_targets = parent_facts.get("repair_targets", {}).get("items", [])
            if not isinstance(parent_targets, list) or not parent_targets:
                raise ProviderFreeError("child parent has no repair target")
            target = parent_targets[0]
            _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": parent_ordinal, "selected_targets": [target], "planned_edits": [{"edit_key": edit_key, "target_ranks": [target["rank"]], "description": edit_description}], "rationale": edit_description, "preview_observation": "the parent frontier identifies the active repair target"})
            attempt = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph, "parent_step_handle": parent["step_handle"]}}); _public(attempt)
            child = attempt["result"]
            _source(candidate_root / "work/source/model.py", width)
            run = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "run_candidate_tool", "args": {"workspace_handle": wh, "attempt_handle": child["attempt_handle"], "candidate_handle": child["candidate_handle"], "operation_handle": child["capability_bundle_handle"]}}); _public(run)
            _json(candidate_root / "work/assessment.json", {"schema": "mesh-to-cad.assessment/1", "from_step": parent_ordinal, "to_step": next_ordinal, "preview_observation": assessment_observation, "summary": assessment_summary})
            response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_repair", "args": {"workspace_handle": wh, "attempt_handle": child["attempt_handle"], "candidate_handle": child["candidate_handle"]}}); _public(response)
            result = response["result"]
            ordinal = result["decision_facts"]["step_ordinal"]
            preview = exp_dir / f"steps/{ordinal:06d}/preview/preview.png"
            data = preview.read_bytes()
            return result, data, _inspect(bridge.socket_path, result["preview_handle"], data)
        published_ordinals = [step_ordinal]
        repair_a, png_a, mcp_a = submit_child(s0, step_ordinal, max(published_ordinals) + 1, REPAIR_A_WIDTH, "expand-primary", "expand toward the reference dimensions", "Repair A expands the narrow candidate toward the reference.", "Applied the bounded expansion repair hypothesis.")
        published_ordinals.append(repair_a["decision_facts"]["step_ordinal"])
        repair_b, png_b, mcp_b = submit_child(repair_a, repair_a["decision_facts"]["step_ordinal"], max(published_ordinals) + 1, REPAIR_B_WIDTH, "shrink-primary", "shrink below the reference as a regression", "Repair B shrinks the candidate and underfits the reference.", "Applied the bounded shrink regression hypothesis.")
        if repair_a["decision_facts"].get("accepted") is not False or repair_b["decision_facts"].get("accepted") is not False:
            raise ProviderFreeError("repair discriminator must remain unaccepted")
        frontier_a = _frontier(repair_a["decision_facts"])
        frontier_b = _frontier(repair_b["decision_facts"])
        best_label = "repair_a" if _frontier_order(frontier_a) > _frontier_order(step_frontier) else "step_zero"
        best = repair_a if best_label == "repair_a" else s0
        best_frontier = frontier_a if best_label == "repair_a" else step_frontier
        if not _frontier_order(best_frontier) > _frontier_order(frontier_b):
            raise ProviderFreeError(f"Active Depth repair ordering discriminator failed: S0={step_frontier},A={frontier_a},B={frontier_b}")
        provenance = supervisor.module_provenance(bridge)
        try:
            workspace_module = provenance["workspace"].resolve()
            workspace_module.relative_to(trusted.resolve())
        except (KeyError, OSError, ValueError) as exc:
            raise ProviderFreeError("loaded workspace module is not from installed publish tree") from exc
        if "selected_step not in graph" in workspace_module.read_text(encoding="utf-8"):
            raise ProviderFreeError("installed workspace module retains current-head selection rejection")
        a_ordinal = repair_a["decision_facts"]["step_ordinal"]
        b_ordinal = repair_b["decision_facts"]["step_ordinal"]
        a_preview_path = exp_dir / f"steps/{a_ordinal:06d}/preview/preview.png"
        best_png = png_a if best_label == "repair_a" else step_preview_bytes
        mcp_selected_reinspect = _inspect(bridge.socket_path, best["preview_handle"], best_png)
        selection_path = candidate_root / "selection.json"
        notes_path = candidate_root / "notes.md"
        best_name = "Step 0" if best_label == "step_zero" else "Repair A"
        _json(selection_path, {"schema": "mesh-to-cad.agent-selection-claim/1", "preview_observation": f"{best_name} is the strongest measured result; Repair B regressed the observed geometry.", "stop_reason": "no_feasible_strategy", "conflict": False, "conflict_details": None, "rationale": f"{best_name} is the strongest returned result after comparing the bounded repair trajectory."})
        notes_path.write_text(f"## Input\n\nThe input was measured against the committed reference fixture.\n## Modeling Intent\n\nThe candidate models the bounded box reconstruction intent.\n## Preserved Structural Features\n\nThe measured candidate preserves the primary solid structure.\n## Omitted Surface Details\n\nResidual surface details remain outside this deterministic gate.\n## Repair Trajectory\n\n{best_name} is the best-so-far result; Repair B was worse.\n## Final Selection\n\nThe best-so-far result is {best_name}, selected from its returned opaque handle.\n## Verification\n\nFinal verification is bound to {best_name} and its committed measurement.\n", encoding="utf-8")
        final_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "select_and_finalize", "args": {"workspace_handle": wh, "step_handle": best["step_handle"], "selection_handle": bootstrap["selection_handle"], "notes_handle": bootstrap["notes_handle"]}}); _public(final_response)
        if final_response["result"].get("state") != "finalized": raise ProviderFreeError("historical selection did not finalize")
        final_root = exp_dir / "final"
        final_manifest = json.loads((final_root / "manifest.json").read_text(encoding="utf-8"))
        graph = supervisor.workspace_api._core._build_graph(exp_dir, validate_steps=True)
        def cycle_record(result: Mapping[str, Any], parent: Mapping[str, Any]) -> dict[str, Any]:
            ordinal = result["decision_facts"]["step_ordinal"]
            parent_target = parent["decision_facts"]["repair_targets"]["items"][0]
            return {"ordinal": ordinal, "from_step": result["decision_facts"]["parent_step_ordinal"], "to_step": ordinal, "selected_parent_target": {key: parent_target[key] for key in ("rank", "kind", "bounds_canonical")}, "artifacts": {name: f"cycles/{ordinal:06d}/{name}.json" for name in ("plan", "assessment", "diff", "cycle", "attempt")}}
        cycles = {"repair_a": cycle_record(repair_a, s0), "repair_b": cycle_record(repair_b, repair_a)}
        def published_relative(path: Path) -> str: return path.resolve().relative_to(trusted.resolve()).as_posix()
        runner_interpreter = Path(sys.executable)
        try:
            runner_interpreter_relative = runner_interpreter.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise ProviderFreeError("runner interpreter is outside repository runtime") from exc
        if runner_interpreter_relative != ".venv/bin/python":
            raise ProviderFreeError("provider-free runner did not use repository venv")
        spec_path = candidate_root / "reconstruction-spec.json"
        spec_path.write_bytes(SPEC_INITIAL_BYTES)
        spec_path.write_bytes(SPEC_FINAL_BYTES)
        enabled_status = runner.persist_agent_reconstruction_spec(exp_dir, candidate_root, enabled=True, workload_status=0)
        disabled_exp = fixture_root / "disabled-exp"
        disabled_candidate = fixture_root / "disabled-candidate"
        (disabled_exp / "run").mkdir(parents=True)
        disabled_candidate.mkdir()
        (disabled_candidate / "reconstruction-spec.json").write_bytes(SPEC_FINAL_BYTES)
        runner.persist_agent_reconstruction_spec(disabled_exp, disabled_candidate, enabled=False, workload_status=0)
        missing_candidate = fixture_root / "missing-candidate"
        missing_candidate.mkdir()
        missing_status = runner.persist_agent_reconstruction_spec(exp_dir, missing_candidate, enabled=True, workload_status=0)
        prior_failure_status = runner.persist_agent_reconstruction_spec(exp_dir, missing_candidate, enabled=True, workload_status=17)
        authority_roots = (exp_dir / "setup", exp_dir / "steps", exp_dir / "cycles", exp_dir / "final")
        workspace_authority_absent = not any(
            path.name == "reconstruction-spec.json"
            for root in authority_roots
            for path in root.rglob("*")
        )
        spec_persistence = {"seam": "runner.persist_agent_reconstruction_spec", "path": runner.RECONSTRUCTION_SPEC_RELATIVE.as_posix(), "enabled_status": enabled_status, "updated_bytes": len((exp_dir / runner.RECONSTRUCTION_SPEC_RELATIVE).read_bytes()), "disabled_absent": not (disabled_exp / runner.RECONSTRUCTION_SPEC_RELATIVE).exists(), "workspace_authority_absent": workspace_authority_absent, "missing_status": missing_status, "prior_failure_status": prior_failure_status}
        evidence = {"schema": EVIDENCE_SCHEMA_V3, "identity": identity, "scenario": SCENARIO, "gate_passed": True, "selection": {"considered": ["step_zero", "repair_a", "repair_b"], "selected": best_label, "selected_step": best["decision_facts"]["step_ordinal"], "repair_b_is_head": b_ordinal}, "steps": {"step_zero": {"step_handle": s0["step_handle"], "ordinal": step_ordinal, "parent": None, "cycle": None, "accepted": False, "frontier": step_frontier, "target_count": len(items), "manifest": f"steps/{step_ordinal:06d}/step.json"}, "repair_a": {"step_handle": repair_a["step_handle"], "ordinal": a_ordinal, "parent": repair_a["decision_facts"]["parent_step_ordinal"], "cycle": a_ordinal, "accepted": False, "frontier": frontier_a, "target_count": len(repair_a["decision_facts"].get("repair_targets", {}).get("items", [])), "manifest": f"steps/{a_ordinal:06d}/step.json"}, "repair_b": {"step_handle": repair_b["step_handle"], "ordinal": b_ordinal, "parent": repair_b["decision_facts"]["parent_step_ordinal"], "cycle": b_ordinal, "accepted": False, "frontier": frontier_b, "target_count": len(repair_b["decision_facts"].get("repair_targets", {}).get("items", [])), "manifest": f"steps/{b_ordinal:06d}/step.json"}}, "graph": {"source": "step_parentage", "heads": [b_ordinal]}, "cycles": cycles, "previews": {"step_zero": {"path": step_preview_path.relative_to(exp_dir).as_posix(), "bytes": len(step_preview_bytes)}, "repair_a": {"path": a_preview_path.relative_to(exp_dir).as_posix(), "bytes": len(png_a)}, "repair_b": {"path": (exp_dir / f"steps/{b_ordinal:06d}/preview/preview.png").relative_to(exp_dir).as_posix(), "bytes": len(png_b)}, "selected_reinspect": {"path": (step_preview_path if best_label == "step_zero" else a_preview_path).relative_to(exp_dir).as_posix(), "bytes": len(best_png)}}, "mcp": {"step_zero": mcp0, "repair_a": mcp_a, "repair_b": mcp_b, "selected_reinspect": mcp_selected_reinspect}, "workspace_validation": runner._workspace_status_available(exp_dir), "module_paths": {"product_root": "skills", **{key: published_relative(value) for key, value in provenance.items()}, "rebuild": published_relative(published_rebuild), "geometry": published_relative(published_geometry)}, "runtime": {"interpreter": runner_interpreter_relative, "registry": {"schema": registry_document["schema"], "rebuild_id": registry_document["rebuild"]["id"], "geometry_id": registry_document["geometry"]["id"], "authority": "installed_publish_tree", "provenance": "receipt.publish_tree"}}, "final": {"manifest": "final/manifest.json", "selected_step": final_manifest.get("selected_step"), "source": "final/source/source/model.py", "measurement": "final/measurement.json", "preview": "final/preview.json", "verification": "final/verification.json", "identity_bound": final_manifest.get("selected_step") == best["decision_facts"]["step_ordinal"]}, "spec_persistence": spec_persistence}
        _json(evidence_path, evidence); _json(artifact_manifest_path, {"schema": "text-to-cad.provider-free-artifact-manifest/3", "final_status": 0, "identity": identity, "evidence": {"path": evidence_path.name}}); validate_artifacts(repo_root, record); return 0
    finally:
        for resource, action in ((bridge, "stop"), (supervisor, "close"), (candidate_lease, "release"), (sidecar, "stop")):
            if resource is None: continue
            try:
                getattr(resource, action)()
                if action == "close" and not supervisor.cancellation_confirmed:
                    cleanup_errors.append("supervisor cancellation was not confirmed")
            except Exception as exc:
                cleanup_errors.append(f"{action}:{type(exc).__name__}")
        try:
            shutil.rmtree(fixture_root)
        except OSError as exc:
            cleanup_errors.append(f"fixture:{type(exc).__name__}")
        if socket_dir is not None:
            try:
                shutil.rmtree(socket_dir)
            except OSError as exc:
                cleanup_errors.append(f"socket:{type(exc).__name__}")
        if cleanup_errors:
            if evidence_path.is_file():
                try:
                    failed = json.loads(evidence_path.read_text(encoding="utf-8")); failed["gate_passed"] = False; failed["cleanup_errors"] = cleanup_errors; _json(evidence_path, failed)
                except (OSError, json.JSONDecodeError):
                    pass
            raise ProviderFreeError("cleanup failed: " + ",".join(cleanup_errors))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--job", required=True); parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = protocol.load_state(args.state_root, args.job)
        return run_job(record, repo_root=Path(__file__).resolve().parents[2], host_home=Path.home(), environ=os.environ)
    except (ProviderFreeError, OSError, protocol.ProtocolError, runner.PilotError, runner.BrowserRuntimeError, runner.CandidateRuntimeError, SupervisorError) as exc:
        print(f"provider-free-workspace-repair-chain: {exc}", file=os.sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
