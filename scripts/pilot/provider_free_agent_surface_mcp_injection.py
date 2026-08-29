#!/usr/bin/env python3
"""Capture the isolated Codex MCP tool injection without a model provider."""

from __future__ import annotations

import argparse
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Any, Mapping, Sequence

from scripts.pilot import plugin_deployment, provider_free_installed_plugin as installed
from scripts.pilot import runner
from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge
from scripts.pilot.cvm_job import protocol


SCENARIO = "agent-surface-mcp-injection"
EVIDENCE_SCHEMA = "text-to-cad.provider-free-agent-surface-mcp-injection-evidence/5"
MANIFEST_SCHEMA = "text-to-cad.provider-free-artifact-manifest/1"
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 4 * 1024
MAX_TOOL_NAMES = 64
MAX_TOOL_NAME_BYTES = 256
MAX_RECEIVER_REQUESTS = 2
_FIXTURE = Path("models/toys4k/cup_cup_033.ply")
_TOOL = "inspect_formal_preview"
_VERSION = re.compile(r"(?:codex-cli|OpenAI Codex v)\s*([0-9]+\.[0-9]+\.[0-9]+)")


class ProviderFreeError(RuntimeError):
    """The closed MCP-injection probe contract was not satisfied."""


def authority_identity(receipt: plugin_deployment.DeploymentReceipt) -> dict[str, str]:
    return installed.authority_identity(receipt)


def expected_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    authority = record.get("plugin_authority")
    if not isinstance(authority, dict) or set(authority) != set(installed.AUTHORITY_FIELDS):
        raise ProviderFreeError("job has invalid plugin-authority binding")
    if any(not isinstance(authority[name], str) or not authority[name] for name in authority):
        raise ProviderFreeError("job has invalid plugin-authority values")
    expected_exp_dir = f"outputs/{record['job']}" if isinstance(record.get("job"), str) else None
    if (
        record.get("provider_free") is not True
        or record.get("scenario") != SCENARIO
        or record.get("object") != SCENARIO
        or record.get("token_slot") is not None
        or record.get("exp_dir") != expected_exp_dir
    ):
        raise ProviderFreeError("job is not an MCP-injection provider-free request")
    return {
        "job": record.get("job"),
        "scenario": SCENARIO,
        "plugin_selector": plugin_deployment.PLUGIN_SELECTOR,
        "marketplace": plugin_deployment.MARKETPLACE_NAME,
        "authority": dict(authority),
    }


def assert_current_authority(record: Mapping[str, Any], host_home: Path) -> plugin_deployment.DeploymentReceipt:
    try:
        receipt = plugin_deployment.resolve_current_authority(host_home)
    except plugin_deployment.PluginAuthorityError as exc:
        raise ProviderFreeError(f"plugin authority is unavailable: {exc}") from exc
    if authority_identity(receipt) != record.get("plugin_authority"):
        raise ProviderFreeError("current plugin authority differs from submitted job")
    return receipt


def build_runner_env(environ: Mapping[str, str]) -> dict[str, str]:
    return installed.build_runner_env(environ)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)



def artifact_paths(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    exp_dir = repo_root / str(record["exp_dir"])
    return exp_dir, exp_dir / "provider-free-evidence.json", exp_dir / "artifact_manifest.json"


def _bounded_names(value: Any) -> list[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        tools = value.get("tools")
        if isinstance(tools, list):
            for item in tools:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.add(item["name"])
        for item in value.values():
            names.update(_bounded_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_bounded_names(item))
    return sorted(name for name in names if len(name) <= MAX_TOOL_NAME_BYTES)[:MAX_TOOL_NAMES]


def _inspect_name(names: list[str]) -> tuple[bool, bool]:
    exact = _TOOL in names
    qualified = any(name.endswith(f"__{_TOOL}") for name in names)
    return exact, qualified


def _sse_event(event: str, value: Mapping[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(value, separators=(',', ':'))}\n\n".encode()


def _response_events(*, call: Mapping[str, str] | None = None) -> bytes:
    response_id = "resp_provider_free"
    events = [
        _sse_event("response.created", {
            "type": "response.created",
            "response": {"id": response_id, "object": "response", "status": "in_progress", "output": []},
        }),
    ]
    output: list[dict[str, Any]] = []
    if call is not None:
        item = {
            "id": "ctc_provider_free",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_provider_free",
            "name": call["name"],
            "input": call["input"],
        }
        events.extend((
            _sse_event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "input": ""}}),
            _sse_event("response.custom_tool_call_input.done", {"type": "response.custom_tool_call_input.done", "input": call["input"], "item_id": item["id"], "output_index": 0}),
            _sse_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
        ))
        output.append(item)
    else:
        item = {
            "id": "msg_provider_free",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "provider-free canary complete"}],
        }
        events.extend((
            _sse_event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}}),
            _sse_event("response.content_part.added", {"type": "response.content_part.added", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": ""}}),
            _sse_event("response.output_text.done", {"type": "response.output_text.done", "text": "provider-free canary complete", "item_id": item["id"], "output_index": 0, "content_index": 0}),
            _sse_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
        ))
        output.append(item)
    events.append(_sse_event("response.completed", {"type": "response.completed", "response": {"id": response_id, "object": "response", "status": "completed", "output": output}}))
    return b"".join(events)


_EXEC_INPUT = """const wanted = \"mcp__agent_surface__inspect_formal_preview\";
const alias_found = ALL_TOOLS.some(({ name }) => name === wanted);
const static_callable = typeof tools.mcp__agent_surface__inspect_formal_preview === \"function\";
let call_attempted = false;
let call_threw = false;
let result = null;
if (alias_found && static_callable) {
  call_attempted = true;
  try {
    result = await tools.mcp__agent_surface__inspect_formal_preview({preview_handle:\"preview-probe\"});
  } catch (_) {
    call_threw = true;
  }
}
const content = Array.isArray(result?.content) ? result.content : [];
const structured = result?.structuredContent;
const first_text = content.find((item) => item?.type === "text" && typeof item?.text === "string")?.text;
let first_text_classification = "none";
if (result?.isError === true && typeof first_text === "string") {
  const lowered = first_text.toLowerCase();
  if (lowered.includes("is not available to the model")) first_text_classification = "not_available_to_model";
  else if (lowered.includes("mcp client is not initialized")) first_text_classification = "mcp_client_not_initialized";
  else if (lowered.includes("mcp client shutdown")) first_text_classification = "mcp_client_shutdown";
  else if (lowered.includes("tool not found")) first_text_classification = "tool_not_found";
  else if (lowered.includes("transport error:") || lowered.includes("transport failed:") || lowered.includes("transport disconnected:")) first_text_classification = "transport";
  else first_text_classification = "other";
}
text(JSON.stringify({
  alias_found,
  static_callable,
  call_attempted,
  call_threw,
  isError: result ? result.isError === true : null,
  content_types: content.map((item) => typeof item?.type === "string" ? item.type : "invalid").slice(0,4),
  structured_content_present: structured !== null && typeof structured === "object",
  supervisor_unavailable: result ? structured?.error?.classification === "supervisor_unavailable" : null,
  first_text_classification
}));
"""


class _Receiver(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, Any, str]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        self.__class__.requests.append((self.path, body, self.client_address[0]))
        if len(self.__class__.requests) == 1:
            response = _response_events(call={"name": "exec", "input": _EXEC_INPUT})
        elif len(self.__class__.requests) == 2:
            response = _response_events()
        else:
            response = b'{"error":{"message":"too many provider-free requests","type":"invalid_request_error"}}'
        self.send_response(200 if len(self.__class__.requests) <= 2 else 400)
        self.send_header("Content-Type", "text/event-stream" if len(self.__class__.requests) <= 2 else "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_: object) -> None:
        pass


class _CaptureReceiver(_Receiver):
    """The one-request receiver retained for the isolated differential gate."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        self.__class__.requests.append((self.path, body, self.client_address[0]))
        response = b'{"error":{"message":"provider-free capture complete","type":"invalid_request_error"}}'
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def _mcp_preflight(socket_path: Path) -> dict[str, Any]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(10)
    try:
        connection.connect(os.fspath(socket_path))
        stream = connection.makefile("rwb")
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "provider-free-gate", "version": "1"},
            },
        }
        stream.write(json.dumps(initialize, separators=(",", ":")).encode() + b"\n")
        stream.flush()
        initialized = json.loads(stream.readline())
        stream.write(b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n')
        stream.flush()
        stream.readline()
        stream.write(b'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n')
        stream.flush()
        listed = json.loads(stream.readline())
    finally:
        connection.close()
    tools = listed.get("result", {}).get("tools") if isinstance(listed, dict) else None
    names = sorted(
        item["name"] for item in tools if isinstance(item, dict) and isinstance(item.get("name"), str)
    ) if isinstance(tools, list) else []
    return {
        "initialize_succeeded": isinstance(initialized, dict) and "result" in initialized,
        "tools_list_succeeded": isinstance(listed, dict) and isinstance(tools, list),
        "tool_descriptor_names": names,
    }


def _audit_projection(audit: list[dict[str, object]]) -> dict[str, object]:
    if not audit:
        return {"audit_count": "0"}
    return {"audit_count": "1" if len(audit) == 1 else "2_plus", **audit[0]}


def _same_sandbox_mcp_preflight(argv: list[str], env: Mapping[str, str]) -> dict[str, object]:
    result: dict[str, object] = {
        "spawned": False,
        "initialize_ok": False,
        "tools_list_ok": False,
        "exact_descriptor_seen": False,
        "tools_call_ok": False,
        "is_error": None,
        "text_present": False,
        "supervisor_unavailable": None,
        "exit_class": "spawn_failed",
    }
    sequence = b"\n".join((
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"provider-free-gate","version":"1"}}}',
        b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}',
        b'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}',
        b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"inspect_formal_preview","arguments":{"preview_handle":"preview-probe"}}}',
    )) + b"\n"
    try:
        completed = subprocess.run(
            argv,
            env=dict(env),
            input=sequence,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        result["exit_class"] = "timeout"
        return result
    except OSError:
        return result
    result["spawned"] = True
    if completed.returncode != 0:
        result["exit_class"] = "nonzero"
        return result
    result["exit_class"] = "zero"
    if len(completed.stdout) > MAX_EVIDENCE_BYTES:
        return result
    try:
        frames = [json.loads(line) for line in completed.stdout.splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError):
        return result
    if len(frames) != 3 or not all(isinstance(frame, dict) for frame in frames):
        return result
    result["initialize_ok"] = frames[0].get("id") == 1 and isinstance(frames[0].get("result"), dict)
    tools = frames[1].get("result", {}).get("tools") if isinstance(frames[1].get("result"), dict) else None
    names = sorted(item.get("name") for item in tools if isinstance(item, dict) and isinstance(item.get("name"), str)) if isinstance(tools, list) else []
    result["tools_list_ok"] = frames[1].get("id") == 2 and isinstance(tools, list)
    result["exact_descriptor_seen"] = names == [_TOOL]
    call = frames[2].get("result") if frames[2].get("id") == 3 else None
    if not isinstance(call, dict) or set(call) != {"isError", "structuredContent", "content"} or type(call.get("isError")) is not bool or not isinstance(call.get("content"), list):
        return result
    result["tools_call_ok"] = True
    result["is_error"] = call["isError"]
    result["text_present"] = any(isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str) for item in call["content"])
    structured = call["structuredContent"]
    result["supervisor_unavailable"] = isinstance(structured, dict) and isinstance(structured.get("error"), dict) and structured["error"].get("classification") == "supervisor_unavailable"
    return result


def _version_from_output(value: str) -> str | None:
    match = _VERSION.search(value)
    return match.group(1) if match else None


def _config_enabled(config_path: Path) -> bool:
    try:
        body = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return '[mcp_servers.agent_surface]' in body and 'args = ["/agent-surface/client.py", "--mcp"]' in body


def _safe_returncode(value: int | None) -> int | None:
    return value if isinstance(value, int) and -255 <= value <= 255 else None


def _schema_projection(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"strict": None, "required": [], "additionalProperties": None}
    required = value.get("required")
    return {
        "strict": value.get("strict") if type(value.get("strict")) is bool else None,
        "required": sorted(item for item in required if type(item) is str and len(item) <= 64)[:16] if isinstance(required, list) else [],
        "additionalProperties": value.get("additionalProperties") if type(value.get("additionalProperties")) is bool else None,
    }


def _tool_projection(item: Mapping[str, object], children: list[str]) -> dict[str, object]:
    schema = item.get("parameters", item.get("input_schema"))
    return {
        "type": item.get("type") if type(item.get("type")) is str and len(item["type"]) <= 64 else None,
        "name": item.get("name") if type(item.get("name")) is str and len(item["name"]) <= MAX_TOOL_NAME_BYTES else None,
        "children": children,
        **_schema_projection(schema),
        "defer": item.get("defer_loading") if type(item.get("defer_loading")) is bool else None,
    }


def _protocol_projection(body: object) -> dict[str, object]:
    request = body if isinstance(body, dict) else {}
    input_value = request.get("input")
    input_items = input_value if isinstance(input_value, list) else []
    input_types = sorted({item.get("type") for item in input_items if isinstance(item, dict) and type(item.get("type")) is str and len(item["type"]) <= 64})[:16]
    additional = [item for item in input_items if isinstance(item, dict) and item.get("type") == "additional_tools"]
    roots = additional[0].get("tools") if len(additional) == 1 else None
    projected: list[dict[str, object]] = []
    raw_tools: list[Mapping[str, object]] = []

    def collect(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or len(projected) >= MAX_TOOL_NAMES:
                continue
            children = item.get("tools")
            child_names = sorted(child.get("name") for child in children if isinstance(child, dict) and type(child.get("name")) is str and len(child["name"]) <= MAX_TOOL_NAME_BYTES)[:MAX_TOOL_NAMES] if isinstance(children, list) else []
            projected.append(_tool_projection(item, child_names))
            raw_tools.append(item)
            collect(children)

    collect(roots)
    exec_items = [item for item in projected if item["name"] == "exec"]
    return {
        "top_level_tools_present": isinstance(request.get("tools"), list),
        "input_types": input_types,
        "additional_tools": projected,
        "tool_search_count": sum(1 for item in projected if item["name"] == "tool_search"),
        "exec": {
            "custom": any(item["type"] == "custom" for item in exec_items),
            "lark": any(
                raw.get("name") == "exec"
                and raw.get("type") == "custom"
                and isinstance(raw.get("format"), dict)
                and raw["format"].get("type") == "grammar"
                and raw["format"].get("syntax") == "lark"
                for raw in raw_tools
            ),
        },
    }


def _output_signal(body: object) -> dict[str, object]:
    items = body.get("input") if isinstance(body, dict) and isinstance(body.get("input"), list) else []
    outputs = [item for item in items if isinstance(item, dict) and item.get("type") == "custom_tool_call_output"]
    result: dict[str, object] = {"call_id_matches": False, "output_shape": "invalid", "script_status": "unknown"}
    if len(outputs) != 1:
        return result
    result["call_id_matches"] = outputs[0].get("call_id") == "call_provider_free"
    content = outputs[0].get("output")
    if not isinstance(content, list) or not 1 <= len(content) <= 4:
        return result
    if len(content) != 2:
        return result
    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "input_text" or set(item) != {"type", "text"} or not isinstance(item["text"], str) or len(item["text"]) > 1024:
            return result
        texts.append(item["text"])
    status_match = re.fullmatch(r"Script (completed|failed)\nWall time [0-9]+(?:\.[0-9])? seconds\nOutput:\n", texts[0])
    if status_match is None:
        return result
    try:
        signal = json.loads(texts[1])
    except json.JSONDecodeError:
        return result
    fields = {"alias_found", "static_callable", "call_attempted", "call_threw", "isError", "content_types", "structured_content_present", "supervisor_unavailable", "first_text_classification"}
    if not isinstance(signal, dict) or set(signal) != fields:
        return result
    if any(type(signal[key]) is not bool for key in ("alias_found", "static_callable", "call_attempted", "call_threw")):
        return result
    if signal["isError"] is not None and type(signal["isError"]) is not bool:
        return result
    if type(signal["structured_content_present"]) is not bool:
        return result
    if signal["supervisor_unavailable"] is not None and type(signal["supervisor_unavailable"]) is not bool:
        return result
    if signal["first_text_classification"] not in {"not_available_to_model", "mcp_client_not_initialized", "mcp_client_shutdown", "transport", "tool_not_found", "other", "none"}:
        return result
    if not isinstance(signal["content_types"], list) or len(signal["content_types"]) > 4 or any(type(value) is not str or len(value) > 64 for value in signal["content_types"]):
        return result
    result.update({"output_shape": "content_array", "script_status": status_match.group(1), **signal})
    return result


class _AuditedBridge(AgentSurfaceBridge):
    """Real bridge dispatch with a bounded record of the one MCP call."""

    def __init__(self, surface: object, socket_path: Path) -> None:
        super().__init__(surface, socket_path)
        self.audit: list[dict[str, object]] = []

    def _mcp_frame(self, request: dict[str, object], state: str):
        if request.get("method") == "tools/call" and len(self.audit) < 2:
            params = request.get("params")
            arguments = params.get("arguments") if isinstance(params, dict) else None
            self.audit.append({
                "tools_call": True,
                "method": "tools/call",
                "tool_name": params.get("name") if isinstance(params, dict) else None,
                "argument_keys": sorted(arguments) if isinstance(arguments, dict) and all(type(key) is str and len(key) <= 64 for key in arguments) else [],
            })
        return super()._mcp_frame(request, state)


def _valid_dispatch(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("audit_count"), str):
        return False
    count = value["audit_count"]
    if count == "0":
        return set(value) == {"audit_count"}
    fields = {"audit_count", "tools_call", "method", "tool_name", "argument_keys"}
    if count not in {"1", "2_plus"} or set(value) != fields:
        return False
    if value["tools_call"] is not True:
        return False
    if type(value["method"]) is not str or len(value["method"]) > 64:
        return False
    if value["tool_name"] is not None and (type(value["tool_name"]) is not str or len(value["tool_name"]) > MAX_TOOL_NAME_BYTES):
        return False
    keys = value["argument_keys"]
    return isinstance(keys, list) and len(keys) <= 16 and all(type(key) is str and len(key) <= 64 for key in keys)


def _valid_second_request(value: object) -> bool:
    base = {"call_id_matches", "output_shape", "script_status"}
    if not isinstance(value, dict) or not base <= set(value):
        return False
    if type(value["call_id_matches"]) is not bool or value["output_shape"] not in {"invalid", "content_array"} or value["script_status"] not in {"unknown", "completed", "failed"}:
        return False
    if value["output_shape"] == "invalid":
        return set(value) == base and value["script_status"] == "unknown"
    fields = base | {"alias_found", "static_callable", "call_attempted", "call_threw", "isError", "content_types", "structured_content_present", "supervisor_unavailable", "first_text_classification"}
    if set(value) != fields:
        return False
    if any(type(value[key]) is not bool for key in ("alias_found", "static_callable", "call_attempted", "call_threw")):
        return False
    if value["isError"] is not None and type(value["isError"]) is not bool:
        return False
    if type(value["structured_content_present"]) is not bool:
        return False
    if value["supervisor_unavailable"] is not None and type(value["supervisor_unavailable"]) is not bool:
        return False
    if value["first_text_classification"] not in {"not_available_to_model", "mcp_client_not_initialized", "mcp_client_shutdown", "transport", "tool_not_found", "other", "none"}:
        return False
    contents = value["content_types"]
    return isinstance(contents, list) and len(contents) <= 4 and all(type(item) is str and len(item) <= 64 for item in contents)


def _valid_protocol(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"top_level_tools_present", "input_types", "additional_tools", "tool_search_count", "exec"}:
        return False
    if value["top_level_tools_present"] is not False or not isinstance(value["input_types"], list) or "additional_tools" not in value["input_types"]:
        return False
    if len(value["input_types"]) > 16 or any(type(item) is not str or len(item) > 64 for item in value["input_types"]):
        return False
    tools = value["additional_tools"]
    if not isinstance(tools, list) or not 1 <= len(tools) <= MAX_TOOL_NAMES:
        return False
    required_tool_fields = {"type", "name", "children", "strict", "required", "additionalProperties", "defer"}
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != required_tool_fields:
            return False
        if tool["type"] is not None and (type(tool["type"]) is not str or len(tool["type"]) > 64):
            return False
        if tool["name"] is not None and (type(tool["name"]) is not str or len(tool["name"]) > MAX_TOOL_NAME_BYTES):
            return False
        for key, maximum in (("children", MAX_TOOL_NAMES), ("required", 16)):
            if not isinstance(tool[key], list) or len(tool[key]) > maximum or any(type(item) is not str or len(item) > MAX_TOOL_NAME_BYTES for item in tool[key]):
                return False
        if any(tool[key] is not None and type(tool[key]) is not bool for key in ("strict", "additionalProperties", "defer")):
            return False
    if type(value["tool_search_count"]) is not int or not 0 <= value["tool_search_count"] <= MAX_TOOL_NAMES:
        return False
    return value["exec"] == {"custom": True, "lark": True}


def validate_artifacts(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path]:
    _, evidence_path, manifest_path = artifact_paths(repo_root, record)
    def read_bounded(path: Path, maximum: int) -> bytes:
        try:
            if path.stat().st_size > maximum:
                raise ProviderFreeError("provider-free artifact exceeds its byte limit")
            return path.read_bytes()
        except OSError as exc:
            raise ProviderFreeError("provider-free evidence or artifact manifest is missing/invalid") from exc

    try:
        evidence = json.loads(read_bounded(evidence_path, MAX_EVIDENCE_BYTES))
        manifest = json.loads(read_bounded(manifest_path, MAX_MANIFEST_BYTES))
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("provider-free evidence or artifact manifest is missing/invalid") from exc
    identity = expected_identity(record)
    fields = {"schema", "identity", "sandbox", "private_config", "mcp_preflight", "preflight_dispatch", "first_request", "mcp_dispatch", "second_request", "receiver", "process", "gate_passed"}
    if not isinstance(evidence, dict) or set(evidence) != fields or evidence.get("schema") != EVIDENCE_SCHEMA or evidence.get("identity") != identity:
        raise ProviderFreeError("provider-free evidence has an invalid shape")
    if evidence.get("sandbox") != {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"}:
        raise ProviderFreeError("provider-free evidence has an invalid sandbox contract")
    if evidence.get("private_config") != {"agent_surface_server_enabled": True}:
        raise ProviderFreeError("provider-free evidence has an invalid config result")
    expected_preflight = {"spawned": True, "initialize_ok": True, "tools_list_ok": True, "exact_descriptor_seen": True, "tools_call_ok": True, "is_error": True, "text_present": True, "supervisor_unavailable": True, "exit_class": "zero"}
    if evidence.get("mcp_preflight") != expected_preflight:
        raise ProviderFreeError("provider-free evidence has an invalid MCP preflight")
    preflight_dispatch = evidence.get("preflight_dispatch")
    if not _valid_dispatch(preflight_dispatch) or preflight_dispatch != {"audit_count": "1", "tools_call": True, "method": "tools/call", "tool_name": _TOOL, "argument_keys": ["preview_handle"]}:
        raise ProviderFreeError("provider-free evidence has an invalid preflight dispatch")
    first = evidence.get("first_request")
    if not isinstance(first, dict) or set(first) != {"received", "protocol"} or first["received"] is not True or not _valid_protocol(first["protocol"]):
        raise ProviderFreeError("provider-free evidence has an invalid first request")
    dispatch = evidence.get("mcp_dispatch")
    if not _valid_dispatch(dispatch):
        raise ProviderFreeError("provider-free evidence has an invalid MCP dispatch")
    if dispatch != {"audit_count": "1", "tools_call": True, "method": "tools/call", "tool_name": _TOOL, "argument_keys": ["preview_handle"]}:
        raise ProviderFreeError("provider-free evidence has an unsuccessful MCP dispatch")
    second = evidence.get("second_request")
    if not _valid_second_request(second):
        raise ProviderFreeError("provider-free evidence has an invalid second request")
    expected_signal = {"call_id_matches": True, "output_shape": "content_array", "script_status": "completed", "alias_found": True, "static_callable": True, "call_attempted": True, "call_threw": False, "isError": True, "content_types": ["text"], "structured_content_present": True, "supervisor_unavailable": True, "first_text_classification": "other"}
    if second != expected_signal:
        raise ProviderFreeError("provider-free evidence has an unsuccessful second request")
    if evidence.get("receiver") != {"loopback_only": True, "provider_escape": False, "request_count": 2}:
        raise ProviderFreeError("provider-free evidence has an invalid receiver result")
    process = evidence.get("process")
    if not isinstance(process, dict) or set(process) != {"codex_version", "version_exit_code", "workload_exit_code"} or process["codex_version"] != "0.147.0" or type(process["version_exit_code"]) is not int or process["version_exit_code"] != 0 or type(process["workload_exit_code"]) is not int or process["workload_exit_code"] != 0 or evidence.get("gate_passed") is not True:
        raise ProviderFreeError("provider-free evidence has an invalid process result")
    expected_manifest = {"schema": MANIFEST_SCHEMA, "final_status": 0, "identity": identity, "evidence": {"path": evidence_path.name}}
    if manifest != expected_manifest:
        raise ProviderFreeError("provider-free artifact manifest differs from evidence")
    return evidence_path, manifest_path


def run_job(record: Mapping[str, Any], *, repo_root: Path, host_home: Path, environ: Mapping[str, str]) -> int:
    identity = expected_identity(record)
    assert_current_authority(record, host_home)
    exp_dir, evidence_path, manifest_path = artifact_paths(repo_root, record)
    exp_dir.mkdir(parents=True, exist_ok=False)
    candidate_dir = Path(tempfile.mkdtemp(prefix="ttc-agent-surface-gate-"))
    socket_dir = Path(tempfile.mkdtemp(prefix="ttc-agent-surface-socket-"))
    socket_path = socket_dir / "surface.sock"
    bridge: _AuditedBridge | None = None
    server: http.server.ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    version_exit = workload_exit = None
    version = None
    preflight = {"spawned": False, "initialize_ok": False, "tools_list_ok": False, "exact_descriptor_seen": False, "tools_call_ok": False, "is_error": None, "text_present": False, "supervisor_unavailable": None, "exit_class": "spawn_failed"}
    preflight_dispatch = {"audit_count": "0"}
    config_enabled = False
    requests: list[tuple[str, Any, str]] = []
    try:
        bridge = _AuditedBridge(None, socket_path)
        bridge.surface = bridge._mcp.AgentSurface(None)
        bridge.start()
        config_path = runner.prepare_isolated_job_codex_home(exp_dir) / plugin_deployment.CONFIG_TOML_NAME
        config_enabled = _config_enabled(config_path)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        _Receiver.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        gate_env = dict(environ)
        gate_env["VENUS_TOKEN"] = "provider-free-loopback-only"
        fixture = repo_root / _FIXTURE
        child_env = runner.build_sandbox_environment(gate_env, f"http://127.0.0.1:{server.server_port}/v1", isolated_agent=True, tap_client_token="provider-free-loopback-token")
        preflight_argv = runner.build_bwrap_argv(repo_root, exp_dir, [fixture], ["python3", "/agent-surface/client.py", "--mcp"], gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path)
        preflight = _same_sandbox_mcp_preflight(preflight_argv, child_env)
        preflight_dispatch = _audit_projection(bridge.audit)
        bridge.audit.clear()
        version_result = subprocess.run(runner.build_bwrap_argv(repo_root, exp_dir, [fixture], ["codex", "--version"], gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path), env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=30)
        version_exit = _safe_returncode(version_result.returncode)
        version = _version_from_output(version_result.stdout + "\n" + version_result.stderr)
        workload = ["gateway/codex-tap-gpt56", "sol", "exec", "--skip-git-repo-check", "--ephemeral", "run the fixed provider-free MCP canary"]
        workload_result = subprocess.run(runner.build_bwrap_argv(repo_root, exp_dir, [fixture], workload, gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path), env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=60)
        workload_exit = _safe_returncode(workload_result.returncode)
        requests = list(_Receiver.requests)
    finally:
        if server is not None:
            server.shutdown(); server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        if bridge is not None:
            bridge.stop()
        shutil.rmtree(candidate_dir, ignore_errors=True)
        shutil.rmtree(socket_dir, ignore_errors=True)
        shutil.rmtree(exp_dir / "run" / ".codex-home", ignore_errors=True)
    first_body = requests[0][1] if requests else None
    second_body = requests[1][1] if len(requests) > 1 else None
    audit = bridge.audit if bridge is not None else []
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "identity": identity,
        "sandbox": {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"},
        "private_config": {"agent_surface_server_enabled": config_enabled},
        "mcp_preflight": preflight,
        "preflight_dispatch": preflight_dispatch,
        "first_request": {"received": bool(requests), "protocol": _protocol_projection(first_body)},
        "mcp_dispatch": _audit_projection(audit),
        "second_request": _output_signal(second_body),
        "receiver": {"loopback_only": len(requests) == 2 and all(path == "/v1/responses" and host == "127.0.0.1" for path, _body, host in requests), "provider_escape": False, "request_count": len(requests)},
        "process": {"codex_version": version, "version_exit_code": version_exit, "workload_exit_code": workload_exit},
        "gate_passed": False,
    }
    try:
        provisional = dict(evidence)
        provisional["gate_passed"] = True
        _atomic_write(evidence_path, provisional)
        _atomic_write(manifest_path, {"schema": MANIFEST_SCHEMA, "final_status": 0, "identity": identity, "evidence": {"path": evidence_path.name}})
        validate_artifacts(repo_root, record)
        evidence = provisional
    except ProviderFreeError:
        _atomic_write(evidence_path, evidence)
        _atomic_write(manifest_path, {"schema": MANIFEST_SCHEMA, "final_status": 1, "identity": identity, "evidence": {"path": evidence_path.name}})
    return 0 if evidence["gate_passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = protocol.load_state(args.state_root, args.job)
        return run_job(record, repo_root=Path(__file__).resolve().parents[2], host_home=Path.home(), environ=os.environ)
    except (ProviderFreeError, plugin_deployment.PluginAuthorityError, protocol.ProtocolError, runner.PilotError) as exc:
        print(f"provider-free-agent-surface-mcp-injection: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
