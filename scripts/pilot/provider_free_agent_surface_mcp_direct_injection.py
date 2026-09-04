#!/usr/bin/env python3
"""Closed direct-only MCP namespace injection discriminator."""

from __future__ import annotations

import argparse
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Mapping, Sequence

from scripts.pilot import plugin_deployment, provider_free_agent_surface_mcp_injection as nested
from scripts.pilot import runner
from scripts.pilot.cvm_job import protocol


SCENARIO = "agent-surface-mcp-direct-injection"
EVIDENCE_SCHEMA = "text-to-cad.provider-free-agent-surface-mcp-direct-injection-evidence/1"
MANIFEST_SCHEMA = nested.MANIFEST_SCHEMA
MAX_EVIDENCE_BYTES = nested.MAX_EVIDENCE_BYTES
MAX_MANIFEST_BYTES = nested.MAX_MANIFEST_BYTES
_TOOL = "inspect_formal_preview"
_NAMESPACE = "mcp__agent_surface"
_CALL_ID = "call_direct_provider_free"
_FIXTURE = nested._FIXTURE


class ProviderFreeError(RuntimeError):
    """The closed direct-only MCP injection contract was not satisfied."""


def authority_identity(receipt: plugin_deployment.DeploymentReceipt) -> dict[str, str]:
    return nested.authority_identity(receipt)


def expected_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    identity = nested.expected_identity({**record, "scenario": nested.SCENARIO, "object": nested.SCENARIO})
    if record.get("scenario") != SCENARIO or record.get("object") != SCENARIO:
        raise ProviderFreeError("job is not a direct-only MCP provider-free request")
    return {**identity, "scenario": SCENARIO}


def assert_current_authority(record: Mapping[str, Any], host_home: Path) -> plugin_deployment.DeploymentReceipt:
    return nested.assert_current_authority(record, host_home)


def build_runner_env(environ: Mapping[str, str]) -> dict[str, str]:
    return nested.build_runner_env(environ)


def artifact_paths(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    return nested.artifact_paths(repo_root, record)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    nested._atomic_write(path, value)


def _sse_event(event: str, value: Mapping[str, Any]) -> bytes:
    return nested._sse_event(event, value)


def _response_events(*, call: bool) -> bytes:
    response = {"id": "resp_direct_provider_free", "object": "response", "status": "completed"}
    if not call:
        item = {"id": "msg_direct_provider_free", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": "provider-free direct canary complete"}]}
        events = [
            _sse_event("response.created", {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}}),
            _sse_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
            _sse_event("response.completed", {"type": "response.completed", "response": {**response, "output": [item]}}),
        ]
        return b"".join(events)
    item = {
        "id": "fc_direct_provider_free", "type": "function_call", "status": "completed",
        "call_id": _CALL_ID, "namespace": _NAMESPACE, "name": _TOOL,
        "arguments": '{"preview_handle":"preview-probe"}',
    }
    events = [
        _sse_event("response.created", {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}}),
        _sse_event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "arguments": ""}}),
        _sse_event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": 0, "delta": item["arguments"]}),
        _sse_event("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": 0, "arguments": item["arguments"]}),
        _sse_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
        _sse_event("response.completed", {"type": "response.completed", "response": {**response, "output": [item]}}),
    ]
    return b"".join(events)


class _Receiver(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, Any, str]] = []

    def do_POST(self) -> None:
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        self.__class__.requests.append((self.path, body, self.client_address[0]))
        count = len(self.__class__.requests)
        body_bytes = _response_events(call=count == 1) if count <= 2 else b'{"error":{"type":"invalid_request_error"}}'
        self.send_response(200 if count <= 2 else 400)
        self.send_header("Content-Type", "text/event-stream" if count <= 2 else "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, *_: object) -> None:
        pass


def _direct_config(config_path: Path) -> bool:
    try:
        body = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return nested._config_enabled(config_path) and '[features.code_mode]' in body and 'direct_only_tool_namespaces=["mcp__agent_surface"]' in body


def _direct_protocol(body: object) -> dict[str, object]:
    request = body if isinstance(body, dict) else {}
    inputs = request.get("input") if isinstance(request.get("input"), list) else []
    additional = [item for item in inputs if isinstance(item, dict) and item.get("type") == "additional_tools"]
    roots = additional[0].get("tools") if len(additional) == 1 and isinstance(additional[0].get("tools"), list) else []
    namespaces = [item for item in roots if isinstance(item, dict) and item.get("name") == _NAMESPACE]
    namespace = namespaces[0] if len(namespaces) == 1 else None
    children = namespace.get("tools") if isinstance(namespace, dict) and isinstance(namespace.get("tools"), list) else []
    matches = [item for item in children if isinstance(item, dict) and item.get("name") == _TOOL]
    child = matches[0] if len(matches) == 1 else None
    schema = child.get("parameters", child.get("input_schema")) if isinstance(child, dict) else None
    required = schema.get("required") if isinstance(schema, dict) and isinstance(schema.get("required"), list) else []
    properties = schema.get("properties") if isinstance(schema, dict) and isinstance(schema.get("properties"), dict) else None
    preview_handle = properties.get("preview_handle") if properties is not None else None
    names = nested._bounded_names(roots)
    return {
        "top_level_tools_present": isinstance(request.get("tools"), list),
        "additional_tools_present": len(additional) == 1,
        "native_namespace": isinstance(namespace, dict),
        "native_namespace_type": namespace.get("type") if isinstance(namespace, dict) and type(namespace.get("type")) is str else None,
        "native_child": isinstance(child, dict),
        "native_child_function": isinstance(child, dict) and child.get("type") == "function",
        "parameters_object": isinstance(schema, dict) and schema.get("type") == "object",
        "preview_handle_string": isinstance(preview_handle, dict) and set(preview_handle) >= {"type"} and preview_handle.get("type") == "string",
        "required_preview_handle": required == ["preview_handle"],
        "additional_properties_false": isinstance(schema, dict) and schema.get("additionalProperties") is False,
        "agent_surface_child_count": len(children),
        "flat_alias_present": "mcp__agent_surface__inspect_formal_preview" in names,
    }


def _native_call(body: object) -> dict[str, object]:
    items = body.get("input") if isinstance(body, dict) and isinstance(body.get("input"), list) else []
    outputs = [item for item in items if isinstance(item, dict) and item.get("type") == "function_call_output"]
    result: dict[str, object] = {
        "call_id_matches": False,
        "output_shape": "invalid",
        "supervisor_unavailable": False,
        "output_type": "missing",
        "output_keys": [],
    }
    if len(outputs) != 1:
        return result
    result["call_id_matches"] = outputs[0].get("call_id") == _CALL_ID
    output = outputs[0].get("output")
    result["output_type"] = type(output).__name__
    if isinstance(output, dict):
        result["output_keys"] = sorted(str(key) for key in output)[:16]
    if isinstance(output, str):
        if len(output) > 4096:
            return result
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return result
    elif isinstance(output, dict):
        payload = output.get("result") if isinstance(output.get("result"), dict) else output
    elif isinstance(output, list) and len(output) == 1 and isinstance(output[0], dict) and output[0].get("type") in {"input_text", "text"} and isinstance(output[0].get("text"), str) and len(output[0]["text"]) <= 4096:
        try:
            payload = json.loads(output[0]["text"])
        except json.JSONDecodeError:
            return result
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            payload = payload["result"]
    else:
        return result
    if not isinstance(payload, dict) or not {"isError", "structuredContent", "content"} <= set(payload) or type(payload.get("isError")) is not bool or payload.get("isError") is not True:
        return result
    structured = payload.get("structuredContent")
    result["supervisor_unavailable"] = isinstance(structured, dict) and isinstance(structured.get("error"), dict) and structured["error"].get("classification") == "supervisor_unavailable"
    result["output_shape"] = "json"
    return result


def _valid_protocol(value: object) -> bool:
    fields = {"top_level_tools_present", "additional_tools_present", "native_namespace", "native_namespace_type", "native_child", "native_child_function", "parameters_object", "preview_handle_string", "required_preview_handle", "additional_properties_false", "agent_surface_child_count", "flat_alias_present"}
    if not isinstance(value, dict) or set(value) != fields:
        return False
    if any(type(value[key]) is not bool for key in fields - {"agent_surface_child_count", "native_namespace_type"}):
        return False
    if type(value["agent_surface_child_count"]) is not int:
        return False
    return type(value["native_namespace_type"]) is str and value["native_namespace_type"] == "namespace" and value == {key: True for key in fields - {"top_level_tools_present", "flat_alias_present", "agent_surface_child_count", "native_namespace_type"}} | {"top_level_tools_present": False, "flat_alias_present": False, "agent_surface_child_count": 1, "native_namespace_type": "namespace"}


def _valid_second(value: object) -> bool:
    fields = {"call_id_matches", "output_shape", "supervisor_unavailable", "output_type", "output_keys"}
    return (
        isinstance(value, dict)
        and set(value) == fields
        and type(value["call_id_matches"]) is bool
        and type(value["supervisor_unavailable"]) is bool
        and type(value["output_shape"]) is str
        and type(value["output_type"]) is str
        and isinstance(value["output_keys"], list)
        and all(type(key) is str for key in value["output_keys"])
        and value["call_id_matches"] is True
        and value["output_shape"] == "json"
        and value["supervisor_unavailable"] is True
    )


def _valid_preflight(value: object) -> bool:
    fields = {"spawned", "initialize_ok", "tools_list_ok", "exact_descriptor_seen", "tools_call_ok", "is_error", "text_present", "supervisor_unavailable", "exit_class"}
    if not isinstance(value, dict) or set(value) != fields:
        return False
    if any(type(value[key]) is not bool for key in fields - {"exit_class"}):
        return False
    return all(value[key] is True for key in fields - {"exit_class"}) and type(value["exit_class"]) is str and value["exit_class"] == "zero"


def _valid_dispatch(value: object) -> bool:
    return isinstance(value, dict) and set(value) == {"audit_count", "tools_call", "method", "tool_name", "argument_keys"} and type(value["audit_count"]) is str and value["audit_count"] == "1" and type(value["tools_call"]) is bool and value["tools_call"] is True and type(value["method"]) is str and value["method"] == "tools/call" and type(value["tool_name"]) is str and value["tool_name"] == _TOOL and isinstance(value["argument_keys"], list) and value["argument_keys"] == ["preview_handle"] and all(type(key) is str for key in value["argument_keys"])


def _valid_process(value: object) -> bool:
    return isinstance(value, dict) and set(value) == {"codex_version", "version_exit_code", "workload_exit_code"} and type(value["codex_version"]) is str and value["codex_version"].startswith("0.") and type(value["version_exit_code"]) is int and value["version_exit_code"] == 0 and type(value["workload_exit_code"]) is int and value["workload_exit_code"] == 0


def validate_artifacts(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path]:
    _, evidence_path, manifest_path = artifact_paths(repo_root, record)
    try:
        if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProviderFreeError("provider-free artifact exceeds its byte limit")
        evidence = json.loads(evidence_path.read_bytes())
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("provider-free evidence or artifact manifest is missing/invalid") from exc
    identity = expected_identity(record)
    fields = {"schema", "identity", "sandbox", "private_config", "mcp_preflight", "preflight_dispatch", "first_request", "mcp_dispatch", "second_request", "receiver", "process", "gate_passed"}
    if not isinstance(evidence, dict) or set(evidence) != fields or evidence.get("schema") != EVIDENCE_SCHEMA or evidence.get("identity") != identity:
        raise ProviderFreeError("provider-free evidence has an invalid shape")
    sandbox = evidence.get("sandbox")
    private_config = evidence.get("private_config")
    if not isinstance(sandbox, dict) or set(sandbox) != {"runner_bwrap", "gateway", "network"} or type(sandbox["runner_bwrap"]) is not bool or sandbox["runner_bwrap"] is not True or type(sandbox["gateway"]) is not str or sandbox["gateway"] != "codex-tap-gpt56" or type(sandbox["network"]) is not str or sandbox["network"] != "loopback-only":
        raise ProviderFreeError("provider-free evidence has an invalid sandbox/config")
    if not isinstance(private_config, dict) or set(private_config) != {"agent_surface_server_enabled", "direct_only_namespace"} or type(private_config["agent_surface_server_enabled"]) is not bool or private_config["agent_surface_server_enabled"] is not True or type(private_config["direct_only_namespace"]) is not str or private_config["direct_only_namespace"] != _NAMESPACE:
        raise ProviderFreeError("provider-free evidence has an invalid sandbox/config")
    first = evidence.get("first_request")
    if not _valid_preflight(evidence.get("mcp_preflight")) or not _valid_dispatch(evidence.get("preflight_dispatch")) or not isinstance(first, dict) or set(first) != {"received", "protocol"} or type(first["received"]) is not bool or first["received"] is not True or not _valid_protocol(first["protocol"]):
        raise ProviderFreeError("provider-free evidence has an invalid direct preflight/request")
    if not _valid_dispatch(evidence.get("mcp_dispatch")) or not _valid_second(evidence.get("second_request")):
        raise ProviderFreeError("provider-free evidence has no successful native dispatch")
    receiver = evidence.get("receiver")
    if not isinstance(receiver, dict) or set(receiver) != {"loopback_only", "provider_escape", "request_count"} or type(receiver["loopback_only"]) is not bool or receiver["loopback_only"] is not True or type(receiver["provider_escape"]) is not bool or receiver["provider_escape"] is not False or type(receiver["request_count"]) is not int or receiver["request_count"] != 2:
        raise ProviderFreeError("provider-free evidence has an invalid receiver result")
    if not _valid_process(evidence.get("process")) or type(evidence.get("gate_passed")) is not bool or evidence["gate_passed"] is not True:
        raise ProviderFreeError("provider-free evidence has an invalid process result")
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "final_status", "identity", "evidence"} or type(manifest["schema"]) is not str or manifest["schema"] != MANIFEST_SCHEMA or type(manifest["final_status"]) is not int or manifest["final_status"] != 0 or manifest["identity"] != identity or not isinstance(manifest["evidence"], dict) or set(manifest["evidence"]) != {"path"} or type(manifest["evidence"]["path"]) is not str or manifest["evidence"]["path"] != evidence_path.name:
        raise ProviderFreeError("provider-free artifact manifest differs from evidence")
    return evidence_path, manifest_path


def run_job(record: Mapping[str, Any], *, repo_root: Path, host_home: Path, environ: Mapping[str, str]) -> int:
    identity = expected_identity(record)
    assert_current_authority(record, host_home)
    exp_dir, evidence_path, manifest_path = artifact_paths(repo_root, record)
    exp_dir.mkdir(parents=True, exist_ok=False)
    candidate_dir = Path(tempfile.mkdtemp(prefix="ttc-agent-surface-direct-gate-"))
    socket_dir = Path(tempfile.mkdtemp(prefix="ttc-agent-surface-direct-socket-"))
    socket_path = socket_dir / "surface.sock"
    bridge: nested._AuditedBridge | None = None
    server = None
    thread = None
    requests: list[tuple[str, Any, str]] = []
    preflight: dict[str, object] = {"spawned": False, "initialize_ok": False, "tools_list_ok": False, "exact_descriptor_seen": False, "tools_call_ok": False, "is_error": None, "text_present": False, "supervisor_unavailable": None, "exit_class": "spawn_failed"}
    preflight_dispatch: dict[str, object] = {"audit_count": "0"}
    config_enabled = False
    version_exit = workload_exit = None
    version = None
    try:
        bridge = nested._AuditedBridge(None, socket_path)
        bridge.surface = bridge._mcp.AgentSurface(None)
        bridge.start()
        config_path = exp_dir / runner.JOB_CODEX_HOME_REL / plugin_deployment.CONFIG_TOML_NAME
        _Receiver.requests = []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        gate_env = dict(environ); gate_env["VENUS_TOKEN"] = "provider-free-loopback-only"
        fixture = repo_root / _FIXTURE
        child_env = runner.build_sandbox_environment(gate_env, f"http://127.0.0.1:{server.server_port}/v1", isolated_agent=True, tap_client_token="provider-free-loopback-token")
        bwrap = lambda command: runner.build_bwrap_argv(repo_root, exp_dir, [fixture], command, gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path, isolated_code_mode_direct_namespace=_NAMESPACE)
        preflight = nested._same_sandbox_mcp_preflight(bwrap(["python3", "/agent-surface/client.py", "--mcp"]), child_env)
        preflight_dispatch = nested._audit_projection(bridge.audit); bridge.audit.clear()
        version_result = subprocess.run(bwrap(["codex", "--version"]), env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=30)
        version_exit = nested._safe_returncode(version_result.returncode); version = nested._version_from_output(version_result.stdout + "\n" + version_result.stderr)
        workload = ["gateway/codex-tap-gpt56", "sol", "exec", "--skip-git-repo-check", "--ephemeral", "run the fixed provider-free direct MCP canary"]
        workload_result = subprocess.run(bwrap(workload), env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=60)
        workload_exit = nested._safe_returncode(workload_result.returncode); requests = list(_Receiver.requests)
        config_enabled = _direct_config(config_path)
    finally:
        if server is not None: server.shutdown(); server.server_close()
        if thread is not None: thread.join(timeout=2)
        if bridge is not None: bridge.stop()
        shutil.rmtree(candidate_dir, ignore_errors=True); shutil.rmtree(socket_dir, ignore_errors=True); shutil.rmtree(exp_dir / "run" / ".codex-home", ignore_errors=True)
    evidence = {"schema": EVIDENCE_SCHEMA, "identity": identity, "sandbox": {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"}, "private_config": {"agent_surface_server_enabled": config_enabled, "direct_only_namespace": _NAMESPACE}, "mcp_preflight": preflight, "preflight_dispatch": preflight_dispatch, "first_request": {"received": bool(requests), "protocol": _direct_protocol(requests[0][1] if requests else None)}, "mcp_dispatch": nested._audit_projection(bridge.audit if bridge is not None else []), "second_request": _native_call(requests[1][1] if len(requests) > 1 else None), "receiver": {"loopback_only": len(requests) == 2 and all(path == "/v1/responses" and host == "127.0.0.1" for path, _body, host in requests), "provider_escape": False, "request_count": len(requests)}, "process": {"codex_version": version, "version_exit_code": version_exit, "workload_exit_code": workload_exit}, "gate_passed": False}
    try:
        passed = dict(evidence); passed["gate_passed"] = True
        _atomic_write(evidence_path, passed); _atomic_write(manifest_path, {"schema": MANIFEST_SCHEMA, "final_status": 0, "identity": identity, "evidence": {"path": evidence_path.name}})
        validate_artifacts(repo_root, record); evidence = passed
    except ProviderFreeError:
        _atomic_write(evidence_path, evidence); _atomic_write(manifest_path, {"schema": MANIFEST_SCHEMA, "final_status": 1, "identity": identity, "evidence": {"path": evidence_path.name}})
    return 0 if evidence["gate_passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--job", required=True); parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run_job(protocol.load_state(args.state_root, args.job), repo_root=Path(__file__).resolve().parents[2], host_home=Path.home(), environ=os.environ)
    except (ProviderFreeError, plugin_deployment.PluginAuthorityError, protocol.ProtocolError, runner.PilotError) as exc:
        print(f"provider-free-agent-surface-mcp-direct-injection: {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
