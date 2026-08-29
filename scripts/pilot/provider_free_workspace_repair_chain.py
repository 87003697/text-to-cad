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
import tempfile
from typing import Any, Mapping, Sequence

from scripts.pilot import plugin_deployment, provider_free_installed_plugin as installed, runner
from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge
from scripts.pilot.cvm_job import protocol
from scripts.pilot.workspace_supervisor import SupervisorError, WorkspaceSupervisor

SCENARIO = "workspace-repair-chain"
EVIDENCE_SCHEMA = "text-to-cad.provider-free-workspace-repair-chain-evidence/1"
MANIFEST_SCHEMA = "text-to-cad.provider-free-artifact-manifest/1"
MAX_EVIDENCE_BYTES = 96 * 1024
MAX_MANIFEST_BYTES = 8 * 1024
MCP_PERMITTED_INTENTS = frozenset({
    "workspace_status", "start_attempt", "run_candidate_tool", "submit_step_zero",
    "submit_repair", "inspect_formal_preview", "select_and_finalize", "observe_reference",
})


class ProviderFreeError(RuntimeError):
    """The deterministic repair-chain contract was not satisfied."""


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
    path.write_text(f"from build123d import Box\n\ndef gen_step():\n    return Box({width}, 6, 3)\n", encoding="utf-8")


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


def validate_artifacts(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path]:
    _, evidence_path, manifest_path = artifact_paths(repo_root, record)
    try:
        if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProviderFreeError("artifact too large")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("evidence missing or invalid") from exc
    identity = expected_identity(record)
    required = {"schema", "identity", "scenario", "gate_passed", "sequence", "active_depth", "selected_public_target", "step_zero", "repair", "ordinals", "attempt", "region_diff", "cycle", "previews", "original_plan_digest_binding", "mcp", "browser", "workspace_validation", "module_paths"}
    if not isinstance(evidence, dict) or set(evidence) != required or evidence.get("schema") != EVIDENCE_SCHEMA or evidence.get("identity") != identity or evidence.get("scenario") != SCENARIO or evidence.get("gate_passed") is not True:
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
    return evidence_path, manifest_path


def run_job(record: Mapping[str, Any], *, repo_root: Path, host_home: Path, environ: Mapping[str, str]) -> int:
    identity = expected_identity(record)
    try:
        receipt = installed.assert_current_authority(
            {**record, "scenario": installed.SCENARIO, "object": installed.SCENARIO}, host_home
        )
    except installed.ProviderFreeError as exc:
        raise ProviderFreeError("plugin authority changed") from exc
    exp_dir, evidence_path, manifest_path = artifact_paths(repo_root, record)
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
        registry = runner.publish_tool_registry(sidecar.capability_dir)
        supervisor = WorkspaceSupervisor(exp_dir, bind_reference=True, candidate_root=candidate_root, rebuild_entrypoint=published_rebuild, geometry_entrypoint=published_geometry, tool_registry=registry, browser_runtime_capability=sidecar.capability_dir / "runtime.json", candidate_runtime=candidate_lease.runtime, trusted_tools_root=trusted, trusted_product_root=trusted, step_zero_evidence_provider=lambda req: runner.real_step_zero_evidence_provider(req, capability_path=sidecar.capability_dir / "runtime.json", meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src", meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src"), repair_evidence_provider=lambda req: runner.real_repair_evidence_provider(req, capability_path=sidecar.capability_dir / "runtime.json", meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src", meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src"))
        socket_dir = Path(tempfile.mkdtemp(prefix="ttc-a-", dir="/tmp"))
        bridge = AgentSurfaceBridge(supervisor.agent_surface(), socket_dir / "surface.sock", trusted_product_root=trusted)
        bridge.start(); sidecar.start(); sidecar.preflight(); sidecar.preflight_mcp()
        bootstrap = supervisor.agent_bootstrap_contract(); surface = supervisor.agent_surface(); wh = bootstrap["workspace_handle"]; ph = bootstrap["plan_handle"]
        plan = candidate_root / "plan.json"; _json(plan, {"schema": "mesh-to-cad.initial-plan/1", "summary": "deterministic box reconstruction"})
        a0_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph}}); _public(a0_response); a0 = a0_response["result"]
        work = candidate_root / "work"; _source(work / "source/model.py", 8)
        run0 = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "run_candidate_tool", "args": {"workspace_handle": wh, "attempt_handle": a0["attempt_handle"], "candidate_handle": a0["candidate_handle"], "operation_handle": a0["capability_bundle_handle"]}}); _public(run0)
        s0_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_step_zero", "args": {"workspace_handle": wh, "attempt_handle": a0["attempt_handle"], "candidate_handle": a0["candidate_handle"]}}); _public(s0_response); s0 = s0_response["result"]
        step_ordinal = s0["decision_facts"]["step_ordinal"]
        step_preview_path = exp_dir / f"steps/{step_ordinal:06d}/preview/preview.png"
        step_preview_bytes = step_preview_path.read_bytes()
        png0 = step_preview_bytes; mcp0 = _inspect(bridge.socket_path, s0["preview_handle"], step_preview_bytes)
        facts = s0.get("decision_facts")
        if not isinstance(facts, dict) or facts.get("accepted") is not False:
            raise ProviderFreeError("Step 0 must remain unaccepted")
        summary = facts.get("residual_summary")
        frontier = summary.get("repair_frontier") if isinstance(summary, dict) else None
        targets = facts.get("repair_targets")
        items = targets.get("items") if isinstance(targets, dict) else None
        active_depth = frontier.get("active_depth") if isinstance(frontier, dict) else None
        if not isinstance(active_depth, int) or active_depth <= 0 or not isinstance(items, list) or not items:
            raise ProviderFreeError("Step 0 repair frontier is invalid")
        active = items[0]
        _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": 0, "selected_targets": [active], "planned_edits": [{"edit_key": "expand-primary", "target_ranks": [active["rank"]], "description": "expand the primary box"}], "rationale": "expand the measured candidate", "preview_observation": "the candidate is narrower than the reference"})
        a1_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph, "parent_step_handle": s0["step_handle"]}}); _public(a1_response); a1 = a1_response["result"]
        _source(candidate_root / "work/source/model.py", 12)
        run1 = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "run_candidate_tool", "args": {"workspace_handle": wh, "attempt_handle": a1["attempt_handle"], "candidate_handle": a1["candidate_handle"], "operation_handle": a1["capability_bundle_handle"]}}); _public(run1)
        from_step = s0["decision_facts"]["step_ordinal"]
        _json(candidate_root / "work/assessment.json", {"schema": "mesh-to-cad.assessment/1", "from_step": from_step, "to_step": from_step + 1, "preview_observation": "Step 0 formal preview is narrower than the reference along the selected active target.", "summary": "Expanded the candidate from 8 to 12 units along the selected target so the repair can be measured against the parent."})
        r1_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_repair", "args": {"workspace_handle": wh, "attempt_handle": a1["attempt_handle"], "candidate_handle": a1["candidate_handle"]}}); _public(r1_response); r1 = r1_response["result"]
        if r1.get("state") != "published": raise ProviderFreeError("repair did not publish")
        repair_ordinal = r1["decision_facts"]["step_ordinal"]
        repair_parent_ordinal = r1["decision_facts"]["parent_step_ordinal"]
        repair_preview_path = exp_dir / f"steps/{repair_ordinal:06d}/preview/preview.png"
        repair_preview_bytes = repair_preview_path.read_bytes()
        png1 = repair_preview_bytes; mcp1 = _inspect(bridge.socket_path, r1["preview_handle"], repair_preview_bytes)
        step_preview = {"path": step_preview_path.relative_to(exp_dir).as_posix(), "bytes": len(step_preview_bytes)}
        repair_preview = {"path": repair_preview_path.relative_to(exp_dir).as_posix(), "bytes": len(repair_preview_bytes)}
        diff_path = exp_dir / f"cycles/{repair_ordinal:06d}/diff.json"
        cycle_path = exp_dir / f"cycles/{repair_ordinal:06d}/cycle.json"
        step0_attempt_path = exp_dir / f"steps/{step_ordinal:06d}/attempt.json"
        repair_attempt_path = exp_dir / f"cycles/{repair_ordinal:06d}/attempt.json"
        if not all(path.is_file() for path in (diff_path, cycle_path, step0_attempt_path, repair_attempt_path)):
            raise ProviderFreeError("exact authority artifact missing")
        diff = {"path": diff_path.relative_to(exp_dir).as_posix(), "bytes": diff_path.stat().st_size}
        cycle = {"path": cycle_path.relative_to(exp_dir).as_posix(), "bytes": cycle_path.stat().st_size}
        attempt_docs = [step0_attempt_path, repair_attempt_path]
        if not all(path.is_file() for path in attempt_docs): raise ProviderFreeError("attempt authority missing")
        plans = [json.loads(path.read_text(encoding="utf-8")) for path in attempt_docs]
        repair_attempt = plans[1]
        if repair_attempt.get("intended_step") != 1 or repair_attempt.get("from_step") != 0:
            raise ProviderFreeError("repair attempt authority mismatch")
        repair_diff = json.loads((exp_dir / diff["path"]).read_text(encoding="utf-8"))
        plan_digest = plans[1].get("plan_digest")
        repair_batch = repair_diff.get("repair_batch")
        required_batch = {"schema", "from_step", "selected_targets", "planned_edits", "plan_sha256"}
        if not isinstance(repair_batch, dict) or set(repair_batch) != required_batch or repair_batch.get("schema") != "voxblame.repair-batch/1" or not isinstance(repair_batch.get("from_step"), int) or not isinstance(repair_batch.get("selected_targets"), list) or not isinstance(repair_batch.get("planned_edits"), list) or not isinstance(repair_batch.get("plan_sha256"), str):
            raise ProviderFreeError("region diff repair batch binding missing")
        batch_digest = repair_batch["plan_sha256"]
        provenance = supervisor.module_provenance(bridge)
        def published_relative(path: Path) -> str:
            return path.resolve().relative_to(trusted.resolve()).as_posix()
        evidence = {"schema": EVIDENCE_SCHEMA, "identity": identity, "scenario": SCENARIO, "gate_passed": True, "sequence": ["plan", "start_attempt", "run_candidate", "submit_step_zero", "mcp_inspect_step_zero", "start_repair", "run_candidate", "submit_repair", "mcp_inspect_repair"], "active_depth": active_depth, "selected_public_target": active, "step_zero": {"handle": s0["step_handle"], "preview": s0["preview_handle"]}, "repair": {"handle": r1["step_handle"], "preview": r1["preview_handle"]}, "ordinals": {"step_zero": step_ordinal, "repair": repair_ordinal, "repair_parent": repair_parent_ordinal}, "attempt": {"path": repair_attempt_path.relative_to(exp_dir).as_posix(), "bytes": repair_attempt_path.stat().st_size}, "region_diff": diff, "cycle": cycle, "previews": {"step_zero": step_preview, "repair": repair_preview, "distinct": png0 != png1}, "original_plan_digest_binding": {"attempts": len(plans), "repair_attempt_path": repair_attempt_path.relative_to(exp_dir).as_posix(), "plan_files": [path.relative_to(exp_dir).as_posix() for path in attempt_docs], "attempt_plan_digest": plan_digest, "region_diff_plan_sha256": batch_digest, "derived_equal": plan_digest == batch_digest}, "mcp": {"step_zero": mcp0, "repair": mcp1}, "browser": {"capability": (sidecar.capability_dir / "runtime.json").is_file(), "preflight": (sidecar.capability_dir / "runtime.json").is_file()}, "workspace_validation": runner._workspace_status_available(exp_dir), "module_paths": {"product_root": "skills", **{key: published_relative(value) for key, value in provenance.items()}, "rebuild": published_relative(published_rebuild), "geometry": published_relative(published_geometry)}}
        _json(evidence_path, evidence); _json(manifest_path, {"schema": MANIFEST_SCHEMA, "final_status": 0, "identity": identity, "evidence": {"path": evidence_path.name}}); validate_artifacts(repo_root, record); return 0
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
